"""Explicit review-draft branches; human observations never authorize publication.

The public admission verifies the frozen version through the trusted resolver.
Only this module writes ``review_base`` into an immutable job input. The
controller checks it again before restoring its private working copy. An older
review checkpoint remains ineligible as a published revision baseline.
"""
from __future__ import annotations

import copy

from sqlalchemy import select

from .acceptance.contracts import SourceBinding
from .acceptance.store import AcceptanceStream, _scope
from .auth import Identity
from .models import AgentOutbox, Checkpoint, Job, Plan, Project, Version
from .repository import digest, fail

SCHEMA = 'ppt-service.review-revision.v1'
# Native Hermes state is only restored with this explicitly supported skill
# contract. Historical records may describe other profiles, but cannot execute.
SUPPORTED_PROFILE = 'ppt-master:c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7'
SUPPORTED_CHECKPOINT_FORMAT = 'hermes-home-project-v1'
AGENT_CHECKPOINT_FORMAT = 'ppt-agent-artifacts-v1'
AGENT_PROFILES = frozenset(('ppt.generate.v1', 'ppt.revise.v1'))
AUTHORITY_KEYS = {'schema', 'target_status', 'base_version_id', 'base_job_id',
                  'checkpoint_id', 'checkpoint_manifest_sha256', 'review_binding',
                  'review_revision', 'control_context', 'external_model_acknowledged'}


def has_review_base(snapshot):
    # Even malformed authority must never fall through to the published path.
    return isinstance(snapshot, dict) and 'review_base' in snapshot


def _supported_runtime(runtime):
    if runtime.checkpoint_format == AGENT_CHECKPOINT_FORMAT:
        return runtime.engine in ('pi', 'hermes') and runtime.profile in AGENT_PROFILES
    return (runtime.engine == 'hermes' and runtime.profile == SUPPORTED_PROFILE
            and runtime.checkpoint_format == SUPPORTED_CHECKPOINT_FORMAT)


def _agent_confirmation_context(session, job, checkpoint):
    """Check both stored website decisions against their original database rows.

    The web image intentionally has no controller/Agent SDK. This projection
    matches the connector's immutable request, including the original plan hash;
    worker checkpoint files cannot create or replace a website confirmation.
    """
    identifiers = checkpoint.manifest.get('confirmation_plan_ids')
    out = session.get(AgentOutbox, job.id)
    if (not isinstance(identifiers, dict) or set(identifiers) != {'stage1', 'stage2'}
            or not out or not isinstance(out.request, dict)
            or not isinstance(out.request.get('task'), dict)):
        fail('review_checkpoint_unavailable')
    expected = out.request.get('task', {}).get('confirmations')
    context = {}
    for stage, phase in (('stage1', 'plan_initial'), ('stage2', 'plan_options')):
        plan = session.get(Plan, identifiers[stage])
        if (not plan or plan.project_id != job.project_id or plan.phase != phase
                or not plan.confirmed_at or plan.input_snapshot_hash != job.input_snapshot.get('hash')
                or not isinstance(plan.outline, dict) or not isinstance(plan.design, dict)
                or not isinstance(plan.confirmation_selection, dict) or not plan.confirmation_selection):
            fail('review_checkpoint_unavailable')
        confirmation = {'channel': 'website_database', 'plan_id': plan.id,
            'plan_revision': plan.revision, 'input_snapshot_sha256': plan.input_snapshot_hash,
            'confirmed_at': plan.confirmed_at.isoformat(), 'selection': plan.confirmation_selection,
            'outline': plan.outline, 'design': {key: value for key, value in plan.design.items()
                if key not in {'source_plan', 'source_projection', 'recommendation'}},
            'plan_sha256': digest({'id': plan.id, 'phase': plan.phase, 'revision': plan.revision,
                'input_snapshot_sha256': plan.input_snapshot_hash, 'outline': plan.outline,
                'design': plan.design, 'selection': plan.confirmation_selection,
                'confirmed_at': plan.confirmed_at.isoformat()})}
        if stage == 'stage2':
            directions = plan.design.get('directions', plan.design.get('options', []))
            selected = [row for row in directions if isinstance(row, dict)
                        and row.get('id') == plan.confirmation_selection.get('direction_id')]
            if len(selected) != 1:
                fail('review_checkpoint_unavailable')
            chosen = selected[0]
            semantic = {key: value for key, value in chosen.items()
                        if key not in {'page_plan', 'production_parameters', 'source_pointer'}}
            outline = chosen.get('page_plan', plan.outline)
            confirmation.update(outline={'pages': outline} if isinstance(outline, list) else outline,
                design={'directions': [semantic], 'production_parameters': chosen.get(
                    'production_parameters', plan.design.get('production_parameters'))},
                selected_direction=semantic)
        if not isinstance(expected, dict) or expected.get(stage) != confirmation:
            fail('review_checkpoint_unavailable')
        context[stage + '_confirmation'] = confirmation
    if set(expected) != {'stage1', 'stage2'}:
        fail('review_checkpoint_unavailable')
    return copy.deepcopy(context)


def _confirmation_context(session, job, checkpoint):
    """Recover only a real website confirmation, including pre-integration drafts.

    Earlier review checkpoints omitted the inherited context. Their immutable
    source job still references the actual prepared checkpoint. It is never
    reconstructed from worker files or from a human review request.
    """
    if checkpoint.manifest.get('checkpoint_format') == AGENT_CHECKPOINT_FORMAT:
        return _agent_confirmation_context(session, job, checkpoint)
    context = checkpoint.manifest.get('control_context')
    if not isinstance(context, dict) or not context.get('stage2_confirmation'):
        previous = session.get(Checkpoint, job.checkpoint_id) if job.checkpoint_id else None
        if previous and (previous.project_id, previous.issuer, previous.subject) == (
                job.project_id, job.issuer, job.subject):
            context = previous.manifest.get('control_context')
        if not isinstance(context, dict) or not context.get('stage2_confirmation'):
            authority = job.input_snapshot.get('review_base', {})
            context = authority.get('control_context') if isinstance(authority, dict) else None
    if not isinstance(context, dict) or set(context) - {'stage1_confirmation', 'stage2_confirmation'}:
        fail('review_checkpoint_unavailable')
    confirmation = context.get('stage2_confirmation')
    if not isinstance(confirmation, dict) or confirmation.get('channel') != 'website':
        fail('review_checkpoint_unavailable')
    plan = session.scalar(select(Plan).where(Plan.project_id == job.project_id,
                                            Plan.revision == confirmation.get('plan_revision'),
                                            Plan.phase == 'plan_options'))
    if (not plan or not plan.confirmed_at or plan.input_snapshot_hash != job.input_snapshot.get('hash')
            or confirmation.get('input_snapshot_sha256') != plan.input_snapshot_hash
            or confirmation.get('selection') != plan.confirmation_selection
            or confirmation.get('outline') != plan.outline
            or confirmation.get('design') != plan.design):
        fail('review_checkpoint_unavailable')
    directions = plan.design.get('directions', plan.design.get('options', []))
    selected = next((item for item in directions if isinstance(item, dict)
                     and item.get('id') == plan.confirmation_selection.get('direction_id')), None)
    if selected is None or confirmation.get('selected_direction') != selected:
        fail('review_checkpoint_unavailable')
    return copy.deepcopy(context)


def _require_base(job, version, checkpoint):
    if (not version or version.status != 'review'
            or (version.project_id, version.issuer, version.subject) != (
                job.project_id, job.issuer, job.subject)
            or not checkpoint or version.checkpoint_id != checkpoint.id
            or (checkpoint.project_id, checkpoint.issuer, checkpoint.subject) != (
                job.project_id, job.issuer, job.subject)
            or checkpoint.manifest.get('status') != 'review'
            or checkpoint.manifest.get('eligible_as_revision_base') is not False
            or checkpoint.manifest.get('input_snapshot_sha256') != job.input_snapshot.get('hash')
            or (checkpoint.manifest.get('checkpoint_format') != AGENT_CHECKPOINT_FORMAT
                and checkpoint.manifest.get('engine', 'hermes') != 'hermes')
            or version.manifest.get('eligible_as_revision_base') is not False
            or version.manifest.get('publication_authorization') is not False):
        fail('review_checkpoint_unavailable')


def admit_review_revision(session, storage, owner, project, version, binding, review_revision):
    """Return the controller-only branch authority after locked real-file checks."""
    from .acceptance.resolver import ReviewContextResolver

    if version.status != 'review':
        fail('review_version_required')
    context = ReviewContextResolver(storage)(session, owner, project.id, version.id, for_update=True)
    if not context.reviewable or context.binding != binding:
        fail('review_source_changed')
    if not _supported_runtime(binding.runtime):
        fail('review_checkpoint_unavailable')
    stream = session.scalar(select(AcceptanceStream).where(
        AcceptanceStream.scope == _scope(owner, project.id, version.id)).with_for_update())
    if review_revision != (stream.revision if stream else 0):
        fail('review_records_changed')
    checkpoint = session.get(Checkpoint, version.checkpoint_id)
    source_job = session.get(Job, version.job_id)
    if (not source_job or source_job.status != 'succeeded' or source_job.stopped is not True
            or source_job.kind not in ('generate', 'revise')
            or (source_job.project_id, source_job.issuer, source_job.subject) != (
                project.id, owner.issuer, owner.sub)
            or source_job.input_snapshot.get('hash') != binding.input_snapshot_sha256):
        fail('review_checkpoint_unavailable')
    _require_base(source_job, version, checkpoint)
    inherited = _confirmation_context(session, source_job, checkpoint)
    return {'schema': SCHEMA, 'target_status': 'review', 'base_version_id': version.id,
            'base_job_id': source_job.id, 'checkpoint_id': checkpoint.id,
            'checkpoint_manifest_sha256': digest(checkpoint.manifest),
            'review_binding': binding.model_dump(mode='json'), 'review_revision': review_revision,
            'control_context': inherited, 'external_model_acknowledged': True}


def validate_review_base(session, job, version, checkpoint):
    """Check the immutable DB authority before build_attempt restores a draft.

    Physical bytes are checked separately by the resolver before launch and by
    the stager's complete no-follow checkpoint copy. This function never mints
    process-stop, human acceptance or publication evidence.
    """
    authority = job.input_snapshot.get('review_base')
    if (not isinstance(authority, dict) or set(authority) != AUTHORITY_KEYS
            or authority.get('schema') != SCHEMA or authority.get('target_status') != 'review'
            or authority.get('external_model_acknowledged') is not True
            or authority.get('base_version_id') != job.base_version_id
            or not version or authority.get('base_job_id') != version.job_id
            or authority.get('checkpoint_id') != job.checkpoint_id
            or not checkpoint or authority.get('checkpoint_manifest_sha256') != digest(checkpoint.manifest)
            or type(authority.get('review_revision')) is not int or authority['review_revision'] < 0):
        fail('review_checkpoint_unavailable')
    _require_base(job, version, checkpoint)
    try:
        binding = SourceBinding.model_validate(authority['review_binding'])
    except (ValueError, TypeError):
        fail('review_checkpoint_unavailable')
    evidence = version.manifest.get('review_evidence', {})
    if (binding.project_id != job.project_id or binding.version_id != version.id
            or binding.input_snapshot_sha256 != job.input_snapshot.get('hash')
            or not _supported_runtime(binding.runtime)
            or (binding.runtime.checkpoint_format == AGENT_CHECKPOINT_FORMAT) != (
                checkpoint.manifest.get('checkpoint_format') == AGENT_CHECKPOINT_FORMAT)
            or binding.manifest_sha256 != evidence.get('source_manifest_sha256')
            or binding.evidence_sha256 != evidence.get('receipt_sha256')
            or binding.evidence_id != evidence.get('controller_nonce')
            or binding.pptx_sha256 != version.manifest.get('pptx_sha256')
            or binding.source_fingerprint_sha256 != evidence.get('source_fingerprint_sha256')):
        fail('review_checkpoint_unavailable')
    source_job = session.get(Job, version.job_id)
    if (not source_job or source_job.status != 'succeeded' or source_job.stopped is not True
            or source_job.kind not in ('generate', 'revise')
            or (source_job.project_id, source_job.issuer, source_job.subject) != (
                job.project_id, job.issuer, job.subject)
            or source_job.input_snapshot.get('hash') != binding.input_snapshot_sha256
            or _confirmation_context(session, source_job, checkpoint) != authority['control_context']):
        fail('review_checkpoint_unavailable')
    if binding.runtime.checkpoint_format == AGENT_CHECKPOINT_FORMAT:
        out = session.get(AgentOutbox, source_job.id)
        if (not out or not isinstance(out.remote_pins, dict)
                or binding.runtime.engine != out.remote_pins.get('engine')
                or binding.runtime.profile != out.remote_pins.get('profile')
                or binding.runtime.runtime_revision != digest(out.remote_pins)):
            fail('review_checkpoint_unavailable')
    return copy.deepcopy(authority['control_context'])


def recheck_review_source(session, storage, job):
    """Re-resolve original bytes under the post-enqueue project revision.

    Enqueue advances the project once. Only that expected advance is normalized
    when comparing the exact admission binding; any other project/input change
    or modified source/evidence fails before a worker can be launched.
    """
    from .acceptance.resolver import ReviewContextResolver

    if not has_review_base(job.input_snapshot):
        return
    authority = job.input_snapshot['review_base']
    binding = SourceBinding.model_validate(authority['review_binding'])
    owner = Identity(job.issuer, job.subject)
    version = session.get(Version, job.base_version_id)
    if not version:
        fail('review_checkpoint_unavailable')
    # The HTTP resolver takes the terminal source job before its project.
    session.get(Job, version.job_id, with_for_update=True)
    project = session.get(Project, job.project_id, with_for_update=True)
    if (not project or project.deletion_requested or project.active_job_id != job.id
            or project.revision != binding.source_revision + 1):
        fail('review_source_changed')
    context = ReviewContextResolver(storage)(session, owner, project.id, job.base_version_id, for_update=True)
    normalized = context.binding.model_copy(update={'source_revision': binding.source_revision})
    if not context.reviewable or normalized != binding:
        fail('review_source_changed')
    checkpoint = session.get(Checkpoint, job.checkpoint_id)
    validate_review_base(session, job, version, checkpoint)
