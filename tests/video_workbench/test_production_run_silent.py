"""Explicit visual clocks skip speech without weakening recorded film runs."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
from test_production_runs import OWNER, children, seed
from test_production_film_runs import env, finish_module, finish_render, RUNTIME


def start_silent(env, key='explicit-silent-film-run'):
    project = env.store.project(OWNER, env.project['id'])
    return env.runs.start(OWNER, project['id'], dict(expected_revision=project['revision'],
        joint_plan_version_id='adopted-plan-1', audio_mode='silent'), key)


def forbid_speech(env, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail('Explicit silent run must not create or read a recording')
    monkeypatch.setattr(env.store, 'start_audio', forbidden)
    monkeypatch.setattr(env.store, 'audio_version', forbidden)
    return calls


def test_explicit_silent_run_freezes_visual_clock_and_authors_real_modules_without_audio(env, monkeypatch):
    before = deepcopy(env.store.project(OWNER, env.project['id']))
    calls = forbid_speech(env, monkeypatch)
    run = start_silent(env)
    assert run['audio_mode'] == 'silent' and run['voice'] is None and run['phase'] == 'module'
    assert run['duration_seconds'] == 16 and run['progress'] == {'done': 0, 'total': 3}
    assert run['duration_source'] == 'planned_visual_durations_on_24fps_clock'
    assert all(item['audio_status'] == 'not_requested' and item['audio_seconds'] == 0
        and item['audio_operation_id'] is item['audio_version_id'] is item['audio_sha256'] is None
        for item in run['items'])
    with env.store.transaction() as db:
        journal = env.runs._read(db, run['id'])
        assert journal['binding_sha256'] == digest(journal['frozen'])
        assert env.creation._recordings(db, before, journal['film']) == []
    film = journal['film']
    assert film['schema'] == 'video.hyperframes-film-clock.v1'
    assert film['total_seconds'] == 16 and journal['film_sha256'] == digest(film)
    for index, row in enumerate(film['scenes']):
        assert row['clock_source'] == 'silent' and row['narration'] == ''
        assert row['audio_version_id'] is row['audio_sha256'] is None and row['audio_seconds'] == 0
        assert row['leading_silence_seconds'] == 0 and row['trailing_silence_seconds'] == 8
        assert (row['start_seconds'], row['end_seconds']) == (8 * index, 8 * (index + 1))
    for index in range(2):
        env.runs.tick()
        operation = children(env, 'film_module_operations')[-1]
        task = operation['document']['core_task']
        assert task['film_context']['schema'] == 'video.hyperframes-film-author-context.v3'
        assert (task['film_context']['predecessor_appearance'] is None) == (index == 0)
        assert task['film_context']['film'] == film
        assert task['scene']['narration'] == ''
        assert task['scene']['beats'] is task['scene']['alignment'] is task['scene']['audio_sha256'] is None
        assert task['constraints']['duration_seconds'] == 8
        assert operation['request']['current_scene_index'] == index
        finish_module(env, operation['id'])
    env.runs.tick()
    operation = children(env, 'film_render_operations')[0]
    snapshot = env.creation.render_snapshot(operation['id'])
    assert snapshot['clock']['frame_count'] == 384
    assert snapshot['recording_sha256'] == {}
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=RUNTIME, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    assert not any(name.endswith('.wav') for name in files)
    assert not children(env, 'audio_operations') and calls == []
    with env.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM audio_versions').fetchone()[0] == 0
    assert env.store.project(OWNER, env.project['id']) == before
    assert start_silent(env)['id'] == run['id']
    # Reuse the stopped nonmedia render fixture while explicitly identifying
    # its silent contract. This verifies custody, never audiovisual quality.
    complete = env.creation.complete_render
    with monkeypatch.context() as patch:
        def complete_silent(operation_id, metadata, data):
            return complete(operation_id, dict(metadata, silent=True, contains_narration=False), data)
        patch.setattr(env.creation, 'complete_render', complete_silent)
        finish_render(env, operation['id'])
    env.runs.tick()
    completed = env.runs.get(OWNER, run['id'])
    assert completed['status'] == 'completed' and completed['progress'] == {'done': 3, 'total': 3}
    assert completed['duration_seconds'] == 16 and completed['audio_mode'] == 'silent'
    assert not children(env, 'audio_operations') and calls == []


def test_silent_300_seconds_is_7200_frames_and_restart_never_enters_audio(env, monkeypatch):
    calls = forbid_speech(env, monkeypatch)
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        template = deepcopy(project['scenes'][0])
        project['scenes'] = [dict(deepcopy(template), id=f'landscape-{index}',
            title=f'Landscape passage {index}', duration_seconds=25) for index in range(12)]
        env.store._save(db, project)
        db.execute('UPDATE joint_plan_heads SET applied_binding_sha256=? WHERE project_id=?',
            (env.joint._binding(project), project['id']))
    original = env.facade.readiness
    monkeypatch.setattr(env.facade, 'readiness', lambda: dict(original(), max_seconds=300))
    run = start_silent(env)
    assert run['duration_seconds'] == 300 and run['progress']['total'] == 13
    with env.store.transaction() as db:
        film = env.runs._read(db, run['id'])['film']
    assert film['total_seconds'] * 24 == 7200
    assert [(row['start_seconds'] * 24, row['end_seconds'] * 24) for row in film['scenes']] == [
        (index * 600, (index + 1) * 600) for index in range(12)]
    env.runs.tick()
    recovered = ProductionRunStore(env.store, env.joint, env.motion, env.production, film_creation=env.facade)
    recovered.tick()
    assert len(children(env, 'film_module_operations')) == 1
    assert not children(env, 'audio_operations') and calls == []
    assert recovered.get(OWNER, run['id'])['current'] is True


def test_silent_fractional_visual_duration_quantizes_up_without_speech_pauses(env):
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        project['scenes'][0]['duration_seconds'] = 1.01
        env.store._save(db, project)
        db.execute('UPDATE joint_plan_heads SET applied_binding_sha256=? WHERE project_id=?',
            (env.joint._binding(project), project['id']))
    run = start_silent(env)
    assert run['items'][0]['duration_seconds'] == 25 / 24
    with env.store.transaction() as db:
        row = env.runs._read(db, run['id'])['film']['scenes'][0]
    assert row['leading_silence_seconds'] == 0
    assert row['trailing_silence_seconds'] == row['end_seconds'] == 25 / 24


def test_missing_recording_in_default_run_is_not_inferred_as_silent(env):
    project = env.store.project(OWNER, env.project['id'])
    request = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1')
    run = env.runs.start(OWNER, project['id'], request, 'legacy-recorded-film-run')
    assert run['phase'] == 'audio'
    env.runs.tick()
    assert len(children(env, 'audio_operations')) == 1
    assert not children(env, 'film_module_operations')
    assert env.runs.start(OWNER, project['id'], request, 'legacy-recorded-film-run')['id'] == run['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        env.runs.start(OWNER, project['id'], dict(request, audio_mode='silent'), 'legacy-recorded-film-run')


def test_explicit_silent_rejects_recording_reuse_before_any_work(env):
    project = env.store.project(OWNER, env.project['id'])
    request = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1',
        audio_mode='silent', reuse_audio_from_run_id='old-recording-run')
    with pytest.raises(ServiceError, match='production_run_silent_audio_reuse_conflict'):
        env.runs.start(OWNER, project['id'], request, 'conflicting-silent-reuse')
    assert not children(env, 'production_runs') and not children(env, 'audio_operations')


def test_silent_resource_graph_preserves_visual_work_without_inventing_recordings(env):
    from video_workbench.production_resource_stage import production_plan, audio_node_id
    project = env.store.project(OWNER, env.project['id'])
    frozen = env.runs._freeze(project, film_aware=True)
    scene_id = frozen['scenes'][0]['id']
    value = dict(nodes=[dict(id='visual-resource', kind='animation', producer='manim',
        inputs=dict(resource_authoring=dict(kind='manim', frame_count=24)), dependencies=[],
        objective='Move through a new landscape.', required_capabilities=['manim'], key_effects=[],
        local_timing={}, placement=dict(scene_id=scene_id, start_frame=0))])
    plan = production_plan(value, frozen, deadline_at=1000, audio_mode='silent')
    assert len(plan['nodes']) == 1 and plan['nodes'][0]['dependencies'] == []
    assert plan['nodes'][0]['producer'] == 'manim' and plan['nodes'][0]['local_timing']['frame_count'] == 24
    assert production_plan(None, frozen, deadline_at=1000, audio_mode='silent') is None
    recorded = production_plan(value, frozen, deadline_at=1000)
    assert recorded['nodes'][0]['dependencies'] == [audio_node_id(scene_id)]
    assert len([node for node in recorded['nodes'] if node['producer'] == 'verified-recording']) == 2
    value['nodes'][0]['dependencies'] = [audio_node_id(scene_id)]
    with pytest.raises(ServiceError, match='production_resource_silent_recording_conflict'):
        production_plan(value, frozen, deadline_at=1000, audio_mode='silent')


def test_silent_requires_formal_renderer_instead_of_falling_back_to_old_motion(env):
    runs = ProductionRunStore(env.store, env.joint, env.motion, env.production)
    project = env.store.project(OWNER, env.project['id'])
    with pytest.raises(ServiceError, match='production_run_silent_requires_film'):
        runs.start(OWNER, project['id'], dict(expected_revision=project['revision'],
            joint_plan_version_id='adopted-plan-1', audio_mode='silent'), 'silent-never-old-motion')
    assert not children(env, 'production_runs') and not children(env, 'audio_operations')


def test_tampered_silent_audio_identity_fails_without_falling_back_to_speech(env, monkeypatch):
    calls = forbid_speech(env, monkeypatch)
    run = start_silent(env)
    with env.store.transaction() as db:
        journal = env.runs._read(db, run['id'])
        journal['items'][0]['audio_version_id'] = 'missing-recording-is-not-silence'
        env.runs._write(db, journal)
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['error'] == 'production_run_silent_audio_identity_changed'
    assert not children(env, 'audio_operations') and not children(env, 'film_module_operations')
    assert calls == []


def test_http_audio_mode_is_explicit_and_omission_preserves_old_request(tmp_path, monkeypatch):
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
            'Idempotency-Key': 'silent-http-contract'})
        url = f'/api/projects/{project["id"]}/production-runs'
        assert client.post(url, json=request).status_code == 202 and requests[-1] == request
        assert client.post(url, json=dict(request, audio_mode=None)).status_code == 202 and requests[-1] == request
        for mode in ('silent', 'narration'):
            body = dict(request, audio_mode=mode)
            assert client.post(url, json=body).status_code == 202 and requests[-1] == body
        for mode in ('', 'quiet', 0, True):
            assert client.post(url, json=dict(request, audio_mode=mode)).status_code == 422
