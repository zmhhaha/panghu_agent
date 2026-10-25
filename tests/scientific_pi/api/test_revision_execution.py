"""Fixed-evidence execution and crash recovery with synthetic local results."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.api.scientific_agent import create_app
from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_contracts import result_digest
from scientific_agent.pi_projection import PiProjection
from scientific_agent.pi_report_versions import ReportVersionStore
from scientific_agent.pi_revision_validation import compose_revision
from scientific_agent.pi_sections import index_sections
from test_api import MemoryDB, FakeWorker
from test_citation_api import records_for


class RevisionWorker(FakeWorker):
    def __init__(self):
        super().__init__()
        self.revision_jobs, self.materials, self.files = {}, {}, {}
        self.supported = True

    def health(self):
        response = super().health()
        if self.supported:
            response['capabilities'].update(revision_job_versions=['1'],
                revision_input_scope='recorded_evidence_only',max_revision_input_bytes=16*1024*1024)
        return response

    def put_revision(self, job, material):
        self.revision_jobs[job['task_id']] = copy.deepcopy(job)
        self.materials[job['task_id']] = copy.deepcopy(material)
        return self.put(job)

    def candidate(self, task_id):
        job, fixed = self.revision_jobs[task_id], self.materials[task_id]
        artifacts = {k:base64.b64decode(v) for k,v in fixed['artifacts'].items()}
        output = dict(schema_version='1',section_markdown=(
            '## Analysis\r\n\r\n<!-- claim:c2 -->\r\n'
            'Revised synthetic finding [1]; abstract evidence is limited.\r\n\r\n'),
            claims=[dict(claim_id='c2',marker='<!-- claim:c2 -->',evidence_ids=['e1'],
                         assessment='supported',explanation='Synthetic recorded abstract only')])
        composed = compose_revision(fixed['report'],fixed['items'],artifacts,
                                    job['section_id'],job['base_sha256'],output)
        super().complete(task_id,composed['report_markdown'])
        result = self.results[task_id]
        raw = json.dumps(output,ensure_ascii=False).encode('utf-8')
        result['artifacts'] = composed['items'] + [dict(artifact_id='revision_output',
            media_type='application/json',size=len(raw),sha256=hashlib.sha256(raw).hexdigest())]
        result['provenance'] = dict(model='fixture',material_policy='recorded_evidence_only',
            retrieval_performed=False,full_text_restored=False,
            revision={k:job[k] for k in ('intent_id','parent_version_id','root_report_id',
                'base_sha256','section_id','section_sha256','input_sha256')})
        result['validation'] = composed['validation']
        result['result_digest'] = result_digest(result)
        self.files[task_id] = dict(composed['artifacts'],revision_output=raw)
        return composed

    def artifact(self, task_id, artifact_id, max_bytes=None):
        return self.files[task_id][artifact_id],{}


class RevisionExecutionTests(unittest.TestCase):
    REPORT = ('# Synthetic report\r\n\r\nRecorded abstract only.\r\n\r\n'
              '## Analysis\r\n\r\n<!-- claim:c1 -->\r\nSynthetic finding [1].\r\n\r\n'
              '## Closing\r\n\r\nExact original suffix 科研.\r\n')

    def setUp(self):
        self.database, self.worker = MemoryDB(), RevisionWorker()
        self.sent = []
        self.projection = PiProjection(self.database,self.worker,'skills-pinned','runtime-pinned',
                                       lambda *args:self.sent.append(args))
        self.now = datetime.now(timezone.utc)
        self.versions = ReportVersionStore(self.database,clock=lambda:self.now,
                                           snapshot_ttl=60,intent_ttl=120)
        self.client = TestClient(create_app(self.database,self.worker,self.projection,
                                start_reconciler=False,versions=self.versions))
        self.client.headers['X-Forwarded-User'] = 'owner'
        self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)
        self.addCleanup(self.database.connection.close)
        self.parent_task = self.client.post('/scientific-research',json={'topic':'Synthetic review'}).json()['id']
        self.worker.complete(self.parent_task,self.REPORT)
        self.projection.reconcile()
        self.items,self.artifacts = records_for(self.REPORT)
        index = index_sections(self.REPORT)
        self.section = next(r for r in index['sections'] if r['title'] == 'Analysis')
        with patch.object(self.worker,'artifacts',return_value={'items':self.items}), \
             patch.object(self.worker,'artifact',side_effect=lambda tid,aid,max_bytes:(self.artifacts[aid],{})):
            response = self.client.post('/scientific-reports/'+self.parent_task+'/revision-previews',
                json=dict(base_sha256=index['report_sha256'],section_id=self.section['section_id'],
                          instruction='Explain limitations using recorded evidence.',timeout_seconds=600),
                headers={'Idempotency-Key':'preview'})
        self.assertEqual(response.status_code,200,response.text)
        self.preview = response.json()
        self.url = '/scientific-revision-previews/'+self.preview['intent']['intent_id']+'/start'

    def start(self):
        response = self.client.post(self.url,json={})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['id']

    def test_revision_dispatch_reuses_task_and_publishes_candidate_without_changing_parent(self):
        task_id = self.start()
        self.assertEqual(self.start(),task_id)
        self.assertEqual(self.worker.put_count,2)
        self.assertEqual(set(self.worker.materials[task_id]),{'report','items','artifacts'})
        candidate = self.worker.candidate(task_id)
        self.projection.reconcile()
        response = self.client.get('/scientific-research/'+task_id)
        self.assertEqual(response.status_code,200,response.text)
        task = response.json()
        self.assertEqual((task['state'],task['task_kind']),('succeeded','revision'))
        version = self.versions.get_version('owner',task['candidate_version_id'],include_content=True)
        self.assertEqual(version['parent_version_id'],self.preview['parent']['version_id'])
        self.assertEqual(version['root_report_id'],self.parent_task)
        self.assertEqual(version['content'],candidate['report_markdown'])
        self.assertEqual(self.client.get('/scientific-download/'+self.parent_task).content,self.REPORT.encode())
        self.assertEqual(self.client.get('/scientific-download/'+task_id).content,candidate['report_markdown'].encode())
        self.assertEqual(self.sent,[])
        self.projection.reconcile()
        self.assertEqual(len(self.versions.list_versions('owner',self.parent_task)),2)

    def test_missing_capability_or_unauthorized_start_never_reserves_or_calls_worker(self):
        self.worker.supported = False
        response = self.client.post(self.url,json={})
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(response.json()['detail']['code'],'revision_not_supported')
        self.assertEqual(self.versions.active_revision_jobs(),[])
        with patch.object(self.worker,'health',side_effect=AssertionError('No worker access')):
            for user,status in (('',401),('other',404)):
                self.assertEqual(self.client.post(self.url,json={},headers={'X-Forwarded-User':user}).status_code,status)
        self.assertEqual(self.client.post(self.url,json={'report':'injected'}).status_code,422)
        self.assertEqual(self.worker.put_count,1)

    def test_ambiguous_put_recovers_same_job_and_never_restarts_after_cancel(self):
        self.worker.timeout_after_put = True
        response = self.client.post(self.url,json={})
        self.assertEqual(response.status_code,503,response.text)
        task_id = response.json()['detail']['task_id']
        original = self.projection.get(task_id)['job']
        self.worker.timeout_after_put = False
        self.projection.reconcile()
        self.assertEqual(self.start(),task_id)
        self.assertEqual(self.projection.get(task_id)['job'],original)
        self.assertEqual(self.worker.put_count,2)
        self.assertEqual(self.client.post('/scientific-research/'+task_id+'/cancel').json()['state'],'cancelled')
        self.assertEqual(self.start(),task_id)
        self.assertEqual(self.worker.put_count,2)
        self.assertEqual(self.database.get_report(self.parent_task)['content'],self.REPORT)

    def test_reserved_binding_recovers_when_index_write_fails(self):
        self.database.fail_index = True
        response = self.client.post(self.url,json={})
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(self.worker.put_count,1)
        binding = self.versions.active_revision_jobs()[0]
        self.database.fail_index = False
        reopened = PiProjection(self.database,self.worker,'','')
        reopened.versions = self.versions
        reopened.reconcile()
        self.assertEqual(self.worker.put_count,2)
        self.assertEqual(self.start(),binding['task_id'])
        self.assertEqual(self.worker.put_count,2)

    def test_active_binding_pins_expired_snapshot_and_terminal_state_releases_it(self):
        task_id = self.start()
        self.now += timedelta(seconds=180)
        self.assertEqual(self.versions.cleanup_expired()['cleared_snapshots'],0)
        self.assertTrue(self.versions.get_snapshot('owner',self.preview['snapshot']['snapshot_id'])['pinned'])
        self.assertEqual(self.start(),task_id)
        self.client.post('/scientific-research/'+task_id+'/cancel')
        self.assertEqual(self.versions.cleanup_expired()['cleared_snapshots'],1)
        self.assertEqual(self.start(),task_id)
        self.assertEqual(self.worker.put_count,2)

    def test_candidate_lineage_is_reserved_before_report_and_recovers_after_storage_failure(self):
        task_id = self.start()
        composed = self.worker.candidate(task_id)
        self.database.skip_report_insert = True
        self.projection.reconcile()
        self.assertEqual(self.worker.tasks[task_id]['state'],'publishing')
        self.assertIsNone(self.database.get_report(task_id))
        binding = self.versions.get_revision_job('owner',task_id)
        self.assertIsNotNone(binding['candidate_version_id'])
        imported = self.versions.ensure_base('owner',task_id,composed['report_markdown'],'Synthetic review')
        self.assertEqual(imported['parent_version_id'],self.preview['parent']['version_id'])
        self.database.skip_report_insert = False
        self.projection.reconcile()
        self.assertEqual(self.worker.tasks[task_id]['state'],'succeeded')
        self.assertEqual(len(self.versions.list_versions('owner',self.parent_task)),2)

    def test_malformed_or_drifting_result_is_not_registered_published_or_acknowledged(self):
        task_id = self.start()
        self.worker.candidate(task_id)
        valid = copy.deepcopy(self.worker.results[task_id])
        ack_count = self.worker.ack_count
        for drift in ('report','provenance','artifact','output'):
            with self.subTest(drift=drift):
                result = copy.deepcopy(valid)
                original_file = self.worker.files[task_id]['revision_output']
                if drift == 'report':
                    result['report_markdown'] += 'outside edit'
                    result['report_sha256'] = hashlib.sha256(result['report_markdown'].encode()).hexdigest()
                elif drift == 'provenance': result['provenance']['revision']['section_sha256'] = '0'*64
                elif drift == 'artifact': next(r for r in result['artifacts'] if r['artifact_id']=='papers')['sha256'] = '0'*64
                else:
                    self.worker.files[task_id]['revision_output'] = b'{}'
                    item = next(r for r in result['artifacts'] if r['artifact_id']=='revision_output')
                    item.update(size=2,sha256=hashlib.sha256(b'{}').hexdigest())
                result['result_digest'] = result_digest(result)
                self.worker.results[task_id] = result
                self.projection.reconcile()
                self.assertIsNone(self.database.get_report(task_id))
                self.assertEqual(self.worker.ack_count,ack_count)
                self.assertEqual(len(self.versions.list_versions('owner',self.parent_task)),1)
                self.worker.files[task_id]['revision_output'] = original_file
        self.worker.results[task_id] = valid
        self.projection.reconcile()
        self.assertEqual(self.worker.tasks[task_id]['state'],'succeeded')

    def test_ack_loss_and_definitive_rejection_keep_recoverable_tombstones(self):
        task_id = self.start()
        self.worker.candidate(task_id)
        self.worker.ack_failure = 'after'
        self.projection.reconcile()
        self.assertEqual(self.worker.tasks[task_id]['state'],'succeeded')
        self.worker.ack_failure = None
        self.projection.reconcile()
        self.assertEqual(self.versions.get_revision_job('owner',task_id)['status'],'succeeded')
        self.assertEqual(self.start(),task_id)
        self.assertEqual(len(self.versions.list_versions('owner',self.parent_task)),2)

    def test_concurrent_start_reserves_only_one_immutable_job(self):
        barrier = threading.Barrier(2)
        health = self.worker.health
        def concurrent_health():
            barrier.wait(timeout=3)
            return health()
        with patch.object(self.worker,'health',side_effect=concurrent_health):
            with ThreadPoolExecutor(max_workers=2) as executor:
                rows = list(executor.map(lambda _:self.projection.prepare_revision('owner',self.preview['intent']['intent_id']),range(2)))
        self.assertEqual(rows[0]['id'],rows[1]['id'])
        self.assertEqual(rows[0]['job'],rows[1]['job'])
        self.assertEqual(len(self.versions.active_revision_jobs()),1)

    def test_reserved_commit_response_loss_is_recovered_without_changing_input(self):
        execute = self.database._execute
        lost = [True]
        def response_lost(query):
            execute(query)
            if lost[0] and query.startswith('INSERT INTO scientific_report_revision_jobs'):
                lost[0] = False
                raise OSError('Committed binding response lost')
        with patch.object(self.database,'_execute',side_effect=response_lost):
            self.assertEqual(self.client.post(self.url,json={}).status_code,503)
        binding = self.versions.active_revision_jobs()[0]
        self.assertEqual(self.worker.put_count,1)
        self.assertEqual(self.start(),binding['task_id'])
        self.assertEqual(self.projection.get(binding['task_id'])['job'],binding['job'])

    def test_rejection_binding_update_failure_recovers_and_releases_pin(self):
        self.worker.reject = 429
        update = self.versions.update_revision_job
        failed = [True]
        def fail_terminal(owner,task_id,status,**values):
            if status == 'rejected' and failed[0]:
                failed[0] = False
                raise OSError('Lost terminal write')
            return update(owner,task_id,status,**values)
        with patch.object(self.versions,'update_revision_job',side_effect=fail_terminal):
            response = self.client.post(self.url,json={})
        self.assertEqual(response.status_code,503,response.text)
        task_id = response.json()['detail']['task_id']
        self.assertEqual(self.projection.get(task_id)['dispatch'],'rejected')
        self.projection.reconcile()
        self.assertEqual(self.versions.get_revision_job('owner',task_id)['status'],'rejected')
        self.now += timedelta(seconds=180)
        self.assertEqual(self.versions.cleanup_expired()['cleared_snapshots'],1)
        self.assertEqual(self.client.post(self.url,json={}).status_code,429)
        self.assertEqual(self.worker.put_count,2)

    def test_cancel_before_worker_acceptance_prevents_dispatch_and_cancels_late_put(self):
        row = self.projection.prepare_revision('owner',self.preview['intent']['intent_id'])
        task_id = row['id']
        response = self.client.post('/scientific-research/'+task_id+'/cancel')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['state'],'cancel_requested')
        self.assertEqual(self.start(),task_id)
        self.projection.reconcile()
        self.assertEqual(self.worker.put_count,1)
        # A pre-existing network request can arrive after cancellation's GET404.
        job = json.loads(row['job'])
        self.worker.put_revision(job,self.versions.revision_materials('owner',task_id))
        self.projection.reconcile()
        self.assertEqual(self.worker.get(task_id)['state'],'cancelled')
        self.assertEqual(self.versions.get_revision_job('owner',task_id)['status'],'cancelled')
        self.assertEqual(self.start(),task_id)
        self.assertEqual(self.worker.put_count,2)

    def test_cancel_confirmed_absent_only_after_deadline_and_publish_boundary_is_preserved(self):
        row = self.projection.prepare_revision('owner',self.preview['intent']['intent_id'])
        task_id = row['id']
        self.client.post('/scientific-research/'+task_id+'/cancel')
        job = json.loads(row['job'])
        after = (datetime.fromisoformat(job['execution_deadline_at'].replace('Z','+00:00'))+timedelta(seconds=1)).isoformat().replace('+00:00','Z')
        with patch('scientific_agent.pi_projection.utc_now',return_value=after):
            response = self.client.get('/scientific-research/'+task_id)
        self.assertEqual(response.json()['state'],'cancelled')
        self.assertEqual(self.worker.put_count,1)
        self.assertEqual(self.versions.get_revision_job('owner',task_id)['status'],'cancelled')

    def test_cancel_after_validation_keeps_candidate_publication(self):
        task_id = self.start()
        self.worker.candidate(task_id)
        response = self.client.post('/scientific-research/'+task_id+'/cancel')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['state'],'publishing')
        self.projection.reconcile()
        self.assertEqual(self.worker.get(task_id)['state'],'succeeded')

    def test_corrupt_reserved_job_cannot_dispatch_or_change_parent(self):
        row = self.projection.prepare_revision('owner',self.preview['intent']['intent_id'])
        self.database._execute("UPDATE scientific_report_revision_jobs SET request_sha256='"+'0'*64+"' WHERE task_id='"+row['id']+"'")
        response = self.client.post(self.url,json={})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['detail']['code'],'revision_job_integrity_error')
        self.assertEqual(self.worker.put_count,1)
        self.assertEqual(self.database.get_report(self.parent_task)['content'],self.REPORT)

    def test_cancellation_recovers_late_definitive_worker_rejection(self):
        row = self.projection.prepare_revision('owner',self.preview['intent']['intent_id'])
        self.versions.update_revision_job('owner',row['id'],'cancel_requested')
        get = self.worker.get
        def late_rejection(task_id):
            if task_id == row['id']:
                raise WorkerError(429,'user_busy','Recorded worker rejection')
            return get(task_id)
        with patch.object(self.worker,'get',side_effect=late_rejection):
            self.projection.reconcile()
        self.assertEqual(self.versions.get_revision_job('owner',row['id'])['status'],'rejected')
        self.assertEqual(self.projection.get(row['id'])['dispatch'],'rejected')
        self.assertEqual(self.worker.put_count,1)

    def test_candidate_quota_is_reserved_before_any_model_dispatch(self):
        original = dict(owner_version_count=self.versions.owner_version_count,
                        owner_version_bytes=self.versions.owner_version_bytes,
                        global_version_bytes=self.versions.global_version_bytes)
        for limit,value in (('owner_version_count',1),
                            ('owner_version_bytes',len(self.REPORT.encode())+2*1024*1024-1),
                            ('global_version_bytes',len(self.REPORT.encode())+2*1024*1024-1)):
            with self.subTest(limit=limit):
                setattr(self.versions,limit,value)
                response = self.client.post(self.url,json={})
                self.assertEqual(response.status_code,429,response.text)
                self.assertEqual(response.json()['detail']['code'],'version_quota_exceeded')
                self.assertEqual(self.versions.active_revision_jobs(),[])
                self.assertEqual(self.worker.put_count,1)
                setattr(self.versions,limit,original[limit])

    def test_revision_and_research_identifiers_use_separate_namespaces(self):
        parent = self.versions.ensure_base('pi',str(uuid.uuid4()),self.REPORT,'Synthetic review')
        snapshot = self.versions.save_snapshot('pi',parent['version_id'],self.items,self.artifacts,'namespace-snapshot')
        intent = self.versions.prepare_intent('pi',parent['version_id'],parent['report_sha256'],
            self.section['section_id'],self.section['sha256'],'Explain limits.','namespace-intent',snapshot['snapshot_id'])
        revision = self.projection.prepare_revision('pi',intent['intent_id'])
        research = self.projection.prepare(dict(topic='Independent research',user_id='revision',engine='pi',
            mode='review',timeout_seconds=600),'',intent['intent_id'])
        self.assertNotEqual(revision['id'],research['id'])
        self.assertEqual(self.projection.get(revision['id'])['user_id'],'pi')
        self.assertEqual(self.projection.get(research['id'])['user_id'],'revision')


if __name__ == '__main__':
    unittest.main()
