"""Five real sampling adapters retain one PNG through one shared authority.

The HTTP hop and sampling store are real. Native custody uses inert retained
capture fixtures, so this proves adapter/byte/clock/role integrity, not browser
execution, model understanding, visual quality, audio or user acceptance.
"""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from threading import Thread
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.ability_sampling import prepare_ability_sampling, read_ability_sampling
from video_workbench.animation_creation_http import CreationAPIClient, CreationHTTPServer
from video_workbench.animation_method_renderer import MethodAnimationRenderer
from video_workbench.native_sampling import prepare_input_frames, read_input_frames
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.scene_space_capture import SAMPLE_CAPTURE_POLICY, validate_frame_capture
from video_workbench.scene_space_renderer import SceneSpaceRenderer
from video_workbench.visual_sampling import VisualSamplingService, verify_delivery
from video_workbench.visual_sampling_http import FormalSamplingTransport, VisualSamplingClient


OWNER = 'matrix-owner'
TOKEN = 'unit-matrix-private-caller-' + 'a'*40


@contextmanager
def private_client(sampler, root):
    transport = FormalSamplingTransport(root, sampler)
    idle = SimpleNamespace(pending=lambda limit=100: [])
    server = CreationHTTPServer(idle, TOKEN, sampling_transport=transport)
    worker = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    worker.start()
    try:
        yield VisualSamplingClient(CreationAPIClient('http://127.0.0.1:' + str(server.server_port), TOKEN))
    finally:
        server.shutdown(); server.server_close(); worker.join(5)
        assert not worker.is_alive()


def wait_complete(client, operation):
    until = time.monotonic()+5
    state = client.get(OWNER, operation)
    while state['status'] in {'queued', 'running'} and time.monotonic() < until:
        time.sleep(.01)
        state = client.get(OWNER, operation)
    assert state['status'] == 'completed', state
    return state


def retained_scene(sampler, raw):
    """Fixture an already checked capture; use the actual native adapter."""
    owner = MethodAnimationRenderer.__new__(MethodAnimationRenderer)
    owner.sampling_service = sampler
    renderer = SceneSpaceRenderer.__new__(SceneSpaceRenderer)
    renderer.owner = owner
    frame_range = dict(start_frame=24, end_frame=73)
    sha = hashlib.sha256(raw).hexdigest()
    rows = []
    for absolute in (24, 48, 72):
        timestamp = (absolute-24)/24
        capture = dict(schema='video.scene-space-frame-capture.v1', absolute_frame=absolute,
            relative_frame=absolute-24, format='png', quality=None, sha256=sha, bytes=len(raw))
        validate_frame_capture(capture, frame_range=frame_range, raw=raw, timestamp_seconds=timestamp)
        rows.append(dict(raw=raw, mime='image/png', timestamp_seconds=timestamp, capture=capture))
    renderer._load = lambda operation: dict(scene_space=dict(frame_range=frame_range),
        request=dict(static=False, sample_capture=deepcopy(SAMPLE_CAPTURE_POLICY)))
    renderer.lookup = lambda operation: dict(technical_passed=True, stop_proven=True,
        source_sha256='b'*64, scene_space=dict(plan_sha256='c'*64), scene_space_sha256='d'*64,
        evidence_sha256='e'*64, runtime=dict(scope='retained_test_pixels_not_native_browser_execution'))
    renderer.frames = lambda operation, max_frames=12: deepcopy(rows[:max_frames])
    return renderer


def test_five_adapters_preserve_same_png_roles_and_distinct_observations(tmp_path, record_property, monkeypatch):
    stream = io.BytesIO()
    Image.new('RGB', (32, 18), (30, 50, 70)).save(stream, format='PNG')
    raw = stream.getvalue(); sha = hashlib.sha256(raw).hexdigest()
    sampler = VisualSamplingService(tmp_path/'core')
    matrix = []

    def record(name, manifest, expected_ids, expected_times, expected_roles):
        rows = sampler.read_frames(OWNER, manifest['operation_id'])
        assert manifest['status'] == 'completed' and manifest['usage']['model_calls'] == 0
        assert [row['frame_id'] for row in rows] == expected_ids
        assert [row['timestamp_seconds'] for row in rows] == expected_times
        assert [row.get('role') for row in rows] == expected_roles
        assert all(row['raw'] == raw and row['mime'] == 'image/png' for row in rows)
        assert [frame['sha256'] for frame in manifest['frames']] == [sha]*len(expected_ids)
        assert len({frame['decoded_rgba_sha256'] for frame in manifest['frames']}) == 1
        # Every observation needs a separate image part, including identical
        # encoded bytes at another time or under another role.
        expected = [dict(frame_id=row['frame_id'], file_name=str(index)+'.png', sha256=sha)
                    for index,row in enumerate(rows)]
        files = {frame['file_name']: raw for frame in expected}
        receipt = dict(evidence_scope='completed_upstream_transport', images=[dict(
            sha256=sha, media_type='image/png', byte_length=len(raw), model_request_sha256='f'*64,
            model_request_completed=True, input_item_index=0, part_index=index)
            for index in range(len(expected))])
        assert verify_delivery(expected, files, receipt)['image_parts'] == len(rows)
        receipt['images'].pop()
        with pytest.raises(ServiceError, match='visual_sampling_delivery_incomplete'):
            verify_delivery(expected, files, receipt)
        matrix.append(dict(entry=name, operation_id=manifest['operation_id'],
            provider=manifest['source_ref']['provider'], manifest_sha256=manifest['manifest_sha256'],
            frame_ids=expected_ids, timestamps=expected_times, roles=expected_roles,
            observations=len(rows), encoded_sha256=sha, mime='image/png', bytes=len(raw),
            model_calls=manifest['usage']['model_calls']))

    frames = [dict(frame_id=role, file_name=role+'.png', purpose=role, clip_time=index,
        sha256=sha) for index,role in enumerate(('reference', 'baseline', 'output'))]
    document = dict(observation=dict(frames=frames), criteria=['Preserve the existing criterion.'])
    original = deepcopy(document)
    files = {frame['file_name']: raw for frame in frames}
    reference, copied = prepare_input_frames(sampler, OWNER, 'native-input', document, files)
    assert copied == files and document == original
    record('native_input', sampler.get(OWNER, reference['operation_id']),
        ['reference', 'baseline', 'output'], [0, 1, 2], ['reference', 'baseline', 'output'])

    with private_client(sampler, tmp_path/'private-transfer') as remote:
        transfer_frames = [dict(raw=raw, mime='image/png', timestamp_seconds=index,
            frame_id=role, role=role) for index,role in enumerate(('reference', 'baseline', 'output'))]
        remote.prepare_frames(OWNER, 'private-retained', dict(owner=OWNER, render_id='retained-fixture'),
            dict(strategy='provided', max_frames=3), transfer_frames, deadline_at=time.time()+300)
        wait_complete(remote, 'private-retained')
        record('private_retained_http', remote.manifest(OWNER, 'private-retained'),
            ['reference', 'baseline', 'output'], [0, 1, 2], ['reference', 'baseline', 'output'])

        ability_root = tmp_path/'ability'; ability_root.mkdir()
        run = dict(identity='1'*64, runtime_identity='2'*64, semantic_input_sha256='3'*64)
        technical = dict(status='passed', scope='inert_retained_capture_fixture')
        ability_frames = [dict(id='ability-'+str(index), variant_id='baseline' if index == 0 else 'candidate',
            clip_id='clip', time_seconds=index, sha256=sha, mime_type='image/png', width=32, height=18)
            for index in range(3)]
        images = {frame['id']: raw for frame in ability_frames}
        ability_ref, retained = prepare_ability_sampling(ability_root, run, technical,
            ability_frames, images, remote, OWNER)
        assert retained == images
        record('ability_capture', remote.manifest(OWNER, ability_ref['operation_id']),
            ['ability-0', 'ability-1', 'ability-2'], [0, 1, 2], ['baseline', 'output', 'output'])

        picture = PictureLearningService(tmp_path/'picture', author_gateway=None, review_gateway=None,
            renderer=SimpleNamespace(sampling_service=sampler))
        uploaded = picture.upload_reference(OWNER, dict(request_id='picture-upload', file_name='same.png',
            mime_type='image/png', content_base64=base64.b64encode(raw).decode('ascii')))
        stored = picture.store.read('references', OWNER, uploaded['id'])
        picture_ref = stored['sampling_ref']
        record('picture_original_and_model_copy', sampler.get(OWNER, picture_ref['operation_id']),
            ['original', 'model-copy'], [0, 0], [None, None])

        native = retained_scene(sampler, raw)
        native_ref = native.prepare_sampling(OWNER, 'scene-native', max_frames=3)
        record('scene_native_png', sampler.get(OWNER, native_ref['operation_id']),
            ['native-24', 'native-48', 'native-72'], [0, 1, 2], [None, None, None])

        # Reads across local and HTTP consumers cannot prepare more evidence.
        monkeypatch.setattr(sampler, 'prepare', lambda *args, **kwargs: pytest.fail('read prepared sampling'))
        assert read_input_frames(sampler, OWNER, reference, document, files) == files
        assert remote.read_frames(OWNER, 'private-retained') == transfer_frames
        assert read_ability_sampling(ability_root, ability_frames, images, remote, OWNER,
            run=run, technical=technical) == images
        assert all(row['raw'] == raw for row in native.read_sampling(OWNER, native_ref))

    assert len(matrix) == 5 and sum(row['observations'] for row in matrix) == 14
    blobs = list(sampler.blobs.iterdir())
    assert len(blobs) == 1 and blobs[0].read_bytes() == raw
    with sampler._db(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM samples').fetchone()[0] == 5
    (tmp_path/'unified-sampling-matrix.json').write_bytes(canonical_bytes(matrix))
    record_property('unified_sampling_matrix', json.dumps(matrix, ensure_ascii=False))
