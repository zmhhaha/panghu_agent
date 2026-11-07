"""Developer cases use production compilers and preserve replay identities."""
import json

import pytest

from video_workbench.animation_lab_samples import MODULES, catalog, compile_sample
from video_workbench.motion import compile_project, sha256
from video_workbench.motion_process import compile_process_project


@pytest.mark.parametrize('case_id', ['diagram-connections', 'diagram-long-label', 'process-retry'])
def test_comparisons_reuse_exact_source_audio_and_real_compiler_output(case_id):
    before = compile_sample(case_id, 'baseline')
    after = compile_sample(case_id, 'candidate')
    assert before['inputs'] == after['inputs']
    assert before['input_sha256'] == after['input_sha256']
    assert before['identity'] == after['identity']
    assert before['artifact_sha256'] != after['artifact_sha256']
    assert before['clips'][0]['files']['audio.wav'] == after['clips'][0]['files']['audio.wav']
    compiler = compile_process_project if case_id == 'process-retry' else compile_project
    for sample in [before, after]:
        expected = compiler(**sample['inputs']['inputs'], compiler_version=sample['compiler'])
        actual = sample['clips'][0]['files']
        assert {name: actual[name] for name in expected} == expected
        assert json.loads(actual['manifest.json'])['compiler'] == sample['compiler']
        assert sha256(actual['audio.wav']) == sample['clips'][0]['timeline']['audio_sha256']
        assert sample['audio_kind'] == 'synthetic_silence_not_narration'
        assert sample['timing_review'] == 'pending'
        assert sample['voice_naturalness'] == 'not_evaluated'
        assert sample['agent_calls'] == sample['tts_calls'] == 0


@pytest.mark.parametrize('mode', ['retain', 'copy'])
def test_cross_shot_cases_keep_boundary_state_and_distinguish_identity(mode):
    baseline = compile_sample('continuity-'+mode, 'baseline')
    candidate = compile_sample('continuity-'+mode, 'candidate')
    assert candidate['inputs'] == baseline['inputs']
    assert candidate['duration_seconds'] > baseline['duration_seconds']
    first, second = [json.loads(clip['files']['plan.json'])['segment'] for clip in candidate['clips']]
    assert first['final_checkpoint'] == second['initial_checkpoint']
    old = first['entity_map']['request']
    new = second['entity_map']['request']
    if mode == 'retain':
        assert old == new
        assert second['copy_lineage'] == []
        assert second['initial_states'][new]['slot'] == 'token_queue'
    else:
        assert old != new
        assert second['copy_lineage'] == [dict(source=old, target=new)]
        assert second['final_checkpoint']['objects'][old]['slot'] == 'archive_0'
    for before, after in zip(baseline['clips'], candidate['clips']):
        assert before['files']['audio.wav'] == after['files']['audio.wav']
        assert before['files']['beats.json'] == after['files']['beats.json']
        assert before['files']['alignment.json'] == after['files']['alignment.json']


def test_catalog_contains_genuine_engines_and_replayable_variants():
    cases = catalog()
    assert len(cases) == 27
    assert {row['module'] for row in cases} == {'diagram', 'process', 'continuity', 'canvas', 'three',
                                               'charts', 'algorithms', 'interface'}
    assert all(row['status'] == 'available' for row in MODULES)
    json.dumps(cases)
    for case in cases:
        assert len(case['identity']) == len(case['input_sha256']) == 64
        for variant in case['variants']:
            sample = compile_sample(case['id'], variant['id'])
            assert sample['identity'] == case['identity']
            assert variant['artifact_sha256'] == sample['artifact_sha256']
            for clip in sample['clips']:
                assert all(0 <= point['time'] < clip['duration_seconds'] for point in clip['checkpoints'])
                manifest = json.loads(clip['files']['manifest.json'])
                assert all(sha256(clip['files'][name]) == checksum
                           for name, checksum in manifest['files'].items())


def test_returned_data_cannot_mutate_a_later_annotation_replay():
    before = compile_sample('process-retry', 'candidate')
    expected = before['identity'], before['clips'][0]['files']['index.html']
    before['inputs']['inputs']['plan']['title'] = 'caller mutation'
    before['clips'][0]['files']['index.html'] = b'caller mutation'
    before['identity'] = 'caller mutation'
    after = compile_sample('process-retry', 'candidate')
    assert (after['identity'], after['clips'][0]['files']['index.html']) == expected
    cases = catalog()
    cases[0]['variants'].clear()
    assert len(catalog()[0]['variants']) == 2


@pytest.mark.parametrize('case_id,variant_id', [('canvas', 'candidate'), ('../process-retry', 'candidate'),
                                             ('process-retry', '../index.html'), ([], 'candidate')])
def test_only_registered_cases_and_variants_can_be_compiled(case_id, variant_id):
    with pytest.raises(ValueError, match='unknown_animation_lab_'):
        compile_sample(case_id, variant_id)
