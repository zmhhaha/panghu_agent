"""Synthetic captures test the reviewer; no model, Pod, or live acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

import pytest

from agent_service.contracts import ExecutionEnvelope, ServiceError, WorkerResult, digest, sign_receipt
from agent_service.deploy import animation_creation_acceptance as admission
from agent_service.deploy.acceptance_probe import harness_sha256, render_probe
from agent_service.deploy.candidate_scenario import module_sha256
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from agent_service.worker_entry import HostConfig, accepted_host
from agent_service import worker_entry
from test_animation_candidate_import import make_pilot, save
from test_candidate_scenario import write_scenario, synthetic_source_root_owner


PACKAGE = Path(admission.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def synthetic_operator_file_reader(monkeypatch):
    # Exercise accepted_host's actual identity/check/hash logic. Root-owned
    # read-only Linux mounts cannot be attested by Windows or pytest /tmp.
    # This is only an offline fixture reader, never an accepted_host=True stub.
    monkeypatch.setattr(worker_entry, '_read_operator_json', lambda path, **kwargs: read(path))


def read(path):
    return json.loads(path.read_bytes())


def edit(root, name, change):
    value = read(root / name)
    change(value)
    save(root / name, value)


def capture(tmp_path, role='review', *, review_profile=visual.REVIEW_SEQUENCE_PROFILE):
    pilot = make_pilot(tmp_path, review_profile=review_profile, with_observer=False)
    directory = tmp_path / ('candidate-animation-' + role + '-p1')
    root = directory / 'candidate-output'
    request, captured, record = (read(root / name) for name in
        ('candidate-request.json', 'candidate-result.json', 'candidate-evidence.json'))
    envelope = ExecutionEnvelope.model_validate(request['envelope'])
    uid = 219100000
    observation = dict(harness_sha256=harness_sha256(),
        scenario=dict(scenario_harness_sha256=module_sha256()),
        capability=dict(engine='pi', package_measured=True, allowed_tools=['read', 'write'],
            pi_cage_repository=admission.PI_CAGE_REPOSITORY, pi_cage_revision=admission.PI_CAGE_REVISION,
            pi_version=admission.PI_VERSION, node_version='24.22.1'),
        isolation={**{key: True for key in admission.ISOLATION}, 'groups': [], 'uid': [uid + 2] * 3,
            'controller_confinement': dict(no_new_privileges=True, seccomp_filtering=True,
                apparmor_label='agent-capsule-inputs (enforce)')},
        model_round_trip=dict(api_mode='responses', status=200, response_sha256='9' * 64))
    worker_uid = record['pod_uid']
    result = WorkerResult.model_validate(captured['result'])
    result = result.model_copy(update={'stop_receipt': sign_receipt(envelope, result, b'synthetic-key' * 4,
        ('pod:' + worker_uid, 'uid:' + str(uid), 'uid:' + str(uid + 1)), time.time() - 2)})
    captured.update(result=result.model_dump(mode='json'), observations=observation)
    record.update(observations=observation, harness_sha256=harness_sha256(), scenario=observation['scenario'],
        candidate_result_sha256=digest(captured['result']), writer_resources=result.stop_receipt.resource_ids,
        writers_observed_stopped_at=result.stop_receipt.observed_at,
        model_calls=[dict(body_sha256='f' * 64, response_sha256='9' * 64, state='complete', status=200)],
        unverified=['full_production_acceptance_suite', 'external_pod_termination'])
    save(root / 'candidate-result.json', captured)
    save(root / 'candidate-evidence.json', record)
    save(root / 'uid-reservation.json', dict(name=directory.name, uid_start=uid, uid_count=3,
        node='offline-node', reusable=False))
    config = read(root / 'host-config.json')
    sources = {item.name: item.content() for item in envelope.inputs}
    scenario_path, source_dir, scenario = write_scenario(tmp_path / 'projected-scenario',
        envelope.profile, envelope.task, sources)
    save(root / 'scenario.json', scenario)
    # Use the public probe renderer itself: its candidate transport URL differs
    # from the captured host, while the immutable execution pins stay exact.
    manifest = render_probe(HostConfig.model_validate(config), name=directory.name,
        worker_image='registry.invalid/pi@' + envelope.image_digest, node='offline-node',
        uid_start=uid, bootstrap_secret='offline-bootstrap', upstream_secret='offline-upstream',
        scenario=scenario_path, source_dir=source_dir)
    for name in ('worker', 'proxy'):
        original = read(root / (name + '-pod.json'))
        planned = next(row for row in manifest['items'] if row['kind'] == 'Pod'
            and row['metadata']['name'] == directory.name + '-' + name)
        actual = deepcopy(planned)
        actual['metadata']['uid'] = original['metadata']['uid']
        actual['spec']['nodeName'] = 'offline-node'
        actual['status'] = original['status']
        actual['status']['phase'] = 'Succeeded'
        actual['status']['containerStatuses'][0]['state']['terminated']['finishedAt'] = (
            datetime.fromtimestamp(time.time() - 1, timezone.utc).isoformat())
        save(root / (name + '-pod.json'), actual)
    save(root / 'manifest.json', manifest)
    save(root / 'operator-summary.json', dict(name=directory.name, status='completed',
        profile=envelope.profile, engine='pi', image_identity_valid=True,
        production_acceptance_created=False, production_routes_changed=False,
        result_state='completed', lease_expiry_rejected=True, model_calls=1))
    input_name = creation.INPUT_NAME if role == 'create' else visual.REVIEW_INPUT_NAME
    save(root / input_name, next(item.content() for item in envelope.inputs if item.name == input_name))
    save(root / 'worker.log', b'non-JSON diagnostic, never copied into the report\n' + json.dumps(dict(
        status='candidate_probe_finished', engine='pi', profile=envelope.profile,
        result_state='completed', lease_expired_http_status=401,
        production_acceptance_created=False, pod_uid=worker_uid)).encode() + b'\n')
    return directory, root


@pytest.mark.parametrize('role', ['create', 'review'])
def test_four_captured_prerequisites_produce_only_a_disabled_host(tmp_path, role):
    directory, root = capture(tmp_path, role)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    output = tmp_path / 'new-review'
    summary = admission.review_candidate(directory, output)
    approval = read(output / 'binding-approvals.json')[0]
    target = output / approval['path']
    host = HostConfig.model_validate(read(target / 'config.json'))
    report = read(target / 'acceptance-evidence.json')
    assert accepted_host(host, target / 'acceptance.json')
    assert host.profile.enabled is False and summary['enabled'] is False
    assert summary['model_requests'] == summary['uid_allocations'] == 0
    assert report['not_claimed'] == admission.NOT_CLAIMED
    assert 'full_production_acceptance_suite' in report['not_claimed']
    assert report['live_http_verified'] is False and report['original_candidate_flags_preserved'] is True
    assert hashlib.sha256((target / 'acceptance-evidence.json').read_bytes()).hexdigest() == approval['acceptance']['report_sha256']
    assert approval['acceptance']['suite'] == ('video.animation-create-contract.v2' if role == 'create'
                                             else 'video.animation-review-contract.v2')
    if role == 'review':
        assert report['model_frame_transport']['observed_frame_ids'] == ['frame-1', 'frame-2']
        assert report['model_frame_transport']['model_understanding_verified'] is False
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()} == before
    assert not list(tmp_path.glob('*.sqlite3'))
    with pytest.raises(ServiceError, match='new_output_required'):
        admission.review_candidate(directory, output)


def test_batch_acceptance_uses_new_profile_bundle_and_suite(tmp_path):
    directory, root = capture(tmp_path, review_profile=visual.REVIEW_BATCH_PROFILE)
    output = tmp_path / 'batch-review'
    summary = admission.review_candidate(directory, output)
    approval = read(output / 'binding-approvals.json')[0]
    target = output / approval['path']
    report = read(target / 'acceptance-evidence.json')
    host = HostConfig.model_validate(read(target / 'config.json'))
    assert summary['profile'] == visual.REVIEW_BATCH_PROFILE
    assert approval['acceptance']['suite'] == 'video.animation-review-contract.v3'
    assert report['profile'] == visual.REVIEW_BATCH_PROFILE
    host_policy_bundle = host.execution_catalog['capability_bundles'][0]
    assert host_policy_bundle['revision'] == visual.BATCH_BUNDLE_REVISION
    assert report['measured_bundle_files_sha256']['manifest.json'] == visual.BATCH_BUNDLE_MANIFEST_SHA256
    assert accepted_host(host, target / 'acceptance.json')
    assert summary['enabled'] is False and summary['live_http_verified'] is False
    assert summary['model_requests'] == summary['uid_allocations'] == 0


def test_batch_bundle_cannot_be_admitted_with_old_review_manifest(tmp_path):
    directory, _ = capture(tmp_path, review_profile=visual.REVIEW_BATCH_PROFILE)
    copied = tmp_path / 'source'
    shutil.copytree(PACKAGE / 'resources' / visual.BATCH_BUNDLE_REVISION,
                    copied / 'resources' / visual.BATCH_BUNDLE_REVISION)
    (copied / 'resources' / visual.BATCH_BUNDLE_REVISION / 'manifest.json').write_bytes(
        (PACKAGE / 'resources' / visual.SEQUENCE_BUNDLE_REVISION / 'manifest.json').read_bytes())
    with pytest.raises(ServiceError, match='animation_creation_admission_resource_mismatch'):
        admission.assess_candidate(directory, source_root=copied)


@pytest.mark.parametrize('name,change', [
    ('candidate-evidence.json', lambda d: d['model_calls'][0].update(state='admitted')),
    ('candidate-evidence.json', lambda d: d['model_calls'][0].update(response_sha256='invalid')),
    ('candidate-evidence.json', lambda d: d.update(model_calls=[])),
    ('candidate-evidence.json', lambda d: d['model_calls'].append(deepcopy(d['model_calls'][0]))),
    ('candidate-evidence.json', lambda d: d['model_image_evidence']['images'][0].update(model_request_sha256='8' * 64)),
    ('candidate-evidence.json', lambda d: d['model_image_evidence']['images'][0].update(byte_length=1)),
    ('candidate-evidence.json', lambda d: d['model_image_evidence'].update(images=[])),
    ('uid-reservation.json', lambda d: d.update(reusable=True)),
    ('uid-reservation.json', lambda d: d.update(uid_start=1)),
    ('uid-reservation.json', lambda d: d.update(name='different-candidate')),
    ('worker-pod.json', lambda d: d['status']['containerStatuses'][0].update(state={'running': {}})),
    ('worker-pod.json', lambda d: d['status']['containerStatuses'][0]['state']['terminated'].update(finishedAt='2000-01-01T00:00:00Z')),
    ('worker-pod.json', lambda d: d['status']['containerStatuses'][0].update(restartCount=1)),
    ('proxy-pod.json', lambda d: d['status']['containerStatuses'][0].update(imageID='wrong@sha256:' + '0' * 64)),
    ('operator-summary.json', lambda d: d.update(model_calls=999)),
    ('scenario.json', lambda d: d['inputs'][0].update(sha256='0' * 64)),
    ('host-config.json', lambda d: d['profile'].update(enabled=True)),
])
def test_missing_or_contradictory_capture_cannot_write_an_acceptance(tmp_path, name, change):
    directory, root = capture(tmp_path)
    edit(root, name, change)
    output = tmp_path / 'rejected'
    with pytest.raises((ServiceError, ValueError)):
        admission.review_candidate(directory, output)
    assert not output.exists()


@pytest.mark.parametrize('change', ['isolation', 'capability', 'harness', 'model_response'])
def test_controller_and_result_agreement_cannot_replace_measured_evidence(tmp_path, change):
    directory, root = capture(tmp_path)
    candidate, record = read(root / 'candidate-result.json'), read(root / 'candidate-evidence.json')
    observation = candidate['observations']
    if change == 'isolation':
        observation['isolation']['read_only_mount_measured'] = False
    elif change == 'capability':
        observation['capability']['pi_cage_revision'] = '0' * 40
    elif change == 'harness':
        observation['harness_sha256'] = '0' * 64
        record['harness_sha256'] = '0' * 64
    else:
        observation['model_round_trip']['response_sha256'] = '0' * 64
    record['observations'] = observation
    save(root / 'candidate-result.json', candidate)
    save(root / 'candidate-evidence.json', record)
    with pytest.raises(ServiceError):
        admission.review_candidate(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


@pytest.mark.parametrize('change', ['missing', 'no_finished', 'not_401', 'wrong_pod', 'duplicate'])
def test_real_worker_finished_record_is_mandatory(tmp_path, change):
    directory, root = capture(tmp_path)
    log = root / 'worker.log'
    if change == 'missing':
        log.unlink()
    elif change == 'no_finished':
        log.write_bytes(b'worker stopped\n')
    elif change == 'duplicate':
        log.write_bytes(log.read_bytes() * 2)
    else:
        row = json.loads(log.read_bytes().splitlines()[-1])
        row.update({'lease_expired_http_status': 200} if change == 'not_401' else {'pod_uid': 'wrong-pod'})
        save(log, row)
    with pytest.raises(ServiceError):
        admission.review_candidate(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


def test_matching_plan_and_actual_cannot_weaken_pod_confinement(tmp_path):
    directory, root = capture(tmp_path)
    edit(root, 'worker-pod.json', lambda d: d['spec']['containers'][0]['securityContext'].update(privileged=True))
    edit(root, 'manifest.json', lambda d: next(row for row in d['items']
        if row['kind'] == 'Pod' and row['metadata']['name'].endswith('-worker'))[
            'spec']['containers'][0]['securityContext'].update(privileged=True))
    with pytest.raises(ServiceError, match='pod_confinement_invalid'):
        admission.review_candidate(directory, tmp_path / 'rejected')


def test_projected_harness_must_match_the_independently_measured_source(tmp_path):
    directory, root = capture(tmp_path)
    def change(document):
        for row in document['items']:
            if row['kind'] == 'ConfigMap':
                row['data']['acceptance_probe.py'] = 'pass\n'
    edit(root, 'manifest.json', change)
    with pytest.raises(ServiceError, match='harness_mismatch'):
        admission.review_candidate(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


@pytest.mark.parametrize('change', ['shared_origin', 'other_candidate', 'model'])
def test_projected_transport_exception_cannot_change_the_origin_or_other_host_fields(tmp_path, change):
    directory, root = capture(tmp_path)
    def mutate(document):
        for row in document['items']:
            if row['kind'] != 'ConfigMap':
                continue
            value = json.loads(row['data']['config.json'])
            if change == 'shared_origin':
                value['model']['proxy_base_url'] = read(root / 'host-config.json')['model']['proxy_base_url']
            elif change == 'other_candidate':
                value['model']['proxy_base_url'] = 'http://other-candidate.agent-service.svc.cluster.local:8080/v1'
            else:
                value['model']['model'] = 'different-model'
            row['data']['config.json'] = json.dumps(value)
    edit(root, 'manifest.json', mutate)
    with pytest.raises((ServiceError, ValueError)):
        admission.review_candidate(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


@pytest.mark.parametrize('field', ['observation', 'record', 'scenario'])
def test_independent_harness_records_must_match_the_frozen_source(tmp_path, field):
    directory, root = capture(tmp_path)
    if field == 'observation':
        for name in ('candidate-result.json', 'candidate-evidence.json'):
            edit(root, name, lambda d: d['observations'].update(harness_sha256='0' * 64))
    elif field == 'record':
        edit(root, 'candidate-evidence.json', lambda d: d.update(harness_sha256='0' * 64))
    else:
        edit(root, 'candidate-evidence.json', lambda d: d['scenario'].update(scenario_harness_sha256='0' * 64))
    with pytest.raises(ServiceError):
        admission.review_candidate(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


def test_resource_drift_in_explicit_source_is_rejected(tmp_path):
    directory, _ = capture(tmp_path)
    source = tmp_path / 'reviewed-source' / 'agent_service'
    shutil.copytree(PACKAGE / 'resources' / visual.SEQUENCE_BUNDLE_REVISION,
                    source / 'resources' / visual.SEQUENCE_BUNDLE_REVISION)
    (source / 'deploy').mkdir()
    for name in ('acceptance_probe.py', 'candidate_scenario.py'):
        shutil.copy2(PACKAGE / 'deploy' / name, source / 'deploy' / name)
    target = source / 'resources' / visual.SEQUENCE_BUNDLE_REVISION / 'review-procedure/SKILL.md'
    target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(ServiceError, match='resource_mismatch'):
        admission.review_candidate(directory, tmp_path / 'rejected', source_root=source.parent)


def test_output_cannot_be_written_inside_the_original_capture(tmp_path):
    directory, _ = capture(tmp_path)
    with pytest.raises(ServiceError, match='new_output_required'):
        admission.review_candidate(directory, directory / 'new-review')
