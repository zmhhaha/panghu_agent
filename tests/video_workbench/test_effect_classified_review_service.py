"""Actual effect coordinator/validators with synthetic renders and opinions."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.learning_review_reliability import CONTRACT
from video_workbench.scene_space_capture import SAMPLE_CAPTURE_POLICY
from video_workbench.scene_space_renderer import render_capability
from video_workbench.scene_space_plan import automatic_plan
from test_scene_space_learning import carrier
from test_animation_method_service import Gateway, Renderer, runtime_capabilities, start, finish
from test_learning_classified_review_service import ClassifiedGateway
from test_learning_review_reliability import png,capture


class Author(Gateway):
    missing_plan=False
    def collect(self,job_id,checksum,document):
        result=super().collect(job_id,checksum,document)
        result['methods'][0]['runtime_requirements']['engine']='canvas'
        if document['mode']=='create':
            offer=document['constraints']['scene_space'];metadata=carrier(scope='scene_data_only')
            metadata['plan']=None if self.missing_plan else automatic_plan([metadata['world']],id='effect-plan',
                viewport=dict(width=1280,height=720),duration_frames=96,budget=offer['plan_budget'],
                intents=[dict(world_id=metadata['world']['id'],weight=1,view='hold',focus=dict(x=700,y=400))])
            result['methods'][0]['runtime_requirements']['scene_space']=metadata
            result['source']=dict(engine='canvas',strategy=dict(description='Paint a shared scene once',tradeoffs='Static samples only'),
                files={'scene.html':'<canvas data-scene-space-output></canvas><script src="scene.js"></script>',
                    'scene.js':'window.__sceneSpaceArtwork={paintObject(ctx,obj){ctx.fillRect(0,0,10,10)}};',
                    'scene.css':'canvas{display:block}'})
        return result


class EffectRenderer(Renderer):
    def capabilities(self):return runtime_capabilities(['canvas'])
    def scene_space_capabilities(self):return render_capability()
    def validate_scene_request(self,*args,**kwargs):
        assert args[2]['constraints']['sample_capture']==SAMPLE_CAPTURE_POLICY
        return dict(request_sha256='d'*64)
    def render(self,*args,scene_space=None,expected_request_sha256=None,**kwargs):
        assert scene_space is not None and expected_request_sha256=='d'*64
        assert args[2]['constraints']['sample_capture']==SAMPLE_CAPTURE_POLICY
        result=super().render(*args,**kwargs)
        result['media']['duration_seconds']=4;result['runtime']['engine']='canvas'
        result.update(status='rendered',stop_proven=True,technical_passed=True,
            sample_capture=deepcopy(SAMPLE_CAPTURE_POLICY),sample_frame_range=dict(start_frame=0,end_frame=96))
        self.results[args[0]]=deepcopy(result);return result
    def frames(self,identity,max_frames=3):
        raw=png()
        return [dict(raw=raw,mime='image/png',timestamp_seconds=absolute/24,capture=capture(raw,absolute))
            for absolute in [0,48,95]]


def system(tmp_path,author=None):
    author,legacy,reviewer,renderer=author or Author(),Gateway(True),ClassifiedGateway(),EffectRenderer(tmp_path/'renders')
    service=AnimationMethodService(tmp_path/'effects',author_gateway=author,review_gateway=legacy,
        classified_review_gateway=reviewer,renderer=renderer,runtime_capabilities=runtime_capabilities(['canvas']))
    return service,author,legacy,reviewer,renderer


def test_effect_new_contract_stores_native_review_and_consume_gate_without_extra_repair(tmp_path):
    service,author,legacy,reviewer,renderer=system(tmp_path)
    created=start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT,
        budget=dict(max_rounds=2,max_seconds=3600))
    completed=finish(service,created['id']);run=service.commissions.get('developer',created['id'])
    assert completed['state']=='completed' and run['stop_reason']=='goal_met'
    assert len(renderer.calls)==len(reviewer.submissions)==1 and legacy.submissions==[]
    record=run['reviews'][0]
    assert record['evidence']['decision']['passed'] is True
    operation=next(row for row in run['operations'] if row['stage']=='review')
    assert operation['result']['schema']=='video.picture-review-result.v2'
    assert operation['result']['review']['findings'][0]['severity']=='advisory'
    inspected=service.operation_input('developer',created['id'],operation['id'])
    assert inspected['input']==operation['input'] and inspected['candidate_ids']==[run['candidates'][0]['id']]
    assert all(frame['file_name'].endswith('.png') for frame in operation['input']['observation']['frames'])
    assert record['evidence']['renderer_provenance']['comparison_scope'].startswith('current_output_only')
    assert run['frozen']['constraints']['classified_review']['model_limits']==dict(max_requests=8,max_output_tokens=8192)


def test_effect_missing_scene_plan_rejects_before_render_or_review_claim(tmp_path):
    service,author,legacy,reviewer,renderer=system(tmp_path);author.missing_plan=True
    created=start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT)
    completed=finish(service,created['id'])
    assert renderer.calls==reviewer.submissions==legacy.submissions==[]
    run=service.commissions.get('developer',created['id'])
    assert completed['state']=='failed' and run['summary']['code']=='effect_review_scene_plan_required'
    assert all(row['stage']=='creator' for row in run['operations'])


@pytest.mark.parametrize('cause',['missing','jpeg','frozen_route'])
def test_effect_definitive_capture_or_route_failure_stops_without_review_claim(tmp_path,cause):
    service,_,legacy,reviewer,renderer=system(tmp_path)
    created=start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT)
    if cause=='frozen_route':
        original=service._effect_review_preflight
        def changed(run,operation,gateway):
            reviewer.preflight=lambda _:dict(profile='changed')
            return original(run,operation,gateway)
        service._effect_review_preflight=changed
    else:
        frames=renderer.frames
        def changed(*args,**kwargs):
            rows=frames(*args,**kwargs)
            if cause=='missing':return rows[:-1]
            rows[0]['mime']='image/jpeg';return rows
        renderer.frames=changed
    completed=finish(service,created['id'])
    assert completed['state']=='failed' and len(renderer.calls)==1
    assert reviewer.uploads==reviewer.submissions==legacy.submissions==[]


def test_effect_wrong_classified_gateway_rejects_before_any_author_claim(tmp_path):
    service,author,_,reviewer,renderer=system(tmp_path);reviewer.profile='video.animation-method-review.v2'
    with pytest.raises(ServiceError,match='classified_review_unavailable'):
        start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT)
    assert author.submissions==reviewer.submissions==renderer.calls==[]


def test_effect_unobserved_reference_is_rejected_before_any_author_claim(tmp_path):
    service,author,_,reviewer,renderer=system(tmp_path)
    with pytest.raises(ServiceError,match='reference_not_admitted'):
        start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT,
            reference=dict(url='https://example.invalid/reference-not-observed'))
    assert author.submissions==reviewer.submissions==renderer.calls==[]


def test_effect_required_failure_keeps_native_blocker_and_does_not_use_optional_advice_as_repair(tmp_path):
    service,author,legacy,reviewer,renderer=system(tmp_path);reviewer.blocked=True
    created=start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT,
        budget=dict(max_rounds=2,max_seconds=3600))
    finished=finish(service,created['id']);run=service.commissions.get('developer',created['id'])
    assert finished['state']=='completed' and run['stop_reason']=='budget_exhausted'
    assert len(renderer.calls)==len(reviewer.submissions)==2 and legacy.submissions==[]
    assert all(row['outcome']=='inconclusive' and row['evidence']['decision']['passed'] is False
        for row in run['reviews'])
    assert 'Retain the issue.' in author.jobs[author.submissions[-1]]['document']['feedback']


def test_effect_skill_protocol_retains_native_review_fact_without_publishing_or_adoption(tmp_path):
    from agent_service.animation_skills_contract import build_skill_package
    from test_animation_skill_method_service import SkillGateway
    class SkillAuthor(Author,SkillGateway):
        def collect(self,*args):
            result=super().collect(*args)
            for method in result['methods']:
                package=method['skill_package']
                method['skill_package']=build_skill_package(skill_id=package['skill_id'],version=package['version'],
                    kind=package['kind'],files=package['files'],runtime_requirements=dict(engines=['canvas']))
            return result
    service,_,legacy,reviewer,renderer=system(tmp_path,author=SkillAuthor())
    created=start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT)
    finished=finish(service,created['id']);run=service.commissions.get('developer',created['id'])
    assert finished['state']=='completed' and len(reviewer.submissions)==1
    facts=[row for row in service.knowledge.list_experiences('developer') if row['kind']=='classified_sampled_review']
    assert len(facts)==1 and facts[0]['document']['review_schema']=='video.picture-review-result.v2'
    assert service.published_catalogue('developer')['methods']==[] and service.knowledge.list_pages('developer')==[]
    assert service.capture_wikiskill_records('developer')
    assert all(row['acceptance'] is None for candidate in run['candidates']
        for version_id in candidate['method_versions'] for row in service.methods.list_validations('developer',version_id))


def test_effect_stop_recommendation_ends_without_another_creator_or_goal_met(tmp_path):
    service,author,legacy,reviewer,renderer=system(tmp_path);collect=reviewer.collect
    def stopped(*args):
        result=collect(*args);result['review']['stop_recommended']=True;return result
    reviewer.collect=stopped
    created=start(service,duration_seconds=4,use_wikiskill=False,review_contract=CONTRACT,
        budget=dict(max_rounds=2,max_seconds=3600))
    finished=finish(service,created['id']);run=service.commissions.get('developer',created['id'])
    assert finished['state']=='failed' and run['summary']['code']=='effect_classified_review_recommends_stop'
    assert run['reviews'][0]['outcome']=='inconclusive' and run['reviews'][0]['evidence']['decision']['passed'] is False
    assert len(renderer.calls)==len(reviewer.submissions)==1 and legacy.submissions==[]
    assert len([row for row in author.jobs.values() if row['document']['mode']=='create'])==1
    assert next(row for row in run['operations'] if row['stage']=='review')['result']['review']['stop_recommended'] is True
