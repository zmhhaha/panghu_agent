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
from .animation_skill_production import MAX_TASK_BYTES
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
            trusted_host_sha256=None, image=None, assets=None, enabled=False, resource_authoring=None, _mode='configured'):
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
        if resource_authoring is not None:
            from .manim_film_admission import validate_consumer_context
            self.configuration['resource_authoring'] = validate_consumer_context(resource_authoring,
                runtime=self.runtime, hosts=self.hosts, image=self.image)
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
                CREATE TABLE IF NOT EXISTS film_module_resources (
                    operation_id TEXT PRIMARY KEY,owner TEXT NOT NULL,project_id TEXT NOT NULL,
                    document TEXT NOT NULL,envelope BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS film_prepared_resources (
                    resource_version_id TEXT PRIMARY KEY,owner TEXT NOT NULL,project_id TEXT NOT NULL,
                    document TEXT NOT NULL,envelope BLOB NOT NULL);
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
        if op.get('resource_authoring'):
            result['resource_authoring'] = dict(kind='manim', status=op.get('resource_status', 'queued'),
                stop_proven=op.get('resource_stop_proven', False),
                envelope_sha256=op.get('generated_resource', {}).get('envelope_sha256'))
        return result

    def register_prepared_resource(self, owner, project_id, output, envelope):
        """Register already verified passive assets; never invoke a producer."""
        from .film_authoring_resources import expected_scope
        from .manim_method_resources import method_resource
        require(type(output) is dict and output.get('schema') == 'video.resource-version.v1'
            and output.get('owner') == owner and output.get('project_id') == project_id
            and output.get('producer') == 'manim' and output.get('kind') == 'animation'
            and type(output.get('resource_version_id')) is str
            and re.fullmatch('[0-9a-f]{64}', output['resource_version_id'])
            and output.get('source_task_id') == output.get('content', {}).get('operation_id')
            and output.get('content_sha256') == digest(output.get('content')), 'film_prepared_resource_invalid', 409)
        content = output['content']
        context = self.configuration.get('resource_authoring', {})
        require(content['resource_image'] == context.get('resource_image'), 'film_resource_not_admitted', 503)
        descriptor, _ = method_resource(envelope, expected_sha256=envelope['sha256'],
            expected_scope=expected_scope(owner, content['operation_id']),
            image_digest=content['resource_image'].rsplit('@', 1)[1])
        require(descriptor == content['descriptor'] and envelope['request']['global_start_frame'] == 0
            and output['local_timing'].get('fps') == 24
            and output['local_timing'].get('frame_count') == descriptor['manifest']['frame_count'],
            'film_prepared_resource_changed', 409)
        record = dict(output=deepcopy(output), descriptor=descriptor, image=content['resource_image'])
        with self.store.transaction() as db:
            self.store._project(db, owner, project_id)
            existing = db.execute('SELECT owner,project_id,document,envelope FROM film_prepared_resources '
                'WHERE resource_version_id=?', (output['resource_version_id'],)).fetchone()
            if existing is not None:
                require(existing['owner'] == owner and existing['project_id'] == project_id
                    and json.loads(existing['document']) == record and json.loads(existing['envelope']) == envelope,
                    'film_prepared_resource_changed', 409)
            else:
                db.execute('INSERT INTO film_prepared_resources VALUES(?,?,?,?,?)',
                    (output['resource_version_id'], owner, project_id, packed(record), packed(envelope)))
        return output['resource_version_id']

    def _prepared_records(self, db, owner, project_id, references, frame_count=None):
        from .resource_placement import validate_manim_placement
        from .film_authoring_resources import expected_scope
        from .manim_method_resources import method_resource
        require(type(references) is list and len(references) <= 16, 'film_resource_references_invalid')
        result = []
        for reference in references:
            require(type(reference) is dict and set(reference) == {'resource_version_id', 'placement'},
                'film_resource_references_invalid')
            row = db.execute('SELECT owner,project_id,document,envelope FROM film_prepared_resources '
                'WHERE resource_version_id=?', (reference['resource_version_id'],)).fetchone()
            require(row is not None and row['owner'] == owner and row['project_id'] == project_id,
                'film_prepared_resource_not_found', 404)
            record, envelope = json.loads(row['document']), json.loads(row['envelope'])
            output = record['output']
            require(output['resource_version_id'] == reference['resource_version_id']
                and output['owner'] == owner and output['project_id'] == project_id
                and output['kind'] == 'animation' and output['producer'] == 'manim'
                and output['source_task_id'] == output['content']['operation_id']
                and output['content_sha256'] == digest(output['content']), 'film_prepared_resource_changed', 409)
            descriptor, assets = method_resource(envelope, expected_sha256=envelope['sha256'],
                expected_scope=expected_scope(owner, output['content']['operation_id']),
                image_digest=record['image'].rsplit('@', 1)[1])
            require(descriptor == record['descriptor'], 'film_prepared_resource_changed', 409)
            if frame_count is not None:
                validate_manim_placement(reference['placement'], descriptor, project_frame_count=frame_count)
            result.append(dict(record, envelope=envelope, reference=reference,
                assets={'assets/' + name: raw for name, raw in assets.items()}))
        return result

    def _resource_records(self, db, owner, operation_ids, seen=None):
        """Resolve only this module's authenticated predecessor/base lineage."""
        seen = set() if seen is None else seen
        records = []
        for operation_id in operation_ids:
            if operation_id in seen:
                continue
            seen.add(operation_id)
            op = self._read(db, 'module', operation_id, owner)
            records.extend(self._resource_records(db, owner, op.get('resource_dependencies', []), seen))
            prepared = self._prepared_records(db, owner, op['project_id'], op.get('resource_references', []),
                round(op['request']['film']['total_seconds'] * 24))
            seen_resources = {record['descriptor']['sha256'] for record in records}
            records.extend(record for record in prepared if record['descriptor']['sha256'] not in seen_resources)
            row = db.execute('SELECT owner,project_id,document,envelope FROM film_module_resources WHERE operation_id=?',
                (operation_id,)).fetchone()
            if row is None:
                require('generated_resource' not in op, 'film_resource_record_missing', 409)
                continue
            record, envelope = json.loads(row['document']), json.loads(row['envelope'])
            require(row['owner'] == owner and row['project_id'] == op['project_id']
                and record['descriptor'] == op.get('generated_resource'), 'film_resource_record_changed', 409)
            from .film_authoring_resources import verify_film_resource
            verify_film_resource(op, record['descriptor'], envelope, image=record['image'])
            records.append(dict(record, envelope=envelope))
        return records

    def _assets_for_operations(self, db, owner, operation_ids):
        from .film_authoring_resources import merge_film_resources
        return merge_film_resources(self.assets, self._resource_records(db, owner, operation_ids))

    def claim_resource(self, operation_id):
        from .film_authoring_resources import resource_request
        from .manim_film_resource_service import validate_request as validate_resource_request
        with self.store.transaction() as db:
            op = self._read(db, 'module', operation_id)
            if op['status'] not in ACTIVE or op.get('resource_submit_attempted'):
                return None
            require(op.get('resource_authoring') and not op['submit_attempted'], 'film_resource_state_invalid', 409)
            if op['cancel_requested'] or time.time() >= op['deadline_at']:
                return None
            frozen = validate_resource_request(resource_request(op))
            op.update(resource_submit_attempted=True, resource_status='submitting', resource_stop_proven=False,
                resource_request=frozen, resource_reservation_confirmed=False)
            self._write(db, op)
            return op

    def confirm_resource_reservation(self, operation_id):
        with self.store.transaction() as db:
            op = self._read(db, 'module', operation_id)
            require(op.get('resource_submit_attempted') is True, 'film_resource_state_invalid', 409)
            op['resource_reservation_confirmed'] = True
            self._write(db, op)
            return op

    def record_resource(self, operation_id, *, status, stop_proven, descriptor=None, envelope=None, image=None):
        require(type(status) is str and type(stop_proven) is bool, 'film_resource_state_invalid')
        initial = self.operation('module', operation_id)
        from .film_authoring_resources import attach_film_resource, verify_film_resource
        new_assets = None
        if status == 'completed':
            require(stop_proven and type(descriptor) is dict and type(envelope) is dict,
                'film_resource_stop_unproven', 409)
            require(image == initial['configuration']['resource_authoring']['resource_image'],
                'film_resource_image_changed', 409)
            new_assets = verify_film_resource(initial, descriptor, envelope, image=image)
        with self.store.transaction() as db:
            op = self._read(db, 'module', operation_id)
            if op['status'] not in ACTIVE:
                return op
            require(op.get('resource_submit_attempted') is True
                and not op['submit_attempted'], 'film_resource_state_invalid', 409)
            require(not op.get('resource_stop_proven') or stop_proven, 'film_resource_stop_evidence_changed', 409)
            op.update(resource_status=status, resource_stop_proven=stop_proven)
            if status == 'completed' and not op['cancel_requested'] and time.time() < op['deadline_at']:
                if op.get('generated_resource'):
                    require(op['generated_resource'] == descriptor, 'film_resource_record_changed', 409)
                    return op
                require(op['document']['schema'] == PENDING_SCHEMA, 'film_resource_state_invalid', 409)
                existing = self._assets_for_operations(db, op['owner'], op.get('resource_dependencies', []))
                # Check aggregate decoded bytes/file slots before accepting the
                # resource or submitting the module author.
                from .film_authoring_resources import merge_film_resources
                prior = self._resource_records(db, op['owner'], op.get('resource_dependencies', []))
                merge_film_resources(self.assets, [*prior, dict(descriptor=descriptor, envelope=envelope, image=image)])
                require(not (set(existing) & set(new_assets) - {'assets/manim_frame_layer.mjs', 'assets/manim_method_frames.mjs'}),
                    'film_resource_asset_collision', 409)
                core = attach_film_resource(op['document']['core_task'], descriptor, new_assets)
                op['document'] = dict(schema=PENDING_SCHEMA, core_task=core)
                op['generated_resource'] = deepcopy(descriptor)
                db.execute('INSERT INTO film_module_resources VALUES(?,?,?,?,?)',
                    (op['id'], op['owner'], op['project_id'], packed(dict(descriptor=descriptor, image=image)), packed(envelope)))
            self._write(db, op)
            return op

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
    def _validate_request(request, fields, optional=()):
        require(type(request) is dict and set(fields) <= set(request) <= set(fields) | set(optional)
            and type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        require(len(canonical_bytes(request)) <= 4 * 1024 * 1024, 'film_creation_request_budget', 413)

    def _base_module(self, db, owner, project_id, scene_id, request):
        """Resolve an exact compatible scene, without rewriting its source."""
        version, package = self._version(db, owner, request['base_module_version_id'])
        original, caps = package['input_document'], self.runtime_capabilities
        module = package['result']['module']
        index = request['current_scene_index']
        row = request['film']['scenes'][index]
        require(version['id'] == request['base_module_version_id'] and version['owner'] == owner
            and version['project_id'] == project_id and version['scene_id'] == scene_id
            and version['film_sha256'] == request['film_sha256']
            and original['scene']['scene_id'] == scene_id
            and original['film_context']['film_sha256'] == request['film_sha256']
            and original['film_context']['film'] == request['film']
            and original['film_context']['current_scene_index'] == index
            and original['target'] == request['target']
            and original['scene']['audio_sha256'] == row['audio_sha256']
            and original['constraints']['duration_seconds'] == row['audio_seconds'],
            'film_creation_base_module_mismatch', 409)
        require(original['constraints']['runtime_identity'] == caps['runtime_identity']
            and module['engine'] in caps['allowed_engines'],
            'film_creation_base_module_runtime_mismatch', 409)
        available = self._assets_for_operations(db, owner, [version['operation_id']])
        require(all(original['assets'].get(name) == hashlib.sha256(available[name]).hexdigest() if name in available else False
            for name in module['assets']), 'film_creation_base_module_assets_changed', 409)
        reference = dict(version_id=version['id'], package_sha256=version['package_sha256'],
            input_sha256=package['input_sha256'], result_sha256=package['result_sha256'])
        return module, reference

    def start_module(self, owner, project_id, scene_id, request, idem):
        self._validate_request(request, {'expected_revision', 'film', 'film_sha256', 'current_scene_index',
            'predecessor_version_id', 'target', 'instruction', 'configuration_sha256'},
            {'base_module_version_id', 'resource_authoring', 'resource_references'})
        if 'base_module_version_id' in request:
            require(type(request['base_module_version_id']) is str and 1 <= len(request['base_module_version_id']) <= 100)
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
            resource_dependencies = []
            if index:
                previous, package = self._version(db, owner, request['predecessor_version_id'])
                require(previous['project_id'] == project_id and previous['scene_id'] == film['scenes'][index - 1]['scene_id']
                    and previous['film_sha256'] == request['film_sha256'], 'film_creation_predecessor_mismatch', 409)
                predecessor = predecessor_from_package(package)
                predecessor_package = package
                resource_dependencies.append(previous['operation_id'])
            else:
                require(request['predecessor_version_id'] is None, 'film_creation_predecessor_mismatch', 409)
            row, recording = film['scenes'][index], recordings[index]
            target = request['target']
            require(type(target) is dict and set(target) == {'module_id', 'namespace', 'transition_target'})
            base_module = base_reference = None
            if 'base_module_version_id' in request:
                base_module, base_reference = self._base_module(db, owner, project_id, scene_id, request)
                resource_dependencies.append(self._version(db, owner, request['base_module_version_id'])[0]['operation_id'])
            caps = self.runtime_capabilities
            references = request.get('resource_references', [])
            prepared = self._prepared_records(db, owner, project_id, references, round(film['total_seconds'] * 24))
            for resource in prepared:
                placement = resource['reference']['placement']
                require(round(row['start_seconds'] * 24) <= placement['global_start_frame']
                    and placement['global_start_frame'] + placement['frame_count'] <= round(row['end_seconds'] * 24),
                    'film_resource_clock_outside_scene', 409)
            beats = make_beats(row['narration'], recording['spoken_text'])
            core = prepare_core_task(scene_id=scene_id, title=row['title'], visual_description=row['visual_intent'],
                narration=row['narration'], beats=beats,
                alignment=estimated_alignment(beats, row['audio_sha256'], row['audio_seconds']),
                audio_sha256=row['audio_sha256'], duration_seconds=row['audio_seconds'],
                film_context=dict(schema=CONTEXT_SCHEMA, film=film, film_sha256=request['film_sha256'],
                    current_scene_index=index, predecessor=predecessor), instruction=request['instruction'],
                module_id=target['module_id'], namespace=target['namespace'], transition_target=target['transition_target'],
                runtime_identity=caps['runtime_identity'], allowed_engines=caps['allowed_engines'],
                assets={name: hashlib.sha256(raw).hexdigest() for name, raw in
                    self._assets_for_operations(db, owner, resource_dependencies).items()}, style=film['style'],
                predecessor_package=predecessor_package, base_module=base_module)
            from .film_authoring_resources import attach_authoring_libraries
            core = attach_authoring_libraries(core, caps)
            from .film_authoring_resources import attach_film_resource, merge_film_resources
            merge_film_resources(self.assets, [*self._resource_records(db, owner, resource_dependencies), *prepared])
            for resource in prepared:
                core = attach_film_resource(core, resource['descriptor'], resource['assets'],
                    placement=resource['reference']['placement'],
                    resource_version_id=resource['reference']['resource_version_id'])
            if base_reference is not None:
                require(len(canonical_bytes(core)) <= MAX_TASK_BYTES,
                    'film_creation_base_module_input_too_large', 413)
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
            op['resource_dependencies'] = resource_dependencies
            op['resource_references'] = deepcopy(references)
            if 'resource_authoring' in request:
                require(self.configuration.get('resource_authoring'), 'film_resource_not_admitted', 503)
                from .film_authoring_resources import freeze_resource_options
                op.update(resource_authoring=freeze_resource_options(request['resource_authoring'], row, film['total_seconds']),
                    resource_submit_attempted=False, resource_status='queued', resource_stop_proven=False)
            if base_reference is not None:
                op['base_module_reference'] = base_reference
            db.execute('INSERT INTO film_module_operations VALUES(?,?,?,?,?,?,?)',
                (op['id'], owner, project_id, scene_id, idem, fingerprint, packed(op)))
            return self.public(op)

    def prepare_module(self, operation_id, adapter):
        op = self.operation('module', operation_id)
        if op['document'].get('schema') != PENDING_SCHEMA or op['cancel_requested']:
            return op
        core = op['document']['core_task']
        require(not op.get('resource_authoring') or op.get('generated_resource'), 'film_resource_not_ready', 409)
        producer = op['configuration'].get('resource_authoring', {}).get('producer') \
            if op.get('generated_resource') or op.get('resource_references') else None
        delivery = adapter.freeze(op['owner'], 'video-film-module:' + op['id'], core, core['constraints']['allowed_engines'],
            **({'resource_authoring': producer} if producer else {}))
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
            packages, source_operations = [], []
            for index, version_id in enumerate(ids):
                version, package = self._version(db, owner, version_id)
                require(version['project_id'] == project_id and version['scene_id'] == film['scenes'][index]['scene_id']
                    and version['film_sha256'] == request['film_sha256'], 'film_creation_render_module_mismatch', 409)
                packages.append(package)
                source_operations.append(version['operation_id'])
            assembled = assemble_film_production(packages=packages, film=film, film_sha256=request['film_sha256'],
                recordings=self._recordings(db, project, film), assets=self._assets_for_operations(db, owner, source_operations), runtime_fingerprint=self.runtime,
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
        if op.get('resource_references'):
            with self.store.transaction() as db:
                resources = self._prepared_records(db, op['owner'], op['project_id'], op['resource_references'])
            for resource in resources:
                required = {resource['descriptor']['manifest_path'], 'assets/manim_frame_layer.mjs',
                    *(row['path'] for row in resource['descriptor']['manifest']['frames'])}
                require(required <= set(package['result']['module']['assets']),
                    'film_resource_not_declared_by_module', 409)
        if op.get('generated_resource'):
            descriptor = op['generated_resource']
            required = {descriptor['manifest_path'], 'assets/manim_frame_layer.mjs',
                *(row['path'] for row in descriptor['manifest']['frames'])}
            require(required <= set(package['result']['module']['assets']),
                'film_resource_not_declared_by_module', 409)
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
