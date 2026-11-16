"""Full-film custody at Python, frozen Job and Node render boundaries.

Fixtures verify source and clock binding; they do not prove visual continuity.
"""
from copy import deepcopy
import hashlib
import json
import math

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_production_contract import (
    FILM_CONTEXT_SCHEMA, FILM_INPUT_SCHEMA, FILM_RESULT_SCHEMA, HANDOFF_SCHEMA,
)
from video_workbench.creative_method_contract import collect_scene_result
from video_workbench.creative_renderer import (
    FILM_COMPILER, FILM_TEMPLATE, compile_creative_project,
)
from video_workbench.motion_renderer.job import freeze_input
from test_creative_engine_renderer import engine_scene, RUNTIME_V2
from test_creative_renderer import node_verify


def film_scene(engine='canvas', *, leading=0, trailing=0):
    scene = engine_scene(engine)
    document = deepcopy(scene['creative']['package']['input_document'])
    seconds = scene['audio']['duration_seconds']
    duration = math.ceil((leading + seconds + trailing) * 24) / 24
    film = dict(project_id='film-project', joint_plan_version_id='film-plan',
        content_binding_sha256='e' * 64, title='One continuous explanation',
        script=scene['narration'], understanding_goal='Follow one object without a reset', style='Consistent blue',
        scenes=[dict(scene_id=scene['id'], title=scene['title'], narration=scene['narration'],
            visual_intent='Track the same object', audio_version_id='audio-1',
            audio_sha256=scene['audio']['sha256'], audio_seconds=seconds, start_seconds=0,
            end_seconds=duration, leading_silence_seconds=leading,
            trailing_silence_seconds=duration - leading - seconds)], total_seconds=duration)
    document.update(schema=FILM_INPUT_SCHEMA, film_context=dict(schema=FILM_CONTEXT_SCHEMA,
        film=film, film_sha256=digest(film), current_scene_index=0, predecessor=None))
    delivery = document['method_delivery']
    delivery['task_sha256'] = digest({k: v for k, v in document.items() if k not in {'schema', 'method_delivery'}})
    delivery['sha256'] = digest({k: v for k, v in delivery.items() if k != 'sha256'})
    result = deepcopy(scene['creative']['package']['result'])
    result.update(schema=FILM_RESULT_SCHEMA, input_sha256=digest(document), handoff=dict(
        schema=HANDOFF_SCHEMA, film_sha256=digest(film), scene_id=scene['id'],
        objects=[dict(object_id='moving-group', meaning='Same object', appearance='Blue', state='Ready',
            source_file='scene.html', source_anchor='id="moving-group"')], entry_state='At the entrance',
        exit_state='Inside the queue', camera='Overhead view', transition_intent='Follow this object'))
    result['method_decision']['delivery_sha256'] = delivery['sha256']
    scene['creative']['package'] = collect_scene_result(document, result)
    return scene


def compile_scene(scene, **options):
    creative = scene['creative']
    return compile_creative_project(creative['package'], creative['beats'], creative['alignment'],
                                    runtime_fingerprint=RUNTIME_V2, **options)


@pytest.mark.parametrize('engine', ['html-svg', 'canvas', 'three'])
@pytest.mark.parametrize('leading,trailing', [(0, 0), (.25, .02)])
def test_film_package_crosses_frozen_job_and_node_boundary(tmp_path, engine, leading, trailing):
    scene = film_scene(engine, leading=leading, trailing=trailing)
    files = compile_scene(scene, leading_seconds=leading, trailing_seconds=trailing)
    manifest, timeline = (json.loads(files[name]) for name in ('manifest.json', 'timeline.json'))
    package = scene['creative']['package']
    assert manifest['compiler'] == timeline['compiler'] == FILM_COMPILER
    assert manifest['template'] == FILM_TEMPLATE
    assert timeline['creative']['film_context_sha256'] == digest(package['input_document']['film_context'])
    assert timeline['creative']['handoff_sha256'] == digest(package['result']['handoff'])
    assert timeline['creative']['predecessor_motion_version_id'] is None
    assert timeline['audio_seconds'] == scene['audio']['duration_seconds']
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(scene['audio']['data'])
    assert freeze_input(tmp_path, image_digest=RUNTIME_V2['image_digest'])['unbound_experiment'] is False
    checked = node_verify(tmp_path, hashlib.sha256(files['manifest.json']).hexdigest())
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize('change', ['schema', 'predecessor', 'clock', 'scene', 'handoff-film'])
def test_node_refuses_rehashed_crossed_film_bindings(tmp_path, change):
    scene = film_scene()
    files = compile_scene(scene)
    package, timeline, manifest = (json.loads(files[name]) for name in
                                   ('plan.json', 'timeline.json', 'manifest.json'))
    if change == 'schema':
        package['schema'] = 'video.creative-scene.v3'
    elif change == 'predecessor':
        timeline['creative']['predecessor_motion_version_id'] = 'other-candidate'
    elif change == 'clock':
        package['input_document']['film_context']['film']['scenes'][0]['end_seconds'] += 1
    elif change == 'scene':
        package['input_document']['film_context']['film']['scenes'][0]['scene_id'] = 'other-scene'
    else:
        package['result']['handoff']['film_sha256'] = 'f' * 64
    files['plan.json'] = canonical_bytes(package)
    timeline['plan_sha256'] = digest(package)
    files['timeline.json'] = canonical_bytes(timeline)
    manifest.update(plan_sha256=digest(package), timeline_sha256=digest(timeline),
        files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items() if name != 'manifest.json'})
    files['manifest.json'] = canonical_bytes(manifest)
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(scene['audio']['data'])
    checked = node_verify(tmp_path, hashlib.sha256(files['manifest.json']).hexdigest())
    assert checked.returncode != 0
    assert ('creative_film_binding_mismatch' if change != 'schema' else 'creative_plan_binding_mismatch') in checked.stderr


def test_film_cannot_be_rendered_using_older_compiler():
    with pytest.raises(ServiceError, match='creative_renderer_version_unsupported'):
        compile_scene(film_scene(), compiler_version='creative-method-engine-scene-adapter-1')


def test_same_duration_cannot_move_silence_to_the_other_side_of_the_recording():
    with pytest.raises(ServiceError, match='creative_film_clock_mismatch'):
        compile_scene(film_scene(leading=.25, trailing=.5), leading_seconds=.5, trailing_seconds=.25)
