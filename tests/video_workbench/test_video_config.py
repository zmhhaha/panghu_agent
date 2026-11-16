"""Deployment configuration and local probes, without contacting an Agent."""
import sqlite3
import json

import pytest
from fastapi.testclient import TestClient

from video_workbench.app import create_app
from video_workbench.config import AgentSettings, ExplanationSettings, creative_runtime_capabilities
from video_workbench.config import ResourcePlanningAgentSettings
from video_workbench.resource_producer_catalog import admitted_resource_producers
from agent_service.contracts import ServiceError


def test_explanation_is_opt_in_with_an_independent_contract_revision():
    assert ExplanationSettings.from_env({}) == ExplanationSettings(False, '1')
    assert ExplanationSettings.from_env({'VIDEO_EXPLANATION_ENABLED': '1',
                                        'VIDEO_EXPLANATION_PROFILE_REVISION': 'review-2'}) == ExplanationSettings(True, 'review-2')


def test_resource_planner_uses_a_separate_explicit_credential_and_hides_secrets():
    assert ResourcePlanningAgentSettings.from_env({'VIDEO_JOINT_PLAN_AGENT_URL': 'http://agent',
        'VIDEO_JOINT_PLAN_AGENT_TOKEN': 's' * 40}) is None
    configured = ResourcePlanningAgentSettings.from_env({'VIDEO_RESOURCE_PLAN_AGENT_URL': 'http://agent',
        'VIDEO_RESOURCE_PLAN_AGENT_TOKEN': 'r' * 40})
    assert configured.token == 'r' * 40 and configured.token not in repr(configured)
    with pytest.raises(ValueError, match='dedicated VIDEO_RESOURCE_PLAN_AGENT'):
        ResourcePlanningAgentSettings.from_env({'VIDEO_RESOURCE_PLAN_AGENT_URL': 'http://agent'})
    with pytest.raises(ValueError, match='Unsupported VIDEO_RESOURCE_PLAN'):
        ResourcePlanningAgentSettings.from_env({'VIDEO_RESOURCE_PLAN_AGENT_URL': 'http://agent',
            'VIDEO_RESOURCE_PLAN_AGENT_TOKEN': 'r' * 40, 'VIDEO_RESOURCE_PLAN_PROFILE_REVISION': '2'})


def test_planner_catalog_requires_both_whole_film_and_resource_admission():
    class Film:
        enabled = True
        consumer = True
        producer = True
        def readiness(self):
            if not self.consumer:
                raise ServiceError('hyperframes_film_not_admitted', 503)
            return True
        def preflight_resources(self, owner):
            assert owner == 'owner'
            if not self.producer:
                raise ServiceError('film_resource_not_admitted', 503)
            return True
    film = Film()
    assert admitted_resource_producers(None, 'owner') == []
    assert admitted_resource_producers(film, 'owner')[0]['id'] == 'manim'
    film.consumer = False
    assert admitted_resource_producers(film, 'owner') == []
    film.consumer, film.producer = True, False
    assert admitted_resource_producers(film, 'owner') == []


def test_creative_engine_configuration_binds_formal_host_and_exact_runtime(tmp_path):
    runtime = dict(schema='video.motion-runtime.v2', image_digest='sha256:' + 'a' * 64,
        creative_host_sha256='b' * 64, engine_runtime_sha256='c' * 64,
        gsap_sha256='d' * 64, font_sha256='e' * 64)
    capabilities = dict(schema='video.animation-runtime-capabilities.v1',
        allowed_engines=['html-svg', 'canvas'], assets={'gsap.min.js': 'd' * 64,
        'NotoSansCJK-Regular.ttc': 'e' * 64}, runtime_identity=dict(image_digest=runtime['image_digest'],
            host_sha256=runtime['creative_host_sha256'], engine_runtime_sha256=runtime['engine_runtime_sha256'],
            admission_id='formal-native-acceptance'))
    path = tmp_path / 'capabilities.json'
    path.write_text(json.dumps(capabilities), encoding='utf-8')
    env = {'VIDEO_CREATIVE_RUNTIME_CAPABILITIES_FILE': str(path)}
    assert creative_runtime_capabilities(runtime, {}) is None
    assert creative_runtime_capabilities(runtime, env) == capabilities
    for key in ('image_digest', 'creative_host_sha256', 'engine_runtime_sha256', 'gsap_sha256', 'font_sha256'):
        changed = dict(runtime, **{key: 'f' * 64})
        with pytest.raises(ValueError, match='do not match'):
            creative_runtime_capabilities(changed, env)
    # A v1 runtime or a missing manifest cannot silently broaden production.
    with pytest.raises(ValueError):
        creative_runtime_capabilities(dict(runtime, schema='video.motion-runtime.v1'), env)
    with pytest.raises(ValueError):
        creative_runtime_capabilities(runtime, {'VIDEO_CREATIVE_RUNTIME_CAPABILITIES_FILE': str(tmp_path / 'missing')})


@pytest.mark.parametrize('env', [
    {'VIDEO_EXPLANATION_ENABLED': 'true'},
    {'VIDEO_EXPLANATION_ENABLED': ''},
    {'VIDEO_EXPLANATION_PROFILE_REVISION': 'bad revision'},
    {'VIDEO_EXPLANATION_PROFILE_REVISION': ''},
])
def test_invalid_explanation_configuration_is_rejected(env):
    with pytest.raises(ValueError):
        ExplanationSettings.from_env(env)


def test_unconfigured_is_editable_but_not_ready(tmp_path, monkeypatch):
    for name in ('VIDEO_AGENT_URL', 'VIDEO_AGENT_TOKEN', 'VIDEO_AGENT_TOKEN_FILE'):
        monkeypatch.delenv(name, raising=False)
    assert AgentSettings.from_env({}) is None
    app = create_app(db_path=tmp_path / 'db', background=False, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        assert client.get('/healthz').json() == {'status': 'ok'}
        assert client.get('/readyz').status_code == 503
        assert client.get('/api/session').status_code == 401
        assert client.get('/healthz', headers={'Host': 'other.example'}).status_code == 400


@pytest.mark.parametrize('values', [
    {'VIDEO_AGENT_URL': 'http://agent'},
    {'VIDEO_AGENT_TOKEN': 's' * 40},
    {'VIDEO_AGENT_TOKEN_FILE': '/missing'},
    {'VIDEO_AGENT_URL': 'http://agent', 'VIDEO_AGENT_TOKEN': 's' * 40,
     'VIDEO_AGENT_TOKEN_FILE': '/missing'},
])
def test_partial_or_ambiguous_settings_rejected(values):
    with pytest.raises(ValueError, match='exactly one'):
        AgentSettings.from_env(values)


def test_projected_credential_file(tmp_path):
    token = tmp_path / 'token'
    token.write_text('s' * 40 + '\n', encoding='utf-8')
    settings = AgentSettings.from_env({'VIDEO_AGENT_URL': 'http://agent:8080/',
                                      'VIDEO_AGENT_TOKEN_FILE': str(token)})
    assert settings.base_url == 'http://agent:8080/'
    assert settings.token == 's' * 40
    assert settings.revision == '1'
    assert settings.token not in repr(settings)


@pytest.mark.parametrize('content', [b's' * 4097, b'\xff' * 40, b'short', b's' * 32 + b'\x00'])
def test_bad_credential_files_do_not_leak_content(tmp_path, content):
    token = tmp_path / 'token'
    token.write_bytes(content)
    with pytest.raises(ValueError) as error:
        AgentSettings.from_env({'VIDEO_AGENT_URL': 'https://agent', 'VIDEO_AGENT_TOKEN_FILE': str(token)})
    assert str(token) not in str(error.value)
    assert 'short' not in str(error.value)


@pytest.mark.parametrize('url', ['https://u:p@agent', 'https://agent/path', 'https://agent?key=abc',
                                'https://@agent',
                                'https://agent/#fragment', 'https://agent:wrong', 'https://agent:0',
                                'file:///agent', 'http://agent\n'])
def test_invalid_origin_rejected(url):
    with pytest.raises(ValueError, match='origin'):
        AgentSettings.from_env({'VIDEO_AGENT_URL': url, 'VIDEO_AGENT_TOKEN': 's' * 40})


def test_configured_probes_use_no_remote_calls_and_detect_db_failure(tmp_path, monkeypatch):
    token = tmp_path / 'token'
    token.write_text('s' * 40, encoding='utf-8')
    monkeypatch.setenv('VIDEO_AGENT_URL', 'http://agent.invalid')
    monkeypatch.setenv('VIDEO_AGENT_TOKEN_FILE', str(token))
    monkeypatch.delenv('VIDEO_AGENT_TOKEN', raising=False)
    app = create_app(db_path=tmp_path / 'db', background=False, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/readyz').json() == {'status': 'ready'}
        with sqlite3.connect(app.state.store.path) as db:
            db.execute('DROP TABLE metadata')
        assert client.get('/readyz').json() == {'status': 'not_ready'}
        assert client.get('/readyz').status_code == 503
        assert client.get('/healthz').status_code == 200


def test_partial_settings_fail_before_database_creation(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDEO_AGENT_URL', 'http://agent')
    monkeypatch.delenv('VIDEO_AGENT_TOKEN', raising=False)
    monkeypatch.delenv('VIDEO_AGENT_TOKEN_FILE', raising=False)
    with pytest.raises(ValueError):
        create_app(db_path=tmp_path / 'db', background=False)
    assert not (tmp_path / 'db').exists()
