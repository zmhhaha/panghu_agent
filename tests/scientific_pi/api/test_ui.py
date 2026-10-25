"""Workbench behavior tests, using a small Gradio stub without a web server.

These exercise confirmation, identity, ambiguous submission, cancellation races
and exact downloads. They do not certify real Gradio/browser rendering.
"""
import copy
import hashlib
import importlib.util
import inspect
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import requests


class Component:
    created = []
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs, self.bindings = args, kwargs, []
        Component.created.append(self)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def click(self, **kwargs): self.bindings.append(dict(event='click', **kwargs))
    def change(self, **kwargs): self.bindings.append(dict(event='change', **kwargs))
    def input(self, **kwargs): self.bindings.append(dict(event='input', **kwargs))
    def tick(self, **kwargs): self.bindings.append(dict(event='tick', **kwargs))
    def load(self, **kwargs): self.bindings.append(dict(event='load', **kwargs))


def load_ui():
    gradio = types.ModuleType('gradio')
    for name in ('Blocks', 'Tab', 'Tabs', 'Row', 'Column', 'Group', 'Accordion', 'Radio',
                 'HTML', 'Markdown', 'Textbox', 'Button', 'DownloadButton', 'Dropdown', 'State', 'Timer'):
        setattr(gradio, name, Component)
    gradio.themes = types.SimpleNamespace(Soft=lambda **kwargs: None)
    gradio.update = lambda **kwargs: dict(__type__='update', **kwargs)
    gradio.skip = lambda: {'__type__': 'update'}
    gradio.Request = type('Request', (), {})
    path = Path(__file__).resolve().parents[3] / 'app' / 'ui' / 'scientific_agent.py'
    spec = importlib.util.spec_from_file_location('scientific_workbench_ui_fixture', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'gradio': gradio}), patch('requests.get') as get, patch('requests.post') as post:
        spec.loader.exec_module(module)
        assert get.call_count == post.call_count == 0
    return module


def response(data=None, status=200, content=b''):
    result = Mock(ok=200 <= status < 300, status_code=status, content=content)
    result.json.return_value = data
    return result


class UITests(unittest.TestCase):
    ID = '00000000-0000-4000-8000-000000000001'
    OTHER = '00000000-0000-4000-8000-000000000002'

    @classmethod
    def setUpClass(cls):
        Component.created.clear()
        cls.ui = load_ui()

    def setUp(self):
        self.session = self.ui.WorkbenchSession()
        self.request = types.SimpleNamespace(headers={'x-FORWARDED-user': 'trusted-user'}, session_hash='browser-a')
        self.fields = ['RAG在何种条件下减少幻觉？', '2020—2024', '工程团队选择方案', 'RAG与Self-RAG', '营销材料', '', 'review', 1800]
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp_patch = patch.object(tempfile, 'tempdir', self.temporary.name)
        self.temp_patch.start()
        self.addCleanup(self.temp_patch.stop)

    def preview(self, fields=None):
        return self.ui.prepare_plan(*(fields or self.fields), self.session, self.request)

    def submit(self, fields=None):
        return self.ui.submit_plan(*(fields or self.fields), self.session, self.request)

    def task(self, state='running', **changes):
        status = 'pending' if state == 'queued' else 'done' if state == 'succeeded' else 'failed' if state in ('failed', 'cancelled', 'timed_out') else 'running'
        return dict(id=self.ID, status=status, engine='pi', state=state, stage='search', progress=None, **changes)

    def open_report(self, content='# Review\n\nA finding [1].\n', engine='pi'):
        task = self.task('succeeded', report=content)
        task['engine'] = engine
        replies = [response(task)] + ([response({'items': []})] if engine == 'pi' else [])
        with patch.object(self.ui.requests, 'get', side_effect=replies):
            return self.ui.query_task(self.ID, self.session, self.request)

    def citation_data(self, content='# Review\n\nA finding [1].\n', **changes):
        paper = dict(paper_id='p1', title='A paper', authors=['Author'], year=2025,
                     doi='10.1234/example', pmid=None, url='https://example.org/paper',
                     availability={level: {'available': level != 'full_text', 'obtained_at': None}
                                   for level in ('metadata', 'abstract', 'full_text')},
                     verification={'status': 'unknown'})
        evidence = dict(evidence_id='e1', paper_id='p1', finding='An observed finding',
                        method='A bounded comparison', limitations='Small sample',
                        access_level='abstract', locator='Abstract, results')
        claim = dict(claim_id='c1', marker='<!-- claim:c1 -->', evidence_ids=['e1'],
                     assessment='supported', explanation='Supports the bounded finding')
        return dict(schema_version='1', availability='available', reason=None,
                    report_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    citations=[dict(n=1, status='resolved', reason=None, paper=paper,
                                    evidence=[evidence], claims=[claim])],
                    warnings=['recorded_assessment_not_independently_verified'], **changes)

    def test_callback_bindings_and_cancel_does_not_wait_for_polling_queue(self):
        bindings = [binding for component in Component.created for binding in component.bindings]
        for binding in bindings:
            parameters = inspect.signature(binding['fn']).parameters.values()
            required = [p for p in parameters if p.default is p.empty and p.name != 'request']
            self.assertEqual(len(binding.get('inputs', [])), len(required), binding['fn'].__name__)
        cancel = next(b for b in bindings if b['fn'] is self.ui.cancel_task)
        refresh = next(b for b in bindings if b['fn'] is self.ui.refresh_task)
        self.assertFalse(cancel['queue'])
        self.assertEqual(refresh['event'], 'tick')
        self.assertFalse(refresh['queue'])
        dropdown_labels = [c.kwargs.get('label') for c in Component.created if c.kwargs.get('choices')]
        self.assertNotIn('执行后端', dropdown_labels)
        self.assertFalse(inspect.isgeneratorfunction(self.ui.submit_plan))
        self.assertFalse(inspect.isgeneratorfunction(self.ui.refresh_task))

    def test_preview_is_read_only_and_confirmation_is_required(self):
        with patch.object(self.ui.requests, 'post') as post, patch.object(self.ui.requests, 'get') as get:
            before = self.submit()
            preview, confirm = self.preview()
        self.assertFalse(before[-1]['interactive'])
        self.assertTrue(confirm['interactive'])
        self.assertIn('2020—2024', preview)
        self.assertIn('不发送邮件', preview)
        self.assertIn('营销材料', preview)
        self.assertEqual(post.call_count, 0)
        self.assertEqual(get.call_count, 0)

    def test_changed_fields_cannot_reuse_confirmation_even_without_change_event(self):
        for index, changed in enumerate(('Changed', '2022', '学生', 'CRAG', '广告', 'a@example.com', 'brief', 600)):
            with self.subTest(index=index):
                self.preview()
                fields = self.fields[:]
                fields[index] = changed
                with patch.object(self.ui.requests, 'post') as post:
                    updates = self.submit(fields)
                self.assertEqual(post.call_count, 0)
                self.assertIn('重新预览', updates[0])
                self.assertIsNone(self.session.plan)

    def test_edit_event_invalidates_plan(self):
        self.preview()
        text, confirm = self.ui.invalidate_plan(self.session)
        self.assertIsNone(self.session.plan)
        self.assertFalse(confirm['interactive'])

    def test_combined_topic_limit_is_checked_without_truncation(self):
        fields = self.fields[:]
        fields[0] = '研' * 495
        with patch.object(self.ui.requests, 'post') as post:
            preview, confirm = self.preview(fields)
        self.assertIn('最多 500 字', preview)
        self.assertFalse(confirm['interactive'])
        self.assertEqual(post.call_count, 0)
        self.assertIsNone(self.session.plan)

    def test_email_and_duration_validation_before_any_submission(self):
        for index, value in ((5, 'bad-address'), (7, True), (7, 59), (7, 3601)):
            fields = self.fields[:]
            fields[index] = value
            with self.subTest(value=value), patch.object(self.ui.requests, 'post') as post:
                _, confirm = self.preview(fields)
                self.assertFalse(confirm['interactive'])
                self.assertEqual(post.call_count, 0)

    def test_submits_only_pi_with_trusted_header_and_complete_confirmed_topic(self):
        self.preview()
        confirmed_topic = self.session.plan['topic']
        key = self.session.plan['key']
        with patch.object(self.ui.requests, 'post', return_value=response(self.task('queued'))) as post:
            updates = self.submit()
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['engine'], 'pi')
        self.assertEqual(payload['topic'], confirmed_topic)
        self.assertNotIn('user_id', payload)
        self.assertEqual(payload['timeout_seconds'], 1800)
        self.assertEqual(post.call_args.kwargs['headers'], {'X-Forwarded-User': 'trusted-user', 'Idempotency-Key': key})
        self.assertEqual(updates[2], self.ID)
        self.assertTrue(updates[5]['active'])
        self.assertTrue(updates[4]['interactive'])

    def test_missing_identity_never_submits(self):
        self.preview()
        self.request.headers = {}
        with patch.object(self.ui.requests, 'post') as post:
            updates = self.submit()
        self.assertEqual(post.call_count, 0)
        self.assertIn('重新登录', updates[0])

    def test_owner_and_browser_session_cannot_be_swapped(self):
        self.preview()
        for request in (types.SimpleNamespace(headers={'x-forwarded-user': 'other'}, session_hash='browser-a'),
                        types.SimpleNamespace(headers=self.request.headers, session_hash='browser-b')):
            with patch.object(self.ui.requests, 'get') as get:
                updates = self.ui.query_task(self.ID, self.session, request)
            self.assertEqual(get.call_count, 0)
            self.assertIn('会话已变化', updates[0])
        separate = copy.deepcopy(self.ui.WorkbenchSession())
        self.assertIsNone(separate.owner)
        self.assertIsNot(separate.lock, self.session.lock)

    def test_initial_state_is_an_instance_usable_before_load_and_deepcopies_are_private(self):
        initial = self.ui.session.kwargs['value']
        self.assertIsInstance(initial, self.ui.WorkbenchSession)
        first, second = copy.deepcopy(initial), copy.deepcopy(initial)
        self.ui._bind(first, self.request)
        first.plan = {'fixture': True}
        self.assertIsNone(second.owner)
        self.assertIsNone(second.plan)
        self.assertIsNot(first.lock, second.lock)
        self.assertIs(self.ui.initialize_session(first), first)

    def test_generated_report_files_are_reused_and_cleanup_is_session_local(self):
        other_session = self.ui.WorkbenchSession()
        first = self.ui._report_download(self.ID, b'# Report', self.session)['value']
        repeated = self.ui._report_download(self.ID, b'# Report', self.session)['value']
        separate = self.ui._report_download(self.ID, b'# Report', other_session)['value']
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, separate)
        with patch.object(self.ui.requests, 'post') as post:
            self.ui.cleanup_session(self.session)
        self.assertEqual(post.call_count, 0)
        self.assertFalse(Path(first).exists())
        self.assertTrue(Path(separate).exists())
        self.assertEqual(self.session.files, {})
        self.ui.cleanup_session(self.session)

    def test_report_remains_visible_if_download_file_cannot_be_created(self):
        old = dict(id=self.ID, engine='crewai', status='done', report='# Preserved report')
        with patch.object(self.ui.requests, 'get', return_value=response(old)), patch.object(self.ui, '_save_file', side_effect=OSError):
            updates = self.ui.query_task(self.ID, self.session, self.request)
        self.assertEqual(updates[1], '# Preserved report')
        self.assertFalse(updates[3]['visible'])
        self.assertIn('历史记录重新下载', updates[0])

    def test_repreview_and_ambiguous_retry_keep_the_same_idempotency_key(self):
        self.preview()
        key = self.session.plan['key']
        self.preview()
        self.assertEqual(self.session.plan['key'], key)
        ambiguous = response({'detail': {'task_id': self.ID, 'retryable': True}}, status=503)
        with patch.object(self.ui.requests, 'post', side_effect=[ambiguous, response(self.task('queued'))]) as post:
            first = self.submit()
            second = self.submit()
        self.assertEqual(first[2], self.ID)
        self.assertTrue(first[5]['active'])
        self.assertTrue(first[-1]['interactive'])
        self.assertEqual(second[2], self.ID)
        self.assertEqual([call.kwargs['headers']['Idempotency-Key'] for call in post.call_args_list], [key, key])

    def test_network_failure_can_retry_without_recreating_plan(self):
        self.preview()
        key = self.session.plan['key']
        with patch.object(self.ui.requests, 'post', side_effect=requests.Timeout):
            updates = self.submit()
        self.assertTrue(updates[-1]['interactive'])
        self.assertEqual(self.session.plan['key'], key)
        with patch.object(self.ui.requests, 'post', return_value=response(self.task('queued'))) as post:
            self.submit()
        self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'], key)

    def test_explicit_repreview_after_rejection_uses_a_new_attempt(self):
        self.preview()
        key = self.session.plan['key']
        with patch.object(self.ui.requests, 'post', return_value=response({'detail': {'task_id': self.ID}}, 429)):
            updates = self.submit()
        self.assertFalse(updates[-1]['interactive'])
        self.assertFalse(updates[5]['active'])
        self.preview()
        self.assertNotEqual(self.session.plan['key'], key)

    def test_terminal_task_can_be_restarted_only_after_explicit_new_preview(self):
        self.preview()
        key = self.session.plan['key']
        with patch.object(self.ui.requests, 'post', return_value=response(self.task('cancelled'))):
            self.submit()
        _, confirm = self.preview()
        self.assertTrue(confirm['interactive'])
        self.assertNotEqual(self.session.plan['key'], key)

    def test_querying_another_task_does_not_disable_a_new_confirmed_draft(self):
        self.preview()
        with patch.object(self.ui.requests, 'get', return_value=response(self.task())):
            updates = self.ui.query_task(self.ID, self.session, self.request)
        self.assertEqual(updates[-1], {'__type__': 'update'})
        self.assertFalse(self.session.plan['submitted'])

    def test_preview_during_submission_preserves_the_original_recovery_key(self):
        self.preview()
        key = self.session.plan['key']
        self.session.submitting = True
        _, confirm = self.preview()
        self.assertFalse(confirm['interactive'])
        self.assertEqual(self.session.plan['key'], key)

    def test_repeated_confirmation_after_success_queries_instead_of_resubmitting(self):
        self.preview()
        with patch.object(self.ui.requests, 'post', return_value=response(self.task('queued'))) as post:
            self.submit()
        with patch.object(self.ui.requests, 'post') as post, patch.object(self.ui.requests, 'get', return_value=response(self.task())) as get:
            self.submit()
        self.assertEqual(post.call_count, 0)
        self.assertEqual(get.call_count, 1)

    def test_manual_query_uses_trusted_header_and_actual_state_and_unknown_progress(self):
        with patch.object(self.ui.requests, 'get', return_value=response(self.task('validating'))) as get:
            updates = self.ui.query_task(self.ID, self.session, self.request)
        self.assertIn('正在检查报告', updates[0])
        self.assertIn('检索文献', updates[0])
        self.assertIn('暂未提供百分比', updates[0])
        self.assertNotIn('0%', updates[0])
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Forwarded-User': 'trusted-user'})
        self.assertEqual(updates[2], self.ID)

    def test_transient_lookup_error_preserves_id_and_keeps_polling(self):
        with patch.object(self.ui.requests, 'get', return_value=response({}, 503)):
            updates = self.ui.query_task(self.ID, self.session, self.request)
        self.assertEqual(updates[2], self.ID)
        self.assertTrue(updates[5]['active'])

    def test_invalid_id_never_calls_api(self):
        with patch.object(self.ui.requests, 'get') as get, patch.object(self.ui.requests, 'post') as post:
            self.ui.query_task('../private', self.session, self.request)
            self.ui.cancel_task('../private', self.session, self.request)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(post.call_count, 0)

    def test_cancellation_requested_is_distinct_from_cancelled_and_publishing(self):
        for state, words, polling in (('cancel_requested', '等待停止', True), ('cancelled', '任务已取消', False), ('publishing', '不能撤回', True)):
            with self.subTest(state=state), patch.object(self.ui.requests, 'post', return_value=response(self.task(state))) as post:
                updates = self.ui.cancel_task(self.ID, self.session, self.request)
            self.assertIn(words, updates[0])
            self.assertEqual(updates[5]['active'], polling)
            self.assertFalse(updates[4]['interactive'])
            self.assertEqual(post.call_args.kwargs['headers'], {'X-Forwarded-User': 'trusted-user'})

    def test_stale_timer_cannot_overwrite_a_new_manual_task_view(self):
        self.ui._bind(self.session, self.request)
        self.ui._select(self.session, self.ID)
        entered, release = threading.Event(), threading.Event()
        old_results = []
        def fetch(url, **kwargs):
            if url.endswith(self.ID):
                entered.set()
                self.assertTrue(release.wait(3))
                return response(self.task())
            task = self.task()
            task['id'] = self.OTHER
            return response(task)
        with patch.object(self.ui.requests, 'get', side_effect=fetch):
            worker = threading.Thread(target=lambda: old_results.append(self.ui.refresh_task(self.session, self.request)))
            worker.start()
            self.assertTrue(entered.wait(3))
            current = self.ui.query_task(self.OTHER, self.session, self.request)
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(current[2], self.OTHER)
        self.assertEqual(self.session.task_id, self.OTHER)
        self.assertTrue(all(update == {'__type__': 'update'} for update in old_results[0]))

    def test_duplicate_timer_does_not_issue_another_lookup(self):
        self.ui._bind(self.session, self.request)
        self.ui._select(self.session, self.ID)
        self.session.inflight = True
        with patch.object(self.ui.requests, 'get') as get:
            updates = self.ui.refresh_task(self.session, self.request)
        self.assertEqual(get.call_count, 0)
        self.assertTrue(all(item == {'__type__': 'update'} for item in updates))

    def test_typing_another_task_id_invalidates_old_timer_and_disables_tracking(self):
        self.ui._bind(self.session, self.request)
        view = self.ui._select(self.session, self.ID)
        updates = self.ui.pause_tracking(self.session)
        self.assertFalse(self.session.polling)
        self.assertNotEqual(self.session.view, view)
        self.assertFalse(updates[4]['active'])
        stale = self.ui._render_task(self.ID, self.task(), self.session, view, {'X-Forwarded-User': 'trusted-user'})
        self.assertTrue(all(item == {'__type__': 'update'} for item in stale))

    def test_completed_report_download_is_exact_and_unknown_attachments_are_hidden(self):
        report = '# 中文报告\r\n\r\n原始内容\n'
        items = {'items': [{'artifact_id': 'evidence'}, {'artifact_id': '../private'}]}
        with patch.object(self.ui.requests, 'get', side_effect=[response(self.task('succeeded', report=report)), response(items)]) as get:
            updates = self.ui.query_task(self.ID, self.session, self.request)
        self.assertEqual(updates[1], report)
        self.assertEqual(Path(updates[3]['value']).read_bytes(), report.encode())
        self.assertEqual(updates[6]['choices'], [('证据记录', 'evidence')])
        self.assertFalse(updates[5]['active'])
        self.assertTrue(all(call.kwargs['headers']['X-Forwarded-User'] == 'trusted-user' for call in get.call_args_list))

    def test_artifact_download_checks_metadata_and_exact_bytes(self):
        content = b'{"evidence":"fixture"}\n'
        metadata = {'items': [{'artifact_id': 'evidence', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}]}
        with patch.object(self.ui.requests, 'get', side_effect=[response(metadata), response(content=content)]) as get:
            download, text = self.ui.download_attachment(self.ID, 'evidence', self.session, self.request)
        self.assertTrue(download['visible'])
        self.assertEqual(Path(download['value']).read_bytes(), content)
        self.assertTrue(download['value'].endswith('_evidence.jsonl'))
        self.assertEqual(get.call_count, 2)

    def test_artifact_hash_mismatch_never_exposes_download(self):
        metadata = {'items': [{'artifact_id': 'evidence', 'size': 3, 'sha256': 'a' * 64}]}
        with patch.object(self.ui.requests, 'get', side_effect=[response(metadata), response(content=b'abc')]):
            download, text = self.ui.download_attachment(self.ID, 'evidence', self.session, self.request)
        self.assertFalse(download['visible'])
        self.assertIn('传输不完整', text)

    def test_invalid_artifact_never_calls_api(self):
        with patch.object(self.ui.requests, 'get') as get:
            download, _ = self.ui.download_attachment(self.ID, '../private', self.session, self.request)
        self.assertFalse(download['visible'])
        self.assertEqual(get.call_count, 0)

    def test_stale_attachment_download_cannot_reappear_after_changing_selection(self):
        self.ui._bind(self.session, self.request)
        self.ui._select(self.session, self.ID)
        content = b'{}\n'
        metadata = {'items': [{'artifact_id': 'evidence', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}]}
        entered, release = threading.Event(), threading.Event()
        downloads = []
        def fetch(url, **kwargs):
            if url.endswith('/artifacts'):
                return response(metadata)
            entered.set()
            self.assertTrue(release.wait(3))
            return response(content=content)
        with patch.object(self.ui.requests, 'get', side_effect=fetch):
            worker = threading.Thread(target=lambda: downloads.append(self.ui.download_attachment(self.ID, 'evidence', self.session, self.request)))
            worker.start()
            self.assertTrue(entered.wait(3))
            self.ui.clear_attachment(self.session)
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(all(item == {'__type__': 'update'} for item in downloads[0]))
        self.assertFalse(self.session.files)

    def test_attachment_requires_querying_a_changed_task_id_first(self):
        self.ui._bind(self.session, self.request)
        self.ui._select(self.session, self.ID)
        with patch.object(self.ui.requests, 'get') as get:
            download, text = self.ui.download_attachment(self.OTHER, 'evidence', self.session, self.request)
        self.assertEqual(get.call_count, 0)
        self.assertFalse(download['visible'])
        self.assertIn('任务 ID 已变化', text)

    def test_invalid_attachment_size_is_rejected_before_binary_download(self):
        for size in (True, -1, self.ui.MAX_ATTACHMENT + 1):
            with self.subTest(size=size), patch.object(self.ui.requests, 'get', return_value=response({'items': [{'artifact_id': 'evidence', 'size': size, 'sha256': 'a' * 64}]})) as get:
                download, _ = self.ui.download_attachment(self.ID, 'evidence', self.session, self.request)
                self.assertFalse(download['visible'])
                self.assertEqual(get.call_count, 1)

    def test_expired_artifact_explains_report_remains_available(self):
        with patch.object(self.ui.requests, 'get', return_value=response({}, 410)):
            download, text = self.ui.download_attachment(self.ID, 'evidence', self.session, self.request)
        self.assertFalse(download['visible'])
        self.assertIn('已过期', text)
        self.assertIn('报告仍可', text)

    def test_legacy_report_query_and_history_download_need_no_pi_artifacts(self):
        old = dict(id=self.ID, engine='crewai', status='done', report='# Old report\n')
        with patch.object(self.ui.requests, 'get', return_value=response(old)) as get:
            updates = self.ui.query_task(self.ID, self.session, self.request)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(Path(updates[3]['value']).read_bytes(), b'# Old report\n')
        with patch.object(self.ui.requests, 'get', return_value=response(content=b'# Old report\r\n')) as get:
            download, _ = self.ui.download_review_history(self.ID, self.session, self.request)
        self.assertEqual(Path(download['value']).read_bytes(), b'# Old report\r\n')
        self.assertTrue(get.call_args.args[0].endswith('/scientific-download/' + self.ID))

    def test_history_uses_encoded_query_parameters_and_user_identity(self):
        with patch.object(self.ui.requests, 'get', return_value=response([])) as get:
            self.ui.search_reviews('a&limit=100', self.session, self.request)
        self.assertEqual(get.call_args.kwargs['params'], {'q': 'a&limit=100', 'limit': 10})
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Forwarded-User': 'trusted-user'})

    def test_history_search_returns_selectable_titles_and_uses_trusted_identity(self):
        rows = [dict(id=self.ID, topic='RAG的证据与局限', created_at='2026-10-24T09:10:00Z'),
                dict(id=self.OTHER, topic='芯片制造的技术演进', created_at='2026-10-23T08:00:00Z')]
        with patch.object(self.ui.requests, 'get', return_value=response(rows)) as get, patch.object(self.ui.requests, 'post') as post:
            choices, notice = self.ui.search_history('a&limit=100', self.session, self.request)
        self.assertEqual(get.call_args.kwargs['params'], {'q': 'a&limit=100', 'limit': 10})
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Forwarded-User': 'trusted-user'})
        self.assertEqual(choices['choices'], [('RAG的证据与局限  ·  2026-10-24', self.ID),
                                              ('芯片制造的技术演进  ·  2026-10-23', self.OTHER)])
        self.assertIsNone(choices['value'])
        self.assertIn('2 份报告', notice)
        self.assertIn('选择题目', notice)
        self.assertEqual(post.call_count, 0)

    def test_history_keeps_untrusted_titles_in_choice_text_and_uses_only_valid_ids(self):
        title = '<img src=x onerror=alert(1)>\n[链接](javascript:alert(1)) | 科研'
        rows = [dict(id='../private', topic='invalid'), None,
                dict(id=self.ID, topic=title, created_at='2026-10-24T09:00:00Z')]
        with patch.object(self.ui.requests, 'get', return_value=response(rows)):
            choices, notice = self.ui.search_history('', self.session, self.request)
        # Choices are Gradio text labels, never an HTML fragment assembled
        # from report data. Their values remain canonical IDs, not titles/URLs.
        self.assertEqual(choices['choices'], [(' '.join(title.split()) + '  ·  2026-10-24', self.ID)])
        self.assertNotIn(title, notice)
        self.assertIn('1 份报告', notice)

    def test_history_empty_and_failed_searches_clear_obsolete_selection(self):
        for data, status, expected in (([], 200, '未找到相关报告'), ({}, 200, '暂未完成'),
                                       ({'detail': 'private server error'}, 403, '没有操作权限'),
                                       ({'detail': 'private server error'}, 503, '暂时未完成')):
            with self.subTest(status=status, data=data), patch.object(self.ui.requests, 'get', return_value=response(data, status)):
                choices, notice = self.ui.search_history('', self.session, self.request)
            self.assertEqual(choices['choices'], [])
            self.assertIsNone(choices['value'])
            self.assertIn(expected, notice)
            self.assertNotIn('private server error', notice)
        with patch.object(self.ui.requests, 'get', side_effect=requests.Timeout):
            choices, notice = self.ui.search_history('', self.session, self.request)
        self.assertEqual(choices['choices'], [])
        self.assertIn('暂未完成', notice)

    def test_history_search_rejects_changed_browser_identity_before_api_call(self):
        self.preview()
        changed = types.SimpleNamespace(headers={'x-forwarded-user': 'someone-else'}, session_hash='browser-a')
        with patch.object(self.ui.requests, 'get') as get:
            choices, notice = self.ui.search_history('', self.session, changed)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(choices['choices'], [])
        self.assertIn('会话已变化', notice)

    def test_successful_confirmation_switches_to_report_view_without_changing_submission(self):
        self.preview()
        confirmed_topic = self.session.plan['topic']
        key = self.session.plan['key']
        with patch.object(self.ui.requests, 'post', return_value=response(self.task('queued'))) as post:
            updates = self.ui.submit_plan_view(*self.fields, self.session, self.request)
        self.assertEqual(len(updates), 10)
        self.assertEqual(updates[-1], {'__type__': 'update', 'selected': 'results'})
        self.assertEqual(updates[2], self.ID)
        self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'], key)
        self.assertEqual(post.call_args.kwargs['json']['topic'], confirmed_topic)

    def test_rejected_or_ambiguous_confirmation_does_not_switch_away_from_draft(self):
        for status in (403, 429, 503):
            with self.subTest(status=status):
                self.session = self.ui.WorkbenchSession()
                self.preview()
                detail = {'task_id': self.ID} if status == 503 else {}
                with patch.object(self.ui.requests, 'post', return_value=response({'detail': detail}, status)):
                    updates = self.ui.submit_plan_view(*self.fields, self.session, self.request)
                self.assertEqual(len(updates), 10)
                self.assertEqual(updates[-1], {'__type__': 'update'})
        self.session = self.ui.WorkbenchSession()
        with patch.object(self.ui.requests, 'post') as post:
            updates = self.ui.submit_plan_view(*self.fields, self.session, self.request)
        self.assertEqual(post.call_count, 0)
        self.assertEqual(updates[-1], {'__type__': 'update'})

    def test_opening_a_history_title_loads_exact_legacy_report_and_switches_tabs(self):
        content = '# 历史报告\r\n\r\n原始文献综述\n'
        old = dict(id=self.ID, engine='crewai', status='done', report=content)
        with patch.object(self.ui.requests, 'get', return_value=response(old)) as get, patch.object(self.ui.requests, 'post') as post:
            updates = self.ui.open_history(self.ID, self.session, self.request)
        self.assertTrue(get.call_args.args[0].endswith('/scientific-research/' + self.ID))
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Forwarded-User': 'trusted-user'})
        self.assertEqual(post.call_count, 0)
        self.assertEqual(updates[1], content)
        self.assertEqual(Path(updates[3]['value']).read_bytes(), content.encode())
        self.assertEqual(updates[-1], {'__type__': 'update', 'selected': 'results'})

    def test_task_recovery_navigation_occurs_only_after_a_successful_lookup(self):
        with patch.object(self.ui.requests, 'get', return_value=response(self.task())):
            updates = self.ui.query_task_view(self.ID, self.session, self.request)
        self.assertEqual(updates[-1], {'__type__': 'update', 'selected': 'results'})
        for status in (401, 404, 503):
            with self.subTest(status=status), patch.object(self.ui.requests, 'get', return_value=response({}, status)):
                updates = self.ui.query_task_view(self.ID, self.session, self.request)
            self.assertEqual(updates[-1], {'__type__': 'update'})
        with patch.object(self.ui.requests, 'get', side_effect=requests.Timeout):
            updates = self.ui.open_history(self.ID, self.session, self.request)
        self.assertEqual(updates[-1], {'__type__': 'update'})
        with patch.object(self.ui.requests, 'get') as get:
            empty = self.ui.open_history(None, self.session, self.request)
            invalid = self.ui.open_history('../private', self.session, self.request)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(empty[-1], {'__type__': 'update'})
        self.assertEqual(invalid[-1], {'__type__': 'update'})

    def test_obsolete_manual_lookup_cannot_navigate_after_newer_task_selection(self):
        entered, release = threading.Event(), threading.Event()
        old_updates = []
        def fetch(url, **kwargs):
            if url.endswith(self.ID):
                entered.set()
                self.assertTrue(release.wait(3))
                return response(self.task())
            task = self.task()
            task['id'] = self.OTHER
            return response(task)
        with patch.object(self.ui.requests, 'get', side_effect=fetch):
            worker = threading.Thread(target=lambda: old_updates.append(self.ui.query_task_view(self.ID, self.session, self.request)))
            worker.start()
            self.assertTrue(entered.wait(3))
            fresh = self.ui.open_history(self.OTHER, self.session, self.request)
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(fresh[2], self.OTHER)
        self.assertEqual(fresh[-1]['selected'], 'results')
        self.assertEqual(len(old_updates[0]), 10)
        self.assertTrue(all(update == {'__type__': 'update'} for update in old_updates[0]))

    def test_empty_history_selection_preserves_running_task_and_all_view_outputs(self):
        self.ui._bind(self.session, self.request)
        view = self.ui._select(self.session, self.ID)
        with patch.object(self.ui.requests, 'get') as get, patch.object(self.ui.requests, 'post') as post:
            updates = self.ui.open_history(None, self.session, self.request)
        self.assertEqual(len(updates), 10)
        self.assertTrue(all(update == {'__type__': 'update'} for update in updates))
        self.assertEqual(self.session.task_id, self.ID)
        self.assertEqual(self.session.view, view)
        self.assertTrue(self.session.polling)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(post.call_count, 0)

    def test_background_tracking_never_selects_a_navigation_tab(self):
        bindings = [binding for component in Component.created for binding in component.bindings]
        refresh = next(binding for binding in bindings if binding['fn'] is self.ui.refresh_task)
        cancel = next(binding for binding in bindings if binding['fn'] is self.ui.cancel_task)
        for binding in (refresh, cancel):
            self.assertEqual(len(binding['outputs']), 9)
            self.assertNotIn(self.ui.workspace_tabs, binding['outputs'])
        self.ui._bind(self.session, self.request)
        self.ui._select(self.session, self.ID)
        with patch.object(self.ui.requests, 'get', return_value=response(self.task())):
            updates = self.ui.refresh_task(self.session, self.request)
        self.assertEqual(len(updates), 9)

    def test_history_navigation_is_user_initiated_and_return_to_draft_does_no_work(self):
        bindings = self.ui.history_choices.bindings
        self.assertTrue(any(binding['event'] == 'input' and binding['fn'] is self.ui.open_history for binding in bindings))
        self.assertFalse(any(binding['event'] == 'change' for binding in bindings))
        self.preview()
        plan = self.session.plan
        with patch.object(self.ui.requests, 'get') as get, patch.object(self.ui.requests, 'post') as post:
            update = self.ui.return_to_draft()
        self.assertEqual(update, {'__type__': 'update', 'selected': 'new'})
        self.assertIs(self.session.plan, plan)
        self.assertFalse(plan['submitted'])
        self.assertEqual(get.call_count, 0)
        self.assertEqual(post.call_count, 0)

    def test_citations_are_requested_only_by_explicit_button_and_keep_output_contract(self):
        bindings = [b for c in Component.created for b in c.bindings]
        citation = next(b for b in bindings if b['fn'] is self.ui.show_citations)
        self.assertEqual(citation['event'], 'click')
        self.assertFalse(citation['queue'])
        self.assertEqual(citation['outputs'], [self.ui.report, self.ui.citation_notice])
        with patch.object(self.ui.requests, 'get', side_effect=[response(self.task('succeeded', report='A [1]')), response({'items': []})]) as get:
            updates = self.ui.query_task(self.ID, self.session, self.request)
        self.assertEqual(len(updates), 9)
        self.assertFalse(any(call.args[0].endswith('/citations') for call in get.call_args_list))

    def test_task_id_change_resets_citation_notice_for_manual_and_programmatic_updates(self):
        binding = next(b for b in self.ui.task_id_box.bindings if b['fn'] is self.ui.reset_citation_notice)
        # Gradio change fires for both typed values and values returned by
        # callbacks; input alone would miss report-history navigation.
        self.assertEqual(binding['event'], 'change')
        self.assertEqual(binding['inputs'], [])
        self.assertEqual(binding['outputs'], [self.ui.citation_notice])
        self.assertFalse(binding['queue'])
        self.open_report()
        with patch.object(self.ui.requests, 'get', return_value=response(self.citation_data())):
            _, old_notice = self.ui.show_citations(self.ID, self.session, self.request)
        self.assertIn('添加跳转', old_notice)
        self.ui.pause_tracking(self.session)
        with patch.object(self.ui.requests, 'get') as get, patch.object(self.ui.requests, 'post') as post:
            manual_notice = binding['fn']()
        self.assertNotIn('添加跳转', manual_notice)
        self.assertIn('查看引用资料', manual_notice)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(post.call_count, 0)

        self.open_report()
        with patch.object(self.ui.requests, 'get', return_value=response(self.citation_data())):
            self.ui.show_citations(self.ID, self.session, self.request)
        old = dict(id=self.OTHER, engine='crewai', status='done', report='# Another report\n')
        with patch.object(self.ui.requests, 'get', return_value=response(old)):
            history_updates = self.ui.open_history(self.OTHER, self.session, self.request)
        self.assertEqual(len(history_updates), 10)
        self.assertEqual(history_updates[2], self.OTHER)
        self.assertEqual(history_updates[1], old['report'])
        with patch.object(self.ui.requests, 'get') as get, patch.object(self.ui.requests, 'post') as post:
            history_notice = binding['fn']()
        self.assertEqual(history_notice, manual_notice)
        self.assertNotIn('添加跳转', history_notice)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(post.call_count, 0)
        self.assertEqual(self.session.report_source['text'], old['report'])

    def test_citation_view_keeps_exact_source_and_download_and_reuses_session_cache(self):
        content = '# 中文综述\r\n\r\nA finding [1].\r\n'
        opened = self.open_report(content)
        original_bytes = Path(opened[3]['value']).read_bytes()
        with patch.object(self.ui.requests, 'get', return_value=response(self.citation_data(content))) as get:
            rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
            repeated = self.ui.show_citations(self.ID, self.session, self.request)
        self.assertEqual(get.call_count, 1)
        self.assertTrue(get.call_args.args[0].endswith('/' + self.ID + '/citations'))
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Forwarded-User': 'trusted-user'})
        self.assertIn('href="#research-citation-', rendered)
        self.assertIn('An observed finding', rendered)
        self.assertIn('材料范围：摘要', rendered)
        self.assertIn('全文：未获取', rendered)
        self.assertIn('标识核验', rendered)
        self.assertIn('尚未确认', rendered)
        self.assertIn('未做专家核实', notice)
        self.assertEqual(repeated, (rendered, notice))
        self.assertEqual(self.session.report_source['text'], content)
        self.assertEqual(Path(opened[3]['value']).read_bytes(), original_bytes)
        self.assertEqual(original_bytes, content.encode())
        self.assertEqual(set(self.session.files), {(self.ID, hashlib.sha256(content.encode()).hexdigest(), '.md')})

    def test_citation_cache_is_discarded_when_task_changes_or_view_clears(self):
        self.open_report()
        with patch.object(self.ui.requests, 'get', return_value=response(self.citation_data())):
            self.ui.show_citations(self.ID, self.session, self.request)
        self.assertIsNotNone(self.session.citation_cache)
        self.ui.pause_tracking(self.session)
        self.assertIsNone(self.session.report_source)
        self.assertIsNone(self.session.citation_cache)
        with patch.object(self.ui.requests, 'get') as get:
            rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
        self.assertEqual(rendered, {'__type__': 'update'})
        self.assertIn('先打开', notice)
        self.assertEqual(get.call_count, 0)
        self.open_report()
        with patch.object(self.ui.requests, 'get', return_value=response({}, 503)):
            self.ui.query_task(self.OTHER, self.session, self.request)
        self.assertIsNone(self.session.report_source)
        self.assertIsNone(self.session.citation_cache)

    def test_citation_cache_does_not_survive_blank_view_from_invalid_confirmation(self):
        self.open_report()
        self.assertIsNotNone(self.session.report_source)
        updates = self.submit()
        self.assertEqual(updates[1], '')
        self.assertIsNone(self.session.report_source)
        with patch.object(self.ui.requests, 'get') as get:
            self.ui.show_citations(self.ID, self.session, self.request)
        self.assertEqual(get.call_count, 0)

    def test_citation_hash_mismatch_never_replaces_report(self):
        self.open_report()
        data = self.citation_data('A different report [1]')
        with patch.object(self.ui.requests, 'get', return_value=response(data)):
            rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
        self.assertEqual(rendered, {'__type__': 'update'})
        self.assertIn('与当前报告不一致', notice)
        self.assertIsNone(self.session.citation_cache)
        self.assertEqual(self.session.report_source['text'], '# Review\n\nA finding [1].\n')

    def test_expired_legacy_and_unavailable_citation_data_preserve_report(self):
        self.open_report()
        for availability, expected in (('expired', '已过期'), ('legacy', '历史报告'), ('unavailable', '暂不可用')):
            data = self.citation_data()
            data.update(availability=availability, reason='private implementation detail', citations=[])
            with self.subTest(availability=availability), patch.object(self.ui.requests, 'get', return_value=response(data)):
                rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
            self.assertEqual(rendered, {'__type__': 'update'})
            self.assertIn(expected, notice)
            self.assertIn('下载仍可使用', notice)
            self.assertNotIn('private implementation', notice)
            self.assertIsNone(self.session.citation_cache)
            self.assertIsNotNone(self.session.report_source)

    def test_citation_http_and_invalid_json_errors_are_safe_and_retryable(self):
        self.open_report()
        for status in (401, 404, 410, 503):
            with self.subTest(status=status), patch.object(self.ui.requests, 'get', return_value=response({'detail': 'private payload'}, status)):
                rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
            self.assertEqual(rendered, {'__type__': 'update'})
            self.assertNotIn('private payload', notice)
            self.assertIsNotNone(self.session.report_source)
        bad = response()
        bad.json.side_effect = ValueError('private malformed response')
        with patch.object(self.ui.requests, 'get', return_value=bad):
            rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
        self.assertEqual(rendered, {'__type__': 'update'})
        self.assertNotIn('private malformed', notice)
        with patch.object(self.ui.requests, 'get', side_effect=requests.Timeout):
            rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
        self.assertIn('稍后重试', notice)
        self.assertIsNone(self.session.citation_cache)

    def test_citation_identity_change_clears_evidence_and_cannot_use_cached_data(self):
        self.open_report()
        with patch.object(self.ui.requests, 'get', return_value=response(self.citation_data())):
            self.ui.show_citations(self.ID, self.session, self.request)
        other = types.SimpleNamespace(headers={'x-forwarded-user': 'other'}, session_hash='browser-a')
        with patch.object(self.ui.requests, 'get') as get:
            rendered, notice = self.ui.show_citations(self.ID, self.session, other)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(rendered, {'__type__': 'update'})
        self.assertIn('会话已变化', notice)
        self.assertIsNone(self.session.report_source)
        self.assertIsNone(self.session.citation_cache)

    def test_citation_cache_is_not_shared_by_deepcopied_sessions(self):
        self.open_report()
        with patch.object(self.ui.requests, 'get', return_value=response(self.citation_data())):
            self.ui.show_citations(self.ID, self.session, self.request)
        duplicate = copy.deepcopy(self.session)
        self.assertIsNone(duplicate.report_source)
        self.assertIsNone(duplicate.citation_cache)
        self.assertIsNot(duplicate.lock, self.session.lock)

    def test_late_citation_result_cannot_replace_a_new_task_view(self):
        self.open_report()
        entered, release, results = threading.Event(), threading.Event(), []
        def fetch(url, **kwargs):
            if url.endswith('/citations'):
                entered.set()
                self.assertTrue(release.wait(3))
                return response(self.citation_data())
            task = self.task()
            task['id'] = self.OTHER
            return response(task)
        with patch.object(self.ui.requests, 'get', side_effect=fetch):
            worker = threading.Thread(target=lambda: results.append(self.ui.show_citations(self.ID, self.session, self.request)))
            worker.start()
            self.assertTrue(entered.wait(3))
            self.ui.query_task(self.OTHER, self.session, self.request)
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [({'__type__': 'update'}, {'__type__': 'update'})])
        self.assertIsNone(self.session.citation_cache)
        self.assertEqual(self.session.task_id, self.OTHER)

    def test_late_citation_result_is_discarded_after_identity_changes(self):
        self.open_report()
        entered, release, results = threading.Event(), threading.Event(), []
        def fetch(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return response(self.citation_data())
        with patch.object(self.ui.requests, 'get', side_effect=fetch):
            worker = threading.Thread(target=lambda: results.append(self.ui.show_citations(self.ID, self.session, self.request)))
            worker.start()
            self.assertTrue(entered.wait(3))
            other = types.SimpleNamespace(headers={'x-forwarded-user': 'other'}, session_hash='browser-a')
            self.ui.query_task(self.ID, self.session, other)
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [({'__type__': 'update'}, {'__type__': 'update'})])
        self.assertIsNone(self.session.report_source)

    def test_citation_cards_escape_dynamic_text_and_hide_machine_warnings(self):
        content = 'A claim [1]. An unknown reference [99].'
        self.open_report(content)
        data = self.citation_data(content)
        injected = '<img src=x onerror=alert(1)> [bad](javascript:alert(1))'
        data['citations'][0]['paper'].update(title=injected, authors=[injected], url='javascript:alert(1)')
        data['citations'][0]['evidence'][0].update(finding=injected, method=injected, limitations=injected, locator=injected)
        data['citations'][0]['claims'][0].update(claim_id='\" onmouseover=alert(1)', explanation=injected)
        data['warnings'].append('private_unknown_machine_code')
        with patch.object(self.ui.requests, 'get', return_value=response(data)):
            rendered, _ = self.ui.show_citations(self.ID, self.session, self.request)
        self.assertNotIn('<img ', rendered)
        self.assertIn('&lt;img ', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertNotIn('onmouseover', rendered)
        self.assertNotIn('recorded_assessment_not_independently_verified', rendered)
        self.assertNotIn('private_unknown_machine_code', rendered)
        self.assertIn('尚未经过独立核实', rendered)
        self.assertIn('参考文献 [99]', rendered)
        self.assertIn('暂时无法追溯', rendered)

    def test_citation_links_allow_only_public_http_urls_and_identifier_fallbacks(self):
        for url in ('javascript:alert(1)', 'data:text/html,attack', '//example.org/p', 'https://user:password@example.org/p',
                    'https://example.org:999999/p', 'https://example.org/\nattack'):
            with self.subTest(url=url):
                self.assertIsNone(self.ui._source_url({'url': url}))
        self.assertEqual(self.ui._source_url({'url': 'https://example.org/p?q=1&v=2'}), 'https://example.org/p?q=1&v=2')
        self.assertEqual(self.ui._source_url({'doi': '10.1234/example'}), 'https://doi.org/10.1234/example')
        self.assertEqual(self.ui._source_url({'pmid': '12345'}), 'https://pubmed.ncbi.nlm.nih.gov/12345/')

    def test_citation_enhancement_preserves_markdown_code_existing_links_and_ranges(self):
        content = ('Ordinary [1][2]. Also [99].\n'
                   '`inline [3]` and ``code with ` and [4]``\n'
                   '```python\n[5]\n```\n~~~\n[6]\n~~~\n'
                   '[7](https://example.org/[8]) [9][source] [10]\n'
                   '[source]: https://example.org\n[10]: https://example.org\n'
                   '![11](https://example.org/image) [1, 2] [1-3] \\[12]\n'
                   '<a href="https://example.org">[13]</a> <!-- claim:[14] -->\n'
                   '    indented [15]\n')
        linked, numbers = self.ui._linked_citation_markdown(content, self.ID, 'a' * 64)
        self.assertEqual(numbers, [1, 2, 99])
        for preserved in ('`inline [3]`', '``code with ` and [4]``', '```python\n[5]\n```', '~~~\n[6]\n~~~',
                          '[7](https://example.org/[8])', '[9][source]', '[10]\n', '[source]: https://example.org',
                          '![11](https://example.org/image)', '[1, 2]', '[1-3]', '\\[12]',
                          '<a href="https://example.org">[13]</a>', '<!-- claim:[14] -->', '    indented [15]'):
            self.assertIn(preserved, linked)
        self.assertEqual(linked.count('class="research-citation-link"'), 3)

    def test_citation_scanner_handles_many_unmatched_brackets_without_losing_real_citation(self):
        content = '[' * 20000 + '\nA real citation [1].\n'
        linked, numbers = self.ui._linked_citation_markdown(content, self.ID, 'a' * 64)
        self.assertEqual(numbers, [1])
        self.assertTrue(linked.startswith('[' * 20000))
        self.assertIn('href="#research-citation-', linked)

    def test_citation_scanner_handles_unclosed_angle_brackets_as_plain_text(self):
        for prefix in ('<' * 20000, '<a' * 10000, '<' * 20000 + '>'):
            with self.subTest(prefix=prefix[:8]):
                content = prefix + '\nA real citation [1].\n'
                linked, numbers = self.ui._linked_citation_markdown(content, self.ID, 'a' * 64)
                self.assertEqual(numbers, [1])
                self.assertTrue(linked.startswith(prefix))

    def test_citation_unavailable_reason_is_explained_without_exposing_internal_codes(self):
        self.open_report()
        for reason, expected in (('report_mismatch', '版本不一致'), ('trace_too_large', '展示上限'),
                                 ('unsupported_citations', '引用格式'), ('invalid_trace', '关联检查'),
                                 ('unknown_private_reason', '暂不可用')):
            data = self.citation_data()
            data.update(availability='unavailable', reason=reason, citations=[])
            with self.subTest(reason=reason), patch.object(self.ui.requests, 'get', return_value=response(data)):
                rendered, notice = self.ui.show_citations(self.ID, self.session, self.request)
            self.assertEqual(rendered, {'__type__': 'update'})
            self.assertIn(expected, notice)
            self.assertNotIn(reason, notice)
            self.assertIn('下载仍可使用', notice)

    def test_citation_payload_shape_and_duplicate_numbers_are_rejected(self):
        self.open_report()
        variants = []
        duplicate = self.citation_data()
        duplicate['citations'].append(copy.deepcopy(duplicate['citations'][0]))
        variants.append(duplicate)
        for key, value in (('citations', {}), ('warnings', [None]), ('schema_version', '2')):
            data = self.citation_data()
            data[key] = value
            variants.append(data)
        for data in variants:
            with self.subTest(data=data), patch.object(self.ui.requests, 'get', return_value=response(data)):
                rendered, _ = self.ui.show_citations(self.ID, self.session, self.request)
            self.assertEqual(rendered, {'__type__': 'update'})
            self.assertIsNone(self.session.citation_cache)

    def test_same_view_rerender_uses_cached_evidence_without_refetching_citations(self):
        content = '# Review\n\nA finding [1].\n'
        self.open_report(content)
        with patch.object(self.ui.requests, 'get', return_value=response(self.citation_data(content))):
            rendered, _ = self.ui.show_citations(self.ID, self.session, self.request)
        with patch.object(self.ui.requests, 'get', return_value=response({'items': []})) as get:
            updates = self.ui._render_task(self.ID, self.task('succeeded', report=content), self.session,
                                           self.session.view, {'X-Forwarded-User': 'trusted-user'})
        self.assertEqual(updates[1], rendered)
        self.assertFalse(any(call.args[0].endswith('/citations') for call in get.call_args_list))


if __name__ == '__main__':
    unittest.main()
