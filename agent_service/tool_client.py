"""Controller-only retrieval of observations from the task-authorized tool bridge."""
import json
import os
from pathlib import Path

import httpx

from .contracts import ServiceError, canonical_bytes


def save_academic_observations(base_url, token, workspace, *, max_bytes=16 * 1024 * 1024):
    target = Path(workspace) / 'result' / 'academic-observations.json'
    import stat
    for parent in (*reversed(target.parent.absolute().parents), target.parent.absolute()):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ServiceError('academic_observation_path_invalid', 503)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10,
                      transport=httpx.HTTPTransport(retries=0)) as client:
        with client.stream('GET', base_url.rstrip('/') + '/tools/observations',
                headers={'Authorization': 'Bearer ' + token}) as response:
            if response.status_code != 200:
                raise ServiceError('academic_observation_unavailable', 503)
            chunks, size = [], 0
            for chunk in response.iter_bytes(65536):
                size += len(chunk)
                if size > max_bytes:
                    raise ServiceError('academic_observation_limit', 503)
                chunks.append(chunk)
    value = json.loads(b''.join(chunks))
    raw = canonical_bytes(value)
    # Called only after all writers stop. A model-created file is replaced by
    # measured bridge data; links and aliased inodes are never followed.
    if target.exists() or target.is_symlink():
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ServiceError('academic_observation_path_invalid', 503)
        target.unlink()
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
