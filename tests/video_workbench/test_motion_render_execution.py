"""Production owner boundary, recovery and actual PCM composition regressions."""
import base64
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench import media
from video_workbench.motion_render_adapter import RemoteMotionRenderer, UNAVAILABLE
from video_workbench.motion_render_k8s import (ANNOTATION, ExecutionJournal, KubeSceneRenderer,
    operation_key, production_resources, unpack_artifacts, validate_artifacts)
from video_workbench.motion_render_service import (MixedMotionRenderer, MotionExecutionService,
    create_app, verify_media)
from video_workbench.deploy.motion_renderer import Settings, render as deployment
from video_workbench.motion import compile_project, estimated_alignment, make_beats, example_plan
from test_motion_snapshot import RUNTIME, freeze, motion_scene, static_scene
from test_motion_review import fixture as observation_fixture

OWNER = hashlib.sha256(b'owner-one').hexdigest()
OTHER = hashlib.sha256(b'owner-two').hexdigest()
IMAGE = 'registry:5000/video-motion-renderer:latest@' + RUNTIME['image_digest']
TOKEN = 't' * 48


class FakeKube:
    namespace = 'video-motion-render'
    def __init__(self, *, lose_create=False, phase='running'):
        self.resources, self.calls = {}, []
        self.lose_create, self.phase = lose_create, phase
    def request(self, method, resource, name=None, *, body=None, params=None, logs=False):
        self.calls.append((method, resource, name))
        if resource == 'networkpolicies':
            return {'items': [{'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}}]}
        if resource == 'pods' and not logs:
            jobs = [v for (kind, _), v in self.resources.items() if kind == 'jobs']
            if not jobs:
                return {'items': []}
            job = jobs[0]
            if job['spec'].get('suspend') is True:
                return {'items': []}
            state = {'running': {}}
            if self.phase == 'oom':
                state = {'terminated': {'reason': 'OOMKilled', 'exitCode': 137}}
            return {'items': [{'metadata': {'name': job['metadata']['name'] + '-pod',
                'annotations': job['metadata']['annotations']},
                'status': {'containerStatuses': [{'name': 'renderer', 'state': state,
                    'imageID': 'containerd://' + RUNTIME['image_digest']}]}}]}
        if method == 'GET':
            return deepcopy(self.resources.get((resource, name)))
        if method == 'POST':
            value = deepcopy(body)
            value['metadata']['uid'] = 'uid-' + body['metadata']['name']
            if resource == 'jobs':
                value['status'] = {'active': 1}
            self.resources[resource, body['metadata']['name']] = value
            if resource == 'jobs' and self.lose_create:
                self.lose_create = False
                raise ServiceError(UNAVAILABLE, 503)
            return value
        if method == 'DELETE':
            assert body['preconditions']['uid'] == self.resources[resource, name]['metadata']['uid']
            self.resources.pop((resource, name), None)
            return {'status': 'Success'}
        raise AssertionError((method, resource, name))


def scene_renderer(tmp_path, kube):
    journal = ExecutionJournal(tmp_path / 'journal.db')
    return KubeSceneRenderer(journal, kube, image=IMAGE, release_id='r-test', runtime=RUNTIME,
                             root=tmp_path / 'runs', seconds=300, poll_seconds=.001)


def render_scene(renderer, *, cancel_event=None):
    return renderer.render(freeze()['scenes'][0], key=operation_key(OWNER, 'operation-one'),
                           scene_index=0, quality='draft', cancel_event=cancel_event)


def test_uncertain_create_queries_exact_job_and_cancel_confirms_cleanup(tmp_path):
    kube = FakeKube(lose_create=True)
    renderer = scene_renderer(tmp_path, kube)
    event = threading.Event()
    original = kube.request
    def request(method, resource, *args, **kwargs):
        result = original(method, resource, *args, **kwargs)
        if method == 'GET' and resource == 'pods' and result['items']:
            event.set()
        return result
    kube.request = request
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        render_scene(renderer, cancel_event=event)
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1
    assert not kube.resources
    row = renderer.journal.scene(operation_key(OWNER, 'operation-one') + ':0')
    assert row['state'] == 'cancelled'


def test_restart_running_operation_resumes_original_job_without_resubmit(tmp_path):
    kube = FakeKube(phase='oom')
    first = scene_renderer(tmp_path, kube)
    # Persist intent and actual Job, then simulate a controller crash before poll.
    frozen = freeze()
    scene, key = frozen['scenes'][0], operation_key(OWNER, 'operation-one')
    motion = scene['motion']
    binding = hashlib.sha256(json.dumps(dict(operation=key, scene=0,
        input=motion['project_files_sha256'], image=IMAGE, quality='draft'), sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    name = 'vm-' + hashlib.sha256((key + ':0').encode()).hexdigest()[:36]
    directory = tmp_path / 'frozen'
    directory.mkdir()
    files = compile_project(motion['plan'], motion['beats'], motion['alignment'], runtime_fingerprint=RUNTIME)
    for filename, raw in files.items():
        (directory / filename).write_bytes(raw)
    (directory / 'audio.wav').write_bytes(base64.b64decode(scene['audio']['data_base64']))
    configs, job = production_resources(directory, image=IMAGE, release_id='r-test',
        namespace=kube.namespace, name=name, binding=binding)
    first.journal.scene_intent(key + ':0', binding, name)
    first.journal.scene_update(key + ':0', 'running')
    for value in configs:
        kube.request('POST', 'configmaps', body=value)
    kube.request('POST', 'jobs', body=job)
    second = scene_renderer(tmp_path, kube)
    with pytest.raises(ServiceError, match='motion_execution_oom'):
        render_scene(second)
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1
    assert not kube.resources


def test_uncertain_missing_job_is_not_blindly_resubmitted(tmp_path):
    kube = FakeKube()
    renderer = scene_renderer(tmp_path, kube)
    original = kube.request
    def request(method, resource, *args, **kwargs):
        if method == 'POST' and resource == 'jobs' and not kwargs.get('body', {}).get('spec', {}).get('suspend'):
            kube.calls.append((method, resource, None))
            raise ServiceError(UNAVAILABLE, 503)
        return original(method, resource, *args, **kwargs)
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    with pytest.raises(ServiceError, match='motion_submission_unconfirmed'):
        render_scene(renderer)
    # The second POST is exclusively a suspended cancellation fence. It cannot
    # create a Pod, and prevents a delayed original POST from creating one.
    jobs = [v for (kind, _), v in kube.resources.items() if kind == 'jobs']
    assert len(jobs) == 1 and jobs[0]['spec']['suspend'] is True
    assert not any(kind == 'configmaps' for kind, _ in kube.resources)
    assert renderer.journal.scene(operation_key(OWNER, 'operation-one') + ':0')['state'] == 'failed'


def test_cancel_after_uncertain_absent_submission_fences_late_create(tmp_path):
    kube = FakeKube()
    renderer = scene_renderer(tmp_path, kube)
    original = kube.request
    def request(method, resource, *args, **kwargs):
        if method == 'POST' and resource == 'jobs' and not kwargs.get('body', {}).get('spec', {}).get('suspend'):
            raise ServiceError(UNAVAILABLE, 503)
        return original(method, resource, *args, **kwargs)
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    event = threading.Event()
    event.set()
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        render_scene(renderer, cancel_event=event)
    row = renderer.journal.scene(operation_key(OWNER, 'operation-one') + ':0')
    assert row['state'] == 'cancelled' and json.loads(row['result'])['submission_fenced']
    fence = next(v for (kind, _), v in kube.resources.items() if kind == 'jobs')
    assert fence['spec']['suspend'] is True
    assert original('GET', 'pods')['items'] == []
    assert not any(kind == 'configmaps' for kind, _ in kube.resources)


def test_confirmed_job_disappearing_on_restart_fails_without_recreation(tmp_path):
    kube = FakeKube()
    renderer = scene_renderer(tmp_path, kube)
    original = kube.request
    def request(method, resource, *args, **kwargs):
        result = original(method, resource, *args, **kwargs)
        if resource == 'pods' and method == 'GET' and result['items']:
            raise ServiceError(UNAVAILABLE, 503)
        return result
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    for key in [key for key in kube.resources if key[0] == 'jobs']:
        kube.resources.pop(key)
    kube.request = original
    with pytest.raises(ServiceError, match='motion_execution_disappeared'):
        render_scene(scene_renderer(tmp_path, kube))
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1
    assert not kube.resources


def test_large_frozen_audio_is_split_into_bounded_immutable_configmaps(tmp_path):
    data = os.urandom(2 * 1024 * 1024)
    beats = make_beats('先提交请求。然后处理请求。最后得到结果。')
    alignment = estimated_alignment(beats, hashlib.sha256(data).hexdigest(), 15)
    files = compile_project(example_plan(beats), beats, alignment, runtime_fingerprint=RUNTIME)
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(data)
    configs, job = production_resources(tmp_path, image=IMAGE, release_id='r-test', namespace='video-motion-render',
        name='vm-test', binding='b' * 64, quality='final')
    assert len(configs) > 1
    assert all(cm['immutable'] is True and len(json.dumps(cm)) < 900 * 1024 for cm in configs)
    env = {row['name']: row['value'] for row in job['spec']['template']['spec']['containers'][0]['env']}
    assert env['MOTION_QUALITY'] == 'final'
    assert job['spec']['template']['spec']['automountServiceAccountToken'] is False


def test_open_network_policy_rejects_before_job_submission(tmp_path):
    kube = FakeKube()
    original = kube.request
    def request(method, resource, *args, **kwargs):
        if resource == 'networkpolicies':
            return {'items': [{'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'],
                                        'ingress': [], 'egress': [{}]}}]}
        return original(method, resource, *args, **kwargs)
    kube.request = request
    with pytest.raises(ServiceError, match='motion_namespace_not_isolated'):
        render_scene(scene_renderer(tmp_path, kube))
    assert not any(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls)


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as target:
        for name, content in entries:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            target.addfile(member, io.BytesIO(content))
    return b'VIDEO_MOTION_ARTIFACT_BASE64=' + base64.b64encode(stream.getvalue())


@pytest.mark.parametrize('entries', [[('../output.mp4', b'bad')], [('output.mp4', b'a'), ('output.mp4', b'b')],
                                    [('observations/../../escape.jpg', b'bad')]])
def test_artifact_archive_cannot_escape_or_shadow(entries):
    with pytest.raises(ServiceError, match='motion_artifacts_invalid'):
        unpack_artifacts(archive(entries))


def test_over_budget_diagnostics_do_not_become_a_success():
    entries = {'experiment.json': b'{"status":"artifact_budget_exceeded"}'}
    with pytest.raises(ServiceError, match='motion_artifact_budget_exceeded'):
        validate_artifacts(entries, {}, RUNTIME, 'draft')


def test_archive_decompression_is_bounded_before_extension_headers(monkeypatch):
    import video_workbench.motion_render_k8s as module
    monkeypatch.setattr(module, 'EXTRACT_BYTES', 1024)
    payload = archive([('output.mp4', b'\0' * (2 * 1024 * 1024))])
    with pytest.raises(ServiceError, match='motion_artifacts_too_large'):
        unpack_artifacts(payload)


@pytest.mark.parametrize('entries,code', [
    ([('experiment.json', b'{"status":"artifact_budget_exceeded"}')], 'motion_artifact_budget_exceeded'),
    ([('experiment.json', b'{"status":"failed"}'), ('render-receipt.json', b'{"status":"timed_out"}')],
     'motion_execution_timed_out')])
def test_failed_pod_diagnostics_preserve_budget_or_timeout_reason(tmp_path, entries, code):
    kube = FakeKube()
    kube.request = lambda *args, **kwargs: archive(entries)
    failure = scene_renderer(tmp_path, kube)._failure({'metadata': {'name': 'owned-pod'}})
    assert failure.code == code


def valid_artifacts():
    from agent_service.contracts import canonical_bytes
    plan, timeline, observations = observation_fixture()
    files = {'manifest.json': b'{}', 'plan.json': canonical_bytes(plan),
             'timeline.json': canonical_bytes(timeline), 'beats.json': b'{}', 'alignment.json': b'{}'}
    entries = dict(files)
    entries['output.mp4'] = b'verified media fixture'
    entries['experiment.json'] = b'{"status":"pending_validation","stages":{"render":0,"observe":0}}'
    for row in observations['windows']:
        for label in ('before', 'middle', 'after'):
            data = b'capture-' + label.encode()
            entries[f"observations/a{row['ordinal']}-{label}.jpg"] = data
            row[label]['frame_sha256'] = hashlib.sha256(data).hexdigest()
    for name, label, field in (('seek-first', 'before', 'first_sha256'), ('seek-last', 'after', 'last_sha256'),
                               ('seek-repeat', 'before', 'repeated_first_sha256')):
        data = b'capture-' + label.encode()
        entries[f'observations/{name}.jpg'] = data
        observations['reverse_seek'][field] = hashlib.sha256(data).hexdigest()
    entries['observations.json'] = canonical_bytes(observations)
    for name, status in (('render-receipt.json', 'encoded_pending_validation'),
                         ('observe-receipt.json', 'observed_pending_validation')):
        receipt = dict(schema='video.motion-render-receipt.v1', status=status,
            input_manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest(),
            timeline_sha256=hashlib.sha256(files['timeline.json']).hexdigest(),
            runtime={k: v for k, v in RUNTIME.items() if k != 'image_digest'},
            runtime_binding=dict(status='matched', image_digest=RUNTIME['image_digest']),
            quality='final', cli_quality='delivery', decode_verified=True,
            output_sha256=hashlib.sha256(entries['output.mp4']).hexdigest())
        entries[name] = canonical_bytes(receipt)
    return entries, files


def test_exact_runtime_quality_and_actual_frame_evidence_required_for_success():
    entries, files = valid_artifacts()
    result = validate_artifacts(entries, files, RUNTIME, 'final')
    assert result['technical_review']['technical_status'] == 'passed'
    assert result['technical_review']['voice_naturalness'] == 'pending_user_review'
    with pytest.raises(ServiceError, match='motion_runtime_receipt_mismatch'):
        validate_artifacts(entries, files, RUNTIME, 'draft')
    entries['observations/a0-middle.jpg'] = b'changed capture'
    with pytest.raises(ServiceError, match='motion_observation_frame_mismatch'):
        validate_artifacts(entries, files, RUNTIME, 'final')


def test_failed_semantic_review_never_returns_media_success():
    entries, files = valid_artifacts()
    observation = json.loads(entries['observations.json'])
    observation['windows'][0]['middle']['elements']['request']['x'] = 162
    entries['observations.json'] = json.dumps(observation).encode()
    with pytest.raises(ServiceError, match='motion_expected_move_missing'):
        validate_artifacts(entries, files, RUNTIME, 'final')


def make_service(tmp_path, renderer=None):
    scene = SimpleNamespace(runtime=RUNTIME, check_policy=lambda: None)
    renderer = renderer or SimpleNamespace(scene_renderer=scene)
    return MotionExecutionService(ExecutionJournal(tmp_path / 'journal.db'), renderer,
        root=tmp_path / 'runs', token=TOKEN, runtime=RUNTIME, image=IMAGE, release_id='r-test')


def headers(owner=OWNER):
    return {'Authorization': 'Bearer ' + TOKEN, 'X-Video-Owner': owner}


def test_api_auth_owner_replay_conflict_and_no_arbitrary_output_path(tmp_path):
    service = make_service(tmp_path)
    with TestClient(create_app(service, start_worker=False)) as client:
        snapshot = freeze()
        path = '/v1/renders/my-operation'
        assert client.put(path, json={'snapshot': snapshot}).status_code == 401
        result = client.put(path, json={'snapshot': snapshot}, headers=headers())
        assert result.status_code == 200 and result.json()['state'] == 'queued'
        assert 'owner' not in result.json() and 'path' not in result.json()
        assert client.get(path, headers=headers(OTHER)).status_code == 404
        assert client.post(path + '/cancel', headers=headers(OTHER)).status_code == 404
        assert client.get(path + '/video', headers=headers(OTHER)).status_code == 404
        assert client.put(path, json={'snapshot': snapshot}, headers=headers()).json() == result.json()
        changed = freeze(quality='final')
        assert client.put(path, json={'snapshot': changed}, headers=headers()).status_code == 409
        assert client.put(path, json={'snapshot': snapshot, 'output': '/etc/passwd'}, headers=headers()).status_code == 422
        capability = client.get('/v1/capabilities', headers=headers()).json()
        assert capability['runtime_fingerprint'] == RUNTIME and TOKEN not in json.dumps(capability)


def test_service_restart_transient_failure_keeps_same_operation_id(tmp_path):
    seen = []
    def render(snapshot, root, **kwargs):
        seen.append(kwargs['key'])
        raise ServiceError(UNAVAILABLE, 503)
    renderer = SimpleNamespace(render=render)
    first = make_service(tmp_path, renderer)
    first.submit(OWNER, 'stable-operation', freeze())
    first.tick()
    second = make_service(tmp_path, renderer)
    second.tick()
    row = second.journal.operation(OWNER, 'stable-operation')
    assert row['state'] == 'queued' and seen == [row['key'], row['key']]


def test_cancel_before_composition_drains_existing_scene_handle(tmp_path):
    calls = []
    scene = SimpleNamespace(runtime=RUNTIME, cancel_operation=lambda *args: calls.append(args))
    event = threading.Event()
    event.set()
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        MixedMotionRenderer(scene).render(freeze(), tmp_path, key='key', cancel_event=event)
    assert len(calls) == 1


def test_remote_client_uncertain_submit_is_recoverable_not_a_second_attempt(tmp_path):
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == 'GET':
            return httpx.Response(404)
        raise httpx.ReadTimeout('uncertain submission')
    client = httpx.Client(transport=httpx.MockTransport(handle))
    renderer = RemoteMotionRenderer('http://internal', TOKEN, client=client, poll_seconds=.001)
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        renderer.render(freeze(), tmp_path / 'out', operation_id='operation', owner='owner-one')
    assert [method for method, _ in calls] == ['GET', 'PUT']


def test_remote_client_cancellation_drains_before_terminal_response(tmp_path):
    snapshot, event, calls = freeze(), threading.Event(), []
    event.set()
    def handle(request):
        calls.append(request.method)
        state = 'running' if len(calls) == 1 else 'cancelled'
        return httpx.Response(200, json=dict(operation_id='operation', snapshot_sha256=snapshot['sha256'], state=state))
    renderer = RemoteMotionRenderer('http://internal', TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(handle)), poll_seconds=.001)
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        renderer.render(snapshot, tmp_path / 'out', operation_id='operation', owner='owner-one', cancel_event=event)
    assert calls == ['GET', 'POST']


def test_remote_cancel_racy_404_binds_same_id_then_drains(tmp_path):
    snapshot, event, calls = freeze(), threading.Event(), []
    event.set()
    def handle(request):
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(404)
        state = 'queued' if request.method == 'PUT' else 'cancelled'
        return httpx.Response(200, json=dict(operation_id='operation', snapshot_sha256=snapshot['sha256'], state=state))
    renderer = RemoteMotionRenderer('http://internal', TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(handle)), poll_seconds=.001)
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        renderer.render(snapshot, tmp_path / 'out', operation_id='operation', owner='owner-one', cancel_event=event)
    assert calls == ['GET', 'PUT', 'POST']


def test_deployment_keeps_k8s_credentials_out_of_render_jobs_and_business():
    output = deployment(Settings(image=IMAGE, renderer_image=IMAGE, release_id='r-test', api_host='10.96.0.1'))
    role = next(v for v in output['items'] if v['kind'] == 'Role')
    assert not any('secrets' in rule['resources'] or '*' in rule['verbs'] for rule in role['rules'])
    deploy = next(v for v in output['items'] if v['kind'] == 'Deployment')
    assert deploy['spec']['strategy']['type'] == 'Recreate'
    spec = deploy['spec']['template']['spec']
    assert spec['automountServiceAccountToken'] is False
    assert {v['name'] for v in spec['volumes']} == {'data', 'tmp', 'token', 'runtime', 'k8s'}
    policy = next(v for v in output['items'] if v['kind'] == 'NetworkPolicy' and v['metadata']['name'] != 'deny-all')
    assert policy['spec']['egress'][0]['to'] == [{'ipBlock': {'cidr': '10.96.0.1/32'}}]
    with pytest.raises(ValueError):
        deployment(Settings(image=IMAGE, renderer_image=IMAGE, release_id='r-test', api_host='0.0.0.0/0'))


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg runtime unavailable')
def test_real_ffmpeg_mixed_output_uses_original_pcm_one_aac_track(tmp_path):
    clips = tmp_path / 'clips'
    clips.mkdir()
    ffmpeg = shutil.which('ffmpeg')
    def clip(destination, seconds, color):
        destination.parent.mkdir(parents=True, exist_ok=True)
        media._run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
            f'color=c={color}:s=1280x720:r=24:d={seconds}', '-f', 'lavfi', '-i',
            f'anullsrc=r=24000:cl=mono:d={seconds}', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', str(seconds), str(destination)], cwd=clips, timeout=30)
    class Scene:
        runtime = RUNTIME
        def render(self, scene, **kwargs):
            path = clips / 'motion.mp4'
            clip(path, scene['timing']['duration_seconds'], 'blue')
            return dict(path=str(path), technical_review={'technical_status': 'passed'})
    def static(snapshot, directory, **kwargs):
        path = directory / 'static.mp4'
        clip(path, snapshot['duration_seconds'], 'red')
        return {'path': str(path)}
    snapshot = freeze([static_scene(), motion_scene()])
    result = MixedMotionRenderer(Scene(), static_renderer=static).render(snapshot, tmp_path / 'result', key='local-test')
    assert result['fps'] == 24 and result['duration_seconds'] == 3
    assert result['visual_mode'] == 'mixed' and result['snapshot_sha256'] == snapshot['sha256']
    assert Path(result['path']).is_file()
    assert not (tmp_path / 'result' / 'assets').exists()
    # Inputs' per-scene video audio above was silence; nonzero original PCM must
    # survive in the final AAC. This catches concatenation of per-scene AAC.
    decoded = tmp_path / 'decoded.wav'
    media._run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-i', result['path'], '-map', '0:a:0',
                '-c:a', 'pcm_s16le', str(decoded)], cwd=tmp_path, timeout=30)
    import wave
    with wave.open(str(decoded), 'rb') as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 24000
        assert any(wav.readframes(wav.getnframes()))
