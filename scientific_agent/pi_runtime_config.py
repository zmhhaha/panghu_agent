"""Trusted image/environment configuration for the isolated scientific Pi runtime.

User job payloads cannot select commands, providers, credentials, skills or readiness.
This module does not read the human Pi HOME or enable fixture execution from env.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pi_worker.runtime import PiRuntime, RuntimeSettings

RUNTIME_REVISION = "pi-jsonl-0.73.1-v1"
BUNDLE_MANIFEST_SHA256 = "fbfc5770ae54f88c7146e94a3845c7ccb480b45137f308c7fc4237dd78803645"
SCIENTIFIC_GATEWAY_COMPLETIONS_COMPAT = {
    "supportsStore": False,
    "supportsUsageInStreaming": False,
    "supportsReasoningEffort": False,
    "maxTokensField": "max_tokens",
}


def _verified(name: str) -> bool:
    value = os.environ.get(name, "0")
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1")
    return value == "1"


def runtime_from_env(runtime_dir: Path) -> PiRuntime:
    root = Path(runtime_dir).resolve()
    control = root / "control"
    control.mkdir(parents=True, exist_ok=True, mode=0o700)
    if control.is_symlink():
        raise ValueError("Control directory cannot be a symbolic link")
    if os.name == "posix":
        control.chmod(0o700)
    sentinel = control / "isolation-sentinel"
    if not sentinel.exists():
        descriptor = os.open(sentinel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("Control files must not be readable by scientific task processes.\n")
    if sentinel.is_symlink() or not sentinel.is_file():
        raise ValueError("Invalid control isolation sentinel")
    if os.name == "posix":
        sentinel.chmod(0o600)
    source_root = Path(__file__).resolve().parent.parent
    bundle_root = source_root / "scientific_skills"
    manifest_path = bundle_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = tuple(bundle_root / value for value in manifest["skills"])
    hashes = {item["path"]: item["sha256"] for item in manifest["local_files"]}
    credentials = {}
    if os.environ.get("SCIENTIFIC_PI_MODEL_API_KEY"):
        credentials["SCIENTIFIC_MODEL_API_KEY"] = os.environ["SCIENTIFIC_PI_MODEL_API_KEY"]
    settings = dict(
        command=("/usr/local/bin/node", "/opt/pi/node_modules/@mariozechner/pi-coding-agent/dist/cli.js"),
        node_command=("/usr/local/bin/node",),
        provider=os.environ.get("SCIENTIFIC_PI_MODEL_PROVIDER", "scientific-worker"),
        model=os.environ.get("SCIENTIFIC_PI_MODEL", ""),
        skill_paths=selected,
        skill_hashes=tuple(hashes[value] for value in manifest["skills"]),
        bundle_manifest=manifest_path,
        bundle_manifest_sha256=BUNDLE_MANIFEST_SHA256,
        skill_bundle_revision=manifest["bundle_revision"],
        runtime_revision=os.environ.get("SCIENTIFIC_PI_RUNTIME_REVISION", RUNTIME_REVISION),
        image_digest=os.environ.get("SCIENTIFIC_PI_IMAGE_DIGEST") or None,
        model_env=credentials,
        tool_pythonpath=source_root,
        fixture_mode=False,
        compact_events=True,
        capability_verified=_verified("SCIENTIFIC_PI_CAPABILITY_VERIFIED"),
        model_verified=_verified("SCIENTIFIC_PI_MODEL_VERIFIED"),
        isolation_verified=_verified("SCIENTIFIC_PI_ISOLATION_VERIFIED"),
        control_dir=control,
        denied_paths=(sentinel,),
        run_uid_start=200000,
        run_gid=200000,
    )
    # Explicit custom-provider configuration is supported by the reviewed adapter;
    # the key is referenced by environment name, not copied into models.json.
    settings["model_base_url"] = os.environ.get("SCIENTIFIC_PI_MODEL_BASE_URL") or None
    settings["model_api"] = os.environ.get("SCIENTIFIC_PI_MODEL_API", "openai-completions")
    profile = os.environ.get("SCIENTIFIC_PI_MODEL_COMPAT_PROFILE",
                             "scientific-gateway" if settings["model_api"] == "openai-completions" else "none")
    if profile not in {"scientific-gateway", "none"}:
        raise ValueError("Unsupported trusted model compatibility profile")
    if profile == "scientific-gateway" and settings["model_api"] != "openai-completions":
        raise ValueError("Scientific gateway completions profile requires openai-completions")
    # Reviewed direct endpoints may explicitly clear this profile; no arbitrary
    # compatibility JSON or job-selected model parameters are accepted.
    settings["model_compat"] = dict(SCIENTIFIC_GATEWAY_COMPLETIONS_COMPAT) if profile == "scientific-gateway" else {}
    settings["model_max_tokens"] = 8192
    return PiRuntime(RuntimeSettings(**settings))
