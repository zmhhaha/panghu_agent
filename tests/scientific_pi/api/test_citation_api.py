"""Citation routes against local storage and synthetic worker records only."""
import copy
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.api.scientific_agent import create_app
from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_projection import PiProjection
from test_api import MemoryDB, FakeWorker


def records_for(report):
    acquired = {'available': True, 'obtained_at': '2026-10-25T00:00:00Z'}
    absent = {'available': False, 'obtained_at': None}
    paper = dict(paper_id='p1', title='Synthetic citation fixture', authors=['Fixture Author'], year=2025,
                 doi='10.1234/fixture', pmid=None, url='https://example.org/fixture', sources=['fixture'],
                 retrieved_at='2026-10-25T00:00:00Z', abstract='Synthetic abstract.', full_text_path=None,
                 availability=dict(metadata=acquired, abstract=acquired, full_text=absent),
                 verification=dict(status='unknown', checked_at=None, source=None, error=None))
    rows = dict(papers=[paper],
                evidence=[dict(evidence_id='e1', paper_id='p1', finding='Synthetic finding',
                               method='Fixture', limitations='Abstract only', access_level='abstract', locator='abstract')],
                claims=[dict(claim_id='c1', marker='<!-- claim:c1 -->', evidence_ids=['e1'],
                             assessment='supported', explanation='Synthetic abstract record only')],
                references=[dict(n=1, paper_id='p1')])
    artifacts = {name: (json.dumps(value) if name == 'references' else
                       ''.join(json.dumps(row) + '\n' for row in value)).encode('utf-8')
                 for name, value in rows.items()}
    all_content = dict(artifacts, report=report.encode('utf-8'))
    items = [dict(artifact_id=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                  media_type='text/markdown' if name == 'report' else
                  'application/json' if name == 'references' else 'application/x-ndjson')
             for name, raw in all_content.items()]
    return items, artifacts


class CitationAPITests(unittest.TestCase):
    REPORT = '# Synthetic report\r\n\r\n<!-- claim:c1 -->\r\nA fixture finding [1].\r\n'

    def setUp(self):
        self.database, self.worker = MemoryDB(), FakeWorker()
        projection = PiProjection(self.database, self.worker, 'skills-pinned', 'runtime-pinned', lambda *args: None)
        self.projection = projection
        self.client = TestClient(create_app(self.database, self.worker, projection, start_reconciler=False))
        self.client.headers['X-Forwarded-User'] = 'trusted-user'
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(self.database.connection.close)
        self.items, self.artifacts = records_for(self.REPORT)

    def publish(self):
        response = self.client.post('/scientific-research', json={'topic': 'Synthetic trace fixture'})
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()['id']
        self.worker.complete(task_id, self.REPORT)
        self.projection.reconcile()
        return task_id

    def read(self, task_id, **kwargs):
        return self.client.get('/scientific-research/' + task_id + '/citations', **kwargs)

    def test_resolves_owned_published_records_and_keeps_exact_download(self):
        task_id = self.publish()
        with patch.object(self.worker, 'artifacts', return_value={'items': self.items}) as listing, \
             patch.object(self.worker, 'artifact', side_effect=lambda tid, aid, max_bytes: (self.artifacts[aid], {})) as artifact:
            response = self.read(task_id)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['availability'], 'available')
        self.assertEqual(data['report_sha256'], hashlib.sha256(self.REPORT.encode('utf-8')).hexdigest())
        self.assertEqual(data['citations'][0]['paper']['title'], 'Synthetic citation fixture')
        self.assertNotIn('full_text_path', data['citations'][0]['paper'])
        self.assertNotIn('abstract', data['citations'][0]['paper'])
        self.assertEqual(data['citations'][0]['evidence'][0]['access_level'], 'abstract')
        self.assertEqual(data['citations'][0]['claims'][0]['claim_id'], 'c1')
        listing.assert_called_once_with(task_id)
        self.assertEqual({call.args[1] for call in artifact.call_args_list}, set(self.artifacts))
        self.assertEqual(artifact.call_count, 4)
        for call in artifact.call_args_list:
            self.assertEqual(call.kwargs['max_bytes'], len(self.artifacts[call.args[1]]))
        self.assertEqual(self.client.get('/scientific-download/' + task_id).content, self.REPORT.encode('utf-8'))
        self.assertEqual((self.worker.put_count, self.worker.ack_count), (1, 1))

    def test_identity_and_ownership_checked_before_worker_access(self):
        task_id = self.publish()
        with patch.object(self.worker, 'artifacts') as listing, patch.object(self.worker, 'artifact') as artifact:
            for user, status in (('', 401), ('other-user', 404), ('x' * 201, 422)):
                with self.subTest(user=user):
                    response = self.read(task_id, headers={'X-Forwarded-User': user})
                    self.assertEqual(response.status_code, status, response.text)
            listing.assert_not_called()
            artifact.assert_not_called()

    def test_legacy_has_explicit_unavailability_without_worker_access(self):
        task_id = str(uuid.uuid4())
        self.database.connection.execute('INSERT INTO scientific_reports (id,task_id,topic,content) VALUES (?,?,?,?)',
                                         (task_id, task_id, 'Legacy fixture', self.REPORT))
        self.database.connection.commit()
        with patch.object(self.worker, 'artifacts') as listing:
            response = self.read(task_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['availability'], 'legacy')
        self.assertEqual(response.json()['citations'], [])
        listing.assert_not_called()
        self.assertEqual(self.read(task_id, headers={'X-Forwarded-User': ''}).status_code, 401)

    def test_alias_cannot_bypass_owned_report_guard(self):
        task_id, alias = self.publish(), str(uuid.uuid4())
        self.database.connection.execute('INSERT INTO scientific_reports (id,task_id,topic,content) VALUES (?,?,?,?)',
                                         (alias, task_id, 'Alias fixture', self.REPORT))
        self.database.connection.commit()
        with patch.object(self.worker, 'artifacts') as listing:
            self.assertEqual(self.read(alias, headers={'X-Forwarded-User': 'other-user'}).status_code, 404)
        listing.assert_not_called()

    def test_running_report_is_not_ready_and_never_reads_artifacts(self):
        task_id = self.client.post('/scientific-research', json={'topic': 'Queued fixture'}).json()['id']
        with patch.object(self.worker, 'artifacts') as listing:
            response = self.read(task_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'result_not_ready')
        listing.assert_not_called()

    def test_manifest_mismatch_prevents_all_record_downloads(self):
        task_id = self.publish()
        items = copy.deepcopy(self.items)
        next(item for item in items if item['artifact_id'] == 'report')['sha256'] = 'a' * 64
        with patch.object(self.worker, 'artifacts', return_value={'items': items}), \
             patch.object(self.worker, 'artifact') as artifact:
            data = self.read(task_id).json()
        self.assertEqual((data['availability'], data['reason']), ('unavailable', 'report_mismatch'))
        self.assertEqual(data['citations'], [])
        artifact.assert_not_called()

    def test_oversized_manifest_prevents_record_downloads(self):
        task_id = self.publish()
        items = copy.deepcopy(self.items)
        next(item for item in items if item['artifact_id'] == 'papers')['size'] = 9 * 1024 * 1024
        with patch.object(self.worker, 'artifacts', return_value={'items': items}), \
             patch.object(self.worker, 'artifact') as artifact:
            data = self.read(task_id).json()
        self.assertEqual(data['availability'], 'unavailable')
        self.assertEqual(data['reason'], 'trace_too_large')
        artifact.assert_not_called()

    def test_corrupted_bytes_never_create_trace(self):
        task_id = self.publish()
        with patch.object(self.worker, 'artifacts', return_value={'items': self.items}), \
             patch.object(self.worker, 'artifact', return_value=(b'corrupted-record', {})) as artifact:
            data = self.read(task_id).json()
        self.assertEqual((data['availability'], data['reason']), ('unavailable', 'artifact_mismatch'))
        self.assertEqual(data['citations'], [])
        self.assertEqual(artifact.call_count, 1)

    def test_expiry_during_listing_or_download_preserves_report(self):
        task_id = self.publish()
        for during_download in (False, True):
            with self.subTest(during_download=during_download):
                error = WorkerError(410, 'artifact_expired', 'private upstream message')
                with patch.object(self.worker, 'artifacts', **({'return_value': {'items': self.items}} if during_download else {'side_effect': error})), \
                     patch.object(self.worker, 'artifact', side_effect=error):
                    data = self.read(task_id).json()
                self.assertEqual((data['availability'], data['reason']), ('expired', 'artifact_expired'))
                self.assertNotIn('private upstream', json.dumps(data))
                self.assertEqual(self.client.get('/scientific-download/' + task_id).content, self.REPORT.encode('utf-8'))

    def test_missing_worker_records_and_invalid_manifest_degrade(self):
        task_id = self.publish()
        with patch.object(self.worker, 'artifacts', side_effect=WorkerError(404, 'task_not_found', 'private message')):
            data = self.read(task_id).json()
        self.assertEqual((data['availability'], data['reason']), ('unavailable', 'structured_records_unavailable'))
        with patch.object(self.worker, 'artifacts', return_value=['invalid']), patch.object(self.worker, 'artifact') as artifact:
            data = self.read(task_id).json()
        self.assertEqual(data['availability'], 'unavailable')
        artifact.assert_not_called()

    def test_worker_outage_is_retryable_and_does_not_expose_upstream_text(self):
        task_id = self.publish()
        with patch.object(self.worker, 'artifacts', side_effect=WorkerError(503, 'worker_unavailable', 'Bearer private-control-token')):
            response = self.read(task_id)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()['detail']['retryable'])
        self.assertNotIn('private-control-token', response.text)

    def test_storage_outage_unknown_and_invalid_ids(self):
        task_id = self.publish()
        with patch.object(self.database, 'get_report_by_task', side_effect=OSError('private storage path')), \
             patch.object(self.worker, 'artifacts') as listing:
            response = self.read(task_id)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private storage path', response.text)
        listing.assert_not_called()
        self.assertEqual(self.read(str(uuid.uuid4())).status_code, 404)
        self.assertEqual(self.read('invalid-id').status_code, 422)


if __name__ == '__main__':
    unittest.main()
