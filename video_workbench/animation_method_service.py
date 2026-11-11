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

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_method as protocol
from .animation_creation_service import _run_lock
from .animation_method_store import AnimationMethodStore, _structural_changes
from .animation_method_commissions import AnimationMethodCommissionStore, TERMINAL


def _require(value, code='animation_method_request_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _public_url(commission_id, candidate_id, kind):
    return '/api/method-lab/commissions/' + commission_id + '/candidates/' + candidate_id + '/' + kind


def _candidates(run):
    rows = list(run['candidates'])
    baseline = run['frozen'].get('base_candidate')
    if baseline and not any(row['id'] == baseline['id'] for row in rows):
        rows.insert(0, baseline)
    return rows


def _finite_number(value):
    # Do not coerce unbounded JSON integers to float before range validation.
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _effect_views(runs, demonstrations):
    """Group only owner-scoped, explicit frozen lineage; never infer a method.

    A missing/foreign ancestor or a cycle makes every affected run independent.
    This is a read projection: neither the recorded choices nor history change.
    """
    by_id = {run['id']: run for run in runs}
    demos = {row['id']: row for row in demonstrations}
    parents, kinds, direct_issues = {}, {}, {}
    for run in runs:
        identity, frozen = run['id'], run['frozen']
        base, baseline = frozen.get('base_commission_id'), frozen.get('baseline_demonstration_id')
        transfer = frozen.get('role', 'reference') == 'transfer'
        parent, issue = None, None
        kinds[identity] = 'continuation' if base else 'transfer' if transfer else 'original'
        if base and (transfer or baseline):
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
        if parent is not None and (type(parent) is not str or parent not in by_id):
            issue = 'ancestor_missing_or_not_owned'
        parents[identity], direct_issues[identity] = parent, issue

    roots, relation_issues = {}, {}
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
            root, issue = roots[current], relation_issues[current]
        for identity in path:
            roots[identity], relation_issues[identity] = root, issue

    groups = {}
    for run in runs:
        groups.setdefault(roots[run['id']] or run['id'], []).append(run)
    effects, membership = [], {}
    for effect_id, members in groups.items():
        original = by_id[effect_id]
        representative = original
        children = {}
        for run in members:
            if kinds[run['id']] == 'continuation' and not relation_issues[run['id']]:
                children.setdefault(parents[run['id']], []).append(run)
        current = original
        # A linear continuation can carry its recorded best forward, including
        # a deliberately retained baseline. Transfers are separate validation,
        # and competing branches supply no global comparison between siblings.
        while children.get(current['id']):
            next_runs = children[current['id']]
            if len(next_runs) != 1:
                representative = original
                break
            current = next_runs[0]
            if current['state'] == 'completed' and any(
                    item['id'] == current['best_candidate_id'] for item in _candidates(current)):
                representative = current
        candidate = next((item for item in _candidates(representative)
                          if item['id'] == representative['best_candidate_id']), None)
        best = (dict(id=candidate['id'], media_url=_public_url(
            representative['id'], candidate['id'], 'media')) if candidate else None)
        summaries = [dict(id=run['id'], title=run['title'],
            role=run['frozen'].get('role', 'reference'), kind=kinds[run['id']], state=run['state'],
            best_candidate_id=run['best_candidate_id'],
            base_commission_id=run['frozen'].get('base_commission_id'),
            stop_reason=run['stop_reason'], created_at=run['created_at']) for run in reversed(members)]
        issues = sorted({relation_issues[run['id']] for run in members if relation_issues[run['id']]})
        active = next((run for run in members if run['state'] not in TERMINAL), None)
        effect = dict(id=effect_id, effect_id=effect_id, title=original['title'],
            goal=original['frozen']['goal'], state=active['state'] if active else representative['state'],
            request_id=representative['request_id'], open_commission_id=representative['id'],
            media_url=best['media_url'] if best else None, best_candidate=best,
            commissions=summaries, relationship_status='needs_review' if issues else 'verified',
            relationship_issues=issues)
        effects.append(effect)
        for run in members:
            membership[run['id']] = effect
    return effects, membership


class AnimationMethodService:
    def __init__(self, root, *, author_gateway, review_gateway, renderer, clock=time.time):
        self.root = Path(root).absolute()
        _require(not any(path.is_symlink() for path in (self.root, *self.root.parents)))
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'locks').mkdir(exist_ok=True)
        self.methods = AnimationMethodStore(self.root / 'methods.sqlite3')
        self.commissions = AnimationMethodCommissionStore(self.root / 'commissions.sqlite3', clock=clock)
        self.author, self.reviewer, self.renderer = author_gateway, review_gateway, renderer
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
            db.commit()

    def pending(self, limit=100):
        return self.commissions.pending(limit)

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

    def catalogue(self, owner):
        rows = []
        runs = self.commissions.list_all(owner)
        effects, _ = _effect_views(runs, self.methods.list_demonstrations(owner))
        for run in runs:
            candidate = next((item for item in _candidates(run) if item['id'] == run['best_candidate_id']), None)
            best = (dict(id=candidate['id'], media_url=_public_url(run['id'], candidate['id'], 'media'))
                    if candidate else None)
            row = dict(id=run['id'], title=run['title'], goal=run['frozen']['goal'],
                       request_id=run['request_id'], state=run['state'], best_candidate_id=run['best_candidate_id'],
                       best_candidate=best, stop_reason=run['stop_reason'])
            rows.append(row)
        return dict(schema='video.animation-method-lab.v1', effects=effects, commissions=rows,
                    availability=dict(enabled=self.author is not None and self.reviewer is not None
                        and self.renderer is not None, reason=None))

    def get(self, owner, commission_id):
        run = self.commissions.get(owner, commission_id)
        all_demos = self.methods.list_demonstrations(owner)
        _, membership = _effect_views(self.commissions.list_all(owner), all_demos)
        effect = membership[commission_id]
        demos = {row['provenance'].get('candidate_id'): row for row in all_demos}
        methods, candidates = {}, []
        for candidate in _candidates(run):
            row = {k: deepcopy(v) for k, v in candidate.items() if k != 'source'}
            row.update(title=candidate.get('title') or '候选 ' + str(len(candidates) + 1),
                media_url=_public_url(commission_id, candidate['id'], 'media'),
                source_url=_public_url(commission_id, candidate['id'], 'source'),
                technical_status=candidate.get('technical', {}).get('status', 'unverified'),
                method_version_ids=candidate.get('method_versions', []),
                demonstration_id=demos.get(candidate['id'], {}).get('id'))
            candidates.append(row)
            for version_id in candidate.get('method_versions', []):
                methods[version_id] = self.methods.get_method(owner, version_id)
        validations = [item for version_id in methods
                       for item in self.methods.list_validations(owner, method_version_id=version_id)]
        best = next((row for row in candidates if row['id'] == run['best_candidate_id']), None)
        selected_method_ids = best['method_version_ids'] if best else []
        blockers = []
        for version_id in selected_method_ids:
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
        transfers = []
        for validation in validations:
            if validation['role'] != 'transfer':
                continue
            demo = demo_ids[validation['demonstration_version_id']]
            origin = demo['provenance']
            transfers.append(dict(validation, title=demo['title'], goal=demo['goal'],
                demonstration_id=demo['id'],
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
        return dict(schema='video.animation-method-lab-detail.v1', id=run['id'], request_id=run['request_id'],
            effect={key: deepcopy(effect[key]) for key in (
                'id', 'title', 'open_commission_id', 'commissions', 'relationship_status', 'relationship_issues')},
            title=run['title'], goal=run['frozen']['goal'], state=run['state'],
            budget=run['budget'], usage=run['usage'], remaining=run['remaining'],
            criteria=standards, candidates=candidates, best_candidate_id=run['best_candidate_id'],
            baseline_candidate=next((row for row in candidates if row['id'] ==
                run['frozen'].get('base_candidate_id')), None),
            methods=list(methods.values()), publication_method_version_ids=selected_method_ids,
            requires_audio_verification=any(row['media'].get('audio_sha256') for row in relevant_demos),
            transfers=transfers,
            validations=validations, feedback=self._feedback(owner, commission_id),
            stop_reason=run['stop_reason'], remaining_issues=latest_review.get('remaining_issues', []),
            summary=summary, error=error, publication_ready=not blockers, publishable=not blockers,
            publication_blockers=sorted(set(blockers)), reviews=run['reviews'],
            execution=[{k: op.get(k) for k in ('id', 'stage', 'status', 'job_id', 'error')}
                       for op in run['operations']])

    def start(self, owner, body):
        fields = {'request_id', 'title', 'goal', 'reference', 'duration_seconds', 'budget',
                  'base_commission_id', 'base_candidate_id', 'feedback', 'criteria', 'method_versions',
                  'role', 'baseline_demonstration_id', 'structural_changes'}
        _require(type(body) is dict and {'request_id', 'goal', 'budget'} <= set(body)
                 and set(body) <= fields)
        request_id = body['request_id']
        data = {key: deepcopy(value) for key, value in body.items() if key != 'request_id' and value is not None}
        budget = data['budget']
        _require(type(budget) is dict and set(budget) == {'max_rounds', 'max_seconds'})
        rounds, seconds = budget['max_rounds'], budget['max_seconds']
        _require(type(rounds) is int and 1 <= rounds <= 8 and type(seconds) is int and 60 <= seconds <= 3600)
        data['budget'] = dict(creator=rounds + 1, review=rounds, render=rounds, seconds=seconds)
        duration = data.setdefault('duration_seconds', 18)
        _require(type(duration) in (int, float) and 1 <= duration <= 60
                 and duration * 24 == int(duration * 24))
        if data.get('criteria'):
            protocol.criteria(data['criteria'])
        _require(data.get('role', 'reference') in {'reference', 'transfer'})
        if data.get('role') == 'transfer':
            _require(not data.get('base_commission_id') and not data.get('base_candidate_id'),
                     'animation_method_transfer_identity_invalid', 409)
        if data.get('base_commission_id'):
            previous = self.commissions.get(owner, data['base_commission_id'])
            baseline_id = data.get('base_candidate_id') or previous['best_candidate_id']
            baseline = next((row for row in _candidates(previous) if row['id'] == baseline_id), None)
            _require(baseline is not None, 'animation_method_baseline_missing', 409)
            data.setdefault('method_versions', baseline.get('method_versions', []))
            if not data.get('criteria'):
                old = self.get(owner, previous['id'])
                data['criteria'] = old['criteria']
        version_ids = data.get('method_versions', [])
        _require(type(version_ids) is list and len(version_ids) <= 32
                 and all(type(item) is str for item in version_ids)
                 and len(set(version_ids)) == len(version_ids))
        for version_id in version_ids:
            version = self.methods.get_method(owner, version_id)
            _require(version['runtime_requirements'].get('engine') == 'html-svg',
                     'animation_method_runtime_not_admitted', 409)
        if data.get('role') == 'transfer':
            _require(bool(version_ids), 'animation_method_transfer_methods_required', 409)
            standards = self._method_criteria(owner, version_ids)
            _require(not data.get('criteria') or data['criteria'] == standards,
                     'animation_method_transfer_criteria_mismatch', 409)
            data['criteria'] = standards
            _structural_changes(data.get('structural_changes'))
            baseline = self.methods.get_demonstration(owner, data.get('baseline_demonstration_id'))
            for version_id in data.get('method_versions', []):
                _require(any(row['role'] == 'reference' and row['demonstration_version_id'] == baseline['id']
                    and row['outcome'] != 'failed' for row in self.methods.list_validations(
                        owner, method_version_id=version_id)), 'animation_method_transfer_baseline_required', 409)
            change = data['structural_changes'].get('duration')
            _require(not change or (change['before'] == baseline['media']['duration_seconds']
                     and change['after'] == duration), 'animation_method_transfer_duration_mismatch', 409)
        # Preflight is a read, not a paid task or budget reservation.
        _require(self.author is not None and self.reviewer is not None and self.renderer is not None,
                 'animation_method_service_unavailable', 503)
        for gateway in (self.author, self.reviewer):
            if callable(getattr(gateway, 'preflight', None)):
                gateway.preflight(self.clock() + seconds)
        run = self.commissions.create(owner, data, request_id)
        return self.get(owner, run['id'])

    def cancel(self, owner, commission_id, body):
        _require(body == {})
        self.commissions.cancel(owner, commission_id)
        return self.get(owner, commission_id)

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
        run = self.commissions.get(owner, commission_id)
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
                return self._feedback_view(db, owner, json.loads(prior[1]))
            record = dict(body, kind=kind, id=str(uuid4()), commission_id=commission_id,
                          source='explicit_developer_feedback', created_at=self.clock())
            db.execute('INSERT INTO feedback VALUES (?,?,?,?,?)',
                       (owner, body['request_id'], commission_id, checksum, canonical_bytes(record).decode()))
            db.commit()
            return self._feedback_view(db, owner, record)

    def withdraw_feedback(self, owner, commission_id, body):
        """Append a durable withdrawal without mutating the original opinion."""
        _require(type(body) is dict and set(body) == {'request_id', 'feedback_id'}
                 and all(type(value) is str and 1 <= len(value) <= 200 for value in body.values()))
        self.commissions.get(owner, commission_id)
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
            return self._feedback_view(db, owner, record)

    def _method_criteria(self, owner, version_ids):
        standards = {}
        for version_id in version_ids:
            for criterion in self.methods.get_method(owner, version_id)['criteria']:
                prior = standards.get(criterion['id'])
                _require(prior is None or prior == criterion,
                         'animation_method_transfer_criteria_conflict', 409)
                standards[criterion['id']] = deepcopy(criterion)
        return list(standards.values())

    def transfer(self, owner, commission_id, body):
        allowed = {'request_id', 'title', 'goal', 'structural_changes', 'budget',
                   'duration_seconds', 'reference', 'base_candidate_id', 'feedback'}
        _require(type(body) is dict and {'request_id', 'goal', 'structural_changes', 'budget'} <= set(body)
                 and set(body) <= allowed)
        run = self.commissions.get(owner, commission_id)
        candidate_id = body.get('base_candidate_id') or run['best_candidate_id']
        source = next((row for row in _candidates(run) if row['id'] == candidate_id), None)
        demo = next((row for row in self.methods.list_demonstrations(owner)
                     if row['provenance'].get('candidate_id') == candidate_id), None)
        _require(demo is not None, 'animation_method_baseline_missing', 409)
        _require(source is not None, 'animation_method_baseline_missing', 409)
        request = dict(body, role='transfer', baseline_demonstration_id=demo['id'],
                       method_versions=source['method_versions'],
                       criteria=self._method_criteria(owner, source['method_versions']))
        request.pop('base_candidate_id', None)
        # The new goal creates a new composition from the exact method, not a copied source baseline.
        return self.start(owner, request)

    def publish(self, owner, commission_id, body):
        required = {'request_id', 'method_version_ids', 'demonstration_ids'}
        _require(type(body) is dict and required <= set(body)
                 and set(body) <= required | {'human_verification'})
        current = self.get(owner, commission_id)
        _require(type(body['method_version_ids']) is list and bool(body['method_version_ids'])
                 and type(body['demonstration_ids']) is list and bool(body['demonstration_ids']))
        allowed = set(current['publication_method_version_ids'])
        _require(set(body['method_version_ids']) <= allowed, 'animation_method_publication_target_invalid', 409)
        human = body.get('human_verification')
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
            results.append(version if version['status'] == 'published' else
                self.methods.publish(owner, version_id, body['request_id'] + ':publish:' + version_id))
        return dict(methods=results, publication='published')

    def read_methods(self, owner, body):
        _require(type(body) is dict and set(body) == {'method_version_ids', 'consumer_id'})
        return self.methods.read_published(owner, body['method_version_ids'], body['consumer_id'])

    def artifact(self, owner, commission_id, candidate_id, kind):
        run = self.commissions.get(owner, commission_id)
        candidate = next((row for row in _candidates(run) if row['id'] == candidate_id), None)
        _require(candidate is not None, 'animation_method_candidate_not_found', 404)
        if kind == 'source':
            return canonical_bytes(candidate['source']), 'application/json'
        _require(kind == 'media')
        path = Path(self.renderer.media_path(candidate['operation_id']))
        _require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 32 * 1024 * 1024,
                 'animation_method_media_changed', 409)
        raw = path.read_bytes()
        _require(len(raw) <= 32 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == candidate['media']['sha256'],
                 'animation_method_media_changed', 409)
        return raw, 'video/mp4'

    def _document(self, owner, run, mode, *, base=None, feedback='', frames=None, comparison=None):
        plan = next((op['result'] for op in run['operations'] if op['input'].get('mode') == 'plan'
                     and op['status'] == 'completed'), None)
        standards = run['frozen'].get('criteria') or (plan['criteria'] if plan else [])
        versions = (base.get('method_versions', []) if base else run['frozen'].get('method_versions', []))
        selected = [self.methods.get_method(owner, version_id) for version_id in versions]
        if not selected and plan:
            selected = deepcopy(plan['methods'])
        document = dict(schema=protocol.INPUT_SCHEMA, mode=mode, goal=run['frozen']['goal'],
            criteria=standards, methods=selected,
            constraints=dict(width=1280, height=720, fps=24,
                             duration_seconds=run['frozen'].get('duration_seconds', 18),
                             allowed_engines=['html-svg']),
            feedback=feedback or run['frozen'].get('feedback', ''),
            base_source=base['source'] if base else None, reference=run['frozen'].get('reference'),
            observation=dict(frames=frames or [], audio_observed=False, motion_observed=False,
                limitations=['Only supplied sampled images were observed; complete motion and audio are unverified.']),
            comparison=comparison, assets={})
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
        protocol.validate_input(document)
        return document

    def _advance_agent(self, owner, run, operation):
        gateway = self.reviewer if operation['stage'] == 'review' else self.author
        if operation['status'] == 'reserved':
            files = {protocol.INPUT_NAME: canonical_bytes(operation['input'])}
            for frame in operation['input']['observation']['frames']:
                path = self.root / 'frame-inputs' / frame['sha256']
                raw = path.read_bytes()
                _require(hashlib.sha256(raw).hexdigest() == frame['sha256'], 'animation_method_frame_changed', 409)
                files[frame['file_name']] = raw
            refs = gateway.upload(operation['input'], files if gateway.visual_input else None)
            admit = gateway.prepare_submission(operation['id'], operation['input'], run['deadline_at'], refs)
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
            if operation['stage'] == 'review':
                receipt = gateway.model_frame_receipt(job['job_id'])
                expected = {row['sha256'] for row in operation['input']['observation']['frames']}
                observed = {row.get('sha256') for row in receipt.get('images', [])
                            if row.get('model_request_completed') is True}
                _require(expected <= observed, 'animation_method_frame_transport_incomplete', 409)
            protocol.validate_result(result, operation['input_sha256'], operation['input'])
        except ServiceError as error:
            # Transport outages may be retried as reads of the same stopped
            # job. A definitive invalid artifact ends this operation.
            definitive = error.status < 500 or not error.code.startswith(('service_', 'http_'))
            if job.get('stop_proven') is True and (definitive or self.clock() >= run['deadline_at']):
                self.commissions.fail(owner, operation['id'], 'animation_method_result_invalid')
                return
            raise
        self.commissions.complete(owner, operation['id'], result)

    def _materialize(self, owner, run, render):
        authored = next(op for op in run['operations'] if op['id'] == render['input']['author_operation_id'])
        result = authored['result']
        versions = []
        if run['frozen'].get('role') == 'transfer':
            versions = run['frozen'].get('method_versions', [])
        else:
            for index, proposal in enumerate(result['methods']):
                parent = next((row for row in authored['input']['methods']
                    if row.get('problem') == proposal['problem'] and row.get('version_id')), None)
                body = deepcopy(proposal)
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
                method = self.methods.create_method(owner, body, authored['id'] + ':method:' + str(index))
                versions.append(method['id'])
        _require(bool(versions), 'animation_method_creator_no_method', 409)
        outcome = render['result']
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
                creator_operation_id=authored['id'], render_operation_id=render['id'])),
            render['id'] + ':demonstration')
        return candidate, demo

    def _record_review(self, owner, run, operation):
        result, document = operation['result'], operation['input']
        opinion = result['review']
        current_id = document['comparison']['candidate_id']
        prior_id = document['comparison'].get('baseline_candidate_id')
        best_id = opinion['preferred_candidate_id'] or run['best_candidate_id'] or current_id
        review_body = dict(
            operation_id=operation['id'], candidate_id=current_id, outcome=opinion['outcome'],
            best_candidate_id=best_id, remaining_issues=opinion['remaining_issues'],
            compared_candidate_id=prior_id, criteria_version=digest(result['criteria']),
            criteria_results=opinion['criteria_results'], evidence=dict(input_sha256=operation['input_sha256']),
            limitations=opinion['limitations'], summary=opinion['summary'])
        demo = self._demonstrations(owner, run['id'])[current_id]
        candidate = next(row for row in run['candidates'] if row['id'] == current_id)
        statuses = {row['criterion_id']: row['status'] for row in opinion['criteria_results']}
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
                    limitations=opinion['limitations']))
            if data['role'] == 'transfer':
                data.update(baseline_demonstration_version_id=run['frozen']['baseline_demonstration_id'],
                            structural_changes=run['frozen']['structural_changes'])
            self.methods.link_validation(owner, data, operation['id'] + ':validation:' + version_id)
        return self.commissions.record_review(owner, run['id'], review_body, operation['id'] + ':review')

    def advance(self, owner, commission_id):
        with _run_lock(self.root / 'locks' / (commission_id + '.lock')) as acquired:
            if not acquired:
                return
            run = self.commissions.get(owner, commission_id)
            if run['state'] in TERMINAL:
                return
            active = next((op for op in run['operations']
                           if op['status'] not in {'completed', 'failed', 'cancelled', 'released'}), None)
            if active:
                if active['status'] == 'reserved' and (run['cancel_requested'] or self.clock() >= run['deadline_at']):
                    self.commissions.release_unsubmitted(owner, active['id'], {'reason': 'stopped_before_claim'})
                    return
                if active['stage'] == 'render':
                    if active['status'] == 'reserved':
                        if run['deadline_at'] - self.clock() < 30:
                            return
                        claim = self.commissions.claim(owner, active['id'])
                        if claim['submission_claimed']:
                            result = self.renderer.render(active['id'], active['input']['source'],
                                active['input']['document'], sound_events=active['input']['sound_events'],
                                deadline_at=run['deadline_at'])
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
                            result = self.renderer.render(active['id'], active['input']['source'],
                                active['input']['document'], sound_events=active['input']['sound_events'],
                                deadline_at=run['deadline_at'])
                    if result is not None:
                        self.commissions.complete(owner, active['id'], result)
                else:
                    self._advance_agent(owner, run, active)
                return
            if run['cancel_requested']:
                self.commissions.stop(owner, commission_id, 'cancelled', '开发者取消了本次实验')
                return
            if any(op['status'] in {'failed', 'cancelled'} for op in run['operations']):
                self.commissions.stop(owner, commission_id, 'failed', '执行失败；保留已有结果与原任务，不自动重试')
                return
            for render in [op for op in run['operations'] if op['stage'] == 'render' and op['status'] == 'completed']:
                if render['result'].get('technical', {}).get('status') != 'passed':
                    self.commissions.stop(owner, commission_id, 'failed', '画面运行检查未通过；保留失败证据，不自动重试')
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
            plans = [op for op in run['operations'] if op['input'].get('mode') == 'plan']
            if not plans:
                self.commissions.reserve(owner, commission_id, 'creator', self._document(owner, run, 'plan'), 'plan')
                return
            creates = [op for op in run['operations'] if op['stage'] == 'creator' and op['input'].get('mode') == 'create']
            for authored in creates:
                if not any(op['stage'] == 'render' and op['input'].get('author_operation_id') == authored['id']
                           for op in run['operations']):
                    if run['remaining']['render'] <= 0:
                        self.commissions.stop(owner, commission_id, 'budget_exhausted', '本次画面渲染预算已用完')
                        return
                    self.commissions.reserve(owner, commission_id, 'render', dict(
                        author_operation_id=authored['id'], source=authored['result']['source'],
                        document=authored['input'], sound_events=authored['result']['sound_events']),
                        'render-' + authored['id'])
                    return
            for candidate in run['candidates']:
                if any(row['candidate_id'] == candidate['id'] for row in run['reviews']):
                    continue
                if run['remaining']['review'] <= 0:
                    self.commissions.stop(owner, commission_id, 'budget_exhausted', '本次评审预算已用完')
                    return
                best = next((row for row in _candidates(run) if row['id'] == run['best_candidate_id']
                             and row['id'] != candidate['id']), None)
                entries, frame_files = [], {}
                for target in ([best, candidate] if best else [candidate]):
                    for index, frame in enumerate(self.renderer.frames(target['operation_id'], max_frames=12)):
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
            feedback = last_review['next_guidance'] if last_review else run['frozen'].get('feedback', '')
            self.commissions.reserve(owner, commission_id, 'creator',
                self._document(owner, run, 'create', base=base, feedback=feedback),
                'create-' + str(len(creates)))
