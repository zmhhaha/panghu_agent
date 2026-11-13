"""Frozen Skill inputs and independently stopped two-artifact production jobs.

The caller durably saves an operation, then the complete upload receipt, before
claiming admission. This adapter never refreshes a catalog, retries a job,
publishes a Skill, renders media or trusts Agent-written reading receipts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import time

from agent_service.animation_skills_contract import bundle_files
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_skill_production import (
    PROFILE, INPUT_NAME, INPUT_ROLE, RESULT_NAME, EVIDENCE_NAME,
    INPUT_MAX_BYTES, OUTPUT_MAX_BYTES, MAX_TOOL_EVIDENCE_BYTES,
    VideoAnimationSkillProductionProfile,
)
from agent_service.profiles.video_animation_skill_production_contract import validate_result
from agent_service.skill_tool_evidence import validate_tool_evidence
from .agent import _decode, _finite_number, _sha, _uuid
from .animation_skill_production import validate_delivery
from .creative_skill_contract import validate_scene_input
from .motion_gateway import MotionAgentGateway


UPLOAD_SCHEMA = 'video.skill-production-input-upload.v1'
RESOURCE_ROLE = 'animation_skill_resource'
_UPLOAD_FIELDS = frozenset({'schema', 'input_sha256', 'catalog_sha256',
                           'skill_delivery_sha256', 'inputs', 'sha256'})


def _require(condition, code='skill_production_agent_protocol_invalid', status=502):
    if not condition:
        raise ServiceError(code, status)


class SkillProductionAgentGateway(MotionAgentGateway):
    """Only the separately admitted Skill author, with exact normal input refs."""
    profile = PROFILE
    input_name, input_role, result_name = INPUT_NAME, INPUT_ROLE, RESULT_NAME
    business_prefix, idempotency_prefix = 'video-skill-production:', 'skill-production-'
    input_limit, output_limit = INPUT_MAX_BYTES, OUTPUT_MAX_BYTES
    validate_input = staticmethod(validate_scene_input)
    validate_result = staticmethod(validate_result)

    def __init__(self, client, skill_adapter, profile_revision='1'):
        super().__init__(client, profile_revision=profile_revision)
        _require(callable(getattr(skill_adapter, 'get_bundle', None)),
                 'skill_production_adapter_required', 503)
        self.skill_adapter = skill_adapter

    def _delivery(self, document):
        catalog = document['skill_catalog']
        # This reads an existing durable freeze only. Never call freeze or
        # reinterpret a missing resource set as an empty directory.
        delivery = validate_delivery(self.skill_adapter.get_bundle(
            catalog['recipient_owner'], catalog['consumer_id'], catalog['sha256']))
        _require(delivery['catalog'] == catalog, 'skill_production_delivery_binding_mismatch', 409)
        return delivery

    def _files(self, document):
        raw = self._document_bytes(document)
        delivery = self._delivery(document)
        files = {self.input_name: raw}
        files.update({file.name: file.content()
                      for file in bundle_files(delivery['catalog'], delivery['packages'])})
        limits = VideoAnimationSkillProductionProfile.limits
        _require(len(files) <= limits.max_input_files
                 and sum(map(len, files.values())) <= limits.max_input_total_bytes,
                 'skill_production_inputs_too_large', 413)
        return files, delivery

    def upload(self, document):
        """Upload main JSON plus every frozen file; return one persistable receipt."""
        files, delivery = self._files(document)
        refs, identities = [], set()
        for name, raw in files.items():
            checksum = hashlib.sha256(raw).hexdigest()
            uploaded = self._remote(self.client.upload_input, name, raw)
            _require(type(uploaded) is dict and _uuid(uploaded.get('input_id'))
                     and uploaded['input_id'] not in identities and uploaded.get('name') == name
                     and uploaded.get('sha256') == checksum and type(uploaded.get('size')) is int
                     and uploaded['size'] == len(raw))
            identities.add(uploaded['input_id'])
            refs.append(dict(input_id=uploaded['input_id'], sha256=checksum, path=name,
                role=self.input_role if name == self.input_name else RESOURCE_ROLE))
        result = dict(schema=UPLOAD_SCHEMA, input_sha256=hashlib.sha256(files[self.input_name]).hexdigest(),
            catalog_sha256=delivery['catalog']['sha256'], skill_delivery_sha256=delivery['sha256'], inputs=refs)
        result['sha256'] = digest(result)
        return result

    def _refs(self, document, receipt):
        files, delivery = self._files(document)
        _require(type(receipt) is dict and set(receipt) == _UPLOAD_FIELDS
                 and receipt['schema'] == UPLOAD_SCHEMA
                 and receipt['input_sha256'] == hashlib.sha256(files[self.input_name]).hexdigest()
                 and receipt['catalog_sha256'] == delivery['catalog']['sha256']
                 and receipt['skill_delivery_sha256'] == delivery['sha256']
                 and type(receipt['inputs']) is list and len(receipt['inputs']) == len(files),
                 'invalid_skill_production_input_refs', 422)
        paths, identities = set(), set()
        for ref in receipt['inputs']:
            _require(type(ref) is dict and set(ref) == {'input_id', 'sha256', 'path', 'role'}
                     and _uuid(ref.get('input_id')) and type(ref.get('path')) is str
                     and ref['path'] in files and ref['path'] not in paths
                     and ref['input_id'] not in identities
                     and ref['sha256'] == hashlib.sha256(files[ref['path']]).hexdigest()
                     and ref['role'] == (self.input_role if ref['path'] == self.input_name else RESOURCE_ROLE),
                     'invalid_skill_production_input_refs', 422)
            paths.add(ref['path'])
            identities.add(ref['input_id'])
        _require(receipt['sha256'] == digest({k: v for k, v in receipt.items() if k != 'sha256'}),
                 'invalid_skill_production_input_refs', 422)
        return deepcopy(receipt['inputs']), receipt['input_sha256']

    def preflight(self, deadline_at):
        _require(_finite_number(deadline_at) and deadline_at > time.time(),
                 'skill_production_deadline_expired', 409)
        try:
            capabilities = self.capabilities()
        except ServiceError:
            raise ServiceError('skill_production_preflight_unavailable', 503) from None
        profiles = capabilities.get('profiles') if type(capabilities) is dict else None
        matches = ([row for row in profiles if type(row) is dict
                    and row.get('profile') == self.profile and row.get('profile_revision') == self.profile_revision]
                   if type(profiles) is list else [])
        _require(len(matches) == 1, 'skill_production_profile_unavailable', 503)
        route = matches[0]
        required = route.get('required_model_capabilities')
        _require(route.get('configured') is True and route.get('ready') is True
                 and route.get('supports_inputs') is True and type(route.get('max_timeout_seconds')) is int
                 and 1 <= route['max_timeout_seconds'] <= 3600
                 and type(required) is list and all(type(value) is str for value in required)
                 and {'text', 'tool_calls'} <= set(required), 'skill_production_profile_unavailable', 503)
        return route

    def prepare_submission(self, operation_id, document, deadline_at, input_ref=None):
        """Validate resources/readiness before a durable admission claim."""
        key = self._operation_key(operation_id)
        refs, input_sha256 = self._refs(document, input_ref)
        route = self.preflight(deadline_at)
        remaining = deadline_at - time.time()
        _require(remaining > 0, 'skill_production_deadline_expired', 409)
        request = dict(api_version='1', profile=self.profile, profile_revision=self.profile_revision,
            business_ref=self.business_prefix + operation_id, task=dict(input_sha256=input_sha256),
            inputs=refs, execution_deadline_at=deadline_at,
            timeout_seconds=min(600, route['max_timeout_seconds'], math.ceil(remaining)))

        def admit():
            _require(deadline_at > time.time(), 'skill_production_deadline_expired', 409)
            response = self._remote(self.client.submit, deepcopy(request), idempotency_key=key)
            try:
                return self._checked_job(response, operation_id=operation_id)
            except ServiceError:
                # The service may have admitted it: reconcile the original key.
                raise ServiceError('service_protocol_invalid', 502) from None
        return admit

    def submit(self, operation_id, document, deadline_at, input_ref=None):
        return self.prepare_submission(operation_id, document, deadline_at, input_ref)()

    def collect(self, job_id, input_sha256, document):
        """Verify custody of both host artifacts before trusting any read claim."""
        _require(_sha(input_sha256) and digest(self.validate_input(document)) == input_sha256,
                 'invalid_skill_production_input_ref', 422)
        delivery = self._delivery(document)
        job = self.poll(job_id)
        _require(job.get('state') == 'completed', 'skill_production_result_not_ready', 409)
        attempt, proof, pins = job.get('attempt'), job.get('stop_proof'), job.get('pins')
        _require(type(attempt) is dict and _uuid(attempt.get('attempt_id'))
                 and type(attempt.get('fence')) is int and attempt['fence'] >= 1
                 and type(pins) is dict and pins.get('profile') == self.profile
                 and pins.get('profile_revision') == self.profile_revision
                 and job.get('stop_proven') is True and type(proof) is dict
                 and proof.get('stop_proven') is True and proof.get('attempt_id') == attempt['attempt_id']
                 and type(proof.get('fence')) is int and proof['fence'] == attempt['fence']
                 and _sha(proof.get('request_sha256')) and _sha(proof.get('result_sha256'))
                 and type(proof.get('resource_ids')) is list and 1 <= len(proof['resource_ids']) <= 64
                 and all(type(value) is str and 1 <= len(value) <= 200
                     and all(ord(c) >= 32 and ord(c) != 127 for c in value) for value in proof['resource_ids'])
                 and len(set(proof['resource_ids'])) == len(proof['resource_ids'])
                 and _finite_number(proof.get('observed_at')) and proof['observed_at'] > 0)
        manifest = self._remote(self.client.artifact_manifest, job_id)
        _require(type(manifest) is dict and set(manifest) == {'schema_version', 'job_id',
            'attempt_id', 'files', 'total_bytes', 'publication_authorized', 'sha256'})
        try:
            checksum = digest({key: value for key, value in manifest.items() if key != 'sha256'})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('skill_production_agent_protocol_invalid', 502) from None
        _require(manifest['schema_version'] == 'agent-artifacts.v1' and manifest['job_id'] == job_id
                 and manifest['attempt_id'] == attempt['attempt_id']
                 and manifest['publication_authorized'] is False and manifest['sha256'] == checksum
                 and job.get('artifact_manifest_sha256') == checksum
                 and type(manifest['files']) is list and len(manifest['files']) == 2)
        limits = {self.result_name: self.output_limit, EVIDENCE_NAME: MAX_TOOL_EVIDENCE_BYTES}
        entries, identities, total = {}, set(), 0
        for item in manifest['files']:
            _require(type(item) is dict and set(item) == {'artifact_id', 'name', 'sha256', 'size'}
                     and _uuid(item.get('artifact_id')) and item['artifact_id'] not in identities
                     and type(item.get('name')) is str and item['name'] in limits
                     and item['name'] not in entries and _sha(item.get('sha256'))
                     and type(item.get('size')) is int and 1 <= item['size'] <= limits[item['name']])
            entries[item['name']] = item
            identities.add(item['artifact_id'])
            total += item['size']
        _require(type(manifest['total_bytes']) is int and manifest['total_bytes'] == total)
        decoded = {}
        for name in (EVIDENCE_NAME, self.result_name):
            item = entries[name]
            raw = self._remote(self.client.download_artifact, item['artifact_id'])
            _require(type(raw) is bytes and len(raw) == item['size']
                     and hashlib.sha256(raw).hexdigest() == item['sha256'],
                     'skill_production_artifact_digest_mismatch')
            decoded[name] = _decode(raw)
        # Provenance comes from the authenticated Agent's stopped artifact
        # handoff above; a source label and an unkeyed hash alone do not grant it.
        evidence = validate_tool_evidence(decoded[EVIDENCE_NAME], delivery['catalog'], delivery['packages'],
            attempt_id=attempt['attempt_id'], input_sha256=input_sha256)
        reads = {event['receipt']['package_sha256'] for event in evidence['events']
                 if event['tool_name'] == 'skill_read' and event['receipt']['path'] == 'SKILL.md'}
        result = validate_result(decoded[self.result_name], input_sha256, document,
            read_package_sha256s=reads, tool_evidence_sha256=evidence['sha256'])
        return dict(result=result, evidence=evidence, skill_delivery=delivery)
