"""Prepare distinct visual video-learning bindings; acceptance stays explicit."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import digest
from agent_service.deploy.animation_review_catalog import _model, _pool
from agent_service.deploy.video_catalog import append_row, registry, require
from agent_service.reference_learning_tools import ENVIRONMENT_NAME, environment_revision, verified_extensions
from agent_service.profiles import implementation
from agent_service.profiles import video_reference_learning as reference
from agent_service.registry import (CapabilityBundle, ExecutionBinding, TaskContract,
    ToolEnvironment, binding_fingerprint, required_model_capabilities)
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy


PROFILES = (reference.AUTHOR_PROFILE, reference.REVIEW_PROFILE)
CALLER = 'video-reference-learning'


def measure_reference_learning_bundle():
    raw = (reference.BUNDLE_ROOT / 'manifest.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == reference.BUNDLE_MANIFEST_SHA256,
        'reference_learning_bundle_changed')
    manifest = json.loads(raw)
    content = reference.verified_bundle()
    verified_extensions()
    return {reference.BUNDLE_REVISION: manifest}, dict(
        schema='reference-learning-local-measurement.v1', bundle_revision=reference.BUNDLE_REVISION,
        manifest_sha256=reference.BUNDLE_MANIFEST_SHA256,
        local_files=[dict(path=row['path'], sha256=row['sha256'], bytes=len(content[row['path']].encode()))
                     for row in manifest['local_files']],
        local_files_verified=True, container_measured=False, vision_delivery_verified=False,
        model_requests=0, routes_changed=False, secrets_created=False)


def prepare_reference_learning_catalog(current, *, worker_image, resource_manifests,
        source_pool_id, author_model_policy=None, review_model_policy=None, caller=None,
        runtime_revision=None, register_caller=True,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    """Inherit confinement, never readiness, credentials or old role acceptance."""
    before = deepcopy(current)
    existing = registry(current)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image),
        'reference_learning_worker_latest_digest_required')
    require(type(register_caller) is bool, 'reference_learning_caller_registration_invalid')
    manifests, _ = measure_reference_learning_bundle()
    require(resource_manifests == manifests, 'reference_learning_bundle_changed')
    rows = manifests[reference.BUNDLE_REVISION]['local_files']
    source_pool, source_binding = _pool(current, existing, source_pool_id)
    source_environment = existing.environment(source_binding.tool_environment.name,
        source_binding.tool_environment.revision)
    require(source_environment is not None, 'reference_learning_source_environment_required')
    author = _model(source_binding.model_policy if author_model_policy is None else author_model_policy,
        {'text', 'tool_calls', 'vision'}, 'reference_learning_author_vision_required')
    reviewer = _model(source_binding.model_policy if review_model_policy is None else review_model_policy,
        {'text', 'tool_calls', 'vision'}, 'reference_learning_reviewer_vision_required')
    caller = deepcopy(caller) if caller is not None else dict(
        tenant='internal', caller=CALLER, profiles=list(PROFILES), token_ref=CALLER)
    require(type(caller) is dict and set(caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
        and caller['profiles'] == list(PROFILES) and caller['caller'] == CALLER
        and type(caller['tenant']) is str and bool(caller['tenant'])
        and type(caller['token_ref']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', caller['token_ref']),
        'dedicated_reference_learning_caller_required')
    require(not any(row['token_ref'] == caller['token_ref']
        and (row['tenant'], row['caller']) != (caller['tenant'], caller['caller'])
        for row in current['callers']), 'dedicated_reference_learning_token_required')
    merged, hosts, combinations = deepcopy(current), {}, []
    for name, model in ((reference.AUTHOR_PROFILE, author), (reference.REVIEW_PROFILE, reviewer)):
        role = 'review' if name == reference.REVIEW_PROFILE else 'author'
        handler = implementation(name)
        contract = TaskContract.model_validate(dict(name=name, revision='1',
            task_schema=handler.task_schema, output_schema=handler.output_schema,
            required_tools=list(handler.required_tools),
            required_model_capabilities=list(required_model_capabilities(handler)),
            max_timeout_seconds=1200, **handler.limits.as_dict()))
        bundle = CapabilityBundle.model_validate(dict(name='reference-learning', revision=reference.BUNDLE_REVISION,
            source='original-local-' + reference.BUNDLE_REVISION, license='MIT',
            required_tools=list(handler.required_tools), resources=[
                dict(name='manifest.json', sha256=reference.BUNDLE_MANIFEST_SHA256),
                *[dict(name=row['path'], sha256=row['sha256']) for row in rows]]))
        environment_data = dict(name=ENVIRONMENT_NAME, tools=list(handler.required_tools),
            capabilities=[handler.output_schema], image_digest=worker_image.rsplit('@', 1)[1],
            runtime_revision=runtime_revision or source_environment.runtime_revision)
        environment_data['revision'] = environment_revision(environment_data)
        environment = ToolEnvironment.model_validate(environment_data)
        binding_data = dict(id='reference-learning-' + role + '-pi', revision='v1', profile=name,
            profile_revision='1', engine='pi', adapter_revision=PI_ADAPTER_REVISION,
            bundle=dict(name=bundle.name, revision=bundle.revision),
            tool_environment=dict(name=environment.name, revision=environment.revision),
            model_policy=model.model_dump(mode='json'))
        fingerprint = binding_fingerprint(contract, bundle, environment, ExecutionBinding.model_validate(binding_data))
        binding_data['revision'] = 'binding-' + fingerprint[:20]
        binding = ExecutionBinding.model_validate(binding_data)
        route = dict(profile=name, profile_revision='1', binding_id=binding.id,
            binding_revision=binding.revision, enabled=False)
        host_name = 'reference-learning-' + role + '-pi-' + fingerprint[:20]
        pool = deepcopy(source_pool)
        pool.update(pool_id=host_name, binding_id=binding.id, binding_revision=binding.revision,
            host_config_map=host_name, image=worker_image, enabled=False, uid_start=None)
        selected = {}
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
        host = HostConfig.model_validate(dict(schema_version=1, engine='pi',
            binding_id=binding.id, binding_revision=binding.revision,
            profile=dict(name=name, revision='1', enabled=False, runtime_revision=environment.runtime_revision,
                image_digest=environment.image_digest, max_timeout_seconds=contract.max_timeout_seconds),
            model=dict(model=model.model, provider=model.provider, api='openai-responses',
                proxy_base_url=model_proxy_url), execution_catalog=selected))
        require(host_policy(host).execution_binding.combination_sha256 == fingerprint,
            'reference_learning_host_binding_mismatch')
        hosts[name] = host.model_dump(mode='json', exclude_none=True)
        combinations.append(dict(profile=name, profile_revision='1', binding_id=binding.id,
            binding_revision=binding.revision, combination_sha256=fingerprint,
            acceptance_suite=name.rsplit('.v', 1)[0] + '-contract.v1',
            worker_host_config_map=host_name, worker_pool_id=host_name,
            required_model_capabilities=sorted(contract.required_model_capabilities),
            route_enabled=False, pool_enabled=False, acceptance_created=False, uid_reserved=False))
    if register_caller:
        append_row(merged, 'callers', caller, ('tenant', 'caller'))
    registry(merged)
    for key, previous in before.items():
        require(merged[key][:len(previous)] == previous
            if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == previous,
            'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    return merged, hosts, dict(schema='reference-learning-catalog-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged), source_pool_id=source_pool_id,
        combinations=combinations, required_caller_token_ref=caller['token_ref'],
        route_enabled=False, pool_enabled=False, acceptance_created=False, uid_reserved=False,
        model_requests=0, secrets_created=False, vision_delivery_verified=False)
