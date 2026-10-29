"""Real ASGI/SQLite checks; synthetic worker readiness is not engine acceptance."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import uuid

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_service.api import ApiSettings, create_app
from agent_service.client import AgentClient
from agent_service.contracts import (JobRequest, Principal, ProfilePolicy,
                                     ProfileRegistry, ServiceError, validate_workspace_task)
from agent_service.store import Store


TOKEN = 'a' * 40
OTHER = 'b' * 40
COLLEAGUE = 'c' * 40
PROFILE = ProfilePolicy('workspace-task.v1', 'r1', 'pi', 'runtime-r1',
                        'sha256:' + 'a' * 64, enabled=True,
                        task_validator=validate_workspace_task, supports_inputs=True)


class Ready:
    def ready(self, policy):
        return True


def settings(**kwargs):
    return ApiSettings({TOKEN: Principal('tenant-a', 'ppt-service', frozenset({PROFILE.name})),
                        OTHER: Principal('tenant-b', 'ppt-service', frozenset({PROFILE.name})),
                        COLLEAGUE: Principal('tenant-a', 'research-service', frozenset({PROFILE.name}))}, **kwargs)


def request_body(**kwargs):
    value = {'api_version': '1', 'profile': PROFILE.name, 'profile_revision': 'r1',
             'business_ref': 'project-stage-1', 'task': {'prompt': 'Private task source'},
             'timeout_seconds': 300}
    value.update(kwargs)
    return value


@pytest.fixture
def setup(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'queue.db'), allow_sqlite=True)
    store.initialize()
    app = create_app(settings(), store, ProfileRegistry([PROFILE]), {'pi': Ready()})
    with TestClient(app) as client:
        yield client, store
    store.engine.dispose()


def headers(token=TOKEN, key='request-1'):
    return {'Authorization': 'Bearer ' + token, 'Idempotency-Key': key}


def submit(client, **kwargs):
    return client.post('/v1/jobs', json=request_body(**kwargs), headers=headers())


def test_authentication_is_required_and_only_health_is_public(setup):
    client, _ = setup
    assert client.get('/healthz').json() == {'status': 'ok'}
    for path in ('/v1/capabilities', '/v1/jobs/' + str(uuid.uuid4()), '/unknown'):
        assert client.get(path).json() == {'detail': {'code': 'authentication_required'}}
    assert client.get('/v1/capabilities', headers={'Authorization': 'Bearer ' + 'wrong' * 8}).status_code == 401
    assert client.get('/openapi.json', headers=headers()).status_code == 404


def test_capabilities_do_not_imply_native_features_or_publication(setup):
    client, store = setup
    item = client.get('/v1/capabilities', headers=headers()).json()['profiles'][0]
    assert item['ready'] is True
    assert item['engine'] == 'pi'
    assert item['native_resume'] is item['native_fork'] is item['steer'] is False
    assert item['publication_authorized'] is False
    app = create_app(settings(), store, ProfileRegistry([PROFILE]))
    with TestClient(app) as unconfigured:
        assert unconfigured.get('/v1/capabilities', headers=headers()).json()['profiles'][0]['ready'] is False
        assert submit(unconfigured).status_code == 503
    assert store.claim() is None


def test_submit_projects_public_metadata_and_idempotently_freezes_deadline(setup):
    client, store = setup
    first = submit(client)
    assert first.status_code == 202
    value = first.json()
    assert value['state'] == 'queued' and value['usage'] is None
    assert value['publication_authorized'] is False
    assert 'Private task source' not in first.text
    assert 'tenant-a' not in first.text and 'ppt-service' not in first.text
    second = submit(client)
    assert second.status_code == 200 and second.json() == value
    conflict = submit(client, task={'prompt': 'changed'})
    assert conflict.status_code == 409
    assert store.get_job('tenant-a', 'ppt-service', value['job_id'])['request']['task']['prompt'] == 'Private task source'


def test_replay_survives_changed_route_missing_revision_and_worker_unavailability(setup):
    client, store = setup
    original = submit(client).json()
    changed = replace(PROFILE, engine='hermes', revision='r2', enabled=False)
    app = create_app(settings(), store, ProfileRegistry([changed]))
    with TestClient(app) as switched:
        replay = submit(switched)
        assert replay.status_code == 200
        assert replay.json() == original
        assert switched.get('/v1/jobs/' + original['job_id'], headers=headers()).status_code == 200
        new = switched.post('/v1/jobs', json=request_body(), headers=headers(key='new'))
        assert new.status_code == 404


def test_profile_authorization_is_rechecked_for_replay(setup):
    client, store = setup
    submit(client)
    revoked = ApiSettings({TOKEN: Principal('tenant-a', 'ppt-service', frozenset())})
    with TestClient(create_app(revoked, store, ProfileRegistry([PROFILE]), {'pi': Ready()})) as denied:
        assert submit(denied).status_code == 404


def test_profile_revocation_blocks_historical_reads_but_allows_cancellation(setup):
    client, store = setup
    identifier = submit(client).json()['job_id']
    claim = store.claim()
    # Synthetic fixture completion lets this test exercise result ACL, not an
    # actual engine stop or production publication boundary.
    store.finish(identifier, claim['attempt_id'], claim['fence'],
                 {'state': 'completed', 'usage': None},
                 [{'name': 'result.txt', 'content': b'private'}], stopped=True)
    artifact = store.get_artifacts('tenant-a', 'ppt-service', identifier)[0]['artifact_id']
    active = client.post('/v1/jobs', json=request_body(business_ref='active'),
                          headers=headers(key='active')).json()['job_id']
    revoked = ApiSettings({TOKEN: Principal('tenant-a', 'ppt-service', frozenset())})
    with TestClient(create_app(revoked, store, ProfileRegistry())) as denied:
        for suffix in ('', '/events', '/artifacts'):
            assert denied.get('/v1/jobs/' + identifier + suffix, headers=headers()).status_code == 404
        assert denied.get('/v1/artifacts/' + artifact + '/content', headers=headers()).status_code == 404
        stopped = denied.post('/v1/jobs/' + active + '/cancel', json={}, headers=headers())
        assert stopped.status_code == 200 and stopped.json()['state'] == 'cancelled'


@pytest.mark.parametrize('extra', [{'engine': 'hermes'}, {'tenant': 'tenant-b'},
                                   {'executable': '/bin/sh'}, {'timeout_seconds': True},
                                   {'task': {'prompt': 'secret', '_runtime_uid': 0}}])
def test_unknown_authority_and_invalid_task_fields_rejected_without_echo(setup, extra):
    client, store = setup
    response = submit(client, **extra)
    assert response.status_code == 422
    assert 'secret' not in response.text and '/bin/sh' not in response.text
    assert store.claim() is None


def test_duplicate_json_key_and_nonfinite_values_rejected(setup):
    client, _ = setup
    for raw in ('{"profile":"secret","profile":"changed"}', '{"task":NaN}'):
        response = client.post('/v1/jobs', content=raw,
                               headers={**headers(), 'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert 'secret' not in response.text


def test_missing_idempotency_key_and_json_body_bound(setup):
    client, store = setup
    response = client.post('/v1/jobs', json=request_body(), headers={'Authorization': 'Bearer ' + TOKEN})
    assert response.status_code == 422
    response = client.post('/v1/jobs', content=b'x' * 65537,
                           headers={**headers(), 'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert store.claim() is None


def test_inputs_bound_streams_names_and_content_type(setup):
    _, store = setup
    app = create_app(settings(max_input_bytes=4), store, ProfileRegistry([PROFILE]), {'pi': Ready()})
    with TestClient(app) as client:
        for name in ('../input.txt', 'bad:name', 'bad\\name', '.'):
            assert client.post('/v1/inputs', params={'name': name}, content=b'abc',
                               headers={**headers(), 'Content-Type': 'application/octet-stream'}).status_code == 422
        assert client.post('/v1/inputs', params={'name': 'input.txt'}, content=b'abc', headers=headers()).status_code == 415
        assert client.post('/v1/inputs', params={'name': 'input.txt'}, content=b'abcde',
                           headers={**headers(), 'Content-Type': 'application/octet-stream'}).status_code == 413
        response = client.post('/v1/inputs', params={'name': '材料.txt'}, content=b'abcd',
                                headers={**headers(), 'Content-Type': 'application/octet-stream'})
        assert response.status_code == 201
        meta = response.json()
        assert meta['sha256'] == hashlib.sha256(b'abcd').hexdigest()
        assert 'content' not in meta and meta['size'] == 4


def test_input_references_are_caller_owned_and_hash_checked(setup):
    client, _ = setup
    value = client.post('/v1/inputs', params={'name': 'source.txt'}, content=b'private',
                         headers={**headers(), 'Content-Type': 'application/octet-stream'}).json()
    ref = {'input_id': value['input_id'], 'sha256': value['sha256']}
    for token in (OTHER, COLLEAGUE):
        assert client.post('/v1/jobs', json=request_body(inputs=[ref]), headers=headers(token)).status_code == 404
    wrong = dict(ref, sha256='0' * 64)
    assert submit(client, inputs=[wrong]).status_code == 409
    assert submit(client, inputs=[ref]).status_code == 202


def test_cross_owner_job_cancel_events_and_artifacts_are_not_found(setup):
    client, _ = setup
    identifier = submit(client).json()['job_id']
    for token in (OTHER, COLLEAGUE):
        for suffix in ('', '/events', '/artifacts'):
            assert client.get('/v1/jobs/' + identifier + suffix, headers=headers(token)).status_code == 404
        assert client.post('/v1/jobs/' + identifier + '/cancel', json={}, headers=headers(token)).status_code == 404
    assert client.get('/v1/jobs/not-a-uuid', headers=headers()).status_code == 404


def test_cancel_is_idempotent_and_running_job_waits_for_stop(setup):
    client, store = setup
    first = submit(client).json()['job_id']
    response = client.post('/v1/jobs/' + first + '/cancel', json={}, headers=headers())
    assert response.json()['state'] == 'cancelled'
    cursor = response.json()['event_cursor']
    assert client.post('/v1/jobs/' + first + '/cancel', json={}, headers=headers()).json()['event_cursor'] == cursor
    second = client.post('/v1/jobs', json=request_body(business_ref='two'), headers=headers(key='two')).json()['job_id']
    claim = store.claim()
    assert claim['job_id'] == second
    response = client.post('/v1/jobs/' + second + '/cancel', json={}, headers=headers())
    assert response.json()['state'] == 'cancelling'
    assert store.get_artifacts('tenant-a', 'ppt-service', second) == []


def test_events_paginate_without_changing_execution(setup):
    client, store = setup
    identifier = submit(client).json()['job_id']
    claim = store.claim()
    store.mark_running(identifier, claim['attempt_id'], claim['fence'])
    first = client.get('/v1/jobs/' + identifier + '/events?limit=1', headers=headers()).json()
    assert first['has_more'] is True and first['next_cursor'] == 1
    second = client.get('/v1/jobs/' + identifier + '/events',
                         params={'after': first['next_cursor'], 'limit': 100}, headers=headers()).json()
    assert [x['seq'] for x in second['items']] == [2, 3]
    assert client.get('/v1/jobs/' + identifier, headers=headers()).json()['state'] == 'running'
    bad = client.get('/v1/jobs/' + identifier + '/events?after=secret', headers=headers())
    assert bad.status_code == 422 and 'secret' not in bad.text
    assert client.get('/v1/jobs/' + identifier + '/events',
                      params={'after': str(2**63)}, headers=headers()).status_code == 422


def test_artifact_download_reauthorizes_and_hides_internal_receipt(setup):
    client, store = setup
    identifier = submit(client).json()['job_id']
    claim = store.claim()
    # Explicit synthetic trusted-controller result, not an actual engine proof.
    store.finish(identifier, claim['attempt_id'], claim['fence'],
                 {'state': 'completed', 'usage': None, 'stop_receipt': {'private_resource': 'private-pod'}},
                 [{'name': 'result.txt', 'content': b'result'}], stopped=True)
    job = client.get('/v1/jobs/' + identifier, headers=headers())
    assert job.json()['usage'] is None and 'private-pod' not in job.text
    artifact = client.get('/v1/jobs/' + identifier + '/artifacts', headers=headers()).json()['items'][0]
    route = '/v1/artifacts/' + artifact['artifact_id'] + '/content'
    assert client.get(route, headers=headers(OTHER)).status_code == 404
    assert client.get(route, headers=headers(COLLEAGUE)).status_code == 404
    response = client.get(route, headers=headers())
    assert response.content == b'result'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['etag'] == '"' + hashlib.sha256(b'result').hexdigest() + '"'


def test_client_does_not_follow_redirect_or_retry_leak_credential():
    calls = []
    def responder(request):
        calls.append(request)
        return httpx.Response(307, headers={'Location': 'https://other.invalid/steal'})
    with AgentClient('https://agent.internal', TOKEN, transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(ServiceError, match='service_redirect_rejected'):
            client.capabilities()
    assert len(calls) == 1 and calls[0].url.host == 'agent.internal'


def test_client_bounds_responses_and_sanitizes_upstream_errors():
    calls = []
    def responder(request):
        calls.append(request)
        return httpx.Response(503, text='private provider URL token secret')
    with AgentClient('http://agent-service.svc', TOKEN, transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(ServiceError) as error:
            client.submit(request_body(), idempotency_key='explicit-key')
        assert error.value.code == 'service_request_failed'
        assert 'private' not in str(error.value)
    assert len(calls) == 1
    assert calls[0].headers['Idempotency-Key'] == 'explicit-key'


def test_client_rejects_large_stream_and_unexpected_content_encoding():
    class Large(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * 800000
            yield b'x' * 800000
    with AgentClient('http://agent-service.svc', TOKEN, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Large()))) as client:
        with pytest.raises(ServiceError, match='service_response_too_large'):
            client.capabilities()
    with AgentClient('http://agent-service.svc', TOKEN, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={'Content-Encoding': 'gzip'}, stream=Large()))) as client:
        with pytest.raises(ServiceError, match='service_protocol_invalid'):
            client.capabilities()


def test_client_invalid_job_never_echoes_task_or_sends_request():
    calls = []
    def responder(request):
        calls.append(request)
        return httpx.Response(200, json={})
    with AgentClient('http://agent-service.svc', TOKEN, transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(ServiceError) as error:
            client.submit(request_body(engine='private-token'), idempotency_key='explicit-key')
        assert error.value.code == 'invalid_request'
        assert 'private-token' not in str(error.value)
    assert calls == []


def test_profile_readiness_must_match_exact_revision_and_pins(setup):
    _, store = setup
    changed = replace(PROFILE, revision='r2', image_digest='sha256:' + 'b' * 64)
    class Exact:
        def ready(self, policy):
            return policy.image_digest == PROFILE.image_digest and policy.revision == PROFILE.revision
    app = create_app(settings(), store, ProfileRegistry([PROFILE, changed]), {'pi': Exact()})
    with TestClient(app) as client:
        capabilities = client.get('/v1/capabilities', headers=headers()).json()['profiles']
        assert {x['profile_revision']: x['ready'] for x in capabilities} == {'r1': True, 'r2': False}
        assert submit(client, profile_revision='r2').status_code == 503
    assert store.claim() is None


@pytest.mark.parametrize('origin', ['https://user:password@agent.internal',
                                    'https://agent.internal/path', 'https://agent.internal?token=secret',
                                    'file:///tmp/api'])
def test_client_rejects_unsafe_configured_origins(origin):
    with pytest.raises(ValueError):
        AgentClient(origin, TOKEN)
