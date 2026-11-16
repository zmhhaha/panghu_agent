"""Durable, bounded formal-film review and refinement coordination.

One sequence owns the allowance; changed renders never create a new allowance.
The injected production driver uses the existing Agent/module/render interfaces.
Every paid child is claimed before submission and uncertain calls only reconcile
the original key. No laboratory operation or aesthetic acceptance is created.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from .production_sequence_store import (require, text, prose, source_ref,
    requirement_items, requirement_budget)


TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
CHILD_ACTIVE = frozenset({'queued', 'starting', 'running', 'submitting', 'cancelling'})


def time_range_intersects(start, end, scope_start, scope_end, *, include_end=False):
    """Half-open overlap; points at the complete film's end belong to its tail."""
    if start == end:
        return scope_start <= start < scope_end or include_end and start == scope_end
    return start < scope_end and end > scope_start


class ProductionRefinementService:
    """Coordinate one review, at most one module rewrite pass and one render.

    Driver contract: ``authorize(owner, project_id)``, ``freeze(owner, project,
    source)``; ``validate_request(context, request)`` checks deterministic input
    budgets without running a model; ``prepare(stage, key, context, state)`` returns an immutable input
    digest plus a callable admitting exactly one child; ``lookup`` returns None
    or a checked stopped/active receipt. ``cancel`` only requests cancellation.
    A receipt contains state, id, stop_proven and output; the driver must verify
    real media/model evidence before reporting a completed child.

    ``tick`` is nonblocking apart from bounded individual adapter calls. The
    persistent CAS journal supports restarts and competing workflow instances.
    """

    def __init__(self, path, sequences, driver):
        self.path, self.sequences, self.driver = str(Path(path).resolve()), sequences, driver
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS production_refinements (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                idem TEXT NOT NULL, request_sha256 TEXT NOT NULL, revision INTEGER NOT NULL,
                document TEXT NOT NULL, UNIQUE(owner, idem))''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _packed(value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        require(len(raw.encode()) <= 4 * 1024 * 1024, 'production_refinement_record_too_large', 413)
        return raw

    def _read(self, operation_id, owner=None):
        with self._db() as db:
            row = db.execute('SELECT document FROM production_refinements WHERE id=?', (operation_id,)).fetchone()
        require(row is not None, 'production_refinement_not_found', 404)
        value = json.loads(row[0])
        require(owner is None or value['owner'] == owner, 'production_refinement_not_found', 404)
        return value

    def _save(self, value):
        previous = value['revision']
        updated = dict(value, revision=previous + 1, updated_at=time.time())
        with self._db() as db:
            count = db.execute('UPDATE production_refinements SET revision=?,document=? WHERE id=? AND revision=?',
                (updated['revision'], self._packed(updated), value['id'], previous)).rowcount
        require(count == 1, 'production_refinement_revision_conflict', 409)
        value.update(updated)

    @staticmethod
    def public(value):
        result = {key: deepcopy(value.get(key)) for key in ('id', 'project_id', 'sequence_id', 'kind',
            'source', 'status', 'phase', 'error', 'budget', 'usage', 'review', 'result', 'created_at', 'updated_at')}
        result.update(selection='draft_only', aesthetic_status='not_accepted',
            request_id=value['request']['request_id'],
            allowance_operation_id=value.get('allowance_operation_id'),
            children=[{key: deepcopy(row.get(key)) for key in ('stage', 'index', 'key', 'status', 'id',
                'stop_proven', 'error')} for row in value['children']])
        return result

    def get(self, owner, operation_id):
        value = self._read(operation_id, owner)
        self.driver.authorize(owner, value['project_id'])
        return self.public(value)

    def list(self, owner, project_id):
        self.driver.authorize(owner, project_id)
        with self._db() as db:
            rows = db.execute('SELECT document FROM production_refinements WHERE owner=? AND project_id=? ORDER BY rowid DESC',
                (owner, project_id)).fetchall()
        return [self.public(json.loads(row[0])) for row in rows]

    def start(self, owner, project_id, source, *, kind, request_id, instruction=None, items=None, budget=None):
        """Admit formal work only; a requirements task never refunds 1+2 rounds."""
        text(owner); text(project_id); text(request_id, 128)
        source = source_ref(source)
        require(type(kind) is str and kind in {'automatic', 'user_requested', 'requirements'})
        if kind == 'automatic':
            require(instruction is None and items is None and budget is None)
        elif kind == 'user_requested':
            instruction = prose(instruction, 16000)
            require(items is None and budget is None)
        else:
            require(instruction is None)
            items, budget = requirement_items(items), requirement_budget(budget)
        request = dict(project_id=project_id, source=source, kind=kind, request_id=request_id,
            instruction=instruction, items=items, budget=budget)
        fingerprint = digest(request)
        self.driver.authorize(owner, project_id)
        with self._db() as db:
            prior = db.execute('SELECT request_sha256,document FROM production_refinements WHERE owner=? AND idem=?',
                (owner, request_id)).fetchone()
        if prior is not None:
            require(prior[0] == fingerprint, 'production_refinement_idempotency_conflict', 409)
            return self.public(json.loads(prior[1]))
        # Source is already registered by a real finished formal-film run.
        known = self.sequences.source_for_run(owner, project_id, source['run_id'], source['version_id'])
        require(known is not None and known['source'] == source, 'production_refinement_source_not_registered', 409)
        context = self.driver.freeze(owner, project_id, source)
        require(type(context) is dict and context.get('source') == source
            and type(context.get('module_version_ids')) is list and bool(context['module_version_ids'])
            and len(context['module_version_ids']) == len(context.get('film', {}).get('scenes', [])),
            'production_refinement_context_invalid', 502)
        self.driver.validate_request(context, request)
        count = len(context['module_version_ids'])
        duration = context['film']['scenes'][-1]['end_seconds']
        if kind == 'requirements':
            require(all(row['scope']['kind'] == 'whole_film'
                or row['scope']['end_seconds'] <= duration for row in items),
                'production_refinement_requirement_outside_film', 422)
        now = time.time()
        value = dict(schema='video.production-refinement.v1', id=str(uuid4()), owner=owner, project_id=project_id,
            request=request, request_sha256=fingerprint, source=source, sequence_id=known['sequence']['id'],
            sequence_revision=known['sequence']['revision'], context=deepcopy(context), context_sha256=digest(context),
            kind=kind, status='admitting', phase='allowance', revision=1, created_at=now, updated_at=now,
            allowance_operation_id=None, cancel_requested=False, children=[], review=None, result=None, error=None,
            module_version_ids=deepcopy(context['module_version_ids']),
            budget=budget or dict(max_agent_tasks=count + 1, max_renders=1),
            usage=dict(agent_tasks=0, renders=0))
        with self._db() as db:
            try:
                db.execute('INSERT INTO production_refinements VALUES (?,?,?,?,?,?,?)',
                    (value['id'], owner, project_id, request_id, fingerprint, 1, self._packed(value)))
            except sqlite3.IntegrityError:
                prior = db.execute('SELECT request_sha256,document FROM production_refinements WHERE owner=? AND idem=?',
                    (owner, request_id)).fetchone()
                require(prior is not None and prior[0] == fingerprint, 'production_refinement_idempotency_conflict', 409)
                return self.public(json.loads(prior[1]))
        self._admit(value)
        return self.public(value)

    def _admit(self, value):
        request = value['request']
        key = 'film-refinement:' + value['id'] + ':allowance'
        args = (value['owner'], value['project_id'], value['sequence_id'])
        if value['kind'] == 'requirements':
            operation = self.sequences.reserve_requirements(*args, value['source']['version_id'], request['items'],
                value['budget'], key, expected_revision=value['sequence_revision'])
        else:
            user = (dict(request_id=request['request_id'], viewed_version_id=value['source']['version_id'],
                instruction=request['instruction']) if value['kind'] == 'user_requested' else None)
            operation = self.sequences.reserve_round(*args, value['kind'], value['source']['version_id'],
                digest(dict(context_sha256=value['context_sha256'], request=request)), key,
                expected_revision=value['sequence_revision'], requested_by_user=user)
        value.update(allowance_operation_id=operation['id'], status='running', phase='review')
        self._save(value)

    def cancel(self, owner, operation_id):
        with self.lock:
            value = self._read(operation_id, owner)
            self.driver.authorize(owner, value['project_id'])
            if value['status'] not in TERMINAL:
                value['cancel_requested'] = True
                self._save(value)
            return self.public(value)

    def _allowance(self, value):
        args = (value['owner'], value['project_id'], value['allowance_operation_id'])
        operation = self.sequences.operation(*args)
        if operation['status'] == 'reserved':
            operation = self.sequences.claim_submission(*args, expected_revision=operation['revision'])
        if operation['status'] in {'submitting', 'submission_unknown'}:
            self.sequences.submitted(*args, value['id'], expected_revision=operation['revision'])

    def _finish(self, value, status, error=None):
        # Never end/refund a round while an ambiguous or active child exists.
        require(all(row['status'] in TERMINAL and row.get('stop_proven') is True for row in value['children']),
            'production_refinement_stop_not_proven', 409)
        proof = dict(coordinator_id=value['id'], children=[dict(key=row['key'],
            receipt_sha256=digest(row.get('receipt', {}))) for row in value['children']])
        result = value['result'] or dict(error=error, review=value['review'])
        operation = self.sequences.operation(value['owner'], value['project_id'], value['allowance_operation_id'])
        self.sequences.finish(value['owner'], value['project_id'], operation['id'], status, digest(result),
            expected_revision=operation['revision'], stop_proof_sha256=digest(proof), error=error)
        value.update(status=status, phase='finished', error=error)
        self._save(value)

    @staticmethod
    def _first_changed(value):
        scenes = value['context']['film']['scenes']
        if value['kind'] == 'user_requested':
            return 0
        ranges = []
        for item in value['request'].get('items') or []:
            if item['scope']['kind'] == 'whole_film':
                return 0
            ranges.append((item['scope']['start_seconds'], item['scope']['end_seconds']))
        for item in value['review']['issues']:
            require(type(item) is dict and all(type(item.get(key)) in (int, float)
                and math.isfinite(item[key]) for key in ('start_seconds', 'end_seconds'))
                and 0 <= item['start_seconds'] <= item['end_seconds'] <= scenes[-1]['end_seconds'],
                'production_refinement_review_range_invalid', 502)
            if value['kind'] != 'requirements':
                ranges.append((item['start_seconds'], item['end_seconds']))
        touched = [index for index, scene in enumerate(scenes) if any(
            time_range_intersects(start, end, scene['start_seconds'], scene['end_seconds'],
                include_end=index == len(scenes) - 1) for start, end in ranges)]
        return min(touched) if touched else None

    def _child(self, value, stage, index=None):
        key = 'film-refinement:' + value['id'] + ':' + stage + (':' + str(index) if index is not None else '')
        current = next((row for row in value['children'] if row['key'] == key), None)
        if current is not None:
            return current
        counter, limit = ('renders', 'max_renders') if stage == 'render' else ('agent_tasks', 'max_agent_tasks')
        require(value['usage'][counter] < value['budget'][limit], 'production_refinement_budget_exhausted', 409)
        current = dict(key=key, stage=stage, index=index, status='reserved', id=None,
            input_sha256=None, stop_proven=False, error=None, receipt=None)
        value['children'].append(current)
        value['usage'][counter] += 1
        self._save(value)
        return current

    def _advance_child(self, value, child):
        if child['status'] == 'reserved':
            try:
                checksum, submit = self.driver.prepare(child['stage'], child['key'], value['context'], value)
            except ServiceError as error:
                if error.status >= 500:
                    raise
                child.update(status='failed', stop_proven=True, error=error.code,
                    receipt=dict(unsubmitted=True, preparation_rejected=error.code))
                self._save(value)
                return
            require(child['input_sha256'] in (None, checksum), 'production_refinement_child_input_changed', 409)
            child.update(status='submitting', input_sha256=checksum)
            self._save(value)  # Single CAS claimant alone can call submit.
            try:
                submit()
            except ServiceError as error:
                if error.status < 500:
                    child.update(status='failed', stop_proven=True, error=error.code,
                        receipt=dict(definitive_rejection=error.code))
                else:
                    child.update(status='submission_unknown', error=error.code)
                self._save(value)
                return
            except Exception:
                child.update(status='submission_unknown', error='production_refinement_submission_interrupted')
                self._save(value)
                return
        receipt = self.driver.lookup(child['stage'], child['key'], value['context'], value)
        if receipt is None:
            child.update(status='submission_unknown', error='production_refinement_child_not_visible')
            self._save(value)
            return
        require(type(receipt) is dict and receipt.get('state') in CHILD_ACTIVE | TERMINAL
            and type(receipt.get('id')) is str and type(receipt.get('stop_proven')) is bool,
            'production_refinement_child_receipt_invalid', 502)
        require(child['id'] in (None, receipt['id']), 'production_refinement_child_identity_changed', 409)
        if receipt['state'] in TERMINAL:
            require(receipt['stop_proven'], 'production_refinement_stop_not_proven', 409)
        child.update(id=receipt['id'], status=receipt['state'], stop_proven=receipt['stop_proven'],
            error=receipt.get('error'), receipt=deepcopy(receipt))
        self._save(value)

    def _step(self, value):
        require(digest(value['context']) == value['context_sha256'], 'production_refinement_context_changed', 409)
        if value['status'] == 'admitting':
            self._admit(value)
        self._allowance(value)
        pending = next((row for row in value['children'] if row['status'] not in TERMINAL), None)
        if value['cancel_requested']:
            if pending and pending['status'] != 'reserved':
                self.driver.cancel(pending['stage'], pending['key'], value['context'], value)
                self._advance_child(value, pending)
                return
            if pending:
                pending.update(status='cancelled', stop_proven=True, receipt=dict(unsubmitted=True))
                self._save(value)
            self._finish(value, 'cancelled')
            return
        if pending:
            self._advance_child(value, pending)
            return
        failed = next((row for row in value['children'] if row['status'] != 'completed'), None)
        if failed:
            self._finish(value, 'failed', failed.get('error') or 'production_refinement_child_failed')
            return
        review = next((row for row in value['children'] if row['stage'] == 'review'), None)
        if review is None:
            self._advance_child(value, self._child(value, 'review'))
            return
        if value['review'] is None:
            output = review['receipt']['output']
            require(type(output) is dict and type(output.get('issues')) is list
                and type(output.get('limitations')) is list and type(output.get('evidence_scope')) is str,
                'production_refinement_review_invalid', 502)
            value['review'] = deepcopy(output)
            value['first_changed_index'] = self._first_changed(value)
            self._save(value)
        first = value['first_changed_index']
        if first is None:
            value['result'] = dict(source=value['source'], revision_performed=False,
                review=value['review'], selection='draft_only', aesthetic_status='not_accepted')
            self._finish(value, 'completed')
            return
        for index in range(first, len(value['module_version_ids'])):
            child = next((row for row in value['children'] if row['stage'] == 'module' and row['index'] == index), None)
            if child is None:
                value['phase'] = 'revision'
                self._advance_child(value, self._child(value, 'module', index))
                return
            version_id = child['receipt']['output']['version_id']
            if value['module_version_ids'][index] != version_id:
                value['module_version_ids'][index] = version_id
                self._save(value)
        render = next((row for row in value['children'] if row['stage'] == 'render'), None)
        if render is None:
            value['phase'] = 'render'
            self._advance_child(value, self._child(value, 'render'))
            return
        output = render['receipt']['output']
        source = source_ref(output['source'])
        require(source['run_id'] == value['source']['run_id'], 'production_refinement_result_lineage_changed', 409)
        if 'result_binding' not in value:
            sequence = self.sequences.get(value['owner'], value['project_id'], value['sequence_id'])
            value['result_binding'] = dict(source=source, expected_revision=sequence['revision'])
            self._save(value)
        binding = value['result_binding']
        require(binding['source'] == source, 'production_refinement_result_changed', 409)
        self.sequences.bind_source(value['owner'], value['project_id'], value['sequence_id'], source,
            'film-refinement:' + value['id'] + ':result', expected_revision=binding['expected_revision'])
        value['result'] = dict(source=source, revision_performed=True, review=value['review'],
            module_version_ids=deepcopy(value['module_version_ids']), selection='draft_only',
            aesthetic_status='not_accepted', post_revision_agent_review_performed=False)
        self._finish(value, 'completed')

    def tick(self):
        with self.lock:
            with self._db() as db:
                rows = db.execute('SELECT document FROM production_refinements ORDER BY rowid').fetchall()
            for row in rows:
                value = json.loads(row[0])
                if value['status'] in TERMINAL:
                    continue
                try:
                    self._step(value)
                except ServiceError as error:
                    if error.code == 'production_refinement_revision_conflict':
                        continue
                    # Immutable input/admission errors are retained. Do not
                    # declare active external work stopped or replenish budget.
                    value['error'] = error.code
                    if not value['children'] and value['allowance_operation_id'] is None and error.status < 500:
                        value.update(status='failed', phase='finished')
                    if (error.code == 'production_refinement_budget_exhausted'
                            and value['allowance_operation_id'] is not None
                            and all(row['status'] in TERMINAL and row.get('stop_proven') for row in value['children'])):
                        self._finish(value, 'failed', error.code)
                    else:
                        self._save(value)
                except Exception:
                    value['error'] = 'production_refinement_reconciliation_interrupted'
                    self._save(value)
