"""Durable composable-film child intents in the existing business database.

The workflow performs external I/O after these transactions commit. Candidates
and whole-film media remain immutable and are never adopted by this store.
"""
from copy import deepcopy
import hashlib
import json
import math
import re
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_film_module import PROFILE, BUNDLE_MANIFEST_SHA256
from agent_service.profiles.video_animation_film_module_contract import CONTEXT_SCHEMA
from agent_service.profiles.video_animation_production_contract import validate_film_context
from .animation_runtime_capabilities import validate_runtime_capabilities
from .audio_store import narration_binding, narration_document
from .creative_film_module_contract import (prepare_core_task, build_module_input,
    collect_module_result, validate_module_package, predecessor_from_package)
from .film_production_assembly import assemble_film_production
from .film_render_adapter import validate_film_render_result
from .hyperframes_film_snapshot import _release, verify_film_execution_snapshot
from .motion import make_beats, estimated_alignment
from .motion_snapshot import _audio
from .production_film_context import freeze_production_inputs

ACTIVE = frozenset({'queued', 'submitting', 'running', 'cancelling'})
TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
PENDING_SCHEMA = 'video.film-module-pending.v1'
_TABLES = {'module': 'film_module_operations', 'render': 'film_render_operations'}


def require(value, code='film_creation_request_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def packed(value):
    return canonical_bytes(value).decode('utf-8')


class FilmCreationStore:
    def __init__(self, store, *, runtime_capabilities=None, runtime_fingerprint=None, host_files=None,
            trusted_host_sha256=None, image=None, assets=None, enabled=False, _mode='configured'):
        require(type(enabled) is bool)
        self.store, self.enabled = store, enabled
        require(_mode in {'configured', 'recovery'}, 'film_creation_configuration_invalid')
        if _mode == 'recovery':
            require(not enabled and all(value is None for value in (runtime_capabilities,
                runtime_fingerprint, host_files, trusted_host_sha256, image, assets)),
                'film_creation_configuration_invalid')
            self.runtime_capabilities = self.runtime = self.hosts = self.image = None
            self.host_files = self.assets = self.configuration = None
            self._init_schema()
            return
        self.runtime_capabilities = validate_runtime_capabilities(runtime_capabilities)
        self.runtime, self.hosts = _release(runtime_fingerprint, image, trusted_host_sha256)
        self.image, self.host_files, self.assets = image, deepcopy(host_files), deepcopy(assets)
        caps = self.runtime_capabilities
        require(type(assets) is dict and caps['runtime_identity']['image_digest'] == self.runtime['image_digest']
            and caps['runtime_identity']['engine_runtime_sha256'] == self.runtime['engine_runtime_sha256']
            and caps['runtime_identity']['host_sha256'] == digest(self.hosts)
            and {name: hashlib.sha256(raw).hexdigest() for name, raw in assets.items()}
                == {'assets/' + name: checksum for name, checksum in caps['assets'].items()}
            and set(host_files) == set(self.hosts)
            and all(hashlib.sha256(raw).hexdigest() == self.hosts[name] for name, raw in host_files.items()),
            'film_creation_configuration_invalid')
        self.configuration = dict(schema='video.film-creation-configuration.v1', profile=PROFILE, profile_revision='1',
            bundle_manifest_sha256=BUNDLE_MANIFEST_SHA256, runtime_capabilities=deepcopy(caps),
            runtime_fingerprint=self.runtime, trusted_host_sha256=self.hosts, image=image,
            renderer_mode='hyperframes-film', max_seconds=60, fps=24)
        self.configuration['sha256'] = digest(self.configuration)
        self._init_schema()

    @classmethod
    def for_recovery(cls, store):
        """Read/drain persisted children without authorizing any new release.

        Submitted operations retain their own exact release identities. This
        mode never invents current capabilities or borrows another Agent role.
        """
        return cls(store, _mode='recovery')

    def _init_schema(self):
        with self.store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS film_module_operations (
                    id TEXT PRIMARY KEY,owner TEXT NOT NULL,project_id TEXT NOT NULL,scene_id TEXT NOT NULL,
                    idem TEXT NOT NULL,request_hash TEXT NOT NULL,document TEXT NOT NULL,UNIQUE(owner,idem));
                CREATE TABLE IF NOT EXISTS film_module_versions (
                    id TEXT PRIMARY KEY,owner TEXT NOT NULL,project_id TEXT NOT NULL,scene_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,document TEXT NOT NULL,package TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS film_render_operations (
                    id TEXT PRIMARY KEY,owner TEXT NOT NULL,project_id TEXT NOT NULL,idem TEXT NOT NULL,
                    request_hash TEXT NOT NULL,document TEXT NOT NULL,snapshot TEXT NOT NULL,data BLOB,
                    UNIQUE(owner,idem));
                CREATE INDEX IF NOT EXISTS film_module_project ON film_module_operations(project_id,scene_id);
                CREATE INDEX IF NOT EXISTS film_render_project ON film_render_operations(project_id);
            ''')

    @property
    def configuration_sha256(self):
        return None if self.configuration is None else self.configuration['sha256']

    @staticmethod
    def public(op, *, current=True):
        names = ('id', 'kind', 'project_id', 'scene_id', 'request_key', 'request_sha256', 'status',
            'error', 'job_id', 'version_id', 'film_sha256', 'input_sha256', 'snapshot_sha256',
            'configuration_sha256', 'stop_proven', 'cancel_requested', 'created_at', 'finished_at')
        result = {name: op.get(name) for name in names}
        if op['kind'] == 'render':
            result.update(renderer_mode='hyperframes-film', visual_mode='hyperframes-film', quality='draft',
                duration_seconds=op['duration_seconds'], result=deepcopy(op.get('result')),
                actual_duration_seconds=op['result']['duration_seconds'] if op.get('result') else None,
                selection='draft_only', content_state='current' if current else 'stale')
            if op.get('result'):
                result.update({key: value for key, value in op['result'].items() if key != 'path'})
        return result

    @staticmethod
    def _read(db, kind, operation_id, owner=None):
        row = db.execute('SELECT owner,document FROM ' + _TABLES[kind] + ' WHERE id=?', (operation_id,)).fetchone()
        if row is None or owner is not None and row['owner'] != owner:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _write(db, op):
        db.execute('UPDATE ' + _TABLES[op['kind']] + ' SET document=? WHERE id=?', (packed(op), op['id']))

    def operation(self, kind, operation_id, owner=None):
        with self.store.transaction() as db:
            op = self._read(db, kind, operation_id, owner)
            if owner is not None:
                self.store._project(db, owner, op['project_id'])
            return op

    def _get(self, owner, operation_id, kind):
        with self.store.transaction() as db:
            op = self._read(db, kind, operation_id, owner)
            project = self.store._project(db, owner, op['project_id'])
            return self.public(op, current=self._current(op, project))

    def _current(self, op, project):
        try:
            return op['request']['film']['content_binding_sha256'] == digest(
                freeze_production_inputs(self.store, project, film_aware=True))
        except (ServiceError, KeyError, TypeError, ValueError):
            # Current invalid editing state does not erase historical media.
            return False

    def get_module(self, owner, operation_id):
        return self._get(owner, operation_id, 'module')

    def get_render(self, owner, operation_id):
        return self._get(owner, operation_id, 'render')

    def _by_key(self, owner, idem, kind):
        with self.store.transaction() as db:
            row = db.execute('SELECT document FROM ' + _TABLES[kind] + ' WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            op = json.loads(row[0])
            project = self.store._project(db, owner, op['project_id'])
            return self.public(op, current=self._current(op, project))

    def module_by_request_key(self, owner, idem):
        return self._by_key(owner, idem, 'module')

    def render_by_request_key(self, owner, idem):
        return self._by_key(owner, idem, 'render')

    def _replay(self, db, owner, idem, kind, fingerprint):
        require(type(idem) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem), 'idempotency_key_required')
        row = db.execute('SELECT request_hash,document FROM ' + _TABLES[kind] + ' WHERE owner=? AND idem=?', (owner, idem)).fetchone()
        if row is not None:
            require(row[0] == fingerprint, 'idempotency_conflict', 409)
            op = json.loads(row[1])
            project = self.store._project(db, owner, op['project_id'])
            return self.public(op, current=self._current(op, project))

    def _film(self, db, owner, project_id, request):
        project = self.store._project(db, owner, project_id)
        self.store._revision(project, request['expected_revision'])
        film = request['film']
        validate_film_context(dict(schema='video.film-context.v1', film=film, film_sha256=request['film_sha256'],
            current_scene_index=0, predecessor=None))
        frozen = freeze_production_inputs(self.store, project, film_aware=True)
        joint = frozen.get('joint_plan') or {}
        require(film['project_id'] == project_id
            and film['content_binding_sha256'] == digest(frozen)
            and film['title'] == frozen['title'] and film['script'] == frozen['script']
            and film['understanding_goal'] == (joint.get('understanding_goal') or joint.get('summary') or frozen['title'])
            and film['style'] == (joint.get('visual_style') or frozen['style'])
            and (not joint.get('version_id') or film['joint_plan_version_id'] == joint['version_id'])
            and [row['scene_id'] for row in film['scenes']] == [scene['id'] for scene in project['scenes']],
            'film_creation_project_binding_mismatch', 409)
        for row, scene in zip(film['scenes'], frozen['scenes']):
            duration = math.ceil((row['audio_seconds'] + scene['leading_seconds'] + scene['trailing_seconds']) * 24) / 24
            require(row['title'] == scene['title'] and row['visual_intent'] == scene['visual_intent']
                and row['leading_silence_seconds'] == scene['leading_seconds']
                and abs(row['end_seconds'] - row['start_seconds'] - duration) < 1e-8,
                'film_creation_project_binding_mismatch', 409)
        require(film['total_seconds'] <= 60, 'film_creation_duration_budget', 422)
        return project, film

    def _recordings(self, db, project, film):
        result = []
        voices = set()
        for row in film['scenes']:
            scene = self.store._scene(project, row['scene_id'])
            record = db.execute('SELECT document,data FROM audio_versions WHERE id=? AND project_id=? AND scene_id=?',
                (row['audio_version_id'], project['id'], row['scene_id'])).fetchone()
            require(record is not None, 'film_creation_recording_missing', 409)
            audio = dict(json.loads(record[0]), data=bytes(record[1]))
            require(audio['input_sha256'] == narration_binding(narration_document(scene,
                self.store._project_speech_provider(project))), 'film_creation_recording_stale', 409)
            checked = _audio(audio)
            require(checked['sha256'] == row['audio_sha256'] and checked['duration_seconds'] == row['audio_seconds']
                and scene['narration'] == row['narration'], 'film_creation_recording_binding_mismatch', 409)
            if audio.get('origin') == 'tts':
                voices.add((audio.get('model'), audio.get('voice')))
            result.append(dict(scene_id=row['scene_id'], audio_version_id=row['audio_version_id'],
                sha256=checked['sha256'], spoken_text=checked['spoken_text'], data=audio['data']))
        require(len(voices) <= 1, 'film_creation_voice_conflict', 409)
        return result

    def _version(self, db, owner, version_id):
        require(type(version_id) is str and 1 <= len(version_id) <= 100)
        row = db.execute('SELECT v.document,v.package FROM film_module_versions v JOIN projects p ON p.id=v.project_id '
            'WHERE v.id=? AND v.owner=? AND p.owner=?', (version_id, owner, owner)).fetchone()
        if row is None:
            raise ServiceError('not_found', 404)
        version, package = json.loads(row[0]), validate_module_package(json.loads(row[1]))
        self.store._project(db, owner, version['project_id'])
        require(version['package_sha256'] == digest(package), 'film_creation_package_changed', 409)
        return version, package

    def module_package(self, owner, version_id):
        with self.store.transaction() as db:
            return self._version(db, owner, version_id)[1]

    def module_predecessor(self, owner, version_id):
        return dict(version_id=version_id, predecessor=predecessor_from_package(self.module_package(owner, version_id)))

    def _configured(self, request):
        require(self.enabled, 'film_creation_disabled', 503)
        require(request['configuration_sha256'] == self.configuration_sha256,
            'film_creation_configuration_changed', 409)

    @staticmethod
    def _validate_request(request, fields):
        require(type(request) is dict and set(request) == set(fields)
            and type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        require(len(canonical_bytes(request)) <= 4 * 1024 * 1024, 'film_creation_request_budget', 413)

    def start_module(self, owner, project_id, scene_id, request, idem):
        self._validate_request(request, {'expected_revision', 'film', 'film_sha256', 'current_scene_index',
            'predecessor_version_id', 'target', 'instruction', 'configuration_sha256'})
        fingerprint = digest(dict(project_id=project_id, scene_id=scene_id, request=request))
        with self.store.transaction() as db:
            if replay := self._replay(db, owner, idem, 'module', fingerprint):
                return replay
            self._configured(request)
            project, film = self._film(db, owner, project_id, request)
            recordings = self._recordings(db, project, film)
            index = request['current_scene_index']
            require(type(index) is int and 0 <= index < len(film['scenes'])
                and film['scenes'][index]['scene_id'] == scene_id)
            predecessor = predecessor_package = None
            if index:
                previous, package = self._version(db, owner, request['predecessor_version_id'])
                require(previous['project_id'] == project_id and previous['scene_id'] == film['scenes'][index - 1]['scene_id']
                    and previous['film_sha256'] == request['film_sha256'], 'film_creation_predecessor_mismatch', 409)
                predecessor = predecessor_from_package(package)
                predecessor_package = package
            else:
                require(request['predecessor_version_id'] is None, 'film_creation_predecessor_mismatch', 409)
            row, recording = film['scenes'][index], recordings[index]
            target = request['target']
            require(type(target) is dict and set(target) == {'module_id', 'namespace', 'transition_target'})
            caps = self.runtime_capabilities
            beats = make_beats(row['narration'], recording['spoken_text'])
            core = prepare_core_task(scene_id=scene_id, title=row['title'], visual_description=row['visual_intent'],
                narration=row['narration'], beats=beats,
                alignment=estimated_alignment(beats, row['audio_sha256'], row['audio_seconds']),
                audio_sha256=row['audio_sha256'], duration_seconds=row['audio_seconds'],
                film_context=dict(schema=CONTEXT_SCHEMA, film=film, film_sha256=request['film_sha256'],
                    current_scene_index=index, predecessor=predecessor), instruction=request['instruction'],
                module_id=target['module_id'], namespace=target['namespace'], transition_target=target['transition_target'],
                runtime_identity=caps['runtime_identity'], allowed_engines=caps['allowed_engines'],
                assets={'assets/' + name: checksum for name, checksum in caps['assets'].items()}, style=film['style'],
                predecessor_package=predecessor_package)
            require(db.execute('SELECT count(*) FROM film_module_operations WHERE project_id=?', (project_id,)).fetchone()[0] < 256,
                'film_creation_operation_limit', 409)
            now = time.time()
            op = dict(id=str(uuid4()), kind='module', owner=owner, project_id=project_id, scene_id=scene_id,
                request_key=idem, request_sha256=digest(request), request=deepcopy(request),
                configuration_sha256=self.configuration_sha256, configuration=deepcopy(self.configuration),
                status='queued', error=None, created_at=now, finished_at=None, deadline_at=now + 600,
                document=dict(schema=PENDING_SCHEMA, core_task=core), input_sha256=None,
                film_sha256=request['film_sha256'], predecessor_version_id=request['predecessor_version_id'],
                version_id=None, input_ref=None, job_id=None, submit_attempted=False, cancel_requested=False,
                cancel_attempts=0, next_cancel_at=0, stop_proven=False)
            db.execute('INSERT INTO film_module_operations VALUES(?,?,?,?,?,?,?)',
                (op['id'], owner, project_id, scene_id, idem, fingerprint, packed(op)))
            return self.public(op)

    def prepare_module(self, operation_id, adapter):
        op = self.operation('module', operation_id)
        if op['document'].get('schema') != PENDING_SCHEMA or op['cancel_requested']:
            return op
        core = op['document']['core_task']
        delivery = adapter.freeze(op['owner'], 'video-film-module:' + op['id'], core, core['constraints']['allowed_engines'])
        document = build_module_input(core, delivery)
        with self.store.transaction() as db:
            current = self._read(db, 'module', operation_id)
            if current['document'].get('schema') != PENDING_SCHEMA:
                require(current['document'] == document, 'film_creation_frozen_input_changed', 409)
            elif current['status'] in ACTIVE and not current['cancel_requested']:
                require(current['document'] == op['document'] and not current['submit_attempted'],
                    'film_creation_frozen_input_changed', 409)
                current.update(document=document, input_sha256=digest(document))
                self._write(db, current)
            return current

    def start_render(self, owner, project_id, request, idem):
        self._validate_request(request, {'expected_revision', 'film', 'film_sha256', 'module_version_ids',
            'module_targets', 'production_run_id', 'timeout_seconds', 'configuration_sha256'})
        fingerprint = digest(dict(project_id=project_id, request=request))
        with self.store.transaction() as db:
            if replay := self._replay(db, owner, idem, 'render', fingerprint):
                return replay
            self._configured(request)
            project, film = self._film(db, owner, project_id, request)
            ids = request['module_version_ids']
            require(type(ids) is list and len(ids) == len(film['scenes'])
                and all(type(value) is str and 1 <= len(value) <= 100 for value in ids)
                and len(set(ids)) == len(ids))
            packages = []
            for index, version_id in enumerate(ids):
                version, package = self._version(db, owner, version_id)
                require(version['project_id'] == project_id and version['scene_id'] == film['scenes'][index]['scene_id']
                    and version['film_sha256'] == request['film_sha256'], 'film_creation_render_module_mismatch', 409)
                packages.append(package)
            assembled = assemble_film_production(packages=packages, film=film, film_sha256=request['film_sha256'],
                recordings=self._recordings(db, project, film), assets=self.assets, runtime_fingerprint=self.runtime,
                runtime_identity=self.runtime_capabilities['runtime_identity'], module_targets=request['module_targets'],
                host_files=self.host_files, trusted_host_sha256=self.hosts, image=self.image,
                project_revision=request['expected_revision'], production_run_id=request['production_run_id'],
                timeout_seconds=request['timeout_seconds'])
            require(db.execute('SELECT count(*) FROM film_render_operations WHERE project_id=?', (project_id,)).fetchone()[0] < 64,
                'film_creation_render_limit', 409)
            now = time.time()
            snapshot = assembled['snapshot']
            op = dict(id=str(uuid4()), kind='render', owner=owner, project_id=project_id, request_key=idem,
                request_sha256=digest(request), request=deepcopy(request), status='queued', error=None,
                created_at=now, finished_at=None, deadline_at=now + request['timeout_seconds'],
                duration_seconds=film['total_seconds'], film_sha256=request['film_sha256'],
                snapshot_sha256=snapshot['sha256'], assembly_manifest=assembled['manifest'],
                configuration_sha256=self.configuration_sha256, configuration=deepcopy(self.configuration),
                submit_attempted=False, cancel_requested=False, stop_proven=False, result=None)
            db.execute('INSERT INTO film_render_operations VALUES(?,?,?,?,?,?,?,NULL)',
                (op['id'], owner, project_id, idem, fingerprint, packed(op), packed(snapshot)))
            return self.public(op)

    def active_operations(self):
        with self.store.transaction() as db:
            return [json.loads(row[0]) for table in _TABLES.values()
                for row in db.execute('SELECT document FROM ' + table)
                if json.loads(row[0])['status'] in ACTIVE]

    def update_operation(self, kind, operation_id, **fields):
        allowed = {'status', 'error', 'job_id', 'input_ref', 'stop_proven', 'finished_at',
            'cancel_attempts', 'next_cancel_at'}
        require(set(fields) <= allowed)
        if 'status' in fields:
            require(type(fields['status']) is str and fields['status'] in ACTIVE | TERMINAL)
        if 'stop_proven' in fields:
            require(type(fields['stop_proven']) is bool)
        with self.store.transaction() as db:
            op = self._read(db, kind, operation_id)
            if op['status'] in TERMINAL:
                return op
            require(not op['stop_proven'] or fields.get('stop_proven', True), 'film_creation_stop_evidence_changed', 409)
            if fields.get('status') in TERMINAL:
                require(fields.get('stop_proven', op['stop_proven']) is True or not op['submit_attempted'],
                    'film_creation_stop_not_proven', 409)
                if not op['submit_attempted']:
                    fields['stop_proven'] = True
                fields['finished_at'] = time.time()
            op.update(fields)
            self._write(db, op)
            return op

    def claim_submission(self, kind, operation_id):
        with self.store.transaction() as db:
            op = self._read(db, kind, operation_id)
            if op['status'] not in ACTIVE or op['submit_attempted']:
                return None
            if op['cancel_requested'] or time.time() >= op['deadline_at']:
                op.update(status='cancelled' if op['cancel_requested'] else 'timed_out', stop_proven=True,
                    finished_at=time.time())
                self._write(db, op)
                return None
            op.update(submit_attempted=True, status='submitting', error=None)
            self._write(db, op)
            return op

    def _cancel(self, owner, operation_id, kind):
        with self.store.transaction() as db:
            op = self._read(db, kind, operation_id, owner)
            self.store._project(db, owner, op['project_id'])
            if op['status'] in ACTIVE:
                op.update(cancel_requested=True, status='cancelling')
                self._write(db, op)
            return self.public(op)

    def cancel_module(self, owner, operation_id):
        return self._cancel(owner, operation_id, 'module')

    def cancel_render(self, owner, operation_id):
        return self._cancel(owner, operation_id, 'render')

    def complete_module(self, operation_id, delivered):
        op = self.operation('module', operation_id)
        package = collect_module_result(op['document'], **delivered)
        with self.store.transaction() as db:
            op = self._read(db, 'module', operation_id)
            if op['status'] in TERMINAL:
                return self.public(op)
            if op['cancel_requested'] or time.time() >= op['deadline_at']:
                op.update(status='completed' if op['cancel_requested'] else 'timed_out',
                    error='cancel_arrived_after_completion' if op['cancel_requested'] else 'film_module_after_deadline')
            else:
                version = dict(id=str(uuid4()), operation_id=op['id'], owner=op['owner'],
                    project_id=op['project_id'], scene_id=op['scene_id'], film_sha256=op['film_sha256'],
                    package_sha256=digest(package), created_at=time.time())
                db.execute('INSERT INTO film_module_versions VALUES(?,?,?,?,?,?,?)',
                    (version['id'], op['owner'], op['project_id'], op['scene_id'], op['id'], packed(version), packed(package)))
                op.update(status='completed', version_id=version['id'], error=None)
            op.update(stop_proven=True, finished_at=time.time())
            self._write(db, op)
            return self.public(op)

    def render_snapshot(self, operation_id):
        with self.store.transaction() as db:
            op = self._read(db, 'render', operation_id)
            row = db.execute('SELECT snapshot FROM film_render_operations WHERE id=?', (operation_id,)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            configuration = op['configuration']
            return verify_film_execution_snapshot(json.loads(row[0]), expected_snapshot_sha256=op['snapshot_sha256'],
                runtime_fingerprint=configuration['runtime_fingerprint'],
                trusted_host_sha256=configuration['trusted_host_sha256'], image=configuration['image'])

    def complete_render(self, operation_id, result, data):
        snapshot = self.render_snapshot(operation_id)
        checked = validate_film_render_result({key: value for key, value in result.items() if key != 'path'},
            snapshot, runtime_fingerprint=snapshot['runtime_fingerprint'])
        require(type(data) is bytes and len(data) == checked['bytes']
            and hashlib.sha256(data).hexdigest() == checked['sha256'], 'film_creation_video_changed', 409)
        with self.store.transaction() as db:
            op = self._read(db, 'render', operation_id)
            if op['status'] in TERMINAL:
                return self.public(op)
            if op['cancel_requested']:
                op.update(status='completed', error='cancel_arrived_after_completion')
            elif time.time() >= op['deadline_at']:
                op.update(status='timed_out', error='film_render_after_deadline')
            else:
                db.execute('UPDATE film_render_operations SET data=? WHERE id=?', (data, operation_id))
                op.update(status='completed', result=checked, error=None)
            op.update(stop_proven=True, finished_at=time.time())
            self._write(db, op)
            return self.public(op)

    def renders(self, owner, project_id):
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            return [self.public(op, current=self._current(op, project))
                for row in db.execute(
                'SELECT document FROM film_render_operations WHERE owner=? AND project_id=? ORDER BY rowid DESC',
                (owner, project_id)) if (op := json.loads(row[0]))]

    def video(self, owner, operation_id):
        with self.store.transaction() as db:
            op = self._read(db, 'render', operation_id, owner)
            self.store._project(db, owner, op['project_id'])
            row = db.execute('SELECT data FROM film_render_operations WHERE id=?', (operation_id,)).fetchone()
            require(op['status'] == 'completed' and op.get('result') is not None and row[0] is not None,
                'film_creation_video_not_ready', 409)
            raw = bytes(row[0])
            require(hashlib.sha256(raw).hexdigest() == op['result']['sha256'], 'film_creation_video_changed', 409)
            return raw
