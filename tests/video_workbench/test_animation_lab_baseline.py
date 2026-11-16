"""The developer corpus reset is explicit, bounded and performed once."""
import sqlite3

from video_workbench.animation_lab_baseline import CORPUS_VERSION, initialize_baseline


def test_reset_discards_old_lab_data_without_touching_other_tables_or_business_file(tmp_path):
    path = tmp_path / 'lab' / 'animation-lab.db'
    path.parent.mkdir()
    business = tmp_path / 'business.db'
    business.write_bytes(b'business projects and media are outside this operation')
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE lab_snapshots(identity TEXT);
            INSERT INTO lab_snapshots VALUES('retired-case');
            CREATE TABLE lab_annotations(comment TEXT);
            INSERT INTO lab_annotations VALUES('old opinion');
            CREATE TABLE unrelated(value TEXT);
            INSERT INTO unrelated VALUES('keep');
        ''')
    receipt = initialize_baseline(path)
    assert receipt == dict(version=CORPUS_VERSION, reset=True,
                           removed_counts={'lab_snapshots': 1, 'lab_annotations': 1})
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM unrelated').fetchall() == [('keep',)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name IN ('lab_snapshots','lab_annotations')").fetchall() == []
        assert db.execute('SELECT version FROM lab_corpus_baseline').fetchall() == [(CORPUS_VERSION,)]
        assert db.execute('PRAGMA quick_check').fetchone() == ('ok',)
    assert business.read_bytes() == b'business projects and media are outside this operation'


def test_restart_preserves_annotations_created_under_current_baseline(tmp_path):
    path = tmp_path / 'animation-lab.db'
    assert initialize_baseline(path)['reset'] is False
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE lab_annotations(comment TEXT); INSERT INTO lab_annotations VALUES('new opinion');")
    assert initialize_baseline(path) == dict(version=CORPUS_VERSION, reset=False)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM lab_annotations').fetchall() == [('new opinion',)]


def test_retired_mounted_reports_cannot_reimport_legacy_feedback(tmp_path, monkeypatch):
    from video_workbench.animation_lab import AnimationLab, LabError
    from video_workbench import animation_lab_evaluation_view
    from threading import RLock
    import pytest
    class RejectRegistration:
        def register_report(self, snapshot):
            raise AssertionError('retired report registered')
    lab = AnimationLab.__new__(AnimationLab)
    lab.evaluations_dir = tmp_path
    lab.triage_store = RejectRegistration()
    lab.evaluation_lock = RLock()
    root = tmp_path / 'retired-run'
    root.mkdir()
    (root / 'report.html').write_text('old report')
    lab.cases, lab.bundles = {}, {}
    for case_id, inputs in [('diagram-connections', {}), ('chart-energy-balance', {})]:
        monkeypatch.setattr(animation_lab_evaluation_view, 'load_evaluation',
            lambda _root, case_id=case_id, inputs=inputs: dict(run=dict(case_id=case_id, bundles=[dict(inputs=inputs)])))
        assert lab.evaluations() == []
        with pytest.raises(LabError, match='animation_lab_report_not_found'):
            lab._evaluation('retired-run')
        with pytest.raises(LabError, match='animation_lab_report_not_found'):
            lab.evaluation_file('retired-run', 'report.html')
