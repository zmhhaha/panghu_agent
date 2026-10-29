"""Shared attempt builder tests with fake session records; no database."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from ppt_service.control.attempt_builder import build_attempt
from ppt_service.control.execution import ControlError, RuntimeConfig
from ppt_service.control.staging import Stager, atomic_json, read_json

PID = '11111111-1111-4111-8111-111111111111'
JID = '22222222-2222-4222-8222-222222222222'
HASH = 'a' * 64


class Obj:
    def __init__(self, **values):
        self.__dict__.update(values)

    def get(self, name, default=None):
        return getattr(self, name, default)


class FakeSession:
    def __init__(self, lookups=None, scalar=None):
        self.lookups, self.plan = lookups or {}, scalar
        self.gets = []

    def get(self, model, key):
        self.gets.append((model.__name__, key))
        return self.lookups.get((model.__name__, key))

    def scalar(self, statement):
        return self.plan


def job(**changes):
    base = dict(id=JID, project_id=PID, issuer='iss', subject='sub', attempt=1, fencing_token=7,
                kind='plan_initial', input_snapshot={'hash': HASH, 'confirmation_selection': {'x': 1}},
                checkpoint_id=None, base_version_id=None, session_id='sess')
    base.update(changes)
    return Obj(**base)


def checkpoint(**changes):
    base = dict(id='cp1', project_id=PID, issuer='iss', subject='sub', storage_key='projects/p/checkpoints/1',
                manifest={'session_id': 'cp-session', 'control_context': {'stage1_confirmation': {'a': 1}}})
    base.update(changes)
    return Obj(**base)


class BuilderTests(unittest.TestCase):
    def test_initial_plan_uses_job_session_and_no_checkpoint(self):
        attempt, manifest = build_attempt(FakeSession(), job())
        self.assertEqual(attempt.phase, 'plan_initial')
        self.assertEqual(attempt.session_id, 'sess')
        self.assertIsNone(attempt.checkpoint_key)
        self.assertIsNone(manifest)
        self.assertEqual(attempt.snapshot['control_context'], {})
        self.assertIsNone(attempt.execution_deadline)

    def test_original_claim_deadline_is_normalized_without_changing_the_job(self):
        original = datetime(2026, 10, 29, 12, 30, tzinfo=timezone(timedelta(hours=8)))
        record = job(deadline=original)
        attempt, _ = build_attempt(FakeSession(), record)
        self.assertEqual(attempt.execution_deadline.isoformat(), '2026-10-29T04:30:00+00:00')
        self.assertIs(record.deadline, original)
        self.assertNotIn('control_context', record.input_snapshot)
        self.assertEqual(record.input_snapshot['hash'], HASH)

    def test_sqlite_naive_claim_deadline_keeps_existing_utc_interpretation(self):
        original = datetime(2026, 10, 29, 4, 30)
        record = job(deadline=original)
        attempt, _ = build_attempt(FakeSession(), record)
        self.assertEqual(attempt.execution_deadline, original.replace(tzinfo=timezone.utc))
        self.assertIsNone(record.deadline.tzinfo)

    def test_invalid_claim_deadlines_fail_before_staging(self):
        for value in ('2026-10-29T04:30:00+00:00', True, 1800):
            with self.subTest(value=value), self.assertRaisesRegex(ControlError, 'invalid_execution_deadline'):
                build_attempt(FakeSession(), job(deadline=value))
        attempt, _ = build_attempt(FakeSession(), job())
        with self.assertRaisesRegex(ControlError, 'invalid_execution_deadline'):
            replace(attempt, execution_deadline=datetime(2026, 10, 29, 4, 30))

    def staged_task(self, record):
        attempt, _ = build_attempt(FakeSession(), record)
        with tempfile.TemporaryDirectory() as directory:
            stager = Stager(RuntimeConfig(Path(directory)))
            layout = stager.layout(attempt)
            for name in ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base'):
                getattr(layout, name).mkdir(parents=True)
            atomic_json(layout.inputs / 'inventory.json', {'sources': []})
            stager.finish_inputs(attempt, layout, {'input_snapshot_sha256': HASH, 'sources': []})
            return read_json(layout.task, 'task.json')

    def test_readonly_task_contains_only_the_original_claim_deadline(self):
        original = datetime(2026, 10, 29, 4, 30, tzinfo=timezone.utc)
        record = job(deadline=original, input_snapshot={
            'hash': HASH, 'execution_deadline': '2099-01-01T00:00:00+00:00',
            'settings': {'page_count': 3},
        })
        task = self.staged_task(record)
        self.assertEqual(task['execution_deadline'], original.isoformat())
        self.assertEqual(task['input_snapshot_sha256'], HASH)
        self.assertEqual(record.input_snapshot['execution_deadline'], '2099-01-01T00:00:00+00:00')

    def test_legacy_attempt_does_not_invent_or_copy_a_snapshot_deadline(self):
        record = job(input_snapshot={'hash': HASH, 'execution_deadline': '2099-01-01T00:00:00+00:00'})
        self.assertNotIn('execution_deadline', self.staged_task(record))

    def test_unsupported_phase_is_refused(self):
        with self.assertRaisesRegex(ControlError, 'unsupported_phase'):
            build_attempt(FakeSession(), job(kind='bogus'))

    def test_missing_checkpoint_for_continuation_is_refused(self):
        with self.assertRaisesRegex(ControlError, 'explicit_checkpoint_required'):
            build_attempt(FakeSession(), job(kind='generate'))

    def test_foreign_checkpoint_owner_is_refused(self):
        session = FakeSession({('Checkpoint', 'cp1'): checkpoint(issuer='other')})
        with self.assertRaisesRegex(ControlError, 'checkpoint_owner_mismatch'):
            build_attempt(session, job(kind='generate', checkpoint_id='cp1'))

    def test_checkpoint_session_and_context_are_inherited(self):
        session = FakeSession({('Checkpoint', 'cp1'): checkpoint()})
        attempt, manifest = build_attempt(session, job(kind='generate', checkpoint_id='cp1'))
        self.assertEqual(attempt.session_id, 'cp-session')
        self.assertEqual(attempt.checkpoint_key, 'projects/p/checkpoints/1')
        self.assertEqual(attempt.snapshot['control_context'], {'stage1_confirmation': {'a': 1}})
        self.assertTrue(manifest['session_id'])

    def test_revise_requires_matching_base_version_and_checkpoint(self):
        version = Obj(project_id=PID, issuer='iss', subject='sub', checkpoint_id='cp1', status='published')
        session = FakeSession({('Version', 'v1'): version, ('Checkpoint', 'cp1'): checkpoint()})
        attempt, _ = build_attempt(session, job(kind='revise', base_version_id='v1', checkpoint_id='cp1'))
        self.assertEqual(attempt.base_version_id, 'v1')
        version.status='review'
        with self.assertRaisesRegex(ControlError, 'base_version_mismatch'):
            build_attempt(session, job(kind='revise', base_version_id='v1', checkpoint_id='cp1'))
        version.status='published'
        with self.assertRaisesRegex(ControlError, 'base_version_mismatch'):
            build_attempt(FakeSession(), job(kind='revise', base_version_id='v1'))
        with self.assertRaisesRegex(ControlError, 'base_checkpoint_mismatch'):
            build_attempt(FakeSession({('Version', 'v1'): version,
                                       ('Checkpoint', 'cp1'): checkpoint()}),
                          job(kind='revise', base_version_id='v1', checkpoint_id='cp2'))

    def test_plan_confirmation_lineage_must_match_the_stored_plan(self):
        session = FakeSession({('Checkpoint', 'cp1'): checkpoint()})
        session.plan = Obj(id='p1', phase='plan_initial', confirmed_at='now', revision=3,
                           confirmation_selection={'direction_id': 'A'}, input_snapshot_hash=HASH,
                           outline={'pages': []}, design={'directions': [{'id': 'A'}]})
        selection = {'hash': HASH, 'confirmation_selection': {'direction_id': 'A'}}
        attempt, _ = build_attempt(session, job(kind='plan_options', checkpoint_id='cp1',
                                                input_snapshot=dict(selection)))
        self.assertEqual(attempt.snapshot['control_context']['stage1_confirmation']['plan_revision'], 3)
        session.plan.phase = 'plan_options'
        attempt, _ = build_attempt(session, job(kind='prepare', checkpoint_id='cp1',
                                                input_snapshot=dict(selection)))
        self.assertEqual(attempt.snapshot['control_context']['stage2_confirmation']['selected_direction'],
                         {'id': 'A'})

    def test_confirmation_mismatch_selection_and_direction_are_refused(self):
        session = FakeSession({('Checkpoint', 'cp1'): checkpoint()})
        session.plan = Obj(id='p1', phase='plan_options', confirmed_at='now', revision=1,
                           confirmation_selection={'x': 1}, input_snapshot_hash='b' * 64,
                           outline={}, design={'directions': [{'id': 'A'}]})
        with self.assertRaisesRegex(ControlError, 'confirmation_lineage_mismatch'):
            build_attempt(session, job(kind='prepare', checkpoint_id='cp1'))
        session.plan = Obj(id='p1', phase='plan_initial', confirmed_at=None, revision=1,
                           confirmation_selection={'x': 1}, input_snapshot_hash=HASH,
                           outline={}, design={})
        with self.assertRaisesRegex(ControlError, 'confirmation_lineage_mismatch'):
            build_attempt(session, job(kind='plan_options', checkpoint_id='cp1'))
        session.plan = Obj(id='p1', phase='plan_options', confirmed_at='now', revision=1,
                           confirmation_selection={'x': 1}, input_snapshot_hash=HASH,
                           outline={}, design={'directions': [{'id': 'B'}]})
        with self.assertRaisesRegex(ControlError, 'selected_direction_missing'):
            build_attempt(session, job(kind='prepare', checkpoint_id='cp1'))

    def test_invalid_session_is_refused(self):
        with self.assertRaisesRegex(ControlError, 'invalid_session'):
            build_attempt(FakeSession(), job(session_id=123))


if __name__ == '__main__':
    unittest.main()
