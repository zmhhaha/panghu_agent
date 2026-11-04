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
