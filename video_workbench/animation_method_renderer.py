"""One-attempt isolated method rendering with deterministic offline sound.

The existing Docker ownership, bounded CLI, and exact cleanup primitives are
shared with the old renderer. Its contracts, limits and host stay unchanged.
New native engines bind their trusted runtime and measured seek evidence.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
from uuid import uuid4
import wave

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_method as contract
from agent_service.profiles.video_animation_create import source_sha256
from agent_service.profiles.video_animation_experiment import _validate_source_output
from . import animation_lab_capture as docker
from .animation_creation_renderer import _plain, _read, _write
from .animation_experiment_render import _IMAGE


HOST = Path(__file__).with_name('animation_method_host.mjs')
ENGINE_RUNTIME = Path(__file__).with_name('motion_renderer') / 'engine_runtime.mjs'
MAX_FRAMES = 1440
MAX_MEDIA_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_BYTES = 192 * 1024 * 1024
MAX_AUDIO_BYTES = 6 * 1024 * 1024
MAX_TIMEOUT = 600
_OP = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_TABLE = 'animation_method_render_operations'
ASSET_SHA256 = {
    'gsap.min.js': 'c174bfce53a729418d57a8ad8625e7247c793a22fef8e2851e3cfa3de9cd8280',
    'NotoSansCJK-Regular.ttc': 'b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a',
    'three.module.min.js': '08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7',
    'three.LICENSE.txt': '4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454',
}
_ASSETS = frozenset(ASSET_SHA256)


def _require(condition, code='animation_method_render_invalid', status=409):
    if not condition:
        raise ServiceError(code, status)


def _json(path, maximum=4 * 1024 * 1024):
    raw = _read(path, maximum)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError('animation_method_render_evidence_invalid', 409) from None
    _require(type(result) is dict)
    return result, raw


def validate_sound_events(events, duration):
    _require(type(events) is list and len(events) <= 128,
             'animation_method_sound_budget_invalid', 422)
    for event in events:
        _require(type(event) is dict and set(event) == {
            'time', 'duration', 'frequency', 'gain', 'waveform'},
            'animation_method_sound_event_invalid', 422)
        for key, low, high in (('time', 0, duration), ('duration', .01, 5),
                               ('frequency', 40, 16000), ('gain', 0, .8)):
            number = event[key]
            _require(type(number) in (int, float) and math.isfinite(number)
                     and low <= number <= high,
                     'animation_method_sound_event_invalid', 422)
        _require(type(event['waveform']) is str and event['waveform'] in {'sine', 'triangle', 'noise'}
                 and event['time'] + event['duration'] <= duration,
                 'animation_method_sound_event_invalid', 422)
    return json.loads(canonical_bytes(events))


def execute_method_render(directory):
    """Execute one frozen plan; use shared owner-verified Docker primitives."""
    _require(sys.platform == 'linux', 'animation_method_render_linux_required', 422)
    root = _plain(directory)
    plan, _ = _json(root / 'render-plan.json', 65536)
    request, request_raw = _json(root / 'input/request.json', 256 * 1024)
    _require(plan['directory'] == str(root) and digest(request) == plan['request_sha256']
             and not (root / 'render-start.json').exists())
    token = uuid4().hex
    name = 'animation-method-' + request['identity'][:12] + '-' + token[:12]
    now = lambda: datetime.now(timezone.utc).isoformat()
    start = dict(schema='video.animation-method-render-start.v1',
                 owner_token=token, container_name=name, started_at=now(), plan=plan)
    _write(root / 'render-start.json', canonical_bytes(start))
    began = time.monotonic()
    execution_seconds = plan['timeout_seconds']
    if plan.get('deadline_at') is not None:
        execution_seconds = min(execution_seconds, plan['deadline_at'] - time.time())
    deadline = began + max(0, execution_seconds)
    run_deadline = deadline - 15
    image_id = container_id = None
    created, error = False, None
    cleanup = dict(status='not_created', container_id=None)
    try:
        _require(execution_seconds >= 30, 'animation_method_render_deadline_insufficient')
        # _write creates private host files. The isolated renderer runs as a
        # different UID and needs explicit read access to the frozen inputs;
        # bind mounts remain read-only inside the container.
        input_root = root / 'input'
        runtime_files = [root / 'engine_runtime.mjs'] if request.get('engine_runtime_sha256') else []
        for path in (input_root, *input_root.rglob('*'), root / 'host.mjs', *runtime_files):
            docker._ownership(_plain(path), directory=path.is_dir())
        inspected = json.loads(docker._checked(
            ['image', 'inspect', '--format', docker._IMAGE_FORMAT, plan['image']], run_deadline))
        _require(re.fullmatch(r'sha256:[0-9a-f]{64}', inspected.get('image_id', ''))
                 and plan['repository'] + '@' + plan['image_digest'] in inspected.get('repo_digests', []),
                 'animation_method_render_image_changed')
        image_id = inspected['image_id']
        work = root / 'work'
        work.mkdir()
        docker._ownership(work, directory=True)
        runtime_mount = (['--mount', 'type=bind,source=' + str(root / 'engine_runtime.mjs')
                         + ',target=/opt/renderer/engine_runtime.mjs,readonly'] if runtime_files else [])
        arguments = ['container', 'create', '--name', name,
            '--label', docker._OWNER + '=' + token, '--label', docker._CAPTURE + '=' + request['identity'],
            '--pull=never', '--network=none', '--read-only', '--user=10001:10001',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=1g',
            '--pids-limit=256', '--shm-size=128m', '--tmpfs=/tmp:rw,nosuid,nodev,size=256m',
            '--restart=no', '--log-driver=none', '--stop-timeout=3', '--init', '--workdir=/work',
            '--env', 'ANIMATION_METHOD_IMAGE_DIGEST=' + plan['image_digest'],
            '--env', 'ANIMATION_METHOD_REQUEST_SHA256=' + hashlib.sha256(request_raw).hexdigest(),
            '--mount', 'type=bind,source=' + str(root / 'input') + ',target=/input,readonly',
            '--mount', 'type=bind,source=' + str(root / 'host.mjs')
                + ',target=/opt/renderer/animation_method_host.mjs,readonly',
            '--mount', 'type=bind,source=' + str(work) + ',target=/work',
            *runtime_mount, '--entrypoint=node', image_id, '/opt/renderer/animation_method_host.mjs']
        created = True
        container_id = docker._checked(arguments, run_deadline)
        _require(_SHA.fullmatch(container_id), 'animation_method_render_container_id_invalid')
        _require(docker._owned(docker._container(container_id, run_deadline), name=name,
            token=token, capture=request['identity'], image_id=image_id, container_id=container_id),
            'animation_method_render_container_identity_invalid')
        completed = docker._command(['container', 'start', '--attach', container_id],
            docker._remaining(run_deadline, plan['timeout_seconds']), log_path=root / 'render.log')
        _require(completed['code'] == 0, 'animation_method_render_container_failed')
        final = docker._container(container_id, run_deadline)
        _require(docker._owned(final, name=name, token=token, capture=request['identity'],
            image_id=image_id, container_id=container_id)
            and final['running'] is False and final['exit_code'] == 0
            and final['oom_killed'] is False, 'animation_method_render_exit_invalid')
    except BaseException as exc:
        error = str(exc) if isinstance(exc, (ServiceError, ValueError, TimeoutError)) else type(exc).__name__
    finally:
        if created:
            try:
                cleanup = docker._cleanup(name=name, token=token, capture=request['identity'],
                    image_id=image_id, container_id=container_id, deadline=deadline)
            except BaseException as exc:
                cleanup = dict(status='unconfirmed', container_id=container_id,
                               error=type(exc).__name__)
                error = error or 'animation_method_render_cleanup_unconfirmed'
    outcome = dict(schema='video.animation-method-render-outcome.v1',
        request_identity=request['identity'], request_sha256=plan['request_sha256'],
        image=plan['image'], actual_image_id=image_id, container_id=container_id,
        container_name=name, owner_token=token, cleanup=cleanup,
        status='failed' if error else 'rendered', error=error,
        execution_timeout_seconds=max(0, execution_seconds),
        started_at=start['started_at'], ended_at=now(), automatic_retry=False)
    _write(root / 'render-outcome.json', canonical_bytes(outcome))
    return outcome


class MethodAnimationRenderer:
    def __init__(self, root, image, assets_dir=None, enabled=False, *, timeout=360,
                 engine_admission_file=None, candidate_engines=()):
        match = _IMAGE.fullmatch(image) if type(image) is str else None
        _require(match is not None, 'animation_method_render_image_pin_required', 422)
        _require(type(enabled) is bool and type(timeout) in (int, float)
                 and math.isfinite(timeout) and 30 <= timeout <= MAX_TIMEOUT,
                 'animation_method_render_config_invalid', 422)
        self.root = _plain(root)
        self.image, self.image_digest, self.repository = image, match[2], match[1]
        self.assets_dir = _plain(assets_dir) if assets_dir is not None else None
        self.enabled, self.timeout = enabled, timeout
        self.ledger = self.root / 'method-render-ledger.sqlite3'
        self.engine_admission_file = (_plain(engine_admission_file)
                                      if engine_admission_file is not None else None)
        # Only offline acceptance code can opt into candidates. The HTTP server
        # never exposes this switch or advertises these unproven engines.
        _require(type(candidate_engines) in (tuple, list)
                 and set(candidate_engines) <= {'canvas', 'three'},
                 'animation_method_render_candidate_invalid', 422)
        self.candidate_engines = tuple(candidate_engines)

    def capabilities(self):
        assets = {}
        if self.assets_dir is not None:
            for name, expected in ASSET_SHA256.items():
                path = self.assets_dir / name
                if path.exists():
                    actual = hashlib.sha256(_read(path, 32 * 1024 * 1024)).hexdigest()
                    _require(actual == expected, 'animation_method_render_assets_changed')
                    assets[name] = actual
        identity = dict(image_digest=self.image_digest,
            host_sha256=hashlib.sha256(_read(HOST, 1024 * 1024)).hexdigest(),
            engine_runtime_sha256=hashlib.sha256(_read(ENGINE_RUNTIME, 1024 * 1024)).hexdigest(),
            admission_id='method-html-svg-v1@' + self.image_digest)
        engines = ['html-svg']
        if self.engine_admission_file is not None:
            admission, _ = _json(self.engine_admission_file, 65536)
            _require(admission.get('schema') == 'video.animation-engine-admission.v1'
                     and admission.get('image_digest') == identity['image_digest']
                     and admission.get('host_sha256') == identity['host_sha256']
                     and admission.get('engine_runtime_sha256') == identity['engine_runtime_sha256']
                     and admission.get('assets') == assets,
                     'animation_method_engine_admission_changed')
            evidence = admission.get('evidence')
            _require(type(evidence) is dict and bool(evidence)
                     and set(evidence) <= {'canvas', 'three'},
                     'animation_method_engine_admission_invalid')
            for engine, record in evidence.items():
                _require(type(record) is dict and type(record.get('operation_id')) is str,
                         'animation_method_engine_admission_invalid')
                actual = self.lookup(record['operation_id'])
                _require(actual is not None and actual['technical_passed'] is True
                         and actual['runtime']['engine'] == engine
                         and actual['runtime']['image'] == self.image
                         and actual['technical']['evidence_sha256'] == record.get('evidence_sha256'),
                         'animation_method_engine_admission_unproven')
            if 'three' in evidence:
                _require({'three.module.min.js', 'three.LICENSE.txt'} <= assets.keys(),
                         'animation_method_render_assets_missing')
            engines += [engine for engine in ('canvas', 'three') if engine in evidence]
            identity['admission_id'] = 'method-engines-v2@' + digest(admission)
        return dict(schema='video.animation-runtime-capabilities.v1',
                    allowed_engines=engines, assets=assets, runtime_identity=identity)

    def _directory(self, operation_id):
        _require(type(operation_id) is str and _OP.fullmatch(operation_id),
                 'animation_method_render_operation_invalid', 422)
        return _plain(self.root / 'operations' / hashlib.sha256(operation_id.encode()).hexdigest())

    def _connect(self, create=False, *, write=False):
        _plain(self.ledger)
        if not self.ledger.exists():
            _require(create, 'animation_method_render_not_found', 404)
            _require(not (self.root / 'operations').exists()
                     or not any((self.root / 'operations').iterdir()),
                     'animation_method_render_ledger_missing')
            self.root.mkdir(parents=True, exist_ok=True)
        db = (sqlite3.connect(self.ledger, timeout=15, isolation_level=None) if create or write else
              sqlite3.connect(self.ledger.as_uri() + '?mode=ro', uri=True, timeout=15, isolation_level=None))
        db.row_factory = sqlite3.Row
        try:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= {_TABLE}, 'animation_method_render_database_not_isolated')
            if create:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('CREATE TABLE IF NOT EXISTS ' + _TABLE + ' ('
                    'operation_id TEXT PRIMARY KEY,binding_sha256 TEXT NOT NULL,'
                    'binding BLOB NOT NULL,proof_sha256 TEXT,result BLOB)')
            else:
                _require(_TABLE in tables, 'animation_method_render_ledger_invalid')
        except BaseException:
            db.close()
            raise
        return db

    def _snapshot(self, operation_id, source, document, sound_events, deadline_at=None):
        self._directory(operation_id)
        _require(deadline_at is None or type(deadline_at) in (int, float)
                 and math.isfinite(deadline_at), 'animation_method_render_deadline_invalid', 422)
        context = contract.validate_input(document)
        bounds = context['constraints']
        _require(bounds['fps'] == 24 and 320 <= bounds['width'] <= 1280
                 and 180 <= bounds['height'] <= 720 and 1 <= bounds['duration_seconds'] <= 60
                 and bounds['duration_seconds'] * 24 == int(bounds['duration_seconds'] * 24),
                 'animation_method_render_budget_not_admitted', 422)
        capabilities = self.capabilities()
        _require(type(source) is dict and source.get('engine') in
                 set(capabilities['allowed_engines']) | set(self.candidate_engines),
                 'animation_method_render_engine_not_admitted', 422)
        payload = _validate_source_output(source, context)
        _require(set(context['assets']) <= _ASSETS,
                 'animation_method_render_asset_not_admitted', 422)
        engine = payload['engine']
        engine_aware = (engine != 'html-svg' or bounds.get('runtime_identity') is not None
                        or 'three.module.min.js' in context['assets'])
        if engine_aware:
            _require(all(ASSET_SHA256[name] == checksum
                         for name, checksum in context['assets'].items()),
                     'animation_method_render_assets_changed')
            _require(engine != 'three' or context['assets'].get('three.module.min.js') ==
                     ASSET_SHA256['three.module.min.js'],
                     'animation_method_render_assets_missing')
        frozen_runtime = bounds.get('runtime_identity')
        if frozen_runtime is not None:
            _require(type(frozen_runtime) is dict and all(
                frozen_runtime.get(key) == capabilities['runtime_identity'][key]
                for key in ('image_digest', 'host_sha256', 'engine_runtime_sha256')),
                'animation_method_render_runtime_changed')
        events = validate_sound_events(sound_events, bounds['duration_seconds'])
        files = {name: hashlib.sha256(text.encode()).hexdigest()
                 for name, text in payload['files'].items()}
        request = dict(schema='video.animation-method-render-request.v1',
            constraints={key: bounds[key] for key in ('width', 'height', 'fps', 'duration_seconds')},
            files_sha256=files, assets_sha256=context['assets'], sound_events=events,
            sound_events_sha256=digest(events),
            host_sha256=hashlib.sha256(_read(HOST, 1024 * 1024)).hexdigest(),
            image_digest=self.image_digest, max_frames=MAX_FRAMES,
            max_media_bytes=MAX_MEDIA_BYTES, max_evidence_bytes=MAX_EVIDENCE_BYTES)
        if engine_aware:
            request.update(schema='video.animation-method-render-request.v2', engine=engine,
                           engine_runtime_sha256=capabilities['runtime_identity']['engine_runtime_sha256'])
        request['identity'] = digest(request)
        return dict(schema='video.animation-method-render-binding.v1',
            operation_id=operation_id, source=payload, source_sha256=source_sha256(payload),
            document=context, input_sha256=digest(context), request=request,
            request_sha256=digest(request), image=self.image,
            image_digest=self.image_digest, timeout_seconds=self.timeout,
            configured_timeout_seconds=self.timeout, deadline_at=deadline_at)

    def _row(self, operation_id, *, historical=False):
        self._directory(operation_id)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE operation_id=?',
                             (operation_id,)).fetchone()
        _require(row is not None, 'animation_method_render_not_found', 404)
        binding = json.loads(row['binding'])
        _require(bytes(row['binding']) == canonical_bytes(binding)
                 and digest(binding) == row['binding_sha256']
                 and binding['operation_id'] == operation_id,
                 'animation_method_render_binding_changed')
        image = _IMAGE.fullmatch(binding.get('image', '')) if type(binding.get('image')) is str else None
        request = binding['request']
        _require(image is not None and image[1] == self.repository
                 and image[2] == binding['image_digest'] == request.get('image_digest')
                 and binding['request_sha256'] == digest(request)
                 and request.get('identity') == digest({k: v for k, v in request.items() if k != 'identity'})
                 and (historical or binding['image'] == self.image),
                 'animation_method_render_binding_changed')
        return dict(row), binding

    def render(self, operation_id, source, document, sound_events=None, *, deadline_at=None):
        _require(self.enabled, 'animation_method_renderer_disabled', 503)
        binding = self._snapshot(operation_id, source, document, sound_events or [], deadline_at)
        # Existing claims are always reconciled even after their deadline. The
        # effective first-attempt timeout is frozen and never recomputed into
        # an idempotency conflict or an authorization for another container.
        existing = None
        if self.ledger.exists():
            try:
                existing, _ = self._row(operation_id)
            except ServiceError as error:
                if error.status != 404:
                    raise
        if existing is None and deadline_at is not None:
            _require(deadline_at - time.time() >= 30,
                     'animation_method_render_deadline_insufficient')
        with closing(self._connect(create=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT binding_sha256,binding FROM ' + _TABLE + ' WHERE operation_id=?',
                             (operation_id,)).fetchone()
            if row:
                stored = json.loads(row['binding'])
                binding['timeout_seconds'] = stored['timeout_seconds']
                _require(row['binding_sha256'] == digest(binding),
                         'animation_method_render_operation_conflict')
                db.commit()
                return self.lookup(operation_id)
            if deadline_at is not None:
                remaining = deadline_at - time.time()
                _require(remaining >= 30, 'animation_method_render_deadline_insufficient')
                binding['timeout_seconds'] = min(self.timeout, remaining)
            db.execute('INSERT INTO ' + _TABLE + '(operation_id,binding_sha256,binding) VALUES (?,?,?)',
                       (operation_id, digest(binding), canonical_bytes(binding)))
            db.commit()
        root = self._directory(operation_id)
        phase = 'preparing'
        try:
            root.mkdir(parents=True, exist_ok=False)
            (root / 'input/scene/assets').mkdir(parents=True)
            for name, text in binding['source']['files'].items():
                _write(root / 'input/scene' / name, text.encode())
            for name, expected in binding['request']['assets_sha256'].items():
                _require(self.assets_dir is not None, 'animation_method_render_assets_missing', 422)
                raw = _read(self.assets_dir / name, 32 * 1024 * 1024)
                _require(hashlib.sha256(raw).hexdigest() == expected,
                         'animation_method_render_assets_changed')
                _write(root / 'input/scene/assets' / name, raw)
            _write(root / 'host.mjs', _read(HOST, 1024 * 1024))
            if binding['request'].get('engine_runtime_sha256'):
                _write(root / 'engine_runtime.mjs', _read(ENGINE_RUNTIME, 1024 * 1024))
            _write(root / 'input/request.json', canonical_bytes(binding['request']))
            _write(root / 'binding.json', canonical_bytes(binding))
            plan = dict(directory=str(root), image=self.image, repository=self.repository,
                        image_digest=self.image_digest, request_sha256=binding['request_sha256'],
                        timeout_seconds=binding['timeout_seconds'], deadline_at=deadline_at)
            _write(root / 'render-plan.json', canonical_bytes(plan))
            phase = 'executing'
            execute_method_render(root)
        except Exception as error:
            if phase == 'preparing' and root.is_dir() and not (root / 'render-outcome.json').exists():
                _write(root / 'render-outcome.json', canonical_bytes(dict(
                    schema='video.animation-method-render-outcome.v1',
                    request_identity=binding['request']['identity'],
                    request_sha256=binding['request_sha256'], image=self.image,
                    status='failed', error=getattr(error, 'code', type(error).__name__),
                    actual_image_id=None, container_id=None,
                    cleanup=dict(status='not_created', container_id=None), automatic_retry=False)))
            # Once execution may have begun, only original evidence can resolve
            # the claim. A lost response never authorizes another container.
        return self.lookup(operation_id)

    def _outputs(self, root, binding, outcome):
        request = binding['request']
        work = root / 'work'
        receipt, receipt_raw = _json(work / 'render-receipt.json')
        native = request.get('schema') == 'video.animation-method-render-request.v2'
        _require(receipt.get('schema') == 'video.animation-method-render-receipt.' + ('v2' if native else 'v1')
                 and receipt.get('status') == 'rendered' and receipt.get('decode_verified') is True
                 and receipt.get('reverse_seek_verified') is True
                 and receipt.get('request_identity') == request['identity']
                 and receipt.get('request_sha256') == binding['request_sha256']
                 and receipt.get('host_sha256') == request['host_sha256']
                 and receipt.get('image_digest') == binding['image_digest'],
                 'animation_method_render_receipt_invalid')
        count = int(request['constraints']['duration_seconds'] * 24)
        frames = receipt.get('frames')
        _require(type(frames) is list and len(frames) == count + 1
                 and receipt.get('frame_count') == count,
                 'animation_method_render_frames_invalid')
        total = 0
        for index, frame in enumerate(frames):
            name = f'frame-{index:06d}.jpg' if index < count else 'reverse-initial.jpg'
            _require(type(frame) is dict and frame.get('name') == name
                     and frame.get('timestamp_seconds') == (index / 24 if index < count else 0),
                     'animation_method_render_frames_invalid')
            raw = _read(work / 'frames' / name, 4 * 1024 * 1024)
            _require(raw[:2] == b'\xff\xd8' and raw[-2:] == b'\xff\xd9'
                     and len(raw) == frame.get('bytes')
                     and hashlib.sha256(raw).hexdigest() == frame.get('sha256'),
                     'animation_method_render_frame_changed')
            total += len(raw)
        names = {frame['name'] for frame in frames}
        if native:
            _require(receipt.get('engine') == request['engine']
                     and receipt.get('engine_runtime_sha256') == request['engine_runtime_sha256'],
                     'animation_method_render_engine_evidence_invalid')
            observation = receipt.get('engine_observation', {})
            contexts = observation.get('contexts')
            _require(observation.get('engine') == request['engine']
                     and observation.get('revision') == 'animation-engine-runtime-1'
                     and type(contexts) is list and len(contexts) <= 16,
                     'animation_method_render_engine_evidence_invalid')
            required_kind = '2d' if request['engine'] == 'canvas' else 'webgl2'
            _require((request['engine'] != 'html-svg' or not contexts)
                     and all(type(row) is dict and row.get('kind') in
                         ({'2d'} if request['engine'] == 'canvas' else {'2d', 'webgl2'}) for row in contexts)
                     and (request['engine'] == 'html-svg' or any(row.get('kind') == required_kind and row.get('connected') is True
                         and row.get('visible') is True and type(row.get('width')) is int
                         and type(row.get('height')) is int
                         and 0 < row['width'] <= 4096 and 0 < row['height'] <= 4096 for row in contexts))
                     and all(row.get('software') is True and
                             'swiftshader' in str(row.get('renderer', '')).lower()
                             and row.get('context_lost') is False and row.get('gl_error') == 0
                             for row in contexts if row.get('kind') == 'webgl2'),
                     'animation_method_render_engine_evidence_invalid')
            checks = receipt.get('seek_checks', {})
            probes = checks.get('probes')
            _require(checks.get('schema') == 'video.animation-engine-seek-checks.v1'
                     and all(checks.get(key) is True for key in ('absolute_seek',
                         'non_monotonic_seek_verified', 'repeated_seek_verified', 'idle_frame_verified',
                         'fresh_load_verified'))
                     and type(probes) is list and 4 <= len(probes) <= 8,
                     'animation_method_render_seek_evidence_invalid')
            for probe in probes:
                name = probe.get('name', '')
                ref = probe.get('reference_name', '')
                _require(re.fullmatch(r'[A-Za-z0-9_-]+\.jpg', name)
                         and name not in names and ref in {frame['name'] for frame in frames[:-1]},
                         'animation_method_render_seek_evidence_invalid')
                reference = next(frame for frame in frames if frame['name'] == ref)
                raw = _read(work / 'frames' / name, 4 * 1024 * 1024)
                _require(probe.get('timestamp_seconds') == reference['timestamp_seconds']
                         and probe.get('sha256') == reference['sha256'] == hashlib.sha256(raw).hexdigest()
                         and probe.get('bytes') == len(raw),
                         'animation_method_render_seek_evidence_invalid')
                names.add(name)
                total += len(raw)
        _require(frames[0]['sha256'] == frames[-1]['sha256']
                 and {path.name for path in (work / 'frames').iterdir()} == names,
                 'animation_method_render_reverse_or_directory_invalid')
        media = _read(work / 'output.mp4', MAX_MEDIA_BYTES)
        _require(media[4:8] == b'ftyp' and len(media) == receipt.get('output_bytes')
                 and hashlib.sha256(media).hexdigest() == receipt.get('output_sha256'),
                 'animation_method_render_media_changed')
        expected_audio = bool(request['sound_events'])
        audio_sha256 = None
        if expected_audio:
            audio = _read(work / 'sound-effects.wav', MAX_AUDIO_BYTES)
            audio_sha256 = hashlib.sha256(audio).hexdigest()
            _require(audio_sha256 == receipt.get('audio_sha256'),
                     'animation_method_render_audio_changed')
            with wave.open(io.BytesIO(audio), 'rb') as wav:
                _require(wav.getnchannels() == 1 and wav.getframerate() == 48000
                         and wav.getsampwidth() == 2 and wav.getnframes() ==
                         round(request['constraints']['duration_seconds'] * 48000),
                         'animation_method_render_audio_format_invalid')
            total += len(audio)
        else:
            _require(receipt.get('audio_sha256') is None,
                     'animation_method_render_unexpected_audio')
        _require(receipt.get('sound_events_sha256') == digest(request['sound_events']),
                 'animation_method_render_sound_events_changed')
        _require(total + len(media) <= MAX_EVIDENCE_BYTES,
                 'animation_method_render_evidence_budget_exceeded')
        allowed = {'frames', 'output.mp4', 'render-receipt.json'}
        if expected_audio:
            allowed.add('sound-effects.wav')
        _require({p.name for p in work.iterdir()} <= allowed,
                 'animation_method_render_extra_output')
        probe = receipt.get('probe', {})
        streams = probe.get('streams', [])
        videos = [row for row in streams if row.get('codec_type') == 'video']
        audios = [row for row in streams if row.get('codec_type') == 'audio']
        bounds = request['constraints']
        try:
            probe_ok = (len(videos) == 1 and len(audios) == int(expected_audio)
                and len(streams) == 1 + int(expected_audio)
                and videos[0]['codec_name'] == 'h264'
                and videos[0]['width'] == bounds['width'] and videos[0]['height'] == bounds['height']
                and videos[0]['avg_frame_rate'] == '24/1'
                and abs(float(probe['format']['duration']) - bounds['duration_seconds']) <= 1/24 + .001
                and (not expected_audio or audios[0]['codec_name'] == 'aac'))
        except (KeyError, ValueError, IndexError, TypeError):
            probe_ok = False
        _require(probe_ok, 'animation_method_render_probe_invalid')
        return receipt, receipt_raw, audio_sha256

    def lookup(self, operation_id):
        # Reading a stopped historical attempt does not authorize execution
        # with its old pin. render() keeps _row's strict current-image default.
        row, binding = self._row(operation_id, historical=True)
        root = self._directory(operation_id)
        try:
            outcome, outcome_raw = _json(root / 'render-outcome.json', 65536)
        except FileNotFoundError:
            return None
        _require(outcome.get('schema') == 'video.animation-method-render-outcome.v1'
                 and outcome.get('request_identity') == binding['request']['identity']
                 and outcome.get('request_sha256') == binding['request_sha256']
                 and outcome.get('image') == binding['image'] and outcome.get('automatic_retry') is False,
                 'animation_method_render_outcome_changed')
        cleanup = outcome.get('cleanup', {})
        if cleanup.get('status') not in {'not_created', 'confirmed_absent', 'removed_and_confirmed_absent'}:
            return None
        rendered = outcome.get('status') == 'rendered'
        _require(rendered or outcome.get('status') == 'failed')
        result = dict(schema='video.animation-method-render-verification.v1',
            operation_id=operation_id, source_sha256=binding['source_sha256'],
            input_sha256=binding['input_sha256'], technical_passed=False, media=None,
            error=outcome.get('error'), whole_video_watched=False, audio_observed=False)
        receipt = None
        if rendered:
            _require(outcome.get('error') is None
                     and type(outcome.get('container_id')) is str and _SHA.fullmatch(outcome['container_id'])
                     and cleanup.get('container_id') == outcome['container_id']
                     and type(outcome.get('actual_image_id')) is str
                     and re.fullmatch(r'sha256:[0-9a-f]{64}', outcome['actual_image_id']),
                     'animation_method_render_container_evidence_invalid')
            _require(_read(root / 'binding.json', 4 * 1024 * 1024) == canonical_bytes(binding),
                     'animation_method_render_binding_changed')
            _require(_read(root / 'input/request.json', 256 * 1024) == canonical_bytes(binding['request']),
                     'animation_method_render_request_changed')
            _require(hashlib.sha256(_read(root / 'host.mjs', 1024 * 1024)).hexdigest()
                     == binding['request']['host_sha256'], 'animation_method_render_host_changed')
            if binding['request'].get('engine_runtime_sha256'):
                _require(hashlib.sha256(_read(root / 'engine_runtime.mjs', 1024 * 1024)).hexdigest()
                         == binding['request']['engine_runtime_sha256'],
                         'animation_method_render_runtime_changed')
            for name, checksum in binding['request']['files_sha256'].items():
                _require(hashlib.sha256(_read(root / 'input/scene' / name, 512 * 1024)).hexdigest()
                         == checksum, 'animation_method_render_source_changed')
            for name, checksum in binding['request']['assets_sha256'].items():
                _require(hashlib.sha256(_read(root / 'input/scene/assets' / name, 32 * 1024 * 1024)).hexdigest()
                         == checksum, 'animation_method_render_assets_changed')
            receipt, _, audio_sha256 = self._outputs(root, binding, outcome)
            result.update(technical_passed=True, frame_count=receipt['frame_count'],
                media=dict(sha256=receipt['output_sha256'],
                    duration_seconds=binding['request']['constraints']['duration_seconds'],
                    audio_sha256=audio_sha256))
        proof = digest(dict(binding_sha256=digest(binding), outcome_sha256=hashlib.sha256(outcome_raw).hexdigest(),
                            receipt_sha256=digest(receipt) if receipt else None))
        result['technical'] = dict(status='passed' if rendered else 'failed',
            evidence_sha256=proof, scope='isolated_media_reverse_seek_and_sound_timing')
        engine = binding['request'].get('engine', 'html-svg')
        result['runtime'] = dict(engine=engine,
            admission=dict(status='admitted' if rendered else 'not_admitted',
                id=('method-' + engine + '-v2@' if binding['request']['schema'].endswith('.v2')
                    else 'method-html-svg-v1@')
                    + binding['image_digest'], evidence_sha256=proof),
            image=binding['image'], actual_image_id=outcome.get('actual_image_id'),
            observation='rendered_frames_not_agent_motion_or_audio_observation')
        if row['proof_sha256'] is not None:
            _require(row['proof_sha256'] == proof, 'animation_method_render_proof_changed')
            _require(row['result'] is not None and bytes(row['result']) == canonical_bytes(result),
                     'animation_method_render_result_changed')
            return result
        # A previously unrecorded completion may reconcile its original claim.
        # Already completed media/frame reads above never rewrite the ledger.
        with closing(self._connect(write=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            stored = db.execute('SELECT binding_sha256,proof_sha256,result FROM ' + _TABLE + ' WHERE operation_id=?',
                                (operation_id,)).fetchone()
            _require(stored is not None and stored['binding_sha256'] == digest(binding),
                     'animation_method_render_binding_changed')
            _require(stored['proof_sha256'] in (None, proof), 'animation_method_render_proof_changed')
            if stored['proof_sha256'] is None:
                db.execute('UPDATE ' + _TABLE + ' SET proof_sha256=?,result=? WHERE operation_id=?',
                           (proof, canonical_bytes(result), operation_id))
            else:
                _require(stored['result'] is not None and bytes(stored['result']) == canonical_bytes(result),
                         'animation_method_render_result_changed')
            db.commit()
        return result

    def media_path(self, operation_id):
        result = self.lookup(operation_id)
        _require(result is not None and result['media'] is not None,
                 'animation_method_render_media_unavailable', 404)
        return self._directory(operation_id) / 'work/output.mp4'

    def frames(self, operation_id, max_frames=12):
        _require(type(max_frames) is int and 1 <= max_frames <= 24,
                 'animation_method_render_frame_budget_invalid', 422)
        result = self.lookup(operation_id)
        _require(result is not None and result['media'] is not None,
                 'animation_method_render_media_unavailable', 404)
        root = self._directory(operation_id) / 'work'
        receipt, _ = _json(root / 'render-receipt.json')
        count = receipt['frame_count']
        indices = sorted({round(i * (count - 1) / max(1, max_frames - 1))
                          for i in range(min(max_frames, count))})
        return [dict(timestamp_seconds=index / 24, mime='image/jpeg',
                     raw=_read(root / 'frames' / receipt['frames'][index]['name'], 4 * 1024 * 1024))
                for index in indices]
