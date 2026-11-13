"""Prepare one disabled production author; never inherit old acceptance."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import digest
from agent_service.deploy.animation_review_catalog import _model, _pool
from agent_service.deploy.video_catalog import append_row, registry, require
from agent_service.input_tools import READ_INPUTS_ENVIRONMENT_NAME, read_inputs_environment_revision
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_production as production
from agent_service.profiles import video_animation_skill_production as skill_production
from agent_service.skill_tools import SKILL_TOOLS_ENVIRONMENT_NAME, skill_tools_environment_revision
from agent_service.registry import (CapabilityBundle, ExecutionBinding, TaskContract,
    ToolEnvironment, binding_fingerprint, required_model_capabilities)
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy


PROFILES = (production.PROFILE,)


def measure_animation_production_bundle(*, film=False, skills=False):
    require(type(film) is bool and type(skills) is bool and not (film and skills),
            'animation_production_generation_invalid')
    role = skill_production if skills else production
    root = production.FILM_BUNDLE_ROOT if film else role.BUNDLE_ROOT
    revision = production.FILM_BUNDLE_REVISION if film else role.BUNDLE_REVISION
    manifest_sha = production.FILM_BUNDLE_MANIFEST_SHA256 if film else role.BUNDLE_MANIFEST_SHA256
    raw = (root / 'manifest.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == manifest_sha,
            'animation_production_bundle_manifest_mismatch')
    manifest = json.loads(raw)
    content = skill_production.verified_bundle() if skills else production.verified_bundle(film=film)
    return {revision: manifest}, dict(
        schema='animation-skill-production-local-bundle-measurement.v1' if skills else
               'animation-film-production-local-bundle-measurement.v1' if film else 'animation-production-local-bundle-measurement.v1',
        bundle_revision=revision, manifest_sha256=manifest_sha,
        local_files=[dict(path=row['path'], sha256=row['sha256'], bytes=len(content[row['path']].encode()))
                     for row in manifest['local_files']], local_files_verified=True,
        container_measured=False, production_task_exercised=False, model_requests=0,
        agent_tasks=0, routes_changed=False, secrets_created=False)


def prepare_animation_production_catalog(current, *, worker_image, resource_manifests,
        source_pool_id, model_policy=None, caller=None, runtime_revision=None,
        register_caller=True, film=False, skills=False,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    require(type(film) is bool and type(skills) is bool and not (film and skills),
            'animation_production_generation_invalid')
    role = skill_production if skills else production
    role_name = 'animation-skill-production' if skills else 'animation-film-production' if film else 'animation-production'
    profiles = (skill_production.PROFILE,) if skills else (production.FILM_PROFILE,) if film else PROFILES
    profile = profiles[0]
    bundle_revision = production.FILM_BUNDLE_REVISION if film else role.BUNDLE_REVISION
    bundle_manifest_sha = production.FILM_BUNDLE_MANIFEST_SHA256 if film else role.BUNDLE_MANIFEST_SHA256
    before = deepcopy(current)
    existing = registry(current)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image),
        'animation_production_worker_latest_digest_required')
    require(type(register_caller) is bool, 'animation_production_caller_registration_invalid')
    manifests, _ = measure_animation_production_bundle(film=film, skills=skills)
    require(resource_manifests == manifests, 'animation_production_bundle_manifest_mismatch')
    rows = manifests[bundle_revision]['local_files']
    source_pool, source_binding = _pool(current, existing, source_pool_id)
    source_environment = existing.environment(source_binding.tool_environment.name,
                                               source_binding.tool_environment.revision)
    require(source_environment is not None, 'animation_production_source_environment_required')
    model = _model(source_binding.model_policy if model_policy is None else model_policy,
                   {'text', 'tool_calls'}, 'animation_production_model_policy_required')
    caller = deepcopy(caller) if caller is not None else dict(tenant='internal',
        caller='video-' + role_name,
        profiles=list(profiles), token_ref='video-' + role_name)
    require(type(caller) is dict and set(caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
        and caller['profiles'] == list(profiles)
        and caller['caller'] not in {'video-workbench', 'video-animation-lab', 'video-animation-methods'},
        'dedicated_animation_production_caller_required')
    require(not any(row['token_ref'] == caller['token_ref']
        and (row['tenant'], row['caller']) != (caller['tenant'], caller['caller'])
        for row in current['callers']), 'dedicated_animation_production_token_required')
    handler = implementation(profile)
    contract = TaskContract.model_validate(dict(name=profile, revision='1',
        task_schema=handler.task_schema, output_schema=handler.output_schema,
        required_tools=list(handler.required_tools),
        required_model_capabilities=list(required_model_capabilities(handler)),
        max_timeout_seconds=1200, **handler.limits.as_dict()))
    bundle = CapabilityBundle.model_validate(dict(name='animation-production',
        revision=bundle_revision, source='original-local-' + bundle_revision,
        license='MIT', required_tools=list(handler.required_tools),
        resources=[dict(name='manifest.json', sha256=bundle_manifest_sha)]
            + [dict(name=row['path'], sha256=row['sha256']) for row in rows]))
    environment_data = dict(name=SKILL_TOOLS_ENVIRONMENT_NAME if skills else READ_INPUTS_ENVIRONMENT_NAME,
        tools=list(handler.required_tools), capabilities=[handler.output_schema],
        image_digest=worker_image.rsplit('@', 1)[1],
        runtime_revision=runtime_revision or source_environment.runtime_revision)
    environment_data['revision'] = (skill_tools_environment_revision(environment_data) if skills else
                                    read_inputs_environment_revision(environment_data))
    environment = ToolEnvironment.model_validate(environment_data)
    binding_data = dict(id='video-' + role_name + '-pi', revision='v1',
        profile=profile, profile_revision='1', engine='pi', adapter_revision=PI_ADAPTER_REVISION,
        bundle=dict(name=bundle.name, revision=bundle.revision),
        tool_environment=dict(name=environment.name, revision=environment.revision),
        model_policy=model.model_dump(mode='json'))
    fingerprint = binding_fingerprint(contract, bundle, environment, ExecutionBinding.model_validate(binding_data))
    binding_data['revision'] = 'binding-' + fingerprint[:20]
    binding = ExecutionBinding.model_validate(binding_data)
    route = dict(profile=profile, profile_revision='1', binding_id=binding.id,
                 binding_revision=binding.revision, enabled=False)
    host_name = role_name + '-pi-' + fingerprint[:20]
    pool = deepcopy(source_pool)
    pool.update(pool_id=host_name, binding_id=binding.id, binding_revision=binding.revision,
                host_config_map=host_name, image=worker_image, enabled=False, uid_start=None)
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
    append_row(merged, 'kubernetes_workers', pool, ('pool_id',))
    if register_caller:
        append_row(merged, 'callers', caller, ('tenant', 'caller'))
    host = HostConfig.model_validate(dict(schema_version=1, engine='pi',
        binding_id=binding.id, binding_revision=binding.revision,
        profile=dict(name=profile, revision='1', enabled=False,
            runtime_revision=environment.runtime_revision, image_digest=environment.image_digest,
            max_timeout_seconds=contract.max_timeout_seconds),
        model=dict(model=model.model, provider=model.provider, api='openai-responses',
                   proxy_base_url=model_proxy_url), execution_catalog=selected))
    policy = host_policy(host).execution_binding
    require(policy.combination_sha256 == fingerprint
        and policy == registry(merged).binding_policy(binding.id, binding.revision).execution_binding,
        'animation_production_host_binding_mismatch')
    for key, previous in before.items():
        require(merged[key][:len(previous)] == previous
                if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == previous,
                'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    return merged, {profile: host.model_dump(mode='json', exclude_none=True)}, dict(
        schema=role_name + '-catalog-preparation.v1', before_sha256=digest(before),
        after_sha256=digest(merged), source_pool_id=source_pool_id,
        combinations=[dict(profile=profile, profile_revision='1', binding_id=binding.id,
            binding_revision=binding.revision, combination_sha256=fingerprint,
            acceptance_suite='video.animation-production-contract.v3' if skills else
                'video.animation-production-contract.v2' if film else 'video.animation-production-contract.v1',
            worker_host_config_map=host_name, worker_pool_id=host_name,
            model_policy_sha256=digest(model.model_dump(mode='json')),
            required_model_capabilities=sorted(contract.required_model_capabilities),
            max_model_calls_per_attempt=8, route_enabled=False, pool_enabled=False,
            acceptance_created=False, uid_reserved=False)],
        bundles=[dict(bundle_revision=bundle_revision,
                      manifest_sha256=bundle_manifest_sha)],
        existing_routes_preserved=len(before['routes']), caller_registered=register_caller,
        required_caller_token_ref=caller['token_ref'], route_enabled=False, pool_enabled=False,
        acceptance_created=False, uid_reserved=False, production_task_exercised=False,
        model_requests=0, secrets_created=False)
