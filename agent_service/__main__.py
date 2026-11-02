"""Explicit CLI entry points; the API process never owns an Agent runtime."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
import signal
import sys
import threading

from .config import load_configuration
from .contracts import ServiceError


class _FixedErrorLog(logging.Filter):
    def filter(self, record):
        # ASGI/driver failures can carry SQL parameters, URLs and task content.
        # The process reports one fixed diagnostic category instead of those.
        record.msg, record.args = 'agent_service_runtime_error', ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def _logging_configuration():
    return {'version': 1, 'disable_existing_loggers': False,
            'filters': {'fixed_error': {'()': _FixedErrorLog}},
            'handlers': {'safe_stderr': {'class': 'logging.StreamHandler',
                         'stream': 'ext://sys.stderr', 'filters': ['fixed_error']}},
            'loggers': {'uvicorn': {'handlers': ['safe_stderr'], 'level': 'ERROR', 'propagate': False},
                        'uvicorn.error': {'handlers': ['safe_stderr'], 'level': 'ERROR', 'propagate': False},
                        'uvicorn.access': {'handlers': [], 'level': 'CRITICAL', 'propagate': False}}}


def _output(value, *, error=False):
    print(json.dumps(value, separators=(',', ':'), sort_keys=True), file=sys.stderr if error else sys.stdout)


def _dispatch(config, store, adapters, once):
    from .dispatcher import Dispatcher
    dispatchers = [Dispatcher(store, adapters, lease_seconds=config.lease_seconds,
                              slot_id=slot_id)
                   for slot_id in range(1, config.execution_slots + 1)]
    stopping = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stopping.set())
    try:
        def run(dispatcher):
            try:
                while not stopping.is_set():
                    result = dispatcher.tick()
                    if once:
                        return result
                    stopping.wait(config.poll_seconds)
            except BaseException:
                # Unexpected control failures restart the supervised process.
                # Normal Worker transport failures are handled within poll().
                stopping.set()
                raise

        if len(dispatchers) == 1:
            results = [run(dispatchers[0])]
        else:
            # A stalled HTTP request in one slot must not delay another slot's
            # launch, lease renewal, cancellation, or stop recovery.
            with ThreadPoolExecutor(max_workers=len(dispatchers),
                                    thread_name_prefix='agent-dispatch') as executor:
                futures = [executor.submit(run, dispatcher) for dispatcher in dispatchers]
                results = [future.result() for future in futures]
        if once:
            states = [result['state'] if result else 'idle' for result in results]
            value = {'status': 'dispatch_tick_complete', 'state': states[0]}
            if len(states) > 1:
                value = {'status': 'dispatch_tick_complete', 'states': states}
            _output(value)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Internal engine-neutral Agent execution service')
    parser.add_argument('--config', required=True, help='Trusted public routing JSON file')
    parser.add_argument('--secrets', required=True, help='Private database/tokens/receipt-keys JSON file')
    parser.add_argument('--allow-sqlite', action='store_true', help='Explicit local development only')
    parser.add_argument('--once', action='store_true', help='Run one dispatch tick (dispatch command only)')
    parser.add_argument('command', choices=('check', 'init-db', 'api', 'dispatch', 'model-proxy'))
    args = parser.parse_args(argv)
    if args.once and args.command != 'dispatch':
        parser.error('--once is only valid with dispatch')
    store, adapters = None, {}
    try:
        config = load_configuration(args.config, args.secrets, allow_sqlite=args.allow_sqlite)
        if args.command == 'check':
            _output(config.summary())
            return 0
        store = config.build_store()
        if args.command == 'init-db':
            from . import model_proxy  # Register durable model admission table.
            store.initialize()
            _output({'status': 'database_initialized'})
            return 0
        if args.command == 'model-proxy':
            if config.model_proxy_settings is None:
                raise ServiceError('model_proxy_configuration_missing', 503)
            import uvicorn
            from .model_proxy import ModelProxy, create_model_proxy_app
            app = create_model_proxy_app(ModelProxy(store, config.model_proxy_settings))
            uvicorn.run(app, host='0.0.0.0', port=8080, access_log=False,
                        proxy_headers=False, server_header=False, log_config=_logging_configuration())
            return 0
        adapters = config.build_adapters(store)
        if args.command == 'api':
            import uvicorn
            from .api import create_app
            app = create_app(config.api_settings, store, config.registry, adapters)
            uvicorn.run(app, host=config.api_host, port=config.api_port,
                        access_log=False, proxy_headers=False, server_header=False,
                        log_config=_logging_configuration())
        else:
            _dispatch(config, store, adapters, args.once)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, ServiceError) else 'runtime_unavailable'
        if not isinstance(code, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code):
            code = 'runtime_unavailable'
        _output({'status': 'error', 'code': code}, error=True)
        return 1
    finally:
        for adapter in adapters.values():
            try:
                adapter.close()
            except Exception:
                pass
        if store is not None:
            try:
                store.engine.dispose()
            except Exception:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
