"""Container plan tests: pure argument composition and refusal boundaries."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ppt_service.control import pi_container
from ppt_service.control.execution import Attempt, ControlError, Layout, RuntimeConfig

JID = '22222222-2222-4222-8222-222222222222'
PID = '11111111-1111-4111-8111-111111111111'
PAIR = (JID, PID)


def attempt():
    return Attempt(JID, PID, 2, 9, 'generate', {'hash': 'a' * 64}, 'session')


class ContainerPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = RuntimeConfig(Path(self.temp.name).resolve())
        self.root = self.config.data_root / 'projects' / PID / 'attempts' / JID / '2-9'
        names = ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base')
        self.layout = Layout(self.root, *(self.root / name for name in names))
        for name in names:
            getattr(self.layout, name).mkdir(parents=True)

    def plan(self, **kwargs):
        kwargs.setdefault('pi_uid', 31601)
        with patch.object(pi_container.sys, 'platform', 'linux'):
            return pi_container.plan_attempt_container(self.config, attempt(), self.layout, **kwargs)

    def test_requires_linux_host(self):
        with patch.object(pi_container.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(ControlError, 'container_plan_requires_linux'):
                pi_container.plan_attempt_container(None, attempt(), self.layout, pi_uid=31601)

    def test_requires_pinned_image_and_valid_limits(self):
        for image in ('ubuntu:latest', 'sha256:abc', 'sha256:' + 'a' * 63, None):
            with self.subTest(image=image), self.assertRaisesRegex(ControlError, 'unpinned_runtime_image'):
                self.plan(image=image)
        for kwargs in ({'pids': 8}, {'pids': 5000}, {'cpus': '3'}, {'memory': '8g'}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ControlError, 'invalid_container_limits'):
                self.plan(**kwargs)

    def test_requires_a_dedicated_uid(self):
        for uid in (9999, 65534, 2 ** 31 - 1, True, '31601', None):
            with self.subTest(uid=uid), self.assertRaisesRegex(ControlError, 'invalid_writer_ids'):
                self.plan(pi_uid=uid)

    def test_layout_directories_must_stay_below_the_attempt_root(self):
        outside = self.root.parent / (self.root.name + '-outside')
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        layout = Layout(self.root, self.layout.inputs, self.layout.task, self.layout.home,
                        outside, self.layout.result, self.layout.audit, self.layout.base)
        with self.assertRaisesRegex(ControlError, 'attempt_layout_mismatch'):
            with patch.object(pi_container.sys, 'platform', 'linux'):
                pi_container.plan_attempt_container(self.config, attempt(), layout, pi_uid=31601)

    def test_argument_vector_encodes_the_isolation_boundary(self):
        plan = self.plan()
        argv = list(plan.argv)
        self.assertEqual(argv[0], 'run')
        self.assertIn('--read-only', argv)
        self.assertEqual(argv[argv.index('--network') + 1], 'none')
        self.assertEqual(argv[argv.index('--user') + 1], '0:0')
        self.assertEqual(argv[argv.index('--cap-drop') + 1], 'ALL')
        added = [argv[i + 1] for i, value in enumerate(argv) if value == '--cap-add']
        self.assertEqual(added, ['CHOWN', 'DAC_OVERRIDE', 'KILL', 'SETGID', 'SETUID'])
        self.assertNotIn('SYS_ADMIN', added)
        self.assertNotIn('--privileged', argv)
        self.assertNotIn('--network', [arg for arg in argv if arg.startswith('host')])
        self.assertIn('no-new-privileges', argv)
        self.assertEqual(argv[-1], pi_container.PINNED_IMAGE)
        self.assertEqual(plan.image, pi_container.PINNED_IMAGE)
        self.assertEqual(plan.name, 'ppt-pi-' + attempt().uid)

    def test_only_declared_mounts_are_present_with_fixed_modes(self):
        argv = list(self.plan().argv)
        volumes = [argv[i + 1] for i, value in enumerate(argv) if value == '--volume']
        self.assertEqual(len(volumes), 6)
        modes = {entry.rsplit(':', 2)[1]: entry.rsplit(':', 1)[1] for entry in volumes}
        self.assertEqual(modes, {'/work/inputs': 'ro', '/work/task': 'ro', '/work/base': 'ro',
                                 '/work/project': 'rw', '/work/result': 'rw', '/work/audit': 'rw'})
        joined = ' '.join(argv)
        for forbidden in ('docker.sock', '/root', '/home', '.env', 'ssh', 'api-db.env'):
            self.assertNotIn(forbidden, joined)
        self.assertNotIn('--env', argv)

    def test_no_environment_or_secret_is_forwarded(self):
        argv = list(self.plan().argv)
        self.assertEqual([value for value in argv if value.startswith('-e') or value == '--env'], [])

    def test_redacted_plan_hides_host_paths(self):
        plan = self.plan()
        redacted = plan.redacted()
        self.assertEqual(len(redacted), len(plan.argv))
        self.assertNotIn(str(self.root), ' '.join(redacted))
        self.assertIn(pi_container.PINNED_IMAGE, redacted)

    def test_digest_must_be_hex_and_reviewed(self):
        with self.assertRaisesRegex(ControlError, 'unpinned_runtime_image'):
            self.plan(image='sha256:' + 'z' * 64)
        with self.assertRaisesRegex(ControlError, 'unreviewed_runtime_image'):
            self.plan(image='sha256:' + 'f' * 64)

    def test_alias_mount_under_same_root_is_still_rejected(self):
        from dataclasses import replace
        with patch.object(pi_container.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(ControlError,'attempt_layout_mismatch'):
                pi_container.plan_attempt_container(self.config,attempt(),
                    replace(self.layout,inputs=self.layout.project),pi_uid=31601)

    def test_linked_ancestor_cannot_be_hidden_by_resolve(self):
        alias=self.config.data_root/'alias'
        try:alias.symlink_to(self.root,target_is_directory=True)
        except OSError as exc:self.skipTest(str(exc))
        with self.assertRaisesRegex(ControlError,'unsafe_attempt_path'):
            pi_container._ordinary_directory(alias/'inputs')

    def test_fixed_offline_entry_has_no_caller_command_or_environment(self):
        code=self.config.data_root/'code'
        entry=code/'ppt_service/images/pi_candidate/domain_smoke.py'
        entry.parent.mkdir(parents=True);entry.write_text('# plan fixture only')
        with patch.object(pi_container.sys,'platform','linux'):
            plan=pi_container.plan_domain_smoke(self.config,attempt(),self.layout,pi_uid=31800,code_root=code)
        self.assertEqual(plan.argv[-2:],(pi_container.PINNED_IMAGE,
            '/code/ppt_service/images/pi_candidate/domain_smoke.py'))
        self.assertIn(str(code.resolve())+':/code:ro',plan.argv)
        self.assertNotIn('--env',plan.argv)

    def test_fixture_admission_is_the_only_configured_mode(self):
        class Settings:
            def __init__(self, fixture):
                self.fixture_mode = fixture

        self.assertIs(pi_container.require_fixture_admission(Settings(True)).fixture_mode, True)
        for value in (False, None, 'true'):
            with self.subTest(value=value), self.assertRaisesRegex(ControlError, 'production_admission_not_configured'):
                pi_container.require_fixture_admission(Settings(value))


if __name__ == '__main__':
    unittest.main()
