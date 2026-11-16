"""Production preparation remains disabled and owns a fresh combination."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from uuid import uuid4

import pytest

from agent_service.build_image import SERVICE, context_archive
from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.joint_plan_catalog import (
    PROFILES, measure_joint_plan_bundle, prepare_joint_plan_catalog,
)
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.profiles import video_joint_plan as contract
from test_animation_review_catalog import IMAGE, public_config


def prepare(current=None, **changes):
    manifests, _ = measure_joint_plan_bundle()
    options = dict(worker_image=IMAGE, resource_manifests=manifests, source_pool_id='existing-pool')
    options.update(changes)
    return prepare_joint_plan_catalog(public_config() if current is None else current, **options)


def test_preparation_preserves_all_old_authority_and_never_borrows_acceptance():
    current = public_config()
    before = deepcopy(current)
    merged, hosts, receipt = prepare(current)
    assert current == before and set(hosts) == set(PROFILES)
    for key, old in before.items():
        assert merged[key][:len(old)] == old if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == old
    host = hosts[contract.PROFILE]
    parsed, policy, binding = candidate_config(host)
    assert not parsed.profile.enabled and not policy.enabled and binding.acceptance is None
    assert binding.profile == contract.PROFILE
    assert receipt['combinations'][0]['combination_sha256'] == policy.execution_binding.combination_sha256
    assert receipt['acceptance_created'] is receipt['uid_reserved'] is receipt['production_task_exercised'] is False
    pool = merged['kubernetes_workers'][-1]
    assert pool['enabled'] is False and pool['uid_start'] is None
    for key in ('namespace', 'node_selector', 'pod_cidrs', 'read_only_mounts', 'mount_apparmor_profile', 'token_ref', 'receipt_key_ref'):
        assert pool[key] == before['kubernetes_workers'][0][key]
    assert merged['callers'][-1]['profiles'] == [contract.PROFILE]
    complete = dict(deepcopy(host), execution_catalog={key:deepcopy(merged[key]) for key in CATALOG_KEYS})
    assert project_animation_worker_host(complete) == host
    assert registry(merged).profiles().policy('workspace-task.v1', 'v1').enabled is True


def test_local_bundle_measurement_is_exact_without_model_or_container_claims():
    manifests, receipt = measure_joint_plan_bundle()
    assert set(manifests) == {contract.BUNDLE_REVISION}
    assert receipt['manifest_sha256'] == hashlib.sha256((contract.BUNDLE_ROOT / 'manifest.json').read_bytes()).hexdigest()
    assert receipt['model_requests'] == receipt['agent_tasks'] == 0
    assert receipt['container_measured'] is receipt['routes_changed'] is receipt['secrets_created'] is False


@pytest.mark.parametrize('changes,match', [
    ({'worker_image':'registry.internal/worker:latest'}, 'latest_digest_required'),
    ({'worker_image':'registry.internal/worker:v1@sha256:'+'c'*64}, 'latest_digest_required'),
    ({'model_policy':{}}, 'model_policy_required'),
    ({'source_pool_id':'missing'}, 'source_pi_pool_required'),
    ({'resource_manifests':{}}, 'bundle_manifest_mismatch'),
    ({'register_caller':'yes'}, 'caller_registration_invalid'),
])
def test_invalid_candidate_parameters_do_not_create_new_authority(changes, match):
    with pytest.raises(VideoCatalogError, match=match): prepare(**changes)


def test_dedicated_production_caller_cannot_borrow_old_credentials_or_extend_scope():
    caller = dict(tenant='internal', caller='video-workbench', profiles=list(PROFILES), token_ref='new-token')
    with pytest.raises(VideoCatalogError, match='dedicated_joint_plan_caller_required'): prepare(caller=caller)
    caller['caller'] = 'video-joint-plan'; caller['token_ref'] = 'existing-service'
    with pytest.raises(VideoCatalogError, match='dedicated_joint_plan_caller_required'): prepare(caller=caller)
    current = public_config(); current['callers'][0]['token_ref'] = 'video-joint-plan'
    with pytest.raises(VideoCatalogError, match='dedicated_joint_plan_token_required'): prepare(current)
    merged, _, _ = prepare(register_caller=False)
    assert merged['callers'] == public_config()['callers']
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        prepare(merged, worker_image=IMAGE.replace('c'*64, 'd'*64))


def test_standard_image_context_carries_new_profile_and_bundle_without_business_source(tmp_path):
    archive = tmp_path / 'context.tar'
    context_archive(SERVICE, 'pi', archive)
    with tarfile.open(archive) as bundle:
        names = set(bundle.getnames())
        assert 'agent_service/profiles/video_joint_plan.py' in names
        assert 'agent_service/profiles/video_joint_plan_contract.py' in names
        assert not any(name.startswith('video_workbench/') for name in names)
        for name in contract.BUNDLE_FILES | {'manifest.json'}:
            path = 'agent_service/resources/' + contract.BUNDLE_REVISION + '/' + name
            assert bundle.extractfile(path).read() == (contract.BUNDLE_ROOT / name).read_bytes()
        bundle.extractall(tmp_path / 'isolated', filter='data')
    # Running from only the ordinary worker context catches accidental business
    # imports that pass in the full repository but fail in the actual image.
    process = subprocess.run([sys.executable, '-B', '-c',
        'from agent_service.profiles import implementation; '
        'from agent_service.profiles.video_joint_plan import verified_bundle; '
        'assert implementation("video.joint-plan.v1").required_tools == ("read_inputs","write"); '
        'verified_bundle()'], cwd=tmp_path / 'isolated',
        env={**os.environ, 'PYTHONPATH': str(tmp_path / 'isolated')}, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr


def test_explicit_joint_plan_scenario_is_checked_by_new_profile_without_running_or_enabling(tmp_path):
    from agent_service.contracts import canonical_bytes, digest, ServiceError
    from agent_service.deploy.acceptance_probe import render_probe
    from agent_service.deploy.candidate_scenario import load_scenario
    from test_joint_plan_profile import document
    _, hosts, _ = prepare()
    host = hosts[contract.PROFILE]
    parsed, policy, _ = candidate_config(host)
    sources = tmp_path / 'sources'; sources.mkdir()
    raw = canonical_bytes(document())
    (sources / contract.INPUT_NAME).write_bytes(raw)
    scenario = dict(schema_version='agent-candidate-scenario.v1', candidate_scope_only=True,
        scenario_id=str(uuid4()), profile=contract.PROFILE, timeout_seconds=600,
        task={'input_sha256': hashlib.sha256(raw).hexdigest()},
        inputs=[dict(name=contract.INPUT_NAME, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))],
        business_export=None, parent=None)
    path = tmp_path / 'scenario.json'; path.write_bytes(canonical_bytes(scenario))
    prepared = load_scenario(path, sources, None, policy)
    assert prepared.files == {contract.INPUT_NAME: raw}
    rendered = render_probe(parsed, name='candidate-joint-plan-r1', worker_image=IMAGE, node='fixture-node',
        uid_start=303200080, bootstrap_secret='fixture-bootstrap', upstream_secret='fixture-upstream',
        scenario=path, source_dir=sources)
    assert rendered and host['profile']['enabled'] is False
    changed = json.loads(raw); changed['allowed_engines'] = ['unknown-renderer']
    broken = canonical_bytes(changed); (sources / contract.INPUT_NAME).write_bytes(broken)
    scenario['inputs'][0].update(sha256=hashlib.sha256(broken).hexdigest(), bytes=len(broken))
    scenario['task']['input_sha256'] = digest(changed); path.write_bytes(canonical_bytes(scenario))
    with pytest.raises(ServiceError, match='invalid_explanation_contract'):
        load_scenario(path, sources, None, policy)


def test_runtime_resource_measurement_uses_new_bundle_not_legacy_acceptance(monkeypatch, tmp_path):
    from agent_service import resources
    from agent_service.worker_entry import HostConfig, host_policy
    _, hosts, _ = prepare()
    config = HostConfig.model_validate(hosts[contract.PROFILE])
    copied = tmp_path / contract.BUNDLE_REVISION; copied.mkdir()
    names = {'manifest.json', *contract.BUNDLE_FILES}
    for name in names:
        target = copied / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((contract.BUNDLE_ROOT / name).read_bytes())

    class ImageOwnedStat:
        def __init__(self, observed):
            self.observed, self.st_uid, self.st_mode = observed, 0, observed.st_mode & ~0o022

        def __getattr__(self, name):
            return getattr(self.observed, name)

    class ImageOwnedPath(type(Path())):
        def lstat(self, *args, **kwargs):
            observed = super().lstat(*args, **kwargs)
            try: self.relative_to(copied)
            except ValueError: return observed
            return ImageOwnedStat(observed)

    # Synthetic ownership is only an offline stand-in; resource bytes and
    # runtime verification are real. It creates no readiness or acceptance.
    root = ImageOwnedPath(copied); monkeypatch.setattr(contract, 'BUNDLE_ROOT', root)
    control = tmp_path / 'control'; control.mkdir()
    measured = resources.measured_package(config, host_policy(config), control)
    assert measured[0] == root
    assert hashlib.sha256(measured[1].read_bytes()).hexdigest() == measured[2]
    assert {row['path'] for row in json.loads(measured[1].read_bytes())['local_files']} == names
    assert measured[3] == (root / 'joint-plan-procedure/SKILL.md',)
