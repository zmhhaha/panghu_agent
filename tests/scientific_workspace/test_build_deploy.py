"""Production packaging allowlists and exact stage cutover capability checks."""
from pathlib import PurePosixPath
import shlex
import subprocess
import sys
import tarfile
import textwrap

import pytest

from scientific_agent import build_images, deploy_workbench


def test_workbench_update_removes_retired_worker_secret_and_keeps_agent_caller():
    image = 'example.invalid/scientific@sha256:' + 'a' * 64
    api, ui, policies = deploy_workbench.resources('scientific-agent', image, image)
    pod = api['spec']['template']['spec']
    container = pod['containers'][0]
    retired = {'SCIENTIFIC_PI_WORKER_URL', 'SCIENTIFIC_PI_WORKER_TOKEN',
               'SCIENTIFIC_PI_RUNTIME_REVISION', 'SCIENTIFIC_PI_SKILL_BUNDLE_REVISION'}
    assert {entry['name'] for entry in container['env']
            if entry.get('$patch') == 'delete'} >= retired
    # A valueFrom on the obsolete Secret would leave the new API unable to
    # start after retirement, even though no worker Deployment is rendered.
    assert not any('valueFrom' in entry for entry in container['env'])
    assert [entry['secret']['secretName'] for entry in pod['volumes']
            if 'secret' in entry] == ['scientific-agent-service']
    assert not any(item['kind'] in {'Deployment', 'PersistentVolumeClaim'}
                   for item in policies['items'])
    assert all(item['kind'] == 'NetworkPolicy' for item in policies['items'])
    assert ui['spec']['template']['spec']['containers'][0]['command'][3] == \
        'scientific_agent.workbench_server:app'


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


def test_api_docker_copy_packages_working_business_projections(tmp_path):
    # A source can be present in the tar and still absent from the image when
    # the production Dockerfile uses an explicit COPY allowlist.
    target = tmp_path / 'api.tar'
    app_root = tmp_path / 'app'
    build_images.build_context('api', target)
    with tarfile.open(target) as archive:
        names = set(archive.getnames())
        dockerfile = archive.extractfile(build_images.DOCKERFILES['api']).read().decode('utf-8')
        for line in dockerfile.replace('\\\n', ' ').splitlines():
            arguments = shlex.split(line, comments=True)
            if not arguments or arguments[0].upper() != 'COPY':
                continue
            # Keep this simulation explicit if the Dockerfile changes COPY
            # forms; silently skipping a new form could mask missing modules.
            assert len(arguments) >= 3 and not arguments[1].startswith(('--', '['))
            sources, destination = arguments[1:-1], arguments[-1]
            for source in sources:
                if not source.endswith('.py'):
                    continue
                assert source in names, f'Docker COPY source missing from API build context: {source}'
                copied = PurePosixPath(destination)
                if destination.endswith('/') or len(sources) > 1:
                    copied /= PurePosixPath(source).name
                relative = copied.relative_to('/app')
                installed = app_root.joinpath(*relative.parts)
                installed.parent.mkdir(parents=True, exist_ok=True)
                installed.write_bytes(archive.extractfile(source).read())

    # The isolated interpreter cannot fall back to the checkout for an omitted
    # relative import. Invoke the public projection, not just its import: its
    # dependencies are loaded lazily when project responses are assembled.
    result = subprocess.run([sys.executable, '-I', '-B', '-c', textwrap.dedent('''
        from pathlib import Path
        import sys
        root = Path(sys.argv[1])
        sys.path.insert(0, str(root))
        from scientific_agent import source_quality
        assert Path(source_quality.__file__).is_relative_to(root)
        planned = '"AutoGen" AND ("multi-agent" OR "agent framework" OR "AgentChat")'
        project = dict(plan=dict(content=dict(search_queries=[planned])), selections=[],
            search_batches=[dict(batch_id='packaging-smoke', papers=[], searches=[
                dict(source='arxiv', query='AutoGen', result_count=4, status='success')])])
        summary = source_quality.retrieval_summary(project)
        outcome, = summary['query_outcomes']
        assert outcome['planned_query'] == planned
        assert outcome['status'] == 'relaxed_result'
        assert outcome['matched_searches'] == [dict(query='AutoGen', provider='arxiv',
            result_count=4, batch_id='packaging-smoke', match='named_entity')]
        assert summary['topics_without_evidence'] == []
        # The image now constructs only the neutral service client, without
        # initializing an archive or reviving a former Worker connection.
        import os
        os.environ['SCIENTIFIC_EXECUTION_BACKEND'] = 'agent-service'
        os.environ['SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE'] = ''
        from app.api.scientific_agent import app
        from scientific_agent.agent_client import ScientificAgentClient
        assert isinstance(app.state.worker, ScientificAgentClient)
        assert not hasattr(app.state.worker, 'legacy_worker')
    '''), str(app_root)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


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
