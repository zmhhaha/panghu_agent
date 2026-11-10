"""Independently attest stopped creation candidates; never enable a route.

This operator reads existing root-owned captures and the original worker log.
It grants only the four technical host prerequisites. It neither reruns an
Agent nor turns candidate evidence into a production/API end-to-end claim.
The controller verified the ephemeral receipt signature before capture; this
review binds its exported digest and does not pretend to possess that key.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.candidate_scenario import PARENT_FILES, verify_parent
from agent_service.pi_cage import PI_CAGE_REPOSITORY, PI_CAGE_REVISION, PI_VERSION
from agent_service.profiles import implementation, video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from agent_service.profiles.video import _read, decode_video_json
from agent_service.registry import binding_fingerprint, required_model_capabilities
from agent_service.worker_entry import HostConfig, _host_registry, accepted_host, host_policy
from video_workbench.animation_candidate_import import EXPORT_NAMES, validate_candidate


PROFILES = {creation.PROFILE: 'create', visual.REVIEW_SEQUENCE_PROFILE: 'review',
            visual.REVIEW_BATCH_PROFILE: 'review'}
CHECKS = ('capability', 'model_round_trip', 'isolation', 'model_proxy_lease')
ISOLATION = ('arbitrary_mount_denied', 'caps_empty', 'control_unreadable',
    'detached_writers_stopped', 'input_write_denied', 'no_new_privileges',
    'private_kernel_scope', 'read_only_mount_measured')
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
NOT_CLAIMED = ['offline_hmac_reverification', 'full_production_acceptance_suite',
    'production_api_dispatcher_end_to_end', 'business_user_approval',
    'subjective_animation_quality', 'model_understanding', 'whole_video_watched',
    'audio_quality', 'other_profiles_or_images']


def require(value, code='animation_creation_admission_invalid'):
    if not value:
        raise ServiceError(code, 422)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _directory(path):
    path = Path(path).absolute()
    require(path.is_dir() and path.resolve() == path, 'animation_creation_admission_directory_invalid')
    for part in (path, *path.parents):
        require(not part.is_symlink() and not getattr(part.lstat(), 'st_file_attributes', 0) & 0x400,
                'animation_creation_admission_directory_invalid')
    return path


def _capture(root, name, maximum=MAX_CAPTURE_BYTES):
    raw = _read(root, name, maximum)
    info = (root / name).stat()
    require(os.name != 'posix' or info.st_uid == 0 and not info.st_mode & 0o022,
            'animation_creation_admission_root_owned_capture_required')
    return raw


def _source_package(source):
    root = _directory(source) if source is not None else Path(__file__).resolve().parents[1]
    return _directory(root / 'agent_service') if (root / 'agent_service').is_dir() else root


def _bundle(package, registry, binding, policy, profile):
    handler = implementation(profile)
    contract = registry.contract(profile, policy.revision)
    bundle = registry.bundle(binding.bundle.name, binding.bundle.revision)
    environment = registry.environment(binding.tool_environment.name, binding.tool_environment.revision)
    if profile == creation.PROFILE:
        revision, manifest_sha, members = (
            creation.BUNDLE_REVISION, creation.BUNDLE_MANIFEST_SHA256, creation.BUNDLE_FILES)
    elif profile == visual.REVIEW_BATCH_PROFILE:
        revision, manifest_sha, members = (
            visual.BATCH_BUNDLE_REVISION, visual.BATCH_BUNDLE_MANIFEST_SHA256, visual.BATCH_BUNDLE_FILES)
    else:
        revision, manifest_sha, members = (
            visual.SEQUENCE_BUNDLE_REVISION, visual.SEQUENCE_BUNDLE_MANIFEST_SHA256, visual.SEQUENCE_BUNDLE_FILES)
    require(bundle.revision == revision and set(contract.required_tools) == set(bundle.required_tools)
            == set(environment.tools) == set(handler.required_tools) == {'read', 'write'},
            'animation_creation_admission_bundle_tools_mismatch')
    require(set(required_model_capabilities(handler)) <= set(binding.model_policy.capabilities),
            'animation_creation_admission_model_policy_mismatch')
    root = package / 'resources' / revision
    raw = _capture(root, 'manifest.json', 65536)
    require(sha(raw) == manifest_sha, 'animation_creation_admission_resource_mismatch')
    manifest = decode_video_json(raw)
    require(manifest.get('bundle_revision') == revision and manifest.get('license') == 'MIT'
            and manifest.get('automatic_updates') is False
            and manifest.get('upstream_content_vendored') is False,
            'animation_creation_admission_resource_mismatch')
    rows = manifest.get('local_files')
    require(type(rows) is list and len(rows) == len(members)
            and all(type(row) is dict and set(row) == {'path', 'sha256'} for row in rows)
            and {row['path'] for row in rows} == members,
            'animation_creation_admission_resource_mismatch')
    measured = {'manifest.json': manifest_sha}
    for row in rows:
        measured[row['path']] = sha(_capture(root, row['path'], 128 * 1024))
        require(measured[row['path']] == row['sha256'], 'animation_creation_admission_resource_mismatch')
    require({row.name: row.sha256 for row in bundle.resources} == measured,
            'animation_creation_admission_resource_mismatch')
    return measured


def _pod_stop(pod, *, worker, proof, uid, node):
    spec, status = pod['spec'], pod['status']
    require(spec.get('nodeName') == node and spec.get('automountServiceAccountToken') is False
            and spec.get('restartPolicy') == 'Never'
            and all(spec.get(k, False) is False for k in
                    ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace'))
            and spec.get('securityContext', {}).get('seccompProfile') == {'type': 'RuntimeDefault'}
            and len(spec['containers']) == len(status['containerStatuses']) == 1,
            'animation_creation_admission_pod_confinement_invalid')
    container, state = spec['containers'][0], status['containerStatuses'][0]
    security = container.get('securityContext', {})
    additions = ['CHOWN', 'DAC_OVERRIDE', 'KILL', 'SETUID', 'SETGID', 'SYS_ADMIN'] if worker else []
    require(container['name'] == ('worker' if worker else 'proxy')
            and container.get('command') == ['/opt/agent-service/.venv/bin/python', '/probe/acceptance_probe.py']
            and type(security.get('runAsUser')) is int and type(security.get('runAsGroup')) is int
            and security.get('runAsUser') == security.get('runAsGroup') == 0
            and security.get('allowPrivilegeEscalation') is False
            and security.get('readOnlyRootFilesystem') is True
            and security.get('privileged', False) is False
            and security.get('capabilities', {}).get('drop') == ['ALL']
            and security.get('capabilities', {}).get('add', []) == additions,
            'animation_creation_admission_pod_confinement_invalid')
    if worker:
        require(security.get('appArmorProfile') ==
                {'type': 'Localhost', 'localhostProfile': 'agent-capsule-inputs'},
                'animation_creation_admission_pod_confinement_invalid')
    else:
        args = container.get('args', [])
        require(args.count('--uid-start') == 1 and args[args.index('--uid-start') + 1:][:1] == [str(uid)],
                'animation_creation_admission_uid_evidence_missing')
    ended = state.get('state', {}).get('terminated')
    require(type(ended) is dict and type(state.get('restartCount')) is int
            and state['restartCount'] == 0 and (not worker or ended.get('exitCode') == 0),
            'animation_creation_admission_pod_stop_invalid')
    try:
        stamp = datetime.fromisoformat(ended['finishedAt'].replace('Z', '+00:00'))
        require(stamp.tzinfo is not None and stamp.timestamp() >= proof.observed_at,
                'animation_creation_admission_pod_stop_invalid')
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ServiceError('animation_creation_admission_pod_stop_invalid', 422) from None
    return dict(pod_uid=pod['metadata']['uid'], image_id=state['imageID'],
        exit_code=ended.get('exitCode'), finished_at=ended['finishedAt'],
        runtime_uid=uid if worker else None, external_containers_observed_stopped=True)


def assess_candidate(candidate_dir, *, source_root=None):
    """Read and recheck one trusted capture. No ledger or output is written."""
    directory = _directory(candidate_dir)
    root = _directory(directory / 'candidate-output')
    names = (*EXPORT_NAMES, 'uid-reservation.json', 'worker.log')
    raw = {name: _capture(root, name) for name in names}
    documents = {name: decode_video_json(value) for name, value in raw.items() if name.endswith('.json')}
    require(all(type(doc) is dict for doc in documents.values()))
    envelope, result = verify_parent({name: documents[name] for name in PARENT_FILES.values()})
    profile, proof = envelope.profile, result.stop_receipt
    require(profile in PROFILES and envelope.engine == 'pi' and envelope.profile_revision == '1',
            'animation_creation_admission_profile_not_allowed')
    input_name = creation.INPUT_NAME if PROFILES[profile] == 'create' else visual.REVIEW_INPUT_NAME
    inputs = {item.name: item.content() for item in envelope.inputs}
    require(input_name in inputs, 'animation_creation_admission_input_missing')
    document = decode_video_json(inputs[input_name])
    raw[input_name] = _capture(root, input_name)
    require(raw[input_name] == inputs[input_name], 'animation_creation_admission_input_mismatch')
    candidate = validate_candidate(directory, role=PROFILES[profile], document=document, files=inputs,
        expected_image_digest=envelope.image_digest, expected_profile=profile)
    require(candidate.state == 'completed', 'animation_creation_admission_candidate_failed')
    result_name = input_name.replace('-input.json', '-result.json')
    raw[result_name] = _capture(root, result_name)
    record, observation = documents['candidate-evidence.json'], documents['candidate-result.json']['observations']
    host, policy, binding = candidate_config(documents['host-config.json'])
    registry = _host_registry(host)
    combination = binding_fingerprint(registry.contract(profile, '1'),
        registry.bundle(binding.bundle.name, binding.bundle.revision),
        registry.environment(binding.tool_environment.name, binding.tool_environment.revision), binding)
    require(policy.execution_binding == envelope.execution_binding
            and combination == record['combination_sha256'], 'animation_creation_admission_binding_mismatch')
    require(host.model is not None and host.model.model == binding.model_policy.model
            and host.model.provider == binding.model_policy.provider
            and host.model.api == binding.model_policy.api_mode,
            'animation_creation_admission_model_policy_mismatch')
    package = _source_package(source_root)
    bundle = _bundle(package, registry, binding, policy, profile)
    harness = sha(_capture(package, 'deploy/acceptance_probe.py').decode('utf-8').replace('\r\n', '\n').encode())
    scenario_harness = sha(_capture(package, 'deploy/candidate_scenario.py').decode('utf-8').replace('\r\n', '\n').encode())
    require(observation.get('harness_sha256') == harness
            and record.get('harness_sha256') == harness
            and record.get('scenario', {}).get('scenario_harness_sha256') == scenario_harness,
            'animation_creation_admission_harness_mismatch')
    maps = [row for row in documents['manifest.json']['items']
            if row.get('kind') == 'ConfigMap' and row.get('metadata', {}).get('name') == directory.name]
    require(len(maps) == 2
            and {row['metadata'].get('namespace') for row in maps} == {'agent-service', 'agent-workers'}
            and all(type(row.get('data')) is dict and row.get('immutable') is True for row in maps)
            and maps[0]['data'] == maps[1]['data'],
            'animation_creation_admission_harness_mismatch')
    projected = maps[0]['data']
    expected_projected_host = deepcopy(documents['host-config.json'])
    expected_projected_host['model']['proxy_base_url'] = (
        'http://' + directory.name + '.agent-service.svc.cluster.local:8080/v1')
    projected_host = decode_video_json(projected.get('config.json'))
    _, projected_policy, _ = candidate_config(projected_host)
    require(type(projected.get('acceptance_probe.py')) is str
            and sha(projected['acceptance_probe.py'].encode('utf-8')) == harness
            and type(projected.get('candidate_scenario.py')) is str
            and sha(projected['candidate_scenario.py'].encode('utf-8')) == scenario_harness
            and projected_host == expected_projected_host
            and projected_policy.execution_binding == policy.execution_binding
            and decode_video_json(projected.get('scenario.json')) == documents['scenario.json'],
            'animation_creation_admission_harness_mismatch')
    capability = observation.get('capability', {})
    require(capability.get('engine') == 'pi' and capability.get('package_measured') is True
            and capability.get('allowed_tools') == ['read', 'write']
            and capability.get('pi_cage_repository') == PI_CAGE_REPOSITORY
            and capability.get('pi_cage_revision') == PI_CAGE_REVISION
            and capability.get('pi_version') == PI_VERSION
            and type(capability.get('node_version')) is str
            and re.fullmatch(r'24\.\d+\.\d+', capability['node_version']),
            'animation_creation_admission_capability_missing')
    reservation = documents['uid-reservation.json']
    uid, node = reservation.get('uid_start'), reservation.get('node')
    require(type(uid) is int and 65536 <= uid <= 2 ** 31 - 4
            and reservation.get('uid_count') == 3 and reservation.get('reusable') is False
            and reservation.get('name') == directory.name and type(node) is str and node,
            'animation_creation_admission_uid_evidence_missing')
    isolation = observation.get('isolation', {})
    confinement = isolation.get('controller_confinement', {})
    require(all(isolation.get(key) is True for key in ISOLATION)
            and isolation.get('groups') == [] and isolation.get('uid') == [uid + 2] * 3
            and {'uid:' + str(uid), 'uid:' + str(uid + 1)} <= set(proof.resource_ids)
            and confinement.get('no_new_privileges') is True and confinement.get('seccomp_filtering') is True
            and confinement.get('apparmor_label') == 'agent-capsule-inputs (enforce)',
            'animation_creation_admission_isolation_missing')
    worker, proxy = documents['worker-pod.json'], documents['proxy-pod.json']
    stopped_worker = _pod_stop(worker, worker=True, proof=proof, uid=uid, node=node)
    stopped_proxy = _pod_stop(proxy, worker=False, proof=proof, uid=uid, node=node)
    require(stopped_worker['pod_uid'] != stopped_proxy['pod_uid'],
            'animation_creation_admission_pod_identity_mismatch')
    calls = record.get('model_calls')
    require(type(calls) is list and 1 <= len(calls) <= 1000 and all(type(row) is dict
            and row.get('state') == 'complete' and type(row.get('status')) is int and row['status'] == 200
            and all(type(row.get(k)) is str and re.fullmatch('[0-9a-f]{64}', row[k])
                    for k in ('body_sha256', 'response_sha256')) for row in calls),
            'animation_creation_admission_model_calls_missing')
    require(len({row['body_sha256'] for row in calls}) == len(calls),
            'animation_creation_admission_model_calls_duplicate')
    trip = observation.get('model_round_trip', {})
    require(trip.get('api_mode') == 'responses' and type(trip.get('status')) is int
            and trip['status'] == 200 and trip.get('response_sha256') in {row['response_sha256'] for row in calls},
            'animation_creation_admission_model_round_trip_missing')
    receipt = candidate.frame_receipt
    if PROFILES[profile] == 'review':
        bodies = {row['body_sha256'] for row in calls}
        require(all(row['model_request_completed'] is True and row['model_request_sha256'] in bodies
                    for row in receipt['images']), 'animation_creation_admission_image_call_unbound')
        for frame in document['observation']['frames']:
            content = inputs[frame['file_name']]
            require(sha(content) == frame['sha256'] and any(row['sha256'] == frame['sha256']
                    and row['byte_length'] == len(content) for row in receipt['images']),
                    'animation_creation_admission_image_bytes_mismatch')
    finished = []
    for line in raw['worker.log'].splitlines():
        try:
            row = decode_video_json(line)
        except ServiceError:
            continue
        if type(row) is dict and row.get('status') == 'candidate_probe_finished':
            finished.append(row)
    require(len(finished) == 1 and finished[0].get('engine') == 'pi'
            and finished[0].get('profile') == profile and finished[0].get('result_state') == 'completed'
            and type(finished[0].get('lease_expired_http_status')) is int
            and finished[0]['lease_expired_http_status'] == 401
            and finished[0].get('pod_uid') == stopped_worker['pod_uid']
            and finished[0].get('production_acceptance_created') is False,
            'animation_creation_admission_real_lease_401_missing')
    summary = documents['operator-summary.json']
    require(summary.get('name') == directory.name and type(summary.get('model_calls')) is int
            and summary['model_calls'] == len(calls)
            and summary.get('result_state') == 'completed' and summary.get('lease_expiry_rejected') is True,
            'animation_creation_admission_operator_summary_mismatch')
    hashes = {name: sha(value) for name, value in raw.items()}
    require(all(_capture(root, name) == value for name, value in raw.items()),
            'animation_creation_admission_capture_changed')
    report = dict(schema_version='video-animation-creation-independent-review.v1',
        evidence_origin='captured_real_candidate', profile=profile, profile_revision='1', engine='pi',
        candidate_name=directory.name, evidence_directory=str(root),
        candidate_job_id=envelope.job_id, candidate_attempt_id=envelope.attempt_id,
        image=worker['spec']['containers'][0]['image'],
        image_digest=envelope.image_digest, runtime_revision=envelope.runtime_revision,
        binding_id=binding.id, binding_revision=binding.revision, combination_sha256=combination,
        source_files_sha256=hashes, measured_bundle_files_sha256=bundle,
        measured_harness_sha256=harness, measured_scenario_harness_sha256=scenario_harness,
        checks=dict(capability=capability, isolation=isolation, model_round_trip=trip,
            model_calls_complete=len(calls), model_proxy_lease=dict(worker_log_http_status=401),
            stopped_worker=stopped_worker, stopped_proxy=stopped_proxy,
            input_sha256=envelope.task['input_sha256'], output_sha256=sha(raw[result_name])),
        model_frame_transport=candidate.observation,
        original_candidate_flags_preserved=True, technical_host_admission_only=True,
        production_routes_enabled=False, deployment_performed=False, model_requests=0,
        uid_allocations=0, live_http_verified=False, not_claimed=NOT_CLAIMED)
    return host.model_dump(mode='json'), report


def _save(path, document):
    raw = canonical_bytes(document) + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return sha(raw)


def review_candidate(candidate_dir, output, *, source_root=None):
    """Write a new disabled host only after every captured check succeeds."""
    directory = _directory(candidate_dir)
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink() and not output.is_relative_to(directory),
            'animation_creation_admission_new_output_required')
    _directory(output.parent)
    host, report = assess_candidate(directory, source_root=source_root)
    profile, combination = report['profile'], report['combination_sha256']
    host_name = report['binding_id'] + '-' + combination[:20]
    require(len(host_name) <= 63, 'animation_creation_admission_host_name_invalid')
    output.mkdir(mode=0o700)
    target = output / 'worker-configs' / host_name
    target.mkdir(mode=0o700, parents=True)
    report_sha = _save(target / 'acceptance-evidence.json', report)
    approval = dict(combination_sha256=combination,
        suite=profile.rsplit('.v', 1)[0] + '-contract.v' + profile.rsplit('.v', 1)[1], report_sha256=report_sha)
    accepted = deepcopy(host)
    selected = next(row for row in accepted['execution_catalog']['execution_bindings']
        if row['id'] == report['binding_id'] and row['revision'] == report['binding_revision'])
    require(selected.get('acceptance') is None, 'animation_creation_admission_already_accepted')
    selected['acceptance'] = approval
    require(accepted['profile']['enabled'] is False
            and all(row['enabled'] is False for row in accepted['execution_catalog']['routes']),
            'animation_creation_admission_disabled_host_required')
    config = HostConfig.model_validate(accepted)
    policy = host_policy(config)
    require(policy.enabled is False and policy.execution_binding.combination_sha256 == combination)
    record = dict(schema_version=1, engine='pi', profile=profile, profile_revision='1',
        runtime_revision=report['runtime_revision'], image_digest=report['image_digest'],
        model_api='openai-responses', checks={key: True for key in CHECKS},
        evidence_sha256=report_sha, execution_binding_sha256=policy.execution_binding.sha256)
    _save(target / 'config.json', accepted)
    _save(target / 'acceptance.json', record)
    require(accepted_host(config, target / 'acceptance.json'), 'animation_creation_admission_host_rejected')
    _save(output / 'binding-approvals.json', [dict(binding_id=report['binding_id'],
        binding_revision=report['binding_revision'], acceptance=approval, host_config_map=host_name,
        path='worker-configs/' + host_name)])
    summary = dict(schema='animation-creation-technical-admission.v1', profile=profile,
        combination_sha256=combination, report_sha256=report_sha,
        accepted_execution_binding_sha256=policy.execution_binding.sha256,
        host_config_map=host_name, accepted_host=True, enabled=False,
        technical_host_admission_only=True, deployment_performed=False,
        model_requests=0, uid_allocations=0, source_evidence_modified=False,
        live_http_verified=False, not_claimed=NOT_CLAIMED)
    _save(output / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir', required=True, type=Path)
    parser.add_argument('--source', type=Path, help='Reviewed source root for measured bundle/harness bytes')
    parser.add_argument('--output', required=True, type=Path, help='New directory; existing evidence is never replaced')
    args = parser.parse_args(argv)
    try:
        require(os.name == 'posix' and os.geteuid() == 0, 'animation_creation_admission_server_root_required')
        summary = review_candidate(args.candidate_dir, args.output, source_root=args.source)
    except (OSError, ValueError, KeyError, TypeError, ServiceError, AttributeError) as error:
        reason = error.code if isinstance(error, ServiceError) else 'malformed_capture'
        reason = reason if re.fullmatch('[a-z][a-z0-9_]{1,120}', reason) else 'malformed_capture'
        print(json.dumps(dict(status='technical_admission_rejected', enabled=False,
            reason=reason, deployment_performed=False, model_requests=0, automatic_retry=False)))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
