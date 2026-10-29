"""Pure staged research contracts and cross-attempt source validation.

These checks establish recorded access, lineage and revision compatibility. They
do not certify scientific accuracy. No source URL or recorded file path is read.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from uuid import UUID

from .academic_observations import validate_academic_observations, AcademicObservationError

STAGES = ('plan', 'retrieve', 'outline', 'draft')
PROVIDERS = {'crossref', 'pubmed', 'arxiv', 'openalex', 'semantic_scholar'}
MAX_BYTES = 16 * 1024 * 1024


class WorkflowContractError(ValueError):
    def __init__(self, code='invalid_workflow_contract'):
        self.code = code
        super().__init__(code)


def _require(value, code='invalid_workflow_contract'):
    if not value:
        raise WorkflowContractError(code)


def canonical_bytes(value):
    # Protected tool timestamps are measured finite floats. The older business
    # contract's integer-only CanonicalJSON cannot represent those observations.
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WorkflowContractError() from None


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, item in items:
            _require(key not in result)
            result[key] = item
        return result
    try:
        _require(type(raw) is bytes and len(raw) <= MAX_BYTES)
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WorkflowContractError() from None


def _object(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _uuid(value):
    try:
        _require(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise WorkflowContractError() from None


def _text(value, maximum=10000, empty=False):
    _require(type(value) is str and (empty or bool(value.strip())) and len(value) <= maximum
        and not any(0xD800 <= ord(c) <= 0xDFFF or ord(c) == 127 or
                    (ord(c) < 32 and c not in '\n\r\t') for c in value))


def _strings(value, maximum=100):
    _require(type(value) is list and len(value) <= maximum)
    for item in value:
        _text(item)


def _time(value):
    try:
        _require(type(value) is str)
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0)
        return parsed.timestamp()
    except (TypeError, AttributeError, ValueError):
        raise WorkflowContractError() from None


def _hash(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None)


def validate_source_policy(policy, *, bounded=False):
    _object(policy, {'providers', 'max_results'} | ({'max_search_calls'} if bounded else set()))
    _require(type(policy['providers']) is list and policy['providers']
        and all(type(p) is str for p in policy['providers'])
        and len(set(policy['providers'])) == len(policy['providers'])
        and set(policy['providers']) <= PROVIDERS
        and type(policy['max_results']) is int and 1 <= policy['max_results'] <= 50)
    if bounded:
        _require(type(policy['max_search_calls']) is int and 1 <= policy['max_search_calls'] <= 20)
    return policy


def validate_task(task, stage=None):
    _object(task, {'schema_version', 'business_task_id', 'project_id', 'stage',
        'context_sha256', 'created_at', 'execution_deadline_at', 'source_policy'})
    _require(task['schema_version'] == '1' and task['stage'] in STAGES
        and (stage is None or task['stage'] == stage))
    _uuid(task['business_task_id'])
    _uuid(task['project_id'])
    _hash(task['context_sha256'])
    _require(0 < _time(task['execution_deadline_at']) - _time(task['created_at']) <= 3600)
    validate_source_policy(task['source_policy'], bounded=True)
    return task


def _materials(materials):
    _require(type(materials) is list and len(materials) <= 100)
    identifiers = set()
    for item in materials:
        _object(item, {'material_id', 'name', 'kind', 'sha256', 'text', 'access_level',
                      'doi', 'status', 'parse_error', 'recorded_at'})
        _uuid(item['material_id'])
        _require(item['material_id'] not in identifiers)
        identifiers.add(item['material_id'])
        _hash(item['sha256'])
        _text(item['name'], 500)
        _text(item['text'], 2 * 1024 * 1024, empty=True)
        _time(item['recorded_at'])
        _require(item['kind'] in ('pdf', 'markdown', 'text', 'doi')
            and item['access_level'] in ('full_text', 'metadata', 'unavailable'))
        _text(item['status'], 100)
        _require(item['parse_error'] is None or type(item['parse_error']) is str)
        _require(item['doi'] is None or type(item['doi']) is str and
                 re.fullmatch(r'10\.\d{4,9}/\S+', item['doi'], re.I))
        _require(item['access_level'] != 'full_text' or bool(item['text'].strip()))
        _require(item['access_level'] == 'full_text' or item['text'] == '')
    return materials


def _plan(output):
    _object(output, {'questions', 'research_questions', 'search_queries', 'inclusion_criteria',
                     'exclusion_criteria', 'scope', 'limitations'})
    for key in set(output) - {'scope'}:
        _strings(output[key])
    _text(output['scope'])
    _require(bool(output['research_questions']) and bool(output['search_queries']))


def _jsonl(rows):
    return b''.join(canonical_bytes(row) + b'\n' for row in rows)


def validate_snapshot(snapshot, project_id):
    _object(snapshot, {'snapshot_id', 'project_id', 'search_batches', 'selections', 'materials'})
    _uuid(snapshot['snapshot_id'])
    _require(snapshot['project_id'] == project_id)
    _materials(snapshot['materials'])
    _require(type(snapshot['search_batches']) is list and len(snapshot['search_batches']) <= 20)
    sources = {m['material_id'] for m in snapshot['materials'] if m['access_level'] != 'unavailable'}
    batches, attempts = set(), set()
    for batch in snapshot['search_batches']:
        _object(batch, {'batch_id', 'project_id', 'attempt_id', 'request_sha256',
                        'source_policy', 'searches', 'papers', 'observations'})
        _uuid(batch['batch_id'])
        _uuid(batch['attempt_id'])
        _hash(batch['request_sha256'])
        _require(batch['project_id'] == project_id and batch['batch_id'] not in batches
            and batch['attempt_id'] not in attempts)
        batches.add(batch['batch_id'])
        attempts.add(batch['attempt_id'])
        validate_retrieval(batch, batch['observations'], batch['attempt_id'],
                           batch['request_sha256'], batch['source_policy'])
        sources.update(row['paper_id'] for row in batch['papers'])
    _require(type(snapshot['selections']) is list and len(snapshot['selections']) <= 5000)
    selected = set()
    for item in snapshot['selections']:
        _object(item, {'source_id', 'included', 'reason'})
        _text(item['source_id'], 500)
        _text(item['reason'], 2000, empty=True)
        _require(item['source_id'] in sources and item['source_id'] not in selected
            and type(item['included']) is bool)
        selected.add(item['source_id'])
    _require(selected == sources, 'incomplete_source_selections')
    return snapshot


def validate_context(context, task):
    validate_task(task)
    fields = {'schema_version', 'project_id', 'requirements', 'materials'}
    if type(context) is dict and 'feedback' in context:
        fields.add('feedback')
        _text(context['feedback'], 4000, empty=True)
    if task['stage'] == 'retrieve':
        fields |= {'plan', 'confirmation'}
    if task['stage'] in ('outline', 'draft'):
        fields.add('evidence_snapshot')
    if task['stage'] == 'draft':
        fields |= {'outline', 'confirmation'}
    _object(context, fields)
    _require(context['schema_version'] == '1' and context['project_id'] == task['project_id']
        and len(canonical_bytes(context)) <= MAX_BYTES
        and digest(context) == task['context_sha256'], 'workflow_context_mismatch')
    requirements = context['requirements']
    _object(requirements, {'question', 'purpose', 'audience', 'language', 'depth', 'time_range',
                           'include', 'exclude', 'notes'})
    for key, value in requirements.items():
        _text(value, empty=key != 'question')
    _require(requirements['depth'] in ('overview', 'review', 'comparison'))
    _materials(context['materials'])
    if task['stage'] == 'retrieve':
        _object(context['plan'], {'plan_id', 'content'})
        _uuid(context['plan']['plan_id'])
        _plan(context['plan']['content'])
        _object(context['confirmation'], {'confirmation_id', 'plan_sha256'})
        _uuid(context['confirmation']['confirmation_id'])
        _require(context['confirmation']['plan_sha256'] == digest(context['plan']),
                 'workflow_confirmation_mismatch')
    if task['stage'] in ('outline', 'draft'):
        validate_snapshot(context['evidence_snapshot'], task['project_id'])
        _require(context['materials'] == context['evidence_snapshot']['materials'],
                 'workflow_material_snapshot_mismatch')
    if task['stage'] == 'draft':
        outline = context['outline']
        _object(outline, {'outline_id', 'snapshot_sha256', 'content'})
        _uuid(outline['outline_id'])
        _require(outline['snapshot_sha256'] == digest(context['evidence_snapshot']))
        _outline(outline['content'], context['evidence_snapshot'])
        confirmation = context['confirmation']
        _object(confirmation, {'confirmation_id', 'snapshot_sha256', 'outline_sha256'})
        _uuid(confirmation['confirmation_id'])
        _require(confirmation['snapshot_sha256'] == outline['snapshot_sha256'] and
            confirmation['outline_sha256'] == digest(outline), 'workflow_confirmation_mismatch')
    return context


def validate_retrieval(output, observations, attempt_id, request_sha256, policy):
    validate_source_policy(policy)
    _require(type(output['searches']) is list and type(output['papers']) is list
        and len(output['searches']) <= 100 and len(output['papers']) <= 5000)
    try:
        summary = validate_academic_observations(canonical_bytes(observations),
            _jsonl(output['searches']), _jsonl(output['papers']), attempt_id=attempt_id,
            request_sha256=request_sha256, source_policy=policy)
    except (AcademicObservationError, KeyError, TypeError):
        raise WorkflowContractError('workflow_observation_mismatch') from None
    from .pi_citations import _paper, TraceUnavailable
    for paper in output['papers']:
        try:
            _paper(paper)
        except TraceUnavailable:
            raise WorkflowContractError('invalid_workflow_paper') from None
        _require(paper['full_text_path'] is None and not paper['availability']['full_text']['available'],
                 'workflow_full_text_not_acquired')
    return summary


def source_papers(snapshot):
    """Deterministic legacy trace records for selected actual source snapshots."""
    included = {s['source_id'] for s in snapshot['selections'] if s['included']}
    papers = {}
    for batch in snapshot['search_batches']:
        for paper in batch['papers']:
            if paper['paper_id'] in included:
                papers.setdefault(paper['paper_id'], paper)
    for material in snapshot['materials']:
        identifier = material['material_id']
        if identifier not in included:
            continue
        full = material['access_level'] == 'full_text'
        stamp = material['recorded_at']
        _require(identifier not in papers, 'workflow_source_collision')
        papers[identifier] = dict(paper_id=identifier, title=material['name'], authors=[], year=None,
            doi=material['doi'], pmid=None, url=None, sources=['user_provided_material'],
            retrieved_at=stamp, abstract=None,
            full_text_path=('provided-materials/' + identifier + '.txt') if full else None,
            availability={key: dict(available=available, obtained_at=stamp if available else None)
                for key, available in [('metadata', True), ('abstract', False), ('full_text', full)]},
            verification=dict(status='unknown', checked_at=None, source=None,
                error='User-provided source; identifier not verified by retrieval bridge'))
    return list(papers.values())


def _outline(output, snapshot):
    from .pi_citations import _evidence, TraceUnavailable
    _object(output, {'evidence', 'sections', 'gaps'})
    _strings(output['gaps'])
    _require(type(output['evidence']) is list and len(output['evidence']) <= 5000
        and type(output['sections']) is list and 1 <= len(output['sections']) <= 100)
    papers = {p['paper_id']: p for p in source_papers(snapshot)}
    evidence = set()
    for row in output['evidence']:
        try:
            _evidence(row)
        except TraceUnavailable:
            raise WorkflowContractError('invalid_workflow_evidence') from None
        _require(row['evidence_id'] not in evidence and row['paper_id'] in papers
            and papers[row['paper_id']]['availability'][row['access_level']]['available'],
            'workflow_evidence_source_mismatch')
        evidence.add(row['evidence_id'])
    sections = set()
    for row in output['sections']:
        _object(row, {'section_id', 'title', 'questions', 'evidence_ids'})
        _text(row['section_id'], 100)
        _text(row['title'], 500)
        _strings(row['questions'])
        _strings(row['evidence_ids'], 5000)
        _require(row['section_id'] not in sections and set(row['evidence_ids']) <= evidence
            and len(row['evidence_ids']) == len(set(row['evidence_ids'])))
        sections.add(row['section_id'])


def trace_artifacts(context, output):
    return dict(papers=_jsonl(source_papers(context['evidence_snapshot'])),
        evidence=_jsonl(context['outline']['content']['evidence']),
        claims=_jsonl(output['claims']), references=canonical_bytes(output['references']))


def trace_manifest(report, artifacts):
    files = dict(artifacts, report=report.encode('utf-8'))
    return [dict(artifact_id=key, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        media_type='text/markdown' if key == 'report' else 'application/json' if
            key == 'references' else 'application/x-ndjson') for key, raw in files.items()]


def _draft(output, context):
    _object(output, {'report_markdown', 'claims', 'references'})
    _text(output['report_markdown'], 2 * 1024 * 1024)
    _require(len(output['report_markdown'].encode('utf-8')) <= 2 * 1024 * 1024)
    _require(type(output['claims']) is list and len(output['claims']) <= 5000
        and type(output['references']) is list and len(output['references']) <= 5000)
    artifacts = trace_artifacts(context, output)
    report = output['report_markdown']
    from .pi_revision_validation import _load, _disclosures, RevisionValidationError
    try:
        parsed, _ = _load(report, trace_manifest(report, artifacts), artifacts)
        _disclosures(report, parsed)
    except RevisionValidationError:
        raise WorkflowContractError('workflow_draft_trace_invalid') from None
    headings = [m.group(1).strip() for m in re.finditer(r'^#{1,6}\s+(.+?)\s*#*$', report, re.M)]
    confirmed = [s['title'] for s in context['outline']['content']['sections']]
    cursor = 0
    for title in headings:
        if cursor < len(confirmed) and title == confirmed[cursor]:
            cursor += 1
    _require(cursor == len(confirmed),
             'workflow_draft_outline_mismatch')
    _require(not context['outline']['content']['gaps'] or
             re.search(r'limitations?|局限|限制|证据缺口', report, re.I), 'workflow_missing_limitations')


def validate_stage_output(stage, output, context, *, observations=None, attempt_id=None,
                          request_sha256=None, source_policy=None):
    _require(stage in STAGES and len(canonical_bytes(output)) <= MAX_BYTES)
    if stage == 'plan':
        _plan(output)
    elif stage == 'retrieve':
        _object(output, {'searches', 'papers', 'gaps'})
        _strings(output['gaps'])
        validate_retrieval(output, observations, attempt_id, request_sha256, source_policy)
    elif stage == 'outline':
        _outline(output, context['evidence_snapshot'])
    else:
        _draft(output, context)
    return output


def stage_result_digest(result):
    return digest({key: value for key, value in result.items() if key != 'result_digest'})


def validate_stage_result(result, task, context):
    validate_context(context, task)
    _object(result, {'schema_version', 'project_id', 'task_id', 'stage', 'context_sha256',
        'output', 'provenance', 'artifacts', 'result_digest'})
    _require(result['schema_version'] == '1' and result['project_id'] == task['project_id']
        and result['task_id'] == task['business_task_id'] and result['stage'] == task['stage']
        and result['context_sha256'] == task['context_sha256']
        and result['result_digest'] == stage_result_digest(result), 'workflow_result_mismatch')
    provenance = result['provenance']
    _require(type(provenance) is dict and provenance.get('profile') == 'research.' + task['stage'] + '.v1'
        and provenance.get('context_sha256') == task['context_sha256'])
    _uuid(provenance.get('execution_attempt_id'))
    _hash(provenance.get('execution_request_sha256'))
    output = result['output']
    if task['stage'] == 'retrieve':
        _object(output, {'searches', 'papers', 'gaps', 'observations', 'retrieval'})
        _object(output['retrieval'], {'attempt_id', 'request_sha256', 'source_policy'})
        _require(output['retrieval'] == dict(attempt_id=provenance['execution_attempt_id'],
            request_sha256=provenance['execution_request_sha256'],
            source_policy={k:task['source_policy'][k] for k in ('providers', 'max_results')}))
        validate_stage_output('retrieve', {k:output[k] for k in ('searches', 'papers', 'gaps')}, context,
            observations=output['observations'], **output['retrieval'])
        _require(provenance.get('retrieval_performed') is True
            and sum(row['request']['tool'] == 'academic_search' for row in
                    output['observations']['observations']) <= task['source_policy']['max_search_calls'],
            'workflow_search_budget_exhausted')
    else:
        _require(provenance.get('retrieval_performed') is False)
        validate_stage_output(task['stage'], output, context)
    if task['stage'] == 'draft':
        _require(provenance.get('evidence_snapshot_sha256') == digest(context['evidence_snapshot'])
            and provenance.get('outline_sha256') == digest(context['outline'])
            and provenance.get('confirmation_id') == context['confirmation']['confirmation_id'],
            'workflow_draft_provenance_mismatch')
        _require(result['artifacts'] == trace_manifest(output['report_markdown'], trace_artifacts(context, output)),
                 'workflow_result_artifact_mismatch')
    else:
        _require(result['artifacts'] == [])
    return result
