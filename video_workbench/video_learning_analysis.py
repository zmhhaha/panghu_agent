"""Durable, separately budgeted reference analysis; never starts creation."""
from copy import deepcopy

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .animation_creation_service import _run_lock
from .picture_learning_store import TERMINAL, identifier, new_id, owner_id, require
from .picture_learning_service import _text, REMOTE_ACTIVE
from .video_learning_media import LIMITATIONS
from .remote_reference_service import remote_reference, MAX_OBSERVATION_REQUESTS, MAX_SUPPLEMENTAL_FRAMES, normalized_crop


REMOTE_ANALYSIS_OVERVIEW_SAMPLES = 8
UPLOADED_ANALYSIS_OVERVIEW_SAMPLES = 24


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
        seconds = body.get('max_seconds', 3600)
        require(type(seconds) is int and 60 <= seconds <= 3600,
                'video_learning_analysis_budget_invalid')
        now, identity = self.clock(), 'analysis-' + new_id()
        frozen = dict(reference=deepcopy(reference), goal=goal, max_seconds=seconds,
            # Remote decoders share a bounded network budget across samples.
            # Begin with an overview; a stopped role can request specific detail.
            # Existing analyses continue to use their own frozen sample count.
            sample_count=(REMOTE_ANALYSIS_OVERVIEW_SAMPLES if remote_reference(reference)
                else UPLOADED_ANALYSIS_OVERVIEW_SAMPLES), frame_observation='sampled_frames_only',
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
        result['observation_request_history'] = deepcopy(value.get('remote_observations', []))
        result['role_operations'] = [{key: item.get(key) for key in
            ('operation_id', 'job_id', 'input_sha256', 'status')} for item in value.get('role_operations', [])]
        reference = value['frozen']['reference']
        if remote_reference(reference) and (value.get('observation') or {}).get('frames'):
            frame = value['observation']['frames'][0]
            result['reference_observation'] = self._remote_reference_public(reference) | dict(
                frame_url=self.prefix + '/references/' + reference['id'] + '/analyses/' + value['id']
                    + '/frames/' + frame['sha256'], actual_pts=frame['actual_pts'],
                observed_width=frame['width'], observed_height=frame['height'],
                observed_sha256=frame['sha256'], observation_status='sampled_frames_only')
        return result

    def analysis_reference_artifact(self, owner, reference_id, analysis_id, frame_sha256):
        self._reference(owner, reference_id)
        value = self.store.analysis(owner, analysis_id)
        require(value['reference_id'] == reference_id, 'video_learning_analysis_not_found', 404)
        observations = ([value['observation']] if value.get('observation') else []) + [
            item['observation'] for item in value.get('remote_observations', [])]
        require(any(frame['sha256'] == frame_sha256 for observed in observations
            for frame in observed['frames']), 'remote_reference_evidence_not_found', 404)
        self.store.read_blob(frame_sha256)
        return self.store.blob_path(frame_sha256), 'image/jpeg'

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
        observed = value.get('observation')
        if observed is None:
            if remote_reference(reference):
                observed = self._remote_sample_reference(reference, 0, reference['duration_seconds'],
                    count=value['frozen']['sample_count'], deadline_at=value['deadline_at'])
            else:
                observed = self.media.sample(self.store.blob_path(reference['sha256']),
                    media_sha256=reference['sha256'], start_seconds=0,
                    end_seconds=reference['duration_seconds'], store=self.store,
                    sample_count=value['frozen']['sample_count'], deadline_at=value['deadline_at'])
        value['observation'] = observed
        duration = reference['duration_seconds']
        frames = self._purpose_frames(observed, 'reference')
        sources = [self._remote_source_input(reference, 0, duration)] if remote_reference(reference) else []
        view = dict(work_kind='reproduction', frozen=dict(reference_sources=sources),
            remote_observations=value.get('remote_observations', []))
        document = dict(schema=self.protocol.INPUT_SCHEMA, mode='analyze',
            project_id='reference-' + reference['id'], experiment_id=value['id'],
            work_kind='reproduction', goal=value['frozen']['goal'], criteria=[],
            constraints=dict(width=1280, height=720, fps=24, duration_seconds=duration,
                allowed_engines=['html-svg', 'canvas', 'three']),
            observation=dict(frames=self._with_remote_supplemental_frames(view, frames),
                coverage=deepcopy(observed['coverage']),
                audio=dict(status='unverified', has_audio=reference['has_audio'], learning_requested=False)),
            base_source=None, feedback='', knowledge_snapshot=None, prior_review=None,
            learned_method=None, knowledge_binding=dict(use_wikiskill=False), assets={},
            frozen_at=value['created_at'])
        if remote_reference(reference):
            document['constraints']['reference_analysis_sampling'] = dict(
                strategy='sparse_overview_then_requested_detail',
                initial_sample_count=value['frozen']['sample_count'],
                supplied_frame_count=len(document['observation']['frames']),
                instructions=[
                    'The supplied pixels are sparse samples, not continuous playback or an exhaustive effect inventory.',
                    'If an interval or effect is uncertain, use a bounded observation_request under the existing source contract only when needed. Do not infer unseen effects from the title or fill a requested count.',
                    'Keep the same analysis deadline and existing supplemental frame/request limits. Do not repeat the whole-source overview, generate source or start creation.',
                    'Ground candidate intervals in the actual supplied frame IDs and report unobserved gaps or ambiguity in limitations.',
                ])
        return self._remote_document(view, document)

    def _analysis_observation_handoff(self, value, result, receipt):
        request = result.get('observation_request')
        if request is None:
            return False
        history = value.get('remote_observations', [])
        require(len(history) < MAX_OBSERVATION_REQUESTS
            and sum(len(item['request']['times']) for item in history) + len(request['times'])
                <= MAX_SUPPLEMENTAL_FRAMES, 'remote_reference_observation_budget_exhausted', 409)
        value.setdefault('role_operations', []).append(dict(
            operation_id=value.get('active_operation_id', value['id']), job_id=value['job_id'],
            input=deepcopy(value['input']), input_sha256=value['input_sha256'],
            result=deepcopy(result), observation_receipt=deepcopy(receipt), status='completed', stop_proven=True))
        value.update(remote_observation_pending=deepcopy(request), state='observing_reference',
            observation_receipt=deepcopy(receipt), operation_status='observing')
        self._save_analysis(value)
        return True

    def _consume_analysis_observation(self, value):
        request = value.get('remote_observation_pending')
        if request is None:
            return False
        reference = value['frozen']['reference']
        require(remote_reference(reference) and request['reference_id'] == reference['id'],
            'remote_reference_unknown_source', 404)
        require(all(0 <= time < reference['duration_seconds'] for time in request['times']),
            'remote_reference_observation_outside_scope')
        observed = self._observe_remote(reference, times=request['times'],
            crop=normalized_crop(request.get('crop')), quality=request['quality'],
            deadline_at=value['deadline_at'])
        history = value.setdefault('remote_observations', [])
        history.append(dict(operation_id=value['id'] + '-reference-' + str(len(history) + 1),
            request=deepcopy(request), observation=observed))
        value.pop('remote_observation_pending')
        value.update(active_operation_id=value['id'] + '-role-' + str(len(history) + 1),
            input=None, input_sha256=None, input_refs=None, job_id=None,
            operation_status='reserved', state='queued')
        self._save_analysis(value)
        return True

    def _save_analysis(self, value):
        value['updated_at'] = self.clock()
        return self.store.save_analysis(value)

    def _analysis_job(self, value):
        if value.get('job_id'):
            return self.reviewer.poll(value['job_id'])
        try:
            job = self.reviewer.lookup(value.get('active_operation_id', value['id']))
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
                if stopping and value['operation_status'] in {'reserved', 'observing'}:
                    value.update(state='cancelled' if value['stop_requested'] else 'completed',
                        operation_status='cancelled', stop_proven=True,
                        stop_reason='developer_stopped' if value['stop_requested'] else 'budget_exhausted')
                    return self._analysis_public(self._save_analysis(value))
                if value.get('remote_observation_pending'):
                    self._consume_analysis_observation(value)
                    return self._analysis_public(value)
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
                    submit = self.reviewer.prepare_submission(value.get('active_operation_id', value['id']), value['input'],
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
                    if self._analysis_observation_handoff(value, result, receipt):
                        if stopping:
                            value.update(state='cancelled' if value['stop_requested'] else 'completed',
                                operation_status='cancelled', stop_proven=True,
                                stop_reason='developer_stopped' if value['stop_requested'] else 'budget_exhausted')
                            self._save_analysis(value)
                        return self._analysis_public(value)
                    actual_duration = value['frozen']['reference']['duration_seconds']
                    require(all(segment['end_seconds'] <= actual_duration for segment
                        in result['analysis']['candidate_segments']), 'video_learning_analysis_segment_outside_media', 409)
                    value.update(result=result, observation_receipt=receipt, state='completed',
                        operation_status='completed', stop_proven=True, stop_reason='analysis_complete')
                self._save_analysis(value)
            except ServiceError as error:
                value['error'] = dict(code=error.code)
                if value.get('remote_observation_pending'):
                    value.update(state='observing_reference' if error.status >= 500 else 'failed',
                        operation_status='observing' if error.status >= 500 else 'failed',
                        stop_proven=error.status < 500,
                        stop_reason=None if error.status >= 500 else 'reference_observation_failed')
                elif value['operation_status'] == 'reserved' and error.status < 500:
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
