"""Release provenance and immutable identity checks, without Docker or network."""
from pathlib import Path
import fnmatch
import shlex
import subprocess

import pytest

from video_workbench.deploy import build_creation_release as release


def test_renderer_copy_inputs_survive_its_exact_docker_context_allowlist():
    root = Path(__file__).resolve().parents[2] / 'video_workbench' / 'motion_renderer'
    rules = (root / '.dockerignore').read_text().splitlines()
    for line in (root / 'Dockerfile').read_text().splitlines():
        if not line.startswith('COPY ') or line.startswith('COPY --from='):
            continue
        for name in shlex.split(line)[1:-1]:
            included = True
            for rule in rules:
                if not rule or rule.startswith('#'):
                    continue
                negated = rule.startswith('!')
                if fnmatch.fnmatchcase(name, rule[1:] if negated else rule):
                    included = negated
            assert (root / name).is_file(), name
            assert included, f'Docker COPY input excluded by .dockerignore: {name}'


def git(root, *arguments):
    result = subprocess.run(['git', '-C', str(root), *arguments], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True)
    return result.stdout.decode().strip()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Build Test')
    git(root, 'config', 'user.email', 'build-test@example.invalid')
    git(root, 'config', 'core.autocrlf', 'false')
    (root / 'agent_service').mkdir()
    (root / 'video_workbench').mkdir()
    (root / 'agent_service' / 'entry.py').write_text('identity = 1\n', encoding='utf-8')
    (root / 'video_workbench' / 'scene.py').write_text('duration = 8\n', encoding='utf-8')
    git(root, 'add', '--', 'agent_service', 'video_workbench')
    git(root, '-c', 'gc.auto=0', 'commit', '-qm', 'fixture')
    return root, git(root, 'rev-parse', 'HEAD')


def test_measures_clean_committed_application_sources_repeatably(checkout):
    root, revision = checkout
    result = release.source_measurement(root, revision)
    assert result == release.source_measurement(root, revision)
    assert result['revision'] == revision and result['source_git_clean']
    assert {row['path'] for row in result['source_files']} == {
        'agent_service/entry.py', 'video_workbench/scene.py'}
    assert all(row['bytes'] > 0 and len(row['sha256']) == 64 for row in result['source_files'])


@pytest.mark.parametrize('change', ['tracked-edit', 'new-source', 'staged-edit'])
def test_rejects_dirty_or_uncommitted_build_inputs(checkout, change):
    root, revision = checkout
    if change == 'new-source':
        (root / 'video_workbench' / 'uncommitted.py').write_text('uncommitted = True\n')
    else:
        (root / 'agent_service' / 'entry.py').write_text('identity = 2\n')
        if change == 'staged-edit':
            git(root, 'add', '--', 'agent_service/entry.py')
    with pytest.raises(RuntimeError, match='clean_application_source_required'):
        release.source_measurement(root, revision)


def test_rejects_revision_different_from_checked_out_commit(checkout):
    root, _ = checkout
    with pytest.raises(RuntimeError, match='source_revision_mismatch'):
        release.source_measurement(root, 'a' * 40)


def image_info(revision, context='c' * 64, identity='sha256:' + '1' * 64):
    return {'Id': identity, 'Config': {'Labels': {
        'org.opencontainers.image.revision': revision,
        'io.panghu.source.context-sha256': context}}}


def test_receipt_binds_pushed_digest_to_revision_and_context_labels(monkeypatch, tmp_path):
    revision, context = 'a' * 40, 'c' * 64
    dockerfile = tmp_path / 'agent_service' / 'Dockerfile.control'
    dockerfile.parent.mkdir()
    dockerfile.write_text('FROM test@sha256:' + '1' * 64 + '\n')
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'inspected', lambda _: image_info(revision, context))
    image = 'registry.example/agent-service-control:latest@sha256:' + '2' * 64
    result = release.receipt('control', image,
        {'revision': revision, 'source_files_manifest_sha256': 'b' * 64},
        'agent_service/Dockerfile.control', context_sha256=context)
    assert result['image'] == image and result['built_image_id'] == 'sha256:' + '1' * 64
    assert result['revision_label_verified'] and result['source_context_sha256'] == context
    assert result['pushed'] and result['source_git_clean']
    assert result['model_requests'] == result['deployments'] == 0


@pytest.mark.parametrize('wrong', ['revision', 'context'])
def test_receipt_rejects_image_labels_from_another_build(monkeypatch, tmp_path, wrong):
    revision, context = 'a' * 40, 'c' * 64
    info = image_info('d' * 40 if wrong == 'revision' else revision,
                      'd' * 64 if wrong == 'context' else context)
    monkeypatch.setattr(release, 'inspected', lambda _: info)
    with pytest.raises(RuntimeError, match='image_' + wrong + '_label_mismatch'):
        release.receipt('worker', 'registry.example/agent-service-pi:latest@sha256:' + '2' * 64,
            {'revision': revision, 'source_files_manifest_sha256': 'b' * 64},
            'agent_service/Dockerfile.agent-pi', context_sha256=context)


def test_receipt_requires_pushed_latest_digest_before_inspection(monkeypatch):
    def unexpected(_):
        pytest.fail('An unpinned image must be rejected before Docker inspection')
    monkeypatch.setattr(release, 'inspected', unexpected)
    with pytest.raises(RuntimeError, match='pushed_latest_digest_required'):
        release.receipt('worker', 'registry.example/agent-service-pi:latest',
            {'revision': 'a' * 40, 'source_files_manifest_sha256': 'b' * 64}, 'unused')


def test_release_rejects_executor_latest_retag_between_build_and_push(monkeypatch, tmp_path):
    from agent_service import build_image

    revision = 'a' * 40
    source = {'revision': revision, 'source_files_manifest_sha256': 'b' * 64,
              'source_files': [], 'source_git_clean': True}
    for name in ('agent_service/Dockerfile.control', 'agent_service/Dockerfile.agent-pi',
                 release.EXECUTOR_DOCKERFILE):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('FROM placeholder\n')
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setenv('PI_CAGE_IMAGE', 'registry.example/pi-cage:latest@sha256:' + '4' * 64)
    monkeypatch.setenv('PPT_DONOR_IMAGE', 'registry.example/ppt-runtime:latest@sha256:' + '5' * 64)
    monkeypatch.setattr(release, 'source_measurement', lambda *_: source)
    monkeypatch.setattr(build_image, 'build', lambda role, **_: (
        'registry.example/agent-service-' + role + ':latest@sha256:' + '2' * 64))
    monkeypatch.setattr(release, 'command', lambda *_, **__: b'')
    executor_reads = 0

    def inspect(image):
        nonlocal executor_reads
        info = image_info(revision)
        if image == 'registry.example/video-animation-creation:latest':
            executor_reads += 1
            if executor_reads > 1:
                info['Id'] = 'sha256:' + '3' * 64
        return info

    monkeypatch.setattr(release, 'inspected', inspect)
    from types import SimpleNamespace
    args = SimpleNamespace(revision=revision, output=tmp_path / 'release', registry='registry.example')
    with pytest.raises(RuntimeError, match='executor_latest_identity_changed'):
        release.build(args)
    assert not (args.output / 'executor-build.json').exists()
    assert not (args.output / 'build-completed.json').exists()


@pytest.mark.parametrize('missing', ['PI_CAGE_IMAGE', 'PPT_DONOR_IMAGE'])
def test_missing_worker_dependency_pin_fails_before_any_build(monkeypatch, tmp_path, missing):
    from agent_service import build_image
    from types import SimpleNamespace

    revision = 'a' * 40
    source = {'revision': revision, 'source_files_manifest_sha256': 'b' * 64,
              'source_files': [], 'source_git_clean': True}
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'source_measurement', lambda *_: source)
    monkeypatch.setenv('PI_CAGE_IMAGE', 'registry.example/pi-cage:latest@sha256:' + '4' * 64)
    monkeypatch.setenv('PPT_DONOR_IMAGE', 'registry.example/ppt-runtime:latest@sha256:' + '5' * 64)
    monkeypatch.delenv(missing)

    def unexpected(*_, **__):
        pytest.fail('Missing worker dependency pin must stop before Docker or Agent build')

    monkeypatch.setattr(build_image, 'build', unexpected)
    monkeypatch.setattr(release, 'command', unexpected)
    monkeypatch.setattr(release, 'inspected', unexpected)
    args = SimpleNamespace(revision=revision, output=tmp_path / 'release', registry='registry.example')
    with pytest.raises(build_image.BuildError, match='immutable_base_required'):
        release.build(args)
    assert not list(args.output.glob('*-build.log'))
    assert not (args.output / 'build-completed.json').exists()
