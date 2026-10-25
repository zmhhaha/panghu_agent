"""Deterministic single-attempt dispatcher. It never executes an Agent itself."""
from __future__ import annotations

import time

from .contracts import ExecutionEnvelope, InlineFile, ServiceError, TERMINAL_STATES, verify_receipt


class Dispatcher:
    def __init__(self, store, adapters: dict, *, clock=time.time, lease_seconds=60):
        if not 15 <= lease_seconds <= 300:
            raise ValueError('Invalid dispatcher lease')
        self.store, self.adapters = store, dict(adapters)
        self.clock, self.lease_seconds = clock, lease_seconds

    def _envelope(self, job):
        pins, request = job['pins'], job['request']
        inputs = []
        for ref in request.get('inputs', []):
            value = self.store.get_input(job['tenant'], job['caller'], ref['input_id'])
            if value['sha256'] != ref['sha256']:
                raise ServiceError('input_integrity_invalid', 503)
            inputs.append(InlineFile.from_bytes(value['name'], value['content']))
        return ExecutionEnvelope(job_id=job['job_id'], attempt_id=job['attempt_id'], fence=job['fence'],
            engine=pins['engine'], profile=request['profile'], profile_revision=request['profile_revision'],
            runtime_revision=pins['runtime_revision'], image_digest=pins['image_digest'],
            deadline_at=float(job['deadline_at']), lease_expires_at=float(job['lease_expires_at']),
            task=request['task'], inputs=inputs)

    def _quarantine(self, job, code):
        return self.store.finish(job['job_id'], job['attempt_id'], job['fence'],
            {'state': 'quarantined', 'error_code': code, 'usage': None}, [], stopped=False)

    def dispatch_once(self):
        self.store.reconcile_expired()
        job = self.store.claim(lease_seconds=self.lease_seconds)
        if job is None:
            return None
        adapter = self.adapters.get(job['pins']['engine'])
        if adapter is None:
            return self._quarantine(job, 'worker_unavailable')
        try:
            envelope = self._envelope(job)
            observed = adapter.start(envelope)
            if (observed.get('attempt_id') != envelope.attempt_id
                    or observed.get('request_sha256') != envelope.request_sha256):
                return self._quarantine(job, 'worker_identity_invalid')
        except Exception:
            # A lost PUT response is uncertain, not authorization to launch again.
            # The persisted attempt remains the only resource reconciliation key.
            try:
                envelope = self._envelope(job)
                observed = adapter.inspect(envelope)
                if (observed.get('attempt_id') != envelope.attempt_id
                        or observed.get('request_sha256') != envelope.request_sha256):
                    raise ServiceError('worker_identity_invalid', 503)
            except Exception:
                return self._quarantine(job, 'worker_admission_uncertain')
        self.store.mark_running(job['job_id'], job['attempt_id'], job['fence'])
        return self.store.get_job(job['tenant'], job['caller'], job['job_id'])

    def poll(self, job):
        if job['state'] in TERMINAL_STATES:
            return job
        adapter = self.adapters.get(job['pins']['engine'])
        if adapter is None:
            return self._quarantine(job, 'worker_unavailable')
        envelope = self._envelope(job)
        # Recheck the authoritative row; caller snapshots cannot undo a cancel.
        current = self.store.get_job(job['tenant'], job['caller'], job['job_id'])
        if current['state'] in TERMINAL_STATES:
            return current
        try:
            observed = adapter.inspect(envelope)
            if (observed.get('attempt_id') != envelope.attempt_id
                    or observed.get('request_sha256') != envelope.request_sha256):
                return self._quarantine(job, 'worker_identity_invalid')
            # Transport failure must not extend an unobserved Worker's lease.
            if not self.store.renew(job['job_id'], job['attempt_id'], job['fence'], self.lease_seconds):
                return self._quarantine(job, 'lease_lost')
            current = self.store.get_job(job['tenant'], job['caller'], job['job_id'])
            if observed.get('state') not in TERMINAL_STATES:
                if current['cancel_requested'] or self.clock() >= job['deadline_at']:
                    adapter.cancel(envelope)
                else:
                    try:
                        adapter.renew(envelope, float(current['lease_expires_at']))
                    except ServiceError:
                        # Completion/cancel can win the renew race. Next poll
                        # reads the same execution instead of losing its result.
                        pass
                return current
            result = adapter.result(envelope)
            if not verify_receipt(envelope, result, adapter.receipt_key, self.clock()):
                return self._quarantine(job, 'worker_stop_receipt_invalid')
            files = [{'name': f.name, 'content': f.content()} for f in result.artifacts]
            if len({f['name'] for f in files}) != len(files) or sum(len(f['content']) for f in files) > 8 * 1024 * 1024:
                return self._quarantine(job, 'worker_artifacts_invalid')
            outcome = result.model_dump(exclude={'artifacts'})
            if current['cancel_requested']:
                outcome['state'], files = 'cancelled', []
            elif self.clock() >= job['deadline_at']:
                outcome['state'], files = 'timed_out', []
            if outcome['state'] not in {'completed', 'needs_input'}:
                files = []
            return self.store.finish(job['job_id'], job['attempt_id'], job['fence'], outcome, files, stopped=True)
        except (ServiceError, ValueError, RecursionError, TypeError):
            # Read failure is not terminal evidence. Keep authority until the
            # existing lease expires; the Worker independently stops on expiry.
            return self.store.get_job(job['tenant'], job['caller'], job['job_id'])

    def reconcile(self):
        """On control restart, reconcile the persisted attempt; never re-submit."""
        self.store.reconcile_expired()
        active = self.store.active()
        return None if active is None else self.poll(active)

    def tick(self):
        self.store.reconcile_expired()
        active = self.store.active()
        if active is not None:
            return self.poll(active)
        return self.dispatch_once()
