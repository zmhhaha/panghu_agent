"""Admitted scene learning, source custody and cumulative resource accounting."""
from copy import deepcopy
import math
import time

from agent_service.contracts import canonical_bytes, digest
from .picture_learning_store import require
from .scene_space_learning import (METHOD_SCHEMA as SCENE_METHOD_SCHEMA,
    scene_method_guidance, transferable_scene_method, validate_scene_method)

MAX_STATIC_DIAGNOSTIC_RENDER_SECONDS = 60


class SceneSpaceLearningMixin:
    def _freeze_learning_options(self, body, frozen, *, prior=None):
        super()._freeze_learning_options(body, frozen, prior=prior)
        capability = frozen.get('runtime_capabilities', {}).get('scene_space')
        # New experiments opt into the new carrier only when its actual host
        # runtime was admitted. Retry copies the previous frozen object and
        # never calls this hook, preserving historical interpretation/routes.
        if type(capability) is dict and capability.get('admitted') is True:
            frozen['scene_space_learning'] = dict(schema='video.scene-space-learning-binding.v1',
                method_schema=SCENE_METHOD_SCHEMA, runtime=deepcopy(capability),
                capability_sha256=digest(capability))
        else:
            frozen.pop('scene_space_learning', None)

    @staticmethod
    def _scene_source_context(run, document):
        transfer = run['work_kind'] == 'transfer'
        frames = [row for row in document['observation']['frames']
                  if not transfer or row['purpose'] not in {'reference', 'baseline'}]
        scope = ('scene_data_only' if transfer and not frames else 'sampled_frames_only'
                 if 'fps' in document['constraints'] or 'duration_seconds' in run['frozen']
                 else 'static_image')
        ids = [row.get('frame_id', row['file_name']) for row in frames]
        refs = [dict(id=identity, sha256=row['sha256']) for identity, row in zip(ids, frames)]
        return scope, refs, ids

    def _scene_document(self, run, document):
        binding = run['frozen'].get('scene_space_learning')
        if binding is None:
            return document
        require(binding.get('schema') == 'video.scene-space-learning-binding.v1'
            and digest(binding['runtime']) == binding['capability_sha256'],
            'scene_space_learning_binding_changed', 409)
        scope, refs, frame_ids = self._scene_source_context(run, document)
        offer = scene_method_guidance(scope)
        offer.update(runtime=deepcopy(binding['runtime']), source_references=refs,
            source_frame_ids=frame_ids, observation_scope='static_or_sampled_inference_only',
            host_render_budget=self._scene_run_status(run))
        if run['work_kind'] == 'transfer':
            method = document.get('learned_method')
            if type(method) is dict and type(method.get('scene_space')) is dict:
                document['learned_method'] = transferable_scene_method(method)
        if run.get('active_candidate_id') and run['stage'] in {'render', 'review', 'transfer_plan'}:
            candidate = self._candidate(run)
            if candidate.get('work_kind') == run['work_kind'] and candidate.get('scene_space'):
                offer['candidate_method'] = deepcopy(candidate['scene_space'])
                offer['candidate_checks'] = deepcopy(candidate.get('scene_space_checks'))
        document['constraints']['scene_space_learning'] = offer
        return document

    def _validate_scene_author(self, run, document, result):
        if run['frozen'].get('scene_space_learning') is None:
            return None
        value = (result.get('method') or {}).get('scene_space')
        if value is None:
            return None
        scope, refs, frame_ids = self._scene_source_context(run, document)
        checked = validate_scene_method(value, source_scope=scope, source_refs=refs, frame_ids=frame_ids)
        plan = checked['plan']
        if plan is not None:
            runtime = run['frozen']['scene_space_learning']['runtime']
            require(result['source']['engine'] in runtime.get('engines', []),
                'scene_space_learning_engine_not_admitted', 409)
            require(plan['viewport'] == dict(width=document['constraints']['width'],
                height=document['constraints']['height']), 'scene_space_learning_viewport_changed', 409)
            frames = (int(run['frozen']['duration_seconds'] * 24)
                      if 'duration_seconds' in run['frozen'] else 1)
            require(plan['duration_frames'] == frames, 'scene_space_learning_duration_changed', 409)
        return checked

    def _prepare_scene_candidate(self, run, operation, candidate):
        checked = self._validate_scene_author(run, operation['input'],
            dict(method=candidate.get('method'), source=candidate['source']))
        if checked is None:
            return
        if candidate.get('scene_space') is not None:
            require(candidate['scene_space'] == checked,
                'scene_space_learning_candidate_changed', 409)
            return
        preview_started = time.monotonic()
        from .scene_space_preview import inspect_world, layout_svg
        from .scene_space_plan import required_coverage
        report = dict(schema='video.scene-space-learning-checks.v1',
            status='valid', plan_sha256=digest(checked['plan']) if checked['plan'] is not None else None,
            evidence_scope='declared_scene_geometry_only',
            world=inspect_world(checked['world']) if checked['world'] is not None else None,
            coverage=required_coverage(checked['plan']) if checked['plan'] is not None else None,
            visual_quality='not_assessed', continuous_motion='not_observed', audio='not_observed')
        candidate.update(scene_space=checked, scene_space_checks=report)
        artifacts = candidate.setdefault('artifacts', {})
        for kind, content in (('scene-space', checked), ('scene-space-checks', report)):
            artifacts[kind] = dict(id=candidate['id'] + '-' + kind,
                sha256=self.store.blob(canonical_bytes(content)), mime_type='application/json')
        if checked['world'] is not None:
            artifacts['scene-space-layout'] = dict(id=candidate['id'] + '-scene-space-layout',
                sha256=self.store.blob(layout_svg(checked['world']).encode('utf-8')),
                mime_type='image/svg+xml', evidence_scope='layout_only_masks_outlined')
        if checked['plan'] is not None:
            task_id = candidate.setdefault('scene_space_task_id', 'learning-scene-' + candidate['id'])
            store = self._scene_store()
            store.freeze(run['owner'], task_id, checked['plan'])
            store.record_preview(run['owner'], task_id, candidate['id'] + '-layout', report)
            store.record_usage(run['owner'], task_id, id=candidate['id'] + '-layout',
                phase='preview', outcome='completed', model_calls=0, model_tokens=0,
                render_seconds=max(0, time.monotonic() - preview_started))
        self._scene_run_charge(run, candidate['id'] + '-layout',
            outcome='completed', seconds=max(0, time.monotonic() - preview_started), phase='preview')

    @staticmethod
    def _new_scene_run_budget(run_id, limit_seconds):
        return dict(schema='video.scene-space-learning-render-budget.v1',
            root_experiment_id=run_id, limit_seconds=limit_seconds, events=[], reservations=[],
            scope='layout_and_render_wall_time_only_model_usage_remains_in_job_receipts')

    @staticmethod
    def _scene_run_status(run):
        budget = run.get('scene_space_render_budget')
        if budget is None:
            return None
        require(budget.get('schema') == 'video.scene-space-learning-render-budget.v1'
            and budget['limit_seconds'] == run['budget']['max_seconds']
            and type(budget['events']) is list and len(budget['events']) <= 1024,
            'scene_space_learning_run_budget_changed', 409)
        ids = set()
        for event in budget['events']:
            require(event['id'] not in ids, 'scene_space_learning_run_budget_changed', 409)
            ids.add(event['id'])
            seconds = event['seconds']
            require(seconds is None or type(seconds) in (int, float) and 0 <= seconds <= 2**53-1
                and math.isfinite(seconds), 'scene_space_learning_measurement_invalid', 409)
        known = sum(event['seconds'] or 0 for event in budget['events'])
        unknown = any(event['seconds'] is None for event in budget['events'])
        remaining = max(0, budget['limit_seconds'] - known)
        return dict(schema='video.scene-space-learning-render-budget-status.v1',
            root_experiment_id=budget['root_experiment_id'], limit_seconds=budget['limit_seconds'],
            used_seconds=None if unknown else known, known_seconds_lower_bound=known,
            remaining_seconds=None if unknown else remaining,
            can_start_render=not unknown and remaining > 0 and len(ids) < 1024,
            scope=budget['scope'])

    def _scene_run_charge(self, run, identity, *, outcome, seconds, phase='render'):
        budget = run.get('scene_space_render_budget')
        if budget is None:
            return
        self._scene_run_status(run)
        previous = next((event for event in budget['events'] if event['id'] == identity), None)
        if previous is not None:
            require(previous['phase'] == phase and previous['outcome'] == outcome and previous['seconds'] == seconds,
                'scene_space_learning_settlement_changed', 409)
            return
        require(len(budget['events']) < 1024, 'scene_space_learning_usage_event_limit', 409)
        budget['events'].append(dict(id=identity, phase=phase, outcome=outcome, seconds=seconds))
        for reservation in budget['reservations']:
            if reservation['id'] == identity:
                reservation['status'] = 'settled'
        self._scene_run_status(run)

    def _scene_model_usage(self, run, operation, job):
        if run['frozen'].get('scene_space_learning') is None:
            return
        raw = job.get('usage')
        receipt = deepcopy(raw) if type(raw) is dict else dict(completeness='unknown')
        require(len(canonical_bytes(receipt)) <= 16384,
            'scene_space_learning_model_usage_invalid', 409)
        completeness = receipt.get('completeness', 'unknown')
        fields = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'request_count')
        known = (completeness == 'complete' and all(type(receipt.get(key)) is int
            and 0 <= receipt[key] <= 2**53-1 for key in fields))
        operation['model_usage_receipt'] = dict(schema='video.scene-space-model-usage-receipt.v1',
            job_id=job.get('job_id', operation.get('job_id')),
            completeness='complete' if known else 'partial' if completeness == 'partial' else 'unknown',
            usage=receipt, measurement_scope='agent_service_job_usage',
            observed_request_count=receipt['request_count'] if known else None,
            token_components={key:receipt[key] for key in fields[:-1]} if known else None)
        receipts = run.setdefault('scene_space_model_receipts', [])
        entry = dict(operation_id=operation['id'], receipt=deepcopy(operation['model_usage_receipt']))
        prior = next((row for row in receipts if row['operation_id'] == operation['id']), None)
        if prior is None:
            require(len(receipts) < 1024, 'scene_space_learning_usage_event_limit', 409)
            receipts.append(entry)
        else:
            require(prior == entry, 'scene_space_learning_model_usage_changed', 409)
        self.store.put('operations', operation)

    def _scene_model_status(self, run):
        if 'scene_space_model_receipts' not in run:
            return None
        receipts = [row['receipt'] for row in run['scene_space_model_receipts']]
        active = (self.store.read('operations', run['owner'], run['active_operation_id'])
                  if run.get('active_operation_id') else None)
        pending = active is not None and active['stage'] != 'render' and not active.get('model_usage_receipt')
        complete = not pending and all(row['completeness'] == 'complete' for row in receipts)
        known = [row for row in receipts if row['completeness'] == 'complete']
        components = {key:sum(row['token_components'][key] for row in known)
            for key in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')}
        requests = sum(row['observed_request_count'] for row in known)
        return dict(schema='video.scene-space-learning-model-usage.v1',
            completeness='complete' if complete else 'unknown',
            request_count=requests if complete else None,
            token_components=components if complete else None,
            known_request_count_lower_bound=requests, known_token_components_lower_bound=components,
            receipt_count=len(receipts), pending_operation_id=active['id'] if pending else None,
            scope='agent_service_job_usage_components_no_price_or_bill_inference')

    def _scene_store(self):
        if self._scene_usage_store is None:
            from .scene_space_store import SceneSpaceStore
            self._scene_usage_store = SceneSpaceStore(self.root / 'scene-space', clock=self.clock)
        return self._scene_usage_store

    def _scene_render_options(self, run, operation, candidate):
        if run['frozen'].get('scene_space_learning') is None:
            return {}
        self._prepare_scene_candidate(run, operation, candidate)
        plan = (candidate.get('scene_space') or {}).get('plan')
        return dict(scene_space=deepcopy(plan)) if plan is not None else {}

    def _scene_admit_render(self, run, operation, candidate, options):
        status = self._scene_run_status(run)
        if status is None:
            return run['deadline_at']
        require(status['can_start_render'], 'scene_space_learning_budget_unavailable', 409)
        budget = run['scene_space_render_budget']
        prior = next((item for item in budget['reservations'] if item['id'] == operation['id']), None)
        active = sum(item['seconds'] for item in budget['reservations']
            if item['status'] == 'active' and item['id'] != operation['id'])
        available = max(0, status['remaining_seconds'] - active)
        reserve = prior['seconds'] if prior else min(available, max(0, run['deadline_at'] - self.clock()))
        require(reserve <= available, 'scene_space_learning_budget_unavailable', 409)
        require(reserve > 0, 'scene_space_learning_budget_exhausted', 409)
        if options:
            store, task_id = self._scene_store(), candidate['scene_space_task_id']
            task = store.get(run['owner'], task_id)
            status = task['usage_status']
            diagnostic = (status['status'] == 'usage_unknown'
                and status['used']['model_tokens'] is None
                and status['used']['render_seconds'] is not None
                and 'duration_seconds' not in run['frozen']
                and options['scene_space']['duration_frames'] == 1)
            task_status = status if diagnostic else store.require_export(run['owner'], task_id)
            held = sum(row['render_seconds'] for row in task['reservations']
                if row['status'] == 'active' and row['id'] != operation['id'])
            reserve = min(reserve, max(0, task_status['remaining']['render_seconds'] - held))
            phase = 'preview' if diagnostic else 'render'
            if diagnostic:
                reserve = min(reserve, MAX_STATIC_DIAGNOSTIC_RENDER_SECONDS)
            require(reserve > 0, 'scene_space_learning_budget_exhausted', 409)
            require(operation.get('scene_space_render_phase', phase) == phase,
                'scene_space_learning_settlement_changed', 409)
            operation['scene_space_render_phase'] = phase
            if diagnostic:
                marker = dict(schema='video.scene-space-learning-diagnostic.v1',
                    scope='static_diagnostic_png_only', status='reserved', reason='model_usage_unknown',
                    plan_sha256=digest(options['scene_space']), source_sha256=candidate['source_sha256'],
                    max_frames=1, max_render_seconds=MAX_STATIC_DIAGNOSTIC_RENDER_SECONDS,
                    model_review_authorized=False, transfer_authorized=False, formal_export_authorized=False,
                    visual_quality='not_assessed', motion_observed=False, audio_observed=False)
                operation['scene_space_diagnostic'] = marker
                run['scene_space_diagnostic'] = deepcopy(marker)
            # Record the chosen phase before the separate shared reservation;
            # recovery must settle the same phase, including a preclaim failure.
            self.store.put('operations', operation)
            store.reserve(run['owner'], task_id, operation['id'], phase=phase,
                model_calls=0, model_tokens=0, render_seconds=reserve)
        if prior is None:
            require(len(budget['reservations']) < 1024, 'scene_space_learning_usage_event_limit', 409)
            budget['reservations'].append(dict(id=operation['id'], seconds=reserve, status='active'))
        self.store.put('experiments', run)
        return min(run['deadline_at'], self.clock() + reserve)

    def _scene_block_diagnostic_model(self, run, operation):
        """A diagnostic bitmap grants no further paid Agent work."""
        if operation['status'] != 'reserved' or not run.get('scene_space_diagnostic'):
            return False
        operation.update(status='cancelled', error='scene_space_diagnostic_model_not_authorized')
        self._finish_operation(run, operation)
        self._finish(run, 'scene_space_diagnostic_preview_only')
        return True

    def _scene_complete_diagnostic(self, run, operation, candidate):
        marker = operation.get('scene_space_diagnostic')
        if marker is None:
            return False
        require(operation.get('scene_space_render_phase') == 'preview'
            and candidate.get('picture_sha256') is not None,
            'scene_space_learning_diagnostic_invalid', 409)
        report = dict(marker, status='rendered', picture_sha256=candidate['picture_sha256'],
            render_operation_id=operation['id'])
        candidate['scene_space_diagnostic'] = deepcopy(report)
        candidate['verification_scope'] = 'static_diagnostic_only_not_reviewed'
        candidate['artifacts']['scene-space-diagnostic'] = dict(
            id=candidate['id'] + '-scene-space-diagnostic',
            sha256=self.store.blob(canonical_bytes(report)), mime_type='application/json')
        run['scene_space_diagnostic'] = deepcopy(report)
        self._finish(run, 'scene_space_diagnostic_preview_only')
        return True

    def _scene_prepare_render(self, run, operation, candidate, options, deadline_at, *, started):
        """Pin a pure render request before the durable execution claim."""
        if not options:
            return options, deadline_at
        current = self.renderer.capabilities().get('scene_space')
        expected = run['frozen']['scene_space_learning']['runtime']
        require(current == expected, 'scene_space_learning_frozen_runtime_changed', 409)
        prepare = getattr(self.renderer, 'validate_scene_request', None)
        require(callable(prepare), 'scene_space_learning_prepare_unavailable', 503)
        previous = operation.get('scene_space_preflight')
        if previous is not None:
            deadline_at = previous['deadline_at']
        require(deadline_at - self.clock() >= 30, 'scene_space_learning_deadline_insufficient', 409)
        binding = prepare(operation['id'], candidate['source'], operation['input'],
            scene_space=options['scene_space'], deadline_at=deadline_at)
        request_sha = binding.get('request_sha256') if type(binding) is dict else None
        require(type(request_sha) is str and len(request_sha) == 64
            and all(char in '0123456789abcdef' for char in request_sha),
            'scene_space_learning_prepare_binding_invalid', 409)
        pinned = dict(schema='video.scene-space-learning-preflight.v1', request_sha256=request_sha,
            plan_sha256=digest(options['scene_space']), source_sha256=candidate['source_sha256'],
            deadline_at=deadline_at, capability_sha256=digest(expected), scope='pure_preclaim_validation')
        if previous is not None:
            require({key:value for key,value in previous.items() if key!='measured_prepare_seconds'} == pinned,
                'scene_space_learning_prepared_request_changed', 409)
        require(deadline_at - self.clock() >= 30, 'scene_space_learning_deadline_insufficient', 409)
        pinned['measured_prepare_seconds'] = (previous or {}).get('measured_prepare_seconds', 0) + max(0, time.monotonic() - started)
        operation['scene_space_preflight'] = pinned
        self.store.put('operations', operation)
        return dict(options, expected_request_sha256=request_sha), deadline_at

    def _scene_reject_preflight(self, run, operation, candidate, error, *, elapsed):
        """A pure rejection proves no dispatch; settle cost and finish it."""
        result = dict(status='failed', technical_passed=False, stop_proven=True,
            not_dispatched=True, error=error.code, rejection_stage='scene_space_preflight')
        self._scene_finish_render(run, operation, candidate, result, elapsed=elapsed)
        operation.update(status='failed', error=error.code, result=result)
        self._finish_operation(run, operation)
        run['error'] = dict(code=error.code, stage='scene_space_preflight', not_dispatched=True)
        self._finish(run, 'render_preflight_rejected', 'failed')

    def _scene_finish_render(self, run, operation, candidate, result, *, elapsed=None):
        task_id = candidate.get('scene_space_task_id')
        if not result or result.get('stop_proven') is not True:
            return
        seconds = elapsed if elapsed is not None else result.get('measured_render_seconds')
        if elapsed is None and seconds is not None:
            seconds += operation.get('scene_space_preflight', {}).get('measured_prepare_seconds', 0)
        outcome = 'completed' if result.get('status') == 'rendered' else 'failed'
        phase = operation.get('scene_space_render_phase', 'render')
        require(phase in {'render', 'preview'}, 'scene_space_learning_settlement_changed', 409)
        budget = run.get('scene_space_render_budget') or {}
        previous = next((event for event in budget.get('events', []) if event['id'] == operation['id']), None)
        store = self._scene_store() if task_id is not None else None
        prior = (next((event for event in store.get(run['owner'], task_id)['usage']['events']
                       if event['id'] == operation['id']), None) if store is not None else None)
        if prior is not None:
            # A response/artifact failure after settlement can re-enter lookup.
            # Retain the first actual measurement rather than measuring again.
            require(prior['phase'] == phase and prior['outcome'] == outcome,
                'scene_space_learning_settlement_changed', 409)
            # Settlement can commit in the shared ledger before the learning
            # run is saved. Reuse that first measurement after a process crash.
            seconds = prior['render_seconds']
        if previous is not None:
            require(prior is None or previous['seconds'] == seconds,
                'scene_space_learning_settlement_changed', 409)
            seconds = previous['seconds']
        if store is not None and prior is None:
            store.record_usage(run['owner'], task_id, id=operation['id'], phase=phase,
                outcome=outcome, model_calls=0, model_tokens=0, render_seconds=seconds)
        self._scene_run_charge(run, operation['id'], outcome=outcome, seconds=seconds, phase=phase)
        if store is None:
            return
        candidate['scene_space_usage'] = dict(task_id=task_id,
            status=store.get(run['owner'], task_id)['usage_status'],
            scope='scene_layout_and_render_usage_only',
            model_usage_scope='Model calls retain their existing experiment operation receipts; this ledger does not invent token measurements.')
