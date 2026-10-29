"""Production packaging allowlists and exact stage cutover capability checks."""
import tarfile

import pytest

from scientific_agent import build_images, deploy_workbench


def write(root, name, content='fixture'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def test_api_build_context_excludes_private_skill_and_operator_files(tmp_path, monkeypatch):
    root = tmp_path / 'repository'
    for name in ('Dockerfile.scientific-pi-api', 'app/__init__.py', 'pi_worker/__init__.py',
        'scientific_agent/requirements-api.lock', 'scientific_agent/workspace.py',
        'app/api/scientific_workspace.py', 'tools/sqlite_client.py',
        *('scientific_skills/' + name for name in build_images.SKILL_FILES)):
        write(root, name)
    for name in ('scientific_skills/.env', 'scientific_skills/model-secrets.json',
                 'scientific_skills/notes/private.md', 'scientific_agent/rendered/secret.json',
                 'scientific_agent/private.db', 'tools/.env'):
        write(root, name, 'private fixture must not enter context')
    monkeypatch.setattr(build_images, 'ROOT', root)
    target = tmp_path / 'api.tar'
    build_images.build_context('api', target)
    with tarfile.open(target) as archive:
        names = set(archive.getnames())
    assert {name for name in names if name.startswith('scientific_skills/')} == {
        'scientific_skills/' + name for name in build_images.SKILL_FILES}
    assert 'scientific_agent/workspace.py' in names and 'app/api/scientific_workspace.py' in names
    assert not any('private' in name or 'secret' in name or '.env' in name for name in names)


def test_ui_build_context_uses_reviewed_root_files_and_source_code_only(tmp_path, monkeypatch):
    root = tmp_path / 'repository'
    for name in ('Dockerfile.scientific-workbench', 'scientific_agent/__init__.py',
        'scientific_agent/workbench_server.py', 'scientific_agent/requirements-api.lock',
        *('scientific_agent/web/' + name for name in build_images.UI_FILES),
        'scientific_agent/web/src/App.tsx', 'scientific_agent/web/src/styles.css'):
        write(root, name)
    for name in ('scientific_agent/web/operator-keys.json', 'scientific_agent/web/docs/private.json',
                 'scientific_agent/web/src/runtime-secrets.json', 'scientific_agent/web/.env',
                 'scientific_agent/web/node_modules/package/index.ts',
                 'scientific_agent/web/dist/generated.js'):
        write(root, name, 'private fixture must not enter context')
    monkeypatch.setattr(build_images, 'ROOT', root)
    target = tmp_path / 'ui.tar'
    build_images.build_context('ui', target)
    with tarfile.open(target) as archive:
        names = set(archive.getnames())
    assert 'scientific_agent/web/package-lock.json' in names
    assert 'scientific_agent/web/src/App.tsx' in names
    assert not any('secret' in name or 'private' in name or 'node_modules' in name
                   or 'operator' in name or '.env' in name or '/dist/' in name for name in names)


def capabilities():
    return [dict(profile='research.' + stage + '.v1', profile_revision='v1', ready=True,
                 supports_inputs=True, max_timeout_seconds=3600)
            for stage in ('plan', 'retrieve', 'outline', 'draft', 'revise')]


def check(rows):
    namespace = {'p': rows}
    exec(compile(deploy_workbench.readiness_assertion(), '<deployment-readiness>', 'exec'), namespace)


@pytest.mark.parametrize('changes', [dict(profile_revision='v2'), dict(ready=False),
    dict(supports_inputs=False), dict(max_timeout_seconds=1799), dict(max_timeout_seconds=True)])
def test_cutover_rejects_wrong_revision_and_unusable_current_stage(changes):
    rows = capabilities()
    rows[2].update(changes)
    with pytest.raises(AssertionError, match='Scientific v1 stages unavailable'):
        check(rows)


def test_cutover_requires_every_exact_current_stage_but_allows_parallel_old_revisions():
    rows = capabilities()
    check(rows + [dict(rows[2], profile_revision='v2')])
    with pytest.raises(AssertionError):
        check(rows[:-1])
