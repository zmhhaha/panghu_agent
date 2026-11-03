"""Serial journal reconciliation; child workers retain all execution ownership."""
from __future__ import annotations

import logging
import threading


LOG = logging.getLogger(__name__)


class BatchWorkflow:
    def __init__(self, batch_store, *, enabled=True):
        if type(enabled) is not bool:
            raise ValueError('An explicit boolean batch feature gate is required')
        self.store, self.enabled = batch_store, enabled
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='video-batch-reconcile', daemon=True)
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
            self.store.tick(enabled=self.enabled)
        except Exception:
            # Details could contain narration or provider credentials. Preserve
            # the prepared request for local lookup on the next reconciliation.
            LOG.error('Batch reconciliation interrupted; durable state retained')
        finally:
            self.lock.release()
