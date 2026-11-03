"""Current Agent review ACLs through in-process ASGI and real RS256 identities.

Only temporary, synthetic data is used. No worker/model runs; the signatures,
database, request authentication and storage readers are real. These are API
contract checks, not proxy, browser, rendering or PowerPoint acceptance.
"""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ppt_service.control.agent_connector import _confirmation
from ppt_service.server.app import create_app
from ppt_service.server.models import AgentOutbox, Job, JobEvent, Plan, Project, Version
from ppt_service.server.acceptance.store import AcceptanceEntry
from ppt_service.server.repository import Repository
from ppt_service.tests.server.agent_review_fixture import seed_agent_review
from .harness import E2ECase, ORIGIN


class CurrentMultiuserTests(E2ECase):
    def setUp(self):
        super().setUp()
        self.backend = patch.dict('os.environ', {'PPT_EXECUTION_BACKEND': 'agent-service'})
        self.backend.start()
        self.addCleanup(self.backend.stop)
        self.data = seed_agent_review(self.app, self.owner())
        # The historical fixture serializes naive dates before commit. Use the
        # actual persisted representation (PG timestamptz returns aware values)
        # to construct synthetic confirmations, as the real connector does.
        # Confirmed Plan rows are immutable and are never edited here.
        with self.sessions.begin() as session:
            confirmations = {stage: _confirmation(session.get(Plan, plan_id))
                            for stage, plan_id in self.data['plan_ids'].items()}
            outbox = session.get(AgentOutbox, self.data['jid'])
            outbox.request = dict(outbox.request, task=dict(outbox.request['task'], confirmations=confirmations))
        self.pid, self.vid = self.data['pid'], self.data['vid']
        self.url = f'/api/v1/projects/{self.pid}/versions/{self.vid}/acceptance'
        self.assertFalse(self.app.dependency_overrides)

    def independent_client(self, who='bob', *, issuer=None):
        app = self.app
        if issuer:
            app = create_app(replace(self.settings, oidc_issuer=issuer), self.engine)
            app.state.jwks = SimpleNamespace(get_signing_key_from_jwt=
                lambda _: SimpleNamespace(key=self.key.public_key()))
        client = TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 23456))
        self.addCleanup(client.close)
        auth = {'Authorization': 'Bearer ' + self.token(who, **({'iss': issuer} if issuer else {}))}
        response = client.get('/api/v1/me', headers=auth)
        self.assertEqual(response.status_code, 200, response.text)
        return client, auth | {'Origin': ORIGIN, 'X-CSRF-Token': response.json()['csrf_token']}

    def snapshot(self, client=None, headers=None):
        response = (client or self.client).get(self.url, headers=headers or self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def observations(snapshot, note='Synthetic owner feedback; no acceptance claimed'):
        return {'kind': 'visual_review', 'binding': snapshot['binding'],
            'expected_revision': snapshot['revision'], 'note': note,
            'pages': [{'number': page['number'], 'preview_sha256': page['preview_sha256'],
                       'verdict': 'pending', 'issues': []} for page in snapshot['pages']]}

    def revision(self, snapshot):
        return {'expected_revision': snapshot['source_revision'], 'base_version_id': self.vid,
            'feedback': 'Synthetic feedback: clarify page two from approved source.', 'pages': [2],
            'review_binding': snapshot['binding'], 'review_revision': snapshot['revision']}

    def test_current_review_reads_writes_and_replay_are_private_to_issuer_and_subject(self):
        snapshot = self.snapshot()
        record = self.observations(snapshot)
        revise = self.revision(snapshot)
        publication = {'kind': 'publish', 'binding': snapshot['binding'],
                       'expected_revision': snapshot['revision'], 'confirm': True}
        get_routes = [f'/api/v1/projects/{self.pid}', f'/api/v1/projects/{self.pid}/versions',
            f'/api/v1/jobs/{self.data["jid"]}', f'/api/v1/jobs/{self.data["jid"]}/events',
            f'/api/v1/versions/{self.vid}/download', f'/api/v1/versions/{self.vid}/slides/1/preview', self.url]
        for route in get_routes:
            response = self.client.get(route, headers=self.headers())
            self.assertEqual(response.status_code, 200, route + ': ' + response.text[:200])
        # Separate authenticated clients have separate cookies, no auth overrides.
        for who, issuer in [('bob', None), ('alice', 'https://other-issuer.ppt-e2e.test')]:
            client, headers = self.independent_client(who, issuer=issuer)
            for route in get_routes + [f'/api/v1/jobs/{self.data["jid"]}/diagnostics',
                    self.url + '/rendered-slides/1/preview?receipt_sha256=' + 'a' * 64]:
                with self.subTest(who=who, issuer=issuer, route=route):
                    self.assertEqual(client.get(route, headers=headers).status_code, 404)
            for route, body in [(self.url + '/records', record), (self.url + '/publication', publication),
                    (f'/api/v1/projects/{self.pid}/review-revisions', revise),
                    (f'/api/v1/jobs/{self.data["jid"]}/cancel', None)]:
                denied = client.post(route, json=body, headers=headers | {'Idempotency-Key': 'foreign-intent'})
                self.assertEqual(denied.status_code, 404, denied.text)
        self.assertEqual(self.snapshot()['revision'], 0)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AcceptanceEntry)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)

    def test_two_documents_preserve_csrf_and_conflict_then_refresh_observations(self):
        # These represent two documents sharing one browser cookie jar; this is
        # ASGI request sequencing, not an executed browser/two-tab test.
        saved_csrf = self.csrf
        first, second = self.snapshot(), self.snapshot()
        for _ in range(3):
            response = self.client.get('/api/v1/me', headers=self.headers())
            self.assertEqual(response.json()['csrf_token'], saved_csrf)
        route = self.url + '/records'
        headers = self.headers() | {'Idempotency-Key': 'document-one'}
        written = self.client.post(route, json=self.observations(first), headers=headers)
        self.assertEqual(written.status_code, 200, written.text)
        self.assertEqual(written.json()['revision'], 1)
        replay = self.client.post(route, json=self.observations(first), headers=headers)
        self.assertEqual(replay.json(), written.json())
        reused = self.client.post(route, json=self.observations(first, 'Different content, reused key'), headers=headers)
        self.assertEqual(reused.status_code, 409, reused.text)
        self.assertEqual(reused.json()['detail']['code'], 'idempotency_key_reused')
        bob, bob_headers = self.independent_client()
        self.assertEqual(bob.post(route, json=self.observations(first),
            headers=bob_headers | {'Idempotency-Key': 'document-one'}).status_code, 404)
        stale = self.client.post(route, json=self.observations(second, 'Second document'),
            headers=self.headers() | {'Idempotency-Key': 'document-two-stale'})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()['detail']['code'], 'acceptance_revision_changed')
        refreshed = self.snapshot()
        recovered = self.client.post(route, json=self.observations(refreshed, 'Refreshed document'),
            headers=self.headers() | {'Idempotency-Key': 'document-two-refreshed'})
        self.assertEqual(recovered.status_code, 200, recovered.text)
        final = self.snapshot()
        self.assertEqual(final['revision'], 2)
        self.assertEqual(len(final['records']), 2)
        self.assertFalse(final['publication_authorized'])
        self.assertNotEqual(final['acceptance_status'], 'passed')

    def test_pending_powerpoint_record_and_revocation_cannot_be_saved_by_other_owner(self):
        snapshot = self.snapshot()
        body = {'kind': 'powerpoint_review', 'binding': snapshot['binding'], 'expected_revision': 0,
            'application_version': '', 'platform': '', 'note': 'Not executed; synthetic contract test only',
            'checks': {'opened': 'pending', 'content_edited': 'pending', 'native_chart_edited': 'pending',
                       'saved': 'pending', 'reopened': 'pending'}}
        route = self.url + '/records'
        bob, headers = self.independent_client()
        self.assertEqual(bob.post(route, json=body,
            headers=headers | {'Idempotency-Key': 'pending-powerpoint'}).status_code, 404)
        saved = self.client.post(route, json=body,
            headers=self.headers() | {'Idempotency-Key': 'pending-powerpoint'})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['powerpoint_status'], 'pending')
        record = saved.json()['records'][0]
        revoke = {'kind': 'revoke', 'binding': snapshot['binding'], 'expected_revision': 1,
                  'record_id': record['id'], 'reason': 'Remove synthetic pending note'}
        self.assertEqual(bob.post(route, json=revoke,
            headers=headers | {'Idempotency-Key': 'revoke-pending'}).status_code, 404)
        response = self.client.post(route, json=revoke,
            headers=self.headers() | {'Idempotency-Key': 'revoke-pending'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['revision'], 2)
        self.assertTrue(response.json()['records'][0]['revoked'])
        self.assertFalse(response.json()['publication_authorized'])

    def test_stale_feedback_cannot_launch_revision_and_duplicate_cancel_stays_single(self):
        snapshot = self.snapshot()
        changed = self.client.post(self.url + '/records', json=self.observations(snapshot),
            headers=self.headers() | {'Idempotency-Key': 'observations'})
        self.assertEqual(changed.status_code, 200, changed.text)
        route = f'/api/v1/projects/{self.pid}/review-revisions'
        stale = self.client.post(route, json=self.revision(snapshot),
            headers=self.headers() | {'Idempotency-Key': 'stale-revision'})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()['detail']['code'], 'review_records_changed')
        body = self.revision(changed.json())
        headers = self.headers() | {'Idempotency-Key': 'current-revision'}
        first = self.client.post(route, json=body, headers=headers)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(self.client.post(route, json=body, headers=headers).json(), first.json())
        bob, bob_headers = self.independent_client()
        self.assertEqual(bob.post(route, json=body,
            headers=bob_headers | {'Idempotency-Key': 'current-revision'}).status_code, 404)
        jid = first.json()['id']
        cancel = f'/api/v1/jobs/{jid}/cancel'
        cancelled = self.client.post(cancel, headers=self.headers() | {'Idempotency-Key': 'cancel-current'})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        with self.sessions() as session:
            event_count = session.scalar(select(func.count()).select_from(JobEvent).where(JobEvent.job_id == jid))
        self.assertEqual(self.client.post(cancel,
            headers=self.headers() | {'Idempotency-Key': 'cancel-current'}).json(), cancelled.json())
        self.assertEqual(bob.post(cancel,
            headers=bob_headers | {'Idempotency-Key': 'cancel-current'}).status_code, 404)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(JobEvent).where(JobEvent.job_id == jid)), event_count)
            self.assertEqual(session.get(Version, self.vid).status, 'review')
            self.assertIsNone(session.get(Project, self.pid).latest_version_id)
        self.assertEqual(self.worker.creates, 0)

    def test_pending_human_checks_cannot_publish_and_delete_revokes_review_records(self):
        snapshot = self.snapshot()
        body = {'kind': 'publish', 'binding': snapshot['binding'], 'expected_revision': 0, 'confirm': True}
        denied = self.client.post(self.url + '/publication', json=body,
            headers=self.headers() | {'Idempotency-Key': 'must-stay-draft'})
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertFalse(self.snapshot()['publication_authorized'])
        record = self.observations(snapshot)
        headers = self.headers() | {'Idempotency-Key': 'deleted-record'}
        self.assertEqual(self.client.post(self.url + '/records', json=record, headers=headers).status_code, 200)
        deleted = self.client.delete(f'/api/v1/projects/{self.pid}', headers=self.headers())
        self.assertEqual(deleted.status_code, 202, deleted.text)
        for route in [self.url, f'/api/v1/versions/{self.vid}/download',
                      f'/api/v1/versions/{self.vid}/slides/1/preview']:
            self.assertEqual(self.client.get(route, headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.post(self.url + '/records', json=record, headers=headers).status_code, 404)

    def test_review_download_and_preview_reject_tampered_bytes(self):
        paths = [(f'/api/v1/versions/{self.vid}/slides/1/preview', '1.png'),
                 (f'/api/v1/versions/{self.vid}/download', 'presentation.pptx')]
        for route, name in paths:
            path = self.data_root / self.data['artifact_key'] / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'SYNTHETIC ALTERATION')
                self.assertNotEqual(self.client.get(route, headers=self.headers()).status_code, 200)
            finally:
                path.write_bytes(original)
        self.assertEqual(self.snapshot()['revision'], 0)

    def test_quality_diagnostics_download_redacts_raw_report_and_rechecks_owner(self):
        pid = self.create_project()['id']
        jid = self.queue_plan(pid)['id']
        with self.sessions.begin() as session:
            repo = Repository(session)
            job = repo.claim_next()
            self.assertEqual(job.id, jid)
            repo.attach_execution(jid, job.attempt, job.fencing_token, job_uid='synthetic-diagnostics')
            repo.prove_stopped(jid, job.attempt, job.fencing_token, job_uid=job.job_uid, all_writers_stopped=True)
            repo.finish_failed(jid, job.attempt, job.fencing_token, error_code='quality_gate_failed')
            ref = f'ppt:{jid}:{job.attempt}:{job.fencing_token}'
            session.add(AgentOutbox(job_id=jid, attempt=job.attempt, fencing_token=job.fencing_token,
                business_ref=ref, idempotency_key='synthetic:' + jid, state='mapped',
                request={'business_ref': ref, 'task': {'business_job_id': jid, 'project_id': pid,
                    'input_snapshot_sha256': 'a' * 64}},
                result={'status': 'failed', 'error_code': 'quality_gate_failed', 'quality_diagnostics': {
                    'schema_version': 1, 'error_code': 'final_pptx_text_layout_failed',
                    'publication_authorized': False, 'project_id': pid, 'job_id': jid,
                    'attempt': job.attempt, 'fencing_token': job.fencing_token,
                    'input_snapshot_sha256': 'a' * 64, 'source_manifest_sha256': 'b' * 64,
                    'private_log': 'synthetic-private-marker', 'evidence_path': '/private/secret'}}))
        route = f'/api/v1/jobs/{jid}/diagnostics'
        response = self.client.get(route, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertIn('attachment;', response.headers['content-disposition'])
        for secret in ('synthetic-private-marker', '/private/secret', 'fencing_token'):
            self.assertNotIn(secret, response.text)
        for who, issuer in [('bob', None), ('alice', 'https://other-issuer.ppt-e2e.test')]:
            client, headers = self.independent_client(who, issuer=issuer)
            self.assertEqual(client.get(route, headers=headers).status_code, 404)
