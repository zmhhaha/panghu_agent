"""Reviewed pi-cage source identity shared by build admission and Worker health."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat


PI_CAGE_REPOSITORY = 'https://github.com/zmhhaha/pi-cage'
PI_CAGE_REVISION = 'e0262e65e43c6924164e995394f715dc5d19c210'
PI_PACKAGE = '@earendil-works/pi-coding-agent'
PI_VERSION = '0.99.2'
PI_CLI = '/usr/local/lib/pi-cage/worker-cli.mjs'
PI_CAGE_METADATA = '/usr/local/lib/pi-cage/runtime.json'
PI_PACKAGE_ROOT = '/usr/local/lib/node_modules/' + PI_PACKAGE
PI_CAGE_ENTRY_SHA256 = '97803d374556bee2db823b219fe1ce2264cd1404c08346f9c6c21d5e99af9c55'
ACADEMIC_EXTENSION = PI_PACKAGE_ROOT + '/agent-service-academic.mjs'


def _read(path, limit=65536):
    path = Path(path)
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or (os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o022))):
            raise ValueError('pi_cage_source_unavailable')
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError('pi_cage_source_unavailable')
    with path.open('rb') as stream:
        if not os.path.samestat(info, os.fstat(stream.fileno())):
            raise ValueError('pi_cage_source_unavailable')
        raw = stream.read(limit + 1)
    if len(raw) != info.st_size or len(raw) > limit:
        raise ValueError('pi_cage_source_unavailable')
    return raw


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('pi_cage_source_unavailable')
        result[key] = value
    return result


def verify_runtime():
    """Measure image-owned files; a tag or a caller's claimed revision is insufficient."""
    expected = dict(schema_version=1, repository=PI_CAGE_REPOSITORY, revision=PI_CAGE_REVISION,
                    package=PI_PACKAGE, version=PI_VERSION, entrypoint=PI_CLI)
    try:
        metadata = json.loads(_read(PI_CAGE_METADATA), object_pairs_hook=_pairs)
        package = json.loads(_read(PI_PACKAGE_ROOT + '/package.json'), object_pairs_hook=_pairs)
        if (type(metadata) is not dict or metadata != expected
                or type(metadata.get('schema_version')) is not int
                or type(package) is not dict or package.get('name') != PI_PACKAGE
                or package.get('version') != PI_VERSION
                or hashlib.sha256(_read(PI_CLI)).hexdigest() != PI_CAGE_ENTRY_SHA256):
            raise ValueError('pi_cage_source_unavailable')
        return expected
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('pi_cage_source_unavailable') from exc


if __name__ == '__main__':
    print(json.dumps(verify_runtime(), sort_keys=True))
