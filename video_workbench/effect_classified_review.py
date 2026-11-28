"""New explicitly selected effect reviews over the common static v2 contract."""
from copy import deepcopy
import hashlib

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_picture_review_v2 as protocol
from agent_service.profiles.video_animation_create import source_sha256
from .learning_review_reliability import (CONTRACT, freeze_binding, validate_binding,
    review_document, capture_frames, validate_transport, validate_provenance, decision,
    validate_job_limits, require)


class EffectClassifiedReviewMixin:
    @staticmethod
    def _effect_review_binding(run):
        value = run['frozen'].get('constraints', {}).get('classified_review')
        return validate_binding(value) if value is not None else None

    def _freeze_effect_review(self, data, choice, *, origin=None):
        if origin is not None:
            binding = origin['frozen'].get('constraints', {}).get('classified_review')
            require(choice is None or binding is not None and choice == CONTRACT,
                    'effect_review_replay_contract_changed')
            return
        if choice is None:
            return
        require(not data.get('collaboration') and not data.get('resource_authoring')
            and not data.get('resource_planning'), 'effect_review_scope_not_admitted')
        require(not data.get('reference'), 'effect_review_reference_not_admitted', 422)
        capability = data.get('constraints', {}).get('scene_space_capability')
        binding = freeze_binding(choice, gateway=self.classified_reviewer,
            capability=capability, width=1280, height=720,
            deadline_at=self.clock() + data['budget']['seconds'])
        data.setdefault('constraints', {})['classified_review'] = binding
        data['constraints']['allowed_engines'] = ['canvas']

    def _effect_review_gateway(self, run):
        binding = self._effect_review_binding(run)
        if binding is None:
            return None
        gateway = self.classified_reviewer
        require(gateway is not None and gateway.profile == binding['profile']
            and gateway.profile_revision == binding['profile_revision'],
            'effect_classified_review_unavailable', 503)
        return gateway

    def _effect_lossless_render(self, run, document):
        binding = self._effect_review_binding(run)
        if binding is None:
            return document
        require(document.get('scene_space') is not None and not document.get('sound_events'),
                'effect_review_scene_plan_required', 422)
        document['document']['constraints']['sample_capture'] = deepcopy(binding['sample_capture'])
        return document

    def _effect_review_document(self, owner, run, candidate):
        binding = self._effect_review_binding(run)
        operation_id = self._artifact_operation_id(owner, candidate['operation_id'])
        rendered = self.renderer.lookup(operation_id)
        require(rendered is not None and rendered.get('technical_passed') is True
            and rendered.get('stop_proven') is True, 'effect_review_render_unavailable')
        render_operation = self.commissions.operation(owner, candidate['operation_id'])
        plan = render_operation['input'].get('scene_space')
        require(plan is not None, 'effect_review_scene_plan_required')
        artifact = dict(source_sha256=source_sha256(candidate['source']), scene_space=dict(plan=plan))
        directory = self.root / 'frame-inputs'
        directory.mkdir(parents=True, exist_ok=True)
        def store(raw):
            checksum = hashlib.sha256(raw).hexdigest()
            path = directory / checksum
            if path.exists():
                require(path.read_bytes() == raw, 'effect_review_frame_changed')
            else:
                path.write_bytes(raw)
            return checksum
        frame_range = dict(start_frame=0, end_frame=round(run['frozen'].get('duration_seconds', 18) * 24))
        prepare=getattr(self.renderer,'prepare_sampling',None)
        reference=prepare(owner,operation_id,max_frames=3,deadline_at=run['deadline_at']) if prepare else None
        rows=(self.renderer.read_sampling(owner,reference) if reference else
            self.renderer.frames(operation_id,max_frames=3))
        observation, provenance = capture_frames(rows,
            result=rendered, candidate=artifact, render_id=operation_id,
            frame_range=frame_range, store_blob=store)
        frames = [dict(row, purpose='output', file_name=row['frame_id'] + '.png')
                  for row in observation['frames']]
        planned = next((op['result'] for op in run['operations']
            if op['input'].get('mode') == 'plan' and op['status'] == 'completed'), None)
        standards = run['frozen'].get('criteria') or (planned or {}).get('criteria', [])
        document = dict(project_id=run['id'], experiment_id=run['id'], goal=run['frozen']['goal'],
            criteria=[row['description'] for row in standards], feedback=run['frozen'].get('feedback', ''),
            observation=dict(frames=frames))
        document = review_document(document, binding)
        document['constraints']['effect_review'] = dict(candidate_id=candidate['id'],
            render_operation_id=operation_id, source_sha256=artifact['source_sha256'],
            plan_sha256=digest(plan), provenance=provenance,
            comparison_scope='current_output_only_no_unobserved_baseline_preference')
        protocol.validate_input(document)
        validate_transport(document, read_blob=lambda sha: (directory / sha).read_bytes(), provenance=provenance)
        return document

    def _effect_review_preflight(self, run, operation, gateway):
        binding = self._effect_review_binding(run)
        require(operation['input']['constraints']['learning_review_binding'] == binding,
                'effect_review_operation_binding_changed')
        require(gateway.preflight(run['deadline_at']) == binding['review_route'],
                'effect_review_frozen_route_changed')
        evidence = operation['input']['constraints']['effect_review']
        current = next(row for row in run['candidates'] if row['id'] == evidence['candidate_id'])
        original = self.commissions.operation(run['owner'], current['operation_id'])
        plan = original['input']['scene_space']
        require(source_sha256(current['source']) == evidence['source_sha256']
            and digest(plan) == evidence['plan_sha256'], 'effect_review_candidate_binding_changed')
        validate_provenance(evidence['provenance'], source_sha256=evidence['source_sha256'],
            plan_sha256=digest(plan), render_id=evidence['render_operation_id'],
            frame_range=dict(start_frame=0, end_frame=plan['duration_frames']),
            read_blob=lambda sha: (self.root / 'frame-inputs' / sha).read_bytes())
        validate_transport(operation['input'],
            read_blob=lambda sha: (self.root / 'frame-inputs' / sha).read_bytes(),
            provenance=evidence['provenance'])

    def _record_classified_effect_review(self, owner, run, operation):
        binding = self._effect_review_binding(run)
        document, result = operation['input'], operation['result']
        evidence = document['constraints']['effect_review']
        current = next(row for row in run['candidates'] if row['id'] == evidence['candidate_id'])
        require(source_sha256(current['source']) == evidence['source_sha256']
            and self._artifact_operation_id(owner, current['operation_id']) == evidence['render_operation_id'],
            'effect_review_candidate_binding_changed')
        receipt = self._effect_review_gateway(run).model_frame_receipt(operation['job_id'])
        verdict = decision(result, document, binding, receipt, job_id=operation['job_id'],
            read_blob=lambda sha: (self.root / 'frame-inputs' / sha).read_bytes())
        opinion = result['review']
        issues = [row['observation'] for row in opinion['findings'] if row['severity'] == 'blocking']
        if not verdict['passed'] and not issues:
            issues = ['Frozen score/stop gate is not satisfied; optional advice is not the failure cause.']
        selected = current['id'] if verdict['passed'] else run['best_candidate_id'] or current['id']
        return self.commissions.record_review(owner, run['id'], dict(
            operation_id=operation['id'], candidate_id=current['id'],
            best_candidate_id=selected, outcome='goal_met' if verdict['passed'] else 'inconclusive',
            remaining_issues=issues, criteria_version=digest(binding['policy']),
            criteria_results=deepcopy(opinion['assessments']), limitations=deepcopy(opinion['limitations']),
            summary=opinion['summary'], evidence=dict(schema='video.effect-classified-review-consumption.v1',
                contract=CONTRACT, input_sha256=operation['input_sha256'], result_sha256=digest(result),
                policy_sha256=digest(binding['policy']), binding_sha256=binding['sha256'],
                model_limits=deepcopy(binding['model_limits']), model_limits_scope=deepcopy(binding['model_limits_scope']),
                usage_scope='consult_original_agent_job_static_gate_does_not_establish_usage_completeness',
                transport_sha256=digest(receipt), decision=verdict, renderer_provenance=deepcopy(evidence),
                continuous_motion_verified=False, audio_verified=False)), operation['id'] + ':review')

    def _effect_classified_submission_options(self, run, operation):
        binding = self._effect_review_binding(run)
        if binding is None or operation['stage'] != 'review':
            return {}
        return dict(model_limits=deepcopy(binding['model_limits']))

    def _effect_classified_job_limits(self, run, operation, job):
        binding = self._effect_review_binding(run)
        if binding is not None and operation['stage'] == 'review':
            try:
                validate_job_limits(job, binding)
            except ServiceError as error:
                if job.get('stop_proven') is True:
                    self.commissions.fail(run['owner'], operation['id'], error.code)
                    return False
                raise
        return True

    def _classified_effect_feedback(self, operation):
        opinion = operation['result']['review']
        return '\n'.join(row['recommendation'] for row in opinion['findings']
                         if row['severity'] == 'blocking') or opinion['summary']

    def _effect_classified_stop(self, owner, run):
        if self._effect_review_binding(run) is None or not run['reviews']:
            return False
        latest = run['reviews'][-1]
        operation = next(row for row in run['operations'] if row['id'] == latest['operation_id'])
        if operation['result']['review']['stop_recommended'] is not True:
            return False
        self.commissions.stop(owner, run['id'], 'failed', dict(
            code='effect_classified_review_recommends_stop',
            message='分类评审建议停止本次迭代；保留意见和证据，不继续创作或渲染',
            review_operation_id=operation['id']))
        return True
