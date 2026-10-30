"""Durable PPT -> Agent Service outbox, with business-owned confirmation gates.

The connector never chooses an engine or transfers native state. One existing
PPT execution slot fences both the old controller and this controller. Remote
completion is only permission to collect; isolated PPT validation and the
existing review/publication gates still decide business acceptance.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from sqlalchemy import select

from agent_service.artifacts import read_output, relative_path, unique_paths
from agent_service.client import AgentClient
from agent_service.contracts import JobRequest, ServiceError, TERMINAL_STATES, canonical_bytes, digest
from agent_service.profiles.ppt import (PPTProfile, checkpoint_descriptor, validate_checkpoint)
from ppt_service.server.models import AgentOutbox, Checkpoint, ExecutionSlot, Job, Plan, Project, Version
from ppt_service.server.phase_limits import phase_timeout_seconds
from ppt_service.server.repository import Repository
from .execution import ControlError
from .staging import EXT, atomic_json, copy_file, copy_tree, make_readonly, open_regular, read_json, require_storage


def require(value, code):
    if not value:
        raise ControlError(code)


def _execution_deadline(job):
    require(isinstance(job.deadline, datetime), 'invalid_execution_deadline')
    # PostgreSQL preserves the timezone; SQLite fixtures return UTC as naive.
    deadline = job.deadline
    return deadline.replace(tzinfo=timezone.utc) if deadline.tzinfo is None else deadline.astimezone(timezone.utc)


def _checked_progress_page(page, remote_id, after):
    """Validate the complete cursor page before committing any business events."""
    require(isinstance(page, dict) and isinstance(page.get('items'), list) and len(page['items']) <= 100
            and type(page.get('next_cursor')) is int and type(page.get('has_more')) is bool
            and page['next_cursor'] == after + len(page['items'])
            and page['next_cursor'] <= 2**63 - 1
            and (not page['has_more'] or bool(page['items'])), 'agent_event_cursor_invalid')
    checked = []
    for seq, item in enumerate(page['items'], after + 1):
        require(isinstance(item, dict) and type(item.get('seq')) is int and item['seq'] == seq
                and item.get('job_id', remote_id) == remote_id
                and isinstance(item.get('type'), str) and isinstance(item.get('payload'), dict),
                'agent_event_cursor_invalid')
        # Copy only known metadata. Worker prose, tools, counts, phase names and
        # apparent completion/validation claims never cross this boundary.
        event = item['payload'].get('event')
        activity = event if isinstance(event, str) and event in ('pi_event', 'engine_started', 'engine_progress') else None
        checked.append((seq, item['type'], activity))
    return checked


def _project_progress(repo, job, kind, activity):
    if job.stopped or job.status not in ('starting', 'running'):
        return
    stages = {'job.queued': 'execution_queued', 'job.starting': 'execution_starting',
              'job.running': 'execution_running'}
    stage = stages.get(kind)
    if kind == 'engine.progress' and activity is not None:
        stage = 'execution_running'
    if stage is None:
        return
    order = {'execution_queued': 1, 'execution_starting': 2, 'execution_running': 3}
    if order[stage] < order.get(job.stage, 0):
        return
    # A known engine activity is a real update, including when its broad stage
    # is unchanged. It says nothing about page counts or percentage complete.
    if job.stage != stage or kind == 'engine.progress':
        job.stage = stage
        repo.event(job, 'stage.changed', {'stage': stage})


def _trusted_progress(repo, job, stage):
    require(job.stopped and stage in ('collecting', 'validating'), 'stop_proof_required')
    if stage == 'collecting' and job.stage == 'validating':
        return
    if job.stage != stage or job.status != stage:
        repo.event(job, 'stage.changed', {'stage': stage})
        job.stage = job.status = stage


@dataclass(frozen=True)
class AgentConnectorSettings:
    enabled: bool = False
    profile_revision: str = 'v1'
    lease_seconds: int = 300

    def __post_init__(self):
        require(type(self.enabled) is bool and type(self.profile_revision) is str
                and re.fullmatch('[A-Za-z0-9_.-]{1,100}', self.profile_revision)
                and type(self.lease_seconds) is int and 60 <= self.lease_seconds <= 600,
                'invalid_agent_connector_settings')

    @classmethod
    def env(cls):
        return cls(enabled=os.getenv('PPT_AGENT_SERVICE_ENABLED') == 'true',
                   profile_revision=os.getenv('PPT_AGENT_PROFILE_REVISION', 'v1'))


def _confirmation(plan):
    require(plan is not None and plan.confirmed_at is not None
            and isinstance(plan.confirmation_selection, dict) and bool(plan.confirmation_selection),
            'confirmation_lineage_mismatch')
    design = {key: copy.deepcopy(value) for key, value in plan.design.items()
              if key not in {'source_plan', 'source_projection', 'recommendation'}}
    result = {'channel': 'website_database', 'plan_id': plan.id, 'plan_revision': plan.revision,
              'input_snapshot_sha256': plan.input_snapshot_hash,
              'confirmed_at': plan.confirmed_at.isoformat(),
              'selection': copy.deepcopy(plan.confirmation_selection),
              'outline': copy.deepcopy(plan.outline), 'design': design,
              'plan_sha256': digest({'id': plan.id, 'phase': plan.phase, 'revision': plan.revision,
                  'input_snapshot_sha256': plan.input_snapshot_hash, 'outline': plan.outline,
                  'design': plan.design, 'selection': plan.confirmation_selection,
                  'confirmed_at': plan.confirmed_at.isoformat()})}
    if plan.phase == 'plan_options':
        directions = plan.design.get('directions', plan.design.get('options', []))
        selected = [row for row in directions if row.get('id') == plan.confirmation_selection.get('direction_id')]
        require(len(selected) == 1, 'selected_direction_missing')
        chosen = selected[0]
        semantic = {key: copy.deepcopy(value) for key, value in chosen.items()
                    if key not in {'page_plan', 'production_parameters', 'source_pointer'}}
        result['outline'] = copy.deepcopy(chosen.get('page_plan', plan.outline))
        if isinstance(result['outline'], list): result['outline'] = {'pages': result['outline']}
        result['design'] = {'directions': [semantic], 'production_parameters': copy.deepcopy(
                            chosen.get('production_parameters', plan.design.get('production_parameters')))}
        result['selected_direction'] = copy.deepcopy(semantic)
    return result


def build_agent_task(session, job):
    """Resolve only exact DB records; no latest-version or caller confirmation."""
    profile = PPTProfile(job.kind)
    snapshot = copy.deepcopy(job.input_snapshot)
    body = {k: snapshot[k] for k in ('project_id', 'title', 'settings', 'assets')}
    require(digest(body) == snapshot['hash'], 'input_snapshot_mismatch')
    from ppt_service.server.review_revisions import has_review_base, validate_review_base
    checkpoint, base, review_authority = None, None, None
    if job.kind == 'revise':
        base = session.get(Version, job.base_version_id) if job.base_version_id else None
        require(base is not None
                and (base.project_id, base.issuer, base.subject) == (job.project_id, job.issuer, job.subject)
                and base.checkpoint_id == job.checkpoint_id, 'base_version_mismatch')
        if has_review_base(snapshot):
            try:
                validate_review_base(session, job, base, session.get(Checkpoint, job.checkpoint_id))
            except (HTTPException, ValueError, TypeError):
                raise ControlError('review_checkpoint_unavailable') from None
            authority = snapshot['review_base']
            review_authority = {'schema': 'ppt-review-authority.v1', 'target_status': 'review',
                'checkpoint_manifest_sha256': authority['checkpoint_manifest_sha256'],
                'review_binding_sha256': digest(authority['review_binding']),
                'review_revision': authority['review_revision']}
        else:
            require(base.status == 'published', 'base_version_mismatch')
    elif has_review_base(snapshot):
        raise ControlError('review_phase_invalid')
    if job.checkpoint_id:
        checkpoint = session.get(Checkpoint, job.checkpoint_id)
        require(checkpoint is not None and (checkpoint.project_id, checkpoint.issuer, checkpoint.subject)
                == (job.project_id, job.issuer, job.subject), 'checkpoint_owner_mismatch')
    require(job.kind == 'plan_initial' or checkpoint is not None, 'explicit_checkpoint_required')
    confirmations = {}
    cp, ref = None, None
    if checkpoint is not None:
        cp = checkpoint.manifest.get('agent_checkpoint')
        validate_checkpoint(cp, project_id=job.project_id, snapshot_hash=snapshot['hash'], phase=job.kind)
        ref = checkpoint.manifest.get('service_checkpoint_ref')
        require(isinstance(ref, dict), 'agent_checkpoint_reference_required')
        for stage, plan_id in checkpoint.manifest.get('confirmation_plan_ids', {}).items():
            plan = session.get(Plan, plan_id)
            expected_phase = {'stage1': 'plan_initial', 'stage2': 'plan_options'}.get(stage)
            require(plan is not None and plan.project_id == job.project_id and plan.phase == expected_phase
                    and plan.input_snapshot_hash == snapshot['hash'], 'confirmation_lineage_mismatch')
            confirmations[stage] = _confirmation(plan)
        if job.kind in ('plan_options', 'prepare'):
            plan = session.scalar(select(Plan).where(Plan.project_id == job.project_id,
                                                    Plan.checkpoint_id == checkpoint.id))
            expected = 'plan_initial' if job.kind == 'plan_options' else 'plan_options'
            require(plan is not None and plan.phase == expected and plan.input_snapshot_hash == snapshot['hash']
                    and plan.confirmation_selection == snapshot.get('confirmation_selection'),
                    'confirmation_lineage_mismatch')
            confirmations['stage1' if job.kind == 'plan_options' else 'stage2'] = _confirmation(plan)
    base_binding = None if base is None else {'version_id': base.id, 'project_id': base.project_id,
         'status': base.status, 'eligible_as_revision_base': review_authority is None,
         'manifest_sha256': digest(base.manifest), 'checkpoint_sha256': cp['sha256']}
    if review_authority is not None:
        base_binding['review_authority'] = review_authority
    task = {'schema_version': profile.task_schema, 'phase': job.kind, 'project_id': job.project_id,
            'business_job_id': job.id, 'input_snapshot_sha256': snapshot['hash'], 'input_snapshot': body,
            'feedback': snapshot.get('feedback', ''), 'revision_pages': snapshot.get('pages', []),
            'confirmations': confirmations, 'checkpoint': None if cp is None else checkpoint_descriptor(cp),
            'base_version': base_binding}
    profile.validate_task(task)
    return task, copy.deepcopy(ref)


class AgentConnector:
    def __init__(self, sessions, config, client, validator, *, settings=None, oracle_provider=None):
        self.sessions, self.config, self.client, self.validator = sessions, config, client, validator
        self.settings = settings or AgentConnectorSettings()
        self.oracle_provider = oracle_provider

    def _fenced(self, s, job_id, *, allow_cancel=True):
        out = s.get(AgentOutbox, job_id)
        require(out is not None, 'agent_outbox_missing')
        # Match review HTTP locking: slot -> terminal source -> job -> project.
        s.get(ExecutionSlot, 1, with_for_update=True)
        current = s.get(Job, job_id)
        if current is not None and 'review_base' in current.input_snapshot:
            base = s.get(Version, current.base_version_id)
            require(base is not None, 'base_version_mismatch')
            s.get(Job, base.job_id, with_for_update=True)
        job, project, slot = Repository(s).fenced(job_id, out.attempt, out.fencing_token, allow_cancel=allow_cancel)
        out = s.get(AgentOutbox, job_id, with_for_update=True)
        require(out.business_ref == out.request['business_ref'], 'agent_outbox_binding_invalid')
        return out, job, project, slot

    def _recheck_review_source(self, session, job):
        if 'review_base' not in job.input_snapshot:
            return
        from ppt_service.server.review_revisions import recheck_review_source
        from ppt_service.server.storage import Storage
        try:
            recheck_review_source(session, Storage(self.config.data_root), job)
        except (HTTPException, ValueError, TypeError, OSError):
            raise ControlError('review_source_changed') from None

    def _reject_stopped_candidate(self, jid, code='quality_gate_failed'):
        """Only before validator launch or after its independent stop proof."""
        with self.sessions.begin() as s:
            out, job, _, _ = self._fenced(s, jid)
            require(job.stopped and job.status in ('collecting', 'validating'), 'stop_proof_required')
            Repository(s).finish_failed(jid, out.attempt, out.fencing_token, error_code=code)
            out.state = 'mapped'; out.result = {'status': 'failed', 'error_code': code}
        return {'status': 'failed', 'job_id': jid}

    def _claim(self):
        with self.sessions.begin() as s:
            slot = s.get(ExecutionSlot, 1, with_for_update=True)
            if slot.job_id:
                require(s.get(AgentOutbox, slot.job_id) is not None, 'legacy_execution_active')
                return slot.job_id
            if not self.settings.enabled:
                return None
            job = Repository(s).claim_next(self.settings.lease_seconds)
            if job is None:
                return None
            try:
                self._recheck_review_source(s, job)
                task, cp_ref = build_agent_task(s, job)
                business_ref = f'ppt:{job.id}:{job.attempt}:{job.fencing_token}'
                request = JobRequest(profile='ppt.' + job.kind + '.v1', profile_revision=self.settings.profile_revision,
                          business_ref=business_ref, task=task, checkpoint_ref=cp_ref,
                          timeout_seconds=phase_timeout_seconds(job.kind),
                          execution_deadline_at=_execution_deadline(job).timestamp()).model_dump(mode='json')
                uploads = []
                for asset in task['input_snapshot']['assets']:
                    require(asset['media_type'] in EXT, 'unsupported_media_type')
                    uploads.append({'name': 'inputs/' + asset['id'] + EXT[asset['media_type']],
                                    'storage_key': asset['storage_key'], 'sha256': asset['sha256'],
                                    'bytes': asset['bytes'], 'ref': None})
            except (ControlError, ServiceError, ValueError, TypeError, KeyError, AttributeError):
                # Claim construction performed no HTTP request or writer launch.
                project = s.get(Project, job.project_id, with_for_update=True)
                job.status = 'failed'; job.error_code = 'execution_interrupted'
                project.active_job_id = None; project.stage = 'ready' if project.latest_version_id else 'draft'
                slot.job_id = None
                Repository(s).event(job, 'job.failed', {'error_code': job.error_code})
                return None
            s.add(AgentOutbox(job_id=job.id, attempt=job.attempt, fencing_token=job.fencing_token,
                business_ref=business_ref, idempotency_key=business_ref, request=request, inputs=uploads))
            s.flush()
            return job.id

    def _snapshot(self, jid, remote):
        listing = self.client.artifact_manifest(remote['job_id'])
        require(listing.get('job_id') == remote['job_id'] and listing.get('attempt_id') == remote['attempt']['attempt_id']
                and listing.get('publication_authorized') is False
                and digest({k: v for k, v in listing.items() if k != 'sha256'}) == listing.get('sha256'),
                'agent_artifact_manifest_invalid')
        rows = listing.get('files')
        require(isinstance(rows, list) and len(rows) <= 2048, 'agent_artifact_limit')
        unique_paths(row.get('name') for row in rows)
        require(all(row['name'].startswith('project/') or row['name'] in
                    ('result/manifest.json', 'result/checkpoint.json') for row in rows), 'agent_artifact_scope_invalid')
        with self.sessions.begin() as s:
            out, job, _, _ = self._fenced(s, jid)
            task = copy.deepcopy(out.request['task'])
            target = self.config.data_root / f'projects/{job.project_id}/agent-results/{jid}/{out.fencing_token}'
        # Only complete files enter the candidate tree. A killed controller may
        # leave this private scratch file; it is outside the measured project
        # roster and is discarded before resuming the same remote download.
        pending = target.with_name('.' + target.name + '-download')
        if pending.exists() or pending.is_symlink():
            with open_regular(pending.parent, pending.name, 64 * 1024**2):
                pass
            pending.unlink()
        require_storage(self.config, listing.get('total_bytes', 256 * 1024**2))
        target.mkdir(parents=True, exist_ok=True)
        total = 0
        for row in rows:
            require(type(row.get('size')) is int and 0 <= row['size'] <= 64 * 1024**2, 'agent_artifact_limit')
            total += row['size']; require(total <= 256 * 1024**2, 'agent_artifact_limit')
            path = target / relative_path(row['name'])
            if path.exists():
                raw = read_output(target, row['name'], limit=64 * 1024**2)
            else:
                raw = self.client.download_artifact(row['artifact_id'])
                require(len(raw) == row['size'] and hashlib.sha256(raw).hexdigest() == row['sha256'], 'agent_artifact_integrity_invalid')
                path.parent.mkdir(parents=True, exist_ok=True)
                # The private controller tree is inaccessible to the remote worker.
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
                with os.fdopen(os.open(pending, flags, 0o600), 'wb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                require(not path.exists() and not path.is_symlink(), 'agent_snapshot_changed')
                os.replace(pending, path)
                if os.name == 'posix':
                    for directory in (path.parent, pending.parent):
                        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
            require(len(raw) == row['size'] and hashlib.sha256(raw).hexdigest() == row['sha256'], 'agent_artifact_integrity_invalid')
        require(total == listing['total_bytes'], 'agent_artifact_integrity_invalid')
        cp = read_json(target / 'result', 'checkpoint.json')
        validate_checkpoint(cp, project_id=task['project_id'], snapshot_hash=task['input_snapshot_sha256'])
        require(cp['source_job_id'] == remote['job_id'] and cp['source_attempt_id'] == remote['attempt']['attempt_id']
                and cp['source_business_job_id'] == jid and cp['phase'] == task['phase'], 'agent_checkpoint_binding_invalid')
        indexed = {row['name']: row for row in rows}
        require({name for name in indexed if name.startswith('project/')} == {'project/' + row['path'] for row in cp['files']},
                'agent_checkpoint_roster_mismatch')
        for row in cp['files']:
            item = indexed['project/' + row['path']]
            require(item['sha256'] == row['sha256'] and item['size'] == row['bytes'], 'agent_checkpoint_roster_mismatch')
        transport = {'remote': remote, 'artifact_manifest_sha256': listing['sha256']}
        if (target / 'remote-job.json').exists():
            previous = read_json(target, 'remote-job.json')
            require(previous.get('artifact_manifest_sha256') == listing['sha256'], 'agent_snapshot_changed')
        else:
            atomic_json(target / 'remote-job.json', transport)
        make_readonly(target)
        return target, cp, rows

    def _persist(self, jid, snapshot, cp, validated, ref):
        from .agent_validation import AgentValidationReceipt
        source = hashlib.sha256(read_output(snapshot, 'result/manifest.json', limit=4 * 1024**2)).hexdigest()
        require(type(validated) is AgentValidationReceipt and validated.verify(source), 'agent_validation_receipt_required')
        evidence_path = validated.evidence_path
        validated = validated.report()
        require(validated.get('input_snapshot_sha256') == cp['input_snapshot_sha256']
                and validated.get('source_manifest_sha256') ==
                    hashlib.sha256(read_output(snapshot, 'result/manifest.json', limit=4 * 1024**2)).hexdigest(),
                'agent_validation_binding_invalid')
        with self.sessions.begin() as s:
            out, job, project, slot = self._fenced(s, jid)
            require(job.stopped and job.status == 'validating', 'stop_proof_required')
            task = out.request['task']
            require(validated.get('phase') == job.kind and validated.get('publication_authorized') is False,
                    'agent_validation_binding_invalid')
            if validated.get('status') == 'needs_input':
                Repository(s).finish_failed(jid, out.attempt, out.fencing_token, error_code='execution_interrupted')
                out.state = 'mapped'; out.result = {'status': 'needs_input', 'questions': validated.get('questions', [])}
                return
            key = f'projects/{project.id}/checkpoints/{uuid4()}'
            target = self.config.data_root / key
            target.mkdir(parents=True, exist_ok=False)
            copy_tree(snapshot / 'project', target / 'project', max_total=256 * 1024**2, max_files=2048)
            copy_file(snapshot / 'result', 'checkpoint.json', target / 'result' / 'checkpoint.json')
            checkpoint = {'schema_version': 1, 'checkpoint_format': 'ppt-agent-artifacts-v1',
                'agent_checkpoint': cp, 'service_checkpoint_ref': ref,
                'confirmation_plan_ids': {stage: value['plan_id'] for stage, value in task['confirmations'].items()},
                'project_root': cp['project_root'], 'input_snapshot_sha256': cp['input_snapshot_sha256'],
                'agent_source': {'storage_key': Path(snapshot).absolute().relative_to(self.config.data_root.absolute()).as_posix(),
                    'remote_job_sha256': hashlib.sha256(read_output(snapshot, 'remote-job.json', limit=4 * 1024**2)).hexdigest(),
                    'artifact_manifest_sha256': read_json(snapshot, 'remote-job.json')['artifact_manifest_sha256']}}
            repo = Repository(s)
            if job.kind in ('plan_initial', 'plan_options'):
                require(validated.get('accepted') is True, 'agent_candidate_unaccepted')
                atomic_json(target / 'checkpoint.json', checkpoint); make_readonly(target)
                plan = repo.accept_plan(jid, out.attempt, out.fencing_token,
                        outline=validated['outline'], design=validated['design'], checkpoint_key=key,
                        checkpoint_manifest=checkpoint, independently_validated=True)
                out.result = {'status': 'awaiting_confirmation', 'plan_revision': plan.revision}
            elif job.kind == 'prepare':
                require(validated.get('accepted') is True, 'agent_candidate_unaccepted')
                atomic_json(target / 'checkpoint.json', checkpoint); make_readonly(target)
                following = repo.accept_prepared(jid, out.attempt, out.fencing_token,
                        checkpoint_key=key, checkpoint_manifest=checkpoint, independently_validated=True)
                out.result = {'status': 'prepared', 'generate_job_id': following.id}
            else:
                # Even a completed generic job cannot bypass pending visual and
                # delivery acceptance. Persist an owner-private review version.
                require(validated.get('accepted') is False and validated.get('programmatic_checks_passed') is True
                        and validated.get('status') == 'candidate', 'agent_review_unvalidated')
                require({'independent_visual_review', 'independent_postflight_and_delivery'} <=
                        set(validated.get('required_pending_gates', [])), 'agent_publication_gate_missing')
                version_id = str(uuid4()); artifact_key = f'projects/{project.id}/versions/{version_id}/review'
                artifact = self.config.data_root / artifact_key; artifact.mkdir(parents=True, exist_ok=False)
                manifest = validated['manifest']; root = snapshot / 'project' / relative_path(manifest['project_root'])
                pptxs = [row for row in manifest['artifacts'] if row['role'] == 'pptx']
                require(len(pptxs) == 1, 'single_pptx_required')
                copy_file(root, pptxs[0]['path'], artifact / 'presentation.pptx', expected_hash=pptxs[0]['sha256'])
                slides = []
                rendered = validated.get('independent_programmatic_evidence')
                require(isinstance(rendered, dict) and isinstance(rendered.get('pngs'), list)
                        and len(rendered['pngs']) == len(manifest['slides']), 'agent_independent_previews_required')
                for n, slide in enumerate(manifest['slides'], 1):
                    row = rendered['pngs'][n - 1]
                    require(row['ordinal'] == n and row['svg_path'] == slide['svg_path'], 'agent_independent_preview_binding_invalid')
                    evidence_key = 'independent/' + rendered['validation_id'] + '/' + relative_path(row['relative_path'])
                    copy_file(evidence_path, evidence_key, artifact / f'{n}.png', expected_hash=row['sha256'])
                    slides.append({'preview_key': artifact_key + f'/{n}.png',
                                   'preview_sha256': row['sha256'], 'source_sha256': row['svg_sha256'],
                                   'title': str(slide.get('title', ''))[:200],
                                   'summary': str(slide.get('summary', ''))[:2000]})
                evidence_path = Path(evidence_path).absolute()
                require(evidence_path.is_relative_to(self.config.data_root.absolute()), 'review_transport_scope_invalid')
                receipt = read_output(evidence_path, 'validated.json', limit=4 * 1024**2)
                require(read_json(evidence_path, 'validated.json') == validated, 'agent_validator_evidence_changed')
                transport = read_json(evidence_path, 'transport-manifest.json')
                require(transport.get('writers_stopped') is True and transport.get('transport_verified') is True
                        and transport.get('sandbox_exit_code') == 0
                        and isinstance(transport.get('execution_image'), str)
                        and transport.get('validation_request_sha256') is not None,
                        'review_transport_unverified')
                checkpoint.update(status='review', eligible_as_revision_base=False)
                atomic_json(target / 'checkpoint.json', checkpoint); make_readonly(target); make_readonly(artifact)
                public = {'status': 'review', 'validation_status': 'gate_pending',
                          'eligible_as_revision_base': False, 'publication_authorization': False,
                          'pptx_key': artifact_key + '/presentation.pptx', 'pptx_sha256': pptxs[0]['sha256'],
                          'slides': slides, 'required_pending_gates': validated['required_pending_gates'],
                          'review_evidence': {'kind': 'agent-validation.v1',
                              'storage_key': evidence_path.relative_to(self.config.data_root.absolute()).as_posix(),
                              'receipt_sha256': hashlib.sha256(receipt).hexdigest(),
                              'controller_nonce': validated['validation_nonce'],
                              'execution_image': transport['execution_image'],
                              'source_manifest_sha256': source,
                              'source_fingerprint_sha256': rendered['source']['copied_source_fingerprint']}}
                repo.save_review_version(jid, out.attempt, out.fencing_token, version_id=version_id,
                    manifest=public, artifact_root=artifact_key, checkpoint_key=key,
                    checkpoint_manifest=checkpoint, review_validated=True)
                out.result = {'status': 'review', 'version_id': version_id}
            out.result = dict(out.result, artifact_manifest_sha256=read_json(snapshot, 'remote-job.json')['artifact_manifest_sha256'])
            out.state = 'mapped'; out.checkpoint_ref = ref

    def tick(self):
        try:
            return self._tick()
        except ServiceError as exc:
            raise ControlError('agent_service_unavailable' if exc.status >= 500
                               else 'agent_service_request_rejected') from None

    def _tick(self):
        # Acknowledgement is retried independently after the business transaction.
        with self.sessions.begin() as s:
            done = s.scalar(select(AgentOutbox).where(AgentOutbox.state == 'mapped', AgentOutbox.acked == False)
                            .order_by(AgentOutbox.job_id).limit(1))
            ack = None if done is None else (done.job_id, done.remote_job_id, copy.deepcopy(done.result))
        if ack is not None:
            if ack[2] and ack[2].get('artifact_manifest_sha256'):
                self.client.acknowledge_publication(ack[1], idempotency_key='ppt-ack:' + ack[0],
                    manifest_sha256=ack[2]['artifact_manifest_sha256'], delivery_ref='ppt:' + ack[0],
                    decision='candidate_received')
            with self.sessions.begin() as s:
                s.get(AgentOutbox, ack[0], with_for_update=True).acked = True
            return {'status': 'acknowledged', 'job_id': ack[0]}
        jid = self._claim()
        if jid is None:
            return {'status': 'idle' if self.settings.enabled else 'disabled'}
        with self.sessions.begin() as s:
            out, job, project, slot = self._fenced(s, jid)
            deadline = _execution_deadline(job)
            cancelled = (job.status == 'cancelling' or project.deletion_requested or not self.settings.enabled
                         or not project.external_model_ack or deadline <= datetime.now(timezone.utc))
            if cancelled and not out.submission_started:
                # No request was issued. Do not invent process-stop evidence.
                job.status = 'cancelled'; project.active_job_id = None; slot.job_id = None
                project.stage = 'ready' if project.latest_version_id else 'draft'; project.cleanup_ready = project.deletion_requested
                out.state = 'mapped'; out.acked = True; out.result = {'status': 'cancelled_before_submission'}
                Repository(s).event(job, 'job.cancelled')
                return {'status': 'cancelled', 'job_id': jid}
            if cancelled and job.status != 'cancelling':
                job.status = 'cancelling'
                Repository(s).event(job, 'job.cancelling')
            pending = next((copy.deepcopy(row) for row in out.inputs if row['ref'] is None), None)
            remote_id = out.remote_job_id
            submission_started = out.submission_started
            request, key = copy.deepcopy(out.request), out.idempotency_key
        if pending is not None and not cancelled:
            limit = PPTProfile.limits.max_input_bytes
            with open_regular(self.config.data_root, pending['storage_key'], limit) as stream:
                content = stream.read(limit + 1)
            require(len(content) == pending['bytes'] and hashlib.sha256(content).hexdigest() == pending['sha256'], 'source_hash_mismatch')
            uploaded = self.client.upload_input(pending['name'], content)
            require(uploaded.get('name') == pending['name'] and uploaded.get('sha256') == pending['sha256'], 'agent_input_binding_invalid')
            with self.sessions.begin() as s:
                out, _, _, _ = self._fenced(s, jid)
                out.inputs = [dict(row, ref={'input_id': uploaded['input_id'], 'sha256': row['sha256'], 'path': row['name']})
                              if row['name'] == pending['name'] else row for row in out.inputs]
            return {'status': 'uploading', 'job_id': jid}
        if remote_id is None:
            remote = None
            if submission_started:
                try:
                    remote = self.client.lookup_job(key)
                except ServiceError as exc:
                    if exc.status != 404:
                        raise
                    if cancelled:
                        # A 404 cannot exclude an in-flight POST. Keep the slot
                        # until the original cutoff makes late execution impossible.
                        with self.sessions.begin() as s:
                            out, job, project, slot = self._fenced(s, jid)
                            if _execution_deadline(job) > datetime.now(timezone.utc):
                                Repository(s).heartbeat(jid, out.attempt, out.fencing_token, self.settings.lease_seconds)
                                return {'status': 'cancelling', 'job_id': jid}
                            job.status = 'cancelled'; project.active_job_id = None; slot.job_id = None
                            project.stage = 'ready' if project.latest_version_id else 'draft'
                            project.cleanup_ready = project.deletion_requested
                            out.state = 'mapped'; out.acked = True
                            out.result = {'status': 'submission_not_found_after_deadline'}
                            Repository(s).event(job, 'job.cancelled')
                        return {'status': 'cancelled', 'job_id': jid}
            with self.sessions.begin() as s:
                out, job, project, _ = self._fenced(s, jid)
                if remote is None and (job.status == 'cancelling' or project.deletion_requested
                        or not self.settings.enabled or not project.external_model_ack
                        or _execution_deadline(job) <= datetime.now(timezone.utc)):
                    # Recheck after the HTTP lookup: cancellation may have
                    # arrived while the previous request was in flight.
                    return {'status': 'cancelling', 'job_id': jid}
                if remote is None:
                    self._recheck_review_source(s, job)
                # An identical persisted request resolves any uncertain POST.
                if remote is None and not out.submission_started:
                    require(all(row['ref'] is not None for row in out.inputs), 'agent_input_upload_incomplete')
                    out.request = dict(out.request, inputs=[row['ref'] for row in out.inputs])
                    out.submission_started = True
                request, key = copy.deepcopy(out.request), out.idempotency_key
            if remote is None:
                remote = self.client.submit(request, idempotency_key=key)
            require(remote.get('business_ref') == request['business_ref'], 'agent_job_binding_invalid')
            remote_id = str(UUID(remote['job_id']))
            with self.sessions.begin() as s:
                out, job, _, _ = self._fenced(s, jid)
                require(out.remote_job_id in (None, remote_id), 'agent_job_binding_invalid')
                out.remote_job_id = remote_id; out.remote_pins = remote.get('pins'); out.state = 'submitted'
                job.job_uid = str(uuid5(NAMESPACE_URL, out.business_ref)); job.run_id = remote_id
                if job.status != 'cancelling': job.status = 'running'
            return {'status': 'submitted', 'job_id': jid}
        if cancelled:
            self.client.cancel(remote_id)
        remote = self.client.get_job(remote_id)
        require(remote.get('job_id') == remote_id and remote.get('business_ref') == request['business_ref'], 'agent_job_binding_invalid')
        with self.sessions.begin() as s:
            out, _, _, _ = self._fenced(s, jid)
            after = out.event_cursor
        page = self.client.events(remote_id, after=after, limit=100)
        checked = _checked_progress_page(page, remote_id, after)
        with self.sessions.begin() as s:
            out, job, _, _ = self._fenced(s, jid)
            require(out.remote_pins == remote.get('pins'), 'agent_execution_binding_changed')
            repo = Repository(s)
            for seq, kind, activity in checked:
                if seq > out.event_cursor:
                    _project_progress(repo, job, kind, activity)
            # Cursor and sanitized business events commit together. Restarting
            # or overlapping reads cannot duplicate an already projected event.
            out.event_cursor = max(out.event_cursor, page['next_cursor'])
            if remote.get('state') not in TERMINAL_STATES or page['has_more']:
                repo.heartbeat(jid, out.attempt, out.fencing_token, self.settings.lease_seconds)
        if remote.get('state') not in TERMINAL_STATES or page['has_more']:
            # Drain real event pages before accepting a terminal remote result;
            # a page boundary never launches or resubmits another paid attempt.
            return {'status': 'running', 'job_id': jid}
        proof, attempt = remote.get('stop_proof'), remote.get('attempt')
        if (remote.get('not_started') is True and attempt is None
                and remote.get('state') in ('cancelled', 'timed_out')):
            with self.sessions.begin() as s:
                out, job, project, slot = self._fenced(s, jid)
                # The authenticated service has terminalized an unadmitted job.
                # Keep stopped false: there was no process to observe.
                job.status = 'cancelled' if cancelled else 'failed'
                job.error_code = None if cancelled else 'execution_timeout'
                project.active_job_id = None; project.stage = 'ready' if project.latest_version_id else 'draft'
                project.cleanup_ready = project.deletion_requested; slot.job_id = None
                out.state = 'mapped'; out.result = {'status': job.status}
                Repository(s).event(job, 'job.cancelled' if cancelled else 'job.failed',
                                    {} if cancelled else {'error_code': job.error_code})
            return {'status': 'not_started_terminal', 'job_id': jid}
        require(remote.get('state') != 'quarantined' and isinstance(proof, dict)
                and proof.get('stop_proven') is True and isinstance(attempt, dict)
                and proof.get('attempt_id') == attempt.get('attempt_id')
                and proof.get('fence') == attempt.get('fence') and bool(proof.get('resource_ids')),
                'agent_remote_stop_unverified')
        with self.sessions.begin() as s:
            out, job, _, _ = self._fenced(s, jid)
            require(out.remote_pins == remote.get('pins'), 'agent_execution_binding_changed')
            stopped = Repository(s).prove_stopped(jid, out.attempt, out.fencing_token,
                        job_uid=job.job_uid, all_writers_stopped=True)
            if stopped.status == 'cancelled':
                out.state = 'mapped'; out.result = {'status': 'cancelled'}
                return {'status': 'cancelled', 'job_id': jid}
            if remote['state'] not in ('completed', 'needs_input'):
                code = 'execution_timeout' if remote['state'] == 'timed_out' else 'execution_interrupted'
                Repository(s).finish_failed(jid, out.attempt, out.fencing_token, error_code=code)
                out.state = 'mapped'; out.result = {'status': 'failed', 'error_code': code}
                return {'status': 'failed', 'job_id': jid}
            _trusted_progress(Repository(s), stopped, 'collecting')
            request = copy.deepcopy(out.request)
        try:
            snapshot, cp, rows = self._snapshot(jid, remote)
        except ControlError:
            # Remote model writers are proven stopped and no validator has
            # started. A malformed stopped candidate is a permanent failure.
            return self._reject_stopped_candidate(jid)
        with self.sessions.begin() as s:
            _, job, _, _ = self._fenced(s, jid)
            _trusted_progress(Repository(s), job, 'validating')
        check_request = {'execution_contract': 'ppt-agent.v1', 'task': request['task'], 'phase': request['task']['phase'],
                         'job_id': jid, 'input_snapshot_sha256': cp['input_snapshot_sha256'],
                         'pages': request['task']['input_snapshot']['settings'].get('page_count', 8)}
        if cp['phase'] in ('generate', 'revise'):
            require(self.oracle_provider is not None, 'trusted_chart_oracle_required')
            oracle = self.oracle_provider(request['task'])
            require(isinstance(oracle, dict) and isinstance(oracle.get('expected_charts'), list), 'trusted_chart_oracle_required')
            authorized = {row['sha256'] for row in request['task']['input_snapshot']['assets']}
            require(set(oracle.get('source_sha256', [])) <= authorized
                    and (not oracle['expected_charts'] or bool(oracle.get('source_sha256'))), 'oracle_source_mismatch')
            check_request['expected_charts'] = oracle['expected_charts']
        from .agent_validation import AgentValidationFailure
        try:
            validated = self.validator.validate(snapshot, check_request)
        except AgentValidationFailure as exc:
            require(exc.validator_stopped is True, 'agent_validation_writers_not_stopped')
            return self._reject_stopped_candidate(jid)
        with self.sessions.begin() as s:
            out, _, _, _ = self._fenced(s, jid)
            ref = copy.deepcopy(out.checkpoint_ref)
        if ref is None:
            created = self.client.create_checkpoint(remote_id, idempotency_key=key + ':checkpoint')
            ref = {field: created[field] for field in ('checkpoint_id', 'sha256', 'format')}
            with self.sessions.begin() as s:
                out, _, _, _ = self._fenced(s, jid); out.checkpoint_ref = ref
        self._persist(jid, snapshot, cp, validated, ref)
        return {'status': 'mapped', 'job_id': jid}
