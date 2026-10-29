"""Deployment configuration and local probes, without contacting an Agent."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from video_workbench.app import create_app
from video_workbench.config import AgentSettings


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
