"""Machine access to the owner's picture and video learning libraries."""

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from .animation_creation_http import CreationTransportError, OWNER, _json
from .picture_learning_http import MAX_BODY as PICTURE_MAX_BODY, PREFIX as PICTURE_PREFIX
from .video_learning_http import MAX_BODY as VIDEO_MAX_BODY, PREFIX as VIDEO_PREFIX

_SUBJECTS = frozenset({'service:dsh', 'service:hermes', 'service:pi'})


def _error(code, status):
    return HTTPException(status, detail={'code': code})


async def _read_body(request, limit):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise _error('learning_json_required', 415)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise _error('learning_body_too_large', 413)
        raw.extend(chunk)
    try:
        body = _json(raw)
    except (ValueError, RecursionError):
        raise _error('learning_json_invalid', 400) from None
    if type(body) is not dict:
        raise _error('learning_json_object_required', 422)
    return body


def _public_links(value, private_prefix, public_prefix):
    if isinstance(value, list):
        return [_public_links(row, private_prefix, public_prefix) for row in value]
    if isinstance(value, dict):
        return {
            key: public_prefix + row[len(private_prefix):]
            if (key == 'url' or key.endswith('_url')) and isinstance(row, str) and row.startswith(private_prefix + '/')
            else _public_links(row, private_prefix, public_prefix)
            for key, row in value.items()
        }
    return value


def register_machine_learning_routes(app, *, principal, client, owner):
    if not owner:
        return
    if not OWNER.fullmatch(owner) or owner.startswith('service:'):
        raise ValueError('VIDEO_LEARNING_OWNER must be the stable animation lab user ID')

    def authorized(authenticated=Depends(principal)):
        if authenticated.auth_kind != 'service' or authenticated.subject not in _SUBJECTS:
            raise _error('learning_access_forbidden', 403)
        if client is None:
            raise _error('learning_executor_unavailable', 503)

    def register_library(library, kind, private_prefix, body_limit):
        prefix = f'/api/learning/{library}'
        public_prefix = f'/api/v1/learning/{library}'

        def route(method, suffix, operation, *, status=200, binary=False, action=None):
            async def endpoint(request: Request):
                args = list(request.path_params.values())
                if action:
                    args.append(action)
                if method == 'POST':
                    body = await _read_body(request, body_limit)
                    if action == 'experiments' and (body.get('mode') == 'transfer' or 'approve_candidate' in body):
                        raise _error('learning_approval_required', 403)
                    args.append(body)
                try:
                    result = await run_in_threadpool(getattr(client, f'{kind}_{operation}'), owner, *args)
                except CreationTransportError as error:
                    raise _error(error.code, error.status) from None
                if binary:
                    raw, mime = result
                    return Response(raw, media_type=mime, headers={'Content-Disposition': 'attachment'})
                return JSONResponse(_public_links(result, private_prefix, public_prefix), status_code=status)

            app.add_api_route(prefix + suffix, endpoint, methods=[method], status_code=status,
                              name=f'{kind}_{method}_{suffix}', dependencies=[Depends(authorized)])

        route('GET', '/catalog', 'catalogue')
        route('GET', '/references/{reference_id}/media', 'reference', binary=True)
        route('GET', '/projects/{project_id}', 'get')
        route('GET', '/projects/{project_id}/experiments/{experiment_id}/input', 'inspect', binary=True)
        route('GET', '/projects/{project_id}/artifacts/{artifact_id}', 'artifact', binary=True)
        route('POST', '/references', 'upload', status=201)
        route('POST', '/projects', 'start', status=202)
        route('POST', '/projects/{project_id}/experiments', 'action', status=202, action='experiments')
        route('POST', '/projects/{project_id}/stop', 'action', action='stop')
        if kind == 'video':
            analysis = '/references/{reference_id}/analyses'
            route('POST', analysis, 'analyze', status=202)
            route('GET', analysis + '/{analysis_id}', 'analysis')
            route('GET', analysis + '/{analysis_id}/frames/{frame_sha256}', 'analysis_frame', binary=True)
            route('POST', analysis + '/{analysis_id}/stop', 'analysis_stop')

    register_library('pictures', 'picture', PICTURE_PREFIX, PICTURE_MAX_BODY)
    register_library('videos', 'video', VIDEO_PREFIX, VIDEO_MAX_BODY)
