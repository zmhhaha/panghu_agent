"""Project-wide content adoption, actual-input bindings and durable job recovery."""
from copy import deepcopy
import io
import json
import wave

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench import explanation_store as module
from video_workbench.explanation_store import ExplanationStore, visual_generation_description
from video_workbench.explanation_workflow import ExplanationWorkflow, CANCEL_RETRY_DELAYS
from video_workbench.motion_store import MotionStore
from video_workbench.production import Production
from video_workbench.store import Store


OWNER = 'alice'
BRIEF = dict(audience='技术入门者', goal='理解请求排队的作用', tone='conversational')


def fields(title='请求排队'):
    return dict(title=title, narration='先接收请求。再放入队列。最后执行任务。',
                visual_description='展示请求、队列、工作节点的关系。', duration_seconds=15, format='svg')


@pytest.fixture
def state(tmp_path):
    base = Store(tmp_path / 'state.db')
    base.configure_speech_provider('kokoro-rk3588', 'zm_yunjian', ['zm_yunjian', 'zm_yunxi'])
    project = base.create_project(OWNER, dict(title='示例项目', script='先接收请求。再放入队列。最后执行任务。', style='清晰图解'))
    for title in ['请求排队', '任务执行']:
        project = base.save_scene(OWNER, project['id'], fields(title))
    return base, ExplanationStore(base), project['id']


def begin(state, key='plan-1', request=None):
    base, explanation, project_id = state
    request = request or dict(expected_revision=base.project(OWNER, project_id)['revision'], brief=BRIEF)
    return explanation.operation(explanation.start(OWNER, project_id, request, key)['id'])


def result_for(op):
    return dict(schema_version='video-explanation-result.v1', input_sha256=op['input_sha256'],
        summary='用排队的例子解释请求处理。', warnings=[], scenes=[dict(scene_id=s['scene_id'],
            title=s['title'] + '：一步步看', narration='请求来了，先排队。然后，轮到工作节点来处理。',
            visual_description='用箭头串起请求、队列和工作节点。', duration_seconds=18,
            pronunciation={'工作节点': '工作节点'}, key_points=['先排队', '再处理'], visual_strategy='flow',
            visual_steps=['出现请求和队列', '请求进入队列，工作节点接走任务'], delivery_note='讲到排队时稍作停顿。',
            source_quotes=['再放入队列。']) for s in op['document']['scenes']])


def complete(state, op=None):
    op = op or begin(state)
    candidate = state[1].complete(op['id'], result_for(op))
    return candidate['version_id']


def adopt(state, version_id, **override):
    base, explanation, project_id = state
    request = dict(expected_revision=base.project(OWNER, project_id)['revision'], version_id=version_id,
                   expected_head_version_id=explanation.project(OWNER, project_id)['head_version_id'])
    request.update(override)
    return explanation.adopt(OWNER, project_id, request)


def test_candidate_is_explicit_and_adoption_is_project_wide_without_media_calls(state):
    base, explanation, project_id = state
    original = base.project(OWNER, project_id)
    op = begin(state)
    public = explanation.public_operation(OWNER, op['id'])
    assert public['created_at'] == op['created_at']
    assert set(public) == {'id', 'kind', 'status', 'error', 'job_id', 'version_id', 'created_at'}
    assert ExplanationStore(Store(base.path)).public_operation(OWNER, op['id'])['created_at'] == op['created_at']
    assert op['document']['project']['voice'] == 'zm_yunjian'
    version_id = complete(state, op)
    pending = explanation.project(OWNER, project_id)
    assert pending['head_version_id'] is None and pending['head_stale'] is False
    assert pending['versions'][0]['result'] == result_for(op)
    assert pending['versions'][0]['adoptable'] and not pending['versions'][0]['stale']
    assert base.project(OWNER, project_id)['scenes'] == original['scenes']
    project = adopt(state, version_id)
    assert [s['id'] for s in project['scenes']] == [s['id'] for s in original['scenes']]
    assert all(s['narration'] == result_for(op)['scenes'][0]['narration'] for s in project['scenes'])
    assert all(s['explanation']['version_id'] == version_id for s in project['scenes'])
    assert all(s['format'] == 'svg' for s in project['scenes'])
    assert base.active_operations() == []
    assert base.active_audio_operations() == []
    pending = explanation.project(OWNER, project_id)
    assert pending['head_version_id'] == version_id and not pending['head_stale']
    assert not pending['versions'][0]['stale']
    # Re-adopting an already applied version is an explicit no-op.
    assert adopt(state, version_id)['revision'] == project['revision']


def _wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x04\x08' * 24000)
    return output.getvalue()


def test_adoption_preserves_voice_speed_formats_and_media_history_but_invalidates_old_heads(state):
    base, explanation, project_id = state
    project = base.project(OWNER, project_id)
    first, second = [s['id'] for s in project['scenes']]
    project = base.save_project_speech_settings(OWNER, project_id,
        dict(expected_revision=project['revision'], voice='zm_yunxi'))
    project = base.save_speech_settings(OWNER, first, dict(expected_revision=project['revision'], speed=1.15,
                                                         pronunciation={'队列': '队 列'}))
    project = base.save_scene(OWNER, project_id, dict(format='html'), second, project['revision'])
    visual = base.start(OWNER, project_id, 'generate', dict(expected_revision=project['revision']), 'visual-1', scene_id=first)
    base.complete(visual['id'], dict(format='svg', source='<svg/>', summary='原画面'))
    visual_id = base.project(OWNER, project_id)['scenes'][0]['versions'][0]['id']
    project = base.adopt(OWNER, first, visual_id, None)
    project = base.upload_audio(OWNER, first, dict(expected_revision=project['revision']), 'audio-upload-1', _wav())
    audio_id = project['scenes'][0]['audio_versions'][0]['id']
    base.adopt_audio(OWNER, first, audio_id, None)
    version_id = complete(state)
    project = adopt(state, version_id)
    scene = project['scenes'][0]
    assert project['speech_settings']['voice'] == 'zm_yunxi'
    assert scene['speech_settings']['speed'] == 1.15
    assert scene['speech_settings']['pronunciation'] == {'工作节点': '工作节点'}
    assert [s['format'] for s in project['scenes']] == ['svg', 'html']
    assert scene['head_version_id'] is None and scene['versions'][0]['id'] == visual_id
    assert base.version(OWNER, visual_id)['source'] == '<svg/>'
    assert scene['head_audio_version_id'] == audio_id and scene['audio_stale']
    assert scene['audio_versions'][0]['id'] == audio_id and scene['audio_versions'][0]['stale']
    assert base.audio_version(OWNER, audio_id)['data'] == _wav()


def test_adopted_composition_reaches_existing_visual_generation_schema_and_stops_after_manual_edit(state):
    base, explanation, project_id = state
    project = adopt(state, complete(state))
    scene = project['scenes'][0]
    op = base.start(OWNER, project_id, 'generate', dict(expected_revision=project['revision']), 'visual-plan', scene_id=scene['id'])
    document = base.operation(op['id'])['document']
    assert '先排队' in document['visual_description'] and '请求进入队列' in document['visual_description']
    assert '画面展开顺序' in document['visual_description']
    assert scene['narration'] not in document['visual_description']
    assert len(document['visual_description']) <= 10000
    base.update_operation(op['id'], status='failed')
    project = base.project(OWNER, project_id)
    project = base.save_scene(OWNER, project_id, dict(visual_description='完全改为新的图示'), scene['id'], project['revision'])
    assert visual_generation_description(project['scenes'][0]) == '完全改为新的图示'
    assert explanation.project(OWNER, project_id)['head_stale']


def test_oversize_composition_is_readable_but_cannot_generate_by_truncating_requirements(state):
    base, explanation, project_id = state
    op = begin(state)
    result = result_for(op)
    for scene in result['scenes']:
        scene.update(visual_description='图' * 8000, key_points=['关键' * 60] * 4, visual_steps=['变化' * 120] * 6)
    version_id = explanation.complete(op['id'], result)['version_id']
    project = adopt(state, version_id)
    description = visual_generation_description(project['scenes'][0])
    assert len(description) > 10000
    assert '图' * 8000 in description
    assert description.count('变化' * 120) == 6
    with pytest.raises(ServiceError, match='explanation_visual_requires_split'):
        base.start(OWNER, project_id, 'generate', dict(expected_revision=project['revision']),
                   'oversize-visual', scene_id=project['scenes'][0]['id'])
    assert base.project(OWNER, project_id)['scenes'][0]['explanation']['version_id'] == version_id


@pytest.mark.parametrize('changed', ['title', 'script', 'style', 'voice', 'scene_title', 'narration',
                                   'visual_description', 'duration_seconds', 'format', 'speed', 'pronunciation', 'order'])
def test_actual_content_changes_make_late_candidate_stale_and_unadoptable(state, changed):
    base, explanation, project_id = state
    op = begin(state)
    with base.transaction() as db:
        project = base._project(db, OWNER, project_id)
        scene = project['scenes'][0]
        if changed in {'title', 'script', 'style'}:
            project[changed] += '（更新）'
        elif changed == 'voice':
            project['speech_settings'] = dict(voice='zm_yunxi')
        elif changed == 'order':
            project['scenes'].reverse()
        elif changed == 'speed':
            scene['speech_settings'] = dict(speed=1.2)
        elif changed == 'pronunciation':
            scene['speech_settings'] = dict(pronunciation={'请求': '请 求'})
        elif changed == 'scene_title':
            scene['title'] += '更新'
        else:
            scene[changed] = {'format': 'html', 'duration_seconds': 19}.get(changed, '更新后的内容')
        base._save(db, project)
    version_id = complete(state, op)
    candidate = explanation.version(OWNER, version_id)
    assert candidate['stale'] and not candidate['adoptable']
    before = base.project(OWNER, project_id)
    with pytest.raises(ServiceError, match='explanation_plan_stale'):
        adopt(state, version_id)
    assert base.project(OWNER, project_id) == before


def test_unrelated_candidate_and_media_operations_do_not_invalidate_binding(state):
    base, explanation, project_id = state
    op = begin(state)
    first_id = complete(state, op)
    second_id = complete(state, begin(state, 'plan-2'))
    project = base.project(OWNER, project_id)
    scene = project['scenes'][0]
    visual = base.start(OWNER, project_id, 'generate', dict(expected_revision=project['revision']), 'extra-visual', scene_id=scene['id'])
    base.complete(visual['id'], dict(source='<svg/>', format='svg', summary='只添加版本'))
    project = base.project(OWNER, project_id)
    base.adopt(OWNER, scene['id'], project['scenes'][0]['versions'][0]['id'], None)
    assert all(not v['stale'] for v in explanation.project(OWNER, project_id)['versions'])
    assert {v['id'] for v in explanation.project(OWNER, project_id)['versions']} == {first_id, second_id}
    adopt(state, first_id)
    assert explanation.version(OWNER, second_id)['stale']


def test_idempotency_replay_precedes_revision_and_capacity_checks(state, monkeypatch):
    base, explanation, project_id = state
    request = dict(expected_revision=base.project(OWNER, project_id)['revision'], brief=BRIEF)
    op = begin(state, request=request)
    complete(state, op)
    monkeypatch.setattr(module, 'MAX_PROJECT_OPERATIONS', 1)
    assert explanation.start(OWNER, project_id, request, 'plan-1')['id'] == op['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        explanation.start(OWNER, project_id, dict(request, instruction='改变重点'), 'plan-1')
    with pytest.raises(ServiceError, match='explanation_operation_limit'):
        begin(state, 'new')


def test_latest_brief_and_instruction_survive_restart_without_affecting_candidate_binding(state):
    base, explanation, project_id = state
    empty = explanation.project(OWNER, project_id)
    assert empty['brief'] is None and empty['instruction'] == ''
    first_id = complete(state)
    request = dict(expected_revision=base.project(OWNER, project_id)['revision'],
                   brief=dict(audience='后端工程师', goal='解释排队如何削峰', tone='precise'),
                   instruction='保留原稿数字，重点比较入队与出队。')
    latest = begin(state, 'plan-latest-brief', request)
    recovered = ExplanationStore(Store(base.path))
    envelope = recovered.project(OWNER, project_id)
    assert envelope['operation']['id'] == latest['id']
    assert envelope['brief'] == request['brief']
    assert envelope['instruction'] == request['instruction']
    assert not recovered.version(OWNER, first_id)['stale']
    with pytest.raises(ServiceError, match='not_found'):
        recovered.project('bob', project_id)


def test_owner_isolation_and_atomic_revision_head_conflicts(state):
    base, explanation, project_id = state
    op = begin(state)
    version_id = complete(state, op)
    for action in [lambda: explanation.project('bob', project_id), lambda: explanation.version('bob', version_id),
                   lambda: explanation.public_operation('bob', op['id']), lambda: explanation.cancel('bob', op['id']),
                   lambda: explanation.adopt('bob', project_id, dict(expected_revision=1, version_id=version_id,
                                                                    expected_head_version_id=None))]:
        with pytest.raises(ServiceError, match='not_found'):
            action()
    before = base.project(OWNER, project_id)
    with pytest.raises(ServiceError, match='revision_conflict'):
        adopt(state, version_id, expected_revision=1)
    with pytest.raises(ServiceError, match='explanation_head_conflict'):
        adopt(state, version_id, expected_head_version_id='incorrect-head')
    assert base.project(OWNER, project_id) == before


@pytest.mark.parametrize('kind', ['visual', 'audio', 'render', 'motion'])
def test_adoption_refuses_any_active_project_media_work_atomically(state, kind):
    base, explanation, project_id = state
    version_id = complete(state)
    project = base.project(OWNER, project_id)
    # Work on the second scene must also block this project-wide adoption.
    scene_id = project['scenes'][1]['id']
    request = dict(expected_revision=project['revision'])
    if kind == 'visual':
        base.start(OWNER, project_id, 'generate', request, 'busy-visual', scene_id=scene_id)
    elif kind == 'audio':
        base.start_audio(OWNER, scene_id, request, 'busy-audio')
    elif kind == 'motion':
        MotionStore(base).start(OWNER, scene_id, request, 'busy-motion')
    else:
        Production(base)
        with base.transaction() as db:
            db.execute('INSERT INTO video_renders VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                       ('render-1', OWNER, project_id, 'busy-render', 'hash', json.dumps(dict(status='running')), '{}'))
    before = base.project(OWNER, project_id)
    with pytest.raises(ServiceError, match='in_progress'):
        adopt(state, version_id)
    assert base.project(OWNER, project_id) == before
    assert explanation.project(OWNER, project_id)['head_version_id'] is None


@pytest.mark.parametrize('limit', ['MAX_PROJECT_JSON_BYTES', 'MAX_TOTAL_JSON_BYTES'])
def test_json_capacity_reservation_precedes_any_new_operation(state, monkeypatch, limit):
    base, explanation, project_id = state
    monkeypatch.setattr(module, limit, module.OUTPUT_RESERVE_BYTES)
    before = base.project(OWNER, project_id)
    with pytest.raises(ServiceError, match='explanation_storage_limit'):
        begin(state)
    assert explanation.active_operations() == []
    assert base.project(OWNER, project_id) == before


def test_candidate_limit_preserves_history_and_completion_releases_reservation(state, monkeypatch):
    base, explanation, project_id = state
    op = begin(state)
    with base.transaction() as db:
        reserved = explanation._usage(db, project_id)
    version_id = complete(state, op)
    with base.transaction() as db:
        assert explanation._usage(db, project_id) < reserved
    monkeypatch.setattr(module, 'MAX_PROJECT_CANDIDATES', 1)
    with pytest.raises(ServiceError, match='explanation_candidate_limit'):
        begin(state, 'second')
    assert explanation.version(OWNER, version_id)['result'] == result_for(op)


@pytest.mark.parametrize('status', ['failed', 'needs_input', 'cancelled', 'timed_out'])
def test_terminal_operations_ignore_late_results_and_do_not_create_candidates(state, status):
    _, explanation, project_id = state
    op = begin(state)
    explanation.update_operation(op['id'], status=status)
    assert explanation.complete(op['id'], result_for(op))['status'] == status
    assert explanation.project(OWNER, project_id)['versions'] == []


class FakeGateway:
    def __init__(self, ambiguous=False, accepted=True):
        self.uploads = self.posts = self.lookups = self.cancels = self.collects = 0
        self.ambiguous, self.accepted = ambiguous, accepted
        self.document = None
        self.state = 'running'
        self.unavailable = False
        self.cancel_failures = 0

    def upload(self, document):
        self.uploads += 1
        return dict(input_id='input-1', sha256=digest(document))

    def submit(self, operation_id, document, deadline_at, input_ref):
        self.posts += 1
        self.document = deepcopy(document) if self.accepted else None
        if self.ambiguous:
            raise ServiceError('service_unavailable', 503)
        return dict(job_id='job-1')

    def lookup(self, operation_id):
        self.lookups += 1
        if self.unavailable:
            raise ServiceError('service_unavailable', 503)
        if self.document is None:
            raise ServiceError('job_not_found', 404)
        return dict(job_id='job-1')

    def poll(self, job_id):
        return dict(state=self.state)

    def collect(self, job_id, input_sha256, input_document):
        self.collects += 1
        assert self.document == input_document
        return result_for(dict(document=input_document, input_sha256=input_sha256))

    def cancel(self, job_id):
        self.cancels += 1
        if self.cancel_failures:
            self.cancel_failures -= 1
            raise ServiceError('service_unavailable', 503)
        self.state = 'cancelling'


def test_workflow_gate_is_explicit_and_disabled_makes_no_agent_calls(state):
    op = begin(state)
    gateway = FakeGateway()
    workflow = ExplanationWorkflow(state[1], gateway)
    workflow.tick()
    assert workflow.thread is None and gateway.posts == gateway.uploads == 0
    assert state[1].operation(op['id'])['status'] == 'cancelled'
    assert state[1].operation(op['id'])['error'] == 'explanation_disabled'
    with pytest.raises(ServiceError, match='explanation_disabled'):
        workflow.require_gateway()
    with pytest.raises(ServiceError, match='agent_not_configured'):
        ExplanationWorkflow(state[1], enabled=True).require_gateway()


def test_disabling_after_uncertain_submit_still_looks_up_and_collects_without_new_paid_post(state):
    base, explanation, project_id = state
    op = begin(state)
    gateway = FakeGateway(ambiguous=True)
    ExplanationWorkflow(explanation, gateway, enabled=True).tick()
    recovered = ExplanationStore(Store(base.path))
    disabled = ExplanationWorkflow(recovered, gateway, enabled=False)
    disabled.tick()
    assert gateway.lookups == 1 and gateway.posts == gateway.uploads == 1
    gateway.state = 'completed'
    disabled.tick()
    assert recovered.operation(op['id'])['status'] == 'completed'
    assert len(recovered.project(OWNER, project_id)['versions']) == 1
    assert recovered.project(OWNER, project_id)['head_version_id'] is None


def test_disabling_after_running_job_still_delivers_cancel_and_reconciles_remote_terminal(state):
    base, explanation, project_id = state
    op = begin(state)
    gateway = FakeGateway()
    ExplanationWorkflow(explanation, gateway, enabled=True).tick()
    recovered = ExplanationStore(Store(base.path))
    disabled = ExplanationWorkflow(recovered, gateway, enabled=False)
    recovered.cancel(OWNER, op['id'])
    disabled.tick()
    assert gateway.cancels == 1 and gateway.posts == gateway.uploads == 1
    gateway.state = 'cancelled'
    disabled.tick()
    assert recovered.operation(op['id'])['status'] == 'cancelled'
    assert recovered.project(OWNER, project_id)['versions'] == []


def test_disabled_workflow_can_start_to_drain_but_missing_gateway_stays_idle(state):
    workflow = ExplanationWorkflow(state[1], FakeGateway(), enabled=False)
    try:
        workflow.start()
        assert workflow.thread is not None
    finally:
        workflow.stop()
    missing = ExplanationWorkflow(state[1], enabled=False)
    missing.start()
    assert missing.thread is None


def test_explanation_requires_existing_scenes_without_saving_an_operation(state):
    base, explanation, _ = state
    empty = base.create_project(OWNER, dict(title='空项目', script='总文稿', style='图解'))
    with pytest.raises(ServiceError, match='explanation_requires_scenes'):
        explanation.start(OWNER, empty['id'], dict(expected_revision=1, brief=BRIEF), 'empty-plan')
    assert explanation.project(OWNER, empty['id'])['operation'] is None


def test_ambiguous_submit_recovers_after_restart_by_lookup_without_second_paid_post(state):
    base, explanation, project_id = state
    op = begin(state)
    gateway = FakeGateway(ambiguous=True)
    ExplanationWorkflow(explanation, gateway, enabled=True).tick()
    assert explanation.operation(op['id'])['error'] == 'submission_uncertain'
    recovered = ExplanationStore(Store(base.path))
    workflow = ExplanationWorkflow(recovered, gateway, enabled=True)
    gateway.unavailable = True
    workflow.tick()
    assert recovered.operation(op['id'])['status'] == 'submitting'
    gateway.unavailable = False
    workflow.tick()
    gateway.state = 'completed'
    workflow.tick()
    assert gateway.posts == gateway.uploads == 1 and gateway.lookups == 2
    assert recovered.operation(op['id'])['status'] == 'completed'
    assert len(recovered.project(OWNER, project_id)['versions']) == 1
    assert recovered.project(OWNER, project_id)['head_version_id'] is None
    assert base.active_operations() == [] and base.active_audio_operations() == []


def test_unconfirmed_submit_times_out_without_resubmitting(state, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    op = begin(state)
    gateway = FakeGateway(ambiguous=True, accepted=False)
    workflow = ExplanationWorkflow(state[1], gateway, enabled=True)
    workflow.tick()
    workflow.tick()
    clock[0] = 1601.0
    workflow.tick()
    final = state[1].operation(op['id'])
    assert final['status'] == 'timed_out' and final['error'] == 'submission_unconfirmed'
    assert gateway.posts == 1


def test_cancel_before_submit_makes_no_remote_calls(state):
    op = begin(state)
    state[1].cancel(OWNER, op['id'])
    gateway = FakeGateway()
    ExplanationWorkflow(state[1], gateway, enabled=True).tick()
    assert gateway.posts == gateway.uploads == gateway.cancels == 0
    assert state[1].operation(op['id'])['status'] == 'cancelled'


def test_cancel_completion_race_discards_result_without_fetching_artifacts(state):
    op = begin(state)
    gateway = FakeGateway()
    workflow = ExplanationWorkflow(state[1], gateway, enabled=True)
    workflow.tick()
    state[1].cancel(OWNER, op['id'])
    gateway.state = 'completed'
    workflow.tick()
    assert gateway.cancels == gateway.collects == 0
    final = state[1].operation(op['id'])
    assert final['status'] == 'completed' and final['error'] == 'cancel_arrived_after_completion'
    assert state[1].project(OWNER, state[2])['versions'] == []


def test_result_observed_after_deadline_is_not_adoptable(state, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    op = begin(state)
    gateway = FakeGateway()
    workflow = ExplanationWorkflow(state[1], gateway, enabled=True)
    workflow.tick()
    clock[0] = 1601.0
    gateway.state = 'completed'
    workflow.tick()
    assert gateway.collects == 0 and state[1].operation(op['id'])['status'] == 'timed_out'
    assert state[1].project(OWNER, state[2])['versions'] == []


def test_cancel_claim_survives_restart_and_four_failed_attempts_keep_polling(state, monkeypatch):
    base, explanation, project_id = state
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    op = begin(state)
    gateway = FakeGateway()
    gateway.cancel_failures = 100
    ExplanationWorkflow(explanation, gateway, enabled=True).tick()
    explanation.cancel(OWNER, op['id'])
    assert explanation.claim_cancellation(op['id'], now=clock[0], retry_delays=CANCEL_RETRY_DELAYS)
    # Simulate a crash after claim persistence, before the cancel POST.
    recovered = ExplanationStore(Store(base.path))
    workflow = ExplanationWorkflow(recovered, gateway, enabled=True)
    workflow.tick()
    assert gateway.cancels == 0
    for i, delay in enumerate(CANCEL_RETRY_DELAYS[1:], 1):
        clock[0] += CANCEL_RETRY_DELAYS[i - 1]
        workflow.tick()
        workflow.tick()
        assert gateway.cancels == i
    clock[0] += 100.0
    workflow.tick()
    assert gateway.cancels == 3 and gateway.posts == 1
    assert recovered.operation(op['id'])['cancel_attempts'] == 4
    assert recovered.operation(op['id'])['error'] == 'cancellation_retry_exhausted'
    gateway.state = 'cancelled'
    workflow.tick()
    assert recovered.operation(op['id'])['status'] == 'cancelled'
    assert recovered.project(OWNER, project_id)['versions'] == []


def test_repeated_submission_claim_and_parallel_project_operation_are_rejected(state):
    op = begin(state)
    assert state[1].claim_submission(op['id'])['submit_attempted']
    assert state[1].claim_submission(op['id']) is None
    with pytest.raises(ServiceError, match='explanation_operation_in_progress'):
        begin(state, 'another')
