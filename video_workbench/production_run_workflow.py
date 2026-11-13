"""Reconcile admitted draft runs even when new-run admission is disabled."""
import logging
import threading

from agent_service.contracts import ServiceError


LOG = logging.getLogger(__name__)


class ProductionRunWorkflow:
    def __init__(self, store, *, enabled=False):
        if type(enabled) is not bool:
            raise ValueError('An explicit boolean production run gate is required')
        self.store, self.enabled = store, enabled
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.thread = None

    def require_enabled(self):
        if not self.enabled:
            raise ServiceError('production_runs_disabled', 503)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='video-production-runs', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=12)

    def _run(self):
        while not self.stopping.is_set():
            self.tick()
            self.stopping.wait(1)

    def tick(self):
        if self.stopping.is_set() or not self.lock.acquire(blocking=False):
            return
        try:
            self.store.tick()
        except Exception:
            LOG.error('Production run reconciliation interrupted; durable state retained')
        finally:
            self.lock.release()
