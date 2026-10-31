"""Real SQLite/PCM tests at the immutable narration and once-only POST boundary."""
import hashlib
import io
import threading
import wave
from dataclasses import replace

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.audio_workflow import AudioWorkflow
from video_workbench.speech import SpeechAudio, SpeechSettings, inspect_wav
from video_workbench.store import Store


SCENE = dict(title='原理', narration='SDK 和 PTY 的工作原理。', visual_description='蓝色架构图',
             duration_seconds=8, format='svg')


def wav_bytes(silent=False):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes((b'\0\0' if silent else b'\x04\x08') * 24000)
    return output.getvalue()


class FakeSpeech:
    def __init__(self):
        self.settings = SpeechSettings('http://speech.internal/v1', 'kokoro-rk3588', voice='zm_yunjian')
        self.requests = []
        self.callback = None
        self.fail = None
        self.bad_binding = False
        self.silent = False

    def synthesize(self, text, *, voice, speed, cancel_event):
        self.requests.append(dict(text=text, voice=voice, speed=speed))
        if self.callback:
            self.callback(cancel_event)
        if self.fail:
            raise self.fail
        data = wav_bytes(self.silent)
        return SpeechAudio(**vars(inspect_wav(data)), data=data, sha256=hashlib.sha256(data).hexdigest(),
            input_sha256='0' * 64 if self.bad_binding else digest(dict(model=self.settings.model,
                input=text, voice=voice, speed=speed, response_format='wav')))


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'state.db')
    project = store.create_project('alice', dict(title='测试', script='技术文稿', style='信息图'))
    project = store.save_scene('alice', project['id'], dict(SCENE))
    speech = FakeSpeech()
    worker = AudioWorkflow(store, speech)
    return store, project, speech, worker


def begin(store, project, key='speech-request-1', index=0):
    return store.start_audio('alice', project['scenes'][index]['id'],
                             dict(expected_revision=project['revision']), key)


def test_generation_is_immutable_and_requires_independent_adoption(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    assert store.active_operations() == []  # Visual reconciler cannot consume speech.
    worker.tick()
    project = store.project('alice', project['id'])
    scene = project['scenes'][0]
    candidate = scene['audio_versions'][0]
    assert scene['head_audio_version_id'] is None and scene['head_version_id'] is None
    assert scene['audio_operation']['status'] == 'completed'
    assert candidate['stale'] is False and candidate['voice'] == 'zm_yunjian'
    assert candidate['source'] == 'tts' and candidate['duration_seconds'] == 1
    assert 'data' not in candidate
    stored = store.audio_version('alice', candidate['id'])
    assert stored['data'] == wav_bytes()
    adopted = store.adopt_audio('alice', scene['id'], candidate['id'], None)
    assert adopted['scenes'][0]['head_audio_version_id'] == candidate['id']
    assert adopted['scenes'][0]['head_version_id'] is None
    assert len(speech.requests) == 1
    worker.tick()
    assert len(speech.requests) == 1


def test_upload_replay_checks_original_intent_before_current_revision(setup):
    store, project, speech, worker = setup
    scene_id = project['scenes'][0]['id']
    request = dict(expected_revision=project['revision'])
    first = store.upload_audio('alice', scene_id, request, 'audio-upload-1', wav_bytes())
    replay = store.upload_audio('alice', scene_id, request, 'audio-upload-1', wav_bytes())
    assert first == replay
    assert len(replay['scenes'][0]['audio_versions']) == 1
    assert replay['scenes'][0]['audio_versions'][0]['source'] == 'upload'
    assert replay['scenes'][0]['head_audio_version_id'] is None
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        store.upload_audio('alice', scene_id, request, 'audio-upload-1', wav_bytes(), source_name='different.wav')


def test_owners_cannot_read_adopt_upload_cancel_or_inspect_others_audio(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    worker.tick()
    project = store.project('alice', project['id'])
    scene = project['scenes'][0]
    candidate_id = scene['audio_versions'][0]['id']
    functions = [
        lambda: store.audio_version('bob', candidate_id),
        lambda: store.audio_operation(op['id'], 'bob'),
        lambda: store.cancel_audio('bob', op['id']),
        lambda: store.adopt_audio('bob', scene['id'], candidate_id, None),
        lambda: store.upload_audio('bob', scene['id'], {'expected_revision': 1}, 'bob-upload-1', wav_bytes()),
        lambda: store.save_speech_settings('bob', scene['id'], {'expected_revision': 1}),
    ]
    for call in functions:
        with pytest.raises(ServiceError) as exc:
            call()
        assert exc.value.status == 404


def test_narration_edits_invalidate_only_one_scene_and_keep_history(setup):
    store, project, speech, worker = setup
    project = store.save_scene('alice', project['id'], dict(SCENE, title='第二镜'))
    for index in range(2):
        begin(store, project, key=f'speech-request-{index}', index=index)
        worker.tick()
        project = store.project('alice', project['id'])
        scene = project['scenes'][index]
        project = store.adopt_audio('alice', scene['id'], scene['audio_versions'][0]['id'], None)
    first, second = project['scenes']
    project = store.save_scene('alice', project['id'], dict(SCENE, narration='已经修改的旁白。'),
                               first['id'], project['revision'])
    assert project['scenes'][0]['audio_stale'] is True
    assert project['scenes'][0]['audio_versions'][0]['stale'] is True
    assert project['scenes'][1]['audio_stale'] is False
    assert store.audio_version('alice', first['head_audio_version_id'])['data'] == wav_bytes()
    with pytest.raises(ServiceError, match='audio_stale'):
        store.adopt_audio('alice', first['id'], first['head_audio_version_id'], first['head_audio_version_id'])


def test_visual_edits_do_not_invalidate_audio(setup):
    store, project, speech, worker = setup
    begin(store, project)
    worker.tick()
    project = store.project('alice', project['id'])
    scene = project['scenes'][0]
    project = store.adopt_audio('alice', scene['id'], scene['audio_versions'][0]['id'], None)
    project = store.save_scene('alice', project['id'], dict(SCENE, visual_description='新的布局', format='html'),
                               scene['id'], project['revision'])
    assert not project['scenes'][0]['audio_stale']


def test_late_result_keeps_original_binding_and_never_overwrites_selection(setup):
    store, project, speech, worker = setup
    scene_id = project['scenes'][0]['id']
    project = store.upload_audio('alice', scene_id, {'expected_revision': project['revision']},
                                 'audio-upload-1', wav_bytes())
    adopted = project['scenes'][0]['audio_versions'][0]['id']
    project = store.adopt_audio('alice', scene_id, adopted, None)
    begin(store, project)
    def editing(_):
        latest = store.project('alice', project['id'])
        store.save_scene('alice', project['id'], dict(SCENE, narration='合成期间更新的旁白。'), scene_id, latest['revision'])
    speech.callback = editing
    worker.tick()
    latest = store.project('alice', project['id'])['scenes'][0]
    assert latest['head_audio_version_id'] == adopted
    assert len(latest['audio_versions']) == 2
    assert all(candidate['stale'] for candidate in latest['audio_versions'])
    assert latest['audio_versions'][1]['narration'] == SCENE['narration']


def test_settings_bind_pronunciation_without_cascading_and_invalidate_audio(setup):
    store, project, speech, worker = setup
    scene_id = project['scenes'][0]['id']
    project = store.save_speech_settings('alice', scene_id,
        dict(expected_revision=project['revision'], speed=0.9, pronunciation={'SDK': 'PTY', 'PTY': '终端'}))
    begin(store, project)
    worker.tick()
    assert speech.requests[0]['text'] == 'PTY 和 终端 的工作原理。'
    assert speech.requests[0]['speed'] == 0.9
    project = store.project('alice', project['id'])
    candidate = project['scenes'][0]['audio_versions'][0]
    assert candidate['narration'] == SCENE['narration']
    project = store.adopt_audio('alice', scene_id, candidate['id'], None)
    project = store.save_speech_settings('alice', scene_id,
        dict(expected_revision=project['revision'], speed=1.0, pronunciation={}))
    assert project['scenes'][0]['audio_stale']


def test_cancel_before_submission_issues_no_provider_request(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    assert store.cancel_audio('alice', op['id'])['status'] == 'cancelled'
    worker.tick()
    assert speech.requests == []
    assert store.project('alice', project['id'])['scenes'][0]['audio_versions'] == []


def test_running_cancellation_discards_late_result_and_does_not_claim_remote_stop(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    def cancelling(signal):
        assert store.cancel_audio('alice', op['id'])['status'] == 'cancelling'
        assert signal.is_set()
    speech.callback = cancelling
    worker.tick()
    result = store.audio_operation(op['id'])
    assert result['status'] == 'cancelled'
    assert result['upstream_stop_confirmed'] is False
    assert store.project('alice', project['id'])['scenes'][0]['audio_versions'] == []


def test_restart_never_resubmits_claimed_ambiguous_operation(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    assert store.claim_audio(op['id'])['submit_attempted'] is True
    reopened = Store(store.path)
    fresh = AudioWorkflow(reopened, speech)
    fresh.tick()
    result = reopened.audio_operation(op['id'])
    assert result['status'] == 'failed' and result['error'] == 'speech_submission_uncertain'
    assert speech.requests == []
    replay = begin(store, project)
    assert replay['id'] == op['id'] and replay['status'] == 'failed'


def test_restart_recovers_queued_request_once(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    fresh = AudioWorkflow(Store(store.path), speech)
    fresh.tick()
    fresh.tick()
    assert len(speech.requests) == 1
    assert store.audio_operation(op['id'])['status'] == 'completed'


def test_multi_scene_queue_keeps_tail_eligible_after_several_syntheses(setup, monkeypatch):
    import video_workbench.audio_store as module
    store, project, speech, worker = setup
    for index in range(7):
        project = store.save_scene('alice', project['id'], dict(SCENE, title=f'镜头{index + 2}'))
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    for index in range(8):
        begin(store, project, key=f'speech-queued-{index}', index=index)
        project = store.project('alice', project['id'])
    for index in range(8):
        worker.tick()
        clock[0] += 35
    assert len(speech.requests) == 8
    assert all(scene['audio_operation']['status'] == 'completed'
               for scene in store.project('alice', project['id'])['scenes'])


def test_expired_queued_operation_never_reaches_provider(setup, monkeypatch):
    import video_workbench.audio_store as module
    store, project, speech, worker = setup
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    op = begin(store, project)
    clock[0] += 1801
    worker.tick()
    assert speech.requests == []
    assert store.audio_operation(op['id'])['status'] == 'timed_out'


def test_concurrent_ticks_cannot_issue_two_speech_requests(setup):
    store, project, speech, worker = setup
    entered, release = threading.Event(), threading.Event()
    def blocking(_):
        entered.set()
        assert release.wait(5)
    speech.callback = blocking
    begin(store, project)
    thread = threading.Thread(target=worker.tick)
    thread.start()
    try:
        assert entered.wait(5)
        worker.tick()
        assert len(speech.requests) == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(store.project('alice', project['id'])['scenes'][0]['audio_versions']) == 1


def test_changed_administrator_provider_marks_old_audio_stale(setup):
    store, project, speech, worker = setup
    begin(store, project)
    worker.tick()
    scene = store.project('alice', project['id'])['scenes'][0]
    store.adopt_audio('alice', scene['id'], scene['audio_versions'][0]['id'], None)
    store.configure_speech_provider('other-model', 'other-voice')
    latest = store.project('alice', project['id'])['scenes'][0]
    assert latest['speech_settings']['voice'] == 'other-voice'
    assert latest['audio_stale']
    assert latest['audio_versions'][0]['voice'] == 'zm_yunjian'


def test_queued_old_voice_is_rejected_before_calling_reconfigured_provider(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    speech.settings = replace(speech.settings, voice='new-fixed-voice')
    worker.tick()
    assert speech.requests == []
    assert store.audio_operation(op['id'])['error'] == 'speech_provider_changed'


def test_shutdown_interruption_does_not_claim_remote_cancellation(setup):
    store, project, speech, worker = setup
    op = begin(store, project)
    def stopping(_):
        worker.stopping.set()
        raise RuntimeError('simulated interrupted HTTP client')
    speech.callback = stopping
    worker.tick()
    result = store.audio_operation(op['id'])
    assert result['status'] == 'failed'
    assert result['error'] == 'speech_submission_uncertain'
    assert result['upstream_stop_confirmed'] is False


def test_captions_bind_to_adopted_audio_without_erasing_old_cues(setup):
    store, project, speech, worker = setup
    scene_id = project['scenes'][0]['id']
    project = store.upload_audio('alice', scene_id, {'expected_revision': project['revision']},
                                 'caption-audio-1', wav_bytes())
    candidate = project['scenes'][0]['audio_versions'][0]
    store.adopt_audio('alice', scene_id, candidate['id'], None)
    with store.transaction() as db:
        current = store._project(db, 'alice', project['id'])
        current['scenes'][0]['captions'] = dict(audio_version_id=candidate['id'], audio_sha256=candidate['sha256'],
                                                cues=[dict(start=0, end=1, text='字幕')])
        store._save(db, current)
    latest = store.project('alice', project['id'])
    assert latest['scenes'][0]['captions']['stale'] is False
    latest = store.save_scene('alice', project['id'], dict(SCENE, narration='新的旁白'), scene_id, latest['revision'])
    assert latest['scenes'][0]['captions']['stale'] is True
    assert latest['scenes'][0]['captions']['cues'][0]['text'] == '字幕'


def test_unsigned_pcm_silence_is_rejected_and_nonzero_pcm_is_accepted(setup):
    store, project, speech, worker = setup
    def unsigned(value):
        output = io.BytesIO()
        with wave.open(output, 'wb') as stream:
            stream.setparams((1, 1, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(bytes([value]) * 8000)
        return output.getvalue()
    with pytest.raises(ServiceError) as exc:
        store.upload_audio('alice', project['scenes'][0]['id'], {'expected_revision': project['revision']},
                            'unsigned-silent', unsigned(128))
    assert exc.value.status == 422
    latest = store.upload_audio('alice', project['scenes'][0]['id'], {'expected_revision': project['revision']},
                                 'unsigned-sound', unsigned(127))
    assert len(latest['scenes'][0]['audio_versions']) == 1


@pytest.mark.parametrize('failure', ['silent', 'bad_binding', 'unavailable'])
def test_invalid_and_failed_provider_results_never_publish_audio(setup, failure):
    store, project, speech, worker = setup
    op = begin(store, project)
    if failure == 'unavailable':
        speech.fail = ServiceError('speech_unavailable', 503)
    else:
        setattr(speech, failure, True)
    worker.tick()
    assert store.audio_operation(op['id'])['status'] == 'failed'
    assert store.project('alice', project['id'])['scenes'][0]['audio_versions'] == []
    worker.tick()
    assert len(speech.requests) == 1


def test_atomic_claim_is_one_shot_and_wrong_revision_does_not_write(setup):
    store, project, speech, worker = setup
    with pytest.raises(ServiceError, match='revision_conflict'):
        store.start_audio('alice', project['scenes'][0]['id'], {'expected_revision': 999}, 'wrong-revision')
    assert store.active_audio_operations() == []
    op = begin(store, project)
    first = store.claim_audio(op['id'])
    assert first is not None
    assert Store(store.path).claim_audio(op['id']) is None


def test_upload_size_integrity_silence_and_quota_fail_without_partial_rows(setup, monkeypatch):
    import video_workbench.audio_store as module
    store, project, speech, worker = setup
    scene_id = project['scenes'][0]['id']
    for value in [b'not wav', wav_bytes()[:-2], wav_bytes(silent=True)]:
        with pytest.raises(ServiceError) as exc:
            store.upload_audio('alice', scene_id, {'expected_revision': project['revision']}, 'invalid-upload', value)
        assert exc.value.status == 422
    monkeypatch.setattr(module, 'MAX_PROJECT_AUDIO_BYTES', len(wav_bytes()) - 1)
    with pytest.raises(ServiceError, match='audio_storage_limit'):
        store.upload_audio('alice', scene_id, {'expected_revision': project['revision']}, 'quota-upload', wav_bytes())
    assert store.project('alice', project['id'])['scenes'][0]['audio_versions'] == []
    assert store.active_audio_operations() == []
