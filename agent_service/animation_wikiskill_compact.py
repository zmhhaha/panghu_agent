"""Expand only compact evidence addresses; never author Skill prose.

The new compiler wire format uses half-open manifest-index ranges, avoiding
repeated long historical IDs. Stored releases retain the exact existing record
IDs and existing validation. The immutable input and every original source stay
available to the compiler.
"""
from copy import deepcopy

from .animation_wikiskill_contract import RESULT_SCHEMA, require, validate_input

COMPACT_RESULT_SCHEMA = 'video.animation-wikiskill-result.v2'


def expand_ranges(ranges, document):
    require(type(ranges) is list and bool(ranges), 'animation_wikiskill_ranges_invalid')
    rows, indices, previous = document['records'], [], -1
    for pair in ranges:
        require(type(pair) is list and len(pair) == 2
            and all(type(item) is int for item in pair)
            and 0 <= pair[0] < pair[1] <= len(rows) and pair[0] >= previous,
            'animation_wikiskill_ranges_invalid')
        indices.extend(range(*pair))
        previous = pair[1]
    return [rows[index]['record_id'] for index in indices]


def expand_compact_result(value, document):
    document = validate_input(document)
    require(type(value) is dict and value.get('schema') == COMPACT_RESULT_SCHEMA,
            'animation_wikiskill_compact_schema_required')
    result = deepcopy(value)
    require(type(result.get('index')) is list and type(result.get('exclusions')) is list)
    result['schema'] = RESULT_SCHEMA
    for row in result['index']:
        require(type(row) is dict and set(row) == {
            'skill_id', 'parent_skill_id', 'source_record_ranges'})
        row['source_record_ids'] = expand_ranges(row.pop('source_record_ranges'), document)
    exclusions = []
    for row in result['exclusions']:
        require(type(row) is dict and set(row) == {'record_ranges', 'reason'})
        for identity in expand_ranges(row['record_ranges'], document):
            exclusions.append(dict(record_id=identity, reason=row['reason']))
    result['exclusions'] = exclusions
    return result
