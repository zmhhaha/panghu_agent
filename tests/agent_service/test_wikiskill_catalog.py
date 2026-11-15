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
        'from agent_service.profiles.video_joint_plan_skills import verified_bundle as planner_bundle; '
        'assert implementation("video.animation-wikiskill-compile.v1").required_tools == ("read","write"); '
        'assert "skill_read" in implementation("video.joint-plan.v2").required_tools; '
        'verified_bundle(); planner_bundle()'], cwd=tmp_path / 'isolated',
        env={**os.environ, 'PYTHONPATH': str(tmp_path / 'isolated')}, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
