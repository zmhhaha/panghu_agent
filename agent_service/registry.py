"""Reviewed task implementations and content-addressed deployment bindings.

This registry parses data only. It never imports configuration-selected modules,
downloads skills or opens execution resources. Adding a domain contract requires
an explicit reviewed implementation, not a new name for the text collector.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import Field, field_validator, model_validator

from .contracts import (ExecutionBindingPins, ProfilePolicy, ProfileRegistry,
                        WireModel, digest, safe_relative_path)
from .profiles import implementation

Tool = Literal['read', 'write', 'bash', 'image', 'academic_search', 'academic_verify']
OutputSchema = Literal['text-output.v1', 'ppt-plan_initial-output.v1', 'ppt-plan_options-output.v1',
    'ppt-generate-output.v1', 'ppt-revise-output.v1', 'ppt-prepare-output.v1',
    'research-output.v1', 'research-revision-output.v1']

NAME = r'^[a-z][a-z0-9_.-]{0,79}$'
REVISION = r'^[A-Za-z0-9_.-]{1,100}$'
SHA256 = r'^[0-9a-f]{64}$'


class _Record(WireModel):
    @model_validator(mode='before')
    @classmethod
    def immutable_sequences(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for key in ('required_tools', 'required_model_capabilities', 'resources',
                        'tools', 'capabilities'):
                if key in value and isinstance(value[key], list):
                    value[key] = tuple(value[key])
        return value


def _unique(values):
    if len(set(values)) != len(values):
        raise ValueError('Duplicate capability')
    return tuple(sorted(values))


class TaskContract(_Record):
    name: str = Field(pattern=NAME)
    revision: str = Field(pattern=REVISION)
    task_schema: str = Field(pattern=NAME)
    output_schema: OutputSchema
    required_tools: tuple[Tool, ...] = Field(min_length=2, max_length=6)
    required_model_capabilities: tuple[Literal['text', 'tool_calls', 'vision'], ...] = Field(min_length=2, max_length=3)
    max_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    max_input_bytes: int | None = Field(default=None, ge=1, le=64 * 1024 * 1024)
    max_input_total_bytes: int | None = Field(default=None, ge=1, le=256 * 1024 * 1024)
    max_input_files: int | None = Field(default=None, ge=1, le=2048)
    max_artifact_bytes: int | None = Field(default=None, ge=1, le=256 * 1024 * 1024)
    max_artifact_file_bytes: int | None = Field(default=None, ge=1, le=64 * 1024 * 1024)
    max_artifact_files: int | None = Field(default=None, ge=1, le=2048)

    @field_validator('required_tools', 'required_model_capabilities')
    @classmethod
    def unique_requirements(cls, values):
        return _unique(values)

    @model_validator(mode='after')
    def reviewed_handler(self):
        handler = implementation(self.name)
        if (self.task_schema != handler.task_schema or self.output_schema != handler.output_schema
                or set(self.required_tools) != set(handler.required_tools)
                or not {'text', 'tool_calls'} <= set(self.required_model_capabilities)):
            raise ValueError('Task contract has no reviewed implementation')
        if 'image' in handler.required_tools and 'vision' not in self.required_model_capabilities:
            raise ValueError('The image tool requires a vision-capable model policy')
        for key, maximum in handler.limits.as_dict().items():
            value = getattr(self, key)
            if value is not None and value > maximum:
                raise ValueError('Contract cannot widen implementation quotas')
        return self

    @property
    def task_validator(self):
        return implementation(self.name).validate_task

    def limits(self):
        return {key: getattr(self, key) if getattr(self, key) is not None else maximum
                for key, maximum in implementation(self.name).limits.as_dict().items()}


class Resource(_Record):
    name: str
    sha256: str = Field(pattern=SHA256)

    @field_validator('name')
    @classmethod
    def ordinary_resource(cls, value):
        return safe_relative_path(value)


class CapabilityBundle(_Record):
    name: str = Field(pattern=NAME)
    revision: str = Field(pattern=REVISION)
    resources: tuple[Resource, ...] = Field(min_length=1, max_length=2048)
    source: str = Field(min_length=1, max_length=300)
    license: str = Field(pattern=r'^[A-Za-z0-9().+ -]{1,120}$')
    required_tools: tuple[Tool, ...] = Field(max_length=6)

    @field_validator('source')
    @classmethod
    def ordinary_source(cls, value):
        if any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError('Invalid bundle provenance')
        return value

    @field_validator('required_tools')
    @classmethod
    def unique_tools(cls, value):
        return _unique(value)

    @field_validator('resources')
    @classmethod
    def unique_resources(cls, values):
        if len({r.name.casefold() for r in values}) != len(values):
            raise ValueError('Duplicate resource identity')
        return tuple(sorted(values, key=lambda r: r.name))


class ToolEnvironment(_Record):
    name: str = Field(pattern=NAME)
    revision: str = Field(pattern=REVISION)
    tools: tuple[Tool, ...] = Field(min_length=2, max_length=6)
    capabilities: tuple[OutputSchema, ...] = Field(min_length=1, max_length=8)
    image_digest: str = Field(pattern=r'^sha256:[0-9a-f]{64}$')
    runtime_revision: str = Field(pattern=REVISION)

    @field_validator('tools', 'capabilities')
    @classmethod
    def unique_capabilities(cls, values):
        return _unique(values)


class ResourceRef(_Record):
    name: str = Field(pattern=NAME)
    revision: str = Field(pattern=REVISION)


class ModelPolicy(_Record):
    revision: str = Field(pattern=REVISION)
    api_mode: Literal['openai-responses']
    provider: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,80}$')
    model: str = Field(pattern=r'^[A-Za-z0-9_./:-]{1,200}$')
    capabilities: tuple[Literal['text', 'tool_calls', 'vision'], ...] = Field(min_length=1, max_length=3)

    @field_validator('capabilities')
    @classmethod
    def unique_capabilities(cls, values):
        return _unique(values)


class AcceptanceRecord(_Record):
    combination_sha256: str = Field(pattern=SHA256)
    suite: str = Field(pattern=NAME)
    report_sha256: str = Field(pattern=SHA256)

    @field_validator('report_sha256')
    @classmethod
    def nonplaceholder_report(cls, value):
        if value == '0' * 64:
            raise ValueError('An acceptance report reference is required')
        return value


class ExecutionBinding(_Record):
    id: str = Field(pattern=NAME)
    revision: str = Field(pattern=REVISION)
    profile: str = Field(pattern=NAME)
    profile_revision: str = Field(pattern=REVISION)
    bundle: ResourceRef
    tool_environment: ResourceRef
    engine: Literal['pi', 'hermes']
    adapter_revision: str = Field(pattern=REVISION)
    model_policy: ModelPolicy
    acceptance: AcceptanceRecord | None = None


class Route(_Record):
    profile: str = Field(pattern=NAME)
    profile_revision: str = Field(pattern=REVISION)
    binding_id: str = Field(pattern=NAME)
    binding_revision: str = Field(pattern=REVISION)
    enabled: bool = False


def _parse(cls, value):
    return value if isinstance(value, cls) else cls.model_validate(value)


def _identity(contract, bundle, environment, binding):
    c, b, e, x = (_parse(cls, value) for cls, value in
                  ((TaskContract, contract), (CapabilityBundle, bundle),
                   (ToolEnvironment, environment), (ExecutionBinding, binding)))
    if ((x.profile, x.profile_revision) != (c.name, c.revision)
            or (x.bundle.name, x.bundle.revision) != (b.name, b.revision)
            or (x.tool_environment.name, x.tool_environment.revision) != (e.name, e.revision)
            or set(e.tools) != set(c.required_tools)
            or not set(b.required_tools) <= set(e.tools)
            or c.output_schema not in e.capabilities
            or not set(c.required_model_capabilities) <= set(x.model_policy.capabilities)):
        raise ValueError('Incompatible execution combination')
    return dict(profile=c.name, profile_revision=c.revision,
        contract_sha256=digest(c.model_dump(mode='json')),
        bundle_name=b.name, bundle_revision=b.revision, bundle_sha256=digest(b.model_dump(mode='json')),
        tool_environment_name=e.name, tool_environment_revision=e.revision,
        tool_environment_sha256=digest(e.model_dump(mode='json')),
        engine=x.engine, runtime_revision=e.runtime_revision, image_digest=e.image_digest,
        adapter_revision=x.adapter_revision, model_policy_revision=x.model_policy.revision,
        model_policy_sha256=digest(x.model_policy.model_dump(mode='json')))


def binding_fingerprint(contract, bundle, environment, binding):
    """Offline candidate identity to bind an operator's acceptance report to."""
    return digest(_identity(contract, bundle, environment, binding))


def _index(cls, values, *, binding=False):
    if type(values) not in (tuple, list) or len(values) > 100:
        raise ValueError('Invalid bounded registry')
    result = {}
    for value in values:
        record = _parse(cls, value)
        key = (record.id if binding else record.name, record.revision)
        if key in result:
            raise ValueError('Duplicate registry identity')
        result[key] = record
    return MappingProxyType(result)


@dataclass(frozen=True)
class DeploymentRegistry:
    _contracts: Mapping
    _bundles: Mapping
    _environments: Mapping
    _bindings: Mapping
    _profiles: ProfileRegistry
    _fingerprints: Mapping

    @classmethod
    def from_config(cls, task_contracts, capability_bundles, tool_environments,
                    execution_bindings, routes):
        contracts = _index(TaskContract, task_contracts)
        bundles = _index(CapabilityBundle, capability_bundles)
        environments = _index(ToolEnvironment, tool_environments)
        bindings = _index(ExecutionBinding, execution_bindings, binding=True)
        fingerprints = {}
        for key, binding in bindings.items():
            try:
                c = contracts[(binding.profile, binding.profile_revision)]
                b = bundles[(binding.bundle.name, binding.bundle.revision)]
                e = environments[(binding.tool_environment.name, binding.tool_environment.revision)]
            except KeyError:
                raise ValueError('Missing reviewed execution resource') from None
            fingerprints[key] = binding_fingerprint(c, b, e, binding)
            if (binding.acceptance is not None
                    and binding.acceptance.combination_sha256 != fingerprints[key]):
                raise ValueError('Stale combination acceptance record')
            suite = ('workspace-contract.v1' if c.name == 'workspace-task.v1'
                     else c.name.replace('.v1', '-contract.v1'))
            if binding.acceptance is not None and binding.acceptance.suite != suite:
                raise ValueError('Acceptance suite does not cover the actual domain contract')
        if type(routes) not in (list, tuple) or len(routes) > 100:
            raise ValueError('Invalid bounded routes')
        policies, seen = [], set()
        for raw in routes:
            route = _parse(Route, raw)
            key = (route.profile, route.profile_revision)
            binding = bindings.get((route.binding_id, route.binding_revision))
            if (key in seen or binding is None or key not in contracts
                    or key != (binding.profile, binding.profile_revision)):
                raise ValueError('Missing or conflicting route')
            if route.enabled and binding.acceptance is None:
                raise ValueError('Enabled combination requires reviewed acceptance')
            seen.add(key)
            contract = contracts[key]
            bundle = bundles[(binding.bundle.name, binding.bundle.revision)]
            environment = environments[(binding.tool_environment.name, binding.tool_environment.revision)]
            pins = ExecutionBindingPins.create(**_identity(contract, bundle, environment, binding),
                binding_id=binding.id, binding_revision=binding.revision,
                acceptance_sha256=digest(binding.acceptance.model_dump(mode='json')
                                         if binding.acceptance else {'status': 'unreviewed'}))
            policies.append(ProfilePolicy(contract.name, contract.revision, binding.engine,
                environment.runtime_revision, environment.image_digest,
                enabled=route.enabled, max_timeout_seconds=contract.max_timeout_seconds,
                task_validator=contract.task_validator,
                supports_inputs=implementation(contract.name).supports_inputs,
                execution_binding=pins, task_schema=contract.task_schema,
                output_schema=contract.output_schema, **contract.limits()))
        return cls(contracts, bundles, environments, bindings, ProfileRegistry(policies),
                   MappingProxyType(fingerprints))

    def profiles(self):
        return self._profiles

    def binding_policy(self, binding_id, revision):
        """Retain disabled historical pools so recovery uses the original pins."""
        binding = self.binding(binding_id, revision)
        if binding is None:
            raise ValueError('Unknown fixed execution binding')
        c = self.contract(binding.profile, binding.profile_revision)
        b = self.bundle(binding.bundle.name, binding.bundle.revision)
        e = self.environment(binding.tool_environment.name, binding.tool_environment.revision)
        pins = ExecutionBindingPins.create(**_identity(c, b, e, binding),
            binding_id=binding.id, binding_revision=binding.revision,
            acceptance_sha256=digest(binding.acceptance.model_dump(mode='json')
                if binding.acceptance else {'status': 'unreviewed'}))
        active = self._profiles.policy(c.name, c.revision)
        return ProfilePolicy(c.name, c.revision, binding.engine, e.runtime_revision, e.image_digest,
            enabled=bool(active and active.execution_binding == pins and active.enabled),
            max_timeout_seconds=c.max_timeout_seconds, task_validator=c.task_validator,
            supports_inputs=implementation(c.name).supports_inputs, execution_binding=pins,
            task_schema=c.task_schema, output_schema=c.output_schema, **c.limits())

    def contract(self, name, revision):
        return self._contracts.get((name, revision))

    def bundle(self, name, revision):
        return self._bundles.get((name, revision))

    def environment(self, name, revision):
        return self._environments.get((name, revision))

    def binding(self, name, revision):
        return self._bindings.get((name, revision))

    def summary(self):
        return dict(task_contracts=len(self._contracts), capability_bundles=len(self._bundles),
                    tool_environments=len(self._environments), execution_bindings=len(self._bindings),
                    routes=len(self._profiles.policies()),
                    combinations=[dict(binding_id=k[0], binding_revision=k[1], combination_sha256=v,
                                       approval_record_present=self._bindings[k].acceptance is not None)
                                  for k, v in self._fingerprints.items()])
