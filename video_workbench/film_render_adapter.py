"""Owner-scoped whole-film client over the existing execution HTTP transport.

This adapter requires external snapshot and release identities. Neither a remote
receipt nor its embedded runtime can authorize its own input or trusted hosts.
"""
from copy import deepcopy
import hashlib
import hmac
import math
from pathlib import Path
import re
import time

import httpx

from agent_service.contracts import ServiceError
from . import media
from .hyperframes_film_snapshot import verify_film_execution_snapshot
from .motion_render_adapter import (RemoteMotionRenderer, IDENTITY, TERMINAL, UNAVAILABLE,
    safe_progress, safe_diagnostic)

_SHA = re.compile(r'[0-9a-f]{64}\Z')
_STATES = TERMINAL | {'queued', 'running', 'cancelling'}


def _require(value):
    if not value:
        raise ServiceError('film_render_response_invalid', 502)


def validate_film_render_result(metadata, snapshot, *, runtime_fingerprint):
    """Validate authenticated executor evidence against the verified input.

    Fixed receipt hashes identify executor-owned export records; they confer no
    Agent quality, human approval or adoption claim.
    """
    _require(type(metadata) is dict and type(metadata.get('bytes')) is int
        and 0 < metadata['bytes'] <= media.MAX_VIDEO_BYTES
        and type(metadata.get('sha256')) is str and _SHA.fullmatch(metadata['sha256'])
        and metadata.get('snapshot_sha256') == snapshot['sha256']
        and metadata.get('runtime_fingerprint') == runtime_fingerprint
        and metadata.get('visual_mode') == 'hyperframes-film' and metadata.get('quality') == 'draft'
        and metadata.get('video_codec') == 'h264' and metadata.get('audio_codec') == 'aac'
        and metadata.get('silent') is False and metadata.get('subtitles') is False
        and metadata.get('audio_sample_rate') == 48000 and type(metadata['audio_sample_rate']) is int
        and metadata.get('audio_channels') == 2 and type(metadata['audio_channels']) is int
        and metadata.get('width') == snapshot['width'] and type(metadata['width']) is int
        and metadata.get('height') == snapshot['height'] and type(metadata['height']) is int
        and metadata.get('fps') == 24 and type(metadata['fps']) in {int, float})
    count = snapshot['clock']['frame_count']
    for name in ('duration_seconds', 'expected_duration_seconds'):
        value = metadata.get(name)
        _require(type(value) in {int, float} and math.isfinite(value)
            and abs(value - count / 24) <= 1e-5)
    export = metadata.get('film_export')
    _require(type(export) is dict
        and export.get('bundle_sha256') == snapshot['bundle_sha256']
        and export.get('film_sha256') == snapshot['film_sha256']
        and export.get('project_manifest_sha256') == snapshot['project_manifest_sha256']
        and export.get('concatenated_scene_videos') is False
        and export.get('clock') == dict(fps=24, frame_count=count,
            sample_rate=48000, sample_count=count * 2000))
    for name in ('export_receipt_sha256', 'capture_sha256', 'audio_mix_sha256',
                 'job_result_sha256', 'job_binding', 'admission_sha256'):
        _require(type(export.get(name)) is str and _SHA.fullmatch(export[name]))
    return deepcopy(metadata)


class RemoteFilmRenderer(RemoteMotionRenderer):
    """Same journal endpoints, a distinct validated snapshot and media contract."""

    def render(self, snapshot, output_dir, *, expected_snapshot_sha256, runtime_fingerprint,
            trusted_host_sha256, image, operation_id, owner, cancel_event=None, progress_callback=None):
        snapshot = verify_film_execution_snapshot(snapshot,
            expected_snapshot_sha256=expected_snapshot_sha256, runtime_fingerprint=runtime_fingerprint,
            trusted_host_sha256=trusted_host_sha256, image=image)
        if not isinstance(owner, str) or not owner or not isinstance(operation_id, str) or not IDENTITY.fullmatch(operation_id):
            raise ServiceError('film_render_identity_invalid', 422)
        owner_hash = hashlib.sha256(owner.encode()).hexdigest()
        root = Path(output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        media._require(not any(root.iterdir()), 'media_output_not_empty', 409)
        path = '/v1/renders/' + operation_id
        response = self._request('GET', path, owner_hash)
        if response is None:
            response = self._request('PUT', path, owner_hash, json={'snapshot': snapshot})
        started, cancelling = time.monotonic(), False
        while True:
            if response is None:
                raise ServiceError(UNAVAILABLE, 503)
            try:
                _require(len(response.content) <= 512 * 1024)
                result = response.json()
                _require(type(result) is dict and result.get('operation_id') == operation_id
                    and result.get('snapshot_sha256') == expected_snapshot_sha256
                    and type(result.get('state')) is str and result['state'] in _STATES)
                state = result['state']
            except (ValueError, KeyError, TypeError):
                raise ServiceError('film_render_response_invalid', 502) from None
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
                    code = result.get('error', 'film_execution_failed')
                    if not re.fullmatch(r'[a-z0-9_]{1,100}', str(code)):
                        code = 'film_execution_failed'
                    raise ServiceError(code, 504 if state == 'timed_out' else 502)
                break
            if time.monotonic() - started >= self.wait_seconds:
                raise ServiceError(UNAVAILABLE, 503)
            if cancel_event is None or wants_cancel:
                time.sleep(self.poll_seconds)
            else:
                cancel_event.wait(self.poll_seconds)
            response = self._request('GET', path, owner_hash)
        metadata = validate_film_render_result(result.get('result'), snapshot,
            runtime_fingerprint=runtime_fingerprint)
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
                        _require(size <= metadata['bytes'])
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
            raise ServiceError('film_render_response_invalid', 502)
        try:
            media._cancel(cancel_event)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return {**metadata, 'path': str(destination)}

    __call__ = render
