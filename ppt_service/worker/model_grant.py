"""Controller-owned, per-attempt model authority. Never deserialize from a worker.

A credential authorizes only this immutable identity and monotonic lease. Expiry
and revocation are sticky; renewal does not replenish the request budget. Socket
shutdown occurs outside the state lock, including sockets attached after revoke.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hmac
import math
import socket
import threading
import time
import uuid
from .prompts import PHASES


@dataclass(frozen=True)
class ModelIdentity:
    project_id: str
    job_id: str
    attempt: int
    token: int
    phase: str

    def __post_init__(self):
        for value in (self.project_id, self.job_id):
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError('invalid_model_identity')
        for value in (self.attempt, self.token):
            if type(value) is not int or not 1 <= value < 2**63:
                raise ValueError('invalid_model_identity')
        if type(self.phase) is not str or self.phase not in PHASES:
            raise ValueError('invalid_model_identity')


def _ttl(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 600:
        raise ValueError('invalid_model_lease')
    return float(value)


def _shutdown(resources):
    for resource in resources:
        try:resource.shutdown(socket.SHUT_RDWR)
        except Exception:pass
        try:resource.close()
        except Exception:pass


class ModelPermit:
    def __init__(self, grant, number):
        self._grant, self._number = grant, number

    def attach_socket(self, resource):
        return self._grant._attach(self._number, resource)

    def allowed(self):
        return self._grant._allowed(self._number)

    def release(self):
        self._grant._release(self._number)


class ModelGrant:
    def __init__(self, identity, client_key, *, ttl_seconds=90, max_requests=128,
                 max_concurrency=1, clock=time.monotonic):
        if type(identity) is not ModelIdentity:
            raise ValueError('invalid_model_identity')
        if (type(client_key) is not str or len(client_key) < 16 or len(client_key) > 256
                or any(ord(c) < 33 or ord(c) > 126 for c in client_key)):
            raise ValueError('invalid_model_credential')
        for value, ceiling in ((max_requests, 4096), (max_concurrency, 16)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError('invalid_model_budget')
        ttl_seconds = _ttl(ttl_seconds)
        self._identity = identity
        self._bearer = ('Bearer ' + client_key).encode('ascii')
        self._clock, self._deadline = clock, clock() + ttl_seconds
        self._limit, self._concurrency, self._used = max_requests, max_concurrency, 0
        self._lock = threading.Lock()
        self._permits = {}
        self._revoked = False
        self._stop = threading.Event()
        self._monitor = threading.Thread(target=self._watch, daemon=True, name='ppt-model-grant')
        self._monitor.start()

    @property
    def identity(self):
        return self._identity

    def authenticates(self, authorization):
        return type(authorization) is bytes and hmac.compare_digest(authorization, self._bearer)

    def _expire_locked(self):
        if not self._revoked and self._clock() >= self._deadline:
            return self._revoke_locked()
        return []

    def _revoke_locked(self):
        self._revoked = True
        self._stop.set()
        resources = [s for sockets in self._permits.values() for s in sockets.values()]
        self._permits.clear()
        return resources

    def _watch(self):
        while not self._stop.wait(0.02):
            with self._lock:resources = self._expire_locked()
            _shutdown(resources)

    def acquire(self, authorization):
        if not self.authenticates(authorization):return None
        with self._lock:
            resources = self._expire_locked()
            if (self._revoked or self._used >= self._limit
                    or len(self._permits) >= self._concurrency):
                permit = None
            else:
                self._used += 1
                self._permits[self._used] = {}
                permit = ModelPermit(self, self._used)
        _shutdown(resources)
        return permit

    def renew(self, identity, ttl_seconds):
        ttl_seconds = _ttl(ttl_seconds)
        if type(identity) is not ModelIdentity or identity != self.identity:return False
        with self._lock:
            resources = self._expire_locked()
            valid = not self._revoked
            if valid:self._deadline = self._clock() + ttl_seconds
        _shutdown(resources)
        return valid

    def revoke(self, identity):
        if type(identity) is not ModelIdentity or identity != self.identity:return False
        with self._lock:resources = self._revoke_locked()
        _shutdown(resources)
        return True

    def close(self):
        self.revoke(self.identity)
        if threading.current_thread() is not self._monitor:self._monitor.join(timeout=1)

    def _attach(self, number, resource):
        if not callable(getattr(resource, 'shutdown', None)) or not callable(getattr(resource, 'close', None)):
            raise ValueError('model_transport_socket_required')
        with self._lock:
            resources = self._expire_locked()
            valid = not self._revoked and number in self._permits
            if valid:self._permits[number][id(resource)] = resource
            else:resources.append(resource)
        _shutdown(resources)
        return valid

    def _allowed(self, number):
        with self._lock:
            resources = self._expire_locked()
            valid = not self._revoked and number in self._permits
        _shutdown(resources)
        return valid

    def _release(self, number):
        with self._lock:self._permits.pop(number, None)

    def snapshot(self):
        with self._lock:
            resources = self._expire_locked()
            result = dict(identity=asdict(self.identity), active=not self._revoked,
                remaining_seconds=max(0, self._deadline-self._clock()),
                requests_used=self._used, request_limit=self._limit, inflight=len(self._permits))
        _shutdown(resources)
        return result
