"""Durable methods-first lab coordinator; only the private executor owns it.

GET is read-only. One advance performs one bounded transition. The shared Agent
creates source and methods; no animation recipe is implemented in this service.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from agent_service.animation_knowledge_contract import MAX_EXPERIENCE_BYTES
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_method as protocol
from agent_service.profiles import video_animation_skill_method as skill_protocol
from agent_service.profiles.video_animation_create import source_sha256
from .animation_creation_service import _run_lock
from .animation_method_store import AnimationMethodStore, _structural_changes
from .animation_method_commissions import (
    AnimationMethodCommissionStore, OP_TERMINAL, RETRY_RELATION_FIELDS, TERMINAL,
)
from .animation_runtime_capabilities import validate_runtime_capabilities
from .animation_authoring_tools import observed_authoring_tools
from .animation_motion_evidence import motion_orchestration
from .animation_quality_policy import select_review
from .animation_creation_selection import (
    freeze_constraints as freeze_creation_selection, manim_request, motion_tools,
    normalize_selection, validate_new_selection, validate_rendered_selection, validate_source_selection,
)
from .animation_method_research import METHOD_SCHEMA, REMOVAL_SCHEMA, validate_content_changes
from .animation_method_renderer import PUBLIC_RENDER_FAILURE_CODES, public_render_failure_code
from .animation_method_trash import AnimationMethodTrash, serialized_library_mutation
from .effect_classified_review import EffectClassifiedReviewMixin


_RENDER_FAILURE_MESSAGE = '画面运行检查未通过；保留失败证据，不自动重试'
_EXECUTION_FAILURE_MESSAGE = '执行失败；保留已有结果与原任务，不自动重试'
_PUBLIC_FAILURE_MESSAGES = frozenset({
    _RENDER_FAILURE_MESSAGE, _EXECUTION_FAILURE_MESSAGE,
    '制作结果未通过入库验证；保留原操作，不自动重跑',
    '评审证据未通过关联验证；保留原操作，不自动重跑',
    'Manim资源创作已停止；保留原操作和证据，不自动重试',
})


def _public_failure(run):
    """Read old/new summaries without exposing arbitrary stored diagnostics."""
    for operation in reversed(run['operations']):
        if (operation['stage'] == 'render' and operation['status'] == 'completed'
                and (operation.get('result') or {}).get('technical', {}).get('status') != 'passed'):
            return _RENDER_FAILURE_MESSAGE, public_render_failure_code(operation['result'].get('error'))
    summary = run['summary']
    message = summary.get('message') if type(summary) is dict else summary
    code = summary.get('code') if type(summary) is dict else None
    return (message if type(message) is str and message in _PUBLIC_FAILURE_MESSAGES else _EXECUTION_FAILURE_MESSAGE,
            code if type(code) is str and code in PUBLIC_RENDER_FAILURE_CODES else 'animation_method_execution_failed')


def _require(value, code='animation_method_request_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _review_experience(document, opinion):
    """Retain short diagnostics, or explicitly project the immutable result.

    Long result text remains in the operation store. A later Agent receives
    only this projection, not an implied full diagnostic resource or fetch tool.
    """
    details = {key: deepcopy(opinion[key]) for key in
               ('criteria_results', 'remaining_issues', 'next_guidance')}
    complete = dict(document, **details)
    if len(canonical_bytes(complete)) <= MAX_EXPERIENCE_BYTES:
        return complete
    field_details = {key: dict(sha256=digest(value), json_bytes=len(canonical_bytes(value)),
        **({'item_count': len(value)} if type(value) is list else {'character_count': len(value)}))
        for key, value in details.items()}
    projection = dict(schema='video.animation-review-diagnostics-projection.v1',
        completeness='structured_summary_only_not_full_diagnostics',
        reason='complete_diagnostics_exceed_experience_byte_budget',
        experience_byte_budget=MAX_EXPERIENCE_BYTES,
        full_result_reference=dict(commission_id=document['commission_id'],
            operation_id=document['operation_id'], result_sha256=document['result_sha256'],
            json_pointer='/review', availability='operation_store_only_not_in_agent_inputs'),
        fields_not_inlined=list(details), fields=field_details,
        criteria_results=[{key: row[key] for key in ('criterion_id', 'status')}
                          for row in opinion['criteria_results']],
        experience_fields_not_inlined=[])
    projected = dict(document, diagnostics_projection=projection)
    # Even a previously admissible base document might have no spare bytes for
    # projection metadata. Omit whole descriptive fields with explicit bindings;
    # never turn a text prefix into an apparently complete original field.
    for key in sorted(('goal', 'summary', 'observation'),
                      key=lambda name: len(canonical_bytes(document[name])), reverse=True):
        if len(canonical_bytes(projected)) <= MAX_EXPERIENCE_BYTES:
            return projected
        value = projected.pop(key)
        projection['experience_fields_not_inlined'].append(dict(field=key,
            sha256=digest(value), json_bytes=len(canonical_bytes(value))))
    _require(len(canonical_bytes(projected)) <= MAX_EXPERIENCE_BYTES,
             'animation_knowledge_too_large', 413)
    return projected


def _public_url(commission_id, candidate_id, kind):
    return '/api/method-lab/commissions/' + commission_id + '/candidates/' + candidate_id + '/' + kind


def _method_view(record):
    if record['schema'] != METHOD_SCHEMA:
        return deepcopy(record)
    view = {key: deepcopy(value) for key, value in record.items() if key != 'skill_package'}
    view['skill_package_sha256'] = record['skill_package']['sha256']
    view['skill_package_url'] = '/api/method-lab/methods/' + record['id']
    if record.get('publication') is not None:
        view['publication_sha256'] = digest(record['publication'])
    return view


def _validation_view(record, *, demonstration=None, method=None):
    if record['schema'] != 'video.animation-method-validation.v2':
        return deepcopy(record)
    view = {key: deepcopy(value) for key, value in record.items() if key != 'reference_removal'}
    # Listening is an exact validation/media decision. An audible reference
    # must not impose listening on a separate silent transfer, while an audio
    # criterion still requires a real audio track before it can be accepted.
    if demonstration is not None and method is not None:
        media = demonstration['media']
        view.update(media_sha256=media['sha256'], audio_sha256=media.get('audio_sha256'),
            requires_audio_verification=media.get('audio_sha256') is not None or any(
                row.get('requires_audio') for row in method['criteria']))
    if record.get('reference_removal'):
        receipt = record['reference_removal']
        view['reference_removal_summary'] = dict(schema=receipt['schema'],
            input_sha256=receipt['input_receipt']['input_sha256'],
            resource_manifest_sha256=receipt['input_receipt']['resource_manifest_sha256'],
            scope='exact_host_records_retained_in_private_validation_store')
    return view


def _candidates(run):
    rows = list(run['candidates'])
    baseline = run['frozen'].get('base_candidate') or run.get('retry_base_candidate')
    if baseline and not any(row['id'] == baseline['id'] for row in rows):
        rows.insert(0, baseline)
    return rows


def _representative_candidate(run):
    if run['state'] != 'completed':
        return None
    return next((item for item in _candidates(run)
                 if item['id'] == run['best_candidate_id']
                 and item.get('technical', {}).get('status') == 'passed'), None)


def _finite_number(value):
    # Do not coerce unbounded JSON integers to float before range validation.
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _sample_index(demonstrations):
    result = {}
    for demonstration in demonstrations:
        candidate_id = demonstration.get('provenance', {}).get('candidate_id')
        if type(candidate_id) is str:
            result.setdefault(candidate_id, []).append(demonstration)
    return result


def _sample_engine(candidate, demonstrations):
    """Describe stored media evidence, never a requested or family-wide engine."""
    unknown = dict(engine=None, engine_status='unknown')
    if not candidate or type(candidate.get('source')) is not dict:
        return unknown
    engine = candidate['source'].get('engine')
    if engine not in ('html-svg', 'canvas', 'three'):
        return unknown
    unverified = dict(engine=None, engine_status='unverified')
    linked = demonstrations.get(candidate['id'], [])
    if not linked:
        return unverified
    media_sha = candidate.get('media', {}).get('sha256')
    operation_id = candidate.get('operation_id')
    confirmed = False
    authoring_tools = {}
    motion = {}
    for demonstration in linked:
        origin = demonstration.get('provenance', {})
        runtime = demonstration.get('runtime', {})
        admission = runtime.get('admission')
        source = demonstration.get('source') or {}
        sample_sha = demonstration.get('media', {}).get('sha256')
        recorded = (source.get('engine'), runtime.get('engine'))
        if (any(value is not None and value != engine for value in recorded)
                or (media_sha and sample_sha and media_sha != sample_sha)
                or (operation_id and origin.get('render_operation_id')
                    and operation_id != origin['render_operation_id'])):
            return dict(engine=None, engine_status='conflict')
        exact_sample = (recorded == (engine, engine)
            and type(media_sha) is str and len(media_sha) == 64
            and set(media_sha) <= set('0123456789abcdef') and sample_sha == media_sha
            and type(operation_id) is str and bool(operation_id)
            and origin.get('render_operation_id') == operation_id
            and candidate.get('technical', {}).get('status') == 'passed'
            and demonstration.get('technical', {}).get('status') == 'passed'
            and type(admission) is dict and admission.get('status') == 'admitted')
        confirmed |= exact_sample
        if exact_sample:
            for tool in observed_authoring_tools(runtime):
                authoring_tools[tool['id']] = tool
            for tool in motion_orchestration(candidate, demonstration):
                motion[tool['id']] = tool
    if not confirmed:
        return unverified
    result = dict(engine=engine, engine_status='rendered')
    if authoring_tools:
        result['authoring_tools'] = [authoring_tools[key] for key in sorted(authoring_tools)]
    if motion:
        result['motion_orchestration'] = [motion[key] for key in sorted(motion)]
    return result


def _historical_membership_issue(previous, canonical):
    if previous['id'] == canonical['id']:
        return 'historical_effect_membership_self_reference'
    for run in (previous, canonical):
        frozen = run['frozen']
        if (frozen.get('role', 'reference') != 'reference' or any(frozen.get(key) for key in (
                'base_commission_id', 'base_candidate_id', 'retry_of_commission_id',
                'baseline_demonstration_id', 'structural_changes'))):
            return 'historical_effect_membership_frozen_relation_conflict'
    if previous['state'] not in {'failed', 'cancelled'} or _candidates(previous):
        return 'historical_effect_membership_previous_ineligible'
    best = next((row for row in _candidates(canonical)
                 if row['id'] == canonical['best_candidate_id']), None)
    if (canonical['state'] not in TERMINAL or best is None
            or best.get('technical', {}).get('status') != 'passed'):
        return 'historical_effect_membership_canonical_ineligible'
    return None


def _deleted_commission_ids(runs, deletions):
    """Only exact owner-confirmed, unchanged empty failures can be hidden."""
    by_id = {run['id']: run for run in runs}
    hidden = set()
    for record in deletions:
        if type(record) is not dict or type(record.get('commission_id')) is not str:
            continue
        run = by_id.get(record['commission_id'])
        if (run is not None and record.get('schema') == 'video.animation-commission-deletion.v1'
                and record.get('owner') == run.get('owner')
                and record.get('commission_frozen_sha256') == run.get('frozen_sha256')
                and record.get('source') == 'explicit_owner_failed_attempt_deletion'
                and type(record.get('reason')) is str and bool(record['reason'].strip())
                and _finite_number(record.get('created_at')) and record['created_at'] >= 0
                and run['state'] == 'failed' and not _candidates(run)):
            hidden.add(run['id'])
    return hidden


def _resolve_effect_roots(runs, parents, direct_issues):
    roots, issues = {}, {}
    for run in runs:
        current, path, visited = run['id'], [], set()
        while current not in roots:
            if current in visited:
                root, issue = None, 'frozen_relation_cycle'
                break
            visited.add(current)
            path.append(current)
            if direct_issues[current]:
                root, issue = None, direct_issues[current]
                break
            parent = parents[current]
            if parent is None:
                root, issue = current, None
                break
            current = parent
        else:
            root, issue = roots[current], issues[current]
        for identity in path:
            roots[identity], issues[identity] = root, issue
    return roots, issues


def _effect_views(runs, demonstrations, memberships=(), deletions=(), separations=(), adoptions=None):
    """Group explicit frozen lineage and separately audited historical membership.

    A missing/foreign ancestor or a cycle makes every affected run independent.
    Membership and explicit separation are presentation identity, never a
    change to creation baselines or stored provenance.
    This read projection never changes recorded inputs, choices or history.
    """
    by_id = {run['id']: run for run in runs}
    hidden = _deleted_commission_ids(runs, deletions)
    demos = {row['id']: row for row in demonstrations}
    samples = _sample_index(demonstrations)
    parents, kinds, direct_issues = {}, {}, {}
    for run in runs:
        identity, frozen = run['id'], run['frozen']
        base, baseline = frozen.get('base_commission_id'), frozen.get('baseline_demonstration_id')
        retry = frozen.get('retry_of_commission_id')
        transfer = frozen.get('role', 'reference') == 'transfer'
        parent, issue = None, None
        fresh = frozen.get('creation_mode') == 'fresh'
        kinds[identity] = 'retry' if retry else 'fresh' if fresh else 'continuation' if base else 'transfer' if transfer else 'original'
        if fresh and not retry:
            parent = frozen.get('effect_id')
            if base or baseline or transfer or not parent:
                issue = 'fresh_effect_relationship_invalid'
        elif base and (transfer or baseline):
            issue = 'conflicting_frozen_relations'
        elif base:
            parent = base
        elif transfer:
            demonstration = demos.get(baseline) if type(baseline) is str else None
            if demonstration is None:
                issue = 'baseline_demonstration_missing_or_not_owned'
            else:
                parent = demonstration.get('provenance', {}).get('commission_id')
                if type(parent) is not str or not parent:
                    issue = 'baseline_commission_missing_or_not_owned'
        elif baseline:
            issue = 'transfer_role_missing'
        if retry is not None:
            previous = by_id.get(retry) if type(retry) is str else None
            if previous is None:
                issue = 'retry_commission_missing_or_not_owned'
            elif previous['state'] not in {'failed', 'cancelled'} or any(
                    frozen.get(key) != previous['frozen'].get(key) for key in RETRY_RELATION_FIELDS):
                issue = 'retry_relationship_invalid'
            # A retry repeats an execution, not the business baseline. Its
            # identity edge must not create a second continuation branch.
            parent = retry
        if parent is not None and (type(parent) is not str or parent not in by_id):
            issue = 'ancestor_missing_or_not_owned'
        parents[identity], direct_issues[identity] = parent, issue

    for membership in memberships:
        identity = membership.get('commission_id')
        if identity not in by_id or identity in hidden or membership.get('canonical_commission_id') in hidden:
            continue
        canonical_id = membership.get('canonical_commission_id')
        canonical = by_id.get(canonical_id)
        issue = ('historical_effect_membership_target_missing_or_not_owned' if canonical is None
                 else _historical_membership_issue(by_id[identity], canonical))
        if (not issue and any(membership.get(key) != value for key, value in (
                ('commission_frozen_sha256', by_id[identity]['frozen_sha256']),
                ('canonical_frozen_sha256', canonical['frozen_sha256'])))):
            issue = 'historical_effect_membership_snapshot_changed'
        if parents[identity] is not None or direct_issues[identity]:
            issue = 'historical_effect_membership_frozen_relation_conflict'
        if issue:
            direct_issues[identity] = issue
            continue
        parents[identity], kinds[identity] = canonical_id, 'historical_attempt'

    separation_titles, separation_origins, separation_issues = {}, {}, {}
    # A new subject or primary drawing technique is a distinct library effect,
    # even when its creation legitimately borrowed a previous experiment.
    # Validate the original lineage first; a display annotation cannot repair
    # or conceal invalid immutable ancestry. Apply annotations in ledger order
    # so a later separation can start inside an already separated effect.
    for record in separations:
        if (type(record) is not dict or type(record.get('commission_id')) is not str
                or record['commission_id'] not in by_id):
            continue
        identity = record['commission_id']
        run, frozen = by_id[identity], by_id[identity]['frozen']
        current_roots, current_issues = _resolve_effect_roots(runs, parents, direct_issues)
        valid = (record.get('schema') == 'video.animation-effect-separation.v1'
            and record.get('owner') == run.get('owner')
            and record.get('commission_frozen_sha256') == run.get('frozen_sha256')
            and identity not in hidden and identity not in separation_titles
            and not current_issues[identity]
            and frozen.get('role', 'reference') == 'reference'
            and bool(frozen.get('base_commission_id')) and not frozen.get('retry_of_commission_id')
            and record.get('source_parent_id') == parents[identity] == frozen['base_commission_id']
            and record.get('source_effect_id') == current_roots[identity] != identity
            and _representative_candidate(run) is not None
            and any(item['id'] == run['best_candidate_id'] for item in run['candidates'])
            and all(type(record.get(key)) is str and bool(record[key].strip())
                    and len(record[key]) <= limit for key, limit in (
                        ('title', 200), ('reason', 2000), ('request_id', 200)))
            and _finite_number(record.get('created_at')) and record['created_at'] >= 0)
        if not valid:
            separation_issues[identity] = 'effect_separation_record_invalid'
            continue
        parents[identity], kinds[identity] = None, 'original'
        separation_titles[identity] = record['title']
        separation_origins[identity] = dict(commission_id=identity,
            source_effect_id=record['source_effect_id'], source_parent_id=record['source_parent_id'],
            reason=record['reason'])

    roots, relation_issues = _resolve_effect_roots(runs, parents, direct_issues)
    if hidden:
        # Keep the real ancestors above for validation. Only presentation skips
        # deleted empty failures; every surviving run keeps its frozen lineage.
        runs = [run for run in runs if run['id'] not in hidden]
        for run in runs:
            identity = run['id']
            if relation_issues[identity]:
                direct_issues[identity] = relation_issues[identity]
                continue
            parent = parents[identity]
            while parent in hidden:
                parent = parents[parent]
            parents[identity] = parent
        roots, relation_issues = _resolve_effect_roots(runs, parents, direct_issues)

    groups = {}
    for run in runs:
        groups.setdefault(roots[run['id']] or run['id'], []).append(run)
    effects, membership = [], {}
    for effect_id, members in groups.items():
        original = by_id[effect_id]
        representative = original
        children = {}
        for run in members:
            if (kinds[run['id']] in {'continuation', 'retry'}
                    and run['frozen'].get('role', 'reference') != 'transfer'
                    and not relation_issues[run['id']]):
                children.setdefault(parents[run['id']], []).append(run)
        # Only completed, technically valid recorded choices compete for the
        # cover. Failed attempts can inherit a baseline without making a new
        # choice; keep them as bridges when a descendant did complete.
        representative_branches = set()
        for run in members:
            if _representative_candidate(run) is None:
                continue
            current = run
            while current['id'] not in representative_branches:
                representative_branches.add(current['id'])
                if (kinds[current['id']] not in {'continuation', 'retry'}
                        or current['frozen'].get('role', 'reference') == 'transfer'
                        or relation_issues[current['id']] or parents[current['id']] is None):
                    break
                current = by_id[parents[current['id']]]
        children = {parent: [run for run in descendants
                            if run['id'] in representative_branches]
                    for parent, descendants in children.items()}
        current = original
        # A linear continuation/retry can carry its recorded best forward, including
        # a deliberately retained baseline. Transfers are separate validation,
        # and competing branches supply no global comparison between siblings.
        while children.get(current['id']):
            next_runs = children[current['id']]
            if len(next_runs) != 1:
                break
            current = next_runs[0]
            if _representative_candidate(current) is not None:
                representative = current
        chosen = (adoptions or {}).get(effect_id)
        chosen_id = representative['best_candidate_id']
        if chosen and chosen['commission_id'] in {row['id'] for row in members}:
            selected_run = by_id[chosen['commission_id']]
            if any(row['id'] == chosen['candidate_id'] for row in _candidates(selected_run)):
                representative, chosen_id = selected_run, chosen['candidate_id']
        candidate = next((item for item in _candidates(representative)
                          if item['id'] == chosen_id), None)
        engine = _sample_engine(candidate, samples)
        best = (dict(id=candidate['id'], media_url=_public_url(
            representative['id'], candidate['id'], 'media'), **engine) if candidate else None)
        summaries = [dict(id=run['id'], title=run['title'],
            role=run['frozen'].get('role', 'reference'), kind=kinds[run['id']], state=run['state'],
            best_candidate_id=run['best_candidate_id'],
            base_commission_id=run['frozen'].get('base_commission_id'),
            retry_of_commission_id=run['frozen'].get('retry_of_commission_id'),
            stop_reason=run['stop_reason'], created_at=run['created_at'],
            **_sample_engine(next((item for item in _candidates(run)
                if item['id'] == run['best_candidate_id']), None), samples)) for run in reversed(members)]
        issues = sorted({issue for run in members for issue in (
            relation_issues[run['id']], separation_issues.get(run['id'])) if issue})
        active = next((run for run in members if run['state'] not in TERMINAL), None)
        effect = dict(id=effect_id, effect_id=effect_id,
            title=separation_titles.get(effect_id, original['title']),
            goal=original['frozen']['goal'], state=active['state'] if active else representative['state'],
            request_id=representative['request_id'], open_commission_id=representative['id'],
            media_url=best['media_url'] if best else None, best_candidate=best,
            **engine, engines=[engine['engine']] if engine['engine'] else [],
            commissions=summaries, relationship_status='needs_review' if issues else 'verified',
            relationship_issues=issues)
        effect.update(representative_revision=chosen['revision'] if chosen else 0,
                      adopted_candidate_id=candidate['id'] if candidate else None)
        if effect_id in separation_origins:
            effect['separation'] = separation_origins[effect_id]
        effects.append(effect)
        for run in members:
            membership[run['id']] = effect
    return effects, membership


class AnimationMethodService(EffectClassifiedReviewMixin):
    def __init__(self, root, *, author_gateway, review_gateway, renderer, clock=time.time,
                 runtime_capabilities=None, knowledge_store=None, manim_coordinator=None,
                 collaboration_gateway=None, library_adapter=None, classified_review_gateway=None):
        self.root = Path(root).absolute()
        _require(not any(path.is_symlink() for path in (self.root, *self.root.parents)))
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'locks').mkdir(exist_ok=True)
        self.methods = AnimationMethodStore(self.root / 'methods.sqlite3')
        self.commissions = AnimationMethodCommissionStore(self.root / 'commissions.sqlite3', clock=clock)
        self.author, self.reviewer, self.renderer = author_gateway, review_gateway, renderer
        self.classified_reviewer = classified_review_gateway
        self.collaboration_author = collaboration_gateway
        self.manim = manim_coordinator
        from .lab_resource_stage import LabResourceStage
        self.resource_stage = LabResourceStage(self.root / 'resource-plans.sqlite3',
            bindings_path=self.commissions.path, coordinator=lambda: self.manim, clock=clock)
        self.author_protocol = getattr(type(author_gateway), 'protocol', protocol)
        self.knowledge = knowledge_store
        if self.knowledge is None and self.author_protocol is skill_protocol:
            from .animation_knowledge import AnimationKnowledgeStore
            self.knowledge = AnimationKnowledgeStore(self.root / 'knowledge.sqlite3')
        self._runtime_capabilities = (None if runtime_capabilities is None
                                      else validate_runtime_capabilities(runtime_capabilities))
        self.clock = clock
        self.metadata_path = self.root / 'feedback.sqlite3'
        with closing(sqlite3.connect(self.metadata_path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS feedback (owner TEXT, request_id TEXT, '
                       'commission_id TEXT, sha256 TEXT, document TEXT, PRIMARY KEY(owner,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS feedback_withdrawals (owner TEXT, '
                       'commission_id TEXT, feedback_id TEXT, document TEXT, '
                       'PRIMARY KEY(owner,commission_id,feedback_id))')
            db.execute('CREATE TABLE IF NOT EXISTS feedback_withdrawal_requests (owner TEXT, '
                       'request_id TEXT, commission_id TEXT, feedback_id TEXT, sha256 TEXT, '
                       'PRIMARY KEY(owner,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS effect_memberships (owner TEXT NOT NULL, '
                       'commission_id TEXT NOT NULL, canonical_commission_id TEXT NOT NULL, '
                       'document TEXT NOT NULL, PRIMARY KEY(owner,commission_id))')
            db.execute('CREATE TABLE IF NOT EXISTS commission_deletions (owner TEXT NOT NULL, '
                       'commission_id TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(owner,commission_id))')
            db.commit()
        from .animation_effect_adoption import AnimationEffectAdoptionStore
        from .animation_laboratory_library import make_laboratory_library_adapter
        self.adoptions = AnimationEffectAdoptionStore(self.metadata_path, clock=clock)
        self.library_adapter = library_adapter or make_laboratory_library_adapter(self.root)
        self.trash = AnimationMethodTrash(self)
        from .animation_effect_identity import AnimationEffectIdentity
        self.effect_identity = AnimationEffectIdentity(self)

    def pending(self, limit=100):
        return self.commissions.pending(limit)

    def capture_wikiskill_records(self, owner):
        """Freeze real, currently visible effect evidence without running work.

        The attached read transaction holds source rows steady. Each operation
        input/result is a separate record, so large histories are neither
        silently truncated nor expanded into one unbounded model prompt.
        Working Wiki snapshots embedded in old inputs remain historical input
        context; they are not an independently published knowledge source.
        """
        records = []

        def add(record_id, kind, content):
            content = deepcopy(content)
            records.append(dict(record_id=record_id, kind=kind, content=content, sha256=digest(content)))

        with self.trash.mutation(owner, wait_seconds=5):
            with self.trash.database() as db:
                # Establish read locks before collecting linked evidence.
                for table in ('feedback', 'commissions.animation_method_commissions', 'methods.animation_methods'):
                    db.execute('SELECT count(*) FROM ' + table + ' WHERE owner=?', (owner,)).fetchone()
                state = self.trash.state(db, owner)
                hidden = state['hidden'] | state['legacy_hidden']
                runs = [run for run in state['runs'] if run['id'] not in hidden]
                visible_ids = {run['id'] for run in runs}
                _require(bool(runs), 'animation_wikiskill_library_empty', 409)
                methods, research = set(), set()
                for run in sorted(runs, key=lambda row: row['id']):
                    add('commission:' + run['id'], 'commission',
                        {key: value for key, value in run.items() if key != 'operations'})
                    for candidate in run.get('candidates', []):
                        methods.update(candidate.get('method_versions', []))
                    methods.update(run['frozen'].get('method_versions', []))
                    for operation in sorted(run['operations'], key=lambda row: row['id']):
                        identity = 'operation:' + operation['id']
                        add(identity, 'operation', {key: value for key, value in operation.items()
                            if key not in {'input', 'result'}})
                        add(identity + ':input', 'operation_input', operation['input'])
                        if operation.get('result') is not None:
                            add(identity + ':result', 'operation_result', operation['result'])
                for demo in state['demos']:
                    if demo.get('provenance', {}).get('commission_id') in visible_ids:
                        add('demonstration:' + demo['id'], 'demonstration', demo)
                for method in self.trash.documents(db, 'methods.animation_methods', owner):
                    if method['id'] in methods:
                        add('method:' + method['id'], 'method', method)
                        research.update(method.get('research_record_ids', []))
                for record in self.trash.documents(db,'methods.animation_method_scene_space',owner):
                    origin=record.get('evidence') or {}
                    if record['method_version_id'] in methods and (
                            not origin.get('commission_id') or origin['commission_id'] in visible_ids):
                        add('method-scene-space:'+record['id'],'method_scene_space',record)
                for observation in self.trash.documents(db, 'methods.animation_research_records', owner):
                    if observation['id'] in research:
                        add('research:' + observation['id'], 'research', observation)
                for table, kind in (('feedback', 'feedback'), ('feedback_withdrawals', 'feedback_withdrawal')):
                    for row in self.trash.documents(db, table, owner):
                        if row['commission_id'] in visible_ids:
                            add(kind + ':' + row['id'], kind, row)
                for effect in state['effects']:
                    members = [row for row in effect['commissions'] if row['id'] in visible_ids]
                    if members:
                        view = deepcopy(dict(effect, commissions=members))
                        visible_candidates={candidate['id'] for run in runs for candidate in _candidates(run)}
                        if view.get('adopted_candidate_id') not in visible_candidates:
                            view['adopted_candidate_id']=None
                        if view.get('open_commission_id') not in visible_ids:
                            usable = [row for row in members if row.get('best_candidate_id') and row['state'] == 'completed']
                            chosen = max(usable or members, key=lambda row: (row['created_at'], row['id']))
                            run = state['by_id'][chosen['id']]
                            candidate = next((row for row in _candidates(run) if row['id'] == run['best_candidate_id']), None)
                            engine = _sample_engine(candidate, _sample_index(state['demos']))
                            view.pop('authoring_tools', None)
                            view.update(engine)
                            best = (dict(id=candidate['id'], media_url=_public_url(run['id'], candidate['id'], 'media'),
                                **engine) if candidate else None)
                            view.update(open_commission_id=run['id'], request_id=run['request_id'], state=run['state'],
                                best_candidate=best, media_url=best['media_url'] if best else None,
                                engines=[engine['engine']] if engine['engine'] else [])
                        add('effect:' + effect['id'], 'effect', view)
        return sorted(records, key=lambda row: row['record_id'])

    def knowledge_catalogue(self, owner):
        _require(self.knowledge is not None, 'animation_method_knowledge_unavailable', 503)
        withdrawn = self._withdrawn_experience_ids(owner)
        return dict(pages=[dict(page_id=row['page_id'], revision=row['revision'],
            title=row['body']['title'], status=row['status'], content_sha256=row['content_sha256'],
            evidence_status='withdrawn_feedback' if withdrawn & set(row['evidence_ids']) else 'current')
            for row in self.knowledge.list_pages(owner, limit=64)],
            experiences=[{key: row[key] for key in ('id', 'kind', 'source_id', 'content_sha256')}
                for row in self.knowledge.list_experiences(owner, limit=64)])

    def knowledge_page(self, owner, page_id):
        _require(self.knowledge is not None, 'animation_method_knowledge_unavailable', 503)
        page = self.knowledge.get_page(owner, page_id)
        return dict(page=page, evidence_issues=sorted(self._withdrawn_experience_ids(owner) & set(page['evidence_ids'])), history=[
            {key: row[key] for key in ('revision', 'status', 'change_kind', 'change_note', 'content_sha256')}
            for row in self.knowledge.page_history(owner, page_id)[-64:]])

    def knowledge_experience(self, owner, experience_id):
        _require(self.knowledge is not None, 'animation_method_knowledge_unavailable', 503)
        return self.knowledge.get_experience(owner, experience_id)

    def knowledge_page_revision(self, owner, page_id, revision):
        _require(self.knowledge is not None, 'animation_method_knowledge_unavailable', 503)
        page = self.knowledge.get_page(owner, page_id, revision=revision)
        return dict(page=page, current_revision=self.knowledge.get_page(owner, page_id)['revision'],
            evidence_issues=sorted(self._withdrawn_experience_ids(owner) & set(page['evidence_ids'])))

    def revise_knowledge_page(self, owner, page_id, body):
        raise ServiceError('animation_knowledge_manual_edit_retired', 410)

    def publication_readiness(self, owner, method_version_id):
        return self.methods.publication_readiness(owner, method_version_id)

    def published_catalogue(self, owner):
        result = dict(methods=[_method_view(row) for row in self.methods.list_methods(owner, published_only=True)])
        _require(len(canonical_bytes(result)) <= 2 * 1024 * 1024,
                 'animation_method_catalogue_too_large', 413)
        return result

    def get_method_version(self, owner, method_version_id):
        result = self.methods.get_method(owner, method_version_id)
        _require(len(canonical_bytes(result)) <= 2 * 1024 * 1024,
                 'animation_method_response_too_large', 413)
        return result

    def get_method_scene_space(self,owner,method_version_id):
        return dict(schema='video.animation-method-scene-records.v1',
            records=self.methods.get_scene_space(owner,method_version_id))

    @serialized_library_mutation
    def accept_validation(self, owner, validation_id, body):
        required = {'request_id', 'method_content_sha256', 'comment', 'human_verification'}
        _require(type(body) is dict and set(body) == required)
        self.trash.require_validation_visible(owner, validation_id)
        return self.methods.accept(owner, dict(validation_id=validation_id,
            **{key: deepcopy(value) for key, value in body.items() if key != 'request_id'}), body['request_id'])

    @staticmethod
    def _protocol(run):
        return skill_protocol if run['frozen'].get('protocol_schema') == skill_protocol.INPUT_SCHEMA else protocol

    def _experience(self, owner, kind, source_id, document):
        if self.knowledge is not None:
            return self.knowledge.record_experience(owner, kind, source_id, document)

    def _withdrawn_experience_ids(self, owner):
        with closing(sqlite3.connect(self.metadata_path, timeout=20)) as db:
            return {'exp_' + digest(dict(owner=owner, kind='developer_feedback', source_id=row[0]))
                    for row in db.execute('SELECT feedback_id FROM feedback_withdrawals WHERE owner=?', (owner,))}

    def _sync_feedback_knowledge(self, owner):
        if self.knowledge is None:
            return
        with closing(sqlite3.connect(self.metadata_path, timeout=20)) as db:
            feedback = [json.loads(row[0]) for row in db.execute('SELECT document FROM feedback WHERE owner=?', (owner,))]
            withdrawals = [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM feedback_withdrawals WHERE owner=?', (owner,))]
        for record in feedback:
            self._experience(owner, 'developer_feedback', record['id'], record)
        for record in withdrawals:
            self._experience(owner, 'feedback_withdrawal', record['id'], record)

    def _knowledge_snapshot(self, owner):
        _require(self.knowledge is not None, 'animation_method_knowledge_unavailable', 503)
        self._sync_feedback_knowledge(owner)
        withdrawn = self._withdrawn_experience_ids(owner)
        records = [row for row in self.knowledge.list_experiences(owner, limit=128)
                   if row['id'] not in withdrawn and row['kind'] != 'knowledge_proposal']
        pages = [row['page_id'] for row in self.knowledge.list_pages(owner, limit=256)
                 if row['status'] != 'deprecated' and not (withdrawn & set(row['evidence_ids']))]
        return self.knowledge.snapshot(owner, page_ids=pages, experience_ids=[row['id'] for row in records])

    def _sync_knowledge(self, owner, run):
        """Recover local knowledge writes without another paid submission."""
        if self._protocol(run) is not skill_protocol:
            return
        for operation in run['operations']:
            if operation['status'] in {'failed', 'cancelled'}:
                self._experience(owner, 'execution_failure', operation['id'], dict(
                    commission_id=run['id'], operation_id=operation['id'], stage=operation['stage'],
                    input_sha256=operation['input_sha256'], error=operation.get('error'),
                    status=operation['status'], scope='actual_stopped_operation_record'))
            if operation['status'] != 'completed':
                continue
            result = operation['result']
            if operation['input'].get('schema') == 'video.animation-collaboration-input.v1':
                self._experience(owner, 'authored_proposal', operation['id'], dict(
                    commission_id=run['id'], operation_id=operation['id'],
                    goal=run['frozen']['goal'], phase=operation['input']['phase'],
                    input_sha256=operation['input_sha256'], result_sha256=operation['result_sha256'],
                    scope='authored_collaborative_proposal_not_rendered_or_accepted'))
                continue
            if operation['stage'] == 'render':
                self._experience(owner, 'render', operation['id'], dict(
                    commission_id=run['id'], operation_id=operation['id'], goal=run['frozen']['goal'],
                    author_operation_id=operation['input']['author_operation_id'],
                    input_sha256=operation['input_sha256'], result_sha256=operation['result_sha256'],
                    engine=operation['input']['source']['engine'], media=result.get('media'),
                    technical=result.get('technical'), runtime=result.get('runtime'),
                    scope='actual_render_record_not_human_or_complete_motion_verification'))
                continue
            opinion = result.get('review')
            if opinion and self._effect_review_binding(run) is not None and operation['stage'] == 'review':
                self._experience(owner, 'classified_sampled_review', operation['id'], dict(
                    commission_id=run['id'], operation_id=operation['id'], goal=run['frozen']['goal'],
                    input_sha256=operation['input_sha256'], result_sha256=operation['result_sha256'],
                    review_schema=result['schema'], review_sha256=digest(opinion),
                    full_review_pointer='/review', full_review_location='immutable_operation_result',
                    observation=operation['input']['observation'],
                    scope='static_samples_only_not_artistic_or_continuous_motion_acceptance'))
                continue
            experience_kind = 'sampled_review' if opinion else 'authored_proposal'
            experience_document = dict(
                commission_id=run['id'], operation_id=operation['id'], goal=run['frozen']['goal'],
                input_sha256=operation['input_sha256'], result_sha256=operation['result_sha256'],
                mode=result['mode'], summary=opinion['summary'] if opinion else 'Authored proposal; not rendered or accepted.',
                outcome=opinion['outcome'] if opinion else None,
                observation=operation['input']['observation'],
                scope='sampled_images_only' if opinion else 'authored_not_rendered_or_accepted')
            if opinion:
                experience_id = 'exp_' + digest(dict(owner=owner, kind=experience_kind, source_id=operation['id']))
                try:
                    previous = self.knowledge.get_experience(owner, experience_id)
                except ServiceError as error:
                    if error.status != 404:
                        raise
                    previous = None
                # Existing experiences and snapshots are immutable. Replay the
                # original shape for older records; only new archives gain details.
                if previous is None or 'diagnostics_projection' in previous['document'] or {
                        'criteria_results', 'remaining_issues', 'next_guidance'} <= set(previous['document']):
                    experience_document = _review_experience(experience_document, opinion)
            self._experience(owner, experience_kind, operation['id'], experience_document)
            snapshot = operation['input'].get('knowledge_snapshot')
            # Published bytes are immutable; new experimental facts above are
            # retained, but old working-Wiki proposals cannot mutate a release.
            if 'library_binding' in run['frozen'] or snapshot is None or not result.get('knowledge_proposals'):
                continue
            proposal = self.knowledge.record_proposals(owner, operation['id'], result['knowledge_proposals'],
                snapshot_id=snapshot['id'])
            try:
                _require(not any(self._withdrawn_experience_ids(owner) & set(row['evidence_ids'])
                                 for row in result['knowledge_proposals']),
                         'animation_method_knowledge_evidence_withdrawn', 409)
                self.knowledge.apply_proposals(owner, proposal['id'])
            except ServiceError as error:
                if error.status != 409:
                    raise
                self._experience(owner, 'knowledge_conflict', operation['id'] + ':' + error.code, dict(
                    commission_id=run['id'], proposal_id=proposal['id'], snapshot_id=snapshot['id'],
                    error=error.code, scope='candidate_preserved_without_overwriting_newer_knowledge'))

    def _capabilities(self):
        if self._runtime_capabilities is not None:
            return deepcopy(self._runtime_capabilities)
        provider = getattr(self.renderer, 'capabilities', None)
        return validate_runtime_capabilities(provider()) if callable(provider) else None

    @staticmethod
    def _feedback_view(db, owner, record):
        withdrawn = db.execute('SELECT document FROM feedback_withdrawals '
            'WHERE owner=? AND commission_id=? AND feedback_id=?',
            (owner, record['commission_id'], record['id'])).fetchone()
        return dict(record, kind=record.get('kind', 'comment'), withdrawn=withdrawn is not None,
                    withdrawal=json.loads(withdrawn[0]) if withdrawn else None)

    def _feedback(self, owner, commission_id):
        with closing(sqlite3.connect(self.metadata_path)) as db:
            return [self._feedback_view(db, owner, json.loads(row[0])) for row in db.execute(
                'SELECT document FROM feedback WHERE owner=? AND commission_id=? ORDER BY rowid',
                (owner, commission_id))]

    def _demonstrations(self, owner, commission_id):
        return {row['provenance'].get('candidate_id'): row
            for row in self.methods.list_demonstrations(owner)
            if row['provenance'].get('commission_id') == commission_id}

    def _effect_memberships(self, owner):
        with closing(sqlite3.connect(self.metadata_path)) as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM effect_memberships WHERE owner=? ORDER BY rowid', (owner,))]

    def _commission_deletions(self, owner):
        records = []
        with closing(sqlite3.connect(self.metadata_path)) as db:
            for identity, raw in db.execute('SELECT commission_id,document FROM commission_deletions '
                                           'WHERE owner=? ORDER BY rowid', (owner,)):
                try:
                    record = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if (type(record) is dict and record.get('owner') == owner
                        and record.get('commission_id') == identity):
                    records.append(record)
        return records

    def _get_visible_commission(self, owner, commission_id):
        run = self.commissions.get(owner, commission_id)
        _require(not _deleted_commission_ids([run], self._commission_deletions(owner)),
                 'animation_method_commission_not_found', 404)
        _require(commission_id not in self.trash.hidden(owner), 'animation_method_commission_not_found', 404)
        return run

    @serialized_library_mutation
    def record_failed_attempt_deletion(self, owner, commission_id, *, reason):
        """Operator-only removal from business views; preserve internal ancestry.

        Only stopped failures without any retained sample are eligible. No
        execution, frozen input, budget or media is modified by this operation.
        """
        _require(type(reason) is str and bool(reason.strip()) and len(reason) <= 2000)
        run = self.commissions.get(owner, commission_id)
        _require(run['state'] == 'failed' and not _candidates(run),
                 'animation_method_failed_attempt_deletion_ineligible', 409)
        _require(all(op['status'] in OP_TERMINAL for op in run['operations']),
                 'animation_method_failed_attempt_deletion_operation_active', 409)
        document = dict(schema='video.animation-commission-deletion.v1', owner=owner,
            commission_id=commission_id, commission_frozen_sha256=run['frozen_sha256'],
            reason=reason, source='explicit_owner_failed_attempt_deletion')
        with closing(sqlite3.connect(self.metadata_path, timeout=20)) as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT document FROM commission_deletions WHERE owner=? AND commission_id=?',
                               (owner, commission_id)).fetchone()
            if prior:
                record = json.loads(prior[0])
                _require({key: record.get(key) for key in document} == document,
                         'animation_method_failed_attempt_deletion_conflict', 409)
                return record
            removed = [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM effect_memberships WHERE owner=? '
                'AND (commission_id=? OR canonical_commission_id=?) ORDER BY rowid',
                (owner, commission_id, commission_id))]
            record = dict(document, created_at=self.clock(), removed_memberships=removed)
            db.execute('INSERT INTO commission_deletions VALUES (?,?,?)',
                       (owner, commission_id, canonical_bytes(record).decode()))
            db.execute('DELETE FROM effect_memberships WHERE owner=? '
                       'AND (commission_id=? OR canonical_commission_id=?)',
                       (owner, commission_id, commission_id))
            db.commit()
            return record

    @serialized_library_mutation
    def record_effect_membership(self, owner, commission_id, canonical_commission_id, *, reason):
        """Operator-only historical correction; never exposed as an HTTP write route.

        This annotates separately created attempts after explicit owner confirmation.
        It creates no retry or creator dependency, accepts no new execution budget,
        and leaves both immutable commission ledgers and all media untouched.
        """
        _require(type(reason) is str and bool(reason.strip()) and len(reason) <= 2000)
        previous = self._get_visible_commission(owner, commission_id)
        canonical = self._get_visible_commission(owner, canonical_commission_id)
        issue = _historical_membership_issue(previous, canonical)
        _require(issue is None, issue or 'animation_method_effect_membership_invalid', 409)
        _require(all(op['status'] in OP_TERMINAL
                     for run in (previous, canonical) for op in run['operations']),
                 'animation_method_effect_membership_operation_active', 409)
        document = dict(schema='video.animation-effect-membership.v1', owner=owner,
            commission_id=commission_id, canonical_commission_id=canonical_commission_id,
            commission_frozen_sha256=previous['frozen_sha256'],
            canonical_frozen_sha256=canonical['frozen_sha256'], reason=reason,
            source='explicit_owner_historical_grouping')
        with closing(sqlite3.connect(self.metadata_path, timeout=20)) as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT document FROM effect_memberships WHERE owner=? AND commission_id=?',
                               (owner, commission_id)).fetchone()
            if prior:
                record = json.loads(prior[0])
                _require({key: record.get(key) for key in document} == document,
                         'animation_method_effect_membership_conflict', 409)
                return record
            # Targets must remain canonical roots; never turn an existing root
            # into a member or use another member as the target of a new link.
            conflict = db.execute('SELECT 1 FROM effect_memberships WHERE owner=? '
                'AND (commission_id=? OR canonical_commission_id=?) LIMIT 1',
                (owner, canonical_commission_id, commission_id)).fetchone()
            _require(conflict is None, 'animation_method_effect_membership_cycle_or_chain', 409)
            # Verify already stored media only for a new annotation. Replaying
            # an existing annotation returns its original record unchanged.
            self.artifact(owner, canonical_commission_id, canonical['best_candidate_id'], 'media')
            record = dict(document, created_at=self.clock())
            db.execute('INSERT INTO effect_memberships VALUES (?,?,?,?)',
                (owner, commission_id, canonical_commission_id, canonical_bytes(record).decode()))
            db.commit()
            return record

    def catalogue(self, owner):
        capabilities = self._capabilities()
        rows = []
        runs = self.commissions.list_all(owner)
        deletions = self._commission_deletions(owner)
        trash_state, effects, _ = self.trash.views(owner)
        hidden = _deleted_commission_ids(runs, deletions) | trash_state['hidden']
        demonstrations = self.methods.list_demonstrations(owner)
        samples = _sample_index(demonstrations)
        deletion = {row['id']: row['deletion'] for effect in effects for row in effect['commissions']}
        for run in runs:
            if run['id'] in hidden:
                continue
            candidate = next((item for item in _candidates(run) if item['id'] == run['best_candidate_id']), None)
            engine = _sample_engine(candidate, samples)
            best = (dict(id=candidate['id'], media_url=_public_url(run['id'], candidate['id'], 'media'), **engine)
                    if candidate else None)
            row = dict(id=run['id'], title=run['title'], goal=run['frozen']['goal'],
                       request_id=run['request_id'], state=run['state'], best_candidate_id=run['best_candidate_id'],
                       best_candidate=best, stop_reason=run['stop_reason'], deletion=deletion[run['id']], **engine)
            rows.append(row)
        return dict(schema='video.animation-method-lab.v1', effects=effects, commissions=rows,
                    availability=dict(enabled=self.author is not None and self.reviewer is not None
                        and self.renderer is not None, reason=None,
                        collaboration=self.collaboration_author is not None,
                        allowed_engines=capabilities['allowed_engines'] if capabilities else ['html-svg'],
                        motion_tools=motion_tools(capabilities),
                        authoring_libraries=deepcopy(capabilities.get('authoring_libraries', {})) if capabilities else {},
                        resource_authoring=dict(manim=self.manim.capabilities() if self.manim else
                            dict(enabled=False, status='prepared_not_admitted', kind='manim'))))

    def get(self, owner, commission_id):
        run = self._get_visible_commission(owner, commission_id)
        all_demos = self.methods.list_demonstrations(owner)
        all_runs = self.commissions.list_all(owner)
        _, _, membership = self.trash.views(owner)
        samples = _sample_index(all_demos)
        effect = membership[commission_id]
        separated_anchor = effect.get('separation', {}).get('commission_id') == commission_id
        own_candidate_ids = {row['id'] for row in run['candidates']}
        creation_reference = None
        demos = {row['provenance'].get('candidate_id'): row for row in all_demos}
        methods, candidates = {}, []
        for candidate in _candidates(run):
            row = {k: deepcopy(v) for k, v in candidate.items() if k != 'source'}
            row.update(title=candidate.get('title') or '候选 ' + str(len(candidates) + 1),
                media_url=_public_url(commission_id, candidate['id'], 'media'),
                source_url=_public_url(commission_id, candidate['id'], 'source'),
                technical_status=candidate.get('technical', {}).get('status', 'unverified'),
                method_version_ids=candidate.get('method_versions', []),
                demonstration_id=demos.get(candidate['id'], {}).get('id'),
                **_sample_engine(candidate, samples))
            if separated_anchor and candidate['id'] not in own_candidate_ids:
                # Borrowed creation inputs remain traceable, but are not
                # versions of the distinct effect shown in the library.
                creation_reference = dict(commission_id=run['frozen']['base_commission_id'],
                                          candidate=row)
                continue
            candidates.append(row)
            for version_id in candidate.get('method_versions', []):
                methods[version_id] = self.methods.get_method(owner, version_id)
        validations = [item for version_id in methods
                       for item in self.methods.list_validations(owner, method_version_id=version_id)]
        best = next((row for row in candidates if row['id'] == run['best_candidate_id']), None)
        selected_method_ids = best['method_version_ids'] if best else []
        blockers = []
        readiness = []
        for version_id in selected_method_ids:
            if methods[version_id]['schema'] == METHOD_SCHEMA:
                ready = self.methods.publication_readiness(owner, version_id)
                readiness.append(ready)
                blockers.extend(ready['unmet_conditions'])
                continue
            selected = [row for row in validations if row['method_version_id'] == version_id
                        and row['outcome'] != 'failed']
            references = {row['demonstration_version_id'] for row in selected if row['role'] == 'reference'}
            if not any(row['role'] == 'reference' for row in selected):
                blockers.append('缺少代表样片验证')
            if not any(row['role'] == 'transfer' and row.get('baseline_demonstration_version_id') in references
                       for row in selected):
                blockers.append('尚未完成异结构迁移验证')
        if not selected_method_ids:
            blockers.append('尚未形成可验收的创作方法')
        demo_ids = {row['id']: row for row in all_demos}
        validation_views = {row['id']: _validation_view(row,
            demonstration=demo_ids[row['demonstration_version_id']],
            method=methods[row['method_version_id']]) for row in validations}
        transfers = []
        for validation in validations:
            if validation['role'] != 'transfer':
                continue
            demo = demo_ids[validation['demonstration_version_id']]
            origin = demo['provenance']
            origin_run = next((item for item in all_runs if item['id'] == origin['commission_id']), None)
            origin_candidate = next((item for item in _candidates(origin_run)
                if item['id'] == origin['candidate_id']), None) if origin_run else None
            transfers.append(dict(validation_views[validation['id']], title=demo['title'], goal=demo['goal'],
                demonstration_id=demo['id'],
                **_sample_engine(origin_candidate, _sample_index([demo])),
                media_url=_public_url(origin['commission_id'], origin['candidate_id'], 'media'),
                source_url=_public_url(origin['commission_id'], origin['candidate_id'], 'source')))
        relevant_demos = [demo_ids[row['demonstration_version_id']] for row in validations
                          if row['method_version_id'] in selected_method_ids and row['outcome'] != 'failed']
        plan = next((op['result'] for op in run['operations']
                     if op['stage'] == 'creator' and op['input'].get('mode') == 'plan'
                     and op['status'] == 'completed'), None)
        standards = run['frozen'].get('criteria') or (plan['criteria'] if plan else [])
        latest_review = run['reviews'][-1] if run['reviews'] else {}
        summary, error = run['summary'], None
        if type(summary) is dict:
            # The durable ledger keeps structured failure/cancellation detail;
            # the browser's summary is prose, never an object coerced to text.
            message = summary.get('message')
            summary = (message if type(message) is str else
                       '开发者取消了本次实验' if run['stop_reason'] == 'cancelled' else
                       '本次实验已结束，请查看执行记录')
            code = run['summary'].get('code')
            if type(code) is str:
                error = code
        if run['state'] == 'failed':
            summary, error = _public_failure(run)
        detail = dict(schema='video.animation-method-lab-detail.v1', id=run['id'], request_id=run['request_id'],
            effect={key: deepcopy(effect[key]) for key in (
                'id', 'title', 'open_commission_id', 'commissions', 'relationship_status', 'relationship_issues',
                'engine', 'engine_status', 'engines', 'deletion')},
            deletion=next(row['deletion'] for row in effect['commissions'] if row['id'] == commission_id),
            title=effect['title'] if separated_anchor else run['title'],
            goal=run['frozen']['goal'], state=run['state'],
            reference=deepcopy(run['frozen'].get('reference')),
            duration_seconds=run['frozen'].get('duration_seconds', 18),
            feedback_text=run['frozen'].get('feedback', ''),
            retry_of_commission_id=run['frozen'].get('retry_of_commission_id'),
            retry_budget=dict(max_rounds=run['budget']['render'], max_seconds=run['budget']['seconds']),
            can_retry=(run['state'] in {'failed', 'cancelled'} and all(
                op['status'] in OP_TERMINAL for op in run['operations'])),
            **({key: best[key] for key in ('engine', 'engine_status')} if best else
               dict(engine=None, engine_status='unknown')),
            budget=run['budget'], usage=run['usage'], remaining=run['remaining'],
            criteria=standards, candidates=candidates, best_candidate_id=run['best_candidate_id'],
            baseline_candidate=next((row for row in candidates if row['id'] ==
                (run['frozen'].get('base_candidate_id') or (run.get('retry_base_candidate') or {}).get('id'))), None),
            methods=[_method_view(row) for row in methods.values()], publication_method_version_ids=selected_method_ids,
            method_publication_readiness=readiness,
            requires_audio_verification=any(row['media'].get('audio_sha256') for row in relevant_demos),
            transfers=transfers,
            validations=[validation_views[row['id']] for row in validations], feedback=self._feedback(owner, commission_id),
            stop_reason=run['stop_reason'], remaining_issues=latest_review.get('remaining_issues', []),
            summary=summary, error=error, publication_ready=not blockers, publishable=not blockers,
            publication_blockers=sorted(set(blockers)), reviews=run['reviews'],
            execution=[{k: op.get(k) for k in ('id', 'stage', 'status', 'job_id', 'error',
                'reused_from', 'artifact_operation_id')}
                       for op in run['operations']])
        if separated_anchor:
            detail['effect']['separation'] = deepcopy(effect['separation'])
            detail['execution_title'] = run['title']
            detail['creation_reference'] = creation_reference
        if self._protocol(run) is skill_protocol:
            _require(len(canonical_bytes(detail)) <= 2 * 1024 * 1024,
                     'animation_method_response_too_large', 413)
        if run['frozen'].get('resource_authoring'):
            decision = run.get('adopted_capabilities')
            if run['frozen'].get('resource_planning') and not (decision or {}).get('uses_manim'):
                state = dict(kind='manim', status='not_adopted' if decision else
                    ('cancelled' if run['cancel_requested'] else
                     'not_started' if run['state'] in TERMINAL else 'awaiting_plan'),
                    operation_id=None, usage=dict(attempts=0), error=None,
                    scope='resource_not_visual_acceptance')
            else:
                options = ({'resource_round': max(self._manim_round(run),
                           self.resource_stage.allocated_round(owner, commission_id) or 0)}
                       if 'manim_iteration' in run['frozen'] else {})
                state = (self.resource_stage.view(owner, commission_id, **options) if self.manim else
                    dict(kind='manim', status='unavailable', error='manim_resource_coordinator_required'))
            detail['resource_authoring'] = dict(deepcopy(run['frozen']['resource_authoring']), **state)
        detail.update(effect_id=effect['id'], representative_revision=effect.get('representative_revision', 0),
            adopted_candidate_id=effect.get('adopted_candidate_id'),
            creation_mode=run['frozen'].get('creation_mode', 'continue' if run['frozen'].get('base_commission_id') else 'new'),
            use_wikiskill=run['frozen'].get('use_wikiskill', True),
            library_binding=deepcopy(run['frozen'].get('library_binding')),
            problem_summary=deepcopy(run['frozen'].get('problem_summary', [])))
        detail['adopted_capabilities'] = deepcopy(run.get('adopted_capabilities'))
        if run['frozen'].get('picture_origin') is not None:
            detail['picture_origin'] = {key: deepcopy(value) for key, value in
                run['frozen']['picture_origin'].items()
                if key not in {'source', 'method', 'manim_source_context'}}
        if 'capability_selection' in run['frozen']:
            detail['capability_selection'] = deepcopy(run['frozen']['capability_selection'])
        if run['frozen'].get('collaboration'):
            from .animation_collaboration import progress_view
            detail['collaboration'] = progress_view(run)
        return detail

    @serialized_library_mutation
    def start(self, owner, body, *, _submission_snapshot=None, _picture_origin=None):
        fields = {'request_id', 'title', 'goal', 'reference', 'duration_seconds', 'budget',
                  'base_commission_id', 'base_candidate_id', 'feedback', 'criteria', 'method_versions',
                  'role', 'baseline_demonstration_id', 'structural_changes', 'retry_of_commission_id',
                  'research_record_ids', 'content_changes', 'resource_authoring', 'collaboration', 'capability_selection',
                  'creation_mode', 'effect_id', 'use_wikiskill', 'problem_summary', 'renderer_rebind', 'review_contract'}
        if type(body) is dict and body.get('baseline_demonstration_id'):
            self.trash.require_demonstrations_visible(owner, [body['baseline_demonstration_id']])
        _require(type(body) is dict and {'request_id', 'budget'} <= set(body)
                 and ('goal' in body or 'retry_of_commission_id' in body) and set(body) <= fields)
        _require('use_wikiskill' not in body or type(body['use_wikiskill']) is bool,
                 'animation_creation_knowledge_choice_invalid')
        request_id = body['request_id']
        from .animation_prompt_inspection import submission_snapshot
        submitted = (deepcopy(_submission_snapshot) if _submission_snapshot is not None else
                     submission_snapshot(body))
        data = {key: deepcopy(value) for key, value in body.items() if key != 'request_id' and value is not None}
        review_choice = data.pop('review_contract', None)
        if _picture_origin is not None:
            from .picture_animation_bridge import validate_origin
            _require(not data.get('base_commission_id') and not data.get('retry_of_commission_id')
                     and data.get('creation_mode', 'new') == 'new', 'picture_animation_origin_invalid')
            data['picture_origin'] = validate_origin(_picture_origin)
        explicit_selection = 'capability_selection' in data
        if 'capability_selection' in data:
            data['capability_selection'] = normalize_selection(data['capability_selection'])
        previous_request = next((row for row in self.commissions.list_all(owner)
                                 if row['request_id'] == request_id), None)
        skill_mode = (previous_request['frozen'].get('protocol_schema') == skill_protocol.INPUT_SCHEMA
                      if previous_request is not None else self.author_protocol is skill_protocol)
        if previous_request is not None:
            self._get_visible_commission(owner, previous_request['id'])
        retry_source = None
        _require('renderer_rebind' not in body or (body.get('renderer_rebind') == 'current-admitted'
            and 'retry_of_commission_id' in data), 'animation_method_renderer_rebind_invalid')
        if 'retry_of_commission_id' in data:
            retry_fields = {'request_id', 'retry_of_commission_id', 'budget'}
            if 'renderer_rebind' in body:
                retry_fields.add('renderer_rebind')
            _require(set(body) == retry_fields,
                     'animation_method_retry_creative_contract_changed', 409)
            retry_source = (self.commissions.get(owner, data['retry_of_commission_id'])
                if previous_request is not None else self._get_visible_commission(owner, data['retry_of_commission_id']))
            _require(retry_source['state'] in {'failed', 'cancelled'} and all(
                op['status'] in OP_TERMINAL for op in retry_source['operations']),
                'animation_method_retry_not_available', 409)
            for key, value in data.items():
                if key in {'budget', 'retry_of_commission_id', 'renderer_rebind'}:
                    continue
                _require(value == retry_source['frozen'].get(key),
                    'animation_method_commission_idempotency_conflict' if previous_request else
                    'animation_method_retry_creative_contract_changed', 409)
            budget = deepcopy(data['budget'])
            retry_id = data['retry_of_commission_id']
            data = {key: deepcopy(value) for key,value in retry_source['frozen'].items()
                    if key != 'base_candidate'}
            data.update(budget=budget, retry_of_commission_id=retry_id)
            if 'renderer_rebind' in body:
                from .animation_renderer_rebind import freeze_binding
                _require(type(budget) is dict and budget.get('max_rounds') == 1,
                    'animation_method_renderer_rebind_single_render_required', 409)
                data['renderer_rebind'] = body['renderer_rebind']
                # An HTTP replay must retain its original new-runtime binding,
                # even if another renderer has since been admitted.
                if previous_request is not None:
                    _require(previous_request.get('submission_snapshot', {}).get('document', {}).get('renderer_rebind')
                        == body['renderer_rebind'], 'animation_method_commission_idempotency_conflict', 409)
                    data['render_context_binding'] = deepcopy(previous_request['frozen']['render_context_binding'])
                else:
                    data['render_context_binding'] = freeze_binding(retry_source, self._capabilities())
        from .animation_creation_policy import normalize
        if previous_request is None or 'creation_mode' in previous_request['frozen']:
            data = normalize(data)
        if data.get('effect_id'):
            effects = self.catalogue(owner)['effects']
            effect = next((row for row in effects if row['id'] == data['effect_id']), None)
            _require(effect is not None, 'animation_method_effect_not_found', 404)
            if data.get('base_commission_id'):
                _require(any(row['id'] == data['base_commission_id'] for row in effect['commissions']),
                         'animation_creation_effect_mismatch', 409)
            if data.get('creation_mode') == 'fresh' and effect['best_candidate']:
                self.adoptions.ensure(owner, effect['id'], effect['open_commission_id'],
                                      effect['best_candidate']['id'])
        if data.get('base_commission_id'):
            baseline_run = self._get_visible_commission(owner, data['base_commission_id'])
            for key in ('collaboration', 'capability_selection'):
                if key not in data and baseline_run['frozen'].get(key):
                    data[key] = deepcopy(baseline_run['frozen'][key])
            chosen_creation = data.get('capability_selection', {}).get('creation', [])
            if (not explicit_selection or 'manim' in chosen_creation) and 'resource_authoring' not in data:
                if baseline_run['frozen'].get('resource_authoring'):
                    data['resource_authoring'] = deepcopy(baseline_run['frozen']['resource_authoring'])
        if 'capability_selection' in data:
            data['capability_selection'] = normalize_selection(data['capability_selection'])
            if previous_request is None:
                validate_new_selection(data['capability_selection'])
        if previous_request is not None:
            if previous_request['frozen'].get('resource_planning'):
                data['resource_planning'] = previous_request['frozen']['resource_planning']
        elif 'manim' in data.get('capability_selection', {}).get('creation', []):
            data['resource_planning'] = 'after-plan.v1'
        duration = data.setdefault('duration_seconds', 18)
        _require(type(duration) in (int, float) and 1 <= duration <= 60
                 and duration * 24 == int(duration * 24))
        if 'capability_selection' in data:
            resource = manim_request(data['capability_selection'], data.get('resource_authoring'), duration)
            if resource is not None:
                data['resource_authoring'] = resource
        budget = data['budget']
        _require(type(budget) is dict and set(budget) == {'max_rounds', 'max_seconds'})
        rounds, seconds = budget['max_rounds'], budget['max_seconds']
        _require(type(rounds) is int and 1 <= rounds <= 8 and type(seconds) is int and 60 <= seconds <= 3600)
        data['budget'] = dict(creator=rounds + 1, review=rounds, render=rounds, seconds=seconds)
        if 'collaboration' in data:
            from .animation_collaboration import validate_request, required_creator_budget
            _require(type(data['collaboration']) is dict, 'animation_collaboration_request_invalid')
            data['collaboration'] = validate_request(data['collaboration'], budget=budget)
            _require(skill_mode and self.collaboration_author is not None,
                     'animation_collaboration_not_admitted', 503)
            _require(not data.get('resource_authoring') and data.get('role', 'reference') == 'reference',
                     'animation_collaboration_scope_invalid')
            data['budget']['creator'] = required_creator_budget(data['collaboration'])
        if data.get('resource_authoring') is not None:
            from .manim_method_coordinator import authoring_request
            data['resource_authoring'] = authoring_request(data['resource_authoring'], duration)
            _require(self.manim is not None, 'manim_method_resource_not_admitted', 503)
            if not data.get('resource_planning'):
                self.manim.preflight(self.clock() + seconds)
        # Runtime admission is trusted server configuration, never a browser
        # field. Replay uses the original server-injected snapshot even if a
        # later deployment admits additional engines or changes image hashes.
        if previous_request is not None or retry_source is not None:
            origin = previous_request or retry_source
            for key in ('constraints', 'assets'):
                if key in origin['frozen']:
                    data[key] = deepcopy(origin['frozen'][key])
        else:
            capabilities = self._capabilities()
            if capabilities is not None:
                data['constraints'] = dict(allowed_engines=capabilities['allowed_engines'],
                                          runtime_identity=capabilities['runtime_identity'])
                data['assets'] = capabilities['assets']
                if capabilities.get('authoring_libraries'):
                    from .animejs_library import method_authoring_context
                    data['constraints'].update(method_authoring_context(capabilities))
            if 'capability_selection' in data:
                data['constraints'] = freeze_creation_selection(data['capability_selection'],
                    data.get('constraints', {}), data.get('assets', {}))
            if set(data.get('constraints', {}).get('allowed_engines', [])) & {'canvas', 'three'}:
                from .animation_runtime_capabilities import drawing_context_budget
                data['constraints']['native_drawing_context_budget'] = drawing_context_budget()
        allowed_engines = data.get('constraints', {}).get('allowed_engines', ['html-svg'])
        if previous_request is None and retry_source is None and 'canvas' in allowed_engines:
            provider=getattr(self.renderer,'scene_space_capabilities',None)
            if callable(provider) and not data.get('resource_authoring') and not data.get('resource_planning') and not data.get('collaboration'):
                scene_capability=provider()
                if scene_capability.get('admitted') is True:
                    data.setdefault('constraints',{})['scene_space_capability']=deepcopy(scene_capability)
        if data.get('criteria'):
            protocol.criteria(data['criteria'])
        _require(data.get('role', 'reference') in {'reference', 'transfer'})
        if data.get('role') == 'transfer':
            _require(not data.get('base_commission_id') and not data.get('base_candidate_id'),
                     'animation_method_transfer_identity_invalid', 409)
        if data.get('base_commission_id'):
            previous = self._get_visible_commission(owner, data['base_commission_id'])
            baseline_id = data.get('base_candidate_id') or previous['best_candidate_id']
            baseline = next((row for row in _candidates(previous) if row['id'] == baseline_id), None)
            _require(baseline is not None, 'animation_method_baseline_missing', 409)
            data.setdefault('method_versions', baseline.get('method_versions', []))
            if not data.get('criteria'):
                old = self.get(owner, previous['id'])
                data['criteria'] = old['criteria']
        if data.get('use_wikiskill') is False and data.get('role') != 'transfer':
            data['method_versions'] = []
        version_ids = data.get('method_versions', [])
        _require(type(version_ids) is list and len(version_ids) <= 32
                 and all(type(item) is str for item in version_ids)
                 and len(set(version_ids)) == len(version_ids))
        for version_id in version_ids:
            version = self.methods.get_method(owner, version_id)
            _require(version['runtime_requirements'].get('engine') in allowed_engines,
                     'animation_method_runtime_not_admitted', 409)
            from .animation_skill_compatibility import require_method_compatible
            from .manim_method_resources import PRODUCER_REQUIREMENT
            require_method_compatible(version, assets=data.get('assets', {}),
                authoring_libraries=data.get('constraints', {}).get('authoring_libraries', {}),
                resource_authoring=PRODUCER_REQUIREMENT if data.get('resource_authoring') else {})
        if data.get('resource_authoring'):
            if previous_request is not None:
                if 'manim_iteration' in previous_request['frozen']:
                    data['manim_iteration'] = deepcopy(previous_request['frozen']['manim_iteration'])
            elif 'manim_iteration' not in data:
                data['manim_iteration'] = (self._manim_iteration_context(owner, previous, baseline,
                    allow_without_resource=bool(data.get('resource_planning')))
                    if data.get('base_commission_id') else dict(criteria=deepcopy(data.get('criteria', [])),
                        methods=[self.methods.get_method(owner, version_id) for version_id in version_ids]))
                if data.get('use_wikiskill') is False:
                    data['manim_iteration']['methods'] = []
            if not data.get('resource_planning') and callable(getattr(self.manim, 'recipe_guidance', None)):
                self.manim.recipe_guidance(owner, data)
        else:
            data.pop('manim_iteration', None)
        if data.get('collaboration') and previous_request is None and retry_source is None:
            from .creative_direction import build_creative_direction
            # Freeze the direction with the request. Later feedback must not
            # change an already reserved operation or its idempotency hash.
            baseline = baseline if data.get('base_commission_id') else None
            opinions = self._feedback(owner, data['base_commission_id']) if baseline else []
            data.setdefault('constraints', {}).setdefault('allowed_engines', allowed_engines)
            data['constraints']['creative_direction'] = build_creative_direction(
                goal=data['goal'], criteria=data.get('criteria', []), allowed_engines=allowed_engines,
                baseline=baseline, feedback=data.get('feedback', ''), developer_feedback=opinions)
        research_ids = data.get('research_record_ids', [])
        _require(type(research_ids) is list and len(research_ids) <= 64
                 and all(type(item) is str for item in research_ids)
                 and len(set(research_ids)) == len(research_ids))
        for record_id in research_ids:
            self.methods.get_research(owner, record_id)
        if data.get('role') == 'transfer':
            _require(bool(version_ids), 'animation_method_transfer_methods_required', 409)
            standards = self._method_criteria(owner, version_ids)
            _require(not data.get('criteria') or data['criteria'] == standards,
                     'animation_method_transfer_criteria_mismatch', 409)
            data['criteria'] = standards
            _structural_changes(data.get('structural_changes'))
            baseline = self.methods.get_demonstration(owner, data.get('baseline_demonstration_id'))
            if skill_mode:
                _require(not data.get('reference') and not research_ids
                         and all(self.methods.get_method(owner, version_id)['schema'] == METHOD_SCHEMA
                                 for version_id in version_ids),
                         'animation_skill_method_transfer_reference_present', 409)
                changes = validate_content_changes(data.get('content_changes'))
                _require(changes['before'] == baseline['goal'] and changes['after'] == data['goal'],
                         'animation_skill_method_transfer_content_mismatch', 409)
                _require(bool({'relations', 'task', 'staging', 'data_structure', 'explanation_problem'}
                              & set(data['structural_changes'])),
                         'animation_method_substantive_transfer_required', 409)
            for version_id in data.get('method_versions', []):
                _require(any(row['role'] == 'reference' and row['demonstration_version_id'] == baseline['id']
                    and row['outcome'] != 'failed' for row in self.methods.list_validations(
                        owner, method_version_id=version_id)), 'animation_method_transfer_baseline_required', 409)
            change = data['structural_changes'].get('duration')
            _require(not change or (change['before'] == baseline['media']['duration_seconds']
                     and change['after'] == duration), 'animation_method_transfer_duration_mismatch', 409)
        # Preflight is a read, not a paid task or budget reservation.
        self._freeze_effect_review(data, review_choice, origin=previous_request or retry_source)
        _require(self.author is not None and self.reviewer is not None and self.renderer is not None,
                 'animation_method_service_unavailable', 503)
        for gateway in (self.author, self._effect_review_gateway(dict(frozen=data)) or self.reviewer,
                        *([self.collaboration_author] if data.get('collaboration') else [])):
            if callable(getattr(gateway, 'preflight', None)):
                gateway.preflight(self.clock() + seconds)
        if 'creation_mode' in data:
            if previous_request is not None:
                data['library_binding'] = deepcopy(previous_request['frozen']['library_binding'])
            elif retry_source is not None and 'library_binding' in retry_source['frozen']:
                data['library_binding'] = deepcopy(retry_source['frozen']['library_binding'])
            else:
                data['library_binding'] = self.library_adapter.pin(owner,
                    'animation-lab-request:' + request_id, use_wikiskill=data['use_wikiskill'])
        if skill_mode:
            data['protocol_schema'] = skill_protocol.INPUT_SCHEMA
            if 'library_binding' in data:
                snapshot = self.library_adapter.laboratory_snapshot(owner, data['library_binding'])
                data['knowledge_snapshot_id'] = snapshot['id']
            elif previous_request is not None:
                data['knowledge_snapshot_id'] = previous_request['frozen']['knowledge_snapshot_id']
            else:
                data['knowledge_snapshot_id'] = (None if data.get('role') == 'transfer' else
                    self.knowledge.snapshot(owner, page_ids=[], experience_ids=[])['id']
                    if data.get('collaboration') else self._knowledge_snapshot(owner)['id'])
            if previous_request is None:
                self._experience(owner, 'brief', request_id, dict(
                    goal=data['goal'], feedback=data.get('feedback', ''),
                    role=data.get('role', 'reference'), scope='requested_goal_not_rendered_or_accepted'))
        # Validate the complete bounded input before reserving paid work.
        if data.get('collaboration'):
            self._collaboration_document(owner, dict(frozen=data, operations=[]), 'plan')
        else:
            self._document(owner, dict(frozen=data, operations=[]), 'plan')
        run = self.commissions.create(owner, data, request_id, submission_snapshot=submitted)
        if retry_source is not None:
            self.commissions.restore_completed_creators(owner, run['id'], retry_source['id'])
        return self.get(owner, run['id'])

    @serialized_library_mutation
    def adopt(self, owner, commission_id, body):
        _require(type(body) is dict and set(body) == {
            'request_id', 'effect_id', 'candidate_id', 'expected_revision'})
        run = self._get_visible_commission(owner, commission_id)
        _require(run['state'] in TERMINAL, 'animation_effect_adoption_commission_active', 409)
        effect = next((row for row in self.catalogue(owner)['effects']
            if row['id'] == body['effect_id'] and any(item['id'] == commission_id for item in row['commissions'])), None)
        _require(effect is not None, 'animation_method_effect_not_found', 404)
        candidate = next((row for row in run['candidates'] if row['id'] == body['candidate_id']), None)
        _require(candidate is not None and candidate.get('technical', {}).get('status') == 'passed',
                 'animation_effect_adoption_candidate_invalid', 409)
        self.artifact(owner, commission_id, candidate['id'], 'media')
        if self.adoptions.get(owner, effect['id']) is None:
            current = effect.get('best_candidate')
            self.adoptions.ensure(owner, effect['id'], effect['open_commission_id'] if current else commission_id,
                                  current['id'] if current else candidate['id'])
        self.adoptions.adopt(owner, effect['id'], commission_id, candidate['id'],
            body['expected_revision'], body['request_id'])
        return self.get(owner, commission_id)

    def prompt_inspection(self, owner, commission_id):
        from .animation_prompt_inspection import inspection_view
        return inspection_view(self._get_visible_commission(owner, commission_id))

    def operation_input(self, owner, commission_id, operation_id):
        from .animation_prompt_inspection import operation_input_view
        run = self._get_visible_commission(owner, commission_id)
        operation = self.commissions.operation(owner, operation_id)
        return operation_input_view(run, operation)

    @serialized_library_mutation
    def cancel(self, owner, commission_id, body):
        _require(body == {})
        self._get_visible_commission(owner, commission_id)
        self.commissions.cancel(owner, commission_id)
        return self.get(owner, commission_id)

    @serialized_library_mutation
    def feedback(self, owner, commission_id, body):
        """Save an opinion; it grants no selection, acceptance or publication.

        Omitted kind preserves the original comment contract. Spatial/time
        annotations belong to one candidate; comparisons explicitly bind two
        candidates and cannot borrow an ambiguous region or time range.
        """
        _require(type(body) is dict and 'request_id' in body)
        kind = body.get('kind', 'comment')
        _require(type(kind) is str and kind in {'comment', 'preference'})
        common = {'request_id', 'kind', 'comment'}
        if kind == 'comment':
            _require({'candidate_id', 'comment'} <= set(body) and set(body) <= common |
                     {'candidate_id', 'start_seconds', 'end_seconds', 'region'})
        else:
            _require({'left_candidate_id', 'right_candidate_id', 'preference'} <= set(body)
                     and set(body) <= common | {'left_candidate_id', 'right_candidate_id', 'preference'})
        _require(type(body['request_id']) is str and 1 <= len(body['request_id']) <= 200)
        comment = body.get('comment', '')
        _require(type(comment) is str and len(comment.strip()) <= 16000
                 and (kind == 'preference' or bool(comment.strip())))
        run = self._get_visible_commission(owner, commission_id)
        candidates = {row['id']: row for row in _candidates(run)}
        ids = ('candidate_id',) if kind == 'comment' else ('left_candidate_id', 'right_candidate_id')
        for key in ids:
            _require(type(body[key]) is str and 1 <= len(body[key]) <= 200)
            _require(body[key] in candidates, 'animation_method_candidate_not_found', 404)
        if kind == 'preference':
            _require(body['left_candidate_id'] != body['right_candidate_id']
                     and type(body['preference']) is str
                     and body['preference'] in {'left', 'right', 'equal', 'neither'})
        else:
            _require(('start_seconds' in body) == ('end_seconds' in body),
                     'animation_method_feedback_range_invalid')
            if 'start_seconds' in body:
                duration = candidates[body['candidate_id']]['media']['duration_seconds']
                _require(_finite_number(body['start_seconds']) and _finite_number(body['end_seconds'])
                         and 0 <= body['start_seconds'] <= body['end_seconds'] <= duration,
                         'animation_method_feedback_range_invalid')
            if 'region' in body:
                region = body['region']
                _require(type(region) is dict and set(region) == {'x', 'y', 'width', 'height'}
                         and all(_finite_number(value) and 0 <= value <= 1 for value in region.values())
                         and region['x'] + region['width'] <= 1 and region['y'] + region['height'] <= 1,
                         'animation_method_feedback_region_invalid')
        try:
            checksum = digest(dict(commission_id=commission_id, body=body))
        except (TypeError, ValueError, OverflowError, UnicodeError):
            raise ServiceError('animation_method_request_invalid', 422) from None
        with closing(sqlite3.connect(self.metadata_path, timeout=20)) as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT sha256,document FROM feedback WHERE owner=? AND request_id=?',
                               (owner, body['request_id'])).fetchone()
            if prior:
                _require(prior[0] == checksum, 'animation_method_feedback_conflict', 409)
                record = json.loads(prior[1])
            else:
                record = dict(body, kind=kind, id=str(uuid4()), commission_id=commission_id,
                              source='explicit_developer_feedback', created_at=self.clock())
                db.execute('INSERT INTO feedback VALUES (?,?,?,?,?)',
                           (owner, body['request_id'], commission_id, checksum, canonical_bytes(record).decode()))
            db.commit()
            result = self._feedback_view(db, owner, record)
        self._sync_feedback_knowledge(owner)
        return result

    @serialized_library_mutation
    def withdraw_feedback(self, owner, commission_id, body):
        """Append a durable withdrawal without mutating the original opinion."""
        _require(type(body) is dict and set(body) == {'request_id', 'feedback_id'}
                 and all(type(value) is str and 1 <= len(value) <= 200 for value in body.values()))
        self._get_visible_commission(owner, commission_id)
        try:
            checksum = digest(dict(commission_id=commission_id, body=body))
        except (TypeError, ValueError, OverflowError, UnicodeError):
            raise ServiceError('animation_method_request_invalid', 422) from None
        with closing(sqlite3.connect(self.metadata_path, timeout=20)) as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT sha256 FROM feedback_withdrawal_requests WHERE owner=? AND request_id=?',
                               (owner, body['request_id'])).fetchone()
            if prior:
                _require(prior[0] == checksum, 'animation_method_feedback_conflict', 409)
            records = (json.loads(row[0]) for row in db.execute(
                'SELECT document FROM feedback WHERE owner=? AND commission_id=?', (owner, commission_id)))
            record = next((row for row in records if row['id'] == body['feedback_id']), None)
            _require(record is not None, 'animation_method_feedback_not_found', 404)
            view = self._feedback_view(db, owner, record)
            if not view['withdrawn']:
                withdrawal = dict(id=str(uuid4()), request_id=body['request_id'], feedback_id=record['id'],
                    commission_id=commission_id, source='explicit_developer_feedback_withdrawal', created_at=self.clock())
                db.execute('INSERT INTO feedback_withdrawals VALUES (?,?,?,?)',
                           (owner, commission_id, record['id'], canonical_bytes(withdrawal).decode()))
            if not prior:
                db.execute('INSERT INTO feedback_withdrawal_requests VALUES (?,?,?,?,?)',
                           (owner, body['request_id'], commission_id, record['id'], checksum))
            db.commit()
            result = self._feedback_view(db, owner, record)
        self._sync_feedback_knowledge(owner)
        return result

    def _method_criteria(self, owner, version_ids):
        standards = {}
        for version_id in version_ids:
            for criterion in self.methods.get_method(owner, version_id)['criteria']:
                prior = standards.get(criterion['id'])
                _require(prior is None or prior == criterion,
                         'animation_method_transfer_criteria_conflict', 409)
                standards[criterion['id']] = deepcopy(criterion)
        return list(standards.values())

    @serialized_library_mutation
    def transfer(self, owner, commission_id, body):
        allowed = {'request_id', 'title', 'goal', 'structural_changes', 'budget',
                   'duration_seconds', 'reference', 'base_candidate_id', 'feedback', 'content_changes',
                   'capability_selection', 'resource_authoring', 'use_wikiskill'}
        _require(type(body) is dict and {'request_id', 'goal', 'structural_changes', 'budget'} <= set(body)
                 and set(body) <= allowed)
        run = self._get_visible_commission(owner, commission_id)
        candidate_id = body.get('base_candidate_id') or run['best_candidate_id']
        source = next((row for row in _candidates(run) if row['id'] == candidate_id), None)
        demo = next((row for row in self.methods.list_demonstrations(owner)
                     if row['provenance'].get('candidate_id') == candidate_id), None)
        _require(demo is not None, 'animation_method_baseline_missing', 409)
        _require(source is not None, 'animation_method_baseline_missing', 409)
        request = dict(body, role='transfer', baseline_demonstration_id=demo['id'],
                       method_versions=source['method_versions'],
                       criteria=self._method_criteria(owner, source['method_versions']))
        if 'capability_selection' in run['frozen']:
            request.setdefault('capability_selection', deepcopy(run['frozen']['capability_selection']))
        chosen_creation = request.get('capability_selection', {}).get('creation', [])
        if 'capability_selection' not in body or 'manim' in chosen_creation:
            if run['frozen'].get('resource_authoring'):
                request.setdefault('resource_authoring', deepcopy(run['frozen']['resource_authoring']))
        request.pop('base_candidate_id', None)
        # The new goal creates a new composition from the exact method, not a copied source baseline.
        from .animation_prompt_inspection import submission_snapshot
        return self.start(owner, request, _submission_snapshot=submission_snapshot(body, action='transfer'))

    @serialized_library_mutation
    def publish(self, owner, commission_id, body):
        required = {'request_id', 'method_version_ids', 'demonstration_ids'}
        _require(type(body) is dict and required <= set(body)
                 and set(body) <= required | {'human_verification'})
        current = self.get(owner, commission_id)
        _require(type(body['method_version_ids']) is list and bool(body['method_version_ids'])
                 and type(body['demonstration_ids']) is list and bool(body['demonstration_ids']))
        self.trash.require_demonstrations_visible(owner, body['demonstration_ids'])
        allowed = set(current['publication_method_version_ids'])
        _require(set(body['method_version_ids']) <= allowed, 'animation_method_publication_target_invalid', 409)
        human = body.get('human_verification')
        legacy_selected = any(self.methods.get_method(owner, version_id)['schema'] != METHOD_SCHEMA
                              for version_id in body['method_version_ids'])
        if legacy_selected:
            _require(type(human) is dict and human.get('motion_watched') is True,
                     'animation_method_human_verification_required', 409)
            _require(not current['requires_audio_verification'] or human.get('audio_listened') is True,
                     'animation_method_human_audio_required', 409)
        results = []
        for version_id in body['method_version_ids']:
            version = self.methods.get_method(owner, version_id)
            validations = self.methods.list_validations(owner, method_version_id=version_id)
            applicable = [row for row in validations if row['demonstration_version_id'] in body['demonstration_ids']
                          and row['outcome'] != 'failed']
            _require(any(row['role'] == 'reference' for row in applicable)
                     and any(row['role'] == 'transfer' for row in applicable),
                     'animation_method_publication_blocked', 409)
            if version['schema'] == METHOD_SCHEMA:
                # Exact validation/media-bound developer decisions are a
                # separate explicit API action. A general publish click or
                # model verdict cannot manufacture those confirmations.
                _require(all(row.get('acceptance') is not None for row in applicable)
                         and self.methods.publication_readiness(owner, version_id)['ready'],
                         'animation_method_publication_blocked', 409)
                self.trash.require_publication_visible(owner, version_id)
                results.append(version if version['status'] == 'published' else
                    self.methods.publish(owner, version_id, body['request_id'] + ':publish:' + version_id))
                continue
            for validation in applicable:
                decision = dict(validation_id=validation['id'],
                    method_content_sha256=version['content_sha256'],
                    comment='开发者已确认此完整样片与迁移结果符合目标')
                decision['human_verification'] = dict(
                    criteria_ids=[row['id'] for row in version['criteria']],
                    motion_watched=human.get('motion_watched') is True,
                    audio_listened=human.get('audio_listened') is True)
                if validation.get('acceptance') is None:
                    self.methods.accept(owner, decision, body['request_id'] + ':accept:' + validation['id'])
            self.trash.require_publication_visible(owner, version_id)
            results.append(version if version['status'] == 'published' else
                self.methods.publish(owner, version_id, body['request_id'] + ':publish:' + version_id))
        return dict(methods=[_method_view(row) for row in results], publication='published')

    def read_methods(self, owner, body):
        _require(type(body) is dict and set(body) == {'method_version_ids', 'consumer_id'})
        result = self.methods.read_published(owner, body['method_version_ids'], body['consumer_id'])
        _require(len(canonical_bytes(result)) <= 2 * 1024 * 1024,
                 'animation_method_response_too_large', 413)
        return result

    def artifact(self, owner, commission_id, candidate_id, kind):
        run = self._get_visible_commission(owner, commission_id)
        candidate = next((row for row in _candidates(run) if row['id'] == candidate_id), None)
        _require(candidate is not None, 'animation_method_candidate_not_found', 404)
        if kind == 'source':
            return canonical_bytes(candidate['source']), 'application/json'
        _require(kind == 'media')
        path = Path(self.renderer.media_path(self._artifact_operation_id(owner, candidate['operation_id'])))
        _require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 32 * 1024 * 1024,
                 'animation_method_media_changed', 409)
        raw = path.read_bytes()
        _require(len(raw) <= 32 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == candidate['media']['sha256'],
                 'animation_method_media_changed', 409)
        return raw, 'video/mp4'

    def _artifact_operation_id(self, owner, operation_id):
        operation = self.commissions.operation(owner, operation_id)
        return operation.get('artifact_operation_id', operation_id)

    def _reuse_retry_render(self, owner, run, operation):
        """Reuse only exact stopped media, never a failed rendering or a review."""
        if run['frozen'].get('render_context_binding') is not None:
            return False
        previous_id = run['frozen'].get('retry_of_commission_id')
        if previous_id is None or operation['status'] != 'reserved':
            return False
        author = self.commissions.operation(owner, operation['input']['author_operation_id'])
        origin = author.get('reused_from')
        if not origin or origin['commission_id'] != previous_id:
            return False
        previous = self.commissions.get(owner, previous_id)
        render = next((row for row in previous['operations']
            if row['stage'] == 'render' and row['status'] == 'completed'
            and row['input'].get('author_operation_id') == origin['operation_id']
            and (row.get('result') or {}).get('technical', {}).get('status') == 'passed'), None)
        if render is None:
            return False
        artifact_id = render.get('artifact_operation_id', render['id'])
        path = Path(self.renderer.media_path(artifact_id))
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
            return False
        raw = path.read_bytes()
        if (len(raw) > 32 * 1024 * 1024
                or hashlib.sha256(raw).hexdigest() != render['result']['media']['sha256']):
            return False
        self.commissions.reuse_render(owner, operation['id'], render['id'])
        return True

    def _retry_creator_already_reviewed(self, owner, run, authored):
        origin = authored.get('reused_from')
        if not origin or origin['commission_id'] != run['frozen'].get('retry_of_commission_id'):
            return False
        previous = self.commissions.get(owner, origin['commission_id'])
        reviewed = {row['candidate_id'] for row in previous['reviews']}
        return any(candidate['id'] in reviewed and candidate.get('provenance', {}).get(
            'author_operation_id') == origin['operation_id'] for candidate in previous['candidates'])

    def trash_catalogue(self, owner):
        return self.trash.catalogue(owner)

    def trash_commission(self, owner, commission_id, body):
        return self.trash.delete(owner, 'commission', commission_id, body)

    def trash_effect(self, owner, effect_id, body):
        return self.trash.delete(owner, 'effect', effect_id, body)

    def restore_trash(self, owner, entry_id, body):
        return self.trash.restore(owner, entry_id, body)

    @staticmethod
    def _uses_manim(run):
        if run['frozen'].get('resource_planning'):
            return bool(run.get('adopted_capabilities', {}).get('uses_manim'))
        return bool(run['frozen'].get('resource_authoring'))

    @staticmethod
    def _effective_selection(run):
        decision = run.get('adopted_capabilities')
        if run['frozen'].get('resource_planning') and decision is not None:
            return {key: deepcopy(decision[key]) for key in ('creation', 'motion')}
        return deepcopy(run['frozen'].get('capability_selection', {}))

    def _derive_capability_decision(self, run, operation, result=None):
        """Reuse the existing plan contract; clients cannot supply adoption."""
        result = operation['result'] if result is None else result
        methods = (operation['input']['methods'] if run['frozen'].get('role') == 'transfer'
                   else result['methods'])
        _require(bool(methods), 'animation_method_capability_plan_missing', 409)
        engines = {row['runtime_requirements']['engine'] for row in methods}
        _require(len(engines) == 1, 'animation_method_capability_plan_ambiguous', 409)
        engine = next(iter(engines))
        uses_manim = any(row['runtime_requirements'].get('resource_authoring') for row in methods)
        from .manim_method_resources import PRODUCER_REQUIREMENT, require_method_producer
        if uses_manim:
            require_method_producer(methods)
        else:
            _require(not any(row.get('skill_package', {}).get('runtime_requirements', {}).get(
                'resource_authoring') for row in methods), 'animation_method_capability_plan_ambiguous', 409)
        selection = run['frozen']['capability_selection']
        candidates = selection.get('creation', [])
        _require(engine in run['frozen']['constraints']['allowed_engines'],
                 'animation_method_runtime_not_admitted', 409)
        _require(engine in candidates or (engine == 'html-svg' and uses_manim),
                 'animation_method_capability_plan_outside_selection', 409)
        _require(candidates != ['manim'] or uses_manim,
                 'animation_method_capability_plan_manim_required', 409)
        from .animation_skill_compatibility import require_method_compatible
        for method in methods:
            require_method_compatible(method, assets=run['frozen'].get('assets', {}),
                authoring_libraries=run['frozen'].get('constraints', {}).get('authoring_libraries', {}),
                resource_authoring=PRODUCER_REQUIREMENT if uses_manim else {})
        creation = ([engine] if engine in candidates else []) + (['manim'] if uses_manim else [])
        motion = [] if creation == ['manim'] else deepcopy(selection.get('motion', []))
        return dict(creation=creation, motion=motion, engine=engine, uses_manim=uses_manim,
                    plan_operation_id=operation['id'], plan_result_sha256=digest(result))

    def _manim_round(self, run, *, mode=None, base=None):
        creates = [op for op in run['operations'] if op.get('stage') == 'creator'
                   and op.get('input', {}).get('mode') == 'create']
        if mode == 'review' and base is not None:
            author_id = base.get('provenance', {}).get('author_operation_id')
            index = next((i for i, op in enumerate(creates) if op['id'] == author_id), None)
            _require(index is not None, 'manim_method_candidate_round_missing', 409)
            return index
        current = max(0, len(creates) - 1)
        if (run.get('cancel_requested') or run.get('state') in TERMINAL
                or self.clock() + 30 >= run.get('deadline_at', float('inf'))
                or any(op['status'] in {'failed', 'cancelled'} for op in run['operations'])):
            return current
        if not creates or not all(run.get('remaining', {}).get(key, 0) > 0
                                  for key in ('creator', 'render', 'review')):
            return current
        latest = next((candidate for candidate in run.get('candidates', [])
                       if candidate.get('provenance', {}).get('author_operation_id') == creates[-1]['id']), None)
        review = next((item for item in reversed(run.get('reviews', []))
                       if latest is not None and item['candidate_id'] == latest['id']), None)
        if review is None or review['outcome'] == 'goal_met':
            return current
        if (len(run['reviews']) >= 2
                and all(row['outcome'] in {'equal', 'regressed', 'inconclusive'} for row in run['reviews'][-2:])
                and len({digest(row['strategy']) for row in run['candidates'][-2:]}) >= 2):
            return current
        return len(creates)

    def _manim_iteration_context(self, owner, run, base, *, review=None, frozen_feedback=None,
                                 allow_without_resource=False):
        """Bind the actual previous Python resource, not an HTML reconstruction."""
        context = {}
        if base is not None:
            author_id = base.get('provenance', {}).get('author_operation_id')
            author = next((op for op in run['operations'] if op['id'] == author_id), None)
            descriptors = (author or {}).get('input', {}).get('constraints', {}).get('generated_resources', [])
            if len(descriptors) == 1:
                context['baseline_descriptor'] = deepcopy(descriptors[0])
            elif base.get('id') == (run['frozen'].get('base_candidate') or {}).get('id'):
                original = run['frozen'].get('manim_iteration', {}).get('baseline_descriptor')
                if original is not None:
                    context['baseline_descriptor'] = deepcopy(original)
            _require(allow_without_resource or 'baseline_descriptor' in context,
                     'manim_method_iteration_baseline_missing', 409)
            context['methods'] = [self.methods.get_method(owner, version_id)
                                  for version_id in base.get('method_versions', [])]
        plan = next((op['result'] for op in run['operations'] if op.get('stage') == 'creator'
                     and op.get('input', {}).get('mode') == 'plan' and op['status'] == 'completed'), {})
        context['criteria'] = deepcopy(run['frozen'].get('criteria') or plan.get('criteria', []))
        if review is None:
            review = next((op['result']['review'] for op in reversed(run['operations'])
                           if op['stage'] == 'review' and op['status'] == 'completed'), None)
        if review is not None:
            context['review'] = deepcopy(review)
        context['developer_feedback'] = deepcopy(frozen_feedback if frozen_feedback is not None else
            [item for item in self._feedback(owner, run['id']) if not item.get('withdrawn')])
        return context

    def _manim_projection(self, owner, run, *, mode=None, base=None):
        if 'library_binding' in run['frozen']:
            run = deepcopy(run)
            run['library_binding'] = deepcopy(run['frozen']['library_binding'])
        if 'manim_iteration' not in run['frozen']:
            return run
        if run['frozen'].get('retry_of_commission_id') and not run.get('reviews'):
            previous = self.commissions.get(owner, run['frozen']['retry_of_commission_id'])
            original = self._manim_projection(owner, previous, mode=mode)
            projected = deepcopy(run)
            projected['resource_round'] = original.get('resource_round', 0)
            projected['frozen']['manim_iteration'] = deepcopy(original['frozen']['manim_iteration'])
            projected['frozen']['feedback'] = original['frozen'].get('feedback', '')
            return projected
        resource_round = self._manim_round(run, mode=mode, base=base)
        if mode != 'review':
            allocated = self.resource_stage.allocated_round(owner, run['id'])
            if allocated is not None:
                resource_round = max(resource_round, allocated)
        projected = deepcopy(run)
        projected['resource_round'] = resource_round
        if run['frozen'].get('resource_planning') and not resource_round:
            plan = next(op for op in run['operations'] if op['input'].get('mode') == 'plan'
                        and op['status'] == 'completed')
            context = projected['frozen']['manim_iteration']
            context['criteria'] = deepcopy(plan['result']['criteria'])
            context['methods'] = deepcopy(plan['input']['methods'] if run['frozen'].get('role') == 'transfer'
                                          else plan['result']['methods'])
        if resource_round:
            creates = [op for op in run['operations'] if op.get('stage') == 'creator'
                       and op.get('input', {}).get('mode') == 'create']
            previous = next(item for item in run['candidates']
                            if item.get('provenance', {}).get('author_operation_id') == creates[resource_round - 1]['id'])
            evaluation = next(row for row in run['reviews'] if row['candidate_id'] == previous['id'])
            baseline_id = evaluation.get('best_candidate_id') or previous['id']
            baseline = next((item for item in _candidates(run) if item['id'] == baseline_id), previous)
            review_operation = next(op for op in run['operations'] if op['id'] == evaluation['operation_id'])
            projected['frozen']['manim_iteration'] = self._manim_iteration_context(owner, run, baseline,
                review=review_operation['result']['review'],
                frozen_feedback=run['frozen']['manim_iteration'].get('developer_feedback', []),
                allow_without_resource=bool(run['frozen'].get('resource_planning')))
            projected['frozen']['feedback'] = review_operation['result']['review']['next_guidance']
        return projected

    def _collaboration_document(self, owner, run, phase, **task):
        from .animation_collaboration import operation_input
        frozen = run['frozen']
        constraints = dict(width=1280, height=720, fps=24,
            duration_seconds=frozen.get('duration_seconds', 18),
            **deepcopy(frozen.get('constraints', {'allowed_engines': ['html-svg']})))
        plans = [op for op in run['operations'] if op['input'].get('phase') == 'plan'
                 and op['status'] == 'completed']
        standards = frozen.get('criteria') or (plans[0]['result']['criteria'] if plans else [])
        selected = [self.methods.get_method(owner, version_id)
                    for version_id in frozen.get('method_versions', [])]
        base = frozen.get('base_candidate') if frozen.get('creation_mode') != 'fresh' else None
        from .animation_creation_policy import authoring_constraints
        if 'creation_mode' in frozen:
            constraints['authoring_policy'] = authoring_constraints(frozen)
            constraints['animation_knowledge'] = self.library_adapter.laboratory_snapshot(owner, frozen['library_binding'])
            constraints['library_binding'] = deepcopy(frozen['library_binding'])
        if frozen.get('use_wikiskill') is False:
            selected = []
        document = dict(goal=frozen['goal'], criteria=deepcopy(standards),
            constraints=constraints, assets=deepcopy(frozen.get('assets', {})),
            reference=deepcopy(frozen.get('reference')), feedback=frozen.get('feedback', ''),
            methods=selected, base_source=deepcopy(base['source']) if base and phase == 'integrate' else None)
        return operation_input(document, phase, config=frozen['collaboration'], **task)

    def _schedule_collaboration(self, owner, run):
        from .animation_collaboration import next_action
        action = next_action(run, now=self.clock())
        if action['kind'] == 'reserve':
            for task in action['operations']:
                document = self._collaboration_document(owner, run, task['phase'],
                    brief=task.get('brief'), component_id=task.get('component_id'),
                    dependencies=task.get('dependencies', []))
                self.commissions.reserve(owner, run['id'], 'creator', document, task['idempotency_key'])
        elif action['kind'] == 'render':
            self.commissions.reserve(owner, run['id'], 'render', action['input'], action['idempotency_key'])
        elif action['kind'] in {'stop', 'complete'}:
            self.commissions.stop(owner, run['id'], action.get('reason', 'budget_exhausted'),
                action.get('message', '本次协作初作已结束，可评价完整样片或继续发起优化'))

    def _document(self, owner, run, mode, *, base=None, feedback='', frames=None, comparison=None):
        active_protocol = self._protocol(run)
        plan = next((op['result'] for op in run['operations'] if (op['input'].get('mode') == 'plan'
                     or op['input'].get('phase') == 'plan')
                     and op['status'] == 'completed'), None)
        standards = run['frozen'].get('criteria') or (plan['criteria'] if plan else [])
        if run['frozen'].get('creation_mode') == 'fresh' and base and base.get('id') not in {
                row['id'] for row in run.get('candidates', [])} | {
                (run.get('retry_base_candidate') or {}).get('id')}:
            base = None
        versions = (base.get('method_versions', []) if base else run['frozen'].get('method_versions', []))
        if run['frozen'].get('use_wikiskill') is False and run['frozen'].get('role') != 'transfer':
            versions = []
        selected = [self.methods.get_method(owner, version_id) for version_id in versions]
        if not selected and plan:
            selected = deepcopy(plan['methods'])
        frozen_constraints = deepcopy(run['frozen'].get('constraints', {}))
        if mode == 'review' and run['frozen'].get('render_context_binding') is not None:
            from .animation_renderer_rebind import review_constraints
            frozen_constraints.update(review_constraints(run['frozen']['render_context_binding']))
        if 'creation_mode' in run['frozen']:
            from .animation_creation_policy import authoring_constraints
            frozen_constraints['authoring_policy'] = authoring_constraints(run['frozen'])
            frozen_constraints['library_binding'] = deepcopy(run['frozen']['library_binding'])
            if active_protocol is not skill_protocol:
                frozen_constraints['animation_knowledge'] = self.library_adapter.laboratory_snapshot(
                    owner, run['frozen']['library_binding'])
        assets = deepcopy(run['frozen'].get('assets', {}))
        deferred = bool(run['frozen'].get('resource_planning'))
        decision = run.get('adopted_capabilities')
        if deferred and mode != 'plan':
            _require(decision is not None, 'animation_method_capability_decision_missing', 409)
            frozen_constraints.update(allowed_engines=[decision['engine']],
                adopted_capabilities=deepcopy(decision), capability_selection=self._effective_selection(run))
        if deferred and mode == 'plan':
            from .manim_method_resources import PRODUCER_REQUIREMENT
            frozen_constraints['resource_authoring_plan'] = dict(
                candidate=deepcopy(run['frozen']['resource_authoring']),
                producer_requirements=deepcopy(PRODUCER_REQUIREMENT),
                instruction=(
                    'No resource has been made. Choose actual drawing capabilities now, using the existing '
                    'methods contract: return nonempty methods with one consistent browser engine. Declare '
                    'runtime_requirements.resource_authoring equal to producer_requirements in every method '
                    'and its Skill package only when adopting Manim. Otherwise omit that dependency entirely. '
                    'For transfer, apply the frozen input methods and return methods=[] as required. '
                    'html-svg outside the requested drawing choices is available solely as a real Manim '
                    'carrier. A pure Manim decision uses native Manim motion, not browser motion libraries. '
                    'Never claim resources already exist or wait to read their output in this plan. '
                    'This choice will be frozen before any producer task and cannot be reversed in create.'))
        if self._uses_manim(run) and run.get('id') and not (deferred and mode == 'plan'):
            _require(self.manim is not None, 'manim_method_resource_coordinator_required', 503)
            projected = self._manim_projection(owner, run, mode=mode, base=base)
            options = ({'resource_round': projected['resource_round']} if 'resource_round' in projected else {})
            descriptor = self.resource_stage.context(owner, run['id'], **options)
            placement = self.resource_stage.placement(owner, projected)
            _require(not (set(assets) & set(descriptor['assets_sha256'])), 'manim_method_resource_collision', 409)
            assets.update(descriptor['assets_sha256'])
            frozen_constraints['generated_resources'] = [descriptor]
            optional_resource = (not deferred and 'capability_selection' in run['frozen']
                and run['frozen']['capability_selection'].get('creation', []) != ['manim'])
            frozen_constraints['generated_resource_usage'] = (
                'A stopped Manim PNG resource has already been created for this experiment. '
                + ('You may choose this resource or another selected drawing capability. If choosing it, use '
                   if optional_resource else 'Use ')
                + 'it as a visual layer in your original scene composition, without running Python or '
                'rebuilding its frames. Import {prepareManimMethodFrames} from /assets/manim_method_frames.mjs. '
                'The exact manifest object is constraints.generated_resources[0].manifest: embed that object '
                'directly in scene.js (connect-src is disabled, so do not fetch JSON). Prepare a positioned '
                'container, call prepareManimMethodFrames(container, manifest, '
                '{placementStartFrame: ' + str(placement['global_start_frame']) + '}), expose its ready promise '
                'through window.__animationExperiment.ready, and call layer.seekFrame(Math.round(seconds*24)) '
                'from the existing synchronous seek(seconds). The manifest retains resource-local timing; '
                'the supplied placementStartFrame maps it into this project without changing the frames. '
                'Outside its half-open placed global frame range the '
                'layer is hidden; create your composition for the remaining project time. The helper '
                'predecodes at most 60 bounded PNGs. Keep all other scene motion on the same absolute clock. '
                'Do not use autoplay, timers, per-scene MP4 or autonomous playback.')
            from .manim_method_resources import PRODUCER_REQUIREMENT
            frozen_constraints['method_resource_producer_requirements'] = deepcopy(PRODUCER_REQUIREMENT)
            frozen_constraints['generated_resource_usage'] += (
                ' Every returned method must declare runtime_requirements.resource_authoring equal to '
                'constraints.method_resource_producer_requirements. In a v2 method its skill_package '
                'must declare the same dependency in its own runtime_requirements. Write and hash the '
                'original complete package yourself. A portable Skill teaches how to generate a new '
                'resource for changed content; never embed this task PNGs or immutable envelope IDs '
                'as if they were reusable knowledge. The host rejects missing producer declarations.')
        document = dict(schema=active_protocol.INPUT_SCHEMA, mode=mode, goal=run['frozen']['goal'],
            criteria=standards, methods=selected,
            constraints=dict(width=1280, height=720, fps=24,
                             duration_seconds=run['frozen'].get('duration_seconds', 18),
                             allowed_engines=frozen_constraints.pop('allowed_engines', ['html-svg']),
                             **frozen_constraints),
            feedback=feedback or run['frozen'].get('feedback', ''),
            base_source=base['source'] if base else None, reference=run['frozen'].get('reference'),
            observation=dict(frames=frames or [], audio_observed=False, motion_observed=False,
                limitations=['Only supplied sampled images were observed; complete motion and audio are unverified.']),
            comparison=comparison, assets=assets)
        if run['frozen'].get('constraints',{}).get('scene_space_capability') and mode in {'plan','create'}:
            from .scene_space_effects import effect_scene_offer
            document['constraints']['scene_space']=effect_scene_offer(run)
            if run['frozen'].get('use_wikiskill') is not False:
                from .scene_space_learning import transferable_scene_method
                methods=[]
                for version_id in run['frozen'].get('method_versions',[]):
                    for record in self.methods.get_scene_space(owner,version_id):
                        methods.append(transferable_scene_method({'scene_space':record['metadata']})['scene_space'])
                if methods:document['constraints']['scene_space_methods']=methods[:32]
        origin = run['frozen'].get('picture_origin')
        if origin is not None and run['frozen'].get('creation_mode') != 'fresh':
            from .picture_animation_bridge import validate_origin
            origin = validate_origin(origin)
            document['constraints']['picture_origin'] = {
                key: deepcopy(value) for key, value in origin.items() if key != 'source'}
            document['constraints']['picture_origin']['instruction'] = (
                'This is a source-preserving still-picture starting point for a NEW original animation, '
                'not a previous animation or motion-validated Skill. Keep the requested visual craft '
                'while authoring movement and rhythm for the current goal. Regenerate resource identities '
                'for this task; do not use old picture resource URLs as runtime dependencies. '
                'Do not claim that the still source proves motion, audio or transfer quality.')
            if base is None and mode in {'plan', 'create'}:
                document['base_source'] = deepcopy(origin['source'])
        if mode == 'plan':
            document['constraints']['result_requirements'] = dict(
                source=None, review=None, sound_events=[],
                instruction=(
                    'Plan criteria and reusable methods only. Return source=null, review=null and sound_events=[]. '
                    'Every criterion id must match [a-z][a-z0-9_.-]{0,99}: use lowercase ids such as c1, '
                    'never uppercase C1. If input criteria are supplied, copy them exactly without changes. '
                    'Each method applicability must be one nonempty string, not a list or object. '
                    'Each method criteria must be a nonempty exact subset of result.criteria: copy complete '
                    'criterion objects with identical ids, descriptions and optional fields; do not invent '
                    'method-only standards or rewrite them. Respect any method_application requirements.'))
        if mode == 'review':
            pair = {value for value in (comparison or {}).values() if type(value) is str}
            preferences = [row for row in self._feedback(owner, run['id'])
                if row.get('kind') == 'preference' and not row.get('withdrawn')
                and {row.get('left_candidate_id'), row.get('right_candidate_id')} == pair]
            document['constraints']['quality_review_policy'] = dict(
                schema='video.quality-review-direction.v1',
                developer_preferences=[{key: deepcopy(row[key]) for key in (
                    'id', 'kind', 'left_candidate_id', 'right_candidate_id', 'preference', 'withdrawn', 'comment')
                    if key in row} for row in preferences[-1:]],
                instruction=(
                    'Assess the actual expression requested in the goal and frozen criteria before general polish. '
                    'Composition, readability or technical success cannot compensate for a failed essential '
                    'material, style, visual mechanism or causal expression. Compare baseline and current '
                    'evidence under the same criteria. Preserve a usable baseline when explicit criteria fail; '
                    'do not recommend the new candidate merely because it is newer or more complex. '
                    'Separate visible facts, authored source intentions and untested hypotheses. A source '
                    'description does not prove its rendered effect. Sampled stills cannot prove continuous '
                    'motion, transitions, rhythm or audio: use insufficient_evidence where necessary, never '
                    'mark these passed from stills. Respect supplied explicit developer preferences without '
                    'inventing their reasons. State both improvements and losses, then give targeted next '
                    'guidance for the unmet goal rather than replacing successful craft with simple placeholders.'))
            document['constraints']['result_requirements'] = dict(
                methods=[], source=None, sound_events=[],
                instruction=(
                    'Review only the supplied actual media evidence and write the assessment in review. '
                    'The input methods are read-only context: do not copy, rewrite or create methods, '
                    'and do not repeat their historical parent_version_id or lineage in the result. '
                    'Return methods=[], source=null and sound_events=[].'))
        if run['frozen'].get('role') == 'transfer':
            # Transfer applies the exact supplied versions. Their historical
            # parent metadata is preserved, but is not an offered parent for
            # this task. The Agent needs that distinction beside the records.
            document['constraints']['method_application'] = dict(
                role='transfer',
                structural_changes=deepcopy(run['frozen']['structural_changes']),
                frozen_version_to_method={row['version_id']: row['method_id'] for row in selected},
                purpose='Apply the frozen methods to the changed structure; do not revise or replace them.',
                lineage_rule=(
                    'The supplied records retain historical parent_version_id metadata. Do not copy it into '
                    'a result. If returning lineage, set parent_version_id to a current version_id from '
                    'frozen_version_to_method and method_id to its mapped value. No other parent is supplied.'),
                result_requirements=dict(
                    plan=dict(methods=[], instruction='Keep the frozen criteria and return methods=[].'),
                    create=dict(instruction=(
                        'Return nonempty method content explaining the application, as required by the '
                        'create contract. It does not revise the frozen versions. Optional lineage must '
                        'use the current supplied version mapping. Create a new composition for the '
                        'structural changes, without copying a reference source.')),
                    review=dict(instruction='Assess the transferred application against the frozen criteria.')))
        if active_protocol is skill_protocol:
            transfer = run['frozen'].get('role') == 'transfer'
            if 'library_binding' in run['frozen']:
                snapshot = (None if transfer else self.library_adapter.laboratory_snapshot(
                    owner, run['frozen']['library_binding']))
            elif run['frozen'].get('collaboration') and mode == 'review':
                # Review the actual candidate and its criteria. An empty,
                # honestly captured snapshot avoids unrelated historical Wiki.
                snapshot = self.knowledge.snapshot(owner, page_ids=[], experience_ids=[])
            else:
                snapshot = (self.knowledge.get_snapshot(owner, run['frozen']['knowledge_snapshot_id'])
                            if mode == 'plan' else self._knowledge_snapshot(owner)) if not transfer else None
            document.update(knowledge_snapshot=snapshot, research_records=[] if transfer else [
                self.methods.get_research(owner, record_id)
                for record_id in run['frozen'].get('research_record_ids', [])])
            if transfer:
                document.update(reference=None, base_source=None)
                document['constraints']['method_application']['result_requirements'] = dict(
                    instruction='Apply the exact Skill versions; return methods=[] in every mode. '
                                'Create source for new content and structure without original reference material.')
            else:
                document['constraints'].setdefault('result_requirements', {})['skill_contract'] = (
                    'New methods must use video.animation-method.v2 with a complete standard Skill draft; '
                    'knowledge_proposals are evidence-bound Wiki updates, never Skill publication.')
                if 'library_binding' in run['frozen']:
                    document['constraints']['result_requirements']['knowledge_proposals'] = []
                    document['constraints']['result_requirements']['knowledge_instruction'] = (
                        'The snapshot is an immutable published release or empty knowledge policy. '
                        'Return knowledge_proposals=[]; preserve new reusable experience in your method draft. '
                        'Do not invent editable working-Wiki pages or evidence.')
        active_protocol.validate_input(document)
        return document

    def _advance_agent(self, owner, run, operation):
        collaborative = operation['input'].get('schema') == 'video.animation-collaboration-input.v1'
        if collaborative:
            from agent_service.profiles import video_animation_collaboration_contract as active_protocol
            gateway = self.collaboration_author
            _require(gateway is not None, 'animation_collaboration_not_admitted', 503)
        else:
            active_protocol = self._protocol(run)
            gateway = self.reviewer if operation['stage'] == 'review' else self.author
            if operation['stage'] == 'review' and self._effect_review_binding(run) is not None:
                from agent_service.profiles import video_picture_review_v2 as active_protocol
                gateway = self._effect_review_gateway(run)
        if operation['status'] == 'reserved':
            if operation['stage'] == 'review' and self._effect_review_binding(run) is not None:
                try:
                    self._effect_review_preflight(run, operation, gateway)
                except ServiceError as error:
                    if error.status >= 500:
                        raise
                    self.commissions.release_unsubmitted(owner, operation['id'], dict(reason=error.code))
                    self.commissions.stop(owner, run['id'], 'failed', dict(
                        message='分类评审输入验证失败；保留已有结果，不提交新的评审任务', code=error.code))
                    return
            files = {active_protocol.INPUT_NAME: canonical_bytes(operation['input'])}
            for frame in operation['input'].get('observation', {}).get('frames', []):
                path = self.root / 'frame-inputs' / frame['sha256']
                raw = path.read_bytes()
                _require(hashlib.sha256(raw).hexdigest() == frame['sha256'], 'animation_method_frame_changed', 409)
                files[frame['file_name']] = raw
            refs = gateway.upload(operation['input'], files if gateway.visual_input else None)
            if active_protocol is skill_protocol or collaborative:
                _require(type(refs) is list and all(type(row) is dict and {'path', 'sha256'} <= set(row)
                                                  for row in refs), 'animation_method_input_manifest_invalid')
                self.commissions.record_input_manifest(owner, operation['id'], sorted([
                    dict(path=row['path'], sha256=row['sha256']) for row in refs], key=lambda row: row['path']))
            admit = gateway.prepare_submission(operation['id'], operation['input'], run['deadline_at'], refs,
                **self._effect_classified_submission_options(run, operation))
            claim = self.commissions.claim(owner, operation['id'])
            if claim['submission_claimed']:
                try:
                    job = admit()
                except Exception:
                    self.commissions.mark_unknown(owner, operation['id'])
                    return
                self.commissions.mark_submitted(owner, operation['id'], job['job_id'])
                return
        if operation.get('job_id'):
            job = gateway.poll(operation['job_id'])
        else:
            try:
                job = gateway.lookup(operation['id'])
            except ServiceError as error:
                if error.status == 404:
                    return
                raise
            if job is None:
                return
            self.commissions.mark_submitted(owner, operation['id'], job['job_id'])
        if not self._effect_classified_job_limits(run, operation, job):
            return
        if job['state'] in {'queued', 'starting', 'running', 'cancelling'}:
            if run['cancel_requested'] or self.clock() >= run['deadline_at']:
                gateway.cancel(job['job_id'])
            return
        if job['state'] != 'completed':
            if job.get('stop_proven') is True:
                self.commissions.fail(owner, operation['id'], 'animation_method_agent_failed',
                                      cancelled=run['cancel_requested'])
            return
        try:
            result = gateway.collect(job['job_id'], operation['input_sha256'], operation['input'])
            if self._uses_manim(run):
                from .manim_method_resources import require_method_producer
                require_method_producer(result['methods'])
            if operation['stage'] == 'review':
                receipt = gateway.model_frame_receipt(job['job_id'])
                expected = {row['sha256'] for row in operation['input']['observation']['frames']}
                observed = {row.get('sha256') for row in receipt.get('images', [])
                            if row.get('model_request_completed') is True}
                _require(expected <= observed, 'animation_method_frame_transport_incomplete', 409)
            active_protocol.validate_result(result, operation['input_sha256'], operation['input'])
            if result.get('source') is not None and run['frozen'].get('constraints',{}).get('scene_space_capability'):
                from .scene_space_effects import effect_scene_metadata
                effect_scene_metadata(result,run,operation['input'])
            if run['frozen'].get('resource_planning'):
                if operation['input'].get('mode') == 'plan':
                    self._derive_capability_decision(run, operation, result)
                elif result.get('source') is not None:
                    decision = run['adopted_capabilities']
                    _require(result['source']['engine'] == decision['engine'],
                             'animation_method_capability_decision_changed', 409)
                    if not decision['uses_manim']:
                        _require(not any(row['runtime_requirements'].get('resource_authoring') or
                            row.get('skill_package', {}).get('runtime_requirements', {}).get('resource_authoring')
                            for row in result['methods']), 'animation_method_capability_decision_changed', 409)
            if 'capability_selection' in run['frozen'] and result.get('source') is not None:
                validate_source_selection(self._effective_selection(run), result['source'])
        except ServiceError as error:
            # Transport outages may be retried as reads of the same stopped
            # job. A definitive invalid artifact ends this operation.
            definitive = error.status < 500 or not error.code.startswith(('service_', 'http_'))
            if job.get('stop_proven') is True and (definitive or self.clock() >= run['deadline_at']):
                self.commissions.fail(owner, operation['id'], 'animation_method_result_invalid')
                return
            raise
        self.commissions.complete(owner, operation['id'], result)

    def _method_author_identity(self, owner, run, authored):
        """Keep method writes idempotent across verified creator checkpoints.

        Candidate/demo provenance still names this retry's operations. Only
        the method request uses the original creator identity, so an already
        persisted Skill package is not interpreted as a new method version.
        """
        seen = set()
        while authored.get('reused_from'):
            origin = authored['reused_from']
            _require(authored['id'] not in seen
                and authored['commission_id'] == run['id']
                and authored['stage'] == 'creator' and authored['status'] == 'completed'
                and origin['commission_id'] == run['frozen'].get('retry_of_commission_id'),
                'animation_method_retry_method_author_changed', 409)
            seen.add(authored['id'])
            previous = self.commissions.get(owner, origin['commission_id'])
            source = self.commissions.operation(owner, origin['operation_id'])
            _require(previous['state'] in {'failed', 'cancelled'}
                and source['commission_id'] == previous['id']
                and source['stage'] == 'creator' and source['status'] == 'completed'
                and all(source.get(key) == authored.get(key) for key in
                    ('idempotency_key', 'input_sha256', 'result_sha256', 'input', 'result', 'job_id')),
                'animation_method_retry_method_author_changed', 409)
            run, authored = previous, source
        return authored['id']

    def _materialize(self, owner, run, render):
        authored = next(op for op in run['operations'] if op['id'] == render['input']['author_operation_id'])
        result = authored['result']
        from .scene_space_effects import effect_scene_metadata,portable_method
        scene_metadata=effect_scene_metadata(result,run,authored['input'])
        if scene_metadata is None and run['frozen'].get('constraints',{}).get('scene_space_capability'):
            origin=run['frozen'].get('picture_origin',{}).get('method') or {}
            candidate=origin.get('scene_space')
            if type(candidate) is dict and candidate.get('schema')=='video.scene-space-method.v1':
                from .scene_space_learning import validate_scene_method
                scene_metadata=validate_scene_method(candidate)
        if 'capability_selection' in run['frozen']:
            validate_rendered_selection(self._effective_selection(run), result['source'],
                                        render['result'], render.get('artifact_operation_id', render['id']))
        if run['frozen'].get('resource_planning'):
            observed_manim = any(row['id'] == 'manim' for row in observed_authoring_tools(
                render['result'].get('runtime', {})))
            _require(observed_manim == self._uses_manim(run),
                     'animation_method_capability_resource_mismatch', 409)
        versions = []
        if run['frozen'].get('role') == 'transfer':
            versions = run['frozen'].get('method_versions', [])
        else:
            method_author_id = self._method_author_identity(owner, run, authored)
            for index, proposal in enumerate(result['methods']):
                parent = (next((row for row in authored['input']['methods']
                    if row.get('problem') == proposal['problem'] and row.get('version_id')), None)
                    if self._protocol(run) is protocol else None)
                body = portable_method(proposal,enabled=bool(run['frozen'].get('constraints',{}).get('scene_space_capability')))
                if body.get('parent_version_id'):
                    parent = self.methods.get_method(owner, body['parent_version_id'])
                if parent and parent.get('owner') != owner:
                    # Shared published knowledge may seed a new developer's
                    # method, but does not grant edit ownership of its lineage.
                    parent = None
                    body.pop('method_id', None)
                    body.pop('parent_version_id', None)
                if parent:
                    body.update(method_id=parent['method_id'], parent_version_id=parent['version_id'])
                method = self.methods.create_method(owner, body, method_author_id + ':method:' + str(index))
                versions.append(method['id'])
        _require(bool(versions), 'animation_method_creator_no_method', 409)
        outcome = render['result']
        if scene_metadata is not None:
            for version_id in versions:
                self.methods.attach_scene_space(owner,version_id,scene_metadata,
                    render['id']+':scene-space:'+version_id,evidence=dict(
                        commission_id=run['id'],author_operation_id=authored['id'],
                        render_operation_id=render['id'],source_sha256=source_sha256(result['source']),
                        input_sha256=authored['input_sha256'],technical=deepcopy(outcome.get('technical')),
                        runtime=deepcopy(outcome.get('runtime',{}).get('scene_space')),
                        continuous_motion_observed=False,audio_observed=False))
        _require(outcome.get('technical', {}).get('status') == 'passed',
                 'animation_method_render_failed', 409)
        candidate = self.commissions.add_candidate(owner, run['id'], dict(
            operation_id=render['id'], media=outcome['media'], source=result['source'],
            strategy=result['source']['strategy'], method_versions=versions,
            criteria_version=digest(result['criteria']), technical=outcome['technical'],
            provenance=dict(author_operation_id=authored['id'], input_sha256=authored['input_sha256'])),
            render['id'] + ':candidate')
        demo = self.methods.add_demonstration(owner, dict(title=run['title'], goal=run['frozen']['goal'],
            media=outcome['media'], source=result['source'], runtime=outcome['runtime'],
            technical=outcome['technical'], provenance=dict(commission_id=run['id'], candidate_id=candidate['id'],
                creator_operation_id=authored['id'], render_operation_id=render['id'],
                input_sha256=authored['input_sha256'])),
            render['id'] + ':demonstration')
        return candidate, demo

    def _scene_render_arguments(self,run,document,operation_id):
        if 'scene_space' not in document:return {}
        expected=run['frozen'].get('constraints',{}).get('scene_space_capability')
        provider=getattr(self.renderer,'scene_space_capabilities',None)
        _require(expected is not None and callable(provider) and provider()==expected,
            'scene_space_render_frozen_runtime_changed',409)
        arguments=dict(scene_space=document['scene_space'])
        prepare=getattr(self.renderer,'validate_scene_request',None)
        if callable(prepare):
            binding=prepare(operation_id,document['source'],document['document'],
                scene_space=document['scene_space'],deadline_at=run['deadline_at'])
            arguments['expected_request_sha256']=binding['request_sha256']
        return arguments

    def _execute_method_render(self,owner,run,operation,arguments):
        try:
            return self.renderer.render(operation['id'],operation['input']['source'],
                operation['input']['document'],sound_events=operation['input']['sound_events'],
                deadline_at=run['deadline_at'],**arguments)
        except ServiceError as error:
            if ('scene_space' in operation['input'] and getattr(error,'not_dispatched',False) is True
                    and getattr(error,'stop_proven',False) is True):
                self.commissions.fail(owner,operation['id'],error.code)
                return None
            raise

    def _record_review(self, owner, run, operation):
        if self._effect_review_binding(run) is not None:
            return self._record_classified_effect_review(owner, run, operation)
        result, document = operation['result'], operation['input']
        opinion = result['review']
        current_id = document['comparison']['candidate_id']
        prior_id = document['comparison'].get('baseline_candidate_id')
        policy_input = document.get('constraints', {}).get('quality_review_policy', {})
        selection = select_review(criteria=document['criteria'], opinion=opinion,
            comparison=document['comparison'], candidates=_candidates(run),
            current_best_id=run['best_candidate_id'], observation=document.get('observation'),
            developer_preferences=policy_input.get('developer_preferences', []))
        review_body = dict(
            operation_id=operation['id'], candidate_id=current_id, outcome=selection['outcome'],
            best_candidate_id=selection['selected_candidate_id'], remaining_issues=opinion['remaining_issues'],
            compared_candidate_id=prior_id, criteria_version=digest(result['criteria']),
            criteria_results=opinion['criteria_results'], evidence=dict(input_sha256=operation['input_sha256'],
                selection_policy=selection),
            limitations=opinion['limitations'], summary=opinion['summary'])
        demo = self._demonstrations(owner, run['id'])[current_id]
        candidate = next(row for row in run['candidates'] if row['id'] == current_id)
        statuses = {row['criterion_id']: row['status'] for row in opinion['criteria_results']}
        for criterion_id in selection['unobserved_criterion_ids']:
            if statuses.get(criterion_id) != 'failed':
                statuses[criterion_id] = 'insufficient_evidence'
        observed_criteria = {row['id']: row for row in document['criteria']}
        for version_id in candidate['method_versions']:
            version = self.methods.get_method(owner, version_id)
            _require(all(observed_criteria.get(row['id']) == row for row in version['criteria']),
                     'animation_method_review_criteria_mismatch', 409)
            evaluations = [statuses.get(row['id'], 'insufficient_evidence') for row in version['criteria']]
            outcome = ('failed' if 'failed' in evaluations else
                       'passed' if all(value == 'passed' for value in evaluations) else 'incomplete')
            data = dict(method_version_id=version_id, demonstration_version_id=demo['id'],
                role=run['frozen'].get('role', 'reference'), criteria_sha256=version['criteria_sha256'],
                outcome=outcome, evidence=dict(media_sha256=demo['media']['sha256'],
                    review_operation_id=operation['id'], criteria_results=opinion['criteria_results'],
                    limitations=opinion['limitations'], selection_policy=selection))
            if data['role'] == 'transfer':
                data.update(baseline_demonstration_version_id=run['frozen']['baseline_demonstration_id'],
                            structural_changes=run['frozen']['structural_changes'])
                if version['schema'] == METHOD_SCHEMA:
                    authored = self.commissions.operation(owner, demo['provenance']['creator_operation_id'])
                    _require(authored['status'] == 'completed' and authored['stage'] == 'creator',
                             'animation_method_input_manifest_required', 409)
                    manifest = authored.get('input_resource_manifest')
                    _require(type(manifest) is list and authored.get('input_resource_manifest_sha256') == digest(manifest),
                             'animation_method_input_manifest_required', 409)
                    data.update(content_changes=run['frozen']['content_changes'], reference_removal=dict(
                        schema=REMOVAL_SCHEMA, input_document=deepcopy(authored['input']),
                        resource_manifest=manifest,
                        input_receipt=dict(source='verified_host_frozen_input', operation_id=authored['id'],
                            input_sha256=authored['input_sha256'], resource_manifest_sha256=digest(manifest),
                            method_content_sha256=version['content_sha256'],
                            skill_package_sha256=version['skill_package']['sha256']),
                        run_result=dict(source='verified_host_render_record', creator_operation_id=authored['id'],
                            render_operation_id=demo['provenance']['render_operation_id'],
                            demonstration_version_id=demo['id'], media_sha256=demo['media']['sha256'],
                            technical_evidence_sha256=demo['technical']['evidence_sha256'])))
            self.methods.link_validation(owner, data, operation['id'] + ':validation:' + version_id)
        return self.commissions.record_review(owner, run['id'], review_body, operation['id'] + ':review')

    def advance(self, owner, commission_id):
        with _run_lock(self.root / 'locks' / (commission_id + '.lock')) as acquired:
            if not acquired:
                return
            run = self.commissions.get(owner, commission_id)
            if run['state'] in TERMINAL:
                return
            if run['frozen'].get('retry_of_commission_id') and not run.get('retry_creator_checkpoint'):
                # Recover the create/checkpoint crash gap before reserving any
                # new work. The original stopped task remains immutable.
                self.commissions.restore_completed_creators(owner, commission_id,
                    run['frozen']['retry_of_commission_id'])
                return
            _require(self._protocol(run) is self.author_protocol,
                     'animation_method_historical_gateway_required', 409)
            if (run['frozen'].get('resource_planning') and not run.get('adopted_capabilities')
                    and not run['cancel_requested'] and self.clock() < run['deadline_at']):
                plan = next((op for op in run['operations'] if op['input'].get('mode') == 'plan'
                             and op['status'] == 'completed'), None)
                if plan is not None:
                    self.commissions.freeze_capability_decision(owner, commission_id,
                        self._derive_capability_decision(run, plan))
                    return
            if self._uses_manim(run):
                _require(self.manim is not None, 'manim_method_resource_coordinator_required', 503)
                projected = self._manim_projection(owner, run)
                allocated = (self.resource_stage.allocated_round(owner, commission_id)
                             if run['frozen'].get('resource_planning') else None)
                if run['frozen'].get('retry_of_commission_id') and not run['cancel_requested']:
                    previous = self.commissions.get(owner, run['frozen']['retry_of_commission_id'])
                    original = self._manim_projection(owner, previous)
                    if (original.get('resource_round', 0) == projected.get('resource_round', 0)
                            and self.resource_stage.allocated_round(owner, commission_id) is None):
                        self.resource_stage.restore_retry(owner, projected, original)
                if (run['frozen'].get('resource_planning') and not run['cancel_requested']
                        and self.clock() < run['deadline_at']
                        and (allocated is None or allocated < projected.get('resource_round', 0))):
                    # Admission belongs before a new durable resource intent.
                    # Once allocated, reconcile its original task and stop
                    # proof even if later configuration no longer admits it.
                    try:
                        self.manim.preflight(run['deadline_at'])
                        if callable(getattr(self.manim, 'recipe_guidance', None)):
                            self.manim.recipe_guidance(owner, projected['frozen'])
                    except ServiceError as error:
                        if error.status >= 500:
                            raise
                        self.commissions.stop(owner, commission_id, 'failed',
                            dict(message='Manim资源创作已停止；保留原操作和证据，不自动重试', code=error.code))
                        return
                resource_state = self.resource_stage.advance(owner, projected,
                    cancelled=run['cancel_requested'] or self.clock() >= run['deadline_at'])
                if resource_state in {'failed', 'cancelled'}:
                    self.commissions.stop(owner, commission_id, resource_state,
                        'Manim资源创作已停止；保留原操作和证据，不自动重试')
                    return
                if resource_state != 'completed':
                    return
            self._sync_knowledge(owner, run)
            active_operations = [op for op in run['operations']
                if op['status'] not in {'completed', 'failed', 'cancelled', 'released'}]
            if (run['frozen'].get('collaboration') and len(active_operations) == 1
                    and active_operations[0]['input'].get('phase') == 'component'
                    and not run['cancel_requested'] and self.clock() < run['deadline_at']
                    and not any(op['status'] in {'failed', 'cancelled'} for op in run['operations'])):
                from .animation_collaboration import next_action
                if next_action(run, now=self.clock())['kind'] == 'reserve':
                    self._schedule_collaboration(owner, run)
                    run = self.commissions.get(owner, commission_id)
                    active_operations = [op for op in run['operations'] if op['status'] not in OP_TERMINAL]
            if run['frozen'].get('collaboration') and len(active_operations) > 1:
                failed = any(op['status'] in {'failed', 'cancelled'} for op in run['operations'])
                errors = []
                for operation in active_operations:
                    stopping = failed or run['cancel_requested'] or self.clock() >= run['deadline_at']
                    try:
                        if operation['status'] == 'reserved' and stopping:
                            self.commissions.release_unsubmitted(owner, operation['id'], {'reason': 'stopped_before_claim'})
                        else:
                            self._advance_agent(owner, dict(run, cancel_requested=stopping), operation)
                    except (ServiceError, OSError) as error:
                        # A transport outage on one original job must not
                        # prevent a sibling from receiving its cancellation.
                        errors.append(error)
                    current = self.commissions.get(owner, commission_id)
                    failed |= any(op['status'] in {'failed', 'cancelled'} for op in current['operations'])
                if errors:
                    raise errors[0]
                return
            active = active_operations[0] if active_operations else None
            if active:
                stopping = (run['cancel_requested'] or self.clock() >= run['deadline_at']
                    or (run['frozen'].get('collaboration') and any(
                        op['status'] in {'failed', 'cancelled'} for op in run['operations'])))
                if active['status'] == 'reserved' and stopping:
                    self.commissions.release_unsubmitted(owner, active['id'], {'reason': 'stopped_before_claim'})
                    return
                if active['stage'] == 'render':
                    if self._reuse_retry_render(owner, run, active):
                        return
                    if active['status'] == 'reserved':
                        if run['deadline_at'] - self.clock() < 30:
                            return
                        try:scene_arguments=self._scene_render_arguments(run,active['input'],active['id'])
                        except ServiceError as error:
                            if error.status>=500:raise
                            self.commissions.fail(owner,active['id'],error.code);return
                        claim = self.commissions.claim(owner, active['id'])
                        if claim['submission_claimed']:
                            result = self._execute_method_render(owner,run,active,scene_arguments)
                        else:
                            result = self.renderer.lookup(active['id'])
                    else:
                        try:
                            result = self.renderer.lookup(active['id'])
                        except ServiceError as error:
                            if error.status != 404:
                                raise
                            # The adapter commits its own unique claim before
                            # any container launch. Its render entry reconciles
                            # an existing claim and refuses an orphan directory.
                            # Recover the outer-claim/inner-claim crash gap with
                            # the exact same operation, never a new render ID.
                            if run['deadline_at'] - self.clock() < 30 or run['cancel_requested']:
                                self.commissions.fail(owner, active['id'],
                                    'animation_method_render_not_started', cancelled=run['cancel_requested'])
                                return
                            try:scene_arguments=self._scene_render_arguments(run,active['input'],active['id'])
                            except ServiceError as error:
                                if error.status>=500:raise
                                self.commissions.fail(owner,active['id'],error.code);return
                            result = self._execute_method_render(owner,run,active,scene_arguments)
                    if result is not None:
                        if ('scene_space' in active['input'] and (result.get('status')=='unknown'
                                or result.get('stop_proven') is not True)):
                            return
                        self.commissions.complete(owner, active['id'], result)
                else:
                    self._advance_agent(owner, dict(run, cancel_requested=bool(stopping)), active)
                return
            if run['cancel_requested']:
                self.commissions.stop(owner, commission_id, 'cancelled', '开发者取消了本次实验')
                return
            if any(op['status'] in {'failed', 'cancelled'} for op in run['operations']):
                self.commissions.stop(owner, commission_id, 'failed', '执行失败；保留已有结果与原任务，不自动重试')
                return
            for render in [op for op in run['operations'] if op['stage'] == 'render' and op['status'] == 'completed']:
                if render['result'].get('technical', {}).get('status') != 'passed':
                    self.commissions.stop(owner, commission_id, 'failed', dict(message=_RENDER_FAILURE_MESSAGE,
                        code=public_render_failure_code(render['result'].get('error'))))
                    return
                candidate = next((row for row in run['candidates'] if row['operation_id'] == render['id']), None)
                if candidate is None or candidate['id'] not in self._demonstrations(owner, run['id']):
                    try:
                        self._materialize(owner, run, render)
                    except ServiceError as error:
                        if error.status >= 500:
                            raise
                        self.commissions.stop(owner, commission_id, 'failed',
                            dict(message='制作结果未通过入库验证；保留原操作，不自动重跑', code=error.code))
                    return
            for review in [op for op in run['operations'] if op['stage'] == 'review' and op['status'] == 'completed']:
                if not any(row['operation_id'] == review['id'] for row in run['reviews']):
                    try:
                        self._record_review(owner, run, review)
                    except ServiceError as error:
                        if error.status >= 500:
                            raise
                        self.commissions.stop(owner, commission_id, 'failed',
                            dict(message='评审证据未通过关联验证；保留原操作，不自动重跑', code=error.code))
                    return
            if self._effect_classified_stop(owner, run):
                return
            if run['reviews'] and run['reviews'][-1]['outcome'] == 'goal_met':
                self.commissions.stop(owner, commission_id, 'goal_met', '本次可观察标准已达成，等待开发者验收')
                return
            if self.clock() >= run['deadline_at']:
                self.commissions.stop(owner, commission_id, 'budget_exhausted', '本次执行时间已用完')
                return
            if run['deadline_at'] - self.clock() < 30:
                # A new render needs a minimum launch/cleanup window. Do not
                # spend another model call when its output cannot be rendered.
                return
            if run['frozen'].get('collaboration'):
                # Final rendering and sampled review below share their existing
                # artifact/acceptance path. Subtasks themselves never become
                # library candidates or consume finished-video polish rounds.
                from .animation_collaboration import next_action
                action = next_action(run, now=self.clock())
                if action['kind'] != 'complete':
                    self._schedule_collaboration(owner, run)
                    return
            plans = [op for op in run['operations'] if op['input'].get('mode') == 'plan']
            if not plans and not run['frozen'].get('collaboration'):
                self.commissions.reserve(owner, commission_id, 'creator', self._document(owner, run, 'plan'), 'plan')
                return
            creates = [op for op in run['operations'] if op['stage'] == 'creator'
                       and (op['input'].get('mode') == 'create' or op['input'].get('phase') == 'integrate')]
            for authored in creates:
                if self._retry_creator_already_reviewed(owner, run, authored):
                    continue
                if not any(op['stage'] == 'render' and op['input'].get('author_operation_id') == authored['id']
                           for op in run['operations']):
                    if run['remaining']['render'] <= 0:
                        self.commissions.stop(owner, commission_id, 'budget_exhausted', '本次画面渲染预算已用完')
                        return
                    from .animation_renderer_rebind import render_input
                    render_document=render_input(run,authored)
                    if run['frozen'].get('constraints',{}).get('scene_space_capability'):
                        from .scene_space_effects import effect_scene_metadata
                        metadata=effect_scene_metadata(authored['result'],run,authored['input'])
                        if metadata and metadata['plan'] is not None:
                            render_document['scene_space']=metadata['plan']
                    try:
                        render_document = self._effect_lossless_render(run, render_document)
                    except ServiceError as error:
                        if error.status >= 500:
                            raise
                        self.commissions.stop(owner, commission_id, 'failed', dict(
                            message='当前结果不满足所选无损场景协议；保留原创作，不启动渲染', code=error.code))
                        return
                    self.commissions.reserve(owner, commission_id, 'render', render_document,
                        'render-' + authored['id'])
                    return
            for candidate in run['candidates']:
                if any(row['candidate_id'] == candidate['id'] for row in run['reviews']):
                    continue
                if run['remaining']['review'] <= 0:
                    self.commissions.stop(owner, commission_id, 'budget_exhausted', '本次评审预算已用完')
                    return
                if self._effect_review_binding(run) is not None:
                    try:
                        document = self._effect_review_document(owner, run, candidate)
                    except ServiceError as error:
                        if error.status >= 500:
                            raise
                        self.commissions.stop(owner, commission_id, 'failed', dict(
                            message='当前无损画面证据不满足所选评审协议；保留原渲染，不提交评审任务', code=error.code))
                        return
                    self.commissions.reserve(owner, commission_id, 'review', document,
                        'review-' + candidate['id'])
                    return
                best = next((row for row in _candidates(run) if row['id'] == run['best_candidate_id']
                             and row['id'] != candidate['id']), None)
                entries, frame_files = [], {}
                for target in ([best, candidate] if best else [candidate]):
                    for index, frame in enumerate(self.renderer.frames(
                            self._artifact_operation_id(owner, target['operation_id']), max_frames=12)):
                        name = 'frame-' + ('baseline' if target is best else 'candidate') + '-' + str(index) + '.jpg'
                        raw = frame['raw']
                        entries.append(dict(frame_id=('b' if target is best else 'c') + str(index),
                            file_name=name, sha256=hashlib.sha256(raw).hexdigest(),
                            timestamp_seconds=frame['timestamp_seconds'], candidate_id=target['id'],
                            media_sha256=target['media']['sha256']))
                        frame_files[name] = raw
                comparison = dict(candidate_id=candidate['id'], baseline_candidate_id=best['id'] if best else None)
                document = self._document(owner, run, 'review', base=candidate, frames=entries, comparison=comparison)
                directory = self.root / 'frame-inputs'
                directory.mkdir(parents=True, exist_ok=True)
                for name, raw in frame_files.items():
                    path = directory / hashlib.sha256(raw).hexdigest()
                    if path.exists():
                        _require(path.read_bytes() == raw, 'animation_method_frame_changed', 409)
                    else:
                        path.write_bytes(raw)
                self.commissions.reserve(owner, commission_id, 'review', document,
                                         'review-' + candidate['id'])
                return
            if len(run['reviews']) >= 2 and all(row['outcome'] in {'equal', 'regressed', 'inconclusive'}
                    for row in run['reviews'][-2:]) and len({digest(row['strategy']) for row in run['candidates'][-2:]}) >= 2:
                self.commissions.stop(owner, commission_id, 'no_progress',
                    '多种尝试尚无可证明的改善；保留最佳结果和待解决问题，可继续发起实验')
                return
            if run['remaining']['creator'] <= 0 or run['remaining']['render'] <= 0 or run['remaining']['review'] <= 0:
                self.commissions.stop(owner, commission_id, 'budget_exhausted', '本次实验预算已用完，可从选定版本继续')
                return
            base = next((row for row in _candidates(run) if row['id'] == run['best_candidate_id']), None)
            base = base or run['frozen'].get('base_candidate')
            last_review = next((op['result']['review'] for op in reversed(run['operations'])
                                if op['stage'] == 'review' and op['status'] == 'completed'), None)
            feedback = (self._classified_effect_feedback(next(op for op in reversed(run['operations'])
                if op['stage'] == 'review' and op['status'] == 'completed'))
                if last_review and self._effect_review_binding(run) is not None else
                last_review['next_guidance'] if last_review else run['frozen'].get('feedback', ''))
            self.commissions.reserve(owner, commission_id, 'creator',
                self._document(owner, run, 'create', base=base, feedback=feedback),
                'create-' + str(len(creates)))
