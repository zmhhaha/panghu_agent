"""Formal generation freezes published knowledge without sharing admission identities."""
from copy import deepcopy
import sqlite3
import hashlib

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, digest, canonical_bytes
from video_workbench.animation_method_production import MethodProductionAdapter
from video_workbench.animation_method_store import AnimationMethodStore
from video_workbench.app import create_app
from video_workbench.config import ProductionAgentSettings, ProductionMethodSettings
from video_workbench.motion_gateway import ProductionCreativeAgentGateway
from video_workbench.motion_store import MotionStore, CREATIVE_PENDING_SCHEMA
from video_workbench.store import Store
from test_animation_method_production import published
from test_creative_method_contract import production_result, production_input
from test_creative_production import state, adopted_audio, OWNER
from test_motion_gateway import Peer, INPUT, JOB, ATTEMPT, ARTIFACT, TOKEN


def start(state, key='methods-aware-formal'):
    adopted_audio(state)
    store, motion, _, project_id, scene_id = state
    return motion.start(OWNER, scene_id, dict(expected_revision=store.project(OWNER, project_id)['revision'],
                                             diagram_kind='creative'), key)


def adapter_for(state, tmp_path, records, calls):
    def unlocked():
        # A different connection must be able to acquire the write lock during
        # either external callback; the business transaction is already closed.
        with sqlite3.connect(state[0].path, timeout=.1) as db:
            db.execute('BEGIN IMMEDIATE')
            db.rollback()

    def catalogue(owner):
        unlocked()
        calls.append(('catalogue', owner))
        return records.list_methods(owner, published_only=True)

    def read(owner, body):
        unlocked()
        calls.append(('read', owner, deepcopy(body)))
        return records.read_published(owner, body['method_version_ids'], body['consumer_id'])

    return MethodProductionAdapter(catalogue, read, ledger_path=tmp_path / 'production-freezes.sqlite3')


def test_formal_input_carries_exact_complete_published_knowledge_and_no_http_in_business_transaction(state, tmp_path):
    started = start(state)
    _, motion, _, _, _ = state
    pending = motion.operation(started['id'])
    assert pending['document']['schema'] == CREATIVE_PENDING_SCHEMA
    records = AnimationMethodStore(tmp_path / 'methods.sqlite3')
    method = published(records, title='请求处理的可见因果', problem='请求排队后身份不清楚',
                       applicability='请求、队列、执行节点', guidance='保留同一请求，先解释占用再解释等待。' * 200)
    calls = []
    adapter = adapter_for(state, tmp_path, records, calls)
    frozen = motion.prepare_creative(started['id'], adapter)
    delivery = frozen['document']['method_delivery']
    assert frozen['document']['schema'] == 'video.animation-production-input.v1'
    assert delivery['consumer_id'] == 'video-production:' + started['id']
    assert delivery['task_sha256'] == digest(pending['document']['core_task'])
    assert delivery['methods'] == [method]
    assert calls[-1][2]['consumer_id'] == delivery['consumer_id']
    assert frozen['input_sha256'] == digest(frozen['document'])
    assert motion.prepare_creative(started['id'], adapter) == frozen
    assert len(calls) == 2


def test_crash_after_delivery_recovers_original_snapshot_without_rereading_new_library(state, tmp_path, monkeypatch):
    started = start(state)
    store, motion, _, _, _ = state
    records = AnimationMethodStore(tmp_path / 'methods.sqlite3')
    method = published(records, title='请求等待', problem='请求身份与等待过程', applicability='请求、队列、执行')
    calls = []
    adapter = adapter_for(state, tmp_path, records, calls)
    original_write = motion._write_op

    def crash(*args):
        raise RuntimeError('fixture crash before business persistence')

    monkeypatch.setattr(motion, '_write_op', crash)
    with pytest.raises(RuntimeError, match='fixture crash'):
        motion.prepare_creative(started['id'], adapter)
    assert motion.operation(started['id'])['document']['schema'] == CREATIVE_PENDING_SCHEMA
    published(records, parent=method, guidance='Later publication cannot change the in-flight task.')

    def no_network(*args):
        raise AssertionError('Recovery must use the original durable delivery')

    recovered = MethodProductionAdapter(no_network, no_network, ledger_path=tmp_path / 'production-freezes.sqlite3')
    restarted = MotionStore(Store(store.path))
    frozen = restarted.prepare_creative(started['id'], recovered)
    assert frozen['document']['method_delivery']['methods'] == [method]
    assert len(calls) == 2
    monkeypatch.setattr(motion, '_write_op', original_write)


def test_declarations_and_exact_delivery_survive_candidate_and_explicit_adoption(state, tmp_path):
    started = start(state)
    _, motion, _, project_id, scene_id = state
    records = AnimationMethodStore(tmp_path / 'methods.sqlite3')
    method = published(records, title='请求身份', problem='请求在队列中失去身份', applicability='请求与执行节点')
    frozen = motion.prepare_creative(started['id'], adapter_for(state, tmp_path, records, []))
    output = production_result(frozen['document'])
    completed = motion.complete(started['id'], output)
    version = motion.version(OWNER, completed['version_id'])
    package = version['plan']
    assert package['schema'] == 'video.creative-scene.v2'
    assert package['method_delivery_sha256'] == frozen['document']['method_delivery']['sha256']
    assert package['method_decision'] == output['method_decision']
    assert package['method_decision']['decisions'][0]['version_id'] == method['id']
    assert package['evidence_scope'] == 'agent_self_report_source_anchors_not_observed_application_or_quality'
    project = motion.project(OWNER, project_id)
    assert project['scenes'][0]['motion']['head_version_id'] is None
    adopted = motion.adopt(OWNER, scene_id, version['id'], None, project['revision'])
    assert adopted['scenes'][0]['motion']['head_version_id'] == version['id']
    assert motion.version(OWNER, version['id'])['plan'] == package


def test_admitted_engine_snapshot_survives_restart_and_builds_engine_package(state, tmp_path):
    """Frozen engine admission, not restart configuration, controls the result."""
    from test_animation_method_service import runtime_capabilities
    from video_workbench.creative_renderer import ENGINE_METHOD_COMPILER

    store, _, timing, project_id, scene_id = state
    capabilities = runtime_capabilities(['html-svg', 'canvas'])
    motion = MotionStore(store, runtime_capabilities=capabilities)
    configured = (store, motion, timing, project_id, scene_id)
    started = start(configured)
    core = motion.operation(started['id'])['document']['core_task']
    assert core['constraints']['allowed_engines'] == ['html-svg', 'canvas']
    assert core['constraints']['runtime_identity'] == capabilities['runtime_identity']
    assert core['assets'] == capabilities['assets']

    # Restart without the new admission. The already frozen request must retain
    # its exact admitted subset and identity instead of falling back to v1.
    restarted = MotionStore(Store(store.path))
    records = AnimationMethodStore(tmp_path / 'methods.sqlite3')
    frozen = restarted.prepare_creative(started['id'], adapter_for(configured, tmp_path, records, []))
    assert frozen['document']['schema'] == 'video.animation-production-input.v2'
    assert frozen['document']['constraints'] == core['constraints']
    assert frozen['document']['assets'] == core['assets']
    assert frozen['document']['method_delivery']['allowed_engines'] == ['html-svg', 'canvas']
    output = production_result(frozen['document'])
    output['source']['engine'] = 'canvas'
    output['source']['files']['scene.html'] = (
        '<!doctype html><canvas id="scene"></canvas><script>'
        'const ctx=document.querySelector("canvas").getContext("2d");'
        'window.__animationExperiment={seek(t){ctx.clearRect(0,0,1280,720);'
        'ctx.fillRect(t*20,100,20,20)}};</script>')
    output['source']['files_sha256']['scene.html'] = hashlib.sha256(
        output['source']['files']['scene.html'].encode()).hexdigest()
    completed = restarted.complete(started['id'], output)
    version = restarted.version(OWNER, completed['version_id'])
    assert version['plan']['schema'] == 'video.creative-scene.v3'
    assert version['compiler_version'] == ENGINE_METHOD_COMPILER
    assert version['plan']['engine'] == 'canvas'
    project = restarted.project(OWNER, project_id)
    adopted = restarted.adopt(OWNER, scene_id, version['id'], None, project['revision'])
    assert adopted['scenes'][0]['motion']['head_version_id'] == version['id']


def test_retrieval_uses_scene_context_and_ignores_old_source_and_assets(state, tmp_path):
    started = start(state)
    _, motion, _, _, _ = state
    records = AnimationMethodStore(tmp_path / 'methods.sqlite3')
    method = published(records, title='请求身份', problem='请求与执行节点', applicability='请求进入队列')
    unrelated = published(records, title='宇宙天文星系', problem='天文轨道星系', applicability='行星天文')
    pending = motion.operation(started['id'])['document']['core_task']
    pending['base_source'] = {'engine': 'html-svg', 'files': {'scene.html': '宇宙天文星系'},
                              'strategy': {'description': '宇宙天文星系', 'tradeoffs': ''}}
    snapshot = adapter_for(state, tmp_path, records, []).freeze(OWNER, 'retrieval-test', pending, ['html-svg'])
    assert [row['id'] for row in snapshot['methods']] == [method['id']]
    assert unrelated['id'] not in [row['id'] for row in snapshot['methods']]


def test_missing_method_service_fails_before_upload_and_does_not_turn_into_zero_match(state):
    from video_workbench.motion_workflow import MotionWorkflow
    started = start(state)
    _, motion, _, _, _ = state

    class NoCalls:
        def upload(self, *args):
            raise AssertionError('No upload is permitted without frozen methods availability')

    workflow = MotionWorkflow(motion, enabled=True, creative_enabled=True, creative_gateway=NoCalls())
    with pytest.raises(ServiceError, match='creative_methods_unavailable'):
        workflow.require_gateway('creative')
    workflow.tick()
    failed = motion.operation(started['id'])
    assert failed['status'] == 'failed' and failed['error'] == 'creative_methods_unavailable'
    assert failed['input_ref'] is None and failed['submit_attempted'] is False


def test_old_caller_configuration_does_not_configure_the_new_production_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDEO_AGENT_URL', 'http://old-agent:8000')
    monkeypatch.setenv('VIDEO_AGENT_TOKEN', 'o' * 32)
    for key in ('VIDEO_PRODUCTION_AGENT_URL', 'VIDEO_PRODUCTION_AGENT_TOKEN', 'VIDEO_PRODUCTION_AGENT_TOKEN_FILE',
                'VIDEO_METHOD_LAB_URL', 'VIDEO_METHOD_LAB_TOKEN', 'VIDEO_METHOD_LAB_TOKEN_FILE'):
        monkeypatch.delenv(key, raising=False)
    assert ProductionAgentSettings.from_env() is None
    assert ProductionMethodSettings.from_env() is None
    app = create_app(db_path=tmp_path / 'state.sqlite3', background=False, trusted_hosts={'testserver'},
                     motion_enabled=True, creative_enabled=True)
    assert app.state.workflow.gateway is not None
    assert app.state.motion_workflow.creative_gateway is None
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = OWNER
        assert client.get('/api/session').json()['creative_configured'] is False


def test_explicit_production_identity_uses_its_own_client_and_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDEO_AGENT_URL', 'http://old-agent:8000')
    monkeypatch.setenv('VIDEO_AGENT_TOKEN', 'o' * 32)
    monkeypatch.setenv('VIDEO_PRODUCTION_AGENT_URL', 'http://production-agent:8000')
    monkeypatch.setenv('VIDEO_PRODUCTION_AGENT_TOKEN', 'p' * 32)
    monkeypatch.setenv('VIDEO_PRODUCTION_AGENT_PROFILE_REVISION', 'production-2')
    app = create_app(db_path=tmp_path / 'state.sqlite3', background=False, trusted_hosts={'testserver'})
    formal = app.state.motion_workflow.creative_gateway
    assert isinstance(formal, ProductionCreativeAgentGateway)
    assert formal.profile == 'video.animation-production.v1'
    assert formal.profile_revision == 'production-2'
    assert formal.client is not app.state.workflow.gateway.client
    with TestClient(app):
        pass


@pytest.mark.parametrize('prefix', ['VIDEO_PRODUCTION_AGENT', 'VIDEO_METHOD_LAB'])
def test_partial_separate_credentials_fail_explicitly_without_echoing_token(prefix):
    settings = ProductionAgentSettings if prefix == 'VIDEO_PRODUCTION_AGENT' else ProductionMethodSettings
    secret = 's' * 32
    with pytest.raises(ValueError) as caught:
        settings.from_env({prefix + '_TOKEN': secret})
    assert secret not in str(caught.value)


@pytest.mark.parametrize('old_profile_only', [False, True])
@pytest.mark.parametrize('film', [False, True])
def test_production_gateway_uses_exact_new_profile_input_namespace_and_stopped_artifact(monkeypatch, old_profile_only, film):
    monkeypatch.setattr('video_workbench.motion_gateway.time.time', lambda: 1000.0)

    class ProductionPeer(Peer):
        def __call__(self, request):
            if request.method == 'POST' and request.url.path == '/v1/inputs':
                self.calls.append(request)
                assert request.url.params['name'] == 'animation-production-input.json'
                return httpx.Response(201, json=dict(input_id=INPUT, name='animation-production-input.json',
                    size=len(request.content), sha256=hashlib.sha256(request.content).hexdigest()))
            return super().__call__(request)

    peer = ProductionPeer()
    profile = 'video.animation-production.v2' if film else 'video.animation-production.v1'
    peer.profiles[0]['profile'] = 'video.animation-experiment.v1' if old_profile_only else profile
    if film:
        from test_creative_film_renderer import film_scene
        package = film_scene('html-svg')['creative']['package']
        doc, expected = package['input_document'], package['result']
    else:
        doc = production_input()
        expected = production_result(doc)
    with AgentClient('http://production-agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        base = ProductionCreativeAgentGateway(client)
        gateway = base.for_document(doc)
        assert base.profile == 'video.animation-production.v1'
        ref = gateway.upload(doc)
        assert ref['role'] == 'animation_production_input'
        if old_profile_only:
            with pytest.raises(ServiceError, match='motion_profile_unavailable'):
                gateway.submit('formal-operation', doc, 1200, ref)
            assert peer.count('POST', '/v1/jobs') == 0
            return
        gateway.submit('formal-operation', doc, 1200, ref)
        assert peer.request['profile'] == profile
        assert peer.request['business_ref'] == 'video-production:formal-operation'
        post = next(row for row in peer.calls if row.method == 'POST' and row.url.path == '/v1/jobs')
        assert post.headers['idempotency-key'] == 'production-formal-operation'
        peer.complete('formal-operation')
        peer.content = canonical_bytes(expected)
        peer.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT,
            name='animation-production-result.json', size=len(peer.content), sha256=digest(expected))])
        peer.job.update(business_ref='video-production:formal-operation',
                        pins=dict(profile=profile, profile_revision='1'),
                        artifact_manifest_sha256=peer.manifest['sha256'])
        assert gateway.collect(JOB, digest(doc), doc) == expected
        assert peer.count('POST', '/v1/jobs') == 1
