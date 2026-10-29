"""Pinned repository entry and identity gates; no model or production acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time
from uuid import uuid4

import pytest

from agent_service.engine_profiles import WorkspaceTaskProfile
from agent_service.engines import EngineRequest
from agent_service.model_timeouts import buffered_model_idle_timeout
from agent_service.pi_cage import PI_CLI, PI_PACKAGE, PI_VERSION
from agent_service.runtime import PiRuntime, RuntimeExecutionError, RuntimeSettings
from agent_service.runtime import adapter


@pytest.mark.parametrize('name,expected', [
    ('research.generate.v1', ('read', 'write', 'academic_search', 'academic_verify')),
    ('research.revise.v1', ('read', 'write')),
    ('ppt.generate.v1', ('read', 'write', 'bash')),
])
def test_cli_tool_selection_exposes_only_contract_authorized_tools(name, expected):
    from agent_service.domain_runtime import DomainExecutionProfile
    profile = DomainExecutionProfile(SimpleNamespace(name=name), 'fixture-bundle')
    assert profile.allowed_tools == expected


def runtime(**changes):
    settings = RuntimeSettings(command=('/usr/local/bin/node', PI_CLI),
        node_command=('/usr/local/bin/node',), provider='fixture', model='fixture-model',
        skill_paths=(Path('SKILL.md'),), skill_hashes=('a' * 64,),
        skill_bundle_revision='fixture-bundle', runtime_revision='fixture-runtime',
        **changes)
    return PiRuntime(settings, profile=WorkspaceTaskProfile(
        runtime_revision='fixture-runtime', skill_bundle_revision='fixture-bundle'))


@pytest.mark.parametrize('maximum,expected', [(60, 630.0), (600, 630.0), (1800, 1800.0), (3600, 3600.0)])
def test_buffered_proxy_wait_follows_policy_without_changing_native_idle_default(maximum, expected):
    assert RuntimeSettings().idle_timeout_seconds == 120.0
    assert buffered_model_idle_timeout(maximum) == expected


@pytest.mark.parametrize('stop', ['deadline', 'cancel'])
def test_buffered_proxy_idle_does_not_extend_execution_deadline_or_delay_cancel(tmp_path, stop):
    """Run a silent subprocess; no real model, proxy grant or network access."""
    fake = Path(__file__).with_name('fixtures') / 'fake_pi.py'
    skill = tmp_path / 'SKILL.md'
    skill.write_text('# Offline fixture skill\n', encoding='utf-8')
    value = runtime(fixture_mode=True, idle_timeout_seconds=buffered_model_idle_timeout(1800),
                    term_grace_seconds=.1, kill_grace_seconds=1)
    value.settings = replace(value.settings,
        command=(sys.executable, str(fake), 'idle'), node_command=(sys.executable, str(fake), 'node'),
        skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),))
    cancel, launched = threading.Event(), threading.Event()
    def emit(event):
        if event['type'] == 'runtime_started':
            launched.set()
            if stop == 'cancel':
                cancel.set()
    request = EngineRequest(str(uuid4()), 'workspace-task.v1', {'prompt': 'Offline bounded fixture'}, 30,
        datetime.now(timezone.utc) + timedelta(seconds=.8 if stop == 'deadline' else 30))
    started = time.monotonic()
    with pytest.raises(RuntimeExecutionError) as failed:
        value.execute(value.profile.make_wire(request), tmp_path / 'attempt', emit, cancel)
    assert launched.is_set()
    assert failed.value.code == ('timed_out' if stop == 'deadline' else 'cancelled')
    assert time.monotonic() - started < 5
    assert not list((tmp_path / 'attempt' / '.home').glob('runtime-*.json'))


@pytest.mark.parametrize('command', [
    ('pi',),
    ('/usr/local/bin/node', '/opt/pi/node_modules/@mariozechner/pi-coding-agent/dist/cli.js'),
    ('/usr/local/bin/node', '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js'),
    ('/usr/local/bin/node', '/request-selected/worker-cli.mjs'),
    ('/bin/bash', '/usr/local/bin/pi-server', 'serve'),
])
def test_production_cannot_bypass_repository_entry(command):
    value = runtime()
    value.settings = replace(value.settings, command=command)
    with pytest.raises(RuntimeExecutionError) as failed:
        value._check_settings()
    assert failed.value.code == 'configuration_missing'


def test_compactor_accepts_only_explicit_node_worker_entry():
    value = runtime(fixture_mode=True, compact_events=True)
    value._check_settings()
    for command in (('/usr/local/bin/node', '/different/cli.js'),
                    ('/bin/bash', PI_CLI),
                    ('/usr/local/bin/node', '--require', 'untrusted', PI_CLI)):
        value.settings = replace(value.settings, command=command)
        with pytest.raises(RuntimeExecutionError):
            value._check_settings()


@pytest.mark.parametrize('failure', [ValueError('private-path'), OSError('private-path')])
def test_missing_or_mismatched_repository_identity_fails_before_native_probe(monkeypatch, failure):
    value = runtime()
    value._pi_cage_identity = {'repository': 'stale', 'revision': 'stale'}
    monkeypatch.setattr(value, '_check_settings', lambda: None)
    def fail():
        raise failure
    monkeypatch.setattr(adapter, 'verify_runtime', fail)
    monkeypatch.setattr(value, '_readiness_versions', lambda: pytest.fail('must not launch native entry'))
    health = value.health()
    assert health['ready'] is False and health['code'] == 'runtime_not_ready'
    assert 'private-path' not in str(health)
    assert value._source_provenance() == {}


def test_health_identifies_measured_repository_and_version(monkeypatch):
    value = runtime()
    measured = {'repository': 'https://github.com/zmhhaha/pi-cage', 'revision': 'b' * 40}
    monkeypatch.setattr(adapter, 'verify_runtime', lambda: measured)
    monkeypatch.setattr(value, '_check_settings', lambda: None)
    monkeypatch.setattr(value, '_verify_resources', value._verify_pi_cage)
    monkeypatch.setattr(value, '_readiness_versions', lambda: (PI_VERSION, '24.21.0'))
    health = value.health()
    assert health['package'] == PI_PACKAGE == '@earendil-works/pi-coding-agent'
    assert health['pi_version'] == PI_VERSION == '0.99.2'
    assert health['pi_cage_repository'] == measured['repository']
    assert health['pi_cage_revision'] == measured['revision']


def test_fixture_never_asserts_real_repository_identity(monkeypatch):
    value = runtime(fixture_mode=True)
    value._pi_cage_identity = {'repository': 'must-not-be-claimed', 'revision': 'b' * 40}
    monkeypatch.setattr(adapter, 'verify_runtime', lambda: pytest.fail('fixture must stay offline'))
    value._verify_pi_cage()
    assert value._source_provenance() == {}


@pytest.mark.parametrize('node_version,ready', [
    ('20.19.0', False), ('22.18.0', False), ('22.19.0', True), ('24.21.0', True),
])
def test_version_probe_enforces_new_package_node_floor(monkeypatch, tmp_path, node_version, ready):
    value = runtime(fixture_mode=True, control_dir=tmp_path)
    value.settings = replace(value.settings,
        command=(sys.executable, 'offline-pi-fixture'),
        node_command=(sys.executable, 'offline-node-fixture'))
    monkeypatch.setattr(value, '_probe', lambda command, *_: (
        node_version if command[-1] == 'offline-node-fixture' else PI_VERSION))
    if ready:
        assert value._readiness_versions() == (PI_VERSION, node_version)
    else:
        with pytest.raises(RuntimeExecutionError) as failed:
            value._readiness_versions()
        assert failed.value.code == 'runtime_not_ready'
