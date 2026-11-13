"""Reversible project removal; media and execution journals are never erased."""
from __future__ import annotations

import json
import time

from agent_service.contracts import ServiceError


CHILD_TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
JOURNALS = ('operations', 'audio_operations', 'motion_operations',
            'explanation_operations', 'joint_plan_operations', 'video_renders',
            'video_batches', 'production_runs')


class ProjectLifecycleMixin:
    @staticmethod
    def _init_project_lifecycle_schema(db):
        db.execute('''CREATE TABLE IF NOT EXISTS project_lifecycle (
            project_id TEXT PRIMARY KEY, deleted_at REAL,
            last_action TEXT NOT NULL, from_revision INTEGER NOT NULL)''')

    @staticmethod
    def _lifecycle(db, project_id):
        row = db.execute('SELECT deleted_at,last_action,from_revision FROM project_lifecycle '
                         'WHERE project_id=?', (project_id,)).fetchone()
        return dict(row) if row is not None else None

    @classmethod
    def _project_summary(cls, db, project):
        lifecycle = cls._lifecycle(db, project['id'])
        return dict(id=project['id'], title=project['title'], revision=project['revision'],
            updated_at=project['updated_at'], scene_count=len(project['scenes']),
            production_mode=project.get('production_mode', 'static'),
            deleted_at=lifecycle['deleted_at'] if lifecycle else None)

    @staticmethod
    def _project_delete_busy(db, project_id):
        """Called under the same IMMEDIATE transaction as the tombstone write.

        Admissions also read _project under an IMMEDIATE transaction. A new
        admission therefore either wins first and blocks trash, or observes
        the tombstone and cannot start. Unknown upstream work remains visible.
        """
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in JOURNALS:
            if table not in tables:
                continue
            for row in db.execute('SELECT document FROM ' + table + ' WHERE project_id=?', (project_id,)):
                work = json.loads(row[0])
                terminal = (frozenset({'completed', 'partial_failed', 'cancelled'}) if table == 'video_batches'
                    else frozenset({'completed', 'failed', 'cancelled', 'stale'}) if table == 'production_runs'
                    else CHILD_TERMINAL)
                error = work.get('error') or ''
                busy = (work.get('status') not in terminal or work.get('draining')
                    or work.get('blocked_reason') or 'uncertain' in error or 'unconfirmed' in error)
                if table == 'audio_operations':
                    # Only an explicit unconfirmed stop is evidence here.
                    # Ordinary historical terminal rows may predate this flag;
                    # absence alone must not make them permanently undeletable.
                    busy = busy or (work.get('status') != 'completed' and work.get('submit_attempted')
                                    and work.get('upstream_stop_confirmed') is False)
                if table == 'production_runs':
                    busy = busy or any(intent.get('active') for intent in work.get('intents', {}).values())
                if table == 'video_batches':
                    busy = busy or any(item.get('_uncertain') for item in work.get('items', []))
                if busy:
                    raise ServiceError('project_delete_busy', 409)

    def set_project_deleted(self, owner, project_id, expected_revision, *, deleted):
        if type(expected_revision) is not int or expected_revision < 1 or type(deleted) is not bool:
            raise ServiceError('invalid_request', 422)
        with self.transaction() as db:
            project = self._project(db, owner, project_id, include_deleted=True)
            lifecycle = self._lifecycle(db, project_id)
            currently_deleted = bool(lifecycle and lifecycle['deleted_at'] is not None)
            action = 'trash' if deleted else 'restore'
            if currently_deleted == deleted:
                # A lost response can be replayed only for the latest matching
                # action. An old trash arriving after restore cannot delete it.
                replay_revision = lifecycle['from_revision'] if lifecycle and lifecycle['last_action'] == action else None
                if expected_revision not in (project['revision'], replay_revision):
                    raise ServiceError('revision_conflict', 409)
                return self._project_summary(db, project)
            self._revision(project, expected_revision)
            if deleted:
                self._project_delete_busy(db, project_id)
            self._save(db, project)
            db.execute('INSERT INTO project_lifecycle VALUES (?, ?, ?, ?) '
                'ON CONFLICT(project_id) DO UPDATE SET deleted_at=excluded.deleted_at,'
                'last_action=excluded.last_action,from_revision=excluded.from_revision',
                (project_id, time.time() if deleted else None, action, expected_revision))
            return self._project_summary(db, project)
