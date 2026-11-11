"""Synthetic original-record fixtures test custody, never live admission/quality."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import time
from uuid import uuid4
import wave

import pytest

from agent_service.contracts import (ExecutionEnvelope, InlineFile, ServiceError,
    WorkerResult, canonical_bytes, digest, sign_receipt)
from agent_service.deploy import animation_production_acceptance as admission
from agent_service.deploy.acceptance_probe import candidate_config, harness_sha256, render_probe
from agent_service.deploy.candidate_scenario import module_sha256
from agent_service.profiles import video_animation_production as production
from video_workbench.creative_method_contract import build_scene_input, collect_scene_result
from video_workbench.creative_renderer import compile_creative_project
from video_workbench.motion_renderer.job import NAMESPACE, resources
from test_animation_production_catalog import prepare
from test_animation_review_catalog import IMAGE
from test_candidate_scenario import write_scenario, synthetic_source_root_owner


RENDER_IMAGE = 'registry.example/renderer:latest@sha256:' + '1' * 64
SOURCE = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if type(value) is bytes else canonical_bytes(value))


def edit(root, name, change):
    value = read(root / name); change(value); save(root / name, value)


@pytest.fixture
def capture(tmp_path, monkeypatch):
    # Test helpers live in the sibling business-test directory, not production.
    monkeypatch.syspath_prepend(str(SOURCE / 'tests/video_workbench'))
    from test_creative_method_contract import core_task, delivery, production_result, published_method
    from test_motion_snapshot import RUNTIME
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\0\0' * 48000)
    audio_raw = audio.getvalue()
    core = core_task(seconds=3)
    core['scene']['audio_sha256'] = hashlib.sha256(audio_raw).hexdigest()
    core['scene']['alignment']['audio_sha256'] = core['scene']['audio_sha256']
    document = build_scene_input(core_task=core, method_delivery=delivery(core, methods=[published_method()]))
    artifact = production_result(document)
    package = collect_scene_result(document, artifact)
    _, hosts, _ = prepare()
    host, policy, binding = candidate_config(hosts[production.PROFILE])
    files = {production.INPUT_NAME:canonical_bytes(document)}
    scenario_path, sources, scenario = write_scenario(tmp_path / 'projected', production.PROFILE,
        dict(input_sha256=digest(document)), files)
    pins = {key:value for key,value in policy.pins().items() if key in ExecutionEnvelope.model_fields}
    envelope = ExecutionEnvelope(**pins, job_id=str(uuid4()), attempt_id=str(uuid4()), fence=1,
        deadline_at=time.time() + 600, lease_expires_at=time.time() + 300,
        task=dict(input_sha256=digest(document)), inputs=[InlineFile.from_bytes(name, raw) for name,raw in files.items()])
    directory = tmp_path / 'production-candidate'; root = directory / 'candidate-output'; root.mkdir(parents=True)
    uid, worker_uid, proxy_uid = 303200080, str(uuid4()), str(uuid4())
    result = WorkerResult(state='completed', artifacts=[InlineFile.from_bytes(production.RESULT_NAME, canonical_bytes(artifact))])
    result = result.model_copy(update=dict(stop_receipt=sign_receipt(envelope, result, b'synthetic-only-key' * 4,
        ('pod:' + worker_uid, 'uid:' + str(uid), 'uid:' + str(uid + 1)), time.time() - 2)))
    observation = dict(harness_sha256=harness_sha256(), scenario=dict(scenario_harness_sha256=module_sha256()),
        capability=dict(engine='pi', package_measured=True, allowed_tools=['read_inputs', 'write'],
            pi_cage_repository=admission.PI_CAGE_REPOSITORY, pi_cage_revision=admission.PI_CAGE_REVISION,
            pi_version=admission.PI_VERSION, node_version='24.22.1'),
        isolation={**{key:True for key in admission.ISOLATION}, 'groups':[], 'uid':[uid + 2] * 3,
            'controller_confinement':dict(no_new_privileges=True, seccomp_filtering=True,
                apparmor_label='agent-capsule-inputs (enforce)')},
        model_round_trip=dict(api_mode='responses', status=200, response_sha256='9' * 64))
    record = dict(schema_version='agent-candidate-probe.v1', candidate_only=True, candidate_scope_only=True,
        production_acceptance_created=False, production_routes_changed=False,
        engine='pi', profile=production.PROFILE, profile_revision='1', image_digest=envelope.image_digest,
        binding_id=binding.id, binding_revision=binding.revision,
        combination_sha256=envelope.execution_binding.combination_sha256,
        execution_binding_sha256=envelope.execution_binding.sha256,
        job_id=envelope.job_id, attempt_id=envelope.attempt_id, request_sha256=envelope.request_sha256,
        candidate_result_sha256=digest(result.model_dump(mode='json')), result_state='completed',
        observations=observation, harness_sha256=harness_sha256(), scenario=observation['scenario'],
        lease_expiry_rejected=True, writer_resources=result.stop_receipt.resource_ids,
        writers_observed_stopped_at=result.stop_receipt.observed_at, pod_uid=worker_uid,
        artifacts=[dict(name=production.RESULT_NAME, sha256=result.artifacts[0].sha256,
            bytes=len(canonical_bytes(artifact)))], artifact_count=1,
        model_calls=[dict(body_sha256='f' * 64, response_sha256='9' * 64, state='complete', status=200)])
    manifest = render_probe(host, name=directory.name, worker_image=IMAGE, node='offline-node', uid_start=uid,
        bootstrap_secret='offline-bootstrap', upstream_secret='offline-upstream', scenario=scenario_path, source_dir=sources)
    for role, pod_uid in (('worker', worker_uid), ('proxy', proxy_uid)):
        pod = deepcopy(next(row for row in manifest['items'] if row['kind'] == 'Pod'
            and row['metadata']['name'] == directory.name + '-' + role))
        pod['metadata']['uid'] = pod_uid; pod['spec']['nodeName'] = 'offline-node'
        pod['status'] = dict(phase='Succeeded', containerStatuses=[dict(name=role, restartCount=0,
            imageID='docker-pullable://' + IMAGE, state=dict(terminated=dict(exitCode=0,
                finishedAt=datetime.fromtimestamp(time.time() - 1, timezone.utc).isoformat())))])
        save(root / (role + '-pod.json'), pod)
    for name, value in {
        'candidate-request.json':dict(schema_version='agent-candidate-request.v1', candidate_scope_only=True,
            envelope=envelope.model_dump(mode='json')),
        'candidate-result.json':dict(result=result.model_dump(mode='json'), observations=observation),
        'candidate-evidence.json':record, 'uid-reservation.json':dict(name=directory.name, uid_start=uid,
            uid_count=3, node='offline-node', reusable=False), 'host-config.json':hosts[production.PROFILE],
        'scenario.json':scenario, 'manifest.json':manifest,
        'operator-summary.json':dict(name=directory.name, status='completed', profile=production.PROFILE, engine='pi',
            image_identity_valid=True, production_acceptance_created=False, production_routes_changed=False,
            result_state='completed', lease_expiry_rejected=True, model_calls=1),
        production.INPUT_NAME:files[production.INPUT_NAME], production.RESULT_NAME:artifact,
        'creative-scene-v2.json':package,
        'worker.log':canonical_bytes(dict(status='candidate_probe_finished', engine='pi', profile=production.PROFILE,
            result_state='completed', lease_expired_http_status=401, production_acceptance_created=False,
            pod_uid=worker_uid)) + b'\n'}.items(): save(root / name, value)
    renderer = renderer_capture(tmp_path / 'renderer', package, audio_raw, RUNTIME)
    return dict(directory=directory, root=root, renderer=renderer, package=package)


def renderer_capture(root, package, audio_raw, runtime):
    """Synthetic K8s/media records exercise byte binding; no process executed."""
    scene = package['input_document']['scene']
    files = compile_creative_project(package, scene['beats'], scene['alignment'], runtime_fingerprint=runtime)
    files['audio.wav'] = audio_raw
    for name, value in files.items(): save(root / 'input' / name, value)
    desired = resources(root / 'input', image=RENDER_IMAGE, release_id='synthetic-test', name='production-fixture')
    planned = next(row for row in desired['items'] if row['kind'] == 'Job')
    job_uid = str(uuid4())
    created = deepcopy(planned); created['metadata']['uid'] = job_uid
    final = deepcopy(created); final['status'] = dict(succeeded=1, conditions=[dict(type='Complete', status='True')])
    pod = dict(metadata=dict(name='production-fixture-pod', uid=str(uuid4()), namespace=NAMESPACE,
        ownerReferences=[dict(kind='Job', name=planned['metadata']['name'], uid=job_uid, controller=True)],
        **deepcopy(planned['spec']['template']['metadata'])), spec=deepcopy(planned['spec']['template']['spec']))
    def ended(name):
        return dict(name=name, restartCount=0, imageID='docker-pullable://' + RENDER_IMAGE,
            state=dict(terminated=dict(exitCode=0, finishedAt=datetime.now(timezone.utc).isoformat())))
    pod['status'] = dict(phase='Succeeded', containerStatuses=[ended('renderer')], initContainerStatuses=[ended('prepare')])
    for name, value in {'desired-resources.json':desired, 'job-created.json':created, 'job-final.json':final,
        'pod-final.json':pod, 'runtime.json':runtime, 'networkpolicies.json':dict(items=[deepcopy(desired['items'][1])])}.items():
        save(root / name, value)
    timeline = read(root / 'input/timeline.json'); count = round(timeline['duration_seconds'] * 24)
    times = [0, *[(count - 1) / 24 * (i + 1) / 18 for i in range(17)], (count - 1) / 24, 0]
    entries = {name:value for name,value in files.items() if name not in {'index.html', 'audio.wav'}}
    samples = []
    for index, timestamp in enumerate(times):
        frame = b'synthetic-zero' if timestamp == 0 else ('synthetic-' + str(timestamp)).encode()
        name = f'observations/creative-{index}.jpg'; entries[name] = frame
        samples.append(dict(path=name, time=timestamp, local_seconds=timestamp,
            frame_sha256=hashlib.sha256(frame).hexdigest(), bytes=len(frame)))
    observations = dict(schema='video.creative-observations.v1', renderer='isolated-chromium-absolute-seek-ffmpeg-1',
        input_sha256=package['input_sha256'], source_sha256=package['result_sha256'], frame_count=count,
        samples=samples, blocked_requests=[], page_errors=[], visual_quality='not_evaluated')
    entries.update({'observations.json':canonical_bytes(observations), 'output.mp4':b'0000ftypSYNTHETIC-MP4',
        'experiment.json':canonical_bytes(dict(status='pending_validation', stages=dict(render=0, observe=0)))})
    for name, status in [('render-receipt.json', 'encoded_pending_validation'), ('observe-receipt.json', 'observed_pending_validation')]:
        receipt = dict(schema='video.motion-render-receipt.v1', status=status,
            input_manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest(),
            timeline_sha256=hashlib.sha256(files['timeline.json']).hexdigest(),
            runtime={key:value for key,value in runtime.items() if key != 'image_digest'},
            runtime_binding=dict(status='matched', image_digest=runtime['image_digest']), quality='draft', cli_quality='draft')
        if name == 'render-receipt.json':
            receipt.update(decode_verified=True, output_sha256=hashlib.sha256(entries['output.mp4']).hexdigest(),
                probe=dict(streams=[dict(codec_type='video', codec_name='h264', width=1280, height=720, avg_frame_rate='24/1'),
                    dict(codec_type='audio', codec_name='aac')], format=dict(duration='3')),
                creative_capture=dict(schema='video.creative-capture.v1', renderer=observations['renderer'],
                    frame_count=count, first_sha256=samples[0]['frame_sha256'], last_sha256=samples[-2]['frame_sha256'],
                    reverse_initial_sha256=samples[-1]['frame_sha256'], probe_sha256=[row['frame_sha256'] for row in samples],
                    blocked_requests=[], page_errors=[]))
        entries[name] = canonical_bytes(receipt)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
        for name, value in entries.items():
            member = tarfile.TarInfo(name); member.size = len(value); bundle.addfile(member, io.BytesIO(value))
            save(root / 'artifacts' / name, value)
    save(root / 'pod.log', b'VIDEO_MOTION_ARTIFACT_BASE64=' + base64.b64encode(archive.getvalue()) + b'\n')
    return root


def assess(value):
    return admission.assess_candidate(value['directory'], source_root=SOURCE,
        renderer_dir=value['renderer'], renderer_image=RENDER_IMAGE)


def test_read_only_suite_binds_new_protocol_to_original_job_and_media(capture, monkeypatch):
    before = {str(path):hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (capture['directory'], capture['renderer']) for path in folder.rglob('*') if path.is_file()}
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('No external processes'))
    host, report = assess(capture)
    assert report['suite'] == 'video.animation-production-contract.v1'
    assert host['profile']['enabled'] is False
    assert all(row.get('acceptance') is None for row in host['execution_catalog']['execution_bindings'])
    assert report['model_requests'] == report['uid_allocations'] == 0 and report['acceptance_created'] is False
    assert report['renderer_proof']['audio_clock']['duration_seconds'] == 3
    assert report['renderer_proof']['audio_clock']['review_status'] == 'pending'
    assert report['renderer_proof']['technical_review']['aesthetic_approval'] is False
    assert report['method_evidence_scope'] == 'agent_self_report_source_anchors_not_observed_application_or_quality'
    assert before == {str(path):hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (capture['directory'], capture['renderer']) for path in folder.rglob('*') if path.is_file()}


@pytest.mark.parametrize('name,change,code', [
    ('candidate-evidence.json', lambda doc:doc['model_calls'][0].update(status=500), 'model_calls_missing'),
    ('candidate-evidence.json', lambda doc:doc['model_calls'].append(deepcopy(doc['model_calls'][0])), 'model_calls_missing'),
    ('operator-summary.json', lambda doc:doc.update(lease_expiry_rejected=False), 'operator_mismatch'),
    ('host-config.json', lambda doc:doc['profile'].update(enabled=True), 'candidate_probe_invalid'),
    ('creative-scene-v2.json', lambda doc:doc.update(schema='video.creative-scene.v1'), 'package_mismatch'),
])
def test_candidate_cannot_bypass_new_host_calls_or_v2_package(capture, name, change, code):
    edit(capture['root'], name, change)
    with pytest.raises(ServiceError, match=code): assess(capture)


@pytest.mark.parametrize('name,change,code', [
    ('pod-final.json', lambda doc:doc['status']['containerStatuses'][0].update(restartCount=1), 'renderer_stop_missing'),
    ('pod-final.json', lambda doc:doc['status']['initContainerStatuses'][0].update(imageID='docker://wrong'), 'renderer_stop_missing'),
    ('pod-final.json', lambda doc:doc['status'].update(phase='Running'), 'renderer_pod_scope_invalid'),
    ('pod-final.json', lambda doc:doc['metadata']['ownerReferences'][0].update(uid='other'), 'renderer_job_owner_invalid'),
    ('job-final.json', lambda doc:doc['metadata'].update(uid='other'), 'renderer_job_incomplete'),
    ('networkpolicies.json', lambda doc:doc['items'][0]['spec'].update(egress=[{}]), 'renderer_network_not_isolated'),
    ('runtime.json', lambda doc:doc.update(image_digest='sha256:' + 'f' * 64), 'renderer_image_mismatch'),
])
def test_renderer_requires_exact_immutable_stopped_isolated_job(capture, name, change, code):
    edit(capture['renderer'], name, change)
    with pytest.raises(ServiceError, match=code): assess(capture)


@pytest.mark.parametrize('name', ['input/index.html', 'input/audio.wav', 'artifacts/output.mp4',
    'artifacts/observations/creative-1.jpg', 'artifacts/render-receipt.json'])
def test_renderer_refuses_changed_source_audio_or_exported_artifact(capture, name):
    path = capture['renderer'] / name; path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises((ServiceError, ValueError)): assess(capture)


def test_original_worker_http401_required(capture):
    save(capture['root'] / 'worker.log', canonical_bytes(dict(status='candidate_probe_finished',
        engine='pi', profile=production.PROFILE, result_state='completed', lease_expired_http_status=200)))
    with pytest.raises(ServiceError, match='real_lease_401_missing'): assess(capture)


def test_legacy_method_profile_is_never_admitted_by_production_suite(capture):
    edit(capture['root'], 'candidate-request.json', lambda doc:doc['envelope'].update(profile='video.animation-method-author.v1'))
    with pytest.raises((ServiceError, ValueError)): assess(capture)


def test_optional_admission_uses_own_suite_and_keeps_routes_disabled(capture, tmp_path, monkeypatch):
    from agent_service import worker_entry
    # Synthetic reader tests accepted_host's exact hashes and pins. It makes no
    # assertion about this Windows test tree's production mount permissions.
    monkeypatch.setattr(worker_entry, '_read_operator_json', lambda path, **kwargs:read(path))
    output = tmp_path / 'admitted'
    summary = admission.review_candidate(capture['directory'], output, source_root=SOURCE,
        renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)
    approval = read(output / 'binding-approvals.json')[0]
    target = output / approval['path']
    host = read(target / 'config.json')
    report = read(target / 'acceptance-evidence.json')
    assert worker_entry.accepted_host(worker_entry.HostConfig.model_validate(host), target / 'acceptance.json')
    assert approval['acceptance']['suite'] == summary['suite'] == report['suite'] == admission.SUITE
    assert summary['accepted_host'] is True and summary['enabled'] is False
    assert summary['model_requests'] == summary['uid_allocations'] == 0
    assert host['profile']['enabled'] is False and all(row['enabled'] is False for row in host['execution_catalog']['routes'])
    assert report['renderer_proof']['technical_review']['aesthetic_approval'] is False
    with pytest.raises(ServiceError, match='new_output_required'):
        admission.review_candidate(capture['directory'], output, source_root=SOURCE,
            renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)


def test_rejected_capture_creates_no_partial_admission(capture, tmp_path):
    output = tmp_path / 'rejected-admission'
    edit(capture['root'], 'operator-summary.json', lambda doc:doc.update(lease_expiry_rejected=False))
    with pytest.raises(ServiceError, match='operator_mismatch'):
        admission.review_candidate(capture['directory'], output, source_root=SOURCE,
            renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)
    assert not output.exists()
