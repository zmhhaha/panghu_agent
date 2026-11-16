"""Actual timing stays exact through text mapping and sentence projection."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.motion import make_beats, text_sha256
from video_workbench.word_alignment import (
    MAX_ALIGNMENT_BYTES, MAX_BACKEND_ITEMS, MAX_UNITS, SCHEMA, beat_segments,
    build_word_alignment, resolve_word_anchor, validate_word_alignment,
)


SOURCE = dict(kind='forced_alignment', provider='local', model='aligner', version='revision-1')


def item(text, start, end):
    return dict(text=text, start_time=start, end_time=end)


def fixture():
    beats = make_beats('SDK 接收请求。请求再次进入队列。', 'S D K 接收请求。请求再次进入队列。')
    items = [item('S', .2, .3), item('D', .4, .5), item('K', .6, .7),
             item('接收', .8, 1.2), item('请求', 1.3, 1.8), item('。', 1.8, 1.8),
             item('请求', 2.4, 2.9), item('再次', 3, 3.3), item('进入', 3.4, 3.8),
             item('队列', 4, 4.6)]
    return beats, items, build_word_alignment(beats, 'a' * 64, 5, items, SOURCE)


def test_real_times_and_complete_mapping_are_preserved_without_interpolation():
    beats, _, document = fixture()
    assert document['schema'] == SCHEMA
    assert document['audio_sha256'] == 'a' * 64
    assert document['audio_seconds'] == 5
    assert document['spoken_sha256'] == text_sha256(beats['spoken_text'])
    assert document['beats_sha256'] == digest(beats)
    assert document['source'] == SOURCE
    assert document['review_status'] == 'pending'
    assert document['reviewed_by'] is document['reviewed_at'] is None
    request = [row for row in document['units'] if row['text'] == '请求']
    assert len(request) == 2
    assert request[0]['spoken_start'] != request[1]['spoken_start']
    assert [(row['start'], row['end']) for row in request] == [(1.3, 1.8), (2.4, 2.9)]
    assert all(row['text'] == beats['spoken_text'][row['spoken_start']:row['spoken_end']]
               for row in document['units'])
    assert len(document['units']) == 9  # One two-character word remains one backend unit.


def test_sentence_projection_keeps_speech_gaps_and_native_provenance():
    beats, items, _ = fixture()
    native = dict(SOURCE, kind='provider_native', provider='speech-api')
    document = build_word_alignment(beats, 'a' * 64, 5, items, native)
    assert beat_segments(document, beats) == [
        dict(beat_id=beats['segments'][0]['id'], start=.2, end=1.8),
        dict(beat_id=beats['segments'][1]['id'], start=2.4, end=4.6),
    ]
    assert document['source'] == native
    assert 'method' not in document


def test_word_anchor_selects_the_exact_repeated_occurrence():
    beats, _, document = fixture()
    first, second = [row for row in document['units'] if row['text'] == '请求']
    result = resolve_word_anchor(document, beats, second['spoken_start'], second['spoken_end'])
    assert result == dict(second)
    assert result['start'] != first['start']
    # Include sentence punctuation without adding a made-up punctuation time.
    result = resolve_word_anchor(document, beats, first['spoken_start'], first['spoken_end'] + 1)
    assert result['text'] == '请求。' and result['start'] == 1.3 and result['end'] == 1.8


def test_an_anchor_cannot_split_a_multi_character_backend_unit():
    beats, _, document = fixture()
    row = next(row for row in document['units'] if row['text'] == '接收')
    with pytest.raises(ServiceError, match='word_anchor_unit_boundary_unsupported'):
        resolve_word_anchor(document, beats, row['spoken_start'], row['spoken_start'] + 1)
    with pytest.raises(ServiceError, match='word_anchor_unit_boundary_unsupported'):
        resolve_word_anchor(document, beats, row['spoken_start'] + 1, row['spoken_end'])


def test_a_backend_unit_crossing_sentences_cannot_be_evenly_split():
    beats = make_beats('请求。返回。')
    document = build_word_alignment(beats, 'b' * 64, 2, [item('请求返回', .1, 1.8)], SOURCE)
    assert document['units'] == [dict(spoken_start=0, spoken_end=5, text='请求。返回', start=.1, end=1.8)]
    with pytest.raises(ServiceError, match='word_anchor_unit_boundary_unsupported'):
        beat_segments(document, beats)


def test_matching_ignores_only_punctuation_and_whitespace():
    beats = make_beats('  GPU：S D K，处理+返回。\n')
    document = build_word_alignment(beats, 'c' * 64, 3,
        [item('GPU', .1, .3), item('：', .3, .3), item('SDK', .4, .8),
         item('处理+返回', 1, 2)], SOURCE)
    assert [row['text'] for row in document['units']] == ['GPU', 'S D K', '处理+返回']
    assert document['units'][1]['start'] == .4
    for wrong in ('gpu', 'ＧＰＵ'):
        with pytest.raises(ServiceError, match='word_alignment_text_mismatch'):
            build_word_alignment(beats, 'c' * 64, 3,
                [item(wrong, .1, .3), item('SDK', .4, .8), item('处理+返回', 1, 2)], SOURCE)
    with pytest.raises(ServiceError, match='word_alignment_text_mismatch'):
        build_word_alignment(beats, 'c' * 64, 3,
            [item('GPU', .1, .3), item('SDK', .4, .8), item('处理返回', 1, 2)], SOURCE)


def test_actual_short_units_are_not_padded_to_legacy_minimums():
    beats = make_beats('好。')
    document = build_word_alignment(beats, 'a' * 64, 1, [item('好', .1, .15)], SOURCE)
    assert beat_segments(document, beats)[0]['end'] == .15
    assert document['units'][0]['end'] - document['units'][0]['start'] < .12


def test_numbers_are_not_rewritten_to_match_backend_transcription():
    beats = make_beats('需要12秒。')
    with pytest.raises(ServiceError, match='word_alignment_text_mismatch'):
        build_word_alignment(beats, 'a' * 64, 2, [item('需要十二秒', .1, 1.5)], SOURCE)


def test_unit_text_keeps_intervening_punctuation_without_timing_it():
    beats = make_beats('模型A、B返回。')
    document = build_word_alignment(beats, 'a' * 64, 2,
        [item('模型', .1, .5), item('A B', .6, 1), item('返回', 1.1, 1.5)], SOURCE)
    assert document['units'][1] == dict(spoken_start=2, spoken_end=5, text='A、B', start=.6, end=1)
    with pytest.raises(ServiceError, match='word_anchor_empty'):
        resolve_word_anchor(document, beats, 3, 4)


@pytest.mark.parametrize('items', [None, {}, (), [], [item('。', 0, 0)], [item(' ', 0, 0)]])
def test_empty_or_non_list_results_cannot_claim_success(items):
    beats = make_beats('请求。')
    with pytest.raises(ServiceError):
        build_word_alignment(beats, 'a' * 64, 2, items, SOURCE)


@pytest.mark.parametrize('change', [
    lambda rows: rows.pop(),
    lambda rows: rows.insert(0, item('额外', 0, .1)),
    lambda rows: rows[0].update(text='X'),
    lambda rows: rows[0].update(text='s'),
    lambda rows: rows[0].update(start_time=True),
    lambda rows: rows[0].update(end_time=float('nan')),
    lambda rows: rows[0].update(end_time=float('inf')),
    lambda rows: rows[0].update(end_time=10 ** 1000),
    lambda rows: rows[0].update(end_time=.1),
    lambda rows: rows[1].update(start_time=.25),
    lambda rows: rows[0].update(start_time=-.1),
    lambda rows: rows[-1].update(end_time=5.1),
    lambda rows: rows[0].update(text='S\x00'),
    lambda rows: rows[0].update(text='S\u202e'),
    lambda rows: rows[0].update(extra='unbounded'),
])
def test_incomplete_wrong_or_invalid_backend_items_fail_honestly(change):
    beats, items, _ = fixture()
    change(items)
    with pytest.raises(ServiceError):
        build_word_alignment(beats, 'a' * 64, 5, items, SOURCE)


@pytest.mark.parametrize('change', [
    lambda doc: doc.update(schema='video.alignment.v1'),
    lambda doc: doc.update(audio_sha256='a' * 63),
    lambda doc: doc.update(audio_seconds=True),
    lambda doc: doc.update(audio_seconds=float('nan')),
    lambda doc: doc.update(audio_seconds=10 ** 1000),
    lambda doc: doc.update(spoken_sha256='0' * 64),
    lambda doc: doc.update(beats_sha256='0' * 64),
    lambda doc: doc.update(review_status='verified'),
    lambda doc: doc.update(reviewed_by='invented'),
    lambda doc: doc.update(reviewed_at='2026-11-05T00:00:00Z'),
    lambda doc: doc.update(units=[]),
    lambda doc: doc['units'][0].update(spoken_start=True),
    lambda doc: doc['units'][0].update(spoken_end=2),
    lambda doc: doc['units'][0].update(text='X'),
    lambda doc: doc['units'][1].update(start=.2),
    lambda doc: doc['units'][0].update(end=float('nan')),
    lambda doc: doc['units'][0].update(extra='unexpected'),
    lambda doc: doc['source'].update(kind='estimated'),
    lambda doc: doc['source'].update(kind=[]),
    lambda doc: doc['source'].update(version='bad\x01'),
    lambda doc: doc['source'].update(provider=' '),
    lambda doc: doc.update(extra='unexpected'),
])
def test_malformed_or_forged_documents_are_rejected(change):
    beats, _, document = fixture()
    change(document)
    with pytest.raises(ServiceError):
        validate_word_alignment(document, beats)


def test_same_spoken_text_with_different_source_mapping_is_stale():
    beats, _, document = fixture()
    changed = make_beats('别名 接收请求。请求再次进入队列。', beats['spoken_text'])
    with pytest.raises(ServiceError, match='word_alignment_text_mismatch'):
        validate_word_alignment(document, changed)


def test_changed_spoken_text_is_stale_even_if_lengths_match():
    beats, _, document = fixture()
    changed = make_beats(beats['source_text'], beats['spoken_text'].replace('队列', '任务'))
    with pytest.raises(ServiceError, match='word_alignment_text_mismatch'):
        validate_word_alignment(document, changed)


def test_no_approval_or_mutable_alias_is_imported_from_backend():
    beats, items, document = fixture()
    original = deepcopy(document)
    items[0]['start_time'] = 0
    output = validate_word_alignment(document, beats)
    output['source']['model'] = 'changed'
    output['units'][0]['start'] = 0
    assert document == original
    assert SOURCE['model'] == 'aligner'


@pytest.mark.parametrize('start,end', [(True, 2), (0, False), (-1, 2), (3, 2), (0, 500)])
def test_invalid_anchor_ranges_fail(start, end):
    beats, _, document = fixture()
    with pytest.raises(ServiceError, match='invalid_word_anchor'):
        resolve_word_anchor(document, beats, start, end)


def test_punctuation_only_anchors_have_no_invented_speech_time():
    beats, _, document = fixture()
    index = beats['spoken_text'].index('。')
    with pytest.raises(ServiceError, match='word_anchor_empty'):
        resolve_word_anchor(document, beats, index, index + 1)


def test_contract_resource_limits_are_enforced(monkeypatch):
    import video_workbench.word_alignment as module
    beats, items, document = fixture()
    monkeypatch.setattr(module, 'MAX_BACKEND_ITEMS', len(items) - 1)
    with pytest.raises(ServiceError):
        build_word_alignment(beats, 'a' * 64, 5, items, SOURCE)
    monkeypatch.setattr(module, 'MAX_UNITS', len(document['units']) - 1)
    with pytest.raises(ServiceError):
        validate_word_alignment(document, beats)
    monkeypatch.setattr(module, 'MAX_UNITS', MAX_UNITS)
    monkeypatch.setattr(module, 'MAX_ALIGNMENT_BYTES', 1)
    with pytest.raises(ServiceError, match='word_alignment_too_large'):
        validate_word_alignment(document, beats)
    assert MAX_BACKEND_ITEMS > MAX_UNITS and MAX_ALIGNMENT_BYTES > 0


@pytest.mark.parametrize('text', ['请求\x00。', '请求\u0085。', '请求\u200b。', '请求\ud800。'])
def test_hidden_or_invalid_spoken_control_characters_are_rejected(text):
    beats = make_beats('请求啊。')
    for prefix in ('source', 'spoken'):
        beats[prefix + '_text'] = text
        beats['segments'][0][prefix + '_text'] = text
    with pytest.raises(ServiceError):
        build_word_alignment(beats, 'a' * 64, 2, [item('请求', .1, .8)], SOURCE)
