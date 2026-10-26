"""Kernel confinement observations and startup ordering, without models/secrets."""
from pathlib import Path
from contextlib import contextmanager
import stat
from types import SimpleNamespace

import pytest

from agent_service import confinement
from agent_service.contracts import ServiceError


STATUS = 'Name:\tpython\nNoNewPrivs:\t1\nSeccomp:\t2\nSeccomp_filters:\t1\n'


def reader(label=confinement.APPARMOR_LABEL + '\n', status=STATUS):
    values = {confinement.STATUS: status, confinement.APPARMOR_CURRENT: label}
    return values.__getitem__


def test_exact_enforced_controller_label_nnp_and_seccomp_are_measured():
    actual = confinement.verify_controller_confinement(read_only_mounts=True, _reader=reader())
    assert actual == dict(no_new_privileges=True, seccomp_filtering=True,
                         apparmor_label='agent-capsule-inputs (enforce)')


@pytest.mark.parametrize('label', ['unconfined\n', 'agent-capsule-inputs (complain)\n',
    'docker-default (enforce)\n', 'agent-capsule-inputs\n',
    ':namespace://agent-capsule-inputs (enforce)\n',
    'agent-capsule-inputs (enforce)\n\n', ' agent-capsule-inputs (enforce)\n',
    'agent-capsule-inputs (enforce)\x00', None, b'agent-capsule-inputs (enforce)'])
def test_unconfined_complain_wrong_or_ambiguous_labels_fail_closed(label):
    with pytest.raises(ServiceError) as failure:
        confinement.verify_controller_confinement(read_only_mounts=True, _reader=reader(label))
    assert failure.value.code == 'controller_apparmor_not_enforced'


@pytest.mark.parametrize('status,code', [
    ('NoNewPrivs:\t0\nSeccomp:\t2\n', 'controller_no_new_privileges_required'),
    ('Seccomp:\t2\n', 'controller_no_new_privileges_required'),
    ('NoNewPrivs:\t1\nSeccomp:\t0\n', 'controller_seccomp_filter_required'),
    ('NoNewPrivs:\t1\nSeccomp:\t1\n', 'controller_seccomp_filter_required'),
    ('NoNewPrivs:\t1\n', 'controller_seccomp_filter_required'),
    (STATUS + 'Seccomp:\t2\n', 'controller_confinement_unavailable'),
])
def test_spec_cannot_substitute_for_actual_nnp_or_seccomp(status, code):
    with pytest.raises(ServiceError) as failure:
        confinement.verify_controller_confinement(read_only_mounts=True, _reader=reader(status=status))
    assert failure.value.code == code


def test_generic_lsm_path_is_only_fallback_for_absent_apparmor_interface():
    calls = []
    def read(path):
        calls.append(path)
        if path == confinement.STATUS:
            return STATUS
        if path == confinement.APPARMOR_CURRENT:
            raise FileNotFoundError()
        assert path == confinement.LSM_CURRENT
        return confinement.APPARMOR_LABEL
    assert confinement.verify_controller_confinement(read_only_mounts=True, _reader=read)['apparmor_label']
    assert calls == [confinement.STATUS, confinement.APPARMOR_CURRENT, confinement.LSM_CURRENT]


@pytest.mark.parametrize('error', [PermissionError('private-read-error'), OSError('private-read-error')])
def test_read_errors_never_fallback_or_echo_arbitrary_diagnostics(error):
    calls = []
    def read(path):
        calls.append(path)
        if path == confinement.STATUS:
            return STATUS
        raise error
    with pytest.raises(ServiceError) as failure:
        confinement.verify_controller_confinement(read_only_mounts=True, _reader=read)
    assert failure.value.code == 'controller_confinement_unavailable'
    assert 'private-read-error' not in str(failure.value)
    assert confinement.LSM_CURRENT not in calls


def test_non_mount_pool_still_requires_nnp_and_seccomp_without_reading_apparmor():
    def read(path):
        assert path == confinement.STATUS
        return STATUS
    actual = confinement.verify_controller_confinement(read_only_mounts=False, _reader=read)
    assert actual['seccomp_filtering'] is True and actual['apparmor_label'] is None


def test_already_enforced_profile_never_attempts_a_transition():
    def forbidden(*_):
        pytest.fail('Already confined controller must not change profile')
    result = confinement.ensure_controller_confinement(read_only_mounts=True,
        _reader=reader(), _writer=forbidden)
    assert result['apparmor_label'] == confinement.APPARMOR_LABEL
    assert result['apparmor_self_restricted'] is False


def test_only_exact_unconfined_can_self_restrict_and_final_kernel_readback_is_required():
    state = {'label': 'unconfined\n'}
    reads, writes = [], []
    def read(path):
        reads.append(path)
        return STATUS if path == confinement.STATUS else state['label']
    def restrict(path):
        writes.append(path)
        state['label'] = confinement.APPARMOR_LABEL + '\n'
    result = confinement.ensure_controller_confinement(read_only_mounts=True, _reader=read, _writer=restrict)
    assert writes == [confinement.APPARMOR_CURRENT]
    assert reads.count(confinement.STATUS) == reads.count(confinement.APPARMOR_CURRENT) == 2
    assert result['apparmor_self_restricted'] is True


def test_self_restriction_uses_generic_interface_only_when_specific_interface_is_absent():
    state, writes = {'label': 'unconfined\n'}, []
    def read(path):
        if path == confinement.STATUS:
            return STATUS
        if path == confinement.APPARMOR_CURRENT:
            raise FileNotFoundError()
        assert path == confinement.LSM_CURRENT
        return state['label']
    def restrict(path):
        writes.append(path)
        state['label'] = confinement.APPARMOR_LABEL + '\n'
    result = confinement.ensure_controller_confinement(read_only_mounts=True, _reader=read, _writer=restrict)
    assert writes == [confinement.LSM_CURRENT] and result['apparmor_self_restricted'] is True


def test_status_is_measured_again_after_transition_without_repairing_kernel_settings():
    state = {'label': 'unconfined\n', 'status': STATUS}
    def read(path):
        return state['status'] if path == confinement.STATUS else state['label']
    def restrict(path):
        state.update(label=confinement.APPARMOR_LABEL + '\n', status='NoNewPrivs:1\nSeccomp:0\n')
    with pytest.raises(ServiceError) as failure:
        confinement.ensure_controller_confinement(read_only_mounts=True, _reader=read, _writer=restrict)
    assert failure.value.code == 'controller_seccomp_filter_required'


@pytest.mark.parametrize('after', ['unconfined\n', 'agent-capsule-inputs (complain)\n',
                                 'other-profile (enforce)\n'])
def test_successful_write_cannot_substitute_for_exact_enforced_readback(after):
    state = {'label': 'unconfined\n'}
    def read(path):
        return STATUS if path == confinement.STATUS else state['label']
    def restrict(path):
        state['label'] = after
    with pytest.raises(ServiceError) as failure:
        confinement.ensure_controller_confinement(read_only_mounts=True, _reader=read, _writer=restrict)
    assert failure.value.code == 'controller_apparmor_not_enforced'


@pytest.mark.parametrize('label', ['agent-capsule-inputs (complain)\n', 'wrong (enforce)\n',
                                 ' unconfined\n', 'unconfined\n\n'])
def test_wrong_or_complain_profile_never_attempts_transition(label):
    def forbidden(*_):
        pytest.fail('Only an exact unconfined label permits a transition')
    with pytest.raises(ServiceError) as failure:
        confinement.ensure_controller_confinement(read_only_mounts=True, _reader=reader(label), _writer=forbidden)
    assert failure.value.code == 'controller_apparmor_not_enforced'


def test_transition_denial_remains_closed_and_does_not_echo_kernel_error():
    def denied(*_):
        raise PermissionError('private-kernel-diagnostic')
    with pytest.raises(ServiceError) as failure:
        confinement.ensure_controller_confinement(read_only_mounts=True,
            _reader=reader('unconfined\n'), _writer=denied)
    assert failure.value.code == 'controller_confinement_unavailable'
    assert 'private-kernel' not in str(failure.value)


@pytest.mark.parametrize('status', ['NoNewPrivs:0\nSeccomp:2\n', 'NoNewPrivs:1\nSeccomp:0\n'])
def test_transition_never_runs_without_initial_nnp_and_seccomp(status):
    def forbidden(*_):
        pytest.fail('Kernel prerequisites must be checked first')
    with pytest.raises(ServiceError):
        confinement.ensure_controller_confinement(read_only_mounts=True,
            _reader=reader('unconfined\n', status), _writer=forbidden)


@pytest.mark.parametrize('short_write', [False, True])
def test_fixed_transition_writer_accepts_no_profile_argument_and_requires_full_write(monkeypatch, short_write):
    calls = []
    @contextmanager
    def opened(path, mode):
        calls.append((path, mode))
        yield 123
    def write(descriptor, command):
        assert descriptor == 123
        calls.append(command)
        return len(command) - int(short_write)
    monkeypatch.setattr(confinement, '_current_proc_file', opened)
    monkeypatch.setattr(confinement.os, 'write', write)
    if short_write:
        with pytest.raises(ServiceError):
            confinement._restrict_to_fixed_profile(confinement.LSM_CURRENT)
    else:
        confinement._restrict_to_fixed_profile(confinement.LSM_CURRENT)
    assert calls == [(confinement.LSM_CURRENT, confinement.os.O_WRONLY),
                     b'changeprofile agent-capsule-inputs']


@pytest.mark.parametrize('supports_inputs,flag,expected', [(False, None, False),
    (False, '1', True), (True, None, True)])
def test_production_preflight_uses_fixed_pool_flag_or_domain_input_requirement(monkeypatch,
        supports_inputs, flag, expected):
    from agent_service import worker_entry
    monkeypatch.setattr(worker_entry, 'host_policy', lambda _: SimpleNamespace(supports_inputs=supports_inputs))
    if flag is None:
        monkeypatch.delenv('AGENT_READ_ONLY_MOUNTS', raising=False)
    else:
        monkeypatch.setenv('AGENT_READ_ONLY_MOUNTS', flag)
    result = worker_entry.controller_preflight(None, _reader=reader())
    assert result['apparmor_label'] == (confinement.APPARMOR_LABEL if expected else None)


@pytest.mark.parametrize('mode', [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600])
def test_proc_reader_rejects_links_and_special_files_before_open(monkeypatch, mode):
    opened, closed = [], []
    monkeypatch.setattr(confinement.sys, 'platform', 'linux')
    for name, value in [('O_DIRECTORY', 65536), ('O_NOFOLLOW', 131072), ('O_NONBLOCK', 2048)]:
        monkeypatch.setattr(confinement.os, name, value, raising=False)
    monkeypatch.setattr(confinement.os, 'geteuid', lambda: 0, raising=False)
    def open_file(path, flags, **options):
        opened.append((path, flags, options))
        return 100 + len(opened)
    monkeypatch.setattr(confinement.os, 'open', open_file)
    monkeypatch.setattr(confinement.os, 'close', closed.append)
    monkeypatch.setattr(confinement.os, 'stat', lambda *_, **__: SimpleNamespace(st_mode=mode, st_uid=0))
    with pytest.raises(ServiceError) as failure:
        confinement._read_current_proc(confinement.STATUS)
    assert failure.value.code == 'controller_confinement_unavailable'
    assert [item[0] for item in opened] == ['/proc', str(confinement.os.getpid())]
    assert closed == [102, 101]


def test_production_main_checks_before_reading_worker_authorization(monkeypatch, capsys):
    from agent_service import worker_entry
    from test_worker_host import config
    reads = []
    def read(path, **_):
        reads.append(Path(path))
        assert len(reads) == 1
        return config().model_dump(mode='json')
    def reject(*_, **__):
        raise ServiceError('controller_apparmor_not_enforced', 503)
    monkeypatch.setattr(worker_entry, '_read_operator_json', read)
    monkeypatch.setattr(worker_entry, 'controller_preflight', reject)
    assert worker_entry.main([]) == 1
    assert reads == [Path('/etc/agent-worker/config.json')]
    assert 'worker_host_unavailable' in capsys.readouterr().out


def test_direct_production_factory_cannot_bypass_actual_controller_check(monkeypatch):
    from agent_service import worker_entry
    from test_worker_host import config
    def reject(*_, **__):
        raise ServiceError('controller_apparmor_not_enforced', 503)
    monkeypatch.setattr(worker_entry, 'controller_preflight', reject)
    monkeypatch.setattr(worker_entry, 'private_kernel_scope', lambda: pytest.fail('No later startup work'))
    with pytest.raises(ServiceError):
        worker_entry.assemble_app(config(), None, None)


@pytest.mark.parametrize('entrypoint', ['run_worker', 'isolation_preflight'])
def test_independent_probe_uses_same_check_before_authorization_or_isolation_work(monkeypatch, entrypoint):
    from agent_service.deploy import acceptance_probe as probe
    calls = []
    def reject(*, read_only_mounts):
        calls.append(read_only_mounts)
        raise ServiceError('controller_apparmor_not_enforced', 503)
    monkeypatch.setattr(probe, 'ensure_controller_confinement', reject)
    monkeypatch.setattr(probe, 'credential', lambda *_: pytest.fail('No authorization may be read'))
    with pytest.raises(ServiceError):
        if entrypoint == 'run_worker':
            probe.run_worker('http://candidate.agent-service.svc.cluster.local:8080')
        else:
            probe.isolation_preflight(None)
    assert calls == [True]
