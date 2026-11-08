"""Explicit, durable four-task creative pilot over the common Agent service.

This CLI is separate from the credential-free lab web server. It does not
retry model work, merge code, publish automatically, or execute scene scripts
outside the isolated renderer.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
from functools import wraps
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from agent_service.client import AgentClient
from agent_service.contracts import ExecutionBindingPins, ExecutionEnvelope, ServiceError
from agent_service.profiles import video_animation_experiment as experiment_profile
from agent_service.profiles.video_animation_experiment import (
    PROFILE, INPUT_NAME, RESULT_NAME, INPUT_MAX_BYTES, OUTPUT_MAX_BYTES,
    VideoAnimationExperimentProfile, validate_experiment_input, validate_experiment_result, scene_files)
from .motion_gateway import MotionAgentGateway
from .animation_experiments import (RUN_SCHEMA, canonical, constraints, decode, definition,
    digest, pilot_input, require, signed, read_run, _ordinary_file)
from .animation_capabilities import capability_release, validate_release, DEFAULT_RELEASE_ID


SLOTS = ('development-v1', 'development-v2', 'held-out-v1', 'held-out-v2')
RESERVATION_NAME = 'candidate-reservation.json'
RESERVATION_SCHEMA = 'video.animation-experiment-candidate-reservation.v1'
EXPECTED_BINDING_NAME = 'expected-candidate-binding.json'
BUILD_RECEIPT_NAME = 'candidate-build-receipt.json'
FAILURE_CLOSURE_NAME = 'candidate-failure-closure.json'
RECOVERY_ORIGIN_NAME = 'recovery-origin.json'
FAILURE_FILES = ('candidate-request.json', 'failure-diagnosis.json', 'failure-cleanup-summary.json',
                 'failure-worker-pod.json', 'failure-proxy-stopped.json')


def _pilot_lock(function):
    @wraps(function)
    def execute(directory, *args, **kwargs):
        root, _ = _load(directory)
        lock = root / '.pilot.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ServiceError('animation_experiment_pilot_busy', 409) from None
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(str(os.getpid()).encode())
            return function(directory, *args, **kwargs)
        finally:
            lock.unlink()
    return execute


class ExperimentGateway(MotionAgentGateway):
    profile, input_name, input_role, result_name = PROFILE, INPUT_NAME, 'animation_experiment_input', RESULT_NAME
    business_prefix, idempotency_prefix = 'animation-experiment:', 'animation-experiment-'
    input_limit, output_limit = INPUT_MAX_BYTES, OUTPUT_MAX_BYTES
    validate_input = staticmethod(validate_experiment_input)
    validate_result = staticmethod(validate_experiment_result)


def _write(path, document, *, exclusive=False):
    path = Path(path)
    if exclusive:
        with path.open('xb') as output:
            output.write(canonical(document))
            output.flush()
            os.fsync(output.fileno())
    else:
        temporary = path.with_name('.' + path.name + '.new')
        with temporary.open('wb') as output:
            output.write(canonical(document))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)


def _immutable_bytes(path, raw):
    path = Path(path)
    if path.exists():
        require(_ordinary_file(path.parent, path.name, 3 * 1024 * 1024) == raw,
                'animation_experiment_immutable_evidence_changed')
        return
    with path.open('xb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def _candidate_build(binding_raw, receipt_raw):
    """Bind the expected worker to inspected build and local prompt sources."""
    binding = ExecutionBindingPins.model_validate(decode(binding_raw))
    receipt = decode(receipt_raw)
    local_source = digest(Path(experiment_profile.__file__).read_bytes())
    require(type(receipt) is dict and receipt.get('schema') == 'animation-agent-overlay-build.v1'
            and type(receipt.get('image')) is str
            and re.fullmatch(r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', receipt['image'])
            and receipt['image'].rsplit('@', 1)[1] == binding.image_digest
            and binding.profile == PROFILE and binding.profile_revision == '1'
            and binding.bundle_name == 'animation-experiment'
            and binding.bundle_revision == experiment_profile.BUNDLE_REVISION
            and receipt.get('new_files', {}).get('profiles/video_animation_experiment.py') == local_source
            and receipt['new_files'].get('resources/animation-experiment-v1/manifest.json')
                == experiment_profile.BUNDLE_MANIFEST_SHA256,
            'animation_experiment_candidate_build_mismatch')
    return binding, receipt, local_source


def _read_model_policy(path):
    if path is None:
        return None
    from agent_service.deploy.candidate_scenario import _read
    from agent_service.registry import ModelPolicy
    path = Path(path)
    return ModelPolicy.model_validate(decode(_read(path.parent, path.name, 65536))).model_dump(mode='json')


def _bind_model_policy(record, policy, pins):
    if policy is None:
        return
    binding = pins.get('execution_binding', pins) if type(pins) is dict else {}
    require(binding.get('model_policy_sha256') == digest(policy)
            and binding.get('model_policy_revision') == policy['revision'],
            'animation_experiment_model_policy_binding_mismatch')
    record['provenance'].update(declared_model_policy=deepcopy(policy),
        declared_model_policy_sha256=digest(policy), requested_model=policy['model'],
        requested_model_note='Operator-declared request policy; not a returned or observed model identity.')


def _new_plan(run_prefix='identity', recovery=None, *, capability_release_id=None):
    plan = dict(schema='video.animation-experiment-pilot.v1', experiment=definition(),
        constraints={role: constraints(role) for role in ('development', 'held_out')},
        constraint_identities={role: digest(constraints(role)) for role in ('development', 'held_out')},
        max_tasks=4, max_attempts_per_task=1,
        guidance={'v1': '', 'v2': None}, feedback=None,
        slots={slot: dict(state='unstarted', run_id=run_prefix + '-' + slot) for slot in SLOTS})
    if recovery is not None:
        plan['recovery'] = deepcopy(recovery)
    if capability_release_id is not None:
        plan['capability_release'] = capability_release(capability_release_id)
    return plan


def initialize(directory, *, capability_release_id=None):
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=False)
    plan = _new_plan(capability_release_id=capability_release_id)
    _write(root / 'pilot.json', plan, exclusive=True)
    return plan


def _load(root):
    root = Path(root).absolute()
    require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)),
            'animation_experiment_directory_invalid')
    plan = decode((root / 'pilot.json').read_bytes())
    require(plan.get('schema') == 'video.animation-experiment-pilot.v1'
            and plan.get('experiment') == definition() and set(plan['slots']) == set(SLOTS)
            and plan['max_tasks'] == 4 and plan['max_attempts_per_task'] == 1
            and all(type(row) is dict and type(row.get('run_id')) is str
                    and re.fullmatch('[a-z][a-z0-9-]{0,63}', row['run_id']) for row in plan['slots'].values()),
            'animation_experiment_plan_changed')
    for role in ('development', 'held_out'):
        require(plan['constraints'][role] == constraints(role)
                and plan['constraint_identities'][role] == digest(constraints(role)),
                'animation_experiment_plan_changed')
    if 'capability_release' in plan:
        validate_release(plan['capability_release'])
    if 'recovery' in plan:
        recovery = plan['recovery']
        require(type(recovery) is dict and recovery.get('schema') == 'video.animation-experiment-recovery.v1'
                and recovery.get('identity') == digest({k: v for k, v in recovery.items() if k != 'identity'})
                and recovery.get('new_budget') == {'max_tasks': 4, 'max_attempts_per_task': 1,
                    'automatic_retry': False, 'original_attempt_still_consumed': True}
                and recovery.get('cumulative_candidate_budget') == 5
                and recovery.get('original_unstarted_slots_abandoned') == 3
                and _ordinary_file(root, RECOVERY_ORIGIN_NAME, INPUT_MAX_BYTES) == canonical(recovery)
                and re.fullmatch('recovery-[0-9a-f]{12}', recovery.get('run_prefix', ''))
                and set(recovery.get('parent_snapshots_sha256', {})) == {'recovery-parent-plan.json',
                    'recovery-parent-run.json', 'recovery-parent-report.json', 'recovery-parent-closure.json'}
                and all(digest(_ordinary_file(root, name, 3 * 1024 * 1024)) == value
                        for name, value in recovery['parent_snapshots_sha256'].items())
                and all(plan['slots'][name]['run_id'] == recovery['run_prefix'] + '-' + name for name in SLOTS),
                'animation_experiment_recovery_changed')
    return root, plan


def _plan_input(plan, role='development', revision='v1', guidance=''):
    release = plan.get('capability_release')
    return pilot_input(role, revision, guidance,
        capability_release_id=None if release is None else validate_release(release)['id'])


@_pilot_lock
def revise(directory, guidance, feedback):
    root, plan = _load(directory)
    require(plan['slots']['development-v1']['state'] == 'finished'
            and plan['guidance']['v2'] is None and all(plan['slots'][s]['state'] == 'unstarted' for s in SLOTS[1:]),
            'animation_experiment_revision_not_ready', 409)
    require(type(guidance) is str and 0 < len(guidance.strip()) <= 16000
            and type(feedback) is str and 0 < len(feedback.strip()) <= 16000,
            'animation_experiment_feedback_invalid')
    plan['guidance']['v2'] = guidance
    plan['feedback'] = dict(comment=feedback, basis_run_id=plan['slots']['development-v1']['run_id'],
        scope='developer_technical_direction_not_user_aesthetic_approval', created_at=time.time())
    _write(root / 'pilot.json', plan)
    return plan


def _publish_record(root, slot, document):
    _write(root / slot['run_id'] / 'experiment-run.json', signed(document))


def _record(slot, document, role):
    revision = document['prompt_revision']
    record = dict(schema=RUN_SCHEMA, id=slot['run_id'], experiment_id=definition()['id'],
        experiment_identity=definition()['identity'], source='agent', input_role=role,
        input_document=document, input_sha256=digest(document),
        label=('开发输入' if role == 'development' else '未参与调优输入') + ' · ' + revision,
        prompt_text=VideoAnimationExperimentProfile().build_prompt({'input_sha256': digest(document)}),
        status='failed', provenance=dict(profile=PROFILE,
            created_at=slot.get('created_at'),
            requested_model=None, returned_model=None, returned_model_note='服务未返回实际模型名称时保持未知。',
            generation_context='profile_prompt_plus_exact_input_document',
            automatic_retries=False), artifacts={}, code_files={}, strategy={})
    if release := document['constraints'].get('capability_release'):
        record['provenance']['capability_release_id'] = release['id']
        record['provenance']['capability_release_sha256'] = release['sha256']
    return record


def _attach_recovery(record, plan):
    if 'recovery' in plan:
        record['provenance']['recovery'] = deepcopy(plan['recovery'])


def _reserved_candidate(root, slot, record, *, slot_name='development-v1'):
    run_dir, document = root / slot['run_id'], record['input_document']
    raw = _ordinary_file(run_dir, RESERVATION_NAME, INPUT_MAX_BYTES)
    reservation = decode(raw)
    require(digest(raw) == slot['candidate_reservation_sha256']
            and reservation.get('schema') == RESERVATION_SCHEMA
            and reservation.get('slot') == slot_name and reservation.get('run_id') == slot['run_id']
            and reservation.get('profile') == PROFILE and reservation.get('profile_revision') == '1'
            and reservation.get('max_generation_attempts') == 1 and reservation.get('automatic_retries') is False
            and reservation.get('input_sha256') == slot['input_sha256'] == digest(document)
            and reservation.get('prompt_text') == record['prompt_text']
            and reservation.get('prompt_sha256') == digest(record['prompt_text'].encode('utf-8')),
            'animation_experiment_candidate_reservation_changed')
    require(_ordinary_file(run_dir, INPUT_NAME, INPUT_MAX_BYTES) == canonical(document),
            'animation_experiment_candidate_input_changed')
    binding_raw = _ordinary_file(run_dir, EXPECTED_BINDING_NAME, INPUT_MAX_BYTES)
    receipt_raw = _ordinary_file(run_dir, BUILD_RECEIPT_NAME, INPUT_MAX_BYTES)
    binding, receipt, source_sha = _candidate_build(binding_raw, receipt_raw)
    require(digest(binding_raw) == reservation['expected_binding_sha256']
            and digest(receipt_raw) == reservation['build_receipt_sha256']
            and source_sha == reservation['profile_source_sha256']
            and receipt['image'] == reservation['worker_image']
            and binding.sha256 == reservation['execution_binding_sha256'],
            'animation_experiment_candidate_reservation_changed')
    return reservation, binding, receipt, source_sha


def _slot_document(root, plan, slot_name, *, importing=False):
    """Share admission order and frozen-input checks across both transports."""
    require('recovery_child' not in plan, 'animation_experiment_pilot_superseded', 409)
    require(slot_name in SLOTS, 'animation_experiment_slot_invalid')
    slot = plan['slots'][slot_name]
    require(slot['state'] == ('candidate_reserved' if importing else 'unstarted'),
            'animation_experiment_candidate_not_reserved' if importing
            else 'animation_experiment_attempt_already_reserved', 409)
    revision = 'v2' if slot_name.endswith('v2') else 'v1'
    role = 'held_out' if slot_name.startswith('held-out') else 'development'
    require(plan['guidance']['v1'] == '' and (revision != 'v2' or plan['guidance']['v2'] is not None),
            'animation_experiment_revision_not_ready', 409)
    if role == 'held_out':
        require(plan['slots']['development-v2']['state'] == 'finished',
                'animation_experiment_holdout_not_released', 409)
        developed = read_run(root / plan['slots']['development-v2']['run_id'])
        require(developed['input_document'] == _plan_input(plan, 'development', 'v2', plan['guidance']['v2']),
                'animation_experiment_frozen_guidance_changed', 409)
    document = _plan_input(plan, role, revision, plan['guidance'][revision])
    validate_experiment_input(document)
    return slot, document, role


def _candidate_comparison_baseline(root, plan):
    """Bind subsequent isolated executions to the already reviewed first run.

    This does not enable a shared route or authorize any new Agent launch. A
    different worker, source package, policy, asset set or renderer is a new
    experiment, not an interchangeable prompt comparison.
    """
    first = plan['slots']['development-v1']
    require(first['state'] == 'finished', 'animation_experiment_baseline_not_ready', 409)
    row = read_run(root / first['run_id'])
    require(row['status'] == 'rendered' and row['input_document'] == _plan_input(plan),
            'animation_experiment_baseline_changed', 409)
    reservation, _, _, _ = _reserved_candidate(root, first, row)
    report = decode(_ordinary_file(root / first['run_id'], 'report.json', 3 * 1024 * 1024))
    renderer = report.get('renderer', {})
    host_sha = renderer.get('receipt', {}).get('host_sha256')
    require(type(renderer.get('image')) is str
            and re.fullmatch(r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', renderer['image'])
            and type(host_sha) is str and re.fullmatch(r'[0-9a-f]{64}', host_sha),
            'animation_experiment_baseline_renderer_missing', 409)
    return dict(run_id=row['id'], run_identity=row['identity'],
        expected_binding_sha256=reservation['expected_binding_sha256'],
        build_receipt_sha256=reservation['build_receipt_sha256'],
        declared_model_policy=reservation.get('declared_model_policy'),
        renderer_image=renderer['image'], renderer_host_sha256=host_sha,
        assets=deepcopy(row['input_document']['assets']))


def _candidate_input_bound(envelope, binding, document):
    profile = VideoAnimationExperimentProfile()
    require(envelope.profile == PROFILE and envelope.profile_revision == '1'
            and envelope.execution_binding == binding and envelope.image_digest == binding.image_digest
            and envelope.task_schema == profile.task_schema and envelope.output_schema == profile.output_schema
            and envelope.task == {'input_sha256': digest(document)}
            and len(envelope.inputs) == 1 and envelope.inputs[0].name == INPUT_NAME
            and envelope.inputs[0].sha256 == digest(document)
            and envelope.inputs[0].content(limit=INPUT_MAX_BYTES) == canonical(document)
            and envelope.checkpoint_ref is None and not envelope.checkpoint_inputs
            and all(getattr(envelope, name) == getattr(profile.limits, name) for name in (
                'max_input_bytes', 'max_input_total_bytes', 'max_input_files',
                'max_artifact_bytes', 'max_artifact_file_bytes', 'max_artifact_files')),
            'animation_experiment_candidate_binding_mismatch')


def _render_record(root, plan, slot, record, result, *, renderer_image, assets_dir):
    run_dir, document = root / slot['run_id'], record['input_document']
    bundle = run_dir / 'bundle'
    bundle.mkdir()
    for name, raw in scene_files(result).items():
        (bundle / name).write_bytes(raw)
    record.update(status='generated', code_files=deepcopy(result['files']), strategy=deepcopy(result['strategy']))
    _publish_record(root, slot, record)
    from .animation_experiment_render import render_scene
    render = render_scene(bundle, run_dir / 'render', image=renderer_image,
                          input_document=document, assets_dir=assets_dir)
    frames = sorted((run_dir / 'render' / 'work' / 'frames').glob('frame-*.jpg'))
    require(len(frames) <= 240, 'animation_experiment_frame_limit')
    published_frames = []
    if frames:
        (run_dir / 'frames').mkdir()
    for frame in frames:
        require(re.fullmatch(r'frame-[0-9]{6}\.jpg', frame.name), 'animation_experiment_frame_invalid')
        index = int(frame.stem.removeprefix('frame-'))
        raw = _ordinary_file(frame.parent, frame.name, 2 * 1024 * 1024)
        name = 'frames/%06d.jpg' % index
        (run_dir / name).write_bytes(raw)
        record['artifacts'][name] = dict(size=len(raw), sha256=digest(raw), mime='image/jpeg')
        published_frames.append(dict(index=index, seconds=index / 24, artifact=name))
    report = dict(schema='video.animation-experiment-report.v1',
        input_sha256=digest(document), agent_job_id=record['provenance'].get('job_id'), renderer=render,
        published_frames=published_frames,
        feedback_basis=deepcopy(plan['feedback']) if document['prompt_revision'] == 'v2' else None,
        technical_scope='independent_geometry_and_runtime_not_aesthetic_approval',
        aesthetic_status='unreviewed', generation_attempts=1)
    _write(run_dir / 'report.json', report, exclusive=True)
    media = run_dir / 'render' / 'work' / 'output.mp4'
    require(media.is_file(), 'animation_experiment_media_missing')
    (run_dir / 'output.mp4').write_bytes(media.read_bytes())
    for name, mime in (('output.mp4', 'video/mp4'), ('report.json', 'application/json')):
        raw = (run_dir / name).read_bytes()
        record['artifacts'][name] = dict(size=len(raw), sha256=digest(raw), mime=mime)
    record.update(status='rendered')
    slot['state'] = 'finished'


def _fail(root, plan, slot, record, error):
    # Fixed diagnostics keep arbitrary provider text and secrets unpublished.
    code = getattr(error, 'code', 'animation_experiment_execution_failed')
    job_id = record['provenance'].get('job_id')
    record.update(status='failed', error=code)
    report_path = root / slot['run_id'] / 'report.json'
    if not report_path.exists():
        _write(report_path, dict(schema='video.animation-experiment-report.v1',
            input_sha256=record['input_sha256'], agent_job_id=job_id,
            renderer=getattr(error, 'outcome', {}), error=code,
            technical_scope='execution_failure_not_aesthetic_approval', aesthetic_status='unreviewed'), exclusive=True)
    raw = report_path.read_bytes()
    record['artifacts']['report.json'] = dict(size=len(raw), sha256=digest(raw), mime='application/json')
    slot.update(state='failed', error=code, job_id=job_id)
    _publish_record(root, slot, record)
    _write(root / 'pilot.json', plan)


@_pilot_lock
def reserve_candidate(directory, *, expected_binding_file, build_receipt, model_policy_file=None,
                      slot_name='development-v1'):
    """Reserve one named pilot slot before an explicit isolated execution.

    No Agent upload, admission or rendering occurs. A crash or later failed
    import leaves this slot consumed. The initial acceptance and later isolated
    executions share the same four-slot budget; no task is added or retried.
    """
    root, plan = _load(directory)
    slot, document, _ = _slot_document(root, plan, slot_name)
    if slot_name == 'development-v1':
        require(all(plan['slots'][name]['state'] == 'unstarted' for name in SLOTS)
                and plan['guidance'] == {'v1': '', 'v2': None},
                'animation_experiment_attempt_already_reserved', 409)
    from agent_service.deploy.candidate_scenario import _read
    expected_binding_file, build_receipt = Path(expected_binding_file), Path(build_receipt)
    binding_raw = _read(expected_binding_file.parent, expected_binding_file.name)
    receipt_raw = _read(build_receipt.parent, build_receipt.name)
    binding, receipt, source_sha = _candidate_build(binding_raw, receipt_raw)
    policy = _read_model_policy(model_policy_file)
    _bind_model_policy({'provenance': {}}, policy, binding.model_dump(mode='json'))
    baseline = None if slot_name == 'development-v1' else _candidate_comparison_baseline(root, plan)
    if baseline is not None:
        require(digest(binding_raw) == baseline['expected_binding_sha256']
                and digest(receipt_raw) == baseline['build_receipt_sha256']
                and policy == baseline['declared_model_policy'] and document['assets'] == baseline['assets'],
                'animation_experiment_comparison_environment_changed', 409)
    prompt = VideoAnimationExperimentProfile().build_prompt({'input_sha256': digest(document)})
    reservation = dict(schema=RESERVATION_SCHEMA, reservation_id=str(uuid4()),
        slot=slot_name, run_id=slot['run_id'], profile=PROFILE, profile_revision='1',
        input_sha256=digest(document), prompt_text=prompt, prompt_sha256=digest(prompt.encode('utf-8')),
        expected_binding_sha256=digest(binding_raw), build_receipt_sha256=digest(receipt_raw),
        profile_source_sha256=source_sha, worker_image=receipt['image'],
        execution_binding_sha256=binding.sha256, declared_model_policy=policy,
        created_at=time.time(), max_generation_attempts=1, automatic_retries=False)
    if baseline is not None:
        reservation['comparison_baseline'] = baseline
    run_dir = root / slot['run_id']
    run_dir.mkdir()
    slot.update(state='candidate_reserved', input_sha256=digest(document),
        candidate_reservation_sha256=digest(reservation), created_at=reservation['created_at'])
    # Persist before writing sources. An incomplete reservation can only fail;
    # it cannot reauthorize this slot or trigger another candidate job.
    _write(root / 'pilot.json', plan)
    _write(run_dir / INPUT_NAME, document, exclusive=True)
    _write(run_dir / RESERVATION_NAME, reservation, exclusive=True)
    for name, raw in ((EXPECTED_BINDING_NAME, binding_raw), (BUILD_RECEIPT_NAME, receipt_raw)):
        with (run_dir / name).open('xb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    return dict(reservation, input_path=str(run_dir / INPUT_NAME))


def _failed_pod(document, *, namespace, uid, image_digest, failed_worker=False):
    metadata, spec, state = document['metadata'], document['spec'], document['status']
    require(metadata.get('namespace') == namespace and metadata.get('uid') == uid
            and spec.get('restartPolicy') == 'Never'
            and all(spec.get(key, False) is False for key in ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace'))
            and not spec.get('initContainers') and not spec.get('ephemeralContainers')
            and not state.get('initContainerStatuses') and not state.get('ephemeralContainerStatuses'),
            'animation_experiment_failure_stop_unverified')
    containers, statuses = spec.get('containers', []), state.get('containerStatuses', [])
    require(len(containers) == len(statuses) == 1 and containers[0]['name'] == statuses[0]['name'],
            'animation_experiment_failure_stop_unverified')
    container, observed = containers[0], statuses[0]
    ended = observed.get('state', {}).get('terminated', {})
    require(type(container.get('image')) is str and container['image'].endswith('@' + image_digest)
            and type(observed.get('imageID')) is str and observed['imageID'].endswith('@' + image_digest)
            and observed.get('restartCount') == 0 and type(ended.get('exitCode')) is int
            and 0 <= ended['exitCode'] <= 255 and (not failed_worker or ended['exitCode'] != 0),
            'animation_experiment_failure_stop_unverified')
    try:
        finished = datetime.fromisoformat(ended['finishedAt'].replace('Z', '+00:00'))
        require(finished.tzinfo is not None, 'animation_experiment_failure_stop_unverified')
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ServiceError('animation_experiment_failure_stop_unverified', 422) from None


@_pilot_lock
def close_failed_candidate(directory, evidence_dir):
    """Close one consumed package-readiness failure without inventing a result.

    Evidence is a root-owned diagnosis and exact cleanup/Pod capture, not a
    completed WorkerResult or an offline HMAC receipt. Explicit continuation of
    this administrative close may finish its immutable writes after a crash;
    it cannot submit, restart, render or release a generation slot.
    """
    from agent_service.deploy.candidate_scenario import _read, _json

    root, plan = _load(directory)
    slot = plan['slots']['development-v1']
    require(slot['state'] in {'candidate_reserved', 'candidate_closing'}
            and all(plan['slots'][name]['state'] == 'unstarted' for name in SLOTS[1:]),
            'animation_experiment_failure_close_not_ready', 409)
    document = _plan_input(plan)
    record = _record(slot, document, 'development')
    _attach_recovery(record, plan)
    reservation, binding, _, source_sha = _reserved_candidate(root, slot, record)
    require(not any((Path(evidence_dir) / name).exists() or (Path(evidence_dir) / name).is_symlink()
                    for name in ('candidate-result.json', 'candidate-evidence.json', RESULT_NAME)),
            'animation_experiment_failure_has_output')
    raw = {name: _read(evidence_dir, name, 3 * 1024 * 1024) for name in FAILURE_FILES}
    docs = {name: _json(value) for name, value in raw.items()}
    request, diagnosis, cleanup, worker, proxy = (docs[name] for name in FAILURE_FILES)
    require(type(request) is dict and set(request) == {'schema_version', 'candidate_scope_only', 'envelope'}
            and request['schema_version'] == 'agent-candidate-request.v1' and request['candidate_scope_only'] is True,
            'animation_experiment_failure_request_invalid')
    envelope = ExecutionEnvelope.model_validate(request['envelope'])
    _candidate_input_bound(envelope, binding, document)
    require(diagnosis.get('status') == 'failed_before_agent_start'
            and diagnosis.get('code') == 'package_resources_incomplete'
            and diagnosis.get('request_sha256') == envelope.request_sha256
            and diagnosis.get('attempt_id') == envelope.attempt_id
            and diagnosis.get('worker_stopped') is True and diagnosis.get('worker_restarts') == 0
            and diagnosis.get('agent_generation_started') is False
            and diagnosis.get('only_expected_connectivity_request') is True
            and diagnosis.get('model_request_count') == 1 and diagnosis.get('retry_performed') is False
            and diagnosis.get('pilot_slot_consumed') is True and diagnosis.get('animation_result_created') is False,
            'animation_experiment_failure_diagnosis_invalid')
    calls = diagnosis.get('model_calls')
    require(type(calls) is list and len(calls) == 1 and calls[0].get('attempt_id') == envelope.attempt_id
            and calls[0].get('state') == 'complete' and calls[0].get('status') == 200
            and all(type(calls[0].get(name)) is str and re.fullmatch('[0-9a-f]{64}', calls[0][name])
                    for name in ('body_sha256', 'response_sha256'))
            and type(calls[0].get('response_bytes')) is int and calls[0]['response_bytes'] > 0,
            'animation_experiment_failure_model_evidence_invalid')
    require(cleanup.get('status') == 'failed_candidate_closed'
            and cleanup.get('worker_stopped') is True and cleanup.get('proxy_stopped') is True
            and cleanup.get('model_request_count') == 1 and cleanup.get('creative_agent_started') is False
            and cleanup.get('animation_result_created') is False and cleanup.get('retry_performed') is False
            and cleanup.get('slot_still_consumed') is True and cleanup.get('uid_reservation_retained') is True
            and cleanup.get('production_routes_changed') is False,
            'animation_experiment_failure_cleanup_invalid')
    _failed_pod(worker, namespace='agent-workers', uid=diagnosis.get('worker_pod_uid'),
                image_digest=binding.image_digest, failed_worker=True)
    _failed_pod(proxy, namespace='agent-service', uid=diagnosis.get('proxy_pod_uid'), image_digest=binding.image_digest)
    worker_name, proxy_name = worker['metadata'].get('name', ''), proxy['metadata'].get('name', '')
    name = worker_name.removesuffix('-worker')
    require(worker_name == name + '-worker' and proxy_name == name + '-proxy'
            and re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', name)
            and worker['metadata']['uid'] != proxy['metadata']['uid'],
            'animation_experiment_failure_cleanup_invalid')
    expected = {('ConfigMap', 'agent-service', name), ('ConfigMap', 'agent-workers', name),
        ('Service', 'agent-service', name), ('NetworkPolicy', 'agent-service', name),
        ('Pod', 'agent-service', proxy_name), ('Pod', 'agent-workers', worker_name),
        ('Secret', 'agent-service', name + '-bootstrap'), ('Secret', 'agent-workers', name + '-bootstrap'),
        ('Secret', 'agent-service', name + '-upstream')}
    absent = cleanup.get('exact_resources_absent')
    require(type(absent) is list and len(absent) == len(expected)
            and all(type(row) is dict and set(row) == {'kind', 'namespace', 'name'} for row in absent)
            and {(row['kind'], row['namespace'], row['name']) for row in absent} == expected,
            'animation_experiment_failure_cleanup_invalid')
    run_dir = root / slot['run_id']
    require(not (run_dir / RESULT_NAME).exists() and not (run_dir / 'bundle').exists()
            and not (run_dir / 'output.mp4').exists(), 'animation_experiment_failure_has_output')
    hashes = {name: digest(value) for name, value in raw.items()}
    if slot['state'] == 'candidate_closing':
        intent = slot['failure_close_intent']
        require(intent['evidence_sha256'] == hashes and intent['attempt_id'] == envelope.attempt_id,
                'animation_experiment_failure_closure_changed')
    else:
        intent = dict(closure_id=str(uuid4()), created_at=time.time(), evidence_sha256=hashes,
                      attempt_id=envelope.attempt_id)
        slot.update(state='candidate_closing', failure_close_intent=intent)
        _write(root / 'pilot.json', plan)
    captured = run_dir / 'failure-evidence'
    captured.mkdir(exist_ok=True)
    for filename, value in raw.items():
        _immutable_bytes(captured / filename, value)
    closure = dict(schema='video.animation-experiment-candidate-failure.v1', **intent,
        run_id=slot['run_id'], input_sha256=digest(document), job_id=envelope.job_id,
        request_sha256=envelope.request_sha256, execution_binding_sha256=binding.sha256,
        failure_phase='package_readiness_before_creative_agent_start', error='package_resources_incomplete',
        evidence_scope='root_owned_operator_diagnosis_and_independently_captured_stopped_pods_no_protocol_receipt',
        worker_pod_uid=worker['metadata']['uid'], proxy_pod_uid=proxy['metadata']['uid'],
        connectivity_model_requests=1, creative_agent_starts=0, animation_results=0,
        original_attempt_still_consumed=True, cleanup_verified=True, automatic_retry=False)
    _immutable_bytes(run_dir / FAILURE_CLOSURE_NAME, canonical(closure))
    _bind_model_policy(record, reservation.get('declared_model_policy'), binding.model_dump(mode='json'))
    record['provenance'].update(job_id=envelope.job_id, state='failed_before_agent_start',
        attempt=dict(attempt_id=envelope.attempt_id, fence=envelope.fence), pins=binding.model_dump(mode='json'),
        request_sha256=envelope.request_sha256, profile_source_sha256=source_sha,
        admission='external_candidate_consumed_failed_before_creative_agent_start',
        stop_proven=False, protocol_stop_receipt_available=False, worker_and_proxy_observed_stopped=True,
        candidate_failure_closure_sha256=digest(closure), connectivity_model_requests=1,
        creative_agent_starts=0, animation_results=0, model_calls=deepcopy(calls))
    _immutable_bytes(run_dir / 'report.json', canonical(dict(schema='video.animation-experiment-report.v1',
        input_sha256=digest(document), agent_job_id=envelope.job_id, error=closure['error'],
        renderer={'status': 'not_started'}, candidate_failure=closure,
        generation_attempts=0, candidate_launch_attempts=1, connectivity_model_requests=1,
        technical_scope='package_preflight_failure_not_a_completed_creative_result', aesthetic_status='unreviewed')))
    slot['failure_closure_sha256'] = digest(closure)
    _fail(root, plan, slot, record, ServiceError(closure['error'], 503))
    return signed(record)


@_pilot_lock
def create_recovery(directory, new_directory, *, authorization_note, max_tasks=4):
    """Declare a separately bounded pilot while retaining the consumed failure.

    This is an explicit operator action, not automatic task recovery. It makes
    no Agent calls, and a parent can reserve only one linked successor pilot.
    """
    root, plan = _load(directory)
    slot = plan['slots']['development-v1']
    require('recovery' not in plan and slot['state'] == 'failed' and slot.get('failure_closure_sha256')
            and all(plan['slots'][name]['state'] == 'unstarted' for name in SLOTS[1:]),
            'animation_experiment_recovery_not_ready', 409)
    require(type(authorization_note) is str and 0 < len(authorization_note.strip()) <= 4000
            and type(max_tasks) is int and max_tasks == 4, 'animation_experiment_recovery_budget_invalid')
    target = Path(new_directory).absolute()
    require(target.parent == root.parent and target != root and not target.is_symlink(),
            'animation_experiment_recovery_directory_invalid')
    run_dir = root / slot['run_id']
    record = read_run(run_dir)
    closure_raw = _ordinary_file(run_dir, FAILURE_CLOSURE_NAME, INPUT_MAX_BYTES)
    closure = decode(closure_raw)
    require(record['status'] == 'failed' and record.get('error') == 'package_resources_incomplete'
            and digest(closure_raw) == slot['failure_closure_sha256']
            and closure.get('schema') == 'video.animation-experiment-candidate-failure.v1'
            and closure.get('original_attempt_still_consumed') is True and closure.get('cleanup_verified') is True
            and closure.get('job_id') == record['provenance'].get('job_id')
            and closure.get('attempt_id') == record['provenance'].get('attempt', {}).get('attempt_id'),
            'animation_experiment_recovery_failure_changed')
    snapshots = {'recovery-parent-run.json': _ordinary_file(run_dir, 'experiment-run.json', 3 * 1024 * 1024),
        'recovery-parent-report.json': _ordinary_file(run_dir, 'report.json', 3 * 1024 * 1024),
        'recovery-parent-closure.json': closure_raw}
    if 'recovery_child' in plan:
        child = plan['recovery_child']
        recovery = child['origin']
        require(child['directory'] == str(target) and recovery['authorization_note'] == authorization_note,
                'animation_experiment_recovery_already_reserved', 409)
        snapshots['recovery-parent-plan.json'] = _ordinary_file(root, 'recovery-parent-plan.json', INPUT_MAX_BYTES)
        require({name: digest(value) for name, value in snapshots.items()} == recovery['parent_snapshots_sha256'],
                'animation_experiment_recovery_failure_changed')
    else:
        require(not target.exists(), 'animation_experiment_recovery_already_reserved', 409)
        snapshots['recovery-parent-plan.json'] = canonical(plan)
        recovery_id = uuid4().hex
        recovery = signed(dict(schema='video.animation-experiment-recovery.v1', recovery_id=recovery_id,
            created_at=time.time(), run_prefix='recovery-' + recovery_id[:12], authorization_note=authorization_note,
            parent_pilot=str(root), parent_run_id=record['id'], parent_run_identity=record['identity'],
            parent_attempt_id=closure['attempt_id'], parent_job_id=closure['job_id'],
            parent_request_sha256=closure['request_sha256'], parent_failure_closure_sha256=digest(closure_raw),
            prior_candidate_launches=1, prior_connectivity_model_requests=1, prior_creative_agent_starts=0,
            cumulative_candidate_budget=5, original_unstarted_slots_abandoned=3,
            parent_snapshots_sha256={name: digest(value) for name, value in snapshots.items()},
            new_budget=dict(max_tasks=4, max_attempts_per_task=1, automatic_retry=False,
                            original_attempt_still_consumed=True)))
        _immutable_bytes(root / 'recovery-parent-plan.json', snapshots['recovery-parent-plan.json'])
        plan['recovery_child'] = dict(state='reserved', directory=str(target), origin=recovery)
        _write(root / 'pilot.json', plan)
    target.mkdir(exist_ok=True)
    for name, value in snapshots.items():
        _immutable_bytes(target / name, value)
    _immutable_bytes(target / RECOVERY_ORIGIN_NAME, canonical(recovery))
    if not (target / 'pilot.json').exists():
        release = plan.get('capability_release')
        _write(target / 'pilot.json', _new_plan(recovery['run_prefix'], recovery,
            capability_release_id=None if release is None else validate_release(release)['id']), exclusive=True)
    _, recovered = _load(target)
    require(recovered.get('recovery') == recovery, 'animation_experiment_recovery_changed')
    plan['recovery_child']['state'] = 'initialized'
    _write(root / 'pilot.json', plan)
    return recovered


@_pilot_lock
def complete_candidate(directory, candidate_dir, *, renderer_image, assets_dir=None,
                       slot_name='development-v1'):
    """Import one stopped, operator-captured candidate into its reserved slot.

    This path never constructs an AgentClient or submits/retries a task. Root-
    owned authority evidence and the independently captured stopped Pod are
    rechecked; the ephemeral HMAC key is deliberately not exported or reverified.
    """
    from agent_service.deploy.candidate_scenario import PARENT_FILES, _read, _json, verify_parent

    root, plan = _load(directory)
    slot, document, role = _slot_document(root, plan, slot_name, importing=True)
    slot['state'] = 'candidate_importing'
    _write(root / 'pilot.json', plan)
    record = _record(slot, document, role)
    _attach_recovery(record, plan)
    record['provenance']['admission'] = (
        'external_acceptance_candidate_reused_in_reserved_pilot_slot' if slot_name == 'development-v1'
        else 'explicit_isolated_pilot_execution_not_shared_route_admission')
    run_dir = root / slot['run_id']
    try:
        reservation, binding, receipt, source_sha = _reserved_candidate(root, slot, record, slot_name=slot_name)
        if slot_name != 'development-v1':
            from .animation_experiment_render import HOST
            baseline = _candidate_comparison_baseline(root, plan)
            require(reservation.get('comparison_baseline') == baseline
                    and renderer_image == baseline['renderer_image']
                    and digest(HOST.read_bytes()) == baseline['renderer_host_sha256']
                    and document['assets'] == baseline['assets'],
                    'animation_experiment_comparison_environment_changed', 409)
            record['provenance']['comparison_baseline'] = baseline
        captured = run_dir / 'candidate-evidence'
        captured.mkdir()
        documents, hashes = {}, {}
        for name in PARENT_FILES.values():
            evidence_raw = _read(candidate_dir, name)
            # Keep the exact captured bytes even when a subsequent check fails.
            with (captured / name).open('xb') as output:
                output.write(evidence_raw)
                output.flush()
                os.fsync(output.fileno())
            hashes[name] = digest(evidence_raw)
            documents[name] = _json(evidence_raw)
        record['provenance']['candidate_files_sha256'] = hashes
        envelope, result = verify_parent(documents)
        for other_name, other_slot in plan['slots'].items():
            if other_name == slot_name:
                continue
            other_dir = root / other_slot['run_id']
            if (other_dir / 'experiment-run.json').exists():
                other = read_run(other_dir)['provenance']
                require(other.get('job_id') != envelope.job_id
                        and other.get('attempt', {}).get('attempt_id') != envelope.attempt_id,
                        'animation_experiment_candidate_execution_reused', 409)
        _bind_model_policy(record, reservation.get('declared_model_policy'),
                           envelope.execution_binding.model_dump(mode='json'))
        _candidate_input_bound(envelope, binding, document)
        require(result.stop_receipt.fence == envelope.fence and result.stop_receipt.engine == envelope.engine,
                'animation_experiment_candidate_binding_mismatch')
        require(len(result.artifacts) == 1 and result.artifacts[0].name == RESULT_NAME,
                'animation_experiment_candidate_artifact_mismatch')
        artifact = result.artifacts[0]
        output = validate_experiment_result(decode(artifact.content(limit=OUTPUT_MAX_BYTES)), digest(document), document)
        _write(run_dir / RESULT_NAME, output, exclusive=True)
        record['provenance'].update(job_id=envelope.job_id, state=result.state,
            attempt=dict(attempt_id=envelope.attempt_id, fence=envelope.fence),
            pins=envelope.execution_binding.model_dump(mode='json'), stop_proven=True,
            build_receipt_sha256=reservation['build_receipt_sha256'], profile_source_sha256=source_sha,
            stop_proof=result.stop_receipt.model_dump(mode='json'),
            request_sha256=envelope.request_sha256, artifact_sha256=artifact.sha256,
            candidate_verification='captured_authority_verified_digests_and_independent_pod_stop_no_offline_hmac')
        slot['job_id'] = envelope.job_id
        _write(root / 'pilot.json', plan)
        _render_record(root, plan, slot, record, output, renderer_image=renderer_image, assets_dir=assets_dir)
    except Exception as error:
        _fail(root, plan, slot, record, error)
        raise
    _publish_record(root, slot, record)
    _write(root / 'pilot.json', plan)
    return signed(record)


@_pilot_lock
def run(directory, slot_name, gateway, *, renderer_image, assets_dir=None, timeout=600, poll_seconds=3,
        model_policy_file=None):
    root, plan = _load(directory)
    slot, document, role = _slot_document(root, plan, slot_name)
    policy = _read_model_policy(model_policy_file)
    run_dir = root / slot['run_id']
    run_dir.mkdir()
    # Reservation is persisted before upload/admission. A process crash cannot
    # authorize another task; recover only the original idempotency key.
    slot.update(state='reserved', input_sha256=digest(document), created_at=time.time())
    _write(root / 'pilot.json', plan)
    record = _record(slot, document, role)
    _attach_recovery(record, plan)
    _write(run_dir / INPUT_NAME, document, exclusive=True)
    deadline = time.time() + timeout
    job_id = None
    try:
        uploaded = gateway.upload(document)
        slot['input_ref'] = uploaded
        _write(root / 'pilot.json', plan)
        slot['state'] = 'admitting'
        _write(root / 'pilot.json', plan)
        try:
            job = gateway.submit(slot['run_id'], document, deadline, uploaded)
        except ServiceError as error:
            if error.status >= 500:
                # Read-only reconciliation is allowed. Never repeat POST.
                job = gateway.lookup(slot['run_id'])
            else:
                raise
        job_id = job['job_id']
        slot.update(state='running', job_id=job_id)
        _write(root / 'pilot.json', plan)
        while job['state'] in {'queued', 'running', 'cancelling'}:
            if time.time() >= deadline:
                gateway.cancel(job_id)
                raise ServiceError('animation_experiment_deadline_elapsed', 409)
            time.sleep(min(poll_seconds, max(0, deadline - time.time())))
            job = gateway.poll(job_id)
        _write(run_dir / 'agent-job.json', job, exclusive=True)
        record['provenance'].update(job_id=job_id, state=job['state'], attempt=job.get('attempt'),
            pins=job.get('pins'), stop_proven=job.get('stop_proven'), stop_proof=job.get('stop_proof'),
            artifact_manifest_sha256=job.get('artifact_manifest_sha256'))
        _bind_model_policy(record, policy, job.get('pins'))
        require(job['state'] == 'completed', 'animation_experiment_agent_' + job['state'], 409)
        result = gateway.collect(job_id, digest(document), document)
        _write(run_dir / RESULT_NAME, result, exclusive=True)
        manifest = gateway.client.artifact_manifest(job_id)
        _write(run_dir / 'agent-artifact-manifest.json', manifest, exclusive=True)
        _render_record(root, plan, slot, record, result, renderer_image=renderer_image, assets_dir=assets_dir)
    except Exception as error:
        _fail(root, plan, slot, record, error)
        raise
    _publish_record(root, slot, record)
    _write(root / 'pilot.json', plan)
    return signed(record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--directory', required=True, type=Path)
    init.add_argument('--capability-release', default=DEFAULT_RELEASE_ID,
                      help='freeze a repository-reviewed strategy also used by production')
    change = commands.add_parser('revise')
    change.add_argument('--directory', required=True, type=Path)
    change.add_argument('--guidance-file', required=True, type=Path)
    change.add_argument('--feedback-file', required=True, type=Path)
    reserve = commands.add_parser('reserve-candidate')
    reserve.add_argument('--directory', required=True, type=Path)
    reserve.add_argument('--slot', choices=SLOTS, default='development-v1',
                         help='one explicit existing pilot slot; does not launch an Agent')
    reserve.add_argument('--expected-binding-file', required=True, type=Path,
                         help='root-owned exact host_policy(host).execution_binding JSON')
    reserve.add_argument('--build-receipt', required=True, type=Path,
                         help='root-owned inspected animation-agent-overlay-build.v1 receipt')
    reserve.add_argument('--model-policy-file', type=Path, help='public operator ModelPolicy JSON; never actual-model evidence')
    complete = commands.add_parser('complete-candidate')
    complete.add_argument('--directory', required=True, type=Path)
    complete.add_argument('--slot', choices=SLOTS, default='development-v1')
    complete.add_argument('--candidate-dir', required=True, type=Path,
                          help='root-owned captured request, evidence, result and stopped worker Pod')
    complete.add_argument('--renderer-image', required=True)
    complete.add_argument('--assets-dir', type=Path)
    close = commands.add_parser('close-failed-candidate')
    close.add_argument('--directory', required=True, type=Path)
    close.add_argument('--evidence-dir', required=True, type=Path,
                       help='root-owned original request, failure diagnosis, stopped Pods and exact cleanup proof')
    recover = commands.add_parser('recover-pilot')
    recover.add_argument('--directory', required=True, type=Path, help='original consumed and explicitly closed failed pilot')
    recover.add_argument('--new-directory', required=True, type=Path, help='new sibling pilot directory')
    recover.add_argument('--authorization-note', required=True, help='record the explicit continuation and new budget decision')
    recover.add_argument('--max-tasks', type=int, choices=(4,), default=4)
    execute = commands.add_parser('run')
    execute.add_argument('--directory', required=True, type=Path)
    execute.add_argument('--slot', choices=SLOTS, required=True)
    execute.add_argument('--agent-config', required=True, type=Path, help='private JSON base_url/token; never published')
    execute.add_argument('--renderer-image', required=True, help='latest@sha256 immutable renderer reference')
    execute.add_argument('--assets-dir', type=Path)
    execute.add_argument('--model-policy-file', type=Path, help='public operator ModelPolicy JSON, checked against actual pins')
    args = parser.parse_args(argv)
    if args.command == 'init':
        result = initialize(args.directory, capability_release_id=args.capability_release)
        print(json.dumps(dict(status='prepared', max_tasks=result['max_tasks'])))
    elif args.command == 'revise':
        revise(args.directory, args.guidance_file.read_text(encoding='utf-8'), args.feedback_file.read_text(encoding='utf-8'))
        print(json.dumps(dict(status='revision_frozen')))
    elif args.command == 'reserve-candidate':
        print(json.dumps(reserve_candidate(args.directory, expected_binding_file=args.expected_binding_file,
                                           build_receipt=args.build_receipt,
                                           model_policy_file=args.model_policy_file,
                                           slot_name=args.slot), ensure_ascii=True))
    elif args.command == 'complete-candidate':
        result = complete_candidate(args.directory, args.candidate_dir,
                                    renderer_image=args.renderer_image, assets_dir=args.assets_dir,
                                    slot_name=args.slot)
        print(json.dumps({k: result[k] for k in ('id', 'identity', 'status')}, ensure_ascii=True))
    elif args.command == 'close-failed-candidate':
        result = close_failed_candidate(args.directory, args.evidence_dir)
        print(json.dumps({k: result[k] for k in ('id', 'identity', 'status', 'error')}, ensure_ascii=True))
    elif args.command == 'recover-pilot':
        result = create_recovery(args.directory, args.new_directory, authorization_note=args.authorization_note,
                                 max_tasks=args.max_tasks)
        print(json.dumps(dict(status='recovery_pilot_prepared', recovery=result['recovery'],
                              max_tasks=result['max_tasks']), ensure_ascii=True))
    else:
        config = decode(args.agent_config.read_bytes())
        require(set(config) == {'base_url', 'token'}, 'animation_experiment_private_config_invalid')
        with AgentClient(config['base_url'], config['token']) as client:
            result = run(args.directory, args.slot, ExperimentGateway(client), renderer_image=args.renderer_image,
                         assets_dir=args.assets_dir, model_policy_file=args.model_policy_file)
        print(json.dumps({k: result[k] for k in ('id', 'identity', 'status')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
