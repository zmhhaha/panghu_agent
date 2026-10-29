"""Build bounded Agent images and retain only a verified pushed registry digest."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import uuid

try:
    from .pi_cage import PI_CAGE_REVISION
except ImportError:  # build.sh executes this file directly.
    from pi_cage import PI_CAGE_REVISION


SERVICE = Path(__file__).resolve().parent
PYTHON_BASE = "python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f"
TARGETS = {"control": "Dockerfile.control", "pi": "Dockerfile.agent-pi", "hermes": "Dockerfile.agent-hermes"}
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
PART = re.compile(r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*\Z")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
PROFILE_RESOURCE = "resources/workspace-text-v1/"


class BuildError(RuntimeError):
    """Fixed diagnostic categories only; never echo arbitrary build inputs."""


def unique_pairs(entries):
    result = {}
    for key, value in entries:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def repository(value, *, registry_required=False):
    if (not isinstance(value, str) or not 1 <= len(value) <= 255 or ".." in value
            or "@" in value or "//" in value):
        raise BuildError("invalid_image_repository")
    parts = value.split("/")
    if len(parts) > 1:
        host = parts[0]
        if ":" in host:
            address, separator, port = host.rpartition(":")
            if (not separator or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", address)
                    or not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535):
                raise BuildError("invalid_image_repository")
        elif not PART.fullmatch(host):
            raise BuildError("invalid_image_repository")
        body = parts[1:]
    else:
        host, body = "", parts
    if any(not PART.fullmatch(part) for part in body):
        raise BuildError("invalid_image_repository")
    if registry_required and not ("." in host or ":" in host or host == "localhost"):
        raise BuildError("explicit_registry_required")
    return value


def tagged_image(value):
    if not isinstance(value, str) or ":" not in value.rsplit("/", 1)[-1]:
        raise BuildError("tagged_image_required")
    name, tag = value.rsplit(":", 1)
    repository(name, registry_required=True)
    if not TAG.fullmatch(tag):
        raise BuildError("invalid_image_tag")
    return name


def immutable_image(value):
    if not isinstance(value, str) or value.count("@") != 1:
        raise BuildError("immutable_base_required")
    name, checksum = value.split("@")
    repository(name)
    if not SHA256.fullmatch(checksum):
        raise BuildError("immutable_base_required")
    return value


def checked_directory(path):
    path = Path(path).absolute()
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise BuildError("linked_build_path")
    if not path.is_dir():
        raise BuildError("missing_build_directory")
    return path


def read_regular(path, limit=2 * 1024 * 1024):
    checked_directory(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise BuildError("invalid_build_source")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(info, opened):
            raise BuildError("build_source_changed")
        content = stream.read(limit + 1)
    after = path.stat()
    if len(content) != info.st_size or len(content) > limit or after.st_mtime_ns != info.st_mtime_ns:
        raise BuildError("build_source_changed")
    return content


def reset_receipt(service, target):
    service = checked_directory(service)
    rendered = service / "rendered"
    if not rendered.exists():
        rendered.mkdir(mode=0o700)
    checked_directory(rendered)
    receipt = rendered / (target + "-image.txt")
    if receipt.exists() or receipt.is_symlink():
        read_regular(receipt, 1024)
        receipt.unlink()
    return receipt


def context_archive(service, target, destination):
    service = checked_directory(service)
    files = {}
    for path in sorted(service.rglob("*")):
        relative = path.relative_to(service)
        if any(part.startswith(".") or part in {"__pycache__", "rendered", "docs", "tests", "secrets", "resources"}
               for part in relative.parts):
            continue
        if (path.suffix in {".py", ".mjs"} or relative.as_posix() in {"requirements.txt", TARGETS[target]}):
            files["agent_service/" + relative.as_posix()] = read_regular(path)
    for required in ("__init__.py", "__main__.py", "requirements.txt", TARGETS[target]):
        if "agent_service/" + required not in files:
            raise BuildError("required_build_source_missing")
    if target in {'control', 'pi', 'hermes'}:
        # Only this reviewed image-owned bundle crosses the non-code allowlist.
        # Its manifest cannot add files, change the directory or load settings.
        manifest = read_regular(service / (PROFILE_RESOURCE + "manifest.json"), 16 * 1024)
        contract = read_regular(service / (PROFILE_RESOURCE + "CONTRACT.md"), 64 * 1024)
        try:
            resource = json.loads(manifest, object_pairs_hook=unique_pairs)
            if (not isinstance(resource, dict) or set(resource) != {"bundle_revision", "local_files"}
                    or resource["bundle_revision"] != "workspace-text-v1"
                    or not isinstance(resource["local_files"], list) or len(resource["local_files"]) != 1):
                raise ValueError()
            entry = resource["local_files"][0]
            if (not isinstance(entry, dict) or set(entry) != {"path", "sha256"}
                    or entry["path"] != "CONTRACT.md"
                    or entry["sha256"] != hashlib.sha256(contract).hexdigest()):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise BuildError("invalid_profile_resource_manifest") from None
        files["agent_service/" + PROFILE_RESOURCE + "manifest.json"] = manifest
        files["agent_service/" + PROFILE_RESOURCE + "CONTRACT.md"] = contract
    root = service.parent
    # Explicit framework-neutral dependencies of the reviewed collectors. No
    # business API/database/publisher, sessions or private Agent HOME is copied.
    ordinary = ['pi_worker/__init__.py', 'scientific_agent/__init__.py', 'tools/__init__.py']
    ordinary += ['scientific_agent/' + name for name in ('pi_contracts.py', 'pi_citations.py',
        'pi_sections.py', 'pi_revision_contracts.py', 'pi_revision_validation.py',
        'academic_observations.py', 'workflow_contracts.py')]
    for name in ordinary:
        files[name] = read_regular(root / name)
    for prefix in ('pi_worker/research_tools', 'tools/academic'):
        base = checked_directory(root / prefix)
        for path in sorted(base.rglob('*.py')):
            relative = path.relative_to(root)
            if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                continue
            files[relative.as_posix()] = read_regular(path)
    for name in ('manifest.json', 'LICENSE', 'task-template.md', 'research-procedure/SKILL.md'):
        files['scientific_skills/' + name] = read_regular(root / 'scientific_skills' / name)
    if len(files) > 512 or sum(map(len, files.values())) > 32 * 1024 * 1024:
        raise BuildError("build_context_too_large")
    with tarfile.open(destination, "w") as archive:
        for name, content in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(content), 0o644, 0
            archive.addfile(entry, io.BytesIO(content))


def docker_json(runner, arguments, *, limit=4 * 1024 * 1024):
    result = runner(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    try:
        if type(result.stdout) is not bytes or not 0 < len(result.stdout) <= limit:
            raise ValueError()
        return json.loads(result.stdout, object_pairs_hook=unique_pairs)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise BuildError('invalid_docker_inspection') from None


def native_platform(runner, platform):
    info = docker_json(runner, ['docker', 'info', '--format',
        '{"os":{{json .OSType}},"architecture":{{json .Architecture}}}'], limit=65536)
    aliases = {'amd64': 'amd64', 'x86_64': 'amd64', 'arm64': 'arm64', 'aarch64': 'arm64'}
    if (type(info) is not dict or info.get('os') != 'linux'
            or type(info.get('architecture')) is not str
            or 'linux/' + aliases.get(info['architecture'], '') != platform):
        raise BuildError('native_builder_platform_mismatch')


def local_image(runner, image, platform):
    info = docker_json(runner, ['docker', 'image', 'inspect', '--format',
        '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},'
        '"repo_digests":{{json .RepoDigests}}}', image], limit=128 * 1024)
    if (type(info) is not dict or type(info.get('id')) is not str
            or not SHA256.fullmatch(info['id']) or info.get('os') != 'linux'
            or info.get('architecture') != platform.split('/')[1]):
        raise BuildError('built_image_platform_mismatch')
    return info


def native_push(runner, image, name, platform):
    # The local content ID binds the inspected remote config to the image that
    # was just built. RepoDigests are checked after a successful push, never
    # treated as proof that a stale image had been published by this invocation.
    built = local_image(runner, image, platform)
    runner(['docker', 'push', image], check=True)
    remote = docker_json(runner, ['docker', 'manifest', 'inspect', '--insecure', '--verbose', image])
    published = local_image(runner, image, platform)
    if type(remote) is not dict:
        raise BuildError('single_platform_manifest_required')
    descriptor = remote.get('Descriptor')
    manifest = remote.get('SchemaV2Manifest', remote.get('OCIManifest'))
    if type(descriptor) is not dict or type(manifest) is not dict:
        raise BuildError('pushed_manifest_mismatch')
    checksum = descriptor.get('digest')
    config = manifest.get('config')
    declared_platform = descriptor.get('platform')
    if (type(checksum) is not str or not SHA256.fullmatch(checksum)
            or manifest.get('schemaVersion') != 2 or type(config) is not dict
            or config.get('digest') != built['id'] or published['id'] != built['id']
            or type(published.get('repo_digests')) is not list
            or name + '@' + checksum not in published['repo_digests']
            or (declared_platform is not None and (type(declared_platform) is not dict
                or declared_platform.get('os') != 'linux'
                or declared_platform.get('architecture') != platform.split('/')[1]))):
        raise BuildError('pushed_manifest_mismatch')
    return checksum


def build(target="control", *, push=True, service=SERVICE, environ=None, runner=None):
    if target not in TARGETS or type(push) is not bool:
        raise BuildError("invalid_build_target")
    receipt = reset_receipt(service, target)
    environ = os.environ if environ is None else environ
    runner = subprocess.run if runner is None else runner
    registry = environ.get("REGISTRY", "arm-cluster-master:5000")
    tag = environ.get("IMAGE_TAG", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    image = environ.get("IMAGE", registry + "/agent-service-" + target + ":" + tag)
    name = tagged_image(image)
    platform = environ.get("PLATFORM", "linux/arm64")
    if platform not in {"linux/arm64", "linux/amd64"}:
        raise BuildError("invalid_build_platform")
    base = immutable_image(environ.get("PYTHON_BASE_IMAGE", PYTHON_BASE))
    arguments = ["--platform", platform, "--file", "agent_service/" + TARGETS[target], "--tag", image,
                 "--build-arg", "PYTHON_BASE_IMAGE=" + base]
    if target == "pi":
        donor = immutable_image(environ.get("PI_CAGE_IMAGE"))
        arguments += ["--build-arg", "PI_CAGE_IMAGE=" + donor,
                      "--build-arg", "PI_CAGE_REVISION=" + PI_CAGE_REVISION]
    if target in {'pi', 'hermes'}:
        ppt_donor = immutable_image(environ.get('PPT_DONOR_IMAGE'))
        arguments += ['--build-arg', 'PPT_DONOR_IMAGE=' + ppt_donor]
    with tempfile.TemporaryDirectory(prefix="agent-service-build-") as temporary:
        archive, metadata = Path(temporary) / "context.tar", Path(temporary) / "metadata.json"
        context_archive(service, target, archive)
        probe = runner(['docker', 'buildx', 'version'], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=False)
        use_buildx = probe.returncode == 0
        if use_buildx:
            arguments = ['docker', 'buildx', 'build', *arguments,
                '--push' if push else '--load', '--metadata-file', str(metadata), '-']
        else:
            native_platform(runner, platform)
            arguments = ['docker', 'build', *arguments, '-']
        with archive.open("rb") as stream:
            options = dict(stdin=stream, check=True)
            if not use_buildx:
                # An inherited DOCKER_BUILDKIT=1 must not make docker build
                # demand the optional plugin that the probe just found absent.
                options['env'] = {**os.environ, 'DOCKER_BUILDKIT': '0'}
            runner(arguments, **options)
        if not push:
            return None
        if use_buildx:
            try:
                report = json.loads(read_regular(metadata, 128 * 1024), object_pairs_hook=unique_pairs)
                checksum = report["containerimage.digest"]
                if (not isinstance(checksum, str) or not SHA256.fullmatch(checksum)
                        or report.get("image.name", image) != image):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise BuildError("invalid_build_metadata") from None
            # Fetch the exact pushed tag's raw manifest. Its digest must match
            # this build's metadata rather than an unrelated/raced registry tag.
            result = runner(["docker", "buildx", "imagetools", "inspect", "--raw", image],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            raw = result.stdout
            if (type(raw) is not bytes or len(raw) > 4 * 1024 * 1024
                    or "sha256:" + hashlib.sha256(raw).hexdigest() != checksum):
                raise BuildError("pushed_manifest_mismatch")
        else:
            checksum = native_push(runner, image, name, platform)
        digest = name + "@" + checksum
        checked_directory(receipt.parent)
        pending = receipt.with_name("." + target + "-image-" + uuid.uuid4().hex + ".tmp")
        try:
            with pending.open("x", encoding="utf-8", newline="\n") as output:
                output.write(digest + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, receipt)
        finally:
            pending.unlink(missing_ok=True)
        return digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Environment: REGISTRY, IMAGE_TAG, IMAGE, PLATFORM (default linux/arm64), "
        "PYTHON_BASE_IMAGE (reviewed Python digest), PI_CAGE_IMAGE (pinned pi-cage digest required for pi), "
        "PPT_DONOR_IMAGE (reviewed PPT/Hermes runtime required for pi/hermes). "
        "A successful push writes rendered/<target>-image.txt; --no-push writes no deployable digest."))
    parser.add_argument("--target", choices=TARGETS, default="control")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build(args.target, push=not args.no_push)
        print("Published " + result if result else "Built locally; no deployable registry digest was retained.")
    except (BuildError, OSError, subprocess.SubprocessError):
        print("Agent image build failed; no deployable digest was retained.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
