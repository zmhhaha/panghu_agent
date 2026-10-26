"""Stop gate tests. Offline logic plus Linux-root real UID termination."""
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ppt_service.control import pi_collect, stop_gate
from ppt_service.control.execution import Attempt, ControlError, Layout, RuntimeConfig, StopProof

ATTEMPT = Attempt('11111111-1111-4111-8111-111111111111',
                  '22222222-2222-4222-8222-222222222222', 1, 2,
                  'generate', {'hash': 'a' * 64}, 'session')
PI, PREVIEW, BYSTANDER = 31201, 31202, 31203


def _drop(uid):
    def apply():
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    return apply


def _spawn(uid):
    return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                            preexec_fn=_drop(uid), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class StopGateLogicTests(unittest.TestCase):
    def test_invalid_uid_arguments_are_rejected_before_any_scan(self):
        bad = [(), (0,), (9999,), (65534,), (2**31 - 1,), (True,), ('31201',), (31201.0,),
               (PI, PI)]
        for uids in bad:
            with self.subTest(uids=uids), patch.object(stop_gate, 'uid_processes') as scan:
                with self.assertRaises(ControlError):
                    stop_gate.stop_uids(uids)
                scan.assert_not_called()
        with self.assertRaises(ControlError):
            stop_gate.stop_uids(PI)  # A bare integer is not a UID list.

    def test_invalid_grace_is_rejected(self):
        for grace in (0, -1, 61, True, '5'):
            with self.subTest(grace=grace), self.assertRaises(ControlError):
                stop_gate.stop_uids((PI,), grace=grace)

    def test_requires_linux_root_controller(self):
        with patch.object(sys, 'platform', 'win32'):
            with self.assertRaisesRegex(ControlError, 'process_safety_unverified'):
                stop_gate.uid_processes(PI)
        with patch.object(sys, 'platform', 'linux'), patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaisesRegex(ControlError, 'process_safety_unverified'):
                stop_gate.uid_processes(PI)
        with self.assertRaisesRegex(ControlError, 'invalid_attempt_uid'):
            stop_gate.uid_processes(65534)

    def test_two_empty_scans_are_required_after_kill(self):
        scans = iter([[11, 12], [12], [], []])
        killed = []
        with patch.object(stop_gate, 'uid_processes', side_effect=lambda uid: next(scans)), \
                patch.object(stop_gate, '_kill', side_effect=lambda pid, uid: killed.append(pid)):
            resources = stop_gate.stop_uids((PI,), grace=5)
        self.assertEqual(resources, (f'uid:{PI}',))
        self.assertEqual(killed, [11, 12, 12])

    def test_surviving_uid_raises_without_proof(self):
        with patch.object(stop_gate, 'uid_processes', return_value=[11]), \
                patch.object(stop_gate, '_kill'), patch.object(stop_gate.time, 'sleep'):
            with self.assertRaisesRegex(ControlError, 'writers_not_stopped'):
                stop_gate.stop_uids((PI,), grace=0.05)

    def test_scan_failure_is_not_treated_as_stopped(self):
        with patch.object(stop_gate, 'uid_processes', side_effect=ControlError('process_safety_unverified')):
            with self.assertRaisesRegex(ControlError, 'process_safety_unverified'):
                stop_gate.stop_uids((PI,))

    def test_reaped_process_is_not_closed_or_signalled(self):
        with patch.object(signal, 'pidfd_send_signal', create=True), \
                patch.object(os, 'pidfd_open', create=True, side_effect=ProcessLookupError):
            stop_gate._kill(11, PI)
        with patch.object(signal, 'pidfd_send_signal', create=True), \
                patch.object(os, 'pidfd_open', create=True, return_value=3), \
                patch.object(stop_gate, 'uid_processes', return_value=[]), \
                patch.object(os, 'close') as close:
            stop_gate._kill(11, PI)
            close.assert_called_once_with(3)

    def test_missing_pidfd_support_fails_closed(self):
        with patch.object(stop_gate.os, 'pidfd_open', new=None, create=True):
            with self.assertRaisesRegex(ControlError, 'process_safety_unverified'):
                stop_gate._kill(11, PI)

    def test_combined_proof_binds_attempt_identity_and_resources(self):
        with patch.object(stop_gate, 'uid_processes', return_value=[]), \
                patch.object(stop_gate, '_kill'):
            proof = stop_gate.combined_stop_proof(
                ATTEMPT, (PI, PREVIEW), resource_ids=('container:abc',))
        self.assertIs(type(proof), StopProof)
        self.assertEqual(proof.uid, ATTEMPT.uid)
        self.assertIs(proof.all_writers_stopped, True)
        self.assertEqual(proof.resource_ids,
                         ('container:abc', f'uid:{PI}', f'uid:{PREVIEW}'))

    def test_invalid_attempt_or_resource_ids_are_rejected(self):
        with patch.object(stop_gate, 'uid_processes') as scan:
            for attempt in (object(), {'uid': 'x'}):
                with self.subTest(attempt=attempt), self.assertRaises(ControlError):
                    stop_gate.combined_stop_proof(attempt, (PI,))
            for value in ('', 'x' * 201, 'bad\nid', 7):
                with self.subTest(value=value), self.assertRaises(ControlError):
                    stop_gate.combined_stop_proof(ATTEMPT, (PI,), resource_ids=(value,))
            scan.assert_not_called()


class StopGateCollectorIntegrationTests(unittest.TestCase):
    """A combined proof must be the value the collector actually consumes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        self.config = RuntimeConfig(self.root)
        self.attempt = Attempt('11111111-1111-4111-8111-111111111111',
                               '22222222-2222-4222-8222-222222222222', 1, 2,
                               'generate', {'hash': 'a' * 64}, 'session')
        root = self.root / f'projects/{self.attempt.project_id}/attempts/{self.attempt.job_id}/1-2'
        names = ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base')
        self.layout = Layout(root, *(root / name for name in names))
        for name in names:
            getattr(self.layout, name).mkdir(parents=True)
        (self.layout.project / 'deck').mkdir()
        (self.layout.project / 'deck' / 'notes.md').write_bytes(b'notes')

    def test_collector_accepts_combined_proof_and_rejects_unproven_stop(self):
        with patch.object(stop_gate, 'uid_processes', return_value=[]), patch.object(stop_gate, '_kill'):
            proof = stop_gate.combined_stop_proof(self.attempt, (PI, PREVIEW),
                                                  resource_ids=('container:abc',))
        frozen = pi_collect.freeze_candidate(self.config, self.attempt, self.layout, proof,
                                            candidate_id='33333333-3333-4333-8333-333333333333')
        self.assertEqual(frozen.manifest['uid'], self.attempt.uid)
        self.assertEqual([row['path'] for row in frozen.indexes['project']['files']],
                         ['deck/notes.md'])

        for stop_proof in (StopProof(self.attempt.uid, False), StopProof('other-uid', True),
                           replace(proof, all_writers_stopped=False)):
            with self.subTest(stop_proof=stop_proof), self.assertRaisesRegex(ControlError, 'writers_not_stopped'):
                pi_collect.freeze_candidate(self.config, self.attempt, self.layout, stop_proof,
                                            candidate_id='44444444-4444-4444-8444-444444444444')
        self.assertFalse((self.root / f'projects/{self.attempt.project_id}/candidates/'
                                     '44444444-4444-4444-8444-444444444444').exists())


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0,
                     'requires a Linux root controller with CAP_SETUID/CAP_KILL')
class StopGateLinuxTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for process in self.children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        for uid in (PI, PREVIEW, BYSTANDER):
            self.assertEqual(stop_gate.uid_processes(uid), [])

    def test_stops_every_attempt_uid_and_leaves_other_uid_running(self):
        pi, preview, bystander = _spawn(PI), _spawn(PREVIEW), _spawn(BYSTANDER)
        self.children += [pi, preview, bystander]
        for uid, process in ((PI, pi), (PREVIEW, preview), (BYSTANDER, bystander)):
            self.assertIn(process.pid, stop_gate.uid_processes(uid))

        proof = stop_gate.combined_stop_proof(ATTEMPT, (PI, PREVIEW), grace=10)

        self.assertIs(proof.all_writers_stopped, True)
        self.assertEqual(proof.resource_ids, (f'uid:{PI}', f'uid:{PREVIEW}'))
        self.assertEqual(stop_gate.uid_processes(PI), [])
        self.assertEqual(stop_gate.uid_processes(PREVIEW), [])
        self.assertEqual(pi.wait(timeout=5), -9)
        self.assertEqual(preview.wait(timeout=5), -9)
        self.assertIsNone(bystander.poll())
        self.assertIn(bystander.pid, stop_gate.uid_processes(BYSTANDER))

    def test_descendant_of_attempt_uid_is_stopped_too(self):
        child = _spawn(PI)
        self.children.append(child)
        # A surviving descendant must be killed even if the parent is still alive.
        grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                                      preexec_fn=_drop(PI), stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (grandchild.poll() is None and grandchild.kill(), grandchild.wait(timeout=10)))
        self.assertEqual(len(stop_gate.uid_processes(PI)), 2)

        proof = stop_gate.combined_stop_proof(ATTEMPT, (PI,), grace=10)

        self.assertIs(proof.all_writers_stopped, True)
        self.assertEqual(stop_gate.uid_processes(PI), [])
        self.assertEqual(child.wait(timeout=5), -9)
        self.assertEqual(grandchild.wait(timeout=5), -9)


if __name__ == '__main__':
    unittest.main()
