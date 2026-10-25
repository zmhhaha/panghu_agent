"""Actual API/client/worker/queue publication chain, with synthetic Pi output."""
import json
import unittest

from harness import FixtureRuntime, IntegrationHarness
from scientific_agent.pi_contracts import utc_now
from scientific_agent.pi_sections import select_section


class RevisionFixtureRuntime(FixtureRuntime):
    capabilities = dict(fixture_only=True, real_pi_verified=False,
                        revision_job_versions=['1'], revision_input_scope='recorded_evidence_only',
                        max_revision_input_bytes=16 * 1024 * 1024)

    def __init__(self):
        super().__init__()
        self.invalid_citation = False

    def execute(self, job, workspace, event_sink, cancel_signal):
        if job.get('task_kind') != 'revision':
            return super().execute(job, workspace, event_sink, cancel_signal)
        self.calls.append(dict(job))
        self.last_workspace = workspace
        citation = '999' if self.invalid_citation else '1'
        output = dict(schema_version='1', section_markdown=(
            '# Findings\r\n\r\n<!-- claim:revision_c2 -->\r\n'
            'The recorded synthetic abstract describes a limited finding [' + citation + '].\r\n\r\n'),
            claims=[dict(claim_id='revision_c2', marker='<!-- claim:revision_c2 -->',
                         evidence_ids=['e1'], assessment='supported',
                         explanation='Synthetic recorded abstract; no independently verified conclusion.')])
        (workspace / 'revision-output.json').write_bytes(json.dumps(output, ensure_ascii=False).encode('utf-8'))
        # Altered copies cannot replace the queue-owned controller input.
        (workspace / 'input-report.md').write_bytes(b'untrusted replacement of parent')
        now = utc_now()
        (workspace / 'provenance.json').write_text(json.dumps(dict(
            model='fixture-no-model-call', pi_version='fixture-no-process', node_version='fixture-no-process',
            image_digest='sha256:' + '0' * 64, skill_bundle_revision=job['skill_bundle_revision'],
            runtime_revision=job['runtime_revision'], retrieval_started_at=now,
            retrieval_finished_at=now)), encoding='utf-8')
        event_sink({'stage': 'write'})


class RevisionPublicationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = RevisionFixtureRuntime()
        self.h = IntegrationHarness(runtime=self.runtime)
        self.addCleanup(self.h.close)
        self.parent_id = self.h.submit().json()['id']
        self.assertEqual(self.h.execute_next()['state'], 'publishing')
        self.h.projection.reconcile()
        self.parent = self.h.public_http.get('/scientific-download/' + self.parent_id).content
        response = self.h.public_http.get('/scientific-reports/' + self.parent_id + '/sections')
        index = response.json()
        self.section = next(row for row in index['sections'] if row['title'] == 'Findings')
        self.preview = self.h.public_http.post('/scientific-reports/' + self.parent_id + '/revision-previews',
            json=dict(base_sha256=index['report_sha256'], section_id=self.section['section_id'],
                      instruction='Explain the limits of the recorded evidence.', timeout_seconds=600),
            headers={'Idempotency-Key': 'revision-integration'}).json()
        self.assertIn('intent', self.preview)
        self.start_url = '/scientific-revision-previews/' + self.preview['intent']['intent_id'] + '/start'

    def start(self):
        response = self.h.public_http.post(self.start_url, json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def test_real_http_revision_chain_preserves_parent_and_publishes_private_candidate(self):
        task_id = self.start()
        self.assertEqual(self.h.execute_next()['state'], 'publishing')
        self.assertFalse(self.runtime.last_workspace.exists())
        self.h.projection.reconcile()
        response = self.h.public_http.get('/scientific-research/' + task_id)
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()
        self.assertEqual(task['state'], 'succeeded')
        self.assertEqual(task['task_kind'], 'revision')
        self.assertEqual(task['root_report_id'], self.parent_id)
        candidate = self.h.public_http.get('/scientific-download/' + task_id).content
        version = self.h.public_http.get('/scientific-report-versions/' + task['candidate_version_id']).json()
        self.assertEqual(version['parent_version_id'], self.preview['parent']['version_id'])
        self.assertEqual(self.h.public_http.get('/scientific-report-versions/' + task['candidate_version_id'] + '/download').content,
                         candidate)
        self.assertEqual(self.h.public_http.get('/scientific-download/' + self.parent_id).content, self.parent)
        selected = select_section(self.parent.decode('utf-8'), self.section['section_id'], self.preview['intent']['base_sha256'])
        suffix_size = len(self.parent) - selected['end_byte']
        self.assertEqual(candidate[:selected['start_byte']], self.parent[:selected['start_byte']])
        self.assertEqual(candidate[-suffix_size:], self.parent[selected['end_byte']:])
        for name in ('papers', 'evidence', 'references'):
            self.assertEqual(self.h.worker.artifact(self.parent_id, name)[0], self.h.worker.artifact(task_id, name)[0])
        with self.h.store._tx() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM revision_inputs').fetchone()[0], 0)

    def test_lost_acceptance_and_ack_responses_recover_via_actual_http_without_duplicate_work(self):
        self.h.transport.lose_put_response_once = True
        response = self.h.public_http.post(self.start_url, json={})
        self.assertEqual(response.status_code, 503, response.text)
        task_id = response.json()['detail']['task_id']
        self.h.projection.reconcile()
        self.assertEqual(self.start(), task_id)
        self.assertEqual(self.h.transport.count('PUT'), 2)  # One parent and one revision.
        self.assertEqual(self.h.execute_next()['state'], 'publishing')
        self.h.transport.lose_ack_after_once = True
        self.h.projection.reconcile()
        self.h.projection.reconcile()
        self.assertEqual(self.h.public_http.get('/scientific-research/' + task_id).json()['state'], 'succeeded')
        versions = self.h.public_http.get('/scientific-reports/' + self.parent_id + '/versions').json()
        self.assertEqual(len(versions['items']), 2)
        self.assertEqual(len(self.runtime.calls), 2)

    def test_queued_revision_cancel_and_replay_do_not_execute_or_change_parent(self):
        task_id = self.start()
        cancel = self.h.public_http.post('/scientific-research/' + task_id + '/cancel')
        self.assertEqual(cancel.status_code, 200, cancel.text)
        self.assertEqual(cancel.json()['state'], 'cancelled')
        self.assertEqual(self.start(), task_id)
        self.h.projection.reconcile()
        self.assertIsNone(self.h.store.claim())
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertIsNone(self.h.db.get_report(task_id))
        self.assertEqual(self.h.public_http.get('/scientific-download/' + self.parent_id).content, self.parent)

    def test_unknown_citation_fails_before_candidate_registration_and_preserves_parent(self):
        task_id = self.start()
        self.runtime.invalid_citation = True
        self.assertEqual(self.h.execute_next()['state'], 'failed')
        self.h.projection.reconcile()
        response = self.h.public_http.get('/scientific-research/' + task_id)
        self.assertEqual(response.json()['state'], 'failed')
        self.assertIsNone(response.json()['candidate_version_id'])
        self.assertIsNone(self.h.db.get_report(task_id))
        self.assertEqual(self.h.public_http.get('/scientific-download/' + self.parent_id).content, self.parent)


if __name__ == '__main__':
    unittest.main()
