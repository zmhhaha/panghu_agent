"""Offline HTTP, subprocess and Kubernetes JSON boundaries; no live systems."""
import copy
import json
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from agent_service.contracts import canonical_bytes, digest
from video_workbench.deploy.cancel_live import CancellationRunner
from video_workbench.deploy.fault_evidence import (
    PREFIX, main, observe_pods, pod_stop, read_json, recovery_evidence, trace_events,
    validate_restart, write_new,
)
from video_workbench.deploy.fault_proxy import Relay, handler_for
from http.server import HTTPServer
from video_workbench.deploy.verify_live import Runner, VerificationFailure
from test_live_verifier import Clock, Peer, TOKEN, CSRF, setup


OPERATION, JOB, OLD_POD, NEW_POD, WORKER_POD = (str(UUID(int=i)) for i in (10, 11, 12, 13, 14))


def pod(uid=OLD_POD, *, worker=False, terminated=False, attempt=None):
    return dict(uid=uid, name='worker' if worker else 'video-workbench-abc', namespace='workers' if worker else 'video-workbench',
        labels={PREFIX + 'attempt': attempt, 'app': 'agent-capsule'} if worker else {'app.kubernetes.io/name': 'video-workbench'},
        created_at='1970-01-01T00:15:00+00:00' if uid == OLD_POD else '1970-01-01T00:16:46+00:00',
        deletion_at=None, phase='Succeeded' if terminated else 'Running',
        containers=[dict(name='worker' if worker else 'workbench', image_id='registry/video@sha256:' + 'a' * 64,
            ready=not terminated, restart_count=0,
            state={'terminated': {'startedAt': '1970-01-01T00:16:00+00:00', 'finishedAt': '1970-01-01T00:16:41+00:00',
                                  'exitCode': 0}} if terminated else {'running': {'startedAt': '1970-01-01T00:16:46+00:00'}})],
        pvc_claims=[] if worker else ['video-workbench-data'])


def observations():
    before = dict(schema_version='video-pod-observation.v1', namespace='video-workbench', selector='app.kubernetes.io/name=video-workbench',
                  started_at=999.0, observed_at=1000.0, items=[pod()])
    after = dict(schema_version='video-pod-observation.v1', namespace='video-workbench', selector='app.kubernetes.io/name=video-workbench',
                 started_at=1009.0, observed_at=1010.0, items=[pod(NEW_POD)])
    return before, after


def headers(op=OPERATION):
    return {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json', 'Idempotency-Key': 'video-' + op}


def submission(op=OPERATION, checksum='a' * 64):
    return dict(api_version='1', profile='video.scene.v1', profile_revision='1', business_ref='video:' + op,
                task={'input_sha256': checksum}, inputs=[], timeout_seconds=300, execution_deadline_at=2000)


def relay_fixture(tmp_path, *, upstream_peer=None):
    token = tmp_path / 'token'
    token.write_text(TOKEN)
    calls = []
    def upstream(request):
        calls.append(request)
        if upstream_peer:
            return upstream_peer(request)
        return httpx.Response(202 if request.method == 'POST' else 200,
                              json=dict(job_id=JOB, business_ref='video:' + OPERATION, state='running'))
    clock = Clock()
    clock.now = 1005
    relay = Relay(upstream='http://agent.invalid', token_file=token, output=tmp_path / 'relay',
                  run_id='offline-run-0001', transport=httpx.MockTransport(upstream), clock=clock)
    return relay, calls, clock


def test_relay_only_drops_one_accepted_response_then_gates_lookup_and_deduplicates(tmp_path):
    relay, calls, clock = relay_fixture(tmp_path)
    try:
        write_new(relay.output / 'arm.json', dict(run_id=relay.run_id, input_sha256='a' * 64))
        assert relay.handle('POST', '/v1/jobs', headers(), canonical_bytes(submission())) is None
        assert len(calls) == 1
        target = '/v1/jobs/by-idempotency/video-' + OPERATION
        assert relay.handle('GET', target, headers())[0] == 503
        assert len(calls) == 1
        write_new(relay.output / 'release.json', dict(job_id=JOB, operation_id=OPERATION,
                  before_sha256='b' * 64, after_sha256='c' * 64))
        assert relay.handle('GET', target, headers())[0] == 200
        assert relay.handle('POST', '/v1/jobs', headers(), canonical_bytes(submission()))[0] == 409
        assert len(calls) == 2 and calls[0].method == 'POST' and calls[1].method == 'GET'
        events = trace_events(read_json(relay.output / 'trace.json'))
        assert sum(e['event'] == 'response_dropped' for e in events) == 1
        assert any(e['event'] == 'lookup_blocked' for e in events)
        assert TOKEN not in (relay.output / 'trace.json').read_text()
        assert 'Authorization' not in (relay.output / 'trace.json').read_text()
    finally:
        relay.close()


@pytest.mark.parametrize('path', ['/v1/jobs?destination=evil', 'http://evil.invalid/v1/jobs',
                                 '/v1/admin', '/v1/jobs/../../admin', '/v1/inputs?name=other.json'])
def test_relay_cannot_be_used_as_arbitrary_or_admin_forwarder(tmp_path, path):
    relay, calls, clock = relay_fixture(tmp_path)
    try:
        assert relay.handle('POST', path, headers(), b'{}')[0] == 400
        assert calls == []
    finally:
        relay.close()


def test_relay_rejects_auth_and_non_video_profile_before_forwarding(tmp_path):
    relay, calls, clock = relay_fixture(tmp_path)
    try:
        assert relay.handle('POST', '/v1/jobs', {}, b'{}')[0] == 401
        body = submission()
        body['profile'] = 'research.generate.v1'
        with pytest.raises(VerificationFailure, match='relay_job_scope_rejected'):
            relay.handle('POST', '/v1/jobs', headers(), canonical_bytes(body))
        assert calls == []
    finally:
        relay.close()


def test_relay_ambiguous_upstream_post_is_not_forwarded_twice(tmp_path):
    def lost(request):
        raise httpx.ReadTimeout(TOKEN + CSRF, request=request)
    relay, calls, clock = relay_fixture(tmp_path, upstream_peer=lost)
    try:
        assert relay.handle('POST', '/v1/jobs', headers(), canonical_bytes(submission()))[0] == 503
        assert relay.handle('POST', '/v1/jobs', headers(), canonical_bytes(submission()))[0] == 409
        assert len(calls) == 1
        assert TOKEN not in (relay.output / 'trace.json').read_text()
    finally:
        relay.close()


def test_relay_does_not_drop_rejected_submission(tmp_path):
    relay, calls, clock = relay_fixture(tmp_path, upstream_peer=lambda r: httpx.Response(422, json={'detail': {'code': 'bad'}}))
    try:
        write_new(relay.output / 'arm.json', dict(run_id=relay.run_id, input_sha256='a' * 64))
        assert relay.handle('POST', '/v1/jobs', headers(), canonical_bytes(submission()))[0] == 422
        assert not any(e['event'] == 'response_dropped' for e in relay.events)
    finally:
        relay.close()


def test_loopback_http_handler_actually_closes_only_the_accepted_response(tmp_path):
    relay, calls, clock = relay_fixture(tmp_path)
    write_new(relay.output / 'arm.json', dict(run_id=relay.run_id, input_sha256='a' * 64))
    server = HTTPServer(('127.0.0.1', 0), handler_for(relay))
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url='http://127.0.0.1:' + str(server.server_port), trust_env=False, timeout=2) as client:
            with pytest.raises(httpx.RemoteProtocolError):
                client.post('/v1/jobs', headers=headers(), content=canonical_bytes(submission()))
            assert client.get('/v1/jobs/by-idempotency/video-' + OPERATION, headers=headers()).status_code == 503
            assert client.post('/v1/jobs', headers=headers(), content=canonical_bytes(submission())).status_code == 409
        assert len(calls) == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        relay.close()


@pytest.mark.parametrize('attack', ['same_uid', 'different_pvc', 'different_image', 'unready', 'too_early'])
def test_restart_evidence_requires_same_pvc_and_image_new_ready_pod_after_drop(attack):
    before, after = observations()
    if attack == 'same_uid': after['items'][0]['uid'] = OLD_POD
    if attack == 'different_pvc': after['items'][0]['pvc_claims'] = ['new-data']
    if attack == 'different_image': after['items'][0]['containers'][0]['image_id'] = 'other'
    if attack == 'unready': after['items'][0]['containers'][0]['ready'] = False
    if attack == 'too_early': after['items'][0]['created_at'] = '1970-01-01T00:16:44+00:00'
    with pytest.raises(VerificationFailure):
        validate_restart(before, after, dropped_at=1005)


def test_pod_observer_runs_fixed_get_and_saves_only_safe_fields(tmp_path):
    uid, namespace, selector = OLD_POD, 'video-workbench', 'app.kubernetes.io/name=video-workbench'
    raw = dict(kind='List', items=[dict(metadata=dict(uid=uid, name='video-workbench-abc', namespace=namespace,
        creationTimestamp='1970-01-01T00:15:00Z', labels={'app.kubernetes.io/name': 'video-workbench'}, annotations={'secret': TOKEN}),
        spec=dict(volumes=[dict(persistentVolumeClaim={'claimName': 'video-workbench-data'})],
                  containers=[dict(name='workbench', env=[dict(name='TOKEN', value=TOKEN)])]),
        status=dict(phase='Running', containerStatuses=[dict(name='workbench', imageID='sha256:abc', ready=True,
            state=dict(running={'startedAt': '1970-01-01T00:16:00Z', 'message': TOKEN}))]))])
    calls = []
    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=canonical_bytes(raw))
    output = tmp_path / 'pods.json'
    result = observe_pods(kubectl=Path('C:/tools/kubectl.exe'), kubeconfig=Path('C:/ops/kubeconfig'),
        namespace=namespace, selector=selector, output=output, executor=execute, clock=lambda: 1000)
    assert calls[0][0][-7:] == ['video-workbench', 'get', 'pods', '-l', selector, '-o', 'json']
    assert calls[0][1]['stderr'] == subprocess.DEVNULL
    assert 'shell' not in calls[0][1]
    assert TOKEN not in output.read_text()
    assert result['items'][0]['pvc_claims'] == ['video-workbench-data']


def paused_svg_run(setup):
    settings, peer, clock = setup
    settings = replace(settings, execute=True, pause_for_faults=True, checkpoint_timeout=1)
    original_sleep = clock.sleep
    def sleep(seconds):
        path = settings.output / 'before-svg-checkpoint.json'
        if path.exists() and not (settings.output / 'before-svg-continue.json').exists():
            value = read_json(path)
            write_new(settings.output / 'before-svg-continue.json', dict(run_id=value['run_id'], checkpoint_sha256=digest(value)))
        original_sleep(seconds)
    runner = Runner(settings, workbench_transport=httpx.MockTransport(peer.workbench),
        agent_transport=httpx.MockTransport(peer.agent), monotonic=clock, sleep=sleep)
    report = runner.run()
    assert report['error'] == 'operator_checkpoint_timeout'
    assert report['generation_posts'] == 2 and report['completed_generation_tasks'] == 2
    assert report['waiting_for_operator']['checkpoint'] == 'after-svg'
    return settings, peer, clock, report


@pytest.mark.parametrize('fence', [1, 37])
def test_recovery_checker_binds_single_post_same_job_restart_and_single_candidate(setup, tmp_path, fence):
    setup[1].fence = fence
    settings, peer, _, report = paused_svg_run(setup)
    svg = report['intents'][1]
    def upstream(request):
        return httpx.Response(202 if request.method == 'POST' else 200, json=peer.jobs[svg['job_id']])
    relay_root = tmp_path / 'relay-case'
    relay_root.mkdir()
    relay, calls, clock = relay_fixture(relay_root, upstream_peer=upstream)
    try:
        write_new(relay.output / 'arm.json', dict(run_id=relay.run_id, input_sha256=svg['input_sha256']))
        assert relay.handle('POST', '/v1/jobs', headers(svg['operation_id']),
            canonical_bytes(submission(svg['operation_id'], svg['input_sha256']))) is None
        lookup = '/v1/jobs/by-idempotency/video-' + svg['operation_id']
        assert relay.handle('GET', lookup, headers())[0] == 503
        before, after = observations()
        before_file, after_file = tmp_path / 'before.json', tmp_path / 'after.json'
        write_new(before_file, before)
        write_new(after_file, after)
        assert main(['release-relay', '--proxy-dir', str(relay.output), '--before', str(before_file),
                     '--after', str(after_file)]) == 0
        clock.now = 1011
        assert relay.handle('GET', lookup, headers())[0] == 200
        result = recovery_evidence(settings.output, relay.output, before, after)
        assert result['status'] == 'passed' and result['jobs_posts'] == result['candidate_versions'] == 1
        assert result['job_id'] == svg['job_id']
        assert result['attempt']['fence'] == fence
        assert result['job_starting_events'] == 1
        assert result['restart']['new_pod_uid'] == NEW_POD
        events_path = settings.output / 'svg-events.json'
        correct_events = read_json(events_path)
        changed = copy.deepcopy(correct_events)
        changed['binding']['attempt']['attempt_id'] = str(UUID(int=99999))
        events_path.write_bytes(canonical_bytes(changed))
        with pytest.raises(VerificationFailure, match='job_events_binding_invalid'):
            recovery_evidence(settings.output, relay.output, before, after)
        changed = copy.deepcopy(correct_events)
        changed['pages'][0]['response']['items'][2]['type'] = 'job.starting'
        events_path.write_bytes(canonical_bytes(changed))
        with pytest.raises(VerificationFailure, match='job_multiple_or_missing_starts'):
            recovery_evidence(settings.output, relay.output, before, after)
        events_path.write_bytes(canonical_bytes(correct_events))
        # A second rejected POST is still a failed recovery experiment.
        relay.handle('POST', '/v1/jobs', headers(svg['operation_id']),
                     canonical_bytes(submission(svg['operation_id'], svg['input_sha256'])))
        with pytest.raises(VerificationFailure):
            recovery_evidence(settings.output, relay.output, before, after)
    finally:
        relay.close()


def test_operator_checkpoint_timeout_sends_no_svg_or_later_model_tasks(setup):
    settings, peer, clock = setup
    settings = replace(settings, execute=True, pause_for_faults=True, checkpoint_timeout=1)
    runner = Runner(settings, workbench_transport=httpx.MockTransport(peer.workbench),
                    agent_transport=httpx.MockTransport(peer.agent), monotonic=clock, sleep=clock.sleep)
    result = runner.run()
    assert result['error'] == 'operator_checkpoint_timeout'
    assert result['generation_posts'] == 1
    assert result['waiting_for_operator']['checkpoint'] == 'before-svg'


def test_bound_operator_files_release_all_three_checkpoints_without_extra_generation(setup):
    settings, peer, clock = setup
    settings = replace(settings, execute=True, pause_for_faults=True, checkpoint_timeout=1)
    released = []
    def sleep(seconds):
        for checkpoint in settings.output.glob('*-checkpoint.json'):
            name = checkpoint.name.replace('-checkpoint.json', '')
            if name not in released:
                assert main(['continue-workflow', '--checkpoint', str(checkpoint)]) == 0
                released.append(name)
        clock.sleep(seconds)
    report = Runner(settings, workbench_transport=httpx.MockTransport(peer.workbench),
        agent_transport=httpx.MockTransport(peer.agent), monotonic=clock, sleep=sleep).run()
    assert report['status'] == 'passed', report
    assert released == ['before-svg', 'after-svg', 'before-revision']
    assert report['generation_posts'] == 4
    # Releasing workflow gates cannot by itself claim either fault accepted.
    assert report['submission_loss_recovery'] == report['running_cancellation'] == 'not_run'


def test_arm_command_binds_exact_svg_input_to_relay_run(setup, tmp_path):
    settings, peer, clock = setup
    settings = replace(settings, execute=True, pause_for_faults=True, checkpoint_timeout=1)
    report = Runner(settings, workbench_transport=httpx.MockTransport(peer.workbench),
        agent_transport=httpx.MockTransport(peer.agent), monotonic=clock, sleep=clock.sleep).run()
    relay_root = tmp_path / 'arm-case'
    relay_root.mkdir()
    relay, calls, clock = relay_fixture(relay_root)
    try:
        checkpoint = settings.output / 'before-svg-checkpoint.json'
        assert main(['arm-relay', '--proxy-dir', str(relay.output), '--checkpoint', str(checkpoint)]) == 0
        assert read_json(relay.output / 'arm.json')['input_sha256'] == digest(read_json(checkpoint)['document'])
        assert calls == []
    finally:
        relay.close()


class CancellationPeer(Peer):
    def __init__(self):
        super().__init__()
        self.pending, self.cancelled, self.behavior = True, False, None
        self.protected_id = str(UUID(int=42))
        self.protected = dict(id=self.protected_id, title='原项目', revision=11, scenes=[])
        self.reads = 0

    def workbench(self, request):
        if request.method == 'GET' and request.url.path == '/api/projects/' + self.protected_id:
            self.calls.append(request)
            return self.response(self.protected)
        if request.method == 'POST' and request.url.path.endswith('/cancel'):
            self.calls.append(request)
            assert request.headers['x-csrf-token'] == CSRF
            self.cancelled = True
            operation = self.project['scenes'][0]['operation']
            operation['status'] = 'cancelling'
            return self.response(operation)
        return super().workbench(request)

    def operation(self, path, body):
        value = super().operation(path, body)
        self.jobs[value['job_id']]['state'] = 'completed' if self.behavior == 'already_completed' else 'running'
        return value

    def agent(self, request):
        path = request.url.path
        if '/v1/jobs/' in path and path.endswith('/events'):
            self.agent_calls.append(request)
            kinds = ['job.starting', 'job.running'] + (['job.cancelling', 'job.cancelled'] if self.cancelled else [])
            if self.behavior == 'no_running_event': kinds = ['job.starting']
            cursor = int(request.url.params['after'])
            rows = [dict(seq=i, type=kind, created_at=1000 + i) for i, kind in enumerate(kinds, 1) if i > cursor]
            return self.response(dict(items=rows, next_cursor=len(kinds), has_more=False))
        if path.endswith('/artifact-manifest') and self.cancelled:
            if self.behavior == 'manifest_present': return self.response({'unexpected': True})
            self.agent_calls.append(request)
            return self.response({'detail': {'code': 'artifact_manifest_unavailable'}}, 409)
        if path.endswith('/artifacts') and self.cancelled:
            self.agent_calls.append(request)
            return self.response({'items': [{}] if self.behavior == 'artifacts_present' else []})
        if path.startswith('/v1/jobs/') and not path.endswith('/artifact-manifest'):
            self.reads += 1
            job = self.jobs[path.split('/')[3]]
            if self.behavior == 'completion_race' and self.reads >= 2:
                job['state'] = 'completed'
            if self.cancelled:
                job['state'] = 'quarantined' if self.behavior == 'quarantined' else 'cancelled'
                job['artifact_manifest_sha256'] = None
                job['not_started'] = self.behavior == 'not_started'
                job['stop_proof']['resource_ids'] = ['k8s:workers:' + WORKER_POD]
                if self.behavior == 'invalid_stop': job['stop_proof']['fence'] += 1
                self.project['scenes'][0]['operation']['status'] = job['state']
        return super().agent(request)


def cancellation_run(setup, *, behavior=None, observe=True, deleted=False):
    settings, _, clock = setup
    peer = CancellationPeer()
    peer.behavior = behavior
    def observer(**kwargs):
        terminated = 'stopped' in kwargs['output'].name
        attempt = kwargs['selector'].split('=', 1)[1]
        value = dict(schema_version='video-pod-observation.v1', namespace='workers', selector=kwargs['selector'],
            started_at=1001.0, observed_at=1002.0,
            items=[] if deleted and terminated else [pod(WORKER_POD, worker=True, terminated=terminated, attempt=attempt)])
        write_new(kwargs['output'], value)
        return value
    extra = dict(kubectl=Path('C:/tools/kubectl.exe'), kubeconfig=Path('C:/ops/kubeconfig'), worker_namespace='workers') if observe else {}
    runner = CancellationRunner(replace(settings, execute=True), protect_project_id=peer.protected_id,
        workbench_transport=httpx.MockTransport(peer.workbench), agent_transport=httpx.MockTransport(peer.agent),
        monotonic=clock, sleep=clock.sleep, observer=observer, **extra)
    return runner.run(), peer


def test_cancellation_has_exactly_one_generation_and_one_cancel_only_after_running(setup):
    report, peer = cancellation_run(setup)
    assert report['status'] == 'passed', report
    assert report['generation_posts'] == len(peer.paid) == report['cancellation_posts'] == 1
    assert report['running_cancellation'] == 'http_stop_confirmed'
    assert report['worker_pod_stop'] == 'confirmed'
    assert report['slot_release_time'] == 'not_directly_observed' and report['timeout_stop'] == 'not_run'
    assert peer.project['scenes'][0]['versions'] == []
    assert report['cost'] == 'unknown'
    assert not any(r.method != 'GET' for r in peer.agent_calls)
    manifest = read_json(setup[0].output / 'cancel-manifest-observation.json')
    assert manifest['available'] is False and manifest['http_status'] == 409
    assert TOKEN not in (setup[0].output / 'cancel-stopped-job.json').read_text()


@pytest.mark.parametrize('behavior,cancel_posts', [('already_completed', 0), ('completion_race', 0),
    ('no_running_event', 0), ('quarantined', 1), ('not_started', 1), ('invalid_stop', 1),
    ('manifest_present', 1), ('artifacts_present', 1)])
def test_cancellation_failures_never_retry_or_fake_a_stopped_process(setup, behavior, cancel_posts):
    report, peer = cancellation_run(setup, behavior=behavior)
    assert report['status'] == 'stopped', report
    assert report['generation_posts'] == len(peer.paid) == 1
    assert report['cancellation_posts'] == cancel_posts
    assert report['worker_pod_stop'] != 'confirmed'
    assert not any(r.url.path.endswith('/adopt') for r in peer.calls)


@pytest.mark.parametrize('missing', ['no_observer', 'deleted_pod'])
def test_http_cancellation_without_actual_pod_stop_stays_pending(setup, missing):
    report, peer = cancellation_run(setup, observe=missing != 'no_observer', deleted=missing == 'deleted_pod')
    assert report['status'] == 'pending_pod_evidence'
    assert report['running_cancellation'] == 'http_stop_confirmed'
    assert report['worker_pod_stop'] != 'confirmed'
