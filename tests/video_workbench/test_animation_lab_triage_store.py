"""Developer dispositions stay bound to exact machine evidence and keep history."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from uuid import UUID

import pytest

from video_workbench.animation_lab_store import AnnotationStore
from video_workbench.animation_lab_triage_store import TriageStore
from video_workbench.motion import canonical, sha256


def report(*, run='a', review='c', description='连线遮挡文字'):
    value = dict(schema='video.animation-lab-triage-snapshot.v1',
        run_identity=run * 64, evidence_identity='b' * 64,
        review_identity=review * 64 if review else None,
        findings=[dict(id='model:issue-1', description=description, variant_id='layout-focus'),
                  dict(id='technical:clip-1', description='技术检查问题', variant_id='candidate')],
        source=dict(module='diagram', artifacts=['d' * 64, 'e' * 64]))
    return dict(value, identity=sha256(value))


def records(store):
    return [json.loads(line) for line in store.export_jsonl().splitlines()]


def test_exact_report_registration_is_idempotent_and_caller_mutation_does_not_relabel_it(tmp_path):
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = report()
    returned = store.register_report(snapshot)
    store.register_report(deepcopy(snapshot))
    original = deepcopy(snapshot)
    snapshot['findings'][0]['description'] = 'changed after registration'
    returned['source']['module'] = 'changed returned object'
    assert records(store) == [dict(record_type='evaluation_snapshot', snapshot=original)]
    with pytest.raises(ValueError, match='identity_mismatch'):
        store.register_report(snapshot)


def test_existing_conflicting_source_is_not_overwritten(tmp_path):
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = report()
    store.register_report(snapshot)
    # Simulate an already corrupted DB row; even a correctly signed incoming
    # report must not silently overwrite different source bytes under its ID.
    corrupted = dict(snapshot, source='unexpected preexisting source')
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE lab_evaluation_snapshots SET document = ? WHERE identity = ?',
                   (canonical(corrupted).decode(), snapshot['identity']))
    with pytest.raises(ValueError, match='identity_conflict'):
        store.register_report(snapshot)
    assert records(store)[0]['snapshot'] == corrupted


def test_latest_decision_supersedes_only_its_report_and_finding_and_survives_restart(tmp_path):
    path = tmp_path / 'lab.db'
    store = TriageStore(path)
    first_report, newer_report = report(), report(review='f')
    store.register_report(first_report)
    store.register_report(newer_report)
    fix = store.append(first_report['identity'], 'model:issue-1', 'needs_fix', '保留原内容，避让标签')
    defer = store.append(first_report['identity'], 'technical:clip-1', 'defer')
    dismiss = store.append(first_report['identity'], 'model:issue-1', 'dismiss', '是原设计的强调效果')
    new_fix = store.append(newer_report['identity'], 'model:issue-1', 'needs_fix', '这是新的评审版本')
    restored = TriageStore(path)
    rows = restored.list()
    assert [row['id'] for row in rows] == [fix['id'], defer['id'], dismiss['id'], new_fix['id']]
    assert [row['effective'] for row in rows] == [False, True, True, True]
    assert [row['superseded'] for row in rows] == [True, False, False, False]
    assert len(restored.list(first_report['identity'])) == 3
    assert restored.list('0' * 64) == []
    assert str(UUID(fix['id'])) == fix['id']
    assert fix['created_at'].endswith('+00:00') and fix['reviewer'] == 'local-developer'
    assert fix['run_identity'] == first_report['run_identity']


def test_changed_run_and_changed_evidence_are_independent_decision_scopes(tmp_path):
    store = TriageStore(tmp_path / 'lab.db')
    first = report()
    changed_run = report(run='f')
    changed_evidence = dict(first, evidence_identity='0' * 64)
    changed_evidence['identity'] = sha256({k: v for k, v in changed_evidence.items() if k != 'identity'})
    for snapshot in (first, changed_run, changed_evidence):
        store.register_report(snapshot)
        store.append(snapshot['identity'], 'model:issue-1', 'needs_fix')
    assert len(store.list()) == 3
    assert all(row['effective'] for row in store.list())


def test_unknown_report_or_finding_does_not_create_decision(tmp_path):
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = report()
    store.register_report(snapshot)
    with pytest.raises(ValueError, match='report_not_found'):
        store.append('0' * 64, 'model:issue-1', 'needs_fix')
    with pytest.raises(ValueError, match='finding_not_found'):
        store.append(snapshot['identity'], 'invented-finding', 'needs_fix')
    assert store.list() == []


@pytest.mark.parametrize('decision,comment', [
    ('approve_production', ''), ([], ''), (True, ''),
    ('needs_fix', 'x' * 4001), ('dismiss', None), ('defer', 'bad\0comment'),
    ('defer', '\ud800'),
])
def test_unbounded_or_invalid_dispositions_are_not_persisted(tmp_path, decision, comment):
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = report()
    store.register_report(snapshot)
    with pytest.raises(ValueError, match='invalid'):
        store.append(snapshot['identity'], 'model:issue-1', decision, comment)
    assert store.list() == []


@pytest.mark.parametrize('mutator', [
    lambda row: row.update(schema='other'),
    lambda row: row.update(run_identity='not-a-sha'),
    lambda row: row.update(evidence_identity=None),
    lambda row: row.update(review_identity='not-a-sha'),
    lambda row: row.pop('review_identity'),
    lambda row: row['findings'].append(deepcopy(row['findings'][0])),
    lambda row: row['findings'][0].update(id='../script'),
    lambda row: row.update(extra=float('nan')),
])
def test_report_schema_and_finding_identity_rejected_before_storage(tmp_path, mutator):
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = report()
    mutator(snapshot)
    with pytest.raises(ValueError):
        store.register_report(snapshot)
    assert records(store) == []


def test_technical_only_and_no_finding_reports_remain_valid_without_implied_approval(tmp_path):
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = report(review=None)
    snapshot['findings'] = []
    snapshot['identity'] = sha256({k: v for k, v in snapshot.items() if k != 'identity'})
    store.register_report(snapshot)
    assert records(store)[0]['snapshot']['review_identity'] is None
    assert store.list() == []
    with pytest.raises(ValueError, match='finding_not_found'):
        store.append(snapshot['identity'], 'model:issue-1', 'needs_fix')


def test_exports_are_self_contained_and_keep_superseded_comments(tmp_path):
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = report()
    store.register_report(snapshot)
    first = store.append(snapshot['identity'], 'model:issue-1', 'needs_fix', '看一下这个位置\n保持文字内容。')
    second = store.append(snapshot['identity'], 'model:issue-1', 'defer', '先处理更严重的问题')
    rows = records(store)
    assert rows[0] == dict(record_type='evaluation_snapshot', snapshot=snapshot)
    decision_rows = rows[1:]
    assert [row['record_type'] for row in decision_rows] == ['evaluation_decision'] * 2
    assert [row['decision']['id'] for row in decision_rows] == [first['id'], second['id']]
    assert [row['effective'] for row in decision_rows] == [False, True]
    assert decision_rows[0]['decision']['comment'] == first['comment']
    assert all(row['decision']['report_identity'] == rows[0]['snapshot']['identity'] for row in decision_rows)


def test_store_coexists_with_manual_annotations_without_mutating_or_exporting_them(tmp_path):
    path = tmp_path / 'animation-lab.db'
    manual = AnnotationStore(path)
    manual.register_snapshot(dict(identity='manual-frozen', case_id='case'))
    original = manual.append(dict(case_id='case', case_identity='manual-frozen', kind='issue',
        variant_id='candidate', comment='人工标注保持独立'))
    before = manual.export_jsonl()
    triage = TriageStore(path)
    snapshot = report()
    triage.register_report(snapshot)
    triage.append(snapshot['identity'], 'model:issue-1', 'dismiss', '误报')
    assert manual.list()[0]['id'] == original['id']
    assert manual.export_jsonl() == before
    assert all(row['record_type'] in {'evaluation_snapshot', 'evaluation_decision'} for row in records(triage))


def test_concurrent_store_instances_preserve_every_event_and_one_latest_choice(tmp_path):
    path = tmp_path / 'lab.db'
    store = TriageStore(path)
    snapshot = report()
    store.register_report(snapshot)
    def save(index):
        other = TriageStore(path)
        other.register_report(snapshot)
        return other.append(snapshot['identity'], 'model:issue-1',
                            ('needs_fix', 'dismiss', 'defer')[index % 3], f'event {index}')
    with ThreadPoolExecutor(max_workers=4) as pool:
        saved = list(pool.map(save, range(12)))
    rows = store.list()
    assert len(rows) == 12 and {row['id'] for row in rows} == {row['id'] for row in saved}
    assert sum(row['effective'] for row in rows) == 1
    assert rows[-1]['effective'] is True
    assert len(records(store)) == 13
