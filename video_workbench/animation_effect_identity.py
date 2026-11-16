"""Explicit library identities without rewriting creative execution evidence."""
from contextlib import closing
import json
import re
import sqlite3

from agent_service.contracts import ServiceError, canonical_bytes
from .animation_method_commissions import OP_TERMINAL, TERMINAL
from .animation_method_trash import serialized_library_mutation


SCHEMA = 'video.animation-effect-separation.v1'


def require(value, code='animation_effect_separation_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _request(body):
    require(type(body) is dict and set(body) == {'request_id', 'title', 'reason'})
    require(type(body['request_id']) is str and
        re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', body['request_id']) is not None)
    for key, limit in (('title', 200), ('reason', 2000)):
        value = body[key]
        require(type(value) is str and 1 <= len(value) <= limit and bool(value.strip()))
        require(not any(ord(char) < 32 and char not in '\n\r\t' for char in value))
    require(not any(ord(char) < 32 for char in body['title']))
    return dict(body)


def _branch(state, effect, anchor_id):
    """Follow creation edges only inside the current presentation identity."""
    identities = {row['id'] for row in effect['commissions']}
    demos = {row['id']: row for row in state['demos']}
    children = {}
    for identity in identities:
        frozen = state['by_id'][identity]['frozen']
        parent = frozen.get('retry_of_commission_id') or frozen.get('base_commission_id')
        if not parent and frozen.get('role', 'reference') == 'transfer':
            demo = demos.get(frozen.get('baseline_demonstration_id'), {})
            parent = demo.get('provenance', {}).get('commission_id')
        if parent in identities:
            children.setdefault(parent, []).append(identity)
    pending, branch = [anchor_id], set()
    while pending:
        identity = pending.pop()
        if identity not in branch:
            branch.add(identity)
            pending.extend(children.get(identity, ()))
    return [state['by_id'][identity] for identity in sorted(branch)]


def _replay(record, owner, run, request):
    require(record.get('schema') == SCHEMA and record.get('owner') == owner
        and record.get('commission_id') == run['id']
        and record.get('commission_frozen_sha256') == run['frozen_sha256']
        and {key: record.get(key) for key in request} == request,
        'animation_effect_separation_conflict', 409)
    return record


def _own_sample(run):
    frozen = run['frozen']
    require(frozen.get('role', 'reference') == 'reference'
        and bool(frozen.get('base_commission_id'))
        and not frozen.get('retry_of_commission_id'),
        'animation_effect_separation_requires_continuation', 409)
    own = next((row for row in run['candidates']
        if row['id'] == run['best_candidate_id']), None)
    require(run['state'] == 'completed' and own is not None
        and own.get('commission_id') == run['id']
        and own.get('technical', {}).get('status') == 'passed',
        'animation_effect_separation_requires_completed_own_sample', 409)
    return own


class AnimationEffectIdentity:
    def __init__(self, service):
        self.service = service
        # serialized_library_mutation uses this same owner-scoped library lock.
        self.trash = service.trash
        with closing(sqlite3.connect(service.metadata_path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS effect_separations '
                '(owner TEXT NOT NULL,commission_id TEXT NOT NULL,document TEXT NOT NULL,'
                'PRIMARY KEY(owner,commission_id))')
            db.commit()

    def records(self, owner):
        with closing(sqlite3.connect(self.service.metadata_path)) as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM effect_separations WHERE owner=? ORDER BY rowid', (owner,))]

    @serialized_library_mutation
    def separate(self, owner, commission_id, body):
        request = _request(body)
        service = self.service
        # Visibility and ownership use the existing business access boundary.
        visible = service._get_visible_commission(owner, commission_id)
        with closing(sqlite3.connect(service.metadata_path)) as db:
            prior = db.execute('SELECT document FROM effect_separations '
                'WHERE owner=? AND commission_id=?', (owner, commission_id)).fetchone()
            if prior:
                return _replay(json.loads(prior[0]), owner, visible, request)
        sample = _own_sample(visible)
        # artifact() opens its own ledger transaction. Verify before taking the
        # attached write transaction, while the owner mutation lock is held.
        service.artifact(owner, commission_id, sample['id'], 'media')
        with service.trash.database(write=True) as db:
            state = service.trash.state(db, owner)
            run = state['by_id'].get(commission_id)
            require(run is not None, 'animation_method_commission_not_found', 404)
            hidden = state['hidden'] | state['legacy_hidden']
            require(commission_id not in hidden, 'animation_method_commission_not_found', 404)
            prior = db.execute('SELECT document FROM effect_separations '
                'WHERE owner=? AND commission_id=?', (owner, commission_id)).fetchone()
            if prior:
                return _replay(json.loads(prior[0]), owner, run, request)
            frozen = run['frozen']
            own = _own_sample(run)
            require(visible['frozen_sha256'] == run['frozen_sha256'] and own == sample,
                'animation_effect_separation_conflict', 409)
            effect = next((row for row in state['effects']
                if any(member['id'] == commission_id for member in row['commissions'])), None)
            require(effect is not None and effect['id'] != commission_id
                and effect['relationship_status'] == 'verified',
                'animation_effect_separation_relationship_invalid', 409)
            branch = _branch(state, effect, commission_id)
            require(not ({row['id'] for row in branch} & hidden),
                'animation_effect_separation_branch_hidden', 409)
            require(all(row['state'] in TERMINAL for row in branch)
                and all(op['status'] in OP_TERMINAL for row in branch for op in row['operations']),
                'animation_effect_separation_branch_active', 409)
            record = dict(schema=SCHEMA, owner=owner, commission_id=commission_id,
                commission_frozen_sha256=run['frozen_sha256'], source_effect_id=effect['id'],
                source_parent_id=frozen['base_commission_id'], **request, created_at=service.clock())
            db.execute('INSERT INTO effect_separations VALUES (?,?,?)',
                (owner, commission_id, canonical_bytes(record).decode()))
            return record
