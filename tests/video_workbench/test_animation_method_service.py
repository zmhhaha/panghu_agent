"""Business orchestration tests: fixtures never represent real Agent/media evidence."""
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_method as protocol
from video_workbench.animation_method_service import AnimationMethodService, _effect_views


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


def runtime_capabilities(engines):
    """Trusted-runtime shape fixture; this is not a real renderer admission."""
    from video_workbench.animation_runtime_capabilities import SCHEMA
    from agent_service.profiles.video_animation_production_contract import THREE_ASSET_SHA256
    return dict(schema=SCHEMA, allowed_engines=list(engines),
        assets={'three.module.min.js': THREE_ASSET_SHA256} if 'three' in engines else {},
        runtime_identity=dict(image_digest='sha256:' + 'a' * 64, host_sha256='b' * 64,
                              engine_runtime_sha256='c' * 64, admission_id='UNIT-TEST-only'))


def test_admitted_engines_and_assets_freeze_before_agent_work_and_replay_keeps_original(work, monkeypatch):
    service, creator, reviewer, renderer = work
    admitted = runtime_capabilities(['html-svg', 'canvas'])
    monkeypatch.setattr(renderer, 'capabilities', lambda: deepcopy(admitted), raising=False)
    body = dict(request_id=str(uuid4()), goal='Choose the admitted medium freely',
                budget=dict(max_rounds=1, max_seconds=300), duration_seconds=18, criteria=deepcopy(CRITERIA))
    queued = service.start('developer', body)
    frozen = service.commissions.get('developer', queued['id'])
    for mode in ('plan', 'create'):
        document = service._document('developer', frozen, mode)
        assert document['constraints']['allowed_engines'] == ['html-svg', 'canvas']
        assert document['constraints']['runtime_identity'] == admitted['runtime_identity']
        assert document['assets'] == admitted['assets']
    # A configuration expansion must neither reinterpret the frozen task nor
    # turn an HTTP replay into a fresh commission or an idempotency conflict.
    admitted.update(runtime_capabilities(['html-svg', 'canvas', 'three']))
    admitted['runtime_identity']['image_digest'] = 'sha256:' + 'd' * 64
    assert service.start('developer', body)['id'] == queued['id']
    assert service.commissions.get('developer', queued['id'])['frozen'] == frozen['frozen']
    assert service.catalogue('developer')['availability']['allowed_engines'] == ['html-svg', 'canvas', 'three']
    fresh = service.start('developer', dict(body, request_id=str(uuid4())))
    document = service._document('developer', service.commissions.get('developer', fresh['id']), 'create')
    assert document['constraints']['allowed_engines'] == ['html-svg', 'canvas', 'three']
    assert document['assets'] == admitted['assets']
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_new_method_engine_requires_actual_runtime_capability_and_cannot_be_selected_by_browser(work, monkeypatch):
    service, _, _, renderer = work
    proposal = method()
    proposal['runtime_requirements']['engine'] = 'canvas'
    version = service.methods.create_method('developer', proposal, str(uuid4()))
    with pytest.raises(ServiceError, match='runtime_not_admitted'):
        start(service, method_versions=[version['id']])
    for field, value in [('allowed_engines', ['canvas']), ('constraints', {'allowed_engines': ['canvas']}),
                         ('assets', {'three.module.min.js': 'a' * 64})]:
        with pytest.raises(ServiceError):
            start(service, **{field: value})
    monkeypatch.setattr(renderer, 'capabilities', lambda: runtime_capabilities(['canvas']), raising=False)
    queued = start(service, method_versions=[version['id']], criteria=deepcopy(CRITERIA))
    document = service._document('developer', service.commissions.get('developer', queued['id']), 'create')
    assert document['constraints']['allowed_engines'] == ['canvas']
    assert document['methods'] == [version]


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


def test_transfer_context_distinguishes_current_versions_from_historical_parents(work):
    service, creator, _, _ = work
    first = finish(service, start(service)['id'])
    revised = finish(service, start(service, base_commission_id=first['id'])['id'])
    version_id = revised['publication_method_version_ids'][0]
    version = service.methods.get_method('developer', version_id)
    historical_parent = first['publication_method_version_ids'][0]
    assert version['parent_version_id'] == historical_parent != version['version_id']
    changes = {'relations': dict(before='One direct route', after='A fork and merge')}
    transfer = service.transfer('developer', revised['id'], dict(request_id=str(uuid4()),
        goal='Apply the method to a fork and merge', structural_changes=changes,
        feedback='Keep the two routes easy to distinguish.', budget=dict(max_rounds=1, max_seconds=300)))
    run = service.commissions.get('developer', transfer['id'])
    before = service.methods.list_methods('developer')
    for mode in ('plan', 'create'):
        document = service._document('developer', run, mode)
        application = document['constraints']['method_application']
        assert application['role'] == 'transfer'
        assert application['structural_changes'] == changes
        assert application['frozen_version_to_method'] == {version_id: version['method_id']}
        assert application['result_requirements']['plan']['methods'] == []
        assert 'nonempty method content' in application['result_requirements']['create']['instruction']
        assert 'Do not copy it' in application['lineage_rule']
        assert document['methods'] == [version]
        assert document['feedback'] == run['frozen']['feedback']
        assert document['base_source'] is None
        proposal = dict(method(document['criteria']), method_id=version['method_id'],
                        parent_version_id=version_id)
        output = dict(schema=protocol.RESULT_SCHEMA, input_sha256=digest(document), mode=mode,
            criteria=document['criteria'], methods=[] if mode == 'plan' else [proposal],
            source=deepcopy(SOURCE) if mode == 'create' else None, sound_events=[], review=None)
        assert protocol.validate_result(output, digest(document), document) == output
        # Reproduce the failed model response: copying a record's historical
        # parent must remain invalid, even though that ID appears in metadata.
        bad = deepcopy(output)
        bad['methods'] = [dict(proposal, parent_version_id=historical_parent)]
        with pytest.raises(ServiceError, match='animation_method_parent_not_supplied'):
            protocol.validate_result(bad, digest(document), document)
        wrong_method = deepcopy(output)
        wrong_method['methods'] = [dict(proposal, method_id=str(uuid4()))]
        with pytest.raises(ServiceError, match='animation_method_parent_not_supplied'):
            protocol.validate_result(wrong_method, digest(document), document)
    completed = finish(service, transfer['id'])
    assert completed['publication_method_version_ids'] == [version_id]
    assert service.methods.list_methods('developer') == before
    offered = [job['document'] for job in creator.jobs.values()
               if job['document']['goal'] == transfer['goal']]
    assert offered and all(document['constraints']['method_application']['role'] == 'transfer'
                           for document in offered)
    assert service.methods.get_method('developer', version_id) == version


def test_reference_documents_keep_existing_constraints_and_method_records(work):
    service, _, _, _ = work
    first = finish(service, start(service)['id'])
    continued = start(service, base_commission_id=first['id'])
    run = service.commissions.get('developer', continued['id'])
    base = run['frozen']['base_candidate']
    for mode in ('plan', 'create'):
        document = service._document('developer', run, mode, base=base)
        expected = dict(width=1280, height=720, fps=24, duration_seconds=18, allowed_engines=['html-svg'])
        assert {key: value for key, value in document['constraints'].items()
                if key != 'result_requirements'} == expected
        assert ('result_requirements' in document['constraints']) == (mode == 'plan')
        assert document['methods'] == [service.methods.get_method('developer', version_id)
                                       for version_id in base['method_versions']]


@pytest.mark.parametrize('role', ['reference', 'transfer'])
def test_review_requests_assessment_only_and_keeps_historical_methods_readonly(work, role):
    service, creator, reviewer, _ = work
    first = finish(service, start(service)['id'])
    revised = finish(service, start(service, base_commission_id=first['id'])['id'])
    version_id = revised['publication_method_version_ids'][0]
    original_method = service.methods.get_method('developer', version_id)
    assert original_method['parent_version_id'] == first['publication_method_version_ids'][0]
    if role == 'reference':
        queued = start(service, base_commission_id=revised['id'])
    else:
        queued = service.transfer('developer', revised['id'], dict(request_id=str(uuid4()),
            goal='Explain a fork and merge using the same method',
            structural_changes={'relations': {'before': 'chain', 'after': 'fork and merge'}},
            budget=dict(max_rounds=1, max_seconds=300)))
    author_offset = len(creator.submissions)
    completed = finish(service, queued['id'])
    assert completed['state'] == 'completed'
    author_inputs = [creator.jobs[operation_id]['document']
                     for operation_id in creator.submissions[author_offset:]]
    assert [document['mode'] for document in author_inputs] == ['plan', 'create']
    assert 'result_requirements' in author_inputs[0]['constraints']
    assert 'result_requirements' not in author_inputs[1]['constraints']

    job = reviewer.jobs[reviewer.submissions[-1]]
    document = job['document']
    requirements = document['constraints']['result_requirements']
    assert requirements['methods'] == [] and requirements['source'] is None
    assert requirements['sound_events'] == []
    assert all(text in requirements['instruction'] for text in (
        'actual media evidence', 'assessment in review', 'read-only context',
        'do not copy, rewrite or create methods', 'historical parent_version_id or lineage'))
    assert json.loads(json.dumps(document)) == document
    assert protocol.validate_input(document) == document
    selected = document['methods']
    assert selected == [service.methods.get_method('developer', row['version_id']) for row in selected]
    assert selected and selected[0]['parent_version_id'] not in {row['version_id'] for row in selected}
    original_input = deepcopy(document)
    original_versions = service.methods.list_methods('developer')
    output = reviewer.collect(job['job_id'], digest(document), document)
    assert output['methods'] == [] and output['source'] is None and output['sound_events'] == []
    assert output['review']['summary'] == 'UNIT TEST sampled-image review'
    assert protocol.validate_result(output, digest(document), document) == output

    # Clear instructions avoid the failed historical-lineage echo; they do not
    # make that echo valid or change the stored source method or its ancestry.
    bad = deepcopy(output)
    bad['methods'] = [dict(method(document['criteria']), method_id=selected[0]['method_id'],
                           parent_version_id=selected[0]['parent_version_id'])]
    with pytest.raises(ServiceError, match='animation_method_parent_not_supplied'):
        protocol.validate_result(bad, digest(document), document)
    # The failed review emitted three same-method proposals with no parent,
    # not a malformed next_guidance. Missing parent and explicit null remain
    # distinct invalid cases in the existing validator.
    bad = deepcopy(output)
    bad['methods'] = [dict(method(document['criteria']), method_id=selected[0]['method_id'])
                      for _ in range(3)]
    assert type(bad['review']['next_guidance']) is str
    with pytest.raises(ServiceError, match='animation_method_parent_not_supplied'):
        protocol.validate_result(bad, digest(document), document)
    bad['methods'][0]['parent_version_id'] = None
    with pytest.raises(ServiceError, match='invalid_animation_experiment_contract'):
        protocol.validate_result(bad, digest(document), document)
    assert document == original_input
    assert service.methods.list_methods('developer') == original_versions
    assert service.methods.get_method('developer', version_id) == original_method


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
    assert row['error'] == 'animation_method_render_failed'
    assert service.commissions.get('developer', row['id'])['summary']['code'] == row['error']
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


def test_engine_metadata_uses_rendered_sample_and_get_never_executes_or_changes_history(work, monkeypatch):
    service, creator, reviewer, renderer = work
    queued = start(service, title='Canvas and Three.js are only words in this title')
    empty = service.catalogue('developer')
    assert empty['availability']['allowed_engines'] == ['html-svg']
    assert empty['effects'][0]['engine'] is None and empty['effects'][0]['engines'] == []
    assert empty['effects'][0]['engine_status'] == 'unknown'
    assert service.get('developer', queued['id'])['engine'] is None
    finished = finish(service, queued['id'])
    paths = [service.root / name for name in ('methods.sqlite3', 'commissions.sqlite3', 'feedback.sqlite3')]
    before = [path.read_bytes() for path in paths]
    calls = (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))
    monkeypatch.setattr(renderer, 'media_path', lambda *_: pytest.fail('metadata must not read rendered files'))
    monkeypatch.setattr(renderer, 'lookup', lambda *_: pytest.fail('metadata must not reconcile render operations'))
    catalogue, detail = service.catalogue('developer'), service.get('developer', finished['id'])
    assert catalogue['effects'][0]['engine'] == detail['effect']['engine'] == 'html-svg'
    assert catalogue['effects'][0]['engines'] == detail['effect']['engines'] == ['html-svg']
    for row in (catalogue['effects'][0], catalogue['commissions'][0], detail,
                detail['candidates'][0], detail['effect']['commissions'][0]):
        assert row['engine'] == 'html-svg' and row['engine_status'] == 'rendered'
    assert [path.read_bytes() for path in paths] == before
    assert calls == (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))


def engine_projection_fixture(engine='html-svg', *, identity='original', parent=None):
    """Inert stored-record fixtures; these never claim an actual Canvas/Three run."""
    candidate = dict(id=identity + '-candidate', operation_id=identity + '-render',
        source=dict(engine=engine), media=dict(sha256='a' * 64), technical=dict(status='passed'))
    run = dict(id=identity, title='Three.js Canvas HTML/SVG', frozen=dict(goal='Compare actual sample evidence'),
        state='completed', request_id=identity, best_candidate_id=candidate['id'], candidates=[candidate],
        stop_reason='budget_exhausted', created_at=1)
    if parent:
        run['frozen']['base_commission_id'] = parent
    demonstration = dict(id=identity + '-demo', source=dict(engine=engine), media=dict(sha256='a' * 64),
        technical=dict(status='passed'), runtime=dict(engine=engine, admission=dict(status='admitted')),
        provenance=dict(commission_id=identity, candidate_id=candidate['id'],
                        render_operation_id=candidate['operation_id']))
    return run, demonstration


@pytest.mark.parametrize('engine', ['html-svg', 'canvas', 'three'])
def test_engine_projection_matches_candidate_and_demonstration_not_method_or_plan(engine):
    run, demo = engine_projection_fixture(engine)
    run['frozen']['allowed_engines'] = ['three' if engine != 'three' else 'html-svg']
    run['frozen']['runtime_requirements'] = {'engine': 'canvas' if engine != 'canvas' else 'html-svg'}
    before = deepcopy((run, demo))
    effects, _ = _effect_views([run], [demo])
    effect = effects[0]
    assert effect['engine'] == effect['best_candidate']['engine'] == effect['commissions'][0]['engine'] == engine
    assert effect['engine_status'] == 'rendered' and effect['engines'] == [engine]
    assert (run, demo) == before


@pytest.mark.parametrize('change,status', [
    ('no-source', 'unknown'), ('no-source-engine', 'unknown'), ('no-demo', 'unverified'),
    ('wrong-runtime', 'conflict'), ('wrong-demo-source', 'conflict'), ('wrong-media', 'conflict'),
    ('wrong-render-operation', 'conflict'), ('foreign-candidate-demo', 'unverified'),
    ('failed-technical', 'unverified'), ('missing-admission', 'unverified'),
])
def test_engine_projection_with_missing_or_conflicting_evidence_remains_unconfirmed(change, status):
    run, demo = engine_projection_fixture()
    candidate = run['candidates'][0]
    demonstrations = [demo]
    if change == 'no-source':
        candidate.pop('source')
    elif change == 'no-source-engine':
        candidate['source'] = {}
    elif change == 'no-demo':
        demonstrations = []
    elif change == 'wrong-runtime':
        demo['runtime']['engine'] = 'canvas'
    elif change == 'wrong-demo-source':
        demo['source']['engine'] = 'three'
    elif change == 'wrong-media':
        demo['media']['sha256'] = 'b' * 64
    elif change == 'wrong-render-operation':
        demo['provenance']['render_operation_id'] = 'unrelated-render'
    elif change == 'foreign-candidate-demo':
        demo['provenance']['candidate_id'] = 'another-candidate'
    elif change == 'failed-technical':
        demo['technical']['status'] = 'failed'
    else:
        demo['runtime']['admission'] = None
    effect = _effect_views([run], demonstrations)[0][0]
    assert effect['engine'] is None and effect['engines'] == []
    assert effect['engine_status'] == effect['commissions'][0]['engine_status'] == status


def test_effect_engine_tracks_only_representative_while_other_versions_keep_their_engine():
    original, first_demo = engine_projection_fixture('html-svg')
    continued, next_demo = engine_projection_fixture('canvas', identity='continued', parent='original')
    transfer, transfer_demo = engine_projection_fixture('three', identity='transfer')
    transfer['frozen'].update(role='transfer', baseline_demonstration_id=first_demo['id'])
    effects, _ = _effect_views([transfer, continued, original], [first_demo, next_demo, transfer_demo])
    effect = effects[0]
    assert effect['open_commission_id'] == 'continued'
    assert effect['engine'] == 'canvas' and effect['engines'] == ['canvas']
    assert {row['id']: row['engine'] for row in effect['commissions']} == {
        'original': 'html-svg', 'continued': 'canvas', 'transfer': 'three'}


def test_transfer_and_history_expose_the_exact_sample_engine(work):
    service, _, _, _ = work
    first = finish(service, start(service)['id'])
    transfer = service.transfer('developer', first['id'], dict(request_id=str(uuid4()),
        goal='Explain a branching intervention', structural_changes={'relations': {'before': 'chain', 'after': 'branch'}},
        budget=dict(max_rounds=1, max_seconds=300)))
    finish(service, transfer['id'])
    detail = service.get('developer', first['id'])
    assert detail['transfers'] and detail['effect']['commissions']
    for row in detail['transfers'] + detail['effect']['commissions']:
        assert row['engine'] == 'html-svg' and row['engine_status'] == 'rendered'
