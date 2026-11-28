"""Scene queue and real sampler custody with inert synthetic render fixtures.

The native prepare/read adapters are exercised unchanged; no browser, paid model
or claim of visual acceptance is involved. Fixture MP4 bytes are not a movie.
"""
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_renderer import MethodAnimationRenderer
from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.scene_space_capture import SAMPLE_CAPTURE_POLICY
from video_workbench.scene_space_render_http import SceneSpaceRenderService, SceneSpaceRenderClient
from video_workbench.scene_space_renderer import SceneSpaceRenderer
from video_workbench.visual_sampling import VisualSamplingService
from test_scene_space_render_http import FixtureRenderer, render_request


class SharedFixtureRenderer(FixtureRenderer):
    """Keep fixture execution, using the actual native shared-sampling adapters."""
    def __init__(self, root, sampler):
        super().__init__(root)
        self.owner = MethodAnimationRenderer.__new__(MethodAnimationRenderer)
        self.owner.sampling_service = sampler
        self.bindings, self.sampling_calls, self.read_calls = {}, [], []

    def render(self, identity, source, document, *, scene_space, **kwargs):
        result = super().render(identity, source, document, scene_space=scene_space, **kwargs)
        if result.get('status') == 'rendered':
            static = 'duration_seconds' not in document['constraints']
            frame_range = kwargs.get('frame_range') or dict(start_frame=0,
                end_frame=1 if static else scene_space['duration_frames'])
            request = dict(static=static)
            if 'sample_capture' in document['constraints']:
                request['sample_capture'] = deepcopy(document['constraints']['sample_capture'])
            self.bindings[identity] = dict(scene_space=dict(frame_range=frame_range), request=request)
            result['evidence_sha256'] = 'd'*64
            result['runtime'] = dict(scope='synthetic_fixture_no_native_execution')
            self.results[identity] = deepcopy(result)
        return result

    def _load(self, operation_id):
        return deepcopy(self.bindings[operation_id])

    def prepare_sampling(self, owner, operation_id, max_frames=12, *, deadline_at=None,
                         on_intent=None, cancelled=None):
        self.sampling_calls.append((owner, operation_id, max_frames, deadline_at))
        return SceneSpaceRenderer.prepare_sampling(self, owner, operation_id,
            max_frames=max_frames, deadline_at=deadline_at, on_intent=on_intent, cancelled=cancelled)

    def read_sampling(self, owner, reference):
        self.read_calls.append((owner, deepcopy(reference)))
        return SceneSpaceRenderer.read_sampling(self, owner, reference)


def setup(tmp_path):
    sampler = VisualSamplingService(tmp_path/'sampling', clock=lambda: 100.)
    renderer = SharedFixtureRenderer(tmp_path, sampler)
    ticks = iter([10., 10.125])
    service = SceneSpaceRenderService(tmp_path/'queue', renderer, sampling_service=sampler,
        clock=lambda: 100., monotonic=lambda: next(ticks))
    return service, renderer, sampler


def stored(service, operation_id):
    with service._db() as db:
        return service._load(db, 'alice', operation_id)


def inventory(root):
    return {path.relative_to(root).as_posix():
        (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('capture', [False, True])
def test_explicit_advance_retains_original_metadata_and_get_reads_only_shared_bytes(
        tmp_path, render_request, capture):
    if capture:
        render_request['document']['constraints']['sample_capture'] = deepcopy(SAMPLE_CAPTURE_POLICY)
        render_request['frame_range'] = dict(start_frame=24, end_frame=48)
    service, renderer, sampler = setup(tmp_path)
    queued = service.start('alice', render_request, 'explicit-advance')
    assert service.get('alice', queued['id']) == queued
    assert renderer.calls == renderer.sampling_calls == renderer.frame_calls == []
    assert not list(sampler.blobs.iterdir())
    with pytest.raises(ServiceError, match='frames_unavailable'):
        service.frames('alice', queued['id'])
    complete = service.advance('alice', queued['id'])
    assert complete['status'] == 'completed' and complete['stop_proven'] is True
    assert complete['usage'] == dict(model_calls=0, model_tokens=0, render_seconds=.125)
    assert renderer.calls == [queued['id']]
    assert renderer.frame_calls == [(queued['id'], 3)]
    reference = stored(service, queued['id'])['sampling_ref']
    manifest = sampler.get('alice', reference['operation_id'])
    assert manifest['status'] == 'completed' and manifest['usage']['model_calls'] == 0
    assert len(manifest['frames']) == 3
    # Repeated pixels at distinct source-clock positions remain independent.
    assert len({row['sha256'] for row in manifest['frames']}) == 1
    assert len({row['timestamp_seconds'] for row in manifest['frames']}) == 3
    native = deepcopy(renderer.samples[queued['id']])
    before = inventory(tmp_path)
    preparations = list(renderer.sampling_calls)
    for limit in (1, 2, 3):
        assert service.frames('alice', queued['id'], limit) == native[:limit]
        response = service.frames_response('alice', queued['id'], limit)
        expected = {'schema', 'operation_id', 'request_sha256', 'plan_sha256', 'source_sha256',
            'snapshot_sha256', 'viewport', 'duration_seconds', 'frames'}
        if capture: expected |= {'sample_capture', 'sample_frame_range'}
        assert set(response) == expected
        assert response['schema'] == ('video.scene-space-preview-frames.v2' if capture
                                     else 'video.scene-space-preview-frames.v1')
        assert [row['timestamp_seconds'] for row in response['frames']] == [
            row['timestamp_seconds'] for row in native[:limit]]
        assert [('capture' in row) for row in response['frames']] == [capture]*limit
        assert service.get('alice', queued['id']) == complete
    assert inventory(tmp_path) == before
    assert renderer.sampling_calls == preparations and renderer.frame_calls == [(queued['id'], 3)]
    assert service.start('alice', render_request, 'explicit-advance') == complete
    assert service.advance('alice', queued['id']) == complete
    assert renderer.calls == [queued['id']] and len(renderer.sampling_calls) == 1
    with pytest.raises(ServiceError, match='not_found'):
        service.frames('bob', queued['id'])


@pytest.mark.parametrize('capture', [False, True])
def test_shared_path_preserves_the_previous_preview_response(tmp_path, render_request, capture):
    if capture:
        render_request['document']['constraints']['sample_capture'] = deepcopy(SAMPLE_CAPTURE_POLICY)
    old_root, new_root = tmp_path/'old', tmp_path/'new'
    old_root.mkdir(); new_root.mkdir()
    old_renderer = SharedFixtureRenderer(old_root, None)
    old_ticks = iter([10., 10.125])
    old = SceneSpaceRenderService(old_root/'queue', old_renderer, clock=lambda: 100.,
        monotonic=lambda: next(old_ticks))
    new, _, _ = setup(new_root)
    old_run = old.start('alice', render_request, 'same-source')
    new_run = new.start('alice', render_request, 'same-source')
    old_done = old.advance('alice', old_run['id'])
    new_done = new.advance('alice', new_run['id'])
    assert old_done == new_done
    for limit in (1, 2, 3):
        assert old.frames_response('alice', old_run['id'], limit) == new.frames_response(
            'alice', new_run['id'], limit)


@pytest.mark.parametrize('capture', [False, True])
def test_authenticated_http_get_preserves_original_frame_envelope_without_native_reads(
        tmp_path, render_request, capture):
    if capture:
        render_request['document']['constraints']['sample_capture'] = deepcopy(SAMPLE_CAPTURE_POLICY)
    service, renderer, _ = setup(tmp_path)
    token = 'synthetic-scene-sampling-token-'+'a'*32
    server = CreationHTTPServer(SimpleNamespace(), token, pump=False, scene_space_service=service)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = SceneSpaceRenderClient('http://127.0.0.1:'+str(server.server_port), token)
    try:
        queued = client.start('alice', render_request, 'http-sampling')
        assert client.get('alice', queued['id'])['status'] == 'queued'
        assert renderer.calls == renderer.sampling_calls == renderer.frame_calls == []
        complete = service.advance('alice', queued['id'])
        before = inventory(tmp_path)
        assert client.get('alice', queued['id']) == complete
        assert client.frames('alice', queued['id']) == renderer.samples[queued['id']]
        assert client.frames('alice', queued['id'], 2) == renderer.samples[queued['id']][:2]
        assert inventory(tmp_path) == before
        assert renderer.frame_calls == [(queued['id'], 3)] and len(renderer.sampling_calls) == 1
        with pytest.raises(ServiceError, match='not_found'):
            client.frames('bob', queued['id'])
    finally:
        server.shutdown(); server.server_close(); worker.join(3)


def test_sampler_failure_preserves_finished_render_stop_and_original_cost(
        tmp_path, render_request, monkeypatch):
    service, renderer, sampler = setup(tmp_path)
    original = renderer.frames
    def invalid(operation_id, max_frames=3):
        rows = original(operation_id, max_frames)
        rows[0]['raw'] = b'not an image'
        return rows
    monkeypatch.setattr(renderer, 'frames', invalid)
    queued = service.start('alice', render_request, 'sampling-failure')
    failed = service.advance('alice', queued['id'])
    assert failed['status'] == 'failed' and failed['stop_proven'] is True
    assert failed['result']['mime'] == 'video/mp4' and failed['result']['sha256']
    assert failed['usage'] == dict(model_calls=0, model_tokens=0, render_seconds=.125)
    assert failed['error'] == 'scene_space_sampling_unavailable'
    assert stored(service, queued['id'])['sampling_pending'] is False
    before = inventory(tmp_path)
    assert service.get('alice', queued['id']) == failed
    assert service.advance('alice', queued['id']) == failed
    assert service.cancel('alice', queued['id']) == failed
    assert inventory(tmp_path) == before
    assert renderer.calls == [queued['id']] and len(renderer.sampling_calls) == 1
    assert renderer.frame_calls == [(queued['id'], 3)]


@pytest.mark.parametrize('after_retention', [False, True])
def test_interrupted_sampling_resumes_same_id_without_rendering_or_resampling_finished_bytes(
        tmp_path, render_request, monkeypatch, after_retention):
    service, renderer, sampler = setup(tmp_path)
    original = renderer.read_sampling if after_retention else renderer.prepare_sampling
    interrupted = [False]
    def checkpoint(*args, **kwargs):
        if not interrupted[0]:
            interrupted[0] = True
            raise KeyboardInterrupt('synthetic coordinator termination')
        return original(*args, **kwargs)
    monkeypatch.setattr(renderer, 'read_sampling' if after_retention else 'prepare_sampling', checkpoint)
    queued = service.start('alice', render_request, 'interrupted-sampling')
    with pytest.raises(KeyboardInterrupt):
        service.advance('alice', queued['id'])
    pending = stored(service, queued['id'])
    assert pending['sampling_pending'] is True and pending['status'] == 'running'
    assert pending['stop_proven'] is False and pending['render_stop_proven'] is True
    assert pending['result']['mime'] == 'video/mp4'
    assert pending['usage']['render_seconds'] == .125
    calls = list(renderer.frame_calls)
    before = inventory(tmp_path)
    service.get('alice', queued['id'])
    assert inventory(tmp_path) == before and renderer.frame_calls == calls
    restarted = SceneSpaceRenderService(service.root, renderer, sampling_service=sampler,
        clock=lambda: 100., monotonic=lambda: pytest.fail('renderer re-dispatched'))
    complete = restarted.advance('alice', queued['id'])
    assert complete['status'] == 'completed' and complete['stop_proven'] is True
    assert complete['usage'] == pending['usage'] and complete['result'] == pending['result']
    assert renderer.calls == [queued['id']] and renderer.frame_calls == [(queued['id'], 3)]
    assert restarted.frames('alice', queued['id']) == renderer.samples[queued['id']]
    assert renderer.frame_calls == [(queued['id'], 3)]


def test_cancel_retained_render_before_sampling_never_samples_or_rerenders(
        tmp_path, render_request, monkeypatch):
    service, renderer, sampler = setup(tmp_path)
    original = renderer.prepare_sampling
    monkeypatch.setattr(renderer, 'prepare_sampling', lambda *a, **k:
        (_ for _ in ()).throw(KeyboardInterrupt('synthetic checkpoint')))
    queued = service.start('alice', render_request, 'cancel-pending')
    with pytest.raises(KeyboardInterrupt): service.advance('alice', queued['id'])
    pending = stored(service, queued['id'])
    monkeypatch.setattr(renderer, 'prepare_sampling', original)
    assert service.cancel('alice', queued['id'])['status'] == 'cancelling'
    cancelled = service.advance('alice', queued['id'])
    assert cancelled['status'] == 'cancelled' and cancelled['stop_proven'] is True
    assert cancelled['result'] == pending['result'] and cancelled['usage'] == pending['usage']
    assert renderer.frame_calls == renderer.sampling_calls == []
    assert renderer.calls == [queued['id']] and not list(sampler.blobs.iterdir())


def test_concurrent_sampling_advances_cannot_fail_the_in_progress_render(
        tmp_path, render_request, monkeypatch):
    service, renderer, sampler = setup(tmp_path)
    original = renderer.frames
    entered, release = threading.Event(), threading.Event()
    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(renderer, 'frames', blocking)
    queued = service.start('alice', render_request, 'concurrent-sampling')
    results, errors = [], []
    def advance():
        try: results.append(service.advance('alice', queued['id']))
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=advance)
    worker.start()
    assert entered.wait(10)
    try:
        concurrent = service.advance('alice', queued['id'])
        assert concurrent['status'] == 'running' and concurrent['error'] is None
        assert concurrent['stop_proven'] is False
        assert concurrent['result']['mime'] == 'video/mp4'
    finally:
        release.set(); worker.join(10)
    assert not worker.is_alive() and errors == []
    assert results[0]['status'] == 'completed'
    assert service.get('alice', queued['id'])['status'] == 'completed'
    assert renderer.calls == [queued['id']] and renderer.frame_calls == [(queued['id'], 3)]


def test_cancel_active_sampler_waits_for_actual_stop_and_keeps_original_render(
        tmp_path, render_request, monkeypatch):
    service, renderer, sampler = setup(tmp_path)
    original = renderer.frames
    entered, release = threading.Event(), threading.Event()
    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(renderer, 'frames', blocking)
    queued = service.start('alice', render_request, 'cancel-active-sampling')
    results, errors = [], []
    def advance():
        try: results.append(service.advance('alice', queued['id']))
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=advance)
    worker.start()
    assert entered.wait(10)
    try:
        pending = stored(service, queued['id'])
        intent = pending['sampling_intent']
        assert pending['stop_proven'] is False and pending['render_stop_proven'] is True
        cancelled = service.cancel('alice', queued['id'])
        assert cancelled['status'] == 'cancelling' and cancelled['stop_proven'] is False
        assert cancelled['result'] == pending['result'] and cancelled['usage'] == pending['usage']
        sample = sampler.get('alice', intent['operation_id'])
        assert sample['cancel_requested'] is True and sample['stop_proven'] is False
        assert service.get('alice', queued['id'])['stop_proven'] is False
    finally:
        release.set(); worker.join(10)
    assert not worker.is_alive() and errors == []
    stopped = service.get('alice', queued['id'])
    assert stopped['status'] == 'cancelled' and stopped['stop_proven'] is True
    assert stopped['result'] == pending['result'] and stopped['usage'] == pending['usage']
    sample = sampler.get('alice', intent['operation_id'])
    assert sample['status'] == 'cancelled' and sample['stop_proven'] is True
    assert renderer.calls == [queued['id']] and renderer.frame_calls == [(queued['id'], 3)]
    preparations = list(renderer.sampling_calls)
    assert service.advance('alice', queued['id']) == stopped
    assert service.cancel('alice', queued['id']) == stopped
    assert renderer.sampling_calls == preparations


@pytest.mark.parametrize('lookup_failure', [False, True])
def test_late_render_reconciliation_cannot_overwrite_cost_while_sampling_is_pending(
        tmp_path, render_request, monkeypatch, lookup_failure):
    service, renderer, _ = setup(tmp_path)
    original_render, original_lookup, original_frames = renderer.render, renderer.lookup, renderer.frames
    entered_render, release_render = threading.Event(), threading.Event()
    entered_lookup, release_lookup = threading.Event(), threading.Event()
    entered_sample, release_sample = threading.Event(), threading.Event()
    def render(*args, **kwargs):
        entered_render.set(); assert release_render.wait(10)
        return original_render(*args, **kwargs)
    def lookup(*args, **kwargs):
        if threading.current_thread().name == 'late-render-reconciliation':
            entered_lookup.set(); assert release_lookup.wait(10)
            if lookup_failure:
                raise ServiceError('synthetic_late_lookup_error', 503)
        return original_lookup(*args, **kwargs)
    def frames(*args, **kwargs):
        entered_sample.set(); assert release_sample.wait(10)
        return original_frames(*args, **kwargs)
    monkeypatch.setattr(renderer, 'render', render)
    monkeypatch.setattr(renderer, 'lookup', lookup)
    monkeypatch.setattr(renderer, 'frames', frames)
    queued = service.start('alice', render_request, 'late-reconcile-sampling')
    results, errors = {}, []
    def advance(label):
        try: results[label] = service.advance('alice', queued['id'])
        except BaseException as exc: errors.append(exc)
    main = threading.Thread(target=advance, args=('main',))
    late = threading.Thread(target=advance, args=('late',), name='late-render-reconciliation')
    main.start()
    assert entered_render.wait(10)
    late.start()
    assert entered_lookup.wait(10)
    try:
        release_render.set()
        assert entered_sample.wait(10)
        pending = service.get('alice', queued['id'])
        assert pending['usage']['render_seconds'] == .125 and pending['stop_proven'] is False
        release_lookup.set(); late.join(10)
        assert not late.is_alive() and errors == []
        assert results['late']['usage'] == pending['usage']
        assert results['late']['result'] == pending['result']
        assert results['late']['status'] == 'running'
    finally:
        release_render.set(); release_lookup.set(); release_sample.set()
        main.join(10); late.join(10)
    assert not main.is_alive() and not late.is_alive() and errors == []
    assert results['main']['status'] == 'completed'
    assert results['main']['usage'] == pending['usage']
    assert renderer.calls == [queued['id']] and renderer.frame_calls == [(queued['id'], 3)]
