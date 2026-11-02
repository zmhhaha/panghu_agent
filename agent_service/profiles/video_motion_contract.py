"""Framework-neutral, bounded data contracts for semantic motion plans.

No renderer, business store, executable source or audio timing is imported here.
The same validator protects stopped Agent output and the trusted compiler input.
"""
from __future__ import annotations

from copy import deepcopy
import re

from ..contracts import ServiceError, canonical_bytes


# The published Agent contract retains its original template vocabulary. Local
# experiments must opt in explicitly; registering slots cannot expand defaults.
DEFAULT_TEMPLATES = frozenset({'flow', 'compare', 'focus'})
DEMONSTRATION_TEMPLATES = frozenset({'demonstration'})
TEMPLATE_SLOTS = {
    'flow': {'card': frozenset({'source', 'queue', 'worker', 'note'}),
             'token': frozenset({'token_source', 'token_queue', 'token_worker'}),
             'label': frozenset({'caption'})},
    'compare': {'card': frozenset({'before', 'after', 'note'}),
                'token': frozenset({'token_before', 'token_after'}),
                'label': frozenset({'caption'})},
    'focus': {'card': frozenset({'top', 'left', 'center', 'right', 'note'}),
              'token': frozenset({'token_top', 'token_left', 'token_center', 'token_right'}),
              'label': frozenset({'caption'})},
    'demonstration': {
        'document': frozenset({'log_center', 'log_left'}),
        'record': frozenset({'answer_center', 'answer_left', 'answer_right'}),
        'attempt': frozenset({'attempt_center', 'attempt_left'}),
        'terminal': frozenset({'context_right'}),
        'label': frozenset({'result_left', 'result_right'}),
    },
}
PLAN_MAX_BYTES = 128 * 1024
BEATS_MAX_BYTES = 256 * 1024
IDENTIFIER = re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')
BEAT_IDENTIFIER = re.compile(r'beat_[0-9a-f]{16}\Z')


def _require(value, code='invalid_motion_plan'):
    if not value:
        raise ServiceError(code, 422)


def _fields(value, keys, code='invalid_motion_plan'):
    _require(type(value) is dict and set(value) == set(keys), code)


def _text(value, maximum, *, empty=False, code='invalid_motion_plan'):
    _require(type(value) is str and len(value) <= maximum
             and (empty or bool(value.strip())) and not any(
                 (ord(c) < 32 and c not in '\t\r\n') or ord(c) == 127
                 or 0xD800 <= ord(c) <= 0xDFFF for c in value), code)


def validate_beats(beats):
    """Validate exact source/spoken spans; IDs do not depend on audio identity."""
    code = 'invalid_motion_beats'
    _fields(beats, {'schema', 'source_text', 'spoken_text', 'segments'}, code)
    _require(beats['schema'] == 'video.beats.v1', code)
    for key in ('source_text', 'spoken_text'):
        _text(beats[key], 10000, code=code)
    rows = beats['segments']
    _require(type(rows) is list and 1 <= len(rows) <= 24, code)
    identities, source_end, spoken_end = set(), 0, 0
    for row in rows:
        _fields(row, {'id', 'source_start', 'source_end', 'spoken_start', 'spoken_end',
                      'source_text', 'spoken_text'}, code)
        identity = row['id']
        _require(type(identity) is str and BEAT_IDENTIFIER.fullmatch(identity)
                 and identity not in identities, code)
        identities.add(identity)
        for prefix, previous in (('source', source_end), ('spoken', spoken_end)):
            start, end = row[prefix + '_start'], row[prefix + '_end']
            _require(type(start) is int and type(end) is int
                     and previous == start < end <= len(beats[prefix + '_text']), code)
            _text(row[prefix + '_text'], 10000, code=code)
            _require(beats[prefix + '_text'][start:end] == row[prefix + '_text'], code)
        source_end, spoken_end = row['source_end'], row['spoken_end']
    _require(source_end == len(beats['source_text']) and spoken_end == len(beats['spoken_text']), code)
    _require(len(canonical_bytes(beats)) <= BEATS_MAX_BYTES, code)
    return deepcopy(beats)


def validate_motion_plan(plan, beats, *, allowed_templates=None):
    """Validate bounded data; experiments never widen the Agent defaults.

    ``allowed_templates`` is a trusted caller option, never a model field. The
    demonstration slots of each movable kind have equal compiler-owned sizes;
    they do not enable arbitrary coordinates, scaling or cross-kind moves.
    """
    if allowed_templates is None:
        allowed_templates = DEFAULT_TEMPLATES
    _require(type(allowed_templates) in (tuple, list, set, frozenset)
             and 1 <= len(allowed_templates) <= len(TEMPLATE_SLOTS)
             and all(type(name) is str and name in TEMPLATE_SLOTS for name in allowed_templates))
    if type(beats) is dict:
        rows = validate_beats(beats)['segments']
    else:
        _require(type(beats) is list and 1 <= len(beats) <= 24)
        rows = beats
    beat_ids = [row.get('id') for row in rows if type(row) is dict]
    _require(len(beat_ids) == len(rows)
             and all(type(item) is str and BEAT_IDENTIFIER.fullmatch(item) for item in beat_ids)
             and len(set(beat_ids)) == len(beat_ids))
    _fields(plan, {'schema', 'template', 'title', 'takeaway', 'elements', 'relations', 'actions'})
    _require(plan['schema'] == 'video.motion-plan.v1'
             and type(plan['template']) is str and plan['template'] in allowed_templates)
    demonstration = plan['template'] == 'demonstration'
    for key, maximum in (('title', 50), ('takeaway', 90)):
        _text(plan[key], maximum)
    slots = TEMPLATE_SLOTS[plan['template']]
    elements, relations, actions = plan['elements'], plan['relations'], plan['actions']
    _require(type(elements) is list and 1 <= len(elements) <= 9
             and type(relations) is list and len(relations) <= 12
             and type(actions) is list and 1 <= len(actions) <= 48)
    _require(not demonstration or not relations)
    by_id, occupied = {}, set()
    for item in elements:
        _fields(item, {'id', 'kind', 'label', 'detail', 'slot'})
        identity, kind, slot = item['id'], item['kind'], item['slot']
        _require(type(identity) is str and IDENTIFIER.fullmatch(identity) and identity not in by_id
                 and type(kind) is str and kind in slots and type(slot) is str
                 and slot in slots[kind] and slot not in occupied)
        _text(item['label'], 28)
        _text(item['detail'], 64, empty=True)
        by_id[identity] = item
        occupied.add(slot)
    relation_ids = set()
    for item in relations:
        _fields(item, {'id', 'source', 'target', 'label'})
        identity, source, target = item['id'], item['source'], item['target']
        _require(type(identity) is str and IDENTIFIER.fullmatch(identity)
                 and identity not in by_id and identity not in relation_ids
                 and type(source) is str and source in by_id
                 and type(target) is str and target in by_id and source != target)
        _text(item['label'], 18, empty=True)
        relation_ids.add(identity)
    conflicts = set()
    properties = {'reveal': 'visibility', 'hide': 'visibility', 'highlight': 'highlight',
                  'move': 'position', 'draw': 'stroke'}
    if demonstration:
        properties = {kind: properties[kind] for kind in ('reveal', 'hide', 'move')}
    for item in actions:
        _fields(item, {'beat_id', 'target', 'kind', 'placement', 'pace', 'to_slot'})
        beat, target, kind = item['beat_id'], item['target'], item['kind']
        _require(type(beat) is str and beat in beat_ids and type(target) is str
                 and type(kind) is str and kind in properties
                 and type(item['placement']) is str and item['placement'] in {'start', 'middle', 'end'}
                 and type(item['pace']) is str and item['pace'] in {'quick', 'normal', 'slow'})
        if kind == 'draw':
            _require(target in relation_ids)
        else:
            _require(target in by_id)
        if kind == 'move':
            target_kind = by_id[target]['kind']
            movable_kinds = {'document', 'record', 'attempt'} if demonstration else {'token'}
            _require(target_kind in movable_kinds and type(item['to_slot']) is str
                     and item['to_slot'] in slots[target_kind])
        else:
            _require(item['to_slot'] is None)
        conflict = (beat, target, properties[kind])
        _require(conflict not in conflicts)
        conflicts.add(conflict)
    _require(len(canonical_bytes(plan)) <= PLAN_MAX_BYTES)
    return deepcopy(plan)
