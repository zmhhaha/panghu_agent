"""Owner-scoped research projects and atomic workflow decisions.

The shared SQLite HTTP service commits one statement per request. A project,
its revision, immutable stage inputs and idempotency receipts therefore live in
one row. An UPDATE guarded by the old revision commits all of them together;
readback of the receipt resolves a lost write response without a fake remote
transaction. Historical stage objects are append-only inside that document.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from .pi_contracts import utc_now, parse_timestamp
from .pi_projection import sql
from .pi_sections import index_sections, select_section
from .workspace_materials import parse_material, doi_material

PROJECT_NAMESPACE = UUID('248f106d-8569-4588-a6d8-6b79c1260845')
MAX_PROJECT_BYTES = 24 * 1024 * 1024
MAX_PROJECTS_PER_OWNER = 50
MAX_MATERIALS = 30
MAX_HISTORY = 1000
MAX_JOBS = 100
MAX_RECEIPTS = 2000
TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out', 'invalid_result'}
REQUIREMENT_DEFAULTS = dict(question='', purpose='', audience='', language='zh-CN',
    depth='review', time_range='', include='', exclude='', notes='')
RESEARCH_KEYS = {'question', 'depth', 'time_range', 'include', 'exclude', 'notes'}


class WorkspaceError(ValueError):
    def __init__(self, code, message=None, status=422):
        self.code, self.status = code, status
        super().__init__(message or code)


def require(condition, code='invalid_workspace_request', status=422):
    if not condition:
        raise WorkspaceError(code, status=status)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def identifier(value):
    try:
        require(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise WorkspaceError('invalid_identifier') from None
    return value


def owner_id(value):
    require(type(value) is str and value.strip() and len(value) <= 200
        and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'authentication_required', 401)
    return value.strip()


def key_id(value):
    require(type(value) is str and 1 <= len(value) <= 128
        and all(33 <= ord(c) <= 126 for c in value), 'idempotency_key_required')
    return value


def text(value, limit=10000, *, blank=True):
    require(type(value) is str and len(value) <= limit
        and not any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127 for c in value))
    value = value.strip()
    require(blank or bool(value))
    return value


def requirements(value, previous=None):
    require(type(value) is dict and set(value) <= set(REQUIREMENT_DEFAULTS))
    result = dict(previous or REQUIREMENT_DEFAULTS)
    for name, item in value.items():
        result[name] = text(item, 10000 if name != 'question' else 4000)
    require(bool(result['question']), 'research_question_required')
    require(result['depth'] in {'overview', 'review', 'comparison'}, 'invalid_research_depth')
    require(bool(result['language']) and len(result['language']) <= 80)
    return result


def _event(project, action, details=None):
    require(len(project['history']) < MAX_HISTORY, 'project_history_limit', 409)
    project['history'].append(dict(id=str(uuid4()), action=action, at=utc_now(),
        revision=project['revision'] + 1, details=details or {}))


def _invalidate(project, plan=False):
    from .workspace_recovery import retain_evidence
    retain_evidence(project)
    project['input_revision'] += 1
    if plan:
        project['search_batch_history'].extend(copy.deepcopy(project['search_batches']))
        project['search_batches'] = []
        project['selections'] = []
        project['confirmations']['plan'] = None
        project['plan'] = None
        project['stage'] = 'goal'
    else:
        project['stage'] = 'evidence' if project['confirmations']['plan'] else 'plan'
    project['confirmations']['evidence'] = None
    # The last published snapshot stays readable until its replacement has
    # passed validation. Its stale marker prevents use under the new scope.


def _stage_output(stage, output):
    # Imported lazily: constructing the application performs no execution I/O.
    from .workflow_contracts import validate_stage_output
    return validate_stage_output(stage, output, {})


def _material_context(project):
    fields = ('material_id', 'name', 'kind', 'sha256', 'text', 'access_level',
              'doi', 'status', 'parse_error', 'recorded_at')
    return [{key: copy.deepcopy(item.get(key)) for key in fields}
            for item in project['materials'] if not item.get('excluded')
            and item.get('material_role', item.get('classification')) != 'draft'
            and item.get('status') in {'ready', 'metadata_only'}]


def _snapshot(project):
    return dict(snapshot_id=str(uuid4()), project_id=project['id'],
        search_batches=copy.deepcopy(project['search_batches']),
        selections=copy.deepcopy(project['selections']), materials=_material_context(project))


def _all_sources(project):
    result = {}
    for batch in project['search_batches']:
        for paper in batch['papers']:
            result[paper['paper_id']] = paper
    for material in _material_context(project):
        result[material['material_id']] = material
    return result


def _fill_selections(project):
    from .source_quality import assess_source
    known = {item['source_id']: item for item in project['selections']}
    result = []
    queries = project.get('plan', {}).get('content', {}).get('search_queries', []) if project.get('plan') else []
    for source_id, source in _all_sources(project).items():
        if source_id in known:
            result.append(known[source_id])
            continue
        quality = assess_source(source, project['requirements'], queries) if 'paper_id' in source else dict(
            default_included=True, reasons=[])
        reasons = quality.get('reasons', [])
        result.append(dict(source_id=source_id, included=quality['default_included'],
            reason='; '.join(item['message'] for item in reasons) if reasons else
                'Suggested inclusion; awaiting evidence confirmation.'))
    project['selections'] = result


def _new_job(project, stage, *, feedback='', source_policy=None, revision=None):
    require(len(project['jobs']) < MAX_JOBS, 'project_job_limit', 409)
    active = next((j for j in project['jobs'] if j['id'] == project['active_job_id']), None)
    require(active is None or active['state'] in TERMINAL, 'job_already_active', 409)
    now = revision['revision_job']['created_at'] if revision is not None else utc_now()
    task_id = revision['revision_job']['task_id'] if revision is not None else str(uuid4())
    deadline = (parse_timestamp(now) + timedelta(seconds=1800)).isoformat().replace('+00:00', 'Z')
    context = dict(schema_version='1', project_id=project['id'],
        requirements=copy.deepcopy(project['requirements']), materials=_material_context(project))
    if feedback:
        context['feedback'] = feedback
    if stage == 'retrieve':
        plan = project['plan']
        require(plan and project['confirmations']['plan'], 'plan_confirmation_required', 409)
        context['plan'] = dict(plan_id=plan['id'], content=copy.deepcopy(plan['content']))
        context['confirmation'] = dict(confirmation_id=project['confirmations']['plan']['id'],
                                      plan_sha256=digest(context['plan']))
    elif stage in {'outline', 'draft'}:
        require(project['confirmations']['plan'], 'plan_confirmation_required', 409)
        _fill_selections(project)
        context['evidence_snapshot'] = (_snapshot(project) if stage == 'outline'
            else copy.deepcopy(project['evidence']['snapshot']))
        if stage == 'draft':
            require(project['outline'] and project['confirmations']['evidence'],
                    'evidence_confirmation_required', 409)
            context['outline'] = dict(outline_id=project['outline']['id'],
                snapshot_sha256=digest(context['evidence_snapshot']),
                content=copy.deepcopy(project['outline']['content']))
            context['confirmation'] = dict(confirmation_id=project['confirmations']['evidence']['id'],
                snapshot_sha256=digest(context['evidence_snapshot']), outline_sha256=digest(context['outline']))
    policy = (project['plan']['source_policy'] if stage == 'retrieve' else source_policy) or dict(
        providers=['crossref', 'pubmed', 'arxiv'], max_results=10, max_search_calls=3)
    task = dict(schema_version='1', business_task_id=task_id, project_id=project['id'],
        stage=stage, context_sha256=digest(context), created_at=now,
        execution_deadline_at=deadline, source_policy=copy.deepcopy(policy))
    if stage != 'revise':
        from .workflow_contracts import validate_context
        validate_context(context, task)
    job = dict(id=task_id, stage=stage, state='pending', input_revision=project['input_revision'],
        created_at=now, updated_at=now, execution_deadline_at=deadline,
        profile='research.' + stage + '.v1', profile_revision='v1', task=task,
        frozen_context=context, idempotency_key='scientific-workspace:' + task_id,
        input_refs=None, request=None, admission_started=False, admission_claim=None,
        remote_job_id=None, cancel_requested=False, stale=False, result=None,
        result_digest=None, manifest_sha256=None, delivery_ack=False, error_code=None)
    if revision is not None:
        job.update(revision)
    project['jobs'].append(job)
    project['active_job_id'] = job['id']
    return job


class WorkspaceStore:
    def __init__(self, database):
        self.db = database

    def initialize(self):
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_workspace_projects (
            project_id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL,
            document TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        self.db._execute('CREATE INDEX IF NOT EXISTS scientific_workspace_owner ON '
                         'scientific_workspace_projects(owner,updated_at,project_id)')

    def _load(self, owner, project_id):
        owner, project_id = owner_id(owner), identifier(project_id)
        rows = self.db._query('SELECT revision,document FROM scientific_workspace_projects WHERE owner='
            + sql(owner) + ' AND project_id=' + sql(project_id))
        require(bool(rows), 'project_not_found', 404)
        try:
            project = json.loads(rows[0]['document'])
            require(project['revision'] == int(rows[0]['revision']) and project['id'] == project_id
                and project['owner'] == owner, 'project_integrity_error', 409)
        except (ValueError, KeyError, TypeError):
            raise WorkspaceError('project_integrity_error', status=409) from None
        from .workspace_recovery import hydrate_evidence
        hydrate_evidence(project)
        return project

    def raw(self, owner, project_id):
        return self._load(owner, project_id)

    @staticmethod
    def public(project, summary=False):
        result = copy.deepcopy(project)
        from .source_quality import assess_source, retrieval_summary
        from .workspace_materials import material_display, materials_summary
        result['retrieval_summary'] = retrieval_summary(project)
        result['materials_summary'] = materials_summary(project['materials'])
        queries = (project.get('plan') or {}).get('content', {}).get('search_queries', [])
        result['selection_assessments'] = {source_id: assess_source(source, project['requirements'], queries)
            for source_id, source in _all_sources(project).items() if 'paper_id' in source}
        result.pop('_receipts', None)
        result.pop('owner', None)
        result.pop('_internal_mutation', None)
        for job in result['jobs']:
            from .workspace_diagnostics import diagnostic
            job['diagnostic'] = diagnostic(job)
            job['error_code'] = job['diagnostic']['code']
            for key in ('frozen_context', 'task', 'request', 'input_refs', 'revision_material',
                        'revision_job', 'admission_claim', 'result'):
                job.pop(key, None)
        for material in result['materials']:
            material.update(material_display(material))
            original = material.pop('text', '')
            material['text_preview'] = original[:2000]
        for version in result['versions']:
            version['stale'] = bool(version.get('stale') or
                                    version['input_revision'] != result['input_revision'])
            version.setdefault('origin', 'website_generated')
            version.setdefault('model_trace_available', bool(version.get('trace_artifacts')))
            for key in ('report_markdown', 'trace_artifacts', 'trace_manifest', 'source_snapshot'):
                version.pop(key, None)
        for batch in result['search_batches'] + result.get('search_batch_history', []):
            batch.pop('observations', None)
        for evidence in result['evidence_history']:
            evidence.pop('snapshot', None)
        if result['evidence']:
            result['evidence'].pop('snapshot', None)
        if summary:
            return {key: result[key] for key in ('id', 'title', 'revision', 'stage', 'created_at',
                'updated_at', 'active_job_id', 'current_version_id')} | {
                'question': result['requirements']['question'],
                'active_job': next((j for j in result['jobs'] if j['id'] == result['active_job_id']), None)}
        return result

    def get(self, owner, project_id):
        return self.public(self._load(owner, project_id))

    def evidence(self, owner, project_id, evidence_id):
        from .workspace_recovery import evidence_detail
        return evidence_detail(self._load(owner, project_id), evidence_id)

    def list(self, owner):
        rows = self.db._query('SELECT document FROM scientific_workspace_projects WHERE owner='
            + sql(owner_id(owner)) + ' ORDER BY updated_at DESC,project_id LIMIT 50')
        return [self.public(json.loads(row['document']), summary=True) for row in rows]

    def create(self, owner, payload, key):
        owner, key = owner_id(owner), key_id(key)
        require(type(payload) is dict and set(payload) <= {'title', 'requirements'})
        checked = requirements(payload.get('requirements', {}))
        title = text(payload.get('title', checked['question'][:100]), 200, blank=False)
        project_id = str(uuid5(PROJECT_NAMESPACE, encode([owner, key])))
        fingerprint = digest(dict(operation='create', payload=payload))
        now = utc_now()
        project = dict(schema_version='1', id=project_id, owner=owner, title=title, revision=0,
            input_revision=0, stage='goal', requirements=checked, materials=[], plan=None,
            plan_history=[], confirmations=dict(plan=None, evidence=None), confirmation_history=[],
            search_batches=[], search_batch_history=[], selections=[], evidence=None, evidence_history=[], outline=None,
            versions=[], current_version_id=None, finalizations=[], annotations=[], jobs=[],
            active_job_id=None, history=[], created_at=now, updated_at=now,
            _receipts={key: fingerprint})
        statement = ('INSERT INTO scientific_workspace_projects (project_id,owner,revision,document,updated_at) '
            'SELECT ' + ','.join(map(sql, (project_id, owner, 0, encode(project), now)))
            + ' WHERE (SELECT COUNT(*) FROM scientific_workspace_projects WHERE owner=' + sql(owner)
            + ')<' + str(MAX_PROJECTS_PER_OWNER) + ' ON CONFLICT(project_id) DO NOTHING')
        self.db._execute(statement)
        try:
            saved = self._load(owner, project_id)
        except WorkspaceError as exc:
            if exc.status == 404:
                raise WorkspaceError('project_count_limit', status=409) from None
            raise
        require(saved['_receipts'].get(key) == fingerprint, 'idempotency_conflict', 409)
        return self.public(saved)

    def mutate(self, owner, project_id, expected_revision, key, operation, payload, change):
        key = key_id(key)
        fingerprint = digest(dict(operation=operation, payload=payload))
        project = self._load(owner, project_id)
        receipt = project['_receipts'].get(key)
        if receipt is not None:
            require(receipt == fingerprint, 'idempotency_conflict', 409)
            return self.public(project)
        require(type(expected_revision) is int and expected_revision >= 0
            and project['revision'] == expected_revision, 'project_revision_conflict', 409)
        require(len(project['_receipts']) < MAX_RECEIPTS, 'project_mutation_limit', 409)
        changed = copy.deepcopy(project)
        change(changed)
        changed['_receipts'][key] = fingerprint
        self._cas(project, changed)
        saved = self._load(owner, project_id)
        receipt = saved['_receipts'].get(key)
        require(receipt is not None, 'project_revision_conflict', 409)
        require(receipt == fingerprint, 'idempotency_conflict', 409)
        return self.public(saved)

    def _cas(self, previous, changed):
        changed['revision'] = previous['revision'] + 1
        changed['updated_at'] = utc_now()
        raw = encode(changed)
        require(len(raw.encode('utf-8')) <= MAX_PROJECT_BYTES, 'project_storage_limit', 413)
        self.db._execute('UPDATE scientific_workspace_projects SET revision=' + str(changed['revision'])
            + ',document=' + sql(raw) + ',updated_at=' + sql(changed['updated_at'])
            + ' WHERE project_id=' + sql(previous['id']) + ' AND owner=' + sql(previous['owner'])
            + ' AND revision=' + str(previous['revision']))

    def internal(self, owner, project_id, change):
        for _ in range(8):
            before = self._load(owner, project_id)
            changed = copy.deepcopy(before)
            if change(changed) is False:
                return before
            token = str(uuid4())
            changed['_internal_mutation'] = token
            self._cas(before, changed)
            saved = self._load(owner, project_id)
            if saved.get('_internal_mutation') == token:
                return saved
        raise WorkspaceError('project_revision_conflict', status=409)

    def active_projects(self):
        # Do not rely on SQLite JSON extensions in the shared service.
        rows = self.db._query('SELECT owner,project_id,document FROM scientific_workspace_projects '
                             'ORDER BY updated_at,project_id LIMIT 2500')
        return [(row['owner'], row['project_id']) for row in rows if any(
            j['state'] not in TERMINAL or (j['state'] == 'completed' and not j.get('delivery_ack'))
            for j in json.loads(row['document'])['jobs'])]

    def patch(self, owner, project_id, payload, key):
        require(type(payload) is dict and set(payload) <= {
            'expected_revision', 'title', 'requirements', 'plan', 'outline', 'selections'})
        def change(project):
            if 'title' in payload:
                project['title'] = text(payload['title'], 200, blank=False)
            if 'requirements' in payload:
                old = project['requirements']
                updated = requirements(payload['requirements'], old)
                if updated != old:
                    affects_plan = any(updated[k] != old[k] for k in RESEARCH_KEYS)
                    _invalidate(project, plan=affects_plan)
                project['requirements'] = updated
            if 'plan' in payload:
                require(project['plan'], 'plan_required', 409)
                output = copy.deepcopy(payload['plan'])
                _stage_output('plan', output)
                previous_plan = project['plan']
                project['plan_history'].append(copy.deepcopy(project['plan']))
                project['plan'] = dict(id=str(uuid4()), revision=project['input_revision'] + 1,
                    content=output, output=output, created_at=utc_now(), edited=True,
                    derived_from_plan_id=previous_plan['id'],
                    source_policy=copy.deepcopy(previous_plan['source_policy']),
                    execution_timeout_seconds=previous_plan['execution_timeout_seconds'])
                _invalidate(project)
                project['confirmations']['plan'] = None
                project['stage'] = 'plan'
            if 'selections' in payload:
                require(project['confirmations']['plan'], 'plan_confirmation_required', 409)
                selected = payload['selections']
                require(type(selected) is list and len(selected) <= 1000)
                known = _all_sources(project)
                _fill_selections(project)
                merged = {item['source_id']: item for item in project['selections']}
                seen = set()
                for item in selected:
                    require(type(item) is dict and set(item) == {'source_id', 'included', 'reason'}
                        and item['source_id'] in known and item['source_id'] not in seen
                        and type(item['included']) is bool)
                    seen.add(item['source_id'])
                    merged[item['source_id']] = dict(source_id=item['source_id'],
                        included=item['included'], reason=text(item['reason'], 2000))
                updated = list(merged.values())
                if updated != project['selections']:
                    _invalidate(project)
                    project['selections'] = updated
            if 'outline' in payload:
                require(project['outline'] and project['evidence'], 'outline_required', 409)
                require(not project['evidence'].get('stale'), 'stale_evidence_requires_update', 409)
                output = copy.deepcopy(payload['outline'])
                from .workspace_recovery import save_manual_evidence
                save_manual_evidence(project, output, allow_limited=True)
            _event(project, 'project_updated', dict(fields=sorted(set(payload) - {'expected_revision'})))
        return self.mutate(owner, project_id, payload.get('expected_revision'), key,
                           'patch', payload, change)

    def add_material(self, owner, project_id, revision, key, *, filename=None, raw=None, doi=None):
        # Verify ownership before spending parser CPU on an uploaded document.
        self._load(owner, project_id)
        key_id(key)
        material = doi_material(doi) if doi is not None else parse_material(filename, raw)
        payload = dict(expected_revision=revision, kind='doi' if doi is not None else 'upload',
            name=material['name'], sha256=material['sha256'])
        def change(project):
            require(len(project['materials']) < MAX_MATERIALS, 'project_material_limit', 409)
            project['materials'].append(material)
            if project['confirmations']['plan']:
                _invalidate(project)
            else:
                _invalidate(project, plan=True)
            _fill_selections(project)
            _event(project, 'material_added', dict(material_id=material['material_id'],
                status=material['status'], access_level=material['access_level']))
        return self.mutate(owner, project_id, revision, key, 'add_material', payload, change)

    def remove_material(self, owner, project_id, material_id, revision, key):
        identifier(material_id)
        def change(project):
            material = next((m for m in project['materials'] if m['material_id'] == material_id), None)
            require(material is not None, 'material_not_found', 404)
            material['excluded'], material['included'] = True, False
            _invalidate(project, plan=not bool(project['confirmations']['plan']))
            _fill_selections(project)
            _event(project, 'material_excluded', dict(material_id=material_id))
        payload = dict(expected_revision=revision, material_id=material_id)
        return self.mutate(owner, project_id, revision, key, 'remove_material', payload, change)

    @staticmethod
    def _version(project, version_id):
        identifier(version_id)
        version = next((v for v in project['versions'] if v['id'] == version_id), None)
        require(version is not None, 'version_not_found', 404)
        return version

    def version(self, owner, project_id, version_id):
        import base64
        from .pi_revision_validation import _REFERENCE_TITLE
        project = self._load(owner, project_id)
        version = copy.deepcopy(self._version(project, version_id))
        version['stale'] = bool(version.get('stale') or
                                version['input_revision'] != project['input_revision'])
        sections = version['section_index']['sections']
        bibliography = [item for item in sections if _REFERENCE_TITLE.search(item['title'])]
        version['revision_sections'] = [item for item in sections if item['editable'] and not any(
            max(item['start_byte'], reference['start_byte']) < min(item['end_byte'], reference['end_byte'])
            for reference in bibliography)]
        artifacts = version.get('trace_artifacts', {})
        version.setdefault('origin', 'website_generated')
        version.setdefault('model_trace_available', bool(artifacts))
        if artifacts:
            version['references'] = json.loads(base64.b64decode(artifacts['references']))
            for role in ('evidence', 'claims'):
                version[role] = [json.loads(line) for line in
                    base64.b64decode(artifacts[role]).decode('utf-8').splitlines() if line]
        else:
            version['revision_sections'] = []
            for role in ('references', 'evidence', 'claims'):
                version.setdefault(role, [])
        for field in ('trace_artifacts', 'trace_manifest', 'source_snapshot'):
            version.pop(field, None)
        version['finalized'] = any(f['version_id'] == version_id for f in project['finalizations'])
        return version

    def compare(self, owner, project_id, base_id, candidate_id):
        project = self._load(owner, project_id)
        base, candidate = self._version(project, base_id), self._version(project, candidate_id)
        require(len(base['report_markdown'].encode('utf-8')) +
            len(candidate['report_markdown'].encode('utf-8')) <= 2 * 1024 * 1024,
            'comparison_too_large', 413)
        lines = list(difflib.unified_diff(base['report_markdown'].splitlines(),
            candidate['report_markdown'].splitlines(), fromfile='base.md', tofile='candidate.md', lineterm=''))
        rendered = '\n'.join(lines)
        require(len(rendered.encode('utf-8')) <= 2 * 1024 * 1024, 'comparison_too_large', 413)
        return dict(base_version_id=base_id, candidate_version_id=candidate_id,
            base_sha256=base['report_sha256'], candidate_sha256=candidate['report_sha256'],
            diff=rendered, changed=base['report_sha256'] != candidate['report_sha256'])

    def annotate(self, owner, project_id, payload, key):
        require(type(payload) is dict and set(payload) <= {
            'expected_revision', 'version_id', 'section_id', 'text'})
        def change(project):
            require(len(project['annotations']) < 500, 'annotation_limit', 409)
            version = self._version(project, payload.get('version_id'))
            section_id = payload.get('section_id') or None
            if section_id:
                select_section(version['report_markdown'], section_id, version['report_sha256'])
            project['annotations'].append(dict(id=str(uuid4()), version_id=version['id'],
                section_id=section_id, text=text(payload.get('text'), 4000, blank=False),
                created_at=utc_now(), resolved=False))
            _event(project, 'annotation_added', dict(version_id=version['id']))
        return self.mutate(owner, project_id, payload.get('expected_revision'), key,
                           'annotate', payload, change)

    def action(self, owner, project_id, action, payload, key, *, source_policy=None):
        require(type(payload) is dict and set(payload) <= {'expected_revision', 'feedback',
            'version_id', 'section_ids', 'job_id', 'allow_limited_evidence', 'unresolved_notes',
            'outline', 'report_markdown', 'origin', 'parent_version_id', 'source_reviewed',
            'material_id', 'classification', 'declared_sources'})
        def change(project):
            feedback = text(payload.get('feedback', ''), 2000)
            if action == 'plan':
                _invalidate(project, plan=True)
                _new_job(project, 'plan', feedback=feedback, source_policy=source_policy)
                project['stage'] = 'plan'
            elif action == 'confirm_plan':
                plan = project['plan']
                require(plan is not None, 'plan_required', 409)
                require(not any(m['status'] == 'failed' and not m.get('excluded')
                    for m in project['materials']), 'unready_materials_must_be_excluded', 409)
                confirmation = dict(id=str(uuid4()), kind='plan', plan_id=plan['id'],
                    input_revision=project['input_revision'], requirements_sha256=digest(project['requirements']),
                    materials_sha256=digest(_material_context(project)), plan_sha256=digest(plan['content']),
                    source_policy_sha256=digest(plan['source_policy']),
                    created_at=utc_now())
                project['confirmations']['plan'] = confirmation
                project['confirmation_history'].append(copy.deepcopy(confirmation))
                _new_job(project, 'retrieve', feedback=feedback, source_policy=plan['source_policy'])
                project['stage'] = 'evidence'
            elif action == 'outline':
                require(project['confirmations']['plan'], 'plan_confirmation_required', 409)
                _new_job(project, 'outline', feedback=feedback, source_policy=source_policy)
                project['stage'] = 'evidence'
            elif action == 'confirm_evidence':
                require(project['confirmations']['plan'], 'plan_confirmation_required', 409)
                require(project['evidence'] and project['outline'], 'evidence_outline_required', 409)
                require(not project['evidence'].get('stale') and not project['outline'].get('stale')
                    and project['evidence'].get('input_revision') == project['input_revision'],
                    'stale_evidence_requires_update', 409)
                gaps = project['outline']['content'].get('gaps', [])
                limited = payload.get('allow_limited_evidence', False)
                require(type(limited) is bool)
                require(not (gaps or not project['outline']['content']['evidence']) or limited,
                        'limited_evidence_confirmation_required', 409)
                confirmation = dict(id=str(uuid4()), kind='evidence',
                    snapshot_id=project['evidence']['id'], snapshot_sha256=project['evidence']['sha256'],
                    outline_id=project['outline']['id'], outline_sha256=digest(project['outline']['content']),
                    input_revision=project['input_revision'], allow_limited_evidence=limited,
                    created_at=utc_now())
                project['confirmations']['evidence'] = confirmation
                project['confirmation_history'].append(copy.deepcopy(confirmation))
                _new_job(project, 'draft', source_policy=source_policy)
                project['stage'] = 'draft'
            elif action == 'supplement':
                require(project['confirmations']['plan'] and project['plan'], 'plan_confirmation_required', 409)
                _invalidate(project)
                _new_job(project, 'retrieve', feedback=feedback, source_policy=project['plan']['source_policy'])
            elif action == 'manual_evidence':
                from .workspace_recovery import save_manual_evidence
                save_manual_evidence(project, copy.deepcopy(payload.get('outline')),
                    allow_limited=payload.get('allow_limited_evidence', False))
            elif action == 'classify_material':
                from .workspace_materials import configure_material
                material_id = identifier(payload.get('material_id'))
                material = next((item for item in project['materials']
                    if item['material_id'] == material_id), None)
                require(material is not None, 'material_not_found', 404)
                configuration = {key: payload[key] for key in ('classification', 'declared_sources')
                                 if key in payload}
                updated = configure_material(material, configuration)
                if updated != material:
                    material.clear()
                    material.update(updated)
                    _invalidate(project, plan=not bool(project['confirmations']['plan']))
                    _fill_selections(project)
            elif action == 'import_report':
                from .workspace_recovery import import_report
                import_report(project, payload)
            elif action == 'confirm_import':
                from .workspace_recovery import confirm_import
                confirm_import(project, self._version(project, payload.get('version_id')), payload)
            elif action == 'revise':
                version = self._version(project, payload.get('version_id'))
                require(version.get('model_trace_available', bool(version.get('trace_artifacts'))),
                    'imported_report_requires_manual_revision', 409)
                require(not version.get('stale') and version['input_revision'] == project['input_revision']
                    and project['confirmations']['evidence'] is not None,
                    'stale_version_requires_reconfirmation', 409)
                sections = payload.get('section_ids')
                require(type(sections) is list and len(sections) == 1,
                        'one_section_per_revision')
                require(bool(feedback), 'revision_feedback_required')
                section = select_section(version['report_markdown'], sections[0], version['report_sha256'])
                from .workspace_execution import revision_input
                revision = revision_input(project, version, section, feedback)
                _new_job(project, 'revise', feedback=feedback, revision=revision,
                         source_policy=source_policy)
                project['stage'] = 'review'
            elif action in {'adopt', 'finalize'}:
                version = self._version(project, payload.get('version_id'))
                require(not version.get('stale') and version['input_revision'] == project['input_revision'],
                        'stale_version_requires_reconfirmation', 409)
                imported = version.get('model_trace_available') is False
                if imported:
                    require(version.get('import_review') is not None,
                            'import_review_confirmation_required', 409)
                else:
                    require(project['confirmations']['evidence'] is not None,
                            'evidence_confirmation_required', 409)
                if action == 'adopt':
                    require(not version.get('parent_version_id') or
                        version['parent_version_id'] == project['current_version_id'],
                        'candidate_parent_conflict', 409)
                    project['current_version_id'] = version['id']
                    version['status'] = 'adopted'
                    project['stage'] = 'review'
                else:
                    require(version['id'] == project['current_version_id'],
                            'finalize_current_version_required', 409)
                    project['finalizations'].append(dict(id=str(uuid4()), version_id=version['id'],
                        report_sha256=version['report_sha256'], evidence_snapshot_id=version['evidence_snapshot_id'],
                        confirmation_id=version['confirmation_id'], created_at=utc_now(),
                        unresolved_notes=text(payload.get('unresolved_notes', ''), 4000),
                        publication_authorized=False))
                    project['stage'] = 'final'
            elif action in {'cancel', 'retry'}:
                job = next((j for j in project['jobs'] if j['id'] == payload.get('job_id')), None)
                require(job is not None, 'job_not_found', 404)
                if action == 'cancel':
                    require(job['state'] not in TERMINAL, 'job_already_finished', 409)
                    job['cancel_requested'] = True
                    if not job['admission_started']:
                        job['state'] = 'cancelled'
                        project['active_job_id'] = None
                else:
                    require(job['state'] in {'failed', 'timed_out', 'cancelled', 'invalid_result'},
                            'job_retry_not_allowed', 409)
                    require(job['input_revision'] == project['input_revision'],
                            'retry_inputs_changed', 409)
                    if job['stage'] == 'revise':
                        parent = self._version(project, job['parent_version_id'])
                        section = select_section(parent['report_markdown'], job['revision_job']['section_id'],
                                                 parent['report_sha256'])
                        from .workspace_execution import revision_input
                        revision = revision_input(project, parent, section, job['revision_job']['instruction'])
                        _new_job(project, 'revise', revision=revision, source_policy=source_policy)
                    else:
                        _new_job(project, job['stage'], feedback=feedback, source_policy=source_policy)
            else:
                raise WorkspaceError('unknown_workspace_action', status=404)
            _event(project, action)
        return self.mutate(owner, project_id, payload.get('expected_revision'), key,
                           'action:' + action, payload, change)
