"""Laboratory adapter over the same durable resource plans used by films.

The existing source-only recipe/Cairo coordinator remains the Manim producer.
Its task is an immutable child of the shared plan; reading progress never
creates a plan, submits work, or manufactures developer acceptance.
"""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import time

from agent_service.contracts import ServiceError, digest
from .manim_method_coordinator import authoring_request
from .manim_resource import require
from .resource_placement import freeze_manim_placement
from .resource_plan_store import ResourcePlanStore
from .resource_plan_workflow import ResourcePlanWorkflow


class LabManimProducer:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def capabilities(self):
        return dict(admitted=bool(self.coordinator.enabled or self.coordinator.candidate),
            kind='animation', revision='manim-lab-local-v1', capabilities=['manim'], key_effects=[])

    @staticmethod
    def _run(task):
        run = dict(id=task['task_id'], frozen=deepcopy(task['inputs']), deadline_at=task['deadline_at'])
        if 'library_binding' in task['inputs']:
            run['library_binding'] = deepcopy(task['inputs']['library_binding'])
        return run

    def _advance(self, task, cancelled=False):
        state = self.coordinator.advance(task['owner'], self._run(task), cancelled=cancelled)
        terminal = state in {'completed', 'failed', 'cancelled'}
        receipt = dict(task_id=task['task_id'], request_sha256=task['request_sha256'],
            status=state if terminal else 'running', stop_proven=terminal)
        if state == 'completed':
            receipt['output'] = dict(content=dict(descriptor=self.coordinator.context(task['owner'], task['task_id'])),
                local_timing=deepcopy(task['local_timing']), capabilities=['manim'], key_effects=[])
        elif state == 'failed':
            receipt['error'] = 'lab_manim_producer_failed'
        return receipt

    def submit(self, task):
        return self._advance(task)

    def lookup(self, task):
        return self._advance(task)

    def cancel(self, task):
        return self._advance(task, cancelled=True)

    def validate_output(self, task, output):
        descriptor = output.get('content', {}).get('descriptor')
        expected = self.coordinator.context(task['owner'], task['task_id'])
        require(descriptor == expected, 'lab_resource_descriptor_changed', 409)
        self.coordinator.resolve(descriptor)
        require(descriptor['manifest']['global_start_frame'] == 0
            and descriptor['manifest']['frame_count'] == task['local_timing']['frame_count']
            and descriptor['manifest']['fps'] == task['local_timing']['fps'],
            'lab_resource_local_timing_changed', 409)
        return True


class LabResourceStage:
    def __init__(self, path, *, bindings_path, coordinator, clock=time.time):
        self.path, self.bindings_path = Path(path), Path(bindings_path)
        self.coordinator, self.clock = coordinator, clock
        self._opened_store = None

    @staticmethod
    def operation_id(commission_id, resource_round=None):
        """Keep historic identity, or name one explicitly budgeted recipe round."""
        if resource_round is None:
            return commission_id
        require(type(resource_round) is int and 0 <= resource_round < 8,
            'lab_resource_round_invalid')
        return 'manim-round-' + digest(dict(commission_id=commission_id, resource_round=resource_round))[:40]

    def _connection(self, *, create=False):
        require(self.bindings_path.is_file() and not self.bindings_path.is_symlink(),
            'lab_resource_bindings_missing', 409)
        db = sqlite3.connect(str(self.bindings_path), isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        if create:
            db.execute('CREATE TABLE IF NOT EXISTS lab_resource_bindings ('
                'owner TEXT NOT NULL,operation_id TEXT NOT NULL,request_sha256 TEXT NOT NULL,'
                'state TEXT NOT NULL,PRIMARY KEY(owner,operation_id))')
        return db

    def _binding(self, owner, operation_id):
        with closing(self._connection()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_resource_bindings'").fetchone():
                return None
            row = db.execute('SELECT * FROM lab_resource_bindings WHERE owner=? AND operation_id=?',
                (owner, operation_id)).fetchone()
            return dict(row) if row else None

    def _has_plan(self, owner, operation_id):
        """Read without creating a ledger, plan, attempt, or producer identity."""
        require(not self.path.is_symlink(), 'resource_plan_database_invalid')
        if not self.path.exists():
            require(not any(Path(str(self.path) + suffix).exists() for suffix in ('-wal', '-shm')),
                'lab_resource_ledger_missing', 409)
            return False
        with closing(sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require(tables <= {'resource_plans'}, 'resource_plan_database_not_isolated', 409)
            return ('resource_plans' in tables and db.execute(
                'SELECT 1 FROM resource_plans WHERE owner=? AND operation_id=?',
                (owner, operation_id)).fetchone() is not None)

    @staticmethod
    def _stopped_intent(binding):
        return {'cancelled_unstarted': 'cancelled', 'timed_out_unstarted': 'failed'}.get(binding['state'])

    def _stop_unstarted_intent(self, owner, operation_id, checksum, *, cancelled):
        # The binding becomes active before any resource workflow may claim a
        # paid child. An intent with no plan therefore has no producer task to
        # stop. Keep a tombstone so a later worker cannot recreate that intent.
        with closing(self._connection()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM lab_resource_bindings WHERE owner=? AND operation_id=?',
                (owner, operation_id)).fetchone()
            require(row is not None and row['request_sha256'] == checksum,
                'lab_resource_request_changed', 409)
            if row['state'] != 'intent' or self._has_plan(owner, operation_id):
                db.commit()
                return False
            db.execute('UPDATE lab_resource_bindings SET state=? WHERE owner=? AND operation_id=?',
                ('cancelled_unstarted' if cancelled else 'timed_out_unstarted', owner, operation_id))
            db.commit()
        return True

    def allocated_round(self, owner, commission_id):
        """Read the latest durable round intent, including pre-author work.

        A resource can be running before its consumer's create operation is
        reserved. Cancellation/recovery must not infer an older identity from
        the number of already-created browser compositions.
        """
        keys = {self.operation_id(commission_id, index): index for index in range(8)}
        with closing(self._connection()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_resource_bindings'").fetchone():
                return None
            rows = db.execute('SELECT operation_id FROM lab_resource_bindings WHERE owner=? AND operation_id IN ('
                + ','.join('?' for _ in keys) + ')', (owner, *keys)).fetchall()
        return max((keys[row['operation_id']] for row in rows), default=None)

    def _store(self):
        if self._opened_store is None:
            self._opened_store = ResourcePlanStore(self.path, clock=self.clock)
        return self._opened_store

    @staticmethod
    def request(run):
        frozen = run['frozen']
        options = authoring_request(frozen['resource_authoring'], frozen['duration_seconds'])
        placement = dict(global_start_frame=options['global_start_frame'])
        options['global_start_frame'] = 0
        inputs = dict(goal=frozen['goal'], feedback=frozen.get('feedback', ''),
            duration_seconds=frozen['duration_seconds'], resource_authoring=options)
        if 'manim_iteration' in frozen:
            inputs['manim_iteration'] = deepcopy(frozen['manim_iteration'])
        if 'capability_selection' in frozen:
            inputs['capability_selection'] = deepcopy(frozen['capability_selection'])
        for name in ('creation_mode', 'use_wikiskill', 'problem_summary'):
            if name in frozen:
                inputs[name] = deepcopy(frozen[name])
        if 'library_binding' in run:
            inputs['library_binding'] = deepcopy(run['library_binding'])
        return dict(nodes=[dict(id='manim', kind='animation', producer='manim', inputs=inputs,
            dependencies=[], objective=frozen['goal'], required_capabilities=['manim'], key_effects=[],
            local_timing=dict(fps=24, frame_count=options['frame_count']), placement=placement)],
            budget=dict(max_attempts=1, max_attempts_per_node=1), deadline_at=run['deadline_at'])

    def _ensure(self, owner, run):
        request = self.request(run)
        checksum = digest(request)
        operation_id = self.operation_id(run['id'], run.get('resource_round'))
        # This intent shares the original commission database. Once active,
        # losing the separate resource ledger must not create fresh paid keys.
        with closing(self._connection(create=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM lab_resource_bindings WHERE owner=? AND operation_id=?',
                (owner, operation_id)).fetchone()
            if row is None:
                db.execute('INSERT INTO lab_resource_bindings VALUES(?,?,?,?)',
                    (owner, operation_id, checksum, 'intent'))
                state = 'intent'
            else:
                require(row['request_sha256'] == checksum, 'lab_resource_request_changed', 409)
                state = row['state']
            require(state in {'intent', 'active'}, 'lab_resource_stopped', 409)
            db.commit()
        if state == 'active':
            require(self.path.is_file(), 'lab_resource_ledger_missing', 409)
            plan = self._store().get(owner, operation_id)
            require(plan['request_sha256'] == checksum, 'lab_resource_request_changed', 409)
            return plan
        plan = self._store().create(owner, run['id'], operation_id, request)
        with closing(self._connection()) as db:
            db.execute("UPDATE lab_resource_bindings SET state='active' WHERE owner=? AND operation_id=? AND request_sha256=? AND state='intent'",
                (owner, operation_id, checksum))
            row = db.execute('SELECT state FROM lab_resource_bindings WHERE owner=? AND operation_id=?',
                (owner, operation_id)).fetchone()
            require(row is not None and row['state'] == 'active', 'lab_resource_stopped', 409)
        return plan

    def restore_retry(self, owner, run, previous_run):
        """Restore one stopped resource round before ordinary retry execution.

        Completed PNGs are verified and adopted by reference, with no producer
        task. Failed resources keep their exact recipe checkpoint and only
        reserve the missing execution. Old attempt outcomes stay unchanged.
        The caller holds the library/run lock and validates the parent runs.
        """
        require(run['id'] != previous_run['id']
            and run['frozen'].get('retry_of_commission_id') == previous_run['id']
            and previous_run.get('state') in {'failed', 'cancelled'},
            'lab_resource_retry_relationship_invalid', 409)
        request, original = self.request(run), self.request(previous_run)
        require({key: value for key, value in request.items() if key != 'deadline_at'}
            == {key: value for key, value in original.items() if key != 'deadline_at'},
            'lab_resource_retry_input_changed', 409)
        source_id = self.operation_id(previous_run['id'], previous_run.get('resource_round'))
        binding = self._binding(owner, source_id)
        if binding is None or self._stopped_intent(binding) is not None:
            return None
        source = self._get(owner, source_id)
        require(source['stop_proven'] and source['status'] in {'completed', 'blocked', 'cancelled', 'timed_out'},
            'lab_resource_retry_source_not_stopped', 409)
        require(source['request_sha256'] == digest(original)
            and set(source['nodes']) == {'manim'}, 'lab_resource_retry_input_changed', 409)
        target = self._ensure(owner, run)
        target = self._store().freeze_retry_source(owner, target['operation_id'], source_id)
        return self._restore_checkpoint(owner, target)

    def _restore_checkpoint(self, owner, plan):
        """Resume a durable restore intent after any process interruption."""
        source_id = plan['retry_source']['operation_id']
        store = self._store()
        plan = store.freeze_retry_source(owner, plan['operation_id'], source_id)
        source = store.get(owner, source_id)
        node = source['nodes']['manim']
        require(source['stop_proven'] and node['stop_proven'], 'lab_resource_retry_source_not_stopped', 409)
        target_node = plan['nodes']['manim']
        require(target_node['definition'] == node['definition'], 'lab_resource_retry_input_changed', 409)
        if node['status'] == 'completed':
            output = node['output']
            require(type(output) is dict and type(output.get('content')) is dict,
                'lab_resource_retry_output_missing', 409)
            descriptor = output['content'].get('descriptor')
            self.coordinator().resolve(descriptor)
            return store.adopt(owner, plan['operation_id'], 'manim',
                {key: deepcopy(output[key]) for key in ('content', 'local_timing', 'capabilities', 'key_effects')},
                'restore-retry-checkpoint')
        if node['task'] is None:
            return plan
        source_record = self.coordinator()._get(owner, node['task']['task_id'])
        require(source_record is not None, 'lab_resource_retry_checkpoint_missing', 409)
        if source_record['state'] == 'completed':
            # A plan can expire after its producer completed, before adopting
            # the output. Verify that stopped producer's actual PNGs; the old
            # cancelled/failed plan itself remains unchanged.
            descriptor = self.coordinator().context(owner, node['task']['task_id'])
            self.coordinator().resolve(descriptor)
            return store.adopt(owner, plan['operation_id'], 'manim',
                dict(content=dict(descriptor=descriptor), local_timing=deepcopy(node['task']['local_timing']),
                    capabilities=['manim'], key_effects=[]), 'restore-retry-checkpoint')
        task = target_node['task']
        if task is None:
            task = store.claim(owner, plan['operation_id'], 'manim', LabManimProducer(self.coordinator()).capabilities())
        if task is None or target_node['status'] in {'completed', 'failed', 'cancelled'}:
            return store.get(owner, plan['operation_id'])
        self.coordinator().restore_retry(owner, LabManimProducer._run(task), node['task']['task_id'])
        # The durable claim has not called submit. Mark it for original-ID
        # lookup immediately instead of waiting the uncertain-submit grace.
        current = store.get(owner, plan['operation_id'])['nodes']['manim']
        if current['status'] == 'submitting':
            store.unknown(owner, plan['operation_id'], 'manim', task['task_id'], 'lab_resource_retry_checkpoint_ready')
        return store.get(owner, plan['operation_id'])

    def advance(self, owner, run, *, cancelled=False):
        operation_id = self.operation_id(run['id'], run.get('resource_round'))
        binding = self._binding(owner, operation_id)
        if binding is None and cancelled:
            return 'cancelled'
        if binding is not None:
            checksum = digest(self.request(run))
            require(binding['request_sha256'] == checksum, 'lab_resource_request_changed', 409)
            stopped = self._stopped_intent(binding)
            if stopped is not None:
                return stopped
            if ((cancelled or self.clock() >= run['deadline_at']) and binding['state'] == 'intent'
                    and self._stop_unstarted_intent(owner, operation_id, checksum, cancelled=cancelled)):
                return 'cancelled' if cancelled else 'failed'
        if binding is None and any(key in run['frozen'] for key in (
                'manim_iteration', 'capability_selection', 'creation_mode')):
            # Check full owned recipe/context bounds before reserving a new
            # resource attempt. Resume always consumes its original task input.
            self.coordinator().recipe_guidance(owner, run['frozen'])
        plan = self._ensure(owner, run)
        if 'retry_source' in plan and not cancelled:
            plan = self._restore_checkpoint(owner, plan)
        store = self._store()
        if cancelled:
            store.cancel(owner, operation_id)
        plan = ResourcePlanWorkflow(store, {'manim': LabManimProducer(self.coordinator())}).advance(owner, operation_id)
        if plan['status'] == 'completed':
            self.placement(owner, run)
            return 'completed'
        if plan['stop_proven'] and plan['status'] in {'blocked', 'timed_out', 'cancelled'}:
            return 'cancelled' if plan['status'] == 'cancelled' else 'failed'
        return 'running'

    def _get(self, owner, operation_id):
        binding = self._binding(owner, operation_id)
        require(binding is not None and binding['state'] == 'active', 'lab_resource_not_ready', 409)
        require(self.path.is_file(), 'lab_resource_ledger_missing', 409)
        plan = self._store().get(owner, operation_id)
        require(plan['request_sha256'] == binding['request_sha256'], 'lab_resource_request_changed', 409)
        return plan

    def context(self, owner, operation_id, *, resource_round=None):
        operation_id = self.operation_id(operation_id, resource_round)
        plan = self._get(owner, operation_id)
        require(plan['status'] == 'completed', 'lab_resource_not_ready', 409)
        return deepcopy(plan['nodes']['manim']['output']['content']['descriptor'])

    def placement(self, owner, run):
        resource_round = run.get('resource_round')
        plan = self._get(owner, self.operation_id(run['id'], resource_round))
        return freeze_manim_placement(self.context(owner, run['id'], resource_round=resource_round),
            start_frame=plan['nodes']['manim']['definition']['placement']['global_start_frame'],
            project_frame_count=round(run['frozen']['duration_seconds'] * 24))

    def view(self, owner, operation_id, *, resource_round=None):
        operation_id = self.operation_id(operation_id, resource_round)
        binding = self._binding(owner, operation_id)
        if binding is not None and self._stopped_intent(binding) is not None:
            return dict(kind='manim', status=('cancelled' if binding['state'] == 'cancelled_unstarted' else 'timed_out'),
                operation_id=None, resource_plan_operation_id=operation_id, resource_version_id=None,
                stop_proven=True, usage=dict(attempts=0), error='stopped_before_resource_plan',
                scope='resource_not_visual_acceptance')
        if binding is None:
            # Historic completed media retains its original task identity. A
            # status read neither migrates its resources nor regenerates them.
            coordinator = self.coordinator()
            if coordinator is not None and resource_round is None:
                original = coordinator.view(owner, operation_id)
                if original.get('operation_id') is not None:
                    return dict(original, historical_resource=True)
            return dict(kind='manim', status='pending', operation_id=None,
                scope='resource_not_visual_acceptance', error=None)
        plan = self._get(owner, operation_id)
        node = plan['nodes']['manim']
        return dict(kind='manim', status=plan['status'], operation_id=node['task']['task_id'] if node['task'] else None,
            resource_plan_operation_id=operation_id, resource_version_id=(node['output'] or {}).get('resource_version_id'),
            budget=plan['budget'], usage=plan['usage'], error=node['error'],
            scope='resource_not_visual_acceptance')
