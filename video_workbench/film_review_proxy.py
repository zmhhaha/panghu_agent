"""Owner-bound formal-film review through the executor's existing v4 caller.

This is deliberately not an Agent gateway: only a validated frozen film review
and its own recorded operation can be submitted, inspected or cancelled. The
paid admission claim is durable before the one POST; uncertain admission only
looks up that original identity, including after an executor restart.
"""
from __future__ import annotations

import base64
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_review as visual
from .animation_observation import validate_model_frame_receipt


PROFILE = visual.REVIEW_PACKED_PROFILE
SCHEMA = 'video.film-review-request.v1'
MAX_BYTES = 32 * 1024 * 1024
MAX_BODY = 45 * 1024 * 1024
_IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_OWNER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,199}\Z')
_ACTIVE = {'queued', 'preparing', 'admitting', 'unknown', 'queued_remote', 'starting', 'running', 'cancelling'}


def require(value, code='film_review_request_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def identity(value):
    require(type(value) is str and _IDENTITY.fullmatch(value))
    return value


def validate_binding(value):
    require(type(value) is dict and set(value) == {'project_id', 'source', 'source_sha256'})
    identity(value['project_id'])
    source = value['source']
    require(type(source) is dict and set(source) == {'run_id', 'version_id', 'sha256'})
    identity(source['run_id'])
    identity(source['version_id'])
    require(type(source['sha256']) is str and _SHA.fullmatch(source['sha256'])
        and type(value['source_sha256']) is str and _SHA.fullmatch(value['source_sha256']))
    return deepcopy(value)


def validate_request(value):
    require(type(value) is dict and set(value) == {'schema', 'operation_id', 'project_id', 'source',
        'source_sha256', 'deadline_at', 'document', 'files'})
    require(value['schema'] == SCHEMA)
    identity(value['operation_id'])
    validate_binding({key: value[key] for key in ('project_id', 'source', 'source_sha256')})
    deadline = value['deadline_at']
    require(type(deadline) in (int, float) and math.isfinite(deadline) and deadline > 0)
    document = visual.validate_review_input(value['document'])
    require(document['source_sha256'] == value['source_sha256']
        and document['media']['sha256'] == value['source']['sha256'], 'film_review_source_changed', 409)
    encoded = value['files']
    expected = {visual.REVIEW_INPUT_NAME: digest(document)}
    expected.update({frame['file_name']: frame['sha256'] for frame in document['observation']['frames']})
    require(type(encoded) is dict and set(encoded) == set(expected), 'film_review_frame_files_invalid')
    files, total = {}, 0
    for name, checksum in expected.items():
        text = encoded[name]
        limit = visual.INPUT_MAX_BYTES if name == visual.REVIEW_INPUT_NAME else visual.MAX_IMAGE_BYTES
        require(type(text) is str and 0 < len(text) <= 4 * ((limit + 2) // 3),
            'film_review_frame_files_invalid', 413)
        try:
            content = base64.b64decode(text, validate=True)
        except (ValueError, UnicodeError):
            raise ServiceError('film_review_frame_files_invalid', 422) from None
        require(0 < len(content) <= limit and hashlib.sha256(content).hexdigest() == checksum,
            'film_review_frame_integrity')
        if name == visual.REVIEW_INPUT_NAME:
            require(content == canonical_bytes(document), 'film_review_document_bytes_changed')
        else:
            visual._image_header(content, name)
        files[name] = content
        total += len(content)
    require(total <= MAX_BYTES and len(canonical_bytes(value)) <= MAX_BODY,
        'film_review_input_too_large', 413)
    return deepcopy(value), files


class FilmReviewProxy:
    def __init__(self, root, review_gateway, *, clock=time.time):
        require(getattr(review_gateway, 'profile', None) == PROFILE, 'film_review_v4_required', 503)
        self.root, self.gateway, self.clock = Path(root).resolve(), review_gateway, clock
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'film-reviews.sqlite3'
        self._locks, self._lock_guard = {}, threading.Lock()
        with closing(self._connect()) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS film_reviews (
                owner TEXT NOT NULL, id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                request BLOB NOT NULL, binding_sha256 TEXT NOT NULL, remote_operation TEXT NOT NULL,
                state TEXT NOT NULL, job BLOB, result BLOB, cancel_requested INTEGER NOT NULL DEFAULT 0,
                error TEXT, PRIMARY KEY(owner,id), UNIQUE(remote_operation))''')
            db.commit()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _lock(self, owner, operation):
        with self._lock_guard:
            return self._locks.setdefault((owner, operation), threading.RLock())

    def _row(self, owner, operation, binding=None):
        require(type(owner) is str and _OWNER.fullmatch(owner), 'film_review_owner_invalid', 403)
        identity(operation)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM film_reviews WHERE owner=? AND id=?', (owner, operation)).fetchone()
        require(row is not None, 'film_review_not_found', 404)
        request = json.loads(row['request'])
        require(digest(request) == row['request_sha256'], 'film_review_record_changed', 409)
        if binding is not None:
            require(digest(validate_binding(binding)) == row['binding_sha256'], 'film_review_not_found', 404)
        return row, request

    def _update(self, owner, operation, **fields):
        allowed = {'state', 'job', 'result', 'cancel_requested', 'error'}
        require(set(fields) <= allowed)
        values = [canonical_bytes(value) if key in {'job', 'result'} and value is not None else value
            for key, value in fields.items()]
        with closing(self._connect()) as db:
            db.execute('UPDATE film_reviews SET ' + ','.join(key + '=?' for key in fields)
                + ' WHERE owner=? AND id=?', [*values, owner, operation])
            db.commit()

    def _transition(self, owner, operation, states, *, allow_cancel=False, **fields):
        """One shared-database CAS also fences simultaneous executor processes."""
        allowed = {'state', 'job', 'error'}
        require(set(fields) <= allowed and states)
        values = [canonical_bytes(value) if key == 'job' and value is not None else value
            for key, value in fields.items()]
        with closing(self._connect()) as db:
            cursor = db.execute('UPDATE film_reviews SET ' + ','.join(key + '=?' for key in fields)
                + ' WHERE owner=? AND id=? AND state IN (' + ','.join('?' for _ in states) + ')'
                + ('' if allow_cancel else ' AND cancel_requested=0'), [*values, owner, operation, *states])
            db.commit()
            return cursor.rowcount == 1

    def capabilities(self):
        self.gateway.preflight(self.clock() + 60)
        return dict(schema='video.film-review-capabilities.v1', enabled=True, profile=PROFILE,
            max_frames=visual.MAX_FRAMES, max_input_bytes=MAX_BYTES, max_timeout_seconds=600,
            evidence_scope='sampled_frames', audio_reviewed=False, continuous_playback_reviewed=False)

    def start(self, owner, request):
        require(type(owner) is str and _OWNER.fullmatch(owner), 'film_review_owner_invalid', 403)
        request, _ = validate_request(request)
        operation = request['operation_id']
        binding = {key: request[key] for key in ('project_id', 'source', 'source_sha256')}
        with self._lock(owner, operation), closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT request_sha256 FROM film_reviews WHERE owner=? AND id=?',
                (owner, operation)).fetchone()
            if previous is not None:
                require(previous['request_sha256'] == digest(request), 'film_review_operation_conflict', 409)
            else:
                require(0 < request['deadline_at'] - self.clock() <= 600, 'film_review_deadline_invalid', 409)
                remote = 'film-review-' + digest(dict(owner=owner, operation=operation, binding=binding))
                db.execute('INSERT INTO film_reviews(owner,id,request_sha256,request,binding_sha256,'
                    'remote_operation,state) VALUES(?,?,?,?,?,?,?)', (owner, operation, digest(request),
                    canonical_bytes(request), digest(binding), remote, 'queued'))
            db.commit()
        return self.get(owner, operation, binding)

    def get(self, owner, operation, binding):
        row, _ = self._row(owner, operation, binding)
        job = json.loads(row['job']) if row['job'] is not None else {}
        state = 'queued' if row['state'] in {'queued', 'preparing'} else 'starting' if row['state'] in {
            'admitting', 'unknown'} else 'queued' if row['state'] == 'queued_remote' else row['state']
        return dict(job_id=operation, state=state, stop_proven=job.get('stop_proven') is True,
            stop_proof=deepcopy(job.get('stop_proof')), error=row['error'], cancel_requested=bool(row['cancel_requested']))

    def pending(self, limit=100):
        with closing(self._connect()) as db:
            rows = db.execute('SELECT owner,id,state FROM film_reviews ORDER BY rowid').fetchall()
        return [dict(owner=row['owner'], id=row['id']) for row in rows if row['state'] in _ACTIVE][:limit]

    def advance(self, owner, operation):
        with self._lock(owner, operation):
            row, request = self._row(owner, operation)
            state = row['state']
            if state not in _ACTIVE:
                return
            if state in {'queued', 'preparing'}:
                if row['cancel_requested'] or self.clock() >= request['deadline_at']:
                    self._transition(owner, operation, {'queued', 'preparing'}, allow_cancel=True,
                        state='cancelled' if row['cancel_requested'] else 'timed_out',
                        job=dict(stop_proven=True, stop_proof=dict(schema='video.film-review-no-admission.v1',
                            operation_id=operation, request_sha256=row['request_sha256'],
                            reason='cancelled' if row['cancel_requested'] else 'deadline_expired',
                            paid_admission_attempted=False)))
                    return
                if not self._transition(owner, operation, {'queued', 'preparing'}, state='preparing'):
                    return
                try:
                    _, files = validate_request(request)
                    self.gateway.preflight(request['deadline_at'])
                    refs = self.gateway.upload(request['document'], files)
                    admit = self.gateway.prepare_submission(row['remote_operation'], request['document'],
                        request['deadline_at'], refs)
                except Exception:
                    # No paid admission has been attempted. Bounded preparation
                    # can recover on a later tick until the frozen deadline.
                    self._update(owner, operation, error='film_review_preparation_unavailable')
                    return
                if not self._transition(owner, operation, {'preparing'}, state='admitting', error=None):
                    return
                try:
                    job = admit()
                except Exception:
                    self._update(owner, operation, state='unknown', error='film_review_admission_unknown')
                    return
            else:
                try:
                    if row['job'] is None:
                        job = self.gateway.lookup(row['remote_operation'])
                    else:
                        saved = json.loads(row['job'])
                        job = self.gateway.poll(saved['job_id'])
                except ServiceError as error:
                    if error.status == 404 and state in {'admitting', 'unknown'}:
                        self._update(owner, operation, state='unknown', error='film_review_admission_unknown')
                    return
            require(type(job) is dict and type(job.get('job_id')) is str
                and job.get('state') in {'queued', 'starting', 'running', 'cancelling', 'completed',
                    'failed', 'needs_input', 'cancelled', 'timed_out', 'quarantined'}, 'film_review_job_invalid', 502)
            self._update(owner, operation, state='queued_remote' if job['state'] == 'queued' else job['state'],
                job=job, error=None)
            if row['cancel_requested'] and job['state'] in {'queued', 'starting', 'running', 'cancelling'}:
                # Only the job recovered from this ledger's exact operation can
                # ever reach the executor's private Agent client.
                self.gateway.client.cancel(job['job_id'])

    def lookup(self, owner, operation, binding):
        self._row(owner, operation, binding)
        self.advance(owner, operation)
        return self.get(owner, operation, binding)

    def result(self, owner, operation, binding):
        with self._lock(owner, operation):
            row, request = self._row(owner, operation, binding)
            require(row['state'] == 'completed' and row['job'] is not None, 'film_review_result_not_ready', 409)
            if row['result'] is not None:
                return json.loads(row['result'])
            job = json.loads(row['job'])
            document = request['document']
            result = self.gateway.collect(job['job_id'], digest(document), document)
            result = visual.validate_review_result(result, digest(document), document)
            control = self.gateway.model_frame_receipt(job['job_id'])
            require(control.get('job_id') == job['job_id'], 'film_review_result_binding_invalid', 409)
            validate_model_frame_receipt(document, control)
            value = dict(result=result, model_frame_receipt=control)
            self._update(owner, operation, result=value)
            return deepcopy(value)

    def cancel(self, owner, operation, binding):
        with self._lock(owner, operation):
            self._row(owner, operation, binding)
            self._update(owner, operation, cancel_requested=1)
            self.advance(owner, operation)
            return self.get(owner, operation, binding)
