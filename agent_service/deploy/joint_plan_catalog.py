"""Prepare an independent disabled joint planner; inherit no acceptance."""
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
from agent_service.profiles import video_joint_plan as planner
from agent_service.registry import (CapabilityBundle, ExecutionBinding, TaskContract,
    ToolEnvironment, binding_fingerprint, required_model_capabilities)
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy


PROFILES = (planner.PROFILE,)


def measure_joint_plan_bundle():
    raw = (planner.BUNDLE_ROOT / 'manifest.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == planner.BUNDLE_MANIFEST_SHA256,
            'joint_plan_bundle_manifest_mismatch')
    manifest = json.loads(raw)
    content = planner.verified_bundle()
    return {planner.BUNDLE_REVISION: manifest}, dict(
        schema='joint-plan-local-bundle-measurement.v1',
        bundle_revision=planner.BUNDLE_REVISION, manifest_sha256=planner.BUNDLE_MANIFEST_SHA256,
        local_files=[dict(path=row['path'], sha256=row['sha256'], bytes=len(content[row['path']].encode()))
                     for row in manifest['local_files']], local_files_verified=True,
        container_measured=False, production_task_exercised=False, model_requests=0,
        agent_tasks=0, routes_changed=False, secrets_created=False)


def prepare_joint_plan_catalog(current, *, worker_image, resource_manifests,
        source_pool_id, model_policy=None, caller=None, runtime_revision=None,
        register_caller=True,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    before = deepcopy(current)
    existing = registry(current)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image),
        'joint_plan_worker_latest_digest_required')
    require(type(register_caller) is bool, 'joint_plan_caller_registration_invalid')
    manifests, _ = measure_joint_plan_bundle()
    require(resource_manifests == manifests, 'joint_plan_bundle_manifest_mismatch')
    rows = manifests[planner.BUNDLE_REVISION]['local_files']
    source_pool, source_binding = _pool(current, existing, source_pool_id)
    source_environment = existing.environment(source_binding.tool_environment.name,
                                               source_binding.tool_environment.revision)
    require(source_environment is not None, 'joint_plan_source_environment_required')
    model = _model(source_binding.model_policy if model_policy is None else model_policy,
                   {'text', 'tool_calls'}, 'joint_plan_model_policy_required')
    caller = deepcopy(caller) if caller is not None else dict(tenant='internal',
        caller='video-joint-plan', profiles=list(PROFILES), token_ref='video-joint-plan')
    require(type(caller) is dict and set(caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
        and caller['profiles'] == list(PROFILES)
        and caller['caller'] == 'video-joint-plan' and caller['token_ref'] == 'video-joint-plan',
        'dedicated_joint_plan_caller_required')
    require(not any(row['token_ref'] == caller['token_ref']
        and (row['tenant'], row['caller']) != (caller['tenant'], caller['caller'])
        for row in current['callers']), 'dedicated_joint_plan_token_required')
    handler = implementation(planner.PROFILE)
    contract = TaskContract.model_validate(dict(name=planner.PROFILE, revision='1',
        task_schema=handler.task_schema, output_schema=handler.output_schema,
        required_tools=list(handler.required_tools),
        required_model_capabilities=list(required_model_capabilities(handler)),
        max_timeout_seconds=600, **handler.limits.as_dict()))
    bundle = CapabilityBundle.model_validate(dict(name='video-joint-plan',
        revision=planner.BUNDLE_REVISION, source='original-local-' + planner.BUNDLE_REVISION,
        license='MIT', required_tools=list(handler.required_tools),
        resources=[dict(name='manifest.json', sha256=planner.BUNDLE_MANIFEST_SHA256)]
            + [dict(name=row['path'], sha256=row['sha256']) for row in rows]))
    environment_data = dict(name=READ_INPUTS_ENVIRONMENT_NAME,
        tools=list(handler.required_tools), capabilities=[handler.output_schema],
        image_digest=worker_image.rsplit('@', 1)[1],
        runtime_revision=runtime_revision or source_environment.runtime_revision)
    environment_data['revision'] = read_inputs_environment_revision(environment_data)
    environment = ToolEnvironment.model_validate(environment_data)
    binding_data = dict(id='video-joint-plan-pi', revision='v1',
        profile=planner.PROFILE, profile_revision='1', engine='pi', adapter_revision=PI_ADAPTER_REVISION,
        bundle=dict(name=bundle.name, revision=bundle.revision),
        tool_environment=dict(name=environment.name, revision=environment.revision),
        model_policy=model.model_dump(mode='json'))
    fingerprint = binding_fingerprint(contract, bundle, environment, ExecutionBinding.model_validate(binding_data))
    binding_data['revision'] = 'binding-' + fingerprint[:20]
    binding = ExecutionBinding.model_validate(binding_data)
    route = dict(profile=planner.PROFILE, profile_revision='1', binding_id=binding.id,
                 binding_revision=binding.revision, enabled=False)
    host_name = 'joint-plan-pi-' + fingerprint[:20]
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
        profile=dict(name=planner.PROFILE, revision='1', enabled=False,
            runtime_revision=environment.runtime_revision, image_digest=environment.image_digest,
            max_timeout_seconds=contract.max_timeout_seconds),
        model=dict(model=model.model, provider=model.provider, api='openai-responses',
                   proxy_base_url=model_proxy_url), execution_catalog=selected))
    policy = host_policy(host).execution_binding
    require(policy.combination_sha256 == fingerprint
        and policy == registry(merged).binding_policy(binding.id, binding.revision).execution_binding,
        'joint_plan_host_binding_mismatch')
    for key, previous in before.items():
        require(merged[key][:len(previous)] == previous
                if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == previous,
                'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    return merged, {planner.PROFILE: host.model_dump(mode='json', exclude_none=True)}, dict(
        schema='joint-plan-catalog-preparation.v1', before_sha256=digest(before),
        after_sha256=digest(merged), source_pool_id=source_pool_id,
        combinations=[dict(profile=planner.PROFILE, profile_revision='1', binding_id=binding.id,
            binding_revision=binding.revision, combination_sha256=fingerprint,
            acceptance_suite='video.joint-plan-contract.v1',
            worker_host_config_map=host_name, worker_pool_id=host_name,
            model_policy_sha256=digest(model.model_dump(mode='json')),
            required_model_capabilities=sorted(contract.required_model_capabilities),
            max_model_calls_per_attempt=8, route_enabled=False, pool_enabled=False,
            acceptance_created=False, uid_reserved=False)],
        bundles=[dict(bundle_revision=planner.BUNDLE_REVISION,
                      manifest_sha256=planner.BUNDLE_MANIFEST_SHA256)],
        existing_routes_preserved=len(before['routes']), caller_registered=register_caller,
        required_caller_token_ref=caller['token_ref'], route_enabled=False, pool_enabled=False,
        acceptance_created=False, uid_reserved=False, production_task_exercised=False,
        model_requests=0, secrets_created=False)
