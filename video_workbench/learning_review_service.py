"""Opted-in picture/video consumers; legacy operations keep their own protocol."""
from copy import deepcopy

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_picture_review_v2 as classified
from .learning_review_reliability import (CONTRACT, EVIDENCE_SCHEMA, freeze_binding,
    validate_binding, review_document, validate_transport, validate_provenance, decision, capture_frames,
    validate_job_limits, require)
from .scene_review_reliability import image_provenance, retain_png


class LearningClassifiedReviewMixin:
    def _capabilities_public(self):
        value = super()._capabilities_public()
        value['classified_review'] = dict(contract=CONTRACT,
            configured=self.classified_reviewer is not None, readiness='historical_frozen_tasks_only',
            new_requests_enabled=False,
            viewport=dict(width=1280, height=720), evidence_scope='static_samples_only',
            selection_field='review_contract', automatically_selected=False)
        return value

    def _freeze_learning_options(self, body, frozen, *, prior=None):
        super()._freeze_learning_options(body, frozen, prior=prior)
        binding = freeze_binding(body.get('review_contract'), gateway=self.classified_reviewer,
            capability=frozen.get('runtime_capabilities', {}).get('scene_space'),
            width=frozen['width'], height=frozen['height'], deadline_at=self.clock() + 30)
        if binding is None:
            frozen.pop('classified_review', None)
        else:
            selection = frozen.get('capability_selection', {}).get('creation', [])
            require(not selection or 'canvas' in selection, 'learning_review_scope_not_admitted', 422)
            frozen['classified_review'] = binding

    @staticmethod
    def _classified_binding(run):
        value = run['frozen'].get('classified_review')
        return validate_binding(value) if value is not None else None

    def _classified_gateway(self, run, operation):
        binding = self._classified_binding(run)
        if binding is None or operation['stage'] != 'review':
            return None
        require(operation['input'].get('constraints', {}).get('learning_review_binding') == binding,
            'learning_review_operation_binding_changed')
        gateway = self.classified_reviewer
        require(gateway is not None and gateway.profile == binding['profile']
            and gateway.profile_revision == binding['profile_revision'],
            'learning_classified_review_unavailable', 503)
        return gateway

    def _operation_protocol(self, run, stage):
        return classified if self._classified_binding(run) is not None and stage == 'review' else self.protocol

    def _classified_document(self, run, document):
        binding = self._classified_binding(run)
        if binding is None:
            return document
        document['constraints']['allowed_engines'] = ['canvas']
        if document['mode'] == 'review':
            return review_document(document, binding)
        if run['stage'] == 'render' and 'duration_seconds' in document['constraints']:
            document['constraints']['sample_capture'] = deepcopy(binding['sample_capture'])
        return document

    def _validate_scene_author(self, run, document, result):
        metadata = super()._validate_scene_author(run, document, result)
        if self._classified_binding(run) is not None:
            require(metadata is not None and metadata.get('plan') is not None
                and result['source']['engine'] == 'canvas', 'learning_review_scene_plan_required', 422)
        return metadata

    def _classified_provenance(self, run):
        candidates = self._context_candidates(run) + run.get('internal_attempts', [])
        base_id = run['frozen'].get('base_candidate_id')
        if base_id and run['frozen'].get('base_observation') and not any(row['id'] == base_id for row in candidates):
            baseline = next((row for row in self._all_candidates(run['owner'], run['project_id']) if row['id'] == base_id), None)
            require(baseline is not None and self._baseline_observation(baseline) == run['frozen']['base_observation']
                and baseline['source'] == run['frozen']['base_source']
                and baseline.get('method') == run['frozen']['learned_method'], 'learning_review_baseline_changed')
            candidates = candidates + [baseline]
        records = []
        from agent_service.profiles.video_animation_create import source_sha256
        for candidate in candidates:
            saved = candidate.get('review_image_provenance')
            if not saved:
                continue
            plan = candidate['scene_space']['plan']
            require(source_sha256(candidate['source']) == candidate['source_sha256'],
                    'learning_review_source_changed')
            static = 'duration_seconds' not in run['frozen']
            records += validate_provenance(saved, source_sha256=candidate['source_sha256'],
                plan_sha256=digest(plan), render_id=candidate['render_operation_id'],
                frame_range=dict(start_frame=0, end_frame=1 if static else plan['duration_frames']),
                read_blob=self.store.read_blob, static=static)
        return records

    def _classified_preflight(self, run, operation, gateway):
        binding = self._classified_binding(run)
        if binding is None or operation['stage'] != 'review':
            return
        require(gateway.preflight(run['deadline_at']) == binding['review_route'],
                'learning_review_frozen_route_changed')
        validate_transport(operation['input'], read_blob=self.store.read_blob,
            provenance=self._classified_provenance(run))

    def _classified_result(self, run, operation, result, receipt):
        binding = self._classified_binding(run)
        if binding is None or operation['stage'] != 'review':
            return
        verdict = decision(result, operation['input'], binding, receipt,
            job_id=operation['job_id'], read_blob=self.store.read_blob)
        candidate = self._candidate(run)
        candidate['review_decision'] = deepcopy(verdict)
        candidate['review_evidence'] = dict(schema=EVIDENCE_SCHEMA,
            binding_sha256=binding['sha256'], input_sha256=operation['input_sha256'],
            result_sha256=digest(result), policy_sha256=digest(binding['policy']),
            model_limits=deepcopy(binding['model_limits']), model_limits_scope=deepcopy(binding['model_limits_scope']),
            model_usage_receipt=deepcopy(operation.get('model_usage_receipt', dict(completeness='unknown'))),
            usage_scope='original_learning_job_accounting_not_established_by_visual_gate',
            decision=deepcopy(verdict), transport_sha256=digest(receipt),
            frames=deepcopy(operation['input']['observation']['frames']),
            renderer_provenance=self._classified_provenance(run),
            source_reference_scope='original_or_decoded_reference_samples_not_renderer_capture',
            continuous_motion_verified=False, audio_verified=False)
        operation['review_decision'] = deepcopy(verdict)
        operation['review_evidence'] = deepcopy(candidate['review_evidence'])
        artifact = candidate['artifacts']['classified-review'] = dict(
            id=candidate['id'] + '-classified-review', mime_type='application/json')
        from agent_service.contracts import canonical_bytes
        artifact['sha256'] = self.store.blob(canonical_bytes(candidate['review_evidence']))

    def _classified_submission_options(self, run, operation):
        binding = self._classified_binding(run)
        if binding is None or operation['stage'] != 'review':
            return {}
        return dict(model_limits=deepcopy(binding['model_limits']))

    def _classified_job_limits(self, run, operation, job):
        binding = self._classified_binding(run)
        if binding is not None and operation['stage'] == 'review':
            try:
                validate_job_limits(job, binding)
            except ServiceError as error:
                if job.get('stop_proven') is True:
                    operation.update(status='failed', error=error.code)
                    self._finish_operation(run, operation)
                raise

    def _classified_stopped_capture_failure(self, run, operation, result, error):
        # The caller already obtained a stopped render result. A definitive
        # capture mismatch is a failed observation, not an uncertain execution
        # that should be polled forever or dispatched again.
        if result.get('stop_proven') is True:
            operation.update(status='failed', error=error.code,
                result={key: value for key, value in result.items() if not key.endswith('_path')})
            self._finish_operation(run, operation)
            candidate=self._candidate(run)
            candidate.update(status='observation_failed',render_operation_id=operation['id'],
                sampling_error=error.code)
            checksum=result.get('sha256')
            if checksum:
                static=bool(result.get('picture_path'))
                kind='picture' if static else 'media'
                candidate['picture_sha256' if static else 'media_sha256']=checksum
                candidate.setdefault('artifacts',{})[kind]=dict(id=candidate['id']+'-'+kind,
                    sha256=checksum,mime_type='image/png' if static else 'video/mp4')
            run['error']=dict(code=error.code)
            self._finish(run,'sampling_failed','failed')

    def _retain_classified_still(self, run, operation, candidate, raw):
        if self._classified_binding(run) is None:
            return None
        checksum = retain_png(raw, 'image/png')
        plan = candidate['scene_space']['plan']
        candidate['review_image_provenance'] = [image_provenance(
            dict(identity='output-1', frame=0, artifact_sha256=checksum), raw,
            source_sha256=candidate['source_sha256'], plan_sha256=digest(plan),
            kind='static_preview', render_id=operation['id'],
            frame_range=dict(start_frame=0, end_frame=1))]
        return checksum, 'image/png'

    def _classified_video_observation(self, run, operation, candidate, result):
        binding = self._classified_binding(run)
        if binding is None:
            return None
        require(result.get('sample_capture') == binding['sample_capture'],
                'learning_review_lossless_capture_missing')
        frame_range = dict(start_frame=0, end_frame=round(run['frozen']['duration_seconds'] * 24))
        prepare=getattr(self.renderer,'prepare_sampling',None)
        reference=prepare(run['owner'],operation['id'],max_frames=3,
            deadline_at=run['deadline_at']) if prepare else None
        rows=(self.renderer.read_sampling(run['owner'],reference) if reference else
            self.renderer.frames(operation['id'],max_frames=3))
        if reference is not None:candidate['native_sampling_ref']=reference
        observation, provenance = capture_frames(rows, result=result, candidate=candidate,
            render_id=operation['id'], frame_range=frame_range, store_blob=self.store.blob)
        candidate['review_image_provenance'] = provenance
        observation.update(capture_source='renderer_original_lossless_png',
            media_sha256=result['sha256'], continuous_motion_verified=False, audio_verified=False)
        return observation
