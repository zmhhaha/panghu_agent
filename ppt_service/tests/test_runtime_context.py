"""Runtime deadline and installed-path behavior; no gateway/model calls."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from ppt_service.worker.runtime_context import ExecutionBudget, installed_workflow_instructions, parse_execution_deadline


class RuntimeContextTests(unittest.TestCase):
    def test_original_cutoff_consumes_startup_and_clock_changes_do_not_renew_it(self):
        task = {'phase': 'generate', 'execution_deadline': '2026-10-29T12:30:00+08:00'}
        epoch = datetime(2026, 10, 29, 4, 5, tzinfo=timezone.utc).timestamp()
        budget = ExecutionBudget.create(task, 1800, 100, epoch)
        self.assertEqual(budget.remaining(100), 1500)
        self.assertEqual(budget.remaining(1000), 600)
        prompt = budget.instructions(1000)
        self.assertIn('最多剩余 600 秒', prompt)
        self.assertIn('最后 200 秒', prompt)
        self.assertIn('2026-10-29T04:30:00+00:00', prompt)
        self.assertIn('不得伪造PPTX', prompt)
        self.assertEqual(budget.remaining(1601), 0)
        with self.assertRaises(ValueError):
            budget.instructions(1601)

    def test_original_future_cutoff_cannot_extend_relative_limit(self):
        budget = ExecutionBudget.create({'phase': 'generate', 'execution_deadline': '2099-01-01T00:00:00+00:00'}, 1800, 10, 1000)
        self.assertEqual(budget.remaining(10), 1800)
        self.assertIn('最后 300 秒', budget.instructions(10))

    def test_non_generation_phases_only_close_out_their_own_stage(self):
        for phase in ('plan_initial', 'plan_options', 'prepare'):
            prompt = ExecutionBudget.create({'phase': phase}, 600, 0, 1000).instructions(1)
            with self.subTest(phase=phase):
                self.assertNotIn('finalize', prompt)
                self.assertNotIn('postflight', prompt)
                if phase == 'prepare':
                    self.assertIn('不生成页面或导出', prompt)
                else:
                    self.assertIn('不越过当前确认门', prompt)

    def test_legacy_task_and_untrusted_nested_fields_do_not_replace_worker_limit(self):
        budget = ExecutionBudget.create({'phase': 'prepare', 'requirements': {'execution_deadline': '2099-01-01T00:00:00Z'}}, 1800, 40, 1000)
        self.assertEqual(budget.remaining(50), 1790)

    def test_deadline_rejects_naive_malformed_or_non_string_values(self):
        for value in (None, True, 1, {}, '', 'tomorrow', '2026-10-29T12:00:00', 'x' * 65):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_execution_deadline(value)

    def test_inventory_uses_actual_image_names_without_file_contents_or_project_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'SKILL.md').write_text('private contents do not enter inventory')
            for name in ('scripts/finalize_svg.py', 'scripts/svg_to_pptx.py', 'workflows/stages/resume-execute.md', 'references/visual-review.md', 'scripts/tests/hidden.py'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('not read')
            prompt = installed_workflow_instructions(root)
            self.assertIn('scripts/finalize_svg.py', prompt)
            self.assertIn('workflows/stages/resume-execute.md', prompt)
            self.assertNotIn('private contents', prompt)
            self.assertNotIn('hidden.py', prompt)
            self.assertNotIn('quality_suite.py', prompt)
            self.assertIn('不要猜测或连续尝试近似脚本名', prompt)
            self.assertIn('原停止交接规则', prompt)

    def test_missing_skill_fails_closed_before_context_can_authorize_a_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError):
                installed_workflow_instructions(Path(temporary))


if __name__ == '__main__':
    unittest.main()
