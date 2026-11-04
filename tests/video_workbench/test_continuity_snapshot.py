"""Durable v3 freezing, exact old sources, and independently forged boundaries."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.continuity import build_continuity_plan
from video_workbench.continuity_snapshot import (freeze_continuity_snapshot,
    verify_continuity_snapshot, verify_motion_snapshot)
from tests.video_workbench.test_motion_snapshot import freeze, motion_scene


def source_snapshot():
    first, second = motion_scene(), motion_scene()
    second['id'] = 'scene-second'
    second['motion']['id'] = 'motion-second'
    second['audio']['id'] = 'audio-second'
    return freeze([first, second])


def continuous(source=None):
    source = source_snapshot() if source is None else source
    return freeze_continuity_snapshot(source, dict(id='continuous-version',
        plan=build_continuity_plan(source['scenes'])))


def test_v3_json_persistence_keeps_original_recordings_and_legacy_inputs():
    source = source_snapshot()
    original = deepcopy(source)
    frozen = continuous(source)
    durable = json.loads(json.dumps(frozen))
    assert durable == frozen
    assert verify_motion_snapshot(durable) == frozen
    assert source == original
    assert verify_motion_snapshot(source) == source
    assert frozen['source_snapshot_sha256'] == source['sha256']
    for old, new in zip(source['scenes'], frozen['scenes']):
        assert old['audio'] == new['audio']
        assert old['motion'] == new['motion']
        assert old['timing'] == new['continuity_base_timing']
        assert new['timing']['leading_seconds'] == new['continuity']['timeline']['leading_seconds']
        assert new['timing']['trailing_seconds'] == new['continuity']['timeline']['trailing_seconds']
    assert frozen['scenes'][0]['continuity']['final_checkpoint'] == frozen['scenes'][1]['continuity']['initial_checkpoint']


@pytest.mark.parametrize('target', ['checkpoint', 'source', 'compiled_files', 'audio', 'timing'])
def test_resigning_outer_digest_cannot_forge_continuity_or_legacy_source(target):
    frozen = continuous()
    scene = frozen['scenes'][1]
    if target == 'checkpoint':
        objects = scene['continuity']['initial_checkpoint']['objects']
        next(iter(objects.values()))['x'] += 17
        segment = scene['continuity']
        segment['sha256'] = digest({key: value for key, value in segment.items() if key != 'sha256'})
    elif target == 'source':
        scene['motion']['plan']['title'] = 'Forged source'
    elif target == 'compiled_files':
        scene['continuity_project_files_sha256']['index.html'] = '0' * 64
    elif target == 'audio':
        scene['audio']['data_base64'] = frozen['scenes'][0]['audio']['data_base64'][:-4] + 'AAAA'
    else:
        scene['timing']['leading_seconds'] += .1
    frozen['sha256'] = digest({key: value for key, value in frozen.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        verify_continuity_snapshot(frozen)


def test_exact_segment_budget_includes_continuity_leading_pause():
    source = source_snapshot()
    source['subtitles'] = True
    source['sha256'] = digest({key: value for key, value in source.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        continuous(source)
