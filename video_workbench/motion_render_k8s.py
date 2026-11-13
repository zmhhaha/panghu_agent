"""Isolated scene Jobs with deterministic identities and durable intent records.

Only this independent execution service holds a namespace-scoped Kubernetes
token. No page, model output, remote receipt or caller supplies commands/paths.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import tarfile
import threading
import tempfile
import time
import zlib

import httpx

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .motion import COMPILER_TEMPLATES, compile_project
from .continuity import (ALL_CONTINUITY_COMPILER_TEMPLATES,
                         compile_continuity_project)
from .motion_review import verify_observations
from .motion_dispatch import PROCESS_SCHEMA, PRODUCTION_PROCESS_COMPILER, scene_compiler
from .process_review import verify_process_observations
from .motion_renderer.job import FILES, INIT, freeze_input
from .motion_render_adapter import UNAVAILABLE, safe_motion_context
from .creative_renderer import (COMPILER_TEMPLATES as CREATIVE_COMPILER_TEMPLATES,
    compile_creative_project)

CHUNK_BYTES = 512 * 1024
BUNDLE_BYTES = 16 * 1024 * 1024
ARCHIVE_BYTES = 6 * 1024 * 1024
EXTRACT_BYTES = 32 * 1024 * 1024
LABEL = 'video-workbench.panghuer.top/'
ANNOTATION = LABEL + 'binding'
CHUNK_INIT = INIT.replace("raw=Path('/bundle/input.tgz').read_bytes()", """count=int(os.environ['MOTION_BUNDLE_PARTS'])
if not 1<=count<=32: raise RuntimeError('bundle_parts')
parts=[Path('/bundle/part-%04d'%index).read_bytes() for index in range(count)]
if any(len(part)>524288 for part in parts): raise RuntimeError('bundle_part_size')
raw=b''.join(parts)
if len(raw)>16777216: raise RuntimeError('bundle_size')""")


def require(condition, code, status=502):
    if not condition:
        raise ServiceError(code, status)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def operation_key(owner, operation_id):
    require(bool(re.fullmatch(r'[0-9a-f]{64}', owner))
            and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', operation_id)),
            'motion_render_identity_invalid', 422)
    return sha((owner + ':' + operation_id).encode())


class ExecutionJournal:
    """One SQLite WAL/PVC, bounded work queue; no caller-selected file paths."""
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS motion_operations(
                key TEXT PRIMARY KEY,owner TEXT NOT NULL,operation_id TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,snapshot TEXT NOT NULL,state TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,result TEXT,error TEXT,
                updated REAL NOT NULL,created REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS motion_scenes(
                key TEXT PRIMARY KEY,binding TEXT NOT NULL,name TEXT NOT NULL,
                state TEXT NOT NULL,result TEXT,error TEXT,updated REAL NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def operation(self, owner, operation_id):
        key = operation_key(owner, operation_id)
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM motion_operations WHERE key=? AND owner=?', (key, owner)).fetchone()
        return dict(row) if row else None

    def submit(self, owner, operation_id, snapshot, *, max_pending=8, max_records=128):
        key = operation_key(owner, operation_id)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM motion_operations WHERE key=?', (key,)).fetchone()
            if row:
                require(row['snapshot_sha256'] == snapshot['sha256'], 'motion_operation_snapshot_conflict', 409)
                return dict(row)
            pending = db.execute("SELECT count(*) FROM motion_operations WHERE state IN ('queued','running')").fetchone()[0]
            count = db.execute('SELECT count(*) FROM motion_operations').fetchone()[0]
            require(pending < max_pending and count < max_records, 'motion_render_capacity_exceeded', 429)
            now = time.time()
            db.execute('INSERT INTO motion_operations(key,owner,operation_id,snapshot_sha256,snapshot,state,updated,created) '
                       'VALUES(?,?,?,?,?,?,?,?)', (key, owner, operation_id, snapshot['sha256'],
                        canonical_bytes(snapshot).decode(), 'queued', now, now))
        return self.operation(owner, operation_id)

    def update(self, key, **values):
        require(set(values) <= {'state', 'result', 'error', 'cancel_requested'}, 'motion_journal_update_invalid')
        values['updated'] = time.time()
        with self.lock, self.connect() as db:
            db.execute('UPDATE motion_operations SET ' + ','.join(k + '=?' for k in values) + ' WHERE key=?',
                       (*values.values(), key))

    def progress(self, key, stage, *, scene_index=None, completed_scenes=0, total_scenes=1):
        from .motion_render_adapter import safe_progress
        progress = safe_progress(dict(stage=stage, scene_index=scene_index,
            completed_scenes=completed_scenes, total_scenes=total_scenes, updated_at=time.time()))
        require(progress is not None, 'motion_progress_invalid')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT result,state FROM motion_operations WHERE key=?', (key,)).fetchone()
            if row is None or row['state'] not in {'running', 'queued'}:
                return
            result = json.loads(row['result']) if row['result'] else {}
            if 'cleanup_pending' in result:
                return
            result['progress'] = progress
            db.execute('UPDATE motion_operations SET result=?,updated=? WHERE key=?',
                       (json.dumps(result), progress['updated_at'], key))

    def next_operation(self):
        with self.lock, self.connect() as db:
            # A lost API response must not let a queued operation start another
            # Job while an earlier exact handle is still running or draining.
            row = db.execute("""SELECT operation.* FROM motion_operations AS operation
                WHERE operation.state IN ('queued','running')
                ORDER BY CASE WHEN operation.result IS NOT NULL OR EXISTS(
                    SELECT 1 FROM motion_scenes AS scene
                    WHERE scene.key LIKE operation.key || ':%'
                    AND scene.state IN ('submitting','running','cleanup_pending'))
                    THEN 0 ELSE 1 END,operation.updated,operation.created LIMIT 1""").fetchone()
        return dict(row) if row else None

    def cancelled(self, key):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT cancel_requested FROM motion_operations WHERE key=?', (key,)).fetchone()
        return bool(row and row[0])

    def scene(self, key):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM motion_scenes WHERE key=?', (key,)).fetchone()
        return dict(row) if row else None

    def scene_intent(self, key, binding, name):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM motion_scenes WHERE key=?', (key,)).fetchone()
            if row:
                require(row['binding'] == binding and row['name'] == name, 'motion_scene_identity_conflict', 409)
            else:
                db.execute('INSERT INTO motion_scenes(key,binding,name,state,updated) VALUES(?,?,?,?,?)',
                           (key, binding, name, 'prepared', time.time()))
        return self.scene(key)

    def scene_update(self, key, state, *, result=None, error=None):
        with self.lock, self.connect() as db:
            db.execute('UPDATE motion_scenes SET state=?,result=COALESCE(?,result),error=?,updated=? WHERE key=?',
                       (state, json.dumps(result) if result is not None else None, error, time.time(), key))


def production_resources(directory, *, image, release_id, namespace, name, binding, quality='draft', seconds=300):
    require(bool(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,48}', name))
            and bool(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', namespace)), 'motion_job_name_invalid', 422)
    require(bool(re.fullmatch(r'[A-Za-z0-9./:_-]+:latest@sha256:[0-9a-f]{64}', image)),
            'motion_immutable_latest_image_required', 422)
    require(bool(re.fullmatch(r'[A-Za-z0-9._-]{1,80}', release_id))
            and bool(re.fullmatch(r'[0-9a-f]{64}', binding)), 'motion_job_binding_invalid', 422)
    require(type(seconds) is int and seconds in {300, 600} and quality in {'draft', 'final'},
            'motion_job_budget_invalid', 422)
    frozen = freeze_input(directory, image_digest=image.rsplit('@', 1)[1], max_payload_bytes=BUNDLE_BYTES)
    manifest = json.loads((Path(directory) / 'manifest.json').read_bytes())
    require(COMPILER_TEMPLATES.get(manifest['compiler']) == manifest['template'] or
            ALL_CONTINUITY_COMPILER_TEMPLATES.get(manifest['compiler']) == manifest['template'] or
            CREATIVE_COMPILER_TEMPLATES.get(manifest['compiler']) == manifest['template'] or
            (manifest['compiler'], manifest['template']) in {
                (PRODUCTION_PROCESS_COMPILER, 'controlled-process-2')},
            'motion_production_template_not_supported', 422)
    labels = {'app': 'video-motion-renderer', LABEL + 'execution': binding[:32]}
    annotations = {ANNOTATION: binding, LABEL + 'bundle-sha256': frozen['bundle_sha256'],
                   LABEL + 'release-id': release_id, LABEL + 'input-sha256': frozen['manifest_sha256']}
    meta = {'namespace': namespace, 'labels': labels, 'annotations': annotations}
    payload, configs, projected = frozen['payload'], [], []
    for index, start in enumerate(range(0, len(payload), CHUNK_BYTES)):
        key, cm_name = f'part-{index:04d}', f'{name}-{index:02d}'
        configs.append(dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name=cm_name, **deepcopy(meta)),
            immutable=True, binaryData={key: base64.b64encode(payload[start:start + CHUNK_BYTES]).decode()}))
        projected.append(dict(configMap=dict(name=cm_name, items=[dict(key=key, path=key)])))
    security = dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, runAsNonRoot=True,
                    runAsUser=10001, runAsGroup=10001, capabilities={'drop': ['ALL']},
                    seccompProfile={'type': 'RuntimeDefault'})
    init = dict(name='prepare', image=image, command=['python3', '-c', CHUNK_INIT], securityContext=security,
        env=[dict(name='MOTION_BUNDLE_SHA256', value=frozen['bundle_sha256']),
             dict(name='MOTION_BUNDLE_PARTS', value=str(len(configs)))],
        resources=dict(requests=dict(cpu='50m', memory='64Mi'), limits=dict(cpu='250m', memory='256Mi')),
        volumeMounts=[dict(name='bundle', mountPath='/bundle', readOnly=True), dict(name='scratch', mountPath='/scratch')])
    renderer = dict(name='renderer', image=image, command=['python3', '/opt/renderer/experiment.py'],
        securityContext=security, env=[dict(name='MOTION_MANIFEST_SHA256', value=frozen['manifest_sha256']),
            dict(name='MOTION_IMAGE_DIGEST', value=image.rsplit('@', 1)[1]),
            dict(name='MOTION_QUALITY', value=quality), dict(name='MOTION_TIMEOUT_SECONDS', value=str(seconds))],
        resources=dict(requests=dict(cpu='1', memory='2Gi', **{'ephemeral-storage': '512Mi'}),
                       limits=dict(cpu='2', memory='4Gi', **{'ephemeral-storage': '4Gi'})),
        volumeMounts=[dict(name='scratch', mountPath='/input', subPath='input', readOnly=True),
                      dict(name='scratch', mountPath='/work', subPath='work'), dict(name='scratch', mountPath='/tmp', subPath='tmp')])
    pod = dict(restartPolicy='Never', automountServiceAccountToken=False, enableServiceLinks=False,
        securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, fsGroup=10001,
                             seccompProfile={'type': 'RuntimeDefault'}),
        nodeSelector={'kubernetes.io/arch': 'arm64'}, initContainers=[init], containers=[renderer],
        volumes=[dict(name='bundle', projected=dict(defaultMode=0o444, sources=projected)),
                 dict(name='scratch', emptyDir=dict(sizeLimit='4Gi'))], terminationGracePeriodSeconds=10)
    job = dict(apiVersion='batch/v1', kind='Job', metadata=dict(name=name, **deepcopy(meta)),
        spec=dict(backoffLimit=0, activeDeadlineSeconds=seconds * 2 + 90, ttlSecondsAfterFinished=86400,
                  template=dict(metadata=dict(labels=labels, annotations=annotations), spec=pod)))
    return configs, job


class KubeHTTP:
    def __init__(self, api, token_file, ca_file, namespace, *, client=None):
        require(bool(re.fullmatch(r'https://[A-Za-z0-9.\[\]:-]+', api)), 'motion_k8s_api_invalid', 422)
        require(bool(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', namespace)), 'motion_namespace_invalid', 422)
        self.api, self.namespace, self.token_file = api.rstrip('/'), namespace, Path(token_file)
        self.client = client or httpx.Client(verify=ssl.create_default_context(cafile=ca_file), timeout=20,
                                             follow_redirects=False, trust_env=False)

    def path(self, resource, name=None):
        group = {'jobs': 'apis/batch/v1', 'networkpolicies': 'apis/networking.k8s.io/v1'}.get(resource, 'api/v1')
        return f'/{group}/namespaces/{self.namespace}/{resource}' + ('/' + name if name else '')

    def request(self, method, resource, name=None, *, body=None, params=None, logs=False):
        suffix = '/log' if logs else ''
        try:
            token = self.token_file.read_text().strip()
            require(0 < len(token) <= 16384, 'motion_k8s_token_invalid', 503)
            with self.client.stream(method, self.api + self.path(resource, name) + suffix,
                    headers={'Authorization': 'Bearer ' + token}, json=body, params=params) as response:
                if response.status_code == 404:
                    return None
                if response.status_code == 409:
                    raise ServiceError('motion_k8s_conflict', 409)
                require(response.status_code < 400, UNAVAILABLE, 503)
                # K8s logs carry bounded base64; never log/print this payload.
                maximum = 9 * 1024 * 1024 if logs else 2 * 1024 * 1024
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    require(len(raw) <= maximum, 'motion_k8s_response_too_large')
                return bytes(raw) if logs else json.loads(raw)
        except (httpx.HTTPError, OSError, ValueError):
            raise ServiceError(UNAVAILABLE, 503) from None


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(_subset(a, b) for a, b in zip(expected, actual))
    return expected == actual


def unpack_artifacts(logs):
    require(isinstance(logs, bytes), 'motion_artifacts_invalid')
    prefix = b'VIDEO_MOTION_ARTIFACT_BASE64='
    rows = [line[len(prefix):] for line in logs.splitlines() if line.startswith(prefix)]
    require(len(rows) == 1 and len(rows[0]) <= (ARCHIVE_BYTES + 2) // 3 * 4, 'motion_artifacts_invalid')
    try:
        raw = base64.b64decode(rows[0], validate=True)
        require(len(raw) <= ARCHIVE_BYTES, 'motion_artifacts_too_large')
        entries, total = {}, 0
        allowed = {'experiment.json', 'render-receipt.json', 'observe-receipt.json', 'render.log', 'observe.log',
                   'output.mp4', 'observations.json', 'timeline.json', 'manifest.json', 'plan.json', 'beats.json',
                   'alignment.json', 'render.log.tail.txt', 'observe.log.tail.txt'}
        # Bound decompression before tarfile processes PAX/GNU extension
        # headers, which may otherwise allocate their declared size before a
        # member is yielded to the checks below.
        tar_limit = EXTRACT_BYTES + 1024 * 1024
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            expanded = compressed.read(tar_limit + 1)
        require(len(expanded) <= tar_limit, 'motion_artifacts_too_large')
        with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
            for member in archive:
                valid_name = member.name in allowed or bool(re.fullmatch(r'observations/[A-Za-z0-9_-]{1,100}\.jpg', member.name))
                total += member.size
                require(valid_name and member.isfile() and member.name not in entries and member.size >= 0
                        and total <= EXTRACT_BYTES and len(entries) < 256, 'motion_artifacts_invalid')
                content = archive.extractfile(member).read(member.size + 1)
                require(len(content) == member.size, 'motion_artifacts_invalid')
                entries[member.name] = content
        return entries
    except (ValueError, TypeError, OSError, EOFError, tarfile.TarError, zlib.error, RecursionError):
        raise ServiceError('motion_artifacts_invalid', 502) from None


def _creative_engine_observation(value, engine):
    require(type(value) is dict and set(value) == {'engine', 'revision', 'contexts'}
        and value['engine'] == engine and value['revision'] == 'animation-engine-runtime-1',
        'creative_engine_observation_invalid')
    rows = value['contexts']
    require(type(rows) is list and len(rows) <= 16, 'creative_engine_observation_invalid')
    kinds = []
    for row in rows:
        require(type(row) is dict and row.get('kind') in ({'2d', 'webgl2'} if engine == 'three'
            else {'2d'} if engine == 'canvas' else set()), 'creative_engine_observation_invalid')
        expected = {'kind', 'width', 'height', 'connected', 'visible'}
        if row['kind'] == 'webgl2':
            expected |= {'renderer', 'software', 'context_lost', 'gl_error'}
            require(type(row.get('renderer')) is str and 0 < len(row['renderer']) <= 2000
                and 'swiftshader' in row['renderer'].lower() and row.get('software') is True
                and row.get('context_lost') is False and type(row.get('gl_error')) is int
                and row['gl_error'] == 0, 'creative_engine_observation_invalid')
        require(set(row) == expected
            and all(type(row[key]) is int and 0 <= row[key] <= 4096 for key in ('width', 'height'))
            and all(type(row[key]) is bool for key in ('connected', 'visible')),
            'creative_engine_observation_invalid')
        if row['connected'] and row['visible'] and row['width'] > 0 and row['height'] > 0:
            kinds.append(row['kind'])
    require(engine == 'html-svg' or ('webgl2' if engine == 'three' else '2d') in kinds,
            'creative_native_context_missing')
    return value


def validate_creative_artifacts(entries, files, observations, runtime, quality):
    """Independent custody/clock checks; no generic aesthetic approval claim."""
    timeline, package = (json.loads(files[name]) for name in ('timeline.json', 'plan.json'))
    count = round(timeline['duration_seconds'] * 24)
    engine_aware = package['schema'] in {'video.creative-scene.v3', 'video.creative-scene.v4', 'video.creative-scene.v5'}
    renderer = 'isolated-chromium-engine-seek-ffmpeg-1' if engine_aware else 'isolated-chromium-absolute-seek-ffmpeg-1'
    require(type(observations) is dict and observations.get('schema') ==
            ('video.creative-observations.v2' if engine_aware else 'video.creative-observations.v1')
            and observations.get('input_sha256') == package['input_sha256']
            and observations.get('source_sha256') == package['result_sha256']
            and observations.get('renderer') == renderer
            and observations.get('frame_count') == count
            and observations.get('blocked_requests') == [] and observations.get('page_errors') == [],
            'creative_observation_binding_mismatch')
    if engine_aware:
        require(observations.get('engine') == package['engine'] and observations.get('idle_clock_verified') is True,
                'creative_engine_observation_invalid')
        require(observations.get('loaded_engine_assets') ==
            (['three.module.min.js'] if package['engine'] == 'three' else []), 'creative_engine_observation_invalid')
    samples = observations.get('samples')
    require(type(samples) is list and len(samples) == 20, 'creative_observation_samples_invalid')
    expected_times = [0, *[(count - 1) / 24 * (i + 1) / 18 for i in range(17)], (count - 1) / 24, 0]
    for index, (row, expected_time) in enumerate(zip(samples, expected_times)):
        filename = f'observations/creative-{index}.jpg'
        local = max(0, min(timeline['audio_seconds'], expected_time - timeline['leading_seconds']))
        require(type(row) is dict and row.get('path') == filename and row.get('time') == expected_time
                and row.get('local_seconds') == local and row.get('bytes') == len(entries[filename])
                and row.get('frame_sha256') == sha(entries[filename]), 'creative_observation_frame_mismatch')
        if engine_aware:
            _creative_engine_observation(row.get('engine_observation'), package['engine'])
    require(samples[0]['frame_sha256'] == samples[-1]['frame_sha256'], 'creative_reverse_seek_mismatch')
    capture = json.loads(entries['render-receipt.json']).get('creative_capture')
    require(type(capture) is dict and capture.get('schema') ==
            ('video.creative-capture.v2' if engine_aware else 'video.creative-capture.v1')
            and capture.get('renderer') == observations['renderer'] and capture.get('frame_count') == count
            and capture.get('first_sha256') == samples[0]['frame_sha256']
            and capture.get('last_sha256') == samples[-2]['frame_sha256']
            and capture.get('reverse_initial_sha256') == samples[-1]['frame_sha256']
            and capture.get('probe_sha256') == [row['frame_sha256'] for row in samples]
            and capture.get('blocked_requests') == [] and capture.get('page_errors') == [],
            'creative_render_observation_mismatch')
    if engine_aware:
        require(capture.get('engine') == package['engine'] and capture.get('idle_clock_verified') is True
            and capture.get('loaded_engine_assets') == observations['loaded_engine_assets']
            and capture.get('probe_engine_observations') == [row['engine_observation'] for row in samples],
            'creative_render_observation_mismatch')
    technical = dict(status='passed', renderer=observations['renderer'],
        reverse_seek_verified=True, source_sha256=package['result_sha256'],
        visual_quality='not_evaluated', aesthetic_approval=False)
    if package['schema'] == 'video.creative-scene.v5':
        technical.update(skill_catalog_sha256=package['skill_catalog_sha256'],
            skill_delivery_sha256=package['skill_delivery_sha256'],
            skill_decisions_sha256=digest(package['skill_decisions']),
            skill_tool_evidence_sha256=package['skill_tool_evidence_sha256'],
            skill_evidence_scope=package['evidence_scope'])
    elif package['schema'] in {'video.creative-scene.v2', 'video.creative-scene.v3', 'video.creative-scene.v4'}:
        technical.update(method_delivery_sha256=package['method_delivery_sha256'],
            method_decision_sha256=digest(package['method_decision']),
            method_evidence_scope=package['evidence_scope'])
    else:
        technical['capability_release_sha256'] = package['capability_release_sha256']
    if package['schema'] in {'video.creative-scene.v4', 'video.creative-scene.v5'}:
        # Source custody is independent of whether adjacent motion looks good.
        context = package['input_document']['film_context']
        technical.update(film_context_sha256=digest(context), film_sha256=context['film_sha256'],
            handoff_sha256=digest(package['result']['handoff']), continuity_quality='not_evaluated')
    if engine_aware:
        technical.update(engine=package['engine'], native_context_verified=True,
            idle_clock_verified=True, engine_evidence_scope='native_context_measured_not_visual_quality')
    return dict(technical_review=technical,
        media_sha256=sha(entries['output.mp4']), manifest_sha256=sha(files['manifest.json']),
        runtime_fingerprint=runtime, quality=quality)


def validate_artifacts(entries, files, runtime, quality):
    try:
        summary = json.loads(entries['experiment.json'])
        if summary.get('status') == 'artifact_budget_exceeded':
            raise ServiceError('motion_artifact_budget_exceeded', 413)
        require(summary.get('status') == 'pending_validation' and summary.get('stages') == {'render': 0, 'observe': 0},
                'motion_execution_failed')
        for name in ('manifest.json', 'timeline.json', 'plan.json', 'beats.json', 'alignment.json'):
            require(entries[name] == files[name], 'motion_artifact_input_mismatch')
        manifest_sha = sha(files['manifest.json'])
        for name, status in (('render-receipt.json', 'encoded_pending_validation'),
                             ('observe-receipt.json', 'observed_pending_validation')):
            receipt = json.loads(entries[name])
            require(receipt.get('schema') == 'video.motion-render-receipt.v1' and receipt.get('status') == status
                    and receipt.get('input_manifest_sha256') == manifest_sha
                    and receipt.get('timeline_sha256') == sha(files['timeline.json'])
                    and receipt.get('runtime') == {k: v for k, v in runtime.items() if k != 'image_digest'}
                    and receipt.get('runtime_binding', {}).get('status') == 'matched'
                    and receipt['runtime_binding'].get('image_digest') == runtime['image_digest']
                    and receipt.get('quality') == quality
                    and receipt.get('cli_quality') == {'draft': 'draft', 'final': 'delivery'}[quality],
                    'motion_runtime_receipt_mismatch')
            if name == 'render-receipt.json':
                require(receipt.get('decode_verified') is True
                        and receipt.get('output_sha256') == sha(entries['output.mp4']), 'motion_media_receipt_invalid')
        observations = json.loads(entries['observations.json'])
        if json.loads(files['manifest.json']).get('compiler') in CREATIVE_COMPILER_TEMPLATES:
            return validate_creative_artifacts(entries, files, observations, runtime, quality)
        for row in observations['windows']:
            for label in ('before', 'middle', 'after'):
                filename = f"observations/a{row['ordinal']}-{label}.jpg"
                require(sha(entries[filename]) == row[label]['frame_sha256'], 'motion_observation_frame_mismatch')
        for filename, field in (('seek-first', 'first_sha256'), ('seek-last', 'last_sha256'),
                                ('seek-repeat', 'repeated_first_sha256')):
            require(sha(entries[f'observations/{filename}.jpg']) == observations['reverse_seek'][field],
                    'motion_observation_frame_mismatch')
        if boundary := observations.get('continuity_boundary'):
            for key in ('transition_middle', 'transition_end'):
                if key in boundary:
                    filename = 'observations/continuity-' + key.replace('_', '-') + '.jpg'
                    require(sha(entries[filename]) == boundary[key]['frame_sha256'],
                            'motion_observation_frame_mismatch')
        if json.loads(files['timeline.json']).get('compiler') == 'motion-process-continuity-compiler-1':
            sequence = observations.get('seek_sequence')
            require(type(sequence) is dict and sequence.get('schema') == 'video.process-continuity-seek-sequence.v1'
                    and type(sequence.get('samples')) is list and 1 <= len(sequence['samples']) <= 32,
                    'motion_observation_sequence_invalid')
            for index, sample in enumerate(sequence['samples']):
                filename = f'observations/continuity-sequence-{index}.jpg'
                require(sha(entries[filename]) == sample['frame_sha256'],
                        'motion_observation_frame_mismatch')
        plan, timeline = json.loads(files['plan.json']), json.loads(files['timeline.json'])
        if plan.get('schema') == PROCESS_SCHEMA or timeline.get('compiler') == PRODUCTION_PROCESS_COMPILER:
            scene_compiler(plan, compiler_version=timeline.get('compiler'))
            reviewer = verify_process_observations
        else:
            reviewer = verify_observations
        if timeline.get('compiler') == 'motion-process-continuity-compiler-1':
            # Each declared frame was bound to its real artifact bytes above.
            # Keep that provenance when checking a repeated pose; a JPEG hash
            # is an integrity key, not a claim of identical browser rastering.
            frames = {sha(raw): raw for name, raw in entries.items()
                      if name.startswith('observations/') and name.endswith('.jpg')}
            review = reviewer(plan, timeline, observations, frame_bytes=frames)
        else:
            review = reviewer(plan, timeline, observations)
        return {'technical_review': review, 'media_sha256': sha(entries['output.mp4']),
                'manifest_sha256': manifest_sha, 'runtime_fingerprint': runtime, 'quality': quality}
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('motion_artifacts_invalid', 502) from None


class KubeSceneRenderer:
    def __init__(self, journal, kube, *, image, release_id, runtime, root, poll_seconds=2, seconds=300):
        self.journal, self.kube, self.image = journal, kube, image
        self.release_id, self.runtime, self.root = release_id, runtime, Path(root)
        self.poll_seconds, self.seconds = poll_seconds, seconds

    def check_policy(self):
        policies = self.kube.request('GET', 'networkpolicies')
        require(isinstance(policies, dict) and bool(policies.get('items')), 'motion_namespace_not_isolated', 503)
        deny = False
        for policy in policies['items']:
            spec = policy.get('spec', {})
            require(not spec.get('ingress') and not spec.get('egress'), 'motion_namespace_not_isolated', 503)
            if spec.get('podSelector') == {} and set(spec.get('policyTypes', [])) == {'Ingress', 'Egress'}:
                deny = True
        require(deny, 'motion_namespace_not_isolated', 503)

    def _bound(self, expected, actual):
        require(actual is not None and actual.get('metadata', {}).get('annotations', {}).get(ANNOTATION)
                == expected['metadata']['annotations'][ANNOTATION]
                and _subset(expected, actual), 'motion_k8s_resource_binding_mismatch')

    def _pods(self, name, binding):
        response = self.kube.request('GET', 'pods', params={'labelSelector': 'job-name=' + name})
        require(isinstance(response, dict) and isinstance(response.get('items'), list), UNAVAILABLE, 503)
        pods = response['items']
        require(len(pods) <= 1, 'motion_unexpected_pod_retry')
        for pod in pods:
            require(pod.get('metadata', {}).get('annotations', {}).get(ANNOTATION) == binding,
                    'motion_k8s_resource_binding_mismatch')
        return pods

    def _cleanup(self, configs, expected_job, binding):
        name = expected_job['metadata']['name']
        actual = self.kube.request('GET', 'jobs', name)
        fenced = False
        if actual:
            self._bound(expected_job, actual)
            fenced = actual['metadata'].get('annotations', {}).get(LABEL + 'submission-fence') == 'true'
            if fenced:
                require(actual['spec'].get('suspend') is True, 'motion_cancel_fence_invalid')
            else:
                self.kube.request('DELETE', 'jobs', name, body={'apiVersion': 'v1', 'kind': 'DeleteOptions',
                    'propagationPolicy': 'Foreground', 'preconditions': {'uid': actual['metadata']['uid']}})
        until = time.monotonic() + 45
        while (not fenced and self.kube.request('GET', 'jobs', name) is not None) or self._pods(name, binding):
            require(time.monotonic() < until, UNAVAILABLE, 503)
            time.sleep(self.poll_seconds)
        for config in configs:
            current = self.kube.request('GET', 'configmaps', config['metadata']['name'])
            if current:
                self._bound(config, current)
                self.kube.request('DELETE', 'configmaps', config['metadata']['name'], body={
                    'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': current['metadata']['uid']}})
                require(self.kube.request('GET', 'configmaps', config['metadata']['name']) is None,
                        UNAVAILABLE, 503)
        return fenced

    def _submission_fence(self, expected):
        """Reserve the same name without executing, fencing a late original POST.

        A permanent suspended, zero-Pod Job is preferable to falsely announcing
        cancellation after a racy 404. The bounded operation journal records it;
        no CM/audio/scratch is retained, and no render command is resubmitted.
        """
        fence = deepcopy(expected)
        fence['spec']['suspend'] = True
        fence['metadata']['annotations'][LABEL + 'submission-fence'] = 'true'
        name = expected['metadata']['name']
        try:
            self.kube.request('POST', 'jobs', body=fence)
        except ServiceError as error:
            if error.code not in {UNAVAILABLE, 'motion_k8s_conflict'}:
                raise
        actual = self.kube.request('GET', 'jobs', name)
        require(actual is not None, UNAVAILABLE, 503)
        self._bound(expected, actual)
        return actual

    def _failure(self, pod, report=None):
        """Preserve bounded known failure reasons; never publish diagnostic media."""
        try:
            logs = self.kube.request('GET', 'pods', pod['metadata']['name'], logs=True,
                                     params={'container': 'renderer', 'limitBytes': 9 * 1024 * 1024})
            require(logs is not None, UNAVAILABLE, 503)
            entries = unpack_artifacts(logs)
            summary = json.loads(entries['experiment.json'])
            if summary.get('status') == 'artifact_budget_exceeded':
                return ServiceError('motion_artifact_budget_exceeded', 413)
            for name in ('render-receipt.json', 'observe-receipt.json'):
                receipt = json.loads(entries.get(name, b'{}'))
                stage = receipt.get('stage')
                if report is not None and stage in {'rendering_scene', 'observing_scene',
                                                    'preparing_audio', 'validating_scene'}:
                    report(stage)
                if receipt.get('status') == 'timed_out':
                    return ServiceError('motion_execution_timed_out', 504)
                if receipt.get('status') == 'failed':
                    code = ('motion_observation_stage_failed' if name == 'observe-receipt.json' else
                            'motion_audio_mux_failed' if stage == 'preparing_audio' else
                            'motion_media_output_invalid' if stage == 'validating_scene' else
                            'motion_render_stage_failed')
                    return ServiceError(code, 502)
        except ServiceError as error:
            if error.code == UNAVAILABLE:
                raise
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            pass
        return ServiceError('motion_execution_failed', 502)

    def _progress_stage(self, pod):
        """Read only our bounded stage marker, never infer progress from stderr."""
        try:
            logs = self.kube.request('GET', 'pods', pod['metadata']['name'], logs=True,
                params={'container': 'renderer', 'limitBytes': 4096, 'tailLines': 8})
        except ServiceError:
            # Telemetry is optional. Missing logs must never fail or rerun a
            # valid Job; execution/cancellation are checked independently.
            return None
        if not isinstance(logs, (str, bytes)):
            return None
        logs = logs[-4096:]
        text = logs.decode('utf-8', errors='replace') if isinstance(logs, bytes) else logs
        for line in reversed(text.splitlines()):
            if not line.startswith('VIDEO_MOTION_PROGRESS='):
                continue
            try:
                value = json.loads(line[len('VIDEO_MOTION_PROGRESS='):])
            except (ValueError, TypeError):
                continue
            if (isinstance(value, dict) and value.get('schema') == 'video.motion-progress.v1'
                    and isinstance(value.get('stage'), str)
                    and value.get('stage') in {'rendering_scene', 'observing_scene'}):
                return value['stage']
        return None

    def _context(self, scene, key, scene_index, quality):
        motion = scene.get('motion')
        if scene['visual_mode'] == 'creative':
            creative = scene['creative']
            files = compile_creative_project(creative['package'], creative['beats'], creative['alignment'],
                runtime_fingerprint=self.runtime, leading_seconds=scene['timing']['leading_seconds'],
                trailing_seconds=scene['timing']['trailing_seconds'],
                compiler_version=creative['timeline']['compiler'])
            expected_files = creative['project_files_sha256']
        elif 'continuity' in scene:
            files = compile_continuity_project(scene, scene['continuity'], runtime_fingerprint=self.runtime)
            expected_files = scene['continuity_project_files_sha256']
        else:
            _, compiler, version = scene_compiler(motion['plan'], compiler_version=motion['timeline']['compiler'])
            files = compiler(motion['plan'], motion['beats'], motion['alignment'],
                runtime_fingerprint=self.runtime, leading_seconds=scene['timing']['leading_seconds'],
                trailing_seconds=scene['timing']['trailing_seconds'], compiler_version=version)
            expected_files = motion['project_files_sha256']
        require({name: sha(raw) for name, raw in files.items()} == expected_files,
                'motion_frozen_project_mismatch', 409)
        require(0 < scene['timing']['duration_seconds'] <= 60, 'motion_scene_duration_exceeded', 422)
        files['audio.wav'] = base64.b64decode(scene['audio']['data_base64'], validate=True)
        binding = sha(canonical_bytes(dict(operation=key, scene=scene_index,
                input=expected_files, image=self.image, quality=quality)))
        scene_key, name = f'{key}:{scene_index}', 'vm-' + sha(f'{key}:{scene_index}'.encode())[:36]
        directory = self.root / key / f'scene-{scene_index:03d}'
        # Resource recovery must not need another write to a full execution PVC.
        # Frozen inputs already live in the journal; this bounded copy uses /tmp.
        with tempfile.TemporaryDirectory(prefix='motion-input-') as scratch:
            input_dir = Path(scratch)
            for filename, raw in files.items():
                (input_dir / filename).write_bytes(raw)
            configs, expected = production_resources(input_dir, image=self.image, release_id=self.release_id,
                namespace=self.kube.namespace, name=name, binding=binding, quality=quality, seconds=self.seconds)
        return files, scene_key, binding, name, directory, configs, expected

    def _local_cleanup(self, directory, *, preserve_media):
        if not directory.exists():
            return
        require(directory.is_dir() and not directory.is_symlink(), 'motion_scratch_invalid')
        inputs = directory / 'input'
        if inputs.exists() or inputs.is_symlink():
            require(inputs.is_dir() and not inputs.is_symlink(), 'motion_scratch_invalid')
            shutil.rmtree(inputs)
        names = ['output.pending'] + ([] if preserve_media else ['output.mp4', 'evidence.json'])
        for name in names:
            path = directory / name
            require(not path.is_symlink(), 'motion_scratch_invalid')
            path.unlink(missing_ok=True)

    def _pending(self, scene_key, state, *, error=None, status=502, result=None, motion_context=None):
        record = self.journal.scene(scene_key)
        if state != 'completed':
            result = dict(result) if result is not None else None
            previous_context = result.pop('motion_context', None) if result is not None else None
            context = safe_motion_context(motion_context if motion_context is not None else previous_context)
            if context:
                result = {**(result or {}), 'motion_context': context}
        pending = dict(state=state, error=error, status=status, result=result,
                       fence_missing=record['state'] == 'submitting')
        self.journal.scene_update(scene_key, 'cleanup_pending', result={'cleanup_pending': pending}, error=error)

    def _finish_cleanup(self, scene_key, directory, configs, expected, binding, *, verify_output=True):
        pending = json.loads(self.journal.scene(scene_key)['result'])['cleanup_pending']
        try:
            if pending['fence_missing'] and self.kube.request('GET', 'jobs', expected['metadata']['name']) is None:
                self._submission_fence(expected)
            fenced = self._cleanup(configs, expected, binding)
            self._local_cleanup(directory, preserve_media=pending['state'] == 'completed')
        except Exception:
            # Keep the durable cleanup intent. Binding/UID conflicts are not
            # permission to delete a foreign object or abandon live resources.
            raise ServiceError(UNAVAILABLE, 503) from None
        result = pending['result']
        context = safe_motion_context(result.get('motion_context')) if isinstance(result, dict) else None
        if isinstance(result, dict) and 'motion_context' in result:
            result.pop('motion_context')
            if context:
                result['motion_context'] = context
        if fenced and result is None:
            result = {'submission_fenced': True}
        output = directory / 'output.mp4'
        if pending['state'] == 'completed' and verify_output:
            require(output.is_file() and not output.is_symlink() and output.stat().st_size <= ARCHIVE_BYTES
                    and sha(output.read_bytes()) == result['media_sha256'], 'motion_cached_media_corrupt')
        self.journal.scene_update(scene_key, pending['state'], result=result or {}, error=pending['error'])
        if pending['state'] != 'completed':
            error = ServiceError(pending['error'] or 'motion_execution_failed', pending['status'])
            if context:
                error.motion_context = context
            raise error
        return {**result, 'path': str(output)}

    def render(self, scene, *, key, scene_index, quality, cancel_event=None):
        files, scene_key, binding, name, directory, configs, expected = self._context(scene, key, scene_index, quality)
        record = self.journal.scene_intent(scene_key, binding, name)
        try:
            if record['state'] == 'cleanup_pending':
                return self._finish_cleanup(scene_key, directory, configs, expected, binding)
            return self._execute(files, scene_key, directory, configs, expected, binding, record,
                                 quality=quality, cancel_event=cancel_event)
        except Exception as failure:
            error = failure if isinstance(failure, ServiceError) else ServiceError(
                'motion_execution_storage_error' if isinstance(failure, OSError) else 'motion_execution_internal_error',
                507 if isinstance(failure, OSError) else 502)
            if error.code == UNAVAILABLE:
                raise error from None
            current = self.journal.scene(scene_key)
            if current['state'] in {'failed', 'cancelled', 'timed_out'} and current['error'] == error.code:
                raise error from None
            state = 'cancelled' if error.code == 'media_render_cancelled' else ('timed_out' if error.status == 504 else 'failed')
            self._pending(scene_key, state, error=error.code, status=error.status,
                          motion_context=getattr(error, 'motion_context', None))
            return self._finish_cleanup(scene_key, directory, configs, expected, binding)

    def _execute(self, files, scene_key, directory, configs, expected, binding, record, *, quality, cancel_event):
        name = expected['metadata']['name']
        report = getattr(cancel_event, 'report_progress', None)
        scene_index = int(scene_key.rsplit(':', 1)[1])
        last_stage = None
        def progress(stage):
            nonlocal last_stage
            if report is not None and last_stage != stage:
                report(stage, scene_index=scene_index, completed_scenes=scene_index)
                last_stage = stage
        output, evidence = directory / 'output.mp4', directory / 'evidence.json'
        if record['state'] == 'completed':
            result = json.loads(record['result'])
            self._cleanup(configs, expected, binding)
            require(output.is_file() and output.stat().st_size <= ARCHIVE_BYTES
                    and sha(output.read_bytes()) == result['media_sha256'], 'motion_cached_media_corrupt')
            self._local_cleanup(directory, preserve_media=True)
            return {**result, 'path': str(output)}
        if record['state'] in {'failed', 'cancelled', 'timed_out'}:
            self._pending(scene_key, record['state'], error=record['error'],
                          status=504 if record['state'] == 'timed_out' else (409 if record['state'] == 'cancelled' else 502),
                          result=json.loads(record['result']) if record['result'] else None)
            return self._finish_cleanup(scene_key, directory, configs, expected, binding)
        self.check_policy()
        progress('waiting_scene')
        cancelled = lambda: cancel_event is not None and cancel_event.is_set()
        actual = self.kube.request('GET', 'jobs', name)
        if actual:
            self._bound(expected, actual)
            if actual['metadata'].get('annotations', {}).get(LABEL + 'submission-fence') == 'true':
                error = 'media_render_cancelled' if cancelled() else 'motion_submission_unconfirmed'
                raise ServiceError(error, 409 if cancelled() else 502)
            self.journal.scene_update(scene_key, 'running')
        elif record['state'] == 'submitting':
            # After uncertain POST or process restart we only query this exact
            # identity. A suspended name reservation safely fences a late POST
            # without submitting the rendering workload another time.
            actual = self._submission_fence(expected)
            error = 'media_render_cancelled' if cancelled() else 'motion_submission_unconfirmed'
            raise ServiceError(error, 409 if cancelled() else 502)
        elif record['state'] == 'running':
            # A previously observed Job already committed. It cannot be
            # recreated implicitly after TTL/manual removal on a later restart.
            error = 'media_render_cancelled' if cancelled() else 'motion_execution_disappeared'
            raise ServiceError(error, 409 if cancelled() else 502)
        else:
            for config in configs:
                old = self.kube.request('GET', 'configmaps', config['metadata']['name'])
                if old is None:
                    try:
                        self.kube.request('POST', 'configmaps', body=config)
                    except ServiceError as error:
                        if error.code not in {UNAVAILABLE, 'motion_k8s_conflict'}:
                            raise
                    old = self.kube.request('GET', 'configmaps', config['metadata']['name'])
                    require(old is not None, UNAVAILABLE, 503)
                self._bound(config, old)
            if not cancelled():
                self.journal.scene_update(scene_key, 'submitting')
                try:
                    self.kube.request('POST', 'jobs', body=expected)
                except ServiceError as error:
                    if error.code not in {UNAVAILABLE, 'motion_k8s_conflict'}:
                        raise
                actual = self.kube.request('GET', 'jobs', name)
                require(actual is not None, UNAVAILABLE, 503)
                self._bound(expected, actual)
                self.journal.scene_update(scene_key, 'running')
        started = time.monotonic()
        while True:
            if cancelled():
                raise ServiceError('media_render_cancelled', 409)
            require(time.monotonic() - started < self.seconds * 2 + 150, 'motion_execution_timed_out', 504)
            actual = self.kube.request('GET', 'jobs', name)
            require(actual is not None, 'motion_execution_disappeared')
            self._bound(expected, actual)
            pods = self._pods(name, binding)
            for pod in pods:
                statuses = pod.get('status', {}).get('containerStatuses', []) + pod.get('status', {}).get('initContainerStatuses', [])
                for container in statuses:
                    if container.get('imageID'):
                        require(container['imageID'].endswith(self.runtime['image_digest']), 'motion_running_image_mismatch')
                    terminated = container.get('state', {}).get('terminated', {})
                    require(terminated.get('reason') != 'OOMKilled', 'motion_execution_oom')
                    if terminated.get('exitCode', 0) != 0:
                        raise (self._failure(pod, progress) if container.get('name') == 'renderer'
                               else ServiceError('motion_execution_failed', 502))
                if report is not None and any(status.get('name') == 'renderer'
                    and 'running' in status.get('state', {}) for status in statuses):
                    # Older immutable images have no marker; rendering_scene
                    # describes a running scene Job without inventing a phase.
                    progress(self._progress_stage(pod) or 'rendering_scene')
            conditions = actual.get('status', {}).get('conditions', [])
            failed = next((c for c in conditions if c.get('type') == 'Failed' and c.get('status') == 'True'), None)
            if failed:
                raise ServiceError('motion_execution_timed_out' if failed.get('reason') == 'DeadlineExceeded'
                                   else 'motion_execution_failed', 504 if failed.get('reason') == 'DeadlineExceeded' else 502)
            if actual.get('status', {}).get('succeeded') == 1:
                progress('validating_scene')
                require(len(pods) == 1, 'motion_execution_pod_missing')
                statuses = pods[0].get('status', {}).get('containerStatuses', [])
                require(len(statuses) == 1 and statuses[0].get('name') == 'renderer'
                        and statuses[0].get('imageID', '').endswith(self.runtime['image_digest'])
                        and statuses[0].get('state', {}).get('terminated', {}).get('exitCode') == 0,
                        'motion_running_image_mismatch')
                logs = self.kube.request('GET', 'pods', pods[0]['metadata']['name'], logs=True,
                                         params={'container': 'renderer', 'limitBytes': 9 * 1024 * 1024})
                require(logs is not None, UNAVAILABLE, 503)
                entries = unpack_artifacts(logs)
                result = validate_artifacts(entries, files, self.runtime, quality)
                directory.mkdir(parents=True, exist_ok=True)
                temporary = directory / 'output.pending'
                temporary.write_bytes(entries['output.mp4'])
                temporary.replace(output)
                evidence.write_bytes(canonical_bytes(result))
                self._pending(scene_key, 'completed', result=result)
                return self._finish_cleanup(scene_key, directory, configs, expected, binding)
            time.sleep(self.poll_seconds)

    def cancel_operation(self, snapshot, key, cancel_event):
        """Drain all durable scene handles before reporting a project cancel."""
        self.cleanup_operation(snapshot, key, state='cancelled', error='media_render_cancelled', status=409)

    def cleanup_operation(self, snapshot, key, *, state='failed', error='motion_execution_failed', status=502):
        """Recover only recorded identities, without invoking the render path."""
        for index, scene in enumerate(snapshot['scenes']):
            record = self.journal.scene(f'{key}:{index}')
            if scene['visual_mode'] not in {'motion', 'creative'} or record is None:
                continue
            _, scene_key, binding, name, directory, configs, expected = self._context(scene, key, index, snapshot['quality'])
            require(record['binding'] == binding and record['name'] == name, UNAVAILABLE, 503)
            if record['state'] != 'cleanup_pending':
                if record['state'] == 'completed':
                    self._pending(scene_key, 'completed', result=json.loads(record['result']))
                elif record['state'] in {'failed', 'cancelled', 'timed_out'}:
                    self._pending(scene_key, record['state'], error=record['error'],
                                  status=504 if record['state'] == 'timed_out' else (409 if record['state'] == 'cancelled' else 502),
                                  result=json.loads(record['result']) if record['result'] else None)
                else:
                    self._pending(scene_key, state, error=error, status=status)
            try:
                # Draining an operation preserves historical successful media;
                # media validation belongs to the caller requesting that media.
                self._finish_cleanup(scene_key, directory, configs, expected, binding, verify_output=False)
            except ServiceError as failure:
                if failure.code == UNAVAILABLE:
                    raise
