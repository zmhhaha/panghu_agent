"""Reported accounting only; missing measurements are never estimated as zero."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import re

from sqlalchemy import select

from .models import AgentOutbox, Job, UsageRecord

MAX_SAFE_INTEGER = 2**53 - 1
AGENT_PROVIDER = 'agent-service'


def counter(value):
    return value if type(value) is int and 0 <= value <= MAX_SAFE_INTEGER else None


def money(value):
    if type(value) not in (str, int, float, Decimal) or len(str(value)) > 100:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal('1e18'):
            return None
        if result.as_tuple().exponent < -60:
            return None
        plain = format(result, 'f')
        return plain if len(plain) <= 60 else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def normalize_agent_usage(value):
    """Whitelist the authenticated Agent terminal outcome, never event prose."""
    value = value if type(value) is dict else {}
    currency = value.get('currency')
    currency = currency if type(currency) is str and re.fullmatch(r'[A-Z]{3}', currency) else None
    supplied_cost = value.get('reported_cost')
    cost = money(supplied_cost) if type(supplied_cost) in (int, float) else None
    status = value.get('cost_status')
    if currency is None or status not in ('known', 'complete', 'partial'):
        cost = None
    model = value.get('model')
    model = model if type(model) is str and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', model) else None
    return {
        'input_tokens': counter(value.get('input_tokens')),
        'output_tokens': counter(value.get('output_tokens')),
        'reported_cost': cost, 'currency': currency if cost is not None else None,
        'cost_status': 'partial' if cost is not None and status == 'partial' else 'reported' if cost is not None else 'unknown',
        # Native counters without an explicit completeness claim are observed
        # partial counters, even when the enclosing job completed successfully.
        'tokens_complete': value.get('completeness') == 'complete',
        'model': model,
    }


def preserve_accounting(out, payload):
    """Keep frozen accounting when later business validation maps the result."""
    old = out.result if type(out.result) is dict else {}
    return dict(payload, accounting=old['accounting']) if 'accounting' in old else payload


def usage_summary(session, *, job=None, owner=None):
    """One owner/job projection, with incomplete historical coverage explicit.

    Agent rows are per frozen remote attempt; old proxy rows are per request.
    Only recorded counters are added. Different currencies are never added.
    unknown_record_count counts missing/partial cost receipts and uncovered
    attempts; token completeness is represented independently by tokens_status.
    """
    scope = 'task' if job is not None else 'account'
    if session is None:
        rows, jobs, outboxes = [], [], []
    else:
        filters = (UsageRecord.issuer == (job.issuer if job is not None else owner.issuer),
                   UsageRecord.subject == (job.subject if job is not None else owner.sub))
        query = select(UsageRecord).where(*filters)
        owner_filters = (Job.issuer == (job.issuer if job is not None else owner.issuer),
                         Job.subject == (job.subject if job is not None else owner.sub))
        job_query = select(Job.id, Job.attempt).where(*owner_filters)
        out_query = select(AgentOutbox.job_id, AgentOutbox.result).join(Job, Job.id == AgentOutbox.job_id).where(*owner_filters)
        if job is not None:
            query = query.where(UsageRecord.job_id == job.id)
            job_query = job_query.where(Job.id == job.id)
            out_query = out_query.where(Job.id == job.id)
        rows = session.scalars(query).all()
        jobs = session.execute(job_query).all()
        # Do not load private task inputs or generate a growing IN (...) list
        # just to read the small frozen accounting metadata.
        outboxes = session.execute(out_query).all()
    metadata = {}
    for out in outboxes:
        accounting = out.result.get('accounting') if type(out.result) is dict else None
        if (type(accounting) is dict and accounting.get('schema') == 'ppt-service.agent-usage.v1'
                and type(accounting.get('provider_request_id')) is str
                and type(accounting.get('usage')) is dict):
            metadata[(out.job_id, accounting['provider_request_id'])] = accounting['usage']
    covered = {}
    for row in rows:
        covered.setdefault(row.job_id, set()).add(row.attempt)
    # Queued/unstarted work is not a zero-cost measurement. Missing historical
    # admitted attempts remain unknown instead of disappearing from coverage.
    missing = sum(max(0, item.attempt - sum(1 <= attempt <= item.attempt
                                           for attempt in covered.get(item.id, ()))) for item in jobs)
    unknown = missing
    inputs, outputs, costs = [], [], {}
    all_tokens_complete = bool(rows) and not missing
    all_costs_known = bool(rows) and not missing
    for row in rows:
        incoming, outgoing = counter(row.input_tokens), counter(row.output_tokens)
        if incoming is not None:
            inputs.append(incoming)
        if outgoing is not None:
            outputs.append(outgoing)
        complete = incoming is not None and outgoing is not None
        if row.provider == AGENT_PROVIDER:
            complete = complete and metadata.get((row.job_id, row.provider_request_id), {}).get('tokens_complete') is True
        all_tokens_complete = all_tokens_complete and complete
        amount = money(row.reported_cost)
        currency = row.currency if isinstance(row.currency, str) and re.fullmatch(r'[A-Z]{3}', row.currency) else None
        cost_known = amount is not None and currency is not None and row.cost_status in ('reported', 'partial')
        if cost_known:
            with localcontext() as ctx:
                ctx.prec = 80
                costs[currency] = costs.get(currency, Decimal(0)) + Decimal(amount)
        all_costs_known = all_costs_known and cost_known and row.cost_status == 'reported'
        if not cost_known or row.cost_status != 'reported':
            unknown += 1
    incoming = sum(inputs) if inputs else None
    outgoing = sum(outputs) if outputs else None
    if incoming is not None and incoming > MAX_SAFE_INTEGER:
        incoming = None
        all_tokens_complete = False
    if outgoing is not None and outgoing > MAX_SAFE_INTEGER:
        outgoing = None
        all_tokens_complete = False
    currencies = sorted(costs)
    currency = currencies[0] if len(currencies) == 1 else None
    return {
        'scope': scope, 'input_tokens': incoming, 'output_tokens': outgoing,
        'tokens_status': 'known' if all_tokens_complete else 'partial' if incoming is not None or outgoing is not None else 'unknown',
        'reported_cost': format(costs[currency], 'f') if currency else None,
        'currency': currency,
        'cost_status': 'mixed_currency' if len(currencies) > 1 else 'reported' if all_costs_known else 'partial' if costs else 'unknown',
        'cost_breakdown': [{'currency': code, 'reported_cost': format(costs[code], 'f')} for code in currencies],
        'record_count': len(rows), 'unknown_record_count': unknown,
    }


def quota_summary(quota):
    """Expose existing counts and limits; do not introduce a reset/refund policy."""
    result = {'enabled': bool(quota and quota.enabled), 'period': quota.period if quota else None}
    for category in ('generation', 'revision'):
        limit = getattr(quota, category + '_limit') if quota else None
        count = getattr(quota, category + '_count') if quota else None
        result[category + '_limit'] = limit
        result[category + '_count'] = count
        valid = (result['enabled'] and bool(result['period']) and counter(limit) is not None and counter(count) is not None)
        result[category + '_remaining'] = max(0, limit - count) if valid else None
    return result
