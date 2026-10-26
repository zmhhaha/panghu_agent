"""Offline filesystem-only tests; no DB, model, network, subprocess or services."""
import copy
import hashlib
import json
import os
import stat
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ppt_service.control.execution import Attempt, ControlError, RuntimeConfig
from ppt_service.control import pi_staging as pi


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def rehash(snapshot):
    core = {key: snapshot[key] for key in ('project_id', 'settings', 'title', 'assets')}
    snapshot['hash'] = sha(json.dumps(core, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
    return snapshot


class PiStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'data'
        self.config = RuntimeConfig(self.root)
        self.stager = pi.PiStager(self.config, 'runtime-pinned', 'ppt-skill-pinned')
        self.pid = str(uuid.uuid4())
        self.snapshot = rehash({'project_id': self.pid, 'settings': {'page_count': 3},
                                'title': 'Public text fixture', 'assets': []})
        self.a = Attempt(str(uuid.uuid4()), self.pid, 1, 7, 'plan_initial', self.snapshot, 'unused-pi-session')

    def asset(self, raw=b'Real immutable source text\n'):
        aid = str(uuid.uuid4()); key = f'projects/{self.pid}/assets/{aid}/original'
        path = self.root/key; path.parent.mkdir(parents=True); path.write_bytes(raw)
        self.snapshot['assets'].append({'id': aid, 'storage_key': key, 'sha256': sha(raw),
                                       'media_type': 'text/markdown', 'bytes': len(raw)})
        rehash(self.snapshot)
        return path

    def confirmation(self, stage):
        direction = {'id': 'A', 'name': 'Public direction', 'page_plan': {'pages': [{'title': 'One'}]}}
        value = {'channel': 'website', 'plan_revision': stage, 'input_snapshot_sha256': self.snapshot['hash'],
                 'selection': {'design_mode': 'free_design', 'template_id': None} if stage == 1 else {'direction_id': 'A'},
                 'outline': {'pages': [{'title': 'One'}]}, 'design': {'directions': [direction]}}
        if stage == 2:
            value['selected_direction'] = copy.deepcopy(direction)
        return value

    def checkpoint(self, phase='generate', source_phase=None):
        source_phase = source_phase or {'plan_options': 'plan_initial', 'prepare': 'plan_options',
                                       'generate': 'prepare', 'revise': 'generate'}[phase]
        context = {'stage1_confirmation': self.confirmation(1)}
        if phase != 'plan_options':
            context['stage2_confirmation'] = self.confirmation(2)
        inherited = {} if phase == 'plan_options' else {'stage1_confirmation': copy.deepcopy(context['stage1_confirmation'])}
        if phase in ('generate', 'revise'):
            inherited = copy.deepcopy(context)
        self.snapshot['control_context'] = context
        if phase in ('plan_options', 'prepare'):
            c = context['stage1_confirmation' if phase == 'plan_options' else 'stage2_confirmation']
            self.snapshot['plan_revision'] = c['plan_revision']
            self.snapshot['confirmation_selection'] = copy.deepcopy(c['selection'])
        version = str(uuid.uuid4()) if source_phase in ('generate', 'revise') else None
        key = f'projects/{self.pid}/checkpoints/{uuid.uuid4()}'
        source = self.root/key; (source/'project/deck').mkdir(parents=True)
        raw = b'# Artifact replay\nThis is public test material.\n'
        (source/'project/deck/notes.md').write_bytes(raw)
        manifest = {'schema_version': 1, 'engine': 'pi', 'profile': 'ppt-master',
                    'checkpoint_format': 'artifact-replay-v1', 'runtime_revision': 'runtime-pinned',
                    'skill_bundle_revision': 'ppt-skill-pinned', 'project_id': self.pid,
                    'input_snapshot_sha256': self.snapshot['hash'], 'source_job_id': str(uuid.uuid4()),
                    'source_attempt': 1, 'source_token': 8, 'phase': source_phase, 'project_root': 'deck',
                    'control_context': inherited, 'version_id': version,
                    'project': {'files': [{'path': 'deck/notes.md', 'bytes': len(raw), 'sha256': sha(raw)}], 'bytes': len(raw)}}
        self.save(source, manifest)
        return replace(self.a, phase=phase, checkpoint_key=key, base_version_id=version if phase == 'revise' else None), source, manifest

    def save(self, source, manifest):
        (source/'checkpoint.json').write_text(json.dumps(manifest), encoding='utf-8')

    def rejected(self, a, manifest=None):
        with self.assertRaises((ControlError, OSError)):
            self.stager.stage(a, manifest)
        self.assertFalse(self.stager.layout(a).root.exists())

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'Symlink creation unavailable on this host: {exc}')

    def test_initial_real_source_and_readonly_inventory(self):
        source = self.asset(); before = source.read_bytes()
        layout, inventory = self.stager.stage(self.a)
        staged = layout.inputs/inventory['sources'][0]['path']
        self.assertEqual(staged.read_bytes(), before)
        self.assertFalse(os.path.samefile(source, staged))
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(staged.stat().st_mode & stat.S_IWUSR)
        self.assertEqual(list(layout.home.iterdir()), [])
        self.assertEqual(list(layout.base.iterdir()), [])
        if os.name == 'posix':
            self.assertEqual(staged.stat().st_uid, os.getuid())

    def test_all_legal_lineages_and_independent_clones(self):
        for phase, source_phase in [('plan_options', 'plan_initial'), ('prepare', 'plan_options'),
                                    ('generate', 'prepare'), ('revise', 'generate'), ('revise', 'revise')]:
            with self.subTest(phase=phase, source_phase=source_phase):
                a, source, manifest = self.checkpoint(phase, source_phase)
                a = replace(a, job_id=str(uuid.uuid4()))
                before = (source/'project/deck/notes.md').read_bytes()
                layout, _ = self.stager.stage(a, manifest)
                base = layout.base/'project/deck/notes.md'; work = layout.project/'deck/notes.md'
                self.assertEqual(base.read_bytes(), before); self.assertEqual(work.read_bytes(), before)
                self.assertFalse(os.path.samefile(base, work))
                self.assertFalse(os.path.samefile(work, source/'project/deck/notes.md'))
                self.assertFalse(base.stat().st_mode & stat.S_IWUSR)
                work.write_bytes(b'Independent edit')
                self.assertEqual(base.read_bytes(), before)
                self.assertEqual((source/'project/deck/notes.md').read_bytes(), before)
                self.assertEqual(list(layout.home.iterdir()), [])

    def test_finish_inputs_delegates_payload_and_preserves_confirmations(self):
        a, _, manifest = self.checkpoint('prepare')
        layout, _ = self.stager.stage(a, manifest)
        parsed = {'input_snapshot_sha256': a.snapshot['hash'], 'sources': []}
        task = self.stager.finish_inputs(a, layout, parsed)
        self.assertEqual(task['stage2_confirmation'], a.snapshot['control_context']['stage2_confirmation'])
        self.assertEqual(task['stage1_confirmation'], a.snapshot['control_context']['stage1_confirmation'])
        self.assertEqual(task['requirements']['source_inventory'], '/work/inputs/inventory.json')
        self.assertFalse((layout.inputs/'parsed.json').stat().st_mode & stat.S_IWUSR)

    def test_finish_inputs_snapshot_guard(self):
        layout, _ = self.stager.stage(self.a)
        with self.assertRaisesRegex(ControlError, 'parse_snapshot_mismatch'):
            self.stager.finish_inputs(self.a, layout, {'input_snapshot_sha256': '0'*64})
        self.assertFalse((layout.inputs/'parsed.json').exists())

    def test_initial_checkpoint_and_implicit_continuation_rejected(self):
        self.rejected(replace(self.a, phase='prepare'))
        self.rejected(self.a, {})
        a, _, m = self.checkpoint()
        self.rejected(replace(a, phase='plan_initial'), m)

    def test_exact_independent_expected_manifest_required(self):
        a, source, m = self.checkpoint()
        self.rejected(a)
        disk = copy.deepcopy(m); disk['source_token'] += 1; self.save(source, disk)
        self.rejected(a, m)

    def test_typed_identity_revision_and_legacy_rejections(self):
        a, source, original = self.checkpoint()
        changes = {'project_id': str(uuid.uuid4()), 'engine': 'hermes', 'profile': 'other',
                   'runtime_revision': 'untrusted', 'skill_bundle_revision': 'other',
                   'checkpoint_format': 'native-sqlite', 'input_snapshot_sha256': '0'*64,
                   'schema_version': True, 'source_attempt': True, 'source_token': 'secret',
                   'source_job_id': '../job', 'phase': 'plan_initial', 'version_id': str(uuid.uuid4())}
        for field, value in changes.items():
            with self.subTest(field=field):
                m = copy.deepcopy(original); m[field] = value; self.save(source, m); self.rejected(a, m)
        for field in ('session_id', 'home', 'accepted', 'candidate'):
            with self.subTest(extra=field):
                m = copy.deepcopy(original); m[field] = True; self.save(source, m); self.rejected(a, m)

    def test_explicit_revise_version_required(self):
        a, _, m = self.checkpoint('revise')
        for version in (None, str(uuid.uuid4())):
            with self.subTest(version=version):
                self.rejected(replace(a, base_version_id=version), m)

    def test_missing_or_forged_stage_confirmations(self):
        for phase in ('plan_options', 'prepare', 'generate', 'revise'):
            with self.subTest(phase=phase):
                a, _, m = self.checkpoint(phase)
                snapshot = copy.deepcopy(a.snapshot); snapshot['control_context'] = {}
                self.rejected(replace(a, snapshot=snapshot), m)
        a, _, m = self.checkpoint('prepare')
        for field, value in [('channel', 'worker'), ('input_snapshot_sha256', '0'*64), ('plan_revision', 99)]:
            with self.subTest(field=field):
                snap = copy.deepcopy(a.snapshot); snap['control_context']['stage2_confirmation'][field] = value
                self.rejected(replace(a, snapshot=snap), m)

    def test_selection_direction_and_selected_object_must_match(self):
        a, _, m = self.checkpoint('prepare')
        for mutation in ('id', 'missing', 'object', 'duplicate', 'selection'):
            with self.subTest(mutation=mutation):
                snap = copy.deepcopy(a.snapshot); c = snap['control_context']['stage2_confirmation']
                if mutation == 'id': c['selected_direction']['id'] = 'B'
                if mutation == 'missing': c['design']['directions'] = []
                if mutation == 'object': c['selected_direction']['name'] = 'changed'
                if mutation == 'duplicate': c['design']['directions'] *= 2
                if mutation == 'selection': snap['confirmation_selection'] = {'direction_id': 'B'}
                self.rejected(replace(a, snapshot=snap), m)

    def test_later_context_must_equal_checkpoint_context(self):
        a, _, m = self.checkpoint()
        snap = copy.deepcopy(a.snapshot)
        snap['control_context']['stage2_confirmation']['outline'] = {'changed': True}
        self.rejected(replace(a, snapshot=snap), m)

    def test_index_hash_size_total_and_duplicate_rejected(self):
        a, source, original = self.checkpoint()
        for case in ('hash', 'size', 'total', 'duplicate', 'bool', 'limit', 'count', 'case'):
            with self.subTest(case=case):
                m = copy.deepcopy(original); rows = m['project']['files']
                if case == 'hash': rows[0]['sha256'] = '0'*64
                if case == 'size': rows[0]['bytes'] += 1; m['project']['bytes'] += 1
                if case == 'total': m['project']['bytes'] += 1
                if case == 'duplicate': rows.append(copy.deepcopy(rows[0])); m['project']['bytes'] *= 2
                if case == 'bool': rows[0]['bytes'] = True
                if case == 'limit': rows[0]['bytes'] = pi.MAX_FILE_BYTES + 1
                if case == 'count': m['project']['files'] *= pi.MAX_FILES + 1
                if case == 'case': rows.append(dict(rows[0], path='deck/NOTES.md')); m['project']['bytes'] *= 2
                self.save(source, m); self.rejected(a, m)

    def test_unsafe_paths_and_native_state_rejected(self):
        a, source, original = self.checkpoint()
        names = ['../escape', '/absolute', 'deck/../escape', 'deck/a\\b', 'deck/C:evil',
                 'deck/a\x00b', 'deck/a\x7fb', 'deck/CON.txt', 'deck/a.', 'deck/a ',
                 'deck/COM1', 'deck/SHORT~1.txt', 'deck//x', 'deck/.pi/auth.json',
                 'deck/home/x', 'deck/state.db', 'deck/sessions.jsonl', 'deck/token']
        for name in names:
            with self.subTest(name=name):
                m = copy.deepcopy(original); m['project']['files'][0]['path'] = name
                self.save(source, m); self.rejected(a, m)

    def test_exact_roster_rejects_extra_file_directory_and_home(self):
        a, source, m = self.checkpoint()
        for key in ('project/deck/hidden', 'home', 'extra-empty'):
            with self.subTest(key=key):
                path = source/key
                if key == 'project/deck/hidden': path.write_bytes(b'hidden')
                else: path.mkdir()
                self.rejected(a, m)
                path.unlink() if path.is_file() else path.rmdir()

    def test_missing_indexed_file_rejected(self):
        a, source, m = self.checkpoint(); (source/'project/deck/notes.md').unlink()
        self.rejected(a, m)

    def test_duplicate_manifest_json_keys_rejected(self):
        a, source, m = self.checkpoint()
        raw = json.dumps(m); (source/'checkpoint.json').write_text('{"engine":"pi",' + raw[1:])
        self.rejected(a, m)

    def test_source_hash_size_mapping_and_duplicate_rejections(self):
        source = self.asset(); original = copy.deepcopy(self.snapshot)
        for field, value in [('sha256', '0'*64), ('bytes', 1), ('storage_key', '../outside'),
                             ('bytes', pi.MAX_ASSET_BYTES + 1), ('media_type', 'application/sqlite')]:
            with self.subTest(field=field, value=value):
                snap = copy.deepcopy(original); snap['assets'][0][field] = value; rehash(snap)
                self.rejected(replace(self.a, snapshot=snap))
        snap = copy.deepcopy(original); snap['assets'] *= 2; rehash(snap)
        self.rejected(replace(self.a, snapshot=snap))
        self.assertEqual(sha(source.read_bytes()), original['assets'][0]['sha256'])

    def test_snapshot_core_hash_recomputed_but_feedback_excluded(self):
        self.asset()
        for field, value in [('title', 'changed'), ('settings', {'page_count': 4}), ('assets', []), ('project_id', str(uuid.uuid4()))]:
            with self.subTest(field=field):
                snap = copy.deepcopy(self.snapshot); snap[field] = value
                self.rejected(replace(self.a, snapshot=snap))
        self.snapshot['feedback'] = 'Extra feedback does not change materials hash'
        self.stager.stage(self.a)

    def test_bounds_aggregate_and_storage(self):
        a, _, m = self.checkpoint()
        self.stager = pi.PiStager(replace(self.config, max_snapshot_bytes=1), 'runtime-pinned', 'ppt-skill-pinned')
        self.rejected(a, m)
        self.stager = pi.PiStager(replace(self.config, max_storage_bytes=1), 'runtime-pinned', 'ppt-skill-pinned')
        self.rejected(a, m)

    def test_collision_keeps_existing_attempt_unchanged(self):
        layout, _ = self.stager.stage(self.a)
        original = (layout.inputs/'inventory.json').read_bytes()
        with self.assertRaisesRegex(ControlError, 'attempt_already_staged'):
            self.stager.stage(self.a)
        self.assertEqual((layout.inputs/'inventory.json').read_bytes(), original)

    def test_partial_copy_failure_quarantines_only_owned_attempt(self):
        a, source, m = self.checkpoint()
        original_copy = pi.copy_file; calls = []
        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2: raise ControlError('injected_second_copy_failure')
            return original_copy(*args, **kwargs)
        before = (source/'project/deck/notes.md').read_bytes()
        with patch.object(pi, 'copy_file', side_effect=fail_second):
            self.rejected(a, m)
        quarantines = list(self.stager.layout(a).root.parent.glob('.quarantine-*'))
        self.assertEqual(len(quarantines), 1)
        self.assertEqual((quarantines[0]/'base/project/deck/notes.md').read_bytes(), before)
        self.assertEqual((source/'project/deck/notes.md').read_bytes(), before)
        self.assertFalse(self.stager._staged)

    def test_source_symlink_rejected_without_touching_target(self):
        source = self.asset(); raw = source.read_bytes(); source.unlink()
        outside = Path(self.temp.name)/'outside'; outside.write_bytes(raw)
        self.symlink(source, outside)
        self.rejected(self.a); self.assertEqual(outside.read_bytes(), raw)

    def test_checkpoint_file_symlink_rejected(self):
        a, source, m = self.checkpoint(); target = source/'project/deck/notes.md'
        raw = target.read_bytes(); target.unlink()
        outside = Path(self.temp.name)/'outside'; outside.write_bytes(raw)
        self.symlink(target, outside); self.rejected(a, m)
        self.assertEqual(outside.read_bytes(), raw)

    def test_checkpoint_root_symlink_rejected(self):
        a, source, m = self.checkpoint(); moved = source.with_name('moved'); source.rename(moved)
        self.symlink(source, moved, True); self.rejected(a, m)

    def test_data_root_symlink_rejected(self):
        link = Path(self.temp.name)/'link'; self.symlink(link, self.root, True)
        with self.assertRaises(ControlError):
            pi.PiStager(replace(self.config, data_root=link), 'runtime-pinned', 'ppt-skill-pinned')

    def test_attempt_parent_symlink_rejected_without_outside_writes(self):
        parent = self.stager.layout(self.a).root.parent
        parent.parent.mkdir(parents=True)
        outside = Path(self.temp.name)/'outside'; outside.mkdir()
        self.symlink(parent, outside, True)
        self.rejected(self.a); self.assertEqual(list(outside.iterdir()), [])

    def test_source_hardlink_rejected(self):
        source = self.asset()
        try: os.link(source, source.with_name('alias'))
        except OSError as exc: self.skipTest(f'Hardlinks unavailable: {exc}')
        self.rejected(self.a)

    def test_checkpoint_hardlink_rejected(self):
        a, source, m = self.checkpoint()
        try: os.link(source/'project/deck/notes.md', Path(self.temp.name)/'alias')
        except OSError as exc: self.skipTest(f'Hardlinks unavailable: {exc}')
        self.rejected(a, m)

    def test_other_hermes_home_symlink_not_followed_by_quota(self):
        self.asset()
        outside = Path(self.temp.name)/'external-skills'; outside.mkdir()
        target = outside/'untouched'; target.write_bytes(b'outside-data')
        home = self.root/'projects/other-hermes/attempts/home'; home.mkdir(parents=True)
        link = home/'skills'; self.symlink(link, outside, True)
        before = pi.storage_usage(self.root, self.config.max_storage_bytes)
        target.write_bytes(b'outside-data' * 1000)
        self.assertEqual(pi.storage_usage(self.root, self.config.max_storage_bytes), before)
        expected = target.read_bytes()
        self.stager.stage(self.a)
        self.assertEqual(target.read_bytes(), expected)

    def test_asset_count_and_aggregate_limits(self):
        for _ in range(pi.MAX_ASSETS + 1): self.asset(b'x')
        self.rejected(self.a)
        self.snapshot['assets'] = self.snapshot['assets'][:2]; rehash(self.snapshot)
        with patch.object(pi, 'MAX_ASSET_TOTAL', 1):
            self.rejected(self.a)

    def test_plan_options_actual_api_shape_without_snapshot_revision(self):
        a, _, m = self.checkpoint('plan_options')
        del a.snapshot['plan_revision']
        del a.snapshot['control_context']['stage1_confirmation']['selection']['template_id']
        a.snapshot['confirmation_selection'] = {'design_mode': 'free_design'}
        self.stager.stage(a, m)

    def test_optional_stage_two_selection_fields_match_selected_design(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                a, _, m = self.checkpoint('prepare')
                a = replace(a, job_id=str(uuid.uuid4()))
                c = a.snapshot['control_context']['stage2_confirmation']
                c['selection']['page_plan'] = copy.deepcopy(c['selected_direction']['page_plan'])
                c['design']['production_parameters'] = {'language': 'zh'}
                c['selection']['production_parameters'] = {'language': 'en' if invalid else 'zh'}
                a.snapshot['confirmation_selection'] = copy.deepcopy(c['selection'])
                if invalid: self.rejected(a, m)
                else: self.stager.stage(a, m)

    def test_prepare_requires_snapshot_revision_and_selection_rejects_extra(self):
        a, _, m = self.checkpoint('prepare')
        snap = copy.deepcopy(a.snapshot); del snap['plan_revision']
        self.rejected(replace(a, snapshot=snap), m)
        snap = copy.deepcopy(a.snapshot); snap['control_context']['stage2_confirmation']['selection']['accepted'] = True
        snap['confirmation_selection'] = copy.deepcopy(snap['control_context']['stage2_confirmation']['selection'])
        self.rejected(replace(a, snapshot=snap), m)

    def test_combined_assets_project_limit_and_existing_storage(self):
        self.asset()
        a, _, m = self.checkpoint()
        combined = self.snapshot['assets'][0]['bytes'] + m['project']['bytes']
        self.stager = pi.PiStager(replace(self.config, max_snapshot_bytes=combined-1), 'runtime-pinned', 'ppt-skill-pinned')
        self.rejected(a, m)
        (self.root/'unrelated').write_bytes(b'x' * 1024)
        allowance = combined + m['project']['bytes'] + pi.MAX_MANIFEST_BYTES * 3
        self.stager = pi.PiStager(replace(self.config, max_storage_bytes=allowance), 'runtime-pinned', 'ppt-skill-pinned')
        self.rejected(a, m)

    def test_finish_inputs_rejects_changed_inventory_and_repeat(self):
        layout, _ = self.stager.stage(self.a)
        parsed = {'input_snapshot_sha256': self.snapshot['hash'], 'sources': []}
        self.stager.finish_inputs(self.a, layout, parsed)
        with self.assertRaises(ControlError):
            self.stager.finish_inputs(self.a, layout, parsed)
        with self.assertRaises(ControlError):
            self.stager.finish_inputs(replace(self.a, session_id='other'), layout, parsed)

    def test_snapshot_settings_reject_unknown_bool_and_nonfinite(self):
        for settings in ({'page_count': True}, {'page_count': 3, 'unknown': 1},
                         {'page_count': 3, 'style': float('nan')}):
            with self.subTest(settings=settings):
                snap = copy.deepcopy(self.snapshot); snap['settings'] = settings; rehash(snap)
                self.rejected(replace(self.a, snapshot=snap))

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership and directory mode semantics required')
    def test_posix_directory_permissions_and_controller_ownership(self):
        a, _, m = self.checkpoint(); layout, _ = self.stager.stage(a, m)
        for path in (layout.inputs, layout.base, layout.base/'project', layout.base/'project/deck'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o555)
            self.assertEqual(path.stat().st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE((layout.project/'deck/notes.md').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((layout.base/'project/deck/notes.md').stat().st_mode), 0o444)
        if os.getuid() != 0:
            with self.assertRaises(PermissionError):
                (layout.base/'project/deck/notes.md').write_bytes(b'forbidden')
            with self.assertRaises(PermissionError):
                (layout.inputs/'forbidden').write_bytes(b'forbidden')

    def test_checkpoint_nested_directory_symlink_rejected(self):
        a, source, m = self.checkpoint(); folder = source/'project/deck'
        outside = Path(self.temp.name)/'outside'; folder.rename(outside)
        self.symlink(folder, outside, True); self.rejected(a, m)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO creation unavailable')
    def test_checkpoint_special_file_rejected(self):
        a, source, m = self.checkpoint(); target = source/'project/deck/notes.md'; target.unlink()
        os.mkfifo(target); self.rejected(a, m)


if __name__ == '__main__':
    unittest.main()
