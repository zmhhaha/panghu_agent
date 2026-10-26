"""Bounded binary snapshots after host stop evidence; manifests grant no authority."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata

from .contracts import MAX_ARTIFACT_BYTES, InlineFile, ServiceError, digest


def relative_path(value: str) -> str:
    """One portable, canonical relative path, without filesystem interpretation."""
    if (type(value) is not str or not 1 <= len(value.encode('utf-8')) <= 1024
            or value != unicodedata.normalize('NFC', value) or '\\' in value
            or ':' in value or value.startswith('/') or value.endswith('/')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ServiceError('invalid_file_path', 422)
    parts = value.split('/')
    reserved = re.compile(r'(?i)^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$')
    if (len(parts) > 32 or any(not p or p in {'.', '..'} or p.endswith((' ', '.'))
                             or len(p.encode('utf-8')) > 255 or reserved.fullmatch(p)
                             for p in parts)
            or str(PurePosixPath(value)) != value):
        raise ServiceError('invalid_file_path', 422)
    return value


def unique_paths(values):
    """Reject case aliases and file/directory collisions before writing anything."""
    seen = set()
    for value in values:
        path = relative_path(value)
        normalized = path.casefold()
        if normalized in seen:
            raise ServiceError('duplicate_file_path', 422)
        seen.add(normalized)
    for value in seen:
        parts = value.split('/')
        if any('/'.join(parts[:index]) in seen for index in range(1, len(parts))):
            raise ServiceError('file_path_collision', 422)


def _ordinary_tree(path):
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ServiceError('unsafe_artifact', 503)


def read_output(workspace: Path, name: str, *, limit=MAX_ARTIFACT_BYTES) -> bytes:
    """Read an exact ordinary file, never following links or special files."""
    workspace = Path(workspace).absolute()
    relative_path(name)
    if type(limit) is not int or not 0 <= limit <= MAX_ARTIFACT_BYTES:
        raise ServiceError('artifact_size_limit', 413)
    _ordinary_tree(workspace)
    path = workspace.joinpath(*name.split('/'))
    _ordinary_tree(path.parent)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or getattr(before, 'st_file_attributes', 0) & 0x400 or before.st_size > limit):
        raise ServiceError('unsafe_artifact', 503)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not os.path.samestat(before, opened)):
            raise ServiceError('unsafe_artifact', 503)
        result = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(result) > limit or len(result) != opened.st_size
            or opened.st_mtime_ns != after.st_mtime_ns or opened.st_size != after.st_size
            or opened.st_ctime_ns != after.st_ctime_ns):
        raise ServiceError('artifact_changed', 503)
    return result


def require_stop(proof, envelope):
    from .engines import StopEvidence
    if (type(proof) is not StopEvidence or proof.execution_id != envelope.attempt_id
            or proof.all_writers_stopped is not True or type(proof.resource_ids) is not tuple
            or not proof.resource_ids or len(set(proof.resource_ids)) != len(proof.resource_ids)):
        raise ServiceError('writers_not_stopped', 503)


def freeze_files(files, envelope, proof):
    """Materialize and hash collector output only after verified host stop."""
    require_stop(proof, envelope)
    max_files = getattr(envelope, 'max_artifact_files', 32)
    max_bytes = getattr(envelope, 'max_artifact_bytes', MAX_ARTIFACT_BYTES)
    max_file_bytes = getattr(envelope, 'max_artifact_file_bytes', max_bytes)
    if type(files) is not list or len(files) > max_files:
        raise ServiceError('artifact_size_limit', 413)
    unique_paths(file.name for file in files if isinstance(file, InlineFile))
    frozen, total = [], 0
    for candidate in files:
        if not isinstance(candidate, InlineFile):
            raise ServiceError('invalid_artifact', 422)
        content = candidate.content(limit=max_file_bytes)
        total += len(content)
        if total > max_bytes:
            raise ServiceError('artifact_size_limit', 413)
        frozen.append(InlineFile.from_bytes(candidate.name, content))
    return sorted(frozen, key=lambda file: file.name)


def frozen_manifest(job_id, attempt_id, files):
    entries = [{key: item[key] for key in ('artifact_id', 'name', 'sha256', 'size') if key in item}
               for item in sorted(files, key=lambda item: item['name'])]
    body = dict(schema_version='agent-artifacts.v1', job_id=job_id, attempt_id=attempt_id,
                files=entries, total_bytes=sum(item['size'] for item in entries),
                publication_authorized=False)
    return {**body, 'sha256': digest(body)}
