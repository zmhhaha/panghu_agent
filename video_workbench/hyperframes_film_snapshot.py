"""Bounded full-film transport, distinct from the seven-file motion snapshot.

Callers resolve owner/version authorization first. Verification requires the
external snapshot digest and execution-side release identities; self-reported
host hashes never authorize executable host bytes. No Job is submitted here.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import gzip
import hashlib
import io
import json
import re
import tarfile

from agent_service.contracts import ServiceError, canonical_bytes, digest, safe_relative_path
from .hyperframes_film_browser import HOST_NAMES, MAX_BUNDLE_BYTES, verify_film_browser_bundle
from .hyperframes_film_project import MAX_FILES as PROJECT_FILES, MAX_MODULES
from .motion_snapshot import _runtime

SCHEMA = 'video.hyperframes-film-execution-snapshot.v1'
TRANSPORT_SCHEMA = 'video.hyperframes-film-archive.v1'
MAX_FILES = PROJECT_FILES + len(HOST_NAMES) + 2 + MAX_MODULES + 1
MAX_RAW_BYTES = MAX_BUNDLE_BYTES + 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_BASE64_CHARS = 4 * ((MAX_ARCHIVE_BYTES + 2) // 3)
MAX_REQUEST_BYTES = MAX_BASE64_CHARS + MAX_METADATA_BYTES
MAX_TAR_BYTES = MAX_RAW_BYTES + MAX_FILES * 2048 + 10240
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'[A-Za-z0-9./:_-]+:latest@sha256:[0-9a-f]{64}\Z')
_RUN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')


def require(value, code='hyperframes_film_snapshot_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _checksum(value):
    require(type(value) is str and _SHA.fullmatch(value), 'hyperframes_film_snapshot_digest_invalid')


def _path(value):
    try:
        safe_relative_path(value)
    except (ValueError, TypeError):
        raise ServiceError('hyperframes_film_archive_path_invalid', 422) from None
    require(len(value) <= 240 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', value),
            'hyperframes_film_archive_path_invalid')


def _hosts(value):
    require(type(value) is dict and set(value) == HOST_NAMES,
            'hyperframes_film_trusted_hosts_required')
    for checksum in value.values():
        _checksum(checksum)
    return dict(value)


def _release(runtime_fingerprint, image, trusted_host_sha256):
    runtime = _runtime(runtime_fingerprint)
    require(runtime['schema'] == 'video.motion-runtime.v3'
        and type(image) is str and _IMAGE.fullmatch(image)
        and image.rsplit('@', 1)[1] == runtime['image_digest'],
        'hyperframes_film_snapshot_release_mismatch', 409)
    return runtime, _hosts(trusted_host_sha256)


def _bundle(files, checksum, runtime, hosts):
    require(type(files) is dict and 1 <= len(files) <= MAX_FILES,
            'hyperframes_film_archive_file_count')
    total, folded = 0, set()
    for name, raw in files.items():
        _path(name)
        require(name.casefold() not in folded, 'hyperframes_film_archive_path_collision')
        folded.add(name.casefold())
        require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES,
                'hyperframes_film_archive_file_budget')
        total += len(raw)
    require(total <= MAX_RAW_BYTES, 'hyperframes_film_archive_raw_budget', 413)
    bundle = verify_film_browser_bundle(files, expected_bundle_sha256=checksum)
    project = json.loads(files['project/manifest.json'])
    require(project['runtime_fingerprint'] == runtime,
            'hyperframes_film_snapshot_runtime_mismatch', 409)
    require(bundle['root_config']['host_files_sha256'] == hosts
        and all(sha(files['host/' + name]) == checksum for name, checksum in hosts.items()),
        'hyperframes_film_snapshot_host_mismatch', 409)
    # The first exporter has a deliberate short-film budget. This boundary must
    # not accept a longer package then silently crop or use per-scene concat.
    require(0 < project['clock']['frame_count'] <= 1440,
            'hyperframes_film_snapshot_duration_budget')
    require(bundle['root_config']['width'] % 2 == bundle['root_config']['height'] % 2 == 0,
            'hyperframes_film_snapshot_even_dimensions_required')
    return bundle, project


def pack_film_archive(files):
    """Deterministic gzip/tar, ordinary exact files only, never extractall()."""
    require(type(files) is dict and 1 <= len(files) <= MAX_FILES,
            'hyperframes_film_archive_file_count')
    target = io.BytesIO()
    total = 0
    with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.GNU_FORMAT) as archive:
            for name, raw in sorted(files.items()):
                _path(name)
                require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES,
                        'hyperframes_film_archive_file_budget')
                total += len(raw)
                require(total <= MAX_RAW_BYTES, 'hyperframes_film_archive_raw_budget', 413)
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), 0o444, 0
                archive.addfile(info, io.BytesIO(raw))
                require(target.tell() <= MAX_ARCHIVE_BYTES, 'hyperframes_film_archive_compressed_budget', 413)
    raw = target.getvalue()
    require(len(raw) <= MAX_ARCHIVE_BYTES, 'hyperframes_film_archive_compressed_budget', 413)
    return raw


class _BoundedTar:
    """Bound metadata reads too, before tarfile processes longname headers."""
    def __init__(self, stream):
        self.stream, self.remaining = stream, MAX_TAR_BYTES

    def read(self, size=-1):
        require(type(size) is int and 0 <= size <= self.remaining,
                'hyperframes_film_archive_tar_budget', 413)
        raw = self.stream.read(size)
        self.remaining -= len(raw)
        return raw


def unpack_film_archive(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_ARCHIVE_BYTES,
            'hyperframes_film_archive_compressed_budget', 413)
    files, folded, total = {}, set(), 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode='rb') as compressed:
            limited = _BoundedTar(compressed)
            with tarfile.open(fileobj=limited, mode='r|') as archive:
                for member in archive:
                    require(len(files) < MAX_FILES, 'hyperframes_film_archive_file_count')
                    _path(member.name)
                    require(member.name.casefold() not in folded,
                            'hyperframes_film_archive_path_collision')
                    require(member.type == tarfile.REGTYPE and member.isfile()
                        and not member.pax_headers and not member.sparse
                        and 0 < member.size <= MAX_FILE_BYTES,
                        'hyperframes_film_archive_member_invalid')
                    total += member.size
                    require(total <= MAX_RAW_BYTES, 'hyperframes_film_archive_raw_budget', 413)
                    stream = archive.extractfile(member)
                    require(stream is not None, 'hyperframes_film_archive_member_invalid')
                    value = stream.read(member.size + 1)
                    require(len(value) == member.size, 'hyperframes_film_archive_member_truncated')
                    files[member.name] = value
                    folded.add(member.name.casefold())
                # Drain tarfile's own read-ahead buffer as well as gzip. Reading
                # only the underlying stream would miss hidden bytes already
                # buffered after an end-of-archive block.
                while True:
                    rest = archive.fileobj.read(65536)
                    if not rest:
                        break
                    require(not any(rest), 'hyperframes_film_archive_trailing_data')
    except (tarfile.TarError, OSError, EOFError, ValueError, UnicodeError):
        raise ServiceError('hyperframes_film_archive_invalid', 422) from None
    require(bool(files), 'hyperframes_film_archive_file_count')
    return files


def _metadata(files, bundle, project, *, project_revision, production_run_id,
              image, runtime, hosts, timeout_seconds, archive):
    require(type(project_revision) is int and project_revision >= 1
        and type(production_run_id) is str and _RUN.fullmatch(production_run_id)
        and type(timeout_seconds) is int and 1 <= timeout_seconds <= 1200)
    return dict(schema=SCHEMA, project_id=project['film']['project_id'],
        project_revision=project_revision, production_run_id=production_run_id,
        image=image, runtime_fingerprint=runtime, trusted_host_sha256=hosts,
        bundle_sha256=sha(files['browser-manifest.json']),
        project_manifest_sha256=bundle['project_manifest_sha256'], film_sha256=project['film_sha256'],
        clock=deepcopy(project['clock']), width=bundle['root_config']['width'], height=bundle['root_config']['height'],
        module_sha256={row['module_id']: row['module_sha256'] for row in project['modules']},
        recording_sha256={row['path']: row['sha256'] for row in project['narration_tracks']},
        timeout_seconds=timeout_seconds,
        transport=dict(schema=TRANSPORT_SCHEMA, format='tar+gzip', sha256=sha(archive),
            bytes=len(archive), file_count=len(files), raw_bytes=sum(map(len, files.values()))),
        execution=dict(status='not_submitted', backend='isolated-full-film-pvc-job-1',
            evidence_scope='frozen_full_project_not_execution_or_adoption'))


def freeze_film_execution_snapshot(files, *, expected_bundle_sha256, runtime_fingerprint,
        trusted_host_sha256, image, project_revision, production_run_id, timeout_seconds=600):
    runtime, hosts = _release(runtime_fingerprint, image, trusted_host_sha256)
    bundle, project = _bundle(files, expected_bundle_sha256, runtime, hosts)
    archive = pack_film_archive(files)
    result = _metadata(files, bundle, project, project_revision=project_revision,
        production_run_id=production_run_id, image=image, runtime=runtime, hosts=hosts,
        timeout_seconds=timeout_seconds, archive=archive)
    require(len(canonical_bytes(result)) <= MAX_METADATA_BYTES,
            'hyperframes_film_snapshot_metadata_budget', 413)
    result['archive_base64'] = base64.b64encode(archive).decode('ascii')
    result['sha256'] = digest(result)
    require(len(canonical_bytes(result)) <= MAX_REQUEST_BYTES,
            'hyperframes_film_snapshot_request_budget', 413)
    return result


def decode_film_execution_snapshot(snapshot, *, expected_snapshot_sha256,
        runtime_fingerprint, trusted_host_sha256, image):
    """Validate external custody, then decompress and recompile exact inputs."""
    _checksum(expected_snapshot_sha256)
    runtime, hosts = _release(runtime_fingerprint, image, trusted_host_sha256)
    require(type(snapshot) is dict and snapshot.get('schema') == SCHEMA
        and type(snapshot.get('archive_base64')) is str
        and len(snapshot['archive_base64']) <= MAX_BASE64_CHARS,
        'hyperframes_film_snapshot_invalid')
    require(snapshot.get('sha256') == expected_snapshot_sha256
        and digest({key: value for key, value in snapshot.items() if key != 'sha256'}) == expected_snapshot_sha256,
        'hyperframes_film_snapshot_changed', 409)
    metadata = {key: value for key, value in snapshot.items() if key not in {'archive_base64', 'sha256'}}
    require(len(canonical_bytes(metadata)) <= MAX_METADATA_BYTES,
            'hyperframes_film_snapshot_metadata_budget', 413)
    require(metadata.get('image') == image and metadata.get('runtime_fingerprint') == runtime
        and metadata.get('trusted_host_sha256') == hosts,
        'hyperframes_film_snapshot_release_mismatch', 409)
    try:
        archive = base64.b64decode(snapshot['archive_base64'], validate=True)
    except (ValueError, binascii.Error):
        raise ServiceError('hyperframes_film_archive_base64_invalid', 422) from None
    transport = metadata.get('transport')
    require(type(transport) is dict and transport.get('bytes') == len(archive)
        and transport.get('sha256') == sha(archive), 'hyperframes_film_archive_changed', 409)
    files = unpack_film_archive(archive)
    bundle, project = _bundle(files, metadata.get('bundle_sha256'), runtime, hosts)
    try:
        expected = _metadata(files, bundle, project, project_revision=metadata['project_revision'],
            production_run_id=metadata['production_run_id'], image=image, runtime=runtime, hosts=hosts,
            timeout_seconds=metadata['timeout_seconds'], archive=archive)
    except (KeyError, ValueError, TypeError):
        raise ServiceError('hyperframes_film_snapshot_invalid', 422) from None
    require(metadata == expected, 'hyperframes_film_snapshot_metadata_changed', 409)
    return files


def verify_film_execution_snapshot(snapshot, **release):
    decode_film_execution_snapshot(snapshot, **release)
    return deepcopy(snapshot)
