"""Transport boundary fixtures; no browser, render, admission or adoption."""
import base64
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import hyperframes_film_snapshot as S
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
from video_workbench.hyperframes_film_project import compile_film_project
from test_hyperframes_film_project import inputs

ROOT = Path(__file__).resolve().parents[2]


def snapshot_fixture():
    value = inputs()
    project = compile_film_project(**value)
    hosts = {name: (ROOT / 'video_workbench/motion_renderer' / name).read_bytes() for name in HOST_NAMES}
    bundle = compile_film_browser_bundle(project,
        expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=640, height=360, host_files=hosts)
    release = dict(runtime_fingerprint=value['runtime_fingerprint'],
        trusted_host_sha256={name: S.sha(raw) for name, raw in hosts.items()},
        image='registry.internal/video-motion-renderer:latest@' + value['runtime_fingerprint']['image_digest'])
    snapshot = S.freeze_film_execution_snapshot(bundle,
        expected_bundle_sha256=S.sha(bundle['browser-manifest.json']),
        project_revision=4, production_run_id='production-run-1', **release)
    return snapshot, bundle, release


def resign(snapshot):
    snapshot['sha256'] = digest({key: value for key, value in snapshot.items() if key != 'sha256'})
    return snapshot


def tar_blob(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.GNU_FORMAT) as archive:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data)
            if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                info.linkname = '../../outside'
            archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return gzip.compress(output.getvalue())


def test_snapshot_roundtrip_preserves_many_modules_and_all_original_recordings():
    snapshot, bundle, release = snapshot_fixture()
    assert len(bundle) > 7 and len(snapshot['recording_sha256']) == 3
    assert snapshot['transport']['file_count'] == len(bundle)
    assert snapshot['transport']['raw_bytes'] == sum(map(len, bundle.values()))
    assert snapshot['clock']['frame_count'] == 56
    assert snapshot['execution']['status'] == 'not_submitted'
    assert S.decode_film_execution_snapshot(snapshot,
        expected_snapshot_sha256=snapshot['sha256'], **release) == bundle
    assert S.verify_film_execution_snapshot(snapshot,
        expected_snapshot_sha256=snapshot['sha256'], **release) == snapshot
    assert S.pack_film_archive(bundle) == S.pack_film_archive(dict(reversed(list(bundle.items()))))
    assert S.MAX_FILES == 568
    assert S.MAX_REQUEST_BYTES < 192 * 1024 * 1024


def test_external_snapshot_digest_and_precise_metadata_are_required():
    snapshot, _, release = snapshot_fixture()
    with pytest.raises(ServiceError, match='hyperframes_film_snapshot_changed'):
        S.decode_film_execution_snapshot(snapshot, expected_snapshot_sha256='b' * 64, **release)
    snapshot['recording_sha256']['project/fictional.wav'] = 'b' * 64
    resign(snapshot)
    with pytest.raises(ServiceError, match='hyperframes_film_snapshot_metadata_changed'):
        S.decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'], **release)


@pytest.mark.parametrize('kind', ['runtime', 'hosts', 'image'])
def test_execution_side_release_is_authoritative(kind):
    snapshot, _, release = snapshot_fixture()
    release = deepcopy(release)
    if kind == 'runtime':
        release['runtime_fingerprint']['browser'] = 'different-browser'
    elif kind == 'hosts':
        release['trusted_host_sha256']['hyperframes_film.mjs'] = 'b' * 64
    else:
        release['image'] = release['image'].replace(':latest@', ':version-1@')
    with pytest.raises(ServiceError, match='release_mismatch'):
        S.decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'], **release)


def test_resealed_archive_cannot_replace_original_wav_or_hide_extra_files():
    snapshot, bundle, release = snapshot_fixture()
    for mutate in ('wav', 'extra'):
        changed = dict(bundle)
        if mutate == 'wav':
            name = next(name for name in changed if name.endswith('.wav'))
            changed[name] = changed[name][:-1] + bytes([changed[name][-1] ^ 1])
        else:
            changed['project/undeclared.mjs'] = b'export const changed=true;'
        raw = S.pack_film_archive(changed)
        modified = deepcopy(snapshot)
        modified['archive_base64'] = base64.b64encode(raw).decode()
        modified['transport'].update(sha256=S.sha(raw), bytes=len(raw))
        resign(modified)
        with pytest.raises(ServiceError):
            S.decode_film_execution_snapshot(modified,
                expected_snapshot_sha256=modified['sha256'], **release)


@pytest.mark.parametrize('entry', [
    ('../escaped', tarfile.REGTYPE, b'a'),
    ('/absolute', tarfile.REGTYPE, b'a'),
    ('host\\escaped', tarfile.REGTYPE, b'a'),
    ('host/link', tarfile.SYMTYPE, b''),
    ('host/hardlink', tarfile.LNKTYPE, b''),
    ('host/pipe', tarfile.FIFOTYPE, b''),
    ('host/directory', tarfile.DIRTYPE, b''),
])
def test_archive_rejects_path_escape_links_and_nonordinary_entries(entry):
    with pytest.raises(ServiceError):
        S.unpack_film_archive(tar_blob([entry]))


@pytest.mark.parametrize('second', ['host/source.mjs', 'HOST/source.mjs'])
def test_archive_rejects_duplicate_or_case_colliding_paths(second):
    raw = tar_blob([('host/source.mjs', tarfile.REGTYPE, b'a'), (second, tarfile.REGTYPE, b'b')])
    with pytest.raises(ServiceError, match='path_collision'):
        S.unpack_film_archive(raw)


def test_archive_budgets_include_decompressed_tar_metadata(monkeypatch):
    snapshot, bundle, _ = snapshot_fixture()
    raw = base64.b64decode(snapshot['archive_base64'])
    monkeypatch.setattr(S, 'MAX_TAR_BYTES', 512)
    with pytest.raises(ServiceError, match='tar_budget'):
        S.unpack_film_archive(raw)
    monkeypatch.setattr(S, 'MAX_ARCHIVE_BYTES', 1)
    with pytest.raises(ServiceError, match='compressed_budget'):
        S.pack_film_archive(bundle)


def test_archive_rejects_hidden_data_in_tar_read_ahead_buffer():
    raw = tar_blob([('host/source.mjs', tarfile.REGTYPE, b'a')])
    uncompressed = gzip.decompress(raw)
    hidden = uncompressed[:-1] + b'X'
    with pytest.raises(ServiceError, match='trailing_data'):
        S.unpack_film_archive(gzip.compress(hidden))


def test_file_raw_and_count_budgets_apply_before_execution(monkeypatch):
    monkeypatch.setattr(S, 'MAX_FILE_BYTES', 1)
    with pytest.raises(ServiceError, match='file_budget'):
        S.pack_film_archive({'host/a': b'ab'})
    monkeypatch.setattr(S, 'MAX_FILE_BYTES', 1024)
    monkeypatch.setattr(S, 'MAX_RAW_BYTES', 1)
    with pytest.raises(ServiceError, match='raw_budget'):
        S.pack_film_archive({'host/a': b'ab'})
    monkeypatch.setattr(S, 'MAX_FILES', 1)
    with pytest.raises(ServiceError, match='file_count'):
        S.pack_film_archive({'host/a': b'a', 'host/b': b'b'})


def test_transport_sha_and_base64_cannot_be_changed_under_same_snapshot():
    snapshot, _, release = snapshot_fixture()
    snapshot['archive_base64'] = snapshot['archive_base64'][:-1] + '!'
    resign(snapshot)
    with pytest.raises(ServiceError, match='base64_invalid'):
        S.decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'], **release)


def test_untrusted_new_host_package_is_not_authorized_by_recompiling_it():
    snapshot, files, release = snapshot_fixture()
    project = {name[len('project/'):]: raw for name, raw in files.items() if name.startswith('project/')}
    hosts = {name: files['host/' + name] for name in HOST_NAMES}
    hosts['hyperframes_film.mjs'] += b'\n// different executable release\n'
    other = compile_film_browser_bundle(project,
        expected_project_manifest_sha256=S.sha(project['manifest.json']),
        width=640, height=360, host_files=hosts)
    with pytest.raises(ServiceError, match='host_mismatch'):
        S.freeze_film_execution_snapshot(other, expected_bundle_sha256=S.sha(other['browser-manifest.json']),
            project_revision=4, production_run_id='production-run-1', **release)
