"""Shared released Skills retain distinct publisher and project identities."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.app import create_app
from video_workbench.config import ProductionMethodSettings
from test_animation_skill_production import published, metadata, TASK


TOKEN = 'publisher-test-credential-' + 'x' * 32
PUBLISHER = 'casdoor:animation-developer'


@pytest.mark.parametrize('publisher', ['', ' ', 'a\nb', 'a b', 'x' * 201, '\ud800'])
def test_invalid_publisher_fails_closed_without_exposing_a_credential(publisher):
    with pytest.raises(ValueError) as caught:
        ProductionMethodSettings.from_env(dict(VIDEO_METHOD_LAB_URL='http://private.invalid',
            VIDEO_METHOD_LAB_TOKEN=TOKEN, VIDEO_METHOD_LAB_PUBLISHER_OWNER=publisher))
    assert TOKEN not in str(caught.value)


def test_publisher_cannot_be_configured_without_the_private_service():
    with pytest.raises(ValueError, match='requires VIDEO_METHOD_LAB'):
        ProductionMethodSettings.from_env(dict(VIDEO_METHOD_LAB_PUBLISHER_OWNER=PUBLISHER))


@pytest.mark.parametrize('shared', [False, True])
def test_app_freezes_release_source_separately_from_recipient_and_historical_methods(tmp_path, monkeypatch, shared):
    monkeypatch.setenv('VIDEO_METHOD_LAB_URL', 'http://private.invalid')
    monkeypatch.setenv('VIDEO_METHOD_LAB_TOKEN', TOKEN)
    if shared:
        monkeypatch.setenv('VIDEO_METHOD_LAB_PUBLISHER_OWNER', PUBLISHER)
    else:
        monkeypatch.delenv('VIDEO_METHOD_LAB_PUBLISHER_OWNER', raising=False)
    for key in ('VIDEO_SKILL_PRODUCTION_AGENT_URL', 'VIDEO_SKILL_PRODUCTION_AGENT_TOKEN',
                'VIDEO_SKILL_PRODUCTION_AGENT_TOKEN_FILE', 'VIDEO_PRODUCTION_AGENT_URL',
                'VIDEO_PRODUCTION_AGENT_TOKEN', 'VIDEO_PRODUCTION_AGENT_TOKEN_FILE'):
        monkeypatch.delenv(key, raising=False)
    calls = []
    released = published(supporting_file=True)

    class ReleasedOnlyClient:
        def __init__(self, url, token):
            assert url == 'http://private.invalid' and token == TOKEN

        def published_catalog(self, owner):
            calls.append(('catalogue', owner))
            return dict(methods=[metadata(released)])

        def published_methods(self, owner, body):
            calls.append(('read', owner, deepcopy(body)))
            assert body['method_version_ids'] == [released['id']]
            return dict(methods=[deepcopy(released)],
                        read_receipt=dict(owner=owner, consumer_id=body['consumer_id']))

        def __getattr__(self, name):
            raise AssertionError('Production cannot access Wiki, candidates or authoring: ' + name)

    monkeypatch.setattr('video_workbench.animation_method_http.MethodLabClient', ReleasedOnlyClient)
    app = create_app(db_path=tmp_path / 'production.db', gateway=object(), background=False)
    workflow = app.state.motion_workflow
    one = workflow.skill_adapter.freeze('alice', 'same-operation', TASK, ['html-svg'])
    two = workflow.skill_adapter.freeze('bob', 'same-operation', TASK, ['html-svg'])
    assert (one['recipient_owner'], two['recipient_owner']) == ('alice', 'bob')
    assert one['catalog']['sha256'] != two['catalog']['sha256']
    reads = [call for call in calls if call[0] == 'read']
    assert reads[0][2]['consumer_id'] != reads[1][2]['consumer_id']
    assert reads[0][1] == (PUBLISHER if shared else 'alice')
    assert reads[1][1] == (PUBLISHER if shared else 'bob')
    assert one['retrieval_receipts'][0]['receipt']['owner'] == reads[0][1]
    assert one['packages'] == two['packages']
    with pytest.raises(ServiceError):
        workflow.skill_adapter.get_bundle('bob', 'same-operation', one['catalog']['sha256'])
    count = len(calls)
    assert workflow.skill_adapter.freeze('alice', 'same-operation', TASK, ['html-svg']) == one
    assert len(calls) == count
    # Historical delivery has a different owner-bound contract and is never
    # reinterpreted as a shared publisher read, including the old joint planner.
    workflow.method_adapter.catalogue('alice')
    assert calls[-1] == ('catalogue', 'alice')
