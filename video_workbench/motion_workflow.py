"""Optional durable reconciliation for motion candidates, isolated from static v1."""
from __future__ import annotations

import logging
import threading
import time

from agent_service.contracts import ServiceError
from .motion_store import ACTIVE, operation_diagram_kind


LOG = logging.getLogger(__name__)
# Four total cancel POST attempts. They target one existing job and never admit
# a new model task. Exhaustion remains visibly uncertain until remote terminal.
CANCEL_RETRY_DELAYS = (2.0, 5.0, 15.0, 30.0)


class MotionWorkflow:
    def __init__(self, store, gateway=None, *, enabled=False, process_gateway=None, process_enabled=False):
        if type(enabled) is not bool or type(process_enabled) is not bool:
            raise ValueError('An explicit boolean motion feature gate is required')
        self.store, self.gateway, self.enabled = store, gateway, enabled
        self.process_gateway, self.process_enabled = process_gateway, process_enabled
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def _enabled(self, diagram_kind):
        return self.enabled and (diagram_kind == 'diagram' or self.process_enabled)

    def _gateway(self, diagram_kind):
        return self.process_gateway if diagram_kind == 'process' else self.gateway

    def require_gateway(self, diagram_kind='diagram'):
        if type(diagram_kind) is not str or diagram_kind not in {'diagram', 'process'}:
            raise ServiceError('invalid_motion_request', 422)
        if not self.enabled:
            raise ServiceError('motion_disabled', 503)
        if diagram_kind == 'process' and not self.process_enabled:
            raise ServiceError('process_disabled', 503)
        gateway = self._gateway(diagram_kind)
        if gateway is None:
            raise ServiceError('agent_not_configured', 503)
        return gateway

    def start(self):
        active = self.store.active_operations()
        for op in active:
            kind = operation_diagram_kind(op)
            if not self._enabled(kind) and not op['submit_attempted'] and not op['job_id']:
                self.store.update_operation(op['id'], status='cancelled',
                    error='motion_disabled' if not self.enabled else 'process_disabled')
        admitted = any(op['submit_attempted'] or op['job_id'] for op in active)
        if (self.gateway is not None or self.process_gateway is not None) and self.thread is None and (self.enabled or admitted):
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
        if (self.gateway is None and self.process_gateway is None) or not self.lock.acquire(blocking=False):
            return
        try:
            for op in self.store.active_operations():
                try:
                    self._step(op)
                except ServiceError as exc:
                    if exc.status >= 500 and exc.code.startswith('service_'):
                        self.store.update_operation(op['id'], error=exc.code)
                    else:
                        self.store.update_operation(op['id'], status='failed', error=exc.code,
                            motion_context=getattr(exc, 'motion_context', None))
                except Exception:
                    LOG.exception('Motion reconciliation failed: %s', op['id'])
                    self.store.update_operation(op['id'], error='reconciliation_failed')
        finally:
            self.lock.release()

    def _step(self, op):
        op = self.store.operation(op['id'])
        if op['status'] not in ACTIVE:
            return
        kind = operation_diagram_kind(op)
        if not self._enabled(kind) and not op['submit_attempted'] and not op['job_id']:
            self.store.update_operation(op['id'], status='cancelled',
                error='motion_disabled' if not self.enabled else 'process_disabled')
            return
        gateway = self._gateway(kind)
        if gateway is None:
            # An admitted job retains its original profile after a gate or
            # configuration change. Never look it up through another adapter.
            self.store.update_operation(op['id'], error='agent_not_configured')
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
            result = None if op['cancel_requested'] or time.time() >= op['deadline_at'] else gateway.collect(
                op['job_id'], op['input_sha256'], op['document']['beats'])
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
