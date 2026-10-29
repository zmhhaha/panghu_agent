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
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import (Column, Float, ForeignKey, Integer, LargeBinary, String,
                        Table, insert, select, update, func)

from .contracts import ServiceError, canonical_bytes, digest
from .diagnostics import log_execution_failure
from .store import metadata, jobs, attempts, worker_allocations


requests = Table('agent_model_requests', metadata,
    Column('attempt_id', String(36), ForeignKey('agent_attempts.id'), primary_key=True),
    Column('body_sha256', String(64), primary_key=True),
    Column('state', String(16), nullable=False),
    Column('created_at', Float, nullable=False),
    Column('status', Integer), Column('media_type', String(80)),
    Column('response', LargeBinary))

observations = Table('agent_tool_observations', metadata,
    Column('attempt_id', String(36), ForeignKey('agent_attempts.id'), primary_key=True),
    Column('call_sha256', String(64), primary_key=True),
    Column('created_at', Float, nullable=False), Column('state', String(16), nullable=False),
    Column('request', LargeBinary, nullable=False), Column('response', LargeBinary))


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
        try:
            identity = digest(body)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('model_request_scope_invalid', 422) from None
        key = (requests.c.attempt_id == grant['attempt']) & (requests.c.body_sha256 == identity)
        # Locking the attempt serializes admission and the per-attempt budget.
        with self.store.engine.begin() as db:
            db.execute(select(attempts.c.id).where(attempts.c.id == grant['attempt']).with_for_update()).first()
            old = db.execute(select(requests).where(key)).mappings().first()
            if old:
                if old['state'] != 'complete':
                    raise ServiceError('model_admission_uncertain', 409)
                return old['status'], old['media_type'], old['response']
            count = db.execute(select(func.count()).select_from(requests).where(
                requests.c.attempt_id == grant['attempt'])).scalar_one()
            if count >= self.settings.max_calls_per_attempt:
                raise ServiceError('model_budget_exhausted', 429)
            db.execute(insert(requests).values(attempt_id=grant['attempt'], body_sha256=identity,
                state='admitted', created_at=time.time()))
        diagnostic_stage = 'authorize_upstream'
        upstream_status = upstream_media = None
        try:
            self.authorize(token)
            remaining = max(.1, min(600., grant['exp'] - time.time()))
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
