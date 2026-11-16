"""Prepare one independent disabled animation-lab combination, without I/O.

The pure preparation function takes an inspected current public configuration,
an immutable worker image and the measured bundle manifest. It preserves every
existing row, creates no acceptance, and never reads or creates credentials.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import canonical_bytes, digest
from agent_service.deploy.video_catalog import VideoCatalogError, append_row, registry
from agent_service.profiles import implementation
from agent_service.profiles.video_animation_experiment import (
    PROFILE, BUNDLE_REVISION, BUNDLE_FILES, BUNDLE_MANIFEST_SHA256)
from agent_service.registry import (CapabilityBundle, ExecutionBinding, TaskContract,
    ToolEnvironment, binding_fingerprint)
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy


def require(value, code):
    if not value:
        raise VideoCatalogError(code)


def prepare_animation_catalog(current, *, worker_image, resource_manifest, source_pool_id,
                              caller=None, runtime_revision=None,
                              model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    """Return (additive public config, disabled host config, preparation summary).

    source_pool_id explicitly chooses the existing Pi model/runtime/network
    operator settings to inherit. No accepted binding, UID reservation or
    caller credential is inherited as authority for this new combination.
    """
    before = deepcopy(current)
    existing = registry(current)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image),
        'animation_worker_latest_digest_required')
    require(type(resource_manifest) is dict and hashlib.sha256(
        canonical_bytes(resource_manifest) + b'\n').hexdigest() == BUNDLE_MANIFEST_SHA256,
        'animation_bundle_manifest_mismatch')
    require(resource_manifest.get('bundle_revision') == BUNDLE_REVISION
        and resource_manifest.get('automatic_updates') is False
        and resource_manifest.get('upstream_content_vendored') is False,
        'animation_bundle_manifest_mismatch')
    rows = resource_manifest.get('local_files')
    require(type(rows) is list and len(rows) == len(BUNDLE_FILES)
        and {row.get('path') for row in rows} == BUNDLE_FILES,
        'animation_bundle_manifest_mismatch')
    sources = [row for row in current['kubernetes_workers'] if row['pool_id'] == source_pool_id]
    require(len(sources) == 1 and sources[0]['engine'] == 'pi', 'animation_source_pi_pool_required')
    source_pool = sources[0]
    source_binding = existing.binding(source_pool['binding_id'], source_pool['binding_revision'])
    require(source_binding is not None and source_binding.engine == 'pi', 'animation_source_pi_pool_required')
    source_environment = existing.environment(source_binding.tool_environment.name,
                                               source_binding.tool_environment.revision)
    require(source_environment is not None, 'animation_source_environment_required')
    handler = implementation(PROFILE)
    contract = TaskContract.model_validate(dict(name=PROFILE, revision='1',
        task_schema=handler.task_schema, output_schema=handler.output_schema,
        required_tools=list(handler.required_tools), required_model_capabilities=['text', 'tool_calls'],
        max_timeout_seconds=1200, **handler.limits.as_dict()))
    bundle = CapabilityBundle.model_validate(dict(name='animation-experiment', revision=BUNDLE_REVISION,
        resources=[dict(name='manifest.json', sha256=BUNDLE_MANIFEST_SHA256)]
            + [dict(name=row['path'], sha256=row['sha256']) for row in rows],
        source='original-local-animation-experiment-procedures-v1', license='MIT',
        required_tools=['read', 'write']))
    environment_data = dict(name='tools-pi-animation-experiment', tools=['read', 'write'],
        capabilities=[handler.output_schema], image_digest=worker_image.rsplit('@', 1)[1],
        runtime_revision=runtime_revision or source_environment.runtime_revision)
    environment_data['revision'] = 'env-' + digest(environment_data)[:20]
    environment = ToolEnvironment.model_validate(environment_data)
    binding_data = dict(id='video-animation-experiment-pi', revision='v1', profile=PROFILE,
        profile_revision='1', bundle=dict(name=bundle.name, revision=bundle.revision),
        tool_environment=dict(name=environment.name, revision=environment.revision), engine='pi',
        adapter_revision=PI_ADAPTER_REVISION,
        model_policy=source_binding.model_policy.model_dump(mode='json'))
    binding = ExecutionBinding.model_validate(binding_data)
    fingerprint = binding_fingerprint(contract, bundle, environment, binding)
    binding_data['revision'] = 'binding-' + fingerprint[:20]
    binding = ExecutionBinding.model_validate(binding_data)
    route = dict(profile=PROFILE, profile_revision='1', binding_id=binding.id,
                 binding_revision=binding.revision, enabled=False)
    host_name = 'animation-experiment-pi-' + fingerprint[:20]
    pool = deepcopy(source_pool)
    pool.update(pool_id=host_name, binding_id=binding.id, binding_revision=binding.revision,
        host_config_map=host_name, image=worker_image, enabled=False, uid_start=None)
    caller = deepcopy(caller) if caller is not None else dict(tenant='internal', caller='video-animation-lab',
        profiles=[PROFILE], token_ref='video-animation-lab')
    require(type(caller) is dict and set(caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
        and caller['profiles'] == [PROFILE]
        and caller['caller'] != 'video-workbench', 'dedicated_animation_caller_required')
    merged = deepcopy(current)
    for section, value, keys in (
        ('task_contracts', contract, ('name', 'revision')),
        ('capability_bundles', bundle, ('name', 'revision')),
        ('tool_environments', environment, ('name', 'revision')),
        ('execution_bindings', binding, ('id', 'revision')),
    ):
        append_row(merged, section, value.model_dump(mode='json', exclude_none=True), keys)
    append_row(merged, 'routes', route, ('profile', 'profile_revision'))
    append_row(merged, 'kubernetes_workers', pool, ('pool_id',))
    append_row(merged, 'callers', caller, ('tenant', 'caller'))
    resolved = registry(merged)
    selected_catalog = dict(task_contracts=[contract.model_dump(mode='json')],
        capability_bundles=[bundle.model_dump(mode='json')],
        tool_environments=[environment.model_dump(mode='json')],
        execution_bindings=[binding.model_dump(mode='json', exclude_none=True)], routes=[route])
    host = HostConfig.model_validate(dict(schema_version=1, engine='pi',
        binding_id=binding.id, binding_revision=binding.revision,
        profile=dict(name=PROFILE, revision='1', runtime_revision=environment.runtime_revision,
            image_digest=environment.image_digest, enabled=False,
            max_timeout_seconds=contract.max_timeout_seconds),
        model=dict(model=binding.model_policy.model, provider=binding.model_policy.provider,
            api='openai-responses', proxy_base_url=model_proxy_url), execution_catalog=selected_catalog))
    require(host_policy(host).execution_binding == resolved.binding_policy(binding.id,
        binding.revision).execution_binding, 'animation_host_binding_mismatch')
    additive = {*CATALOG_KEYS, 'kubernetes_workers', 'callers'}
    for name, previous in before.items():
        require(merged[name][:len(previous)] == previous if name in additive
            else merged[name] == previous, 'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    return merged, host.model_dump(mode='json', exclude_none=True), dict(
        schema_version='animation-experiment-catalog-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged),
        profile=PROFILE, binding_id=binding.id, binding_revision=binding.revision,
        combination_sha256=fingerprint, acceptance_suite=PROFILE.replace('.v1', '-contract.v1'),
        worker_host_config_map=host_name, worker_pool_id=host_name,
        existing_routes_preserved=len(before['routes']),
        required_caller_token_ref=caller['token_ref'],
        route_enabled=False, pool_enabled=False, acceptance_created=False,
        uid_reserved=False, model_requests=0, secrets_created=False)
