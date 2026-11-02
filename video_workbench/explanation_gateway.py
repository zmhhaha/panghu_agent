"""One bounded project explanation request over the existing AgentClient.

The business store persists admission state; this adapter performs no retries,
adoption, rendering or text-to-speech. Static video wire contracts stay intact.
"""
from __future__ import annotations

import hashlib
import math
import time

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.profiles.video_explanation import (INPUT_MAX_BYTES, OUTPUT_MAX_BYTES,
    validate_explanation_input, validate_explanation_result)
from .agent import VideoAgentGateway, _decode, _finite_number, _operation, _sha, _uuid


PROFILE = 'video.explanation-plan.v1'
INPUT_NAME, INPUT_ROLE, RESULT_NAME = 'explanation-input.json', 'explanation_input', 'explanation-result.json'


def _require(condition, code='explanation_agent_protocol_invalid', status=502):
    if not condition:
        raise ServiceError(code, status)


def _key(operation_id):
    value = _operation(operation_id)
    _require(len(value) <= 116, 'invalid_explanation_operation', 422)
    return 'explanation-' + value


def _document_bytes(document):
    validate_explanation_input(document)
    raw = canonical_bytes(document)
    _require(len(raw) <= INPUT_MAX_BYTES, 'explanation_input_too_large', 413)
    return raw


def _job(document, *, job_id=None, operation_id=None):
    _require(type(document) is dict and _uuid(document.get('job_id'))
             and type(document.get('state')) is str and document['state'] in {
                 'queued', 'running', 'cancelling', 'completed', 'failed', 'needs_input',
                 'cancelled', 'timed_out', 'quarantined'}
             and (job_id is None or document['job_id'] == job_id)
             and (operation_id is None or document.get('business_ref') == 'video-explanation:' + operation_id)
             and document.get('publication_authorized') is False)
    return document


class ExplanationAgentGateway(VideoAgentGateway):
    def upload(self, document):
        raw = _document_bytes(document)
        checksum = hashlib.sha256(raw).hexdigest()
        uploaded = self._remote(self.client.upload_input, INPUT_NAME, raw)
        _require(type(uploaded) is dict and _uuid(uploaded.get('input_id'))
                 and uploaded.get('name') == INPUT_NAME and uploaded.get('sha256') == checksum
                 and type(uploaded.get('size')) is int and uploaded['size'] == len(raw))
        return dict(input_id=uploaded['input_id'], sha256=checksum, path=INPUT_NAME, role=INPUT_ROLE)

    def submit(self, operation_id, document, deadline_at, input_ref=None):
        key = _key(operation_id)
        checksum = hashlib.sha256(_document_bytes(document)).hexdigest()
        _require(type(input_ref) is dict and set(input_ref) == {'input_id', 'sha256', 'path', 'role'}
                 and _uuid(input_ref.get('input_id')) and input_ref.get('sha256') == checksum
                 and input_ref.get('path') == INPUT_NAME and input_ref.get('role') == INPUT_ROLE,
                 'invalid_explanation_input_ref', 422)
        _require(_finite_number(deadline_at) and deadline_at > time.time(), 'explanation_deadline_expired', 409)
        try:
            capabilities = self.capabilities()
        except ServiceError:
            raise ServiceError('explanation_preflight_unavailable', 503) from None
        profiles = capabilities.get('profiles') if type(capabilities) is dict else None
        matches = ([item for item in profiles if type(item) is dict and item.get('profile') == PROFILE
                    and item.get('profile_revision') == self.profile_revision]
                   if type(profiles) is list else [])
        _require(len(matches) == 1, 'explanation_profile_unavailable', 503)
        route = matches[0]
        _require(route.get('configured') is True and route.get('ready') is True
                 and route.get('supports_inputs') is True and type(route.get('max_timeout_seconds')) is int
                 and 1 <= route['max_timeout_seconds'] <= 3600, 'explanation_profile_unavailable', 503)
        remaining = deadline_at - time.time()
        _require(remaining > 0, 'explanation_deadline_expired', 409)
        request = dict(api_version='1', profile=PROFILE, profile_revision=self.profile_revision,
            business_ref='video-explanation:' + operation_id, task={'input_sha256': checksum},
            inputs=[dict(input_ref)], execution_deadline_at=deadline_at,
            timeout_seconds=min(600, route['max_timeout_seconds'], math.ceil(remaining)))
        response = self._remote(self.client.submit, request, idempotency_key=key)
        try:
            return _job(response, operation_id=operation_id)
        except ServiceError:
            # Admission may have succeeded even when its response is malformed.
            # The durable coordinator must query the original key, not resubmit.
            raise ServiceError('service_protocol_invalid', 502) from None

    def lookup(self, operation_id):
        response = self._remote(self.client.lookup_job, _key(operation_id))
        try:
            return _job(response, operation_id=operation_id)
        except ServiceError:
            raise ServiceError('service_protocol_invalid', 502) from None

    def poll(self, job_id):
        response = self._remote(self.client.get_job, job_id)
        try:
            return _job(response, job_id=job_id)
        except ServiceError:
            raise ServiceError('service_protocol_invalid', 502) from None

    def cancel(self, job_id):
        response = self._remote(self.client.cancel, job_id)
        try:
            return _job(response, job_id=job_id)
        except ServiceError:
            raise ServiceError('service_protocol_invalid', 502) from None

    def collect(self, job_id, input_sha256, input_document):
        """Verify stopped writers and immutable manifest before interpreting the proposal."""
        _require(_sha(input_sha256), 'invalid_explanation_input_ref', 422)
        job = self.poll(job_id)
        _require(job.get('state') == 'completed', 'explanation_result_not_ready', 409)
        attempt, proof, pins = job.get('attempt'), job.get('stop_proof'), job.get('pins')
        _require(type(attempt) is dict and _uuid(attempt.get('attempt_id'))
                 and type(attempt.get('fence')) is int and attempt['fence'] >= 1
                 and type(pins) is dict and pins.get('profile') == PROFILE
                 and pins.get('profile_revision') == self.profile_revision
                 and job.get('stop_proven') is True and type(proof) is dict
                 and proof.get('stop_proven') is True and proof.get('attempt_id') == attempt['attempt_id']
                 and type(proof.get('fence')) is int and proof['fence'] == attempt['fence']
                 and _sha(proof.get('request_sha256')) and _sha(proof.get('result_sha256'))
                 and type(proof.get('resource_ids')) is list and 1 <= len(proof['resource_ids']) <= 64
                 and all(type(item) is str and 1 <= len(item) <= 200
                         and all(ord(c) >= 32 and ord(c) != 127 for c in item) for item in proof['resource_ids'])
                 and len(set(proof['resource_ids'])) == len(proof['resource_ids'])
                 and _finite_number(proof.get('observed_at')) and proof['observed_at'] > 0)
        manifest = self._remote(self.client.artifact_manifest, job_id)
        _require(type(manifest) is dict and set(manifest) == {
            'schema_version', 'job_id', 'attempt_id', 'files', 'total_bytes', 'publication_authorized', 'sha256'})
        try:
            checksum = hashlib.sha256(canonical_bytes({k: v for k, v in manifest.items() if k != 'sha256'})).hexdigest()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('explanation_agent_protocol_invalid', 502) from None
        entries = manifest['files']
        _require(manifest['schema_version'] == 'agent-artifacts.v1' and manifest['job_id'] == job_id
                 and manifest['attempt_id'] == attempt['attempt_id'] and manifest['publication_authorized'] is False
                 and manifest['sha256'] == checksum and job.get('artifact_manifest_sha256') == checksum
                 and type(entries) is list and len(entries) == 1)
        item = entries[0]
        _require(type(item) is dict and set(item) == {'artifact_id', 'name', 'sha256', 'size'}
                 and _uuid(item.get('artifact_id')) and item.get('name') == RESULT_NAME
                 and _sha(item.get('sha256')) and type(item.get('size')) is int
                 and 1 <= item['size'] <= OUTPUT_MAX_BYTES and type(manifest['total_bytes']) is int
                 and manifest['total_bytes'] == item['size'])
        raw = self._remote(self.client.download_artifact, item['artifact_id'])
        _require(type(raw) is bytes and len(raw) == item['size']
                 and hashlib.sha256(raw).hexdigest() == item['sha256'], 'explanation_artifact_digest_mismatch')
        return validate_explanation_result(_decode(raw), input_sha256, input_document)
