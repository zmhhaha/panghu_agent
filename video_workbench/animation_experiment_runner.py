"""Explicit, durable four-task creative pilot over the common Agent service.

This CLI is separate from the credential-free lab web server. It does not
retry model work, merge code, publish automatically, or execute scene scripts
outside the isolated renderer.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from functools import wraps
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from agent_service.client import AgentClient
from agent_service.contracts import ExecutionBindingPins, ServiceError
from agent_service.profiles import video_animation_experiment as experiment_profile
from agent_service.profiles.video_animation_experiment import (
    PROFILE, INPUT_NAME, RESULT_NAME, INPUT_MAX_BYTES, OUTPUT_MAX_BYTES,
    VideoAnimationExperimentProfile, validate_experiment_input, validate_experiment_result, scene_files)
from .motion_gateway import MotionAgentGateway
from .animation_experiments import (RUN_SCHEMA, canonical, constraints, decode, definition,
    digest, pilot_input, require, signed, _ordinary_file)


SLOTS = ('development-v1', 'development-v2', 'held-out-v1', 'held-out-v2')
RESERVATION_NAME = 'candidate-reservation.json'
RESERVATION_SCHEMA = 'video.animation-experiment-candidate-reservation.v1'
EXPECTED_BINDING_NAME = 'expected-candidate-binding.json'
BUILD_RECEIPT_NAME = 'candidate-build-receipt.json'


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


def initialize(directory):
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=False)
    plan = dict(schema='video.animation-experiment-pilot.v1', experiment=definition(),
        constraints={role: constraints(role) for role in ('development', 'held_out')},
        constraint_identities={role: digest(constraints(role)) for role in ('development', 'held_out')},
        max_tasks=4, max_attempts_per_task=1,
        guidance={'v1': '', 'v2': None}, feedback=None,
        slots={slot: dict(state='unstarted', run_id='identity-' + slot) for slot in SLOTS})
    _write(root / 'pilot.json', plan, exclusive=True)
    return plan


def _load(root):
    root = Path(root).absolute()
    require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)),
            'animation_experiment_directory_invalid')
    plan = decode((root / 'pilot.json').read_bytes())
    require(plan.get('schema') == 'video.animation-experiment-pilot.v1'
            and plan.get('experiment') == definition() and set(plan['slots']) == set(SLOTS)
            and plan['max_tasks'] == 4 and plan['max_attempts_per_task'] == 1,
            'animation_experiment_plan_changed')
    for role in ('development', 'held_out'):
        require(plan['constraints'][role] == constraints(role)
                and plan['constraint_identities'][role] == digest(constraints(role)),
                'animation_experiment_plan_changed')
    return root, plan


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


def _record(slot, document):
    role, revision = ('held_out' if slot['run_id'].startswith('identity-held-out') else 'development'), document['prompt_revision']
    return dict(schema=RUN_SCHEMA, id=slot['run_id'], experiment_id=definition()['id'],
        experiment_identity=definition()['identity'], source='agent', input_role=role,
        input_document=document, input_sha256=digest(document),
        label=('开发输入' if role == 'development' else '未参与调优输入') + ' · ' + revision,
        prompt_text=VideoAnimationExperimentProfile().build_prompt({'input_sha256': digest(document)}),
        status='failed', provenance=dict(profile=PROFILE,
            requested_model=None, returned_model=None, returned_model_note='服务未返回实际模型名称时保持未知。',
            generation_context='profile_prompt_plus_exact_input_document',
            automatic_retries=False), artifacts={}, code_files={}, strategy={})


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
def reserve_candidate(directory, *, expected_binding_file, build_receipt, model_policy_file=None):
    """Reserve the first pilot attempt before an external candidate is run.

    No Agent upload, admission or rendering occurs. A crash or later failed
    import leaves this slot consumed, so candidate acceptance is not a fifth task.
    """
    root, plan = _load(directory)
    require(all(plan['slots'][name]['state'] == 'unstarted' for name in SLOTS)
            and plan['guidance'] == {'v1': '', 'v2': None},
            'animation_experiment_attempt_already_reserved', 409)
    slot = plan['slots']['development-v1']
    document = pilot_input('development', 'v1', '')
    validate_experiment_input(document)
    from agent_service.deploy.candidate_scenario import _read
    expected_binding_file, build_receipt = Path(expected_binding_file), Path(build_receipt)
    binding_raw = _read(expected_binding_file.parent, expected_binding_file.name)
    receipt_raw = _read(build_receipt.parent, build_receipt.name)
    binding, receipt, source_sha = _candidate_build(binding_raw, receipt_raw)
    policy = _read_model_policy(model_policy_file)
    _bind_model_policy({'provenance': {}}, policy, binding.model_dump(mode='json'))
    prompt = VideoAnimationExperimentProfile().build_prompt({'input_sha256': digest(document)})
    reservation = dict(schema=RESERVATION_SCHEMA, reservation_id=str(uuid4()),
        slot='development-v1', run_id=slot['run_id'], profile=PROFILE, profile_revision='1',
        input_sha256=digest(document), prompt_text=prompt, prompt_sha256=digest(prompt.encode('utf-8')),
        expected_binding_sha256=digest(binding_raw), build_receipt_sha256=digest(receipt_raw),
        profile_source_sha256=source_sha, worker_image=receipt['image'],
        execution_binding_sha256=binding.sha256, declared_model_policy=policy,
        created_at=time.time(), max_generation_attempts=1, automatic_retries=False)
    run_dir = root / slot['run_id']
    run_dir.mkdir()
    slot.update(state='candidate_reserved', input_sha256=digest(document),
        candidate_reservation_sha256=digest(reservation), created_at=reservation['created_at'])
    # Persist before writing sources. An incomplete reservation can only fail;
    # it cannot reauthorize the first slot or trigger another candidate job.
    _write(root / 'pilot.json', plan)
    _write(run_dir / INPUT_NAME, document, exclusive=True)
    _write(run_dir / RESERVATION_NAME, reservation, exclusive=True)
    for name, raw in ((EXPECTED_BINDING_NAME, binding_raw), (BUILD_RECEIPT_NAME, receipt_raw)):
        with (run_dir / name).open('xb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    return dict(reservation, input_path=str(run_dir / INPUT_NAME))


@_pilot_lock
def complete_candidate(directory, candidate_dir, *, renderer_image, assets_dir=None):
    """Import one stopped, operator-captured candidate into its reserved slot.

    This path never constructs an AgentClient or submits/retries a task. Root-
    owned authority evidence and the independently captured stopped Pod are
    rechecked; the ephemeral HMAC key is deliberately not exported or reverified.
    """
    from agent_service.deploy.candidate_scenario import PARENT_FILES, _read, _json, verify_parent

    root, plan = _load(directory)
    slot = plan['slots']['development-v1']
    require(slot['state'] == 'candidate_reserved', 'animation_experiment_candidate_not_reserved', 409)
    slot['state'] = 'candidate_importing'
    _write(root / 'pilot.json', plan)
    document = pilot_input('development', 'v1', '')
    record = _record(slot, document)
    record['provenance']['admission'] = 'external_acceptance_candidate_reused_in_reserved_pilot_slot'
    run_dir = root / slot['run_id']
    try:
        raw = _ordinary_file(run_dir, RESERVATION_NAME, INPUT_MAX_BYTES)
        reservation = decode(raw)
        require(digest(raw) == slot['candidate_reservation_sha256']
                and reservation.get('schema') == RESERVATION_SCHEMA
                and reservation.get('slot') == 'development-v1' and reservation.get('run_id') == slot['run_id']
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
        _bind_model_policy(record, reservation.get('declared_model_policy'),
                           envelope.execution_binding.model_dump(mode='json'))
        profile = VideoAnimationExperimentProfile()
        require(envelope.profile == PROFILE and envelope.profile_revision == '1'
                and envelope.execution_binding == binding
                and envelope.image_digest == receipt['image'].rsplit('@', 1)[1]
                and result.stop_receipt.fence == envelope.fence and result.stop_receipt.engine == envelope.engine
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
    require(slot_name in SLOTS, 'animation_experiment_slot_invalid')
    slot = plan['slots'][slot_name]
    require(slot['state'] == 'unstarted', 'animation_experiment_attempt_already_reserved', 409)
    revision = 'v2' if slot_name.endswith('v2') else 'v1'
    role = 'held_out' if slot_name.startswith('held-out') else 'development'
    require(revision != 'v2' or plan['guidance']['v2'] is not None, 'animation_experiment_revision_not_ready', 409)
    if role == 'held_out':
        require(plan['slots']['development-v2']['state'] == 'finished',
                'animation_experiment_holdout_not_released', 409)
    document = pilot_input(role, revision, plan['guidance'][revision])
    validate_experiment_input(document)
    policy = _read_model_policy(model_policy_file)
    run_dir = root / slot['run_id']
    run_dir.mkdir()
    # Reservation is persisted before upload/admission. A process crash cannot
    # authorize another task; recover only the original idempotency key.
    slot.update(state='reserved', input_sha256=digest(document), created_at=time.time())
    _write(root / 'pilot.json', plan)
    record = _record(slot, document)
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
    change = commands.add_parser('revise')
    change.add_argument('--directory', required=True, type=Path)
    change.add_argument('--guidance-file', required=True, type=Path)
    change.add_argument('--feedback-file', required=True, type=Path)
    reserve = commands.add_parser('reserve-candidate')
    reserve.add_argument('--directory', required=True, type=Path)
    reserve.add_argument('--expected-binding-file', required=True, type=Path,
                         help='root-owned exact host_policy(host).execution_binding JSON')
    reserve.add_argument('--build-receipt', required=True, type=Path,
                         help='root-owned inspected animation-agent-overlay-build.v1 receipt')
    reserve.add_argument('--model-policy-file', type=Path, help='public operator ModelPolicy JSON; never actual-model evidence')
    complete = commands.add_parser('complete-candidate')
    complete.add_argument('--directory', required=True, type=Path)
    complete.add_argument('--candidate-dir', required=True, type=Path,
                          help='root-owned captured request, evidence, result and stopped worker Pod')
    complete.add_argument('--renderer-image', required=True)
    complete.add_argument('--assets-dir', type=Path)
    execute = commands.add_parser('run')
    execute.add_argument('--directory', required=True, type=Path)
    execute.add_argument('--slot', choices=SLOTS, required=True)
    execute.add_argument('--agent-config', required=True, type=Path, help='private JSON base_url/token; never published')
    execute.add_argument('--renderer-image', required=True, help='latest@sha256 immutable renderer reference')
    execute.add_argument('--assets-dir', type=Path)
    execute.add_argument('--model-policy-file', type=Path, help='public operator ModelPolicy JSON, checked against actual pins')
    args = parser.parse_args(argv)
    if args.command == 'init':
        result = initialize(args.directory)
        print(json.dumps(dict(status='prepared', max_tasks=result['max_tasks'])))
    elif args.command == 'revise':
        revise(args.directory, args.guidance_file.read_text(encoding='utf-8'), args.feedback_file.read_text(encoding='utf-8'))
        print(json.dumps(dict(status='revision_frozen')))
    elif args.command == 'reserve-candidate':
        print(json.dumps(reserve_candidate(args.directory, expected_binding_file=args.expected_binding_file,
                                           build_receipt=args.build_receipt,
                                           model_policy_file=args.model_policy_file), ensure_ascii=True))
    elif args.command == 'complete-candidate':
        result = complete_candidate(args.directory, args.candidate_dir,
                                    renderer_image=args.renderer_image, assets_dir=args.assets_dir)
        print(json.dumps({k: result[k] for k in ('id', 'identity', 'status')}, ensure_ascii=True))
    else:
        config = decode(args.agent_config.read_bytes())
        require(set(config) == {'base_url', 'token'}, 'animation_experiment_private_config_invalid')
        with AgentClient(config['base_url'], config['token']) as client:
            result = run(args.directory, args.slot, ExperimentGateway(client), renderer_image=args.renderer_image,
                         assets_dir=args.assets_dir, model_policy_file=args.model_policy_file)
        print(json.dumps({k: result[k] for k in ('id', 'identity', 'status')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
