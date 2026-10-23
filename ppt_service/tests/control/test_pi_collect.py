import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from ppt_service.control.execution import Attempt, ControlError, Layout, RuntimeConfig, StopProof
from ppt_service.control import pi_collect as collect


class PiCollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).absolute()
        self.config = RuntimeConfig(self.root)
        self.a = Attempt('11111111-1111-4111-8111-111111111111',
                         '22222222-2222-4222-8222-222222222222', 1, 2,
                         'generate', {'hash': 'a' * 64}, 'unused')
        root = self.root / f'projects/{self.a.project_id}/attempts/{self.a.job_id}/1-2'
        names = ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base')
        self.layout = Layout(root, *(root / name for name in names))
        for name in names: getattr(self.layout, name).mkdir(parents=True)
        self.source = self.layout.project / 'deck' / 'notes.md'
        self.source.parent.mkdir()
        self.source.write_bytes(b'notes')
        self.proof = StopProof(self.a.uid, True)
        self.cid = '33333333-3333-4333-8333-333333333333'
        self.parent = self.root / f'projects/{self.a.project_id}/candidates'
        self.target = self.parent / self.cid

    def tearDown(self):
        for folder, dirs, files in os.walk(self.root):
            Path(folder).chmod(0o700)
            for name in files:
                path = Path(folder) / name
                if not path.is_symlink(): path.chmod(0o600)
        self.tmp.cleanup()

    def freeze(self, **kwargs):
        return collect.freeze_candidate(self.config, self.a, self.layout, self.proof, candidate_id=self.cid, **kwargs)

    def test_bad_stop_proofs_write_nothing(self):
        for proof in (StopProof(self.a.uid, False), StopProof('wrong', True),
                      StopProof(self.a.uid, 1), {'uid': self.a.uid, 'all_writers_stopped': True}):
            with self.subTest(proof=proof), self.assertRaises(ControlError):
                collect.freeze_candidate(self.config, self.a, self.layout, proof)
            self.assertFalse(self.parent.exists())

    def test_layout_mismatch_writes_nothing(self):
        with self.assertRaisesRegex(ControlError, 'attempt_layout_mismatch'):
            collect.freeze_candidate(self.config, self.a, replace(self.layout, result=self.layout.project), self.proof)
        self.assertFalse(self.parent.exists())

    def test_copy_unchanged_source_and_controller_index(self):
        before = self.source.stat(); frozen = self.freeze(); after = self.source.stat()
        self.assertEqual((frozen.path / 'project/deck/notes.md').read_bytes(), b'notes')
        self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns), (after.st_ino, after.st_mode, after.st_mtime_ns))
        self.assertEqual(frozen.indexes['project'], {'files': [{'path': 'deck/notes.md', 'bytes': 5,
            'sha256': hashlib.sha256(b'notes').hexdigest()}], 'bytes': 5})
        manifest = json.loads((frozen.path / 'snapshot.json').read_text())
        self.assertEqual(manifest, frozen.manifest)
        self.assertEqual(manifest['uid'], self.a.uid)
        self.assertNotIn('version_id', manifest)
        self.assertNotIn('accepted', manifest)
        self.assertFalse((frozen.path / 'checkpoint.json').exists())

    def test_home_and_staged_trees_untouched(self):
        for name in ('home', 'inputs', 'task', 'base', 'audit'):
            (getattr(self.layout, name) / 'state.sqlite').write_bytes(b'secret')
        frozen = self.freeze()
        self.assertEqual(set(p.name for p in frozen.path.iterdir()), {'project', 'result', 'snapshot.json'})
        for name in ('home', 'inputs', 'task', 'base', 'audit'):
            self.assertEqual((getattr(self.layout, name) / 'state.sqlite').read_bytes(), b'secret')

    def test_native_paths_rejected(self):
        for key in ('state.sqlite', 'trace.jsonl', '.env', '.pi/auth.json', '.hermes/state.json',
                    'runtime/state.json', 'credentials.json', 'secrets.yaml', 'HOME/notes.md', 'secret.pem'):
            with self.subTest(key=key):
                path = self.layout.project / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
                try:
                    with self.assertRaises(ControlError): self.freeze()
                    self.assertFalse(self.parent.exists())
                finally:
                    path.unlink()
                    if path.parent != self.layout.project: path.parent.rmdir()

    def test_hardlink_rejected(self):
        os.link(self.source, self.layout.project / 'alias.md')
        with self.assertRaises(ControlError): self.freeze()
        self.assertFalse(self.parent.exists())

    def test_symlink_rejected(self):
        try: (self.layout.project / 'alias.md').symlink_to(self.source)
        except OSError as exc: self.skipTest(f'Host cannot create symlinks: {exc}')
        with self.assertRaises(ControlError): self.freeze()
        self.assertFalse(self.parent.exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO')
    def test_fifo_rejected_without_open(self):
        os.mkfifo(self.layout.project / 'pipe.md')
        with self.assertRaises(ControlError): self.freeze()
        self.assertFalse(self.parent.exists())

    def test_file_and_aggregate_bounds(self):
        with self.assertRaises(ControlError): self.freeze(max_file_bytes=4)
        (self.layout.result / 'notes.md').write_bytes(b'abc')
        with self.assertRaises(ControlError): self.freeze(max_files=1)
        self.config = replace(self.config, max_snapshot_bytes=7)
        with self.assertRaises(ControlError): self.freeze()
        self.assertFalse(self.parent.exists())

    def test_quota_preflight(self):
        self.config = replace(self.config, max_storage_bytes=1)
        with self.assertRaises(ControlError): self.freeze()
        self.assertFalse(self.parent.exists())

    def test_collision_never_quarantines_existing_target(self):
        self.target.mkdir(parents=True)
        sentinel = self.target / 'owned.txt'; sentinel.write_bytes(b'existing')
        with self.assertRaises(FileExistsError): self.freeze()
        self.assertEqual(sentinel.read_bytes(), b'existing')
        self.assertEqual(list(self.parent.iterdir()), [self.target])

    def test_source_race_quarantines_only_new_target(self):
        original = collect.copy_file
        def race(*args, **kwargs):
            result = original(*args, **kwargs)
            self.source.write_bytes(b'other')
            return result
        with patch.object(collect, 'copy_file', side_effect=race):
            with self.assertRaises(ControlError): self.freeze()
        self.assertFalse(self.target.exists())
        quarantines = list(self.parent.iterdir())
        self.assertEqual(len(quarantines), 1)
        self.assertTrue(quarantines[0].name.startswith('.quarantine-' + self.cid))
        self.assertFalse((quarantines[0] / 'snapshot.json').exists())
        self.assertEqual(self.source.read_bytes(), b'other')

    def test_source_hash_error_quarantines_new_target(self):
        with patch.object(collect, 'verify_file', side_effect=ControlError('source_hash_mismatch')):
            with self.assertRaisesRegex(ControlError, 'source_hash_mismatch'): self.freeze()
        self.assertFalse(self.target.exists())
        self.assertEqual(len(list(self.parent.glob('.quarantine-*'))), 1)

    def test_validation_mapping_uses_measured_rows(self):
        raw = {'phase': self.a.phase, 'input_snapshot_sha256': self.a.snapshot['hash'],
               'project_root': 'deck', 'slides': [],
               'artifacts': [{'path': 'notes.md', 'role': 'notes', 'bytes': 999, 'sha256': '0' * 64}]}
        (self.layout.result / 'manifest.json').write_text(json.dumps(raw))
        frozen = self.freeze(); mapped = frozen.validation_manifest()
        self.assertEqual(mapped['artifacts'][0]['bytes'], 5)
        self.assertEqual(mapped['artifacts'][0]['role'], 'notes')
        self.assertEqual(json.loads((frozen.path / 'result/manifest.json').read_text()), raw)

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership/modes')
    def test_controller_ownership_and_private_modes(self):
        frozen = self.freeze()
        for folder, dirs, files in os.walk(frozen.path):
            self.assertEqual(stat.S_IMODE(Path(folder).stat().st_mode), 0o500)
            self.assertEqual(Path(folder).stat().st_uid, os.geteuid())
            for name in files:
                info = (Path(folder) / name).stat()
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o400)
                self.assertEqual(info.st_uid, os.geteuid())

    def test_path_alias_rejection_without_creating_devices(self):
        for key in ('NUL.txt', 'COM1.svg', 'deck./a.md', 'deck /a.md', 'deck~1/a.md',
                    '../a.md', '/a.md', 'deck/a:stream.md', 'deck\\a.md'):
            with self.subTest(key=key), self.assertRaises(ControlError): collect._artifact(key)

    def test_source_directory_escape_rejected(self):
        external = self.root / 'external'; external.mkdir()
        (external / 'notes.md').write_bytes(b'outside')
        try: (self.layout.project / 'outside').symlink_to(external, target_is_directory=True)
        except OSError as exc: self.skipTest(f'Host cannot create directory symlinks: {exc}')
        with self.assertRaises(ControlError): self.freeze()
        self.assertFalse(self.parent.exists())

    @unittest.skipUnless(os.name == 'posix', 'case-sensitive POSIX fixture')
    def test_case_directory_alias_rejected(self):
        (self.layout.project / 'Deck').mkdir()
        with self.assertRaisesRegex(ControlError, 'duplicate_project_path'): self.freeze()
        self.assertFalse(self.parent.exists())


    def test_measurements_are_detached_from_returned_mappings(self):
        frozen = self.freeze(); original = frozen.manifest
        indexes = frozen.indexes
        indexes['project']['files'][0]['sha256'] = '0' * 64
        indexes['project']['bytes'] = 999
        exposed = frozen.manifest
        exposed['phase'] = 'revise'
        exposed['indexes']['project']['files'].clear()
        self.assertEqual(frozen.manifest, original)
        self.assertEqual(frozen.indexes, original['indexes'])

    def test_constructor_detaches_caller_owned_mappings(self):
        frozen = self.freeze(); manifest = frozen.manifest; indexes = manifest['indexes']
        independent = collect.FrozenCandidate(frozen.path, indexes, manifest)
        manifest['phase'] = 'revise'; indexes['project']['files'].clear()
        self.assertEqual(independent.manifest, frozen.manifest)

    def test_manifest_encode_limit_quarantines_before_manifest_write(self):
        with patch.object(collect, 'MAX_MANIFEST_BYTES', 128):
            with self.assertRaisesRegex(ControlError, 'manifest_limit'): self.freeze()
        self.assertFalse(self.target.exists())
        quarantines = list(self.parent.glob('.quarantine-*'))
        self.assertEqual(len(quarantines), 1)
        self.assertFalse((quarantines[0] / 'snapshot.json').exists())

    def test_manifest_limit_counts_utf8_bytes(self):
        manifest = {'indexes': {}, 'text': '\u4e2d' * 40}
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with patch.object(collect, 'MAX_MANIFEST_BYTES', len(raw)):
            with self.assertRaisesRegex(ControlError, 'manifest_limit'):
                collect.FrozenCandidate(self.target, {}, manifest)


if __name__ == '__main__': unittest.main()
