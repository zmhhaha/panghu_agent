#!/usr/bin/env python3
"""Opt-in live-model research/publication probe using only synthetic source data.

The real Pi CLI and provider must write the research artifacts autonomously.
The report store is isolated local SQLite, HTTP apps run through their actual
ASGI routes, and email is empty. No production data or mail is touched.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback

from model_probe import (DUMMY_KEY, Evidence, GatewayProxy, LIMIT, MODEL,
                         ProbeFailure, no_key_in_workspace, require)

ROOT = Path(__file__).resolve().parents[3]
MAX_REQUESTS = 12
TIMEOUT_SECONDS = 480
SKILL = ROOT / "scientific_skills/research-procedure/SKILL.md"
PROBE_STAGE = "not_started"
PROBE_DIAGNOSTICS = {}


class ResearchEvidence(Evidence):
    """Bounded fixed-provider forwarding; retain only counters and safe metadata."""
    def __init__(self):
        super().__init__()
        self.skill_read = False

    def observe_request(self, request):
        require(isinstance(request, dict) and request.get("model") == MODEL
                and request.get("stream") is True, "request_invalid")
        messages = request.get("messages")
        require(isinstance(messages, list), "request_invalid")
        with self.lock:
            self.requests += 1
            require(self.requests <= MAX_REQUESTS, "request_limit")
            skill_calls = set()
            for message in messages:
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                for call in message.get("tool_calls", []):
                    function = call.get("function", {})
                    if function.get("name") != "read":
                        continue
                    arguments = json.loads(function.get("arguments", "{}"))
                    if arguments.get("path") == str(SKILL):
                        skill_calls.add(call.get("id"))
            self.skill_read |= any(
                isinstance(message, dict) and message.get("role") == "tool"
                and message.get("tool_call_id") in skill_calls
                and "# Auditable research procedure" in json.dumps(message.get("content", ""))
                for message in messages)
            return self.requests


def source_packet():
    from pi_worker.research_tools.bridge import normalize_paper
    timestamp = "2026-01-01T00:00:00Z"
    papers = [normalize_paper({
        "title": "Synthetic controlled fixture " + label,
        "provider": "controlled-synthetic-fixture", "authors": ["Fictional Test Author"],
        "date": "2026-01-01", "url": "https://example.invalid/fixture-" + label.lower(),
        "abstract": "The synthetic intervention " + direction + " the synthetic outcome.",
    }, timestamp) for label, direction in (("Increase", "increased"), ("Decrease", "decreased"))]
    searches = [{"source": "controlled-synthetic-fixture", "query": "Supplied two-record synthetic source packet; no online search",
                 "searched_at": timestamp, "status": "success", "result_count": 2, "error": None}]
    return {"searches": searches, "papers": papers}


def prompt_suffix(packet):
    return (
        "\n\nCONTROLLED SYNTHETIC ACCEPTANCE TASK — this explicitly replaces online retrieval.\n"
        "This packet contains fictional test data, not published studies. Do not search or use the network, "
        "inspect environment/private files, install anything, send mail, or contact services. "
        "First use the read tool to read the reviewed skill at " + str(SKILL) + ". "
        "Then independently write all required research files in the assigned workspace using the given schemas. "
        "Copy the supplied search and paper records exactly. Include both papers and give explicit screening reasons. "
        "Create one abstract-level evidence item per paper: its finding must be the paper's exact abstract sentence. "
        "Create at least one conflicting claim linked to both evidence items. Explain that the synthetic observations "
        "conflict and support no conclusion about the real world. Use claim markers and numbered references, "
        "and cite both papers beside the conflicting claim. Write a short English report with Scope, Methods, "
        "Findings, Limitations and References. State explicitly that data are synthetic, access is abstract-only, "
        "identifier verification is unknown, and this is not a systematic review. Do not claim scientific truth. "
        "Run python -m pi_worker.research_tools validate --workspace . and fix any errors before finishing. "
        "Do not create provenance.json or manifest.json; those are controller-owned. "
        "The only source packet follows as JSON data:\n" + json.dumps(packet, ensure_ascii=False) + "\n"
    )


def check_research(result, artifact_get, packet):
    """Assess exact controlled source/evidence relationships, without truth claims."""
    def rows(name):
        return [json.loads(line) for line in artifact_get(name).decode("utf-8").splitlines() if line.strip()]
    papers = rows("papers")
    require(sorted(papers, key=lambda row: row["paper_id"]) == sorted(packet["papers"], key=lambda row: row["paper_id"]), "source_packet_changed")
    require(rows("searches") == packet["searches"], "source_packet_changed")
    evidence = rows("evidence")
    paper_by_id = {paper["paper_id"]: paper for paper in papers}
    require(len(evidence) == 2 and {item["paper_id"] for item in evidence} == set(paper_by_id), "evidence_missing")
    require(all(item["access_level"] == "abstract" and item["finding"] == paper_by_id[item["paper_id"]]["abstract"] for item in evidence), "evidence_changed")
    evidence_ids = {item["evidence_id"] for item in evidence}
    claims = rows("claims")
    require(any(item["assessment"] == "conflicting" and set(item["evidence_ids"]) == evidence_ids for item in claims), "conflict_not_reported")
    report = result["report_markdown"].casefold()
    require(all(word in report for word in ("synthetic", "abstract", "unknown", "conflict")), "limitations_missing")
    return {"sources": 1, "papers": len(papers), "evidence_items": len(evidence), "claims": len(claims), "conflicting_claim_verified": True}


def run_probe():
    global PROBE_STAGE
    PROBE_STAGE = "prerequisites"
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_required")
    capabilities = next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("CapEff:"))
    require(int(capabilities, 16) == sum(1 << bit for bit in (0, 1, 5, 6, 7)), "capabilities_invalid")
    key = os.environ.pop("SCIENTIFIC_PI_MODEL_API_KEY", "")
    require(len(key) >= 8, "credential_missing")
    digest = os.environ.get("SCIENTIFIC_PI_IMAGE_DIGEST", "")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "image_digest_missing")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests/scientific_pi/integration"))
    import resource
    from unittest.mock import patch
    from harness import IntegrationHarness
    from scientific_agent.pi_runtime_config import runtime_from_env
    from scientific_agent.pi_contracts import result_digest, report_sha256
    from pi_worker.runtime import PiRuntime
    from pi_worker.research_tools import build_research_prompt

    case = Path("/data/research-probe")
    case.mkdir(mode=0o711)  # A reused scratch directory is an error.
    case.chmod(0o711)
    base = runtime_from_env(case)
    packet = source_packet()
    evidence = ResearchEvidence()
    harness = runtime = None
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        PROBE_STAGE = "runtime_settings"
        with GatewayProxy(key, evidence, started + TIMEOUT_SECONDS) as proxy:
            settings = replace(base.settings, provider="scientific-controlled-live", model=MODEL,
                               model_base_url=proxy.base_url, model_api="openai-completions",
                               model_env={"SCIENTIFIC_MODEL_API_KEY": DUMMY_KEY}, fixture_mode=False,
                               # Test-process declarations only; never deployment readiness.
                               capability_verified=True, model_verified=True, isolation_verified=True,
                               image_digest=digest, idle_timeout_seconds=110)
            runtime = PiRuntime(settings)
            PROBE_STAGE = "harness_initialization"
            harness = IntegrationHarness(runtime=runtime, runtime_revision=settings.runtime_revision)
            PROBE_STAGE = "submit"
            response = harness.submit(key="live-controlled-research", user="isolated-research-probe",
                                      topic="What can two contradictory synthetic abstracts establish? Controlled fixture only.",
                                      mode="brief", timeout_seconds=TIMEOUT_SECONDS, email="")
            require(response.status_code == 200, "submission_failed")
            task_id = response.json()["id"]
            PROBE_STAGE = "execute"
            # Test-process instrumentation records numeric stdout/stderr accounting
            # from the actual adapter, without retaining any line or model content.
            execution_code = PiRuntime.execute.__code__
            previous_profile = threading.getprofile()
            def execution_profile(frame, event, _argument):
                if event == "return" and frame.f_code is execution_code:
                    total = frame.f_locals.get("total")
                    if type(total) is int:
                        PROBE_DIAGNOSTICS["runtime_output_bytes"] = total
            threading.setprofile(execution_profile)
            try:
                with patch("pi_worker.research_tools.build_research_prompt", side_effect=lambda job: build_research_prompt(job) + prompt_suffix(packet)):
                    snapshot = harness.execute_next()
            finally:
                threading.setprofile(previous_profile)
            PROBE_DIAGNOSTICS.update(state=snapshot["state"], error_code=snapshot.get("error_code"))
            if snapshot["state"] != "publishing":
                code = snapshot.get("error_code")
                require(isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code), "research_not_publishing")
                raise ProbeFailure("research_" + code)
        PROBE_STAGE = "result_validation"
        require(not evidence.error and evidence.requests == evidence.done, "model_rounds_incomplete")
        require(evidence.skill_read, "skill_read_unverified")
        result = harness.worker.result(task_id)
        require(result["validation"]["valid"] is True, "output_validation_failed")
        require(result_digest(result) == result["result_digest"] and report_sha256(result["report_markdown"]) == result["report_sha256"], "result_digest_mismatch")
        require(result["provenance"]["fixture_mode"] is False, "provenance_invalid")
        artifact_get = lambda name: harness.worker.artifact(task_id, name)[0]
        chain = check_research(result, artifact_get, packet)
        for entry in result["artifacts"]:
            data = artifact_get(entry["artifact_id"])
            require(len(data) == entry["size"] and hashlib.sha256(data).hexdigest() == entry["sha256"], "artifact_digest_mismatch")
        require(harness.db.get_report(task_id) is None, "premature_publication")
        PROBE_STAGE = "publication"
        harness.projection.reconcile()
        public = harness.public_http.get("/scientific-research/" + task_id).json()
        require(public["state"] == "succeeded" and public["status"] == "done" and public["report_id"] == task_id, "publication_failed")
        download = harness.public_http.get("/scientific-download/" + task_id)
        require(download.status_code == 200 and download.content == result["report_markdown"].encode("utf-8"), "download_mismatch")
        require(not harness.db._query("SELECT * FROM scientific_notifications") and public["notification_status"] is None, "unexpected_notification")
        require(harness.transport.count("POST", "/publication-ack") == 1, "ack_mismatch")
        uids = harness.store.execution_uids()
        require(len(uids) == 1 and not runtime._uid_processes(uids[0]), "cleanup_unverified")
        require(not list(settings.control_dir.glob("runtime-*.json"))
                and not list(settings.control_dir.glob("launch-*.json")), "cleanup_unverified")
        no_key_in_workspace(harness.root, key)
        require(key not in json.dumps(result) and key not in json.dumps(sorted(evidence.models))
                and key not in json.dumps(list(evidence.names.values())), "credential_leak")
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        PROBE_STAGE = "report_write"
        # Save only scanned, controlled synthetic report bytes for human review.
        with Path("/data/research-probe-report.md").open("xb") as stream:
            stream.write(download.content)
        return dict(valid=True, code="controlled_research_published", scientific_accuracy_accepted=False,
                    production_acceptance=False, synthetic_source_packet=True, synthetic_harness_gates=True,
                    real_provider=True, fixture_mode=False, configured_alias=MODEL,
                    upstream_model_observed=sorted(evidence.models), model_observation="SSE self-report only",
                    model_requests=evidence.requests, tool_names=sorted(set(evidence.names.values())),
                    runtime_output_bytes=PROBE_DIAGNOSTICS.get("runtime_output_bytes"),
                    runtime_output_limit_bytes=settings.max_output_bytes,
                    skill_read=True, evidence_chain=chain, publication_ack_count=1, email_sent=False,
                    report_sha256=result["report_sha256"], result_digest=result["result_digest"],
                    pi_version=result["provenance"]["pi_version"], node_version=result["provenance"]["node_version"],
                    image_digest=digest, child_uids=uids, child_uid_cleanup=True, real_key_absent=True,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    child_cpu_user_seconds=round(after.ru_utime - before.ru_utime, 3),
                    child_cpu_system_seconds=round(after.ru_stime - before.ru_stime, 3),
                    child_maxrss_kib=after.ru_maxrss, memory_measurement="Linux RUSAGE_CHILDREN high-water; not container peak",
                    token_usage=None, cost=None, report_store="isolated in-memory SQLite facade through real API projection")
    finally:
        PROBE_DIAGNOSTICS.update(model_requests=evidence.requests, model_rounds_done=evidence.done,
                                 proxy_error=evidence.error, skill_read=evidence.skill_read)
        if runtime is not None and harness is not None:
            require(runtime.recover(harness.store.execution_uids()) is True, "cleanup_unverified")
        if harness is not None:
            harness.close()


def self_test():
    sys.path.insert(0, str(ROOT))
    packet = source_packet()
    require(len(packet["papers"]) == 2 and len({row["paper_id"] for row in packet["papers"]}) == 2, "fixture_invalid")
    require(all(row["verification"]["status"] == "unknown" and not row["availability"]["full_text"]["available"] for row in packet["papers"]), "fixture_invalid")
    require("not a systematic review" in prompt_suffix(packet), "fixture_invalid")
    items = [{"evidence_id": "e" + str(index), "paper_id": paper["paper_id"],
              "finding": paper["abstract"], "access_level": "abstract"}
             for index, paper in enumerate(packet["papers"])]
    artifacts = dict(packet, evidence=items, claims=[{"claim_id": "c1", "assessment": "conflicting", "evidence_ids": [row["evidence_id"] for row in items]}])
    get = lambda name: "".join(json.dumps(row) + "\n" for row in artifacts[name]).encode()
    result = {"report_markdown": "Synthetic abstract fixture; identifiers unknown; conflicting observations."}
    require(check_research(result, get, packet)["conflicting_claim_verified"], "self_test_failed")
    artifacts["evidence"][0]["finding"] = "invented finding"
    try:
        check_research(result, get, packet)
    except ProbeFailure as exc:
        require(exc.code == "evidence_changed", "self_test_failed")
    else:
        raise ProbeFailure("self_test_failed")
    evidence = ResearchEvidence()
    request = {"model": MODEL, "stream": True, "messages": [
        {"role": "assistant", "tool_calls": [{"id": "skill-read", "function": {"name": "read", "arguments": json.dumps({"path": str(SKILL)})}}]},
        {"role": "tool", "tool_call_id": "skill-read", "content": "# Auditable research procedure"}]}
    require(evidence.observe_request(request) == 1 and evidence.skill_read, "self_test_failed")
    for _ in range(MAX_REQUESTS - 1):
        evidence.observe_request(request)
    try:
        evidence.observe_request(request)
    except ProbeFailure as exc:
        require(exc.code == "request_limit", "self_test_failed")
    else:
        raise ProbeFailure("self_test_failed")
    return {"valid": True, "code": "self_test_passed", "network_requests": 0, "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="offline fixtures and forwarding limits only")
    parser.add_argument("--report", action="store_true", help="write sanitized JSON to /data/research-probe-result.json")
    args = parser.parse_args()
    try:
        result = self_test() if args.self_test else run_probe()
    except Exception as exc:
        result = {"valid": False, "code": exc.code if isinstance(exc, ProbeFailure) else "controlled_probe_failed",
                  "production_acceptance": False, "message": "Controlled research probe did not pass; no raw diagnostics emitted",
                  "stage": PROBE_STAGE, "exception_type": type(exc).__name__,
                  "diagnostics": PROBE_DIAGNOSTICS,
                  "source_locations": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                                       for frame in traceback.extract_tb(exc.__traceback__)
                                       if Path(frame.filename).resolve().is_relative_to(ROOT)][-5:]}
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if args.report:
        with Path("/data/research-probe-result.json").open("x", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
    print(serialized)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
