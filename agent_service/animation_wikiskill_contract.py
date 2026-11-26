"""Whole-library compilation contracts, separate from method acceptance.

Source records are immutable evidence, not instructions or new visual approval.
The Agent authors all Skill text. The host only verifies bindings and computes
the existing Skill package metadata; it never substitutes a template library.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import time
from uuid import UUID, uuid4

from .animation_skills_contract import (
    MAX_CATALOG_SKILLS, MAX_BUNDLE_BYTES, MAX_BUNDLE_FILES,
    build_skill_package, validate_skill_package,
)
from .contracts import ServiceError, canonical_bytes, digest

SNAPSHOT_SCHEMA = 'video.animation-wikiskill-snapshot.v1'
INPUT_SCHEMA = 'video.animation-wikiskill-input.v1'
RESULT_SCHEMA = 'video.animation-wikiskill-result.v1'
RELEASE_SCHEMA = 'video.animation-wikiskill-release.v1'
INPUT_NAME = 'animation-wikiskill-input.json'
RESULT_NAME = 'animation-wikiskill-result.json'
INPUT_MAX_BYTES = 1024 * 1024
RECORD_MAX_BYTES = 4 * 1024 * 1024
SNAPSHOT_MAX_BYTES = 30 * 1024 * 1024
INPUT_TOTAL_MAX_BYTES = 32 * 1024 * 1024
CAPACITY_SNAPSHOT_MAX_BYTES = 63 * 1024 * 1024
CAPACITY_INPUT_TOTAL_MAX_BYTES = 64 * 1024 * 1024
OUTPUT_MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 2047
EVIDENCE_SCOPE = 'frozen_records_only_no_new_media_validation'
COMPILATION_LIMITATION = 'No new video, motion or audio validation was performed by this compilation.'
_SHA = re.compile(r'^[0-9a-f]{64}$')


def require(condition, code='animation_wikiskill_invalid', status=422):
    if not condition:
        raise ServiceError(code, status)


def compilation_limits(compilation_revision='1'):
    require(type(compilation_revision) is str and compilation_revision in {'1', '2', '3'},
        'animation_wikiskill_compilation_revision_invalid')
    return (SNAPSHOT_MAX_BYTES, INPUT_TOTAL_MAX_BYTES) if compilation_revision == '1' else (
        CAPACITY_SNAPSHOT_MAX_BYTES, CAPACITY_INPUT_TOTAL_MAX_BYTES)


def compilation_native_max_seconds(compilation_revision='1'):
    """Only the explicit third compiler contract receives the hour budget."""
    compilation_limits(compilation_revision)
    return 3600 if compilation_revision == '3' else 900


def text(value, maximum=400, *, empty=False):
    require(type(value) is str and (empty or bool(value.strip())) and len(value) <= maximum
        and not any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127
                    or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def identity(value):
    text(value)
    require(not any(ord(c) < 32 for c in value))
    return value


def checksum(value):
    require(type(value) is str and _SHA.fullmatch(value))
    return value


def uuid_identity(value):
    try:
        require(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise ServiceError('animation_wikiskill_invalid_request_id', 422) from None
    return value


def _bytes(value):
    try:
        return canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ServiceError('animation_wikiskill_json_invalid', 422) from None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _record(value):
    require(type(value) is dict and set(value) == {'record_id', 'kind', 'content', 'sha256'})
    identity(value['record_id'])
    identity(value['kind'])
    require(type(value['content']) is dict, 'animation_wikiskill_record_content_invalid')
    require(checksum(value['sha256']) == hashlib.sha256(_bytes(value['content'])).hexdigest(),
            'animation_wikiskill_record_changed', 409)
    require(len(_bytes(value)) <= RECORD_MAX_BYTES, 'animation_wikiskill_record_too_large', 413)
    return deepcopy(value)


def make_snapshot(owner, records, *, captured_at=None, snapshot_id=None, compilation_revision='1'):
    """Capture caller-supplied records verbatim; never truncate an oversized set.

    A record may omit sha256, which is then computed from its actual content.
    A supplied checksum is always verified rather than silently repaired.
    """
    snapshot_limit, _ = compilation_limits(compilation_revision)
    identity(owner)
    require(type(records) is list and 1 <= len(records) <= MAX_RECORDS,
            'animation_wikiskill_snapshot_record_limit', 413 if records else 422)
    frozen = []
    for source in records:
        require(type(source) is dict)
        row = deepcopy(source)
        if 'sha256' not in row and 'content' in row:
            row['sha256'] = hashlib.sha256(_bytes(row['content'])).hexdigest()
        frozen.append(_record(row))
    require(len({row['record_id'] for row in frozen}) == len(frozen),
            'animation_wikiskill_duplicate_record', 409)
    frozen.sort(key=lambda row: row['record_id'])
    captured_at = time.time() if captured_at is None else captured_at
    require(_number(captured_at))
    snapshot_id = str(uuid4()) if snapshot_id is None else uuid_identity(snapshot_id)
    value = dict(schema=SNAPSHOT_SCHEMA, id=snapshot_id, owner=owner,
        captured_at=captured_at, records=frozen)
    value['sha256'] = digest(value)
    require(len(_bytes(value)) <= snapshot_limit, 'animation_wikiskill_snapshot_too_large', 413)
    return value


def validate_snapshot(value, *, compilation_revision='1'):
    require(type(value) is dict and set(value) ==
        {'schema', 'id', 'owner', 'captured_at', 'records', 'sha256'} and value['schema'] == SNAPSHOT_SCHEMA)
    expected = make_snapshot(value['owner'], value['records'], captured_at=value['captured_at'],
                             snapshot_id=value['id'], compilation_revision=compilation_revision)
    require(value == expected, 'animation_wikiskill_snapshot_changed', 409)
    return expected


def build_input(snapshot, *, note='', compilation_revision='1'):
    snapshot = validate_snapshot(snapshot, compilation_revision=compilation_revision)
    text(note, 4000, empty=True)
    records = []
    for index, row in enumerate(snapshot['records']):
        raw = _bytes(row)
        records.append(dict(record_id=row['record_id'], kind=row['kind'], sha256=row['sha256'],
            file_name=f'records/{index:06d}.json', file_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
    value = dict(schema=INPUT_SCHEMA,
        snapshot={key: deepcopy(item) for key, item in snapshot.items() if key != 'records'},
        records=records, note=note, evidence_scope=EVIDENCE_SCOPE)
    return validate_input(value, compilation_revision=compilation_revision)


def validate_input(value, *, compilation_revision='1'):
    _, input_total_limit = compilation_limits(compilation_revision)
    require(type(value) is dict and set(value) == {'schema', 'snapshot', 'records', 'note', 'evidence_scope'}
        and value['schema'] == INPUT_SCHEMA and value['evidence_scope'] == EVIDENCE_SCOPE)
    head = value['snapshot']
    require(type(head) is dict and set(head) == {'schema', 'id', 'owner', 'captured_at', 'sha256'}
        and head['schema'] == SNAPSHOT_SCHEMA and _number(head['captured_at']))
    uuid_identity(head['id'])
    identity(head['owner'])
    checksum(head['sha256'])
    text(value['note'], 4000, empty=True)
    rows = value['records']
    require(type(rows) is list and 1 <= len(rows) <= MAX_RECORDS)
    for index, row in enumerate(rows):
        require(type(row) is dict and set(row) ==
            {'record_id', 'kind', 'sha256', 'file_name', 'file_sha256', 'bytes'})
        identity(row['record_id'])
        identity(row['kind'])
        checksum(row['sha256'])
        checksum(row['file_sha256'])
        require(row['file_name'] == f'records/{index:06d}.json'
            and type(row['bytes']) is int and 1 <= row['bytes'] <= RECORD_MAX_BYTES)
    require(len({row['record_id'] for row in rows}) == len(rows)
        and rows == sorted(rows, key=lambda row: row['record_id']))
    raw = _bytes(value)
    require(len(raw) <= INPUT_MAX_BYTES, 'animation_wikiskill_manifest_too_large', 413)
    require(len(raw) + sum(row['bytes'] for row in rows) <= input_total_limit,
            'animation_wikiskill_inputs_too_large', 413)
    return deepcopy(value)


def input_files(snapshot, document=None, *, compilation_revision='1'):
    snapshot = validate_snapshot(snapshot, compilation_revision=compilation_revision)
    document = (build_input(snapshot, compilation_revision=compilation_revision) if document is None else
        validate_input(document, compilation_revision=compilation_revision))
    require(build_input(snapshot, note=document['note'], compilation_revision=compilation_revision) == document,
            'animation_wikiskill_input_binding_mismatch', 409)
    result = {INPUT_NAME: _bytes(document)}
    result.update({entry['file_name']: _bytes(row)
        for entry, row in zip(document['records'], snapshot['records'])})
    return result


def validate_input_files(document, files, *, compilation_revision='1'):
    document = validate_input(document, compilation_revision=compilation_revision)
    require(type(files) is dict and set(files) == {INPUT_NAME} | {row['file_name'] for row in document['records']},
            'animation_wikiskill_unexpected_input')
    require(files[INPUT_NAME] == _bytes(document), 'animation_wikiskill_input_binding_mismatch', 409)
    records = []
    for entry in document['records']:
        raw = files[entry['file_name']]
        require(type(raw) is bytes and len(raw) == entry['bytes']
            and hashlib.sha256(raw).hexdigest() == entry['file_sha256'],
            'animation_wikiskill_input_binding_mismatch', 409)
        try:
            record = json.loads(raw)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('animation_wikiskill_json_invalid', 422) from None
        require(raw == _bytes(record), 'animation_wikiskill_input_not_canonical')
        record = _record(record)
        require(all(record[key] == entry[key] for key in ('record_id', 'kind', 'sha256')),
                'animation_wikiskill_input_binding_mismatch', 409)
        records.append(record)
    head = document['snapshot']
    snapshot = make_snapshot(head['owner'], records, captured_at=head['captured_at'], snapshot_id=head['id'],
        compilation_revision=compilation_revision)
    require(snapshot['sha256'] == head['sha256'], 'animation_wikiskill_snapshot_changed', 409)
    return snapshot


def normalize_agent_result(value):
    """Compute hashes only; all prose, hierarchy and evidence links stay authored."""
    result = deepcopy(value)
    require(type(result) is dict and type(result.get('packages')) is list)
    draft = {'skill_id', 'version', 'kind', 'files', 'runtime_requirements'}
    for index, package in enumerate(result['packages']):
        require(type(package) is dict)
        if set(package) == draft:
            result['packages'][index] = build_skill_package(**package)
    return result


def validate_result(value, input_sha256, input_document=None, *, compilation_revision='1'):
    require(input_document is not None, 'animation_wikiskill_input_required')
    context = validate_input(input_document, compilation_revision=compilation_revision)
    require(type(value) is dict and set(value) == {'schema', 'input_sha256', 'snapshot_sha256',
        'evidence_scope', 'summary', 'packages', 'index', 'exclusions', 'limitations'}
        and value['schema'] == RESULT_SCHEMA and value['input_sha256'] == input_sha256 == digest(context)
        and value['snapshot_sha256'] == context['snapshot']['sha256']
        and value['evidence_scope'] == EVIDENCE_SCOPE, 'animation_wikiskill_result_binding_mismatch')
    text(value['summary'], 16000)
    packages = value['packages']
    require(type(packages) is list and 1 <= len(packages) <= MAX_CATALOG_SKILLS)
    packages = [validate_skill_package(row) for row in packages]
    by_id = {row['skill_id']: row for row in packages}
    require(len(by_id) == len(packages) and len({row['name'] for row in packages}) == len(packages),
            'animation_wikiskill_duplicate_skill', 409)
    require(all(row['version'] == 1 for row in packages), 'animation_wikiskill_package_version_invalid')
    require(sum(len(text.encode('utf-8')) for row in packages for text in row['files'].values())
            <= MAX_BUNDLE_BYTES
        and sum(len(row['files']) for row in packages) + len(packages) + 1 <= MAX_BUNDLE_FILES,
            'animation_wikiskill_bundle_too_large', 413)
    rows = value['index']
    require(type(rows) is list and len(rows) == len(packages))
    available = {row['record_id'] for row in context['records']}
    parents, used = {}, set()
    for row in rows:
        require(type(row) is dict and set(row) == {'skill_id', 'parent_skill_id', 'source_record_ids'}
            and row['skill_id'] in by_id and row['skill_id'] not in parents)
        parent = row['parent_skill_id']
        require(parent is None or type(parent) is str and parent in by_id and parent != row['skill_id'])
        sources = row['source_record_ids']
        require(type(sources) is list and bool(sources) and all(type(item) is str for item in sources)
            and len(set(sources)) == len(sources) and set(sources) <= available,
            'animation_wikiskill_evidence_not_supplied')
        used.update(sources)
        parents[row['skill_id']] = parent
    roots = [key for key, parent in parents.items() if parent is None]
    require(len(roots) == 1 and by_id[roots[0]]['kind'] == 'foundation',
            'animation_wikiskill_root_required')
    for key in parents:
        seen = set()
        while key is not None:
            require(key not in seen, 'animation_wikiskill_hierarchy_cycle')
            seen.add(key)
            key = parents[key]
    excluded = value['exclusions']
    require(type(excluded) is list and len(excluded) <= len(available))
    excluded_ids = set()
    for row in excluded:
        require(type(row) is dict and set(row) == {'record_id', 'reason'}
            and type(row['record_id']) is str and row['record_id'] in available
            and row['record_id'] not in excluded_ids)
        text(row['reason'], 4000)
        excluded_ids.add(row['record_id'])
    require(not used & excluded_ids and used | excluded_ids == available,
            'animation_wikiskill_snapshot_coverage_incomplete')
    limitations = value['limitations']
    require(type(limitations) is list and 1 <= len(limitations) <= 128
        and COMPILATION_LIMITATION in limitations, 'animation_wikiskill_observation_scope_required')
    for item in limitations:
        text(item, 4000)
    require(len(_bytes(value)) <= OUTPUT_MAX_BYTES, 'animation_wikiskill_result_too_large', 413)
    return deepcopy(value)


def validate_release(value):
    fields = {'schema', 'id', 'owner', 'sequence', 'job_id', 'snapshot_id', 'snapshot_sha256',
        'input_sha256', 'created_at', 'summary', 'packages', 'index', 'exclusions',
        'limitations', 'evidence_scope', 'sha256'}
    require(type(value) is dict and set(value) == fields and value['schema'] == RELEASE_SCHEMA)
    uuid_identity(value['id'])
    uuid_identity(value['job_id'])
    uuid_identity(value['snapshot_id'])
    identity(value['owner'])
    require(type(value['sequence']) is int and value['sequence'] >= 1 and _number(value['created_at'])
        and value['evidence_scope'] == EVIDENCE_SCOPE)
    for key in ('snapshot_sha256', 'input_sha256', 'sha256'):
        checksum(value[key])
    require(digest({key: item for key, item in value.items() if key != 'sha256'}) == value['sha256'],
            'animation_wikiskill_release_changed', 409)
    text(value['summary'], 16000)
    require(type(value['packages']) is list and 1 <= len(value['packages']) <= MAX_CATALOG_SKILLS)
    packages = {}
    for row in value['packages']:
        validate_skill_package(row)
        require(row['skill_id'] not in packages and row['version'] == 1,
                'animation_wikiskill_duplicate_skill', 409)
        packages[row['skill_id']] = row
    require(len({row['name'] for row in packages.values()}) == len(packages))
    require(type(value['index']) is list and len(value['index']) == len(packages))
    parents, used = {}, set()
    for row in value['index']:
        require(type(row) is dict and set(row) == {'skill_id', 'parent_skill_id', 'source_record_ids'}
            and type(row['skill_id']) is str and row['skill_id'] in packages and row['skill_id'] not in parents)
        parent = row['parent_skill_id']
        require(parent is None or type(parent) is str and parent in packages and parent != row['skill_id'])
        require(type(row['source_record_ids']) is list and bool(row['source_record_ids']))
        for record_id in row['source_record_ids']:
            identity(record_id)
        require(len(set(row['source_record_ids'])) == len(row['source_record_ids']))
        parents[row['skill_id']] = parent
        used.update(row['source_record_ids'])
    roots = [key for key, parent in parents.items() if parent is None]
    require(len(roots) == 1 and packages[roots[0]]['kind'] == 'foundation',
            'animation_wikiskill_root_required')
    for key in parents:
        seen = set()
        while key is not None:
            require(key not in seen, 'animation_wikiskill_hierarchy_cycle')
            seen.add(key)
            key = parents[key]
    require(type(value['exclusions']) is list and len(value['exclusions']) <= MAX_RECORDS)
    excluded = set()
    for row in value['exclusions']:
        require(type(row) is dict and set(row) == {'record_id', 'reason'})
        identity(row['record_id'])
        text(row['reason'], 4000)
        require(row['record_id'] not in excluded and row['record_id'] not in used)
        excluded.add(row['record_id'])
    require(type(value['limitations']) is list and 1 <= len(value['limitations']) <= 128
        and COMPILATION_LIMITATION in value['limitations'], 'animation_wikiskill_observation_scope_required')
    for item in value['limitations']:
        text(item, 4000)
    require(len(_bytes(value)) <= OUTPUT_MAX_BYTES + 65536,
            'animation_wikiskill_release_too_large', 413)
    return deepcopy(value)
