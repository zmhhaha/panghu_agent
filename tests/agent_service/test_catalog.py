"""Offline catalog composition with synthetic image packages; no native/model I/O."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_service import catalog
from agent_service.config import _Public
from agent_service.profiles import implementation
from agent_service.registry import DeploymentRegistry
from agent_service.worker_entry import HostConfig, host_policy


PI = 'registry.invalid/agent-pi@sha256:' + 'a' * 64
HERMES = 'registry.invalid/agent-hermes@sha256:' + 'b' * 64


class RootImageOS:
    # This only opens the offline generator's Linux/root gate in a fixture.
    # Package reads below are replaced by explicit synthetic image files;
    # global os.name and pathlib behavior remain those of the real test host.
    name = 'posix'
    @staticmethod
    def geteuid():
        return 0
    def __getattr__(self, key):
        return getattr(os, key)


@pytest.fixture
def image_packages(tmp_path, monkeypatch):
    ppt = tmp_path / 'ppt-master'
    skill = ppt / 'skills/ppt-master'
    references = skill / 'references'
    references.mkdir(parents=True)
    (ppt / 'LICENSE').write_text('MIT License\nSynthetic test package only.\n')
    (skill / 'LICENSE').write_text('MIT License\n')
    (skill / 'SKILL.md').write_text('Synthetic PPT procedure, never executed.\n')
    (references / 'layout.md').write_text('Synthetic layout reference.\n')
    commit = tmp_path / 'ppt-master.commit'
    commit.write_text(catalog.PPT_COMMIT + '\n')
    monkeypatch.setattr(catalog, 'PPT_ROOT', ppt)
    monkeypatch.setattr(catalog, 'PPT_BUILD_COMMIT', commit)
    monkeypatch.setattr(catalog, 'os', RootImageOS())
    monkeypatch.setattr(catalog, '_directory', lambda path, **kwargs: Path(path).absolute())
    monkeypatch.setattr(catalog, '_read', lambda path, limit=catalog.MAX_FILE_BYTES: Path(path).read_bytes())
    return SimpleNamespace(ppt=ppt, skill=skill, references=references, commit=commit)


def generate(**changes):
    return catalog.generate(pi_image=PI, hermes_image=HERMES, engine='pi', runtime_revision='runtime-v1', **changes)


def test_dual_engine_catalog_matches_real_domain_schemas_and_limits(image_packages):
    public, hosts, summary = generate()
    _Public.model_validate(public)
    assert len(public['task_contracts']) == 11
    assert len(public['capability_bundles']) == 2
    assert len(public['execution_bindings']) == len(public['kubernetes_workers']) == len(hosts) == 22
    assert len(public['tool_environments']) == 8
    assert len(public['routes']) == 11
    assert summary['routes_enabled'] is summary['acceptance_created'] is False
    assert summary['network_requests'] == 0
    assert summary['pi_runtime_requirement']['repository'] == 'https://github.com/zmhhaha/pi-cage'
    assert summary['pi_runtime_requirement']['adapter_revision'] == 'pi-cage-adapter-v1'
    assert all(binding['adapter_revision'] == ('pi-cage-adapter-v1' if binding['engine'] == 'pi'
        else 'hermes-adapter-v1') for binding in public['execution_bindings'])
    assert public['model_proxy']['upstream_base_url'] == 'http://llm-service.llm.svc.cluster.local/v1'
    for contract in public['task_contracts']:
        handler = implementation(contract['name'])
        assert contract['task_schema'] == handler.task_schema
        assert contract['output_schema'] == handler.output_schema
        assert set(contract['required_tools']) == set(handler.required_tools)
        for key, expected in handler.limits.as_dict().items():
            assert contract[key] == expected
    registry = DeploymentRegistry.from_config(**{key: public[key] for key in catalog.CATALOG_KEYS})
    for name, host in hosts.items():
        parsed = HostConfig.model_validate(host)
        resolved = host_policy(parsed)
        expected = registry.binding_policy(host['binding_id'], host['binding_revision'])
        assert resolved.pins() == expected.pins()
        assert resolved.enabled is False
        assert host['profile']['enabled'] is False
        assert host['model']['api'] == 'openai-responses'
        assert any(pool['host_config_map'] == name for pool in public['kubernetes_workers'])
    assert all(not pool['enabled'] and pool['uid_start'] is None
        and pool['read_only_mounts'] is False for pool in public['kubernetes_workers'])
    assert all('acceptance' not in binding for binding in public['execution_bindings'])
    assert all(route['enabled'] is False and route['binding_id'].endswith('-pi') for route in public['routes'])


def test_hermes_only_catalog_never_requires_a_pi_image(image_packages):
    public, hosts, _ = catalog.generate(hermes_image=HERMES, engine='hermes', runtime_revision='runtime-v1')
    assert len(hosts) == 11
    assert all(host['engine'] == 'hermes' for host in hosts.values())
    assert all(route['binding_id'].endswith('-hermes') for route in public['routes'])


def test_content_changes_derive_new_binding_and_host_identity(image_packages):
    before, old_hosts, _ = generate()
    (image_packages.references / 'layout.md').write_text('A different synthetic procedure.\n')
    after, new_hosts, _ = generate()
    old = {item['id']: item['revision'] for item in before['execution_bindings']}
    new = {item['id']: item['revision'] for item in after['execution_bindings']}
    assert all((old[key] != new[key]) == key.startswith('ppt-') for key in old)
    assert set(old_hosts) != set(new_hosts)
    assert before['capability_bundles'][1] == after['capability_bundles'][1]


def test_native_image_and_model_policy_are_part_of_combination_identity(image_packages):
    _, before, _ = generate()
    _, after, _ = catalog.generate(pi_image=PI.replace('a' * 64, 'c' * 64),
        hermes_image=HERMES, engine='pi', runtime_revision='runtime-v1', model='another-fixed-model')
    assert not set(before) & set(after)


@pytest.mark.parametrize('field,value', [('pi_image', 'registry.invalid/agent:latest'),
    ('pi_image', 'registry.invalid/agent@sha256:' + '0' * 64),
    ('engine', 'hermes'), ('runtime_revision', '../runtime'),
    ('pod_cidrs', ('0.0.0.0/0',)), ('pod_cidrs', ('8.8.8.0/24',)),
    ('upstream_base_url', 'https://user:pass@example.invalid/v1'),
    ('upstream_base_url', 'https://example.invalid/v1?api_key=forbidden')])
def test_unsafe_operator_inputs_fail_before_catalog_output(image_packages, field, value):
    inputs = dict(pi_image=PI, engine='pi', runtime_revision='runtime-v1')
    inputs[field] = value
    with pytest.raises((catalog.CatalogError, ValueError)):
        catalog.generate(**inputs)


def test_pinned_ppt_commit_and_package_entrypoints_are_required(image_packages):
    image_packages.commit.write_text('wrong-commit\n')
    with pytest.raises(catalog.CatalogError, match='ppt_master_commit_mismatch'):
        generate()
    image_packages.commit.write_text(catalog.PPT_COMMIT + '\n')
    (image_packages.skill / 'SKILL.md').unlink()
    with pytest.raises(catalog.CatalogError, match='ppt_master_resources_missing'):
        generate()


def test_public_output_is_complete_and_cannot_replace_an_existing_catalog(image_packages, tmp_path):
    public, hosts, summary = generate()
    directory = tmp_path / 'public-catalog'
    catalog.write_catalog(directory, public, hosts, summary)
    assert json.loads((directory / 'config.public.json').read_bytes()) == public
    assert json.loads((directory / 'summary.json').read_bytes()) == summary
    assert len(list((directory / 'worker-configs').glob('*/config.json'))) == 22
    assert summary['public_config_sha256'] == hashlib.sha256(catalog.canonical_bytes(public)).hexdigest()
    with pytest.raises(catalog.CatalogError, match='catalog_output_must_be_empty'):
        catalog.write_catalog(directory, public, hosts, summary)


@pytest.mark.parametrize('name,uid', [('nt', 0), ('posix', 10000)])
def test_generator_requires_the_actual_root_worker_image_on_non_posix_hosts(monkeypatch, name, uid):
    # Replace only this module's OS view so both refusal branches execute on
    # every host without changing pathlib's real platform behavior.
    monkeypatch.setattr(catalog, 'os', SimpleNamespace(name=name, geteuid=lambda: uid))
    with pytest.raises(catalog.CatalogError, match='run_inside_root_owned_worker_image'):
        catalog.generate(pi_image=PI, runtime_revision='runtime-v1')
