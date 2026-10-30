"""Controller evidence transported to real mapped mounts; no model or services."""
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from ppt_service.control.execution import ControlError
from ppt_service.control.parsing import parse_inputs
from ppt_service.tests.control import test_pi_staging as staging_fixtures
from ppt_service.worker.model_grant import ModelIdentity
from ppt_service.worker.pi_context import PreparedPptContext, bounded_json


class MountedContextTests(unittest.TestCase):
    def prepare(self, phase='plan_initial', execution_deadline=None):
        fixture = staging_fixtures.PiStagingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.asset(b'# Public material\nA real markdown source for a three slide plan.\n')
        attempt, manifest = fixture.a, None
        if phase != 'plan_initial':
            attempt, _, manifest = fixture.checkpoint(phase)
        attempt = dataclasses.replace(attempt, execution_deadline=execution_deadline)
        layout, inventory = fixture.stager.stage(attempt, manifest)
        parsed = parse_inputs(layout.inputs, inventory)
        fixture.stager.finish_inputs(attempt, layout, parsed)
        context = PreparedPptContext.from_stager(fixture.stager, attempt, layout, checkpoint_manifest=manifest)
        root = Path(fixture.temp.name) / 'mapped-work'
        shutil.copytree(layout.root, root)
        identity = ModelIdentity(attempt.project_id, attempt.job_id, attempt.attempt, attempt.token, attempt.phase)
        return context, identity, root

    def mount(self, context, identity, root, bundle=None, **kwargs):
        return PreparedPptContext.from_mounts(context.export_for_mounts() if bundle is None else bundle,
            identity, root=root, runtime_revision=kwargs.get('runtime_revision', context.runtime_revision),
            skill_bundle_revision=kwargs.get('skill_bundle_revision', context.skill_bundle_revision))

    def write_json(self, path, value):
        path.chmod(0o600)
        path.write_text(json.dumps(value), encoding='utf-8')

    def rehash(self, bundle, field, value):
        bundle['receipt'][field] = hashlib.sha256(bounded_json(value, 4 * 1024**2).encode()).hexdigest()

    def test_real_markdown_roundtrip_all_phases(self):
        for phase in ('plan_initial', 'plan_options', 'prepare', 'generate', 'revise'):
            with self.subTest(phase=phase):
                context, identity, root = self.prepare(phase)
                mounted = self.mount(context, identity, root)
                self.assertEqual(context.task, mounted.task)
                self.assertEqual(context.checkpoint, mounted.checkpoint)
                self.assertEqual(mounted.receipt['stage_root'], '/work')
                self.assertEqual(mounted.receipt['paths']['task'], '/work/task/task.json')
                parsed = json.loads((root/'inputs/parsed.json').read_text(encoding='utf-8'))
                self.assertIn('real markdown source', parsed['sources'][0]['text'])
                self.assertNotIn(str(root), bounded_json(mounted.export_for_mounts(), 4 * 1024**2))

    def test_original_execution_deadline_roundtrips_all_phases(self):
        deadline = datetime(2026, 10, 29, 4, 30, tzinfo=timezone.utc)
        for phase in ('plan_initial', 'plan_options', 'prepare', 'generate', 'revise'):
            with self.subTest(phase=phase):
                context, identity, root = self.prepare(phase, execution_deadline=deadline)
                mounted = self.mount(context, identity, root)
                self.assertEqual(mounted.task['execution_deadline'], deadline.isoformat())
                task = context.task
                task['execution_deadline'] = '2099-01-01T00:00:00+00:00'
                self.write_json(root/'task/task.json', task)
                with self.assertRaisesRegex(ControlError, 'mounted_content_mismatch'):
                    self.mount(context, identity, root)

    def test_deadline_requires_a_canonical_utc_timestamp_even_with_rehashed_evidence(self):
        for value in (True, 'not-a-timestamp', '2026-10-29T04:30:00', '2026-10-29T12:30:00+08:00'):
            with self.subTest(deadline=value):
                context, identity, root = self.prepare()
                task = context.task | {'execution_deadline': value}
                self.write_json(root/'task/task.json', task)
                bundle = context.export_for_mounts()
                self.rehash(bundle, 'task_sha256', task)
                with self.assertRaisesRegex(ControlError, 'invalid_execution_deadline'):
                    self.mount(context, identity, root, bundle)

    def test_host_original_source_unavailable_and_semantic_json_hashing(self):
        context, identity, root = self.prepare()
        aid = context.task['assets'][0]['id']
        original = root.parent/'data'/'projects'/identity.project_id/'assets'/aid/'original'
        original.unlink()
        # Whitespace and object order are outside canonical JSON identity.
        task = context.task
        path = root/'task/task.json'
        path.chmod(0o600)
        path.write_text(json.dumps(dict(reversed(list(task.items()))), indent=4), encoding='utf-8')
        with patch.object(staging_fixtures.pi.PiStager, '_sources', side_effect=AssertionError('host source access')):
            mounted = self.mount(context, identity, root)
        self.assertEqual(mounted.task, context.task)
        self.assertEqual(mounted.receipt['task_sha256'], context.receipt['task_sha256'])

    def test_transport_and_context_are_detached_and_frozen(self):
        context, identity, root = self.prepare('prepare')
        bundle = context.export_for_mounts()
        mounted = self.mount(context, identity, root, bundle)
        bundle['receipt']['token'] += 1
        bundle['checkpoint']['control_context'].clear()
        detached = mounted.task
        detached['stage2_confirmation']['outline'].clear()
        self.assertEqual(mounted.task, context.task)
        self.assertEqual(mounted.checkpoint, context.checkpoint)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            mounted.runtime_revision = 'changed'
        with self.assertRaises(TypeError):
            PreparedPptContext()

    def test_strict_bundle_receipt_and_fixed_paths(self):
        context, identity, root = self.prepare()
        for target, key, value in [('bundle', 'extra', True), ('bundle', 'schema_version', True),
                                   ('receipt', 'extra', True), ('receipt', 'schema_version', True),
                                   ('receipt', 'stage_root', str(root)), ('receipt', 'paths', {'task': str(root/'task/task.json')})]:
            with self.subTest(target=target, key=key):
                bundle = context.export_for_mounts()
                (bundle if target == 'bundle' else bundle['receipt'])[key] = value
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root, bundle)
        bundle = context.export_for_mounts()
        del bundle['receipt']['base_version_id']
        with self.assertRaises(ControlError):
            self.mount(context, identity, root, bundle)
        with self.assertRaises(ValueError):
            self.mount(context, identity, root, {'padding': 'x' * (4 * 1024**2)})

    def test_trusted_identity_and_pins_reject_mismatch_before_io(self):
        context, identity, root = self.prepare()
        with patch('ppt_service.worker.pi_context.checked_directory', side_effect=AssertionError('unexpected IO')):
            for changes in ({'project_id': str(uuid.uuid4())}, {'job_id': str(uuid.uuid4())},
                            {'attempt': 2}, {'token': identity.token + 1}, {'phase': 'prepare'}):
                with self.subTest(changes=changes), self.assertRaises(ControlError):
                    self.mount(context, dataclasses.replace(identity, **changes), root)
            for pin in ('runtime_revision', 'skill_bundle_revision'):
                with self.subTest(pin=pin), self.assertRaises(ControlError):
                    self.mount(context, identity, root, **{pin: 'wrong'})
            with self.assertRaises(ControlError):
                self.mount(context, dataclasses.asdict(identity), root)

    def test_actual_task_parsed_inventory_hashes(self):
        for key in ('task/task.json', 'inputs/inventory.json', 'inputs/parsed.json'):
            with self.subTest(key=key):
                context, identity, root = self.prepare()
                path = root/key
                data = json.loads(path.read_text(encoding='utf-8'))
                data['extra'] = True
                self.write_json(path, data)
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root)

    def test_actual_source_bytes_and_all_rosters(self):
        for target in ('source', 'inputs', 'task', 'project', 'base'):
            with self.subTest(target=target):
                context, identity, root = self.prepare()
                if target == 'source':
                    path = root/'inputs'/Path(context.task['assets'][0]['path']).name
                    path.chmod(0o600)
                    path.write_bytes(b'changed')
                else:
                    folder = root/target
                    folder.chmod(0o700)
                    (folder/'unexpected').write_bytes(b'extra')
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root)

    def test_optional_metadata_names_only(self):
        context, identity, root = self.prepare()
        (root/'task').chmod(0o700)
        for name in ('mounted-context.json', 'domain-launch.json'):
            (root/'task'/name).write_text('{}')
        self.mount(context, identity, root)
        (root/'task/sourceReceipt.json').write_text('{}')
        with self.assertRaises(ControlError):
            self.mount(context, identity, root)

    def test_checkpoint_and_restored_project_base_tamper(self):
        for target in ('checkpoint', 'project', 'base'):
            with self.subTest(target=target):
                context, identity, root = self.prepare('prepare')
                bundle = context.export_for_mounts()
                if target == 'checkpoint':
                    bundle['checkpoint']['source_token'] += 1
                else:
                    path = root/('project/deck/notes.md' if target == 'project' else 'base/project/deck/notes.md')
                    path.chmod(0o600)
                    path.write_bytes(b'changed')
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root, bundle)

    def test_checkpoint_semantic_validation_after_canonical_hash(self):
        context, identity, root = self.prepare('revise')
        for key, value in [('engine', 'hermes'), ('checkpoint_format', 'native-sqlite'),
                           ('phase', 'plan_initial'), ('project_id', str(uuid.uuid4())),
                           ('runtime_revision', 'other'), ('skill_bundle_revision', 'other'),
                           ('version_id', str(uuid.uuid4())), ('control_context', {})]:
            with self.subTest(key=key):
                bundle = context.export_for_mounts()
                bundle['checkpoint'][key] = value
                self.rehash(bundle, 'checkpoint_sha256', bundle['checkpoint'])
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root, bundle)

    def test_explicit_checkpoint_key_base_and_initial_context(self):
        for phase in ('plan_initial', 'prepare', 'revise'):
            context, identity, root = self.prepare(phase)
            mutations = [('checkpoint_key', None if phase != 'plan_initial' else 'projects/other/checkpoints/key'),
                         ('base_version_id', str(uuid.uuid4()) if phase != 'revise' else None)]
            for key, value in mutations:
                with self.subTest(phase=phase, key=key):
                    bundle = context.export_for_mounts()
                    bundle['receipt'][key] = value
                    with self.assertRaises(ControlError):
                        self.mount(context, identity, root, bundle)

    def test_typed_inventory_and_exact_assets_even_with_rehashed_evidence(self):
        for mutation in ('bytes', 'hash', 'path', 'roster', 'assets'):
            with self.subTest(mutation=mutation):
                context, identity, root = self.prepare()
                bundle = context.export_for_mounts()
                inventory_path = root/'inputs/inventory.json'
                inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
                task = context.task
                if mutation == 'assets':
                    task['assets'][0]['bytes'] = True
                else:
                    row = inventory['sources'][0]
                    if mutation == 'bytes': row['bytes'] = True
                    if mutation == 'hash': row['sha256'] = '0' * 64
                    if mutation == 'path': row['path'] = '../escape'
                    if mutation == 'roster': inventory['sources'] = []
                self.write_json(inventory_path, inventory)
                self.write_json(root/'task/task.json', task)
                self.rehash(bundle, 'inventory_sha256', inventory)
                self.rehash(bundle, 'task_sha256', task)
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root, bundle)

    def test_confirmation_channel_and_cross_phase_context(self):
        for phase in ('plan_options', 'prepare', 'generate', 'revise'):
            with self.subTest(phase=phase):
                context, identity, root = self.prepare(phase)
                bundle = context.export_for_mounts()
                task = context.task
                needed = 'stage1_confirmation' if phase == 'plan_options' else 'stage2_confirmation'
                task[needed]['channel'] = 'worker'
                self.write_json(root/'task/task.json', task)
                self.rehash(bundle, 'task_sha256', task)
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root, bundle)

    def test_hardlinked_source_rejected(self):
        context, identity, root = self.prepare()
        source = root/'inputs'/Path(context.task['assets'][0]['path']).name
        try:
            os.link(source, root/'outside-hardlink')
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'Hardlink creation unavailable: {exc}')
        with self.assertRaises(ControlError):
            self.mount(context, identity, root)

    def test_symlink_source_rejected(self):
        context, identity, root = self.prepare()
        source = root/'inputs'/Path(context.task['assets'][0]['path']).name
        outside = root/'outside-source'
        outside.write_bytes(source.read_bytes())
        source.parent.chmod(0o700)
        source.chmod(0o600)
        source.unlink()
        try:
            source.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'Symlink creation unavailable: {exc}')
        with self.assertRaises(ControlError):
            self.mount(context, identity, root)

    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO verification requires Linux/POSIX host')
    def test_fifo_source_rejected_without_opening(self):
        context, identity, root = self.prepare()
        source = root/'inputs'/Path(context.task['assets'][0]['path']).name
        source.parent.chmod(0o700)
        source.chmod(0o600)
        source.unlink()
        os.mkfifo(source)
        with self.assertRaises(ControlError):
            self.mount(context, identity, root)

    def test_root_checked_before_any_json_read(self):
        context, identity, root = self.prepare()
        with patch('ppt_service.worker.pi_context.checked_directory', side_effect=ControlError('unsafe_directory')):
            with patch('ppt_service.worker.pi_context.read_json') as read:
                with self.assertRaises(ControlError):
                    self.mount(context, identity, root)
                read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
