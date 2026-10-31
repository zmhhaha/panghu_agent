"""Explicit recovery decisions without pretending that an agent run succeeded.

Evidence snapshots and imported reports remain owner-scoped project objects.
Imported prose has no manufactured claim ledger or execution receipt, and uses
its own source-review confirmation before adoption or private finalization.
"""
from __future__ import annotations

import copy
import hashlib
from uuid import NAMESPACE_URL, uuid4, uuid5

from .pi_contracts import utc_now
from .pi_sections import index_sections


def hydrate_evidence(project):
    """Make pre-fix retained history visible; never bless it as current input."""
    jobs = {job['id']: job for job in project['jobs']}
    history = project.get('evidence_history', [])
    for evidence in history + ([project['evidence']] if project.get('evidence') else []):
        if 'input_revision' not in evidence:
            evidence['input_revision'] = jobs.get(evidence.get('task_id'), {}).get('input_revision')
        evidence['stale'] = bool(evidence.get('stale') or
            evidence['input_revision'] != project['input_revision'])
        evidence.setdefault('origin', 'website_generated')
    if project.get('evidence') is None and history:
        # Older releases cleared the visible pointer on filtering. Re-expose a
        # read-only historical object rather than requiring another model run.
        evidence = copy.deepcopy(history[-1])
        evidence['stale'] = True
        project['evidence'] = evidence
        stored = evidence.get('outline')
        project['outline'] = copy.deepcopy(stored) if stored else dict(
            id=str(uuid5(NAMESPACE_URL, 'scientific-outline:' + evidence['id'])),
            revision=evidence.get('input_revision'), content=copy.deepcopy(evidence['content']),
            output=copy.deepcopy(evidence['content']), snapshot_sha256=evidence['sha256'],
            created_at=evidence.get('created_at'), origin=evidence['origin'])
        project['outline']['stale'] = True
    if project.get('evidence') and project.get('outline'):
        project['outline']['stale'] = bool(project['outline'].get('stale') or project['evidence']['stale'])
        project['outline'].setdefault('input_revision', project['evidence'].get('input_revision'))


def retain_evidence(project):
    evidence, outline = project.get('evidence'), project.get('outline')
    if not evidence:
        return
    evidence.setdefault('input_revision', project['input_revision'])
    # Keep the original outline identity alongside the snapshot for exact
    # read-only historical display, including manually edited outlines.
    old = next((item for item in project['evidence_history'] if item['id'] == evidence['id']), None)
    if old is None:
        old = copy.deepcopy(evidence)
        project['evidence_history'].append(old)
    if outline and 'outline' not in old:
        old['outline'] = copy.deepcopy(outline)
    evidence['stale'] = True
    if outline:
        outline['stale'] = True


def save_manual_evidence(project, output, *, allow_limited=False):
    from .workspace import require, _snapshot, _invalidate, digest
    from .workflow_contracts import validate_stage_output, source_papers
    require(project['confirmations']['plan'], 'plan_confirmation_required', 409)
    _idle(project)
    require(type(allow_limited) is bool)
    snapshot = _snapshot(project)
    validate_stage_output('outline', output, {'evidence_snapshot': snapshot})
    require(not (output['gaps'] or not output['evidence']) or allow_limited,
            'limited_evidence_confirmation_required', 409)
    _invalidate(project)
    evidence = dict(id=snapshot['snapshot_id'], snapshot=snapshot, sha256=digest(snapshot),
        content=copy.deepcopy(output), papers=source_papers(snapshot), created_at=utc_now(),
        task_id=None, input_revision=project['input_revision'], stale=False, origin='manual_edit')
    outline = dict(id=str(uuid4()), revision=project['input_revision'],
        input_revision=project['input_revision'], content=copy.deepcopy(output),
        output=copy.deepcopy(output), snapshot_sha256=evidence['sha256'],
        created_at=utc_now(), task_id=None, origin='manual_edit', edited=True, stale=False)
    evidence['outline'] = copy.deepcopy(outline)
    project['evidence_history'].append(copy.deepcopy(evidence))
    project['evidence'], project['outline'] = evidence, outline
    project['stage'] = 'evidence'


def _idle(project):
    from .workspace import require, TERMINAL
    require(not any(job['id'] == project.get('active_job_id') and job['state'] not in TERMINAL
                    for job in project['jobs']), 'job_already_active', 409)


def import_report(project, payload):
    from .workspace import require, text, _snapshot, digest
    from .workflow_contracts import source_papers
    _idle(project)
    require(len(project['versions']) < 100, 'project_version_limit', 409)
    origin = payload.get('origin', 'user_import')
    require(type(origin) is str and origin in {'user_import', 'manual_edit'}, 'invalid_report_origin')
    require(payload.get('source_reviewed') is True, 'source_review_required', 409)
    require(payload.get('allow_limited_evidence') is True,
            'limited_evidence_confirmation_required', 409)
    report = payload.get('report_markdown')
    require(type(report) is str and bool(report.strip()) and len(report) <= 2 * 1024 * 1024,
            'invalid_report')
    try:
        report_bytes = report.encode('utf-8')
    except UnicodeError:
        require(False, 'invalid_report')
    require(len(report_bytes) <= 2 * 1024 * 1024, 'report_too_large', 413)
    # Validate controls while preserving the exact uploaded UTF-8 document.
    text(report, 2 * 1024 * 1024, blank=False)
    sections = index_sections(report)
    parent_id = payload.get('parent_version_id')
    parent = None
    if parent_id:
        parent = next((version for version in project['versions'] if version['id'] == parent_id), None)
        require(parent is not None, 'version_not_found', 404)
        require(origin == 'manual_edit', 'import_parent_requires_manual_edit')
        require(parent_id == project['current_version_id'], 'candidate_parent_conflict', 409)
    elif origin == 'manual_edit':
        require(project['current_version_id'] is None, 'manual_edit_parent_required', 409)
    snapshot = _snapshot(project)
    snapshot_sha = digest(snapshot)
    notes = text(payload.get('unresolved_notes', ''), 4000)
    version_id = str(uuid4())
    version = dict(id=version_id, task_id=None, parent_version_id=parent_id,
        root_version_id=parent.get('root_version_id', parent['id']) if parent else version_id,
        status='candidate', stale=False, input_revision=project['input_revision'],
        report_markdown=report, report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        section_index=sections, sources=source_papers(snapshot),
        evidence_snapshot_id=snapshot['snapshot_id'], confirmation_id=None,
        result_digest=None, created_at=utc_now(), origin=origin, model_trace_available=False,
        source_review=dict(source_reviewed=True, allow_limited_evidence=True,
            input_revision=project['input_revision'], snapshot_sha256=snapshot_sha,
            confirmed_at=utc_now(), unresolved_notes=notes,
            validation='user_reviewed_not_machine_claim_validated'),
        source_snapshot=snapshot, import_review=None,
        references=[], evidence=[], claims=[])
    project['versions'].append(version)
    # An imported file is a candidate, including the first report. It only
    # becomes the active report after a separate review and explicit adoption.
    project['stage'] = 'review'
    return version


def confirm_import(project, version, payload):
    from .workspace import require, text
    _idle(project)
    require(version.get('model_trace_available') is False, 'imported_version_required', 409)
    require(not version.get('stale') and version['input_revision'] == project['input_revision'],
            'stale_version_requires_reconfirmation', 409)
    require(payload.get('source_reviewed') is True, 'source_review_required', 409)
    require(payload.get('allow_limited_evidence') is True,
            'limited_evidence_confirmation_required', 409)
    require(version.get('import_review') is None, 'import_already_reviewed', 409)
    review = dict(id=str(uuid4()), kind='import_review', version_id=version['id'],
        report_sha256=version['report_sha256'], input_revision=project['input_revision'],
        source_snapshot_sha256=version['source_review']['snapshot_sha256'],
        source_reviewed=True, allow_limited_evidence=True,
        unresolved_notes=text(payload.get('unresolved_notes', ''), 4000), created_at=utc_now())
    version['import_review'] = review
    version['confirmation_id'] = review['id']
    project['confirmation_history'].append(copy.deepcopy(review))
    project['stage'] = 'review'


def evidence_detail(project, evidence_id):
    from .workspace import require, identifier
    identifier(evidence_id)
    evidence = next((item for item in [project.get('evidence')] + project['evidence_history']
                     if item and item['id'] == evidence_id), None)
    require(evidence is not None, 'evidence_not_found', 404)
    value = copy.deepcopy(evidence)
    snapshot = value.pop('snapshot', {})
    # Exports explain which selection/source version was used without exposing
    # extracted private file bodies or raw provider observations.
    value['source_lineage'] = dict(snapshot_id=snapshot.get('snapshot_id', evidence_id),
        selections=copy.deepcopy(snapshot.get('selections', [])),
        batch_ids=[batch['batch_id'] for batch in snapshot.get('search_batches', [])],
        materials=[{key: item.get(key) for key in
            ('material_id', 'name', 'sha256', 'access_level', 'status')}
            for item in snapshot.get('materials', [])])
    value['current_input_revision'] = project['input_revision']
    value['stale'] = bool(value.get('stale') or value.get('input_revision') != project['input_revision'])
    value['read_only'] = True
    return value
