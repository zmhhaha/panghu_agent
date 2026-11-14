"""New Manim recipe authority stays separate, disabled and source-only."""
from copy import deepcopy

import pytest

from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_manim_catalog import measure_manim_recipe_bundle, prepare_manim_recipe_catalog, PROFILES
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.model_proxy import profile_model_call_limit
from agent_service.profiles.video_animation_manim_recipe import PROFILE
from agent_service.deploy.animation_manim_prepare import prepare_candidate
from agent_service.profiles.video_animation_manim_contract import validate_input
import json
import hashlib
from test_animation_review_catalog import public_config, IMAGE


def prepare(current=None, **changes):
    manifests, _ = measure_manim_recipe_bundle()
    options = dict(worker_image=IMAGE, resource_manifests=manifests, source_pool_id='existing-pool')
    options.update(changes)
    return prepare_manim_recipe_catalog(public_config() if current is None else current, **options)


def test_source_only_manim_preparation_preserves_every_existing_route_and_credential():
    current = public_config()
    before = deepcopy(current)
    merged, hosts, receipt = prepare(current)
    assert current == before
    assert set(hosts) == set(PROFILES)
    for key, old in before.items():
        assert merged[key][:len(old)] == old if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == old
    host = hosts[PROFILE]
    parsed, policy, binding = candidate_config(host)
    assert parsed.engine == 'pi' and parsed.profile.enabled is False
    assert binding.acceptance is None and policy.enabled is False
    assert set(host['execution_catalog']['task_contracts'][0]['required_tools']) == {'read_inputs', 'skill_search', 'skill_read', 'write'}
    assert receipt['combination_sha256'] == policy.execution_binding.combination_sha256
    assert receipt['max_model_calls_per_attempt'] == 8
    assert receipt['route_enabled'] is receipt['pool_enabled'] is receipt['uid_reserved'] is False
    assert merged['callers'][-1]['profiles'] == [PROFILE]
    worker = merged['kubernetes_workers'][-1]
    assert worker['uid_start'] is None and worker['enabled'] is False
    complete = dict(host, execution_catalog={key: merged[key] for key in CATALOG_KEYS})
    assert project_animation_worker_host(complete) == host
    assert registry(merged).profiles().policy('workspace-task.v1', 'v1').enabled is True


@pytest.mark.parametrize('changes', [dict(worker_image='registry/manim:latest'), dict(resource_manifests={}),
    dict(caller=dict(tenant='internal', caller='existing-service', token_ref='existing-service', profiles=[PROFILE])),
    dict(caller=dict(tenant='internal', caller='new-only', token_ref='existing-service', profiles=[PROFILE])),
    dict(caller=dict(tenant='internal', caller='new-only', token_ref='new-only', profiles=[PROFILE, 'video.animation-film-module.v1']))])
def test_invalid_pins_or_borrowed_authority_are_rejected_before_any_live_action(changes):
    with pytest.raises(VideoCatalogError): prepare(**changes)


def test_manim_recipe_has_hard_eight_model_calls_and_stricter_operator_limit_wins():
    assert profile_model_call_limit(PROFILE, 40) == 8
    assert profile_model_call_limit(PROFILE, 4) == 4
    assert profile_model_call_limit('workspace-task.v1', 40) == 40


def test_prepare_without_caller_changes_no_credentials_and_measures_only_local_bundle():
    merged, _, receipt = prepare(register_caller=False)
    assert merged['callers'] == public_config()['callers']
    manifests, measurement = measure_manim_recipe_bundle()
    assert receipt['model_requests'] == 0 and not receipt['secrets_created']
    assert measurement['local_files_verified'] and not measurement['container_measured']
    assert measurement['model_requests'] == measurement['agent_tasks'] == 0


def test_candidate_operator_freezes_original_source_only_request_and_disabled_manifest(tmp_path):
    current = public_config()
    before = deepcopy(current)
    output = tmp_path/'candidate'
    plan = prepare_candidate(current, output=output, worker_image=IMAGE, source_pool_id='existing-pool',
        source_revision='a'*40, candidate_name='candidate-manim-test', candidate_uid_start=450000000)
    assert current == before and not plan['uid_reserved'] and not plan['activation_permitted']
    assert plan['model_requests'] == 0 and plan['source_only'] is True
    frozen = json.loads((output/'frozen-files.json').read_bytes())
    for name, checksum in frozen.items():
        assert hashlib.sha256((output/name).read_bytes()).hexdigest() == checksum
    document = validate_input(json.loads((output/'candidate-sources/animation-manim-recipe-input.json').read_bytes()))
    assert document['resource_constraints']['frame_count'] == 48
    assert 'skill_read' in document['guidance']
    assert 'source' not in document and not (output/'scene.py').exists()
    assert not json.loads((output/'public-disabled.json').read_bytes())['routes'][-1]['enabled']
    assert json.loads((output/'budget.json').read_bytes())['hard_model_requests_per_attempt'] == 8
    with pytest.raises(ValueError, match='new_directory'):
        prepare_candidate(current, output=output, worker_image=IMAGE, source_pool_id='existing-pool',
            source_revision='a'*40, candidate_name='candidate-manim-test', candidate_uid_start=450000000)
