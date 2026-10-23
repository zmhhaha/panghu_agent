"""Fixture-only queue driver. Never accepts plans or publishes artifacts."""
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from .execution import Attempt, ControlError, RuntimeConfig, StopProof
from .pi_pipeline import run_offline_attempt
from .pi_queue import public_error
from .stop_gate import stop_uids


class PiDispatchError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ClaimedExecution:
    attempt: Attempt
    checkpoint_manifest: dict | None


def _require_execution_host():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise PiDispatchError('dispatch_requires_linux_root')


class PiDispatch:
    """Single-controller fixture driver; real engine/container admission stays closed.

    Fixtures run trusted commands in disposable test containers, not user jobs.
    The actual container supervisor and model proxy are not connected here.
    """
    def __init__(self, queue, config, allocator, runtime_settings, stager, scratch_root, *, parsed_for=None, sandbox=None, build=None):
        self.queue, self.config, self.allocator = queue, config, allocator
        self.runtime_settings, self.stager = runtime_settings, stager
        self.scratch_root, self.parsed_for = Path(scratch_root), parsed_for
        self.sandbox = sandbox
        self.build = build or queue.build

    def run_next(self):
        _require_execution_host()
        if type(self.config) is not RuntimeConfig:
            raise PiDispatchError('invalid_dispatch_config')
        if getattr(self.runtime_settings, 'fixture_mode', None) is not True:
            raise PiDispatchError('production_admission_not_configured')
        if (self.parsed_for is None) == (self.sandbox is None):
            raise PiDispatchError('invalid_parser_configuration')
        job = self.queue.claim_next()
        if job is None:
            return None
        try:
            claimed = self.build(job)
            if type(claimed) is not ClaimedExecution or type(claimed.attempt) is not Attempt:
                raise ControlError('invalid_attempt_request')
            a, manifest = claimed.attempt, claimed.checkpoint_manifest
            self.allocator.verify(project_id=a.project_id, job_id=a.job_id,
                attempt=a.attempt, token=a.token, role='pi')
        except Exception as exc:
            self.queue.abandon_claim(job, public_error(getattr(exc, 'code', None)))
            raise PiDispatchError('prelaunch_refused') from None
        self.queue.attach_execution(a)
        stop_heartbeat = threading.Event()
        lease_errors = []

        def maintain_lease():
            while not stop_heartbeat.wait(10):
                try:
                    if self.queue.heartbeat(a) == 'cancelling':
                        lease_errors.append('execution_revoked')
                        return
                except Exception:
                    lease_errors.append('stale_execution')
                    return
        heartbeat = threading.Thread(target=maintain_lease, daemon=True)
        heartbeat.start()
        try:
            parser_args = {'parsed': self.parsed_for(a)} if self.parsed_for is not None else {'sandbox': self.sandbox}
            outcome = run_offline_attempt(self.config, self.stager, a, self.runtime_settings,
                self.scratch_root / f'{a.job_id}-{a.attempt}-{a.token}', allocator=self.allocator,
                checkpoint_manifest=manifest, timeout=600, **parser_args)
            if lease_errors:
                raise ControlError(lease_errors[0])
            proof = outcome.proof
            if type(proof) is not StopProof or proof.uid != a.uid or proof.all_writers_stopped is not True:
                raise ControlError('writers_not_stopped')
        except BaseException as exc:
            self._abandon(a, public_error(getattr(exc, 'code', None)))
            raise
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)
        status = self.queue.prove_stopped(a, True)
        return None if status == 'cancelled' else outcome

    def _abandon(self, a, code):
        try:
            uids = self.allocator.writers(project_id=a.project_id, job_id=a.job_id,
                attempt=a.attempt, token=a.token)
            if not uids:
                raise ControlError('missing_writer_identity')
            stop_uids(uids)
        except Exception:
            raise PiDispatchError('writers_not_stopped') from None
        if self.queue.prove_stopped(a, True) != 'cancelled':
            self.queue.finish_failed(a, public_error(code))
