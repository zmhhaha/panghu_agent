"""Fixed-input revision admission, recovery and immutable publication fixtures."""
import base64
import concurrent.futures
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from fastapi.testclient import TestClient

from scientific_agent.pi_contracts import (ContractError, ResearchJob, canonical_json,
                                         input_digest, parse_timestamp, utc_now)
from scientific_agent.pi_revision_contracts import (RevisionJob, revision_input_digest,
                                                   revision_material_digest)
from scientific_agent.pi_sections import index_sections
from pi_worker.app import WorkerSettings, create_app
from pi_worker.artifacts import artifact_bytes, validate_and_copy
from pi_worker.scheduler import Scheduler
from pi_worker.store import QueueStore
from pi_worker.runtime import PiRuntime, RuntimeSettings
from pi_worker.research_tools import BUNDLE_REVISION


def revision_fixture(user='fixture-user', full_text=False):
    report = ('# Fixture 科研\r\nUnchanged opening.\r\n\r\n'
              '## Findings\r\n<!-- claim:c1 -->\r\nSynthetic finding [1].\r\n\r\n'
              '## Limitations\r\nUnchanged ending.\r\n')
    acquired = dict(available=True, obtained_at='2026-10-25T00:00:00Z')
    absent = dict(available=False, obtained_at=None)
    paper = dict(paper_id='p1', title='Synthetic paper', authors=['Fixture Author'], year=2025,
                 doi=None, pmid=None, url='https://example.org/fixture', sources=['fixture'],
                 retrieved_at='2026-10-25T00:00:00Z', abstract='Synthetic finding',
                 full_text_path='full-text/p1.txt' if full_text else None,
                 availability=dict(metadata=acquired, abstract=acquired,
                                   full_text=acquired if full_text else absent),
                 verification=dict(status='unknown', checked_at=None, source=None, error=None))
    records = dict(papers=[paper],
                   evidence=[dict(evidence_id='e1', paper_id='p1', finding='Synthetic finding',
                                  method='Fixture', limitations='Recorded fixture only',
                                  access_level='full_text' if full_text else 'abstract',
                                  locator='page 1' if full_text else 'abstract')],
                   claims=[dict(claim_id='c1', marker='<!-- claim:c1 -->', evidence_ids=['e1'],
                                assessment='supported', explanation='Recorded fixture only')],
                   references=[dict(n=1, paper_id='p1')])
    artifacts = {key:(json.dumps(value) if key == 'references' else
                     ''.join(json.dumps(row)+'\n' for row in value)).encode('utf-8')
                 for key,value in records.items()}
    all_files = dict(artifacts, report=report.encode('utf-8'))
    items = [dict(artifact_id=key, media_type=('text/markdown' if key == 'report' else
                 'application/json' if key == 'references' else 'application/x-ndjson'),
                  size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
             for key,raw in all_files.items()]
    material = dict(report=report, items=items,
                    artifacts={key:base64.b64encode(raw).decode('ascii') for key,raw in artifacts.items()})
    index = index_sections(report)
    section = next(row for row in index['sections'] if row['title'] == 'Findings')
    created = utc_now()
    job = dict(schema_version='1', task_kind='revision', task_id=str(uuid.uuid4()),
               topic='Synthetic fixture revision', user_id=user, engine='pi', mode='review',
               timeout_seconds=1800, created_at=created,
               execution_deadline_at=(parse_timestamp(created)+timedelta(seconds=1800)).isoformat(),
               runtime_revision='fixture', skill_bundle_revision='fixture',
               intent_id=str(uuid.uuid4()), parent_version_id=str(uuid.uuid4()),
               root_report_id=str(uuid.uuid4()), base_sha256=index['report_sha256'],
               section_id=section['section_id'], section_sha256=section['sha256'],
               instruction='Explain the limitations of the recorded result.',
               input_sha256=revision_material_digest(material), material_policy='recorded_evidence_only')
    job['input_digest'] = revision_input_digest(job)
    output = dict(schema_version='1', section_markdown=(
        '## Findings\r\n<!-- claim:revised1 -->\r\n'
        'The recorded synthetic finding is limited to the supplied evidence [1].\r\n\r\n'),
        claims=[dict(claim_id='revised1', marker='<!-- claim:revised1 -->', evidence_ids=['e1'],
                     assessment='supported', explanation='Only the recorded synthetic finding is used.')])
    return job, material, output


def research_job(user):
    value, _, _ = revision_fixture(user)
    keys = set(ResearchJob.__dataclass_fields__)
    value = {key:item for key,item in value.items() if key in keys}
    value['input_digest'] = input_digest(value)
    return value


def provenance(workspace, job):
    value = dict(model='fixture', pi_version='fixture', node_version='fixture',
                 image_digest='sha256:'+'0'*64, skill_bundle_revision=job['skill_bundle_revision'],
                 runtime_revision=job['runtime_revision'], retrieval_started_at=utc_now(),
                 retrieval_finished_at=utc_now())
    (workspace/'provenance.json').write_text(json.dumps(value), encoding='utf-8')


class RevisionRuntime:
    ready = True
    capabilities = dict(revision_job_versions=['1'], revision_input_scope='recorded_evidence_only',
                        max_revision_input_bytes=16*1024*1024)

    def __init__(self, output=None):
        self.output = output

    def execute(self, job, workspace, event_sink, cancel):
        event_sink({'stage':'write'})
        (workspace/'revision-output.json').write_text(json.dumps(self.output), encoding='utf-8')
        # Child-controlled material is deliberately unrelated to the queue copy.
        (workspace/'input-report.md').write_text('Untrusted altered input', encoding='utf-8')
        provenance(workspace, job)


class RevisionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = QueueStore(self.root/'control'/'queue.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def input_count(self):
        with self.store._tx() as db:
            return db.execute('SELECT COUNT(*) FROM revision_inputs').fetchone()[0]

    def test_private_input_persists_through_restart_retry_and_unique_uid(self):
        job, material, _ = revision_fixture()
        snapshot, created = self.store.submit_revision(job, material)
        self.assertTrue(created)
        self.assertNotIn('input', snapshot)
        self.assertEqual(snapshot['task_kind'], 'revision')
        first = self.store.claim()
        self.assertEqual(first['job']['_revision_input'], material)
        reopened = QueueStore(self.store.path)
        reopened.recover()
        second = reopened.claim()
        self.assertEqual(second['snapshot']['attempt'], 2)
        self.assertEqual(second['job']['_revision_input'], material)
        self.assertNotEqual(first['job']['_runtime_uid'],second['job']['_runtime_uid'])
        with self.assertRaises(ContractError):
            reopened.renew(job['task_id'],1,first['token'])
        reopened.finish_attempt(job['task_id'],2,second['token'],'failed','fixture')
        self.assertEqual(self.input_count(),0)

    def test_research_and_revision_share_atomic_active_user_limit(self):
        job, material, _ = revision_fixture()
        regular = research_job(job['user_id'])
        barrier = threading.Barrier(2)
        def submit(revision):
            other = QueueStore(self.store.path)
            barrier.wait()
            try:
                return (other.submit_revision(job,material) if revision else other.submit(regular))[0]['id']
            except ContractError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit,[True,False]))
        self.assertEqual(results.count('user_busy'),1)
        self.assertEqual(self.input_count(),int(job['task_id'] in results))

    def test_global_byte_quota_is_atomic_across_connections(self):
        fixtures = [revision_fixture('user-'+str(i)) for i in range(4)]
        limit = max(len(canonical_json(material).encode('utf-8')) for _,material,_ in fixtures)
        barrier = threading.Barrier(4)
        def submit(fixture):
            other = QueueStore(self.store.path,revision_global_bytes=limit)
            barrier.wait()
            try:
                other.submit_revision(fixture[0],fixture[1])
                return 'accepted'
            except ContractError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit,fixtures))
        self.assertEqual(results.count('accepted'),1)
        self.assertEqual(results.count('revision_input_quota_exceeded'),3)
        self.assertEqual(self.input_count(),1)

    def test_owner_byte_quota_and_shared_queue_budget(self):
        job, material, _ = revision_fixture()
        small = QueueStore(self.store.path,revision_owner_bytes=1)
        with self.assertRaises(ContractError) as caught:
            small.submit_revision(job,material)
        self.assertEqual(caught.exception.code,'revision_input_quota_exceeded')
        self.assertEqual(self.input_count(),0)
        changed=dict(job,instruction='Changed request under a rejected task ID')
        changed['input_digest']=revision_input_digest(changed)
        with self.assertRaises(ContractError) as caught:
            self.store.submit_revision(changed,material)
        self.assertEqual(caught.exception.code,'task_conflict')
        self.store.submit(research_job('other-user'))
        job, material, _ = revision_fixture('next-user')
        with self.assertRaises(ContractError) as caught:
            self.store.submit_revision(job,material,max_queue=1)
        self.assertEqual(caught.exception.code,'queue_full')
        self.assertEqual(self.input_count(),0)

    def test_terminal_replay_keeps_tombstone_and_releases_input(self):
        job, material, _ = revision_fixture()
        self.store.submit_revision(job,material)
        cancelled = self.store.cancel(job['task_id'])
        self.assertEqual(self.input_count(),0)
        self.assertEqual(QueueStore(self.store.path).submit_revision(job,material),(cancelled,False))
        changed = dict(job,instruction='A different instruction')
        changed['input_digest'] = revision_input_digest(changed)
        with self.assertRaises(ContractError) as caught:
            self.store.submit_revision(changed,material)
        self.assertEqual(caught.exception.code,'task_conflict')

    def test_running_cancel_releases_only_after_process_stopped(self):
        job, material, _ = revision_fixture()
        self.store.submit_revision(job,material)
        claim = self.store.claim()
        self.assertEqual(self.store.cancel(job['task_id'])['state'],'cancel_requested')
        self.assertEqual(self.input_count(),1)
        self.store.finish_attempt(job['task_id'],1,claim['token'],'failed','cancelled')
        self.assertEqual(self.store.get(job['task_id'])['state'],'cancelled')
        self.assertEqual(self.input_count(),0)

    def test_corrupted_input_fails_without_execution(self):
        job, material, _ = revision_fixture()
        self.store.submit_revision(job,material)
        with self.store._tx() as db:
            db.execute("UPDATE revision_inputs SET payload='{}'")
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get(job['task_id'])['error_code'],'revision_input_corrupt')
        self.assertEqual(self.input_count(),0)

    def test_expired_queued_input_is_released(self):
        job, material, _ = revision_fixture()
        self.store.submit_revision(job,material)
        with self.store._tx() as db:
            snap=json.loads(db.execute('SELECT snapshot FROM tasks').fetchone()[0])
            snap['execution_deadline_at']='2000-01-01T00:00:00Z'
            db.execute('UPDATE tasks SET snapshot=?',(json.dumps(snap),))
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get(job['task_id'])['state'],'timed_out')
        self.assertEqual(self.input_count(),0)

    def test_scheduler_composes_candidate_and_keeps_external_bytes(self):
        job, material, output = revision_fixture(full_text=True)
        self.store.submit_revision(job,material)
        claim = self.store.claim()
        scheduler = Scheduler(self.store,RevisionRuntime(output),self.root)
        scheduler.run_claim(claim)
        self.assertFalse((self.root/'tasks'/job['task_id']/'attempts'/'1'/'workspace').exists())
        self.assertEqual(self.store.get(job['task_id'])['state'],'publishing')
        self.assertEqual(self.input_count(),0)
        result = self.store.result(job['task_id'])
        section = next(item for item in index_sections(material['report'])['sections']
                       if item['section_id'] == job['section_id'])
        original = material['report'].encode('utf-8')
        candidate = result['report_markdown'].encode('utf-8')
        self.assertEqual(candidate,original[:section['start_byte']] +
                         output['section_markdown'].encode('utf-8') + original[section['end_byte']:])
        self.assertFalse(result['provenance']['full_text_restored'])
        self.assertFalse(result['provenance']['retrieval_performed'])
        self.assertEqual(result['provenance']['acquired_full_text'],[])
        self.assertEqual(result['provenance']['revision']['intent_id'],job['intent_id'])
        for name in ('papers','evidence','references'):
            self.assertEqual(artifact_bytes(self.store,job['task_id'],name)[0],
                             base64.b64decode(material['artifacts'][name]))
        self.assertEqual(artifact_bytes(self.store,job['task_id'],'searches')[0],b'')
        self.assertEqual(artifact_bytes(self.store,job['task_id'],'screening')[0],b'')
        reopened=QueueStore(self.store.path)
        self.assertEqual(reopened.result(job['task_id']),result)
        final=reopened.ack(job['task_id'],1,result['result_digest'],job['task_id'])
        self.assertEqual(final['state'],'succeeded')

    def test_invalid_output_fails_without_rewriting_original(self):
        job, material, output = revision_fixture()
        output['section_markdown'] += 'Unregistered citation [99].\r\n'
        self.store.submit_revision(job,material)
        claim=self.store.claim()
        Scheduler(self.store,RevisionRuntime(output),self.root).run_claim(claim)
        self.assertFalse((self.root/'tasks'/job['task_id']/'attempts'/'1'/'workspace').exists())
        self.assertEqual(self.store.get(job['task_id'])['state'],'failed')
        with self.assertRaises(ContractError):
            self.store.result(job['task_id'])
        self.assertEqual(self.input_count(),0)

    def test_uncertain_process_safety_preserves_workspace_and_fencing(self):
        job, material, output=revision_fixture()
        self.store.submit_revision(job,material)
        claim=self.store.claim()
        class Unsafe(RevisionRuntime):
            def execute(self, job, workspace, event_sink, cancel):
                super().execute(job,workspace,event_sink,cancel)
                error=RuntimeError('Process termination cannot be confirmed')
                error.code='process_safety_unverified'
                raise error
        scheduler=Scheduler(self.store,Unsafe(output),self.root)
        scheduler.run_claim(claim)
        self.assertTrue(scheduler.degraded)
        self.assertTrue((self.root/'tasks'/job['task_id']/'attempts'/'1'/'workspace').exists())
        self.assertEqual(self.input_count(),1)
        self.assertIsNone(self.store.claim())

    def test_scheduler_cancel_and_timeout_discard_copies_without_result(self):
        for state in ('cancelled','timed_out'):
            with self.subTest(state=state):
                job, material, output=revision_fixture()
                self.store.submit_revision(job,material)
                claim=self.store.claim()
                store=self.store
                class Stop(RevisionRuntime):
                    def execute(self, job, workspace, event_sink, cancel):
                        super().execute(job,workspace,event_sink,cancel)
                        if state == 'cancelled':
                            store.cancel(job['task_id'])
                        else:
                            with store._tx() as db:
                                row=db.execute('SELECT snapshot FROM tasks WHERE id=?',(job['task_id'],)).fetchone()
                                value=json.loads(row[0])
                                value['execution_deadline_at']='2000-01-01T00:00:00Z'
                                db.execute('UPDATE tasks SET snapshot=? WHERE id=?',(json.dumps(value),job['task_id']))
                scheduler=Scheduler(self.store,Stop(output),self.root)
                scheduler.run_claim(claim)
                self.assertEqual(self.store.get(job['task_id'])['state'],state)
                self.assertFalse((self.root/'tasks'/job['task_id']/'attempts'/'1'/'workspace').exists())
                self.assertEqual(self.input_count(),0)
                with self.assertRaises(ContractError):
                    self.store.result(job['task_id'])

    def test_duplicate_output_keys_are_rejected(self):
        job, material, _ = revision_fixture()
        self.store.submit_revision(job,material)
        claim=self.store.claim()
        workspace=self.root/'candidate'
        workspace.mkdir()
        provenance(workspace,job)
        (workspace/'revision-output.json').write_text('{"schema_version":"1","schema_version":"2"}')
        with self.assertRaises(ContractError):
            validate_and_copy(workspace,self.root/'results',claim['job'],1)

    def test_http_capabilities_submission_replay_and_legacy_contract(self):
        job, material, output = revision_fixture()
        runtime=RevisionRuntime(output)
        app=create_app(WorkerSettings(self.root/'http','token',start_scheduler=False),self.store,runtime)
        headers={'Authorization':'Bearer token'}
        with TestClient(app) as client:
            health=client.get('/v1/health',headers=headers).json()
            self.assertEqual(health['capabilities']['revision_job_versions'],['1'])
            response=client.put('/v1/revisions/'+job['task_id'],json={'job':job,'input':material},headers=headers)
            self.assertEqual(response.status_code,201,response.text)
            runtime.ready=False
            self.assertEqual(client.put('/v1/revisions/'+job['task_id'],json={'job':job,'input':material},headers=headers).status_code,200)
            self.assertEqual(client.put('/v1/tasks/'+job['task_id'],json=job,headers=headers).status_code,422)
            self.assertEqual(client.post('/v1/tasks/'+job['task_id']+'/cancel',headers=headers).json()['state'],'cancelled')
            self.assertEqual(client.put('/v1/revisions/'+job['task_id'],json={'job':job,'input':material},headers=headers).status_code,200)

    def test_http_unsupported_runtime_auth_bounds_and_duplicate_json(self):
        job, material, _ = revision_fixture()
        class OldRuntime:
            ready=True
            capabilities={}
        app=create_app(WorkerSettings(self.root/'http-old','token',start_scheduler=False),self.store,OldRuntime())
        headers={'Authorization':'Bearer token'}
        path='/v1/revisions/'+job['task_id']
        with TestClient(app) as client:
            self.assertEqual(client.put(path,json={'job':job,'input':material},headers=headers).status_code,503)
            self.assertEqual(client.put(path,content=b'x'*20000).status_code,401)
            self.assertEqual(client.put('/v1/tasks/'+job['task_id'],content=b'x'*20000,headers=headers).status_code,413)
            self.assertEqual(client.put(path,content=b'x'*(16*1024*1024+16385),headers=headers).status_code,413)
            self.assertEqual(client.put(path,content='{"job":{},"job":{},"input":{}}',headers=headers).status_code,422)
        self.assertEqual(self.input_count(),0)

    def test_actual_adapter_selects_revision_profile_and_exposes_only_fixed_records(self):
        import sys
        job, material, _ = revision_fixture(full_text=True)
        repo=Path(__file__).resolve().parents[3]
        fake=Path(__file__).resolve().parents[1]/'runtime'/'fake_pi.py'
        skill=repo/'scientific_skills'/'research-procedure'/'SKILL.md'
        manifest=repo/'scientific_skills'/'manifest.json'
        job.update(runtime_revision='runtime-test',skill_bundle_revision=BUNDLE_REVISION)
        job['input_digest']=revision_input_digest(job)
        settings=RuntimeSettings(command=(sys.executable,str(fake),'success'),
                                 node_command=(sys.executable,str(fake),'node'),
                                 provider='fixture',model='fixture',fixture_mode=True,
                                 skill_paths=(skill,),skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
                                 bundle_manifest=manifest,bundle_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                                 skill_bundle_revision=BUNDLE_REVISION,runtime_revision='runtime-test')
        runtime=PiRuntime(settings)
        self.assertEqual(runtime.capabilities['revision_job_versions'],['1'])
        workspace=self.root/'actual-runtime'
        runtime.execute(dict(job,attempt=1,_revision_input=material),workspace,lambda event:None,threading.Event())
        self.assertEqual((workspace/'input-report.md').read_bytes(),material['report'].encode('utf-8'))
        for name,encoded in material['artifacts'].items():
            filename=name+('.json' if name=='references' else '.jsonl')
            self.assertEqual((workspace/'fixed-evidence'/filename).read_bytes(),base64.b64decode(encoded))
        self.assertFalse((workspace/'full-text').exists())
        prompt=(workspace/'task.md').read_text(encoding='utf-8')
        self.assertIn('revision-output.json',prompt)
        self.assertIn('No source text files have been restored',prompt)
        observed=json.loads((workspace/'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(observed['profile'],'scientific-fixed-evidence-revision-v1')
        self.assertEqual(observed['retrieval_interval_basis'],'not_performed_recorded_evidence_only')
        process=json.loads((workspace/'observed.json').read_text(encoding='utf-8'))
        self.assertNotIn('--skill',process['argv'])
        self.assertEqual(process['argv'][process['argv'].index('--tools')+1],'read,write')


if __name__ == '__main__':
    unittest.main()
