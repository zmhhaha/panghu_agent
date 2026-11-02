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
import sqlite3
import ssl
import tarfile
import threading
import time
import zlib

import httpx

from agent_service.contracts import ServiceError, canonical_bytes
from .motion import COMPILER_TEMPLATES, compile_project
from .motion_review import verify_observations
from .motion_renderer.job import FILES, INIT, freeze_input
from .motion_render_adapter import UNAVAILABLE

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

    def next_operation(self):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM motion_operations WHERE state IN ('queued','running') ORDER BY updated,created LIMIT 1").fetchone()
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
    require(COMPILER_TEMPLATES.get(manifest['compiler']) == manifest['template'],
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
        for row in observations['windows']:
            for label in ('before', 'middle', 'after'):
                filename = f"observations/a{row['ordinal']}-{label}.jpg"
                require(sha(entries[filename]) == row[label]['frame_sha256'], 'motion_observation_frame_mismatch')
        for filename, field in (('seek-first', 'first_sha256'), ('seek-last', 'last_sha256'),
                                ('seek-repeat', 'repeated_first_sha256')):
            require(sha(entries[f'observations/{filename}.jpg']) == observations['reverse_seek'][field],
                    'motion_observation_frame_mismatch')
        review = verify_observations(json.loads(files['plan.json']), json.loads(files['timeline.json']), observations)
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

    def _failure(self, pod):
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
                if receipt.get('status') == 'timed_out':
                    return ServiceError('motion_execution_timed_out', 504)
        except ServiceError as error:
            if error.code == UNAVAILABLE:
                raise
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            pass
        return ServiceError('motion_execution_failed', 502)

    def render(self, scene, *, key, scene_index, quality, cancel_event=None):
        motion = scene['motion']
        files = compile_project(motion['plan'], motion['beats'], motion['alignment'],
            runtime_fingerprint=self.runtime, leading_seconds=scene['timing']['leading_seconds'],
            trailing_seconds=scene['timing']['trailing_seconds'], compiler_version=motion['timeline']['compiler'])
        require({name: sha(raw) for name, raw in files.items()} == motion['project_files_sha256'],
                'motion_frozen_project_mismatch', 409)
        require(0 < scene['timing']['duration_seconds'] <= 60, 'motion_scene_duration_exceeded', 422)
        files['audio.wav'] = base64.b64decode(scene['audio']['data_base64'], validate=True)
        binding = sha(canonical_bytes(dict(operation=key, scene=scene_index,
                input=motion['project_files_sha256'], image=self.image, quality=quality)))
        scene_key, name = f'{key}:{scene_index}', 'vm-' + sha(f'{key}:{scene_index}'.encode())[:36]
        directory = self.root / key / f'scene-{scene_index:03d}'
        directory.mkdir(parents=True, exist_ok=True)
        input_dir = directory / 'input'
        input_dir.mkdir(exist_ok=True)
        for filename, raw in files.items():
            (input_dir / filename).write_bytes(raw)
        configs, expected = production_resources(input_dir, image=self.image, release_id=self.release_id,
            namespace=self.kube.namespace, name=name, binding=binding, quality=quality, seconds=self.seconds)
        record = self.journal.scene_intent(scene_key, binding, name)
        output, evidence = directory / 'output.mp4', directory / 'evidence.json'
        if record['state'] == 'completed':
            result = json.loads(record['result'])
            require(output.is_file() and output.stat().st_size <= ARCHIVE_BYTES
                    and sha(output.read_bytes()) == result['media_sha256'], 'motion_cached_media_corrupt')
            self._cleanup(configs, expected, binding)
            return {**result, 'path': str(output)}
        if record['state'] in {'failed', 'cancelled', 'timed_out'}:
            self._cleanup(configs, expected, binding)
            raise ServiceError(record['error'] or 'motion_execution_failed', 502)
        self.check_policy()
        cancelled = lambda: cancel_event is not None and cancel_event.is_set()
        actual = self.kube.request('GET', 'jobs', name)
        if actual:
            self._bound(expected, actual)
            if actual['metadata'].get('annotations', {}).get(LABEL + 'submission-fence') == 'true':
                self._cleanup(configs, expected, binding)
                error = 'media_render_cancelled' if cancelled() else 'motion_submission_unconfirmed'
                self.journal.scene_update(scene_key, 'cancelled' if cancelled() else 'failed',
                                          error=error, result={'submission_fenced': True})
                raise ServiceError(error, 409 if cancelled() else 502)
            self.journal.scene_update(scene_key, 'running')
        elif record['state'] == 'submitting':
            # After uncertain POST or process restart we only query this exact
            # identity. A suspended name reservation safely fences a late POST
            # without submitting the rendering workload another time.
            actual = self._submission_fence(expected)
            self._cleanup(configs, expected, binding)
            state = 'cancelled' if cancelled() else 'failed'
            error = 'media_render_cancelled' if cancelled() else 'motion_submission_unconfirmed'
            self.journal.scene_update(scene_key, state, error=error,
                                      result={'submission_fenced': True})
            raise ServiceError(error, 409 if cancelled() else 502)
        elif record['state'] == 'running':
            # A previously observed Job already committed. It cannot be
            # recreated implicitly after TTL/manual removal on a later restart.
            self._cleanup(configs, expected, binding)
            error = 'media_render_cancelled' if cancelled() else 'motion_execution_disappeared'
            self.journal.scene_update(scene_key, 'cancelled' if cancelled() else 'failed', error=error)
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
        try:
            while True:
                if cancelled():
                    self._cleanup(configs, expected, binding)
                    self.journal.scene_update(scene_key, 'cancelled', error='media_render_cancelled')
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
                            raise (self._failure(pod) if container.get('name') == 'renderer'
                                   else ServiceError('motion_execution_failed', 502))
                conditions = actual.get('status', {}).get('conditions', [])
                failed = next((c for c in conditions if c.get('type') == 'Failed' and c.get('status') == 'True'), None)
                if failed:
                    raise ServiceError('motion_execution_timed_out' if failed.get('reason') == 'DeadlineExceeded'
                                       else 'motion_execution_failed', 504 if failed.get('reason') == 'DeadlineExceeded' else 502)
                if actual.get('status', {}).get('succeeded') == 1:
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
                    temporary = directory / 'output.pending'
                    temporary.write_bytes(entries['output.mp4'])
                    temporary.replace(output)
                    evidence.write_bytes(canonical_bytes(result))
                    self.journal.scene_update(scene_key, 'completed', result=result)
                    self._cleanup(configs, expected, binding)
                    return {**result, 'path': str(output)}
                time.sleep(self.poll_seconds)
        except ServiceError as error:
            if error.code == UNAVAILABLE:
                raise
            self._cleanup(configs, expected, binding)
            state = 'cancelled' if error.code == 'media_render_cancelled' else ('timed_out' if error.status == 504 else 'failed')
            self.journal.scene_update(scene_key, state, error=error.code)
            raise

    def cancel_operation(self, snapshot, key, cancel_event):
        """Drain all durable scene handles before reporting a project cancel."""
        for index, scene in enumerate(snapshot['scenes']):
            if scene['visual_mode'] != 'motion' or self.journal.scene(f'{key}:{index}') is None:
                continue
            try:
                self.render(scene, key=key, scene_index=index, quality=snapshot['quality'], cancel_event=cancel_event)
            except ServiceError as error:
                if error.code == UNAVAILABLE:
                    raise
                record = self.journal.scene(f'{key}:{index}')
                actual = self.kube.request('GET', 'jobs', record['name'])
                safe_fence = (actual is not None and actual.get('spec', {}).get('suspend') is True
                    and actual.get('metadata', {}).get('annotations', {}).get(ANNOTATION) == record['binding']
                    and actual['metadata']['annotations'].get(LABEL + 'submission-fence') == 'true')
                if (actual is not None and not safe_fence) or self._pods(record['name'], record['binding']):
                    raise ServiceError(UNAVAILABLE, 503) from None
