"""Preview launch tests. Offline validation plus a real Linux privilege drop."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ppt_service.control import preview_launch, stop_gate
from ppt_service.control.execution import Attempt, ControlError, Layout
from ppt_service.control.pi_uid import PiUIDAllocator, UIDScope

PID = '11111111-1111-4111-8111-111111111111'
JID = '22222222-2222-4222-8222-222222222222'
PREVIEW_UID = 31301
ABS = Path(tempfile.gettempdir()).resolve()


def attempt():
    return Attempt(JID, PID, 1, 2, 'generate', {'hash': 'a' * 64}, 'session')


def layout(root):
    names = ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base')
    return Layout(root, *(root / name for name in names))


def deck(root):
    project = root / 'project' / 'deck'
    project.mkdir(parents=True)
    for name in ('design_spec.md', 'spec_lock.md'):
        (project / name).write_text('fixture', encoding='utf-8')
    return project


class FakeLedger:
    def __init__(self, uid=PREVIEW_UID):
        self.uid, self.calls = uid, []

    def verify(self, **binding):
        self.calls.append(binding)
        return self.uid if binding['role'] == 'preview' else 31300


class PreviewSpecTests(unittest.TestCase):
    def test_rejects_malformed_specs(self):
        good = dict(launcher=ABS / 'l', python=ABS / 'l', server=ABS / 'l')
        cases = [dict(good, launcher=Path('relative')),
                 dict(good, python='string'),
                 dict(good, server=None),
                 dict(good, port=80), dict(good, port=70000), dict(good, port=True),
                 dict(good, lifetime=5), dict(good, lifetime=86401),
                 dict(good, ready_timeout=0), dict(good, ready_timeout=121),
                 dict(good, max_processes=1), dict(good, max_processes=4097)]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ControlError):
                preview_launch.PreviewSpec(**case).validate()
        self.assertIs(preview_launch.PreviewSpec(**good).validate().__class__,
                      preview_launch.PreviewSpec)

    @unittest.skipUnless(sys.platform == 'linux', 'deployed paths are Linux image paths')
    def test_deployed_spec_points_at_fixed_absolute_paths(self):
        spec = preview_launch.PreviewSpec.deployed().validate()
        self.assertTrue(spec.launcher.is_absolute() and spec.launcher.is_file())
        self.assertEqual(spec.python, preview_launch.PREVIEW_PYTHON)
        self.assertEqual(spec.server, preview_launch.PREVIEW_SERVER)
        self.assertEqual(spec.port, preview_launch.PREVIEW_PORT)


class PreviewLaunchRefusalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        self.layout = layout(self.root)
        self.layout.project.mkdir(parents=True)
        self.project = deck(self.root)

    def test_requires_linux_root_before_touching_the_ledger(self):
        ledger = FakeLedger()
        with patch.object(preview_launch.sys, 'platform', 'win32'), \
                patch.object(stop_gate, 'uid_processes') as scan:
            with self.assertRaisesRegex(ControlError, 'preview_supervision_requires_linux_root'):
                preview_launch.launch_preview(attempt(), self.layout, ledger, self.project,
                                              spec=preview_launch.PreviewSpec(
                                                  ABS / 'l', ABS / 'l', ABS / 'l'))
        self.assertEqual(ledger.calls, [])
        scan.assert_not_called()

    def test_project_must_be_a_marked_child_of_the_attempt(self):
        with self.assertRaisesRegex(ControlError, 'preview_project_outside_attempt'):
            preview_launch._project(self.layout.project, self.layout)
        outside = self.root / 'outside'
        outside.mkdir()
        for name in ('design_spec.md', 'spec_lock.md'):
            (outside / name).write_text('fixture', encoding='utf-8')
        with self.assertRaisesRegex(ControlError, 'preview_project_outside_attempt'):
            preview_launch._project(outside, self.layout)

    def test_project_markers_must_be_ordinary_files(self):
        (self.project / 'spec_lock.md').unlink()
        with self.assertRaises(ControlError):
            preview_launch._project(self.project, self.layout)
        (self.project / 'spec_lock.md').mkdir()
        with self.assertRaises(ControlError):
            preview_launch._project(self.project, self.layout)
    def test_uid_must_be_reserved_int_and_distinct_from_pi(self):
        for uid in (9999, 65534, '31301', None, True, 2**31 - 1):
            with self.subTest(uid=uid), self.assertRaisesRegex(ControlError, 'invalid_preview_uid'):
                preview_launch._preview_uid(FakeLedger(uid), attempt())
        with self.assertRaisesRegex(ControlError, 'invalid_pi_uid'):
            preview_launch._preview_uid(FakeLedger(PREVIEW_UID), attempt(), PREVIEW_UID)
        with self.assertRaisesRegex(ControlError, 'preview_uid_shares_pi_identity'):
            preview_launch._preview_uid(FakeLedger(31300), attempt())
        with self.assertRaisesRegex(ControlError, 'invalid_uid_ledger'):
            preview_launch._preview_uid(object(), attempt())
        ledger = FakeLedger()
        self.assertEqual(preview_launch._preview_uid(ledger, attempt()), PREVIEW_UID)
        self.assertEqual(ledger.calls, [dict(project_id=PID, job_id=JID, attempt=1, token=2,
                                            role=role) for role in ('preview', 'pi')])

    def test_writability_follows_owner_group_then_other_bits(self):
        class Info:
            def __init__(self, uid, gid, mode):
                self.st_uid, self.st_gid, self.st_mode = uid, gid, mode
        writable = preview_launch._writable_by
        self.assertTrue(writable(Info(PREVIEW_UID, 0, stat.S_IFDIR | 0o200), PREVIEW_UID))
        self.assertFalse(writable(Info(PREVIEW_UID, 0, stat.S_IFDIR | 0o000), PREVIEW_UID))
        self.assertTrue(writable(Info(0, PREVIEW_UID, stat.S_IFDIR | 0o020), PREVIEW_UID))
        self.assertFalse(writable(Info(0, PREVIEW_UID, stat.S_IFDIR | 0o002), PREVIEW_UID))
        self.assertTrue(writable(Info(0, 0, stat.S_IFDIR | 0o002), PREVIEW_UID))
        self.assertFalse(writable(Info(0, 0, stat.S_IFDIR | 0o555), PREVIEW_UID))

    def test_traversal_follows_execute_bits_only(self):
        class Info:
            def __init__(self, uid, gid, mode):
                self.st_uid, self.st_gid, self.st_mode = uid, gid, mode
        traversable = preview_launch._traversable_by
        self.assertTrue(traversable(Info(0, 0, stat.S_IFDIR | 0o711), PREVIEW_UID))
        self.assertFalse(traversable(Info(0, 0, stat.S_IFDIR | 0o600), PREVIEW_UID))
        self.assertTrue(traversable(Info(PREVIEW_UID, 0, stat.S_IFDIR | 0o100), PREVIEW_UID))
        self.assertTrue(traversable(Info(0, PREVIEW_UID, stat.S_IFDIR | 0o010), PREVIEW_UID))
        self.assertFalse(traversable(Info(0, 0, stat.S_IFDIR | 0o004), PREVIEW_UID))

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory execute bits')
    def test_untraversable_attempt_root_is_refused_before_spawn(self):
        os.chmod(self.layout.root, 0o700)
        for name in ('l', 'p', 's'):
            (self.root / name).write_text('fixture', encoding='utf-8')
        with patch.object(preview_launch, '_require_linux_root'), \
                patch.object(preview_launch, '_preview_uid', return_value=PREVIEW_UID), \
                patch.object(preview_launch, '_writable_by', return_value=True), \
                patch.object(preview_launch.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ControlError, 'preview_path_not_traversable'):
                preview_launch.launch_preview(
                    attempt(), self.layout, FakeLedger(), self.project,
                    spec=preview_launch.PreviewSpec(self.root / 'l', self.root / 'p', self.root / 's'))
            spawn.assert_not_called()

    def test_hardlinked_marker_is_rejected_without_touching_sentinel(self):
        sentinel = self.root / 'sentinel'
        sentinel.write_bytes(b'private evidence')
        marker = self.project / 'spec_lock.md'
        marker.unlink()
        os.link(sentinel, marker)
        before = sentinel.stat()
        with self.assertRaises(ControlError):
            preview_launch._project(self.project, self.layout)
        self.assertEqual(sentinel.read_bytes(), b'private evidence')
        self.assertEqual(sentinel.stat().st_mode, before.st_mode)

    def test_symlink_ancestor_is_rejected(self):
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.layout.project, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'directory symlinks unavailable: {exc}')
        from dataclasses import replace
        with self.assertRaises(ControlError):
            preview_launch._project(alias / 'deck', replace(self.layout, project=alias))

    def test_untrusted_runtime_parent_refused_before_mutation(self):
        from types import SimpleNamespace
        unsafe = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o777)
        with patch.object(preview_launch, '_safe_directory_chain'), \
                patch.object(Path, 'lstat', return_value=unsafe), \
                patch.object(preview_launch, '_mutate') as mutate:
            with self.assertRaisesRegex(ControlError, 'unsafe_preview_runtime_dir'):
                preview_launch._private_dir(self.root / 'runtime', PREVIEW_UID)
            mutate.assert_not_called()

    def test_audit_parent_is_traverse_only_before_spawn(self):
        binary = self.root / 'fixture-binary'
        binary.write_text('fixture')
        with patch.object(preview_launch, '_require_linux_root'), \
                patch.object(preview_launch, '_preview_uid', return_value=PREVIEW_UID), \
                patch.object(preview_launch, '_writable_by', return_value=True), \
                patch.object(preview_launch, '_require_traversal'), \
                patch.object(preview_launch, '_trusted_parent'), \
                patch.object(preview_launch, '_mutate') as mutate, \
                patch.object(preview_launch, '_private_dir', side_effect=lambda path, uid: path), \
                patch.object(preview_launch, '_port_is_free'), \
                patch.object(preview_launch.subprocess, 'Popen'), \
                patch.object(preview_launch, 'wait_ready'):
            preview_launch.launch_preview(attempt(), self.layout, FakeLedger(), self.project,
                                          spec=preview_launch.PreviewSpec(binary, binary, binary))
        mutate.assert_called_once_with(self.layout.audit, mode=0o711)

    def test_environment_has_no_inherited_secrets(self):
        env = preview_launch._environment(ABS / 'h', ABS / 't')
        self.assertEqual(set(env), {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL',
                                    'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED',
                                    'PLAYWRIGHT_BROWSERS_PATH'})
        self.assertEqual(env['HOME'], str(ABS / 'h'))
        self.assertNotIn('API_KEY', json.dumps(env))

    def test_config_carries_fixed_command_and_identity(self):
        spec = preview_launch.PreviewSpec(ABS / 'launch.py', ABS / 'python',
                                          ABS / 'server.py', port=8791, lifetime=60)
        config = preview_launch._config(self.project, PREVIEW_UID, spec, (ABS / 'secret',))
        self.assertEqual(config['uid'], PREVIEW_UID)
        self.assertEqual(config['gid'], PREVIEW_UID)
        self.assertIsNone(config['cgroup'])
        self.assertEqual(config['cwd'], str(self.project))
        self.assertEqual(config['denied_paths'], [str(ABS / 'secret')])
        self.assertEqual(config['command'], [str(ABS / 'python'), str(ABS / 'server.py'),
                                             str(self.project), '--live', '--no-browser',
                                             '--port', '8791', '--timeout', '60'])
        self.assertEqual(config['controller_pid'], os.getpid())

    def test_config_write_is_exclusive_and_bounded(self):
        target = self.root / 'preview-launch.json'
        preview_launch._write_config(target, {'a': 1})
        self.assertEqual(json.loads(target.read_text()), {'a': 1})
        with self.assertRaises(FileExistsError):
            preview_launch._write_config(target, {'a': 2})
        with patch.object(preview_launch, 'MAX_LAUNCH_CONFIG', 8):
            with self.assertRaisesRegex(ControlError, 'preview_config_too_large'):
                preview_launch._write_config(self.root / 'large.json', {'key': 'value' * 8})

    def test_denied_paths_must_be_absolute_paths(self):
        ledger = FakeLedger()
        for name in ('launch.py', 'python', 'server.py'):
            (self.root / name).write_text('fixture', encoding='utf-8')
        with patch.object(preview_launch, '_require_linux_root'), \
                patch.object(preview_launch, '_preview_uid', return_value=PREVIEW_UID), \
                patch.object(preview_launch, '_writable_by', return_value=True), \
                patch.object(preview_launch, '_require_traversal'):
            for value in ('/not/a/path-object', Path('relative'), 7):
                with self.subTest(value=value), self.assertRaisesRegex(ControlError, 'invalid_denied_path'):
                    preview_launch.launch_preview(attempt(), self.layout, ledger, self.project,
                                                  spec=preview_launch.PreviewSpec(
                                                      self.root / 'launch.py', self.root / 'python',
                                                      self.root / 'server.py'),
                                                  denied_paths=(value,))


PINNED = Path(preview_launch.PREVIEW_SERVER).is_file() and Path(preview_launch.PREVIEW_PYTHON).is_file()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0 and PINNED,
                     'requires the pinned official Linux image and a root controller')
class PreviewLaunchLinuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        os.chmod(self.root, 0o711)
        self.layout = layout(self.root)
        for name in ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base'):
            getattr(self.layout, name).mkdir(parents=True, mode=0o711)
        self.project = deck(self.root)
        os.chmod(self.project, 0o711)
        scope = UIDScope('ppt-test', 'offline', 'dedicated-hosts', 31300, 31399)
        self.ledger = PiUIDAllocator.initialize(self.root / 'ledger', scope)
        self.attempt = attempt()
        self.pi_uid = self.ledger.allocate(project_id=PID, job_id=JID, attempt=1, token=2,
                                           role='pi')
        self.uid = self.ledger.allocate(project_id=PID, job_id=JID, attempt=1, token=2,
                                        role='preview')
        self.addCleanup(stop_gate.stop_uids, (self.uid,))
        self.addCleanup(self._reclaim)
        for target in (self.project, self.project / 'design_spec.md', self.project / 'spec_lock.md'):
            os.chown(target, self.uid, self.uid)

    def _reclaim(self):
        # Root without CAP_FOWNER cannot chmod another UID's tree; take ownership
        # back after the writers are stopped so temporary cleanup can proceed.
        for root, directories, files in os.walk(self.root):
            for name in directories + files:
                try:
                    os.chown(os.path.join(root, name), 0, 0)
                except FileNotFoundError:
                    continue
        os.chown(self.root, 0, 0)

    def test_real_preview_runs_dropped_and_stop_gate_reaps_it(self):
        spec = preview_launch.PreviewSpec(preview_launch.PreviewSpec.deployed().launcher,
                                          preview_launch.PREVIEW_PYTHON,
                                          preview_launch.PREVIEW_SERVER, port=8791, lifetime=60)
        handle = preview_launch.launch_preview(self.attempt, self.layout, self.ledger,
                                              self.project, spec=spec)
        try:
            self.assertEqual(handle.uid, self.uid)
            self.assertEqual(set(preview_launch._process_uid(handle.pid)), {self.uid})
            self.assertEqual(stat.S_IMODE(handle.config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.layout.audit.stat().st_mode), 0o711)
            for name in ('preview-home', 'preview-tmp'):
                self.assertEqual(stat.S_IMODE((self.layout.audit / name).stat().st_mode), 0o700)
            self.assertEqual(stop_gate.uid_processes(self.uid), [handle.pid])
            health = preview_launch._health(spec.port)
            self.assertEqual(health['pid'], handle.pid)
            self.assertEqual(health['project'], str(self.project))
        finally:
            handle.stop(grace=10)
        self.assertEqual(handle.process.returncode, -9)
        self.assertEqual(stop_gate.uid_processes(self.uid), [])
        with self.assertRaises(OSError):
            preview_launch._health(spec.port)

    def test_unwritable_project_is_refused_before_spawn(self):
        os.chown(self.project, 0, 0)
        os.chmod(self.project, 0o555)
        spec = preview_launch.PreviewSpec(preview_launch.PreviewSpec.deployed().launcher,
                                          preview_launch.PREVIEW_PYTHON,
                                          preview_launch.PREVIEW_SERVER, port=8792, lifetime=60)
        with patch.object(preview_launch.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ControlError, 'preview_project_not_writable'):
                preview_launch.launch_preview(self.attempt, self.layout, self.ledger,
                                              self.project, spec=spec)
            spawn.assert_not_called()
        self.assertEqual(stop_gate.uid_processes(self.uid), [])


if __name__ == '__main__':
    unittest.main()
