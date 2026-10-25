"""Offline citation-reader contracts; all paper and study data are synthetic."""
import copy
import hashlib
import json
import unittest

from scientific_agent.pi_citations import (
    MAX_RECORDS, MAX_REPORT_BYTES, MAX_RESPONSE_BYTES, MAX_TRACE_BYTES,
    TraceUnavailable, build_citation_trace, validate_manifest,
)


STAMP = "2026-01-01T00:00:00Z"
MEDIA = {"papers": "application/x-ndjson", "evidence": "application/x-ndjson",
         "claims": "application/x-ndjson", "references": "application/json"}


def paper(paper_id="p1"):
    return {"paper_id": paper_id, "title": "Synthetic fixture study",
            "authors": ["Fixture Author"], "year": 2026, "doi": "10.1234/fixture",
            "pmid": None, "url": "https://example.org/study", "sources": ["fixture"],
            "retrieved_at": STAMP,
            "availability": {"metadata": {"available": True, "obtained_at": STAMP},
                             "abstract": {"available": True, "obtained_at": STAMP},
                             "full_text": {"available": False, "obtained_at": None}},
            "abstract": "Synthetic abstract, not a scientific finding.",
            "full_text_path": None,
            "verification": {"status": "unknown", "checked_at": None, "source": None,
                             "error": "Synthetic fixture; not independently verified"}}


def fixture():
    return {
        "papers": [paper()],
        "evidence": [{"evidence_id": "e1", "paper_id": "p1", "finding": "Synthetic finding",
                      "method": "Fixture method", "limitations": "Fixture only",
                      "access_level": "abstract", "locator": "abstract, sentence 1"}],
        "claims": [{"claim_id": "c1", "marker": "<!-- claim:c1 -->", "evidence_ids": ["e1"],
                    "assessment": "supported", "explanation": "Records synthetic evidence"}],
        "references": [{"n": 1, "paper_id": "p1"}],
    }


REPORT = "# 合成报告\r\n\r\n<!-- claim:c1 -->\nSynthetic finding [1].\n\n# References\n[1] Fixture.\n"


def pack(report=REPORT, data=None, raw=None):
    data = fixture() if data is None else data
    artifacts = {}
    for kind in MEDIA:
        artifacts[kind] = (json.dumps(data[kind], ensure_ascii=False) if kind == "references"
                           else "".join(json.dumps(row, ensure_ascii=False) + "\n"
                                        for row in data[kind])).encode("utf-8")
    if raw:
        artifacts.update(raw)
    report_bytes = report.encode("utf-8")
    items = [{"artifact_id": "report", "media_type": "text/markdown", "size": len(report_bytes),
              "sha256": hashlib.sha256(report_bytes).hexdigest()}]
    items.extend({"artifact_id": kind, "media_type": MEDIA[kind], "size": len(content),
                  "sha256": hashlib.sha256(content).hexdigest()}
                 for kind, content in artifacts.items())
    return items, artifacts


class CitationTraceTests(unittest.TestCase):
    def trace(self, report=REPORT, data=None, raw=None):
        items, artifacts = pack(report, data, raw)
        return build_citation_trace(report, items, artifacts)

    def unavailable(self, code, report=REPORT, data=None, raw=None):
        items, artifacts = pack(report, data, raw)
        with self.assertRaises(TraceUnavailable) as caught:
            build_citation_trace(report, items, artifacts)
        self.assertEqual(caught.exception.code, code)

    def test_trace_resolves_explicit_chain_and_keeps_original_report(self):
        original = REPORT.encode("utf-8")
        result = self.trace()
        self.assertEqual(set(result), {"schema_version", "availability", "reason", "report_sha256",
                                       "citations", "warnings"})
        self.assertEqual((result["schema_version"], result["availability"], result["reason"]),
                         ("1", "available", None))
        self.assertEqual(result["report_sha256"], hashlib.sha256(original).hexdigest())
        citation = result["citations"][0]
        self.assertEqual(citation["n"], 1)
        self.assertEqual(citation["status"], "resolved")
        self.assertEqual(citation["evidence"][0]["evidence_id"], "e1")
        self.assertEqual(citation["claims"][0]["claim_id"], "c1")
        self.assertNotIn("full_text_path", citation["paper"])
        self.assertNotIn("abstract", citation["paper"])
        self.assertEqual(REPORT.encode("utf-8"), original)
        self.assertIn("recorded_assessment_not_independently_verified", result["warnings"])

    def test_manifest_returns_only_expected_public_metadata_copies(self):
        items, _ = pack()
        items[1]["path"] = "/private/workspace/papers.jsonl"
        items.append({"artifact_id": "searches", "media_type": "application/x-ndjson",
                      "size": 0, "sha256": hashlib.sha256(b"").hexdigest()})
        result = validate_manifest(REPORT, items)
        self.assertEqual(set(result), set(MEDIA))
        self.assertNotIn("path", result["papers"])
        result["papers"]["size"] = 0
        self.assertNotEqual(items[1]["size"], 0)

    def test_report_size_and_digest_bind_manifest_to_displayed_utf8(self):
        items, _ = pack()
        for field, value in (("size", items[0]["size"] + 1), ("sha256", "a" * 64)):
            with self.subTest(field=field), self.assertRaises(TraceUnavailable) as caught:
                changed = copy.deepcopy(items)
                changed[0][field] = value
                validate_manifest(REPORT, changed)
            self.assertEqual(caught.exception.code, "report_mismatch")
        with self.assertRaises(TraceUnavailable) as caught:
            validate_manifest(REPORT.replace("\r\n", "\n"), items)
        self.assertEqual(caught.exception.code, "report_mismatch")

    def test_manifest_missing_duplicate_or_wrong_metadata_is_rejected(self):
        items, _ = pack()
        cases = [items[1:], items + [items[1]], None, [None], items[:-1]]
        for field, value in (("size", True), ("size", -1), ("size", "12"),
                             ("sha256", "A" * 64), ("sha256", "short"),
                             ("media_type", None), ("media_type", "text/html"),
                             ("artifact_id", "../papers")):
            changed = copy.deepcopy(items)
            changed[1][field] = value
            cases.append(changed)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(TraceUnavailable) as caught:
                validate_manifest(REPORT, case)
            self.assertEqual(caught.exception.code, "manifest_invalid")

    def test_input_limits_fail_before_artifact_parsing(self):
        items, _ = pack()
        items[1]["size"] = MAX_TRACE_BYTES + 1
        with self.assertRaises(TraceUnavailable) as caught:
            validate_manifest(REPORT, items)
        self.assertEqual(caught.exception.code, "trace_too_large")
        with self.assertRaises(TraceUnavailable) as caught:
            validate_manifest("x" * (MAX_REPORT_BYTES + 1), [])
        self.assertEqual(caught.exception.code, "trace_too_large")

    def test_artifact_actual_size_hash_and_bytes_type_are_checked(self):
        items, artifacts = pack()
        for value in (b"damaged", artifacts["papers"] + b"\n", bytearray(artifacts["papers"]), None):
            with self.subTest(value=type(value)), self.assertRaises(TraceUnavailable) as caught:
                build_citation_trace(REPORT, items, dict(artifacts, papers=value))
            self.assertEqual(caught.exception.code, "artifact_mismatch")

    def test_bad_utf8_json_duplicate_keys_nan_and_extreme_numbers_are_controlled(self):
        for content in (b"\xff", b"{}\n{", b'{"paper_id":"x","paper_id":"y"}\n',
                        b'{"paper_id":NaN}\n', b'{"paper_id":Infinity}\n',
                        b'{"paper_id":' + b"1" * 6000 + b"}\n",
                        b"[" * 2000 + b"0" + b"]" * 2000,
                        b'{"paper_id":"\\ud800"}\n'):
            with self.subTest(prefix=content[:20]):
                self.unavailable("invalid_trace", raw={"papers": content})

    def test_record_count_limit_is_enforced(self):
        raw = b'{"n":1,"paper_id":"p1"}\n' * (MAX_RECORDS + 1)
        self.unavailable("trace_too_large", raw={"claims": raw})
        self.unavailable("trace_too_large", raw={"references": b"[" +
                         b','.join([b'{"n":1,"paper_id":"p1"}'] * (MAX_RECORDS + 1)) + b"]"})

    def test_missing_extra_fields_and_wrong_types_are_rejected(self):
        for kind, key in (("papers", "authors"), ("evidence", "finding"),
                          ("claims", "explanation"), ("references", "paper_id")):
            for action in ("missing", "extra", "type"):
                data = fixture()
                if action == "missing":
                    del data[kind][0][key]
                elif action == "extra":
                    data[kind][0]["unexpected"] = True
                else:
                    data[kind][0][key] = 23
                with self.subTest(kind=kind, action=action):
                    self.unavailable("invalid_trace", data=data)
        for kind, key in (("papers", "year"), ("references", "n")):
            data = fixture()
            data[kind][0][key] = True
            self.unavailable("invalid_trace", data=data)

    def test_duplicate_ids_reference_numbers_and_paper_mapping_are_rejected(self):
        for kind in MEDIA:
            data = fixture()
            data[kind].append(copy.deepcopy(data[kind][0]))
            with self.subTest(kind=kind):
                self.unavailable("invalid_trace", data=data)
        data = fixture()
        data["references"].append({"n": 2, "paper_id": "p1"})
        self.unavailable("invalid_trace", data=data)

    def test_missing_explicit_relationships_are_never_guessed(self):
        for kind, key, value in (("references", "paper_id", "missing"),
                                 ("evidence", "paper_id", "missing"),
                                 ("claims", "evidence_ids", ["missing"])):
            data = fixture()
            data[kind][0][key] = value
            with self.subTest(kind=kind):
                self.unavailable("invalid_trace", data=data)
        data = fixture()
        data["references"] = []
        self.unavailable("invalid_trace", data=data)

    def test_report_claim_markers_must_resolve_exactly_once(self):
        for report in (REPORT.replace("<!-- claim:c1 -->", ""),
                       REPORT + "\n<!-- claim:c1 -->", REPORT + "\n<!-- claim:missing -->"):
            self.unavailable("invalid_trace", report=report)
        data = fixture()
        data["claims"][0]["marker"] = "<!-- claim:other -->"
        self.unavailable("invalid_trace", data=data)

    def test_evidence_access_and_paper_availability_cannot_contradict(self):
        for level in ("full_text", "invalid"):
            data = fixture()
            data["evidence"][0]["access_level"] = level
            self.unavailable("invalid_trace", data=data)
        for level, field, value in (("metadata", "available", False),
                                     ("abstract", "available", False),
                                     ("abstract", "obtained_at", None),
                                     ("full_text", "available", True)):
            data = fixture()
            data["papers"][0]["availability"][level][field] = value
            self.unavailable("invalid_trace", data=data)
        data = fixture()
        data["papers"][0]["abstract"] = None
        self.unavailable("invalid_trace", data=data)
        data = fixture()
        data["evidence"][0]["locator"] = "page 1"
        self.unavailable("invalid_trace", data=data)

    def test_actual_full_text_access_is_disclosed_without_private_path(self):
        data = fixture()
        data["papers"][0]["availability"]["full_text"] = {"available": True, "obtained_at": STAMP}
        data["papers"][0]["full_text_path"] = "acquired/private-source.txt"
        data["evidence"][0].update(access_level="full_text", locator="page 2, section 3")
        result = self.trace(data=data)["citations"][0]
        self.assertTrue(result["paper"]["availability"]["full_text"]["available"])
        self.assertEqual(result["evidence"][0]["access_level"], "full_text")
        self.assertNotIn("private-source", json.dumps(result))

    def test_metadata_only_material_is_valid_and_explicit(self):
        data = fixture()
        data["papers"][0]["availability"]["abstract"] = {"available": False, "obtained_at": None}
        data["papers"][0]["abstract"] = None
        data["evidence"][0].update(access_level="metadata", locator="title and publication date")
        self.assertEqual(self.trace(data=data)["citations"][0]["evidence"][0]["access_level"], "metadata")

    def test_source_urls_are_filtered_and_identifiers_remain_plain_text(self):
        for url in ("javascript:alert(1)", "file:///private/file", "//example.org/paper",
                    "https://user:secret@example.org/paper", "https://example.org:bad/path",
                    "https://example.org/\nsecret", 'https://example.org/"onclick=alert(1)',
                    "https://example.org\\evil", "https://"):
            data = fixture()
            data["papers"][0].update(url=url, doi="<plain-doi>", pmid="<plain-pmid>")
            with self.subTest(url=url):
                result = self.trace(data=data)
                self.assertIsNone(result["citations"][0]["paper"]["url"])
                self.assertEqual(result["citations"][0]["paper"]["doi"], "<plain-doi>")
                self.assertIn("unsafe_source_url_omitted", result["warnings"])
        for url in (None, "https://example.org/paper?x=1&y=2", "http://example.org/source"):
            data = fixture()
            data["papers"][0]["url"] = url
            self.assertEqual(self.trace(data=data)["citations"][0]["paper"]["url"], url)

    def test_timestamps_accept_explicit_rfc3339_offsets_but_reject_missing_zone(self):
        data = fixture()
        data["papers"][0]["retrieved_at"] = "2026-10-25T08:00:00+08:00"
        self.assertEqual(self.trace(data=data)["availability"], "available")
        for stamp in ("2026-10-25T08:00:00", "2026-02-30T00:00:00Z", "2026-01-01T00:00:00+25:00"):
            data["papers"][0]["retrieved_at"] = stamp
            self.unavailable("invalid_trace", data=data)

    def test_verification_is_not_promoted_and_verified_needs_provenance(self):
        self.assertEqual(self.trace()["citations"][0]["paper"]["verification"]["status"], "unknown")
        data = fixture()
        data["papers"][0]["verification"]["status"] = "verified"
        self.unavailable("invalid_trace", data=data)
        data["papers"][0]["verification"].update(checked_at=STAMP, source="synthetic check")
        self.assertEqual(self.trace(data=data)["citations"][0]["paper"]["verification"]["status"], "verified")

    def test_unknown_visible_number_is_unresolved_and_not_matched_by_title(self):
        result = self.trace(report=REPORT + "\nOther statement [7].\n")
        unknown = result["citations"][1]
        self.assertEqual(unknown, {"n": 7, "status": "unresolved", "reason": "reference_missing",
                                   "paper": None, "evidence": [], "claims": []})

    def test_citation_scanner_ignores_code_comments_and_existing_markdown_links(self):
        ignored = ("\n```python\n[2] [3,4]\n```\n~~~\n[5]\n~~~\n"
                   "    [6]\n`[7]` and ``code `[8]` here``\n"
                   "[label [9]](https://example.org/(a)) ![10](https://example.org/img)\n"
                   "[11][source] [12][] [13]\n[source]: https://example.org/ref\n"
                   "[12]: https://example.org/twelve\n[13]: https://example.org/thirteen\n"
                   "<!-- hidden [14] --> <span title='[15]'>text</span> \\[16]\n")
        result = self.trace(report=REPORT + ignored)
        self.assertEqual([row["n"] for row in result["citations"]], [1])

    def test_visible_separate_numbers_resolve_without_guessing_groups_or_ranges(self):
        self.assertEqual([row["n"] for row in self.trace(report=REPORT + " [3] [2]")["citations"]], [1, 2, 3])
        for grouped in ("[1,2]", "[1-2]", "[1–2]", "[1; 2]"):
            self.unavailable("unsupported_citations", report=REPORT + grouped)
        self.unavailable("invalid_trace", report=REPORT + "[" + "9" * 80 + "]")

    def test_nested_nonlinks_do_not_require_expanding_every_label(self):
        nested = "[" * 4000 + "literal text" + "]" * 4000
        report = REPORT + "\n" + nested + "\n[other]: https://example.org/other\n"
        self.assertEqual([row["n"] for row in self.trace(report=report)["citations"]], [1])

    def test_unclosed_html_openers_do_not_hide_following_visible_citation(self):
        report = REPORT + "\n" + "<" * 20000 + " [2]\n"
        self.assertEqual([row["n"] for row in self.trace(report=report)["citations"]], [1, 2])

    def test_zero_evidence_and_insufficient_claim_are_valid(self):
        data = {kind: [] for kind in MEDIA}
        data["claims"] = [{"claim_id": "empty", "marker": "<!-- claim:empty -->", "evidence_ids": [],
                           "assessment": "insufficient", "explanation": "No synthetic evidence"}]
        result = self.trace(report="# Empty\n<!-- claim:empty -->\nInsufficient evidence.\n", data=data)
        self.assertEqual(result["citations"], [])
        self.assertIn("no_extracted_evidence", result["warnings"])
        data["claims"][0]["assessment"] = "supported"
        self.unavailable("invalid_trace", report="<!-- claim:empty -->", data=data)

    def test_conflicting_claim_can_link_two_papers_without_asserting_occurrence_support(self):
        data = fixture()
        data["papers"].append(paper("p2"))
        data["evidence"].append(dict(data["evidence"][0], evidence_id="e2", paper_id="p2"))
        data["references"].append({"n": 2, "paper_id": "p2"})
        data["claims"][0].update(assessment="conflicting", evidence_ids=["e1", "e2"])
        result = self.trace(report=REPORT + "\n[2] Second fixture.", data=data)
        self.assertEqual([row["paper"]["paper_id"] for row in result["citations"]], ["p1", "p2"])
        self.assertEqual([len(row["evidence"]) for row in result["citations"]], [1, 1])
        self.assertTrue(all(row["claims"][0]["assessment"] == "conflicting"
                            for row in result["citations"]))
        data["claims"][0]["evidence_ids"] = ["e1", "e1"]
        self.unavailable("invalid_trace", data=data)

    def test_one_claim_is_not_duplicated_for_multiple_evidence_rows_of_same_paper(self):
        data = fixture()
        data["evidence"].append(dict(data["evidence"][0], evidence_id="e2"))
        data["claims"][0]["evidence_ids"] = ["e1", "e2"]
        row = self.trace(data=data)["citations"][0]
        self.assertEqual(len(row["claims"]), 1)
        self.assertEqual(len(row["evidence"]), 2)

    def test_many_to_many_response_amplification_is_bounded_before_expansion(self):
        data = {kind: [] for kind in MEDIA}
        for index in range(80):
            paper_id, evidence_id = "p" + str(index), "e" + str(index)
            data["papers"].append(paper(paper_id))
            data["evidence"].append(dict(fixture()["evidence"][0],
                                         evidence_id=evidence_id, paper_id=paper_id))
            data["references"].append({"n": index + 1, "paper_id": paper_id})
        data["claims"] = [{"claim_id": "large", "marker": "<!-- claim:large -->",
                           "evidence_ids": [row["evidence_id"] for row in data["evidence"]],
                           "assessment": "supported", "explanation": "x" * 200000}]
        report = "<!-- claim:large -->\nSynthetic many-to-many fixture."
        items, artifacts = pack(report, data)
        self.assertLess(sum(len(value) for value in artifacts.values()), MAX_TRACE_BYTES)
        self.assertLess(sum(row["size"] for row in validate_manifest(report, items).values()), MAX_TRACE_BYTES)
        with self.assertRaises(TraceUnavailable) as caught:
            build_citation_trace(report, items, artifacts)
        self.assertEqual(caught.exception.code, "trace_too_large")

    def test_success_response_stays_within_wire_budget(self):
        result = self.trace()
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode("utf-8")), MAX_RESPONSE_BYTES)


if __name__ == "__main__":
    unittest.main()
