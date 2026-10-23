"""Task model lifecycle with real queue transactions; Linux controller only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from pi_worker.runtime import RuntimeSettings
from ppt_service.control.execution import Attempt, ControlError, Layout, RuntimeConfig
from ppt_service.control.pi_model import TaskModelProxy
from ppt_service.server.models import Job, ExecutionSlot
from ppt_service.server.repository import Repository
from ppt_service.tests.control import test_pi_queue as db_fixture


def no_upstream(*args,**kwargs):
    raise AssertionError('test must not open an upstream connection')


@unittest.skipUnless(sys.platform=='linux' and os.geteuid()==0,'Linux root controller')
class TaskModelTests(unittest.TestCase):
    def setUp(self):
        self.db=db_fixture.QueueTests();self.db.setUp();self.addCleanup(self.db.doCleanups)
        claim=self.db.queue.claim_next()
        self.a=Attempt(claim.job_id,self.db.pid,claim.attempt,claim.token,claim.kind,{'hash':'a'*64},'correlation')
        self.db.queue.attach_execution(self.a)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config=RuntimeConfig(Path(self.temp.name)/'data')
        root=self.config.data_root/'projects'/self.a.project_id/'attempts'/self.a.job_id/f'{self.a.attempt}-{self.a.token}'
        names=('inputs','task','home','project','result','audit','base')
        self.layout=Layout(root,*(root/name for name in names))
        for name in names:getattr(self.layout,name).mkdir(parents=True)

    def proxy(self):
        proxy=TaskModelProxy.for_offline_test(self.config,self.a,self.layout,no_upstream)
        self.addCleanup(proxy.close)
        return proxy

    def receipt(self):
        return json.loads((self.layout.audit/'model-authority/authority.json').read_text())

    def test_successful_fenced_database_heartbeat_can_renew(self):
        proxy=self.proxy()
        self.assertTrue(proxy.heartbeat_and_renew(self.db.queue,self.a))
        self.assertEqual(self.receipt()['state'],'renewed')
        self.assertEqual(proxy.snapshot()['identity']['token'],self.a.token)
        with self.db.sessions() as s:self.assertEqual(s.get(Job,self.a.job_id).status,'running')

    def test_database_fence_loss_revokes_instead_of_renewing(self):
        proxy=self.proxy()
        with self.db.sessions.begin() as s:
            s.get(Job,self.a.job_id).fencing_token+=1
            s.get(ExecutionSlot,1).fencing_token+=1
        with self.assertRaises(Exception):proxy.heartbeat_and_renew(self.db.queue,self.a)
        self.assertFalse(proxy.snapshot()['active'])
        self.assertEqual(self.receipt()['state'],'revoked')

    def test_cancelled_heartbeat_revokes_before_worker_stop(self):
        proxy=self.proxy()
        with self.db.sessions.begin() as s:Repository(s).cancel(s.get(Job,self.a.job_id))
        self.assertFalse(proxy.heartbeat_and_renew(self.db.queue,self.a))
        self.assertFalse(proxy.snapshot()['active'])
        self.assertEqual(self.receipt()['state'],'revoked')

    def test_stale_identity_does_not_revoke_the_correct_owner(self):
        proxy=self.proxy()
        with self.assertRaisesRegex(ControlError,'model_identity_mismatch'):
            proxy.heartbeat_and_renew(self.db.queue,replace(self.a,token=self.a.token+1))
        self.assertTrue(proxy.snapshot()['active'])

    def test_private_receipt_and_runtime_do_not_expose_provider_credential(self):
        proxy=self.proxy()
        private=self.layout.audit/'model-authority'
        self.assertEqual(private.stat().st_mode&0o777,0o700)
        self.assertEqual((private/'authority.json').stat().st_mode&0o777,0o600)
        raw=(private/'authority.json').read_bytes()
        settings=proxy.runtime_settings(RuntimeSettings(fixture_mode=True))
        key=settings.model_env['SCIENTIFIC_MODEL_API_KEY']
        self.assertTrue(key.startswith('ppt-task-'))
        self.assertNotIn(key.encode(),raw)
        self.assertNotIn(b'fake-provider-protocol-key',raw)
        self.assertEqual(settings.model_input,('text','image'))
        self.assertFalse(settings.capability_verified)
        self.assertFalse(settings.model_verified)
        self.assertFalse(settings.isolation_verified)
        self.assertEqual(self.receipt()['identity']['project_id'],self.a.project_id)

    def test_recreation_cannot_replenish_same_attempt_budget(self):
        proxy=self.proxy();proxy.close()
        with self.assertRaises(FileExistsError):
            TaskModelProxy.for_offline_test(self.config,self.a,self.layout,no_upstream)
        self.assertFalse(self.receipt()['grant']['active'])
        with self.assertRaises(ControlError):proxy.runtime_settings(RuntimeSettings(fixture_mode=True))

    def test_live_constructor_keeps_existing_paid_admission_gate(self):
        with self.assertRaisesRegex(ControlError,'execution_disabled'):
            TaskModelProxy(self.config,self.a,self.layout,'must-not-be-used')
        self.assertFalse((self.layout.audit/'model-authority').exists())

    def test_slow_commit_does_not_add_time_to_model_authority(self):
        proxy=self.proxy()
        class Queue:
            lease_seconds=60
            def heartbeat(self,a):return 'running'
        with patch('ppt_service.control.pi_model.time.monotonic',side_effect=[10.0,30.0]):
            self.assertTrue(proxy.heartbeat_and_renew(Queue(),self.a))
        self.assertLessEqual(proxy.snapshot()['remaining_seconds'],40)

    def test_offline_connector_cannot_fall_back_to_real_https(self):
        for connector in (None,False,0):
            with self.assertRaisesRegex(ControlError,'offline_connector_required'):
                TaskModelProxy.for_offline_test(self.config,self.a,self.layout,connector)
        with self.assertRaises(TypeError):
            TaskModelProxy(self.config,self.a,self.layout,'must-not-be-used',_test_connector=no_upstream)
        self.assertFalse((self.layout.audit/'model-authority').exists())

    def test_wrong_layout_and_linked_authority_are_rejected(self):
        wrong=replace(self.layout,root=self.layout.root.parent)
        with self.assertRaisesRegex(ControlError,'model_layout_mismatch'):
            TaskModelProxy.for_offline_test(self.config,self.a,wrong,no_upstream)
        (self.layout.audit/'model-authority').symlink_to(self.layout.inputs,target_is_directory=True)
        with self.assertRaises(FileExistsError):self.proxy()

    def test_constructor_failure_revokes_grant_and_records_closed(self):
        with patch('ppt_service.control.pi_model.ModelProxyServer',side_effect=RuntimeError('test start failure')):
            with self.assertRaisesRegex(RuntimeError,'test start failure'):self.proxy()
        self.assertFalse(self.receipt()['grant']['active'])
        self.assertEqual(self.receipt()['state'],'closed')


if __name__=='__main__':unittest.main()
