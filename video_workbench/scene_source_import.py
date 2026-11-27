"""One independently budgeted same-content run; parent records remain immutable."""
from copy import deepcopy
import math
import re

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .scene_space_budget import create_usage, usage_status, validate_usage

SCHEMA='video.scene-source-import-grant.v1'
MODEL_LIMITS=dict(max_requests=8,max_output_tokens=8192)
LIMITS=dict(author_jobs=0,review_jobs=1,maximum_lineage_distinct_jobs=7,
    provider_requests=8,max_output_tokens_per_request=8192,admission_reserve_tokens=262144,
    total_input_output_token_hard_cap=None,render_seconds=600,repairs=0,exports=1,window_seconds=3600)
ACCEPTANCES=('accept_parent_cumulative_usage_unknown','accept_original_token_cap_unproven',
    'allow_export_with_parent_unknown')
REQUEST_FIELDS={'expected_revision','expected_source_run_sha256','expected_scene_sha256',
    'expected_task_sha256','expected_parent_usage_sha256','expected_author_job_id',
    'expected_author_input_sha256','expected_author_result_sha256','expected_source_sha256',
    'expected_plan_sha256','expected_parent_configuration_sha256','expected_configuration_sha256',
    'expected_usage_evidence_sha256','expected_lineage_job_ids','authorization_sha256',
    'execution_deadline_at',*ACCEPTANCES}

def require(value,code='scene_source_import_invalid',status=409):
    if not value:raise ServiceError(code,status)

def identity(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}',value) is not None

def validate_request(value):
    require(type(value) is dict and set(value)==REQUEST_FIELDS
        and type(value['expected_revision']) is int and value['expected_revision']>=1
        and identity(value['expected_author_job_id'])
        and all(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}',value[key])
            for key in REQUEST_FIELDS if key.endswith('_sha256'))
        and type(value['execution_deadline_at']) in (int,float)
        and math.isfinite(value['execution_deadline_at']) and value['execution_deadline_at']>0
        and all(value[key] is True for key in ACCEPTANCES), 'invalid_scene_source_import_request',422)
    ids=value['expected_lineage_job_ids']
    require(type(ids) is list and 1<=len(ids)<=6 and all(identity(item) for item in ids)
        and ids==sorted(set(ids)),'scene_source_import_lineage_invalid',422)
    return deepcopy(value)

def task_document(view):
    return {key:deepcopy(value) for key,value in view.items()
        if key not in {'usage_status','continuation_usage_status','source_import_execution_status'}}

def parent_ledgers(record):
    values=[validate_usage(record['usage'])]
    if record.get('review_continuation'):
        values.append(validate_usage(record['continuation_usage']))
    return deepcopy(values)

def validate_grant(value):
    fields={'schema','parent_run_id','original_run_id','parent_run_sha256','parent_scene_sha256',
        'parent_task_sha256','parent_usage_sha256','parent_configuration_sha256','configuration_sha256',
        'author_job_id','author_input_sha256','author_result_sha256','author_usage_evidence_sha256',
        'source_sha256','plan_sha256','film_sha256','authorization_sha256','lineage_job_ids',
        'issued_at','execution_deadline_at','limits','model_limits','maximum_imports',
        'accept_parent_cumulative_usage_unknown','accept_original_token_cap_unproven',
        'allow_export_with_parent_unknown','cumulative_cost_status','new_unknown_usage_policy','sha256'}
    require(type(value) is dict and set(value)==fields and value['schema']==SCHEMA
        and canonical_bytes(value['limits'])==canonical_bytes(LIMITS)
        and canonical_bytes(value['model_limits'])==canonical_bytes(MODEL_LIMITS)
        and type(value['maximum_imports']) is int and value['maximum_imports']==1
        and all(value[key] is True for key in ACCEPTANCES)
        and value['cumulative_cost_status']=='unknown' and value['new_unknown_usage_policy']=='stop')
    require(all(identity(value[k]) for k in ('parent_run_id','original_run_id','author_job_id'))
        and all(type(value[k]) is str and re.fullmatch('[a-f0-9]{64}',value[k])
            for k in fields if k.endswith('_sha256') or k=='sha256')
        and all(type(value[k]) in (int,float) and math.isfinite(value[k]) and value[k]>0
            for k in ('issued_at','execution_deadline_at'))
        and value['issued_at']<value['execution_deadline_at']<=value['issued_at']+LIMITS['window_seconds'])
    ids=value['lineage_job_ids']
    require(type(ids) is list and 1<=len(ids)<=6 and ids==sorted(set(ids))
        and all(identity(item) for item in ids) and value['author_job_id'] in ids)
    require(value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'}),
        'scene_source_import_identity_changed')
    return deepcopy(value)

def new_usage(frozen,grant_sha256):
    usage=create_usage(frozen,lineage_id='source-import-'+grant_sha256)
    usage['baseline'].update(model_calls=0,model_tokens=0,render_seconds=0,revisions=0)
    usage['limits']=dict(model_calls=8,model_tokens=LIMITS['admission_reserve_tokens'],render_seconds=600,revisions=0)
    usage['sha256']=digest({k:v for k,v in usage.items() if k!='sha256'})
    return validate_usage(usage)

def cumulative_status(record):
    """An unknown parent total cannot become a known fresh-run total."""
    fresh=usage_status(record['usage']);result=deepcopy(fresh)
    for ledger in record['source_import_parent_ledgers']:
        old=usage_status(ledger)
        for key in ('model_calls','known_tokens_lower_bound','known_seconds_lower_bound'):
            result['used'][key]+=old['used'][key]
        if result['used']['render_seconds'] is not None and old['used']['render_seconds'] is not None:
            result['used']['render_seconds']+=old['used']['render_seconds']
        else:result['used']['render_seconds']=None
    result['used']['model_tokens']=None
    result.update(status='usage_unknown',can_start_export=False,cumulative_cost_status='unknown',
        original_total_token_cap_proven=False,export_authority='explicit_same_content_import_grant_only',
        source_import_usage_sha256=record['usage']['sha256'])
    result['remaining'].update(model_tokens=None,model_calls=0,render_seconds=0,revisions=0)
    return result
