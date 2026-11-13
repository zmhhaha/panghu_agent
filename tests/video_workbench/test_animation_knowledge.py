"""Wiki custody/CAS tests; synthetic records do not imply artistic approval."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.animation_knowledge_contract import (
    MAX_EXPERIENCE_BYTES, SNAPSHOT_SCHEMA, validate_snapshot, validate_proposals,
)
from video_workbench.animation_knowledge import AnimationKnowledgeStore


@pytest.fixture
def store(tmp_path):
    return AnimationKnowledgeStore(tmp_path / 'knowledge.sqlite3')


def experience(store, source='render-failure', owner='alice', kind='failure'):
    return store.record_experience(owner, kind, source, dict(
        run_id=source, status='failed', error='Async draw rejected.',
        observation='Synthetic persistence fixture only.',
        source_url='https://www.bilibili.com/video/example'))


def body(content='Await actual draw completion before taking a frame.'):
    return dict(title='Asynchronous drawing', content=content,
                applicability=['Native hf-seek listeners'], limitations=['No visual quality conclusion.'])


def proposal(record_id, page_id='async-draw', revision=0, **changes):
    result = dict(page_id=page_id, expected_revision=revision, body=body(),
                  evidence_ids=[record_id], change_note='Preserve this actual failure and a provisional lesson.')
    result.update(changes)
    return result


def revise(store, record_id, page_id='async-draw', revision=0, **changes):
    value = proposal(record_id, page_id, revision, **changes)
    return store.revise_page('alice', value.pop('page_id'), value.pop('body'), **value)


def seal(value):
    value = deepcopy(value)
    value.pop('sha256', None)
    return dict(value, sha256=digest(value))


def test_archive_preserves_failures_feedback_urls_and_no_derived_quality(store):
    original = experience(store)
    assert original['document']['status'] == 'failed'
    assert original['document']['source_url'].startswith('https://')
    assert set(original['document']) == {'run_id', 'status', 'error', 'observation', 'source_url'}
    feedback = store.record_experience('alice', 'feedback', 'feedback-1', {'comment': 'Motion still feels mechanical.'})
    assert feedback['document'] == {'comment': 'Motion still feels mechanical.'}
    original['document']['error'] = 'Caller mutated its copy'
    assert store.get_experience('alice', original['id'])['document']['error'] == 'Async draw rejected.'
    assert len(store.list_experiences('alice')) == 2
    assert len(store.list_experiences('alice', kind='feedback')) == 1


def test_raw_archive_is_idempotent_and_restart_safe(store):
    record = experience(store)
    reopened = AnimationKnowledgeStore(store.path)
    assert experience(reopened) == record
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        reopened.record_experience('alice', 'failure', 'render-failure', {'error': 'A different failure'})
    assert reopened.get_experience('alice', record['id']) == record


def test_owner_isolation_all_read_write_references(store):
    alice = experience(store)
    bob = experience(store, owner='bob')
    assert alice['id'] != bob['id']
    assert store.list_experiences('charlie') == []
    with pytest.raises(ServiceError, match='not_found'):
        store.get_experience('bob', alice['id'])
    with pytest.raises(ServiceError, match='not_found'):
        store.revise_page('bob', 'async-draw', body(), expected_revision=0,
                          evidence_ids=[alice['id']], change_note='Cannot cite another owner.')
    page = revise(store, alice['id'])
    assert store.list_pages('bob') == []
    with pytest.raises(ServiceError, match='not_found'):
        store.get_page('bob', page['page_id'])
    snap = store.snapshot('alice')
    with pytest.raises(ServiceError, match='not_found'):
        store.get_snapshot('bob', snap['id'])
    with pytest.raises(ServiceError, match='not_found'):
        store.snapshot('bob', experience_ids=[alice['id']])


def test_revision_chain_counterexamples_scope_correction_and_deprecation(store):
    failed = experience(store)
    first = revise(store, failed['id'])
    assert first['revision'] == 1 and first['status'] == 'provisional'
    assert first['change_kind'] == 'create' and first['previous_revision_sha256'] is None
    feedback = experience(store, source='feedback-2', kind='feedback')
    richer = body('Await hf-seek work; a delay alone does not establish a completed frame.')
    richer['counterexamples'] = [dict(description='A fixed timeout still captured a blank frame.',
                                       evidence_ids=[feedback['id']])]
    second = revise(store, failed['id'], revision=1, body=richer,
                    evidence_ids=[failed['id'], feedback['id']], change_kind='correct',
                    change_note='The feedback contradicts the earlier general advice.')
    assert second['previous_revision_sha256'] == first['sha256']
    assert store.get_page('alice', 'async-draw', revision=1) == first
    third = revise(store, feedback['id'], revision=2, status='deprecated', change_kind='deprecate',
                   change_note='Kept for history; superseded by corrected frame protocol.')
    assert third['status'] == 'deprecated'
    assert store.page_history('alice', 'async-draw') == [first, second, third]
    assert store.list_pages('alice', status='deprecated') == [third]
    assert store.snapshot('alice')['pages'] == []
    assert store.snapshot('alice', page_ids=['async-draw'])['pages'] == [third]


def test_revision_cas_and_stable_retry_do_not_overwrite_new_head(store):
    record = experience(store)
    first = revise(store, record['id'], operation_id='initial-request')
    second = revise(store, record['id'], revision=1, body=body('An actual correction.'), operation_id='next-request')
    assert revise(store, record['id'], operation_id='initial-request') == first
    assert revise(AnimationKnowledgeStore(store.path), record['id'], operation_id='initial-request') == first
    assert store.get_page('alice', 'async-draw') == second
    with pytest.raises(ServiceError, match='revision_conflict'):
        revise(store, record['id'], revision=0, operation_id='another-create')
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        revise(store, record['id'], body=body('Altered replay.'), operation_id='initial-request')
    assert len(store.page_history('alice', 'async-draw')) == 2


def test_default_revision_operation_id_is_restart_idempotent(store):
    record = experience(store)
    first = revise(store, record['id'])
    assert revise(AnimationKnowledgeStore(store.path), record['id']) == first


def test_relations_pin_exact_owner_local_version(store):
    record = experience(store)
    target = revise(store, record['id'], page_id='target')
    relation = dict(relation='contradicts', page_id='target', revision=1, content_sha256=target['content_sha256'])
    page = revise(store, record['id'], page_id='second', relations=[relation])
    assert page['relations'] == [relation]
    bad = dict(relation, content_sha256='f' * 64)
    with pytest.raises(ServiceError, match='relation_changed'):
        revise(store, record['id'], page_id='third', relations=[bad])
    assert [item['page_id'] for item in store.list_pages('alice')] == ['second', 'target']


def test_snapshot_freezes_full_versions_and_evidence_without_rereading_live_heads(store):
    record = experience(store)
    first = revise(store, record['id'])
    snapshot = store.snapshot('alice')
    assert snapshot['schema'] == SNAPSHOT_SCHEMA
    assert snapshot['pages'] == [first] and snapshot['experiences'] == [record]
    assert store.snapshot('alice') == snapshot
    revised = revise(store, record['id'], revision=1, body=body('Changed current wiki.'))
    assert store.snapshot('alice')['id'] != snapshot['id']
    assert store.get_snapshot('alice', snapshot['id'])['pages'] == [first]
    assert AnimationKnowledgeStore(store.path).get_snapshot('alice', snapshot['id']) == snapshot
    snapshot['pages'][0]['body']['content'] = 'Caller changed returned snapshot'
    assert store.get_snapshot('alice', snapshot['id'])['pages'][0]['body'] == first['body']
    assert store.get_page('alice', 'async-draw') == revised


def test_snapshot_accepts_raw_experiences_before_first_wiki_page(store):
    record = experience(store)
    snapshot = store.snapshot('alice', experience_ids=[record['id']])
    assert snapshot['pages'] == [] and snapshot['experiences'] == [record]
    assert validate_snapshot(snapshot, owner='alice') == snapshot
    assert store.snapshot('bob')['experiences'] == []


def test_proposals_archive_then_atomic_apply_and_restart_replay(store):
    record = experience(store)
    snap = store.snapshot('alice', experience_ids=[record['id']])
    proposals = [proposal(record['id']), proposal(record['id'], 'second')]
    candidate = store.record_proposals('alice', 'actual-author-run', proposals, snapshot_id=snap['id'])
    assert candidate['kind'] == 'knowledge_proposal'
    assert store.list_pages('alice') == []
    assert store.record_proposals('alice', 'actual-author-run', proposals, snapshot_id=snap['id']) == candidate
    pages = store.apply_proposals('alice', candidate['id'])
    assert [page['page_id'] for page in pages] == ['async-draw', 'second']
    assert all(page['status'] == 'provisional' for page in pages)
    second = revise(store, record['id'], revision=1, body=body('Newer direct correction.'))
    assert AnimationKnowledgeStore(store.path).apply_proposals('alice', candidate['id']) == pages
    assert store.get_page('alice', 'async-draw') == second
    assert not hasattr(store, 'publish')


def test_candidate_group_cas_conflict_rolls_back_all_pages_and_retains_candidate(store):
    record = experience(store)
    first = revise(store, record['id'], page_id='existing')
    snap = store.snapshot('alice')
    candidate = store.record_proposals('alice', 'stale-agent-result', [
        proposal(record['id'], 'would-be-new'), proposal(record['id'], 'existing', revision=1)], snapshot_id=snap['id'])
    current = revise(store, record['id'], page_id='existing', revision=1, body=body('Concurrent correction.'))
    with pytest.raises(ServiceError, match='revision_conflict'):
        store.apply_proposals('alice', candidate['id'])
    assert store.get_experience('alice', candidate['id']) == candidate
    assert store.get_page('alice', 'existing') == current
    with pytest.raises(ServiceError, match='not_found'):
        store.get_page('alice', 'would-be-new')
    assert len(store.page_history('alice', 'existing')) == 2


def test_candidate_can_correct_then_deprecate_wiki_without_publishing_skill(store):
    record = experience(store)
    first = revise(store, record['id'])
    snap = store.snapshot('alice')
    candidate = store.record_proposals('alice', 'correction', [proposal(record['id'], revision=1,
        change_kind='correct', body=body('The observed failure applies only to asynchronous draws.'))], snapshot_id=snap['id'])
    correction = store.apply_proposals('alice', candidate['id'])[0]
    newer = store.snapshot('alice')
    retired = store.record_proposals('alice', 'retire', [proposal(record['id'], revision=2,
        status='deprecated', change_kind='deprecate', change_note='Retain but stop recommending this version.')], snapshot_id=newer['id'])
    result = store.apply_proposals('alice', retired['id'])[0]
    assert result['revision'] == 3 and result['status'] == 'deprecated'
    assert correction['previous_revision_sha256'] == first['sha256']


def test_empty_candidate_group_is_idempotent(store):
    snap = store.snapshot('alice')
    candidate = store.record_proposals('alice', 'no-lessons', [], snapshot_id=snap['id'])
    assert store.apply_proposals('alice', candidate['id']) == []
    assert AnimationKnowledgeStore(store.path).apply_proposals('alice', candidate['id']) == []


@pytest.mark.parametrize('bad', ['unseen_evidence', 'missing_evidence', 'wrong_revision', 'duplicate_page',
                                'unseen_relation', 'wrong_relation_hash', 'candidate_as_evidence'])
def test_candidate_must_bind_to_frozen_snapshot_not_current_live_data(store, bad):
    record = experience(store)
    page = revise(store, record['id'])
    snap = store.snapshot('alice')
    values = [proposal(record['id'], revision=1)]
    if bad == 'unseen_evidence':
        newer = experience(store, source='a-newer-failure')
        values[0]['evidence_ids'] = [newer['id']]
    elif bad == 'missing_evidence':
        values[0]['evidence_ids'] = ['exp_' + 'a' * 64]
    elif bad == 'wrong_revision':
        values[0]['expected_revision'] = 2
    elif bad == 'duplicate_page':
        values *= 2
    elif bad in {'unseen_relation', 'wrong_relation_hash'}:
        values[0]['relations'] = [dict(relation='extends', page_id='unknown' if bad == 'unseen_relation' else page['page_id'],
                                      revision=1, content_sha256='f' * 64)]
    else:
        old = store.record_proposals('alice', 'prior-candidate', [], snapshot_id=snap['id'])
        snap = store.snapshot('alice', experience_ids=[old['id']])
        values[0]['evidence_ids'] = [old['id']]
    with pytest.raises(ServiceError):
        store.record_proposals('alice', 'bad-result', values, snapshot_id=snap['id'])
    assert store.get_page('alice', 'async-draw') == page


@pytest.mark.parametrize('field,value', [('status', []), ('status', 'published'), ('change_kind', 'publish'),
    ('expected_revision', True), ('evidence_ids', []), ('relations', 'not-a-list'), ('body', {'title': 'Missing content'})])
def test_invalid_candidate_shapes_fail_closed(store, field, value):
    record = experience(store)
    snap = store.snapshot('alice', experience_ids=[record['id']])
    candidate = proposal(record['id'])
    candidate[field] = value
    with pytest.raises(ServiceError):
        validate_proposals([candidate], snap)


def test_counterexample_requires_actual_cited_experience(store):
    record = experience(store)
    invalid = body()
    invalid['counterexamples'] = [dict(description='Claim with no cited raw evidence.', evidence_ids=['missing'])]
    with pytest.raises(ServiceError):
        revise(store, record['id'], body=invalid)


@pytest.mark.parametrize('target', ['page', 'experience', 'snapshot'])
def test_changed_persisted_bytes_are_rejected(store, target):
    record = experience(store)
    page = revise(store, record['id'])
    snap = store.snapshot('alice')
    table, key, value = ({'experience': ('knowledge_experiences', 'id', record),
                          'page': ('knowledge_revisions', 'page_id', page),
                          'snapshot': ('knowledge_snapshots', 'id', snap)})[target]
    changed = deepcopy(value)
    changed['created_at'] += 1
    identity = value['page_id'] if target == 'page' else value['id']
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE ' + table + ' SET record=? WHERE owner=? AND ' + key + '=?',
                   (canonical_bytes(changed), 'alice', identity))
    with pytest.raises(ServiceError, match='record_changed'):
        if target == 'experience':
            store.get_experience('alice', record['id'])
        elif target == 'page':
            store.get_page('alice', page['page_id'])
        else:
            store.get_snapshot('alice', snap['id'])


def test_snapshot_rejects_embedded_cross_owner_or_changed_version_even_if_outer_resealed(store):
    record = experience(store)
    revise(store, record['id'])
    snap = store.snapshot('alice')
    bad = deepcopy(snap)
    bad['experiences'][0]['owner'] = 'bob'
    bad['experiences'][0] = seal(bad['experiences'][0])
    with pytest.raises(ServiceError):
        validate_snapshot(seal(bad))
    bad = deepcopy(snap)
    bad['pages'][0]['body']['content'] = 'Changed content without changing version hash'
    bad['pages'][0] = seal(bad['pages'][0])
    with pytest.raises(ServiceError, match='record_changed'):
        validate_snapshot(seal(bad))


def test_independent_database_refuses_other_application_tables(tmp_path):
    path = tmp_path / 'business.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects (owner TEXT)')
    store = AnimationKnowledgeStore(path)
    with pytest.raises(ServiceError, match='database_not_isolated'):
        experience(store)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('projects',)]


def test_invalid_json_and_byte_budget_do_not_write_archive(store):
    for document in ({'value': float('nan')}, {'value': object()}, {'value': '\ud800'}):
        with pytest.raises(ServiceError):
            store.record_experience('alice', 'failure', 'bad-json', document)
    with pytest.raises(ServiceError, match='too_large'):
        store.record_experience('alice', 'failure', 'too-large', {'text': 'x' * MAX_EXPERIENCE_BYTES})
    assert store.list_experiences('alice') == []


def test_concurrent_revision_cas_has_one_winner_and_no_lost_update(store):
    record = experience(store)
    revise(store, record['id'])
    def update(index):
        try:
            return revise(AnimationKnowledgeStore(store.path), record['id'], revision=1,
                          body=body('Concurrent correction ' + str(index)), operation_id='request-' + str(index))
        except ServiceError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, [1, 2]))
    assert sum(type(value) is dict for value in results) == 1
    assert results.count('animation_knowledge_revision_conflict') == 1
    assert len(store.page_history('alice', 'async-draw')) == 2


def test_concurrent_archive_replay_returns_one_immutable_experience(store):
    def write(_):
        return experience(AnimationKnowledgeStore(store.path))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [1, 2]))
    assert results[0] == results[1]
    assert store.list_experiences('alice') == [results[0]]


def test_raw_caller_cannot_impersonate_validated_proposal_record(store):
    with pytest.raises(ServiceError, match='proposal_api_required'):
        store.record_experience('alice', 'knowledge_proposal', 'unvalidated', {'proposals': []})
