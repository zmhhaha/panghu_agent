"""Prepare only a disabled independent Manim recipe role; no live mutations."""
from copy import deepcopy
import hashlib
import json
import re

from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import digest
from agent_service.deploy.animation_review_catalog import _model, _pool
from agent_service.deploy.video_catalog import append_row, registry, require
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_manim_recipe as role
from agent_service.registry import (CapabilityBundle, ExecutionBinding, TaskContract,
    ToolEnvironment, binding_fingerprint, required_model_capabilities)
from agent_service.skill_tools import SKILL_TOOLS_ENVIRONMENT_NAME, skill_tools_environment_revision
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy

PROFILES = (role.PROFILE,)
SUITE = 'video.animation-manim-recipe-contract.v1'


def measure_manim_recipe_bundle():
    content = role.verified_bundle()
    raw = (role.BUNDLE_ROOT / 'manifest.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == role.BUNDLE_MANIFEST_SHA256, 'manim_recipe_bundle_manifest_mismatch')
    manifest = json.loads(raw)
    return {role.BUNDLE_REVISION: manifest}, dict(schema='animation-manim-recipe-bundle-measurement.v1',
        bundle_revision=role.BUNDLE_REVISION, manifest_sha256=role.BUNDLE_MANIFEST_SHA256,
        files=[dict(path=name, sha256=hashlib.sha256(text.encode()).hexdigest(), bytes=len(text.encode()))
            for name, text in sorted(content.items())], local_files_verified=True,
        container_measured=False, model_requests=0, agent_tasks=0, routes_changed=False)


def prepare_manim_recipe_catalog(current, *, worker_image, resource_manifests, source_pool_id,
        runtime_revision=None, model_policy=None, caller=None, register_caller=True,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    before = deepcopy(current)
    existing = registry(current)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image), 'manim_recipe_worker_latest_digest_required')
    require(type(register_caller) is bool, 'manim_recipe_caller_registration_invalid')
    manifests, _ = measure_manim_recipe_bundle()
    require(resource_manifests == manifests, 'manim_recipe_bundle_manifest_mismatch')
    pool, source_binding = _pool(current, existing, source_pool_id)
    environment = existing.environment(source_binding.tool_environment.name, source_binding.tool_environment.revision)
    require(environment is not None, 'manim_recipe_source_environment_required')
    model = _model(source_binding.model_policy if model_policy is None else model_policy,
        {'text', 'tool_calls'}, 'manim_recipe_model_policy_required')
    caller = deepcopy(caller) if caller is not None else dict(tenant='internal', caller='video-animation-manim-recipe',
        profiles=list(PROFILES), token_ref='video-animation-manim-recipe')
    require(type(caller) is dict and set(caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
        and caller['profiles'] == list(PROFILES)
        and not any(item['caller'] == caller['caller'] or item['token_ref'] == caller['token_ref']
            for item in current['callers']), 'manim_recipe_independent_caller_required')
    handler = implementation(role.PROFILE)
    contract = TaskContract.model_validate(dict(name=role.PROFILE, revision='1',
        task_schema=handler.task_schema, output_schema=handler.output_schema,
        required_tools=list(handler.required_tools), required_model_capabilities=list(required_model_capabilities(handler)),
        max_timeout_seconds=600, **handler.limits.as_dict()))
    bundle = CapabilityBundle.model_validate(dict(name='animation-manim-recipe', revision=role.BUNDLE_REVISION,
        source='original-local-' + role.BUNDLE_REVISION, license='MIT', required_tools=list(handler.required_tools),
        resources=[dict(name='manifest.json', sha256=role.BUNDLE_MANIFEST_SHA256)]
            + [dict(name=row['path'], sha256=row['sha256']) for row in manifests[role.BUNDLE_REVISION]['local_files']]))
    tool_data = dict(name=SKILL_TOOLS_ENVIRONMENT_NAME, tools=list(handler.required_tools),
        capabilities=[handler.output_schema], image_digest=worker_image.rsplit('@', 1)[1],
        runtime_revision=runtime_revision or environment.runtime_revision)
    tool_data['revision'] = skill_tools_environment_revision(tool_data)
    tools = ToolEnvironment.model_validate(tool_data)
    binding_data = dict(id='video-animation-manim-recipe-pi', revision='v1', profile=role.PROFILE,
        profile_revision='1', engine='pi', adapter_revision=PI_ADAPTER_REVISION,
        bundle=dict(name=bundle.name, revision=bundle.revision),
        tool_environment=dict(name=tools.name, revision=tools.revision), model_policy=model.model_dump(mode='json'))
    fingerprint = binding_fingerprint(contract, bundle, tools, ExecutionBinding.model_validate(binding_data))
    binding_data['revision'] = 'binding-' + fingerprint[:20]
    binding = ExecutionBinding.model_validate(binding_data)
    route = dict(profile=role.PROFILE, profile_revision='1', binding_id=binding.id,
        binding_revision=binding.revision, enabled=False)
    name = 'animation-manim-recipe-pi-' + fingerprint[:20]
    worker = deepcopy(pool)
    worker.update(pool_id=name, binding_id=binding.id, binding_revision=binding.revision,
        host_config_map=name, image=worker_image, enabled=False, uid_start=None)
    merged, selected = deepcopy(current), {}
    for section, value, keys in (
        ('task_contracts', contract, ('name', 'revision')), ('capability_bundles', bundle, ('name', 'revision')),
        ('tool_environments', tools, ('name', 'revision')), ('execution_bindings', binding, ('id', 'revision'))):
        row = value.model_dump(mode='json', exclude_none=True)
        append_row(merged, section, row, keys)
        selected[section] = [deepcopy(row)]
    selected['routes'] = [route]
    append_row(merged, 'routes', route, ('profile', 'profile_revision'))
    append_row(merged, 'kubernetes_workers', worker, ('pool_id',))
    if register_caller:
        append_row(merged, 'callers', caller, ('tenant', 'caller'))
    host = HostConfig.model_validate(dict(schema_version=1, engine='pi', binding_id=binding.id,
        binding_revision=binding.revision, profile=dict(name=role.PROFILE, revision='1', enabled=False,
            runtime_revision=tools.runtime_revision, image_digest=tools.image_digest, max_timeout_seconds=600),
        model=dict(model=model.model, provider=model.provider, api='openai-responses', proxy_base_url=model_proxy_url),
        execution_catalog=selected))
    require(host_policy(host).execution_binding.combination_sha256 == fingerprint
        and registry(merged).binding_policy(binding.id, binding.revision).execution_binding.combination_sha256 == fingerprint,
        'manim_recipe_host_binding_mismatch')
    for key, old in before.items():
        require(merged[key][:len(old)] == old if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == old,
            'manim_recipe_existing_catalog_changed')
    require(current == before, 'manim_recipe_current_catalog_mutated')
    receipt = dict(schema='video.manim-recipe-catalog-preparation.v1', profile=role.PROFILE,
        before_sha256=digest(before), after_sha256=digest(merged), combination_sha256=fingerprint,
        source_pool_id=source_pool_id, worker_pool_id=name, worker_host_config_map=name,
        binding_id=binding.id, binding_revision=binding.revision, acceptance_suite=SUITE,
        caller=caller, caller_registered=register_caller, max_model_calls_per_attempt=8,
        maximum_new_recipe_tasks=1, route_enabled=False, pool_enabled=False, acceptance_created=False,
        uid_reserved=False, model_requests=0, secrets_created=False)
    return merged, {role.PROFILE: host.model_dump(mode='json', exclude_none=True)}, receipt
