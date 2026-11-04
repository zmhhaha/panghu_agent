"""Safety checks for the production snapshot operator; execute on server only."""
import io
import json
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from ppt_service.acceptance import production_operations_backup as backup


def tar(path, rows):
    with tarfile.open(path, 'w') as out:
        for name, kind, value in rows:
            item = tarfile.TarInfo(name)
            item.uid, item.gid = os.getuid(), os.getgid()
            item.mode = 0o750 if kind == 'directory' else 0o640
            if kind == 'directory':
                item.type = tarfile.DIRTYPE
                out.addfile(item)
            elif kind == 'link':
                item.type, item.linkname, item.mode = tarfile.SYMTYPE, value, 0o777
                out.addfile(item)
            elif kind == 'hardlink':
                item.type, item.linkname = tarfile.LNKTYPE, value
                out.addfile(item)
            else:
                item.size = len(value)
                out.addfile(item, io.BytesIO(value))


def test_independent_copy_restores_regular_hardlink_and_opaque_link(tmp_path):
    archive = tmp_path / 'files.tar'
    tar(archive, [('data', 'directory', None), ('data/file', 'file', b'actual retained file'),
                  ('data/copy', 'hardlink', 'data/file'), ('data/link', 'link', 'file')])
    rows = backup.safe_extract(archive, tmp_path / 'restored')
    assert (tmp_path / 'restored/data/file').read_bytes() == b'actual retained file'
    assert (tmp_path / 'restored/data/copy').read_bytes() == b'actual retained file'
    assert (tmp_path / 'restored/data/link').is_symlink()
    assert rows['data/file']['sha256'] == rows['data/copy']['sha256']
    assert (tmp_path / 'restored/data/file').stat().st_mode & 0o777 == 0o640
    assert (tmp_path / 'restored/data').stat().st_mode & 0o777 == 0o750


@pytest.mark.parametrize('name', ['../escape', '/escape', 'data/../../escape', 'data/./file'])
def test_archive_paths_cannot_escape_or_alias_destination(tmp_path, name):
    archive = tmp_path / 'files.tar'
    tar(archive, [('data', 'directory', None), (name, 'file', b'forbidden')])
    with pytest.raises((RuntimeError, ValueError)):
        backup.safe_extract(archive, tmp_path / 'restored')
    assert not (tmp_path / 'escape').exists()


def test_archive_symlink_cannot_become_parent_of_written_file(tmp_path):
    archive = tmp_path / 'files.tar'
    outside = tmp_path / 'outside'
    outside.mkdir()
    tar(archive, [('data', 'directory', None), ('data/link', 'link', str(outside)),
                  ('data/link/file', 'file', b'forbidden')])
    with pytest.raises(RuntimeError, match='symlink_parent'):
        backup.safe_extract(archive, tmp_path / 'restored')
    assert not (outside / 'file').exists()


def test_restore_never_adopts_existing_directory(tmp_path):
    archive = tmp_path / 'files.tar'
    tar(archive, [('data', 'directory', None)])
    target = tmp_path / 'existing'
    target.mkdir()
    with pytest.raises(RuntimeError, match='fresh_restore'):
        backup.safe_extract(archive, target)


def test_cas_refuses_different_uid_or_concurrently_changed_spec(monkeypatch):
    expected = {'metadata': {'uid': 'old', 'name': 'ppt-site'}, 'spec': {'replicas': 1}}
    calls = []
    monkeypatch.setattr(backup, 'kube', lambda *args, **kw: calls.append(args))
    for current in ({'metadata': {'uid': 'new'}, 'spec': expected['spec']},
                    {'metadata': {'uid': 'old'}, 'spec': {'replicas': 2}}):
        monkeypatch.setattr(backup, 'get', lambda *args: current)
        with pytest.raises(RuntimeError, match='concurrent_resource'):
            backup.cas_spec('deployment', expected, {'replicas': 0})
    assert calls == []


def test_lost_create_response_cleanup_recovers_only_exact_run_label(monkeypatch):
    observed = {'metadata': {'uid': 'recovered-uid', 'labels': {backup.OWNER: 'expected-run'}}}
    calls = []
    def kube(*args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(stdout=json.dumps(observed).encode())
    monkeypatch.setattr(backup, 'kube', kube)
    assert backup.delete_owned('pod', 'reserved-name', None, 'expected-run') is True
    payload = json.loads(calls[-1][1]['data'])
    assert payload['preconditions'] == {'uid': 'recovered-uid'}
    calls.clear()
    with pytest.raises(RuntimeError, match='owner_changed'):
        backup.delete_owned('pod', 'reserved-name', None, 'different-run')
    assert len(calls) == 1


def test_missing_owned_resource_is_not_deleted_again(monkeypatch):
    calls = []
    def kube(*args, **kw):
        calls.append(args)
        return SimpleNamespace(stdout=b'')
    monkeypatch.setattr(backup, 'kube', kube)
    assert backup.delete_owned('pod', 'reserved-name', 'old-uid', 'expected-run') is False
    assert len(calls) == 1


@pytest.mark.parametrize('changed', ['active_jobs', 'active_projects', 'busy_parses', 'occupied_slots',
                                   'unproven_attached_jobs', 'unacked_outboxes', 'schema'])
def test_idle_checks_all_writers_and_stop_proofs(monkeypatch, changed):
    actual = dict(active_jobs=0, active_projects=0, busy_parses=0, occupied_slots=0,
                  unproven_attached_jobs=0, unacked_outboxes=0, schema=4)
    actual[changed] += 1
    monkeypatch.setattr(backup, 'source_psql', lambda sql: json.dumps(actual))
    with pytest.raises(RuntimeError, match='production_ppt_not_idle'):
        backup.idle()


@pytest.mark.parametrize('path', ['/tmp/ppt-production-backup-test', '/root/unrelated',
                                '/root/ppt-production-backup-good/child'])
def test_outputs_cannot_target_other_paths(path):
    with pytest.raises(RuntimeError, match='private_root_output'):
        backup.private_directory(Path(path))


def capacity(**changes):
    return dict(total_bytes=20 * 1024**3, available_bytes=19 * 1024**3,
                inodes_total=1000, free_inodes=900) | changes


@pytest.mark.parametrize('total,free', [(0, 0), (18541, -1), (-1, -1)])
def test_unknown_inode_accounting_is_preserved_and_never_claimed_sufficient(total, free):
    original = capacity(inodes_total=total, free_inodes=free)
    actual = backup.validate_capacity(original)
    assert actual['inode_capacity_status'] == 'not_reported'
    assert actual['inode_headroom_verified'] is False
    assert actual['inodes_total'] == total and actual['free_inodes'] == free
    assert 'inode_capacity_status' not in original


def test_measured_inode_and_byte_headroom_are_recorded():
    actual = backup.validate_capacity(capacity())
    assert actual['inode_capacity_status'] == 'measured'
    assert actual['inode_headroom_verified'] is True


@pytest.mark.parametrize('changes', [dict(free_inodes=0), dict(free_inodes=100), dict(free_inodes=1001)])
def test_reported_inode_exhaustion_or_inconsistent_measurement_is_refused(changes):
    with pytest.raises(RuntimeError, match='inodes_below_capture_floor'):
        backup.validate_capacity(capacity(**changes))


@pytest.mark.parametrize('changes', [dict(available_bytes=128 * 1024**2), dict(available_bytes=0),
    dict(total_bytes=0), dict(available_bytes=21 * 1024**3)])
def test_unknown_inodes_never_bypass_real_byte_budget(changes):
    with pytest.raises(RuntimeError, match='capacity_below_capture_floor'):
        backup.validate_capacity(capacity(inodes_total=0, free_inodes=0, **changes))


@pytest.mark.parametrize('changes', [dict(available_bytes=True), dict(inodes_total=-2), dict(free_inodes=-2)])
def test_invalid_capacity_fields_are_not_an_unknown_inode_escape(changes):
    with pytest.raises(RuntimeError, match='capacity_measurement_invalid'):
        backup.validate_capacity(capacity(**changes))


def test_capacity_preflight_only_executes_inside_existing_site(monkeypatch):
    calls = []
    def kube(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(capacity(inodes_total=18541, free_inodes=-1)).encode())
    monkeypatch.setattr(backup, 'kube', kube)
    result = backup.capacity_preflight()
    assert result['inode_headroom_verified'] is False
    assert len(calls) == 1 and calls[0][:5] == ('exec', 'deployment/ppt-site', '-c', 'site', '--')


def test_preflight_failure_precedes_every_production_mutation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(backup, 'private_directory', lambda path: tmp_path)
    monkeypatch.setattr(backup, 'source_proof', lambda: {'test_only': True})
    monkeypatch.setattr(backup, 'idle', lambda: {})
    def refuse():
        raise RuntimeError('capacity_preflight_refused')
    monkeypatch.setattr(backup, 'capacity_preflight', refuse)
    monkeypatch.setattr(backup, 'kube', lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(backup, 'preserve', lambda: pytest.fail('must preflight before lock or writer discovery'))
    with pytest.raises(RuntimeError, match='capacity_preflight_refused'):
        backup.capture(tmp_path)
    assert calls == []
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    assert receipt['passed'] is False and receipt['writer_specs_restored'] is True


def restore_container(*, running=True):
    return {'Id': 'a' * 64, 'Name': '/ppt-restore-fixture',
        'Config': {'User': '70:70', 'Labels': {backup.OWNER: 'ppt-restore-fixture'}},
        'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True},
        'Mounts': [{'Type': 'tmpfs', 'Destination': '/var/lib/postgresql/data'}],
        'State': {'Running': running, 'Pid': 12345 if running else 0, 'Restarting': False}}


@pytest.mark.parametrize('known_id', [None, 'a' * 64])
def test_restore_cleanup_binds_stop_wait_zero_pid_and_remove_to_exact_id(monkeypatch, known_id):
    initial, stopped = restore_container(), restore_container(running=False)
    observations = iter([initial, stopped, None, None])
    identities, calls = [], []
    def inspect(identity):
        identities.append(identity)
        return next(observations)
    monkeypatch.setattr(backup, 'inspect_restore_container', inspect)
    monkeypatch.setattr(backup, 'command', lambda args, **kw: calls.append(args))
    receipt = backup.remove_restore_container('ppt-restore-fixture', known_id)
    assert receipt['removed'] and receipt['stop_proven'] and receipt['state']['Pid'] == 0
    assert calls == [['docker', 'stop', '--time=10', 'a' * 64],
                     ['docker', 'wait', 'a' * 64], ['docker', 'rm', 'a' * 64]]
    assert identities == ['ppt-restore-fixture', 'a' * 64, 'a' * 64, 'ppt-restore-fixture']


@pytest.mark.parametrize('change', ['id', 'name', 'label', 'network', 'readonly', 'user', 'volume'])
def test_restore_cleanup_refuses_replaced_or_out_of_scope_container(monkeypatch, change):
    actual, calls = restore_container(), []
    if change == 'id':
        actual['Id'] = 'b' * 64
    elif change == 'name':
        actual['Name'] = '/other'
    elif change == 'label':
        actual['Config']['Labels'][backup.OWNER] = 'other'
    elif change == 'network':
        actual['HostConfig']['NetworkMode'] = 'host'
    elif change == 'readonly':
        actual['HostConfig']['ReadonlyRootfs'] = False
    elif change == 'user':
        actual['Config']['User'] = '0:0'
    else:
        actual['Mounts'] = [{'Type': 'volume', 'Name': 'unowned-anonymous-volume'}]
    monkeypatch.setattr(backup, 'inspect_restore_container', lambda identity: actual)
    monkeypatch.setattr(backup, 'command', lambda args, **kw: calls.append(args))
    with pytest.raises(RuntimeError, match='database_cleanup_'):
        backup.remove_restore_container('ppt-restore-fixture', 'a' * 64)
    assert calls == []


@pytest.mark.parametrize('state', ['running', 'pid', 'restarting', 'missing'])
def test_restore_cleanup_never_removes_without_physical_stop_receipt(monkeypatch, state):
    initial, stopped, calls = restore_container(), restore_container(running=False), []
    if state == 'running':
        stopped['State']['Running'] = True
    elif state == 'pid':
        stopped['State']['Pid'] = 12345
    elif state == 'restarting':
        stopped['State']['Restarting'] = True
    else:
        stopped = None
    observations = iter([initial, stopped])
    monkeypatch.setattr(backup, 'inspect_restore_container', lambda identity: next(observations))
    monkeypatch.setattr(backup, 'command', lambda args, **kw: calls.append(args))
    with pytest.raises(RuntimeError, match='database_(stop_not_proven|disappeared_before_stop_proof)'):
        backup.remove_restore_container('ppt-restore-fixture', 'a' * 64)
    assert all(args[1] != 'rm' for args in calls)


def test_restore_known_container_cannot_disappear_before_stop_proof(monkeypatch):
    monkeypatch.setattr(backup, 'inspect_restore_container', lambda identity: None)
    with pytest.raises(RuntimeError, match='disappeared_before_cleanup'):
        backup.remove_restore_container('ppt-restore-fixture', 'a' * 64)
    assert backup.remove_restore_container('ppt-restore-fixture', None) == {
        'created': False, 'removed': True, 'stop_proven': False}


@pytest.mark.parametrize('message,stdout', [
    (b'Cannot connect to the Docker daemon', b''),
    (b'permission denied', b''),
    (b'Error: No such object: other', b'[]'),
    (b'Error: No such object: ppt-restore-fixture', b'not-json')])
def test_docker_errors_cannot_be_mistaken_for_database_absence(monkeypatch, message, stdout):
    monkeypatch.setattr(backup, 'command', lambda args, **kw: SimpleNamespace(
        returncode=1, stderr=message, stdout=stdout))
    with pytest.raises(RuntimeError, match='inspect_failure_not_absence'):
        backup.inspect_restore_container('ppt-restore-fixture')


def test_exact_docker_not_found_also_requires_healthy_daemon(monkeypatch):
    calls = []
    def command(args, **kw):
        calls.append(args)
        if args[1] == 'inspect':
            return SimpleNamespace(returncode=1, stderr=b'Error: No such object: ppt-restore-fixture', stdout=b'[]')
        raise RuntimeError('daemon_unavailable')
    monkeypatch.setattr(backup, 'command', command)
    with pytest.raises(RuntimeError, match='daemon_unavailable'):
        backup.inspect_restore_container('ppt-restore-fixture')
    assert calls[-1] == ['docker', 'info', '--format', '{{.ID}}']


def test_restore_cleanup_checks_name_and_id_absence_after_removal(monkeypatch):
    observations = iter([restore_container(), restore_container(running=False), None, restore_container(running=False)])
    monkeypatch.setattr(backup, 'inspect_restore_container', lambda identity: next(observations))
    monkeypatch.setattr(backup, 'command', lambda args, **kw: None)
    with pytest.raises(RuntimeError, match='database_removal_not_proven'):
        backup.remove_restore_container('ppt-restore-fixture', 'a' * 64)
