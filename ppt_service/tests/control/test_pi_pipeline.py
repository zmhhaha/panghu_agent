"""Pipeline composition tests: fixture execution, real stop proof, frozen candidate."""
import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_worker.runtime import RuntimeSettings
from ppt_service.control import pi_pipeline, preview_launch, stop_gate
from ppt_service.control.execution import ControlError
from ppt_service.control.pi_uid import PiUIDAllocator, UIDScope
from ppt_service.tests.control import test_pi_staging as staging_fixtures

ROOT = Path(__file__).resolve().parents[3]
FAKE = ROOT / 'tests/scientific_pi/runtime/fake_pi.py'
PINS = ('runtime-pinned', 'ppt-skill-pinned')


class PipelineRefusalTests(unittest.TestCase):
    def test_non_linux_host_is_refused_before_staging(self):
        with patch.object(pi_pipeline.sys, 'platform', 'win32'), \
                patch.object(pi_pipeline, 'freeze_candidate') as freeze:
            with self.assertRaisesRegex(ControlError, 'attempt_pipeline_requires_linux_root'):
                pi_pipeline.run_offline_attempt(None, None, None, None, None, allocator=None, parsed={})
            freeze.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root controller')
    def test_invalid_writer_ids_are_refused_before_staging(self):
        with patch.object(pi_pipeline, 'freeze_candidate') as freeze:
            for writers in ((9999,), (65534,), ('31301',), (31301, 31301)):
                with self.subTest(writers=writers), self.assertRaisesRegex(ControlError, 'invalid_writer_ids'):
                    pi_pipeline.run_offline_attempt(object(), object(), object(), object(), None,
                                                    allocator=None, parsed={}, extra_writers=writers)
            freeze.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0,
                     'requires a Linux root controller with the verified image')
class PipelineLinuxTests(unittest.TestCase):
    def setUp(self):
        self.fixture = staging_fixtures.PiStagingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = Path(self.fixture.temp.name)
        self.fixture.asset()
        scope = UIDScope('ppt-test', 'offline', 'dedicated-hosts', 31500, 31599)
        self.allocator = PiUIDAllocator.initialize(self.root / 'ledger', scope)
        attempt = self.fixture.a
        self.pi_uid = self.allocator.allocate(project_id=attempt.project_id, job_id=attempt.job_id,
                                              attempt=attempt.attempt, token=attempt.token, role='pi')
        self.preview_uid = self.allocator.allocate(project_id=attempt.project_id, job_id=attempt.job_id,
                                                  attempt=attempt.attempt, token=attempt.token,
                                                  role='preview')
        self.addCleanup(stop_gate.stop_uids, (self.pi_uid, self.preview_uid))

    def parsed(self):
        return {'input_snapshot_sha256': self.fixture.a.snapshot['hash'], 'sources': []}

    def settings(self, mode='success'):
        skill = self.root / 'SKILL.md'
        skill.write_text('fixture skill; no production evidence', encoding='utf-8')
        return RuntimeSettings(
            command=(sys.executable, str(FAKE), mode),
            node_command=(sys.executable, str(FAKE), 'node'),
            provider='fixture', model='fixture-model', fixture_mode=True,
            skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
            runtime_revision=PINS[0], skill_bundle_revision=PINS[1],
            term_grace_seconds=0.1, kill_grace_seconds=1, idle_timeout_seconds=2)

    def run_pipeline(self, mode='success', **kwargs):
        return pi_pipeline.run_offline_attempt(
            self.fixture.config, self.fixture.stager, self.fixture.a, self.settings(mode),
            self.root / 'scratch', allocator=self.allocator, parsed=self.parsed(), **kwargs)

    def test_fixture_attempt_produces_frozen_candidate_only_after_stop(self):
        outcome = self.run_pipeline(candidate_id='55555555-5555-4555-8555-555555555555')
        self.assertIs(outcome.proof.all_writers_stopped, True)
        self.assertEqual(outcome.proof.uid, self.fixture.a.uid)
        self.assertIn(f'uid:{self.pi_uid}', outcome.proof.resource_ids)
        self.assertEqual(outcome.execution.event_types[-1], 'runtime_finished')
        self.assertEqual(set(path.name for path in outcome.frozen.path.iterdir()),
                         {'project', 'result', 'snapshot.json'})
        self.assertEqual(outcome.frozen.indexes['project']['files'], [])
        self.assertEqual(outcome.frozen.manifest['phase'], 'plan_initial')
        self.assertEqual(outcome.frozen.manifest['uid'], self.fixture.a.uid)
        self.assertNotIn('accepted', outcome.frozen.manifest)
        self.assertFalse((outcome.frozen.path / 'checkpoint.json').exists())

    def test_both_reserved_writers_are_covered_by_the_single_proof(self):
        outcome = self.run_pipeline(extra_writers=(self.preview_uid,),
                                    candidate_id='66666666-6666-4666-8666-666666666666')
        self.assertIn(f'uid:{self.preview_uid}', outcome.proof.resource_ids)
        self.assertEqual(stop_gate.uid_processes(self.preview_uid), [])

    def test_unproven_stop_blocks_freezing(self):
        target = self.root / f'projects/{self.fixture.a.project_id}/candidates'
        with patch.object(pi_pipeline, 'combined_stop_proof',
                          side_effect=ControlError('writers_not_stopped')):
            with self.assertRaisesRegex(ControlError, 'writers_not_stopped'):
                self.run_pipeline(candidate_id='77777777-7777-4777-8777-777777777777')
        if target.exists():
            self.assertEqual(list(target.iterdir()), [])

    def test_restored_phase_keeps_checkpoint_project_in_the_candidate(self):
        attempt, _, manifest = self.fixture.checkpoint('generate')
        pi_uid = self.allocator.verify(project_id=attempt.project_id, job_id=attempt.job_id,
                                       attempt=attempt.attempt, token=attempt.token, role='pi')
        self.addCleanup(stop_gate.stop_uids, (pi_uid,))
        outcome = pi_pipeline.run_offline_attempt(
            self.fixture.config, self.fixture.stager, attempt, self.settings(),
            self.root / 'scratch-generate', allocator=self.allocator,
            parsed={'input_snapshot_sha256': attempt.snapshot['hash'], 'sources': []},
            checkpoint_manifest=manifest,
            candidate_id='88888888-8888-4888-8888-888888888888')
        self.assertEqual(outcome.proof.uid, attempt.uid)
        self.assertEqual([row['path'] for row in outcome.frozen.indexes['project']['files']],
                         ['deck/notes.md'])
        self.assertEqual((outcome.frozen.path / 'project/deck/notes.md').read_bytes(),
                         (outcome.layout.project / 'deck/notes.md').read_bytes())
        self.assertEqual(outcome.frozen.manifest['phase'], 'generate')

    def test_preview_runs_after_stop_and_shares_one_proof(self):
        if not (Path(preview_launch.PREVIEW_SERVER).is_file()
                and Path(preview_launch.PREVIEW_PYTHON).is_file()):
            self.skipTest('pinned official Linux image is required')
        attempt, source, manifest = self.fixture.checkpoint('generate')
        for name in ('design_spec.md', 'spec_lock.md'):
            raw = b'preview marker'
            (source / 'project/deck' / name).write_bytes(raw)
            manifest['project']['files'].append(
                {'path': f'deck/{name}', 'bytes': len(raw),
                 'sha256': hashlib.sha256(raw).hexdigest()})
            manifest['project']['bytes'] += len(raw)
        self.fixture.save(source, manifest)
        os.chmod(self.root, 0o711)
        os.chmod(self.fixture.root, 0o711)
        spec = preview_launch.PreviewSpec(preview_launch.PREVIEW_LAUNCHER,
                                          preview_launch.PREVIEW_PYTHON,
                                          preview_launch.PREVIEW_SERVER, port=8795, lifetime=45)
        outcome = pi_pipeline.run_offline_attempt(
            self.fixture.config, self.fixture.stager, attempt, self.settings(),
            self.root / 'scratch-preview', allocator=self.allocator,
            parsed={'input_snapshot_sha256': attempt.snapshot['hash'], 'sources': []},
            checkpoint_manifest=manifest, preview=spec,
            candidate_id='99999999-9999-4999-8999-999999999999')
        self.assertEqual(outcome.preview_uid, self.preview_uid)
        self.assertIn(f'uid:{self.preview_uid}', outcome.proof.resource_ids)
        self.assertIn(f'uid:{self.pi_uid}', outcome.proof.resource_ids)
        self.assertEqual(stop_gate.uid_processes(self.preview_uid), [])
        self.assertEqual(stop_gate.uid_processes(self.pi_uid), [])
        paths = [row['path'] for row in outcome.frozen.indexes['project']['files']]
        self.assertEqual(paths, ['deck/design_spec.md', 'deck/notes.md', 'deck/spec_lock.md'])
        self.assertEqual((outcome.frozen.path / 'project/deck/design_spec.md').read_bytes(),
                         b'preview marker')

    def test_runtime_failure_never_reaches_collection(self):
        with patch.object(pi_pipeline, 'freeze_candidate') as freeze:
            with self.assertRaises(Exception):
                self.run_pipeline(mode='exit')
            freeze.assert_not_called()


class PipelineSafetyTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from ppt_service.tests.control.test_preview_launch import layout, deck, attempt
        from ppt_service.control.execution import RuntimeConfig
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        self.layout = layout(self.root / 'attempt')
        self.project = deck(self.layout.root)
        self.layout.audit.mkdir()
        self.attempt = attempt()
        self.config = RuntimeConfig(data_root=self.root)

    def test_stop_failure_never_reclaims_or_deletes(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        for failure in ('handle', 'all'):
            with self.subTest(failure=failure):
                ledger = Mock()
                ledger.verify.side_effect = [31300, 31301]
                stager = Mock()
                stager.stage.return_value = (self.layout, None)
                handle = Mock()
                if failure == 'handle':
                    handle.stop.side_effect = ControlError('stop_failed')
                stops = [(), (), ControlError('still_running')] if failure == 'all' else [(), ()]
                with patch.object(pi_pipeline, '_require_offline_linux'), \
                        patch.object(pi_pipeline, 'run_prepared_fixture'), \
                        patch.object(pi_pipeline, 'stop_uids', side_effect=stops), \
                        patch.object(pi_pipeline, '_handoff'), \
                        patch.object(pi_pipeline, 'launch_preview', return_value=handle), \
                        patch.object(pi_pipeline, '_reclaim') as reclaim, \
                        patch.object(pi_pipeline, '_reclaim_private') as private, \
                        patch.object(pi_pipeline, '_clear_preview_state') as clear, \
                        patch.object(pi_pipeline, 'freeze_candidate') as freeze:
                    with self.assertRaisesRegex(ControlError, 'writers_not_stopped'):
                        pi_pipeline.run_offline_attempt(self.config, stager, self.attempt,
                                                       SimpleNamespace(fixture_mode=True), self.root, allocator=ledger,
                                                       parsed={}, preview=object())
                    for operation in (reclaim, private, clear, freeze):
                        operation.assert_not_called()

    def test_linked_sentinel_rejected_before_any_mutation(self):
        sentinel = self.root / 'sentinel'
        sentinel.write_bytes(b'evidence')
        before = sentinel.stat()
        for kind in ('hardlink', 'symlink'):
            target = self.project / 'unsafe'
            try:
                if kind == 'hardlink':
                    os.link(sentinel, target)
                else:
                    target.symlink_to(sentinel)
            except OSError as exc:
                if kind == 'symlink' and os.name == 'nt':
                    continue
                raise exc
            try:
                with patch.object(pi_pipeline, '_mutate') as mutate:
                    with self.assertRaises(ControlError):
                        pi_pipeline._handoff(self.config, self.layout, self.project, 31301)
                    with self.assertRaises(ControlError):
                        pi_pipeline._reclaim(self.layout.project)
                    mutate.assert_not_called()
                self.assertEqual(sentinel.read_bytes(), b'evidence')
                self.assertEqual(sentinel.stat().st_mode, before.st_mode)
            finally:
                target.unlink()

    def test_unsafe_entry_types_preflight_before_mutation(self):
        import stat
        from types import SimpleNamespace
        target = self.project / 'unsafe'
        target.write_bytes(b'evidence')
        original = Path.lstat
        for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFREG, 0x400),
                                 (stat.S_IFIFO, 0)):
            unsafe = SimpleNamespace(st_mode=mode, st_file_attributes=attributes, st_nlink=1)
            def observed(path, *args, **kwargs):
                return unsafe if path == target else original(path, *args, **kwargs)
            with self.subTest(mode=mode, attributes=attributes), \
                    patch.object(Path, 'lstat', observed), \
                    patch.object(pi_pipeline, '_mutate') as mutate:
                with self.assertRaises(ControlError):
                    pi_pipeline._handoff(self.config, self.layout, self.project, 31301)
                mutate.assert_not_called()
            self.assertEqual(target.read_bytes(), b'evidence')

    def test_open_chain_only_mutates_below_data_root(self):
        with patch.object(pi_pipeline, '_mutate') as mutate:
            pi_pipeline._open_chain(self.config, self.layout)
        self.assertEqual([call.args[0] for call in mutate.call_args_list], [self.layout.root])

    def test_malformed_root_never_changes_ancestor_modes(self):
        from dataclasses import replace
        for root in (self.root.parent, self.root / '..' / 'outside', Path('relative')):
            with self.subTest(root=root), patch.object(pi_pipeline, '_mutate') as mutate:
                with self.assertRaises(ControlError):
                    pi_pipeline._open_chain(self.config, replace(self.layout, root=root))
                mutate.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX nofollow directory descriptors')
    def test_cleanup_preserves_evidence_and_removes_only_exact_locks(self):
        state = self.project / 'live_preview'
        state.mkdir()
        (state / 'lock.json').write_text('lock')
        (self.project / '.live_preview.lock').write_text('lock')
        (state / 'evidence.json').write_text('evidence')
        pi_pipeline._clear_preview_state(self.project)
        self.assertEqual((state / 'evidence.json').read_text(), 'evidence')
        self.assertFalse((state / 'lock.json').exists())
        self.assertFalse((self.project / '.live_preview.lock').exists())


if __name__ == '__main__':
    unittest.main()
