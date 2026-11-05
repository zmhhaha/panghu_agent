"""Storage audits preserve business history and fail closed before reclamation."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from video_workbench.batch_store import BatchStore
from video_workbench.explanation_store import ExplanationStore
from video_workbench.motion_store import MotionStore
from video_workbench.motion_timing import MotionTiming
from video_workbench.production import Production
from video_workbench.store import Store
from video_workbench.motion_render_k8s import ExecutionJournal, operation_key
from video_workbench import storage_maintenance as storage
from test_motion_snapshot import freeze


@pytest.fixture
def workspace(tmp_path):
    business = tmp_path / 'business.db'
    executor = tmp_path / 'executor.db'
    root = tmp_path / 'executions'
    root.mkdir()
    store = Store(business)
    motion = MotionStore(store)
    MotionTiming(motion)
    ExplanationStore(store)
    production = Production(store)
    BatchStore(store, motion, production)
    owner, operation = 'one-owner', 'one-operation'
    snapshot = freeze()
    packed = dict(snapshot, scenes=[dict(scene, audio={key: value for key, value in scene['audio'].items()
        if key != 'data_base64'}) for scene in snapshot['scenes']])
    job = dict(id=operation, owner=owner, project_id='project', status='completed',
               snapshot_sha256=snapshot['sha256'])
    project = dict(id='project', scenes=[dict(id='scene-motion', head_audio_version_id='adopted-audio',
                                           head_version_id='adopted-visual')])
    with store.transaction() as db:
        db.execute('INSERT INTO projects VALUES (?,?,?)', ('project', owner, json.dumps(project)))
        db.execute('INSERT INTO video_renders VALUES (?,?,?,?,?,?,?,?)',
            (operation, owner, 'project', 'idempotent-render', 'r' * 64, json.dumps(job), json.dumps(packed), b'historical mp4'))
        db.execute('INSERT INTO audio_versions VALUES (?,?,?,?,?,?)',
            ('adopted-audio', 'project', 'scene-motion', 'audio-op', '{}', b'historical adopted wav'))
    journal = ExecutionJournal(executor)
    row = journal.submit(hashlib.sha256(owner.encode()).hexdigest(), operation, snapshot)
    key = row['key']
    journal.update(key, state='completed', result='{}')
    scene_name = 'vm-' + hashlib.sha256((key + ':0').encode()).hexdigest()[:36]
    journal.scene_intent(key + ':0', 'b' * 64, scene_name)
    journal.scene_update(key + ':0', 'completed', result={})
    files = {
        'result/assets/master.wav': b'rebuildable PCM',
        'scene-000/input/audio.wav': b'rebuildable input',
        'scene-000/output.pending': b'unfinished output',
        'result/video.mp4': b'preserved complete mp4',
        'result/report.json': b'preserved report',
        'scene-000/output.mp4': b'preserved scene mp4',
        'scene-000/evidence.json': b'preserved evidence',
        'unknown/notes.txt': b'unknown preserve',
    }
    for relative, raw in files.items():
        path = root / key / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return dict(business=business, executor=executor, root=root, store=store, journal=journal,
                key=key, owner=owner, operation=operation, files=files)


def plan(workspace, **kwargs):
    return storage.inventory(workspace['business'], workspace['executor'], workspace['root'],
                             min_age_seconds=kwargs.pop('min_age_seconds', 0), **kwargs)


def test_inventory_uses_real_schema_and_never_changes_business_rows_or_media(workspace):
    before = workspace['business'].read_bytes(), workspace['executor'].read_bytes()
    result = plan(workspace)
    assert result['apply_allowed'] and result['blockers'] == []
    assert {row['path'].split('/', 1)[1] for row in result['candidates']} == {
        'result/assets/master.wav', 'scene-000/input/audio.wav', 'scene-000/output.pending'}
    assert result['business']['tables']['video_renders']['blob_bytes'] == len(b'historical mp4')
    assert result['business']['tables']['audio_versions']['blob_bytes'] == len(b'historical adopted wav')
    assert before == (workspace['business'].read_bytes(), workspace['executor'].read_bytes())
    assert all((workspace['root'] / workspace['key'] / name).read_bytes() == raw
               for name, raw in workspace['files'].items())
    encoded = json.dumps(result)
    assert 'historical adopted wav' not in encoded and workspace['store'].csrf_secret not in encoded


@pytest.mark.parametrize('table,state', [('motion_operations', 'running'), ('motion_scenes', 'cleanup_pending')])
def test_active_executor_or_pending_cleanup_blocks_apply(workspace, table, state):
    with sqlite3.connect(workspace['executor']) as db:
        db.execute(f'UPDATE {table} SET state=?', (state,))
    result = plan(workspace)
    assert not result['apply_allowed']
    assert any('executor' in reason for reason in result['blockers'])


def test_active_business_operation_blocks_apply_even_for_terminal_executor(workspace):
    with workspace['store'].transaction() as db:
        db.execute('UPDATE video_renders SET document=?', (json.dumps(dict(status='cancelling')),))
    assert 'active_or_unknown_business_operation' in plan(workspace)['blockers']


@pytest.mark.parametrize('change', ['missing_business_render', 'owner_mismatch', 'snapshot_mismatch'])
def test_unproven_orphan_or_broken_mapping_is_report_only(workspace, change):
    with workspace['store'].transaction() as db:
        if change == 'missing_business_render':
            db.execute('DELETE FROM video_renders')
        elif change == 'owner_mismatch':
            db.execute("UPDATE video_renders SET owner='other'")
        else:
            db.execute("UPDATE video_renders SET snapshot='{}'")
    assert not plan(workspace)['candidates']
    assert all((workspace['root'] / workspace['key'] / name).exists() for name in workspace['files'])


def test_possible_reference_in_business_source_preserves_the_file(workspace):
    with workspace['store'].transaction() as db:
        db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?)',
            ('reference', 'project', 'scene-motion', 'visual-op', 'master.wav', 'svg'))
    result = plan(workspace)
    assert not any(row['path'].endswith('master.wav') for row in result['candidates'])
    assert any(row['reason'] == 'possible_business_reference_preserved' for row in result['retained'])


def test_default_age_preserves_recent_scratch(workspace):
    assert not plan(workspace, min_age_seconds=86400)['candidates']


@pytest.mark.parametrize('limit,value', [('max_files', 2), ('max_rows', 1), ('max_text_bytes', 1),
                                       ('max_hash_bytes', 1), ('max_candidates', 1)])
def test_bounded_partial_inventory_never_produces_an_applicable_manifest(workspace, limit, value):
    with pytest.raises(storage.MaintenanceError, match='budget'):
        plan(workspace, limits={limit: value})


@pytest.mark.parametrize('statement', [
    'CREATE TABLE future_file_references(path TEXT)',
    'CREATE TABLE sqliteXreferences(path TEXT)',
    'CREATE VIEW future_reference_view AS SELECT document FROM projects',
    'CREATE TRIGGER future_reference_trigger AFTER INSERT ON projects BEGIN SELECT 1; END',
])
def test_unknown_future_reference_schema_blocks_automatic_reclamation(workspace, statement):
    with sqlite3.connect(workspace['business']) as db:
        db.execute(statement)
    with pytest.raises(storage.MaintenanceError, match='unknown_schema'):
        plan(workspace)


def test_unreadable_database_is_not_created_or_treated_as_empty(workspace, tmp_path, capsys):
    missing = tmp_path / 'missing.db'
    assert storage.main(['--business-db', str(missing), '--executor-db', str(workspace['executor']),
                         '--execution-root', str(workspace['root'])]) == 2
    assert not missing.exists()
    assert json.loads(capsys.readouterr().out)['apply_allowed'] is False


def test_manifest_requires_external_new_file_and_has_exact_file_digest(workspace, tmp_path):
    value = plan(workspace)
    path = tmp_path / 'review.json'
    checksum = storage.write_manifest(value, path)
    assert checksum == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        storage.write_manifest(value, path)
    with pytest.raises(storage.MaintenanceError, match='location_invalid'):
        storage.write_manifest(value, workspace['root'] / 'review.json')


def test_hardlink_does_not_turn_an_external_file_into_reclaimable_scratch(workspace, tmp_path):
    outside = tmp_path / 'external.bin'
    outside.write_bytes(b'keep outside')
    alias = workspace['root'] / workspace['key'] / 'result/assets/linked.bin'
    try:
        os.link(outside, alias)
    except OSError:
        pytest.skip('hardlinks unavailable')
    assert not plan(workspace)['apply_allowed']
    assert outside.read_bytes() == b'keep outside'


def test_directory_symlink_never_scans_or_reclaims_an_outside_tree(workspace, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'precious').write_bytes(b'keep outside')
    link = workspace['root'] / workspace['key'] / 'result/assets/linked'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlink privilege unavailable')
    result = plan(workspace)
    assert not result['apply_allowed']
    assert not any('precious' in row['path'] for row in result['candidates'])


def test_reserved_wal_lock_blocks_new_work_and_authorizer_denies_row_changes(workspace):
    with storage.database(workspace['business'], reserve=True) as readonly:
        with pytest.raises(sqlite3.DatabaseError):
            readonly.execute("DELETE FROM video_renders")
        with sqlite3.connect(workspace['business'], timeout=0) as writer:
            with pytest.raises(sqlite3.OperationalError):
                writer.execute('BEGIN IMMEDIATE')
    with sqlite3.connect(workspace['business']) as db:
        assert db.execute('SELECT data FROM video_renders').fetchone()[0] == b'historical mp4'


@pytest.mark.parametrize('change', ['digest', 'expired', 'blocked'])
def test_reviewed_manifest_rejects_wrong_digest_expiry_or_prior_blockers(workspace, tmp_path, change):
    value = plan(workspace)
    if change == 'blocked':
        value['blockers'] = ['active']
        value['apply_allowed'] = False
    target = tmp_path / 'review.json'
    checksum = storage.write_manifest(value, target)
    with pytest.raises(storage.MaintenanceError):
        storage.inspect_manifest(target, '0' * 64 if change == 'digest' else checksum,
            now=value['expires_at'] + 1 if change == 'expired' else value['created_at'])
    assert all((workspace['root'] / workspace['key'] / name).exists() for name in workspace['files'])


def test_file_mutating_during_hash_never_receives_a_valid_preview(workspace, monkeypatch):
    original = os.read
    changed = False
    target = workspace['root'] / workspace['key'] / 'result/assets/master.wav'
    def read(descriptor, count):
        nonlocal changed
        raw = original(descriptor, count)
        if raw and not changed:
            changed = True
            target.write_bytes(b'concurrently updated scratch')
        return raw
    monkeypatch.setattr(storage.os, 'read', read)
    with pytest.raises(storage.MaintenanceError, match='identity_changed'):
        plan(workspace)


def test_manifest_cannot_replace_sqlite_sidecar(workspace):
    with pytest.raises(storage.MaintenanceError, match='location_invalid'):
        storage.write_manifest(plan(workspace), Path(str(workspace['business']) + '-wal'))


@pytest.mark.skipif(os.name == 'posix', reason='Windows-specific safe refusal')
def test_apply_refuses_platform_without_safe_directory_descriptors(workspace, tmp_path):
    target = tmp_path / 'review.json'
    checksum = storage.write_manifest(plan(workspace), target)
    with pytest.raises(storage.MaintenanceError, match='linux_required'):
        storage.apply_manifest(target, checksum, tmp_path / 'receipt.jsonl')
    assert not (tmp_path / 'receipt.jsonl').exists()
