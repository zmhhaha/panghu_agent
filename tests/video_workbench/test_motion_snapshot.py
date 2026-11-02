"""Immutable v2 semantics beside the unchanged static v1 export contract."""
from copy import deepcopy
import base64
import hashlib
import io
import wave

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench import media
from video_workbench.audio_store import narration_binding, narration_document
from video_workbench.motion import estimated_alignment, example_plan, make_beats, manual_alignment
from video_workbench.motion_snapshot import freeze_snapshot_v2, verify_snapshot_v2


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="blue"/></svg>'
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
               hyperframes_source='2905162d925fc87fb8242e24ed0bbf025d307ea1',
               node='v22.20.0', browser='HeadlessChrome/141.0.7390.54',
               ffmpeg='ffmpeg version 5.1.7-0+deb12u1', gsap='3.14.2',
               gsap_sha256='a' * 64, font_sha256='f' * 64, dpkg_sha256='d' * 64,
               npm_lock_sha256='e' * 64, image_digest='sha256:' + '1' * 64)


def pcm(value=1):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(int(value).to_bytes(2, 'little', signed=True) * 36000)
    return stream.getvalue()


def audio(value=1, identity='audio-candidate'):
    data = pcm(value)
    return dict(id=identity, sha256=hashlib.sha256(data).hexdigest(), data=data,
                duration_seconds=1.5, spoken_text='请求到达。进入队列。执行节点开始工作。',
                origin='tts', model='kokoro-rk3588', voice='zm_yunjian')


def motion_scene(*, reviewed=False, value=1):
    candidate = audio(value)
    beats = make_beats(candidate['spoken_text'])
    alignment = estimated_alignment(beats, candidate['sha256'], 1.5)
    if reviewed:
        alignment = manual_alignment(beats, candidate['sha256'], 1.5, alignment['segments'],
            previous_alignment=alignment, reviewed_by='fixture-reviewer', reviewed_at='2026-11-02T14:00:00+08:00')
    return dict(id='scene-motion', title='流程', narration=beats['source_text'],
                visual_mode='motion', audio=candidate,
                motion=dict(id='motion-candidate', plan=example_plan(beats), beats=beats, alignment=alignment))


def static_scene():
    return dict(id='scene-static', title='静态', narration='保留旧图解。', duration_seconds=1.5,
                visual_mode='static', visual=dict(id='static-candidate', format='svg', source=SVG),
                audio=audio())


def adopt(scenes):
    return {s['id']: dict(visual_mode=s['visual_mode'],
                          visual_version_id=s['motion' if s['visual_mode'] == 'motion' else 'visual']['id'],
                          audio_version_id=s['audio']['id']) for s in scenes}


def freeze(scenes=None, **kwargs):
    return freeze_snapshot_v2('project', 7, '同一项目', [motion_scene()] if scenes is None else scenes,
                              runtime_fingerprint=RUNTIME, **kwargs)


def resign(snapshot):
    snapshot['sha256'] = digest({key: value for key, value in snapshot.items() if key != 'sha256'})
    return snapshot


def test_draft_can_reference_unadopted_candidates_without_mutation():
    scene = motion_scene()
    heads = {'scene-motion': dict(visual_mode='motion', visual_version_id='older-motion', audio_version_id='older-audio')}
    original_scene, original_heads = deepcopy(scene), deepcopy(heads)
    result = freeze([scene], current_adoptions=heads)
    assert result['purpose'] == 'draft'
    assert result['scenes'][0]['motion']['id'] == 'motion-candidate'
    assert result['scenes'][0]['audio']['id'] == 'audio-candidate'
    assert scene == original_scene and heads == original_heads
    assert verify_snapshot_v2(result) == result


def test_mixed_snapshot_freezes_exact_inputs_and_complete_audio():
    scenes = [static_scene(), motion_scene()]
    result = freeze(scenes)
    assert [s['visual_mode'] for s in result['scenes']] == ['static', 'motion']
    assert result['duration_seconds'] == 3
    assert result['scenes'][1]['start_seconds'] == 1.5
    assert result['scenes'][1]['end_seconds'] == 3
    assert result['silent'] is result['subtitles'] is False
    for frozen, source in zip(result['scenes'], scenes):
        assert base64.b64decode(frozen['audio']['data_base64']) == source['audio']['data']
    assert verify_snapshot_v2(result) == result


def test_delivery_requires_current_adoption_evidence_and_verified_alignment():
    reviewed = motion_scene(reviewed=True)
    with pytest.raises(ServiceError, match='motion_adoption_evidence_missing'):
        freeze([reviewed], purpose='delivery')
    wrong = adopt([reviewed])
    wrong['scene-motion']['audio_version_id'] = 'another-audio'
    with pytest.raises(ServiceError, match='motion_adoption_conflict'):
        freeze([reviewed], purpose='delivery', current_adoptions=wrong)
    pending = motion_scene()
    with pytest.raises(ServiceError, match='motion_timing_needs_review'):
        freeze([pending], purpose='delivery', current_adoptions=adopt([pending]))
    result = freeze([reviewed], purpose='delivery', quality='final', current_adoptions=adopt([reviewed]))
    assert result['purpose'] == 'delivery'
    assert result['scenes'][0]['motion']['alignment']['review_status'] == 'verified'
    assert verify_snapshot_v2(result) == result


def test_mixed_delivery_adoption_map_checks_static_and_motion():
    scenes = [static_scene(), motion_scene(reviewed=True)]
    heads = adopt(scenes)
    heads['scene-static']['visual_version_id'] = 'wrong-static'
    with pytest.raises(ServiceError, match='motion_adoption_conflict'):
        freeze(scenes, purpose='delivery', current_adoptions=heads)
    result = freeze(scenes, purpose='delivery', current_adoptions=adopt(scenes))
    assert verify_snapshot_v2(result) == result


def test_voice_change_reuses_plan_but_requires_a_new_audio_timeline_binding():
    first = motion_scene()
    changed = deepcopy(first)
    changed['audio'] = audio(2, 'other-voice-audio')
    changed['audio']['voice'] = 'zm_yunxi'
    with pytest.raises(ServiceError, match='motion_audio_binding_mismatch'):
        freeze([changed])
    changed['motion']['alignment'] = estimated_alignment(changed['motion']['beats'], changed['audio']['sha256'], 1.5)
    one, two = freeze([first]), freeze([changed])
    a, b = one['scenes'][0]['motion'], two['scenes'][0]['motion']
    assert a['plan'] == b['plan']
    assert a['semantic_sha256'] == b['semantic_sha256']
    assert a['timeline']['alignment_sha256'] != b['timeline']['alignment_sha256']
    assert one['sha256'] != two['sha256']


def test_project_cannot_mix_generated_voices_but_uploads_stay_explicit():
    first, second = motion_scene(), motion_scene()
    second['id'] = 'second-scene'
    second['audio']['voice'] = 'zm_yunxi'
    with pytest.raises(ServiceError, match='motion_project_voice_conflict'):
        freeze([first, second])
    second['audio']['origin'] = 'upload'
    second['audio'].pop('model')
    second['audio'].pop('voice')
    result = freeze([first, second])
    assert result['scenes'][1]['audio']['origin'] == 'upload'
    assert 'voice' not in result['scenes'][1]['audio']
    assert verify_snapshot_v2(result) == result


def test_quality_and_runtime_are_part_of_the_frozen_identity():
    draft, final = freeze(), freeze(quality='final')
    assert draft['sha256'] != final['sha256']
    assert draft['scenes'][0]['motion']['plan'] == final['scenes'][0]['motion']['plan']
    scene = motion_scene()
    runtime = dict(RUNTIME, browser='HeadlessChrome/142.0.0.1')
    changed = freeze_snapshot_v2('project', 7, '同一项目', [scene], runtime_fingerprint=runtime)
    assert changed['sha256'] != draft['sha256']
    assert changed['scenes'][0]['motion']['project_files_sha256']['manifest.json'] != draft['scenes'][0]['motion']['project_files_sha256']['manifest.json']


@pytest.mark.parametrize('location', ['timeline', 'files', 'audio', 'duration', 'semantic', 'alignment'])
def test_resigned_tampering_cannot_bypass_rebuild_validation(location):
    snapshot = freeze()
    target = snapshot['scenes'][0]
    if location == 'timeline':
        target['motion']['timeline']['actions'][0]['start'] += .2
    elif location == 'files':
        target['motion']['project_files_sha256']['index.html'] = '0' * 64
    elif location == 'audio':
        target['audio']['data_base64'] = base64.b64encode(pcm(2)).decode()
    elif location == 'duration':
        target['timing']['duration_seconds'] += .5
    elif location == 'semantic':
        target['motion']['semantic_sha256'] = '1' * 64
    else:
        target['motion']['alignment']['segments'][1]['start'] = 0
    resign(snapshot)
    with pytest.raises(ServiceError):
        verify_snapshot_v2(snapshot)


def test_plain_snapshot_hash_mismatch_is_rejected():
    snapshot = freeze()
    snapshot['title'] = '未更新摘要'
    with pytest.raises(ServiceError, match='motion_snapshot_corrupt'):
        verify_snapshot_v2(snapshot)


def test_wrong_spoken_text_is_not_validated_by_matching_original_narration():
    scene = motion_scene()
    scene['audio']['spoken_text'] = '实际上念了另一句话。'
    with pytest.raises(ServiceError, match='motion_audio_binding_mismatch'):
        freeze([scene])


def test_legacy_audio_metadata_can_derive_exact_pronunciation_binding():
    scene = motion_scene()
    settings = dict(model='kokoro-rk3588', voice='zm_yunjian', speed=1.0, pronunciation={'请求': '情求'})
    document = narration_document(dict(narration=scene['narration'], speech_settings=settings),
                                   dict(model=settings['model'], voice=settings['voice']))
    scene['audio'].pop('spoken_text')
    scene['audio'].update(narration=scene['narration'], settings=settings, input_sha256=narration_binding(document))
    beats = make_beats(scene['narration'], document['spoken_text'])
    scene['motion'].update(beats=beats, alignment=estimated_alignment(beats, scene['audio']['sha256'], 1.5))
    result = freeze([scene])
    assert result['scenes'][0]['audio']['spoken_text'] == document['spoken_text']
    assert verify_snapshot_v2(result) == result
    scene['audio']['input_sha256'] = '0' * 64
    with pytest.raises(ServiceError, match='motion_audio_text_binding_mismatch'):
        freeze([scene])


@pytest.mark.parametrize('mutation', [
    lambda s: s['audio'].update(stale='yes'),
    lambda s: s['motion'].update(stale='yes'),
    lambda s: s.update(narration='已经修改的旁白。'),
    lambda s: s['audio'].update(duration_seconds=2),
])
def test_stale_or_incorrect_current_inputs_are_refused(mutation):
    scene = motion_scene()
    mutation(scene)
    with pytest.raises(ServiceError):
        freeze([scene])


def test_static_v1_contract_and_frozen_renderer_meaning_remain_unchanged(monkeypatch):
    legacy = media.freeze_snapshot('project', 7, '旧片', [static_scene()], subtitles=False)
    assert legacy['schema_version'] == '1' and legacy['visual_mode'] == 'static'
    assert 'motion' not in legacy['scenes'][0]
    assert media.verify_snapshot(legacy) == legacy
    snapshot = freeze([static_scene()])
    saved = snapshot['scenes'][0]['static_renderer']
    monkeypatch.setenv('VIDEO_STATIC_RENDERER', 'weasyprint' if saved == 'chromium' else 'chromium')
    assert verify_snapshot_v2(snapshot) == snapshot


def test_independent_scene_edits_do_not_change_other_scene_frozen_bytes():
    first, second = motion_scene(), motion_scene()
    second['id'] = 'second-scene'
    before = freeze([first, second])
    second['motion']['plan']['title'] = '只修改第二镜的标题'
    after = freeze([first, second])
    assert before['scenes'][0] == after['scenes'][0]
    assert before['scenes'][1]['motion']['plan'] != after['scenes'][1]['motion']['plan']


def test_padding_and_subtitles_choices_survive_rebuild():
    scene = motion_scene()
    scene.update(leading_seconds=3, trailing_seconds=3)
    snapshot = freeze([scene])
    assert snapshot['duration_seconds'] == 7.5
    assert snapshot['scenes'][0]['captions']['cues'] == []
    assert verify_snapshot_v2(snapshot) == snapshot


@pytest.mark.parametrize('key', list(RUNTIME))
def test_incomplete_runtime_fingerprint_is_refused(key):
    runtime = dict(RUNTIME)
    runtime.pop(key)
    with pytest.raises(ServiceError, match='motion_runtime_fingerprint'):
        freeze_snapshot_v2('project', 7, '同一项目', [motion_scene()], runtime_fingerprint=runtime)


@pytest.mark.parametrize('key,value', [
    ('schema', 'some-other-schema'), ('hyperframes', 'latest'),
    ('hyperframes_source', 'another-source'), ('gsap', 'latest'),
    ('gsap_sha256', 'not-a-hash'), ('font_sha256', 'g' * 64),
    ('image_digest', 'latest'), ('node', ''), ('browser', []),
])
def test_invalid_or_unpinned_runtime_fingerprint_is_refused(key, value):
    runtime = dict(RUNTIME, **{key: value})
    with pytest.raises(ServiceError, match='motion_runtime_'):
        freeze_snapshot_v2('project', 7, '同一项目', [motion_scene()], runtime_fingerprint=runtime)
