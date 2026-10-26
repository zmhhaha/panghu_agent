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


SERVICE = Path(__file__).resolve().parent
PYTHON_BASE = "python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f"
TARGETS = {"control": "Dockerfile.control", "pi": "Dockerfile.agent-pi"}
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
    if target == "pi":
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
    if len(files) > 256 or sum(map(len, files.values())) > 16 * 1024 * 1024:
        raise BuildError("build_context_too_large")
    with tarfile.open(destination, "w") as archive:
        for name, content in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(content), 0o644, 0
            archive.addfile(entry, io.BytesIO(content))


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
    arguments = ["docker", "buildx", "build", "--platform", platform,
                 "--file", "agent_service/" + TARGETS[target], "--tag", image,
                 "--build-arg", "PYTHON_BASE_IMAGE=" + base]
    if target == "pi":
        donor = immutable_image(environ.get("PI_DONOR_IMAGE"))
        arguments += ["--build-arg", "PI_DONOR_IMAGE=" + donor]
    with tempfile.TemporaryDirectory(prefix="agent-service-build-") as temporary:
        archive, metadata = Path(temporary) / "context.tar", Path(temporary) / "metadata.json"
        context_archive(service, target, archive)
        arguments += ["--push" if push else "--load", "--metadata-file", str(metadata), "-"]
        with archive.open("rb") as stream:
            runner(arguments, stdin=stream, check=True)
        if not push:
            return None
        try:
            report = json.loads(read_regular(metadata, 128 * 1024), object_pairs_hook=unique_pairs)
            checksum = report["containerimage.digest"]
            if (not isinstance(checksum, str) or not SHA256.fullmatch(checksum)
                    or report.get("image.name", image) != image):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise BuildError("invalid_build_metadata") from None
        # Fetch the exact pushed tag's raw manifest. Its digest must match the
        # build metadata, so an unrelated repository digest or raced tag cannot
        # become the deployment receipt.
        result = runner(["docker", "buildx", "imagetools", "inspect", "--raw", image],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        raw = result.stdout
        if (type(raw) is not bytes or len(raw) > 4 * 1024 * 1024
                or "sha256:" + hashlib.sha256(raw).hexdigest() != checksum):
            raise BuildError("pushed_manifest_mismatch")
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
        "PYTHON_BASE_IMAGE (reviewed Python digest), PI_DONOR_IMAGE (required for pi). "
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
