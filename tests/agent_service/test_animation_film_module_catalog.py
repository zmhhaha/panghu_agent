"""A new module author ships ordinary source, without inheriting admission."""
from copy import deepcopy
import os
import subprocess
import sys
import tarfile

from agent_service.build_image import SERVICE, context_archive
from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_production_catalog import (
    measure_animation_production_bundle, prepare_animation_production_catalog,
)
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.model_proxy import profile_model_call_limit
from agent_service.profiles import video_animation_film_module as P
from agent_service.skill_tools import SKILL_TOOLS_ENVIRONMENT_NAME, TOOL_SCOPE
from test_animation_review_catalog import IMAGE, public_config


def test_new_module_profile_is_a_distinct_disabled_candidate_with_skill_tools():
    before = public_config()
    manifests, measurement = measure_animation_production_bundle(modules=True)
    merged, hosts, preparation = prepare_animation_production_catalog(deepcopy(before),
        worker_image=IMAGE, resource_manifests=manifests, source_pool_id='existing-pool', modules=True)
    for key, rows in before.items():
        assert merged[key][:len(rows)] == rows if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == rows
    host, policy, binding = candidate_config(hosts[P.PROFILE])
    assert host.profile.enabled is policy.enabled is False and binding.acceptance is None
    environment = hosts[P.PROFILE]['execution_catalog']['tool_environments'][0]
    assert environment['name'] == SKILL_TOOLS_ENVIRONMENT_NAME and set(environment['tools']) == TOOL_SCOPE
    assert preparation['combinations'][0]['acceptance_suite'] == 'video.animation-film-module-contract.v1'
    assert preparation['model_requests'] == 0 and not measurement['container_measured']
    assert not preparation['acceptance_created'] and not preparation['uid_reserved']
    assert not merged['routes'][-1]['enabled'] and not merged['kubernetes_workers'][-1]['enabled']
    complete = dict(hosts[P.PROFILE], execution_catalog={key: deepcopy(merged[key]) for key in CATALOG_KEYS})
    assert project_animation_worker_host(complete) == hosts[P.PROFILE]
    assert profile_model_call_limit(P.PROFILE, 128) == 8
    assert profile_model_call_limit(P.PROFILE, 3) == 3


def test_clean_standard_worker_context_imports_new_profile_and_exact_bundle(tmp_path):
    archive = tmp_path / 'context.tar'
    context_archive(SERVICE, 'pi', archive)
    isolated = tmp_path / 'isolated'
    with tarfile.open(archive) as bundle:
        assert not any(name.startswith('video_workbench/') for name in bundle.getnames())
        for name in P.BUNDLE_FILES | {'manifest.json'}:
            path = f'agent_service/resources/{P.BUNDLE_REVISION}/{name}'
            assert bundle.extractfile(path).read() == (P.BUNDLE_ROOT / name).read_bytes()
        bundle.extractall(isolated, filter='data')
    process = subprocess.run([sys.executable, '-B', '-c',
        'from agent_service.profiles import implementation; '
        'from agent_service.profiles.video_animation_film_module import verified_bundle; '
        'assert implementation("video.animation-film-module.v1").output_schema == "video-animation-film-module-output.v1"; '
        'verified_bundle()'], cwd=isolated,
        env={**os.environ, 'PYTHONPATH': str(isolated)}, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr.decode(errors='replace')
