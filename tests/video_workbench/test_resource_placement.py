"""Placement custody tests; no Manim, model or actual rendering is claimed."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.resource_placement import freeze_manim_placement, validate_manim_placement


def resource(start=0):
    value = dict(schema='video.manim-method-resource.v1',
        scope=dict(owner='owner', effect_id='example', experiment_id='original', operation_id='resource-task'),
        envelope_sha256='a' * 64, image_digest='sha256:' + 'b' * 64,
        manifest=dict(schema='video.manim-frame-resource.v1', global_start_frame=start,
            frame_count=48, fps=24, source_sha256='c' * 64))
    value['sha256'] = digest(value)
    return value


def test_same_resource_can_have_distinct_frozen_placements_without_changing_content():
    original = resource()
    before = deepcopy(original)
    first = freeze_manim_placement(original, start_frame=24, project_frame_count=240)
    later = freeze_manim_placement(original, start_frame=144, project_frame_count=240)
    assert first['resource_sha256'] == later['resource_sha256'] == original['sha256']
    assert first['resource_start_frame'] == later['resource_start_frame'] == 0
    assert first['sha256'] != later['sha256']
    assert original == before
    assert validate_manim_placement(first, original, project_frame_count=240) == first
    assert validate_manim_placement(later, original, project_frame_count=240) == later


def test_original_manifest_coordinates_do_not_become_new_global_placement():
    original = resource(start=72)
    placed = freeze_manim_placement(original, start_frame=0, project_frame_count=48)
    assert placed['resource_start_frame'] == 72 and placed['global_start_frame'] == 0
    assert original['manifest']['global_start_frame'] == 72
    assert validate_manim_placement(placed, original, project_frame_count=48) == placed


@pytest.mark.parametrize('start,total', [(-1, 240), (.5, 240), (True, 240), (193, 240),
    (0, 47), (0, 0), (0, True), (0, 14401), (float('inf'), 240)])
def test_placement_cannot_escape_project_bounds_or_change_frame_units(start, total):
    with pytest.raises(ServiceError, match='outside_project'):
        freeze_manim_placement(resource(), start_frame=start, project_frame_count=total)


def test_changed_or_foreign_resource_cannot_reuse_an_old_placement():
    original = resource()
    placed = freeze_manim_placement(original, start_frame=0, project_frame_count=240)
    for field, changed in [('scope', dict(owner='another')), ('envelope_sha256', 'f' * 64),
            ('manifest', dict(original['manifest'], source_sha256='d' * 64))]:
        other = dict(original, **{field: changed})
        other['sha256'] = digest({key: item for key, item in other.items() if key != 'sha256'})
        with pytest.raises(ServiceError, match='resource_changed'):
            validate_manim_placement(placed, other, project_frame_count=240)


def test_rehashed_tampering_does_not_change_resource_clock_or_scope():
    original = resource()
    placed = freeze_manim_placement(original, start_frame=24, project_frame_count=240)
    for field, changed in [('resource_sha256', 'f' * 64), ('resource_start_frame', 1),
            ('frame_count', 47), ('fps', 30)]:
        forged = dict(placed, **{field: changed})
        forged['sha256'] = digest({key: item for key, item in forged.items() if key != 'sha256'})
        with pytest.raises(ServiceError, match='resource_changed'):
            validate_manim_placement(forged, original, project_frame_count=240)
    with pytest.raises(ServiceError, match='binding_invalid'):
        validate_manim_placement(dict(placed, global_start_frame=48), original, project_frame_count=240)
    with pytest.raises(ServiceError, match='binding_invalid'):
        validate_manim_placement(dict(placed, undeclared='value'), original, project_frame_count=240)


def test_invalid_descriptor_and_non_integer_manifest_coordinates_are_rejected():
    original = resource()
    with pytest.raises(ServiceError, match='descriptor_invalid'):
        freeze_manim_placement(dict(original, sha256='f' * 64), start_frame=0, project_frame_count=240)
    for changes in [dict(global_start_frame=True), dict(global_start_frame=-1),
            dict(frame_count=True), dict(frame_count=241), dict(fps=30), dict(schema='another')]:
        other = dict(original, manifest=dict(original['manifest'], **changes))
        other['sha256'] = digest({key: item for key, item in other.items() if key != 'sha256'})
        with pytest.raises(ServiceError, match='manifest_invalid'):
            freeze_manim_placement(other, start_frame=0, project_frame_count=240)
