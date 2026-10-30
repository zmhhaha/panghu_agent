"""Portable workspace checkpoint metadata, not native session restoration."""
from __future__ import annotations

from .artifacts import relative_path, unique_paths
from .contracts import ExecutionBindingPins, ServiceError, digest


CHECKPOINT_FORMAT = 'workspace-tree.v1'
PPT_STAGE_EDGES = frozenset({
    ('ppt.plan_initial.v1', 'ppt.plan_options.v1'),
    ('ppt.plan_options.v1', 'ppt.prepare.v1'),
    ('ppt.prepare.v1', 'ppt.generate.v1'),
    ('ppt.generate.v1', 'ppt.revise.v1'),
    ('ppt.revise.v1', 'ppt.revise.v1'),
})


def checkpoint_manifest(*, job_id, attempt_id, profile, profile_revision,
                        contract_sha256, artifacts, parent_checkpoint_id=None,
                        execution_binding=None):
    unique_paths(item['name'] for item in artifacts)
    files = [dict(artifact_id=item['artifact_id'], path=relative_path(item['name']),
                  sha256=item['sha256'], size=item['size'])
             for item in sorted(artifacts, key=lambda item: item['name'])]
    body = dict(schema_version='agent-checkpoint.v1', format=CHECKPOINT_FORMAT,
                source_job_id=job_id, source_attempt_id=attempt_id,
                parent_checkpoint_id=parent_checkpoint_id, profile=profile,
                profile_revision=profile_revision, contract_sha256=contract_sha256,
                execution_binding=execution_binding,
                files=files, native_resume=False, publication_authorized=False)
    return {**body, 'sha256': digest(body)}


def compatible_checkpoint(manifest, pins):
    binding = pins.get('execution_binding') or {}
    expected = manifest.get('sha256')
    if expected != digest({key: value for key, value in manifest.items() if key != 'sha256'}):
        raise ServiceError('checkpoint_integrity_invalid', 503)
    source = manifest.get('execution_binding') or {}
    try:
        # Revalidate the service-pinned content identities. Portable checkpoints
        # never import an opaque native session or its model credentials.
        source = ExecutionBindingPins.model_validate(source).model_dump(mode='json')
        target = ExecutionBindingPins.model_validate(binding).model_dump(mode='json')
    except (ValueError, TypeError):
        raise ServiceError('checkpoint_format_incompatible', 409) from None
    same_bundle = all(source[key] == target[key] for key in
                      ('bundle_name', 'bundle_revision', 'bundle_sha256'))
    same_contract = (manifest.get('profile') == pins.get('profile')
        and manifest.get('profile_revision') == pins.get('profile_revision')
        and manifest.get('contract_sha256') == target['contract_sha256'])
    stage_edge = ((manifest.get('profile'), pins.get('profile')) in PPT_STAGE_EDGES
        and manifest.get('profile_revision') == pins.get('profile_revision'))
    if (manifest.get('format') != CHECKPOINT_FORMAT or not same_bundle
            or source['profile'] != manifest.get('profile')
            or source['profile_revision'] != manifest.get('profile_revision')
            or source['contract_sha256'] != manifest.get('contract_sha256')
            or not (same_contract or stage_edge)):
        raise ServiceError('checkpoint_format_incompatible', 409)
    return True
