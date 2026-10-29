"""Bounded video jobs over the general Agent API, without implicit retries.

The caller owns durable admission state. Save an operation before ``upload``;
save its returned input reference and admission claim before ``submit``. After
an ambiguous submission response, use ``lookup`` instead of submitting again.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Literal
from uuid import UUID

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes


VideoKind = Literal['storyboard', 'scene']
INPUT_NAME = 'video-input.json'
INPUT_ROLE = 'video_input'
RESULT_NAME = 'video-result.json'
MAX_INPUT_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r'[0-9a-f]{64}')
_REVISION = re.compile(r'[A-Za-z0-9_.-]{1,100}')


def _require(condition, code='video_agent_protocol_invalid', status=502):
    if not condition:
        raise ServiceError(code, status)


def _sha(value):
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _kind(value):
    _require(type(value) is str and value in {'storyboard', 'scene'}, 'invalid_video_kind', 422)
    return value


def _operation(value):
    _require(type(value) is str and 1 <= len(value) <= 122
             and all(33 <= ord(char) <= 126 for char in value), 'invalid_video_operation', 422)
    return value


def _document_bytes(document):
    _require(type(document) is dict, 'invalid_video_input', 422)
    try:
        raw = canonical_bytes(document)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceError('invalid_video_input', 422) from None
    _require(len(raw) <= MAX_INPUT_BYTES, 'video_input_too_large', 413)
    return raw


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('Non-finite JSON number')

    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceError('video_agent_output_invalid', 502) from None
    _require(type(result) is dict, 'video_agent_output_invalid')
    return result


class VideoAgentGateway:
    """Keep the generic execution service separate from business persistence."""

    def __init__(self, client: AgentClient, profile_revision='1'):
        if type(profile_revision) is not str or not _REVISION.fullmatch(profile_revision):
            raise ValueError('A configured video profile revision is required')
        self.client = client
        self.profile_revision = profile_revision

    def capabilities(self) -> dict:
        return self.client.capabilities()

    def upload(self, document: dict) -> dict:
        """Upload exact canonical bytes; the caller persists this returned ref."""
        raw = _document_bytes(document)
        checksum = hashlib.sha256(raw).hexdigest()
        uploaded = self.client.upload_input(INPUT_NAME, raw)
        _require(type(uploaded) is dict and _uuid(uploaded.get('input_id'))
                 and uploaded.get('name') == INPUT_NAME and uploaded.get('sha256') == checksum
                 and type(uploaded.get('size')) is int and uploaded['size'] == len(raw))
        return dict(input_id=uploaded['input_id'], sha256=checksum,
                    path=INPUT_NAME, role=INPUT_ROLE)

    def submit(self, operation_id: str, kind: VideoKind, document: dict,
               deadline_at: float, input_ref: dict | None = None) -> dict:
        """Submit once, only with an already persisted, matching input ref."""
        operation_id, kind = _operation(operation_id), _kind(kind)
        raw = _document_bytes(document)
        checksum = hashlib.sha256(raw).hexdigest()
        _require(type(input_ref) is dict
                 and set(input_ref) == {'input_id', 'sha256', 'path', 'role'}
                 and _uuid(input_ref.get('input_id')) and input_ref.get('sha256') == checksum
                 and input_ref.get('path') == INPUT_NAME and input_ref.get('role') == INPUT_ROLE,
                 'invalid_video_input_ref', 422)
        _require(_finite_number(deadline_at) and deadline_at > 0, 'invalid_video_deadline', 422)
        remaining = deadline_at - time.time()
        _require(remaining > 0, 'video_deadline_expired', 409)
        profile = 'video.' + kind + '.v1'
        try:
            capabilities = self.capabilities()
        except ServiceError:
            # This read failed before the paid admission POST was attempted.
            raise ServiceError('video_preflight_unavailable', 503) from None
        profiles = capabilities.get('profiles') if type(capabilities) is dict else None
        matches = ([item for item in profiles if type(item) is dict
                    and item.get('profile') == profile
                    and item.get('profile_revision') == self.profile_revision]
                   if type(profiles) is list else [])
        _require(len(matches) == 1, 'video_profile_unavailable', 503)
        route = matches[0]
        _require(route.get('configured') is True and route.get('ready') is True
                 and route.get('supports_inputs') is True
                 and type(route.get('max_timeout_seconds')) is int
                 and 1 <= route['max_timeout_seconds'] <= 3600,
                 'video_profile_unavailable', 503)
        # An absolute deadline remains fixed even when submission itself is slow.
        remaining = deadline_at - time.time()
        _require(remaining > 0, 'video_deadline_expired', 409)
        request = dict(api_version='1', profile=profile, profile_revision=self.profile_revision,
                       business_ref='video:' + operation_id, task={'input_sha256': checksum},
                       inputs=[dict(input_ref)], execution_deadline_at=deadline_at,
                       timeout_seconds=min(600, route['max_timeout_seconds'], math.ceil(remaining)))
        return self.client.submit(request, idempotency_key='video-' + operation_id)

    def lookup(self, operation_id: str) -> dict:
        """Read-only recovery of an admission whose POST response was lost."""
        return self.client.lookup_job('video-' + _operation(operation_id))

    def poll(self, job_id: str) -> dict:
        return self.client.get_job(job_id)

    def cancel(self, job_id: str) -> dict:
        return self.client.cancel(job_id)

    def collect(self, job_id: str, kind: VideoKind, input_sha256: str) -> dict:
        """Independently validate the service's stopped, hash-bound candidate."""
        kind = _kind(kind)
        _require(_sha(input_sha256), 'invalid_video_input_ref', 422)
        job = self.poll(job_id)
        _require(job.get('state') == 'completed', 'video_result_not_ready', 409)
        attempt, proof, pins = job.get('attempt'), job.get('stop_proof'), job.get('pins')
        _require(job.get('job_id') == job_id and _uuid(job_id)
                 and type(attempt) is dict and _uuid(attempt.get('attempt_id'))
                 and type(attempt.get('fence')) is int and attempt['fence'] >= 1
                 and type(pins) is dict and pins.get('profile') == 'video.' + kind + '.v1'
                 and pins.get('profile_revision') == self.profile_revision
                 and job.get('publication_authorized') is False
                 and job.get('stop_proven') is True and type(proof) is dict
                 and proof.get('stop_proven') is True
                 and proof.get('attempt_id') == attempt['attempt_id']
                 and type(proof.get('fence')) is int and proof['fence'] == attempt['fence']
                 and _sha(proof.get('request_sha256')) and _sha(proof.get('result_sha256'))
                 and type(proof.get('resource_ids')) is list
                 and 1 <= len(proof['resource_ids']) <= 64
                 and all(type(item) is str and 1 <= len(item) <= 200
                         and all(ord(char) >= 32 and ord(char) != 127 for char in item)
                         for item in proof['resource_ids'])
                 and len(set(proof['resource_ids'])) == len(proof['resource_ids'])
                 and _finite_number(proof.get('observed_at')) and proof['observed_at'] > 0)
        manifest = self.client.artifact_manifest(job_id)
        _require(type(manifest) is dict and set(manifest) == {
            'schema_version', 'job_id', 'attempt_id', 'files', 'total_bytes',
            'publication_authorized', 'sha256'})
        body = {key: value for key, value in manifest.items() if key != 'sha256'}
        try:
            checksum = hashlib.sha256(canonical_bytes(body)).hexdigest()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('video_agent_protocol_invalid', 502) from None
        entries = manifest['files']
        _require(manifest['schema_version'] == 'agent-artifacts.v1'
                 and manifest['job_id'] == job_id and manifest['attempt_id'] == attempt['attempt_id']
                 and manifest['publication_authorized'] is False
                 and manifest['sha256'] == checksum and job.get('artifact_manifest_sha256') == checksum
                 and type(entries) is list and len(entries) == 1)
        item = entries[0]
        # The real Jobs manifest has names, not Agent-controlled path/role aliases.
        _require(type(item) is dict and set(item) == {'artifact_id', 'name', 'sha256', 'size'}
                 and _uuid(item.get('artifact_id')) and item.get('name') == RESULT_NAME
                 and _sha(item.get('sha256')) and type(item.get('size')) is int
                 and 1 <= item['size'] <= MAX_RESULT_BYTES
                 and type(manifest['total_bytes']) is int and manifest['total_bytes'] == item['size'])
        raw = self.client.download_artifact(item['artifact_id'])
        _require(type(raw) is bytes and len(raw) == item['size']
                 and hashlib.sha256(raw).hexdigest() == item['sha256'], 'video_artifact_digest_mismatch')
        from agent_service.profiles.video import validate_video_result

        return validate_video_result(_decode(raw), input_sha256, kind)
