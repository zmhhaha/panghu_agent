"""Synthetic native/capture records test v3 rejection; no live admission claim."""
import base64
import io
import json
from pathlib import Path
import tarfile

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.deploy.animation_production_acceptance import verify_renderer_capture
from test_animation_production_acceptance import renderer_capture, RENDER_IMAGE


@pytest.fixture
def engine_capture(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'tests/video_workbench'))
    from test_creative_engine_renderer import engine_scene, RUNTIME_V2
    scene = engine_scene('three')
    package = scene['creative']['package']
    root = renderer_capture(tmp_path / 'engine-renderer', package, scene['audio']['data'], RUNTIME_V2)
    return root, package


def rebuild_log(root):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as stream:
        for path in sorted((root / 'artifacts').rglob('*')):
            if path.is_file():
                raw = path.read_bytes()
                item = tarfile.TarInfo(path.relative_to(root / 'artifacts').as_posix())
                item.size = len(raw)
                stream.addfile(item, io.BytesIO(raw))
    (root / 'pod.log').write_bytes(b'VIDEO_MOTION_ARTIFACT_BASE64=' + base64.b64encode(archive.getvalue()) + b'\n')


def test_v3_original_capture_has_distinct_native_context_scope(engine_capture):
    root, package = engine_capture
    proof = verify_renderer_capture(root, package, renderer_image=RENDER_IMAGE)
    assert proof['schema'] == 'video.animation-production-captured-render-proof.v2'
    assert 'native_context' in proof['scope']
    assert proof['technical_review']['engine'] == 'three'
    assert proof['technical_review']['native_context_verified'] is True
    assert proof['technical_review']['idle_clock_verified'] is True
    assert proof['technical_review']['aesthetic_approval'] is False
    assert proof['audio_clock']['sha256'] == package['input_document']['scene']['audio_sha256']
    assert proof['audio_clock']['duration_seconds'] == package['input_document']['constraints']['duration_seconds']
    assert proof['subjective_quality_approved'] is False


@pytest.mark.parametrize('change', ['old-capture', 'missing-native', 'hardware', 'lost', 'gl-error', 'missing-module'])
def test_v3_admission_refuses_rehashed_inadequate_engine_evidence(engine_capture, change):
    root, package = engine_capture
    name = 'render-receipt.json' if change == 'old-capture' else 'observations.json'
    path = root / 'artifacts' / name
    value = json.loads(path.read_bytes())
    if change == 'old-capture': value['creative_capture']['schema'] = 'video.creative-capture.v1'
    elif change == 'missing-native': value['samples'][0]['engine_observation']['contexts'] = []
    elif change == 'hardware': value['samples'][0]['engine_observation']['contexts'][0].update(renderer='Hardware GPU', software=False)
    elif change == 'lost': value['samples'][0]['engine_observation']['contexts'][0]['context_lost'] = True
    elif change == 'gl-error': value['samples'][0]['engine_observation']['contexts'][0]['gl_error'] = 1280
    else: value['loaded_engine_assets'] = []
    path.write_bytes(canonical_bytes(value))
    rebuild_log(root)
    with pytest.raises(ServiceError):
        verify_renderer_capture(root, package, renderer_image=RENDER_IMAGE)
