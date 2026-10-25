"""Scientific-only shared SQLite index and recoverable worker publication.

The worker owns execution. This module never writes worker queue state.
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import timedelta
import base64
import hashlib
from .pi_client import WorkerError
from .pi_contracts import canonical_json, input_digest, report_sha256, result_digest, utc_now, parse_timestamp, ResearchJob

log = logging.getLogger(__name__)
NAMESPACE = uuid.UUID('bb91b052-f393-4653-ae4a-0a4845fce498')
REVISION_NAMESPACE = uuid.UUID('a1266b7f-2ef8-49fc-bf67-962d2c0e01d2')

def sql(value):
    return "'" + str(value).replace("'", "''") + "'" if value is not None else 'NULL'

class PiProjection:
    def __init__(self, database, worker, skill_revision='', runtime_revision='', notifier=None):
        self.db, self.worker = database, worker
        self.skill_revision, self.runtime_revision = skill_revision, runtime_revision
        self.notifier = notifier
        self.versions = None

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

    def _revision_index(self, binding):
        binding = self.versions.get_revision_job(binding['owner'],binding['task_id'])
        if binding is None:
            raise WorkerError(409,'revision_job_conflict','Revision input reservation is missing')
        job = json.loads(binding['job'])
        values = [job['task_id'],job['user_id'],job['input_digest'],binding['job'],'','prepared',None,
                  job['created_at'],utc_now()]
        self.db._execute('INSERT INTO scientific_pi_index (id,user_id,input_digest,job,email,dispatch,notification_status,created_at,updated_at) VALUES (' +
                         ','.join(map(sql,values)) + ') ON CONFLICT(id) DO NOTHING')
        row = self.get(job['task_id'])
        if (not row or row['job'] != binding['job'] or row['user_id'] != job['user_id']
                or row['input_digest'] != job['input_digest'] or row['email']):
            raise WorkerError(409,'revision_job_conflict','Revision index does not match its reserved input')
        return row

    def prepare_revision(self, owner, intent_id):
        from .pi_revision_contracts import RevisionJob, revision_material_digest, revision_input_digest
        binding = self.versions.find_revision_job(owner,intent_id)
        if binding:
            return self._revision_index(binding)
        intent = self.versions.get_intent(owner,intent_id)
        health = self.worker.health()
        capabilities = health.get('capabilities',{})
        if (not all(health.get(k) is True for k in ('alive','queue_writable','runtime_ready','worker_ready'))
                or '1' not in capabilities.get('revision_job_versions',[])
                or capabilities.get('revision_input_scope') != 'recorded_evidence_only'):
            raise WorkerError(503,'revision_not_supported','The worker is not ready for fixed-evidence revisions',True)
        if not self.skill_revision or not self.runtime_revision:
            raise WorkerError(503,'configuration_missing','Pinned Pi skill and runtime revisions are required',True)
        fixed = self.versions.get_snapshot(owner,intent['snapshot_id'])
        material = dict(report=fixed['report'],items=fixed['items'],
                        artifacts={k:base64.b64encode(v).decode('ascii') for k,v in fixed['artifacts'].items()})
        maximum = capabilities.get('max_revision_input_bytes')
        if type(maximum) is not int or len(canonical_json(material).encode('utf-8')) > maximum:
            raise WorkerError(413,'revision_input_too_large','The worker cannot accept the fixed revision input')
        now = utc_now()
        task_id = str(uuid.uuid5(REVISION_NAMESPACE,canonical_json([owner,intent_id])))
        parent = self.versions.get_version(owner,intent['parent_version_id'])
        topic = parent['topic'].strip()[:500] or 'Report section revision'
        job = dict(schema_version='1',task_kind='revision',task_id=task_id,user_id=owner,
                   topic=topic,engine='pi',mode='review',created_at=now,
                   execution_deadline_at=(parse_timestamp(now)+timedelta(seconds=intent['timeout_seconds'])).isoformat().replace('+00:00','Z'),
                   skill_bundle_revision=self.skill_revision,runtime_revision=self.runtime_revision,
                   input_sha256=revision_material_digest(material),material_policy='recorded_evidence_only',
                   **{k:intent[k] for k in ('intent_id','parent_version_id','root_report_id','base_sha256',
                       'section_id','section_sha256','instruction','timeout_seconds')})
        job['input_digest'] = revision_input_digest(job)
        job = RevisionJob.from_dict(job).to_dict()
        binding = self.versions.reserve_revision_job(owner,intent_id,job)
        return self._revision_index(binding)

    def sync_revision(self, row, snapshot):
        if json.loads(row['job']).get('task_kind') == 'revision':
            self.versions.update_revision_job(row['user_id'],row['id'],snapshot['state'])
        return snapshot

    def _cancellation_snapshot(self, row, state):
        job = json.loads(row['job'])
        return dict(id=row['id'],topic=job['topic'],engine='pi',mode=job['mode'],state=state,
                    schema_version='1',attempt=0,revision=0,created_at=job['created_at'],
                    updated_at=utc_now(),execution_deadline_at=job['execution_deadline_at'],
                    error_code='cancelled' if state == 'cancelled' else None,
                    error='Cancelled before worker acceptance' if state == 'cancelled' else None)

    def is_revision(self, row):
        return json.loads(row['job']).get('task_kind') == 'revision'

    def recover_revision_cancel(self, row):
        """Persisted cancellation wins over a later dispatch retry.

        An in-flight PUT may still arrive after GET returns 404. Keep polling
        until its immutable execution deadline; only then can absence confirm
        cancellation. Publishing retains the existing non-cancellable boundary.
        """
        binding = self.versions.get_revision_job(row['user_id'],row['id'])
        if binding['status'] == 'cancelled':
            return self._cancellation_snapshot(row,'cancelled')
        try:
            snapshot = self.worker.get(row['id'])
        except WorkerError as exc:
            if exc.status in (409,422,429):
                self.update(row['id'],dispatch='rejected',rejection_status=exc.status,error_code=exc.code,error=str(exc))
                self.versions.update_revision_job(row['user_id'],row['id'],'rejected')
                raise
            if exc.status != 404: raise
            self.update(row['id'],dispatch='cancel_pending')
            job = json.loads(row['job'])
            if parse_timestamp(job['execution_deadline_at']) <= parse_timestamp(utc_now()):
                self.versions.update_revision_job(row['user_id'],row['id'],'cancelled')
                self.update(row['id'],dispatch='cancelled')
                return self._cancellation_snapshot(row,'cancelled')
            return self._cancellation_snapshot(row,'cancel_requested')
        if snapshot['state'] not in ('publishing','succeeded','failed','timed_out','cancelled'):
            snapshot = self.worker.cancel(row['id'])
        self.update(row['id'],dispatch='accepted')
        return self.sync_revision(row,snapshot)

    def cancel_revision(self, row):
        binding = self.versions.get_revision_job(row['user_id'],row['id'])
        if binding['status'] not in ('publishing','succeeded','failed','timed_out','cancelled','rejected'):
            self.versions.update_revision_job(row['user_id'],row['id'],'cancel_requested')
        return self.recover_revision_cancel(row)

    def dispatch(self, row):
        if self.is_revision(row):
            binding = self.versions.get_revision_job(row['user_id'],row['id'])
            if binding['status'] in ('cancel_requested','cancelled'):
                return self.recover_revision_cancel(row)
        if row['dispatch'] == 'rejected':
            raise WorkerError(int(row['rejection_status']), row['error_code'], row['error'])
        if row['dispatch'] == 'accepted':
            return self.sync_revision(row,self.worker.get(row['id']))
        try:
            try:
                snapshot = self.worker.get(row['id'])
            except WorkerError as exc:
                if exc.status != 404: raise
                job = json.loads(row['job'])
                if job.get('task_kind') == 'revision':
                    material = self.versions.revision_materials(row['user_id'],row['id'])
                    snapshot = self.worker.put_revision(job,material)
                else:
                    snapshot = self.worker.put(job)
        except WorkerError as exc:
            if exc.status in (409, 422, 429):
                self.update(row['id'], dispatch='rejected', rejection_status=exc.status, error_code=exc.code, error=str(exc))
                if json.loads(row['job']).get('task_kind') == 'revision':
                    self.versions.update_revision_job(row['user_id'],row['id'],'rejected')
            else:
                self.update(row['id'], dispatch='ambiguous', error_code=exc.code, error=str(exc))
            raise
        self.update(row['id'], dispatch='accepted', error_code=None, error=None)
        if self.is_revision(row):
            current = self.versions.get_revision_job(row['user_id'],row['id'])
            if current['status'] == 'cancel_requested':
                return self.recover_revision_cancel(row)
        return self.sync_revision(row,snapshot)

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
        if job.get('task_kind') == 'revision':
            self._publish_revision(row,job,result)
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
        if job.get('task_kind') == 'revision':
            self.versions.update_revision_job(row['user_id'],task_id,'succeeded')
        self.notify(task_id, row['email'], job['topic'])
        self.update(task_id, error_code=None, error=None)

    def _publish_revision(self, row, job, result):
        from .pi_revision_validation import validate_revision_candidate
        from .pi_revision_contracts import validate_revision_materials
        from .pi_citations import validate_manifest, TraceUnavailable
        binding = self.versions.get_revision_job(row['user_id'],row['id'])
        if not binding or binding['job'] != row['job']:
            raise WorkerError(409,'publication_conflict','Missing immutable revision binding')
        expected = {k:job[k] for k in ('intent_id','parent_version_id','root_report_id','base_sha256',
                                      'section_id','section_sha256','input_sha256')}
        provenance = result['provenance']
        if (provenance.get('revision') != expected
                or provenance.get('material_policy') != 'recorded_evidence_only'
                or provenance.get('retrieval_performed') is not False
                or provenance.get('full_text_restored') is not False):
            raise WorkerError(409,'publication_conflict','Revision result provenance does not match fixed input')
        fixed = validate_revision_materials(job,self.versions.revision_materials(row['user_id'],row['id']))
        try:
            validate_manifest(result['report_markdown'],result['artifacts'])
        except TraceUnavailable as exc:
            raise WorkerError(409,'publication_conflict','Revision result manifest is invalid') from exc
        artifacts, output = {}, None
        for name in ('papers','evidence','claims','references','revision_output'):
            item = next((i for i in result['artifacts'] if i['artifact_id'] == name),None)
            if item is None or type(item['size']) is not int or not 0 <= item['size'] <= 16*1024*1024:
                raise WorkerError(409,'publication_conflict','Revision result artifact is missing or too large')
            raw,_ = self.worker.artifact(row['id'],name,max_bytes=item['size'])
            if (not isinstance(raw,bytes) or len(raw) != item['size']
                    or hashlib.sha256(raw).hexdigest() != item['sha256']):
                raise WorkerError(409,'publication_conflict','Revision result artifact does not match its manifest')
            if name == 'revision_output': output = raw
            else: artifacts[name] = raw
        try:
            validate_revision_candidate(fixed['report'],fixed['items'],fixed['artifacts'],
                                        job['section_id'],job['base_sha256'],result['report_markdown'],
                                        result['artifacts'],artifacts,output=output)
        except ValueError as exc:
            raise WorkerError(409,'publication_conflict','Revision candidate did not pass independent validation') from exc
        # Reserve lineage before making the report readable by its task ID.
        # A crash in either subsequent SQL call replays this same version.
        candidate = self.versions.append_candidate(row['user_id'],job['parent_version_id'],job['base_sha256'],
                    result['report_markdown'],'revision:'+row['id'],source_report_id=row['id'])
        self.versions.update_revision_job(row['user_id'],row['id'],'publishing',
                    candidate_version_id=candidate['version_id'],result_digest=result['result_digest'])

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
        if self.versions is not None:
            for binding in self.versions.active_revision_jobs():
                try:
                    row = self._revision_index(binding)
                    if row['dispatch'] == 'rejected':
                        self.versions.update_revision_job(row['user_id'],row['id'],'rejected')
                    elif binding['status'] == 'cancel_requested':
                        self.recover_revision_cancel(row)
                    elif row['dispatch'] == 'accepted':
                        self.sync_revision(row,self.worker.get(row['id']))
                except Exception: log.exception('Pi revision binding recovery failed for %s',binding['task_id'])
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
