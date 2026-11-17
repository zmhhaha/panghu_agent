"""Display-choice custody/CAS tests; no media or model execution is implied."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_effect_adoption import AnimationEffectAdoptionStore, MAX_REVISION


@pytest.fixture
def store(tmp_path):
    return AnimationEffectAdoptionStore(tmp_path / 'library.sqlite3', clock=lambda: 1700000000)


def adopt(store, *, owner='alice', effect='effect-1', commission='fresh-1',
          candidate='candidate-2', revision=0, request='request-1'):
    return store.adopt(owner, effect, commission, candidate, revision, request)


def initialize(store, owner='alice', effect='effect-1'):
    return store.ensure(owner, effect, 'original', 'candidate-1')


def counts(store):
    with sqlite3.connect(store.path) as db:
        return tuple(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
            for table in ('animation_effect_adoption_heads', 'animation_effect_adoption_requests'))


def test_fresh_completion_cannot_change_initialized_default_even_after_restart(store):
    assert store.get('alice', 'effect-1') is None
    original = initialize(store)
    assert original == dict(revision=0, commission_id='original', candidate_id='candidate-1')
    reopened = AnimationEffectAdoptionStore(store.path)
    assert reopened.ensure('alice', 'effect-1', 'fresh-1', 'candidate-2') == original
    assert reopened.get('alice', 'effect-1') == original
    assert counts(store) == (1, 0)
    original['candidate_id'] = 'caller mutation'
    assert reopened.get('alice', 'effect-1')['candidate_id'] == 'candidate-1'


def test_explicit_adoption_is_versioned_and_idempotent_without_reverting_newer_choice(store):
    initialize(store)
    first = adopt(store)
    assert first == dict(revision=1, commission_id='fresh-1', candidate_id='candidate-2')
    second = adopt(store, commission='fresh-2', candidate='candidate-3', revision=1, request='request-2')
    assert second['revision'] == 2
    reopened = AnimationEffectAdoptionStore(store.path)
    assert adopt(reopened) == first
    assert reopened.get('alice', 'effect-1') == second
    assert reopened.ensure('alice', 'effect-1', 'original', 'candidate-1') == second
    assert counts(store) == (1, 2)


def test_an_explicit_same_candidate_decision_advances_revision_only_once(store):
    initialize(store)
    result = adopt(store, commission='original', candidate='candidate-1')
    assert result['revision'] == 1
    assert adopt(store, commission='original', candidate='candidate-1') == result
    assert counts(store) == (1, 1)


@pytest.mark.parametrize('changes', [dict(effect='effect-2'), dict(commission='different'),
    dict(candidate='different'), dict(revision=1)])
def test_request_key_cannot_be_rebound_to_different_input(store, changes):
    initialize(store)
    first = adopt(store)
    with pytest.raises(ServiceError, match='animation_effect_adoption_idempotency_conflict') as error:
        adopt(store, **changes)
    assert error.value.status == 409
    assert store.get('alice', 'effect-1') == first
    assert counts(store) == (1, 1)


def test_stale_revision_and_uninitialized_effect_fail_without_recording_request(store):
    with pytest.raises(ServiceError, match='animation_effect_adoption_uninitialized'):
        adopt(store)
    assert counts(store) == (0, 0)
    initialize(store)
    with pytest.raises(ServiceError, match='animation_effect_adoption_revision_conflict'):
        adopt(store, revision=1)
    assert counts(store) == (1, 0)
    assert adopt(store)['revision'] == 1


def test_owner_scopes_both_default_and_idempotency_key(store):
    initialize(store)
    assert store.get('bob', 'effect-1') is None
    with pytest.raises(ServiceError, match='animation_effect_adoption_uninitialized'):
        adopt(store, owner='bob')
    initialize(store, owner='bob')
    alice = adopt(store)
    bob = adopt(store, owner='bob', commission='bob-fresh', candidate='bob-candidate')
    assert store.get('alice', 'effect-1') == alice
    assert store.get('bob', 'effect-1') == bob
    assert counts(store) == (2, 2)


def test_initialization_of_different_effects_does_not_share_a_pointer(store):
    initialize(store)
    initialize(store, effect='effect-2')
    first = adopt(store)
    assert store.get('alice', 'effect-1') == first
    assert store.get('alice', 'effect-2')['revision'] == 0


def test_two_tabs_with_the_same_revision_cannot_silently_overwrite_each_other(store):
    initialize(store)
    barrier = threading.Barrier(2)

    def choose(index):
        other = AnimationEffectAdoptionStore(store.path)
        barrier.wait(timeout=10)
        try:
            return adopt(other, commission='fresh-' + str(index), candidate='candidate-' + str(index),
                request='request-' + str(index))
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(choose, (2, 3)))
    winner = [result for result in results if type(result) is dict]
    assert len(winner) == 1
    assert results.count('animation_effect_adoption_revision_conflict') == 1
    assert store.get('alice', 'effect-1') == winner[0]
    assert counts(store) == (1, 1)


def test_duplicate_requests_racing_on_separate_connections_apply_once(store):
    initialize(store)
    barrier = threading.Barrier(4)

    def choose(_):
        other = AnimationEffectAdoptionStore(store.path)
        barrier.wait(timeout=10)
        return adopt(other)

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(choose, range(4)))
    assert all(result == results[0] for result in results)
    assert counts(store) == (1, 1)


def test_initialization_is_first_writer_wins_under_concurrency(store):
    barrier = threading.Barrier(2)

    def ensure(index):
        other = AnimationEffectAdoptionStore(store.path)
        barrier.wait(timeout=10)
        return other.ensure('alice', 'effect-1', 'commission-' + str(index), 'candidate-' + str(index))

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(ensure, (1, 2)))
    assert results[0] == results[1] == store.get('alice', 'effect-1')
    assert counts(store) == (1, 0)


def test_head_update_rolls_back_if_the_atomic_request_receipt_cannot_be_saved(store):
    original = initialize(store)
    with sqlite3.connect(store.path) as db:
        db.execute('CREATE TRIGGER fail_receipt BEFORE INSERT ON animation_effect_adoption_requests '
            "BEGIN SELECT RAISE(ABORT, 'fixture interruption'); END")
    with pytest.raises(ServiceError, match='animation_effect_adoption_record_changed'):
        adopt(store)
    assert store.get('alice', 'effect-1') == original
    assert counts(store) == (1, 0)
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER fail_receipt')
    assert adopt(store)['revision'] == 1


def test_existing_library_tables_and_immutable_evidence_are_untouched(store):
    with sqlite3.connect(store.path) as db:
        db.execute('CREATE TABLE existing_library_evidence (id TEXT PRIMARY KEY, bytes BLOB NOT NULL)')
        db.execute('INSERT INTO existing_library_evidence VALUES (?,?)', ('source-1', b'original\x00evidence'))
    initialize(store)
    adopt(store)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT * FROM existing_library_evidence').fetchall() == [
            ('source-1', b'original\x00evidence')]
        receipt = json.loads(db.execute('SELECT document FROM animation_effect_adoption_requests').fetchone()[0])
    assert receipt['request']['expected_revision'] == 0
    assert receipt['request']['effect_id'] == 'effect-1'
    assert not ({'published', 'quality_approved', 'human_verification'} & set(receipt))


@pytest.mark.parametrize('changes', [dict(owner=''), dict(owner='\n'), dict(owner='x' * 201),
    dict(owner='\ud800'), dict(effect='../outside'), dict(effect='x' * 201),
    dict(commission=123), dict(candidate=''), dict(request='line\nbreak'), dict(request='x' * 201),
    dict(revision=-1), dict(revision=True), dict(revision=0.0), dict(revision=MAX_REVISION)])
def test_invalid_or_unbounded_request_values_do_not_mutate_metadata(store, changes):
    initialize(store)
    with pytest.raises(ServiceError) as error:
        adopt(store, **changes)
    assert error.value.status == (401 if 'owner' in changes else 422)
    assert counts(store) == (1, 0)


def test_identifier_limit_accepts_valid_maximum_values(store):
    key = 'x' * 200
    result = store.ensure(key, key, key, key)
    assert result['revision'] == 0
    assert store.adopt(key, key, key, key, 0, key)['revision'] == 1


def test_changed_request_receipt_is_rejected_instead_of_claiming_success(store):
    initialize(store)
    adopt(store)
    with sqlite3.connect(store.path) as db:
        record = json.loads(db.execute('SELECT document FROM animation_effect_adoption_requests').fetchone()[0])
        record['result']['candidate_id'] = 'another-candidate'
        record.pop('sha256')
        record['sha256'] = digest(record)
        db.execute('UPDATE animation_effect_adoption_requests SET document=?', (json.dumps(record),))
    with pytest.raises(ServiceError, match='animation_effect_adoption_record_changed'):
        adopt(store)
    assert store.get('alice', 'effect-1')['candidate_id'] == 'candidate-2'
