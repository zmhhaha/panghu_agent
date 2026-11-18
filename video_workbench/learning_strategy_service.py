"""Small service hooks for adaptive reference-learning studies and authors.

The ordinary learning service remains responsible for paid submission, original
ID reconciliation, rendering, artifact hashes, independent review and stopping.
These hooks only decide which frozen task the next ordinary operation serves.
"""
from __future__ import annotations

from copy import deepcopy
import uuid

from agent_service.contracts import canonical_bytes, digest
from . import learning_strategy as strategy


class LearningStrategyMixin:
    """Compose with PictureLearningService and its video specialization.

    Required host hooks:
    * return ``_learning_document(run, document)`` from the document builder;
    * prefer ``_learning_candidate(run)`` in its active candidate lookup;
    * call ``_learning_consume_agent_result`` after stopped/validated transport
      and before the normal author/reviewer result consumer;
    * call ``_learning_resources_ready`` after an optional resource gate;
    * retain these state fields when explicitly restoring a technical retry.

    Existing render/review stages and their cancellation handling are reused.
    No per-run memory, thread-local switches or hidden transport is introduced.
    """

    def _learning_operation(self, run, binding):
        operation = self.store.read('operations', run['owner'], binding['id'])
        return strategy.verify_operation(operation, binding)

    def _learning_state(self, run):
        state = run.get('learning_strategy')
        if state is None:
            return None
        plan_operation = self._learning_operation(run, state['plan_operation'])
        plan = state['plan']
        strategy.require(digest(plan) == state['plan_sha256'], 'plan_changed', 409)
        source = plan_operation['result']['source']
        strategy.require(source['engine'] == plan['engine'], 'engine_changed', 409)
        for row in state['components']:
            self._learning_operation(run, row['operation'])
        return state

    def _learning_candidate(self, run):
        """An internal study is a real artifact, never a library work card."""
        state = self._learning_state(run)
        identity = state.get('active_probe_id') if state else None
        if identity is None:
            return None
        matches = [row for row in run.get('internal_attempts', []) if row['id'] == identity]
        strategy.require(len(matches) == 1, 'probe_candidate_missing', 409)
        return matches[0]

    def _learning_document(self, run, document):
        config = run['frozen'].get('learning_strategy')
        if config is None or run.get('learning_strategy_complete'):
            return document
        strategy.validate_options(config)
        state = self._learning_state(run)
        if state is None:
            # No mandatory planning pass: the first ordinary creator already
            # emits a complete source and may simply choose no extra work.
            if (document['mode'] == 'create' and run['work_kind'] == 'reproduction'
                    and run['round'] == 1):
                document = deepcopy(document)
                document['constraints']['learning_strategy_task'] = strategy.task_guidance(config)
            return document
        if state.get('complete'):
            return document
        phase = state['phase']
        if phase == 'resources':
            # The resource producer consumes its own frozen input.  It does
            # not receive a phantom browser creator or review operation.
            return document
        if phase == 'probe_review' and run['stage'] == 'render':
            phase = 'probe_render'
        document = deepcopy(document)
        details = dict(schema=strategy.TASK_SCHEMA, phase=phase,
            plan_operation=deepcopy(state['plan_operation']),
            plan_sha256=state['plan_sha256'], whole_goal=run['frozen']['goal'],
            instructions=[
                'This is one actual subtask inside the same experiment, not a new project or budget.',
                'Keep the ordinary result format and frozen knowledge/feedback boundaries. Do not submit or claim another creator, render or review yourself.',
                'Do not propose another learning_strategy or resource_plan. The host owns this bounded plan.',
                'A source contribution is not evidence of visual quality. Only the supplied actual rendered images support visual findings.',
            ])
        document['constraints']['learning_strategy_task'] = details
        if phase in {'probe_author', 'probe_render', 'probe_review'}:
            probe = state['plan']['probes'][state['probe_index']]
            details.update(task_id=probe['id'], whole_criteria=deepcopy(state['plan']['criteria']),
                purpose='internal_technique_study_not_complete_reproduction')
            document['goal'], document['criteria'] = probe['goal'], deepcopy(probe['criteria'])
            if phase == 'probe_author':
                document['mode'], document['base_source'], document['prior_review'] = 'create', None, None
                details['instructions'].append(
                    'Create only this focused technical study using the original canvas dimensions and clip clock. It will be rendered and independently inspected before complete reproduction.')
            elif phase == 'probe_review':
                candidate = self._learning_candidate(run)
                strategy.require(candidate is not None and self._has_picture(candidate),
                    'probe_not_rendered', 409)
                document['mode'], document['base_source'] = 'review', deepcopy(candidate['source'])
                details['instructions'].append(
                    'Review the supplied actual study output only against this local technique goal. Do not score it as a complete scene or claim unobserved motion/audio. transfer_task must be null.')
            else:
                document['mode'] = 'create'
                document['base_source'] = deepcopy(self._learning_candidate(run)['source'])
        elif phase == 'component':
            part = next(row for row in state['plan']['components'] if row['id'] == state['component_id'])
            details.update(task_id=part['id'], interface=part['interface'],
                dependencies=self._learning_dependencies(run, part['depends_on']),
                whole_criteria=deepcopy(state['plan']['criteria']),
                purpose='specialist_source_contribution')
            document['mode'], document['goal'], document['criteria'] = 'create', part['goal'], deepcopy(part['criteria'])
            document['base_source'], document['prior_review'] = None, None
            document['constraints']['allowed_engines'] = [state['plan']['engine']]
            details['instructions'].append(
                'Author a complete valid editable source contribution for your responsibility. Match the stated interface, common coordinates, style and absolute clock. Your source is integrated later; do not claim the full result was rendered.')
            self._learning_probe_inputs(run, document, details)
        elif phase == 'compose':
            details.update(purpose='complete_reproduction_after_actual_subtasks',
                dependencies=self._learning_dependencies(run,
                    [row['id'] for row in state['plan']['components']]))
            document['mode'] = 'create'
            document['criteria'] = deepcopy(run['frozen']['criteria'] or state['plan']['criteria'])
            if state['plan']['components']:
                document['constraints']['allowed_engines'] = [state['plan']['engine']]
            details['instructions'].append(
                'Create one complete editable source that integrates the actual contributions and useful probe findings. Reconcile style, layout, occlusion and timing yourself. It will be rendered and independently compared as a whole.')
            self._learning_probe_inputs(run, document, details)
        else:
            strategy.require(False, 'phase_invalid', 409)
        if document['mode'] == 'create':
            details['instructions'].extend(strategy.source_package_guidance())
        return document

    def _learning_dependencies(self, run, identities):
        state = self._learning_state(run)
        rows = {row['id']: row for row in state['components']}
        strategy.require(set(identities) <= set(rows), 'dependency_not_ready', 409)
        result = []
        for identity in identities:
            row = rows[identity]
            operation = self._learning_operation(run, row['operation'])
            result.append(dict(id=identity, operation=deepcopy(row['operation']),
                source=deepcopy(operation['result']['source']),
                method=deepcopy(operation['result'].get('method')),
                observation_scope='source_only_not_separately_rendered'))
        return result

    def _learning_probe_inputs(self, run, document, details):
        state = self._learning_state(run)
        completed = [row for row in state['probes'] if row['status'] == 'reviewed']
        if not completed:
            return
        frames = document['observation']['frames']
        room = self.protocol.MAX_FRAMES - len(frames)
        strategy.require(room >= len(completed), 'probe_frame_budget_exceeded', 413)
        details['probes'] = []
        for index, row in enumerate(completed):
            candidate = next(work for work in run['internal_attempts'] if work['id'] == row['candidate_id'])
            strategy.require(self._has_picture(candidate), 'probe_not_rendered', 409)
            record = dict(id=row['id'], goal=row['goal'], source=deepcopy(candidate['source']),
                review=deepcopy(row['review']), candidate_id=candidate['id'],
                author_operation=deepcopy(row['author_operation']),
                review_operation=deepcopy(row['review_operation']), evidence_frame_ids=[])
            if candidate.get('observation'):
                samples = candidate['observation']['frames']
                # Deliberately bounded temporal evidence; retained coverage
                # records identify the wider saved sample sequence separately.
                count = min(3, len(samples), room - (len(completed) - index - 1))
                positions = ([0] if count == 1 else
                    [round(i * (len(samples) - 1) / (count - 1)) for i in range(count)])
                selected = [deepcopy(samples[i]) for i in positions]
                record['saved_observation'] = deepcopy(candidate['observation'].get('coverage'))
                suffix = '.jpg'
            else:
                selected = [dict(sha256=candidate['observed_sha256'])]
                suffix = candidate['observed_suffix']
            for sample_index, frame in enumerate(selected):
                identity = f'probe-{index + 1}-{sample_index + 1}'
                frame.update(frame_id=identity, file_name=identity + suffix, purpose='baseline')
                frames.append(frame)
                record['evidence_frame_ids'].append(identity)
            room -= len(selected)
            details['probes'].append(record)

    def _learning_consume_agent_result(self, run, operation, result):
        """Return true only when the ordinary result consumer must defer."""
        task = operation['input'].get('constraints', {}).get('learning_strategy_task')
        if not task or task.get('schema') != strategy.TASK_SCHEMA:
            return False
        phase = task['phase']
        method = result.get('method') or {}
        if phase == 'plan':
            config = strategy.validate_options(run['frozen']['learning_strategy'])
            proposed = method.get('learning_strategy')
            resources = method.get('resource_plan')
            if proposed is None and resources is None:
                run['learning_strategy_complete'] = True
                return False
            if proposed is None:
                proposed = dict(rationale='The creator requested a resource before full composition.',
                    criteria=deepcopy(operation['input']['criteria']) or
                        ['Follow the frozen reproduction goal and constraints.'], probes=[], components=[])
            plan = strategy.validate_plan(proposed, config=config,
                engine=result['source']['engine'],
                allowed_engines=operation['input']['constraints']['allowed_engines'],
                resource_plan=resources)
            state = dict(schema=strategy.SCHEMA, phase='plan', plan=plan,
                plan_sha256=digest(plan), plan_operation=strategy.operation_binding(operation),
                probes=[dict(id=row['id'], goal=row['goal'], status='planned') for row in plan['probes']],
                components=[], probe_index=0, active_probe_id=None, complete=False)
            run['learning_strategy'] = state
            if resources is not None:
                state['phase'] = 'resources'
                run['stage'] = 'learning_resources'
                return True
            if not plan['probes'] and not plan['components']:
                state['complete'] = run['learning_strategy_complete'] = True
                state['phase'] = 'complete'
                return False
            self._learning_schedule(run)
            return True
        state = self._learning_state(run)
        strategy.require(state is not None and phase == state['phase'], 'phase_changed', 409)
        expected_task = (state['plan']['probes'][state['probe_index']]['id']
            if phase in {'probe_author', 'probe_review'} else
            state.get('component_id') if phase == 'component' else None)
        strategy.require(task.get('task_id') == expected_task, 'task_changed', 409)
        strategy.require(not method.get('resource_plan'), 'nested_resource_plan_forbidden', 409)
        nested = method.get('learning_strategy')
        strategy.require(not nested or not nested.get('probes') and not nested.get('components'),
            'nested_plan_forbidden', 409)
        if phase == 'probe_author':
            probe = state['plan']['probes'][state['probe_index']]
            candidate_id = uuid.uuid4().hex
            source = result['source']
            candidate = dict(id=candidate_id, work_kind=run['work_kind'], role='internal_probe',
                round=run['round'], source=deepcopy(source), source_sha256=source['source_sha256'],
                method=deepcopy(result.get('method')), sound_events=deepcopy(result.get('sound_events', [])),
                review=None, status='authored', created_at=self.clock(),
                author_operation_id=operation['id'], probe_id=probe['id'])
            candidate['artifacts'] = {'source': dict(id=candidate_id + '-source',
                sha256=self.store.blob(canonical_bytes(source)), mime_type='application/json')}
            run.setdefault('internal_attempts', []).append(candidate)
            state['active_probe_id'] = candidate_id
            state['probes'][state['probe_index']].update(status='authored', candidate_id=candidate_id,
                author_operation=strategy.operation_binding(operation))
            state['phase'], run['stage'] = 'probe_review', 'render'
            return True
        if phase == 'probe_review':
            candidate = self._learning_candidate(run)
            strategy.require(candidate is not None and self._has_picture(candidate),
                'probe_not_rendered', 409)
            candidate.update(review=deepcopy(result['review']), status='reviewed',
                review_operation_id=operation['id'])
            state['probes'][state['probe_index']].update(status='reviewed',
                review=deepcopy(result['review']), review_operation=strategy.operation_binding(operation))
            state['probe_index'] += 1
            state['active_probe_id'] = None
            self._learning_schedule(run)
            return True
        if phase == 'component':
            strategy.require(result['source']['engine'] == state['plan']['engine'],
                'component_engine_changed', 409)
            state['components'].append(dict(id=state['component_id'],
                operation=strategy.operation_binding(operation)))
            self._learning_schedule(run)
            return True
        if phase == 'compose':
            state['complete'] = run['learning_strategy_complete'] = True
            state['phase'] = 'complete'
            state['composition_operation'] = strategy.operation_binding(operation)
            return False
        strategy.require(False, 'phase_invalid', 409)

    def _learning_schedule(self, run):
        state = self._learning_state(run)
        strategy.require(state is not None, 'plan_missing', 409)
        if self.clock() >= run['deadline_at']:
            self._finish(run, 'budget_exhausted')
            return
        if state['probe_index'] < len(state['plan']['probes']):
            state['phase'], run['stage'] = 'probe_author', 'author'
            return
        if state['plan']['components']:
            plan_operation = self._learning_operation(run, state['plan_operation'])
            components = [(row['id'], self._learning_operation(run, row['operation']))
                for row in state['components']]
            action = strategy.collaborative_action(state['plan'], plan_operation, components,
                experiment_id=run['id'], owner=run['owner'],
                deadline_at=run['deadline_at'], now=self.clock())
            strategy.require(action['kind'] == 'reserve', 'collaboration_stalled', 409)
            next_task = action['operations'][0]
            if next_task['phase'] == 'component':
                state['phase'], state['component_id'], run['stage'] = 'component', next_task['component_id'], 'author'
                return
        state['phase'], run['stage'] = 'compose', 'author'

    def _learning_resources_ready(self, run):
        state = self._learning_state(run)
        strategy.require(state is not None and state['phase'] == 'resources',
            'resource_phase_invalid', 409)
        self._learning_schedule(run)

    def _learning_restore_retry(self, prior, run):
        """Preserve paid plan/probe/source identities across technical recovery."""
        for key in ('learning_strategy', 'learning_strategy_complete', 'internal_attempts'):
            if key in prior:
                run[key] = deepcopy(prior[key])
        if prior['work_kind'] == 'transfer':
            self._learning_restore_completed_transfer(prior, run)
            return
        state = run.get('learning_strategy') or {}
        # Only the first complete composition can be awaiting source acceptance.
        # A later failed revision must not silently fall back to this old source.
        if (prior['stage'] != 'author' or prior['work_kind'] != 'reproduction'
                or prior['round'] != 1 or prior['candidates']
                or state.get('phase') != 'complete' or state.get('complete') is not True
                or run.get('learning_strategy_complete') is not True):
            return
        binding = state.get('composition_operation')
        if binding is None:
            return
        previous_intent = prior.get('completed_author_retry')
        if previous_intent is not None:
            strategy.require(previous_intent.get('consumed') is False
                and previous_intent.get('operation') == binding
                and not prior['operation_ids'], 'retry_checkpoint_changed', 409)
        else:
            strategy.require(bool(prior['operation_ids'])
                and prior['operation_ids'][-1] == binding['id'], 'retry_checkpoint_changed', 409)
        operation = self._learning_operation(prior, binding)
        self._learning_check_completed_author(run, operation)
        receipt = operation.get('observation_receipt')
        strategy.require(type(receipt) is dict, 'retry_receipt_missing', 409)
        intent = dict(operation=deepcopy(binding), experiment_id=operation['experiment_id'],
            job_id=operation['job_id'], receipt_sha256=digest(receipt), consumed=False)
        if previous_intent is not None:
            strategy.require(previous_intent == intent, 'retry_checkpoint_changed', 409)
        run['completed_author_retry'] = intent

    def _learning_transfer_lineage(self, run):
        """Bind the reviewed reproduction that supplied this transfer method."""
        candidates = self._context_candidates(run)
        best = next((row for row in candidates if row['id'] == run['best_candidate_id']), None)
        strategy.require(best is not None and best['work_kind'] == 'reproduction'
            and best.get('status') == 'reviewed' and best.get('review') is not None
            and self._has_picture(best) and best.get('method') == run.get('transfer_method'),
            'retry_transfer_lineage_changed', 409)
        author = self.store.read('operations', run['owner'], best['author_operation_id'])
        review = self.store.read('operations', run['owner'], best['review_operation_id'])
        render = self.store.read('operations', run['owner'], best['render_operation_id'])
        for operation in (author, review, render):
            origin = self.store.read('experiments', run['owner'], operation['experiment_id'])
            strategy.require(operation.get('owner') == run['owner']
                and operation['id'] in origin['operation_ids']
                and operation.get('status') == 'completed'
                and origin['project_id'] == run['project_id']
                and digest(operation['input']) == operation['input_sha256'],
                'retry_transfer_lineage_changed', 409)
        strategy.require(author['stage'] == 'author'
            and author['result'].get('source') == best['source']
            and review['stage'] == 'review' and review['input']['work_kind'] == 'reproduction'
            and review['result'].get('review') == best['review']
            and render['stage'] == 'render'
            and render['result'].get('sha256') == (best.get('media_sha256') or best.get('picture_sha256')),
            'retry_transfer_lineage_changed', 409)
        return dict(candidate_id=best['id'], candidate_sha256=digest(best),
            author_operation=strategy.operation_binding(author),
            review_operation=strategy.operation_binding(review),
            render_operation_sha256=digest(render),
            review_receipt_sha256=digest(review.get('observation_receipt')))

    def _learning_restore_completed_transfer(self, prior, run):
        """Recover only the latest paid transfer author, never an old compose."""
        if (prior['stage'] != 'author' or prior['round'] != 1
                or prior.get('active_operation_id') is not None
                or any(row['work_kind'] != 'reproduction' for row in prior['candidates'])
                or prior.get('resource_plan')):
            return
        state = run.get('learning_strategy')
        if state is not None and (state.get('phase') != 'complete'
                or state.get('complete') is not True or run.get('learning_strategy_complete') is not True):
            return
        previous = prior.get('completed_author_retry')
        pending = previous is not None and previous.get('consumed') is False
        if pending:
            strategy.require(previous.get('work_kind') == 'transfer' and not prior['operation_ids'],
                'retry_checkpoint_changed', 409)
            binding = previous['operation']
            operation = self._learning_operation(prior, binding)
        else:
            if not prior['operation_ids']:
                return
            operation = self.store.read('operations', prior['owner'], prior['operation_ids'][-1])
            if operation['stage'] != 'author' or operation.get('status') != 'completed':
                return
            binding = strategy.operation_binding(operation)
        self._learning_check_completed_author(run, operation)
        receipt = operation.get('observation_receipt')
        strategy.require(type(receipt) is dict, 'retry_receipt_missing', 409)
        intent = dict(work_kind='transfer', operation=deepcopy(binding),
            experiment_id=operation['experiment_id'], job_id=operation['job_id'],
            receipt_sha256=digest(receipt), reproduction=self._learning_transfer_lineage(run), consumed=False)
        if pending:
            strategy.require(previous == intent, 'retry_checkpoint_changed', 409)
        run['completed_author_retry'] = intent

    def _learning_check_completed_author(self, run, operation):
        """Keep the original paid input/result bound to the same frozen study."""
        document = operation['input']
        task = document.get('constraints', {}).get('learning_strategy_task') or {}
        origin = self.store.read('experiments', run['owner'], operation['experiment_id'])
        strategy.require(operation.get('owner') == run['owner']
            and operation['id'] in origin['operation_ids']
            and operation['stage'] == 'author' and operation.get('job_id')
            and document.get('experiment_id') == origin['id']
            and document.get('project_id') == run['project_id'] == origin['project_id']
            and document.get('work_kind') == run['work_kind'] == origin['work_kind']
            and origin['round'] == run['round']
            and document.get('mode') == 'create',
            'retry_checkpoint_changed', 409)
        strategy.require(origin['frozen_sha256'] == run['frozen_sha256']
            == digest(origin['frozen']) == digest(run['frozen'])
            and origin['frozen'] == run['frozen']
            and origin.get('learning_strategy') == run.get('learning_strategy')
            and document.get('knowledge_binding') == run['frozen']['library_binding']
            and document.get('knowledge_snapshot') == run['frozen']['knowledge_snapshot'],
            'retry_frozen_input_changed', 409)
        if run['work_kind'] == 'transfer':
            strategy.require(not task and origin.get('stop_proven') is True
                and origin['state'] in {'failed', 'cancelled'}
                and origin['stage'] == 'author' and origin['round'] == 1
                and origin['active_operation_id'] is None
                and origin['operation_ids'][-1] == operation['id']
                and origin.get('transfer_status') == run.get('transfer_status') == 'running'
                and origin.get('transfer_task') == run.get('transfer_task')
                and origin.get('transfer_method') == run.get('transfer_method')
                and origin.get('best_candidate_id') == run.get('best_candidate_id'),
                'retry_transfer_input_changed', 409)
            expected = self._document(run, 'create')
            expected.update(experiment_id=origin['id'], frozen_at=origin['created_at'])
            strategy.require(document == self._document(origin, 'create') == expected
                and self._learning_transfer_lineage(origin) == self._learning_transfer_lineage(run),
                'retry_transfer_input_changed', 409)
        else:
            strategy.require(task.get('schema') == strategy.TASK_SCHEMA and task.get('phase') == 'compose'
                and origin.get('learning_strategy_complete') is True
                and document.get('goal') == run['frozen']['goal'], 'retry_checkpoint_changed', 409)

    def _learning_resume_completed_author(self, run):
        """Revalidate a stopped complete author without another creator POST.

        Candidate, consumed intent and the next stage are saved together by the
        host. A restart before that save repeats only reads and validation.
        """
        intent = run.get('completed_author_retry')
        if intent is None or intent.get('consumed'):
            return False
        state = self._learning_state(run)
        transfer = intent.get('work_kind') == 'transfer'
        strategy.require(run['stage'] == 'author' and not run['candidates']
            and not run['operation_ids'] and run['active_operation_id'] is None,
            'retry_checkpoint_changed', 409)
        strategy.require((state is not None and state.get('phase') == 'complete'
                and state.get('complete') is True and run.get('learning_strategy_complete') is True)
                or transfer and state is None, 'retry_checkpoint_changed', 409)
        strategy.require((transfer and run['work_kind'] == 'transfer'
                and self._learning_transfer_lineage(run) == intent.get('reproduction'))
            or (not transfer and run['work_kind'] == 'reproduction'
                and state.get('composition_operation') == intent.get('operation')),
            'retry_checkpoint_changed', 409)
        for probe in (state or {}).get('probes', []):
            if probe['status'] == 'reviewed':
                self._learning_operation(run, probe['author_operation'])
                self._learning_operation(run, probe['review_operation'])
        operation = self._learning_operation(run, intent['operation'])
        self._learning_check_completed_author(run, operation)
        strategy.require(operation['experiment_id'] == intent['experiment_id']
            and operation['job_id'] == intent['job_id']
            and digest(operation.get('observation_receipt')) == intent['receipt_sha256'],
            'retry_checkpoint_changed', 409)
        job = self.author.poll(intent['job_id'])
        strategy.require(job.get('job_id') == intent['job_id'], 'retry_job_changed', 409)
        if job.get('state') != 'completed' or job.get('stop_proven') is not True:
            run['state'] = 'submission_unknown'
            return True
        document, result = operation['input'], operation['result']
        self.protocol.validate_input(document)
        self.protocol.validate_result(result, operation['input_sha256'], document)
        expected = {frame['sha256'] for frame in document['observation']['frames']}
        observed = {row.get('sha256') for row in operation['observation_receipt'].get('images', [])
            if row.get('model_request_completed') is True}
        strategy.require(expected <= observed, 'retry_frame_transport_incomplete', 409)
        candidate = self._accept_learning_author(run, operation, result)
        candidate['reused_author'] = dict(source_experiment_id=intent['experiment_id'],
            source_operation_id=operation['id'], job_id=intent['job_id'],
            input_sha256=operation['input_sha256'], result_sha256=intent['operation']['result_sha256'],
            observation_receipt_sha256=intent['receipt_sha256'], retry_experiment_id=run['id'])
        intent['consumed'] = True
        return True

    @staticmethod
    def _learning_progress(run):
        result = strategy.progress(run.get('learning_strategy'))
        if result is not None and result['phase'] == 'probe_review' and run.get('stage') == 'render':
            result['phase'] = 'probe_render'
        return result
