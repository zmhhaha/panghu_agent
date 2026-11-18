"""Prepare disabled WikiSkill compiler/planner combinations; never activate.

The caller explicitly extends its existing caller's profile permission during
deployment. This pure builder creates neither credentials nor acceptances and
preserves all existing catalog rows and historical execution bindings.
"""
from copy import deepcopy
import hashlib
import json
import re

from agent_service.catalog import CATALOG_KEYS
from agent_service.compilation_input_tools import (COMPILATION_INPUT_ENVIRONMENT_NAME,
    COMPILATION_EVIDENCE_ENVIRONMENT_NAME,
    compilation_input_environment_revision, verified_compilation_input_extension)
from agent_service.contracts import digest
from agent_service.deploy.animation_review_catalog import _model, _pool
from agent_service.deploy.video_catalog import append_row, registry, require
from agent_service.profiles import implementation
from agent_service.registry import (CapabilityBundle, ExecutionBinding, TaskContract,
    ToolEnvironment, binding_fingerprint, required_model_capabilities)
from agent_service.skill_tools import SKILL_TOOLS_ENVIRONMENT_NAME, skill_tools_environment_revision
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy


PROFILES = ('video.animation-wikiskill-compile.v1', 'video.joint-plan.v2',
            'video.animation-wikiskill-compile.v2', 'video.animation-wikiskill-compile.v3')
COMPILER_PROFILES = (PROFILES[0], PROFILES[2], PROFILES[3])


def role(profile):
    require(profile in PROFILES, 'wikiskill_profile_invalid')
    if profile == PROFILES[0]:
        from agent_service.profiles import video_animation_wikiskill as module
    elif profile == PROFILES[1]:
        from agent_service.profiles import video_joint_plan_skills as module
    elif profile == PROFILES[2]:
        from agent_service.profiles import video_animation_wikiskill_v2 as module
    else:
        from agent_service.profiles import video_animation_wikiskill_v3 as module
    return module


def measure_wikiskill_bundle(profile):
    module = role(profile)
    content = module.verified_bundle()
    raw = (module.BUNDLE_ROOT / 'manifest.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == module.BUNDLE_MANIFEST_SHA256,
            'wikiskill_bundle_manifest_mismatch')
    return {module.BUNDLE_REVISION: json.loads(raw)}, dict(
        profile=profile, bundle_revision=module.BUNDLE_REVISION,
        manifest_sha256=module.BUNDLE_MANIFEST_SHA256, local_files_verified=True,
        container_measured=False, model_requests=0,
        files=[dict(path=name, sha256=hashlib.sha256(text.encode()).hexdigest())
               for name, text in sorted(content.items())])


def prepare_wikiskill_catalog(current, *, profile, worker_image, source_pool_id,
                              resource_manifests, runtime_revision=None,
                              model_policy=None,
                              model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    before = deepcopy(current)
    existing, module = registry(current), role(profile)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image),
        'wikiskill_worker_latest_digest_required')
    manifests, _ = measure_wikiskill_bundle(profile)
    require(resource_manifests == manifests, 'wikiskill_bundle_manifest_mismatch')
    pool, source = _pool(current, existing, source_pool_id)
    previous_environment = existing.environment(source.tool_environment.name, source.tool_environment.revision)
    require(previous_environment is not None, 'wikiskill_source_environment_required')
    model = _model(source.model_policy if model_policy is None else model_policy,
                   {'text', 'tool_calls'}, 'wikiskill_model_policy_required')
    handler = implementation(profile)
    contract = TaskContract.model_validate(dict(name=profile, revision='1',
        task_schema=handler.task_schema, output_schema=handler.output_schema,
        required_tools=list(handler.required_tools),
        required_model_capabilities=list(required_model_capabilities(handler)),
        max_timeout_seconds=900 if profile in COMPILER_PROFILES else 600, **handler.limits.as_dict()))
    bundle = CapabilityBundle.model_validate(dict(name=module.BUNDLE_REVISION,
        revision=module.BUNDLE_REVISION, source='original-local-' + module.BUNDLE_REVISION,
        license='MIT', required_tools=list(handler.required_tools),
        resources=[dict(name='manifest.json', sha256=module.BUNDLE_MANIFEST_SHA256)] + [
            dict(name=row['path'], sha256=row['sha256']) for row in manifests[module.BUNDLE_REVISION]['local_files']]))
    skills = 'skill_read' in handler.required_tools
    compilation_input = 'read_compilation_input' in handler.required_tools
    compilation_environment = (COMPILATION_EVIDENCE_ENVIRONMENT_NAME if profile == PROFILES[3]
        else COMPILATION_INPUT_ENVIRONMENT_NAME)
    if compilation_input:
        verified_compilation_input_extension(compilation_environment)
    environment_data = dict(name=(compilation_environment if compilation_input
            else SKILL_TOOLS_ENVIRONMENT_NAME if skills else 'tools-pi-wikiskill-evidence'),
        tools=list(handler.required_tools), capabilities=[handler.output_schema],
        image_digest=worker_image.rsplit('@', 1)[1],
        runtime_revision=runtime_revision or previous_environment.runtime_revision)
    environment_data['revision'] = (compilation_input_environment_revision(environment_data) if compilation_input
        else skill_tools_environment_revision(environment_data) if skills
        else 'evidence-' + digest(environment_data))
    environment = ToolEnvironment.model_validate(environment_data)
    prefix = ('wikiskill-compile-v3' if profile == PROFILES[3]
        else 'wikiskill-compile-v2' if profile == PROFILES[2]
        else 'wikiskill-compile' if profile == PROFILES[0] else 'joint-plan-skills')
    binding_data = dict(id=prefix + '-pi', revision='v1', profile=profile, profile_revision='1',
        engine='pi', adapter_revision=PI_ADAPTER_REVISION,
        bundle=dict(name=bundle.name, revision=bundle.revision),
        tool_environment=dict(name=environment.name, revision=environment.revision),
        model_policy=model.model_dump(mode='json'))
    fingerprint = binding_fingerprint(contract, bundle, environment, ExecutionBinding.model_validate(binding_data))
    binding_data['revision'] = 'binding-' + fingerprint[:20]
    binding = ExecutionBinding.model_validate(binding_data)
    route = dict(profile=profile, profile_revision='1', binding_id=binding.id,
                 binding_revision=binding.revision, enabled=False)
    name = prefix + '-pi-' + fingerprint[:20]
    worker = dict(deepcopy(pool), pool_id=name, binding_id=binding.id,
                  binding_revision=binding.revision, host_config_map=name,
                  image=worker_image, enabled=False, uid_start=None)
    merged, selected = deepcopy(current), {}
    for section, value, keys in (
            ('task_contracts', contract, ('name', 'revision')),
            ('capability_bundles', bundle, ('name', 'revision')),
            ('tool_environments', environment, ('name', 'revision')),
            ('execution_bindings', binding, ('id', 'revision'))):
        row = value.model_dump(mode='json', exclude_none=True)
        append_row(merged, section, row, keys)
        selected[section] = [deepcopy(row)]
    selected['routes'] = [route]
    append_row(merged, 'routes', route, ('profile', 'profile_revision'))
    append_row(merged, 'kubernetes_workers', worker, ('pool_id',))
    host = HostConfig.model_validate(dict(schema_version=1, engine='pi',
        binding_id=binding.id, binding_revision=binding.revision,
        profile=dict(name=profile, revision='1', enabled=False,
            runtime_revision=environment.runtime_revision, image_digest=environment.image_digest,
            max_timeout_seconds=contract.max_timeout_seconds),
        model=dict(model=model.model, provider=model.provider, api='openai-responses', proxy_base_url=model_proxy_url),
        execution_catalog=selected))
    require(host_policy(host).execution_binding.combination_sha256 == fingerprint,
            'wikiskill_host_binding_mismatch')
    for key, old in before.items():
        require(merged[key][:len(old)] == old if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'}
                else merged[key] == old, 'wikiskill_existing_catalog_changed')
    require(current == before, 'wikiskill_current_catalog_mutated')
    return merged, {profile: host.model_dump(mode='json', exclude_none=True)}, dict(
        schema='video.wikiskill-catalog-preparation.v1', profile=profile,
        before_sha256=digest(before), after_sha256=digest(merged),
        combination_sha256=fingerprint, binding_id=binding.id, binding_revision=binding.revision,
        worker_pool_id=name, worker_host_config_map=name,
        acceptance_suite=('video.animation-wikiskill-compile-contract.v3' if profile == PROFILES[3]
            else 'video.animation-wikiskill-compile-contract.v2' if profile == PROFILES[2]
            else 'video.animation-wikiskill-compile-contract.v1' if profile == PROFILES[0]
            else 'video.joint-plan-contract.v2'), source_pool_id=source_pool_id,
        route_enabled=False, pool_enabled=False, acceptance_created=False,
        uid_reserved=False, model_requests=0, credentials_created=False)
