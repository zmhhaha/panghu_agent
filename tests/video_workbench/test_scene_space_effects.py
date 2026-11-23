"""Spatial evidence is retained separately from portable method identities."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_store import AnimationMethodStore
from video_workbench.scene_space_effects import effect_scene_metadata,effect_scene_offer,portable_method
from video_workbench.scene_space_learning import validate_scene_method
from test_scene_space_learning import carrier
from test_animation_method_store import method_body


def test_method_attachment_does_not_rewrite_method_or_adopt_or_publish(tmp_path):
    store=AnimationMethodStore(tmp_path/'methods.sqlite3')
    method=store.create_method('alice',method_body(),'method')
    metadata=carrier()
    first=store.attach_scene_space('alice',method['id'],metadata,'spatial-1',evidence=dict(commission_id='one'))
    assert store.get_method('alice',method['id'])==method
    assert first['metadata']==validate_scene_method(metadata)
    assert not first['adoption_implied'] and not first['publication_implied']
    assert store.attach_scene_space('alice',method['id'],metadata,'spatial-1',evidence=dict(commission_id='one'))==first
    second=store.attach_scene_space('alice',method['id'],metadata,'spatial-2',evidence=dict(commission_id='two'))
    assert first['id']!=second['id']
    assert len(AnimationMethodStore(tmp_path/'methods.sqlite3').get_scene_space('alice',method['id']))==2
    with pytest.raises(ServiceError):store.get_scene_space('bob',method['id'])
    with pytest.raises(ServiceError):
        store.attach_scene_space('alice',method['id'],metadata,'spatial-1',evidence=dict(commission_id='changed'))


def test_optional_spatial_data_is_not_synthesized_for_an_old_method(tmp_path):
    store=AnimationMethodStore(tmp_path/'methods.sqlite3')
    method=store.create_method('alice',method_body(),'old-method')
    assert store.get_scene_space('alice',method['id'])==[]
    assert store.get_method('alice',method['id'])==method


def test_carrier_is_extracted_without_mutating_original_result():
    proposal=method_body();proposal['runtime_requirements']['scene_space']=carrier(scope='scene_data_only')
    original=deepcopy(proposal)
    cleaned=portable_method(proposal,enabled=True)
    assert proposal==original
    assert cleaned==method_body()
    assert portable_method(proposal,enabled=False)==original


def scene_run():
    return dict(frozen=dict(duration_seconds=4,constraints=dict(scene_space_capability=dict(admitted=True))),
        budget=dict(creator=2,review=1,render=1,seconds=3600))


def test_effect_source_contract_stays_three_files_and_only_host_admitted_carrier_is_read():
    proposal=method_body();proposal['runtime_requirements']['scene_space']=carrier(scope='scene_data_only')
    result=dict(methods=[proposal],source=dict(engine='canvas'))
    assert effect_scene_metadata(result,dict(frozen={})) is None
    assert effect_scene_metadata(result,scene_run())['source']['scope']=='scene_data_only'
    offer=effect_scene_offer(scene_run())
    assert offer['result_field']=='methods[].runtime_requirements.scene_space'
    assert all('in method.scene_space' not in text for text in offer['instructions'])
    assert offer['plan_budget']['model']['limit_calls']==3


def test_forged_source_references_cannot_become_effect_evidence():
    proposal=method_body();proposal['runtime_requirements']['scene_space']=carrier(scope='sampled_frames_only')
    result=dict(methods=[proposal],source=dict(engine='canvas'))
    with pytest.raises(ServiceError,match='source_changed'):
        effect_scene_metadata(result,scene_run(),dict(observation=dict(frames=[])))


def test_effect_plan_cannot_change_clock_or_expand_host_caps():
    plan=json.loads((Path(__file__).resolve().parents[1]/'fixtures/scene_space/plan.json').read_text(encoding='utf-8'))
    metadata=carrier(scope='scene_data_only');metadata['world']=plan['worlds'][0];metadata['plan']=plan
    proposal=method_body();proposal['runtime_requirements']['scene_space']=metadata
    result=dict(methods=[proposal],source=dict(engine='canvas'))
    with pytest.raises(ServiceError,match='host_budget_exceeded'):effect_scene_metadata(result,scene_run())
    plan['budget']=effect_scene_offer(scene_run())['plan_budget']
    assert effect_scene_metadata(result,scene_run())['plan']['duration_frames']==96
    wrong=scene_run();wrong['frozen']['duration_seconds']=5
    with pytest.raises(ServiceError,match='clock_changed'):effect_scene_metadata(result,wrong)


@pytest.mark.parametrize('render_behavior',['completed','not_dispatched','uncertain'])
def test_real_effect_orchestration_dispatches_plan_and_manual_snapshot_keeps_evidence(tmp_path,render_behavior):
    from test_animation_method_service import Gateway,Renderer,runtime_capabilities,start,finish
    from video_workbench.animation_method_service import AnimationMethodService
    from video_workbench.scene_space_plan import automatic_plan
    class SpatialAuthor(Gateway):
        def collect(self,job_id,checksum,document):
            result=super().collect(job_id,checksum,document)
            result['methods'][0]['runtime_requirements']['engine']='canvas'
            if document['mode']=='create':
                offer=document['constraints']['scene_space']
                metadata=carrier(scope='scene_data_only')
                metadata['plan']=automatic_plan([metadata['world']],id='effect-plan',
                    viewport=dict(width=1280,height=720),duration_frames=96,budget=offer['plan_budget'],
                    intents=[dict(world_id=metadata['world']['id'],weight=1,view='hold',focus=dict(x=700,y=400))])
                result['methods'][0]['runtime_requirements']['scene_space']=metadata
                result['source']=dict(engine='canvas',strategy=dict(description='Paint once',tradeoffs='Static evidence'),
                    files={'scene.html':'<canvas data-scene-space-output></canvas><script src="scene.js"></script>',
                        'scene.js':'window.__sceneSpaceArtwork={paintObject(ctx,obj){ctx.fillRect(0,0,10,10)}};',
                        'scene.css':'canvas{display:block}'})
            return result
    class SpatialRenderer(Renderer):
        def __init__(self,root):super().__init__(root);self.plans=[];self.finished={}
        def scene_space_capabilities(self):return dict(admitted=True,engines=['canvas'],cache_policies=['static'])
        def validate_scene_request(self,*args,**kwargs):return dict(request_sha256='d'*64)
        def render(self,*args,scene_space=None,expected_request_sha256=None,**kwargs):
            assert expected_request_sha256=='d'*64
            if render_behavior=='not_dispatched':
                error=ServiceError('scene_space_render_expected_request_changed',409)
                error.not_dispatched=True;error.stop_proven=True
                raise error
            self.plans.append(deepcopy(scene_space))
            outcome=super().render(*args,**kwargs)
            outcome['media']['duration_seconds']=4
            outcome['runtime']['engine']='canvas'
            outcome.update(status='rendered',stop_proven=True,technical_passed=True)
            self.finished[args[0]]=deepcopy(outcome)
            if render_behavior=='uncertain':outcome=dict(status='unknown',stop_proven=False)
            self.results[args[0]]=deepcopy(outcome)
            return outcome
    renderer=SpatialRenderer(tmp_path/'renders')
    service=AnimationMethodService(tmp_path/'lab',author_gateway=SpatialAuthor(),
        review_gateway=Gateway(True),renderer=renderer,runtime_capabilities=runtime_capabilities(['canvas']))
    started=start(service,duration_seconds=4,use_wikiskill=False)
    if render_behavior=='uncertain':
        for _ in range(20):service.advance('developer',started['id'])
        waiting=service.commissions.get('developer',started['id'])
        assert waiting['candidates']==[]
        operations=[op for op in waiting['operations'] if op['stage']=='render']
        assert len(operations)==1 and operations[0]['status']!='completed'
        assert len(renderer.calls)==1
        renderer.results.update(deepcopy(renderer.finished))
    run=finish(service,started['id'])
    if render_behavior=='not_dispatched':
        assert run['state']=='failed' and not renderer.calls
        operations=service.commissions.get('developer',started['id'])['operations']
        assert any(op['stage']=='creator' and op['result'] and op['result'].get('source') for op in operations)
        return
    assert run['state']=='completed' and len(renderer.plans)==1
    assert renderer.plans[0]['duration_frames']==96
    candidate=service.commissions.get('developer',run['id'])['candidates'][0]
    method=service.methods.get_method('developer',candidate['method_versions'][0])
    assert 'scene_space' not in method['runtime_requirements']
    retained=service.get_method_scene_space('developer',method['id'])['records']
    assert len(retained)==1 and retained[0]['evidence']['render_operation_id']==candidate['operation_id']
    assert retained[0]['evidence']['continuous_motion_observed'] is False
    records=service.capture_wikiskill_records('developer')
    spatial=[record for record in records if record['kind']=='method_scene_space']
    assert len(spatial)==1 and spatial[0]['content']['sha256']==retained[0]['sha256']
    assert service.methods.get_method('developer',method['id'])==method
