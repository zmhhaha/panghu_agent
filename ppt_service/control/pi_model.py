"""Controller lifecycle for one task-bound model proxy. No public control route.

The real-provider constructor requires the existing paid/network admission gate.
The explicit offline constructor redirects only through a trusted test connector
and uses a fixed synthetic provider credential. Neither enables a PPT profile.
"""
from __future__ import annotations
from dataclasses import asdict, replace
import hashlib
import json
import math
import time
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
from .execution import Attempt, ControlError, Layout, RuntimeConfig
from ..worker.model_grant import ModelGrant, ModelIdentity
from ..worker.model_proxy import ModelProxyServer, ProxyConfig
from ..worker.proxy_usage import UsageLedger

BUDGETS = {'plan_initial':8, 'plan_options':12, 'prepare':24, 'generate':128, 'revise':96}


def identity(a):
    if type(a) is not Attempt:raise ControlError('invalid_attempt_request')
    return ModelIdentity(a.project_id, a.job_id, a.attempt, a.token, a.phase)


def _private_parent(path):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ControlError('model_authority_requires_linux_root')
    if not path.is_absolute() or '..' in path.parts:raise ControlError('unsafe_model_authority')
    for part in (*reversed(path.parents),path):
        info = part.lstat()
        # A root-owned sticky system tmp directory cannot be replaced by users.
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or part.is_symlink() or info.st_uid != 0
                or (info.st_mode & 0o022 and not sticky)):
            raise ControlError('unsafe_model_authority')


class TaskModelProxy:
    def __init__(self, config, attempt, layout, upstream_key):
        if type(config) is not RuntimeConfig:raise ControlError('invalid_attempt_request')
        config.require_paid_admission()
        self._initialize(config, attempt, layout, upstream_key, None)

    def _initialize(self, config, attempt, layout, upstream_key, connector):
        if type(config) is not RuntimeConfig or type(layout) is not Layout:
            raise ControlError('invalid_attempt_request')
        self.identity = identity(attempt)
        self._lease_seconds = config.lease_seconds
        expected = Path(config.data_root)/'projects'/attempt.project_id/'attempts'/attempt.job_id/f'{attempt.attempt}-{attempt.token}'
        if Path(layout.root) != expected:raise ControlError('model_layout_mismatch')
        _private_parent(Path(layout.audit))
        if Path(layout.audit) != Path(layout.root)/'audit':
            raise ControlError('unsafe_model_authority')
        self._private = Path(layout.audit)/'model-authority'
        # An existing receipt requires reconciliation, never silently reissue a
        # credential or replenish the request budget for the same attempt.
        self._private.mkdir(mode=0o700)
        self._receipt = self._private/'authority.json'
        self._key = 'ppt-task-' + secrets.token_urlsafe(32)
        self._grant = ModelGrant(self.identity,self._key,ttl_seconds=config.lease_seconds,
            max_requests=BUDGETS[attempt.phase],max_concurrency=1)
        self._server = None
        self._thread = None
        self._closed = False
        self._lifecycle = threading.Lock()
        try:
            self._record('created')
            proxy_config = ProxyConfig(self._key,upstream_key)
            ledger = UsageLedger(str(self._private/'usage.jsonl'),job_id=attempt.job_id,
                attempt=attempt.attempt,phase=attempt.phase)
            self._server = ModelProxyServer(('127.0.0.1',0),proxy_config,
                connector=connector,usage_ledger=ledger,model_grant=self._grant)
            self._thread = threading.Thread(target=self._server.serve_forever,
                kwargs={'poll_interval':0.02},daemon=True,name='ppt-task-model-proxy')
            self._thread.start()
            self._record('active')
        except BaseException:
            self.close()
            raise

    @classmethod
    def for_offline_test(cls,config,attempt,layout,connector):
        if not callable(connector) or not connector:raise ControlError('offline_connector_required')
        instance = cls.__new__(cls)
        instance._initialize(config,attempt,layout,'fake-provider-protocol-key',connector)
        return instance

    def _record(self,state):
        body = json.dumps(dict(identity=asdict(self.identity),state=state,
            credential_sha256=hashlib.sha256(self._key.encode()).hexdigest(),
            grant=self._grant.snapshot()),sort_keys=True).encode()
        temp = self._private/('receipt-'+secrets.token_hex(12))
        fd = os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(body);out.flush();os.fsync(out.fileno())
        os.replace(temp,self._receipt)
        directory = os.open(self._private,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)

    def runtime_settings(self,base):
        """Only the proxy credential enters child env; preserve readiness evidence."""
        from pi_worker.runtime import RuntimeSettings
        if type(base) is not RuntimeSettings or self._closed or not self._grant.snapshot()['active']:
            raise ControlError('invalid_model_runtime_settings')
        return replace(base,provider='ppt-task-proxy',model=self._server.config.model,
            model_base_url=f'http://127.0.0.1:{self._server.server_port}/v1',
            model_api='openai-responses',model_input=('text','image'),model_compat={},
            model_env={'SCIENTIFIC_MODEL_API_KEY':self._key})

    def heartbeat_and_renew(self,queue,attempt):
        if identity(attempt) != self.identity:raise ControlError('model_identity_mismatch')
        with self._lifecycle:
            if self._closed:return False
            try:
                # RepositoryQueue.heartbeat returns only after its fenced DB
                # transaction commits. Failure/rollback must never extend a grant.
                sql_ttl = queue.lease_seconds
                if type(sql_ttl) not in (int,float) or not math.isfinite(sql_ttl) or not 0 < sql_ttl <= 600:
                    raise ControlError('invalid_model_lease')
                if not self._grant.snapshot()['active']:
                    self._record('revoked')
                    return False
                deadline = time.monotonic() + min(sql_ttl,self._lease_seconds)
                status = queue.heartbeat(attempt)
                remaining = deadline - time.monotonic()
                renewed = status == 'running' and remaining > 0 and self._grant.renew(self.identity,remaining)
                if not renewed:
                    self._grant.revoke(self.identity)
                    self._record('revoked')
                else:self._record('renewed')
                return renewed
            except BaseException:
                self._grant.revoke(self.identity)
                self._record('revoked')
                raise

    def snapshot(self):
        return self._grant.snapshot()

    def close(self):
        with self._lifecycle:
            if self._closed:return
            self._closed = True
            self._grant.close()
            try:self._record('closed')
            finally:
                if self._server is not None:
                    if self._thread is not None and self._thread.is_alive():self._server.shutdown()
                    self._server.server_close()
                if self._thread is not None and self._thread.ident is not None:
                    self._thread.join(timeout=2)
                    if self._thread.is_alive():raise ControlError('model_proxy_not_stopped')

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
