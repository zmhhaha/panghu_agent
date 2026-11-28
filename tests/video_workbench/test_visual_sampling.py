"""Shared evidence custody, real media compatibility, and no model spending."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
from PIL import Image

from agent_service.contracts import ServiceError
from video_workbench.visual_sampling import (VisualSamplingService, select_indices,
    uniform_indices, verify_delivery)


def image(kind='PNG', color=(20, 80, 120)):
    out = io.BytesIO()
    Image.new('RGB', (32, 18), color).save(out, format=kind)
    return out.getvalue()


def provider(rows, calls=None):
    def read(owner, source_ref, policy, check):
        if calls is not None:
            calls.append((owner, source_ref, policy))
        check()
        return dict(source=dict(source_sha256='a' * 64, origin='native_retained'), frames=deepcopy(rows))
    return read


def frame(raw=None, timestamp=0, name='first', **metadata):
    raw = raw or image()
    return dict(raw=raw, mime='image/png', timestamp_seconds=timestamp,
                frame_id=name, role='output', **metadata)


def prepare(service, rows, operation='one', owner='owner', **kwargs):
    return service.prepare(owner, operation, {'provider': 'fixture', 'source_sha256': 'a' * 64},
        {'strategy': 'provided', 'max_frames': 24}, provider=provider(rows), **kwargs)


def test_identical_pixels_at_different_times_keep_distinct_identity_and_bytes(tmp_path):
    service = VisualSamplingService(tmp_path / 'sampling')
    rows = [frame(timestamp=0, name='first'), frame(timestamp=1, name='last')]
    result = prepare(service, rows)
    assert result['status'] == 'completed' and result['stop_proven']
    assert result['frames'][0]['sha256'] == result['frames'][1]['sha256']
    assert [r['frame_id'] for r in result['frames']] == ['first', 'last']
    assert (result['frames'][0]['width'], result['frames'][0]['height']) == (32, 18)
    assert result['frames'][0]['decoded_rgba_sha256'] == result['frames'][1]['decoded_rgba_sha256']
    assert service.read_frames('owner', 'one') == rows
    assert service.read_frame('owner', 'one', 1,
        expected_manifest_sha256=result['manifest_sha256']) == rows[1]
    with pytest.raises(ServiceError, match='frame_not_found'):
        service.read_frame('owner', 'one', -1)
    assert result['usage']['model_calls'] == 0
    assert result['usage']['cpu_scope'] == 'sampling_python_process_excludes_decoder_subprocess'


def test_completed_operation_is_frozen_reusable_and_reads_do_not_write(tmp_path):
    root = tmp_path / 'sampling'
    calls = []
    service = VisualSamplingService(root)
    source, policy = {'provider': 'fixture'}, {'strategy': 'provided'}
    result = service.prepare('owner', 'one', source, policy, provider=provider([frame()], calls))
    def inventory():
        return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob('*') if p.is_file()}
    before = inventory()
    assert service.get('owner', 'one') == result
    assert service.read_frames('owner', 'one', expected_manifest_sha256=result['manifest_sha256']) == [frame()]
    assert inventory() == before
    restarted = VisualSamplingService(root)
    again = restarted.prepare('owner', 'one', source, policy,
        provider=lambda *args: pytest.fail('completed operation resolved source again'))
    assert again == result and len(calls) == 1
    with pytest.raises(ServiceError, match='operation_conflict'):
        restarted.prepare('owner', 'one', {'provider': 'other'}, policy)
    with pytest.raises(ServiceError, match='manifest_changed'):
        restarted.read_frames('owner', 'one', expected_manifest_sha256='b' * 64)
    with pytest.raises(ServiceError, match='not_found'):
        restarted.get('different-owner', 'one')


def test_policy_budget_and_mime_failures_are_retained_not_retried(tmp_path):
    service = VisualSamplingService(tmp_path / 'sampling')
    rows = [frame()]
    result = service.prepare('owner', 'budget', {}, {'max_frame_bytes': 1}, provider=provider(rows))
    assert result['status'] == 'failed' and result['error'] == 'visual_sampling_frame_budget_exceeded'
    assert result['stop_proven']
    again = service.prepare('owner', 'budget', {}, {'max_frame_bytes': 1},
        provider=lambda *args: pytest.fail('failed operation automatically retried'))
    assert again == result
    rows[0]['mime'] = 'image/jpeg'
    result = service.prepare('owner', 'mime', {}, {}, provider=provider(rows))
    assert result['status'] == 'failed' and result['error'] == 'visual_sampling_frame_mime_changed'


def test_provider_cannot_drop_or_reorder_explicit_requested_frame_ids(tmp_path):
    service = VisualSamplingService(tmp_path / 'sampling')
    result = service.prepare('owner', 'selection', {}, {'frame_ids': ['first', 'last']},
                             provider=provider([frame(name='last'), frame(name='first')]))
    assert result['status'] == 'failed' and result['error'] == 'visual_sampling_selection_changed'


def test_lazy_inventory_uses_common_exact_legacy_selection(tmp_path):
    service = VisualSamplingService(tmp_path / 'sampling')
    reads = []
    def resolve(owner, source, policy, check):
        def read(index, row):
            reads.append(index)
            return frame(timestamp=index / 24, name=str(index))
        return dict(source={'kind': 'native'}, inventory=[{'index': i} for i in range(120)], read_frame=read)
    result = service.prepare('owner', 'lazy', {}, {'strategy': 'method_uniform', 'max_frames': 4}, provider=resolve)
    assert result['status'] == 'completed'
    assert reads == [0, 40, 79, 119]
    assert [r['timestamp_seconds'] for r in result['frames']] == [i / 24 for i in reads]
    # Preserve the old short-clip limitation; do not falsely claim full span.
    assert uniform_indices(3, 12) == [0]
    assert select_indices('native_capture', 120, 3) == [0, 60, 119]


def test_concurrent_prepare_and_cancel_never_resolve_twice(tmp_path):
    root = tmp_path / 'sampling'
    first, second = VisualSamplingService(root), VisualSamplingService(root)
    ready, release = threading.Event(), threading.Event()
    result, calls = [], []
    def blocking(owner, source, policy, check):
        calls.append(1); ready.set(); assert release.wait(5); check()
        return dict(source={}, frames=[frame()])
    thread = threading.Thread(target=lambda: result.append(first.prepare('owner', 'one', {}, {}, provider=blocking)))
    thread.start(); assert ready.wait(5)
    assert second.prepare('owner', 'one', {}, {}, provider=lambda *args: pytest.fail('double resolver'))['status'] == 'running'
    assert second.cancel('owner', 'one')['stop_proven'] is False
    release.set(); thread.join(5); assert not thread.is_alive()
    assert result[0]['status'] == 'cancelled' and result[0]['stop_proven']
    assert len(calls) == 1


def test_contending_prepare_before_first_registration_never_fails_or_resolves_twice(tmp_path, monkeypatch):
    root = tmp_path / 'sampling'
    first, second = VisualSamplingService(root), VisualSamplingService(root)
    entered, release = threading.Event(), threading.Event()
    original = first._lock
    @contextmanager
    def before_registration(key):
        with original(key) as held:
            assert held
            entered.set()
            assert release.wait(5)
            yield held
    monkeypatch.setattr(first, '_lock', before_registration)
    calls, result = [], []
    thread = threading.Thread(target=lambda: result.append(first.prepare('owner', 'new', {}, {},
        provider=provider([frame()], calls), deadline_at=first.clock()+100)))
    thread.start()
    assert entered.wait(5)
    try:
        pending = second.prepare('owner', 'new', {}, {},
            provider=lambda *args: pytest.fail('second resolver ran'))
        assert pending['status'] == 'running' and pending['registration_pending']
        assert pending['stop_proven'] is False and pending['manifest_sha256'] is None
        assert 'request_sha256' not in pending and calls == []
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and result[0]['status'] == 'completed' and len(calls) == 1
    assert second.prepare('owner', 'new', {}, {}) == result[0]


def test_running_writer_disappears_and_is_not_replayed(tmp_path):
    root = tmp_path / 'sampling'
    script = ('import os\nfrom video_workbench.visual_sampling import VisualSamplingService\n'
        f's=VisualSamplingService({str(root)!r})\n'
        'def crash(*args): os._exit(17)\n'
        "s.prepare('owner','crash',{}, {}, provider=crash)\n")
    child = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=20)
    assert child.returncode == 17, child.stderr.decode(errors='replace')
    service = VisualSamplingService(root)
    assert service.get('owner', 'crash')['status'] == 'running'
    result = service.prepare('owner', 'crash', {}, {}, provider=lambda *args: pytest.fail('crash replay'))
    assert result['status'] == 'interrupted' and result['stop_proven']


def test_lock_setup_failure_releases_local_lock(tmp_path, monkeypatch):
    service = VisualSamplingService(tmp_path / 'sampling')
    original = service._ordinary_parents
    def unavailable(path):
        if path.parent == service.locks:
            raise ServiceError('visual_sampling_path_invalid', 409)
        return original(path)
    monkeypatch.setattr(service, '_ordinary_parents', unavailable)
    with pytest.raises(ServiceError, match='path_invalid'):
        prepare(service, [frame()])
    monkeypatch.setattr(service, '_ordinary_parents', original)
    assert prepare(service, [frame()])['status'] == 'completed'


def test_deadline_is_frozen_and_cooperatively_stops(tmp_path):
    now = [100.]
    service = VisualSamplingService(tmp_path / 'sampling', clock=lambda: now[0])
    def late(owner, source, policy, check):
        now[0] = 111.; check()
        pytest.fail('expired provider continued')
    result = service.prepare('owner', 'late', {}, {}, provider=late, deadline_at=110.)
    assert result['status'] == 'timed_out' and result['deadline_at'] == 110.
    with pytest.raises(ServiceError, match='operation_conflict'):
        service.prepare('owner', 'late', {}, {}, provider=late, deadline_at=200.)


def test_retained_blob_tampering_does_not_become_new_sampling(tmp_path):
    service = VisualSamplingService(tmp_path / 'sampling')
    result = prepare(service, [frame()])
    blob = service.blobs / result['frames'][0]['sha256']
    blob.write_bytes(b'changed')
    with pytest.raises(ServiceError, match='blob_changed'):
        service.read_frames('owner', 'one')


def delivery(rows):
    return dict(schema='agent-model-image-evidence.v1', evidence_scope='completed_upstream_transport',
                images=[dict(model_request_completed=True, model_request_sha256='c' * 64,
                    sha256=r['sha256'], media_type='image/png', byte_length=r['bytes'],
                    input_item_index=0, part_index=i) for i, r in enumerate(rows)])


def delivery_inputs():
    raw = image(); sha = hashlib.sha256(raw).hexdigest()
    frames = [dict(frame_id='first', file_name='first.png', sha256=sha),
              dict(frame_id='later', file_name='later.png', sha256=sha)]
    files = {'first.png': raw, 'later.png': raw}
    return frames, files, delivery([dict(sha256=sha, bytes=len(raw))] * 2)


def test_transport_requires_independent_parts_for_equal_images():
    frames, files, receipt = delivery_inputs()
    proof = verify_delivery(frames, files, receipt)
    assert proof['image_parts'] == 2 and proof['frame_ids'] == ['first', 'later']
    assert proof['model_understanding_verified'] is False
    assert proof['encoded_bytes_verified'] is True
    assert verify_delivery(frames, None, receipt)['encoded_bytes_verified'] is False
    receipt['images'].pop()
    with pytest.raises(ServiceError, match='delivery_incomplete'):
        verify_delivery(frames, files, receipt)


@pytest.mark.parametrize('change', ['position', 'mime', 'length', 'unfinished', 'split-request'])
def test_transport_does_not_relabel_or_double_count_receipts(change):
    frames, files, receipt = delivery_inputs()
    row = receipt['images'][1]
    if change == 'position': row['part_index'] = 0
    elif change == 'mime': row['media_type'] = 'image/jpeg'
    elif change == 'length': row['byte_length'] += 1
    elif change == 'unfinished': row['model_request_completed'] = False
    else: row['model_request_sha256'] = 'd' * 64
    with pytest.raises(ServiceError, match='delivery_'):
        verify_delivery(frames, files, receipt)


def test_real_legacy_decoder_keeps_exact_original_bundle_and_honest_clock(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Media decoder unavailable')
    media = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'color=c=blue:s=320x180:r=12:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-threads', '1', str(media)], capture_output=True, check=True, timeout=30)
    from video_workbench.animation_observation import prepare_sampled_media
    kwargs = dict(clip_id='same-fixture', goal='Observe the exact supplied samples.',
                  sample_fps=2, frame_limit=24, source_sha256='a' * 64,
                  review_profile='video.animation-review.v4')
    baseline = tmp_path / 'baseline'
    prepare_sampled_media(media, baseline, **kwargs)
    original = {p.relative_to(baseline).as_posix(): p.read_bytes() for p in baseline.rglob('*') if p.is_file()}
    service = VisualSamplingService(tmp_path / 'sampling')
    checksum = hashlib.sha256(media.read_bytes()).hexdigest()
    result = service.prepare('owner', 'formal', {'media_sha256': checksum, 'bytes': media.stat().st_size},
        {'strategy': 'formal_legacy', 'frame_limit': 24},
        provider=lambda *args: dict(source={'media_sha256': checksum}, media_path=media, legacy_bundle=kwargs))
    assert result['status'] == 'completed', result
    assert service.export_bundle('owner', 'formal') == original
    profile, doc, files = service.bundle('owner', 'formal')
    assert profile == 'video.animation-review.v4'
    assert doc == json.loads(original['inputs/animation-review-input.json'])
    assert files == {k[len('inputs/'):]: v for k, v in original.items() if k.startswith('inputs/')}
    assert service.bundle_receipt('owner', 'formal') == json.loads(original['evidence.json'])
    frames = service.read_frames('owner', 'formal')
    assert [f['timestamp_seconds'] for f in frames] == [0, .5, 1, 1.5]
    assert all(f['origin'] == 'decoded_media' and 'not_measured' in f['clock_scope'] for f in frames)


def test_real_temporal_decoder_preserves_measured_pts_aliases_and_original_bundle(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Media decoder unavailable')
    media = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'color=c=blue:s=320x180:r=12:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-threads', '1', str(media)], capture_output=True, check=True, timeout=30)
    from video_workbench.animation_observation import prepare_temporal_media
    kwargs = dict(clip_id='same-temporal', goal='Observe the same temporal evidence.',
                  sampling_layers=[dict(start_seconds=0, end_seconds=1, sample_fps=12)],
                  frame_limit=24, source_sha256='a' * 64,
                  review_profile='video.animation-review.v4')
    baseline = tmp_path / 'baseline'
    prepare_temporal_media(media, baseline, **kwargs)
    original = {p.relative_to(baseline).as_posix(): p.read_bytes() for p in baseline.rglob('*') if p.is_file()}
    service = VisualSamplingService(tmp_path / 'sampling')
    result = service.prepare('owner', 'temporal', {}, {'frame_limit': 24},
        provider=lambda *args: dict(source={'kind': 'temporal'}, media_path=media, legacy_bundle=kwargs))
    assert result['status'] == 'completed', result
    assert service.export_bundle('owner', 'temporal') == original
    frames = service.read_frames('owner', 'temporal')
    assert all(f['timestamp_seconds'] == f['pts'] * f['time_base'][0] / f['time_base'][1]
               and f['clock_scope'].startswith('measured_ffmpeg') for f in frames)
    plan = json.loads(original['sampling-plan.json'])
    assert len(frames) == len({row['frame_id'] for row in plan['requests']})
    assert service.bundle_receipt('owner', 'temporal')['requested_samples'] == 12
