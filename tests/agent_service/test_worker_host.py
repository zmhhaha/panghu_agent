"""Offline capsule configuration and Linux supervision contracts; no model I/O."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import uuid

import pytest

from agent_service.contracts import ExecutionEnvelope, ProfilePolicy, ServiceError
from agent_service.engines import EngineRequest
from agent_service.model_timeouts import buffered_model_idle_timeout
from agent_service.linux_stop import KernelScope, LinuxUidStopController, StopScopeError, private_kernel_scope
from agent_service.worker_entry import (CapsuleScope, HostConfig, HostSecrets, ProxyModel,
                                       ScopedWorkerHost, WorkerConfigError, accepted_host, build_pi_driver)


ATTEMPT = str(uuid.UUID(int=1))
POD = str(uuid.UUID(int=2))
REQUEST_HASH = 'a' * 64
IMAGE = 'sha256:' + 'b' * 64
KERNEL = KernelScope(str(uuid.UUID(int=3)), 'pid:[12345]')
TOKEN = 'offline-host-caller-key-000000000000'
RUNTIME_UID = 1000001


def config(**changes):
    value = dict(schema_version=1, engine='pi', profile=dict(name='workspace-task.v1', revision='v1',
        runtime_revision='runtime-v1', image_digest=IMAGE, enabled=True, max_timeout_seconds=60),
        model=dict(provider='custom', model='fixed-model', api='openai-responses',
                   proxy_base_url='http://task-proxy.agent-service.svc.cluster.local:8080/v1'))
    value.update(changes)
    return HostConfig.model_validate(value)


def scope():
    return CapsuleScope(attempt_id=ATTEMPT, pod_uid=POD, request_sha256=REQUEST_HASH,
                        agent_uid=RUNTIME_UID, preview_uid=RUNTIME_UID + 1)


def scope_env():
    return dict(AGENT_ATTEMPT_ID=ATTEMPT, AGENT_POD_UID=POD, AGENT_REQUEST_SHA256=REQUEST_HASH,
        AGENT_ENGINE='pi', AGENT_PROFILE_NAME='workspace-task.v1', AGENT_PROFILE_REVISION='v1',
        AGENT_RUNTIME_REVISION='runtime-v1', AGENT_RUNTIME_IMAGE_DIGEST=IMAGE,
        AGENT_RUNTIME_UID=str(RUNTIME_UID), AGENT_PREVIEW_UID=str(RUNTIME_UID + 1))


def credentials(*, grant=True, **changes):
    value = dict(worker_token=TOKEN, receipt_key_hex='c' * 64)
    if grant:
        value.update(model_proxy_token='offline-task-key-000000000000000000000',
                     model_proxy_attempt_id=ATTEMPT, model_proxy_request_sha256=REQUEST_HASH,
                     model_proxy_expires_at=200.0)
    value.update(changes)
    return HostSecrets.model_validate(value)


def test_only_operator_owned_capsule_configuration_is_accepted():
    for extra in ({'command': ['/bin/sh']}, {'uid': 0}, {'host_pid': True}, {'fixture_mode': True}):
        with pytest.raises(ValueError):
            config(**extra)
    with pytest.raises(ValueError):
        config(profile={**config().profile.model_dump(), 'production_ready': True})


@pytest.mark.parametrize('url', ['https://supplier.example/v1', 'http://127.0.0.1:8080/v1',
    'http://8.8.8.8:8080/v1', 'http://169.254.169.254:8080/v1',
    'http://task-proxy:8080/v1', 'http://x.agent.svc.cluster.local:8080/v1?key=x',
    'http://key@x.agent.svc.cluster.local:8080/v1'])
def test_raw_upstream_or_ambiguous_proxy_origin_is_rejected(url):
    with pytest.raises(ValueError):
        ProxyModel(provider='custom', model='fixed-model', api='openai-responses', proxy_base_url=url)


def test_responses_mode_is_explicit_and_only_supported_mode():
    assert config().model.api == 'openai-responses'
    with pytest.raises(ValueError):
        ProxyModel(provider='custom', model='fixed-model', api='openai-completions',
                   proxy_base_url='http://10.1.1.8:8080/v1')


def test_scope_must_match_downward_api_and_all_server_pins():
    c = config()
    env = scope_env()
    assert CapsuleScope.from_env(c, env) == scope()
    for field in env:
        changed = {**env, field: 'untrusted'}
        with pytest.raises((ValueError, WorkerConfigError)):
            CapsuleScope.from_env(c, changed)


@pytest.mark.parametrize('value', [None, '', '10001', '65535', '065536', '+65536', ' 65536',
    '65536 ', '65536.0', '6.5536e4', '2147483648', '999999999999999999999', 65536, True])
def test_production_scope_requires_canonical_reserved_uid(value):
    env = scope_env()
    if value is None:
        del env['AGENT_RUNTIME_UID']
    else:
        env['AGENT_RUNTIME_UID'] = value
    with pytest.raises(WorkerConfigError):
        CapsuleScope.from_env(config(), env)


@pytest.mark.parametrize('uid', [65536, RUNTIME_UID, 2**31 - 2])
def test_production_scope_keeps_exact_trusted_uid(uid):
    actual = CapsuleScope.from_env(config(), {**scope_env(), 'AGENT_RUNTIME_UID': str(uid),
                                             'AGENT_PREVIEW_UID': str(uid + 1)})
    assert actual.agent_uid == uid and actual.preview_uid == uid + 1


def test_direct_scope_construction_keeps_legacy_offline_default_only():
    assert CapsuleScope(attempt_id=ATTEMPT, pod_uid=POD, request_sha256=REQUEST_HASH).agent_uid == 10001


@pytest.mark.parametrize('uid', [None, True, False, 0, -1, 2**31, '65536', 65536.0])
def test_stop_controller_rejects_non_integer_or_out_of_range_uid(tmp_path, uid):
    with pytest.raises(StopScopeError):
        LinuxUidStopController(tmp_path, ATTEMPT, POD, REQUEST_HASH, agent_uid=uid,
                               _scope_reader=lambda: KERNEL)


def test_process_inventory_targets_actual_uid_in_any_credential_slot(tmp_path, monkeypatch):
    import agent_service.linux_stop as module
    stopper = LinuxUidStopController(tmp_path, ATTEMPT, POD, REQUEST_HASH, agent_uid=RUNTIME_UID,
                                     _scope_reader=lambda: KERNEL)
    proc = tmp_path / 'proc'
    proc.mkdir()
    for pid, uids, state in [('11', [10001] * 4, 'S'), ('12', [RUNTIME_UID] * 4, 'S'),
                             ('13', [0, 0, 0, RUNTIME_UID], 'S'), ('14', [RUNTIME_UID] * 4, 'Z')]:
        directory = proc / pid
        directory.mkdir()
        (directory / 'status').write_text('Uid:\t' + ' '.join(map(str, uids)) + '\nState:\t' + state + '\n')
    monkeypatch.setattr(module, 'Path', lambda path: proc if path == '/proc' else Path(path))
    assert set(stopper._uid_processes()) == {12, 13}


def test_unaccepted_host_or_missing_grant_never_advertises_native_readiness():
    for kwargs in ({'acceptance': False}, {'acceptance': True}):
        driver = build_pi_driver(config(), credentials(grant=False), scope(), object(), now=lambda: 100, **kwargs)
        assert driver.ready is False
    for engine in ('pi', 'hermes'):
        driver = build_pi_driver(config(engine=engine), credentials(), scope(), object(),
                                 acceptance=False, now=lambda: 100)
        assert driver.engine == engine and driver.ready is False
    assert build_pi_driver(config(engine='hermes'), credentials(), scope(), object(),
                           acceptance=True, now=lambda: 100).ready is False


@pytest.mark.parametrize('changes', [{'model_proxy_attempt_id': str(uuid.UUID(int=9))},
    {'model_proxy_request_sha256': 'd' * 64}, {'model_proxy_expires_at': 99.0},
    {'model_proxy_expires_at': 701.0}])
def test_proxy_key_must_be_task_bound_unexpired_and_short_lived(changes):
    driver = build_pi_driver(config(), credentials(**changes), scope(), object(),
                             acceptance=True, now=lambda: 100)
    assert driver.ready is False


def test_proxy_credentials_cannot_be_partial_or_printed():
    with pytest.raises(ValueError):
        credentials(grant=False, model_proxy_token='supplier-key-00000000000000000000000')
    value = credentials()
    assert value.worker_token.get_secret_value() not in repr(value)
    assert value.receipt_key_hex.get_secret_value() not in repr(value)
    assert value.model_proxy_token.get_secret_value() not in repr(value)


@pytest.mark.parametrize('max_timeout_seconds', [60, 600, 1800, 3600])
def test_pinned_pi_configuration_keeps_control_credentials_out_of_agent(monkeypatch, max_timeout_seconds):
    captured = {}
    class Runtime:
        def __init__(self, settings, *, profile):
            captured.update(settings=settings, profile=profile)
        def health(self):
            return {'ready': False, 'fixture_mode': False}
    class Stopper:
        consumed = False
    monkeypatch.setattr('agent_service.worker_entry.PiRuntime', Runtime)
    value = config()
    value = config(profile={**value.profile.model_dump(), 'max_timeout_seconds': max_timeout_seconds})
    driver = build_pi_driver(value, credentials(), scope(), Stopper(), acceptance=True, now=lambda: 100)
    s = captured['settings']
    assert s.command == ('/usr/local/bin/node', '/usr/local/lib/pi-cage/worker-cli.mjs')
    assert s.model_api == 'openai-responses' and s.fixture_mode is False
    assert s.model_max_tokens == 8192
    assert s.idle_timeout_seconds == buffered_model_idle_timeout(max_timeout_seconds)
    assert set(s.model_env) == {'AGENT_MODEL_API_KEY'}
    assert TOKEN not in str(s.model_env) and 'c' * 64 not in str(s.model_env)
    assert s.run_uid_start == RUNTIME_UID and s.run_gid == RUNTIME_UID
    assert s.denied_paths == (Path('/run/agent-secrets/worker.json'),)
    assert captured['profile'].load_skills is False
    request = EngineRequest(ATTEMPT, 'workspace-task.v1', {'prompt': 'offline'}, 50,
                            datetime.fromtimestamp(150, timezone.utc))
    wire = driver.wire_builder(request)
    assert wire['_runtime_uid'] == RUNTIME_UID
    late = EngineRequest(ATTEMPT, 'workspace-task.v1', {'prompt': 'offline'}, 101,
                         datetime.fromtimestamp(201, timezone.utc))
    with pytest.raises(ServiceError):
        driver.wire_builder(late)
    assert driver.ready is False  # Runtime checks, not config booleans, decide.


def test_acceptance_needs_exact_pins_and_separate_evidence(monkeypatch, tmp_path):
    import agent_service.worker_entry as module
    c = config()
    artifact = tmp_path / 'acceptance-evidence.json'
    artifact.write_bytes(b'{"offline_fixture_only":true}')
    accepted = dict(schema_version=1, engine='pi', profile=c.profile.name,
        profile_revision=c.profile.revision, runtime_revision=c.profile.runtime_revision,
        image_digest=IMAGE, model_api='openai-responses', checks=dict(capability=True,
        model_round_trip=True, isolation=True, model_proxy_lease=True),
        evidence_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    values = {'acceptance.json': accepted, 'acceptance-evidence.json': {'offline_fixture_only': True}}
    monkeypatch.setattr(module, '_read_operator_json', lambda p: values[Path(p).name])
    path = tmp_path / 'acceptance.json'
    assert accepted_host(c, path)
    for field in ('image_digest', 'runtime_revision', 'profile_revision', 'model_api'):
        changed = dict(accepted, **{field: 'untrusted'})
        values['acceptance.json'] = changed
        assert accepted_host(c, path) is False
    values['acceptance.json'] = dict(accepted, checks={**accepted['checks'], 'isolation': False})
    assert accepted_host(c, path) is False
    values['acceptance.json'] = accepted
    artifact.write_bytes(b'{"changed":true}')
    assert accepted_host(c, path) is False


def test_non_pid_one_controller_is_rejected_before_namespace_access(monkeypatch):
    import agent_service.linux_stop as module
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(module.os, 'getpid', lambda: 12345)
    with pytest.raises(StopScopeError) as exc:
        private_kernel_scope()
    assert exc.value.code == 'private_pid_namespace_required'


@pytest.fixture
def stopper(tmp_path):
    if sys.platform != 'linux':
        pytest.skip('Linux private-control filesystem semantics')
    root = tmp_path / 'private-control'
    root.mkdir(mode=0o700)
    value = LinuxUidStopController(root, ATTEMPT, POD, REQUEST_HASH,
        agent_uid=RUNTIME_UID, _scope_reader=lambda: KERNEL, _sleep=lambda _: None,
        _control_uid=os.geteuid())
    value.initialize()
    return value


def test_stop_scope_is_persisted_before_native_launch_and_cannot_change(stopper):
    assert json.loads((stopper.control_dir / 'uid-scope.json').read_text())['binding']['attempt_id'] == ATTEMPT
    with pytest.raises(StopScopeError):
        LinuxUidStopController(stopper.control_dir, ATTEMPT, str(uuid.UUID(int=8)), REQUEST_HASH,
            agent_uid=RUNTIME_UID, _scope_reader=lambda: KERNEL, _control_uid=os.geteuid()).initialize()
    assert stopper.prove_stopped(ATTEMPT).all_writers_stopped is False


def test_changed_uid_cannot_reopen_persisted_scope(stopper):
    record = json.loads((stopper.control_dir / 'uid-scope.json').read_text())
    assert record['binding']['agent_uid'] == RUNTIME_UID
    with pytest.raises(StopScopeError) as exc:
        LinuxUidStopController(stopper.control_dir, ATTEMPT, POD, REQUEST_HASH,
            agent_uid=RUNTIME_UID + 1, _scope_reader=lambda: KERNEL,
            _control_uid=os.geteuid()).initialize()
    assert exc.value.code == 'host_scope_binding_mismatch'


def test_stop_requires_two_empty_scans_and_is_bound_to_kernel_namespace(stopper, monkeypatch):
    scans = []
    monkeypatch.setattr(stopper, '_uid_processes', lambda: scans.append(True) or [])
    stopper.stop(ATTEMPT)
    assert len(scans) == 2
    proof = stopper.prove_stopped(ATTEMPT)
    assert proof.all_writers_stopped is True and 'pod:' + POD in proof.resource_ids
    assert 'uid:' + str(RUNTIME_UID) in proof.resource_ids
    assert stopper.consumed is True
    stopper._scope_reader = lambda: KernelScope(KERNEL.boot_id, 'pid:[999]')
    with pytest.raises(StopScopeError):
        stopper.prove_stopped(ATTEMPT)


def test_detached_same_uid_writer_is_killed_by_pidfd_not_process_group(stopper, monkeypatch):
    alive = [322]
    sent = []
    monkeypatch.setattr(stopper, '_uid_processes', lambda: list(alive))
    monkeypatch.setattr(os, 'pidfd_open', lambda pid: 700 + pid, raising=False)
    monkeypatch.setattr(os, 'close', lambda fd: None)
    def kill(fd, signum):
        sent.append((fd, signum))
        alive.clear()
    monkeypatch.setattr(signal, 'pidfd_send_signal', kill, raising=False)
    stopper.stop(ATTEMPT)
    assert sent == [(1022, signal.SIGKILL)]
    assert stopper.prove_stopped(ATTEMPT).all_writers_stopped is True


def test_reused_pid_with_different_uid_is_not_killed(stopper, monkeypatch):
    count = [0]
    def scan():
        count[0] += 1
        return [322] if count[0] == 1 else []
    monkeypatch.setattr(stopper, '_uid_processes', scan)
    monkeypatch.setattr(os, 'pidfd_open', lambda pid: 1022, raising=False)
    monkeypatch.setattr(os, 'close', lambda fd: None)
    monkeypatch.setattr(signal, 'pidfd_send_signal', lambda *args: pytest.fail('Reused PID was signalled'), raising=False)
    stopper.stop(ATTEMPT)
    assert stopper.prove_stopped(ATTEMPT).all_writers_stopped is True


def test_unknown_inventory_or_persistent_writer_never_produces_proof(stopper, monkeypatch):
    tick = [0]
    def clock():
        tick[0] += 1
        return float(tick[0])
    stopper._clock = clock
    stopper.stop_seconds = 2
    monkeypatch.setattr(stopper, '_uid_processes', lambda: [322])
    monkeypatch.setattr(os, 'pidfd_open', lambda pid: 1022, raising=False)
    monkeypatch.setattr(os, 'close', lambda fd: None)
    monkeypatch.setattr(signal, 'pidfd_send_signal', lambda *args: None, raising=False)
    with pytest.raises(StopScopeError):
        stopper.stop(ATTEMPT)
    assert stopper.prove_stopped(ATTEMPT).all_writers_stopped is False
    def unavailable():
        raise StopScopeError('process_inventory_unavailable')
    monkeypatch.setattr(stopper, '_uid_processes', unavailable)
    with pytest.raises(StopScopeError):
        stopper.stop(ATTEMPT)


def test_scope_and_request_hash_are_checked_before_any_worker_execution(tmp_path):
    class Stopper:
        consumed = False
    class Driver:
        ready = False
        engine = 'pi'
    policy = ProfilePolicy('workspace-task.v1', 'v1', 'pi', 'runtime-v1', IMAGE)
    host = ScopedWorkerHost(policy, Driver(), tmp_path / 'control', tmp_path / 'workspace',
                            b'x' * 32, scope=scope(), stop_controller=Stopper(), proxy_expires_at=300.0)
    envelope = ExecutionEnvelope(job_id=str(uuid.UUID(int=4)), attempt_id=ATTEMPT, fence=1,
        engine='pi', profile='workspace-task.v1', profile_revision='v1', runtime_revision='runtime-v1',
        image_digest=IMAGE, deadline_at=200.0, lease_expires_at=100.0, task={'prompt': 'offline'})
    with pytest.raises(ServiceError) as exc:
        host.start(envelope)
    assert exc.value.code == 'execution_scope_mismatch' and host._state is None


def test_fixed_bundle_integrity_is_part_of_candidate_image():
    import agent_service.worker_entry as module
    manifest = json.loads((module.RESOURCE_ROOT / 'manifest.json').read_text())
    item = manifest['local_files'][0]
    assert manifest['bundle_revision'] == module.PROFILE_BUNDLE_REVISION
    assert hashlib.sha256((module.RESOURCE_ROOT / item['path']).read_bytes()).hexdigest() == item['sha256']
