"""File-only preparation and evidence import for two creative production trials.

This command never applies Kubernetes resources, opens a credential, submits an
Agent task, resets an attempt or changes shared admission. The existing root
candidate operator executes an explicitly prepared one-shot manifest. Import
reuses its captured authority verification and independently stopped Pod proof.
Neither an isolated success nor this package creates shared-route acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4
import wave

from agent_service.contracts import ExecutionBindingPins, ServiceError
from agent_service.profiles.video_animation_experiment import (
    INPUT_NAME, RESULT_NAME, INPUT_MAX_BYTES, OUTPUT_MAX_BYTES, PROFILE,
    VideoAnimationExperimentProfile, validate_experiment_result)
from .animation_capabilities import canonical, digest
from .animation_experiment_runner import _candidate_build, _candidate_input_bound
from .creative_contract import validate_scene_input, collect_scene_result


SCHEMA = 'video.creative-validation.v1'
RESERVATION_SCHEMA = 'video.creative-validation-reservation.v1'
SLOTS = ('first', 'second')
MAX_AUDIO_BYTES = 16 * 1024 * 1024


def _require(condition, code='creative_validation_invalid', status=422):
    if not condition:
        raise ServiceError(code, status)


def _read(path, limit):
    path = Path(path).absolute()
    _require(not any(item.is_symlink() for item in (path, *path.parents))
             and path.is_file() and path.stat().st_nlink == 1
             and 0 < path.stat().st_size <= limit, 'creative_validation_file_invalid')
    raw = path.read_bytes()
    _require(0 < len(raw) <= limit, 'creative_validation_file_invalid')
    return raw


def _json(raw):
    from agent_service.profiles.video import decode_video_json
    return decode_video_json(raw)


def _save(path, value, *, replace=False):
    raw = value if type(value) is bytes else canonical(value)
    path = Path(path)
    if path.exists() and not replace:
        _require(_read(path, max(len(raw), 1)) == raw, 'creative_validation_immutable_file_changed', 409)
        return
    temporary = path.with_name('.' + path.name + '.new') if replace else path
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        temporary.replace(path)


def initialize(directory, authorization_note):
    _require(type(authorization_note) is str and 0 < len(authorization_note.strip()) <= 4000)
    root = Path(directory).absolute()
    _require(not root.exists() and not any(path.is_symlink() for path in (root, *root.parents)),
             'creative_validation_new_scope_required', 409)
    plan = dict(schema=SCHEMA, scope_id=str(uuid4()), authorization_note=authorization_note,
        created_at=time.time(), max_tasks=2, max_attempts_per_task=1, automatic_retries=False,
        shared_admission_changed=False, original_lab_pilot_modified=False,
        slots={name: dict(state='unstarted') for name in SLOTS})
    root.mkdir(mode=0o700)
    _save(root / 'validation.json', plan)
    return deepcopy(plan)


def _load(directory):
    root = Path(directory).absolute()
    plan = _json(_read(root / 'validation.json', 65536))
    _require(type(plan) is dict and plan.get('schema') == SCHEMA
             and plan.get('max_tasks') == 2 and type(plan['max_tasks']) is int
             and plan.get('max_attempts_per_task') == 1 and type(plan['max_attempts_per_task']) is int
             and plan.get('automatic_retries') is False and plan.get('shared_admission_changed') is False
             and plan.get('original_lab_pilot_modified') is False and type(plan.get('slots')) is dict
             and set(plan['slots']) == set(SLOTS), 'creative_validation_budget_changed', 409)
    for slot in plan['slots'].values():
        _require(type(slot) is dict and slot.get('state') in {'unstarted', 'reserved', 'prepared',
                 'manifest_prepared', 'collection_failed', 'completed'})
    return root, plan


@contextmanager
def _locked(directory):
    root, plan = _load(directory)
    path = root / '.validation.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        raise ServiceError('creative_validation_busy', 409) from None
    try:
        os.close(descriptor)
        # Re-read under the lock: another operator may have completed before it.
        yield _load(root)
    finally:
        path.unlink()


def wav_clock(raw):
    """Use actual PCM samples, rejecting a metadata-only/truncated WAV."""
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_AUDIO_BYTES, 'creative_validation_audio_invalid')
    try:
        with wave.open(io.BytesIO(raw), 'rb') as source:
            channels, sample_width, rate, frames = (source.getnchannels(), source.getsampwidth(),
                                                   source.getframerate(), source.getnframes())
            _require(source.getcomptype() == 'NONE' and channels in {1, 2}
                     and sample_width in {1, 2, 3, 4} and 8000 <= rate <= 192000
                     and 0 < frames / rate <= 60, 'creative_validation_audio_invalid')
            pcm = source.readframes(frames + 1)
            _require(len(pcm) == frames * channels * sample_width, 'creative_validation_audio_truncated')
    except (wave.Error, EOFError, OSError):
        raise ServiceError('creative_validation_audio_invalid', 422) from None
    return dict(sha256=digest(raw), duration_seconds=frames / rate, frames=frames,
                sample_rate=rate, channels=channels, sample_width=sample_width)


def prepare(directory, slot_name, *, input_file, audio_file, expected_binding_file,
            build_receipt_file, candidate_name, uid_start, node='arm-cluster-master'):
    """Consume one new slot, prepare exact scenario; no launch or paid retry."""
    _require(slot_name in SLOTS and type(candidate_name) is str
             and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?', candidate_name)
             and type(uid_start) is int and 65536 <= uid_start <= 2**31 - 4
             and type(node) is str and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', node))
    with _locked(directory) as (root, plan):
        _require(plan['slots'][slot_name]['state'] == 'unstarted', 'creative_validation_attempt_consumed', 409)
        document_raw = _read(input_file, INPUT_MAX_BYTES)
        document = validate_scene_input(_json(document_raw))
        _require(document_raw == canonical(document), 'creative_validation_input_not_canonical')
        audio_raw = _read(audio_file, MAX_AUDIO_BYTES)
        audio = wav_clock(audio_raw)
        bounds, context = document['constraints'], document['constraints']['production']
        _require(context['audio_sha256'] == audio['sha256']
                 and abs(bounds['duration_seconds'] - audio['duration_seconds']) <= 1 / audio['sample_rate'],
                 'creative_validation_audio_binding_mismatch')
        binding_raw = _read(expected_binding_file, 131072)
        receipt_raw = _read(build_receipt_file, 131072)
        binding, receipt, profile_sha = _candidate_build(binding_raw, receipt_raw)
        for existing in plan['slots'].values():
            _require(existing.get('candidate_name') != candidate_name
                     and not set(range(uid_start, uid_start + 3)).intersection(existing.get('uid_range', [])),
                     'creative_validation_candidate_reused', 409)
        checksum = digest(document_raw)
        reservation = dict(schema=RESERVATION_SCHEMA, reservation_id=str(uuid4()), scope_id=plan['scope_id'],
            slot=slot_name, candidate_name=candidate_name, node=node, uid_range=list(range(uid_start, uid_start + 3)),
            profile=PROFILE, profile_revision='1', max_generation_attempts=1, automatic_retries=False,
            input_sha256=checksum, audio=audio, worker_image=receipt['image'], profile_source_sha256=profile_sha,
            expected_binding_sha256=digest(binding_raw), build_receipt_sha256=digest(receipt_raw),
            execution_binding_sha256=binding.sha256,
            capability_release_sha256=bounds['capability_release']['sha256'],
            prompt_sha256=digest(VideoAnimationExperimentProfile().build_prompt({'input_sha256': checksum}).encode()),
            uid_freshness='root_operator_must_check_and_permanently_reserve_before_launch', created_at=time.time())
        slot = dict(state='reserved', candidate_name=candidate_name, uid_range=reservation['uid_range'],
                    reservation_sha256=digest(reservation))
        plan['slots'][slot_name] = slot
        _save(root / 'validation.json', plan, replace=True)
        target = root / slot_name
        target.mkdir(mode=0o700)
        sources = target / 'candidate-sources'
        sources.mkdir(mode=0o700)
        _save(sources / INPUT_NAME, document_raw)
        _save(target / 'audio.wav', audio_raw)
        _save(target / 'expected-binding.json', binding_raw)
        _save(target / 'pi-image.json', receipt_raw)
        _save(target / 'reservation.json', reservation)
        scenario = dict(schema_version='agent-candidate-scenario.v1', candidate_scope_only=True,
            scenario_id=reservation['reservation_id'], profile=PROFILE, timeout_seconds=600,
            task=dict(input_sha256=checksum), inputs=[dict(name=INPUT_NAME, sha256=checksum, bytes=len(document_raw))],
            business_export=None, parent=None)
        _save(target / 'candidate-scenario.json', scenario)
        slot.update(state='prepared', scenario_sha256=digest(scenario))
        _save(root / 'validation.json', plan, replace=True)
        return deepcopy(reservation)


def _slot(root, plan, slot_name):
    _require(slot_name in SLOTS)
    slot = plan['slots'][slot_name]
    _require(slot['state'] in {'prepared', 'manifest_prepared', 'collection_failed', 'completed'},
             'creative_validation_not_prepared', 409)
    target = root / slot_name
    reservation = _json(_read(target / 'reservation.json', 131072))
    _require(reservation.get('schema') == RESERVATION_SCHEMA and reservation.get('scope_id') == plan['scope_id']
             and reservation.get('slot') == slot_name and digest(reservation) == slot['reservation_sha256']
             and reservation.get('profile') == PROFILE and reservation.get('profile_revision') == '1'
             and type(reservation.get('max_generation_attempts')) is int
             and reservation['max_generation_attempts'] == 1 and reservation.get('automatic_retries') is False
             and reservation.get('candidate_name') == slot['candidate_name']
             and reservation.get('uid_range') == slot['uid_range'], 'creative_validation_reservation_changed', 409)
    document_raw = _read(target / 'candidate-sources' / INPUT_NAME, INPUT_MAX_BYTES)
    document = validate_scene_input(_json(document_raw))
    _require(canonical(document) == document_raw and digest(document_raw) == reservation['input_sha256'])
    _require(wav_clock(_read(target / 'audio.wav', MAX_AUDIO_BYTES)) == reservation['audio'])
    binding_raw, receipt_raw = (_read(target / name, 131072) for name in ('expected-binding.json', 'pi-image.json'))
    binding, _, profile_sha = _candidate_build(binding_raw, receipt_raw)
    _require(digest(binding_raw) == reservation['expected_binding_sha256']
             and digest(receipt_raw) == reservation['build_receipt_sha256']
             and profile_sha == reservation['profile_source_sha256']
             and binding.sha256 == reservation['execution_binding_sha256'])
    scenario = _json(_read(target / 'candidate-scenario.json', 131072))
    _require(digest(scenario) == slot['scenario_sha256'])
    return target, slot, reservation, document, binding


def resources(directory, slot_name, host_config_file):
    """Generate existing candidate harness manifests; never apply them."""
    from agent_service.deploy.acceptance_probe import candidate_config, render_probe
    from agent_service.worker_entry import HostConfig, host_policy
    with _locked(directory) as (root, plan):
        target, slot, reservation, _, binding = _slot(root, plan, slot_name)
        _require(slot['state'] in {'prepared', 'manifest_prepared'}, 'creative_validation_manifest_not_ready', 409)
        raw = _read(host_config_file, 1024 * 1024)
        host = HostConfig.model_validate(_json(raw))
        candidate_config(host.model_dump(mode='json'))
        _require(host_policy(host).execution_binding == binding, 'creative_validation_host_binding_mismatch')
        manifest = render_probe(host, name=reservation['candidate_name'], worker_image=reservation['worker_image'],
            node=reservation['node'], uid_start=reservation['uid_range'][0],
            bootstrap_secret=reservation['candidate_name'] + '-bootstrap',
            upstream_secret=reservation['candidate_name'] + '-upstream',
            scenario=target / 'candidate-scenario.json', source_dir=target / 'candidate-sources')
        _save(target / 'host-disabled.json', raw)
        _save(target / 'candidate-manifest.json', manifest)
        receipt = dict(schema='video.creative-validation-manifest.v1', scope_id=plan['scope_id'], slot=slot_name,
            reservation_sha256=digest(reservation), host_config_sha256=digest(raw), manifest_sha256=digest(manifest),
            max_agent_tasks=1, automatic_retry=False, cluster_writes=False, credentials_opened=False,
            shared_admission_changed=False)
        _save(target / 'manifest-preparation.json', receipt)
        slot.update(state='manifest_prepared', manifest_sha256=digest(manifest))
        _save(root / 'validation.json', plan, replace=True)
        return receipt


def collect(directory, slot_name, evidence_dir):
    """Import real captured custody; never manufacture a successful Agent job."""
    from agent_service.deploy.candidate_scenario import PARENT_FILES, _read as authority_read, _json as authority_json, verify_parent
    with _locked(directory) as (root, plan):
        target, slot, reservation, document, binding = _slot(root, plan, slot_name)
        captured = target / 'candidate-evidence'
        captured.mkdir(mode=0o700, exist_ok=True)
        try:
            docs, hashes = {}, {}
            for name in PARENT_FILES.values():
                raw = authority_read(evidence_dir, name)
                _save(captured / name, raw)
                hashes[name] = digest(raw)
                docs[name] = authority_json(raw)
            envelope, stopped_result = verify_parent(docs)
            _candidate_input_bound(envelope, binding, document)
            proof = stopped_result.stop_receipt
            _require(proof.fence == envelope.fence and proof.engine == envelope.engine)
            pod = docs['worker-pod.json']
            _require(pod['metadata'].get('name') == reservation['candidate_name'] + '-worker'
                     and pod['metadata'].get('namespace') == 'agent-workers', 'creative_validation_pod_scope_mismatch')
            observed_uids = docs['candidate-result.json']['observations'].get('isolation', {}).get('uid')
            # isolation_preflight runs its child as scope.agent_uid + 2;
            # getresuid reports real/effective/saved IDs of that one process.
            _require(type(observed_uids) is list and observed_uids == [reservation['uid_range'][2]] * 3,
                     'creative_validation_uid_scope_mismatch')
            for other_name, other in plan['slots'].items():
                if other_name != slot_name:
                    _require(other.get('job_id') != envelope.job_id and other.get('attempt_id') != envelope.attempt_id,
                             'creative_validation_execution_reused', 409)
            _require(len(stopped_result.artifacts) == 1 and stopped_result.artifacts[0].name == RESULT_NAME,
                     'creative_validation_artifact_mismatch')
            artifact = stopped_result.artifacts[0]
            output = validate_experiment_result(_json(artifact.content(limit=OUTPUT_MAX_BYTES)), digest(document), document)
            package = collect_scene_result(document, output)
            provenance = dict(schema='video.creative-validation-evidence.v1', scope_id=plan['scope_id'], slot=slot_name,
                reservation_sha256=digest(reservation), input_sha256=digest(document),
                capability_release_sha256=package['capability_release_sha256'], profile=package['profile'],
                source_files_sha256=output['files_sha256'], result_sha256=digest(output),
                candidate_files_sha256=hashes, job_id=envelope.job_id, attempt_id=envelope.attempt_id,
                request_sha256=envelope.request_sha256, execution_binding=binding.model_dump(mode='json'),
                stop_proof=proof.model_dump(mode='json'), artifact_sha256=artifact.sha256,
                custody='captured_authority_verified_digests_and_independent_pod_stop_not_offline_hmac',
                admission='explicit_isolated_creative_validation_not_shared_route',
                production_acceptance_created=False, shared_admission_changed=False,
                visual_quality='unreviewed', rendered=False, actual_model=None)
            _save(target / RESULT_NAME, output)
            _save(target / 'creative-scene.json', package)
            _save(target / 'provenance.json', provenance)
            slot.update(state='completed', job_id=envelope.job_id, attempt_id=envelope.attempt_id,
                        package_sha256=digest(package), provenance_sha256=digest(provenance))
            slot.pop('error', None)
            _save(root / 'validation.json', plan, replace=True)
            return package
        except Exception as exc:
            if slot['state'] != 'completed':
                slot.update(state='collection_failed', error=exc.code if isinstance(exc, ServiceError) else type(exc).__name__)
                _save(root / 'validation.json', plan, replace=True)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--directory', type=Path, required=True)
    init.add_argument('--authorization-note', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--directory', type=Path, required=True)
    prep.add_argument('--slot', choices=SLOTS, required=True)
    prep.add_argument('--input-file', type=Path, required=True)
    prep.add_argument('--audio-file', type=Path, required=True)
    prep.add_argument('--expected-binding-file', type=Path, required=True)
    prep.add_argument('--build-receipt-file', type=Path, required=True)
    prep.add_argument('--candidate-name', required=True)
    prep.add_argument('--uid-start', type=int, required=True)
    prep.add_argument('--node', default='arm-cluster-master')
    manifest = commands.add_parser('resources')
    manifest.add_argument('--directory', type=Path, required=True)
    manifest.add_argument('--slot', choices=SLOTS, required=True)
    manifest.add_argument('--host-config-file', type=Path, required=True)
    complete = commands.add_parser('collect')
    complete.add_argument('--directory', type=Path, required=True)
    complete.add_argument('--slot', choices=SLOTS, required=True)
    complete.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    values = vars(args).copy()
    command = values.pop('command')
    if command == 'init':
        result = initialize(**values)
        summary = dict(status='prepared', scope_id=result['scope_id'], max_tasks=2)
    elif command == 'prepare':
        values['slot_name'] = values.pop('slot')
        result = prepare(**values)
        summary = dict(status='reserved', slot=result['slot'], input_sha256=result['input_sha256'],
                       capability_release_sha256=result['capability_release_sha256'])
    elif command == 'resources':
        values['slot_name'] = values.pop('slot')
        result = resources(**values)
        summary = dict(status='manifest_prepared', manifest_sha256=result['manifest_sha256'], cluster_writes=False)
    else:
        values['slot_name'] = values.pop('slot')
        result = collect(**values)
        summary = dict(status='source_collected_not_rendered', package_sha256=digest(result),
                       shared_admission_changed=False)
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    main()

