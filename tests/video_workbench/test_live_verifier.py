"""Offline HTTP peers only: never Docker, models, or a live service."""
import copy
import hashlib
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.contracts import canonical_bytes, digest
from video_workbench.deploy.verify_live import Runner, Settings, VerificationFailure, main


TOKEN = 'synthetic-live-verifier-token-0000000000000000'
CSRF = 'c' * 64
FIELDS = ('title', 'narration', 'visual_description', 'duration_seconds', 'format')
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="20" y="50">文稿到镜头</text></svg>'
HTML = '<!doctype html><html><head><title>审阅</title></head><body><h1>审阅与导出</h1></body></html>'
REVISED = SVG.replace('文稿到镜头', '已修订的流程')


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class Peer:
    """Independent in-memory wire implementation, not the application/store."""

    def __init__(self):
        self.calls, self.agent_calls, self.inputs, self.paid = [], [], [], []
        self.project = None
        self.sources, self.jobs, self.results, self.manifests, self.artifacts = {}, {}, {}, {}, {}
        self.serial = 100
        self.attack = None
        self.lost_post = False
        self.pending = False
        self.one_scene = False

    def uid(self):
        self.serial += 1
        return str(UUID(int=self.serial))

    def response(self, value, status=200):
        return httpx.Response(status, json=value)

    def scene(self, scene_id):
        return next(s for s in self.project['scenes'] if s['id'] == scene_id)

    def add_scene(self, fields):
        return dict(fields, id=self.uid(), versions=[], head_version_id=None, operation=None)

    def operation(self, path, body):
        self.paid.append((path, copy.deepcopy(body)))
        is_storyboard = path.endswith('/storyboard')
        scene = None if is_storyboard else self.scene(path.split('/')[3])
        p = self.project
        assert body['expected_revision'] == p['revision']
        if is_storyboard:
            assert body['max_scenes'] == 2
            document = dict(schema_version='1', kind='storyboard', title=p['title'], script=p['script'],
                            style=p['style'], max_scenes=2)
            rows = [dict(title='文稿到镜头', narration='先拆镜，再做图。', visual_description='两个方框和箭头。',
                         duration_seconds=8, format='svg'),
                    dict(title='审阅与导出', narration='采用候选后导出。', visual_description='候选与素材文件夹。',
                         duration_seconds=9, format='html')]
            if self.one_scene:
                rows = rows[:1]
            output = dict(schema_version='1', scenes=rows)
        else:
            base = self.sources.get(body.get('base_version_id'))
            document = dict(schema_version='1', kind='scene', title=scene['title'], narration=scene['narration'],
                visual_description=scene['visual_description'], style=p['style'], format=scene['format'],
                width=1280, height=720, duration_seconds=scene['duration_seconds'],
                base_source=base, instruction=body['instruction'])
            source = REVISED if base else SVG if scene['format'] == 'svg' else HTML
            output = dict(schema_version='1', format=scene['format'], source=source, summary='离线测试候选')
        self.inputs.append(document)
        op_id, job_id, attempt_id, artifact_id = (self.uid() for _ in range(4))
        op = dict(id=op_id, kind='storyboard' if is_storyboard else 'revise' if body.get('base_version_id') else 'scene',
                  status='running' if self.pending else 'completed', error=None, job_id=job_id)
        result = dict(schema_version='1', kind='storyboard' if is_storyboard else 'scene',
                      input_sha256=digest(document), output=output)
        if self.attack == 'input_hash':
            result['input_sha256'] = '0' * 64
        raw = canonical_bytes(result)
        manifest = frozen_manifest(job_id, attempt_id, [dict(artifact_id=artifact_id, name='video-result.json',
            size=len(raw), sha256=hashlib.sha256(raw).hexdigest())])
        job = dict(job_id=job_id, state='completed', business_ref='video:' + op_id, publication_authorized=False,
            artifact_manifest_sha256=manifest['sha256'], pins={'profile': 'video.' + result['kind'] + '.v1', 'profile_revision': '1'},
            attempt=dict(attempt_id=attempt_id, fence=1), stop_proven=True,
            stop_proof=dict(stop_proven=True, attempt_id=attempt_id, fence=1,
                request_sha256='a' * 64, result_sha256='b' * 64, resource_ids=['offline-only'], observed_at=1000.0),
            future_private_field={'credential': TOKEN})
        if self.attack == 'stop':
            job['stop_proof']['fence'] = 2
        if self.attack == 'manifest':
            job['artifact_manifest_sha256'] = '0' * 64
        if self.attack == 'business_ref':
            job['business_ref'] = 'video:' + self.uid()
        if self.attack == 'artifact_digest':
            raw += b' '
        self.jobs[job_id], self.results[job_id], self.manifests[job_id], self.artifacts[artifact_id] = job, result, manifest, raw
        if is_storyboard:
            p['storyboard_operation'] = op
            if not self.pending:
                p['scenes'] = [self.add_scene(s) for s in rows]
                if self.attack == 'storyboard_mismatch':
                    p['scenes'][0]['title'] = '无关镜头'
        else:
            scene['operation'] = op
            if not self.pending:
                version_id = self.uid()
                self.sources[version_id] = output['source']
                scene['versions'].append(dict(id=version_id, number=len(scene['versions']) + 1,
                    summary=output['summary'], format=output['format'], created_at=1000.0,
                    parent_version_id=body.get('base_version_id'),
                    source_sha256=hashlib.sha256(output['source'].encode()).hexdigest()))
                if self.attack == 'implicit_adoption':
                    scene['head_version_id'] = version_id
                if self.attack == 'source':
                    self.sources[version_id] += ' '
                if self.attack == 'secret_source':
                    self.sources[version_id] = TOKEN
        p['revision'] += 1
        return op

    def export(self):
        if any(s['head_version_id'] is None for s in self.project['scenes']):
            return self.response({'detail': {'code': 'unadopted_scenes'}}, 409)
        manifest = dict(schema_version='1', title=self.project['title'], style=self.project['style'], scenes=[])
        files = {'index.html': '<html>离线顺序预览</html>', 'README.txt': '素材包'}
        for i, scene in enumerate(self.project['scenes'], 1):
            version = next(v for v in scene['versions'] if v['id'] == scene['head_version_id'])
            name, preview = f'scenes/{i:03d}.{version["format"]}', f'preview/{i:03d}.html'
            files[name] = self.sources[version['id']]
            files[preview] = '<html>预览</html>'
            manifest['scenes'].append(dict(index=i, title=scene['title'], narration=scene['narration'],
                duration_seconds=scene['duration_seconds'], format=version['format'], source=name, preview=preview,
                version_id=version['id'], source_sha256=version['source_sha256']))
        if self.attack == 'export_version':
            manifest['scenes'][0]['version_id'] = self.uid()
        if self.attack == 'export_bytes':
            files[manifest['scenes'][0]['source']] += 'changed'
        if self.attack == 'export_secret':
            files['README.txt'] += CSRF
        if self.attack == 'export_path':
            files['../untrusted'] = 'escape'
        files['manifest.json'] = json.dumps(manifest, ensure_ascii=False)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return httpx.Response(200, content=buffer.getvalue())

    def workbench(self, request):
        self.calls.append(request)
        assert request.headers['host'] == 'video.example.invalid'
        assert request.headers['origin'] == 'https://video.example.invalid'
        assert request.headers['x-forwarded-user'] == 'operator@example.invalid'
        path, method = request.url.path, request.method
        if method != 'GET':
            assert request.headers['x-csrf-token'] == CSRF
        if path == '/api/session':
            return self.response(dict(user_id='operator@example.invalid', csrf_token=CSRF, agent_configured=True))
        if path == '/api/projects' and method == 'POST':
            self.project = dict(json.loads(request.content), id=self.uid(), revision=1,
                                updated_at=1000.0, scenes=[], storyboard_operation=None)
            return self.response(self.project)
        if method == 'GET' and path == '/api/projects/' + (self.project or {}).get('id', ''):
            return self.response(self.project)
        if method == 'POST' and path.endswith(('/storyboard', '/generate', '/revise')):
            op = self.operation(path, json.loads(request.content))
            assert request.headers['idempotency-key'].startswith('live-')
            if self.lost_post:
                raise httpx.ReadTimeout('DO NOT PRINT ' + TOKEN + CSRF, request=request)
            return self.response(op, 202)
        if method == 'POST' and path.endswith('/scenes'):
            self.project['scenes'].append(self.add_scene(json.loads(request.content)))
            self.project['revision'] += 1
            return self.response(self.project)
        if method == 'PATCH':
            body = json.loads(request.content)
            assert body.pop('expected_revision') == self.project['revision']
            # Model coercion changes ints to floats; digest must use saved types.
            body['duration_seconds'] = float(body['duration_seconds'])
            self.scene(path.split('/')[3]).update(body)
            self.project['revision'] += 1
            return self.response(self.project)
        if method == 'GET' and path.endswith('/source'):
            return httpx.Response(200, content=self.sources[path.split('/')[3]].encode())
        if method == 'POST' and path.endswith('/adopt'):
            body, scene = json.loads(request.content), self.scene(path.split('/')[3])
            assert scene['head_version_id'] == body['expected_head_version_id']
            scene['head_version_id'] = body['version_id']
            self.project['revision'] += 1
            return self.response(self.project)
        if method == 'GET' and path.endswith('/export'):
            return self.export()
        raise AssertionError((method, path))

    def agent(self, request):
        self.agent_calls.append(request)
        assert request.method == 'GET', 'Verifier must never mutate Agent directly'
        assert request.headers['authorization'] == 'Bearer ' + TOKEN
        path = request.url.path
        if path == '/v1/capabilities':
            return self.response(dict(api_version='1', profiles=[dict(profile='video.' + kind + '.v1',
                profile_revision='1', configured=True, ready=True, supports_inputs=True, max_timeout_seconds=600)
                for kind in ('storyboard', 'scene')]))
        if path.endswith('/artifact-manifest'):
            return self.response(self.manifests[path.split('/')[3]])
        if path.startswith('/v1/jobs/'):
            return self.response(self.jobs[path.split('/')[3]])
        if path.startswith('/v1/artifacts/'):
            return httpx.Response(200, content=self.artifacts[path.split('/')[3]])
        raise AssertionError(path)


@pytest.fixture
def setup(tmp_path):
    token = tmp_path / 'agent-token'
    token.write_text(TOKEN + '\n', encoding='ascii')
    settings = Settings(workbench_url='http://127.0.0.1:9081', origin='https://video.example.invalid',
        host='video.example.invalid', owner='operator@example.invalid', agent_url='http://agent.invalid',
        agent_token_file=token, output=tmp_path / 'evidence', run_id='offline-run-0001', poll_interval=.5, poll_timeout=2)
    return settings, Peer(), Clock()


def run(setup, *, execute=True):
    settings, peer, clock = setup
    runner = Runner(replace(settings, execute=execute), workbench_transport=httpx.MockTransport(peer.workbench),
                    agent_transport=httpx.MockTransport(peer.agent), monotonic=clock, sleep=clock.sleep)
    return runner.run()


def assert_no_secrets(path):
    for file in path.iterdir():
        assert TOKEN.encode() not in file.read_bytes()
        assert CSRF.encode() not in file.read_bytes()


def test_default_is_read_only_and_leaves_explicit_limits(setup):
    settings, peer, clock = setup
    report = run(setup, execute=False)
    assert report['status'] == 'passed'
    assert report['mode'] == 'read_only'
    assert report['generation_posts'] == report['completed_generation_tasks'] == 0
    assert all(r.method == 'GET' for r in peer.calls + peer.agent_calls)
    assert peer.project is None
    assert report['cost'] == 'unknown'
    for key in ('visual_quality', 'ingress_authentication', 'running_cancellation', 'submission_loss_recovery'):
        assert report[key] == 'not_run'
    assert_no_secrets(settings.output)


@pytest.mark.parametrize('one_scene', [False, True])
def test_four_tasks_bind_real_saved_types_and_explicit_adoption_exports(setup, one_scene):
    settings, peer, clock = setup
    peer.one_scene = one_scene
    report = run(setup)
    assert report['status'] == 'passed', report
    assert report['generation_posts'] == report['completed_generation_tasks'] == len(peer.paid) == 4
    assert report['manual_second_scene_added'] is one_scene
    assert [s['format'] for s in peer.project['scenes']] == ['svg', 'html']
    assert peer.project['title'].startswith('[LIVE VERIFY offline-run-0001]')
    assert peer.inputs[0]['max_scenes'] == 2
    assert type(peer.inputs[1]['duration_seconds']) is float
    assert peer.inputs[3]['base_source'] == SVG
    assert all(i['state'] == 'validated_completed' for i in report['intents'])
    for label, document in zip(('storyboard', 'svg', 'html', 'revision'), peer.inputs):
        saved = json.loads((settings.output / (label + '-input.json')).read_bytes())
        assert canonical_bytes(saved) == canonical_bytes(document)
        assert (settings.output / (label + '-manifest.json')).is_file()
        job = json.loads((settings.output / (label + '-job.json')).read_bytes())
        assert 'future_private_field' not in job
    initial = json.loads((settings.output / 'export-initial-manifest.json').read_bytes())
    before = json.loads((settings.output / 'export-before-adoption-manifest.json').read_bytes())
    final = json.loads((settings.output / 'export-final-manifest.json').read_bytes())
    assert initial == before
    assert initial['scenes'][0]['version_id'] != final['scenes'][0]['version_id']
    assert initial['scenes'][1] == final['scenes'][1]
    assert len(list(settings.output.glob('*.zip'))) == 3
    assert all('elapsed_seconds' in step for step in report['steps'])
    assert not any(r.url.path.endswith('/cancel') for r in peer.calls + peer.agent_calls)
    assert_no_secrets(settings.output)


@pytest.mark.parametrize('attack,expected_count', [
    ('input_hash', 1), ('stop', 1), ('manifest', 1), ('business_ref', 1), ('artifact_digest', 1),
    ('storyboard_mismatch', 1), ('source', 2), ('implicit_adoption', 2), ('secret_source', 2),
    ('export_version', 3), ('export_bytes', 3), ('export_secret', 3), ('export_path', 3),
])
def test_untrusted_proof_or_saved_output_stops_remaining_paid_steps(setup, attack, expected_count):
    settings, peer, clock = setup
    peer.attack = attack
    report = run(setup)
    assert report['status'] == 'stopped'
    assert report['generation_posts'] == len(peer.paid) == expected_count
    assert report['completed_generation_tasks'] < 4
    assert_no_secrets(settings.output)


def test_lost_paid_post_response_is_never_repeated_and_preserves_query_intent(setup):
    settings, peer, clock = setup
    peer.lost_post = True
    report = run(setup)
    assert report['status'] == 'stopped' and report['admission_unknown'] is True
    assert report['generation_posts'] == len(peer.paid) == 1
    assert report['completed_generation_tasks'] == 0
    intent = report['intents'][0]
    assert intent['state'] == 'submission_started'
    assert intent['recovery_get'] == '/api/projects/' + peer.project['id']
    assert len(intent['request_sha256']) == 64
    assert report['submission_loss_recovery'] == 'not_run'
    assert_no_secrets(settings.output)


def test_pending_polling_is_bounded_and_keeps_known_operation_and_job(setup):
    settings, peer, clock = setup
    peer.pending = True
    report = run(setup)
    assert report['error'] == 'operation_poll_timeout'
    assert report['generation_posts'] == len(peer.paid) == 1
    assert len(clock.waits) == 4 and sum(clock.waits) == 2
    assert report['intents'][0]['operation_id']
    assert report['intents'][0]['job_id']
    assert report['running_cancellation'] == 'not_run'


def test_existing_output_refuses_execution_before_any_http(setup):
    settings, peer, clock = setup
    settings.output.mkdir()
    with pytest.raises(VerificationFailure, match='new_evidence_directory_required'):
        run(setup)
    assert peer.calls == peer.agent_calls == []


@pytest.mark.parametrize('change', [
    {'origin': 'https://different.invalid'}, {'workbench_url': 'https://user:pass@example.invalid'},
    {'agent_url': 'https://agent.invalid/untrusted'}, {'owner': 'name\nheader'},
    {'poll_timeout': 901}, {'poll_interval': 0}, {'run_id': '../unsafe'},
    {'agent_token_file': Path('relative')}, {'output': Path('relative')},
    {'execute': True, 'project_id': str(UUID(int=1))},
])
def test_explicit_configuration_rejects_unsafe_or_unbounded_settings(setup, change):
    settings, peer, clock = setup
    with pytest.raises((ValueError, VerificationFailure)):
        replace(settings, **change)


def test_cli_default_does_not_enable_execute_and_stdout_never_leaks_credentials(setup, monkeypatch, capsys):
    settings, peer, clock = setup
    real_runner = Runner
    def factory(config):
        return real_runner(config, workbench_transport=httpx.MockTransport(peer.workbench),
                           agent_transport=httpx.MockTransport(peer.agent), monotonic=clock, sleep=clock.sleep)
    monkeypatch.setattr('video_workbench.deploy.verify_live.Runner', factory)
    args = ['--workbench-url', settings.workbench_url, '--origin', settings.origin, '--host', settings.host,
            '--owner', settings.owner, '--agent-url', settings.agent_url,
            '--agent-token-file', str(settings.agent_token_file), '--output', str(settings.output)]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert 'generation POSTs=0' in output and 'cost=unknown' in output
    assert TOKEN not in output and CSRF not in output
    assert all(r.method == 'GET' for r in peer.calls + peer.agent_calls)
