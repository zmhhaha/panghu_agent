"""Recoverable library visibility, separate from immutable creative evidence.

Only this metadata store is written. Originals, execution budgets, publications
and production inputs remain intact. Unknown execution is never stopped here.
"""
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from functools import wraps
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .animation_creation_service import _run_lock
from .animation_method_commissions import OP_TERMINAL, TERMINAL
from .animation_method_gateway import (AnimationMethodAuthorGateway, AnimationMethodReviewGateway,
    AnimationSkillMethodAuthorGateway, AnimationSkillMethodReviewGateway)
from .animation_collaboration_gateway import AnimationCollaborationGateway

_PROBES = ThreadPoolExecutor(max_workers=4, thread_name_prefix='library-stop-read')
_PROBE_SLOTS = threading.BoundedSemaphore(4)
_AGENT_STOPPED = frozenset({'completed', 'failed', 'cancelled', 'needs_input', 'timed_out'})
_METHOD_STOP_GATEWAYS = {
    AnimationCollaborationGateway.protocol.INPUT_SCHEMA: {'creator': AnimationCollaborationGateway},
    AnimationMethodAuthorGateway.protocol.INPUT_SCHEMA: {
        'creator': AnimationMethodAuthorGateway, 'review': AnimationMethodReviewGateway},
    AnimationSkillMethodAuthorGateway.protocol.INPUT_SCHEMA: {
        'creator': AnimationSkillMethodAuthorGateway, 'review': AnimationSkillMethodReviewGateway},
}


def require(value, code='animation_method_trash_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def serialized_library_mutation(function):
    @wraps(function)
    def wrapped(self, owner, *args, **kwargs):
        # Concurrent opinion retries retain their original atomic/idempotent
        # behavior while sharing the deletion lock. Execution actions fail
        # immediately when another mutation is already in progress.
        wait = 5 if function.__name__ in {'feedback', 'withdraw_feedback'} else 0
        with self.trash.mutation(owner, wait_seconds=wait):
            return function(self, owner, *args, **kwargs)
    return wrapped


class AnimationMethodTrash:
    def __init__(self, service):
        self.service = service
        self.local = threading.local()
        with closing(sqlite3.connect(service.metadata_path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS animation_trash_entries '
                '(id TEXT PRIMARY KEY,owner TEXT NOT NULL,document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS animation_trash_restorations '
                '(entry_id TEXT PRIMARY KEY,owner TEXT NOT NULL,document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS animation_trash_requests '
                '(owner TEXT NOT NULL,request_id TEXT NOT NULL,request_sha256 TEXT NOT NULL,'
                'result TEXT NOT NULL,PRIMARY KEY(owner,request_id))')
            db.commit()

    @contextmanager
    def mutation(self, owner, *, wait_seconds=0):
        require(type(owner) is str and 1 <= len(owner) <= 200
                and not any(ord(c) < 32 for c in owner), 'animation_method_owner_invalid', 401)
        held = getattr(self.local, 'owner', None)
        if held == owner:
            yield
            return
        require(held is None, 'animation_method_library_busy', 409)
        path = self.service.root / 'locks' / ('library-' + digest(owner) + '.lock')
        deadline = time.monotonic() + wait_seconds
        while True:
            with _run_lock(path) as acquired:
                if acquired:
                    self.local.owner = owner
                    try:
                        yield
                    finally:
                        self.local.owner = None
                    return
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'animation_method_library_busy', 409)
            time.sleep(min(.025, remaining))

    @contextmanager
    def database(self, *, write=False):
        path = self.service.metadata_path
        db = sqlite3.connect(str(path) if write else path.as_uri() + '?mode=ro',
                             uri=not write, timeout=20)
        try:
            for alias, filename in (('commissions', self.service.commissions.path),
                                    ('methods', self.service.methods.path)):
                db.execute('ATTACH DATABASE ? AS ' + alias,
                    (str(filename) if write else Path(filename).as_uri() + '?mode=ro',))
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            if write:
                db.commit()
        except (KeyError, ValueError, TypeError, sqlite3.DatabaseError):
            raise ServiceError('animation_method_trash_record_changed', 409) from None
        finally:
            db.close()

    @staticmethod
    def documents(db, table, owner=None):
        statement = 'SELECT document FROM ' + table
        return [json.loads(row[0]) for row in db.execute(
            statement + (' WHERE owner=?' if owner is not None else '') + ' ORDER BY rowid',
            (owner,) if owner is not None else ())]

    def state(self, db, owner):
        # Match list_all(): effect grouping receives newest runs first and
        # presents each effect's history oldest first.
        runs = list(reversed(self.documents(db, 'commissions.animation_method_commissions', owner)))
        ops = self.documents(db, 'commissions.animation_method_commission_operations', owner)
        identities = {r[0]: r[1:] for r in db.execute('SELECT id,owner,state FROM commissions.animation_method_commissions WHERE owner=?', (owner,))}
        op_identities = {row[0]: row[1:] for row in db.execute('SELECT id,owner,commission_id,status '
            'FROM commissions.animation_method_commission_operations WHERE owner=?', (owner,))}
        for run in runs:
            require(identities.get(run['id']) == (owner, run.get('state')) and run.get('owner') == owner
                and run.get('frozen_sha256') == digest(run['frozen']),
                'animation_method_trash_record_changed', 409)
            run['operations'] = [op for op in ops if op['commission_id'] == run['id']]
        for op in ops:
            require(op_identities.get(op['id']) == (owner, op.get('commission_id'), op.get('status'))
                and op.get('commission_id') in identities
                and op.get('owner') == owner and op.get('input_sha256') == digest(op['input'])
                and op.get('request_sha256') == digest(dict(stage=op['stage'], input=op['input']))
                and (op['status'] != 'completed' or op.get('result_sha256') == digest(op['result'])),
                'animation_method_trash_record_changed', 409)
            if 'input_resource_manifest' in op:
                collaborative = op['input'].get('schema') == AnimationCollaborationGateway.protocol.INPUT_SCHEMA
                expected = [dict(path='collaboration-input.json' if collaborative else 'animation-method-input.json',
                                 sha256=op['input_sha256'])]
                expected.extend(dict(path=r['file_name'], sha256=r['sha256'])
                                for r in op['input'].get('observation', {}).get('frames', []))
                require(op['input_resource_manifest'] == sorted(expected, key=lambda r: r['path'])
                    and op.get('input_resource_manifest_sha256') == digest(op['input_resource_manifest']),
                    'animation_method_trash_record_changed', 409)
        entries = self.documents(db, 'animation_trash_entries', owner)
        restorations = {r['entry_id']: r for r in self.documents(db, 'animation_trash_restorations', owner)}
        by_id = {r['id']: r for r in runs}
        hidden = set()
        for entry in entries:
            require(entry['owner'] == owner and entry['sha256'] == digest({k: v for k, v in entry.items()
                if k != 'sha256'}), 'animation_method_trash_record_changed', 409)
            if entry['id'] not in restorations:
                for member in entry['members']:
                    run = by_id.get(member['id'])
                    require(run is not None and run['frozen_sha256'] == member['frozen_sha256'],
                        'animation_method_trash_record_changed', 409)
                    hidden.add(member['id'])
            else:
                restored = restorations[entry['id']]
                require(restored.get('owner') == owner and restored.get('entry_sha256') == entry['sha256'],
                    'animation_method_trash_record_changed', 409)
        demos = self.documents(db, 'methods.animation_demonstrations', owner)
        memberships = self.documents(db, 'effect_memberships', owner)
        deletions = self.documents(db, 'commission_deletions', owner)
        separations = self.documents(db, 'effect_separations', owner)
        from .animation_method_service import _effect_views, _deleted_commission_ids
        adoptions = {row[0]: dict(revision=row[1], commission_id=row[2],
            candidate_id=row[3]) for row in db.execute(
                'SELECT effect_id,revision,commission_id,candidate_id FROM animation_effect_adoption_heads WHERE owner=?', (owner,))}
        effects, _ = _effect_views(runs, demos, memberships, deletions, separations, adoptions=adoptions)
        return dict(runs=runs, by_id=by_id, demos=demos, effects=effects,
                    hidden=hidden, legacy_hidden=_deleted_commission_ids(runs, deletions),
                    entries=entries, restorations=restorations)

    def hidden(self, owner):
        with self.database() as db:
            return self.state(db, owner)['hidden']

    @staticmethod
    def snapshot(scope, target_id, members):
        return dict(scope=scope, target_id=target_id,
            members=sorted((dict(id=r['id'], frozen_sha256=r['frozen_sha256'],
                execution_sha256=digest(dict(state=r['state'], operations=[
                    {k: op.get(k) for k in ('id', 'status', 'job_id', 'input_sha256', 'result_sha256')}
                    for op in r['operations']]))) for r in members),
                           key=lambda r: r['id']))

    def protected(self, db, state):
        """Bind published evidence and exact delivered versions, not acceptance."""
        publications = self.documents(db, 'methods.animation_method_publications')
        validation_ids = {identity for p in publications for key in
            ('reference_validation_ids', 'transfer_validation_ids') for identity in p.get(key, [])}
        validations = self.documents(db, 'methods.animation_method_validations')
        demo_ids = {v[key] for v in validations if v['id'] in validation_ids
                    for key in ('demonstration_version_id', 'baseline_demonstration_version_id') if v.get(key)}
        protected = {d['provenance']['commission_id'] for d in state['demos']
            if d['id'] in demo_ids and d.get('provenance', {}).get('commission_id')}
        # Formal delivery only admits exact published versions. Their actual
        # publication evidence above remains protected regardless of recipient.
        # Merely reusing that method in a new experiment does not protect it.
        return protected

    def eligibility(self, db, state, scope, target_id):
        if scope == 'commission':
            run = state['by_id'].get(target_id)
            require(run is not None, 'animation_method_commission_not_found', 404)
            members, title = [run], run['title']
        else:
            effect = next((e for e in state['effects'] if e['id'] == target_id), None)
            require(effect is not None, 'animation_method_effect_not_found', 404)
            members = [state['by_id'][r['id']] for r in effect['commissions']]
            title = effect['title']
        snap = self.snapshot(scope, target_id, members)
        reasons = []
        if all(r['id'] in state['hidden'] | state['legacy_hidden'] for r in members):
            reasons.append('already_trashed')
        if any(r['state'] not in TERMINAL for r in members):
            reasons.append('active')
        if any(op['status'] in {'submitting', 'submission_unknown'} for r in members for op in r['operations']):
            reasons.append('uncertain')
        if any(op['status'] not in OP_TERMINAL for r in members for op in r['operations']):
            reasons.append('active')
        if {r['id'] for r in members} & self.protected(db, state):
            reasons.append('protected')
        return dict(allowed=not reasons, reason_codes=sorted(set(reasons)),
                    snapshot_sha256=digest(snap), member_count=len(members),
                    requires_stop_check=True), members, title, snap

    def views(self, owner):
        """Group before hiding, so deleting a root never renames an effect."""
        from .animation_method_service import _sample_engine, _sample_index, _candidates, _public_url
        with self.database() as db:
            state = self.state(db, owner)
            effects, membership = [], {}
            samples = _sample_index(state['demos'])
            for original in state['effects']:
                remaining = [r for r in original['commissions'] if r['id'] not in state['hidden']]
                if not remaining:
                    continue
                view = deepcopy(original)
                view['commissions'] = remaining
                for row in remaining:
                    row['deletion'] = self.eligibility(db, state, 'commission', row['id'])[0]
                if view['open_commission_id'] in state['hidden']:
                    usable = [r for r in remaining if r['best_candidate_id'] and r['state'] == 'completed']
                    chosen = max(usable or remaining, key=lambda r: (r['created_at'], r['id']))
                    run = state['by_id'][chosen['id']]
                    candidate = next((c for c in _candidates(run) if c['id'] == run['best_candidate_id']), None)
                    engine = _sample_engine(candidate, samples)
                    view.pop('authoring_tools', None)
                    view.update(engine)
                    best = (dict(id=candidate['id'], media_url=_public_url(run['id'], candidate['id'], 'media'),
                        **engine) if candidate else None)
                    view.update(open_commission_id=run['id'], request_id=run['request_id'], state=run['state'],
                        best_candidate=best, media_url=best['media_url'] if best else None,
                        engines=[engine['engine']] if engine['engine'] else [])
                view['deletion'] = self.eligibility(db, state, 'effect', view['id'])[0]
                effects.append(view)
                for row in remaining:
                    membership[row['id']] = view
            return state, effects, membership

    def _read_stop(self, function, deadline):
        require(time.monotonic() < deadline and _PROBE_SLOTS.acquire(blocking=False),
                'animation_method_trash_stop_unproven', 409)
        try:
            future = _PROBES.submit(function)
        except Exception:
            _PROBE_SLOTS.release()
            raise
        future.add_done_callback(lambda _: _PROBE_SLOTS.release())
        try:
            return future.result(timeout=max(.001, min(5, deadline - time.monotonic())))
        except Exception:
            raise ServiceError('animation_method_trash_stop_unproven', 409) from None

    def _stop_gateway(self, operation):
        schema, stage = operation['input'].get('schema'), operation['stage']
        gateway = (self.service.collaboration_author if schema == AnimationCollaborationGateway.protocol.INPUT_SCHEMA
                   else self.service.reviewer if stage == 'review' else self.service.author)
        expected = _METHOD_STOP_GATEWAYS.get(schema, {}).get(stage)
        require(gateway is not None and expected is not None, 'animation_method_trash_stop_unproven', 409)
        # Lookup uses frozen business/idempotency namespaces. Keep the same
        # current caller's transport without changing the admission gateway.
        historical = copy(gateway)
        for name in ('protocol', 'profile', 'business_prefix', 'idempotency_prefix'):
            setattr(historical, name, getattr(expected, name))
        return historical

    def _stop_origin(self, owner, run, operation):
        """Resolve a retry checkpoint to the task that actually executed it."""
        original, current, current_run, seen = operation, operation, run, set()
        while current.get('reused_from') is not None:
            origin = current['reused_from']
            require(type(origin) is dict and set(origin) == {'commission_id', 'operation_id'}
                and all(type(value) is str and value for value in origin.values())
                and current['id'] not in seen and current['status'] == 'completed'
                and current['stage'] in {'creator', 'render'}
                and current_run['frozen'].get('retry_of_commission_id') == origin['commission_id'],
                'animation_method_trash_stop_unproven', 409)
            seen.add(current['id'])
            previous_run = self.service.commissions.get(owner, origin['commission_id'])
            previous = self.service.commissions.operation(owner, origin['operation_id'])
            require(previous_run['owner'] == owner and previous['owner'] == owner
                and previous['commission_id'] == previous_run['id']
                and previous_run['state'] in {'failed', 'cancelled'}
                and all(op['status'] in OP_TERMINAL for op in previous_run['operations'])
                and previous['stage'] == current['stage'] and previous['status'] == 'completed'
                and previous.get('job_id') == current.get('job_id')
                and previous['result_sha256'] == current['result_sha256']
                and previous['result'] == current['result'],
                'animation_method_trash_stop_unproven', 409)
            if current['stage'] == 'creator':
                require(previous['input_sha256'] == current['input_sha256']
                    and previous['input'] == current['input'], 'animation_method_trash_stop_unproven', 409)
            else:
                require({key: value for key, value in current['input'].items() if key != 'author_operation_id'}
                    == {key: value for key, value in previous['input'].items() if key != 'author_operation_id'},
                    'animation_method_trash_stop_unproven', 409)
            current, current_run = previous, previous_run
        if original['stage'] == 'render':
            require(original.get('artifact_operation_id', original['id']) == current['id'],
                'animation_method_trash_stop_unproven', 409)
        return current

    def stopped(self, owner, members):
        deadline = time.monotonic() + 20
        for run in members:
            for op in run['operations']:
                if op['status'] == 'released':
                    require(op.get('job_id') is None and bool(op.get('release_proof')),
                            'animation_method_trash_stop_unproven', 409)
                    continue
                actual = self._stop_origin(owner, run, op)
                if op['stage'] == 'render':
                    reader = getattr(self.service.renderer, 'stopping_proof', None)
                    require(callable(reader), 'animation_method_trash_stop_unproven', 409)
                    proof = self._read_stop(lambda: reader(actual['id']), deadline)
                    require(type(proof) is dict and proof.get('operation_id') == actual['id']
                            and proof.get('stop_proven') is True, 'animation_method_trash_stop_unproven', 409)
                else:
                    gateway = self._stop_gateway(actual)
                    job = self._read_stop(lambda: gateway.lookup(actual['id']), deadline)
                    require(type(job) is dict and job.get('stop_proven') is True
                        and job.get('state') in _AGENT_STOPPED
                        and (actual.get('job_id') is None or job.get('job_id') == actual['job_id']),
                        'animation_method_trash_stop_unproven', 409)
            if run['frozen'].get('resource_authoring'):
                self.resources_stopped(owner, run, deadline)

    @staticmethod
    @contextmanager
    def resource_database(path):
        path = Path(path)
        require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
                'animation_method_trash_stop_unproven', 409)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db

    def coordinator_record(self, coordinator, owner, operation_id):
        with self.resource_database(coordinator.path) as db:
            row = db.execute('SELECT document,sha256 FROM resources WHERE owner=? AND commission_id=?',
                             (owner, operation_id)).fetchone()
        if row is None:
            return None
        value = json.loads(row['document'])
        require(digest(value) == row['sha256'] and value['scope']['owner'] == owner
            and value['scope']['experiment_id'] == operation_id,
            'animation_method_trash_stop_unproven', 409)
        return value

    def resources_stopped(self, owner, run, deadline):
        stage, coordinator = self.service.resource_stage, self.service.manim
        require(coordinator is not None, 'animation_method_trash_stop_unproven', 409)
        with self.resource_database(stage.bindings_path) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_resource_bindings'").fetchone()
            binding = db.execute('SELECT * FROM lab_resource_bindings WHERE owner=? AND operation_id=?',
                (owner, run['id'])).fetchone() if exists else None
        if binding is not None:
            require(binding['state'] == 'active', 'animation_method_trash_stop_unproven', 409)
            with self.resource_database(stage.path) as db:
                row = db.execute('SELECT * FROM resource_plans WHERE owner=? AND operation_id=?',
                    (owner, run['id'])).fetchone()
            require(row is not None, 'animation_method_trash_stop_unproven', 409)
            plan = json.loads(row['document'])
            require(digest(plan) == row['sha256'] and plan['owner'] == owner
                and plan['operation_id'] == run['id'] and plan['request_sha256'] == binding['request_sha256']
                and plan['project_id'] == row['project_id'] and plan['request_sha256'] == row['request_sha256'],
                'animation_method_trash_stop_unproven', 409)
            require(all(n['stop_proven'] is True and n['status'] in {'completed', 'failed', 'blocked', 'cancelled'}
                for n in plan['nodes'].values()), 'animation_method_trash_stop_unproven', 409)
            task = plan['nodes']['manim'].get('task')
            if task is None:
                require(plan['nodes']['manim']['status'] != 'completed', 'animation_method_trash_stop_unproven', 409)
                return
            old = self.coordinator_record(coordinator, owner, task['task_id'])
        else:
            old = self.coordinator_record(coordinator, owner, run['id'])
            if old is None and not run['operations'] and run['state'] == 'cancelled':
                return  # Existing intact ledgers prove that no resource was claimed.
        self.manim_stopped(coordinator, old, deadline)

    def manim_stopped(self, coordinator, old, deadline):
        require(old is not None and old.get('state') in TERMINAL,
                'animation_method_trash_stop_unproven', 409)
        if old.get('recipe_claimed'):
            job = self._read_stop(lambda: coordinator.gateway.lookup(old['scope']['operation_id']), deadline)
            require(type(job) is dict and job.get('stop_proven') is True
                and job.get('state') in _AGENT_STOPPED and job.get('job_id') == old.get('job_id'),
                'animation_method_trash_stop_unproven', 409)
        if 'plan' in old:
            result = self._read_stop(lambda: coordinator.executor.lookup(**old['scope']), deadline)
            require(result.get('stop_proven') is True and result.get('status') in TERMINAL
                and result.get('scope') == old['scope'], 'animation_method_trash_stop_unproven', 409)
        else:
            require(old['state'] != 'completed', 'animation_method_trash_stop_unproven', 409)

    def require_demonstrations_visible(self, owner, demo_ids):
        with self.database() as db:
            state = self.state(db, owner)
            demos = {d['id']: d for d in state['demos']}
            for identity in demo_ids:
                demo = demos.get(identity)
                require(demo is not None, 'animation_method_demonstration_not_found', 404)
                origin = demo.get('provenance', {}).get('commission_id')
                require(origin is None or origin not in state['hidden'] | state['legacy_hidden'],
                    'animation_method_evidence_trashed', 409)

    def require_validation_visible(self, owner, validation_id):
        with self.database() as db:
            rows = self.documents(db, 'methods.animation_method_validations', owner)
            row = next((r for r in rows if r['id'] == validation_id), None)
            require(row is not None, 'animation_method_validation_not_found', 404)
        self.require_demonstrations_visible(owner, [row[k] for k in
            ('demonstration_version_id', 'baseline_demonstration_version_id') if row.get(k)])

    def require_publication_visible(self, owner, method_version_id):
        validations = self.service.methods.list_validations(owner, method_version_id=method_version_id)
        ready = self.service.methods.publication_readiness(owner, method_version_id)
        identities = set(ready['reference_validation_ids'] + ready['transfer_validation_ids'])
        selected = [v for v in validations if v['id'] in identities]
        self.require_demonstrations_visible(owner, [v[k] for v in selected for k in
            ('demonstration_version_id', 'baseline_demonstration_version_id') if v.get(k)])

    @staticmethod
    def request(body, fields):
        require(type(body) is dict and set(body) == set(fields) | {'request_id'})
        identity = body['request_id']
        require(type(identity) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', identity))

    @staticmethod
    def replay(db, owner, body, request):
        row = db.execute('SELECT request_sha256,result FROM animation_trash_requests '
            'WHERE owner=? AND request_id=?', (owner, body['request_id'])).fetchone()
        if row is not None:
            require(row[0] == digest(request), 'animation_method_trash_idempotency_conflict', 409)
            return json.loads(row[1])

    @staticmethod
    def record(db, owner, body, request, result):
        db.execute('INSERT INTO animation_trash_requests VALUES(?,?,?,?)',
            (owner, body['request_id'], digest(request), canonical_bytes(result).decode()))

    @staticmethod
    def entry_view(entry, *, restored=False):
        return dict(id=entry['id'], scope=entry['scope'], target_id=entry['target_id'], title=entry['title'],
            request_id=entry['request_id'], member_ids=[r['id'] for r in entry['members']],
            member_count=len(entry['members']), deleted_at=entry['deleted_at'],
            status='restored' if restored else 'trashed', restore=dict(allowed=not restored, reason_codes=[]))

    def catalogue(self, owner):
        with self.database() as db:
            state = self.state(db, owner)
            return dict(schema='video.animation-trash.v1', items=[self.entry_view(e) for e in reversed(state['entries'])
                if e['id'] not in state['restorations']])

    def delete(self, owner, scope, target_id, body):
        self.request(body, {'expected_snapshot_sha256'})
        require(scope in {'commission', 'effect'})
        request = dict(action='trash', scope=scope, target_id=target_id, **body)
        with self.mutation(owner):
            with self.database() as db:
                prior = self.replay(db, owner, body, request)
                if prior is not None:
                    return prior
                state = self.state(db, owner)
                eligibility, members, title, snap = self.eligibility(db, state, scope, target_id)
                require(eligibility['snapshot_sha256'] == body['expected_snapshot_sha256'],
                    'animation_method_trash_snapshot_changed', 409)
                reasons = eligibility['reason_codes']
                require(not reasons, 'animation_method_trash_' + ('protected' if 'protected' in reasons
                    else 'uncertain' if 'uncertain' in reasons else 'active' if 'active' in reasons else 'already_trashed'), 409)
            self.stopped(owner, members)
            with self.database(write=True) as db:
                prior = self.replay(db, owner, body, request)
                if prior is not None:
                    return prior
                state = self.state(db, owner)
                current, members, title, snap = self.eligibility(db, state, scope, target_id)
                require(current['allowed'] and current['snapshot_sha256'] == body['expected_snapshot_sha256'],
                    'animation_method_trash_snapshot_changed', 409)
                entry = dict(schema='video.animation-trash-entry.v1', id=str(uuid4()), owner=owner,
                    title=title, request_id=body['request_id'], deleted_at=self.service.clock(), **snap)
                entry['sha256'] = digest(entry)
                db.execute('INSERT INTO animation_trash_entries VALUES(?,?,?)',
                    (entry['id'], owner, canonical_bytes(entry).decode()))
                result = dict(schema='video.animation-trash-operation.v1', action='trash', entry=self.entry_view(entry))
                self.record(db, owner, body, request, result)
                return result

    def restore(self, owner, entry_id, body):
        self.request(body, set())
        request = dict(action='restore', entry_id=entry_id, **body)
        with self.mutation(owner), self.database(write=True) as db:
            prior = self.replay(db, owner, body, request)
            if prior is not None:
                return prior
            state = self.state(db, owner)
            entry = next((e for e in state['entries'] if e['id'] == entry_id), None)
            require(entry is not None, 'animation_method_trash_not_found', 404)
            if entry_id not in state['restorations']:
                record = dict(schema='video.animation-trash-restoration.v1', entry_id=entry_id,
                    owner=owner, request_id=body['request_id'], restored_at=self.service.clock(),
                    entry_sha256=entry['sha256'])
                db.execute('INSERT INTO animation_trash_restorations VALUES(?,?,?)',
                    (entry_id, owner, canonical_bytes(record).decode()))
            result = dict(schema='video.animation-trash-operation.v1', action='restore',
                          entry=self.entry_view(entry, restored=True))
            self.record(db, owner, body, request, result)
            return result
