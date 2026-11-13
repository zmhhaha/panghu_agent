"""Prepare new method author/reviewer Pi identities without granting readiness.

Only inspected pool confinement is inherited. Every new image/model/bundle/tool
combination remains disabled and requires its own actual candidate acceptance.
"""
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
from agent_service.profiles import video_animation_method as method
from agent_service.registry import (CapabilityBundle, ExecutionBinding, TaskContract,
    ToolEnvironment, binding_fingerprint, required_model_capabilities)
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy


PROFILES = (method.AUTHOR_PROFILE, method.REVIEW_PROFILE)
SKILL_PROFILES = ('video.animation-method-author.v2', 'video.animation-method-review.v2')


def _protocol(skills=False):
    require(type(skills) is bool, 'animation_method_protocol_invalid')
    if skills:
        from agent_service.profiles import video_animation_skill_method
        return video_animation_skill_method
    return method


def _manifest(value, *, skills=False):
    method = _protocol(skills)
    raw = (method.BUNDLE_ROOT / 'manifest.json').read_bytes()
    require(type(value) is dict and hashlib.sha256(raw).hexdigest() == method.BUNDLE_MANIFEST_SHA256
        and json.loads(raw) == value,
        'animation_method_bundle_manifest_mismatch')
    rows = value.get('local_files')
    require(value.get('bundle_revision') == method.BUNDLE_REVISION
        and value.get('automatic_updates') is False
        and value.get('upstream_content_vendored') is False and value.get('license') == 'MIT'
        and type(rows) is list and len(rows) == len(method.BUNDLE_FILES)
        and all(type(row) is dict and set(row) == {'path', 'sha256'} for row in rows)
        and {row['path'] for row in rows} == method.BUNDLE_FILES,
        'animation_method_bundle_manifest_mismatch')
    return rows


def measure_animation_method_bundle(*, skills=False):
    """Measure exact local files; neither inspect a container nor call a model."""
    method = _protocol(skills)
    raw = (method.BUNDLE_ROOT / 'manifest.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == method.BUNDLE_MANIFEST_SHA256,
            'animation_method_bundle_manifest_mismatch')
    manifest = json.loads(raw)
    rows = _manifest(manifest, skills=skills)
    content = method.verified_bundle()
    require(set(content) == method.BUNDLE_FILES, 'animation_method_bundle_content_mismatch')
    return {method.BUNDLE_REVISION: manifest}, dict(
        schema='animation-method-local-bundle-measurement.v1',
        bundle_revision=method.BUNDLE_REVISION, manifest_sha256=method.BUNDLE_MANIFEST_SHA256,
        local_files=[dict(path=row['path'], sha256=row['sha256'],
            bytes=len(content[row['path']].encode('utf-8'))) for row in rows],
        local_files_verified=True, container_measured=False, vision_delivery_verified=False,
        model_requests=0, agent_tasks=0, routes_changed=False, secrets_created=False)


def prepare_animation_method_catalog(current, *, worker_image, resource_manifests,
        source_pool_id, author_model_policy=None, review_model_policy=None,
        caller=None, runtime_revision=None, register_caller=True,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1',
        skills=False):
    """Return additive catalog, exact disabled hosts, and preparation receipt.

    The source pool supplies confinement only. New models can be explicitly
    proposed; a vision policy declaration is never actual frame-delivery proof.
    A dedicated caller can be registered without reusing another service token.
    """
    method = _protocol(skills)
    profiles = (method.AUTHOR_PROFILE, method.REVIEW_PROFILE)
    role_prefix = 'video-animation-skill-method' if skills else 'video-animation-method'
    bundle_name = 'animation-skill-method' if skills else 'animation-method'
    caller_name = 'video-animation-skill-methods' if skills else 'video-animation-methods'
    before = deepcopy(current)
    existing = registry(current)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image),
        'animation_method_worker_latest_digest_required')
    require(type(register_caller) is bool, 'animation_method_caller_registration_invalid')
    require(type(resource_manifests) is dict and set(resource_manifests) == {method.BUNDLE_REVISION},
            'animation_method_bundle_manifests_required')
    rows = _manifest(resource_manifests[method.BUNDLE_REVISION], skills=skills)
    source_pool, source_binding = _pool(current, existing, source_pool_id)
    source_environment = existing.environment(source_binding.tool_environment.name,
                                               source_binding.tool_environment.revision)
    require(source_environment is not None, 'animation_method_source_environment_required')
    author_model = _model(source_binding.model_policy if author_model_policy is None else author_model_policy,
                         {'text', 'tool_calls'}, 'animation_method_author_model_policy_required')
    review_model = _model(source_binding.model_policy if review_model_policy is None else review_model_policy,
                         {'text', 'tool_calls', 'vision'}, 'animation_method_review_model_policy_required')
    caller = deepcopy(caller) if caller is not None else dict(tenant='internal',
        caller=caller_name, profiles=list(profiles), token_ref=caller_name)
    require(type(caller) is dict and set(caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
        and caller['profiles'] == list(profiles)
        and caller['caller'] not in {'video-workbench', 'video-animation-lab'},
        'dedicated_animation_method_caller_required')
    require(not any(row['token_ref'] == caller['token_ref']
        and (row['tenant'], row['caller']) != (caller['tenant'], caller['caller'])
        for row in current['callers']), 'dedicated_animation_method_token_required')

    merged, hosts, combinations = deepcopy(current), {}, []
    for profile, model in ((method.AUTHOR_PROFILE, author_model), (method.REVIEW_PROFILE, review_model)):
        role = 'author' if profile == method.AUTHOR_PROFILE else 'review'
        handler = implementation(profile)
        contract = TaskContract.model_validate(dict(name=profile, revision='1',
            task_schema=handler.task_schema, output_schema=handler.output_schema,
            required_tools=list(handler.required_tools),
            required_model_capabilities=list(required_model_capabilities(handler)),
            max_timeout_seconds=1200, **handler.limits.as_dict()))
        bundle = CapabilityBundle.model_validate(dict(name=bundle_name,
            revision=method.BUNDLE_REVISION, source='original-local-' + method.BUNDLE_REVISION,
            license='MIT', required_tools=list(handler.required_tools),
            resources=[dict(name='manifest.json', sha256=method.BUNDLE_MANIFEST_SHA256)]
                + [dict(name=row['path'], sha256=row['sha256']) for row in rows]))
        environment_data = dict(name=READ_INPUTS_ENVIRONMENT_NAME,
            tools=list(handler.required_tools), capabilities=[handler.output_schema],
            image_digest=worker_image.rsplit('@', 1)[1],
            runtime_revision=runtime_revision or source_environment.runtime_revision)
        environment_data['revision'] = read_inputs_environment_revision(environment_data)
        environment = ToolEnvironment.model_validate(environment_data)
        binding_data = dict(id=role_prefix + '-' + role + '-pi', revision='v1',
            profile=profile, profile_revision='1', engine='pi', adapter_revision=PI_ADAPTER_REVISION,
            bundle=dict(name=bundle.name, revision=bundle.revision),
            tool_environment=dict(name=environment.name, revision=environment.revision),
            model_policy=model.model_dump(mode='json'))
        fingerprint = binding_fingerprint(contract, bundle, environment, ExecutionBinding.model_validate(binding_data))
        binding_data['revision'] = 'binding-' + fingerprint[:20]
        binding = ExecutionBinding.model_validate(binding_data)
        route = dict(profile=profile, profile_revision='1', binding_id=binding.id,
                     binding_revision=binding.revision, enabled=False)
        host_name = ('animation-skill-method-' if skills else 'animation-method-') + role + '-pi-' + fingerprint[:20]
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
            profile=dict(name=profile, revision='1', enabled=False,
                runtime_revision=environment.runtime_revision, image_digest=environment.image_digest,
                max_timeout_seconds=contract.max_timeout_seconds),
            model=dict(model=model.model, provider=model.provider, api='openai-responses',
                       proxy_base_url=model_proxy_url), execution_catalog=selected))
        require(host_policy(host).execution_binding.combination_sha256 == fingerprint,
                'animation_method_host_binding_mismatch')
        hosts[profile] = host.model_dump(mode='json', exclude_none=True)
        combinations.append(dict(profile=profile, profile_revision='1', binding_id=binding.id,
            binding_revision=binding.revision, combination_sha256=fingerprint,
            acceptance_suite=profile.rsplit('.v', 1)[0] + ('-contract.v2' if skills else '-contract.v1'),
            worker_host_config_map=host_name, worker_pool_id=host_name,
            required_model_capabilities=sorted(contract.required_model_capabilities),
            model_policy_sha256=digest(model.model_dump(mode='json')),
            max_model_calls_per_attempt=8,
            route_enabled=False, pool_enabled=False, acceptance_created=False, uid_reserved=False))
    if register_caller:
        append_row(merged, 'callers', caller, ('tenant', 'caller'))
    resolved = registry(merged)
    for host in hosts.values():
        require(host_policy(HostConfig.model_validate(host)).execution_binding
            == resolved.binding_policy(host['binding_id'], host['binding_revision']).execution_binding,
            'animation_method_host_binding_mismatch')
    additive = {*CATALOG_KEYS, 'kubernetes_workers', 'callers'}
    for key, previous in before.items():
        require(merged[key][:len(previous)] == previous if key in additive else merged[key] == previous,
                'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    return merged, hosts, dict(schema='animation-method-catalog-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged), combinations=combinations,
        source_pool_id=source_pool_id, bundles=[dict(bundle_revision=method.BUNDLE_REVISION,
            manifest_sha256=method.BUNDLE_MANIFEST_SHA256)],
        existing_routes_preserved=len(before['routes']), caller_registered=register_caller,
        required_caller_token_ref=caller['token_ref'], route_enabled=False, pool_enabled=False,
        acceptance_created=False, vision_delivery_verified=False, uid_reserved=False,
        model_requests=0, secrets_created=False)


def prepare_animation_method_catalog_upgrade(current, *, worker_image, resource_manifests,
        source_pool_id, author_model_policy=None, review_model_policy=None,
        caller=None, runtime_revision=None, register_caller=False,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1',
        skills=True):
    """Prepare replacement Skill routes without rewriting historical resources.

    Only the two current v2 routes may be replaced, in their original slots.
    Their old bindings, acceptances, pools, host references and UID reservations
    stay exact. New hosts and pools remain disabled and unreserved; admission,
    permanent fresh UID allocation and publication belong to the operator.
    """
    require(skills is True and register_caller is False,
            'animation_method_upgrade_skills_without_caller_registration_required')
    before = deepcopy(current)
    registry(current)
    selected = [(index, row) for index, row in enumerate(current['routes'])
                if row['profile'] in SKILL_PROFILES]
    require(len(selected) == 2
        and {row['profile'] for _, row in selected} == set(SKILL_PROFILES)
        and all(row['profile_revision'] == '1' for _, row in selected),
        'animation_method_upgrade_existing_routes_required')
    selected_caller = deepcopy(caller) if caller is not None else dict(tenant='internal',
        caller='video-animation-skill-methods', profiles=list(SKILL_PROFILES),
        token_ref='video-animation-skill-methods')
    require(sum(row == selected_caller for row in current['callers']) == 1,
            'animation_method_upgrade_existing_caller_required')

    # The additive preparer intentionally rejects replacement identities. Use
    # a private derived input with only these routing pointers removed; never
    # remove their immutable resources or publish the intermediate catalog.
    intermediate = deepcopy(current)
    intermediate['routes'] = [deepcopy(row) for row in current['routes']
                              if row['profile'] not in SKILL_PROFILES]
    merged, hosts, preparation = prepare_animation_method_catalog(intermediate,
        worker_image=worker_image, resource_manifests=resource_manifests,
        source_pool_id=source_pool_id, author_model_policy=author_model_policy,
        review_model_policy=review_model_policy, caller=selected_caller,
        runtime_revision=runtime_revision, register_caller=False,
        model_proxy_url=model_proxy_url, skills=True)
    replacements = {row['profile']: deepcopy(row) for row in merged['routes']
                    if row['profile'] in SKILL_PROFILES}
    require(set(replacements) == set(SKILL_PROFILES)
        and all((row['binding_id'], row['binding_revision']) !=
                (replacements[row['profile']]['binding_id'], replacements[row['profile']]['binding_revision'])
                for _, row in selected), 'animation_method_upgrade_new_bindings_required')
    merged['routes'] = [deepcopy(replacements.get(row['profile'], row))
                        for row in before['routes']]
    additive = {'task_contracts', 'capability_bundles', 'tool_environments',
                'execution_bindings', 'kubernetes_workers'}
    for key, previous in before.items():
        if key == 'routes':
            continue
        require(merged[key][:len(previous)] == previous if key in additive else merged[key] == previous,
                'animation_method_upgrade_historical_records_changed')
    require(len(merged['kubernetes_workers']) == len(before['kubernetes_workers']) + 2,
            'animation_method_upgrade_two_new_pools_required')
    registry(merged)
    require(current == before, 'current_catalog_mutated')
    return merged, hosts, dict(preparation,
        schema='animation-method-catalog-upgrade-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged),
        derived_preparation_before_sha256=preparation['before_sha256'],
        replaced_routes=[dict(index=index, before=deepcopy(row),
            after=deepcopy(replacements[row['profile']])) for index, row in selected],
        existing_routes_preserved=len(before['routes']) - 2,
        historical_resources_preserved=True, old_pools_and_uids_preserved=True,
        caller_registered=False, existing_callers_unchanged=True)
