"""Synthetic recorded-evidence revisions; no model, network or full-text reads."""
import copy
import hashlib
import json
from unittest import mock
import unittest

from scientific_agent.pi_revision_validation import (
    MAX_OUTPUT_BYTES, MAX_RECORDS, MAX_REPORT_BYTES, MAX_TRACE_BYTES,
    RevisionValidationError, compose_revision,
    parse_revision_output, validate_revision_candidate, validate_revision_input)
from scientific_agent.pi_sections import index_sections


def manifest(report, artifacts):
    files = dict(artifacts, report=report.encode("utf-8"))
    return [dict(artifact_id=key, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                 media_type="text/markdown" if key == "report" else
                 "application/json" if key == "references" else "application/x-ndjson")
            for key, raw in files.items()]


def claim(claim_id, evidence_ids=("e1",), assessment="supported"):
    return dict(claim_id=claim_id, marker="<!-- claim:" + claim_id + " -->",
                evidence_ids=list(evidence_ids), assessment=assessment,
                explanation="Synthetic recorded evidence; scientific accuracy requires review.")


def revision_fixture(assessment="supported", newline="\r\n", report=None):
    """Reusable coherent v1 fixture for API/worker contract tests."""
    report = report or newline.join([
        "# 合成综述", "", "## 方法", "仅使用记录中的摘要材料；标识核验未独立重复。", "",
        "<!-- claim:c_out -->", "方法背景引用 [1]。", "", "## 结果", "",
        "<!-- claim:c1 -->", "原始综合结果 [1] [2]。", "", "### 子节", "原始结果细节。", "",
        "## 局限", "", "<!-- claim:c_tail -->", "研究局限 [2]。", "",
        "## 参考文献", "[1] 合成文献一。", "[2] 合成文献二。", ""])
    acquired = dict(available=True, obtained_at="2026-10-25T00:00:00Z")
    absent = dict(available=False, obtained_at=None)
    papers = [dict(paper_id="p" + str(n), title="Synthetic paper " + str(n), authors=["Fixture Author"],
                   year=2025, doi=None, pmid=None, url="https://example.org/p" + str(n), sources=["fixture"],
                   retrieved_at="2026-10-25T00:00:00Z", abstract="Synthetic abstract", full_text_path=None,
                   availability=dict(metadata=acquired, abstract=acquired, full_text=absent),
                   verification=dict(status="unknown", checked_at=None, source=None, error=None))
              for n in (1, 2)]
    evidence = [dict(evidence_id="e" + str(n), paper_id="p" + str(n), finding="Synthetic finding",
                     method="Fixture", limitations="Abstract only", access_level="abstract", locator="摘要")
                for n in (1, 2)]
    records = dict(papers=papers, evidence=evidence,
                   claims=[claim("c_out"), claim("c1", ("e1", "e2"), assessment), claim("c_tail", ("e2",))],
                   references=[dict(n=1, paper_id="p1"), dict(n=2, paper_id="p2")])
    artifacts = {kind: (json.dumps(rows, ensure_ascii=False, indent=2) if kind == "references" else
                       "".join(json.dumps(row, ensure_ascii=False) + newline for row in rows)).encode("utf-8")
                 for kind, rows in records.items()}
    index = index_sections(report)
    section = next(row for row in index["sections"] if row["title"] == "结果")
    output = dict(schema_version="1", section_markdown=newline.join([
        "## 结果", "", "<!-- claim:c2 -->", "修订后的综合结果仅限摘要所载信息 [1] [2]。", "",
        "### 子节", "修订细节与待研究问题。", "", ""]),
                  claims=[claim("c2", ("e1", "e2"), assessment)])
    return dict(report=report, items=manifest(report, artifacts), artifacts=artifacts,
                section_id=section["section_id"], base_sha256=index["report_sha256"], output=output)


class RevisionValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = revision_fixture()

    def compose(self, **updates):
        return compose_revision(**dict(self.fixture, **updates))

    def input(self, **updates):
        data = dict(self.fixture, **updates)
        data.pop("output", None)
        return validate_revision_input(**data)

    def assertCode(self, code, operation):
        with self.assertRaises(RevisionValidationError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.status, 422)
        self.assertNotIn("Synthetic", str(raised.exception))

    def report_fixture(self, report):
        self.fixture["report"] = report
        self.fixture["items"] = manifest(report, self.fixture["artifacts"])
        index = index_sections(report)
        self.fixture["base_sha256"] = index["report_sha256"]
        self.fixture["section_id"] = next(row["section_id"] for row in index["sections"]
                                          if row["title"] == "结果")

    def test_exact_utf8_range_and_material_bytes_are_preserved(self):
        selected = self.input()
        self.assertEqual(selected["target_claim_ids"], ["c1"])
        self.assertEqual(selected["material_policy"], "recorded_evidence_only")
        result = self.compose()
        section = selected["section"]
        raw = self.fixture["report"].encode("utf-8")
        replacement = self.fixture["output"]["section_markdown"].encode("utf-8")
        self.assertEqual(result["report_markdown"].encode("utf-8"),
                         raw[:section["start_byte"]] + replacement + raw[section["end_byte"]:])
        for key in ("papers", "evidence", "references"):
            self.assertIs(result["artifacts"][key], self.fixture["artifacts"][key])
        old_lines = self.fixture["artifacts"]["claims"].splitlines(keepends=True)
        self.assertTrue(result["artifacts"]["claims"].startswith(old_lines[0] + old_lines[2]))
        self.assertEqual(result["removed_claim_ids"], ["c1"])
        self.assertEqual(result["added_claim_ids"], ["c2"])
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["validation"]["provenance"]["source_verification"],
                         "recorded_evidence_not_independently_repeated")
        self.assertEqual(result["report_sha256"], hashlib.sha256(result["report_markdown"].encode()).hexdigest())

    def test_candidate_is_independently_rebuilt_with_and_without_output(self):
        result = self.compose()
        data = {key: value for key, value in self.fixture.items() if key != "output"}
        data.update(candidate_report=result["report_markdown"], candidate_items=result["items"],
                    candidate_artifacts=result["artifacts"])
        self.assertEqual(validate_revision_candidate(**data), result)
        self.assertEqual(validate_revision_candidate(**data, output=json.dumps(self.fixture["output"]).encode()), result)

    def test_candidate_material_or_external_bytes_cannot_change(self):
        result = self.compose()
        data = {key: value for key, value in self.fixture.items() if key != "output"}
        data.update(candidate_report=result["report_markdown"], candidate_items=result["items"],
                    candidate_artifacts=result["artifacts"])
        self.assertCode("candidate_mismatch", lambda: validate_revision_candidate(**dict(data,
            candidate_report=result["report_markdown"].replace("研究局限", "更改局限"))))
        for key in ("papers", "evidence", "references"):
            changed = dict(result["artifacts"], **{key: result["artifacts"][key] + b" "})
            self.assertCode("candidate_mismatch", lambda changed=changed:
                            validate_revision_candidate(**dict(data, candidate_artifacts=changed)))

    def test_candidate_claim_bytes_must_match_deterministic_composition(self):
        result = self.compose()
        changed = dict(result["artifacts"], claims=result["artifacts"]["claims"] + b"\n")
        data = {key: value for key, value in self.fixture.items() if key != "output"}
        data.update(candidate_report=result["report_markdown"],
                    candidate_items=manifest(result["report_markdown"], changed), candidate_artifacts=changed)
        self.assertCode("candidate_mismatch", lambda: validate_revision_candidate(**data))

    def test_unknown_citation_and_unknown_evidence_are_rejected(self):
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] += "额外内容 [99]。\r\n"
        self.assertCode("unknown_citation", lambda: self.compose(output=output))
        output = copy.deepcopy(self.fixture["output"])
        output["claims"][0]["evidence_ids"] = ["unknown"]
        self.assertCode("invalid_revision_input", lambda: self.compose(output=output))

    def test_claim_must_actually_cite_each_evidence_paper_in_its_paragraph(self):
        for replacement in ("", "`[2]`", "<!-- [2] -->", "[2](https://example.org)"):
            output = copy.deepcopy(self.fixture["output"])
            output["section_markdown"] = output["section_markdown"].replace("[2]", replacement)
            self.assertCode("claim_citation_mismatch", lambda output=output: self.compose(output=output))

    def test_unknown_duplicate_missing_and_malformed_markers_are_rejected(self):
        for replacement in ("<!-- claim:unknown -->", "", "<!-- claim:c2-->",
                            "<!-- claim:c2 -->\r\n<!-- claim:c2 -->"):
            output = copy.deepcopy(self.fixture["output"])
            output["section_markdown"] = output["section_markdown"].replace("<!-- claim:c2 -->", replacement)
            with self.assertRaises(RevisionValidationError):
                self.compose(output=output)

    def test_marker_in_code_quote_list_or_inline_code_is_rejected(self):
        prefixes = ["    ", "\t", " \t", "> ", "- ", "1. ", "`"]
        for prefix in prefixes:
            output = copy.deepcopy(self.fixture["output"])
            marker = prefix + "<!-- claim:c2 -->" + ("`" if prefix == "`" else "")
            output["section_markdown"] = output["section_markdown"].replace("<!-- claim:c2 -->", marker)
            with self.subTest(prefix=prefix):
                self.assertCode("invalid_claim_markers", lambda: self.compose(output=output))
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] = "## 结果\r\n\r\n```markdown\r\n<!-- claim:c2 -->\r\n结果 [1] [2]。\r\n```\r\n\r\n"
        self.assertCode("invalid_claim_markers", lambda: self.compose(output=output))

    def test_marker_in_multiline_inline_code_html_or_lazy_list_is_rejected(self):
        for prefix, suffix in (("`span\r\n", "\r\n`"), ("<div>\r\n", "\r\n</div>"),
                               ("<!-- ordinary\r\n", "\r\n-->"), ("- list\r\n", "")):
            output = copy.deepcopy(self.fixture["output"])
            output["section_markdown"] = "## 结果\r\n\r\n" + prefix + "<!-- claim:c2 -->\r\n结果 [1] [2]。" + suffix + "\r\n\r\n"
            self.assertCode("invalid_claim_markers", lambda: self.compose(output=output))

    def test_unclosed_markdown_cannot_swallow_external_sections(self):
        for append in ("```\r\n", "<div>\r\n", "<!-- unclosed\r\n", "<script>\r\n"):
            output = copy.deepcopy(self.fixture["output"])
            output["section_markdown"] += append
            self.assertCode("section_structure_conflict", lambda: self.compose(output=output))

    def test_boundary_newline_and_target_heading_are_required(self):
        for replacement in ("tail", "## 更名\r\n\r\n<!-- claim:c2 -->\r\n结果 [1] [2]。\r\n\r\n"):
            output = copy.deepcopy(self.fixture["output"])
            output["section_markdown"] = output["section_markdown"].rstrip() + replacement if replacement == "tail" else replacement
            self.assertCode("section_structure_conflict", lambda: self.compose(output=output))

    def test_references_and_parent_containing_references_are_not_editable(self):
        index = index_sections(self.fixture["report"])
        for title in ("参考文献", "合成综述"):
            row = next(row for row in index["sections"] if row["title"] == title)
            self.assertCode("references_not_editable", lambda: self.input(section_id=row["section_id"]))

    def test_conflicting_and_insufficient_cannot_be_upgraded_by_renaming_claim(self):
        for assessment in ("conflicting", "insufficient"):
            self.fixture = revision_fixture(assessment=assessment)
            self.compose()
            output = copy.deepcopy(self.fixture["output"])
            output["claims"][0]["assessment"] = "supported"
            self.assertCode("unsupported_assessment_upgrade", lambda: self.compose(output=output))

    def shared_evidence_fixture(self, assessment, inside=False):
        self.fixture = revision_fixture()
        rows = [json.loads(line) for line in self.fixture["artifacts"]["claims"].splitlines()]
        rows[0] = claim("c_out", ("e1", "e2"), assessment)
        self.fixture["artifacts"]["claims"] = b"".join(
            json.dumps(row).encode() + b"\r\n" for row in rows)
        report = self.fixture["report"].replace("方法背景引用 [1]。", "方法背景引用 [1] [2]。")
        output = self.fixture["output"]
        output["claims"] = [claim("c1", ("e1", "e2"))]
        output["section_markdown"] = output["section_markdown"].replace("claim:c2", "claim:c1")
        if inside:
            block = "<!-- claim:c_out -->\r\n方法背景引用 [1] [2]。\r\n\r\n"
            report = report.replace(block, "").replace("## 局限", block + "## 局限")
            output["section_markdown"] += block
            output["claims"].append(rows[0])
        self.report_fixture(report)

    def test_supported_claim_can_preserve_id_and_evidence_shared_with_uncertain_claim(self):
        for assessment in ("conflicting", "insufficient"):
            for inside in (False, True):
                for evidence_ids in (("e1", "e2"), ("e2", "e1")):
                    with self.subTest(assessment=assessment, inside=inside, evidence_ids=evidence_ids):
                        self.shared_evidence_fixture(assessment, inside)
                        self.fixture["output"]["claims"][0]["evidence_ids"] = list(evidence_ids)
                        selected = self.input()["section"]
                        result = self.compose()
                        raw = self.fixture["report"].encode("utf-8")
                        replacement = self.fixture["output"]["section_markdown"].encode("utf-8")
                        self.assertEqual(result["report_markdown"].encode("utf-8"),
                                         raw[:selected["start_byte"]] + replacement + raw[selected["end_byte"]:])
                        for kind in ("papers", "evidence", "references"):
                            self.assertEqual(result["artifacts"][kind], self.fixture["artifacts"][kind])
                        claims = [json.loads(line) for line in result["artifacts"]["claims"].splitlines()]
                        self.assertEqual(next(row["assessment"] for row in claims
                                              if row["claim_id"] == "c1"), "supported")
                        self.assertEqual(next(row["assessment"] for row in claims
                                              if row["claim_id"] == "c_out"), assessment)

    def test_supported_claim_cannot_be_renamed_when_evidence_is_shared_with_uncertain_claim(self):
        for assessment in ("conflicting", "insufficient"):
            self.shared_evidence_fixture(assessment)
            output = self.fixture["output"]
            output["claims"][0] = claim("c_new", ("e1", "e2"))
            output["section_markdown"] = output["section_markdown"].replace("claim:c1", "claim:c_new")
            self.assertCode("unsupported_assessment_upgrade", self.compose)

    def test_same_uncertain_claim_cannot_upgrade_with_identical_evidence(self):
        for assessment in ("conflicting", "insufficient"):
            self.fixture = revision_fixture(assessment=assessment)
            output = self.fixture["output"]
            output["claims"][0] = claim("c1", ("e1", "e2"))
            output["section_markdown"] = output["section_markdown"].replace("claim:c2", "claim:c1")
            self.assertCode("unsupported_assessment_upgrade", self.compose)

    def test_supported_claim_with_changed_evidence_cannot_bypass_uncertain_evidence_check(self):
        for assessment in ("conflicting", "insufficient"):
            self.shared_evidence_fixture(assessment)
            rows = [json.loads(line) for line in self.fixture["artifacts"]["claims"].splitlines()]
            rows[1]["evidence_ids"] = ["e1"]
            self.fixture["artifacts"]["claims"] = b"".join(
                json.dumps(row).encode() + b"\r\n" for row in rows)
            self.fixture["items"] = manifest(self.fixture["report"], self.fixture["artifacts"])
            self.assertCode("unsupported_assessment_upgrade", self.compose)

    def test_supported_claim_outside_selection_cannot_be_borrowed_with_identical_evidence(self):
        self.shared_evidence_fixture("conflicting", inside=True)
        rows = [json.loads(line) for line in self.fixture["artifacts"]["claims"].splitlines()]
        rows[2] = claim("c_tail", ("e1", "e2"))
        self.fixture["artifacts"]["claims"] = b"".join(
            json.dumps(row).encode() + b"\r\n" for row in rows)
        self.report_fixture(self.fixture["report"].replace("研究局限 [2]。", "研究局限 [1] [2]。"))
        output = self.fixture["output"]
        output["claims"][0] = claim("c_tail", ("e1", "e2"))
        output["section_markdown"] = output["section_markdown"].replace("claim:c1", "claim:c_tail")
        self.assertCode("invalid_claim_markers", self.compose)

    def test_external_claim_id_cannot_be_reused(self):
        output = copy.deepcopy(self.fixture["output"])
        output["claims"][0] = claim("c_out", ("e1", "e2"))
        output["section_markdown"] = output["section_markdown"].replace("claim:c2", "claim:c_out")
        self.assertCode("invalid_claim_markers", lambda: self.compose(output=output))

    def test_same_uncertain_claim_cannot_upgrade_by_dropping_evidence(self):
        for assessment in ("conflicting", "insufficient"):
            self.fixture = revision_fixture(assessment=assessment)
            output = copy.deepcopy(self.fixture["output"])
            output["claims"] = [claim("c1", ("e1",), "supported")]
            output["section_markdown"] = output["section_markdown"].replace("claim:c2", "claim:c1")
            self.assertCode("unsupported_assessment_upgrade", lambda: self.compose(output=output))

    def test_distinct_narrower_claim_is_structurally_allowed_with_human_review_warning(self):
        self.fixture = revision_fixture(assessment="conflicting")
        output = copy.deepcopy(self.fixture["output"])
        output["claims"] = [claim("c2", ("e1",), "supported")]
        result = self.compose(output=output)
        self.assertIn("structure_and_traceability_do_not_establish_scientific_accuracy",
                      result["validation"]["warnings"])

    def test_uncertain_claim_can_use_additional_already_recorded_evidence(self):
        self.fixture = revision_fixture(assessment="conflicting")
        evidence = json.loads(self.fixture["artifacts"]["evidence"].splitlines()[0])
        evidence["evidence_id"] = "e3"
        self.fixture["artifacts"]["evidence"] += json.dumps(evidence).encode() + b"\n"
        self.fixture["items"] = manifest(self.fixture["report"], self.fixture["artifacts"])
        output = copy.deepcopy(self.fixture["output"])
        output["claims"] = [claim("c1", ("e1", "e3"), "supported")]
        output["section_markdown"] = output["section_markdown"].replace("claim:c2", "claim:c1")
        self.compose(output=output)

    def test_schema_duplicate_keys_unknown_fields_and_invalid_utf8(self):
        raw = json.dumps(self.fixture["output"])
        self.assertCode("invalid_revision_output", lambda: parse_revision_output(raw[:-1] + ',"schema_version":"1"}'))
        self.assertCode("invalid_revision_output", lambda: parse_revision_output(b"\xff"))
        for unknown in ("full_text_path", "files", "report_markdown"):
            self.assertCode("invalid_revision_output", lambda unknown=unknown:
                            parse_revision_output(dict(self.fixture["output"], **{unknown: "../../outside"})))
        output = copy.deepcopy(self.fixture["output"])
        output["claims"][0]["file"] = "outside"
        self.assertCode("invalid_revision_output", lambda: parse_revision_output(output))
        self.assertCode("invalid_revision_output", lambda: parse_revision_output(dict(self.fixture["output"], section_markdown="\ud800")))
        self.assertCode("revision_too_large", lambda: parse_revision_output(b"x" * (MAX_OUTPUT_BYTES + 1)))

    def test_output_records_are_defensively_copied(self):
        parsed = parse_revision_output(self.fixture["output"])
        self.fixture["output"]["claims"][0]["evidence_ids"].append("changed")
        self.assertEqual(parsed["claims"][0]["evidence_ids"], ["e1", "e2"])

    def test_abstract_disclosure_and_false_fulltext_claims_are_checked(self):
        for assertion in ("已阅读全部文献全文。", "We reviewed the full texts."):
            output = copy.deepcopy(self.fixture["output"])
            output["section_markdown"] += assertion + "\r\n"
            self.assertCode("missing_evidence_disclosure", lambda: self.compose(output=output))
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] += "没有阅读全文。We have not reviewed the full texts.\r\n"
        self.compose(output=output)

    def test_fulltext_paths_are_never_opened(self):
        papers = [json.loads(line) for line in self.fixture["artifacts"]["papers"].splitlines()]
        papers[0]["full_text_path"] = "../../private-material.txt"
        papers[0]["availability"]["full_text"] = dict(available=True, obtained_at="2026-10-25T00:00:00Z")
        self.fixture["artifacts"]["papers"] = b"".join(json.dumps(row).encode() + b"\n" for row in papers)
        self.fixture["items"] = manifest(self.fixture["report"], self.fixture["artifacts"])
        with mock.patch("builtins.open", side_effect=AssertionError("must never open paths")):
            self.compose()

    def test_setext_target_duplicate_titles_and_nested_sections(self):
        report = self.fixture["report"].replace("## 结果\r\n", "结果\r\n-----\r\n")
        report = report.replace("原始结果细节。", "原始结果细节。\r\n\r\n### 结果\r\n重复标题细节。")
        self.report_fixture(report)
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] = output["section_markdown"].replace("## 结果\r\n", "结果\r\n-----\r\n")
        self.compose(output=output)

    def test_all_existing_target_claims_cannot_disappear(self):
        output = dict(schema_version="1", section_markdown="## 结果\r\n\r\n没有已登记论断的结论。\r\n\r\n", claims=[])
        self.assertCode("invalid_claim_markers", lambda: self.compose(output=output))

    def test_cr_only_lf_and_bom_reports_preserve_bytes(self):
        for newline in ("\r", "\n"):
            self.fixture = revision_fixture(newline=newline)
            self.report_fixture("\ufeff" + self.fixture["report"])
            result = self.compose()
            self.assertTrue(result["report_markdown"].startswith("\ufeff"))
            self.assertEqual(result["report_markdown"].count(newline),
                             self.fixture["report"].count(newline))

    def test_last_section_can_preserve_absent_eof_newline(self):
        report = self.fixture["report"]
        before, references = report.split("## 参考文献", 1)
        report = before.replace("## 局限", "## 参考文献" + references + "\r\n## 局限").rstrip()
        self.fixture["report"] = report
        self.fixture["items"] = manifest(report, self.fixture["artifacts"])
        index = index_sections(report)
        self.fixture["section_id"] = next(row["section_id"] for row in index["sections"] if row["title"] == "局限")
        self.fixture["base_sha256"] = index["report_sha256"]
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] = output["section_markdown"].replace("## 结果", "## 局限").rstrip()
        result = self.compose(output=output)
        self.assertFalse(result["report_markdown"].endswith(("\r", "\n")))

    def test_spanning_inline_code_cannot_change_external_prose(self):
        # No external marker inside this span is necessary for rejection.
        report = self.fixture["report"].replace("研究局限 [2]。", "研究局限 [2]。`existing`末尾。")
        self.report_fixture(report)
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] += "`new code span\r\n"
        self.assertCode("section_structure_conflict", lambda: self.compose(output=output))

    def test_replacement_cannot_claim_new_acquisition_even_if_history_had_fulltexts(self):
        papers = [json.loads(line) for line in self.fixture["artifacts"]["papers"].splitlines()]
        papers[0]["full_text_path"] = "historical-fulltext.txt"
        papers[0]["availability"]["full_text"] = dict(available=True, obtained_at="2026-10-25T00:00:00Z")
        self.fixture["artifacts"]["papers"] = b"".join(json.dumps(row).encode() + b"\n" for row in papers)
        self.fixture["items"] = manifest(self.fixture["report"], self.fixture["artifacts"])
        for sentence in ("本轮已阅读原文全文。", "我们重新检索数据库。", "We newly acquired the full texts.",
                         "We conducted additional database searches."):
            output = copy.deepcopy(self.fixture["output"])
            output["section_markdown"] += sentence + "\r\n"
            self.assertCode("missing_evidence_disclosure", lambda: self.compose(output=output))
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] += "本轮未阅读原文。We have not conducted additional database searches.\r\n"
        self.compose(output=output)

    def test_zero_evidence_still_requires_explicit_insufficiency(self):
        for kind in ("papers", "evidence"):
            self.fixture["artifacts"][kind] = b""
        self.fixture["artifacts"]["references"] = b"[]"
        claims = [claim(name, (), "insufficient") for name in ("c_out", "c1", "c_tail")]
        self.fixture["artifacts"]["claims"] = b"".join(json.dumps(row).encode() + b"\n" for row in claims)
        self.report_fixture(self.fixture["report"].replace("[1]", "").replace("[2]", ""))
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] = output["section_markdown"].replace("[1]", "").replace("[2]", "")
        output["claims"] = [claim("c2", (), "insufficient")]
        self.assertCode("missing_evidence_disclosure", lambda: self.compose(output=output))
        output["section_markdown"] += "当前记录的证据不足。\r\n"
        result = self.compose(output=output)
        self.assertIn("no_extracted_evidence", result["validation"]["warnings"])

    def test_resource_limits_count_utf8_bytes_records_and_total_artifacts(self):
        output = copy.deepcopy(self.fixture["output"])
        output["section_markdown"] = "科研" * (MAX_REPORT_BYTES // 6 + 1)
        self.assertCode("revision_too_large", lambda: self.compose(output=output))
        output = copy.deepcopy(self.fixture["output"])
        output["claims"] *= MAX_RECORDS + 1
        self.assertCode("invalid_revision_output", lambda: parse_revision_output(output))
        oversized = dict(self.fixture["artifacts"], papers=b" " * (MAX_TRACE_BYTES + 1))
        self.assertCode("invalid_revision_input", lambda: self.input(
            artifacts=oversized, items=manifest(self.fixture["report"], oversized)))

    def test_input_hashes_schema_and_claim_citations_are_validated(self):
        for key in self.fixture["artifacts"]:
            artifacts = dict(self.fixture["artifacts"], **{key: self.fixture["artifacts"][key] + b" "})
            self.assertCode("invalid_revision_input", lambda artifacts=artifacts: self.input(artifacts=artifacts))
        report = self.fixture["report"].replace("原始综合结果 [1] [2]。", "原始综合结果 [1]。")
        self.report_fixture(report)
        self.assertCode("claim_citation_mismatch", self.input)


if __name__ == "__main__":
    unittest.main()
