"""Preserve captured failure values; validate v5 examples with host CAS.

The fixture captures the actual knowledge_proposals values, not a byte span of
the original write.content. Full original transport/output bytes stay in the
server's failure evidence; the captured null remains unmodified here.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from agent_service.animation_knowledge_contract import _proposal, validate_proposals
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_skill_method as contract
from video_workbench.animation_knowledge import AnimationKnowledgeStore
from test_animation_skill_method_profile import context, output


def documented_proposal(value):
    prompt = contract.VideoAnimationSkillMethodProfile().build_prompt({'input_sha256': digest(value)})
    example = re.search(r'```json\n(.*?)\n```', prompt, re.DOTALL)
    assert example is not None
    evidence_id = value['knowledge_snapshot']['experiences'][0]['id']
    return json.loads(example[1].replace('exp_synthetic_brief', evidence_id))


def test_captured_real_new_page_null_proposal_is_not_repaired_or_relaxed():
    fixture = Path(__file__).parent / 'fixtures/animation-knowledge-new-page-null.json'
    raw = fixture.read_bytes()
    failed = json.loads(raw)['knowledge_proposals'][0]
    assert failed['page_id'] == 'animation-evidence-boundaries'
    assert failed['expected_revision'] is None and failed['change_kind'] == 'create'
    before = canonical_bytes(failed)
    with pytest.raises(ServiceError, match='^invalid_animation_knowledge$'):
        _proposal(failed)
    assert canonical_bytes(failed) == before and fixture.read_bytes() == raw


def test_actual_prompt_example_supports_new_zero_and_empty_array_but_rejects_null(tmp_path):
    value = context(tmp_path)
    result = output(value)
    candidate = documented_proposal(value)
    assert type(candidate['expected_revision']) is int and candidate['expected_revision'] == 0
    result['knowledge_proposals'] = [candidate]
    assert contract.validate_result(result, digest(value), value) == result
    for invalid in (None, '0', False, 0.0, -1):
        changed = deepcopy(result)
        changed['knowledge_proposals'][0]['expected_revision'] = invalid
        before = canonical_bytes(changed)
        with pytest.raises(ServiceError, match='^invalid_animation_knowledge$'):
            contract.validate_result(changed, digest(value), value)
        assert canonical_bytes(changed) == before
    result['knowledge_proposals'] = []
    assert contract.validate_result(result, digest(value), value) == result


def test_existing_revision_and_relation_use_exact_frozen_page_and_content_hash(tmp_path):
    value = context(tmp_path)
    candidate = documented_proposal(value)
    wiki = AnimationKnowledgeStore(tmp_path / 'knowledge.sqlite3')
    page = wiki.revise_page('fixture-owner', candidate['page_id'], candidate['body'],
        expected_revision=0, evidence_ids=candidate['evidence_ids'],
        change_note=candidate['change_note'])
    snapshot = wiki.snapshot('fixture-owner', experience_ids=candidate['evidence_ids'])
    revision = dict(candidate, expected_revision=page['revision'], change_kind='correct')
    assert validate_proposals([revision], snapshot)[0]['expected_revision'] == page['revision']
    for wrong in (0, page['revision'] + 1):
        changed = dict(revision, expected_revision=wrong,
                       change_kind='create' if wrong == 0 else 'correct')
        with pytest.raises(ServiceError, match='snapshot_mismatch'):
            validate_proposals([changed], snapshot)
    relation = dict(relation='extends', page_id=page['page_id'], revision=page['revision'],
                    content_sha256=page['content_sha256'])
    related = dict(candidate, page_id='additional-scope', relations=[relation])
    assert validate_proposals([related], snapshot)[0]['relations'] == [relation]
    wrong_hash = dict(related, relations=[dict(relation, content_sha256=page['sha256'])])
    with pytest.raises(ServiceError, match='snapshot_mismatch'):
        validate_proposals([wrong_hash], snapshot)


def test_prompt_details_knowledge_types_without_changing_the_validator(tmp_path):
    prompt = contract.VideoAnimationSkillMethodProfile().build_prompt({'input_sha256': digest(context(tmp_path))})
    assert 'integer **0**' in prompt and 'null, false, true, "0", 0.0' in prompt
    assert 'copy its exact integer revision' in prompt
    assert 'not its sha256 or the snapshot hash' in prompt
    assert 'A page proposal\nis not independent evidence for another page' in prompt
    assert 'knowledge_proposals=[] whenever no evidence-backed Wiki change is needed' in prompt
    assert 'expected_revision=0, never null' in prompt
