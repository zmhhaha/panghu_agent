"""Actual local probe admission/SQLite/grant execution, with mock upstream only."""
from copy import deepcopy
import base64
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import delete, func, select

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy import acceptance_probe as probe, candidate_scenario
from agent_service.model_proxy import ModelProxy, requests, request_limits, profile_model_call_limit
from agent_service.profiles import video_picture_learning as old
from agent_service.profiles import video_picture_review_v2 as new
from agent_service.store import Store, jobs
from agent_service.worker_entry import CapsuleScope, HostConfig, HostSecrets, host_policy
from test_candidate_scenario import write_scenario, synthetic_source_root_owner
from test_picture_review_v2_catalog import prepare
from test_picture_learning_catalog import prepare as prepare_old
from test_picture_review_v2_profile import document, PNG


CONNECTIVITY = 'Return the exact text CANDIDATE_MODEL_OK as plain text. This is a connectivity probe.'


@pytest.fixture
def candidate(tmp_path):
    values=[]
    def make(*, legacy=False, usage=True, fail_transport=False, reported_output=1):
        _,hosts,_=(prepare_old() if legacy else prepare())
        name=old.REVIEW_PROFILE if legacy else new.PROFILE
        config=HostConfig.model_validate(hosts[name]);value=document()
        path,source,_=write_scenario(tmp_path/('scenario-'+str(len(values))),name,
            dict(input_sha256=digest(value)),{new.INPUT_NAME:canonical_bytes(value),
                **{f['file_name']:PNG for f in value['observation']['frames']}})
        scenario=candidate_scenario.load_scenario(path,source,None,host_policy(config))
        authority=probe.ProbeAuthority(config,tmp_path/('probe-'+str(len(values))),
            'offline-upstream-token','offline-bootstrap-token-'+('a'*32),'candidate-worker','agent-workers',
            2100000000,scenario=scenario)
        calls=[]
        def upstream(request):
            calls.append(request)
            if fail_transport:raise httpx.ReadError('Synthetic ambiguous supplier response',request=request)
            result=dict(object='response',id='mock-response-'+str(len(calls)),status='completed',output=[dict(type='message',role='assistant',
                content=[dict(type='output_text',text='CANDIDATE_MODEL_OK')])])
            if usage:result['usage']=dict(input_tokens=10,output_tokens=reported_output,total_tokens=10+reported_output)
            return httpx.Response(200,json=result)
        authority.proxy.client.close()
        authority.proxy.client=httpx.Client(transport=httpx.MockTransport(upstream))
        package=authority.register(str(uuid4()))
        item=SimpleNamespace(authority=authority,calls=calls,package=package,
            body=dict(model=config.model.model,store=False,input=CONNECTIVITY,max_output_tokens=2048))
        values.append(item)
        return item
    yield make
    for value in values:
        value.authority.proxy.client.close();value.authority.store.close()


def test_cold_probe_persists_real_job_limits_and_connectivity_consumes_the_same_eight_slots(candidate):
    run=candidate();authority=run.authority
    assert authority.proxy.settings.max_calls_per_attempt==8 and authority.proxy.settings.max_output_tokens==8192
    with authority.store.engine.connect() as db:
        stored=db.execute(select(jobs.c.request)).scalar_one()
    assert stored['model_limits']==dict(max_requests=8,max_output_tokens=8192)
    assert authority.proxy.authorize(authority.grant)['model_limits']==stored['model_limits']
    headers={'Authorization':'Bearer '+run.package['secrets']['model_proxy_token']}
    with TestClient(probe.proxy_app(authority)) as client:
        connection=client.post('/v1/responses',headers=headers,json=run.body)
        assert probe.response_completed(connection)
        for i in range(7):
            body=dict(model=authority.config.model.model,store=False,input='Review turn '+str(i))
            assert client.post('/v1/responses',headers=headers,json=body).status_code==200
        assert client.post('/v1/responses',headers=headers,json=dict(run.body,input='ninth paid request')).status_code==429
        assert client.post('/v1/responses',headers=headers,json=run.body).content==connection.content
    assert len(run.calls)==8 and json.loads(run.calls[0].content)['max_output_tokens']==2048
    assert all(json.loads(r.content)['max_output_tokens']==8192 for r in run.calls[1:])
    with authority.store.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(requests)).scalar_one()==8
        limits=db.execute(select(request_limits)).mappings().all()
    assert len(limits)==8 and all(row['max_requests']==8 for row in limits)
    assert sorted(row['max_output_tokens'] for row in limits)==[2048]+[8192]*7


def test_eighth_request_and_grant_limits_survive_new_store_and_proxy_instances(candidate):
    run=candidate();authority=run.authority
    for i in range(8):authority.proxy.respond(authority.grant,dict(run.body,input='turn '+str(i)))
    # Reopen the actual private on-disk SQLite ledger, with the same signed grant.
    restarted_store=Store(str(authority.store.engine.url),allow_sqlite=True)
    try:
        restarted=ModelProxy(restarted_store,authority.proxy.settings,client=authority.proxy.client)
        with pytest.raises(ServiceError,match='model_(job_)?budget_exhausted'):
            restarted.respond(authority.grant,dict(run.body,input='new after restart'))
        assert restarted.authorize(authority.grant)['model_limits']==dict(max_requests=8,max_output_tokens=8192)
        assert restarted.respond(authority.grant,dict(run.body,input='turn 0'))[0]==200
    finally:restarted_store.close()
    assert len(run.calls)==8


def test_candidate_proxy_rejects_overlarge_requested_output_before_spending(candidate):
    run=candidate();authority=run.authority
    with pytest.raises(ServiceError,match='model_output_bound_invalid'):
        authority.proxy.respond(authority.grant,dict(run.body,max_output_tokens=32768))
    assert run.calls==[]
    authority.proxy.respond(authority.grant,dict(model=authority.config.model.model,input='default bound'))
    assert json.loads(run.calls[0].content)['max_output_tokens']==8192
    with authority.store.engine.connect() as db:
        assert db.execute(select(request_limits.c.max_output_tokens)).scalar_one()==8192


@pytest.mark.parametrize('failure',['missing_usage','uncertain_transport','supplier_overrun'])
def test_connectivity_unknown_or_supplier_violation_stops_every_following_body(candidate,failure):
    run=candidate(usage=failure!='missing_usage',fail_transport=failure=='uncertain_transport',
        reported_output=3000 if failure=='supplier_overrun' else 1)
    authority=run.authority
    if failure=='uncertain_transport':
        with pytest.raises(ServiceError,match='model_admission_uncertain'):
            authority.proxy.respond(authority.grant,run.body)
    else:authority.proxy.respond(authority.grant,run.body)
    expected='model_job_output_limit_exceeded' if failure=='supplier_overrun' else 'model_admission_uncertain'
    with pytest.raises(ServiceError,match=expected):
        authority.proxy.respond(authority.grant,dict(run.body,input='first Agent turn after connection'))
    assert len(run.calls)==1
    with authority.store.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(requests)).scalar_one()==1
        assert db.execute(select(request_limits.c.max_output_tokens)).scalar_one()==2048


def test_v1_candidate_retains_old_proxy_and_unmarked_wire_defaults(candidate):
    run=candidate(legacy=True);authority=run.authority
    assert authority.proxy.settings.max_calls_per_attempt==40 and authority.proxy.settings.max_output_tokens==32768
    with authority.store.engine.connect() as db:request=db.execute(select(jobs.c.request)).scalar_one()
    assert 'model_limits' not in request
    assert profile_model_call_limit(new.PROFILE,40)==8
    assert profile_model_call_limit(new.PROFILE,3)==3
    assert profile_model_call_limit('workspace-task.v1',40)==40


def test_budget_receipt_exports_exact_supplier_bytes_and_persisted_admission_bounds(candidate):
    run=candidate();authority=run.authority
    authority.proxy.respond(authority.grant,run.body)
    authority.proxy.respond(authority.grant,dict(run.body,input='Actual Agent turn',max_output_tokens=8192))
    evidence=authority.model_budget_evidence()
    assert evidence['sha256']==digest({k:v for k,v in evidence.items() if k!='sha256'})
    assert evidence['request_count']==2 and evidence['usage_completeness']=='complete'
    assert evidence['physical_stop_scope']=='external_operator_required' and evidence['cost_status']=='unknown'
    assert evidence['grant_sha256']==hashlib.sha256(authority.grant.encode()).hexdigest()
    assert evidence['job_request_sha256']==digest(evidence['job_request'])
    assert evidence['job_request']['model_limits']==dict(max_requests=8,max_output_tokens=8192)
    assert authority.grant not in json.dumps(evidence)
    assert evidence['totals']==dict(input_tokens=20,output_tokens=2,total_tokens=22,
        cache_read_tokens=None,cache_write_tokens=None)
    for row in evidence['requests']:
        raw=base64.b64decode(row['response_base64'],validate=True)
        assert len(raw)==row['response_bytes'] and hashlib.sha256(raw).hexdigest()==row['response_sha256']
        assert row['execution_request_sha256']==authority.envelope.request_sha256
        assert row['attempt_id']==authority.envelope.attempt_id and row['fence']==authority.envelope.fence
        assert row['usage']['total_tokens']==json.loads(raw)['usage']['total_tokens']
        assert row['enforced_model_limits']['max_requests']==8
    assert evidence['requests'][0]['body_sha256']==digest(json.loads(run.calls[0].content))
    assert evidence['requests'][0]['enforced_model_limits']['max_output_tokens']==2048


@pytest.mark.parametrize('failure',['missing_usage','missing_limit_row','uncertain_transport'])
def test_incomplete_real_ledger_remains_partial_and_stops_following_requests(candidate,failure):
    run=candidate(usage=failure!='missing_usage',fail_transport=failure=='uncertain_transport')
    authority=run.authority
    if failure=='uncertain_transport':
        with pytest.raises(ServiceError):authority.proxy.respond(authority.grant,run.body)
    else:authority.proxy.respond(authority.grant,run.body)
    if failure=='missing_limit_row':
        with authority.store.engine.begin() as db:db.execute(delete(request_limits))
    evidence=authority.model_budget_evidence()
    assert evidence['usage_completeness']=='partial' and evidence['request_count']==1
    if failure=='missing_usage':assert evidence['totals']['total_tokens'] is None
    if failure=='missing_limit_row':assert evidence['requests'][0]['enforced_model_limits'] is None
    with pytest.raises(ServiceError,match='model_admission_uncertain'):
        authority.proxy.respond(authority.grant,dict(run.body,input='must not be paid'))
    assert len(run.calls)==1


def test_candidate_factory_uses_the_exact_original_image_extension_for_new_profile(monkeypatch,tmp_path):
    from agent_service import resources, runtime
    from agent_service.picture_learning_tools import verified_extensions
    _,hosts,_=prepare();config=HostConfig.model_validate(hosts[new.PROFILE]);policy=host_policy(config)
    scope=CapsuleScope(attempt_id=str(uuid4()),pod_uid=str(uuid4()),request_sha256='b'*64,
        agent_uid=1000001,preview_uid=1000002,execution_binding_sha256=policy.execution_binding.sha256)
    credentials=HostSecrets(worker_token='synthetic-worker-token-0000000000000',receipt_key_hex='c'*64,
        model_proxy_token='synthetic-proxy-token-00000000000000',model_proxy_attempt_id=scope.attempt_id,
        model_proxy_request_sha256='b'*64,model_proxy_expires_at=3600.)
    monkeypatch.setattr(resources,'measured_package',lambda *args:(new.BUNDLE_ROOT,
        new.BUNDLE_ROOT/'manifest.json',new.BUNDLE_MANIFEST_SHA256,(),()))
    captures=[]
    class Runtime:
        def __init__(self,settings,*,profile):captures.append((settings,profile))
        def _verify_resources(self):pass
        def _readiness_versions(self):return '0.99.2','24.9.0'
        def _source_provenance(self):return {'synthetic_test_only':True}
        def health(self):return dict(ready=False,fixture_mode=False)
    monkeypatch.setattr(runtime,'PiRuntime',Runtime)
    flags={key:True for key in ['detached_writers_stopped','read_only_mount_measured','control_unreadable',
        'input_write_denied','caps_empty','no_new_privileges','arbitrary_mount_denied','private_kernel_scope']}
    observations=dict(model_round_trip=dict(status=200),isolation=flags)
    driver=probe.build_candidate_driver(config,credentials,scope,SimpleNamespace(consumed=False),observations)
    assert driver.ready is False and len(captures)==2
    assert captures[-1][1].extension_paths==verified_extensions()
    assert captures[-1][0].model_max_tokens==8192
