"""Bounded HTTP acceptance runner. No mutation without an explicit --execute.

Run from the repository root with ``python -m video_workbench.deploy.verify_live``.
The transport endpoint is normally an authenticated operator's port-forward;
Host/Origin/owner are explicit. This does not test ingress authentication or
visual quality. Never point header-based operator access at an untrusted proxy.

An evidence directory is created exclusively, including for read-only runs.
No request is retried. An uncertain mutation stops the run; inspect the marked
project and recorded intent through GETs before planning a separate recovery.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.agent import VideoAgentGateway


MAX_RESPONSE = 8 * 1024 * 1024
SCENE_FIELDS = ('title', 'narration', 'visual_description', 'duration_seconds', 'format')
SCRIPT = ('请恰好用两个镜头解释：文稿先拆成镜头，每个镜头生成可编辑的静态图形；'
          '审阅者比较候选，明确采用后导出素材。第一镜展示文稿到镜头，第二镜展示审阅到导出。')
STYLE = '深蓝背景，白色中文标题，青绿色重点；仅静态 SVG/HTML，清楚的留白和流程图。'


class VerificationFailure(RuntimeError):
    """Only controlled codes, never HTTP bodies, credentials or exception text."""

    def __init__(self, code, *, uncertain=False):
        super().__init__(code)
        self.code, self.uncertain = code, uncertain


def require(condition, code='http_evidence_invalid'):
    if not condition:
        raise VerificationFailure(code)


def canonical_uuid(value):
    try:
        require(type(value) is str and str(UUID(value)) == value, 'server_identifier_invalid')
    except (ValueError, TypeError, AttributeError):
        raise VerificationFailure('server_identifier_invalid') from None
    return value


def origin(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path in {'', '/'} and parsed.port != 0
                 and all(33 <= ord(c) < 127 for c in value))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('An explicit HTTP(S) origin is required')
    return value.rstrip('/')


@dataclass(frozen=True)
class Settings:
    workbench_url: str
    origin: str
    host: str
    owner: str
    agent_url: str
    agent_token_file: Path
    output: Path
    execute: bool = False
    project_id: str | None = None
    run_id: str = ''
    poll_interval: float = 2.0
    poll_timeout: float = 660.0
    pause_for_faults: bool = False
    checkpoint_timeout: float = 900.0

    def __post_init__(self):
        for value in (self.workbench_url, self.origin, self.agent_url):
            origin(value)
        if (urlsplit(self.origin).netloc != self.host or not self.owner
                or len(self.owner) > 200 or any(not 33 <= ord(c) <= 126 for c in self.owner)):
            raise ValueError('Explicit matching Origin/Host and an ordinary owner are required')
        if not self.agent_token_file.is_absolute() or not self.output.is_absolute():
            raise ValueError('Credential and evidence paths must be absolute')
        if self.project_id is not None:
            canonical_uuid(self.project_id)
        if self.execute and self.project_id is not None:
            raise ValueError('Execute creates a new marked project; project-id is read-only')
        if self.pause_for_faults and not self.execute:
            raise ValueError('Operator checkpoints require explicit execution')
        marker = self.run_id or str(uuid4())
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,64}', marker):
            raise ValueError('An ordinary unique run identifier is required')
        object.__setattr__(self, 'run_id', marker)
        for value, minimum, maximum in ((self.poll_interval, .1, 10), (self.poll_timeout, 1, 900),
                                        (self.checkpoint_timeout, 1, 1800)):
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError('Bounded polling settings are required')


def read_token(path):
    try:
        # Projected Kubernetes secrets can be symlinks. Resolve only for reading.
        require(path.is_file() and path.stat().st_size <= 514, 'credential_file_invalid')
        raw = path.read_bytes()
        token = raw.decode('ascii').rstrip('\r\n')
        require(32 <= len(token) <= 512 and all(33 <= ord(c) <= 126 for c in token),
                'credential_file_invalid')
        return token
    except (OSError, UnicodeError):
        raise VerificationFailure('credential_file_invalid') from None


def decode_object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'response_json_invalid')
            result[key] = value
        return result

    def constant(value):
        raise VerificationFailure('response_json_invalid')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        require(type(value) is dict, 'response_json_invalid')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise VerificationFailure('response_json_invalid') from None


class EvidenceAgentClient(AgentClient):
    """Retain the exact read-only snapshots that Gateway.collect validates."""

    def get_job(self, job_id):
        self.last_job = super().get_job(job_id)
        return self.last_job

    def artifact_manifest(self, job_id):
        self.last_manifest = super().artifact_manifest(job_id)
        return self.last_manifest

    def download_artifact(self, artifact_id):
        self.last_artifact = super().download_artifact(artifact_id)
        return self.last_artifact


class Runner:
    def __init__(self, settings, *, workbench_transport=None, agent_transport=None,
                 monotonic=time.monotonic, sleep=time.sleep):
        self.settings = settings
        self.clock, self.sleep = monotonic, sleep
        self.token = read_token(settings.agent_token_file)
        self.secrets = [self.token.encode(), json.dumps(self.token, ensure_ascii=False)[1:-1].encode()]
        self.csrf = None
        self.http = httpx.Client(base_url=origin(settings.workbench_url), timeout=10,
            follow_redirects=False, trust_env=False, transport=workbench_transport,
            headers={'Host': settings.host, 'Origin': origin(settings.origin),
                     'X-Forwarded-User': settings.owner, 'Accept-Encoding': 'identity'})
        self.agent = EvidenceAgentClient(origin(settings.agent_url), self.token, transport=agent_transport)
        self.gateway = VideoAgentGateway(self.agent)
        self.report = dict(schema_version='1', run_id=settings.run_id,
            mode='execute' if settings.execute else 'read_only', status='running',
            scope='operator_http_business_protocol', project_id=settings.project_id,
            project_marker=f'[LIVE VERIFY {settings.run_id}]', steps=[], intents=[],
            generation_posts=0, completed_generation_tasks=0, cost='unknown',
            visual_quality='not_run', ingress_authentication='not_run',
            running_cancellation='not_run', submission_loss_recovery='not_run')
        self.project = None
        self.started = self.clock()

    def _safe_bytes(self, raw):
        require(not any(secret in raw for secret in self.secrets), 'credential_in_evidence_rejected')
        return raw

    def save(self, name, value):
        raw = value if type(value) is bytes else canonical_bytes(value)
        self._safe_bytes(raw)
        target = self.settings.output / name
        temporary = target.with_name(target.name + '.tmp')
        with temporary.open('wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)

    def flush(self):
        self.report['elapsed_seconds'] = round(self.clock() - self.started, 3)
        self.save('report.json', self.report)

    def checkpoint(self, name, document=None):
        """An operator gate; a release file proves intent, not fault acceptance."""
        if not self.settings.pause_for_faults:
            return
        content = dict(schema_version='video-operator-checkpoint.v1', run_id=self.settings.run_id,
                       project_id=self.report['project_id'], checkpoint=name, document=document)
        checksum = digest(content)
        self.save(name + '-checkpoint.json', content)
        release = self.settings.output / (name + '-continue.json')
        self.report['waiting_for_operator'] = dict(checkpoint=name, checkpoint_sha256=checksum)
        self.flush()
        deadline = self.clock() + self.settings.checkpoint_timeout
        while True:
            if release.exists():
                require(release.is_file() and release.stat().st_size <= 4096, 'operator_release_invalid')
                approved = decode_object(release.read_bytes())
                require(approved == dict(checkpoint_sha256=checksum, run_id=self.settings.run_id),
                        'operator_release_invalid')
                self.report['waiting_for_operator'] = None
                self.flush()
                return
            if self.clock() >= deadline:
                raise VerificationFailure('operator_checkpoint_timeout')
            self.sleep(min(self.settings.poll_interval, max(0, deadline - self.clock())))

    @contextmanager
    def step(self, name):
        entry = dict(name=name, status='running')
        self.report['steps'].append(entry)
        start = self.clock()
        self.flush()
        try:
            yield entry
            entry['status'] = 'passed'
        except BaseException:
            entry['status'] = 'stopped'
            raise
        finally:
            entry['elapsed_seconds'] = round(self.clock() - start, 3)
            self.flush()

    def request(self, method, path, *, body=None, expected=(200,), idem=None, raw=False):
        require(method == 'GET' or self.settings.execute, 'execution_not_authorized')
        headers = {}
        if method != 'GET':
            headers['X-CSRF-Token'] = self.csrf or ''
        if idem:
            headers['Idempotency-Key'] = idem
        mutation = method != 'GET'
        try:
            with self.http.stream(method, path, json=body, headers=headers) as response:
                require(response.headers.get('content-encoding', 'identity').lower() == 'identity',
                        'response_encoding_invalid')
                content = bytearray()
                for chunk in response.iter_bytes():
                    require(len(content) + len(chunk) <= MAX_RESPONSE, 'response_too_large')
                    content.extend(chunk)
                if response.status_code not in expected:
                    raise VerificationFailure('http_status_' + str(response.status_code),
                        uncertain=mutation and (response.status_code >= 500 or response.is_redirect))
                return bytes(content) if raw else decode_object(bytes(content))
        except (httpx.HTTPError, OSError):
            raise VerificationFailure('mutation_response_unknown' if mutation else 'http_unavailable',
                                      uncertain=mutation) from None
        except VerificationFailure as error:
            if mutation and error.code in {'response_json_invalid', 'response_too_large', 'response_encoding_invalid'}:
                raise VerificationFailure('mutation_response_unknown', uncertain=True) from None
            raise

    def refresh(self):
        self.project = self.request('GET', '/api/projects/' + self.report['project_id'])
        require(self.project.get('id') == self.report['project_id'], 'project_identity_mismatch')
        self.save('project.json', self.project)
        return self.project

    def scene(self, scene_id):
        found = [s for s in self.project['scenes'] if s['id'] == scene_id]
        require(len(found) == 1, 'scene_identity_mismatch')
        return found[0]

    def preflight(self):
        with self.step('read_only_preflight'):
            session = self.request('GET', '/api/session')
            require(session.get('user_id') == self.settings.owner and session.get('agent_configured') is True,
                    'workbench_session_invalid')
            require(type(session.get('csrf_token')) is str and re.fullmatch(r'[a-f0-9]{64}', session['csrf_token']),
                    'workbench_session_invalid')
            self.csrf = session['csrf_token']
            self.secrets.append(self.csrf.encode())
            capabilities = self.agent.capabilities()
            require(capabilities.get('api_version') == '1' and type(capabilities.get('profiles')) is list,
                    'agent_capabilities_invalid')
            accepted = []
            for name in ('video.storyboard.v1', 'video.scene.v1'):
                rows = [p for p in capabilities['profiles'] if type(p) is dict and p.get('profile') == name
                        and p.get('profile_revision') == '1']
                require(len(rows) == 1 and all(rows[0].get(k) is True for k in
                        ('configured', 'ready', 'supports_inputs')), 'video_profile_unavailable')
                accepted.append({k: rows[0].get(k) for k in
                    ('profile', 'profile_revision', 'configured', 'ready', 'supports_inputs', 'max_timeout_seconds')})
            self.save('capabilities.json', dict(api_version='1', profiles=accepted))
            if self.report['project_id']:
                self.refresh()

    def input_document(self, kind, scene_id=None, base_source=None, instruction=''):
        p = self.project
        if kind == 'storyboard':
            return dict(schema_version='1', kind='storyboard', title=p['title'], script=p['script'],
                        style=p['style'], max_scenes=2)
        s = self.scene(scene_id)
        return dict(schema_version='1', kind='scene', title=s['title'], narration=s['narration'],
            visual_description=s['visual_description'], style=p['style'], format=s['format'],
            width=1280, height=720, duration_seconds=s['duration_seconds'],
            base_source=base_source, instruction=instruction)

    def generate(self, label, kind, *, scene_id=None, base_version=None, base_source=None, instruction=''):
        with self.step(label) as step:
            document = self.input_document(kind, scene_id, base_source, instruction)
            self.save(label + '-input.json', document)
            expected_sha = digest(document)
            body = dict(expected_revision=self.project['revision'])
            if kind == 'storyboard':
                body['max_scenes'] = 2
                path = '/api/projects/' + self.report['project_id'] + '/storyboard'
                previous_versions, previous_head = [], None
            else:
                body.update(base_version_id=base_version, instruction=instruction)
                path = '/api/scenes/' + scene_id + ('/revise' if base_version else '/generate')
                previous_versions = list(self.scene(scene_id)['versions'])
                previous_head = self.scene(scene_id)['head_version_id']
            require(self.report['generation_posts'] < 4, 'generation_budget_exhausted')
            intent = dict(step=label, idempotency_key='live-' + self.settings.run_id + '-' + label,
                path=path, request_sha256=digest(body), input_sha256=expected_sha,
                state='submission_started', recovery_get='/api/projects/' + self.report['project_id'])
            self.report['intents'].append(intent)
            self.report['generation_posts'] += 1
            self.flush()  # durable intent BEFORE the single POST; never retry it
            op = self.request('POST', path, body=body, expected=(202,), idem=intent['idempotency_key'])
            try:
                operation_id = canonical_uuid(op.get('id'))
                require(op.get('kind') == ('storyboard' if kind == 'storyboard' else 'revise' if base_version else 'scene'),
                        'operation_kind_invalid')
            except VerificationFailure:
                raise VerificationFailure('mutation_response_unknown', uncertain=True) from None
            intent.update(operation_id=operation_id, state='polling')
            self.flush()
            deadline = self.clock() + self.settings.poll_timeout
            for _ in range(math.ceil(self.settings.poll_timeout / self.settings.poll_interval) + 1):
                self.refresh()
                current = self.project.get('storyboard_operation') if kind == 'storyboard' else self.scene(scene_id).get('operation')
                require(type(current) is dict and current.get('id') == operation_id, 'operation_identity_mismatch')
                if current.get('job_id'):
                    intent['job_id'] = canonical_uuid(current['job_id'])
                if current.get('status') == 'completed':
                    require(current.get('error') is None, 'operation_completed_with_error')
                    break
                require(current.get('status') in {'queued', 'preparing', 'uploading', 'submitting', 'running',
                        'collecting', 'cancelling', 'recovering'}, 'operation_not_completed')
                if self.clock() >= deadline:
                    raise VerificationFailure('operation_poll_timeout')
                self.sleep(min(self.settings.poll_interval, max(0, deadline - self.clock())))
            else:
                raise VerificationFailure('operation_poll_timeout')
            job_id = canonical_uuid(current.get('job_id'))
            result = self.gateway.collect(job_id, kind, expected_sha)
            job = self.agent.last_job
            require(job.get('business_ref') == 'video:' + operation_id, 'job_business_ref_mismatch')
            manifest = self.agent.last_manifest
            # Save only the public proof fields, never an arbitrary future Job payload.
            self.save(label + '-job.json', {k: job.get(k) for k in ('job_id', 'state', 'business_ref',
                'publication_authorized', 'artifact_manifest_sha256', 'pins', 'attempt', 'stop_proven', 'stop_proof')})
            self.save(label + '-manifest.json', manifest)
            self.save(label + '-result.json', result)
            self.save(label + '-artifact.json', self.agent.last_artifact)
            output = result['output']
            if kind == 'storyboard':
                scenes = self.project['scenes']
                require(1 <= len(scenes) <= 2 and len(scenes) == len(output['scenes']), 'storyboard_scene_count_invalid')
                for saved, produced in zip(scenes, output['scenes']):
                    require({k: saved[k] for k in SCENE_FIELDS} == produced, 'storyboard_candidate_mismatch')
                    require(not saved['versions'] and saved['head_version_id'] is None, 'implicit_adoption_detected')
                version = None
            else:
                scene = self.scene(scene_id)
                require(scene['head_version_id'] == previous_head, 'implicit_adoption_detected')
                require(scene['versions'][:-1] == previous_versions and len(scene['versions']) == len(previous_versions) + 1,
                        'candidate_history_mismatch')
                version = scene['versions'][-1]
                canonical_uuid(version['id'])
                source = self.request('GET', '/api/versions/' + version['id'] + '/source', raw=True)
                require(source == output['source'].encode('utf-8') and version['format'] == output['format']
                        and version['source_sha256'] == hashlib.sha256(source).hexdigest()
                        and version['summary'] == output['summary'] and version['parent_version_id'] == base_version,
                        'saved_candidate_mismatch')
                self.save(label + '.' + version['format'], source)
            intent['state'] = 'validated_completed'
            self.report['completed_generation_tasks'] += 1
            step.update(job_id=job_id, operation_id=operation_id, input_sha256=expected_sha,
                        manifest_sha256=manifest['sha256'])
            return version

    def edit_formats(self):
        with self.step('prepare_svg_and_html_scenes'):
            # max_scenes is an upper bound. One valid automatic scene is extended
            # manually, explicitly recorded, without another generation request.
            if len(self.project['scenes']) == 1:
                self.project = self.request('POST', '/api/projects/' + self.report['project_id'] + '/scenes',
                    body=dict(title='审阅与导出', narration='审阅候选，明确采用后导出。',
                              visual_description='并列展示候选、采用按钮和素材文件夹。', duration_seconds=8, format='html'))
                self.report['manual_second_scene_added'] = True
            else:
                self.report['manual_second_scene_added'] = False
            require(len(self.project['scenes']) == 2, 'storyboard_scene_count_invalid')
            ids = [canonical_uuid(s['id']) for s in self.project['scenes']]
            for scene_id, format in zip(ids, ('svg', 'html')):
                scene = self.scene(scene_id)
                body = {k: scene[k] for k in SCENE_FIELDS}
                body.update(format=format, expected_revision=self.project['revision'])
                self.project = self.request('PATCH', '/api/scenes/' + scene_id, body=body)
            self.refresh()
            return ids

    def adopt(self, scene_id, version_id, previous_head):
        self.project = self.request('POST', '/api/scenes/' + scene_id + '/adopt',
            body=dict(version_id=version_id, expected_head_version_id=previous_head))
        require(self.scene(scene_id)['head_version_id'] == version_id, 'adoption_failed')

    def export(self, label, versions):
        raw = self.request('GET', '/api/projects/' + self.report['project_id'] + '/export', raw=True)
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.infolist()
                expected_names = {'manifest.json', 'index.html', 'README.txt'}
                for i, version in enumerate(versions, 1):
                    expected_names |= {f'scenes/{i:03d}.{version["format"]}', f'preview/{i:03d}.html'}
                require(len(info) == len(expected_names) and {m.filename for m in info} == expected_names
                        and all(not m.is_dir() and not m.flag_bits & 1 and m.file_size <= MAX_RESPONSE for m in info)
                        and sum(m.file_size for m in info) <= MAX_RESPONSE, 'export_archive_invalid')
                files = {m.filename: self._safe_bytes(archive.read(m)) for m in info}
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as error:
            if isinstance(error, VerificationFailure):
                raise
            raise VerificationFailure('export_archive_invalid') from None
        manifest = decode_object(files['manifest.json'])
        require(manifest.get('schema_version') == '1' and manifest.get('title') == self.project['title']
                and manifest.get('style') == self.project['style'] and len(manifest.get('scenes', [])) == len(versions),
                'export_manifest_invalid')
        for i, (row, version, scene) in enumerate(zip(manifest['scenes'], versions, self.project['scenes']), 1):
            source_path, preview_path = f'scenes/{i:03d}.{version["format"]}', f'preview/{i:03d}.html'
            require(row.get('index') == i and row.get('version_id') == version['id']
                and row.get('source_sha256') == version['source_sha256']
                and row.get('format') == version['format'] and row.get('source') == source_path
                and row.get('preview') == preview_path and row.get('title') == scene['title']
                and row.get('narration') == scene['narration'] and row.get('duration_seconds') == scene['duration_seconds']
                and hashlib.sha256(files[source_path]).hexdigest() == version['source_sha256'], 'export_version_mismatch')
        self.save(label + '.zip', raw)
        self.save(label + '-manifest.json', manifest)
        return manifest

    def execute(self):
        with self.step('create_marked_project'):
            self.project = self.request('POST', '/api/projects', body=dict(
                title=self.report['project_marker'] + ' 文稿到素材联调', script=SCRIPT, style=STYLE))
            self.report['project_id'] = canonical_uuid(self.project.get('id'))
            require(self.project.get('title') == self.report['project_marker'] + ' 文稿到素材联调'
                    and not self.project.get('scenes'), 'marked_project_invalid')
            self.save('project-created.json', self.project)
        self.generate('storyboard', 'storyboard')
        svg_id, html_id = self.edit_formats()
        self.checkpoint('before-svg', self.input_document('scene', svg_id))
        svg = self.generate('svg', 'scene', scene_id=svg_id)
        self.checkpoint('after-svg', dict(version_id=svg['id'], source_sha256=svg['source_sha256']))
        html = self.generate('html', 'scene', scene_id=html_id)
        with self.step('explicit_adoption_and_initial_export'):
            rejected = self.request('GET', '/api/projects/' + self.report['project_id'] + '/export', expected=(409,))
            require(rejected.get('detail', {}).get('code') == 'unadopted_scenes', 'unadopted_export_not_rejected')
            self.adopt(svg_id, svg['id'], None)
            self.adopt(html_id, html['id'], None)
            initial = self.export('export-initial', [svg, html])
        self.checkpoint('before-revision', dict(svg_version_id=svg['id'], html_version_id=html['id']))
        original = self.request('GET', '/api/versions/' + svg['id'] + '/source', raw=True)
        revised = self.generate('revision', 'scene', scene_id=svg_id, base_version=svg['id'],
            base_source=original.decode('utf-8'), instruction='保留现有内容，把主要流程的连线和重点色改成橙色，提高文字可读性。')
        with self.step('revision_keeps_adopted_export'):
            require(self.request('GET', '/api/versions/' + svg['id'] + '/source', raw=True) == original,
                    'old_candidate_changed')
            unchanged = self.export('export-before-adoption', [svg, html])
            require(unchanged == initial, 'unadopted_revision_changed_export')
        with self.step('adopt_revision_and_export'):
            self.adopt(svg_id, revised['id'], svg['id'])
            final = self.export('export-final', [revised, html])
            require(final['scenes'][0]['version_id'] != initial['scenes'][0]['version_id']
                    and final['scenes'][1] == initial['scenes'][1], 'adoption_export_identity_invalid')
            self.refresh()

    def run(self):
        # Exclusive evidence directory refuses accidental replay before any HTTP.
        try:
            self.settings.output.mkdir(parents=True, exist_ok=False)
        except OSError:
            self.http.close()
            self.agent.close()
            raise VerificationFailure('new_evidence_directory_required') from None
        try:
            self.flush()
            self.preflight()
            if self.settings.execute:
                self.execute()
            self.report['status'] = 'passed'
        except (VerificationFailure, ServiceError) as error:
            self.report.update(status='stopped', error=error.code,
                admission_unknown=isinstance(error, VerificationFailure) and error.uncertain)
            if self.report['admission_unknown']:
                self.report['recovery_instruction'] = 'Use GET project/list and recorded intent; do not repeat POST.'
        except (KeyError, TypeError, IndexError, ValueError, UnicodeError):
            self.report.update(status='stopped', error='http_evidence_invalid')
        except KeyboardInterrupt:
            self.report.update(status='stopped', error='operator_interrupted', admission_unknown=True,
                recovery_instruction='Use GET project/list and recorded intent; do not repeat POST.')
        finally:
            self.http.close()
            self.agent.close()
            self.flush()
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workbench-url', 'origin', 'host', 'owner', 'agent-url'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--agent-token-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', default='')
    parser.add_argument('--project-id', help='Read an existing project; incompatible with --execute')
    parser.add_argument('--execute', action='store_true', help='Create a marked project; at most four generation POSTs')
    parser.add_argument('--poll-interval', type=float, default=2)
    parser.add_argument('--poll-timeout', type=float, default=660)
    parser.add_argument('--pause-for-faults', action='store_true',
                        help='Wait for bound operator release files before SVG, after SVG, and before revision')
    parser.add_argument('--checkpoint-timeout', type=float, default=900)
    args = parser.parse_args(argv)
    try:
        report = Runner(Settings(**vars(args))).run()
    except (ValueError, VerificationFailure, OSError):
        print('Verification could not start; check explicit settings and a new evidence directory.')
        return 2
    print('Verification ' + report['status'] + '; generation POSTs=' + str(report['generation_posts'])
          + '; completed=' + str(report['completed_generation_tasks']) + '; cost=unknown.')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
