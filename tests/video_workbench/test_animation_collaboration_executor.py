"""Collaborative author startup uses its independent caller and performs no I/O."""
import socket

import pytest

from video_workbench import animation_creation_executor as executor
from test_animation_creation_executor import (
    config, NoNetworkAgentClient, AGENT_TOKEN, CALLER_TOKEN, METHOD_TOKEN, IMAGE,
)


COLLABORATION_TOKEN = 'unit-collaboration-credential-' + 'f' * 40


@pytest.fixture
def collaborative(config, tmp_path):
    token = tmp_path / 'collaboration-token'
    token.write_text(COLLABORATION_TOKEN + '\n', encoding='ascii')
    return dict(config, enable_collaborative_authoring=True, collaboration_agent_token_file=token,
                method_project_runtime='hyperframes-core-bridge')


def test_explicit_collaboration_has_its_own_caller_and_closes_it_once(collaborative):
    server, client = executor.create_server(**collaborative)
    collaboration_client = server.method_service.collaboration_author.client
    try:
        assert collaboration_client.token == COLLABORATION_TOKEN
        assert collaboration_client is not client
        assert collaboration_client is not server.method_service.author.client
        assert server.method_service.author.client.token == METHOD_TOKEN
        assert server.method_service.collaboration_author.profile == 'video.animation-collaboration.v1'
        assert server.method_service.catalogue('test-developer')['availability']['collaboration'] is True
        assert server.method_service.pending() == []
        assert len(NoNetworkAgentClient.instances) == 3
        assert all(value.calls == [] for value in NoNetworkAgentClient.instances)
    finally:
        server.server_close()
        server.server_close()
        client.close()
    assert all(value.close_count == 1 for value in NoNetworkAgentClient.instances)


@pytest.mark.parametrize('missing', ['flag', 'collaboration_agent_token_file', 'method_agent_token_file'])
def test_incomplete_collaboration_fails_before_storage_clients_or_startup(collaborative, missing):
    if missing == 'flag':
        collaborative['enable_collaborative_authoring'] = False
    else:
        collaborative.pop(missing)
    with pytest.raises(ValueError, match='animation_collaboration_config_incomplete'):
        executor.create_server(**collaborative)
    assert NoNetworkAgentClient.instances == []
    assert not collaborative['root'].exists()


@pytest.mark.parametrize('changes', [dict(method_protocol='legacy'), dict(method_project_runtime='native-seek')])
def test_collaboration_requires_skills_and_real_hyperframes_before_initialization(collaborative, changes):
    with pytest.raises(ValueError, match='animation_collaboration_hyperframes_skills_required'):
        executor.create_server(**dict(collaborative, **changes))
    assert NoNetworkAgentClient.instances == []
    assert not collaborative['root'].exists()


@pytest.mark.parametrize('token', [AGENT_TOKEN, CALLER_TOKEN, METHOD_TOKEN])
def test_collaboration_rejects_existing_business_credentials_before_initialization(collaborative, token):
    collaborative['collaboration_agent_token_file'].write_text(token, encoding='ascii')
    with pytest.raises(ValueError, match='animation_collaboration_distinct_credentials_required'):
        executor.create_server(**collaborative)
    assert NoNetworkAgentClient.instances == []
    assert not collaborative['root'].exists()


def test_executor_bind_failure_closes_the_independent_collaboration_client(collaborative):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0)); occupied.listen(1)
        with pytest.raises(OSError):
            executor.create_server(**dict(collaborative, port=occupied.getsockname()[1]))
    assert len(NoNetworkAgentClient.instances) == 3
    assert all(value.close_count == 1 and value.calls == [] for value in NoNetworkAgentClient.instances)


def test_manim_cannot_reuse_collaboration_credential_and_failure_closes_clients(collaborative):
    with pytest.raises(ValueError, match='manim_method_distinct_credentials_required'):
        executor.create_server(**dict(collaborative,
            manim_recipe_agent_token_file=collaborative['collaboration_agent_token_file'], manim_resource_image=IMAGE))
    assert len(NoNetworkAgentClient.instances) == 3
    assert all(value.close_count == 1 and value.calls == [] for value in NoNetworkAgentClient.instances)


def test_cli_projects_both_collaboration_opt_in_and_distinct_token_to_factory(collaborative, monkeypatch):
    observed = {}

    class FactoryIntercept(Exception):
        pass

    def intercept(**options):
        observed.update(options)
        raise FactoryIntercept

    monkeypatch.setattr(executor, 'create_server', intercept)
    with pytest.raises(FactoryIntercept):
        executor.main(['--root', str(collaborative['root']), '--agent-url', collaborative['agent_url'],
            '--agent-token-file', str(collaborative['agent_token_file']),
            '--token-file', str(collaborative['token_file']), '--renderer-image', IMAGE,
            '--method-agent-token-file', str(collaborative['method_agent_token_file']),
            '--method-project-runtime', 'hyperframes-core-bridge', '--enable-isolated-rendering',
            '--enable-collaborative-authoring', '--collaboration-agent-token-file',
            str(collaborative['collaboration_agent_token_file'])])
    assert observed['enable_collaborative_authoring'] is True
    assert observed['collaboration_agent_token_file'] == collaborative['collaboration_agent_token_file']
    assert NoNetworkAgentClient.instances == []
