"""Pure compiler/dependency contracts; actual WebGL is checked by browser capture."""
from copy import deepcopy
import json

import pytest

from video_workbench import animation_lab_three as three
from video_workbench.motion import estimated_alignment, make_beats, sha256
from video_workbench.animation_lab_webgl import export_assets as exporter


def inputs():
    beats = make_beats('服务首先整体展示。然后逐层拆开。观察交互层和业务层。最后组合成完整服务。')
    return three.example_three_plan(), beats, estimated_alignment(beats, 'a'*64, 18)


def test_real_three_project_is_local_seekable_and_deterministic():
    args = inputs()
    files = three.compile_three_project(*args)
    assert files == three.compile_three_project(*deepcopy(args))
    assert set(files) == {'index.html', 'plan.json', 'beats.json', 'alignment.json', 'timeline.json', 'manifest.json'}
    page = files['index.html'].decode()
    assert "import * as THREE from './assets/three.module.min.js'" in page
    assert 'new THREE.WebGLRenderer' in page and 'new THREE.BoxGeometry' in page
    assert "window.__timelines['video-motion']=timeline" in page and 'preserveDrawingBuffer:true' in page
    assert 'gl.readPixels' in page and 'camera.matrixWorld.toArray()' in page
    assert 'requestAnimationFrame' not in page and 'Math.random' not in page and 'https://' not in page
    assert args[0] == three.example_three_plan()


def test_variants_keep_content_and_audio_but_declare_only_real_actions():
    baseline = three.compile_three_project(*inputs(), variant='baseline')
    candidate = three.compile_three_project(*inputs(), variant='candidate')
    assert all(baseline[name] == candidate[name] for name in ('plan.json', 'beats.json', 'alignment.json'))
    for files, expected in [(baseline, ['layers', 'layers']), (candidate, ['layers', 'camera', 'layers'])]:
        timeline = json.loads(files['timeline.json'])
        assert [row['target'] for row in timeline['actions']] == expected
        assert [row['ordinal'] for row in timeline['actions']] == list(range(len(expected)))
        assert timeline['audio_sha256'] == 'a'*64 and timeline['duration_seconds'] == 18
        for row in timeline['actions']:
            assert 0 <= row['start'] < row['start']+row['duration'] <= 18
        manifest = json.loads(files['manifest.json'])
        assert manifest['engine_assets'] == three.THREE_ASSET_SHA256
        assert manifest['scope'] == 'developer_sample_not_production_admitted'
        for name, digest in manifest['files'].items():
            assert sha256(files[name]) == digest
    assert baseline['index.html'] != candidate['index.html']


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(schema='video.motion-plan.v1'),
    lambda p: p.update(extra='free-form JavaScript'),
    lambda p: p.update(title='x'*51),
    lambda p: p.update(title='标题'+' '*100),
    lambda p: p.update(takeaway=''),
    lambda p: p.update(layers=p['layers'][:2]),
    lambda p: p['layers'][1].update(id=p['layers'][0]['id']),
    lambda p: p['layers'][0].update(color='red; background:url(https://example.invalid)'),
    lambda p: p['layers'][0].update(label='\0'),
    lambda p: p['layers'][0].update(detail='x'*49),
])
def test_unbounded_or_malformed_scene_plans_are_rejected(mutate):
    plan, beats, alignment = inputs()
    mutate(plan)
    with pytest.raises(ValueError):
        three.compile_three_project(plan, beats, alignment)


def test_text_cannot_escape_html_or_script_or_replace_internal_placeholders():
    plan, beats, alignment = inputs()
    plan['title'] = '__ENGINE__ <title> & test'
    plan['layers'][0]['label'] = '</script><img>'
    files = three.compile_three_project(plan, beats, alignment)
    page = files['index.html'].decode()
    assert '<h1>__ENGINE__ &lt;title&gt; &amp; test</h1>' in page
    assert '&lt;/script&gt;&lt;img&gt;' in page
    assert '\\u003c/script\\u003e\\u003cimg\\u003e' in page
    assert '<img>' not in page


def test_asset_manifest_is_read_at_compile_time(monkeypatch):
    monkeypatch.setattr(three, 'THREE_ASSET_SHA256', {'three.module.min.js': 'b'*64})
    manifest = json.loads(three.compile_three_project(*inputs())['manifest.json'])
    assert manifest['engine_assets'] == {'three.module.min.js': 'b'*64}


@pytest.mark.parametrize('seconds', [1, 7.9, 60.1])
def test_out_of_budget_scene_duration_rejected(seconds):
    plan, beats, _ = inputs()
    alignment = estimated_alignment(beats, 'a'*64, seconds)
    with pytest.raises(ValueError, match='duration_invalid'):
        three.compile_three_project(plan, beats, alignment)


def test_variant_is_a_finite_presentation_choice():
    with pytest.raises(ValueError, match='variant_invalid'):
        three.compile_three_project(*inputs(), variant='arbitrary-camera-code')


def _synthetic_install(tmp_path, monkeypatch):
    """Synthetic byte fixture tests transfer checks, never proves real Three.js."""
    package = tmp_path/'package'
    (package/'build').mkdir(parents=True)
    raw = b'synthetic transfer contract fixture, not a Three.js runtime'
    (package/'build'/'three.module.min.js').write_bytes(raw)
    (package/'package.json').write_text(json.dumps({'version': '0.170.0'}))
    (package/'LICENSE').write_bytes(b'MIT License\nSynthetic unit fixture')
    monkeypatch.setattr(exporter, 'THREE_ASSET_SHA256', {'three.module.min.js': sha256(raw)})
    destination = tmp_path/'assets'
    destination.mkdir()
    return package, destination, raw


def test_asset_export_checks_bytes_and_preserves_existing_matching_resources(tmp_path, monkeypatch):
    package, destination, raw = _synthetic_install(tmp_path, monkeypatch)
    receipt = exporter.export_assets(destination, installation=package)
    assert (destination/'three.module.min.js').read_bytes() == raw
    assert receipt['network_calls'] == 0 and receipt['version'] == '0.170.0'
    assert exporter.export_assets(destination, installation=package) == receipt


def test_modified_dependency_never_reaches_asset_directory(tmp_path, monkeypatch):
    package, destination, _ = _synthetic_install(tmp_path, monkeypatch)
    (package/'build'/'three.module.min.js').write_bytes(b'modified runtime')
    with pytest.raises(ValueError, match='digest_mismatch'):
        exporter.export_assets(destination, installation=package)
    assert list(destination.iterdir()) == []


def test_export_refuses_to_overwrite_a_different_existing_asset(tmp_path, monkeypatch):
    package, destination, _ = _synthetic_install(tmp_path, monkeypatch)
    (destination/'three.module.min.js').write_bytes(b'user existing bytes')
    with pytest.raises(ValueError, match='destination_conflict'):
        exporter.export_assets(destination, installation=package)
    assert (destination/'three.module.min.js').read_bytes() == b'user existing bytes'
    assert not (destination/'three-assets.json').exists()
