"""Offline real staged contracts: immutable cross-attempt evidence, no models."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from agent_service.contracts import ServiceError
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import research_workflow as profiles
from scientific_agent import workflow_contracts as contracts
from scientific_agent.pi_revision_validation import validate_revision_input
from scientific_agent.pi_sections import index_sections


def uid(number):
    return str(UUID(int=number))


PROJECT, TASK, ATTEMPT = uid(100), uid(101), uid(102)
STAMP = '2026-10-29T00:00:00Z'
POLICY = dict(providers=['crossref'], max_results=2, max_search_calls=3)


def context():
    return dict(schema_version='1', project_id=PROJECT, requirements=dict(
        question='What can the recorded source establish?', purpose='', audience='', language='zh-CN',
        depth='overview', time_range='', include='', exclude='', notes=''), materials=[])


def task(stage, ctx):
    return dict(schema_version='1', business_task_id=TASK, project_id=PROJECT, stage=stage,
        context_sha256=contracts.digest(ctx), source_policy=deepcopy(POLICY), created_at=STAMP,
        execution_deadline_at='2026-10-29T00:30:00Z')


def plan_context():
    ctx = context()
    ctx['plan'] = dict(plan_id=uid(103), content=deepcopy(profiles.OUTPUT_EXAMPLES['plan']))
    ctx['confirmation'] = dict(confirmation_id=uid(104), plan_sha256=contracts.digest(ctx['plan']))
    return ctx


def retrieval():
    stamp = STAMP
    available = lambda flag: dict(available=flag, obtained_at=stamp if flag else None)
    paper = dict(paper_id='doi:10.1234/fixture', title='A synthetic source, not a scientific finding',
        authors=['Fixture'], year=2026, doi='10.1234/fixture', pmid=None,
        url='https://example.invalid/fixture', sources=['crossref'], retrieved_at=stamp,
        availability=dict(metadata=available(True), abstract=available(True), full_text=available(False)),
        abstract='This synthetic observation only tests recorded access.', full_text_path=None,
        verification=dict(status='unknown', checked_at=None, source=None, error=None))
    search = dict(source='crossref', query='synthetic observation', searched_at=stamp,
        status='success', result_count=1, error=None)
    request = dict(tool='academic_search', payload=dict(query=search['query'], providers=['crossref'], limit=2))
    observed = dict(schema_version='1', attempt_id=ATTEMPT, request_sha256='a' * 64, observations=[dict(
        call_sha256=contracts.digest(request), observed_at=123.5, state='complete', request=request,
        response=dict(schema_version='1', searches=[search], papers=[paper], database_total=None, notes=[]))])
    output = dict(searches=[search], papers=[paper], gaps=['Synthetic fixture only'])
    return output, deepcopy(observed)


def outline_context():
    output, observed = retrieval()
    ctx = context()
    ctx['evidence_snapshot'] = dict(snapshot_id=uid(105), project_id=PROJECT, search_batches=[dict(
        batch_id=uid(106), project_id=PROJECT, attempt_id=ATTEMPT, request_sha256='a' * 64,
        source_policy={k:POLICY[k] for k in ('providers', 'max_results')},
        searches=output['searches'], papers=output['papers'], observations=observed)], materials=[],
        selections=[dict(source_id=output['papers'][0]['paper_id'], included=True, reason='Relevant')])
    return ctx


def outline():
    output = deepcopy(profiles.OUTPUT_EXAMPLES['outline'])
    output['evidence'][0]['paper_id'] = 'doi:10.1234/fixture'
    output['evidence'][0]['finding'] = 'Only recorded access was tested.'
    return output


def draft_context():
    ctx = outline_context()
    ctx['outline'] = dict(outline_id=uid(107), snapshot_sha256=contracts.digest(ctx['evidence_snapshot']),
                          content=outline())
    ctx['confirmation'] = dict(confirmation_id=uid(108),
        snapshot_sha256=ctx['outline']['snapshot_sha256'], outline_sha256=contracts.digest(ctx['outline']))
    return ctx


def draft():
    output = deepcopy(profiles.OUTPUT_EXAMPLES['draft'])
    output['references'][0]['paper_id'] = 'doi:10.1234/fixture'
    return output


def envelope(stage, ctx):
    return SimpleNamespace(task=task(stage, ctx), profile='research.' + stage + '.v1',
        profile_revision='v1', engine='pi', runtime_revision='offline-runtime-v1',
        image_digest='sha256:' + 'b' * 64, attempt_id=uid(109), fence=1,
        request_sha256='c' * 64, execution_binding=SimpleNamespace(sha256='d' * 64))


@pytest.mark.parametrize('stage', contracts.STAGES)
def test_stages_are_real_distinct_contracts_with_only_retrieval_tools_in_retrieve(stage):
    profile = implementation('research.' + stage + '.v1')
    assert profile.task_schema == 'research-' + stage + '.v1'
    assert profile.supports_inputs is True
    assert profile.required_tools == (('read', 'write', 'academic_search', 'academic_verify')
        if stage == 'retrieve' else ('read', 'write'))
    assert 'result/output.json' in profile.build_prompt(task(stage, context()))


def test_planning_reads_structured_requirements_and_feedback_without_short_topic_limit():
    ctx = context()
    ctx['requirements']['notes'] = 'A long, separate structured requirement. ' * 100
    ctx['feedback'] = 'Narrow the recommendation.'
    assert contracts.validate_context(ctx, task('plan', ctx)) == ctx


def test_retrieval_requires_bound_human_plan_confirmation():
    ctx = plan_context()
    contracts.validate_context(ctx, task('retrieve', ctx))
    ctx['plan']['content']['scope'] = 'Changed after confirmation'
    with pytest.raises(contracts.WorkflowContractError, match='confirmation'):
        contracts.validate_context(ctx, task('retrieve', ctx))


def test_original_protected_cross_attempt_observations_are_not_fabricated_in_draft():
    ctx = draft_context()
    request = task('draft', ctx)
    contracts.validate_context(ctx, request)
    assert ctx['evidence_snapshot']['search_batches'][0]['attempt_id'] != uid(109)
    contracts.validate_stage_output('draft', draft(), ctx)


@pytest.mark.parametrize('mutate', [
    lambda c: c['evidence_snapshot']['search_batches'][0]['papers'][0].update(title='Fabricated metadata'),
    lambda c: c['evidence_snapshot']['search_batches'][0].update(attempt_id=uid(999)),
    lambda c: c['evidence_snapshot']['search_batches'][0].update(project_id=uid(999)),
    lambda c: c['evidence_snapshot']['search_batches'][0]['observations']['observations'][0].update(state='admitted'),
])
def test_source_tampering_or_uncertain_observations_cannot_enter_outline(mutate):
    ctx = outline_context()
    mutate(ctx)
    with pytest.raises(contracts.WorkflowContractError):
        contracts.validate_context(ctx, task('outline', ctx))


def test_excluded_sources_and_access_upgrades_are_rejected():
    ctx = outline_context()
    ctx['evidence_snapshot']['selections'][0]['included'] = False
    with pytest.raises(contracts.WorkflowContractError, match='source_mismatch'):
        contracts.validate_stage_output('outline', outline(), ctx)
    ctx['evidence_snapshot']['selections'][0]['included'] = True
    output = outline()
    output['evidence'][0]['access_level'] = 'full_text'
    with pytest.raises(contracts.WorkflowContractError, match='source_mismatch'):
        contracts.validate_stage_output('outline', output, ctx)


def test_draft_rejects_unconfirmed_outline_and_new_source_references():
    ctx = draft_context()
    ctx['outline']['content']['sections'][0]['title'] = 'Changed heading'
    with pytest.raises(contracts.WorkflowContractError, match='confirmation'):
        contracts.validate_context(ctx, task('draft', ctx))
    ctx = draft_context()
    output = draft()
    output['references'][0]['paper_id'] = 'unobserved-source'
    with pytest.raises(contracts.WorkflowContractError, match='draft_trace'):
        contracts.validate_stage_output('draft', output, ctx)


def test_draft_collection_produces_fixed_evidence_revision_compatible_bytes(tmp_path):
    ctx, output = draft_context(), draft()
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'result').mkdir()
    (tmp_path / 'inputs/context.json').write_bytes(contracts.canonical_bytes(ctx))
    (tmp_path / 'result/output.json').write_bytes(contracts.canonical_bytes(output))
    execution = envelope('draft', ctx)
    profile = implementation(execution.profile)
    result = {f.name:f.content() for f in profile.collect_output(tmp_path, execution,
        StopEvidence(execution.attempt_id, True, ('offline-stopped-writer',)))}
    stage = contracts.decode(result['stage-result.json'])
    contracts.validate_stage_result(stage, execution.task, ctx)
    assert stage['provenance']['retrieval_performed'] is False
    assert 'academic-observations.json' not in result
    artifacts = contracts.trace_artifacts(ctx, output)
    sections = index_sections(output['report_markdown'])
    section = next(s for s in sections['sections'] if s['editable'] and s['title'] == 'Findings')
    checked = validate_revision_input(output['report_markdown'], stage['artifacts'], artifacts,
        section['section_id'], hashlib.sha256(result['report.md']).hexdigest())
    assert checked['material_policy'] == 'recorded_evidence_only'


def test_retrieval_cannot_accept_agent_owned_observation_file(tmp_path):
    ctx = plan_context()
    output, observed = retrieval()
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'result').mkdir()
    (tmp_path / 'inputs/context.json').write_bytes(contracts.canonical_bytes(ctx))
    (tmp_path / 'result/output.json').write_bytes(contracts.canonical_bytes(output))
    execution = envelope('retrieve', ctx)
    observed.update(attempt_id=execution.attempt_id, request_sha256=execution.request_sha256)
    target = tmp_path / 'result/academic-observations.json'
    target.write_bytes(contracts.canonical_bytes(observed))
    target.chmod(0o644)
    with pytest.raises(ServiceError, match='observation_not_protected'):
        implementation(execution.profile).collect_output(tmp_path, execution,
            StopEvidence(execution.attempt_id, True, ('offline-stopped-writer',)))


@pytest.mark.parametrize('stage', ['plan', 'retrieve', 'outline'])
def test_nonwriting_collectors_return_only_validated_stage_result(tmp_path, monkeypatch, stage):
    ctx = context() if stage == 'plan' else plan_context() if stage == 'retrieve' else outline_context()
    output = deepcopy(profiles.OUTPUT_EXAMPLES['plan']) if stage == 'plan' else (
        retrieval()[0] if stage == 'retrieve' else outline())
    execution = envelope(stage, ctx)
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'result').mkdir()
    (tmp_path / 'inputs/context.json').write_bytes(contracts.canonical_bytes(ctx))
    (tmp_path / 'result/output.json').write_bytes(contracts.canonical_bytes(output))
    if stage == 'retrieve':
        observed = retrieval()[1]
        observed.update(attempt_id=execution.attempt_id, request_sha256=execution.request_sha256)
        (tmp_path / 'result/academic-observations.json').write_bytes(contracts.canonical_bytes(observed))
        # Simulate the authenticated root read only; actual comparisons remain real.
        original = profiles._read
        monkeypatch.setattr(profiles, '_read', lambda root, name, limit, **options:
            (Path(root) / name).read_bytes() if options.get('protected') else original(root, name, limit))
    collected = implementation(execution.profile).collect_output(tmp_path, execution,
        StopEvidence(execution.attempt_id, True, ('offline-stopped-writer',)))
    assert [f.name for f in collected] == ['stage-result.json']
    value = contracts.decode(collected[0].content())
    contracts.validate_stage_result(value, execution.task, ctx)
    assert value['provenance']['retrieval_performed'] is (stage == 'retrieve')


def test_prelaunch_input_check_rejects_mutated_context_before_tools(tmp_path):
    ctx = plan_context()
    request = task('retrieve', ctx)
    ctx['requirements']['question'] = 'Different question after admission'
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs/context.json').write_bytes(contracts.canonical_bytes(ctx))
    with pytest.raises(ServiceError, match='context_mismatch'):
        implementation('research.retrieve.v1').validate_inputs(tmp_path, request)


def test_explicit_user_materials_preserve_honest_access_and_no_public_url():
    ctx = outline_context()
    material = dict(material_id=uid(110), name='Provided notes', kind='markdown', sha256='e' * 64,
        text='Actual provided text.', access_level='full_text', doi=None, status='ready',
        parse_error=None, recorded_at=STAMP)
    ctx['materials'] = [material]
    snapshot = ctx['evidence_snapshot']
    snapshot['materials'] = [material]
    snapshot['selections'].append(dict(source_id=uid(110), included=True, reason=''))
    contracts.validate_context(ctx, task('outline', ctx))
    paper = next(p for p in contracts.source_papers(snapshot) if p['paper_id'] == uid(110))
    assert paper['availability']['full_text']['obtained_at'] == STAMP
    assert paper['url'] is None and paper['verification']['status'] == 'unknown'
    assert paper['sources'] == ['user_provided_material']
