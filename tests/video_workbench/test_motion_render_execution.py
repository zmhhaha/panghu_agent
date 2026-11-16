"""Production owner boundary, recovery and actual PCM composition regressions."""
import base64
from copy import deepcopy
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
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
    create_app, public_operation, verify_media)
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
            elif self.phase == 'succeeded':
                state = {'terminated': {'exitCode': 0}}
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
                value['status'] = ({'succeeded': 1} if self.phase == 'succeeded' else
                    {'conditions': [{'type': 'Failed', 'status': 'True', 'reason': 'DeadlineExceeded'}]}
                    if self.phase == 'timed_out' else {'active': 1})
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


def successful_kube(monkeypatch):
    """Synthetic bounded artifacts; no render, Agent or speech API is called."""
    import video_workbench.motion_render_k8s as module
    kube = FakeKube(phase='succeeded')
    original = kube.request
    def request(method, resource, *args, **kwargs):
        if kwargs.get('logs'):
            kube.calls.append((method, resource, args[0] if args else None))
            return b'synthetic-artifacts'
        return original(method, resource, *args, **kwargs)
    kube.request = request
    monkeypatch.setattr(module, 'unpack_artifacts', lambda logs: {'output.mp4': b'mp4'})
    monkeypatch.setattr(module, 'validate_artifacts', lambda *args: {'media_sha256': module.sha(b'mp4')})
    return kube


@pytest.mark.parametrize('filename', ['output.pending', 'evidence.json'])
def test_disk_full_output_or_evidence_cleans_before_terminal_state(tmp_path, monkeypatch, filename):
    kube = successful_kube(monkeypatch)
    renderer = scene_renderer(tmp_path, kube)
    scene_key = operation_key(OWNER, 'operation-one') + ':0'
    original_write, original_request = Path.write_bytes, kube.request
    def write(path, data):
        if path.name == filename:
            original_write(path, data[:1])
            raise OSError(errno.ENOSPC, 'synthetic disk full')
        return original_write(path, data)
    def request(method, resource, *args, **kwargs):
        if method == 'DELETE':
            assert renderer.journal.scene(scene_key)['state'] == 'cleanup_pending'
        return original_request(method, resource, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_bytes', write)
    kube.request = request
    with pytest.raises(ServiceError, match='motion_execution_storage_error') as failure:
        render_scene(renderer)
    assert failure.value.status == 507
    assert not kube.resources
    assert renderer.journal.scene(scene_key)['state'] == 'failed'
    assert not list((tmp_path / 'runs').rglob('output*'))
    assert not list((tmp_path / 'runs').rglob('evidence.json'))


def test_disk_failure_cleanup_outage_restart_only_cleans_same_job(tmp_path, monkeypatch):
    kube = successful_kube(monkeypatch)
    renderer = scene_renderer(tmp_path, kube)
    original_write, original_request = Path.write_bytes, kube.request
    blocked = True
    def write(path, data):
        if path.name == 'output.pending':
            raise OSError(errno.ENOSPC, 'synthetic disk full')
        return original_write(path, data)
    def request(method, resource, *args, **kwargs):
        if blocked and method == 'DELETE':
            raise ServiceError(UNAVAILABLE, 503)
        return original_request(method, resource, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_bytes', write)
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    scene_key = operation_key(OWNER, 'operation-one') + ':0'
    assert renderer.journal.scene(scene_key)['state'] == 'cleanup_pending'
    assert any(kind == 'jobs' for kind, _ in kube.resources)
    # PVC writes remain unavailable on restart. Resource reconstruction uses
    # bounded temporary storage and does not rerun artifact collection.
    def no_pvc_write(path, data):
        if (tmp_path / 'runs') in path.parents:
            raise OSError(errno.ENOSPC, 'PVC still full')
        return original_write(path, data)
    monkeypatch.setattr(Path, 'write_bytes', no_pvc_write)
    blocked = False
    with pytest.raises(ServiceError, match='motion_execution_storage_error') as failure:
        render_scene(scene_renderer(tmp_path, kube))
    assert failure.value.status == 507 and not kube.resources
    assert renderer.journal.scene(scene_key)['state'] == 'failed'
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1
    assert sum(bool(r == 'pods' and n and n.endswith('-pod')) for _, r, n in kube.calls) == 1


def test_success_cleanup_outage_resumes_and_preserves_verified_output(tmp_path, monkeypatch):
    kube = successful_kube(monkeypatch)
    renderer = scene_renderer(tmp_path, kube)
    original = kube.request
    blocked = True
    def request(method, resource, *args, **kwargs):
        if blocked and method == 'DELETE':
            raise ServiceError(UNAVAILABLE, 503)
        return original(method, resource, *args, **kwargs)
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    key = operation_key(OWNER, 'operation-one')
    assert renderer.journal.scene(key + ':0')['state'] == 'cleanup_pending'
    blocked = False
    result = render_scene(scene_renderer(tmp_path, kube))
    assert Path(result['path']).read_bytes() == b'mp4'
    assert Path(result['path']).with_name('evidence.json').is_file()
    assert renderer.journal.scene(key + ':0')['state'] == 'completed'
    assert not kube.resources
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1


def test_corrupt_completed_pending_media_fails_after_cleanup_without_recreation(tmp_path, monkeypatch):
    kube = successful_kube(monkeypatch)
    renderer = scene_renderer(tmp_path, kube)
    original, blocked = kube.request, True
    def request(method, resource, *args, **kwargs):
        if blocked and method == 'DELETE':
            raise ServiceError(UNAVAILABLE, 503)
        return original(method, resource, *args, **kwargs)
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    output = next((tmp_path / 'runs').rglob('output.mp4'))
    output.write_bytes(b'corrupt media')
    blocked = False
    with pytest.raises(ServiceError, match='motion_cached_media_corrupt'):
        render_scene(scene_renderer(tmp_path, kube))
    assert not kube.resources and not output.exists()
    assert renderer.journal.scene(operation_key(OWNER, 'operation-one') + ':0')['state'] == 'failed'
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1


def test_configmap_delete_requires_observed_absence_before_terminal_success(tmp_path, monkeypatch):
    kube = successful_kube(monkeypatch)
    renderer = scene_renderer(tmp_path, kube)
    original, blocked = kube.request, True
    def request(method, resource, *args, **kwargs):
        if blocked and method == 'DELETE' and resource == 'configmaps':
            return {'status': 'Success'}  # Admission is not deletion observation.
        return original(method, resource, *args, **kwargs)
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    assert renderer.journal.scene(operation_key(OWNER, 'operation-one') + ':0')['state'] == 'cleanup_pending'
    assert any(kind == 'configmaps' for kind, _ in kube.resources)
    blocked = False
    assert Path(render_scene(scene_renderer(tmp_path, kube))['path']).read_bytes() == b'mp4'
    assert not kube.resources


@pytest.mark.parametrize('failure_kind', ['foreign_job', 'foreign_configmap', 'uid_conflict'])
def test_cleanup_conflicts_keep_recoverable_intent_and_refuse_foreign_deletion(tmp_path, failure_kind):
    kube = FakeKube(phase='oom')
    renderer = scene_renderer(tmp_path, kube)
    original = kube.request
    def request(method, resource, *args, **kwargs):
        if method == 'GET' and resource == 'pods':
            result = original(method, resource, *args, **kwargs)
            if result['items'] and failure_kind != 'uid_conflict':
                kind = 'jobs' if failure_kind == 'foreign_job' else 'configmaps'
                target = next(value for (resource_kind, _), value in kube.resources.items() if resource_kind == kind)
                target['metadata']['annotations'][ANNOTATION] = 'f' * 64
            return result
        if method == 'DELETE' and failure_kind == 'uid_conflict':
            raise ServiceError('motion_k8s_conflict', 409)
        return original(method, resource, *args, **kwargs)
    kube.request = request
    with pytest.raises(ServiceError, match=UNAVAILABLE):
        render_scene(renderer)
    assert renderer.journal.scene(operation_key(OWNER, 'operation-one') + ':0')['state'] == 'cleanup_pending'
    assert kube.resources
    if failure_kind != 'uid_conflict':
        kind = 'jobs' if failure_kind == 'foreign_job' else 'configmaps'
        assert not any(m == 'DELETE' and r == kind for m, r, _ in kube.calls)


def test_deadline_failure_cleans_then_retains_timeout_status_on_restart(tmp_path):
    kube = FakeKube(phase='timed_out')
    renderer = scene_renderer(tmp_path, kube)
    for _ in range(2):
        with pytest.raises(ServiceError, match='motion_execution_timed_out') as failure:
            render_scene(renderer)
        assert failure.value.status == 504
    assert not kube.resources
    assert renderer.journal.scene(operation_key(OWNER, 'operation-one') + ':0')['state'] == 'timed_out'
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1


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
     'motion_execution_timed_out'),
    ([('experiment.json', b'{"status":"failed"}'), ('render-receipt.json', b'{"status":"failed","stage":"rendering_scene","error":"private path"}')],
     'motion_render_stage_failed'),
    ([('experiment.json', b'{"status":"failed"}'), ('render-receipt.json', b'{"status":"failed","stage":"preparing_audio"}')],
     'motion_audio_mux_failed'),
    ([('experiment.json', b'{"status":"failed"}'), ('render-receipt.json', b'{"status":"failed","stage":"validating_scene"}')],
     'motion_media_output_invalid'),
    ([('experiment.json', b'{"status":"failed"}'), ('observe-receipt.json', b'{"status":"failed","stage":"observing_scene"}')],
     'motion_observation_stage_failed')])
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


@pytest.mark.parametrize('scene_state', ['submitting', 'running', 'cleanup_pending'])
def test_recoverable_scene_handle_precedes_other_queued_operations_after_restart(tmp_path, scene_state):
    seen = []
    def render(snapshot, root, **kwargs):
        seen.append(kwargs['key'])
        raise ServiceError(UNAVAILABLE, 503)
    renderer = SimpleNamespace(render=render)
    first = make_service(tmp_path, renderer)
    active = first.submit(OWNER, 'active-operation', freeze())
    first.journal.scene_intent(active['key'] + ':0', 'a' * 64, 'vm-test')
    first.journal.scene_update(active['key'] + ':0', scene_state)
    queued = first.submit(OWNER, 'queued-operation', freeze())
    # A transient API failure requeues the active handle after the other row.
    first.journal.update(active['key'], state='queued')
    restarted = make_service(tmp_path, renderer)
    restarted.tick()
    restarted.tick()
    assert seen == [active['key'], active['key']]
    assert restarted.journal.operation(OWNER, 'queued-operation')['state'] == 'queued'
    assert queued['key'] not in seen


def test_pending_operation_cleanup_blocks_new_queued_render_after_restart(tmp_path):
    calls = []
    def cleanup(snapshot, *, key, **kwargs):
        calls.append(('cleanup', key))
        raise ServiceError(UNAVAILABLE, 503)
    renderer = SimpleNamespace(render=lambda *args, **kwargs: pytest.fail('new operation started during cleanup'),
                               cleanup=cleanup)
    service = make_service(tmp_path, renderer)
    active = service.submit(OWNER, 'cleanup-operation', freeze())
    queued = service.submit(OWNER, 'queued-operation', freeze())
    service.journal.update(active['key'], state='queued', result=json.dumps({'cleanup_pending': dict(
        state='failed', error='motion_execution_failed', status=502)}))
    restarted = make_service(tmp_path, renderer)
    restarted.tick()
    restarted.tick()
    assert calls == [('cleanup', active['key']), ('cleanup', active['key'])]
    assert restarted.journal.operation(OWNER, 'queued-operation')['state'] == 'queued'
    assert queued['key'] != active['key']


@pytest.mark.parametrize('code,status,state', [
    ('motion_execution_storage_error', 507, 'failed'),
    ('media_render_cancelled', 409, 'cancelled'),
    ('motion_execution_timed_out', 504, 'timed_out')])
def test_service_cleanup_outage_is_private_and_restart_never_calls_render(tmp_path, code, status, state):
    calls, blocked = [], True
    def render(snapshot, root, **kwargs):
        calls.append('render')
        (root / 'assets').mkdir(parents=True)
        (root / 'assets' / 'master.wav').write_bytes(b'owned scratch')
        (root / 'video.mp4').write_bytes(b'partial video')
        (root / 'report.json').write_bytes(b'partial report')
        if status == 507:
            raise OSError(errno.ENOSPC, 'private filesystem detail')
        raise ServiceError(code, status)
    def cleanup(snapshot, *, key, **pending):
        calls.append('cleanup')
        assert pending == dict(state=state, error=code, status=status)
        if blocked:
            raise ServiceError(UNAVAILABLE, 503)
    renderer = SimpleNamespace(render=render, cleanup=cleanup)
    service = make_service(tmp_path, renderer)
    service.submit(OWNER, 'cleanup-operation', freeze())
    unrelated = service.root / 'other-operation' / 'result'
    unrelated.mkdir(parents=True)
    (unrelated / 'video.mp4').write_bytes(b'previous success')
    service.tick()
    row = service.journal.operation(OWNER, 'cleanup-operation')
    assert row['state'] == 'queued' and 'cleanup_pending' in json.loads(row['result'])
    assert public_operation(row) == dict(operation_id='cleanup-operation', snapshot_sha256=freeze()['sha256'], state='queued')
    blocked = False
    restarted = make_service(tmp_path, renderer)
    restarted.tick()
    row = restarted.journal.operation(OWNER, 'cleanup-operation')
    assert row['state'] == state and row['error'] == code
    details = json.loads(row['result'])
    assert set(details) == {'diagnostic'} and details['diagnostic']['code'] == code
    assert public_operation(row)['diagnostic'] == details['diagnostic']
    assert 'private filesystem detail' not in json.dumps(public_operation(row))
    assert calls == ['render', 'cleanup', 'cleanup']
    assert not list((service.root / row['key'] / 'result').rglob('*'))
    assert (unrelated / 'video.mp4').read_bytes() == b'previous success'


def test_service_disk_error_with_real_scene_handle_restarts_cleanup_only(tmp_path, monkeypatch):
    kube = successful_kube(monkeypatch)
    scene = scene_renderer(tmp_path, kube)
    calls, blocked = [], True
    original_write, original_request = Path.write_bytes, kube.request
    def write(path, data):
        if path.name == 'output.pending':
            raise OSError(errno.ENOSPC, 'synthetic full PVC')
        return original_write(path, data)
    def request(method, resource, *args, **kwargs):
        if blocked and method == 'DELETE':
            raise ServiceError(UNAVAILABLE, 503)
        return original_request(method, resource, *args, **kwargs)
    def render(snapshot, root, *, key, cancel_event):
        calls.append('render')
        # This models an unexpected composer exception after a durable scene
        # handle exists. The outer operation must still drain that handle.
        try:
            scene.render(snapshot['scenes'][0], key=key, scene_index=0, quality=snapshot['quality'])
        except ServiceError:
            raise RuntimeError('synthetic composer fault')
    def cleanup(snapshot, *, key, **pending):
        calls.append('cleanup')
        scene.cleanup_operation(snapshot, key, **pending)
    monkeypatch.setattr(Path, 'write_bytes', write)
    kube.request = request
    renderer = SimpleNamespace(render=render, cleanup=cleanup)
    service = make_service(tmp_path, renderer)
    service.submit(OWNER, 'cleanup-operation', freeze())
    service.tick()
    assert service.journal.operation(OWNER, 'cleanup-operation')['state'] == 'queued'
    blocked = False
    restarted = make_service(tmp_path, renderer)
    restarted.tick()
    row = restarted.journal.operation(OWNER, 'cleanup-operation')
    assert row['state'] == 'failed' and row['error'] == 'motion_execution_internal_error'
    assert not kube.resources and calls == ['render', 'cleanup', 'cleanup']
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1


def test_full_journal_retains_running_identity_until_storage_recovers(tmp_path, monkeypatch):
    kube = successful_kube(monkeypatch)
    scene = scene_renderer(tmp_path, kube)
    original_write = Path.write_bytes
    original_scene_update, original_update = ExecutionJournal.scene_update, ExecutionJournal.update
    blocked = True
    def write(path, data):
        if path.name == 'output.pending':
            raise OSError(errno.ENOSPC, 'synthetic full PVC')
        return original_write(path, data)
    def scene_update(journal, key, state, **kwargs):
        if blocked and state == 'cleanup_pending':
            raise sqlite3.OperationalError('database or disk is full')
        return original_scene_update(journal, key, state, **kwargs)
    def update(journal, key, **values):
        if blocked and values.get('result') and 'cleanup_pending' in values['result']:
            raise sqlite3.OperationalError('database or disk is full')
        return original_update(journal, key, **values)
    def render(snapshot, root, *, key, **kwargs):
        return scene.render(snapshot['scenes'][0], key=key, scene_index=0, quality=snapshot['quality'])
    renderer = SimpleNamespace(render=render,
        cleanup=lambda snapshot, *, key, **pending: scene.cleanup_operation(snapshot, key, **pending))
    monkeypatch.setattr(Path, 'write_bytes', write)
    monkeypatch.setattr(ExecutionJournal, 'scene_update', scene_update)
    monkeypatch.setattr(ExecutionJournal, 'update', update)
    service = make_service(tmp_path, renderer)
    service.submit(OWNER, 'full-journal-operation', freeze())
    with pytest.raises(sqlite3.OperationalError):
        service.tick()
    row = service.journal.operation(OWNER, 'full-journal-operation')
    assert row['state'] == 'running' and row['result'] is None
    assert scene.journal.scene(row['key'] + ':0')['state'] == 'running'
    assert any(kind == 'jobs' for kind, _ in kube.resources)
    blocked = False
    make_service(tmp_path, renderer).tick()
    row = service.journal.operation(OWNER, 'full-journal-operation')
    assert row['state'] == 'failed' and row['error'] == 'motion_execution_storage_error'
    assert not kube.resources
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1


def test_service_success_preserves_final_video_and_report(tmp_path):
    def render(snapshot, root, **kwargs):
        root.mkdir(parents=True)
        output = root / 'video.mp4'
        output.write_bytes(b'completed media')
        (root / 'report.json').write_bytes(b'completed report')
        return dict(path=str(output), bytes=output.stat().st_size)
    renderer = SimpleNamespace(render=render, cleanup=lambda *args, **kwargs: pytest.fail('success was cleaned'))
    service = make_service(tmp_path, renderer)
    service.submit(OWNER, 'success-operation', freeze())
    service.tick()
    row = service.journal.operation(OWNER, 'success-operation')
    root = service.root / row['key'] / 'result'
    assert row['state'] == 'completed'
    assert (root / 'video.mp4').read_bytes() == b'completed media'
    assert (root / 'report.json').read_bytes() == b'completed report'


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
    stages = []
    event = SimpleNamespace(is_set=lambda: False,
        report_progress=lambda stage, **details: stages.append((stage, details)))
    result = MixedMotionRenderer(Scene(), static_renderer=static).render(snapshot, tmp_path / 'result',
        key='local-test', cancel_event=event)
    assert result['fps'] == 24 and result['duration_seconds'] == 3
    assert result['visual_mode'] == 'mixed' and result['snapshot_sha256'] == snapshot['sha256']
    assert Path(result['path']).is_file()
    assert not (tmp_path / 'result' / 'assets').exists()
    assert stages[-2:] == [('compositing', {'scene_index': None, 'completed_scenes': 2}),
                          ('validating_video', {'scene_index': None, 'completed_scenes': 2})]
    assert [details['scene_index'] for stage, details in stages if stage == 'preparing_scene'] == [0, 1]
    # Inputs' per-scene video audio above was silence; nonzero original PCM must
    # survive in the final AAC. This catches concatenation of per-scene AAC.
    decoded = tmp_path / 'decoded.wav'
    media._run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-i', result['path'], '-map', '0:a:0',
                '-c:a', 'pcm_s16le', str(decoded)], cwd=tmp_path, timeout=30)
    import wave
    with wave.open(str(decoded), 'rb') as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 24000
        assert any(wav.readframes(wav.getnframes()))


def test_progress_survives_service_restart_and_failure_cleanup_without_private_details(tmp_path):
    calls = []
    def render(snapshot, root, *, key, cancel_event):
        calls.append(key)
        cancel_event.report_progress('observing_scene', scene_index=0)
        if len(calls) == 1:
            raise ServiceError(UNAVAILABLE, 503)
        raise ServiceError('motion_observation_stage_failed', 502)
    service = make_service(tmp_path, SimpleNamespace(render=render))
    original = service.submit(OWNER, 'status-operation', freeze())
    service.tick()
    first = public_operation(service.journal.operation(OWNER, 'status-operation'))
    assert first['state'] == 'queued' and first['progress']['stage'] == 'observing_scene'
    restarted = make_service(tmp_path, SimpleNamespace(render=render))
    assert public_operation(restarted.journal.operation(OWNER, 'status-operation')) == first
    restarted.tick()
    failed = public_operation(restarted.journal.operation(OWNER, 'status-operation'))
    assert failed['state'] == 'failed' and failed['progress']['scene_index'] == 0
    assert failed['diagnostic']['category'] == 'observation'
    assert failed['diagnostic']['stage'] == 'observing_scene'
    assert failed['diagnostic']['diagnostic_id'].startswith('MR-')
    assert calls == [original['key'], original['key']]
    assert 'cleanup_pending' not in json.dumps(failed) and 'snapshot' not in failed
    assert restarted.journal.operation(OTHER, 'status-operation') is None


def test_progress_updates_do_not_replace_persisted_cleanup_intent(tmp_path):
    journal = ExecutionJournal(tmp_path / 'journal.db')
    row = journal.submit(OWNER, 'status-operation', freeze())
    pending = {'cleanup_pending': {'state': 'failed', 'error': 'motion_execution_failed', 'status': 502}}
    journal.update(row['key'], state='queued', result=json.dumps(pending))
    journal.progress(row['key'], 'rendering_scene', scene_index=0)
    assert json.loads(journal.operation(OWNER, 'status-operation')['result']) == pending


def test_renderer_status_callback_filters_private_fields_and_preserves_failure_code(tmp_path):
    snapshot, seen = freeze(), []
    progress = dict(stage='observing_scene', scene_index=0, completed_scenes=0, total_scenes=1,
                    updated_at=1234, path='/private/path', token=TOKEN)
    diagnostic = dict(code='motion_observation_stage_failed', category='observation',
        stage='observing_scene', scene_index=0, diagnostic_id='MR-' + 'a' * 16, stderr='secret')
    response = dict(operation_id='operation', snapshot_sha256=snapshot['sha256'], state='failed',
                    error='motion_observation_stage_failed', progress=progress, diagnostic=diagnostic)
    renderer = RemoteMotionRenderer('http://internal', TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    with pytest.raises(ServiceError, match='motion_observation_stage_failed'):
        renderer.render(snapshot, tmp_path / 'out', operation_id='operation', owner='owner-one',
                        progress_callback=seen.append)
    assert len(seen) == 1 and seen[0]['progress']['stage'] == 'observing_scene'
    assert 'token' not in seen[0]['progress'] and 'path' not in seen[0]['progress']
    assert 'stderr' not in seen[0]['diagnostic']


@pytest.mark.parametrize('progress', [
    {'stage': '/private/path'},
    {'stage': ['rendering_scene']},
    {'stage': 'compositing', 'scene_index': 999, 'completed_scenes': 0, 'total_scenes': 1, 'updated_at': 1},
    {'stage': 'compositing', 'scene_index': None, 'completed_scenes': 3, 'total_scenes': 1, 'updated_at': 1},
    {'stage': 'compositing', 'scene_index': None, 'completed_scenes': 0, 'total_scenes': 1, 'updated_at': float('nan')},
])
def test_invalid_remote_progress_is_not_forwarded(progress):
    from video_workbench.motion_render_adapter import safe_progress
    assert safe_progress(progress) is None


@pytest.mark.parametrize('field,value', [('category', []), ('stage', {}), ('scene_index', True),
                                       ('diagnostic_id', '/private/path')])
def test_invalid_remote_diagnostic_is_not_forwarded(field, value):
    from video_workbench.motion_render_adapter import safe_diagnostic
    diagnostic = dict(code='motion_execution_failed', category='execution', stage=None,
                      scene_index=0, diagnostic_id='MR-' + 'a' * 16)
    diagnostic[field] = value
    assert safe_diagnostic(diagnostic) is None


def test_stage_marker_is_bounded_and_does_not_interpret_unstructured_logs(tmp_path):
    kube = FakeKube()
    values = [b'private stderr observe failed',
              b'VIDEO_MOTION_PROGRESS={"schema":"video.motion-progress.v1","stage":"observing_scene"}\n',
              b'VIDEO_MOTION_PROGRESS={"schema":"video.motion-progress.v1","stage":"private-token"}\n']
    def request(*args, **kwargs):
        assert kwargs['params'] == {'container': 'renderer', 'limitBytes': 4096, 'tailLines': 8}
        return values.pop(0)
    kube.request = request
    renderer = scene_renderer(tmp_path, kube)
    pod = {'metadata': {'name': 'owned-pod'}}
    assert renderer._progress_stage(pod) is None
    assert renderer._progress_stage(pod) == 'observing_scene'
    assert renderer._progress_stage(pod) is None


@pytest.mark.parametrize('code', ['motion_expected_highlight_missing', 'motion_invisible_highlight',
                                 'motion_text_overflow'])
def test_real_feedback_validation_codes_remain_specific_and_classified(code):
    from video_workbench.motion_render_service import failure_diagnostic
    result = failure_diagnostic('a' * 64, code,
        dict(stage='validating_scene', scene_index=0))
    assert result['code'] == code and result['category'] == 'validation'
    assert result['stage'] == 'validating_scene' and result['scene_index'] == 0


def test_motion_context_survives_scene_and_service_cleanup_restarts_to_remote_callback(tmp_path, monkeypatch):
    """One synthetic Job, two independent outages, no new render/model/TTS attempt."""
    import video_workbench.motion_render_k8s as module
    context = dict(target='certificate', kind='move', ordinal=7, beat_id='beat_' + '1' * 16)
    private = dict(path='/private/path', stderr='private stderr', owner=OWNER, token=TOKEN)
    kube = successful_kube(monkeypatch)
    original_request = kube.request
    scene_blocked = service_blocked = True
    calls = []

    def invalid_artifacts(*args):
        calls.append('verify')
        error = ServiceError('motion_invisible_move', 422)
        error.motion_context = dict(context, **private)
        raise error

    def request(method, resource, *args, **kwargs):
        if scene_blocked and method == 'DELETE':
            raise ServiceError(UNAVAILABLE, 503)
        return original_request(method, resource, *args, **kwargs)

    def render(snapshot, root, *, key, cancel_event):
        calls.append('render')
        return scene_renderer(tmp_path, kube).render(snapshot['scenes'][0], key=key,
            scene_index=0, quality=snapshot['quality'], cancel_event=cancel_event)

    def cleanup(snapshot, *, key, **pending):
        calls.append('cleanup')
        assert pending == dict(state='failed', error='motion_invisible_move', status=422)
        if service_blocked:
            raise ServiceError(UNAVAILABLE, 503)
        scene_renderer(tmp_path, kube).cleanup_operation(snapshot, key, **pending)

    monkeypatch.setattr(module, 'validate_artifacts', invalid_artifacts)
    kube.request = request
    renderer = SimpleNamespace(render=render, cleanup=cleanup)
    service = make_service(tmp_path, renderer)
    row = service.submit(OWNER, 'context-operation', freeze())
    assert service.tick()
    pending = json.loads(service.journal.scene(row['key'] + ':0')['result'])['cleanup_pending']
    assert pending['result'] == {'motion_context': context}
    assert service.journal.operation(OWNER, 'context-operation')['state'] == 'queued'

    scene_blocked = False
    restarted = make_service(tmp_path, renderer)
    assert restarted.tick()
    row = restarted.journal.operation(OWNER, 'context-operation')
    assert row['state'] == 'queued'
    assert json.loads(row['result'])['diagnostic']['motion_context'] == context
    assert 'diagnostic' not in public_operation(row)

    service_blocked = False
    resumed = make_service(tmp_path, renderer)
    assert resumed.tick()
    row = resumed.journal.operation(OWNER, 'context-operation')
    response = public_operation(row)
    assert response['state'] == 'failed' and response['error'] == 'motion_invisible_move'
    assert response['diagnostic']['motion_context'] == context
    assert response['diagnostic']['scene_index'] == 0
    assert json.loads(resumed.journal.scene(row['key'] + ':0')['result']) == {'motion_context': context}
    assert calls == ['render', 'verify', 'render', 'cleanup', 'cleanup']
    assert sum(m == 'POST' and r == 'jobs' for m, r, _ in kube.calls) == 1
    assert not kube.resources
    for secret in private.values():
        assert secret not in json.dumps(response)

    seen = []
    remote = RemoteMotionRenderer('http://internal', TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    with pytest.raises(ServiceError, match='motion_invisible_move') as failure:
        remote.render(freeze(), tmp_path / 'remote', operation_id='context-operation', owner='owner-one',
                      progress_callback=seen.append)
    assert failure.value.motion_context == context
    assert seen[0]['diagnostic']['motion_context'] == context


@pytest.mark.parametrize('field,value', [
    ('target', '/private/path'), ('target', []), ('kind', ['move']), ('kind', 'exec'),
    ('ordinal', True), ('ordinal', -1), ('ordinal', 48), ('beat_id', 'private-beat'),
])
def test_invalid_optional_motion_context_is_dropped_without_hiding_valid_diagnostic(field, value):
    from video_workbench.motion_render_adapter import safe_diagnostic, safe_motion_context
    context = dict(target='certificate', kind='move', ordinal=47, beat_id='beat_' + '1' * 16)
    clean = dict(context)
    assert safe_motion_context(dict(context, stderr='private', path='/private')) == clean
    context[field] = value
    assert safe_motion_context(context) is None
    diagnostic = dict(code='motion_invisible_move', category='validation', stage='validating_scene',
                      scene_index=0, diagnostic_id='MR-' + 'a' * 16)
    assert safe_diagnostic(dict(diagnostic, motion_context=context)) == diagnostic


def test_invalid_base_diagnostic_cannot_publish_a_valid_motion_context():
    from video_workbench.motion_render_adapter import safe_diagnostic, safe_motion_context
    context = dict(target='certificate', kind='move', ordinal=0, beat_id='beat_' + '1' * 16)
    assert safe_motion_context({key: value for key, value in context.items() if key != 'beat_id'}) is None
    assert safe_diagnostic(dict(code='motion_invisible_move', category='validation', stage='private',
        scene_index=0, diagnostic_id='MR-' + 'a' * 16, motion_context=context)) is None


@pytest.mark.parametrize('diagnostic', [None, {}, {'stderr': 'private'}])
def test_legacy_terminal_failure_without_diagnostic_context_keeps_original_error(tmp_path, diagnostic):
    snapshot, seen = freeze(), []
    response = dict(operation_id='legacy-operation', snapshot_sha256=snapshot['sha256'], state='failed',
                    error='motion_invisible_move', diagnostic=diagnostic)
    renderer = RemoteMotionRenderer('http://internal', TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    with pytest.raises(ServiceError, match='motion_invisible_move') as failure:
        renderer.render(snapshot, tmp_path / 'legacy', operation_id='legacy-operation', owner='owner-one',
                        progress_callback=seen.append)
    assert not hasattr(failure.value, 'motion_context') and seen == []
