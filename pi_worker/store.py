"""SQLite lifecycle authority; every transition uses a write transaction."""
import base64
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from scientific_agent.pi_contracts import ContractError, canonical_json, parse_timestamp, public_status, utc_now

ACTIVE = ('queued', 'running', 'cancel_requested', 'validating', 'publishing')
TERMINAL = ('succeeded', 'failed', 'cancelled', 'timed_out')


class QueueStore:
    def __init__(self, db_path, *, revision_owner_bytes=64*1024*1024,
                 revision_global_bytes=256*1024*1024):
        if (type(revision_owner_bytes) is not int or revision_owner_bytes < 1
                or type(revision_global_bytes) is not int or revision_global_bytes < 1):
            raise ValueError('Revision input quotas must be positive byte counts')
        self.revision_owner_bytes = revision_owner_bytes
        self.revision_global_bytes = revision_global_bytes
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if __import__('os').name != 'nt':
            self.path.parent.chmod(0o700)
        self._lock = threading.RLock()
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created TEXT NOT NULL, state TEXT NOT NULL, snapshot TEXT NOT NULL, job TEXT NOT NULL, lease TEXT, lease_until TEXT, result TEXT, result_path TEXT, expires TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS rejected_tasks (id TEXT PRIMARY KEY, input_digest TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, status INTEGER NOT NULL, created TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS execution_uids (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, attempt INTEGER NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS revision_inputs (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id), payload TEXT NOT NULL,
                payload_bytes INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
                expires TEXT NOT NULL)''')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_user ON tasks(user_id) WHERE state IN ('queued','running','cancel_requested','validating','publishing')")

    @contextmanager
    def _tx(self):
        with self._lock:
            db = sqlite3.connect(str(self.path), timeout=10)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def _row(self, db, task_id):
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            rejected = db.execute('SELECT * FROM rejected_tasks WHERE id=?',(task_id,)).fetchone()
            if rejected:
                raise ContractError(rejected['code'],rejected['message'],rejected['status'])
            raise ContractError('task_not_found', 'Task not found', 404)
        return row

    def _save(self, db, row, snap, **fields):
        snap['revision'] += 1
        snap['updated_at'] = utc_now()
        snap['status'] = public_status(snap['state'])
        values = dict(state=snap['state'], snapshot=canonical_json(snap), **fields)
        db.execute('UPDATE tasks SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?', (*values.values(), row['id']))
        return snap

    @staticmethod
    def _remember_rejection(db, payload, error):
        db.execute('INSERT INTO rejected_tasks(id,input_digest,code,message,status,created) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',(payload['task_id'],payload['input_digest'],error.code,error.message,error.status_code,utc_now()))

    def reject(self, payload, error):
        """Durably bind a valid, new wire task ID to its admission rejection."""
        with self._tx() as db:
            if db.execute('SELECT 1 FROM tasks WHERE id=?',(payload['task_id'],)).fetchone():
                raise ContractError('task_conflict','Task ID already accepted',409)
            self._remember_rejection(db,payload,error)
            row=db.execute('SELECT * FROM rejected_tasks WHERE id=?',(payload['task_id'],)).fetchone()
            if row['input_digest'] != payload['input_digest']:
                raise ContractError('task_conflict','Task ID is bound to another input',409)
            rejection=ContractError(row['code'],row['message'],row['status'])
        raise rejection

    def submit(self, job, max_queue=None):
        from scientific_agent.pi_contracts import ResearchJob
        payload = job.to_dict() if hasattr(job, 'to_dict') else ResearchJob.from_dict(job).to_dict()
        # Keep the original endpoint strictly ResearchJob v1, including callers
        # that pass an object rather than a dict.
        payload = ResearchJob.from_dict(payload).to_dict()
        return self._submit(payload, max_queue=max_queue)

    def submit_revision(self, job, material, max_queue=None):
        from scientific_agent.pi_revision_contracts import RevisionJob, validate_revision_materials
        payload = job.to_dict() if hasattr(job, 'to_dict') else job
        payload = RevisionJob.from_dict(payload).to_dict()
        validate_revision_materials(payload, material)
        encoded = canonical_json(material)
        # Input size/hash checks are independent of the HTTP transport and apply
        # equally to in-process admission and replay.
        raw = encoded.encode('utf-8')
        if len(raw) > 16*1024*1024:
            raise ContractError('revision_input_too_large', 'Revision input exceeds byte limit', 413)
        if hashlib.sha256(raw).hexdigest() != payload['input_sha256']:
            raise ContractError('revision_input_mismatch', 'Revision input digest mismatch', 409)
        self.expire_queued()
        return self._submit(payload, max_queue=max_queue, revision_input=encoded)

    def _submit(self, payload, max_queue=None, revision_input=None):
        rejection = None
        with self._tx() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (payload['task_id'],)).fetchone()
            if row:
                if json.loads(row['job'])['input_digest'] != payload['input_digest']:
                    raise ContractError('task_conflict', 'Task ID is bound to another input', 409)
                return json.loads(row['snapshot']), False
            tombstone=db.execute('SELECT * FROM rejected_tasks WHERE id=?',(payload['task_id'],)).fetchone()
            if tombstone:
                if tombstone['input_digest'] != payload['input_digest']:
                    raise ContractError('task_conflict','Task ID is bound to another input',409)
                raise ContractError(tombstone['code'],tombstone['message'],tombstone['status'])
            try:
                if max_queue is not None and db.execute("SELECT count(*) FROM tasks WHERE state IN ('queued','running','cancel_requested','validating','publishing')").fetchone()[0] >= max_queue:
                    raise ContractError('queue_full','Queue admission limit reached',429)
                if revision_input is not None:
                    required = len(revision_input.encode('utf-8'))
                    global_bytes = db.execute('SELECT COALESCE(SUM(payload_bytes),0) FROM revision_inputs').fetchone()[0]
                    owner_bytes = db.execute('SELECT COALESCE(SUM(i.payload_bytes),0) FROM revision_inputs i JOIN tasks t ON t.id=i.task_id WHERE t.user_id=?', (payload['user_id'],)).fetchone()[0]
                    if owner_bytes+required > self.revision_owner_bytes or global_bytes+required > self.revision_global_bytes:
                        raise ContractError('revision_input_quota_exceeded', 'Revision input storage limit reached', 429)
                if parse_timestamp(payload['execution_deadline_at']) <= parse_timestamp(utc_now()):
                    raise ContractError('invalid_deadline', 'Execution deadline has elapsed', 422)
                snap = dict(schema_version='1', id=payload['task_id'], topic=payload['topic'], engine='pi', mode=payload['mode'], state='queued', status='pending', revision=0, attempt=0, stage=None, progress=None, created_at=payload['created_at'], updated_at=utc_now(), execution_deadline_at=payload['execution_deadline_at'], started_at=None, finished_at=None, error_code=None, error=None, report_id=None, result_digest=None, report_sha256=None, report=None)
                if revision_input is not None:
                    snap['task_kind'] = 'revision'
                    snap['intent_id'] = payload['intent_id']
                try:
                    db.execute('INSERT INTO tasks(id,user_id,created,state,snapshot,job) VALUES(?,?,?,?,?,?)', (snap['id'], payload['user_id'], snap['created_at'], 'queued', canonical_json(snap), canonical_json(payload)))
                except sqlite3.IntegrityError:
                    raise ContractError('user_busy', 'User already has an active Pi task', 429)
                if revision_input is not None:
                    db.execute('INSERT INTO revision_inputs(task_id,payload,payload_bytes,payload_sha256,expires) VALUES(?,?,?,?,?)',
                               (payload['task_id'], revision_input, len(revision_input.encode('utf-8')),
                                payload['input_sha256'], payload['execution_deadline_at']))
            except ContractError as exc:
                rejection=exc
                self._remember_rejection(db,payload,exc)
        if rejection:
            raise rejection
        return snap, True

    def get(self, task_id):
        with self._tx() as db:
            return json.loads(self._row(db, task_id)['snapshot'])

    def _finish(self, db, row, snap, state, code=None):
        snap.update(state=state, error_code=code, error=code, finished_at=utc_now())
        # Only tombstones and bounded job metadata survive terminal execution.
        # Replays are still bound by the immutable task input digest.
        db.execute('DELETE FROM revision_inputs WHERE task_id=?', (row['id'],))
        return self._save(db, row, snap, lease=None, lease_until=None)

    def cancel(self, task_id):
        with self._tx() as db:
            row = self._row(db, task_id)
            snap = json.loads(row['snapshot'])
            if snap['state'] == 'queued':
                return self._finish(db, row, snap, 'cancelled', 'cancelled')
            if snap['state'] in ('running', 'validating'):
                snap['state'] = 'cancel_requested'
                return self._save(db, row, snap)
            return snap

    def expire_queued(self):
        with self._tx() as db:
            for row in db.execute("SELECT * FROM tasks WHERE state='queued'").fetchall():
                snap = json.loads(row['snapshot'])
                if parse_timestamp(snap['execution_deadline_at']) <= parse_timestamp(utc_now()):
                    self._finish(db, row, snap, 'timed_out', 'deadline_exceeded')

    def claim(self, ttl=30):
        self.expire_queued()
        with self._tx() as db:
            if db.execute("SELECT 1 FROM tasks WHERE state IN ('running','validating','cancel_requested')").fetchone():
                return None
            row = db.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if not row:
                return None
            snap = json.loads(row['snapshot'])
            # Starting Pi does not establish which research phase it is in.
            # Keep the stage unknown until an observed event or controller
            # transition supplies it; never display "search" for the whole run.
            snap.update(state='running', attempt=snap['attempt']+1, started_at=utc_now(), stage=None)
            token = uuid.uuid4().hex
            allocation = db.execute('INSERT INTO execution_uids(task_id,attempt) VALUES(?,?)',(snap['id'],snap['attempt']))
            runtime_uid = 199999 + allocation.lastrowid
            if runtime_uid >= 2**31-1:
                raise ContractError('configuration_missing','Execution identity range exhausted',503)
            self._save(db, row, snap, lease=token, lease_until=self._until(ttl))
            internal_job = json.loads(row['job'])
            internal_job['_runtime_uid'] = runtime_uid
            if internal_job.get('task_kind') == 'revision':
                material = db.execute('SELECT * FROM revision_inputs WHERE task_id=?', (row['id'],)).fetchone()
                if material is None:
                    self._finish(db, row, snap, 'failed', 'revision_input_unavailable')
                    return None
                raw = material['payload'].encode('utf-8')
                if (len(raw) > 16*1024*1024 or len(raw) != material['payload_bytes']
                        or hashlib.sha256(raw).hexdigest() != material['payload_sha256']
                        or material['payload_sha256'] != internal_job['input_sha256']):
                    self._finish(db, row, snap, 'failed', 'revision_input_corrupt')
                    return None
                try:
                    internal_job['_revision_input'] = json.loads(material['payload'])
                except (ValueError, TypeError):
                    self._finish(db, row, snap, 'failed', 'revision_input_corrupt')
                    return None
            return dict(job=internal_job, snapshot=snap, token=token)

    @staticmethod
    def _until(seconds):
        return (parse_timestamp(utc_now()) + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')

    def _fenced(self, db, task_id, attempt, token):
        row = self._row(db, task_id)
        snap = json.loads(row['snapshot'])
        if row['lease'] != token or snap['attempt'] != attempt or snap['state'] not in ('running','validating','cancel_requested') or parse_timestamp(row['lease_until']) <= parse_timestamp(utc_now()):
            raise ContractError('stale_attempt', 'Attempt no longer owns this task', 409)
        return row, snap

    def renew(self, task_id, attempt, token, ttl=30):
        with self._tx() as db:
            row, snap = self._fenced(db, task_id, attempt, token)
            db.execute('UPDATE tasks SET lease_until=? WHERE id=?', (self._until(ttl), task_id))
            return snap

    def stage(self, task_id, attempt, token, stage):
        if stage not in ('search','screen','extract','synthesize','write','validate','publish'):
            return
        with self._tx() as db:
            row, snap = self._fenced(db, task_id, attempt, token)
            if snap['state'] == 'cancel_requested':
                return snap
            snap['stage'] = stage
            if stage == 'validate':
                snap['state'] = 'validating'
            return self._save(db, row, snap)

    def finish_attempt(self, task_id, attempt, token, state, code, retry=False, max_attempts=2):
        with self._tx() as db:
            row, snap = self._fenced(db, task_id, attempt, token)
            if snap['state'] == 'cancel_requested':
                state, code, retry = 'cancelled', 'cancelled', False
            if parse_timestamp(snap['execution_deadline_at']) <= parse_timestamp(utc_now()):
                state, code, retry = 'timed_out', 'deadline_exceeded', False
            if retry and attempt < max_attempts:
                snap.update(state='queued', stage=None, error_code=code, error=code)
                return self._save(db, row, snap, lease=None, lease_until=None)
            return self._finish(db, row, snap, state, code)

    def recover(self, max_attempts=2):
        """Call only under instance lock after orphan processes are confirmed stopped."""
        with self._tx() as db:
            for row in db.execute("SELECT * FROM tasks WHERE state IN ('running','validating','cancel_requested')").fetchall():
                snap = json.loads(row['snapshot'])
                if snap['state'] == 'cancel_requested':
                    self._finish(db, row, snap, 'cancelled', 'cancelled')
                elif parse_timestamp(snap['execution_deadline_at']) <= parse_timestamp(utc_now()):
                    self._finish(db, row, snap, 'timed_out', 'deadline_exceeded')
                elif snap['attempt'] >= max_attempts:
                    self._finish(db, row, snap, 'failed', 'worker_interrupted')
                else:
                    snap.update(state='queued', stage=None, error_code='worker_interrupted', error='worker_interrupted')
                    self._save(db, row, snap, lease=None, lease_until=None)
        self.expire_queued()

    def publish(self, task_id, attempt, token, result, result_path, retention_days=30):
        with self._tx() as db:
            row, snap = self._fenced(db, task_id, attempt, token)
            if snap['state'] == 'cancel_requested':
                return self._finish(db, row, snap, 'cancelled', 'cancelled')
            if parse_timestamp(snap['execution_deadline_at']) <= parse_timestamp(utc_now()):
                return self._finish(db, row, snap, 'timed_out', 'deadline_exceeded')
            snap.update(state='publishing', stage='publish', result_digest=result['result_digest'], report_sha256=result['report_sha256'], error_code=None, error=None)
            db.execute('DELETE FROM revision_inputs WHERE task_id=?', (task_id,))
            return self._save(db, row, snap, lease=None, lease_until=None, result=canonical_json(result), result_path=str(result_path), expires=self._until(retention_days*86400))

    def result(self, task_id):
        with self._tx() as db:
            row = self._row(db, task_id)
            if row['state'] not in ('publishing','succeeded') or not row['result']:
                raise ContractError('result_not_ready', 'Validated result is not ready', 409)
            return json.loads(row['result'])

    def ack(self, task_id, attempt, digest, report_id):
        with self._tx() as db:
            row = self._row(db, task_id)
            snap = json.loads(row['snapshot'])
            if row['state'] not in ('publishing','succeeded') or attempt != snap['attempt'] or digest != snap['result_digest'] or report_id != task_id:
                raise ContractError('publication_conflict', 'Acknowledgement does not match committed result', 409)
            if row['state'] == 'succeeded':
                return snap
            snap['report_id'] = task_id
            return self._finish(db, row, snap, 'succeeded')

    def pending(self, cursor=None, limit=50):
        if not 1 <= limit <= 100:
            raise ContractError('invalid_limit', 'Limit must be 1..100', 422)
        where, args = "state='publishing' AND result IS NOT NULL", []
        if cursor:
            try:
                created, task_id = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if not isinstance(created, str) or not isinstance(task_id, str):
                    raise ValueError()
            except Exception:
                raise ContractError('invalid_cursor', 'Invalid publication cursor', 422)
            where += ' AND (created,id) > (?,?)'
            args += [created, task_id]
        with self._tx() as db:
            rows = db.execute('SELECT * FROM tasks WHERE '+where+' ORDER BY created,id LIMIT ?', (*args,limit+1)).fetchall()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps([last['created'],last['id']]).encode()).decode()
        return dict(items=[json.loads(r['snapshot']) for r in rows[:limit]], next_cursor=next_cursor)

    def artifact_record(self, task_id):
        with self._tx() as db:
            row = self._row(db, task_id)
            if row['state'] not in ('publishing','succeeded'):
                raise ContractError('result_not_ready', 'Artifacts are not ready', 409)
            if parse_timestamp(row['expires']) <= parse_timestamp(utc_now()):
                raise ContractError('artifact_expired', 'Artifact retention has elapsed', 410)
            return json.loads(row['result']), Path(row['result_path'])

    def cleanup_expired(self, runtime_root=None, retention_days=30):
        import shutil
        def remove_tree(path):
            path=Path(path)
            if path.is_symlink():
                raise ContractError('invalid_artifact','Refusing symlink cleanup target',422)
            def remove_readonly(function, filename, exc):
                __import__('os').chmod(filename,0o700)
                function(filename)
            if path.exists():
                shutil.rmtree(path,onexc=remove_readonly)
        with self._tx() as db:
            rows = db.execute("SELECT * FROM tasks WHERE state='succeeded' AND result_path IS NOT NULL").fetchall()
            for row in rows:
                if parse_timestamp(row['expires']) <= parse_timestamp(utc_now()):
                    remove_tree(row['result_path'])
                    db.execute('UPDATE tasks SET result_path=NULL WHERE id=?', (row['id'],))
            if runtime_root is not None:
                root=Path(runtime_root)
                cutoff=parse_timestamp(utc_now())-timedelta(days=retention_days)
                for row in db.execute("SELECT * FROM tasks WHERE state IN ('succeeded','failed','cancelled','timed_out')").fetchall():
                    finished=json.loads(row['snapshot'])['finished_at']
                    if finished and parse_timestamp(finished)<=cutoff:
                        remove_tree(root/'tasks'/row['id'])
                referenced={str(Path(r[0]).resolve()) for r in db.execute('SELECT result_path FROM tasks WHERE result_path IS NOT NULL')}
                results=root/'results'
                if results.is_dir() and not results.is_symlink():
                    for task_dir in results.iterdir():
                        if not task_dir.is_dir() or task_dir.is_symlink():
                            continue
                        candidates=[task_dir] if task_dir.name.startswith('.validate-') else list(task_dir.iterdir())
                        for path in candidates:
                            if path.is_dir() and not path.is_symlink() and str(path.resolve()) not in referenced and path.stat().st_mtime<=cutoff.timestamp():
                                remove_tree(path)

    def execution_uids(self):
        with self._tx() as db:
            return [199999 + row[0] for row in db.execute('SELECT sequence FROM execution_uids ORDER BY sequence')]

    def writable(self):
        with self._tx() as db:
            db.execute('UPDATE tasks SET id=id WHERE 0')
        return True
