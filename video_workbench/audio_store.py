"""Owner-scoped, immutable narration candidates and durable one-attempt work.

Audio has its own tables and approval pointer: visual operations and adoptions
do not implicitly modify speech. Byte payloads live in the existing SQLite
volume, so candidates cannot select or traverse filesystem paths.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
from uuid import uuid4
import wave

from agent_service.contracts import ServiceError, digest
from .speech import inspect_wav

AUDIO_ACTIVE = {'queued', 'running', 'cancelling'}
MAX_PROJECT_AUDIO_BYTES = 512 * 1024 * 1024
MAX_SCENE_AUDIO_VERSIONS = 64


def _id():
    return str(uuid4())


def _text(value, maximum):
    return (type(value) is str and 1 <= len(value) <= maximum and value.strip()
            and not any((ord(c) < 32 and c not in '\t\n\r') or ord(c) == 127
                        or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def speech_options(speed=1.0, pronunciation=None):
    if type(speed) not in {int, float} or not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise ServiceError('speech_invalid_input', 422)
    pronunciation = {} if pronunciation is None else pronunciation
    if (type(pronunciation) is not dict or len(pronunciation) > 32
            or any(not _text(k, 80) or not _text(v, 200) for k, v in pronunciation.items())):
        raise ServiceError('speech_invalid_input', 422)
    return dict(speed=float(speed), pronunciation=dict(pronunciation))


def narration_document(scene, provider):
    settings = dict(provider, **speech_options(**{
        k: scene.get('speech_settings', {}).get(k, default)
        for k, default in [('speed', 1.0), ('pronunciation', {})]
    }))
    narration = scene['narration']
    if not _text(narration, 10000):
        raise ServiceError('speech_invalid_input', 422)
    # A single longest-match substitution avoids replacements cascading into
    # each other. Both the original narration and explicit rules are bound.
    rules = settings['pronunciation']
    spoken = re.sub('|'.join(re.escape(k) for k in sorted(rules, key=len, reverse=True)),
                    lambda match: rules[match.group()], narration) if rules else narration
    return dict(narration=narration, spoken_text=spoken, settings=settings)


def narration_binding(document):
    return digest(dict(narration=document['narration'], settings=document['settings']))


def _wav(data):
    info = inspect_wav(data)
    with wave.open(io.BytesIO(data), 'rb') as stream:
        frames = stream.readframes(stream.getnframes())
    # Unsigned 8-bit PCM silence is 128; wider PCM silence is all zero.
    silent = not any(value != 128 for value in frames) if info.sample_width == 1 else not any(frames)
    if silent:
        raise ServiceError('speech_invalid_audio', 502)
    return info


class AudioStoreMixin:
    def _init_audio_schema(self, db):
        self.speech_provider = dict(model='kokoro-rk3588', voice='zm_yunjian')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS audio_versions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                scene_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                document TEXT NOT NULL, data BLOB NOT NULL);
            CREATE INDEX IF NOT EXISTS audio_versions_scene ON audio_versions(project_id, scene_id);
            CREATE TABLE IF NOT EXISTS audio_operations (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                scene_id TEXT NOT NULL, idem TEXT NOT NULL, request_hash TEXT NOT NULL,
                document TEXT NOT NULL, UNIQUE(owner, idem));
        ''')

    def configure_speech_provider(self, model, voice):
        if (not _text(model, 200) or not _text(voice, 200)
                or any(ord(c) < 32 for c in model + voice)):
            raise ValueError('An explicit speech model and voice are required')
        self.speech_provider = dict(model=model, voice=voice)

    @staticmethod
    def _audio_public(op):
        return {key: op.get(key) for key in
                ('id', 'kind', 'status', 'error', 'candidate_id', 'created_at', 'upstream_stop_confirmed')}

    @staticmethod
    def _audio_scene_project(db, owner, scene_id):
        for row in db.execute('SELECT document FROM projects WHERE owner=?', (owner,)):
            project = json.loads(row[0])
            if any(scene['id'] == scene_id for scene in project['scenes']):
                return project['id']
        raise ServiceError('not_found', 404)

    def _attach_audio(self, db, project):
        operations = [json.loads(row[0]) for row in db.execute(
            'SELECT document FROM audio_operations WHERE project_id=? ORDER BY rowid', (project['id'],))]
        for scene in project['scenes']:
            document = narration_document(scene, self.speech_provider)
            binding = narration_binding(document)
            scene['speech_settings'] = document['settings']
            scene['audio_input_sha256'] = binding
            scene.setdefault('head_audio_version_id', None)
            scene['audio_versions'] = [dict(json.loads(row[0])) for row in db.execute(
                'SELECT document FROM audio_versions WHERE project_id=? AND scene_id=? ORDER BY rowid',
                (project['id'], scene['id']))]
            for candidate in scene['audio_versions']:
                candidate['stale'] = candidate['input_sha256'] != binding
            adopted = next((v for v in scene['audio_versions'] if v['id'] == scene['head_audio_version_id']), None)
            scene['audio_stale'] = bool(scene['head_audio_version_id'] and (adopted is None or adopted['stale']))
            scene['audio_operation'] = next((self._audio_public(op) for op in reversed(operations)
                                             if op['scene_id'] == scene['id']), None)
            captions = scene.get('captions')
            if isinstance(captions, dict):
                captions['stale'] = (adopted is None or scene['audio_stale']
                    or captions.get('audio_version_id') != adopted['id']
                    or captions.get('audio_sha256') != adopted['sha256'])
        return project

    def save_speech_settings(self, owner, scene_id, request):
        options = speech_options(request.get('speed', 1.0), request.get('pronunciation', {}))
        project_id = self.scene_project(owner, scene_id)
        with self.transaction() as db:
            project = self._project(db, owner, project_id)
            self._revision(project, request['expected_revision'])
            self._scene(project, scene_id)['speech_settings'] = options
            self._save(db, project)
        return self.project(owner, project_id)

    @staticmethod
    def _audio_replay(db, owner, idem, fingerprint):
        if type(idem) is not str or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem):
            raise ServiceError('idempotency_key_required', 422)
        row = db.execute('SELECT request_hash,document FROM audio_operations WHERE owner=? AND idem=?',
                         (owner, idem)).fetchone()
        if row and row[0] != fingerprint:
            raise ServiceError('idempotency_conflict', 409)
        return json.loads(row[1]) if row else None

    def start_audio(self, owner, scene_id, request, idem, *, timeout=1800):
        fingerprint = digest(dict(kind='speech', scene_id=scene_id, request=request))
        with self.transaction() as db:
            previous = self._audio_replay(db, owner, idem, fingerprint)
            if previous:
                return self._audio_public(previous)
            project_id = self._audio_scene_project(db, owner, scene_id)
            project = self._project(db, owner, project_id)
            self._revision(project, request['expected_revision'])
            for row in db.execute('SELECT document FROM audio_operations WHERE project_id=? AND scene_id=?',
                                  (project_id, scene_id)):
                if json.loads(row[0])['status'] in AUDIO_ACTIVE:
                    raise ServiceError('audio_operation_in_progress', 409)
            document = narration_document(self._scene(project, scene_id), self.speech_provider)
            if len(document['spoken_text']) > 2000 or len(document['spoken_text'].encode()) > 16 * 1024:
                raise ServiceError('audio_narration_too_long', 422)
            now = time.time()
            settings = document['settings']
            provider_hash = digest(dict(model=settings['model'], input=document['spoken_text'],
                voice=settings['voice'], speed=settings['speed'], response_format='wav'))
            op = dict(id=_id(), owner=owner, project_id=project_id, scene_id=scene_id, kind='speech',
                status='queued', error=None, created_at=now, deadline_at=now + timeout,
                submit_attempted=False, cancel_requested=False, candidate_id=None,
                upstream_stop_confirmed=None, input_sha256=narration_binding(document),
                provider_input_sha256=provider_hash, input=document)
            db.execute('INSERT INTO audio_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (op['id'], owner, project_id, scene_id, idem, fingerprint, json.dumps(op)))
            self._save(db, project)
        return self._audio_public(op)

    def _insert_audio(self, db, project, scene_id, op, data, info, *, origin, source_name):
        count = db.execute('SELECT count(*) FROM audio_versions WHERE project_id=? AND scene_id=?',
                           (project['id'], scene_id)).fetchone()[0]
        total = db.execute('SELECT coalesce(sum(length(data)),0) FROM audio_versions WHERE project_id=?',
                           (project['id'],)).fetchone()[0]
        if count >= MAX_SCENE_AUDIO_VERSIONS or total + len(data) > MAX_PROJECT_AUDIO_BYTES:
            raise ServiceError('audio_storage_limit', 409)
        version_id = _id()
        metadata = dict(id=version_id, number=count + 1, origin=origin, source=origin,
            type='audio/wav', model=op['input']['settings']['model'], voice=op['input']['settings']['voice'],
            speed=op['input']['settings']['speed'],
            source_name=source_name, operation_id=op['id'], created_at=time.time(),
            input_sha256=op['input_sha256'], narration_sha256=hashlib.sha256(op['input']['narration'].encode()).hexdigest(),
            narration=op['input']['narration'], settings=op['input']['settings'],
            sha256=hashlib.sha256(data).hexdigest(), audio_bytes=len(data), **vars(info))
        db.execute('INSERT INTO audio_versions VALUES (?, ?, ?, ?, ?, ?)',
                   (version_id, project['id'], scene_id, op['id'], json.dumps(metadata), data))
        return version_id

    def upload_audio(self, owner, scene_id, request, idem, data, source_name='upload.wav'):
        if (not _text(source_name, 200) or any(c in source_name for c in '/\\')
                or any(ord(c) < 32 for c in source_name)):
            raise ServiceError('audio_invalid_filename', 422)
        # Validate before beginning a write transaction or allocating a BLOB.
        try:
            info = _wav(data)
        except ServiceError as exc:
            # This input came from the user, not an upstream TTS provider.
            raise ServiceError(exc.code, 422) from None
        fingerprint = digest(dict(kind='audio_upload', scene_id=scene_id, request=request,
                                  data_sha256=hashlib.sha256(data).hexdigest(), source_name=source_name))
        with self.transaction() as db:
            previous = self._audio_replay(db, owner, idem, fingerprint)
            if previous:
                project_id = previous['project_id']
            else:
                project_id = self._audio_scene_project(db, owner, scene_id)
                project = self._project(db, owner, project_id)
                self._revision(project, request['expected_revision'])
                document = narration_document(self._scene(project, scene_id), self.speech_provider)
                # A replacement recording binds to original text and settings;
                # it does not need a potentially expanded synthesis string.
                document.pop('spoken_text', None)
                op = dict(id=_id(), owner=owner, project_id=project_id, scene_id=scene_id, kind='audio_upload',
                    status='completed', error=None, created_at=time.time(), candidate_id=None,
                    submit_attempted=False, cancel_requested=False, upstream_stop_confirmed=True,
                    input_sha256=narration_binding(document), input=document)
                op['candidate_id'] = self._insert_audio(db, project, scene_id, op, data, info,
                                                      origin='upload', source_name=source_name)
                db.execute('INSERT INTO audio_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (op['id'], owner, project_id, scene_id, idem, fingerprint, json.dumps(op)))
                self._save(db, project)
        return self.project(owner, project_id)

    def audio_version(self, owner, version_id):
        with self.transaction() as db:
            row = db.execute('SELECT a.document,a.data,a.project_id,a.scene_id FROM audio_versions a '
                             'JOIN projects p ON a.project_id=p.id WHERE a.id=? AND p.owner=?',
                             (version_id, owner)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            metadata = json.loads(row['document'])
            return dict(metadata, data=bytes(row['data']), project_id=row['project_id'], scene_id=row['scene_id'])

    def adopt_audio(self, owner, scene_id, version_id, expected_head):
        project_id = self.scene_project(owner, scene_id)
        with self.transaction() as db:
            project = self._project(db, owner, project_id)
            scene = self._scene(project, scene_id)
            if scene.get('head_audio_version_id') != expected_head:
                raise ServiceError('audio_head_conflict', 409)
            row = db.execute('SELECT document FROM audio_versions WHERE id=? AND project_id=? AND scene_id=?',
                             (version_id, project_id, scene_id)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            metadata = json.loads(row[0])
            if metadata['input_sha256'] != narration_binding(narration_document(scene, self.speech_provider)):
                raise ServiceError('audio_stale', 409)
            scene['head_audio_version_id'] = version_id
            self._save(db, project)
        return self.project(owner, project_id)

    def audio_operation(self, operation_id, owner=None):
        with self.transaction() as db:
            row = db.execute('SELECT owner,document FROM audio_operations WHERE id=?', (operation_id,)).fetchone()
            if row is None or owner is not None and row['owner'] != owner:
                raise ServiceError('not_found', 404)
            return json.loads(row['document'])

    def active_audio_operations(self):
        with self.transaction() as db:
            return [op for row in db.execute('SELECT document FROM audio_operations ORDER BY rowid')
                    if (op := json.loads(row[0]))['status'] in AUDIO_ACTIVE]

    def recover_audio_operations(self):
        """A synchronous provider has no lookup API: never repeat an ambiguous POST."""
        with self.transaction() as db:
            for row in db.execute('SELECT id,document FROM audio_operations'):
                op = json.loads(row['document'])
                if op['status'] in AUDIO_ACTIVE and op['submit_attempted']:
                    op.update(status='cancelled' if op['cancel_requested'] else 'failed',
                        error='speech_upstream_stop_unconfirmed' if op['cancel_requested'] else 'speech_submission_uncertain',
                        upstream_stop_confirmed=False)
                    db.execute('UPDATE audio_operations SET document=? WHERE id=?', (json.dumps(op), op['id']))

    def claim_audio(self, operation_id):
        with self.transaction() as db:
            row = db.execute('SELECT document FROM audio_operations WHERE id=?', (operation_id,)).fetchone()
            if row is None:
                return None
            op = json.loads(row[0])
            if op['status'] not in AUDIO_ACTIVE or op['submit_attempted']:
                return None
            if op['cancel_requested'] or time.time() >= op['deadline_at']:
                op.update(status='cancelled' if op['cancel_requested'] else 'timed_out', error=None)
                claimed = None
            else:
                op.update(status='running', submit_attempted=True, upstream_stop_confirmed=False)
                claimed = op
            db.execute('UPDATE audio_operations SET document=? WHERE id=?', (json.dumps(op), operation_id))
            return claimed

    def finish_audio(self, operation_id, *, audio=None, error=None, interrupted=False):
        info = _wav(audio.data) if audio is not None else None
        with self.transaction() as db:
            op = json.loads(db.execute('SELECT document FROM audio_operations WHERE id=?', (operation_id,)).fetchone()[0])
            if op['status'] not in AUDIO_ACTIVE:
                return
            project = self._project(db, op['owner'], op['project_id'])
            if op['cancel_requested']:
                op.update(status='cancelled', error='speech_upstream_stop_unconfirmed', upstream_stop_confirmed=False)
            elif interrupted:
                op.update(status='failed', error='speech_submission_uncertain', upstream_stop_confirmed=False)
            elif error:
                op.update(status='failed', error=error, upstream_stop_confirmed=False)
            elif time.time() >= op['deadline_at']:
                op.update(status='timed_out', error='speech_timeout', upstream_stop_confirmed=False)
            elif audio is not None:
                if audio.input_sha256 != op['provider_input_sha256'] or hashlib.sha256(audio.data).hexdigest() != audio.sha256:
                    raise ServiceError('speech_input_mismatch', 502)
                op['candidate_id'] = self._insert_audio(db, project, op['scene_id'], op, audio.data, info,
                                                       origin='tts', source_name='generated.wav')
                op.update(status='completed', error=None, upstream_stop_confirmed=True)
            else:
                raise ValueError('A result or explicit error is required')
            db.execute('UPDATE audio_operations SET document=? WHERE id=?', (json.dumps(op), operation_id))
            self._save(db, project)

    def cancel_audio(self, owner, operation_id):
        with self.transaction() as db:
            row = db.execute('SELECT document FROM audio_operations WHERE id=? AND owner=?', (operation_id, owner)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            op = json.loads(row[0])
            if op['status'] in AUDIO_ACTIVE:
                op.update(cancel_requested=True,
                    status='cancelling' if op['submit_attempted'] else 'cancelled',
                    upstream_stop_confirmed=not op['submit_attempted'])
                db.execute('UPDATE audio_operations SET document=? WHERE id=?', (json.dumps(op), operation_id))
            return self._audio_public(op)
