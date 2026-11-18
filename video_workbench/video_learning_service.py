"""Reference-clip learning with source custody and bounded reproduction/transfer.

The durable paid-operation and stopping rules are shared with picture learning;
media, observations, products and publication facts remain video scoped.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_reference_learning as protocol
from .picture_learning_service import PictureLearningService, _text
from .picture_learning_store import identifier, new_id, owner_id, require
from .video_learning_store import VideoLearningStore
from .video_learning_media import VideoLearningMedia, MAX_UPLOAD, MAX_DURATION, LIMITATIONS
from .video_learning_analysis import VideoLearningAnalysis

PREFIX = '/api/video-lab'


def review_output_contract(mode):
    """Self-contained wire guidance, separate from creative feedback.

    The input cannot contain its own final SHA. The example is explicitly a
    template; its binding is resolved from the actual read_inputs tool receipt.
    """
    require(mode in {'review', 'transfer_plan'}, 'video_learning_review_mode_invalid')
    task = (dict(goal='Change object count and spatial structure while testing the same learned technique.',
        criteria=['The changed structure still demonstrates the learned technique.'],
        rationale='A structural change tests reuse beyond the reference example.')
        if mode == 'transfer_plan' else None)
    return dict(schema='video.reference-learning-review-output-guide.v1',
        binding=dict(mode='Copy the exact input mode.',
            input_sha256='Use the exact read_inputs.details.input_sha256 value. Never output the template marker or a source/image SHA.'),
        instructions=[
            'This is an output-format contract, not creative feedback. Fill the example with findings grounded in the actual supplied frames.',
            'The JSON example is a template. Replace its input_sha256 marker with the actual tool receipt SHA before write_outputs.',
            'Do not add unlisted fields or put issue objects inside issues. Encode severity, description and evidence in one plain issue string when needed.',
            'Text arrays contain at most 32 plain strings; each string has at most 8000 characters.',
            'Do not create source or method in review/transfer_plan. source and method must be null; sound_events must be empty.',
            'preferred may be output, baseline or inconclusive. baseline requires actual supplied baseline frames.',
            'motion and audio can only be unverified or not_requested. Sampled pixels do not establish continuous playback or audio listening.',
            'transfer_plan requires a non-null transfer_task with a concrete changed-content goal and string[] criteria. A proposed task is not completed transfer validation.',
        ],
        top_level_required=dict(schema='literal video.reference-learning-result.v1',
            input_sha256='exact input SHA-256 from read_inputs.details.input_sha256', mode='exact input mode',
            source='null', review='object using the listed field types', method='null',
            transfer_task='task object required for transfer_plan; null or task object for review',
            evidence_scope='literal sampled_frames_only'),
        top_level_optional=dict(sound_events='empty array only', limitations='string[]', analysis='omit or null'),
        review_required=dict(score='finite number from 0 to 100', summary='nonempty string',
            issues='string[]; NEVER object[]', stop_recommended='boolean'),
        review_optional=dict(comparison='plain string; NEVER object',
            preferred='output | baseline | inconclusive', observed_facts='string[]',
            evidence_frame_ids='string[]; actual supplied frame IDs only', limitations='string[]',
            reference_fidelity='plain string', work_quality='plain string', transfer='plain string',
            motion='unverified | not_requested', audio='unverified | not_requested'),
        transfer_task=dict(goal='nonempty plain string', criteria='string[]', rationale='optional plain string'),
        result_example=dict(schema=protocol.RESULT_SCHEMA, input_sha256='BIND_FROM_READ_INPUTS_DETAILS_INPUT_SHA256',
            mode=mode, source=None, method=None, transfer_task=task, evidence_scope='sampled_frames_only',
            sound_events=[], review=dict(score=50, summary='Describe concrete findings from the actual supplied frames.',
                issues=['Describe a specific visible issue and its actual frame evidence in this string.'],
                stop_recommended=False, comparison='Describe only the actual reference/output/baseline comparison.',
                preferred='inconclusive', reference_fidelity='Describe fidelity only within the observed frames.',
                work_quality='Describe actual visible strengths and remaining problems.',
                transfer='State the actual changed-content validation scope; do not imply that a proposed task was executed.',
                motion='unverified', audio='unverified')))


class VideoLearningService(VideoLearningAnalysis, PictureLearningService):
    protocol = protocol
    prefix = PREFIX
    store_type = VideoLearningStore
    start_fields = PictureLearningService.start_fields | {
        'start_seconds', 'end_seconds', 'learn_audio', 'analysis_id', 'segment_index'}

    def __init__(self, root, *, author_gateway, review_gateway, renderer,
                 library_adapter=None, media_adapter=None, clock=time.time):
        super().__init__(root, author_gateway=author_gateway, review_gateway=review_gateway,
            renderer=renderer, library_adapter=library_adapter, clock=clock)
        self.media = media_adapter or VideoLearningMedia(self.root / 'media-preparation')

    def _reference_public(self, row):
        return {key: deepcopy(row[key]) for key in ('id', 'file_name', 'mime_type', 'sha256',
            'width', 'height', 'duration_seconds', 'has_audio', 'source_url', 'created_at')} | {
                'url': self.prefix + '/references/' + row['id'] + '/media',
                'observation_status': 'uploaded_not_model_observed'}

    def upload_reference(self, owner, body):
        owner_id(owner)
        require(type(body) is dict and {'request_id', 'file_name', 'mime_type', 'content_base64'}
                <= set(body) <= {'request_id', 'file_name', 'mime_type', 'content_base64', 'source_url'},
                'video_learning_reference_invalid')
        identifier(body['request_id'])
        existing = self.store.request(owner, 'reference', body['request_id'], body)
        if existing:
            return self._reference_public(self._reference(owner, existing))
        name = _text(body['file_name'], 240)
        require('/' not in name and '\\' not in name and name not in {'.', '..'},
                'video_learning_reference_name')
        require(type(body['mime_type']) is str and body['mime_type'] in {'video/mp4', 'video/webm'},
                'video_learning_reference_mime')
        source_url = _text(body.get('source_url', ''), 2000, empty=True)
        require(not source_url or source_url.startswith(('https://', 'http://')),
                'video_learning_reference_source_url')
        encoded = body['content_base64']
        require(type(encoded) is str and len(encoded) <= (MAX_UPLOAD + 2) // 3 * 4,
                'video_learning_reference_size', 413)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            raise ServiceError('video_learning_reference_encoding', 422) from None
        require(0 < len(raw) <= MAX_UPLOAD, 'video_learning_reference_size', 413)
        sha = self.store.blob(raw)
        metadata = self.media.probe(self.store.blob_path(sha))
        require(metadata['mime_type'] == body['mime_type'], 'video_learning_reference_mime_mismatch')
        row = dict(owner=owner, id=new_id(), file_name=name, sha256=sha,
            created_at=self.clock(), source_url=source_url, **metadata)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.store.request(owner, 'reference', body['request_id'], body, db=db)
            if existing:
                row = self._reference(owner, existing)
            else:
                self.store.put('references', row, db=db, insert=True)
                self.store.bind_request(owner, 'reference', body['request_id'], body, row['id'], db)
        return self._reference_public(row)

    reference = upload_reference

    def start(self, owner, body):
        require(type(body) is dict and type(body.get('reference_ids')) is list
                and len(body['reference_ids']) == 1, 'video_learning_one_reference_required')
        require(type(body.get('width', 1280)) is int and 320 <= body.get('width', 1280) <= 1280
                and type(body.get('height', 720)) is int and 180 <= body.get('height', 720) <= 720,
                'video_learning_dimensions_invalid')
        require(type(body.get('learn_audio', True)) is bool, 'video_learning_audio_intent_invalid')
        return super().start(owner, body)

    def _extra_frozen(self, owner, body, references):
        ref = references[0]
        # A missing end on a long upload must never quietly choose its first minute.
        require('start_seconds' in body and 'end_seconds' in body,
                'video_learning_explicit_clip_required')
        clip = self.media.clip(ref, body['start_seconds'], body['end_seconds'])
        selection = None
        if 'analysis_id' in body or 'segment_index' in body:
            require(type(body.get('analysis_id')) is str and type(body.get('segment_index')) is int,
                    'video_learning_analysis_selection_invalid')
            analysis = self.store.analysis(owner, body['analysis_id'])
            segments = (analysis.get('result') or {}).get('analysis', {}).get('candidate_segments', [])
            require(analysis['reference_id'] == ref['id'] and analysis['state'] == 'completed'
                    and 0 <= body['segment_index'] < len(segments),
                    'video_learning_analysis_selection_invalid')
            segment = segments[body['segment_index']]
            require(abs(segment['start_seconds'] - clip['start_seconds']) < 1e-6
                    and abs(segment['end_seconds'] - clip['end_seconds']) < 1e-6,
                    'video_learning_analysis_selection_range_changed')
            selection = dict(analysis_id=analysis['id'], segment_index=body['segment_index'],
                candidate=deepcopy(segment), analysis_input_sha256=analysis['input_sha256'])
        observed = self.media.sample(self.store.blob_path(ref['sha256']),
            media_sha256=ref['sha256'], start_seconds=clip['start_seconds'],
            end_seconds=clip['end_seconds'], store=self.store)
        return dict(clip=clip, start_seconds=clip['start_seconds'], end_seconds=clip['end_seconds'],
            fps=24, duration_seconds=clip['duration_seconds'],
            reference_observation=observed, learn_audio=body.get('learn_audio', True),
            analysis_selection=selection,
            audio_observation=dict(has_audio=ref['has_audio'],
                learning_requested=body.get('learn_audio', True), status='unverified',
                reason='当前角色接收实际画面序列，尚未接入实际音频听审。'),
            continuous_motion_verified=False, audio_verified=False)

    def _baseline_observation(self, candidate):
        return deepcopy(candidate['observation'])

    @staticmethod
    def _has_picture(candidate):
        return (bool(candidate.get('media_sha256')) and bool(candidate.get('observation', {}).get('frames'))
            and candidate.get('artifacts', {}).get('media', {}).get('sha256') == candidate['media_sha256'])

    @staticmethod
    def _purpose_frames(observation, purpose):
        result = []
        for index, frame in enumerate(observation.get('frames', [])):
            result.append(deepcopy(frame) | dict(purpose=purpose, frame_id=f'{purpose}-{index + 1}',
                file_name=f'{purpose}-{index + 1}.jpg'))
        return result

    def _frames(self, run, mode):
        transfer = run['work_kind'] == 'transfer'
        frames = [] if transfer else self._purpose_frames(run['frozen']['reference_observation'], 'reference')
        output = self._candidate(run) if mode != 'create' else None
        reproductions = [item for item in self._context_candidates(run) if item['work_kind'] == 'reproduction'
                         and self._has_picture(item) and (output is None or item['id'] != output['id'])]
        baseline = next((item for item in reproductions if item['id'] == run['best_candidate_id']), None)
        if baseline is None and reproductions:
            baseline = reproductions[-1]
        if output is not None:
            frames += self._purpose_frames(output['observation'], 'output')
        if not transfer:
            observed = baseline['observation'] if baseline else run['frozen'].get('base_observation')
            if observed:
                frames += self._purpose_frames(observed, 'baseline')
        return frames

    def _document(self, run, mode):
        document = super()._document(run, mode)
        frozen = run['frozen']
        document['constraints'].update(fps=24, duration_seconds=frozen['duration_seconds'])
        document['observation'].update(coverage=dict(scope='sampled_frames_only',
            reference=deepcopy(frozen['reference_observation']['coverage'])
                if run['work_kind'] == 'reproduction' else None,
            limitations=deepcopy(LIMITATIONS), continuous_motion_verified=False, audio_verified=False),
            audio=deepcopy(frozen['audio_observation']) if run['work_kind'] == 'reproduction' else
                dict(status='unverified', learning_requested=False, has_audio=False))
        if run['stage'] == 'render':
            document['sound_events'] = deepcopy(self._candidate(run).get('sound_events', []))
        if mode in {'review', 'transfer_plan'}:
            document['constraints']['output_contract'] = review_output_contract(mode)
        return document

    def _agent(self, run, operation):
        super()._agent(run, operation)
        if operation['stage'] == 'author' and operation.get('status') == 'completed':
            candidate = next((item for item in run['candidates'] + run.get('internal_attempts', [])
                if item.get('author_operation_id') == operation['id']), None)
            if candidate is not None:
                candidate['sound_events'] = deepcopy(operation['result'].get('sound_events', []))

    def _render(self, run, operation):
        candidate = self._candidate(run)
        if operation['status'] == 'reserved':
            if not self.store.claim(run['owner'], operation['id'], now=self.clock()):
                return
            operation['status'] = 'claimed'
            result = self.renderer.render(operation['id'], candidate['source'], operation['input'],
                                          deadline_at=run['deadline_at'])
        else:
            recover = getattr(self.renderer, 'recover', None) or self.renderer.lookup
            result = recover(operation['id'])
        if not result or result.get('status') == 'unknown' or result.get('stop_proven') is not True:
            run['state'] = 'submission_unknown'
            return
        if result.get('status') != 'rendered' or result.get('technical_passed') is not True:
            operation.update(status='failed', result=result)
            self._finish_operation(run, operation)
            run['error'] = dict(code='video_learning_render_failed')
            self._finish(run, 'render_failed', 'failed')
            return
        path = Path(result.get('media_path') or result['artifact_path'])
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        require(sha == result['sha256'], 'video_learning_render_changed', 409)
        metadata = self.media.probe(path)
        require((metadata['width'], metadata['height']) == (run['frozen']['width'], run['frozen']['height'])
            == (result['width'], result['height']), 'video_learning_render_dimensions_changed', 409)
        require(abs(metadata['duration_seconds'] - run['frozen']['duration_seconds']) <= 1 / 24 + 1e-5,
                'video_learning_render_duration_changed', 409)
        self.store.blob(raw)
        observed = self.media.sample(path, media_sha256=sha, start_seconds=0,
            end_seconds=run['frozen']['duration_seconds'], store=self.store)
        poster_sha = observed['frames'][0]['sha256']
        artifacts = {}
        for kind, content, mime in (
            ('media', raw, 'video/mp4'), ('poster', self.store.read_blob(poster_sha), 'image/jpeg'),
            ('source', canonical_bytes(candidate['source']), 'application/json'),
            ('receipt', Path(result['receipt_path']).read_bytes(), 'application/json')):
            artifacts[kind] = dict(id=candidate['id'] + '-' + kind,
                sha256=self.store.blob(content), mime_type=mime)
        candidate.update(media_sha256=sha, width=metadata['width'], height=metadata['height'],
            duration_seconds=metadata['duration_seconds'], fps=24, has_audio=metadata['has_audio'],
            observation=observed, artifacts=artifacts, status='rendered',
            render_operation_id=operation['id'], verification_scope='sampled_frames_only',
            continuous_motion_verified=False, audio_verified=False)
        operation.update(status='completed', result={key: value for key, value in result.items()
                                                     if not key.endswith('_path')})
        self._finish_operation(run, operation)
        run['stage'] = 'review'

    def get(self, owner, project_id):
        result = super().get(owner, project_id)
        result.update(schema='video.reference-learning-project.v1', verification_scope='sampled_frames_only',
            continuous_motion_verified=False, audio_verified=False)
        return result

    def catalogue(self, owner):
        owner_id(owner)
        projects = []
        for project in self.store.list('projects', owner):
            runs = self.store.list('experiments', owner, project_id=project['id'])
            latest = next(run for run in runs if run['id'] == project['latest_experiment_id'])
            candidates = [item for run in runs for item in run['candidates'] if self._has_picture(item)]
            chosen = project['adopted_candidate_id'] or project['best_candidate_id']
            preview = next((item for item in candidates if item['id'] == chosen), None)
            preview = preview or (candidates[0] if candidates else None)
            row = {key: deepcopy(value) for key, value in project.items() if key != 'owner'}
            row.update(state=latest['state'], experiments_count=len(runs),
                latest_experiment_request_id=latest['request_id'],
                reference_url=self.prefix + '/references/' + project['reference_ids'][0] + '/media',
                preview_url=self.prefix + '/projects/' + project['id'] + '/artifacts/'
                    + preview['artifacts']['poster']['id'] if preview else None)
            projects.append(row)
        return dict(schema='video.reference-learning-catalog.v1', enabled=self._enabled(),
            owner_key=hashlib.sha256(owner.encode('utf-8')).hexdigest(), projects=projects,
            availability=dict(enabled=self._enabled(), reason=None if self._enabled() else '视频学习执行器尚未配置'),
            limits=dict(reference_bytes=MAX_UPLOAD, reference_count=1, reference_duration_seconds=MAX_DURATION,
                clip_seconds=60, max_rounds=8, max_seconds=3600, max_width=1280, max_height=720, fps=24),
            references=[self._reference_public(value) for value in self.store.list('references', owner)],
            analyses=[self._analysis_public(value) for value in self.store.analyses(owner)],
            capabilities=dict(explicit_clip=True, long_video_candidate_analysis=True,
                sampled_visual_comparison=True, continuous_motion_observation=False, audio_observation=False,
                **self._capabilities_public()), runtime_capabilities=self._runtime_capabilities(),
            verification_scope='sampled_frames_only')

    catalog = catalogue

    def inspect(self, owner, project_id, experiment_id):
        result = super().inspect(owner, project_id, experiment_id)
        result['schema'] = 'video.reference-learning-input-inspection.v1'
        return result

    def capture_wikiskill_records(self, owner):
        owner_id(owner)
        records = []
        def add(identity, kind, content):
            records.append(dict(record_id=identity, kind=kind, content=content, sha256=digest(content)))
        with self.store.db() as db:
            db.execute('BEGIN')
            for row in db.execute('SELECT document FROM video_projects WHERE owner=? ORDER BY id', (owner,)):
                project = json.loads(row['document'])
                add('video-project:' + project['id'], 'video_learning_project', project)
            for row in db.execute('SELECT document FROM video_experiments WHERE owner=? ORDER BY id', (owner,)):
                run = json.loads(row['document'])
                add('video-experiment:' + run['id'], 'video_learning_experiment',
                    {key: deepcopy(value) for key, value in run.items() if key not in {'candidates', 'operation_ids'}})
                for candidate in run['candidates']:
                    add('video-work:' + candidate['id'], 'video_learning_work', dict(candidate,
                        project_id=run['project_id'], experiment_id=run['id'],
                        verification_scope='sampled_frames_only', continuous_motion_verified=False,
                        audio_verified=False, model_opinion_is_developer_acceptance=False))
            for row in db.execute('SELECT document FROM video_operations WHERE owner=? ORDER BY id', (owner,)):
                op = json.loads(row['document'])
                identity = 'video-operation:' + op['id']
                add(identity, 'video_learning_operation', {key: value for key, value in op.items()
                    if key not in {'input', 'result'}})
                add(identity + ':input', 'video_learning_operation_input', op['input'])
                if op.get('result') is not None:
                    add(identity + ':result', 'video_learning_operation_result', op['result'])
            for row in db.execute('SELECT document FROM video_analyses WHERE owner=? ORDER BY id', (owner,)):
                analysis = json.loads(row['document'])
                add('video-analysis:' + analysis['id'], 'video_learning_analysis', analysis)
        return sorted(records, key=lambda row: row['record_id'])
