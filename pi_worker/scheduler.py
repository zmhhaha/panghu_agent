"""Single-instance, single-runner scheduler. Runtime owns process-tree termination."""
import json
import os
import shutil
import threading
import time
from pathlib import Path
from scientific_agent.pi_contracts import ContractError, canonical_json, parse_timestamp, utc_now
from .artifacts import validate_and_copy


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                self.handle.write(b'0')
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError('Another worker instance owns the queue')
        return self

    def release(self):
        if self.handle:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class Scheduler:
    def __init__(self, store, runtime, runtime_dir, validator=None, max_attempts=2, cancel_grace=12, retention_days=30):
        self.store, self.runtime = store, runtime
        self.root = Path(runtime_dir)
        self.validator = validator
        self.max_attempts, self.cancel_grace, self.retention_days = max_attempts, cancel_grace, retention_days
        self.stop_event = threading.Event()
        self.active_cancel = None
        self.thread = None
        self.degraded = False
        self._runner = None

    def start(self):
        with self.store._tx() as db:
            interrupted = bool(db.execute("SELECT 1 FROM tasks WHERE state IN ('running','validating','cancel_requested')").fetchone())
        if interrupted:
            recover = getattr(self.runtime,'recover',None)
            if recover is None or recover(self.store.execution_uids()) is not True:
                self.degraded = True
                return
        self.store.recover(self.max_attempts)
        self.thread = threading.Thread(target=self._loop, name='pi-scheduler',daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.active_cancel:
            self.active_cancel.set()
        if self.thread:
            self.thread.join(self.cancel_grace+2)
        if self.thread and self.thread.is_alive():
            self.degraded = True
        return not self.degraded

    def _loop(self):
        maintenance_at = 0
        while not self.stop_event.is_set() and not self.degraded:
            try:
                claim = self.store.claim()
                if claim:
                    self.run_claim(claim)
                else:
                    if time.monotonic() >= maintenance_at:
                        self.store.cleanup_expired(self.root,self.retention_days)
                        maintenance_at = time.monotonic()+60
                    self.stop_event.wait(.2)
            except Exception:
                # Storage uncertainty cannot authorize a second execution.
                self.degraded = True

    def run_claim(self, claim):
        job, snap, token = claim['job'],claim['snapshot'],claim['token']
        task_id, attempt = snap['id'],snap['attempt']
        task_root = self.root/'tasks'
        workspace = task_root/task_id/'attempts'/str(attempt)/'workspace'
        workspace.mkdir(parents=True, exist_ok=False)
        if os.name != 'nt':
            self.root.chmod(0o711)
            for parent in (task_root,task_root/task_id,task_root/task_id/'attempts',workspace.parent):
                parent.chmod(0o711)
            workspace.chmod(0o700)
            if os.geteuid() == 0:
                os.chown(workspace,job['_runtime_uid'],job['_runtime_uid'])
        cancel = threading.Event()
        self.active_cancel = cancel
        errors = []
        def event_sink(event):
            try:
                if not isinstance(event,dict) or len(canonical_json(event).encode('utf-8'))>8192:
                    return
                self.store.stage(task_id,attempt,token,event.get('stage'))
            except ContractError:
                cancel.set()
        def execute():
            try:
                target = getattr(self.runtime,'execute',self.runtime)
                target(dict(job,attempt=attempt),workspace,event_sink,cancel)
            except Exception as exc:
                errors.append(exc)
        runner = threading.Thread(target=execute,name='pi-attempt',daemon=True)
        self._runner = runner
        runner.start()
        renewed = time.monotonic()
        cancellation_started = None
        lease_lost = False
        while runner.is_alive():
            try:
                current = self.store.get(task_id)
                deadline = parse_timestamp(current['execution_deadline_at']) <= parse_timestamp(utc_now())
                if current['state']=='cancel_requested' or deadline or self.stop_event.is_set():
                    cancel.set()
                if time.monotonic()-renewed>=5:
                    self.store.renew(task_id,attempt,token)
                    self.store.expire_queued()
                    renewed = time.monotonic()
            except Exception:
                lease_lost = True
                cancel.set()
            if cancel.is_set():
                if cancellation_started is None:
                    cancellation_started = time.monotonic()
                elif time.monotonic()-cancellation_started > self.cancel_grace:
                    # Never fence/requeue while the old process might still run.
                    self.degraded = True
                    return
            runner.join(.05)
        self.active_cancel = None
        if lease_lost:
            self.degraded = True
            return
        try:
            if errors and getattr(errors[0],'code',None) in ('process_safety_unverified','process_termination_failed'):
                self.degraded = True
                return
            if cancel.is_set():
                self.store.finish_attempt(task_id,attempt,token,'failed','worker_interrupted',retry=self.stop_event.is_set(),max_attempts=self.max_attempts)
                return
            if errors:
                exc = errors[0]
                code = getattr(exc,'code','runtime_error')
                if code in ('process_safety_unverified','process_termination_failed'):
                    self.degraded = True
                    return
                self.store.finish_attempt(task_id,attempt,token,'failed',code,retry=not isinstance(exc,ContractError) and getattr(exc,'retryable',True),max_attempts=self.max_attempts)
                return
            self.store.stage(task_id,attempt,token,'validate')
            # Validation can take time: maintain lease and propagate cancellation
            # until a final transactional publish decides which transition wins.
            validation_stop = threading.Event()
            def heartbeat():
                while not validation_stop.wait(5):
                    try:
                        self.store.renew(task_id,attempt,token)
                        self.store.expire_queued()
                    except Exception:
                        return
            heartbeat_thread = threading.Thread(target=heartbeat,daemon=True)
            heartbeat_thread.start()
            try:
                result,path = validate_and_copy(workspace,self.root/'results',job,attempt,self.validator)
                self.store.publish(task_id,attempt,token,result,path,self.retention_days)
            finally:
                validation_stop.set()
                heartbeat_thread.join(1)
        except ContractError as exc:
            if exc.code != 'stale_attempt':
                self.store.finish_attempt(task_id,attempt,token,'failed',exc.code)
        except Exception:
            self.store.finish_attempt(task_id,attempt,token,'failed','validation_failed')
        finally:
            self._runner = None
            # A stopped revision needs no second on-disk evidence copy after
            # controller packaging. Do not clean anything while process safety
            # is uncertain: fencing and orphan recovery retain priority.
            if job.get('task_kind') == 'revision' and not self.degraded:
                try:
                    self._discard_revision_workspace(workspace)
                except (OSError, ContractError):
                    self.degraded = True

    @staticmethod
    def _discard_revision_workspace(workspace):
        workspace = Path(workspace)
        if workspace.is_symlink():
            raise ContractError('invalid_artifact','Refusing symlink revision workspace cleanup',422)
        def remove_readonly(function, filename, exc):
            target = Path(filename)
            if target.is_symlink():
                target.unlink()
            else:
                target.chmod(0o700)
                function(filename)
        if workspace.exists():
            shutil.rmtree(workspace,onexc=remove_readonly)
