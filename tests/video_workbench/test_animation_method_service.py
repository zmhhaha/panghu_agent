"""Business orchestration tests: fixtures never represent real Agent/media evidence."""
from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_method as protocol
from video_workbench.animation_method_service import AnimationMethodService


CRITERIA = [dict(id='causality', description='The cause and the changed result can be identified.')]
SOURCE = dict(engine='html-svg', strategy=dict(description='Show the cause before the consequence', tradeoffs=''),
              files={'scene.html': '<html><body><svg></svg></body></html>'})


def method(criteria=None):
    return dict(title='Causal staging', problem='Explain an intervention', guidance='Stage cause and effect clearly.',
        applicability='Causal explanations', limitations=['No guarantee for arbitrary tasks'],
        criteria=deepcopy(criteria or CRITERIA), runtime_requirements={'engine': 'html-svg'})


class Gateway:
    def __init__(self, review=False):
        self.visual_input = review
        self.jobs, self.submissions = {}, []
        self.lose_response = self.active = self.incomplete = self.choose_baseline = False

    def preflight(self, deadline):
        return {}

    def upload(self, document, files):
        if self.visual_input:
            assert all(hashlib.sha256(files[row['file_name']]).hexdigest() == row['sha256']
                       for row in document['observation']['frames'])
        return []

    def prepare_submission(self, operation_id, document, deadline, refs):
        def submit():
            self.submissions.append(operation_id)
            self.jobs[operation_id] = dict(job_id=str(uuid4()), state='running' if self.active else 'completed',
                                           stop_proven=not self.active, document=deepcopy(document))
            if self.lose_response:
                raise OSError('ack lost')
            return deepcopy(self.jobs[operation_id])
        return submit

    def lookup(self, operation_id):
        return deepcopy(self.jobs.get(operation_id))

    def _job(self, job_id):
        return next(row for row in self.jobs.values() if row['job_id'] == job_id)

    def poll(self, job_id):
        return deepcopy(self._job(job_id))

    def cancel(self, job_id):
        self._job(job_id).update(state='cancelled', stop_proven=True)

    def collect(self, job_id, checksum, document):
        assert digest(self._job(job_id)['document']) == checksum == digest(document)
        criteria = document['criteria'] or deepcopy(CRITERIA)
        source, opinion = None, None
        if document['mode'] == 'create':
            source = deepcopy(SOURCE)
        elif document['mode'] == 'review':
            baseline = document['comparison']['baseline_candidate_id']
            opinion = dict(summary='UNIT TEST sampled-image review',
                outcome='regressed' if self.choose_baseline and baseline else 'inconclusive' if self.incomplete else 'goal_met',
                preferred_candidate_id=baseline if self.choose_baseline and baseline else document['comparison']['candidate_id'],
                criteria_results=[dict(criterion_id=row['id'], status='insufficient_evidence' if self.incomplete else 'passed',
                                       evidence='UNIT TEST still image') for row in criteria],
                remaining_issues=['Motion not observed'] if self.incomplete else [], next_guidance='Try a different staging.',
                limitations=document['observation']['limitations'])
        return dict(schema=protocol.RESULT_SCHEMA, input_sha256=checksum, mode=document['mode'],
            criteria=criteria, methods=[] if self.visual_input else [method(criteria)], source=source,
            sound_events=[], review=opinion)

    def model_frame_receipt(self, job_id):
        return dict(images=[dict(sha256=row['sha256'], model_request_completed=True)
            for row in self._job(job_id)['document']['observation']['frames']])


class Renderer:
    def __init__(self, root):
        self.root = root
        self.results, self.calls = {}, []
        self.failed = self.lose_response = False

    def render(self, operation_id, source, document, sound_events=None, deadline_at=None):
        self.calls.append(operation_id)
        self.root.mkdir(exist_ok=True)
        raw = b'UNIT TEST inert media:' + operation_id.encode()
        self.media_path(operation_id).write_bytes(raw)
        self.results[operation_id] = dict(media=dict(sha256=hashlib.sha256(raw).hexdigest(), duration_seconds=18),
            technical=dict(status='failed' if self.failed else 'passed', evidence_sha256='a' * 64),
            runtime=dict(engine='html-svg', admission=dict(status='admitted', id='unit-test', evidence_sha256='b' * 64)))
        if self.lose_response:
            raise OSError('render acknowledgement lost')
        return deepcopy(self.results[operation_id])

    def lookup(self, operation_id):
        return deepcopy(self.results.get(operation_id))

    def media_path(self, operation_id):
        return self.root / (operation_id + '.mp4')

    def frames(self, operation_id, max_frames=12):
        return [dict(raw=b'UNIT TEST image', timestamp_seconds=0), dict(raw=b'UNIT TEST image2', timestamp_seconds=9)]


@pytest.fixture
def work(tmp_path):
    creator, reviewer, renderer = Gateway(), Gateway(True), Renderer(tmp_path / 'renders')
    service = AnimationMethodService(tmp_path / 'lab', author_gateway=creator, review_gateway=reviewer, renderer=renderer)
    return service, creator, reviewer, renderer


def start(service, **options):
    body = dict(request_id=str(uuid4()), goal='Explain an intervention and its consequence',
                budget=dict(max_rounds=1, max_seconds=300), duration_seconds=18)
    body.update(options)
    return service.start('developer', body)


def finish(service, run_id, steps=50):
    for _ in range(steps):
        row = service.get('developer', run_id)
        if row['state'] in {'completed', 'cancelled', 'failed'}:
            return row
        service.advance('developer', run_id)
    pytest.fail('commission failed to reach a terminal state')


def test_complete_bounded_commission_and_readonly_get(work):
    service, creator, reviewer, renderer = work
    row = start(service)
    for _ in range(3):
        assert service.get('developer', row['id'])['state'] == 'queued'
    assert creator.submissions == []
    row = finish(service, row['id'])
    assert row['stop_reason'] == 'goal_met'
    assert len(creator.submissions) == 2 and len(reviewer.submissions) == len(renderer.calls) == 1
    frames = reviewer.jobs[reviewer.submissions[0]]['document']['observation']['frames']
    assert [frame['file_name'] for frame in frames] == ['frame-candidate-0.jpg', 'frame-candidate-1.jpg']
    assert row['candidates'][0]['demonstration_id']
    assert row['publication_ready'] is False
    assert '尚未完成异结构迁移验证' in row['publication_blockers']
    assert service.artifact('developer', row['id'], row['best_candidate_id'], 'media')[0].startswith(b'UNIT TEST')
    with pytest.raises(ServiceError):
        service.get('another-developer', row['id'])


def test_uncertain_submit_reconciles_original_without_resubmission(work):
    service, creator, _, _ = work
    creator.lose_response = True
    row = finish(service, start(service)['id'])
    assert row['stop_reason'] == 'goal_met'
    assert len(creator.submissions) == len(set(creator.submissions)) == 2


def test_failed_render_stops_instead_of_pump_loop(work):
    service, _, reviewer, renderer = work
    renderer.failed = True
    row = finish(service, start(service)['id'])
    assert row['state'] == 'failed' and reviewer.submissions == []
    assert len(renderer.calls) == 1


def test_cancel_active_agent_waits_for_stop_proof(work):
    service, creator, _, _ = work
    creator.active = True
    row = start(service)
    service.advance('developer', row['id'])
    service.advance('developer', row['id'])
    assert service.cancel('developer', row['id'], {})['state'] == 'cancelling'
    row = finish(service, row['id'])
    assert row['state'] == 'cancelled' and len(creator.submissions) == 1


def test_cancel_before_work_has_readable_summary_and_reopens_without_work(work):
    service, creator, reviewer, renderer = work
    row = start(service)
    cancelled = service.cancel('developer', row['id'], {})
    assert cancelled['state'] == 'cancelled'
    assert cancelled['summary'] == '开发者取消了本次实验' and cancelled['error'] is None
    original = service.commissions.get('developer', row['id'])
    assert original['summary'] == {'reason': 'developer_cancelled'}
    restored = AnimationMethodService(service.root, author_gateway=creator,
        review_gateway=reviewer, renderer=renderer)
    restored.advance('developer', row['id'])
    assert restored.get('developer', row['id']) == cancelled
    assert restored.commissions.get('developer', row['id']) == original
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_failed_admission_summary_remains_prose_with_original_code_preserved(work):
    service, creator, reviewer, renderer = work
    row = start(service)
    summary = dict(message='制作结果未通过入库验证；保留原操作，不自动重跑',
                   code='animation_method_render_failed')
    service.commissions.stop('developer', row['id'], 'failed', summary)
    failed = service.get('developer', row['id'])
    assert failed['summary'] == summary['message'] and failed['error'] == summary['code']
    assert service.commissions.get('developer', row['id'])['summary'] == summary
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_continue_preserves_better_baseline(work):
    service, _, reviewer, _ = work
    first = finish(service, start(service)['id'])
    reviewer.choose_baseline = True
    row = finish(service, start(service, base_commission_id=first['id'],
        base_candidate_id=first['best_candidate_id'], feedback='Try a spatial alternative')['id'])
    assert row['best_candidate_id'] == first['best_candidate_id']
    assert row['baseline_candidate']['id'] == first['best_candidate_id']
    assert len(row['candidates']) == 2
    assert service.artifact('developer', row['id'], row['best_candidate_id'], 'media')
    frames = reviewer.jobs[reviewer.submissions[-1]]['document']['observation']['frames']
    assert [frame['file_name'] for frame in frames] == [
        'frame-baseline-0.jpg', 'frame-baseline-1.jpg', 'frame-candidate-0.jpg', 'frame-candidate-1.jpg']
    assert len({frame['frame_id'] for frame in frames}) == len(frames)


def test_transfer_retained_baseline_uses_exact_method_criteria(work):
    service, creator, reviewer, _ = work
    first = finish(service, start(service)['id'])
    reviewer.choose_baseline = True
    changed = [dict(id='causality', description='Axis labels can be identified.')]
    continued = finish(service, start(service, base_commission_id=first['id'], criteria=changed)['id'])
    assert continued['best_candidate_id'] == first['best_candidate_id']
    assert continued['criteria'] == changed
    transfer = service.transfer('developer', continued['id'], dict(request_id=str(uuid4()),
        goal='Explain a branching intervention', structural_changes={'relations': {'before': 'chain', 'after': 'branch'}},
        budget=dict(max_rounds=1, max_seconds=300)))
    assert transfer['criteria'] == CRITERIA
    transfer = finish(service, transfer['id'])
    frozen = service.commissions.get('developer', transfer['id'])['frozen']
    inputs = [row['document'] for row in creator.jobs.values()
              if row['document']['goal'] == transfer['goal']]
    assert inputs and all(row['criteria'] == CRITERIA for row in inputs)
    assert all(row['methods'][0]['id'] == frozen['method_versions'][0] for row in inputs)
    validation = transfer['validations'][-1]
    assert validation['criteria_sha256'] == digest(CRITERIA)


def test_transfer_rejects_foreign_baseline_and_internal_overrides_before_work(work):
    service, creator, _, _ = work
    first = finish(service, start(service)['id'])
    body = dict(request_id=str(uuid4()), goal='Explain a branch',
        structural_changes={'relations': {'before': 'chain', 'after': 'branch'}},
        budget=dict(max_rounds=1, max_seconds=300))
    submitted = len(creator.submissions)
    for key, value in [('base_commission_id', first['id']), ('criteria', CRITERIA),
                       ('method_versions', []), ('role', 'reference'),
                       ('baseline_demonstration_id', first['candidates'][0]['demonstration_id'])]:
        with pytest.raises(ServiceError):
            service.transfer('developer', first['id'], dict(body, **{key: value}))
    assert len(creator.submissions) == submitted


def test_direct_transfer_validates_identity_and_standards_before_work(work):
    service, creator, _, _ = work
    first = finish(service, start(service)['id'])
    body = dict(role='transfer', method_versions=first['publication_method_version_ids'],
        baseline_demonstration_id=first['candidates'][0]['demonstration_id'],
        structural_changes={'relations': {'before': 'chain', 'after': 'branch'}})
    for override in [dict(base_commission_id=first['id']), dict(method_versions=[]),
                     dict(criteria=[dict(id='causality', description='Unrelated standard')])]:
        with pytest.raises(ServiceError):
            start(service, **dict(body, **override))
    assert len(creator.submissions) == 2


def test_materialization_resumes_after_candidate_commit(work, monkeypatch):
    service, _, _, renderer = work
    original = service.methods.add_demonstration
    failed = []
    def crash_once(*args, **kwargs):
        if not failed:
            failed.append(True)
            raise OSError('simulated process interruption')
        return original(*args, **kwargs)
    monkeypatch.setattr(service.methods, 'add_demonstration', crash_once)
    row = start(service)
    for _ in range(50):
        try:
            service.advance('developer', row['id'])
        except OSError:
            break
    row = finish(service, row['id'])
    assert row['candidates'][0]['demonstration_id'] and len(renderer.calls) == 1


def test_structural_transfer_then_explicit_publication(work):
    service, _, _, _ = work
    first = finish(service, start(service)['id'])
    transfer = service.transfer('developer', first['id'], dict(request_id=str(uuid4()),
        goal='Explain a branching intervention', structural_changes={'relations': {'before': 'chain', 'after': 'branch'}},
        budget=dict(max_rounds=1, max_seconds=300)))
    transfer = finish(service, transfer['id'])
    first = service.get('developer', first['id'])
    assert first['publication_ready']
    assert first['transfers'][0]['media_url']
    body = dict(request_id=str(uuid4()), method_version_ids=first['publication_method_version_ids'],
        demonstration_ids=[first['candidates'][0]['demonstration_id'], transfer['candidates'][0]['demonstration_id']])
    with pytest.raises(ServiceError, match='human_verification'):
        service.publish('developer', first['id'], body)
    body['human_verification'] = dict(motion_watched=True, audio_listened=False)
    published = service.publish('developer', first['id'], body)
    assert published['publication'] == 'published'
    assert service.publish('developer', first['id'], body)['publication'] == 'published'
    delivery = service.read_methods('consumer', dict(method_version_ids=body['method_version_ids'], consumer_id='formal-op-1'))
    assert delivery['methods'][0]['id'] == body['method_version_ids'][0]
    assert delivery['read_receipt']['scope'] == 'methods_delivered_to_caller_not_model_observation'


def test_visual_only_review_remains_incomplete_until_explicit_human_verification(work):
    service, _, reviewer, _ = work
    reviewer.incomplete = True
    row = finish(service, start(service, criteria=[dict(CRITERIA[0], requires_motion=True)])['id'])
    assert row['validations'][0]['outcome'] == 'incomplete'
    assert row['validations'][0]['acceptance'] is None
    assert row['stop_reason'] == 'budget_exhausted'


def test_outer_render_claim_recovers_missing_inner_ledger_once(work, monkeypatch):
    service, _, _, renderer = work
    row = start(service)
    for _ in range(15):
        service.advance('developer', row['id'])
        run = service.commissions.get('developer', row['id'])
        render = next((op for op in run['operations'] if op['stage'] == 'render'), None)
        if render:
            service.commissions.claim('developer', render['id'])
            break
    original = renderer.lookup
    def absent_is_404(operation_id):
        value = original(operation_id)
        if value is None:
            raise ServiceError('animation_method_render_not_found', 404)
        return value
    monkeypatch.setattr(renderer, 'lookup', absent_is_404)
    row = finish(service, row['id'])
    assert row['stop_reason'] == 'goal_met' and len(renderer.calls) == 1
