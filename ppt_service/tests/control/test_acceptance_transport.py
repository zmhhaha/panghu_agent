"""Synthetic transport contracts, not evidence that a presentation passed.

No Office, model, database, network or Kubernetes process is started here.
These Linux tests exercise the controller's exact Pod-stop authority, immutable
copy and retry cache with a deliberately synthetic backend.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from agent_service.contracts import digest
from ppt_service.control.agent_validation import AgentKubernetesSandbox, _measured_tree
from ppt_service.control.execution import ControlError
from ppt_service.control.staging import atomic_json, read_json


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Acceptance transport requires Linux")
IMAGE = "registry.fixture/validator@sha256:" + "d" * 64


@pytest.fixture
def transport_case(tmp_path):
    """The synthetic receipt intentionally does not claim any completed gate."""
    project, version, evidence = (str(uuid4()) for _ in range(3))
    binding = {"project_id": project, "version_id": version, "source_revision": 2,
        "manifest_sha256": "a" * 64, "input_snapshot_sha256": "b" * 64,
        "pptx_sha256": "c" * 64, "source_fingerprint_sha256": "e" * 64,
        "evidence_id": str(uuid4()), "evidence_sha256": "f" * 64,
        "runtime": {"engine": "pi", "runtime_revision": "fixture", "profile": "ppt.generate.v1",
            "checkpoint_format": "ppt-agent-artifacts-v1"}}
    original = {"fixture_only": True, "validation_nonce": str(uuid4())}
    request = {"schema": "ppt-service.acceptance-request.v1", "evidence_id": evidence,
        "validation_nonce": evidence, "binding": binding, "job_id": str(uuid4()),
        "attempt": 1, "fencing_token": 40, "execution_image": IMAGE,
        "original_request_sha256": digest(original)}
    signature = digest(binding)
    scope = str(uuid5(NAMESPACE_URL, "ppt-acceptance:" + signature))
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    atomic_json(snapshot / "original-receipt.json", original)
    base = tmp_path / "projects" / project / "versions" / version
    state = SimpleNamespace(root=tmp_path, snapshot=snapshot, original=original, request=request,
        signature=signature, scope=scope, base=base, target=base / "acceptance-evidence" / evidence,
        attempted=tmp_path / "acceptance-attempts" / signature, runs=[], registrations=[],
        discarded=[], fault=None, output=None, runtime=None)
    pod_uid, pod_name = "synthetic-physical-pod-uid", "synthetic-validator-pod"

    def subpath(path):
        return Path(path).relative_to(tmp_path).as_posix()

    def register(uid):
        state.registrations.append(uid)
        return SimpleNamespace(uid=uid)

    def authority(handle):
        if not state.runs:
            return {"resources": []}
        row = {"resource": "pods", "name": pod_name, "api_uid": pod_uid, "deleted": True,
            "stop_receipt": {"terminated": True, "api_uid": pod_uid}}
        if state.fault == "missing_pod":
            return {"resources": []}
        if state.fault == "duplicate_pod":
            return {"resources": [row, copy.deepcopy(row)]}
        if state.fault == "missing_uid":
            row.pop("api_uid")
        if state.fault == "none_uid":
            row["api_uid"] = row["stop_receipt"]["api_uid"] = None
        if state.fault == "mismatched_uid":
            row["stop_receipt"]["api_uid"] = "different-physical-pod"
        if state.fault == "not_deleted":
            row["deleted"] = False
        if state.fault == "not_terminated":
            row["stop_receipt"]["terminated"] = False
        return {"resources": [row]}

    def run(module, mounts, envelope, filename, timeout):
        state.runs.append(module)
        assert module == "ppt_service.control.acceptance_validation"
        assert mounts == [(snapshot.absolute(), "/snapshot", True)]
        assert envelope == {"original_request": original, "acceptance_request": request}
        assert filename == "receipt.json" and timeout == 300
        root = tmp_path / "private-validator-runtime"
        output = root / "output"
        output.mkdir(parents=True)
        state.runtime, state.output = root, output
        result = {"schema": "ppt-service.acceptance-evidence.v1", "status": "pending",
            "evidence_id": request["evidence_id"], "binding_sha256": signature,
            "request_sha256": digest(request), "execution_image": IMAGE,
            "publication_authorized": False}
        if state.fault == "receipt_schema":
            result["schema"] = "untrusted"
        if state.fault == "receipt_id":
            result["evidence_id"] = str(uuid4())
        if state.fault == "receipt_binding":
            result["binding_sha256"] = "0" * 64
        if state.fault == "receipt_request":
            result["request_sha256"] = "0" * 64
        if state.fault == "receipt_image":
            result["execution_image"] = "untrusted-image"
        if state.fault == "receipt_publication":
            result["publication_authorized"] = True
        if state.fault == "missing_status":
            result.pop("status")
        if state.fault == "unknown_status":
            result["status"] = "published"
        atomic_json(output / "receipt.json", result)
        atomic_json(output / "request.json", request)
        (output / "private").mkdir()
        (output / "private/check.log").write_bytes(b"Synthetic administrator-only output\n")
        if state.fault == "persisted_receipt":
            atomic_json(output / "receipt.json", dict(result, status="needs_changes"))
        if state.fault == "persisted_request":
            atomic_json(output / "request.json", dict(request, attempt=2))
        if state.fault == "missing_receipt":
            (output / "receipt.json").unlink()
        if state.fault == "malformed_receipt":
            (output / "receipt.json").write_bytes(b"{not-json")
        if state.fault == "reserved_transport":
            atomic_json(output / "transport-manifest.json", {"transport_verified": True})
        if state.fault == "symlink":
            (output / "linked.log").symlink_to(output / "private/check.log")
        if state.fault == "fifo":
            os.mkfifo(output / "pipe")
        handle = SimpleNamespace(uid="different-private-scope" if state.fault == "scope" else scope)
        return (root, output, None if state.fault == "missing_result" else result,
            2 if state.fault == "nonzero" else 0,
            ControlError("synthetic_failure") if state.fault == "transport_failure" else None,
            state.fault != "running", handle, pod_name)

    backend = SimpleNamespace(subpath=subpath, register_sandbox_scope=register, _read=authority)
    sandbox = object.__new__(AgentKubernetesSandbox)
    sandbox.config = SimpleNamespace(data_root=tmp_path, validation_timeout=30)
    sandbox.transport = SimpleNamespace(backend=backend, settings=SimpleNamespace(worker_image=IMAGE),
        _run=run, _discard=lambda root: state.discarded.append(root))
    state.sandbox = sandbox
    return state


def invoke(case):
    return case.sandbox.validate_acceptance(case.snapshot, case.original, case.request)


def assert_no_bundle(case):
    assert not case.target.exists()
    assert not list((case.base / "acceptance-evidence").glob("*"))


def test_exact_stop_freezes_complete_roster_without_publication(transport_case):
    case = transport_case
    before = (case.snapshot / "original-receipt.json").read_bytes()
    assert invoke(case) == {"status": "acceptance_validated", "binding_sha256": case.signature}
    assert case.target.name == case.request["evidence_id"]
    assert case.target.parent.name == "acceptance-evidence"
    body = read_json(case.target, "transport-manifest.json")
    assert body["schema"] == "ppt-service.acceptance-transport.v1"
    assert body["evidence_id"] == case.request["evidence_id"]
    assert body["binding_sha256"] == case.signature
    assert body["request_sha256"] == digest(case.request)
    assert body["execution_image"] == IMAGE and body["validator_exit_code"] == 0
    # The scope is a private controller scope, not a Kubernetes Job UID.
    assert body["validator_scope_uid"] == case.scope
    assert body["validator_pod_uid"] == "synthetic-physical-pod-uid"
    assert body["writers_stopped"] is body["transport_verified"] is True
    rows, total = _measured_tree(case.target, body["files"], max_files=8192,
        exclude=("transport-manifest.json",))
    assert set(rows) == {"receipt.json", "request.json", "private/check.log"}
    assert body["bytes"] == total
    for row in rows.values():
        raw = (case.target / row["path"]).read_bytes()
        assert row["bytes"] == len(raw) and row["sha256"] == hashlib.sha256(raw).hexdigest()
    assert read_json(case.target, "receipt.json")["publication_authorized"] is False
    for path in [case.target, *case.target.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0
    assert (case.snapshot / "original-receipt.json").read_bytes() == before
    assert read_json(case.attempted.parent, case.attempted.name)["status"] == "pending"
    assert case.discarded == [case.runtime]
    assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1


@pytest.mark.parametrize("fault", ["nonzero", "transport_failure"])
def test_failed_stopped_attempt_is_private_and_not_repeated(transport_case, fault):
    case = transport_case
    case.fault = fault
    assert invoke(case)["status"] == "acceptance_failed"
    assert_no_bundle(case)
    cache = read_json(case.attempted.parent, case.attempted.name)
    assert cache["status"] == "failed" and cache["writers_stopped"] is True
    assert cache["pod_uid"] == "synthetic-physical-pod-uid"
    assert not case.discarded
    assert (case.output / "private/check.log").exists()
    assert stat.S_IMODE(case.runtime.stat().st_mode) & 0o222 == 0
    assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1


@pytest.mark.parametrize("fault", ["receipt_schema", "receipt_id", "receipt_binding", "receipt_request",
    "receipt_image", "receipt_publication", "missing_status", "unknown_status", "missing_result",
    "persisted_receipt", "persisted_request", "missing_receipt", "malformed_receipt", "reserved_transport"])
def test_stopped_malformed_output_is_cached_before_inspection(transport_case, fault):
    case = transport_case
    case.fault = fault
    with pytest.raises((ControlError, ValueError, FileNotFoundError)):
        invoke(case)
    assert_no_bundle(case)
    cache = read_json(case.attempted.parent, case.attempted.name)
    assert cache["status"] == "stopped_pending_inspection" and cache["writers_stopped"] is True
    assert not case.discarded
    assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1


@pytest.mark.parametrize("fault", ["running", "scope", "missing_pod", "duplicate_pod", "missing_uid",
    "none_uid", "mismatched_uid", "not_deleted", "not_terminated"])
def test_missing_exact_stop_cannot_mint_stop_cache_or_bundle(transport_case, fault):
    case = transport_case
    case.fault = fault
    with pytest.raises(ControlError):
        invoke(case)
    assert_no_bundle(case)
    assert not case.attempted.exists() and not case.discarded
    assert case.runtime.exists()


@pytest.mark.parametrize("fault", ["symlink", "fifo"])
def test_copy_rejects_links_and_special_files_outside_discoverable_root(transport_case, fault):
    case = transport_case
    case.fault = fault
    with pytest.raises(ControlError, match="linked_or_special_file"):
        invoke(case)
    assert_no_bundle(case)
    assert list(case.base.glob(".acceptance-pending-*"))
    assert read_json(case.attempted.parent, case.attempted.name)["status"] == "stopped_pending_inspection"
    assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1


@pytest.mark.parametrize("evidence", ["../escaped", "/tmp/escaped", "not-a-uuid", "{00000000-0000-0000-0000-000000000000}",
    "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", None])
def test_invalid_evidence_path_is_rejected_before_backend_launch(transport_case, evidence):
    case = transport_case
    case.request["evidence_id"] = case.request["validation_nonce"] = evidence
    with pytest.raises(ControlError, match="acceptance_request_binding_invalid"):
        invoke(case)
    assert not case.runs and not case.registrations and not case.attempted.exists()


@pytest.mark.parametrize("field,value", [("execution_image", "untrusted-image"),
    ("original_request_sha256", "0" * 64), ("validation_nonce", "different-nonce")])
def test_request_binding_is_checked_before_backend_launch(transport_case, field, value):
    case = transport_case
    case.request[field] = value
    with pytest.raises(ControlError, match="acceptance_request_binding_invalid"):
        invoke(case)
    assert not case.runs and not case.registrations and not case.attempted.exists()


@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_complete_frozen_roster_detects_later_tampering(transport_case, change):
    case = transport_case
    invoke(case)
    body = read_json(case.target, "transport-manifest.json")
    if change == "added":
        case.target.chmod(0o700)
        (case.target / "undeclared.log").write_bytes(b"Added after freeze")
    else:
        parent = case.target / "private"
        parent.chmod(0o700)
        path = parent / "check.log"
        path.chmod(0o600)
        if change == "changed":
            path.write_bytes(b"Changed after freeze")
        else:
            path.unlink()
    with pytest.raises(ControlError):
        _measured_tree(case.target, body["files"], max_files=8192,
            exclude=("transport-manifest.json",))
