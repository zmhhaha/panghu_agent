"""Real HTTP client/gateway protocol with synthetic Agent/render transports.

This does not execute a model or browser. The tests verify stopped artifact
custody, bounded task admission, concurrency and durable failure publication.
"""
from concurrent.futures import ThreadPoolExecutor
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


def reserve(root):
    # Explicitly synthetic build-source descriptors for offline unit custody.
    binding = root / '.synthetic-expected-binding.json'
    receipt = root / '.synthetic-build-receipt.json'
    binding.write_bytes(lab.canonical(synthetic_binding().model_dump(mode='json')))
    receipt.write_bytes(lab.canonical(dict(schema='animation-agent-overlay-build.v1',
        image='registry.invalid/agent-pi:latest@' + synthetic_binding().image_digest,
        new_files={'profiles/video_animation_experiment.py':
            lab.digest(runner.Path(runner.experiment_profile.__file__).read_bytes()),
            'resources/animation-experiment-v1/manifest.json': runner.experiment_profile.BUNDLE_MANIFEST_SHA256})))
    return runner.reserve_candidate(root, expected_binding_file=binding, build_receipt=receipt)


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
    envelope = ExecutionEnvelope(job_id=str(UUID(int=2)), attempt_id=str(UUID(int=3)), fence=1,
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
