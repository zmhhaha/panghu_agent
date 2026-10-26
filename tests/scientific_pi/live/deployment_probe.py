#!/usr/bin/env python3
"""Explicit production canary through the internal scientific API only.

submit creates one real brief research job with email disabled. Reusing its
task key is idempotent; status and verify are read-only. No credentials, report
text, paper titles, authors, URLs or raw service errors are printed. --self-test
uses an in-memory HTTP-response fixture and makes no network/model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import uuid
from urllib.parse import urlencode, urlsplit

ALLOWED_APIS = {"http://api.scientific-agent.svc.cluster.local", "http://127.0.0.1:8000"}
USER = "scientific-pi-production-canary"
NAMESPACE = uuid.UUID("bb91b052-f393-4653-ae4a-0a4845fce498")
REQUIRED_ARTIFACTS = {"report", "searches", "papers", "screening", "evidence", "claims", "references", "validation"}
MAX_BYTES = 50 * 1024 * 1024
FIELDS = ("artifact_id", "media_type", "size", "sha256")
STATES = {"queued", "running", "cancel_requested", "validating", "publishing", "succeeded", "failed", "cancelled", "timed_out"}
EXPECTED_SKILL_BUNDLE_REVISION = "local-procedures-v2"


class ProbeFailure(Exception):
    def __init__(self, code, **diagnostics):
        self.code, self.diagnostics = code, diagnostics


def require(condition, code):
    if not condition:
        raise ProbeFailure(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def identity(key):
    require(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key), "invalid_task_key")
    return str(uuid.uuid5(NAMESPACE, canonical([USER, "pi", key])))


def api_url(value):
    require(value in ALLOWED_APIS, "api_url_not_allowed")
    return value


class Api:
    def __init__(self, base):
        parsed = urlsplit(api_url(base))
        self.host, self.port = parsed.hostname, parsed.port or 80

    def request(self, path, payload=None, key=None, binary=False):
        require(path.startswith("/scientific-"), "unexpected_api_path")
        headers = {"Accept": "application/json", "User-Agent": "scientific-pi-deployment-probe/1"}
        body = None
        if payload is not None:
            body = canonical(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        # Direct HTTPConnection intentionally ignores proxy/credential env and
        # does not follow redirects to an unreviewed endpoint.
        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        try:
            connection.request("POST" if payload is not None else "GET", path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_BYTES + 1)
            require(len(data) <= MAX_BYTES, "response_too_large")
            if response.status != 200:
                details = {"http_status": response.status}
                try:
                    detail = json.loads(data).get("detail", {})
                    if isinstance(detail, dict):
                        code = detail.get("code")
                        if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code):
                            details["service_code"] = code
                except (ValueError, AttributeError):
                    pass
                raise ProbeFailure("api_request_failed", **details)
            return data if binary else json.loads(data)
        finally:
            connection.close()


def task_summary(row, task_id):
    require(isinstance(row, dict) and row.get("id") == task_id, "task_identity_mismatch")
    require(row.get("engine") == "pi" and row.get("mode") == "brief" and row.get("user_id") == USER, "canary_input_mismatch")
    require(row.get("state") in STATES, "invalid_task_state")
    require("notification_status" in row and row["notification_status"] is None, "unexpected_notification")
    result = {"valid": True, "task_id": task_id, "engine": "pi", "mode": "brief", "state": row["state"],
              "email_requested": False, "notification_status": None}
    for field in ("attempt", "revision", "progress"):
        if type(row.get(field)) is int:
            result[field] = row[field]
    for field in ("stage", "error_code"):
        value = row.get(field)
        if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,80}", value):
            result[field] = value
    return result


def submit(api, key, topic, timeout):
    require(isinstance(topic, str) and 1 <= len(topic.strip()) <= 500, "invalid_topic")
    require(type(timeout) is int and 60 <= timeout <= 3600, "invalid_timeout")
    task_id = identity(key)
    payload = dict(topic=topic.strip(), email="", user_id=USER, engine="pi", mode="brief", timeout_seconds=timeout)
    row = api.request("/scientific-research", payload, key)
    require(row.get("topic") == payload["topic"], "canary_topic_mismatch")
    return dict(task_summary(row, task_id), code="canary_submitted", topic_sha256=sha(payload["topic"].encode("utf-8")))


def json_rows(data):
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    require(all(isinstance(row, dict) for row in rows), "invalid_evidence_records")
    return rows


def unique_index(rows, field):
    require(all(isinstance(row.get(field), str) and row[field] for row in rows), "invalid_evidence_identity")
    result = {row[field]: row for row in rows}
    require(len(result) == len(rows), "duplicate_evidence_identity")
    return result


def evidence_chain(contents, report):
    searches, papers, screening, evidence, claims = (json_rows(contents[name]) for name in ("searches", "papers", "screening", "evidence", "claims"))
    references = json.loads(contents["references"])
    require(papers and evidence and claims and references, "zero_evidence_canary")
    require(any(row.get("status") in {"success", "partial"} and type(row.get("result_count")) is int and row["result_count"] > 0 for row in searches), "successful_retrieval_missing")
    p = unique_index(papers, "paper_id")
    e = unique_index(evidence, "evidence_id")
    c = unique_index(claims, "claim_id")
    screened = unique_index(screening, "paper_id")
    require(isinstance(references, list) and all(isinstance(row, dict) for row in references), "invalid_references")
    require(len({row.get("n") for row in references}) == len(references), "duplicate_reference_number")
    mapped = {}
    for row in references:
        require(row.get("paper_id") in p and type(row.get("n")) is int and row["n"] > 0, "reference_identity_mismatch")
        require(row["paper_id"] not in mapped and f"[{row['n']}]" in report, "report_reference_missing")
        mapped[row["paper_id"]] = row["n"]
    for row in evidence:
        paper = p.get(row.get("paper_id"))
        require(paper is not None and screened.get(row["paper_id"], {}).get("decision") == "include", "evidence_paper_link_missing")
        require(isinstance(row.get("finding"), str) and row["finding"].strip(), "empty_evidence_finding")
        level = row.get("access_level")
        require(level in {"metadata", "abstract", "full_text"} and paper.get("availability", {}).get(level, {}).get("available") is True, "evidence_access_mismatch")
    linked = set()
    for row in c.values():
        ids = row.get("evidence_ids")
        require(isinstance(ids, list) and all(identifier in e for identifier in ids), "claim_evidence_link_missing")
        marker = "<!-- claim:" + row["claim_id"] + " -->"
        require(row.get("marker") == marker and report.count(marker) == 1, "claim_marker_missing")
        paragraph = re.split(r"\n\s*\n|(?=<!-- claim:)|(?=^#{1,6} )", report.split(marker, 1)[1].lstrip(), maxsplit=1, flags=re.M)[0]
        for identifier in ids:
            paper_id = e[identifier]["paper_id"]
            require(paper_id in mapped and f"[{mapped[paper_id]}]" in paragraph, "claim_citation_missing")
            linked.add(identifier)
    require(linked, "zero_linked_evidence_canary")
    return {"searches": len(searches), "papers": len(p), "screened": len(screened), "included": sum(row.get("decision") == "include" for row in screening),
            "evidence_items": len(e), "claims": len(c), "references": len(references), "linked_evidence_items": len(linked)}


def verify(api, task_id):
    row = api.request("/scientific-research/" + task_id)
    summary = task_summary(row, task_id)
    require(row["state"] == "succeeded" and row.get("status") == "done", "task_not_published")
    require(row.get("report_id") == task_id, "report_identity_mismatch")
    report = row.get("report")
    require(isinstance(report, str) and report.strip(), "empty_report")
    report_bytes = report.encode("utf-8")
    require(digest(row.get("report_sha256")) and sha(report_bytes) == row["report_sha256"], "task_report_digest_mismatch")
    detail = api.request("/scientific-reports/" + task_id)
    require(detail.get("id") == task_id and detail.get("content") == report and detail.get("topic") == row.get("topic"), "report_detail_mismatch")
    download = api.request("/scientific-download/" + task_id, binary=True)
    require(download == report_bytes, "report_download_mismatch")
    keywords = json.loads(detail.get("keywords") or "null")
    require(isinstance(keywords, list) and keywords and all(isinstance(word, str) and word for word in keywords), "keywords_unavailable")
    for query in (row["topic"], keywords[0]):
        found = api.request("/scientific-reports?" + urlencode({"q": query, "limit": 100}))
        require(isinstance(found, list) and any(item.get("id") == task_id for item in found), "report_search_mismatch")
    base = "/scientific-research/" + task_id + "/artifacts"
    items = api.request(base).get("items")
    require(isinstance(items, list) and all(isinstance(item, dict) for item in items), "invalid_artifact_listing")
    require(len(items) == len(REQUIRED_ARTIFACTS) and {item.get("artifact_id") for item in items} == REQUIRED_ARTIFACTS, "artifact_set_mismatch")
    contents, checked, total = {}, [], 0
    for item in items:
        require(type(item.get("size")) is int and 0 <= item["size"] <= MAX_BYTES and digest(item.get("sha256")), "invalid_artifact_metadata")
        data = api.request(base + "/" + item["artifact_id"], binary=True)
        total += len(data)
        require(total <= MAX_BYTES, "artifact_total_too_large")
        require(len(data) == item["size"] and sha(data) == item["sha256"], "artifact_digest_mismatch")
        contents[item["artifact_id"]] = data
        checked.append({field: item[field] for field in FIELDS})
    require(contents["report"] == report_bytes, "artifact_report_mismatch")
    validation = json.loads(contents["validation"])
    require(validation.get("valid") is True and validation.get("errors") == [], "validation_not_passed")
    manifest = json.loads(api.request(base + "/manifest", binary=True))
    require(manifest.get("schema_version") == "1" and manifest.get("task_id") == task_id and manifest.get("attempt") == row.get("attempt"), "manifest_identity_mismatch")
    manifest_items = [{field: item[field] for field in FIELDS} for item in manifest["artifacts"]]
    require(sorted(manifest_items, key=lambda item: item["artifact_id"]) == sorted(checked, key=lambda item: item["artifact_id"]), "manifest_artifacts_mismatch")
    provenance = manifest.get("provenance", {})
    require(provenance.get("fixture_mode") is False, "fixture_result_rejected")
    require(provenance.get("runtime_revision") == "pi-jsonl-0.73.1-v1" and provenance.get("skill_bundle_revision") == EXPECTED_SKILL_BUNDLE_REVISION, "unexpected_runtime_revision")
    require(isinstance(provenance.get("image_digest"), str) and re.fullmatch(r"sha256:[0-9a-f]{64}", provenance["image_digest"]), "image_digest_missing")
    envelope = dict(schema_version="1", task_id=task_id, attempt=row["attempt"], report_sha256=row["report_sha256"],
                    artifacts=sorted(checked, key=lambda item: item["artifact_id"]), provenance=provenance)
    result_digest = sha(canonical(envelope).encode("utf-8"))
    require(result_digest == row.get("result_digest") == manifest.get("result_digest"), "result_digest_mismatch")
    chain = evidence_chain(contents, report)
    return dict(summary, code="production_canary_verified", report_id=task_id, report_sha256=row["report_sha256"], result_digest=result_digest,
                report_bytes=len(report_bytes), artifacts=sorted(checked, key=lambda item: item["artifact_id"]), artifact_bytes=total,
                validation_valid=True, evidence_chain=chain, search_compatible=True, keywords_compatible=True, download_verified=True,
                fixture_mode=False, scientific_accuracy_accepted=False, notification_verification="API notification_status is null; submission email was empty",
                image_digest=provenance["image_digest"])


def self_test():
    key = "offline-self-test"
    task_id = identity(key)
    require(identity(key) == task_id and identity(key + "-different") != task_id, "identity_self_test_failed")
    for base in ALLOWED_APIS:
        api_url(base)
    for base in ("https://api.scientific-agent.svc.cluster.local", "http://127.0.0.1:8000@evil.example", "http://127.0.0.1:8000/", "http://api.scientific-agent.svc.cluster.local:8000"):
        try:
            api_url(base)
        except ProbeFailure:
            pass
        else:
            raise ProbeFailure("url_self_test_failed")
    report = "# Offline fixture\n\n<!-- claim:c1 -->\nAbstract evidence [1].\n"
    rows = {"searches": [{"status": "success", "result_count": 1}],
            "papers": [{"paper_id": "p1", "availability": {"abstract": {"available": True}}}],
            "screening": [{"paper_id": "p1", "decision": "include"}],
            "evidence": [{"evidence_id": "e1", "paper_id": "p1", "finding": "Fixture finding", "access_level": "abstract"}],
            "claims": [{"claim_id": "c1", "marker": "<!-- claim:c1 -->", "evidence_ids": ["e1"]}]}
    contents = {name: ("\n".join(canonical(row) for row in values) + "\n").encode() for name, values in rows.items()}
    contents.update(report=report.encode(), references=canonical([{"n": 1, "paper_id": "p1"}]).encode(), validation=canonical({"valid": True, "errors": []}).encode())
    items = [dict(artifact_id=name, size=len(data), sha256=sha(data), media_type="text/markdown" if name == "report" else "application/json") for name, data in sorted(contents.items())]
    provenance = dict(fixture_mode=False, runtime_revision="pi-jsonl-0.73.1-v1", skill_bundle_revision=EXPECTED_SKILL_BUNDLE_REVISION, image_digest="sha256:" + "0" * 64)
    envelope = dict(schema_version="1", task_id=task_id, attempt=1, report_sha256=sha(report.encode()), artifacts=items, provenance=provenance)
    result_hash = sha(canonical(envelope).encode())
    row = dict(id=task_id, engine="pi", mode="brief", user_id=USER, state="succeeded", status="done", topic="Offline fixture", report=report,
               report_id=task_id, report_sha256=envelope["report_sha256"], attempt=1, notification_status=None, result_digest=result_hash)
    base = "/scientific-research/" + task_id + "/artifacts"
    responses = {"/scientific-research/" + task_id: row, "/scientific-reports/" + task_id: dict(id=task_id, content=report, topic=row["topic"], keywords='["Offline"]'),
                 "/scientific-download/" + task_id: report.encode(), base: {"items": items},
                 base + "/manifest": canonical(dict(envelope, result_digest=result_hash)).encode()}
    responses.update({base + "/" + name: data for name, data in contents.items()})

    class FixtureApi:
        def request(self, path, payload=None, key=None, binary=False):
            if path == "/scientific-research":
                require(payload["email"] == "" and payload["engine"] == "pi" and payload["mode"] == "brief" and key == "offline-self-test", "submit_self_test_failed")
                return row
            if path.startswith("/scientific-reports?"):
                return [{"id": task_id}]
            return responses[path]

    api = FixtureApi()
    require(submit(api, key, "Offline fixture", 900)["task_id"] == task_id, "submit_self_test_failed")
    require(verify(api, task_id)["evidence_chain"]["papers"] == 1, "verify_self_test_failed")
    responses[base + "/evidence"] = b"corrupted"
    try:
        verify(api, task_id)
    except ProbeFailure as exc:
        require(exc.code == "artifact_digest_mismatch", "corruption_self_test_failed")
    else:
        raise ProbeFailure("corruption_self_test_failed")
    return dict(valid=True, code="self_test_passed", network_requests=0, model_calls=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("submit", "status", "verify"))
    parser.add_argument("--task-key", help="Stable unique canary Idempotency-Key; reused for status/verify")
    parser.add_argument("--topic", help="Real scientific retrieval topic; submit only")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--api-url", default=os.environ.get("SCIENTIFIC_PI_PROBE_API_URL", "http://api.scientific-agent.svc.cluster.local"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    task_id = None
    try:
        if args.self_test:
            require(args.command is None, "self_test_command_conflict")
            result = self_test()
        else:
            require(args.command is not None, "command_required")
            task_id = identity(args.task_key)
            api = Api(args.api_url)
            if args.command == "submit":
                result = submit(api, args.task_key, args.topic, args.timeout_seconds)
            elif args.command == "status":
                result = dict(task_summary(api.request("/scientific-research/" + task_id), task_id), code="canary_status")
            else:
                result = verify(api, task_id)
    except Exception as exc:
        result = dict(valid=False, code=exc.code if isinstance(exc, ProbeFailure) else "probe_failed", task_id=task_id,
                      diagnostics=exc.diagnostics if isinstance(exc, ProbeFailure) else {}, exception_type=type(exc).__name__)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
