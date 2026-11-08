"""Real HTTP client/gateway protocol with synthetic Agent/render transports.

This does not execute a model or browser. The tests verify stopped artifact
custody, bounded task admission, concurrency and durable failure publication.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
from uuid import UUID

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ExecutionBindingPins, ExecutionEnvelope, InlineFile, ServiceError, WorkerResult, sign_receipt
from agent_service.deploy import candidate_scenario
from agent_service.profiles.video_animation_experiment import (
    PROFILE, RESULT_SCHEMA, RESULT_NAME, INPUT_NAME, VideoAnimationExperimentProfile)
from video_workbench import animation_experiment_render as media
from video_workbench import animation_experiment_runner as runner
from video_workbench import animation_experiments as lab


IMAGE = 'registry.example/renderer:latest@sha256:' + 'a' * 64
TOKEN = 'offline-animation-experiment-test-' + 'x' * 32


@pytest.fixture(autouse=True)
def synthetic_operator_source_owner(tmp_path, monkeypatch):
    # Linux CI runs unprivileged. Only synthetic test-tree evidence gets an
    # owner double; native link/mode checks and production readers are intact.
    native = type(Path())
    controlled = tmp_path.absolute()
    class SyntheticRootPath(native):
        def lstat(self, *args, **kwargs):
            observed = super().lstat(*args, **kwargs)
            try:
                self.relative_to(controlled)
            except ValueError:
                return observed
            class OwnerStat:
                st_uid = 0
                def __getattr__(self, name):
                    return getattr(observed, name)
            return OwnerStat()
    monkeypatch.setattr(candidate_scenario, 'Path', SyntheticRootPath)


class Peer:
    def __init__(self):
        self.calls, self.jobs, self.contents, self.manifests, self.key_jobs = [], {}, {}, {}, {}
        self.input = None
        self.lose_submission = False
        self.lookup_missing = False
        self.failure = None
        self.model_policy = None

    def __call__(self, request):
        self.calls.append(request)
        method, path = request.method, request.url.path
        if path == '/v1/capabilities':
            return httpx.Response(200, json=dict(profiles=[dict(profile=PROFILE,
                profile_revision='1', configured=True, ready=True, supports_inputs=True, max_timeout_seconds=600)]))
        if method == 'POST' and path == '/v1/inputs':
            self.input = json.loads(request.content)
            return httpx.Response(201, json=dict(input_id=str(UUID(int=10)), name=request.url.params['name'],
                size=len(request.content), sha256=lab.digest(request.content)))
        if method == 'POST' and path == '/v1/jobs':
            document = json.loads(request.content)
            job_id = str(UUID(int=100 + len(self.jobs)))
            attempt = str(UUID(int=1000 + len(self.jobs)))
            artifact = str(UUID(int=10000 + len(self.jobs)))
            result = dict(schema=RESULT_SCHEMA, input_sha256=lab.digest(self.input),
                prompt_revision=self.input['prompt_revision'], engine='html-svg',
                strategy=dict(description='Synthetic generation fixture', tradeoffs='No native rendering'),
                files={'scene.html': '<!doctype html><p data-object-id="A">A</p>'})
            result['files_sha256'] = {name: lab.digest(content.encode()) for name, content in result['files'].items()}
            if self.failure == 'wrong_input':
                result['input_sha256'] = '0' * 64
            elif self.failure == 'source_digest':
                result['files_sha256']['scene.html'] = '0' * 64
            raw = lab.canonical(result)
            manifest = frozen_manifest(job_id, attempt, [dict(artifact_id=artifact, name=RESULT_NAME,
                size=len(raw), sha256=lab.digest(raw))])
            job = dict(job_id=job_id, state='completed', business_ref=document['business_ref'],
                publication_authorized=False, artifact_manifest_sha256=manifest['sha256'],
                attempt=dict(attempt_id=attempt, fence=1), pins=dict(profile=PROFILE, profile_revision='1'),
                stop_proven=True, stop_proof=dict(stop_proven=True, attempt_id=attempt, fence=1,
                    request_sha256='d' * 64, result_sha256='e' * 64, resource_ids=['synthetic-writer'], observed_at=1000))
            if self.model_policy is not None:
                job['pins']['execution_binding'] = dict(model_policy_sha256=lab.digest(self.model_policy),
                    model_policy_revision=self.model_policy['revision'])
            if self.failure == 'writers_running':
                job['stop_proven'] = False
            elif self.failure == 'terminal':
                job['state'] = 'failed'
            self.contents[artifact], self.manifests[job_id], self.jobs[job_id] = raw, manifest, job
            self.key_jobs[request.headers['idempotency-key']] = job_id
            if self.lose_submission:
                raise httpx.ReadTimeout('Lost response after synthetic admission', request=request)
            return httpx.Response(202, json=job)
        if path.startswith('/v1/jobs/by-idempotency/'):
            key = path.rsplit('/', 1)[-1]
            if self.lookup_missing or key not in self.key_jobs:
                return httpx.Response(404, json={'detail': {'code': 'job_not_found'}})
            return httpx.Response(200, json=self.jobs[self.key_jobs[key]])
        if path.endswith('/artifact-manifest'):
            return httpx.Response(200, json=self.manifests[path.split('/')[-2]])
        if path.startswith('/v1/artifacts/'):
            return httpx.Response(200, content=self.contents[path.split('/')[-2]])
        if path.startswith('/v1/jobs/'):
            return httpx.Response(200, json=self.jobs[path.rsplit('/', 1)[-1]])
        raise AssertionError((method, path))

    def count(self, method, prefix):
        return sum(request.method == method and request.url.path.startswith(prefix) for request in self.calls)


@pytest.fixture
def wire(monkeypatch):
    peer, rendered = Peer(), []

    def fake_render(bundle, destination, **kwargs):
        # Synthetic bytes exercise publication custody only. They never stand
        # in for native renderer acceptance or human effect approval.
        rendered.append(dict(bundle=bundle, destination=destination, **kwargs))
        destination.mkdir()
        (destination / 'work').mkdir()
        (destination / 'work' / 'output.mp4').write_bytes(b'0000ftypSYNTHETIC-TEST-MEDIA')
        (destination / 'work' / 'frames').mkdir()
        for index in (0, 24, 72, 120, 156, 191):
            (destination / 'work' / 'frames' / ('frame-%06d.jpg' % index)).write_bytes(b'SYNTHETIC-JPEG-' + str(index).encode())
        return dict(schema='video.animation-experiment-render-outcome.v1', status='rendered',
            image=kwargs['image'], receipt=dict(host_sha256=lab.digest(media.HOST.read_bytes())),
            technical=dict(status='passed', scope='synthetic unit transport only'),
            cleanup=dict(status='removed_and_confirmed_absent'), visual_quality='not_evaluated')

    monkeypatch.setattr(media, 'render_scene', fake_render)
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        yield runner.ExperimentGateway(client), peer, rendered


def init(tmp_path):
    root = tmp_path / 'pilot'
    runner.initialize(root)
    return root


def run(root, slot, gateway):
    return runner.run(root, slot, gateway, renderer_image=IMAGE, poll_seconds=0)


def test_successful_real_client_protocol_publishes_bound_media_without_aesthetic_claim(tmp_path, wire):
    gateway, peer, rendered = wire
    root = init(tmp_path)
    row = run(root, 'development-v1', gateway)
    current = lab.read_run(root / row['id'])
    assert current == row and row['status'] == 'rendered'
    assert row['provenance']['stop_proven'] and row['provenance']['returned_model'] is None
    assert row['provenance']['artifact_manifest_sha256'] == peer.manifests[row['provenance']['job_id']]['sha256']
    assert len(rendered) == 1 and peer.count('POST', '/v1/jobs') == 1
    assert peer.count('POST', '/v1/inputs') == 1
    assert not (root / '.pilot.lock').exists()
    report = json.loads((root / row['id'] / 'report.json').read_bytes())
    assert report['aesthetic_status'] == 'unreviewed' and report['generation_attempts'] == 1
    assert len(report['published_frames']) == 6
    assert row['artifacts']['frames/000191.jpg']['sha256'] == lab.digest(b'SYNTHETIC-JPEG-191')


def test_lost_submission_response_performs_one_readonly_lookup_without_second_post(tmp_path, wire):
    gateway, peer, rendered = wire
    peer.lose_submission = True
    root = init(tmp_path)
    result = run(root, 'development-v1', gateway)
    assert result['status'] == 'rendered' and len(rendered) == 1
    assert peer.count('POST', '/v1/jobs') == 1
    assert peer.count('GET', '/v1/jobs/by-idempotency/') == 1
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(root, 'development-v1', gateway)
    assert peer.count('POST', '/v1/jobs') == 1


def test_unresolved_ambiguous_admission_is_retained_as_failure_without_paid_retry(tmp_path, wire):
    gateway, peer, rendered = wire
    peer.lose_submission = peer.lookup_missing = True
    root = init(tmp_path)
    with pytest.raises(ServiceError, match='job_not_found'):
        run(root, 'development-v1', gateway)
    plan = json.loads((root / 'pilot.json').read_bytes())
    assert plan['slots']['development-v1']['state'] == 'failed'
    row = lab.read_run(root / 'identity-development-v1')
    assert row['status'] == 'failed' and row['error'] == 'job_not_found'
    assert 'report.json' in row['artifacts'] and 'output.mp4' not in row['artifacts']
    assert peer.count('POST', '/v1/jobs') == 1 and len(rendered) == 0
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(root, 'development-v1', gateway)
    assert peer.count('POST', '/v1/jobs') == 1


@pytest.mark.parametrize('failure', ['writers_running', 'wrong_input', 'source_digest', 'terminal'])
def test_untrusted_proof_or_result_cannot_reach_renderer_and_keeps_failure(tmp_path, wire, failure):
    gateway, peer, rendered = wire
    peer.failure = failure
    root = init(tmp_path)
    with pytest.raises((ServiceError, lab.ExperimentError)):
        run(root, 'development-v1', gateway)
    row = lab.read_run(root / 'identity-development-v1')
    assert row['status'] == 'failed' and 'report.json' in row['artifacts']
    assert rendered == [] and peer.count('POST', '/v1/jobs') == 1
    assert (root / row['id'] / 'agent-job.json').exists()
    assert not (root / '.pilot.lock').exists()


def test_render_failure_keeps_generated_sources_and_original_outcome_for_inspection(tmp_path, wire, monkeypatch):
    gateway, peer, _ = wire
    root = init(tmp_path)
    outcome = dict(status='failed', error='animation_experiment_render_container_failed',
        cleanup=dict(status='removed_and_confirmed_absent'), technical=None)

    def failed(*args, **kwargs):
        raise media.RenderExecutionError(outcome)

    monkeypatch.setattr(media, 'render_scene', failed)
    with pytest.raises(media.RenderExecutionError):
        run(root, 'development-v1', gateway)
    row = lab.read_run(root / 'identity-development-v1')
    assert row['status'] == 'failed' and row['code_files']['scene.html']
    report = json.loads((root / row['id'] / 'report.json').read_bytes())
    assert report['renderer'] == outcome
    assert 'output.mp4' not in row['artifacts'] and peer.count('POST', '/v1/jobs') == 1


def test_held_out_released_after_development_and_v2_is_frozen_once(tmp_path, wire):
    gateway, peer, _ = wire
    root = init(tmp_path)
    with pytest.raises(lab.ExperimentError, match='holdout_not_released'):
        run(root, 'held-out-v1', gateway)
    with pytest.raises(lab.ExperimentError, match='revision_not_ready'):
        runner.revise(root, 'Avoid crossing', 'A is hard to follow')
    run(root, 'development-v1', gateway)
    runner.revise(root, 'Arrange distinct motion lanes.', 'A is hard to follow during crossing.')
    with pytest.raises(lab.ExperimentError, match='revision_not_ready'):
        runner.revise(root, 'Replace the frozen direction.', 'Second change must be refused.')
    with pytest.raises(lab.ExperimentError, match='holdout_not_released'):
        run(root, 'held-out-v1', gateway)
    run(root, 'development-v2', gateway)
    run(root, 'held-out-v1', gateway)
    result = run(root, 'held-out-v2', gateway)
    assert result['input_role'] == 'held_out'
    assert result['input_document']['guidance'] == 'Arrange distinct motion lanes.'
    assert peer.count('POST', '/v1/jobs') == 4
    for slot in runner.SLOTS:
        with pytest.raises(lab.ExperimentError, match='already_reserved'):
            run(root, slot, gateway)
    with pytest.raises(lab.ExperimentError, match='slot_invalid'):
        run(root, 'extra-fifth-task', gateway)
    assert peer.count('POST', '/v1/jobs') == 4


def test_existing_pilot_lock_rejects_execution_and_revision_without_mutation(tmp_path, wire):
    gateway, peer, _ = wire
    root = init(tmp_path)
    before = (root / 'pilot.json').read_bytes()
    (root / '.pilot.lock').write_text('other-process')
    with pytest.raises(ServiceError, match='pilot_busy'):
        run(root, 'development-v1', gateway)
    with pytest.raises(ServiceError, match='pilot_busy'):
        runner.revise(root, 'Direction', 'Feedback')
    assert (root / 'pilot.json').read_bytes() == before
    assert peer.calls == []
    assert (root / '.pilot.lock').read_text() == 'other-process'


def test_concurrent_executor_cannot_submit_second_task_for_same_slot(tmp_path, wire, monkeypatch):
    gateway, peer, _ = wire
    root = init(tmp_path)
    entered, release = Event(), Event()
    original = gateway.upload

    def blocking_upload(document):
        entered.set()
        assert release.wait(5), 'test executor was not released'
        return original(document)

    monkeypatch.setattr(gateway, 'upload', blocking_upload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run, root, 'development-v1', gateway)
        assert entered.wait(5)
        try:
            with pytest.raises(ServiceError, match='pilot_busy'):
                run(root, 'development-v1', gateway)
        finally:
            release.set()
        assert first.result(timeout=5)['status'] == 'rendered'
    assert peer.count('POST', '/v1/jobs') == 1


def test_pilot_plan_cannot_add_slots_or_change_held_out_constraints(tmp_path, wire):
    gateway, peer, _ = wire
    root = init(tmp_path)
    plan = json.loads((root / 'pilot.json').read_bytes())
    plan['constraints']['held_out']['objects'][0]['width'] += 1
    (root / 'pilot.json').write_bytes(lab.canonical(plan))
    with pytest.raises(lab.ExperimentError, match='plan_changed'):
        run(root, 'development-v1', gateway)
    assert peer.calls == []


def synthetic_binding(profile_name=PROFILE):
    return ExecutionBindingPins.create(binding_id='offline-animation-test', binding_revision='1',
        profile=profile_name, profile_revision='1', contract_sha256='b' * 64,
        bundle_name='animation-experiment', bundle_revision='animation-experiment-v1', bundle_sha256='c' * 64,
        tool_environment_name='read-write', tool_environment_revision='1', tool_environment_sha256='d' * 64,
        engine='pi', runtime_revision='offline-test', image_digest='sha256:' + 'e' * 64,
        adapter_revision='1', model_policy_revision='1', model_policy_sha256='f' * 64,
        acceptance_sha256='0' * 64)


def reserve(root, slot_name='development-v1'):
    # Explicitly synthetic build-source descriptors for offline unit custody.
    binding = root / '.synthetic-expected-binding.json'
    receipt = root / '.synthetic-build-receipt.json'
    binding.write_bytes(lab.canonical(synthetic_binding().model_dump(mode='json')))
    receipt.write_bytes(lab.canonical(dict(schema='animation-agent-overlay-build.v1',
        image='registry.invalid/agent-pi:latest@' + synthetic_binding().image_digest,
        new_files={'profiles/video_animation_experiment.py':
            lab.digest(runner.Path(runner.experiment_profile.__file__).read_bytes()),
            'resources/animation-experiment-v1/manifest.json': runner.experiment_profile.BUNDLE_MANIFEST_SHA256})))
    return runner.reserve_candidate(root, expected_binding_file=binding, build_receipt=receipt,
                                    slot_name=slot_name)


def synthetic_candidate(directory, document, change=None):
    """Captured-shape fixture only; neither a real candidate nor actual stop."""
    directory.mkdir()
    profile = VideoAnimationExperimentProfile()
    profile_name = 'video.scene.v1' if change == 'profile' else PROFILE
    raw_input = lab.canonical(document) + (b'\n' if change == 'input_bytes' else b'')
    input_hash = lab.digest(raw_input)
    binding = synthetic_binding(profile_name)
    limits = {name: getattr(profile.limits, name) for name in ('max_input_bytes', 'max_input_total_bytes',
        'max_input_files', 'max_artifact_bytes', 'max_artifact_file_bytes', 'max_artifact_files')}
    # Distinct input executions have distinct synthetic authority identities.
    identity = 2 if document == lab.pilot_input() else int(input_hash[:30], 16) + 10
    envelope = ExecutionEnvelope(job_id=str(UUID(int=identity)), attempt_id=str(UUID(int=identity + 1)), fence=1,
        engine=binding.engine, profile=profile_name, profile_revision='1', runtime_revision=binding.runtime_revision,
        image_digest=binding.image_digest, deadline_at=2000., lease_expires_at=1500.,
        task={'input_sha256': input_hash}, inputs=[InlineFile.from_bytes(INPUT_NAME, raw_input)],
        execution_binding=binding, task_schema=profile.task_schema, output_schema=profile.output_schema, **limits)
    result_json = dict(schema=RESULT_SCHEMA, input_sha256=input_hash, prompt_revision=document['prompt_revision'],
        engine='html-svg', strategy=dict(description='Synthetic candidate custody fixture.', tradeoffs='No visual evidence.'),
        files={'scene.html': '<!doctype html><p data-object-id="A">A</p>'})
    result_json['files_sha256'] = {name: lab.digest(value.encode()) for name, value in result_json['files'].items()}
    if change == 'source_digest':
        result_json['files_sha256']['scene.html'] = '0' * 64
    artifact = InlineFile.from_bytes('wrong-name.json' if change == 'artifact' else RESULT_NAME,
                                     lab.canonical(result_json))
    result = WorkerResult(state='completed', artifacts=[artifact])
    proof = sign_receipt(envelope, result, b'SYNTHETIC-KEY-FOR-OFFLINE-TESTS-ONLY',
                         ('pod:synthetic-pod-uid',), 1000.)
    result = result.model_copy(update={'stop_receipt': proof})
    serialized_result = result.model_dump(mode='json')
    evidence = dict(candidate_only=True, production_acceptance_created=False, production_routes_changed=False,
        result_state='completed', lease_expiry_rejected=True, candidate_result_sha256=lab.digest(serialized_result),
        request_sha256=envelope.request_sha256, job_id=envelope.job_id, attempt_id=envelope.attempt_id,
        profile=envelope.profile, engine=envelope.engine, image_digest=envelope.image_digest,
        execution_binding_sha256=binding.sha256, writer_resources=list(proof.resource_ids),
        writers_observed_stopped_at=proof.observed_at, harness_sha256='a' * 64,
        artifacts=[dict(name=artifact.name, sha256=artifact.sha256, bytes=len(artifact.content()))],
        artifact_count=1, pod_uid='synthetic-pod-uid')
    image = 'registry.invalid/agent-pi:latest@' + envelope.image_digest
    pod = dict(metadata=dict(uid='synthetic-pod-uid'), spec=dict(restartPolicy='Never',
        containers=[dict(name='worker', image=image)]), status=dict(phase='Succeeded',
        containerStatuses=[dict(name='worker', restartCount=0, imageID='docker-pullable://' + image,
            state=dict(terminated=dict(exitCode=0, finishedAt=datetime.fromtimestamp(1001, timezone.utc).isoformat())))]))
    if change == 'running':
        pod['status']['containerStatuses'][0]['state'] = {'running': {}}
    elif change == 'restart':
        pod['status']['containerStatuses'][0]['restartCount'] = 1
    elif change == 'image':
        pod['spec']['containers'][0]['image'] = 'registry.invalid/changed:latest@sha256:' + '1' * 64
    elif change == 'uid':
        pod['metadata']['uid'] = 'another-pod'
    elif change == 'evidence_digest':
        evidence['candidate_result_sha256'] = '0' * 64
    documents = {'candidate-request.json': dict(schema_version='agent-candidate-request.v1',
        candidate_scope_only=True, envelope=envelope.model_dump(mode='json')),
        'candidate-result.json': dict(result=serialized_result, observations=dict(harness_sha256='a' * 64)),
        'candidate-evidence.json': evidence, 'worker-pod.json': pod}
    for name, value in documents.items():
        (directory / name).write_bytes(lab.canonical(value))
    return documents


def test_candidate_reservation_survives_commands_without_model_or_published_result(tmp_path, wire):
    gateway, peer, rendered = wire
    root = init(tmp_path)
    reservation = reserve(root)
    _, plan = runner._load(root)
    slot = plan['slots']['development-v1']
    assert slot['state'] == 'candidate_reserved' and peer.calls == [] and rendered == []
    assert lab.digest((root / slot['run_id'] / runner.RESERVATION_NAME).read_bytes()) == slot['candidate_reservation_sha256']
    assert json.loads((root / slot['run_id'] / INPUT_NAME).read_bytes()) == lab.pilot_input()
    assert reservation['input_path'] == str(root / slot['run_id'] / INPUT_NAME)
    assert not (root / slot['run_id'] / 'experiment-run.json').exists()
    assert not (root / '.pilot.lock').exists()
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        reserve(root)
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(root, 'development-v1', gateway)
    assert peer.calls == []


def test_complete_candidate_reuses_reserved_first_attempt_then_only_three_normal_admissions(tmp_path, wire):
    gateway, peer, rendered = wire
    root = init(tmp_path)
    reserve(root)
    candidate = tmp_path / 'synthetic-candidate'
    documents = synthetic_candidate(candidate, lab.pilot_input())
    row = runner.complete_candidate(root, candidate, renderer_image=IMAGE)
    assert row == lab.read_run(root / row['id']) and row['status'] == 'rendered'
    assert row['source'] == 'agent' and len(rendered) == 1 and peer.calls == []
    assert row['provenance']['job_id'] == str(UUID(int=2)) and row['provenance']['stop_proven']
    assert row['provenance']['returned_model'] is None
    assert row['provenance']['candidate_verification'].endswith('no_offline_hmac')
    for name, document in documents.items():
        assert (root / row['id'] / 'candidate-evidence' / name).read_bytes() == lab.canonical(document)
    with pytest.raises(lab.ExperimentError, match='candidate_not_reserved'):
        runner.complete_candidate(root, candidate, renderer_image=IMAGE)
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(root, 'development-v1', gateway)
    runner.revise(root, 'Use separated motion lanes.', 'Synthetic direction only, no user approval.')
    for slot in runner.SLOTS[1:]:
        run(root, slot, gateway)
    assert peer.count('POST', '/v1/jobs') == 3
    assert all(slot['state'] == 'finished' for slot in runner._load(root)[1]['slots'].values())


@pytest.mark.parametrize('change', ['input_bytes', 'profile', 'artifact', 'source_digest', 'running',
                                  'restart', 'image', 'uid', 'evidence_digest'])
def test_candidate_binding_and_actual_pod_failures_consume_slot_without_task_or_renderer(tmp_path, wire, change):
    gateway, peer, rendered = wire
    root = init(tmp_path)
    reserve(root)
    candidate = tmp_path / 'synthetic-candidate'
    synthetic_candidate(candidate, lab.pilot_input(), change)
    with pytest.raises((ServiceError, lab.ExperimentError)):
        runner.complete_candidate(root, candidate, renderer_image=IMAGE)
    row = lab.read_run(root / 'identity-development-v1')
    assert row['status'] == 'failed' and 'report.json' in row['artifacts']
    assert runner._load(root)[1]['slots']['development-v1']['state'] == 'failed'
    assert rendered == [] and peer.calls == []
    assert (root / row['id'] / 'candidate-evidence' / 'worker-pod.json').exists()
    assert not (root / '.pilot.lock').exists()
    with pytest.raises(lab.ExperimentError, match='candidate_not_reserved'):
        runner.complete_candidate(root, candidate, renderer_image=IMAGE)
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(root, 'development-v1', gateway)


@pytest.mark.parametrize('filename', [runner.RESERVATION_NAME, INPUT_NAME,
                                    runner.EXPECTED_BINDING_NAME, runner.BUILD_RECEIPT_NAME])
def test_changed_candidate_reservation_or_input_is_failed_durably(tmp_path, wire, filename):
    _, peer, rendered = wire
    root = init(tmp_path)
    reserve(root)
    path = root / 'identity-development-v1' / filename
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(lab.ExperimentError):
        runner.complete_candidate(root, tmp_path / 'not-read', renderer_image=IMAGE)
    row = lab.read_run(root / 'identity-development-v1')
    assert row['status'] == 'failed' and peer.calls == [] and rendered == []


def test_candidate_render_failure_keeps_exact_sources_and_outcome_without_retry(tmp_path, wire, monkeypatch):
    _, peer, _ = wire
    root = init(tmp_path)
    reserve(root)
    candidate = tmp_path / 'synthetic-candidate'
    synthetic_candidate(candidate, lab.pilot_input())
    outcome = dict(status='failed', error='animation_experiment_render_container_failed',
                   cleanup=dict(status='removed_and_confirmed_absent'), technical=None)
    def failed(*args, **kwargs):
        raise media.RenderExecutionError(outcome)
    monkeypatch.setattr(media, 'render_scene', failed)
    with pytest.raises(media.RenderExecutionError):
        runner.complete_candidate(root, candidate, renderer_image=IMAGE)
    row = lab.read_run(root / 'identity-development-v1')
    report = json.loads((root / row['id'] / 'report.json').read_bytes())
    assert row['code_files']['scene.html'] and report['renderer'] == outcome and peer.calls == []
    with pytest.raises(lab.ExperimentError, match='candidate_not_reserved'):
        runner.complete_candidate(root, candidate, renderer_image=IMAGE)


def test_technical_goal_failure_keeps_rendered_media_and_frames_inspectable(tmp_path, wire, monkeypatch):
    gateway, _, _ = wire
    root = init(tmp_path)
    original = media.render_scene
    def technically_failed(*args, **kwargs):
        outcome = original(*args, **kwargs)
        outcome['technical'] = dict(status='failed', issues=['synthetic-goal-mismatch'])
        return outcome
    monkeypatch.setattr(media, 'render_scene', technically_failed)
    row = run(root, 'development-v1', gateway)
    assert row['status'] == 'rendered' and 'output.mp4' in row['artifacts']
    assert 'frames/000191.jpg' in row['artifacts']
    report = json.loads((root / row['id'] / 'report.json').read_bytes())
    assert report['renderer']['technical']['status'] == 'failed'


def test_existing_lock_guards_candidate_commands_before_reservation_or_import(tmp_path, wire):
    _, peer, rendered = wire
    root = init(tmp_path)
    (root / '.pilot.lock').write_text('other-process')
    with pytest.raises(ServiceError, match='pilot_busy'):
        runner.reserve_candidate(root)
    with pytest.raises(ServiceError, match='pilot_busy'):
        runner.complete_candidate(root, tmp_path / 'unused', renderer_image=IMAGE)
    assert runner._load(root)[1]['slots']['development-v1']['state'] == 'unstarted'
    assert peer.calls == [] and rendered == []


def test_declared_model_policy_is_bound_to_actual_job_pins_without_claiming_returned_model(tmp_path, wire):
    gateway, peer, _ = wire
    root = init(tmp_path)
    policy = dict(revision='offline-policy-1', api_mode='openai-responses', provider='synthetic',
                  model='declared-offline-model', capabilities=['text', 'tool_calls'])
    path = tmp_path / 'synthetic-public-model-policy.json'
    path.write_bytes(lab.canonical(policy))
    peer.model_policy = policy
    row = runner.run(root, 'development-v1', gateway, renderer_image=IMAGE, model_policy_file=path)
    assert row['provenance']['declared_model_policy'] == policy
    assert row['provenance']['requested_model'] == policy['model']
    assert row['provenance']['returned_model'] is None


def test_declared_model_policy_mismatch_preserves_failed_paid_attempt_without_render(tmp_path, wire):
    gateway, peer, rendered = wire
    root = init(tmp_path)
    policy = dict(revision='offline-policy-1', api_mode='openai-responses', provider='synthetic',
                  model='declared-offline-model', capabilities=['text', 'tool_calls'])
    path = tmp_path / 'synthetic-public-model-policy.json'
    path.write_bytes(lab.canonical(policy))
    peer.model_policy = dict(policy, model='different-model')
    with pytest.raises(lab.ExperimentError, match='model_policy_binding_mismatch'):
        runner.run(root, 'development-v1', gateway, renderer_image=IMAGE, model_policy_file=path)
    assert lab.read_run(root / 'identity-development-v1')['status'] == 'failed'
    assert peer.count('POST', '/v1/jobs') == 1 and rendered == []


def test_changed_expected_build_source_is_refused_before_candidate_reservation(tmp_path, wire):
    _, peer, rendered = wire
    root = init(tmp_path)
    binding = tmp_path / 'synthetic-binding.json'
    receipt = tmp_path / 'synthetic-build-receipt.json'
    binding.write_bytes(lab.canonical(synthetic_binding().model_dump(mode='json')))
    receipt.write_bytes(lab.canonical(dict(schema='animation-agent-overlay-build.v1',
        image='registry.invalid/agent-pi:latest@' + synthetic_binding().image_digest,
        new_files={'profiles/video_animation_experiment.py': '0' * 64,
            'resources/animation-experiment-v1/manifest.json': runner.experiment_profile.BUNDLE_MANIFEST_SHA256})))
    with pytest.raises(lab.ExperimentError, match='candidate_build_mismatch'):
        runner.reserve_candidate(root, expected_binding_file=binding, build_receipt=receipt)
    assert runner._load(root)[1]['slots']['development-v1']['state'] == 'unstarted'
    assert peer.calls == [] and rendered == []


def synthetic_failure(directory, document):
    """Root-capture shapes for an offline precreative failure, never real proof."""
    docs = synthetic_candidate(directory, document)
    for name in ('candidate-result.json', 'candidate-evidence.json'):
        (directory / name).unlink()
    request = docs['candidate-request.json']
    envelope = ExecutionEnvelope.model_validate(request['envelope'])
    worker = deepcopy(docs['worker-pod.json'])
    worker['metadata'] = dict(name='candidate-animation-exp-v1-worker', namespace='agent-workers', uid=str(UUID(int=4)))
    worker['status']['phase'] = 'Failed'
    worker['status']['containerStatuses'][0]['state']['terminated']['exitCode'] = 1
    proxy = deepcopy(worker)
    proxy['metadata'] = dict(name='candidate-animation-exp-v1-proxy', namespace='agent-service', uid=str(UUID(int=5)))
    proxy['status']['containerStatuses'][0]['state']['terminated']['exitCode'] = 143
    name = 'candidate-animation-exp-v1'
    resources = [('ConfigMap', 'agent-service', name), ('ConfigMap', 'agent-workers', name),
        ('Service', 'agent-service', name), ('NetworkPolicy', 'agent-service', name),
        ('Pod', 'agent-service', name + '-proxy'), ('Pod', 'agent-workers', name + '-worker'),
        ('Secret', 'agent-service', name + '-bootstrap'), ('Secret', 'agent-workers', name + '-bootstrap'),
        ('Secret', 'agent-service', name + '-upstream')]
    diagnosis = dict(status='failed_before_agent_start', code='package_resources_incomplete',
        worker_pod_uid=worker['metadata']['uid'], proxy_pod_uid=proxy['metadata']['uid'],
        request_sha256=envelope.request_sha256, attempt_id=envelope.attempt_id,
        worker_stopped=True, worker_restarts=0, agent_generation_started=False,
        only_expected_connectivity_request=True, model_request_count=1,
        model_calls=[dict(attempt_id=envelope.attempt_id, body_sha256='f' * 64, state='complete', status=200,
                         response_sha256='d' * 64, response_bytes=100)],
        retry_performed=False, pilot_slot_consumed=True, animation_result_created=False)
    cleanup = dict(status='failed_candidate_closed', worker_stopped=True, proxy_stopped=True,
        model_request_count=1, creative_agent_started=False, animation_result_created=False,
        retry_performed=False, slot_still_consumed=True, uid_reservation_retained=True,
        exact_resources_absent=[dict(kind=k, namespace=ns, name=n) for k, ns, n in resources],
        production_routes_changed=False)
    failure = dict(zip(runner.FAILURE_FILES, (request, diagnosis, cleanup, worker, proxy)))
    for filename, value in failure.items():
        (directory / filename).write_bytes(lab.canonical(value))
    return failure


def closed_failure(tmp_path):
    root = init(tmp_path)
    reserve(root)
    evidence = tmp_path / 'synthetic-failure-evidence'
    synthetic_failure(evidence, lab.pilot_input())
    row = runner.close_failed_candidate(root, evidence)
    return root, evidence, row


def test_explicit_failed_candidate_close_preserves_consumed_original_without_fake_result(tmp_path, wire):
    gateway, peer, rendered = wire
    root = init(tmp_path)
    reserve(root)
    reserved = (root / 'identity-development-v1' / runner.RESERVATION_NAME).read_bytes()
    evidence = tmp_path / 'synthetic-failure-evidence'
    documents = synthetic_failure(evidence, lab.pilot_input())
    row = runner.close_failed_candidate(root, evidence)
    run_dir = root / row['id']
    assert lab.read_run(run_dir) == row and row['status'] == 'failed'
    assert row['error'] == 'package_resources_incomplete'
    assert row['code_files'] == row['strategy'] == {} and set(row['artifacts']) == {'report.json'}
    assert row['provenance']['creative_agent_starts'] == 0
    assert row['provenance']['connectivity_model_requests'] == 1
    assert row['provenance']['stop_proven'] is False and row['provenance']['returned_model'] is None
    assert row['provenance']['worker_and_proxy_observed_stopped'] is True
    assert (run_dir / runner.RESERVATION_NAME).read_bytes() == reserved
    for filename, value in documents.items():
        assert (run_dir / 'failure-evidence' / filename).read_bytes() == lab.canonical(value)
    report = json.loads((run_dir / 'report.json').read_bytes())
    assert report['generation_attempts'] == 0 and report['candidate_launch_attempts'] == 1
    assert report['renderer'] == {'status': 'not_started'}
    assert runner._load(root)[1]['slots']['development-v1']['state'] == 'failed'
    assert peer.calls == [] and rendered == []
    with pytest.raises(lab.ExperimentError, match='failure_close_not_ready'):
        runner.close_failed_candidate(root, evidence)
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(root, 'development-v1', gateway)
    with pytest.raises(lab.ExperimentError, match='candidate_not_reserved'):
        runner.complete_candidate(root, evidence, renderer_image=IMAGE)
    with pytest.raises(lab.ExperimentError, match='revision_not_ready'):
        runner.revise(root, 'A new direction', 'No successful baseline exists.')


@pytest.mark.parametrize('change', ['request', 'uncertain_model', 'cleanup_count', 'resource_missing',
    'worker_running', 'worker_image', 'worker_restart', 'proxy_running', 'output_present'])
def test_failed_candidate_closure_rejects_unbound_or_incomplete_proof_without_releasing_slot(tmp_path, wire, change):
    gateway, peer, rendered = wire
    root = init(tmp_path)
    reserve(root)
    evidence = tmp_path / 'synthetic-failure-evidence'
    docs = synthetic_failure(evidence, lab.pilot_input())
    if change == 'request':
        docs['failure-diagnosis.json']['request_sha256'] = '0' * 64
    elif change == 'uncertain_model':
        docs['failure-diagnosis.json']['model_calls'][0]['state'] = 'admitted'
    elif change == 'cleanup_count':
        docs['failure-cleanup-summary.json']['model_request_count'] = 0
    elif change == 'resource_missing':
        docs['failure-cleanup-summary.json']['exact_resources_absent'].pop()
    elif change == 'worker_running':
        docs['failure-worker-pod.json']['status']['containerStatuses'][0]['state'] = {'running': {}}
    elif change == 'worker_image':
        docs['failure-worker-pod.json']['spec']['containers'][0]['image'] = 'different:latest@sha256:' + '1' * 64
    elif change == 'worker_restart':
        docs['failure-worker-pod.json']['status']['containerStatuses'][0]['restartCount'] = 1
    elif change == 'proxy_running':
        docs['failure-proxy-stopped.json']['status']['containerStatuses'][0]['state'] = {'running': {}}
    else:
        (evidence / 'candidate-result.json').write_text('{}')
    for filename, value in docs.items():
        (evidence / filename).write_bytes(lab.canonical(value))
    with pytest.raises((lab.ExperimentError, ServiceError)):
        runner.close_failed_candidate(root, evidence)
    assert runner._load(root)[1]['slots']['development-v1']['state'] == 'candidate_reserved'
    assert not (root / 'identity-development-v1' / 'experiment-run.json').exists()
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(root, 'development-v1', gateway)
    with pytest.raises(lab.ExperimentError, match='recovery_not_ready'):
        runner.create_recovery(root, tmp_path / 'recovered', authorization_note='Explicit new four-task budget')
    assert peer.calls == [] and rendered == []


def test_interrupted_failure_close_resumes_same_immutable_evidence_without_model_retry(tmp_path, wire, monkeypatch):
    _, peer, rendered = wire
    root = init(tmp_path)
    reserve(root)
    evidence = tmp_path / 'synthetic-failure-evidence'
    synthetic_failure(evidence, lab.pilot_input())
    original = runner._immutable_bytes
    interrupted = False
    def one_interruption(path, raw):
        nonlocal interrupted
        if path.name == 'failure-diagnosis.json' and not interrupted:
            interrupted = True
            raise OSError('Synthetic interrupted evidence copy')
        return original(path, raw)
    monkeypatch.setattr(runner, '_immutable_bytes', one_interruption)
    with pytest.raises(OSError):
        runner.close_failed_candidate(root, evidence)
    intent = deepcopy(runner._load(root)[1]['slots']['development-v1']['failure_close_intent'])
    assert runner._load(root)[1]['slots']['development-v1']['state'] == 'candidate_closing'
    row = runner.close_failed_candidate(root, evidence)
    closure = json.loads((root / row['id'] / runner.FAILURE_CLOSURE_NAME).read_bytes())
    assert closure['closure_id'] == intent['closure_id'] and closure['created_at'] == intent['created_at']
    assert row['status'] == 'failed' and peer.calls == [] and rendered == []


def test_explicit_recovery_declares_four_new_slots_retains_parent_and_uses_unique_ids(tmp_path, wire):
    gateway, peer, rendered = wire
    root, _, failed = closed_failure(tmp_path)
    original = (root / failed['id'] / 'experiment-run.json').read_bytes()
    target = tmp_path / 'recovered'
    plan = runner.create_recovery(root, target, authorization_note='User explicitly continued; four new bounded tasks.')
    origin = plan['recovery']
    assert origin['parent_run_identity'] == failed['identity']
    assert origin['parent_attempt_id'] == failed['provenance']['attempt']['attempt_id']
    assert origin['cumulative_candidate_budget'] == 5 and origin['original_unstarted_slots_abandoned'] == 3
    assert origin['new_budget']['max_tasks'] == 4 and origin['new_budget']['max_attempts_per_task'] == 1
    assert origin['new_budget']['automatic_retry'] is False
    assert (root / failed['id'] / 'experiment-run.json').read_bytes() == original
    assert all(row['state'] == 'unstarted' for row in plan['slots'].values())
    assert set(row['run_id'] for row in plan['slots'].values()).isdisjoint(
        row['run_id'] for row in runner._load(root)[1]['slots'].values())
    assert runner._load(target)[1] == plan and peer.calls == [] and rendered == []
    for name, expected in origin['parent_snapshots_sha256'].items():
        assert lab.digest((target / name).read_bytes()) == expected
    first = run(target, 'development-v1', gateway)
    runner.revise(target, 'Distinct motion lanes.', 'Synthetic operator direction, not aesthetic approval.')
    for name in runner.SLOTS[1:]:
        row = run(target, name, gateway)
        assert row['provenance']['recovery']['identity'] == origin['identity']
        assert type(row['provenance']['created_at']) is float
        assert row['input_role'] == ('held_out' if name.startswith('held-out') else 'development')
    assert first['provenance']['recovery'] == origin and peer.count('POST', '/v1/jobs') == 4
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        run(target, 'development-v1', gateway)
    with pytest.raises(lab.ExperimentError, match='pilot_superseded'):
        run(root, 'development-v2', gateway)
    # Re-reading the explicit recovery cannot create another budget or reset any task.
    repeated = runner.create_recovery(root, target, authorization_note=origin['authorization_note'])
    assert all(row['state'] == 'finished' for row in repeated['slots'].values())
    with pytest.raises(lab.ExperimentError, match='recovery_already_reserved'):
        runner.create_recovery(root, tmp_path / 'another', authorization_note=origin['authorization_note'])
    assert peer.count('POST', '/v1/jobs') == 4


@pytest.mark.parametrize('bad', ['budget', 'empty_note', 'unclosed', 'nested_directory'])
def test_recovery_requires_closed_parent_explicit_note_and_exact_new_budget(tmp_path, wire, bad):
    _, peer, rendered = wire
    if bad == 'unclosed':
        root = init(tmp_path)
        reserve(root)
    else:
        root, _, _ = closed_failure(tmp_path)
    target = root / 'nested' if bad == 'nested_directory' else tmp_path / 'recovered'
    with pytest.raises(lab.ExperimentError):
        runner.create_recovery(root, target, authorization_note='' if bad == 'empty_note' else 'Explicit continuation',
                               max_tasks=5 if bad == 'budget' else 4)
    assert not target.exists() and 'recovery_child' not in runner._load(root)[1]
    assert peer.calls == [] and rendered == []


@pytest.mark.parametrize('filename', [runner.RECOVERY_ORIGIN_NAME, 'recovery-parent-report.json'])
def test_recovery_origin_or_parent_snapshot_tamper_blocks_generation_before_admission(tmp_path, wire, filename):
    gateway, peer, rendered = wire
    root, _, _ = closed_failure(tmp_path)
    target = tmp_path / 'recovered'
    runner.create_recovery(root, target, authorization_note='Explicit four-task continuation')
    path = target / filename
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(lab.ExperimentError, match='recovery_changed'):
        run(target, 'development-v1', gateway)
    assert peer.calls == [] and rendered == []


def test_recovery_initialization_resumes_reserved_same_successor_without_new_budget(tmp_path, wire, monkeypatch):
    _, peer, rendered = wire
    root, _, _ = closed_failure(tmp_path)
    target = tmp_path / 'recovered'
    original = runner._write
    def interrupted(path, document, **kwargs):
        if path == target / 'pilot.json':
            raise OSError('Synthetic initialization interruption')
        return original(path, document, **kwargs)
    monkeypatch.setattr(runner, '_write', interrupted)
    with pytest.raises(OSError):
        runner.create_recovery(root, target, authorization_note='Explicit continuation')
    marker = runner._load(root)[1]['recovery_child']
    assert marker['state'] == 'reserved' and not (target / 'pilot.json').exists()
    monkeypatch.setattr(runner, '_write', original)
    recovered = runner.create_recovery(root, target, authorization_note='Explicit continuation')
    assert recovered['recovery']['identity'] == marker['origin']['identity']
    assert runner._load(root)[1]['recovery_child']['state'] == 'initialized'
    assert peer.calls == [] and rendered == []


def isolated_slot(root, temporary, slot_name):
    reservation = reserve(root, slot_name)
    document = json.loads(Path(reservation['input_path']).read_bytes())
    evidence = temporary / ('isolated-' + slot_name)
    synthetic_candidate(evidence, document)
    return runner.complete_candidate(root, evidence, slot_name=slot_name, renderer_image=IMAGE)


def test_four_explicit_isolated_slots_share_budget_frozen_environment_and_no_gateway_launch(tmp_path, wire):
    _, peer, rendered = wire
    root = init(tmp_path)
    first = isolated_slot(root, tmp_path, 'development-v1')
    guidance = 'Preserve distinct identities through continuous curved movement.'
    runner.revise(root, guidance, 'Synthetic engineering observation only; no aesthetic approval.')
    rows = [first]
    for slot_name in runner.SLOTS[1:]:
        row = isolated_slot(root, tmp_path, slot_name)
        rows.append(row)
        assert row['input_document']['guidance'] == (guidance if slot_name.endswith('v2') else '')
        assert row['input_document']['constraints'] == lab.constraints(row['input_role'])
        assert row['provenance']['admission'] == 'explicit_isolated_pilot_execution_not_shared_route_admission'
        reference = row['provenance']['comparison_baseline']
        assert reference['run_identity'] == first['identity']
        assert reference['renderer_image'] == IMAGE
        assert reference['assets'] == {} and reference['declared_model_policy'] is None
        assert reference['renderer_host_sha256'] == lab.digest(media.HOST.read_bytes())
        assert row['provenance']['returned_model'] is None
    assert len({row['provenance']['attempt']['attempt_id'] for row in rows}) == 4
    assert len(rendered) == 4 and peer.calls == []
    assert all(slot['state'] == 'finished' for slot in runner._load(root)[1]['slots'].values())
    for slot_name in runner.SLOTS:
        with pytest.raises(lab.ExperimentError, match='already_reserved'):
            reserve(root, slot_name)
        with pytest.raises(lab.ExperimentError, match='candidate_not_reserved'):
            runner.complete_candidate(root, tmp_path / 'unused', slot_name=slot_name, renderer_image=IMAGE)
    with pytest.raises(lab.ExperimentError, match='slot_invalid'):
        reserve(root, 'fifth-task')
    assert len(rendered) == 4 and peer.calls == []


@pytest.mark.parametrize('slot_name,code', [
    ('development-v2', 'revision_not_ready'), ('held-out-v1', 'holdout_not_released'),
    ('held-out-v2', 'revision_not_ready'), ('extra', 'slot_invalid')])
def test_isolated_candidate_order_rejected_before_any_slot_or_input_write(tmp_path, wire, slot_name, code):
    _, peer, rendered = wire
    root = init(tmp_path)
    before = (root / 'pilot.json').read_bytes()
    with pytest.raises(lab.ExperimentError, match=code):
        reserve(root, slot_name)
    assert (root / 'pilot.json').read_bytes() == before
    assert all(not (root / row['run_id']).exists() for row in runner._load(root)[1]['slots'].values())
    assert peer.calls == [] and rendered == []


def test_isolated_heldout_waits_for_completed_development_v2_and_frozen_guidance(tmp_path, wire):
    _, peer, rendered = wire
    root = init(tmp_path)
    isolated_slot(root, tmp_path, 'development-v1')
    runner.revise(root, 'Continuous curved choreography.', 'Synthetic direction only.')
    with pytest.raises(lab.ExperimentError, match='holdout_not_released'):
        reserve(root, 'held-out-v1')
    isolated_slot(root, tmp_path, 'development-v2')
    plan = runner._load(root)[1]
    plan['guidance']['v2'] = 'A new direction informed by heldout would contaminate the comparison.'
    (root / 'pilot.json').write_bytes(lab.canonical(plan))
    with pytest.raises(lab.ExperimentError, match='frozen_guidance_changed'):
        reserve(root, 'held-out-v1')
    with pytest.raises(lab.ExperimentError, match='frozen_guidance_changed'):
        reserve(root, 'held-out-v2')
    assert len(rendered) == 2 and peer.calls == []
    assert runner._load(root)[1]['slots']['held-out-v1']['state'] == 'unstarted'


@pytest.mark.parametrize('change', ['receipt', 'binding'])
def test_subsequent_isolated_candidate_cannot_change_reviewed_build_environment(tmp_path, wire, change):
    _, peer, rendered = wire
    root = init(tmp_path)
    isolated_slot(root, tmp_path, 'development-v1')
    runner.revise(root, 'Continuous motion.', 'Synthetic engineering observation.')
    binding = root / '.synthetic-expected-binding.json'
    receipt = root / '.synthetic-build-receipt.json'
    changed = binding if change == 'binding' else receipt
    # JSON whitespace does not change decoded policy, but this path requires the
    # exact reviewed receipt/binding bytes, not an equivalent unreviewed copy.
    changed.write_bytes(changed.read_bytes() + b'\n')
    before = (root / 'pilot.json').read_bytes()
    with pytest.raises(lab.ExperimentError, match='comparison_environment_changed'):
        runner.reserve_candidate(root, slot_name='development-v2', expected_binding_file=binding,
                                 build_receipt=receipt)
    assert (root / 'pilot.json').read_bytes() == before
    assert len(rendered) == 1 and peer.calls == []


@pytest.mark.parametrize('change', ['renderer', 'host', 'input', 'guidance', 'envelope'])
def test_isolated_import_rejects_drift_and_preserves_consumed_slot(tmp_path, wire, monkeypatch, change):
    _, peer, rendered = wire
    root = init(tmp_path)
    isolated_slot(root, tmp_path, 'development-v1')
    runner.revise(root, 'Continuous motion.', 'Synthetic engineering observation.')
    reservation = reserve(root, 'development-v2')
    document = json.loads(Path(reservation['input_path']).read_bytes())
    candidate = tmp_path / 'changed-candidate'
    synthetic_candidate(candidate, lab.pilot_input() if change == 'envelope' else document)
    renderer_image = IMAGE
    if change == 'renderer':
        renderer_image = IMAGE[:-1] + 'b'
    elif change == 'host':
        host = tmp_path / 'different-host.mjs'
        host.write_bytes(media.HOST.read_bytes() + b'\n// synthetic changed host\n')
        monkeypatch.setattr(media, 'HOST', host)
    elif change == 'input':
        Path(reservation['input_path']).write_bytes(lab.canonical(document) + b'\n')
    elif change == 'guidance':
        plan = runner._load(root)[1]
        plan['guidance']['v2'] += ' Changed after reservation.'
        (root / 'pilot.json').write_bytes(lab.canonical(plan))
    with pytest.raises(lab.ExperimentError):
        runner.complete_candidate(root, candidate, slot_name='development-v2', renderer_image=renderer_image)
    row = lab.read_run(root / 'identity-development-v2')
    assert row['status'] == 'failed' and 'output.mp4' not in row['artifacts']
    assert runner._load(root)[1]['slots']['development-v2']['state'] == 'failed'
    assert len(rendered) == 1 and peer.calls == []
    with pytest.raises(lab.ExperimentError, match='already_reserved'):
        reserve(root, 'development-v2')


def test_named_isolated_reservations_cannot_extend_superseded_original_pilot(tmp_path, wire):
    _, peer, rendered = wire
    root, _, failed = closed_failure(tmp_path)
    before = (root / failed['id'] / 'experiment-run.json').read_bytes()
    recovered = tmp_path / 'recovered'
    plan = runner.create_recovery(root, recovered, authorization_note='Explicit bounded continuation.')
    for slot_name in runner.SLOTS:
        with pytest.raises(lab.ExperimentError, match='pilot_superseded'):
            reserve(root, slot_name)
    assert (root / failed['id'] / 'experiment-run.json').read_bytes() == before
    first = isolated_slot(recovered, tmp_path, 'development-v1')
    assert first['provenance']['recovery']['identity'] == plan['recovery']['identity']
    assert first['id'] != failed['id'] and first['provenance']['recovery']['cumulative_candidate_budget'] == 5
    assert peer.calls == [] and len(rendered) == 1
