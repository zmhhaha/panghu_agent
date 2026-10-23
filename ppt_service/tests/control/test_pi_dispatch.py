"""Queue driver contract; no paid engine calls or database."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ppt_service.control import pi_dispatch as dispatch
from ppt_service.control.pi_queue import Claim
from ppt_service.control.execution import Attempt, ControlError, RuntimeConfig, StopProof

A=Attempt('22222222-2222-4222-8222-222222222222','11111111-1111-4111-8111-111111111111',1,3,'plan_initial',{'hash':'a'*64},'correlation')


class Queue:
    def __init__(self, idle=False, cancelled=False):
        self.calls=[]; self.idle=idle; self.cancelled=cancelled
    def claim_next(self):
        self.calls.append('claim'); return None if self.idle else Claim(A.job_id,A.phase,A.attempt,A.token)
    def attach_execution(self,a):self.calls.append('attach')
    def heartbeat(self,a):return 'running'
    def abandon_claim(self,c,code):self.calls.append(('prelaunch',code))
    def prove_stopped(self,a,ok):
        self.calls.append('prove'); return 'cancelled' if self.cancelled else 'validating'
    def finish_failed(self,a,code):self.calls.append(('failed',code))


class Ledger:
    def verify(self,**binding):return 31701
    def writers(self,**binding):return (31701,31702,31703)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.q=Queue()
        self.driver=dispatch.PiDispatch(self.q,RuntimeConfig(Path(self.temp.name)),Ledger(),
            SimpleNamespace(fixture_mode=True),object(),Path(self.temp.name)/'scratch',
            parsed_for=lambda a:{'input_snapshot_sha256':a.snapshot['hash']},
            build=lambda c:dispatch.ClaimedExecution(A,None))
        self.host=patch.object(dispatch,'_require_execution_host');self.host.start();self.addCleanup(self.host.stop)
        self.stop=patch.object(dispatch,'stop_uids');self.kill=self.stop.start();self.addCleanup(self.stop.stop)

    def result(self,proof=None):
        return SimpleNamespace(proof=proof or StopProof(A.uid,True,('uid:31701',)))

    def test_idle_does_not_launch(self):
        self.q.idle=True
        with patch.object(dispatch,'run_offline_attempt') as run:
            self.assertIsNone(self.driver.run_next());run.assert_not_called()
        self.assertEqual(self.q.calls,['claim'])

    def test_success_uses_fenced_scratch_and_stops_at_validation(self):
        out=self.result()
        with patch.object(dispatch,'run_offline_attempt',return_value=out) as run:
            self.assertIs(self.driver.run_next(),out)
        self.assertTrue(str(run.call_args.args[4]).endswith(A.job_id+'-1-3'))
        self.assertEqual(self.q.calls,['claim','attach','prove'])

    def test_real_mode_refused_before_claim(self):
        self.driver.runtime_settings=SimpleNamespace(fixture_mode=False)
        with self.assertRaisesRegex(dispatch.PiDispatchError,'production_admission_not_configured'):
            self.driver.run_next()
        self.assertEqual(self.q.calls,[])

    def test_malformed_build_is_terminal_prelaunch(self):
        self.driver.build=lambda c:object()
        with self.assertRaisesRegex(dispatch.PiDispatchError,'prelaunch_refused'):
            self.driver.run_next()
        self.assertEqual(self.q.calls,['claim',('prelaunch','execution_interrupted')])

    def test_uid_lookup_failure_is_prelaunch_not_stop_evidence(self):
        with patch.object(self.driver.allocator,'verify',side_effect=RuntimeError('ledger unavailable')):
            with self.assertRaisesRegex(dispatch.PiDispatchError,'prelaunch_refused'):self.driver.run_next()
        self.kill.assert_not_called()
        self.assertEqual(self.q.calls,['claim',('prelaunch','execution_interrupted')])

    def test_failed_run_stops_every_allocated_uid(self):
        with patch.object(dispatch,'run_offline_attempt',side_effect=ControlError('preview_ready_timeout')):
            with self.assertRaisesRegex(ControlError,'preview_ready_timeout'):self.driver.run_next()
        self.kill.assert_called_once_with((31701,31702,31703))
        self.assertEqual(self.q.calls,['claim','attach','prove',('failed','execution_interrupted')])

    def test_stop_failure_preserves_slot_and_does_not_finish_failed(self):
        self.kill.side_effect=ControlError('writers_not_stopped')
        with patch.object(dispatch,'run_offline_attempt',side_effect=RuntimeError('failed')):
            with self.assertRaisesRegex(dispatch.PiDispatchError,'writers_not_stopped'):self.driver.run_next()
        self.assertEqual(self.q.calls,['claim','attach'])

    def test_cancellation_does_not_fail_already_released_slot(self):
        self.q.cancelled=True
        with patch.object(dispatch,'run_offline_attempt',side_effect=ControlError('execution_timeout')):
            with self.assertRaises(ControlError):self.driver.run_next()
        self.assertEqual(self.q.calls,['claim','attach','prove'])

    def test_forged_completion_without_bound_stop_proof_is_rejected(self):
        with patch.object(dispatch,'run_offline_attempt',return_value=self.result(StopProof('other',True))):
            with self.assertRaisesRegex(ControlError,'writers_not_stopped'):self.driver.run_next()
        self.assertEqual(self.q.calls[-1],('failed','execution_interrupted'))

    def test_cancelled_success_not_returned_as_candidate(self):
        self.q.cancelled=True
        with patch.object(dispatch,'run_offline_attempt',return_value=self.result()):
            self.assertIsNone(self.driver.run_next())


if __name__=='__main__':unittest.main()
