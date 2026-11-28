"""Bind retained capability-experiment pictures to the common private sampler.

The existing capture reviewer still establishes source/runtime custody. This
adapter transfers its exact selected bytes; it never captures, renders or calls
a model and does not change the evaluation request.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest


INTENT_NAME='sampling-intent.json'
REFERENCE_NAME='sampling-reference.json'
TERMINAL={'failed','cancelled','timed_out','interrupted','unknown'}


def require(ok,code='ability_sampling_evidence_changed'):
    if not ok:raise ServiceError(code,409)


def _read(path):
    require(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=256*1024)
    return json.loads(path.read_bytes())


def _save(path,value):
    require(not path.is_symlink())
    raw=canonical_bytes(value)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())


def _source(run,technical):
    return dict(provider='ability-retained-capture',run_identity=run['identity'],
        runtime_identity=run['runtime_identity'],semantic_input_sha256=run['semantic_input_sha256'],
        technical_evidence_sha256=digest(technical),
        origin='client_retained_capture',
        provenance_scope='existing_capture_reviewer_checked_not_executor_native_measurement')


def _policy(frames):
    return dict(strategy='provided_labels',frame_limit=len(frames),max_frame_bytes=4*1024*1024,
        max_total_bytes=96*1024*1024,selection=[frame['id'] for frame in frames],
        clock='recorded_capture_times',scope='selected_capability_capture_states_only')


def _check_frames(frames,images):
    require(type(frames) is list and 1<=len(frames)<=240)
    require(len({frame['id'] for frame in frames})==len(frames))
    require(type(images) is dict and set(images)=={frame['id'] for frame in frames})
    for frame in frames:
        raw=images[frame['id']]
        require(type(raw) is bytes and 0<len(raw)<=4*1024*1024
            and hashlib.sha256(raw).hexdigest()==frame['sha256'])
        timestamp=frame['time_seconds']
        require(type(timestamp) in (int,float) and math.isfinite(timestamp) and timestamp>=0)
    require(sum(map(len,images.values()))<=96*1024*1024)


def _read_images(client,owner,reference,frames,images,intent):
    _check_frames(frames,images)
    manifest=client.read_manifest(owner,reference['operation_id'])
    require(manifest.get('status')=='completed'
        and manifest.get('manifest_sha256')==reference['manifest_sha256'])
    identity={key:intent[key] for key in ('owner','operation_id','source_ref','policy')}
    require(reference.get('owner')==owner==intent['owner']
        and reference.get('operation_id')==intent['operation_id']
        and reference.get('request_sha256')==digest(identity)
        and manifest.get('policy')==intent['policy']
        and manifest.get('deadline_at')==intent['deadline_at'])
    source=manifest.get('source_ref')
    transferred=(type(source) is dict and source.get('provider')=='private-retained-source'
        and source.get('registration_id')==intent['operation_id']
        and source.get('source')==intent['source_ref'])
    require(source==intent['source_ref'] or transferred)
    rows=client.read_frames(owner,reference['operation_id'])
    require(len(rows)==len(frames))
    verified={}
    for row,frame in zip(rows,frames):
        raw=row.get('raw')
        require(row.get('frame_id')==frame['id'] and row.get('mime')==frame['mime_type']
            and row.get('timestamp_seconds')==frame['time_seconds']
            and row.get('metadata',{}).get('observation')==frame
            and type(raw) is bytes and raw==images[frame['id']])
        verified[frame['id']]=raw
    return verified


def prepare_ability_sampling(root,run,technical,frames,images,client,owner,*,
                             deadline_at=None,clock=time.time,pause=time.sleep):
    """Explicit collect-stage write; retry reconciles exactly the same operation."""
    root=Path(root)
    require(root.is_dir() and not any(path.is_symlink() for path in (root,*root.parents)))
    require(client is not None and type(owner) is str and bool(owner),'ability_sampling_service_required')
    _check_frames(frames,images)
    source=_source(run,technical);policy=_policy(frames)
    operation='ability-'+digest(dict(owner=owner,source=source,policy=policy))[:56]
    identity=dict(owner=owner,operation_id=operation,source_ref=source,policy=policy)
    intent_path=root/INTENT_NAME
    if intent_path.exists():
        intent=_read(intent_path)
        require({key:intent.get(key) for key in identity}==identity)
        require(deadline_at is None or deadline_at==intent['deadline_at'])
    else:
        now=clock();deadline_at=now+600 if deadline_at is None else deadline_at
        require(type(deadline_at) in (int,float) and math.isfinite(deadline_at)
            and now<deadline_at<=now+3600,'ability_sampling_deadline_invalid')
        intent=dict(identity,deadline_at=deadline_at)
        try:_save(intent_path,intent)
        except FileExistsError:
            intent=_read(intent_path)
            require({key:intent.get(key) for key in identity}==identity)
    reference_path=root/REFERENCE_NAME
    if reference_path.exists():
        reference=_read(reference_path)
        require(reference.get('request_sha256')==digest(identity))
        return reference,_read_images(client,owner,reference,frames,images,intent)
    rows=[dict(raw=images[frame['id']],mime=frame['mime_type'],
        timestamp_seconds=frame['time_seconds'],frame_id=frame['id'],
        role='baseline' if frame['variant_id']=='baseline' else 'output',
        metadata=dict(observation=deepcopy(frame))) for frame in frames]
    try:state=client.get(owner,operation)
    except ServiceError as error:
        if error.status!=404:raise
        state=None
    # A crash after remote completion must not prevent later read-only recovery
    # of the original operation, even when its execution deadline has elapsed.
    if state is None or state.get('status') not in TERMINAL|{'completed'}:
        if clock()>=intent['deadline_at']:
            if state is not None:client.cancel(owner,operation)
            raise ServiceError('ability_sampling_deadline_expired',409)
        state=client.prepare_frames(owner,operation,source,policy,rows,deadline_at=intent['deadline_at'])
    while state.get('status')!='completed':
        require(state.get('status') not in TERMINAL,'ability_sampling_not_completed')
        if clock()>=intent['deadline_at']:
            cancel=getattr(client,'cancel',None)
            if callable(cancel):cancel(owner,operation)
            raise ServiceError('ability_sampling_deadline_expired',409)
        pause(.1)
        state=client.get(owner,operation)
    require(type(state.get('manifest_sha256')) is str)
    reference=dict(schema='video.ability-sampling-reference.v1',owner=owner,
        operation_id=operation,request_sha256=digest(identity),manifest_sha256=state['manifest_sha256'],
        selected_frame_ids=[frame['id'] for frame in frames],
        scope='retained_capture_samples_not_continuous_video_or_audio')
    verified=_read_images(client,owner,reference,frames,images,intent)
    try:_save(reference_path,reference)
    except FileExistsError:require(_read(reference_path)==reference)
    return reference,verified


def read_ability_sampling(root,frames,images,client,owner,*,run,technical):
    """Read-only model preparation for a run already collected through the service."""
    reference=_read(Path(root)/REFERENCE_NAME)
    intent=_read(Path(root)/INTENT_NAME)
    require(intent.get('source_ref')==_source(run,technical) and intent.get('policy')==_policy(frames))
    require(reference['owner']==owner and reference['selected_frame_ids']==[frame['id'] for frame in frames])
    return _read_images(client,owner,reference,frames,images,intent)
