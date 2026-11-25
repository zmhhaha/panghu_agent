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
        if _active_model(value['reservations']):
            value['usage_status']['can_start_export']=False
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

    def plan(self,owner,task_id,plan_sha256):
        with self._db() as db:
            self._load(db,owner,task_id)
            row=db.execute('SELECT document FROM scene_plans WHERE owner=? AND task_id=? AND plan_sha256=?',
                (owner,task_id,plan_sha256)).fetchone()
            _require(row is not None,'scene_space_plan_not_found',404)
            return technical_retry(json.loads(row['document']),plan_sha256)

    def reserve(self,owner,task_id,operation_id,*,phase,model_calls=0,model_tokens=0,render_seconds=0):
        identifier(operation_id)
        hold=validate_reservation(id=operation_id,phase=phase,model_calls=model_calls,
            model_tokens=model_tokens,render_seconds=render_seconds)
        with self._db() as db:
            record=self._load(db,owner,task_id)
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
            status=usage_status(record['usage'])
            diagnostic_preview=(phase=='preview' and model_calls==0 and model_tokens==0
                and status['status']=='usage_unknown' and status['used']['render_seconds'] is not None)
            _require(status['status']=='within_budget' or diagnostic_preview,'scene_space_budget_unavailable')
            _require(status['available_event_slots']>sum(r['status']=='active' for r in record['reservations']),
                'scene_space_usage_event_limit')
            _require(not any(e['id']==operation_id for e in record['usage']['events']),
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
            updated=record_usage(record['usage'],**event)
            if updated==record['usage']:return self._view(record)
            record['usage']=updated
            for reservation in record['reservations']:
                if reservation['id']==event['id']:
                    _require(reservation['phase']==event['phase'],'scene_space_reservation_conflict')
                    reservation['status']='settled'
            self._put(db,record)
            return self._view(record)

    def require_export(self,owner,task_id,*,reserve_seconds=0):
        with self._db() as db:
            record=self._load(db,owner,task_id)
            status=require_export_budget(record['usage'],reserve_seconds=reserve_seconds)
            _require(not _active_model(record['reservations']),'scene_space_operations_still_active')
            held=sum(r['render_seconds'] for r in record['reservations'] if r['status']=='active')
            _require(held+reserve_seconds<=status['remaining']['render_seconds'],
                'scene_space_budget_reservation_exceeded')
            return status

    def retry(self,owner,task_id,expected_plan_sha256):
        with self._db() as db:
            record=self._load(db,owner,task_id)
            _require(not any(r['status']=='active' for r in record['reservations']),
                'scene_space_operations_still_active')
            prepared=prepare_retry(record['usage'],record['frozen'],expected_plan_sha256)
            return dict(**prepared,task_id=task_id,revision=record['revision'])

    def revise(self,owner,task_id,replacement,*,reason,expected_plan_sha256):
        with self._db() as db:
            record=self._load(db,owner,task_id)
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
