"""Independent cross-layer recovery/release checks with inert offline fixtures.

These tests exercise persistence boundaries, not actual model, media or human
observations. They deliberately do not assert aesthetic or animation quality.
"""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_service import AnimationMethodService
from test_animation_skill_method_service import SkillGateway
from test_animation_method_service import Renderer, finish, start


@pytest.fixture
def work(tmp_path):
    author, reviewer, renderer = SkillGateway(), SkillGateway(True), Renderer(tmp_path / 'renders')
    service = AnimationMethodService(tmp_path / 'lab', author_gateway=author,
                                    review_gateway=reviewer, renderer=renderer)
    return service, author, reviewer, renderer


def completed_plan(service, run_id):
    for _ in range(3):
        service.advance('developer', run_id)
    run = service.commissions.get('developer', run_id)
    plan = next(operation for operation in run['operations'] if operation['input'].get('mode') == 'plan')
    assert plan['status'] == 'completed'
    assert service.knowledge.list_pages('developer') == []
    return run, plan


def test_wiki_apply_committed_then_ack_lost_recovers_once_without_another_model_call(work, monkeypatch):
    service, author, reviewer, renderer = work
    queued = start(service)
    run, plan = completed_plan(service, queued['id'])
    original = service.knowledge.apply_proposals
    def lost_ack(owner, proposal_id):
        result = original(owner, proposal_id)
        raise OSError('Synthetic interruption after Wiki commit, before caller acknowledgement')
    monkeypatch.setattr(service.knowledge, 'apply_proposals', lost_ack)
    with pytest.raises(OSError):
        service.advance('developer', run['id'])
    first = service.knowledge.get_page('developer', 'causal-identity')
    assert first['revision'] == 1
    assert len(author.submissions) == 1 and reviewer.submissions == renderer.calls == []
    recovered = AnimationMethodService(service.root, author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    recovered.advance('developer', run['id'])
    assert recovered.knowledge.get_page('developer', 'causal-identity') == first
    assert len(recovered.knowledge.page_history('developer', 'causal-identity')) == 1
    assert len(author.submissions) == 1 and reviewer.submissions == renderer.calls == []
    candidates = [row for row in recovered.knowledge.list_experiences('developer') if row['kind'] == 'knowledge_proposal']
    assert len(candidates) == 1 and candidates[0]['source_id'] == plan['id']
    assert finish(recovered, run['id'])['state'] == 'completed'


def test_stale_wiki_candidate_survives_conflict_and_restart_without_overwriting_or_duplicate_records(work):
    service, author, reviewer, renderer = work
    run, plan = completed_plan(service, start(service)['id'])
    raw = run['operations'][0]['input']['knowledge_snapshot']['experiences'][0]
    actual = service.knowledge.revise_page('developer', 'causal-identity',
        {'title': 'A concurrent correction', 'content': 'Preserve this independently stored version.'},
        expected_revision=0, evidence_ids=[raw['id']], change_note='Synthetic concurrent edit.')
    service.advance('developer', run['id'])
    experiences = service.knowledge.list_experiences('developer')
    assert len([row for row in experiences if row['kind'] == 'knowledge_conflict']) == 1
    candidate = next(row for row in experiences if row['kind'] == 'knowledge_proposal')
    restarted = AnimationMethodService(service.root, author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    restarted._sync_knowledge('developer', restarted.commissions.get('developer', run['id']))
    assert restarted.knowledge.get_page('developer', 'causal-identity') == actual
    assert len(restarted.knowledge.page_history('developer', 'causal-identity')) == 1
    assert restarted.knowledge.get_experience('developer', candidate['id']) == candidate
    assert len(restarted.knowledge.list_experiences('developer')) == len(experiences)
    assert len(author.submissions) == 1 and reviewer.submissions == renderer.calls == []


def test_reference_free_transfer_is_independent_of_oversized_live_wiki(work):
    service, author, reviewer, renderer = work
    reference = finish(service, start(service)['id'])
    for index in range(20):
        service.knowledge.record_experience('developer', 'large_offline_fixture', str(index), {'text': 'x' * 230000})
    before = len(author.submissions), len(reviewer.submissions), len(renderer.calls)
    body = dict(request_id=str(uuid4()), goal='Explain a competition among branching routes',
        structural_changes={'relations': {'before': 'an intervention chain', 'after': 'competing branching routes'}},
        content_changes={'before': reference['goal'], 'after': 'Explain a competition among branching routes',
                         'reason': 'Different explanatory content and relationship topology.'},
        budget={'max_rounds': 1, 'max_seconds': 300})
    queued = service.transfer('developer', reference['id'], body)
    run = service.commissions.get('developer', queued['id'])
    for mode in ('plan', 'create'):
        document = service._document('developer', run, mode)
        assert document['knowledge_snapshot'] is None and document['research_records'] == []
        assert document['reference'] is None and document['base_source'] is None
    assert run['frozen']['knowledge_snapshot_id'] is None
    assert before == (len(author.submissions), len(reviewer.submissions), len(renderer.calls))


@pytest.mark.parametrize('invalid', ['media', 'validation', 'unwatched'])
def test_v2_acceptance_rejects_another_media_or_validation_or_unwatched_sample(work, invalid):
    service, _, _, _ = work
    row = finish(service, start(service)['id'])
    method_id = row['publication_method_version_ids'][0]
    validation = service.methods.list_validations('developer', method_id)[0]
    demonstration = service.methods.get_demonstration('developer', validation['demonstration_version_id'])
    human = dict(criteria_ids=[criterion['id'] for criterion in row['criteria']], motion_watched=True,
        audio_listened=False, validation_target_sha256=validation['acceptance_target_sha256'],
        media_sha256=demonstration['media']['sha256'])
    if invalid == 'media':
        human['media_sha256'] = 'f' * 64
    elif invalid == 'validation':
        human['validation_target_sha256'] = 'f' * 64
    else:
        human['motion_watched'] = False
    with pytest.raises(ServiceError, match='acceptance_target_mismatch|human_motion_required'):
        service.accept_validation('developer', validation['id'], dict(request_id=str(uuid4()),
            method_content_sha256=validation['method_content_sha256'], comment='Synthetic negative decision.',
            human_verification=human))
    assert all(value['acceptance'] is None for value in service.methods.list_validations('developer', method_id))
    assert service.published_catalogue('developer')['methods'] == []


def test_pending_candidate_cannot_apply_new_wiki_after_its_feedback_evidence_was_withdrawn(work):
    service, _, _, _ = work
    reference = finish(service, start(service)['id'])
    feedback = service.feedback('developer', reference['id'], dict(request_id=str(uuid4()),
        candidate_id=reference['best_candidate_id'], comment='Synthetic opinion that will be withdrawn.'))
    record = next(value for value in service.knowledge.list_experiences('developer')
                  if value['kind'] == 'developer_feedback' and value['source_id'] == feedback['id'])
    snapshot = service._knowledge_snapshot('developer')
    frozen = dict(protocol_schema='video.animation-method-input.v2', goal='Synthetic delayed result')
    proposal = dict(page_id='withdrawn-conclusion', expected_revision=0,
        body=dict(title='Dependent opinion', content='This provisional claim depends on withdrawn feedback.'),
        evidence_ids=[record['id']], change_note='Delayed synthetic Agent result.')
    operation = dict(id='synthetic-delayed-operation', status='completed', stage='creator',
        input_sha256='a' * 64, result_sha256='b' * 64,
        input={'knowledge_snapshot': snapshot, 'observation': {'frames': []}},
        result={'mode': 'plan', 'review': None, 'knowledge_proposals': [proposal]})
    run = dict(id='synthetic-delayed-commission', frozen=frozen, operations=[operation])
    service.withdraw_feedback('developer', reference['id'], dict(request_id=str(uuid4()), feedback_id=feedback['id']))
    service._sync_knowledge('developer', deepcopy(run))
    with pytest.raises(ServiceError, match='not_found'):
        service.knowledge.get_page('developer', proposal['page_id'])
    candidates = [value for value in service.knowledge.list_experiences('developer')
                  if value['kind'] == 'knowledge_proposal' and value['source_id'] == operation['id']]
    assert len(candidates) == 1
    conflicts = [value for value in service.knowledge.list_experiences('developer')
                 if value['kind'] == 'knowledge_conflict' and value['document']['proposal_id'] == candidates[0]['id']]
    assert len(conflicts) == 1 and conflicts[0]['document']['error'] == 'animation_method_knowledge_evidence_withdrawn'
    current = service._knowledge_snapshot('developer')
    assert record['id'] not in {value['id'] for value in current['experiences']}
    assert any(value['kind'] == 'feedback_withdrawal' and value['document']['feedback_id'] == feedback['id']
               for value in current['experiences'])
