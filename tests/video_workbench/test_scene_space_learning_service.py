"""New learning runs use an admitted spatial sidecar; old frozen runs stay exact."""
from copy import deepcopy
import time
from uuid import uuid4

import pytest

from agent_service.contracts import canonical_bytes, digest
from agent_service.contracts import ServiceError
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.scene_space_plan import automatic_plan, default_budget
from test_picture_learning_service import Gateway, Renderer, finish, request_transfer, start
from test_scene_space_learning import carrier
from test_video_learning_service import (
    Gateway as VideoGateway, Renderer as VideoRenderer, real_mp4,
    start as start_video, finish as finish_video,
)
from video_workbench.video_learning_service import VideoLearningService


class SceneGateway(Gateway):
    invalid_motion = False

    def collect(self, job_id, input_sha256, document):
        result=super().collect(job_id,input_sha256,document)
        offer=document['constraints'].get('scene_space_learning')
        if document['mode']=='create' and offer:
            value=carrier(scope=offer['source_scope'],refs=offer['source_references'],
                frames=offer['source_frame_ids'],plan=True,
                viewport={key:document['constraints'][key] for key in ('width','height')})
            if document['work_kind']=='transfer':
                value['world']['id']='new-content-world'
                value['world']['seed']+=1
                value['plan']=automatic_plan([value['world']],id='transfer-plan',
                    viewport=value['plan']['viewport'],duration_frames=1,budget=default_budget(),
                    intents=[dict(world_id=value['world']['id'],weight=1,view='hold',focus=dict(x=700,y=400))])
            if self.invalid_motion:
                value['source']['motion_observed']=True
            result['method']['scene_space']=value
        return result


class SceneRenderer(Renderer):
    admitted=True
    prepare_error=None

    def __init__(self,root):
        super().__init__(root)
        self.scene_plans=[]
        self.deadlines=[]
        self.prepared=[]

    def capabilities(self):
        result=super().capabilities()
        if self.admitted:
            result['scene_space']=dict(schema='video.scene-space-render-capability.v1',admitted=True,
                engines=['canvas'],runtime_sha256='a'*64,artwork_api='window.__sceneSpaceArtwork')
        return result

    def validate_scene_request(self,operation_id,source_document,document,*,scene_space,deadline_at=None):
        self.prepared.append(operation_id)
        if self.prepare_error:
            raise ServiceError(self.prepare_error,409)
        return dict(request_sha256=digest(dict(source_sha256=source_document['source_sha256'],
            input_sha256=digest(document),plan=scene_space)))

    def render(self,*args,scene_space=None,expected_request_sha256=None,**kwargs):
        if scene_space is not None:
            binding=self.validate_scene_request(*args,scene_space=scene_space,deadline_at=kwargs.get('deadline_at'))
            assert binding['request_sha256']==expected_request_sha256
        self.scene_plans.append(deepcopy(scene_space))
        self.deadlines.append(kwargs.get('deadline_at'))
        return super().render(*args,**kwargs)


@pytest.fixture
def system(tmp_path):
    author=SceneGateway()
    reviewer=Gateway(scores=[85])
    renderer=SceneRenderer(tmp_path/'renders')
    service=PictureLearningService(tmp_path/'picture',author_gateway=author,review_gateway=reviewer,renderer=renderer)
    return service,author,reviewer,renderer


@pytest.fixture
def unknown_token_system(system,monkeypatch):
    service,author,reviewer,renderer=system
    collect=author.collect
    def unknown_tokens(*args,**kwargs):
        result=collect(*args,**kwargs)
        if result.get('source') is not None:
            result['method']['scene_space']['plan']['budget']['model']['used_tokens']=None
        return result
    monkeypatch.setattr(author,'collect',unknown_tokens)
    return system


def test_unknown_model_budget_keeps_one_diagnostic_png_without_review_or_transfer(unknown_token_system):
    service,author,reviewer,renderer=unknown_token_system
    project,_=start(service,max_rounds=1)
    completed=finish(service,project)
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    assert run['state']=='completed' and run['stop_reason']=='scene_space_diagnostic_preview_only'
    assert len(author.submissions)==len(renderer.calls)==1
    assert reviewer.submissions==reviewer.uploads==[]
    assert run['transfer_task'] is None and run['transfer_status']=='not_requested'
    assert len(run['candidates'])==1 and run['candidates'][0]['review'] is None
    candidate=run['candidates'][0]
    assert candidate['picture_sha256'] and candidate['source_sha256']
    assert candidate['scene_space']['plan']['budget']['model']['used_tokens'] is None
    assert candidate['verification_scope']=='static_diagnostic_only_not_reviewed'
    report=candidate['scene_space_diagnostic']
    assert report['status']=='rendered' and report['max_frames']==1
    assert report['model_review_authorized'] is report['transfer_authorized'] is report['formal_export_authorized'] is False
    path,mime=service.artifact('developer',project['id'],candidate['artifacts']['scene-space-diagnostic']['id'])
    assert mime=='application/json' and path.read_bytes()==canonical_bytes(report)
    task=service._scene_store().get('developer',candidate['scene_space_task_id'])
    assert task['usage']['baseline']['model_tokens'] is None
    reservation=task['reservations'][0]
    assert reservation['phase']=='preview' and reservation['model_calls']==reservation['model_tokens']==0
    assert 0<reservation['render_seconds']<=60 and reservation['status']=='settled'
    event=next(row for row in task['usage']['events'] if row['id']==candidate['render_operation_id'])
    local=next(row for row in run['scene_space_render_budget']['events'] if row['id']==event['id'])
    assert event['phase']==local['phase']=='preview'
    assert event['render_seconds']==local['seconds']>=0
    assert completed['experiments'][-1]['scene_space_model_usage']['completeness']=='unknown'
    with pytest.raises(ServiceError,match='export_budget_unavailable'):
        service._scene_store().require_export('developer',candidate['scene_space_task_id'])
    with pytest.raises(ServiceError,match='budget_unavailable'):
        service._scene_store().reserve('developer',candidate['scene_space_task_id'],'paid-review',
            phase='review',model_calls=1,model_tokens=1)


def test_diagnostic_marker_blocks_a_reserved_paid_operation_after_recovery(unknown_token_system):
    service,author,reviewer,renderer=unknown_token_system
    project,_=start(service,max_rounds=1)
    completed=finish(service,project)
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    run.update(state='running',stage='review',active_operation_id=None)
    operation=service._operation(run)
    service._agent(run,operation)
    assert operation['status']=='cancelled'
    assert run['stop_reason']=='scene_space_diagnostic_preview_only'
    assert reviewer.uploads==reviewer.submissions==[]
    assert len(author.submissions)==len(renderer.calls)==1


def test_unknown_budget_technical_retry_reuses_the_unstarted_source_plan_and_ledger(unknown_token_system,monkeypatch):
    service,author,reviewer,renderer=unknown_token_system
    current_admit=service._scene_admit_render
    def old_export_gate(run,operation,candidate,options):
        return service._scene_store().require_export(run['owner'],candidate['scene_space_task_id'])
    monkeypatch.setattr(service,'_scene_admit_render',old_export_gate)
    project,_=start(service,max_rounds=1)
    failed=finish(service,project)
    before=service.store.read('experiments','developer',failed['latest_experiment_id'])
    candidate=deepcopy(before['candidates'][0])
    task_before=service._scene_store().get('developer',candidate['scene_space_task_id'])
    assert before['state']=='failed' and before['stop_proven']
    assert before['error']['code']=='scene_space_export_budget_unavailable'
    assert not renderer.calls and len(author.submissions)==1
    monkeypatch.setattr(service,'_scene_admit_render',current_admit)
    service.next_experiment('developer',project['id'],dict(request_id=uuid4().hex,mode='retry'))
    completed=finish(service,project)
    after=service.store.read('experiments','developer',completed['latest_experiment_id'])
    restored=after['candidates'][0]
    assert after['stop_reason']=='scene_space_diagnostic_preview_only'
    assert restored['source']==candidate['source'] and restored['scene_space']==candidate['scene_space']
    assert restored['scene_space_task_id']==candidate['scene_space_task_id']
    assert restored['picture_sha256'] and len(author.submissions)==len(renderer.calls)==1
    assert not reviewer.submissions
    assert after['scene_space_render_budget']['root_experiment_id']==before['scene_space_render_budget']['root_experiment_id']
    assert all(row in after['scene_space_render_budget']['events'] for row in before['scene_space_render_budget']['events'])
    task_after=service._scene_store().get('developer',candidate['scene_space_task_id'])
    assert task_after['frozen']==task_before['frozen']
    assert task_after['usage']['root_plan_sha256']==task_before['usage']['root_plan_sha256']
    assert task_after['usage']['baseline']==task_before['usage']['baseline']


def test_diagnostic_recovery_reuses_shared_preview_settlement_and_never_renders_twice(unknown_token_system,monkeypatch):
    service,author,reviewer,renderer=unknown_token_system
    renderer.lose_result=True
    project,_=start(service,max_rounds=1)
    for _ in range(8):
        service.advance('developer',project['latest_experiment_id'])
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['state']=='submission_unknown':break
    assert run['state']=='submission_unknown' and len(renderer.calls)==1
    operation=service.store.read('operations','developer',run['active_operation_id'])
    assert operation['status']=='claimed' and operation['scene_space_render_phase']=='preview'
    candidate=run['candidates'][0]
    # The PNG exists but its response was lost; emulate shared settlement
    # committing before the experiment write, then resume the same operation.
    shared=service._scene_store()
    shared.record_usage('developer',candidate['scene_space_task_id'],id=operation['id'],
        phase='preview',outcome='completed',model_calls=0,model_tokens=0,render_seconds=.25)
    recover=renderer.recover
    monkeypatch.setattr(renderer,'recover',lambda identity:dict(recover(identity),measured_render_seconds=.1))
    restarted=PictureLearningService(service.root,author_gateway=author,review_gateway=reviewer,renderer=renderer)
    completed=finish(restarted,project)
    after=restarted.store.read('experiments','developer',run['id'])
    assert after['stop_reason']=='scene_space_diagnostic_preview_only'
    assert completed['candidates'][0]['picture_url']
    assert len(renderer.calls)==len(author.submissions)==1 and not reviewer.submissions
    event=next(row for row in after['scene_space_render_budget']['events'] if row['id']==operation['id'])
    assert event['phase']=='preview' and event['seconds']==.25
    assert all(row['status']=='settled' for row in after['scene_space_render_budget']['reservations'])
    ledger=shared.get('developer',candidate['scene_space_task_id'])['usage']
    restarted.advance('developer',run['id'])
    assert shared.get('developer',candidate['scene_space_task_id'])['usage']==ledger


@pytest.mark.parametrize('unavailable',['unknown_render','over_budget'])
def test_diagnostic_cannot_bypass_unknown_render_cost_or_exhaustion(unknown_token_system,unavailable):
    service,author,reviewer,renderer=unknown_token_system
    project,_=start(service,max_rounds=1)
    for _ in range(8):
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['stage']=='render':break
        service.advance('developer',run['id'])
    candidate=run['candidates'][0]
    service._scene_store().record_usage('developer',candidate['scene_space_task_id'],id='prior-render',
        phase='render',outcome='failed',render_seconds=None if unavailable=='unknown_render' else 3601)
    failed=finish(service,project)
    assert failed['state']=='failed' and not renderer.calls and not reviewer.submissions
    assert failed['experiments'][-1]['error']['code']=='scene_space_export_budget_unavailable'


def test_diagnostic_preclaim_failure_settles_the_reserved_preview_phase(unknown_token_system):
    service,author,reviewer,renderer=unknown_token_system
    renderer.prepare_error='scene_space_test_preflight_rejection'
    project,_=start(service,max_rounds=1)
    failed=finish(service,project)
    run=service.store.read('experiments','developer',failed['latest_experiment_id'])
    assert run['state']=='failed' and run['stop_reason']=='render_preflight_rejected'
    assert not renderer.calls and not reviewer.submissions
    operation=service.store.read('operations','developer',run['operation_ids'][-1])
    assert operation['scene_space_render_phase']=='preview' and operation['result']['not_dispatched']
    task=service._scene_store().get('developer',run['candidates'][0]['scene_space_task_id'])
    assert task['reservations'][0]['phase']=='preview' and task['reservations'][0]['status']=='settled'
    event=next(row for row in task['usage']['events'] if row['id']==operation['id'])
    assert event['phase']=='preview' and event['outcome']=='failed' and event['render_seconds']>=0


def test_new_learning_carries_a_validated_plan_and_transfers_only_general_principles(system):
    service,author,reviewer,renderer=system
    project,_=start(service,max_rounds=1)
    completed=finish(service,project)
    assert completed['state']=='completed'
    assert len(renderer.scene_plans)==1
    assert completed['experiments'][-1]['transfer_status']=='awaiting_approval'
    assert all(work['work_kind']=='reproduction' for work in completed['candidates'])
    approved=completed['candidates'][0]['id']
    completed=finish(service,request_transfer(service,completed,approved))
    assert completed['state']=='completed'
    assert len(renderer.scene_plans)==2
    assert all(plan is not None for plan in renderer.scene_plans)
    assert renderer.scene_plans[0]['worlds'][0]['id']!=renderer.scene_plans[1]['worlds'][0]['id']
    transfer=next(document for document,_ in author.uploads if document['work_kind']=='transfer')
    learned=transfer['learned_method']['scene_space']
    assert learned['world'] is learned['plan'] is None
    assert learned['source']['references']==learned['source']['frame_ids']==[]
    assert transfer['base_source'] is None and not transfer['observation']['frames']
    assert transfer['constraints']['scene_space_learning']['source_references']==[]
    for work in completed['candidates']:
        assert work['scene_space_checks']['visual_quality']=='not_assessed'
        assert work['scene_space']['source']['motion_observed'] is False
        assert {'scene-space','scene-space-checks','scene-space-layout'}<=set(work['artifacts'])
        path,mime=service.artifact('developer',project['id'],work['artifacts']['scene-space']['id'])
        assert mime=='application/json'
        assert path.read_bytes()==canonical_bytes(work['scene_space'])
    review=reviewer.uploads[0][0]
    assert review['constraints']['scene_space_learning']['candidate_method']['plan']
    # Source custody stays separate from the new carrier.
    assert all('scene-space.json' not in work['source']['files'] for work in
        service.store.read('experiments','developer',completed['latest_experiment_id'])['candidates'])


def test_an_old_frozen_run_does_not_pick_up_new_capabilities_mid_run(system):
    service,author,reviewer,renderer=system
    renderer.admitted=False
    project,_=start(service,max_rounds=1)
    renderer.admitted=True
    completed=finish(service,project)
    assert completed['state']=='completed'
    assert renderer.scene_plans==[None]
    assert all('scene_space_learning' not in document['constraints'] for document,_ in author.uploads)
    assert all('scene_space' not in work for work in completed['candidates'])


def test_invalid_observation_claim_stops_before_render(system):
    service,author,reviewer,renderer=system
    author.invalid_motion=True
    project,_=start(service,max_rounds=1)
    completed=finish(service,project)
    assert completed['state']=='failed'
    assert renderer.calls==[]
    assert completed['experiments'][-1]['error']['code']=='scene_space_method_observation_not_admitted'


def test_retry_keeps_the_original_admission_plan_and_source(system):
    service,author,reviewer,renderer=system
    renderer.fail=True
    project,_=start(service,max_rounds=1)
    failed=finish(service,project)
    before=service.store.read('experiments','developer',project['latest_experiment_id'])
    original=deepcopy(renderer.scene_plans[0])
    calls=len(author.submissions)
    renderer.fail=False
    service.next_experiment('developer',project['id'],dict(request_id=uuid4().hex,mode='retry'))
    completed=finish(service,project)
    assert completed['state']=='completed'
    assert renderer.scene_plans[1]==original
    after=service.store.read('experiments','developer',completed['latest_experiment_id'])
    assert after['frozen']['scene_space_learning']==before['frozen']['scene_space_learning']
    assert after['candidates'][0]['source']==before['candidates'][0]['source']
    assert digest(after['candidates'][0]['scene_space']['plan'])==digest(original)
    # Recovery creates no second author for the failed reproduction.
    assert len(author.submissions)==calls
    assert after['scene_space_render_budget']['root_experiment_id']==before['id']
    assert after['scene_space_render_budget']['limit_seconds']==before['budget']['max_seconds']
    prior_events=before['scene_space_render_budget']['events']
    assert all(event in after['scene_space_render_budget']['events'] for event in prior_events)
    assert len(after['scene_space_render_budget']['events'])>len(prior_events)
    assert all(row in after['scene_space_model_receipts'] for row in before['scene_space_model_receipts'])


def test_actual_job_usage_components_are_preserved_and_not_invented(system,monkeypatch):
    service,author,reviewer,renderer=system
    receipt=dict(input_tokens=10,output_tokens=20,cache_read_tokens=1,cache_write_tokens=2,
        request_count=2,completeness='complete')
    for gateway in (author,reviewer):
        original=gateway.poll
        monkeypatch.setattr(gateway,'poll',lambda job_id,original=original: dict(original(job_id),usage=deepcopy(receipt)))
    project,_=start(service,max_rounds=1)
    completed=finish(service,project)
    status=completed['experiments'][-1]['scene_space_model_usage']
    assert status['completeness']=='complete'
    assert status['request_count']==4
    assert status['token_components']==dict(input_tokens=20,output_tokens=40,cache_read_tokens=2,cache_write_tokens=4)
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    operations=[service.store.read('operations','developer',identity) for identity in run['operation_ids']]
    paid=[operation for operation in operations if operation['stage']!='render']
    assert all(operation['model_usage_receipt']['usage']==receipt for operation in paid)


def test_missing_usage_remains_unknown_and_unknown_render_cost_blocks_further_output(system):
    service,author,reviewer,renderer=system
    renderer.lose_result=True
    reviewer.scores=[60]
    project,_=start(service,max_rounds=2)
    completed=finish(service,project)
    assert completed['state']=='completed'
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    assert run['stop_reason']=='scene_space_render_budget_unavailable'
    assert len(author.submissions)==len(renderer.calls)==1
    assert len(completed['candidates'])==1
    assert completed['candidates'][0]['picture_url']
    budget=completed['experiments'][-1]['scene_space_render_status']
    assert budget['used_seconds'] is None and not budget['can_start_render']
    usage=completed['experiments'][-1]['scene_space_model_usage']
    assert usage['completeness']=='unknown' and usage['request_count'] is None


def test_host_render_budget_caps_author_plan_and_render_deadline_across_operations(system):
    service,author,reviewer,renderer=system
    project,_=start(service,max_rounds=1,max_seconds=60)
    for _ in range(8):
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['stage']=='render':break
        service.advance('developer',run['id'])
    assert run['stage']=='render'
    candidate=service._candidate(run)
    assert candidate['scene_space']['plan']['budget']['render']['limit_seconds']==3600
    service._scene_run_charge(run,'previous-measured-failure',outcome='failed',seconds=20)
    service.store.put('experiments',run)
    now=service.clock()
    service.advance('developer',run['id'])
    assert renderer.deadlines[0]<=now+40.1
    updated=service.store.read('experiments','developer',run['id'])
    budget=updated['scene_space_render_budget']
    assert budget['limit_seconds']==60
    assert budget['reservations'][0]['seconds']<=40
    assert service._scene_run_status(updated)['used_seconds']>=20


def test_exhausted_run_budget_stops_before_new_author_calls(system):
    service,author,reviewer,renderer=system
    project,_=start(service,max_seconds=60)
    run=service.store.read('experiments','developer',project['latest_experiment_id'])
    service._scene_run_charge(run,'measured-overrun',outcome='failed',seconds=61)
    service.store.put('experiments',run)
    completed=finish(service,project)
    assert completed['state']=='completed'
    assert author.submissions==renderer.calls==[]
    assert completed['experiments'][-1]['stop_reason']=='scene_space_render_budget_unavailable'


@pytest.mark.parametrize('completeness',['complete','partial'])
def test_failed_model_jobs_retain_actual_or_incomplete_usage(system,monkeypatch,completeness):
    service,author,reviewer,renderer=system
    original=author.poll
    receipt=dict(input_tokens=11,output_tokens=7,cache_read_tokens=3,cache_write_tokens=0,
        request_count=1,completeness=completeness)
    monkeypatch.setattr(author,'poll',lambda job_id:dict(original(job_id),
        state='failed',stop_proven=True,usage=deepcopy(receipt)))
    project,_=start(service,max_rounds=1)
    failed=finish(service,project)
    assert failed['state']=='failed' and renderer.calls==[]
    run=service.store.read('experiments','developer',project['latest_experiment_id'])
    saved=run['scene_space_model_receipts'][0]['receipt']
    assert saved['usage']==receipt
    status=service._scene_model_status(run)
    if completeness=='complete':
        assert status['request_count']==1 and status['token_components']['input_tokens']==11
    else:
        assert status['request_count'] is None and status['token_components'] is None


def test_run_usage_event_ids_cannot_replace_a_prior_measurement(system):
    service,author,reviewer,renderer=system
    project,_=start(service)
    run=service.store.read('experiments','developer',project['latest_experiment_id'])
    service._scene_run_charge(run,'one-measurement',outcome='failed',seconds=5)
    original=deepcopy(run['scene_space_render_budget'])
    service._scene_run_charge(run,'one-measurement',outcome='failed',seconds=5)
    assert run['scene_space_render_budget']==original
    with pytest.raises(ServiceError,match='settlement_changed'):
        service._scene_run_charge(run,'one-measurement',outcome='failed',seconds=0)


def test_pure_preflight_pins_the_request_before_claim(system,monkeypatch):
    service,author,reviewer,renderer=system
    states=[]
    prepare=renderer.validate_scene_request
    def inspect_claim(operation_id,*args,**kwargs):
        states.append(service.store.read('operations','developer',operation_id)['status'])
        return prepare(operation_id,*args,**kwargs)
    monkeypatch.setattr(renderer,'validate_scene_request',inspect_claim)
    project,_=start(service,max_rounds=1)
    completed=finish(service,project)
    assert completed['state']=='completed'
    assert states==['reserved','claimed']
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    renders=[service.store.read('operations','developer',identity) for identity in run['operation_ids']
        if service.store.read('operations','developer',identity)['stage']=='render']
    assert all(operation['scene_space_preflight']['request_sha256'] for operation in renders)
    assert all(operation['scene_space_preflight']['measured_prepare_seconds']>=0 for operation in renders)


def test_preclaim_rejection_is_terminal_not_unknown_and_settles_measured_cost(system):
    service,author,reviewer,renderer=system
    renderer.prepare_error='scene_space_cache_policy_not_admitted'
    project,_=start(service,max_rounds=1)
    failed=finish(service,project)
    assert failed['state']=='failed' and renderer.calls==[]
    run=service.store.read('experiments','developer',failed['latest_experiment_id'])
    operation=service.store.read('operations','developer',run['operation_ids'][-1])
    assert run['stop_proven'] is True and run['active_operation_id'] is None
    assert run['stop_reason']=='render_preflight_rejected'
    assert operation['status']=='failed' and operation['result']['not_dispatched'] is True
    assert operation['error']=='scene_space_cache_policy_not_admitted'
    assert operation['id'] not in renderer.calls
    event=next(row for row in run['scene_space_render_budget']['events'] if row['id']==operation['id'])
    assert event['outcome']=='failed' and event['seconds']>=0
    assert all(row['status']=='settled' for row in run['scene_space_render_budget']['reservations'])
    task=service._scene_store().get('developer',run['candidates'][0]['scene_space_task_id'])
    assert all(row['status']=='settled' for row in task['reservations'])


def test_pure_prepare_internal_failure_also_proves_no_dispatch(system,monkeypatch):
    service,author,reviewer,renderer=system
    monkeypatch.setattr(renderer,'validate_scene_request',lambda *args,**kwargs: (_ for _ in ()).throw(OSError('internal fixture')))
    project,_=start(service,max_rounds=1)
    failed=finish(service,project)
    assert failed['state']=='failed' and renderer.calls==[]
    assert failed['experiments'][-1]['error']['code']=='scene_space_learning_prepare_failed'
    assert failed['experiments'][-1]['error']['not_dispatched'] is True


def test_frozen_runtime_drift_fails_before_claim_or_dispatch(system):
    service,author,reviewer,renderer=system
    project,_=start(service,max_rounds=1)
    for _ in range(8):
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['stage']=='render':break
        service.advance('developer',run['id'])
    assert run['stage']=='render'
    renderer.admitted=False
    failed=finish(service,project)
    assert failed['state']=='failed'
    assert renderer.calls==renderer.prepared==[]
    assert failed['experiments'][-1]['error']['code']=='scene_space_learning_frozen_runtime_changed'


def test_runtime_proven_nondispatch_rejection_after_prepare_is_terminal(system,monkeypatch):
    service,author,reviewer,renderer=system
    def reject(*args,**kwargs):
        assert kwargs['expected_request_sha256']
        error=ServiceError('scene_space_render_expected_request_changed',409)
        error.not_dispatched=True
        error.stop_proven=True
        raise error
    monkeypatch.setattr(renderer,'render',reject)
    project,_=start(service,max_rounds=1)
    failed=finish(service,project)
    assert failed['state']=='failed' and renderer.calls==[]
    run=service.store.read('experiments','developer',failed['latest_experiment_id'])
    operation=service.store.read('operations','developer',run['operation_ids'][-1])
    assert operation['scene_space_preflight']['request_sha256']
    assert operation['result']['not_dispatched'] is operation['result']['stop_proven'] is True
    assert operation['status']=='failed' and not renderer.recovered


def test_unproven_exception_after_claim_keeps_original_operation_unknown(system,monkeypatch):
    service,author,reviewer,renderer=system
    monkeypatch.setattr(renderer,'render',lambda *args,**kwargs: (_ for _ in ()).throw(
        ServiceError('render_transport_unconfirmed',409)))
    project,_=start(service,max_rounds=1)
    for _ in range(8):
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['state']=='submission_unknown':break
        service.advance('developer',run['id'])
    assert run['state']=='submission_unknown' and run['stop_proven'] is False
    operation=service.store.read('operations','developer',run['active_operation_id'])
    assert operation['status']=='claimed' and operation['scene_space_preflight']['request_sha256']


def test_cancel_between_pure_prepare_and_claim_settles_cost_and_allows_frozen_retry(system):
    service,author,reviewer,renderer=system
    project,_=start(service,max_rounds=1)
    for _ in range(8):
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['stage']=='render':break
        service.advance('developer',run['id'])
    operation=service._operation(run)
    options=service._scene_render_options(run,operation,service._candidate(run))
    started=time.monotonic()
    deadline=service._scene_admit_render(run,operation,service._candidate(run),options)
    service._scene_prepare_render(run,operation,service._candidate(run),options,deadline,started=started)
    service.stop('developer',project['id'])
    cancelled=finish(service,project)
    assert cancelled['state']=='cancelled' and renderer.calls==[]
    cancelled_run=service.store.read('experiments','developer',run['id'])
    assert all(row['status']=='settled' for row in cancelled_run['scene_space_render_budget']['reservations'])
    task=service._scene_store().get('developer',run['candidates'][0]['scene_space_task_id'])
    assert all(row['status']=='settled' for row in task['reservations'])
    service.next_experiment('developer',project['id'],dict(request_id=uuid4().hex,mode='retry'))
    completed=finish(service,project)
    assert completed['state']=='completed'
    assert len(author.submissions)==1  # Frozen retry reuses the original authored source.


def test_shared_settlement_survives_a_crash_before_the_learning_run_is_saved(system):
    service,author,reviewer,renderer=system
    project,_=start(service,max_rounds=1)
    for _ in range(8):
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['stage']=='render':break
        service.advance('developer',run['id'])
    candidate=service._candidate(run)
    operation=service._operation(run)
    options=service._scene_render_options(run,operation,candidate)
    service._scene_admit_render(run,operation,candidate,options)
    shared=service._scene_store()
    shared.record_usage('developer',candidate['scene_space_task_id'],id=operation['id'],
        phase='render',outcome='failed',model_calls=0,model_tokens=0,render_seconds=.25)
    # Reload the old run: its ledger was not saved before the simulated crash.
    restored=service.store.read('experiments','developer',run['id'])
    assert not any(event['id']==operation['id'] for event in restored['scene_space_render_budget']['events'])
    service._scene_finish_render(restored,operation,service._candidate(restored),
        dict(status='failed',stop_proven=True),elapsed=.1)
    event=next(event for event in restored['scene_space_render_budget']['events'] if event['id']==operation['id'])
    assert event['seconds']==.25 and event['outcome']=='failed'
    assert all(item['status']=='settled' for item in restored['scene_space_render_budget']['reservations'])


class VideoSceneGateway(VideoGateway):
    def collect(self,job_id,input_sha256,document):
        result=super().collect(job_id,input_sha256,document)
        offer=document['constraints'].get('scene_space_learning')
        if document['mode']=='create' and offer:
            value=carrier(scope=offer['source_scope'],refs=offer['source_references'],
                frames=offer['source_frame_ids'],plan=True,
                viewport={key:document['constraints'][key] for key in ('width','height')},
                duration_frames=int(document['constraints']['duration_seconds']*24))
            if document['work_kind']=='transfer':
                value['world']['id']='new-video-world'
                value['world']['seed']+=1
                value['plan']=automatic_plan([value['world']],id='video-transfer',
                    viewport=value['plan']['viewport'],duration_frames=value['plan']['duration_frames'],
                    budget=default_budget(),intents=[dict(world_id=value['world']['id'],
                        weight=1,view='hold',focus=dict(x=700,y=400))])
            result['method']['scene_space']=value
        return result


class VideoSceneRenderer(VideoRenderer):
    prepare_error=None

    def __init__(self,root,raw):
        super().__init__(root,raw)
        self.scene_plans=[]
        self.prepared=[]

    def capabilities(self):
        result=super().capabilities()
        result['scene_space']=dict(schema='video.scene-space-render-capability.v1',admitted=True,
            engines=['canvas'],runtime_sha256='a'*64,artwork_api='window.__sceneSpaceArtwork')
        return result

    validate_scene_request=SceneRenderer.validate_scene_request

    def render(self,*args,scene_space=None,expected_request_sha256=None,**kwargs):
        if scene_space is not None:
            binding=self.validate_scene_request(*args,scene_space=scene_space,deadline_at=kwargs.get('deadline_at'))
            assert binding['request_sha256']==expected_request_sha256
        self.scene_plans.append(deepcopy(scene_space))
        return super().render(*args,**kwargs)


def test_video_sidecar_preserves_measured_frame_and_unobserved_motion_boundaries(tmp_path,real_mp4):
    author,reviewer=VideoSceneGateway(),VideoGateway()
    renderer=VideoSceneRenderer(tmp_path/'renders',real_mp4)
    service=VideoLearningService(tmp_path/'video',author_gateway=author,review_gateway=reviewer,renderer=renderer)
    project,_=start_video(service,real_mp4,max_rounds=1)
    completed=finish_video(service,project)
    assert completed['state']=='completed'
    assert len(renderer.scene_plans)==1
    assert all(work['work_kind']=='reproduction' for work in completed['candidates'])
    approved=completed['candidates'][0]['id']
    completed=finish_video(service,request_transfer(service,completed,approved))
    assert completed['state']=='completed'
    assert len(renderer.scene_plans)==2
    assert all(plan['duration_frames']==48 for plan in renderer.scene_plans)
    assert all(work['verification_scope']=='sampled_frames_only' for work in completed['candidates'])
    assert all(work['continuous_motion_verified'] is work['audio_verified'] is False
        for work in completed['candidates'])
    original=completed['candidates'][0]['scene_space']
    offered=author.uploads[0][0]['constraints']['scene_space_learning']
    assert original['source']['scope']=='sampled_frames_only'
    assert set(original['source']['frame_ids'])<=set(offered['source_frame_ids'])
    transfer=next(document for document,_ in author.uploads if document['work_kind']=='transfer')
    assert transfer['learned_method']['scene_space']['world'] is None
    assert not transfer['observation']['frames']
    records=service.capture_wikiskill_records('developer')
    works=[record['content'] for record in records if record['kind']=='video_learning_work']
    assert all(work['continuous_motion_verified'] is False for work in works)
    assert any(work.get('scene_space') for work in works)


def test_video_preclaim_rejection_does_not_create_a_render_or_lookup_dead_end(tmp_path,real_mp4):
    author,reviewer=VideoSceneGateway(),VideoGateway()
    renderer=VideoSceneRenderer(tmp_path/'renders',real_mp4)
    renderer.prepare_error='scene_space_assets_not_admitted'
    service=VideoLearningService(tmp_path/'video',author_gateway=author,review_gateway=reviewer,renderer=renderer)
    project,_=start_video(service,real_mp4,max_rounds=1)
    failed=finish_video(service,project)
    assert failed['state']=='failed' and not renderer.calls
    run=service.store.read('experiments','developer',failed['latest_experiment_id'])
    operation=service.store.read('operations','developer',run['operation_ids'][-1])
    assert run['stop_proven'] is True and run['stop_reason']=='render_preflight_rejected'
    assert operation['status']=='failed' and operation['result']['not_dispatched'] is True


def test_unknown_model_budget_does_not_admit_a_multiframe_video_as_a_diagnostic(tmp_path,real_mp4,monkeypatch):
    author,reviewer=VideoSceneGateway(),VideoGateway()
    collect=author.collect
    def unknown_tokens(*args,**kwargs):
        result=collect(*args,**kwargs)
        if result.get('source') is not None:
            result['method']['scene_space']['plan']['budget']['model']['used_tokens']=None
        return result
    monkeypatch.setattr(author,'collect',unknown_tokens)
    renderer=VideoSceneRenderer(tmp_path/'renders',real_mp4)
    service=VideoLearningService(tmp_path/'video',author_gateway=author,review_gateway=reviewer,renderer=renderer)
    project,_=start_video(service,real_mp4,max_rounds=1)
    failed=finish_video(service,project)
    assert failed['state']=='failed' and not renderer.calls and not reviewer.submissions
    run=service.store.read('experiments','developer',failed['latest_experiment_id'])
    assert run['error']['code']=='scene_space_export_budget_unavailable'
    assert run['candidates'][0]['scene_space']['plan']['duration_frames']==48
    assert not run.get('scene_space_diagnostic')
