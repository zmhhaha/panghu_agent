"""An unrelated donor or forged identity must not become an admitted Pi runtime."""
import hashlib
import json

import pytest

from agent_service import pi_cage


@pytest.fixture
def measured_files(monkeypatch):
    entry = b'// synthetic fixed entry, never executed\n'
    monkeypatch.setattr(pi_cage, 'PI_CAGE_ENTRY_SHA256', hashlib.sha256(entry).hexdigest())
    metadata = dict(schema_version=1, repository=pi_cage.PI_CAGE_REPOSITORY,
        revision=pi_cage.PI_CAGE_REVISION, package=pi_cage.PI_PACKAGE,
        version=pi_cage.PI_VERSION, entrypoint=pi_cage.PI_CLI)
    files = {pi_cage.PI_CAGE_METADATA: json.dumps(metadata).encode(),
        pi_cage.PI_CLI: entry, pi_cage.PI_PACKAGE_ROOT + '/package.json':
        json.dumps(dict(name=pi_cage.PI_PACKAGE, version=pi_cage.PI_VERSION)).encode()}
    monkeypatch.setattr(pi_cage, '_read', lambda path: files[path])
    return files


def test_measures_entry_package_and_exact_repository_commit(measured_files):
    identity = pi_cage.verify_runtime()
    assert identity['revision'] == pi_cage.PI_CAGE_REVISION
    assert identity['entrypoint'] == pi_cage.PI_CLI


@pytest.mark.parametrize('field,value', [('revision', 'f' * 40), ('revision', None),
    ('repository', 'https://github.com/unrelated/pi-cage'), ('version', '0.73.1'),
    ('entrypoint', '/opt/pi/cli.js'), ('schema_version', True)])
def test_rejects_relabelled_or_unpinned_donor(measured_files, field, value):
    data = json.loads(measured_files[pi_cage.PI_CAGE_METADATA])
    data[field] = value
    measured_files[pi_cage.PI_CAGE_METADATA] = json.dumps(data).encode()
    with pytest.raises(ValueError, match='pi_cage_source_unavailable'):
        pi_cage.verify_runtime()


@pytest.mark.parametrize('target', ['entry', 'package', 'duplicate_metadata'])
def test_metadata_cannot_hide_modified_installed_runtime(measured_files, target):
    if target == 'entry':
        measured_files[pi_cage.PI_CLI] += b'// modified\n'
    elif target == 'package':
        measured_files[pi_cage.PI_PACKAGE_ROOT + '/package.json'] = b'{"name":"old-package","version":"0.99.2"}'
    else:
        measured_files[pi_cage.PI_CAGE_METADATA] = measured_files[pi_cage.PI_CAGE_METADATA][:-1] + b',"revision":"duplicate"}'
    with pytest.raises(ValueError, match='pi_cage_source_unavailable'):
        pi_cage.verify_runtime()
