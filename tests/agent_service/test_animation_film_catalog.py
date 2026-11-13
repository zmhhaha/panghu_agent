"""New film role is a separate disabled closure in the ordinary worker build."""
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

from agent_service.build_image import SERVICE, context_archive, ANIMATION_RUNTIME_BUNDLES
from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy.acceptance_probe import candidate_config, render_probe
from agent_service.deploy.animation_film_catalog import (
    measure_animation_film_bundle, prepare_animation_film_catalog,
)
from agent_service.deploy.animation_production_catalog import (
    measure_animation_production_bundle, prepare_animation_production_catalog,
)
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.deploy.candidate_scenario import load_scenario
from agent_service.deploy.video_catalog import VideoCatalogError
from agent_service.profiles import video_animation_production as production
from agent_service.worker_entry import HostConfig, host_policy
from test_animation_film_production_profile import film_document
from test_animation_review_catalog import IMAGE, public_config


def prepare():
    manifests, _ = measure_animation_production_bundle()
    current, _, _ = prepare_animation_production_catalog(public_config(), worker_image=IMAGE,
        resource_manifests=manifests, source_pool_id='existing-pool')
    manifests, measured = measure_animation_film_bundle()
    merged, hosts, receipt = prepare_animation_film_catalog(current, worker_image=IMAGE,
        resource_manifests=manifests, source_pool_id='existing-pool', register_caller=False)
    return current, merged, hosts[production.FILM_PROFILE], receipt, measured


def test_film_preparation_preserves_old_production_and_never_borrows_its_acceptance_or_caller():
    current, merged, host, receipt, measured = prepare()
    for key, old in current.items():
        assert merged[key][:len(old)] == old if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == old
    assert merged['callers'] == current['callers']
    parsed, policy, binding = candidate_config(host)
    assert policy.name == production.FILM_PROFILE and not policy.enabled
    assert binding.acceptance is None
    assert binding.bundle.revision == production.FILM_BUNDLE_REVISION
    assert receipt['combinations'][0]['acceptance_suite'] == 'video.animation-production-contract.v2'
    assert receipt['bundles'] == [dict(bundle_revision=production.FILM_BUNDLE_REVISION,
                                     manifest_sha256=production.FILM_BUNDLE_MANIFEST_SHA256)]
    assert receipt['caller_registered'] is receipt['route_enabled'] is receipt['acceptance_created'] is False
    assert measured['model_requests'] == 0 and measured['container_measured'] is False
    complete = dict(deepcopy(host), execution_catalog={key: deepcopy(merged[key]) for key in CATALOG_KEYS})
    assert project_animation_worker_host(complete) == host
    assert parsed.profile.enabled is False


def test_film_role_does_not_accept_legacy_bundle_or_arbitrary_generation_override():
    manifests, _ = measure_animation_production_bundle()
    with pytest.raises(VideoCatalogError, match='bundle_manifest_mismatch'):
        prepare_animation_film_catalog(public_config(), worker_image=IMAGE,
            resource_manifests=manifests, source_pool_id='existing-pool')
    with pytest.raises(ValueError, match='generation_fixed'):
        prepare_animation_film_catalog(public_config(), film=False)


def test_explicit_film_scenario_is_validated_before_any_job_can_run(tmp_path):
    _, _, host, _, _ = prepare()
    parsed, policy, _ = candidate_config(host)
    source = tmp_path / 'sources'
    source.mkdir()
    context = film_document(1)
    raw = canonical_bytes(context)
    (source / production.INPUT_NAME).write_bytes(raw)
    scenario = dict(schema_version='agent-candidate-scenario.v1', candidate_scope_only=True,
        scenario_id=str(uuid4()), profile=production.FILM_PROFILE, timeout_seconds=600,
        task={'input_sha256': digest(context)},
        inputs=[dict(name=production.INPUT_NAME, sha256=digest(context), bytes=len(raw))],
        business_export=None, parent=None)
    path = tmp_path / 'scenario.json'
    path.write_bytes(canonical_bytes(scenario))
    loaded = load_scenario(path, source, None, policy)
    assert loaded.files == {production.INPUT_NAME: raw}
    assert render_probe(parsed, name='candidate-film-r1', worker_image=IMAGE, node='fixture-node',
        uid_start=303200080, bootstrap_secret='fixture-bootstrap', upstream_secret='fixture-upstream',
        scenario=path, source_dir=source)
    context['film_context']['predecessor']['handoff']['film_sha256'] = 'f' * 64
    # Rebind the outside bytes; malformed inner custody still cannot enter a Job.
    raw = canonical_bytes(context)
    (source / production.INPUT_NAME).write_bytes(raw)
    scenario['inputs'][0].update(sha256=digest(context), bytes=len(raw))
    scenario['task']['input_sha256'] = digest(context)
    path.write_bytes(canonical_bytes(scenario))
    with pytest.raises(ServiceError, match='handoff_binding_mismatch'):
        load_scenario(path, source, None, policy)


def test_normal_worker_context_contains_verified_film_bundle_without_business_imports(tmp_path):
    pins = {revision: sha for revision, sha, _ in ANIMATION_RUNTIME_BUNDLES}
    assert pins[production.FILM_BUNDLE_REVISION] == production.FILM_BUNDLE_MANIFEST_SHA256
    archive = tmp_path / 'context.tar'
    context_archive(SERVICE, 'pi', archive)
    with tarfile.open(archive) as bundle:
        names = set(bundle.getnames())
        assert not any(name.startswith('video_workbench/') for name in names)
        for name in production.BUNDLE_FILES | {'manifest.json'}:
            member = 'agent_service/resources/' + production.FILM_BUNDLE_REVISION + '/' + name
            assert bundle.extractfile(member).read() == (production.FILM_BUNDLE_ROOT / name).read_bytes()
        bundle.extractall(tmp_path / 'isolated', filter='data')
    process = subprocess.run([sys.executable, '-B', '-c',
        'from agent_service.profiles import implementation; '
        'from agent_service.profiles.video_animation_production import verified_bundle; '
        'assert implementation("video.animation-production.v2").required_tools == ("read_inputs","write"); '
        'verified_bundle(film=True)'], cwd=tmp_path / 'isolated',
        env={**os.environ, 'PYTHONPATH': str(tmp_path / 'isolated')}, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr


def test_runtime_measures_only_new_image_owned_resources(monkeypatch, tmp_path):
    from agent_service import resources
    _, _, host, _, _ = prepare()
    config = HostConfig.model_validate(host)
    copied = tmp_path / production.FILM_BUNDLE_REVISION
    copied.mkdir()
    names = {'manifest.json', *production.BUNDLE_FILES}
    for name in names:
        target = copied / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((production.FILM_BUNDLE_ROOT / name).read_bytes())

    class ImageOwnedStat:
        def __init__(self, observed):
            self.observed, self.st_uid, self.st_mode = observed, 0, observed.st_mode & ~0o022

        def __getattr__(self, name):
            return getattr(self.observed, name)

    class ImageOwnedPath(type(Path())):
        def lstat(self, *args, **kwargs):
            observed = super().lstat(*args, **kwargs)
            try:
                self.relative_to(copied)
            except ValueError:
                return observed
            return ImageOwnedStat(observed)

    # Synthetic owner metadata grants no real admission; exact bytes are measured.
    root = ImageOwnedPath(copied)
    monkeypatch.setattr(production, 'FILM_BUNDLE_ROOT', root)
    control = tmp_path / 'control'
    control.mkdir()
    measured = resources.measured_package(config, host_policy(config), control)
    assert measured[0] == root
    assert hashlib.sha256(measured[1].read_bytes()).hexdigest() == measured[2]
    assert {row['path'] for row in json.loads(measured[1].read_bytes())['local_files']} == names
    assert measured[3] == (root / 'production-procedure/SKILL.md',)
