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
])
def test_unsupported_fulltext_assertions_remain_rejected_across_local_contrasts(wording):
    with pytest.raises(RevisionValidationError) as failure:
        _disclosures('摘要 / abstract evidence only.\n' + wording, recorded_abstract_only())
    assert failure.value.code == 'missing_evidence_disclosure'
