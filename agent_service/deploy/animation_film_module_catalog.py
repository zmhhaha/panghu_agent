"""Deliberate current-route upgrade; immutable former bindings remain intact."""
from copy import deepcopy

from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import digest
from agent_service.deploy.animation_production_catalog import prepare_animation_production_catalog
from agent_service.deploy.video_catalog import registry, require
from agent_service.profiles.video_animation_film_module import PROFILE


def prepare_animation_film_module_catalog_upgrade(current, *, worker_image, resource_manifests,
        source_pool_id, model_policy=None, caller=None, runtime_revision=None,
        model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1'):
    """Replace only the selected module route with a disabled fresh binding.

    The old bundle, binding, acceptance, pool, UID reservation and callers stay
    unchanged. The operator must separately admit/enable the new combination.
    The private route-less intermediate is never a publishable deployment.
    """
    before = deepcopy(current)
    registry(current)
    selected = [(index, row) for index, row in enumerate(current['routes']) if row['profile'] == PROFILE]
    require(len(selected) == 1 and selected[0][1]['profile_revision'] == '1',
        'film_module_upgrade_existing_route_required')
    matches = [row for row in current['callers'] if row.get('profiles') == [PROFILE]]
    selected_caller = deepcopy(caller) if caller is not None else deepcopy(matches[0]) if len(matches) == 1 else None
    require(type(selected_caller) is dict and set(selected_caller) == {'tenant', 'caller', 'profiles', 'token_ref'}
        and selected_caller['profiles'] == [PROFILE]
        and sum(row == selected_caller for row in current['callers']) == 1,
        'film_module_upgrade_existing_caller_required')
    intermediate = deepcopy(current)
    intermediate['routes'] = [row for row in intermediate['routes'] if row['profile'] != PROFILE]
    merged, hosts, preparation = prepare_animation_production_catalog(intermediate,
        worker_image=worker_image, resource_manifests=resource_manifests, source_pool_id=source_pool_id,
        model_policy=model_policy, caller=selected_caller, runtime_revision=runtime_revision,
        register_caller=False, modules=True, model_proxy_url=model_proxy_url)
    replacement, = [row for row in merged['routes'] if row['profile'] == PROFILE]
    original = selected[0][1]
    require((original['binding_id'], original['binding_revision']) !=
        (replacement['binding_id'], replacement['binding_revision']), 'film_module_upgrade_new_binding_required')
    merged['routes'] = [deepcopy(replacement if row['profile'] == PROFILE else row) for row in before['routes']]
    additive = {*CATALOG_KEYS, 'kubernetes_workers'} - {'routes'}
    for key, previous in before.items():
        if key == 'routes':
            continue
        require(merged[key][:len(previous)] == previous if key in additive else merged[key] == previous,
            'film_module_upgrade_historical_records_changed')
    require(len(merged['kubernetes_workers']) == len(before['kubernetes_workers']) + 1,
        'film_module_upgrade_new_pool_required')
    registry(merged)
    require(current == before, 'current_catalog_mutated')
    return merged, hosts, dict(preparation, schema='animation-film-module-catalog-upgrade-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged),
        derived_preparation_before_sha256=preparation['before_sha256'],
        replaced_routes=[dict(index=selected[0][0], before=deepcopy(original), after=deepcopy(replacement))],
        existing_routes_preserved=len(before['routes']) - 1, historical_resources_preserved=True,
        old_pools_and_uids_preserved=True, caller_registered=False, existing_callers_unchanged=True)
