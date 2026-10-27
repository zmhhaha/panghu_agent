"""Model Linux chmod ownership checks without adding capsule capabilities."""
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_service.preview import TrustedPreview
from agent_service.runtime import PiRuntime, RuntimeSettings
from agent_service.runtime.profiles import ExecutionProfile


class Profile(ExecutionProfile):
    protected_paths = ('inputs', 'base')

    def __init__(self, shared):
        self.shared_project = shared

    def validate(self, wire):
        raise AssertionError('Not used by this isolation preparation test')

    def build_prompt(self, wire):
        raise AssertionError('Not used by this isolation preparation test')

    def execution_provenance(self, started, finished):
        return {}


class OwnershipTests(unittest.TestCase):
    def test_preview_sets_shared_modes_as_owner_and_preserves_private_subtrees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace, control = root / 'workspace', root / 'control'
            control.mkdir()
            for name in ('project/deck/svg_output', '.home', 'inputs', 'result'):
                (workspace / name).mkdir(parents=True, exist_ok=True)
            shared = workspace / 'project/deck/svg_output/page.svg'
            private = workspace / '.home/models.json'
            source = workspace / 'inputs/source.md'
            for path in (shared, private, source):
                path.write_text('Fixed offline file', encoding='utf-8')
            paths = [workspace, *workspace.rglob('*')]
            owners = {path: (200001, 200001) for path in paths}
            modes = {path: 0o700 if path.is_dir() else 0o600 for path in paths}
            owners[workspace / 'inputs'] = owners[source] = (0, 0)
            modes[workspace / 'inputs'], modes[source] = 0o555, 0o444
            original = {path: (owners[path], modes[path]) for path in
                        (private, private.parent, source, source.parent, workspace / 'result')}
            chmod_paths = []

            def chown(path, uid, gid):
                path = Path(path)
                if path not in owners:
                    # The only new directories are fresh, private preview HOME
                    # and TMP created with mode 0700 before their handoff.
                    self.assertIn(path.name, {'.preview-home', '.preview-tmp'})
                    modes[path] = 0o700
                owners[path] = (uid, gid)

            def chmod(path, mode, **kwargs):
                path = Path(path)
                if owners[path][0] != 0:
                    raise PermissionError(errno.EPERM, 'Root has no CAP_FOWNER')
                modes[path] = mode
                chmod_paths.append(path)

            class StopBeforeNetwork(RuntimeError):
                pass

            preview = TrustedPreview(workspace / 'project/deck', workspace, control,
                                     200001, 200002, 0)
            with patch('agent_service.preview.os.geteuid', return_value=0, create=True), \
                 patch('os.chown', side_effect=chown, create=True), \
                 patch('os.chmod', side_effect=chmod), \
                 patch('agent_service.preview.socket.socket', side_effect=StopBeforeNetwork):
                with self.assertRaises(StopBeforeNetwork):
                    preview.start()
            self.assertEqual((owners[workspace], modes[workspace]), ((200001, 200001), 0o710))
            for path in (workspace / 'project', workspace / 'project/deck', shared.parent):
                self.assertEqual((owners[path], modes[path]), ((200001, 200001), 0o2770))
            self.assertEqual((owners[shared], modes[shared]), ((200001, 200001), 0o660))
            for path, expected in original.items():
                self.assertEqual((owners[path], modes[path]), expected)
                self.assertNotIn(path, chmod_paths)
            for name in ('.preview-home', '.preview-tmp'):
                path = workspace / name
                self.assertEqual((owners[path], modes[path]), ((200002, 200001), 0o700))
                self.assertNotIn(path, chmod_paths)

    def test_modes_are_applied_as_owner_before_agent_handoff_without_fowner(self):
        for shared in (False, True):
            with self.subTest(shared=shared), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                workspace, control = root / 'workspace', root / 'control'
                control.mkdir()
                for name in ('.home', 'project/deck/nested', 'result', 'inputs', 'base'):
                    (workspace / name).mkdir(parents=True, exist_ok=True)
                private = workspace / '.home/models.json'
                project_file = workspace / 'project/deck/nested/source.md'
                source = workspace / 'inputs/source.md'
                base = workspace / 'base/checkpoint.json'
                for path in (private, project_file, source, base):
                    path.write_text('Fixed offline file', encoding='utf-8')
                paths = [workspace, *workspace.rglob('*')]
                owners = {path: (0, 0) for path in paths}
                modes = {path: 0o700 if path.is_dir() else 0o600 for path in paths}
                for path in (workspace / 'inputs', workspace / 'base'):
                    modes[path] = 0o555
                for path in (source, base):
                    modes[path] = 0o444
                changed = set()

                def chown(path, uid, gid):
                    path = Path(path)
                    owners[path] = (uid, gid)
                    changed.add(path)

                def chmod(path, mode):
                    path = Path(path)
                    # Linux CAP_DAC_OVERRIDE bypasses file access checks but
                    # chmod requires ownership or the omitted CAP_FOWNER.
                    if owners[path][0] != 0:
                        raise PermissionError(errno.EPERM, 'Root has no CAP_FOWNER')
                    modes[path] = mode
                    changed.add(path)

                runtime = PiRuntime(RuntimeSettings(control_dir=control), profile=Profile(shared))
                with patch.object(runtime, '_stop_uid') as stop, \
                     patch('os.chown', side_effect=chown, create=True), \
                     patch('os.chmod', side_effect=chmod):
                    group, launch, command = runtime._prepare_cgroup(workspace, ['fixed-command'], 200001)
                stop.assert_called_once_with(200001)
                self.assertIsNone(group)
                self.assertEqual(owners[workspace], (200001, 200001))
                self.assertEqual(modes[workspace], 0o710 if shared else 0o700)
                for path in (workspace / 'project', workspace / 'project/deck', project_file.parent):
                    self.assertEqual(owners[path], (200001, 200001))
                    self.assertEqual(modes[path], 0o2770 if shared else 0o700)
                self.assertEqual(owners[project_file], (200001, 200001))
                self.assertEqual(modes[project_file], 0o660 if shared else 0o600)
                self.assertEqual(owners[private], (200001, 200001))
                self.assertEqual(modes[private], 0o600)
                self.assertEqual(modes[private.parent], 0o700)
                for path in (workspace / 'inputs', workspace / 'base', source, base):
                    self.assertNotIn(path, changed)
                    self.assertEqual(owners[path], (0, 0))
                    self.assertEqual(modes[path], 0o555 if path.is_dir() else 0o444)
                config = json.loads(launch.read_text(encoding='utf-8'))
                self.assertEqual(config['uid'], 200001)
                self.assertEqual(config['gid'], 200001)
                self.assertEqual(config['umask'], 0o007 if shared else 0o077)


if __name__ == '__main__':
    unittest.main()
