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

from agent_service.contracts import canonical_bytes, digest
from ppt_service.control.agent_validation import AgentKubernetesSandbox, _measured_tree, _acceptance_runtime_scope
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
        "pptx_sha256": hashlib.sha256(b"Synthetic layout source").hexdigest(), "source_fingerprint_sha256": "e" * 64,
        "evidence_id": str(uuid4()), "evidence_sha256": "f" * 64,
        "runtime": {"engine": "pi", "runtime_revision": "fixture", "profile": "ppt.generate.v1",
            "checkpoint_format": "ppt-agent-artifacts-v1"}}
    original = {"fixture_only": True, "validation_nonce": str(uuid4())}
    request = {"schema": "ppt-service.acceptance-request.v1", "evidence_id": evidence,
        "validation_nonce": evidence, "binding": binding, "job_id": str(uuid4()),
        "attempt": 1, "fencing_token": 40, "execution_image": IMAGE,
        "original_request_sha256": digest(original)}
    signature = digest(binding)
    legacy_scope = str(uuid5(NAMESPACE_URL, "ppt-acceptance:" + signature))
    runtime_pins = {"execution_image": IMAGE, "namespace": "ppt-service", "pvc_name": "ppt-data",
        "pvc_data_prefix": "state", "worker_service_account": "ppt-worker",
        "provider_secret": "ppt-model-upstream", "provider_secret_key": "PPT_UPSTREAM_API_KEY"}
    scope = _acceptance_runtime_scope(signature, runtime_pins)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    atomic_json(snapshot / "original-receipt.json", original)
    base = tmp_path / "projects" / project / "versions" / version
    state = SimpleNamespace(root=tmp_path, snapshot=snapshot, original=original, request=request,
        signature=signature, scope=scope, legacy_scope=legacy_scope, pins=runtime_pins,
        index=tmp_path / "acceptance-scopes" / (signature + ".json"), scopes={},
        base=base, target=base / "acceptance-evidence" / evidence,
        attempted=tmp_path / "acceptance-attempts" / signature, runs=[], registrations=[],
        discarded=[], fault=None, output=None, runtime=None, layout_output=False,
        layout_pod_uid=str(uuid4()), layout_fault=None)
    pod_uid, pod_name = "synthetic-physical-pod-uid", "synthetic-validator-pod"

    def subpath(path):
        return Path(path).relative_to(tmp_path).as_posix()

    def register(uid):
        state.registrations.append(uid)
        state.scopes[uid] = SimpleNamespace(uid=uid)
        return state.scopes[uid]

    def handle_for_scope(uid):
        if uid not in state.scopes:
            raise FileNotFoundError(uid)
        return state.scopes[uid]

    def authority(handle):
        if not state.runs:
            return {"resources": [], "launch_not_started": True,
                "sandbox_only": True, "runtime": runtime_pins}
        if state.fault in ("launch_rejected", "launch_uncertain", "launch_missing_flag", "launch_unexplained"):
            record = {"resources": [{"resource": "networkpolicies", "name": pod_name + "-net", "deleted": True}],
                "launch_not_started": state.fault != "launch_uncertain"}
            if state.fault == "launch_missing_flag":
                record.pop("launch_not_started")
            return record
        observed_uid = state.layout_pod_uid if state.layout_output else pod_uid
        row = {"resource": "pods", "name": pod_name, "api_uid": observed_uid, "deleted": True,
            "stop_receipt": {"terminated": True, "api_uid": observed_uid}}
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
        registry = read_json(state.index.parent, state.index.name)
        assert registry["scopes"] == [{"scope_uid": scope, "runtime": runtime_pins}]
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
        if state.layout_output:
            (output / "receipt.json").unlink()
            (output / "request.json").write_bytes(canonical_bytes(request))
            atomic_json(output / "original-validator-request.json", original)
            raw_pptx, raw_pdf = b"Synthetic layout source", b"%PDF-1.5\nSynthetic final Office PDF only"
            native = output / "final-pptx/input/presentation.pptx"
            native.parent.mkdir(parents=True)
            native.write_bytes(raw_pptx)
            pdf = output / "final-pptx/pdf/presentation.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(raw_pdf)
            report = {"schema": "ppt-service.final-text-layout.v1", "status": "failed",
                "binding": {"controller_nonce": request["validation_nonce"],
                    "source_manifest_sha256": binding["manifest_sha256"],
                    "input_snapshot_sha256": binding["input_snapshot_sha256"],
                    "pptx_sha256": binding["pptx_sha256"], "pdf_sha256": hashlib.sha256(raw_pdf).hexdigest()},
                "publication_authorized": False, "human_visual_acceptance": False, "powerpoint_acceptance": "pending",
                "method": "final-pdf-glyphs-and-native-pptx-frames", "scope": "conservative_text_geometry_checks_only",
                "engine": {"name": "PyMuPDF", "version": "synthetic"}, "issue_count": 1, "issues_truncated": False,
                "issues": [{"code": "short_token_orphan_fragment", "severity": "error", "page": 2, "shape_id": 17,
                    "message": "private source text /private/path"}],
                "pages": [{"ordinal": n, "issue_count": int(n == 2)} for n in range(1, 4)]}
            if state.layout_fault == "nonce": report["binding"]["controller_nonce"] = str(uuid4())
            if state.layout_fault == "page": report["issues"][0]["page"] = 99
            if state.layout_fault == "pptx": native.write_bytes(b"different deck")
            if state.layout_fault == "code": report["issues"][0]["code"] = "candidate_controlled_rule"
            atomic_json(output / "final-pptx/text-layout.json", report)
            atomic_json(output / "acceptance-error.json", {"schema": "ppt-service.acceptance-error.v1",
                "publication_authorized": False, "error_code": "final_pptx_text_layout_failed", "error_type": "ControlError"})
        handle = SimpleNamespace(uid="different-private-scope" if state.fault == "scope" else scope)
        from ppt_service.control.kubernetes_sandbox import _MissingSandboxResult
        return (root, output, None if state.fault == "missing_result" or state.layout_output else result,
            2 if state.fault == "nonzero" or state.layout_output else 0,
            (ControlError("unapproved_kubernetes_entrypoint")
                if state.fault in ("launch_rejected", "launch_uncertain", "launch_missing_flag")
                else ControlError("synthetic_failure") if state.fault == "transport_failure"
                else _MissingSandboxResult("receipt.json") if state.layout_output else None),
            state.fault != "running", handle, pod_name)

    backend = SimpleNamespace(subpath=subpath, register_sandbox_scope=register, _read=authority,
        runtime_pins=lambda: copy.deepcopy(runtime_pins), handle_for_scope=handle_for_scope,
        prove_sandbox_scope_absent=lambda uid: True,
        prove_stopped=lambda h: SimpleNamespace(uid=h.uid, all_writers_stopped=True),
        remove_sandbox_policy=lambda *args: None)
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


def real_acceptance_sandbox(case, image, api):
    from ppt_service.control.execution import RuntimeConfig
    from ppt_service.control.kubernetes import KubeSettings
    settings = KubeSettings(worker_image=image, stop_timeout=0.1, poll_seconds=0.001)
    return AgentKubernetesSandbox(RuntimeConfig(case.root), settings, client=api)


def install_acceptance_api_result(case, api, *, before_pod=None, exit_code=0):
    """Only synthetic output; real backend lifecycle code observes the API."""
    def on_pod(obj):
        container = obj["spec"]["containers"][0]
        assert container["args"] == ["-m", "ppt_service.control.acceptance_validation"]
        if before_pod:
            before_pod(obj)
        mounts = {row["mountPath"]: row for row in container["volumeMounts"]}
        prefix = case.sandbox.transport.settings.pvc_data_prefix + "/"
        paths = {}
        for name in ("/request", "/output"):
            assert mounts[name]["subPath"].startswith(prefix)
            paths[name] = case.root / mounts[name]["subPath"][len(prefix):]
        envelope = read_json(paths["/request"], "request.json")
        request = envelope["acceptance_request"]
        assert request == case.request
        # The registry must already be durable and reference a real journal at
        # the point the actual backend reaches the Pod POST.
        backend = case.sandbox.transport.backend
        scope = _acceptance_runtime_scope(case.signature, backend.runtime_pins())
        registry = read_json(case.index.parent, case.index.name)
        assert {"scope_uid": scope, "runtime": backend.runtime_pins()} in registry["scopes"]
        assert backend.handle_for_scope(scope).uid == scope
        atomic_json(paths["/output"] / "request.json", request)
        atomic_json(paths["/output"] / "receipt.json", {
            "schema": "ppt-service.acceptance-evidence.v1", "status": "pending",
            "evidence_id": request["evidence_id"], "binding_sha256": case.signature,
            "request_sha256": digest(request), "execution_image": container["image"],
            "publication_authorized": False})
        api.terminal(obj, exit_code=exit_code)
    api.on_pod = on_pod


def index_existing_scope(case, backend, handle):
    from ppt_service.control.agent_validation import _save_acceptance_scope_index
    case.index.parent.mkdir(mode=0o700, exist_ok=True)
    _save_acceptance_scope_index(case.index, case.signature,
        [{"scope_uid": handle.uid, "runtime": backend.runtime_pins()}])


@pytest.mark.parametrize("kind", ["legacy", "indexed_runtime"])
def test_new_image_recovers_prelaunch_scope_without_repinning_its_journal(transport_case, kind):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    old_image = "registry.fixture/validator@sha256:" + "1" * 64
    old = real_acceptance_sandbox(case, old_image, api).transport.backend
    old_uid = (case.legacy_scope if kind == "legacy"
        else _acceptance_runtime_scope(case.signature, old.runtime_pins()))
    handle = old.register_sandbox_scope(old_uid)
    policy = "prelaunch-stale-policy"
    old.create_resource(handle, "networkpolicies", old.policy(handle, policy, "sandbox"), sandbox=True)
    if kind == "indexed_runtime":
        index_existing_scope(case, old, handle)
    identity, runtime = copy.deepcopy(old._read(handle)["identity"]), copy.deepcopy(old._read(handle)["runtime"])
    case.base.mkdir(parents=True)
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)
    install_acceptance_api_result(case, api)
    assert invoke(case)["status"] == "acceptance_validated"
    preserved = old._read(handle)
    assert preserved["identity"] == identity and preserved["runtime"] == runtime
    assert preserved["launch_not_started"] is True and old._path(handle.uid).exists()
    assert next(row for row in preserved["resources"] if row["name"] == policy)["deleted"] is True
    assert ("networkpolicies", policy) not in api.objects
    assert all(call[3]["spec"]["containers"][0]["image"] == IMAGE
        for call in api.calls if call[:2] == ("POST", "pods"))
    assert (case.root / "agent-validator-locks" / (case.legacy_scope + ".lock")).exists()


@pytest.mark.parametrize("kind", ["legacy", "indexed_runtime"])
def test_old_image_pod_is_physically_stopped_before_new_runtime_post(transport_case, kind):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    old = real_acceptance_sandbox(case, "registry.fixture/validator@sha256:" + "1" * 64, api).transport.backend
    uid = case.legacy_scope if kind == "legacy" else _acceptance_runtime_scope(case.signature, old.runtime_pins())
    handle = old.register_sandbox_scope(uid)
    old_name = "old-runtime-validator"
    body = old.pod(handle, old_name, "sandbox", "ppt_service.control.acceptance_validation",
        [(case.snapshot, "/snapshot", True)])
    old.create_resource(handle, "pods", body, sandbox=True)
    old_api_uid = old._read(handle)["resources"][0]["api_uid"]
    if kind == "indexed_runtime":
        index_existing_scope(case, old, handle)
    case.base.mkdir(parents=True)
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)

    def before_new_post(obj):
        assert ("pods", old_name) not in api.objects
        row = old._read(handle)["resources"][0]
        assert row["deleted"] is True and row["stop_receipt"]["terminated"] is True
        assert row["api_uid"] == row["stop_receipt"]["api_uid"] == old_api_uid
        assert obj["spec"]["containers"][0]["image"] == IMAGE
    install_acceptance_api_result(case, api, before_pod=before_new_post)
    assert invoke(case)["status"] == "acceptance_validated"
    posts = [i for i, call in enumerate(api.calls) if call[:2] == ("POST", "pods")]
    stop = next(i for i, call in enumerate(api.calls)
        if call[:3] == ("DELETE", "pods", old_name))
    assert len(posts) == 2 and posts[0] < stop < posts[1]
    assert old._read(handle)["runtime"]["execution_image"] != IMAGE


@pytest.mark.parametrize("kind", ["legacy", "indexed_runtime"])
def test_missing_old_pod_without_stop_receipt_blocks_new_image(transport_case, kind):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    old = real_acceptance_sandbox(case, "registry.fixture/validator@sha256:" + "1" * 64, api).transport.backend
    uid = case.legacy_scope if kind == "legacy" else _acceptance_runtime_scope(case.signature, old.runtime_pins())
    handle = old.register_sandbox_scope(uid)
    old.create_resource(handle, "pods", old.pod(handle, "missing-validator", "sandbox",
        "ppt_service.control.acceptance_validation", [(case.snapshot, "/snapshot", True)]), sandbox=True)
    if kind == "indexed_runtime":
        index_existing_scope(case, old, handle)
    del api.objects["pods", "missing-validator"]
    case.base.mkdir(parents=True)
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)
    with pytest.raises(ControlError, match="execution_writer_missing"):
        invoke(case)
    assert_no_bundle(case)
    assert not case.attempted.exists()
    assert len([call for call in api.calls if call[:2] == ("POST", "pods")]) == 1
    assert "stop_receipt" not in old._read(handle)["resources"][0]


def test_index_survives_restart_before_launch_and_recovers_prior_runtime(transport_case):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    case.base.mkdir(parents=True)
    old_image = "registry.fixture/validator@sha256:" + "1" * 64
    first = real_acceptance_sandbox(case, old_image, api)
    old_runtime = first.transport.backend.runtime_pins()
    old_scope = _acceptance_runtime_scope(case.signature, old_runtime)
    case.request["execution_image"] = old_image
    case.sandbox = first

    def restart_before_post(*args, **kwargs):
        assert read_json(case.index.parent, case.index.name)["scopes"] == [
            {"scope_uid": old_scope, "runtime": old_runtime}]
        assert first.transport.backend.handle_for_scope(old_scope).uid == old_scope
        raise RuntimeError("Synthetic controller restart before Pod POST")
    first.transport._run = restart_before_post
    with pytest.raises(RuntimeError, match="Synthetic controller restart"):
        invoke(case)
    assert not case.attempted.exists() and not any(call[0] == "POST" for call in api.calls)
    # A fresh backend/controller reads the persisted index, not in-memory state.
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)
    case.request["execution_image"] = IMAGE
    install_acceptance_api_result(case, api)
    assert invoke(case)["status"] == "acceptance_validated"
    rows = read_json(case.index.parent, case.index.name)["scopes"]
    new_runtime = case.sandbox.transport.backend.runtime_pins()
    assert rows == [{"scope_uid": old_scope, "runtime": old_runtime},
        {"scope_uid": _acceptance_runtime_scope(case.signature, new_runtime), "runtime": new_runtime}]
    assert first.transport.backend.handle_for_scope(old_scope).uid == old_scope


def test_binding_cache_prevents_failed_attempt_retry_after_image_change(transport_case):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    case.base.mkdir(parents=True)
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)
    install_acceptance_api_result(case, api, exit_code=2)
    assert invoke(case)["status"] == "acceptance_failed"
    before = copy.deepcopy(api.calls)
    newer = "registry.fixture/validator@sha256:" + "2" * 64
    case.sandbox = real_acceptance_sandbox(case, newer, api)
    case.request["execution_image"] = newer
    assert invoke(case)["status"] == "already_attempted"
    assert api.calls == before and len(read_json(case.index.parent, case.index.name)["scopes"]) == 1
    assert_no_bundle(case)


@pytest.mark.parametrize("fault", ["foreign_uid", "duplicate", "missing_journal", "pins_mismatch", "too_many",
    "file_symlink", "directory_symlink", "file_hardlink", "partial_index", "malformed_json"])
def test_registry_never_forgets_or_substitutes_historical_authority(transport_case, fault):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    case.base.mkdir(parents=True)
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)
    backend = case.sandbox.transport.backend
    pins = backend.runtime_pins()
    scope = _acceptance_runtime_scope(case.signature, pins)
    if fault != "missing_journal":
        backend.register_sandbox_scope(scope)
    row = {"scope_uid": scope, "runtime": copy.deepcopy(pins)}
    if fault == "foreign_uid":
        row["scope_uid"] = str(uuid4())
    if fault == "pins_mismatch":
        row["runtime"]["pvc_name"] = "different-data"
        row["scope_uid"] = _acceptance_runtime_scope(case.signature, row["runtime"])
        backend.register_sandbox_scope(row["scope_uid"])
    rows = [row, copy.deepcopy(row)] if fault == "duplicate" else [row] * 33 if fault == "too_many" else [row]
    case.index.parent.mkdir(mode=0o700)
    atomic_json(case.index, {"schema": "ppt-service.acceptance-scopes.v1",
        "binding_sha256": case.signature, "scopes": rows})
    if fault == "partial_index":
        atomic_json(case.index, {"schema": "ppt-service.acceptance-scopes.v1", "binding_sha256": case.signature})
    if fault == "malformed_json":
        case.index.write_bytes(b'{"schema":"ppt-service.acceptance-scopes.v1",')
    if fault == "file_hardlink":
        os.link(case.index, case.root / "index-linked.json")
    if fault == "file_symlink":
        real = case.root / "index-original.json"
        case.index.rename(real)
        case.index.symlink_to(real)
    if fault == "directory_symlink":
        real = case.root / "index-original-directory"
        case.index.parent.rename(real)
        case.index.parent.symlink_to(real, target_is_directory=True)
    with pytest.raises((ControlError, OSError, ValueError)):
        invoke(case)
    assert_no_bundle(case)
    assert not case.attempted.exists() and not any(call[0] == "POST" for call in api.calls)


def test_scope_index_byte_limit_preserves_existing_history(transport_case):
    from ppt_service.control.agent_validation import _save_acceptance_scope_index
    case = transport_case
    case.index.parent.mkdir(mode=0o700)
    _save_acceptance_scope_index(case.index, case.signature,
        [{"scope_uid": case.scope, "runtime": case.pins}])
    original = case.index.read_bytes()
    # Row/string count limits alone do not bound a UTF-8 document's bytes.
    pins = {key: "\U0001f9ea" * 512 for key in case.pins}
    rows = [{"scope_uid": _acceptance_runtime_scope(case.signature, dict(pins, namespace=str(n))),
        "runtime": dict(pins, namespace=str(n))} for n in range(32)]
    with pytest.raises(ControlError, match="acceptance_scope_registry_limit"):
        _save_acceptance_scope_index(case.index, case.signature, rows)
    assert case.index.read_bytes() == original


@pytest.mark.parametrize("prefix", ["ppt-service.panghuer.top/", "ppt.panghuer.top/"])
def test_absent_legacy_journal_never_hides_untracked_pod_under_either_metadata_scheme(transport_case, prefix):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    case.base.mkdir(parents=True)
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)
    api.objects["pods", "untracked-old-validator"] = {"metadata": {
        "name": "untracked-old-validator", "labels": {prefix + "attempt": case.legacy_scope}}}
    with pytest.raises(ControlError, match="acceptance_scope_authority_missing"):
        invoke(case)
    assert not case.sandbox.transport.backend._path(case.legacy_scope).exists()
    assert not case.attempted.exists() and not any(call[0] == "POST" for call in api.calls)
    assert_no_bundle(case)


def test_absent_legacy_scope_requires_complete_api_inventory(transport_case, monkeypatch):
    from test_kubernetes import FakeAPI
    case, api = transport_case, FakeAPI()
    case.base.mkdir(parents=True)
    case.sandbox = real_acceptance_sandbox(case, IMAGE, api)
    request = api.request

    def incomplete(method, resource, name=None, body=None, query=None):
        if method == "GET" and resource == "pods" and name is None:
            return {"metadata": {"continue": "unread-next-page"}, "items": []}
        return request(method, resource, name, body, query)
    monkeypatch.setattr(api, "request", incomplete)
    with pytest.raises(ControlError, match="incomplete_execution_inventory"):
        invoke(case)
    assert not case.sandbox.transport.backend._path(case.legacy_scope).exists()
    assert not case.attempted.exists() and not any(call[0] == "POST" for call in api.calls)
    assert_no_bundle(case)


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


def test_acceptance_uses_real_backend_entrypoint_journal_and_exact_stop(transport_case):
    """API-shaped observations exercise the actual backend and _run together."""
    from test_kubernetes import FakeAPI
    from ppt_service.control.execution import RuntimeConfig
    from ppt_service.control.kubernetes import KubeSettings

    case = transport_case
    config = RuntimeConfig(case.root)
    settings = KubeSettings(worker_image=IMAGE, stop_timeout=0.1, poll_seconds=0.001)
    api = FakeAPI()
    case.base.mkdir(parents=True)
    case.sandbox = AgentKubernetesSandbox(config, settings, client=api)
    case.scope = _acceptance_runtime_scope(case.signature, case.sandbox.transport.backend.runtime_pins())

    def observe_pod(obj):
        container = obj["spec"]["containers"][0]
        assert container["args"] == ["-m", "ppt_service.control.acceptance_validation"]
        mounts = {row["mountPath"]: row for row in container["volumeMounts"]}
        assert mounts["/snapshot"]["readOnly"] is True
        assert mounts["/request"]["readOnly"] is True
        assert mounts["/output"]["readOnly"] is False
        prefix = settings.pvc_data_prefix + "/"
        paths = {}
        for name in ("/request", "/output"):
            assert mounts[name]["subPath"].startswith(prefix)
            paths[name] = case.root / mounts[name]["subPath"][len(prefix):]
        envelope = read_json(paths["/request"], "request.json")
        assert envelope == {"original_request": case.original, "acceptance_request": case.request}
        atomic_json(paths["/output"] / "request.json", case.request)
        atomic_json(paths["/output"] / "receipt.json", {
            "schema": "ppt-service.acceptance-evidence.v1", "status": "pending",
            "evidence_id": case.request["evidence_id"], "binding_sha256": case.signature,
            "request_sha256": digest(case.request), "execution_image": IMAGE,
            "publication_authorized": False})
        api.terminal(obj)

    api.on_pod = observe_pod
    assert invoke(case)["status"] == "acceptance_validated"
    backend = case.sandbox.transport.backend
    handle = backend.handle_for_scope(case.scope)
    authority = backend._read(handle)
    pods = [row for row in authority["resources"] if row["resource"] == "pods"]
    assert len(pods) == 1 and authority["launch_not_started"] is False
    pod = pods[0]
    assert pod["deleted"] is True and pod["finalizer_release_requested"] is True
    assert pod["stop_receipt"]["terminated"] is True
    assert pod["stop_receipt"]["api_uid"] == pod["api_uid"]
    body = read_json(case.target, "transport-manifest.json")
    assert body["validator_pod_uid"] == pod["api_uid"]
    assert body["validator_scope_uid"] == case.scope
    assert not api.objects
    assert len([call for call in api.calls if call[:2] == ("POST", "pods")]) == 1
    assert read_json(case.attempted.parent, case.attempted.name)["status"] == "pending"


@pytest.mark.parametrize("rename_fails", [False, True])
def test_cross_parent_install_keeps_private_root_writable_until_real_rename(
        transport_case, monkeypatch, rename_fails):
    """Run the real rename so uid262/server runs exercise Linux permissions.

    Checking the modes also keeps a root-run suite from concealing the original
    failure: root could move a 0555 staging directory that uid262 cannot move.
    """
    from ppt_service.control import agent_validation

    case = transport_case
    real_replace = os.replace
    observed = []

    def observe_replace(source, destination):
        source, destination = Path(source), Path(destination)
        if source.name == ".acceptance-pending-" + case.request["evidence_id"]:
            observed.append((source, destination))
            assert source.parent == case.base and destination == case.target
            assert source.parent != destination.parent
            assert stat.S_IMODE(source.stat().st_mode) == 0o700
            assert not destination.exists()
            assert not list(destination.parent.glob("*"))
            for node in source.rglob("*"):
                assert stat.S_IMODE(node.stat().st_mode) & 0o222 == 0
            if rename_fails:
                raise PermissionError("Synthetic install failure before atomic rename")
        return real_replace(source, destination)

    monkeypatch.setattr(agent_validation.os, "replace", observe_replace)
    if rename_fails:
        with pytest.raises(PermissionError, match="Synthetic install failure"):
            invoke(case)
        assert_no_bundle(case)
        assert stat.S_IMODE(observed[0][0].stat().st_mode) == 0o700
        assert read_json(case.attempted.parent, case.attempted.name)["status"] == "stopped_pending_inspection"
        assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1
        assert not case.discarded
    else:
        assert invoke(case)["status"] == "acceptance_validated"
        assert not observed[0][0].exists()
        assert stat.S_IMODE(case.target.stat().st_mode) == 0o555
        for node in case.target.rglob("*"):
            assert stat.S_IMODE(node.stat().st_mode) & 0o222 == 0
        body = read_json(case.target, "transport-manifest.json")
        _measured_tree(case.target, body["files"], max_files=8192,
            exclude=("transport-manifest.json",))
    assert len(observed) == 1


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


def test_journal_proven_launch_rejection_retains_original_reason_without_pod_stop_claim(transport_case):
    case = transport_case
    case.fault = "launch_rejected"
    assert invoke(case) == {"status": "acceptance_failed", "binding_sha256": case.signature,
        "error_code": "unapproved_kubernetes_entrypoint"}
    assert_no_bundle(case)
    cache = read_json(case.attempted.parent, case.attempted.name)
    assert cache["status"] == "launch_rejected" and cache["error_code"] == "unapproved_kubernetes_entrypoint"
    assert cache["launch_not_started"] is True and cache["pod_post_intents"] == 0
    assert not {"pod_uid", "validator_pod_uid", "stop_receipt", "transport_verified"} & set(cache)
    assert not case.discarded
    assert stat.S_IMODE(case.runtime.stat().st_mode) == 0o555
    assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1


@pytest.mark.parametrize("fault", ["launch_uncertain", "launch_missing_flag", "launch_unexplained"])
def test_no_pod_row_alone_never_proves_no_launch(transport_case, fault):
    case = transport_case
    case.fault = fault
    with pytest.raises(ControlError, match="acceptance_validator_stop_unverified"):
        invoke(case)
    assert_no_bundle(case)
    assert not case.attempted.exists() and not case.discarded


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


def configure_measured_layout_failure(case, monkeypatch):
    from ppt_service.control import agent_validation
    case.layout_output = True
    case.original.update(task={"synthetic_only": True}, pages=3)
    case.request["original_request_sha256"] = digest(case.original)
    project = case.snapshot / "project/deck"
    project.mkdir(parents=True)
    (project / "presentation.pptx").write_bytes(b"Synthetic layout source")
    manifest = {"project_root": "deck", "artifacts": [{"role": "pptx", "path": "presentation.pptx",
        "sha256": case.request["binding"]["pptx_sha256"], "bytes": len(b"Synthetic layout source")}]}

    def source(snapshot, task, sha):
        assert snapshot == case.snapshot and task == case.original["task"]
        assert sha == case.request["binding"]["manifest_sha256"]
        return manifest, {}

    monkeypatch.setattr(agent_validation, "_candidate_snapshot", source)
    return case.base / "acceptance-failures" / case.request["evidence_id"]


def test_actual_async_stop_path_installs_distinct_bound_layout_failure_and_never_repeats_office(transport_case, monkeypatch):
    case = transport_case
    target = configure_measured_layout_failure(case, monkeypatch)
    before = (case.snapshot / "project/deck/presentation.pptx").read_bytes()
    result = invoke(case)
    assert result == {"status": "acceptance_failed", "binding_sha256": case.signature,
                      "error_code": "final_pptx_text_layout_failed"}
    assert_no_bundle(case)
    assert target.exists()
    receipt = read_json(target, "failure-receipt.json")
    transport = read_json(target, "transport-manifest.json")
    assert receipt["schema"] == "ppt-service.acceptance-failure.v1"
    assert receipt["accepted"] is receipt["publication_authorized"] is False
    assert receipt["human_visual_acceptance"] is False and receipt["powerpoint_acceptance"] == "pending"
    assert receipt["pptx"]["sha256"] == case.request["binding"]["pptx_sha256"]
    assert transport["schema"] == "ppt-service.acceptance-failure-transport.v1"
    assert transport["writers_stopped"] is transport["transport_verified"] is True
    assert transport["validator_exit_code"] == 2
    assert transport["validator_pod_uid"] == case.layout_pod_uid
    assert transport["validator_scope_uid"] == case.scope
    roster, total = _measured_tree(target, transport["files"], max_files=8192,
        exclude=("transport-manifest.json",))
    assert total == transport["bytes"] and "final-pptx/text-layout.json" in roster
    assert "receipt.json" not in roster
    for node in [target, *target.rglob("*")]:
        assert stat.S_IMODE(node.stat().st_mode) & 0o222 == 0
    assert read_json(case.attempted.parent, case.attempted.name)["error_code"] == "final_pptx_text_layout_failed"
    assert (case.snapshot / "project/deck/presentation.pptx").read_bytes() == before
    assert case.discarded == [case.runtime]
    assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1


@pytest.mark.parametrize("fault", ["nonce", "page", "pptx", "code"])
def test_bad_measured_layout_report_remains_private_without_owner_diagnostic(transport_case, monkeypatch, fault):
    case = transport_case
    target = configure_measured_layout_failure(case, monkeypatch)
    case.layout_fault = fault
    assert invoke(case)["status"] == "acceptance_failed"
    assert not target.exists() and not case.discarded
    assert read_json(case.attempted.parent, case.attempted.name)["error_code"] == "acceptance_validation_failed"
    assert (case.output / "final-pptx/text-layout.json").exists()
    assert invoke(case)["status"] == "already_attempted" and len(case.runs) == 1


@pytest.mark.parametrize("fault", ["running", "not_terminated", "mismatched_uid", "transport_failure"])
def test_layout_failure_diagnostic_never_replaces_missing_stop_or_lifecycle_failure(transport_case, monkeypatch, fault):
    case = transport_case
    target = configure_measured_layout_failure(case, monkeypatch)
    case.fault = fault
    if fault == "transport_failure":
        assert invoke(case)["status"] == "acceptance_failed"
    else:
        with pytest.raises(ControlError):
            invoke(case)
    assert not target.exists() and not case.discarded
