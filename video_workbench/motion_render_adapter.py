"""Owner-bound HTTP rendering client. Business Pods never receive K8s access.

An uncertain submission/cancellation retains the same operation ID for recovery;
neither the client nor the service invents another attempt behind the user's back.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx

from agent_service.contracts import ServiceError
from agent_service.profiles.video_motion_contract import BEAT_IDENTIFIER, IDENTIFIER
from . import media
from .continuity_snapshot import verify_motion_snapshot

UNAVAILABLE = 'motion_render_service_unavailable'
TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z')
PROGRESS_STAGES = frozenset({'preparing_scene', 'waiting_scene', 'rendering_scene',
    'observing_scene', 'validating_scene', 'normalizing_scene', 'preparing_audio',
    'compositing', 'validating_video', 'completed', 'cleanup'})


def safe_progress(value):
    """Only bounded presentation metadata crosses the renderer boundary."""
    if (not isinstance(value, dict) or not isinstance(value.get('stage'), str)
            or value['stage'] not in PROGRESS_STAGES):
        return None
    total, completed, index = (value.get(key) for key in ('total_scenes', 'completed_scenes', 'scene_index'))
    updated = value.get('updated_at')
    if (type(total) is not int or not 1 <= total <= 24 or type(completed) is not int
            or not 0 <= completed <= total or index is not None and
            (type(index) is not int or not 0 <= index < total)
            or type(updated) not in {int, float} or not 0 <= updated < 1e12):
        return None
    return dict(stage=value['stage'], scene_index=index, completed_scenes=completed,
                total_scenes=total, updated_at=updated)


def safe_motion_context(value):
    """Only a complete, bounded action identity may describe a motion failure."""
    if not isinstance(value, dict):
        return None
    target, kind, ordinal, beat = (value.get(key) for key in ('target', 'kind', 'ordinal', 'beat_id'))
    # video_motion_contract caps plans at 48 actions and validates these IDs.
    # Require the whole identity so a malformed hint cannot point at an action.
    if (not isinstance(target, str) or not IDENTIFIER.fullmatch(target)
            or not isinstance(kind, str) or kind not in {'reveal', 'hide', 'highlight', 'move', 'draw'}
            or type(ordinal) is not int or not 0 <= ordinal < 48
            or not isinstance(beat, str) or not BEAT_IDENTIFIER.fullmatch(beat)):
        return None
    return dict(target=target, kind=kind, ordinal=ordinal, beat_id=beat)


def safe_diagnostic(value):
    if not isinstance(value, dict):
        return None
    code, identity = value.get('code'), value.get('diagnostic_id')
    category, stage, index = (value.get(key) for key in ('category', 'stage', 'scene_index'))
    if (not isinstance(code, str) or not re.fullmatch(r'[a-z0-9_]{1,100}', code)
            or not isinstance(identity, str) or not re.fullmatch(r'MR-[0-9a-f]{16}', identity)
            or not isinstance(category, str)
            or category not in {'resource', 'timeout', 'artifact_budget', 'render', 'observation',
                                'validation', 'storage', 'submission', 'cancelled', 'execution'}
            or stage is not None and (not isinstance(stage, str) or stage not in PROGRESS_STAGES)
            or index is not None and (type(index) is not int or not 0 <= index < 24)):
        return None
    result = dict(code=code, category=category, stage=stage, scene_index=index, diagnostic_id=identity)
    if context := safe_motion_context(value.get('motion_context')):
        result['motion_context'] = context
    return result


def read_secret(path):
    target = Path(path)
    if not target.is_absolute() or not target.is_file() or target.stat().st_size > 4096:
        raise ValueError('motion_service_token_file_invalid')
    token = target.read_text(encoding='utf-8').strip()
    if not 32 <= len(token) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('motion_service_token_invalid')
    return token


class RemoteMotionRenderer:
    supports_progress = True

    def __init__(self, url, token, *, client=None, poll_seconds=1, wait_seconds=1800):
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('motion_service_url_invalid')
        self.url, self.token = url.rstrip('/'), token
        self.client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self.poll_seconds, self.wait_seconds = poll_seconds, wait_seconds

    def _request(self, method, path, owner, **kwargs):
        try:
            response = self.client.request(method, self.url + path, headers={
                'Authorization': 'Bearer ' + self.token,
                'X-Video-Owner': owner}, **kwargs)
        except (httpx.HTTPError, OSError):
            raise ServiceError(UNAVAILABLE, 503) from None
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            # A terminal remote failure is carried as a normal status resource,
            # not an HTTP 5xx which could disguise an uncertain transport failure.
            raise ServiceError(UNAVAILABLE, 503)
        if response.status_code >= 400:
            try:
                code = response.json().get('error', 'motion_render_request_rejected')
            except ValueError:
                code = 'motion_render_request_rejected'
            if not re.fullmatch(r'[a-z0-9_]{1,100}', str(code)):
                code = 'motion_render_request_rejected'
            raise ServiceError(code, response.status_code)
        return response

    def render(self, snapshot, output_dir, *, operation_id, owner, cancel_event=None, progress_callback=None):
        verify_motion_snapshot(snapshot)
        if not isinstance(owner, str) or not owner or not IDENTITY.fullmatch(operation_id):
            raise ServiceError('motion_render_identity_invalid', 422)
        owner_hash = hashlib.sha256(owner.encode()).hexdigest()
        root = Path(output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        media._require(not any(root.iterdir()), 'media_output_not_empty', 409)
        path = '/v1/renders/' + operation_id
        # Query before an idempotent exact-snapshot submission. Even an already
        # cancelled local caller must drain an existing remote operation.
        response = self._request('GET', path, owner_hash)
        if response is None:
            # A previous PUT may still be committing after its response was
            # lost. Bind the same ID idempotently before cancelling, rather
            # than treating a racy 404 as proof that execution cannot appear.
            response = self._request('PUT', path, owner_hash, json={'snapshot': snapshot})
        started, cancelling = time.monotonic(), False
        while True:
            if response is None:
                raise ServiceError(UNAVAILABLE, 503)
            try:
                result = response.json()
                if (result['operation_id'] != operation_id
                        or result['snapshot_sha256'] != snapshot['sha256']):
                    raise ValueError()
                state = result['state']
            except (ValueError, KeyError, TypeError):
                raise ServiceError('motion_render_response_invalid', 502) from None
            status = {}
            if progress := safe_progress(result.get('progress')):
                status['progress'] = progress
            if diagnostic := safe_diagnostic(result.get('diagnostic')):
                status['diagnostic'] = diagnostic
            if status and progress_callback is not None:
                progress_callback(status)
            wants_cancel = cancel_event is not None and cancel_event.is_set()
            if wants_cancel and not cancelling and state not in TERMINAL:
                response = self._request('POST', path + '/cancel', owner_hash)
                cancelling = True
                continue
            if state in TERMINAL:
                if wants_cancel or state == 'cancelled':
                    raise ServiceError('media_render_cancelled', 409)
                if state != 'completed':
                    code = result.get('error', 'motion_execution_failed')
                    if not re.fullmatch(r'[a-z0-9_]{1,100}', str(code)):
                        code = 'motion_execution_failed'
                    error = ServiceError(code, 504 if state == 'timed_out' else 502)
                    diagnostic = status.get('diagnostic') or {}
                    if diagnostic.get('code') == code and (context := diagnostic.get('motion_context')):
                        error.motion_context = context
                    raise error
                break
            if time.monotonic() - started >= self.wait_seconds:
                # Remote remains journalled, same ID resumes next tick.
                raise ServiceError(UNAVAILABLE, 503)
            if cancel_event is not None:
                # Event.wait returns immediately after cancellation; avoid a hot
                # loop while waiting for the remote Job's confirmed deletion.
                if wants_cancel:
                    time.sleep(self.poll_seconds)
                else:
                    cancel_event.wait(self.poll_seconds)
            else:
                time.sleep(self.poll_seconds)
            response = self._request('GET', path, owner_hash)
        metadata = result.get('result')
        if (not isinstance(metadata, dict) or type(metadata.get('bytes')) is not int
                or not 0 < metadata['bytes'] <= media.MAX_VIDEO_BYTES
                or not re.fullmatch(r'[0-9a-f]{64}', str(metadata.get('sha256', '')))
                or metadata.get('snapshot_sha256') != snapshot['sha256']):
            raise ServiceError('motion_render_response_invalid', 502)
        # Stream only the fixed, owner-scoped endpoint; never follow a URL/path
        # supplied by a renderer receipt. Bound before writing and hash again.
        destination, checksum, size = root / 'video.mp4', hashlib.sha256(), 0
        try:
            with self.client.stream('GET', self.url + path + '/video', headers={
                    'Authorization': 'Bearer ' + self.token, 'X-Video-Owner': owner_hash}) as stream:
                if stream.status_code != 200:
                    raise ServiceError(UNAVAILABLE, 503)
                with destination.open('xb') as target:
                    for chunk in stream.iter_bytes(1024 * 1024):
                        media._cancel(cancel_event)
                        size += len(chunk)
                        if size > metadata['bytes']:
                            raise ServiceError('motion_render_response_invalid', 502)
                        checksum.update(chunk)
                        target.write(chunk)
        except (httpx.HTTPError, OSError):
            destination.unlink(missing_ok=True)
            raise ServiceError(UNAVAILABLE, 503) from None
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        if size != metadata['bytes'] or not hmac.compare_digest(checksum.hexdigest(), metadata['sha256']):
            destination.unlink(missing_ok=True)
            raise ServiceError('motion_render_response_invalid', 502)
        media._cancel(cancel_event)
        return {**metadata, 'path': str(destination)}

    __call__ = render

    def close(self):
        self.client.close()


def configured_motion_renderer(environ=None):
    env = os.environ if environ is None else environ
    url, token_file = env.get('VIDEO_MOTION_RENDER_URL'), env.get('VIDEO_MOTION_RENDER_TOKEN_FILE')
    if not url and not token_file:
        return None
    if not url or not token_file:
        raise ValueError('motion_service_configuration_incomplete')
    return RemoteMotionRenderer(url, read_secret(token_file))
