"""Pure compiler checks; synthetic timing does not prove real video quality."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion import make_beats, estimated_alignment, sha256
from video_workbench.motion_process import compile_process_project
from video_workbench.process_continuity import (
    build_process_continuity_plan, validate_process_continuity_plan, resolve_process_continuity,
    compile_process_continuity_project, recompile_process_continuity_project,
    process_scene_summary, process_continuity_summaries, expected_process_continuity_states,
)


def process_scenes(count=3, *, repeat=False):
    result = []
    legs = [('source', 'queue'), ('queue', 'worker'), ('worker', 'queue')]
    for index in range(count):
        start, end = legs[0 if repeat else index % 3]
        beats = make_beats('请求经过处理环节。执行节点开始工作。保留处理状态。')
        ids = [row['id'] for row in beats['segments']]
        elements = [dict(id=slot, kind='card', slot=slot, label=label, detail='同一处理环节')
                    for slot, label in [('source', '入口'), ('queue', '队列'), ('worker', '执行器')]]
        elements.append(dict(id='request', kind='token', slot='token_'+start, label='请求', detail=''))
        plan = dict(schema='video.process-plan.v1', template='flow', title=f'过程第{index+1}步',
            takeaway='对象移动，状态继续保留。', elements=elements,
            relations=[dict(id='route', source=start, target=end, label='传递')], actions=[
                dict(kind='draw', target='route', beat_id=ids[0], placement='start', pace='quick'),
                dict(kind='reveal', target='request', beat_id=ids[0], placement='middle', pace='quick'),
                dict(kind='transfer', target='request', relation_id='route', beat_id=ids[1], placement='start', pace='slow'),
                dict(kind='set_state', target='worker', state='running', beat_id=ids[1], placement='end', pace='normal')])
        alignment = estimated_alignment(beats, f'{index+1:064x}', 12)
        result.append(dict(id=f'scene-{index}', title=plan['title'], audio=dict(id=f'audio-{index}',
            sha256=alignment['audio_sha256']), motion=dict(id=f'motion-{index}', plan=plan, beats=beats,
            alignment=alignment, compiler_version='motion-process-compiler-2'),
            timing=dict(leading_seconds=0, trailing_seconds=0, duration_seconds=12)))
    return result


def test_three_scene_retention_preserves_real_states_token_locations_and_identity():
    scenes = process_scenes()
    before = deepcopy(scenes)
    plan = build_process_continuity_plan(scenes)
    assert len(plan['links']) == 8 and {row['mode'] for row in plan['links']} == {'retain'}
    result = resolve_process_continuity(scenes, plan)
    first, second, third = result['segments']
    assert first['final_checkpoint'] == second['initial_checkpoint']
    assert second['final_checkpoint'] == third['initial_checkpoint']
    assert first['entity_map'] == second['entity_map'] == third['entity_map']
    worker = first['entity_map']['worker']
    token = first['entity_map']['request']
    assert second['initial_states'][worker]['state'] == 'running'
    assert second['initial_states'][token]['slot'] == 'token_queue'
    assert third['initial_states'][token]['slot'] == 'token_worker'
    assert any(event['scope'] == 'state' and event['kind'] == 'hold' for event in second['timeline']['actions'])
    for scene, segment in zip(scenes, result['segments']):
        assert segment['timeline']['audio_sha256'] == scene['audio']['sha256']
        assert segment['timeline']['audio_seconds'] == 12
        actual = expected_process_continuity_states(segment, segment['timeline']['duration_seconds']-1/24)
        for identity, wanted in segment['final_checkpoint']['objects'].items():
            assert actual['elements'][identity] == wanted
    assert scenes == before


def test_copy_is_explicit_has_new_identity_and_originals_remain_across_three_scenes():
    scenes = process_scenes(repeat=True)
    plan = build_process_continuity_plan(scenes)
    assert [row['mode'] for row in plan['links'] if row['to_element_id'] == 'request'] == ['copy', 'copy']
    result = resolve_process_continuity(scenes, plan)
    first, second, third = result['segments']
    source = first['entity_map']['request']
    target = second['entity_map']['request']
    assert source != target
    assert second['copy_lineage'] == [dict(source=source, target=target)]
    assert second['final_checkpoint']['objects'][source]['slot'] == 'archive_0'
    assert source in third['final_checkpoint']['objects']
    assert third['final_checkpoint']['objects'][target]['slot'] == 'archive_1'
    assert third['initial_checkpoint'] == second['final_checkpoint']
    for scene, segment in zip(scenes, result['segments']):
        assert compile_process_continuity_project(scene, segment)


def test_archive_capacity_fails_before_silently_discarding_an_original():
    scenes = process_scenes(count=5, repeat=True)
    plan = build_process_continuity_plan(scenes)
    with pytest.raises(ServiceError, match='process_continuity_copy_archive_unavailable'):
        resolve_process_continuity(scenes, plan)


def test_audio_free_summaries_report_effective_inherited_state_and_independent_baseline():
    scenes = process_scenes()
    for scene in scenes:
        scene.pop('audio')
        scene.pop('timing')
        scene['motion'].pop('alignment')
    assert process_scene_summary(scenes[0])['elements'][2]['initial_state'] == 'idle'
    summaries = process_continuity_summaries(scenes, build_process_continuity_plan(scenes))
    worker = next(row for row in summaries[2]['elements'] if row['id'] == 'worker')
    assert worker['initial_state'] == worker['final_state'] == 'running'
    assert worker['standalone_initial_state'] == 'idle'


def test_each_segment_rebuilds_exactly_and_keeps_old_process2_bytes():
    scenes = process_scenes()
    source = scenes[0]['motion']
    old = compile_process_project(source['plan'], source['beats'], source['alignment'])
    result = resolve_process_continuity(scenes, build_process_continuity_plan(scenes))
    for scene, segment in zip(scenes, result['segments']):
        files = compile_process_continuity_project(scene, segment)
        assert len(files) == 6
        assert files == recompile_process_continuity_project(json.loads(files['plan.json']),
            json.loads(files['beats.json']), json.loads(files['alignment.json']))
        assert json.loads(files['manifest.json'])['compiler'] == 'motion-process-continuity-compiler-1'
    assert compile_process_project(source['plan'], source['beats'], source['alignment']) == old


def test_transfer_runtime_uses_transform_offsets_from_each_frozen_initial_pose():
    scenes = process_scenes(repeat=True)
    resolution = resolve_process_continuity(scenes, build_process_continuity_plan(scenes))
    for scene, segment in zip(scenes, resolution['segments']):
        page = compile_process_continuity_project(scene, segment)['index.html'].decode()
        script = page.rsplit('<script>', 1)[1].split('tl.set({}, {},data.timeline.duration_seconds)', 1)[0]
        # Strict HyperFrames permits transform motion. Static CSS positioning
        # is not an animation, and must remain the per-object base for seeking.
        assert '{left:' not in script and '{top:' not in script
        assert "key==='x'?'left'" not in script and "key==='y'?'top'" not in script
        assert 'x:points[i-1][0]-base.width/2-base.x' in script
        assert 'y:points[i-1][1]-base.height/2-base.y' in script
        for event in segment['timeline']['actions']:
            if event['process_kind'] != 'transfer':
                continue
            base = segment['initial_states'][event['target']]
            for endpoint, position in zip((event['points'][0], event['points'][-1]),
                                          (event['before'], event['after'])):
                dx, dy = endpoint[0]-base['width']/2-base['x'], endpoint[1]-base['height']/2-base['y']
                assert (base['x']+dx, base['y']+dy) == (position['x'], position['y'])
        # Last->zero->middle->zero uses the frozen base rather than accumulating
        # offsets from the previous seek or a copy's later archive location.
        last = segment['timeline']['duration_seconds']-1/24
        initial = expected_process_continuity_states(segment, 0)
        for time in (last, 0, .375, 0):
            sample = expected_process_continuity_states(segment, time)
            if time == 0:
                assert sample == initial


def test_unsafe_links_and_inherited_state_conflicts_are_rejected_before_audio():
    scenes = process_scenes()
    plan = build_process_continuity_plan(scenes)
    bad = deepcopy(plan)
    bad['links'][0]['mode'] = 'copy'
    with pytest.raises(ServiceError, match='process_continuity_card_copy_not_supported'):
        validate_process_continuity_plan(bad, scenes)
    repeat = process_scenes(count=2, repeat=True)
    copy = build_process_continuity_plan(repeat)
    next(row for row in copy['links'] if row['to_element_id'] == 'request')['mode'] = 'retain'
    with pytest.raises(ServiceError, match='process_continuity_slot_conflict'):
        validate_process_continuity_plan(copy, repeat)
    # completed cannot silently reset to running across retained identity.
    for scene in scenes[:1]:
        row = scene['motion']['plan']['actions'][-1]
        scene['motion']['plan']['actions'].append(dict(row, state='completed', placement='start',
            beat_id=scene['motion']['beats']['segments'][2]['id']))
    with pytest.raises(ServiceError, match='process_continuity_state_conflict'):
        validate_process_continuity_plan(plan, scenes)
    assert not any(row['to_element_id'] == 'worker' and row['to_scene_id'] == 'scene-1'
                   for row in build_process_continuity_plan(scenes)['links'])


def test_corrupted_state_checkpoint_or_source_binding_does_not_rebuild():
    scenes = process_scenes(count=1)
    segment = resolve_process_continuity(scenes, dict(schema='video.continuity-plan.v1', links=[]))['segments'][0]
    for field in ('source_binding_sha256', 'final_checkpoint'):
        bad = deepcopy(segment)
        if field == 'source_binding_sha256':
            bad[field] = '0'*64
        else:
            next(iter(bad[field]['objects'].values()))['state'] = 'failed'
        bad['sha256'] = sha256({key: value for key, value in bad.items() if key != 'sha256'})
        with pytest.raises(ServiceError):
            compile_process_continuity_project(scenes[0], bad)
