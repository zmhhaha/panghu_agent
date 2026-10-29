"""Offline packaging, native/buildx digest verification and startup isolation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from video_workbench import build_image as builder


IMAGE = 'arm-cluster-master:5000/video-workbench:latest'
LOCAL_ID = 'sha256:' + 'a' * 64


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source tree'
    for name in builder.SOURCE_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((builder.ROOT / name).read_bytes())
    # All these remain outside the context, regardless of path or extension.
    for name in ('video_workbench/private.py', 'video_workbench/workbench.db',
                 'video_workbench/workbench.db-wal', 'video_workbench/.env',
                 'video_workbench/secrets.json', 'video_workbench/web/private.css',
                 'video_workbench/rendered/config.production.json',
                 'video_workbench/deploy/private.py', 'video_workbench/.runtime/state.py',
                 'agent_service/config.production.json', 'agent_service/private.py',
                 'agent_service/runtime/private.py', 'agent_service/.env',
                 'agent_service/resources/video-workbench-v1/private.py',
                 'tests/video_workbench/private.py'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'PRIVATE_STATE_MUST_NOT_ENTER_THE_CONTEXT')
    return root


def old_receipt(root):
    path = root / 'video_workbench/rendered/workbench-image.txt'
    path.parent.mkdir(exist_ok=True)
    path.write_text(IMAGE + '@sha256:' + '0' * 64 + '\n', encoding='utf-8')
    return path


class Docker:
    """One fully offline Docker executable and registry."""
    def __init__(self, *, native=False, fail=None, mismatch=None, arch='aarch64'):
        self.calls, self.arch = [], arch
        self.native, self.fail, self.mismatch = native, fail, mismatch
        self.manifest = b'{"schemaVersion":2,"mediaType":"offline-fixture"}'
        self.digest = 'sha256:' + hashlib.sha256(self.manifest).hexdigest()
        self.pushed = False
        self.names, self.context, self.build_args = None, None, None

    def __call__(self, arguments, **options):
        assert isinstance(arguments, list) and 'shell' not in options
        self.calls.append(arguments)
        if arguments == ['docker', 'buildx', 'version']:
            return SimpleNamespace(returncode=1 if self.native else 0)
        assert options['check'] is True
        if arguments[:2] == ['docker', 'info']:
            return SimpleNamespace(stdout=json.dumps(dict(os='linux', architecture=self.arch)).encode())
        if arguments[:3] == ['docker', 'buildx', 'build'] or arguments[:2] == ['docker', 'build']:
            if self.fail == 'build':
                raise subprocess.CalledProcessError(1, arguments)
            self.build_args = arguments
            self.context = options['stdin'].read()
            options['stdin'].seek(0)
            with tarfile.open(fileobj=options['stdin']) as archive:
                self.names = archive.getnames()
                assert set(self.names) == builder.SOURCE_FILES
                assert all(member.isfile() and member.mode == 0o644 for member in archive.getmembers())
                assert b'PRIVATE_STATE_MUST_NOT_ENTER_THE_CONTEXT' not in b''.join(
                    archive.extractfile(item).read() for item in self.names)
            if '--metadata-file' in arguments:
                report = {'containerimage.digest': self.digest, 'image.name': IMAGE}
                if self.mismatch == 'metadata':
                    report['containerimage.digest'] = 'sha256:' + 'f' * 64
                if self.mismatch == 'name':
                    report['image.name'] = 'registry.example/unrelated:latest'
                path = Path(arguments[arguments.index('--metadata-file') + 1])
                path.write_text(json.dumps(report), encoding='utf-8')
            else:
                assert options['env']['DOCKER_BUILDKIT'] == '0'
            return SimpleNamespace(returncode=0)
        if arguments[:5] == ['docker', 'buildx', 'imagetools', 'inspect', '--raw']:
            if self.fail == 'inspect':
                raise subprocess.CalledProcessError(1, arguments)
            return SimpleNamespace(stdout=b'changed registry manifest' if self.mismatch == 'raw' else self.manifest)
        if arguments[:3] == ['docker', 'image', 'inspect']:
            digest = IMAGE.rsplit(':', 1)[0] + '@' + self.digest
            identifier = 'sha256:' + 'b' * 64 if self.pushed and self.mismatch == 'local' else LOCAL_ID
            return SimpleNamespace(stdout=json.dumps(dict(id=identifier, os='linux', architecture='arm64',
                repo_digests=[] if self.mismatch == 'repository' or not self.pushed else [digest])).encode())
        if arguments[:2] == ['docker', 'push']:
            if self.fail == 'push':
                raise subprocess.CalledProcessError(1, arguments)
            self.pushed = True
            return SimpleNamespace(returncode=0)
        assert arguments[:3] == ['docker', 'manifest', 'inspect']
        if self.fail == 'inspect':
            raise subprocess.CalledProcessError(1, arguments)
        response = dict(Descriptor=dict(digest=self.digest, platform=dict(os='linux', architecture='arm64')),
                        SchemaV2Manifest=dict(schemaVersion=2, config=dict(
                            digest='sha256:' + 'c' * 64 if self.mismatch == 'config' else LOCAL_ID)))
        if self.mismatch == 'index':
            response = [response]
        return SimpleNamespace(stdout=json.dumps(response).encode())


def test_context_is_exact_deterministic_file_allowlist_and_rejects_private_state(source, tmp_path):
    one, two = tmp_path / 'one.tar', tmp_path / 'two.tar'
    identity = builder.context_archive(source, one)
    for name in builder.SOURCE_FILES:
        os.utime(source / name, (100, 100))
    assert builder.context_archive(source, two) == identity
    assert one.read_bytes() == two.read_bytes()
    assert identity['context_sha256'] == hashlib.sha256(one.read_bytes()).hexdigest()
    with tarfile.open(one) as archive:
        assert set(archive.getnames()) == builder.SOURCE_FILES
        assert all(item.mtime == 0 and item.isfile() for item in archive.getmembers())
    assert 'agent_service/store.py' not in builder.SOURCE_FILES
    assert 'agent_service/runtime/adapter.py' not in builder.SOURCE_FILES


def test_packaged_runtime_imports_and_opens_its_database_without_repository_access(source, tmp_path):
    archive, isolated = tmp_path / 'context.tar', tmp_path / 'isolated'
    builder.context_archive(source, archive)
    isolated.mkdir()
    with tarfile.open(archive) as package:
        package.extractall(isolated, filter='data')
    code = ('import sys;sys.path.insert(0,sys.argv[1]);'
            'from video_workbench.app import create_app;'
            'a=create_app(db_path=sys.argv[2],local_mode=True,background=False);'
            'assert a.state.store.list_projects("local-developer")==[];'
            'assert "agent_service.store" not in sys.modules;print("packaged-runtime-ok")')
    result = subprocess.run([sys.executable, '-I', '-c', code, str(isolated), str(tmp_path / 'state.db')],
                            cwd=isolated, capture_output=True, check=True,
                            env={key: value for key, value in os.environ.items() if not key.startswith('VIDEO_')})
    assert result.stdout.strip() == b'packaged-runtime-ok'


def test_reviewed_resource_modification_is_rejected_before_writing_context(source, tmp_path):
    (source / (builder.RESOURCE + 'CONTRACT.md')).write_bytes(b'Changed contract')
    destination = tmp_path / 'context.tar'
    with pytest.raises(builder.BuildError, match='invalid_video_resource_manifest'):
        builder.context_archive(source, destination)
    assert not destination.exists()


def test_missing_or_hardlinked_build_source_cannot_be_packed(source, tmp_path):
    path = source / 'video_workbench/app.py'
    path.unlink()
    with pytest.raises(OSError):
        builder.context_archive(source, tmp_path / 'context.tar')
    os.link(source / 'video_workbench/store.py', path)
    with pytest.raises(builder.BuildError, match='invalid_build_source'):
        builder.context_archive(source, tmp_path / 'context.tar')


def test_source_destination_and_hardlinked_destination_are_rejected(source):
    path = source / 'video_workbench/app.py'
    original = path.read_bytes()
    with pytest.raises(builder.BuildError, match='build_destination_is_source'):
        builder.context_archive(source, path)
    assert path.read_bytes() == original
    linked = source / 'output.tar'
    os.link(path, linked)
    with pytest.raises(builder.BuildError, match='invalid_build_source'):
        builder.context_archive(source, linked)
    assert path.read_bytes() == original


def test_buildx_push_records_only_matching_digest_and_source_context_labels(source):
    receipt, docker = old_receipt(source), Docker()
    result = builder.build(root=source, environ={'SOURCE_REVISION': '1' * 40}, runner=docker)
    assert result == IMAGE + '@' + docker.digest
    assert receipt.read_text(encoding='utf-8') == result + '\n'
    assert '--push' in docker.build_args and '--load' not in docker.build_args
    assert 'PYTHON_BASE_IMAGE=' + builder.PYTHON_BASE in docker.build_args
    assert 'org.opencontainers.image.revision=' + '1' * 40 in docker.build_args
    assert builder.LABEL_PREFIX + 'context-sha256=' + hashlib.sha256(docker.context).hexdigest() in docker.build_args
    assert any(value.startswith(builder.LABEL_PREFIX + 'source-sha256=') for value in docker.build_args)


@pytest.mark.parametrize('native', [False, True])
def test_local_build_removes_receipt_and_never_pushes_or_reads_registry(source, native):
    receipt, docker = old_receipt(source), Docker(native=native)
    assert builder.build(root=source, push=False, environ={}, runner=docker) is None
    assert not receipt.exists()
    assert not any(command[:2] == ['docker', 'push'] or 'imagetools' in command or 'manifest' in command
                   for command in docker.calls)
    assert ('--load' in docker.build_args) is not native


@pytest.mark.parametrize('mismatch', ['metadata', 'name', 'raw'])
def test_buildx_registry_or_metadata_mismatch_invalidates_previous_receipt(source, mismatch):
    receipt = old_receipt(source)
    with pytest.raises(builder.BuildError):
        builder.build(root=source, environ={}, runner=Docker(mismatch=mismatch))
    assert not receipt.exists()


@pytest.mark.parametrize('failure', ['build', 'inspect'])
def test_buildx_failure_cannot_leave_a_stale_release_receipt(source, failure):
    receipt = old_receipt(source)
    with pytest.raises(subprocess.CalledProcessError):
        builder.build(root=source, environ={}, runner=Docker(fail=failure))
    assert not receipt.exists()


def test_native_push_verifies_config_identity_and_published_repository_digest(source):
    docker = Docker(native=True)
    result = builder.build(root=source, native=True, environ={}, runner=docker)
    assert result == IMAGE + '@' + docker.digest
    assert docker.build_args[:2] == ['docker', 'build']
    assert ['docker', 'buildx', 'version'] not in docker.calls
    assert docker.pushed


@pytest.mark.parametrize('mismatch', ['config', 'local', 'repository', 'index'])
def test_native_push_mismatch_cannot_generate_receipt(source, mismatch):
    receipt = old_receipt(source)
    with pytest.raises(builder.BuildError):
        builder.build(root=source, environ={}, runner=Docker(native=True, mismatch=mismatch))
    assert not receipt.exists()


def test_native_platform_mismatch_is_rejected_before_build(source):
    receipt, docker = old_receipt(source), Docker(native=True, arch='x86_64')
    with pytest.raises(builder.BuildError, match='native_builder_platform_mismatch'):
        builder.build(root=source, environ={}, runner=docker)
    assert not receipt.exists() and docker.build_args is None


@pytest.mark.parametrize('environment', [
    {'IMAGE_TAG': 'v1'}, {'IMAGE': 'registry.example/workbench:commit-123'},
    {'IMAGE': 'registry.example/workbench:latest;echo bad'}, {'IMAGE': 'workbench:latest'},
    {'IMAGE': 'registry.example/../workbench:latest'}, {'PLATFORM': 'linux/arm64;echo bad'},
    {'SOURCE_REVISION': 'not-a-git-revision'}, {'PYTHON_BASE_IMAGE': 'python:3.12-slim'},
])
def test_unapproved_build_settings_do_not_reach_docker_and_invalidate_receipt(source, environment):
    receipt, docker = old_receipt(source), Docker()
    with pytest.raises(builder.BuildError):
        builder.build(root=source, environ=environment, runner=docker)
    assert not receipt.exists() and not docker.calls


def test_dockerfile_uses_pinned_base_and_checks_installed_dependencies():
    text = (builder.ROOT / builder.DOCKERFILE).read_text(encoding='utf-8')
    assert 'ARG PYTHON_BASE_IMAGE=' + builder.PYTHON_BASE in text
    assert 'FROM ${PYTHON_BASE_IMAGE}' in text
    assert 'python -m pip check' in text
    assert 'USER 10001:10001' in text


def test_context_cli_only_creates_archive(source, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(builder, 'ROOT', source)
    monkeypatch.setattr(builder, 'build', lambda **_: pytest.fail('Context mode must not build'))
    destination = tmp_path / 'context.tar'
    assert builder.main(['--context', str(destination)]) == 0
    assert json.loads(capsys.readouterr().out)['context_sha256'] == hashlib.sha256(destination.read_bytes()).hexdigest()
