"""Lab creative source reaches owner-scoped adoption and frozen production.

Agent artifacts and media I/O are synthetic; SQLite, WAV measurement, source
binding, timing review and snapshot recompilation use the real implementation.
"""
from copy import deepcopy
import hashlib

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_animation_experiment import validate_experiment_result
from video_workbench.creative_contract import build_scene_input, collect_scene_result, base_scene_result
from video_workbench.creative_snapshot import freeze_snapshot_v4, verify_snapshot_v4
from video_workbench.motion import estimated_alignment, make_beats
from video_workbench.motion_store import MotionStore, operation_diagram_kind, semantic_binding
from video_workbench.motion_timing import MotionTiming
from video_workbench.motion_workflow import MotionWorkflow
from video_workbench.production import Production
from video_workbench.store import Store
from test_motion_snapshot import RUNTIME, audio, static_scene, motion_scene


OWNER = 'creative-owner'
NARRATION = '请求到达。进入队列。执行节点开始工作。'
HTML = '''<!doctype html><html><body><svg viewBox="0 0 1280 720"><circle id="dot" r="40" cy="300"/></svg>
<script>window.__animationExperiment={seek(t){document.getElementById('dot').setAttribute('cx',100+t*100)}};
window.__animationExperiment.seek(0);</script></body></html>'''


def result(document, html=HTML):
    files = {'scene.html': html}
    value = dict(schema='video.animation-experiment-result.v1', input_sha256=digest(document),
        prompt_revision=document['prompt_revision'], engine='html-svg',
        strategy=dict(description='沿叙事节拍移动对象，逐步解释关系。', tradeoffs='不包含三维视角。'),
        files=files, files_sha256={name: hashlib.sha256(raw.encode()).hexdigest() for name, raw in files.items()})
    return validate_experiment_result(value, digest(document), document)


def source_scene():
    recording = audio()
    beats = make_beats(NARRATION)
    alignment = estimated_alignment(beats, recording['sha256'], recording['duration_seconds'])
    document = build_scene_input(scene_id='creative-scene', title='真实旁白动画', visual_description='请求进入队列并开始执行。',
        narration=NARRATION, beats=beats, alignment=alignment, audio_sha256=recording['sha256'], duration_seconds=1.5)
    package = collect_scene_result(document, result(document))
    return dict(id='creative-scene', title='真实旁白动画', narration=NARRATION, visual_mode='creative', audio=recording,
                creative=dict(id='creative-candidate', package=package, beats=beats, alignment=alignment))


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / 'creative.db')
    project = store.create_project(OWNER, dict(title='正式制作', script='项目总稿', style='清晰图解', production_mode='motion'))
    project = store.save_scene(OWNER, project['id'], dict(title='请求处理', narration=NARRATION,
        visual_description='对象连续移动，解释请求经过队列和执行节点。', duration_seconds=2, format='svg'))
    scene_id = project['scenes'][0]['id']
    motion = MotionStore(store)
    timing = MotionTiming(motion)
    return store, motion, timing, project['id'], scene_id


def adopted_audio(state, *, value=1, key='recording'):
    store, _, _, project_id, scene_id = state
    project = store.project(OWNER, project_id)
    project = store.upload_audio(OWNER, scene_id, dict(expected_revision=project['revision']),
        key, audio(value)['data'], source_name='narration.wav')
    scene = project['scenes'][0]
    identity = scene['audio_versions'][-1]['id']
    store.adopt_audio(OWNER, scene_id, identity, scene.get('head_audio_version_id'))
    return identity


def generated(state, *, key='creative', base_id=None, html=HTML):
    store, motion, _, project_id, scene_id = state
    request = dict(expected_revision=store.project(OWNER, project_id)['revision'], diagram_kind='creative')
    if base_id:
        request.update(base_version_id=base_id, instruction='改用另一条运动曲线。')
    started = motion.start(OWNER, scene_id, request, key)
    operation = motion.operation(started['id'])
    completed = motion.complete(operation['id'], result(operation['document'], html))
    return operation, completed


def production(state):
    store, motion, timing, _, _ = state
    return Production(store, motion_store=motion, motion_timing=timing,
                      motion_enabled=True, runtime_fingerprint=RUNTIME)


def test_generation_requires_current_audio_before_admission(state):
    store, motion, _, project_id, scene_id = state
    with pytest.raises(ServiceError, match='missing_current_audio'):
        motion.start(OWNER, scene_id, dict(expected_revision=store.project(OWNER, project_id)['revision'],
                     diagram_kind='creative'), 'missing-audio')
    assert motion.active_operations() == []


def test_creative_candidate_freezes_audio_policy_and_source_without_auto_adoption(state):
    identity = adopted_audio(state)
    store, motion, _, project_id, scene_id = state
    operation, completed = generated(state)
    assert operation_diagram_kind(operation) == 'creative'
    assert operation['audio_version_id'] == identity
    context = operation['document']['constraints']['production']
    assert context['audio_sha256'] == audio()['sha256']
    assert operation['document']['constraints']['duration_seconds'] == 1.5
    version = motion.version(OWNER, completed['version_id'])
    assert version['diagram_kind'] == 'creative'
    assert version['plan']['result']['files']['scene.html'] == HTML
    project = motion.project(OWNER, project_id)
    assert project['scenes'][0]['motion']['head_version_id'] is None
    assert project['scenes'][0]['motion']['versions'][0]['template'] == 'creative'
    adopted = motion.adopt(OWNER, scene_id, version['id'], None, project['revision'])
    assert adopted['scenes'][0]['motion']['head_version_id'] == version['id']
    assert store.project(OWNER, project_id)['scenes'][0]['head_audio_version_id'] == identity


def test_different_recording_makes_creative_stale_and_refuses_adoption(state):
    adopted_audio(state)
    _, motion, _, project_id, scene_id = state
    _, completed = generated(state)
    adopted_audio(state, value=2, key='replacement')
    version = motion.version(OWNER, completed['version_id'])
    assert version['stale'] is True
    project = motion.project(OWNER, project_id)
    assert project['scenes'][0]['motion']['versions'][0]['stale'] is True
    with pytest.raises(ServiceError, match='creative_audio_binding_mismatch'):
        motion.adopt(OWNER, scene_id, version['id'], None, project['revision'])


@pytest.mark.parametrize('change', ['narration', 'visual_description', 'voice', 'speed'])
def test_changed_semantics_or_speech_settings_invalidate_creative_head(state, change):
    adopted_audio(state)
    store, motion, _, project_id, scene_id = state
    _, completed = generated(state)
    project = motion.project(OWNER, project_id)
    project = motion.adopt(OWNER, scene_id, completed['version_id'], None, project['revision'])
    if change == 'voice':
        store.configure_speech_provider('kokoro-rk3588', 'zm_yunjian', ['zm_yunjian', 'zm_yunxi'])
        store.save_project_speech_settings(OWNER, project_id,
            dict(expected_revision=project['revision'], voice='zm_yunxi'))
    elif change == 'speed':
        store.save_speech_settings(OWNER, scene_id, dict(expected_revision=project['revision'], speed=1.1))
    else:
        original = project['scenes'][0]
        fields = {key: original[key] for key in ('title', 'narration', 'visual_description', 'duration_seconds', 'format')}
        fields[change] += '变化。'
        store.save_scene(OWNER, project_id, fields, scene_id, project['revision'])
    project = motion.project(OWNER, project_id)
    assert project['scenes'][0]['motion']['head_stale'] is True
    assert motion.version(OWNER, completed['version_id'])['stale'] is True
    project = motion.set_mode(OWNER, scene_id, 'static', project['revision'])
    with pytest.raises(ServiceError):
        motion.set_mode(OWNER, scene_id, 'motion', project['revision'])


def test_cross_revision_creative_request_must_pin_the_current_adopted_recording(state):
    original_audio = adopted_audio(state)
    store, motion, _, project_id, scene_id = state
    original = motion.project(OWNER, project_id)
    request = dict(expected_revision=original['revision'], diagram_kind='creative',
        expected_semantic_binding_sha256=semantic_binding(original, original['scenes'][0]))
    # A new selected recording changes the clock without changing scene text.
    adopted_audio(state, value=2, key='changed-audio-during-click')
    with pytest.raises(ServiceError, match='revision_conflict'):
        motion.start(OWNER, scene_id, request, 'unbound-audio')
    with pytest.raises(ServiceError, match='creative_audio_binding_mismatch'):
        motion.start(OWNER, scene_id, dict(request, expected_audio_version_id=original_audio), 'old-audio')
    current = store.project(OWNER, project_id)['scenes'][0]['head_audio_version_id']
    operation = motion.start(OWNER, scene_id, dict(request, expected_audio_version_id=current), 'current-audio')
    assert motion.operation(operation['id'])['audio_version_id'] == current


def test_revision_receives_original_source_and_unchanged_result_is_not_saved(state):
    adopted_audio(state)
    _, motion, _, _, _ = state
    _, completed = generated(state)
    original = completed['version_id']
    operation, unchanged = generated(state, key='revise', base_id=original)
    assert base_scene_result(operation['document'])['files']['scene.html'] == HTML
    assert unchanged['status'] == 'needs_input'
    assert unchanged['error'] == 'motion_revision_unchanged'
    assert unchanged['version_id'] is None
    _, changed = generated(state, key='revise-again', base_id=original, html=HTML.replace('t*100', 't*t*100'))
    assert motion.version(OWNER, changed['version_id'])['parent_version_id'] == original


def test_frozen_mixed_v4_preserves_generated_source_and_existing_compilers():
    sources = [static_scene(), source_scene(), motion_scene()]
    frozen = freeze_snapshot_v4('project', 7, '混合成片', sources, runtime_fingerprint=RUNTIME)
    assert [row['visual_mode'] for row in frozen['scenes']] == ['static', 'creative', 'motion']
    assert frozen['scenes'][1]['creative']['package']['result']['files']['scene.html'] == HTML
    assert frozen['scenes'][1]['creative']['timeline']['audio_seconds'] == 1.5
    assert verify_snapshot_v4(frozen) == frozen
    assert frozen['scenes'][2]['motion']['timeline']['compiler'].startswith('motion-compiler-')


def test_resigned_outer_snapshot_cannot_change_generated_source():
    frozen = freeze_snapshot_v4('project', 7, '源码冻结', [source_scene()], runtime_fingerprint=RUNTIME)
    changed = deepcopy(frozen)
    changed['scenes'][0]['creative']['package']['result']['files']['scene.html'] += '<p>changed</p>'
    changed['sha256'] = digest({k: v for k, v in changed.items() if k != 'sha256'})
    with pytest.raises(ServiceError):
        verify_snapshot_v4(changed)
    assert verify_snapshot_v4(frozen) == frozen


def test_production_preview_review_adoption_and_delivery_follow_same_candidate(state):
    identity = adopted_audio(state)
    _, motion, timing, project_id, scene_id = state
    _, completed = generated(state)
    version_id = completed['version_id']
    project = motion.project(OWNER, project_id)
    service = production(state)
    request = dict(expected_revision=project['revision'], purpose='draft', scene_id=scene_id,
                   motion_version_id=version_id, audio_version_id=identity)
    draft = service.snapshot_v2(OWNER, project_id, request)
    assert draft['schema'] == 'video.render-snapshot.v4'
    assert draft['scenes'][0]['creative']['id'] == version_id
    current = timing.get(OWNER, version_id, identity)
    assert current['requires_review'] is True
    with pytest.raises(ServiceError, match='creative_timing_requires_regeneration'):
        timing.save(OWNER, version_id, dict(expected_revision=project['revision'], audio_version_id=identity,
            expected_alignment_sha256=current['alignment_sha256'], segments=current['alignment']['segments']))
    project = motion.adopt(OWNER, scene_id, version_id, None, project['revision'])
    with pytest.raises(ServiceError, match='motion_timing_needs_review'):
        service.snapshot_v2(OWNER, project_id, dict(expected_revision=project['revision'], purpose='delivery'))
    timing.save(OWNER, version_id, dict(expected_revision=project['revision'], audio_version_id=identity,
        expected_alignment_sha256=current['alignment_sha256']), verify=True)
    project = motion.project(OWNER, project_id)
    delivery = service.snapshot_v2(OWNER, project_id, dict(expected_revision=project['revision'], purpose='delivery'))
    assert delivery['scenes'][0]['creative']['alignment']['review_status'] == 'verified'
    assert delivery['scenes'][0]['creative']['package'] == draft['scenes'][0]['creative']['package']
    assert verify_snapshot_v4(delivery) == delivery


def test_workflow_routes_creative_to_shared_profile_gateway_with_exact_input(state):
    adopted_audio(state)
    store, motion, _, project_id, scene_id = state
    started = motion.start(OWNER, scene_id, dict(expected_revision=store.project(OWNER, project_id)['revision'],
        diagram_kind='creative'), 'via-common-agent')

    class Gateway:
        def upload(self, document):
            self.document = deepcopy(document)
            return dict(input_id='input')

        def submit(self, operation_id, document, deadline_at, input_ref):
            assert document == self.document
            return dict(job_id='creative-job')

        def poll(self, identity):
            return dict(state='completed')

        def collect(self, identity, checksum, input_document):
            assert input_document == self.document
            assert digest(input_document) == checksum
            return result(input_document)

    workflow = MotionWorkflow(motion, enabled=True, creative_enabled=True, creative_gateway=Gateway())
    assert workflow.require_gateway('creative') is workflow.creative_gateway
    workflow.tick()
    assert motion.public_operation(OWNER, started['id'])['status'] == 'completed'
    with pytest.raises(ServiceError, match='creative_disabled'):
        MotionWorkflow(motion, enabled=True, creative_gateway=Gateway()).require_gateway('creative')


def test_creative_ambiguous_submission_recovers_original_job_after_restart(state):
    adopted_audio(state)
    store, motion, _, project_id, scene_id = state
    started = motion.start(OWNER, scene_id, dict(expected_revision=store.project(OWNER, project_id)['revision'],
        diagram_kind='creative'), 'ambiguous-creative')

    class Gateway:
        posts = lookups = 0

        def upload(self, document):
            self.document = deepcopy(document)
            return dict(input_id='one-input')

        def submit(self, operation_id, document, deadline_at, input_ref):
            self.posts += 1
            raise ServiceError('service_unavailable', 503)

        def lookup(self, operation_id):
            self.lookups += 1
            assert operation_id == started['id']
            return dict(job_id='already-admitted-creative')

        def poll(self, identity):
            assert identity == 'already-admitted-creative'
            return dict(state='completed')

        def collect(self, identity, checksum, document):
            assert document == self.document and digest(document) == checksum
            return result(document)

    gateway = Gateway()
    MotionWorkflow(motion, enabled=True, creative_enabled=True, creative_gateway=gateway).tick()
    assert motion.operation(started['id'])['error'] == 'submission_uncertain'
    restarted = MotionStore(Store(store.path))
    MotionWorkflow(restarted, enabled=True, creative_enabled=True, creative_gateway=gateway).tick()
    assert restarted.public_operation(OWNER, started['id'])['status'] == 'completed'
    assert gateway.posts == gateway.lookups == 1


def test_creative_cancel_uses_same_gateway_and_never_admits_again(state):
    adopted_audio(state)
    store, motion, _, project_id, scene_id = state
    started = motion.start(OWNER, scene_id, dict(expected_revision=store.project(OWNER, project_id)['revision'],
        diagram_kind='creative'), 'cancel-creative')

    class Gateway:
        posts = cancels = 0
        status = 'running'

        def upload(self, document):
            return dict(input_id='one-input')

        def submit(self, operation_id, document, deadline_at, input_ref):
            self.posts += 1
            return dict(job_id='same-creative-job')

        def poll(self, identity):
            assert identity == 'same-creative-job'
            return dict(state=self.status)

        def cancel(self, identity):
            assert identity == 'same-creative-job'
            self.cancels += 1
            self.status = 'cancelled'

    gateway = Gateway()
    workflow = MotionWorkflow(motion, enabled=True, creative_enabled=True, creative_gateway=gateway)
    workflow.tick()
    motion.cancel(OWNER, started['id'])
    workflow.tick()
    workflow.tick()
    assert motion.public_operation(OWNER, started['id'])['status'] == 'cancelled'
    assert gateway.posts == gateway.cancels == 1
    assert motion.project(OWNER, project_id)['scenes'][0]['motion']['versions'] == []
