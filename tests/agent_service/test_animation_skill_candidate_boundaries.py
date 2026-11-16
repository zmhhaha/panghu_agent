"""Offline admission boundaries, without model runs or production acceptance."""
from copy import deepcopy
import hashlib

import pytest

from agent_service.animation_skills_contract import bundle_files
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy import candidate_scenario as scenario
from agent_service.deploy.animation_production_catalog import (
    measure_animation_production_bundle, prepare_animation_production_catalog,
)
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.profiles import video_animation_skill_method as method
from agent_service.profiles import video_animation_skill_production as production
from agent_service.worker_entry import CATALOG_KEYS, HostConfig, host_policy
from test_animation_method_profile import review_context
from test_animation_review_catalog import IMAGE
from test_animation_skill_method_catalog import prepare
from test_animation_skill_method_profile import context as method_context
from test_animation_skill_production_profile import context as production_context
from test_candidate_scenario import synthetic_source_root_owner, write_scenario
from test_video_animation_review_profile import PNG


PROFILES = (method.AUTHOR_PROFILE, method.REVIEW_PROFILE, production.PROFILE)


@pytest.fixture(scope='module')
def candidate_hosts():
    merged, hosts, _ = prepare()
    manifests, _ = measure_animation_production_bundle(skills=True)
    merged, production_hosts, _ = prepare_animation_production_catalog(
        merged, worker_image=IMAGE, resource_manifests=manifests,
        source_pool_id='existing-pool', skills=True)
    hosts.update(production_hosts)
    return {name: dict(deepcopy(host), execution_catalog={
        key: deepcopy(merged[key]) for key in CATALOG_KEYS}) for name, host in hosts.items()}


def inputs(tmp_path, profile):
    if profile == production.PROFILE:
        document, packages = production_context()
        files = {row.name: row.content() for row in bundle_files(document['skill_catalog'], packages)}
        files[production.INPUT_NAME] = canonical_bytes(document)
    else:
        document = method_context(tmp_path)
        files = {}
        if profile == method.REVIEW_PROFILE:
            review, _ = review_context()
            document.update(mode='review', observation=review['observation'], comparison=review['comparison'])
            for frame in document['observation']['frames']:
                frame['sha256'] = hashlib.sha256(PNG).hexdigest()
                files[frame['file_name']] = PNG
        files[method.INPUT_NAME] = canonical_bytes(document)
    return {'input_sha256': digest(document)}, files


@pytest.mark.parametrize('profile', PROFILES)
def test_skill_projection_keeps_candidate_disabled_and_exact_pins(candidate_hosts, profile):
    host = deepcopy(candidate_hosts[profile])
    original = host_policy(HostConfig.model_validate(host))
    # Unrelated future contracts are excluded before parsing this image's scope.
    unknown = deepcopy(host['execution_catalog']['task_contracts'][0])
    unknown['name'] = 'future.unavailable-domain.v99'
    host['execution_catalog']['task_contracts'].append(unknown)
    before = deepcopy(host)
    projected = project_animation_worker_host(host)
    actual = host_policy(HostConfig.model_validate(projected))
    assert host == before
    assert all(len(rows) == 1 for rows in projected['execution_catalog'].values())
    assert {key: value for key, value in projected.items() if key != 'execution_catalog'} == {
        key: value for key, value in before.items() if key != 'execution_catalog'}
    assert actual.name == profile and actual.enabled is original.enabled is False
    assert actual.pins() == original.pins()
    assert actual.execution_binding == original.execution_binding
    assert projected['execution_catalog']['execution_bindings'][0].get('acceptance') is None
    assert projected['execution_catalog']['routes'][0]['enabled'] is False


@pytest.mark.parametrize('profile', PROFILES)
def test_skill_candidates_validate_exact_projected_inputs_without_parent_or_export(
        candidate_hosts, tmp_path, profile):
    task, files = inputs(tmp_path, profile)
    path, sources, _ = write_scenario(tmp_path / 'c', profile, task, files)
    policy = host_policy(HostConfig.model_validate(candidate_hosts[profile]))
    loaded = scenario.load_scenario(path, sources, None, policy)
    assert loaded.files == files
    assert not loaded.checkpoint_files and loaded.checkpoint_ref is None
    assert loaded.metadata['production_acceptance_created'] is False


@pytest.mark.parametrize('profile', PROFILES)
def test_skill_candidates_cannot_add_business_export(candidate_hosts, tmp_path, profile):
    task, files = inputs(tmp_path, profile)
    path, sources, _ = write_scenario(tmp_path / 'c', profile, task, files, export=True)
    policy = host_policy(HostConfig.model_validate(candidate_hosts[profile]))
    with pytest.raises(ServiceError, match='candidate_animation_role_business_export_not_supported'):
        scenario.load_scenario(path, sources, None, policy)


def test_production_candidate_rejects_rehashed_but_substituted_skill_source(candidate_hosts, tmp_path):
    task, files = inputs(tmp_path, production.PROFILE)
    name = next(name for name in files if name.endswith('/SKILL.md'))
    files[name] += b'\nSubstituted after the Skill was frozen.'
    # The scenario hashes these new bytes. The inner frozen package must still reject them.
    path, sources, _ = write_scenario(tmp_path / 'c', production.PROFILE, task, files)
    policy = host_policy(HostConfig.model_validate(candidate_hosts[production.PROFILE]))
    with pytest.raises(ServiceError):
        scenario.load_scenario(path, sources, None, policy)


def test_reviewer_candidate_rejects_rehashed_but_substituted_frame(candidate_hosts, tmp_path):
    task, files = inputs(tmp_path, method.REVIEW_PROFILE)
    name = next(name for name in files if name.endswith('.png'))
    files[name] += b'Substituted after the observation was frozen.'
    path, sources, _ = write_scenario(tmp_path / 'c', method.REVIEW_PROFILE, task, files)
    policy = host_policy(HostConfig.model_validate(candidate_hosts[method.REVIEW_PROFILE]))
    with pytest.raises(ServiceError):
        scenario.load_scenario(path, sources, None, policy)
