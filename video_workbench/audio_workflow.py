"""One serial speech worker; claimed requests are never automatically repeated."""
from __future__ import annotations

import logging
import threading

from agent_service.contracts import ServiceError

LOG = logging.getLogger(__name__)


class _Cancellation:
    def __init__(self, workflow, operation_id):
        self.workflow, self.operation_id = workflow, operation_id

    def is_set(self):
        return (self.workflow.stopping.is_set()
                or self.workflow.store.audio_operation(self.operation_id)['cancel_requested'])


class AudioWorkflow:
    def __init__(self, store, speech_client=None):
        self.store, self.speech_client = store, speech_client
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None
        self._recovered = False

    def require_client(self):
        if self.speech_client is None:
            raise ServiceError('speech_not_configured', 503)
        return self.speech_client

    def start(self):
        self.thread = threading.Thread(target=self._run, name='video-speech', daemon=True)
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
        """Process at most one queued request, blocking until that request exits."""
        if not self.lock.acquire(blocking=False):
            return
        try:
            if not self._recovered:
                self.store.recover_audio_operations()
                self._recovered = True
            if self.speech_client is None or self.stopping.is_set():
                return
            for operation in self.store.active_audio_operations():
                op = self.store.claim_audio(operation['id'])
                if op is None:
                    continue
                try:
                    if (op['input']['settings']['model'] != self.speech_client.settings.model
                            or self.speech_client.settings.voice is not None
                            and op['input']['settings']['voice'] != self.speech_client.settings.voice):
                        raise ServiceError('speech_provider_changed', 409)
                    settings = op['input']['settings']
                    result = self.speech_client.synthesize(op['input']['spoken_text'],
                        voice=settings['voice'], speed=settings['speed'],
                        cancel_event=_Cancellation(self, op['id']))
                    self.store.finish_audio(op['id'], audio=result)
                except ServiceError as exc:
                    self.store.finish_audio(op['id'], error=exc.code,
                                            interrupted=self.stopping.is_set())
                except Exception:
                    # Error strings may contain upstream credentials or text.
                    LOG.error('Speech operation failed: %s', op['id'])
                    self.store.finish_audio(op['id'], error='speech_generation_failed',
                                            interrupted=self.stopping.is_set())
                break
        finally:
            self.lock.release()
