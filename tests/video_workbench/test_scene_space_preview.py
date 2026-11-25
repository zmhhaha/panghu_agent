"""Inspection artifacts preserve scene identity without claiming painted proof."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from agent_service.contracts import ServiceError
from agent_service.scene_space_contract import world_geometry, world_sha256
from video_workbench.scene_space_preview import (
    frame_layout_svg, inspect_world, layout_svg, preview_frames, production_preview_plan,
    write_plan_preview, write_world_preview,
)
from video_workbench.scene_space_plan import evaluate_frame, freeze_plan
from video_workbench.examples.scene_space_demo import build_demo


FIXTURE = Path(__file__).resolve().parents[1]/'fixtures/scene_space/world.json'
NS = {'s': 'http://www.w3.org/2000/svg'}


@pytest.fixture
def world():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


@pytest.fixture
def plan(world):
    return build_demo(world)


@pytest.fixture
def production_plan(plan):
    value = deepcopy(plan)
    value['duration_frames'] = 192
    value['shots'] = [deepcopy(value['shots'][0])]
    value['shots'][0]['end_frame'] = 192
    return value


def test_layout_is_repeatable_and_matches_the_declared_world_anchors(world):
    original = deepcopy(world)
    raw = layout_svg(world, show_grid=True)
    assert layout_svg(world, show_grid=True) == raw
    assert world == original
    root = ET.fromstring(raw)
    metadata = json.loads(root.find('s:metadata', NS).text)
    assert metadata['world_sha256'] == world_sha256(world)
    assert metadata['scope'] == 'layout_only_masks_outlined'
    assert metadata['recipes_executed'] == 0
    geometry = world_geometry(world)
    for anchor in root.findall('.//s:circle[@data-anchor-id]', NS):
        point = geometry[anchor.attrib['data-object-id']]['anchors'][anchor.attrib['data-anchor-id']]
        assert float(anchor.attrib['cx']) == pytest.approx(point[0])
        assert float(anchor.attrib['cy']) == pytest.approx(point[1])
    # No executable content or external resources belong to a geometry preview.
    assert not root.findall('.//s:script', NS)
    assert not root.findall('.//s:image', NS)
    assert not root.findall('.//s:foreignObject', NS)


def test_data_validation_is_distinct_from_art_motion_and_audio(world):
    report = inspect_world(world)
    assert report['status'] == 'valid'
    assert report['checks'] == dict(contract='passed', spatial='passed')
    assert report['visual_quality'] == 'not_assessed'
    assert report['continuous_motion'] == report['audio'] == 'not_observed'
    assert report['evidence_scope'] == 'scene_data_and_geometry_only'


def test_invalid_layout_writes_a_diagnostic_without_a_misleading_svg(world, tmp_path):
    world['objects'][0]['parent_id'] = world['objects'][0]['id']
    report = write_world_preview(world, tmp_path/'invalid')
    assert report['status'] == 'invalid'
    assert report['checks']['spatial'] == 'not_completed'
    assert report['issues'] and report['world_sha256'] is None
    assert not (tmp_path/'invalid/layout.svg').exists()
    assert json.loads((tmp_path/'invalid/layout-report.json').read_bytes()) == report


def test_export_does_not_overwrite_an_older_preview_with_a_new_claim(world, tmp_path):
    destination = tmp_path/'preview'
    report = write_world_preview(world, destination)
    raw = (destination/'layout.svg').read_bytes()
    assert report['artifact']['sha256'] == hashlib.sha256(raw).hexdigest()
    invalid = deepcopy(world)
    invalid['objects'][0]['parent_id'] = invalid['objects'][0]['id']
    with pytest.raises(ServiceError, match='preview_destination_exists'):
        write_world_preview(invalid, destination)
    assert (destination/'layout.svg').read_bytes() == raw
    assert json.loads((destination/'layout-report.json').read_bytes())['status'] == 'valid'


def test_grid_is_an_explicit_diagnostic_and_does_not_change_scene_identity(world):
    identity = world_sha256(world)
    basic = layout_svg(world, show_grid=False)
    detailed = layout_svg(world, show_grid=True)
    assert world_sha256(world) == identity
    assert 'scene-space-layout-svg.v1' in basic and 'scene-space-layout-svg.v1' in detailed
    if world['layout']['grid']:
        assert basic != detailed
    assert 'layout_only_masks_outlined' in detailed


def test_plan_samples_include_shot_and_execution_boundaries(plan):
    frozen = freeze_plan(plan)
    frames = preview_frames(plan)
    for section in frozen['plan']['shots']+frozen['intervals']:
        assert section['start_frame'] in frames
        assert section['end_frame']-1 in frames
    assert frames == sorted(set(frames))
    assert max(frames) < plan['duration_frames']
    # Missing mandatory samples is a budget failure, not a truncated success.
    with pytest.raises(ServiceError,match='frame_budget_exceeded'):
        preview_frames(plan,max_frames=1)


def test_projected_layout_anchors_match_absolute_frame_state(plan):
    frames = preview_frames(plan)
    for frame in (frames[0],frames[len(frames)//2],frames[-1]):
        state = evaluate_frame(plan,frame)
        objects = {item['id']:item for item in state['objects']}
        raw = frame_layout_svg(plan,frame)
        assert frame_layout_svg(plan,frame) == raw
        root = ET.fromstring(raw)
        for anchor in root.findall('.//s:circle[@data-anchor-id]',NS):
            point = objects[anchor.attrib['data-object-id']]['screen_anchors'][anchor.attrib['data-anchor-id']]
            assert float(anchor.attrib['cx']) == pytest.approx(point[0])
            assert float(anchor.attrib['cy']) == pytest.approx(point[1])
        metadata = json.loads(root.find('s:metadata',NS).text)
        assert metadata['plan_sha256'] == state['plan_sha256']
        assert metadata['continuous_motion'] == metadata['audio'] == 'not_observed'
        assert metadata['visibility_scope'] == 'bounds_only'


def test_plan_export_binds_artifacts_to_validated_geometry_without_motion_claim(plan,tmp_path):
    report = write_plan_preview(plan,tmp_path/'plan')
    assert report['status'] == 'valid'
    assert report['visual_quality'] == 'not_assessed'
    assert report['continuous_motion'] == report['audio'] == 'not_observed'
    assert report['recipes_executed'] == 0
    assert report['evidence_scope'] == 'scene_data_and_sampled_geometry_only'
    assert report['frames'] == preview_frames(plan)
    for artifact in report['artifacts']:
        assert hashlib.sha256((tmp_path/'plan'/artifact['path']).read_bytes()).hexdigest() == artifact['sha256']
    frozen = json.loads((tmp_path/'plan/frozen-plan.json').read_bytes())
    assert frozen['plan_sha256'] == report['plan_sha256']


def test_invalid_plan_preserves_diagnostics_without_rendering_frames(plan,tmp_path):
    plan['shots'][0]['world_id'] = 'missing'
    report = write_plan_preview(plan,tmp_path/'bad-plan')
    assert report['status'] == 'invalid' and report['issues']
    assert report['frames'] == report['artifacts'] == []
    assert not list((tmp_path/'bad-plan').glob('*.svg'))
    assert not (tmp_path/'bad-plan/frozen-plan.json').exists()


def test_production_gate_is_pure_and_binds_frozen_geometry_without_pixel_claims(production_plan):
    original = deepcopy(production_plan)
    frozen = freeze_plan(production_plan)
    report = production_preview_plan(frozen)
    assert report == production_preview_plan(production_plan)
    assert production_plan == original and frozen == freeze_plan(production_plan)
    assert report['plan_sha256'] == frozen['plan_sha256']
    assert report['static_frames'] == [0, 191]
    assert report['clips'] == [dict(start_frame=0, end_frame=48, reason='static_plan')]
    assert report['rendered'] is False
    layout = report['layout_report']
    assert layout['checks'] == dict(contract='passed', spatial='passed')
    assert layout['coverage']['spatial_task_frames_checked'] is True
    assert layout['coverage'] == frozen['coverage']
    assert layout['worlds'][0]['world_sha256'] == world_sha256(production_plan['worlds'][0])
    assert layout['visibility_scope'] == 'bounds_only'
    assert layout['pixel_occlusion'] == layout['visual_quality'] == 'not_assessed'
    assert layout['continuous_motion'] == layout['audio'] == 'not_observed'
    assert layout['recipes_executed'] == 0
    assert all(frame['pixels_observed'] is False for frame in layout['frames'])
    assert len(json.dumps(report)) < 4 * 1024 * 1024
    # Returned metadata has no mutable alias to the original envelope.
    layout['coverage']['layers'][0]['world_id'] = 'changed'
    assert frozen['coverage']['layers'][0]['world_id'] != 'changed'


def test_production_static_samples_use_greatest_drawable_zoom_not_exclusive_key(production_plan):
    first = production_plan['shots'][0]
    first['end_frame'] = 48
    first['camera'] = dict(max_zoom=1.2, keyframes=[
        dict(frame=0, x=700, y=400, zoom=1),
        dict(frame=48, x=700, y=400, zoom=1.2),
    ])
    second = deepcopy(first)
    second.update(id='near-maximum', start_frame=48, end_frame=144, transition='cut',
        camera=dict(max_zoom=1.2, keyframes=[dict(frame=48, x=700, y=400, zoom=1.199)]))
    third = deepcopy(second)
    third.update(id='ending', start_frame=144, end_frame=192,
        camera=dict(max_zoom=1.2, keyframes=[dict(frame=144, x=700, y=400, zoom=1)]))
    production_plan['shots'].extend([second, third])
    report = production_preview_plan(production_plan)
    sampled_zooms = [evaluate_frame(production_plan, frame)['camera']['zoom']
        for frame in report['static_frames']]
    assert report['static_frames'] == [0, 143, 191]
    assert max(sampled_zooms) == 1.199
    assert evaluate_frame(production_plan, 47)['camera']['zoom'] < 1.199


def test_production_gate_rejects_over_budget_world_coverage_without_dropping_worlds(production_plan):
    base_world, base_shot = production_plan['worlds'][0], production_plan['shots'][0]
    production_plan['worlds'], production_plan['shots'] = [], []
    for index in range(4):
        world = deepcopy(base_world)
        world['id'] = 'world-' + str(index)
        shot = deepcopy(base_shot)
        shot.update(id='shot-' + str(index), world_id=world['id'],
            start_frame=index * 48, end_frame=(index + 1) * 48)
        shot['camera']['keyframes'][0]['frame'] = shot['start_frame']
        production_plan['worlds'].append(world)
        production_plan['shots'].append(shot)
    frozen = freeze_plan(production_plan)
    with pytest.raises(ServiceError, match='preview_coverage_budget_exceeded') as caught:
        production_preview_plan(frozen)
    assert caught.value.status == 409
    assert caught.value.details == dict(required_frames=[0, 48, 96, 144, 191],
        world_ids=['world-0', 'world-1', 'world-2', 'world-3'], max_preview_frames=3,
        retained_plan_sha256=frozen['plan_sha256'])


def test_production_clips_cover_fast_local_and_camera_changes_with_bounded_review_frames(production_plan):
    shot = production_plan['shots'][0]
    shot['camera'] = dict(max_zoom=1.12, keyframes=[
        dict(frame=0, x=700, y=400, zoom=1),
        dict(frame=120, x=700, y=400, zoom=1),
        dict(frame=124, x=700, y=400, zoom=1.12),
        dict(frame=192, x=700, y=400, zoom=1.12),
    ])
    shot['local_animation'] = [dict(id='leaf-fade', target_id='leaf-group', property='opacity',
        keyframes=[dict(frame=0, value=1), dict(frame=60, value=1),
            dict(frame=64, value=.2), dict(frame=192, value=.2)])]
    report = production_preview_plan(production_plan)
    assert len(report['clips']) == 2
    local, camera = report['clips']
    assert local['start_frame'] <= 60 < 64 < local['end_frame']
    assert camera['start_frame'] <= 120 < 124 < camera['end_frame']
    assert local['reason'] == 'local_animation_change'
    assert camera['reason'] == 'camera_change'
    assert all(0 <= clip['start_frame'] < clip['end_frame'] <= 192
        and clip['end_frame'] - clip['start_frame'] <= 48 for clip in report['clips'])
    expected = set(report['static_frames']) | {frame for clip in report['clips']
        for frame in (clip['start_frame'], clip['end_frame'] - 1)}
    assert {frame['frame'] for frame in report['layout_report']['frames']} == expected
    assert len(expected) <= 7


def test_production_clips_prioritize_world_change_boundary_and_preserve_every_world_sample(production_plan):
    production_plan['duration_frames'] = 288
    first = production_plan['shots'][0]
    first['end_frame'] = 96
    first['camera'] = dict(max_zoom=1.12, keyframes=[
        dict(frame=0, x=700, y=400, zoom=1),
        dict(frame=96, x=700, y=400, zoom=1.12),
    ])
    second = deepcopy(first)
    second.update(id='same-world', start_frame=96, end_frame=192, transition='continuous',
        camera=dict(max_zoom=1.12, keyframes=[dict(frame=96, x=700, y=400, zoom=1.12)]))
    world = deepcopy(production_plan['worlds'][0])
    world['id'] = 'second-world'
    third = deepcopy(second)
    third.update(id='new-world', world_id=world['id'], start_frame=192, end_frame=288,
        transition='cut', camera=dict(max_zoom=1.12,
            keyframes=[dict(frame=192, x=700, y=400, zoom=1.12)]))
    production_plan['worlds'].append(world)
    production_plan['shots'].extend([second, third])
    report = production_preview_plan(production_plan)
    assert report['static_frames'] == [0, 192, 287]
    assert len(report['clips']) == 2
    assert any(clip['start_frame'] <= 48 < clip['end_frame'] for clip in report['clips'])
    assert any(clip['start_frame'] < 192 < clip['end_frame']
        and 'shot_boundary' in clip['reason'] for clip in report['clips'])


def test_production_gate_allows_stillness_and_explicit_transparency_without_changing_them(production_plan):
    production_plan['duration_frames'] = 1
    production_plan['shots'][0]['end_frame'] = 1
    production_plan['worlds'][0]['layers'][0]['opacity'] = .35
    original = deepcopy(production_plan)
    report = production_preview_plan(production_plan)
    assert report['static_frames'] == [0]
    assert report['clips'] == [dict(start_frame=0, end_frame=1, reason='static_plan')]
    assert production_plan == original
    assert report['layout_report']['pixel_occlusion'] == 'not_assessed'


def test_production_clip_limit_can_be_tightened_and_boundaries_have_both_sides(production_plan):
    first = production_plan['shots'][0]
    first['end_frame'] = 1
    second = deepcopy(first)
    second.update(id='rest', start_frame=1, end_frame=192)
    second['camera']['keyframes'][0]['frame'] = 1
    production_plan['shots'].append(second)
    report = production_preview_plan(production_plan, max_static_frames=2, max_clip_frames=2)
    assert report['static_frames'] == [0, 191]
    assert report['clips'] == [dict(start_frame=0, end_frame=2, reason='shot_boundary')]


def test_production_boundary_windows_prioritize_local_resets_over_unchanged_cuts(production_plan):
    base = production_plan['shots'][0]
    production_plan['shots'] = []
    for index in range(4):
        shot = deepcopy(base)
        shot.update(id='shot-' + str(index), start_frame=index * 48, end_frame=(index + 1) * 48)
        shot['camera']['keyframes'][0]['frame'] = shot['start_frame']
        if index == 2:
            shot['local_animation'] = [dict(id='dimmed', target_id='leaf-group', property='opacity',
                keyframes=[dict(frame=96, value=.1)])]
        production_plan['shots'].append(shot)
    report = production_preview_plan(production_plan)
    # The unchanged boundary at 48 loses the bounded slots to the .9 alpha jumps.
    assert [(clip['start_frame'], clip['end_frame']) for clip in report['clips']] == [(72, 120), (120, 168)]
    assert all(clip['reason'] == 'shot_boundary' for clip in report['clips'])


@pytest.mark.parametrize('options', [
    dict(max_static_frames=False), dict(max_static_frames=0), dict(max_static_frames=4),
    dict(max_clip_frames=True), dict(max_clip_frames=1), dict(max_clip_frames=49),
])
def test_production_gate_rejects_unbounded_or_non_integer_preview_options(production_plan, options):
    with pytest.raises(ServiceError, match='preview_.*_budget_invalid'):
        production_preview_plan(production_plan, **options)


def test_production_gate_does_not_refreeze_tampered_frozen_plan(production_plan):
    frozen = freeze_plan(production_plan)
    frozen['plan']['worlds'][0]['style'] = 'changed after freeze'
    with pytest.raises(ServiceError, match='scene_space_frozen_plan_changed'):
        production_preview_plan(frozen)


def test_production_gate_does_not_issue_a_valid_report_for_invalid_geometry(production_plan):
    production_plan['worlds'][0]['objects'][0]['parent_id'] = 'missing'
    with pytest.raises(ServiceError):
        production_preview_plan(production_plan)
