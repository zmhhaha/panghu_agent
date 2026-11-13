"""Read-only technical review of original production/v2-render captures.

This separate suite invokes no model, subprocess, renderer or Kubernetes API.
Assessment writes nothing. Optional --output freezes a new disabled technical
admission only after every check passes; it enables no route or publication. Root
operator captures are the authority; an exported receipt is not a fresh offline
HMAC check, model comprehension claim or developer aesthetic approval.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import time
import wave

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_creation_acceptance import (
    CHECKS, ISOLATION, _capture, _directory, _pod_stop, _save, _source_package)
from agent_service.deploy.candidate_scenario import PARENT_FILES, verify_parent
from agent_service.input_tools import (READ_INPUTS_ENVIRONMENT_NAME,
    READ_INPUTS_EXTENSION_SHA256, read_inputs_environment_revision)
from agent_service.pi_cage import PI_CAGE_REPOSITORY, PI_CAGE_REVISION, PI_VERSION
from agent_service.profiles import video_animation_production as production
from agent_service.profiles.video import decode_video_json
from agent_service.registry import binding_fingerprint, required_model_capabilities
from agent_service.worker_entry import HostConfig, _host_registry, accepted_host, host_policy
from video_workbench.animation_candidate_import import EXPORT_NAMES, _pod
from video_workbench.creative_method_contract import collect_scene_result, validate_scene_package
from video_workbench.creative_renderer import (METHOD_COMPILER, ENGINE_METHOD_COMPILER, FILM_COMPILER,
    compile_creative_project)
from video_workbench.motion_render_k8s import _subset, unpack_artifacts, validate_artifacts
from video_workbench.motion_renderer.job import FILES, NAMESPACE, resources
from video_workbench.motion_snapshot import _runtime


SUITE = 'video.animation-production-contract.v1'
FILM_SUITE = 'video.animation-production-contract.v2'
SHA = re.compile(r'[0-9a-f]{64}\Z')
IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}\Z')
NOT_CLAIMED = ['offline_hmac_reverification', 'current_git_status_reverified',
    'production_api_dispatcher_end_to_end', 'business_user_approval', 'subjective_animation_quality',
    'model_understanding', 'whole_video_watched', 'audio_quality', 'method_application_observed',
    'method_quality_improvement', 'other_profiles_or_images']


def require(value, code='animation_production_admission_invalid'):
    if not value:
        raise ServiceError(code, 422)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _generation(film):
    require(type(film) is bool, 'animation_production_admission_generation_invalid')
    return (production.FILM_PROFILE, FILM_SUITE, production.FILM_BUNDLE_REVISION,
        production.FILM_BUNDLE_MANIFEST_SHA256, production.VideoAnimationFilmProductionProfile()) if film else (
        production.PROFILE, SUITE, production.BUNDLE_REVISION, production.BUNDLE_MANIFEST_SHA256,
        production.VideoAnimationProductionProfile())


def _bundle(package, registry, binding, policy, *, film=False):
    profile, _, bundle_revision, manifest_sha, handler = _generation(film)
    contract = registry.contract(profile, policy.revision)
    bundle = registry.bundle(binding.bundle.name, binding.bundle.revision)
    environment = registry.environment(binding.tool_environment.name, binding.tool_environment.revision)
    require(bundle.name == 'animation-production' and bundle.revision == bundle_revision
        and set(contract.required_tools) == set(bundle.required_tools) == set(environment.tools)
        == set(handler.required_tools) == {'read_inputs', 'write'}
        and environment.name == READ_INPUTS_ENVIRONMENT_NAME
        and environment.revision == read_inputs_environment_revision(environment.model_dump(mode='json')),
        'animation_production_admission_tools_mismatch')
    require(set(required_model_capabilities(handler)) <= set(binding.model_policy.capabilities),
            'animation_production_admission_model_policy_mismatch')
    root = package / 'resources' / bundle_revision
    raw = _capture(root, 'manifest.json', 65536)
    require(sha(raw) == manifest_sha, 'animation_production_admission_resource_mismatch')
    manifest = decode_video_json(raw)
    rows = manifest.get('local_files')
    require(manifest.get('bundle_revision') == bundle_revision
        and manifest.get('automatic_updates') is False and manifest.get('upstream_content_vendored') is False
        and manifest.get('license') == 'MIT' and type(rows) is list
        and len(rows) == len(production.BUNDLE_FILES)
        and all(type(row) is dict and set(row) == {'path', 'sha256'} for row in rows)
        and {row['path'] for row in rows} == production.BUNDLE_FILES,
        'animation_production_admission_resource_mismatch')
    measured = {'manifest.json':sha(raw)}
    for row in rows:
        measured[row['path']] = sha(_capture(root, row['path'], 128 * 1024))
        require(measured[row['path']] == row['sha256'], 'animation_production_admission_resource_mismatch')
    require({row.name:row.sha256 for row in bundle.resources} == measured
        and sha(_capture(package, 'read_inputs_extension.mjs', 128 * 1024)) == READ_INPUTS_EXTENSION_SHA256,
        'animation_production_admission_resource_mismatch')
    return measured


def _audio_clock(raw, document):
    try:
        with wave.open(io.BytesIO(raw), 'rb') as audio:
            require(audio.getcomptype() == 'NONE' and audio.getsampwidth() == 2
                and audio.getnchannels() in (1, 2) and 8000 <= audio.getframerate() <= 48000,
                'animation_production_admission_audio_invalid')
            count, rate = audio.getnframes(), audio.getframerate()
            require(len(audio.readframes(count)) == count * audio.getnchannels() * 2,
                    'animation_production_admission_audio_invalid')
    except (wave.Error, EOFError):
        raise ServiceError('animation_production_admission_audio_invalid', 422) from None
    seconds = count / rate
    scene = document['scene']
    require(sha(raw) == scene['audio_sha256'] == scene['alignment']['audio_sha256']
        and abs(seconds - document['constraints']['duration_seconds']) <= 1e-9
        and abs(seconds - scene['alignment']['audio_seconds']) <= 1e-9,
        'animation_production_admission_audio_clock_mismatch')
    return dict(sha256=sha(raw), bytes=len(raw), sample_rate=rate, frame_count=count,
        duration_seconds=seconds, alignment_method=scene['alignment']['method'],
        review_status=scene['alignment']['review_status'], audio_quality_approved=False)


def _renderer_stop(pod, job, desired):
    expected = desired['spec']['template']
    metadata, spec, state = pod.get('metadata', {}), pod.get('spec', {}), pod.get('status', {})
    require(metadata.get('namespace') == NAMESPACE and type(metadata.get('uid')) is str
        and bool(metadata['uid']) and _subset(expected.get('metadata', {}), metadata)
        and _subset(expected['spec'], spec)
        and all(spec.get(key, False) is False for key in ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace'))
        and not spec.get('ephemeralContainers') and not state.get('ephemeralContainerStatuses')
        and spec.get('automountServiceAccountToken') is False and spec.get('restartPolicy') == 'Never'
        and state.get('phase') == 'Succeeded', 'animation_production_renderer_pod_scope_invalid')
    owners = metadata.get('ownerReferences')
    require(type(owners) is list and len(owners) == 1 and owners[0].get('kind') == 'Job'
        and owners[0].get('name') == job['metadata']['name'] and owners[0].get('uid') == job['metadata']['uid']
        and owners[0].get('controller') is True, 'animation_production_renderer_job_owner_invalid')
    rows = []
    for spec_key, status_key in (('initContainers', 'initContainerStatuses'), ('containers', 'containerStatuses')):
        containers, statuses = spec.get(spec_key), state.get(status_key)
        require(type(containers) is list and type(statuses) is list and len(containers) == len(statuses) == 1,
                'animation_production_renderer_stop_missing')
        container, status = containers[0], statuses[0]
        security = container.get('securityContext', {})
        ended = status.get('state', {}).get('terminated')
        image_digest = container['image'].rsplit('@', 1)[1]
        require(container['name'] == status.get('name') and security.get('privileged', False) is False
            and security.get('capabilities', {}).get('add', []) == [] and type(ended) is dict
            and type(ended.get('exitCode')) is int and ended['exitCode'] == 0
            and type(status.get('restartCount')) is int and status['restartCount'] == 0
            and type(status.get('imageID')) is str and status['imageID'].endswith('@' + image_digest),
            'animation_production_renderer_stop_missing')
        try:
            finished = datetime.fromisoformat(ended['finishedAt'].replace('Z', '+00:00'))
            require(finished.tzinfo is not None and 0 < finished.timestamp() <= time.time() + 5,
                    'animation_production_renderer_stop_missing')
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ServiceError('animation_production_renderer_stop_missing', 422) from None
        rows.append(dict(name=container['name'], image_id=status['imageID'], finished_at=ended['finishedAt'], restarts=0))
    return dict(pod_uid=metadata['uid'], job_uid=job['metadata']['uid'], all_containers_stopped=True, containers=rows)


def verify_renderer_capture(directory, package, *, renderer_image):
    """Check original seven-file Job input, raw K8s stop and log export bytes."""
    package = validate_scene_package(package)
    film = package['schema'] == 'video.creative-scene.v4'
    engine_aware = package['schema'] in {'video.creative-scene.v3', 'video.creative-scene.v4'}
    compiler = FILM_COMPILER if film else ENGINE_METHOD_COMPILER if engine_aware else METHOD_COMPILER
    require(package['schema'] in {'video.creative-scene.v2', 'video.creative-scene.v3', 'video.creative-scene.v4'} and type(renderer_image) is str
        and IMAGE.fullmatch(renderer_image), 'animation_production_renderer_v2_required')
    root = _directory(directory)
    names = ('desired-resources.json', 'job-created.json', 'job-final.json', 'pod-final.json',
             'pod.log', 'runtime.json', 'networkpolicies.json')
    raw = {name:_capture(root, name, 12 * 1024 * 1024) for name in names}
    docs = {name:decode_video_json(value) for name,value in raw.items() if name.endswith('.json')}
    runtime = _runtime(docs['runtime.json'])
    require(runtime['image_digest'] == renderer_image.rsplit('@', 1)[1],
            'animation_production_renderer_image_mismatch')
    files = {name:_capture(root / 'input', name, 8 * 1024 * 1024) for name in FILES}
    require({path.relative_to(root / 'input').as_posix() for path in (root / 'input').rglob('*')
        if path.is_file()} == set(FILES), 'animation_production_renderer_input_members_changed')
    require(decode_video_json(files['plan.json']) == package, 'animation_production_renderer_package_mismatch')
    scene = package['input_document']['scene']
    timeline = decode_video_json(files['timeline.json'])
    expected_files = compile_creative_project(package, scene['beats'], scene['alignment'],
        runtime_fingerprint=runtime, leading_seconds=timeline['leading_seconds'],
        trailing_seconds=timeline['trailing_seconds'], compiler_version=compiler)
    require(all(files[name] == value for name,value in expected_files.items())
        and timeline['compiler'] == compiler, 'animation_production_renderer_input_mismatch')
    audio = _audio_clock(files['audio.wav'], package['input_document'])
    desired = docs['desired-resources.json']
    jobs = [row for row in desired.get('items', []) if row.get('kind') == 'Job']
    require(len(jobs) == 1, 'animation_production_renderer_job_invalid')
    planned = jobs[0]
    release_id = planned['spec']['template']['metadata']['annotations']['video-workbench.panghuer.top/release-id']
    seconds = planned['spec']['activeDeadlineSeconds'] - 90
    expected = resources(root / 'input', image=renderer_image, release_id=release_id,
        name=planned['metadata']['name'], seconds=seconds, allow_unbound_experiment=False)
    require(desired == expected, 'animation_production_renderer_resources_changed')
    created, final = docs['job-created.json'], docs['job-final.json']
    require(_subset(planned, created) and _subset(planned, final)
        and type(created.get('metadata', {}).get('uid')) is str and bool(created['metadata']['uid'])
        and final.get('metadata', {}).get('uid') == created['metadata']['uid']
        and final.get('status', {}).get('succeeded') == 1 and final['status'].get('failed', 0) == 0
        and final['status'].get('active', 0) == 0
        and any(row.get('type') == 'Complete' and row.get('status') == 'True'
                for row in final['status'].get('conditions', [])), 'animation_production_renderer_job_incomplete')
    stopped = _renderer_stop(docs['pod-final.json'], final, planned)
    policies = docs['networkpolicies.json'].get('items')
    require(type(policies) is list and policies
        and all(not row.get('spec', {}).get('ingress') and not row.get('spec', {}).get('egress') for row in policies)
        and any(row.get('metadata', {}).get('namespace') == NAMESPACE and row.get('spec', {}).get('podSelector') == {}
            and set(row['spec'].get('policyTypes', [])) == {'Ingress', 'Egress'} for row in policies),
        'animation_production_renderer_network_not_isolated')
    entries = unpack_artifacts(raw['pod.log'])
    artifact_root = _directory(root / 'artifacts')
    actual = {path.relative_to(artifact_root).as_posix() for path in artifact_root.rglob('*') if path.is_file()}
    require(actual == set(entries) and all(_capture(artifact_root, name, 32 * 1024 * 1024) == value
        for name,value in entries.items()), 'animation_production_renderer_log_export_changed')
    evidence = validate_artifacts(entries, files, runtime, 'draft')
    receipt = decode_video_json(entries['render-receipt.json'])
    probe = receipt.get('probe', {})
    streams = probe.get('streams')
    require(type(streams) is list and len(streams) == 2, 'animation_production_renderer_media_probe_missing')
    videos, audios = ([row for row in streams if row.get('codec_type') == kind] for kind in ('video', 'audio'))
    try:
        duration = float(probe['format']['duration'])
    except (KeyError, TypeError, ValueError):
        raise ServiceError('animation_production_renderer_media_probe_missing', 422) from None
    require(len(videos) == len(audios) == 1 and videos[0].get('codec_name') == 'h264'
        and videos[0].get('width') == 1280 and videos[0].get('height') == 720
        and videos[0].get('avg_frame_rate') == '24/1' and audios[0].get('codec_name') == 'aac'
        and math.isfinite(duration) and abs(duration - timeline['duration_seconds']) <= .35,
        'animation_production_renderer_media_probe_mismatch')
    require(all(_capture(root, name, 12 * 1024 * 1024) == value for name,value in raw.items())
        and all(_capture(root / 'input', name, 8 * 1024 * 1024) == value for name,value in files.items())
        and all(_capture(artifact_root, name, 32 * 1024 * 1024) == value for name,value in entries.items()),
        'animation_production_renderer_capture_changed')
    return dict(schema='video.animation-film-production-captured-render-proof.v1' if film else
        'video.animation-production-captured-render-proof.v2' if engine_aware
        else 'video.animation-production-captured-render-proof.v1',
        package_sha256=digest(package), source_sha256=package['result_sha256'],
        input_sha256=package['input_sha256'], renderer_image=renderer_image, runtime=runtime,
        audio_clock=audio, stopped=stopped, media_sha256=evidence['media_sha256'],
        technical_review=evidence['technical_review'],
        capture_sha256={name:sha(value) for name,value in raw.items()},
        input_files_sha256={name:sha(value) for name,value in files.items()},
        artifact_files_sha256={name:sha(value) for name,value in entries.items()},
        scope='original_v4_film_input_isolated_job_media_native_context_and_reverse_seek' if film else
            'original_v3_engine_input_isolated_job_media_native_context_and_reverse_seek' if engine_aware
            else 'original_v2_input_isolated_job_media_and_reverse_seek',
        whole_video_watched=False, audio_observed=False, subjective_quality_approved=False)


def assess_candidate(candidate_dir, *, source_root=None, renderer_dir, renderer_image, film=False):
    """Return (unchanged disabled Host, new-suite report), with no writes."""
    selected_profile, suite, _, _, _ = _generation(film)
    directory = _directory(candidate_dir)
    root = _directory(directory / 'candidate-output')
    raw = {name:_capture(root, name) for name in (*EXPORT_NAMES, 'uid-reservation.json', 'worker.log')}
    docs = {name:decode_video_json(value) for name,value in raw.items() if name.endswith('.json')}
    require(all(type(value) is dict for value in docs.values()))
    envelope, result = verify_parent({name:docs[name] for name in PARENT_FILES.values()})
    require(envelope.profile == selected_profile and envelope.profile_revision == '1' and envelope.engine == 'pi'
        and not envelope.checkpoint_inputs and envelope.checkpoint_ref is None,
        'animation_production_admission_profile_not_allowed')
    proof = result.stop_receipt
    require(len(envelope.inputs) == 1 and envelope.inputs[0].name == production.INPUT_NAME,
            'animation_production_admission_input_mismatch')
    input_raw = envelope.inputs[0].content()
    document = production.validate_input(decode_video_json(input_raw))
    require(document['schema'] in ({production.FILM_INPUT_SCHEMA} if film else
        {production.INPUT_SCHEMA, production.LEGACY_INPUT_SCHEMA}), 'animation_production_admission_profile_not_allowed')
    require(input_raw == canonical_bytes(document) and envelope.task == {'input_sha256':digest(document)},
            'animation_production_admission_input_mismatch')
    package_name = ('creative-scene-v4.json' if film else 'creative-scene-v3.json' if document['schema'] == production.INPUT_SCHEMA
                    else 'creative-scene-v2.json')
    for name, maximum in ((production.INPUT_NAME, production.INPUT_MAX_BYTES),
            (production.RESULT_NAME, production.OUTPUT_MAX_BYTES),
            (package_name, production.INPUT_MAX_BYTES + production.OUTPUT_MAX_BYTES + 1024 * 1024)):
        raw[name] = _capture(root, name, maximum)
    require(raw[production.INPUT_NAME] == input_raw and len(result.artifacts) == 1
        and result.artifacts[0].name == production.RESULT_NAME
        and result.artifacts[0].content() == raw[production.RESULT_NAME],
        'animation_production_admission_artifact_mismatch')
    artifact = production.validate_result(decode_video_json(raw[production.RESULT_NAME]), digest(document), document)
    package = collect_scene_result(document, artifact)
    require(decode_video_json(raw[package_name]) == package,
            'animation_production_admission_package_mismatch')
    record, observation = docs['candidate-evidence.json'], docs['candidate-result.json']['observations']
    host, policy, binding = candidate_config(docs['host-config.json'])
    registry = _host_registry(host)
    combination = binding_fingerprint(registry.contract(selected_profile, '1'),
        registry.bundle(binding.bundle.name, binding.bundle.revision),
        registry.environment(binding.tool_environment.name, binding.tool_environment.revision), binding)
    require(policy.matches(envelope) and policy.execution_binding == envelope.execution_binding
        and record.get('schema_version') == 'agent-candidate-probe.v1'
        and record.get('candidate_only') is True and record.get('candidate_scope_only') is True
        and record.get('production_acceptance_created') is False and record.get('production_routes_changed') is False
        and record.get('profile_revision') == '1' and record.get('binding_id') == binding.id
        and record.get('binding_revision') == binding.revision and record.get('combination_sha256') == combination
        and record.get('execution_binding_sha256') == envelope.execution_binding.sha256
        and record.get('observations') == observation and type(record.get('artifact_count')) is int
        and record['artifact_count'] == 1 and record.get('artifacts') == [dict(name=production.RESULT_NAME,
            sha256=sha(raw[production.RESULT_NAME]), bytes=len(raw[production.RESULT_NAME]))]
        and proof is not None and proof.fence == envelope.fence and proof.engine == envelope.engine
        and 0 < proof.observed_at <= time.time() + 5 and len(set(proof.resource_ids)) == len(proof.resource_ids),
        'animation_production_admission_controller_mismatch')
    scenario = docs['scenario.json']
    require(set(scenario) == {'schema_version', 'candidate_scope_only', 'scenario_id', 'profile',
        'timeout_seconds', 'task', 'inputs', 'business_export', 'parent'}
        and scenario.get('schema_version') == 'agent-candidate-scenario.v1'
        and scenario.get('candidate_scope_only') is True and scenario.get('profile') == selected_profile
        and scenario.get('task') == envelope.task and scenario.get('business_export') is None
        and scenario.get('parent') is None and scenario.get('inputs') == [dict(name=production.INPUT_NAME,
            sha256=sha(input_raw), bytes=len(input_raw))], 'animation_production_admission_scenario_mismatch')
    source = _source_package(source_root)
    measured_bundle = _bundle(source, registry, binding, policy, film=film)
    harness = sha(_capture(source, 'deploy/acceptance_probe.py').decode().replace('\r\n', '\n').encode())
    scenario_harness = sha(_capture(source, 'deploy/candidate_scenario.py').decode().replace('\r\n', '\n').encode())
    require(observation.get('harness_sha256') == record.get('harness_sha256') == harness
        and observation.get('scenario') == record.get('scenario')
        and record.get('scenario', {}).get('scenario_harness_sha256') == scenario_harness,
        'animation_production_admission_harness_mismatch')
    maps = [row for row in docs['manifest.json'].get('items', []) if row.get('kind') == 'ConfigMap'
        and row.get('metadata', {}).get('name') == directory.name]
    require(len(maps) == 2 and {row['metadata'].get('namespace') for row in maps}
        == {'agent-service', 'agent-workers'} and all(row.get('immutable') is True for row in maps)
        and maps[0].get('data') == maps[1].get('data') and type(maps[0].get('data')) is dict,
        'animation_production_admission_harness_mismatch')
    projected = maps[0]['data']
    expected_host = deepcopy(docs['host-config.json'])
    expected_host['model']['proxy_base_url'] = 'http://' + directory.name + '.agent-service.svc.cluster.local:8080/v1'
    require(set(projected) == {'config.json', 'acceptance_probe.py', 'candidate_scenario.py', 'scenario.json'}
        and decode_video_json(projected.get('config.json')) == expected_host
        and sha(projected.get('acceptance_probe.py', '').encode()) == harness
        and sha(projected.get('candidate_scenario.py', '').encode()) == scenario_harness
        and decode_video_json(projected.get('scenario.json')) == scenario,
        'animation_production_admission_harness_mismatch')
    capability = observation.get('capability', {})
    require(capability.get('engine') == 'pi' and capability.get('package_measured') is True
        and capability.get('allowed_tools') == ['read_inputs', 'write']
        and capability.get('pi_cage_repository') == PI_CAGE_REPOSITORY
        and capability.get('pi_cage_revision') == PI_CAGE_REVISION and capability.get('pi_version') == PI_VERSION
        and type(capability.get('node_version')) is str and re.fullmatch(r'24\.\d+\.\d+', capability['node_version']),
        'animation_production_admission_capability_missing')
    reservation = docs['uid-reservation.json']
    uid, node = reservation.get('uid_start'), reservation.get('node')
    require(type(uid) is int and 65536 <= uid <= 2**31 - 4 and reservation.get('uid_count') == 3
        and reservation.get('reusable') is False and reservation.get('name') == directory.name
        and type(node) is str and bool(node), 'animation_production_admission_uid_missing')
    isolation = observation.get('isolation', {})
    confinement = isolation.get('controller_confinement', {})
    require(all(isolation.get(key) is True for key in ISOLATION) and isolation.get('groups') == []
        and isolation.get('uid') == [uid + 2] * 3
        and {'uid:' + str(uid), 'uid:' + str(uid + 1)} <= set(proof.resource_ids)
        and confinement.get('no_new_privileges') is True and confinement.get('seccomp_filtering') is True
        and confinement.get('apparmor_label') == 'agent-capsule-inputs (enforce)',
        'animation_production_admission_isolation_missing')
    planned = {(row['metadata']['namespace'], row['metadata']['name']):row
        for row in docs['manifest.json'].get('items', []) if row.get('kind') == 'Pod'}
    require(len(planned) == sum(row.get('kind') == 'Pod' for row in docs['manifest.json']['items']) == 2,
            'animation_production_admission_pod_mismatch')
    stopped = {}
    for namespace, role in (('agent-workers', 'worker'), ('agent-service', 'proxy')):
        pod = docs[role + '-pod.json']
        require((namespace, directory.name + '-' + role) in planned, 'animation_production_admission_pod_mismatch')
        _pod(pod, planned[namespace, directory.name + '-' + role], envelope.image_digest, completed_worker=role == 'worker')
        stopped[role] = _pod_stop(pod, worker=role == 'worker', proof=proof, uid=uid, node=node)
    require(stopped['worker']['pod_uid'] == record.get('pod_uid') != stopped['proxy']['pod_uid'],
            'animation_production_admission_pod_mismatch')
    calls = record.get('model_calls')
    require(type(calls) is list and 1 <= len(calls) <= 8 and all(type(row) is dict
        and row.get('state') == 'complete' and type(row.get('status')) is int and row['status'] == 200
        and all(type(row.get(key)) is str and SHA.fullmatch(row[key]) for key in
                ('body_sha256', 'response_sha256')) for row in calls)
        and len({row['body_sha256'] for row in calls}) == len(calls),
        'animation_production_admission_model_calls_missing')
    trip = observation.get('model_round_trip', {})
    require(trip.get('api_mode') == 'responses' and type(trip.get('status')) is int and trip['status'] == 200
        and trip.get('response_sha256') in {row['response_sha256'] for row in calls},
        'animation_production_admission_round_trip_missing')
    finished = []
    for line in raw['worker.log'].splitlines():
        try: row = decode_video_json(line)
        except ServiceError: continue
        if type(row) is dict and row.get('status') == 'candidate_probe_finished': finished.append(row)
    require(len(finished) == 1 and finished[0].get('engine') == 'pi'
        and finished[0].get('profile') == selected_profile and finished[0].get('result_state') == 'completed'
        and type(finished[0].get('lease_expired_http_status')) is int and finished[0]['lease_expired_http_status'] == 401
        and finished[0].get('pod_uid') == stopped['worker']['pod_uid']
        and finished[0].get('production_acceptance_created') is False,
        'animation_production_admission_real_lease_401_missing')
    summary = docs['operator-summary.json']
    require(summary.get('name') == directory.name and summary.get('status') == 'completed'
        and summary.get('profile') == selected_profile and summary.get('engine') == 'pi'
        and summary.get('image_identity_valid') is True and summary.get('production_acceptance_created') is False
        and summary.get('production_routes_changed') is False and type(summary.get('model_calls')) is int
        and summary['model_calls'] == len(calls) and summary.get('result_state') == 'completed'
        and summary.get('lease_expiry_rejected') is True, 'animation_production_admission_operator_mismatch')
    render_proof = verify_renderer_capture(renderer_dir, package, renderer_image=renderer_image)
    require(all(_capture(root, name) == value for name,value in raw.items()),
            'animation_production_admission_capture_changed')
    report = dict(schema='video-animation-film-production-independent-review.v1' if film else
        'video-animation-production-independent-review.v1', suite=suite,
        evidence_origin='captured_real_candidate', profile=selected_profile, profile_revision='1', engine='pi',
        candidate_name=directory.name, image_digest=envelope.image_digest, runtime_revision=envelope.runtime_revision,
        candidate_job_id=envelope.job_id, candidate_attempt_id=envelope.attempt_id,
        binding_id=binding.id, binding_revision=binding.revision, combination_sha256=combination,
        source_files_sha256={name:sha(value) for name,value in raw.items()},
        measured_bundle_files_sha256=measured_bundle, measured_harness_sha256=harness,
        measured_scenario_harness_sha256=scenario_harness,
        checks=dict(capability=capability, isolation=isolation, model_round_trip=trip,
            model_calls_complete=len(calls), model_proxy_lease=dict(worker_log_http_status=401),
            stopped_worker=stopped['worker'], stopped_proxy=stopped['proxy'],
            input_sha256=digest(document), output_sha256=sha(raw[production.RESULT_NAME])),
        method_delivery_sha256=package['method_delivery_sha256'], method_decision=package['method_decision'],
        method_evidence_scope=package['evidence_scope'], renderer_proof=render_proof,
        original_candidate_flags_preserved=True, technical_host_prerequisites_passed=True,
        acceptance_created=False, production_routes_enabled=False, deployment_performed=False,
        model_requests=0, uid_allocations=0, source_evidence_modified=False, live_http_verified=False,
        not_claimed=NOT_CLAIMED + (['whole_film_continuity_observed', 'predecessor_frame_observed'] if film else []))
    if film:
        context = document['film_context']
        report['film_context_binding'] = dict(film_sha256=context['film_sha256'],
            current_scene_index=context['current_scene_index'],
            predecessor_sha256=digest(context['predecessor']) if context['predecessor'] is not None else None,
            handoff_sha256=digest(artifact['handoff']), scope='frozen_context_and_author_declaration_not_observed_continuity')
    return host.model_dump(mode='json'), report


def review_candidate(candidate_dir, output, *, source_root=None, renderer_dir, renderer_image, film=False):
    """Freeze standard admission files in a new directory; never deploy/enable."""
    selected_profile, suite, _, _, _ = _generation(film)
    directory, render_root = _directory(candidate_dir), _directory(renderer_dir)
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink() and not output.is_relative_to(directory)
        and not output.is_relative_to(render_root), 'animation_production_admission_new_output_required')
    _directory(output.parent)
    host, report = assess_candidate(directory, source_root=source_root,
        renderer_dir=render_root, renderer_image=renderer_image, film=film)
    combination = report['combination_sha256']
    name = report['binding_id'] + '-' + combination[:20]
    require(len(name) <= 63, 'animation_production_admission_host_name_invalid')
    output.mkdir(mode=0o700)
    target = output / 'worker-configs' / name
    target.mkdir(mode=0o700, parents=True)
    checksum = _save(target / 'acceptance-evidence.json', report)
    approval = dict(combination_sha256=combination, suite=suite, report_sha256=checksum)
    binding = next(row for row in host['execution_catalog']['execution_bindings']
        if (row['id'], row['revision']) == (report['binding_id'], report['binding_revision']))
    require(binding.get('acceptance') is None, 'animation_production_admission_already_accepted')
    binding['acceptance'] = approval
    config = HostConfig.model_validate(host)
    policy = host_policy(config)
    require(not policy.enabled and all(row.get('enabled') is False for row in host['execution_catalog']['routes'])
        and policy.execution_binding.combination_sha256 == combination)
    _save(target / 'config.json', host)
    _save(target / 'acceptance.json', dict(schema_version=1, engine='pi', profile=selected_profile,
        profile_revision='1', runtime_revision=report['runtime_revision'], image_digest=report['image_digest'],
        model_api='openai-responses', checks={key:True for key in CHECKS}, evidence_sha256=checksum,
        execution_binding_sha256=policy.execution_binding.sha256))
    require(accepted_host(config, target / 'acceptance.json'), 'animation_production_admission_host_rejected')
    _save(output / 'binding-approvals.json', [dict(binding_id=report['binding_id'],
        binding_revision=report['binding_revision'], acceptance=approval, host_config_map=name,
        path='worker-configs/' + name)])
    summary = dict(schema='animation-film-production-technical-admission.v1' if film else 'animation-production-technical-admission.v1', suite=suite,
        profile=selected_profile, combination_sha256=combination, report_sha256=checksum,
        accepted_execution_binding_sha256=policy.execution_binding.sha256, host_config_map=name,
        accepted_host=True, enabled=False, technical_host_admission_only=True, deployment_performed=False,
        model_requests=0, uid_allocations=0, source_evidence_modified=False,
        live_http_verified=False, not_claimed=NOT_CLAIMED + (['whole_film_continuity_observed', 'predecessor_frame_observed'] if film else []))
    _save(output / 'summary.json', summary)
    return summary


def main(argv=None, *, film=False):
    selected_profile, suite, _, _, _ = _generation(film)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--renderer-dir', required=True, type=Path)
    parser.add_argument('--renderer-image', required=True)
    parser.add_argument('--output', type=Path, help='Optional new disabled technical-admission directory')
    args = parser.parse_args(argv)
    try:
        require(os.name == 'posix' and os.geteuid() == 0, 'animation_production_admission_server_root_required')
        options = dict(film=film, source_root=args.source, renderer_dir=args.renderer_dir, renderer_image=args.renderer_image)
        if args.output is not None:
            report = review_candidate(args.candidate_dir, args.output, **options)
        else:
            _, detailed = assess_candidate(args.candidate_dir, **options)
            report = dict(status='technical_prerequisites_passed', suite=suite, enabled=False,
                report_content_sha256=digest(detailed), profile=selected_profile,
                acceptance_created=False, deployment_performed=False, model_requests=0, uid_allocations=0)
    except (OSError, ValueError, KeyError, TypeError, ServiceError, AttributeError) as error:
        print(json.dumps(dict(status='technical_review_rejected', suite=suite,
            reason=error.code if isinstance(error, ServiceError) else 'malformed_capture',
            acceptance_created=False, deployment_performed=False, model_requests=0, automatic_retry=False)))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
