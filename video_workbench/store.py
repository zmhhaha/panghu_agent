"""Transactional business state. Agent completion creates a candidate, never approval."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video import validate_video_input

ACTIVE = {'queued', 'submitting', 'running', 'cancelling'}


def uid():
    return str(uuid4())


class Store:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE, source TEXT NOT NULL, format TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    idem TEXT NOT NULL, request_hash TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(owner, idem));
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('csrf_secret', secrets.token_hex(32)))
            self.csrf_secret = db.execute('SELECT value FROM metadata WHERE key=?', ('csrf_secret',)).fetchone()[0]

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _project(db, owner, project_id):
        row = db.execute('SELECT document FROM projects WHERE id=? AND owner=?', (project_id, owner)).fetchone()
        if row is None:
            raise ServiceError('not_found', 404)
        return json.loads(row[0])

    @staticmethod
    def _save(db, project):
        project['revision'] += 1
        project['updated_at'] = time.time()
        db.execute('UPDATE projects SET document=? WHERE id=?', (json.dumps(project), project['id']))

    @staticmethod
    def _revision(project, expected):
        if project['revision'] != expected:
            raise ServiceError('revision_conflict', 409)

    @staticmethod
    def _scene(project, scene_id):
        for scene in project['scenes']:
            if scene['id'] == scene_id:
                return scene
        raise ServiceError('not_found', 404)

    @staticmethod
    def _op_public(op):
        return {key: op.get(key) for key in ('id', 'kind', 'status', 'error', 'job_id')}

    @staticmethod
    def _ops(db, project_id):
        return [json.loads(r[0]) for r in db.execute('SELECT document FROM operations WHERE project_id=? ORDER BY rowid', (project_id,))]

    def _busy(self, db, project_id, scene_id=None):
        for op in self._ops(db, project_id):
            if op['status'] in ACTIVE and (op['kind'] == 'storyboard' or scene_id is not None and op['scene_id'] == scene_id):
                raise ServiceError('operation_in_progress', 409)

    def create_project(self, owner, fields):
        project = dict(fields, id=uid(), revision=1, updated_at=time.time(), scenes=[])
        with self.transaction() as db:
            db.execute('INSERT INTO projects VALUES (?, ?, ?)', (project['id'], owner, json.dumps(project)))
        return project

    def list_projects(self, owner):
        with self.transaction() as db:
            projects = [json.loads(r[0]) for r in db.execute('SELECT document FROM projects WHERE owner=?', (owner,))]
        return [dict(id=p['id'], title=p['title'], updated_at=p['updated_at'], scene_count=len(p['scenes']))
                for p in sorted(projects, key=lambda p: p['updated_at'], reverse=True)]

    def project(self, owner, project_id):
        with self.transaction() as db:
            project = self._project(db, owner, project_id)
            project['storyboard_operation'] = None
            for scene in project['scenes']:
                scene['operation'] = None
            for op in self._ops(db, project_id):
                if op['kind'] == 'storyboard':
                    project['storyboard_operation'] = self._op_public(op)
                else:
                    self._scene(project, op['scene_id'])['operation'] = self._op_public(op)
            return project

    def scene_project(self, owner, scene_id):
        with self.transaction() as db:
            for row in db.execute('SELECT document FROM projects WHERE owner=?', (owner,)):
                p = json.loads(row[0])
                if any(s['id'] == scene_id for s in p['scenes']):
                    return p['id']
        raise ServiceError('not_found', 404)

    def save_scene(self, owner, project_id, fields, scene_id=None, expected_revision=None):
        with self.transaction() as db:
            project = self._project(db, owner, project_id)
            self._busy(db, project_id, scene_id)
            if scene_id:
                self._revision(project, expected_revision)
                self._scene(project, scene_id).update(fields)
            else:
                if len(project['scenes']) >= 24:
                    raise ServiceError('scene_limit', 422)
                project['scenes'].append(dict(fields, id=uid(), head_version_id=None, versions=[]))
            self._save(db, project)
        return self.project(owner, project_id)

    def start(self, owner, project_id, kind, request, idem, *, scene_id=None, timeout=600):
        fingerprint = digest(dict(project_id=project_id, scene_id=scene_id, kind=kind, request=request))
        with self.transaction() as db:
            # Idempotent replay precedes current revision/head checks.
            existing = db.execute('SELECT request_hash,document FROM operations WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise ServiceError('idempotency_conflict', 409)
                return self._op_public(json.loads(existing[1]))
            project = self._project(db, owner, project_id)
            self._revision(project, request['expected_revision'])
            self._busy(db, project_id, scene_id)
            base = None
            if kind == 'storyboard':
                if project['scenes']:
                    raise ServiceError('storyboard_requires_empty_project', 409)
                document = dict(schema_version='1', kind=kind, title=project['title'], script=project['script'],
                                style=project['style'], max_scenes=12)
            else:
                scene = self._scene(project, scene_id)
                base_id = request.get('base_version_id')
                if kind == 'revise' and not base_id:
                    raise ServiceError('base_version_required', 422)
                if base_id:
                    base = db.execute('SELECT source,format FROM versions WHERE id=? AND scene_id=? AND project_id=?',
                                      (base_id, scene_id, project_id)).fetchone()
                    if base is None:
                        raise ServiceError('not_found', 404)
                    if base['format'] != scene['format']:
                        raise ServiceError('base_format_conflict', 409)
                document = dict(schema_version='1', kind='scene', title=scene['title'], narration=scene['narration'],
                                visual_description=scene['visual_description'], style=project['style'], format=scene['format'],
                                width=1280, height=720, duration_seconds=scene['duration_seconds'],
                                base_source=base['source'] if base else None, instruction=request.get('instruction', ''))
            validate_video_input(document, 'storyboard' if kind == 'storyboard' else 'scene')
            now = time.time()
            op = dict(id=uid(), owner=owner, project_id=project_id, scene_id=scene_id, kind=kind,
                      document=document, input_sha256=digest(document), status='queued', error=None,
                      job_id=None, input_ref=None, submit_attempted=False, cancel_requested=False,
                      deadline_at=now + timeout, created_at=now, base_version_id=request.get('base_version_id'))
            db.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?)',
                       (op['id'], owner, project_id, idem, fingerprint, json.dumps(op)))
            self._save(db, project)
        return self._op_public(op)

    def operation(self, operation_id, owner=None):
        with self.transaction() as db:
            row = db.execute('SELECT owner,document FROM operations WHERE id=?', (operation_id,)).fetchone()
            if row is None or owner is not None and row['owner'] != owner:
                raise ServiceError('not_found', 404)
            return json.loads(row['document'])

    def update_operation(self, operation_id, **fields):
        with self.transaction() as db:
            row = db.execute('SELECT document FROM operations WHERE id=?', (operation_id,)).fetchone()
            op = json.loads(row[0])
            op.update(fields)
            db.execute('UPDATE operations SET document=? WHERE id=?', (json.dumps(op), operation_id))
            return op

    def active_operations(self):
        with self.transaction() as db:
            return [op for row in db.execute('SELECT document FROM operations')
                    if (op := json.loads(row[0]))['status'] in ACTIVE]

    def claim_submission(self, operation_id):
        """Atomically recheck latest cancellation/deadline before one POST claim."""
        with self.transaction() as db:
            row = db.execute('SELECT document FROM operations WHERE id=?', (operation_id,)).fetchone()
            op = json.loads(row[0])
            if op['status'] not in ACTIVE or op['submit_attempted']:
                return None
            if op['cancel_requested'] or time.time() >= op['deadline_at']:
                op.update(status='cancelled' if op['cancel_requested'] else 'timed_out', error=None)
                claimed = None
            else:
                op.update(submit_attempted=True, status='submitting', error=None)
                claimed = op
            db.execute('UPDATE operations SET document=? WHERE id=?', (json.dumps(op), operation_id))
            return claimed

    def cancel(self, owner, operation_id):
        with self.transaction() as db:
            row = db.execute('SELECT document FROM operations WHERE id=? AND owner=?', (operation_id, owner)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            op = json.loads(row[0])
            if op['status'] in ACTIVE:
                op.update(cancel_requested=True, status='cancelling')
                db.execute('UPDATE operations SET document=? WHERE id=?', (json.dumps(op), operation_id))
            return self._op_public(op)

    def complete(self, operation_id, output):
        with self.transaction() as db:
            op = json.loads(db.execute('SELECT document FROM operations WHERE id=?', (operation_id,)).fetchone()[0])
            if op['status'] not in ACTIVE:
                return
            project = self._project(db, op['owner'], op['project_id'])
            if op['cancel_requested']:
                # The remote job completed before cancellation. Its unused result is discarded.
                op.update(status='completed', error='cancel_arrived_after_completion')
            elif op['kind'] == 'storyboard':
                if project['scenes']:
                    raise ServiceError('storyboard_requires_empty_project', 409)
                project['scenes'] = [dict(s, id=uid(), head_version_id=None, versions=[]) for s in output['scenes']]
                op.update(status='completed', error=None)
            else:
                scene = self._scene(project, op['scene_id'])
                version_id = uid()
                source = output['source']
                metadata = dict(id=version_id, number=len(scene['versions']) + 1, summary=output['summary'],
                                format=output['format'], source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                                created_at=time.time(), parent_version_id=op['base_version_id'])
                db.execute('INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?)',
                           (version_id, project['id'], scene['id'], op['id'], source, output['format']))
                scene['versions'].append(metadata)
                op.update(status='completed', error=None)
            self._save(db, project)
            db.execute('UPDATE operations SET document=? WHERE id=?', (json.dumps(op), operation_id))

    def adopt(self, owner, scene_id, version_id, expected_head):
        project_id = self.scene_project(owner, scene_id)
        with self.transaction() as db:
            project = self._project(db, owner, project_id)
            scene = self._scene(project, scene_id)
            if scene['head_version_id'] != expected_head:
                raise ServiceError('head_conflict', 409)
            if not any(v['id'] == version_id for v in scene['versions']):
                raise ServiceError('not_found', 404)
            scene['head_version_id'] = version_id
            self._save(db, project)
        return self.project(owner, project_id)

    def version(self, owner, version_id):
        with self.transaction() as db:
            row = db.execute('SELECT v.* FROM versions v JOIN projects p ON p.id=v.project_id WHERE v.id=? AND p.owner=?',
                             (version_id, owner)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            return dict(row)
