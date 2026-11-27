"""Real manifest/job entrypoints, isolated SQLite and mock readiness; no model."""
from copy import deepcopy
import json
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy import acceptance_probe as probe
from agent_service.deploy.model_checked_candidate import prepare_checked_candidate, create_checked_candidate
from agent_service.model_route_readiness import (CONTRACT, READINESS_URL, candidate_model_route,
    check_fresh_model_route, picture_review_expectation, read_trusted_readiness)
from agent_service.store import jobs
from agent_service.worker_entry import HostConfig, host_policy
from test_candidate_scenario import write_scenario
from test_picture_review_v2_catalog import prepare
from test_picture_review_v2_profile import document, PNG

REVISION='fe4ce4894a71e7b9812f7264'


def ready(*,model='gpt-6.2-sol',revision=REVISION):
    return dict(status='ready',routes={'llm-trusted':dict(requested_alias='llm-trusted',
        configured_model=model,route_revision=revision,resolved_upstream='custom',tier='trusted',wire_api='responses')})


def reader(body=None,*,status=200,age=0,calls=None):
    def read():
        if calls is not None:calls.append('readiness')
        now=time.time()-age
        return dict(http_status=status,body=deepcopy(ready() if body is None else body),
            origin=READINESS_URL,started_at=now,observed_at=now)
    return read


@pytest.fixture
def staged(tmp_path):
    model=dict(revision='checked-route-policy',api_mode='openai-responses',provider='task-model',
        model='llm-trusted',capabilities=['text','tool_calls','vision'])
    _,hosts,_=prepare(review_model_policy=model)
    config=HostConfig.model_validate(next(iter(hosts.values())));value=document()
    path,source,_=write_scenario(tmp_path/'scenario',config.profile.name,
        dict(input_sha256=digest(value)),{'picture-learning-input.json':canonical_bytes(value),
            **{f['file_name']:PNG for f in value['observation']['frames']}})
    options=dict(name='checked-native',worker_image='registry.invalid/pi:latest@'+config.profile.image_digest,
        node='offline-node',bootstrap_secret='original-bootstrap',upstream_secret='original-upstream',
        scenario=path,source_dir=source)
    reservation=dict(name=options['name'],profile=config.profile.name,node=options['node'],
        reusable=False,uid_count=3,uid_start=2100000000)
    return config,options,reservation


@pytest.mark.parametrize('body,status',[(ready(model='other'),200),(ready(revision='changed'),200),
    (dict(status='ready',routes={'gpt-6.2-sol':ready()['routes']['llm-trusted']}),200),(ready(),503)])
def test_bad_actual_route_stops_before_uid_authority_or_manifest(staged,body,status):
    config,options,reservation=staged;events=[]
    def reserve():events.append('reserve_uid');return reservation
    with pytest.raises(ServiceError):prepare_checked_candidate(config,route_revision=REVISION,
        reserve_uid=reserve,reader=reader(body,status=status,calls=events),**options)
    assert events==['readiness']


def test_real_prepare_and_create_entrypoints_check_before_their_side_effects(staged):
    config,options,reservation=staged;events=[]
    def reserve():events.append('reserve_uid');return reservation
    prepared=prepare_checked_candidate(config,route_revision=REVISION,reserve_uid=reserve,
        reader=reader(calls=events),**options)
    assert events==['readiness','reserve_uid','readiness']
    manifest=prepared['manifest'];proxy=next(p for p in manifest['items'] if p['kind']=='Pod'
        and p['metadata']['namespace']=='agent-service')
    assert proxy['spec']['containers'][0]['args'][-4:]==[
        '--model-route-contract',CONTRACT,'--model-route-revision',REVISION]
    assert proxy['metadata']['annotations']['agent-service.panghuer.top/model-route-public-alias']=='llm-trusted'
    def create(value):events.append('create_pods');assert value==manifest;return {'created':True}
    result=create_checked_candidate(config,manifest,route_revision=REVISION,
        create_once=create,reader=reader(calls=events))
    assert events[-2:]==['readiness','create_pods'] and result['outcome']=={'created':True}
    assert prepared['before_uid_preflight']['configured_model']=='gpt-6.2-sol'


def test_changed_route_at_create_does_not_call_create_or_rewrite_alias(staged):
    config,options,reservation=staged
    prepared=prepare_checked_candidate(config,route_revision=REVISION,reserve_uid=lambda:reservation,
        reader=reader(),**options)
    calls=[]
    with pytest.raises(ServiceError,match='revision_mismatch'):
        create_checked_candidate(config,prepared['manifest'],route_revision=REVISION,
            create_once=lambda m:calls.append(m),reader=reader(ready(revision='new')))
    assert calls==[] and config.model.model=='llm-trusted'


@pytest.mark.parametrize('change',[
    lambda m:m['items'][-2]['spec']['containers'][0]['args'].__setitem__(-1,'other-revision'),
    lambda m:m['items'][-2]['metadata']['annotations'].__setitem__('agent-service.panghuer.top/model-route-configured-model','other'),
    lambda m:m['items'][0]['data'].__setitem__('config.json',m['items'][0]['data']['config.json'].replace('llm-trusted','gpt-6.2-sol')),
])
def test_manifest_tampering_is_rejected_before_create(staged,change):
    config,options,reservation=staged
    prepared=prepare_checked_candidate(config,route_revision=REVISION,reserve_uid=lambda:reservation,
        reader=reader(),**options)
    manifest=deepcopy(prepared['manifest']);change(manifest);calls=[]
    with pytest.raises((ServiceError,ValueError)):
        create_checked_candidate(config,manifest,route_revision=REVISION,
            create_once=lambda m:calls.append(m),reader=reader())
    assert calls==[]


def test_runtime_check_rejects_before_scenario_uid_check_private_store_or_grant(staged,tmp_path,monkeypatch):
    config,_,_=staged;events=[]
    class Scenario:
        document={'profile':config.profile.name}
        def reserve_uid(self,uid):events.append('scenario_uid_check')
    destination=tmp_path/'must-not-exist'
    with pytest.raises(ServiceError,match='target_mismatch'):
        probe.ProbeAuthority(config,destination,'not-used-upstream','not-used-bootstrap','native-worker','agent-workers',
            2100000000,scenario=Scenario(),model_route_contract=CONTRACT,model_route_revision=REVISION,
            model_route_reader=reader(ready(model='other'),calls=events))
    assert events==['readiness'] and not destination.exists()


def test_runtime_rechecks_fresh_route_then_creates_real_bounded_sqlite_job_without_model(staged,tmp_path):
    config,options,reservation=staged
    from agent_service.deploy.candidate_scenario import load_scenario
    scenario=load_scenario(options['scenario'],options['source_dir'],None,host_policy(config));events=[]
    authority=probe.ProbeAuthority(config,tmp_path/'private-probe','not-used-upstream','not-used-bootstrap',
        'native-worker','agent-workers',reservation['uid_start'],scenario=scenario,
        model_route_contract=CONTRACT,model_route_revision=REVISION,model_route_reader=reader(calls=events))
    try:
        assert events==['readiness'] and authority.calls()==[]
        with authority.store.engine.connect() as db:request=db.execute(select(jobs.c.request)).scalar_one()
        assert request['model_limits']==dict(max_requests=8,max_output_tokens=8192)
        assert authority.model_route_preflight['public_alias']=='llm-trusted'
        assert authority.model_route_preflight['route_revision']==REVISION
    finally:authority.proxy.client.close();authority.store.close()


def test_new_v2_cli_requires_explicit_contract_before_credentials_or_outputs(staged,tmp_path,monkeypatch):
    config,options,reservation=staged;path=tmp_path/'host.json';path.write_bytes(canonical_bytes(config.model_dump(mode='json')))
    calls=[];monkeypatch.setattr(probe,'credential',lambda p:calls.append(p))
    assert probe.main(['proxy','--config',str(path),'--worker-name','native-worker',
        '--uid-start',str(reservation['uid_start'])])==1
    assert calls==[]
    # Default legacy in-process APIs remain available for original frozen tests;
    # the new v2 CLI cannot silently choose that path.
    with pytest.raises(ServiceError,match='contract_required'):
        probe._require_new_model_route_contract(config,None,None)


@pytest.mark.parametrize('status',[200,302,503])
def test_trusted_transport_only_gets_fixed_endpoint_and_never_follows_redirect(status):
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(status,json=ready(),headers={'location':'https://must-not-follow.invalid'})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        observation=read_trusted_readiness(client=client)
    assert len(calls)==1 and calls[0].method=='GET' and str(calls[0].url)==READINESS_URL
    assert 'authorization' not in calls[0].headers and observation['http_status']==status
    if status!=200:
        with pytest.raises(ServiceError,match='not_ready'):
            check_fresh_model_route(picture_review_expectation(REVISION),reader=lambda:observation)


def test_saved_or_foreign_readiness_cannot_substitute_for_fresh_trusted_read(staged):
    config,_,_=staged
    with pytest.raises(ServiceError,match='not_fresh'):
        candidate_model_route(config,CONTRACT,REVISION,reader=reader(age=60))
    value=reader()();value['origin']='https://caller.invalid/health/ready'
    with pytest.raises(ServiceError,match='not_fresh'):
        candidate_model_route(config,CONTRACT,REVISION,reader=lambda:value)


def test_backend_model_mistaken_for_alias_stops_before_any_uid_or_read(staged):
    config,options,reservation=staged
    model=deepcopy(config.execution_catalog['execution_bindings'][0]['model_policy'])
    model['model']='gpt-6.2-sol'
    _,hosts,_=prepare(review_model_policy=model)
    altered=HostConfig.model_validate(next(iter(hosts.values())))
    events=[]
    with pytest.raises((ServiceError,ValueError)):
        prepare_checked_candidate(altered,route_revision=REVISION,
            reserve_uid=lambda:events.append('uid'),reader=reader(calls=events),**options)
    assert events==[]


def test_bounded_readiness_transport_rejects_oversize_and_never_retries():
    calls=[]
    def transport(request):
        calls.append(request);return httpx.Response(200,content=b' '*(1024*1024+1))
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ServiceError,match='too_large'):read_trusted_readiness(client=client)
    assert len(calls)==1


def test_readiness_transport_error_stops_without_a_retry():
    calls=[]
    def transport(request):
        calls.append(request);raise httpx.ConnectError('unreachable',request=request)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ServiceError,match='unavailable'):read_trusted_readiness(client=client)
    assert len(calls)==1


def test_frozen_full_preflight_proof_must_match_manifest_digest(staged):
    config,options,reservation=staged
    value=prepare_checked_candidate(config,route_revision=REVISION,reserve_uid=lambda:reservation,
        reader=reader(),**options)
    manifest=deepcopy(value['manifest'])
    pod=next(row for row in manifest['items'] if row['kind']=='Pod')
    marks=pod['metadata']['annotations'];proof=json.loads(marks['agent-service.panghuer.top/model-route-preflight'])
    proof['observed_at']+=1;marks['agent-service.panghuer.top/model-route-preflight']=json.dumps(proof)
    calls=[]
    with pytest.raises(ServiceError,match='manifest_invalid'):
        create_checked_candidate(config,manifest,route_revision=REVISION,
            create_once=lambda m:calls.append(m),reader=reader())
    assert calls==[]
