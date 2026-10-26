"""Sidecar lifecycle tests use only synthetic credentials and loopback HTTP."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from ppt_service.worker import pi_proxy_entry as entry
from ppt_service.worker.container_lease import ContainerLaunch, ContainerLease
from ppt_service.worker.model_grant import ModelIdentity
from ppt_service.worker.model_proxy import ModelProxyServer, ProxyConfig
from ppt_service.control.pi_container import PINNED_IMAGE

IDENTITY = ModelIdentity('11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222', 2, 5, 'plan_initial')
BOOT = '33333333-3333-4333-8333-333333333333'
CLIENT, PROVIDER = 'synthetic-worker-key-only', 'synthetic-provider-key-only'


def launch():
    return ContainerLaunch.parse(dict(schema=1, identity=asdict(IDENTITY), nonce='a'*64,
        pi_uid=31800, preview_uid=31801, image_digest=PINNED_IMAGE, mode='hold'))


class ProxyEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audit = Path(self.temp.name)
        self.audit.chmod(0o700)
        # Only permission validation is substituted on non-Linux hosts. Linux
        # root permission tests below exercise the production checks directly.
        if sys.platform != 'linux' or os.geteuid() != 0:
            policy = patch.object(entry, '_private_directory')
            policy.start()
            self.addCleanup(policy.stop)
        self.record = dict(schema=1, identity=asdict(IDENTITY), nonce='a'*64,
            pi_uid=31800, preview_uid=31801, image_digest=PINNED_IMAGE,
            boot_id=BOOT, deadline_monotonic=time.monotonic()+10, seq=1,
            revoked=False, state='running', container_id='b'*64)
        self.leases = []
        self.config = ProxyConfig(CLIENT, PROVIDER)

    def lease_factory(self, value, path):
        self.assertEqual(path, entry.AUTHORITY)
        lease = ContainerLease(value, path, boot_id=BOOT, reader=lambda _: dict(self.record))
        self.leases.append(lease)
        return lease

    def test_real_requests_role_attribution_revoke_listener_and_usage_once(self):
        observed, servers, result = [], [], []
        ready, stop = threading.Event(), threading.Event()
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append((self.headers['Authorization'], payload))
                body = json.dumps(dict(id='resp_fixture', status='completed',
                    usage=dict(input_tokens=7, output_tokens=3))).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        backend = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        backend_thread = threading.Thread(target=backend.serve_forever,
            kwargs={'poll_interval':0.02}, daemon=True)
        backend_thread.start()
        def factory(address, config, **kwargs):
            self.assertEqual(address, ('0.0.0.0', 8080))
            def connector(*args, **ignored):
                return http.client.HTTPConnection('127.0.0.1', backend.server_port, timeout=2)
            server = ModelProxyServer(('127.0.0.1', 0), config, connector=connector, **kwargs)
            self.assertEqual(server.model_grant.snapshot()['request_limit'], 8)
            servers.append(server)
            ready.set()
            return server
        def run():
            result.append(entry.run_proxy(self.config, launch(), server_factory=factory,
                lease_factory=self.lease_factory, audit_path=self.audit, stop_event=stop))
        output = io.StringIO()
        worker = threading.Thread(target=run, daemon=True)
        try:
            with redirect_stdout(output), redirect_stderr(output):
                worker.start()
                self.assertTrue(ready.wait(2))
                port = servers[0].server_port
                for credential, expected in ((PROVIDER, 401), (CLIENT, 200)):
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                    try:
                        conn.request('POST', '/v1/responses', json.dumps(dict(model=self.config.model,
                            input='synthetic', metadata={'job_id':'forged-job', 'phase':'revise'})),
                            {'Authorization':'Bearer '+credential, 'Content-Type':'application/json',
                             'X-PPT-Job-ID':'forged-job'})
                        response = conn.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                    finally:
                        conn.close()
                self.record.update(seq=2, revoked=True, state='revoking')
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result, [dict(identity=asdict(IDENTITY), code='stopped', closed=True)])
                with self.assertRaises(OSError):
                    socket.create_connection(('127.0.0.1', port), timeout=.2)
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0][0], 'Bearer '+PROVIDER)
            rows = [json.loads(line) for line in (self.audit/'usage.jsonl').read_text().splitlines()]
            self.assertEqual([row['event'] for row in rows], ['request_started', 'request_finished'])
            for row in rows:
                self.assertEqual((row['job_id'], row['attempt'], row['phase']),
                                 (IDENTITY.job_id, IDENTITY.attempt, IDENTITY.phase))
            self.assertEqual(rows[-1]['input_tokens'], 7)
            self.assertEqual(rows[-1]['output_tokens'], 3)
            for key in (CLIENT, PROVIDER):
                self.assertNotIn(key, output.getvalue()+json.dumps(rows)+json.dumps(result))
            self.assertFalse(self.leases[0]._thread.is_alive())
            self.assertFalse(servers[0].model_grant._monitor.is_alive())
            self.assertFalse(any(t.name == 'ppt-proxy-entry' for t in threading.enumerate()))
        finally:
            stop.set()
            worker.join(2)
            backend.shutdown()
            backend.server_close()
            backend_thread.join(2)

    def test_invalid_authority_rejected_before_listener_bind(self):
        for changes in ({'nonce':'c'*64}, {'deadline_monotonic':time.monotonic()+601},
                        {'deadline_monotonic':time.monotonic()-1}, {'revoked':True},
                        {'identity':dict(asdict(IDENTITY), token=6)}, {'schema':True}):
            original = dict(self.record)
            self.record.update(changes)
            factory = unittest.mock.Mock(side_effect=AssertionError('must not bind'))
            with self.subTest(changes=changes):
                status = entry.run_proxy(self.config, launch(), server_factory=factory,
                    lease_factory=self.lease_factory, audit_path=self.audit)
                self.assertEqual(status['code'], 'setup_invalid')
                self.assertTrue(status['closed'])
                factory.assert_not_called()
            self.record = original

    def test_server_setup_error_closes_lease_grant_and_redacts(self):
        grants = []
        def fail(*args, **kwargs):
            grants.append(kwargs['model_grant'])
            raise RuntimeError(PROVIDER+CLIENT)
        status = entry.run_proxy(self.config, launch(), server_factory=fail,
            lease_factory=self.lease_factory, audit_path=self.audit)
        self.assertEqual(status['code'], 'setup_invalid')
        self.assertTrue(status['closed'])
        self.assertFalse(grants[0]._monitor.is_alive())
        self.assertFalse(self.leases[0]._thread.is_alive())
        self.assertNotIn(PROVIDER, json.dumps(status))

    def test_revocation_seals_outstanding_usage_exactly_once(self):
        observations = []
        def factory(address, config, **kwargs):
            server = ModelProxyServer(('127.0.0.1', 0), config, **kwargs)
            observations.append(kwargs['usage_ledger'].begin(config.model))
            self.record.update(seq=2, revoked=True, state='revoking')
            return server
        status = entry.run_proxy(self.config, launch(), server_factory=factory,
            lease_factory=self.lease_factory, audit_path=self.audit)
        self.assertEqual(status['code'], 'stopped')
        self.assertTrue(status['closed'])
        # A late handler completion cannot append a second final observation.
        observations[0].finish('completed')
        rows = [json.loads(line) for line in (self.audit/'usage.jsonl').read_text().splitlines()]
        self.assertEqual([row['event'] for row in rows], ['request_started', 'request_finished'])
        self.assertEqual(rows[-1]['outcome'], 'proxy_shutdown')

    def test_expired_lease_stops_server_without_external_event(self):
        self.record['deadline_monotonic'] = time.monotonic()+.2
        servers = []
        def factory(address, config, **kwargs):
            server = ModelProxyServer(('127.0.0.1', 0), config, **kwargs)
            servers.append(server)
            return server
        status = entry.run_proxy(self.config, launch(), server_factory=factory,
            lease_factory=self.lease_factory, audit_path=self.audit)
        self.assertEqual(status['code'], 'stopped')
        self.assertFalse(servers[0].model_grant._monitor.is_alive())

    def test_main_fixed_paths_opaque_environment_and_safe_status(self):
        output = io.StringIO()
        with patch.object(entry, '_private_directory'), patch.object(entry, 'read_private') as read, \
             patch.object(entry.ProxyConfig, 'from_env', side_effect=ValueError(CLIENT+PROVIDER)), \
             redirect_stdout(output), redirect_stderr(output):
            read.return_value = dict(schema=1, **asdict(launch()))
            self.assertEqual(entry.main([]), 2)
            read.assert_called_once_with(entry.LAUNCH, launch=True)
        self.assertEqual(json.loads(output.getvalue()),
            dict(identity=asdict(IDENTITY), code='setup_invalid', closed=True))
        with patch.object(entry, 'read_private') as read, redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(['--audit=/tmp/public']), 2)
            read.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root directory policy')
class DirectoryPolicyTests(unittest.TestCase):
    def test_private_root_directory_and_immutable_parents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            entry._private_directory(root)
            for mode in (0o755, 0o777, 0o1777):
                root.chmod(mode)
                with self.assertRaises(Exception):
                    entry._private_directory(root)
            root.chmod(0o700)
            child = root/'private'
            child.mkdir(mode=0o700)
            root.chmod(0o777)
            with self.assertRaises(Exception):
                entry._private_directory(child)
            root.chmod(0o700)
            alias = root/'alias'
            alias.symlink_to(child, target_is_directory=True)
            with self.assertRaises(Exception):
                entry._private_directory(alias)
            os.chown(child, 10001, 10001)
            try:
                with self.assertRaises(Exception):
                    entry._private_directory(child)
            finally:
                os.chown(child, 0, 0)


if __name__ == '__main__':
    unittest.main()
