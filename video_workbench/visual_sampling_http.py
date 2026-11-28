"""Private, owner-bound source transfer into the common sampling service.

Only explicit POST preparation writes or decodes. GET reads retained results.
The caller verifies its business source before registering immutable bytes;
this transport proves transfer identity, not independent native capture.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote, unquote

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .animation_creation_http import CreationTransportError

PREFIX = '/api/visual-sampling'
SCHEMA = 'video.visual-sampling-transfer.v1'
CHUNK_BYTES = 512 * 1024
MAX_BODY = 768 * 1024
MAX_MEDIA_BYTES = 512 * 1024 * 1024
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_FRAME_TOTAL = 96 * 1024 * 1024
MAX_BUNDLE_RESPONSE = 16 * 1024 * 1024
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z')
_OWNER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,199}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_ACTIVE = {'uploading', 'queued', 'running', 'cancelling'}
_DONE = {'completed', 'failed', 'cancelled', 'timed_out', 'interrupted'}


def require(value, code='visual_sampling_transfer_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _identity(value):
    require(type(value) is str and _ID.fullmatch(value), 'visual_sampling_operation_invalid')
    return value


def _owner(value):
    require(type(value) is str and _OWNER.fullmatch(value), 'visual_sampling_owner_invalid', 403)
    return value


def _sha(value):
    require(type(value) is str and _SHA.fullmatch(value), 'visual_sampling_hash_invalid')
    return value


def _ordinary(path):
    require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
        for p in (path, *path.parents)), 'visual_sampling_transfer_path_invalid', 409)


def _hash_file(path):
    _ordinary(path)
    before = path.stat()
    require(path.is_file() and before.st_nlink == 1, 'visual_sampling_transfer_path_invalid', 409)
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b''):
            checksum.update(chunk)
    after = path.stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns) ==
        (after.st_ino, after.st_size, after.st_mtime_ns), 'visual_sampling_source_changed', 409)
    return checksum.hexdigest()


def _registration(value):
    require(type(value) is dict and set(value) == {'schema', 'operation_id', 'kind', 'source',
        'policy', 'sha256', 'bytes', 'files', 'deadline_at'} and value['schema'] == SCHEMA)
    _identity(value['operation_id']); _sha(value['sha256'])
    require(len(canonical_bytes(value)) <= MAX_BODY, 'visual_sampling_registration_too_large', 413)
    require(value['kind'] in {'formal-media', 'retained-frames'}
        and type(value['source']) is dict and value['source']
        and type(value['policy']) is dict and value['policy']
        and len(canonical_bytes(value['source'])) <= 32768
        and len(canonical_bytes(value['policy'])) <= 32768)
    require(type(value['deadline_at']) in (int, float) and math.isfinite(value['deadline_at'])
        and value['deadline_at'] > 0)
    maximum = MAX_MEDIA_BYTES if value['kind'] == 'formal-media' else MAX_FRAME_TOTAL
    require(type(value['bytes']) is int and 0 < value['bytes'] <= maximum,
        'visual_sampling_source_size_invalid', 413)
    if value['kind'] == 'formal-media':
        require(value['files'] == [] and value['policy'].get('strategy') == 'formal_legacy')
        source = value['source']
        required = {'project_id', 'render_id', 'production_run_id', 'film_sha256', 'media_sha256'}
        require(required <= set(source) and source['media_sha256'] == value['sha256'])
        for key in ('project_id', 'render_id', 'production_run_id'):
            require(type(source[key]) is str and 0 < len(source[key]) <= 200)
        for key in ('film_sha256', 'media_sha256'): _sha(source[key])
        policy = value['policy']
        require(set(policy) == {'strategy', 'clip_id', 'goal', 'source_sha256', 'sample_fps',
            'frame_limit', 'review_profile'} and policy['source_sha256'] == source['film_sha256'])
        require(type(policy['sample_fps']) in (int, float) and math.isfinite(policy['sample_fps'])
            and .25 <= policy['sample_fps'] <= 12 and type(policy['frame_limit']) is int
            and 2 <= policy['frame_limit'] <= 24)
    else:
        rows = value['files']
        require(type(rows) is list and 1 <= len(rows) <= 240)
        total = 0
        identities = set()
        for row in rows:
            require(type(row) is dict and set(row) == {'metadata', 'bytes', 'sha256', 'offset'})
            require(type(row['metadata']) is dict and len(canonical_bytes(row['metadata'])) <= 32768
                and type(row['bytes']) is int and 0 < row['bytes'] <= MAX_FRAME_BYTES
                and row['offset'] == total)
            _sha(row['sha256'])
            meta = row['metadata']
            require(meta.get('mime') in {'image/png', 'image/jpeg'}
                and type(meta.get('timestamp_seconds')) in (int, float)
                and math.isfinite(meta['timestamp_seconds']) and meta['timestamp_seconds'] >= 0)
            if 'frame_id' in meta:
                require(type(meta['frame_id']) is str and 0 < len(meta['frame_id']) <= 240
                    and meta['frame_id'] not in identities)
                identities.add(meta['frame_id'])
            total += row['bytes']
        require(total == value['bytes'])
    return deepcopy(value)


class FormalSamplingTransport:
    """One transfer ledger and source provider for the executor-owned sampler."""
    def __init__(self, root, sampling_service, *, clock=time.time, max_total_bytes=4*1024**3,
                 max_pending_per_owner=8, max_pending_total=64):
        self.root = Path(root).absolute(); _ordinary(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'sampling-transfers.sqlite3'
        self.sampler, self.clock = sampling_service, clock
        self.max_total_bytes = max_total_bytes
        self.max_pending_per_owner, self.max_pending_total = max_pending_per_owner, max_pending_total
        with closing(self._connect()) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS sampling_transfers (
                owner TEXT NOT NULL,id TEXT NOT NULL,request_sha256 TEXT NOT NULL,request BLOB NOT NULL,
                state TEXT NOT NULL,offset INTEGER NOT NULL,cancel_requested INTEGER NOT NULL DEFAULT 0,
                error TEXT,started_at REAL,finished_at REAL,elapsed_seconds REAL,
                PRIMARY KEY(owner,id))''')
            db.commit()

    def _connect(self):
        _ordinary(self.path)
        db = sqlite3.connect(self.path, timeout=30); db.row_factory = sqlite3.Row
        return db

    def _row(self, owner, operation):
        _owner(owner); _identity(operation)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM sampling_transfers WHERE owner=? AND id=?', (owner, operation)).fetchone()
        require(row is not None, 'visual_sampling_not_found', 404)
        request = json.loads(row['request'])
        require(digest(request) == row['request_sha256'], 'visual_sampling_transfer_record_changed', 409)
        return dict(row), request

    def _source_path(self, owner, operation):
        directory = self.root / 'sources' / digest([_owner(owner), _identity(operation)])
        _ordinary(directory)
        return directory / 'source.bin'

    @staticmethod
    def _sample_operation(owner, operation):
        return 'transfer-' + digest([owner, operation])[:48]

    def _public(self, row, request):
        manifest_sha256 = self._manifest_hash(row['owner'], row['id']) if row['state'] == 'completed' else None
        return dict(schema=SCHEMA, operation_id=row['id'], kind=request['kind'],
            request_sha256=row['request_sha256'], state=row['state'], status=row['state'], error=row['error'],
            received_bytes=row['offset'], bytes=request['bytes'], sha256=request['sha256'],
            deadline_at=request['deadline_at'], source=deepcopy(request['source']), policy=deepcopy(request['policy']),
            stop_proven=row['state'] in _DONE, elapsed_seconds=row['elapsed_seconds'],
            sampling_operation_id=self._sample_operation(row['owner'], row['id']),
            sampling_manifest_sha256=manifest_sha256, manifest_sha256=manifest_sha256)

    def _manifest_hash(self, owner, operation):
        return self.sampler.get(owner, self._sample_operation(owner, operation)).get('manifest_sha256')

    def capabilities(self):
        return dict(schema=SCHEMA, enabled=True, chunk_bytes=CHUNK_BYTES,
            max_media_bytes=MAX_MEDIA_BYTES, max_frame_bytes=MAX_FRAME_BYTES,
            max_frame_total_bytes=MAX_FRAME_TOTAL, supports=['formal-media', 'retained-frames'],
            evaluation_changed=False, model_calls=0)

    def register(self, owner, value):
        _owner(owner); request = _registration(value); operation = request['operation_id']
        if 'owner' in request['source']:
            require(request['source']['owner'] == owner, 'visual_sampling_source_owner_changed', 403)
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM sampling_transfers WHERE owner=? AND id=?', (owner, operation)).fetchone()
            if old is not None:
                require(old['request_sha256'] == digest(request), 'visual_sampling_operation_conflict', 409)
            else:
                require(now < request['deadline_at'] <= now + 3600, 'visual_sampling_deadline_invalid', 409)
                rows = db.execute('SELECT owner,state,request,request_sha256 FROM sampling_transfers').fetchall()
                retained_bytes = 0
                for row in rows:
                    retained = json.loads(row['request'])
                    require(digest(retained) == row['request_sha256'], 'visual_sampling_transfer_record_changed', 409)
                    retained_bytes += retained['bytes']
                require(retained_bytes + request['bytes'] <= self.max_total_bytes,
                    'visual_sampling_storage_capacity_exceeded', 429)
                pending = [row for row in rows if row['state'] in _ACTIVE]
                require(len(pending) < self.max_pending_total
                    and sum(row['owner'] == owner for row in pending) < self.max_pending_per_owner,
                    'visual_sampling_pending_capacity_exceeded', 429)
                db.execute('INSERT INTO sampling_transfers(owner,id,request_sha256,request,state,offset) VALUES(?,?,?,?,?,?)',
                    (owner, operation, digest(request), canonical_bytes(request), 'uploading', 0))
            db.commit()
        return self.get(owner, operation)

    def get(self, owner, operation):
        return self._public(*self._row(owner, operation))

    def upload(self, owner, operation, value):
        require(type(value) is dict and set(value) == {'offset', 'sha256', 'content_base64'})
        offset = value['offset']; _sha(value['sha256'])
        require(type(offset) is int and offset >= 0 and type(value['content_base64']) is str
            and 0 < len(value['content_base64']) <= 4*((CHUNK_BYTES+2)//3))
        try: raw = base64.b64decode(value['content_base64'], validate=True)
        except (ValueError, binascii.Error): raise ServiceError('visual_sampling_chunk_invalid', 422) from None
        require(0 < len(raw) <= CHUNK_BYTES and hashlib.sha256(raw).hexdigest() == value['sha256'],
            'visual_sampling_chunk_invalid', 422)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            stored = db.execute('SELECT * FROM sampling_transfers WHERE owner=? AND id=?', (_owner(owner), _identity(operation))).fetchone()
            require(stored is not None, 'visual_sampling_not_found', 404)
            request = json.loads(stored['request'])
            require(digest(request) == stored['request_sha256'], 'visual_sampling_transfer_record_changed', 409)
            require(stored['state'] == 'uploading' and not stored['cancel_requested']
                and self.clock() < request['deadline_at'], 'visual_sampling_upload_not_active', 409)
            require(offset <= stored['offset'] and offset + len(raw) <= request['bytes'],
                'visual_sampling_chunk_offset_invalid', 409)
            path = self._source_path(owner, operation); path.parent.mkdir(parents=True, exist_ok=True)
            _ordinary(path)
            if offset < stored['offset']:
                require(offset + len(raw) <= stored['offset'] and path.is_file(), 'visual_sampling_chunk_offset_invalid', 409)
                with path.open('rb') as stream:
                    stream.seek(offset); require(stream.read(len(raw)) == raw, 'visual_sampling_chunk_changed', 409)
            else:
                if path.exists():
                    require(path.is_file() and path.stat().st_nlink == 1
                        and offset <= path.stat().st_size <= offset + len(raw), 'visual_sampling_upload_file_changed', 409)
                    with path.open('rb') as stream:
                        stream.seek(offset); tail = stream.read()
                    require(tail == raw[:len(tail)], 'visual_sampling_chunk_changed', 409)
                else: require(offset == 0, 'visual_sampling_chunk_offset_invalid', 409)
                with path.open('r+b' if path.exists() else 'xb') as stream:
                    stream.seek(offset); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                db.execute('UPDATE sampling_transfers SET offset=? WHERE owner=? AND id=?',
                    (offset + len(raw), owner, operation))
            db.commit()
        return self.get(owner, operation)

    def commit(self, owner, operation):
        row, request = self._row(owner, operation)
        if row['state'] != 'uploading': return self.get(owner, operation)
        require(not row['cancel_requested'] and self.clock() < request['deadline_at'], 'visual_sampling_upload_not_active', 409)
        path = self._source_path(owner, operation)
        require(row['offset'] == request['bytes'] and path.stat().st_size == request['bytes']
            and _hash_file(path) == request['sha256'], 'visual_sampling_source_changed', 409)
        with closing(self._connect()) as db:
            db.execute('UPDATE sampling_transfers SET state=? WHERE owner=? AND id=? AND state=? AND cancel_requested=0',
                ('queued', owner, operation, 'uploading')); db.commit()
        return self.get(owner, operation)

    def pending(self, limit=100):
        with closing(self._connect()) as db:
            rows = db.execute('SELECT owner,id,state FROM sampling_transfers ORDER BY rowid').fetchall()
        return [dict(owner=row['owner'], id=row['id']) for row in rows if row['state'] in _ACTIVE][:limit]

    def _check(self, owner, operation):
        row, request = self._row(owner, operation)
        require(not row['cancel_requested'], 'visual_sampling_cancelled', 409)
        require(self.clock() < request['deadline_at'], 'visual_sampling_deadline_expired', 409)

    def _provider(self, owner, operation, request):
        def provider(actual_owner, source_ref, policy, check):
            require(actual_owner == owner, 'visual_sampling_source_owner_changed', 403)
            self._check(owner, operation); check()
            path = self._source_path(owner, operation)
            require(path.stat().st_size == request['bytes'] and _hash_file(path) == request['sha256'],
                'visual_sampling_source_changed', 409)
            source = dict(request['source'], transfer_sha256=request['sha256'], transfer_bytes=request['bytes'],
                provenance_scope='client_retained_source_identity_and_verified_transfer_not_independent_native_capture')
            if request['kind'] == 'formal-media':
                kwargs = {key: value for key, value in policy.items() if key != 'strategy'}
                return dict(source=source, media_path=path, legacy_bundle=kwargs)
            frames = []
            with path.open('rb') as stream:
                for row in request['files']:
                    self._check(owner, operation); check(); stream.seek(row['offset']); raw = stream.read(row['bytes'])
                    require(len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256'],
                        'visual_sampling_source_changed', 409)
                    frames.append(dict(row['metadata'], raw=raw))
            return dict(source=source, frames=frames)
        return provider

    def advance(self, owner, operation):
        row, request = self._row(owner, operation)
        if row['state'] not in _ACTIVE: return
        if row['state'] == 'uploading':
            if self.clock() >= request['deadline_at']: self._finish(owner, operation, 'timed_out', 'visual_sampling_deadline_expired')
            return
        if row['cancel_requested'] and row['state'] == 'queued':
            self._finish(owner, operation, 'cancelled', 'visual_sampling_cancelled'); return
        with closing(self._connect()) as db:
            db.execute('UPDATE sampling_transfers SET state=?,started_at=COALESCE(started_at,?) WHERE owner=? AND id=? AND state=?',
                ('running', self.clock(), owner, operation, 'queued')); db.commit()
        sample_id = self._sample_operation(owner, operation)
        source_ref = dict(provider='private-retained-source', registration_id=operation,
            media_sha256=request['sha256'], bytes=request['bytes'], source=request['source'])
        try:
            result = self.sampler.prepare(owner, sample_id, source_ref, request['policy'],
                provider=self._provider(owner, operation, request), deadline_at=request['deadline_at'])
            state = result.get('status', result.get('state'))
            if state in {'pending', 'running'}: return
            require(state in _DONE, 'visual_sampling_core_state_invalid', 502)
            row, _ = self._row(owner, operation)
            if row['cancel_requested']: state = 'cancelled'
            self._finish(owner, operation, state, result.get('error'))
        except ServiceError as error:
            row, _ = self._row(owner, operation)
            state = 'cancelled' if row['cancel_requested'] else 'timed_out' if self.clock() >= request['deadline_at'] else 'failed'
            self._finish(owner, operation, state, error.code)
        except Exception:
            self._finish(owner, operation, 'failed', 'visual_sampling_preparation_failed')

    def _finish(self, owner, operation, state, error):
        now = self.clock()
        with closing(self._connect()) as db:
            row = db.execute('SELECT state,started_at FROM sampling_transfers WHERE owner=? AND id=?', (owner, operation)).fetchone()
            require(row is not None, 'visual_sampling_not_found', 404)
            if row['state'] in _DONE: return
            elapsed = max(0., now - row['started_at']) if row['started_at'] is not None else 0.
            db.execute('UPDATE sampling_transfers SET state=?,error=?,finished_at=?,elapsed_seconds=? WHERE owner=? AND id=?',
                (state, error, now, elapsed, owner, operation)); db.commit()

    def cancel(self, owner, operation):
        row, _ = self._row(owner, operation)
        if row['state'] in _DONE: return self.get(owner, operation)
        with closing(self._connect()) as db:
            db.execute('UPDATE sampling_transfers SET cancel_requested=1 WHERE owner=? AND id=?', (owner, operation)); db.commit()
        if row['state'] in {'uploading', 'queued'}:
            self._finish(owner, operation, 'cancelled', 'visual_sampling_cancelled')
        else:
            try: self.sampler.cancel(owner, self._sample_operation(owner, operation))
            except ServiceError as error:
                if error.status != 404: raise
        return self.get(owner, operation)

    def manifest(self, owner, operation):
        row, _ = self._row(owner, operation)
        require(row['state'] == 'completed', 'visual_sampling_result_unavailable', 409)
        return self.sampler.get(owner, self._sample_operation(owner, operation))

    def frames_metadata(self, owner, operation):
        manifest = self.manifest(owner, operation)
        return [dict(frame['original'], bytes=frame['bytes'], sha256=frame['sha256'])
            for frame in manifest['frames']]

    def frame(self, owner, operation, index):
        manifest = self.manifest(owner, operation)
        require(type(index) is int and 0 <= index < 240, 'visual_sampling_frame_invalid')
        require(index < len(manifest['frames']), 'visual_sampling_not_found', 404)
        frame = self.sampler.read_frame(owner, self._sample_operation(owner, operation), index,
            expected_manifest_sha256=manifest['manifest_sha256'])
        return frame['raw'], frame['mime']

    def bundle(self, owner, operation):
        manifest = self.manifest(owner, operation)
        sample_id = self._sample_operation(owner, operation)
        profile, document, _ = self.sampler.bundle(owner, sample_id)
        exported = self.sampler.export_bundle(owner, sample_id)
        value = dict(profile=profile, document=document, sampling=manifest,
            files={name:dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
                content_base64=base64.b64encode(raw).decode('ascii')) for name,raw in exported.items()})
        require(len(canonical_bytes(value)) <= MAX_BUNDLE_RESPONSE, 'visual_sampling_bundle_too_large', 413)
        return value


class VisualSamplingClient:
    """Reuse the existing private fixed-origin, owner-authenticated transport."""
    def __init__(self, transport):
        require(callable(getattr(transport, '_request', None)), 'visual_sampling_transport_required', 503)
        self.transport = transport

    def _request(self, owner, method, path, body=None, *, artifact=False, response_limit=None):
        try:
            options = dict(artifact=True, response_limit=response_limit) if artifact else {}
            return self.transport._request(owner, method, PREFIX+path, body, **options)
        except CreationTransportError as error: raise ServiceError(error.code, error.status) from None

    def get(self, owner, operation):
        return self._request(owner, 'GET', '/sources/'+quote(_identity(operation),safe=''))

    def _prepare(self, owner, request, data):
        try: current = self.get(owner, request['operation_id'])
        except ServiceError as error:
            if error.status != 404: raise
            current = self._request(owner, 'POST', '/sources', request)
        require(current.get('request_sha256') == digest(request), 'visual_sampling_operation_conflict', 409)
        operation = quote(request['operation_id'],safe='')
        if current['state'] == 'uploading':
            offset = current['received_bytes']
            require(type(offset) is int and 0 <= offset <= len(data), 'visual_sampling_chunk_offset_invalid', 502)
            while offset < len(data):
                raw = data[offset:offset+CHUNK_BYTES]
                current = self._request(owner, 'POST', '/sources/'+operation+'/chunks',
                    dict(offset=offset,sha256=hashlib.sha256(raw).hexdigest(),content_base64=base64.b64encode(raw).decode('ascii')))
                require(current.get('received_bytes') == offset+len(raw), 'visual_sampling_chunk_offset_invalid', 502)
                offset += len(raw)
            current = self._request(owner, 'POST', '/sources/'+operation+'/commit', {})
        return current

    def prepare_media(self, owner, operation_id, source_ref, policy, raw, *, deadline_at):
        require(type(raw) is bytes and 0 < len(raw) <= MAX_MEDIA_BYTES, 'visual_sampling_source_size_invalid', 413)
        request = dict(schema=SCHEMA,operation_id=_identity(operation_id),kind='formal-media',
            source=deepcopy(source_ref),policy=deepcopy(policy),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
            files=[],deadline_at=deadline_at)
        return self._prepare(owner, _registration(request), raw)

    def prepare_frames(self, owner, operation_id, source_ref, policy, frames, *, deadline_at=None):
        if deadline_at is None:
            try: deadline_at = self.get(owner, operation_id)['deadline_at']
            except ServiceError as error:
                if error.status != 404: raise
                deadline_at = time.time()+600
        require(type(frames) is list and 1 <= len(frames) <= 240)
        rows=[];chunks=[];offset=0
        for frame in frames:
            require(type(frame) is dict and type(frame.get('raw')) is bytes)
            raw=frame['raw'];metadata={k:deepcopy(v) for k,v in frame.items() if k!='raw'}
            require('content_base64' not in metadata)
            rows.append(dict(metadata=metadata,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),offset=offset))
            chunks.append(raw);offset+=len(raw)
        require(offset <= MAX_FRAME_TOTAL, 'visual_sampling_source_size_invalid', 413)
        raw=b''.join(chunks)
        request=dict(schema=SCHEMA,operation_id=_identity(operation_id),kind='retained-frames',source=deepcopy(source_ref),
            policy=deepcopy(policy),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),files=rows,deadline_at=deadline_at)
        return self._prepare(owner,_registration(request),raw)

    def manifest(self, owner, operation):
        registration = self.get(owner, operation)
        require(registration.get('state') == 'completed' and registration.get('stop_proven') is True,
            'visual_sampling_result_unavailable', 409)
        value = self._request(owner,'GET','/sources/'+quote(_identity(operation),safe='')+'/manifest')
        expected_source_ref = dict(provider='private-retained-source',registration_id=operation,
            media_sha256=registration['sha256'],bytes=registration['bytes'],source=registration['source'])
        require(type(value) is dict and value.get('status') == 'completed'
            and value.get('operation_id') == registration['sampling_operation_id']
            and value.get('manifest_sha256') == registration['sampling_manifest_sha256']
            and value.get('source_ref') == expected_source_ref and value.get('policy') == registration['policy'],
            'visual_sampling_manifest_identity_changed',502)
        return value

    read_manifest = manifest

    def read_frames(self, owner, operation):
        manifest = self.manifest(owner, operation)
        path='/sources/'+quote(_identity(operation),safe='')
        rows=self._request(owner,'GET',path+'/frames')
        require(type(rows) is list and 1<=len(rows)<=240, 'visual_sampling_frames_invalid',502)
        expected = [dict(frame['original'],bytes=frame['bytes'],sha256=frame['sha256']) for frame in manifest['frames']]
        require(rows == expected, 'visual_sampling_frame_manifest_changed', 502)
        output=[]
        for index,row in enumerate(rows):
            require(type(row) is dict and type(row.get('bytes')) is int and 0<row['bytes']<=MAX_FRAME_BYTES,
                'visual_sampling_frame_invalid',502)
            raw,mime=self._request(owner,'GET',path+'/frames/'+str(index),artifact=True,response_limit=MAX_FRAME_BYTES)
            require(mime==row.get('mime') and len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256'],
                'visual_sampling_frame_changed',502)
            output.append(dict({k:v for k,v in row.items() if k not in {'bytes','sha256'}},raw=raw))
        return output

    def export_bundle(self, owner, operation):
        manifest = self.manifest(owner, operation)
        raw,mime=self._request(owner,'GET','/sources/'+quote(_identity(operation),safe='')+'/bundle',
            artifact=True,response_limit=MAX_BUNDLE_RESPONSE)
        require(mime=='application/json','visual_sampling_bundle_invalid',502)
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):raise ServiceError('visual_sampling_bundle_invalid',502) from None
        require(type(value) is dict and set(value)=={'profile','document','sampling','files'}
            and type(value['files']) is dict and value['sampling']==manifest,'visual_sampling_bundle_invalid',502)
        files={}
        for name,row in value['files'].items():
            relative=Path(name)
            require(type(name) is str and not relative.is_absolute() and '..' not in relative.parts
                and '\\' not in name and type(row) is dict and set(row)=={'bytes','sha256','content_base64'},
                'visual_sampling_bundle_invalid',502)
            require(name in {'evidence.json','sampling-plan.json'} or (name.startswith('inputs/')
                and len(relative.parts)==2 and relative.parts[-1] not in {'.','..'}),
                'visual_sampling_bundle_invalid',502)
            try:content=base64.b64decode(row['content_base64'],validate=True)
            except (ValueError,binascii.Error):raise ServiceError('visual_sampling_bundle_invalid',502) from None
            require(len(content)==row['bytes'] and hashlib.sha256(content).hexdigest()==row['sha256'],
                'visual_sampling_bundle_changed',502)
            files[name]=content
        return value['profile'],value['document'],files,value['sampling']

    def cancel(self, owner, operation):
        return self._request(owner,'POST','/sources/'+quote(_identity(operation),safe='')+'/cancel',{})


def route_private(handler, owner, method, path, body=None):
    if not (path==PREFIX or path.startswith(PREFIX+'/')):return False
    service=getattr(handler.server,'sampling_transport',None)
    require(service is not None,'visual_sampling_not_configured',503)
    pieces=path[len(PREFIX):].strip('/').split('/')
    if method=='GET' and pieces==['capabilities']:
        handler._send(service.capabilities());return True
    if method=='POST' and pieces==['sources']:
        handler._send(service.register(owner,body),202);return True
    if len(pieces)>=2 and pieces[0]=='sources':
        operation=_identity(unquote(pieces[1]))
        if method=='GET':
            if len(pieces)==2:value=service.get(owner,operation)
            elif pieces[2:]==['manifest']:value=service.manifest(owner,operation)
            elif pieces[2:]==['frames']:value=service.frames_metadata(owner,operation)
            elif len(pieces)==4 and pieces[2]=='frames' and pieces[3].isdigit():
                raw,mime=service.frame(owner,operation,int(pieces[3]));handler._send(raw,raw=True,mime=mime);return True
            elif pieces[2:]==['bundle']:value=service.bundle(owner,operation)
            else:raise ServiceError('visual_sampling_not_found',404)
            handler._send(value);return True
        if method=='POST' and len(pieces)==3:
            if pieces[2]=='chunks':value=service.upload(owner,operation,body)
            elif pieces[2] in {'commit','cancel'}:
                require(body=={});value=getattr(service,pieces[2])(owner,operation)
            else:raise ServiceError('visual_sampling_not_found',404)
            handler._send(value,202)
            pump=getattr(handler.server,'sampling_pump',None)
            if pump is not None:pump.notify()
            return True
    raise ServiceError('visual_sampling_not_found',404)
