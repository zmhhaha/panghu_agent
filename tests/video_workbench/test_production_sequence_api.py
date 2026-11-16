"""HTTP lineage reads retain identity without reviewing or allocating rounds.

Stopped local child fixtures exercise custody, not real encoded media quality.
"""
from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from video_workbench.app import create_app
from test_production_runs import OWNER, children
from test_production_film_runs import env, audio_phase, module_phase, finish_render
from test_production_run_audio_reuse import completed_source


def application(env):
    env.facade.stop = lambda: None
    return create_app(db_path=env.store.path, film_creation=env.facade, gateway=object(),
        background=False, local_mode=False, production_runs_enabled=True,
        trusted_hosts={'testserver'})


def headers(client):
    client.headers['x-forwarded-user'] = OWNER
    client.headers['x-csrf-token'] = client.get('/api/session').json()['csrf_token']


def test_http_registered_film_identity_survives_reads_restart_and_trash(env):
    app = application(env)
    env.runs = app.state.production_run_store
    ledger = app.state.production_sequence_store
    assert Path(ledger.path) == Path(env.store.path).with_name('production-sequences.sqlite3')
    with TestClient(app) as client:
        headers(client)
        project = env.store.project(OWNER, env.project['id'])
        url = f'/api/projects/{project["id"]}/production-runs'
        response = client.post(url, json=dict(expected_revision=project['revision'],
            joint_plan_version_id='adopted-plan-1'), headers={'Idempotency-Key': 'http-sequence-origin'})
        assert response.status_code == 202
        run_id = response.json()['id']
        audio_phase(env)
        module_phase(env)
        finish_render(env, children(env, 'film_render_operations')[-1]['id'])
        env.runs.tick()
        result = client.get('/api/production-runs/' + run_id).json()
        assert result['status'] == 'completed' and result['review_status'] == 'not_reviewed'
        registration = result['sequence_registration']
        assert registration['status'] == 'registered' and registration['sequence_id'] == result['sequence_id']
        assert registration['source']['run_id'] == run_id
        assert registration['source']['version_id'] == result['draft_render_id']
        assert not any(key in registration for key in ('idempotency_key', 'expected_revision', 'intent'))
        identity = result['sequence_id']
        original_events = deepcopy(ledger.events(OWNER, project['id'], identity))
        original_runs = deepcopy(children(env, 'production_runs'))
        assert ledger.operations(OWNER, project['id'], identity) == []
        assert client.get(url).json()['runs'][0]['sequence_id'] == identity
        assert client.get('/api/production-runs/' + run_id).json()['sequence_id'] == identity
        assert ledger.events(OWNER, project['id'], identity) == original_events
        assert children(env, 'production_runs') == original_runs
        client.headers['x-forwarded-user'] = 'another-owner'
        assert client.get('/api/production-runs/' + run_id).status_code == 404
        headers(client)
        current = env.store.project(OWNER, project['id'])
        removed = client.post(f'/api/projects/{project["id"]}/trash',
            json=dict(expected_revision=current['revision']))
        assert removed.status_code == 200
        assert client.get('/api/production-runs/' + run_id).status_code == 404
        restored = client.post(f'/api/projects/{project["id"]}/restore',
            json=dict(expected_revision=removed.json()['project']['revision']))
        assert restored.status_code == 200
        assert client.get('/api/production-runs/' + run_id).json()['sequence_id'] == identity
        assert ledger.events(OWNER, project['id'], identity) == original_events
    restarted = application(env)
    with TestClient(restarted) as client:
        headers(client)
        result = client.get('/api/production-runs/' + run_id).json()
        assert result['sequence_id'] == identity and result['review_status'] == 'not_reviewed'
        assert restarted.state.production_sequence_store.operations(OWNER, project['id'], identity) == []
        assert len(children(env, 'audio_operations')) == 2
        assert len(children(env, 'film_module_operations')) == 2
        assert len(children(env, 'film_render_operations')) == 1
    # The durable intent exists before the sidecar acknowledgement. Losing
    # the sidecar in that window must not create another production identity.
    with env.store.transaction() as db:
        pending = env.runs._read(db, run_id, OWNER)
        assert pending['sequence_intent']['action'] == 'create'
        pending['sequence_registration'].update(status='pending', sequence_id=None)
        env.runs._write(db, pending)
    path = Path(ledger.path)
    path.rename(path.with_suffix('.lost'))
    with pytest.raises(ValueError, match='Restore the existing production sequence database'):
        application(env)
    assert not path.exists()


def test_http_read_of_old_completed_film_does_not_register_or_create_work(env):
    old = completed_source(env)  # The historical store has no sequence integration.
    before = deepcopy(children(env, 'production_runs'))
    app = application(env)
    ledger = app.state.production_sequence_store
    with TestClient(app) as client:
        headers(client)
        result = client.get('/api/production-runs/' + old['id']).json()
        assert result['status'] == 'completed' and result['review_status'] == 'not_reviewed'
        assert result.get('sequence_id') is None
        assert children(env, 'production_runs') == before
        with sqlite3.connect(ledger.path) as db:
            assert db.execute('SELECT count(*) FROM production_sequences').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM production_sequence_rounds').fetchone()[0] == 0
        assert len(children(env, 'audio_operations')) == 2
        assert len(children(env, 'film_module_operations')) == 2
        assert len(children(env, 'film_render_operations')) == 1


def test_readiness_refuses_missing_sequence_database_without_recreating_it(env):
    old = completed_source(env)
    app = application(env)
    path = Path(app.state.production_sequence_store.path)
    with TestClient(app) as client:
        headers(client)
        assert client.get('/readyz').status_code == 200
        path.rename(path.with_suffix('.removed'))
        assert client.get('/readyz').status_code == 503
        assert not path.exists()
        assert client.get('/api/production-runs/' + old['id']).status_code == 200
        assert not path.exists()
