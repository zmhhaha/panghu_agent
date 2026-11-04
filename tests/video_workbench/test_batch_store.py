"""Batch journals use real SQLite and wholly synthetic model/audio/media results."""
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
import wave

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.batch_store import BatchStore
from video_workbench.batch_workflow import BatchWorkflow
from video_workbench.audio_workflow import AudioWorkflow
from video_workbench.motion import example_plan
from video_workbench.motion_store import MotionStore
from video_workbench.motion_timing import MotionTiming
from video_workbench.production import Production
from video_workbench.speech import SpeechAudio, inspect_wav
from video_workbench.store import Store


OWNER = 'alice'
SCENE = dict(title='请求与队列', narration='请求到达。进入队列。执行节点开始工作。',
             visual_description='请求、队列、执行节点逐步出现。', duration_seconds=3, format='svg')
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='2905162d925fc87fb8242e24ed0bbf025d307ea1', node='v22.20.0',
    browser='HeadlessChrome/141.0.7390.54', ffmpeg='ffmpeg version 5.1.7-0+deb12u1', gsap='3.14.2',
    gsap_sha256='a' * 64, font_sha256='f' * 64, dpkg_sha256='d' * 64,
    npm_lock_sha256='e' * 64, image_digest='sha256:' + '1' * 64)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / 'batch.db')
    motion = MotionStore(store)
    timing = MotionTiming(motion)
    renders = []
    def render(snapshot, directory, **kwargs):
        renders.append(deepcopy(snapshot))
        path = directory / 'synthetic.mp4'
        path.write_bytes(b'synthetic-media-not-a-quality-sample')
        return dict(path=path, duration_seconds=snapshot['duration_seconds'])
    production = Production(store, motion_store=motion, motion_timing=timing,
                            motion_renderer=render, motion_enabled=True, runtime_fingerprint=RUNTIME)
    batches = BatchStore(store, motion, production)
    return SimpleNamespace(store=store, motion=motion, timing=timing, production=production,
                           batches=batches, renders=renders)


def project(env, count=2):
    p = env.store.create_project(OWNER, dict(title='批量图解', script=SCENE['narration'], style='清晰图解', production_mode='motion'))
    for index in range(count):
        p = env.store.save_scene(OWNER, p['id'], dict(SCENE, title=f'第{index + 1}镜'))
    return env.motion.project(OWNER, p['id'])


def plan(env, p, steps=('motion', 'audio', 'draft')):
    request = dict(expected_revision=p['revision'], scene_ids=[s['id'] for s in p['scenes']], steps=list(steps), reuse_current=True)
    preview = env.batches.preview(OWNER, p['id'], request)
    return request, preview


def start(env, p, steps=('motion', 'audio', 'draft'), idem='start-batch-test'):
    request, preview = plan(env, p, steps)
    return env.batches.start(OWNER, p['id'], dict(request, plan_sha256=preview['plan_sha256']), idem)


def complete_motion(env, child):
    env.motion.complete(child['id'], dict(schema='video.motion-result.v1', input_sha256=child['input_sha256'],
                                         plan=example_plan(child['document']['beats'])))


def complete_audio(env, child):
    env.store.claim_audio(child['id'])
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x04\x08' * 72000)
    data = output.getvalue()
    audio = SpeechAudio(**vars(inspect_wav(data)), data=data, sha256=hashlib.sha256(data).hexdigest(),
                        input_sha256=child['provider_input_sha256'])
    env.store.finish_audio(child['id'], audio=audio)


def raw(env, batch_id):
    with env.store.transaction() as db:
        return env.batches._read(db, batch_id, OWNER)


def count(env, table):
    with env.store.transaction() as db:
        return db.execute('SELECT count(*) FROM ' + table).fetchone()[0]


def finish(env, batch_id):
    for _ in range(50):
        env.batches.tick()
        for child in env.motion.active_operations():
            complete_motion(env, child)
        for child in env.store.active_audio_operations():
            complete_audio(env, child)
        env.production.tick()
        result = env.batches.get(OWNER, batch_id)
        if result['status'] in {'completed', 'partial_failed', 'blocked', 'cancelled'}:
            return result
    raise AssertionError('synthetic batch did not terminate')


def test_preview_is_readonly_counts_tasks_and_normalizes_order(env):
    p = project(env)
    before = env.store.project(OWNER, p['id'])
    request, result = plan(env, p, ('draft', 'audio', 'motion'))
    assert result['estimated_tasks'] == dict(motion=2, audio=2, draft=2)
    assert [i['step'] for i in result['items']] == ['motion', 'audio', 'draft'] * 2
    assert all(i['status'] == 'queued' for i in result['items'])
    assert count(env, 'video_batches') == count(env, 'motion_operations') == count(env, 'audio_operations') == 0
    assert env.store.project(OWNER, p['id']) == before
    with pytest.raises(ServiceError, match='invalid_batch_request'):
        env.batches.preview(OWNER, p['id'], dict(request, reuse_current=False))


def test_two_scene_pipeline_survives_own_revision_changes_and_never_approves(env):
    p = project(env)
    batch = start(env, p)
    done = finish(env, batch['id'])
    assert done['status'] == 'completed'
    assert done['progress']['completed'] == done['progress']['total'] == 6
    assert count(env, 'motion_operations') == count(env, 'audio_operations') == count(env, 'video_renders') == 2
    assert len(env.renders) == 2
    current = env.motion.project(OWNER, p['id'])
    assert current['revision'] > p['revision']
    assert all(s['motion']['head_version_id'] is None and s['head_audio_version_id'] is None for s in current['scenes'])
    assert count(env, 'motion_alignments') == 0
    for item in done['items']:
        if item['step'] == 'draft':
            assert item['motion_version_id'] and item['audio_version_id'] and item['render_id']


def test_new_preview_reuses_exact_candidates_and_completed_drafts(env):
    p = project(env)
    first = finish(env, start(env, p)['id'])
    current = env.motion.project(OWNER, p['id'])
    request, preview = plan(env, current)
    assert preview['estimated_tasks'] == dict(motion=0, audio=0, draft=0)
    assert all(item['status'] == 'reused' for item in preview['items'])
    second = env.batches.start(OWNER, p['id'], dict(request, plan_sha256=preview['plan_sha256']), 'reuse-entire-batch')
    assert second['status'] == 'completed' and second['progress']['reused'] == 6
    assert [i['version_id'] for i in first['items']] == [i['version_id'] for i in second['items']]
    env.batches.tick()
    assert count(env, 'motion_operations') == count(env, 'audio_operations') == count(env, 'video_renders') == 2


def test_create_is_idempotent_before_stale_revision_and_start_is_local_only(env):
    p = project(env, 1)
    request, preview = plan(env, p)
    request['plan_sha256'] = preview['plan_sha256']
    first = env.batches.start(OWNER, p['id'], request, 'same-batch-intent')
    env.batches.tick()
    assert count(env, 'motion_operations') == 1
    assert env.batches.start(OWNER, p['id'], request, 'same-batch-intent')['id'] == first['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        env.batches.start(OWNER, p['id'], dict(request, steps=['motion']), 'same-batch-intent')
    with pytest.raises(ServiceError, match='not_found'):
        env.batches.get('bob', first['id'])


def test_global_fence_is_one_active_child_across_projects_and_ticks(env):
    p1, p2 = project(env, 1), project(env, 1)
    b1 = start(env, p1, ('motion',), 'first-project-batch')
    b2 = start(env, p2, ('motion',), 'second-project-batch')
    for _ in range(4):
        env.batches.tick()
    assert count(env, 'motion_operations') == 1
    complete_motion(env, env.motion.active_operations()[0])
    env.batches.tick()
    assert env.batches.get(OWNER, b1['id'])['status'] == 'completed'
    assert env.batches.get(OWNER, b2['id'])['status'] == 'running'
    assert count(env, 'motion_operations') == 2


def test_crash_after_child_creation_recovers_exact_original_idem_and_request(env, monkeypatch):
    p = project(env, 1)
    batch = start(env, p)
    admit = env.batches._admit
    def crash(batch, item):
        admit(batch, item)
        raise SystemExit('synthetic process exit after child admission')
    monkeypatch.setattr(env.batches, '_admit', crash)
    with pytest.raises(SystemExit):
        env.batches.tick()
    journal = raw(env, batch['id'])['items'][0]
    assert journal['operation_id'] is None and journal['_request']['expected_revision'] == p['revision']
    assert count(env, 'motion_operations') == 1
    recovered = BatchStore(env.store, env.motion, env.production)
    recovered.tick()
    item = recovered.get(OWNER, batch['id'])['items'][0]
    assert item['operation_id'] == env.motion.active_operations()[0]['id']
    assert raw(env, batch['id'])['items'][0]['_request'] == journal['_request']
    assert count(env, 'motion_operations') == 1


def test_material_change_blocks_unsubmitted_items_but_retains_child_result(env):
    p = project(env, 1)
    batch = start(env, p)
    env.batches.tick()
    child = env.motion.active_operations()[0]
    current = env.store.project(OWNER, p['id'])
    env.store.save_scene(OWNER, p['id'], dict(SCENE, narration='已经变更的旁白。'),
                         p['scenes'][0]['id'], current['revision'])
    complete_motion(env, child)
    env.batches.tick()
    result = env.batches.get(OWNER, batch['id'])
    assert result['status'] == 'blocked' and result['error'] == 'batch_inputs_changed'
    assert result['items'][0]['version_id'] is not None
    assert count(env, 'audio_operations') == count(env, 'video_renders') == 0
    assert env.batches.cancel(OWNER, batch['id'])['status'] == 'cancelled'


def test_unrelated_revision_change_before_first_admission_is_not_material(env):
    p = project(env, 1)
    batch = start(env, p, ('motion',))
    env.store.save_scene(OWNER, p['id'], dict(SCENE, title='未选择的新镜头'))
    env.batches.tick()
    assert env.batches.get(OWNER, batch['id'])['status'] == 'running'
    assert count(env, 'motion_operations') == 1


def test_cancel_only_owns_its_child_and_waits_for_real_terminal(env):
    p1, p2 = project(env, 1), project(env, 1)
    batch = start(env, p1)
    env.batches.tick()
    child = env.motion.active_operations()[0]
    foreign = env.motion.start(OWNER, p2['scenes'][0]['id'], dict(expected_revision=p2['revision']), 'foreign-manual-task')
    result = env.batches.cancel(OWNER, batch['id'])
    assert result['status'] == 'cancelling'
    assert all(i['status'] == 'cancelled' for i in result['items'][1:])
    assert env.motion.operation(foreign['id'])['cancel_requested'] is False
    env.motion.update_operation(child['id'], status='cancelled', error=None)
    env.batches.tick()
    assert env.batches.get(OWNER, batch['id'])['status'] == 'cancelled'
    assert count(env, 'audio_operations') == 0


def test_speech_restart_uncertainty_blocks_global_queue_and_forbids_retry(env):
    p1, p2 = project(env, 1), project(env, 1)
    batch = start(env, p1, ('audio',), 'uncertain-audio-batch')
    second = start(env, p2, ('motion',), 'second-after-uncertain')
    env.batches.tick()
    child = env.store.active_audio_operations()[0]
    env.store.claim_audio(child['id'])
    env.store.recover_audio_operations()
    for _ in range(3):
        env.batches.tick()
    blocked = env.batches.get(OWNER, batch['id'])
    assert blocked['status'] == 'blocked' and 'uncertain' in blocked['error']
    assert env.batches.get(OWNER, second['id'])['status'] == 'queued'
    assert count(env, 'motion_operations') == 0 and count(env, 'audio_operations') == 1
    with pytest.raises(ServiceError):
        env.batches.retry(OWNER, batch['id'], env.store.project(OWNER, p1['id'])['revision'],
                          [blocked['items'][0]['id']], 'forbidden-uncertain-retry')


def test_only_explicit_failed_step_retries_without_repeating_successful_audio(env):
    p = project(env, 1)
    batch = start(env, p)
    env.batches.tick()
    failed = env.motion.active_operations()[0]
    env.motion.update_operation(failed['id'], status='failed', error='agent_failed')
    done = finish(env, batch['id'])
    assert done['status'] == 'partial_failed'
    assert [i['status'] for i in done['items']] == ['failed', 'completed', 'failed']
    current = env.store.project(OWNER, p['id'])
    retried = env.batches.retry(OWNER, batch['id'], current['revision'], [done['items'][0]['id']], 'retry-only-motion')
    assert len(retried['items']) == 1 and retried['parent_batch_id'] == batch['id']
    assert finish(env, retried['id'])['status'] == 'completed'
    assert count(env, 'audio_operations') == 1 and count(env, 'motion_operations') == 2
    with pytest.raises(ServiceError, match='batch_retry_not_allowed'):
        env.batches.retry(OWNER, batch['id'], env.store.project(OWNER, p['id'])['revision'],
                          [done['items'][1]['id']], 'cannot-retry-completed')


def test_atomic_adopt_preflights_every_head_and_does_not_verify_timing(env):
    p = project(env)
    done = finish(env, start(env, p)['id'])
    selections = []
    for scene in p['scenes']:
        items = [i for i in done['items'] if i['scene_id'] == scene['id']]
        selections.append(dict(scene_id=scene['id'], motion_version_id=items[0]['version_id'],
            audio_version_id=items[1]['version_id'], expected_motion_head_version_id=None,
            expected_audio_head_version_id=None))
    current = env.motion.project(OWNER, p['id'])
    bad = deepcopy(selections)
    bad[1]['expected_audio_head_version_id'] = 'wrong-head'
    with pytest.raises(ServiceError, match='audio_head_conflict'):
        env.batches.adopt(OWNER, p['id'], current['revision'], bad)
    unchanged = env.motion.project(OWNER, p['id'])
    assert unchanged['revision'] == current['revision']
    assert all(s['motion']['head_version_id'] is None and s['head_audio_version_id'] is None for s in unchanged['scenes'])
    adopted = env.batches.adopt(OWNER, p['id'], current['revision'], selections)
    assert adopted['revision'] == current['revision'] + 1
    assert all(s['motion']['head_version_id'] and s['head_audio_version_id'] for s in adopted['scenes'])
    assert count(env, 'motion_alignments') == 0
    for selected in selections:
        assert env.timing.get(OWNER, selected['motion_version_id'], selected['audio_version_id'])['requires_review'] is True


def test_feature_gate_off_still_cancels_and_reconciles_owned_work(env):
    p = project(env, 1)
    batch = start(env, p, ('motion',))
    workflow = BatchWorkflow(env.batches, enabled=False)
    workflow.tick()
    assert count(env, 'motion_operations') == 0
    assert env.batches.cancel(OWNER, batch['id'])['status'] == 'cancelled'
    workflow.tick()
    assert count(env, 'motion_operations') == 0


def test_saved_anchor_changes_invalidate_draft_reuse_and_a_prepared_plan(env):
    p = project(env, 1)
    done = finish(env, start(env, p)['id'])
    motion_id, audio_id = done['items'][0]['version_id'], done['items'][1]['version_id']
    current = env.motion.project(OWNER, p['id'])
    request, before = plan(env, current)
    assert before['estimated_tasks']['draft'] == 0
    timing = env.timing.get(OWNER, motion_id, audio_id)
    segments = [dict(beat_id=beat['id'], start=index, end=index + 0.8)
                for index, beat in enumerate(timing['beats']['segments'])]
    env.timing.save(OWNER, motion_id, dict(expected_revision=current['revision'], audio_version_id=audio_id,
        expected_alignment_sha256=timing['alignment_sha256'], segments=segments, correction_reason='synthetic anchors'))
    updated = env.motion.project(OWNER, p['id'])
    _, after = plan(env, updated)
    assert after['estimated_tasks'] == dict(motion=0, audio=0, draft=1)
    assert [i['status'] for i in after['items']] == ['reused', 'reused', 'queued']
    assert count(env, 'video_renders') == 1
    with pytest.raises(ServiceError, match='revision_conflict'):
        env.batches.start(OWNER, p['id'], dict(request, plan_sha256=before['plan_sha256']), 'stale-anchor-plan')


def test_changed_compiler_prevents_historical_draft_reuse(env, monkeypatch):
    from video_workbench import batch_store as module
    p = project(env, 1)
    finish(env, start(env, p)['id'])
    current = env.motion.project(OWNER, p['id'])
    assert plan(env, current)[1]['estimated_tasks']['draft'] == 0
    monkeypatch.setattr(module, 'COMPILER_VERSION', 'motion-compiler-next')
    assert plan(env, current)[1]['estimated_tasks'] == dict(motion=0, audio=0, draft=1)


def test_changed_anchors_after_batch_start_block_new_draft_admission(env):
    p = project(env, 1)
    done = finish(env, start(env, p)['id'])
    motion_id, audio_id = done['items'][0]['version_id'], done['items'][1]['version_id']
    current = env.motion.project(OWNER, p['id'])
    # Invalidate just the old runtime cache without altering material inputs.
    with env.store.transaction() as db:
        db.execute('UPDATE video_renders SET data=NULL')
        for row in db.execute('SELECT id,document FROM video_renders').fetchall():
            job = json.loads(row['document']); job['status'] = 'failed'
            db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(job), row['id']))
    batch = start(env, current, ('draft',), 'anchor-bound-draft-batch')
    timing = env.timing.get(OWNER, motion_id, audio_id)
    segments = [dict(beat_id=beat['id'], start=index, end=index + 0.8)
                for index, beat in enumerate(timing['beats']['segments'])]
    env.timing.save(OWNER, motion_id, dict(expected_revision=current['revision'], audio_version_id=audio_id,
        expected_alignment_sha256=timing['alignment_sha256'], segments=segments, correction_reason='changed anchors'))
    env.batches.tick()
    result = env.batches.get(OWNER, batch['id'])
    assert result['status'] == 'blocked' and result['error'] == 'batch_inputs_changed'
    assert count(env, 'video_renders') == 1


@pytest.mark.parametrize('error', ['speech_timeout', 'speech_unavailable', 'speech_submission_uncertain'])
def test_submitted_speech_failure_without_stop_proof_is_fenced_even_without_uncertain_text(env, error):
    p = project(env, 1)
    batch = start(env, p, ('audio',))
    env.batches.tick()
    child = env.store.active_audio_operations()[0]
    env.store.claim_audio(child['id'])
    env.store.finish_audio(child['id'], error=error)
    env.batches.tick()
    result = env.batches.get(OWNER, batch['id'])
    assert result['status'] == 'blocked' and result['items'][0]['status'] == 'blocked'
    with pytest.raises(ServiceError):
        env.batches.retry(OWNER, batch['id'], env.store.project(OWNER, p['id'])['revision'],
                          [result['items'][0]['id']], 'no-uncertain-manual-retry')
    assert count(env, 'audio_operations') == 1
    assert env.batches.cancel(OWNER, batch['id'])['status'] == 'blocked'


def test_provider_preflight_failure_records_no_submission_and_batch_can_continue(env):
    p = project(env)
    batch = start(env, p, ('audio',))
    calls = []
    class Provider:
        settings = SimpleNamespace(model='changed-after-admission', voice='zm_yunjian')
        voice_catalog = [{'id': 'zm_yunjian'}]
        def synthesize(self, *args, **kwargs):
            calls.append(True)
            raise AssertionError('Preflight must reject before synthesis')
    worker = AudioWorkflow(env.store, Provider())
    env.batches.tick()
    first = env.store.active_audio_operations()[0]
    worker.tick()
    failed = env.store.audio_operation(first['id'])
    assert failed['status'] == 'failed' and failed['error'] == 'speech_provider_changed'
    assert failed['submit_attempted'] is True and failed['submission_not_started'] is True
    assert failed['upstream_stop_confirmed'] is True and calls == []
    env.batches.tick()
    current = env.batches.get(OWNER, batch['id'])
    assert current['status'] == 'running' and current['items'][0]['status'] == 'failed'
    assert len(env.store.active_audio_operations()) == 1
    complete_audio(env, env.store.active_audio_operations()[0])
    env.batches.tick()
    done = env.batches.get(OWNER, batch['id'])
    assert done['status'] == 'partial_failed' and done['items'][1]['status'] == 'completed'
    retry = env.batches.retry(OWNER, batch['id'], env.store.project(OWNER, p['id'])['revision'],
                              [done['items'][0]['id']], 'retry-known-preflight-failure')
    assert retry['status'] == 'queued' and len(retry['items']) == 1 and calls == []


@pytest.mark.parametrize('race', ['cancel', 'shutdown'])
def test_preflight_no_submission_evidence_survives_cancel_and_shutdown_races(env, race):
    p = project(env, 1)
    batch = start(env, p, ('audio',))
    env.batches.tick()
    child = env.store.active_audio_operations()[0]
    class Provider:
        settings = SimpleNamespace(model='different-model', voice='zm_yunjian')
        @property
        def voice_catalog(self):
            if race == 'cancel':
                env.store.cancel_audio(OWNER, child['id'])
            else:
                worker.stopping.set()
            return [{'id': 'zm_yunjian'}]
        def synthesize(self, *args, **kwargs):
            raise AssertionError('This preflight never submits')
    worker = AudioWorkflow(env.store, Provider())
    worker.tick()
    finished = env.store.audio_operation(child['id'])
    assert finished['status'] == ('cancelled' if race == 'cancel' else 'failed')
    assert finished['upstream_stop_confirmed'] is True and finished['submission_not_started'] is True
    assert 'uncertain' not in (finished['error'] or '') and 'unconfirmed' not in (finished['error'] or '')
    env.batches.tick()
    assert env.batches.get(OWNER, batch['id'])['status'] == 'partial_failed'


def test_timeout_raised_inside_synthesis_still_blocks_batch(env):
    p = project(env, 1)
    batch = start(env, p, ('audio',))
    calls = []
    class Provider:
        settings = SimpleNamespace(model='kokoro-rk3588', voice='zm_yunjian')
        voice_catalog = [{'id': 'zm_yunjian'}]
        def synthesize(self, *args, **kwargs):
            calls.append(True)
            raise ServiceError('speech_timeout', 504)
    worker = AudioWorkflow(env.store, Provider())
    env.batches.tick()
    child = env.store.active_audio_operations()[0]
    worker.tick()
    failed = env.store.audio_operation(child['id'])
    assert failed['status'] == 'failed' and failed['error'] == 'speech_timeout'
    assert failed['upstream_stop_confirmed'] is False and not failed.get('submission_not_started')
    assert calls == [True]
    env.batches.tick()
    assert env.batches.get(OWNER, batch['id'])['status'] == 'blocked'
    assert count(env, 'audio_operations') == 1
