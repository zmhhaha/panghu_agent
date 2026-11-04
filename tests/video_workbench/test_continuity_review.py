"""Contract-level tamper tests; these fixtures are not real Chrome evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.continuity import build_continuity_plan, resolve_continuity, compile_continuity_project
from video_workbench.continuity_review import expected_states, _text_bounds
from video_workbench.motion import FPS, action_windows, sha256
from video_workbench.motion_review import verify_observations
from tests.video_workbench.test_continuity import scenes


def rgb(value):
    if type(value) is tuple:
        return 'rgb('+', '.join(str(round(channel)) for channel in value)+')'
    if value.startswith('#'):
        return 'rgb('+', '.join(str(int(value[index:index+2],16)) for index in (1,3,5))+')'
    return value


def box(x=72,y=74,width=100,height=30,**extra):
    return dict(x=x,y=y,width=width,height=height,overflow=False,**extra)


def observation(segment,time):
    expected = expected_states(segment,time)
    elements = {}
    for identity,item in expected['elements'].items():
        x,y,width,height = (item[key] for key in ('x','y','width','height'))
        area = dict(left=x+2,top=y+2,right=x+width-2,bottom=y+height-2)
        child = box(area['left'],area['top'],10,10,scrollWidth=10,clientWidth=10,scrollHeight=10,clientHeight=10)
        text_boxes = {key:deepcopy(child) for key in (('copy','label','detail') if item['kind'] == 'card' else ('copy','label'))}
        elements[identity] = box(x,y,width,height,opacity=item['opacity'],highlightWidth=item['outlineWidth'],
            borderColor=rgb(item['borderColor']),backgroundColor=rgb(item['backgroundColor']),
            label=item['label'],detail=item['detail'],text_area=area,text_boxes=text_boxes)
        if identity in segment['detail_updates']:
            update,wanted = segment['detail_updates'][identity],expected['detail_updates'][identity]
            heights = {key:27 if update[key] else 0 for key in ('before','after')}
            elements[identity]['detailLayers'] = {key:dict(text=update[key],opacity=wanted[key+'_opacity'],
                x=area['left'],y=area['top']+10,width=20,height=heights[key],
                scrollWidth=20,clientWidth=20,scrollHeight=heights[key],clientHeight=heights[key])
                for key in ('before','after')}
            elements[identity]['detailBox'] = dict(x=area['left'],y=area['top']+10,width=20,
                height=heights['before']+(heights['after']-heights['before'])*wanted['progress'],
                marginTop=update['before_margin']+(update['after_margin']-update['before_margin'])*wanted['progress'],
                oldHeight=heights['before'],newHeight=heights['after'])
    relations,labels = {},[]
    for identity,item in expected['relations'].items():
        relations[identity] = {key:item[key] for key in ('opacity','strokeDashoffset','markerOpacity','labelOpacity')}
        relations[identity]['length'] = 100
        x,y,width,height = item['box']
        labels.append(box(x,y,width,height,id=identity,text=item['label'],plate=dict(x=x,y=y,width=width,height=height),
            ink=dict(x=x+1,y=y+1,width=max(0,width-2),height=max(0,height-2)) if item['label'] else None,
            overlapsNodes=[],overlapsLabels=[]))
    headers = {}
    for key,item in expected['headers'].items():
        checkpoint = segment['initial_checkpoint'] if key == 'previous' else segment['final_checkpoint']
        headers[key] = dict(opacity=item['opacity'],title=box(text=checkpoint['title']),takeaway=box(y=636,text=checkpoint['takeaway']))
    return dict(time=time,actual_time=time,elements=elements,relations=relations,
        layout=dict(title=box(),takeaway=box(y=636),headers=headers,relations=labels,fontReady=True),
        frame_sha256=hashlib.sha256(str(time).encode()).hexdigest())


def fixture(*,copy=False,detail_change=False):
    inputs = scenes(single=True)
    if detail_change:
        inputs[1]['motion']['plan']['elements'][0]['detail'] = '保留完整回答与尝试记录'
    plan = build_continuity_plan(inputs)
    if copy:
        plan['links'][0]['mode'] = 'copy'
    segment = resolve_continuity(inputs,plan)['segments'][1]
    files = compile_continuity_project(inputs[1],segment)
    wrapper,timeline = (json.loads(files[name]) for name in ('plan.json','timeline.json'))
    windows = [dict(ordinal=action['ordinal'],**{key:observation(segment,time) for key,time in times.items()
                 if key in {'before','middle','after'}})
               for action,times in zip(timeline['actions'],action_windows(timeline))]
    initial,final = observation(segment,0),observation(segment,timeline['duration_seconds']-1/FPS)
    boundary = dict(initial=initial,final=final,repeated_initial=deepcopy(initial),
                    transition_middle=observation(segment,.375),transition_end=observation(segment,.6))
    observations = dict(schema='video.motion-observations.v1',seek_protocol='hyperframes-core-renderSeek-exact',
        render_fps=24,timeline_sha256=sha256(timeline),windows=windows,continuity_boundary=boundary,
        reverse_seek=dict(first_sha256=initial['frame_sha256'],last_sha256=final['frame_sha256'],
                          repeated_first_sha256=initial['frame_sha256'],repeat_state_equal=True))
    return wrapper,timeline,observations


@pytest.mark.parametrize('copy',[False,True])
def test_continuity_review_accepts_complete_contract_evidence(copy):
    wrapper,timeline,observations = fixture(copy=copy)
    result = verify_observations(wrapper,timeline,observations)
    assert result['technical_status'] == result['continuity']['boundary_status'] == 'passed'
    assert result['timing_review_status'] == 'pending'


def test_continuity_review_rejects_missing_boundaries_even_with_all_action_windows():
    wrapper,timeline,observations = fixture()
    observations.pop('continuity_boundary')
    with pytest.raises(ServiceError,match='continuity_boundary_observation_missing'):
        verify_observations(wrapper,timeline,observations)


def test_continuity_review_rejects_reset_identity_at_next_zero_frame():
    wrapper,timeline,observations = fixture()
    sample = observations['continuity_boundary']['initial']
    next(iter(sample['elements'].values()))['opacity'] = 0
    with pytest.raises(ServiceError,match='continuity_object_opacity_mismatch'):
        verify_observations(wrapper,timeline,observations)


def test_continuity_review_rejects_disappearing_copy_source_and_text_overflow():
    wrapper,timeline,observations = fixture(copy=True)
    source = wrapper['segment']['copy_lineage'][0]['source']
    observations['continuity_boundary']['transition_middle']['elements'][source]['opacity'] = 0
    with pytest.raises(ServiceError,match='continuity_object_opacity_mismatch'):
        verify_observations(wrapper,timeline,observations)
    wrapper,timeline,observations = fixture()
    next(iter(observations['continuity_boundary']['final']['elements'].values()))['overflow'] = True
    with pytest.raises(ServiceError,match='motion_text_overflow'):
        verify_observations(wrapper,timeline,observations)


def test_continuity_review_rejects_changed_boundary_title():
    wrapper,timeline,observations = fixture()
    observations['continuity_boundary']['initial']['layout']['headers']['previous']['title']['text'] = 'wrong'
    with pytest.raises(ServiceError,match='continuity_header_reset'):
        verify_observations(wrapper,timeline,observations)


def test_continuity_review_rejects_frozen_screenshots_despite_forged_changing_metrics():
    wrapper,timeline,observations = fixture()
    for row in observations['windows']:
        for key in ('before','middle','after'):
            row[key]['frame_sha256'] = 'a'*64
    with pytest.raises(ServiceError,match='motion_frozen_frames'):
        verify_observations(wrapper,timeline,observations)


def test_continuity_review_rejects_missing_native_seek_at_the_boundary():
    wrapper,timeline,observations = fixture()
    observations['continuity_boundary']['initial']['actual_time'] = .1
    with pytest.raises(ServiceError,match='motion_seek_time_mismatch'):
        verify_observations(wrapper,timeline,observations)


def test_detail_change_contract_verifies_both_text_layers_resize_and_reverse_state():
    wrapper,timeline,observations = fixture(detail_change=True)
    assert verify_observations(wrapper,timeline,observations)['technical_status'] == 'passed'
    identity = next(iter(wrapper['segment']['detail_updates']))
    observations['continuity_boundary']['repeated_initial']['elements'][identity]['detailLayers']['before']['text'] = '反向后错误文本'
    with pytest.raises(ServiceError,match='continuity_detail_content_mismatch'):
        verify_observations(wrapper,timeline,observations)


def test_detail_change_rejects_hidden_instant_swap_or_clipping_visible_new_text():
    wrapper,timeline,observations = fixture(detail_change=True)
    identity = next(iter(wrapper['segment']['detail_updates']))
    sample = observations['continuity_boundary']['transition_end']['elements'][identity]
    sample['detailLayers']['after']['opacity'] = 0
    with pytest.raises(ServiceError,match='continuity_detail_content_mismatch'):
        verify_observations(wrapper,timeline,observations)
    wrapper,timeline,observations = fixture(detail_change=True)
    observations['continuity_boundary']['transition_end']['elements'][identity]['detailBox']['height'] = 0
    with pytest.raises(ServiceError,match='continuity_detail_resize_mismatch'):
        verify_observations(wrapper,timeline,observations)


def float_boundary_state():
    """The real t=.525 retained-card DOMRect from the Chrome preflight."""
    left,right = 95.740699768,595.740699768
    child = box(left,220,500.0000305175781,27,
                scrollWidth=500,clientWidth=500,scrollHeight=27,clientHeight=27)
    return box(79.740699768,200,532,132,
               text_area=dict(left=left,top=216,right=right,bottom=316),
               text_boxes={key:deepcopy(child) for key in ('copy','label','detail')})


def test_real_float32_domrect_roundoff_is_accepted_only_in_new_continuity_review():
    state = float_boundary_state()
    _text_bounds(state,'card')
    from video_workbench.motion_review import _text_bounds as legacy_text_bounds
    with pytest.raises(ServiceError,match='motion_text_overflow'):
        legacy_text_bounds(state,'card')


@pytest.mark.parametrize('extra',[.001001,.01,.5,1.0])
def test_coordinate_tolerance_does_not_hide_real_text_overflow(extra):
    state = float_boundary_state()
    state['text_boxes']['label']['width'] = 500+extra
    with pytest.raises(ServiceError,match='motion_text_overflow'):
        _text_bounds(state,'card')


@pytest.mark.parametrize('scroll,client',[('scrollWidth','clientWidth'),('scrollHeight','clientHeight')])
def test_integer_scroll_overflow_remains_exact_despite_domrect_roundoff(scroll,client):
    state = float_boundary_state()
    state['text_boxes']['detail'][scroll] = state['text_boxes']['detail'][client]+1
    with pytest.raises(ServiceError,match='motion_text_overflow'):
        _text_bounds(state,'card')
