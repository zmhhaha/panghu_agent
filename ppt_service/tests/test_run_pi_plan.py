"""Offline paid-CLI boundary tests. Fake Docker/firewall; no provider requests."""
import argparse
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from ppt_service.control.execution import ControlError
from ppt_service.control.parsing import parse_inputs
from ppt_service.tests.control import test_pi_staging as staging_fixtures
from ppt_service.worker.model_grant import ModelIdentity
from ppt_service.worker.pi_context import PreparedPptContext
from ppt_service.scripts import run_pi_plan as cli

BOOT = '00000000-0000-0000-0000-000000000001'


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def pause(self, seconds): self.now += seconds


class FakeFirewall:
    def __init__(self, events):
        self.events = events
        self.exists = False
        self.removed = False
    def ipt(self, *args, **kwargs):
        self.events.append(('ipt', args))
        return (0 if self.exists else 1), b''
    def install(self, uid, upstream):
        self.events.append(('firewall_install', uid, upstream))
        self.exists = True
    def remove(self, uid):
        self.events.append(('firewall_remove', uid))
        self.exists = False
        self.removed = True


class FakeDocker:
    def __init__(self, events, *, clock=None, finish_at=0, exit_code=0):
        self.events = events
        self.clock = clock or Clock()
        self.finish_at = finish_at
        self.exit_code = exit_code
        self.networks = {}
        self.containers = {}
        self.roles = {}
        self.on_start = None
        self.fail_worker_create = False
        self.foreign_role = None
        self.stop_fails = False
        self.topology_bad = False
        self.pending_ips = True
        self.wrong_pending_role = None
        self.wrong_assigned_role = None
        self.empty_assigned_role = None
        self.network_observations = []
        self.report_root = None
        self.report_status = 'awaiting_confirmation'
        self.interrupt_on_worker = None

    def call(self, *args, **kwargs):
        self.events.append(('docker', args))
        if args[:2] == ('image', 'inspect'):
            return 0, cli.PINNED_IMAGE.encode()
        if args[:2] == ('network', 'create'):
            name = args[-1]
            options = dict(args[i+1].split('=', 1) for i, value in enumerate(args[:-1]) if value == '--opt')
            label = args[args.index('--label')+1].split('=', 1)
            self.networks[name] = dict(Id=str(len(self.networks)+7)*64, Internal='--internal' in args,
                Labels={label[0]: label[1]}, Options=options,
                IPAM={'Config': [{'Subnet': args[args.index('--subnet')+1]}]})
            return 0, self.networks[name]['Id'].encode()
        if args[:2] == ('network', 'inspect'):
            return 0, json.dumps(self.networks[args[-1]]).encode()
        if args[:2] in (('network', 'connect'), ('network', 'disconnect'), ('network', 'rm')):
            return 0, b''
        if args[0] == 'create':
            role = 'worker' if args[args.index('--name')+1].endswith('-worker') else 'proxy'
            if role == 'worker' and self.fail_worker_create:
                raise ControlError('control_command_failed')
            cid = ('1' if role == 'worker' else '2') * 64
            if role == 'worker' and self.report_root is None:
                for i, value in enumerate(args[:-1]):
                    if value == '--mount' and 'dst=/work/result' in args[i+1]:
                        fields = dict(item.split('=', 1) for item in args[i+1].split(',') if '=' in item)
                        self.report_root = Path(fields['src'])
            labels = dict(args[i+1].split('=', 1) for i, value in enumerate(args[:-1]) if value == '--label')
            self.roles[role] = cid
            self.containers[cid] = dict(id=cid, image=cli.PINNED_IMAGE, labels=labels, role=role,
                state=dict(Running=False, Pid=0, Restarting=False, Status='created', ExitCode=0),
                pid_mode='', auto_remove=False, restart={'Name': 'no', 'MaximumRetryCount': 0})
            return 0, cid.encode()
        if args[0] == 'inspect':
            cid = args[-1]
            obj = self.containers[cid]
            role = obj['role']
            if args[2] == '{{json .NetworkSettings.Networks}}':
                wb, eb = tuple(self.networks)
                value = ({wb: {'IPAddress': '172.30.240.3'}} if role == 'worker' else
                         {wb: {'IPAddress': '172.30.240.2'}, eb: {'IPAddress': '172.30.241.2'}})
                pending = obj['state']['Status'] == 'created'
                for endpoint in value.values():
                    ip = endpoint['IPAddress']
                    endpoint['IPAMConfig'] = {'IPv4Address': '172.30.240.6' if pending and role == self.wrong_pending_role else ip}
                    if pending and self.pending_ips:
                        endpoint['IPAddress'] = ''
                    elif not pending and role == self.wrong_assigned_role:
                        endpoint['IPAddress'] = '172.30.240.6'
                    elif not pending and role == self.empty_assigned_role:
                        endpoint['IPAddress'] = ''
                if self.topology_bad: value['bridge'] = {'IPAddress': '172.17.0.2'}
                self.network_observations.append((role, obj['state']['Status'], value))
                return 0, json.dumps(value).encode()
            authority_starting = (self.report_root is not None and
                (self.report_root.parent/'audit/container-authority/control.json').exists() and
                json.loads((self.report_root.parent/'audit/container-authority/control.json').read_text()).get('state') == 'starting')
            if role == 'worker' and obj['state']['Running'] and self.clock() >= self.finish_at and not authority_starting:
                obj['state'].update(Running=False, Pid=0, Status='exited', ExitCode=self.exit_code)
            value = dict(obj)
            if role == self.foreign_role: value['image'] = 'sha256:' + 'f'*64
            return 0, json.dumps(value).encode()
        if args[0] == 'start':
            cid = args[-1]
            if self.on_start: self.on_start(cid)
            self.containers[cid]['state'].update(Running=True, Pid=42, Status='running')
            if self.containers[cid]['role'] == 'worker':
                if self.report_root:
                    (self.report_root.parent/'audit/network-preflight.json').write_text('{"passed":true}')
                    (self.report_root/'manifest.json').write_text(json.dumps({'status': self.report_status}))
                if self.interrupt_on_worker: self.interrupt_on_worker.set()
            return 0, b''
        if args[0] == 'exec': return 0, b''
        if args[0] in ('stop', 'kill'):
            if not self.stop_fails:
                self.containers[args[-1]]['state'].update(Running=False, Pid=0, Status='exited')
            return 0, b''
        if args[0] == 'logs': return 0, b'{"code":"stopped"}\n'
        raise AssertionError('Unexpected fake Docker verb: ' + args[0])


class RunPiPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root/'inputs'
        self.inputs.mkdir()
        self.raw = b'# Real public source\nA bounded planning input.\n'
        (self.inputs/'source.md').write_bytes(self.raw)
        self.data = self.root/'data'
        self.data.mkdir()
        self.task = dict(phase='plan_initial', requirements=dict(title='Public sample', pages=6,
            external_model_acknowledged=True, goal='Explain the source', native_charts=True),
            assets=[dict(asset_id='public-doc-1', path='/work/inputs/source.md', bytes=len(self.raw),
                         sha256=hashlib.sha256(self.raw).hexdigest())])

    def prepared(self):
        fixture = staging_fixtures.PiStagingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.asset(self.raw)
        layout, inventory = fixture.stager.stage(fixture.a)
        fixture.stager.finish_inputs(fixture.a, layout, parse_inputs(layout.inputs, inventory))
        context = PreparedPptContext.from_stager(fixture.stager, fixture.a, layout)
        for name in ('container-authority', 'proxy-ledger'):
            (layout.audit/name).mkdir(mode=0o700)
        operator = Path(fixture.temp.name)/'operator'
        operator.mkdir(mode=0o700)
        (operator/'logs').mkdir(mode=0o700)
        launch = dict(schema=1, identity=asdict(ModelIdentity(fixture.a.project_id, fixture.a.job_id,
            fixture.a.attempt, fixture.a.token, fixture.a.phase)), nonce='a'*64, pi_uid=100000,
            preview_uid=100001, image_digest=cli.PINNED_IMAGE, mode='complete')
        (layout.task/'domain-launch.json').write_text(json.dumps(launch))
        events = []
        clock = Clock()
        docker, firewall = FakeDocker(events, clock=clock), FakeFirewall(events)
        docker.report_root = layout.result
        return fixture, layout, context, operator, launch, events, clock, docker, firewall

    def execute(self, prepared, **kwargs):
        fixture, layout, context, root, launch, _, clock, docker, firewall = prepared
        return cli.execute_prepared(fixture.config, fixture.a, layout, context, launch, root=root,
            upstream_env=self.root/'upstream.env', code_root=cli.CODE_ROOT, deadline=60,
            docker=docker, firewall=firewall, interrupted=kwargs.get('interrupted', threading.Event()),
            clock=clock, pause=clock.pause, boot=BOOT)

    def test_legacy_source_copy_fresh_identity_and_canonical_snapshot(self):
        snapshot = cli.snapshot_from_legacy(self.task, self.inputs, self.data)
        self.assertEqual(snapshot['settings']['page_count'], 6)
        self.assertEqual(snapshot['settings']['purpose'], 'Explain the source')
        self.assertIn('public-doc-1', snapshot['settings']['requirements'])
        self.assertEqual(str(uuid.UUID(snapshot['project_id'])), snapshot['project_id'])
        asset = snapshot['assets'][0]
        self.assertEqual(asset['media_type'], 'text/markdown')
        self.assertEqual((self.data/asset['storage_key']).read_bytes(), self.raw)
        self.assertFalse(os.path.samefile(self.inputs/'source.md', self.data/asset['storage_key']))
        core = {key: snapshot[key] for key in ('project_id', 'settings', 'title', 'assets')}
        self.assertEqual(snapshot['hash'], hashlib.sha256(cli.bounded_json(core).encode()).hexdigest())

    def test_relative_sources_default_six_pages_and_reject_escape(self):
        task = dict(self.task, requirements=dict(title='Title', external_model_acknowledged=True),
                    assets=[{'path': 'source.md'}])
        snapshot = cli.snapshot_from_legacy(task, self.inputs, self.data)
        self.assertEqual(snapshot['settings']['page_count'], 6)
        for path in ('/etc/passwd', '/work/inputs/../upstream.env', '../source.md', 'sub/../../source.md', 'C:\\source.md'):
            with self.subTest(path=path), self.assertRaises(ControlError):
                cli.snapshot_from_legacy(dict(task, assets=[{'path': path}]), self.inputs, self.data)

    def test_initial_only_no_worker_authority_or_confirmation(self):
        for key, value in [('phase', 'generate'), ('base_version_id', str(uuid.uuid4())),
                           ('stage1_confirmation', {}), ('checkpoint', {}), ('command', ['evil'])]:
            with self.subTest(key=key), self.assertRaises(ControlError):
                cli.snapshot_from_legacy(dict(self.task, **{key: value}), self.inputs, self.data)
        for pages in (True, 2, 21, '6'):
            with self.subTest(pages=pages), self.assertRaises(ControlError):
                cli.snapshot_from_legacy(dict(self.task, requirements=dict(self.task['requirements'], pages=pages)),
                                         self.inputs, self.data)

    def test_asset_count_declared_hash_size_mime_duplicate_and_hardlink(self):
        for assets in ([{'path': 'source.md'}]*11, [{'path': 'source.md'}]*2,
                       [{'path': 'source.md', 'bytes': True}], [{'path': 'source.md', 'sha256': '0'*64}],
                       [{'path': 'source.md', 'media_type': 'image/png'}]):
            with self.subTest(assets=assets), self.assertRaises(ControlError):
                cli.snapshot_from_legacy(dict(self.task, assets=assets), self.inputs, self.data)
        try:
            os.link(self.inputs/'source.md', self.inputs/'linked.md')
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'Host cannot create hardlinks: {exc}')
        with self.assertRaises(ControlError):
            cli.snapshot_from_legacy(self.task, self.inputs, self.data)

    def test_source_symlink_rejected(self):
        try:
            (self.inputs/'alias.md').symlink_to(self.inputs/'source.md')
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'Host cannot create symlinks: {exc}')
        with self.assertRaises((ControlError, OSError)):
            cli.snapshot_from_legacy(dict(self.task, assets=[{'path': 'alias.md'}]), self.inputs, self.data)

    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO creation requires Linux/POSIX host')
    def test_source_fifo_rejected_before_read(self):
        os.mkfifo(self.inputs/'pipe.md')
        with self.assertRaises(ControlError):
            cli.snapshot_from_legacy(dict(self.task, assets=[{'path': 'pipe.md'}]), self.inputs, self.data)

    def test_worker_proxy_fixed_images_argv_mounts_and_credentials(self):
        prepared = self.prepared()
        fixture, layout, _, _, launch, _, _, _, _ = prepared
        key = 'scoped-key-not-a-provider-key'
        worker = cli.container_args(fixture.a, layout, launch, role='worker', code_root=cli.CODE_ROOT,
            upstream_env=self.root/'upstream.env', key=key, deadline=60)
        proxy = cli.container_args(fixture.a, layout, launch, role='proxy', code_root=cli.CODE_ROOT,
            upstream_env=self.root/'upstream.env', key=key, deadline=60)
        self.assertEqual(worker[-2:], [cli.PINNED_IMAGE, '/code/ppt_service/worker/pi_stage_entry.py'])
        self.assertEqual(proxy[-3:], [cli.PINNED_IMAGE, '-m', 'ppt_service.worker.pi_proxy_entry'])
        self.assertNotIn('--env-file', worker)
        self.assertEqual(proxy[proxy.index('--env-file')+1], str(self.root/'upstream.env'))
        self.assertEqual([worker[i+1] for i, v in enumerate(worker[:-1]) if v == '--cap-add'], list(cli.ROOT_CAPABILITIES))
        self.assertNotIn('--cap-add', proxy)
        self.assertIn('PPT_MODEL_API_KEY=' + key, worker)
        self.assertIn('PPT_PROXY_CLIENT_KEY=' + key, proxy)
        self.assertIn('PPT_DEADLINE_SECONDS=60', worker)
        self.assertIn('PPT_MODEL_PROXY_ORIGIN=http://model-proxy:8080', worker)
        self.assertIn('127.0.0.1', worker)
        self.assertNotIn('--pid', worker)
        self.assertNotIn('--rm', worker)
        mounts = [worker[i+1] for i, v in enumerate(worker[:-1]) if v == '--mount']
        self.assertEqual(len(mounts), 7)
        for name in ('inputs', 'task', 'base'):
            self.assertTrue(any('dst=/work/' + name + ',readonly' in item for item in mounts))
        self.assertFalse(any('/work/home' in item or '/var/run/docker.sock' in item for item in mounts))

    def test_success_durable_ids_before_start_revocation_before_stop(self):
        prepared = self.prepared()
        fixture, layout, _, root, _, events, _, docker, _ = prepared
        def start(cid):
            handle = json.loads((root/'execution-handle.json').read_text())
            authority = json.loads((layout.audit/'container-authority/control.json').read_text())
            self.assertEqual(set(handle['containers']), {'worker', 'proxy'})
            self.assertEqual(authority['container_id'], handle['containers']['worker'])
            self.assertEqual(authority['state'], 'starting')
            self.assertFalse(authority['revoked'])
        docker.on_start = start
        state, code = self.execute(prepared)
        self.assertEqual(code, 0)
        self.assertEqual(state['status'], 'candidate_execution_finished')
        self.assertTrue(state['worker_stopped'] and state['proxy_stopped'])
        self.assertTrue(state['authority_revoked'] and state['networks_removed'] and state['firewall_removed'])
        self.assertFalse(state['delivery_published'])
        self.assertEqual(state['artifact_acceptance'], 'not_performed')
        self.assertTrue(state['network_preflight_passed'])
        authority = json.loads((layout.audit/'container-authority/control.json').read_text())
        self.assertTrue(authority['revoked'])
        self.assertEqual(authority['state'], 'stopped')
        actions = [item[1][0] for item in events if item[0] == 'docker']
        self.assertNotIn('rm', actions)
        self.assertTrue((root/'logs/worker.private.log').exists())
        self.assertIn(('firewall_install', fixture.a.uid, cli.UPSTREAM_IP), events)

    def test_renew_only_active_run_and_timeout_revokes_before_stop(self):
        prepared = self.prepared()
        _, layout, _, _, _, _, clock, docker, _ = prepared
        docker.finish_at = 100
        original = docker.call
        def call(*args, **kwargs):
            if args[0] in ('stop', 'kill'):
                record = json.loads((layout.audit/'container-authority/control.json').read_text())
                self.assertTrue(record['revoked'])
            return original(*args, **kwargs)
        docker.call = call
        state, code = self.execute(prepared)
        self.assertEqual(code, 2)
        self.assertEqual(state['error_code'], 'controller_deadline_exceeded')
        record = json.loads((layout.audit/'container-authority/control.json').read_text())
        self.assertGreater(record['seq'], 6)
        self.assertTrue(state['authority_revoked'])
        self.assertGreaterEqual(clock(), 60)

    def test_signal_and_proxy_stop_proof_failure_retain_cage(self):
        prepared = self.prepared()
        _, layout, _, _, _, events, _, docker, firewall = prepared
        interrupted = threading.Event()
        docker.interrupt_on_worker = interrupted
        docker.finish_at = 100
        docker.stop_fails = True
        state, code = self.execute(prepared, interrupted=interrupted)
        self.assertEqual(code, 2)
        self.assertEqual(state['status'], 'stop_unproven')
        self.assertTrue(state['authority_revoked'])
        self.assertFalse(state['proxy_stopped'])
        self.assertFalse(state['networks_removed'])
        self.assertFalse(firewall.removed)
        self.assertFalse(any(item[0] == 'docker' and item[1][:2] == ('network', 'rm') for item in events))
        record = json.loads((layout.audit/'container-authority/control.json').read_text())
        self.assertEqual(record['state'], 'stop_unproven')

    def test_foreign_identity_and_ambiguous_create_never_remove_cage(self):
        for failure in ('foreign', 'create'):
            with self.subTest(failure=failure):
                prepared = self.prepared()
                _, _, _, _, _, _, _, docker, firewall = prepared
                if failure == 'foreign': docker.foreign_role = 'worker'
                else: docker.fail_worker_create = True
                state, code = self.execute(prepared)
                self.assertEqual(code, 2)
                self.assertEqual(state['status'], 'stop_unproven')
                self.assertFalse(firewall.removed)
                self.assertFalse(state['networks_removed'])

    def test_docker29_pending_ipam_then_assigned_addresses_before_running_authority(self):
        prepared = self.prepared()
        docker = prepared[-2]
        real_write = cli.write_authority
        granted = []
        def write(*args, **kwargs):
            if kwargs['state'] == 'running':
                observed = docker.network_observations
                self.assertEqual([(role, status) for role, status, _ in observed[-2:]],
                                 [('worker', 'running'), ('proxy', 'running')])
                self.assertTrue(all(endpoint['IPAddress'] for _, _, rows in observed[-2:]
                                    for endpoint in rows.values()))
                granted.append(True)
            return real_write(*args, **kwargs)
        with patch.object(cli, 'write_authority', side_effect=write):
            state, code = self.execute(prepared)
        self.assertEqual(code, 0)
        self.assertTrue(granted)
        self.assertEqual([(role, status) for role, status, _ in docker.network_observations],
            [('worker', 'created'), ('proxy', 'created'), ('worker', 'created'),
             ('proxy', 'running'), ('worker', 'running'), ('proxy', 'running')])
        self.assertTrue(all(endpoint['IPAddress'] == '' and endpoint['IPAMConfig']['IPv4Address']
            for _, _, rows in docker.network_observations[:2] for endpoint in rows.values()))
        self.assertEqual(state['status'], 'candidate_execution_finished')

    def test_wrong_pending_ipam_never_starts_either_container(self):
        for role in ('worker', 'proxy'):
            with self.subTest(role=role):
                prepared = self.prepared()
                docker = prepared[-2]
                docker.wrong_pending_role = role
                state, code = self.execute(prepared)
                self.assertEqual(code, 2)
                self.assertEqual(state['error_code'], 'container_network_mismatch')
                self.assertFalse(any(item[0] == 'docker' and item[1][0] == 'start' for item in prepared[5]))

    def test_wrong_or_empty_live_address_never_grants_running_authority(self):
        for role in ('worker', 'proxy'):
            for empty in (False, True):
                with self.subTest(role=role, empty=empty):
                    prepared = self.prepared()
                    docker = prepared[-2]
                    if empty: docker.empty_assigned_role = role
                    else: docker.wrong_assigned_role = role
                    real_write = cli.write_authority
                    states = []
                    def write(*args, **kwargs):
                        states.append(kwargs['state'])
                        return real_write(*args, **kwargs)
                    with patch.object(cli, 'write_authority', side_effect=write):
                        state, code = self.execute(prepared)
                    self.assertEqual(code, 2)
                    self.assertEqual(state['error_code'], 'container_network_mismatch')
                    self.assertNotIn('running', states)
                    self.assertIn('revoking', states)
                    if role == 'proxy':
                        self.assertFalse(any(item[0] == 'docker' and item[1] == ('start', '1'*64) for item in prepared[5]))

    def test_pending_fallback_requires_created_stopped_pid_zero(self):
        for change in ({'Status': 'exited'}, {'Running': True}, {'Pid': 7}):
            with self.subTest(change=change):
                prepared = self.prepared()
                fixture, _, _, _, launch, _, _, docker, _ = prepared
                real_inspect = cli.inspect_owned
                def inspect(*args, **kwargs):
                    value = real_inspect(*args, **kwargs)
                    return dict(value, **change)
                with patch.object(cli, 'inspect_owned', side_effect=inspect):
                    state, code = self.execute(prepared)
                self.assertEqual(code, 2)
                self.assertEqual(state['error_code'], 'container_not_pending')
                self.assertFalse(any(item[0] == 'docker' and item[1][0] == 'start' for item in prepared[5]))

    def test_topology_mismatch_never_starts_either_container(self):
        prepared = self.prepared()
        _, _, _, _, _, events, _, docker, _ = prepared
        docker.topology_bad = True
        state, code = self.execute(prepared)
        self.assertEqual(code, 2)
        self.assertEqual(state['error_code'], 'container_network_mismatch')
        self.assertFalse(any(item[0] == 'docker' and item[1][0] == 'start' for item in events))

    def test_needs_input_and_worker_failure_preserved_without_publication(self):
        for status, exit_code, expected in [('needs_input', 0, 'needs_input'), ('awaiting_confirmation', 7, 'worker_failed')]:
            with self.subTest(status=status):
                prepared = self.prepared()
                docker = prepared[-2]
                docker.report_status = status
                docker.exit_code = exit_code
                state, code = self.execute(prepared)
                self.assertEqual(code, 2)
                self.assertEqual(state['status'], expected)
                self.assertFalse(state['delivery_published'])

    def test_safe_stdout_does_not_format_exception_or_credentials(self):
        secret = 'DO-NOT-PRINT-UPSTREAM-KEY'
        with patch.object(cli, 'run', side_effect=RuntimeError(secret)):
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                code = cli.main(['--root', str(self.root/'new'), '--task', str(self.root/'task.json'),
                                 '--inputs', str(self.inputs), '--upstream-env', str(self.root/'upstream.env'), '--execute-paid'])
                text = output.getvalue()
        self.assertEqual(code, 2)
        self.assertNotIn(secret, text)
        self.assertEqual(json.loads(text)['error_code'], 'development_run_failed')
        with self.assertRaises(SystemExit):
            with patch('sys.stderr', new_callable=io.StringIO):
                cli.argument_parser().parse_args(['--root', '/new', '--task', '/task', '--inputs', '/inputs', '--upstream-env', '/env'])

    def test_whole_cli_real_stage_parse_mount_context_and_opaque_provider_file(self):
        from types import SimpleNamespace
        root = self.root/'new-operator'
        task_path = self.root/'task.json'
        task_path.write_text(json.dumps(self.task), encoding='utf-8')
        upstream = self.root/'upstream.env'
        upstream.write_text('PPT_UPSTREAM_API_KEY=opaque-provider-secret-never-read\n')
        upstream.chmod(0o600)
        if os.name == 'posix' and os.geteuid() == 0:
            os.chown(self.inputs, 10000, 10000)
            os.chown(self.inputs/'source.md', 10000, 10000)
            os.chown(task_path, 10000, 10000)
        args = argparse.Namespace(root=str(root), task=str(task_path), inputs=str(self.inputs),
            upstream_env=str(upstream), execute_paid=True, deadline_seconds=60)
        events = []
        docker, firewall = FakeDocker(events), FakeFirewall(events)
        class Parser:
            scope_uid = None
            def parse(self, inputs, inventory): return parse_inputs(inputs, inventory)
        real_execute, real_ordinary = cli.execute_prepared, cli.ordinary
        def execute(*args, **kwargs):
            layout = args[2]
            self.assertEqual((layout.task/'task.json').stat().st_mode & 0o777, 0o444)
            for name in ('mounted-context.json', 'domain-launch.json'):
                self.assertEqual((layout.task/name).stat().st_mode & 0o777, 0o444)
            self.assertEqual(layout.task.stat().st_mode & 0o555, 0o555)
            return real_execute(*args, **kwargs, boot=BOOT)
        def trusted_parents(path):
            self.assertNotEqual(path, self.inputs, 'Source leaf ownership is not controller authority')
            return cli.checked_directory(path)
        def ordinary(path, *args):
            info = real_ordinary(path, *args)
            return SimpleNamespace(st_uid=0, st_mode=0o100600) if path == upstream else info
        with patch.object(cli.sys, 'platform', 'linux'), patch.object(cli.os, 'geteuid', return_value=0, create=True):
            with patch.object(cli, '_parents', side_effect=trusted_parents) as ancestors:
                with patch.object(cli.PiUIDAllocator, 'initialize') as initialize:
                    initialize.return_value.allocate.side_effect = [100000, 100001]
                    with patch.object(cli, 'ordinary', side_effect=ordinary), patch.object(cli, 'execute_prepared', side_effect=execute):
                        state, code = cli.run(args, docker=docker, firewall=firewall, sandbox=Parser())
        self.assertEqual(code, 0)
        checked = [call.args[0] for call in ancestors.call_args_list]
        self.assertIn(self.inputs.parent, checked)
        self.assertIn(upstream.parent, checked)
        self.assertIn(cli.CODE_ROOT, checked)
        self.assertNotIn(self.inputs, checked)
        stage = Path(state['paths']['attempt'])
        launch = json.loads((stage/'task/domain-launch.json').read_text())
        bundle = json.loads((stage/'task/mounted-context.json').read_text())
        self.assertEqual(launch['pi_uid'], 100000)
        self.assertEqual(launch['preview_uid'], 100001)
        identity = ModelIdentity(**launch['identity'])
        context = PreparedPptContext.from_mounts(bundle, identity, root=stage,
            runtime_revision=cli.RUNTIME_REVISION, skill_bundle_revision=cli.SKILL_BUNDLE_REVISION)
        self.assertEqual(context.task['requirements']['pages'], 6)
        self.assertEqual(context.task['phase'], 'plan_initial')
        self.assertNotEqual(context.task['assets'][0]['id'], 'public-doc-1')
        self.assertEqual(context.task['base_version_id'], None)
        self.assertNotIn('stage1_confirmation', context.task)
        self.assertNotIn('opaque-provider-secret', json.dumps(state))
        self.assertEqual(initialize.call_args.args[1].uid_min, 100000)
        commands = [item[1] for item in events if item[0] == 'docker']
        workers = [args for args in commands if args[0] == 'create' and args[args.index('--name')+1].endswith('-worker')]
        self.assertEqual(len(workers), 1)
        self.assertNotIn('--env-file', workers[0])
        self.assertEqual(sum(args.count('--env-file') for args in commands), 1)
        self.assertEqual(json.loads((root/'run-state.json').read_text())['scope'], 'paid_cli_development')

    def test_paid_flag_host_deadline_and_existing_root_reject_before_docker(self):
        args = argparse.Namespace(execute_paid=False, deadline_seconds=60, root=str(self.root),
            task=str(self.root/'task.json'), inputs=str(self.inputs), upstream_env=str(self.root/'upstream.env'))
        with patch.object(cli, 'ControlDocker') as docker:
            with self.assertRaisesRegex(ControlError, 'explicit_paid_execution_required'): cli.run(args)
            docker.assert_not_called()
        args.execute_paid = True
        with patch.object(cli.sys, 'platform', 'linux'), patch.object(cli.os, 'geteuid', return_value=0, create=True):
            args.deadline_seconds = 59
            with self.assertRaisesRegex(ControlError, 'invalid_deadline'): cli.run(args)
            args.deadline_seconds = 60
            with patch.object(cli, '_parents', side_effect=lambda p: cli.checked_directory(p)):
                with self.assertRaisesRegex(ControlError, 'exclusive_new_root_required'): cli.run(args)


if __name__ == '__main__':
    unittest.main()
