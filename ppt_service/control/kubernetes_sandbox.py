"""Network-isolated image-owned parsing/validation with durable stopped output.

Transported JSON remains audit data; this module never reconstructs a trusted
ProgrammaticEvidence object or grants generated-version publication.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

from .execution import Attempt, ControlError, RuntimeConfig
from .kubernetes import KubernetesBackend, KubeSettings, require
from .staging import atomic_json, copy_tree, make_readonly, read_json, relative
from .render_validation import hash_file


class _MissingSandboxResult(FileNotFoundError):
    """Marks a result read failure, distinct from a sandbox lifecycle failure."""
    def __init__(self, output_name):
        super().__init__(output_name)
        self.output_name = output_name


def _review_failure_code(output, envelope, code, failure, result):
    """Read only an image validator's bound failure diagnostic after stop.

    A diagnostic can explain a missing success receipt. It cannot replace a
    lifecycle error or turn any nonzero execution into a candidate result.
    """
    if (type(code) is not int or code == 0 or result is not None
            or not isinstance(failure, _MissingSandboxResult)
            or failure.output_name != 'delivery/candidate-delivery.json'
            or (output / 'delivery' / 'candidate-delivery.json').exists()):
        return None
    try:
        report = read_json(output, 'delivery/failure.json', 16 * 1024)
    except (OSError, ControlError, ValueError, TypeError):
        return None
    fields = {'schema', 'status', 'accepted', 'publication_authorization',
              'review_artifacts_ready', 'error_code', 'validation_nonce', 'source_manifest_sha256'}
    if (not isinstance(report, dict) or set(report) != fields
            or report.get('schema') != 'ppt-service.hermes-review-failure.v1'
            or report.get('status') != 'failed'
            or any(report.get(key) is not False for key in
                   ('accepted', 'publication_authorization', 'review_artifacts_ready'))
            or report.get('validation_nonce') != envelope['validation_nonce']
            or report.get('source_manifest_sha256') != envelope['source_manifest_sha256']):
        return None
    error = report.get('error_code')
    return error if isinstance(error, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', error) else None


class KubernetesSandbox:
    def __init__(self, config: RuntimeConfig, settings: KubeSettings, backend=None, *, client=None):
        self.config, self.settings = config, settings
        self.backend = backend or KubernetesBackend(config, settings, client=client)
        self.scope_uid = None

    def _handle(self):
        if self.scope_uid:
            return self.backend.handle_for_scope(self.scope_uid)
        # Parse-only material coordinator also has a persistent, independently
        # recoverable sandbox scope, without admitting any model phase.
        attempt = Attempt(str(uuid.uuid4()), str(uuid.uuid4()), 1, 1, 'plan_initial', {}, 'sandbox')
        return self.backend.reconcile(attempt)

    def _run(self, module, mounts, request, output_name, timeout):
        h = self._handle()
        nonce = str(uuid.uuid4())
        root = Path(tempfile.mkdtemp(prefix='ppt-kube-sandbox-', dir=self.config.data_root))
        inputs, output = root / 'request', root / 'output'
        inputs.mkdir(mode=0o755)
        output.mkdir(mode=0o777)
        output.chmod(0o777)
        atomic_json(inputs / 'request.json', request)
        make_readonly(inputs)
        name = 'ppt-sandbox-' + nonce.replace('-', '')
        code = None
        failure = None
        submitted = False
        stopped = False
        try:
            policy = self.backend.policy(h, name + '-net', 'sandbox')
            self.backend.create_resource(h, 'networkpolicies', policy, sandbox=True)
            body = self.backend.pod(h, name, 'sandbox', module,
                                    [(inputs, '/request', True), (output, '/output', False), *mounts],
                                    deadline=max(60, int(timeout) + 30))
            submitted = True
            self.backend.create_resource(h, 'pods', body, sandbox=True)
            code = self.backend.wait_pod(h, name, timeout)
        except Exception as exc:
            failure = exc
        finally:
            if submitted:
                try:
                    self.backend.finish_sandbox(h, name)
                    stopped = True
                except Exception:
                    problem = ControlError('validation_writers_not_stopped')
                    atomic_json(root / 'controller-quarantine.json', {'sandbox': name, 'scope_uid': h.uid,
                                'writers_stopped': False, 'transport_verified': False})
                    problem.evidence_path = str(root)
                    raise problem from None
            else:
                stopped = True  # No Pod POST intent was ever issued in this scope.
        if stopped:
            try:
                self.backend.remove_sandbox_policy(h, name + '-net')
            except Exception:
                failure = ControlError('sandbox_policy_cleanup_failed')
        if failure is None:
            try:
                result = read_json(output, output_name, 8 * 1024**2)
                require(isinstance(result, dict), 'invalid_sandbox_result')
            except FileNotFoundError:
                # Only this read site may issue the marker. A FileNotFoundError
                # from Pod creation/wait/cleanup remains a lifecycle failure.
                result, failure = None, _MissingSandboxResult(output_name)
            except Exception as exc:
                result, failure = None, exc
        else:
            result = None
        return root, output, result, code, failure, stopped, h, name

    @staticmethod
    def _discard(root):
        # The caller invokes this only after a recorded physical stop proof.
        for parent, directories, files in os.walk(root):
            Path(parent).chmod(0o700)
            for name in files:
                path = Path(parent) / name
                info = path.lstat()
                # Rejected output may contain links. Never chmod their targets
                # (or a hardlinked original) while removing the private tree.
                if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    path.chmod(0o600)
        shutil.rmtree(root)

    def parse(self, inputs, inventory):
        root, output, result, code, failure, stopped, h, name = self._run(
            'ppt_service.control.parsing', [(Path(inputs), '/inputs', True)], {}, 'parsed.json', self.config.parse_timeout)
        try:
            if failure is not None or code != 0:
                raise failure if isinstance(failure, ControlError) else ControlError('parse_failed')
            require(result.get('input_snapshot_sha256') == inventory.get('input_snapshot_sha256'), 'parse_snapshot_mismatch')
            return result
        finally:
            if stopped:
                self._discard(root)

    def validate(self, snapshot, request):
        h = self._handle()
        authority = self.backend._read(h)
        require(authority['revoked'] and self.backend.prove_stopped(h).all_writers_stopped,
                'validation_model_writers_not_stopped')
        require(h.job_id == request.get('job_id') and h.attempt == request.get('attempt')
                and authority.get('phase') == request.get('phase'), 'validation_scope_mismatch')
        writers = [entry for entry in authority['resources'] if entry['resource'] == 'pods'
                   and entry['role'] in ('worker', 'proxy')]
        require({entry['role'] for entry in writers} == {'worker', 'proxy'}
                and len(writers) == 2 and all(entry.get('stop_receipt') for entry in writers),
                'validation_model_writers_not_stopped')
        snapshot = Path(snapshot).absolute()
        self.backend.subpath(snapshot)
        source_hash = hash_file(snapshot / 'result', 'manifest.json', 4 * 1024**2)['sha256']
        nonce = str(uuid.uuid4())
        require(authority['runtime']['execution_image'] == self.settings.worker_image,
                'execution_runtime_mismatch')
        envelope = dict(request, validation_nonce=nonce, source_manifest_sha256=source_hash,
                        execution_image=self.settings.worker_image)
        retained = snapshot.parent / 'validation-evidence'
        retained.mkdir(exist_ok=True)
        require(not retained.is_symlink(), 'linked_validation_evidence')
        final, pending = retained / nonce, retained / ('.pending-' + nonce)
        review = request.get('phase') in ('generate', 'revise')
        module = 'ppt_service.control.hermes_delivery' if review else 'ppt_service.control.publisher'
        output_name = 'delivery/candidate-delivery.json' if review else 'validated.json'
        if review:
            from .hermes_delivery import MAX_RETAINED_BYTES, MAX_RETAINED_FILES
            limit_bytes, limit_files = MAX_RETAINED_BYTES + 8 * 1024**2, MAX_RETAINED_FILES + 2
        else:
            limit_bytes, limit_files = 128 * 1024**2, 4096
        root, output, result, code, failure, stopped, h, name = self._run(
            module, [(snapshot, '/snapshot', True)], envelope, output_name, self.config.validation_timeout)
        try:
            require(stopped, 'validation_writers_not_stopped')
            require(not (output / 'transport-manifest.json').exists()
                    and not (output / '.controller-quarantine.json').exists(), 'reserved_validation_output')
            inventory = copy_tree(output, pending, max_total=limit_bytes, max_files=limit_files)
            by_path = {row['path']: row for row in inventory['files']}
            require(hash_file(snapshot / 'result', 'manifest.json', 4 * 1024**2)['sha256'] == source_hash,
                    'validation_source_changed')
            if review:
                if ('delivery/candidate-delivery.json' in by_path and 'delivery/failure.json' in by_path
                        and failure is None):
                    failure = ControlError('validation_conflicting_output')
                diagnostic = _review_failure_code(pending, envelope, code, failure, result)
                if diagnostic is not None:
                    failure = ControlError(diagnostic)
            if review and result is not None and failure is None and code == 0:
                from .hermes_delivery import read_candidate_delivery
                try:
                    delivery = read_candidate_delivery(pending / 'delivery', envelope)
                    original = read_json(snapshot / 'result', 'manifest.json')
                    require(original.get('status') == 'candidate' and original.get('phase') == request['phase'],
                            'validation_candidate_mismatch')
                    from .publisher import complete_index
                    completed, _ = complete_index(snapshot, original)
                    result = {'review_candidate': True, 'accepted': False, 'status': 'candidate',
                              'manifest': completed, 'delivery': delivery,
                              'validation_status': 'gate_pending', 'validation_nonce': nonce,
                              'source_manifest_sha256': source_hash,
                              'required_pending_gates': delivery['required_pending_gates'],
                              'completed_programmatic_gates': delivery['completed_programmatic_gates']}
                except (ControlError, KeyError, TypeError, ValueError) as exc:
                    failure = exc if isinstance(exc, ControlError) else ControlError('validation_candidate_mismatch')
            if result is not None and not review:
                if result.get('validation_nonce') != nonce or result.get('source_manifest_sha256') != source_hash:
                    failure = ControlError('validation_envelope_mismatch')
                evidence = result.get('independent_programmatic_evidence')
                if isinstance(evidence, dict):
                    try:
                        self._verify_evidence(snapshot, request, evidence, by_path, nonce, source_hash)
                    except (ControlError, KeyError, ValueError, TypeError):
                        failure = ControlError('validation_artifact_mismatch')
                if request.get('phase') in ('generate', 'revise'):
                    gates = result.get('required_pending_gates')
                    if (result.get('accepted') is not False or (result.get('status') == 'candidate'
                            and (not isinstance(gates, list) or not all(isinstance(g, str) for g in gates)
                                 or not {'independent_visual_review', 'independent_postflight_and_delivery'}.issubset(gates)))):
                        failure = ControlError('publication_guard_violation')
            safe_error = failure.code if isinstance(failure, ControlError) else 'validation_execution_failed'
            if not review and code != 0 and isinstance(result, dict) and not isinstance(failure, ControlError):
                safe_error = result.get('error_code', safe_error)
            if not isinstance(safe_error, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', safe_error):
                safe_error = 'validation_execution_failed'
            verified = failure is None and code == 0
            receipt = self.backend._read(h)
            pod = next(e for e in receipt['resources'] if e['resource'] == 'pods' and e['name'] == name)
            atomic_json(pending / 'transport-manifest.json', {
                'schema_version': 1, 'controller_nonce': nonce, 'source_manifest_sha256': source_hash,
                'execution_image': self.settings.worker_image,
                'pod_uid': pod['api_uid'], 'sandbox_exit_code': code, 'writers_stopped': True,
                'transport_verified': verified, 'controller_error_code': None if verified else safe_error, **inventory})
            make_readonly(pending)
            require(not final.exists(), 'validation_evidence_exists')
            os.replace(pending, final)
            if not verified:
                problem = failure if isinstance(failure, ControlError) else ControlError(safe_error)
                problem.evidence_path = str(final)
                raise problem
            result['persistent_evidence'] = {'path': str(final), 'controller_nonce': nonce,
                'execution_image': self.settings.worker_image,
                'source_manifest_sha256': source_hash, 'transport_manifest': 'transport-manifest.json',
                'transport_verified': True, 'publication_authorization': False,
                'file_count': len(inventory['files']), 'bytes': inventory['bytes']}
            return result
        except Exception as exc:
            if pending.exists() and not final.exists():
                atomic_json(pending / '.controller-quarantine.json', {
                    'controller_nonce': nonce, 'source_manifest_sha256': source_hash,
                    'writers_stopped': True, 'transport_verified': False})
                if isinstance(exc, ControlError):
                    exc.evidence_path = str(pending)
            raise
        finally:
            if stopped:
                self._discard(root)

    @staticmethod
    def _verify_evidence(snapshot, request, evidence, by_path, nonce, source_hash):
        require(evidence.get('source', {}).get('source_manifest_sha256') == source_hash
                and evidence.get('controller_nonce') == nonce, 'validation_envelope_mismatch')
        run_id = str(uuid.UUID(evidence['validation_id']))
        manifest = read_json(snapshot / 'result', 'manifest.json')
        slides, pngs = manifest.get('slides', []), evidence.get('pngs')
        require(isinstance(pngs, list) and type(request.get('pages')) is int
                and len(pngs) == request['pages'] == len(slides), 'validation_artifact_mismatch')
        project = relative(manifest['project_root'])
        seen = set()
        for ordinal, (png, slide) in enumerate(zip(pngs, slides), 1):
            svg = relative(slide['svg_path'])
            expected = 'scratch-projects/' + project + '/.preview/' + Path(svg).stem + '.png'
            require(png.get('relative_path') == expected and expected not in seen
                    and png.get('ordinal') == ordinal and png.get('svg_path') == svg, 'validation_artifact_mismatch')
            seen.add(expected)
            require(hash_file(snapshot / 'project' / project, svg, 8 * 1024**2)['sha256'] == png.get('svg_sha256'),
                    'validation_artifact_mismatch')
            row = by_path.get(relative('independent/' + run_id + '/' + expected))
            require(row and row['sha256'] == png['sha256'] and row['bytes'] == png['bytes'], 'validation_artifact_mismatch')
