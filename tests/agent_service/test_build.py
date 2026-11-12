"""Offline build admission and pushed-digest receipts; no real Docker is used."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from agent_service.build_image import BuildError, PYTHON_BASE, build, immutable_image


def dockerfile_text(name):
    path = Path(__file__).parents[2] / 'agent_service' / name
    return path.read_text(encoding='utf-8')


def docker_entrypoint(text):
    entries = [line.removeprefix('ENTRYPOINT ').strip()
               for line in text.splitlines() if line.startswith('ENTRYPOINT ')]
    assert len(entries) == 1
    return json.loads(entries[0])


@pytest.mark.parametrize('name', ['Dockerfile.agent-pi', 'Dockerfile.agent-hermes'])
def test_workers_isolate_neutral_controller_dependencies_from_native_hermes_environment(name):
    text = dockerfile_text(name)
    commands = ' '.join(text.replace('\\\n', ' ').split())
    native = '/opt/hermes/.venv/bin/python'
    controller = '/opt/agent-service/.venv/bin/python'
    assert native + ' -m venv /opt/agent-service/.venv' in commands
    assert 'COPY agent_service/requirements.txt /tmp/agent-service-requirements.txt' in commands
    assert controller + ' -m pip install --no-cache-dir -r /tmp/agent-service-requirements.txt' in commands
    assert controller + ' -m pip check' in commands
    # Installing the controller requirements into the donor environment can
    # break Hermes/PPT's reviewed dependency set before any Worker starts.
    assert native + ' -m pip install' not in commands
    assert native + ' -m pip check' not in commands
    assert docker_entrypoint(text) == [controller, '-m', 'agent_service.worker_entry']


def test_control_image_keeps_its_independent_default_python_entrypoint():
    text = dockerfile_text('Dockerfile.control')
    assert docker_entrypoint(text) == ['python', '-m', 'agent_service']
    assert 'python -m pip install --no-cache-dir -r /opt/agent-service/agent_service/requirements.txt' in text
    assert 'python -m pip check' in text
    assert '/opt/hermes/.venv' not in text


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "agent service"
    root.mkdir()
    for name in ("__init__.py", "__main__.py", "runtime.py"):
        (root / name).write_text("# fixture source\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fixture==1.0\n", encoding="utf-8")
    for name in ("Dockerfile.control", "Dockerfile.agent-pi", "Dockerfile.agent-hermes"):
        (root / name).write_text("FROM scratch\nCOPY agent_service/ /code/\n", encoding="utf-8")
    (root / ".env").write_text("PRIVATE_KEY=not-for-the-build\n", encoding="utf-8")
    (root / "credentials.json").write_text('{"key":"private"}', encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "private.py").write_text("PRIVATE_DOC='private'\n", encoding="utf-8")
    (root / "rendered").mkdir()
    (root / "rendered" / "private.py").write_text("PRIVATE_RUNTIME='private'\n", encoding="utf-8")
    bundle = root / "resources" / "workspace-text-v1"
    bundle.mkdir(parents=True)
    contract = b"# Fixed fixture output contract\n"
    (bundle / "CONTRACT.md").write_bytes(contract)
    (bundle / "manifest.json").write_text(json.dumps({"bundle_revision": "workspace-text-v1",
        "local_files": [{"path": "CONTRACT.md", "sha256": hashlib.sha256(contract).hexdigest()}]}), encoding="utf-8")
    (bundle / "credentials.json").write_text('{"key":"private"}', encoding="utf-8")
    (bundle / "NOT_ALLOWED.md").write_text("private resource notes\n", encoding="utf-8")
    (bundle / "private.py").write_text("PRIVATE_RESOURCE='private'\n", encoding="utf-8")
    from agent_service.profiles.video import BUNDLE_ROOT, BUNDLE_FILES
    for name in ('manifest.json', *BUNDLE_FILES):
        target = root / 'resources/video-workbench-v1' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((BUNDLE_ROOT / name).read_bytes())
    from agent_service.profiles.video_motion import (BUNDLE_ROOT as MOTION_ROOT, BUNDLE_FILES as MOTION_FILES,
        BUNDLE_REVISION as MOTION_REVISION)
    for revision in ('video-motion-v1', MOTION_REVISION):
        source = MOTION_ROOT.parent / revision
        for name in ('manifest.json', *MOTION_FILES):
            target = root / 'resources' / revision / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / name).read_bytes())
    from agent_service.profiles.video_process import BUNDLE_ROOT as PROCESS_ROOT, BUNDLE_FILES as PROCESS_FILES
    for name in ('manifest.json', *PROCESS_FILES):
        target = root / 'resources/video-process-v1' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PROCESS_ROOT / name).read_bytes())
    from agent_service.profiles.video_explanation import BUNDLE_ROOT as EXPLANATION_ROOT, BUNDLE_FILES as EXPLANATION_FILES
    for name in ('manifest.json', *EXPLANATION_FILES):
        target = root / 'resources/video-explanation-v1' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((EXPLANATION_ROOT / name).read_bytes())
    from agent_service.profiles.video_animation_experiment import BUNDLE_ROOT as ANIMATION_ROOT, BUNDLE_FILES as ANIMATION_FILES
    for name in ('manifest.json', *ANIMATION_FILES):
        target = root / 'resources/animation-experiment-v1' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ANIMATION_ROOT / name).read_bytes())
    from agent_service.profiles import (video_animation_review as review, video_animation_create as creation,
        video_animation_method as method, video_animation_production as production, video_joint_plan as joint)
    for source, names in (
            (joint.BUNDLE_ROOT, joint.BUNDLE_FILES),
            (review.BUNDLE_ROOT, review.BUNDLE_FILES),
            (review.SEQUENCE_BUNDLE_ROOT, review.SEQUENCE_BUNDLE_FILES),
            (review.BATCH_BUNDLE_ROOT, review.BATCH_BUNDLE_FILES),
            (review.PACKED_BUNDLE_ROOT, review.PACKED_BUNDLE_FILES),
            (creation.BUNDLE_ROOT, creation.BUNDLE_FILES),
            (method.BUNDLE_ROOT, method.BUNDLE_FILES),
            (production.BUNDLE_ROOT, production.BUNDLE_FILES),
            (method.BUNDLE_ROOT.parent / method.LEGACY_BUNDLE_REVISION, method.BUNDLE_FILES),
            (production.BUNDLE_ROOT.parent / production.LEGACY_BUNDLE_REVISION, production.BUNDLE_FILES)):
        for name in ('manifest.json', *names):
            target = root / 'resources' / source.name / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / name).read_bytes())
    # Only reviewed framework-neutral helpers may cross the package allowlist;
    # business API/database/runtime files must still remain outside it.
    names = ['pi_worker/__init__.py', 'pi_worker/research_tools/__init__.py',
             'pi_worker/research_tools/validation.py', 'tools/__init__.py',
             'tools/academic/__init__.py', 'scientific_agent/__init__.py']
    names += ['scientific_agent/' + name for name in ('pi_contracts.py', 'pi_citations.py',
        'pi_sections.py', 'pi_revision_contracts.py', 'pi_revision_validation.py',
        'academic_observations.py', 'workflow_contracts.py')]
    names += ['scientific_skills/' + name for name in
              ('manifest.json', 'LICENSE', 'task-template.md', 'research-procedure/SKILL.md')]
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('# Reviewed neutral fixture resource\n', encoding='utf-8')
    (tmp_path / "scientific_agent" / "private.py").write_text("PRIVATE_BUSINESS='private'\n", encoding="utf-8")
    return root


class DockerFixture:
    def __init__(self, *, fail=None, metadata=None, raw=None):
        self.calls, self.names = [], []
        self.manifest = b'{"schemaVersion":2,"mediaType":"fixture-image-manifest"}'
        self.checksum = "sha256:" + hashlib.sha256(self.manifest).hexdigest()
        self.fail, self.metadata, self.raw = fail, metadata, raw

    def __call__(self, arguments, **options):
        assert isinstance(arguments, list) and "shell" not in options
        if arguments == ['docker', 'buildx', 'version']:
            assert options['check'] is False
            return SimpleNamespace(returncode=0, stdout=b'fixture buildx', stderr=b'')
        assert options["check"] is True
        self.calls.append(arguments)
        if arguments[:3] == ["docker", "buildx", "build"]:
            if self.fail == "build":
                raise subprocess.CalledProcessError(1, arguments)
            with tarfile.open(fileobj=options["stdin"]) as archive:
                self.names = archive.getnames()
                assert all(member.isfile() for member in archive.getmembers())
                assert b"not-for-the-build" not in b"".join(archive.extractfile(item).read() for item in self.names)
            metadata = Path(arguments[arguments.index("--metadata-file") + 1])
            image = arguments[arguments.index("--tag") + 1]
            report = {"containerimage.digest": self.checksum, "image.name": image}
            if self.metadata is not None:
                report.update(self.metadata)
            metadata.write_text(json.dumps(report), encoding="utf-8")
            return SimpleNamespace(returncode=0)
        assert arguments[:5] == ["docker", "buildx", "imagetools", "inspect", "--raw"]
        if self.fail == "inspect":
            raise subprocess.CalledProcessError(1, arguments)
        return SimpleNamespace(stdout=self.manifest if self.raw is None else self.raw, returncode=0)


def old_receipt(service, target="control"):
    path = service / "rendered" / (target + "-image.txt")
    path.write_text("registry:5000/old@sha256:" + "0" * 64 + "\n", encoding="utf-8")
    return path


def test_build_push_records_exact_matching_immutable_digest_and_bounds_context(service):
    runner = DockerFixture()
    receipt = old_receipt(service)
    digest = build(service=service, environ={"SOURCE_REVISION": "a" * 40}, runner=runner)
    expected = "arm-cluster-master:5000/agent-service-control:latest@" + runner.checksum
    assert digest == expected and receipt.read_text(encoding="utf-8") == expected + "\n"
    command = runner.calls[0]
    assert command[:3] == ["docker", "buildx", "build"]
    assert command[command.index("--platform") + 1] == "linux/arm64"
    assert "--push" in command and "--load" not in command
    assert "PYTHON_BASE_IMAGE=" + PYTHON_BASE in command
    assert {"agent_service/__init__.py", "agent_service/__main__.py", "agent_service/runtime.py",
            "agent_service/requirements.txt", "agent_service/Dockerfile.control",
            "agent_service/resources/workspace-text-v1/CONTRACT.md",
            "scientific_agent/academic_observations.py", "pi_worker/research_tools/validation.py"} <= set(runner.names)
    assert not any(name.endswith(('credentials.json', 'private.py', 'NOT_ALLOWED.md')) for name in runner.names)
    assert all(name.startswith(('agent_service/', 'scientific_agent/', 'pi_worker/', 'tools/', 'scientific_skills/'))
               for name in runner.names)
    assert runner.calls[1][-1] == "arm-cluster-master:5000/agent-service-control:latest"
    assert 'org.opencontainers.image.revision=' + 'a' * 40 in command
    assert any(item.startswith('agent-service.panghuer.top/source-sha256=') for item in command)
    assert any(item.startswith('org.opencontainers.image.created=') for item in command)


def test_no_push_removes_previous_deployable_digest_and_does_not_query_registry(service):
    receipt = old_receipt(service)
    runner = DockerFixture()
    assert build(push=False, service=service, environ={}, runner=runner) is None
    assert not receipt.exists()
    assert len(runner.calls) == 1
    assert "--load" in runner.calls[0] and "--push" not in runner.calls[0]


def test_default_latest_build_retains_source_identity_in_labels(service):
    runner = DockerFixture()
    revision = 'a' * 40
    build(push=False, service=service, environ={'SOURCE_REVISION': revision}, runner=runner)
    command = runner.calls[0]
    assert command[command.index('--tag') + 1] == 'arm-cluster-master:5000/agent-service-control:latest'
    labels = [command[index + 1] for index, value in enumerate(command) if value == '--label']
    assert 'org.opencontainers.image.revision=' + revision in labels
    assert 'org.opencontainers.image.source=https://github.com/zmhhaha/panghu_agent' in labels
    assert len(next(value.removeprefix('io.panghu.source.context-sha256=') for value in labels
                    if value.startswith('io.panghu.source.context-sha256='))) == 64


def test_invalid_source_revision_never_reaches_docker(service):
    runner = DockerFixture()
    with pytest.raises(BuildError, match='invalid_source_revision'):
        build(service=service, environ={'SOURCE_REVISION': 'unverified-branch'}, runner=runner)
    assert not runner.calls


def test_latest_base_still_requires_an_exact_digest():
    value = 'registry:5000/base:latest@sha256:' + 'a' * 64
    assert immutable_image(value) == value
    for unsafe in ('registry:5000/base:latest', 'registry:5000/base:version@sha256:' + 'a' * 64):
        with pytest.raises(BuildError):
            immutable_image(unsafe)


@pytest.mark.parametrize("failure", ["build", "inspect"])
def test_failed_build_or_push_verification_cannot_reuse_an_old_digest(service, failure):
    receipt, other = old_receipt(service), old_receipt(service, "pi")
    runner = DockerFixture(fail=failure)
    with pytest.raises(subprocess.CalledProcessError):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists()
    assert other.exists()


@pytest.mark.parametrize("metadata,raw", [
    ({"containerimage.digest": "sha256:" + "f" * 64}, None),
    ({"containerimage.digest": "not-a-digest"}, None),
    ({"image.name": "unrelated.example/image:tag"}, None),
    ({}, b"different-tag-manifest"),
])
def test_unrelated_or_changed_pushed_manifest_cannot_become_deployment_receipt(service, metadata, raw):
    receipt = old_receipt(service)
    with pytest.raises(BuildError):
        build(service=service, environ={}, runner=DockerFixture(metadata=metadata, raw=raw))
    assert not receipt.exists()


@pytest.mark.parametrize("environment", [
    {"IMAGE": "registry:5000/image:$(touch injected)"},
    {"IMAGE": "registry:5000/image:tag;echo injected"},
    {"IMAGE": "--secret /path/image:tag"},
    {"IMAGE": "registry:5000/image:tag\n--load"},
    {"IMAGE": "registry:5000/a/../image:tag"},
    {"IMAGE": "image:tag"},
    {"PLATFORM": "linux/arm64;echo injected"},
    {"PYTHON_BASE_IMAGE": "python:latest"},
    {"PYTHON_BASE_IMAGE": "python@sha256:" + "g" * 64},
])
def test_invalid_build_inputs_never_reach_docker_and_invalidate_stale_receipts(service, environment):
    receipt = old_receipt(service)
    runner = DockerFixture()
    with pytest.raises(BuildError):
        build(service=service, environ=environment, runner=runner)
    assert runner.calls == [] and not receipt.exists()


@pytest.mark.parametrize("donor", [None, "registry:5000/pi:latest", "registry:5000/pi@sha256:" + "z" * 64])
def test_pi_build_requires_immutable_reviewed_donor_before_docker(service, donor):
    receipt = old_receipt(service, "pi")
    runner = DockerFixture()
    environment = {} if donor is None else {"PI_CAGE_IMAGE": donor}
    with pytest.raises(BuildError):
        build("pi", service=service, environ=environment, runner=runner)
    assert not receipt.exists() and runner.calls == []


class NativeDockerFixture:
    """Legacy single-architecture Docker with no optional buildx plugin."""
    def __init__(self, *, arch='aarch64', fail=None, mismatch=None, oci=False):
        self.calls, self.names = [], []
        self.arch, self.fail, self.mismatch, self.oci = arch, fail, mismatch, oci
        self.image_id = 'sha256:' + 'c' * 64
        self.checksum = 'sha256:' + 'd' * 64
        self.pushed, self.inspections = False, 0

    def __call__(self, arguments, **options):
        assert isinstance(arguments, list) and 'shell' not in options
        self.calls.append(arguments)
        if arguments == ['docker', 'buildx', 'version']:
            assert options['check'] is False
            return SimpleNamespace(returncode=1, stdout=b'', stderr=b'docker: buildx unavailable')
        assert options['check'] is True
        if self.fail and arguments[1:1 + len(self.fail.split())] == self.fail.split():
            raise subprocess.CalledProcessError(1, arguments)
        if arguments[:2] == ['docker', 'info']:
            return SimpleNamespace(stdout=json.dumps({'os': 'linux', 'architecture': self.arch}).encode())
        if arguments[:2] == ['docker', 'build']:
            assert options['env']['DOCKER_BUILDKIT'] == '0'
            with tarfile.open(fileobj=options['stdin']) as archive:
                self.names = archive.getnames()
                assert all(member.isfile() for member in archive.getmembers())
            self.image = arguments[arguments.index('--tag') + 1]
            return SimpleNamespace(returncode=0)
        if arguments[:3] == ['docker', 'image', 'inspect']:
            self.inspections += 1
            name = self.image.rsplit(':', 1)[0]
            references = [name + '@' + self.checksum] if self.pushed else []
            value = dict(id=self.image_id, os='linux', architecture='arm64', repo_digests=references)
            if self.mismatch == 'local-platform':
                value['architecture'] = 'amd64'
            if self.pushed and self.mismatch == 'local-id':
                value['id'] = 'sha256:' + 'f' * 64
            if self.pushed and self.mismatch == 'repo':
                value['repo_digests'] = ['different.invalid/image@' + self.checksum]
            if self.pushed and self.mismatch == 'digest':
                value['repo_digests'] = [name + '@sha256:' + 'f' * 64]
            if self.pushed and self.mismatch == 'missing-repodigests':
                value['repo_digests'] = None
            return SimpleNamespace(stdout=json.dumps(value).encode())
        if arguments[:2] == ['docker', 'push']:
            self.pushed = True
            return SimpleNamespace(returncode=0)
        assert arguments[:5] == ['docker', 'manifest', 'inspect', '--insecure', '--verbose']
        descriptor = dict(digest=self.checksum, platform={'os': 'linux', 'architecture': 'arm64'})
        manifest = dict(schemaVersion=2, config={'digest': self.image_id}, layers=[])
        if self.mismatch == 'remote-platform':
            descriptor['platform']['architecture'] = 'amd64'
        if self.mismatch == 'config':
            manifest['config']['digest'] = 'sha256:' + 'f' * 64
        result = dict(Ref=self.image, Descriptor=descriptor,
            **{'OCIManifest' if self.oci else 'SchemaV2Manifest': manifest})
        if self.mismatch == 'multiplatform':
            result = [result]
        if self.mismatch == 'malformed':
            return SimpleNamespace(stdout=b'{invalid json')
        return SimpleNamespace(stdout=json.dumps(result).encode())


@pytest.mark.parametrize('oci', [False, True])
def test_no_buildx_uses_native_build_push_and_exact_remote_digest_receipt(service, oci):
    runner = NativeDockerFixture(oci=oci)
    receipt = old_receipt(service)
    expected = 'arm-cluster-master:5000/agent-service-control:latest@' + runner.checksum
    assert build(service=service, environ={}, runner=runner) == expected
    assert receipt.read_text() == expected + '\n'
    build_command = next(command for command in runner.calls if command[:2] == ['docker', 'build'])
    assert build_command[build_command.index('--platform') + 1] == 'linux/arm64'
    assert '--push' not in build_command and '--metadata-file' not in build_command
    assert any(command[:2] == ['docker', 'push'] for command in runner.calls)
    assert any(command[:5] == ['docker', 'manifest', 'inspect', '--insecure', '--verbose'] for command in runner.calls)
    assert runner.inspections == 2
    assert not any(command[1:3] == ['buildx', 'build'] for command in runner.calls)
    assert not any(name.endswith(('credentials.json', 'private.py', 'NOT_ALLOWED.md')) for name in runner.names)


def test_no_buildx_no_push_builds_locally_without_registry_or_deployable_receipt(service):
    runner = NativeDockerFixture()
    receipt = old_receipt(service)
    assert build(service=service, push=False, environ={}, runner=runner) is None
    assert not receipt.exists()
    assert [command[:2] for command in runner.calls] == [['docker', 'buildx'], ['docker', 'info'], ['docker', 'build']]


@pytest.mark.parametrize('arch', ['x86_64', 'amd64', 'armv7l', 'unknown'])
def test_native_fallback_refuses_cross_architecture_before_build(service, arch):
    runner = NativeDockerFixture(arch=arch)
    receipt = old_receipt(service)
    with pytest.raises(BuildError, match='native_builder_platform_mismatch'):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists()
    assert not any(command[:2] == ['docker', 'build'] for command in runner.calls)


@pytest.mark.parametrize('mismatch', ['local-platform', 'local-id', 'repo', 'digest', 'missing-repodigests',
    'remote-platform', 'config', 'multiplatform', 'malformed'])
def test_native_fallback_cannot_accept_raced_unrelated_or_unverified_digest(service, mismatch):
    runner = NativeDockerFixture(mismatch=mismatch)
    receipt = old_receipt(service)
    with pytest.raises(BuildError):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists()


@pytest.mark.parametrize('failure', ['info', 'build', 'push', 'manifest inspect', 'image inspect'])
def test_native_failure_never_preserves_old_or_writes_new_receipt(service, failure):
    runner = NativeDockerFixture(fail=failure)
    receipt = old_receipt(service)
    with pytest.raises(subprocess.CalledProcessError):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists()


def test_pi_build_copies_only_neutral_context_and_passes_donor_as_one_argument(service):
    runner = DockerFixture()
    donor = "registry:5000/reviewed-pi:latest@sha256:" + "a" * 64
    ppt_donor = "registry:5000/reviewed-ppt:latest@sha256:" + "b" * 64
    result = build("pi", service=service,
                   environ={"PI_CAGE_IMAGE": donor, "PPT_DONOR_IMAGE": ppt_donor,
                            "IMAGE": "registry:5000/agent-pi:latest"}, runner=runner)
    assert result == "registry:5000/agent-pi:latest@" + runner.checksum
    from agent_service.pi_cage import PI_CAGE_REVISION
    assert "PI_CAGE_IMAGE=" + donor in runner.calls[0]
    assert "PI_CAGE_REVISION=" + PI_CAGE_REVISION in runner.calls[0]
    assert "PPT_DONOR_IMAGE=" + ppt_donor in runner.calls[0]
    assert "agent_service/Dockerfile.agent-pi" in runner.names
    assert "agent_service/Dockerfile.control" not in runner.names
    from agent_service.profiles.video_motion import BUNDLE_REVISION as MOTION_REVISION
    assert set(name for name in runner.names if "/resources/" in name) == {
        "agent_service/resources/workspace-text-v1/CONTRACT.md",
        "agent_service/resources/workspace-text-v1/manifest.json",
        "agent_service/resources/video-workbench-v1/manifest.json",
        "agent_service/resources/video-workbench-v1/CONTRACT.md",
        "agent_service/resources/video-workbench-v1/LICENSE",
        "agent_service/resources/video-workbench-v1/video-procedure/SKILL.md",
        f"agent_service/resources/{MOTION_REVISION}/manifest.json",
        f"agent_service/resources/{MOTION_REVISION}/CONTRACT.md",
        f"agent_service/resources/{MOTION_REVISION}/LICENSE",
        f"agent_service/resources/{MOTION_REVISION}/PLAN.schema.json",
        f"agent_service/resources/{MOTION_REVISION}/motion-procedure/SKILL.md",
        "agent_service/resources/video-motion-v1/manifest.json",
        "agent_service/resources/video-motion-v1/CONTRACT.md",
        "agent_service/resources/video-motion-v1/LICENSE",
        "agent_service/resources/video-motion-v1/PLAN.schema.json",
        "agent_service/resources/video-motion-v1/motion-procedure/SKILL.md",
        "agent_service/resources/video-process-v1/manifest.json",
        "agent_service/resources/video-process-v1/CONTRACT.md",
        "agent_service/resources/video-process-v1/LICENSE",
        "agent_service/resources/video-process-v1/PLAN.schema.json",
        "agent_service/resources/video-process-v1/process-procedure/SKILL.md",
        "agent_service/resources/video-explanation-v1/manifest.json",
        "agent_service/resources/video-explanation-v1/CONTRACT.md",
        "agent_service/resources/video-explanation-v1/LICENSE",
        "agent_service/resources/video-explanation-v1/RESULT.schema.json",
        "agent_service/resources/video-explanation-v1/explanation-procedure/SKILL.md",
        "agent_service/resources/animation-experiment-v1/manifest.json",
        "agent_service/resources/animation-experiment-v1/CONTRACT.md",
        "agent_service/resources/animation-experiment-v1/LICENSE",
        "agent_service/resources/animation-experiment-v1/animation-procedure/SKILL.md"} | {
            'agent_service/resources/animation-method-v1/' + name
            for name in ('manifest.json', 'CONTRACT.md', 'LICENSE', 'method-procedure/SKILL.md')} | {
            'agent_service/resources/' + revision + '/' + name
            for revision in ('animation-review-v1', 'animation-review-v2', 'animation-review-v3', 'animation-review-v4')
            for name in ('manifest.json', 'CONTRACT.md', 'LICENSE', 'review-procedure/SKILL.md')} | {
            'agent_service/resources/animation-create-v2/' + name
            for name in ('manifest.json', 'CONTRACT.md', 'LICENSE', 'creation-procedure/SKILL.md')} | {
            'agent_service/resources/animation-production-v1/' + name
            for name in ('manifest.json', 'CONTRACT.md', 'LICENSE', 'production-procedure/SKILL.md')} | {
            'agent_service/resources/animation-method-v2/' + name
            for name in ('manifest.json', 'CONTRACT.md', 'LICENSE', 'method-procedure/SKILL.md')} | {
            'agent_service/resources/animation-production-v2/' + name
            for name in ('manifest.json', 'CONTRACT.md', 'LICENSE', 'production-procedure/SKILL.md')} | {
            'agent_service/resources/video-joint-plan-v1/' + name
            for name in ('manifest.json', 'CONTRACT.md', 'LICENSE', 'joint-plan-procedure/SKILL.md')}
    assert not any(name.endswith(("credentials.json", "NOT_ALLOWED.md", "private.py")) for name in runner.names)


@pytest.mark.parametrize("change", ["hash", "extra_file", "path"])
def test_pi_profile_manifest_cannot_replace_or_expand_reviewed_resource_inputs(service, change):
    manifest = service / "resources" / "workspace-text-v1" / "manifest.json"
    resource = json.loads(manifest.read_text(encoding="utf-8"))
    if change == "hash":
        resource["local_files"][0]["sha256"] = "0" * 64
    elif change == "extra_file":
        resource["local_files"].append({"path": "credentials.json", "sha256": "a" * 64})
    else:
        resource["local_files"][0]["path"] = "../credentials.json"
    manifest.write_text(json.dumps(resource), encoding="utf-8")
    receipt, runner = old_receipt(service, "pi"), DockerFixture()
    with pytest.raises(BuildError, match="invalid_profile_resource_manifest"):
        build("pi", service=service, environ={"PI_CAGE_IMAGE": "registry:5000/pi@sha256:" + "a" * 64,
            "PPT_DONOR_IMAGE": "registry:5000/ppt@sha256:" + "b" * 64}, runner=runner)
    assert not receipt.exists() and runner.calls == []


def symlink_or_skip(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("Symbolic links require local platform permission")


def test_linked_rendered_directory_cannot_delete_an_external_receipt(service, tmp_path):
    rendered = service / "rendered"
    (rendered / "private.py").unlink()
    rendered.rmdir()
    external = tmp_path / "external"
    external.mkdir()
    protected = external / "control-image.txt"
    protected.write_text("must survive", encoding="utf-8")
    symlink_or_skip(rendered, external, directory=True)
    runner = DockerFixture()
    with pytest.raises(BuildError):
        build(service=service, environ={}, runner=runner)
    assert protected.read_text(encoding="utf-8") == "must survive" and runner.calls == []


def test_linked_python_source_cannot_enter_the_build_context(service, tmp_path):
    source = service / "runtime.py"
    source.unlink()
    outside = tmp_path / "private.py"
    outside.write_text("PRIVATE='private'\n", encoding="utf-8")
    symlink_or_skip(source, outside)
    receipt = old_receipt(service)
    runner = DockerFixture()
    with pytest.raises(BuildError):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists() and runner.calls == []


@pytest.mark.parametrize('changed', ['manifest.json', 'CONTRACT.md', 'video-procedure/SKILL.md'])
def test_modified_video_resource_fails_before_docker(service, changed):
    path = service / 'resources/video-workbench-v1' / changed
    path.write_bytes(path.read_bytes() + b' ')
    receipt, runner = old_receipt(service), DockerFixture()
    with pytest.raises(BuildError, match='invalid_video_resource_manifest'):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists() and not runner.calls


@pytest.mark.parametrize('target', ['control', 'pi', 'hermes'])
def test_clean_context_preserves_exact_legacy_and_current_motion_bundles(service, tmp_path, target):
    from agent_service.build_image import context_archive, MOTION_FILES
    destination = tmp_path / 'motion-context.tar'
    context_archive(service, target, destination)
    with tarfile.open(destination) as archive:
        for revision in ('video-motion-v1', 'video-motion-v2'):
            root = 'agent_service/resources/' + revision + '/'
            assert {name.removeprefix(root) for name in archive.getnames() if name.startswith(root)} == {
                'manifest.json', *MOTION_FILES}
            for name in ('manifest.json', *MOTION_FILES):
                assert archive.extractfile(root + name).read() == (service / 'resources' / revision / name).read_bytes()


@pytest.mark.parametrize('target', ['control', 'pi', 'hermes'])
def test_clean_source_context_carries_all_real_profile_resource_bytes(tmp_path, target):
    from agent_service.build_image import context_archive
    service = Path(__file__).parents[2] / 'agent_service'
    destination = tmp_path / 'complete-source-context.tar'
    context_archive(service, target, destination)
    actual = {path.relative_to(service).as_posix(): path.read_bytes()
        for path in (service / 'resources').rglob('*') if path.is_file()}
    assert len(actual) == 70
    with tarfile.open(destination) as archive:
        captured = {name.removeprefix('agent_service/'): archive.extractfile(name).read()
            for name in archive.getnames() if name.startswith('agent_service/resources/')}
        assert captured == actual
        from agent_service.input_tools import READ_INPUTS_EXTENSION_SHA256
        extension = archive.extractfile('agent_service/read_inputs_extension.mjs').read()
        assert extension == (service / 'read_inputs_extension.mjs').read_bytes()
        assert hashlib.sha256(extension).hexdigest() == READ_INPUTS_EXTENSION_SHA256
        assert archive.extractfile('scientific_agent/workflow_contracts.py').read() == (
            service.parent / 'scientific_agent/workflow_contracts.py').read_bytes()
        assert archive.extractfile('scientific_agent/academic_observations.py').read() == (
            service.parent / 'scientific_agent/academic_observations.py').read_bytes()


def test_clean_builder_animation_pins_match_independently_verified_profile_bundles():
    from agent_service.build_image import ANIMATION_RUNTIME_BUNDLES
    from agent_service.profiles import (video_animation_review as review, video_animation_create as creation,
        video_animation_method as method, video_animation_production as production, video_joint_plan as joint)
    measured = {}
    for root, files, verify in (
            (joint.BUNDLE_ROOT, joint.BUNDLE_FILES, joint.verified_bundle),
            (review.BUNDLE_ROOT, review.BUNDLE_FILES, review.verified_bundle),
            (review.SEQUENCE_BUNDLE_ROOT, review.SEQUENCE_BUNDLE_FILES, review.verified_sequence_bundle),
            (review.BATCH_BUNDLE_ROOT, review.BATCH_BUNDLE_FILES, review.verified_batch_bundle),
            (review.PACKED_BUNDLE_ROOT, review.PACKED_BUNDLE_FILES, review.verified_packed_bundle),
            (creation.BUNDLE_ROOT, creation.BUNDLE_FILES, creation.verified_bundle),
            (method.BUNDLE_ROOT, method.BUNDLE_FILES, method.verified_bundle),
            (production.BUNDLE_ROOT, production.BUNDLE_FILES, production.verified_bundle)):
        assert set(verify()) == files
        measured[root.name] = (hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(), files)
    for profile in (method, production):
        root = profile.BUNDLE_ROOT.parent / profile.LEGACY_BUNDLE_REVISION
        raw = (root / 'manifest.json').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == profile.LEGACY_BUNDLE_MANIFEST_SHA256
        for row in json.loads(raw)['local_files']:
            assert hashlib.sha256((root / row['path']).read_bytes()).hexdigest() == row['sha256']
        measured[root.name] = (profile.LEGACY_BUNDLE_MANIFEST_SHA256, profile.BUNDLE_FILES)
    assert {revision: (pin, files) for revision, pin, files in ANIMATION_RUNTIME_BUNDLES} == measured


def test_clean_context_imports_control_roles_and_existing_profile_closures_without_repository_fallback(tmp_path):
    from agent_service.build_image import context_archive
    service = Path(__file__).parents[2] / 'agent_service'
    destination = tmp_path / 'isolated-source-context.tar'
    extracted = tmp_path / 'isolated-source'
    extracted.mkdir()
    context_archive(service, 'pi', destination)
    with tarfile.open(destination) as archive:
        for member in archive.getmembers():
            # Only files emitted by this bounded context builder are measured.
            assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            path = extracted / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
    code = r'''import importlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
modules=('agent_service.__main__','agent_service.api','agent_service.dispatcher',
 'agent_service.model_proxy','agent_service.worker_entry','agent_service.resources',
 'scientific_agent.workflow_contracts','scientific_agent.academic_observations',
 'scientific_agent.pi_revision_validation','pi_worker.research_tools.bridge')
for name in modules:
 module=importlib.import_module(name)
 assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
from agent_service.profiles import implementation
profiles=('workspace-task.v1','ppt.plan_initial.v1','ppt.plan_options.v1','ppt.prepare.v1',
 'ppt.generate.v1','ppt.revise.v1','research.generate.v1','research.revise.v1',
 'research.plan.v1','research.retrieve.v1','research.outline.v1','research.draft.v1',
 'video.storyboard.v1','video.scene.v1','video.motion-plan.v1','video.process-plan.v1',
 'video.explanation-plan.v1','video.animation-experiment.v1','video.animation-observe.v1',
 'video.animation-review.v1','video.animation-review.v2','video.animation-review.v3','video.animation-review.v4','video.animation-create.v2',
 'video.animation-method-author.v1','video.animation-method-review.v1')
for name in profiles:implementation(name)
for name in ('video','video_motion','video_process','video_explanation','video_animation_experiment',
 'video_animation_create','video_animation_review','video_animation_method','research'):
 importlib.import_module('agent_service.profiles.'+name).verified_bundle()
from agent_service.profiles.video_animation_review import verified_sequence_bundle,verified_batch_bundle,verified_packed_bundle
verified_sequence_bundle();verified_batch_bundle();verified_packed_bundle()
from agent_service.catalog_limits import HISTORY_RECORD_LIMIT
from agent_service.model_proxy import image_inputs
assert HISTORY_RECORD_LIMIT==256 and image_inputs.name=='agent_model_image_inputs'
print(json.dumps({'control_modules':len(modules),'profile_implementations':len(profiles)}))
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(extracted)],
        cwd=extracted, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
    assert json.loads(result.stdout) == dict(control_modules=10, profile_implementations=26)


@pytest.mark.parametrize('revision', ['animation-review-v1', 'animation-review-v2', 'animation-review-v3',
                                     'animation-create-v2', 'animation-method-v1', 'animation-method-v2',
                                     'animation-production-v1', 'animation-production-v2'])
@pytest.mark.parametrize('changed', ['manifest.json', 'CONTRACT.md', 'LICENSE', 'procedure'])
def test_changed_animation_runtime_resource_fails_before_docker(service, revision, changed):
    if changed == 'procedure':
        changed = ('method' if revision.startswith('animation-method-') else
                   'production' if revision.startswith('animation-production-') else
                   'creation' if revision == 'animation-create-v2' else 'review') + '-procedure/SKILL.md'
    path = service / 'resources' / revision / changed
    path.write_bytes(path.read_bytes() + b' ')
    receipt, runner = old_receipt(service), DockerFixture()
    with pytest.raises(BuildError, match='invalid_animation_runtime_resource_manifest'):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists() and not runner.calls


@pytest.mark.parametrize('revision', ['animation-review-v1', 'animation-review-v2', 'animation-review-v3',
                                     'animation-create-v2', 'animation-method-v1', 'animation-method-v2',
                                     'animation-production-v1', 'animation-production-v2'])
def test_missing_animation_runtime_resource_cannot_produce_deployable_digest(service, revision):
    (service / 'resources' / revision / 'LICENSE').unlink()
    receipt, runner = old_receipt(service), DockerFixture()
    with pytest.raises(BuildError, match='invalid_animation_runtime_resource_manifest'):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists() and not runner.calls


def test_unreviewed_animation_resource_files_stay_out_of_clean_image(service, tmp_path):
    from agent_service.build_image import context_archive
    private = service / 'resources/animation-review-v3/private.py'
    private.write_bytes(b'PRIVATE_RESOURCE = True\n')
    extra = service / 'resources/animation-unreviewed/credentials.json'
    extra.parent.mkdir()
    extra.write_bytes(b'{"private":true}')
    destination = tmp_path / 'bounded-animation-context.tar'
    context_archive(service, 'control', destination)
    with tarfile.open(destination) as archive:
        assert 'agent_service/resources/animation-review-v3/private.py' not in archive.getnames()
        assert not any('animation-unreviewed' in name for name in archive.getnames())


@pytest.mark.parametrize('revision', ['video-motion-v1', 'video-motion-v2'])
@pytest.mark.parametrize('changed', ['manifest.json', 'CONTRACT.md', 'LICENSE', 'PLAN.schema.json', 'motion-procedure/SKILL.md'])
def test_modified_motion_resource_fails_before_docker(service, changed, revision):
    path = service / 'resources' / revision / changed
    path.write_bytes(path.read_bytes() + b' ')
    receipt, runner = old_receipt(service), DockerFixture()
    with pytest.raises(BuildError, match='invalid_motion_resource_manifest'):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists() and not runner.calls


@pytest.mark.parametrize('changed', ['manifest.json', 'CONTRACT.md', 'PLAN.schema.json', 'process-procedure/SKILL.md'])
def test_modified_process_resource_fails_before_docker(service, changed):
    path = service / 'resources/video-process-v1' / changed
    path.write_bytes(path.read_bytes() + b' ')
    receipt, runner = old_receipt(service), DockerFixture()
    with pytest.raises(BuildError, match='invalid_process_resource_manifest'):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists() and not runner.calls


@pytest.mark.parametrize('changed', ['manifest.json', 'CONTRACT.md', 'RESULT.schema.json',
                                   'explanation-procedure/SKILL.md'])
def test_modified_explanation_resource_fails_before_docker(service, changed):
    path = service / 'resources/video-explanation-v1' / changed
    path.write_bytes(path.read_bytes() + b' ')
    receipt, runner = old_receipt(service), DockerFixture()
    with pytest.raises(BuildError, match='invalid_explanation_resource_manifest'):
        build(service=service, environ={}, runner=runner)
    assert not receipt.exists() and not runner.calls


@pytest.mark.parametrize('environment', [{'IMAGE_TAG': 'version'}, {'IMAGE': 'registry:5000/agent:version'}])
def test_application_build_uses_latest_only(service, environment):
    runner = DockerFixture()
    with pytest.raises(BuildError, match='latest_image_tag_required'):
        build(service=service, environ=environment, runner=runner)
    assert not runner.calls
