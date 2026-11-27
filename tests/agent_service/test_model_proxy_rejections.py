"""Real durable rejection evidence stays uncertain; all upstreams are doubles."""
import hashlib
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_service.contracts import ServiceError
from agent_service.model_proxy import (
    MAX_REJECTION_BODY_BYTES, ModelProxy, create_model_proxy_app, request_limits,
    requests, usage_evidence_for_job,
)
from agent_service.store import jobs
from test_model_proxy import execution
from test_job_model_limits import install_limits


def reject_with(run, body, *, status=400, media='application/json', stream=None):
    calls=[]
    def upstream(request):
        calls.append(request)
        return httpx.Response(status, headers={'content-type': media},
            **({'content': body} if stream is None else {'stream': stream}))
    run.proxy.client.close()
    run.proxy.client=httpx.Client(transport=httpx.MockTransport(upstream))
    return calls


def row_for(run):
    with run.store.engine.connect() as db:
        return dict(db.execute(select(requests)).mappings().one())


def test_real_400_body_is_retained_without_success_usage_replay_or_secret_logs(execution, caplog):
    run=execution();install_limits(run,max_requests=8)
    raw=b'{"error":{"code":"unknown_alias","message":"PRIVATE_REJECTION_BODY"}}'
    calls=reject_with(run,raw)
    with caplog.at_level(logging.WARNING,logger='agent_service.diagnostics'):
        with TestClient(create_model_proxy_app(run.proxy)) as client:
            response=client.post('/v1/responses',headers={'Authorization':'Bearer '+run.token},json=run.body)
            assert response.status_code==502 and response.json()=={'error':{'code':'model_admission_uncertain'}}
            assert 'PRIVATE_REJECTION_BODY' not in response.text
    assert 'PRIVATE_REJECTION_BODY' not in caplog.text
    assert run.proxy.settings.upstream_token not in caplog.text
    row=row_for(run)
    assert row['state']=='admitted' and row['status']==400
    assert row['media_type']=='application/json' and row['response']==raw
    with run.store.engine.connect() as db:
        job=dict(db.execute(select(jobs)).mappings().one())
        assert db.execute(select(request_limits)).mappings().one()['max_requests']==8
    evidence=usage_evidence_for_job(run.store,job)
    assert evidence['requests'][0]['status']==400
    assert evidence['requests'][0]['response_sha256']==hashlib.sha256(raw).hexdigest()
    assert evidence['requests'][0]['usage_complete'] is False
    assert evidence['completeness']!='complete'
    # Same-body replay and a fresh body remain refused after a proxy restart.
    restarted=ModelProxy(run.store,run.proxy.settings,
        client=httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('No new paid call'))))
    try:
        for body in (run.body,dict(run.body,input='a different body')):
            with pytest.raises(ServiceError,match='model_admission_uncertain'):
                restarted.respond(run.token,body)
    finally:restarted.client.close()
    assert len(calls)==1


@pytest.mark.parametrize('size',[0,MAX_REJECTION_BODY_BYTES])
def test_complete_empty_and_exact_limit_bodies_are_retained(execution,size):
    run=execution();raw=b'x'*size;reject_with(run,raw,status=503,media='text/plain')
    with pytest.raises(ServiceError,match='model_admission_uncertain'):run.proxy.respond(run.token,run.body)
    row=row_for(run)
    assert row['state']=='admitted' and row['status']==503 and row['response']==raw
    with run.store.engine.connect() as db:job=dict(db.execute(select(jobs)).mappings().one())
    assert usage_evidence_for_job(run.store,job)['requests'][0]['response_sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('configured_limit,body_size',[(32,33),(2*MAX_REJECTION_BODY_BYTES,MAX_REJECTION_BODY_BYTES+1)])
def test_oversize_rejection_never_persists_a_prefix(execution,configured_limit,body_size):
    run=execution(max_response_bytes=configured_limit)
    reject_with(run,b'x'*body_size)
    with pytest.raises(ServiceError,match='model_admission_uncertain'):run.proxy.respond(run.token,run.body)
    row=row_for(run)
    assert row['state']=='admitted' and row['status']==400 and row['media_type']=='application/json'
    assert row['response'] is None
    with run.store.engine.connect() as db:job=dict(db.execute(select(jobs)).mappings().one())
    assert usage_evidence_for_job(run.store,job)['requests'][0]['response_sha256'] is None


def test_interrupted_error_body_keeps_http_metadata_but_not_partial_bytes(execution):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b'private incomplete body'
            raise httpx.ReadError('private failed stream')
    run=execution();reject_with(run,None,stream=Broken())
    with pytest.raises(ServiceError,match='model_admission_uncertain'):run.proxy.respond(run.token,run.body)
    row=row_for(run)
    assert row['status']==400 and row['state']=='admitted' and row['response'] is None


def test_grant_expiry_during_error_capture_cannot_turn_prefix_into_complete_evidence(execution,monkeypatch):
    run=execution();reject_with(run,b'complete bytes received after revocation')
    original=run.proxy.authorize;count=[]
    def revoke(token):
        count.append(True)
        if len(count)>=3:raise ServiceError('model_grant_revoked',401)
        return original(token)
    monkeypatch.setattr(run.proxy,'authorize',revoke)
    with pytest.raises(ServiceError,match='model_admission_uncertain'):run.proxy.respond(run.token,run.body)
    row=row_for(run)
    assert row['status']==400 and row['state']=='admitted' and row['response'] is None


def test_unbounded_untrusted_content_type_is_not_persisted_or_logged(execution,caplog):
    run=execution();media='application/'+('PRIVATE_MEDIA_'*10)
    reject_with(run,b'{}',media=media)
    with caplog.at_level(logging.WARNING,logger='agent_service.diagnostics'):
        with pytest.raises(ServiceError,match='model_admission_uncertain'):run.proxy.respond(run.token,run.body)
    assert row_for(run)['media_type'] is None
    assert 'PRIVATE_MEDIA_' not in caplog.text


def test_error_payload_resembling_success_usage_still_does_not_close_usage(execution):
    run=execution();install_limits(run,max_requests=8)
    raw=json.dumps(dict(object='response',status='completed',usage=dict(input_tokens=0,output_tokens=0,total_tokens=0))).encode()
    reject_with(run,raw,status=429)
    with pytest.raises(ServiceError,match='model_admission_uncertain'):run.proxy.respond(run.token,run.body)
    with run.store.engine.connect() as db:job=dict(db.execute(select(jobs)).mappings().one())
    evidence=usage_evidence_for_job(run.store,job)
    assert evidence['requests'][0]['usage_complete'] is False
    assert evidence['requests'][0].get('total_tokens') is None
    with pytest.raises(ServiceError,match='model_admission_uncertain'):
        run.proxy.respond(run.token,dict(run.body,input='must remain blocked'))
