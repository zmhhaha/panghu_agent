"""Authenticated private queue for the versioned scene-space renderer.

GET is read-only. A durable claim is written before invoking the renderer;
uncertain work is reconciled by the same ID and is never automatically rerun.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import base64
import binascii
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote, unquote

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .animation_creation_http import CreationAPIClient, CreationTransportError
from .picture_learning_store import owner_id, identifier, _plain
from .scene_space_plan import freeze_plan, technical_retry
from .scene_space_capture import validate_sample_capture, validate_frame_capture

PREFIX='/api/scene-space-renders'
MAX_BODY=24*1024*1024
MAX_PREVIEW_FRAMES=3
MAX_PREVIEW_FRAME_BYTES=4*1024*1024
PREVIEW_SCHEMA='video.scene-space-preview-frames.v1'
CAPTURE_PREVIEW_SCHEMA='video.scene-space-preview-frames.v2'


def _require(ok,code='scene_space_render_request_invalid',status=422):
    if not ok:raise ServiceError(code,status)


def _frame_limit(value):
    _require(type(value) is int and 1<=value<=MAX_PREVIEW_FRAMES,
        'scene_space_preview_frame_limit_invalid')
    return value


def _frame_response_limit(max_frames):
    return _frame_limit(max_frames)*((MAX_PREVIEW_FRAME_BYTES+2)//3*4)+65536


def _sample_capture_metadata(row,result):
    """Bind new samples to their stored request, never today's capabilities."""
    request=row.get('request')
    constraints=(request.get('document',{}).get('constraints',{})
        if type(request) is dict else {})
    requested=type(constraints) is dict and 'sample_capture' in constraints
    fields={'sample_capture','sample_frame_range'}
    if not requested and not fields.intersection(result):return {}
    code='scene_space_preview_capture_metadata_changed'
    _require(fields<=set(result),code,409)
    policy=validate_sample_capture(result['sample_capture'])
    frame_range=result['sample_frame_range']
    _require(type(frame_range) is dict and set(frame_range)=={'start_frame','end_frame'}
        and all(type(frame_range[key]) is int for key in frame_range)
        and 0<=frame_range['start_frame']<frame_range['end_frame']<=7200
        and frame_range['end_frame']-frame_range['start_frame']==result.get('frame_count'),code,409)
    if request is not None:
        _require(requested and 'duration_seconds' in constraints
            and policy==validate_sample_capture(constraints['sample_capture']),code,409)
        frozen=request.get('scene_space')
        _require(type(frozen) is dict,code,409)
        plan=frozen.get('plan') if frozen.get('schema')=='video.scene-space-frozen-plan.v1' else frozen
        _require(type(plan) is dict and type(plan.get('duration_frames')) is int,code,409)
        expected_range=request.get('frame_range') or dict(start_frame=0,end_frame=plan['duration_frames'])
        _require(frame_range==expected_range,code,409)
    return dict(sample_capture=policy,sample_frame_range=deepcopy(frame_range))


def _preview_metadata(row,operation_id):
    _require(type(row) is dict and row.get('id')==operation_id
        and row.get('status')=='completed' and row.get('stop_proven') is True,
        'scene_space_preview_frames_unavailable',409)
    result=row.get('result')
    _require(type(result) is dict and result.get('mime')=='video/mp4'
        and result.get('fps')==24 and type(result.get('frame_count')) is int
        and 1<=result['frame_count']<=7200,'scene_space_preview_frames_unavailable',409)
    viewport=dict(width=result.get('width'),height=result.get('height'))
    _require(all(type(viewport[key]) is int and 1<=viewport[key]<=limit
        for key,limit in (('width',1280),('height',720))),
        'scene_space_preview_metadata_changed',409)
    duration=result['frame_count']/24
    _require(type(result.get('duration_seconds')) in (int,float)
        and math.isfinite(result['duration_seconds']) and result['duration_seconds']==duration,
        'scene_space_preview_metadata_changed',409)
    identities={key:row.get(key) for key in ('request_sha256','plan_sha256','source_sha256')}
    identities['snapshot_sha256']=result.get('snapshot_sha256')
    _require(all(type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
        for value in identities.values()) and result.get('plan_sha256')==identities['plan_sha256']
        and result.get('source_sha256')==identities['source_sha256'],
        'scene_space_preview_metadata_changed',409)
    capture=_sample_capture_metadata(row,result)
    return dict(schema=CAPTURE_PREVIEW_SCHEMA if capture else PREVIEW_SCHEMA,
        operation_id=operation_id,**identities,viewport=viewport,duration_seconds=duration,**capture)


def _preview_frame(value,metadata,*,encoded=False):
    code='scene_space_preview_frame_invalid'
    expected={'timestamp_seconds','mime','bytes','sha256','content_base64'} if encoded else {'timestamp_seconds','mime','raw'}
    captured=metadata['schema']==CAPTURE_PREVIEW_SCHEMA
    if captured:expected=expected|{'capture'}
    _require(type(value) is dict and set(value)==expected,code,409)
    timestamp=value['timestamp_seconds'];mime=value['mime']
    _require(type(timestamp) in (int,float) and math.isfinite(timestamp)
        and 0<=timestamp<metadata['duration_seconds'] and mime in ('image/png','image/jpeg'),code,409)
    if encoded:
        length=value['bytes'];content=value['content_base64']
        _require(type(length) is int and 0<length<=MAX_PREVIEW_FRAME_BYTES
            and type(content) is str and len(content)==4*((length+2)//3),code,409)
        try:raw=base64.b64decode(content,validate=True)
        except (ValueError,binascii.Error):raise ServiceError(code,409) from None
        _require(len(raw)==length and type(value['sha256']) is str
            and hashlib.sha256(raw).hexdigest()==value['sha256'],'scene_space_preview_frame_changed',409)
    else:raw=value['raw']
    _require(type(raw) is bytes and 0<len(raw)<=MAX_PREVIEW_FRAME_BYTES,code,409)
    if captured:
        _require(mime=='image/png',code,409)
        validate_frame_capture(value['capture'],frame_range=metadata['sample_frame_range'],
            raw=raw,timestamp_seconds=timestamp)
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as image:
            _require(image.format==('PNG' if mime=='image/png' else 'JPEG')
                and image.size==(metadata['viewport']['width'],metadata['viewport']['height']),code,409)
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:image.load()
    except (OSError,ValueError,SyntaxError,Image.DecompressionBombError):
        raise ServiceError(code,409) from None
    frame=dict(timestamp_seconds=timestamp,mime=mime,raw=raw)
    if captured:frame['capture']=deepcopy(value['capture'])
    return frame


def _preview_frames(frames,metadata,max_frames,*,encoded=False):
    _require(type(frames) is list and 1<=len(frames)<=max_frames,
        'scene_space_preview_frame_count_invalid',409)
    checked=[_preview_frame(frame,metadata,encoded=encoded) for frame in frames]
    if metadata['schema']==CAPTURE_PREVIEW_SCHEMA:
        frame_range=metadata['sample_frame_range'];count=frame_range['end_frame']-frame_range['start_frame']
        selected=sorted({0,count//2,count-1})[:max_frames]
        _require([frame['capture']['relative_frame'] for frame in checked]==selected,
            'scene_space_preview_frame_selection_changed',409)
        _require(sum(len(frame['raw']) for frame in checked)<=metadata['sample_capture']['max_total_sample_bytes'],
            'scene_space_preview_frames_too_large',413)
    return checked


class SceneSpaceRenderClient(CreationAPIClient):
    def _request(self,*args,**kwargs):
        try:return super()._request(*args,**kwargs)
        except CreationTransportError as error:
            raise ServiceError(error.code,error.status) from None

    def start(self,owner,request,request_key):
        identifier(request_key)
        return self._request(owner,'POST',PREFIX,dict(request,request_id=request_key))

    def lookup(self,owner,request_key):
        identifier(request_key)
        try:return self._request(owner,'GET',PREFIX+'/requests/'+quote(request_key,safe=''))
        except ServiceError as error:
            if error.status==404:return None
            raise

    def get(self,owner,operation_id):
        identifier(operation_id)
        return self._request(owner,'GET',PREFIX+'/'+quote(operation_id,safe=''))

    def cancel(self,owner,operation_id):
        identifier(operation_id)
        return self._request(owner,'POST',PREFIX+'/'+quote(operation_id,safe='')+'/cancel',{})

    def video(self,owner,operation_id):
        identifier(operation_id)
        return self._request(owner,'GET',PREFIX+'/'+quote(operation_id,safe='')+'/video',artifact=True)

    def frames(self,owner,operation_id,max_frames=3):
        identifier(operation_id);_frame_limit(max_frames)
        metadata=_preview_metadata(self.get(owner,operation_id),operation_id)
        raw,mime=self._request(owner,'GET',PREFIX+'/'+quote(operation_id,safe='')+'/frames/'+str(max_frames),
            artifact=True,response_limit=_frame_response_limit(max_frames))
        _require(mime=='application/json','scene_space_preview_frame_invalid',409)
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):raise ServiceError('scene_space_preview_frame_invalid',409) from None
        _require(type(value) is dict and set(value)==set(metadata)|{'frames'}
            and all(value.get(key)==item for key,item in metadata.items()),
            'scene_space_preview_metadata_changed',409)
        return _preview_frames(value['frames'],metadata,max_frames,encoded=True)

    def capabilities(self,owner):
        return self._request(owner,'GET',PREFIX+'/capabilities')


class SceneSpaceRenderService:
    def __init__(self,root,renderer,*,clock=time.time,monotonic=time.monotonic):
        self.root=_plain(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'queue.sqlite3';self.renderer=renderer
        self.clock,self.monotonic=clock,monotonic
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations(owner TEXT NOT NULL,id TEXT NOT NULL,'
                'request_id TEXT NOT NULL,request_sha256 TEXT NOT NULL,document TEXT NOT NULL,'
                'PRIMARY KEY(owner,id),UNIQUE(owner,request_id))')

    @contextmanager
    def _db(self):
        _plain(self.path)
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    @staticmethod
    def _load(db,owner,operation_id):
        owner_id(owner);identifier(operation_id)
        row=db.execute('SELECT document FROM operations WHERE owner=? AND id=?',
            (owner,operation_id)).fetchone()
        _require(row is not None,'scene_space_render_not_found',404)
        return json.loads(row['document'])

    @staticmethod
    def _public(row):
        result={key:deepcopy(row[key]) for key in ('id','status','stop_proven','request_sha256',
            'plan_sha256','source_sha256','created_at','updated_at','result','usage','error')}
        if row.get('stop_verification_error') is not None:
            result['stop_verification_error']=row['stop_verification_error']
        return result

    def _put(self,db,row):
        row['updated_at']=self.clock()
        db.execute('UPDATE operations SET document=? WHERE owner=? AND id=?',
            (canonical_bytes(row).decode(),row['owner'],row['id']))

    def capabilities(self,owner):
        owner_id(owner)
        value=deepcopy(self.renderer.capabilities())
        provider=getattr(self.renderer,'authoring_constraints',None)
        if callable(provider):value['authoring_constraints']=provider()
        return value

    def _runtime_identity(self,operation_id,value):
        prepare=getattr(self.renderer,'validate_request',None)
        if callable(prepare):
            binding=prepare(operation_id,value['source'],value['document'],scene_space=value['scene_space'],
                deadline_at=value.get('deadline_at'),frame_range=value.get('frame_range'))
            if type(binding) is dict and type(binding.get('request')) is dict:
                return dict(identity_sha256=digest(dict(request=binding['request'],image=binding['image'],
                    timeout_seconds=binding['configured_timeout_seconds'])),request_sha256=binding['request_sha256'])
        return dict(identity_sha256=digest(self.renderer.capabilities()),request_sha256=None)

    def start(self,owner,request,request_key=None):
        owner_id(owner)
        _require(type(request) is dict and {'source','document','scene_space'}<=set(request)
            <={'source','document','scene_space','deadline_at','request_id','frame_range'})
        request_key=request_key if request_key is not None else request.get('request_id')
        identifier(request_key)
        value={key:deepcopy(item) for key,item in request.items() if key!='request_id'}
        _require(len(canonical_bytes(value))<=MAX_BODY,'scene_space_render_request_too_large',413)
        fingerprint=digest(value)
        # Replays retain their original deadline and runtime, including after
        # completion or a later deployment. Only fresh requests preflight anew.
        with self._db() as db:
            previous=db.execute('SELECT document,request_sha256 FROM operations WHERE owner=? AND request_id=?',
                (owner,request_key)).fetchone()
            if previous:
                _require(previous['request_sha256']==fingerprint,'scene_space_render_idempotency_conflict',409)
                return self._public(json.loads(previous['document']))
        plan=value['scene_space']
        frozen=(technical_retry(plan,plan.get('plan_sha256')) if type(plan) is dict
            and plan.get('schema')=='video.scene-space-frozen-plan.v1' else freeze_plan(plan))
        source=value['source']
        _require(type(source) is dict and source.get('engine')=='canvas'
            and type(value['document']) is dict,'scene_space_render_source_invalid')
        from agent_service.profiles.video_animation_experiment import _validate_source_output
        from agent_service.profiles.video_animation_create import source_sha256
        checked=_validate_source_output({key:source.get(key) for key in ('engine','strategy','files')})
        source_hash=source_sha256(checked)
        _require(source.get('source_sha256',source_hash)==source_hash,'scene_space_render_source_changed',409)
        constraints=value['document'].get('constraints',{})
        if type(constraints) is dict and 'sample_capture' in constraints:
            validate_sample_capture(constraints['sample_capture'])
            _require('duration_seconds' in constraints,'scene_space_sample_capture_clip_required')
        frame_range=value.get('frame_range')
        if frame_range is not None:
            _require(type(frame_range) is dict and set(frame_range)=={'start_frame','end_frame'}
                and all(type(frame_range[key]) is int for key in frame_range)
                and 0<=frame_range['start_frame']<frame_range['end_frame']<=frozen['plan']['duration_frames'],
                'scene_space_render_frame_range_invalid')
        if value.get('deadline_at') is not None:
            deadline=value['deadline_at']
            _require(type(deadline) in (int,float) and self.clock()+30<=deadline<=self.clock()+3600,
                'scene_space_render_deadline_invalid')
        identity='ss-'+digest([owner,request_key])[:48]
        runtime_identity=self._runtime_identity(identity,value)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT document,request_sha256 FROM operations WHERE owner=? AND request_id=?',
                (owner,request_key)).fetchone()
            if previous:
                _require(previous['request_sha256']==fingerprint,'scene_space_render_idempotency_conflict',409)
                return self._public(json.loads(previous['document']))
            active=db.execute("SELECT count(*) FROM operations WHERE owner=? AND json_extract(document,'$.status') "
                "IN ('queued','running','reconciling','cancelling')",(owner,)).fetchone()[0]
            _require(active<16,'scene_space_render_queue_full',429)
            row=dict(owner=owner,id=identity,request_id=request_key,request_sha256=fingerprint,
                request=value,plan_sha256=frozen['plan_sha256'],source_sha256=source_hash,
                runtime_identity_sha256=runtime_identity['identity_sha256'],
                render_request_sha256=runtime_identity['request_sha256'],
                status='queued',stop_proven=False,created_at=self.clock(),updated_at=self.clock(),
                cancel_requested=False,result=None,usage=None,error=None)
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                (owner,identity,request_key,fingerprint,canonical_bytes(row).decode()))
            return self._public(row)

    def get(self,owner,operation_id):
        with self._db() as db:return self._public(self._load(db,owner,operation_id))

    def lookup(self,owner,request_key):
        owner_id(owner);identifier(request_key)
        with self._db() as db:
            row=db.execute('SELECT document FROM operations WHERE owner=? AND request_id=?',
                (owner,request_key)).fetchone()
            _require(row is not None,'scene_space_render_not_found',404)
            return self._public(json.loads(row['document']))

    def pending(self,limit=100):
        _require(type(limit) is int and 1<=limit<=100)
        with self._db() as db:
            rows=db.execute("SELECT owner,id FROM operations WHERE json_extract(document,'$.status') "
                "IN ('queued','running','reconciling','cancelling') ORDER BY rowid LIMIT ?",(limit,)).fetchall()
        return [dict(owner=row['owner'],id=row['id']) for row in rows]

    def cancel(self,owner,operation_id):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self._load(db,owner,operation_id)
            if row['status'] in {'completed','failed','cancelled'}:return self._public(row)
            row['cancel_requested']=True
            if row['status']=='queued':row.update(status='cancelled',stop_proven=True)
            else:row['status']='cancelling'
            self._put(db,row)
            return self._public(row)

    def advance(self,owner,operation_id):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self._load(db,owner,operation_id)
            if row['status'] in {'completed','failed','cancelled'}:return self._public(row)
            execute=row['status']=='queued'
            if execute:
                try:
                    current_identity=self._runtime_identity(operation_id,row['request'])
                    _require(current_identity['identity_sha256']==row['runtime_identity_sha256']
                        and current_identity['request_sha256']==row['render_request_sha256'],
                        'scene_space_render_frozen_runtime_changed',409)
                    deadline=row['request'].get('deadline_at')
                    _require(deadline is None or deadline>=self.clock()+30,
                        'scene_space_render_deadline_insufficient',409)
                except ServiceError as error:
                    if error.status>=500:
                        row['error']='scene_space_render_preflight_unavailable'
                    else:
                        row.update(status='failed',stop_proven=True,error=error.code,
                            usage=dict(model_calls=0,model_tokens=0,render_seconds=0))
                    self._put(db,row);return self._public(row)
                row['status']='running';self._put(db,row)
        elapsed=None
        try:
            if execute:
                started=self.monotonic()
                request=row['request']
                result=self.renderer.render(operation_id,request['source'],request['document'],
                    scene_space=request['scene_space'],deadline_at=request.get('deadline_at'),
                    **({'frame_range':request['frame_range']} if 'frame_range' in request else {}),
                    **({'expected_request_sha256':row['render_request_sha256']} if row['render_request_sha256'] else {}))
                elapsed=self.monotonic()-started
            else:result=self.renderer.lookup(operation_id)
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=self._load(db,owner,operation_id)
                if current['status'] in {'completed','failed','cancelled'}:
                    return self._public(current)
                if result is None or result.get('stop_proven') is not True:
                    current['status']='cancelling' if current['cancel_requested'] else 'reconciling'
                    self._put(db,current);return self._public(current)
                current['stop_proven']=True
                current['usage']=dict(model_calls=0,model_tokens=0,render_seconds=elapsed)
                if current['cancel_requested']:
                    current.update(status='cancelled',error='scene_space_render_cancelled')
                elif result.get('technical_passed') is True and result.get('status')=='rendered':
                    path=Path(result['artifact_path']);raw=path.read_bytes()
                    _require(0<len(raw)<=32*1024*1024 and hashlib.sha256(raw).hexdigest()==result['sha256'],
                        'scene_space_render_media_changed',409)
                    frozen=current['request']['scene_space']
                    plan=frozen['plan'] if frozen.get('schema')=='video.scene-space-frozen-plan.v1' else frozen
                    _require(result.get('scene_space',{}).get('plan_sha256')==current['plan_sha256']
                        and result.get('source_sha256')==current['source_sha256'],
                        'scene_space_render_result_changed',409)
                    _require(current['render_request_sha256'] is None
                        or result.get('render_request_sha256')==current['render_request_sha256'],
                        'scene_space_render_frozen_runtime_changed',409)
                    metadata={key:deepcopy(value) for key,value in result.items() if not key.endswith('_path')}
                    static='duration_seconds' not in current['request']['document'].get('constraints',{})
                    frame_range=current['request'].get('frame_range') or dict(start_frame=0,end_frame=1 if static else plan['duration_frames'])
                    expected_count=frame_range['end_frame']-frame_range['start_frame']
                    _require(result.get('frame_count')==expected_count and type(result.get('frame_count')) is int
                        and result.get('fps')==24 and result.get('mime')==('image/png' if static else 'video/mp4'),
                        'scene_space_render_media_clock_changed',409)
                    metadata.update(_sample_capture_metadata(current,result))
                    metadata.update(bytes=len(raw),frame_count=expected_count,fps=24,
                        snapshot_sha256=result['scene_space_sha256'],
                        plan_sha256=current['plan_sha256'],source_sha256=current['source_sha256'])
                    current.update(status='completed',result=metadata,artifact_path=str(path))
                else:current.update(status='failed',error='scene_space_render_failed')
                self._put(db,current);return self._public(current)
        except Exception as error:
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=self._load(db,owner,operation_id)
                if current['status'] in {'completed','failed','cancelled'}:
                    return self._public(current)
                # A renderer exception alone cannot prove the container stopped.
                try:
                    proof=self.renderer.stopping_proof(operation_id)
                except Exception as proof_error:
                    proof=None
                    current['stop_verification_error']=getattr(proof_error,'code',
                        'scene_space_render_stop_verification_failed')
                stopped=bool(type(proof) is dict and proof.get('stop_proven') is True
                    or isinstance(error,ServiceError) and getattr(error,'not_dispatched',False) is True)
                if stopped and execute and elapsed is None:
                    elapsed=max(0,self.monotonic()-started)
                current.update(status='failed' if stopped else 'reconciling',stop_proven=stopped,
                    error=getattr(error,'code','scene_space_render_executor_error'),
                    usage=dict(model_calls=0,model_tokens=0,render_seconds=elapsed))
                self._put(db,current);return self._public(current)

    def video(self,owner,operation_id):
        with self._db() as db:row=self._load(db,owner,operation_id)
        _require(row['status']=='completed' and row['stop_proven'],'scene_space_render_media_unavailable',409)
        path=_plain(row['artifact_path']);raw=path.read_bytes()
        _require(len(raw)==row['result']['bytes'] and hashlib.sha256(raw).hexdigest()==row['result']['sha256'],
            'scene_space_render_media_changed',409)
        return raw,row['result'].get('mime','video/mp4')

    def _verified_frames(self,owner,operation_id,max_frames):
        _frame_limit(max_frames)
        with self._db() as db:row=self._load(db,owner,operation_id)
        metadata=_preview_metadata(row,operation_id)
        request=row['request'];frozen=request['scene_space']
        plan=frozen['plan'] if frozen.get('schema')=='video.scene-space-frozen-plan.v1' else frozen
        frame_range=request.get('frame_range') or dict(start_frame=0,end_frame=plan['duration_frames'])
        _require(digest(request)==row['request_sha256'] and metadata['viewport']==plan['viewport']
            and metadata['duration_seconds']==(frame_range['end_frame']-frame_range['start_frame'])/24,
            'scene_space_preview_metadata_changed',409)
        verified=self.renderer.lookup(operation_id)
        _require(type(verified) is dict and verified.get('status')=='rendered'
            and verified.get('technical_passed') is True and verified.get('stop_proven') is True,
            'scene_space_preview_frames_unavailable',409)
        _require(type(verified.get('scene_space')) is dict and verified.get('source_sha256')==row['source_sha256']
            and verified.get('scene_space',{}).get('plan_sha256')==row['plan_sha256']
            and verified.get('scene_space_sha256')==metadata['snapshot_sha256']
            and all(verified.get(key)==row['result'].get(key) for key in
                ('sha256','frame_count','fps','mime','width','height','duration_seconds'))
            and (row['render_request_sha256'] is None
                or verified.get('render_request_sha256')==row['render_request_sha256']),
            'scene_space_preview_result_changed',409)
        _require(all((key in verified)==(key in metadata)
            and (key not in metadata or verified[key]==metadata[key])
            for key in ('sample_capture','sample_frame_range')),
            'scene_space_preview_capture_metadata_changed',409)
        frames=self.renderer.frames(operation_id,max_frames=max_frames)
        return metadata,_preview_frames(frames,metadata,max_frames)

    def frames(self,owner,operation_id,max_frames=3):
        return self._verified_frames(owner,operation_id,max_frames)[1]

    def frames_response(self,owner,operation_id,max_frames=3):
        metadata,frames=self._verified_frames(owner,operation_id,max_frames)
        value=dict(metadata,frames=[dict(timestamp_seconds=frame['timestamp_seconds'],mime=frame['mime'],
            bytes=len(frame['raw']),sha256=hashlib.sha256(frame['raw']).hexdigest(),
            content_base64=base64.b64encode(frame['raw']).decode('ascii'),
            **({'capture':deepcopy(frame['capture'])} if 'capture' in frame else {})) for frame in frames])
        _require(len(canonical_bytes(value))<=_frame_response_limit(max_frames),
            'scene_space_preview_frames_too_large',413)
        return value


def route_private(handler,owner,method,path,body=None):
    if not (path==PREFIX or path.startswith(PREFIX+'/')):return False
    service=getattr(handler.server,'scene_space_service',None)
    _require(service is not None,'scene_space_render_unavailable',503)
    pieces=path[len(PREFIX):].strip('/').split('/')
    if method=='GET':
        if pieces==['capabilities']:value=service.capabilities(owner)
        elif len(pieces)==2 and pieces[0]=='requests':value=service.lookup(owner,unquote(pieces[1]))
        elif len(pieces)==2 and pieces[1]=='video':
            raw,mime=service.video(owner,unquote(pieces[0]));handler._send(raw,raw=True,mime=mime);return True
        elif len(pieces) in (2,3) and pieces[1]=='frames':
            _require(len(pieces)==2 or pieces[2] in ('1','2','3'),'scene_space_preview_frame_limit_invalid')
            value=service.frames_response(owner,unquote(pieces[0]),int(pieces[2]) if len(pieces)==3 else 3)
        elif len(pieces)==1 and pieces[0]:value=service.get(owner,unquote(pieces[0]))
        else:raise ServiceError('scene_space_render_not_found',404)
        handler._send(value);return True
    if method=='POST' and path==PREFIX:
        value=service.start(owner,body)
        if getattr(handler.server,'scene_space_pump',None):handler.server.scene_space_pump.notify()
        handler._send(value,202);return True
    if method=='POST' and len(pieces)==2 and pieces[1]=='cancel':
        _require(body=={})
        handler._send(service.cancel(owner,unquote(pieces[0])));return True
    raise ServiceError('scene_space_render_not_found',404)
