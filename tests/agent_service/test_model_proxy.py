"""Offline grants and durable admissions use the real execution database.

Synthetic catalog acceptance and MockTransport do not establish native Worker
or upstream readiness. No model/provider or academic network calls are made.
"""
from copy import deepcopy
from dataclasses import replace
import json
import logging
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select, update

from agent_service.contracts import ExecutionBindingPins, ServiceError, canonical_bytes, digest
from agent_service.model_proxy import (
    ModelGrantIssuer, ModelProxy, ModelProxySettings, create_model_proxy_app,
    observations, requests,
)
from agent_service.registry import DeploymentRegistry
from agent_service.store import Store, attempts, jobs, worker_allocations
from registry_fixtures import make_catalog
from scientific_agent.academic_observations import (
    AcademicObservationError, validate_academic_observations,
)


@pytest.fixture
def execution(tmp_path):
    opened = []

    def make(*, engine='pi', profile='workspace-task.v1', allocation=True, **settings):
        configured = make_catalog(engine, True)
        registry = DeploymentRegistry.from_config(**{
            key: value for key, value in configured.items() if key != 'schema_version'})
        policy = registry.profiles().policy('workspace-task.v1', 'v1')
        model_policy = registry.binding('workspace-' + engine, 'binding-v1').model_policy
        if profile != policy.name:
            # Only model scope is under test. This explicitly synthetic binding
            # supplies no native profile acceptance or production route.
            identity = policy.execution_binding.model_dump(mode='json', exclude={'sha256', 'schema_version'})
            identity['profile'] = profile
            policy = replace(policy, name=profile,
                execution_binding=ExecutionBindingPins.create(**identity))
        store = Store('sqlite:///' + str(tmp_path / (str(len(opened)) + '.db')),
                      allow_sqlite=True)
        store.initialize()
        opened.append(store)
        request = dict(api_version='1', profile=profile, profile_revision='v1',
            business_ref='offline-task', task={'prompt': 'offline'}, inputs=[], timeout_seconds=600)
        store.submit('tenant', 'business', 'one', request, policy.pins())
        claimed = store.claim(lease_seconds=300, provisioning={engine: 'kubernetes_per_attempt'})
        with store.engine.connect() as db:
            row = db.execute(select(jobs).where(jobs.c.id == claimed['id'])).mappings().one()
            envelope = store._worker_envelope(db, row)
        if allocation:
            store.reserve_worker(envelope, 'offline-pool', 'agent-workers',
                'worker-' + envelope.attempt_id, 'a' * 64, agent_uid=2000000, aux_uid=2000001)
            assert store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
        calls = []

        def upstream(request):
            calls.append(request)
            return httpx.Response(200, json={'object': 'response', 'output': []})

        client = httpx.Client(transport=httpx.MockTransport(upstream))
        opened.append(client)
        proxy = ModelProxy(store, ModelProxySettings(b'g' * 32,
            'https://fixed-upstream.invalid/v1', 'offline-provider-token', **settings), client=client)
        token = proxy.issuer.issue(envelope, model_policy)
        return SimpleNamespace(store=store, envelope=envelope, model_policy=model_policy,
            proxy=proxy, token=token, calls=calls,
            body={'model': model_policy.model, 'input': 'Offline fixture'})

    yield make
    for item in reversed(opened):
        item.close()


def error(code, action, status=None):
    with pytest.raises(ServiceError) as failure:
        action()
    assert failure.value.code == code
    if status is not None:
        assert failure.value.status == status


@pytest.mark.parametrize('engine', ['pi', 'hermes'])
def test_both_engines_use_scoped_responses_and_replay_without_spending_again(execution, engine):
    run = execution(engine=engine)
    assert run.proxy.issuer.issue(run.envelope, run.model_policy) == run.token
    grant = run.proxy.authorize(run.token)
    assert grant['request'] == run.envelope.request_sha256
    assert grant['binding'] == run.envelope.execution_binding.sha256
    expected = run.proxy.respond(run.token, run.body)
    assert expected == run.proxy.respond(run.token, dict(reversed(list(run.body.items()))))
    assert len(run.calls) == 1
    request = run.calls[0]
    assert str(request.url) == 'https://fixed-upstream.invalid/v1/responses'
    sent = json.loads(request.content)
    assert sent['store'] is False and sent['max_output_tokens'] == 32768
    assert sent['model'] == run.model_policy.model
    assert request.headers['authorization'] == 'Bearer offline-provider-token'
    assert 'offline-provider-token' not in run.token


def test_ephemeral_native_cache_hint_is_not_forwarded_or_a_new_paid_admission(execution):
    run = execution()
    body = dict(run.body, stream=True, max_output_tokens=8192,
        tools=[{'type': 'function', 'name': 'read', 'parameters': {'type': 'object'}}],
        prompt_cache_key='private-native-session-id')
    result = run.proxy.respond(run.token, body)
    sent = json.loads(run.calls[0].content)
    assert sent == {**run.body, 'stream': True, 'max_output_tokens': 8192,
                   'tools': body['tools'], 'store': False}
    assert 'private-native-session-id' not in run.calls[0].content.decode()
    assert run.proxy.respond(run.token, dict(body, prompt_cache_key='another-native-session')) == result
    assert run.proxy.respond(run.token, {key: value for key, value in body.items()
                                        if key != 'prompt_cache_key'}) == result
    assert len(run.calls) == 1


@pytest.mark.parametrize('cache_hint', [False, [], '', 'x' * 257, 'line\nbreak'])
def test_invalid_native_cache_hint_fails_before_admission(execution, cache_hint):
    run = execution()
    error('model_request_scope_invalid', lambda: run.proxy.respond(
        run.token, dict(run.body, prompt_cache_key=cache_hint)), 422)
    assert run.calls == []
    with run.store.engine.connect() as db:
        assert db.execute(select(requests)).all() == []


def test_complete_response_is_replayed_by_new_proxy_after_controller_restart(execution):
    run = execution()
    original = run.proxy.respond(run.token, run.body)
    recovered_store = Store(str(run.store.engine.url), allow_sqlite=True)
    recovered_store.initialize()
    recovered = ModelProxy(recovered_store, run.proxy.settings,
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail('Completed admission must not call upstream'))))
    try:
        assert recovered.respond(run.token, run.body) == original
    finally:
        recovered.client.close()
        recovered_store.close()


@pytest.mark.parametrize('token', ['', 'not-a-grant', 'a.b', None, 'x' * 2049])
def test_invalid_grants_never_reach_the_upstream(execution, token):
    run = execution()
    error('model_grant_invalid', lambda: run.proxy.respond(token, run.body), 401)
    assert run.calls == []


def test_signature_cannot_be_reused_with_another_attempt_or_signing_key(execution):
    run = execution()
    payload, signature = run.token.split('.')
    error('model_grant_invalid', lambda: run.proxy.authorize(payload[:-1] + 'A.' + signature), 401)
    error('model_grant_invalid', lambda: ModelGrantIssuer(b'z' * 32).decode(run.token), 401)
    expired = run.proxy.issuer.issue(run.envelope.model_copy(
        update={'deadline_at': time.time() - 1}), run.model_policy)
    error('model_grant_invalid', lambda: run.proxy.authorize(expired), 401)


@pytest.mark.parametrize('table,change', [
    (jobs, {'cancel_requested': True}),
    (jobs, {'fence': 999}),
    (jobs, {'state': 'completed'}),
    (jobs, {'lease_expires_at': 1.0}),
    (jobs, {'deadline_at': 1.0}),
    (attempts, {'fence': 999}),
    (worker_allocations, {'retired': True}),
    (worker_allocations, {'create_claimed': False}),
    (worker_allocations, {'request_sha256': '0' * 64}),
])
def test_grants_are_revoked_by_authoritative_job_and_allocation_state(execution, table, change):
    run = execution()
    with run.store.engine.begin() as db:
        db.execute(update(table).values(**change))
    error('model_grant_revoked', lambda: run.proxy.respond(run.token, run.body), 401)
    assert run.calls == []


def test_grant_requires_claimed_allocation_and_pinned_model_policy(execution):
    unallocated = execution(allocation=False)
    error('model_grant_revoked', lambda: unallocated.proxy.authorize(unallocated.token), 401)
    run = execution()
    changed = run.model_policy.model_copy(update={'model': 'a-different-model'})
    token = run.proxy.issuer.issue(run.envelope, changed)
    error('model_grant_scope_mismatch', lambda: run.proxy.authorize(token), 401)


def test_lost_upstream_response_retains_admission_and_never_automatically_retries(execution):
    run = execution()
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadError('private upstream detail must not escape')

    run.proxy.client.close()
    run.proxy.client = httpx.Client(transport=httpx.MockTransport(fail))
    try:
        error('model_admission_uncertain', lambda: run.proxy.respond(run.token, run.body), 502)
        error('model_admission_uncertain', lambda: run.proxy.respond(run.token, run.body), 409)
        assert len(calls) == 1
        with run.store.engine.connect() as db:
            record = db.execute(select(requests)).mappings().one()
        assert record['state'] == 'admitted' and record['response'] is None
    finally:
        run.proxy.client.close()


def test_cancel_during_response_cannot_complete_or_replay_the_admission(execution):
    run = execution()

    class CancelledStream(httpx.SyncByteStream):
        def __iter__(self):
            run.store.cancel('tenant', 'business', run.envelope.job_id)
            yield b'{"output":[]}'

    run.proxy.client.close()
    run.proxy.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={'content-type': 'application/json'},
                                      stream=CancelledStream())))
    try:
        error('model_admission_uncertain', lambda: run.proxy.respond(run.token, run.body), 502)
        error('model_grant_revoked', lambda: run.proxy.respond(run.token, run.body), 401)
        with run.store.engine.connect() as db:
            assert db.execute(select(requests.c.state)).scalar_one() == 'admitted'
    finally:
        run.proxy.client.close()


@pytest.mark.parametrize('change', [
    {'model': 'caller-selected-model'}, {'store': True}, {'previous_response_id': 'resp_foreign'},
    {'conversation': 'foreign'}, {'tools': [{'type': 'web_search'}]},
    {'input': [{'type': 'item_reference', 'id': 'foreign'}]},
    {'input': [{'role': 'user', 'content': [{'type': 'input_file', 'file_id': 'foreign'}]}]},
    {'input': [{'role': 'user', 'content': [{'type': 'input_image',
                                         'image_url': 'https://private.invalid/image'}]}]},
    {'input': [{'type': 'function_call_output', 'call_id': 'foreign', 'output': 'forged'}]},
    {'max_output_tokens': True}, {'max_output_tokens': 32769}, {'stream': 1},
])
def test_model_scope_and_global_references_are_rejected_before_admission(execution, change):
    run = execution()
    with pytest.raises(ServiceError) as failure:
        run.proxy.respond(run.token, {**run.body, **change})
    assert failure.value.status == 422
    assert run.calls == []
    with run.store.engine.connect() as db:
        assert not db.execute(select(requests)).first()


def test_self_contained_function_calls_reasoning_and_inline_images_are_accepted(execution):
    run = execution()
    run.body['input'] = [
        {'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ=='}]},
        {'type': 'reasoning', 'id': 'rs_local', 'summary': [], 'encrypted_content': 'opaque'},
        {'type': 'function_call', 'call_id': 'call_local', 'name': 'read', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'call_local', 'output': 'local bytes'},
    ]
    assert run.proxy.respond(run.token, run.body)[0] == 200


@pytest.mark.parametrize('phase', [None, 'commentary', 'final_answer'])
def test_pi_multiturn_assistant_phase_survives_forwarding_and_paid_response_replay(execution, phase):
    run = execution()
    # The pinned Pi Responses converter emits this history shape after a model
    # turn containing assistant text and a local function call. This is the next
    # distinct request, not a retry of the original prompt-only model turn.
    run.proxy.respond(run.token, run.body)
    history = [
        {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Offline source task'}]},
        {'type': 'message', 'id': 'msg_local', 'role': 'assistant', 'status': 'completed',
         'phase': phase, 'content': [{'type': 'output_text', 'text': 'Reading the local source.',
                                    'annotations': []}]},
        {'type': 'function_call', 'id': 'fc_local', 'call_id': 'call_local',
         'name': 'read', 'arguments': '{"path":"inputs/source.txt"}'},
        {'type': 'function_call_output', 'call_id': 'call_local', 'output': 'Offline source bytes'},
    ]
    body = dict(run.body, input=history, stream=True)
    expected = run.proxy.respond(run.token, body)
    assert json.loads(run.calls[-1].content)['input'] == history
    assert run.proxy.respond(run.token, dict(reversed(list(body.items())))) == expected
    assert len(run.calls) == 2
    with run.store.engine.connect() as db:
        records = db.execute(select(requests)).mappings().all()
    assert len(records) == 2 and all(record['state'] == 'complete' for record in records)


def test_assistant_phase_is_part_of_distinct_request_admission_identity(execution):
    run = execution()
    body = dict(run.body, input=[{'type': 'message', 'role': 'assistant',
        'content': 'Offline prior model turn', 'phase': 'commentary'}])
    run.proxy.respond(run.token, body)
    changed = deepcopy(body)
    changed['input'][0]['phase'] = 'final_answer'
    run.proxy.respond(run.token, changed)
    run.proxy.respond(run.token, body)
    assert len(run.calls) == 2
    assert [json.loads(call.content)['input'][0]['phase'] for call in run.calls] == ['commentary', 'final_answer']
    with run.store.engine.connect() as db:
        identities = db.execute(select(requests.c.body_sha256)).scalars().all()
    assert len(set(identities)) == 2


@pytest.mark.parametrize('phase', ['', 'analysis', 'commentary\n', True, False, 0, 1, [], {}, ['commentary']])
def test_unknown_or_wrongly_typed_phase_is_rejected_before_admission(execution, phase):
    run = execution()
    body = dict(run.body, input=[{'type': 'message', 'role': 'assistant',
        'content': 'Offline history', 'phase': phase}])
    error('model_input_reference_not_authorized', lambda: run.proxy.respond(run.token, body), 422)
    assert run.calls == []
    with run.store.engine.connect() as db:
        assert db.execute(select(requests)).first() is None


@pytest.mark.parametrize('item', [
    {'type': 'message', 'role': 'assistant', 'content': 'Offline history',
     'phase': 'commentary', 'previous_response_id': 'foreign'},
    {'type': 'function_call', 'call_id': 'call_local', 'name': 'read',
     'arguments': '{}', 'phase': 'commentary'},
    {'type': 'item_reference', 'id': 'foreign', 'phase': 'final_answer'},
])
def test_phase_support_does_not_allow_extra_fields_or_other_input_types(execution, item):
    run = execution()
    error('model_input_reference_not_authorized', lambda: run.proxy.respond(
        run.token, dict(run.body, input=[item])), 422)
    assert run.calls == []
    with run.store.engine.connect() as db:
        assert db.execute(select(requests)).first() is None


@pytest.mark.parametrize('content', [
    [{'type': 'input_text', 'text': 'Offline tool result'}],
    [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ=='}],
    [{'type': 'input_text', 'text': 'Offline preview'},
     {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,YQ==', 'detail': 'high'},
     {'type': 'input_image', 'image_url': 'data:image/webp;base64,Yg==', 'detail': 'auto'}],
])
def test_pi_image_tool_result_preserves_content_role_call_identity_and_durable_replay(execution, content):
    run = execution()
    history = [
        {'role': 'user', 'content': 'Inspect the offline preview'},
        {'type': 'function_call', 'id': 'fc_preview', 'call_id': 'call_preview',
         'name': 'read', 'arguments': '{"path":"preview.png"}'},
        {'type': 'function_call_output', 'call_id': 'call_preview', 'output': deepcopy(content)},
    ]
    original = deepcopy(history)
    body = dict(run.body, input=history, stream=True)
    result = run.proxy.respond(run.token, body)
    assert json.loads(run.calls[0].content)['input'] == original
    assert history == original
    # Recover through another proxy instance to exercise persisted response
    # replay, not an in-memory cache. No extra paid model request is allowed.
    recovered = ModelProxy(run.store, run.proxy.settings, client=httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail('Do not repay for image tool history'))))
    try:
        assert recovered.respond(run.token, body) == result
    finally:
        recovered.client.close()
    assert len(run.calls) == 1
    with run.store.engine.connect() as db:
        assert db.execute(select(requests.c.state)).scalar_one() == 'complete'


def test_changed_tool_image_bytes_create_distinct_request_identity(execution):
    run = execution()
    body = dict(run.body, input=[
        {'type': 'function_call', 'call_id': 'call_preview', 'name': 'read', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'call_preview',
         'output': [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ=='}]},
    ])
    run.proxy.respond(run.token, body)
    changed = deepcopy(body)
    changed['input'][-1]['output'][0]['image_url'] = 'data:image/png;base64,Yg=='
    run.proxy.respond(run.token, changed)
    run.proxy.respond(run.token, body)
    assert len(run.calls) == 2
    with run.store.engine.connect() as db:
        assert len(set(db.execute(select(requests.c.body_sha256)).scalars())) == 2


@pytest.mark.parametrize('output', [
    [], [{'type': 'input_text', 'text': ''}] * 2001, None, {}, False,
    [None], ['text'], [{'type': 'input_text', 'text': 42}],
    [{'type': 'input_text', 'text': 'x', 'file_id': 'foreign'}],
    [{'type': 'output_text', 'text': 'x'}],
    [{'type': 'input_file', 'file_id': 'foreign'}],
    [{'type': 'item_reference', 'id': 'foreign'}],
    [{'type': 'input_image', 'file_id': 'foreign'}],
    [{'type': 'input_image', 'image_url': 'https://remote.invalid/private.png'}],
    [{'type': 'input_image', 'image_url': 'file:///private.png'}],
    [{'type': 'input_image', 'image_url': 'data:image/svg+xml;base64,YQ=='}],
    [{'type': 'input_image', 'image_url': 'data:image/png;base64,'}],
    [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ==\n'}],
    [{'type': 'input_image', 'image_url': {'url': 'data:image/png;base64,YQ=='}}],
    [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ==', 'file_id': 'foreign'}],
    [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ==', 'detail': []}],
    [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ==', 'detail': 'unknown'}],
])
def test_tool_output_array_scope_is_checked_before_admission(execution, output):
    run = execution()
    body = dict(run.body, input=[
        {'type': 'function_call', 'call_id': 'call_preview', 'name': 'read', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'call_preview', 'output': output},
    ])
    error('model_input_reference_not_authorized', lambda: run.proxy.respond(run.token, body), 422)
    assert run.calls == []
    with run.store.engine.connect() as db:
        assert db.execute(select(requests)).first() is None


def test_inline_tool_image_cannot_reference_a_call_outside_its_history(execution):
    run = execution()
    body = dict(run.body, input=[{'type': 'function_call_output', 'call_id': 'foreign',
        'output': [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ=='}]}])
    error('model_input_reference_not_authorized', lambda: run.proxy.respond(run.token, body), 422)
    assert run.calls == []
    with run.store.engine.connect() as db:
        assert db.execute(select(requests)).first() is None


def test_tool_result_content_part_limit_includes_the_last_allowed_part():
    history = [
        {'type': 'function_call', 'call_id': 'local', 'name': 'read', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'local',
         'output': [{'type': 'input_text', 'text': 'Offline tool result'}] * 2000},
    ]
    ModelProxy._input(history)
    history[-1]['output'].append({'type': 'input_text', 'text': 'Offline excess part'})
    error('model_input_reference_not_authorized', lambda: ModelProxy._input(history), 422)


def test_sse_response_bytes_and_media_type_survive_durable_replay(execution):
    run = execution()
    payload = b'event: response.completed\ndata: {"output":[]}\n\n'
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream; charset=utf-8'},
                              content=payload)

    run.proxy.client.close()
    run.proxy.client = httpx.Client(transport=httpx.MockTransport(upstream))
    try:
        body = {**run.body, 'stream': True}
        assert run.proxy.respond(run.token, body) == (200, 'text/event-stream', payload)
        assert run.proxy.respond(run.token, body) == (200, 'text/event-stream', payload)
        assert len(calls) == 1
    finally:
        run.proxy.client.close()


def test_completed_replay_does_not_consume_the_distinct_request_budget(execution):
    run = execution(max_calls_per_attempt=1)
    run.proxy.respond(run.token, run.body)
    run.proxy.respond(run.token, run.body)
    error('model_budget_exhausted', lambda: run.proxy.respond(
        run.token, {**run.body, 'input': 'Different request'}), 429)
    assert len(run.calls) == 1


@pytest.mark.parametrize('status,headers,raw', [
    (302, {'location': 'https://other.invalid', 'content-type': 'application/json'}, b'{}'),
    (429, {'content-type': 'application/json'}, b'{"private":"provider response"}'),
    (200, {'content-type': 'text/html'}, b'private page'),
    (200, {'content-type': 'application/json'}, b''),
    (200, {'content-type': 'application/json'}, b'0123456789'),
])
def test_bad_or_oversized_upstream_responses_remain_uncertain(execution, status, headers, raw):
    run = execution(max_response_bytes=8)
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(status, headers=headers, content=raw)

    run.proxy.client.close()
    run.proxy.client = httpx.Client(transport=httpx.MockTransport(upstream))
    try:
        error('model_admission_uncertain', lambda: run.proxy.respond(run.token, run.body), 502)
        error('model_admission_uncertain', lambda: run.proxy.respond(run.token, run.body), 409)
        assert len(calls) == 1
    finally:
        run.proxy.client.close()


@pytest.mark.parametrize('failure_kind,stage,category,code,status,media', [
    ('rejected', 'validate_upstream', 'service_error', 'model_upstream_rejected', 503, 'json'),
    ('private_media', 'validate_upstream', 'service_error', 'model_upstream_protocol_invalid', 200, 'other'),
    ('connect_timeout', 'open_upstream', 'http_connect_timeout', 'unclassified_error', None, 'unknown'),
    ('read_error', 'receive_upstream', 'http_read_error', 'unclassified_error', 200, 'event_stream'),
    ('revoked', 'authorize_upstream', 'service_error', 'model_grant_revoked', None, 'unknown'),
    ('persistence', 'persist_response', 'unexpected_error', 'unclassified_error', 200, 'json'),
])
def test_upstream_failure_diagnostics_are_bounded_and_preserve_uncertain_admission(
        execution, caplog, monkeypatch, failure_kind, stage, category, code, status, media):
    run = execution()
    private = 'PRIVATE_BODY_HEADER_EXCEPTION_AND_REQUEST_DATA'
    run.body['input'] = private
    calls = []

    class BrokenStream(httpx.SyncByteStream):
        def __init__(self, request):
            self.request = request

        def __iter__(self):
            raise httpx.ReadError(private, request=self.request)
            yield b''  # Explicit stream generator; no model bytes are delivered.

    def upstream(request):
        calls.append(request)
        if failure_kind == 'connect_timeout':
            raise httpx.ConnectTimeout(private, request=request)
        if failure_kind == 'read_error':
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  stream=BrokenStream(request))
        if failure_kind == 'rejected':
            return httpx.Response(503, headers={'content-type': 'application/json',
                'x-private-header': private}, json={'error': {'message': private}})
        if failure_kind == 'private_media':
            return httpx.Response(200, headers={'content-type': 'application/' + private},
                                  content=private.encode())
        return httpx.Response(200, json={'output': [private]})

    if failure_kind == 'revoked':
        original_authorize, authorizations = run.proxy.authorize, []

        def revoke_after_admission(token):
            authorizations.append(True)
            if len(authorizations) == 2:
                raise ServiceError('model_grant_revoked', 401)
            return original_authorize(token)

        monkeypatch.setattr(run.proxy, 'authorize', revoke_after_admission)
    if failure_kind == 'persistence':
        original_begin, transactions = run.store.engine.begin, []

        def fail_response_persistence():
            transactions.append(True)
            if len(transactions) == 2:
                raise RuntimeError(private)
            return original_begin()

        monkeypatch.setattr(run.store.engine, 'begin', fail_response_persistence)
    run.proxy.client.close()
    run.proxy.client = httpx.Client(transport=httpx.MockTransport(upstream))
    try:
        with caplog.at_level(logging.WARNING, logger='agent_service.diagnostics'):
            error('model_admission_uncertain', lambda: run.proxy.respond(run.token, run.body), 502)
            # An uncertain admission never calls the upstream a second time.
            error('model_admission_uncertain', lambda: run.proxy.respond(run.token, run.body), 409)
        assert len(calls) == (0 if failure_kind == 'revoked' else 1)
        records = [record for record in caplog.records if record.name == 'agent_service.diagnostics']
        assert len(records) == 1
        assert json.loads(records[0].getMessage()) == dict(event='execution_failure',
            component='model_proxy', stage=stage, category=category, code=code, errno=None,
            upstream_status=status, upstream_media=media)
        assert records[0].exc_info is None and not records[0].stack_info
        for sensitive in (private, run.token, run.proxy.settings.upstream_token,
                          run.proxy.settings.upstream_base_url, run.envelope.attempt_id):
            assert sensitive not in records[0].getMessage()
        with run.store.engine.connect() as db:
            record = db.execute(select(requests)).mappings().one()
        assert record['state'] == 'admitted'
        assert record['status'] is None and record['response'] is None
    finally:
        run.proxy.client.close()


def test_successful_model_response_and_replay_emit_no_failure_warning(execution, caplog):
    run = execution()
    with caplog.at_level(logging.WARNING, logger='agent_service.diagnostics'):
        first = run.proxy.respond(run.token, run.body)
        assert run.proxy.respond(run.token, run.body) == first
    assert len(run.calls) == 1
    assert not [record for record in caplog.records if record.name == 'agent_service.diagnostics']


def observed_search():
    search = dict(source='crossref', query='controlled fixture', searched_at='2026-10-26T00:00:00Z',
        status='success', result_count=0, error=None)
    request = dict(tool='academic_search',
                   payload={'query': search['query'], 'providers': ['crossref'], 'limit': 1})
    response = dict(schema_version='1', searches=[search], papers=[], database_total=None, notes=[])
    return request, response


def test_real_tool_admission_is_replayed_and_bound_to_observed_search_bytes(execution, monkeypatch):
    run = execution(profile='research.generate.v1')
    request, result = observed_search()
    calls = []

    def actual_bridge(tool, payload, task):
        calls.append((tool, payload, task))
        return result

    monkeypatch.setattr('agent_service.profiles.research.academic_tool', actual_bridge)
    assert run.proxy.academic(run.token, request) == result
    assert run.proxy.academic(run.token, request) == result
    assert len(calls) == 1
    observed = run.proxy.academic_observations(run.token)
    assert observed['observations'][0]['call_sha256'] == digest(request)
    arguments = dict(attempt_id=run.envelope.attempt_id,
        request_sha256=run.envelope.request_sha256,
        source_policy={'providers': ['crossref'], 'max_results': 1})
    searches = canonical_bytes(result['searches'][0]) + b'\n'
    checked = validate_academic_observations(canonical_bytes(observed), searches, b'', **arguments)
    assert checked['search_calls'] == 1 and checked['scientific_accuracy_verified'] is False
    forged = deepcopy(result['searches'][0])
    forged['result_count'] = 123
    with pytest.raises(AcademicObservationError):
        validate_academic_observations(canonical_bytes(observed), canonical_bytes(forged) + b'\n', b'', **arguments)
    with pytest.raises(AcademicObservationError):
        validate_academic_observations(canonical_bytes(observed), searches, b'',
            **{**arguments, 'attempt_id': '00000000-0000-0000-0000-000000000001'})


def test_interrupted_tool_call_is_retained_and_forged_complete_ledger_is_rejected(execution, monkeypatch):
    run = execution(profile='research.generate.v1')
    request, result = observed_search()
    calls = []

    def fail(*args):
        calls.append(args)
        raise RuntimeError('Interrupted actual bridge')

    monkeypatch.setattr('agent_service.profiles.research.academic_tool', fail)
    with pytest.raises(RuntimeError):
        run.proxy.academic(run.token, request)
    error('tool_observation_uncertain', lambda: run.proxy.academic(run.token, request), 409)
    assert len(calls) == 1
    observed = run.proxy.academic_observations(run.token)
    assert observed['observations'][0]['state'] == 'admitted'
    with pytest.raises(AcademicObservationError):
        validate_academic_observations(canonical_bytes(observed),
            canonical_bytes(result['searches'][0]) + b'\n', b'',
            attempt_id=run.envelope.attempt_id, request_sha256=run.envelope.request_sha256,
            source_policy={'providers': ['crossref'], 'max_results': 1})


def test_tool_bridge_rejects_non_research_profile_without_calling_it(execution, monkeypatch):
    run = execution()
    monkeypatch.setattr('agent_service.profiles.research.academic_tool',
        lambda *args: pytest.fail('Workspace profile cannot access the research bridge'))
    error('research_tool_not_authorized', lambda: run.proxy.academic(run.token, observed_search()[0]), 403)
    with run.store.engine.connect() as db:
        assert not db.execute(select(observations)).first()


@pytest.mark.parametrize('raw', [
    b'{"model":"fixture-model","input":"ok","temperature":NaN}',
    b'{"model":"fixture-model","input":[{"type":"reasoning","summary":null}]}',
    b'{"model":"fixture-model","input":[{"role":[],"content":"bad role"}]}',
    b'{"model":"fixture-model","input":[{"type":"function_call_output","call_id":[],"output":"x"}]}',
    b'{"model":"fixture-model","input":[{"role":"user","content":[{"type":"input_image",'
        b'"image_url":"data:image/png;base64,YQ==","detail":[]}]}]}',
    b'{"model":"fixture-model","input":[{"role":"user","content":[{"type":[]}]}]}',
    b'{"model":"fixture-model","input":"ok","temperature":1e9999}',
    b'{"model":"fixture-model","input":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}',
])
def test_malformed_client_json_is_rejected_without_internal_errors_or_admission(execution, raw):
    run = execution()
    with TestClient(create_model_proxy_app(run.proxy), raise_server_exceptions=False) as client:
        response = client.post('/v1/responses', content=raw,
            headers={'authorization': 'Bearer ' + run.token, 'content-type': 'application/json'})
    assert response.status_code == 422
    assert set(response.json()) == {'error'}
    assert run.calls == []
    with run.store.engine.connect() as db:
        assert not db.execute(select(requests)).first()


@pytest.mark.parametrize('raw', [
    b'{"tool":[],"payload":{}}',
    b'{"tool":"academic_search","payload":{"query":NaN}}',
    b'{"tool":"academic_search","payload":{"query":1e9999}}',
])
def test_malformed_academic_request_cannot_crash_or_create_an_admission(execution, raw):
    run = execution(profile='research.generate.v1')
    with TestClient(create_model_proxy_app(run.proxy), raise_server_exceptions=False) as client:
        response = client.post('/v1/tools/academic', content=raw,
            headers={'authorization': 'Bearer ' + run.token, 'content-type': 'application/json'})
    assert response.status_code in {403, 422}
    assert set(response.json()) == {'error'}
    with run.store.engine.connect() as db:
        assert not db.execute(select(observations)).first()


def test_http_boundary_lists_only_granted_model_and_enforces_request_size(execution):
    run = execution(max_request_bytes=32)
    with TestClient(create_model_proxy_app(run.proxy)) as client:
        assert client.get('/v1/models').status_code == 401
        headers = {'authorization': 'Bearer ' + run.token}
        assert client.get('/v1/models', headers=headers).json()['data'] == [
            {'id': 'fixture-model', 'object': 'model'}]
        assert client.post('/v1/responses', headers=headers, content=b'{' * 33).status_code == 413
        assert client.post('/v1/responses', headers=headers, content=b'{invalid').status_code == 422
    assert run.calls == []
