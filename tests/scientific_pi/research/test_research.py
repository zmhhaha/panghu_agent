from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pi_worker.research_tools import (BUNDLE_MANIFEST, BUNDLE_REVISION, build_research_prompt,
                                      validate_research_outputs, validate_workspace)
from pi_worker.research_tools.__main__ import main
from pi_worker.research_tools.bridge import normalize_paper, search, verify_identifier
from fixtures import abstract_only, write_fixture, zero_evidence


class ValidationTests(unittest.TestCase):
    def validate(self, data):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_fixture(workspace, data)
            return validate_research_outputs(workspace)

    def assert_invalid(self, data, fragment):
        result = self.validate(data)
        self.assertIs(result["valid"], False)
        self.assertTrue(any(fragment in error for error in result["errors"]), result)

    def test_zero_evidence_is_valid_only_as_insufficiency(self):
        result = self.validate(zero_evidence())
        self.assertIs(result["valid"], True, result)
        self.assertTrue(result["warnings"])
        data = zero_evidence()
        data["report"] = data["report"].replace("Insufficient evidence", "Treatment is effective")
        self.assert_invalid(data, "explicitly state insufficient")

    def test_abstract_only_with_unknown_verification(self):
        result = self.validate(abstract_only())
        self.assertIs(result["valid"], True, result)
        self.assertTrue(any("verification unknown" in warning for warning in result["warnings"]))
        self.assertTrue(any(check["status"] == "unknown" for check in result["checks"]))
        self.assertEqual(result["provenance"]["source_verification"], "offline_not_repeated")
        self.assertIs(validate_workspace, validate_research_outputs)

    def test_full_text_level_cannot_be_invented_from_abstract(self):
        data = abstract_only()
        data["evidence"][0]["access_level"] = "full_text"
        self.assert_invalid(data, "access level contradicts")

    def test_full_text_acquisition_requires_actual_workspace_file(self):
        data = abstract_only()
        data["papers"][0]["availability"]["full_text"] = {"available": True, "obtained_at": "2026-01-01T00:00:00Z"}
        data["papers"][0]["full_text_path"] = "missing.txt"
        self.assert_invalid(data, "unsafe/missing full text")

    def test_abstract_limits_must_be_disclosed(self):
        data = abstract_only()
        data["report"] = data["report"].replace("abstract", "text").replace("Abstract", "Text")
        self.assert_invalid(data, "abstract-only evidence")

    def test_malformed_identifiers_and_source_urls_fail(self):
        for key, value, error in [("doi", "not-a-doi", "malformed DOI"), ("pmid", "../1", "malformed PMID"), ("url", "https://user:password@example.org/paper", "source URL must be HTTP")]:
            data = abstract_only()
            data["papers"][0][key] = value
            self.assert_invalid(data, error)

    def test_invalid_reference_and_missing_evidence_fail(self):
        data = abstract_only()
        data["references"][0]["paper_id"] = "missing"
        self.assert_invalid(data, "reference points to missing")
        data = abstract_only()
        data["claims"][0]["evidence_ids"] = ["missing"]
        self.assert_invalid(data, "claim references missing evidence")

    def test_unknown_citation_and_duplicate_number_fail(self):
        data = abstract_only()
        data["report"] += "Unknown citation [2].\n"
        self.assert_invalid(data, "citation [2]")
        data = abstract_only()
        data["references"].append(copy.deepcopy(data["references"][0]))
        self.assert_invalid(data, "duplicate n")

    def test_duplicate_papers_fail(self):
        data = abstract_only()
        duplicate = copy.deepcopy(data["papers"][0])
        duplicate["paper_id"] = "duplicate"
        data["papers"].append(duplicate)
        data["screening"].append({**data["screening"][0], "paper_id": "duplicate"})
        self.assert_invalid(data, "duplicate paper doi")

    def test_conflicting_evidence_remains_traceable(self):
        data = abstract_only()
        data["evidence"].append({**data["evidence"][0], "evidence_id": "e2", "finding": "Conflicting synthetic finding"})
        data["claims"][0].update(assessment="conflicting", evidence_ids=["e1", "e2"], explanation="Two synthetic abstract statements conflict")
        result = self.validate(data)
        self.assertIs(result["valid"], True, result)

    def test_source_failure_unknown_count_and_disclosure(self):
        data = zero_evidence()
        data["searches"][0].update(status="error", result_count=None, error="TimeoutError: fixture")
        data["report"] += "Source request failed; completeness unknown.\n"
        result = self.validate(data)
        self.assertIs(result["valid"], True, result)
        self.assertTrue(any("completeness unknown" in warning for warning in result["warnings"]))
        data["searches"][0]["result_count"] = 0
        self.assert_invalid(data, "failed search count must be null")

    def test_bool_reference_number_and_unknown_fields_fail(self):
        data = abstract_only()
        data["references"][0]["n"] = True
        self.assert_invalid(data, "invalid type")
        data = abstract_only()
        data["papers"][0]["invented"] = "field"
        self.assert_invalid(data, "unknown field")

    def test_claim_marker_and_screening_links_required(self):
        data = abstract_only()
        data["report"] = data["report"].replace("<!-- claim:c1 -->", "")
        self.assert_invalid(data, "exact claim marker missing")
        data = abstract_only()
        data["screening"][0]["decision"] = "exclude"
        self.assert_invalid(data, "included paper")

    def test_json_duplicate_keys_invalid_utf8_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_fixture(workspace, zero_evidence())
            (workspace / "references.json").write_text('[{"n":1,"n":2,"paper_id":"x"}]', encoding="utf-8")
            self.assertIs(validate_workspace(workspace)["valid"], False)
            (workspace / "report.md").write_bytes(b"\xff")
            result = validate_workspace(workspace)
            self.assertIs(result["valid"], False)
            self.assertTrue(any("UnicodeDecodeError" in error for error in result["errors"]))
            (workspace / "report.md").unlink()
            self.assertIs(validate_workspace(workspace)["valid"], False)

    def test_traversal_and_encoded_full_text_paths_fail(self):
        for path in ["../outside.txt", "C:/outside.txt", "%2e%2e/outside.txt", "folder\\file.txt"]:
            data = abstract_only()
            data["papers"][0]["availability"]["full_text"] = {"available": True, "obtained_at": "2026-01-01T00:00:00Z"}
            data["papers"][0]["full_text_path"] = path
            self.assert_invalid(data, "unsafe/missing full text")

    def test_symlink_and_hardlink_required_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_fixture(workspace, zero_evidence())
            outside = workspace / "untrusted.md"
            outside.write_text("outside", encoding="utf-8")
            (workspace / "report.md").unlink()
            try:
                (workspace / "report.md").symlink_to(outside)
            except OSError:
                # Windows may deny symlink privilege; hard links need no elevation.
                os.link(outside, workspace / "report.md")
            result = validate_workspace(workspace)
            self.assertIs(result["valid"], False, result)

    def test_claim_paragraph_requires_citation_not_only_bibliography(self):
        data = abstract_only()
        data["report"] = data["report"].replace("finding [1]", "finding")
        self.assert_invalid(data, "claim paragraph must cite")

    def test_full_text_report_wording_cannot_contradict_access(self):
        data = abstract_only()
        data["report"] += "We reviewed full text.\n"
        self.assert_invalid(data, "report claims full-text access")
        data["report"] = data["report"].replace("We reviewed full text", "We did not read full text")
        self.assertIs(self.validate(data)["valid"], True)

    def test_screening_counts_cannot_contradict_observed_records(self):
        data = abstract_only()
        data["report"] = data["report"].replace("included one record", "included 12 records")
        self.assert_invalid(data, "included count contradicts")

    def test_timestamps_require_utc_rfc3339(self):
        for timestamp in ["2026-01-01", "2026-01-01 00:00:00Z", "2026-01-01T00:00:00+08:00"]:
            data = zero_evidence()
            data["searches"][0]["searched_at"] = timestamp
            self.assert_invalid(data, "UTC RFC3339")

    def test_report_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_fixture(workspace, zero_evidence())
            with patch("pi_worker.research_tools.validation.REPORT_LIMIT", 10):
                self.assertIs(validate_workspace(workspace)["valid"], False)


class BridgeTests(unittest.TestCase):
    def test_exact_query_logging_dedup_and_partial_source_failure(self):
        calls = []
        def working(query, limit):
            calls.append((query, limit))
            return [{"title": "Synthetic", "doi": "10.1234/test", "provider": "fixture", "abstract": "abstract", "pdf_url": "https://example.org/paper.pdf"}] * 2
        def failing(query, limit):
            raise TimeoutError("secret-token-in-raw-error")
        with patch.dict("tools.academic.search.PROVIDER_SEARCHERS", {"crossref": working, "pubmed": failing}, clear=True):
            result = search("exact query", providers=["crossref", "pubmed"], limit=7)
        self.assertEqual(calls, [("exact query", 7)])
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(result["searches"][0]["result_count"], 2)
        self.assertIsNone(result["searches"][1]["result_count"])
        self.assertEqual(result["searches"][1]["status"], "error")
        self.assertNotIn("secret-token", json.dumps(result))
        self.assertFalse(result["papers"][0]["availability"]["full_text"]["available"])
        self.assertEqual(result["papers"][0]["verification"]["status"], "unknown")
        self.assertIsNone(result["database_total"])

    def test_identifier_timeout_is_unknown_not_verified(self):
        with patch("tools.academic.http_client.request_json", side_effect=TimeoutError("secret")):
            result = verify_identifier("doi", "10.1234/fixture")
        self.assertEqual(result["verification"]["status"], "unknown")
        self.assertNotIn("secret", json.dumps(result))

    def test_identifier_lookup_verifies_only_exact_identifier(self):
        with patch("tools.academic.http_client.request_json", return_value={"message": {"DOI": "10.1234/fixture", "title": ["Synthetic"]}}):
            result = verify_identifier("doi", "https://doi.org/10.1234/FIXTURE")
        self.assertEqual(result["verification"]["status"], "verified")
        with patch("tools.academic.http_client.request_json", return_value={"message": {"DOI": "10.1234/other"}}):
            self.assertEqual(verify_identifier("doi", "10.1234/fixture")["verification"]["status"], "failed")

    def test_invalid_provider_and_identifiers_fail_before_network(self):
        with self.assertRaises(ValueError):
            search("test", providers=["paid-saas"])
        for kind, value in [("doi", "not-doi"), ("pmid", "../1"), ("url", "https://localhost")]:
            with self.assertRaises(ValueError):
                verify_identifier(kind, value)

    def test_cli_schema_and_validation_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(["schema"]), 0)
        self.assertIn("records", json.loads(stream.getvalue()))
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(main(["validate", "--workspace", directory]), 2)
            self.assertIs(json.loads(stream.getvalue())["valid"], False)

    def test_prompt_omits_private_fields_and_contains_exact_contract(self):
        prompt = build_research_prompt({"topic": "test </research-job-json>", "mode": "brief", "user_id": "SECRET_USER", "email": "SECRET_EMAIL"})
        self.assertNotIn("SECRET_USER", prompt)
        self.assertNotIn("SECRET_EMAIL", prompt)
        self.assertEqual(prompt.count("</research-job-json>"), 1)
        self.assertIn("\\u003c/research-job-json\\u003e", prompt)
        self.assertIn("references.json", prompt)
        self.assertIn("full_text.available=false", prompt)

    def test_bundle_local_hashes_and_unreviewed_upstream(self):
        manifest = json.loads(BUNDLE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["bundle_revision"], BUNDLE_REVISION)
        self.assertFalse(manifest["upstream_content_vendored"])
        self.assertIsNone(manifest["upstream_audit"]["upstream_commit"])
        for candidate in manifest["upstream_audit"]["candidates"]:
            self.assertEqual(candidate["status"], "not_vendored_not_verified")
            self.assertIsNone(candidate["sha256"])
        for entry in manifest["local_files"]:
            content = (BUNDLE_MANIFEST.parent / entry["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"])

    def test_validator_import_never_loads_crewai(self):
        # A fresh interpreter ensures existing test runner imports cannot hide this.
        completed = subprocess.run([sys.executable, "-c", "import sys; import pi_worker.research_tools; assert 'crewai' not in sys.modules"], check=False)
        self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
