"""Explicit, owner-scoped WAV reuse starts fresh single-attempt visual work."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError
from video_workbench.app import create_app
from video_workbench.production_run_store import ProductionRunStore
from test_production_runs import OWNER, children, seed, wav
from test_production_film_runs import (
    env, begin, audio_phase, module_phase, finish_module, finish_render,
)


def completed_source(env):
    source = begin(env)
    audio_phase(env)
    module_phase(env)
    finish_render(env, children(env, 'film_render_operations')[-1]['id'])
    env.runs.tick()
    result = env.runs.get(OWNER, source['id'])
    assert result['status'] == 'completed'
    return result


def reuse(env, source, *, key='reuse-audio-fresh-run'):
    project = env.store.project(OWNER, env.project['id'])
    return env.runs.start(OWNER, project['id'], dict(expected_revision=project['revision'],
        joint_plan_version_id='adopted-plan-1', reuse_audio_from_run_id=source['id']), key)


def snapshot(env):
    with env.store.transaction() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
            for table in ('production_runs', 'audio_versions', 'audio_operations',
                          'film_module_operations', 'film_render_operations')}


def edit_project(env, change):
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        change(project)
        env.store._save(db, project)
        db.execute('UPDATE joint_plan_heads SET applied_binding_sha256=? WHERE project_id=?',
            (env.joint._binding(project), project['id']))


def test_reuse_original_wavs_makes_new_modules_and_render_without_changing_source(env):
    source = completed_source(env)
    # Visual revisions are the point of reusing a locked recording.
    edit_project(env, lambda project: project.update(style='改进材质和对象连续性'))
    before = snapshot(env)
    run = reuse(env, source)
    assert run['id'] != source['id'] and run['status'] == 'queued'
    assert run['audio_reuse']['source_run_id'] == source['id']
    assert run['work_budget']['audio_operations'] == run['work_budget']['automatic_retries'] == 0
    assert run['work_budget']['visual_operations'] == 2 and run['work_budget']['draft_renders'] == 1
    fields = ('audio_version_id', 'audio_sha256', 'audio_seconds')
    assert [[item[field] for field in fields] for item in run['items']] == [
        [item[field] for field in fields] for item in source['items']]
    assert all(item['audio_operation_id'] is None for item in run['items'])
    with env.store.transaction() as db:
        frozen = env.runs._read(db, run['id'])
    assert frozen['intents'] == {} and frozen['film']['total_seconds'] == source['duration_seconds']
    env.runs.tick()
    for index in range(2):
        child = children(env, 'film_module_operations')[-1]
        assert child['request_key'].startswith('production-run:' + run['id'] + ':module:')
        assert child['request']['target'] != children(env, 'film_module_operations')[index]['request']['target']
        finish_module(env, child['id'])
        env.runs.tick()
    render = children(env, 'film_render_operations')[-1]
    assert render['request_key'] == 'production-run:' + run['id'] + ':draft'
    assert render['id'] != source['draft_render_id']
    finish_render(env, render['id'])
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == 'completed' and result['audio_reuse'] == run['audio_reuse']
    after = snapshot(env)
    for table, original in before.items():
        assert after[table][:len(original)] == original
    assert len(after['audio_operations']) == len(before['audio_operations']) == 2
    assert not children(env, 'motion_operations') and not children(env, 'video_renders')
    next_run = reuse(env, result, key='reuse-audio-new-lineage')
    assert next_run['id'] != result['id'] and next_run['audio_reuse']['source_run_id'] == result['id']
    assert next_run['work_budget'] == result['work_budget']
    assert len(children(env, 'audio_operations')) == 2


def test_reuse_recovers_one_fresh_child_after_response_loss_without_tts(env, monkeypatch):
    source = completed_source(env)
    run = reuse(env, source)
    original = env.facade.start_module
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('local response lost')
    monkeypatch.setattr(env.facade, 'start_module', lost)
    env.runs.tick()
    assert len(children(env, 'film_module_operations')) == 3
    recovered = ProductionRunStore(env.store, env.joint, env.motion, env.production, film_creation=env.facade)
    recovered.tick()
    assert len(children(env, 'film_module_operations')) == 3
    assert len(children(env, 'audio_operations')) == 2
    assert recovered.get(OWNER, run['id'])['audio_reuse'] == run['audio_reuse']
    assert reuse(env, source)['id'] == run['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        reuse(env, dict(id='another-source'), key='reuse-audio-fresh-run')


def test_reuse_cancel_before_child_and_during_child_never_cancels_old_audio(env):
    source = completed_source(env)
    original_audio = deepcopy(children(env, 'audio_operations'))
    first = reuse(env, source)
    assert env.runs.cancel(OWNER, first['id'])['status'] == 'cancelled'
    assert len(children(env, 'film_module_operations')) == 2
    second = reuse(env, source, key='reuse-audio-cancel-child')
    env.runs.tick()
    child = children(env, 'film_module_operations')[-1]
    result = env.runs.cancel(OWNER, second['id'])
    assert result['status'] == 'running' and result['draining'] is True
    assert env.creation.claim_submission('module', child['id']) is None  # queued writer never starts
    env.runs.tick()
    result = env.runs.get(OWNER, second['id'])
    assert result['status'] == 'cancelled' and result['draining'] is False
    assert env.facade.cancel_calls == [('module', child['id'])]
    assert children(env, 'audio_operations') == original_audio
    assert env.runs.get(OWNER, source['id']) == source


@pytest.mark.parametrize('change', ['narration', 'order', 'scene_id', 'voice', 'speed', 'pronunciation', 'model'])
def test_changed_speech_or_scene_mapping_rejects_before_creating_any_work(env, change):
    source = completed_source(env)
    def mutate(project):
        if change == 'narration': project['scenes'][0]['narration'] += '新内容'
        elif change == 'order': project['scenes'].reverse()
        elif change == 'scene_id': project['scenes'][0]['id'] = 'different-scene'
        elif change == 'voice': project['speech_settings'] = dict(voice='zm_yunxi')
        elif change == 'model': env.store.speech_provider['model'] = 'other-model'
        else: project['scenes'][0]['speech_settings'] = (
            dict(speed=1.2) if change == 'speed' else dict(pronunciation={'原文': '读法'}))
    edit_project(env, mutate)
    before = snapshot(env)
    with pytest.raises(ServiceError, match='production_run_audio_reuse_mismatch'):
        reuse(env, source)
    assert snapshot(env) == before


@pytest.mark.parametrize('change', ['missing', 'invalid_wav', 'changed_wav', 'hash', 'duration',
                                    'metadata_narration', 'version_scene', 'source_audio_ref', 'source_frozen', 'source_film'])
def test_missing_or_changed_original_recording_rejects_atomically(env, change):
    source = completed_source(env)
    version_id = source['items'][0]['audio_version_id']
    with env.store.transaction() as db:
        row = db.execute('SELECT document,data FROM audio_versions WHERE id=?', (version_id,)).fetchone()
        metadata, data = json.loads(row['document']), bytes(row['data'])
        if change == 'missing': db.execute('DELETE FROM audio_versions WHERE id=?', (version_id,))
        elif change == 'version_scene':
            db.execute('UPDATE audio_versions SET scene_id=? WHERE id=?', ('different-scene', version_id))
        elif change.startswith('source_'):
            document = env.runs._read(db, source['id'])
            if change == 'source_audio_ref': document['items'][0]['audio_sha256'] = 'f' * 64
            elif change == 'source_film': document['film']['scenes'][0]['audio_sha256'] = 'f' * 64
            else: document['frozen']['scenes'][0]['audio_input']['narration'] = 'corrupted original'
            env.runs._write(db, document)
        else:
            if change == 'invalid_wav': data = b'invalid wav'
            elif change == 'changed_wav': data = wav(1.1)
            elif change == 'hash': metadata['sha256'] = 'f' * 64
            elif change == 'duration': metadata['duration_seconds'] = 1.1
            else: metadata['narration'] += 'tampered'
            db.execute('UPDATE audio_versions SET document=?,data=? WHERE id=?',
                (json.dumps(metadata), data, version_id))
    before = snapshot(env)
    with pytest.raises(ServiceError, match='production_run_audio_reuse_'):
        reuse(env, source)
    assert snapshot(env) == before


@pytest.mark.parametrize('change', ['owner', 'project', 'failed', 'draining', 'blocked', 'cancelled'])
def test_only_same_owner_project_and_clean_completed_source_is_eligible(env, change):
    source = completed_source(env)
    with env.store.transaction() as db:
        document = env.runs._read(db, source['id'])
        if change == 'owner': db.execute('UPDATE production_runs SET owner=? WHERE id=?', ('other', source['id']))
        elif change == 'project':
            document['project_id'] = 'other-project'
            db.execute('UPDATE production_runs SET project_id=? WHERE id=?', ('other-project', source['id']))
        elif change == 'failed': document['status'] = 'failed'
        elif change == 'draining': document['draining'] = True
        elif change == 'blocked': document['blocked_reason'] = 'unknown_stop'
        else: document['cancel_requested'] = True
        env.runs._write(db, document)
    before = snapshot(env)
    with pytest.raises(ServiceError):
        reuse(env, source)
    assert snapshot(env) == before


def test_frozen_reuse_survives_source_journal_changes_and_new_input_edit_becomes_stale(env):
    source = completed_source(env)
    run = reuse(env, source)
    with env.store.transaction() as db:
        old = env.runs._read(db, source['id'])
        old['items'][0]['audio_version_id'] = 'later-source-journal-corruption'
        env.runs._write(db, old)
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['phase'] == 'module'
    edit_project(env, lambda project: project['scenes'][0].update(narration='新旁白'))
    env.runs.tick()
    child = children(env, 'film_module_operations')[-1]
    assert env.creation.claim_submission('module', child['id']) is None
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == 'stale' and result['draining'] is False
    assert len(children(env, 'audio_operations')) == 2
    assert len(children(env, 'film_module_operations')) == 3


def test_missing_frozen_reuse_ref_fails_without_generating_replacement_audio(env):
    source = completed_source(env)
    run = reuse(env, source)
    with env.store.transaction() as db:
        document = env.runs._read(db, run['id'])
        document['items'][0]['audio_version_id'] = None
        env.runs._write(db, document)
    env.runs.tick()
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == 'failed' and result['error'] == 'production_run_audio_reuse_mismatch'
    assert result['draining'] is False and len(children(env, 'audio_operations')) == 2
    assert len(children(env, 'film_module_operations')) == 2


def test_omitting_reuse_still_generates_new_tts(env):
    completed_source(env)
    run = begin(env, key='fresh-recordings-default')
    assert 'audio_reuse' not in run and all(item['audio_version_id'] is None for item in run['items'])
    env.runs.tick()
    assert len(children(env, 'audio_operations')) == 3
    assert len(children(env, 'film_module_operations')) == 2


def test_reuse_checks_actual_recording_clock_with_new_pauses_before_admission(env):
    source = completed_source(env)
    edit_project(env, lambda project: project['scenes'][0].update(leading_seconds=57))
    before = snapshot(env)
    with pytest.raises(ServiceError, match='production_run_film_too_long'):
        reuse(env, source)
    assert snapshot(env) == before


def test_http_optional_reuse_preserves_original_request_and_strict_validation(tmp_path, monkeypatch):
    app = create_app(db_path=tmp_path / 'api.db', background=False, local_mode=True,
        production_runs_enabled=True, trusted_hosts={'testserver'})
    project = seed(app.state.store, app.state.joint_plan_store)
    requests = []
    def capture(owner, project_id, request, key):
        requests.append(deepcopy(request))
        return dict(id='captured')
    monkeypatch.setattr(app.state.production_run_store, 'start', capture)
    request = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1')
    with TestClient(app) as client:
        client.headers.update({'x-csrf-token': client.get('/api/session').json()['csrf_token'],
            'Idempotency-Key': 'reuse-http-contract'})
        url = f'/api/projects/{project["id"]}/production-runs'
        assert client.post(url, json=request).status_code == 202
        assert requests[-1] == request
        assert client.post(url, json=dict(request, reuse_audio_from_run_id=None)).status_code == 202
        assert requests[-1] == request
        requested = dict(request, reuse_audio_from_run_id='original-completed-run')
        assert client.post(url, json=requested).status_code == 202
        assert requests[-1] == requested
        for invalid in ('', 'x' * 101, 123):
            assert client.post(url, json=dict(request, reuse_audio_from_run_id=invalid)).status_code == 422
        assert client.post(url, json=dict(request, reuse_audio=True)).status_code == 422
