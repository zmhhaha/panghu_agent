"""Authenticated control API. Agent execution belongs to the separate dispatcher.

The app factory has no implicit database initialization, scheduler, model calls,
or worker subprocesses. Calling services receive execution results, never a
business publication authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from .contracts import (MAX_INPUT_BYTES, ExecutionBindingPins, JobRequest, Principal, ProfileRegistry,
                        ServiceError)
from .artifacts import relative_path
from .storage import execution_fields


@dataclass(frozen=True)
class ApiSettings:
    principals: Mapping[str, Principal] = field(repr=False)
    max_request_bytes: int = 65536
    max_input_bytes: int = MAX_INPUT_BYTES

    def __post_init__(self):
        if not self.principals:
            raise ValueError('At least one service identity is required')
        copied = dict(self.principals)
        for token, principal in copied.items():
            if (not isinstance(token, str) or not 32 <= len(token.encode('utf-8')) <= 512
                    or any(ord(c) < 33 or ord(c) > 126 for c in token)
                    or not isinstance(principal, Principal)
                    or not principal.tenant or not principal.caller
                    or len(principal.tenant) > 128 or len(principal.caller) > 128
                    or not isinstance(principal.profiles, frozenset)):
                raise ValueError('Invalid configured service identity')
        if (type(self.max_request_bytes) is not int or not 1024 <= self.max_request_bytes <= 4 * 1024 * 1024
                or type(self.max_input_bytes) is not int or not 1 <= self.max_input_bytes <= MAX_INPUT_BYTES):
            raise ValueError('Invalid API request bounds')
        object.__setattr__(self, 'principals', MappingProxyType(copied))


def _error(code: str, status: int):
    return JSONResponse({'detail': {'code': code}}, status_code=status,
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def _id(value: str):
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ServiceError('not_found', 404) from None
    return value


def _usage(value):
    """Only bounded accounting fields cross the business-facing API."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    allowed_counts = {'input_tokens', 'output_tokens', 'cache_read_tokens',
                      'cache_write_tokens', 'request_count'}
    result = {k: v for k, v in value.items()
              if k in allowed_counts and (v is None or type(v) is int and 0 <= v <= 2**63 - 1)}
    for key in ('cost_status', 'completeness'):
        if value.get(key) in {'unknown', 'known', 'partial', 'complete'}:
            result[key] = value[key]
    for key in ('model', 'provider', 'currency'):
        val = value.get(key)
        if isinstance(val, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', val):
            result[key] = val
    cost = value.get('reported_cost')
    if cost is None or type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
        result['reported_cost'] = cost
    return result or None


def public_job(job: dict) -> dict:
    outcome = job.get('outcome') or {}
    pins = job.get('pins') or {}
    visible_pins = {k: pins[k] for k in ('profile', 'profile_revision', 'engine',
                                       'runtime_revision', 'image_digest') if k in pins}
    visible_pins.update(execution_fields(pins))
    if 'execution_binding' in pins:
        try:
            binding = ExecutionBindingPins.model_validate(pins['execution_binding'])
            if not binding.matches_base(pins):
                raise ValueError('Execution binding disagrees with stored pins')
        except (ValidationError, ValueError, TypeError):
            raise ServiceError('invalid_execution_pins', 503) from None
        # The strict metadata model excludes credentials and arbitrary nested
        # configuration; the business caller receives every pinned identifier.
        visible_pins['execution_binding'] = binding.model_dump(mode='json')
    proof = _public_stop_proof(job, outcome)
    return {
        'api_version': '1', 'job_id': job.get('job_id', job.get('id')),
        'state': job['state'],
        'pins': visible_pins,
        'business_ref': job['request']['business_ref'],
        'created_at': job['created_at'], 'deadline_at': job['deadline_at'],
        'event_cursor': job.get('event_cursor', 0),
        'attempt': ({'attempt_id': job['attempt_id'], 'fence': job['fence']}
                    if job.get('attempt_id') else None),
        'error_code': (outcome.get('error_code') if isinstance(outcome.get('error_code'), str)
                       and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', outcome['error_code']) else None),
        'usage': _usage(outcome.get('usage')), 'publication_authorized': False,
        'stop_proven': proof['stop_proven'], 'stop_proof': proof,
        'not_started': proof.get('not_started', False),
        'artifact_manifest_sha256': outcome.get('_service_artifact_manifest_sha256'),
        'checkpoint_ref': job['request'].get('checkpoint_ref'),
    }


def _public_stop_proof(job, outcome):
    receipt = outcome.get('stop_receipt')
    stopped = (outcome.get('_service_stop_proven') is True
        and job['state'] in {'completed', 'needs_input', 'failed', 'cancelled', 'timed_out'}
        and isinstance(receipt, dict) and receipt.get('job_id') == job.get('job_id', job.get('id'))
        and receipt.get('attempt_id') == job.get('attempt_id') and receipt.get('fence') == job.get('fence')
        and receipt.get('engine') == job.get('pins', {}).get('engine'))
    if stopped:
        stopped = (all(isinstance(receipt.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', receipt[key])
                       for key in ('request_sha256', 'result_sha256'))
            and type(receipt.get('resource_ids')) is list and 1 <= len(receipt['resource_ids']) <= 64
            and all(type(item) is str and 1 <= len(item) <= 200
                    and all(ord(char) >= 32 and ord(char) != 127 for char in item)
                    for item in receipt['resource_ids'])
            and type(receipt.get('observed_at')) in (int, float)
            and math.isfinite(receipt['observed_at']) and receipt['observed_at'] > 0)
    if not stopped:
        return dict(stop_proven=False, not_started=outcome.get('_service_not_started') is True
                    or job['state'] in {'cancelled', 'timed_out'}
                    and job.get('attempt_id') is None and job.get('fence', 0) == 0)
    return dict(stop_proven=True, **{key: receipt[key] for key in
        ('attempt_id', 'fence', 'request_sha256', 'result_sha256', 'resource_ids', 'observed_at')})


def _public_event(item: dict):
    payload = item.get('payload') or {}
    safe = {}
    for key in ('state', 'error_code', 'engine', 'event', 'phase', 'code'):
        value = payload.get(key)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value):
            safe[key] = value
    if type(payload.get('count')) is int and 0 <= payload['count'] <= 2**31 - 1:
        safe['count'] = payload['count']
    return {'seq': item['seq'], 'type': item['type'], 'payload': safe,
            'created_at': item['created_at']}


def _input_metadata(value):
    return {key: value[key] for key in ('input_id', 'name', 'sha256', 'size', 'created_at')}


def _artifact_metadata(value):
    return {key: value[key] for key in ('artifact_id', 'name', 'sha256', 'size', 'job_id', 'attempt_id',
                                       'available') if key in value}


async def _bounded_body(request: Request, limit: int):
    length = request.headers.get('content-length')
    if length is not None:
        try:
            if not re.fullmatch(r'[0-9]{1,12}', length) or int(length) > limit:
                raise ServiceError('request_too_large', 413)
        except ValueError:
            raise ServiceError('invalid_request', 400) from None
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                raise ServiceError('request_too_large', 413)
            body.extend(chunk)
    except ClientDisconnect:
        raise ServiceError('request_disconnected', 400) from None
    return bytes(body)


def _json(raw):
    def object_pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate key')
            value[key] = item
        return value
    def constant(_):
        raise ValueError('Nonfinite JSON is unsupported')
    try:
        result = json.loads(raw, object_pairs_hook=object_pairs, parse_constant=constant)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError('invalid_request', 422) from None


def _ready(adapter, policy):
    if adapter is None:
        return False
    try:
        readiness = getattr(adapter, 'ready', False)
        return (readiness(policy) if callable(readiness) else readiness) is True
    except Exception:
        # Endpoint/credentials/native error bodies must not enter public errors.
        return False


def create_app(settings: ApiSettings, store, registry: ProfileRegistry,
               adapters: Mapping[str, Any] | None = None) -> FastAPI:
    if not isinstance(settings, ApiSettings) or not isinstance(registry, ProfileRegistry):
        raise TypeError('Trusted API settings and profile registry are required')
    adapters = MappingProxyType(dict(adapters or {}))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.registry = settings, store, registry

    async def authorized_job(principal: Principal, identifier: str):
        job = await run_in_threadpool(store.get_job, principal.tenant, principal.caller, _id(identifier))
        # Historical jobs keep their original revision even after a route update;
        # current identity scope still controls access to their results.
        if job['request']['profile'] not in principal.profiles:
            raise ServiceError('not_found', 404)
        return job

    @app.middleware('http')
    async def identity(request: Request, call_next):
        if request.method == 'GET' and request.url.path == '/healthz':
            return await call_next(request)
        supplied = request.headers.get('authorization', '').encode('utf-8')
        principal = None
        for token, configured in settings.principals.items():
            if hmac.compare_digest(supplied, ('Bearer ' + token).encode('utf-8')):
                principal = configured
        if principal is None:
            return _error('authentication_required', 401)
        if request.headers.get('content-encoding', 'identity').lower() != 'identity':
            return _error('unsupported_content_encoding', 415)
        request.state.principal = principal
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return _error(exc.code, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return _error('invalid_request', 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return _error({404: 'not_found', 405: 'method_not_allowed'}.get(exc.status_code, 'invalid_request'),
                      exc.status_code)

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        return _error('service_unavailable', 503)

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/v1/capabilities')
    async def capabilities(request: Request):
        values = registry.capabilities(request.state.principal)
        for item in values:
            engine = item['engine']
            policy = registry.policy(item['profile'], item['profile_revision'])
            item['ready'] = item['configured'] and await run_in_threadpool(_ready, adapters.get(engine), policy)
            if getattr(adapters.get(engine), 'provisioning', None) == 'kubernetes_per_attempt':
                item['provisioning'] = 'kubernetes_per_attempt'
                item['readiness_basis'] = 'pool_admission'
                item['requires_native_readiness_check'] = True
            item['cancel'] = item['ready']
            item['native_fork'] = False
            item['steer'] = False
        return {'api_version': '1', 'profiles': values}

    @app.post('/v1/inputs', status_code=201)
    async def upload_input(request: Request, name: str = Query(...)):
        try:
            relative_path(name)
        except (ValueError, ServiceError):
            raise ServiceError('invalid_file_name', 422) from None
        if request.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/octet-stream':
            raise ServiceError('unsupported_content_type', 415)
        data = await _bounded_body(request, settings.max_input_bytes)
        principal = request.state.principal
        saved = await run_in_threadpool(store.put_input, principal.tenant, principal.caller, name, data)
        return _input_metadata(saved)

    @app.get('/v1/inputs/{input_id}')
    async def get_input(request: Request, input_id: str):
        principal = request.state.principal
        value = await run_in_threadpool(store.get_input, principal.tenant, principal.caller, _id(input_id))
        return _input_metadata(value)

    @app.get('/v1/inputs/{input_id}/content')
    async def input_content(request: Request, input_id: str):
        principal = request.state.principal
        value = await run_in_threadpool(store.get_input, principal.tenant, principal.caller, _id(input_id))
        return Response(value['content'], media_type='application/octet-stream',
                        headers={'ETag': '"' + value['sha256'] + '"'})

    @app.delete('/v1/inputs/{input_id}')
    async def delete_input(request: Request, input_id: str):
        principal = request.state.principal
        return await run_in_threadpool(store.delete_input, principal.tenant, principal.caller, _id(input_id))

    @app.get('/v1/jobs/by-idempotency/{key:path}')
    async def lookup_job(request: Request, key: str):
        principal = request.state.principal
        job = await run_in_threadpool(store.lookup_job, principal.tenant, principal.caller, key)
        if job['request']['profile'] not in principal.profiles:
            raise ServiceError('not_found', 404)
        return public_job(job)

    @app.post('/v1/jobs')
    async def submit(request: Request):
        key = request.headers.get('idempotency-key', '')
        if not key or len(key) > 128 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ServiceError('idempotency_key_required', 422)
        if request.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/json':
            raise ServiceError('unsupported_content_type', 415)
        body = _json(await _bounded_body(request, settings.max_request_bytes))
        try:
            parsed = JobRequest.model_validate(body)
        except (ValidationError, ValueError, RecursionError):
            raise ServiceError('invalid_request', 422) from None
        principal = request.state.principal
        if parsed.profile not in principal.profiles:
            raise ServiceError('profile_unavailable', 404)
        wire = parsed.model_dump()
        replay = await run_in_threadpool(store.replay, principal.tenant, principal.caller, key, wire)
        if replay is not None:
            return JSONResponse(public_job(replay), status_code=200)
        profile = registry.resolve(principal, parsed)
        if not await run_in_threadpool(_ready, adapters.get(profile.engine), profile):
            raise ServiceError('worker_not_ready', 503)
        job, created = await run_in_threadpool(store.submit, principal.tenant, principal.caller,
                                             key, wire, profile.pins())
        return JSONResponse(public_job(job), status_code=202 if created else 200)

    @app.get('/v1/jobs/{job_id}')
    async def get_job(request: Request, job_id: str):
        principal = request.state.principal
        job = await authorized_job(principal, job_id)
        return public_job(job)

    @app.get('/v1/jobs/{job_id}/events')
    async def events(request: Request, job_id: str, after: int = Query(0, ge=0, le=2**63 - 1),
                     limit: int = Query(100, ge=1, le=100)):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        page = await run_in_threadpool(store.events, principal.tenant, principal.caller,
                                       _id(job_id), after, limit)
        return {**page, 'items': [_public_event(item) for item in page['items']]}

    @app.post('/v1/jobs/{job_id}/cancel')
    async def cancel(request: Request, job_id: str):
        raw = await _bounded_body(request, 1024)
        if raw and _json(raw) != {}:
            raise ServiceError('invalid_request', 422)
        principal = request.state.principal
        job = await run_in_threadpool(store.cancel, principal.tenant, principal.caller, _id(job_id))
        return public_job(job)

    @app.get('/v1/jobs/{job_id}/artifacts')
    async def artifacts(request: Request, job_id: str):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        values = await run_in_threadpool(store.get_artifacts, principal.tenant, principal.caller, _id(job_id))
        return {'items': [_artifact_metadata(value) for value in values]}

    @app.get('/v1/jobs/{job_id}/artifact-manifest')
    async def artifact_manifest(request: Request, job_id: str):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        return await run_in_threadpool(store.get_artifact_manifest, principal.tenant, principal.caller, _id(job_id))

    @app.delete('/v1/artifacts/{artifact_id}')
    async def delete_artifact(request: Request, artifact_id: str):
        principal = request.state.principal
        return await run_in_threadpool(store.delete_artifact, principal.tenant, principal.caller, _id(artifact_id))

    @app.post('/v1/jobs/{job_id}/checkpoints')
    async def create_checkpoint(request: Request, job_id: str):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        raw = await _bounded_body(request, 256 * 1024)
        body = _json(raw) if raw else {}
        if set(body) - {'artifact_ids', 'format'}:
            raise ServiceError('invalid_checkpoint_request', 422)
        result, created = await run_in_threadpool(store.create_checkpoint, principal.tenant,
            principal.caller, _id(job_id), request.headers.get('idempotency-key', ''),
            artifact_ids=body.get('artifact_ids'), format=body.get('format', 'workspace-tree.v1'))
        return JSONResponse(result, status_code=201 if created else 200)

    @app.get('/v1/checkpoints/{checkpoint_id}')
    async def get_checkpoint(request: Request, checkpoint_id: str):
        principal = request.state.principal
        result = await run_in_threadpool(store.get_checkpoint, principal.tenant, principal.caller, _id(checkpoint_id))
        await authorized_job(principal, result['source_job_id'])
        return result

    @app.delete('/v1/checkpoints/{checkpoint_id}')
    async def delete_checkpoint(request: Request, checkpoint_id: str):
        principal = request.state.principal
        return await run_in_threadpool(store.delete_checkpoint, principal.tenant, principal.caller, _id(checkpoint_id))

    @app.post('/v1/jobs/{job_id}/retention/release')
    async def release_retention(request: Request, job_id: str):
        principal = request.state.principal
        body = _json(await _bounded_body(request, 1024))
        if set(body) - {'inputs', 'artifacts'}:
            raise ServiceError('invalid_retention_request', 422)
        return await run_in_threadpool(store.release_job_resources, principal.tenant, principal.caller,
            _id(job_id), inputs=body.get('inputs', True), artifacts=body.get('artifacts', True))

    @app.get('/v1/jobs/{job_id}/recovery')
    async def recovery(request: Request, job_id: str):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        return await run_in_threadpool(store.recovery, principal.tenant, principal.caller, _id(job_id))

    @app.put('/v1/jobs/{job_id}/recovery')
    async def save_recovery(request: Request, job_id: str):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        body = _json(await _bounded_body(request, 65536))
        if set(body) - {'document', 'expected_revision'} or type(body.get('document')) is not dict:
            raise ServiceError('invalid_recovery_document', 422)
        return await run_in_threadpool(store.save_recovery, principal.tenant, principal.caller,
            _id(job_id), body['document'], expected_revision=body.get('expected_revision'))

    @app.post('/v1/jobs/{job_id}/publication-ack')
    async def publication_ack(request: Request, job_id: str):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        body = _json(await _bounded_body(request, 4096))
        result, created = await run_in_threadpool(store.acknowledge_publication,
            principal.tenant, principal.caller, _id(job_id),
            request.headers.get('idempotency-key', ''), body)
        return JSONResponse(result, status_code=201 if created else 200)

    @app.get('/v1/jobs/{job_id}/publication-ack')
    async def publication_acknowledgements(request: Request, job_id: str):
        principal = request.state.principal
        await authorized_job(principal, job_id)
        values = await run_in_threadpool(store.publication_acknowledgements,
                                       principal.tenant, principal.caller, _id(job_id))
        return {'items': values, 'publication_authorized': False}

    @app.get('/v1/artifacts/{artifact_id}/content')
    async def artifact_content(request: Request, artifact_id: str):
        principal = request.state.principal
        value = await run_in_threadpool(store.get_artifact, principal.tenant, principal.caller, _id(artifact_id))
        await authorized_job(principal, value['job_id'])
        return Response(value['content'], media_type='application/octet-stream', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(value['name'], safe=''),
            'ETag': '"' + value['sha256'] + '"',
        })

    return app
