"""Native PNG transport follows only the already admitted v2 profile budget."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import struct
from uuid import uuid4
import zlib

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.profiles import video_picture_review_v2 as protocol
from video_workbench.animation_observation import MAX_FRAME_TOTAL_BYTES
from video_workbench.animation_review_gateway import AnimationObserveGateway, AnimationReviewGateway
from video_workbench.picture_learning_gateway import ClassifiedPictureReviewGateway, PictureReviewGateway
from video_workbench.scene_space_production import SceneSpaceClassifiedReviewGateway
from test_classified_review_gateway import document
from test_animation_review_gateway import visual_document


def png_bytes(size):
    stream = io.BytesIO()
    Image.new('RGB', (1280, 720), (30, 45, 60)).save(stream, format='PNG')
    raw = stream.getvalue()
    payload = b'fixture\0' + b'x' * (size - len(raw) - 20)
    kind = b'tEXt'
    chunk = struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    raw = raw[:-12] + chunk + raw[-12:]
    assert len(raw) == size
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (1280, 720)
        image.verify()
    return raw


def inputs(gateway, raw, count=4):
    if isinstance(gateway, (AnimationObserveGateway, AnimationReviewGateway)):
        value = visual_document(review=isinstance(gateway, AnimationReviewGateway))
        frames = [dict(frame_id='frame-' + str(index), file_name='frame-' + str(index) + '.png',
            time_seconds=index / 10, sha256=hashlib.sha256(raw).hexdigest()) for index in range(count)]
    else:
        value = document()
        frames = [dict(frame_id='native-' + str(index), file_name='native-' + str(index) + '.png',
            purpose='output', sha256=hashlib.sha256(raw).hexdigest()) for index in range(count)]
    value['observation']['frames'] = frames
    files = {gateway.input_name: canonical_bytes(value), **{frame['file_name']: raw for frame in frames}}
    return value, files


class UploadOnly:
    def __init__(self):
        self.files = []

    def upload_input(self, name, raw):
        self.files.append((name, raw))
        return dict(input_id=str(uuid4()), name=name, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))

    def submit(self, *_args, **_kwargs):
        raise AssertionError('No paid request is authorized by a transport test')


@pytest.mark.parametrize('gateway_type', [ClassifiedPictureReviewGateway, SceneSpaceClassifiedReviewGateway])
def test_four_native_pngs_above_retained_native_lane_reject_before_any_upload(gateway_type):
    raw = png_bytes(838118)
    assert 4 * len(raw) == 3352472 > MAX_FRAME_TOTAL_BYTES
    peer = UploadOnly()
    gateway = gateway_type(peer)
    value, files = inputs(gateway, raw)
    with pytest.raises(ServiceError, match='picture_review_native_wire_too_large') as failure:
        gateway.upload(value, files)
    assert failure.value.status == 413 and peer.files == []
    limits = gateway._frame_input_limits()
    assert (limits.max_input_files, limits.max_input_bytes, limits.max_input_total_bytes) == (9, 2 * 1024**2, 18 * 1024**2)


@pytest.mark.parametrize('gateway_type', [PictureReviewGateway, AnimationObserveGateway, AnimationReviewGateway])
def test_legacy_roles_still_reject_four_native_pngs_before_any_upload(gateway_type):
    gateway = gateway_type(UploadOnly())
    value, files = inputs(gateway, png_bytes(838118))
    with pytest.raises(ServiceError, match='animation_frame_inputs_too_large') as failure:
        gateway.upload(value, files)
    assert failure.value.status == 413 and gateway.client.files == []


@pytest.mark.parametrize('change', ['revision', 'profile'])
def test_unadmitted_profile_or_revision_does_not_gain_v2_transport_budget(change):
    gateway = ClassifiedPictureReviewGateway(UploadOnly(), 'future' if change == 'revision' else '1')
    if change == 'profile':
        gateway.profile = 'video.picture-learning-review.v1'
    value, files = inputs(gateway, png_bytes(838118))
    with pytest.raises(ServiceError, match='animation_frame_inputs_too_large'):
        gateway.upload(value, files)
    assert gateway.client.files == []


def test_eight_pngs_at_profile_file_boundary_still_obey_independent_native_event_limit():
    gateway = ClassifiedPictureReviewGateway(UploadOnly())
    value, files = inputs(gateway, png_bytes(2 * 1024**2), count=8)
    with pytest.raises(ServiceError, match='picture_review_native_wire_too_large'):
        gateway.upload(value, files)
    assert gateway.client.files == []
    assert sum(len(raw) for raw in files.values()) <= 18 * 1024**2


@pytest.mark.parametrize('gateway_type', [ClassifiedPictureReviewGateway, SceneSpaceClassifiedReviewGateway])
def test_three_native_pngs_fit_native_lane_and_upload_original_bytes_without_job(gateway_type):
    raw=png_bytes(838118);gateway=gateway_type(UploadOnly())
    value,files=inputs(gateway,raw,count=3)
    refs=gateway.upload(value,files)
    assert len(refs)==len(gateway.client.files)==4 and dict(gateway.client.files)==files
    assert all(uploaded is raw for name,uploaded in gateway.client.files if name.endswith('.png'))


@pytest.mark.parametrize('change', ['oversized_file', 'ninth_frame', 'wrong_sha', 'missing_file', 'extra_file', 'changed_document'])
def test_v2_budget_never_bypasses_count_byte_integrity_or_exact_file_manifest(change):
    gateway = ClassifiedPictureReviewGateway(UploadOnly())
    value, files = inputs(gateway, png_bytes(2 * 1024**2 + 1 if change == 'oversized_file' else 838118), count=9 if change == 'ninth_frame' else 4)
    if change == 'wrong_sha':
        name = value['observation']['frames'][0]['file_name']
        files[name] = files[name][:-1] + bytes([files[name][-1] ^ 1])
    elif change == 'missing_file':
        files.pop(value['observation']['frames'][0]['file_name'])
    elif change == 'extra_file':
        files['unlisted.png'] = next(raw for name, raw in files.items() if name.endswith('.png'))
    elif change == 'changed_document':
        files[gateway.input_name] += b'\n'
    with pytest.raises(ServiceError):
        gateway.upload(value, files)
    assert gateway.client.files == []


def test_native_total_budget_counts_the_canonical_task_json(monkeypatch):
    gateway = ClassifiedPictureReviewGateway(UploadOnly())
    value, files = inputs(gateway, png_bytes(838118))
    limits = gateway._frame_input_limits()
    frame_bytes = sum(len(raw) for name, raw in files.items() if name.endswith('.png'))
    # A synthetic stricter profile proves the shared transport includes JSON;
    # no production contract, route, binding or provider limit changes here.
    monkeypatch.setattr(protocol.VideoPictureReviewV2Profile, 'limits', replace(limits,
        max_input_total_bytes=frame_bytes))
    with pytest.raises(ServiceError, match='animation_frame_inputs_too_large'):
        gateway.upload(value, files)
    assert gateway.client.files == []
