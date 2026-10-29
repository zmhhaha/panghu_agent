"""Durable task reconciliation; restart recovery never repeats an ambiguous POST."""
from __future__ import annotations

import logging
import threading
import time

from agent_service.contracts import ServiceError
from agent_service.profiles.video import validate_video_result

LOG = logging.getLogger(__name__)


class Workflow:
    def __init__(self, store, gateway=None):
        self.store, self.gateway = store, gateway
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def require_gateway(self):
        if self.gateway is None:
            raise ServiceError('agent_not_configured', 503)
        return self.gateway

    def start(self):
        self.thread = threading.Thread(target=self._run, name='video-reconcile', daemon=True)
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
        if self.gateway is None or not self.lock.acquire(blocking=False):
            return
        try:
            for op in self.store.active_operations():
                try:
                    self._step(op)
                except ServiceError as exc:
                    # A remote outage is recoverable; validation failures are not publishable.
                    if exc.status >= 500 and exc.code.startswith('service_'):
                        self.store.update_operation(op['id'], error=exc.code)
                    else:
                        self.store.update_operation(op['id'], status='failed', error=exc.code)
                except Exception:
                    LOG.exception('Operation reconciliation failed: %s', op['id'])
                    self.store.update_operation(op['id'], error='reconciliation_failed')
        finally:
            self.lock.release()

    def _step(self, op):
        gateway = self.require_gateway()
        op = self.store.operation(op['id'])
        if op['status'] not in {'queued', 'submitting', 'running', 'cancelling'}:
            return
        if not op['job_id']:
            if not op['submit_attempted']:
                if op['cancel_requested'] or time.time() >= op['deadline_at']:
                    self.store.update_operation(op['id'], status='cancelled' if op['cancel_requested'] else 'timed_out', error=None)
                    return
                if op['input_ref'] is None:
                    ref = gateway.upload(op['document'])
                    op = self.store.update_operation(op['id'], input_ref=ref)
                if op['cancel_requested']:
                    self.store.update_operation(op['id'], status='cancelled', error=None)
                    return
                # Persist before POST. A crash even here favors no duplicate execution.
                op = self.store.claim_submission(op['id'])
                if op is None:
                    return
                try:
                    job = gateway.submit(op['id'], 'storyboard' if op['kind'] == 'storyboard' else 'scene',
                                         op['document'], op['deadline_at'], input_ref=op['input_ref'])
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
                        # The Agent endpoint enforces the same absolute deadline; late POSTs cannot start work.
                        if time.time() >= op['deadline_at']:
                            self.store.update_operation(op['id'], status='cancelled' if op['cancel_requested'] else 'timed_out',
                                                        error='submission_unconfirmed')
                        return
                    raise
            op = self.store.update_operation(op['id'], job_id=job['job_id'], error=None)
        job = gateway.poll(op['job_id'])
        op = self.store.operation(op['id'])
        state = job['state']
        if state == 'completed':
            result = gateway.collect(op['job_id'], 'storyboard' if op['kind'] == 'storyboard' else 'scene', op['input_sha256'])
            valid = validate_video_result(result, op['input_sha256'], 'storyboard' if op['kind'] == 'storyboard' else 'scene')
            if op['kind'] != 'storyboard' and valid['output']['format'] != op['document']['format']:
                raise ServiceError('video_format_mismatch', 422)
            self.store.complete(op['id'], valid['output'])
        elif state in {'failed', 'needs_input', 'cancelled', 'timed_out', 'quarantined'}:
            self.store.update_operation(op['id'], status=state if state != 'quarantined' else 'failed',
                                        error=None if state == 'cancelled' else 'agent_' + state)
        elif op['cancel_requested'] or time.time() >= op['deadline_at']:
            gateway.cancel(op['job_id'])
            self.store.update_operation(op['id'], status='cancelling', error=None)
        else:
            self.store.update_operation(op['id'], status='running', error=None)
