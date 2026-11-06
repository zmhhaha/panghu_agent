"""Motion candidates beside static state; synthetic plans and no model I/O."""
from copy import deepcopy
import json
from uuid import UUID

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.motion import example_plan
from video_workbench.motion_store import MotionStore, semantic_binding
from video_workbench import motion_store as motion_module
from video_workbench.motion_workflow import CANCEL_RETRY_DELAYS, MotionWorkflow
from video_workbench.store import Store


OWNER = 'alice'
JOB = str(UUID(int=50))


def fields(title='请求流转'):
    return dict(title=title, narration='先接收请求。再放入队列。最后执行任务。',
                visual_description='展示请求、队列、工作节点的关系。', duration_seconds=15, format='svg')


@pytest.fixture
def state(tmp_path):
    base = Store(tmp_path / 'state.db')
    project = base.create_project(OWNER, dict(title='示例项目', script='总文稿', style='深色清晰图解'))
    project = base.save_scene(OWNER, project['id'], fields())
    motion = MotionStore(base)
    return base, motion, project['id'], project['scenes'][0]['id']


def start(state, *, key='motion-1', request=None):
    base, motion, project_id, scene_id = state
    request = request or dict(expected_revision=base.project(OWNER, project_id)['revision'])
    op = motion.start(OWNER, scene_id, request, key)
    return motion.operation(op['id'], OWNER)


def complete(motion, op):
    result = dict(schema='video.motion-result.v1', input_sha256=op['input_sha256'],
                  plan=example_plan(op['document']['beats']))
    completed = motion.complete(op['id'], result)
    return completed['version_id'], result


def test_additive_candidate_never_changes_static_head_or_auto_adopts(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    assert 'voice' not in json.dumps(op['document']) and 'audio_sha256' not in json.dumps(op['document'])
    version_id, result = complete(motion, op)
    project = motion.project(OWNER, project_id)
    scene = project['scenes'][0]
    assert scene['head_version_id'] is None and scene['versions'] == []
    assert scene['motion']['mode'] == 'static' and scene['motion']['head_version_id'] is None
    assert scene['motion']['versions'][0]['id'] == version_id
    assert scene['motion']['versions'][0]['stale'] is False
    assert motion.version(OWNER, version_id)['plan'] == result['plan']
    assert motion.complete(op['id'], result)['version_id'] == version_id
    assert len(motion.project(OWNER, project_id)['scenes'][0]['motion']['versions']) == 1
    project = motion.adopt(OWNER, scene_id, version_id, None, project['revision'])
    assert project['scenes'][0]['motion']['mode'] == 'motion'
    assert project['scenes'][0]['motion']['head_version_id'] == version_id
    assert base.project(OWNER, project_id)['scenes'][0]['head_version_id'] is None


def test_motion_and_static_selected_versions_coexist(state):
    base, motion, project_id, scene_id = state
    static = base.start(OWNER, project_id, 'generate', dict(expected_revision=2), 'static-1', scene_id=scene_id)
    base.complete(static['id'], dict(format='svg', source='<svg/>', summary='原静态图'))
    project = base.project(OWNER, project_id)
    old_id = project['scenes'][0]['versions'][0]['id']
    base.adopt(OWNER, scene_id, old_id, None)
    version_id, _ = complete(motion, start(state))
    project = motion.project(OWNER, project_id)
    project = motion.adopt(OWNER, scene_id, version_id, None, project['revision'])
    assert project['scenes'][0]['head_version_id'] == old_id
    project = motion.set_mode(OWNER, scene_id, 'static', project['revision'])
    assert project['scenes'][0]['motion']['mode'] == 'static'
    assert project['scenes'][0]['motion']['head_version_id'] == version_id
    project = motion.set_mode(OWNER, scene_id, 'motion', project['revision'])
    assert project['scenes'][0]['motion']['mode'] == 'motion'
    assert base.version(OWNER, old_id)['source'] == '<svg/>'


def test_voice_speed_and_pronunciation_changes_reuse_visual_semantics(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    version_id, _ = complete(motion, op)
    original = motion.version(OWNER, version_id)
    base.configure_speech_provider('kokoro-rk3588', 'zm_yunjian', ['zm_yunjian', 'zm_yunxi'])
    project = base.project(OWNER, project_id)
    project = base.save_project_speech_settings(OWNER, project_id,
        dict(expected_revision=project['revision'], voice='zm_yunxi'))
    project = base.save_speech_settings(OWNER, scene_id,
        dict(expected_revision=project['revision'], speed=1.2, pronunciation={'请求': '请 求'}))
    current = motion.version(OWNER, version_id)
    assert current == original
    assert semantic_binding(project, project['scenes'][0]) == original['semantic_binding_sha256']
    project = motion.adopt(OWNER, scene_id, version_id, None, project['revision'])
    assert project['scenes'][0]['motion']['head_stale'] is False


@pytest.mark.parametrize('field', ['narration', 'title', 'visual_description', 'style'])
def test_late_semantically_stale_candidate_is_preserved_and_cannot_be_adopted(state, field):
    base, motion, project_id, scene_id = state
    op = start(state)
    project = base.project(OWNER, project_id)
    if field == 'style':
        with base.transaction() as db:
            doc = base._project(db, OWNER, project_id)
            doc['style'] = '修改主题与受众'
            base._save(db, doc)
    else:
        changed = fields()
        changed[field] += '改变'
        base.save_scene(OWNER, project_id, changed, scene_id, project['revision'])
    version_id, _ = complete(motion, op)
    project = motion.project(OWNER, project_id)
    assert project['scenes'][0]['motion']['versions'][0]['stale'] is True
    with pytest.raises(ServiceError, match='motion_plan_stale'):
        motion.adopt(OWNER, scene_id, version_id, None, project['revision'])
    assert motion.version(OWNER, version_id)['plan_sha256'] == digest(example_plan(op['document']['beats']))


def test_editing_another_scene_does_not_invalidate_adopted_motion(state):
    base, motion, project_id, scene_id = state
    version_id, _ = complete(motion, start(state))
    project = base.save_scene(OWNER, project_id, fields('另一个镜头'))
    project = motion.adopt(OWNER, scene_id, version_id, None, project['revision'])
    assert project['scenes'][0]['motion']['head_stale'] is False
    assert project['scenes'][1]['motion']['versions'] == []


def test_request_replay_precedes_changed_revision_and_conflicting_intent_is_refused(state):
    base, motion, project_id, scene_id = state
    request = dict(expected_revision=2, instruction='突出队列')
    op = start(state, request=request)
    assert motion.start(OWNER, scene_id, request, 'motion-1')['id'] == op['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        motion.start(OWNER, scene_id, dict(request, instruction='改成其他内容'), 'motion-1')
    with pytest.raises(ServiceError, match='motion_operation_in_progress'):
        motion.start(OWNER, scene_id, dict(expected_revision=base.project(OWNER, project_id)['revision']), 'motion-2')


def test_revision_and_head_conflicts_are_checked_transactionally(state):
    base, motion, project_id, scene_id = state
    version_id, _ = complete(motion, start(state))
    project = motion.project(OWNER, project_id)
    with pytest.raises(ServiceError, match='revision_conflict'):
        motion.adopt(OWNER, scene_id, version_id, None, 2)
    with pytest.raises(ServiceError, match='motion_head_conflict'):
        motion.adopt(OWNER, scene_id, version_id, 'incorrect-head', project['revision'])
    assert motion.project(OWNER, project_id)['revision'] == project['revision']


@pytest.mark.parametrize('kind', ['diagram', 'process'])
def test_generation_binding_allows_other_scene_background_updates_and_replay(state, kind):
    base, motion, project_id, scene_id = state
    project = base.save_scene(OWNER, project_id, fields('另一个镜头'))
    second = project['scenes'][1]['id']
    request = dict(expected_revision=project['revision'], diagram_kind=kind,
                   expected_semantic_binding_sha256=semantic_binding(project, project['scenes'][1]))
    first = motion.operation(motion.start(OWNER, scene_id,
        dict(expected_revision=project['revision']), 'other-scene-background')['id'])
    # Starting and completing another scene both update the shared revision.
    complete(motion, first)
    started = motion.start(OWNER, second, request, 'bound-second-scene')
    operation = motion.operation(started['id'])
    assert operation['semantic_binding_sha256'] == request['expected_semantic_binding_sha256']
    assert operation['document']['title'] == '另一个镜头'
    assert motion.start(OWNER, second, request, 'bound-second-scene') == started
    with pytest.raises(ServiceError, match='motion_operation_in_progress'):
        motion.start(OWNER, second, request, 'bound-second-scene-duplicate')
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        motion.start(OWNER, second, dict(request, instruction='不同要求'), 'bound-second-scene')
    # The new optional binding must not turn an old stale request into acceptance.
    with pytest.raises(ServiceError, match='revision_conflict'):
        motion.start(OWNER, second, dict(expected_revision=request['expected_revision']), 'legacy-stale')


@pytest.mark.parametrize('field', ['title', 'narration', 'visual_description', 'style'])
def test_bound_generation_rejects_changed_target_and_future_revision(state, field):
    base, motion, project_id, scene_id = state
    project = base.project(OWNER, project_id)
    binding = semantic_binding(project, project['scenes'][0])
    request = dict(expected_revision=project['revision'], expected_semantic_binding_sha256=binding)
    with pytest.raises(ServiceError, match='revision_conflict'):
        motion.start(OWNER, scene_id, dict(request, expected_revision=project['revision'] + 1), 'future')
    with pytest.raises(ServiceError, match='not_found'):
        motion.start('bob', scene_id, request, 'wrong-owner')
    if field == 'style':
        with base.transaction() as db:
            latest = base._project(db, OWNER, project_id)
            latest['style'] = '新的风格'
            base._save(db, latest)
    else:
        changed = fields()
        changed[field] += '已更改'
        base.save_scene(OWNER, project_id, changed, scene_id, project['revision'])
    with pytest.raises(ServiceError, match='motion_generation_conflict'):
        motion.start(OWNER, scene_id, request, 'stale-scene')
    # A matching shared revision cannot override a stale explicit binding either.
    request['expected_revision'] = base.project(OWNER, project_id)['revision']
    with pytest.raises(ServiceError, match='motion_generation_conflict'):
        motion.start(OWNER, scene_id, request, 'stale-scene-current-revision')
    assert not motion.active_operations()


@pytest.mark.parametrize('binding', ['', 'wrong', 'A' * 64, True, {}, 'a' * 63])
def test_invalid_generation_binding_never_starts_work(state, binding):
    base, motion, project_id, scene_id = state
    with pytest.raises(ServiceError, match='invalid_motion_request'):
        motion.start(OWNER, scene_id, dict(expected_revision=2,
            expected_semantic_binding_sha256=binding), 'malformed-binding')
    assert not motion.active_operations()


def test_owners_cannot_read_cancel_or_adopt_each_others_candidates(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    version_id, _ = complete(motion, op)
    for action in (lambda: motion.project('bob', project_id), lambda: motion.version('bob', version_id),
                   lambda: motion.public_operation('bob', op['id']), lambda: motion.cancel('bob', op['id']),
                   lambda: motion.adopt('bob', scene_id, version_id, None, 1)):
        with pytest.raises(ServiceError, match='not_found'):
            action()


def test_cancel_completion_race_discards_result_without_claiming_upstream_cancel(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    motion.cancel(OWNER, op['id'])
    version_id, _ = complete(motion, op)
    assert version_id is None
    public = motion.public_operation(OWNER, op['id'])
    assert public['status'] == 'completed' and public['error'] == 'cancel_arrived_after_completion'
    assert motion.project(OWNER, project_id)['scenes'][0]['motion']['versions'] == []


def test_batch_adoption_is_atomic_when_one_selection_is_invalid(state):
    base, motion, project_id, scene_id = state
    version_id, _ = complete(motion, start(state))
    project = base.save_scene(OWNER, project_id, fields('第二镜'))
    second = project['scenes'][1]['id']
    op = motion.operation(motion.start(OWNER, second, dict(expected_revision=project['revision']), 'second')['id'])
    second_version, _ = complete(motion, op)
    project = motion.project(OWNER, project_id)
    selections = [dict(scene_id=scene_id, version_id=version_id, expected_head_version_id=None),
                  dict(scene_id=second, version_id=second_version, expected_head_version_id='wrong')]
    with pytest.raises(ServiceError, match='motion_head_conflict'):
        motion.adopt_batch(OWNER, project_id, selections, project['revision'])
    after = motion.project(OWNER, project_id)
    assert after['revision'] == project['revision']
    assert all(s['motion']['head_version_id'] is None for s in after['scenes'])
    selections[1]['expected_head_version_id'] = None
    adopted = motion.adopt_batch(OWNER, project_id, selections, project['revision'])
    assert [s['motion']['head_version_id'] for s in adopted['scenes']] == [version_id, second_version]


def test_revision_material_is_immutable_and_bound_to_the_same_scene(state):
    base, motion, project_id, scene_id = state
    version_id, _ = complete(motion, start(state))
    project = base.project(OWNER, project_id)
    op = start(state, key='revision', request=dict(expected_revision=project['revision'],
        base_version_id=version_id, instruction='突出工作节点'))
    assert op['document']['base_plan'] == motion.version(OWNER, version_id)['plan']
    assert op['parent_version_id'] == version_id
    new_id, _ = complete(motion, op)
    assert motion.version(OWNER, new_id)['parent_version_id'] == version_id
    assert motion.project(OWNER, project_id)['scenes'][0]['motion']['head_version_id'] is None


class FakeGateway:
    """Synthetic service state; deliberately loses a POST response when asked."""
    def __init__(self, *, ambiguous=False, accepted=True):
        self.uploads = self.posts = self.lookups = self.cancels = 0
        self.ambiguous, self.accepted = ambiguous, accepted
        self.document, self.state = None, 'running'
        self.unavailable = False

    def upload(self, document):
        self.uploads += 1
        return dict(input_id=str(UUID(int=51)), sha256=digest(document),
                    path='motion-input.json', role='motion_input')

    def submit(self, operation_id, document, deadline_at, input_ref):
        self.posts += 1
        self.document = document if self.accepted else None
        if self.ambiguous:
            raise ServiceError('service_unavailable', 503)
        return dict(job_id=JOB)

    def lookup(self, operation_id):
        self.lookups += 1
        if self.unavailable:
            raise ServiceError('service_unavailable', 503)
        if self.document is None:
            raise ServiceError('job_not_found', 404)
        return dict(job_id=JOB)

    def poll(self, job_id):
        return dict(state=self.state)

    def cancel(self, job_id):
        self.cancels += 1
        self.state = 'cancelling'

    def collect(self, job_id, input_sha256, beats):
        return dict(schema='video.motion-result.v1', input_sha256=input_sha256, plan=example_plan(beats))


def test_optional_workflow_is_disabled_by_default(state):
    base, motion, project_id, scene_id = state
    start(state)
    gateway = FakeGateway()
    workflow = MotionWorkflow(motion, gateway)
    workflow.start()
    workflow.tick()
    assert workflow.thread is None and gateway.posts == gateway.uploads == 0


def test_lost_submit_response_recovers_after_restart_with_one_post(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    gateway = FakeGateway(ambiguous=True)
    MotionWorkflow(motion, gateway, enabled=True).tick()
    assert motion.operation(op['id'])['error'] == 'submission_uncertain'
    recovered = MotionStore(Store(base.path))
    workflow = MotionWorkflow(recovered, gateway, enabled=True)
    workflow.tick()
    assert recovered.operation(op['id'])['job_id'] == JOB
    assert gateway.posts == gateway.uploads == gateway.lookups == 1
    gateway.state = 'completed'
    workflow.tick()
    assert recovered.operation(op['id'])['status'] == 'completed'
    assert len(recovered.project(OWNER, project_id)['scenes'][0]['motion']['versions']) == 1
    assert recovered.project(OWNER, project_id)['scenes'][0]['motion']['head_version_id'] is None


def test_ambiguous_unconfirmed_submit_times_out_without_resubmitting(state, monkeypatch):
    base, motion, project_id, scene_id = state
    clock = [1000.0]
    monkeypatch.setattr('video_workbench.motion_store.time.time', lambda: clock[0])
    op = start(state)
    gateway = FakeGateway(ambiguous=True, accepted=False)
    workflow = MotionWorkflow(motion, gateway, enabled=True)
    workflow.tick()
    workflow.tick()
    assert gateway.posts == 1 and motion.operation(op['id'])['status'] == 'submitting'
    clock[0] = 1700.0
    workflow.tick()
    final = motion.operation(op['id'])
    assert final['status'] == 'timed_out' and final['error'] == 'submission_unconfirmed'
    assert gateway.posts == 1


def test_lookup_outage_preserves_ambiguous_operation(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    gateway = FakeGateway(ambiguous=True)
    workflow = MotionWorkflow(motion, gateway, enabled=True)
    workflow.tick()
    gateway.unavailable = True
    workflow.tick()
    assert motion.operation(op['id'])['status'] == 'submitting' and gateway.posts == 1
    gateway.unavailable = False
    workflow.tick()
    assert motion.operation(op['id'])['status'] == 'running' and gateway.posts == 1


def test_cancel_before_submission_makes_no_upstream_request(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    motion.cancel(OWNER, op['id'])
    gateway = FakeGateway()
    MotionWorkflow(motion, gateway, enabled=True).tick()
    assert gateway.posts == gateway.uploads == gateway.cancels == 0
    assert motion.operation(op['id'])['status'] == 'cancelled'


def test_running_cancel_is_requested_once_and_waits_for_real_terminal(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    gateway = FakeGateway()
    workflow = MotionWorkflow(motion, gateway, enabled=True)
    workflow.tick()
    motion.cancel(OWNER, op['id'])
    workflow.tick()
    workflow.tick()
    assert gateway.cancels == 1 and motion.operation(op['id'])['status'] == 'cancelling'
    gateway.state = 'cancelled'
    workflow.tick()
    assert motion.operation(op['id'])['status'] == 'cancelled'
    assert motion.project(OWNER, project_id)['scenes'][0]['motion']['versions'] == []


class UnreliableCancelGateway(FakeGateway):
    """Lose cancellation before delivery, so upstream remains running."""
    def __init__(self, failures=1):
        super().__init__()
        self.cancel_failures = failures
        self.cancel_jobs = []

    def cancel(self, job_id):
        self.cancel_jobs.append(job_id)
        if self.cancel_failures:
            self.cancels += 1
            self.cancel_failures -= 1
            raise ServiceError('service_unavailable', 503)
        super().cancel(job_id)


def test_cancel_not_delivered_retries_same_job_after_persisted_backoff(state, monkeypatch):
    base, motion, project_id, scene_id = state
    clock = [1000.0]
    monkeypatch.setattr('video_workbench.motion_store.time.time', lambda: clock[0])
    op = start(state)
    gateway = UnreliableCancelGateway()
    workflow = MotionWorkflow(motion, gateway, enabled=True)
    workflow.tick()
    motion.cancel(OWNER, op['id'])
    workflow.tick()
    assert gateway.state == 'running' and gateway.cancels == 1
    failed = motion.operation(op['id'])
    assert failed['status'] == 'cancelling' and failed['error'] == 'cancellation_uncertain'
    assert failed['cancel_attempts'] == 1 and failed['next_cancel_at'] == 1002.0
    workflow.tick()
    clock[0] = 1001.99
    workflow.tick()
    assert gateway.cancels == 1
    clock[0] = 1002.0
    workflow.tick()
    assert gateway.cancels == 2 and gateway.state == 'cancelling'
    assert gateway.cancel_jobs == [JOB, JOB] and gateway.posts == 1
    clock[0] = 1100.0
    workflow.tick()
    assert gateway.cancels == 2
    gateway.state = 'cancelled'
    workflow.tick()
    assert motion.operation(op['id'])['status'] == 'cancelled'


def test_crash_after_cancel_claim_recovers_after_restart_without_new_submission(state, monkeypatch):
    base, motion, project_id, scene_id = state
    clock = [1000.0]
    monkeypatch.setattr('video_workbench.motion_store.time.time', lambda: clock[0])
    op = start(state)
    gateway = UnreliableCancelGateway(failures=0)
    MotionWorkflow(motion, gateway, enabled=True).tick()
    motion.cancel(OWNER, op['id'])
    assert motion.claim_cancellation(op['id'], now=clock[0], retry_delays=CANCEL_RETRY_DELAYS)
    # The process stops after persisting the claim and before sending the POST.
    recovered = MotionStore(Store(base.path))
    workflow = MotionWorkflow(recovered, gateway, enabled=True)
    workflow.tick()
    assert gateway.cancels == 0
    clock[0] = 1002.0
    workflow.tick()
    assert gateway.cancel_jobs == [JOB] and gateway.posts == 1
    assert recovered.operation(op['id'])['cancel_attempts'] == 2


def test_cancel_retry_recovers_legacy_attempt_marker(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    gateway = UnreliableCancelGateway(failures=0)
    MotionWorkflow(motion, gateway, enabled=True).tick()
    motion.cancel(OWNER, op['id'])
    with base.transaction() as db:
        legacy = motion._op(db, op['id'])
        legacy.pop('cancel_attempts')
        legacy.pop('next_cancel_at')
        legacy['cancel_attempted'] = True
        motion._write_op(db, legacy)
    recovered = MotionStore(Store(base.path))
    MotionWorkflow(recovered, gateway, enabled=True).tick()
    assert gateway.cancel_jobs == [JOB]
    assert recovered.operation(op['id'])['cancel_attempts'] == 1


def test_remote_cancelling_never_receives_additional_cancel_requests(state, monkeypatch):
    base, motion, project_id, scene_id = state
    clock = [1000.0]
    monkeypatch.setattr('video_workbench.motion_store.time.time', lambda: clock[0])
    op = start(state)
    gateway = FakeGateway()
    workflow = MotionWorkflow(motion, gateway, enabled=True)
    workflow.tick()
    motion.cancel(OWNER, op['id'])
    gateway.state = 'cancelling'
    workflow.tick()
    clock[0] = 2000.0
    workflow.tick()
    assert gateway.cancels == 0
    assert motion.operation(op['id'])['status'] == 'cancelling'
    assert motion.operation(op['id'])['cancel_attempts'] == 0


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'needs_input', 'cancelled', 'timed_out', 'quarantined'])
def test_remote_terminal_after_user_cancel_is_reconciled_without_cancel_post(state, terminal):
    base, motion, project_id, scene_id = state
    op = start(state)
    gateway = FakeGateway()
    workflow = MotionWorkflow(motion, gateway, enabled=True)
    workflow.tick()
    motion.cancel(OWNER, op['id'])
    gateway.state = terminal
    workflow.tick()
    workflow.tick()
    final = motion.operation(op['id'])
    assert gateway.cancels == 0
    assert final['status'] == ('failed' if terminal == 'quarantined' else terminal)
    assert final['version_id'] is None
    assert motion.project(OWNER, project_id)['scenes'][0]['motion']['versions'] == []
    if terminal == 'completed':
        assert final['error'] == 'cancel_arrived_after_completion'


def test_failed_cancellation_has_bounded_attempts_but_keeps_polling_for_terminal(state, monkeypatch):
    base, motion, project_id, scene_id = state
    clock = [1000.0]
    monkeypatch.setattr('video_workbench.motion_store.time.time', lambda: clock[0])
    op = start(state)
    gateway = UnreliableCancelGateway(failures=100)
    workflow = MotionWorkflow(motion, gateway, enabled=True)
    workflow.tick()
    motion.cancel(OWNER, op['id'])
    for attempt, delay in enumerate(CANCEL_RETRY_DELAYS, 1):
        workflow.tick()
        assert gateway.cancels == attempt
        assert motion.operation(op['id'])['cancel_attempts'] == attempt
        clock[0] += delay
    for _ in range(3):
        workflow.tick()
        clock[0] += 1000.0
    exhausted = motion.operation(op['id'])
    assert gateway.cancels == 4 and gateway.cancel_jobs == [JOB] * 4
    assert exhausted['status'] == 'cancelling' and exhausted['error'] == 'cancellation_retry_exhausted'
    gateway.state = 'cancelled'
    workflow.tick()
    assert motion.operation(op['id'])['status'] == 'cancelled'
    assert motion.operation(op['id'])['error'] is None


def test_cancel_claim_is_atomic_and_cannot_claim_terminal_operation(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    MotionWorkflow(motion, FakeGateway(), enabled=True).tick()
    motion.cancel(OWNER, op['id'])
    now = motion.operation(op['id'])['created_at']
    assert motion.claim_cancellation(op['id'], now=now, retry_delays=CANCEL_RETRY_DELAYS)
    recovered = MotionStore(Store(base.path))
    assert recovered.claim_cancellation(op['id'], now=now, retry_delays=CANCEL_RETRY_DELAYS) is None
    motion.update_operation(op['id'], status='cancelled', error=None)
    assert recovered.claim_cancellation(op['id'], now=now + 100, retry_delays=CANCEL_RETRY_DELAYS) is None


def test_claim_is_atomic_and_refuses_repeat_submission(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    assert motion.claim_submission(op['id'])['submit_attempted'] is True
    assert motion.claim_submission(op['id']) is None


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), 10 ** 1000, True, -1, 601],
                         ids=['nan', 'inf', 'huge-int', 'bool', 'negative', 'too-long'])
def test_deadline_inputs_are_bounded_and_finite(state, timeout):
    base, motion, project_id, scene_id = state
    with pytest.raises(ServiceError, match='invalid_motion_request'):
        motion.start(OWNER, scene_id, dict(expected_revision=2), 'invalid-time', timeout=timeout)
    assert motion.active_operations() == []


def test_candidate_count_limit_refuses_new_work_and_preserves_history(state, monkeypatch):
    base, motion, project_id, scene_id = state
    version_id, _ = complete(motion, start(state))
    monkeypatch.setattr(motion_module, 'MAX_CANDIDATES_PER_SCENE', 1)
    with pytest.raises(ServiceError, match='motion_candidate_limit'):
        start(state, key='second')
    assert motion.version(OWNER, version_id)['plan']
    assert len(motion.project(OWNER, project_id)['scenes'][0]['motion']['versions']) == 1


def test_operation_count_limit_still_allows_idempotent_history_reads(state, monkeypatch):
    base, motion, project_id, scene_id = state
    request = dict(expected_revision=2)
    op = start(state, request=request)
    complete(motion, op)
    monkeypatch.setattr(motion_module, 'MAX_PROJECT_OPERATIONS', 1)
    assert motion.start(OWNER, scene_id, request, 'motion-1')['id'] == op['id']
    with pytest.raises(ServiceError, match='motion_operation_limit'):
        start(state, key='second')


@pytest.mark.parametrize('limit', ['MAX_PROJECT_JSON_BYTES', 'MAX_TOTAL_JSON_BYTES'])
def test_capacity_is_reserved_before_any_new_operation_or_paid_request(state, monkeypatch, limit):
    base, motion, project_id, scene_id = state
    monkeypatch.setattr(motion_module, limit, motion_module.OUTPUT_RESERVE_BYTES)
    before = base.project(OWNER, project_id)
    with pytest.raises(ServiceError, match='motion_storage_limit'):
        start(state)
    assert motion.active_operations() == []
    assert base.project(OWNER, project_id)['revision'] == before['revision']


def test_completion_releases_reserved_capacity_and_never_deletes_history(state):
    base, motion, project_id, scene_id = state
    op = start(state)
    with base.transaction() as db:
        reserved = motion._usage(db, project_id)
    version_id, _ = complete(motion, op)
    with base.transaction() as db:
        completed = motion._usage(db, project_id)
    assert completed < reserved and motion.version(OWNER, version_id)['plan']
