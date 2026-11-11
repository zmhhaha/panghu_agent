"""Private executor startup with real storage/HTTP, never Agent or renderer I/O."""
from contextlib import contextmanager
import http.client
import json
import socket
from threading import Thread

import pytest

from agent_service.contracts import ServiceError
from video_workbench import animation_creation_executor as executor
from video_workbench.animation_creation_renderer import DurableAnimationRenderer
from video_workbench.animation_creation_service import AnimationCreationService
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.animation_method_renderer import MethodAnimationRenderer


AGENT_TOKEN = 'unit-agent-credential-' + 'a' * 40
CALLER_TOKEN = 'unit-lab-caller-credential-' + 'b' * 40
METHOD_TOKEN = 'unit-method-agent-credential-' + 'd' * 40
IMAGE = 'registry.example/renderer:latest@sha256:' + 'c' * 64


class NoNetworkAgentClient:
    instances = []

    def __init__(self, base_url, token, *, timeout):
        self.base_url, self.token, self.timeout = base_url, token, timeout
        self.calls, self.close_count = [], 0
        self.instances.append(self)

    def close(self):
        self.close_count += 1

    def __getattr__(self, name):
        def forbidden(*_args, **_kwargs):
            self.calls.append(name)
            raise AssertionError('Startup/read-only runtime tests must never use an Agent API')
        return forbidden


@pytest.fixture
def config(tmp_path, monkeypatch):
    NoNetworkAgentClient.instances = []
    monkeypatch.setattr(executor, 'AgentClient', NoNetworkAgentClient)
    agent, caller, method = tmp_path / 'agent-token', tmp_path / 'caller-token', tmp_path / 'method-token'
    agent.write_text(AGENT_TOKEN + '\n', encoding='ascii')
    caller.write_text(CALLER_TOKEN + '\n', encoding='ascii')
    method.write_text(METHOD_TOKEN + '\n', encoding='ascii')
    return dict(root=tmp_path / 'executor', agent_url='http://127.0.0.1:1',
        agent_token_file=agent, token_file=caller, renderer_image=IMAGE,
        enable_isolated_rendering=True, listen='127.0.0.1', port=0, method_agent_token_file=method)


@contextmanager
def serving(server):
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def request(server, path, *, token=None, owner=None):
    headers = {}
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    if owner is not None:
        headers['X-Animation-Creation-Owner'] = owner
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        connection.close()


def test_real_executor_wires_separate_credentials_storage_profiles_and_default_pump_without_jobs(config):
    server, client = executor.create_server(**config)
    try:
        assert isinstance(server.service, AnimationCreationService)
        assert isinstance(server.service.renderer, DurableAnimationRenderer)
        assert server._token == CALLER_TOKEN and client.token == AGENT_TOKEN
        assert server.service.creation_gateway.client is client
        assert server.service.review_gateway.client is client
        assert server.service.creation_gateway.profile == 'video.animation-create.v2'
        assert server.service.review_gateway.profile == 'video.animation-review.v4'
        assert server.service.renderer.enabled is True and server.service.renderer.image == IMAGE
        assert server.service.path == config['root'] / 'creation-service.sqlite3'
        assert server.service.revisions.path == str((config['root'] / 'creation-polish.sqlite3').resolve())
        assert server.service.renderer.root == config['root'] / 'renders'
        assert server.pump is not None and server.pump.thread.is_alive()
        assert isinstance(server.method_service, AnimationMethodService)
        assert isinstance(server.method_service.renderer, MethodAnimationRenderer)
        method_client = server.method_service.author.client
        assert method_client is not client and method_client.token == METHOD_TOKEN
        assert server.method_service.reviewer.client is method_client
        assert server.method_service.author.profile == 'video.animation-method-author.v1'
        assert server.method_service.reviewer.profile == 'video.animation-method-review.v1'
        assert server.method_service.root == config['root'] / 'method-lab'
        assert server.method_service.renderer.root == config['root'] / 'method-renders'
        assert server.method_service.renderer.enabled is True
        assert server.method_service.renderer.image == IMAGE
        assert server.method_service.pending() == []
        assert server.method_service.catalogue('developer-1')['commissions'] == []
        assert server.method_pump is not None and server.method_pump.thread.is_alive()
        assert server.service.pending() == [] and server.service.list('developer-1') == []
        assert not (config['root'] / 'renders').exists()
        assert not (config['root'] / 'method-renders').exists()
        assert client.timeout == 30 and client.calls == []
    finally:
        server.server_close()
        client.close()
    assert not server.pump.thread.is_alive() and client.close_count == 1
    assert method_client.close_count == 1 and method_client.calls == []
    assert not server.method_pump.thread.is_alive()


@pytest.mark.parametrize('enabled', [None, False, 1, 'true'])
def test_isolated_execution_requires_explicit_true_before_reading_credentials_or_starting_client(config, enabled):
    config['enable_isolated_rendering'] = enabled
    config['agent_token_file'] = config['root'] / 'does-not-exist'
    with pytest.raises(ValueError, match='animation_creation_isolated_rendering_opt_in_required'):
        executor.create_server(**config)
    assert NoNetworkAgentClient.instances == [] and not config['root'].exists()


def test_omitting_enable_flag_fails_closed(config):
    config.pop('enable_isolated_rendering')
    with pytest.raises(ValueError, match='animation_creation_isolated_rendering_opt_in_required'):
        executor.create_server(**config)
    assert NoNetworkAgentClient.instances == []


def test_agent_credential_cannot_be_reused_as_lab_caller_credential(config):
    config['token_file'].write_text(AGENT_TOKEN, encoding='ascii')
    with pytest.raises(ValueError, match='animation_creation_distinct_credentials_required'):
        executor.create_server(**config)
    assert NoNetworkAgentClient.instances == [] and not config['root'].exists()


def test_service_initialization_failure_closes_the_constructed_agent_client(config):
    config['root'].write_bytes(b'An ordinary file cannot be an executor data directory.')
    with pytest.raises(OSError):
        executor.create_server(**config)
    assert len(NoNetworkAgentClient.instances) == 1
    client = NoNetworkAgentClient.instances[0]
    assert client.close_count == 1 and client.calls == []


def test_http_bind_failure_after_real_service_initialization_closes_agent_client(config):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        config['port'] = occupied.getsockname()[1]
        with pytest.raises(OSError):
            executor.create_server(**config)
    assert (config['root'] / 'creation-service.sqlite3').is_file()
    assert (config['root'] / 'method-lab/methods.sqlite3').is_file()
    assert len(NoNetworkAgentClient.instances) == 2
    assert all(client.close_count == 1 and client.calls == [] for client in NoNetworkAgentClient.instances)


def test_renderer_configuration_failure_does_not_construct_an_agent_client(config):
    config['renderer_image'] = 'registry.example/renderer:latest'
    with pytest.raises(ServiceError, match='animation_creation_render_image_pin_required'):
        executor.create_server(**config)
    assert NoNetworkAgentClient.instances == [] and not config['root'].exists()


def test_private_http_accepts_only_caller_credential_and_does_not_expose_tokens_or_submit(config):
    server, client = executor.create_server(**config)
    try:
        with serving(server):
            for token in (None, AGENT_TOKEN):
                status, value, _ = request(server, '/api/creation-runs', token=token, owner='developer-1')
                assert status == 403 and value == {'error': 'animation_creation_unauthorized'}
            status, value, headers = request(server, '/api/creation-runs', token=CALLER_TOKEN, owner='developer-1')
            assert status == 200 and value['enabled'] is True and value['runs'] == []
            assert headers['Cache-Control'] == 'no-store'
            encoded = json.dumps(value)
            assert AGENT_TOKEN not in encoded and CALLER_TOKEN not in encoded
            method_path = '/api/method-lab/catalog'
            assert request(server, method_path, token=AGENT_TOKEN, owner='developer-1')[0] == 403
            status, methods, _ = request(server, method_path, token=CALLER_TOKEN, owner='developer-1')
            assert status == 200 and methods['effects'] == methods['commissions'] == []
            assert methods['availability']['enabled'] is True
            assert AGENT_TOKEN not in json.dumps(methods) and CALLER_TOKEN not in json.dumps(methods)
            assert server.method_service.pending() == []
            assert client.calls == [] and server.service.pending() == []
    finally:
        client.close()
    assert not server.pump.thread.is_alive()
    assert not server.method_pump.thread.is_alive()


def test_private_health_is_unauthenticated_status_only_and_never_drives_work(config):
    server, client = executor.create_server(**config)
    try:
        with serving(server):
            status, value, headers = request(server, '/healthz')
            assert status == 200 and value == {'status': 'ok'}
            assert headers['Cache-Control'] == 'no-store'
            assert AGENT_TOKEN not in json.dumps(value) and CALLER_TOKEN not in json.dumps(value)
            assert request(server, '/api/creation-runs')[0] == 403
            assert request(server, '/healthz?not-a-health-probe=1')[0] == 403
            assert client.calls == [] and server.service.pending() == []
    finally:
        client.close()


def test_unconfigured_methods_are_disabled_without_expanding_legacy_caller(config):
    config.pop('method_agent_token_file')
    server, client = executor.create_server(**config)
    try:
        with serving(server):
            status, value, _ = request(server, '/api/method-lab/catalog', token=CALLER_TOKEN, owner='developer-1')
            assert status == 200 and value['availability']['enabled'] is False
            assert server.method_service is server.method_pump is None
            assert len(NoNetworkAgentClient.instances) == 1 and client.calls == []
    finally:
        client.close()


@pytest.mark.parametrize('token', [AGENT_TOKEN, CALLER_TOKEN])
def test_methods_require_a_distinct_scoped_agent_credential(config, token):
    config['method_agent_token_file'].write_text(token, encoding='ascii')
    with pytest.raises(ValueError, match='animation_method_distinct_credentials_required'):
        executor.create_server(**config)
    assert all(client.close_count == 1 for client in NoNetworkAgentClient.instances)
