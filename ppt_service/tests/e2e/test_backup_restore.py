"""Quiescent SQLite + filesystem recovery through a fresh authenticated app.

This test class intentionally uses SQLite even when the other tests target an
isolated PostgreSQL service. It does not claim pg_dump, K8s/PVC or live backup
coverage. Its historical deck fixture is not publication/PowerPoint evidence.
"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select

from ppt_service.server.app import create_app
from ppt_service.server.models import Checkpoint, Job

from .backup_helpers import (
    assert_quiescent, backup_sqlite_fixture, files_inventory,
    metadata_snapshot, restore_sqlite_fixture,
)
from .harness import E2ECase, ORIGIN
from .test_security import parse_sse, seed_synthetic_historical_version


class SQLiteBackupRecoveryTests(E2ECase):
    database_backend = 'sqlite'

    def _prepare_backup(self):
        project = self.create_project()
        pid = project['id']
        self.upload(pid)
        body = {'expected_revision': self.project(pid)['revision'], 'phase': 'plan_initial'}
        key = 'restorable-plan-request'
        queued = self.client.post(f'/api/v1/projects/{pid}/plans', json=body,
                                 headers=self.headers() | {'Idempotency-Key': key})
        self.assertEqual(queued.status_code, 202)
        job = queued.json()
        self.assertEqual(self.complete_fake_phase(job['id'])['status'], 'succeeded')
        self.confirm(pid, {'design_mode': 'free_design', 'template_id': None})
        first, _ = seed_synthetic_historical_version(self, pid)
        second, _ = seed_synthetic_historical_version(self, pid, parent_version_id=first)
        self.assertFalse(self.worker.running)
        self.assertFalse(self.dispatcher.owned)
        with self.sessions() as session:
            checkpoints = session.scalars(select(Checkpoint).where(Checkpoint.project_id == pid)).all()
            self.assertGreaterEqual(len(checkpoints), 3)
            for checkpoint in checkpoints:
                self.assertTrue((self.data_root / checkpoint.storage_key).is_dir())
        events = parse_sse(self.client.get(f'/api/v1/jobs/{job["id"]}/events', headers=self.headers()))
        detail = self.project(pid)
        versions = self.client.get(f'/api/v1/projects/{pid}/versions', headers=self.headers()).json()
        self.assertEqual([row['id'] for row in versions], [first, second])
        self.assertEqual(versions[1]['parent_version_id'], first)
        self.client.close()
        assert_quiescent(self.sessions)
        backup = self.temp_root / 'snapshot'
        manifest = backup_sqlite_fixture(self.engine, self.sessions, self.db_path, self.data_root, backup)
        return {'pid': pid, 'job': job, 'body': body, 'key': key, 'events': events,
                'detail': detail, 'versions': versions, 'backup': backup, 'manifest': manifest}

    def test_sqlite_restore_preserves_bytes_metadata_auth_history_and_idempotency(self):
        source = self._prepare_backup()
        restored = self.temp_root / 'restored-instance'
        manifest = restore_sqlite_fixture(source['backup'], restored)
        restored_engine = create_engine('sqlite:///' + (restored / 'state.sqlite3').as_posix(),
                                        connect_args={'check_same_thread': False})
        @event.listens_for(restored_engine, 'connect')
        def foreign_keys(connection, _record):
            connection.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(restored_engine.dispose)
        settings = replace(self.settings, database_url=str(restored_engine.url), data_root=str(restored / 'data'))
        restored_app = create_app(settings, restored_engine)
        restored_app.state.jwks = self.app.state.jwks
        self.assertNotEqual(restored_app.state.storage.root, self.app.state.storage.root)
        self.assertEqual(metadata_snapshot(restored_engine), manifest['metadata'])
        self.assertEqual(files_inventory(restored / 'data'), manifest['files'])
        with TestClient(restored_app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
            self.assertEqual(client.get(f'/api/v1/projects/{source["pid"]}').status_code, 401)
            me = client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + self.token()})
            self.assertEqual(me.status_code, 200)
            headers = {'Authorization': 'Bearer ' + self.token(), 'Origin': ORIGIN,
                       'X-CSRF-Token': me.json()['csrf_token']}
            detail = client.get(f'/api/v1/projects/{source["pid"]}', headers=headers)
            self.assertEqual(detail.json(), source['detail'])
            versions = client.get(f'/api/v1/projects/{source["pid"]}/versions', headers=headers)
            self.assertEqual(versions.json(), source['versions'])
            events = client.get(f'/api/v1/jobs/{source["job"]["id"]}/events', headers=headers)
            self.assertEqual(parse_sse(events), source['events'])
            for version in source['versions']:
                for path in [version['download_url'], version['slides'][0]['preview_url']]:
                    self.assertEqual(client.get(path, headers=headers).status_code, 200)
                    self.assertEqual(client.get(path, headers=headers | {
                        'Authorization': 'Bearer ' + self.token('bob')}).status_code, 404)
            self.assertEqual(client.get(f'/api/v1/projects/{source["pid"]}', headers={
                'Authorization': 'Bearer ' + self.token('bob')}).status_code, 404)
            replay = client.post(f'/api/v1/projects/{source["pid"]}/plans', json=source['body'],
                                 headers=headers | {'Idempotency-Key': source['key']})
            self.assertEqual(replay.status_code, 202)
            self.assertEqual(replay.json(), source['job'])
            changed = client.post(f'/api/v1/projects/{source["pid"]}/plans',
                json=source['body'] | {'feedback': 'new request body'},
                headers=headers | {'Idempotency-Key': source['key']})
            self.assertEqual(changed.status_code, 409)
            self.assertEqual(changed.json()['detail']['code'], 'idempotency_conflict')
            self.assertEqual(metadata_snapshot(restored_engine), manifest['metadata'])
            fresh = client.post('/api/v1/projects', json={'title': 'Restored database is writable'}, headers=headers)
            self.assertEqual(fresh.status_code, 201)
        self.assertEqual(metadata_snapshot(self.engine), manifest['metadata'])
        self.assertEqual(files_inventory(self.data_root), manifest['files'])

    def test_backup_refuses_active_writer(self):
        pid = self.create_project()['id']
        queued = self.queue_plan(pid)
        self.complete_fake_phase(queued['id'], hold=True)
        self.client.close()
        with self.assertRaisesRegex(RuntimeError, 'execution slot must be idle'):
            backup_sqlite_fixture(self.engine, self.sessions, self.db_path,
                                  self.data_root, self.temp_root / 'forbidden-live-snapshot')
        self.assertFalse((self.temp_root / 'forbidden-live-snapshot').exists())
        self.assertTrue(self.worker.running)
        # Stop this synthetic writer through the real cancellation/controller path.
        with self.sessions.begin() as session:
            from ppt_service.server.repository import Repository
            Repository(session).cancel(session.get(Job, queued['id']))
        self.assertEqual(self.dispatcher.tick()['status'], 'stopped')
        self.assertFalse(self.worker.running)

    def test_restore_refuses_tampered_blob(self):
        source = self._prepare_backup()
        asset_key = next(key for key in source['manifest']['files'] if '/assets/' in key)
        asset = source['backup'] / 'data' / asset_key
        asset.chmod(0o600)
        asset.write_bytes(b'tampered synthetic bytes')
        destination = self.temp_root / 'tampered-restore'
        with self.assertRaisesRegex(RuntimeError, 'files checksum or metadata mismatch'):
            restore_sqlite_fixture(source['backup'], destination)
        self.assertFalse(destination.exists())
