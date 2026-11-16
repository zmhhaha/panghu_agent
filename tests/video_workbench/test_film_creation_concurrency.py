"""Real workflow threads with controlled, explicitly synthetic remote peers.

These tests prove scheduling and durable cancellation/restart behavior. They do
not render browser frames, execute an Agent, or establish visual admission.
"""
from copy import deepcopy
from pathlib import Path
import threading

from agent_service.contracts import ServiceError
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from test_film_creation_workflow import (
    OWNER, WholeRenderer, completed_modules, env, render_request, start_module,
)
from test_film_render_adapter import VIDEO, result_metadata


class BlockingRenderer:
    """An interruptible remote wait with observable invocation boundaries."""

    def __init__(self, creation):
        self.creation = creation
        self.condition = threading.Condition()
        self.calls, self.gates, self.business_cancellations, self.errors = [], {}, [], []
        self.active = self.max_active = 0

    def wait_for_calls(self, count, timeout=8):
        with self.condition:
            return self.condition.wait_for(lambda: len(self.calls) >= count, timeout)

    def release(self, operation_id):
        with self.condition:
            self.gates[operation_id].set()

    def release_all(self):
        with self.condition:
            for gate in self.gates.values():
                gate.set()

    def render(self, snapshot, directory, **options):
        identity = options['operation_id']
        durable = self.creation.operation('render', identity)
        assert durable['submit_attempted'] and durable['snapshot_sha256'] == snapshot['sha256']
        with self.condition:
            gate = self.gates.setdefault(identity, threading.Event())
            self.calls.append((deepcopy(snapshot), options))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.condition.notify_all()
        try:
            while not gate.wait(.02):
                self._check_cancel(identity, options['cancel_event'])
            self._check_cancel(identity, options['cancel_event'])
            path = Path(directory) / 'video.mp4'
            path.write_bytes(VIDEO)
            return dict(result_metadata(snapshot), path=str(path))
        except ServiceError as exc:
            with self.condition:
                self.errors.append((identity, exc.code))
            raise
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def _check_cancel(self, identity, cancel_event):
        if cancel_event.is_set():
            with self.condition:
                self.business_cancellations.append(identity)
            error = ServiceError('media_render_cancelled', 409)
            error.stop_proven = True
            raise error


def queue_render(env, modules, key):
    return env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), key)


def test_blocked_render_does_not_starve_another_module_cancellation(env):
    modules = completed_modules(env)
    renderer = BlockingRenderer(env.creation)
    env.workflow.renderer = renderer
    render = queue_render(env, modules, 'blocked-render-module-cancel')
    module = start_module(env, idem='independent-running-module')
    env.gateway.state, env.gateway.proven = 'running', False
    polled, cancelled = threading.Event(), threading.Event()
    original_poll, original_cancel = env.gateway.poll, env.gateway.cancel

    def poll(job_id):
        result = original_poll(job_id)
        if job_id == env.gateway.jobs.get(module['id']):
            polled.set()
        return result

    def cancel(job_id):
        original_cancel(job_id)
        if job_id == env.gateway.jobs.get(module['id']):
            cancelled.set()

    env.gateway.poll, env.gateway.cancel = poll, cancel
    try:
        env.workflow.start()
        assert renderer.wait_for_calls(1), 'The render worker did not enter its remote wait'
        assert polled.wait(8), 'The independent module was not submitted and polled'
        env.workflow.cancel_module(OWNER, module['id'])
        assert cancelled.wait(8), 'Module cancellation was blocked behind the render wait'
        assert renderer.active == 1 and renderer.business_cancellations == []
        assert renderer.calls[0][1]['operation_id'] == render['id']
        child = env.workflow.get_module(OWNER, module['id'])
        assert child['cancel_requested'] and child['status'] == 'cancelling'
        assert child['stop_proven'] is False
    finally:
        env.workflow.stop()
        renderer.release_all()


def test_background_and_public_tick_share_one_bounded_render_worker(env):
    modules = completed_modules(env)
    renderer = BlockingRenderer(env.creation)
    env.workflow.renderer = renderer
    first = queue_render(env, modules, 'single-render-worker-first')
    second = queue_render(env, modules, 'single-render-worker-second')
    tick_done = threading.Event()

    def synchronous_tick():
        try:
            env.workflow.tick()
        finally:
            tick_done.set()

    tick_worker = None
    try:
        env.workflow.start()
        assert renderer.wait_for_calls(1)
        tick_worker = threading.Thread(target=synchronous_tick, daemon=True)
        tick_worker.start()
        assert tick_done.wait(2), 'A synchronous tick must not join an occupied render worker'
        assert len(renderer.calls) == 1 and renderer.max_active == 1
        assert renderer.calls[0][1]['operation_id'] == first['id']
        assert env.creation.operation('render', second['id'])['submit_attempted'] is False
        renderer.release(first['id'])
        assert renderer.wait_for_calls(2), 'The next render did not start after the first finished'
        assert renderer.calls[1][1]['operation_id'] == second['id']
        assert renderer.active == 1 and renderer.max_active == 1
        assert env.workflow.get_render(OWNER, first['id'])['status'] == 'completed'
    finally:
        env.workflow.stop()
        renderer.release_all()
        if tick_worker is not None:
            tick_worker.join(timeout=2)


def test_threaded_shutdown_keeps_original_render_recoverable_without_business_cancel(env):
    modules = completed_modules(env)
    renderer = BlockingRenderer(env.creation)
    env.workflow.renderer = renderer
    op = queue_render(env, modules, 'threaded-shutdown-render')
    try:
        env.workflow.start()
        assert renderer.wait_for_calls(1)
        env.workflow.stop()
        assert renderer.active == 0
        assert renderer.business_cancellations == []
        assert renderer.errors == [(op['id'], 'film_creation_restart_pending')]
        pending = env.workflow.get_render(OWNER, op['id'])
        assert pending['status'] == 'submitting' and pending['stop_proven'] is False
        assert pending['cancel_requested'] is False
        assert pending['error'] == 'film_creation_restart_pending'
        original = env.creation.operation('render', op['id'])

        resumed_renderer = WholeRenderer(env.creation)
        resumed = FilmCreationWorkflow(env.creation, env.gateway, env.adapter, resumed_renderer, enabled=False)
        resumed.tick()
        result = resumed.get_render(OWNER, op['id'])
        assert result['status'] == 'completed' and result['stop_proven'] is True
        assert resumed_renderer.calls[0][1]['operation_id'] == op['id']
        assert resumed_renderer.calls[0][0] == renderer.calls[0][0]
        assert env.creation.operation('render', op['id'])['snapshot_sha256'] == original['snapshot_sha256']
        assert resumed.video(OWNER, op['id']) == VIDEO
    finally:
        env.workflow.stop()
        renderer.release_all()
