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
        return dict(id=task['task_id'], frozen=deepcopy(task['inputs']), deadline_at=task['deadline_at'])

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
        return dict(nodes=[dict(id='manim', kind='animation', producer='manim', inputs=inputs,
            dependencies=[], objective=frozen['goal'], required_capabilities=['manim'], key_effects=[],
            local_timing=dict(fps=24, frame_count=options['frame_count']), placement=placement)],
            budget=dict(max_attempts=1, max_attempts_per_node=1), deadline_at=run['deadline_at'])

    def _ensure(self, owner, run):
        request = self.request(run)
        checksum = digest(request)
        # This intent shares the original commission database. Once active,
        # losing the separate resource ledger must not create fresh paid keys.
        with closing(self._connection(create=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM lab_resource_bindings WHERE owner=? AND operation_id=?',
                (owner, run['id'])).fetchone()
            if row is None:
                db.execute('INSERT INTO lab_resource_bindings VALUES(?,?,?,?)',
                    (owner, run['id'], checksum, 'intent'))
                state = 'intent'
            else:
                require(row['request_sha256'] == checksum, 'lab_resource_request_changed', 409)
                state = row['state']
            db.commit()
        if state == 'active':
            require(self.path.is_file(), 'lab_resource_ledger_missing', 409)
            plan = self._store().get(owner, run['id'])
            require(plan['request_sha256'] == checksum, 'lab_resource_request_changed', 409)
            return plan
        plan = self._store().create(owner, run['id'], run['id'], request)
        with closing(self._connection()) as db:
            db.execute("UPDATE lab_resource_bindings SET state='active' WHERE owner=? AND operation_id=? AND request_sha256=?",
                (owner, run['id'], checksum))
        return plan

    def advance(self, owner, run, *, cancelled=False):
        binding = self._binding(owner, run['id'])
        if binding is None and cancelled:
            return 'cancelled'
        plan = self._ensure(owner, run)
        store = self._store()
        if cancelled:
            store.cancel(owner, run['id'])
        plan = ResourcePlanWorkflow(store, {'manim': LabManimProducer(self.coordinator())}).advance(owner, run['id'])
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

    def context(self, owner, operation_id):
        plan = self._get(owner, operation_id)
        require(plan['status'] == 'completed', 'lab_resource_not_ready', 409)
        return deepcopy(plan['nodes']['manim']['output']['content']['descriptor'])

    def placement(self, owner, run):
        plan = self._get(owner, run['id'])
        return freeze_manim_placement(self.context(owner, run['id']),
            start_frame=plan['nodes']['manim']['definition']['placement']['global_start_frame'],
            project_frame_count=round(run['frozen']['duration_seconds'] * 24))

    def view(self, owner, operation_id):
        if self._binding(owner, operation_id) is None:
            # Historic completed media retains its original task identity. A
            # status read neither migrates its resources nor regenerates them.
            coordinator = self.coordinator()
            if coordinator is not None:
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
