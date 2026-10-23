"""Protocol and cancellation tests; injected reads do not require Docker."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from ppt_service.worker.container_lease import ContainerLaunch,ContainerLease,ContainerLeaseError,decode,read_private
from ppt_service.worker.model_grant import ModelGrant,ModelIdentity
from ppt_service.control.pi_container import PINNED_IMAGE

IDENTITY=ModelIdentity('11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222',2,5,'plan_initial')
BOOT='33333333-3333-4333-8333-333333333333'


def launch():
    return dict(schema=1,identity=asdict(IDENTITY),nonce='a'*64,pi_uid=31800,preview_uid=31801,image_digest=PINNED_IMAGE,mode='hold')


def control():
    v=launch();v.pop('mode')
    return dict(v,boot_id=BOOT,deadline_monotonic=10.0,seq=1,revoked=False,state='running',container_id='b'*64)


class ContainerLeaseTests(unittest.TestCase):
    def setUp(self):
        self.record=control();self.now=0.0
        self.lease=ContainerLease(ContainerLaunch.parse(launch()),clock=lambda:self.now,boot_id=BOOT,reader=lambda _:dict(self.record))
        self.addCleanup(self.lease.close)

    def test_launch_schema_rejects_flags_and_untrusted_execution(self):
        for bad in (dict(launch(),command='sh'),dict(launch(),schema=True),dict(launch(),pi_uid=True),
                    dict(launch(),pi_uid=31801),dict(launch(),mode='generate'),dict(launch(),nonce='a'*63),
                    dict(launch(),image_digest='sha256:'+'f'*64)):
            with self.subTest(value=bad),self.assertRaises(ContainerLeaseError):ContainerLaunch.parse(bad)

    def test_strict_json_rejects_duplicate_nan_and_invalid_encoding(self):
        for raw in (b'{"schema":1,"schema":1}',b'{"deadline_monotonic":NaN}',b'\xff',b'{'):
            with self.subTest(raw=raw),self.assertRaises(ContainerLeaseError):decode(raw)

    def test_renewal_requires_strictly_incremented_sequence_for_change(self):
        self.assertEqual(self.lease.poll()['seq'],1)
        self.assertEqual(self.lease.poll()['seq'],1)
        self.record['seq']=2;self.record['deadline_monotonic']=20
        self.assertEqual(self.lease.poll()['seq'],2)
        self.record['deadline_monotonic']=30
        with self.assertRaises(ContainerLeaseError):self.lease.poll()
        self.record['seq']=3
        with self.assertRaises(ContainerLeaseError):self.lease.poll()

    def test_missing_or_foreign_record_is_sticky_fenced(self):
        self.record['nonce']='c'*64
        with self.assertRaises(ContainerLeaseError):self.lease.poll()
        self.record=control()
        with self.assertRaises(ContainerLeaseError):self.lease.poll()
        self.assertTrue(self.lease.snapshot()['failed'])

    def test_expiry_cannot_be_revived_by_later_file(self):
        self.now=10
        with self.assertRaises(ContainerLeaseError):self.lease.poll()
        self.record['seq']=2;self.record['deadline_monotonic']=30
        with self.assertRaises(ContainerLeaseError):self.lease.poll()

    def test_deadline_and_boot_are_bounded(self):
        for patch in ({'deadline_monotonic':float('inf')},{'deadline_monotonic':True},
                      {'deadline_monotonic':601},{'boot_id':'4'*36},{'seq':True},{'state':'stopped'},
                      {'revoked':'false'},{'container_id':'unknown'},{'extra':'public override'}):
            local=ContainerLease(ContainerLaunch.parse(launch()),clock=lambda:0,boot_id=BOOT,reader=lambda _,p=patch:dict(control(),**p))
            with self.subTest(patch=patch),self.assertRaises(ContainerLeaseError):local.poll()
            local.close()

    def test_revocation_interrupts_model_socket_and_pi_cancel_event(self):
        grant=ModelGrant(IDENTITY,'test-worker-credential-only',ttl_seconds=10,clock=lambda:self.now)
        self.addCleanup(grant.close)
        permit=grant.acquire(b'Bearer test-worker-credential-only')
        a,b=socket.socketpair();self.addCleanup(a.close);self.addCleanup(b.close)
        b.settimeout(1);self.assertTrue(permit.attach_socket(a))
        interrupted=threading.Event();self.lease.bind(grant,interrupted)
        self.record['seq']=2;self.record['revoked']=True;self.record['state']='revoking'
        self.assertTrue(interrupted.wait(1),'reader did not propagate revoke')
        self.assertEqual(b.recv(1),b'')
        self.assertFalse(grant.snapshot()['active'])


@unittest.skipUnless(sys.platform=='linux' and os.geteuid()==0,'Linux root private file policy')
class PrivateRecordTests(unittest.TestCase):
    def test_root_private_single_link_record_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'control.json'
            path.write_text(json.dumps(control()));path.chmod(0o600)
            self.assertEqual(read_private(path)['seq'],1)
            alias=root/'alias';os.link(path,alias)
            with self.assertRaises(ContainerLeaseError):read_private(path)
            alias.unlink();path.chmod(0o666)
            with self.assertRaises(ContainerLeaseError):read_private(path)
            path.chmod(0o600);alias.symlink_to(path)
            with self.assertRaises(OSError):read_private(alias)


if __name__=='__main__':unittest.main()
