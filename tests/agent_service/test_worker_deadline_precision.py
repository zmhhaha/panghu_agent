"""Native clocks round inward; exact admission and watchdog clocks stay intact."""
from datetime import datetime, timezone
import math
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from agent_service.contracts import ExecutionEnvelope, ProfilePolicy, validate_workspace_task
from agent_service.engines import NativeOutcome
from agent_service.worker_api import WorkerHost, _native_deadline


BASE = 1793060000.125
ROUNDS_UP = math.nextafter(BASE, -math.inf)
IMAGE = 'sha256:' + '4' * 64
KEY = b'precision-fixture-receipt-key-00000000'


class CaptureDriver:
    ready = True

    def __init__(self, engine, original_deadline, *, wait=False):
        self.engine, self.original_deadline, self.wait = engine, original_deadline, wait
        self.request = None
        self.started, self.cancelled = threading.Event(), threading.Event()

    def execute(self, request, workspace, emit, cancel):
        self.request = request
        # This mirrors the strict scoped-grant comparison in both real engines
        # and the candidate wire builder; an upward rounding must fail here.
        assert request.deadline_at.timestamp() <= self.original_deadline
        self.started.set()
        if self.wait:
            assert cancel.wait(2), 'Watchdog failed to stop the offline fixture'
            self.cancelled.set()
        return NativeOutcome('failed', error_code='offline_precision_fixture', stopped=True,
                             resource_ids=('offline-precision-writer',))


class DeadlinePrecisionTests(unittest.TestCase):
    def envelope(self, policy, deadline, lease):
        return ExecutionEnvelope(job_id=str(uuid4()), attempt_id=str(uuid4()), fence=1,
            **policy.pins(), deadline_at=deadline, lease_expires_at=lease,
            task={'prompt': 'Offline precision fixture only'})

    def test_nextafter_fractional_values_never_extend_native_deadline(self):
        self.assertGreater(datetime.fromtimestamp(ROUNDS_UP, timezone.utc).timestamp(), ROUNDS_UP)
        for base in (1793060000., BASE, 1793060000.999999, 1793060001.):
            for value in (math.nextafter(base, -math.inf), base, math.nextafter(base, math.inf)):
                with self.subTest(value=value):
                    result = _native_deadline(value)
                    self.assertEqual(result.utcoffset().total_seconds(), 0)
                    self.assertLessEqual(result.timestamp(), value)
                    self.assertLess(value - result.timestamp(), 0.0000011)
                    rounded = datetime.fromtimestamp(value, timezone.utc)
                    if rounded.timestamp() <= value:
                        self.assertEqual(result, rounded)

    def test_both_engine_requests_fit_exact_grant_without_mutating_envelope_or_lease(self):
        for engine in ('pi', 'hermes'):
            for deadline in (ROUNDS_UP, BASE, math.nextafter(BASE, math.inf)):
                with self.subTest(engine=engine, deadline=deadline), tempfile.TemporaryDirectory() as directory:
                    policy = ProfilePolicy('workspace-task.v1', 'r1', engine, 'runtime-r1', IMAGE,
                                           enabled=True, task_validator=validate_workspace_task)
                    lease = math.nextafter(deadline - 1., -math.inf)
                    envelope = self.envelope(policy, deadline, lease)
                    identity = envelope.request_sha256
                    driver = CaptureDriver(engine, deadline)
                    root = Path(directory)
                    host = WorkerHost(policy, driver, root / 'control', root / 'work', KEY,
                                      clock=lambda: deadline - 5.)
                    host.initialize()
                    try:
                        host.start(envelope)
                        host._thread.join(3)
                        self.assertFalse(host._thread.is_alive())
                        self.assertIsNotNone(driver.request)
                        self.assertLessEqual(driver.request.deadline_at.timestamp(), deadline)
                        self.assertEqual(host.result(envelope.attempt_id)['error_code'], 'offline_precision_fixture')
                        self.assertEqual(host._state['envelope']['deadline_at'].hex(), deadline.hex())
                        self.assertEqual(host._state['lease_expires_at'].hex(), lease.hex())
                        self.assertEqual(envelope.request_sha256, identity)
                    finally:
                        host.close()

    def test_watchdog_uses_exact_float_after_native_datetime_rounds_inward(self):
        deadline = ROUNDS_UP
        now = [deadline - 5.]
        policy = ProfilePolicy('workspace-task.v1', 'r1', 'pi', 'runtime-r1', IMAGE,
                               enabled=True, task_validator=validate_workspace_task)
        envelope = self.envelope(policy, deadline, deadline)
        driver = CaptureDriver('pi', deadline, wait=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = WorkerHost(policy, driver, root / 'control', root / 'work', KEY, clock=lambda: now[0])
            host.initialize()
            try:
                host.start(envelope)
                self.assertTrue(driver.started.wait(2))
                # Already later than the inward native datetime, still earlier
                # than the authoritative execution/lease float.
                now[0] = math.nextafter(deadline, -math.inf)
                self.assertGreater(now[0], driver.request.deadline_at.timestamp())
                self.assertFalse(driver.cancelled.wait(.12))
                now[0] = deadline
                self.assertTrue(driver.cancelled.wait(2))
                host._thread.join(3)
                self.assertEqual(host.result(envelope.attempt_id)['state'], 'timed_out')
                self.assertEqual(host._state['lease_expires_at'].hex(), deadline.hex())
            finally:
                host.close()


if __name__ == '__main__':
    unittest.main()
