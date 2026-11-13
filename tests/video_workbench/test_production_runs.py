"""Audio-first durable orchestration, independent of external model providers."""
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
from uuid import uuid4
import wave

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.joint_plan_store import JointPlanStore
from video_workbench.motion_store import MotionStore
from video_workbench.production import Production
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.production_run_workflow import ProductionRunWorkflow
from video_workbench.speech import inspect_wav
from video_workbench.store import Store


OWNER = 'local-developer'


def seed(store, joint, count=2):
    project = store.create_project(OWNER, dict(title='粒子如何扩散', script='先观察整体，再观察粒子。',
        style='同一水杯', production_mode='motion'))
    for i in range(count):
        project = store.save_scene(OWNER, project['id'], dict(title=f'片段{i}', narration=f'第{i}段  原文。',
            visual_description='随粒子运动观察密度变化', duration_seconds=8, format='html'))
    with store.transaction() as db:
        project = store._project(db, OWNER, project['id'])
        project['joint_plan'] = dict(version_id='adopted-plan-1', visual_style='连续水杯')
        for scene in project['scenes']:
            scene['leading_seconds'], scene['trailing_seconds'] = .25, .5
        db.execute('INSERT INTO joint_plan_heads VALUES (?, ?, ?, ?)',
            (OWNER, project['id'], 'adopted-plan-1', joint._binding(project)))
        store._save(db, project)
    return store.project(OWNER, project['id'])


class CandidateMotion:
    """Local admission fake: persistent child keys and exact candidate pairs."""
    def __init__(self, store):
        self.store = store
        self.real = MotionStore(store)

    def start_candidate(self, owner, scene_id, request, idem, *, audio_version_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT document FROM motion_operations WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if row:
                return json.loads(row[0])
        project_id = self.store.scene_project(owner, scene_id)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, request['expected_revision'])
            op = dict(id=str(uuid4()), owner=owner, project_id=project_id, scene_id=scene_id,
                status='queued', version_id=None, audio_version_id=audio_version_id, error=None,
                cancel_requested=False, request=request)
            db.execute('INSERT INTO motion_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                (op['id'], owner, project_id, scene_id, idem, digest(request), json.dumps(op)))
            self.store._save(db, project)
            return op

    def candidate_version(self, owner, version_id, audio_version_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT document FROM motion_versions WHERE id=? AND owner=?', (version_id, owner)).fetchone()
            assert row
            version = json.loads(row[0])
            assert version['audio_version_id'] == audio_version_id
            return version

    def cancel(self, owner, op_id):
        with self.store.transaction() as db:
            op = self.real._op(db, op_id, owner)
            op['status'], op['cancel_requested'], op['error'] = 'cancelled', True, None
            self.real._write_op(db, op)

    def finish(self, op_id):
        with self.store.transaction() as db:
            op = self.real._op(db, op_id)
            version = dict(id=str(uuid4()), project_id=op['project_id'], scene_id=op['scene_id'],
                audio_version_id=op['audio_version_id'], stale=False)
            op.update(status='completed', error=None, version_id=version['id'])
            db.execute('INSERT INTO motion_versions VALUES (?, ?, ?, ?, ?, ?)',
                (version['id'], op['owner'], op['project_id'], op['scene_id'], op_id, json.dumps(version)))
            self.real._write_op(db, op)
            project = self.store._project(db, op['owner'], op['project_id'])
            self.store._save(db, project)


class CandidateProduction:
    def __init__(self, store):
        self.store = store
        self.real = Production(store)

    def start_candidate_render(self, owner, project_id, request, idem):
        with self.store.transaction() as db:
            row = db.execute('SELECT document FROM video_renders WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if row:
                return json.loads(row[0])
            job = dict(id=str(uuid4()), status='queued', error=None, owner=owner,
                project_id=project_id, purpose='draft', request=request)
            db.execute('INSERT INTO video_renders VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                (job['id'], owner, project_id, idem, digest(request), json.dumps(job), json.dumps(request)))
            return job

    def cancel(self, owner, job_id):
        return self.real.cancel(owner, job_id)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / 'production.db')
    joint = JointPlanStore(store)
    motion, production = CandidateMotion(store), CandidateProduction(store)
    runs = ProductionRunStore(store, joint, motion, production)
    project = seed(store, joint)
    return SimpleNamespace(store=store, joint=joint, motion=motion, production=production, runs=runs, project=project)


def begin(env, key='production-test-key'):
    return env.runs.start(OWNER, env.project['id'], dict(expected_revision=env.project['revision'],
        joint_plan_version_id='adopted-plan-1'), key)


def wav(seconds=1):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\x01\x00' * round(8000 * seconds))
    return buffer.getvalue()


def finish_audio(env, op_id, seconds=1):
    op = env.store.claim_audio(op_id)
    assert op is not None
    data = wav(seconds)
    env.store.finish_audio(op_id, audio=SimpleNamespace(data=data, sha256=hashlib.sha256(data).hexdigest(),
        input_sha256=op['provider_input_sha256']))


def children(env, table):
    with env.store.transaction() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT document FROM ' + table + ' ORDER BY rowid')]


def complete_audio_phase(env):
    for i in range(len(env.project['scenes'])):
        env.runs.tick()
        assert not children(env, 'motion_operations')
        finish_audio(env, children(env, 'audio_operations')[-1]['id'], seconds=1 + i)
    env.runs.tick()


def test_audio_all_first_then_candidate_motions_and_whole_draft_leave_heads_unchanged(env):
    original = deepcopy(env.store.project(OWNER, env.project['id']))
    run = begin(env)
    complete_audio_phase(env)
    state = env.runs.get(OWNER, run['id'])
    assert state['phase'] == 'motion'
    assert [item['audio_seconds'] for item in state['items']] == [1, 2]
    assert all(item['audio_sha256'] for item in state['items'])
    for i in range(2):
        op = children(env, 'motion_operations')[-1]
        assert op['audio_version_id'] == state['items'][i]['audio_version_id']
        env.motion.finish(op['id'])
        env.runs.tick()
    draft = children(env, 'video_renders')[0]
    assert [pair['scene_id'] for pair in draft['request']['selections']] == [s['id'] for s in original['scenes']]
    with env.store.transaction() as db:
        draft['status'] = 'completed'
        db.execute('UPDATE video_renders SET document=?,data=? WHERE id=?', (json.dumps(draft), b'mp4', draft['id']))
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == result['phase'] == 'completed'
    assert result['review_status'] == 'not_reviewed' and result['selection'] == 'draft_only'
    assert result['voice'] == 'zm_yunjian' and result['draft_render_id'] == draft['id']
    current = env.store.project(OWNER, env.project['id'])
    assert [s['head_audio_version_id'] for s in current['scenes']] == [None, None]
    assert [s['narration'] for s in current['scenes']] == [s['narration'] for s in original['scenes']]
    with env.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM motion_heads').fetchone()[0] == 0


def test_replay_after_lost_local_response_and_restart_creates_one_child(env, monkeypatch):
    run = begin(env)
    original = env.store.start_audio
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('response lost after local commit')
    monkeypatch.setattr(env.store, 'start_audio', interrupted)
    env.runs.tick()
    assert len(children(env, 'audio_operations')) == 1
    monkeypatch.setattr(env.store, 'start_audio', original)
    recovered = ProductionRunStore(env.store, env.joint, env.motion, env.production)
    recovered.tick()
    assert len(children(env, 'audio_operations')) == 1
    assert begin(env)['id'] == run['id']
    assert recovered.list(OWNER, env.project['id'])[0]['request_key'] == 'production-test-key'
    finish_audio(env, children(env, 'audio_operations')[0]['id'])
    recovered.tick()
    assert len(children(env, 'audio_operations')) == 2
    with pytest.raises(ServiceError) as error:
        recovered.start(OWNER, env.project['id'], dict(expected_revision=env.project['revision'] + 1,
            joint_plan_version_id='adopted-plan-1'), 'production-test-key')
    assert error.value.code == 'idempotency_conflict'


@pytest.mark.parametrize('change', ['script', 'order', 'voice', 'pronunciation', 'pause', 'head'])
def test_changed_inputs_stop_new_admission_without_replanning(env, change):
    run = begin(env)
    env.runs.tick()
    with env.store.transaction() as db:
        p = env.store._project(db, OWNER, env.project['id'])
        if change == 'script': p['script'] += '新材料'
        elif change == 'order': p['scenes'].reverse()
        elif change == 'voice': p['speech_settings'] = dict(voice='zm_yunxi')
        elif change == 'pronunciation': p['scenes'][0]['speech_settings'] = dict(pronunciation={'粒子':'例子'})
        elif change == 'pause': p['scenes'][0]['leading_seconds'] = 1
        else: db.execute('UPDATE joint_plan_heads SET version_id=?', ('new-head',))
        env.store._save(db, p)
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['status'] == 'stale'
    assert len(children(env, 'audio_operations')) == 1
    assert children(env, 'audio_operations')[0]['status'] == 'cancelled'
    assert not children(env, 'motion_operations')


def test_cancel_before_admission_and_during_unconfirmed_speech(env):
    run = begin(env)
    result = env.runs.cancel(OWNER, run['id'])
    assert result['status'] == 'cancelled'
    assert not children(env, 'audio_operations')
    env.project = env.store.project(OWNER, env.project['id'])
    run = begin(env, 'production-next-key')
    env.runs.tick()
    op = env.store.claim_audio(children(env, 'audio_operations')[0]['id'])
    result = env.runs.cancel(OWNER, run['id'])
    assert result['status'] == 'running' and result['blocked_reason'] == 'production_run_cancellation_pending'
    env.store.finish_audio(op['id'], interrupted=True)
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == 'failed' and result['blocked_reason'] == 'speech_upstream_stop_unconfirmed'
    assert not children(env, 'motion_operations')
    env.project = env.store.project(OWNER, env.project['id'])
    with pytest.raises(ServiceError, match='production_run_in_progress'):
        begin(env, 'production-third-key')


def test_uncertain_speech_is_not_resent_on_restart(env):
    run = begin(env)
    env.runs.tick()
    env.store.claim_audio(children(env, 'audio_operations')[0]['id'])
    env.store.recover_audio_operations()
    recovered = ProductionRunStore(env.store, env.joint, env.motion, env.production)
    recovered.tick()
    recovered.tick()
    result = recovered.get(OWNER, run['id'])
    assert result['status'] == 'failed' and result['error'] == 'speech_submission_uncertain'
    assert len(children(env, 'audio_operations')) == 1 and not children(env, 'motion_operations')


def test_active_motion_unknown_submit_recovers_same_child_then_continues(env):
    run = begin(env)
    complete_audio_phase(env)
    op = children(env, 'motion_operations')[0]
    with env.store.transaction() as db:
        op.update(status='submitting', error='submission_uncertain')
        db.execute('UPDATE motion_operations SET document=? WHERE id=?', (json.dumps(op), op['id']))
    recovered = ProductionRunStore(env.store, env.joint, env.motion, env.production)
    recovered.tick()
    state = recovered.get(OWNER, run['id'])
    assert state['status'] == 'running' and state['error'] == 'submission_uncertain'
    assert len(children(env, 'motion_operations')) == 1
    env.motion.finish(op['id'])
    recovered.tick()
    assert len(children(env, 'motion_operations')) == 2
    assert recovered.get(OWNER, run['id'])['items'][0]['motion_version_id']


def test_stale_running_audio_remains_tracked_until_upstream_result(env):
    run = begin(env)
    env.runs.tick()
    op = env.store.claim_audio(children(env, 'audio_operations')[0]['id'])
    with env.store.transaction() as db:
        p = env.store._project(db, OWNER, env.project['id'])
        p['script'] += ' changed'
        env.store._save(db, p)
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'stale' and state['draining'] is True
    env.store.finish_audio(op['id'], interrupted=True)
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['draining'] is False and state['blocked_reason'] == 'speech_upstream_stop_unconfirmed'
    assert len(children(env, 'audio_operations')) == 1


def test_measured_audio_plus_pauses_rejects_over_sixty_before_motion(env):
    run = begin(env)
    env.runs.tick()
    finish_audio(env, children(env, 'audio_operations')[0]['id'], 59.5)
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == 'failed' and result['error'] == 'motion_scene_duration_exceeded'
    assert not children(env, 'motion_operations')
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['draining'] is False and result['error'] == 'motion_scene_duration_exceeded'


def test_thirteen_segments_admit_and_total_over_sixty_seconds_reaches_motion(env):
    env.project = seed(env.store, env.joint, count=13)
    run = begin(env)
    assert len(run['items']) == 13
    for i in range(13):
        env.runs.tick()
        assert not children(env, 'motion_operations')
        finish_audio(env, children(env, 'audio_operations')[-1]['id'], seconds=5)
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'running' and state['phase'] == 'motion'
    assert sum(item['audio_seconds'] + .75 for item in state['items']) == 74.75
    assert len(children(env, 'audio_operations')) == 13
    assert len(children(env, 'motion_operations')) == 1


def test_disabled_admission_keeps_existing_journal_draining(env):
    begin(env)
    workflow = ProductionRunWorkflow(env.runs, enabled=False)
    with pytest.raises(ServiceError, match='production_runs_disabled'):
        workflow.require_enabled()
    workflow.tick()
    assert len(children(env, 'audio_operations')) == 1


def test_real_candidate_motion_and_multi_scene_render_snapshot_without_adoption(tmp_path):
    from video_workbench.motion_timing import MotionTiming
    from test_creative_production import empty_methods, result
    from test_motion_snapshot import RUNTIME
    store = Store(tmp_path / 'real-candidates.db')
    joint = JointPlanStore(store)
    motion = MotionStore(store)
    production = Production(store, motion_store=motion, motion_timing=MotionTiming(motion),
        motion_enabled=True, motion_renderer=object(), runtime_fingerprint=RUNTIME)
    runs = ProductionRunStore(store, joint, motion, production)
    project = seed(store, joint)
    env = SimpleNamespace(store=store, joint=joint, motion=motion, production=production, runs=runs, project=project)
    run = begin(env)
    complete_audio_phase(env)
    for i in range(2):
        op = motion.active_operations()[0]
        assert op['audio_version_id'] == runs.get(OWNER, run['id'])['items'][i]['audio_version_id']
        prepared = motion.prepare_creative(op['id'], empty_methods(store))
        motion.complete(op['id'], result(prepared['document']))
        runs.tick()
    state = runs.get(OWNER, run['id'])
    assert state['phase'] == 'draft' and state['draft_render_id']
    with store.transaction() as db:
        row = db.execute('SELECT document,snapshot FROM video_renders WHERE id=?', (state['draft_render_id'],)).fetchone()
        job, snapshot = json.loads(row[0]), json.loads(row[1])
        assert job['purpose'] == 'draft' and job['selection'] == 'draft_only' and job['subtitles'] is False
        assert [s['id'] for s in snapshot['scenes']] == [s['id'] for s in project['scenes']]
        assert [s['audio']['id'] for s in snapshot['scenes']] == [s['audio_version_id'] for s in state['items']]
        assert snapshot['duration_seconds'] == 4.5  # 1+2 seconds WAV plus .75 seconds per scene.
        assert db.execute('SELECT count(*) FROM motion_heads').fetchone()[0] == 0
    current = store.project(OWNER, project['id'])
    assert all(s['head_audio_version_id'] is None for s in current['scenes'])


def test_default_new_admission_requires_motion_gate(tmp_path):
    from test_motion_snapshot import RUNTIME
    speech = SimpleNamespace(settings=SimpleNamespace(model='kokoro-rk3588', voice='zm_yunjian', voice_discovery=False),
                             voice_catalog=[dict(id='zm_yunjian', name='云健')])
    app = create_app(db_path=tmp_path / 'gate.db', background=False, local_mode=True,
        trusted_hosts={'testserver'}, joint_plan_enabled=True, motion_enabled=False, creative_enabled=True,
        speech_client=speech, creative_gateway=object(), method_adapter=object(),
        motion_renderer=object(), motion_runtime=RUNTIME)
    with TestClient(app) as client:
        assert client.get('/api/session').json()['production_runs_enabled'] is False


def test_owner_csrf_request_key_and_gate_api(tmp_path):
    app = create_app(db_path=tmp_path / 'api.db', local_mode=False, background=False,
        production_runs_enabled=True, trusted_hosts={'testserver'})
    project = seed(app.state.store, app.state.joint_plan_store)
    with TestClient(app) as client:
        client.headers['x-forwarded-user'] = OWNER
        session = client.get('/api/session').json()
        assert session['production_runs_enabled'] is True
        url = f'/api/projects/{project["id"]}/production-runs'
        request = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1')
        assert client.post(url, json=request, headers={'Idempotency-Key':'api-production-key'}).status_code == 403
        client.headers['x-csrf-token'] = session['csrf_token']
        response = client.post(url, json=request, headers={'Idempotency-Key':'api-production-key'})
        assert response.status_code == 202
        run = response.json()
        assert client.get(url).json()['runs'][0]['request_key'] == 'api-production-key'
        assert client.get('/api/production-runs/' + run['id']).status_code == 200
        client.headers['x-forwarded-user'] = 'other-owner'
        assert client.get('/api/production-runs/' + run['id']).status_code == 404
        client.headers['x-csrf-token'] = client.get('/api/session').json()['csrf_token']
        assert client.post('/api/production-runs/' + run['id'] + '/cancel').status_code == 404
    disabled = create_app(db_path=tmp_path / 'default.db', background=False, local_mode=True,
        trusted_hosts={'testserver'})
    with TestClient(disabled) as client:
        assert client.get('/api/session').json()['production_runs_enabled'] is False
