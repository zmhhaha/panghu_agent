"""One budgeted creator repair after a stopped, classified render failure.

This preserves the failed source and its frozen input. A diagnostic is a host
report, never a visual verdict; uncertain executions cannot authorize a repair.
"""
from copy import deepcopy

from agent_service.contracts import digest
from .learning_execution_guidance import learning_execution_guidance
from .picture_learning_store import require


class VideoLearningRepairMixin:
    def _schedule_render_repair(self, run, operation, result):
        diagnostic = result.get('diagnostic') or {}
        candidate = self._candidate(run)
        if (result.get('stop_proven') is not True
                or result.get('status') != 'failed'
                or diagnostic.get('source_repair_recommended') is not True
                or diagnostic.get('receipt_bound') is not True):
            return False
        require(diagnostic.get('operation_id') == operation['id']
            and diagnostic.get('source_sha256') == candidate['source_sha256']
            and diagnostic.get('input_sha256') == operation['input_sha256']
            and digest(operation['input']) == operation['input_sha256']
            and diagnostic.get('evidence_sha256') == result.get('evidence_sha256',
                result.get('technical', {}).get('evidence_sha256')),
            'video_learning_repair_binding_changed', 409)
        candidate.update(status='render_failed', render_operation_id=operation['id'],
            render_failure=deepcopy(diagnostic))
        persisted = self.store.read('experiments', run['owner'], operation['experiment_id'])
        repairs = run.setdefault('render_repairs', [])
        if (run['stop_requested'] or persisted['stop_requested'] or len(repairs) >= 1
                or run['round'] >= run['budget']['max_rounds']
                or run['deadline_at'] - self.clock() < 90):
            return False
        repair = dict(failed_candidate_id=candidate['id'], failed_candidate_experiment_id=run['id'],
            operation_id=operation['id'],
            input_sha256=operation['input_sha256'], source_sha256=candidate['source_sha256'],
            diagnostic=deepcopy(diagnostic), status='pending', created_at=self.clock(),
            remaining_seconds=max(0, run['deadline_at'] - self.clock()))
        repairs.append(repair)
        run['render_repair_pending'] = len(repairs) - 1
        state = run.get('learning_strategy')
        if state and state.get('active_probe_id') == candidate['id']:
            state.update(phase='probe_author', active_probe_id=None)
        run.update(stage='author', round=run['round'] + 1, error=None)
        return True

    def _restore_render_repair(self, prior, run):
        for key in ('render_repairs', 'render_repair_pending'):
            if key in prior:
                run[key] = deepcopy(prior[key])
        if prior['stage'] != 'render' or prior.get('stop_reason') != 'render_failed':
            return
        operations = [self.store.read('operations', run['owner'], identity)
            for identity in reversed(prior['operation_ids'])]
        operation = next((row for row in operations
            if row['stage'] == 'render' and row['status'] == 'failed'), None)
        if operation is None:
            return
        # Re-read the sealed old outcome for new safe diagnostics. This does
        # not run a renderer, alter the old result, or submit another task.
        result = self.renderer.lookup(operation['id'])
        if result and self._schedule_render_repair(run, operation, result):
            context = run.get('technical_retry_context')
            if context:
                context['consumed'] = True

    def _render_repair_document(self, run, document):
        index = run.get('render_repair_pending')
        if index is None or run['stage'] != 'author' or document['mode'] != 'create':
            return document
        repair = run['render_repairs'][index]
        operation = self.store.read('operations', run['owner'], repair['operation_id'])
        require(operation['input_sha256'] == repair['input_sha256']
            == digest(operation['input']), 'video_learning_repair_binding_changed', 409)
        candidates = self._context_candidates(run) + run.get('internal_attempts', [])
        candidate = next((row for row in candidates
            if row['id'] == repair['failed_candidate_id']), None)
        if candidate is None:
            origin = self.store.read('experiments', run['owner'],
                repair['failed_candidate_experiment_id'])
            require(origin['project_id'] == run['project_id'],
                'video_learning_repair_source_changed', 409)
            candidate = next((row for row in origin['candidates'] + origin.get('internal_attempts', [])
                if row['id'] == repair['failed_candidate_id']), None)
        require(candidate is not None and candidate['source_sha256'] == repair['source_sha256'],
            'video_learning_repair_source_changed', 409)
        # Retain the exact effective task, reference images, review, tool
        # restrictions and WikiSkill binding that produced this failed work.
        execution_guidance = deepcopy(document['constraints'].get('execution_guidance',
            learning_execution_guidance()))
        document = deepcopy(operation['input'])
        document.update(mode='create', experiment_id=run['id'], frozen_at=run['created_at'])
        document.pop('sound_events', None)
        document['base_source'] = (None if run['work_kind'] == 'transfer'
            else deepcopy(candidate['source']))
        document['constraints']['render_repair'] = dict(schema='video.learning-render-repair.v1',
            failed_candidate_id=candidate['id'], failed_source=deepcopy(candidate['source']),
            diagnostic=deepcopy(repair['diagnostic']), remaining_seconds=repair['remaining_seconds'],
            instructions=[
                'Repair this failed implementation while preserving the actual visual goal and reference, not the failed technology choice.',
                'The host report establishes a technical failure only. No output frames, aesthetic improvement, motion or sound acceptance are established by it.',
                'Use the failure evidence to fix or redesign your complete source. When comparable visual quality can be achieved more cheaply, prefer the simpler, stable implementation.',
                'Custom shaders are optional. You may change implementation only within the supplied allowed engines, selected capabilities and prepared resource constraints. Never silently replace an explicitly required capability or key visual effect.',
                'This repair consumes one existing exploration round and the remaining deadline. Do not propose another learning_strategy or resource_plan, request a new producer, launch tasks, or claim a render yourself. Reuse only prepared resources already supplied in this input.',
            ])
        if execution_guidance is not None:
            document['constraints']['execution_guidance'] = execution_guidance
        task = document['constraints'].get('learning_strategy_task')
        if task and task.get('phase') == 'probe_render':
            task['phase'] = 'probe_author'
        return document

    def _consume_render_repair_author(self, run, operation):
        index = run.get('render_repair_pending')
        if (index is None or operation['stage'] != 'author'
                or operation['status'] != 'completed'
                or 'render_repair' not in operation['input']['constraints']):
            return
        candidate = next((row for row in run['candidates'] + run.get('internal_attempts', [])
            if row.get('author_operation_id') == operation['id']), None)
        if candidate is not None:
            repair = run['render_repairs'][index]
            candidate['repair_of_candidate_id'] = repair['failed_candidate_id']
            repair.update(status='authored', author_operation_id=operation['id'],
                candidate_id=candidate['id'])
            run.pop('render_repair_pending', None)
