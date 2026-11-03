"""Synthetic Linux capacity faults; never fills a disk or touches real users.

Run on the server. Measurements and failures are injected at actual filesystem
operations; the small upload files, file descriptors and cleanup are real.
The HTTP fixture uses in-memory SQLite and an explicit identity override, so it
does not provide PostgreSQL, reverse-proxy, SSO or production filesystem proof.
"""
import asyncio
import builtins
import errno
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from ppt_service.server import storage as storage_module
from ppt_service.server.app import create_app
from ppt_service.server.auth import Identity, Settings, authenticate
from ppt_service.server.migrate import migrate
from ppt_service.server.models import Asset
from ppt_service.server.storage import DEFAULT_UPLOAD_MIN_FREE_BYTES, Storage


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Linux statvfs and real-file-descriptor contract')
RESERVE = 4096


class Chunks:
    filename = 'synthetic.md'

    def __init__(self, *chunks):
        self.chunks = list(chunks)
        self.reads = 0

    async def read(self, maximum):
        self.reads += 1
        chunk = self.chunks.pop(0) if self.chunks else b''
        assert len(chunk) <= maximum
        return chunk


def measured(available):
    return SimpleNamespace(f_bavail=available, f_frsize=1)


def upload(storage, source):
    return asyncio.run(storage.upload('synthetic-project', source))


def no_originals_or_attempt_directories(root):
    assert not list(root.rglob('original'))
    assets = root / 'projects' / 'synthetic-project' / 'assets'
    assert not assets.exists() or not list(assets.iterdir())


def assert_capacity_error(error, status=507, code='storage_capacity_low'):
    assert error.value.status_code == status
    assert error.value.detail == {'code': code}


def test_exact_reserve_plus_chunk_boundary_accepts_and_hashes_actual_bytes(tmp_path, monkeypatch):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    source = Chunks(b'boundary')
    root_calls, descriptor_calls = [], []
    def at_root(path):
        root_calls.append(Path(path))
        return measured(RESERVE)
    def at_file(fd):
        assert stat.S_ISREG(os.fstat(fd).st_mode)
        descriptor_calls.append(os.lseek(fd, 0, os.SEEK_CUR))
        return measured(RESERVE + len(b'boundary'))
    monkeypatch.setattr(storage_module.os, 'statvfs', at_root)
    monkeypatch.setattr(storage_module.os, 'fstatvfs', at_file)
    _, key, size, digest, mime = upload(storage, source)
    assert root_calls == [tmp_path]
    assert descriptor_calls == [0]
    assert source.reads == 2
    assert size == len(b'boundary') and digest == hashlib.sha256(b'boundary').hexdigest()
    assert mime == 'text/markdown'
    with storage.open(key, size) as stream:
        assert stream.read() == b'boundary'


def test_below_reserve_refuses_before_directories_or_stream_reads(tmp_path, monkeypatch):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    source = Chunks(b'should not be read')
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(RESERVE - 1))
    def no_file_measurement(_):
        pytest.fail('A rejected preflight must not open the destination')
    monkeypatch.setattr(storage_module.os, 'fstatvfs', no_file_measurement)
    with pytest.raises(HTTPException) as error:
        upload(storage, source)
    assert_capacity_error(error)
    assert source.reads == 0
    assert list(tmp_path.iterdir()) == []


def test_second_chunk_capacity_loss_removes_partial_file_and_attempt_directory(tmp_path, monkeypatch):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    first, second = b'first chunk', b'second chunk'
    source = Chunks(first, second)
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(RESERVE + 100))
    calls = []
    def dynamic(fd):
        assert stat.S_ISREG(os.fstat(fd).st_mode)
        offset = os.lseek(fd, 0, os.SEEK_CUR)
        calls.append(offset)
        if len(calls) == 1:
            return measured(RESERVE + len(first))
        assert offset == len(first), 'Previous chunk must have reached the actual file'
        assert os.fstat(fd).st_size == len(first)
        return measured(RESERVE + len(second) - 1)
    monkeypatch.setattr(storage_module.os, 'fstatvfs', dynamic)
    with pytest.raises(HTTPException) as error:
        upload(storage, source)
    assert_capacity_error(error)
    assert calls == [0, len(first)] and source.reads == 2
    no_originals_or_attempt_directories(tmp_path)


def test_capacity_uses_available_blocks_and_fragment_size(tmp_path, monkeypatch):
    storage = Storage(tmp_path, min_free_bytes=4096)
    # f_bfree is deliberately high: only unprivileged available blocks count.
    root_stat = SimpleNamespace(f_bavail=1, f_frsize=4096, f_bfree=999999)
    file_stat = SimpleNamespace(f_bavail=1, f_frsize=4096, f_bfree=999999)
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: root_stat)
    monkeypatch.setattr(storage_module.os, 'fstatvfs', lambda _: file_stat)
    with pytest.raises(HTTPException) as error:
        upload(storage, Chunks(b'x'))
    assert_capacity_error(error)
    no_originals_or_attempt_directories(tmp_path)


@pytest.mark.parametrize('available,status', [(0, 507), (1, 201)])
def test_zero_reserve_still_checks_headroom_for_incoming_bytes(tmp_path, monkeypatch, available, status):
    storage = Storage(tmp_path, min_free_bytes=0)
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(0))
    descriptors = []
    def check_file(fd):
        descriptors.append(fd)
        assert stat.S_ISREG(os.fstat(fd).st_mode)
        return measured(available)
    monkeypatch.setattr(storage_module.os, 'fstatvfs', check_file)
    if status == 507:
        with pytest.raises(HTTPException) as error:
            upload(storage, Chunks(b'x'))
        assert_capacity_error(error)
        no_originals_or_attempt_directories(tmp_path)
    else:
        _, key, size, _, _ = upload(storage, Chunks(b'x'))
        assert size == 1 and (tmp_path / key).read_bytes() == b'x'
    assert len(descriptors) == 1


class FileProxy:
    """Target-only wrapper, preserving the real descriptor and actual writes."""

    def __init__(self, stream, *, failure=None, short_first=False):
        self.stream, self.failure, self.short_first = stream, failure, short_first
        self.writes = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    def fileno(self):
        return self.stream.fileno()

    def write(self, data):
        self.writes += 1
        if self.failure == 'write':
            self.stream.write(data[:2])
            raise OSError(errno.ENOSPC, 'synthetic full filesystem')
        if self.short_first and self.writes == 1:
            return self.stream.write(data[:2])
        return self.stream.write(data)

    def close(self):
        self.stream.close()
        if self.failure == 'close':
            self.failure = None
            raise OSError(errno.ENOSPC, 'synthetic close failure')


def wrap_target_open(monkeypatch, *, failure=None, short_first=False):
    original = builtins.open
    created = []
    def injected(file, mode='r', *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).name == 'original' and mode == 'xb':
            assert kwargs.get('buffering') == 0, 'Capacity checks require actual unbuffered writes'
            if failure == 'open':
                raise OSError(errno.ENOSPC, 'synthetic open failure')
            proxy = FileProxy(original(file, mode, *args, **kwargs), failure=failure, short_first=short_first)
            created.append(proxy)
            return proxy
        return original(file, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', injected)
    return created


@pytest.mark.parametrize('stage', ['mkdir', 'open', 'write', 'close'])
def test_enospc_maps_to_507_and_removes_only_new_attempt(tmp_path, monkeypatch, stage):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    retained = tmp_path / 'retained-original'
    retained.write_bytes(b'existing data must survive')
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(RESERVE + 100))
    monkeypatch.setattr(storage_module.os, 'fstatvfs', lambda _: measured(RESERVE + 100))
    source = Chunks(b'synthetic partial bytes')
    if stage == 'mkdir':
        original = Path.mkdir
        def full_directory(path, *args, **kwargs):
            if path.parent.name == 'assets':
                raise OSError(errno.ENOSPC, 'synthetic mkdir failure')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'mkdir', full_directory)
    else:
        wrap_target_open(monkeypatch, failure=stage)
    with pytest.raises(HTTPException) as error:
        upload(storage, source)
    assert_capacity_error(error)
    assert retained.read_bytes() == b'existing data must survive'
    if stage in ('mkdir', 'open'):
        assert source.reads == 0
    no_originals_or_attempt_directories(tmp_path)


def test_short_write_rechecks_real_descriptor_then_finishes_exact_payload(tmp_path, monkeypatch):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    payload = b'one complete synthetic chunk'
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(RESERVE + len(payload)))
    offsets = []
    def at_file(fd):
        offset = os.lseek(fd, 0, os.SEEK_CUR)
        offsets.append(offset)
        assert os.fstat(fd).st_size == offset
        return measured(RESERVE + len(payload) - offset)
    monkeypatch.setattr(storage_module.os, 'fstatvfs', at_file)
    proxies = wrap_target_open(monkeypatch, short_first=True)
    _, key, size, digest, _ = upload(storage, Chunks(payload))
    assert offsets == [0, 2]
    assert len(proxies) == 1 and proxies[0].writes == 2
    assert (tmp_path / key).read_bytes() == payload
    assert size == len(payload) and digest == hashlib.sha256(payload).hexdigest()


def test_uuid_directory_collision_does_not_remove_existing_original(tmp_path, monkeypatch):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    aid = '0c63717c-e9c5-45fb-90a2-57968be32b78'
    original = tmp_path / 'projects' / 'synthetic-project' / 'assets' / aid / 'original'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'retained existing original')
    monkeypatch.setattr(storage_module.uuid, 'uuid4', lambda: aid)
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(RESERVE + 100))
    source = Chunks(b'must not replace existing original')
    with pytest.raises(FileExistsError):
        upload(storage, source)
    assert source.reads == 0
    assert original.read_bytes() == b'retained existing original'
    assert list(original.parent.iterdir()) == [original]


@pytest.mark.parametrize('where', ['root', 'file'])
@pytest.mark.parametrize('number,status,code', [
    (errno.EIO, 503, 'storage_capacity_unavailable'),
    (errno.EACCES, 503, 'storage_capacity_unavailable'),
    (errno.ENOSPC, 507, 'storage_capacity_low'),
])
def test_measurement_oserror_fails_closed(tmp_path, monkeypatch, where, number, status, code):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(RESERVE + 100))
    monkeypatch.setattr(storage_module.os, 'fstatvfs', lambda _: measured(RESERVE + 100))
    def failure(_):
        raise OSError(number, 'synthetic capacity measurement failure')
    monkeypatch.setattr(storage_module.os, 'statvfs' if where == 'root' else 'fstatvfs', failure)
    source = Chunks(b'not accepted')
    with pytest.raises(HTTPException) as error:
        upload(storage, source)
    assert_capacity_error(error, status, code)
    if where == 'root':
        assert source.reads == 0 and list(tmp_path.iterdir()) == []
    no_originals_or_attempt_directories(tmp_path)


@pytest.mark.parametrize('where', ['root', 'file'])
@pytest.mark.parametrize('value', [
    SimpleNamespace(f_bavail=-1, f_frsize=4096),
    SimpleNamespace(f_bavail=True, f_frsize=4096),
    SimpleNamespace(f_bavail=1.5, f_frsize=4096),
    SimpleNamespace(f_bavail=10, f_frsize=0),
    SimpleNamespace(f_bavail=10, f_frsize=-1),
    SimpleNamespace(f_bavail=10, f_frsize=True),
    SimpleNamespace(f_bavail=10, f_frsize='4096'),
    SimpleNamespace(),
    None,
])
def test_invalid_measurement_never_silently_disables_guard(tmp_path, monkeypatch, where, value):
    storage = Storage(tmp_path, min_free_bytes=0)
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(100))
    monkeypatch.setattr(storage_module.os, 'fstatvfs', lambda _: measured(100))
    monkeypatch.setattr(storage_module.os, 'statvfs' if where == 'root' else 'fstatvfs', lambda _: value)
    source = Chunks(b'x')
    with pytest.raises(HTTPException) as error:
        upload(storage, source)
    assert_capacity_error(error, 503, 'storage_capacity_unavailable')
    if where == 'root':
        assert source.reads == 0 and list(tmp_path.iterdir()) == []
    no_originals_or_attempt_directories(tmp_path)


@pytest.mark.parametrize('value', [-1, True, False, 1.5, '4096', None])
def test_storage_and_settings_reject_invalid_reserve_before_creating_root(tmp_path, value):
    root = tmp_path / 'must-not-be-created'
    with pytest.raises(ValueError):
        Storage(root, min_free_bytes=value)
    assert not root.exists()
    with pytest.raises(ValueError):
        Settings('sqlite://', str(root), upload_min_free_bytes=value)


def test_existing_blob_can_be_read_and_removed_when_capacity_is_unavailable(tmp_path, monkeypatch):
    storage = Storage(tmp_path, min_free_bytes=RESERVE)
    monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(RESERVE + 100))
    monkeypatch.setattr(storage_module.os, 'fstatvfs', lambda _: measured(RESERVE + 100))
    _, key, size, _, _ = upload(storage, Chunks(b'existing source'))
    def must_not_measure(_):
        pytest.fail('Only new uploads should require a capacity measurement')
    monkeypatch.setattr(storage_module.os, 'statvfs', must_not_measure)
    monkeypatch.setattr(storage_module.os, 'fstatvfs', must_not_measure)
    with storage.open(key, size) as source:
        assert source.read() == b'existing source'
    storage.remove_upload(key)
    assert not (tmp_path / key).exists()


def settings_environment(monkeypatch, root):
    monkeypatch.setenv('DATABASE_URL', 'sqlite://')
    monkeypatch.setenv('DATA_ROOT', str(root))


def test_settings_environment_uses_documented_default_and_explicit_nonnegative_override(tmp_path, monkeypatch):
    settings_environment(monkeypatch, tmp_path)
    monkeypatch.delenv('PPT_UPLOAD_MIN_FREE_BYTES', raising=False)
    assert Settings.env().upload_min_free_bytes == DEFAULT_UPLOAD_MIN_FREE_BYTES == 128 * 1024**2
    for raw, wanted in [('0', 0), ('8192', 8192)]:
        monkeypatch.setenv('PPT_UPLOAD_MIN_FREE_BYTES', raw)
        assert Settings.env().upload_min_free_bytes == wanted


@pytest.mark.parametrize('raw', ['-1', '', 'true', '1.5', 'not-an-integer'])
def test_invalid_environment_reserve_fails_configuration(tmp_path, monkeypatch, raw):
    settings_environment(monkeypatch, tmp_path)
    monkeypatch.setenv('PPT_UPLOAD_MIN_FREE_BYTES', raw)
    with pytest.raises(ValueError):
        Settings.env()


def test_http_app_passes_configured_reserve_and_rejection_leaves_no_asset_row(tmp_path, monkeypatch):
    """In-process synthetic identity/SQLite, without calling authentication/SSO."""
    reserve = 37 * 1024
    root = tmp_path / 'data'
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    try:
        migrate(engine)
        app = create_app(Settings('sqlite://', str(root), upload_min_free_bytes=reserve), engine)
        app.dependency_overrides[authenticate] = lambda: Identity('https://capacity-fixture.invalid', 'synthetic-user')
        free = [reserve - 1]
        monkeypatch.setattr(storage_module.os, 'statvfs', lambda _: measured(free[0]))
        payload = b'# Synthetic capacity wiring fixture\n'
        monkeypatch.setattr(storage_module.os, 'fstatvfs', lambda _: measured(reserve + len(payload)))
        with TestClient(app) as client:
            created = client.post('/api/v1/projects', json={'title': 'Capacity fixture', 'external_model_ack': True})
            assert created.status_code == 201
            project_id = created.json()['id']
            endpoint = f'/api/v1/projects/{project_id}/assets'
            rejected = client.post(endpoint, files={'file': ('synthetic.md', payload, 'text/markdown')})
            assert rejected.status_code == 507
            assert rejected.json() == {'detail': {'code': 'storage_capacity_low'}}
            assert not (root / 'projects').exists()
            with app.state.sessions() as session:
                assert session.scalar(select(func.count(Asset.id))) == 0
            free[0] = reserve
            accepted = client.post(endpoint, files={'file': ('synthetic.md', payload, 'text/markdown')})
            assert accepted.status_code == 201
            assert accepted.json()['bytes'] == len(payload)
            with app.state.sessions() as session:
                assert session.scalar(select(func.count(Asset.id))) == 1
    finally:
        engine.dispose()
