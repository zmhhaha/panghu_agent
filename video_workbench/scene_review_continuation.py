"""One explicit legacy-unknown exception; never a general budget reset."""
from copy import deepcopy
import math
import re

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .scene_space_budget import create_usage, usage_status, validate_usage

SCHEMA = 'video.scene-review-continuation-grant.v1'
MODEL_LIMITS = dict(max_requests=8, max_output_tokens=8192)
MODEL_LIMIT_CAPABILITY = dict(schema='agent-job-model-limits.v1',
    max_requests_scope='job_all_attempts_including_uncertain_admissions',
    max_output_tokens_scope='each_forwarded_provider_request',
    total_input_output_token_hard_cap=False)
LIMITS = dict(author_jobs=0, review_jobs=1, maximum_lineage_distinct_jobs=7,
    provider_requests=8, max_output_tokens_per_request=8192,
    admission_reserve_tokens=262144, total_input_output_token_hard_cap=None,
    render_seconds=600, repairs=0, exports=1, window_seconds=3600)


def require(ok, code='scene_review_continuation_invalid', status=409):
    if not ok:
        raise ServiceError(code, status)


def validate_grant(value):
    fields={'schema','parent_run_id','original_run_id','parent_run_sha256','parent_scene_sha256',
        'parent_task_sha256','legacy_usage_sha256','legacy_unknown_event_id','legacy_review_job_id',
        'legacy_review_usage_evidence_sha256','author_job_id','author_input_sha256',
        'author_result_sha256','author_usage_evidence_sha256','source_sha256','plan_sha256',
        'configuration_sha256','film_sha256','original_deadline_at','execution_deadline_at','issued_at',
        'limits','model_limits','lineage_job_ids','maximum_continuations','accept_legacy_usage_unknown',
        'accept_original_token_cap_unproven','allow_export_with_legacy_unknown','cumulative_cost_status',
        'original_total_token_cap_proven','new_unknown_usage_policy','sha256'}
    require(type(value) is dict and set(value)==fields and value.get('schema') == SCHEMA
        and canonical_bytes(value.get('limits')) == canonical_bytes(LIMITS)
        and canonical_bytes(value.get('model_limits')) == canonical_bytes(MODEL_LIMITS)
        and type(value.get('maximum_continuations')) is int and value['maximum_continuations'] == 1
        and value.get('cumulative_cost_status') == 'unknown'
        and value.get('original_total_token_cap_proven') is False
        and value.get('new_unknown_usage_policy') == 'stop'
        and value.get('accept_legacy_usage_unknown') is True
        and value.get('accept_original_token_cap_unproven') is True
        and value.get('allow_export_with_legacy_unknown') is True)
    identity=lambda v: type(v) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}',v)
    require(all(identity(value[k]) for k in ('parent_run_id','original_run_id','author_job_id',
            'legacy_unknown_event_id','legacy_review_job_id'))
        and value['parent_run_id']!=value['original_run_id']
        and value['legacy_unknown_event_id']=='scene-review-'+value['parent_run_id']
        and all(type(value[k]) is str and re.fullmatch('[0-9a-f]{64}',value[k])
            for k in fields if k.endswith('_sha256') or k=='sha256')
        and all(type(value[k]) in (int,float) and math.isfinite(value[k]) and value[k]>0
            for k in ('issued_at','original_deadline_at','execution_deadline_at'))
        and value['issued_at']<value['execution_deadline_at']<=value['issued_at']+LIMITS['window_seconds'])
    ids=value['lineage_job_ids']
    require(type(ids) is list and 3<=len(ids)<=6 and all(identity(v) for v in ids)
        and ids==sorted(set(ids)) and {value['author_job_id'],value['legacy_review_job_id']}<=set(ids)
        and value['author_job_id']!=value['legacy_review_job_id'])
    require(value.get('sha256') == digest({k: v for k, v in value.items() if k != 'sha256'}),
        'scene_review_continuation_identity_changed')
    return deepcopy(value)


def new_usage(frozen, *, grant_sha256):
    # This is a separately named grant ledger. The original ledger is retained
    # verbatim, including its unknown event; it is never used to infer this cap.
    value = create_usage(frozen, lineage_id='continuation-' + grant_sha256)
    value['baseline'].update(model_calls=0, model_tokens=0, render_seconds=0, revisions=0)
    value['limits'] = dict(model_calls=LIMITS['provider_requests'],
        model_tokens=LIMITS['admission_reserve_tokens'],
        render_seconds=LIMITS['render_seconds'], revisions=0)
    value['sha256'] = digest({k: v for k, v in value.items() if k != 'sha256'})
    return validate_usage(value)


def execution_usage(record):
    return record['continuation_usage'] if record.get('review_continuation') else record['usage']


def execution_status(record):
    return usage_status(execution_usage(record))


def cumulative_status(record):
    old, new = usage_status(record['usage']), execution_status(record)
    result = deepcopy(old)
    result['used']['model_calls'] += new['used']['model_calls']
    result['used']['known_tokens_lower_bound'] += new['used']['known_tokens_lower_bound']
    result['used']['known_seconds_lower_bound'] += new['used']['known_seconds_lower_bound']
    if result['used']['render_seconds'] is not None and new['used']['render_seconds'] is not None:
        result['used']['render_seconds'] += new['used']['render_seconds']
    else:
        result['used']['render_seconds'] = None
    result.update(status='usage_unknown', can_start_export=False,
        cumulative_cost_status='unknown', original_total_token_cap_proven=False,
        export_authority='explicit_continuation_grant_only',
        continuation_usage_sha256=record['continuation_usage']['sha256'])
    # No remaining legacy amount can authorize new spending. The separately
    # exposed grant status is the only permitted admission view.
    result['remaining'].update(model_tokens=None, model_calls=0, render_seconds=0, revisions=0)
    return result
