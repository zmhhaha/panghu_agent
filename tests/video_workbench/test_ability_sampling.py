"""Real shared image custody for explicitly synthetic capability captures.

No browser render, model request or production source is used here.
"""
from copy import deepcopy
import hashlib
import io
import json
import time

import pytest
from PIL import Image

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench import ability_sampling
from video_workbench.ability_sampling import (INTENT_NAME, REFERENCE_NAME,
    prepare_ability_sampling, read_ability_sampling)
from video_workbench.visual_sampling import VisualSamplingService


class LocalClient:
    """Exercise the actual persistent core without an HTTP or model stub."""
    def __init__(self, root, *, clock=time.time):
        self.service = VisualSamplingService(root, clock=clock)
        self.prepares, self.resolves, self.reads, self.cancellations = [], [], [], []

    def prepare_frames(self, owner, operation, source, policy, rows, *, deadline_at):
        self.prepares.append((owner, operation))
        def resolve(*args):
            self.resolves.append((owner, operation))
            return dict(source=deepcopy(source), frames=deepcopy(rows))
        return self.service.prepare(owner, operation, source, policy, provider=resolve,
                                    deadline_at=deadline_at)

    def get(self, owner, operation):
        self.reads.append(('manifest', owner, operation))
        return self.service.get(owner, operation)

    read_manifest = get

    def read_frames(self, owner, operation):
        self.reads.append(('frames', owner, operation))
        return self.service.read_frames(owner, operation)

    def cancel(self, owner, operation):
        self.cancellations.append((owner, operation))
        return self.service.cancel(owner, operation)


def fixture(tmp_path, count=2, *, clock=time.time):
    root = tmp_path / 'run'
    root.mkdir()
    run = dict(identity='a'*64, runtime_identity='b'*64, semantic_input_sha256='c'*64)
    technical = dict(status='passed', scope='synthetic_test_only')
    image = io.BytesIO()
    Image.new('RGB', (32, 18), (25, 50, 75)).save(image, format='PNG')
    raw = image.getvalue()
    frames = [dict(id=f'{"baseline" if i % 2 == 0 else "candidate"}:scene:sample-{i}',
        variant_id='baseline' if i % 2 == 0 else 'candidate', clip_id='scene',
        time_seconds=i / 12, sha256=hashlib.sha256(raw).hexdigest(),
        mime_type='image/png', width=32, height=18) for i in range(count)]
    images = {frame['id']: raw for frame in frames}
    client = LocalClient(tmp_path / 'sampling', clock=clock)
    return root, run, technical, frames, images, client


def collect(parts, **kwargs):
    root, run, technical, frames, images, client = parts
    return prepare_ability_sampling(root, run, technical, frames, images, client, 'owner', **kwargs)


def read(parts, **kwargs):
    root, run, technical, frames, images, client = parts
    return read_ability_sampling(root, frames, images, client, 'owner', run=run,
                                 technical=technical, **kwargs)


def test_all_original_states_and_identical_pixels_at_distinct_times_are_preserved(tmp_path):
    parts = fixture(tmp_path, 30)
    root, run, technical, frames, images, client = parts
    reference, actual = collect(parts)
    assert actual == images and len(actual) == 30
    rows = client.read_frames('owner', reference['operation_id'])
    assert [row['frame_id'] for row in rows] == [frame['id'] for frame in frames]
    assert [row['timestamp_seconds'] for row in rows] == [frame['time_seconds'] for frame in frames]
    assert [row['metadata']['observation'] for row in rows] == frames
    assert [row['role'] for row in rows] == ['baseline', 'output'] * 15
    manifest = client.get('owner', reference['operation_id'])
    assert manifest['usage']['frames'] == 30 and manifest['usage']['model_calls'] == 0
    assert len({row['sha256'] for row in manifest['frames']}) == 1
    assert manifest['source_ref']['origin'] == 'client_retained_capture'
    assert 'not_executor_native' in manifest['source_ref']['provenance_scope']
    assert 'not_continuous_video_or_audio' in reference['scope']
    assert (root / INTENT_NAME).is_file() and (root / REFERENCE_NAME).is_file()


def test_read_and_completed_collect_reuse_exact_evidence_without_dispatch(tmp_path):
    parts = fixture(tmp_path)
    root, _, _, _, images, client = parts
    reference, _ = collect(parts)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert read(parts) == images
    assert collect(parts, clock=lambda: time.time()+7200) == (reference, images)
    assert len(client.prepares) == len(client.resolves) == 1
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_remote_completion_recovers_missing_local_reference_after_deadline_without_prepare_or_cancel(
        tmp_path, monkeypatch):
    now = [100.]
    parts = fixture(tmp_path, clock=lambda: now[0])
    original = ability_sampling._save
    def interrupted(path, value):
        if path.name == REFERENCE_NAME:
            raise KeyboardInterrupt('synthetic termination after remote completion')
        return original(path, value)
    monkeypatch.setattr(ability_sampling, '_save', interrupted)
    with pytest.raises(KeyboardInterrupt):
        collect(parts, deadline_at=101., clock=lambda: now[0])
    assert not (parts[0]/REFERENCE_NAME).exists()
    assert len(parts[5].prepares) == len(parts[5].resolves) == 1
    monkeypatch.setattr(ability_sampling, '_save', original)
    now[0] = 102.
    reference, images = collect(parts, clock=lambda: now[0])
    assert images == parts[4] and (parts[0]/REFERENCE_NAME).exists()
    assert parts[5].get('owner', reference['operation_id'])['status'] == 'completed'
    assert len(parts[5].prepares) == len(parts[5].resolves) == 1
    assert parts[5].cancellations == []


@pytest.mark.parametrize('change', ['owner', 'selection', 'manifest', 'operation'])
def test_reference_tampering_fails_without_recollecting(tmp_path, change):
    parts = fixture(tmp_path)
    root, _, _, _, _, client = parts
    collect(parts)
    path = root / REFERENCE_NAME
    reference = json.loads(path.read_bytes())
    if change == 'owner': reference['owner'] = 'another-owner'
    elif change == 'selection': reference['selected_frame_ids'].reverse()
    elif change == 'manifest': reference['manifest_sha256'] = 'd'*64
    else: reference['operation_id'] = 'nonexistent-operation'
    path.write_bytes(canonical_bytes(reference))
    with pytest.raises(ServiceError):
        read(parts)
    assert len(client.prepares) == len(client.resolves) == 1


def test_same_pixels_from_another_run_cannot_replace_the_bound_source(tmp_path):
    first_dir, second_dir = tmp_path/'first', tmp_path/'second'
    first_dir.mkdir(); second_dir.mkdir()
    first = fixture(first_dir)
    second = list(fixture(second_dir))
    second[1]['identity'] = 'e'*64
    second[5] = first[5]  # Same real owner/service and identical selected images.
    collect(first); collect(second)
    assert first[4] == second[4]
    for name in (INTENT_NAME, REFERENCE_NAME):
        (first[0]/name).write_bytes((second[0]/name).read_bytes())
    with pytest.raises(ServiceError, match='ability_sampling_evidence_changed'):
        read(first)
    assert len(first[5].prepares) == len(first[5].resolves) == 2


@pytest.mark.parametrize('change', ['source', 'policy', 'deadline', 'rows'])
def test_changed_shared_response_cannot_rebind_or_relabel_evidence(tmp_path, monkeypatch, change):
    parts = fixture(tmp_path)
    *_, client = parts
    collect(parts)
    original = client.read_frames if change == 'rows' else client.read_manifest
    def changed(*args):
        value = deepcopy(original(*args))
        if change == 'source': value['source_ref']['run_identity'] = 'f'*64
        elif change == 'policy': value['policy']['selection'].reverse()
        elif change == 'deadline': value['deadline_at'] += 1
        else: value[0]['timestamp_seconds'] += 1
        return value
    monkeypatch.setattr(client, 'read_frames' if change == 'rows' else 'read_manifest', changed)
    with pytest.raises(ServiceError, match='ability_sampling_evidence_changed'):
        read(parts)
    assert len(client.prepares) == len(client.resolves) == 1


def test_bad_image_failure_is_durable_and_does_not_automatically_rerun(tmp_path):
    parts = fixture(tmp_path)
    root, _, _, frames, images, client = parts
    raw = b'\x89PNG\r\n\x1a\ninvalid-truncated-image'
    for frame in frames:
        images[frame['id']] = raw
        frame['sha256'] = hashlib.sha256(raw).hexdigest()
    for _ in range(2):
        with pytest.raises(ServiceError, match='ability_sampling_not_completed'):
            collect(parts)
    assert len(client.prepares) == len(client.resolves) == 1
    assert (root/INTENT_NAME).exists() and not (root/REFERENCE_NAME).exists()
    operation = client.prepares[0][1]
    assert client.get('owner', operation)['error'] == 'visual_sampling_image_invalid'


def test_missing_reference_read_cannot_create_sampling(tmp_path):
    parts = fixture(tmp_path)
    with pytest.raises(ServiceError, match='ability_sampling_evidence_changed'):
        read(parts)
    assert parts[5].prepares == parts[5].reads == []


def test_pending_sampler_deadline_cancels_once_and_keeps_frozen_intent(tmp_path):
    now = [100.]
    parts = list(fixture(tmp_path, clock=lambda: now[0]))
    class PendingClient:
        calls, cancelled = [], []
        def get(self, owner, operation):
            raise ServiceError('visual_sampling_not_found', 404)
        def prepare_frames(self, owner, operation, source, policy, rows, *, deadline_at):
            self.calls.append((owner, operation, deadline_at))
            now[0] = deadline_at
            return {'status': 'running'}
        def cancel(self, owner, operation): self.cancelled.append((owner, operation))
    parts[5] = client = PendingClient()
    with pytest.raises(ServiceError, match='ability_sampling_deadline_expired'):
        collect(parts, deadline_at=101., clock=lambda: now[0], pause=lambda seconds: None)
    with pytest.raises(ServiceError, match='ability_sampling_evidence_changed'):
        collect(parts, deadline_at=102., clock=lambda: now[0])
    assert len(client.calls) == len(client.cancelled) == 1
    assert json.loads((parts[0]/INTENT_NAME).read_bytes())['deadline_at'] == 101.
    assert not (parts[0]/REFERENCE_NAME).exists()
