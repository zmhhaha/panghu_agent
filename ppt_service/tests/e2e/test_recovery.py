"""Crash/cancel/replay checks use real durable rows and explicit stop fencing."""
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from ppt_service.control.execution import ControlError
from ppt_service.control.staging import read_json
from ppt_service.server.models import Checkpoint, ExecutionSlot, Job, JobEvent, Plan, Project, Version, now
from ppt_service.server.repository import Repository
from .harness import E2ECase


class RecoveryTests(E2ECase):
    def running_job(self):
        pid = self.create_project()['id']
        self.upload(pid)
        job = self.queue_plan(pid)
        self.complete_fake_phase(job['id'], hold=True)
        return pid, job['id']

    def cancel_http(self, jid, key='cancel-intent'):
        return self.client.post(f'/api/v1/jobs/{jid}/cancel', headers=self.headers() | {'Idempotency-Key': key})

    def expire(self, jid):
        with self.sessions.begin() as session:
            session.get(Job, jid).lease_until = now() - timedelta(seconds=1)

    def test_replacement_waits_for_lease_then_reconciles_without_reexecution(self):
        pid, jid = self.running_job()
        attempt = self.worker.attempts[jid]
        replacement = self.new_dispatcher()
        self.assertEqual(replacement.tick(), {'status': 'leased', 'job_id': jid})
        self.assertEqual(self.worker.creates, 1)
        self.assertEqual(self.worker.stops, 0)
        self.expire(jid)
        self.assertEqual(replacement.tick(), {'status': 'reconciled', 'job_id': jid})
        self.assertEqual(self.worker.creates, 1)
        self.assertEqual(self.worker.stops, 1)
        with self.sessions() as session:
            job = session.get(Job, jid)
            self.assertEqual(job.status, 'failed')
            self.assertEqual(job.error_code, 'execution_interrupted')
            self.assertTrue(job.stopped)
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])
        with self.assertRaises(HTTPException) as failure:
            self.dispatcher.progress(jid, attempt.attempt, attempt.token, 'validating')
        self.assertEqual(failure.exception.detail, {'code': 'stale_execution'})
        self.assertEqual(replacement.tick()['status'], 'idle')
        self.assertIsNone(self.project(pid)['active_job_id'])

    def test_cancel_keeps_slot_until_all_writers_have_stopped(self):
        _pid, jid = self.running_job()
        self.worker.stop_provable = False
        cancel = self.cancel_http(jid)
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()['status'], 'cancelling')
        with self.assertRaises(ControlError) as failure:
            self.dispatcher.tick()
        self.assertEqual(failure.exception.code, 'writers_not_stopped')
        with self.sessions.begin() as session:
            self.assertEqual(session.get(Job, jid).status, 'cancelling')
            self.assertFalse(session.get(Job, jid).stopped)
            self.assertEqual(session.get(ExecutionSlot, 1).job_id, jid)
            self.assertIsNone(Repository(session).claim_next())
        self.worker.stop_provable = True
        self.assertEqual(self.dispatcher.tick()['status'], 'stopped')
        with self.sessions() as session:
            self.assertEqual(session.get(Job, jid).status, 'cancelled')
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])
            self.assertEqual(session.scalars(select(Version)).all(), [])
        duplicate = self.cancel_http(jid)
        self.assertEqual(duplicate.json(), cancel.json())
        with self.sessions() as session:
            events = session.scalars(select(JobEvent).where(JobEvent.job_id == jid)).all()
            self.assertEqual([e.type for e in events].count('job.cancelled'), 1)

    def test_late_cancel_during_validation_revokes_checkpoint_promotion(self):
        pid = self.create_project()['id']
        self.upload(pid)
        jid = self.queue_plan(pid)['id']
        validate = self.sandbox.validate

        def cancel_after_validation(snapshot, request):
            result = validate(snapshot, request)
            self.assertTrue(result['accepted'])
            response = self.cancel_http(jid)
            self.assertEqual(response.status_code, 200)
            return result

        self.sandbox.validate = cancel_after_validation
        result = self.complete_fake_phase(jid)
        self.assertEqual(result['status'], 'cancelled')
        with self.sessions() as session:
            self.assertEqual(session.get(Job, jid).status, 'cancelled')
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])
            self.assertEqual(session.scalars(select(Plan)).all(), [])
        self.assertFalse((self.data_root / f'projects/{pid}/checkpoints').exists())

    def test_crash_between_staging_and_worker_create_is_reconciled_not_restarted(self):
        pid = self.create_project()['id']
        self.upload(pid)
        jid = self.queue_plan(pid)['id']
        with patch.object(self.worker, 'create', side_effect=SystemExit('synthetic-controller-crash')):
            with self.assertRaises(SystemExit) as crash:
                self.dispatcher.tick()
        self.assertEqual(str(crash.exception), 'synthetic-controller-crash')
        with self.sessions() as session:
            job = session.get(Job, jid)
            self.assertEqual(job.status, 'starting')
            self.assertIsNotNone(job.job_uid)
            self.assertEqual(session.get(ExecutionSlot, 1).job_id, jid)
        self.expire(jid)
        replacement = self.new_dispatcher()
        self.assertEqual(replacement.tick()['status'], 'reconciled')
        self.assertEqual(self.worker.creates, 0)
        with self.sessions() as session:
            self.assertEqual(session.get(Job, jid).status, 'failed')
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])

    def test_late_execution_token_cannot_mutate_next_job(self):
        _pid, first = self.running_job()
        stale = self.worker.attempts[first]
        self.cancel_http(first)
        self.dispatcher.tick()
        second_pid = self.create_project()['id']
        self.upload(second_pid)
        second = self.queue_plan(second_pid)['id']
        self.complete_fake_phase(second, hold=True)
        current = self.worker.attempts[second]
        self.assertGreater(current.token, stale.token)
        with self.assertRaises(HTTPException) as failure:
            with self.sessions.begin() as session:
                Repository(session).heartbeat(first, stale.attempt, stale.token)
        self.assertEqual(failure.exception.detail, {'code': 'stale_execution'})
        with self.assertRaises(HTTPException) as failure:
            with self.sessions.begin() as session:
                Repository(session).prove_stopped(first, stale.attempt, stale.token,
                    job_uid=stale.uid, all_writers_stopped=True)
        self.assertEqual(failure.exception.detail, {'code': 'stale_execution'})
        with self.sessions() as session:
            self.assertEqual(session.get(ExecutionSlot, 1).job_id, second)
            self.assertEqual(session.get(Job, second).status, 'running')
        self.cancel_http(second)
        self.dispatcher.tick()

    def test_checkpoint_restore_uses_explicit_selected_baseline_not_latest(self):
        pid = self.create_project()['id']
        self.upload(pid)
        first = self.queue_plan(pid)['id']
        self.assertEqual(self.complete_fake_phase(first)['status'], 'succeeded')
        self.confirm(pid, {'design_mode': 'free_design'})
        options = self.queue_plan(pid, 'plan_options')['id']
        with self.sessions() as session:
            baseline_id = session.get(Job, options).checkpoint_id
            baseline = session.get(Checkpoint, baseline_id)
            session_id = baseline.manifest['session_id']
            expected = baseline.manifest['project']['files']
        self.assertEqual(self.complete_fake_phase(options)['status'], 'succeeded')
        self.assertEqual(self.worker.tasks[options]['session_id'], session_id)
        restored = self.worker.layouts[options].base / 'project'
        for row in expected:
            from hashlib import sha256
            self.assertEqual(sha256((restored / row['path']).read_bytes()).hexdigest(), row['sha256'])
        with self.sessions() as session:
            self.assertEqual(session.get(Job, options).checkpoint_id, baseline_id)
            self.assertEqual(len(session.scalars(select(Checkpoint)).all()), 2)

    def test_missing_explicit_checkpoint_fails_once_and_next_job_can_run(self):
        pid = self.create_project()['id']
        self.upload(pid)
        first = self.queue_plan(pid)['id']
        self.complete_fake_phase(first)
        self.confirm(pid, {'design_mode': 'free_design'})
        options = self.queue_plan(pid, 'plan_options')['id']
        with self.sessions.begin() as session:
            session.get(Job, options).checkpoint_id = None
        next_pid = self.create_project()['id']
        self.upload(next_pid)
        next_job = self.queue_plan(next_pid)['id']
        self.assertEqual(self.dispatcher.tick(), {
            'status': 'failed', 'job_id': options, 'error_code': 'execution_interrupted'})
        with self.sessions() as session:
            rejected = session.get(Job, options)
            self.assertEqual(rejected.status, 'failed')
            self.assertFalse(rejected.stopped)
            self.assertIsNone(rejected.job_uid)
            self.assertIsNone(rejected.lease_until)
            self.assertIsNone(session.get(Project, pid).active_job_id)
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
            self.assertEqual(len(session.scalars(select(Checkpoint)).all()), 1)
            self.assertEqual(session.scalars(select(JobEvent.type).where(
                JobEvent.job_id == options).order_by(JobEvent.seq)).all(),
                ['job.queued', 'job.started', 'job.failed'])
        self.assertEqual(self.worker.creates, 1)
        self.assertEqual(self.worker.stops, 1)  # Only the completed first plan.
        self.assertEqual(self.complete_fake_phase(next_job)['status'], 'succeeded')
        self.assertEqual(self.worker.creates, 2)
        self.assertEqual(self.dispatcher.tick()['status'], 'idle')

    def test_malformed_checkpoint_manifest_is_terminal_before_worker_creation(self):
        pid = self.create_project()['id']
        self.upload(pid)
        self.complete_fake_phase(self.queue_plan(pid)['id'])
        self.confirm(pid, {'design_mode': 'free_design'})
        options = self.queue_plan(pid, 'plan_options')['id']
        with self.sessions.begin() as session:
            job = session.get(Job, options)
            original = session.get(Checkpoint, job.checkpoint_id)
            # Historical Checkpoint rows are immutable on PostgreSQL. Seed a
            # dedicated malformed record rather than alter the valid baseline.
            malformed = Checkpoint(project_id=pid, issuer=original.issuer,
                subject=original.subject, storage_key=original.storage_key + '-synthetic-malformed', manifest=[])
            session.add(malformed)
            session.flush()
            job.checkpoint_id = malformed.id
        self.assertEqual(self.dispatcher.tick()['status'], 'failed')
        with self.sessions() as session:
            self.assertEqual(session.get(Job, options).status, 'failed')
            self.assertFalse(session.get(Job, options).stopped)
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
        self.assertEqual(self.worker.creates, 1)
        self.assertEqual(self.worker.stops, 1)

    def test_prelaunch_failure_preserves_cancel_and_delete_intent(self):
        for disposition in ('cancel', 'delete'):
            with self.subTest(disposition=disposition):
                pid = self.create_project()['id']
                self.upload(pid)
                jid = self.queue_plan(pid)['id']
                def refuse(session, job):
                    if disposition == 'delete':
                        session.get(Project, pid).deletion_requested = True
                    Repository(session).cancel(job)
                    raise ControlError('explicit_checkpoint_required')
                with patch.object(self.dispatcher, '_attempt', side_effect=refuse):
                    self.assertEqual(self.dispatcher.tick(), {'status': 'cancelled', 'job_id': jid})
                with self.sessions() as session:
                    job, project = session.get(Job, jid), session.get(Project, pid)
                    self.assertEqual(job.status, 'cancelled')
                    self.assertFalse(job.stopped)
                    self.assertIsNone(job.error_code)
                    self.assertIsNone(project.active_job_id)
                    self.assertEqual(project.cleanup_ready, disposition == 'delete')
                    self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
                    self.assertEqual(session.scalars(select(JobEvent.type).where(
                        JobEvent.job_id == jid).order_by(JobEvent.seq)).all(),
                        ['job.queued', 'job.started', 'job.cancelling', 'job.cancelled'])
        self.assertEqual(self.worker.creates, 0)
        self.assertEqual(self.worker.stops, 0)

    def test_active_attempt_build_failure_preserves_slot_without_stop_proof(self):
        _pid, jid = self.running_job()
        with patch.object(self.dispatcher, '_attempt', side_effect=ControlError('explicit_checkpoint_required')):
            with self.assertRaisesRegex(ControlError, 'explicit_checkpoint_required'):
                self.dispatcher.tick()
        with self.sessions() as session:
            job = session.get(Job, jid)
            self.assertEqual(job.status, 'running')
            self.assertFalse(job.stopped)
            self.assertEqual(session.get(ExecutionSlot, 1).job_id, jid)
        self.assertEqual(self.worker.creates, 1)
        self.assertEqual(self.worker.stops, 0)
        self.cancel_http(jid)
        self.dispatcher.tick()

    def test_prelaunch_release_rejects_stale_identity_and_attached_writer(self):
        pid = self.create_project()['id']
        self.upload(pid)
        jid = self.queue_plan(pid)['id']
        with self.sessions.begin() as session:
            job = Repository(session).claim_next()
            identity = (job.id, job.attempt, job.fencing_token, job.kind)
        bad_identities = (
            (jid, identity[1] + 1, identity[2], identity[3]),
            (jid, identity[1], identity[2] + 1, identity[3]),
            (jid, identity[1], identity[2], 'generate'))
        for bad in bad_identities:
            with self.subTest(identity=bad), self.assertRaisesRegex(ControlError, 'stale_claim'):
                with self.sessions.begin() as session:
                    self.dispatcher._fail_unlaunched(session, *bad)
        with self.sessions.begin() as session:
            session.get(Job, jid).job_uid = 'a-possibly-live-writer'
        with self.assertRaisesRegex(ControlError, 'stale_claim'):
            with self.sessions.begin() as session:
                self.dispatcher._fail_unlaunched(session, *identity)
        with self.sessions() as session:
            self.assertEqual(session.get(Job, jid).status, 'starting')
            self.assertFalse(session.get(Job, jid).stopped)
            self.assertEqual(session.get(ExecutionSlot, 1).job_id, jid)
        self.assertEqual(self.worker.stops, 0)
        # This fixture has no real process. Remove the synthetic attachment and
        # take the ordinary backend stop/proof path to dispose of its owned slot.
        with self.sessions.begin() as session:
            session.get(Job, jid).job_uid = None
        self.cancel_http(jid)
        self.dispatcher.tick()

    def test_tampered_checkpoint_is_not_restored_or_replaced_with_latest(self):
        pid = self.create_project()['id']
        self.upload(pid)
        first = self.queue_plan(pid)['id']
        self.assertEqual(self.complete_fake_phase(first)['status'], 'succeeded')
        self.confirm(pid, {'design_mode': 'free_design'})
        options = self.queue_plan(pid, 'plan_options')['id']
        with self.sessions() as session:
            checkpoint = session.get(Checkpoint, session.get(Job, options).checkpoint_id)
            path = self.data_root / checkpoint.storage_key / 'project' / 'synthetic-deck' / 'source.md'
        path.chmod(0o600)
        path.write_bytes(b'SYNTHETIC corruption of retained checkpoint')
        result = self.dispatcher.tick()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.worker.creates, 1)
        with self.sessions() as session:
            self.assertEqual(session.get(Job, options).status, 'failed')
            self.assertEqual(len(session.scalars(select(Checkpoint)).all()), 1)
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)

    def test_queued_cancel_is_durable_without_worker_or_stop_calls(self):
        pid = self.create_project()['id']
        self.upload(pid)
        jid = self.queue_plan(pid)['id']
        response = self.cancel_http(jid)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'cancelled')
        self.assertEqual(self.new_dispatcher().tick()['status'], 'idle')
        self.assertEqual(self.worker.creates, 0)
        self.assertEqual(self.worker.stops, 0)
        with self.sessions() as session:
            self.assertTrue(session.get(Job, jid).stopped)
            self.assertIsNone(session.get(ExecutionSlot, 1).job_id)
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])

    def test_operator_approval_denied_preserves_actor_without_new_checkpoint(self):
        pid = self.create_project()['id']
        self.upload(pid)
        jid = self.queue_plan(pid)['id']
        self.worker.needs_input = True
        self.assertEqual(self.complete_fake_phase(jid)['status'], 'needs_input')
        layout = self.worker.layouts[jid]
        self.assertEqual(read_json(layout.result, 'manifest.json')['status'], 'needs_input')
        self.assertEqual(read_json(layout.result, 'actor.private.json')['message'], 'P5 test fixture')
        validation = read_json(layout.audit, 'validation-result.json')
        self.assertEqual(validation['blocked_kind'], 'operator_permission')
        self.assertTrue(validation['actor_record_preserved'])
        with self.sessions() as session:
            self.assertEqual(session.get(Job, jid).status, 'failed')
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])
        events = self.client.get(f'/api/v1/jobs/{jid}/events', headers=self.headers())
        self.assertEqual(events.status_code, 200)
        self.assertNotIn('P5 test fixture', events.text)
        self.assertNotIn('synthetic-tool', events.text)

    def test_project_deletion_waits_for_worker_stop_and_blocks_late_results(self):
        pid, jid = self.running_job()
        response = self.client.delete(f'/api/v1/projects/{pid}', headers=self.headers())
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json()['cleanup_ready'])
        self.assertEqual(self.client.get(f'/api/v1/projects/{pid}', headers=self.headers()).status_code, 404)
        self.assertEqual(self.dispatcher.tick()['status'], 'stopped')
        with self.sessions() as session:
            self.assertTrue(session.get(Project, pid).cleanup_ready)
            self.assertEqual(session.get(Job, jid).status, 'cancelled')
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])
