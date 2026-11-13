"""A prepared Skill author is disabled until its exact native admission passes."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from agent_service.build_image import SERVICE, context_archive
from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_production_catalog import (
    measure_animation_production_bundle, prepare_animation_production_catalog,
)
from agent_service.deploy.video_catalog import VideoCatalogError
from agent_service.profiles import video_animation_skill_production as contract
from agent_service.skill_tools import SKILL_TOOLS_ENVIRONMENT_NAME, TOOL_SCOPE
from test_animation_review_catalog import IMAGE, public_config


def test_skill_author_preparation_cannot_inherit_legacy_acceptance_or_tools():
    current = public_config()
    before = deepcopy(current)
    manifests, receipt = measure_animation_production_bundle(skills=True)
    assert set(manifests) == {contract.BUNDLE_REVISION} and not receipt['container_measured']
    merged, hosts, preparation = prepare_animation_production_catalog(current,
        worker_image=IMAGE, resource_manifests=manifests, source_pool_id='existing-pool', skills=True)
    assert current == before
    for key, rows in before.items():
        assert merged[key][:len(rows)] == rows if key in {*CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == rows
    host, policy, binding = candidate_config(hosts[contract.PROFILE])
    assert host.profile.enabled is policy.enabled is False
    assert binding.acceptance is None
    environment = hosts[contract.PROFILE]['execution_catalog']['tool_environments'][0]
    assert environment['name'] == SKILL_TOOLS_ENVIRONMENT_NAME and set(environment['tools']) == TOOL_SCOPE
    assert preparation['combinations'][0]['acceptance_suite'] == 'video.animation-production-contract.v3'
    assert preparation['model_requests'] == 0 and not preparation['acceptance_created']
    assert merged['routes'][-1]['enabled'] is merged['kubernetes_workers'][-1]['enabled'] is False


def test_skill_profile_and_both_fixed_extensions_survive_a_clean_worker_build(tmp_path):
    archive = tmp_path / 'context.tar'
    context_archive(SERVICE, 'pi', archive)
    with tarfile.open(archive) as bundle:
        names = set(bundle.getnames())
        for filename in ('skill_tool_evidence.py', 'animation_skills_contract.py', 'skill_tools.py',
                         'read_skill_task_extension.mjs', 'skill_tools_extension.mjs'):
            assert 'agent_service/' + filename in names
        assert not any(name.startswith('video_workbench/') for name in names)
        for filename in contract.BUNDLE_FILES | {'manifest.json'}:
            path = 'agent_service/resources/' + contract.BUNDLE_REVISION + '/' + filename
            assert bundle.extractfile(path).read() == (contract.BUNDLE_ROOT / filename).read_bytes()
        bundle.extractall(tmp_path / 'worker', filter='data')
    command = ('from agent_service.profiles import implementation; '
               'from agent_service.profiles.video_animation_skill_production import verified_bundle; '
               'from agent_service.skill_tools import verified_skill_tools_extensions; '
               'assert implementation("video.animation-production.v3").required_tools == '
               '("read_inputs","skill_search","skill_read","write"); '
               'verified_bundle(); verified_skill_tools_extensions()')
    result = subprocess.run([sys.executable, '-B', '-c', command], cwd=tmp_path / 'worker',
        env={**os.environ, 'PYTHONPATH': str(tmp_path / 'worker')},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_film_and_skill_generation_are_distinct_frozen_roles():
    with pytest.raises(VideoCatalogError, match='generation_invalid'):
        measure_animation_production_bundle(film=True, skills=True)
