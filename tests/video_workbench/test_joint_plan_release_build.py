"""Three-image release admission and receipts, without Docker or network."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_service import build_image
from video_workbench.deploy import build_joint_plan_release as release


@pytest.fixture
def build_env(monkeypatch, tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    for name in ('agent_service/Dockerfile.control', 'agent_service/Dockerfile.agent-pi',
                 'video_workbench/Dockerfile', 'video_workbench/web/app.mjs',
                 'scientific_agent/__init__.py'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('ordinary fixture source\n', encoding='utf-8')
    ignored = root / 'video_workbench' / 'ignored-runtime-secret.env'
    ignored.write_text('THIS_MUST_NOT_ENTER_THE_CONTEXT', encoding='utf-8')
    revision = 'a' * 40
    rows = [dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size,
                 sha256=release.base.sha(p.read_bytes()))
            for p in sorted(root.rglob('*')) if p.is_file() and p != ignored]
    source = dict(revision=revision, source_files_manifest_sha256=release.base.sha(release.base.canonical(rows)),
                  source_files=rows, source_git_clean=True)
    args = SimpleNamespace(revision=revision, output=tmp_path / 'output', registry='registry.example')
    monkeypatch.setattr(release.base, 'ROOT', root)
    monkeypatch.setattr(release.base, 'source_measurement', lambda *_: deepcopy(source))
    monkeypatch.setenv('PI_CAGE_IMAGE', 'registry.example/pi-cage:latest@sha256:' + '4' * 64)
    monkeypatch.setenv('PPT_DONOR_IMAGE', 'registry.example/ppt-runtime:latest@sha256:' + '5' * 64)
    calls, agents = [], []

    def agent_builder(target, **options):
        agents.append((target, options))
        return 'registry.example/agent-service-' + target + ':latest@sha256:' + '2' * 64

    def command(argv, **options):
        calls.append((argv, options))
        assert argv[0] == 'docker', 'No deployment or model command is allowed'
        return b''

    def inspected(image):
        context_rows = [row for row in rows if Path(row['path']).parts[0] in release.CONTEXT_ROOTS]
        context_sha = release.base.sha(release.base.canonical(context_rows))
        return dict(Id='sha256:' + '1' * 64, Architecture='arm64', Config=dict(Labels={
            'org.opencontainers.image.revision': revision,
            'io.panghu.source.context-sha256': context_sha if '/video-workbench:' in image else 'c' * 64}),
            RepoDigests=['registry.example/video-workbench@sha256:' + '3' * 64])

    monkeypatch.setattr(build_image, 'build', agent_builder)
    monkeypatch.setattr(release.base, 'command', command)
    monkeypatch.setattr(release.base, 'inspected', inspected)
    return SimpleNamespace(root=root, source=source, args=args, calls=calls, agents=agents,
                           inspected=inspected, ignored=ignored)


def test_builds_only_ordinary_control_worker_and_measured_workbench_latest(build_env):
    env = build_env
    result = release.build(env.args)
    assert set(result['images']) == {'control', 'worker', 'workbench'}
    assert [target for target, _ in env.agents] == ['control', 'pi']
    for target, options in env.agents:
        assert options['service'] == env.root / 'agent_service'
        assert options['environ']['SOURCE_REVISION'] == env.args.revision
        assert options['environ']['IMAGE_TAG'] == 'latest'
        assert options['environ']['PLATFORM'] == 'linux/arm64'
        assert options['environ']['IMAGE'] == f'registry.example/agent-service-{target}:latest'
    build, = [row for row in env.calls if row[0][1] == 'build']
    assert build[0][-3:] == ['-t', 'registry.example/video-workbench:latest', '.']
    assert build[1]['cwd'] == env.args.output / 'workbench-source'
    context_files = {p.relative_to(build[1]['cwd']).as_posix()
                     for p in build[1]['cwd'].rglob('*') if p.is_file()}
    assert context_files == {row['path'] for row in env.source['source_files']
                             if Path(row['path']).parts[0] in release.CONTEXT_ROOTS}
    assert not (build[1]['cwd'] / env.ignored.relative_to(env.root)).exists()
    assert not (build[1]['cwd'] / 'scientific_agent').exists()
    for role in result['images']:
        receipt = json.loads((env.args.output / (role + '-build.json')).read_bytes())
        assert receipt['role'] == role
        assert receipt['source_git_clean'] and receipt['revision_label_verified']
        assert receipt['source_context_sha256']
        assert receipt['source_files_manifest_sha256'] == env.source['source_files_manifest_sha256']
        assert receipt['image'].startswith('registry.example/') and ':latest@sha256:' in receipt['image']
        assert receipt['deployments'] == receipt['model_requests'] == 0
    assert json.loads((env.args.output / 'build-completed.json').read_bytes()) == result
    assert not any(token in str(env.calls) for token in ('executor', 'renderer', 'animation-lab', 'kubectl'))


@pytest.mark.parametrize('missing', ['PI_CAGE_IMAGE', 'PPT_DONOR_IMAGE'])
def test_missing_dependency_pin_stops_before_output_or_build(build_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(build_image.BuildError, match='immutable_base_required'):
        release.build(build_env.args)
    assert not build_env.args.output.exists()
    assert not build_env.agents and not build_env.calls


@pytest.mark.parametrize('invalid', ['relative-output', 'existing-output', 'short-revision', 'dirty-source', 'registry'])
def test_rejects_invalid_source_or_output_before_build(build_env, monkeypatch, invalid):
    env = build_env
    expected = 'new_release_output_required'
    if invalid == 'relative-output': env.args.output = Path('relative-release')
    elif invalid == 'existing-output': env.args.output.mkdir()
    elif invalid == 'short-revision': env.args.revision = 'abc123'; expected = 'full_git_revision_required'
    elif invalid == 'registry': env.args.registry = 'registry.example/../other'; expected = 'explicit_registry_required'
    else:
        def dirty(*_): raise RuntimeError('clean_application_source_required')
        monkeypatch.setattr(release.base, 'source_measurement', dirty)
        expected = 'clean_application_source_required'
    with pytest.raises(RuntimeError, match=expected): release.build(env.args)
    assert not env.agents and not env.calls


def test_changed_file_cannot_enter_measured_workbench_context(build_env):
    env = build_env
    (env.root / 'video_workbench/web/app.mjs').write_text('changed after admission\n')
    with pytest.raises(RuntimeError, match='source_changed_during_build'): release.build(env.args)
    assert not any(argv[1] == 'build' for argv, _ in env.calls)
    assert not (env.args.output / 'build-completed.json').exists()


@pytest.mark.parametrize('wrong', ['retag', 'architecture', 'digest', 'revision-label', 'context-label'])
def test_workbench_receipt_refuses_changed_or_unbound_image(build_env, monkeypatch, wrong):
    env = build_env
    reads = 0

    def inspected(image):
        nonlocal reads
        info = env.inspected(image)
        if '/video-workbench:' in image:
            reads += 1
            if wrong == 'retag' and reads > 1: info['Id'] = 'sha256:' + '9' * 64
            elif wrong == 'architecture': info['Architecture'] = 'amd64'
            elif wrong == 'digest': info['RepoDigests'] = []
            elif wrong == 'revision-label': info['Config']['Labels']['org.opencontainers.image.revision'] = 'd' * 40
            elif wrong == 'context-label': info['Config']['Labels']['io.panghu.source.context-sha256'] = 'd' * 64
        return info

    monkeypatch.setattr(release.base, 'inspected', inspected)
    with pytest.raises(RuntimeError): release.build(env.args)
    assert not (env.args.output / 'workbench-build.json').exists()
    assert not (env.args.output / 'build-completed.json').exists()


def test_source_must_stay_identical_until_all_receipts_complete(build_env, monkeypatch):
    env = build_env
    calls = 0

    def measurement(*_):
        nonlocal calls
        calls += 1
        source = deepcopy(env.source)
        if calls > 1: source['source_files_manifest_sha256'] = 'f' * 64
        return source

    monkeypatch.setattr(release.base, 'source_measurement', measurement)
    with pytest.raises(RuntimeError, match='source_changed_during_build'): release.build(env.args)
    assert not (env.args.output / 'build-completed.json').exists()
