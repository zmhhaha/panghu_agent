"""Formal candidate custody only: no reviewer, new render or round consumption.

The inherited film fixture uses stopped synthetic module output and nonmedia
bytes. These tests prove durable identity, not actual audiovisual quality.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.production_sequence_store import ProductionSequenceStore
from test_production_runs import OWNER, children, finish_audio
from test_production_film_runs import env as film_env, begin, audio_phase, module_phase, finish_module, finish_render
from test_production_run_audio_reuse import completed_source, reuse


@pytest.fixture
def env(film_env):
    film_env.sequences = ProductionSequenceStore(film_env.path / 'sequences.db')
    film_env.runs = restarted(film_env)
    return film_env


def restarted(env, sequence_store=None):
    return ProductionRunStore(env.store, env.joint, env.motion, env.production,
        film_creation=env.facade, sequence_store=sequence_store or env.sequences)


def ledger_counts(env):
    with env.sequences._connect() as db:
        return {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
            for table in ('production_sequences', 'production_sequence_sources',
                          'production_sequence_rounds', 'production_requirement_batches')}


def work_counts(env):
    return {table: len(children(env, table)) for table in
        ('audio_operations', 'film_module_operations', 'film_render_operations')}


def finish_reuse(env, run):
    env.runs.tick()
    for index in range(len(run['items'])):
        finish_module(env, children(env, 'film_module_operations')[-1]['id'])
        env.runs.tick()
    finish_render(env, children(env, 'film_render_operations')[-1]['id'])
    env.runs.tick()
    return env.runs.get(OWNER, run['id'])


def test_real_candidate_registered_once_without_review_or_allowance(env, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('identity registration must never reserve a refinement round')
    monkeypatch.setattr(env.sequences, 'reserve_round', forbidden)
    monkeypatch.setattr(env.sequences, 'reserve_requirements', forbidden)
    run = begin(env)
    assert run['sequence_id'] is None
    assert run['sequence_registration']['status'] == 'awaiting_draft'
    assert ledger_counts(env)['production_sequences'] == 0
    audio_phase(env)
    module_phase(env)
    render = children(env, 'film_render_operations')[-1]
    assert ledger_counts(env)['production_sequences'] == 0
    finish_render(env, render['id'])
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    registration = result['sequence_registration']
    assert result['status'] == 'completed' and registration['status'] == 'registered'
    assert result['review_status'] == 'not_reviewed' and result['selection'] == 'draft_only'
    assert registration['source'] == dict(run_id=run['id'], version_id=render['id'],
        sha256=hashlib.sha256(env.creation.video(OWNER, render['id'])).hexdigest())
    assert registration['parent_source'] is None and result['sequence_id'] == registration['sequence_id']
    assert ledger_counts(env) == dict(production_sequences=1, production_sequence_sources=1,
        production_sequence_rounds=0, production_requirement_batches=0)
    before = work_counts(env)
    restarted(env).tick()
    assert work_counts(env) == before
    assert env.runs.get(OWNER, run['id']) == result


def test_original_audio_and_multilevel_derivatives_keep_same_sequence(env):
    original = completed_source(env)
    first = reuse(env, original)
    assert first['sequence_id'] == original['sequence_id']
    assert first['sequence_registration']['status'] == 'awaiting_draft'
    assert first['sequence_registration']['parent_source'] == original['sequence_registration']['source']
    first = finish_reuse(env, first)
    second = finish_reuse(env, reuse(env, first, key='sequence-second-derivative'))
    assert second['sequence_id'] == first['sequence_id'] == original['sequence_id']
    assert second['sequence_registration']['parent_source'] == first['sequence_registration']['source']
    assert len({x['sequence_registration']['source']['version_id'] for x in (original, first, second)}) == 3
    # Equal fixture movie bytes must not collapse distinct version identities.
    assert len({x['sequence_registration']['source']['sha256'] for x in (original, first, second)}) == 1
    assert ledger_counts(env) == dict(production_sequences=1, production_sequence_sources=3,
        production_sequence_rounds=0, production_requirement_batches=0)
    assert work_counts(env) == dict(audio_operations=2, film_module_operations=6, film_render_operations=3)


def test_separate_original_runs_with_equal_movie_hash_have_distinct_sequences(env):
    original = completed_source(env)
    run = begin(env, key='independent-original-film')
    for seconds in (1, 2):
        env.runs.tick()
        finish_audio(env, children(env, 'audio_operations')[-1]['id'], seconds)
    env.runs.tick()
    for index in range(2):
        finish_module(env, children(env, 'film_module_operations')[-1]['id'])
        env.runs.tick()
    finish_render(env, children(env, 'film_render_operations')[-1]['id'])
    env.runs.tick()
    second = env.runs.get(OWNER, run['id'])
    assert original['sequence_registration']['source']['sha256'] == second['sequence_registration']['source']['sha256']
    assert original['sequence_id'] != second['sequence_id']
    assert ledger_counts(env)['production_sequences'] == 2


def test_completed_legacy_origin_is_registered_only_by_explicit_reuse(env):
    env.runs = ProductionRunStore(env.store, env.joint, env.motion, env.production, film_creation=env.facade)
    source = completed_source(env)
    assert 'sequence_registration' not in source
    env.runs = restarted(env)
    env.runs.tick()
    assert env.runs.get(OWNER, source['id']) == source
    assert ledger_counts(env)['production_sequences'] == 0
    derivative = reuse(env, source)
    registered = env.runs.get(OWNER, source['id'])
    assert registered['sequence_registration']['status'] == 'registered'
    assert derivative['sequence_id'] == registered['sequence_id']
    assert ledger_counts(env)['production_sequence_rounds'] == 0


@pytest.mark.parametrize('derived', [False, True])
def test_ledger_commit_before_main_write_recovers_without_new_creation(env, monkeypatch, derived):
    if derived:
        original = completed_source(env)
        run = reuse(env, original)
        env.runs.tick()
        for index in range(2):
            finish_module(env, children(env, 'film_module_operations')[-1]['id'])
            env.runs.tick()
    else:
        run = begin(env)
        audio_phase(env)
        module_phase(env)
    finish_render(env, children(env, 'film_render_operations')[-1]['id'])
    save = env.runs._save
    def crash(document):
        if document.get('sequence_registration', {}).get('status') == 'registered':
            raise SystemExit('simulated process death after ledger commit')
        return save(document)
    monkeypatch.setattr(env.runs, '_save', crash)
    with pytest.raises(SystemExit):
        env.runs.tick()
    pending = env.runs.get(OWNER, run['id'])
    assert pending['status'] == 'completed' and pending['sequence_registration']['status'] == 'pending'
    before, counts = work_counts(env), ledger_counts(env)
    env.runs = restarted(env)
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['sequence_registration']['status'] == 'registered'
    assert work_counts(env) == before and ledger_counts(env) == counts
    if derived:
        assert result['sequence_id'] == original['sequence_id']


def test_cas_change_freezes_distinct_intent_and_recovers(env, monkeypatch):
    original = completed_source(env)
    run = reuse(env, original)
    bind = env.sequences.bind_source
    mutated = False
    def race(*args, **kwargs):
        nonlocal mutated
        if not mutated:
            mutated = True
            with env.sequences._transaction() as db:
                sequence = env.sequences._sequence(db, OWNER, original['project_id'], original['sequence_id'])
                env.sequences._bump_sequence(db, sequence)
        return bind(*args, **kwargs)
    monkeypatch.setattr(env.sequences, 'bind_source', race)
    pending = finish_reuse(env, run)
    assert pending['status'] == 'completed' and pending['sequence_registration']['status'] == 'pending'
    with env.store.transaction() as db:
        document = env.runs._read(db, run['id'])
    assert len(document['sequence_intent_history']) == 1
    assert document['sequence_intent_history'][0]['key'] != document['sequence_intent']['key']
    counts = work_counts(env)
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['sequence_registration']['status'] == 'registered'
    assert work_counts(env) == counts and ledger_counts(env)['production_sequence_rounds'] == 0


def test_concurrent_same_reuse_request_has_one_run_one_inherited_identity(env):
    source = completed_source(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reuse(env, source), range(2)))
    assert results[0]['id'] == results[1]['id']
    assert results[0]['sequence_id'] == results[1]['sequence_id'] == source['sequence_id']
    assert len(children(env, 'production_runs')) == 2
    assert work_counts(env) == dict(audio_operations=2, film_module_operations=2, film_render_operations=1)


@pytest.mark.parametrize('tamper', ['owner', 'project', 'sha256', 'data', 'snapshot'])
def test_changed_completed_candidate_rejects_before_new_work(env, tamper):
    source = completed_source(env)
    with env.store.transaction() as db:
        row = db.execute('SELECT document,data FROM film_render_operations WHERE id=?',
            (source['draft_render_id'],)).fetchone()
        document, data = json.loads(row['document']), bytes(row['data'])
        if tamper == 'owner':
            db.execute('UPDATE film_render_operations SET owner=? WHERE id=?', ('someone-else', document['id']))
        elif tamper == 'project':
            document['project_id'] = 'other-project'
        elif tamper == 'sha256':
            document['result']['sha256'] = 'e' * 64
        elif tamper == 'data':
            data += b'changed'
        else:
            document['snapshot_sha256'] = 'e' * 64
        db.execute('UPDATE film_render_operations SET document=?,data=? WHERE id=?',
            (json.dumps(document), data, document['id']))
    before = work_counts(env)
    with pytest.raises(ServiceError):
        reuse(env, source)
    assert work_counts(env) == before and ledger_counts(env)['production_sequences'] == 1


def test_missing_sidecar_binding_blocks_instead_of_replenishing_sequence(env):
    source = completed_source(env)
    empty = ProductionSequenceStore(env.path / 'empty-sidecar.db')
    env.runs = restarted(env, empty)
    with pytest.raises(ServiceError, match='production_run_sequence_binding_missing'):
        reuse(env, source)
    current = env.runs.get(OWNER, source['id'])
    assert current['status'] == 'completed' and current['sequence_id'] == source['sequence_id']
    assert current['sequence_registration']['status'] == 'blocked'
    with empty._connect() as db:
        assert db.execute('SELECT count(*) FROM production_sequences').fetchone()[0] == 0
    env.runs.tick()
    assert work_counts(env) == dict(audio_operations=2, film_module_operations=2, film_render_operations=1)
    env.runs = restarted(env)
    env.runs.tick()
    assert env.runs.get(OWNER, source['id'])['sequence_registration']['status'] == 'registered'


def test_physically_missing_sidecar_rejects_mutation_without_creating_database(env):
    source = completed_source(env)
    path = env.path / 'sequences.db'
    path.unlink()
    with pytest.raises(ServiceError, match='production_run_sequence_unavailable'):
        reuse(env, source)
    assert not path.exists()
    state = env.runs.get(OWNER, source['id'])
    assert state['status'] == 'completed' and state['sequence_id'] == source['sequence_id']
    assert state['sequence_registration']['status'] == 'blocked'
    assert work_counts(env) == dict(audio_operations=2, film_module_operations=2, film_render_operations=1)


@pytest.mark.parametrize('window', ['before_start', 'before_child', 'disabled_store'])
def test_identity_unavailable_blocks_new_paid_work(env, window):
    if window == 'before_start':
        (env.path / 'sequences.db').unlink()
        with pytest.raises(ServiceError, match='production_run_sequence_unavailable'):
            begin(env)
        assert not children(env, 'production_runs')
    else:
        run = begin(env)
        if window == 'before_child':
            (env.path / 'sequences.db').unlink()
        else:
            env.runs = ProductionRunStore(env.store, env.joint, env.motion, env.production,
                film_creation=env.facade)
        env.runs.tick()
        current = env.runs.get(OWNER, run['id'])
        assert current['status'] == 'failed' and current['error'] == 'production_run_sequence_unavailable'
    assert work_counts(env) == dict(audio_operations=0, film_module_operations=0, film_render_operations=0)


@pytest.mark.parametrize('missing', ['sequence_store', 'film_creation'])
def test_bound_source_cannot_be_reused_by_unconfigured_constructor(env, missing):
    source = completed_source(env)
    env.runs = ProductionRunStore(env.store, env.joint, env.motion, env.production,
        film_creation=None if missing == 'film_creation' else env.facade,
        sequence_store=None if missing == 'sequence_store' else env.sequences)
    before = work_counts(env)
    with pytest.raises(ServiceError, match='production_run_sequence_unavailable'):
        reuse(env, source)
    assert work_counts(env) == before and len(children(env, 'production_runs')) == 1


def test_missing_sidecar_after_render_completion_preserves_finished_candidate(env):
    run = begin(env)
    audio_phase(env)
    module_phase(env)
    render = children(env, 'film_render_operations')[-1]
    finish_render(env, render['id'])
    path = env.path / 'sequences.db'
    path.unlink()
    before = work_counts(env)
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == result['phase'] == 'completed'
    assert result['draft_render_id'] == render['id'] and not result['draining']
    assert result['sequence_registration']['status'] == 'blocked'
    assert result['sequence_registration']['error'] == 'production_run_sequence_unavailable'
    assert result['review_status'] == 'not_reviewed' and result['selection'] == 'draft_only'
    assert env.creation.video(OWNER, render['id'])
    assert work_counts(env) == before and not path.exists()


def test_consumed_rounds_are_preserved_when_reusing_original_voice(env):
    source = completed_source(env)
    sequence = env.sequences.get(OWNER, source['project_id'], source['sequence_id'])
    operation = env.sequences.reserve_round(OWNER, source['project_id'], source['sequence_id'], 'automatic',
        source['sequence_registration']['source']['version_id'], 'a' * 64, 'test-consumed-automatic-round',
        expected_revision=sequence['revision'])
    env.sequences.finish(OWNER, source['project_id'], operation['id'], 'cancelled', 'b' * 64,
        expected_revision=operation['revision'])
    before = env.sequences.operations(OWNER, source['project_id'], source['sequence_id'])
    derived = finish_reuse(env, reuse(env, source))
    assert derived['sequence_id'] == source['sequence_id']
    assert env.sequences.operations(OWNER, source['project_id'], source['sequence_id']) == before
    assert ledger_counts(env)['production_sequences'] == ledger_counts(env)['production_sequence_rounds'] == 1


@pytest.mark.parametrize('ending', ['cancelled', 'failed'])
def test_cancelled_or_failed_work_never_registers_candidate(env, ending):
    run = begin(env)
    if ending == 'cancelled':
        env.runs.cancel(OWNER, run['id'])
    else:
        env.runs.tick()
        child = children(env, 'audio_operations')[-1]
        with env.store.transaction() as db:
            child.update(status='failed', error='synthetic_failure', submit_attempted=False)
            db.execute('UPDATE audio_operations SET document=? WHERE id=?', (json.dumps(child), child['id']))
        env.runs.tick()
    env.runs.tick()
    current = env.runs.get(OWNER, run['id'])
    assert current['status'] == ending
    assert current['sequence_id'] is None and current['sequence_registration']['source'] is None
    assert ledger_counts(env)['production_sequences'] == 0
    with pytest.raises(ServiceError):
        reuse(env, current)
    assert ledger_counts(env)['production_sequences'] == 0


def test_deleted_pending_registration_stays_blocked_without_replaying_media(env, monkeypatch):
    original = env.sequences.create
    def unavailable(*args, **kwargs):
        raise RuntimeError('ledger temporarily unavailable')
    monkeypatch.setattr(env.sequences, 'create', unavailable)
    run = completed_source(env)
    assert run['sequence_registration']['status'] == 'pending'
    before = work_counts(env)
    project = env.store.project(OWNER, env.project['id'])
    env.store.set_project_deleted(OWNER, project['id'], project['revision'], deleted=True)
    monkeypatch.setattr(env.sequences, 'create', original)
    env.runs.tick()
    with env.store.transaction() as db:
        document = env.runs._read(db, run['id'])
    assert document['sequence_registration']['status'] == 'blocked'
    assert document['sequence_registration']['source'] is not None
    assert work_counts(env) == before and ledger_counts(env)['production_sequences'] == 0
