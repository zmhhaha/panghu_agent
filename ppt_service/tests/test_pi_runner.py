"""No-model integration of actual staging with a separate fresh fixture runtime."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch
from pi_worker.runtime import RuntimeExecutionError
from ppt_service.control.execution import ControlError
from ppt_service.tests import test_pi_context as fixtures
from ppt_service.worker.pi_runner import run_prepared_fixture


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def prepared(self, phase='plan_initial'):
        context, attempt, layout, inventory = self.fixture.prepare(phase)
        return context, attempt, layout, self.fixture.runtime().settings

    def run_fixture(self, context, attempt, layout, settings, workspace=None, **kwargs):
        return run_prepared_fixture(self.fixture.fixture.stager, attempt, layout, settings,
            workspace or self.fixture.root/'runner-scratch', checkpoint_manifest=context.checkpoint, **kwargs)

    def test_prepared_assets_run_with_separate_empty_workspace(self):
        context, attempt, layout, settings = self.prepared()
        result = self.run_fixture(context, attempt, layout, settings)
        self.assertEqual(result.context.receipt, context.receipt)
        self.assertEqual(result.event_types[-1], 'runtime_finished')
        self.assertTrue((result.workspace/'.home').is_dir())
        self.assertFalse((layout.project/'.home').exists())
        self.assertTrue(result.context.task['assets'])
        self.assertFalse(hasattr(result, 'stop_proof'))
        self.assertFalse(hasattr(result, 'accepted'))

    def test_restored_prepare_keeps_exact_confirmation_and_baseline(self):
        context, attempt, layout, settings = self.prepared('prepare')
        baseline = (layout.base/'project/deck/notes.md').read_bytes()
        result = self.run_fixture(context, attempt, layout, settings)
        self.assertEqual(result.context.task['stage2_confirmation'], context.task['stage2_confirmation'])
        self.assertEqual((layout.base/'project/deck/notes.md').read_bytes(), baseline)
        self.assertIn('checkpoint was restored', (result.workspace/'task.md').read_text(encoding='utf-8'))

    def test_production_is_rejected_before_io_or_spawn(self):
        context, attempt, layout, settings = self.prepared()
        with patch('ppt_service.worker.pi_runner.PreparedPptContext.from_stager') as build, patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeExecutionError, 'offline fixtures'):
                self.run_fixture(context, attempt, layout, replace(settings, fixture_mode=False))
            build.assert_not_called(); spawn.assert_not_called()

    def test_rechecks_disk_before_runtime(self):
        context, attempt, layout, settings = self.prepared()
        task = layout.task/'task.json'; task.chmod(0o600); task.write_text('{}')
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(ControlError): self.run_fixture(context, attempt, layout, settings)
            spawn.assert_not_called()

    def test_domain_overlap_and_nonempty_scratch_rejected(self):
        context, attempt, layout, settings = self.prepared()
        for path in (layout.root, layout.project, layout.root/'runtime', layout.root.parent):
            with self.subTest(path=path), patch('subprocess.Popen') as spawn:
                with self.assertRaises(RuntimeExecutionError): self.run_fixture(context, attempt, layout, settings, path)
                spawn.assert_not_called()
        scratch = self.fixture.root/'occupied'; scratch.mkdir(); (scratch/'sentinel').write_text('keep')
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(RuntimeExecutionError): self.run_fixture(context, attempt, layout, settings, scratch)
            spawn.assert_not_called()
        self.assertEqual((scratch/'sentinel').read_text(), 'keep')

    def test_no_caller_wire_or_uid_override(self):
        context, attempt, layout, settings = self.prepared()
        for key in ('wire', '_runtime_uid', 'attempt_override', 'production_ready'):
            with self.subTest(key=key), self.assertRaises(TypeError):
                self.run_fixture(context, attempt, layout, settings, **{key: 12345})


if __name__ == '__main__': unittest.main()
