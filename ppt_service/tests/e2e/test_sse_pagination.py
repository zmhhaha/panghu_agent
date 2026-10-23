"""Persisted event replay through HTTP and controlled per-page stream timing."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from ppt_service.server.models import Job
from ppt_service.server.repository import Repository
from .harness import E2ECase
from .test_security import parse_sse


class SSEPaginationTests(E2ECase):
    def event_job(self, count, *, terminal=True):
        pid = self.create_project()['id']
        jid = self.queue_plan(pid)['id']
        with self.sessions.begin() as session:
            repo = Repository(session)
            job = session.get(Job, jid)
            for _ in range(count - (2 if terminal else 1)):
                repo.event(job, 'stage.changed', {'stage': 'collecting'})
            if terminal:
                repo.cancel(job)
            self.assertEqual(job.event_cursor, count)
        return pid, jid

    def response(self, jid, cursor=0):
        return self.client.get(f'/api/v1/jobs/{jid}/events',
                               headers=self.headers() | {'Last-Event-ID': str(cursor)})

    def test_terminal_replay_includes_exact_and_partial_page_boundaries(self):
        # All event batches come from real persisted rows and authenticated HTTP.
        for count in (2, 99, 100, 101, 199, 200, 201, 207):
            with self.subTest(count=count):
                _pid, jid = self.event_job(count)
                response = self.response(jid)
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row[0] for row in parse_sse(response)], list(range(1, count + 1)))
                self.assertEqual(response.text.count(': keepalive'), 0)

    def test_multi_page_reconnect_preserves_cursor_order_without_duplicates(self):
        _pid, jid = self.event_job(207)
        for cursor in (1, 99, 100, 101, 200, 207):
            with self.subTest(cursor=cursor):
                response = self.response(jid, cursor)
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row[0] for row in parse_sse(response)], list(range(cursor + 1, 208)))

    def test_query_cursor_and_header_precedence_across_page_boundaries(self):
        _pid, jid = self.event_job(207)
        path = f'/api/v1/jobs/{jid}/events'
        query = self.client.get(path + '?after=99', headers=self.headers())
        self.assertEqual([row[0] for row in parse_sse(query)], list(range(100, 208)))
        preferred = self.client.get(path + '?after=1',
            headers=self.headers() | {'Last-Event-ID': '199'})
        self.assertEqual([row[0] for row in parse_sse(preferred)], list(range(200, 208)))
        with self.sessions.begin() as session:
            session.get(Job, jid).event_floor = 100
        at_floor = self.response(jid, 100)
        self.assertEqual(at_floor.status_code, 200)
        self.assertEqual([row[0] for row in parse_sse(at_floor)], list(range(101, 208)))

    def stream(self, jid, disconnected=None):
        # Direct handler invocation gives deterministic between-page timing.
        # HTTP authentication is verified by the tests above and test_security.
        async def connected():
            return disconnected() if disconnected is not None else False
        request = SimpleNamespace(query_params={}, is_disconnected=connected)
        endpoint = next(route.endpoint for route in self.app.routes
                        if getattr(route, 'path', '') == '/api/v1/jobs/{jid}/events')
        with self.sessions() as session:
            return endpoint(jid, request, last_event_id='0', owner=self.owner(), s=session).body_iterator

    def test_disconnect_after_first_page_resumes_from_last_delivered_event(self):
        _pid, jid = self.event_job(207)
        state = {'disconnected': False}
        iterator = self.stream(jid, lambda: state['disconnected'])

        async def exercise():
            chunks = []
            async for chunk in iterator:
                chunks.append(chunk)
                if len(chunks) == 100:
                    state['disconnected'] = True
            return chunks

        chunks = asyncio.run(exercise())
        first = parse_sse(SimpleNamespace(text=''.join(chunks)))
        resumed = parse_sse(self.response(jid, first[-1][0]))
        self.assertEqual(len(first), 100)
        self.assertEqual([row[0] for row in first + resumed], list(range(1, 208)))

    def test_deletion_between_pages_stops_the_existing_stream(self):
        pid, jid = self.event_job(207)
        iterator = self.stream(jid)

        async def exercise():
            events = []
            async for chunk in iterator:
                events.append(chunk)
                if len(events) == 100:
                    response = self.client.delete(f'/api/v1/projects/{pid}', headers=self.headers())
                    self.assertEqual(response.status_code, 202)
            return events

        chunks = asyncio.run(exercise())
        self.assertEqual(len(chunks), 100)
        self.assertIn('id: 100\n', chunks[-1])
        self.assertEqual(self.response(jid, 100).status_code, 404)

    def test_retention_between_pages_requests_a_fresh_snapshot(self):
        _pid, jid = self.event_job(207)
        iterator = self.stream(jid)

        async def exercise():
            chunks = []
            async for chunk in iterator:
                chunks.append(chunk)
                if len(chunks) == 100:
                    with self.sessions.begin() as session:
                        session.get(Job, jid).event_floor = 150
            return chunks

        chunks = asyncio.run(exercise())
        self.assertEqual(len(chunks), 101)
        self.assertEqual(chunks[-1], 'event: snapshot.required\ndata: {"action":"refresh_snapshot"}\n\n')

    def test_running_job_drains_backlog_before_waiting_and_then_finishes(self):
        _pid, jid = self.event_job(207, terminal=False)
        iterator = self.stream(jid)

        async def exercise():
            chunks = []
            async for chunk in iterator:
                chunks.append(chunk)
                if len(chunks) == 207:
                    with self.sessions.begin() as session:
                        Repository(session).cancel(session.get(Job, jid))
            return chunks

        with patch('ppt_service.server.app.asyncio.sleep', side_effect=AssertionError('Backlog must drain before idle wait')):
            chunks = asyncio.run(exercise())
        self.assertEqual(len(chunks), 208)
        self.assertIn('event: job.cancelled\n', chunks[-1])
