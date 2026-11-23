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
    frame_layout_svg, inspect_world, layout_svg, preview_frames,
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
