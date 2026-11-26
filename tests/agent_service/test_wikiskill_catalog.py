"""Pure disabled catalog preparation is neither model evidence nor release."""
from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys
import tarfile

import pytest

from agent_service.catalog import CATALOG_KEYS
from agent_service.compilation_input_tools import (COMPILATION_INPUT_ENVIRONMENT_NAME,
    COMPILATION_INPUT_EXTENSION_SHA256, compilation_input_environment_revision)
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.deploy.wikiskill_catalog import PROFILES, role, measure_wikiskill_bundle, prepare_wikiskill_catalog
from agent_service.profiles import implementation
from test_animation_review_catalog import IMAGE, public_config


def prepare(profile, current=None, **changes):
    manifests, _ = measure_wikiskill_bundle(profile)
    options = dict(profile=profile, worker_image=IMAGE, source_pool_id='existing-pool', resource_manifests=manifests)
    options.update(changes)
    return prepare_wikiskill_catalog(public_config() if current is None else current, **options)


@pytest.mark.parametrize('profile', PROFILES)
def test_preparation_is_additive_disabled_and_preserves_callers_and_old_authority(profile):
    current = public_config()
    before = deepcopy(current)
    merged, hosts, receipt = prepare(profile, current)
    assert current == before and set(hosts) == {profile}
    for key, old in before.items():
        assert merged[key][:len(old)] == old if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == old
    assert merged['callers'] == before['callers']
    parsed, policy, binding = candidate_config(hosts[profile])
    assert not parsed.profile.enabled and not policy.enabled and binding.acceptance is None
    assert binding.profile == profile and binding.engine == 'pi'
    assert policy.execution_binding.combination_sha256 == receipt['combination_sha256']
    assert receipt['route_enabled'] is receipt['pool_enabled'] is receipt['acceptance_created'] is False
    assert receipt['uid_reserved'] is receipt['credentials_created'] is False and receipt['model_requests'] == 0
    pool = merged['kubernetes_workers'][-1]
    assert pool['enabled'] is False and pool['uid_start'] is None and pool['image'] == IMAGE
    for key in ('namespace', 'node_selector', 'pod_cidrs', 'read_only_mounts', 'mount_apparmor_profile', 'token_ref', 'receipt_key_ref'):
        assert pool[key] == before['kubernetes_workers'][0][key]
    assert registry(merged).profiles().policy('workspace-task.v1', 'v1').enabled
    contract = registry(merged).contract(profile, '1')
    assert set(contract.required_tools) == set(implementation(profile).required_tools)


@pytest.mark.parametrize('profile', PROFILES)
def test_measured_resources_and_host_are_bound_to_exact_current_bytes(profile):
    module = role(profile)
    manifests, receipt = measure_wikiskill_bundle(profile)
    assert set(manifests) == {module.BUNDLE_REVISION}
    assert receipt['manifest_sha256'] == hashlib.sha256((module.BUNDLE_ROOT / 'manifest.json').read_bytes()).hexdigest()
    assert receipt['local_files_verified'] is True and receipt['container_measured'] is False
    assert receipt['model_requests'] == 0
    expected = {name: hashlib.sha256((module.BUNDLE_ROOT / name).read_bytes()).hexdigest() for name in module.BUNDLE_FILES}
    assert {row['path']: row['sha256'] for row in receipt['files']} == expected
    merged, hosts, _ = prepare(profile)
    host = hosts[profile]
    bundle = host['execution_catalog']['capability_bundles'][0]
    assert {row['name']: row['sha256'] for row in bundle['resources']} == {
        'manifest.json': module.BUNDLE_MANIFEST_SHA256, **expected}
    assert host['profile']['image_digest'] == IMAGE.rsplit('@', 1)[1]
    assert host['model']['api'] == 'openai-responses'


def test_v2_appends_new_identity_without_replacing_v1_or_planner():
    assert PROFILES[:3] == ('video.animation-wikiskill-compile.v1', 'video.joint-plan.v2',
                        'video.animation-wikiskill-compile.v2')
    first, old_hosts, old_receipt = prepare(PROFILES[0])
    previous, planner_hosts, _ = prepare(PROFILES[1], first)
    frozen = deepcopy(previous)
    merged, hosts, receipt = prepare(PROFILES[2], previous)
    assert previous == frozen
    for key in (*CATALOG_KEYS, 'kubernetes_workers', 'callers'):
        assert merged[key][:len(frozen[key])] == frozen[key]
    assert role(PROFILES[0]).BUNDLE_REVISION == 'animation-wikiskill-v1'
    assert role(PROFILES[2]).BUNDLE_REVISION == 'animation-wikiskill-v2'
    assert old_receipt['binding_id'] == 'wikiskill-compile-pi'
    assert receipt['binding_id'] == 'wikiskill-compile-v2-pi'
    assert receipt['acceptance_suite'] == 'video.animation-wikiskill-compile-contract.v2'
    assert old_hosts[PROFILES[0]]['execution_catalog']['task_contracts'][0]['required_tools'] == ['read', 'write']
    assert set(planner_hosts[PROFILES[1]]['execution_catalog']['task_contracts'][0]['required_tools']) == {
        'read_inputs', 'skill_search', 'skill_read', 'write'}
    assert hosts[PROFILES[2]]['execution_catalog']['task_contracts'][0]['required_tools'] == [
        'read_compilation_input', 'write']


def test_v2_environment_binds_extension_and_full_image_runtime_identity():
    _, hosts, _ = prepare(PROFILES[2])
    host = hosts[PROFILES[2]]
    environment = host['execution_catalog']['tool_environments'][0]
    assert environment['name'] == COMPILATION_INPUT_ENVIRONMENT_NAME
    assert environment['revision'] == compilation_input_environment_revision(environment)
    assert COMPILATION_INPUT_EXTENSION_SHA256 in environment['revision']
    assert host['execution_catalog']['task_contracts'][0]['max_timeout_seconds'] == 900
    for changes in ({'runtime_revision': 'changed-runtime'},
                    {'worker_image': 'registry.internal/worker:latest@sha256:' + 'd' * 64}):
        _, changed, _ = prepare(PROFILES[2], **changes)
        observed = changed[PROFILES[2]]['execution_catalog']['tool_environments'][0]
        assert observed['revision'] != environment['revision']
        assert COMPILATION_INPUT_EXTENSION_SHA256 in observed['revision']


def test_capacity_contract_is_additive_and_preserves_historical_v4_authority():
    from agent_service.compilation_input_tools import (COMPILATION_CAPACITY_ENVIRONMENT_NAME,
        COMPILATION_CHECKED_ENVIRONMENT_NAME)
    profile = 'video.animation-wikiskill-compile.v4'
    previous, old_hosts, _ = prepare(profile)
    frozen = deepcopy(previous)
    merged, hosts, receipt = prepare(profile, previous, profile_revision='2')
    assert previous == frozen
    for section in (*CATALOG_KEYS, 'kubernetes_workers', 'callers'):
        assert merged[section][:len(frozen[section])] == frozen[section]
    admitted = registry(merged)
    assert admitted.contract(profile, '1').max_input_total_bytes == 32 * 1024 * 1024
    assert admitted.contract(profile, '2').max_input_total_bytes == 64 * 1024 * 1024
    assert old_hosts[profile]['execution_catalog']['tool_environments'][0]['name'] == COMPILATION_CHECKED_ENVIRONMENT_NAME
    assert hosts[profile]['execution_catalog']['tool_environments'][0]['name'] == COMPILATION_CAPACITY_ENVIRONMENT_NAME
    config, policy, binding = candidate_config(hosts[profile])
    assert config.profile.revision == policy.revision == binding.profile_revision == '2'
    assert policy.max_input_total_bytes == 64 * 1024 * 1024
    assert not policy.enabled and binding.acceptance is None
    assert receipt['profile_revision'] == '2' and receipt['acceptance_created'] is False


def test_hour_budget_is_additive_and_reuses_exact_capacity_tools_and_bundle():
    from agent_service.compilation_input_tools import COMPILATION_CAPACITY_ENVIRONMENT_NAME
    profile = 'video.animation-wikiskill-compile.v4'
    original, _, _ = prepare(profile)
    capacity, old_hosts, previous_receipt = prepare(profile, original, profile_revision='2')
    frozen = deepcopy(capacity)
    extended, hosts, receipt = prepare(profile, capacity, profile_revision='3')
    assert capacity == frozen
    for section in (*CATALOG_KEYS, 'kubernetes_workers', 'callers'):
        assert extended[section][:len(frozen[section])] == frozen[section]
    admitted = registry(extended)
    assert admitted.contract(profile, '1').max_timeout_seconds == 900
    assert admitted.contract(profile, '2').max_timeout_seconds == 900
    assert admitted.contract(profile, '3').max_timeout_seconds == 3600
    previous, current = old_hosts[profile]['execution_catalog'], hosts[profile]['execution_catalog']
    assert current['capability_bundles'] == previous['capability_bundles']
    assert current['tool_environments'] == previous['tool_environments']
    assert current['tool_environments'][0]['name'] == COMPILATION_CAPACITY_ENVIRONMENT_NAME
    config, policy, binding = candidate_config(hosts[profile])
    assert config.profile.revision == policy.revision == binding.profile_revision == '3'
    assert policy.max_timeout_seconds == 3600 and policy.max_input_total_bytes == 64 * 1024 * 1024
    assert not policy.enabled and binding.acceptance is None
    assert receipt['profile_revision'] == '3' and receipt['acceptance_created'] is False
    assert receipt['combination_sha256'] != previous_receipt['combination_sha256']


@pytest.mark.parametrize('native_max', [900, 3599, 3601])
def test_registry_cannot_reinterpret_revision_three_as_another_native_budget(native_max):
    from agent_service.registry import TaskContract
    profile = 'video.animation-wikiskill-compile.v4'
    _, hosts, _ = prepare(profile, profile_revision='3')
    changed = deepcopy(hosts[profile]['execution_catalog']['task_contracts'][0])
    changed['max_timeout_seconds'] = native_max
    with pytest.raises(ValueError):
        TaskContract.model_validate(changed)


def test_v2_preparation_rejects_changed_fixed_input_reader(tmp_path, monkeypatch):
    from agent_service import compilation_input_tools
    changed = tmp_path / 'read_compilation_input_extension.mjs'
    changed.write_bytes(b'changed reader')
    monkeypatch.setattr(compilation_input_tools, 'COMPILATION_INPUT_EXTENSION', changed)
    with pytest.raises(ValueError, match='compilation_input_extension_integrity'):
        prepare(PROFILES[2])


@pytest.mark.parametrize('changes,code', [
    ({'worker_image': 'registry.internal/worker:latest'}, 'latest_digest_required'),
    ({'worker_image': 'registry.internal/worker:v1@sha256:' + 'c' * 64}, 'latest_digest_required'),
    ({'resource_manifests': {}}, 'bundle_manifest_mismatch'),
    ({'source_pool_id': 'absent'}, 'source_pi_pool_required'),
    ({'model_policy': {}}, 'model_policy_required'),
])
def test_invalid_pins_do_not_change_existing_authority(changes, code):
    current = public_config()
    before = deepcopy(current)
    with pytest.raises(VideoCatalogError, match=code):
        prepare(PROFILES[0], current, **changes)
    assert current == before


def test_changed_source_bytes_fail_local_measurement_before_host_preparation(tmp_path, monkeypatch):
    module = role(PROFILES[0])
    for name in {*module.BUNDLE_FILES, 'manifest.json'}:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((module.BUNDLE_ROOT / name).read_bytes())
    (tmp_path / 'CONTRACT.md').write_bytes(b'changed local instructions')
    monkeypatch.setattr(module, 'BUNDLE_ROOT', tmp_path)
    from agent_service.contracts import ServiceError
    with pytest.raises(ServiceError, match='bundle_integrity'):
        measure_wikiskill_bundle(PROFILES[0])


def test_standard_worker_context_includes_exact_bundles_and_needs_no_business_import(tmp_path):
    from agent_service.build_image import SERVICE, context_archive
    archive = tmp_path / 'worker-context.tar'
    context_archive(SERVICE, 'pi', archive)
    with tarfile.open(archive) as bundle:
        names = set(bundle.getnames())
        assert 'agent_service/animation_wikiskill_contract.py' in names
        assert 'agent_service/profiles/video_animation_wikiskill.py' in names
        assert 'agent_service/profiles/video_animation_wikiskill_v2.py' in names
        assert 'agent_service/profiles/video_animation_wikiskill_v3.py' in names
        assert 'agent_service/profiles/video_animation_wikiskill_v4.py' in names
        assert 'agent_service/checked_compilation_extension.mjs' in names
        assert 'agent_service/compilation_output_preflight.mjs' in names
        assert 'agent_service/animation_wikiskill_compact.py' in names
        assert 'agent_service/compilation_input_tools.py' in names
        assert 'agent_service/read_compilation_input_extension.mjs' in names
        assert 'agent_service/read_compilation_evidence_extension.mjs' in names
        assert 'agent_service/profiles/video_joint_plan_skills.py' in names
        assert not any(name.startswith('video_workbench/') for name in names)
        for profile in PROFILES:
            module = role(profile)
            for name in {*module.BUNDLE_FILES, 'manifest.json'}:
                path = 'agent_service/resources/' + module.BUNDLE_REVISION + '/' + name
                assert bundle.extractfile(path).read() == (module.BUNDLE_ROOT / name).read_bytes()
        bundle.extractall(tmp_path / 'isolated', filter='data')
    process = subprocess.run([sys.executable, '-B', '-c',
        'from agent_service.profiles import implementation; '
        'from agent_service.profiles.video_animation_wikiskill import verified_bundle; '
        'from agent_service.profiles.video_animation_wikiskill_v2 import verified_bundle as compiler_v2_bundle; '
        'from agent_service.profiles.video_animation_wikiskill_v3 import verified_bundle as compiler_v3_bundle; '
        'from agent_service.profiles.video_animation_wikiskill_v4 import verified_bundle as compiler_v4_bundle; '
        'from agent_service.compilation_input_tools import verified_compilation_input_extension; '
        'from agent_service.profiles.video_joint_plan_skills import verified_bundle as planner_bundle; '
        'assert implementation("video.animation-wikiskill-compile.v1").required_tools == ("read","write"); '
        'assert implementation("video.animation-wikiskill-compile.v2").required_tools == ("read_compilation_input","write"); '
        'assert implementation("video.animation-wikiskill-compile.v3").required_tools == ("read_compilation_input","write"); '
        'assert "skill_read" in implementation("video.joint-plan.v2").required_tools; '
        'verified_bundle(); compiler_v2_bundle(); compiler_v3_bundle(); compiler_v4_bundle(); '
        'verified_compilation_input_extension(); '
        'verified_compilation_input_extension("tools-pi-compilation-evidence"); '
        'verified_compilation_input_extension("tools-pi-compilation-checked"); planner_bundle()'], cwd=tmp_path / 'isolated',
        env={**os.environ, 'PYTHONPATH': str(tmp_path / 'isolated')}, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
