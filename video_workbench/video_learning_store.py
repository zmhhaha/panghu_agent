"""Independent video-learning records over the shared durable learning store."""
from copy import deepcopy
import json

from agent_service.contracts import canonical_bytes, digest
from .picture_learning_store import PictureLearningStore, identifier, owner_id, require


class VideoLearningStore(PictureLearningStore):
    table_prefix = 'video'
    database_name = 'video-learning.sqlite3'
    max_blob_bytes = 32 * 1024 * 1024

    def __init__(self, root):
        super().__init__(root)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS video_analyses ('
                'owner TEXT NOT NULL,id TEXT NOT NULL,reference_id TEXT NOT NULL,document TEXT NOT NULL,'
                'PRIMARY KEY(owner,id))')

    def analysis(self, owner, identity, *, db=None):
        owner_id(owner)
        identifier(identity)
        if db is None:
            with self.db() as connection:
                return self.analysis(owner, identity, db=connection)
        row = db.execute('SELECT document FROM video_analyses WHERE owner=? AND id=?',
            (owner, identity)).fetchone()
        require(row is not None, 'video_learning_analysis_not_found', 404)
        value = json.loads(row['document'])
        require(value['owner'] == owner and value['id'] == identity
            and digest(value['frozen']) == value['frozen_sha256'],
            'video_learning_analysis_changed', 409)
        require(value.get('input') is None or digest(value['input']) == value['input_sha256'],
                'video_learning_analysis_input_changed', 409)
        return value

    def save_analysis(self, value, *, insert=False, db=None):
        if db is None:
            with self.db() as connection:
                return self.save_analysis(value, insert=insert, db=connection)
        if insert:
            db.execute('INSERT INTO video_analyses VALUES (?,?,?,?)', (value['owner'], value['id'],
                value['reference_id'], canonical_bytes(value).decode('utf-8')))
        else:
            prior = self.analysis(value['owner'], value['id'], db=db)
            require(prior['frozen_sha256'] == value['frozen_sha256']
                and prior['frozen'] == value['frozen'], 'video_learning_analysis_changed', 409)
            value['stop_requested'] = prior['stop_requested'] or value['stop_requested']
            db.execute('UPDATE video_analyses SET document=? WHERE owner=? AND id=?',
                (canonical_bytes(value).decode('utf-8'), value['owner'], value['id']))
        return deepcopy(value)

    def analyses(self, owner=None):
        with self.db() as db:
            if owner is None:
                rows = db.execute('SELECT document FROM video_analyses ORDER BY rowid DESC')
            else:
                owner_id(owner)
                rows = db.execute('SELECT document FROM video_analyses WHERE owner=? ORDER BY rowid DESC', (owner,))
            return [json.loads(row['document']) for row in rows]

    def claim_analysis(self, owner, identity, now):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            value = self.analysis(owner, identity, db=db)
            if value['operation_status'] != 'reserved' or value['stop_requested'] or now >= value['deadline_at']:
                return False
            value.update(operation_status='claimed', claimed_at=now)
            self.save_analysis(value, db=db)
            return True
