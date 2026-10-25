"""Read a fixed ordinary output only after the host has proved stop."""
import os
from pathlib import Path
import stat

from .contracts import MAX_ARTIFACT_BYTES, ServiceError, safe_name


def read_output(workspace: Path, name: str) -> bytes:
    workspace = Path(workspace).absolute()
    safe_name(name)
    for part in (*reversed(workspace.parents), workspace):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ServiceError('unsafe_artifact', 503)
    path = workspace / name
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_ARTIFACT_BYTES:
        raise ServiceError('unsafe_artifact', 503)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not os.path.samestat(before, opened):
            raise ServiceError('unsafe_artifact', 503)
        result = stream.read(MAX_ARTIFACT_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (len(result) > MAX_ARTIFACT_BYTES or len(result) != opened.st_size
            or opened.st_mtime_ns != after.st_mtime_ns or opened.st_size != after.st_size):
        raise ServiceError('artifact_changed', 503)
    return result
