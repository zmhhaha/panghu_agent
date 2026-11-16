"""An experiment must not pass on missing, stale or invented references."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.alignment_benchmark import prepare_benchmark, evaluate_benchmark
from video_workbench.motion import make_beats
from video_workbench.word_alignment import build_word_alignment


def fixture():
    beats = make_beats('甲乙丙丁戊己庚辛壬癸。')
    items = [dict(text=c, start_time=i * .4, end_time=i * .4 + .2)
             for i, c in enumerate(beats['spoken_text'][:-1])]
    alignment = build_word_alignment(beats, 'a' * 64, 5, items,
        dict(kind='forced_alignment', provider='test-fixture', model='synthetic', version='1'))
    baseline = prepare_benchmark(beats, 'a' * 64, 5, [[i, i + 1] for i in range(10)])
    return baseline, alignment


def reviewed():
    baseline, alignment = fixture()
    for i, row in enumerate(baseline['references']):
        row.update(reference_start=i * .4 + .02, uncertainty_seconds=.01,
                   reviewed_by='unit-test-fixture-only')
    return baseline, alignment


def test_unreviewed_references_cannot_pass_or_grant_approval():
    baseline, alignment = fixture()
    result = evaluate_benchmark(baseline, alignment)
    assert result['status'] == 'reference_pending'
    assert result['measured_references'] == 0
    assert result['median_error_seconds'] is None
    assert not result['grants_human_timing_approval']
    assert all(r['reference_start'] is None for r in baseline['references'])


def test_measures_errors_and_fails_an_outlier_without_changing_inputs():
    baseline, alignment = reviewed()
    original = deepcopy(baseline)
    result = evaluate_benchmark(baseline, alignment)
    assert result['status'] == 'passed'
    assert result['median_error_seconds'] == pytest.approx(.02)
    assert baseline == original
    baseline['references'][-1]['reference_start'] = 4.5
    assert evaluate_benchmark(baseline, alignment)['status'] == 'failed'


@pytest.mark.parametrize('mutation', [
    lambda b: b.update(audio_sha256='b' * 64),
    lambda b: b.update(audio_seconds=6),
    lambda b: b['references'][0].update(text='错误'),
    lambda b: b['references'][0].update(reference_start=True),
    lambda b: b['references'][0].update(reference_start=float('nan')),
    lambda b: b['references'][0].update(uncertainty_seconds=.2),
    lambda b: b['references'][0].update(reviewed_by=''),
    lambda b: b['references'][0].update(extra=True),
    lambda b: b.update(references=b['references'][:9]),
    lambda b: b['gates'].update(minimum_references=True),
])
def test_bad_references_and_stale_audio_are_rejected(mutation):
    baseline, alignment = reviewed()
    mutation(baseline)
    with pytest.raises(ServiceError):
        evaluate_benchmark(baseline, alignment)


def test_partial_unit_is_reported_and_never_interpolated():
    baseline, _ = reviewed()
    beats = baseline['beats']
    alignment = build_word_alignment(beats, 'a' * 64, 5,
        [dict(text=beats['spoken_text'][:-1], start_time=.1, end_time=4)],
        dict(kind='provider_native', provider='test-fixture', model='synthetic', version='1'))
    result = evaluate_benchmark(baseline, alignment)
    assert result['status'] == 'unit_boundary_unsupported'
    assert result['measured_references'] == 0
    assert result['source']['kind'] == 'provider_native'


def test_duplicate_spans_and_small_baselines_are_not_accepted():
    baseline, _ = fixture()
    for spans in ([[0, 1]] * 10, [[i, i + 1] for i in range(9)],
                  [[0, i + 1] for i in range(10)],
                  [[i, i + 2] for i in range(10)]):
        with pytest.raises(ServiceError):
            prepare_benchmark(baseline['beats'], 'a' * 64, 5, spans)
