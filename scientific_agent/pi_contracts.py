"""Versioned, framework-neutral contracts shared by the scientific API and Pi worker.

No imports of CrewAI, FastAPI, database clients, or environment configuration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping
from uuid import UUID

SCHEMA_VERSION = "1"
MAX_SAFE_INTEGER = 2**53 - 1
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_JOB_BYTES = 16 * 1024
ACTIVE_STATES = frozenset({"queued", "running", "cancel_requested", "validating", "publishing"})
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
STAGES = frozenset({"search", "screen", "extract", "synthesize", "write", "validate", "publish"})
_HEX = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def to_dict(self, task_id: str | None = None) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "retryable": self.retryable, "task_id": task_id}}


def _canonical_value(value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ContractError("invalid_unicode", "Unpaired surrogate is not allowed")
        return
    if type(value) is int and abs(value) <= MAX_SAFE_INTEGER:
        return
    if isinstance(value, list):
        for item in value:
            _canonical_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("invalid_json", "Object keys must be strings")
            _canonical_value(key)
            _canonical_value(item)
        return
    raise ContractError("invalid_json", "CanonicalJSON accepts only safe integers and JSON values")


def canonical_json(value: Any) -> str:
    _canonical_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def input_digest(payload: Mapping[str, Any]) -> str:
    try:
        selected = {key: payload[key] for key in ("topic", "user_id", "engine", "mode", "timeout_seconds")}
    except KeyError as exc:
        raise ContractError("invalid_input", f"Missing input field: {exc.args[0]}") from exc
    return _sha(canonical_json(selected))


def report_sha256(report_markdown: str) -> str:
    if not isinstance(report_markdown, str):
        raise ContractError("invalid_report", "Report must be a string")
    _canonical_value(report_markdown)
    return _sha(report_markdown)


def validate_digest(value: Any, field: str = "digest") -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ContractError("invalid_digest", f"{field} must be 64 lowercase hexadecimal characters")
    return value


def validate_task_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ContractError("invalid_task_id", "Task identifier must be a canonical UUID")
    try:
        if str(UUID(value)) != value:
            raise ValueError("Noncanonical UUID")
    except (ValueError, AttributeError) as exc:
        raise ContractError("invalid_task_id", "Task identifier must be a canonical UUID") from exc
    return value


def public_status(state: str) -> str:
    if state == "queued":
        return "pending"
    if state in ACTIVE_STATES:
        return "running"
    if state == "succeeded":
        return "done"
    if state in TERMINAL_STATES:
        return "failed"
    raise ContractError("invalid_state", "Unknown task state")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    try:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("Not RFC3339")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError("Timestamp must be UTC")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError("invalid_timestamp", "Timestamp must be RFC3339 in UTC") from exc


def result_envelope(result: Mapping[str, Any]) -> dict:
    """Select the immutable envelope; do not hash report text, manifest, or digest recursively."""
    try:
        task_id = validate_task_id(result["task_id"])
        attempt = result["attempt"]
        if type(attempt) is not int or not 1 <= attempt <= MAX_SAFE_INTEGER:
            raise ContractError("invalid_attempt", "Attempt must be a positive integer")
        if result["schema_version"] != SCHEMA_VERSION:
            raise ContractError("invalid_schema", "Unsupported result schema")
        artifacts = []
        seen = set()
        for raw in result["artifacts"]:
            item = {key: raw[key] for key in ("artifact_id", "media_type", "size", "sha256")}
            identifier = item["artifact_id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier):
                raise ContractError("invalid_artifact", "Artifact identifier is invalid")
            if identifier in seen:
                raise ContractError("invalid_artifact", "Duplicate artifact identifier")
            seen.add(identifier)
            if type(item["size"]) is not int or not 0 <= item["size"] <= MAX_ARTIFACT_BYTES:
                raise ContractError("invalid_artifact", "Artifact size is invalid")
            if not isinstance(item["media_type"], str) or not item["media_type"]:
                raise ContractError("invalid_artifact", "Artifact media type is required")
            validate_digest(item["sha256"], "artifact sha256")
            artifacts.append(item)
        provenance = result["provenance"]
        if not isinstance(provenance, dict):
            raise ContractError("invalid_provenance", "Provenance must be an object")
        envelope = {"schema_version": SCHEMA_VERSION, "task_id": task_id, "attempt": attempt,
                    "report_sha256": validate_digest(result["report_sha256"], "report_sha256"),
                    "artifacts": sorted(artifacts, key=lambda item: item["artifact_id"]),
                    "provenance": provenance}
        canonical_json(envelope)
        return envelope
    except (KeyError, TypeError) as exc:
        raise ContractError("invalid_result", "Result envelope is missing or has malformed fields") from exc


def result_digest(envelope: Mapping[str, Any]) -> str:
    return _sha(canonical_json(result_envelope(envelope)))


@dataclass(frozen=True)
class ResearchJob:
    schema_version: str
    task_id: str
    input_digest: str
    topic: str
    user_id: str
    engine: str
    mode: str
    timeout_seconds: int
    created_at: str
    execution_deadline_at: str
    skill_bundle_revision: str
    runtime_revision: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchJob":
        if not isinstance(payload, dict):
            raise ContractError("invalid_job", "Job must be an object")
        expected = set(cls.__dataclass_fields__)
        if set(payload) != expected:
            missing, unknown = sorted(expected - set(payload)), sorted(set(payload) - expected)
            raise ContractError("invalid_job", f"Invalid job fields; missing={missing}, unknown={unknown}")
        if len(canonical_json(payload).encode("utf-8")) > MAX_JOB_BYTES:
            raise ContractError("job_too_large", "Job exceeds 16 KiB", 413)
        if payload["schema_version"] != SCHEMA_VERSION or payload["engine"] != "pi":
            raise ContractError("invalid_job", "Expected schema version 1 and engine pi")
        validate_task_id(payload["task_id"])
        if not isinstance(payload["topic"], str) or not 1 <= len(payload["topic"]) <= 500 or payload["topic"] != payload["topic"].strip():
            raise ContractError("invalid_topic", "Topic must be trimmed and contain 1 to 500 characters")
        if not isinstance(payload["user_id"], str) or not payload["user_id"].strip() or len(payload["user_id"]) > 200:
            raise ContractError("invalid_user", "Pi jobs require a trusted nonempty user identifier")
        if payload["mode"] not in ("brief", "review"):
            raise ContractError("invalid_mode", "Unknown research mode")
        timeout = payload["timeout_seconds"]
        if type(timeout) is not int or not 60 <= timeout <= 3600:
            raise ContractError("invalid_timeout", "Execution timeout must be an integer from 60 to 3600")
        created, deadline = parse_timestamp(payload["created_at"]), parse_timestamp(payload["execution_deadline_at"])
        duration = (deadline - created).total_seconds()
        if duration <= 0 or duration > timeout:
            raise ContractError("invalid_deadline", "Deadline must follow creation within the execution timeout")
        for key in ("skill_bundle_revision", "runtime_revision"):
            if not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 200:
                raise ContractError("invalid_revision", f"{key} is required")
        if validate_digest(payload["input_digest"], "input_digest") != input_digest(payload):
            raise ContractError("input_digest_mismatch", "Input digest does not match the immutable request")
        return cls(**payload)

    def to_dict(self) -> dict:
        return asdict(self)


def contract_schema() -> dict:
    """JSON Schema for the wire job. Semantic/digest checks remain in from_dict."""
    strings = {name: {"type": "string"} for name in ResearchJob.__dataclass_fields__}
    strings.update({"schema_version": {"const": "1"}, "engine": {"const": "pi"},
                    "mode": {"enum": ["brief", "review"]},
                    "timeout_seconds": {"type": "integer", "minimum": 60, "maximum": 3600},
                    "task_id": {"type": "string", "format": "uuid"},
                    "input_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"}})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "ResearchJob v1",
            "type": "object", "additionalProperties": False,
            "required": list(ResearchJob.__dataclass_fields__), "properties": strings}
