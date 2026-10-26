"""Trusted root sidecar; host admission and network confinement precede this entry."""
from __future__ import annotations
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
from ..control.pi_model import BUDGETS
from .container_lease import ContainerLaunch, ContainerLease, read_private, require
from .model_grant import ModelGrant
from .model_proxy import ModelProxyServer, ProxyConfig
from .proxy_usage import UsageLedger

LAUNCH = Path('/work/task/domain-launch.json')
AUTHORITY = Path('/work/audit/container-authority/control.json')
AUDIT = Path('/audit')


def _private_directory(path):
    require(sys.platform == 'linux' and os.geteuid() == 0)
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        require(stat.S_ISDIR(info.st_mode) and not parent.is_symlink()
                and info.st_uid == 0 and (not info.st_mode & 0o022 or sticky))
    require(stat.S_IMODE(path.lstat().st_mode) == 0o700)


def run_proxy(config, launch, *, server_factory=ModelProxyServer, stop_event=None,
              audit_path=AUDIT, lease_factory=ContainerLease):
    """Trusted test hooks only; CLI paths, address and factories are fixed."""
    require(type(config) is ProxyConfig and type(launch) is ContainerLaunch)
    # Recheck even an explicitly constructed dataclass against the wire contract.
    ContainerLaunch.parse(dict(schema=1, **asdict(launch)))
    _private_directory(audit_path)
    path = Path(audit_path) / 'usage.jsonl'
    if path.exists() or path.is_symlink():
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_uid == 0
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
    interrupted = stop_event if stop_event is not None else threading.Event()
    lease = grant = server = thread = ledger = None
    failed = threading.Event()
    code, closed = 'stopped', True
    try:
        lease = lease_factory(launch, AUTHORITY)
        authority = lease.poll()
        grant = ModelGrant(launch.identity, config.client_key,
            ttl_seconds=authority['deadline_monotonic'] - time.monotonic(),
            max_requests=BUDGETS[launch.identity.phase], max_concurrency=1)
        lease.bind(grant, interrupted)
        require(not interrupted.is_set() and grant.snapshot()['active'])
        ledger = UsageLedger(str(path), job_id=launch.identity.job_id,
                            attempt=launch.identity.attempt, phase=launch.identity.phase)
        server = server_factory(('0.0.0.0', 8080), config,
                               usage_ledger=ledger, model_grant=grant)
        def serve():
            try:
                server.serve_forever(poll_interval=0.02)
            except Exception:
                failed.set()
            finally:
                interrupted.set()
        thread = threading.Thread(target=serve, name='ppt-proxy-entry', daemon=True)
        thread.start()
        while not interrupted.wait(0.02):
            if not grant.snapshot()['active']:
                interrupted.set()
        if failed.is_set() or not ledger.healthy:
            code = 'proxy_failed'
    except Exception:
        code = 'setup_invalid'
    finally:
        # Signal handlers only set the event. All revocation/IO occurs here.
        for cleanup in (lease.close if lease else None,
                        grant.close if grant else None,
                        server.shutdown if server and thread and thread.is_alive() else None,
                        server.server_close if server else None,
                        ledger.shutdown if ledger else None):
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    closed, code = False, 'cleanup_failed'
        if thread is not None and thread.ident is not None:
            thread.join(timeout=2)
            if thread.is_alive():
                closed, code = False, 'cleanup_failed'
    return dict(identity=asdict(launch.identity), code=code, closed=closed)


def main(argv=None):
    status = dict(identity=None, code='setup_invalid', closed=True)
    interrupted = threading.Event()
    previous = {}
    try:
        require(not (sys.argv[1:] if argv is None else argv))
        _private_directory(AUDIT)
        _private_directory(AUTHORITY.parent)
        launch = ContainerLaunch.parse(read_private(LAUNCH, launch=True))
        status['identity'] = asdict(launch.identity)
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: interrupted.set())
        status = run_proxy(ProxyConfig.from_env(), launch, stop_event=interrupted)
    except Exception:
        # Never format exceptions: configuration and transport errors may contain keys.
        pass
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(json.dumps(status, separators=(',', ':')), flush=True)
    return 0 if status['closed'] and status['code'] == 'stopped' else 2


if __name__ == '__main__':
    raise SystemExit(main())
