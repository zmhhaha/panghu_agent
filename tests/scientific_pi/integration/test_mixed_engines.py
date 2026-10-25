"""Pi-only submission with shared legacy history and a real local Pi worker.

The historical filename remains for suite discovery. Research uses synthetic
records; the HTTP apps, queue, output validation and publication are real. No
CrewAI execution, external model, network provider or email is used.
"""
from unittest.mock import patch
import unittest
import uuid

from fastapi.testclient import TestClient

from app.api.scientific_agent import create_app
from harness import IntegrationHarness


class PiOnlyMigrationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = IntegrationHarness()
        self.addCleanup(self.h.close)

    def legacy_task(self, status='running', content=None):
        task_id = str(uuid.uuid4())
        with self.h.db.lock:
            self.h.db.connection.execute(
                'INSERT INTO scientific_tasks(id,topic,status,user_id,report) VALUES(?,?,?,?,?)',
                (task_id,'Legacy shared history',status,'trusted-fixture-user',content))
            if content is not None:
                self.h.db.connection.execute(
                    'INSERT INTO scientific_reports(id,task_id,topic,content,created_at) VALUES(?,?,?,?,?)',
                    (task_id,task_id,'Legacy shared history',content,'2020-01-01'))
            self.h.db.connection.commit()
        return task_id

    def test_legacy_running_record_does_not_execute_or_block_pi_and_survives_restart(self):
        active_legacy = self.legacy_task()
        legacy_bytes = '# Historical report\r\nOriginal 科研 content.\r\n'.encode('utf-8')
        legacy_report = self.legacy_task('done',legacy_bytes.decode('utf-8'))
        # Re-enter the API lifespan with historical rows already present. Old
        # configuration cannot re-enable CrewAI or rewrite historical statuses.
        with patch.dict('os.environ',{'SCIENTIFIC_DEFAULT_ENGINE':'crewai',
                                      'LLM_BASE_URL':'','LLM_SERVICE_TOKEN':''}):
            with TestClient(create_app(self.h.db,self.h.worker,self.h.projection,start_reconciler=False)) as public:
                public.headers['X-Forwarded-User'] = 'trusted-fixture-user'
                self.assertEqual(self.h.db.legacy_cleanup_calls,0)
                self.assertEqual(public.get('/scientific-research/'+active_legacy).json()['status'],'running')
                puts = self.h.transport.count('PUT')
                rejected = public.post('/scientific-research',json={'topic':'No new CrewAI','engine':'crewai'})
                self.assertEqual(rejected.status_code,422)
                self.assertEqual(self.h.transport.count('PUT'),puts)
                submitted = public.post('/scientific-research',json={'topic':'Controlled fixture research','mode':'brief'},
                                        headers={'Idempotency-Key':'pi-default-after-migration'})
                self.assertEqual(submitted.status_code,200,submitted.text)
                pi_id = submitted.json()['id']
                self.assertEqual((submitted.json()['engine'],submitted.json()['user_id']),('pi','trusted-fixture-user'))
                self.assertIsNone(self.h.db.get_task(pi_id))
                self.assertIsNone(self.h.projection.get(active_legacy))
                busy = public.post('/scientific-research',json={'topic':'Second Pi job'},
                                   headers={'Idempotency-Key':'still-one-pi-slot'})
                self.assertEqual(busy.status_code,429,busy.text)
                self.assertEqual(public.post('/scientific-research/'+active_legacy+'/cancel').status_code,409)
                self.assertEqual(self.h.worker.get(pi_id)['state'],'queued')
                cancelled = public.post('/scientific-research/'+pi_id+'/cancel')
                self.assertEqual(cancelled.json()['state'],'cancelled')
                self.assertEqual(self.h.db.get_task(active_legacy)['status'],'running')
                next_pi = public.post('/scientific-research',json={'topic':'Controlled fixture research','mode':'brief'},
                                      headers={'Idempotency-Key':'pi-after-cancellation'})
                self.assertEqual(next_pi.status_code,200,next_pi.text)
                next_id = next_pi.json()['id']
                published = self.h.execute_next()
                self.assertEqual((published['id'],published['state']),(next_id,'publishing'))
                self.h.projection.reconcile()
                self.assertEqual(public.get('/scientific-research/'+next_id).json()['state'],'succeeded')
                self.assertEqual(public.get('/scientific-download/'+next_id).content,self.h.runtime.last_report_bytes)
                self.assertEqual(public.get('/scientific-download/'+legacy_report).content,legacy_bytes)
                self.assertEqual(self.h.db.get_task(active_legacy)['status'],'running')
                self.assertEqual(self.h.db.get_task(legacy_report)['status'],'done')
                self.assertEqual(self.h.db._query('SELECT count(*) AS n FROM scientific_reports')[0]['n'],2)
                self.assertEqual(self.h.db._query('SELECT * FROM scientific_notifications'),[])
                self.assertEqual(len(self.h.runtime.calls),1)

    def test_legacy_shared_history_remains_available_while_pi_results_are_private(self):
        legacy_id = self.legacy_task('done','# Legacy shared report\n')
        first = self.h.submit(key='first-owner')
        self.assertEqual(first.status_code,200,first.text)
        first_id = first.json()['id']
        self.h.execute_next()
        self.h.projection.reconcile()
        other = self.h.submit(key='other-owner',user='other-user')
        self.assertEqual(other.status_code,200,other.text)
        other_id = other.json()['id']
        # Real worker admission is independent for different authenticated users.
        self.assertEqual(self.h.public_http.post('/scientific-research/'+other_id+'/cancel').status_code,404)
        self.assertEqual(self.h.worker.get(other_id)['state'],'queued')
        self.h.execute_next()
        self.h.projection.reconcile()
        paths = ['/scientific-research/'+other_id,
                 '/scientific-research/'+other_id+'/artifacts',
                 '/scientific-research/'+other_id+'/artifacts/report',
                 '/scientific-reports/'+other_id,'/scientific-download/'+other_id]
        for path in paths:
            self.assertEqual(self.h.public_http.get(path).status_code,404,path)
            self.assertEqual(self.h.public_http.get(path,headers={'X-Forwarded-User':''}).status_code,401,path)
            self.assertEqual(self.h.public_http.get(path,headers={'X-Forwarded-User':'other-user'}).status_code,200,path)
        own_search = self.h.public_http.get('/scientific-reports').json()
        self.assertEqual({row['id'] for row in own_search},{legacy_id,first_id})
        anonymous_search = self.h.public_http.get('/scientific-reports',headers={'X-Forwarded-User':''}).json()
        self.assertEqual([row['id'] for row in anonymous_search],[legacy_id])
        self.assertEqual(self.h.public_http.get('/scientific-research/'+legacy_id,headers={'X-Forwarded-User':''}).status_code,200)
        self.assertEqual(self.h.public_http.get('/scientific-download/'+legacy_id,headers={'X-Forwarded-User':''}).content,
                         b'# Legacy shared report\n')
        self.assertEqual(self.h.db.legacy_cleanup_calls,0)
        self.assertEqual(len(self.h.runtime.calls),2)
        self.assertEqual(self.h.db._query('SELECT * FROM scientific_notifications'),[])


if __name__ == '__main__':
    unittest.main()
