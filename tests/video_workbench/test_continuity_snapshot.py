"""Durable v3 freezing, exact old sources, and independently forged boundaries."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.continuity import CONTINUITY_COMPILER_VERSION, build_continuity_plan
from video_workbench.continuity_snapshot import (freeze_continuity_snapshot,
    verify_continuity_snapshot, verify_motion_snapshot)
from tests.video_workbench.test_motion_snapshot import freeze, motion_scene


def source_snapshot(*, compiler_version=None):
    first, second = motion_scene(), motion_scene()
    second['id'] = 'scene-second'
    second['motion']['id'] = 'motion-second'
    second['audio']['id'] = 'audio-second'
    if compiler_version is not None:
        for scene in (first, second):
            scene['motion']['compiler_version'] = compiler_version
    return freeze([first, second])


def continuous(source=None, *, compiler_version=CONTINUITY_COMPILER_VERSION):
    source = source_snapshot() if source is None else source
    return freeze_continuity_snapshot(source, dict(id='continuous-version',
        plan=build_continuity_plan(source['scenes'])), compiler_version=compiler_version)


def test_historical_compiler1_snapshot_rebuild_keeps_its_pre_fix_identity():
    # Frozen before compiler2 was introduced. The original failed CLI attempt
    # remains inspectable; changing publication order must not rewrite it.
    frozen = continuous(source_snapshot(compiler_version='motion-compiler-5'),
                        compiler_version='motion-continuity-compiler-1')
    assert frozen['sha256'] == 'ca594d9ade70731e47e495106308f7f7287893933145e394ae926e8797cc61b6'
    assert digest(frozen['continuity']['resolution']) == 'fb4f863720941699f7421f918980e53f3fdbc09e797e2504ca3f48f68725f0e2'
    assert [s['continuity_project_files_sha256']['index.html'] for s in frozen['scenes']] == [
        'ccc994c39b8accde451ffab5c500eca12e0d6ac115432b59dae0734bdbcf4de5',
        'a4c93ec902d1748ab380cbde824560ca2927b8f423952379dfbb098fd8684537']
    assert verify_motion_snapshot(json.loads(json.dumps(frozen))) == frozen


def test_new_snapshot_uses_fixed_compiler_without_rewriting_legacy_media():
    source = source_snapshot()
    old = continuous(source, compiler_version='motion-continuity-compiler-1')
    new = continuous(source)
    assert new['continuity']['resolution']['compiler'] == 'motion-continuity-compiler-2'
    assert new['sha256'] != old['sha256']
    assert verify_continuity_snapshot(json.loads(json.dumps(new))) == new
    for previous, current in zip(old['scenes'], new['scenes']):
        assert previous['audio'] == current['audio']
        assert previous['motion'] == current['motion']
        assert previous['timing'] == current['timing']
        assert previous['continuity_project_files_sha256']['index.html'] != current['continuity_project_files_sha256']['index.html']


@pytest.mark.parametrize('compiler', ['unknown-compiler', 'motion-compiler-5', None, []])
def test_unknown_continuity_compiler_cannot_be_resigned_into_a_valid_snapshot(compiler):
    frozen = continuous()
    frozen['continuity']['resolution']['compiler'] = compiler
    frozen['sha256'] = digest({key: value for key, value in frozen.items() if key != 'sha256'})
    with pytest.raises(ServiceError, match='continuity_snapshot_corrupt'):
        verify_continuity_snapshot(frozen)


def test_frozen_resolution_cannot_silently_mix_compiler1_and_compiler2():
    frozen = continuous()
    frozen['scenes'][0]['continuity']['compiler'] = 'motion-continuity-compiler-1'
    frozen['sha256'] = digest({key: value for key, value in frozen.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        verify_continuity_snapshot(frozen)


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
