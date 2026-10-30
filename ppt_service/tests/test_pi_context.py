"""Prepared-context contracts using actual staging and fake commands only."""
import copy
import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from ppt_service.control.execution import ControlError
from ppt_service.tests.control import test_pi_staging as staging_fixtures
from ppt_service.tests import test_pi_profile as profile_fixtures
from ppt_service.worker.pi_context import PreparedPptContext, bounded_json
from ppt_service.worker.pi_profile import PptProfile
from pi_worker.runtime import RuntimeExecutionError
from pi_worker.runtime.profiles import ProfileError


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture = staging_fixtures.PiStagingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = Path(self.fixture.temp.name)
        self.skill = self.root / 'SKILL.md'
        self.skill.write_text('Offline placeholder, not production evidence', encoding='utf-8')

    def prepare(self, phase='plan_initial', assets=True, execution_deadline=None):
        f = self.fixture
        if assets:
            f.asset()
        if phase == 'plan_initial':
            attempt, manifest = f.a, None
        else:
            attempt, _, manifest = f.checkpoint(phase)
        attempt = dataclasses.replace(attempt, execution_deadline=execution_deadline)
        layout, inventory = f.stager.stage(attempt, manifest)
        task = f.stager.finish_inputs(attempt, layout, {'input_snapshot_sha256': attempt.snapshot['hash'], 'sources': []})
        context = PreparedPptContext.from_stager(f.stager, attempt, layout, checkpoint_manifest=manifest)
        self.job = profile_fixtures.ppt_job()
        self.job.update(phase=phase, job_id=attempt.job_id, input_snapshot_sha256=attempt.snapshot['hash'],
                        runtime_revision=context.runtime_revision, skill_bundle_revision=context.skill_bundle_revision,
                        task=task, checkpoint=manifest)
        self.profile = PptProfile(runtime_revision=context.runtime_revision,
                                  skill_bundle_revision=context.skill_bundle_revision, prepared_context=context)
        return context, attempt, layout, inventory

    def runtime(self, **kwargs):
        return profile_fixtures.ProfileTests.runtime(self, **kwargs)

    def reject_before_probe(self, job):
        with patch('pi_worker.runtime.adapter.subprocess.Popen') as spawn:
            with self.assertRaises(RuntimeExecutionError):
                self.runtime().execute(job, self.root/'unused', lambda event: None, threading.Event())
            spawn.assert_not_called()

    def test_bound_assets_real_stage_and_fake_execution(self):
        context, attempt, layout, inventory = self.prepare()
        self.assertTrue(self.job['task']['assets'])
        self.assertEqual(context.receipt['token'], attempt.token)
        self.assertEqual(context.receipt['stage_root'], str(layout.root))
        self.assertEqual(context.receipt['paths']['task'], '/work/task/task.json')
        self.assertEqual(context.receipt['task_sha256'], hashlib.sha256(bounded_json(context.task).encode()).hexdigest())
        self.runtime().execute(self.job, self.root/'runtime-scratch', lambda event: None, threading.Event())
        prompt = (self.root/'runtime-scratch/task.md').read_text(encoding='utf-8')
        self.assertEqual(prompt, self.profile.build_prompt(self.job))
        for text in ('cd /work/project', '/work/inputs/inventory.json', '/work/task/task.json',
                     '/work/base', '/work/result/manifest.json', 'separate fresh scratch cwd', 'not parser authenticity'):
            self.assertIn(text, prompt)
        self.assertNotIn('has no staged source files', prompt)
        self.assertNotIn('checkpoint was restored', prompt)
        self.assertFalse(self.profile.production_ready)

    def test_every_continuation_has_exact_confirmations_and_fake_execution(self):
        for phase in ('plan_options', 'prepare', 'generate', 'revise'):
            with self.subTest(phase=phase):
                other = ContextTests(); other.setUp()
                try:
                    context, _, _, _ = other.prepare(phase)
                    other.profile.validate(other.job)
                    key = 'stage1_confirmation' if phase == 'plan_options' else 'stage2_confirmation'
                    self.assertIn(key, context.task)
                    self.assertIn('checkpoint was restored', other.profile.build_prompt(other.job))
                    other.runtime().execute(other.job, other.root/'scratch', lambda event: None, threading.Event())
                    bad = copy.deepcopy(other.job); bad['task'][key]['outline'] = {'mutated': True}
                    other.reject_before_probe(bad)
                    bad = copy.deepcopy(other.job); del bad['task'][key]
                    other.reject_before_probe(bad)
                finally:
                    other.doCleanups()

    def test_original_deadline_is_bound_and_profile_rejects_extensions(self):
        deadline = datetime(2026, 10, 29, 12, 30, tzinfo=timezone(timedelta(hours=8)))
        context, attempt, layout, _ = self.prepare(execution_deadline=deadline)
        self.assertEqual(context.task['execution_deadline'], '2026-10-29T04:30:00+00:00')
        self.profile.validate(self.job)
        for value in ('2099-01-01T00:00:00+00:00', None):
            with self.subTest(deadline=value):
                bad = copy.deepcopy(self.job)
                if value is None:
                    del bad['task']['execution_deadline']
                else:
                    bad['task']['execution_deadline'] = value
                self.reject_before_probe(bad)
        with self.assertRaisesRegex(ControlError, 'unstaged_attempt'):
            PreparedPptContext.from_stager(self.fixture.stager,
                dataclasses.replace(attempt, execution_deadline=deadline + timedelta(hours=1)), layout)

    def test_staged_deadline_cannot_change_before_finishing_inputs(self):
        fixture = self.fixture
        deadline = datetime(2026, 10, 29, 4, 30, tzinfo=timezone.utc)
        attempt = dataclasses.replace(fixture.a, execution_deadline=deadline)
        layout, _ = fixture.stager.stage(attempt)
        parsed = {'input_snapshot_sha256': attempt.snapshot['hash'], 'sources': []}
        with self.assertRaisesRegex(ControlError, 'unstaged_attempt'):
            fixture.stager.finish_inputs(
                dataclasses.replace(attempt, execution_deadline=deadline + timedelta(hours=1)), layout, parsed)
        fixture.stager.finish_inputs(attempt, layout, parsed)
        self.assertEqual(PreparedPptContext.from_stager(fixture.stager, attempt, layout).task[
            'execution_deadline'], deadline.isoformat())

    def test_bound_identity_pins_checkpoint_base_and_extras_rejected_before_probe(self):
        self.prepare('revise')
        mutations = [('job_id', 'other'), ('input_snapshot_sha256', '0'*64), ('runtime_revision', 'other'),
                     ('skill_bundle_revision', 'other'), ('phase', 'plan_initial'), ('checkpoint', None),
                     ('prepared_context', {}), ('production_ready', True), ('paths', {'project': '/elsewhere'})]
        for key, value in mutations:
            with self.subTest(key=key):
                bad = copy.deepcopy(self.job); bad[key] = value
                self.reject_before_probe(bad)
        for key, value in [('attempt', 2), ('session_id', 'other'), ('base_version_id', None),
                           ('feedback', 'changed'), ('extra', True), ('token', 8), ('assets', [])]:
            with self.subTest(task=key):
                bad = copy.deepcopy(self.job); bad['task'][key] = value
                self.reject_before_probe(bad)
        for key in ('source_job_id', 'source_token', 'phase', 'project_id'):
            with self.subTest(checkpoint=key):
                bad = copy.deepcopy(self.job); bad['checkpoint'][key] = 'wrong'
                self.reject_before_probe(bad)

    def test_context_is_frozen_detached_and_not_json_constructible(self):
        context, attempt, _, _ = self.prepare('prepare')
        original = context.task
        detached = context.task; detached['stage2_confirmation']['outline']['changed'] = True
        self.job['task']['requirements']['title'] = 'mutated alias'
        attempt.snapshot['control_context']['stage2_confirmation']['outline']['changed'] = True
        self.assertEqual(context.task, original)
        receipt = context.receipt; receipt['token'] = 999
        self.assertNotEqual(context.receipt['token'], 999)
        cp = context.checkpoint; cp['phase'] = 'changed'
        self.assertNotEqual(context.checkpoint['phase'], 'changed')
        with self.assertRaises((AttributeError, dataclasses.FrozenInstanceError)):
            context.runtime_revision = 'other'
        with self.assertRaises(TypeError): PreparedPptContext()
        with self.assertRaises(TypeError): PreparedPptContext(**context.receipt)
        with self.assertRaises(ProfileError):
            PptProfile(runtime_revision=context.runtime_revision, skill_bundle_revision=context.skill_bundle_revision,
                       prepared_context=context.receipt)

    def test_constructor_requires_actual_stage_and_exact_attempt(self):
        f = self.fixture
        with self.assertRaises(ControlError): PreparedPptContext.from_stager(f.stager, f.a, f.stager.layout(f.a))
        context, attempt, layout, _ = self.prepare()
        for change in ({'token': attempt.token + 1}, {'session_id': 'other'}, {'phase': 'prepare'}):
            with self.subTest(change=change), self.assertRaises(ControlError):
                PreparedPptContext.from_stager(f.stager, dataclasses.replace(attempt, **change), layout)
        with self.assertRaises(ControlError): PreparedPptContext.from_stager({}, attempt, layout)
        with self.assertRaises(ProfileError):
            PptProfile(runtime_revision='other', skill_bundle_revision=context.skill_bundle_revision, prepared_context=context)

    def test_constructor_rechecks_task_inventory_assets_and_restored_base(self):
        for target in ('task', 'inventory', 'asset', 'base', 'project', 'parsed'):
            with self.subTest(target=target):
                other = ContextTests(); other.setUp()
                try:
                    context, attempt, layout, inventory = other.prepare('prepare')
                    paths = {'task': layout.task/'task.json', 'inventory': layout.inputs/'inventory.json',
                             'asset': layout.inputs/inventory['sources'][0]['path'],
                             'base': layout.base/'project/deck/notes.md', 'project': layout.project/'deck/notes.md',
                             'parsed': layout.inputs/'parsed.json'}
                    path = paths[target]; path.chmod(0o600)
                    if target == 'task':
                        task = context.task; task['extra'] = True
                        path.write_text(json.dumps(task), encoding='utf-8')
                    else: path.write_bytes(b'{}')
                    with self.assertRaises((ControlError, ValueError)):
                        PreparedPptContext.from_stager(other.fixture.stager, attempt, layout, checkpoint_manifest=context.checkpoint)
                finally: other.doCleanups()

    def test_independent_expected_checkpoint_manifest_is_required(self):
        context, attempt, layout, _ = self.prepare('prepare')
        with self.assertRaises(ControlError): PreparedPptContext.from_stager(self.fixture.stager, attempt, layout)
        manifest = context.checkpoint; manifest['source_token'] += 1
        with self.assertRaises(ControlError):
            PreparedPptContext.from_stager(self.fixture.stager, attempt, layout, checkpoint_manifest=manifest)

    def test_strict_json_and_deterministic_pure_validation(self):
        self.prepare(); expected = self.profile.build_prompt(self.job)
        with patch('ppt_service.worker.pi_context.read_json', side_effect=AssertionError('unexpected IO')):
            self.assertEqual(self.profile.build_prompt(self.job), expected)
            self.profile.validate(self.job)
        for value in (float('nan'), {'tuple': (1, 2)}, {1: 'non-string key'}, 'x'*65537):
            with self.subTest(value_type=type(value)), self.assertRaises((ValueError, TypeError)): bounded_json(value)
        bad = copy.deepcopy(self.job); bad['task']['requirements']['title'] = 'x'*65537
        self.reject_before_probe(bad)

    def test_bound_context_does_not_grant_production_permission(self):
        self.prepare('generate')
        runtime = self.runtime(fixture_mode=False, capability_verified=True, model_verified=True,
                               isolation_verified=True, image_digest='sha256:'+'a'*64)
        with patch('pi_worker.runtime.adapter.subprocess.Popen') as spawn:
            self.assertFalse(runtime.health()['ready'])
            with self.assertRaises(RuntimeExecutionError) as caught:
                runtime.execute(self.job, self.root/'production', lambda event: None, threading.Event())
            self.assertEqual(caught.exception.code, 'runtime_not_ready')
            spawn.assert_not_called()


if __name__ == '__main__': unittest.main()
