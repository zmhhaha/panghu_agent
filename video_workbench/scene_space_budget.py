"""Pure cumulative usage accounting, separate from an immutable creative plan.

The host must persist this ledger atomically and provide actual measurements.
An event hash is a content identity, not authentication or proof of measurement.
Unknown token or render usage stays unknown and cannot authorize a final export.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from agent_service.contracts import ServiceError, digest
from video_workbench.scene_space_plan import revise_plan, technical_retry

LEDGER_SCHEMA = 'video.scene-space-usage.v1'
MAX_EVENTS = 1024
MAX_HISTORY = 32
MAX_MEASUREMENT = 2**53-1
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
MODEL_PHASES = frozenset({'generation','review','collaboration','repair'})
_PHASES = MODEL_PHASES | {'preview','render','export','retry'}
_UNMEASURED = object()


def _require(ok, code='scene_space_usage_invalid', status=422):
    if not ok:
        raise ServiceError(code,status)


def _fields(value, keys):
    _require(type(value) is dict and set(value)==set(keys))


def _id(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)


def _hash(value):
    _require(type(value) is str and _SHA.fullmatch(value) is not None)


def _int(value):
    _require(type(value) is int and 0<=value<=MAX_MEASUREMENT)


def _seconds(value):
    _require(type(value) in (int,float) and 0<=value<=MAX_MEASUREMENT and math.isfinite(value))


def _seal(value):
    body=deepcopy(value)
    body.pop('sha256',None)
    return dict(body,sha256=digest(body))


def _limits(plan):
    budget=plan['budget']
    return dict(model_calls=budget['model']['limit_calls'],
        model_tokens=budget['model']['limit_tokens'],
        render_seconds=budget['render']['limit_seconds'],
        revisions=budget['revisions']['limit'])


def _event(value):
    _fields(value,('id','phase','outcome','model_calls','model_tokens','render_seconds'))
    _id(value['id'])
    _require(type(value['phase']) is str and value['phase'] in _PHASES)
    _require(type(value['outcome']) is str and value['outcome'] in {'completed','failed','cancelled'})
    _int(value['model_calls'])
    if value['model_tokens'] is not None:
        _int(value['model_tokens'])
    else:
        _require(value['model_calls']>0)
    if value['render_seconds'] is not None:
        _seconds(value['render_seconds'])
    return deepcopy(value)


def validate_reservation(*, id, phase, model_calls=0, model_tokens=0, render_seconds=0):
    """Validate finite admission holds without treating estimates as spending.

    Every admitted model operation needs both a call and a token allowance.
    Actual settlement may still report unknown or overrun usage; neither is
    replaced with this estimate or rejected to hide consumed resources.
    """
    _require(model_tokens is not None and render_seconds is not None,
        'scene_space_reservation_measurement_invalid')
    event=_event(dict(id=id,phase=phase,outcome='completed',model_calls=model_calls,
        model_tokens=model_tokens,render_seconds=render_seconds))
    if phase in MODEL_PHASES or model_calls or model_tokens:
        _require(model_calls>0 and model_tokens>0,'scene_space_model_reservation_required')
    return {key:value for key,value in event.items() if key!='outcome'}


def validate_usage(value):
    """Validate content identity and all bounded accounting inputs."""
    _fields(value,('schema','lineage_id','plan_id','root_plan_sha256','plan_sha256',
        'plan_revision','limits','baseline','history','events','sha256'))
    _require(value['schema']==LEDGER_SCHEMA)
    for key in ('lineage_id','plan_id'):_id(value[key])
    for key in ('root_plan_sha256','plan_sha256','sha256'):_hash(value[key])
    _int(value['plan_revision']);_require(value['plan_revision']>0)
    _fields(value['limits'],('model_calls','model_tokens','render_seconds','revisions'))
    for key in ('model_calls','model_tokens','revisions'):_int(value['limits'][key])
    _seconds(value['limits']['render_seconds'])
    _fields(value['baseline'],('model_calls','model_tokens','render_seconds','revisions','plan_revision'))
    for key in ('model_calls','revisions','plan_revision'):_int(value['baseline'][key])
    if value['baseline']['model_tokens'] is not None:_int(value['baseline']['model_tokens'])
    _seconds(value['baseline']['render_seconds'])
    _require(type(value['events']) is list and len(value['events'])<=MAX_EVENTS)
    ids=set()
    for event in value['events']:
        _event(event);_require(event['id'] not in ids);ids.add(event['id'])
    _require(type(value['history']) is list and len(value['history'])<=MAX_HISTORY)
    cursor=value['root_plan_sha256']
    revision=value['baseline']['plan_revision']
    for item in value['history']:
        _fields(item,('from_sha256','to_sha256','revision'))
        _hash(item['from_sha256']);_hash(item['to_sha256']);_int(item['revision'])
        _require(item['from_sha256']==cursor and item['revision']==revision+1)
        cursor,revision=item['to_sha256'],item['revision']
    _require(cursor==value['plan_sha256'] and revision==value['plan_revision'])
    _require(_seal(value)['sha256']==value['sha256'],'scene_space_usage_identity_changed',409)
    return deepcopy(value)


def create_usage(frozen, *, lineage_id):
    """Start one host-owned task lineage from the frozen opening counters."""
    _id(lineage_id)
    checked=technical_retry(frozen,frozen.get('plan_sha256') if type(frozen) is dict else None)
    plan=checked['plan'];budget=plan['budget']
    return validate_usage(_seal(dict(schema=LEDGER_SCHEMA,lineage_id=lineage_id,
        plan_id=plan['id'],root_plan_sha256=checked['plan_sha256'],
        plan_sha256=checked['plan_sha256'],plan_revision=plan['revision'],
        limits=_limits(plan),baseline=dict(model_calls=budget['model']['used_calls'],
            model_tokens=budget['model']['used_tokens'],render_seconds=budget['render']['used_seconds'],
            revisions=budget['revisions']['used'],plan_revision=plan['revision']),history=[],events=[])))


def record_usage(ledger, *, id, phase, outcome, model_calls=_UNMEASURED,
                 model_tokens=_UNMEASURED, render_seconds=_UNMEASURED):
    """Charge measured costs even for failure or overrun; never discard spending.

    Replaying the identical event is idempotent. Reusing its ID with different
    measurements fails. Actual overruns remain recorded and block later work.
    """
    checked=validate_usage(ledger)
    if model_calls is _UNMEASURED:
        _require(type(phase) is str and phase not in MODEL_PHASES,
            'scene_space_usage_measurement_required')
        model_calls=0
    if model_tokens is _UNMEASURED:
        model_tokens=None if model_calls else 0
    if render_seconds is _UNMEASURED:
        render_seconds=None if type(phase) is str and phase in {'preview','render','export','retry'} else 0
    event=_event(dict(id=id,phase=phase,outcome=outcome,model_calls=model_calls,
        model_tokens=model_tokens,render_seconds=render_seconds))
    for previous in checked['events']:
        if previous['id']==id:
            _require(previous==event,'scene_space_usage_event_conflict',409)
            return checked
    _require(len(checked['events'])<MAX_EVENTS,'scene_space_usage_event_limit',409)
    checked['events'].append(event)
    return validate_usage(_seal(checked))


def usage_status(ledger):
    checked=validate_usage(ledger)
    baseline=checked['baseline'];events=checked['events'];limits=checked['limits']
    calls=baseline['model_calls']+sum(e['model_calls'] for e in events)
    seconds=baseline['render_seconds']+sum(e['render_seconds'] or 0 for e in events)
    unknown_tokens=baseline['model_tokens'] is None or any(e['model_tokens'] is None for e in events)
    unknown_seconds=any(e['render_seconds'] is None for e in events)
    unknown=unknown_tokens or unknown_seconds
    tokens=(baseline['model_tokens'] or 0)+sum(e['model_tokens'] or 0 for e in events)
    revisions=baseline['revisions']+len(checked['history'])
    over=[key for key,used in [('model_calls',calls),('model_tokens',tokens),
        ('render_seconds',seconds),('revisions',revisions)] if used>limits[key]]
    return dict(schema='video.scene-space-usage-status.v1',ledger_sha256=checked['sha256'],
        status='over_budget' if over else 'usage_unknown' if unknown else 'within_budget',
        used=dict(model_calls=calls,model_tokens=None if unknown_tokens else tokens,
            known_tokens_lower_bound=tokens,render_seconds=None if unknown_seconds else seconds,
            known_seconds_lower_bound=seconds,revisions=revisions),
        remaining=dict(model_calls=max(0,limits['model_calls']-calls),
            model_tokens=None if unknown_tokens else max(0,limits['model_tokens']-tokens),
            render_seconds=None if unknown_seconds else max(0,limits['render_seconds']-seconds),
            revisions=max(0,limits['revisions']-revisions)),exceeded=over,
        available_event_slots=MAX_EVENTS-len(events),
        can_start_export=not over and not unknown and seconds<limits['render_seconds'] and len(events)<MAX_EVENTS,
        measurement_scope='host_supplied_usage_not_independently_observed')


def require_export_budget(ledger, *, reserve_seconds=0):
    _seconds(reserve_seconds)
    status=usage_status(ledger)
    _require(status['can_start_export'] and status['remaining']['render_seconds']>=reserve_seconds,
        'scene_space_export_budget_unavailable',409)
    return status


def bind_revision(ledger, before_frozen, after_frozen, *, reason):
    """Carry the same actual cost history to a separately validated revision."""
    checked=validate_usage(ledger)
    before=technical_retry(before_frozen,checked['plan_sha256'])
    after=technical_retry(after_frozen,after_frozen.get('plan_sha256') if type(after_frozen) is dict else None)
    plan=after['plan']
    _require(before['plan']['id']==checked['plan_id']==plan['id']
        and before['plan']['revision']==checked['plan_revision']
        and plan['revision']==checked['plan_revision']+1,'scene_space_usage_revision_invalid',409)
    _require(_limits(before['plan'])==checked['limits']==_limits(plan),
        'scene_space_usage_cap_changed',409)
    revise_plan(before,plan,reason=reason)
    used=usage_status(checked)['used']
    _require(plan['budget']['model']['used_calls']<=used['model_calls']
        and plan['budget']['render']['used_seconds']<=used['known_seconds_lower_bound'],
        'scene_space_usage_unrecorded_spending',409)
    tokens=plan['budget']['model']['used_tokens']
    _require(tokens is None or used['model_tokens'] is not None and tokens<=used['model_tokens'],
        'scene_space_usage_unrecorded_spending',409)
    _require(plan['budget']['revisions']['used']==used['revisions']+1,
        'scene_space_usage_revision_invalid',409)
    _require(len(checked['history'])<MAX_HISTORY,'scene_space_usage_history_limit',409)
    checked['history'].append(dict(from_sha256=checked['plan_sha256'],
        to_sha256=after['plan_sha256'],revision=plan['revision']))
    checked.update(plan_sha256=after['plan_sha256'],plan_revision=plan['revision'])
    return validate_usage(_seal(checked))


def prepare_retry(ledger, frozen, expected_plan_sha256):
    """Validate a frozen retry while retaining the exact cumulative ledger."""
    checked=validate_usage(ledger)
    _require(checked['plan_sha256']==expected_plan_sha256,'scene_space_usage_plan_changed',409)
    retried=technical_retry(frozen,expected_plan_sha256)
    _require(retried['plan']['id']==checked['plan_id']
        and retried['plan']['revision']==checked['plan_revision']
        and _limits(retried['plan'])==checked['limits'],
        'scene_space_usage_plan_changed',409)
    status=usage_status(checked)
    _require(status['can_start_export'],'scene_space_retry_budget_unavailable',409)
    return dict(frozen=retried,usage=checked,status=status)
