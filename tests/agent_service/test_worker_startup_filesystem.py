"""Production entrypoint boundaries for Kubernetes projected and emptyDir mounts."""
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

from agent_service import worker_entry as module


pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux mount and permission semantics')


@pytest.fixture
def filesystem(monkeypatch):
    """Keep real file reads/links, while supplying the operator/mount metadata."""
    original_stat = Path.stat
    metadata, flags, measured = {}, {}, []

    def observed_stat(path, *, follow_symlinks=True):
        value = original_stat(path, follow_symlinks=follow_symlinks)
        fields = list(value)
        fields[4] = 0
        if stat.S_ISDIR(value.st_mode):
            fields[0] = stat.S_IFDIR | 0o755
        changes = metadata.get(Path(os.path.abspath(path)), {})
        for name, index in (('mode', 0), ('nlink', 3), ('uid', 4)):
            if name in changes:
                fields[index] = (stat.S_IFMT(value.st_mode) | changes[name]
                                 if name == 'mode' else changes[name])
        return os.stat_result(fields)

    def statvfs(path):
        path = Path(path)
        measured.append(path)
        return SimpleNamespace(f_flag=flags.get(path, 0))

    monkeypatch.setattr(Path, 'stat', observed_stat)
    monkeypatch.setattr(module.os, 'statvfs', statvfs)
    return SimpleNamespace(metadata=metadata, flags=flags, measured=measured)


@pytest.fixture
def projected(tmp_path, filesystem):
    mount = tmp_path / 'operator-mount'
    target = mount / '..2026_10_28'
    target.mkdir(parents=True)
    document = target / 'config.json'
    document.write_bytes(b'{"configured":true}')
    document.chmod(0o444)
    (mount / '..data').symlink_to(target.name, target_is_directory=True)
    path = mount / 'config.json'
    path.symlink_to('..data/config.json')
    filesystem.metadata[mount] = {'mode': 0o777}
    filesystem.flags[mount] = os.ST_RDONLY
    return SimpleNamespace(mount=mount, document=document, path=path)


def test_readonly_projected_mount_allows_mode_bits_and_real_data_links(projected, filesystem):
    assert module._read_operator_json(projected.path) == {'configured': True}
    assert filesystem.measured == [projected.mount]


@pytest.mark.parametrize('component', ['mount', 'ancestor'])
def test_writable_directory_requires_measured_readonly_mount(projected, filesystem, component):
    path = projected.mount if component == 'mount' else projected.mount.parent
    filesystem.metadata[path] = {'mode': 0o777}
    filesystem.flags[path] = 0
    with pytest.raises(module.WorkerConfigError):
        module._read_operator_json(projected.path)
    assert path in filesystem.measured


@pytest.mark.parametrize('component', ['mount', 'ancestor', 'file'])
def test_readonly_mount_never_allows_non_root_ownership(projected, filesystem, component):
    path = {'mount': projected.mount, 'ancestor': projected.mount.parent,
            'file': projected.document}[component]
    filesystem.metadata[path] = {'uid': 10001, 'mode': 0o777 if component != 'file' else 0o444}
    filesystem.flags[path] = os.ST_RDONLY
    with pytest.raises(module.WorkerConfigError):
        module._read_operator_json(projected.path)


@pytest.mark.parametrize('failure', ['writable_file', 'hardlink', 'empty', 'oversized',
                                    'escape', 'nonregular', 'duplicate_json'])
def test_readonly_projection_keeps_file_and_document_checks(projected, tmp_path, failure):
    if failure == 'writable_file':
        projected.document.chmod(0o666)
    elif failure == 'hardlink':
        os.link(projected.document, projected.document.with_name('other.json'))
    elif failure == 'empty':
        projected.document.write_bytes(b'')
    elif failure == 'oversized':
        projected.document.write_bytes(b' ' * 33)
    elif failure == 'escape':
        outside = tmp_path / 'outside.json'
        outside.write_bytes(b'{"configured":true}')
        projected.path.unlink()
        projected.path.symlink_to(outside)
    elif failure == 'nonregular':
        projected.document.unlink()
        projected.document.mkdir()
    elif failure == 'duplicate_json':
        projected.document.write_bytes(b'{"same":1,"same":2}')
    with pytest.raises(module.WorkerConfigError):
        module._read_operator_json(projected.path, max_bytes=32)


def test_readonly_projection_preserves_private_file_mode_requirement(projected):
    with pytest.raises(module.WorkerConfigError):
        module._read_operator_json(projected.path, private=True)
    projected.document.chmod(0o400)
    assert module._read_operator_json(projected.path, private=True) == {'configured': True}


@pytest.fixture
def startup(tmp_path, monkeypatch):
    control, work = tmp_path / 'control', tmp_path / 'work'
    work.mkdir()
    work.chmod(0o777)
    monkeypatch.setattr(module, 'CONTROL_ROOT', control)
    monkeypatch.setattr(module, 'WORKSPACE_ROOT', work)
    monkeypatch.setattr(module, 'controller_preflight', lambda _: None)
    monkeypatch.setattr(module, 'private_kernel_scope', lambda: None)
    monkeypatch.setattr(module, 'host_policy', lambda _: object())
    # Model/runtime construction must only be reached after the mount boundary.
    class BoundaryReached(Exception):
        pass
    def driver(*args, **kwargs):
        assert stat.S_IMODE(work.stat().st_mode) == 0o711
        assert stat.S_IMODE(control.stat().st_mode) == 0o700
        raise BoundaryReached()
    monkeypatch.setattr(module, 'accepted_host', lambda _: False)
    monkeypatch.setattr(module, 'build_pi_driver', driver)
    # No scope journal or model process is started in these entrypoint tests.
    monkeypatch.setattr(module, 'LinuxUidStopController', lambda *a, **kw: object())
    return SimpleNamespace(work=work, control=control, reached=BoundaryReached,
        config=SimpleNamespace(engine='pi'),
        scope=SimpleNamespace(attempt_id='offline', pod_uid='offline', request_sha256='a' * 64,
                              agent_uid=65536, preview_uid=65537))


def test_fresh_emptydir_is_private_before_driver_initialization(startup, monkeypatch):
    original_stat = Path.stat
    def root_owned(path, *, follow_symlinks=True):
        value = list(original_stat(path, follow_symlinks=follow_symlinks))
        value[4] = 0
        return os.stat_result(value)
    monkeypatch.setattr(Path, 'stat', root_owned)
    with pytest.raises(startup.reached):
        module.assemble_app(startup.config, None, startup.scope)


@pytest.mark.parametrize('failure', ['nonempty', 'symlink', 'not_root', 'not_directory'])
def test_emptydir_does_not_adopt_untrusted_existing_paths(startup, monkeypatch, failure):
    original_stat = Path.stat
    def ownership(path, *, follow_symlinks=True):
        value = list(original_stat(path, follow_symlinks=follow_symlinks))
        value[4] = 10001 if failure == 'not_root' and path == startup.work else 0
        return os.stat_result(value)
    monkeypatch.setattr(Path, 'stat', ownership)
    if failure == 'nonempty':
        (startup.work / 'existing').write_bytes(b'untrusted')
    elif failure == 'symlink':
        target = startup.work.with_name('other')
        target.mkdir()
        startup.work.rmdir()
        startup.work.symlink_to(target, target_is_directory=True)
    elif failure == 'not_directory':
        startup.work.rmdir()
        startup.work.write_bytes(b'untrusted')
    with pytest.raises(module.WorkerConfigError):
        module.assemble_app(startup.config, None, startup.scope)
    if failure == 'nonempty':
        assert stat.S_IMODE(startup.work.stat().st_mode) == 0o777
        assert (startup.work / 'existing').read_bytes() == b'untrusted'
