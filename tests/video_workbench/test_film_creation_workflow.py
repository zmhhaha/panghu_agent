"""Durable business children with real frozen Skill/worker contracts, no model.

The fake Agent peer proves orchestration and custody, not visual quality or
production admission. The renderer writes deliberately synthetic media bytes.
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
from video_workbench.animation_runtime_capabilities import SCHEMA as CAPS_SCHEMA
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.film_creation_store import FilmCreationStore
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from video_workbench.hyperframes_film_browser import HOST_NAMES
from video_workbench.production_film_context import freeze_production_inputs
from video_workbench.store import Store
from test_creative_film_module_contract import raw_result, observe, RUNTIME
from test_animation_skill_production import published
from test_film_render_adapter import result_metadata, VIDEO
from test_production_runs import wav

OWNER = 'film-owner'


class StoppedGateway:
    profile = P.PROFILE

    def __init__(self, creation, adapter, root):
        self.creation, self.skill_adapter, self.root = creation, adapter, root
        self.calls, self.jobs, self.documents = [], {}, {}
        self.ready, self.lose_submit, self.lookup_missing = True, False, False
        self.state, self.proven = 'completed', True

    def preflight(self, deadline):
        if not self.ready:
            raise ServiceError('skill_production_profile_unavailable', 503)
        return dict(ready=True)

    def upload(self, document):
        self.calls.append(('upload', digest(document)))
        return dict(schema='fixture-input-receipt', sha256=digest(document))

    def prepare_submission(self, identity, document, deadline, input_ref):
        self.preflight(deadline)
        self.documents[identity] = deepcopy(document)
        def admit():
            durable = self.creation.operation('module', identity)
            assert durable['submit_attempted'] and durable['input_ref'] == input_ref
            assert durable['document'] == document and durable['input_sha256'] == digest(document)
            self.calls.append(('submit', identity))
            self.jobs[identity] = str(uuid4())
            if self.lose_submit:
                raise ServiceError('service_unavailable', 503)
            return dict(job_id=self.jobs[identity])
        return admit

    def lookup(self, identity):
        self.calls.append(('lookup', identity))
        if self.lookup_missing or identity not in self.jobs:
            raise ServiceError('not_found', 404)
        return dict(job_id=self.jobs[identity])

    def poll(self, job_id):
        identity = next(key for key, value in self.jobs.items() if value == job_id)
        attempt = str(uuid4())
        return dict(job_id=job_id, state=self.state, stop_proven=self.proven,
            pins=dict(profile=P.PROFILE, profile_revision='1'),
            attempt=dict(attempt_id=attempt, fence=1),
            stop_proof=dict(stop_proven=self.proven, attempt_id=attempt, fence=1,
                request_sha256='a' * 64, result_sha256='b' * 64,
                resource_ids=['fixture-stopped-agent'], observed_at=1000))

    def collect(self, job_id, checksum, document):
        assert digest(document) == checksum
        delivery = self.skill_adapter.get_bundle(OWNER, document['skill_catalog']['consumer_id'], document['skill_catalog']['sha256'])
        workspace = self.root / job_id[:8]
        files = [InlineFile.from_bytes(P.INPUT_NAME, canonical_bytes(document)),
            *bundle_files(delivery['catalog'], delivery['packages'])]
        for item in files:
            target = workspace / 'inputs' / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.content())
        handler = P.VideoAnimationFilmModuleProfile()
        observer = handler.create_tool_observer(workspace, {'input_sha256': checksum})
        raw = raw_result(document)
        for key in delivery['packages']:
            observe(observer, 'skill_search', {'query': 'subject relationship'}, 'search-' + key)
            observe(observer, 'skill_read', {'package_sha256': key}, 'read-' + key)
            raw['skill_decisions'].append(dict(package_sha256=key, action='adapt', reason='Preserve one subject.',
                scene_targets=[dict(target_id='subject', description='Same subject.',
                    source_file=raw['module']['entry'], source_anchor='node.id="subject"')]))
        (workspace / 'output.json').write_bytes(canonical_bytes(raw))
        attempt = str(uuid4())
        envelope = SimpleNamespace(profile=P.PROFILE, profile_revision='1', attempt_id=attempt,
            task={'input_sha256': checksum}, inputs=files, checkpoint_inputs=[], checkpoint_ref=None)
        artifacts = handler.collect_output_with_host_evidence(workspace, envelope,
            StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('fixture-stopped-agent',)),
            observer.finish(attempt))
        result, evidence = [json.loads(item.content()) for item in artifacts]
        return dict(result=result, evidence=evidence, skill_delivery=delivery)

    def cancel(self, job_id):
        self.calls.append(('cancel', job_id))


class WholeRenderer:
    def __init__(self, creation):
        self.creation, self.calls, self.uncertain = creation, [], False

    def render(self, snapshot, directory, **options):
        durable = self.creation.operation('render', options['operation_id'])
        assert durable['submit_attempted'] and durable['snapshot_sha256'] == snapshot['sha256']
        self.calls.append((deepcopy(snapshot), options))
        if self.uncertain:
            raise ServiceError('motion_render_service_unavailable', 503)
        if options['cancel_event'].is_set():
            error = ServiceError('media_render_cancelled', 409)
            error.stop_proven = True
            raise error
        path = Path(directory) / 'video.mp4'
        path.write_bytes(VIDEO)
        return dict(result_metadata(snapshot), path=str(path))


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / 'business.db')
    project = store.create_project(OWNER, dict(title='同一对象的关系变化', script='先认识对象，再观察关系。',
        style='空间关系讲解', production_mode='motion'))
    for index in range(2):
        project = store.save_scene(OWNER, project['id'], dict(title=f'段落{index}', narration=f'第{index}段对象关系。',
            visual_description='保持同一对象的身份', duration_seconds=1, format='html'))
    audio_ids = []
    for index, scene in enumerate(project['scenes']):
        project = store.upload_audio(OWNER, scene['id'], {'expected_revision': project['revision']},
            f'upload-recording-{index}', wav(1))
        audio_ids.append(project['scenes'][index]['audio_versions'][-1]['id'])
    rows = []
    for index, (scene, version_id) in enumerate(zip(project['scenes'], audio_ids)):
        audio = store.audio_version(OWNER, version_id)
        rows.append(dict(scene_id=scene['id'], title=scene['title'], narration=scene['narration'],
            visual_intent=scene['visual_description'], audio_version_id=version_id, audio_sha256=audio['sha256'],
            audio_seconds=1, start_seconds=index, end_seconds=index + 1,
            leading_silence_seconds=0, trailing_silence_seconds=0))
    film = dict(project_id=project['id'], joint_plan_version_id='frozen-plan',
        content_binding_sha256=digest(freeze_production_inputs(store, project, film_aware=True)),
        title=project['title'], script=project['script'], understanding_goal=project['title'], style=project['style'],
        scenes=rows, total_seconds=2)
    root = Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer'
    hosts = {name: (root / name).read_bytes() for name in HOST_NAMES}
    caps = dict(schema=CAPS_SCHEMA, allowed_engines=['html-svg'], assets={},
        runtime_identity=dict(image_digest=RUNTIME['image_digest'],
            host_sha256=digest({name: hashlib.sha256(raw).hexdigest() for name, raw in hosts.items()}),
            engine_runtime_sha256=RUNTIME['engine_runtime_sha256'], admission_id='synthetic-business-fixture'))
    creation = FilmCreationStore(store, runtime_capabilities=caps, runtime_fingerprint=RUNTIME,
        host_files=hosts, trusted_host_sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in hosts.items()},
        image='registry.internal/video-motion-renderer:latest@' + RUNTIME['image_digest'], assets={}, enabled=True)
    catalog_calls, methods = [], []
    def catalogue(owner):
        catalog_calls.append(owner)
        return deepcopy(methods)
    adapter = SkillProductionAdapter(catalogue, None, tmp_path / 'skills.sqlite3')
    gateway = StoppedGateway(creation, adapter, tmp_path / 'worker')
    renderer = WholeRenderer(creation)
    workflow = FilmCreationWorkflow(creation, gateway, adapter, renderer, enabled=True, render_readiness=lambda: True)
    return SimpleNamespace(store=store, creation=creation, workflow=workflow, gateway=gateway,
        renderer=renderer, adapter=adapter, project=project, film=film, catalog_calls=catalog_calls, methods=methods)


def module_request(env, index=0, predecessor=None):
    return dict(expected_revision=env.project['revision'], film=deepcopy(env.film), film_sha256=digest(env.film),
        current_scene_index=index, predecessor_version_id=predecessor,
        target=dict(module_id=f'module-{index}', namespace=f'scene_{index}',
            transition_target=dict(module_id='transition-0', namespace='transition_0') if index else None),
        instruction='', configuration_sha256=env.creation.configuration_sha256)


def start_module(env, index=0, predecessor=None, idem=None):
    return env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][index]['id'],
        module_request(env, index, predecessor), idem or f'film-module-request-{index}')


def completed_modules(env):
    first = start_module(env)
    env.workflow.tick()
    first = env.workflow.get_module(OWNER, first['id'])
    second = start_module(env, 1, first['version_id'])
    env.workflow.tick()
    second = env.workflow.get_module(OWNER, second['id'])
    return [first, second]


def render_request(env, modules):
    return dict(expected_revision=env.project['revision'], film=deepcopy(env.film), film_sha256=digest(env.film),
        module_version_ids=[op['version_id'] for op in modules],
        module_targets=[module_request(env, index)['target'] for index in range(2)],
        production_run_id='production-whole-film', timeout_seconds=600,
        configuration_sha256=env.creation.configuration_sha256)


def test_module_intent_and_skill_resources_commit_before_one_stopped_agent_admission(env):
    env.methods.append(published(0))
    op = start_module(env)
    assert env.gateway.calls == [] and env.catalog_calls == []
    assert op['request_sha256'] == digest(module_request(env))
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'completed' and result['stop_proven']
    package = env.workflow.module_package(OWNER, result['version_id'])
    assert package['result']['module']['lifecycle']['exports']['render_at'] == 'renderAt'
    assert {event['tool_name'] for event in package['skill_tool_evidence']['events']} == {'skill_search', 'skill_read'}
    assert env.workflow.module_by_request_key(OWNER, op['request_key']) == result
    assert len([call for call in env.gateway.calls if call[0] == 'submit']) == 1
    assert start_module(env)['id'] == op['id']
    assert env.catalog_calls == [OWNER]


def test_exact_predecessor_versions_and_real_wav_freeze_one_whole_render(env):
    modules = completed_modules(env)
    assert all(op['status'] == 'completed' for op in modules)
    request = render_request(env, modules)
    render = env.workflow.start_render(OWNER, env.project['id'], request, 'whole-film-render-key')
    assert env.renderer.calls == []
    env.workflow.tick()
    result = env.workflow.get_render(OWNER, render['id'])
    assert result['status'] == 'completed' and result['stop_proven']
    assert result['renderer_mode'] == 'hyperframes-film' and result['quality'] == 'draft'
    assert result['selection'] == 'draft_only' and result['content_state'] == 'current'
    assert env.workflow.video(OWNER, render['id']) == VIDEO
    assert len(env.renderer.calls) == 1
    snapshot = env.renderer.calls[0][0]
    assert len(snapshot['module_sha256']) == len(snapshot['recording_sha256']) == 2
    assert env.workflow.renders(OWNER, env.project['id'])[0]['id'] == render['id']
    current = env.store.project(OWNER, env.project['id'])
    assert all(scene.get('head_version_id') is None and scene.get('head_audio_version_id') is None for scene in current['scenes'])


def test_lost_submit_response_is_recovered_by_original_key_without_resubmission(env):
    env.gateway.lose_submit = True
    op = start_module(env)
    env.workflow.tick()
    assert env.workflow.get_module(OWNER, op['id'])['status'] == 'submitting'
    resumed = FilmCreationWorkflow(env.creation, env.gateway, env.adapter, env.renderer,
        enabled=False, render_readiness=None)
    resumed.tick()
    assert resumed.get_module(OWNER, op['id'])['status'] == 'completed'
    assert [call[0] for call in env.gateway.calls].count('submit') == 1
    assert [call[0] for call in env.gateway.calls].count('lookup') == 1


def test_missing_lookup_after_uncertain_admission_never_claims_cancelled_or_stopped(env):
    env.gateway.lose_submit, env.gateway.lookup_missing = True, True
    op = start_module(env)
    env.workflow.tick()
    env.workflow.cancel_module(OWNER, op['id'])
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'cancelling' and result['stop_proven'] is False
    assert result['error'] == 'film_module_submission_unconfirmed'


@pytest.mark.parametrize('state', ['failed', 'cancelled', 'timed_out', 'quarantined'])
def test_terminal_agent_state_without_stop_proof_remains_unresolved(env, state):
    env.gateway.state, env.gateway.proven = state, False
    op = start_module(env)
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'cancelling' and result['stop_proven'] is False


def test_cancel_before_admission_never_freezes_skills_or_submits_agent(env):
    op = start_module(env)
    env.workflow.cancel_module(OWNER, op['id'])
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'cancelled' and result['stop_proven'] is True
    assert env.gateway.calls == env.catalog_calls == []


def test_disabled_or_unadmitted_new_path_never_falls_back(env):
    env.workflow.render_readiness = lambda: False
    with pytest.raises(ServiceError, match='hyperframes_film_not_admitted'):
        start_module(env)
    assert env.creation.active_operations() == [] and env.gateway.calls == []


def test_owner_and_idempotency_bindings_and_config_are_strict(env):
    op = start_module(env)
    with pytest.raises(ServiceError, match='not_found'):
        env.workflow.get_module('other-owner', op['id'])
    changed = module_request(env); changed['instruction'] = 'Different request'
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'], changed, op['request_key'])
    changed['configuration_sha256'] = 'a' * 64
    with pytest.raises(ServiceError, match='configuration_changed'):
        env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'], changed, 'another-request-key')


def test_predecessor_must_be_completed_same_project_previous_scene(env):
    with pytest.raises(ServiceError, match='not_found'):
        start_module(env, 1, 'missing-version')
    op = start_module(env)
    env.workflow.tick()
    first = env.workflow.get_module(OWNER, op['id'])
    request = module_request(env, 0); request['predecessor_version_id'] = first['version_id']
    with pytest.raises(ServiceError, match='predecessor_mismatch'):
        env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'], request, 'wrong-predecessor-key')


def test_render_transport_uncertainty_reuses_same_frozen_snapshot_and_operation(env):
    modules = completed_modules(env)
    op = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'uncertain-render-key')
    env.renderer.uncertain = True
    env.workflow.tick()
    assert env.workflow.get_render(OWNER, op['id'])['stop_proven'] is False
    env.renderer.uncertain = False
    env.workflow.tick()
    assert env.workflow.get_render(OWNER, op['id'])['status'] == 'completed'
    assert env.renderer.calls[0][0] == env.renderer.calls[1][0]
    assert env.renderer.calls[0][1]['operation_id'] == env.renderer.calls[1][1]['operation_id']


def test_completed_render_staleness_is_computed_without_losing_historical_download(env):
    modules = completed_modules(env)
    op = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'staleness-render-key')
    env.workflow.tick()
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        project['script'] = '新的讲解文稿'
        env.store._save(db, project)
    assert env.workflow.get_render(OWNER, op['id'])['content_state'] == 'stale'
    assert env.workflow.renders(OWNER, env.project['id'])[0]['content_state'] == 'stale'
    assert env.workflow.video(OWNER, op['id']) == VIDEO


def test_failed_preflight_before_submit_is_terminal_and_proves_no_remote_writer(env):
    op = start_module(env)
    env.gateway.ready = False
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'failed' and result['stop_proven'] is True
    assert env.gateway.calls == []


def test_changed_configuration_blocks_only_new_admission(env):
    op = start_module(env)
    env.creation.configuration['sha256'] = 'b' * 64
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'failed' and result['stop_proven'] is True
    assert result['error'] == 'film_creation_configuration_changed'
    assert env.gateway.calls == []


def test_non_one_profile_revision_is_not_admitted_under_the_v1_worker_contract(env):
    env.gateway.profile_revision = '2'
    with pytest.raises(ServiceError, match='film_creation_unavailable'):
        start_module(env)
    assert env.creation.active_operations() == []


def test_changed_original_wav_is_rejected_before_any_skill_or_agent_work(env):
    with env.store.transaction() as db:
        version_id = env.film['scenes'][0]['audio_version_id']
        db.execute('UPDATE audio_versions SET data=? WHERE id=?', (wav(.5), version_id))
    with pytest.raises(ServiceError):
        start_module(env)
    assert env.gateway.calls == env.catalog_calls == []


def test_failed_collection_after_real_stop_proof_does_not_persist_a_candidate(env):
    collect = env.gateway.collect
    def corrupt(*args):
        result = collect(*args)
        result['result']['module']['film_sha256'] = 'f' * 64
        return result
    env.gateway.collect = corrupt
    op = start_module(env)
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'failed' and result['stop_proven'] is True
    assert result['version_id'] is None


def test_uncertain_render_is_not_marked_cancelled_until_remote_reports_stop(env):
    modules = completed_modules(env)
    op = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'cancelled-render-key')
    env.renderer.uncertain = True
    env.workflow.tick()
    env.workflow.cancel_render(OWNER, op['id'])
    env.workflow.tick()
    assert env.workflow.get_render(OWNER, op['id'])['stop_proven'] is False
    env.renderer.uncertain = False
    env.workflow.tick()
    result = env.workflow.get_render(OWNER, op['id'])
    assert result['status'] == 'cancelled' and result['stop_proven'] is True
    with pytest.raises(ServiceError, match='video_not_ready'):
        env.workflow.video(OWNER, op['id'])


def test_process_shutdown_exits_wait_without_cancelling_and_restart_recovers_original_render(env):
    modules = completed_modules(env)
    op = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'restart-render-key')
    render = env.renderer.render
    def shutdown_during_wait(*args, **kwargs):
        env.workflow.stopping.set()
        return render(*args, **kwargs)
    env.renderer.render = shutdown_during_wait
    env.workflow.tick()
    waiting = env.workflow.get_render(OWNER, op['id'])
    assert waiting['status'] == 'submitting' and waiting['stop_proven'] is False
    assert waiting['cancel_requested'] is False and waiting['error'] == 'film_creation_restart_pending'
    env.renderer.render = render
    resumed = FilmCreationWorkflow(env.creation, env.gateway, env.adapter, env.renderer, enabled=False)
    resumed.tick()
    result = resumed.get_render(OWNER, op['id'])
    assert result['status'] == 'completed' and result['stop_proven'] is True
    assert env.renderer.calls[0][1]['operation_id'] == env.renderer.calls[1][1]['operation_id']
    assert env.renderer.calls[0][0] == env.renderer.calls[1][0]


def test_cancel_error_code_alone_is_not_proof_that_remote_execution_stopped(env):
    modules = completed_modules(env)
    op = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'unproven-cancel-key')
    def unproven(*args, **kwargs):
        raise ServiceError('media_render_cancelled', 409)
    env.renderer.render = unproven
    env.workflow.tick()
    result = env.workflow.get_render(OWNER, op['id'])
    assert result['status'] == 'submitting' and result['stop_proven'] is False


@pytest.mark.parametrize('change', ['title', 'goal', 'visual', 'clock'])
def test_film_cannot_reuse_a_valid_content_hash_with_altered_semantics_or_clock(env, change):
    request = module_request(env)
    if change == 'title': request['film']['title'] = '借用旧摘要的新标题'
    elif change == 'goal': request['film']['understanding_goal'] = '借用旧摘要的新理解目标'
    elif change == 'visual': request['film']['scenes'][0]['visual_intent'] = '完全不同的画面意图'
    else:
        request['film']['scenes'][0]['leading_silence_seconds'] = .25
        request['film']['scenes'][0]['end_seconds'] += .25
        request['film']['scenes'][1]['start_seconds'] += .25
        request['film']['scenes'][1]['end_seconds'] += .25
        request['film']['total_seconds'] += .25
    request['film_sha256'] = digest(request['film'])
    with pytest.raises(ServiceError, match='project_binding_mismatch'):
        env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'], request, 'forged-frozen-film')
    assert env.gateway.calls == env.catalog_calls == []


def test_without_release_configuration_history_downloads_and_submitted_render_recovers_same_operation(env):
    modules = completed_modules(env)
    request = render_request(env, modules)
    historical = env.workflow.start_render(OWNER, env.project['id'], request, 'history-render-key')
    env.workflow.tick()
    pending = env.workflow.start_render(OWNER, env.project['id'], request, 'recover-without-config')
    env.renderer.uncertain = True
    env.workflow.tick()
    recovery = FilmCreationStore.for_recovery(env.store)
    assert recovery.enabled is False and recovery.configuration_sha256 is None
    assert recovery.video(OWNER, historical['id']) == VIDEO
    assert len(recovery.renders(OWNER, env.project['id'])) == 2
    resumed = FilmCreationWorkflow(recovery, env.gateway, env.adapter, env.renderer, enabled=False)
    with pytest.raises(ServiceError, match='film_creation_unavailable'):
        resumed.readiness()
    env.renderer.uncertain = False
    resumed.tick()
    assert resumed.get_render(OWNER, pending['id'])['status'] == 'completed'
    assert resumed.video(OWNER, pending['id']) == VIDEO
    assert env.renderer.calls[-2][0] == env.renderer.calls[-1][0]
    assert env.renderer.calls[-2][1]['operation_id'] == env.renderer.calls[-1][1]['operation_id']
    assert env.renderer.calls[-1][1]['image'] == env.creation.image


def test_current_invalid_editing_state_marks_history_stale_instead_of_hiding_it(env):
    modules = completed_modules(env)
    op = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'invalid-current-history')
    env.workflow.tick()
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        project['scenes'][0]['visual_description'] = 'x' * 20000
        env.store._save(db, project)
    assert env.workflow.get_render(OWNER, op['id'])['content_state'] == 'stale'
    assert env.workflow.render_by_request_key(OWNER, 'invalid-current-history')['content_state'] == 'stale'
    assert env.workflow.renders(OWNER, env.project['id'])[0]['content_state'] == 'stale'
    assert env.workflow.video(OWNER, op['id']) == VIDEO
