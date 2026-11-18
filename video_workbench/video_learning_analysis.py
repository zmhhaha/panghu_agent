"""Durable, separately budgeted reference analysis; never starts creation."""
from copy import deepcopy

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .animation_creation_service import _run_lock
from .picture_learning_store import TERMINAL, identifier, new_id, owner_id, require
from .picture_learning_service import _text, REMOTE_ACTIVE
from .video_learning_media import LIMITATIONS


class VideoLearningAnalysis:
    def analyze_reference(self, owner, reference_id, body):
        owner_id(owner)
        reference = self._reference(owner, reference_id)
        require(type(body) is dict and {'request_id'} <= set(body)
            <= {'request_id', 'goal', 'max_seconds'}, 'video_learning_analysis_request_invalid')
        identifier(body['request_id'])
        require(self.reviewer is not None, 'video_learning_analysis_unavailable', 503)
        kind = 'reference-analysis:' + reference_id
        existing = self.store.request(owner, kind, body['request_id'], body)
        if existing:
            return self.get_analysis(owner, reference_id, existing)
        goal = _text(body.get('goal', 'Identify distinct visual techniques and candidate effect segments to learn.'), 8000)
        seconds = body.get('max_seconds', 600)
        require(type(seconds) is int and 60 <= seconds <= 1800,
                'video_learning_analysis_budget_invalid')
        now, identity = self.clock(), 'analysis-' + new_id()
        frozen = dict(reference=deepcopy(reference), goal=goal, max_seconds=seconds,
            sample_count=24, frame_observation='sampled_frames_only',
            audio_observation='unverified', input_boundary='frozen_for_entire_analysis')
        value = dict(id=identity, owner=owner, request_id=body['request_id'], reference_id=reference_id,
            frozen=frozen, frozen_sha256=digest(frozen), state='queued', operation_status='reserved',
            input=None, input_sha256=None, input_refs=None, observation=None, job_id=None,
            result=None, observation_receipt=None, stop_requested=False, stop_proven=False,
            stop_reason=None, error=None, created_at=now, updated_at=now, deadline_at=now + seconds)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.store.request(owner, kind, body['request_id'], body, db=db)
            if not existing:
                self.store.save_analysis(value, insert=True, db=db)
                self.store.bind_request(owner, kind, body['request_id'], body, identity, db)
        return self.get_analysis(owner, reference_id, existing or identity)

    def _analysis_public(self, value):
        result = {key: deepcopy(value[key]) for key in ('id', 'reference_id', 'request_id', 'state',
            'operation_status', 'input_sha256', 'result', 'stop_requested', 'stop_proven', 'error',
            'stop_reason', 'created_at', 'updated_at', 'deadline_at')}
        result.update(schema='video.reference-learning-analysis.v1',
            goal=value['frozen']['goal'], max_seconds=value['frozen']['max_seconds'],
            observation=deepcopy(value['observation']),
            candidate_segments=deepcopy((value.get('result') or {}).get('analysis', {}).get('candidate_segments', [])),
            verification_scope='sampled_frames_only', continuous_motion_verified=False,
            audio_verified=False, creation_started=False)
        result['candidates'] = deepcopy(result['candidate_segments'])
        return result

    def get_analysis(self, owner, reference_id, analysis_id):
        self._reference(owner, reference_id)
        value = self.store.analysis(owner, analysis_id)
        require(value['reference_id'] == reference_id, 'video_learning_analysis_not_found', 404)
        return self._analysis_public(value)

    def stop_analysis(self, owner, reference_id, analysis_id):
        self.get_analysis(owner, reference_id, analysis_id)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            value = self.store.analysis(owner, analysis_id, db=db)
            if value['state'] not in TERMINAL:
                value.update(stop_requested=True, state='stopping')
                self.store.save_analysis(value, db=db)
        return self.get_analysis(owner, reference_id, analysis_id)

    def pending(self, limit=100):
        require(type(limit) is int and 1 <= limit <= 1000)
        projects = super().pending(limit)
        analyses = [dict(owner=value['owner'], id=value['id']) for value in self.store.analyses()
                    if value['state'] not in TERMINAL]
        return (projects + analyses)[:limit]

    def advance(self, owner, experiment_id):
        if experiment_id.startswith('analysis-'):
            return self._advance_analysis(owner, experiment_id)
        return super().advance(owner, experiment_id)

    def _analysis_document(self, value):
        reference = value['frozen']['reference']
        observed = self.media.sample(self.store.blob_path(reference['sha256']),
            media_sha256=reference['sha256'], start_seconds=0,
            end_seconds=reference['duration_seconds'], store=self.store,
            sample_count=value['frozen']['sample_count'], deadline_at=value['deadline_at'])
        value['observation'] = observed
        duration = reference['duration_seconds']
        return dict(schema=self.protocol.INPUT_SCHEMA, mode='analyze',
            project_id='reference-' + reference['id'], experiment_id=value['id'],
            work_kind='reproduction', goal=value['frozen']['goal'], criteria=[],
            constraints=dict(width=1280, height=720, fps=24, duration_seconds=duration,
                allowed_engines=['html-svg', 'canvas', 'three']),
            observation=dict(frames=self._purpose_frames(observed, 'reference'),
                coverage=deepcopy(observed['coverage']),
                audio=dict(status='unverified', has_audio=reference['has_audio'], learning_requested=False)),
            base_source=None, feedback='', knowledge_snapshot=None, prior_review=None,
            learned_method=None, knowledge_binding=dict(use_wikiskill=False), assets={},
            frozen_at=value['created_at'])

    def _save_analysis(self, value):
        value['updated_at'] = self.clock()
        return self.store.save_analysis(value)

    def _analysis_job(self, value):
        if value.get('job_id'):
            return self.reviewer.poll(value['job_id'])
        try:
            job = self.reviewer.lookup(value['id'])
        except ServiceError as error:
            if error.status == 404:
                return None
            raise
        if job:
            value.update(job_id=job['job_id'], operation_status='submitted')
            self._save_analysis(value)
        return job

    def _advance_analysis(self, owner, identity):
        value = self.store.analysis(owner, identity)
        if value['state'] in TERMINAL:
            return self._analysis_public(value)
        with _run_lock(self.root / 'locks' / (identity + '.lock')) as acquired:
            if not acquired:
                return self._analysis_public(value)
            value = self.store.analysis(owner, identity)
            try:
                stopping = value['stop_requested'] or self.clock() >= value['deadline_at']
                if stopping and value['operation_status'] == 'reserved':
                    value.update(state='cancelled' if value['stop_requested'] else 'completed',
                        operation_status='cancelled', stop_proven=True,
                        stop_reason='developer_stopped' if value['stop_requested'] else 'budget_exhausted')
                    return self._analysis_public(self._save_analysis(value))
                if value['input'] is None:
                    value['input'] = self._analysis_document(value)
                    self.protocol.validate_input(value['input'])
                    value['input_sha256'] = digest(value['input'])
                    self._save_analysis(value)
                if value['operation_status'] == 'reserved':
                    preflight = getattr(self.reviewer, 'preflight', None)
                    if callable(preflight):
                        preflight(value['deadline_at'])
                    if value['input_refs'] is None:
                        files = {self.protocol.INPUT_NAME: canonical_bytes(value['input'])}
                        files.update({frame['file_name']: self.store.read_blob(frame['sha256'])
                            for frame in value['input']['observation']['frames']})
                        value['input_refs'] = self.reviewer.upload(value['input'], files)
                        self._save_analysis(value)
                    submit = self.reviewer.prepare_submission(value['id'], value['input'],
                        value['deadline_at'], value['input_refs'])
                    if not self.store.claim_analysis(owner, identity, self.clock()):
                        return self._analysis_public(self.store.analysis(owner, identity))
                    value['operation_status'] = 'claimed'
                    try:
                        job = submit()
                    except Exception:
                        value['state'] = 'submission_unknown'
                        return self._analysis_public(self._save_analysis(value))
                    value.update(operation_status='submitted', state='running', job_id=job['job_id'])
                    return self._analysis_public(self._save_analysis(value))
                job = self._analysis_job(value)
                if job is None:
                    value['state'] = 'stopping' if value['stop_requested'] else 'submission_unknown'
                elif job['state'] in REMOTE_ACTIVE:
                    if stopping:
                        self.reviewer.cancel(job['job_id'])
                        value['state'] = 'stopping'
                elif job.get('stop_proven') is not True:
                    value['state'] = 'submission_unknown'
                elif job['state'] != 'completed':
                    value.update(state='cancelled' if stopping else 'failed', stop_proven=True,
                        operation_status='cancelled' if stopping else 'failed',
                        stop_reason='developer_stopped' if value['stop_requested'] else
                            'budget_exhausted' if stopping else 'agent_failed',
                        error=None if stopping else dict(code='video_learning_analysis_agent_failed'))
                else:
                    result = self.reviewer.collect(job['job_id'], value['input_sha256'], value['input'])
                    self.protocol.validate_result(result, value['input_sha256'], value['input'])
                    receipt = self.reviewer.model_frame_receipt(job['job_id'])
                    expected = {frame['sha256'] for frame in value['input']['observation']['frames']}
                    received = {frame.get('sha256') for frame in receipt.get('images', [])
                        if frame.get('model_request_completed') is True}
                    require(expected <= received, 'video_learning_analysis_frame_transport_incomplete', 409)
                    actual_duration = value['frozen']['reference']['duration_seconds']
                    require(all(segment['end_seconds'] <= actual_duration for segment
                        in result['analysis']['candidate_segments']), 'video_learning_analysis_segment_outside_media', 409)
                    value.update(result=result, observation_receipt=receipt, state='completed',
                        operation_status='completed', stop_proven=True, stop_reason='analysis_complete')
                self._save_analysis(value)
            except ServiceError as error:
                value['error'] = dict(code=error.code)
                if value['operation_status'] == 'reserved' and error.status < 500:
                    value.update(state='failed', operation_status='failed', stop_proven=True,
                        stop_reason='invalid_input_or_result')
                elif value.get('job_id') and 'job' in locals() and job and job.get('stop_proven') is True:
                    value.update(state='failed', operation_status='failed', stop_proven=True,
                        stop_reason='invalid_input_or_result')
                else:
                    value['state'] = 'submission_unknown'
                self._save_analysis(value)
            except Exception:
                value.update(state='submission_unknown', error=dict(code='video_learning_analysis_execution_unknown'))
                self._save_analysis(value)
        return self._analysis_public(self.store.analysis(owner, identity))
