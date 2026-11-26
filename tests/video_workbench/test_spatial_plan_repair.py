"""Identity-preserving structure changes reuse the actual revision safeguards."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.scene_space_plan import automatic_plan, default_budget, freeze_plan, validate_plan
from video_workbench.spatial_plan_repair import PLAN_REPAIR_REVISION, validate_same_content_plan_repair


FIXTURE = Path(__file__).resolve().parents[1]/'fixtures/scene_space/world.json'


@pytest.fixture
def plan():
    world = json.loads(FIXTURE.read_text(encoding='utf-8'))
    return automatic_plan([world], id='same-content-plan', viewport=dict(width=1280, height=720),
        duration_frames=48, intents=[dict(world_id=world['id'], weight=1, view='hold', focus=dict(x=700, y=400))],
        budget=default_budget(model_calls=4, model_tokens=10000, revisions=1))


def revised(plan, *, world=False):
    replacement = deepcopy(plan)
    replacement['revision'] += 1
    replacement['budget']['revisions']['used'] += 1
    if world:
        replacement['worlds'][0]['revision'] += 1
        for shot in replacement['shots']:
            if shot['world_id'] == replacement['worlds'][0]['id']:
                shot['world_revision'] = replacement['worlds'][0]['revision']
    return replacement


def test_source_only_plan_remains_unchanged_without_consuming_a_plan_revision(plan):
    original = deepcopy(plan)
    frozen = freeze_plan(plan)
    result = validate_same_content_plan_repair(frozen, deepcopy(plan), 'Correct brush source only')
    assert result == dict(plan=validate_plan(plan), change=None, frozen=frozen)
    assert plan == original
    assert result['plan']['budget']['revisions']['used'] == 0
    assert PLAN_REPAIR_REVISION == 1
    result['plan']['worlds'][0]['seed'] = 999
    assert frozen['plan']['worlds'][0]['seed'] == 31


def test_layout_bounds_depth_layers_and_relations_can_be_repaired_with_lineage(plan):
    replacement = revised(plan, world=True)
    world = replacement['worlds'][0]
    main = next(obj for obj in world['objects'] if obj['id'] == 'main-peak')
    main['transform']['x'] += 8
    main['bounds']['width'] -= 20
    main['depth']['value'] = 1.2
    world['layers'][0]['parallax'] = .25
    world['layout']['points'][0]['position'][0] += 10
    world['relations'][0]['tolerance'] = .02
    before, proposed = deepcopy(plan), deepcopy(replacement)
    result = validate_same_content_plan_repair(plan, replacement, reason='Correct peak proportions and layer spacing')
    report = result['change']
    assert report['from_sha256'] == digest(plan)
    assert report['to_sha256'] == digest(result['plan'])
    assert report['changed_world_ids'] == ['river-world']
    assert report['changed_object_ids'] == ['river-world/main-peak']
    assert report['changed_layer_ids'] == ['river-world/far']
    assert report['affected_shot_ids'] == ['shot-1']
    assert report['invalidated_cache_ids'] == ['river-world/far', 'river-world/middle', 'river-world/near']
    assert {'spatial', 'coverage', 'deterministic_frames', 'visual_preview'} == set(report['required_rechecks'])
    assert report['budget_history_preserved'] is True
    assert report['visual_acceptance'] is False
    assert result['frozen']['plan_sha256'] == report['to_sha256']
    assert plan == before and replacement == proposed


def test_camera_plan_can_change_without_replacing_worlds(plan):
    replacement = revised(plan)
    camera = replacement['shots'][0]['camera']
    camera['max_zoom'] = 1.05
    camera['keyframes'].append(dict(frame=48, x=710, y=400, zoom=1.05, easing='smoothstep'))
    result = validate_same_content_plan_repair(freeze_plan(plan), replacement, 'Reveal the peak with a short push')
    assert result['change']['changed_world_ids'] == []
    assert result['change']['reusable_world_ids'] == ['river-world']
    assert result['change']['affected_shot_ids'] == ['shot-1']
    assert len(result['change']['invalidated_cache_ids']) == 3
    assert result['plan']['shots'][0]['camera']['keyframes'][-1]['zoom'] == 1.05


@pytest.mark.parametrize('key,value,code', [
    ('seed', 32, 'seed_changed'), ('style', 'neon-city', 'style_changed'),
])
def test_seed_and_style_identity_cannot_be_changed(plan, key, value, code):
    replacement = revised(plan, world=True)
    replacement['worlds'][0][key] = value
    with pytest.raises(ServiceError, match=code):
        validate_same_content_plan_repair(plan, replacement, 'Try a different subject')


def test_world_collection_cannot_be_expanded_or_replaced(plan):
    replacement = revised(plan)
    extra = deepcopy(replacement['worlds'][0]);extra['id'] = 'different-world'
    replacement['worlds'].append(extra)
    replacement['shots'][0]['end_frame'] = 24
    second = deepcopy(replacement['shots'][0])
    second.update(id='shot-2', world_id=extra['id'], start_frame=24, end_frame=48)
    second['camera']['keyframes'][0]['frame'] = 24
    replacement['shots'].append(second)
    # This is otherwise a valid general revision; same-content repairs narrow it.
    validate_plan(replacement)
    with pytest.raises(ServiceError, match='worlds_changed'):
        validate_same_content_plan_repair(plan, replacement, 'Add a second scene')
    replacement = revised(plan)
    replacement['worlds'] = [extra]
    replacement['shots'][0]['world_id'] = extra['id']
    with pytest.raises(ServiceError, match='worlds_changed'):
        validate_same_content_plan_repair(plan, replacement, 'Replace the scene')


@pytest.mark.parametrize('operation', ['add', 'remove', 'kind'])
def test_object_id_and_kind_collection_cannot_be_changed(plan, operation):
    replacement = revised(plan, world=True)
    world = replacement['worlds'][0]
    if operation == 'add':
        extra = deepcopy(world['objects'][0]);extra['id'] = 'new-mountain'
        world['objects'].append(extra)
    elif operation == 'remove':
        world['objects'] = [obj for obj in world['objects'] if obj['id'] != 'leaf-group']
        world['relations'] = [row for row in world['relations'] if row['id'] != 'leaf-on-tree']
    else:
        next(obj for obj in world['objects'] if obj['id'] == 'main-peak')['kind'] = 'building'
    validate_plan(replacement)
    with pytest.raises(ServiceError, match='objects_changed'):
        validate_same_content_plan_repair(plan, replacement, 'Swap visual content')


@pytest.mark.parametrize('field', ['viewport', 'duration_frames', 'fps'])
def test_output_clock_and_viewport_cannot_be_reset(plan, field):
    replacement = revised(plan)
    if field == 'viewport':
        replacement['viewport']['width'] = 1200
    elif field == 'duration_frames':
        replacement['duration_frames'] = 24
        replacement['shots'][0]['end_frame'] = 24
    else:
        replacement['fps'] = 12
    with pytest.raises(ServiceError, match='clock_changed|scene_space_plan_invalid'):
        validate_same_content_plan_repair(plan, replacement, 'Change output timing')


@pytest.mark.parametrize('section,key,old,new', [
    ('model', 'used_calls', 2, 1), ('model', 'used_tokens', 800, 100),
    ('model', 'used_tokens', None, 0), ('render', 'used_seconds', 10, 0),
])
def test_existing_cost_or_unknown_usage_cannot_be_reset(plan, section, key, old, new):
    plan['budget'][section][key] = old
    replacement = revised(plan)
    replacement['budget'][section][key] = new
    with pytest.raises(ServiceError, match='budget_history_reset'):
        validate_same_content_plan_repair(plan, replacement, 'Try to reset usage')


def test_usage_may_increase_but_caps_and_revision_budget_cannot_be_reset(plan):
    plan['budget']['model']['used_calls'] = 1
    plan['budget']['model']['used_tokens'] = 500
    replacement = revised(plan)
    replacement['budget']['model'].update(used_calls=2, used_tokens=900)
    result = validate_same_content_plan_repair(plan, replacement, 'Repair after measured work')
    assert result['plan']['budget']['model']['used_tokens'] == 900
    larger = deepcopy(replacement);larger['budget']['model']['limit_tokens'] += 1
    with pytest.raises(ServiceError, match='budget_cap_changed'):
        validate_same_content_plan_repair(plan, larger, 'Increase quota')
    reset = deepcopy(replacement);reset['budget']['revisions']['used'] = 0
    with pytest.raises(ServiceError, match='revision_budget_reset'):
        validate_same_content_plan_repair(plan, reset, 'Free extra revision')
    with pytest.raises(ServiceError):
        validate_same_content_plan_repair(result['plan'], revised(result['plan']), 'Exceed the one revision cap')


def test_repair_cannot_relabel_source_evidence_as_observed(plan):
    replacement = revised(plan)
    replacement['provenance']['evidence_scope'] = 'static_image'
    with pytest.raises(ServiceError, match='source_evidence_changed'):
        validate_same_content_plan_repair(plan, replacement, 'Claim more evidence')


def test_geometry_and_all_frame_camera_coverage_are_validated_before_acceptance(plan):
    replacement = revised(plan, world=True)
    next(obj for obj in replacement['worlds'][0]['objects'] if obj['id'] == 'near-tree')['transform']['x'] += 40
    with pytest.raises(ServiceError, match='attachment'):
        validate_same_content_plan_repair(plan, replacement, 'Move a tree without its support')
    replacement = revised(plan)
    replacement['shots'][0]['camera']['keyframes'].append(dict(frame=48, x=5000, y=400, zoom=1))
    with pytest.raises(ServiceError, match='camera_outside_artwork'):
        validate_same_content_plan_repair(plan, replacement, 'Expose unavailable artwork')


def test_tampered_frozen_original_is_not_accepted_for_source_only_repair(plan):
    frozen = freeze_plan(plan)
    frozen['plan']['worlds'][0]['seed'] += 1
    with pytest.raises(ServiceError, match='frozen_plan_changed'):
        validate_same_content_plan_repair(frozen, plan, 'Use edited frozen input')


@pytest.mark.parametrize('reason', ['', '   ', None, 'bad\x00reason', 'x'*4001, ' '*4000+'x'])
def test_audit_reason_is_required_even_for_source_only_corrections(plan, reason):
    with pytest.raises(ServiceError, match='reason_invalid'):
        validate_same_content_plan_repair(plan, plan, reason)
