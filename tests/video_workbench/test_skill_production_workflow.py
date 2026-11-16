"""Real production stores, WAV clocks, HTTP custody and native render boundary.

No external model or visual approval. Production v3 is not admitted by these
offline tests; missing readiness must fail before the durable submission claim.
"""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.config import SkillProductionAgentSettings, creative_runtime_capabilities
from video_workbench.creative_contract import scene_source, validate_scene_package
from video_workbench.creative_renderer import SKILL_COMPILER, compile_creative_project
from video_workbench.creative_snapshot import freeze_snapshot_v4, verify_snapshot_v4
from video_workbench.joint_plan_store import JointPlanStore
from video_workbench.motion_store import MotionStore, SKILL_PENDING_SCHEMA
from video_workbench.motion_timing import MotionTiming
from video_workbench.motion_workflow import MotionWorkflow
from video_workbench.motion_renderer.job import freeze_input
from video_workbench.production import Production
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.skill_production_gateway import SkillProductionAgentGateway, UPLOAD_SCHEMA
from video_workbench.store import Store
from test_creative_engine_renderer import RUNTIME_V2
from test_hyperframes_runtime_identity import CORE_SHA
from test_creative_film_renderer import film_scene
from test_creative_renderer import node_verify
from test_production_runs import OWNER, seed, begin, complete_audio_phase
from test_skill_production_gateway import Peer, TOKEN, method


RUNTIME_CAPABILITIES = dict(schema='video.animation-runtime-capabilities.v1',
    allowed_engines=['html-svg', 'canvas', 'three'],
    assets={name: RUNTIME_V2[key] for name, key in (
        ('gsap.min.js', 'gsap_sha256'), ('NotoSansCJK-Regular.ttc', 'font_sha256'),
        ('three.module.min.js', 'three_sha256'), ('three.LICENSE.txt', 'three_license_sha256'))},
    runtime_identity=dict(image_digest=RUNTIME_V2['image_digest'],
        host_sha256=RUNTIME_V2['creative_host_sha256'],
        engine_runtime_sha256=RUNTIME_V2['engine_runtime_sha256'], admission_id='synthetic-formal-admission'))
RUNTIME_V3 = dict(RUNTIME_V2, schema='video.motion-runtime.v3',
    hyperframes_core_sha256=CORE_SHA, hyperframes_project_host_sha256='b' * 64)


def environment(tmp_path, count=1):
    store = Store(tmp_path / 'production.db')
    joint = JointPlanStore(store)
    motion = MotionStore(store, skill_production=True,
                         runtime_capabilities=RUNTIME_CAPABILITIES)
    production = Production(store, motion_store=motion, motion_timing=MotionTiming(motion),
        motion_enabled=True, motion_renderer=object(), runtime_fingerprint=RUNTIME_V2)
    runs = ProductionRunStore(store, joint, motion, production)
    project = seed(store, joint, count=count)
    state = SimpleNamespace(store=store, joint=joint, motion=motion, production=production,
                            runs=runs, project=project)
    state.run = begin(state)
    complete_audio_phase(state)
    return state


def original_result(document, engine='html-svg'):
    value = deepcopy(film_scene(engine)['creative']['package']['result'])
    value['handoff'].update(film_sha256=document['film_context']['film_sha256'],
                            scene_id=document['scene']['scene_id'])
    return value


class ProductionPeer(Peer):
    def __init__(self, motion, operation_id):
        super().__init__()
        self.motion, self.operation_id = motion, operation_id

    def __call__(self, request):
        if request.url.path in {'/v1/capabilities', '/v1/jobs'}:
            op = self.motion.operation(self.operation_id)
            assert op['document']['schema'] == 'video.animation-production-input.v4'
            assert op['input_ref']['schema'] == UPLOAD_SCHEMA
            assert len(op['input_ref']['inputs']) == len(self.uploads)
            assert op['submit_attempted'] is (request.url.path == '/v1/jobs')
        return super().__call__(request)

    def complete(self, document, delivery, source_result, **kwargs):
        value = super().complete(document, delivery, source_result, **kwargs)
        self.job['business_ref'] = self.request['business_ref']
        return value


@pytest.mark.parametrize('method_count', [0, 5])
def test_formal_run_freezes_all_skills_and_recovers_same_job_into_full_snapshot(tmp_path, method_count):
    state = environment(tmp_path, count=2)
    published = [method(i) for i in range(method_count)]
    catalog_calls = []
    def catalog(owner):
        catalog_calls.append(owner)
        return deepcopy(published)
    ledger = tmp_path / 'skills.sqlite3'
    versions, sources = [], []
    for index in range(2):
        op = state.motion.active_operations()[0]
        assert op['document']['schema'] == SKILL_PENDING_SCHEMA
        adapter = SkillProductionAdapter(catalog, None, ledger)
        peer = ProductionPeer(state.motion, op['id'])
        with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
            gateway = SkillProductionAgentGateway(client, adapter)
            workflow = MotionWorkflow(state.motion, enabled=True, creative_enabled=True,
                                      skill_gateway=gateway, skill_adapter=adapter)
            if index == 0:
                peer.lose_response = True
            workflow.tick()
            prepared = state.motion.operation(op['id'])
            assert prepared['submit_attempted'] is True
            document, receipt = prepared['document'], prepared['input_ref']
            assert len(document['skill_catalog']['skills']) == method_count
            assert 'method_delivery' not in document and 'skill_delivery' not in document
            delivery = adapter.get_bundle(OWNER, document['skill_catalog']['consumer_id'],
                                          document['skill_catalog']['sha256'])
            assert len(delivery['packages']) == method_count
            uploaded = deepcopy(peer.uploads)
            # Publishing more methods must not reinterpret even an empty freeze.
            published.append(method(100 + index))
            restarted = MotionStore(state.store, skill_production=True,
                runtime_capabilities=RUNTIME_CAPABILITIES)
            restored_adapter = SkillProductionAdapter(catalog, None, ledger)
            restored = MotionWorkflow(restarted, enabled=True, creative_enabled=True,
                skill_gateway=SkillProductionAgentGateway(client, restored_adapter), skill_adapter=restored_adapter)
            peer.motion = restarted
            restored.tick()
            assert restarted.operation(op['id'])['input_ref'] == receipt
            assert peer.uploads == uploaded and peer.count('POST', '/v1/jobs') == 1
            assert len(catalog_calls) == index + 1
            peer.complete(document, delivery, original_result(document), read=bool(method_count))
            restored.tick()
            completed = restarted.operation(op['id'])
            assert completed['status'] == 'completed', completed
            version = restarted.candidate_version(OWNER, completed['version_id'], op['audio_version_id'])
            package = validate_scene_package(version['plan'])
            assert package['schema'] == 'video.creative-scene.v5'
            assert version['compiler_version'] == SKILL_COMPILER
            assert package['skill_catalog_sha256'] == document['skill_catalog']['sha256']
            assert package['skill_tool_evidence_sha256'] == package['skill_tool_evidence']['sha256']
            assert 'method_decision' not in package and 'capability_release_sha256' not in package
            assert not version['stale']
            versions.append(version)
            scene = state.project['scenes'][index]
            timing = state.production.motion_timing.get_candidate(OWNER, version['id'], op['audio_version_id'])
            sources.append(dict(id=scene['id'], title=scene['title'], narration=scene['narration'],
                visual_mode='creative', audio=state.store.audio_version(OWNER, op['audio_version_id']),
                leading_seconds=.25, trailing_seconds=.5,
                creative=dict(id=version['id'], package=package, beats=version['beats'],
                    alignment=timing['alignment'], compiler_version=version['compiler_version'])))
            # Next operation must receive this exact source and handoff.
            state.runs.tick()
            if index == 0:
                next_op = state.motion.active_operations()[0]
                previous = next_op['document']['core_task']['film_context']['predecessor']
                assert previous == restarted.continuity_predecessor(OWNER, version['id'], op['audio_version_id'])
                assert previous['source_sha256'] == digest(scene_source(package))
        published.pop()  # Keep the second scene's catalog fixture at the original size.
    full = freeze_snapshot_v4(state.project['id'], state.store.project(OWNER, state.project['id'])['revision'],
        state.project['title'], sources, runtime_fingerprint=RUNTIME_V2)
    assert verify_snapshot_v4(full) == full
    assert state.runs.get(OWNER, state.run['id'])['phase'] == 'draft'
    with state.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM motion_heads').fetchone()[0] == 0
    changed = deepcopy(sources)
    changed[0]['creative']['id'] = 'unrelated-candidate'
    with pytest.raises(ServiceError, match='creative_film_predecessor_mismatch'):
        freeze_snapshot_v4(state.project['id'], 1, state.project['title'], changed, runtime_fingerprint=RUNTIME_V2)


@pytest.mark.parametrize('failure', ['unready', 'bad-receipt'])
def test_skill_preflight_and_full_receipt_fail_before_claim_or_submission(tmp_path, failure):
    state = environment(tmp_path)
    op = state.motion.active_operations()[0]
    adapter = SkillProductionAdapter(lambda _: [], None, tmp_path / 'skills.sqlite3')
    peer = ProductionPeer(state.motion, op['id'])
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        gateway = SkillProductionAgentGateway(client, adapter)
        if failure == 'unready':
            peer.profile['ready'] = False
        else:
            state.motion.prepare_creative(op['id'], adapter)
            document = state.motion.operation(op['id'])['document']
            receipt = gateway.upload(document)
            receipt['inputs'] = []
            state.motion.update_operation(op['id'], input_ref=receipt)
        MotionWorkflow(state.motion, enabled=True, creative_enabled=True,
                       skill_gateway=gateway, skill_adapter=adapter).tick()
        saved = state.motion.operation(op['id'])
        assert saved['status'] == 'failed' and saved['submit_attempted'] is False
        assert peer.count('POST', '/v1/jobs') == 0
        assert saved['error'] == ('skill_production_profile_unavailable' if failure == 'unready'
                                  else 'invalid_skill_production_input_refs')


@pytest.mark.parametrize('engine', ['html-svg', 'canvas', 'three'])
@pytest.mark.parametrize('runtime', [RUNTIME_V2, RUNTIME_V3])
def test_v5_source_retains_clock_and_passes_native_python_and_node_boundaries(tmp_path, engine, runtime):
    from agent_service.profiles.video_animation_skill_production_contract import CORE_FIELDS
    from agent_service.skill_tool_evidence import SkillToolObserver
    from video_workbench.creative_skill_contract import build_scene_input, collect_scene_result
    from test_skill_production_gateway import ATTEMPT
    scene = film_scene(engine, leading=.25, trailing=.02)
    old = scene['creative']['package']
    core = {key: old['input_document'][key] for key in CORE_FIELDS}
    adapter = SkillProductionAdapter(lambda _: [], None, tmp_path / 'skills.sqlite3')
    delivery = adapter.freeze(OWNER, 'native-boundary', core, [engine])
    document = build_scene_input(core, delivery)
    observer = SkillToolObserver(delivery['catalog'], delivery['packages'], input_sha256=digest(document))
    evidence = observer.finish(ATTEMPT)
    result = dict(schema='video.animation-production-result.v4', input_sha256=digest(document),
        source=old['result']['source'], handoff=old['result']['handoff'], skill_decisions=[],
        skill_tool_evidence_sha256=evidence['sha256'])
    package = collect_scene_result(document, result, evidence, skill_delivery=delivery)
    files = compile_creative_project(package, scene['creative']['beats'], scene['creative']['alignment'],
        runtime_fingerprint=runtime, leading_seconds=.25, trailing_seconds=.02)
    timeline, manifest = (json.loads(files[name]) for name in ('timeline.json', 'manifest.json'))
    assert timeline['audio_seconds'] == scene['audio']['duration_seconds']
    assert timeline['creative']['seek_protocol'] == 'window.__animationExperiment.seek(seconds)'
    assert manifest['runtime_fingerprint'] == runtime
    assert files['index.html'] == old['result']['source']['files']['scene.html'].encode()
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(scene['audio']['data'])
    assert freeze_input(tmp_path, image_digest=runtime['image_digest'])['unbound_experiment'] is False
    checked = node_verify(tmp_path, hashlib.sha256(files['manifest.json']).hexdigest())
    assert checked.returncode == 0, checked.stderr


def test_v3_native_fingerprint_is_complete_and_opted_in_only_for_new_skill_production(tmp_path):
    path = tmp_path / 'capabilities.json'
    path.write_text(json.dumps(RUNTIME_CAPABILITIES), encoding='utf-8')
    env = {'VIDEO_CREATIVE_RUNTIME_CAPABILITIES_FILE': str(path)}
    assert creative_runtime_capabilities(RUNTIME_V3, env, skill_production=True) == RUNTIME_CAPABILITIES
    with pytest.raises(ValueError):
        creative_runtime_capabilities(RUNTIME_V3, env)
    for field in ('hyperframes_core_sha256', 'hyperframes_project_host_sha256', 'creative_host_sha256'):
        changed = deepcopy(RUNTIME_V3)
        changed.pop(field)
        with pytest.raises(ValueError):
            creative_runtime_capabilities(changed, env, skill_production=True)
    old = film_scene('canvas')
    with pytest.raises(ServiceError, match='creative_engine_runtime_required'):
        compile_creative_project(old['creative']['package'], old['creative']['beats'], old['creative']['alignment'],
                                 runtime_fingerprint=RUNTIME_V3)


def test_app_does_not_fall_back_to_old_method_author_without_skill_configuration(tmp_path):
    app = create_app(db_path=tmp_path / 'app.db', background=False, local_mode=True,
        trusted_hosts={'testserver'}, motion_enabled=True, creative_enabled=True,
        creative_gateway=object(), method_adapter=object())
    with TestClient(app) as client:
        assert app.state.motion_store.skill_production is True
        assert client.get('/api/session').json()['creative_configured'] is False
        assert app.state.production_run_workflow.enabled is False
        with pytest.raises(ServiceError, match='creative_skills_unavailable'):
            app.state.motion_workflow.require_gateway('creative')
    assert SkillProductionAgentSettings.from_env({}) is None
    assert SkillProductionAgentSettings.from_env(dict(VIDEO_PRODUCTION_AGENT_URL='http://agent.invalid',
        VIDEO_PRODUCTION_AGENT_TOKEN=TOKEN)) is None
    settings = SkillProductionAgentSettings.from_env(dict(VIDEO_SKILL_PRODUCTION_AGENT_URL='http://agent.invalid',
        VIDEO_SKILL_PRODUCTION_AGENT_TOKEN=TOKEN, VIDEO_SKILL_PRODUCTION_PROFILE_REVISION='review-2'))
    assert settings.base_url == 'http://agent.invalid' and settings.revision == 'review-2'
    assert settings.token == TOKEN and TOKEN not in repr(settings)


def test_app_real_workflow_submits_new_role_without_calling_historical_gateway(tmp_path):
    state = environment(tmp_path)
    op = state.motion.active_operations()[0]
    adapter = SkillProductionAdapter(lambda _: [], None, tmp_path / 'skills.sqlite3')
    peer = ProductionPeer(state.motion, op['id'])
    class HistoricalGateway:
        def __getattr__(self, name):
            raise AssertionError('New production must not call the old method author: ' + name)
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        gateway = SkillProductionAgentGateway(client, adapter)
        app = create_app(db_path=state.store.path, background=False, local_mode=True, trusted_hosts={'testserver'},
            motion_enabled=True, creative_enabled=True, creative_gateway=HistoricalGateway(), method_adapter=object(),
            skill_gateway=gateway, skill_adapter=adapter, creative_capabilities=RUNTIME_CAPABILITIES,
            motion_runtime=RUNTIME_V2)
        with TestClient(app) as browser:
            assert browser.get('/api/session').json()['creative_configured'] is True
            assert app.state.motion_workflow.require_gateway('creative') is gateway
            peer.motion = app.state.motion_store
            app.state.motion_workflow.tick()
            prepared = app.state.motion_store.operation(op['id'])
            assert peer.request['profile'] == 'video.animation-production.v3'
            assert prepared['document']['schema'] == 'video.animation-production-input.v4'
            delivery = adapter.get_bundle(OWNER, prepared['document']['skill_catalog']['consumer_id'],
                prepared['document']['skill_catalog']['sha256'])
            peer.complete(prepared['document'], delivery, original_result(prepared['document']), read=False)
            app.state.motion_workflow.tick()
            completed = app.state.motion_store.operation(op['id'])
            assert completed['status'] == 'completed'
            assert app.state.motion_store.candidate_version(OWNER, completed['version_id'],
                op['audio_version_id'])['plan']['schema'] == 'video.creative-scene.v5'
