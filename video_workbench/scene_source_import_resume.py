"""One pre-model successor within an existing import grant and original clock.

No new grant or budget: retain every measured event and the original unknown
ancestor ledgers. This certificate permits only the unused first review/export.
"""
from copy import deepcopy
import math
import re
from collections import Counter

from agent_service.contracts import ServiceError, digest
from .scene_source_import import validate_grant
from .scene_space_budget import usage_status, validate_usage
from .scene_review_continuation import MODEL_LIMIT_CAPABILITY
from .scene_review_canonicalization import CANONICALIZATION_REVISION

SCHEMA = 'video.scene-source-import-resume.v1'
ERROR = 'animation_frame_inputs_too_large'
REQUEST_FIELDS = {'expected_revision', 'expected_source_run_sha256', 'expected_scene_sha256',
    'expected_task_sha256', 'expected_usage_sha256', 'expected_configuration_sha256',
    'expected_artifacts_sha256', 'expected_grant_sha256', 'authorization_sha256', 'execution_deadline_at'}


def require(ok, code='scene_source_import_resume_invalid', status=409):
    if not ok:
        raise ServiceError(code, status)


def validate_request(value):
    require(type(value) is dict and set(value) == REQUEST_FIELDS
        and type(value['expected_revision']) is int and value['expected_revision'] >= 1
        and all(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key])
            for key in REQUEST_FIELDS if key.endswith('_sha256'))
        and type(value['execution_deadline_at']) in (int, float)
        and math.isfinite(value['execution_deadline_at']) and value['execution_deadline_at'] > 0,
        'invalid_scene_source_import_resume_request', 422)
    return deepcopy(value)


def operation_key(owner, parent_id, grant_sha256):
    return 'scene-source-import-resume-' + digest([owner, parent_id, grant_sha256])[:48]


def review_route_identity(route):
    value={key:deepcopy(route.get(key)) for key in ('profile','profile_revision','max_input_files',
        'max_input_bytes','max_input_total_bytes','model_request_limits')}
    value['execution_binding_sha256']=(route.get('execution_binding') or {}).get('sha256')
    require(value['profile']=='video.picture-learning-review.v2' and value['profile_revision']=='1'
        and value['max_input_files']==9 and value['max_input_bytes']==2*1024*1024
        and value['max_input_total_bytes']==18*1024*1024
        and value['model_request_limits']==MODEL_LIMIT_CAPABILITY
        and type(value['execution_binding_sha256']) is str
        and re.fullmatch('[a-f0-9]{64}',value['execution_binding_sha256']),
        'scene_source_import_resume_review_route_unavailable')
    return value


def require_native_wire_budget(input_name,files):
    """Use the same accepted native-lane bound as every classified v2 caller."""
    from .picture_review_transport_budget import require_native_wire_budget as bounded
    return bounded(input_name,files)


def require_supplied_frame_receipt(document,receipt):
    """Equal PNG hashes at different clocks still need distinct image parts."""
    evidence={row['frame_id']:row for row in document['constraints']['review_evidence']['frames']}
    expected=Counter((row['sha256'],'image/png',evidence[row['frame_id']]['model_input_bytes'])
        for row in document['observation']['frames'])
    requests={};positions=set()
    for row in receipt.get('images',[]):
        if row.get('model_request_completed') is not True:continue
        request=row.get('model_request_sha256')
        require(type(request) is str and re.fullmatch('[a-f0-9]{64}',request)
            and type(row.get('input_item_index')) is int and type(row.get('part_index')) is int,
            'scene_source_import_resume_frame_transport_unproven')
        position=(request,row['input_item_index'],row['part_index'])
        require(position not in positions,'scene_source_import_resume_frame_transport_unproven');positions.add(position)
        requests.setdefault(request,Counter())[(row.get('sha256'),row.get('media_type'),row.get('byte_length'))]+=1
    require(any(all(actual[key]>=count for key,count in expected.items()) for actual in requests.values()),
        'scene_source_import_resume_frame_transport_unproven')


def _certificate_common(value, grant, *, schema=SCHEMA, extra_fields=()):
    grant = validate_grant(grant)
    fields = {'schema', 'owner', 'project_id', 'parent_run_id', 'parent_run_sha256', 'parent_scene_sha256',
        'parent_task_sha256', 'parent_usage_sha256', 'configuration_sha256', 'artifacts_sha256',
        'source_import_grant_sha256', 'authorization_sha256', 'source_sha256', 'plan_sha256',
        'execution_deadline_at', 'issued_at', 'maximum_successors', 'request_key', 'review_route',
        'canonicalization_revision', 'review_input_sha256', 'sha256', *extra_fields}
    require(type(value) is dict and set(value) == fields and value['schema'] == schema
        and type(value['maximum_successors']) is int and value['maximum_successors'] == 1
        and all(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key])
            for key in fields if key.endswith('_sha256') or key == 'sha256')
        and all(type(value[key]) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}', value[key])
            for key in ('owner', 'project_id', 'parent_run_id'))
        and all(type(value[key]) in (int, float) and math.isfinite(value[key]) for key in ('issued_at', 'execution_deadline_at'))
        and grant['issued_at'] <= value['issued_at'] and value['execution_deadline_at'] == grant['execution_deadline_at']
        and value['source_import_grant_sha256'] == grant['sha256']
        and value['authorization_sha256'] == grant['authorization_sha256']
        and value['source_sha256'] == grant['source_sha256'] and value['plan_sha256'] == grant['plan_sha256']
        and value['configuration_sha256'] == grant['configuration_sha256']
        and value['request_key'] == operation_key(value['owner'], value['parent_run_id'], grant['sha256'])
        and value['canonicalization_revision']==CANONICALIZATION_REVISION
        and value['sha256'] == digest({key: row for key, row in value.items() if key != 'sha256'}),
        'scene_source_import_resume_identity_changed')
    route=value['review_route']
    require(type(route) is dict and set(route)=={'profile','profile_revision','max_input_files',
        'max_input_bytes','max_input_total_bytes','model_request_limits','execution_binding_sha256'}
        and review_route_identity(dict(route,execution_binding=dict(sha256=route['execution_binding_sha256'])))==route,
        'scene_source_import_resume_review_route_changed')
    return deepcopy(value)


def validate_certificate(value, grant):
    """The original certificate always inherits the exact original clock."""
    value=_certificate_common(value,grant)
    require(value['issued_at']<value['execution_deadline_at'],'scene_source_import_resume_identity_changed')
    return value


def validate_resume_certificate(value,grant):
    if type(value) is dict and value.get('schema')=='video.scene-source-import-resume-window.v1':
        from .scene_source_import_resume_window import validate_window_certificate
        return validate_window_certificate(value,grant)
    return validate_certificate(value,grant)


def effective_deadline(certificate):
    if certificate['schema']=='video.scene-source-import-resume-window.v1':
        return certificate['phase_execution_deadline_at']
    return certificate['execution_deadline_at']


def unused_execution(record):
    usage = validate_usage(record['usage']); status = usage_status(usage)
    require(status['status'] == 'within_budget' and status['used']['model_calls'] == 0
        and status['used']['model_tokens'] == 0 and status['used']['revisions'] == 0
        and status['remaining']['render_seconds'] >= 30 and not usage['history']
        and len(usage['events']) == 3
        and all(event['phase'] == 'preview' and event['outcome'] == 'completed'
            and event['model_calls'] == event['model_tokens'] == 0 and event['render_seconds'] is not None
            for event in usage['events'])
        and len(record['reservations']) == 3
        and all(row['status'] == 'settled' and row['phase'] == 'preview' for row in record['reservations'])
        and {row['id'] for row in record['reservations']} == {row['id'] for row in usage['events']},
        'scene_source_import_resume_usage_unavailable')
    return status


def require_carried_prefix(record, parent, certificate):
    require(digest(parent) == certificate['parent_task_sha256']
        and parent['usage']['sha256'] == certificate['parent_usage_sha256']
        and not parent.get('source_import_resume') and record['source_import'] == parent['source_import']
        and record['frozen'] == parent['frozen']
        and record['source_import_parent_ledgers'] == parent['source_import_parent_ledgers'],
        'scene_source_import_resume_parent_changed')
    unused_execution(parent)
    before, after = parent['usage'], record['usage']
    require(all(before[key] == after[key] for key in before if key not in {'events', 'sha256'})
        and after['events'][:len(before['events'])] == before['events']
        and record['reservations'][:len(parent['reservations'])] == parent['reservations'],
        'scene_source_import_resume_usage_prefix_changed')
    additions = after['events'][len(before['events']):]
    require(all(event['phase'] in {'review', 'export'} for event in additions)
        and all(sum(event['phase'] == phase for event in additions) <= 1 for phase in ('review', 'export')),
        'scene_source_import_resume_operation_limit')
