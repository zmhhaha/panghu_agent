"""Owner-scoped durable resource plans, immutable attempts and content versions.

This is a business execution budget, not the shared metering/fee ledger. Reads
never schedule work. Adopted media must be verified by its business caller.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from agent_service.contracts import digest
from .resource_plan import (ACTIVE, SCHEMA, TERMINAL, content_definition, descendants,
    identifier, packed, require, text, validate_nodes, validate_output, validate_request, verify_producer)


class ResourcePlanStore:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path).absolute()
        require(not self.path.is_symlink(), 'resource_plan_database_invalid')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with closing(self._connect()) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require(tables <= {'resource_plans'}, 'resource_plan_database_not_isolated', 409)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS resource_plans ('
                'owner TEXT NOT NULL,operation_id TEXT NOT NULL,project_id TEXT NOT NULL,'
                'request_sha256 TEXT NOT NULL,document TEXT NOT NULL,sha256 TEXT NOT NULL,'
                'PRIMARY KEY(owner,operation_id))')

    def _connect(self):
        require(not self.path.is_symlink(), 'resource_plan_database_invalid')
        db = sqlite3.connect(str(self.path), isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def _scope(owner, operation_id):
        text(owner, 200)
        identifier(operation_id)

    def _read(self, db, owner, operation_id):
        self._scope(owner, operation_id)
        row = db.execute('SELECT * FROM resource_plans WHERE owner=? AND operation_id=?',
            (owner, operation_id)).fetchone()
        require(row is not None, 'resource_plan_not_found', 404)
        value = json.loads(row['document'])
        require(digest(value) == row['sha256'] and value['owner'] == owner
            and value['operation_id'] == operation_id and value['project_id'] == row['project_id']
            and value['request_sha256'] == row['request_sha256'], 'resource_plan_record_changed', 409)
        return value

    def _write(self, db, plan):
        raw = packed(plan)
        db.execute('UPDATE resource_plans SET document=?,sha256=? WHERE owner=? AND operation_id=?',
            (raw, digest(plan), plan['owner'], plan['operation_id']))

    @contextmanager
    def _mutation(self, owner, operation_id):
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                plan = self._read(db, owner, operation_id)
                yield plan
                self._write(db, plan)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _new_node(definition):
        return dict(definition=deepcopy(definition), status='queued', output=None, task=None,
            attempts=[], error=None, stop_proven=True, generation=1, history=[])

    def create(self, owner, project_id, operation_id, request):
        self._scope(owner, operation_id)
        identifier(project_id)
        request = validate_request(request)
        checksum = digest(request)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT request_sha256,project_id FROM resource_plans WHERE owner=? AND operation_id=?',
                (owner, operation_id)).fetchone()
            if row:
                require(row['request_sha256'] == checksum and row['project_id'] == project_id,
                    'resource_plan_idempotency_conflict', 409)
            else:
                require(request['deadline_at'] > self.clock(), 'resource_plan_deadline_expired', 409)
                plan = dict(schema=SCHEMA, owner=owner, project_id=project_id, operation_id=operation_id,
                    request_sha256=checksum, deadline_at=request['deadline_at'], budget=request['budget'],
                    usage=dict(attempts=0), revision=1, cancel_requested=False, created_at=self.clock(),
                    nodes={row['id']: self._new_node(row) for row in request['nodes']}, mutations={})
                db.execute('INSERT INTO resource_plans VALUES(?,?,?,?,?,?)',
                    (owner, operation_id, project_id, checksum, packed(plan), digest(plan)))
            db.commit()
        return self.get(owner, operation_id)

    def _view(self, plan):
        result = deepcopy(plan)
        rows = list(result['nodes'].values())
        active = any(not row['stop_proven'] for row in rows)
        expired = self.clock() >= result['deadline_at']
        if result['cancel_requested']:
            status = 'cancelling' if active else 'cancelled'
        elif expired and not all(row['status'] == 'completed' for row in rows):
            status = 'cancelling' if active else 'timed_out'
        elif all(row['status'] == 'completed' for row in rows):
            status = 'completed'
        elif active:
            status = 'running'
        elif any(row['status'] in {'failed', 'blocked'} for row in rows):
            status = 'blocked'
        else:
            status = 'queued'
        result.update(status=status, stop_proven=not active)
        return result

    def get(self, owner, operation_id):
        with closing(self._connect()) as db:
            return self._view(self._read(db, owner, operation_id))

    def cancel(self, owner, operation_id):
        with self._mutation(owner, operation_id) as plan:
            if all(node['status'] == 'completed' for node in plan['nodes'].values()):
                return self._view(plan)
            plan['cancel_requested'] = True
            for node in plan['nodes'].values():
                if node['status'] in {'queued', 'blocked'}:
                    node.update(status='cancelled', stop_proven=True)
        return self.get(owner, operation_id)

    @staticmethod
    def _dependencies(plan, node):
        return {key: plan['nodes'][key]['output'] for key in node['definition']['dependencies']}

    def claim(self, owner, operation_id, node_id, producer_descriptor):
        """Reserve the complete attempt before the adapter can submit anything."""
        with self._mutation(owner, operation_id) as plan:
            require(not plan['cancel_requested'] and self.clock() < plan['deadline_at'],
                'resource_plan_stopped', 409)
            node = plan['nodes'].get(node_id)
            require(node is not None, 'resource_plan_node_not_found', 404)
            if node['status'] != 'queued':
                return None
            dependencies = self._dependencies(plan, node)
            if any(value is None for value in dependencies.values()):
                return None
            descriptor = verify_producer(producer_descriptor, node['definition'])
            require(plan['usage']['attempts'] < plan['budget']['max_attempts']
                and len(node['attempts']) < plan['budget']['max_attempts_per_node'],
                'resource_plan_budget_exhausted', 409)
            task = dict(schema='video.resource-task.v1', task_id=str(uuid4()), owner=owner,
                project_id=plan['project_id'], operation_id=operation_id, node_id=node_id,
                generation=node['generation'], deadline_at=plan['deadline_at'],
                producer_revision=descriptor['revision'], dependency_outputs=deepcopy(dependencies),
                **{key: deepcopy(value) for key, value in node['definition'].items()
                    if key not in {'id', 'dependencies', 'placement'}})
            task['request_sha256'] = digest(task)
            node.update(task=task, status='submitting', error=None, stop_proven=False)
            node['attempts'].append(dict(task=deepcopy(task), status='submitting',
                stop_proven=False, created_at=self.clock(), error=None))
            plan['usage']['attempts'] += 1
            return deepcopy(task)

    def submission_allowed(self, owner, operation_id, task_id):
        plan = self.get(owner, operation_id)
        return (not plan['cancel_requested'] and self.clock() < plan['deadline_at']
            and any(node['task'] is not None and node['task']['task_id'] == task_id
                and node['status'] == 'submitting' for node in plan['nodes'].values()))

    @staticmethod
    def _freeze_output(plan, node, output, task_id):
        output = validate_output(output, node['definition'])
        dependencies = ResourcePlanStore._dependencies(plan, node)
        require(all(value is not None for value in dependencies.values()), 'resource_plan_dependencies_not_ready', 409)
        identity = dict(owner=plan['owner'], project_id=plan['project_id'],
            definition=content_definition(node['definition']),
            source_task_id=task_id,
            producer_revision=node['task']['producer_revision'] if task_id else 'caller-verified-existing',
            dependencies={key: value['resource_version_id'] for key, value in dependencies.items()}, output=output)
        return dict(output, schema='video.resource-version.v1', resource_version_id=digest(identity),
            content_sha256=digest(output['content']), owner=plan['owner'], project_id=plan['project_id'],
            kind=node['definition']['kind'], producer=node['definition']['producer'], source_task_id=task_id,
            producer_revision=identity['producer_revision'],
            validation_scope='resource_custody_and_basic_checks_not_expression_acceptance')

    def record(self, owner, operation_id, node_id, task_id, receipt, *, validated=False):
        with self._mutation(owner, operation_id) as plan:
            node = plan['nodes'].get(node_id)
            require(node is not None and node['task'] and node['task']['task_id'] == task_id,
                'resource_plan_stale_result', 409)
            task, attempt = node['task'], node['attempts'][-1]
            require(type(receipt) is dict and receipt.get('task_id') == task_id
                and receipt.get('request_sha256') == task['request_sha256']
                and type(receipt.get('stop_proven')) is bool,
                'resource_plan_receipt_binding_mismatch', 502)
            status = receipt.get('status')
            require(status in {'queued', 'running', 'completed', 'failed', 'cancelled', 'submission_unknown', 'cancelling'},
                'resource_plan_receipt_invalid', 502)
            if attempt['status'] in TERMINAL:
                require(digest(receipt) == attempt.get('receipt_sha256'),
                    'resource_plan_terminal_result_changed', 409)
                return
            require(receipt['stop_proven'] == (status in TERMINAL), 'resource_plan_stop_unproven', 502)
            if status == 'completed':
                require(validated is True, 'resource_plan_output_unverified', 409)
                if not plan['cancel_requested'] and self.clock() < plan['deadline_at']:
                    node['output'] = self._freeze_output(plan, node, receipt.get('output'), task_id)
                else:
                    status = 'cancelled'
            stored_error = receipt.get('error')
            if stored_error is not None:
                text(stored_error, 1000)
            attempt.update(status=status, stop_proven=receipt['stop_proven'],
                error=stored_error, receipt_sha256=digest(receipt), receipt=deepcopy(receipt), updated_at=self.clock())
            node.update(status='running' if status == 'queued' else status,
                stop_proven=receipt['stop_proven'], error=stored_error)
        return self.get(owner, operation_id)

    def unknown(self, owner, operation_id, node_id, task_id, error='resource_plan_submission_unknown'):
        plan = self.get(owner, operation_id)
        task = plan['nodes'][node_id]['task']
        return self.record(owner, operation_id, node_id, task_id, dict(task_id=task_id,
            request_sha256=task['request_sha256'], status='submission_unknown', stop_proven=False, error=error))

    def block(self, owner, operation_id, node_id, error):
        with self._mutation(owner, operation_id) as plan:
            node = plan['nodes'][node_id]
            if node['status'] == 'queued':
                node.update(status='blocked', error=text(error, 1000))
        return self.get(owner, operation_id)

    @staticmethod
    def _mutation_key(plan, request_id, intent):
        identifier(request_id)
        checksum = digest(intent)
        prior = plan['mutations'].get(request_id)
        require(prior is None or prior == checksum, 'resource_plan_mutation_conflict', 409)
        if prior is not None:
            return False
        require(len(plan['mutations']) < 256, 'resource_plan_mutation_limit', 413)
        plan['mutations'][request_id] = checksum
        return True

    def adopt(self, owner, operation_id, node_id, output, request_id):
        """Record caller-verified existing media without scheduling paid work."""
        with self._mutation(owner, operation_id) as plan:
            if not self._mutation_key(plan, request_id, dict(kind='adopt', node_id=node_id, output=output)):
                return self._view(plan)
            require(not plan['cancel_requested'] and self.clock() < plan['deadline_at'], 'resource_plan_stopped', 409)
            node = plan['nodes'].get(node_id)
            require(node is not None, 'resource_plan_node_not_found', 404)
            require(node['status'] == 'queued' and not node['attempts'], 'resource_plan_adoption_conflict', 409)
            node.update(output=self._freeze_output(plan, node, output, None), status='completed', stop_proven=True)
            plan['revision'] += 1
        return self.get(owner, operation_id)

    def revise(self, owner, operation_id, changes, request_id, reason):
        text(reason)
        require(type(changes) is dict and changes, 'resource_plan_changes_invalid')
        with self._mutation(owner, operation_id) as plan:
            if not self._mutation_key(plan, request_id, dict(kind='revise', changes=changes, reason=reason)):
                return self._view(plan)
            require(not plan['cancel_requested'] and self.clock() < plan['deadline_at'], 'resource_plan_stopped', 409)
            definitions = {key: deepcopy(row['definition']) for key, row in plan['nodes'].items()}
            for key, update in changes.items():
                identifier(key)
                require(type(update) is dict and update, 'resource_plan_changes_invalid')
                before = definitions.get(key)
                definitions[key] = dict(before or {}, **update)
                require(definitions[key].get('id') == key, 'resource_plan_node_identity_changed')
            definitions = validate_nodes(list(definitions.values()))
            old_definitions = {key: row['definition'] for key, row in plan['nodes'].items()}
            content_changes = set()
            for key in changes:
                before, after = old_definitions.get(key), definitions[key]
                if before is not None:
                    require(after['objective'] == before['objective']
                        and set(before['required_capabilities']) <= set(after['required_capabilities'])
                        and set(before['key_effects']) <= set(after['key_effects']),
                        'resource_plan_substitution_forbidden', 409)
                if before is None or content_definition(before) != content_definition(after):
                    content_changes.add(key)
            affected = descendants(definitions, content_changes) | descendants(old_definitions, content_changes)
            require(all(plan['nodes'][key]['stop_proven'] for key in affected if key in plan['nodes']),
                'resource_plan_active_dependency', 409)
            for key, definition in definitions.items():
                if key not in plan['nodes']:
                    plan['nodes'][key] = self._new_node(definition)
                    continue
                node = plan['nodes'][key]
                if key in affected:
                    node['history'].append(dict(generation=node['generation'], definition=deepcopy(node['definition']),
                        output=deepcopy(node['output']), status=node['status'], reason=reason))
                    node.update(generation=node['generation'] + 1, output=None, task=None,
                        status='queued', error=None, stop_proven=True)
                node['definition'] = definition
            plan['revision'] += 1
        return self.get(owner, operation_id)

    def retry(self, owner, operation_id, node_id, request_id, *, producer=None, reason='explicit bounded retry'):
        text(reason)
        with self._mutation(owner, operation_id) as plan:
            if not self._mutation_key(plan, request_id, dict(kind='retry', node_id=node_id, producer=producer, reason=reason)):
                return self._view(plan)
            require(not plan['cancel_requested'] and self.clock() < plan['deadline_at'], 'resource_plan_stopped', 409)
            node = plan['nodes'].get(node_id)
            require(node is not None, 'resource_plan_node_not_found', 404)
            require(node['status'] in {'failed', 'blocked'} and node['stop_proven'],
                'resource_plan_retry_not_stopped', 409)
            require(plan['usage']['attempts'] < plan['budget']['max_attempts']
                and len(node['attempts']) < plan['budget']['max_attempts_per_node'],
                'resource_plan_budget_exhausted', 409)
            # Substitution changes only the producer. Explicit creation goals,
            # required capabilities and key effects cannot be erased here.
            node['history'].append(dict(generation=node['generation'], definition=deepcopy(node['definition']),
                output=deepcopy(node['output']), status=node['status'], reason=reason))
            if producer is not None:
                node['definition']['producer'] = identifier(producer)
            node.update(generation=node['generation'] + 1, output=None, task=None,
                status='queued', error=None, stop_proven=True)
            definitions = {key: row['definition'] for key, row in plan['nodes'].items()}
            for key in descendants(definitions, {node_id}) - {node_id}:
                dependent = plan['nodes'][key]
                if dependent['status'] == 'blocked' and dependent['error'] == 'resource_plan_dependency_unavailable':
                    dependent.update(status='queued', error=None)
            plan['revision'] += 1
        return self.get(owner, operation_id)
