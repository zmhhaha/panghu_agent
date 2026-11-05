"""Opt-in data for a small two-dimensional process; never a published profile.

This separate contract does not widen motion-plan v1 or authorize executable
model output. Coordinates, paths, state labels and glyphs belong to a compiler.
"""
from __future__ import annotations

from copy import deepcopy
import re

from ..contracts import ServiceError, canonical_bytes
from .video_motion_contract import BEAT_IDENTIFIER, validate_beats


SCHEMA = 'video.process-plan.v1'
MAX_PLAN_BYTES = 64 * 1024
IDENTIFIER = re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')
CARD_SLOTS = ('source', 'queue', 'worker')
TOKEN_SLOTS = tuple('token_' + slot for slot in CARD_SLOTS)
STATES = frozenset({'idle', 'waiting', 'running', 'completed', 'failed'})
STATE_TRANSITIONS = {
    'idle': frozenset({'waiting', 'running'}),
    'waiting': frozenset({'running'}),
    'running': frozenset({'completed', 'failed'}),
    'failed': frozenset({'waiting', 'running'}),
    'completed': frozenset(),
}
ACTION_FIELDS = {
    'reveal': {'kind', 'target', 'beat_id', 'placement', 'pace'},
    'hide': {'kind', 'target', 'beat_id', 'placement', 'pace'},
    'draw': {'kind', 'target', 'beat_id', 'placement', 'pace'},
    'transfer': {'kind', 'target', 'relation_id', 'beat_id', 'placement', 'pace'},
    'set_state': {'kind', 'target', 'state', 'beat_id', 'placement', 'pace'},
}


def _require(value, code='invalid_process_plan'):
    if not value:
        raise ServiceError(code, 422)


def _fields(value, fields):
    _require(type(value) is dict and set(value) == fields)


def _text(value, maximum, *, empty=False):
    _require(type(value) is str and len(value) <= maximum and
             (empty or bool(value.strip())) and not any(
                 ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
                 for char in value))


def validate_process_plan(plan, beats):
    """Validate one token, three cards and explicit adjacent/return relations."""
    if type(beats) is dict:
        beat_rows = validate_beats(beats)['segments']
    else:
        _require(type(beats) is list and 1 <= len(beats) <= 24)
        beat_rows = beats
    _require(all(type(row) is dict and type(row.get('id')) is str and
                 BEAT_IDENTIFIER.fullmatch(row['id']) for row in beat_rows))
    beat_ids = {row['id'] for row in beat_rows}
    _require(len(beat_ids) == len(beat_rows))
    _fields(plan, {'schema', 'template', 'title', 'takeaway', 'elements', 'relations', 'actions'})
    _require(plan['schema'] == SCHEMA and plan['template'] == 'flow')
    _text(plan['title'], 34)
    _text(plan['takeaway'], 90)
    elements, relations, actions = plan['elements'], plan['relations'], plan['actions']
    _require(type(elements) is list and len(elements) == 4)
    by_id, occupied, tokens = {}, set(), []
    for item in elements:
        _fields(item, {'id', 'kind', 'slot', 'label', 'detail'})
        identity, kind, slot = item['id'], item['kind'], item['slot']
        _require(type(identity) is str and IDENTIFIER.fullmatch(identity) and identity not in by_id)
        _require(type(kind) is str and kind in {'card', 'token'} and type(slot) is str and slot not in occupied)
        _require(slot in (CARD_SLOTS if kind == 'card' else TOKEN_SLOTS))
        _text(item['label'], 18 if kind == 'card' else 6)
        _text(item['detail'], 28 if kind == 'card' else 0, empty=True)
        by_id[identity] = item
        occupied.add(slot)
        if kind == 'token':
            tokens.append(identity)
    _require(len(tokens) == 1 and all(slot in occupied for slot in CARD_SLOTS))
    _require(type(relations) is list and 1 <= len(relations) <= 3)
    relation_ids, edges = set(), set()
    for row in relations:
        _fields(row, {'id', 'source', 'target', 'label'})
        identity, source, target = row['id'], row['source'], row['target']
        _require(type(identity) is str and IDENTIFIER.fullmatch(identity) and
                 identity not in by_id and identity not in relation_ids)
        _require(type(source) is str and type(target) is str and source in by_id and target in by_id
                 and by_id[source]['kind'] == by_id[target]['kind'] == 'card')
        pair = by_id[source]['slot'], by_id[target]['slot']
        _require(pair in {('source', 'queue'), ('queue', 'worker'), ('worker', 'queue')} and pair not in edges,
                 'process_relation_direction_invalid')
        _text(row['label'], 12, empty=True)
        relation_ids.add(identity)
        edges.add(pair)
    _require(type(actions) is list and 1 <= len(actions) <= 24)
    conflicts, verbs = set(), set()
    for row in actions:
        _require(type(row) is dict and type(row.get('kind')) is str and row['kind'] in ACTION_FIELDS)
        kind = row['kind']
        _fields(row, ACTION_FIELDS[kind])
        _require(type(row['beat_id']) is str and row['beat_id'] in beat_ids and
                 type(row['placement']) is str and row['placement'] in {'start', 'middle', 'end'} and
                 type(row['pace']) is str and row['pace'] in {'quick', 'normal', 'slow'})
        target = row['target']
        _require(type(target) is str)
        if kind == 'draw':
            _require(target in relation_ids)
        else:
            _require(target in by_id)
            if kind in {'transfer', 'set_state'}:
                _require(by_id[target]['kind'] == ('token' if kind == 'transfer' else 'card'))
        if kind == 'transfer':
            _require(type(row['relation_id']) is str and row['relation_id'] in relation_ids)
        if kind == 'set_state':
            _require(type(row['state']) is str and row['state'] in STATES)
        prop = 'visibility' if kind in {'reveal', 'hide'} else kind
        conflict = row['beat_id'], row['placement'], target, prop
        _require(conflict not in conflicts, 'conflicting_process_actions')
        conflicts.add(conflict)
        verbs.add(kind)
    _require({'transfer', 'set_state'} <= verbs, 'process_changes_required')
    # Resolve visibility order semantically, never by the supplied action-list
    # order. A repeated reveal/hide cannot demonstrate an actual change, and
    # the token must start hidden until its first explicit reveal completes.
    beat_order = {row['id']: index for index, row in enumerate(beat_rows)}
    last_visibility = {}
    visibility_actions = [row for row in actions if row['kind'] in {'reveal', 'hide'}]
    visibility_actions.sort(key=lambda row: (beat_order[row['beat_id']],
        {'start': 0, 'middle': 1, 'end': 2}[row['placement']]))
    for row in visibility_actions:
        target, kind = row['target'], row['kind']
        if target not in last_visibility and by_id[target]['kind'] == 'token':
            _require(kind == 'reveal', 'process_token_initial_visibility_invalid')
        _require(last_visibility.get(target) != kind, 'process_visibility_change_required')
        last_visibility[target] = kind
    _require(len(canonical_bytes(plan)) <= MAX_PLAN_BYTES, 'process_plan_too_large')
    return deepcopy(plan)
