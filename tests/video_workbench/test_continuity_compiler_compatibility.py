"""Production admission and strict observation dispatch preserve both revisions."""
import base64
import hashlib
import json

import pytest

from video_workbench.continuity import build_continuity_plan, compile_continuity_project, resolve_continuity
from video_workbench.motion import sha256
from video_workbench.motion_render_k8s import production_resources
from video_workbench.motion_renderer.job import freeze_input
from video_workbench.motion_review import verify_observations
from tests.video_workbench.test_continuity_snapshot import source_snapshot
from tests.video_workbench.test_continuity_review import fixture


@pytest.mark.parametrize('version', ['motion-continuity-compiler-1', 'motion-continuity-compiler-2'])
def test_each_revision_is_recompiled_before_production_job_admission(tmp_path, version):
    source = source_snapshot()
    segment = resolve_continuity(source['scenes'], build_continuity_plan(source['scenes']),
                                 compiler_version=version)['segments'][0]
    scene = source['scenes'][0]
    files = compile_continuity_project(scene, segment, runtime_fingerprint=source['runtime_fingerprint'])
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(base64.b64decode(scene['audio']['data_base64']))
    frozen = freeze_input(tmp_path, image_digest=source['runtime_fingerprint']['image_digest'])
    assert frozen['manifest_sha256'] == hashlib.sha256(files['manifest.json']).hexdigest()
    resources = production_resources(tmp_path,
        image='registry:5000/video-motion-renderer:latest@'+source['runtime_fingerprint']['image_digest'],
        release_id='continuity-compatibility', namespace='video-motion-render', name='compat-scene', binding='a'*64)
    assert resources
    manifest = json.loads(files['manifest.json'])
    manifest['template'] = 'continuous-diagrams-2' if version.endswith('-1') else 'continuous-diagrams-1'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='project_contract_mismatch'):
        freeze_input(tmp_path, image_digest=source['runtime_fingerprint']['image_digest'])


@pytest.mark.parametrize('version', ['motion-continuity-compiler-1', 'motion-continuity-compiler-2'])
def test_review_dispatch_checks_the_same_actual_states_for_both_revisions(version):
    from tests.video_workbench.test_continuity import scenes
    inputs = scenes(single=True)
    segment = resolve_continuity(inputs, build_continuity_plan(inputs), compiler_version=version)['segments'][1]
    files = compile_continuity_project(inputs[1], segment)
    wrapper, timeline = (json.loads(files[name]) for name in ('plan.json', 'timeline.json'))
    _, _, observations = fixture()
    observations['timeline_sha256'] = sha256(timeline)
    assert verify_observations(wrapper, timeline, observations)['technical_status'] == 'passed'
