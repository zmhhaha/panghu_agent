"""Audio-first native-module orchestration with real local child stores.

Synthetic stopped Agent output and nonmedia bytes test durable custody only;
these fixtures do not call models, render a browser, or claim human approval.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import bundle_files
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_film_module as P
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.creative_film_module_contract import predecessor_from_package
from video_workbench.film_creation_store import FilmCreationStore
from video_workbench.hyperframes_film_browser import HOST_NAMES
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
from video_workbench.joint_plan_store import JointPlanStore
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.store import Store
from test_creative_film_module_contract import raw_result, RUNTIME
from test_production_runs import OWNER, CandidateMotion, CandidateProduction, children, finish_audio, seed


class CreationFacade:
    """Local readiness and child-store API; no external admission is asserted."""
    def __init__(self, creation):
        self.creation = creation
        self.ready = True
        self.identity = creation.configuration_sha256
        self.cancel_calls = []

    def __getattr__(self, name):
        return getattr(self.creation, name)

    def readiness(self):
        if not self.ready:
            raise ServiceError('film_creation_not_ready', 503)
        return dict(available=True, renderer_mode='hyperframes-film', configuration_sha256=self.identity,
            max_seconds=60, fps=24)

    def freeze_configuration(self, owner, project_id):
        self.creation.store.project(owner, project_id)
        return self.readiness()

    def cancel_module(self, owner, child_id):
        self.cancel_calls.append(('module', child_id))
        return self.creation.cancel_module(owner, child_id)

    def cancel_render(self, owner, child_id):
        self.cancel_calls.append(('render', child_id))
        return self.creation.cancel_render(owner, child_id)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / 'business.db')
    joint = JointPlanStore(store)
    motion, production = CandidateMotion(store), CandidateProduction(store)
    project = seed(store, joint)
    renderer = Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer'
    hosts = {name: (renderer / name).read_bytes() for name in HOST_NAMES}
    host_sha = {name: hashlib.sha256(raw).hexdigest() for name, raw in hosts.items()}
    caps = dict(schema='video.animation-runtime-capabilities.v1', allowed_engines=['html-svg'], assets={},
        runtime_identity=dict(image_digest=RUNTIME['image_digest'], host_sha256=digest(host_sha),
            engine_runtime_sha256=RUNTIME['engine_runtime_sha256'], admission_id='synthetic-run-fixture'))
    creation = FilmCreationStore(store, runtime_capabilities=caps, runtime_fingerprint=RUNTIME,
        host_files=hosts, trusted_host_sha256=host_sha,
        image='registry.internal/renderer:latest@' + RUNTIME['image_digest'], assets={}, enabled=True)
    facade = CreationFacade(creation)
    runs = ProductionRunStore(store, joint, motion, production, film_creation=facade)
    adapter = SkillProductionAdapter(lambda _: [], None, tmp_path / 'skills.db')
    return SimpleNamespace(store=store, joint=joint, motion=motion, production=production, project=project,
        creation=creation, facade=facade, runs=runs, adapter=adapter, path=tmp_path)


def begin(env, key='native-film-run-test'):
    project = env.store.project(OWNER, env.project['id'])
    return env.runs.start(OWNER, project['id'], dict(expected_revision=project['revision'],
        joint_plan_version_id='adopted-plan-1'), key)


def audio_phase(env, *, seconds=(1, 2)):
    for duration in seconds:
        env.runs.tick()
        assert not children(env, 'film_module_operations')
        finish_audio(env, children(env, 'audio_operations')[-1]['id'], duration)
    env.runs.tick()


def finish_module(env, operation_id):
    pending = env.creation.operation('module', operation_id)
    task = pending['document']['core_task']
    delivery = env.adapter.freeze(OWNER, 'video-film-module:' + operation_id, task,
        task['constraints']['allowed_engines'])
    prepared = env.creation.prepare_module(operation_id, env.adapter)
    document = prepared['document']
    files = [InlineFile.from_bytes(P.INPUT_NAME, canonical_bytes(document)),
        *bundle_files(delivery['catalog'], delivery['packages'])]
    workspace = env.path / ('agent-output-' + operation_id)
    for item in files:
        path = workspace / 'inputs' / item.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.content())
    (workspace / 'output.json').write_bytes(canonical_bytes(raw_result(document)))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=P.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(document)}, inputs=files, checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True,
        resource_ids=('synthetic-stopped-module-writer',))
    handler = P.VideoAnimationFilmModuleProfile()
    evidence = handler.create_tool_observer(workspace, envelope.task).finish(attempt)
    artifacts = handler.collect_output_with_host_evidence(workspace, envelope, proof, evidence)
    result, tool_evidence = [json.loads(item.content()) for item in artifacts]
    return env.creation.complete_module(operation_id, dict(result=result, evidence=tool_evidence,
        skill_delivery=delivery))


def module_phase(env):
    for index in range(len(env.project['scenes'])):
        operations = children(env, 'film_module_operations')
        assert len(operations) == index + 1 and not children(env, 'film_render_operations')
        finish_module(env, operations[index]['id'])
        env.runs.tick()


def finish_render(env, operation_id):
    snapshot = env.creation.render_snapshot(operation_id)
    data = b'explicit-nonmedia-whole-film-fixture'
    count = snapshot['clock']['frame_count']
    metadata = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
        snapshot_sha256=snapshot['sha256'], runtime_fingerprint=RUNTIME,
        visual_mode='hyperframes-film', quality='draft', silent=False, subtitles=False,
        audio_sample_rate=48000, audio_channels=2, width=snapshot['width'], height=snapshot['height'],
        fps=24, video_codec='h264', audio_codec='aac', duration_seconds=count / 24,
        expected_duration_seconds=count / 24,
        film_export=dict(bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
            project_manifest_sha256=snapshot['project_manifest_sha256'], concatenated_scene_videos=False,
            clock=dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000),
            **{name: 'b' * 64 for name in ('export_receipt_sha256', 'capture_sha256', 'audio_mix_sha256',
                'job_result_sha256', 'job_binding', 'admission_sha256')}))
    return env.creation.complete_render(operation_id, metadata, data)


def test_all_recordings_then_exact_native_modules_then_one_film_without_old_motion_or_adoption(env):
    before = deepcopy(env.store.project(OWNER, env.project['id']))
    run = begin(env)
    assert run['renderer_mode'] == run['draft_render_source'] == 'hyperframes-film'
    audio_phase(env)
    state = env.runs.get(OWNER, run['id'])
    assert state['phase'] == 'module' and state['voice'] == 'zm_yunjian'
    first = children(env, 'film_module_operations')[0]
    assert first['request']['predecessor_version_id'] is None
    assert len(children(env, 'audio_operations')) == 2
    assert first['request']['film']['total_seconds'] == 4.5
    finish_module(env, first['id'])
    env.runs.tick()
    second = children(env, 'film_module_operations')[1]
    first_done = env.creation.get_module(OWNER, first['id'])
    assert second['request']['predecessor_version_id'] == first_done['version_id']
    assert second['document']['core_task']['film_context']['predecessor'] == predecessor_from_package(
        env.creation.module_package(OWNER, first_done['version_id']))
    assert second['request']['film'] == first['request']['film']
    finish_module(env, second['id'])
    env.runs.tick()
    render = children(env, 'film_render_operations')[0]
    snapshot = env.creation.render_snapshot(render['id'])
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=RUNTIME, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    assert len(snapshot['module_sha256']) == 2 and len(snapshot['recording_sha256']) == 2
    assert not any(name.endswith('.mp4') for name in files)
    for index, version in enumerate(state['items']):
        assert files[f'project/recordings/{index:03d}.wav'] == env.store.audio_version(
            OWNER, version['audio_version_id'])['data']
    finish_render(env, render['id'])
    env.runs.tick()
    result = env.runs.get(OWNER, run['id'])
    assert result['status'] == result['phase'] == 'completed'
    assert result['draft_render_id'] == render['id'] and result['review_status'] == 'not_reviewed'
    assert result['selection'] == 'draft_only' and result['progress'] == {'done': 5, 'total': 5}
    assert all(item['module_version_id'] and item['module_status'] == 'completed' for item in result['items'])
    assert all(item['motion_version_id'] is None and item['motion_operation_id'] is None for item in result['items'])
    assert not children(env, 'motion_operations') and not children(env, 'video_renders')
    after = env.store.project(OWNER, env.project['id'])
    assert [scene['head_audio_version_id'] for scene in after['scenes']] == [None, None]
    assert [scene['narration'] for scene in after['scenes']] == [scene['narration'] for scene in before['scenes']]


@pytest.mark.parametrize('phase', ['module', 'render'])
def test_lost_local_child_response_restart_finds_same_frozen_intent_without_reissue(env, monkeypatch, phase):
    run = begin(env)
    original = env.facade.start_module if phase == 'module' else env.facade.start_render
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('lost after the local transaction committed')
    if phase == 'module':
        monkeypatch.setattr(env.facade, 'start_module', lost)
    audio_phase(env)
    if phase == 'render':
        monkeypatch.setattr(env.facade, 'start_render', lost)
        module_phase(env)
    table = 'film_module_operations' if phase == 'module' else 'film_render_operations'
    count = len(children(env, table))
    recovered = ProductionRunStore(env.store, env.joint, env.motion, env.production, film_creation=env.facade)
    recovered.tick()
    assert len(children(env, table)) == count
    assert recovered.get(OWNER, run['id'])['status'] == 'running'
    with env.store.transaction() as db:
        document = recovered._read(db, run['id'])
    for intent in document['intents'].values():
        assert set(intent['request']) == {'expected_revision'}
        assert 'film' not in intent and 'module' not in intent


def test_unavailable_start_rejects_before_recordings_and_historical_replay_keeps_mode(env):
    env.facade.ready = False
    with pytest.raises(ServiceError, match='not_ready'):
        begin(env)
    assert not children(env, 'audio_operations')
    env.facade.ready = True
    historical = ProductionRunStore(env.store, env.joint, env.motion, env.production)
    request = dict(expected_revision=env.project['revision'], joint_plan_version_id='adopted-plan-1')
    old = historical.start(OWNER, env.project['id'], request, 'historical-run-key')
    env.facade.ready = False
    replay = env.runs.start(OWNER, env.project['id'], request, 'historical-run-key')
    assert replay['id'] == old['id'] and replay['renderer_mode'] is None
    env.runs.tick()
    assert not children(env, 'film_module_operations')
    assert len(children(env, 'audio_operations')) == 1


def test_configuration_change_stops_new_module_after_recordings_without_legacy_fallback(env):
    run = begin(env)
    audio_phase(env)
    first = children(env, 'film_module_operations')[0]
    finish_module(env, first['id'])
    env.facade.identity = 'c' * 64
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'failed' and state['error'] == 'production_run_film_configuration_changed'
    assert len(children(env, 'film_module_operations')) == 1
    assert not children(env, 'motion_operations') and not children(env, 'film_render_operations')


def test_full_film_over_runtime_limit_is_rejected_after_measuring_original_recordings(env):
    run = begin(env)
    audio_phase(env, seconds=(30, 30))
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'failed' and state['error'] == 'production_run_film_too_long'
    assert not children(env, 'film_module_operations') and not children(env, 'motion_operations')


def test_clearly_overlong_confirmed_plan_is_rejected_before_any_paid_child(env):
    env.project = seed(env.store, env.joint, count=13)  # 13 planned scenes * 8s.
    with pytest.raises(ServiceError, match='production_run_film_plan_too_long'):
        begin(env)
    assert not children(env, 'audio_operations')
    assert not children(env, 'film_module_operations') and not children(env, 'film_render_operations')


@pytest.mark.parametrize('phase', ['module', 'render'])
def test_cancel_tracks_unknown_child_until_original_stop_is_proven(env, phase):
    run = begin(env)
    audio_phase(env)
    if phase == 'render':
        module_phase(env)
    table = 'film_module_operations' if phase == 'module' else 'film_render_operations'
    child = children(env, table)[-1]
    env.creation.claim_submission(phase, child['id'])
    state = env.runs.cancel(OWNER, run['id'])
    assert state['status'] == 'running' and state['draining'] is True
    assert state['blocked_reason'] == 'production_run_cancellation_pending'
    assert (phase, child['id']) in env.facade.cancel_calls
    env.runs.tick()
    assert len(children(env, table)) == (1 if phase == 'module' else 1)
    env.creation.update_operation(phase, child['id'], status='cancelled', stop_proven=True)
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'cancelled' and state['draining'] is False
    assert not children(env, 'motion_operations') and not children(env, 'video_renders')
