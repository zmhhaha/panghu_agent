"""Administrator-only, small candidate scenarios; never production acceptance.

Source bytes stay in a dedicated read-only projected directory, not ConfigMaps.
PPT continuation uses an actual stopped candidate's measured file checkpoint.
Business confirmations are exact operator exports from the test business control
database; this module does not create confirmations or claim a human made them.
The independent operator must still inspect full business validation/publication.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID, uuid5

from agent_service.artifacts import read_output, relative_path, unique_paths
from agent_service.checkpoints import checkpoint_manifest, compatible_checkpoint, PPT_STAGE_EDGES
from agent_service.contracts import ExecutionEnvelope, InlineFile, ServiceError, WorkerResult, canonical_bytes, digest
from agent_service.profiles import implementation
from agent_service.store import Store


MAX_SCENARIO_BYTES = 8 * 1024 * 1024
MAX_PROBE_WIRE = 16 * 1024 * 1024
PARENT_FILES = {'request_sha256': 'candidate-request.json', 'evidence_sha256': 'candidate-evidence.json',
                'result_sha256': 'candidate-result.json', 'pod_sha256': 'worker-pod.json'}


def require(value, code='candidate_scenario_invalid'):
    if not value:
        raise ServiceError(code, 422)


def module_sha256():
    return hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ServiceError('candidate_scenario_invalid', 422) from None


def _sha(value):
    require(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value))


def _uuid(value):
    try:
        require(type(value) is str and str(UUID(value)) == value)
    except (ValueError, AttributeError, TypeError):
        raise ServiceError('candidate_scenario_invalid', 422) from None


def _read(root, name, limit=MAX_PROBE_WIRE):
    root = Path(root).absolute()
    relative_path(name)
    for entry in (root, *root.joinpath(*name.split('/')).relative_to(root).parents):
        target = root / entry if not entry.is_absolute() else entry
        info = target.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400)
        if os.name == 'posix':
            require(info.st_uid == 0 and not info.st_mode & 0o022, 'candidate_source_ownership_invalid')
    target = root.joinpath(*name.split('/'))
    info = target.lstat()
    if os.name == 'posix':
        require(info.st_uid == 0 and not info.st_mode & 0o022, 'candidate_source_ownership_invalid')
    return read_output(root, name, limit=limit)


def _source_members(root, expected):
    info = Path(root).lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        and not getattr(info, 'st_file_attributes', 0) & 0x400)
    if os.name == 'posix':
        require(info.st_uid == 0 and not info.st_mode & 0o022, 'candidate_source_ownership_invalid')
    actual = []
    directories = {'/'.join(name.split('/')[:index]) for name in expected
                   for index in range(1, len(name.split('/')))}
    for directory, folders, files in os.walk(root, followlinks=False):
        current = Path(directory)
        require(all((current / name).relative_to(root).as_posix() in directories for name in folders),
            'candidate_source_members_mismatch')
        for name in [*folders, *files]:
            info = (current / name).lstat()
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400)
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
            if os.name == 'posix':
                require(info.st_uid == 0 and not info.st_mode & 0o022, 'candidate_source_ownership_invalid')
        actual.extend((current / name).relative_to(root).as_posix() for name in files)
        require(len(actual) <= 2050)
    unique_paths(actual)
    require(set(actual) == set(expected), 'candidate_source_members_mismatch')


def verify_parent(documents):
    """Recheck captured authority digests and independently captured Pod stop.

    The old ephemeral receipt key is deliberately not exported. This does not
    assert a new offline HMAC verification: it binds the result to the authority's
    captured verified digest and root-owned operator evidence instead.
    """
    request, evidence, document, pod = (documents[name] for name in PARENT_FILES.values())
    require(type(request) is dict and set(request) == {'schema_version', 'candidate_scope_only', 'envelope'}
        and request['schema_version'] == 'agent-candidate-request.v1' and request['candidate_scope_only'] is True)
    envelope = ExecutionEnvelope.model_validate(request['envelope'])
    result = WorkerResult.model_validate(document['result'])
    proof = result.stop_receipt
    require(result.state == 'completed' and proof is not None and envelope.execution_binding is not None
        and evidence.get('candidate_only') is True and evidence.get('production_acceptance_created') is False
        and evidence.get('production_routes_changed') is False and evidence.get('result_state') == 'completed'
        and evidence.get('lease_expiry_rejected') is True
        and evidence.get('candidate_result_sha256') == digest(document['result'])
        and proof.result_sha256 == result.result_digest()
        and evidence.get('request_sha256') == envelope.request_sha256 == proof.request_sha256
        and evidence.get('job_id') == envelope.job_id == proof.job_id
        and evidence.get('attempt_id') == envelope.attempt_id == proof.attempt_id
        and evidence.get('profile') == envelope.profile and evidence.get('engine') == envelope.engine
        and evidence.get('image_digest') == envelope.image_digest
        and evidence.get('execution_binding_sha256') == envelope.execution_binding.sha256
        and evidence.get('writer_resources') == list(proof.resource_ids)
        and evidence.get('writers_observed_stopped_at') == proof.observed_at
        and evidence.get('harness_sha256') == document['observations'].get('harness_sha256'),
        'candidate_parent_integrity_invalid')
    unique_paths(file.name for file in result.artifacts)
    measured = [dict(name=file.name, sha256=file.sha256, bytes=len(file.content())) for file in result.artifacts]
    require(evidence.get('artifacts') == measured
        and evidence.get('artifact_count') == len(measured), 'candidate_parent_integrity_invalid')
    require(pod.get('metadata', {}).get('uid') == evidence.get('pod_uid')
        and 'pod:' + evidence['pod_uid'] in proof.resource_ids
        and pod.get('status', {}).get('phase') == 'Succeeded', 'candidate_parent_stop_unverified')
    spec, state = pod['spec'], pod['status']
    require(all(spec.get(key, False) is False for key in ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace')),
        'candidate_parent_stop_unverified')
    require(not spec.get('initContainers') and not spec.get('ephemeralContainers')
        and not state.get('initContainerStatuses') and not state.get('ephemeralContainerStatuses'),
        'candidate_parent_stop_unverified')
    containers, statuses = spec.get('containers', []), state.get('containerStatuses', [])
    require(containers and len(containers) == len(statuses)
        and {row['name'] for row in containers} == {row['name'] for row in statuses},
        'candidate_parent_stop_unverified')
    for row in containers:
        require(type(row.get('image')) is str and row['image'].endswith('@' + envelope.image_digest),
            'candidate_parent_image_mismatch')
    for row in statuses:
        ended = row.get('state', {}).get('terminated')
        require(type(ended) is dict and ended.get('exitCode') == 0 and row.get('restartCount') == 0
            and type(row.get('imageID')) is str and row['imageID'].endswith('@' + envelope.image_digest),
            'candidate_parent_stop_unverified')
        try:
            finished = datetime.fromisoformat(ended['finishedAt'].replace('Z', '+00:00'))
            require(finished.tzinfo is not None and finished.timestamp() >= proof.observed_at,
                'candidate_parent_stop_unverified')
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ServiceError('candidate_parent_stop_unverified', 422) from None
    return envelope, result


@dataclass(frozen=True)
class CandidateScenario:
    document: dict
    files: dict[str, bytes]
    checkpoint_files: tuple[InlineFile, ...]
    checkpoint_ref: dict | None
    metadata: dict
    source_dir: Path
    parent_dir: Path | None
    parent_uids: frozenset[int]

    def reserve_uid(self, uid):
        require(not set(range(uid, uid + 3)) & self.parent_uids, 'candidate_parent_uid_reuse')


def load_scenario(path, source_dir, parent_dir, policy):
    """Read only explicitly named admin source files before creating a Job."""
    path, source_dir = Path(path).absolute(), Path(source_dir).absolute()
    value = _json(_read(path.parent, path.name, 1024 * 1024))
    fields = {'schema_version', 'candidate_scope_only', 'scenario_id', 'profile', 'timeout_seconds',
              'task', 'inputs', 'business_export', 'parent'}
    require(type(value) is dict and set(value) == fields
        and value['schema_version'] == 'agent-candidate-scenario.v1' and value['candidate_scope_only'] is True
        and value['profile'] == policy.name and type(value['timeout_seconds']) is int
        and 1 <= value['timeout_seconds'] <= policy.max_timeout_seconds)
    _uuid(value['scenario_id'])
    implementation(policy.name).validate_task(value['task'])
    require(type(value['inputs']) is list and len(value['inputs']) <= policy.max_input_files)
    files, total = {}, 0
    for row in value['inputs']:
        require(type(row) is dict and set(row) == {'name', 'sha256', 'bytes'}
            and type(row['bytes']) is int and 0 <= row['bytes'] <= min(policy.max_input_bytes, MAX_SCENARIO_BYTES))
        relative_path(row['name']); _sha(row['sha256'])
        raw = _read(source_dir, row['name'], min(policy.max_input_bytes, MAX_SCENARIO_BYTES))
        require(len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256'])
        require(row['name'] not in files)
        files[row['name']] = raw
        total += len(raw)
    unique_paths(files)
    expected = set(files)
    export = value['business_export']
    if export is not None:
        require(type(export) is dict and set(export) == {'name', 'sha256'})
        _sha(export['sha256']); relative_path(export['name'])
        require(export['name'] not in expected)
        raw = _read(source_dir, export['name'], 1024 * 1024)
        require(hashlib.sha256(raw).hexdigest() == export['sha256'])
        business = _json(raw)
        require(type(business) is dict and set(business) == {'schema_version', 'source', 'task'}
            and business['schema_version'] == 'agent-candidate-business-export.v1'
            and business['source'] == ('ppt-control-test-database' if policy.name.startswith('ppt.') else 'scientific-test-database')
            and business['task'] == value['task'], 'candidate_business_export_mismatch')
        expected.add(export['name'])
    _source_members(source_dir, expected)
    task, parent = value['task'], value['parent']
    continuation = policy.name.startswith('ppt.') and policy.name != 'ppt.plan_initial.v1'
    require(not (continuation or policy.name == 'research.revise.v1') or export is not None,
        'candidate_business_export_required')
    require((parent is not None) == (continuation or policy.name == 'research.revise.v1'),
        'candidate_parent_required')
    require((parent_dir is not None) == (parent is not None), 'candidate_parent_required')
    checkpoint_files, reference, parent_uids = (), None, set()
    checked_parent = None
    if parent is not None:
        require(type(parent) is dict and set(parent) == set(PARENT_FILES))
        parent_dir = Path(parent_dir).absolute()
        documents = {}
        for key, name in PARENT_FILES.items():
            _sha(parent[key])
            raw = _read(parent_dir, name)
            require(hashlib.sha256(raw).hexdigest() == parent[key], 'candidate_parent_integrity_invalid')
            documents[name] = _json(raw)
        original, result = verify_parent(documents)
        data = {file.name: file.content() for file in result.artifacts}
        parent_uids.update(int(item[4:]) for item in result.stop_receipt.resource_ids
            if item.startswith('uid:') and item[4:].isdigit())
        isolated = documents['candidate-result.json']['observations'].get('isolation', {}).get('uid', [])
        require(type(isolated) is list and all(type(uid) is int for uid in isolated))
        parent_uids.update(isolated)
        require(parent_uids, 'candidate_parent_stop_unverified')
        checked_parent = dict(profile=original.profile, job_id=original.job_id, attempt_id=original.attempt_id,
            pod_uid=documents['candidate-evidence.json']['pod_uid'], request_sha256=original.request_sha256,
            captured_authority_result_verified=True, external_all_containers_stopped=True,
            image_digest=original.image_digest, evidence_sha256=parent['evidence_sha256'])
        if continuation:
            require((original.profile, policy.name) in PPT_STAGE_EDGES
                and original.task['project_id'] == task['project_id']
                and original.task['input_snapshot_sha256'] == task['input_snapshot_sha256']
                and original.task['input_snapshot'] == task['input_snapshot'], 'candidate_parent_lineage_invalid')
            for stage, confirmation in original.task['confirmations'].items():
                require(task['confirmations'].get(stage) == confirmation, 'candidate_parent_lineage_invalid')
            from agent_service.profiles.ppt import checkpoint_descriptor, validate_checkpoint
            checkpoint = _json(data['result/checkpoint.json'])
            validate_checkpoint(checkpoint, project_id=task['project_id'],
                snapshot_hash=task['input_snapshot_sha256'], phase=task['phase'])
            require(checkpoint_descriptor(checkpoint) == task['checkpoint']
                and checkpoint['source_job_id'] == original.job_id
                and checkpoint['source_attempt_id'] == original.attempt_id
                and checkpoint['source_business_job_id'] == original.task['business_job_id']
                and checkpoint['phase'] == original.task['phase'], 'candidate_parent_lineage_invalid')
            manifest = checkpoint_manifest(job_id=original.job_id, attempt_id=original.attempt_id,
                profile=original.profile, profile_revision=original.profile_revision,
                contract_sha256=original.execution_binding.contract_sha256,
                execution_binding=original.execution_binding.model_dump(mode='json'),
                artifacts=[dict(artifact_id=str(uuid5(UUID(original.attempt_id), file.name)),
                    name=file.name, sha256=file.sha256, size=len(data[file.name])) for file in result.artifacts])
            compatible_checkpoint(manifest, policy.pins())
            reference = dict(checkpoint_id=str(uuid5(UUID(original.attempt_id), 'candidate-files:' + manifest['sha256'])),
                             sha256=manifest['sha256'], format='workspace-tree.v1')
            checkpoint_files = tuple(result.artifacts)
            total += sum(map(len, data.values()))
            checked_parent['portable_checkpoint_sha256'] = manifest['sha256']
        else:
            # A revision is a new business task. This candidate export carries
            # no published version-to-source mapping for a previous revision,
            # so only a directly verified generation can be its parent.
            require(original.profile == 'research.generate.v1',
                'candidate_research_revision_parent_unsupported')
            require(task['root_report_id'] == original.task['business_task_id']
                and task['business_task_id'] != original.task['business_task_id']
                and original.task['topic'] == task['topic'], 'candidate_parent_lineage_invalid')
            require('report.md' in data and files.get('input-report.md') == data['report.md'],
                'candidate_parent_lineage_invalid')
            require(task['base_sha256'] == hashlib.sha256(data['report.md']).hexdigest(),
                'candidate_parent_lineage_invalid')
            for name in ('papers.jsonl', 'evidence.jsonl', 'claims.jsonl', 'references.json'):
                require(name in data and files.get(name) == data[name], 'candidate_parent_lineage_invalid')
    if policy.name.startswith('ppt.'):
        extensions = {'text/markdown': '.md', 'text/plain': '.txt', 'application/pdf': '.pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
            'image/png': '.png', 'image/jpeg': '.jpg'}
        assets = task['input_snapshot']['assets']
        require(set(files) == {asset['id'] + extensions[asset['media_type']] for asset in assets})
        for asset in assets:
            raw = files[asset['id'] + extensions[asset['media_type']]]
            require(len(raw) == asset['bytes'] and hashlib.sha256(raw).hexdigest() == asset['sha256'],
                'candidate_source_snapshot_mismatch')
    elif policy.name == 'research.generate.v1':
        require(not files)
    else:
        require(set(files) == {'revision-materials.json', 'input-report.md', 'input-section.md',
                              'papers.jsonl', 'evidence.jsonl', 'claims.jsonl', 'references.json'})
        material = _json(files['revision-materials.json'])
        require(digest(material) == task['input_sha256'] and material['report'].encode() == files['input-report.md'])
        evidence = {name: base64.b64decode(raw, validate=True) for name, raw in material['artifacts'].items()}
        from scientific_agent.pi_revision_validation import validate_revision_input
        section = validate_revision_input(material['report'], material['items'], evidence,
            task['section_id'], task['base_sha256'])['section']
        require(section['sha256'] == task['section_sha256'] and section['text'].encode() == files['input-section.md'])
        for name, raw in evidence.items():
            require(files[name + ('.json' if name == 'references' else '.jsonl')] == raw)
    require(total <= MAX_SCENARIO_BYTES, 'candidate_probe_scale_exceeded')
    metadata = dict(candidate_scope_only=True, scenario_id=value['scenario_id'], scenario_sha256=digest(value),
        scenario_harness_sha256=module_sha256(), parent=checked_parent,
        business_export_sha256=None if export is None else export['sha256'],
        production_acceptance_created=False, source_and_checkpoint_bytes=total,
        probe_wire_limit=MAX_PROBE_WIRE, production_artifact_limit=policy.max_artifact_bytes)
    return CandidateScenario(deepcopy(value), files, checkpoint_files, reference, metadata,
                             source_dir, parent_dir, frozenset(parent_uids))


class CandidateScenarioStore(Store):
    """Probe-only file transport adapter, with no production checkpoint records.

    Every Store envelope calculation uses the same immutable external candidate
    files before reserving an allocation or issuing its grant. No signed scope
    is altered afterward; no historical finish/lease/acceptance is synthesized.
    """
    def __init__(self, *args, scenario, **kwargs):
        self.scenario = scenario
        super().__init__(*args, **kwargs)

    def _worker_envelope(self, conn, row):
        value = super()._worker_envelope(conn, row)
        if self.scenario.checkpoint_ref is None:
            return value
        require(row['request']['task'] == self.scenario.document['task'], 'candidate_scope_mismatch')
        return ExecutionEnvelope.model_validate({**value.model_dump(mode='json'),
            'checkpoint_ref': self.scenario.checkpoint_ref,
            'checkpoint_inputs': [item.model_dump(mode='json') for item in self.scenario.checkpoint_files]})
