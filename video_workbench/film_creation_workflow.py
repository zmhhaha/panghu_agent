"""Reconcile durable film children using the shared Agent and renderer services.

There is no fallback author, scene MP4 renderer or independent execution daemon.
An uncertain paid admission is only looked up by its original immutable key.
"""
import logging
import math
from pathlib import Path
import re
import tempfile
import threading
import time

from agent_service.contracts import ServiceError
from agent_service.profiles.video_animation_film_module import PROFILE
from .agent import _uuid
from .film_creation_store import ACTIVE, PENDING_SCHEMA

LOG = logging.getLogger(__name__)
_SHA = re.compile(r'[0-9a-f]{64}\Z')
CANCEL_DELAYS = (2, 5, 15, 30)


def stopped_module_job(job):
    """A terminal state alone is not evidence that all original writers stopped."""
    attempt, proof, pins = (job.get(name) for name in ('attempt', 'stop_proof', 'pins'))
    if not (job.get('stop_proven') is True and type(attempt) is dict and _uuid(attempt.get('attempt_id'))
            and type(attempt.get('fence')) is int and attempt['fence'] >= 1
            and type(proof) is dict and proof.get('stop_proven') is True
            and proof.get('attempt_id') == attempt['attempt_id']
            and type(proof.get('fence')) is int and proof['fence'] == attempt['fence']
            and type(pins) is dict and pins.get('profile') == PROFILE and pins.get('profile_revision') == '1'
            and all(type(proof.get(name)) is str and _SHA.fullmatch(proof[name])
                for name in ('request_sha256', 'result_sha256'))
            and type(proof.get('resource_ids')) is list and 1 <= len(proof['resource_ids']) <= 64
            and all(type(value) is str and 1 <= len(value) <= 200
                and all(ord(c) >= 32 and ord(c) != 127 for c in value) for value in proof['resource_ids'])
            and len(set(proof['resource_ids'])) == len(proof['resource_ids'])
            and type(proof.get('observed_at')) in {int, float}
            and math.isfinite(proof['observed_at']) and proof['observed_at'] > 0):
        return False
    return True


class _RenderCancellation:
    def __init__(self, creation, operation_id, stopping):
        self.creation, self.operation_id, self.stopping = creation, operation_id, stopping

    def is_set(self):
        if self.stopping.is_set():
            # Application shutdown is not user cancellation. Abort this local
            # wait while retaining the original remote operation for recovery.
            raise ServiceError('film_creation_restart_pending', 503)
        op = self.creation.operation('render', self.operation_id)
        return op['cancel_requested'] or time.time() >= op['deadline_at']

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.stopping.wait(min(.2, remaining))
        return True


class FilmCreationWorkflow:
    def __init__(self, creation_store, gateway, skill_adapter, renderer, *, enabled=False, render_readiness=None,
            resource_client=None):
        if type(enabled) is not bool:
            raise ValueError('An explicit film creation gate is required')
        self.creation, self.gateway, self.skill_adapter, self.renderer = creation_store, gateway, skill_adapter, renderer
        self.enabled, self.render_readiness = enabled, render_readiness
        self.resource_client = resource_client
        self.lock, self.render_lock, self.stopping = threading.Lock(), threading.Lock(), threading.Event()
        self.thread, self.render_thread = None, None

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self.creation, name)

    def readiness(self):
        if not (self.enabled and self.creation.enabled and self.gateway is not None
                and getattr(self.gateway, 'profile', None) == PROFILE
                and getattr(self.gateway, 'profile_revision', '1') == '1'
                and self.skill_adapter is not None and callable(getattr(self.skill_adapter, 'freeze', None))
                and self.renderer is not None and callable(self.render_readiness)):
            raise ServiceError('film_creation_unavailable', 503)
        self.gateway.preflight(time.time() + 30)
        admitted = self.render_readiness()
        if admitted is not True and not (type(admitted) is dict and admitted.get('available') is True):
            raise ServiceError('hyperframes_film_not_admitted', 503)
        return dict(available=True, renderer_mode='hyperframes-film',
            configuration_sha256=self.creation.configuration_sha256, max_seconds=60, fps=24)

    def freeze_configuration(self, owner, project_id):
        self.creation.store.project(owner, project_id)
        return self.readiness()

    def preflight_resources(self, owner):
        from .manim_film_admission import validate_producer_capabilities
        configuration = self.creation.configuration or {}
        context = configuration.get('resource_authoring')
        if self.resource_client is None or context is None:
            raise ServiceError('film_resource_not_admitted', 503)
        return validate_producer_capabilities(self.resource_client.capabilities(owner), context)

    def start_module(self, owner, project_id, scene_id, request, idem):
        try:
            self.creation.module_by_request_key(owner, idem)
        except ServiceError as exc:
            if exc.status != 404:
                raise
            self.readiness()
            if 'resource_authoring' in request:
                self.preflight_resources(owner)
        return self.creation.start_module(owner, project_id, scene_id, request, idem)

    def start_render(self, owner, project_id, request, idem):
        try:
            self.creation.render_by_request_key(owner, idem)
        except ServiceError as exc:
            if exc.status != 404:
                raise
            self.readiness()
        return self.creation.start_render(owner, project_id, request, idem)

    def start(self):
        active = self.enabled or bool(self.creation.active_operations())
        if self.thread is None and active:
            self.thread = threading.Thread(target=self._run, args=('module',), name='video-film-module-reconcile', daemon=True)
            self.thread.start()
        if self.render_thread is None and active:
            self.render_thread = threading.Thread(target=self._run, args=('render',), name='video-film-render-reconcile', daemon=True)
            self.render_thread.start()

    def stop(self):
        self.stopping.set()
        for worker in (self.thread, self.render_thread):
            if worker is not None:
                worker.join(timeout=12)

    def _run(self, kind):
        while not self.stopping.is_set():
            self._tick_kind(kind)
            self.stopping.wait(2)

    def tick(self):
        """One synchronous reconciliation pass, also useful without threads."""
        self._tick_kind('module')
        self._tick_kind('render')

    def _tick_kind(self, kind):
        if self.stopping.is_set():
            return
        lock = self.lock if kind == 'module' else self.render_lock
        if not lock.acquire(blocking=False):
            return
        try:
            operations = [op for op in self.creation.active_operations() if op['kind'] == kind]
            # A single bounded full-film worker does not block module polling or
            # cancellation in the other reconciliation thread.
            for op in operations[:1] if kind == 'render' else operations:
                self._reconcile(op)
        finally:
            lock.release()

    def _reconcile(self, op):
        try:
            (self._module if op['kind'] == 'module' else self._render)(op)
        except ServiceError as exc:
            current = self.creation.operation(op['kind'], op['id'])
            # Admission/read/cleanup uncertainty remains recoverable.
            if (current['submit_attempted'] and not current['stop_proven']
                    or current.get('resource_submit_attempted') and not current.get('resource_stop_proven')):
                self.creation.update_operation(op['kind'], op['id'], error=exc.code)
            else:
                self.creation.update_operation(op['kind'], op['id'], status='failed', error=exc.code)
        except Exception:
            LOG.exception('Film child reconciliation failed: %s', op['id'])
            self.creation.update_operation(op['kind'], op['id'], error='film_creation_reconciliation_failed')

    def _new_admission(self, op):
        if op['configuration_sha256'] != self.creation.configuration_sha256:
            raise ServiceError('film_creation_configuration_changed', 409)
        self.readiness()

    def _module(self, initial):
        op = self.creation.operation('module', initial['id'])
        if op['status'] not in ACTIVE:
            return
        if (not op['submit_attempted'] and (not op.get('resource_submit_attempted') or op.get('resource_stop_proven'))
                and (op['cancel_requested'] or time.time() >= op['deadline_at'])):
            self.creation.update_operation('module', op['id'],
                status='cancelled' if op['cancel_requested'] else 'timed_out', stop_proven=True)
            return
        if op.get('resource_authoring') and not op.get('generated_resource'):
            if not self._resource(op):
                return
            op = self.creation.operation('module', op['id'])
        if self.gateway is None:
            raise ServiceError('film_module_profile_unavailable', 503)
        if not op['job_id']:
            if not op['submit_attempted']:
                self._new_admission(op)
                if op['document']['schema'] == PENDING_SCHEMA:
                    op = self.creation.prepare_module(op['id'], self.skill_adapter)
                if op['cancel_requested']:
                    return
                if op['input_ref'] is None:
                    receipt = self.gateway.upload(op['document'])
                    op = self.creation.update_operation('module', op['id'], input_ref=receipt)
                admit = self.gateway.prepare_submission(op['id'], op['document'], op['deadline_at'], input_ref=op['input_ref'])
                if self.stopping.is_set():
                    return
                op = self.creation.claim_submission('module', op['id'])
                if op is None:
                    return
                try:
                    job = admit()
                except ServiceError as exc:
                    if exc.status < 500:
                        self.creation.update_operation('module', op['id'], status='failed',
                            error=exc.code, stop_proven=True)
                    else:
                        self.creation.update_operation('module', op['id'], error='film_module_submission_uncertain')
                    return
            else:
                try:
                    job = self.gateway.lookup(op['id'])
                except ServiceError as exc:
                    if exc.status == 404:
                        self.creation.update_operation('module', op['id'],
                            error='film_module_submission_unconfirmed')
                        return
                    raise
            op = self.creation.update_operation('module', op['id'], job_id=job['job_id'], error=None)
        job = self.gateway.poll(op['job_id'])
        op = self.creation.operation('module', op['id'])
        state = job['state']
        if state in {'completed', 'failed', 'needs_input', 'cancelled', 'timed_out', 'quarantined'}:
            if not stopped_module_job(job):
                self.creation.update_operation('module', op['id'], status='cancelling', error='film_module_stop_unproven')
                return
            self.creation.update_operation('module', op['id'], stop_proven=True)
            if state == 'completed':
                delivered = self.gateway.collect(op['job_id'], op['input_sha256'], op['document'])
                self.creation.complete_module(op['id'], delivered)
            else:
                self.creation.update_operation('module', op['id'],
                    status='failed' if state == 'quarantined' else state,
                    error=None if state == 'cancelled' else 'agent_' + state, stop_proven=True)
        elif state == 'cancelling':
            self.creation.update_operation('module', op['id'], status='cancelling')
        elif op['cancel_requested'] or time.time() >= op['deadline_at']:
            attempts = op['cancel_attempts']
            if attempts < len(CANCEL_DELAYS) and time.time() >= op['next_cancel_at']:
                self.creation.update_operation('module', op['id'], status='cancelling',
                    cancel_attempts=attempts + 1, next_cancel_at=time.time() + CANCEL_DELAYS[attempts])
                self.gateway.cancel(op['job_id'])
            elif attempts >= len(CANCEL_DELAYS):
                self.creation.update_operation('module', op['id'], status='cancelling', error='film_module_cancellation_unconfirmed')
        else:
            self.creation.update_operation('module', op['id'], status='running', error=None)

    def _resource(self, initial):
        """One frozen producer operation; no recipe permission for the module."""
        from agent_service.contracts import digest
        op = self.creation.operation('module', initial['id'])
        if self.resource_client is None:
            raise ServiceError('film_resource_client_unavailable', 503)
        cancelled = op['cancel_requested'] or time.time() >= op['deadline_at']
        if not op.get('resource_submit_attempted'):
            if cancelled or self.stopping.is_set():
                return False
            self._new_admission(op)
            self.preflight_resources(op['owner'])
            op = self.creation.claim_resource(op['id'])
            if op is None:
                return False
            # This POST only reserves an idempotent remote resource intent.
            # Its producer claims are separate and precede actual paid work.
            result = self.resource_client.start(op['owner'], op['resource_request'])
        else:
            try:
                result = self.resource_client.get(op['owner'], op['id'])
            except ServiceError as error:
                if error.status != 404 or op.get('resource_reservation_confirmed'):
                    raise
                # Cover the local-claim -> POST crash window by replaying only
                # this exact unpaid reservation. Never invent a key, request,
                # producer attempt or Cairo operation. A previously confirmed
                # remote record disappearing is not recreated.
                result = (self.resource_client.cancel_reservation(op['owner'], op['resource_request'])
                    if cancelled else self.resource_client.start(op['owner'], op['resource_request']))
        if not (type(result) is dict and result.get('operation_id') == op['id']
                and result.get('project_id') == op['project_id'] and result.get('scene_id') == op['scene_id']
                and result.get('film_sha256') == op['film_sha256']
                and result.get('request_sha256') == digest(op['resource_request'])
                and type(result.get('stop_proven')) is bool and type(result.get('status')) is str):
            raise ServiceError('film_resource_response_binding_mismatch', 502)
        self.creation.confirm_resource_reservation(op['id'])
        op = self.creation.operation('module', op['id'])
        cancelled = op['cancel_requested'] or time.time() >= op['deadline_at']
        status, stopped = result['status'], result['stop_proven']
        if status in {'completed', 'failed', 'cancelled'}:
            if not stopped:
                raise ServiceError('film_resource_stop_unproven', 502)
            # Persist actual stop evidence before collection/validation so a
            # rejected envelope does not masquerade as an outstanding process.
            self.creation.record_resource(op['id'], status='stopped', stop_proven=True)
            if cancelled or status != 'completed':
                self.creation.update_operation('module', op['id'], stop_proven=True,
                    status='cancelled' if op['cancel_requested'] or status == 'cancelled'
                        else 'timed_out' if cancelled else 'failed',
                    error=None if op['cancel_requested'] else 'film_resource_' + status)
                return False
            envelope = self.resource_client.envelope(op['owner'], op['id'])
            adopted = self.creation.record_resource(op['id'], status='completed', stop_proven=True,
                descriptor=result.get('descriptor'), envelope=envelope, image=result.get('resource_image'))
            return bool(adopted.get('generated_resource'))
        self.creation.record_resource(op['id'], status=status, stop_proven=False)
        if cancelled:
            self.resource_client.cancel(op['owner'], op['id'])
            self.creation.update_operation('module', op['id'], status='cancelling')
        else:
            self.creation.update_operation('module', op['id'], status='running', error=None)
        return False

    def _render(self, initial):
        op = self.creation.operation('render', initial['id'])
        if op['status'] not in ACTIVE:
            return
        if not op['submit_attempted']:
            if op['cancel_requested'] or time.time() >= op['deadline_at']:
                self.creation.update_operation('render', op['id'],
                    status='cancelled' if op['cancel_requested'] else 'timed_out', stop_proven=True)
                return
            self._new_admission(op)
            if self.stopping.is_set():
                return
            op = self.creation.claim_submission('render', op['id'])
            if op is None:
                return
        if self.renderer is None:
            raise ServiceError('film_renderer_unavailable', 503)
        snapshot = self.creation.render_snapshot(op['id'])
        configuration = op['configuration']
        cancel = _RenderCancellation(self.creation, op['id'], self.stopping)
        with tempfile.TemporaryDirectory(prefix='film-render-', dir=Path(self.creation.store.path).parent) as directory:
            try:
                result = self.renderer.render(snapshot, directory,
                    expected_snapshot_sha256=op['snapshot_sha256'], runtime_fingerprint=configuration['runtime_fingerprint'],
                    trusted_host_sha256=configuration['trusted_host_sha256'], image=configuration['image'],
                    operation_id=op['id'], owner=op['owner'], cancel_event=cancel)
                path = Path(result['path'])
                require_path = Path(directory) / 'video.mp4'
                if path != require_path or path.is_symlink() or not path.is_file():
                    raise ServiceError('film_creation_video_path_invalid', 502)
                data = path.read_bytes()
                self.creation.complete_render(op['id'], result, data)
            except ServiceError as exc:
                if getattr(exc, 'stop_proven', False):
                    self.creation.update_operation('render', op['id'], stop_proven=True,
                        status='cancelled' if exc.code == 'media_render_cancelled' else
                            'timed_out' if getattr(exc, 'remote_state', None) == 'timed_out' else 'failed', error=exc.code)
                    return
                raise
