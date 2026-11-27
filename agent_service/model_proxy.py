"""Attempt-scoped Responses gateway. Provider credentials never enter capsules.

Admission of each distinct HTTP body is durable before contacting the upstream.
An interrupted admission remains uncertain and is never silently reissued. A
completed response may be replayed from the database without another model call.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import (Column, Float, ForeignKey, Integer, LargeBinary, String,
                        Table, insert, select, update, func)

from .contracts import JobModelLimits, ServiceError, canonical_bytes, digest
from .diagnostics import log_execution_failure
from .model_timeouts import MODEL_PROXY_IO_TIMEOUT_SECONDS
from .store import metadata, jobs, attempts, worker_allocations


requests = Table('agent_model_requests', metadata,
    Column('attempt_id', String(36), ForeignKey('agent_attempts.id'), primary_key=True),
    Column('body_sha256', String(64), primary_key=True),
    Column('state', String(16), nullable=False),
    Column('created_at', Float, nullable=False),
    Column('status', Integer), Column('media_type', String(80)),
    Column('response', LargeBinary))

# New limited jobs retain the exact non-sensitive bounds normalized before the
# upstream POST. Existing requests/rows and their historical proofs are intact.
request_limits = Table('agent_model_request_limits', metadata,
    Column('attempt_id', String(36), ForeignKey('agent_attempts.id'), primary_key=True),
    Column('body_sha256', String(64), primary_key=True),
    Column('max_requests', Integer, nullable=False),
    Column('max_output_tokens', Integer, nullable=False))

observations = Table('agent_tool_observations', metadata,
    Column('attempt_id', String(36), ForeignKey('agent_attempts.id'), primary_key=True),
    Column('call_sha256', String(64), primary_key=True),
    Column('created_at', Float, nullable=False), Column('state', String(16), nullable=False),
    Column('request', LargeBinary, nullable=False), Column('response', LargeBinary))

# Transport evidence only: retain hashes and positions, never image bytes, tool
# arguments or prompt text. Completion comes from the actual request row, not
# an Agent-provided claim. A separate table requires no rewrite of old rows.
image_inputs = Table('agent_model_image_inputs', metadata,
    Column('attempt_id', String(36), ForeignKey('agent_attempts.id'), primary_key=True),
    Column('body_sha256', String(64), primary_key=True),
    Column('input_item_index', Integer, primary_key=True),
    Column('part_index', Integer, primary_key=True),
    Column('sha256', String(64), nullable=False),
    Column('media_type', String(32), nullable=False),
    Column('byte_length', Integer, nullable=False))


def _usage_counter(value):
    return type(value) is int and 0 <= value <= 2**63 - 1


def _response_usage(raw, media_type):
    """Read supplier terminal Responses usage, never assistant/tool output."""
    if not raw or len(raw) > 32 * 1024 * 1024:
        return None
    candidates = []
    try:
        if media_type.split(';', 1)[0].strip() == 'text/event-stream':
            text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            for block in text.split('\n\n'):
                data = '\n'.join(line[5:].lstrip(' ') for line in block.split('\n') if line.startswith('data:'))
                if not data or data.strip() == '[DONE]':
                    continue
                value = json.loads(data)
                if isinstance(value, dict) and value.get('type') == 'response.completed':
                    candidates.append(value.get('response'))
        else:
            value = json.loads(raw)
            if isinstance(value, dict) and value.get('object') == 'response':
                candidates.append(value)
    except (ValueError, UnicodeError, RecursionError):
        return None
    # Repeated identical terminal events do not multiply a single request.
    if not candidates or any(not isinstance(v, dict) for v in candidates):
        return None
    try:
        if len({digest(v) for v in candidates}) != 1:
            return None
    except (ValueError, TypeError, RecursionError):
        return None
    value = candidates[0]
    usage = value.get('usage')
    if value.get('status', 'completed') != 'completed' or not isinstance(usage, dict):
        return None
    counters = {key: usage.get(key) for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    if (not all(_usage_counter(v) for v in counters.values())
            or counters['input_tokens'] + counters['output_tokens'] != counters['total_tokens']):
        return None
    detail = usage.get('input_tokens_details')
    detail = detail if isinstance(detail, dict) else {}
    for name, source in (('cache_read_tokens', 'cached_tokens'), ('cache_write_tokens', 'cache_write_tokens')):
        count = detail.get(source)
        if count is not None and (not _usage_counter(count) or count > counters['input_tokens']):
            return None
        counters[name] = count
    model = value.get('model')
    if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', model):
        counters['model'] = model
    return counters


def _attempt_usage_custody(attempt, allocation, job, has_requests):
    """Only control-validated stopped execution can close a usage snapshot."""
    outcome = attempt.get('outcome') or {}
    if (not has_requests and outcome.get('_service_not_started') is True
            and attempt['state'] in {'cancelled', 'timed_out'}):
        return True
    receipt = outcome.get('stop_receipt') or {}
    candidate = (allocation or {}).get('candidate_result') or {}
    native_receipt = candidate.get('stop_receipt') or {}
    observed = (allocation or {}).get('stop_observed_at')
    core = ('job_id', 'attempt_id', 'fence', 'engine', 'request_sha256', 'result_sha256')
    return (attempt['state'] in {'completed', 'needs_input', 'failed', 'cancelled', 'timed_out'}
        and outcome.get('_service_stop_proven') is True
        and allocation is not None
        and allocation['job_id'] == job['id'] and allocation['fence'] == attempt['fence']
        and receipt.get('job_id') == job['id'] and receipt.get('attempt_id') == attempt['id']
        and receipt.get('fence') == attempt['fence']
        and receipt.get('engine') == job['pins'].get('engine')
        and receipt.get('request_sha256') == allocation['request_sha256']
        and all(native_receipt.get(key) == receipt.get(key) for key in core)
        and all(isinstance(receipt.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', receipt[key])
                for key in ('request_sha256', 'result_sha256', 'signature'))
        and isinstance(native_receipt.get('signature'), str)
        and re.fullmatch(r'[0-9a-f]{64}', native_receipt['signature']) is not None
        and allocation.get('retired') is True and bool(allocation.get('pod_uid'))
        and type(observed) in (int, float) and math.isfinite(observed) and observed > 0
        and receipt.get('observed_at') == observed
        and receipt.get('resource_ids') == [f'k8s:{allocation["namespace"]}:{allocation["pod_uid"]}'])


def usage_evidence_for_job(store, job):
    """Fresh, read-only accounting from every original request of every attempt.

    The authorized job is rechecked against current control identity. Old worker
    outcomes and immutable model responses remain untouched. Missing or uncertain
    requests stay partial; an empty ledger never becomes invented zero usage.
    """
    job_id = job.get('job_id', job.get('id'))
    with store.engine.connect() as db:
        current = db.execute(select(jobs.c.id, jobs.c.pins, jobs.c.state, jobs.c.attempt_id, jobs.c.request,
            jobs.c.fence, jobs.c.outcome).where(jobs.c.id == job_id)).mappings().one_or_none()
        if current is None or any(current[key] != job.get(key) for key in ('attempt_id', 'fence', 'state')):
            return None
        current = dict(current)
        attempt_count = db.execute(select(func.count()).select_from(attempts).where(
            attempts.c.job_id == job_id)).scalar_one()
        attempts_seen = [dict(row) for row in db.execute(select(attempts.c.id, attempts.c.fence,
            attempts.c.state, attempts.c.outcome).where(attempts.c.job_id == job_id)
            .order_by(attempts.c.fence).limit(1025)).mappings()]
        request_count = db.execute(select(func.count()).select_from(requests).join(
            attempts, requests.c.attempt_id == attempts.c.id).where(attempts.c.job_id == job_id)).scalar_one()
        if not request_count:
            return None
        bounded = request_count <= 1024 and attempt_count <= 1024
        limited = current['request'].get('model_limits')
        limit_rows = {}
        if limited:
            for item in db.execute(select(request_limits).join(attempts,
                    request_limits.c.attempt_id == attempts.c.id).where(attempts.c.job_id == job_id)
                    .limit(1025)).mappings():
                limit_rows[(item['attempt_id'], item['body_sha256'])] = dict(item)
        allocations = {}
        for row in db.execute(select(worker_allocations.c.attempt_id, worker_allocations.c.job_id,
                worker_allocations.c.fence, worker_allocations.c.request_sha256,
                worker_allocations.c.namespace, worker_allocations.c.pod_uid,
                worker_allocations.c.stop_observed_at, worker_allocations.c.retired,
                worker_allocations.c.candidate_result['stop_receipt'].label('candidate_stop_receipt'))
                .where(worker_allocations.c.job_id == job_id,
                    worker_allocations.c.attempt_id.in_([a['id'] for a in attempts_seen[:1024]]))
                .limit(1024)).mappings():
            value = dict(row)
            value['candidate_result'] = dict(stop_receipt=value.pop('candidate_stop_receipt'))
            allocations[row['attempt_id']] = value
        columns = (list(requests.c) if bounded else [requests.c.attempt_id, requests.c.body_sha256,
            requests.c.state, requests.c.status])
        query = select(*columns, attempts.c.fence).join(attempts, requests.c.attempt_id == attempts.c.id).where(
            attempts.c.job_id == job_id).order_by(requests.c.created_at,
                requests.c.attempt_id, requests.c.body_sha256).limit(1025)
        # A server-side cursor retains at most one supplier body at a time.
        # Over-limit histories never select response bytes at all.
        records = []
        with db.execute(query.execution_options(stream_results=True, yield_per=1)) as result:
            for index, row in enumerate(result.mappings()):
                if index == 1024:
                    bounded = False
                    break
                raw = bytes(row['response'] or b'') if bounded else None
                usage = (_response_usage(raw, row['media_type'] or '')
                         if raw is not None and row['state'] == 'complete' and row['status'] == 200 else None)
                records.append(dict(attempt_id=row['attempt_id'], fence=row['fence'], body_sha256=row['body_sha256'],
                    execution_request_sha256=allocations.get(row['attempt_id'], {}).get('request_sha256'),
                    state=row['state'], status=row['status'],
                    response_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,
                    usage_complete=usage is not None, **(usage or {})))
                if limited:
                    measured_limit = limit_rows.get((row['attempt_id'], row['body_sha256']))
                    records[-1]['enforced_model_limits'] = ({key: measured_limit[key]
                        for key in ('max_requests', 'max_output_tokens')} if measured_limit else None)
    request_attempts = {row['attempt_id'] for row in records}
    attempt_proofs = []
    for attempt in attempts_seen[:1024]:
        allocation = allocations.get(attempt['id'])
        attempt_proofs.append(dict(attempt_id=attempt['id'], fence=attempt['fence'], state=attempt['state'],
            stop_proven=_attempt_usage_custody(attempt, allocation, current, attempt['id'] in request_attempts),
            execution_request_sha256=(allocation or {}).get('request_sha256'),
            stop_result_sha256=((attempt.get('outcome') or {}).get('stop_receipt') or {}).get('result_sha256'),
            native_stop_receipt_sha256=digest((allocation or {}).get('candidate_result', {}).get('stop_receipt')),
            control_stop_receipt_sha256=digest((attempt.get('outcome') or {}).get('stop_receipt'))))
    custody = bounded and bool(attempts_seen) and all(_attempt_usage_custody(
        attempt, allocations.get(attempt['id']), current, attempt['id'] in request_attempts)
        for attempt in attempts_seen)
    active_attempt = next((row for row in attempts_seen if row['id'] == current['attempt_id']), None)
    custody = custody and current['state'] in {'completed', 'needs_input', 'failed', 'cancelled', 'timed_out'} \
        and (current.get('outcome') or {}).get('_service_stop_proven') is True \
        and active_attempt is not None \
        and (current.get('outcome') or {}).get('stop_receipt') == (active_attempt.get('outcome') or {}).get('stop_receipt')
    complete = bounded and custody and all(row['usage_complete'] for row in records)
    totals = {}
    for key in ('input_tokens', 'output_tokens', 'total_tokens', 'cache_read_tokens', 'cache_write_tokens'):
        values = [row.get(key) for row in records]
        total = sum(values) if bounded and all(_usage_counter(value) for value in values) else None
        totals[key] = total if _usage_counter(total) else None
    complete = complete and all(totals[key] is not None for key in ('input_tokens', 'output_tokens', 'total_tokens'))
    models = {row.get('model') for row in records}
    evidence = dict(schema='agent-model-usage-evidence.v1', job_id=job_id,
        attempt_id=current['attempt_id'], fence=current['fence'], state=current['state'],
        execution_binding_sha256=(current['pins'].get('execution_binding') or {}).get('sha256'),
        stop_proven=custody, all_attempts=True, authoritative=custody and bounded,
        completeness='complete' if complete else 'partial', request_count=request_count, attempt_count=attempt_count,
        **totals, reported_cost=None, cost_status='unknown', attempts=attempt_proofs, requests=records)
    if limited:
        evidence['model_limits'] = limited
        evidence['model_limits_verified'] = (custody and bounded and request_count <= limited['max_requests']
            and len(limit_rows) == request_count and all(
                row.get('enforced_model_limits') is not None
                and row['enforced_model_limits']['max_requests'] == limited['max_requests']
                and 1 <= row['enforced_model_limits']['max_output_tokens'] <= limited['max_output_tokens']
                and (row.get('output_tokens') is None
                     or row['output_tokens'] <= row['enforced_model_limits']['max_output_tokens'])
                for row in records))
    if len(models) == 1 and None not in models:
        evidence['model'] = next(iter(models))
    return dict(evidence, sha256=digest(evidence))


def _image_inputs(value):
    """Hash the self-contained image bytes in an already validated request."""
    if not isinstance(value, list):
        return []
    images = []
    for index, item in enumerate(value):
        parts = item.get('output') if item.get('type') == 'function_call_output' else item.get('content')
        if not isinstance(parts, list):
            continue
        for part_index, part in enumerate(parts):
            if part.get('type') != 'input_image':
                continue
            header, encoded = part['image_url'].split(',', 1)
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise ServiceError('model_input_reference_not_authorized', 422) from None
            if not raw:
                raise ServiceError('model_input_reference_not_authorized', 422)
            images.append(dict(input_item_index=index, part_index=part_index,
                sha256=hashlib.sha256(raw).hexdigest(), byte_length=len(raw),
                media_type=header[5:].split(';', 1)[0]))
    return images


def image_evidence_for_job(store, job):
    """Control-only evidence for this job's current attempt, also after stop.

    This proves that exact image bytes were included in a completed upstream
    transport. It cannot prove model understanding or observation of unsupplied
    frames. Old requests without recorded image hashes provide no evidence.
    The caller must authorize ownership/profile before calling this function.
    """
    job_id = job.get('job_id', job.get('id'))
    attempt_id = job.get('attempt_id')
    with store.engine.connect() as db:
        envelope_sha256 = db.execute(select(worker_allocations.c.request_sha256).where(
            worker_allocations.c.job_id == job_id, worker_allocations.c.attempt_id == attempt_id,
            worker_allocations.c.fence == job.get('fence'))).scalar_one_or_none() if attempt_id else None
        rows = db.execute(select(image_inputs, requests.c.state, requests.c.status).join(
            requests, (image_inputs.c.attempt_id == requests.c.attempt_id)
            & (image_inputs.c.body_sha256 == requests.c.body_sha256)).join(
            attempts, image_inputs.c.attempt_id == attempts.c.id).where(
                attempts.c.job_id == job_id, attempts.c.id == attempt_id,
                attempts.c.fence == job.get('fence'), requests.c.state == 'complete',
                requests.c.status == 200).order_by(requests.c.created_at,
                image_inputs.c.body_sha256, image_inputs.c.input_item_index,
                image_inputs.c.part_index)).mappings().all() if attempt_id else []
    return dict(schema='agent-model-image-evidence.v1', job_id=job_id,
        attempt_id=attempt_id, request_sha256=envelope_sha256,
        evidence_scope='completed_upstream_transport', images=[dict(
            sha256=row['sha256'], media_type=row['media_type'], byte_length=row['byte_length'],
            input_item_index=row['input_item_index'], part_index=row['part_index'],
            model_request_sha256=row['body_sha256'], model_request_completed=True) for row in rows])


def _b64(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _request_json(raw):
    def reject_constant(_value):
        raise ValueError()

    try:
        return json.loads(raw, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError('invalid_json', 422) from None


@dataclass(frozen=True)
class ModelProxySettings:
    signing_key: bytes = field(repr=False)
    upstream_base_url: str
    upstream_token: str = field(repr=False)
    max_request_bytes: int = 8 * 1024 * 1024
    max_response_bytes: int = 32 * 1024 * 1024
    max_calls_per_attempt: int = 128
    max_output_tokens: int = 32768

    def __post_init__(self):
        u = urlsplit(self.upstream_base_url)
        if (len(self.signing_key) < 32 or not self.upstream_token
                or any(ord(c) < 33 or ord(c) > 126 for c in self.upstream_token)
                or u.scheme not in {'https', 'http'} or not u.hostname
                or u.username or u.password or u.query or u.fragment
                or u.path.rstrip('/') != '/v1' or '\\' in self.upstream_base_url):
            raise ValueError('A dedicated fixed model upstream and signing key are required')


def profile_model_call_limit(profile, configured):
    """Bound method/production roles without changing older budgets.

    The profile comes from the authenticated stored job, never request JSON or
    grant metadata supplied by a caller. A stricter operator cap still wins.
    """
    if profile == 'video.animation-film-module.v1':
        # Skill discovery/reads and writing the module each need a model turn;
        # the write's tool result also needs a final assistant turn.
        return min(configured, 16)
    if profile == 'video.animation-collaboration.v1':
        return min(configured, 10)
    if profile in {'video.joint-plan.v2',
                   'video.picture-learning-author.v1', 'video.picture-learning-review.v1',
                   'video.picture-learning-review.v2',
                   'video.reference-learning-author.v1', 'video.reference-learning-review.v1',
                   'video.animation-method-author.v1', 'video.animation-method-review.v1',
                   'video.animation-method-author.v2', 'video.animation-method-review.v2',
                   'video.animation-production.v1', 'video.animation-production.v2',
                   'video.animation-production.v3', 'video.joint-plan.v1',
                   'video.resource-plan.v1',
                   'video.animation-manim-recipe.v1'}:
        return min(configured, 8)
    return configured


class ModelGrantIssuer:
    def __init__(self, signing_key):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError('A dedicated grant signing key is required')
        self._key = signing_key

    def issue(self, envelope, model_policy):
        # Every field is frozen: reloading an allocation yields identical Secret
        # bytes. No provider token or caller-selected upstream enters the grant.
        body = dict(v=1, job=envelope.job_id, attempt=envelope.attempt_id,
            fence=envelope.fence, request=envelope.request_sha256,
            binding=envelope.execution_binding.sha256 if envelope.execution_binding else None,
            model=model_policy.model, provider=model_policy.provider,
            policy=digest(model_policy.model_dump(mode='json')), exp=envelope.deadline_at)
        payload = _b64(canonical_bytes(body))
        signature = _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())
        return payload + '.' + signature

    def decode(self, token):
        try:
            if not isinstance(token, str) or len(token) > 2048:
                raise ValueError()
            payload, signature = token.split('.')
            expected = _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
            body = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            if (set(body) != {'v', 'job', 'attempt', 'fence', 'request', 'binding',
                              'model', 'provider', 'policy', 'exp'} or body['v'] != 1
                    or type(body['exp']) not in (float, int) or not time.time() < body['exp']):
                raise ValueError()
            return body
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError('model_grant_invalid', 401) from None


class ModelProxy:
    def __init__(self, store, settings: ModelProxySettings, *, client=None):
        self.store, self.settings = store, settings
        self.issuer = ModelGrantIssuer(settings.signing_key)
        self.client = client or httpx.Client(trust_env=False, follow_redirects=False,
            transport=httpx.HTTPTransport(retries=0))

    def authorize(self, token):
        grant = self.issuer.decode(token)
        now = time.time()
        with self.store.engine.connect() as db:
            job = db.execute(select(jobs).where(jobs.c.id == grant['job'])).mappings().first()
            attempt = db.execute(select(attempts).where(attempts.c.id == grant['attempt'])).mappings().first()
            allocation = db.execute(select(worker_allocations).where(
                worker_allocations.c.attempt_id == grant['attempt'])).mappings().first()
        if (not job or not attempt or job['attempt_id'] != grant['attempt']
                or job['fence'] != grant['fence'] or attempt['fence'] != grant['fence']
                or attempt['job_id'] != grant['job'] or job['state'] not in {'starting', 'running'}
                or job['cancel_requested'] or job['lease_expires_at'] is None
                or job['lease_expires_at'] <= now or job['deadline_at'] < grant['exp']
                or job['deadline_at'] <= now
                or job['pins'].get('execution_binding', {}).get('sha256') != grant['binding']
                or not allocation or allocation['retired'] or not allocation['create_claimed']
                or allocation['job_id'] != grant['job'] or allocation['fence'] != grant['fence']
                or allocation['request_sha256'] != grant['request']):
            raise ServiceError('model_grant_revoked', 401)
        if job['pins'].get('execution_binding', {}).get('model_policy_sha256') != grant['policy']:
            raise ServiceError('model_grant_scope_mismatch', 401)
        if job['request'].get('model_limits') is not None:
            try:
                limits = JobModelLimits.model_validate(job['request']['model_limits'])
            except (ValueError, TypeError):
                raise ServiceError('model_job_limits_invalid', 503) from None
            # The immutable job request is control-owned and scoped by the
            # signed job identity. A worker cannot add or increase these caps.
            grant = dict(grant, model_limits=limits.model_dump(mode='json'))
        return grant

    def _body(self, value, grant):
        # Only model text/tool calls. Hosted tools, background jobs, remote
        # conversations and URLs outside message data cannot bypass task scope.
        allowed = {'model', 'input', 'instructions', 'tools', 'tool_choice',
            'parallel_tool_calls', 'stream', 'stream_options', 'temperature', 'top_p',
            'max_output_tokens', 'reasoning', 'text', 'metadata', 'store', 'include',
            'service_tier', 'truncation', 'prompt_cache_key'}
        if (not isinstance(value, dict) or set(value) - allowed
                or value.get('model') != grant['model'] or value.get('store') not in (None, False)
                or 'input' not in value or not isinstance(value.get('tools', []), list)
                or len(value.get('tools', [])) > 64
                or any(not isinstance(t, dict) or t.get('type') != 'function' for t in value.get('tools', []))):
            raise ServiceError('model_request_scope_invalid', 422)
        bound = value.get('max_output_tokens', self.settings.max_output_tokens)
        if type(bound) is not int or not 1 <= bound <= self.settings.max_output_tokens:
            raise ServiceError('model_output_bound_invalid', 422)
        if grant.get('model_limits'):
            bound = min(bound, grant['model_limits']['max_output_tokens'])
        if (type(value.get('stream', False)) is not bool
                or value.get('include', []) not in ([], ['reasoning.encrypted_content'])):
            raise ServiceError('model_request_scope_invalid', 422)
        cache_key = value.get('prompt_cache_key')
        if cache_key is not None and (type(cache_key) is not str or not 1 <= len(cache_key) <= 256
                or any(ord(char) < 32 or ord(char) == 127 for char in cache_key)):
            raise ServiceError('model_request_scope_invalid', 422)
        self._input(value['input'])
        # Pi attaches its ephemeral native session ID even in --no-session
        # mode. Task-scoped execution does not preserve native sessions or
        # request upstream cache affinity; omit this advisory hint before
        # admission hashing and forwarding to the shared Responses gateway.
        # Conversation/model/tool content remains unchanged.
        value = {key: item for key, item in value.items() if key != 'prompt_cache_key'}
        value.update(store=False, max_output_tokens=bound)
        return value

    @staticmethod
    def _input(value):
        """Allow self-contained conversation data, never provider/global refs."""
        def reject():
            raise ServiceError('model_input_reference_not_authorized', 422)

        def inline_image(part):
            image = part.get('image_url')
            if (set(part) - {'type', 'image_url', 'detail'} or not isinstance(image, str)
                    or not re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+=*', image)
                    or not isinstance(part.get('detail', 'auto'), str)
                    or part.get('detail', 'auto') not in {'auto', 'low', 'high', 'original'}):
                reject()

        def tool_output(value):
            if isinstance(value, str):
                return
            # Pi's Responses converter keeps image-bearing tool results as
            # content parts. Keep their tool-call identity and bytes intact;
            # only self-contained text and inline images are authorized.
            if not isinstance(value, list) or not 1 <= len(value) <= 2000:
                reject()
            for part in value:
                if not isinstance(part, dict):
                    reject()
                if part.get('type') == 'input_text':
                    if set(part) != {'type', 'text'} or not isinstance(part['text'], str):
                        reject()
                elif part.get('type') == 'input_image':
                    inline_image(part)
                else:
                    reject()

        if isinstance(value, str):
            return
        if not isinstance(value, list) or len(value) > 2000:
            reject()
        call_ids = set()
        for item in value:
            if not isinstance(item, dict):
                reject()
            kind = item.get('type', 'message' if 'role' in item else '')
            if kind == 'function_call':
                if (set(item) - {'type', 'id', 'call_id', 'name', 'arguments', 'status'}
                        or not all(isinstance(item.get(k), str) for k in ('call_id', 'name', 'arguments'))):
                    reject()
                call_ids.add(item['call_id'])
            elif kind == 'function_call_output':
                if (set(item) - {'type', 'call_id', 'output', 'id', 'status'}
                        or not isinstance(item.get('call_id'), str)
                        or item['call_id'] not in call_ids):
                    reject()
                tool_output(item.get('output'))
            elif kind == 'reasoning':
                # Native Responses output can carry reasoning even when the
                # client did not request a reasoning mode. Pi replays the full
                # signed item, including content:[], on the next tool turn.
                if (set(item) - {'type', 'id', 'summary', 'encrypted_content', 'status', 'content'}
                        or ('id' in item and (type(item['id']) is not str or not item['id']))
                        or (item.get('encrypted_content') is not None
                            and type(item['encrypted_content']) is not str)
                        or (item.get('status') is not None and (type(item['status']) is not str
                            or item['status'] not in {'in_progress', 'completed', 'incomplete'}))):
                    reject()
                for key, part_type in (('summary', 'summary_text'), ('content', 'reasoning_text')):
                    parts = item.get(key, [])
                    if type(parts) is not list or len(parts) > 2000 or any(
                            type(part) is not dict or set(part) != {'type', 'text'}
                            or part['type'] != part_type or type(part['text']) is not str for part in parts):
                        reject()
            elif kind == 'message':
                phase = item.get('phase')
                # Pi 0.73.1 replays assistant text signatures with Responses
                # phase metadata after tool calls. Preserve this semantic field
                # in forwarding/admission identity; reject unknown phase values.
                if (set(item) - {'type', 'id', 'role', 'content', 'status', 'phase'}
                        or not isinstance(item.get('role'), str)
                        or item['role'] not in {'user', 'assistant', 'system', 'developer'}
                        or (phase is not None and (type(phase) is not str
                            or phase not in {'commentary', 'final_answer'}))):
                    reject()
                content = item.get('content')
                if isinstance(content, str):
                    continue
                if not isinstance(content, list):
                    reject()
                for part in content:
                    if not isinstance(part, dict):
                        reject()
                    content_type = part.get('type')
                    if not isinstance(content_type, str):
                        reject()
                    if content_type in {'input_text', 'output_text'}:
                        if set(part) - {'type', 'text', 'annotations', 'logprobs'} or not isinstance(part.get('text'), str):
                            reject()
                        if part.get('annotations') not in (None, []):
                            reject()
                    elif content_type == 'input_image':
                        inline_image(part)
                    elif content_type == 'refusal':
                        if set(part) != {'type', 'refusal'} or not isinstance(part['refusal'], str):
                            reject()
                    else:
                        reject()
            else:
                reject()

    def academic(self, token, value):
        grant = self.authorize(token)
        if (not isinstance(value, dict) or set(value) != {'tool', 'payload'}
                or not isinstance(value['tool'], str)
                or value['tool'] not in {'academic_search', 'academic_verify'}
                or not isinstance(value['payload'], dict)):
            raise ServiceError('research_tool_not_authorized', 403)
        try:
            raw_request = canonical_bytes(value)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('research_tool_not_authorized', 403) from None
        if len(raw_request) > 16384:
            raise ServiceError('research_tool_not_authorized', 403)
        with self.store.engine.connect() as db:
            job = db.execute(select(jobs).where(jobs.c.id == grant['job'])).mappings().one()
        if job['request']['profile'] not in {'research.generate.v1', 'research.retrieve.v1'}:
            raise ServiceError('research_tool_not_authorized', 403)
        identity = digest(value)
        key = (observations.c.attempt_id == grant['attempt']) & (observations.c.call_sha256 == identity)
        with self.store.engine.begin() as db:
            db.execute(select(attempts.c.id).where(attempts.c.id == grant['attempt']).with_for_update()).first()
            old = db.execute(select(observations).where(key)).mappings().first()
            if old:
                if old['state'] != 'complete':
                    raise ServiceError('tool_observation_uncertain', 409)
                return json.loads(old['response'])
            count = db.execute(select(func.count()).select_from(observations).where(
                observations.c.attempt_id == grant['attempt'])).scalar_one()
            if count >= 100:
                raise ServiceError('research_tool_budget_exhausted', 429)
            if job['request']['profile'] == 'research.retrieve.v1' and value['tool'] == 'academic_search':
                # Admission, including an uncertain prior call, consumes budget.
                # The attempt lock above serializes the check with insertion.
                recorded = db.execute(select(observations.c.request).where(
                    observations.c.attempt_id == grant['attempt'])).scalars().all()
                searches = sum(json.loads(raw)['tool'] == 'academic_search' for raw in recorded)
                maximum = job['request']['task']['source_policy']['max_search_calls']
                if searches >= maximum:
                    raise ServiceError('research_tool_budget_exhausted', 429)
            db.execute(insert(observations).values(attempt_id=grant['attempt'], call_sha256=identity,
                created_at=time.time(), state='admitted', request=canonical_bytes(value)))
        from .profiles.research import academic_tool
        result = academic_tool(value['tool'], value['payload'], job['request']['task'])
        if job['request']['profile'] == 'research.retrieve.v1' and value['tool'] == 'academic_search':
            from scientific_agent.retrieval_guidance import low_result_notes
            result['notes'] = list(result.get('notes', [])) + low_result_notes(
                value['payload']['query'], len(result.get('papers', [])), remaining=maximum - searches - 1)
        raw = canonical_bytes(result)
        if len(raw) > 2 * 1024 * 1024:
            raise ServiceError('research_tool_output_limit', 413)
        self.authorize(token)
        with self.store.engine.begin() as db:
            db.execute(update(observations).where(key).values(state='complete', response=raw))
        return result

    def academic_observations(self, token):
        grant = self.authorize(token)
        with self.store.engine.connect() as db:
            rows = db.execute(select(observations).where(
                observations.c.attempt_id == grant['attempt']).order_by(
                    observations.c.created_at, observations.c.call_sha256)).mappings().all()
        return dict(schema_version='1', attempt_id=grant['attempt'], request_sha256=grant['request'],
            observations=[dict(call_sha256=r['call_sha256'], observed_at=r['created_at'],
                state=r['state'], request=json.loads(r['request']),
                response=json.loads(r['response']) if r['response'] else None) for r in rows])

    def respond(self, token, value):
        grant = self.authorize(token)
        body = self._body(value, grant)
        images = _image_inputs(body['input'])
        try:
            identity = digest(body)
            if len(canonical_bytes(body)) > self.settings.max_request_bytes:
                raise ServiceError('request_too_large', 413)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('model_request_scope_invalid', 422) from None
        key = (requests.c.attempt_id == grant['attempt']) & (requests.c.body_sha256 == identity)
        # Locking the attempt serializes admission and the per-attempt budget.
        with self.store.engine.begin() as db:
            if grant.get('model_limits'):
                # Serialize all attempts of this job, including uncertain
                # admissions. A restarted attempt never receives eight more.
                db.execute(select(jobs.c.id).where(jobs.c.id == grant['job']).with_for_update()).first()
            db.execute(select(attempts.c.id).where(attempts.c.id == grant['attempt']).with_for_update()).first()
            old = db.execute(select(requests).where(key)).mappings().first()
            if old:
                if old['state'] != 'complete':
                    raise ServiceError('model_admission_uncertain', 409)
                return old['status'], old['media_type'], old['response']
            count = db.execute(select(func.count()).select_from(requests).where(
                requests.c.attempt_id == grant['attempt'])).scalar_one()
            request = db.execute(select(jobs.c.request).where(jobs.c.id == grant['job'])).scalar_one()
            maximum = profile_model_call_limit(request['profile'], self.settings.max_calls_per_attempt)
            if count >= maximum:
                raise ServiceError('model_budget_exhausted', 429)
            if grant.get('model_limits'):
                job_count = db.execute(select(func.count()).select_from(requests).join(
                    attempts, requests.c.attempt_id == attempts.c.id).where(
                    attempts.c.job_id == grant['job'])).scalar_one()
                if job_count >= grant['model_limits']['max_requests']:
                    raise ServiceError('model_job_budget_exhausted', 429)
                previous = select(requests.c.state, requests.c.status, requests.c.media_type,
                    requests.c.response, request_limits.c.max_requests,
                    request_limits.c.max_output_tokens).join(
                    attempts, requests.c.attempt_id == attempts.c.id).outerjoin(request_limits,
                    (request_limits.c.attempt_id == requests.c.attempt_id)
                    & (request_limits.c.body_sha256 == requests.c.body_sha256)).where(
                    attempts.c.job_id == grant['job'])
                # Limited jobs run serial model turns. Missing/uncertain prior
                # usage must stop new spending inside the worker too, rather
                # than waiting for the application's final job settlement.
                with db.execute(previous.execution_options(stream_results=True, yield_per=1)) as rows:
                    for row in rows.mappings():
                        usage = (_response_usage(bytes(row['response'] or b''), row['media_type'] or '')
                            if row['state'] == 'complete' and row['status'] == 200 else None)
                        bound = row['max_output_tokens']
                        if (usage is None or row['max_requests'] != grant['model_limits']['max_requests']
                                or type(bound) is not int or not 1 <= bound <= grant['model_limits']['max_output_tokens']):
                            raise ServiceError('model_admission_uncertain', 409)
                        if usage['output_tokens'] > bound:
                            # A provider may violate the forwarded bound. Keep
                            # its actual usage and free replay, but never fund a
                            # new body after this known per-request violation.
                            raise ServiceError('model_job_output_limit_exceeded', 429)
            db.execute(insert(requests).values(attempt_id=grant['attempt'], body_sha256=identity,
                state='admitted', created_at=time.time()))
            if grant.get('model_limits'):
                db.execute(insert(request_limits).values(attempt_id=grant['attempt'], body_sha256=identity,
                    max_requests=grant['model_limits']['max_requests'], max_output_tokens=body['max_output_tokens']))
            if images:
                db.execute(insert(image_inputs), [dict(attempt_id=grant['attempt'],
                    body_sha256=identity, **image) for image in images])
        diagnostic_stage = 'authorize_upstream'
        upstream_status = upstream_media = None
        try:
            self.authorize(token)
            remaining = max(.1, min(MODEL_PROXY_IO_TIMEOUT_SECONDS, grant['exp'] - time.time()))
            # No retry, no redirect, no client/discovery URL, no environment proxy.
            diagnostic_stage = 'open_upstream'
            with self.client.stream('POST', self.settings.upstream_base_url.rstrip('/') + '/responses',
                    headers={'Authorization': 'Bearer ' + self.settings.upstream_token,
                             'Content-Type': 'application/json', 'Accept-Encoding': 'identity'},
                    content=canonical_bytes(body), timeout=httpx.Timeout(remaining, connect=min(10., remaining))) as response:
                diagnostic_stage = 'validate_upstream'
                upstream_status = response.status_code
                upstream_media = response.headers.get('content-type', '').split(';')[0]
                if response.status_code != 200:
                    raise ServiceError('model_upstream_rejected', 502)
                media = upstream_media
                if media not in {'application/json', 'text/event-stream'}:
                    raise ServiceError('model_upstream_protocol_invalid', 502)
                chunks, size = [], 0
                diagnostic_stage = 'receive_upstream'
                for chunk in response.iter_bytes(65536):
                    diagnostic_stage = 'authorize_upstream'
                    self.authorize(token)
                    diagnostic_stage = 'receive_upstream'
                    size += len(chunk)
                    if size > self.settings.max_response_bytes:
                        raise ServiceError('model_response_too_large', 502)
                    chunks.append(chunk)
                payload = b''.join(chunks)
                if not payload:
                    raise ServiceError('model_upstream_protocol_invalid', 502)
            diagnostic_stage = 'authorize_upstream'
            self.authorize(token)
            diagnostic_stage = 'persist_response'
            with self.store.engine.begin() as db:
                db.execute(update(requests).where(key).values(state='complete', status=200,
                    media_type=media, response=payload))
            return 200, media, payload
        except Exception as exc:
            log_execution_failure('model_proxy', diagnostic_stage, exc,
                upstream_status=upstream_status, upstream_media=upstream_media)
            # Retain admitted, including ambiguous network failures. Reconciliation
            # never spends again merely because an HTTP response was lost.
            raise ServiceError('model_admission_uncertain', 502) from None


def create_model_proxy_app(proxy: ModelProxy):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ServiceError)
    async def failure(_request, exc):
        return JSONResponse({'error': {'code': exc.code}}, status_code=exc.status)

    def token(request):
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer '):
            raise ServiceError('authentication_required', 401)
        return header[7:]

    @app.get('/v1/models')
    def models(request: Request):
        grant = proxy.authorize(token(request))
        return {'object': 'list', 'data': [{'id': grant['model'], 'object': 'model'}]}

    @app.post('/v1/responses')
    async def responses(request: Request):
        authorization = token(request)
        proxy.authorize(authorization)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > proxy.settings.max_request_bytes:
                raise ServiceError('request_too_large', 413)
            chunks.append(chunk)
        value = _request_json(b''.join(chunks))
        from starlette.concurrency import run_in_threadpool
        status, media, payload = await run_in_threadpool(proxy.respond, authorization, value)
        return Response(payload, status_code=status, media_type=media,
                        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @app.post('/v1/tools/academic')
    async def academic(request: Request):
        authorization = token(request)
        proxy.authorize(authorization)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384:
                raise ServiceError('request_too_large', 413)
        value = _request_json(raw)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(proxy.academic, authorization, value)

    @app.get('/v1/tools/observations')
    def observed(request: Request):
        return proxy.academic_observations(token(request))

    @app.on_event('shutdown')
    def close():
        proxy.client.close()

    return app
