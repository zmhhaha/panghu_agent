"""Real authenticated HTTP ACL/event regressions using isolated synthetic data.

Historical versions below are inserted only to exercise existing read ACLs. They
are deliberately not publication, rendering or PowerPoint acceptance evidence.
"""
from __future__ import annotations

import base64
from dataclasses import replace
import io
import json
import time
import uuid
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from ppt_service.server.app import create_app
from ppt_service.server.models import (
    Asset, Checkpoint, ExecutionSlot, Job, Project, UsageRecord, Version,
)
from ppt_service.server.repository import Repository

from .harness import E2ECase


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII='
)


def seed_synthetic_historical_version(case, pid, *, parent_version_id=None):
    """Persist an explicitly synthetic legacy fixture; never invoke a publisher."""
    version_id = str(uuid.uuid4())
    root = f'projects/{pid}/versions/{version_id}/synthetic-history'
    checkpoint_key = f'projects/{pid}/checkpoints/{uuid.uuid4()}'
    marker = {'fixture': 'synthetic-history-for-acl-and-restore-only',
              'publication_verified': False, 'powerpoint_acceptance': 'pending'}
    checkpoint = case.data_root / checkpoint_key
    checkpoint.mkdir(parents=True)
    (checkpoint / 'fixture.json').write_text(json.dumps(marker), encoding='utf-8')
    folder = case.data_root / root
    folder.mkdir(parents=True)
    deck = io.BytesIO()
    with zipfile.ZipFile(deck, 'w') as archive:
        archive.writestr('SYNTHETIC-NOT-A-VALIDATED-DECK.txt', json.dumps(marker))
    (folder / 'deck.pptx').write_bytes(deck.getvalue())
    (folder / '1.png').write_bytes(PNG)
    with case.sessions.begin() as session:
        project = session.get(Project, pid)
        job = Job(project_id=pid, issuer=project.issuer, subject=project.subject,
                  kind='generate', stage='generate', status='succeeded',
                  stopped=True, input_snapshot=marker)
        session.add(job)
        session.flush()
        cp = Checkpoint(project_id=pid, issuer=project.issuer, subject=project.subject,
                        storage_key=checkpoint_key, manifest=marker)
        session.add(cp)
        session.flush()
        version = Version(id=version_id, project_id=pid, job_id=job.id,
                          issuer=project.issuer, subject=project.subject,
                          parent_version_id=parent_version_id, checkpoint_id=cp.id,
                          artifact_root=root, status='published',
                          manifest=marker | {'pptx_key': root + '/deck.pptx',
                                             'slides': [{'title': 'Synthetic fixture',
                                                         'preview_key': root + '/1.png'}]})
        session.add(version)
        project.latest_version_id = version_id
    return version_id, folder


def parse_sse(response):
    events = []
    for block in response.text.split('\n\n'):
        if not block.strip() or block.startswith(':'):
            continue
        fields = dict(line.split(': ', 1) for line in block.splitlines())
        events.append((int(fields['id']), fields['event'], json.loads(fields['data'])))
    return events


class SecurityHTTPTests(E2ECase):
    def test_real_jwt_and_forged_identity_headers(self):
        self.assertFalse(self.app.dependency_overrides)
        alice = self.create_project()
        bob = self.create_project(who='bob', title='Bob private project')
        for who, expected in [('alice', alice['id']), ('bob', bob['id'])]:
            response = self.client.get('/api/v1/projects', headers=self.headers(who))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([p['id'] for p in response.json()], [expected])
        forged = {'X-Auth-Request-User': 'alice', 'X-Auth-Request-Email': 'alice@example.test',
                  'X-Forwarded-User': 'alice', 'X-Forwarded-For': '127.0.0.1'}
        response = self.client.get('/api/v1/me', headers=forged)
        self.assertEqual(response.status_code, 401)
        response = self.client.get('/api/v1/me', headers=self.headers('bob') | forged)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sub'], 'bob')
        for claims in ({'iss': 'https://untrusted.example.test'}, {'aud': 'some-other-client'},
                       {'exp': int(time.time()) - 60}, {'email': 'not-an-email'}):
            with self.subTest(claims=claims):
                response = self.client.get('/api/v1/projects', headers={
                    'Authorization': 'Bearer ' + self.token('alice', **claims)})
                self.assertEqual(response.status_code, 401)

    def test_new_authenticated_account_needs_no_invitation_and_keeps_ownership(self):
        original = self.create_project()
        headers = self.headers('new-feedback-user')
        me = self.client.get('/api/v1/me', headers=headers)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()['sub'], 'new-feedback-user')
        self.assertEqual(self.client.get('/api/v1/projects', headers=headers).json(), [])
        self.assertEqual(self.client.get('/api/v1/projects/' + original['id'], headers=headers).status_code, 404)
        response = self.client.post('/api/v1/projects', json={'title': 'New participant feedback'},
            headers=headers | {'X-CSRF-Token': me.json()['csrf_token']})
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()['id']
        self.assertEqual(self.client.get('/api/v1/projects/' + created, headers=self.headers()).status_code, 404)

    def test_valid_token_from_untrusted_peer_is_rejected(self):
        with TestClient(self.app, base_url='https://ppt-e2e.test',
                        client=('203.0.113.24', 12345)) as client:
            response = client.get('/api/v1/me', headers=self.headers())
        self.assertEqual(response.status_code, 401)

    def test_mutations_require_both_origin_and_csrf(self):
        valid = self.headers()
        variants = [
            ({k: v for k, v in valid.items() if k.lower() != 'origin'}, 'origin_rejected'),
            (valid | {'Origin': 'https://attacker.example.test'}, 'origin_rejected'),
            ({k: v for k, v in valid.items() if k.lower() != 'x-csrf-token'}, 'csrf_rejected'),
            (valid | {'X-CSRF-Token': 'not-the-cookie'}, 'csrf_rejected'),
            (valid | {'Cookie': ''}, 'csrf_rejected'),
        ]
        for headers, code in variants:
            with self.subTest(code=code, headers=list(headers)):
                response = self.client.post('/api/v1/projects', json={'title': 'rejected'}, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()['detail']['code'], code)
        self.assertEqual(self.client.get('/api/v1/projects', headers=valid).json(), [])
        self.assertEqual(self.create_project()['title'], 'SYNTHETIC P5 project')

    def test_other_owner_cannot_read_or_mutate_project_resources(self):
        project = self.create_project()
        pid = project['id']
        asset = self.upload(pid)
        version, _ = seed_synthetic_historical_version(self, pid)
        job = self.queue_plan(pid)
        # A terminal job makes the authorized SSE request finite.
        cancelled = self.client.post('/api/v1/jobs/' + job['id'] + '/cancel',
                                     headers=self.headers() | {'Idempotency-Key': 'stop-acl'})
        self.assertEqual(cancelled.status_code, 200)
        routes = [f'/projects/{pid}', f'/projects/{pid}/versions', f'/jobs/{job["id"]}',
                  f'/jobs/{job["id"]}/events', f'/versions/{version}/download',
                  f'/versions/{version}/slides/1/preview']
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get('/api/v1' + route, headers=self.headers()).status_code, 200)
                response = self.client.get('/api/v1' + route, headers=self.headers('bob'))
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()['detail']['code'], 'not_found')
        self.assertEqual(self.client.get('/api/v1/projects', headers=self.headers('bob')).json(), [])
        revision = self.project(pid)['revision']
        mutations = [
            ('delete', f'/projects/{pid}', None),
            ('delete', f'/projects/{pid}/assets/{asset["id"]}?expected_revision={revision}', None),
            ('post', f'/jobs/{job["id"]}/cancel', None),
            ('post', f'/projects/{pid}/plans', {'expected_revision': revision}),
            ('post', f'/projects/{pid}/generations', {'expected_revision': revision, 'plan_revision': 1}),
            ('post', f'/projects/{pid}/revisions', {'expected_revision': revision,
                'base_version_id': version, 'feedback': 'unauthorized edit'}),
        ]
        for index, (method, route, body) in enumerate(mutations):
            with self.subTest(method=method, route=route):
                response = self.client.request(method, '/api/v1' + route, json=body,
                    headers=self.headers('bob') | {'Idempotency-Key': f'forbidden-{index}'})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.project(pid)['revision'], revision)

    def test_identical_subject_from_another_valid_issuer_has_no_access(self):
        project = self.create_project()
        version, _ = seed_synthetic_historical_version(self, project['id'])
        job = self.queue_plan(project['id'])
        self.assertEqual(self.client.post(f'/api/v1/jobs/{job["id"]}/cancel',
            headers=self.headers() | {'Idempotency-Key': 'issuer-fixture-stop'}).status_code, 200)
        issuer = 'https://second-identity.ppt-e2e.test'
        app = create_app(replace(self.settings, oidc_issuer=issuer), self.engine)
        app.state.jwks = self.app.state.jwks
        with TestClient(app, base_url='https://ppt-e2e.test',
                        client=('127.0.0.1', 12345)) as client:
            auth = {'Authorization': 'Bearer ' + self.token('alice', iss=issuer)}
            me = client.get('/api/v1/me', headers=auth)
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()['issuer'], issuer)
            self.assertEqual(client.get('/api/v1/projects', headers=auth).json(), [])
            for route in [f'/projects/{project["id"]}', f'/jobs/{job["id"]}',
                          f'/jobs/{job["id"]}/events', f'/versions/{version}/download',
                          f'/versions/{version}/slides/1/preview']:
                self.assertEqual(client.get('/api/v1' + route, headers=auth).status_code, 404)

    def test_deletion_revokes_reads_and_retains_bytes_until_writer_stop(self):
        project = self.create_project()
        pid = project['id']
        asset = self.upload(pid, content=b'# Synthetic source must remain until writers stop')
        version, folder = seed_synthetic_historical_version(self, pid)
        job = self.queue_plan(pid)
        with self.sessions.begin() as session:
            repo = Repository(session)
            claimed = repo.claim_next()
            self.assertEqual(claimed.id, job['id'])
            token = claimed.fencing_token
            repo.attach_execution(claimed.id, claimed.attempt, token, job_uid='p5-synthetic-writer')
            upload_path = self.data_root / session.get(Asset, asset['id']).storage_key
            version_row = session.get(Version, version)
            checkpoint_key = session.get(Checkpoint, version_row.checkpoint_id).storage_key
            checkpoint_path = self.data_root / checkpoint_key / 'fixture.json'
            before = {path: path.read_bytes() for path in [upload_path, checkpoint_path,
                                                         folder / 'deck.pptx', folder / '1.png']}
        deleted = self.client.delete(f'/api/v1/projects/{pid}', headers=self.headers())
        self.assertEqual(deleted.status_code, 202)
        self.assertFalse(deleted.json()['cleanup_ready'])
        for route in [f'/projects/{pid}', f'/jobs/{job["id"]}', f'/jobs/{job["id"]}/events',
                      f'/versions/{version}/download', f'/versions/{version}/slides/1/preview']:
            self.assertEqual(self.client.get('/api/v1' + route, headers=self.headers()).status_code, 404)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        with self.sessions.begin() as session:
            repo = Repository(session)
            with self.assertRaises(HTTPException) as error:
                repo.prove_stopped(job['id'], 1, token, job_uid='p5-synthetic-writer', all_writers_stopped=False)
            self.assertEqual(error.exception.detail['code'], 'stop_proof_required')
            self.assertFalse(session.get(Project, pid).cleanup_ready)
            self.assertEqual(session.get(ExecutionSlot, 1).job_id, job['id'])
            repo.prove_stopped(job['id'], 1, token, job_uid='p5-synthetic-writer', all_writers_stopped=True)
            self.assertTrue(session.get(Project, pid).cleanup_ready)
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
        # There is no automatic purge in this fixture or this assertion.
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_deleted_project_cannot_replay_cached_write_response(self):
        project = self.create_project()
        pid = project['id']
        body = {'expected_revision': project['revision'], 'phase': 'plan_initial'}
        headers = self.headers() | {'Idempotency-Key': 'cached-before-deletion'}
        queued = self.client.post(f'/api/v1/projects/{pid}/plans', json=body, headers=headers)
        self.assertEqual(queued.status_code, 202)
        self.assertEqual(self.client.delete(f'/api/v1/projects/{pid}', headers=self.headers()).status_code, 202)
        replay = self.client.post(f'/api/v1/projects/{pid}/plans', json=body, headers=headers)
        self.assertEqual(replay.status_code, 404, 'Deleted resources must be re-authorized before idempotent replay')

    def test_sse_reconnect_orders_events_and_strips_private_payload_fields(self):
        project = self.create_project()
        job = self.queue_plan(project['id'])
        secret = 'SYNTHETIC-PRIVATE-TOKEN-DO-NOT-STREAM'
        with self.sessions.begin() as session:
            repo = Repository(session)
            row = session.get(Job, job['id'])
            repo.event(row, 'stage.changed', {'stage': 'collecting', 'authorization': secret,
                                             'raw_log': secret, 'prompt': secret})
            repo.cancel(row)
        response = self.client.get(f'/api/v1/jobs/{job["id"]}/events', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['x-accel-buffering'], 'no')
        self.assertNotIn(secret, response.text)
        events = parse_sse(response)
        self.assertEqual([row[0] for row in events], [1, 2, 3])
        self.assertEqual(events[1][2]['payload'], {'stage': 'collecting'})
        for cursor in [0, 1, 2, 3]:
            replay = self.client.get(f'/api/v1/jobs/{job["id"]}/events',
                headers=self.headers() | {'Last-Event-ID': str(cursor)})
            self.assertEqual(parse_sse(replay), [row for row in events if row[0] > cursor])
        for cursor, expected in [('invalid', 400), ('99', 409)]:
            self.assertEqual(self.client.get(f'/api/v1/jobs/{job["id"]}/events',
                headers=self.headers() | {'Last-Event-ID': cursor}).status_code, expected)
        with self.sessions.begin() as session:
            session.get(Job, job['id']).event_floor = 2
        expired = self.client.get(f'/api/v1/jobs/{job["id"]}/events',
            headers=self.headers() | {'Last-Event-ID': '1'})
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(expired.json()['detail']['action'], 'refresh_snapshot')

    def test_terminal_sse_drains_all_event_pages_before_closing(self):
        project = self.create_project()
        job = self.queue_plan(project['id'])
        with self.sessions.begin() as session:
            repo = Repository(session)
            row = session.get(Job, job['id'])
            for _ in range(105):
                repo.event(row, 'stage.changed', {'stage': 'collecting'})
            repo.cancel(row)
            final_cursor = row.event_cursor
        response = self.client.get(f'/api/v1/jobs/{job["id"]}/events', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row[0] for row in parse_sse(response)], list(range(1, final_cursor + 1)),
                         'A terminal job still has to stream every persisted event page')

    def test_failed_usage_is_deduplicated_and_missing_cost_stays_unknown(self):
        project = self.create_project()
        job = self.queue_plan(project['id'])
        with self.sessions.begin() as session:
            repo = Repository(session)
            claimed = repo.claim_next()
            repo.attach_execution(claimed.id, claimed.attempt, claimed.fencing_token, job_uid='usage-fixture')
            repo.prove_stopped(claimed.id, claimed.attempt, claimed.fencing_token,
                              job_uid='usage-fixture', all_writers_stopped=True)
            repo.finish_failed(claimed.id, claimed.attempt, claimed.fencing_token, error_code='model_unavailable')
            one = repo.record_usage(job['id'], 1, phase='plan_initial', outcome='failed',
                provider='synthetic-provider', provider_request_id='synthetic-request-1', input_tokens=17)
            session.flush()
            duplicate = repo.record_usage(job['id'], 1, phase='plan_initial', outcome='failed',
                provider='synthetic-provider', provider_request_id='synthetic-request-1', input_tokens=17)
            self.assertEqual(one.id, duplicate.id)
        with self.sessions() as session:
            usage = session.scalars(select(UsageRecord).where(UsageRecord.job_id == job['id'])).all()
            self.assertEqual(len(usage), 1)
            self.assertEqual((usage[0].input_tokens, usage[0].output_tokens), (17, None))
            self.assertEqual((usage[0].cost_status, usage[0].reported_cost, usage[0].currency), ('unknown', None, None))
            self.assertEqual(usage[0].outcome, 'failed')
        response = self.client.get(f'/api/v1/jobs/{job["id"]}', headers=self.headers())
        self.assertEqual(response.json()['status'], 'failed')
        self.assertEqual(response.json()['error_code'], 'model_unavailable')
