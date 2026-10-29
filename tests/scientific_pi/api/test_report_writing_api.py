"""Local phase-2 writing APIs; no model, email or production services."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.api.scientific_agent import create_app
from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_projection import PiProjection
from scientific_agent.pi_sections import index_sections
from test_api import MemoryDB, FakeWorker
from test_citation_api import records_for


class ReportWritingAPITests(unittest.TestCase):
    REPORT = ('前言：合成演示，不作学术引用。\r\n\r\n'
              '# 综述\r\n\r\n## 分析\r\n<!-- claim:c1 -->\r\n合成发现 [1]。\r\n\r\n'
              '```python\r\n# 不是章节\r\n```\r\n\r\n## 分析\r\n另一段分析 [1]。\r\n')

    def setUp(self):
        self.database, self.worker = MemoryDB(), FakeWorker()
        self.projection = PiProjection(self.database, self.worker, 'skills-pinned', 'runtime-pinned', lambda *args: None)
        self.app = create_app(self.database, self.worker, self.projection, start_reconciler=False)
        self.client = TestClient(self.app)
        self.client.headers['X-Forwarded-User'] = 'trusted-user'
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(self.database.connection.close)
        self.items, self.artifacts = records_for(self.REPORT)
        self.task_id = self.client.post('/scientific-research', json={'topic': 'Synthetic phase-2 fixture'}).json()['id']
        self.worker.complete(self.task_id, self.REPORT)
        self.projection.reconcile()
        self.index = index_sections(self.REPORT)
        self.section = next(item for item in self.index['sections'] if item['title'] == '分析')
        self.payload = dict(base_sha256=self.index['report_sha256'], section_id=self.section['section_id'],
                            instruction='解释现有证据的局限，保留引用。', timeout_seconds=600)

    def prepare(self, key='preview-key', report_id=None, **changes):
        with patch.object(self.worker, 'artifacts', return_value={'items': self.items}), \
             patch.object(self.worker, 'artifact', side_effect=lambda tid, aid, max_bytes: (self.artifacts[aid], {})):
            return self.client.post('/scientific-reports/' + (report_id or self.task_id) + '/revision-previews',
                                    json=dict(self.payload, **changes), headers={'Idempotency-Key': key})

    def test_sections_are_exact_read_only_and_duplicate_titles_are_distinct(self):
        before = len(self.database.sql)
        response = self.client.get('/scientific-reports/' + self.task_id + '/sections')
        self.assertEqual(response.status_code, 200, response.text)
        index = response.json()
        self.assertEqual(index['report_sha256'], hashlib.sha256(self.REPORT.encode()).hexdigest())
        repeated = [row for row in index['sections'] if row['title'] == '分析']
        self.assertEqual(len(repeated), 2)
        self.assertNotEqual(repeated[0]['section_id'], repeated[1]['section_id'])
        self.assertNotIn('不是章节', [row['title'] for row in index['sections']])
        self.assertEqual(len(self.database.sql), before)
        self.assertEqual(self.client.get('/scientific-download/' + self.task_id).content, self.REPORT.encode())

    def test_listing_versions_does_not_create_a_copy(self):
        before = len(self.database.sql)
        response = self.client.get('/scientific-reports/' + self.task_id + '/versions')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['items'], [])
        self.assertEqual(response.json()['source_sha256'], self.index['report_sha256'])
        self.assertEqual(len(self.database.sql), before)

    def test_report_and_version_download_headers_reject_topic_control_characters(self):
        topics = ('Synthetic\r\nHeader: injected', 'Synthetic\t title\nsecond line',
                  'Synthetic\x00\x1f\x7f title', '科研报告', '', 'a' * 80)
        for number, topic in enumerate(topics):
            with self.subTest(number=number):
                self.database.connection.execute('UPDATE scientific_reports SET topic=? WHERE id=?',
                    (topic, self.task_id))
                self.database.connection.commit()
                # Version creation already rejects NUL; legacy SQL rows can
                # still contain it. Both paths must emit bounded safe headers.
                version = self.app.state.versions.ensure_base('trusted-user',
                    str(uuid.uuid4()), self.REPORT, topic.replace('\x00', ''))
                for path in ('/scientific-download/' + self.task_id,
                             '/scientific-report-versions/' + version['version_id'] + '/download'):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.content, self.REPORT.encode('utf-8'))
                    header = response.headers['content-disposition']
                    self.assertRegex(header, r'^attachment; filename=[A-Za-z0-9_-]{1,40}\.md$')
                    self.assertTrue(all(32 <= ord(char) < 127 for char in header))
                    self.assertNotIn('header', response.headers)
        self.database.connection.execute('UPDATE scientific_reports SET topic=? WHERE id=?', ('', self.task_id))
        self.database.connection.commit()
        self.assertEqual(self.client.get('/scientific-download/' + self.task_id).headers['content-disposition'],
                         'attachment; filename=review.md')

    def test_preparation_persists_exact_input_and_never_dispatches(self):
        response = self.prepare()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertFalse(data['dispatch_available'])
        self.assertEqual(data['intent']['status'], 'prepared')
        self.assertEqual(data['section']['sha256'], self.section['sha256'])
        start, end = self.section['start_byte'], self.section['end_byte']
        self.assertEqual(data['section']['text'].encode(), self.REPORT.encode()[start:end])
        self.assertEqual(data['snapshot']['material_policy'], 'recorded_evidence_only')
        self.assertEqual(len(data['snapshot']['snapshot_sha256']), 64)
        version_id, intent_id = data['parent']['version_id'], data['intent']['intent_id']
        self.assertEqual(self.client.get('/scientific-report-versions/' + version_id + '/download').content, self.REPORT.encode())
        self.assertEqual(self.client.get('/scientific-report-versions/' + version_id).json()['content'], self.REPORT)
        sections = self.client.get('/scientific-report-versions/' + version_id + '/sections').json()
        self.assertEqual(sections['sections'], self.index['sections'])
        preview = self.client.get('/scientific-revision-previews/' + intent_id).json()
        self.assertFalse(preview['dispatch_available'])
        self.assertEqual(preview['intent']['intent_id'], intent_id)
        self.assertEqual((self.worker.put_count, self.worker.ack_count), (1, 1))
        self.assertEqual(self.database.get_report(self.task_id)['content'], self.REPORT)

    def test_retry_and_new_instruction_reuse_fixed_snapshot_after_worker_expiry(self):
        first = self.prepare().json()
        with patch.object(self.worker, 'artifacts', side_effect=AssertionError('Must use fixed snapshot')), \
             patch.object(self.worker, 'artifact', side_effect=AssertionError('Must use fixed snapshot')):
            replay = self.client.post('/scientific-reports/' + self.task_id + '/revision-previews',
                                      json=self.payload, headers={'Idempotency-Key': 'preview-key'})
            another = self.client.post('/scientific-reports/' + self.task_id + '/revision-previews',
                                       json=dict(self.payload, instruction='补充已有证据的不确定性。'), headers={'Idempotency-Key': 'another-key'})
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(another.status_code, 200, another.text)
        self.assertEqual(replay.json()['intent']['intent_id'], first['intent']['intent_id'])
        self.assertEqual(another.json()['snapshot']['snapshot_id'], first['snapshot']['snapshot_id'])
        self.assertNotEqual(another.json()['intent']['intent_id'], first['intent']['intent_id'])
        self.assertEqual(len(self.database._query('SELECT snapshot_id FROM scientific_report_snapshots')), 1)

    def test_conflicting_key_and_stale_report_never_dispatch(self):
        first = self.prepare()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.prepare(instruction='Different instruction').status_code, 409)
        with patch.object(self.worker, 'artifacts') as listing:
            response = self.client.post('/scientific-reports/' + self.task_id + '/revision-previews',
                json=dict(self.payload, base_sha256='0' * 64), headers={'Idempotency-Key': 'stale'})
        self.assertEqual(response.status_code, 409)
        listing.assert_not_called()
        self.assertEqual(self.worker.put_count, 1)

    def test_snapshot_metadata_exposes_no_raw_records_or_paths(self):
        data = self.prepare().json()
        response = self.client.get('/scientific-report-snapshots/' + data['snapshot']['snapshot_id'])
        self.assertEqual(response.status_code, 200, response.text)
        public = response.json()
        self.assertNotIn('artifacts', public)
        self.assertNotIn('report', public)
        self.assertNotIn('items', public)
        self.assertNotIn('payload', public)
        self.assertEqual(public['material_policy'], 'recorded_evidence_only')

    def test_all_private_reads_enforce_owner(self):
        data = self.prepare().json()
        version_id = data['parent']['version_id']
        paths = ['/scientific-report-versions/' + version_id,
                 '/scientific-report-versions/' + version_id + '/sections',
                 '/scientific-report-versions/' + version_id + '/download',
                 '/scientific-report-snapshots/' + data['snapshot']['snapshot_id'],
                 '/scientific-revision-previews/' + data['intent']['intent_id'],
                 '/scientific-reports/' + self.task_id + '/sections',
                 '/scientific-reports/' + self.task_id + '/versions']
        for path in paths:
            for user, status in (('other', 404), ('', 401)):
                with self.subTest(path=path, user=user):
                    response = self.client.get(path, headers={'X-Forwarded-User': user})
                    self.assertEqual(response.status_code, status, response.text)

    def test_unauthorized_prepare_never_reads_worker_or_creates_private_data(self):
        with patch.object(self.worker, 'artifacts') as listing:
            for user, status in (('other', 404), ('', 401)):
                response = self.client.post('/scientific-reports/' + self.task_id + '/revision-previews',
                    json=self.payload, headers={'X-Forwarded-User': user, 'Idempotency-Key': 'private'})
                self.assertEqual(response.status_code, status, response.text)
        listing.assert_not_called()
        self.assertEqual(self.database._query('SELECT version_id FROM scientific_report_versions'), [])

    def test_alias_cannot_bypass_original_pi_ownership(self):
        alias = str(uuid.uuid4())
        self.database.connection.execute('INSERT INTO scientific_reports (id,task_id,topic,content) VALUES (?,?,?,?)',
                                         (alias, self.task_id, 'Private alias', self.REPORT))
        self.database.connection.commit()
        with patch.object(self.worker, 'artifacts') as listing:
            for suffix in ('sections', 'versions'):
                self.assertEqual(self.client.get('/scientific-reports/' + alias + '/' + suffix,
                                 headers={'X-Forwarded-User': 'other'}).status_code, 404)
            response = self.client.post('/scientific-reports/' + alias + '/revision-previews', json=self.payload,
                        headers={'X-Forwarded-User': 'other', 'Idempotency-Key': 'alias'})
            self.assertEqual(response.status_code, 404)
        listing.assert_not_called()

    def test_legacy_reading_is_shared_but_preparation_needs_structured_records(self):
        legacy_id = str(uuid.uuid4())
        self.database.connection.execute('INSERT INTO scientific_reports (id,task_id,topic,content) VALUES (?,?,?,?)',
                                         (legacy_id, legacy_id, 'Legacy fixture', self.REPORT))
        self.database.connection.commit()
        self.assertEqual(self.client.get('/scientific-reports/' + legacy_id + '/sections').status_code, 200)
        with patch.object(self.worker, 'artifacts') as listing:
            response = self.prepare(report_id=legacy_id)
        self.assertEqual(response.status_code, 409, response.text)
        listing.assert_not_called()

    def test_expired_source_evidence_does_not_create_snapshot_or_intent(self):
        with patch.object(self.worker, 'artifacts', side_effect=WorkerError(410, 'artifact_expired', 'private text')):
            response = self.client.post('/scientific-reports/' + self.task_id + '/revision-previews', json=self.payload,
                                        headers={'Idempotency-Key': 'expired'})
        self.assertEqual(response.status_code, 410)
        self.assertNotIn('private text', response.text)
        self.assertEqual(self.database._query('SELECT snapshot_id FROM scientific_report_snapshots'), [])
        self.assertEqual(self.database._query('SELECT intent_id FROM scientific_report_revision_intents'), [])
        self.assertEqual(self.database.get_report(self.task_id)['content'], self.REPORT)

    def test_mismatched_records_and_outage_are_safe(self):
        items = copy.deepcopy(self.items)
        next(item for item in items if item['artifact_id'] == 'report')['sha256'] = '0' * 64
        with patch.object(self.worker, 'artifacts', return_value={'items': items}), patch.object(self.worker, 'artifact') as artifact:
            response = self.client.post('/scientific-reports/' + self.task_id + '/revision-previews', json=self.payload,
                                        headers={'Idempotency-Key': 'mismatch'})
        self.assertEqual(response.status_code, 409)
        artifact.assert_not_called()
        with patch.object(self.worker, 'artifacts', side_effect=WorkerError(503, 'worker_unavailable', 'Bearer private')):
            response = self.client.post('/scientific-reports/' + self.task_id + '/revision-previews', json=self.payload,
                                        headers={'Idempotency-Key': 'outage'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Bearer private', response.text)

    def test_request_validation_rejects_paths_owner_and_invalid_limits(self):
        url = '/scientific-reports/' + self.task_id + '/revision-previews'
        for changes in (dict(instruction=' '), dict(instruction='a\x00b'), dict(timeout_seconds=True),
                        dict(instruction='字' * 2001), dict(timeout_seconds=59), dict(section_id='x' * 201),
                        dict(path='/private/file'), dict(owner='other')):
            with self.subTest(changes=changes):
                response = self.client.post(url, json=dict(self.payload, **changes), headers={'Idempotency-Key': 'invalid'})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.post(url, json=self.payload).status_code, 422)
        maximum = self.prepare(key='maximum-unicode', instruction='🔬' * 2000)
        self.assertEqual(maximum.status_code, 200, maximum.text)
        self.assertEqual(maximum.json()['intent']['instruction'], '🔬' * 2000)

    def test_private_records_survive_store_restart(self):
        first = self.prepare().json()
        from scientific_agent.pi_report_versions import ReportVersionStore
        reopened = ReportVersionStore(self.database)
        reopened.initialize()
        self.assertEqual(reopened.get_version('trusted-user', first['parent']['version_id'], include_content=True)['content'], self.REPORT)
        self.assertEqual(reopened.get_snapshot('trusted-user', first['snapshot']['snapshot_id'])['report'], self.REPORT)
        self.assertEqual(reopened.find_intent('trusted-user', 'preview-key')['intent_id'], first['intent']['intent_id'])

    def test_internally_registered_version_uses_root_history_and_exact_download(self):
        parent = self.prepare().json()['parent']
        content = self.REPORT.replace('另一段分析', '修订后的合成分析')
        source_id = self.client.post('/scientific-research', json={'topic': 'Synthetic published version'}).json()['id']
        self.worker.complete(source_id, content)
        self.projection.reconcile()
        candidate = self.app.state.versions.append_candidate('trusted-user', parent['version_id'],
            parent['report_sha256'], content, 'internal-fixture-candidate', source_report_id=source_id)
        before = len(self.database.sql)
        history = self.client.get('/scientific-reports/' + source_id + '/versions')
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual({item['version_id'] for item in history.json()['items']},
                         {parent['version_id'], candidate['version_id']})
        self.assertEqual(len(self.database.sql), before)
        path = '/scientific-report-versions/' + candidate['version_id']
        self.assertEqual(self.client.get(path + '/download').content, content.encode('utf-8'))
        self.assertEqual(self.client.get(path).json()['parent_version_id'], parent['version_id'])
        self.assertEqual(self.client.get(path + '/download', headers={'X-Forwarded-User': 'other'}).status_code, 404)
        self.assertEqual(self.client.get('/scientific-reports/' + source_id + '/versions',
                                       headers={'X-Forwarded-User': 'other'}).status_code, 404)
        self.assertEqual(self.database.get_report(self.task_id)['content'], self.REPORT)

    def test_preparation_reclaims_unpinned_expired_payload_quota(self):
        store = self.app.state.versions
        initial = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store.clock = lambda: initial
        store.owner_snapshot_count = 1
        older = store.ensure_base('trusted-user', str(uuid.uuid4()), self.REPORT)
        snapshot = store.save_snapshot('trusted-user', older['version_id'], self.items, self.artifacts, 'older-snapshot')
        store.clock = lambda: initial + timedelta(days=31)
        response = self.prepare()
        self.assertEqual(response.status_code, 200, response.text)
        tombstone = self.database._query("SELECT payload,purged_at FROM scientific_report_snapshots WHERE snapshot_id='" + snapshot['snapshot_id'] + "'")[0]
        self.assertIsNone(tombstone['payload'])
        self.assertIsNotNone(tombstone['purged_at'])
        self.assertEqual(len(self.database._query('SELECT snapshot_id FROM scientific_report_snapshots WHERE payload IS NOT NULL')), 1)
        self.assertEqual(self.database.get_report(self.task_id)['content'], self.REPORT)

    def test_periodic_cleanup_runs_when_reconciliation_fails(self):
        cleaned = threading.Event()
        store = self.app.state.versions
        actual_cleanup = store.cleanup_expired

        def cleanup():
            result = actual_cleanup()
            cleaned.set()
            return result

        with patch.object(self.projection, 'reconcile', side_effect=OSError('fixture outage')), \
             patch.object(store, 'cleanup_expired', side_effect=cleanup), \
             self.assertLogs('app.api.scientific_agent', level='ERROR'):
            with TestClient(create_app(self.database, self.worker, self.projection, versions=store)):
                self.assertTrue(cleaned.wait(2), 'Periodic cleanup was skipped after a task index failure')


if __name__ == '__main__':
    unittest.main()
