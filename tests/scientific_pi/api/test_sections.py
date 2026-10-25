"""Read-only section contracts over synthetic Markdown, without a web server."""
import hashlib
import unittest

from scientific_agent.pi_sections import MAX_REPORT_BYTES, MAX_SECTIONS, SectionError, index_sections, select_section


class SectionIndexTests(unittest.TestCase):
    def assert_selection(self, report, entry, expected):
        digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
        selected = select_section(report, entry["section_id"], digest)
        self.assertEqual(selected["text"], expected)
        self.assertEqual(selected["sha256"], hashlib.sha256(expected.encode("utf-8")).hexdigest())
        self.assertEqual(report.encode("utf-8")[entry["start_byte"]:entry["end_byte"]], expected.encode("utf-8"))

    def test_parent_contains_children_and_ends_at_peer(self):
        report = "# Parent\nIntro.\n## Child\nChild text.\n### Detail\nDetail.\n# Peer\nLast.\n"
        result = index_sections(report)
        entries = result["sections"]
        self.assertEqual([row["title"] for row in entries], ["Parent", "Child", "Detail", "Peer"])
        self.assert_selection(report, entries[0], report[:report.index("# Peer")])
        self.assert_selection(report, entries[1], report[report.index("## Child"):report.index("# Peer")])
        self.assert_selection(report, entries[2], "### Detail\nDetail.\n")
        self.assert_selection(report, entries[3], "# Peer\nLast.\n")

    def test_unicode_crlf_and_preamble_preserve_byte_ranges(self):
        report = "前言😀\r\n\r\n# 研究问题\r\n中文内容。\r\n## 方法 α\r\n保留 CRLF。\r\n# 结论\r\n末尾无换行"
        entries = index_sections(report)["sections"]
        self.assertEqual([row["title"] for row in entries], ["前言", "研究问题", "方法 α", "结论"])
        self.assertEqual(entries[1]["start_byte"], len("前言😀\r\n\r\n".encode("utf-8")))
        self.assert_selection(report, entries[0], "前言😀\r\n\r\n")
        self.assert_selection(report, entries[2], "## 方法 α\r\n保留 CRLF。\r\n")
        self.assert_selection(report, entries[3], "# 结论\r\n末尾无换行")

    def test_duplicate_titles_have_distinct_stable_ids(self):
        report = "## 方法\nFirst.\n## 方法\nSecond.\n"
        first = index_sections(report)
        self.assertEqual(first, index_sections(report))
        entries = first["sections"]
        self.assertEqual(entries[0]["title"], entries[1]["title"])
        self.assertNotEqual(entries[0]["section_id"], entries[1]["section_id"])
        self.assert_selection(report, entries[1], "## 方法\nSecond.\n")

    def test_ids_bind_full_report_even_when_target_range_is_unchanged(self):
        before = "# Target\nSame.\n# Other\nBefore.\n"
        after = before.replace("Before.", "After.")
        old = index_sections(before)["sections"][0]
        new = index_sections(after)["sections"][0]
        self.assertEqual(old["sha256"], new["sha256"])
        self.assertNotEqual(old["section_id"], new["section_id"])

    def test_setext_multiline_title_and_cr_only_line_endings(self):
        report = "Summary\rcontinued title\r===\rBody.\r\rMethods\r---\rContent.\r"
        entries = index_sections(report)["sections"]
        self.assertEqual([(row["title"], row["level"]) for row in entries], [("Summary continued title", 1), ("Methods", 2)])
        self.assert_selection(report, entries[1], "Methods\r---\rContent.\r")

    def test_fenced_code_and_wrong_or_short_closing_fence_are_ignored(self):
        report = "# Real\n```python\n# code\n~~~\n## still code\n``\n### still code too\n````\n## Actual\nText.\n"
        self.assertEqual([row["title"] for row in index_sections(report)["sections"]], ["Real", "Actual"])

    def test_tilde_fence_and_unclosed_fence(self):
        report = "# Real\n  ~~~~ example\n# code\n~~~\n## code too\n  ~~~~~\n## Actual\n```\n# hidden to end\n"
        self.assertEqual([row["title"] for row in index_sections(report)["sections"]], ["Real", "Actual"])

    def test_backtick_in_info_string_does_not_open_a_fence(self):
        report = "```bad`info\n# Actual\n"
        entries = index_sections(report)["sections"]
        self.assertEqual(entries[0]["kind"], "preamble")
        self.assertEqual(entries[1]["title"], "Actual")

    def test_indented_code_blockquotes_and_list_items_do_not_create_headings(self):
        report = "    # Indented\n\t## Tab\n  \t### Tab after spaces\n> # Quote\n- # List\n1. ## Ordered\n\n   ## Actual\nText.\n"
        self.assertEqual([row["title"] for row in index_sections(report)["sections"]], ["前言", "Actual"])

    def test_setext_like_code_list_or_thematic_break_is_not_heading(self):
        report = "    Code\n    ---\n\n- Item\n---\n\n***\n\n# Actual\n"
        self.assertEqual([row["title"] for row in index_sections(report)["sections"]], ["前言", "Actual"])

    def test_html_code_comments_and_block_tags_do_not_create_headings(self):
        report = "<pre>\n# In pre\n</pre>\n<!--\n# In comment\n-->\n<div>\n## In div\n</div>\n\n# Actual\n"
        self.assertEqual([row["title"] for row in index_sections(report)["sections"]], ["前言", "Actual"])

    def test_atx_syntax_closing_hashes_and_empty_heading(self):
        report = "#NoSpace\n####### TooMany\n\\# Escaped\n\n### Title ###\nBody\n##\n"
        self.assertEqual([(row["title"], row["level"]) for row in index_sections(report)["sections"]],
                         [("前言", 0), ("Title", 3), ("未命名章节", 2)])

    def test_atx_only_closing_hashes_are_empty_but_escaped_hashes_are_text(self):
        report = "# ###\n## \\#\n### Title###\n"
        self.assertEqual([row["title"] for row in index_sections(report)["sections"]],
                         ["未命名章节", "\\#", "Title###"])

    def test_no_headings_returns_exact_whole_report(self):
        report = "无标题报告😀\r\n第二行\n"
        entry = index_sections(report)["sections"][0]
        self.assertEqual((entry["kind"], entry["level"], entry["title"], entry["editable"]), ("preamble", 0, "正文", True))
        self.assert_selection(report, entry, report)

    def test_empty_and_whitespace_only_ranges_are_not_editable(self):
        for report in ("", " \r\n\t\n", "\u3000"):
            with self.subTest(report=report):
                entry = index_sections(report)["sections"][0]
                self.assertFalse(entry["editable"])
                self.assert_selection(report, entry, report)
        entries = index_sections("\r\n# Title\n")["sections"]
        self.assertFalse(entries[0]["editable"])
        self.assertTrue(entries[1]["editable"])

    def test_bom_is_preserved_while_first_heading_is_recognized(self):
        report = "\ufeff# Title\r\nText\r\n"
        entries = index_sections(report)["sections"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Title")
        self.assert_selection(report, entries[0], report)

    def test_top_level_ranges_cover_every_byte_without_gaps(self):
        report = "Lead\n\n## Two\nText\n### Three\nChild\n# One\nText\n## Two again\nTail"
        entries = index_sections(report)["sections"]
        raw = report.encode("utf-8")
        segments, current_end = [], 0
        for row in entries:
            if row["start_byte"] >= current_end:
                self.assertEqual(row["start_byte"], current_end)
                segments.append(raw[row["start_byte"]:row["end_byte"]])
                current_end = row["end_byte"]
        self.assertEqual(current_end, len(raw))
        self.assertEqual(b"".join(segments), raw)

    def test_stale_base_and_foreign_section_return_conflict(self):
        report = "# Title\nOriginal.\n"
        indexed = index_sections(report)
        section_id = indexed["sections"][0]["section_id"]
        for args, code in (((report + "Changed", section_id, indexed["report_sha256"]), "stale_report"),
                           ((report, "unknown-section", indexed["report_sha256"]), "section_not_found")):
            with self.subTest(code=code), self.assertRaises(SectionError) as raised:
                select_section(*args)
            self.assertEqual((raised.exception.code, raised.exception.status, raised.exception.status_code), (code, 409, 409))

    def test_invalid_input_and_resource_limits(self):
        for report, code in ((None, "invalid_report"), (b"# bytes", "invalid_report"),
                             ("\ud800", "invalid_report"), ("x" * (MAX_REPORT_BYTES + 1), "report_too_large"),
                             ("中" * (MAX_REPORT_BYTES // 3 + 1), "report_too_large")):
            with self.subTest(code=code), self.assertRaises(SectionError) as raised:
                index_sections(report)
            self.assertEqual((raised.exception.code, raised.exception.status), (code, 422))
        with self.assertRaises(SectionError) as raised:
            index_sections("# Title\n" * (MAX_SECTIONS + 1))
        self.assertEqual(raised.exception.code, "too_many_sections")
        with self.assertRaises(SectionError) as raised:
            index_sections("Lead\n\n" + "# Title\n" * MAX_SECTIONS)
        self.assertEqual(raised.exception.code, "too_many_sections")

    def test_selection_requires_valid_base_and_id(self):
        for section_id, base, code in (("id", None, "invalid_base_sha256"), ("id", "A" * 64, "invalid_base_sha256"),
                                       (None, "a" * 64, "invalid_section_id"), ("", "a" * 64, "invalid_section_id")):
            with self.subTest(code=code), self.assertRaises(SectionError) as raised:
                select_section("# Report\n", section_id, base)
            self.assertEqual(raised.exception.code, code)

    def test_many_unclosed_markers_remain_plain_text(self):
        report = "<" * 20000 + "\n[" * 20000 + "\n\n# Actual\n"
        self.assertEqual([row["title"] for row in index_sections(report)["sections"]], ["前言", "Actual"])


if __name__ == "__main__":
    unittest.main()
