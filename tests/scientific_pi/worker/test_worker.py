"""Worker tests run without model credentials: python -m unittest discover -s tests/scientific_pi/worker -v."""
import concurrent.futures
import importlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from scientific_agent.pi_contracts import ContractError, ResearchJob, input_digest, parse_timestamp, utc_now, result_digest
from pi_worker.app import WorkerSettings, create_app
from pi_worker.store import QueueStore
from pi_worker.scheduler import InstanceLock, Scheduler
from pi_worker.artifacts import FILES, safe_read, validate_and_copy, artifact_bytes


def job(user='u', topic='Evidence question'):
    value = dict(schema_version='1',task_id=str(uuid.uuid4()),topic=topic,user_id=user,engine='pi',mode='review',timeout_seconds=1800,created_at=utc_now(),execution_deadline_at=(parse_timestamp(utc_now())+timedelta(seconds=1800)).isoformat().replace('+00:00','Z'),runtime_revision='fixture',skill_bundle_revision='fixture')
    value['input_digest'] = input_digest(value)
    return value


def valid(workspace):
    return dict(validator_version='fixture',valid=True,checks=[],errors=[],warnings=[])


def outputs(workspace, payload):
    for identifier,(name,_) in FILES.items():
        if identifier=='validation':
            continue
        (workspace/name).write_text('# Evidence insufficient\n' if identifier=='report' else ('[]' if identifier=='references' else ''),encoding='utf-8')
    provenance = dict(model='fixture',pi_version='fixture',node_version='fixture',image_digest='sha256:'+'0'*64,skill_bundle_revision=payload['skill_bundle_revision'],runtime_revision=payload['runtime_revision'],retrieval_started_at=utc_now(),retrieval_finished_at=utc_now(),token_usage=None)
    (workspace/'provenance.json').write_text(json.dumps(provenance),encoding='utf-8')


class FakeRuntime:
    ready=True
    capabilities={'fixture':True}
    def execute(self, payload, workspace, event_sink, cancel_event):
        event_sink({'stage':'write'})
        outputs(workspace,payload)
    def recover(self, uids=()):
        return True


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = QueueStore(self.root/'control'/'queue.sqlite')
    def tearDown(self):
        self.temp.cleanup()
    def claim(self,payload=None):
        payload = payload or job()
        self.store.submit(ResearchJob.from_dict(payload))
        return self.store.claim()
    def commit(self,payload=None):
        claim=self.claim(payload)
        workspace=self.root/'fixture-output'
        workspace.mkdir()
        outputs(workspace,claim['job'])
        result,path=validate_and_copy(workspace,self.root/'results',claim['job'],claim['snapshot']['attempt'],valid)
        snap=self.store.publish(claim['snapshot']['id'],claim['snapshot']['attempt'],claim['token'],result,path)
        return claim,result,path,snap
    def test_atomic_user_admission_and_replay_conflict(self):
        requests=[job() for _ in range(8)]
        def submit(payload):
            try:
                return self.store.submit(ResearchJob.from_dict(payload))[0]['id']
            except ContractError as exc:
                return exc.status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(submit,requests))
        self.assertEqual(results.count(429),7)
        winner=next(p for p in requests if p['task_id'] in results)
        snap,new=self.store.submit(ResearchJob.from_dict(winner))
        self.assertFalse(new)
        changed=dict(winner,topic='Different')
        changed['input_digest']=input_digest(changed)
        with self.assertRaises(ContractError) as ctx:
            self.store.submit(ResearchJob.from_dict(changed))
        self.assertEqual(ctx.exception.status_code,409)
    def test_stale_attempt_recovery_and_persistence(self):
        first=self.claim()
        self.assertIsNone(first['snapshot']['stage'])
        self.store.stage(first['snapshot']['id'],1,first['token'],'write')
        other=QueueStore(self.store.path)
        other.recover()
        second=other.claim()
        self.assertEqual(second['snapshot']['attempt'],2)
        self.assertIsNone(second['snapshot']['stage'])
        with self.assertRaises(ContractError) as ctx:
            self.store.stage(first['snapshot']['id'],1,first['token'],'write')
        self.assertEqual(ctx.exception.code,'stale_attempt')
        other.recover()
        self.assertEqual(other.get(first['snapshot']['id'])['state'],'failed')
    def test_lease_expiry_fences_late_attempt(self):
        claim=self.claim()
        with self.store._tx() as db:
            db.execute('UPDATE tasks SET lease_until=?',('2000-01-01T00:00:00Z',))
        with self.assertRaises(ContractError):
            self.store.renew(claim['snapshot']['id'],1,claim['token'])
        # Recovery only follows process-tree confirmation by scheduler.
        self.store.recover()
        self.assertEqual(self.store.claim()['snapshot']['attempt'],2)
    def test_cancel_before_publish_wins(self):
        claim=self.claim()
        task=claim['snapshot']['id']
        self.assertEqual(self.store.cancel(task)['state'],'cancel_requested')
        self.assertEqual(self.store.publish(task,1,claim['token'],{'result_digest':'x'},self.root)['state'],'cancelled')
        with self.assertRaises(ContractError):
            self.store.stage(task,1,claim['token'],'write')
    def test_publishing_commit_ack_and_deadline(self):
        claim,result,path,snap=self.commit()
        task=snap['id']
        self.assertEqual(snap['status'],'running')
        self.assertEqual(self.store.cancel(task),snap)
        # Simulate the overall execution deadline passing during publication.
        updated=self.store.get(task)
        with self.store._tx() as db:
            updated['execution_deadline_at']='2000-01-01T00:00:00Z'
            db.execute('UPDATE tasks SET snapshot=? WHERE id=?',(json.dumps(updated),task))
        self.store.expire_queued()
        self.store.recover()
        self.assertEqual(self.store.get(task)['state'],'publishing')
        with self.assertRaises(ContractError):
            self.store.ack(task,2,result['result_digest'],task)
        final=self.store.ack(task,1,result['result_digest'],task)
        self.assertEqual(final['status'],'done')
        self.assertEqual(self.store.ack(task,1,result['result_digest'],task),final)
        self.assertEqual(QueueStore(self.store.path).result(task),result)
        self.assertEqual(result_digest(result),result['result_digest'])
    def test_queued_cancel_and_deadline(self):
        payload=job()
        self.store.submit(ResearchJob.from_dict(payload))
        self.assertEqual(self.store.cancel(payload['task_id'])['state'],'cancelled')
        self.assertIsNone(self.store.claim())
        payload=job()
        self.store.submit(ResearchJob.from_dict(payload))
        with self.store._tx() as db:
            snap=json.loads(db.execute('SELECT snapshot FROM tasks WHERE id=?',(payload['task_id'],)).fetchone()[0])
            snap['execution_deadline_at']='2000-01-01T00:00:00Z'
            db.execute('UPDATE tasks SET snapshot=? WHERE id=?',(json.dumps(snap),payload['task_id']))
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get(payload['task_id'])['state'],'timed_out')
    def test_manifest_whitelist_integrity_and_expiry(self):
        claim,result,path,snap=self.commit()
        task=snap['id']
        data,media=artifact_bytes(self.store,task,'report')
        self.assertEqual(data.decode(),result['report_markdown'])
        self.assertEqual(media,'text/markdown')
        manifest=json.loads(artifact_bytes(self.store,task,'manifest')[0])
        self.assertNotIn('manifest',[a['artifact_id'] for a in manifest['artifacts']])
        with self.assertRaises(ContractError):
            artifact_bytes(self.store,task,'provenance')
        report=path/'report.md'
        report.chmod(0o600)
        report.write_text('tampered',encoding='utf-8')
        with self.assertRaises(ContractError) as ctx:
            artifact_bytes(self.store,task,'report')
        self.assertEqual(ctx.exception.code,'artifact_corrupt')
        self.store.ack(task,1,result['result_digest'],task)
        with self.store._tx() as db:
            db.execute('UPDATE tasks SET expires=?',('2000-01-01T00:00:00Z',))
        self.store.cleanup_expired()
        self.assertFalse(path.exists())
        with self.assertRaises(ContractError) as ctx:
            artifact_bytes(self.store,task,'report')
        self.assertEqual(ctx.exception.status_code,410)
        self.assertEqual(self.store.get(task)['state'],'succeeded')
        self.assertEqual(self.store.result(task)['report_markdown'],result['report_markdown'])
    def test_paths_symlinks_and_malicious_manifest(self):
        workspace=self.root/'output'
        workspace.mkdir()
        payload=job()
        outputs(workspace,payload)
        for path in ('../queue.sqlite','/etc/passwd','%2e%2e/secret','a\\b','C:/secret'):
            with self.assertRaises(ContractError):
                safe_read(workspace,path)
        (workspace/'manifest.json').write_text(json.dumps({'artifacts':[{'artifact_id':'report','path':'../secret'}]}))
        with self.assertRaises(ContractError):
            validate_and_copy(workspace,self.root/'results',payload,1,valid)
        (workspace/'manifest.json').unlink()
        target=workspace/'report.md'
        target.unlink()
        try:
            target.symlink_to(self.store.path)
        except OSError:
            self.skipTest('OS disallows unprivileged symlink creation')
        with self.assertRaises(ContractError):
            validate_and_copy(workspace,self.root/'results',payload,1,valid)
    def test_size_and_failed_validation(self):
        workspace=self.root/'output'
        workspace.mkdir()
        payload=job()
        outputs(workspace,payload)
        with self.assertRaises(ContractError) as ctx:
            validate_and_copy(workspace,self.root/'results',payload,1,lambda p:{'valid':False})
        self.assertEqual(ctx.exception.code,'validation_failed')
        (workspace/'report.md').write_bytes(b'x'*(2*1024*1024+1))
        with self.assertRaises(ContractError) as ctx:
            validate_and_copy(workspace,self.root/'results',payload,1,valid)
        self.assertEqual(ctx.exception.status_code,413)
    def test_instance_lock(self):
        first=InstanceLock(self.root/'instance.lock').acquire()
        second=InstanceLock(self.root/'instance.lock')
        try:
            with self.assertRaises(RuntimeError):
                second.acquire()
        finally:
            first.release()
        second.acquire().release()
    def test_fake_scheduler_lifecycle(self):
        claim=self.claim()
        scheduler=Scheduler(self.store,FakeRuntime(),self.root,valid)
        scheduler.run_claim(claim)
        self.assertEqual(self.store.get(claim['snapshot']['id'])['state'],'publishing')
    def test_recovery_refuses_unconfirmed_orphans(self):
        self.claim()
        scheduler=Scheduler(self.store,lambda *a:None,self.root,valid)
        scheduler.start()
        self.assertTrue(scheduler.degraded)
        self.assertIsNone(scheduler.thread)
    def test_runtime_cancel_and_unstoppable_process(self):
        claim=self.claim()
        started=threading.Event()
        class Blocking:
            def execute(inner,payload,workspace,sink,cancel):
                started.set()
                cancel.wait(2)
        scheduler=Scheduler(self.store,Blocking(),self.root,valid)
        thread=threading.Thread(target=scheduler.run_claim,args=(claim,))
        thread.start()
        self.assertTrue(started.wait(1))
        self.store.cancel(claim['snapshot']['id'])
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.store.get(claim['snapshot']['id'])['state'],'cancelled')
    def test_http_auth_status_codes_and_no_factory_io(self):
        settings=WorkerSettings(self.root/'app','secret',start_scheduler=False)
        app=create_app(settings,self.store,FakeRuntime(),valid)
        self.assertFalse((self.root/'app').exists())
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/health').status_code,401)
            headers={'Authorization':'Bearer secret'}
            payload=job()
            endpoint='/v1/tasks/'+payload['task_id']
            self.assertEqual(client.put(endpoint,json=payload,headers=headers).status_code,201)
            self.assertEqual(client.put(endpoint,json=payload,headers=headers).status_code,200)
            second=job()
            self.assertEqual(client.put('/v1/tasks/'+second['task_id'],json=second,headers=headers).status_code,429)
            changed=dict(payload,topic='Changed')
            changed['input_digest']=input_digest(changed)
            self.assertEqual(client.put(endpoint,json=changed,headers=headers).status_code,409)
            self.assertEqual(client.put(endpoint,content=b'x'*16385,headers=headers).status_code,413)
            self.assertEqual(client.get(endpoint+'/result',headers=headers).status_code,409)
            self.assertEqual(client.get('/v1/tasks/not-uuid',headers=headers).status_code,422)
            self.assertEqual(client.get('/v1/health',headers=headers).status_code,200)
    def test_persistent_uid_allocation_and_pagination(self):
        first=self.claim(job('first'))
        uid=first['job']['_runtime_uid']
        self.assertEqual(uid,200000)
        self.store.recover()
        second=self.store.claim()
        self.assertGreater(second['job']['_runtime_uid'],uid)
        self.assertNotIn('_runtime_uid',ResearchJob.from_dict(job()).to_dict())
        self.store.finish_attempt(second['snapshot']['id'],2,second['token'],'failed','fixture')
        a=self.commit(job('a'))
        # commit helper uses a fixed fixture directory; remove it between jobs.
        import shutil
        shutil.rmtree(self.root/'fixture-output')
        b=self.commit(job('b'))
        page=self.store.pending(limit=1)
        self.assertEqual(len(page['items']),1)
        self.assertIsNotNone(page['next_cursor'])
        last=self.store.pending(page['next_cursor'],1)
        self.assertEqual(len(last['items']),1)
        self.assertIsNone(last['next_cursor'])
        self.assertNotEqual(page['items'][0]['id'],last['items'][0]['id'])
        with self.assertRaises(ContractError):
            self.store.pending('invalid')
        self.assertEqual(QueueStore(self.store.path).execution_uids(),[200000,200001,200002,200003])

    def test_unstoppable_runner_blocks_replacement(self):
        claim=self.claim()
        started=threading.Event()
        release=threading.Event()
        class Unstoppable:
            def execute(inner,payload,workspace,sink,cancel):
                started.set()
                release.wait(2)
        scheduler=Scheduler(self.store,Unstoppable(),self.root,valid,cancel_grace=.05)
        thread=threading.Thread(target=scheduler.run_claim,args=(claim,))
        thread.start()
        self.assertTrue(started.wait(1))
        self.store.cancel(claim['snapshot']['id'])
        thread.join(1)
        try:
            self.assertTrue(scheduler.degraded)
            self.assertEqual(self.store.get(claim['snapshot']['id'])['state'],'cancel_requested')
            self.assertIsNone(self.store.claim())
        finally:
            release.set()
            scheduler._runner.join(1)

    def test_validator_observes_copy_and_missing_required_output(self):
        workspace=self.root/'output'
        workspace.mkdir()
        payload=job()
        outputs(workspace,payload)
        def mutate_original(staged):
            original=(workspace/'report.md').read_text()
            (workspace/'report.md').write_text('changed')
            self.assertEqual((staged/'report.md').read_text(),original)
            return valid(staged)
        expected_report=(workspace/'report.md').read_bytes().decode('utf-8')
        result,path=validate_and_copy(workspace,self.root/'results',payload,1,mutate_original)
        self.assertEqual(result['report_markdown'],expected_report)
        (workspace/'claims.jsonl').unlink()
        with self.assertRaises(ContractError):
            validate_and_copy(workspace,self.root/'results',payload,2,valid)

    def test_real_research_validator_zero_evidence(self):
        workspace=self.root/'output'
        workspace.mkdir()
        payload=job()
        outputs(workspace,payload)
        (workspace/'searches.jsonl').write_text(json.dumps(dict(source='fixture',query='empty evidence',searched_at=utc_now(),status='success',result_count=0,error=None))+'\n',encoding='utf-8')
        (workspace/'report.md').write_text('# Scope\nControlled fixture.\n# Methods\nOne source returned zero records.\n# Findings\nInsufficient evidence.\n# Limitations\nNo scientific conclusion is supported.\n# References\nNone.\n',encoding='utf-8')
        result,path=validate_and_copy(workspace,self.root/'results',payload,1)
        self.assertTrue(result['validation']['valid'])
        self.assertTrue((path/'validation.json').exists())
        self.assertEqual(result_digest(result),result['result_digest'])

    def test_definitive_rejection_tombstone_survives_capacity_and_restart(self):
        accepted=job()
        rejected=job()
        self.store.submit(accepted)
        with self.assertRaises(ContractError) as ctx:
            self.store.submit(rejected)
        self.assertEqual(ctx.exception.status_code,429)
        self.store.cancel(accepted['task_id'])
        reopened=QueueStore(self.store.path)
        for operation in (lambda:reopened.get(rejected['task_id']),lambda:reopened.submit(rejected)):
            with self.assertRaises(ContractError) as ctx:
                operation()
            self.assertEqual(ctx.exception.code,'user_busy')
        self.assertIsNone(reopened.claim())
        # A conflicting payload cannot mark an accepted identity rejected.
        changed=dict(accepted,topic='different')
        changed['input_digest']=input_digest(changed)
        with self.assertRaises(ContractError):
            reopened.submit(changed)
        self.assertEqual(reopened.get(accepted['task_id'])['state'],'cancelled')
        revision_rejected=job('other')
        with self.assertRaises(ContractError):
            reopened.reject(revision_rejected,ContractError('configuration_missing','Pinned revision mismatch',422))
        with self.assertRaises(ContractError) as ctx:
            QueueStore(self.store.path).submit(revision_rejected)
        self.assertEqual(ctx.exception.status_code,422)

    def test_declared_full_text_is_private_bounded_and_hashed(self):
        workspace=self.root/'output'
        workspace.mkdir()
        payload=job()
        outputs(workspace,payload)
        (workspace/'acquired').mkdir()
        (workspace/'acquired'/'paper.txt').write_bytes(b'controlled full text')
        (workspace/'papers.jsonl').write_text(json.dumps(dict(paper_id='p1',full_text_path='acquired/paper.txt'))+'\n',encoding='utf-8')
        def inspect_full_text(staged):
            self.assertEqual((staged/'acquired'/'paper.txt').read_bytes(),b'controlled full text')
            return valid(staged)
        result,path=validate_and_copy(workspace,self.root/'results',payload,1,inspect_full_text)
        self.assertEqual((path/'acquired'/'paper.txt').read_bytes(),b'controlled full text')
        self.assertEqual(result['provenance']['acquired_full_text'][0]['size'],20)
        self.assertEqual(result_digest(result),result['result_digest'])
        self.assertNotIn('acquired/paper.txt',[a['path'] for a in result['artifacts']])
        (workspace/'papers.jsonl').write_text(json.dumps(dict(paper_id='p1',full_text_path='../control/queue.sqlite'))+'\n',encoding='utf-8')
        with self.assertRaises(ContractError):
            validate_and_copy(workspace,self.root/'results',payload,2,valid)

    def test_runtime_reports_unverified_process_tree(self):
        claim=self.claim()
        class UnsafeRuntime:
            def execute(inner,*args):
                error=RuntimeError('not confirmed stopped')
                error.code='process_safety_unverified'
                raise error
        scheduler=Scheduler(self.store,UnsafeRuntime(),self.root,valid)
        scheduler.run_claim(claim)
        self.assertTrue(scheduler.degraded)
        self.assertEqual(self.store.get(claim['snapshot']['id'])['state'],'running')
        self.assertIsNone(self.store.claim())

    def test_retention_cleanup_preserves_publishing_and_task_history(self):
        payload=job('old')
        self.store.submit(payload)
        snap=self.store.cancel(payload['task_id'])
        snap['finished_at']='2000-01-01T00:00:00Z'
        with self.store._tx() as db:
            db.execute('UPDATE tasks SET snapshot=? WHERE id=?',(json.dumps(snap),payload['task_id']))
        old_workspace=self.root/'tasks'/payload['task_id']
        old_workspace.mkdir(parents=True)
        (old_workspace/'diagnostic').write_text('controlled')
        claim,result,publishing_path,published=self.commit(job('publisher'))
        with self.store._tx() as db:
            db.execute('UPDATE tasks SET expires=? WHERE id=?',('2000-01-01T00:00:00Z',published['id']))
        orphan=self.root/'results'/'.validate-old'
        orphan.mkdir()
        os.utime(orphan,(1,1))
        scheduler=Scheduler(self.store,FakeRuntime(),self.root,valid,retention_days=30)
        cleanup=self.store.cleanup_expired
        def cleanup_once(*args):
            cleanup(*args)
            scheduler.stop_event.set()
        with patch.object(self.store,'cleanup_expired',side_effect=cleanup_once) as maintenance:
            scheduler._loop()
            maintenance.assert_called_once_with(self.root,30)
        self.assertFalse(old_workspace.exists())
        self.assertFalse(orphan.exists())
        self.assertTrue(publishing_path.exists())
        self.assertEqual(self.store.get(payload['task_id'])['state'],'cancelled')
        self.assertEqual(self.store.get(published['id'])['state'],'publishing')

    @unittest.skipUnless(os.name == 'posix' and hasattr(os,'mkfifo'), 'POSIX FIFO required')
    def test_fifo_artifact_is_rejected_without_blocking(self):
        import subprocess
        import sys
        workspace=self.root/'fifo-output'
        workspace.mkdir()
        os.mkfifo(workspace/'report.md')
        script = (
            'import sys\n'
            'from pi_worker.artifacts import safe_read\n'
            'from scientific_agent.pi_contracts import ContractError\n'
            'try:\n'
            '    safe_read(sys.argv[1], "report.md")\n'
            'except ContractError as exc:\n'
            '    assert exc.code == "invalid_artifact", exc.code\n'
            'else:\n'
            '    raise AssertionError("FIFO accepted as artifact")\n'
        )
        # A regressed blocking open is killed by run(timeout), so it cannot hang
        # the test process or leave a reader waiting for a FIFO writer.
        subprocess.run([sys.executable,'-c',script,str(workspace)],cwd=Path(__file__).resolve().parents[3],check=True,timeout=3)

    def test_fail_closed(self):
        with self.assertRaises(RuntimeError):
            create_app(WorkerSettings(self.root,''))
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(RuntimeError):
                create_app()
        app=create_app(WorkerSettings(self.root/'app','secret',start_scheduler=False),self.store)
        with TestClient(app) as client:
            payload=job()
            response=client.put('/v1/tasks/'+payload['task_id'],json=payload,headers={'Authorization':'Bearer secret'})
            self.assertEqual(response.status_code,503)


if __name__=='__main__':
    unittest.main()
