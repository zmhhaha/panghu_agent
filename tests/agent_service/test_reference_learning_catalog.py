"""New picture roles inherit confinement and require their own acceptance."""
from copy import deepcopy

import pytest

from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.deploy.reference_learning_catalog import (
    CALLER, PROFILES, measure_reference_learning_bundle, prepare_reference_learning_catalog)
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.reference_learning_tools import ENVIRONMENT_NAME, environment_revision
from test_animation_review_catalog import IMAGE, public_config, model


def prepare(current=None, **changes):
    manifests, _ = measure_reference_learning_bundle()
    settings = dict(worker_image=IMAGE, resource_manifests=manifests,
        source_pool_id='existing-pool', author_model_policy=model(), review_model_policy=model())
    settings.update(changes)
    return prepare_reference_learning_catalog(public_config() if current is None else current, **settings)


def test_catalog_adds_two_distinct_vision_roles_with_disabled_routes_and_no_acceptance():
    current = public_config()
    before = deepcopy(current)
    merged, hosts, summary = prepare(current)
    assert current == before
    for key in {*CATALOG_KEYS, 'callers', 'kubernetes_workers'}:
        assert merged[key][:len(before[key])] == before[key]
    assert set(hosts) == set(PROFILES)
    assert merged['callers'][-1]['caller'] == CALLER
    assert summary['acceptance_created'] is summary['vision_delivery_verified'] is False
    assert summary['model_requests'] == 0
    resolved = registry(merged)
    for name, host in hosts.items():
        parsed, policy, binding = candidate_config(host)
        assert parsed.engine == 'pi'
        assert policy.enabled is False and binding.acceptance is None
        assert set(resolved.contract(name, '1').required_model_capabilities) == {'text', 'vision', 'tool_calls'}
        env = resolved.environment(binding.tool_environment.name, binding.tool_environment.revision)
        assert env.name == ENVIRONMENT_NAME
        assert env.revision == environment_revision(env.model_dump(mode='json'))
        assert set(env.tools) == {'read_inputs', 'skill_search', 'skill_read', 'write'}
        full = dict(deepcopy(host), execution_catalog={key: deepcopy(merged[key]) for key in CATALOG_KEYS})
        projected = project_animation_worker_host(full)
        assert all(len(rows) == 1 for rows in projected['execution_catalog'].values())


@pytest.mark.parametrize('role', ['author_model_policy', 'review_model_policy'])
def test_text_only_model_cannot_admit_either_new_role(role):
    with pytest.raises(VideoCatalogError, match='vision_required'):
        prepare(**{role: model(vision=False)})


def test_modified_bundle_or_unpinned_worker_is_rejected():
    manifests, _ = measure_reference_learning_bundle()
    manifests[next(iter(manifests))]['local_files'][0]['sha256'] = 'b' * 64
    with pytest.raises(VideoCatalogError, match='bundle_changed'):
        prepare(resource_manifests=manifests)
    with pytest.raises(VideoCatalogError, match='latest_digest_required'):
        prepare(worker_image='registry.internal/pi:latest')
