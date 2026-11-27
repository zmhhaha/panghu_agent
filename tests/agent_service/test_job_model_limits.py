"""Actual proxy admission against the persistent execution DB; no paid calls."""
from copy import deepcopy
import json
from uuid import uuid4

import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import delete, func, insert, select, update

from agent_service.contracts import JobModelLimits, JobRequest, ServiceError
from agent_service.model_proxy import ModelProxy, request_limits, requests, usage_evidence_for_job
from agent_service.store import attempts, jobs
from test_model_proxy import execution
from test_api import setup as api_setup


def install_limits(run, max_requests=2, max_output_tokens=8192):
    # Test fixture control data. Production callers can only submit this once
    # through the validated immutable JobRequest, not update a running job.
    with run.store.engine.begin() as db:
        row = db.execute(select(jobs)).mappings().one()
        request = dict(row['request'], model_limits=dict(max_requests=max_requests,
            max_output_tokens=max_output_tokens))
        db.execute(update(jobs).where(jobs.c.id == row['id']).values(request=request))
    return request


def test_optional_limits_preserve_the_original_unmarked_job_wire():
    original = dict(profile='workspace-task.v1', profile_revision='v1',
        business_ref='test', task={'prompt': 'test'})
    legacy = JobRequest.model_validate(original)
    assert 'model_limits' not in legacy.model_dump(mode='json')
    assert 'model_limits' not in json.loads(legacy.model_dump_json())
    bounded = JobRequest.model_validate(dict(original,
        model_limits={'max_requests': 8, 'max_output_tokens': 8192}))
    assert bounded.model_dump(mode='json')['model_limits'] == {
        'max_requests': 8, 'max_output_tokens': 8192}
    assert bounded.model_copy(update={'model_limits': None}).model_dump(mode='json') == legacy.model_dump(mode='json')


@pytest.mark.parametrize('value', [None, {}, {'max_requests': True, 'max_output_tokens': 8192},
    {'max_requests': 8.0, 'max_output_tokens': 8192}, {'max_requests': 0, 'max_output_tokens': 8192},
    {'max_requests': 8, 'max_output_tokens': 0}, {'max_requests': 8, 'max_output_tokens': 8192, 'ignore': True}])
def test_limits_are_strict_positive_integers_without_extra_policy_fields(value):
    with pytest.raises(ValidationError):
        JobModelLimits.model_validate(value)


def test_actual_forwarded_output_is_clamped_and_equivalent_inputs_replay(execution):
    run = execution()
    install_limits(run)
    first = run.proxy.respond(run.token, dict(run.body, max_output_tokens=32768))
    assert json.loads(run.calls[0].content)['max_output_tokens'] == 8192
    assert run.proxy.respond(run.token, dict(run.body, max_output_tokens=8192)) == first
    assert run.proxy.respond(run.token, run.body) == first
    assert len(run.calls) == 1
    with run.store.engine.connect() as db:
        assert [row.max_output_tokens for row in db.execute(select(request_limits))] == [8192]
    run.proxy.respond(run.token, dict(run.body, input='smaller', max_output_tokens=4096))
    assert json.loads(run.calls[-1].content)['max_output_tokens'] == 4096


def test_per_job_calls_stop_before_contact_and_replay_does_not_spend(execution):
    run = execution()
    install_limits(run)
    first = run.proxy.respond(run.token, run.body)
    run.proxy.respond(run.token, dict(run.body, input='second'))
    with pytest.raises(ServiceError, match='model_job_budget_exhausted'):
        run.proxy.respond(run.token, dict(run.body, input='third'))
    assert run.proxy.respond(run.token, run.body) == first and len(run.calls) == 2
    with run.store.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(requests)).scalar_one() == 2


def test_uncertain_admission_retains_its_job_slot_across_proxy_restart(execution):
    run = execution()
    install_limits(run, max_requests=1)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _: (_ for _ in ()).throw(httpx.ReadError('offline uncertain'))))
    failed = ModelProxy(run.store, run.proxy.settings, client=client)
    try:
        with pytest.raises(ServiceError, match='model_admission_uncertain'):
            failed.respond(run.token, run.body)
    finally:
        client.close()
    restarted = ModelProxy(run.store, run.proxy.settings, client=run.proxy.client)
    with pytest.raises(ServiceError, match='model_admission_uncertain'):
        restarted.respond(run.token, run.body)
    with pytest.raises(ServiceError, match='model_job_budget_exhausted'):
        restarted.respond(run.token, dict(run.body, input='new identity'))
    assert run.calls == []


def test_prior_attempt_admissions_count_toward_the_same_job_cap(execution):
    run = execution()
    install_limits(run, max_requests=1)
    with run.store.engine.begin() as db:
        original = dict(db.execute(select(attempts)).mappings().one())
        older = dict(original, id=str(uuid4()), fence=original['fence'] - 1, state='failed')
        db.execute(insert(attempts).values(**older))
        db.execute(insert(requests).values(attempt_id=older['id'], body_sha256='a' * 64,
            state='admitted', created_at=original['created_at']))
    with pytest.raises(ServiceError, match='model_job_budget_exhausted'):
        run.proxy.respond(run.token, run.body)
    assert run.calls == []


def test_per_job_limits_cannot_raise_global_or_profile_limits(execution):
    run = execution(max_calls_per_attempt=1, max_output_tokens=2048)
    install_limits(run, max_requests=8, max_output_tokens=8192)
    run.proxy.respond(run.token, run.body)
    assert json.loads(run.calls[0].content)['max_output_tokens'] == 2048
    with pytest.raises(ServiceError, match='model_budget_exhausted'):
        run.proxy.respond(run.token, dict(run.body, input='second'))


def test_persisted_invalid_limit_fails_closed_before_upstream(execution):
    run = execution()
    request = install_limits(run)
    request['model_limits']['max_requests'] = True
    with run.store.engine.begin() as db:
        db.execute(update(jobs).values(request=request))
    with pytest.raises(ServiceError, match='model_job_limits_invalid'):
        run.proxy.respond(run.token, run.body)
    assert run.calls == []


def test_api_does_not_offer_or_accept_model_limits_for_an_unscoped_adapter(api_setup):
    from test_api import headers, request_body
    client, store = api_setup
    item = client.get('/v1/capabilities', headers=headers()).json()['profiles'][0]
    assert 'model_request_limits' not in item
    response = client.post('/v1/jobs', headers=headers(), json=request_body(
        model_limits={'max_requests': 8, 'max_output_tokens': 8192}))
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'model_job_limits_not_supported'
    assert store.claim() is None


def test_api_freezes_and_projects_limits_for_scoped_execution_without_task_echo(api_setup):
    from fastapi.testclient import TestClient
    from agent_service.api import create_app
    from agent_service.contracts import ProfileRegistry
    from test_api import PROFILE, Ready, headers, request_body, settings
    _, store = api_setup
    class ScopedReady(Ready):
        provisioning = 'kubernetes_per_attempt'
    with TestClient(create_app(settings(), store, ProfileRegistry([PROFILE]), {'pi': ScopedReady()})) as client:
        capability = client.get('/v1/capabilities', headers=headers()).json()['profiles'][0]
        assert capability['model_request_limits'] == {
            'schema': 'agent-job-model-limits.v1',
            'max_requests_scope': 'job_all_attempts_including_uncertain_admissions',
            'max_output_tokens_scope': 'each_forwarded_provider_request',
            'total_input_output_token_hard_cap': False}
        limits = {'max_requests': 8, 'max_output_tokens': 8192}
        body = request_body(model_limits=limits)
        response = client.post('/v1/jobs', json=body, headers=headers())
        assert response.status_code == 202
        value = response.json()
        assert value['model_limits'] == limits and 'Private task source' not in response.text
        assert client.post('/v1/jobs', json=body, headers=headers()).json() == value
        readback = client.get('/v1/jobs/' + value['job_id'], headers=headers()).json()
        assert readback['model_limits'] == limits
        body['model_limits']['max_requests'] = 9
        assert client.post('/v1/jobs', json=body, headers=headers()).status_code == 409


@pytest.mark.parametrize('output_tokens, verified', [(3, True), (9000, False)])
def test_stopped_new_job_proves_actual_durable_limits_without_trusting_worker_usage(execution, output_tokens, verified):
    from test_model_proxy import _supplier_response, _usage_stopped
    run = execution()
    limits = install_limits(run)['model_limits']
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json=_supplier_response(output_tokens=output_tokens))))
    try:
        proxy = ModelProxy(run.store, run.proxy.settings, client=client)
        proxy.respond(run.token, run.body)
    finally:
        client.close()
    job = _usage_stopped(run)
    proof = usage_evidence_for_job(run.store, job)
    assert proof['completeness'] == 'complete' and proof['model_limits_verified'] is verified
    assert proof['model_limits'] == proof['requests'][0]['enforced_model_limits'] == limits
    original = deepcopy(proof)
    assert usage_evidence_for_job(run.store, job) == original
    with run.store.engine.begin() as db:
        db.execute(delete(request_limits))
    damaged = usage_evidence_for_job(run.store, job)
    assert damaged['model_limits_verified'] is False
    assert damaged['requests'][0]['enforced_model_limits'] is None
