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
    for name in ("Dockerfile.control", "Dockerfile.agent-pi"):
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
    # Sibling business packages never enter the neutral build context.
    (tmp_path / "scientific_agent").mkdir()
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
    assert set(runner.names) == {"agent_service/__init__.py", "agent_service/__main__.py",
                                 "agent_service/runtime.py", "agent_service/requirements.txt",
                                 "agent_service/Dockerfile.control"}
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


def test_pi_build_copies_only_neutral_context_and_passes_donor_as_one_argument(service):
    runner = DockerFixture()
    donor = "registry:5000/reviewed-pi@sha256:" + "a" * 64
    result = build("pi", service=service,
                   environ={"PI_DONOR_IMAGE": donor, "IMAGE": "registry:5000/agent-pi:review"}, runner=runner)
    assert result == "registry:5000/agent-pi@" + runner.checksum
    assert "PI_DONOR_IMAGE=" + donor in runner.calls[0]
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
        build("pi", service=service, environ={"PI_DONOR_IMAGE": "registry:5000/pi@sha256:" + "a" * 64}, runner=runner)
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
