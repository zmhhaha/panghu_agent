"""Versioned visual/create jobs over the generic Agent's stopped-artifact API.

Upload is separate from a single paid submission. Durable callers must claim
their ledger slot before submit, and reconcile the same operation after any
uncertain response. This adapter never retries, renders or approves a result.
"""
from __future__ import annotations

import hashlib
import math
import time

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.profiles import video_animation_review as visual
from agent_service.profiles import video_animation_create as creation
from .agent import _finite_number, _sha, _uuid
from .motion_gateway import MotionAgentGateway
from .animation_observation import MAX_FRAME_TOTAL_BYTES


def _require(condition, code='animation_agent_protocol_invalid', status=502):
    if not condition:
        raise ServiceError(code, status)


class _AnimationGateway(MotionAgentGateway):
    """Keep shared stop/manifest verification while supporting all input refs."""
    visual_input = False

    def _files(self, document, files=None):
        raw = self._document_bytes(document)
        expected = {self.input_name: raw}
        if self.visual_input:
            _require(type(files) is dict, 'animation_frame_inputs_required', 422)
            total = 0
            for frame in document['observation']['frames']:
                name = frame['file_name']
                content = files.get(name)
                _require(type(content) is bytes and 1 <= len(content) <= visual.MAX_IMAGE_BYTES
                         and hashlib.sha256(content).hexdigest() == frame['sha256'],
                         'animation_frame_integrity', 422)
                visual._image_header(content, name)
                expected[name] = content
                total += len(content)
            _require(total <= MAX_FRAME_TOTAL_BYTES, 'animation_frame_inputs_too_large', 413)
        if files is not None:
            _require(type(files) is dict and files == expected, 'animation_input_files_changed', 422)
        return expected

    def upload(self, document, files=None):
        """Upload every immutable file before a single job POST is possible."""
        content = self._files(document, files)
        refs = []
        for name, raw in content.items():
            checksum = hashlib.sha256(raw).hexdigest()
            uploaded = self._remote(self.client.upload_input, name, raw)
            _require(type(uploaded) is dict and _uuid(uploaded.get('input_id'))
                     and uploaded.get('name') == name and uploaded.get('sha256') == checksum
                     and type(uploaded.get('size')) is int and uploaded['size'] == len(raw))
            refs.append(dict(input_id=uploaded['input_id'], sha256=checksum, path=name,
                             role=self.input_role if name == self.input_name else 'animation_frame'))
        _require(len({ref['input_id'] for ref in refs}) == len(refs), 'animation_input_ref_duplicate')
        return refs

    def preflight(self, deadline_at):
        _require(_finite_number(deadline_at) and deadline_at > time.time(),
                 'animation_deadline_expired', 409)
        try:
            capabilities = self.capabilities()
        except ServiceError:
            raise ServiceError('animation_preflight_unavailable', 503) from None
        profiles = capabilities.get('profiles') if type(capabilities) is dict else None
        matches = ([row for row in profiles if type(row) is dict
                    and row.get('profile') == self.profile and row.get('profile_revision') == self.profile_revision]
                   if type(profiles) is list else [])
        _require(len(matches) == 1, 'animation_profile_unavailable', 503)
        route = matches[0]
        _require(route.get('configured') is True and route.get('ready') is True
                 and route.get('supports_inputs') is True and type(route.get('max_timeout_seconds')) is int
                 and 1 <= route['max_timeout_seconds'] <= 3600, 'animation_profile_unavailable', 503)
        required = route.get('required_model_capabilities')
        expected = {'text', 'tool_calls', *({'vision'} if self.visual_input else set())}
        _require(type(required) is list and all(type(item) is str for item in required)
                 and expected <= set(required), 'animation_model_capability_unavailable', 503)
        return route

    def prepare_submission(self, operation_id, document, deadline_at, input_refs=None):
        """Validate and read readiness before the durable admission claim.

        The returned callable performs one job POST. A preparation outage has
        not attempted admission and must not strand a ledger in unknown state.
        The remote API still checks its authoritative route at that POST.
        """
        key = self._operation_key(operation_id)
        raw = self._document_bytes(document)
        expected = {self.input_name: hashlib.sha256(raw).hexdigest()}
        if self.visual_input:
            expected.update({row['file_name']: row['sha256'] for row in document['observation']['frames']})
        _require(type(input_refs) is list and len(input_refs) == len(expected),
                 'invalid_animation_input_refs', 422)
        seen, identities = set(), set()
        for ref in input_refs:
            _require(type(ref) is dict and set(ref) == {'input_id', 'sha256', 'path', 'role'}
                     and _uuid(ref.get('input_id')) and type(ref.get('path')) is str
                     and ref['path'] in expected and ref['sha256'] == expected[ref['path']]
                     and ref['role'] == (self.input_role if ref['path'] == self.input_name else 'animation_frame')
                     and ref['path'] not in seen and ref['input_id'] not in identities,
                     'invalid_animation_input_refs', 422)
            seen.add(ref['path'])
            identities.add(ref['input_id'])
        route = self.preflight(deadline_at)
        remaining = deadline_at - time.time()
        _require(remaining > 0, 'animation_deadline_expired', 409)
        request = dict(api_version='1', profile=self.profile, profile_revision=self.profile_revision,
            business_ref=self.business_prefix + operation_id,
            task=dict(input_sha256=hashlib.sha256(raw).hexdigest()), inputs=[dict(ref) for ref in input_refs],
            execution_deadline_at=deadline_at,
            timeout_seconds=min(600, route['max_timeout_seconds'], math.ceil(remaining)))
        def admit():
            response = self._remote(self.client.submit, request, idempotency_key=key)
            try:
                return self._checked_job(response, operation_id=operation_id)
            except ServiceError:
                raise ServiceError('service_protocol_invalid', 502) from None
        return admit

    def submit(self, operation_id, document, deadline_at, input_refs=None):
        return self.prepare_submission(operation_id, document, deadline_at, input_refs)()

    def model_frame_receipt(self, job_id):
        """Read control-plane evidence, bound to the completed stopped attempt.

        Matching actual image SHAs to the manifest remains the business adapter's
        responsibility. Neither model output nor supplied-frame lists are receipts.
        """
        _require(self.visual_input, 'animation_visual_profile_required', 422)
        job = self.poll(job_id)
        attempt, proof, pins = job.get('attempt'), job.get('stop_proof'), job.get('pins')
        _require(job.get('state') == 'completed' and job.get('stop_proven') is True
                 and type(attempt) is dict and _uuid(attempt.get('attempt_id'))
                 and type(attempt.get('fence')) is int and attempt['fence'] >= 1
                 and type(pins) is dict and pins.get('profile') == self.profile
                 and pins.get('profile_revision') == self.profile_revision
                 and type(proof) is dict and proof.get('stop_proven') is True
                 and proof.get('attempt_id') == attempt['attempt_id']
                 and type(proof.get('fence')) is int and proof['fence'] == attempt['fence']
                 and _sha(proof.get('request_sha256')), 'animation_frame_receipt_attempt_unproven')
        receipt = self._remote(self.client.model_image_evidence, job_id)
        _require(type(receipt) is dict and receipt.get('schema') == 'agent-model-image-evidence.v1'
                 and receipt.get('evidence_scope') == 'completed_upstream_transport'
                 and receipt.get('job_id') == job_id and receipt.get('attempt_id') == attempt['attempt_id']
                 and receipt.get('request_sha256') == proof['request_sha256']
                 and type(receipt.get('images')) is list, 'animation_frame_receipt_binding_mismatch')
        return receipt


class AnimationObserveGateway(_AnimationGateway):
    profile = visual.OBSERVE_PROFILE
    input_name, input_role = visual.OBSERVE_INPUT_NAME, 'animation_observation_input'
    result_name = visual.OBSERVE_RESULT_NAME
    business_prefix, idempotency_prefix = 'animation-observe:', 'animation-observe-'
    input_limit, output_limit = visual.INPUT_MAX_BYTES, visual.OUTPUT_MAX_BYTES
    validate_input = staticmethod(visual.validate_observation_input)
    validate_result = staticmethod(visual.validate_observation_result)
    visual_input = True


class AnimationReviewGateway(_AnimationGateway):
    profile = visual.REVIEW_PROFILE
    input_name, input_role = visual.REVIEW_INPUT_NAME, 'animation_review_input'
    result_name = visual.REVIEW_RESULT_NAME
    business_prefix, idempotency_prefix = 'animation-review:', 'animation-review-'
    input_limit, output_limit = visual.INPUT_MAX_BYTES, visual.OUTPUT_MAX_BYTES
    validate_input = staticmethod(visual.validate_review_input)
    validate_result = staticmethod(visual.validate_review_result)
    visual_input = True


class AnimationCreateGateway(_AnimationGateway):
    profile = creation.PROFILE
    input_name, input_role = creation.INPUT_NAME, 'animation_creation_input'
    result_name = creation.RESULT_NAME
    business_prefix, idempotency_prefix = 'animation-create:', 'animation-create-'
    input_limit, output_limit = creation.INPUT_MAX_BYTES, creation.OUTPUT_MAX_BYTES
    validate_input = staticmethod(creation.validate_creation_input)
    validate_result = staticmethod(creation.validate_creation_result)


class AnimationSequenceReviewGateway(AnimationReviewGateway):
    """Explicit opt-in; original v1 identity and defaults remain frozen."""
    profile = visual.REVIEW_SEQUENCE_PROFILE
    business_prefix, idempotency_prefix = 'animation-review-sequence:', 'animation-review-seq-'
