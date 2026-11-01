"""Independent neutral PPT validator and its own stopped Kubernetes sandbox.

Model-worker receipts are never substituted for validator stop evidence. The
Kubernetes transport observes its exact sandbox Pod UID and all terminated
containers, freezes ordinary files, and only then issues an in-process receipt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from agent_service.artifacts import read_output
from agent_service.contracts import digest
from agent_service.profiles.ppt import PPTProfile, validate_checkpoint
from ppt_service.server.quality_diagnostics import known_code
from .execution import ControlError
from .staging import atomic_json, copy_tree, make_readonly, read_json, relative

_ISSUER = object()
TRANSPORT_SCHEMA = 'ppt-agent-validation-transport.v1'
FAILURE_TRANSPORT_SCHEMA = 'ppt-agent-validation-failure-transport.v1'


def _measured_tree(root, rows, *, limit=512 * 1024**2, max_files=4096, exclude=()):
    """Recheck the entire stopped tree, including undeclared files and links."""
    require(isinstance(rows, list) and len(rows) <= max_files, 'agent_validation_roster_mismatch')
    indexed, folded, directories, total = {}, set(), set(), 0
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'path', 'bytes', 'sha256'},
                'agent_validation_roster_mismatch')
        key = relative(row['path'])
        require(key.casefold() not in folded and key not in exclude
                and type(row['bytes']) is int and 0 <= row['bytes'] <= 64 * 1024**2
                and isinstance(row['sha256'], str) and re.fullmatch('[0-9a-f]{64}', row['sha256']),
                'agent_validation_roster_mismatch')
        indexed[key] = row; folded.add(key.casefold()); total += row['bytes']
        require(total <= limit, 'agent_validation_roster_mismatch')
        parts = key.split('/')
        directories.update('/'.join(parts[:i]) for i in range(1, len(parts)))
    require(not (folded & {key.casefold() for key in directories}), 'agent_validation_roster_mismatch')
    actual, visited = set(), 0

    def walk(folder, prefix=''):
        nonlocal visited
        info = folder.lstat(); visited += 1
        require(visited <= 65536 and stat.S_ISDIR(info.st_mode)
                and not folder.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'agent_validation_roster_mismatch')
        with os.scandir(folder) as entries:
            for entry in entries:
                key = prefix + entry.name
                if not prefix and key in exclude:
                    continue
                key = relative(key); node = Path(entry.path); info = entry.stat(follow_symlinks=False)
                require(not entry.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400,
                        'agent_validation_roster_mismatch')
                if stat.S_ISDIR(info.st_mode):
                    walk(node, key + '/')
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                            and key in indexed and len(actual) < max_files, 'agent_validation_roster_mismatch')
                    raw = read_output(root, key, limit=64 * 1024**2)
                    require(len(raw) == indexed[key]['bytes']
                            and hashlib.sha256(raw).hexdigest() == indexed[key]['sha256'],
                            'agent_validator_evidence_changed')
                    actual.add(key)

    walk(Path(root))
    require(actual == set(indexed), 'agent_validation_roster_mismatch')
    return indexed, total


def _candidate_snapshot(snapshot, task, source):
    require(hashlib.sha256(read_output(snapshot, 'result/manifest.json', limit=4 * 1024**2)).hexdigest() == source,
            'agent_validation_source_changed')
    manifest = read_json(snapshot / 'result', 'manifest.json')
    cp = read_json(snapshot / 'result', 'checkpoint.json')
    validate_checkpoint(cp, project_id=task['project_id'], snapshot_hash=task['input_snapshot_sha256'])
    require(cp['phase'] == task['phase'] and cp['source_business_job_id'] == task['business_job_id']
            and manifest.get('phase') == task['phase'] and manifest.get('input_snapshot_sha256') == task['input_snapshot_sha256'],
            'agent_validation_identity_mismatch')
    require(manifest.get('project_root') == cp['project_root'], 'agent_validation_root_mismatch')
    _measured_tree(snapshot / 'project', cp['files'], limit=256 * 1024**2, max_files=2046)
    expected = {row['path'][len(cp['project_root']) + 1:]: row for row in cp['files']}
    rows = manifest.get('artifacts')
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
            and len(rows) == len(expected) and {row.get('path') for row in rows} == set(expected),
            'agent_validation_roster_mismatch')
    for row in rows:
        require(row.get('bytes') == expected[row['path']]['bytes'] and row.get('sha256') == expected[row['path']]['sha256'],
                'agent_validation_roster_mismatch')
    return manifest, cp


def require(value, code):
    if not value:
        raise ControlError(code)


class AgentValidationFailure(ControlError):
    """Known rejected output after the validator's own physical stop proof."""
    def __init__(self, code, *, _issuer=None, diagnostic=None, request_sha256=None):
        if _issuer is not _ISSUER:
            raise TypeError('A stopped validator must issue this failure')
        super().__init__(code)
        self.validator_stopped = True
        import copy
        self._issuer = _issuer
        self._diagnostic = copy.deepcopy(diagnostic)
        self._request_sha256 = request_sha256
        self._seal = digest(self._diagnostic)

    def diagnostic(self, request):
        """Only a bound frozen rejection can supply a public explanation."""
        import copy
        if (self._issuer is _ISSUER and self._request_sha256 == digest(request)
                and digest(self._diagnostic) == self._seal):
            return copy.deepcopy(self._diagnostic)
        return None


class AgentValidationReceipt:
    __slots__ = ('_issuer', '_report', '_seal', '_source', 'evidence_path')

    def __init__(self, *args, **kwargs):
        raise TypeError('Only a stopped independent sandbox issues this receipt')

    @classmethod
    def _issue(cls, report, source, evidence):
        import copy
        result = object.__new__(cls)
        result._issuer = _ISSUER; result._report = copy.deepcopy(report)
        result._seal = digest(result._report); result._source = source
        result.evidence_path = Path(evidence)
        return result

    def verify(self, source):
        return self._issuer is _ISSUER and self._source == source and digest(self._report) == self._seal

    def report(self):
        import copy
        return copy.deepcopy(self._report)


def validate_private_candidate(snapshot, request, *, gate_runner=None, evidence_root=None):
    """Runs in a network-isolated validator, not in the web/controller process."""
    from ppt_service.worker.candidate_validation import validate_manifest
    from .planning import project_planning
    from .publisher import OfficialGates, publication_readiness
    task = request['task']; profile = PPTProfile(task['phase']); profile.validate_task(task)
    require(request.get('execution_contract') == 'ppt-agent.v1'
            and request.get('job_id') == task['business_job_id'] and request.get('phase') == task['phase']
            and request.get('input_snapshot_sha256') == task['input_snapshot_sha256']
            and type(request.get('pages')) is int
            and request['pages'] == task['input_snapshot']['settings'].get('page_count', 8),
            'agent_validation_identity_mismatch')
    raw = read_output(snapshot, 'result/manifest.json', limit=4 * 1024**2)
    source = hashlib.sha256(raw).hexdigest()
    require(source == request.get('source_manifest_sha256'), 'agent_validation_source_changed')
    manifest, cp = _candidate_snapshot(snapshot, task, source)
    root = snapshot / 'project' / relative(cp['project_root'])
    if task['phase'] in ('generate', 'revise'):
        require(isinstance(request.get('expected_charts'), list), 'trusted_chart_oracle_required')
    integrity = validate_manifest(snapshot / 'project', manifest, expected_charts=request.get('expected_charts'))
    base = {'phase': task['phase'], 'input_snapshot_sha256': task['input_snapshot_sha256'],
            'source_manifest_sha256': source, 'validation_nonce': request['validation_nonce'],
            'publication_authorized': False, 'manifest': manifest, 'integrity': integrity}
    if manifest['status'] == 'needs_input':
        require(isinstance(manifest.get('questions'), list) and bool(manifest['questions']), 'agent_questions_required')
        return dict(base, status='needs_input', accepted=False, questions=manifest['questions'])
    if task['phase'] in ('plan_initial', 'plan_options'):
        require(manifest['status'] == 'awaiting_confirmation'
                and not (root / 'design_spec.md').exists() and not (root / 'spec_lock.md').exists(), 'premature_formal_spec')
        projection = project_planning(manifest, source_manifest_sha256=source, expected_page_count=request['pages'])
        return dict(base, accepted=True, status='awaiting_confirmation', **projection)
    for name in ('design_spec.md', 'spec_lock.md'):
        content = read_output(root, name, limit=4 * 1024**2)
        if task['phase'] == 'generate':
            require(hashlib.sha256(content).hexdigest() == task['checkpoint']['approved_specs'].get(name), 'approved_spec_lock_changed')
    with tempfile.TemporaryDirectory(prefix='ppt-agent-independent-') as temp:
        scratch = Path(temp)
        project = scratch / 'project' / root.name
        copy_tree(root, project)
        from .staging import make_writable
        make_writable(project)
        if task['phase'] == 'prepare':
            # Portable checkpoints carry files, so the official init's empty
            # output directory is absent after transport. Recreate only this
            # known empty structure in the disposable validation copy.
            (project / 'svg_output').mkdir(exist_ok=True)
        # Keep image-owned check output outside the disposable source copy.
        # This remains private administrator evidence, never browser content.
        gate_output = Path(evidence_root) / 'official-gates' if evidence_root is not None else scratch / 'evidence'
        gates = gate_runner or OfficialGates(gate_output)
        evidence = gates.run(project, task['phase'], request['pages'])
    require(evidence.get('official_gates_verified') is True, 'independent_quality_gate_failed')
    if task['phase'] == 'prepare':
        require(manifest['status'] == 'prepared' and not manifest.get('slides'), 'premature_slide_generation')
        return dict(base, accepted=True, status='prepared', evidence=evidence)
    require(manifest['status'] == 'candidate', 'agent_candidate_incomplete')
    from .render_validation import verify_programmatic
    require(evidence_root is not None, 'independent_evidence_retention_required')
    validation_id = str(uuid4())
    issued = verify_programmatic(snapshot, manifest, request, Path(evidence_root) / validation_id, validation_id)
    return publication_readiness(task['phase'], dict(base, accepted=False, status='candidate', evidence=evidence),
                programmatic_evidence=issued, validation_id=validation_id, source_manifest_sha256=source)


@contextmanager
def _exclusive(path):
    """Serialize recovery/start for this validator scope across controller restarts."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ControlError('agent_validation_busy') from None
        yield
    finally:
        os.close(fd)


_ACCEPTANCE_RUNTIME_KEYS = frozenset(('execution_image', 'namespace', 'pvc_name',
    'pvc_data_prefix', 'worker_service_account', 'provider_secret', 'provider_secret_key'))
_ACCEPTANCE_SCOPE_LIMIT = 32


def _acceptance_runtime_scope(signature, runtime):
    return str(uuid5(NAMESPACE_URL, 'ppt-acceptance-runtime:' + signature + ':' + digest(runtime)))


def _acceptance_scope_index(data_root, signature):
    """Read only a bounded, no-link index of derived scope IDs and full pins."""
    folder = data_root / 'acceptance-scopes'
    folder.mkdir(mode=0o700, exist_ok=True)
    info = folder.lstat()
    require(stat.S_ISDIR(info.st_mode) and not folder.is_symlink()
            and not getattr(info, 'st_file_attributes', 0) & 0x400, 'acceptance_scope_registry_invalid')
    folder.chmod(0o700)
    path = folder / (signature + '.json')
    if not path.exists() and not path.is_symlink():
        return path, []
    body = read_json(folder, path.name, 128 * 1024)
    require(isinstance(body, dict) and set(body) == {'schema', 'binding_sha256', 'scopes'}
            and body['schema'] == 'ppt-service.acceptance-scopes.v1'
            and body['binding_sha256'] == signature and isinstance(body['scopes'], list)
            and len(body['scopes']) <= _ACCEPTANCE_SCOPE_LIMIT, 'acceptance_scope_registry_invalid')
    seen = set()
    for row in body['scopes']:
        require(isinstance(row, dict) and set(row) == {'scope_uid', 'runtime'}
                and isinstance(row['runtime'], dict) and set(row['runtime']) == _ACCEPTANCE_RUNTIME_KEYS
                and all(isinstance(value, str) and 0 < len(value) <= 512 for value in row['runtime'].values()),
                'acceptance_scope_registry_invalid')
        expected = _acceptance_runtime_scope(signature, row['runtime'])
        require(row['scope_uid'] == expected and expected not in seen, 'acceptance_scope_registry_invalid')
        seen.add(expected)
    return path, body['scopes']


def _save_acceptance_scope_index(path, signature, rows):
    body = {'schema': 'ppt-service.acceptance-scopes.v1',
        'binding_sha256': signature, 'scopes': rows}
    require(len(json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()) <= 128 * 1024,
            'acceptance_scope_registry_limit')
    atomic_json(path, body)
    path.chmod(0o600)
    # Persist both the index rename and (on its first run) its parent entry
    # before any Pod POST intent. A registered journal always precedes indexing.
    for folder in (path.parent, path.parent.parent):
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _recover_acceptance_scope(backend, scope, *, runtime=None, allow_absent=False):
    """Retire only a derived sandbox scope, preserving its original journal."""
    try:
        handle = backend.handle_for_scope(scope)
    except FileNotFoundError:
        require(allow_absent, 'acceptance_scope_authority_missing')
        require(backend.prove_sandbox_scope_absent(scope) is True, 'acceptance_scope_authority_missing')
        return
    require(handle.uid == scope, 'acceptance_validator_scope_mismatch')
    record = backend._read(handle)
    require(record.get('sandbox_only') is True
            and (runtime is None or record.get('runtime') == runtime)
            and all(row.get('role') == 'sandbox' and row.get('resource') in ('pods', 'networkpolicies')
                    for row in record['resources']), 'acceptance_scope_registry_invalid')
    # First reconcile a durable finalizer-release receipt whose Pod has already
    # disappeared; a missing Pod without that authority remains unprovable.
    backend.prove_stopped(handle)
    record = backend._read(handle)
    for row in record['resources']:
        if row['resource'] == 'pods' and not row.get('deleted'):
            backend.finish_sandbox(handle, row['name'])
    proof = backend.prove_stopped(handle)
    require(proof.uid == scope and proof.all_writers_stopped is True, 'acceptance_prior_scope_stop_unverified')
    record = backend._read(handle)
    require(all(row.get('deleted') is True for row in record['resources'] if row['resource'] == 'pods'),
            'acceptance_prior_scope_stop_unverified')
    for row in record['resources']:
        if row['resource'] == 'networkpolicies' and not row.get('deleted'):
            backend.remove_sandbox_policy(handle, row['name'])


class AgentKubernetesSandbox:
    """Reuse only the reviewed sandbox transport, never its Hermes-model checks."""
    def __init__(self, config, settings, *, client=None):
        from .kubernetes_sandbox import KubernetesSandbox
        self.config = config
        self.transport = KubernetesSandbox(config, settings, client=client)

    def parse(self, inputs, inventory):
        return self.transport.parse(inputs, inventory)

    @property
    def scope_uid(self):
        return self.transport.scope_uid

    @scope_uid.setter
    def scope_uid(self, value):
        self.transport.scope_uid = value

    def validate_acceptance(self, snapshot, original_request, acceptance_request):
        """Append separate per-Version evidence after this sandbox's exact stop.

        No model task, original validation receipt, Version or human record is
        changed. A stopped failed attempt is retained and is never blindly
        repeated by the idle coordinator across restarts.
        """
        from ppt_service.server.acceptance.contracts import SourceBinding
        require(os.name == 'posix', 'acceptance_validator_requires_linux')
        snapshot = Path(snapshot).absolute()
        binding = SourceBinding.model_validate(acceptance_request['binding'])
        evidence_id = acceptance_request.get('evidence_id')
        try:
            require(isinstance(evidence_id, str) and str(UUID(evidence_id)) == evidence_id
                    and acceptance_request.get('validation_nonce') == evidence_id,
                    'acceptance_request_binding_invalid')
        except (ValueError, AttributeError):
            raise ControlError('acceptance_request_binding_invalid') from None
        signature = digest(binding.model_dump(mode='json'))
        legacy_scope = str(uuid5(NAMESPACE_URL, 'ppt-acceptance:' + signature))
        base = self.config.data_root / 'projects' / binding.project_id / 'versions' / binding.version_id
        target = base / 'acceptance-evidence' / acceptance_request['evidence_id']
        attempted = self.config.data_root / 'acceptance-attempts' / signature
        with _exclusive(self.config.data_root / 'agent-validator-locks' / (legacy_scope + '.lock')):
            if attempted.exists():
                return {'status': 'already_attempted', 'binding_sha256': signature}
            require(acceptance_request['execution_image'] == self.transport.settings.worker_image
                    and acceptance_request['original_request_sha256'] == digest(original_request),
                    'acceptance_request_binding_invalid')
            self.transport.backend.subpath(snapshot)
            self.transport.backend.subpath(base)
            backend = self.transport.backend
            runtime = backend.runtime_pins()
            require(isinstance(runtime, dict) and set(runtime) == _ACCEPTANCE_RUNTIME_KEYS
                    and all(isinstance(value, str) and 0 < len(value) <= 512 for value in runtime.values())
                    and runtime['execution_image'] == self.transport.settings.worker_image,
                    'acceptance_scope_registry_invalid')
            scope = _acceptance_runtime_scope(signature, runtime)
            index, rows = _acceptance_scope_index(self.config.data_root, signature)
            _recover_acceptance_scope(backend, legacy_scope, allow_absent=True)
            for row in rows:
                _recover_acceptance_scope(backend, row['scope_uid'], runtime=row['runtime'])
            if not any(row['scope_uid'] == scope for row in rows):
                require(len(rows) < _ACCEPTANCE_SCOPE_LIMIT, 'acceptance_scope_registry_limit')
                _recover_acceptance_scope(backend, scope, runtime=runtime, allow_absent=True)
            handle = backend.register_sandbox_scope(scope)
            require(handle.uid == scope and backend._read(handle).get('runtime') == runtime,
                    'acceptance_scope_registry_invalid')
            if not any(row['scope_uid'] == scope for row in rows):
                rows = [*rows, {'scope_uid': scope, 'runtime': runtime}]
                _save_acceptance_scope_index(index, signature, rows)
            self.transport.scope_uid = scope
            root, output, result, code, failure, stopped, handle, name = self.transport._run(
                'ppt_service.control.acceptance_validation', [(snapshot, '/snapshot', True)],
                {'original_request': original_request, 'acceptance_request': acceptance_request},
                'receipt.json', 300)
            require(stopped, 'acceptance_validation_writers_not_stopped')
            require(handle.uid == scope, 'acceptance_validator_scope_mismatch')
            authority = backend._read(handle)
            pods = [row for row in authority['resources'] if row['resource'] == 'pods' and row['name'] == name]
            if (not pods and isinstance(failure, ControlError)
                    and authority.get('launch_not_started') is True
                    and not any(row['resource'] == 'pods' for row in authority['resources'])):
                # _run can reject a fixed entrypoint/mount before writing a Pod
                # POST intent. The private journal proves no writer was launched;
                # this is not a physical Pod-stop receipt or acceptance evidence.
                error = failure.code
                if not isinstance(error, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', error):
                    error = 'acceptance_launch_rejected'
                atomic_json(attempted, {'schema': 'ppt-service.acceptance-attempt.v1',
                    'binding_sha256': signature, 'status': 'launch_rejected',
                    'scope_uid': scope, 'launch_not_started': True, 'pod_post_intents': 0,
                    'evidence_path': str(root), 'writers_stopped': True,
                    'error_code': error})
                make_readonly(root)
                return {'status': 'acceptance_failed', 'binding_sha256': signature,
                        'error_code': error}
            require(len(pods) == 1, 'acceptance_validator_stop_unverified')
            pod = pods[0]
            require(pod.get('deleted') and pod.get('stop_receipt', {}).get('terminated') is True
                    and isinstance(pod.get('api_uid'), str) and bool(pod['api_uid'])
                    and pod['stop_receipt'].get('api_uid') == pod['api_uid'],
                    'acceptance_validator_stop_unverified')
            attempted.parent.mkdir(parents=True, exist_ok=True)
            # A deterministic stopped result, including malformed receipts and
            # copy failures, must never cause a fresh expensive idle-loop retry.
            atomic_json(attempted, {'schema': 'ppt-service.acceptance-attempt.v1',
                'binding_sha256': signature, 'status': 'stopped_pending_inspection',
                'scope_uid': scope, 'pod_uid': pod['api_uid'], 'sandbox_exit_code': code,
                'evidence_path': str(root), 'writers_stopped': True})
            if failure is not None or code != 0:
                # Keep the output beneath its private sandbox; this is not a
                # successful bundle and cannot become publication evidence.
                atomic_json(attempted, {'schema': 'ppt-service.acceptance-attempt.v1',
                    'binding_sha256': signature, 'status': 'failed', 'scope_uid': scope,
                    'pod_uid': pod['api_uid'], 'sandbox_exit_code': code,
                    'evidence_path': str(root), 'writers_stopped': True,
                    'error_code': 'acceptance_validation_failed'})
                make_readonly(root)
                return {'status': 'acceptance_failed', 'binding_sha256': signature}
            require(isinstance(result, dict) and result.get('schema') == 'ppt-service.acceptance-evidence.v1'
                    and result.get('status') in ('passed', 'pending', 'needs_changes')
                    and result.get('evidence_id') == acceptance_request['evidence_id']
                    and result.get('binding_sha256') == signature
                    and result.get('request_sha256') == digest(acceptance_request)
                    and result.get('execution_image') == self.transport.settings.worker_image
                    and result.get('publication_authorized') is False,
                    'acceptance_receipt_binding_invalid')
            require(read_json(output, 'receipt.json') == result
                    and read_json(output, 'request.json') == acceptance_request
                    and not (output / 'transport-manifest.json').exists(), 'acceptance_output_binding_invalid')
            # Pending copies live outside the discoverable UUID bundle root.
            pending = base / ('.acceptance-pending-' + acceptance_request['evidence_id'])
            require(not pending.exists() and not target.exists(), 'acceptance_output_not_fresh')
            inventory = copy_tree(output, pending, max_total=512 * 1024**2, max_files=8192)
            atomic_json(pending / 'transport-manifest.json', {
                'schema': 'ppt-service.acceptance-transport.v1',
                'evidence_id': acceptance_request['evidence_id'], 'binding_sha256': signature,
                'request_sha256': digest(acceptance_request),
                'execution_image': self.transport.settings.worker_image,
                'validator_scope_uid': scope, 'validator_pod_uid': pod['api_uid'],
                'validator_exit_code': code, 'writers_stopped': True, 'transport_verified': True,
                **inventory})
            make_readonly(pending)
            # A non-root cross-parent rename must update this directory's '..'
            # entry. Keep only the private staging root owner-writable; every
            # evidence file and descendant directory remains frozen.
            pending.chmod(0o700)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending, target)
            target.chmod(0o555)
            atomic_json(attempted, {'schema': 'ppt-service.acceptance-attempt.v1',
                'binding_sha256': signature, 'status': result['status'], 'evidence_path': str(target),
                'scope_uid': scope, 'pod_uid': pod['api_uid'], 'writers_stopped': True})
            self.transport._discard(root)
            return {'status': 'acceptance_validated', 'binding_sha256': signature}

    def _read_verified(self, final, source, snapshot, request):
        transport = read_json(final, 'transport-manifest.json')
        require(transport.get('schema_version') == TRANSPORT_SCHEMA
                and transport.get('writers_stopped') is True and transport.get('transport_verified') is True
                and transport.get('source_manifest_sha256') == source
                and transport.get('validation_request_sha256') == digest(request)
                and transport.get('execution_image') == self.transport.settings.worker_image
                and type(transport.get('sandbox_exit_code')) is int and transport['sandbox_exit_code'] == 0
                and isinstance(transport.get('sandbox_pod_uid'), str) and bool(transport['sandbox_pod_uid']),
                'agent_validator_transport_unverified')
        rows, total = _measured_tree(final, transport.get('files'), exclude=('transport-manifest.json',))
        require(type(transport.get('bytes')) is int and transport['bytes'] == total,
                'agent_validator_transport_invalid')
        report = read_json(final, 'validated.json', 8 * 1024**2)
        manifest, _ = _candidate_snapshot(snapshot, request['task'], source)
        require(report.get('source_manifest_sha256') == source
                and report.get('validation_nonce') == transport['validation_nonce']
                and report.get('phase') == request['phase']
                and report.get('input_snapshot_sha256') == request['input_snapshot_sha256']
                and report.get('publication_authorized') is False and report.get('manifest') == manifest,
                'agent_validator_binding_changed')
        status = manifest['status']
        require(report.get('status') == status
                and report.get('accepted') is (status in ('awaiting_confirmation', 'prepared')),
                'agent_validator_binding_changed')
        evidence = report.get('independent_programmatic_evidence')
        if request['phase'] in ('generate', 'revise') and status == 'candidate':
            from .render_validation import RASTER_GATE, DELIVERY_GATE, NATIVE_GATE, fingerprint
            require(isinstance(evidence, dict) and report.get('programmatic_checks_passed') is True
                    and {'independent_visual_review', 'independent_postflight_and_delivery'} <=
                        set(report.get('required_pending_gates', [])), 'agent_validator_evidence_changed')
            validation_id = evidence.get('validation_id')
            try:
                require(isinstance(validation_id, str) and str(UUID(validation_id)) == validation_id,
                        'agent_validator_evidence_changed')
            except (ValueError, AttributeError):
                raise ControlError('agent_validator_evidence_changed') from None
            require(evidence.get('schema_version') == 1
                    and evidence.get('issuer') == 'ppt-service-independent-programmatic-runtime-v1'
                    and evidence.get('source', {}).get('source_manifest_sha256') == source
                    and evidence['source'].get('input_snapshot_sha256') == request['input_snapshot_sha256']
                    and evidence['source'].get('project_root') == manifest['project_root']
                    and evidence['source'].get('expected_charts_canonical_sha256') == fingerprint(request.get('expected_charts'))
                    and evidence.get('controller_nonce') == transport['validation_nonce']
                    and evidence.get('preview_group_stopped') is True
                    and evidence.get('command_groups_stopped') is True
                    and type(evidence.get('render_exit_code')) is int and evidence['render_exit_code'] == 0
                    and type(evidence.get('delivery_exit_code')) is int and evidence['delivery_exit_code'] == 0
                    and evidence.get('independent_visual_review') is False
                    and evidence.get('full_exporter_postflight_verified') is False
                    and {RASTER_GATE, DELIVERY_GATE, NATIVE_GATE} <= set(evidence.get('completed_programmatic_gates', [])),
                    'agent_validator_evidence_changed')
            root = snapshot / 'project' / relative(manifest['project_root'])
            pptxs = [row for row in manifest['artifacts'] if row.get('role') == 'pptx']
            require(len(pptxs) == 1 and evidence['source'].get('pptx') ==
                    {key: pptxs[0][key] for key in ('path', 'bytes', 'sha256')}, 'agent_validator_evidence_changed')
            pngs = evidence.get('pngs')
            require(isinstance(pngs, list) and len(pngs) == len(manifest['slides']) == request['pages'],
                    'agent_validator_evidence_changed')
            for png, slide in zip(pngs, manifest['slides']):
                require(type(png['ordinal']) is int and png['ordinal'] == slide['ordinal'] and png['svg_path'] == slide['svg_path']
                        and hashlib.sha256(read_output(root, slide['svg_path'], limit=8 * 1024**2)).hexdigest() == png['svg_sha256'],
                        'agent_validator_evidence_changed')
                key = 'independent/' + evidence['validation_id'] + '/' + relative(png['relative_path'])
                row = rows.get(key)
                require(row and row['bytes'] == png['bytes'] and row['sha256'] == png['sha256'], 'agent_validator_evidence_changed')
            persisted = read_json(final, 'independent/' + validation_id + '/programmatic-evidence.json', 8 * 1024**2)
            require(persisted == evidence, 'agent_validator_evidence_changed')
        return AgentValidationReceipt._issue(report, source, final)

    def _read_failure_verified(self, final, source, snapshot, request):
        """Read a rejected, stopped sandbox without issuing acceptance authority."""
        transport = read_json(final, 'transport-manifest.json')
        require(transport.get('schema_version') == FAILURE_TRANSPORT_SCHEMA
                and transport.get('writers_stopped') is True and transport.get('transport_verified') is True
                and transport.get('source_manifest_sha256') == source
                and transport.get('validation_request_sha256') == digest(request)
                and transport.get('execution_image') == self.transport.settings.worker_image
                and type(transport.get('sandbox_exit_code')) is int and transport['sandbox_exit_code'] != 0
                and isinstance(transport.get('sandbox_pod_uid'), str) and bool(transport['sandbox_pod_uid']),
                'agent_validator_transport_unverified')
        _, total = _measured_tree(final, transport.get('files'), exclude=('transport-manifest.json',))
        require(type(transport.get('bytes')) is int and transport['bytes'] == total,
                'agent_validator_transport_invalid')
        _candidate_snapshot(snapshot, request['task'], source)
        # Only the report is optional/untrusted here. Stop, frozen output,
        # request and source checks above must fail closed on every read.
        # Replaying a malformed report after a controller restart has the same
        # stopped, generic rejection as the initial frozen-output read.
        try:
            report = read_json(final, 'validated.json', 8 * 1024**2)
        except (ControlError, OSError, ValueError, TypeError):
            return AgentValidationFailure('agent_independent_validation_failed', _issuer=_ISSUER)
        task = request['task']
        bound = (type(report) is dict and report.get('accepted') is False
                and report.get('publication_authorized') is False
                and type(report.get('schema_version')) is int and report['schema_version'] == 1
                and report.get('source_manifest_sha256') == source
                and report.get('validation_nonce') == transport['validation_nonce']
                and report.get('job_id') == request['job_id'] == task['business_job_id']
                and report.get('project_id') == task['project_id']
                and report.get('phase') == request['phase'] == task['phase']
                and report.get('input_snapshot_sha256') == request['input_snapshot_sha256'] == task['input_snapshot_sha256']
                and known_code(report.get('error_code')))
        if not bound:
            return AgentValidationFailure('agent_independent_validation_failed', _issuer=_ISSUER)
        diagnostic = {'schema_version': 1, 'error_code': report['error_code'],
            'publication_authorized': False, 'source_manifest_sha256': source,
            'input_snapshot_sha256': request['input_snapshot_sha256']}
        return AgentValidationFailure(report['error_code'], _issuer=_ISSUER,
            diagnostic=diagnostic, request_sha256=digest(request))

    def _freeze_output(self, output, final, source, request, nonce, code, handle, name, *, failed=False):
        """No output is retained as trusted until this sandbox's stop is checked."""
        require(not (output / 'transport-manifest.json').exists(), 'reserved_validation_output')
        authority = self.transport.backend._read(handle)
        pods = [row for row in authority['resources'] if row['resource'] == 'pods' and row['name'] == name]
        require(len(pods) == 1, 'agent_validator_stop_unverified')
        pod = pods[0]
        require(pod.get('deleted') and pod.get('stop_receipt', {}).get('terminated') is True
                and pod['stop_receipt'].get('api_uid') == pod.get('api_uid')
                and isinstance(pod.get('api_uid'), str) and bool(pod['api_uid']), 'agent_validator_stop_unverified')
        pending = final.with_name('.pending-' + final.name + '-' + nonce)
        inventory = copy_tree(output, pending, max_total=512 * 1024**2, max_files=4096)
        atomic_json(pending / 'transport-manifest.json', {
            'schema_version': FAILURE_TRANSPORT_SCHEMA if failed else TRANSPORT_SCHEMA,
            'source_manifest_sha256': source, 'validation_nonce': nonce,
            'sandbox_pod_uid': pod['api_uid'], 'validation_request_sha256': digest(request),
            'execution_image': self.transport.settings.worker_image,
            'sandbox_exit_code': code, 'writers_stopped': True, 'transport_verified': True, **inventory})
        make_readonly(pending); os.replace(pending, final)

    def validate(self, snapshot, request):
        require(os.name == 'posix', 'agent_kubernetes_validator_requires_linux')
        snapshot = Path(snapshot).absolute(); self.transport.backend.subpath(snapshot)
        source = hashlib.sha256(read_output(snapshot, 'result/manifest.json', limit=4 * 1024**2)).hexdigest()
        scope = str(uuid5(NAMESPACE_URL, 'ppt-agent-validator:' + request['job_id'] + ':' + source))
        final = snapshot.parent / ('agent-validation-' + snapshot.name + '-' + source[:16])
        rejected = snapshot.parent / ('agent-validation-failure-' + snapshot.name + '-' + source[:16])
        with _exclusive(self.config.data_root / 'agent-validator-locks' / (scope + '.lock')):
            if final.exists():
                return self._read_verified(final, source, snapshot, request)
            if rejected.exists():
                raise self._read_failure_verified(rejected, source, snapshot, request)
            backend = self.transport.backend
            h = backend.register_sandbox_scope(scope)
            self.transport.scope_uid = scope
            # Reconcile all previous POST intents before allowing another sandbox.
            record = backend._read(h)
            for entry in record['resources']:
                if entry['resource'] == 'pods' and not entry.get('deleted'):
                    backend.finish_sandbox(h, entry['name'])
            nonce = str(uuid4()); envelope = dict(request, validation_nonce=nonce, source_manifest_sha256=source)
            root, output, result, code, failure, stopped, handle, name = self.transport._run(
                'ppt_service.control.agent_validation', [(snapshot, '/snapshot', True)], envelope,
                'validated.json', self.config.validation_timeout)
            require(stopped, 'agent_validation_writers_not_stopped')
            if failure is not None or code != 0:
                if failure is None and type(code) is int and code != 0:
                    # Freeze administrator evidence even for a malformed result;
                    # only the strict bound reader can issue detailed diagnostics.
                    self._freeze_output(output, rejected, source, request, nonce, code, handle, name, failed=True)
                    self.transport._discard(root)
                    raise self._read_failure_verified(rejected, source, snapshot, request)
                error = failure.code if isinstance(failure, ControlError) else 'agent_independent_validation_failed'
                raise AgentValidationFailure(error, _issuer=_ISSUER)
            self._freeze_output(output, final, source, request, nonce, code, handle, name)
            self.transport._discard(root)
            return self._read_verified(final, source, snapshot, request)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=Path('/snapshot'))
    parser.add_argument('--request', type=Path, default=Path('/request/request.json'))
    parser.add_argument('--output', type=Path, default=Path('/output/validated.json'))
    args = parser.parse_args(argv); request = {}
    try:
        request = read_json(args.request.parent, args.request.name)
        result = validate_private_candidate(args.snapshot, request, evidence_root=args.output.parent / 'independent')
        atomic_json(args.output, result); return 0
    except Exception as exc:
        from ppt_service.worker.candidate_validation import ValidationError
        request = request if type(request) is dict else {}
        code = ('candidate_integrity_failed' if isinstance(exc, ValidationError) else
                exc.code if isinstance(exc, ControlError) and known_code(exc.code) else 'quality_gate_failed')
        task = request.get('task') if type(request.get('task')) is dict else {}
        atomic_json(args.output, {'schema_version': 1, 'accepted': False, 'publication_authorized': False,
                    'error_code': code, 'job_id': request.get('job_id'), 'project_id': task.get('project_id'),
                    'phase': request.get('phase'), 'input_snapshot_sha256': request.get('input_snapshot_sha256'),
                    'validation_nonce': request.get('validation_nonce'),
                    'source_manifest_sha256': request.get('source_manifest_sha256')})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
