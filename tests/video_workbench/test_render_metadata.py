"""Frozen creative provenance stays readable beside shared motion heads."""
from copy import deepcopy

import pytest

from agent_service.contracts import canonical_bytes
from video_workbench.creative_snapshot import freeze_snapshot_v4
from video_workbench.render_metadata import render_metadata
from test_creative_production import source_scene
from test_motion_snapshot import RUNTIME


def inputs():
    source = source_scene()
    snapshot = freeze_snapshot_v4('project', 3, '创意动画', [source], runtime_fingerprint=RUNTIME)
    identity, audio = source['creative']['id'], source['audio']['id']
    project = dict(scenes=[dict(id=source['id'], narration=source['narration'], duration_seconds=99,
        leading_seconds=0, trailing_seconds=0, head_version_id='unrelated-static-version',
        versions=[dict(id='unrelated-static-version')], head_audio_version_id=audio,
        audio_versions=[dict(id=audio, stale=False, number=2)],
        motion=dict(mode='motion', head_version_id=identity,
                    versions=[dict(id=identity, stale=False, diagram_kind='creative')]))])
    return snapshot, project


@pytest.mark.parametrize('purpose', ['draft', 'delivery'])
def test_creative_provenance_uses_its_frozen_version_and_current_motion_head(purpose):
    snapshot, project = inputs()
    snapshot['purpose'] = purpose
    before = canonical_bytes(snapshot)
    result = render_metadata(snapshot, project)
    assert result['content_state'] == 'current'
    assert result['scenes'][0] == dict(scene_id='creative-scene', title='真实旁白动画',
        visual_mode='creative', visual_version_id='creative-candidate',
        audio_version_id='audio-candidate', visual_version_number=1, audio_version_number=2)
    assert canonical_bytes(snapshot) == before


@pytest.mark.parametrize('purpose,expected', [('draft', 'current'), ('delivery', 'changed')])
def test_changing_an_adopted_head_changes_delivery_without_relabeling_candidate_drafts(purpose, expected):
    snapshot, project = inputs()
    snapshot['purpose'] = purpose
    project['scenes'][0]['motion']['head_version_id'] = 'another-creative-candidate'
    project['scenes'][0]['motion']['versions'].append(dict(id='another-creative-candidate', stale=False))
    result = render_metadata(snapshot, project)
    assert result['content_state'] == expected
    assert result['scenes'][0]['visual_version_id'] == 'creative-candidate'


@pytest.mark.parametrize('change', ['audio-head', 'audio-stale', 'visual-stale', 'withdraw-motion', 'missing-scene'])
def test_creative_delivery_detects_changed_or_unavailable_adopted_resources(change):
    snapshot, project = inputs()
    snapshot['purpose'] = 'delivery'
    scene = project['scenes'][0]
    if change == 'audio-head':
        scene['head_audio_version_id'] = 'replacement-audio'
    elif change == 'audio-stale':
        scene['audio_versions'][0]['stale'] = True
    elif change == 'visual-stale':
        scene['motion']['versions'][0]['stale'] = True
    elif change == 'withdraw-motion':
        scene['motion']['mode'] = 'static'
    else:
        project['scenes'] = []
    assert render_metadata(snapshot, project)['content_state'] == 'changed'


def test_confirming_the_authored_alignment_does_not_change_pixels_or_frozen_history():
    snapshot, project = inputs()
    before = canonical_bytes(snapshot)
    original = snapshot['scenes'][0]['creative']['alignment']
    reviewed = dict(deepcopy(original), review_status='verified', reviewed_by='owner',
                    reviewed_at='2026-11-08T12:00:00+08:00')
    alignments = {('creative-candidate', 'audio-candidate'): reviewed}
    assert render_metadata(snapshot, project, alignments)['content_state'] == 'current'
    assert canonical_bytes(snapshot) == before
    reviewed['segments'][0]['end'] -= .01
    assert render_metadata(snapshot, project, alignments)['content_state'] == 'changed'
    assert canonical_bytes(snapshot) == before


def test_last_frame_hold_is_compared_to_effective_creative_timing():
    snapshot, project = inputs()
    snapshot['scenes'][0]['timing']['trailing_seconds'] = 4
    assert render_metadata(snapshot, project, end_hold_seconds=4)['content_state'] == 'current'
    project['scenes'][0]['trailing_seconds'] = 5
    assert render_metadata(snapshot, project, end_hold_seconds=4)['content_state'] == 'changed'
