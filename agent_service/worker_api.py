"""One-attempt Worker capsule API; host code supplies isolation and native driver.

No default engine, executable, network, model credential or stop controller is
constructed here. The API never accepts these settings from a task. A real host
must assemble and verify those dependencies before its capsule can be ready.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field

from .contracts import (ExecutionEnvelope, InlineFile, ProfilePolicy, ServiceError, TERMINAL_STATES,
                        WireModel, WorkerResult, canonical_bytes, sign_receipt)
from .engines import EngineRequest, NativeOutcome


class LeaseRequest(WireModel):
    fence: int = Field(ge=1)
    lease_expires_at: float


class CapsuleLock:
    """Lifetime OS lock; unresolved execution cannot be reclaimed by another app."""
    def __init__(self, path):
        self.path, self.stream = path, None

    def acquire(self):
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        descriptor = os.open(self.path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor)
            raise RuntimeError('Unsafe capsule lock')
        self.stream = os.fdopen(descriptor, 'r+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.stream.close()
            self.stream = None
            raise RuntimeError('Worker capsule already owned') from None

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class WorkerHost:
    def __init__(self, policy: ProfilePolicy, driver, control_dir: Path, workspace_root: Path,
                 receipt_key: bytes, *, clock=time.time, output_name='output.json'):
        from .contracts import safe_name
        self.policy, self.driver = policy, driver
        self.control_dir, self.workspace_root = Path(os.path.abspath(control_dir)), Path(os.path.abspath(workspace_root))
        if (self.control_dir == self.workspace_root or self.control_dir in self.workspace_root.parents
                or self.workspace_root in self.control_dir.parents or len(receipt_key) < 32):
            raise ValueError('Worker control and domain storage must be separate')
        self.receipt_key, self.clock, self.output_name = receipt_key, clock, safe_name(output_name)
        self._guard, self._cancel = threading.RLock(), threading.Event()
        self._thread = None
        self._lock = None
        self._state = None
        self._storage_failed = False
        self._cancel_cause = None

    @staticmethod
    def _checked_directory(path):
        for part in (*reversed(path.parents), path):
            if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
                raise RuntimeError('Worker storage contains a link')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir():
            raise RuntimeError('Worker storage is not a directory')

    def _check_control_file(self, path):
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, 'st_file_attributes', 0) & 0x400
                    or os.name != 'nt' and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
                raise RuntimeError('Unsafe Worker control file')

    def initialize(self):
        self._checked_directory(self.control_dir)
        self._checked_directory(self.workspace_root)
        info = self.control_dir.stat()
        if os.name != 'nt' and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise RuntimeError('Worker control directory must be private and host-owned')
        for name in ('capsule.lock', 'execution.sqlite', 'execution.sqlite-journal', 'execution.sqlite-wal', 'execution.sqlite-shm'):
            self._check_control_file(self.control_dir / name)
        self._lock = CapsuleLock(self.control_dir / 'capsule.lock')
        self._lock.acquire()
        try:
            with closing(sqlite3.connect(self.control_dir / 'execution.sqlite')) as con:
                with con:
                    con.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
                    row = con.execute('SELECT data FROM state WHERE id=1').fetchone()
            (self.control_dir / 'execution.sqlite').chmod(0o600)
            if row is not None:
                self._state = json.loads(row[0])
                if self._state['state'] not in TERMINAL_STATES:
                    changed = deepcopy(self._state)
                    changed['state'] = 'quarantined'
                    changed['result'] = WorkerResult(state='quarantined', error_code='worker_restart_unresolved').model_dump()
                    self._commit(changed)
        except BaseException:
            self._lock.close()
            raise

    def _write(self, state):
        # This DB is host-owned, never mounted into the untrusted Agent runtime.
        with closing(sqlite3.connect(self.control_dir / 'execution.sqlite')) as con:
            with con:
                con.execute('INSERT INTO state(id,data) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                            (canonical_bytes(state).decode('utf-8'),))

    def _commit(self, state):
        try:
            self._write(state)
        except Exception:
            self._storage_failed = True
            self._cancel_cause = 'worker_storage_failed'
            self._cancel.set()
            raise ServiceError('worker_storage_failed', 503) from None
        self._state = state

    def _save(self):
        # Test/diagnostic seeding only; live mutations use commit-before-expose.
        self._commit(deepcopy(self._state))

    def capabilities(self):
        with self._guard:
            try:
                value = self.driver.ready
                native_ready = value() is True if callable(value) else value is True
            except Exception:
                native_ready = False
            return dict(api_version='1', **self.policy.pins(), ready=self.policy.enabled and native_ready and self._state is None and not self._storage_failed,
                        supports_inputs=False, native_resume=False, publication_authorized=False)

    def _matching(self, attempt_id):
        if self._state is None or self._state['envelope']['attempt_id'] != attempt_id:
            raise ServiceError('execution_not_found', 404)

    def status(self, attempt_id):
        with self._guard:
            self._matching(attempt_id)
            return dict(attempt_id=attempt_id, state='quarantined' if self._storage_failed else self._state['state'],
                        request_sha256=self._state['request_sha256'])

    def start(self, envelope):
        with self._guard:
            if self._state is not None:
                if self._state['request_sha256'] != envelope.request_sha256:
                    raise ServiceError('execution_conflict', 409)
                return self.status(envelope.attempt_id)
            if not self.capabilities()['ready']:
                raise ServiceError('worker_not_ready', 503)
            if (any(getattr(envelope, k) != v for k, v in self.policy.pins().items())
                    or self.driver.engine != envelope.engine):
                raise ServiceError('execution_pins_mismatch', 422)
            if envelope.deadline_at <= self.clock() or envelope.lease_expires_at <= self.clock():
                raise ServiceError('execution_expired', 409)
            if envelope.lease_expires_at > envelope.deadline_at + 45 or envelope.lease_expires_at > self.clock() + 300:
                raise ServiceError('invalid_lease', 422)
            self.policy.task_validator(envelope.task)
            # The first host provides prompt-only tasks. Domain mount plans must
            # be implemented and verified before a profile advertises file input.
            if envelope.inputs:
                raise ServiceError('input_staging_not_ready', 422)
            changed = dict(envelope=envelope.model_dump(), request_sha256=envelope.request_sha256,
                               state='running', result=None, events=[], lease_expires_at=envelope.lease_expires_at)
            self._commit(changed)  # Durable identity exists before any native API.
            self._thread = threading.Thread(target=self._execute, args=(envelope,), daemon=True,
                                            name='agent-capsule-' + envelope.attempt_id)
            self._thread.start()
            return self.status(envelope.attempt_id)

    def renew(self, attempt_id, lease):
        with self._guard:
            self._matching(attempt_id)
            e = self._state['envelope']
            if (lease.fence != e['fence'] or self._state['state'] in TERMINAL_STATES
                    or self.clock() >= self._state['lease_expires_at'] or self._cancel.is_set()
                    or not math.isfinite(lease.lease_expires_at)
                    or not self._state['lease_expires_at'] <= lease.lease_expires_at <= min(e['deadline_at'] + 45, self.clock() + 300)):
                raise ServiceError('worker_lease_rejected', 409)
            changed = deepcopy(self._state)
            changed['lease_expires_at'] = lease.lease_expires_at
            self._commit(changed)
            return self.status(attempt_id)

    def cancel(self, attempt_id):
        with self._guard:
            self._matching(attempt_id)
            if self._state['state'] not in TERMINAL_STATES:
                self._cancel_cause = self._cancel_cause or 'user_cancel'
                self._cancel.set()
                changed = deepcopy(self._state)
                changed['state'] = 'cancelling'
                self._commit(changed)
            return self.status(attempt_id)

    def result(self, attempt_id):
        with self._guard:
            self._matching(attempt_id)
            if self._storage_failed:
                return WorkerResult(state='quarantined', error_code='worker_storage_failed').model_dump()
            if self._state['result'] is None:
                raise ServiceError('result_not_ready', 409)
            return self._state['result']

    def events(self, attempt_id, after):
        with self._guard:
            self._matching(attempt_id)
            items = self._state['events'][after:after + 100]
            return {'items': items, 'next_cursor': items[-1]['seq'] if items else after}

    def _emit(self, event):
        # Native tool data/prose is never forwarded to the business event stream.
        kind = event.get('type') if isinstance(event, dict) else None
        if kind not in {'pi_event', 'runtime_finished', 'engine_started', 'engine_progress'}:
            return
        with self._guard:
            changed = deepcopy(self._state)
            events = changed['events']
            if len(events) < 500:
                events.append({'seq': len(events) + 1, 'type': 'engine.progress', 'payload': {'event': kind}})
                self._commit(changed)

    def _execute(self, envelope):
        finished = threading.Event()
        native = None
        workspace = self.workspace_root / envelope.attempt_id
        def watch():
            while not finished.wait(0.05):
                with self._guard:
                    if self.clock() >= min(envelope.deadline_at, self._state['lease_expires_at']):
                        if not self._cancel.is_set():
                            self._cancel_cause = 'timed_out' if self.clock() >= envelope.deadline_at else 'worker_lease_expired'
                        self._cancel.set()
        watchdog = threading.Thread(target=watch, daemon=True)
        watchdog.start()
        try:
            request = EngineRequest(envelope.attempt_id, envelope.profile, envelope.task,
                max(1, min(self.policy.max_timeout_seconds, math.ceil(envelope.deadline_at - self.clock()))),
                datetime.fromtimestamp(envelope.deadline_at, timezone.utc))
            native = self.driver.execute(request, workspace, self._emit, self._cancel)
            if type(native) is not NativeOutcome or native.stopped is not True or not native.resource_ids:
                raise ServiceError('writers_not_stopped', 503)
            state = native.status if native.status in TERMINAL_STATES else 'failed'
            if self._cancel.is_set():
                state = ('timed_out' if self.clock() >= envelope.deadline_at else
                         'cancelled' if self._cancel_cause == 'user_cancel' else 'failed')
            artifacts = []
            if state in {'completed', 'needs_input'}:
                if native.output is not None:
                    value = ({'schema_version': '1', 'text': native.output}
                             if type(native.output) is str else native.output)
                    self._validate_text_output(value)
                    artifacts.append(InlineFile.from_bytes('response.json', canonical_bytes(value)))
                elif state == 'completed':
                    from .artifacts import read_output
                    raw = read_output(workspace, self.output_name)
                    def pairs(items):
                        result = {}
                        for key, value in items:
                            if key in result:
                                raise ValueError('Duplicate output key')
                            result[key] = value
                        return result
                    value = json.loads(raw, object_pairs_hook=pairs)
                    self._validate_text_output(value)
                    artifacts.append(InlineFile.from_bytes(self.output_name, raw))
            code = self._cancel_cause if state == 'failed' and self._cancel_cause else native.error_code
            result = WorkerResult(state=state, error_code=code, usage=native.usage, artifacts=artifacts)
            receipt = sign_receipt(envelope, result, self.receipt_key, native.resource_ids, self.clock())
            result = result.model_copy(update={'stop_receipt': receipt})
        except Exception:
            # Output failure after proven stop is a failed handoff, not an
            # unresolved writer. It must not permanently consume the queue slot.
            if type(native) is NativeOutcome and native.stopped is True and native.resource_ids:
                try:
                    result = WorkerResult(state='failed', error_code='worker_output_invalid')
                    result = result.model_copy(update={'stop_receipt': sign_receipt(envelope, result,
                        self.receipt_key, native.resource_ids, self.clock())})
                except Exception:
                    result = WorkerResult(state='quarantined', error_code='worker_execution_unresolved')
            else:
                result = WorkerResult(state='quarantined', error_code='worker_execution_unresolved')
        finally:
            finished.set()
            watchdog.join(timeout=1)
        with self._guard:
            # Recheck cancel under the same lock as publishing the final result.
            if self._cancel.is_set() and result.state in {'completed', 'needs_input'}:
                state = ('timed_out' if self.clock() >= envelope.deadline_at else
                         'cancelled' if self._cancel_cause == 'user_cancel' else 'failed')
                result = WorkerResult(state=state, error_code=self._cancel_cause, usage=result.usage)
                result = result.model_copy(update={'stop_receipt': sign_receipt(envelope, result,
                    self.receipt_key, native.resource_ids, self.clock())})
            changed = deepcopy(self._state)
            changed['state'], changed['result'] = result.state, result.model_dump()
            try:
                self._commit(changed)
            except ServiceError:
                pass  # storage failure remains fenced and never exposes success

    @staticmethod
    def _validate_text_output(value):
        if (type(value) is not dict or set(value) != {'schema_version', 'text'}
                or value['schema_version'] != '1' or type(value['text']) is not str
                or len(canonical_bytes(value)) > 1024 * 1024):
            raise ServiceError('worker_output_invalid', 422)

    def close(self):
        self._cancel_cause = self._cancel_cause or 'worker_host_shutdown'
        self._cancel.set()
        if self._thread:
            self._thread.join(timeout=5)
        # Keep ownership for this process if native execution cannot be stopped.
        if self._lock and (self._thread is None or not self._thread.is_alive()):
            self._lock.close()


def create_worker_app(host: WorkerHost, token: str):
    if len(token.encode()) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('Dedicated worker token required')

    @asynccontextmanager
    async def lifespan(app):
        host.initialize()
        try:
            yield
        finally:
            host.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def auth(request, call_next):
        if not hmac.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer ' + token).encode()):
            return JSONResponse({'detail': {'code': 'authentication_required'}}, status_code=401)
        # Bound allocation even for a malicious authenticated control client.
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 12 * 1024 * 1024:
                return JSONResponse({'detail': {'code': 'request_too_large'}}, status_code=413)
            chunks.append(chunk)
        request._body = b''.join(chunks)
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def error(request, exc):
        return JSONResponse({'detail': {'code': exc.code}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return JSONResponse({'detail': {'code': 'invalid_request'}}, status_code=422)

    @app.get('/v1/capabilities')
    def caps():
        return host.capabilities()

    @app.put('/v1/executions/{attempt_id}', status_code=202)
    def start(attempt_id: str, envelope: ExecutionEnvelope):
        if attempt_id != envelope.attempt_id:
            raise ServiceError('execution_identity_mismatch', 422)
        return host.start(envelope)

    @app.get('/v1/executions/{attempt_id}')
    def status(attempt_id: str):
        return host.status(attempt_id)

    @app.post('/v1/executions/{attempt_id}/lease')
    def lease(attempt_id: str, body: LeaseRequest):
        return host.renew(attempt_id, body)

    @app.post('/v1/executions/{attempt_id}/cancel')
    def cancel(attempt_id: str):
        return host.cancel(attempt_id)

    @app.get('/v1/executions/{attempt_id}/result')
    def result(attempt_id: str):
        return host.result(attempt_id)

    @app.get('/v1/executions/{attempt_id}/events')
    def events(attempt_id: str, after: int = 0):
        if after < 0:
            raise ServiceError('invalid_cursor', 422)
        return host.events(attempt_id, after)

    return app
