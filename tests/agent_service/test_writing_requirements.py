"""Reported fixed-case loss, revision growth and midnight date regressions."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.research_context_view import build_context_view
from scientific_agent import workflow_contracts as contracts
from scientific_agent.writing_requirements import (assess_writing, body_text,
    length_target, literal_requirements, local_timestamp)
from test_research_workflow import (context, draft, draft_context, envelope,
    outline_context, plan_context, task)


def fixed_requirements():
    return dict(question='DSH、LangGraph与AutoGen架构选型，写约3000字报告。',
        include='使用同一固定案例：读取README.md和pyproject.toml，回答“如何安装依赖并启动项目”。',
        notes='不要求运行真实代码或基准测试。', exclude='不要展示private.json')


def confirmed_plan():
    existing = plan_context()
    plan = existing['plan']
    plan['content']['scope'] = fixed_requirements()['include']
    return dict(plan_id=plan['plan_id'], content=plan['content'],
                confirmation_id=existing['confirmation']['confirmation_id'])


def complete_report(output):
    output['report_markdown'] = output['report_markdown'].replace('...\n',
        '固定概念示例：读取README.md和pyproject.toml，回答“如何安装依赖并启动项目”。'
        '此处不宣称已读取实际仓库或运行代码。\n', 1)
    return output


def test_reported_fixed_case_is_required_from_requirements_and_confirmed_plan():
    expected = ['README.md', 'pyproject.toml', '如何安装依赖并启动项目']
    assert literal_requirements(fixed_requirements()) == expected
    assert literal_requirements(context()['requirements'], confirmed_plan()) == expected
    assert assess_writing('# 初稿\n泛化为读取配置文件。', fixed_requirements())['missing_requirements'] == expected


def test_files_from_attachments_urls_exclusions_and_searches_are_not_mandatory_body_topics():
    requirements = dict(question='DSH architecture comparison',
        include='', notes='请阅读上传的资料包dsh-review-evidence-pack.md。\n'
        '官方来源：https://github.com/example/project/blob/main/README.md。\n'
        '请读取docs/architecture.md作为研究来源。\n'
        '不要在正文展示private.json。', exclude='configuration.yaml')
    plan = confirmed_plan()
    plan['content']['scope'] = 'Compare the architectures.'
    plan['content']['search_queries'] = ['read setup.py examples']
    assert literal_requirements(requirements, plan) == []
    assert assess_writing('# Result\nAn architecture overview.', requirements, plan)['missing_requirements'] == []


def test_required_case_in_bibliography_or_hidden_comments_does_not_satisfy_body_coverage():
    required = fixed_requirements()
    literals = 'README.md pyproject.toml 如何安装依赖并启动项目'
    report = '# Report\nA different example.\n<!-- ' + literals + ' -->\n## References\n' + literals
    assert assess_writing(report, required)['missing_requirements'] == literal_requirements(required)
    assert assess_writing('# Report\nA different example.\n<!-- ' + literals,
                          required)['missing_requirements'] == literal_requirements(required)


def test_negative_filename_list_does_not_turn_its_second_item_into_a_requirement():
    assert literal_requirements(dict(include='Do not read README.md, pyproject.toml.')) == []
    assert literal_requirements(dict(include='不要提及private.json，而必须包含README.md。')) == ['README.md']


def test_output_filename_is_not_a_required_body_topic_but_fixed_case_is_preserved():
    assert literal_requirements(dict(include='报告输出为report.md。')) == []
    assert literal_requirements(dict(include='指定输出文件名为report.md。')) == []
    assert literal_requirements(dict(notes='Must save as review.md.')) == []
    assert literal_requirements(dict(question='Read README.md and write to report.md.')) == ['README.md']
    assert literal_requirements(dict(include='正文必须提及生成文件report.md，作为固定案例。')) == ['report.md']


@pytest.mark.parametrize('notes,expected,unit', [
    ('资料范围为2022—2026年，访问日期2026-11-04。', None, None),
    ('报告约3000字；摘要200字。', 3000, '汉字'),
    ('每节200字，全文约3000字。', 3000, '汉字'),
    ('约3千字报告。', 3000, '汉字'),
    ('Write a 3,000-word report.', 3000, 'words'),
    ('The downloaded file has 3000字节。', None, None),
])
def test_length_target_uses_writing_units_and_whole_report_not_years_or_section_budget(notes, expected, unit):
    target = length_target(dict(notes=notes))
    assert (target['target_length'] if target else None) == expected
    assert (target['unit'] if target else None) == unit


def test_revision_reports_whole_body_growth_without_mutating_parent_or_ignoring_bibliography_boundary():
    parent = '文' * 3106 + '\n\n## 参考文献\n' + '文' * 9000
    candidate = '文' * 4404 + '\n<!-- claim:internal -->\n\n## 参考文献\n' + '文' * 9000
    original = deepcopy((parent, candidate))
    measured = assess_writing(candidate, fixed_requirements(), parent_report=parent)
    assert measured['actual_length'] == 4404
    assert measured['parent_length'] == 3106
    assert measured['length_delta'] == 1298
    assert measured['target_length'] == 3000 and measured['unit'] == '汉字'
    assert any('精炼本节' in warning for warning in measured['warnings'])
    assert (parent, candidate) == original
    assert not assess_writing('文' * 3106, fixed_requirements())['warnings']
    assert assess_writing('文' * 3106, dict(notes='全文最多3000字。'))['warnings']


def test_literal_coverage_and_count_do_not_confuse_references_heading_in_code_with_bibliography():
    report = '# 正文\n```markdown\n## References\nREADME.md\n```\npyproject.toml\n## References\nignored.py'
    body = body_text(report)
    assert 'README.md' in body and 'pyproject.toml' in body and 'ignored.py' not in body
    hidden_heading = '<!--\n## References\n-->\nREADME.md\n'
    assert body_text(hidden_heading).strip() == 'README.md'


@pytest.mark.parametrize('heading', ['## 7. References', '## 参考文献 / References', 'References\n----------'])
def test_numbered_bilingual_and_setext_bibliography_cannot_satisfy_body_literals(heading):
    required = fixed_requirements()
    report = '# 正文\n泛化例子。\n\n' + heading + '\nREADME.md pyproject.toml 如何安装依赖并启动项目'
    assert assess_writing(report, required)['missing_requirements'] == literal_requirements(required)


def test_report_title_about_references_is_not_mistaken_for_bibliography():
    report = '# DSH参考文献综述\n固定案例：README.md、pyproject.toml，如何安装依赖并启动项目。'
    assert assess_writing(report, fixed_requirements())['missing_requirements'] == []
    assert assess_writing(report, fixed_requirements())['actual_length'] > 0
    assert body_text('# References for software engineers\nREADME.md').endswith('README.md')


def test_repeated_limitations_are_a_review_hint_never_a_silent_rewrite():
    report = '\n\n'.join('证据不足，暂不作实现断言。' for _ in range(8))
    measured = assess_writing(report, {})
    assert any('集中说明' in item for item in measured['warnings'])
    assert measured['actual_length'] > 0 and measured['missing_requirements'] == []
    assert report.count('证据不足') == 8


def test_cross_midnight_local_date_preserves_recorded_instant_and_unknown_remains_unknown():
    assert local_timestamp('2026-11-03T16:55:00Z') == '2026-11-04T00:55:00+08:00'
    assert local_timestamp('2026-11-03T16:55:00+00:00') == '2026-11-04T00:55:00+08:00'
    assert local_timestamp('2026-11-04') is None
    assert local_timestamp('unavailable') is None
    assert local_timestamp(None) is None
    assert local_timestamp('9999-12-31T23:59:59Z') is None


def test_legacy_context_remains_valid_but_new_context_binds_confirmed_plan_to_digest():
    legacy = draft_context()
    contracts.validate_context(legacy, task('draft', legacy))
    current = draft_context()
    current['confirmed_plan'] = confirmed_plan()
    frozen = task('draft', current)
    contracts.validate_context(current, frozen)
    current['confirmed_plan']['content']['scope'] = 'Changed after admission'
    with pytest.raises(contracts.WorkflowContractError, match='context_mismatch'):
        contracts.validate_context(current, frozen)


@pytest.mark.parametrize('mutate', [
    lambda item: item.pop('confirmation_id'),
    lambda item: item.update(confirmation_id='not-a-uuid'),
    lambda item: item.update(plan_id='not-a-uuid'),
    lambda item: item['content'].update(scope=''),
])
def test_optional_confirmed_plan_requires_complete_recorded_identity_and_valid_plan(mutate):
    current = draft_context()
    current['confirmed_plan'] = confirmed_plan()
    mutate(current['confirmed_plan'])
    with pytest.raises(contracts.WorkflowContractError):
        contracts.validate_context(current, task('draft', current))


def test_context_view_supplies_exact_confirmed_case_and_user_timezone_without_rewriting_source():
    current = outline_context()
    current['confirmed_plan'] = confirmed_plan()
    batch = current['evidence_snapshot']['search_batches'][0]
    for paper in (batch['papers'][0], batch['observations']['observations'][0]['response']['papers'][0]):
        paper['retrieved_at'] = '2026-11-03T16:55:00Z'
    before = deepcopy(current)
    raw = contracts.canonical_bytes(current)
    files = build_context_view(current, task('outline', current), input_bytes_sha256=hashlib.sha256(raw).hexdigest())
    start = json.loads(files['START.json'])
    writing = json.loads(files[start['writing_requirements']])
    assert writing['confirmed_plan'] == before['confirmed_plan']
    assert writing['required_literals'] == ['README.md', 'pyproject.toml', '如何安装依赖并启动项目']
    assert writing['reporting_timezone'] == 'Asia/Shanghai'
    assert writing['recorded_source_times'][0]['recorded_instant'] == '2026-11-03T16:55:00Z'
    assert writing['recorded_source_times'][0]['local'] == '2026-11-04T00:55:00+08:00'
    assert writing['execution_time_is_not_source_date'] is True
    assert current == before


@pytest.mark.parametrize('literal_origin', ['requirements', 'confirmed_plan'])
def test_draft_validator_rejects_reported_case_loss_and_accepts_conceptual_example(literal_origin):
    current = draft_context()
    if literal_origin == 'requirements':
        current['requirements'].update(fixed_requirements())
    else:
        current['confirmed_plan'] = confirmed_plan()
    with pytest.raises(contracts.WorkflowContractError, match='workflow_writing_requirement_missing'):
        contracts.validate_stage_output('draft', draft(), current)
    good = complete_report(draft())
    contracts.validate_stage_output('draft', good, current)
    assert good['claims'] == draft()['claims'] and good['references'] == draft()['references']


def test_stopped_draft_collector_enforces_case_before_publishing_artifacts(tmp_path):
    current = draft_context()
    current['confirmed_plan'] = confirmed_plan()
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'result').mkdir()
    (tmp_path / 'inputs/context.json').write_bytes(contracts.canonical_bytes(current))
    output_path = tmp_path / 'result/output.json'
    output_path.write_bytes(contracts.canonical_bytes(draft()))
    execution = envelope('draft', current)
    proof = StopEvidence(execution.attempt_id, True, ('offline-stopped-writer',))
    profile = implementation(execution.profile)
    with pytest.raises(ServiceError) as missing:
        profile.collect_output(tmp_path, execution, proof)
    assert missing.value.code == 'workflow_writing_requirement_missing'
    output_path.write_bytes(contracts.canonical_bytes(complete_report(draft())))
    artifacts = profile.collect_output(tmp_path, execution, proof)
    assert any(item.name == 'report.md' for item in artifacts)
