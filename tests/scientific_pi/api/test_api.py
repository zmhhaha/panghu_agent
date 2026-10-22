"""Local package D regression suite; no model, network, pytest, or shared service."""
import importlib
import json
import logging
import sqlite3
import threading
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.api.scientific_agent import create_app
from scientific_agent.pi_client import PiClient, WorkerError
from scientific_agent.pi_contracts import report_sha256, result_digest, public_status
from scientific_agent.pi_projection import PiProjection


class MemoryDB:
    def __init__(self):
        self.connection = sqlite3.connect(':memory:', check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.fail_reports = False
        self.fail_index = False
        self.skip_report_insert = False
        self.sql = []

    def _execute(self, sql):
        with self.lock:
            self.sql.append(sql)
            if self.fail_reports and 'scientific_reports' in sql: raise OSError('report store offline')
            if self.fail_index and 'scientific_pi_index' in sql: raise OSError('index offline')
            if self.skip_report_insert and sql.startswith('INSERT INTO scientific_reports'): return
            self.connection.execute(sql)
            self.connection.commit()

    def _query(self, sql):
        with self.lock:
            if self.fail_reports and 'scientific_reports' in sql: raise OSError('report store offline')
            if self.fail_index and 'scientific_pi_index' in sql: raise OSError('index offline')
            return [dict(row) for row in self.connection.execute(sql).fetchall()]

    def init_db(self, service):
        assert service == 'scientific'
        self._execute('CREATE TABLE IF NOT EXISTS scientific_tasks (id TEXT PRIMARY KEY, topic TEXT, status TEXT, user_id TEXT, report TEXT, error TEXT)')
        self._execute("CREATE TABLE IF NOT EXISTS scientific_reports (id TEXT PRIMARY KEY, task_id TEXT, topic TEXT, summary TEXT, keywords TEXT, content TEXT, model_used TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")

    def clear_stale_tasks(self):
        self._execute("UPDATE scientific_tasks SET status='failed' WHERE status IN ('pending','running')")

    def create_task(self, topic, user_id):
        task_id = str(uuid.uuid4())
        with self.lock:
            self.connection.execute('INSERT INTO scientific_tasks VALUES (?,?,?,?,NULL,NULL)', (task_id, topic, 'pending', user_id))
            self.connection.commit()
        return task_id

    def get_task(self, task_id):
        rows = self._query("SELECT * FROM scientific_tasks WHERE id='"+task_id+"'")
        return rows[0] if rows else None

    def get_running_task_by_user(self, user_id): return None
    def get_report_by_task(self, task_id): return self.get_report(task_id)
    def get_report(self, task_id):
        rows = self._query("SELECT * FROM scientific_reports WHERE id='"+task_id+"'")
        return rows[0] if rows else None
    def search_reports(self, q, limit, offset):
        return self._query('SELECT * FROM scientific_reports LIMIT '+str(limit)+' OFFSET '+str(offset))


class FakeWorker:
    def __init__(self):
        self.tasks, self.results = {}, {}
        self.put_count = self.ack_count = 0
        self.reject = None
        self.timeout_after_put = False
        self.ack_failure = None

    def get(self, task_id):
        if task_id not in self.tasks: raise WorkerError(404, 'task_not_found', 'Task not found')
        return dict(self.tasks[task_id])

    def put(self, job):
        self.put_count += 1
        if self.reject: raise WorkerError(self.reject, 'user_busy' if self.reject == 429 else 'task_conflict', 'Rejected')
        task = dict(id=job['task_id'], topic=job['topic'], engine='pi', mode=job['mode'], state='queued',
                    status='pending', stage=None, progress=None, attempt=0, revision=1,
                    created_at=job['created_at'], updated_at=job['created_at'], execution_deadline_at=job['execution_deadline_at'],
                    started_at=None, finished_at=None, error_code=None, error=None, report=None, report_id=None)
        self.tasks[job['task_id']] = task
        if self.timeout_after_put: raise WorkerError(503, 'worker_unavailable', 'Timed out', True)
        return dict(task)

    def complete(self, task_id, content='# Review\r\nExact UTF-8: 科研\r\n'):
        self.tasks[task_id].update(state='publishing', status='running', attempt=1, stage='publish', revision=2)
        result = dict(schema_version='1', task_id=task_id, attempt=1, report_markdown=content,
                      report_sha256=report_sha256(content), artifacts=[], provenance={'model':'fixture-model'}, validation={})
        result['result_digest'] = result_digest(result)
        self.results[task_id] = result

    def result(self, task_id): return dict(self.results[task_id])
    def pending(self, cursor=None):
        return dict(items=[dict(task) for task in self.tasks.values() if task['state']=='publishing'], next_cursor=None)
    def ack(self, task_id, payload):
        self.ack_count += 1
        result = self.results[task_id]
        assert payload == dict(attempt=1, result_digest=result['result_digest'], report_id=task_id)
        if self.ack_failure == 'before': raise WorkerError(503, 'worker_unavailable', 'Ack lost', True)
        self.tasks[task_id].update(state='succeeded', status='done', report_id=task_id)
        if self.ack_failure == 'after': raise WorkerError(503, 'worker_unavailable', 'Ack response lost', True)
        return self.get(task_id)
    def cancel(self, task_id):
        if self.tasks[task_id]['state'] == 'queued':
            self.tasks[task_id].update(state='cancelled', status='failed', error_code='cancelled', error='Cancelled')
        return self.get(task_id)
    def artifacts(self, task_id):
        return {'items':[dict(artifact_id='report', media_type='text/markdown', size=3, sha256='a'*64, path='/private/control', token='secret')]}
    def artifact(self, task_id, artifact_id):
        return b'abc', {'content-type':'text/markdown', 'Authorization':'secret'}
    def health(self):
        return dict(alive=True, queue_writable=True, runtime_ready=True, worker_ready=True, capabilities={'protocol':'v1'})


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.database, self.worker = MemoryDB(), FakeWorker()
        self.sent = []
        self.projection = PiProjection(self.database, self.worker, 'skills-pinned', 'runtime-pinned', lambda *args:self.sent.append(args))
        self.client = TestClient(create_app(self.database, self.worker, self.projection, start_reconciler=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(self.database.connection.close)
        self.payload = dict(topic='  Test review  ', user_id='trusted-user', engine='pi', mode='review')

    def submit(self, key='fixed-key', **extra):
        return self.client.post('/scientific-research', json=dict(self.payload, **extra), headers={'Idempotency-Key':key})

    def accepted(self, **extra):
        response = self.submit(**extra)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def restart(self):
        self.projection = PiProjection(self.database, self.worker, 'skills-pinned', 'runtime-pinned', lambda *args:self.sent.append(args))
        self.projection.initialize()
        return self.projection

    def test_definitive_rejections_never_replayed(self):
        for status in (429,409,422):
            with self.subTest(status=status):
                self.worker.reject = status
                response = self.submit(key='reject-'+str(status))
                self.assertEqual(response.status_code, status)
                task_id = response.json()['detail']['task_id']
                self.assertEqual(self.projection.get(task_id)['dispatch'], 'rejected')
                before = self.worker.put_count
                self.worker.reject = None
                self.restart().reconcile()
                again = self.submit(key='reject-'+str(status))
                self.assertEqual(again.status_code, status)
                self.assertEqual(self.worker.put_count, before)
                self.assertNotIn(task_id, self.worker.tasks)

    def test_ambiguous_acceptance_preserves_id_and_recovers_without_new_put(self):
        self.worker.timeout_after_put = True
        response = self.submit()
        self.assertEqual(response.status_code, 503)
        detail = response.json()['detail']
        task_id = detail['task_id']
        self.assertTrue(detail['retryable'])
        self.assertEqual(self.projection.get(task_id)['dispatch'], 'ambiguous')
        self.restart().reconcile()
        self.assertEqual(self.projection.get(task_id)['dispatch'], 'accepted')
        self.assertEqual(self.worker.put_count, 1)
        self.assertEqual(self.submit().json()['id'], task_id)

    def test_prepared_intent_recovers_and_replay_keeps_original_job(self):
        payload = dict(topic='Original',user_id='u',engine='pi',mode='review',timeout_seconds=1800)
        row = self.projection.prepare(payload, '', 'prepared')
        original = row['job']
        projection = PiProjection(self.database,self.worker,'','')
        self.assertEqual(projection.prepare(payload,'','prepared')['job'], original)
        projection.reconcile()
        self.assertEqual(self.worker.put_count, 1)
        self.assertEqual(projection.get(row['id'])['dispatch'], 'accepted')

    def test_index_failure_does_not_dispatch(self):
        self.database.fail_index = True
        response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.worker.put_count, 0)

    def test_report_store_failure_keeps_running_then_recovers_exact_bytes(self):
        task_id = self.accepted()
        self.worker.complete(task_id)
        self.database.fail_reports = True
        self.projection.reconcile()
        response = self.client.get('/scientific-research/'+task_id)
        self.assertEqual(response.json()['status'], 'running')
        self.assertEqual(response.json()['error_code'], 'storage_unavailable')
        self.assertEqual(self.worker.ack_count, 0)
        self.database.fail_reports = False
        self.restart().reconcile()
        task = self.client.get('/scientific-research/'+task_id).json()
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['report_id'], task_id)
        self.assertEqual(task['report'], self.worker.results[task_id]['report_markdown'])
        report = self.client.get('/scientific-reports/'+task_id).json()
        self.assertEqual(report['id'], task_id)
        self.assertIsInstance(report['keywords'], str)
        self.assertIsInstance(json.loads(report['keywords']), list)
        self.assertEqual(self.client.get('/scientific-download/'+task_id).content, task['report'].encode('utf-8'))
        self.assertIsInstance(self.client.get('/scientific-reports').json(), list)

    def test_corrupt_worker_result_is_not_stored_or_acknowledged(self):
        task_id = self.accepted()
        self.worker.complete(task_id)
        self.worker.results[task_id]['report_markdown'] += 'altered'
        self.projection.reconcile()
        self.assertIsNone(self.database.get_report(task_id))
        self.assertEqual(self.worker.ack_count,0)
        self.assertEqual(self.projection.get(task_id)['error_code'],'publication_conflict')

    def test_recovery_get_definitive_errors_never_put(self):
        for status in (409,422,429):
            with self.subTest(status=status):
                row = self.projection.prepare(dict(topic='Recover rejection',user_id='u',engine='pi',mode='review',timeout_seconds=1800),'','get-reject-'+str(status))
                with patch.object(self.worker,'get',side_effect=WorkerError(status,'task_conflict','Durably rejected')):
                    with self.assertRaises(WorkerError): self.projection.dispatch(row)
                self.assertEqual(self.projection.get(row['id'])['dispatch'],'rejected')
                self.assertEqual(self.worker.put_count,0)

    def test_missing_insert_readback_does_not_ack(self):
        task_id = self.accepted()
        self.worker.complete(task_id)
        self.database.skip_report_insert = True
        self.projection.reconcile()
        self.assertEqual(self.projection.get(task_id)['error_code'], 'storage_unavailable')
        self.assertEqual(self.worker.ack_count, 0)

    def test_report_conflict_never_overwrites_or_acks(self):
        task_id = self.accepted()
        self.worker.complete(task_id)
        self.database.connection.execute('INSERT INTO scientific_reports (id,task_id,content) VALUES (?,?,?)', (task_id,task_id,'foreign content'))
        self.database.connection.commit()
        self.projection.reconcile()
        self.assertEqual(self.database.get_report(task_id)['content'], 'foreign content')
        self.assertEqual(self.projection.get(task_id)['error_code'], 'publication_conflict')
        self.assertEqual(self.worker.ack_count, 0)

    def test_restart_after_report_insert_before_ack(self):
        task_id = self.accepted()
        self.worker.complete(task_id)
        self.worker.ack_failure = 'before'
        self.projection.reconcile()
        self.assertIsNotNone(self.database.get_report(task_id))
        self.assertEqual(self.worker.get(task_id)['state'], 'publishing')
        self.assertEqual(self.worker.cancel(task_id)['state'], 'publishing')
        self.worker.ack_failure = None
        self.restart().reconcile()
        self.assertEqual(self.worker.get(task_id)['state'], 'succeeded')
        self.assertEqual(len(self.database._query('SELECT * FROM scientific_reports')), 1)
        self.assertTrue(all('REPLACE' not in sql for sql in self.database.sql))

    def test_ack_commit_response_loss_recovers_notification_independently(self):
        task_id = self.accepted(email='user@example.test')
        self.worker.complete(task_id)
        self.worker.ack_failure = 'after'
        self.projection.reconcile()
        self.assertEqual(self.worker.get(task_id)['state'], 'succeeded')
        self.restart().reconcile()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.projection.get(task_id)['notification_status'], 'sent')
        self.assertEqual(self.client.get('/scientific-research/'+task_id).json()['status'], 'done')

    def test_notification_failure_never_reverses_success(self):
        task_id = self.accepted(email='user@example.test')
        self.worker.complete(task_id)
        def fail(*args): raise TimeoutError('possibly delivered')
        self.projection.notifier = fail
        self.projection.reconcile()
        self.assertEqual(self.worker.get(task_id)['state'], 'succeeded')
        self.assertEqual(self.projection.get(task_id)['notification_status'], 'unknown')
        self.assertEqual(self.client.get('/scientific-research/'+task_id).json()['status'], 'done')

    def test_cancelled_mapping_and_idempotent_cancel(self):
        task_id = self.accepted()
        response = self.client.post('/scientific-research/'+task_id+'/cancel')
        self.assertEqual(response.json()['state'], 'cancelled')
        self.assertEqual(response.json()['status'], 'failed')
        self.assertEqual(response.json()['user_id'], 'trusted-user')
        self.assertEqual(self.client.post('/scientific-research/'+task_id+'/cancel').json()['state'], 'cancelled')

    def test_artifact_metadata_filtered_and_headers_safe(self):
        task_id = self.accepted()
        response = self.client.get('/scientific-research/'+task_id+'/artifacts')
        self.assertEqual(set(response.json()['items'][0]), {'artifact_id','media_type','size','sha256'})
        response = self.client.get('/scientific-research/'+task_id+'/artifacts/report')
        self.assertEqual(response.content,b'abc')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertNotIn('authorization', response.headers)
        self.assertEqual(self.client.get('/scientific-research/'+task_id+'/artifacts/bad.name').status_code,422)

    def test_legacy_default_fields_without_pi_fallback(self):
        with patch.dict('os.environ', {'LLM_BASE_URL':'http://llm.invalid','LLM_SERVICE_TOKEN':'test'}), patch('app.api.scientific_agent.threading.Thread'):
            response = self.client.post('/scientific-research',json=dict(topic='Legacy',user_id='old-user'))
        self.assertEqual(response.status_code,200)
        task = response.json()
        self.assertEqual(task['engine'],'crewai')
        self.assertEqual(task['status'],'pending')
        self.assertEqual(task['user_id'],'old-user')
        self.assertEqual(self.client.get('/scientific-research/'+task['id']).json()['topic'],'Legacy')
        self.assertEqual(self.worker.put_count,0)

    def test_legacy_execution_stores_before_done_and_mail_failure_stays_done(self):
        import types
        import app.api.scientific_agent as api
        events = []
        class LegacyDB:
            def update_task(self,task_id,**values): events.append(('task',values['status']))
            def save_report(self,*args): events.append(('report',args[-1]))
            def get_report_by_task(self,task_id): return {'content':'Legacy result'}
        crew = types.ModuleType('scientific_agent.crew')
        crew.create_scientific_crew = lambda:types.SimpleNamespace(kickoff=lambda **kwargs:'Legacy result')
        notifier = types.SimpleNamespace(notify=lambda *args:(_ for _ in ()).throw(TimeoutError('mail failed')))
        with patch.dict('sys.modules',{'scientific_agent.crew':crew}):
            api._run_refinement('fixture-id','Legacy','user@example.test',LegacyDB(),notifier)
        self.assertEqual(events,[('task','running'),('report','Legacy result'),('task','done')])

    def test_health_uses_actual_worker_readiness(self):
        with patch.dict('os.environ', {'SCIENTIFIC_DEFAULT_ENGINE':'pi'}):
            self.assertTrue(self.client.get('/scientific-health').json()['worker_ready'])
            self.worker.health = lambda:dict(alive=True,queue_writable=True,runtime_ready=False,worker_ready=False)
            self.assertEqual(self.client.get('/scientific-health').json()['status'],'degraded')

    def test_validation_and_idempotency_scope(self):
        for change in ({'user_id':''},{'topic':'   '},{'engine':'bad'},{'mode':'bad'},{'timeout_seconds':True},{'timeout_seconds':60.5}):
            self.assertEqual(self.submit(**change).status_code,422)
        first = self.accepted()
        self.assertEqual(self.submit().json()['id'], first)
        self.assertEqual(self.submit(topic='Changed').status_code,409)
        self.assertEqual(self.submit(email='new@example.test').status_code,409)
        self.assertNotEqual(self.submit(user_id='another-user').json()['id'],first)
        self.assertEqual(self.client.get('/scientific-research/not-a-uuid').status_code,422)


class ClientTests(unittest.TestCase):
    def test_exact_v1_routes_and_auth_including_health(self):
        calls = []
        task_id = str(uuid.uuid4())
        def transport(*args):
            calls.append(args)
            return 200, {'content-type':'text/plain'}, b'{}'
        client = PiClient('http://worker/', 'control-secret', transport)
        client.put({'task_id':task_id}); client.get(task_id); client.cancel(task_id)
        client.result(task_id); client.pending('opaque+/='); client.ack(task_id,{'attempt':1})
        client.artifacts(task_id); client.artifact(task_id,'report'); client.health()
        paths = [('PUT','/tasks/'+task_id),('GET','/tasks/'+task_id),('POST','/tasks/'+task_id+'/cancel'),
                 ('GET','/tasks/'+task_id+'/result'),('GET','/tasks?publication=pending&limit=50&cursor=opaque%2B%2F%3D'),
                 ('POST','/tasks/'+task_id+'/publication-ack'),('GET','/tasks/'+task_id+'/artifacts'),
                 ('GET','/tasks/'+task_id+'/artifacts/report'),('GET','/health')]
        self.assertEqual([(call[0],call[1]) for call in calls],[(method,'http://worker/v1'+path) for method,path in paths])
        self.assertTrue(all(call[2]['Authorization']=='Bearer control-secret' for call in calls))

    def test_no_control_secret_in_errors_or_transport_exceptions(self):
        for body in (b'not-json', b'[]', json.dumps({'error':{'code':'user_busy','message':'secret /private/control'}}).encode()):
            client = PiClient('http://worker','secret',lambda *args:(429,{},body))
            with self.assertRaises(WorkerError) as error: client.health()
            self.assertNotIn('secret',str(error.exception))
            self.assertNotIn('/private',str(error.exception))
        def fail(*args): raise TimeoutError('secret')
        with self.assertRaises(WorkerError) as error: PiClient('http://worker','secret',fail).health()
        self.assertEqual(error.exception.status,503)
        self.assertNotIn('secret',str(error.exception))

    def test_import_and_construction_do_not_touch_network(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network on import')) as urlopen:
            import app.api.scientific_agent as api
            importlib.reload(api)
            PiClient('http://worker','secret')
            self.assertEqual(urlopen.call_count,0)

    def test_all_cancel_timeout_public_mappings(self):
        for state in ('cancelled','timed_out','failed'): self.assertEqual(public_status(state),'failed')
        for state in ('publishing','validating','cancel_requested'): self.assertEqual(public_status(state),'running')


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
