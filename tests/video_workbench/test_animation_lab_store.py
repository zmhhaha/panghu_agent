"""Review corrections must not become contradictory active training examples."""
import json

import pytest

from video_workbench.animation_lab_store import AnnotationStore


def test_preference_correction_and_withdrawal_preserve_history_but_one_effective_choice(tmp_path):
    store = AnnotationStore(tmp_path / 'lab.db')
    store.register_snapshot(dict(identity='frozen-comparison', case_id='case'))
    first = store.append(dict(case_id='case', case_identity='frozen-comparison',
        variant_id='comparison', kind='preference', preference='baseline'))
    second = store.append(dict(case_id='case', case_identity='frozen-comparison',
        variant_id='comparison', kind='preference', preference='candidate'))

    def records():
        return [row for row in map(json.loads, store.export_jsonl().decode().splitlines())
                if row['record_type'] == 'annotation']

    rows = records()
    assert [row['annotation']['id'] for row in rows] == [first['id'], second['id']]
    assert [row['effective'] for row in rows] == [False, True]
    assert rows[0]['superseded'] is True
    store.retract(second['id'], '误点，恢复上一项选择')
    assert [row['effective'] for row in records()] == [True, False]
    assert records()[1]['annotation']['retraction']['reason'] == '误点，恢复上一项选择'


def test_snapshot_identity_cannot_relabel_already_reviewed_content(tmp_path):
    store = AnnotationStore(tmp_path / 'lab.db')
    snapshot = dict(identity='one-comparison', case_id='case', content='old')
    store.register_snapshot(snapshot)
    store.register_snapshot(snapshot)
    with pytest.raises(ValueError, match='identity_conflict'):
        store.register_snapshot(dict(snapshot, content='different'))
    exported = [json.loads(line) for line in store.export_jsonl().decode().splitlines()]
    assert exported == [dict(record_type='case_snapshot', snapshot=snapshot)]
