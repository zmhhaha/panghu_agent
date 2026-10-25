"""Exercise D's real HTTP client against B's local /v1 ASGI app.

Runtime and result validation are deliberately bypassed; this verifies protocol
integration and publication, not Pi/model or isolation readiness.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from fastapi.testclient import TestClient
from pi_worker.app import create_app as create_worker, WorkerSettings
from scientific_agent.pi_client import PiClient
from scientific_agent.pi_contracts import report_sha256, result_digest
from scientific_agent.pi_projection import PiProjection
from app.api.scientific_agent import create_app
from test_api import MemoryDB


class WorkerHTTPTests(unittest.TestCase):
    def test_real_http_admission_cancel_and_publication(self):
        with tempfile.TemporaryDirectory() as root:
            settings = WorkerSettings(Path(root), 'fixture-control-token', 'runtime-pinned', 'skills-pinned', start_scheduler=False)
            runtime = type('FixtureRuntime', (), {'ready':True, 'capabilities':{'fixture':True}})()
            with TestClient(create_worker(settings, runtime=runtime)) as internal:
                self.assertEqual(internal.get('/v1/health').status_code, 401)
                calls = []
                def transport(method, url, headers, payload, timeout):
                    calls.append((method,url))
                    response = internal.request(method,url,headers=headers,json=payload)
                    return response.status_code,dict(response.headers),response.content
                worker = PiClient('http://worker',settings.worker_token,transport)
                database = MemoryDB()
                try:
                    projection = PiProjection(database,worker,'skills-pinned','runtime-pinned')
                    with TestClient(create_app(database,worker,projection,start_reconciler=False)) as public:
                        public.headers['X-Forwarded-User'] = 'user'
                        self.assertTrue(public.get('/scientific-health').json()['worker_ready'])
                        payload = dict(topic='Fixture',user_id='user',engine='pi')
                        task_id = public.post('/scientific-research',json=payload).json()['id']
                        rejected = public.post('/scientific-research',json=payload,headers={'Idempotency-Key':'rejected'})
                        self.assertEqual(rejected.status_code,429)
                        rejected_id = rejected.json()['detail']['task_id']
                        self.assertEqual(public.post('/scientific-research/'+task_id+'/cancel').json()['state'],'cancelled')
                        projection.reconcile()
                        self.assertEqual(internal.get('/v1/tasks/'+rejected_id,headers={'Authorization':'Bearer '+settings.worker_token}).status_code,429)
                        task_id = public.post('/scientific-research',json=payload).json()['id']
                        store = internal.app.state.store
                        claimed = store.claim()
                        self.assertEqual(claimed['snapshot']['id'],task_id)
                        content = '# Fixture\nEvidence remains limited.\n'
                        result = dict(schema_version='1',task_id=task_id,attempt=1,report_markdown=content,
                                      report_sha256=report_sha256(content),artifacts=[],provenance={'model':'fixture'},validation={})
                        result['result_digest'] = result_digest(result)
                        store.publish(task_id,1,claimed['token'],result,Path(root)/'fixture-result')
                        self.assertEqual(public.post('/scientific-research/'+task_id+'/cancel').json()['state'],'publishing')
                        projection.reconcile()
                        response = public.get('/scientific-research/'+task_id)
                        self.assertEqual(response.json()['status'],'done')
                        self.assertEqual(response.json()['report'],content)
                        self.assertEqual(public.get('/scientific-research/'+task_id+'/artifacts').json(),{'items':[]})
                        self.assertEqual(public.get('/scientific-download/'+task_id).content,content.encode())
                        self.assertTrue(any(url.endswith('/publication-ack') for method,url in calls))
                finally:
                    database.connection.close()

    def test_worker_rejection_survives_failed_index_update_and_restart(self):
        class RejectionWriteFailureDB(MemoryDB):
            fail_rejection_write = True
            def _execute(self, sql):
                if self.fail_rejection_write and sql.startswith('UPDATE scientific_pi_index') and "dispatch='rejected'" in sql:
                    raise OSError('API rejection write unavailable')
                return super()._execute(sql)
        with tempfile.TemporaryDirectory() as root:
            settings = WorkerSettings(Path(root), 'token', 'runtime', 'skills', start_scheduler=False)
            runtime = type('FixtureRuntime', (), {'ready':True})()
            with TestClient(create_worker(settings, runtime=runtime)) as internal:
                puts = []
                def transport(method,url,headers,payload,timeout):
                    if method == 'PUT': puts.append(url)
                    response = internal.request(method,url,headers=headers,json=payload)
                    return response.status_code,dict(response.headers),response.content
                worker = PiClient('http://worker','token',transport)
                database = RejectionWriteFailureDB()
                try:
                    projection = PiProjection(database,worker,'skills','runtime')
                    with TestClient(create_app(database,worker,projection,start_reconciler=False)) as public:
                        public.headers['X-Forwarded-User'] = 'same-user'
                        payload = dict(topic='Busy fixture',user_id='same-user',engine='pi')
                        active_id = public.post('/scientific-research',json=payload).json()['id']
                        response = public.post('/scientific-research',json=payload,headers={'Idempotency-Key':'rejected-before-crash'})
                        self.assertEqual(response.status_code,503)
                        rejected_id = response.json()['detail']['task_id']
                        self.assertEqual(projection.get(rejected_id)['dispatch'],'prepared')
                        public.post('/scientific-research/'+active_id+'/cancel')
                        original_puts = len(puts)
                        database.fail_rejection_write = False
                        reopened = PiProjection(database,worker,'skills','runtime')
                        reopened.initialize()
                        reopened.reconcile()
                        self.assertEqual(reopened.get(rejected_id)['dispatch'],'rejected')
                        self.assertEqual(reopened.get(rejected_id)['rejection_status'],429)
                        self.assertEqual(len(puts),original_puts,'Recovered rejection must never send PUT')
                        self.assertEqual(public.post('/scientific-research',json=payload,headers={'Idempotency-Key':'rejected-before-crash'}).status_code,429)
                finally:
                    database.connection.close()

    def test_startup_reconciler_publishes_without_browser_polling(self):
        from test_api import FakeWorker
        database, worker = MemoryDB(), FakeWorker()
        database.init_db('scientific')
        observed = threading.Event()
        class ObservedProjection(PiProjection):
            def reconcile(self):
                try: super().reconcile()
                finally: observed.set()
        projection = ObservedProjection(database,worker,'skills','runtime')
        projection.initialize()
        row = projection.prepare(dict(topic='Recovery',user_id='u',engine='pi',mode='brief',timeout_seconds=1800))
        projection.dispatch(row)
        worker.complete(row['id'])
        try:
            with TestClient(create_app(database,worker,projection)):
                self.assertTrue(observed.wait(timeout=2),'Startup reconciler did not run')
                self.assertEqual(worker.get(row['id'])['state'],'succeeded')
                self.assertIsNotNone(database.get_report(row['id']))
                self.assertIsNone(database.get_task(row['id']))
        finally:
            database.connection.close()
