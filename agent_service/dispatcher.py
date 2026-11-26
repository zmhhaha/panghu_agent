"""Deterministic single-attempt dispatcher. It never executes an Agent itself."""
from __future__ import annotations

from contextlib import contextmanager
import threading
import time

from .contracts import ServiceError, TERMINAL_STATES, verify_receipt
from .event_ingestion import sync_worker_events


class Dispatcher:
    preparation_heartbeat_seconds = 5.0

    def __init__(self, store, adapters: dict, *, clock=time.time, lease_seconds=60,
                 slot_id=None):
        if not 15 <= lease_seconds <= 300:
            raise ValueError('Invalid dispatcher lease')
        self.store, self.adapters = store, dict(adapters)
        self.clock, self.lease_seconds = clock, lease_seconds
        self.slot_id = slot_id
        if getattr(store, 'execution_slots', 1) > 1 and any(
                getattr(adapter, 'provisioning', 'external_static') != 'kubernetes_per_attempt'
                for adapter in self.adapters.values()):
            raise ValueError('Shared slots require per-attempt Workers')

    def _envelope(self, job):
        return self.store.execution_envelope(job['job_id'], job['attempt_id'], job['fence'])

    @contextmanager
    def _preparing(self, job):
        """Heartbeat only the fenced interval before any native-start claim.

        Large immutable inputs and cold Pods are preparation, not observed
        execution. The Store atomically refuses this authority after start,
        cancellation, expiry or the fixed task deadline. Running Workers still
        require an actual successful inspection before their normal renewal.
        """
        adapter = self.adapters.get(job['pins']['engine'])
        if (job['state'] != 'starting'
                or getattr(adapter, 'provisioning', None) != 'kubernetes_per_attempt'):
            yield job
            return
        def pulse():
            return self.store.renew_preparing(job['job_id'], job['attempt_id'], job['fence'], self.lease_seconds)
        try:
            current = pulse()
        except Exception:
            current = None
        if current is None:
            yield job
            return
        finished = threading.Event()
        interval = min(self.preparation_heartbeat_seconds, self.lease_seconds / 4)
        def heartbeat():
            while not finished.wait(interval):
                try:
                    if pulse() is None:
                        return
                except Exception:
                    return
        thread = threading.Thread(target=heartbeat, name='agent-preparing-' + job['attempt_id'], daemon=True)
        thread.start()
        try:
            yield current
        finally:
            finished.set()
            thread.join(timeout=5)

    def _quarantine(self, job, code):
        return self.store.finish(job['job_id'], job['attempt_id'], job['fence'],
            {'state': 'quarantined', 'error_code': code, 'usage': None}, [], stopped=False)

    def _control(self, job):
        adapter = self.adapters.get(job['pins']['engine'])
        read = getattr(self.store, 'worker_control', None)
        if (getattr(adapter, 'provisioning', None) == 'kubernetes_per_attempt'
                and callable(read)):
            return read(job['job_id'], job['attempt_id'], job['fence'])
        return None

    def _poll_envelope(self, job):
        return self._control(job) or self._envelope(job)

    @staticmethod
    def _receipt_key(adapter, envelope):
        derive = getattr(adapter, 'receipt_key_for', None)
        return derive(envelope) if callable(derive) else adapter.receipt_key

    @staticmethod
    def _resolve_uncreated(adapter, envelope, exc=None):
        resolve = getattr(adapter, 'resolve_uncreated', None)
        if not callable(resolve):
            return None
        code = (exc.code if isinstance(exc, ServiceError) and exc.code in {
            'worker_uid_range_exhausted', 'execution_pins_mismatch'} else 'worker_not_created')
        try:
            return resolve(envelope, code)
        except (ServiceError, ValueError, TypeError):
            return None

    def dispatch_once(self):
        self.store.reconcile_expired()
        job = self.store.claim(lease_seconds=self.lease_seconds, provisioning={
            engine: getattr(adapter, 'provisioning', 'external_static')
            for engine, adapter in self.adapters.items()}, slot_id=self.slot_id)
        if job is None:
            return None
        adapter = self.adapters.get(job['pins']['engine'])
        if adapter is None:
            return self._quarantine(job, 'worker_unavailable')
        envelope = None
        try:
            with self._preparing(job) as preparing:
                envelope = self._envelope(preparing)
                observed = adapter.start(envelope)
            if (observed.get('attempt_id') != envelope.attempt_id
                    or observed.get('request_sha256') != envelope.request_sha256):
                return self._quarantine(job, 'worker_identity_invalid')
        except Exception as exc:
            if envelope is not None:
                unallocated = self._resolve_uncreated(adapter, envelope, exc)
                if unallocated is not None:
                    return unallocated
            # A lost PUT response is uncertain, not authorization to launch again.
            # The persisted attempt remains the only resource reconciliation key.
            try:
                with self._preparing(job) as preparing:
                    envelope = self._poll_envelope(preparing)
                    observed = adapter.inspect(envelope)
                if (observed.get('attempt_id') != envelope.attempt_id
                        or observed.get('request_sha256') != envelope.request_sha256):
                    raise ServiceError('worker_identity_invalid', 503)
            except Exception:
                return self._quarantine(job, 'worker_admission_uncertain')
        if observed.get('state') == 'running':
            envelope = self._control(job) or envelope
            # Cold admission can consume most of the first lease. Deliver a
            # fresh bound lease now, before another tick rebuilds large inputs.
            if not self.store.renew(job['job_id'], job['attempt_id'], job['fence'], self.lease_seconds):
                return self._quarantine(job, 'lease_lost')
            current = self.store.get_job(job['tenant'], job['caller'], job['job_id'])
            if current['cancel_requested'] or self.clock() >= job['deadline_at']:
                adapter.cancel(envelope)
            else:
                try:
                    adapter.renew(envelope, float(current['lease_expires_at']))
                except ServiceError:
                    # Completion can win this race; reconcile the same attempt.
                    pass
            self.store.mark_running(job['job_id'], job['attempt_id'], job['fence'])
        return self.store.get_job(job['tenant'], job['caller'], job['job_id'])

    def poll(self, job):
        with self._preparing(job) as preparing:
            return self._poll(preparing)

    def _poll(self, job):
        if job['state'] == 'quarantined':
            adapter = self.adapters.get(job['pins']['engine'])
            recover = getattr(adapter, 'reconcile_quarantined', None)
            if not callable(recover):
                return job
            try:
                envelope = self._poll_envelope(job)
                unallocated = self._resolve_uncreated(adapter, envelope)
                if unallocated is not None:
                    return unallocated
                result = recover(envelope)
                if not verify_receipt(envelope, result, self._receipt_key(adapter, envelope), self.clock()):
                    return job
                return self.store.resolve_worker_quarantine(job['job_id'], job['attempt_id'], job['fence'])
            except (ServiceError, ValueError, TypeError):
                return self.store.get_job(job['tenant'], job['caller'], job['job_id'])
        if job['state'] in TERMINAL_STATES:
            return job
        adapter = self.adapters.get(job['pins']['engine'])
        if adapter is None:
            return self._quarantine(job, 'worker_unavailable')
        envelope = self._poll_envelope(job)
        # Recheck the authoritative row; caller snapshots cannot undo a cancel.
        current = self.store.get_job(job['tenant'], job['caller'], job['job_id'])
        if current['state'] in TERMINAL_STATES:
            return current
        try:
            # A cold capsule must receive cancellation even before native start
            # can acquire its admission CAS. Inspect cannot bypass that guard.
            if current['cancel_requested'] or self.clock() >= job['deadline_at']:
                adapter.cancel(envelope)
            observed = adapter.inspect(envelope)
            if (observed.get('attempt_id') != envelope.attempt_id
                    or observed.get('request_sha256') != envelope.request_sha256):
                return self._quarantine(job, 'worker_identity_invalid')
            # inspect may have claimed the first native start of a cold Pod.
            # All subsequent control uses its durable, fully measured identity.
            envelope = self._control(job) or envelope
            # Transport failure must not extend an unobserved Worker's lease.
            if not self.store.renew(job['job_id'], job['attempt_id'], job['fence'], self.lease_seconds):
                return self._quarantine(job, 'lease_lost')
            current = self.store.get_job(job['tenant'], job['caller'], job['job_id'])
            if observed.get('state') not in TERMINAL_STATES:
                if current['cancel_requested'] or self.clock() >= job['deadline_at']:
                    adapter.cancel(envelope)
                else:
                    try:
                        # Event pages can be slow or paginated. The Worker must
                        # receive its renewed lease before that optional I/O.
                        adapter.renew(envelope, float(current['lease_expires_at']))
                    except ServiceError:
                        # Completion/cancel can win the renew race. Next poll
                        # reads this execution rather than launching a new one.
                        pass
            if observed.get('state') == 'running' and current['state'] == 'starting':
                self.store.mark_running(job['job_id'], job['attempt_id'], job['fence'])
                current = self.store.get_job(job['tenant'], job['caller'], job['job_id'])
            synced = sync_worker_events(self.store, adapter, envelope)
            if synced['has_more']:
                return current
            if observed.get('state') not in TERMINAL_STATES:
                return current
            result = adapter.result(envelope)
            if not verify_receipt(envelope, result, self._receipt_key(adapter, envelope), self.clock()):
                return self._quarantine(job, 'worker_stop_receipt_invalid')
            from .artifacts import unique_paths
            files = [{'name': f.name, 'content': f.content(limit=envelope.max_artifact_file_bytes)}
                     for f in result.artifacts]
            unique_paths(file['name'] for file in files)
            if (len(files) > envelope.max_artifact_files
                    or sum(len(f['content']) for f in files) > envelope.max_artifact_bytes):
                return self._quarantine(job, 'worker_artifacts_invalid')
            outcome = result.model_dump(exclude={'artifacts'})
            if current['cancel_requested']:
                outcome['state'], files = 'cancelled', []
            elif self.clock() >= job['deadline_at']:
                outcome['state'], files = 'timed_out', []
            if outcome['state'] not in {'completed', 'needs_input'}:
                files = []
            return self.store.finish(job['job_id'], job['attempt_id'], job['fence'], outcome, files, stopped=True)
        except (ServiceError, ValueError, RecursionError, TypeError) as exc:
            unallocated = self._resolve_uncreated(adapter, envelope, exc)
            if unallocated is not None:
                return unallocated
            if isinstance(exc, ServiceError) and exc.code in {
                    'worker_events_invalid', 'invalid_worker_events', 'worker_resource_replaced',
                    'kubernetes_resource_mismatch', 'worker_finalizer_missing'}:
                try:
                    adapter.cancel(envelope)
                except Exception:
                    pass
                return self._quarantine(job, exc.code)
            # Read failure is not terminal evidence. Keep authority until the
            # existing lease expires; the Worker independently stops on expiry.
            return self.store.get_job(job['tenant'], job['caller'], job['job_id'])

    def reconcile(self):
        """On control restart, reconcile the persisted attempt; never re-submit."""
        self.store.reconcile_expired()
        active = self.store.active(slot_id=self.slot_id)
        return None if active is None else self.poll(active)

    def tick(self):
        self.store.reconcile_expired()
        active = self.store.active(slot_id=self.slot_id)
        if active is not None:
            return self.poll(active)
        return self.dispatch_once()
