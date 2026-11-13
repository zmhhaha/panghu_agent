"""Real WAV, source and snapshot bindings for unadopted whole-video drafts."""
import io
import json
import wave

import pytest

from agent_service.contracts import ServiceError
from video_workbench.creative_snapshot import verify_snapshot_v4
from video_workbench.production import Production
from test_creative_production import OWNER, NARRATION, empty_methods, result, state
from test_motion_snapshot import RUNTIME, audio


def candidate(state, *, key='run-candidate', value=1, scene_id=None, duration=None):
    store, motion, _, project_id, original_scene = state
    scene_id = scene_id or original_scene
    project = store.project(OWNER, project_id)
    data = audio(value)['data']
    if duration is not None:
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes(int(value).to_bytes(2, 'little', signed=True) * int(duration * 24000))
        data = stream.getvalue()
    project = store.upload_audio(OWNER, scene_id, dict(expected_revision=project['revision']),
        key + '-audio', data)
    scene = store._scene(project, scene_id)
    audio_id = scene['audio_versions'][-1]['id']
    request = dict(expected_revision=project['revision'], diagram_kind='creative')
    operation = motion.start_candidate(OWNER, scene_id, request, key, audio_version_id=audio_id)
    operation = motion.prepare_creative(operation['id'], empty_methods(store))
    completed = motion.complete(operation['id'], result(operation['document']))
    return dict(scene_id=scene_id, motion_version_id=completed['version_id'], audio_version_id=audio_id), request


def renderer(state):
    store, motion, timing, _, _ = state
    return Production(store, motion_store=motion, motion_timing=timing,
        motion_enabled=True, motion_renderer=object(), runtime_fingerprint=RUNTIME)


def test_unadopted_pair_has_real_clock_without_granting_user_review(state):
    store, motion, timing, project_id, scene_id = state
    pair, request = candidate(state)
    assert motion.version(OWNER, pair['motion_version_id'])['stale']  # ordinary head semantics remain
    assert not motion.candidate_version(OWNER, pair['motion_version_id'], pair['audio_version_id'])['stale']
    clock = timing.get_candidate(OWNER, pair['motion_version_id'], pair['audio_version_id'])
    assert clock['audio']['duration_seconds'] == 1.5
    assert clock['requires_review'] is True
    assert clock['alignment']['review_status'] != 'verified'
    assert not clock['alignment'].get('reviewed_by')
    current = motion.project(OWNER, project_id)['scenes'][0]
    assert current['head_audio_version_id'] is None and current['motion']['head_version_id'] is None
    replay = motion.start_candidate(OWNER, scene_id, request, 'run-candidate', audio_version_id=pair['audio_version_id'])
    assert replay['version_id'] == pair['motion_version_id']
    # Identical ordinary request cannot borrow a coordinator admission.
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        motion.start(OWNER, scene_id, request, 'run-candidate')


def test_candidate_idempotency_pins_recording_and_owner(state):
    store, motion, _, project_id, scene_id = state
    pair, request = candidate(state)
    project = store.project(OWNER, project_id)
    project = store.upload_audio(OWNER, scene_id, dict(expected_revision=project['revision']),
        'replacement-audio', audio(2)['data'])
    replacement = project['scenes'][0]['audio_versions'][-1]['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        motion.start_candidate(OWNER, scene_id, request, 'run-candidate', audio_version_id=replacement)
    with pytest.raises(ServiceError, match='creative_audio_binding_mismatch'):
        motion.candidate_version(OWNER, pair['motion_version_id'], replacement)
    with pytest.raises(ServiceError, match='not_found'):
        motion.candidate_version('someone-else', pair['motion_version_id'], pair['audio_version_id'])


@pytest.mark.parametrize('change', ['voice', 'speed', 'narration', 'visual_description'])
def test_draft_pair_still_invalidates_when_real_inputs_change(state, change):
    store, motion, timing, project_id, scene_id = state
    pair, _ = candidate(state)
    project = store.project(OWNER, project_id)
    if change == 'voice':
        store.configure_speech_provider('kokoro-rk3588', 'zm_yunjian', ['zm_yunjian', 'zm_yunxi'])
        store.save_project_speech_settings(OWNER, project_id,
            dict(expected_revision=project['revision'], voice='zm_yunxi'))
    elif change == 'speed':
        store.save_speech_settings(OWNER, scene_id, dict(expected_revision=project['revision'], speed=1.2))
    else:
        scene = project['scenes'][0]
        fields = {k: scene[k] for k in ('title', 'narration', 'visual_description', 'duration_seconds', 'format')}
        fields[change] += '修改。'
        store.save_scene(OWNER, project_id, fields, scene_id, project['revision'])
    with pytest.raises(ServiceError):
        timing.get_candidate(OWNER, pair['motion_version_id'], pair['audio_version_id'])


def test_whole_draft_uses_exact_ordered_pairs_with_no_heads(state):
    store, motion, _, project_id, scene_id = state
    first, _ = candidate(state)
    project = store.project(OWNER, project_id)
    project = store.save_scene(OWNER, project_id, dict(title='第二段', narration=NARRATION,
        visual_description='继续解释同一队列。', duration_seconds=12, format='html'))
    second, _ = candidate(state, key='second-candidate', value=2, scene_id=project['scenes'][1]['id'])
    production = renderer(state)
    request = dict(expected_revision=store.project(OWNER, project_id)['revision'], selections=[first, second])
    started = production.start_candidate_render(OWNER, project_id, request, 'whole-draft-intent')
    assert started['purpose'] == 'draft' and started['subtitles'] is False
    assert started['selection'] == 'draft_only' and started['review_status'] == 'not_reviewed'
    with store.transaction() as db:
        packed = json.loads(db.execute('SELECT snapshot FROM video_renders WHERE id=?', (started['id'],)).fetchone()[0])
    assert packed['duration_seconds'] == 3  # measured audio, not 2+12 seconds of estimates
    assert [s['id'] for s in packed['scenes']] == [first['scene_id'], second['scene_id']]
    assert all(s['creative']['alignment']['review_status'] != 'verified' for s in packed['scenes'])
    import base64
    for scene in packed['scenes']:
        recording = store.audio_version(OWNER, scene['audio']['id'])
        scene['audio']['data_base64'] = base64.b64encode(recording['data']).decode()
    assert verify_snapshot_v4(packed) == packed
    current = motion.project(OWNER, project_id)
    assert all(s['head_audio_version_id'] is None and s['motion']['head_version_id'] is None for s in current['scenes'])
    assert production.renders(OWNER, project_id)[0]['content_state'] == 'current'
    assert production.start_candidate_render(OWNER, project_id, request, 'whole-draft-intent')['id'] == started['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        production.start_candidate_render(OWNER, project_id, dict(request, selections=[second, first]), 'whole-draft-intent')
    with pytest.raises(ServiceError, match='candidate_render_scene_mismatch'):
        production.start_candidate_render(OWNER, project_id, dict(request, selections=[second, first]), 'wrong-order-intent')
    with pytest.raises(ServiceError, match='candidate_render_scene_mismatch'):
        production.start_candidate_render(OWNER, project_id, dict(request, selections=[first]), 'missing-scene-intent')
    with pytest.raises(ServiceError, match='not_found'):
        production.start_candidate_render('another-owner', project_id, request, 'other-owner-intent')
    store.save_speech_settings(OWNER, scene_id, dict(expected_revision=current['revision'], speed=1.1))
    assert production.renders(OWNER, project_id)[0]['content_state'] == 'changed'


def test_candidate_render_does_not_offer_delivery_or_ignore_source_change(state):
    store, _, _, project_id, scene_id = state
    pair, _ = candidate(state)
    production = renderer(state)
    project = store.project(OWNER, project_id)
    request = dict(expected_revision=project['revision'], selections=[pair])
    with pytest.raises(ServiceError, match='invalid_candidate_render_request'):
        production.start_candidate_render(OWNER, project_id, dict(request, purpose='delivery'), 'delivery-bypass-intent')
    with pytest.raises(ServiceError, match='invalid_candidate_render_request'):
        production.start_candidate_render(OWNER, project_id, dict(request, subtitles=True), 'subtitles-bypass-intent')
    scene = project['scenes'][0]
    fields = {k: scene[k] for k in ('title', 'narration', 'visual_description', 'duration_seconds', 'format')}
    fields['visual_description'] += '改为另一种过程。'
    updated = store.save_scene(OWNER, project_id, fields, scene_id, project['revision'])
    with pytest.raises(ServiceError, match='motion_plan_stale'):
        production.start_candidate_render(OWNER, project_id, dict(request, expected_revision=updated['revision']), 'stale-source-intent')


def test_candidate_timing_enforces_actual_audio_with_leading_and_trailing_limit(state):
    store, _, _, project_id, scene_id = state
    pair, _ = candidate(state)
    production = renderer(state)
    project = store.project(OWNER, project_id)
    production.timing(OWNER, scene_id, dict(leading_seconds=1, trailing_seconds=2), project['revision'])
    request = dict(expected_revision=store.project(OWNER, project_id)['revision'], selections=[pair])
    started = production.start_candidate_render(OWNER, project_id, request, 'leading-trailing-intent')
    assert production.renders(OWNER, project_id)[0]['duration_seconds'] == 4.5
    assert started['review_status'] == 'not_reviewed'


def test_candidate_render_rejects_actual_audio_plus_holds_over_sixty_seconds(state):
    store, _, _, project_id, scene_id = state
    pair, _ = candidate(state, duration=59)
    production = renderer(state)
    project = store.project(OWNER, project_id)
    production.timing(OWNER, scene_id, dict(leading_seconds=1, trailing_seconds=1), project['revision'])
    request = dict(expected_revision=store.project(OWNER, project_id)['revision'], selections=[pair])
    with pytest.raises(ServiceError, match='motion_scene_duration_exceeded'):
        production.start_candidate_render(OWNER, project_id, request, 'too-long-candidate')
    assert production.renders(OWNER, project_id) == []


def test_candidate_rechecks_wav_bytes_under_the_same_recording_id(state):
    store, motion, _, project_id, _ = state
    pair, _ = candidate(state)
    with store.transaction() as db:
        db.execute('UPDATE audio_versions SET data=? WHERE id=?',
            (audio(2)['data'], pair['audio_version_id']))
    with pytest.raises(ServiceError):
        motion.candidate_version(OWNER, pair['motion_version_id'], pair['audio_version_id'])
    with pytest.raises(ServiceError):
        renderer(state).start_candidate_render(OWNER, project_id,
            dict(expected_revision=store.project(OWNER, project_id)['revision'], selections=[pair]),
            'tampered-wav-candidate')


@pytest.mark.parametrize('change', ['reorder', 'append'])
def test_whole_candidate_history_detects_changed_scene_list(state, change):
    store, _, _, project_id, _ = state
    first, _ = candidate(state)
    project = store.save_scene(OWNER, project_id, dict(title='第二段', narration=NARRATION,
        visual_description='继续解释同一队列。', duration_seconds=12, format='html'))
    second, _ = candidate(state, key='second-history', value=2, scene_id=project['scenes'][1]['id'])
    production = renderer(state)
    production.start_candidate_render(OWNER, project_id,
        dict(expected_revision=store.project(OWNER, project_id)['revision'], selections=[first, second]),
        'whole-history-intent')
    assert production.renders(OWNER, project_id)[0]['content_state'] == 'current'
    if change == 'append':
        store.save_scene(OWNER, project_id, dict(title='第三段', narration=NARRATION,
            visual_description='补充新的讲解。', duration_seconds=3, format='html'))
    else:
        with store.transaction() as db:
            project = store._project(db, OWNER, project_id)
            project['scenes'].reverse()
            store._save(db, project)
    assert production.renders(OWNER, project_id)[0]['content_state'] == 'changed'
