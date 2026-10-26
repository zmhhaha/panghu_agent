"""Offline contract tests: fake tool output is never live PPT acceptance.

The deck bytes and ProgrammaticEvidence below are explicitly synthetic. The
fixtures exercise orchestration and hostile evidence, not the official checker,
PowerPoint, a model, or the production execution sandbox.
"""
from __future__ import annotations

import copy
import hashlib
import os
import pickle
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from ppt_service.control import postflight_validation as postflight
from ppt_service.control.execution import ControlError, PINNED_WORKER, PPT_MASTER_COMMIT
from ppt_service.control.render_validation import (
    CACHE_ROOTS, DELIVERY_GATE, NATIVE_GATE, RASTER_GATE, ProgrammaticEvidence,
    fingerprint, hash_file, source_binding,
)
from ppt_service.control.staging import atomic_json, read_json


def quality_fixture(project, rows):
    """Pinned tool's report shape, generated independently of its parser."""
    entries = sorted(
        ({"file": Path(row["path"]).name, "sha256": row["sha256"]} for row in rows),
        key=lambda row: row["file"],
    )
    encoded = b"".join(
        row["file"].encode() + b"\0" + row["sha256"].encode() + b"\n"
        for row in entries
    )
    return {
        "schema": "ppt-master.svg-quality-report.v1",
        "stage": "final", "target": str(project.resolve()),
        "source_fingerprint": {
            "algorithm": "sha256", "digest": hashlib.sha256(encoded).hexdigest(),
            "file_count": len(entries), "files": entries,
        },
        "files": [
            {"path": str((project / row["path"]).resolve()), "exists": True,
             "source_sha256": row["sha256"], "errors": [], "warnings": []}
            for row in rows
        ],
        "summary": {"total": len(rows), "passed": len(rows), "errors": 0, "warnings": 0},
        "project_issues": {
            "template": [], "animation": [], "illustration": [],
            "communication_trace": [], "pptx_structure": [],
        },
        "categories": {
            "blocking": {"count": 0, "issues": []},
            "introduced": {"count": 0, "issues": []},
            "inherited": {"count": 0, "issues": []},
            "source-import": {"count": 0, "summary": {"warning_count": 0}},
        },
    }


def resource_fixture():
    return {
        "unresolved_template_tokens": [],
        "fonts": {"stacks": ["Noto Sans CJK SC"], "generic_only_stacks": [],
                  "unsafe_exported_faces": []},
        "images": {"data_uri": 0, "local": 0, "external": 0, "external_references": []},
    }


def copied_source_fingerprint(manifest):
    rows = [
        {key: row[key] for key in ("path", "sha256", "bytes")}
        for row in manifest["artifacts"]
        if row["path"].split("/", 1)[0] not in CACHE_ROOTS
    ]
    return fingerprint(sorted(rows, key=lambda row: row["path"]))


class FakeOfficialCommands:
    """Writes synthetic official-shaped reports; no executable is launched."""

    def __init__(self, case):
        self.case = case
        self.calls = []
        self.failure = None
        self.timeout = None
        self.stale = False
        self.quality_mutation = None
        self.resource_mutation = None
        self.after_audit = None

    def run(self, args, stdout, stderr, **kwargs):
        self.calls.append(list(args))
        stdout.write_text("Offline test fixture\n")
        stderr.write_text("")
        if "--json-output" in args:
            stage = "quality"
            project = Path(args[2])
            report = quality_fixture(project, self.case.binding["svg_sources"])
            if self.quality_mutation:
                self.quality_mutation(report)
            target = Path(args[args.index("--json-output") + 1])
            atomic_json(target, report)
            if self.stale:
                os.utime(target, (1, 1))
        elif len(args) > 1 and args[1] == "-c":
            stage = "resources"
            report = resource_fixture()
            if self.resource_mutation:
                self.resource_mutation(report)
            atomic_json(stdout, report)
            if self.after_audit:
                self.after_audit()
        elif "attribution_guard.py" in args[1]:
            stage = "attribution"
        else:
            stage = "project"
        if self.timeout == stage:
            raise ControlError("independent_command_timeout")
        return 17 if self.failure == stage else 0


@pytest.fixture
def case(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    project = snapshot / "project" / "deck"
    (project / "svg_output").mkdir(parents=True)
    (snapshot / "result").mkdir()
    rows, slides = [], []
    for ordinal in range(1, 4):
        key = f"svg_output/{ordinal:02}.svg"
        (project / key).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            f'<text x="100" y="100">Offline fixture {ordinal}</text></svg>'
        )
        rows.append({**hash_file(project, key), "role": "svg"})
        slides.append({"ordinal": ordinal, "svg_path": key,
                       "preview_path": f".preview/{ordinal:02}.png"})
    (project / "deck.pptx").write_bytes(b"Synthetic deck; not a valid PPTX or acceptance sample")
    rows.append({**hash_file(project, "deck.pptx"), "role": "pptx"})
    (project / "validation").mkdir()
    atomic_json(project / "validation/svg_quality_report.json", {"fake_old_report": True})
    rows.append({**hash_file(project, "validation/svg_quality_report.json"), "role": "old-report"})
    manifest = {
        "phase": "generate", "status": "candidate", "project_root": "deck",
        "input_snapshot_sha256": "a" * 64, "slides": slides, "artifacts": rows,
    }
    atomic_json(snapshot / "result/manifest.json", manifest)
    charts = [{"slide": 2, "categories": ["Q1"], "series": [{"name": "Units", "values": [12]}]}]
    request = {
        "phase": "generate", "pages": 3, "input_snapshot_sha256": "a" * 64,
        "validation_nonce": str(uuid.uuid4()), "expected_charts": charts,
        "source_manifest_sha256": hash_file(snapshot / "result", "manifest.json")["sha256"],
    }
    binding, _, _ = source_binding(snapshot, manifest, request)
    binding["copied_source_fingerprint"] = copied_source_fingerprint(manifest)
    validation_id = str(uuid.uuid4())
    prior_payload = {
        "validation_id": validation_id, "controller_nonce": request["validation_nonce"],
        "worker_image": PINNED_WORKER, "ppt_master_commit": PPT_MASTER_COMMIT,
        "source": copy.deepcopy(binding),
        "completed_programmatic_gates": [RASTER_GATE, DELIVERY_GATE, NATIVE_GATE],
        "native": {"slide_count": 3, "chart_count": 1, "chart_data_verified": True},
        "delivery": {"status": "passed", "advisory_count": 0, "advisories": []},
        "preview_group_stopped": True, "command_groups_stopped": True,
    }
    data = SimpleNamespace(
        snapshot=snapshot, project=project, manifest=manifest, request=request,
        binding=binding, prior_payload=prior_payload, output=tmp_path / "postflight",
        oracle={"expected_charts": copy.deepcopy(charts), "source_sha256": ["b" * 64]},
        authorized={"b" * 64}, official_commit=PPT_MASTER_COMMIT,
    )
    data.prior = issue_prior(data)
    data.commands = FakeOfficialCommands(data)
    scripts = tmp_path / "image-owned-scripts"
    for name in ("attribution_guard.py", "project_manager.py", "svg_quality_checker.py",
                 "svg_quality/checker.py", "svg_to_pptx/pptx_package/cli.py"):
        target = scripts / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Offline identity fixture; never executed\n" + name)
    monkeypatch.setattr(postflight, "SCRIPTS", scripts)
    monkeypatch.setattr(postflight, "OfficialCommands", lambda: data.commands)
    read_text = Path.read_text

    def fixture_commit(path, *args, **kwargs):
        if path.as_posix() == "/opt/ppt-service/build-info/ppt-master.commit":
            return data.official_commit
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fixture_commit)
    return data


def issue_prior(case):
    return ProgrammaticEvidence._issue(
        case.prior_payload, case.prior_payload["validation_id"],
        case.binding["source_manifest_sha256"],
    )


def execute(case, **overrides):
    values = {
        "snapshot": case.snapshot, "completed_manifest": case.manifest,
        "request": case.request, "trusted_oracle": case.oracle,
        "authorized_source_sha256": case.authorized, "output": case.output,
        "prior_evidence": case.prior,
    }
    values.update(overrides)
    return postflight.run_postflight(**values)


def assert_not_publishable(report):
    assert report["status"] == "pending"
    assert report["publication_authorization"] is False
    assert report["full_exporter_postflight_verified"] is False
    assert set(postflight.PENDING_GATES).issubset(report["required_pending_gates"])
    assert report.get("accepted") is not True


def test_fresh_quality_and_resource_evidence_retains_all_acceptance_gates(case):
    evidence = execute(case)
    report = evidence.report()
    assert_not_publishable(report)
    assert evidence.verify(case.request["validation_nonce"], case.request["source_manifest_sha256"])
    assert report["completed_programmatic_gates"] == [postflight.SOURCE_GATE]
    assert report["source_and_quality_verified"] is True
    assert report["source_unchanged"] is True
    assert report["source"]["copied_source_fingerprint"] == copied_source_fingerprint(case.manifest)
    assert report["oracle"]["source_sha256"] == ["b" * 64]
    assert len(case.commands.calls) == 4
    assert all("_write_postflight_report" not in str(args) for args in case.commands.calls)
    assert read_json(case.output, "postflight-evidence.json") == report
    assert read_json(case.project / "validation", "svg_quality_report.json") == {"fake_old_report": True}
    assert report["reports"]["quality"]["sha256"] == hash_file(
        case.output / "source/deck/validation", "svg_quality_report.json"
    )["sha256"]


@pytest.mark.parametrize("missing,gate", [
    ("oracle", "trusted_source_oracle"),
    ("prior", "independent_programmatic_evidence"),
])
def test_missing_trusted_evidence_stays_pending_without_running_tools(case, missing, gate):
    kwargs = {"trusted_oracle": None} if missing == "oracle" else {"prior_evidence": None}
    report = execute(case, **kwargs).report()
    assert_not_publishable(report)
    assert gate in report["required_pending_gates"]
    assert report["completed_programmatic_gates"] == []
    assert case.commands.calls == []


@pytest.mark.parametrize("problem", ["malformed_nonce", "hash_format", "wrong_manifest", "wrong_input", "invalid_phase", "boolean_pages"])
def test_request_identity_cannot_be_rebound(case, problem):
    mutations = {
        "malformed_nonce": ("validation_nonce", "old-run"),
        "hash_format": ("source_manifest_sha256", "not-a-sha"),
        "wrong_manifest": ("source_manifest_sha256", "f" * 64),
        "wrong_input": ("input_snapshot_sha256", "f" * 64),
        "invalid_phase": ("phase", "plan"), "boolean_pages": ("pages", True),
    }
    key, value = mutations[problem]
    case.request[key] = value
    with pytest.raises(ControlError):
        execute(case)
    assert not (case.output / "postflight-evidence.json").exists()
    assert not case.commands.calls


@pytest.mark.parametrize("problem", ["json", "mutated", "nonce", "source", "copied_fingerprint", "image", "commit", "raster", "delivery", "native", "chart_unverified", "writers"])
def test_forged_replayed_or_incomplete_prior_is_rejected(case, problem):
    payload = case.prior_payload
    if problem == "json":
        case.prior = case.prior.report()
    elif problem == "mutated":
        case.prior._payload["native"]["chart_data_verified"] = False
    else:
        if problem == "nonce":
            payload["controller_nonce"] = str(uuid.uuid4())
        elif problem == "source":
            payload["source"]["pptx"]["sha256"] = "f" * 64
        elif problem == "copied_fingerprint":
            payload["source"]["copied_source_fingerprint"] = "f" * 64
        elif problem == "image":
            payload["worker_image"] = "unapproved:latest"
        elif problem == "commit":
            payload["ppt_master_commit"] = "0" * 40
        elif problem in ("raster", "delivery", "native"):
            payload["completed_programmatic_gates"].remove({
                "raster": RASTER_GATE, "delivery": DELIVERY_GATE, "native": NATIVE_GATE,
            }[problem])
        elif problem == "chart_unverified":
            payload["native"]["chart_data_verified"] = False
        elif problem == "writers":
            payload["command_groups_stopped"] = False
        case.prior = issue_prior(case)
    with pytest.raises(ControlError):
        execute(case)
    assert not case.commands.calls


@pytest.mark.parametrize("problem", ["unauthorized", "duplicate", "malformed", "missing_source", "chart_mismatch", "untrusted_container"])
def test_chart_oracle_must_match_authorized_material(case, problem):
    if problem == "unauthorized":
        case.authorized = {"c" * 64}
    elif problem == "duplicate":
        case.oracle["source_sha256"] *= 2
    elif problem == "malformed":
        case.oracle["source_sha256"] = ["not-a-sha"]
    elif problem == "missing_source":
        case.oracle["source_sha256"] = []
    elif problem == "chart_mismatch":
        case.oracle["expected_charts"][0]["series"][0]["values"] = [99]
    else:
        case.authorized = ["b" * 64]
    with pytest.raises(ControlError):
        execute(case)
    assert not case.commands.calls


@pytest.mark.parametrize("problem", ["missing", "duplicate", "extra", "fingerprint", "file_sha", "stage", "target", "blocking", "contradictory_count", "source_import_count", "source_import_summary", "negative_count", "invented_warning_total", "boolean_count"])
def test_incomplete_stale_or_contradictory_quality_reports_reject(case, problem):
    report = quality_fixture(case.project, case.binding["svg_sources"])
    if problem == "missing":
        report["files"].pop()
    elif problem == "duplicate":
        report["files"][1] = copy.deepcopy(report["files"][0])
    elif problem == "extra":
        report["files"].append(copy.deepcopy(report["files"][0]))
    elif problem == "fingerprint":
        report["source_fingerprint"]["files"][0]["sha256"] = "f" * 64
    elif problem == "file_sha":
        report["files"][0]["source_sha256"] = "f" * 64
    elif problem == "stage":
        report["stage"] = "draft"
    elif problem == "target":
        report["target"] = str(case.project.parent / "old-deck")
    elif problem == "blocking":
        report["categories"]["blocking"] = {"count": 1, "issues": [{"code": "blocked"}]}
    elif problem == "contradictory_count":
        report["categories"]["introduced"] = {"count": 0, "issues": [{"code": "warning"}]}
    elif problem == "source_import_count":
        report["categories"]["source-import"]["count"] = 1
    elif problem == "source_import_summary":
        report["categories"]["source-import"]["summary"]["warning_count"] = 1
    elif problem == "negative_count":
        report["summary"]["warnings"] = -1
    elif problem == "invented_warning_total":
        report["summary"]["warnings"] = 999
    else:
        report["categories"]["inherited"]["count"] = False
    with pytest.raises(ControlError):
        postflight.check_quality_report(report, case.project, case.binding["svg_sources"])


@pytest.mark.parametrize("warning", ["quality", "multiple_on_one_page", "project_warning", "source_import", "fonts", "template", "delivery"])
def test_warnings_are_preserved_and_require_review(case, warning):
    if warning in ("quality", "multiple_on_one_page"):
        def quality_warning(report):
            issue = {"code": "small_text", "message": "Requires human review"}
            issues = [issue]
            if warning == "multiple_on_one_page":
                issues.append({"code": "font_portability", "message": "Review font substitution"})
            report["files"][0]["warnings"] = issues
            report["summary"]["warnings"] = 1
            report["summary"]["passed"] = len(report["files"]) - 1
            report["categories"]["introduced"] = {"count": len(issues), "issues": issues}
        case.commands.quality_mutation = quality_warning
    elif warning == "project_warning":
        def project_warning(report):
            issue = {"severity": "warning", "message": "Review project-level communication trace"}
            report["project_issues"]["communication_trace"] = [issue]
            report["summary"]["warnings"] = 1
            report["categories"]["introduced"] = {"count": 1, "issues": [issue]}
        case.commands.quality_mutation = project_warning
    elif warning == "source_import":
        case.commands.quality_mutation = lambda report: report["categories"].update({
            "source-import": {"count": 1, "summary": {"warning_count": 1}},
        })
    elif warning == "fonts":
        case.commands.resource_mutation = lambda report: report["fonts"]["generic_only_stacks"].append("sans-serif")
    elif warning == "template":
        case.commands.resource_mutation = lambda report: report["unresolved_template_tokens"].append("{{TITLE}}")
    else:
        case.prior_payload["delivery"] = {
            "status": "passed-with-advisories", "advisory_count": 1,
            "advisories": [{"code": "font_portability"}],
        }
        case.prior = issue_prior(case)
    report = execute(case).report()
    assert_not_publishable(report)
    assert "postflight_warnings_review" in report["required_pending_gates"]
    if warning == "quality":
        assert report["quality"]["category_counts"]["introduced"] == 1
        assert report["quality"]["summary"]["passed"] == 2
    elif warning == "multiple_on_one_page":
        assert report["quality"]["category_counts"]["introduced"] == 2
        assert report["quality"]["summary"]["warnings"] == 1
        assert report["quality"]["summary"]["passed"] == 2
    elif warning == "project_warning":
        assert report["quality"]["category_counts"]["introduced"] == 1
        assert report["quality"]["summary"]["warnings"] == 1
        assert report["quality"]["summary"]["passed"] == 3
    elif warning == "source_import":
        assert report["quality"]["category_counts"]["source-import"] == 1
    elif warning == "delivery":
        assert report["reused_programmatic_evidence"]["delivery"]["advisory_count"] == 1


@pytest.mark.parametrize("problem", ["external", "negative", "contradictory", "boolean"])
def test_unsafe_or_invalid_resource_reports_reject(case, problem):
    report = resource_fixture()
    if problem == "external":
        report["images"].update(external=1, external_references=["https://example.test/remote.png"])
    elif problem == "negative":
        report["images"]["local"] = -1
    elif problem == "contradictory":
        report["images"]["external_references"] = ["https://example.test/unaccounted.png"]
    else:
        report["images"]["data_uri"] = False
    with pytest.raises(ControlError):
        postflight.check_resource_report(report)


@pytest.mark.parametrize("stage", ["attribution", "project", "quality", "resources"])
def test_official_command_failure_never_issues_success_evidence(case, stage):
    case.commands.failure = stage
    with pytest.raises(ControlError):
        execute(case)
    assert not (case.output / "postflight-evidence.json").exists()


def test_command_timeout_propagates_without_success_report(case):
    case.commands.timeout = "quality"
    with pytest.raises(ControlError, match="independent_command_timeout"):
        execute(case)
    assert not (case.output / "postflight-evidence.json").exists()


def test_image_commit_must_match_pinned_official_tool_version(case):
    case.official_commit = "0" * 40
    with pytest.raises(ControlError, match="postflight_official_version_mismatch"):
        execute(case)
    assert not case.commands.calls
    assert not (case.output / "postflight-evidence.json").exists()


def test_old_report_timestamp_is_rejected_even_with_correct_hashes(case):
    case.commands.stale = True
    with pytest.raises(ControlError, match="postflight_stale_quality_report"):
        execute(case)


@pytest.mark.parametrize("target", ["snapshot_svg", "copied_svg", "manifest"])
def test_changes_during_audit_invalidate_evidence(case, target):
    paths = {
        "snapshot_svg": case.project / "svg_output/01.svg",
        "copied_svg": case.output / "source/deck/svg_output/01.svg",
        "manifest": case.snapshot / "result/manifest.json",
    }
    case.commands.after_audit = lambda: paths[target].write_text("changed during fake tool execution")
    with pytest.raises(ControlError):
        execute(case)
    assert not (case.output / "postflight-evidence.json").exists()


@pytest.mark.parametrize("target", ["existing", "inside_snapshot", "same_snapshot", "symlink"])
def test_output_cannot_reuse_or_overwrite_candidate_storage(case, tmp_path, target):
    output = case.output
    if target == "existing":
        output.mkdir()
        (output / "marker").write_text("keep")
    elif target == "inside_snapshot":
        output = case.snapshot / "new-evidence"
    elif target == "same_snapshot":
        output = case.snapshot
    else:
        output.symlink_to(tmp_path / "not-created-yet", target_is_directory=True)
    with pytest.raises(ControlError, match="postflight_output_not_fresh"):
        execute(case, output=output)
    assert not case.commands.calls
    if target == "existing":
        assert (output / "marker").read_text() == "keep"


def test_needs_input_is_retained_after_programmatic_success(case):
    case.manifest["status"] = "needs_input"
    atomic_json(case.snapshot / "result/manifest.json", case.manifest)
    case.request["source_manifest_sha256"] = hash_file(case.snapshot / "result", "manifest.json")["sha256"]
    case.binding, _, _ = source_binding(case.snapshot, case.manifest, case.request)
    case.binding["copied_source_fingerprint"] = copied_source_fingerprint(case.manifest)
    case.prior_payload["source"] = copy.deepcopy(case.binding)
    case.prior = issue_prior(case)
    report = execute(case).report()
    assert_not_publishable(report)
    assert report["original_candidate_status"] == "needs_input"
    assert "candidate_needs_input" in report["required_pending_gates"]


def test_evidence_copy_is_not_mutable_authority_or_replay_certificate(case):
    evidence = execute(case)
    nonce, source = case.request["validation_nonce"], case.request["source_manifest_sha256"]
    copied = evidence.report()
    copied["publication_authorization"] = True
    assert evidence.verify(nonce, source)
    assert evidence.report()["publication_authorization"] is False
    assert not evidence.verify(str(uuid.uuid4()), source)
    assert not evidence.verify(nonce, "f" * 64)
    with pytest.raises(TypeError):
        postflight.PostflightEvidence(copied)
    with pytest.raises(TypeError):
        pickle.dumps(evidence)
    evidence._payload["completed_programmatic_gates"].append("forged_visual_acceptance")
    assert not evidence.verify(nonce, source)
