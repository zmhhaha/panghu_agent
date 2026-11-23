"""Deterministic scene planning, bounded camera coverage and revision lineage."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.scene_space_contract import world_sha256
from video_workbench.scene_space_plan import (
    FPS, MAX_DURATION_FRAMES, PLAN_SCHEMA, automatic_plan, compile_evaluator,
    default_budget, default_provenance, evaluate_frame, evaluate_frames, freeze_plan,
    representative_frames, required_coverage, revise_plan, split_intervals,
    technical_retry, validate_plan,
)


FIXTURE = Path(__file__).resolve().parents[1]/'fixtures/scene_space/plan.json'


@pytest.fixture
def plan():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def one_shot(plan, *, duration=96):
    result = deepcopy(plan)
    result['duration_frames'] = duration
    result['shots'] = [deepcopy(result['shots'][0])]
    result['shots'][0]['end_frame'] = duration
    return result


def track(*, identity='drift', target='far-mountain', prop='x', duration=96, end=12):
    return dict(id=identity, target_id=target, property=prop,
        keyframes=[dict(frame=0, value=1 if prop in {'scale', 'opacity'} else 0),
                   dict(frame=duration, value=end, easing='smoothstep')])


def revision_of(plan):
    revised = deepcopy(plan)
    revised['revision'] += 1
    revised['budget']['revisions']['used'] += 1
    return revised


def objects(frame):
    return {row['id']: row for row in frame['objects']}


def test_fixture_freezes_without_mutation_or_claiming_pixel_observation(plan):
    original = deepcopy(plan)
    normalized = validate_plan(plan)
    assert plan == original
    assert validate_plan(normalized) == normalized
    frozen = freeze_plan(plan)
    assert frozen == freeze_plan(plan)
    assert frozen['plan_sha256'] == digest(normalized)
    assert frozen['world_bindings']['river-world'] == dict(
        revision=1, sha256=world_sha256(plan['worlds'][0]))
    frame = evaluate_frame(frozen, 0)
    assert frame['world_sha256'] == frozen['world_bindings']['river-world']['sha256']
    assert frame['geometry_scope'] == 'declared_scene_geometry_only'
    assert frame['visual_observed'] is frame['motion_observed'] is frame['audio_observed'] is False
    assert all(row['pixels_observed'] is False for row in frame['objects'])
    assert frame['objects'][0]['visibility_scope'] == 'bounds_only'


def test_random_access_and_repeated_seeks_have_no_mutable_clock(plan):
    evaluator = compile_evaluator(plan)
    frames = evaluator.frames([95, 0, 48, 95, 12, 96])
    assert frames[0] == frames[3]
    assert [row['shot_id'] for row in frames[:3]] == ['approach', 'still', 'approach']
    assert frames[0]['time_seconds'] == 95/FPS
    assert frames[-1]['camera'] == dict(x=760, y=400, zoom=1.2)
    frames[0]['objects'][0]['screen_matrix'][0] = 99
    assert evaluator.frame(95) == frames[3]
    with pytest.raises(ServiceError):
        evaluator.frame(97)


def test_parent_transforms_and_opacity_follow_safe_local_animation(plan):
    plan = one_shot(plan)
    world = plan['worlds'][0]
    world['relations'] = [row for row in world['relations']
        if row['id'] not in {'root-on-bank', 'tree-supported'}]
    plan['shots'][0]['local_animation'] = [
        track(target='near-tree', end=12),
        track(identity='fade', target='near-tree', prop='opacity', end=.5),
    ]
    start, end = evaluate_frames(plan, [0, 96])
    before, after = objects(start), objects(end)
    assert after['leaf-group']['world_matrix'][4] - before['leaf-group']['world_matrix'][4] == 12
    assert after['near-tree']['opacity'] == after['leaf-group']['opacity'] == .5
    assert after['leaf-group']['screen_anchors']['base'] == after['near-tree']['screen_anchors']['leaf-join']
    assert start['camera'] == end['camera']


def test_empty_local_animation_and_fixed_shots_are_valid(plan):
    plan = one_shot(plan)
    assert evaluate_frame(plan, 0)['camera'] == evaluate_frame(plan, 96)['camera']
    assert plan['shots'][0]['local_animation'] == []
    assert not any(row['kind'] in {'fog', 'bird'} for row in plan['worlds'][0]['objects'])


def test_long_execution_splitting_preserves_world_and_absolute_time(plan):
    plan = one_shot(plan, duration=MAX_DURATION_FRAMES)
    normalized = validate_plan(plan)
    pieces = split_intervals(plan)
    assert len(pieces) == 5
    assert [(row['start_frame'], row['end_frame']) for row in pieces] == [
        (0, 1440), (1440, 2880), (2880, 4320), (4320, 5760), (5760, 7200)]
    assert all(row['plan_sha256'] == digest(normalized) and row['absolute_clock']
        and row['world_id'] == 'river-world' for row in pieces)
    frames = evaluate_frames(plan, [1439, 1440, 7199, 7200])
    assert frames[0]['interval_id'] != frames[1]['interval_id']
    assert frames[1]['time_seconds'] == 60
    assert frames[-1]['interval_id'] == frames[-2]['interval_id']
    assert len(split_intervals(plan, 30)) == 10
    with pytest.raises(ServiceError):
        split_intervals(plan, 61)
    with pytest.raises(ServiceError):
        split_intervals(plan, .1)


def test_representative_samples_include_real_shot_and_interval_boundaries(plan):
    plan = one_shot(plan, duration=3000)
    samples = representative_frames(plan)
    assert {0, 1439, 1440, 2879, 2880, 2999} <= set(samples)
    with pytest.raises(ServiceError, match='preview_frame_budget'):
        representative_frames(plan, 2)


def test_coverage_bounds_all_frames_and_estimates_overscan_work_surface(plan):
    coverage = required_coverage(plan)
    assert coverage['spatial_task_frames_checked'] is True
    assert coverage['temporary_memory_measured'] is False
    assert coverage['continuous_between_frames_verified'] is False
    pixels = plan['viewport']['width'] * plan['viewport']['height']
    assert coverage['estimated_temporary_bytes'] == (max(row['pixels'] for row in coverage['layers']) + 2*pixels)*4
    assert coverage['estimated_temporary_bytes'] > 3*pixels*4
    assert coverage['bounded_temporary_bytes'] == coverage['estimated_temporary_bytes']
    assert coverage['validation_work_measured'] is False
    assert 0 < coverage['estimated_validation_work_units'] <= coverage['max_validation_work_units']
    assert all(row['max_scale'] > 1 for row in coverage['layers'])
    plan['budget']['render']['max_temporary_bytes'] = 3*pixels*4
    with pytest.raises(ServiceError, match='temporary_budget_exceeded'):
        validate_plan(plan)


def test_camera_cannot_leave_artwork_or_exceed_declared_magnification(plan):
    plan['shots'][1]['camera']['keyframes'][-1]['x'] = 2000
    with pytest.raises(ServiceError, match='camera_outside_artwork'):
        validate_plan(plan)
    plan = one_shot(json.loads(FIXTURE.read_text(encoding='utf-8')))
    plan['shots'][0]['camera']['max_zoom'] = .9
    with pytest.raises(ServiceError, match='magnification_exceeded'):
        validate_plan(plan)


def test_complex_static_polygon_does_not_get_revalidated_for_every_opacity_frame(plan):
    plan = one_shot(plan, duration=MAX_DURATION_FRAMES)
    world = plan['worlds'][0]
    polygon = dict(id='detailed-shore',kind='polygon',points=[
        [700+100*math.cos(i*2*math.pi/256),400+100*math.sin(i*2*math.pi/256)] for i in range(256)])
    world['layout']['regions'].append(polygon)
    plan['shots'][0]['local_animation'] = [track(prop='opacity',duration=MAX_DURATION_FRAMES,end=.5)]
    coverage = required_coverage(plan)
    assert coverage['estimated_validation_work_units'] < coverage['max_validation_work_units']
    # A geometry track would repeat the polygon topology checks at every frame.
    plan['shots'][0]['local_animation'] = [track(duration=MAX_DURATION_FRAMES,end=12)]
    with pytest.raises(ServiceError, match='validation_work_budget_exceeded') as caught:
        freeze_plan(plan)
    assert caught.value.details['estimated_work_units'] > caught.value.details['limit_work_units']
    assert caught.value.details['measured'] is False


def test_intermediate_camera_keys_are_checked_not_only_shot_endpoints(plan):
    plan = one_shot(plan)
    plan['worlds'][0]['layers'] = [dict(row, parallax=1) for row in plan['worlds'][0]['layers']]
    # An intermediate keyframe outside artwork is still detected even when the
    # first and last cameras are identical and valid.
    plan['shots'][0]['camera']['keyframes'] = [
        dict(frame=0, x=700, y=400, zoom=1),
        dict(frame=48, x=500, y=400, zoom=1),
        dict(frame=96, x=700, y=400, zoom=1)]
    with pytest.raises(ServiceError, match='camera_outside_artwork'):
        freeze_plan(plan)


def test_cross_layer_attachment_drift_is_rejected_at_first_bad_frame(plan):
    next(obj for obj in plan['worlds'][0]['objects'] if obj['id'] == 'shore')['layer_id'] = 'middle'
    with pytest.raises(ServiceError, match='projected_attachment_mismatch') as caught:
        freeze_plan(plan)
    assert caught.value.details['relation_id'] == 'root-on-bank'
    assert caught.value.details['shot_id'] == 'approach'
    assert caught.value.details['frame'] > 48


def test_attachment_can_match_both_camera_keys_but_drift_between_them(plan):
    plan = one_shot(plan)
    world = plan['worlds'][0]
    next(obj for obj in world['objects'] if obj['id'] == 'shore')['layer_id'] = 'middle'
    # Camera endpoints put both projections of root (250,650) together. The
    # interpolated camera does not follow the nonlinear required locus.
    plan['shots'][0]['camera'] = dict(max_zoom=1.2, keyframes=[
        dict(frame=0,x=700,y=400,zoom=1),
        dict(frame=96,x=700-90/1.32,y=400+50/1.32,zoom=1.2)])
    with pytest.raises(ServiceError, match='projected_attachment_mismatch') as caught:
        freeze_plan(plan)
    assert 0 < caught.value.details['frame'] < 96


def test_cross_layer_parent_drift_is_rejected_even_without_explicit_attachment(plan):
    world = plan['worlds'][0]
    world['relations'] = [row for row in world['relations'] if row['id'] != 'leaf-on-tree']
    next(obj for obj in world['objects'] if obj['id'] == 'leaf-group')['layer_id'] = 'middle'
    with pytest.raises(ServiceError, match='parent_projection_mismatch'):
        validate_plan(plan)


def test_projected_relative_scale_is_checked_throughout_camera_motion(plan):
    plan['worlds'][0]['relations'].append(dict(id='peak-scale', kind='scale',
        object_id='main-peak', reference_object_id='far-mountain', metric='width', min_ratio=.8, max_ratio=.81))
    with pytest.raises(ServiceError, match='projected_scale_mismatch') as caught:
        validate_plan(plan)
    assert caught.value.details['relation_id'] == 'peak-scale'


@pytest.mark.parametrize('kind', ['attachment', 'support', 'scale'])
def test_dynamic_geometry_cannot_break_declared_world_relations(plan, kind):
    plan = one_shot(plan)
    if kind == 'attachment':
        local = track(target='near-tree', end=25)
    elif kind == 'support':
        plan['worlds'][0]['relations'] = [row for row in plan['worlds'][0]['relations']
            if row['id'] != 'root-on-bank']
        local = track(target='near-tree', prop='y', end=120)
    else:
        plan['worlds'][0]['relations'].append(dict(id='peak-scale', kind='scale',
            object_id='main-peak', reference_object_id='far-mountain', metric='width', min_ratio=.8, max_ratio=.81))
        local = track(target='main-peak', prop='scale', end=1.2)
    plan['shots'][0]['local_animation'] = [local]
    with pytest.raises(ServiceError, match=f'scene_world_{kind}_mismatch') as caught:
        freeze_plan(plan)
    assert 0 < caught.value.details['frame'] <= 96


def test_continuous_shot_changes_cannot_reset_camera_or_local_tracks(plan):
    plan['shots'][1]['camera']['keyframes'][0]['x'] = 701
    with pytest.raises(ServiceError, match='camera_discontinuity'):
        validate_plan(plan)
    plan['shots'][1]['camera']['keyframes'][0]['x'] = 700
    plan['shots'][0]['local_animation'] = [dict(id='fade', target_id='far-mountain', property='opacity',
        keyframes=[dict(frame=0, value=.5)])]
    with pytest.raises(ServiceError, match='local_animation_discontinuity'):
        validate_plan(plan)


def test_frozen_hash_and_retry_are_bound_to_exact_plan_and_preserve_usage(plan):
    plan['budget']['model']['used_calls'] = 2
    plan['budget']['model']['used_tokens'] = 500
    plan['budget']['render']['used_seconds'] = 10
    frozen = freeze_plan(plan, max_interval_seconds=30)
    retry = technical_retry(frozen, frozen['plan_sha256'])
    assert retry == frozen and retry is not frozen
    assert retry['plan']['budget'] == plan['budget']
    with pytest.raises(ServiceError, match='retry_plan_changed'):
        technical_retry(frozen, '0'*64)
    changed = deepcopy(frozen)
    changed['plan']['shots'][0]['camera']['keyframes'][0]['x'] += 1
    with pytest.raises(ServiceError, match='frozen_plan_changed'):
        evaluate_frame(changed, 0)
    # Rehashing just the envelope cannot hide stale bindings or coverage.
    changed['sha256'] = digest({k:v for k,v in changed.items() if k != 'sha256'})
    with pytest.raises(ServiceError, match='frozen_plan_changed'):
        evaluate_frame(changed, 0)


def test_viewport_revision_invalidates_all_projection_outputs_and_caches(plan):
    changed = revision_of(plan)
    changed['viewport']['width'] = 1200
    result = revise_plan(plan, changed, reason='Change the output aspect ratio')
    report = result['revision']
    assert report['global_geometry_changed'] is True
    assert report['affected_shot_ids'] == ['approach', 'still']
    assert report['invalidated_cache_ids'] == ['river-world/far', 'river-world/middle', 'river-world/near']
    assert {'spatial', 'coverage', 'deterministic_frames', 'visual_preview'} == set(report['required_rechecks'])
    assert report['reusable_world_ids'] == ['river-world']
    assert report['visual_acceptance'] is False


def test_parent_world_revision_invalidates_dependents_and_all_world_layers(plan):
    changed = revision_of(plan)
    changed['worlds'][0]['revision'] += 1
    # A seed revision requires regeneration without executing any source.
    changed['worlds'][0]['seed'] += 1
    for shot in changed['shots']:
        shot['world_revision'] = 2
    report = revise_plan(plan, changed, reason='Revise world seed')['revision']
    assert report['changed_world_ids'] == ['river-world']
    assert 'river-world/leaf-group' in report['affected_object_ids']
    assert len(report['invalidated_cache_ids']) == 3
    assert report['reusable_world_ids'] == []


@pytest.mark.parametrize('field,new', [('used_calls', 1), ('used_tokens', 100)])
def test_creative_revision_cannot_reset_consumed_model_budget(plan, field, new):
    plan['budget']['model'][field] = new+1
    changed = revision_of(plan)
    changed['budget']['model'][field] = new
    with pytest.raises(ServiceError, match='budget_history_reset'):
        revise_plan(plan, changed, reason='Try resetting prior cost')


def test_unknown_token_usage_cannot_be_replaced_by_zero_in_a_revision(plan):
    plan['budget']['model']['used_tokens'] = None
    changed = revision_of(plan)
    changed['budget']['model']['used_tokens'] = 0
    with pytest.raises(ServiceError, match='budget_history_reset'):
        revise_plan(plan, changed, reason='Erase unknown cost')


def test_revision_and_caps_have_finite_bounded_lineage(plan):
    changed = revision_of(plan)
    changed['budget']['model']['limit_calls'] += 1
    with pytest.raises(ServiceError, match='budget_cap_changed'):
        revise_plan(plan, changed, reason='Add free calls')
    changed = revision_of(plan)
    changed['budget']['revisions']['used'] = 0
    with pytest.raises(ServiceError, match='revision_budget_reset'):
        revise_plan(plan, changed, reason='Reset revision count')
    assert isinstance(default_budget()['model']['limit_tokens'], int)
    assert default_budget()['model']['used_tokens'] == 0
    with pytest.raises(ServiceError):
        default_budget(model_tokens=None)
    with pytest.raises(ServiceError):
        validate_plan(plan, caps=dict(model_calls=1))


def test_automatic_planner_allocates_clock_and_chooses_motion_without_extra_content(plan):
    world = plan['worlds'][0]
    result = automatic_plan([world], id='automatic-river', viewport=plan['viewport'], duration_frames=240,
        intents=[dict(world_id=world['id'], weight=1, view='hold', focus=dict(x=700,y=400)),
            dict(world_id=world['id'],weight=3,view='pan-zoom',focus=dict(x=700,y=400),
                 end_focus=dict(x=760,y=400),end_zoom=1.2,transition='continuous')],
        budget=default_budget())
    assert result['schema'] == PLAN_SCHEMA
    assert result['worlds'] == validate_plan(plan)['worlds']
    assert [row['end_frame']-row['start_frame'] for row in result['shots']] == [61, 179]
    assert len(result['shots'][0]['camera']['keyframes']) == 1
    assert len(result['shots'][1]['camera']['keyframes']) == 2
    assert all(row['local_animation'] == [] for row in result['shots'])


def test_automatic_local_intents_compile_fractions_to_absolute_frames(plan):
    world = plan['worlds'][0]
    intent = dict(world_id=world['id'],weight=1,view='hold',focus=dict(x=700,y=400),
        local_animation=[dict(id='drift',target_id='far-mountain',property='x',
            keyframes=[dict(at=0,value=0),dict(at=1,value=12)])])
    before = deepcopy(intent)
    result = automatic_plan([world], id='automatic-drift', viewport=plan['viewport'], duration_frames=24,
        intents=[intent], budget=default_budget())
    assert intent == before
    assert [key['frame'] for key in result['shots'][0]['local_animation'][0]['keyframes']] == [0,24]
    assert 'at' not in result['shots'][0]['local_animation'][0]['keyframes'][0]


@pytest.mark.parametrize('path,bad', [
    (('provenance','origin'), []), (('provenance','evidence_scope'), {}),
    (('shots',0,'world_id'), []), (('shots',0,'transition'), []),
    (('shots',0,'world_revision'), True),
    (('shots',0,'camera','keyframes',0,'easing'), {}),
    (('shots',0,'camera','keyframes',0,'x'), 10**400),
    (('shots',0,'camera','keyframes',0,'zoom'), float('inf')),
    (('shots',0,'camera','keyframes',0,'zoom'), float('nan')),
    (('budget','model','limit_tokens'), None),
    (('provenance','motion_observed'), True),
])
def test_malformed_input_is_a_contract_error_not_an_implementation_exception(plan, path, bad):
    target = plan
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with pytest.raises(ServiceError):
        validate_plan(plan)


@pytest.mark.parametrize('key,bad', [('target_id', []), ('property', {}), ('id', True)])
def test_malformed_local_tracks_are_rejected(plan, key, bad):
    plan = one_shot(plan)
    local = track()
    local[key] = bad
    plan['shots'][0]['local_animation'] = [local]
    with pytest.raises(ServiceError):
        validate_plan(plan)


@pytest.mark.parametrize('key,bad', [('world_id', []), ('view', {}), ('end_zoom', []),
    ('local_animation', {}), ('local_animation', [[]]), ('transition', [])])
def test_malformed_automatic_intents_are_rejected(plan, key, bad):
    intent = dict(world_id='river-world',weight=1,view='hold',focus=dict(x=700,y=400))
    intent[key] = bad
    with pytest.raises(ServiceError):
        automatic_plan(plan['worlds'], id='automatic-invalid', viewport=plan['viewport'],
            duration_frames=24, intents=[intent], budget=default_budget())


def test_unknown_extra_fields_and_non_video_clocks_are_rejected(plan):
    plan['fps'] = 60
    with pytest.raises(ServiceError):
        validate_plan(plan)
    plan['fps'] = FPS
    plan['shots'][0]['camera']['orbit'] = True
    with pytest.raises(ServiceError):
        validate_plan(plan)


def test_unobserved_sources_and_clips_do_not_become_verified_video_evidence(plan):
    plan['provenance'] = dict(default_provenance(), origin='inferred', evidence_scope='static_image')
    with pytest.raises(ServiceError, match='source_evidence_required'):
        validate_plan(plan)
    plan['provenance']['source_refs'] = [dict(id='image-1',sha256='a'*64)]
    assert validate_plan(plan)['provenance']['motion_observed'] is False
    plan['provenance']['whole_video_watched'] = True
    with pytest.raises(ServiceError, match='observation_not_admitted'):
        validate_plan(plan)
