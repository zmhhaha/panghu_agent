"""Offline collaboration Secret/flag generation; no cluster changes."""
from dataclasses import replace

import pytest

from video_workbench.deploy.animation_creation_executor import COLLABORATION_TOKEN, render
from test_animation_creation_executor_deploy import settings, pod


def test_default_has_no_collaboration_dispatch_or_secret(settings):
    spec = pod(render(settings))
    assert '--enable-collaborative-authoring' not in spec['containers'][0]['args']
    assert not any(row['name'] == 'collaboration-agent-token' for row in spec['volumes'])


def test_explicit_enable_uses_an_independent_readonly_token(settings):
    spec = pod(render(replace(settings, method_agent_token_secret='methods',
        collaboration_agent_token_secret='collaboration', enable_collaborative_authoring=True)))
    container = spec['containers'][0]
    assert container['args'][-3:] == ['--enable-collaborative-authoring',
        '--collaboration-agent-token-file', COLLABORATION_TOKEN]
    assert next(row for row in spec['volumes'] if row['name'] == 'collaboration-agent-token')['secret'] == dict(
        secretName='collaboration', optional=False, defaultMode=0o400, items=[dict(key='token', path='token')])
    assert next(row for row in container['volumeMounts']
                if row['name'] == 'collaboration-agent-token')['readOnly'] is True


@pytest.mark.parametrize('changes', [dict(), dict(method_agent_token_secret='methods'),
    dict(collaboration_agent_token_secret='collaboration'),
    dict(method_agent_token_secret='methods', collaboration_agent_token_secret='collaboration', method_protocol='legacy'),
    dict(method_agent_token_secret='methods', collaboration_agent_token_secret='methods'),
    dict(method_agent_token_secret='methods', collaboration_agent_token_secret='creation-agent-caller'),
    dict(method_agent_token_secret='methods', collaboration_agent_token_secret='creation-lab-caller')])
def test_incomplete_or_shared_authority_fails_before_generation(settings, changes):
    with pytest.raises(ValueError):
        render(replace(settings, enable_collaborative_authoring=True, **changes))
