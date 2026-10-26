"""No-provider unit checks for the development entry's network gate."""
import io
import socket
import unittest
from pathlib import Path
from unittest.mock import patch
from ppt_service.worker import pi_stage_entry as entry


class Reply(io.BytesIO):
    status=200
    def __init__(self):super().__init__(b'{"status":"ok"}')


class DeniedSocket:
    def settimeout(self,value):pass
    def connect(self,address):raise OSError('fixture denied')
    def close(self):pass


class NetworkGateTests(unittest.TestCase):
    def setUp(self):
        self.route='Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
        self.external=False;self.proxy='172.30.240.2';self.allowed=False
        def addresses(host,*args,**kwargs):
            if host=='model-proxy':return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(self.proxy,8080))]
            if self.external:return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))]
            raise socket.gaierror('fixture no external DNS')
        class Opener:
            def open(self,*args,**kwargs):return Reply()
        self.patches=[patch.object(Path,'read_text',side_effect=lambda:self.route),
            patch.object(Path,'exists',return_value=False),
            patch.object(entry.socket,'getaddrinfo',side_effect=addresses),
            patch.object(entry.socket,'socket',side_effect=lambda *a,**k:DeniedSocket()),
            patch.object(entry.urllib.request,'build_opener',return_value=Opener()),
            patch.object(entry,'write_report')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.report=entry.write_report

    def test_gate_success_records_only_protected_audit_evidence(self):
        result=entry.network_preflight()
        self.assertTrue(result['passed'])
        self.assertEqual(self.report.call_count,2)
        self.assertEqual(self.report.call_args.args[0],Path('/work/audit/network-preflight.json'))

    def test_default_route_rejected_before_model(self):
        self.route+='eth0 00000000 0100007F 0003 0 0 0 00000000\n'
        with self.assertRaisesRegex(RuntimeError,'external_default_route'):entry.network_preflight()
        self.assertEqual(self.report.call_count,1)

    def test_external_dns_is_rejected(self):
        self.external=True
        with self.assertRaisesRegex(RuntimeError,'external_dns_enabled'):entry.network_preflight()

    def test_wrong_proxy_address_is_rejected(self):
        self.proxy='172.30.240.9'
        with self.assertRaisesRegex(RuntimeError,'proxy_address_mismatch'):entry.network_preflight()

    def test_reachable_host_or_metadata_is_rejected(self):
        with patch.object(DeniedSocket,'connect',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'forbidden_tcp_succeeded'):entry.network_preflight()

    def test_base_profile_stays_disabled_and_development_requires_bound_context(self):
        self.assertFalse(entry.PptProfile.production_ready)
        with self.assertRaisesRegex(RuntimeError,'bound_initial_context_required'):
            entry.ControlledDevelopmentPptProfile(runtime_revision=entry.RUNTIME_REVISION,
                skill_bundle_revision=entry.SKILL_BUNDLE_REVISION,prepared_context=None)


if __name__=='__main__':unittest.main()
