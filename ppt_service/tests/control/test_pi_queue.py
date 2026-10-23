"""Real Repository transactions: SQLite locally, isolated PostgreSQL schema in CI."""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from ppt_service.server.models import Base, Project, Job, JobEvent, ExecutionSlot, Checkpoint
from ppt_service.control.pi_queue import RepositoryQueue, public_error
from ppt_service.control.execution import Attempt, ControlError


class QueueTests(unittest.TestCase):
    def setUp(self):
        url = os.environ.get('PPT_PI_TEST_DATABASE_URL')
        self.schema = 'pi_queue_' + uuid.uuid4().hex
        self.admin = None
        if url:
            self.admin = create_engine(url)
            with self.admin.begin() as s:
                s.execute(text('CREATE SCHEMA ' + self.schema))
            self.engine = create_engine(url, connect_args={'options': '-csearch_path=' + self.schema})
        else:
            self.engine = create_engine('sqlite:///:memory:')
        self.addCleanup(self.cleanup)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.queue = RepositoryQueue(self.sessions)
        with self.sessions.begin() as s:
            p = Project(issuer='test', subject='owner', title='queue fixture', settings={}, external_model_ack=True)
            s.add(p); s.flush()
            j = Job(project_id=p.id, issuer=p.issuer, subject=p.subject, kind='plan_initial', stage='plan_initial', input_snapshot={'hash': 'a'*64})
            s.add(j); s.flush()
            p.active_job_id = j.id
            s.add(ExecutionSlot(id=1))
            self.jid, self.pid = j.id, p.id

    def cleanup(self):
        self.engine.dispose()
        if self.admin is not None:
            with self.admin.begin() as s:
                s.execute(text('DROP SCHEMA ' + self.schema + ' CASCADE'))
            self.admin.dispose()

    def claim(self):
        c = self.queue.claim_next()
        a = Attempt(c.job_id, self.pid, c.attempt, c.token, c.kind, {'hash':'a'*64}, 'correlation')
        return c, a

    def test_claim_build_attach_and_observed_stop(self):
        c, a = self.claim()
        built = self.queue.build(c)
        self.assertEqual(built.attempt.job_id, a.job_id)
        self.assertIsNone(self.queue.claim_next())
        self.queue.attach_execution(a)
        self.assertEqual(self.queue.heartbeat(a), 'running')
        self.assertEqual(self.queue.prove_stopped(a, True), 'validating')
        self.queue.finish_failed(a, 'preview_ready_timeout')
        with self.sessions() as s:
            j = s.get(Job, self.jid)
            self.assertEqual(j.status, 'failed')
            self.assertEqual(j.error_code, 'execution_interrupted')
            self.assertIsNone(s.get(ExecutionSlot, 1).job_id)
            self.assertEqual(s.scalars(select(JobEvent.type).where(JobEvent.job_id==j.id).order_by(JobEvent.seq)).all(), ['job.started','job.failed'])

    def test_prelaunch_permanent_failure_is_not_requeued_or_fake_stopped(self):
        c, _ = self.claim()
        self.queue.abandon_claim(c, 'confirmation_lineage_mismatch')
        self.assertIsNone(self.queue.claim_next())
        with self.sessions() as s:
            j, p = s.get(Job,self.jid), s.get(Project,self.pid)
            self.assertEqual(j.status, 'failed'); self.assertFalse(j.stopped)
            self.assertIsNone(p.active_job_id)
            self.assertEqual(s.scalars(select(JobEvent.type).order_by(JobEvent.seq)).all(), ['job.started', 'job.failed'])

    def test_stale_token_or_attempt_cannot_release_same_job(self):
        c, _ = self.claim()
        for bad in (replace(c,token=c.token+1), replace(c,attempt=c.attempt+1), replace(c,kind='generate')):
            with self.subTest(claim=bad), self.assertRaisesRegex(ControlError,'stale_claim'):
                self.queue.abandon_claim(bad, 'x')
        with self.sessions() as s:
            self.assertEqual(s.get(Job,self.jid).status,'starting')
            self.assertEqual(s.get(ExecutionSlot,1).job_id,self.jid)

    def test_slot_token_change_blocks_release(self):
        c, _ = self.claim()
        with self.sessions.begin() as s:
            s.get(ExecutionSlot,1).fencing_token += 1
        with self.assertRaisesRegex(ControlError,'stale_claim'):
            self.queue.abandon_claim(c,'x')

    def test_attached_writer_cannot_use_prelaunch_release(self):
        c, a = self.claim(); self.queue.attach_execution(a)
        with self.assertRaisesRegex(ControlError,'stale_claim'):
            self.queue.abandon_claim(c,'x')

    def test_stop_proof_requires_actual_boolean_true(self):
        _, a = self.claim(); self.queue.attach_execution(a)
        for value in ('true', 1, {}, None, False):
            with self.subTest(value=value), self.assertRaisesRegex(ControlError,'stop_proof_required'):
                self.queue.prove_stopped(a,value)
        with self.sessions() as s:
            self.assertFalse(s.get(Job,self.jid).stopped)

    def test_cancellation_returns_terminal_status_without_failing_again(self):
        _, a = self.claim(); self.queue.attach_execution(a)
        from ppt_service.server.repository import Repository
        with self.sessions.begin() as s:
            Repository(s).cancel(s.get(Job,self.jid))
        self.assertEqual(self.queue.prove_stopped(a,True),'cancelled')
        with self.sessions() as s:
            self.assertIsNone(s.get(ExecutionSlot,1).job_id)
            self.assertEqual(s.get(Job,self.jid).status,'cancelled')

    def test_pi_checkpoint_build_does_not_require_hermes_session(self):
        with self.sessions.begin() as s:
            cp = Checkpoint(project_id=self.pid, issuer='test', subject='owner', storage_key='projects/'+self.pid+'/checkpoints/fixture', manifest={'engine':'pi','checkpoint_format':'artifact-replay-v1','control_context':{}})
            s.add(cp);s.flush()
            j = s.get(Job,self.jid);j.checkpoint_id=cp.id;j.kind='generate';j.stage='generate'
        c, _ = self.claim()
        built = self.queue.build(c)
        self.assertTrue(built.attempt.session_id.startswith('ppt-'))
        self.assertNotIn('session_id',built.checkpoint_manifest)
        with self.sessions.begin() as s:
            cp = s.get(Checkpoint,s.get(Job,self.jid).checkpoint_id)
            cp.manifest={'session_id':'hermes-private-session'}
        with self.assertRaisesRegex(ControlError,'checkpoint_engine_mismatch'):
            self.queue.build(c)

    def test_error_mapping_and_invalid_lease(self):
        self.assertEqual(public_error('preview_ready_timeout'),'execution_interrupted')
        self.assertEqual(public_error('storage_full'),'storage_full')
        for lease in (True,30,601,'90'):
            with self.assertRaises(ControlError):RepositoryQueue(self.sessions,lease_seconds=lease)

    def test_postgres_concurrent_claim_only_one_winner(self):
        if self.engine.dialect.name != 'postgresql':self.skipTest('PostgreSQL row locks')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.queue.claim_next(),range(4)))
        self.assertEqual(sum(c is not None for c in results),1)


if __name__ == '__main__':unittest.main()
