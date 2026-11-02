"""Optional durable reconciliation for motion candidates, isolated from static v1."""
from __future__ import annotations

import logging
import threading
import time

from agent_service.contracts import ServiceError
from agent_service.profiles.video_motion import validate_motion_result
from .motion_store import ACTIVE


LOG = logging.getLogger(__name__)
# Four total cancel POST attempts. They target one existing job and never admit
# a new model task. Exhaustion remains visibly uncertain until remote terminal.
CANCEL_RETRY_DELAYS = (2.0, 5.0, 15.0, 30.0)


class MotionWorkflow:
    def __init__(self, store, gateway=None, *, enabled=False):
        if type(enabled) is not bool:
            raise ValueError('An explicit boolean motion feature gate is required')
        self.store, self.gateway, self.enabled = store, gateway, enabled
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        if self.enabled and self.gateway is not None and self.thread is None:
            self.thread = threading.Thread(target=self._run, name='video-motion-reconcile', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=12)

    def _run(self):
        while not self.stopping.is_set():
            self.tick()
            self.stopping.wait(2)

    def tick(self):
        if not self.enabled or self.gateway is None or not self.lock.acquire(blocking=False):
            return
        try:
            for op in self.store.active_operations():
                try:
                    self._step(op)
                except ServiceError as exc:
                    if exc.status >= 500 and exc.code.startswith('service_'):
                        self.store.update_operation(op['id'], error=exc.code)
                    else:
                        self.store.update_operation(op['id'], status='failed', error=exc.code)
                except Exception:
                    LOG.exception('Motion reconciliation failed: %s', op['id'])
                    self.store.update_operation(op['id'], error='reconciliation_failed')
        finally:
            self.lock.release()

    def _step(self, op):
        gateway = self.gateway
        op = self.store.operation(op['id'])
        if op['status'] not in ACTIVE:
            return
        if not op['job_id']:
            if not op['submit_attempted']:
                if op['cancel_requested'] or time.time() >= op['deadline_at']:
                    self.store.update_operation(op['id'], status='cancelled' if op['cancel_requested'] else 'timed_out', error=None)
                    return
                if op['input_ref'] is None:
                    ref = gateway.upload(op['document'])
                    op = self.store.update_operation(op['id'], input_ref=ref)
                op = self.store.claim_submission(op['id'])
                if op is None:
                    return
                try:
                    job = gateway.submit(op['id'], op['document'], op['deadline_at'], input_ref=op['input_ref'])
                except ServiceError as exc:
                    if exc.status >= 500 and exc.code.startswith(('service_', 'http_')):
                        self.store.update_operation(op['id'], error='submission_uncertain')
                        return
                    raise
            else:
                try:
                    job = gateway.lookup(op['id'])
                except ServiceError as exc:
                    if exc.status == 404:
                        if time.time() >= op['deadline_at']:
                            self.store.update_operation(op['id'],
                                status='cancelled' if op['cancel_requested'] else 'timed_out',
                                error='submission_unconfirmed')
                        return
                    raise
            op = self.store.update_operation(op['id'], job_id=job['job_id'], error=None)
        job = gateway.poll(op['job_id'])
        op = self.store.operation(op['id'])
        state = job['state']
        if state == 'completed':
            result = gateway.collect(op['job_id'], op['input_sha256'], op['document']['beats'])
            validate_motion_result(result, op['input_sha256'], op['document']['beats'])
            self.store.complete(op['id'], result)
        elif state in {'failed', 'needs_input', 'cancelled', 'timed_out', 'quarantined'}:
            self.store.update_operation(op['id'], status='failed' if state == 'quarantined' else state,
                error=None if state == 'cancelled' else 'agent_' + state)
        elif state == 'cancelling':
            self.store.update_operation(op['id'], status='cancelling')
        elif state in {'queued', 'running'} and (op['cancel_requested'] or time.time() >= op['deadline_at']):
            claimed = self.store.claim_cancellation(op['id'], now=time.time(), retry_delays=CANCEL_RETRY_DELAYS)
            if claimed is not None:
                try:
                    gateway.cancel(op['job_id'])
                except ServiceError as exc:
                    if exc.status >= 500:
                        self.store.update_operation(op['id'], error='cancellation_uncertain')
                        return
                    raise
        else:
            self.store.update_operation(op['id'], status='running', error=None)
