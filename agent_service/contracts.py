"""Version one wire contracts. Execution completion grants no business approval."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_FILES = 2048
TERMINAL_STATES = frozenset({'completed', 'needs_input', 'failed', 'cancelled', 'timed_out', 'quarantined'})


class ServiceError(RuntimeError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def safe_name(value: str) -> str:
    if (not value or len(value) > 120 or value in {'.', '..'}
            or any(c in value for c in '/\\:\x00')
            or any(ord(c) < 32 for c in value)):
        raise ValueError('An ordinary basename is required')
    return value


def safe_relative_path(value: str) -> str:
    """Portable project-relative path, never an archive or a host path."""
    if (type(value) is not str or not 1 <= len(value) <= 500 or '\\' in value
            or value.startswith('/') or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('An ordinary relative path is required')
    parts = value.split('/')
    for part in parts:
        safe_name(part)
        if (any(c in part for c in '\"<>|?*') or part.endswith((' ', '.')) or part.split('.')[0].casefold() in
                {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)),
                 *(f'lpt{i}' for i in range(1, 10))}):
            raise ValueError('A portable relative path is required')
    return value


class WireModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class ExecutionBindingPins(WireModel):
    """Content-addressed server resolution, never a caller-selected backend.

    Only ordinary public identifiers and hashes cross this boundary. The
    deployment registry owns the approval record; its digest is not a substitute
    for native readiness, model authorization or actual host stop observation.
    """
    schema_version: Literal['1'] = '1'
    binding_id: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    binding_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    profile: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    profile_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    contract_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bundle_name: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    bundle_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    bundle_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    tool_environment_name: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    tool_environment_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    tool_environment_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    engine: Literal['pi', 'hermes']
    runtime_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    image_digest: str = Field(pattern=r'^sha256:[0-9a-f]{64}$')
    adapter_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    model_policy_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    model_policy_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    combination_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    acceptance_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @classmethod
    def create(cls, **identity):
        identity = dict(identity)
        combination = {k: v for k, v in identity.items()
                       if k not in {'schema_version', 'binding_id', 'binding_revision',
                                    'acceptance_sha256', 'combination_sha256', 'sha256'}}
        identity['combination_sha256'] = digest(combination)
        body = dict(schema_version='1', **identity)
        body.pop('sha256', None)
        return cls(**body, sha256=digest(body))

    @model_validator(mode='after')
    def content_addressed(self):
        body = self.model_dump(exclude={'sha256'})
        combination = {k: v for k, v in body.items()
                       if k not in {'schema_version', 'binding_id', 'binding_revision',
                                    'acceptance_sha256', 'combination_sha256'}}
        if digest(body) != self.sha256 or digest(combination) != self.combination_sha256:
            raise ValueError('Execution binding identity changed')
        return self

    def matches_base(self, pins):
        return all(pins.get(k) == getattr(self, k) for k in
                   ('profile', 'profile_revision', 'engine', 'runtime_revision', 'image_digest'))


class InputRef(WireModel):
    input_id: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    path: str | None = None
    role: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_.-]{0,79}$')

    @field_validator('path')
    @classmethod
    def relative_path(cls, value):
        return safe_relative_path(value) if value is not None else None

    @field_validator('input_id')
    @classmethod
    def uuid_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('Canonical UUID required')
        return value


class CheckpointRef(WireModel):
    checkpoint_id: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    format: Literal['workspace-tree.v1'] = 'workspace-tree.v1'

    @field_validator('checkpoint_id')
    @classmethod
    def canonical_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('Canonical checkpoint UUID required')
        return value


class JobRequest(WireModel):
    api_version: Literal['1'] = '1'
    profile: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    profile_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    business_ref: str = Field(min_length=1, max_length=200)
    task: dict[str, Any]
    inputs: list[InputRef] = Field(default_factory=list, max_length=MAX_FILES)
    checkpoint_ref: CheckpointRef | None = None
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    execution_deadline_at: float | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def bounded_task(self):
        if self.execution_deadline_at is not None and not math.isfinite(self.execution_deadline_at):
            raise ValueError('A finite absolute execution deadline is required')
        if len(canonical_bytes(self.task)) > 32768:
            raise ValueError('Task exceeds its bound')
        def depth(value, level=0):
            if level > 10:
                raise ValueError('Task nesting exceeds its bound')
            if isinstance(value, dict):
                for k, v in value.items():
                    if not isinstance(k, str):
                        raise ValueError('JSON object keys must be strings')
                    depth(v, level + 1)
            elif isinstance(value, list):
                for v in value:
                    depth(v, level + 1)
        depth(self.task)
        if len({x.input_id for x in self.inputs}) != len(self.inputs):
            raise ValueError('Duplicate input reference')
        paths = [x.path.casefold() for x in self.inputs if x.path is not None]
        if len(set(paths)) != len(paths):
            raise ValueError('Duplicate input path')
        return self


class InlineFile(WireModel):
    name: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    content_base64: str = Field(max_length=((MAX_INPUT_BYTES + 2) // 3) * 4)

    @field_validator('name')
    @classmethod
    def basename(cls, value):
        return safe_relative_path(value)

    def content(self, limit=MAX_ARTIFACT_BYTES) -> bytes:
        if len(self.content_base64) > ((limit + 2) // 3) * 4:
            raise ServiceError('file_too_large', 413)
        try:
            data = base64.b64decode(self.content_base64, validate=True)
        except ValueError:
            raise ServiceError('invalid_file', 422) from None
        if len(data) > limit or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ServiceError('file_integrity_invalid', 422)
        return data

    @classmethod
    def from_bytes(cls, name: str, content: bytes):
        return cls(name=name, sha256=hashlib.sha256(content).hexdigest(),
                   content_base64=base64.b64encode(content).decode('ascii'))


class ExecutionEnvelope(WireModel):
    api_version: Literal['1'] = '1'
    job_id: str
    attempt_id: str
    fence: int = Field(ge=1)
    engine: Literal['pi', 'hermes']
    profile: str
    profile_revision: str
    runtime_revision: str
    image_digest: str = Field(pattern=r'^sha256:[0-9a-f]{64}$')
    deadline_at: float
    lease_expires_at: float
    task: dict[str, Any]
    inputs: list[InlineFile] = Field(default_factory=list, max_length=MAX_FILES)
    checkpoint_ref: CheckpointRef | None = None
    checkpoint_inputs: list[InlineFile] = Field(default_factory=list, max_length=MAX_FILES)
    execution_binding: ExecutionBindingPins | None = None
    task_schema: str = 'workspace-text.v1'
    output_schema: str = 'text-output.v1'
    max_input_bytes: int = Field(default=1024 * 1024, ge=1, le=MAX_INPUT_BYTES)
    max_input_total_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=MAX_ARTIFACT_BYTES)
    max_input_files: int = Field(default=10, ge=0, le=MAX_FILES)
    max_artifact_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=MAX_ARTIFACT_BYTES)
    max_artifact_file_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=MAX_INPUT_BYTES)
    max_artifact_files: int = Field(default=32, ge=1, le=MAX_FILES)

    @field_validator('job_id', 'attempt_id')
    @classmethod
    def uuid_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('Canonical UUID required')
        return value

    def identity(self):
        # Lease renewal does not change immutable execution identity.
        excluded = {'lease_expires_at'}
        if self.execution_binding is None:
            excluded.add('execution_binding')
        return self.model_dump(exclude=excluded)

    @model_validator(mode='after')
    def valid_times(self):
        if (not all(math.isfinite(x) and x > 0 for x in (self.deadline_at, self.lease_expires_at))
                or self.lease_expires_at > self.deadline_at + 45):
            raise ValueError('Invalid bounded execution clocks')
        if len(canonical_bytes(self.task)) > 32768:
            raise ValueError('Task exceeds its bound')
        if self.execution_binding is not None and not self.execution_binding.matches_base(self.model_dump()):
            raise ValueError('Execution binding does not match its envelope')
        return self

    @property
    def request_sha256(self):
        return digest(self.identity())


class StopReceipt(WireModel):
    job_id: str
    attempt_id: str
    fence: int
    engine: Literal['pi', 'hermes']
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    resource_ids: list[str] = Field(min_length=1, max_length=64)
    observed_at: float
    result_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    signature: str = Field(pattern=r'^[0-9a-f]{64}$')

    @field_validator('observed_at')
    @classmethod
    def finite_time(cls, value):
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid observation time')
        return value


class WorkerResult(WireModel):
    state: Literal['completed', 'needs_input', 'failed', 'cancelled', 'timed_out', 'quarantined']
    error_code: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,79}$')
    usage: dict[str, Any] | None = None
    artifacts: list[InlineFile] = Field(default_factory=list, max_length=MAX_FILES)
    stop_receipt: StopReceipt | None = None

    def result_digest(self):
        return digest(self.model_dump(exclude={'stop_receipt'}))


def sign_receipt(envelope: ExecutionEnvelope, result: WorkerResult, key: bytes,
                 resource_ids: tuple[str, ...], observed_at: float) -> StopReceipt:
    if len(key) < 32 or not resource_ids or not all(isinstance(x, str) and 0 < len(x) <= 200 for x in resource_ids):
        raise ServiceError('stop_evidence_invalid', 503)
    body = dict(job_id=envelope.job_id, attempt_id=envelope.attempt_id, fence=envelope.fence,
                engine=envelope.engine, request_sha256=envelope.request_sha256,
                resource_ids=list(resource_ids), observed_at=observed_at,
                result_sha256=result.result_digest())
    return StopReceipt(**body, signature=hmac.new(key, canonical_bytes(body), hashlib.sha256).hexdigest())


def verify_receipt(envelope: ExecutionEnvelope, result: WorkerResult, key: bytes, now: float) -> bool:
    proof = result.stop_receipt
    if proof is None or len(key) < 32 or result.state == 'quarantined':
        return False
    if (proof.job_id != envelope.job_id or proof.attempt_id != envelope.attempt_id
            or proof.fence != envelope.fence or proof.engine != envelope.engine
            or proof.request_sha256 != envelope.request_sha256
            or proof.result_sha256 != result.result_digest()
            or not 0 < proof.observed_at <= now + 5
            or len(set(proof.resource_ids)) != len(proof.resource_ids)
            or any(not x or len(x) > 200 for x in proof.resource_ids)):
        return False
    expected = hmac.new(key, canonical_bytes(proof.model_dump(exclude={'signature'})), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, proof.signature)


@dataclass(frozen=True)
class Principal:
    tenant: str
    caller: str
    profiles: frozenset[str]


@dataclass(frozen=True)
class ProfilePolicy:
    name: str
    revision: str
    engine: Literal['pi', 'hermes']
    runtime_revision: str
    image_digest: str
    enabled: bool = False
    max_timeout_seconds: int = 600
    task_validator: Callable[[dict], None] = field(default=lambda _: None, repr=False, compare=False)
    supports_inputs: bool = False
    execution_binding: ExecutionBindingPins | None = None
    task_schema: str = 'workspace-text.v1'
    output_schema: str = 'text-output.v1'
    max_input_bytes: int = 1024 * 1024
    max_input_total_bytes: int = 10 * 1024 * 1024
    max_input_files: int = 10
    max_artifact_bytes: int = 8 * 1024 * 1024
    max_artifact_file_bytes: int = 8 * 1024 * 1024
    max_artifact_files: int = 32

    def __post_init__(self):
        if (self.engine not in {'pi', 'hermes'} or not self.name or not self.revision
                or not self.runtime_revision or not re.fullmatch(r'sha256:[0-9a-f]{64}', self.image_digest)
                or type(self.enabled) is not bool or type(self.supports_inputs) is not bool
                or not 1 <= self.max_timeout_seconds <= 3600):
            raise ValueError('Invalid server-owned profile policy')
        if self.execution_binding is not None:
            if (not isinstance(self.execution_binding, ExecutionBindingPins)
                    or not self.execution_binding.matches_base(self.pins())):
                raise ValueError('Execution binding does not match its profile')
        bounds = {'max_input_bytes': MAX_INPUT_BYTES, 'max_input_total_bytes': MAX_ARTIFACT_BYTES,
                  'max_input_files': MAX_FILES, 'max_artifact_bytes': MAX_ARTIFACT_BYTES,
                  'max_artifact_file_bytes': MAX_INPUT_BYTES, 'max_artifact_files': MAX_FILES}
        if any(type(getattr(self, k)) is not int or not 1 <= getattr(self, k) <= v
               for k, v in bounds.items()):
            raise ValueError('Invalid profile resource bounds')

    def pins(self):
        value = dict(engine=self.engine, runtime_revision=self.runtime_revision,
                     image_digest=self.image_digest, profile_revision=self.revision, profile=self.name)
        if self.execution_binding is not None:
            value['execution_binding'] = self.execution_binding.model_dump(mode='json')
            value.update(self.execution_limits())
            value.update(task_schema=self.task_schema, output_schema=self.output_schema)
        return value

    def execution_limits(self):
        return {name: getattr(self, name) for name in ('max_input_bytes', 'max_input_total_bytes',
            'max_input_files', 'max_artifact_bytes', 'max_artifact_file_bytes', 'max_artifact_files')}

    def matches(self, value):
        data = value.model_dump(mode='json') if isinstance(value, ExecutionEnvelope) else value
        if not isinstance(data, dict):
            return False
        pins = self.pins()
        return (all(data.get(k) == v for k, v in pins.items())
                and data.get('execution_binding') == pins.get('execution_binding'))


class ProfileRegistry:
    def __init__(self, profiles=()):
        indexed = {}
        for profile in profiles:
            key = (profile.name, profile.revision)
            if key in indexed:
                raise ValueError('Duplicate profile revision')
            indexed[key] = profile
        self._profiles = MappingProxyType(indexed)

    def policy(self, name, revision):
        return self._profiles.get((name, revision))

    def policies(self):
        return tuple(self._profiles.values())

    def resolve(self, principal: Principal, request: JobRequest, *, require_enabled=True):
        profile = self._profiles.get((request.profile, request.profile_revision))
        if request.profile not in principal.profiles or profile is None:
            raise ServiceError('profile_unavailable', 404)
        if require_enabled and not profile.enabled:
            raise ServiceError('profile_not_ready', 503)
        if request.timeout_seconds > profile.max_timeout_seconds:
            raise ServiceError('deadline_exceeds_profile', 422)
        if request.inputs and not profile.supports_inputs:
            raise ServiceError('input_staging_not_ready', 503)
        if request.checkpoint_ref is not None and not profile.supports_inputs:
            raise ServiceError('checkpoint_not_supported', 422)
        if len(request.inputs) > profile.max_input_files:
            raise ServiceError('input_quota_exceeded', 413)
        profile.task_validator(request.task)
        return profile

    def capabilities(self, principal: Principal):
        return [dict(profile=p.name, profile_revision=p.revision, **{k: v for k, v in p.pins().items() if k not in {'profile', 'profile_revision'}},
                     configured=p.enabled, supports_inputs=p.supports_inputs,
                     native_resume=False, publication_authorized=False,
                     max_timeout_seconds=p.max_timeout_seconds)
                for p in self._profiles.values() if p.name in principal.profiles]


def validate_workspace_task(task: dict):
    if set(task) != {'prompt'} or not isinstance(task['prompt'], str) or not 1 <= len(task['prompt']) <= 16000:
        raise ServiceError('invalid_workspace_task', 422)
