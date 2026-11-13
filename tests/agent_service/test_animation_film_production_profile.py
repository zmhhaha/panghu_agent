"""Film source custody, clock, bounded handoff and independent profile admission."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_production as profile
from agent_service.profiles.video_animation_production_contract import (
    FILM_CONTEXT_SCHEMA, FILM_INPUT_SCHEMA, FILM_RESULT_SCHEMA, HANDOFF_SCHEMA,
    validate_core_task, validate_film_context, validate_input, validate_result,
)
from test_animation_production_profile import document, raw_result


def handoff(film_sha256, scene_id):
    return dict(schema=HANDOFF_SCHEMA, film_sha256=film_sha256, scene_id=scene_id,
        objects=[dict(object_id='queue', meaning='同一条任务队列', appearance='蓝色容器', state='等待执行',
                      source_file='scene.html', source_anchor='id="object"')],
        entry_state='队列在画面中央', exit_state='对象移动到队列前方', camera='保持俯视视角',
        transition_intent='下一段进入队列内部，保持同一对象身份')


def bind(context):
    delivery = context['method_delivery']
    delivery['task_sha256'] = digest({key: value for key, value in context.items()
                                    if key not in {'schema', 'method_delivery'}})
    delivery['sha256'] = digest({key: value for key, value in delivery.items() if key != 'sha256'})
    return context


def film_document(index=0):
    context = document()
    scene = context['scene']
    rows = [dict(scene_id=scene['scene_id'] if i == 0 else 'next', title='对象与队列',
        narration=scene['narration'], visual_intent='展示同一对象状态的变化', audio_version_id=f'audio-{i}',
        audio_sha256=scene['audio_sha256'], audio_seconds=3, start_seconds=i * 3, end_seconds=(i + 1) * 3,
        leading_silence_seconds=0, trailing_silence_seconds=0) for i in range(2)]
    film = dict(project_id='project', joint_plan_version_id='plan', content_binding_sha256='e' * 64,
        title='对象与队列', script='总稿先解释对象，再说明队列。', understanding_goal='理解对象进入队列后的变化',
        style='蓝色主体与清楚的状态变化', scenes=rows, total_seconds=6)
    film_sha = digest(film)
    predecessor = None
    if index:
        prior = raw_result(context)['source']
        prior['files_sha256'] = {key: hashlib.sha256(value.encode()).hexdigest()
                                for key, value in prior['files'].items()}
        predecessor = dict(scene_id=rows[0]['scene_id'], motion_version_id='motion-0',
            input_sha256='1' * 64, result_sha256='2' * 64, source_sha256=digest(prior),
            source_summary={key: deepcopy(prior[key]) for key in ('engine', 'strategy', 'files_sha256')},
            handoff=handoff(film_sha, rows[0]['scene_id']))
    context['schema'] = FILM_INPUT_SCHEMA
    context['scene']['scene_id'] = rows[index]['scene_id']
    context['film_context'] = dict(schema=FILM_CONTEXT_SCHEMA, film_sha256=film_sha,
        film=film, current_scene_index=index, predecessor=predecessor)
    return bind(context)


def film_result(context):
    result = raw_result(context)
    result['schema'] = FILM_RESULT_SCHEMA
    result['source']['files_sha256'] = {key: hashlib.sha256(value.encode()).hexdigest()
                                       for key, value in result['source']['files'].items()}
    result['handoff'] = handoff(context['film_context']['film_sha256'], context['scene']['scene_id'])
    return result


@pytest.mark.parametrize('index', [0, 1])
def test_film_carries_frozen_whole_script_real_clock_and_exact_predecessor(index):
    context = film_document(index)
    assert validate_input(context) == context
    core = {key: value for key, value in context.items() if key not in {'schema', 'method_delivery'}}
    assert validate_core_task(core, engine_aware=True, film_aware=True) == core
    with pytest.raises(ServiceError):
        validate_core_task(core, engine_aware=True)
    result = film_result(context)
    assert validate_result(result, digest(context), context) == result


def test_quantized_hold_preserves_recording_seconds_and_exact_render_boundaries():
    context = film_document()
    film = context['film_context']['film']
    film['scenes'][0]['trailing_silence_seconds'] = 1 / 24
    film['scenes'][0]['end_seconds'] += 1 / 24
    film['scenes'][1]['start_seconds'] += 1 / 24
    film['scenes'][1]['end_seconds'] += 1 / 24
    film['total_seconds'] += 1 / 24
    context['film_context']['film_sha256'] = digest(film)
    assert validate_input(bind(context)) == context
    assert context['constraints']['duration_seconds'] == 3


@pytest.mark.parametrize('change', ['script-tamper', 'gap', 'unquantized', 'wrong-total',
    'current-audio', 'current-narration', 'duplicate-scene', 'wrong-current-index'])
def test_wrong_whole_film_clock_custody_or_current_audio_is_rejected(change):
    context = film_document()
    whole = context['film_context']
    film = whole['film']
    if change == 'script-tamper':
        film['script'] += '未冻结内容'
    elif change == 'gap':
        film['scenes'][1]['start_seconds'] += 1
    elif change == 'unquantized':
        film['scenes'][0]['end_seconds'] += .01
        film['scenes'][0]['trailing_silence_seconds'] += .01
    elif change == 'wrong-total':
        film['total_seconds'] = 7
    elif change == 'current-audio':
        film['scenes'][0]['audio_sha256'] = 'b' * 64
    elif change == 'current-narration':
        film['scenes'][0]['narration'] = '另一段录音'
    elif change == 'duplicate-scene':
        film['scenes'][1]['scene_id'] = film['scenes'][0]['scene_id']
    else:
        whole['current_scene_index'] = True
    if change != 'script-tamper':
        whole['film_sha256'] = digest(film)
    with pytest.raises(ServiceError):
        validate_input(bind(context))


@pytest.mark.parametrize('change', ['missing', 'wrong-scene', 'wrong-film', 'nested-package',
    'source-hash', 'unknown-summary-field', 'extra-handoff-field', 'handoff-file'])
def test_predecessor_references_are_bounded_explicit_and_cannot_recurse(change):
    context = film_document(1)
    predecessor = context['film_context']['predecessor']
    if change == 'missing':
        context['film_context']['predecessor'] = None
    elif change == 'wrong-scene':
        predecessor['scene_id'] = 'not-the-neighbor'
    elif change == 'wrong-film':
        predecessor['handoff']['film_sha256'] = 'f' * 64
    elif change == 'nested-package':
        predecessor['package'] = {'input_document': deepcopy(context)}
    elif change == 'source-hash':
        predecessor['source_sha256'] = 'invalid'
    elif change == 'unknown-summary-field':
        predecessor['source_summary']['files'] = {'scene.html': 'nested source'}
    elif change == 'handoff-file':
        predecessor['handoff']['objects'][0]['source_file'] = 'scene.css'
    else:
        predecessor['handoff']['accepted'] = True
    with pytest.raises(ServiceError):
        validate_input(bind(context))


@pytest.mark.parametrize('change', ['film', 'scene', 'anchor', 'duplicate-object', 'extra-field', 'oversize'])
def test_handoff_is_a_source_bound_declaration_and_not_a_quality_or_identity_claim(change):
    context = film_document()
    result = film_result(context)
    state = result['handoff']
    if change == 'film': state['film_sha256'] = 'f' * 64
    elif change == 'scene': state['scene_id'] = 'somewhere-else'
    elif change == 'anchor': state['objects'][0]['source_anchor'] = 'not-in-returned-source'
    elif change == 'duplicate-object': state['objects'].append(deepcopy(state['objects'][0]))
    elif change == 'extra-field': state['observed_quality'] = 'verified'
    else:
        base = state['objects'][0]
        state['objects'] = [dict(base, object_id=f'object_{i}', meaning='x' * 2000,
            appearance='y' * 2000, state='z' * 2000) for i in range(10)]
    with pytest.raises(ServiceError):
        validate_result(result, digest(context), context)


def stage(tmp_path, context):
    raw = canonical_bytes(context)
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / profile.INPUT_NAME).write_bytes(raw)
    result = film_result(context)
    del result['source']['files_sha256']
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=profile.FILM_PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(context)}, inputs=[InlineFile.from_bytes(profile.INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('offline-film-fixture',))
    return envelope, proof


def test_separately_admitted_film_profile_collects_stopped_hashes_without_mutating_old_prompt(tmp_path):
    context = film_document(1)
    envelope, proof = stage(tmp_path, context)
    handler = profile.VideoAnimationFilmProductionProfile()
    artifact = handler.collect_output(tmp_path, envelope, proof)[0]
    assert json.loads(artifact.content()) == film_result(context)
    assert handler.required_model_capabilities == ('text', 'tool_calls')
    with pytest.raises(ServiceError, match='profile_schema_mismatch'):
        profile.VideoAnimationProductionProfile().validate_inputs(tmp_path, envelope.task)
    assert set(profile.verified_bundle(film=True)) == profile.BUNDLE_FILES
    new_prompt = handler.build_prompt(envelope.task)
    assert 'film_context' in new_prompt and 'video.scene-handoff.v1' in new_prompt
    assert 'does not mean you observed a frame' in new_prompt
    assert 'animation-production-instructions-4' in new_prompt
    old_prompt = profile.VideoAnimationProductionProfile().build_prompt(envelope.task)
    assert 'animation-production-instructions-3' in old_prompt and 'film_context' not in old_prompt
    assert profile.BUNDLE_REVISION == 'animation-production-v2'


def test_old_inputs_cannot_be_mislabeled_for_new_profile(tmp_path):
    context = document()
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / profile.INPUT_NAME).write_bytes(canonical_bytes(context))
    with pytest.raises(ServiceError, match='profile_schema_mismatch'):
        profile.VideoAnimationFilmProductionProfile().validate_inputs(tmp_path, {'input_sha256': digest(context)})


def test_method_delivery_binds_the_complete_film_input_not_just_the_local_scene():
    context = film_document()
    context['film_context']['film']['style'] = '改变全片风格'
    context['film_context']['film_sha256'] = digest(context['film_context']['film'])
    with pytest.raises(ServiceError, match='method_task_mismatch'):
        validate_input(context)
