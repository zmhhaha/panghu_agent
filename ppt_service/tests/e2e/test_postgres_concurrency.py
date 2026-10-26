"""Concurrent real HTTP admission and independently connected PostgreSQL claims.

SQLite cannot establish these locking guarantees; skip it explicitly. No worker
or model is started. The test barrier releases distinct clients/connections.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from ppt_service.server.models import ExecutionSlot, IdempotencyKey, Job, JobEvent
from ppt_service.server.repository import Repository
from .harness import E2ECase, ORIGIN


class PostgreSQLConcurrencyTests(E2ECase):
    def setUp(self):
        super().setUp()
        if self.database_kind != 'postgresql':
            self.skipTest('Requires isolated PostgreSQL row/advisory locks; SQLite is not equivalent')

    def simultaneous_posts(self, requests):
        barrier = Barrier(len(requests))

        def submit(request):
            pid, who, key, body = request
            with TestClient(self.app, base_url=ORIGIN, client=('127.0.0.1', 23456)) as client:
                me = client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + self.token(who)})
                self.assertEqual(me.status_code, 200, me.text)
                headers = self.headers(who) | {'X-CSRF-Token': me.json()['csrf_token'], 'Idempotency-Key': key}
                barrier.wait(timeout=10)
                response = client.post(f'/api/v1/projects/{pid}/plans', headers=headers, json=body)
                return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return list(pool.map(submit, requests))

    def intent(self, who='alice'):
        pid = self.create_project(who)['id']
        self.upload(pid, who)
        return pid, {'expected_revision': self.project(pid, who)['revision'], 'phase': 'plan_initial'}

    def test_duplicate_concurrent_http_requests_create_one_job_and_event(self):
        pid, body = self.intent()
        replies = self.simultaneous_posts([(pid, 'alice', 'same-intent', body)] * 4)
        self.assertEqual([code for code, _ in replies], [202] * 4)
        self.assertTrue(all(payload == replies[0][1] for _, payload in replies))
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(JobEvent)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(IdempotencyKey)), 1)
        self.assertEqual(self.worker.creates, 0)

    def test_distinct_intents_on_same_revision_have_one_winner(self):
        pid, body = self.intent()
        replies = self.simultaneous_posts([(pid, 'alice', f'intent-{n}', body) for n in range(4)])
        self.assertEqual(sorted(code for code, _ in replies), [202, 409, 409, 409])
        for code, payload in replies:
            if code == 409:
                self.assertEqual(payload['detail']['code'], 'stale_project_revision')
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(JobEvent)), 1)

    def test_concurrent_projects_enforce_per_user_queue_cap(self):
        intents = [self.intent() for _ in range(3)]
        replies = self.simultaneous_posts([(pid, 'alice', f'queue-{n}', body)
                                         for n, (pid, body) in enumerate(intents)])
        self.assertEqual(sorted(code for code, _ in replies), [202, 202, 429])
        self.assertEqual(next(body for code, body in replies if code == 429)['detail']['code'], 'queue_full')
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 2)

    def test_separate_postgres_connections_cannot_double_claim_global_slot(self):
        intents = [(who, self.intent(who)) for who in ('alice', 'bob')]
        replies = self.simultaneous_posts([(pid, who, f'claim-{who}', body) for who, (pid, body) in intents])
        self.assertEqual([code for code, _ in replies], [202, 202])
        barrier = Barrier(4)

        def claim(_):
            with self.sessions.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection_pid = session.scalar(text('SELECT pg_backend_pid()'))
                barrier.wait(timeout=10)
                job = Repository(session).claim_next()
                return connection_pid, None if job is None else job.id

        with ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(claim, range(4)))
        self.assertEqual(len({pid for pid, _ in claimed}), 4)
        winners = [jid for _, jid in claimed if jid is not None]
        self.assertEqual(len(winners), 1)
        with self.sessions() as session:
            self.assertEqual(session.get(ExecutionSlot, 1).job_id, winners[0])
            self.assertEqual(sorted(session.scalars(select(Job.status)).all()), ['queued', 'starting'])
            self.assertEqual(session.scalar(select(func.count()).select_from(JobEvent).where(JobEvent.type == 'job.started')), 1)
