"""Continuity retains identity and complete independent audio without widening v1 plans."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.continuity import (
    CONTINUITY_COMPILER_VERSION, build_continuity_plan, validate_continuity_plan,
    resolve_continuity, compile_continuity_project, recompile_continuity_project,
)
from video_workbench.motion import make_beats, estimated_alignment, example_plan, sha256
from video_workbench.continuity_review import expected_states


def scenes(*, template='flow', single=False):
    result = []
    for index in range(2):
        beats = make_beats('请求到达。进入队列。执行节点开始工作。')
        plan = example_plan(beats,template=template)
        if single:
            plan['elements'] = [plan['elements'][0]]
            plan['relations'] = []
            plan['actions'] = [dict(beat_id=beats['segments'][0]['id'],target='node0',kind='reveal',
                                    placement='start',pace='normal',to_slot=None)]
        alignment = estimated_alignment(beats,hex(index+1)[2:]*64,15)
        result.append(dict(id=f'scene-{index}',title=f'第{index+1}镜',narration=beats['source_text'],
            audio=dict(id=f'audio-{index}',sha256=alignment['audio_sha256']),
            motion=dict(id=f'motion-{index}',plan=plan,beats=beats,alignment=alignment),
            timing=dict(leading_seconds=0,trailing_seconds=0,duration_seconds=15)))
    return result


def test_candidates_use_only_exact_unique_adjacent_appearances_and_support_sparse_store_rows():
    inputs = scenes()
    for row in inputs:
        row.pop('audio')
        row.pop('timing')
        row['motion'].pop('alignment')
    result = build_continuity_plan(inputs)
    assert len(result['links']) == len(inputs[0]['motion']['plan']['elements'])
    inputs[1]['motion']['plan']['elements'][0]['detail'] = '改变了含义'
    assert any(link['to_element_id'] == 'node0' and link['mode'] == 'retain'
               for link in build_continuity_plan(inputs)['links'])
    duplicate = dict(inputs[0]['motion']['plan']['elements'][0],id='duplicate',slot='note')
    inputs[0]['motion']['plan']['elements'].append(duplicate)
    inputs[1]['motion']['plan']['elements'][0]['detail'] = ''
    assert not any(link['to_element_id'] == 'node0' for link in build_continuity_plan(inputs)['links'])


@pytest.mark.parametrize('feature', ['sequence', 'nested', 'sources'])
def test_continuity_does_not_silently_recompile_new_semantics_as_compiler5(feature):
    inputs = scenes(template=feature if feature != 'sources' else 'flow')
    if feature == 'sources':
        for row in inputs:
            row['motion']['plan']['sources'] = [dict(url='https://example.org/full-source', label='官方资料')]
    with pytest.raises(ServiceError, match='continuity_semantic_layout_not_supported'):
        build_continuity_plan(inputs)
    with pytest.raises(ServiceError, match='continuity_semantic_layout_not_supported'):
        resolve_continuity(inputs, dict(schema='video.continuity-plan.v1', links=[]))


@pytest.mark.parametrize('field,value',[('mode','guess'),('to_scene_id','unrelated'),('from_element_id','absent')])
def test_invalid_or_ambiguous_links_fail_closed(field,value):
    inputs = scenes(single=True)
    plan = build_continuity_plan(inputs)
    plan['links'][0][field] = value
    with pytest.raises(ServiceError):
        validate_continuity_plan(plan,inputs)


def test_one_source_or_target_cannot_be_reused_and_public_input_has_no_coordinates():
    inputs = scenes(single=True)
    plan = build_continuity_plan(inputs)
    plan['links'].append(deepcopy(plan['links'][0]))
    with pytest.raises(ServiceError,match='continuity_ambiguous_link'):
        validate_continuity_plan(plan,inputs)
    plan = build_continuity_plan(inputs)
    plan['links'][0]['x'] = 100
    with pytest.raises(ServiceError):
        validate_continuity_plan(plan,inputs)


@pytest.mark.parametrize('template',['flow','compare','focus'])
def test_adjacent_scene_checkpoint_and_identity_are_preserved_with_whole_original_audio(template):
    inputs = scenes(template=template)
    original = deepcopy(inputs)
    resolved = resolve_continuity(inputs,build_continuity_plan(inputs))
    first,second = resolved['segments']
    assert first['final_checkpoint'] == second['initial_checkpoint']
    assert first['entity_map'] == second['entity_map']
    assert first['timeline']['audio_sha256'] == inputs[0]['audio']['sha256']
    assert second['timeline']['audio_sha256'] == inputs[1]['audio']['sha256']
    assert second['timeline']['audio_seconds'] == 15
    assert second['timeline']['leading_seconds'] == .6
    assert second['global_start_frame'] == first['global_end_frame']
    assert inputs == original


def test_retained_reveal_becomes_an_explicit_hold_and_initial_browser_state_stays_visible():
    inputs = scenes(single=True)
    segment = resolve_continuity(inputs,build_continuity_plan(inputs))['segments'][1]
    identity = segment['entity_map']['node0']
    assert segment['initial_states'][identity]['opacity'] == 1
    assert any(event['target'] == identity and event['kind'] == 'hold'
               and event['reason'] == 'retained_already_visible' for event in segment['timeline']['actions'])
    assert expected_states(segment,0)['elements'][identity]['opacity'] == 1


def test_copy_has_new_identity_lineage_and_source_remains_in_a_safe_archive():
    inputs = scenes(single=True)
    plan = build_continuity_plan(inputs)
    plan['links'][0]['mode'] = 'copy'
    first,second = resolve_continuity(inputs,plan)['segments']
    source,target = first['entity_map']['node0'],second['entity_map']['node0']
    assert source != target and second['copy_lineage'] == [dict(source=source,target=target)]
    assert {source,target} <= set(second['final_checkpoint']['objects'])
    assert second['final_checkpoint']['objects'][target]['copied_from'] == source
    middle = expected_states(second,.375)['elements']
    assert middle[source]['opacity'] == middle[target]['opacity'] == 1
    before,after = (second['final_checkpoint']['objects'][key] for key in (source,target))
    assert before['x'] >= after['x']+after['width'] or after['x'] >= before['x']+before['width']


def test_copy_rejects_full_canvas_instead_of_dropping_the_source():
    inputs = scenes()
    plan = build_continuity_plan(inputs)
    plan['links'][0]['mode'] = 'copy'
    # Occupy the final otherwise empty, full-width note slot.
    for scene in inputs:
        scene['motion']['plan']['elements'].append(dict(id='note',kind='card',label='说明',detail='',slot='note'))
    with pytest.raises(ServiceError,match='continuity_copy_archive_unavailable'):
        resolve_continuity(inputs,plan)


def test_compilation_is_reproducible_and_does_not_double_add_effective_leading():
    inputs = scenes(single=True)
    segment = resolve_continuity(inputs,build_continuity_plan(inputs))['segments'][1]
    first = compile_continuity_project(inputs[1],segment)
    effective = deepcopy(inputs[1])
    effective['continuity_base_timing'] = effective['timing']
    effective['timing'] = dict(effective['timing'],leading_seconds=.6)
    assert compile_continuity_project(effective,segment) == first
    plan,beats,alignment = (json.loads(first[name]) for name in ('plan.json','beats.json','alignment.json'))
    assert recompile_continuity_project(plan,beats,alignment) == first
    assert json.loads(first['timeline.json'])['compiler'] == CONTINUITY_COMPILER_VERSION
    assert json.loads(first['timeline.json'])['audio_sha256'] == inputs[1]['audio']['sha256']
    assert json.loads(first['manifest.json'])['files']['plan.json'] == sha256(first['plan.json'])


def test_changed_scene_audio_or_narration_rejects_frozen_segment():
    inputs = scenes(single=True)
    segment = resolve_continuity(inputs,build_continuity_plan(inputs))['segments'][1]
    inputs[1]['audio']['sha256'] = 'e'*64
    with pytest.raises(ServiceError,match='continuity_source_stale'):
        compile_continuity_project(inputs[1],segment)


def test_retained_objects_cannot_swap_through_each_other_during_transition():
    inputs = scenes()
    elements = inputs[1]['motion']['plan']['elements']
    elements[0]['slot'],elements[1]['slot'] = elements[1]['slot'],elements[0]['slot']
    with pytest.raises(ServiceError,match='continuity_unsafe_transition'):
        resolve_continuity(inputs,build_continuity_plan(inputs))


def test_internal_segment_cannot_smuggle_arbitrary_css_even_with_a_recomputed_hash():
    inputs = scenes(single=True)
    segment = resolve_continuity(inputs,build_continuity_plan(inputs))['segments'][1]
    segment['timeline']['actions'][0]['before']['onComplete'] = 'alert(1)'
    segment['timeline']['actions'][0]['after']['onComplete'] = 'alert(1)'
    segment['sha256'] = sha256({key:value for key,value in segment.items() if key != 'sha256'})
    with pytest.raises(ServiceError,match='continuity_segment_invalid'):
        compile_continuity_project(inputs[1],segment)


def test_internal_segment_cannot_smuggle_attributes_in_a_connector_path():
    inputs = scenes()
    segment = resolve_continuity(inputs,build_continuity_plan(inputs))['segments'][0]
    next(iter(segment['relations'].values()))['path'] = 'M 0 0" onload="alert(1)'
    segment['sha256'] = sha256({key:value for key,value in segment.items() if key != 'sha256'})
    with pytest.raises(ServiceError,match='continuity_segment_invalid'):
        compile_continuity_project(inputs[0],segment)


def test_detail_update_keeps_identity_and_outer_checkpoint_but_stages_old_and_new_text():
    inputs = scenes(single=True)
    inputs[1]['motion']['plan']['elements'][0]['detail'] = '保留完整回答与尝试记录'
    plan = build_continuity_plan(inputs)
    assert len(plan['links']) == 1
    first,second = resolve_continuity(inputs,plan)['segments']
    identity = first['entity_map']['node0']
    assert second['entity_map']['node0'] == identity
    assert second['initial_checkpoint'] == first['final_checkpoint']
    assert second['initial_states'][identity]['detail'] == ''
    assert second['final_checkpoint']['objects'][identity]['detail'] == '保留完整回答与尝试记录'
    assert expected_states(second,0)['elements'][identity]['detail'] == ''
    assert expected_states(second,.375)['detail_updates'][identity]['before_opacity'] == 0
    assert expected_states(second,.375)['detail_updates'][identity]['after_opacity'] == 0
    assert expected_states(second,.6)['elements'][identity]['detail'] == '保留完整回答与尝试记录'
    files = compile_continuity_project(inputs[1],second)
    wrapper,beats,alignment = (json.loads(files[name]) for name in ('plan.json','beats.json','alignment.json'))
    assert recompile_continuity_project(wrapper,beats,alignment) == files
    assert 'detail-before' in files['index.html'].decode() and 'detail-after' in files['index.html'].decode()
    assert 'onUpdate:' not in files['index.html'].decode()  # Exact seek may suppress callbacks.


def test_detail_change_does_not_enable_copy_or_cross_kind_identity_guessing():
    inputs = scenes(single=True)
    inputs[1]['motion']['plan']['elements'][0]['detail'] = '新的说明'
    plan = build_continuity_plan(inputs)
    plan['links'][0]['mode'] = 'copy'
    with pytest.raises(ServiceError,match='continuity_copy_content_changed'):
        validate_continuity_plan(plan,inputs)
    inputs[1]['motion']['plan']['elements'][0]['kind'] = 'token'
    assert build_continuity_plan(inputs)['links'] == []
