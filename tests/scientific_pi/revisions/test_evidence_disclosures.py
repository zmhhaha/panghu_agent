"""Access wording must distinguish acquired abstracts from acquired full text."""
import pytest

from scientific_agent.pi_revision_validation import _disclosures, RevisionValidationError


def recorded_abstract_only():
    return dict(papers=[dict(availability=dict(full_text=dict(available=False)))],
        evidence=[dict(access_level='abstract')], claims=[])


@pytest.mark.parametrize('wording', [
    '已获取摘要，无全文。',
    '已获取摘要,无全文。',
    '## References\n[1] Synthetic source. 已获取摘要，无全文。',
    '摘要可用；没有已获取全文的证据。',
    '没有阅读全文。We have not reviewed the full texts.',
    'We have not, to date, reviewed the full texts; abstract-only evidence.',
    '全部十项来源均无已获取全文。',
    '全部来源均无已经获取全文。',
    '这些来源无已阅读全文的记录。',
    '这些来源均无已获取部分全文。',
])
def test_honest_abstract_access_and_local_fulltext_denials_pass(wording):
    _disclosures('摘要 / abstract evidence only.\n' + wording, recorded_abstract_only())


@pytest.mark.parametrize('wording', [
    '已获取全文。',
    '已经获取全文。',
    '已获取相关文献全文。',
    '已阅读全文。',
    '已获取摘要，无全文；但随后已获取全文。',
    '未获取摘要，但已阅读全文。',
    '未获取摘要，已获取全文。',
    '未获取摘要,已获取全文。',
    '已获取全文，摘要尚未获得。',
    '没有获取全文，但已阅读全部文献全文。',
    'We did not read abstracts, but we reviewed the full texts.',
    'We did not read abstracts, we reviewed the full texts.',
    'No abstract is available; full texts were reviewed.',
    '## References\n[1] Synthetic source. 已获取全文。',
    '全部十项来源均无已获取全文且本报告已阅读全文。',
    '全部十项来源均无已获取全文，但随后已获取全文。',
    '无摘要来源且已获取全文。',
    '无疑已获取全文。',
    '无障碍已获取全文。',
    '并非全部十项来源均无已获取全文。',
    '不是全部十项来源均无已获取全文。',
    '并不是全部十项来源均无已获取全文。',
    '不代表全部来源均无已获取全文。',
    '非全部来源均无已获取全文。',
    '没有全部来源均无已获取全文的情况。',
    '从未出现全部来源均无已获取全文的情况。',
    '未必全部来源均无已获取全文。',
    '不能断言全部来源均无已获取全文。',
    '未获得摘要且全部来源均无已获取全文且本报告已阅读全文。',
    '无已获取全文且已阅读全文。',
    '无 已获取全文 且 已阅读全文。',
    '来源无已获取部分全文且已阅读全文。',
    '均无已经获取全文且已经阅读全文。',
    '无已获取全文且已分析全文。',
    '全部来源均无已获取全文且已获取全文。',
])
def test_unsupported_fulltext_assertions_remain_rejected_across_local_contrasts(wording):
    with pytest.raises(RevisionValidationError) as failure:
        _disclosures('摘要 / abstract evidence only.\n' + wording, recorded_abstract_only())
    assert failure.value.code == 'missing_evidence_disclosure'
