"""Offline ownership model reproducing root without CAP_FOWNER.

Actual kernel ownership/mount acceptance remains a separate Linux image probe.
"""
import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_service.hermes_host import HermesHostDriver


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    for name in ('project/nested', '.home/skills', '.tmp', 'result',
                 'inputs', 'base', '.preview-home', '.preview-tmp'):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    for name in ('project/page.svg', 'project/nested/asset.txt', '.home/config.yaml',
                 'result/response.json', 'inputs/source.md', 'base/checkpoint.json',
                 '.preview-home/config.json', '.preview-tmp/scratch'):
        (workspace / name).write_text('offline fixture', encoding='utf-8')
    protected = ('inputs', 'base', '.preview-home', '.preview-tmp')
    paths = [workspace, *workspace.rglob('*')]
    owners = {path: 0 for path in paths}
    modes = {path: 0o700 if path.is_dir() else 0o600 for path in paths}
    operations = []

    def chown(path, uid, gid):
        path = Path(path)
        owners[path] = uid
        operations.append(('chown', path, uid, gid))

    def chmod(path, mode, **kwargs):
        path = Path(path)
        # Root can use CAP_CHOWN to reclaim a path, but has no CAP_FOWNER
        # to chmod an Agent-owned inode directly.
        if owners[path] != 0:
            raise PermissionError(errno.EPERM, 'offline root lacks CAP_FOWNER')
        modes[path] = mode
        operations.append(('chmod', path, mode))

    monkeypatch.setattr(os, 'chown', chown, raising=False)
    monkeypatch.setattr(Path, 'chmod', chmod)
    driver = HermesHostDriver.__new__(HermesHostDriver)
    driver.scope = SimpleNamespace(agent_uid=42001000)
    driver.profile = SimpleNamespace(shared_project=True, protected_paths=protected)
    return SimpleNamespace(driver=driver, root=workspace, owners=owners, modes=modes,
                           paths=paths, protected=protected, operations=operations)


@pytest.mark.parametrize('preview_already_owns_project', [False, True])
def test_shared_permissions_are_set_before_final_handoff_without_fowner(handoff, preview_already_owns_project):
    h = handoff
    project = h.root / 'project'
    if preview_already_owns_project:
        for path in h.paths:
            if path == h.root or path == project or project in path.parents:
                h.owners[path] = h.driver.scope.agent_uid
    h.driver._prepare_ownership(h.root)
    assert h.modes[h.root] == 0o710
    for path in h.paths:
        if path == project or project in path.parents:
            assert h.owners[path] == h.driver.scope.agent_uid
            assert h.modes[path] == (0o2770 if path.is_dir() else 0o660)
    # Private Agent HOME/result stay private; the preview cannot read config.
    assert h.modes[h.root / '.home'] == 0o700
    assert h.modes[h.root / '.home/config.yaml'] == 0o600
    assert h.modes[h.root / 'result'] == 0o700
    assert h.owners[h.root / '.home/config.yaml'] == h.driver.scope.agent_uid
    for name in h.protected:
        boundary = h.root / name
        assert not any(row[1] == boundary or boundary in row[1].parents for row in h.operations)
    for index, operation in enumerate(h.operations):
        if operation[0] == 'chmod':
            path = operation[1]
            assert h.operations[index - 1] == ('chown', path, 0, 0)
            assert h.operations[index + 1] == ('chown', path, h.driver.scope.agent_uid, h.driver.scope.agent_uid)


def test_research_handoff_does_not_add_shared_modes(handoff):
    h = handoff
    h.driver.profile.shared_project = False
    original = dict(h.modes)
    h.driver._prepare_ownership(h.root)
    assert h.modes == original
    assert not any(row[0] == 'chmod' for row in h.operations)
    assert h.owners[h.root / 'project/page.svg'] == h.driver.scope.agent_uid


def test_skill_directory_links_are_not_traversed_or_reowned(handoff, tmp_path):
    h = handoff
    resource = tmp_path / 'fixed-image-resources'
    resource.mkdir()
    (resource / 'SKILL.md').write_text('fixed skill', encoding='utf-8')
    link = h.root / '.home/skills/fixed-skill'
    try:
        link.symlink_to(resource, target_is_directory=True)
    except OSError:
        pytest.skip('Host cannot create directory symlinks')
    h.driver._prepare_ownership(h.root)
    assert not any(row[1] == resource or resource in row[1].parents or row[1] == link
                   for row in h.operations)
