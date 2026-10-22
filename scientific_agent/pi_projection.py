"""Scientific-only shared SQLite index and recoverable worker publication.

The worker owns execution. This module never writes worker queue state.
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import timedelta
from .pi_client import WorkerError
from .pi_contracts import canonical_json, input_digest, report_sha256, result_digest, utc_now, parse_timestamp, ResearchJob

log = logging.getLogger(__name__)
NAMESPACE = uuid.UUID('bb91b052-f393-4653-ae4a-0a4845fce498')

def sql(value):
    return "'" + str(value).replace("'", "''") + "'" if value is not None else 'NULL'

class PiProjection:
    def __init__(self, database, worker, skill_revision='', runtime_revision='', notifier=None):
        self.db, self.worker = database, worker
        self.skill_revision, self.runtime_revision = skill_revision, runtime_revision
        self.notifier = notifier

    def initialize(self):
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_pi_index (
            id TEXT PRIMARY KEY, engine TEXT NOT NULL DEFAULT 'pi', user_id TEXT NOT NULL,
            input_digest TEXT NOT NULL, job TEXT NOT NULL, email TEXT NOT NULL,
            dispatch TEXT NOT NULL, rejection_status INTEGER, error_code TEXT, error TEXT,
            notification_status TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_notifications (
            id TEXT PRIMARY KEY, email TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL)''')

    def get(self, task_id):
        rows = self.db._query('SELECT * FROM scientific_pi_index WHERE id=' + sql(task_id))
        return rows[0] if rows else None

    def update(self, task_id, **values):
        values['updated_at'] = utc_now()
        self.db._execute('UPDATE scientific_pi_index SET ' + ','.join(k+'='+sql(v) for k,v in values.items()) + ' WHERE id=' + sql(task_id))

    def prepare(self, payload, email='', key=None):
        digest = input_digest(payload)
        task_id = str(uuid.uuid5(NAMESPACE, canonical_json([payload['user_id'], 'pi', key]))) if key else str(uuid.uuid4())
        existing = self.get(task_id)
        if existing:
            if existing['input_digest'] != digest or existing['email'] != email:
                raise WorkerError(409, 'task_conflict', 'Idempotency key is already bound to different input or email')
            return existing
        if not self.skill_revision or not self.runtime_revision:
            raise WorkerError(503, 'configuration_missing', 'Pinned Pi skill and runtime revisions are required', True)
        now = utc_now()
        job = dict(payload, schema_version='1', task_id=task_id, input_digest=digest, created_at=now,
                   execution_deadline_at=(parse_timestamp(now)+timedelta(seconds=payload['timeout_seconds'])).isoformat().replace('+00:00','Z'),
                   skill_bundle_revision=self.skill_revision, runtime_revision=self.runtime_revision)
        job = ResearchJob.from_dict(job).to_dict()
        self.db._execute('INSERT INTO scientific_pi_index (id,user_id,input_digest,job,email,dispatch,notification_status,created_at,updated_at) VALUES (' +
            ','.join(map(sql, [task_id,payload['user_id'],digest,canonical_json(job),email,'prepared','pending' if email else None,now,now])) + ') ON CONFLICT(id) DO NOTHING')
        row = self.get(task_id)
        if not row:
            raise WorkerError(503, 'storage_unavailable', 'Dispatch intent could not be read back', True)
        if row['input_digest'] != digest or row['email'] != email:
            raise WorkerError(409, 'task_conflict', 'Idempotency key is already bound to different input or email')
        return row

    def dispatch(self, row):
        if row['dispatch'] == 'rejected':
            raise WorkerError(int(row['rejection_status']), row['error_code'], row['error'])
        if row['dispatch'] == 'accepted':
            return self.worker.get(row['id'])
        try:
            try:
                snapshot = self.worker.get(row['id'])
            except WorkerError as exc:
                if exc.status != 404: raise
                snapshot = self.worker.put(json.loads(row['job']))
        except WorkerError as exc:
            if exc.status in (409, 422, 429):
                self.update(row['id'], dispatch='rejected', rejection_status=exc.status, error_code=exc.code, error=str(exc))
            else:
                self.update(row['id'], dispatch='ambiguous', error_code=exc.code, error=str(exc))
            raise
        self.update(row['id'], dispatch='accepted', error_code=None, error=None)
        return snapshot

    def publish(self, snapshot):
        task_id = snapshot['id']
        result = self.worker.result(task_id)
        content = result['report_markdown']
        if result['task_id'] != task_id or result['attempt'] != snapshot['attempt'] or report_sha256(content) != result['report_sha256'] or result_digest(result) != result['result_digest']:
            raise WorkerError(409, 'publication_conflict', 'Worker result digest or identity mismatch')
        row = self.get(task_id)
        if not row:
            raise WorkerError(409, 'publication_conflict', 'No durable API dispatch record for worker task')
        job = json.loads(row['job'])
        summary = next((line.strip() for line in content.splitlines() if line.strip() and not line.startswith('#')), content)[:200]
        values = [task_id,task_id,job['topic'],summary,json.dumps(job['topic'].split()[:5],ensure_ascii=False),content,result['provenance'].get('model')]
        self.db._execute('INSERT INTO scientific_reports (id,task_id,topic,summary,keywords,content,model_used) VALUES (' + ','.join(map(sql,values)) + ') ON CONFLICT(id) DO NOTHING')
        reports = self.db._query('SELECT * FROM scientific_reports WHERE id=' + sql(task_id))
        if not reports:
            raise WorkerError(503, 'storage_unavailable', 'Report insert could not be read back', True)
        if reports[0]['task_id'] != task_id or report_sha256(reports[0]['content']) != result['report_sha256']:
            raise WorkerError(409, 'publication_conflict', 'Stored report does not match immutable worker result')
        # A crash after insert and before ack is safe: replay checks exactly the same bytes.
        self.worker.ack(task_id, dict(attempt=result['attempt'],result_digest=result['result_digest'],report_id=task_id))
        self.notify(task_id, row['email'], job['topic'])
        self.update(task_id, error_code=None, error=None)

    def notify(self, task_id, email, topic):
        if not email: return
        self.db._execute('INSERT INTO scientific_notifications (id,email,status,updated_at) VALUES (' + ','.join(map(sql,[task_id,email,'pending',utc_now()])) + ') ON CONFLICT(id) DO NOTHING')
        # Conditional claim prevents duplicate concurrent sends. Unknown survives a send/crash window.
        claim = str(uuid.uuid4())
        self.db._execute('UPDATE scientific_notifications SET status='+sql('sending:'+claim)+',updated_at='+sql(utc_now())+' WHERE id='+sql(task_id)+" AND status='pending'")
        rows = self.db._query('SELECT * FROM scientific_notifications WHERE id='+sql(task_id))
        if not rows: return
        if rows[0]['status'] != 'sending:'+claim:
            # A prior sender may have finished before index update, or died mid-send.
            status = rows[0]['status']
            if status.startswith('sending:'): status = 'unknown'
            if self.get(task_id): self.update(task_id, notification_status=status)
            return
        try:
            if self.notifier:
                if self.notifier(email, topic, task_id) is False:
                    raise RuntimeError('Notification delivery was not confirmed')
            else: raise RuntimeError('Notification sender unavailable')
        except Exception:
            status = 'unknown'  # Remote send may have succeeded before the exception.
            log.warning('Notification delivery uncertain for %s', task_id)
        else: status = 'sent'
        self.db._execute('UPDATE scientific_notifications SET status='+sql(status)+',updated_at='+sql(utc_now())+' WHERE id='+sql(task_id))
        row = self.get(task_id)
        if row: self.update(task_id, notification_status=status)

    def reconcile(self):
        for row in self.db._query("SELECT * FROM scientific_pi_index WHERE dispatch IN ('prepared','ambiguous')"):
            try: self.dispatch(row)
            except Exception: log.exception('Pi dispatch recovery failed for %s', row['id'])
        cursor = None
        while True:
            page = self.worker.pending(cursor)
            for snapshot in page['items']:
                try: self.publish(snapshot)
                except Exception as exc:
                    log.exception('Pi publication pending for %s', snapshot['id'])
                    self.update(snapshot['id'], error_code=getattr(exc,'code','storage_unavailable'), error='Report publication pending; retrying')
            cursor = page.get('next_cursor')
            if not cursor: break
        # Ack may have succeeded before API crashed: finish notifications independently.
        for row in self.db._query("SELECT * FROM scientific_pi_index WHERE dispatch='accepted' AND notification_status='pending'"):
            try:
                snapshot = self.worker.get(row['id'])
                if snapshot['state'] == 'succeeded': self.notify(row['id'],row['email'],json.loads(row['job'])['topic'])
            except Exception: log.exception('Pi notification recovery failed for %s',row['id'])
