"""Pinned repository entry and identity gates; no model or production acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
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


@pytest.mark.parametrize('task_seconds,deadline_seconds,lower_ms,upper_ms', [
    (600, 600, 550_000, 600_000),
    (60, 60, 50_000, 60_000),
    (600, 20, 10_000, 20_000),
])
def test_native_http_wait_uses_remaining_task_budget_without_enabling_retries(
        tmp_path, task_seconds, deadline_seconds, lower_ms, upper_ms):
    """Inspect the settings consumed by a real offline child, without waiting minutes."""
    fake = Path(__file__).with_name('fixtures') / 'fake_pi.py'
    skill = tmp_path / 'SKILL.md'
    skill.write_text('# Offline fixture skill\n', encoding='utf-8')
    value = runtime(fixture_mode=True, idle_timeout_seconds=buffered_model_idle_timeout(task_seconds),
                    term_grace_seconds=.1, kill_grace_seconds=1)
    value.settings = replace(value.settings,
        command=(sys.executable, str(fake), 'success'), node_command=(sys.executable, str(fake), 'node'),
        skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),))
    request = EngineRequest(str(uuid4()), 'workspace-task.v1', {'prompt': 'Offline bounded fixture'},
        task_seconds, datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds))
    workspace = tmp_path / 'attempt'
    value.execute(value.profile.make_wire(request), workspace, lambda _: None, threading.Event())
    settings = json.loads((workspace / '.home/.pi/agent/settings.json').read_text())
    assert type(settings['httpIdleTimeoutMs']) is int
    assert lower_ms <= settings['httpIdleTimeoutMs'] <= upper_ms
    assert settings['retry'] == {'enabled': False, 'provider': {'maxRetries': 0}}
    assert (workspace / 'observed.json').is_file()


@pytest.mark.parametrize('stop,expected', [('deadline', 'timed_out'), ('cancel', 'cancelled')])
def test_no_native_launch_when_deadline_expired_or_cancelled_before_setup(tmp_path, monkeypatch, stop, expected):
    value = runtime(fixture_mode=True)
    cancel = threading.Event()
    if stop == 'cancel':
        cancel.set()
    request = EngineRequest(str(uuid4()), 'workspace-task.v1', {'prompt': 'Offline bounded fixture'}, 60,
        datetime.now(timezone.utc) + timedelta(seconds=-1 if stop == 'deadline' else 60))
    monkeypatch.setattr(value, '_probe', lambda *_: pytest.fail('expired/cancelled task must not launch'))
    with pytest.raises(RuntimeExecutionError) as failed:
        value.execute(value.profile.make_wire(request), tmp_path / 'attempt', lambda _: None, cancel)
    assert failed.value.code == expected
    assert not (tmp_path / 'attempt').exists()


def test_deadline_consumed_during_setup_rejects_before_native_probe(tmp_path, monkeypatch):
    value = runtime(fixture_mode=True)
    clock = [100.0]
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(value, '_verify_resources', lambda: clock.__setitem__(0, 200.0))
    monkeypatch.setattr(value, '_probe', lambda *_: pytest.fail('setup exhausted the task deadline'))
    request = EngineRequest(str(uuid4()), 'workspace-task.v1', {'prompt': 'Offline bounded fixture'}, 60,
        datetime.now(timezone.utc) + timedelta(seconds=60))
    workspace = tmp_path / 'attempt'
    with pytest.raises(RuntimeExecutionError) as failed:
        value.execute(value.profile.make_wire(request), workspace, lambda _: None, threading.Event())
    assert failed.value.code == 'timed_out'
    assert not (workspace / '.home/.pi/agent/settings.json').exists()
    assert not (workspace / 'observed.json').exists()


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


def _offline_drain_runtime(tmp_path, mode='success', command=None):
    fake = Path(__file__).with_name('fixtures') / 'fake_pi.py'
    skill = tmp_path / 'SKILL.md'
    skill.write_text('# Offline native drain fixture\n', encoding='utf-8')
    value = runtime(fixture_mode=True, idle_timeout_seconds=10,
                    term_grace_seconds=.1, kill_grace_seconds=1)
    value.settings = replace(value.settings,
        command=command or (sys.executable, str(fake), mode),
        node_command=(sys.executable, str(fake), 'node'),
        skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),))
    return value


def _delay_native_line(monkeypatch, marker, delay, on_delay=None):
    """Delay a line already read from a real child's pipe, never invent it."""
    original = subprocess.Popen
    children, held = [], threading.Event()

    class DelayedPipe:
        def __init__(self, pipe):
            self.pipe = pipe

        def readline(self, *args):
            data = self.pipe.readline(*args)
            if marker in data:
                held.set()
                if on_delay:
                    on_delay()
                threading.Event().wait(delay)
            return data

        def close(self):
            self.pipe.close()

    def launch(command, *args, **kwargs):
        process = original(command, *args, **kwargs)
        if '--print' in command:
            children.append(process)
            process.stdout = DelayedPipe(process.stdout)
        return process

    monkeypatch.setattr(adapter.subprocess, 'Popen', launch)
    return children, held


def _drain_request(value, seconds=30):
    request = EngineRequest(str(uuid4()), 'workspace-task.v1', {'prompt': 'Offline real subprocess event drain.'},
        30, datetime.now(timezone.utc) + timedelta(seconds=seconds))
    return value.profile.make_wire(request)


def test_exited_root_waits_for_delayed_real_terminal_line(tmp_path, monkeypatch):
    value = _offline_drain_runtime(tmp_path)
    children, held = _delay_native_line(monkeypatch, b'"type": "agent_end"', .5)
    events = []
    value.execute(_drain_request(value), tmp_path / 'attempt', events.append, threading.Event())
    assert held.is_set() and children[0].returncode == 0
    assert any(event.get('event') == 'agent_end' for event in events)
    assert events[-1]['type'] == 'runtime_finished'
    assert (tmp_path / 'attempt' / 'provenance.json').is_file()


def test_eof_without_real_terminal_event_is_still_protocol_error(tmp_path):
    value = _offline_drain_runtime(tmp_path, 'incomplete')
    events = []
    with pytest.raises(RuntimeExecutionError) as failure:
        value.execute(_drain_request(value), tmp_path / 'attempt', events.append, threading.Event())
    assert failure.value.code == 'runtime_protocol_error'
    assert 'complete assistant/agent' in failure.value.message
    assert not any(event.get('event') == 'agent_end' for event in events)
    assert not (tmp_path / 'attempt' / 'provenance.json').exists()


def test_delayed_assistant_still_enforces_provider_model_identity(tmp_path, monkeypatch):
    value = _offline_drain_runtime(tmp_path, 'wrong_model')
    children, held = _delay_native_line(monkeypatch, b'"type": "message_end"', .5)
    with pytest.raises(RuntimeExecutionError) as failure:
        value.execute(_drain_request(value), tmp_path / 'attempt', lambda _: None, threading.Event())
    assert held.is_set() and children[0].returncode == 0
    assert failure.value.code == 'model_mismatch'
    assert not (tmp_path / 'attempt' / 'provenance.json').exists()


@pytest.mark.parametrize('stop,expected', [('cancel', 'cancelled'), ('deadline', 'timed_out')])
def test_exited_root_drain_keeps_original_cancel_and_deadline(tmp_path, monkeypatch, stop, expected):
    value = _offline_drain_runtime(tmp_path)
    cancel = threading.Event()
    timers = []

    def on_delay():
        if stop == 'cancel':
            timer = threading.Timer(.1, cancel.set)
            timers.append(timer)
            timer.start()

    children, held = _delay_native_line(monkeypatch, b'"type": "agent_end"', 1.0, on_delay)
    started = time.monotonic()
    with pytest.raises(RuntimeExecutionError) as failure:
        value.execute(_drain_request(value, .6 if stop == 'deadline' else 30),
                      tmp_path / 'attempt', lambda _: None, cancel)
    for timer in timers:
        timer.join(timeout=1)
    assert held.is_set() and children[0].returncode == 0
    assert failure.value.code == expected and time.monotonic() - started < 2
    assert not (tmp_path / 'attempt' / 'provenance.json').exists()


def test_exited_root_with_descendant_holding_pipes_has_finite_drain_and_stops_tree(tmp_path, monkeypatch):
    cli = tmp_path / 'inherited-pipes.py'
    cli.write_text('''import json,subprocess,sys
from pathlib import Path
if '--version' in sys.argv:print('0.99.2');sys.exit(0)
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
Path('child.pid').write_text(str(child.pid))
for event in [{'type':'agent_start'},
 {'type':'message_end','message':{'role':'assistant','provider':'fixture','model':'fixture-model','stopReason':'stop'}},
 {'type':'agent_end'}]:print(json.dumps(event),flush=True)
''', encoding='utf-8')
    value = _offline_drain_runtime(tmp_path, command=(sys.executable, str(cli)))
    original_stop, stopped = value._stop_tree, []

    def observe_stop(process, group, job_object, uid=None):
        original_stop(process, group, job_object, uid)
        if job_object:
            assert not job_object.alive()
        elif os.name != 'nt':
            assert not value._fixture_group_processes(process.pid)
        stopped.append(True)

    monkeypatch.setattr(value, '_stop_tree', observe_stop)
    events, started = [], time.monotonic()
    with pytest.raises(RuntimeExecutionError) as failure:
        value.execute(_drain_request(value), tmp_path / 'attempt', events.append, threading.Event())
    assert failure.value.code == 'runtime_protocol_error'
    assert failure.value.message == 'Pi output did not finish after root exit'
    assert any(event.get('event') == 'agent_end' for event in events)
    assert stopped == [True] and 5 <= time.monotonic() - started < 8
    assert not (tmp_path / 'attempt' / 'provenance.json').exists()
    assert not list((tmp_path / 'attempt' / '.home').glob('runtime-*.json'))


def test_real_completion_does_not_hide_delayed_malformed_tail(tmp_path, monkeypatch):
    cli = tmp_path / 'malformed-tail.py'
    cli.write_text('''import json,sys
if '--version' in sys.argv:print('0.99.2');sys.exit(0)
for event in [{'type':'agent_start'},
 {'type':'message_end','message':{'role':'assistant','provider':'fixture','model':'fixture-model','stopReason':'stop'}},
 {'type':'agent_end'}]:print(json.dumps(event),flush=True)
print('not-json-late-tail',flush=True)
''', encoding='utf-8')
    value = _offline_drain_runtime(tmp_path, command=(sys.executable, str(cli)))
    children, held = _delay_native_line(monkeypatch, b'not-json-late-tail', .5)
    events = []
    with pytest.raises(RuntimeExecutionError) as failure:
        value.execute(_drain_request(value), tmp_path / 'attempt', events.append, threading.Event())
    assert held.is_set() and children[0].returncode == 0
    assert any(event.get('event') == 'agent_end' for event in events)
    assert failure.value.code == 'runtime_protocol_error'
    assert failure.value.message == 'Pi emitted malformed JSONL'
    assert not (tmp_path / 'attempt' / 'provenance.json').exists()


def test_exited_root_queued_output_drains_through_slow_sink(tmp_path):
    cli = tmp_path / 'queued-output.py'
    cli.write_text('''import json,sys
if '--version' in sys.argv:print('0.99.2');sys.exit(0)
def emit(event):print(json.dumps(event),flush=True)
emit({'type':'agent_start'})
for index in range(80):emit({'type':'tool_execution_end','toolName':'offline'})
emit({'type':'message_end','message':{'role':'assistant','provider':'fixture','model':'fixture-model','stopReason':'stop'}})
emit({'type':'agent_end'})
''', encoding='utf-8')
    value = _offline_drain_runtime(tmp_path, command=(sys.executable, str(cli)))
    events = []

    def slow_sink(event):
        events.append(event)
        if event.get('event') == 'tool_execution_end':
            threading.Event().wait(.075)

    started = time.monotonic()
    value.execute(_drain_request(value), tmp_path / 'attempt', slow_sink, threading.Event())
    assert sum(event.get('event') == 'tool_execution_end' for event in events) == 80
    assert events[-1]['type'] == 'runtime_finished'
    assert time.monotonic() - started > 5
    assert (tmp_path / 'attempt' / 'provenance.json').is_file()
