"""Chapter editing, explicit adoption and stale-response isolation in the UI.

HTTP and Gradio are stubbed; root browser acceptance covers real rendering.
All research, source records and report prose used here are synthetic.
"""
import copy
import hashlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import requests

from scientific_agent.pi_sections import index_sections
from test_ui import Component, load_ui, response


ROOT = "10000000-0000-4000-8000-000000000001"
BASE = "10000000-0000-4000-8000-000000000002"
CANDIDATE = "10000000-0000-4000-8000-000000000003"
TASK = "10000000-0000-4000-8000-000000000004"
INTENT = "10000000-0000-4000-8000-000000000005"
OTHER = "10000000-0000-4000-8000-000000000006"
REPORT = "# 合成报告\r\n\r\n## 结果\r\n\r\n原始主题综合 [1]。\r\n\r\n## 局限\r\n仅有摘要。\r\n\r\n## 参考文献\r\n[1] 合成文献。\r\n"
NEW_REPORT = REPORT.replace("原始主题综合", "修订后的主题综合")
INSTRUCTION = "加强综合分析，保留证据限制。"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def version(version_id=BASE, content=REPORT, source_id=ROOT, parent=None):
    return dict(version_id=version_id, root_report_id=ROOT, source_report_id=source_id,
                parent_version_id=parent, report_sha256=digest(content),
                kind="candidate" if parent else "base", created_at="2026-10-25T00:00:00Z", content=content)


def writing_state(versions=None, current=BASE, sequence=0, source=ROOT, content=REPORT):
    return dict(schema_version="1", root_report_id=ROOT, current_version_id=current,
                current_sha256=digest(NEW_REPORT if current == CANDIDATE else REPORT) if current else None,
                selection_revision=sequence, versions=versions if versions is not None else [version()],
                source_report_id=source, source_sha256=digest(content))


class RevisionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Component.created.clear()
        cls.ui = load_ui()

    def setUp(self):
        self.session = self.ui.WorkbenchSession()
        self.request = types.SimpleNamespace(headers={"x-forwarded-user": "synthetic-owner"}, session_hash="browser")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_patch = patch.object(tempfile, "tempdir", self.temp.name)
        self.temp_patch.start()
        self.addCleanup(self.temp_patch.stop)
        self.open_reader()
        self.load()

    def open_reader(self, source_id=ROOT, content=REPORT):
        self.ui._bind(self.session, self.request, require_user=True)
        self.ui._select(self.session, source_id)
        self.session.polling = False
        self.session.report_source = dict(key=(self.session.owner, source_id, digest(content)), text=content)

    def load(self, state=None):
        with patch.object(self.ui.requests, "get", side_effect=[response(state or writing_state()), response(index_sections(REPORT))]):
            result = self.ui.load_writing_workspace(self.session, self.request)
        self.work = self.session.writing
        self.section_id = self.work["section_id"]
        return result

    def preview_data(self):
        section = dict(self.work["sections"][self.section_id])
        return dict(schema_version="1", parent=version(), section=section,
                    intent=dict(intent_id=INTENT, parent_version_id=BASE),
                    snapshot=dict(material_policy="recorded_evidence_only"))

    def preview(self, instruction=INSTRUCTION, duration=1200):
        with patch.object(self.ui.requests, "post", return_value=response(self.preview_data())):
            return self.ui.prepare_section_revision(self.section_id, instruction, duration, self.session, self.request)

    def task(self, state="running", candidate=None, **changes):
        status = "done" if state == "succeeded" else "failed" if state in ("failed", "cancelled", "timed_out") else "pending" if state == "queued" else "running"
        result = dict(id=TASK, task_kind="revision", intent_id=INTENT, root_report_id=ROOT,
                      parent_version_id=BASE, candidate_version_id=candidate, status=status,
                      state=state, engine="pi", report="NEVER INSERT THE TASK REPORT INTO THE READER")
        result.update(changes)
        return result

    def start(self, state="running"):
        self.preview()
        with patch.object(self.ui.requests, "post", return_value=response(self.task(state))):
            return self.ui.start_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)

    def ready(self):
        self.start()
        with patch.object(self.ui.requests, "get", return_value=response(self.task("succeeded", CANDIDATE))):
            return self.ui.refresh_revision_task(self.session, self.request)

    def comparison_data(self, can_adopt=True):
        before = self.work["sections"][self.section_id]["text"]
        after = before.replace("原始主题综合", "修订后的主题综合")
        return dict(schema_version="1", root_report_id=ROOT, parent=version(),
                    candidate=version(CANDIDATE, NEW_REPORT, TASK, BASE),
                    section=dict(section_id=self.section_id, before=before, after=after,
                                 before_sha256=digest(before), after_sha256=digest(after)),
                    diff=dict(format="unified", available=True, reason=None, text="-原始主题综合\n+修订后的主题综合\n"),
                    adoption=dict(expected_current_version_id=BASE, expected_selection_revision=0,
                                  base_sha256=digest(REPORT), candidate_sha256=digest(NEW_REPORT),
                                  comparison_token="a" * 64, can_adopt=can_adopt,
                                  reason=None if can_adopt else "parent_not_current"))

    def compare(self, data=None):
        with patch.object(self.ui.requests, "get", return_value=response(data or self.comparison_data())):
            return self.ui.compare_revision_candidate(self.session, self.request)

    def adoption_result(self, current=CANDIDATE, sequence=1):
        return dict(schema_version="1", root_report_id=ROOT, current_version_id=current,
                    current_sha256=digest(NEW_REPORT if current == CANDIDATE else REPORT), selection_revision=sequence,
                    adoption=dict(adoption_id=OTHER, target_version_id=CANDIDATE, report_sha256=digest(NEW_REPORT),
                                  selection_revision=1, replayed=sequence > 1))

    def assertSkipped(self, updates):
        self.assertTrue(all(update == {"__type__": "update"} for update in updates), updates)

    def test_editor_layout_has_explicit_actions_sanitized_prose_and_independent_timer(self):
        components = Component.created
        for label in ("阅读版本", "选择要修改的章节", "你希望怎样改进？", "逐行差异（− 删除，+ 新增）"):
            self.assertTrue(any(c.kwargs.get("label") == label for c in components), label)
        bindings = [b for c in components for b in c.bindings]
        timer = next(b for b in bindings if b["fn"] is self.ui.refresh_revision_task)
        self.assertFalse(timer["queue"])
        self.assertEqual(timer["event"], "tick")
        for function in (self.ui.start_section_revision, self.ui.adopt_revision_candidate, self.ui.cancel_revision_task):
            self.assertFalse(next(b for b in bindings if b["fn"] is function)["queue"])
        prose = [c for c in components if "research-revision-prose" in c.kwargs.get("elem_classes", [])]
        self.assertEqual(len(prose), 3)
        self.assertTrue(all(c.kwargs.get("sanitize_html") is True for c in prose))
        self.assertIn("#revision-controls", self.ui.CSS)
        download = self.ui.version_download
        self.assertIsNot(download.kwargs.get("visible"), False)
        self.assertFalse(download.kwargs["interactive"])

    def test_loading_is_read_only_and_excludes_reference_parent_sections(self):
        self.open_reader()
        with patch.object(self.ui.requests, "get", side_effect=[response(writing_state()), response(index_sections(REPORT))]) as get, patch.object(self.ui.requests, "post") as post:
            updates = self.ui.load_writing_workspace(self.session, self.request)
        self.assertEqual(len(updates), self.ui.WRITING_OUTPUTS)
        self.assertEqual(post.call_count, 0)
        self.assertEqual(get.call_count, 2)
        labels = [name for name, _ in updates[2]["choices"]]
        self.assertFalse(any("参考文献" in label or "合成报告" in label for label in labels))
        self.assertTrue(any("结果" in label for label in labels))

    def test_rerender_citation_enhancement_does_not_reset_editor_or_refetch(self):
        self.preview()
        plan = self.work["prepared"]
        with patch.object(self.ui.requests, "get") as get:
            self.assertSkipped(self.ui.load_writing_workspace(self.session, self.request))
        self.assertIs(self.work["prepared"], plan)
        self.assertEqual(get.call_count, 0)

    def test_late_writing_load_cannot_replace_new_report(self):
        self.open_reader()
        count = 0
        def get(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                self.open_reader(OTHER, "# A new report")
                return response(writing_state())
            return response(index_sections(REPORT))
        with patch.object(self.ui.requests, "get", side_effect=get):
            result = self.ui.load_writing_workspace(self.session, self.request)
        self.assertSkipped(result)
        self.assertIsNone(self.session.writing)
        self.assertEqual(self.session.task_id, OTHER)

    def test_prepare_is_explicit_and_repeated_preview_reuses_idempotency_key(self):
        with patch.object(self.ui.requests, "post", return_value=response(self.preview_data())) as post:
            first = self.ui.prepare_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
            second = self.ui.prepare_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertTrue(first[1]["interactive"])
        self.assertTrue(second[1]["interactive"])
        self.assertEqual(post.call_args_list[0].kwargs["headers"], post.call_args_list[1].kwargs["headers"])
        self.assertTrue(all(call.args[0].endswith("/revision-previews") for call in post.call_args_list))
        self.assertEqual(post.call_args.kwargs["json"]["base_sha256"], digest(REPORT))
        self.assertIsNone(self.work["tracking"])

    def test_first_prepare_imports_base_without_changing_idempotent_preview(self):
        self.open_reader()
        self.load(writing_state(versions=[], current=None))
        with patch.object(self.ui.requests, "post", return_value=response(self.preview_data())) as post:
            first = self.ui.prepare_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
            second = self.ui.prepare_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertEqual(self.work["version_id"], BASE)
        self.assertEqual(len(self.work["plans"]), 1)
        self.assertEqual(post.call_args_list[0].kwargs["headers"], post.call_args_list[1].kwargs["headers"])
        self.assertIn("初始写作稿", first[3])
        self.assertNotIn("第一次准备", first[3])
        self.assertTrue(second[1]["interactive"])
        with patch.object(self.ui.requests, "get", return_value=response(writing_state())):
            self.ui.refresh_writing_versions(self.session, self.request)
        self.assertEqual(self.work["current_version_id"], BASE)
        self.assertEqual(self.work["selection_revision"], 0)

    def test_edit_without_event_cannot_reuse_prepared_start(self):
        self.preview()
        with patch.object(self.ui.requests, "post") as post:
            updates = self.ui.start_section_revision(self.section_id, "changed", 1200, self.session, self.request)
        self.assertIn("重新预览", updates[0])
        self.assertFalse(updates[-1]["interactive"])
        self.assertEqual(post.call_count, 0)

    def test_late_prepare_and_late_comparison_after_edit_are_discarded(self):
        prepared = self.preview_data()
        def edited_preview(*args, **kwargs):
            self.ui.invalidate_revision_draft(self.session)
            return response(prepared)
        with patch.object(self.ui.requests, "post", side_effect=edited_preview):
            result = self.ui.prepare_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertSkipped(result)
        self.assertIsNone(self.work["prepared"])
        self.ready()
        compared = self.comparison_data()
        def edited_comparison(*args, **kwargs):
            self.ui.invalidate_revision_draft(self.session)
            return response(compared)
        with patch.object(self.ui.requests, "get", side_effect=edited_comparison):
            self.assertSkipped(self.ui.compare_revision_candidate(self.session, self.request))
        self.assertIsNone(self.work["comparison"])

    def test_start_keeps_reading_and_adopted_version_separate(self):
        before = (self.session.view, self.session.task_id, self.session.report_source, self.work["current_version_id"])
        updates = self.start("queued")
        self.assertEqual(updates[1], TASK)
        self.assertTrue(updates[3]["active"])
        self.assertEqual((self.session.view, self.session.task_id, self.session.report_source, self.work["current_version_id"]), before)

    def test_ambiguous_start_retries_same_intent_and_then_queries_same_task(self):
        self.preview()
        with patch.object(self.ui.requests, "post", side_effect=[requests.Timeout, response(self.task("queued"))]) as post:
            first = self.ui.start_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
            second = self.ui.start_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertTrue(first[-1]["interactive"])
        self.assertEqual(second[1], TASK)
        self.assertEqual([call.args[0] for call in post.call_args_list], [self.ui.API_BASE + f"/scientific-revision-previews/{INTENT}/start"] * 2)
        with patch.object(self.ui.requests, "post") as post, patch.object(self.ui.requests, "get", return_value=response(self.task())) as get:
            self.ui.start_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertEqual(post.call_count, 0)
        self.assertTrue(get.call_args.args[0].endswith(TASK))

    def test_ambiguous_start_with_task_id_resumes_by_get_without_new_dispatch(self):
        self.preview()
        with patch.object(self.ui.requests, "post", return_value=response(dict(detail=dict(task_id=TASK)), status=503)):
            result = self.ui.start_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertEqual(result[1], TASK)
        self.assertTrue(result[3]["active"])
        with patch.object(self.ui.requests, "get", return_value=response(self.task())) as get, patch.object(self.ui.requests, "post") as post:
            self.ui.start_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(post.call_count, 0)

    def test_delayed_start_cannot_replace_newer_manual_tracking(self):
        self.preview()
        def newer_tracking(*args, **kwargs):
            self.work["track_revision"] += 1
            self.work["tracking"] = dict(task_id=OTHER, polling=True)
            return response(self.task())
        with patch.object(self.ui.requests, "post", side_effect=newer_tracking):
            result = self.ui.start_section_revision(self.section_id, INSTRUCTION, 1200, self.session, self.request)
        self.assertSkipped(result)
        self.assertEqual(self.work["tracking"]["task_id"], OTHER)
        self.assertEqual(self.work["prepared"]["task_id"], TASK)

    def test_completed_revision_only_enables_comparison(self):
        old = self.session.report_source
        with patch.object(self.ui.requests, "post") as post:
            updates = self.ready()
        self.assertTrue(updates[4]["interactive"])
        self.assertFalse(updates[3]["active"])
        self.assertIs(self.session.report_source, old)
        self.assertEqual(self.work["current_version_id"], BASE)
        self.assertIsNone(self.work["comparison"])
        self.assertEqual(post.call_count, 0)

    def test_late_task_poll_cannot_write_new_reader_or_changed_owner(self):
        self.start()
        def new_reader(*args, **kwargs):
            self.open_reader(OTHER, "# New report")
            return response(self.task("succeeded", CANDIDATE))
        with patch.object(self.ui.requests, "get", side_effect=new_reader):
            self.assertSkipped(self.ui.refresh_revision_task(self.session, self.request))
        self.assertEqual(self.session.report_source["text"], "# New report")
        self.assertIsNone(self.session.writing)

    def test_cancel_runs_independently_and_publishing_is_not_cancellable(self):
        self.start()
        with patch.object(self.ui.requests, "post", return_value=response(self.task("publishing"))) as post:
            result = self.ui.cancel_revision_task(self.session, self.request)
        self.assertFalse(result[2]["interactive"])
        self.assertTrue(post.call_args.args[0].endswith(f"/{TASK}/cancel"))
        self.assertEqual(self.session.task_id, ROOT)

    def test_restore_rejects_other_root_without_displaying_task_report(self):
        with patch.object(self.ui.requests, "get", return_value=response(self.task(root_report_id=OTHER))):
            result = self.ui.restore_revision_task(TASK, self.session, self.request)
        self.assertIn("不属于", result[0])
        self.assertNotIn("NEVER INSERT", str(result))
        self.assertFalse(result[3]["active"])

    def test_compare_displays_exact_target_and_plain_text_diff_without_adopting(self):
        self.ready()
        data = self.comparison_data()
        data["diff"]["text"] = "<script>literal diff text</script>"
        with patch.object(self.ui.requests, "post") as post:
            result = self.compare(data)
        self.assertEqual(result[:3], (data["section"]["before"], data["section"]["after"], data["diff"]["text"]))
        self.assertTrue(result[3]["interactive"])
        self.assertEqual(post.call_count, 0)
        self.assertEqual(self.work["current_version_id"], BASE)

    def test_adoption_requires_comparison_then_sends_only_five_bound_fields(self):
        self.ready()
        with patch.object(self.ui.requests, "post") as post:
            before = self.ui.adopt_revision_candidate(self.session, self.request)
        self.assertFalse(before[1]["interactive"])
        self.assertEqual(post.call_count, 0)
        self.compare()
        reader = self.session.report_source
        with patch.object(self.ui.requests, "post", return_value=response(self.adoption_result())) as post:
            result = self.ui.adopt_revision_candidate(self.session, self.request)
        self.assertEqual(set(post.call_args.kwargs["json"]), set(self.ui.ADOPTION_FIELDS))
        self.assertEqual(post.call_args.kwargs["headers"]["X-Forwarded-User"], "synthetic-owner")
        self.assertEqual(self.work["current_version_id"], CANDIDATE)
        self.assertIs(self.session.report_source, reader)
        self.assertIn("已采用", result[0])

    def test_ambiguous_adoption_reuses_key_and_old_receipt_cannot_replace_latest_head(self):
        self.ready()
        self.compare()
        with patch.object(self.ui.requests, "post", side_effect=[requests.Timeout, response(self.adoption_result(OTHER, 3))]) as post:
            first = self.ui.adopt_revision_candidate(self.session, self.request)
            second = self.ui.adopt_revision_candidate(self.session, self.request)
        self.assertTrue(first[1]["interactive"])
        self.assertEqual(post.call_args_list[0].kwargs["headers"]["Idempotency-Key"], post.call_args_list[1].kwargs["headers"]["Idempotency-Key"])
        self.assertEqual(self.work["current_version_id"], OTHER)
        self.assertIn("后续更新", second[0])

    def test_late_refresh_cannot_roll_back_a_successful_adoption(self):
        self.ready()
        self.compare()
        def get_old_state(*args, **kwargs):
            with patch.object(self.ui.requests, "post", return_value=response(self.adoption_result())):
                self.ui.adopt_revision_candidate(self.session, self.request)
            return response(writing_state())
        with patch.object(self.ui.requests, "get", side_effect=get_old_state):
            self.assertSkipped(self.ui.refresh_writing_versions(self.session, self.request))
        self.assertEqual((self.work["current_version_id"], self.work["selection_revision"]), (CANDIDATE, 1))

    def test_late_adoption_response_cannot_roll_back_a_newer_refreshed_head(self):
        self.ready()
        self.compare()
        def post_old_result(*args, **kwargs):
            newer = writing_state(current=OTHER, sequence=3)
            with patch.object(self.ui.requests, "get", return_value=response(newer)):
                self.ui.refresh_writing_versions(self.session, self.request)
            return response(self.adoption_result())
        with patch.object(self.ui.requests, "post", side_effect=post_old_result):
            result = self.ui.adopt_revision_candidate(self.session, self.request)
        self.assertEqual((self.work["current_version_id"], self.work["selection_revision"]), (OTHER, 3))
        self.assertIn("较新的", result[0])

    def test_same_selection_revision_cannot_change_an_existing_head(self):
        bad = writing_state(current=OTHER, sequence=0)
        with patch.object(self.ui.requests, "get", return_value=response(bad)):
            result = self.ui.refresh_writing_versions(self.session, self.request)
        self.assertEqual((self.work["current_version_id"], self.work["selection_revision"]), (BASE, 0))
        self.assertIn("无法刷新", result[0])

    def test_delayed_old_comparison_cannot_enable_adopt_after_newer_head_refresh(self):
        self.ready()
        old_comparison = self.comparison_data()
        def get_old_comparison(*args, **kwargs):
            self.work.update(current_version_id=OTHER, current_sha256=digest(REPORT), selection_revision=2)
            return response(old_comparison)
        with patch.object(self.ui.requests, "get", side_effect=get_old_comparison):
            result = self.ui.compare_revision_candidate(self.session, self.request)
        self.assertFalse(result[3]["interactive"])
        self.assertIn("状态已变化", result[4])

    def test_stale_parent_candidate_is_readable_but_cannot_be_adopted(self):
        self.ready()
        result = self.compare(self.comparison_data(can_adopt=False))
        self.assertFalse(result[3]["interactive"])
        self.assertIn("较早", result[4])
        with patch.object(self.ui.requests, "post") as post:
            self.ui.adopt_revision_candidate(self.session, self.request)
        self.assertEqual(post.call_count, 0)

    def test_late_compare_error_and_late_adopt_success_do_not_override_new_tracking(self):
        self.ready()
        def new_tracking_error(*args, **kwargs):
            self.work["track_revision"] += 1
            self.work["tracking"] = dict(task_id=OTHER, polling=True)
            return response(status=503)
        with patch.object(self.ui.requests, "get", side_effect=new_tracking_error):
            self.assertSkipped(self.ui.compare_revision_candidate(self.session, self.request))
        self.work["tracking"] = dict(task_id=TASK, parent_version_id=BASE, candidate_version_id=CANDIDATE, polling=False)
        self.compare()
        def new_reader(*args, **kwargs):
            self.open_reader(OTHER, "# New reader")
            return response(self.adoption_result())
        with patch.object(self.ui.requests, "post", side_effect=new_reader):
            self.assertSkipped(self.ui.adopt_revision_candidate(self.session, self.request))
        self.assertEqual(self.session.report_source["text"], "# New reader")

    def test_large_diff_is_bounded_and_report_content_is_still_available(self):
        self.ready()
        data = self.comparison_data()
        data["diff"]["text"] = "x" * (512 * 1024 + 1)
        result = self.compare(data)
        self.assertIn("未在页面展开", result[2])
        self.assertEqual(result[1], data["section"]["after"])

    def test_saved_version_switch_and_download_use_exact_source_and_bytes(self):
        candidate = version(CANDIDATE, NEW_REPORT, TASK, BASE)
        self.work["versions"].append(candidate)
        old_view = self.session.view
        with patch.object(self.ui.requests, "get", side_effect=[response(candidate), response(index_sections(NEW_REPORT)), response(dict(items=[]))]):
            updates = self.ui.select_writing_version(CANDIDATE, self.session, self.request)
        self.assertEqual(updates[1], NEW_REPORT)
        self.assertEqual(self.session.task_id, TASK)
        self.assertGreater(self.session.view, old_view)
        self.assertEqual(self.session.writing["current_version_id"], BASE)
        selected_download = updates[9 + 16]
        self.assertIsNone(selected_download["value"])
        self.assertFalse(selected_download["interactive"])
        self.assertNotIn("visible", selected_download)
        with patch.object(self.ui.requests, "get", return_value=response(content=NEW_REPORT.encode())) as get:
            download, notice = self.ui.prepare_version_download(self.session, self.request)
        self.assertTrue(get.call_args.args[0].endswith(f"/{CANDIDATE}/download"))
        self.assertEqual(Path(download["value"]).read_bytes(), NEW_REPORT.encode())
        self.assertTrue(download["interactive"])
        self.assertNotIn("visible", download)
        self.assertIn("原始 Markdown", notice)

    def test_wrong_version_download_digest_and_late_download_are_rejected(self):
        with patch.object(self.ui.requests, "get", return_value=response(content=b"wrong")):
            result = self.ui.prepare_version_download(self.session, self.request)
        self.assertFalse(result[0]["interactive"])
        self.assertIsNone(result[0]["value"])
        self.assertIn("不一致", result[1])
        def late(*args, **kwargs):
            self.open_reader(OTHER, "# New report")
            return response(content=REPORT.encode())
        with patch.object(self.ui.requests, "get", side_effect=late):
            self.assertSkipped(self.ui.prepare_version_download(self.session, self.request))

    def test_changed_identity_clears_revision_state_before_network_call(self):
        self.ready()
        self.compare()
        self.request.headers = {"x-forwarded-user": "different-owner"}
        with patch.object(self.ui.requests, "post") as post:
            result = self.ui.adopt_revision_candidate(self.session, self.request)
        self.assertEqual(post.call_count, 0)
        self.assertFalse(result[1]["interactive"])
        self.assertIsNone(self.session.writing)
        self.assertIsNone(self.session.report_source)


if __name__ == "__main__":
    unittest.main()
