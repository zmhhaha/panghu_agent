"""Fenced persistence of independently checked, owner-private review drafts.

Only the trusted controller calls this after actual worker and sandbox teardown.
The sandbox descriptor is checked against its controller-owned transport files;
neither worker JSON nor an HTTP body can enter this boundary. Review artifacts
are never published versions or eligible revision checkpoints.
"""
from __future__ import annotations

from pathlib import Path
import uuid

from .execution import Attempt, ControlError, RuntimeConfig
from .hermes_delivery import read_candidate_delivery
from .publisher import complete_index
from .render_validation import hash_file
from .staging import (
    atomic_json, copy_file, copy_tree, make_readonly, read_json, relative, require_storage,
)


def _require(value, code):
    if not value:
        raise ControlError(code)


def _transport(snapshot, request, validated, config):
    _require(isinstance(validated, dict) and validated.get('accepted') is False
             and validated.get('review_candidate') is True, 'review_delivery_unvalidated')
    descriptor = validated.get('persistent_evidence')
    _require(isinstance(descriptor, dict) and descriptor.get('transport_verified') is True
             and descriptor.get('publication_authorization') is False
             and descriptor.get('transport_manifest') == 'transport-manifest.json', 'review_transport_required')
    _require(isinstance(request.get('execution_image'), str)
             and descriptor.get('execution_image') == request['execution_image'], 'review_execution_image_mismatch')
    nonce = descriptor.get('controller_nonce')
    try:
        _require(isinstance(nonce, str) and str(uuid.UUID(nonce)) == nonce, 'review_transport_nonce_invalid')
    except (ValueError, AttributeError):
        raise ControlError('review_transport_nonce_invalid') from None
    root = snapshot.parent / 'validation-evidence' / nonce
    _require(isinstance(descriptor.get('path'), str) and Path(descriptor['path']).absolute() == root
             and root.is_relative_to(config.data_root.absolute()), 'review_transport_scope_invalid')
    for path in (root, *root.parents):
        _require(not path.is_symlink() and not path.is_junction(), 'review_transport_linked')
    source_sha = hash_file(snapshot / 'result', 'manifest.json')['sha256']
    _require(descriptor.get('source_manifest_sha256') == source_sha
             and validated.get('source_manifest_sha256') == source_sha
             and validated.get('validation_nonce') == nonce, 'review_transport_binding_invalid')
    envelope = dict(request, validation_nonce=nonce, source_manifest_sha256=source_sha)
    transport = read_json(root, 'transport-manifest.json')
    _require(transport.get('schema_version') == 1 and transport.get('controller_nonce') == nonce
             and transport.get('source_manifest_sha256') == source_sha
             and transport.get('transport_verified') is True and transport.get('writers_stopped') is True
             and transport.get('sandbox_exit_code') == 0 and transport.get('controller_error_code') is None
             and isinstance(transport.get('pod_uid'), str) and bool(transport['pod_uid']), 'review_transport_unverified')
    _require(transport.get('execution_image') == request['execution_image'], 'review_execution_image_mismatch')
    rows = transport.get('files')
    _require(isinstance(rows, list) and len(rows) <= 4096
             and len(rows) == descriptor.get('file_count'), 'review_transport_index_invalid')
    indexed, size = {}, 0
    for row in rows:
        _require(isinstance(row, dict), 'review_transport_index_invalid')
        key = relative(row.get('path'))
        _require(key != 'transport-manifest.json' and key.casefold() not in indexed, 'review_transport_index_invalid')
        actual = hash_file(root, key)
        _require(actual == row, 'review_transport_file_changed')
        indexed[key.casefold()] = key
        size += actual['bytes']
        _require(size <= 512 * 1024**2, 'review_transport_limit')
    actual_paths = set()
    for path in root.rglob('*'):
        _require(not path.is_symlink() and not path.is_junction(), 'review_transport_linked')
        key = path.relative_to(root).as_posix()
        if not path.is_dir() and key != 'transport-manifest.json':
            actual_paths.add(key)
    _require(set(indexed.values()) == actual_paths and size == transport.get('bytes') == descriptor.get('bytes'),
             'review_transport_index_invalid')
    receipt = read_candidate_delivery(root / 'delivery', envelope)
    completed = complete_index(snapshot, read_json(snapshot / 'result', 'manifest.json'))[0]
    _require(validated.get('manifest') == completed
             and completed.get('status') == 'candidate', 'review_candidate_manifest_invalid')
    return root, envelope, receipt, completed


def save_review_candidate(repo, a, snapshot, request, validated, config):
    """Save only a verified review draft in the caller's open DB transaction.

    ``validated`` must be the actual trusted sandbox result, after sandbox stop
    proof and bounded transfer. This method rechecks that receipt and every file;
    it does not reconstruct process-local evidence or issue publication rights.
    Late cancellation/fence changes reject before public files or DB mutation.
    """
    _require(type(a) is Attempt and isinstance(config, RuntimeConfig), 'review_attempt_invalid')
    _require(a.phase in ('generate', 'revise') and all(request.get(key) == value for key, value in (
        ('job_id', a.job_id), ('attempt', a.attempt), ('phase', a.phase), ('session_id', a.session_id),
        ('input_snapshot_sha256', a.snapshot['hash']))), 'review_attempt_binding_invalid')
    job, project, _ = repo.fenced(a.job_id, a.attempt, a.token)
    _require(job.stopped is True and job.status == 'validating'
             and job.project_id == a.project_id and job.kind == a.phase
             and job.session_id == a.session_id and job.job_uid == a.uid
             and job.input_snapshot.get('hash') == a.snapshot['hash'], 'review_stop_proof_required')
    snapshot = Path(snapshot).absolute()
    _require(snapshot.is_relative_to(config.data_root.absolute()), 'review_snapshot_scope_invalid')
    root, envelope, receipt, manifest = _transport(snapshot, request, validated, config)
    # Preserve a private checkpoint for audit/recovery only. It is deliberately
    # ineligible as a generation/revision baseline, regardless of Actor claims.
    require_storage(config, receipt['artifact_bytes'] + config.max_snapshot_bytes * 2)
    checkpoint_key = f'projects/{a.project_id}/checkpoints/{uuid.uuid4()}'
    checkpoint = config.data_root / checkpoint_key
    checkpoint.mkdir(parents=True, exist_ok=False)
    indexes = {name: copy_tree(snapshot / name, checkpoint / name, skip_home_bootstrap=name == 'home')
               for name in ('home', 'project')}
    checkpoint_manifest = {
        'schema_version': 1, 'status': 'review', 'eligible_as_revision_base': False,
        'session_id': a.session_id, 'input_snapshot_sha256': a.snapshot['hash'],
        'project_root': relative(manifest['project_root']),
        'control_context': a.snapshot.get('control_context', {}), **indexes,
    }
    atomic_json(checkpoint / 'checkpoint.json', checkpoint_manifest)
    make_readonly(checkpoint)
    version_id = str(uuid.uuid4())
    artifact_key = f'projects/{a.project_id}/versions/{version_id}/review'
    target = config.data_root / artifact_key
    target.mkdir(parents=True, exist_ok=False)
    artifacts = receipt['artifacts']
    copy_file(root / 'delivery', artifacts[0]['path'], target / 'presentation.pptx', expected_hash=artifacts[0]['sha256'])
    slides = []
    for ordinal, artifact in enumerate(artifacts[1:], 1):
        copy_file(root / 'delivery', artifact['path'], target / f'{ordinal}.png', expected_hash=artifact['sha256'])
        original = manifest['slides'][ordinal - 1]
        slides.append({'preview_key': artifact_key + f'/{ordinal}.png',
                       'preview_sha256': artifact['sha256'], 'source_sha256': receipt['pages'][ordinal - 1]['source_sha256'],
                       'title': str(original.get('title', ''))[:200], 'summary': str(original.get('summary', ''))[:2000]})
    public_manifest = {
        'status': 'review', 'validation_status': 'gate_pending', 'eligible_as_revision_base': False,
        'publication_authorization': False, 'pptx_key': artifact_key + '/presentation.pptx',
        'pptx_sha256': artifacts[0]['sha256'], 'slides': slides,
        'required_pending_gates': receipt['required_pending_gates'],
        'review_evidence': {'storage_key': root.relative_to(config.data_root.absolute()).as_posix(),
                            'receipt_sha256': hash_file(root / 'delivery', 'candidate-delivery.json')['sha256'],
                            'controller_nonce': envelope['validation_nonce'],
                            'execution_image': receipt['execution_image'],
                            'source_manifest_sha256': envelope['source_manifest_sha256'],
                            'source_fingerprint_sha256': receipt['source_fingerprint_sha256']},
    }
    make_readonly(target)
    # Recheck the fenced DB state in the repository operation before release.
    return repo.save_review_version(a.job_id, a.attempt, a.token, version_id=version_id,
                                   manifest=public_manifest, artifact_root=artifact_key,
                                   checkpoint_key=checkpoint_key, checkpoint_manifest=checkpoint_manifest,
                                   review_validated=True)
