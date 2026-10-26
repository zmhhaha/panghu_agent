"""Trusted job -> attempt construction shared by every execution backend.

This holds the confirmation-lineage and checkpoint-ownership rules in one place
so the Hermes dispatcher and the Pi dispatcher cannot drift apart. It performs no
I/O beyond the supplied session and never starts a writer.
"""
from __future__ import annotations

import copy
import uuid

from sqlalchemy import select

from ppt_service.server.models import Checkpoint, Plan, Version
from .execution import Attempt, ControlError, PHASES

CONFIRMATION_PHASES = ('plan_options', 'prepare')


def build_attempt(session, job, *, engine='hermes'):
    """Return (Attempt, checkpoint manifest or None) for a claimed job.

    Raises ControlError before any execution when lineage, ownership or the
    required confirmation does not match the stored records.
    """
    if engine not in ('hermes', 'pi'):
        raise ControlError('unsupported_engine')
    if job.kind not in PHASES:
        raise ControlError('unsupported_phase')
    checkpoint = None
    if job.kind == 'revise':
        version = session.get(Version, job.base_version_id) if job.base_version_id else None
        if not version or version.status != 'published' or (version.project_id, version.issuer, version.subject) != (
                job.project_id, job.issuer, job.subject):
            raise ControlError('base_version_mismatch')
        checkpoint = session.get(Checkpoint, version.checkpoint_id)
        if job.checkpoint_id != version.checkpoint_id:
            raise ControlError('base_checkpoint_mismatch')
    elif job.checkpoint_id:
        checkpoint = session.get(Checkpoint, job.checkpoint_id)
    if checkpoint and (checkpoint.project_id, checkpoint.issuer, checkpoint.subject) != (
            job.project_id, job.issuer, job.subject):
        raise ControlError('checkpoint_owner_mismatch')
    if job.kind != 'plan_initial' and not checkpoint:
        raise ControlError('explicit_checkpoint_required')
    if checkpoint:
        manifest_engine = checkpoint.manifest.get('engine', 'hermes')
        if manifest_engine != engine:
            raise ControlError('checkpoint_engine_mismatch')
        if engine == 'pi' and checkpoint.manifest.get('checkpoint_format') != 'artifact-replay-v1':
            raise ControlError('incompatible_checkpoint')
    session_id = (checkpoint.manifest.get('session_id') if checkpoint and engine == 'hermes'
                  else job.session_id or 'ppt-' + uuid.uuid4().hex)
    if not isinstance(session_id, str) or not session_id or len(session_id) > 100:
        raise ControlError('invalid_session')
    snapshot = copy.deepcopy(job.input_snapshot)
    context = copy.deepcopy(checkpoint.manifest.get('control_context', {})) if checkpoint else {}
    if job.kind in CONFIRMATION_PHASES:
        plan = session.scalar(select(Plan).where(Plan.checkpoint_id == checkpoint.id,
                                                 Plan.project_id == job.project_id))
        expected_phase = 'plan_initial' if job.kind == 'plan_options' else 'plan_options'
        if (not plan or plan.phase != expected_phase or not plan.confirmed_at
                or plan.confirmation_selection != snapshot.get('confirmation_selection')
                or plan.input_snapshot_hash != snapshot['hash']):
            raise ControlError('confirmation_lineage_mismatch')
        confirmation = {'channel': 'website', 'plan_revision': plan.revision,
                        'input_snapshot_sha256': plan.input_snapshot_hash,
                        'selection': plan.confirmation_selection, 'outline': plan.outline,
                        'design': plan.design}
        if job.kind == 'prepare':
            options = plan.design.get('directions', plan.design.get('options', []))
            selected = next((direction for direction in options
                             if direction.get('id') == plan.confirmation_selection.get('direction_id')), None)
            if not selected:
                raise ControlError('selected_direction_missing')
            confirmation['selected_direction'] = selected
        context['stage1_confirmation' if job.kind == 'plan_options' else 'stage2_confirmation'] = confirmation
    snapshot['control_context'] = context
    attempt = Attempt(job.id, job.project_id, job.attempt, job.fencing_token, job.kind, snapshot,
                      session_id, checkpoint.storage_key if checkpoint else None, job.base_version_id)
    return attempt, (checkpoint.manifest if checkpoint else None)
