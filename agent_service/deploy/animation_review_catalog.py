"""Prepare disabled animation roles without changing live authority.

The pure preparation function inherits only inspected Pi operator settings. A
vision policy is a declared candidate requirement, not proof that frames reached
the model. Each new combination still needs its own real candidate acceptance.
Local bundle measurement is available separately and never invokes an Agent.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from types import SimpleNamespace

from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import canonical_bytes, digest
from agent_service.deploy.video_catalog import VideoCatalogError, append_row, registry
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as review
from agent_service.registry import (CapabilityBundle, ExecutionBinding, ModelPolicy,
    TaskContract, ToolEnvironment, binding_fingerprint, required_model_capabilities)
from agent_service.worker_entry import HostConfig, PI_ADAPTER_REVISION, host_policy


PROFILES = (review.OBSERVE_PROFILE, review.REVIEW_PROFILE, creation.PROFILE)
_MODULES = (review, creation)
_VISION = frozenset({'text', 'tool_calls', 'vision'})


def _modules(include_sequence_review, include_batched_review=False):
    require(type(include_sequence_review) is bool, 'animation_sequence_review_selection_invalid')
    require(type(include_batched_review) is bool, 'animation_batch_review_selection_invalid')
    require(not (include_sequence_review and include_batched_review),
            'animation_review_selection_ambiguous')
    if include_batched_review:
        return (creation, SimpleNamespace(
            BUNDLE_ROOT=review.BATCH_BUNDLE_ROOT,
            BUNDLE_REVISION=review.BATCH_BUNDLE_REVISION,
            BUNDLE_MANIFEST_SHA256=review.BATCH_BUNDLE_MANIFEST_SHA256,
            BUNDLE_FILES=review.BATCH_BUNDLE_FILES,
            verified_bundle=review.verified_batch_bundle))
    if not include_sequence_review:
        return _MODULES
    return (*_MODULES, SimpleNamespace(
        BUNDLE_ROOT=review.SEQUENCE_BUNDLE_ROOT,
        BUNDLE_REVISION=review.SEQUENCE_BUNDLE_REVISION,
        BUNDLE_MANIFEST_SHA256=review.SEQUENCE_BUNDLE_MANIFEST_SHA256,
        BUNDLE_FILES=review.SEQUENCE_BUNDLE_FILES,
        verified_bundle=review.verified_sequence_bundle))


def require(value, code):
    if not value:
        raise VideoCatalogError(code)


def _manifest(manifest, module):
    require(type(manifest) is dict and module.BUNDLE_MANIFEST_SHA256 in {
        hashlib.sha256(canonical_bytes(manifest) + suffix).hexdigest() for suffix in (b'', b'\n')},
        'animation_review_bundle_manifest_mismatch')
    rows = manifest.get('local_files')
    require(manifest.get('bundle_revision') == module.BUNDLE_REVISION
            and manifest.get('automatic_updates') is False
            and manifest.get('upstream_content_vendored') is False
            and manifest.get('license') == 'MIT'
            and type(rows) is list and len(rows) == len(module.BUNDLE_FILES)
            and all(type(row) is dict and set(row) == {'path', 'sha256'} for row in rows)
            and {row['path'] for row in rows} == module.BUNDLE_FILES,
            'animation_review_bundle_manifest_mismatch')
    return rows


def measure_animation_review_bundles(*, include_sequence_review=False, include_batched_review=False):
    """Verify frozen bundles, adding sequence review only when explicitly selected.

    This measures local procedure bytes only. Image/container measurement,
    actual frame delivery and model readiness require separate evidence.
    """
    manifests, measured = {}, []
    for module in _modules(include_sequence_review, include_batched_review):
        raw = (module.BUNDLE_ROOT / 'manifest.json').read_bytes()
        require(hashlib.sha256(raw).hexdigest() == module.BUNDLE_MANIFEST_SHA256,
                'animation_review_bundle_manifest_mismatch')
        manifest = json.loads(raw)
        rows = _manifest(manifest, module)
        content = module.verified_bundle()
        require(set(content) == module.BUNDLE_FILES, 'animation_review_bundle_content_mismatch')
        manifests[module.BUNDLE_REVISION] = manifest
        measured.append(dict(bundle_revision=module.BUNDLE_REVISION,
            manifest_sha256=module.BUNDLE_MANIFEST_SHA256,
            local_files=[dict(path=row['path'], sha256=row['sha256'],
                bytes=len(content[row['path']].encode('utf-8'))) for row in rows]))
    return manifests, dict(schema='animation-review-local-bundle-measurement.v1',
        bundles=measured, local_files_verified=True, container_measured=False,
        vision_delivery_verified=False, model_requests=0, agent_tasks=0,
        routes_changed=False, secrets_created=False)


def _pool(current, existing, pool_id):
    require(type(pool_id) is str and bool(pool_id), 'animation_review_source_pool_required')
    matches = [row for row in current['kubernetes_workers'] if row['pool_id'] == pool_id]
    require(len(matches) == 1 and matches[0]['engine'] == 'pi', 'animation_review_source_pi_pool_required')
    pool = matches[0]
    binding = existing.binding(pool['binding_id'], pool['binding_revision'])
    require(binding is not None and binding.engine == 'pi', 'animation_review_source_pi_pool_required')
    return pool, binding


def _model(policy, required, code):
    try:
        parsed = ModelPolicy.model_validate(policy)
    except (TypeError, ValueError):
        raise VideoCatalogError(code) from None
    require(required <= set(parsed.capabilities), code)
    return parsed


def prepare_animation_review_catalog(current, *, worker_image, resource_manifests,
        source_pool_id, vision_source_pool_id=None, vision_source_binding=None,
        vision_model_policy=None, creator_model_policy=None, caller=None,
        runtime_revision=None, include_sequence_review=False, include_batched_review=False,
        creator_worker_image=None, review_only=False, register_caller=True,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    """Return (additive catalog, hosts keyed by profile, preparation receipt).

    source_pool_id is mandatory and supplies runtime/network settings. The
    optional vision source selects only a model policy, never another pool's
    acceptance or permissions. A new explicit policy may prepare a disabled
    candidate; it is not admitted until that exact combination passes real
    acceptance. Default text-only policies are rejected for visual roles.
    """
    modules = _modules(include_sequence_review, include_batched_review)
    profiles = ((creation.PROFILE, review.REVIEW_BATCH_PROFILE) if include_batched_review else
                (*PROFILES, review.REVIEW_SEQUENCE_PROFILE) if include_sequence_review else PROFILES)
    require(type(review_only) is bool and (not review_only or include_batched_review),
            'animation_review_only_selection_invalid')
    require(type(register_caller) is bool, 'animation_review_caller_registration_invalid')
    caller_profiles = profiles
    if review_only:
        profiles = (review.REVIEW_BATCH_PROFILE,)
    before = deepcopy(current)
    existing = registry(current)
    require(type(worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', worker_image),
        'animation_review_worker_latest_digest_required')
    require(creator_worker_image is None or type(creator_worker_image) is str and re.fullmatch(
        r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', creator_worker_image),
        'animation_review_creator_worker_latest_digest_required')
    require(type(resource_manifests) is dict
            and set(resource_manifests) == {module.BUNDLE_REVISION for module in modules},
            'animation_review_bundle_manifests_required')
    for module in modules:
        _manifest(resource_manifests[module.BUNDLE_REVISION], module)
    source_pool, source_binding = _pool(current, existing, source_pool_id)
    source_environment = existing.environment(source_binding.tool_environment.name,
                                               source_binding.tool_environment.revision)
    require(source_environment is not None, 'animation_review_source_environment_required')
    require(vision_source_pool_id is None or vision_source_binding is None,
            'animation_review_vision_source_ambiguous')
    vision_binding = source_binding
    if vision_source_pool_id is not None:
        _, vision_binding = _pool(current, existing, vision_source_pool_id)
    if vision_source_binding is not None:
        require(type(vision_source_binding) is dict and set(vision_source_binding) == {'id', 'revision'},
                'animation_review_vision_binding_required')
        vision_binding = existing.binding(vision_source_binding['id'], vision_source_binding['revision'])
        require(vision_binding is not None and vision_binding.engine == 'pi',
                'animation_review_vision_binding_required')
    visual_model = _model(vision_binding.model_policy if vision_model_policy is None else vision_model_policy,
                          _VISION, 'animation_review_vision_model_policy_required')
    creator_model = _model(source_binding.model_policy if creator_model_policy is None else creator_model_policy,
                           {'text', 'tool_calls'}, 'animation_review_creator_model_policy_required')
    if review_only:
        routes = [row for row in current['routes']
                  if row['profile'] == creation.PROFILE and row['profile_revision'] == '1']
        require(len(routes) == 1, 'animation_review_existing_creator_required')
        selected_creator = existing.binding(routes[0]['binding_id'], routes[0]['binding_revision'])
        require(selected_creator is not None and selected_creator.profile == creation.PROFILE,
                'animation_review_existing_creator_required')
        creator_environment = existing.environment(selected_creator.tool_environment.name,
                                                   selected_creator.tool_environment.revision)
        require(creator_worker_image is None or creator_environment.image_digest == creator_worker_image.rsplit('@', 1)[1],
                'animation_review_existing_creator_image_mismatch')
        require(creator_model_policy is None or dict(creator_model) == dict(selected_creator.model_policy),
                'animation_review_existing_creator_model_mismatch')
    caller_name = ('video-animation-creation-batch' if include_batched_review else
                   'video-animation-creation-sequence' if include_sequence_review else 'video-animation-creation')
    caller = deepcopy(caller) if caller is not None else dict(tenant='internal',
        caller=caller_name, profiles=list(caller_profiles), token_ref=caller_name)
    require(type(caller) is dict and set(caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
            and caller['profiles'] == list(caller_profiles)
            and caller['caller'] not in {'video-workbench', 'video-animation-lab'},
            'dedicated_animation_review_caller_required')

    merged, hosts, combinations = deepcopy(current), {}, []
    for profile in profiles:
        batched = include_batched_review and profile == review.REVIEW_BATCH_PROFILE
        sequence = include_sequence_review and profile == review.REVIEW_SEQUENCE_PROFILE
        visual = profile in {review.OBSERVE_PROFILE, review.REVIEW_PROFILE} or sequence or batched
        module = modules[-1] if sequence or batched else review if visual else creation
        role = ('review-batch-v3' if batched else 'review-sequence-v2' if sequence else 'observe' if profile == review.OBSERVE_PROFILE
                else 'review' if visual else 'create-v2')
        selected_image = worker_image if visual else creator_worker_image or worker_image
        model = visual_model if visual else creator_model
        handler = implementation(profile)
        contract = TaskContract.model_validate(dict(name=profile, revision='1',
            task_schema=handler.task_schema, output_schema=handler.output_schema,
            required_tools=list(handler.required_tools),
            required_model_capabilities=list(required_model_capabilities(handler)),
            max_timeout_seconds=1200, **handler.limits.as_dict()))
        bundle = CapabilityBundle.model_validate(dict(name='animation-review' if visual else 'animation-create',
            revision=module.BUNDLE_REVISION, source='original-local-' + module.BUNDLE_REVISION,
            license='MIT', required_tools=list(handler.required_tools),
            resources=[dict(name='manifest.json', sha256=module.BUNDLE_MANIFEST_SHA256)]
                + [dict(name=row['path'], sha256=row['sha256'])
                   for row in resource_manifests[module.BUNDLE_REVISION]['local_files']]))
        environment_data = dict(name='tools-pi-animation-' + role,
            tools=list(handler.required_tools), capabilities=[handler.output_schema],
            image_digest=selected_image.rsplit('@', 1)[1],
            runtime_revision=runtime_revision or source_environment.runtime_revision)
        environment_data['revision'] = 'env-' + digest(environment_data)[:20]
        environment = ToolEnvironment.model_validate(environment_data)
        binding_data = dict(id='video-animation-' + role + '-pi', revision='v1',
            profile=profile, profile_revision='1', engine='pi', adapter_revision=PI_ADAPTER_REVISION,
            bundle=dict(name=bundle.name, revision=bundle.revision),
            tool_environment=dict(name=environment.name, revision=environment.revision),
            model_policy=model.model_dump(mode='json'))
        fingerprint = binding_fingerprint(contract, bundle, environment, ExecutionBinding.model_validate(binding_data))
        binding_data['revision'] = 'binding-' + fingerprint[:20]
        binding = ExecutionBinding.model_validate(binding_data)
        route = dict(profile=profile, profile_revision='1', binding_id=binding.id,
                     binding_revision=binding.revision, enabled=False)
        host_name = 'animation-' + role + '-pi-' + fingerprint[:20]
        pool = deepcopy(source_pool)
        pool.update(pool_id=host_name, binding_id=binding.id, binding_revision=binding.revision,
                    host_config_map=host_name, image=selected_image, enabled=False, uid_start=None)
        for section, value, keys in (
            ('task_contracts', contract, ('name', 'revision')),
            ('capability_bundles', bundle, ('name', 'revision')),
            ('tool_environments', environment, ('name', 'revision')),
            ('execution_bindings', binding, ('id', 'revision')),
        ):
            append_row(merged, section, value.model_dump(mode='json', exclude_none=True), keys)
        append_row(merged, 'routes', route, ('profile', 'profile_revision'))
        append_row(merged, 'kubernetes_workers', pool, ('pool_id',))
        selected_catalog = dict(task_contracts=[contract.model_dump(mode='json')],
            capability_bundles=[bundle.model_dump(mode='json')],
            tool_environments=[environment.model_dump(mode='json')],
            execution_bindings=[binding.model_dump(mode='json', exclude_none=True)], routes=[route])
        host = HostConfig.model_validate(dict(schema_version=1, engine='pi',
            binding_id=binding.id, binding_revision=binding.revision,
            profile=dict(name=profile, revision='1', enabled=False,
                runtime_revision=environment.runtime_revision, image_digest=environment.image_digest,
                max_timeout_seconds=contract.max_timeout_seconds),
            model=dict(model=model.model, provider=model.provider, api='openai-responses',
                       proxy_base_url=model_proxy_url), execution_catalog=selected_catalog))
        require(host_policy(host).execution_binding.combination_sha256 == fingerprint,
                'animation_review_host_binding_mismatch')
        hosts[profile] = host.model_dump(mode='json', exclude_none=True)
        combinations.append(dict(profile=profile, profile_revision='1', binding_id=binding.id,
            binding_revision=binding.revision, combination_sha256=fingerprint,
            acceptance_suite=profile.rsplit('.v', 1)[0] + '-contract.v' + profile.rsplit('.v', 1)[1],
            worker_host_config_map=host_name, worker_pool_id=host_name,
            required_model_capabilities=sorted(contract.required_model_capabilities),
            model_policy_sha256=digest(model.model_dump(mode='json')),
            route_enabled=False, pool_enabled=False, acceptance_created=False, uid_reserved=False))
    if register_caller:
        append_row(merged, 'callers', caller, ('tenant', 'caller'))
    resolved = registry(merged)
    for profile, host in hosts.items():
        require(host_policy(HostConfig.model_validate(host)).execution_binding
                == resolved.binding_policy(host['binding_id'], host['binding_revision']).execution_binding,
                'animation_review_host_binding_mismatch')
    additive = {*CATALOG_KEYS, 'kubernetes_workers', 'callers'}
    for name, previous in before.items():
        require(merged[name][:len(previous)] == previous if name in additive else merged[name] == previous,
                'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    return merged, hosts, dict(schema_version='animation-review-catalog-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged), combinations=combinations,
        source_pool_id=source_pool_id, vision_policy_source='explicit' if vision_model_policy is not None else
            dict(binding_id=vision_binding.id, binding_revision=vision_binding.revision),
        bundles=[dict(bundle_revision=module.BUNDLE_REVISION, manifest_sha256=module.BUNDLE_MANIFEST_SHA256)
                 for module in modules], existing_routes_preserved=len(before['routes']),
        existing_creator_reused=review_only, caller_registered=register_caller,
        required_caller_token_ref=caller['token_ref'], route_enabled=False, pool_enabled=False,
        acceptance_created=False, vision_delivery_verified=False, uid_reserved=False,
        model_requests=0, secrets_created=False)
