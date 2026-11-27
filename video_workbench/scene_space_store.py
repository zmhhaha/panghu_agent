"""Owner-scoped scene plans and transactional cumulative usage.

Only service code writes this store. Clients cannot replace a ledger or its
history. Reservations prevent concurrent admissions from spending the same
remaining capacity; settlements preserve failures and measured overruns.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .picture_learning_store import identifier, owner_id, _plain
from .scene_space_budget import (
    bind_revision, create_usage, prepare_retry, record_usage, require_export_budget,
    usage_status, validate_usage, validate_reservation, MODEL_PHASES,
)
from .scene_space_plan import freeze_plan, revise_plan, technical_retry


def _require(ok,code,status=409):
    if not ok:raise ServiceError(code,status)


def _json(value):
    return canonical_bytes(value).decode('utf-8')


def _active_model(reservations):
    return any(row['status']=='active' and (row['phase'] in MODEL_PHASES
        or row['model_calls']>0 or row['model_tokens']>0) for row in reservations)


class SceneSpaceStore:
    def __init__(self,root,*,clock=time.time):
        self.root=_plain(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'scene-space.sqlite3'
        self.clock=clock
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS scene_tasks ('
                'owner TEXT NOT NULL,task_id TEXT NOT NULL,document TEXT NOT NULL,'
                'PRIMARY KEY(owner,task_id))')
            db.execute('CREATE TABLE IF NOT EXISTS scene_plans ('
                'owner TEXT NOT NULL,task_id TEXT NOT NULL,plan_sha256 TEXT NOT NULL,'
                'document TEXT NOT NULL,PRIMARY KEY(owner,task_id,plan_sha256))')

    @contextmanager
    def _db(self):
        _plain(self.path)
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=15000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    @staticmethod
    def _load(db,owner,task_id):
        owner_id(owner);identifier(task_id)
        row=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',
            (owner,task_id)).fetchone()
        _require(row is not None,'scene_space_task_not_found',404)
        record=json.loads(row['document'])
        _require(record['owner']==owner and record['task_id']==task_id,
            'scene_space_record_changed')
        validate_usage(record['usage'])
        _require(record['frozen']['plan_sha256']==record['usage']['plan_sha256'],
            'scene_space_record_changed')
        _require(('continuation_usage' in record)==bool(record.get('review_continuation')),
            'scene_review_continuation_usage_changed')
        if record.get('review_continuation'):
            from .scene_review_continuation import validate_grant, new_usage
            grant=validate_grant(record['review_continuation'])
            fresh=new_usage(record['frozen'],grant_sha256=grant['sha256'])
            current=validate_usage(record['continuation_usage'])
            _require(current['limits']==fresh['limits'] and current['baseline']==fresh['baseline']
                and current['lineage_id']==fresh['lineage_id'] and not current['history']
                and current['plan_sha256']==record['frozen']['plan_sha256'],
                'scene_review_continuation_usage_changed')
            parent=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',
                (owner,grant['parent_run_id'])).fetchone()
            _require(parent is not None and digest(json.loads(parent[0]))==grant['parent_task_sha256'],
                'scene_review_continuation_parent_changed')
            previous=json.loads(parent[0])
            _require(record['usage']==previous['usage'] and record['frozen']==previous['frozen']
                and record['legacy_reservations']==previous['reservations'],
                'scene_review_continuation_legacy_changed')
        if record.get('source_import'):
            from .scene_source_import import validate_grant, new_usage, parent_ledgers
            grant=validate_grant(record['source_import'])
            fresh=new_usage(record['frozen'],grant['sha256'])
            _require(not record.get('review_continuation') and record['usage']['limits']==fresh['limits']
                and record['usage']['baseline']==fresh['baseline']
                and record['usage']['lineage_id']==fresh['lineage_id'] and not record['usage']['history'],
                'scene_source_import_usage_changed')
            parent=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',
                (owner,grant['parent_run_id'])).fetchone()
            _require(parent is not None and digest(json.loads(parent[0]))==grant['parent_task_sha256'],
                'scene_source_import_parent_changed')
            previous=json.loads(parent[0]);ledgers=parent_ledgers(previous)
            _require(record['frozen']==previous['frozen'] and record['source_import_parent_ledgers']==ledgers
                and digest(ledgers)==grant['parent_usage_sha256'], 'scene_source_import_parent_changed')
        if record.get('source_import_resume'):
            from .scene_source_import_resume import validate_resume_certificate, require_carried_prefix
            certificate=validate_resume_certificate(record['source_import_resume'],record['source_import'])
            _require(certificate['owner']==owner and certificate['parent_run_id']!=task_id,
                'scene_source_import_resume_parent_changed')
            parent=SceneSpaceStore._load(db,owner,certificate['parent_run_id'])
            require_carried_prefix(record,parent,certificate)
        return record

    def _put(self,db,record):
        record['revision']+=1
        record['updated_at']=self.clock()
        db.execute('UPDATE scene_tasks SET document=? WHERE owner=? AND task_id=?',
            (_json(record),record['owner'],record['task_id']))

    @staticmethod
    def _view(record):
        value=deepcopy(record)
        value['usage_status']=usage_status(value['usage'])
        if value.get('review_continuation'):
            from .scene_review_continuation import cumulative_status, execution_status
            value['usage_status']=cumulative_status(value)
            value['continuation_usage_status']=execution_status(value)
        if value.get('source_import'):
            from .scene_source_import import cumulative_status
            value['source_import_execution_status']=deepcopy(value['usage_status'])
            value['usage_status']=cumulative_status(value)
        if _active_model(value['reservations']):
            value['usage_status']['can_start_export']=False
            if 'continuation_usage_status' in value:
                value['continuation_usage_status']['can_start_export']=False
            if 'source_import_execution_status' in value:
                value['source_import_execution_status']['can_start_export']=False
        return value

    def freeze(self,owner,task_id,plan):
        owner_id(owner);identifier(task_id)
        frozen=freeze_plan(plan)
        with self._db() as db:
            prior=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',
                (owner,task_id)).fetchone()
            if prior:
                record=self._load(db,owner,task_id)
                _require(record['usage']['root_plan_sha256']==frozen['plan_sha256'],
                    'scene_space_task_plan_conflict')
                return self._view(record)
            record=dict(schema='video.scene-space-task.v1',owner=owner,task_id=task_id,
                frozen=frozen,usage=create_usage(frozen,lineage_id='task-'+digest([owner,task_id])),
                revision=1,created_at=self.clock(),updated_at=self.clock(),reservations=[],previews=[])
            db.execute('INSERT INTO scene_tasks VALUES (?,?,?)',(owner,task_id,_json(record)))
            db.execute('INSERT INTO scene_plans VALUES (?,?,?,?)',
                (owner,task_id,frozen['plan_sha256'],_json(frozen)))
            return self._view(record)

    def get(self,owner,task_id):
        with self._db() as db:return self._view(self._load(db,owner,task_id))

    def recover_source(self,owner,task_id,source_task_id,*,source_task_sha256,author_operation_id,
                       measured_usage,recovery):
        """Append a linked task; only its original unknown author cost is supplemented.

        The source task, unknown event, frozen plan and every other expense stay
        immutable. This is not a fresh budget or permission to regenerate.
        """
        owner_id(owner);identifier(task_id);identifier(source_task_id)
        with self._db() as db:
            prior=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',
                (owner,task_id)).fetchone()
            if prior:
                record=self._load(db,owner,task_id)
                _require(record.get('source_recovery')==recovery,'scene_space_source_recovery_conflict')
                return self._view(record)
            source=self._load(db,owner,source_task_id)
            _require(digest(source)==source_task_sha256
                and not any(r['status']=='active' for r in source['reservations']),
                'scene_space_source_recovery_changed')
            usage=deepcopy(source['usage']);events=usage['events']
            original=[event for event in events if event['id']==author_operation_id]
            _require(len(original)==1 and original[0]['phase']=='generation'
                and original[0]['outcome']=='completed' and original[0]['model_tokens'] is None
                and measured_usage['model_calls']>=original[0]['model_calls'],
                'scene_space_source_recovery_usage_invalid')
            original[0].update(measured_usage)
            usage['lineage_id']='task-'+digest([owner,task_id])
            usage['sha256']=digest({key:value for key,value in usage.items() if key!='sha256'})
            usage=validate_usage(usage)
            _require(usage_status(usage)['status']=='within_budget','scene_space_source_recovery_budget_unavailable')
            record=dict(schema='video.scene-space-task.v1',owner=owner,task_id=task_id,
                frozen=deepcopy(source['frozen']),usage=usage,revision=1,created_at=self.clock(),
                updated_at=self.clock(),reservations=deepcopy(source['reservations']),previews=[],
                source_recovery=deepcopy(recovery))
            db.execute('INSERT INTO scene_tasks VALUES (?,?,?)',(owner,task_id,_json(record)))
            # Source-only recovery disallows plan revisions; the exact frozen
            # root/current plan is copied, never rebuilt from today's defaults.
            db.execute('INSERT INTO scene_plans VALUES (?,?,?,?)',
                (owner,task_id,record['frozen']['plan_sha256'],_json(record['frozen'])))
            return self._view(record)

    def plan(self,owner,task_id,plan_sha256):
        with self._db() as db:
            self._load(db,owner,task_id)
            row=db.execute('SELECT document FROM scene_plans WHERE owner=? AND task_id=? AND plan_sha256=?',
                (owner,task_id,plan_sha256)).fetchone()
            _require(row is not None,'scene_space_plan_not_found',404)
            return technical_retry(json.loads(row['document']),plan_sha256)

    def import_source(self,owner,task_id,*,grant):
        """Install one fresh execution ledger beside exact immutable parent references."""
        from .scene_source_import import validate_grant, new_usage, parent_ledgers
        grant=validate_grant(grant);owner_id(owner);identifier(task_id)
        with self._db() as db:
            previous=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',
                (owner,task_id)).fetchone()
            if previous:
                record=self._load(db,owner,task_id)
                _require(record.get('source_import')==grant,'scene_source_import_conflict')
                return self._view(record)
            parent=self._load(db,owner,grant['parent_run_id']);ledgers=parent_ledgers(parent)
            _require(not parent.get('source_import') and digest(parent)==grant['parent_task_sha256']
                and digest(ledgers)==grant['parent_usage_sha256']
                and parent['frozen']['plan_sha256']==grant['plan_sha256']
                and not any(r['status']=='active' for r in parent['reservations']),
                'scene_source_import_parent_changed')
            used=db.execute("SELECT 1 FROM scene_tasks WHERE owner=? AND "
                "json_extract(document,'$.source_import.author_job_id')=? LIMIT 1",
                (owner,grant['author_job_id'])).fetchone()
            _require(used is None,'scene_source_import_already_exists')
            record=dict(schema='video.scene-space-task.v1',owner=owner,task_id=task_id,
                frozen=deepcopy(parent['frozen']),usage=new_usage(parent['frozen'],grant['sha256']),
                source_import=deepcopy(grant),source_import_parent_ledgers=ledgers,
                revision=1,created_at=self.clock(),updated_at=self.clock(),reservations=[],previews=[])
            db.execute('INSERT INTO scene_tasks VALUES (?,?,?)',(owner,task_id,_json(record)))
            db.execute('INSERT INTO scene_plans VALUES (?,?,?,?)',
                (owner,task_id,record['frozen']['plan_sha256'],_json(record['frozen'])))
            return self._view(record)

    def resume_import_review(self,owner,task_id,*,certificate,grant):
        """Carry the entire original grant ledger; never reset or mint a budget."""
        from .scene_source_import_resume import validate_resume_certificate, unused_execution
        certificate=validate_resume_certificate(certificate,grant);owner_id(owner);identifier(task_id)
        _require(certificate['owner']==owner and certificate['parent_run_id']!=task_id,
            'scene_source_import_resume_parent_changed')
        with self._db() as db:
            prior=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',(owner,task_id)).fetchone()
            if prior:
                record=self._load(db,owner,task_id)
                _require(record.get('source_import_resume')==certificate,'scene_source_import_resume_conflict')
                return self._view(record)
            parent=self._load(db,owner,certificate['parent_run_id']);unused_execution(parent)
            _require(not parent.get('source_import_resume') and parent.get('source_import')==grant
                and digest(parent)==certificate['parent_task_sha256']
                and parent['usage']['sha256']==certificate['parent_usage_sha256'],
                'scene_source_import_resume_parent_changed')
            used=db.execute("SELECT 1 FROM scene_tasks WHERE owner=? AND "
                "json_extract(document,'$.source_import_resume.source_import_grant_sha256')=? LIMIT 1",
                (owner,grant['sha256'])).fetchone()
            _require(used is None,'scene_source_import_resume_already_exists')
            record=deepcopy(parent)
            record.update(task_id=task_id,source_import_resume=certificate,revision=1,
                created_at=self.clock(),updated_at=self.clock())
            db.execute('INSERT INTO scene_tasks VALUES (?,?,?)',(owner,task_id,_json(record)))
            db.execute('INSERT INTO scene_plans VALUES (?,?,?,?)',
                (owner,task_id,record['frozen']['plan_sha256'],_json(record['frozen'])))
            return self._view(record)

    def continue_review(self,owner,task_id,*,grant):
        """Install one grant without changing any legacy event or frozen plan."""
        from .scene_review_continuation import validate_grant, new_usage
        grant=validate_grant(grant)
        owner_id(owner);identifier(task_id)
        with self._db() as db:
            prior=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',
                (owner,task_id)).fetchone()
            if prior:
                record=self._load(db,owner,task_id)
                _require(record.get('review_continuation')==grant,'scene_review_continuation_conflict')
                return self._view(record)
            parent=self._load(db,owner,grant['parent_run_id'])
            _require(not parent.get('review_continuation')
                and digest(parent)==grant['parent_task_sha256']
                and parent['usage']['sha256']==grant['legacy_usage_sha256']
                and not any(r['status']=='active' for r in parent['reservations']),
                'scene_review_continuation_parent_changed')
            usage=deepcopy(parent['usage'])
            record=dict(schema='video.scene-space-task.v1',owner=owner,task_id=task_id,
                frozen=deepcopy(parent['frozen']),usage=usage,
                continuation_usage=new_usage(parent['frozen'],grant_sha256=grant['sha256']),
                review_continuation=deepcopy(grant),legacy_reservations=deepcopy(parent['reservations']),
                revision=1,created_at=self.clock(),updated_at=self.clock(),reservations=[],
                previews=deepcopy(parent['previews']))
            db.execute('INSERT INTO scene_tasks VALUES (?,?,?)',(owner,task_id,_json(record)))
            db.execute('INSERT INTO scene_plans VALUES (?,?,?,?)',
                (owner,task_id,record['frozen']['plan_sha256'],_json(record['frozen'])))
            return self._view(record)

    def reserve(self,owner,task_id,operation_id,*,phase,model_calls=0,model_tokens=0,render_seconds=0):
        identifier(operation_id)
        hold=validate_reservation(id=operation_id,phase=phase,model_calls=model_calls,
            model_tokens=model_tokens,render_seconds=render_seconds)
        with self._db() as db:
            record=self._load(db,owner,task_id)
            if record.get('source_import_resume'):
                _require(phase in {'review','export'},'scene_source_import_resume_operation_not_allowed')
            if record.get('review_continuation'):
                _require(phase in {'review','export'},'scene_review_continuation_operation_not_allowed')
                _require(not any(row['phase']==phase and row['id']!=operation_id
                    for row in record['reservations']),'scene_review_continuation_operation_limit')
            if record.get('source_import'):
                _require(phase in {'preview','review','export'},'scene_source_import_operation_not_allowed')
                if phase in {'review','export'}:
                    _require(not any(row['phase']==phase and row['id']!=operation_id
                        for row in record['reservations']),'scene_source_import_operation_limit')
            reservation=dict(hold,status='active')
            for previous in record['reservations']:
                if previous['id']==operation_id:
                    _require({k:v for k,v in previous.items() if k!='status'}
                        =={k:v for k,v in reservation.items() if k!='status'},
                        'scene_space_reservation_conflict')
                    _require(previous['status']=='active','scene_space_operation_already_settled')
                    return self._view(record)
            # Keep both directions atomic: an export cannot race a model hold,
            # and model work cannot start while an admitted export is running.
            if phase=='export':
                _require(not _active_model(record['reservations']),'scene_space_operations_still_active')
            elif phase in MODEL_PHASES or model_calls or model_tokens:
                _require(not any(row['status']=='active' and row['phase']=='export'
                    for row in record['reservations']),'scene_space_operations_still_active')
            from .scene_review_continuation import execution_status
            status=execution_status(record)
            diagnostic_preview=(phase=='preview' and model_calls==0 and model_tokens==0
                and status['status']=='usage_unknown' and status['used']['render_seconds'] is not None)
            _require(status['status']=='within_budget' or diagnostic_preview,'scene_space_budget_unavailable')
            _require(status['available_event_slots']>sum(r['status']=='active' for r in record['reservations']),
                'scene_space_usage_event_limit')
            spending=record.get('continuation_usage',record['usage'])
            _require(not any(e['id']==operation_id for e in spending['events']),
                'scene_space_operation_already_settled')
            for key,amount in [('model_calls',model_calls),('model_tokens',model_tokens),
                               ('render_seconds',render_seconds)]:
                held=sum(r[key] for r in record['reservations'] if r['status']=='active')
                if key=='model_tokens' and status['remaining'][key] is None:
                    _require(amount==0,'scene_space_budget_unavailable')
                    continue
                _require(amount+held<=status['remaining'][key], 'scene_space_budget_reservation_exceeded')
            _require(len(record['reservations'])<1024,'scene_space_usage_event_limit')
            record['reservations'].append(reservation)
            self._put(db,record)
            return self._view(record)

    def record_usage(self,owner,task_id,**event):
        with self._db() as db:
            record=self._load(db,owner,task_id)
            key='continuation_usage' if record.get('review_continuation') else 'usage'
            if record.get('source_import_resume'):
                _require(event['phase'] in {'review','export'},'scene_source_import_resume_operation_not_allowed')
            if record.get('source_import'):
                _require(event['phase'] in {'preview','review','export'}
                    and any(r['id']==event['id'] and r['phase']==event['phase']
                        for r in record['reservations']), 'scene_source_import_reservation_missing')
            if key=='continuation_usage':
                _require(event['phase'] in {'review','export'},'scene_review_continuation_operation_not_allowed')
                _require(any(r['id']==event['id'] and r['phase']==event['phase']
                    for r in record['reservations']),'scene_review_continuation_reservation_missing')
            updated=record_usage(record[key],**event)
            if updated==record[key]:return self._view(record)
            record[key]=updated
            for reservation in record['reservations']:
                if reservation['id']==event['id']:
                    _require(reservation['phase']==event['phase'],'scene_space_reservation_conflict')
                    reservation['status']='settled'
            self._put(db,record)
            return self._view(record)

    def require_export(self,owner,task_id,*,reserve_seconds=0):
        with self._db() as db:
            record=self._load(db,owner,task_id)
            status=require_export_budget(record.get('continuation_usage',record['usage']),reserve_seconds=reserve_seconds)
            _require(not _active_model(record['reservations']),'scene_space_operations_still_active')
            held=sum(r['render_seconds'] for r in record['reservations'] if r['status']=='active')
            _require(held+reserve_seconds<=status['remaining']['render_seconds'],
                'scene_space_budget_reservation_exceeded')
            return status

    def retry(self,owner,task_id,expected_plan_sha256):
        with self._db() as db:
            record=self._load(db,owner,task_id)
            _require(not record.get('review_continuation'),'scene_review_continuation_retry_forbidden')
            _require(not record.get('source_import'),'scene_source_import_retry_forbidden')
            _require(not any(r['status']=='active' for r in record['reservations']),
                'scene_space_operations_still_active')
            prepared=prepare_retry(record['usage'],record['frozen'],expected_plan_sha256)
            return dict(**prepared,task_id=task_id,revision=record['revision'])

    def revise(self,owner,task_id,replacement,*,reason,expected_plan_sha256):
        with self._db() as db:
            record=self._load(db,owner,task_id)
            _require(not record.get('review_continuation'),'scene_review_continuation_revision_forbidden')
            _require(not record.get('source_import'),'scene_source_import_revision_forbidden')
            _require(record['frozen']['plan_sha256']==expected_plan_sha256,'scene_space_plan_revision_conflict')
            _require(not any(r['status']=='active' for r in record['reservations']),
                'scene_space_operations_still_active')
            revision=revise_plan(record['frozen'],replacement,reason=reason)
            usage=bind_revision(record['usage'],record['frozen'],revision['frozen'],reason=reason)
            record.update(frozen=revision['frozen'],usage=usage)
            for preview in record['previews']:preview['current']=False
            db.execute('INSERT INTO scene_plans VALUES (?,?,?,?)',
                (owner,task_id,revision['frozen']['plan_sha256'],_json(revision['frozen'])))
            self._put(db,record)
            return dict(self._view(record),change=revision['revision'])

    def record_preview(self,owner,task_id,preview_id,report):
        identifier(preview_id)
        _require(type(report) is dict and report.get('status') in {'valid','invalid'}
            and len(canonical_bytes(report))<=4*1024*1024,'scene_space_preview_report_invalid',422)
        with self._db() as db:
            record=self._load(db,owner,task_id)
            _require(report.get('plan_sha256')==record['frozen']['plan_sha256'],
                'scene_space_preview_plan_changed')
            item=dict(id=preview_id,plan_sha256=report['plan_sha256'],report=deepcopy(report),
                sha256=digest(report),current=True)
            for previous in record['previews']:
                if previous['id']==preview_id:
                    _require(previous['sha256']==item['sha256'],'scene_space_preview_identity_conflict')
                    return self._view(record)
            _require(len(record['previews'])<64,'scene_space_preview_count_exceeded')
            record['previews'].append(item)
            self._put(db,record)
            return self._view(record)
