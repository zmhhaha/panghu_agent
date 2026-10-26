"""Offline build admission and pushed-digest receipts; no real Docker is used."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from agent_service.build_image import BuildError, PYTHON_BASE, build


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
    # Only reviewed framework-neutral helpers may cross the package allowlist;
    # business API/database/runtime files must still remain outside it.
    names = ['pi_worker/__init__.py', 'pi_worker/research_tools/__init__.py',
             'pi_worker/research_tools/validation.py', 'tools/__init__.py',
             'tools/academic/__init__.py', 'scientific_agent/__init__.py']
    names += ['scientific_agent/' + name for name in ('pi_contracts.py', 'pi_citations.py',
        'pi_sections.py', 'pi_revision_contracts.py', 'pi_revision_validation.py',
        'academic_observations.py')]
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
    digest = build(service=service, environ={"IMAGE_TAG": "fixture"}, runner=runner)
    expected = "arm-cluster-master:5000/agent-service-control@" + runner.checksum
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
    assert runner.calls[1][-1] == "arm-cluster-master:5000/agent-service-control:fixture"


def test_no_push_removes_previous_deployable_digest_and_does_not_query_registry(service):
    receipt = old_receipt(service)
    runner = DockerFixture()
    assert build(push=False, service=service, environ={}, runner=runner) is None
    assert not receipt.exists()
    assert len(runner.calls) == 1
    assert "--load" in runner.calls[0] and "--push" not in runner.calls[0]


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
    environment = {} if donor is None else {"PI_DONOR_IMAGE": donor}
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
    expected = 'arm-cluster-master:5000/agent-service-control@' + runner.checksum
    assert build(service=service, environ={'IMAGE_TAG': 'native'}, runner=runner) == expected
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
    donor = "registry:5000/reviewed-pi@sha256:" + "a" * 64
    ppt_donor = "registry:5000/reviewed-ppt@sha256:" + "b" * 64
    result = build("pi", service=service,
                   environ={"PI_DONOR_IMAGE": donor, "PPT_DONOR_IMAGE": ppt_donor,
                            "IMAGE": "registry:5000/agent-pi:review"}, runner=runner)
    assert result == "registry:5000/agent-pi@" + runner.checksum
    assert "PI_DONOR_IMAGE=" + donor in runner.calls[0]
    assert "PPT_DONOR_IMAGE=" + ppt_donor in runner.calls[0]
    assert "agent_service/Dockerfile.agent-pi" in runner.names
    assert "agent_service/Dockerfile.control" not in runner.names
    assert set(name for name in runner.names if "/resources/" in name) == {
        "agent_service/resources/workspace-text-v1/CONTRACT.md",
        "agent_service/resources/workspace-text-v1/manifest.json"}
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
        build("pi", service=service, environ={"PI_DONOR_IMAGE": "registry:5000/pi@sha256:" + "a" * 64,
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
