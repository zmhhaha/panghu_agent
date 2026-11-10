"""Operator-only import of stopped phase-one candidates; no execution or HTTP.

The candidate controller verified the private stop-receipt signature before
exporting evidence. This importer checks exported identities/digests and actual
Pod observations, but does not possess or recreate that private signing key.
An explicit historical import event consumes each stage without manufacturing
a prior submission event. Sources must be trusted operator exports.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time

from agent_service.contracts import ExecutionEnvelope, ServiceError, WorkerResult, canonical_bytes, digest
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from agent_service.profiles.video import _read, decode_video_json
from .animation_observation import load_bundle, validate_model_frame_receipt
from .animation_revision_store import AnimationRevisionStore, TERMINAL


MAX_EXPORT_BYTES = 16 * 1024 * 1024
EXPORT_NAMES = ('candidate-request.json', 'candidate-result.json', 'candidate-evidence.json',
    'worker-pod.json', 'proxy-pod.json', 'operator-summary.json', 'host-config.json',
    'scenario.json', 'manifest.json')
ROLE_PROFILES = {'observe': visual.OBSERVE_PROFILE, 'review': visual.REVIEW_PROFILE,
                 'create': creation.PROFILE}


def require(value, code='animation_candidate_import_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _json(path):
    path = Path(path).absolute()
    value = decode_video_json(_read(path.parent, path.name, MAX_EXPORT_BYTES))
    require(type(value) is dict)
    return value


def _rows(value, key, code):
    require(type(value) is list and all(type(row) is dict and type(row.get(key)) is str
            and row[key] for row in value), code)
    result = {row[key]: row for row in value}
    require(len(result) == len(value), code)
    return result


def _contains(actual, planned):
    """Permit API defaults while requiring every frozen declaration unchanged."""
    if type(planned) is dict:
        return type(actual) is dict and all(key in actual and _contains(actual[key], value)
                                           for key, value in planned.items())
    if type(planned) is list:
        return type(actual) is list and len(actual) == len(planned) and all(
            _contains(actual_item, planned_item) for actual_item, planned_item in zip(actual, planned))
    return type(actual) is type(planned) and actual == planned


def _environment(actual, planned):
    """Accept only the known downward-API version default inside env lists."""
    if actual is None or planned is None:
        return actual is planned
    if type(actual) is not list or type(planned) is not list or len(actual) != len(planned):
        return False
    normalized = deepcopy(actual)
    for row, declaration in zip(normalized, planned):
        if type(row) is not dict or type(declaration) is not dict:
            return False
        actual_source, declared_source = row.get('valueFrom'), declaration.get('valueFrom')
        if type(actual_source) is dict and type(declared_source) is dict:
            actual_ref, declared_ref = actual_source.get('fieldRef'), declared_source.get('fieldRef')
            if type(actual_ref) is dict and type(declared_ref) is dict:
                if 'apiVersion' not in declared_ref and actual_ref.get('apiVersion') == 'v1':
                    actual_ref.pop('apiVersion')
    # Symmetry rejects every other additional/missing dict field, while the
    # recursive list contract preserves order, length and primitive types.
    return _contains(normalized, planned) and _contains(planned, normalized)


def _pod(value, planned, image_digest, *, completed_worker=False):
    require(type(value) is dict and value.get('kind') == 'Pod'
            and type(value.get('metadata')) is dict and type(value.get('spec')) is dict
            and type(value.get('status')) is dict, 'animation_candidate_pod_invalid')
    require(type(planned) is dict and type(planned.get('metadata')) is dict
            and type(planned.get('spec')) is dict, 'animation_candidate_pod_invalid')
    metadata, spec, status = value['metadata'], value['spec'], value['status']
    require(metadata.get('name') == planned['metadata']['name']
            and metadata.get('namespace') == planned['metadata']['namespace'],
            'animation_candidate_pod_identity_mismatch')
    wanted = planned['spec']
    require(type(wanted.get('containers')) is list and type(wanted.get('nodeSelector', {})) is dict,
            'animation_candidate_pod_invalid')
    for key in ('labels', 'annotations'):
        require(_contains(metadata.get(key, {}), planned['metadata'].get(key, {})),
                'animation_candidate_pod_scope_changed')
    for key in ('securityContext', 'automountServiceAccountToken', 'hostPID', 'hostIPC',
                'hostNetwork', 'shareProcessNamespace', 'restartPolicy'):
        # Core PodSpec encodes these false booleans with omitempty. Their
        # absence after an API round trip still means false, never true.
        default = False if key in ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace') else None
        require(type(spec.get(key, default)) is type(wanted.get(key, default))
                and spec.get(key, default) == wanted.get(key, default),
                'animation_candidate_pod_scope_changed')
    require(spec.get('restartPolicy') == 'Never'
            and not spec.get('initContainers') and not spec.get('ephemeralContainers')
            and not status.get('initContainerStatuses') and not status.get('ephemeralContainerStatuses'),
            'animation_candidate_pod_scope_changed')
    node = wanted.get('nodeSelector', {}).get('kubernetes.io/hostname')
    require(not node or spec.get('nodeName') == node, 'animation_candidate_pod_scope_changed')
    containers, statuses = spec.get('containers'), status.get('containerStatuses')
    require(type(containers) is list and type(statuses) is list
            and len(containers) == len(wanted['containers']) == len(statuses) > 0,
            'animation_candidate_pod_not_stopped')
    expected = _rows(wanted['containers'], 'name', 'animation_candidate_pod_invalid')
    require(all(type(row.get('image')) is str for row in expected.values()),
            'animation_candidate_pod_invalid')
    _rows(containers, 'name', 'animation_candidate_pod_invalid')
    _rows(statuses, 'name', 'animation_candidate_pod_invalid')
    require(len(expected) == len(containers)
            and {row.get('name') for row in containers} == set(expected)
            and {row.get('name') for row in statuses} == set(expected),
            'animation_candidate_pod_identity_mismatch')
    for container in containers:
        planned_container = expected[container['name']]
        require(container.get('image') == planned_container['image']
                and container['image'].endswith('@' + image_digest)
                and container.get('securityContext') == planned_container.get('securityContext'),
                'animation_candidate_pod_scope_changed')
        for key in ('command', 'args', 'workingDir'):
            require(container.get(key) == planned_container.get(key),
                    'animation_candidate_pod_scope_changed')
        require(_environment(container.get('env'), planned_container.get('env')),
                'animation_candidate_pod_scope_changed')
        # Kubernetes adds harmless defaults to mounts. Every declared mount and
        # its read-only/source-path properties must still match the frozen plan.
        mounts = _rows(container.get('volumeMounts', []), 'mountPath', 'animation_candidate_pod_invalid')
        planned_mounts = _rows(planned_container.get('volumeMounts', []), 'mountPath',
                              'animation_candidate_pod_invalid')
        require(set(mounts) == set(planned_mounts), 'animation_candidate_pod_scope_changed')
        for mount in planned_container.get('volumeMounts', []):
            actual = mounts.get(mount['mountPath'])
            require(_contains(actual, mount),
                    'animation_candidate_pod_scope_changed')
            require(actual.get('mountPropagation') == mount.get('mountPropagation')
                    and actual.get('subPathExpr') == mount.get('subPathExpr'),
                    'animation_candidate_pod_scope_changed')
    volumes = _rows(spec.get('volumes', []), 'name', 'animation_candidate_pod_invalid')
    planned_volumes = _rows(wanted.get('volumes', []), 'name', 'animation_candidate_pod_invalid')
    require(set(volumes) == set(planned_volumes)
            and all(set(volumes[name]) == set(declaration) and _contains(volumes[name], declaration)
                    for name, declaration in planned_volumes.items()), 'animation_candidate_pod_scope_changed')
    for row in statuses:
        state = row.get('state')
        require(type(state) is dict and type(state.get('terminated')) is dict
                and type(row.get('restartCount')) is int and row['restartCount'] == 0
                and type(row.get('imageID')) is str and row['imageID'].endswith('@' + image_digest),
                'animation_candidate_pod_not_stopped')
        if completed_worker:
            require(state['terminated'].get('exitCode') == 0,
                    'animation_candidate_worker_failed')


@dataclass(frozen=True)
class VerifiedCandidate:
    role: str
    envelope: ExecutionEnvelope
    state: str
    artifact: dict | None
    frame_receipt: dict | None
    observation: dict | None
    provenance: dict


def validate_candidate(directory, *, role, document, files, expected_image_digest, expected_profile=None):
    """Verify one immutable operator export against the exact business input.

    No artifact, image or exported process is executed. Completed visual roles
    need actual proxy transport evidence. A stopped, controller-recorded failed
    result is importable as failed, consuming its allowance without a retry.
    Versioned sequence/batch reviews require an explicit expected_profile. They share the
    frozen evidence schema, but its execution identity cannot be relabelled.
    """
    require(role in ROLE_PROFILES and type(expected_image_digest) is str
            and re.fullmatch(r'sha256:[0-9a-f]{64}', expected_image_digest) is not None)
    profile = ROLE_PROFILES[role] if expected_profile is None else expected_profile
    allowed_profiles = {ROLE_PROFILES[role]}
    if role == 'review':
        allowed_profiles.update({visual.REVIEW_SEQUENCE_PROFILE, visual.REVIEW_BATCH_PROFILE})
    require(type(profile) is str and profile in allowed_profiles,
            'animation_candidate_profile_not_allowed')
    root = Path(directory).absolute() / 'candidate-output'
    raw = {name: _read(root, name, MAX_EXPORT_BYTES) for name in EXPORT_NAMES}
    exports = {name: decode_video_json(value) for name, value in raw.items()}
    require(all(type(value) is dict for value in exports.values()))
    request, submitted = exports['candidate-request.json'], exports['candidate-result.json']
    record, summary = exports['candidate-evidence.json'], exports['operator-summary.json']
    require(request.get('schema_version') == 'agent-candidate-request.v1'
            and request.get('candidate_scope_only') is True and type(submitted.get('observations')) is dict)
    try:
        envelope = ExecutionEnvelope.model_validate(request['envelope'])
        result = WorkerResult.model_validate(submitted['result'])
    except (KeyError, ValueError, TypeError):
        raise ServiceError('animation_candidate_wire_invalid', 422) from None
    input_name = creation.INPUT_NAME if role == 'create' else (
        visual.REVIEW_INPUT_NAME if role == 'review' else visual.OBSERVE_INPUT_NAME)
    result_name = input_name.replace('-input.json', '-result.json')
    validator = creation.validate_creation_input if role == 'create' else (
        visual.validate_review_input if role == 'review' else visual.validate_observation_input)
    validator(document)
    require(type(files) is dict and files.get(input_name) == canonical_bytes(document)
            and envelope.profile == profile and envelope.profile_revision == '1'
            and envelope.engine == 'pi' and envelope.image_digest == expected_image_digest
            and envelope.task == {'input_sha256': digest(document)}
            and len(envelope.inputs) == len(files)
            and {item.name: item.content() for item in envelope.inputs} == files
            and not envelope.checkpoint_inputs and envelope.checkpoint_ref is None,
            'animation_candidate_input_binding_mismatch')
    scenario = exports['scenario.json']
    require(scenario.get('candidate_scope_only') is True and scenario.get('profile') == profile
            and scenario.get('task') == envelope.task and type(scenario.get('inputs')) is list
            and all(type(row) is dict for row in scenario['inputs'])
            and len(scenario['inputs']) == len(files)
            and {row.get('name'): (row.get('sha256'), row.get('bytes')) for row in scenario['inputs']}
            == {name: (hashlib.sha256(value).hexdigest(), len(value)) for name, value in files.items()},
            'animation_candidate_scenario_binding_mismatch')
    from agent_service.deploy.acceptance_probe import candidate_config
    from agent_service.worker_entry import _host_registry
    from agent_service.registry import binding_fingerprint
    host, policy, binding = candidate_config(exports['host-config.json'])
    registry = _host_registry(host)
    combination = binding_fingerprint(registry.contract(binding.profile, binding.profile_revision),
        registry.bundle(binding.bundle.name, binding.bundle.revision),
        registry.environment(binding.tool_environment.name, binding.tool_environment.revision), binding)
    require(policy.matches(envelope) and envelope.execution_binding is not None
            and record.get('schema_version') == 'agent-candidate-probe.v1'
            and record.get('candidate_only') is True and record.get('candidate_scope_only') is True
            and record.get('production_acceptance_created') is False
            and record.get('production_routes_changed') is False
            and record.get('profile') == profile and record.get('profile_revision') == '1'
            and record.get('engine') == 'pi' and record.get('image_digest') == expected_image_digest
            and record.get('binding_id') == binding.id and record.get('binding_revision') == binding.revision
            and record.get('combination_sha256') == combination
            and record.get('execution_binding_sha256') == envelope.execution_binding.sha256
            and record.get('job_id') == envelope.job_id and record.get('attempt_id') == envelope.attempt_id
            and record.get('request_sha256') == envelope.request_sha256
            and record.get('candidate_result_sha256') == digest(submitted['result'])
            and record.get('result_state') == result.state
            and record.get('observations') == submitted['observations']
            and record.get('harness_sha256') == submitted['observations'].get('harness_sha256')
            and record.get('scenario') == submitted['observations'].get('scenario')
            and record.get('lease_expiry_rejected') is True,
            'animation_candidate_controller_evidence_mismatch')
    proof = result.stop_receipt
    require(proof is not None and proof.job_id == envelope.job_id and proof.attempt_id == envelope.attempt_id
            and proof.fence == envelope.fence and proof.engine == envelope.engine
            and proof.request_sha256 == envelope.request_sha256 and proof.result_sha256 == result.result_digest()
            and 0 < proof.observed_at <= time.time() + 5
            and len(set(proof.resource_ids)) == len(proof.resource_ids)
            and all(type(value) is str and 1 <= len(value) <= 200 for value in proof.resource_ids)
            and record.get('writer_resources') == proof.resource_ids
            and record.get('writers_observed_stopped_at') == proof.observed_at,
            'animation_candidate_stop_receipt_mismatch')
    completed = result.state == 'completed'
    require(result.state != 'quarantined' and summary.get('status') == ('completed' if completed else 'candidate_failed')
            and summary.get('profile') == profile and summary.get('engine') == 'pi'
            and summary.get('image_identity_valid') is True
            and summary.get('production_acceptance_created') is False
            and summary.get('production_routes_changed') is False,
            'animation_candidate_operator_evidence_mismatch')
    plan = exports['manifest.json']
    require(type(plan.get('items')) is list and all(type(row) is dict for row in plan['items'])
            and all(type(row.get('metadata')) is dict and type(row['metadata'].get('namespace')) is str
                    and type(row['metadata'].get('name')) is str
                    for row in plan['items'] if row.get('kind') == 'Pod'),
            'animation_candidate_pod_invalid')
    planned = {(row['metadata']['namespace'], row['metadata']['name']):row for row in plan['items']
               if row.get('kind') == 'Pod'}
    require(len(planned) == sum(row.get('kind') == 'Pod' for row in plan['items']) == 2,
            'animation_candidate_pod_identity_mismatch')
    name = Path(directory).name
    for namespace, suffix, file_name in (('agent-workers', '-worker', 'worker-pod.json'),
                                       ('agent-service', '-proxy', 'proxy-pod.json')):
        require((namespace, name + suffix) in planned, 'animation_candidate_pod_identity_mismatch')
        _pod(exports[file_name], planned[namespace, name + suffix], expected_image_digest,
             completed_worker=completed and suffix == '-worker')
    require(exports['worker-pod.json']['metadata'].get('uid') == record.get('pod_uid')
            and type(record.get('pod_uid')) is str, 'animation_candidate_pod_identity_mismatch')
    expected_artifacts = [dict(name=item.name, sha256=item.sha256, bytes=len(item.content()))
                          for item in result.artifacts]
    require(record.get('artifact_count') == len(result.artifacts) and record.get('artifacts') == expected_artifacts,
            'animation_candidate_artifact_binding_mismatch')
    artifact, receipt, observation = None, None, None
    if completed:
        require(len(result.artifacts) == 1 and result.artifacts[0].name == result_name,
                'animation_candidate_artifact_binding_mismatch')
        artifact_raw = result.artifacts[0].content()
        exported = _read(root, result_name, MAX_EXPORT_BYTES)
        require(exported == artifact_raw, 'animation_candidate_artifact_binding_mismatch')
        raw[result_name] = exported
        validate_result = creation.validate_creation_result if role == 'create' else (
            visual.validate_review_result if role == 'review' else visual.validate_observation_result)
        artifact = validate_result(decode_video_json(exported), digest(document), document)
        if role != 'create':
            receipt = record.get('model_image_evidence')
            require(type(receipt) is dict and receipt.get('job_id') == envelope.job_id
                    and receipt.get('attempt_id') == envelope.attempt_id
                    and receipt.get('request_sha256') == envelope.request_sha256,
                    'animation_candidate_image_evidence_mismatch')
            observation = validate_model_frame_receipt(document, receipt)
    provenance = dict(schema='video.animation-candidate-origin.v1', execution_path='isolated_candidate_import',
        role=role, profile=profile, job_id=envelope.job_id, attempt_id=envelope.attempt_id,
        request_sha256=envelope.request_sha256, input_sha256=digest(document),
        image_digest=expected_image_digest, execution_binding_sha256=envelope.execution_binding.sha256,
        files_sha256={name: hashlib.sha256(value).hexdigest() for name,value in raw.items()},
        stop_signature_verified_by_candidate_controller=True, stop_signature_verified_locally=False,
        external_pods_observed_stopped=True, live_http_verified=False, new_model_submissions=0,
        production_routes_changed=False, production_acceptance_created=False)
    return VerifiedCandidate(role, envelope, 'completed' if completed else 'failed', artifact,
                             receipt, observation, provenance)


def _import_stage(store, operation, candidate, result, issues):
    """Narrow operator extension using the ledger's transaction/audit helpers.

    Never call submission transitions for work that already ran historically.
    The ordinary reserve method has already enforced lineage and stage limits.
    """
    outcome = dict(status=candidate.state, result=result, issues=issues, output_media_versions=[])
    origin_sha = digest(candidate.provenance)
    with store._transaction() as db:
        value = store._operation(db, operation['id'])
        if value['status'] in TERMINAL:
            require(value.get('outcome_sha256') == digest(outcome)
                    and value.get('candidate_origin_sha256') == origin_sha,
                    'animation_candidate_import_conflict', 409)
            return value
        require(value['status'] == 'reserved', 'animation_candidate_import_existing_submission', 409)
        value.update(**outcome, job_id=candidate.envelope.job_id, outcome_sha256=digest(outcome),
            result_sha256=digest(result), issues_sha256=digest(issues),
            execution_path='isolated_candidate_import', candidate_origin=deepcopy(candidate.provenance),
            candidate_origin_sha256=origin_sha)
        return store._write(db, value, 'isolated_candidate_import',
            candidate_origin_sha256=origin_sha, job_id=candidate.envelope.job_id,
            historical_execution=True, new_model_submissions=0, live_http_verified=False)


def _immutable(path, value):
    raw = canonical_bytes(value)
    if path.exists():
        require(_read(path.parent, path.name, MAX_EXPORT_BYTES) == raw,
                'animation_candidate_import_audit_conflict', 409)
        return
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _candidate_paths(root, directories):
    """Resolve explicit unique run names inside this trusted operator root.

    Mapping values are direct role-scoped child names, never paths. Defaults
    retain the original layout; no prior directory is moved or relabelled.
    """
    require(directories is None or type(directories) is dict,
            'animation_candidate_directory_invalid')
    supplied = {} if directories is None else directories
    require(set(supplied) <= set(ROLE_PROFILES), 'animation_candidate_directory_invalid')
    names = {role: 'candidate-animation-' + role + '-p1' for role in ROLE_PROFILES}
    names.update(supplied)
    require(all(type(name) is str and re.fullmatch(
        r'candidate-animation-' + role + r'-[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?', name)
        is not None for role, name in names.items()), 'animation_candidate_directory_invalid')
    require(len(set(names.values())) == len(names), 'animation_candidate_directory_alias')
    require(root.is_dir() and not any(path.is_symlink() for path in (root, *root.parents))
            and root.resolve() == root, 'animation_candidate_directory_invalid')
    paths = {role: root / name for role, name in names.items()}
    require(all(not path.is_symlink() and path.resolve() == path for path in paths.values()),
            'animation_candidate_directory_alias')
    return paths


def import_phase_one(root, *, owner, project_id, expected_image_digest, stage='review', review_directory=None,
                     candidate_directories=None):
    """Import review before preparing create, then import it with --stage revision.

    Only the separate SQLite ledger and importer audit receipts are written.
    The candidate directories, contexts and all source/media remain read-only.
    --stage all additionally requires the separately observed reference export.
    Explicit role directories select a unique run without renaming old exports.
    """
    require(stage in ('review', 'revision', 'all'))
    root = Path(root).absolute()
    candidate_paths = _candidate_paths(root, candidate_directories)
    if review_directory is None:
        selected = (_json(root / 'review-selection.json').get('directory')
                    if (root / 'review-selection.json').exists() else 'review-original')
        require(type(selected) is str and re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,99}', selected) is not None,
                'animation_candidate_review_selection_invalid')
        review_directory = root / selected
    else:
        review_directory = Path(review_directory)
        if not review_directory.is_absolute():
            review_directory = root / review_directory
    base, context = _json(root / 'base-source.json'), _json(root / 'creation-context.json')
    creation.validate_creation_input(context)
    require(context['base_source'] is None and context['review'] is None,
            'animation_candidate_original_context_required')
    require(type(base) is dict and set(base) == {'engine', 'files', 'source_revision', 'source_sha256'}
            and creation.source_sha256(base) == base['source_sha256'])
    review_profile, review_doc, review_files = load_bundle(review_directory)
    require(review_profile in (visual.REVIEW_PROFILE, visual.REVIEW_SEQUENCE_PROFILE, visual.REVIEW_BATCH_PROFILE)
            and review_doc['source_sha256'] == base['source_sha256'])
    observer = None
    observe_directory = candidate_paths['observe'] / 'candidate-output'
    if stage == 'all' or observe_directory.exists():
        profile, doc, files = load_bundle(root / 'observation-reference')
        require(profile == visual.OBSERVE_PROFILE)
        observer = validate_candidate(observe_directory.parent, role='observe', document=doc,
            files=files, expected_image_digest=expected_image_digest)
    reviewer = validate_candidate(candidate_paths['review'], role='review',
        document=review_doc, files=review_files, expected_image_digest=expected_image_digest,
        expected_profile=review_profile)
    creator, revised_input = None, None
    if stage in ('revision', 'all') and reviewer.state == 'completed' and reviewer.artifact['issues']:
        revised_input = creation.validate_creation_input(dict(context, base_source=base, review=reviewer.artifact))
        creator = validate_candidate(candidate_paths['create'], role='create',
            document=revised_input, files={creation.INPUT_NAME:canonical_bytes(revised_input)},
            expected_image_digest=expected_image_digest)
    # Validate every requested export before allocating any new ledger slot.
    store = AnimationRevisionStore(root / 'revision-ledger.sqlite3')
    lineage = store.create_lineage(owner, project_id, base['source_revision'], base['source_sha256'],
                                   'candidate-import:' + digest(dict(source=base['source_sha256'], context=context)))
    media_versions = [dict(version_id=base['source_revision'], sha256=review_doc['media']['sha256'],
                     source_sha256=base['source_sha256'], creation_input_sha256=digest(context))]
    review_id = lineage['id'] + ':review'
    versions = store.review_media_versions(review_id, media_versions, review_profile,
                                          reviewer.envelope.profile_revision)
    operation = store.reserve(lineage['id'], 'review', review_id, review_id, digest(review_doc), versions)
    result = (dict(review=reviewer.artifact, observation=reviewer.observation,
                   model_frame_receipt=reviewer.frame_receipt) if reviewer.state == 'completed'
              else dict(candidate_failed=True, candidate_origin_sha256=digest(reviewer.provenance)))
    issues = reviewer.artifact['issues'] if reviewer.state == 'completed' else []
    imported_review = _import_stage(store, operation, reviewer, result, issues)
    imported_revision = None
    if creator is not None:
        revision_id = lineage['id'] + ':revision'
        versions = store.review_media_versions(review_id, media_versions, review_profile,
            reviewer.envelope.profile_revision, operation_id=revision_id)
        operation = store.reserve(lineage['id'], 'revision', revision_id, revision_id, digest(revised_input), versions)
        result = creator.artifact if creator.state == 'completed' else dict(
            candidate_failed=True, candidate_origin_sha256=digest(creator.provenance))
        imported_revision = _import_stage(store, operation, creator, result, [])
    audit = root / 'candidate-import-audit'
    audit.mkdir(mode=0o700, exist_ok=True)
    for candidate in (observer, reviewer, creator):
        if candidate is not None:
            record = dict(origin=candidate.provenance, artifact=candidate.artifact,
                          model_frame_receipt=candidate.frame_receipt, observation=candidate.observation,
                          candidate_state=candidate.state)
            _immutable(audit / (candidate.role + '-' + digest(record) + '.json'), record)
    receipt = dict(schema='video.animation-candidate-import.v1', execution_path='isolated_candidate_import',
        lineage_id=lineage['id'], owner=owner, project_id=project_id, source_sha256=base['source_sha256'],
        review_directory=Path(review_directory).name, review_input_sha256=digest(review_doc),
        review_operation_id=review_id, review_status=imported_review['status'],
        revision_operation_id=imported_revision['id'] if imported_revision else None,
        revision_status=imported_revision['status'] if imported_revision else None,
        observation_status=observer.state if observer else 'not_imported',
        new_model_submissions=0, live_http_verified=False, verification_started_by_importer=False,
        production_routes_changed=False, production_acceptance_created=False, aesthetic_status='unreviewed')
    if candidate_directories is not None:
        receipt['candidate_directories'] = {role: path.name for role, path in candidate_paths.items()}
    _immutable(audit / ('receipt-' + digest(receipt) + '.json'), receipt)
    target = root / 'import-receipt.json'
    temporary = root / ('.import-receipt-' + digest(receipt) + '.json')
    _immutable(temporary, receipt)
    temporary.replace(target)
    return receipt


class ImportedCandidateOnlyGateway:
    """Guard for coordinator replay: every execution-gateway action is disabled."""
    def _disabled(self, *args, **kwargs):
        raise ServiceError('animation_candidate_import_execution_disabled', 409)
    preflight = upload = submit = lookup = poll = collect = model_frame_receipt = _disabled


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--expected-image-digest', required=True)
    parser.add_argument('--stage', choices=['review', 'revision', 'all'], default='review')
    parser.add_argument('--review-directory', type=Path)
    for role in ROLE_PROFILES:
        parser.add_argument('--' + role + '-candidate-directory',
            help='Direct role-scoped candidate directory name inside --root')
    args = parser.parse_args(argv)
    try:
        receipt = import_phase_one(args.root, owner=args.owner, project_id=args.project_id,
            expected_image_digest=args.expected_image_digest, stage=args.stage,
            review_directory=args.review_directory, candidate_directories={
                role: getattr(args, role + '_candidate_directory') for role in ROLE_PROFILES
                if getattr(args, role + '_candidate_directory') is not None} or None)
    except (ServiceError, OSError, ValueError, KeyError, TypeError) as error:
        code = error.code if isinstance(error, ServiceError) else 'animation_candidate_import_invalid'
        print(json.dumps(dict(status='rejected', code=code, new_model_submissions=0)))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt['review_status'] == 'completed' and receipt['revision_status'] != 'failed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
