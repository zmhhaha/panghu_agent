"""Synthetic custody mutations test verification; none attest a live candidate."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from agent_service import worker_entry
from agent_service.contracts import ExecutionEnvelope,JobRequest,ServiceError,canonical_bytes,digest
from agent_service.deploy import picture_review_v2_acceptance as admission
from agent_service.profiles import video_picture_review_v2 as protocol
from test_picture_review_v2_catalog import prepare
from test_picture_review_v2_profile import document,result,PNG
from test_candidate_scenario import synthetic_source_root_owner
from test_picture_review_v2_probe import candidate
import test_animation_method_acceptance as fixture


def capture(tmp_path,monkeypatch):
    original_writer=fixture.write_scenario
    def projected(directory,profile,task,files):
        value=json.loads(files[protocol.INPUT_NAME]);initial=files[protocol.INPUT_NAME]
        files.clear();files[protocol.INPUT_NAME]=initial
        files.update({frame['file_name']:PNG for frame in value['observation']['frames']})
        return original_writer(directory,profile,task,files)
    monkeypatch.setattr(fixture,'method',protocol)
    monkeypatch.setattr(fixture,'prepare',prepare)
    monkeypatch.setattr(fixture,'document',lambda review:document())
    monkeypatch.setattr(fixture,'output',result)
    monkeypatch.setattr(fixture,'write_scenario',projected)
    directory,root,context,artifact=fixture.capture(tmp_path,review=True)
    envelope=ExecutionEnvelope.model_validate(fixture.read(root/'candidate-request.json')['envelope'])
    record=fixture.read(root/'candidate-evidence.json')
    observation=record['observations']
    observation['capability']['allowed_tools']=['read_inputs','skill_search','skill_read','write']
    requests=[]
    for index in (1,2):
        provider=canonical_bytes(dict(object='response',id='resp-synthetic-'+str(index),status='completed',
            usage=dict(input_tokens=10,output_tokens=2,total_tokens=12),model='synthetic-model'))
        requests.append(dict(attempt_id=envelope.attempt_id,fence=envelope.fence,
            execution_request_sha256=envelope.request_sha256,body_sha256=str(index)*64,state='complete',status=200,
            response_sha256=hashlib.sha256(provider).hexdigest(),response_media_type='application/json',
            response_bytes=len(provider),response_base64=base64.b64encode(provider).decode(),
            enforced_model_limits=dict(max_requests=8,max_output_tokens=2048 if index==1 else 8192),usage=dict(input_tokens=10,output_tokens=2,
                total_tokens=12,cache_read_tokens=None,cache_write_tokens=None,model='synthetic-model')))
    observation['model_round_trip']['response_sha256']=requests[0]['response_sha256']
    job_request=JobRequest(profile=envelope.profile,profile_revision=envelope.profile_revision,
        business_ref='candidate-only:synthetic-test',task=envelope.task,timeout_seconds=600,
        inputs=[dict(input_id=str(uuid4()),sha256=item.sha256) for item in envelope.inputs],
        model_limits=deepcopy(admission.MODEL_LIMITS)).model_dump(mode='json')
    budget=dict(schema='agent-candidate-model-budget.v1',job_id=envelope.job_id,attempt_id=envelope.attempt_id,
        fence=envelope.fence,execution_request_sha256=envelope.request_sha256,
        execution_binding_sha256=envelope.execution_binding.sha256,grant_sha256='9'*64,
        job_request=job_request,job_request_sha256=digest(job_request),
        model_limits=deepcopy(admission.MODEL_LIMITS),proxy_limits=dict(max_calls_per_attempt=8,max_output_tokens=8192),
        request_count=2,requests=requests,usage_completeness='complete',
        totals=dict(input_tokens=20,output_tokens=4,total_tokens=24,cache_read_tokens=None,cache_write_tokens=None),
        physical_stop_scope='external_operator_required',cost_status='unknown')
    budget['sha256']=digest(budget);record['model_budget_evidence']=budget
    record['model_calls']=[{key:row[key] for key in ('body_sha256','response_sha256','state','status')} for row in requests]
    record['model_image_evidence']['images']=[dict(sha256=hashlib.sha256(PNG).hexdigest(),
        model_request_completed=True,model_request_sha256=requests[1]['body_sha256'],media_type='image/png',
        byte_length=len(PNG),input_item_index=index+1,part_index=0)
        for index,frame in enumerate(context['observation']['frames'])]
    fixture.save(root/'candidate-evidence.json',record)
    fixture.edit(root,'candidate-result.json',lambda value:value.update(observations=observation))
    fixture.edit(root,'operator-summary.json',lambda value:value.update(model_calls=2))
    return directory,root,context,artifact


def test_new_v2_capture_is_checked_from_raw_bytes_and_only_emits_disabled_host(tmp_path,monkeypatch):
    directory,root,_,_=capture(tmp_path,monkeypatch)
    before={path.name:path.read_bytes() for path in root.iterdir()}
    _,report=admission.assess_candidate(directory)
    assert report['schema']=='video-picture-review-v2-independent-review.v1'
    assert report['profile']==protocol.PROFILE and report['model_budget']['provider_usage_complete'] is True
    assert report['model_budget']['request_count']==2
    assert report['model_budget']['totals']['total_tokens']==24
    assert report['model_budget']['totals']['cache_read_tokens'] is None
    assert report['candidate_external_stop_verified'] is True
    assert report['production_control_usage_closure_claimed'] is False
    assert report['technical_host_admission_only'] is True and report['model_requests']==0
    monkeypatch.setattr(worker_entry,'_read_operator_json',lambda path,**kwargs:fixture.read(path))
    summary=admission.review_candidate(directory,tmp_path/'disabled-admission')
    assert summary['accepted_host'] is True and summary['enabled'] is False
    assert summary['deployment_performed'] is False and summary['model_requests']==0
    assert {path.name:path.read_bytes() for path in root.iterdir()}==before


def mutate_budget(root,change,*,reseal=True):
    value=fixture.read(root/'candidate-evidence.json');budget=value['model_budget_evidence']
    change(budget)
    if reseal:budget['sha256']=digest({k:v for k,v in budget.items() if k!='sha256'})
    fixture.save(root/'candidate-evidence.json',value)


@pytest.mark.parametrize('change',[
    lambda b:b.update(model_limits=dict(max_requests=40,max_output_tokens=32768)),
    lambda b:b.update(proxy_limits=dict(max_calls_per_attempt=40,max_output_tokens=32768)),
    lambda b:b.update(job_id='00000000-0000-0000-0000-000000000001'),
    lambda b:b.update(attempt_id='00000000-0000-0000-0000-000000000001'),
    lambda b:b.update(fence=True),
    lambda b:b.update(execution_request_sha256='0'*64),
    lambda b:b.update(execution_binding_sha256='0'*64),
    lambda b:b.update(grant_sha256='0'*64),
    lambda b:b.update(request_count=1),
    lambda b:b['requests'].pop(),
    lambda b:b['requests'].append(deepcopy(b['requests'][0])),
    lambda b:b['requests'][1].update(enforced_model_limits=None),
    lambda b:b['requests'][1].update(enforced_model_limits=dict(max_requests=8,max_output_tokens=32768)),
    lambda b:b['requests'][1].update(fence=True),
    lambda b:b['requests'][1].update(status=201),
    lambda b:b['requests'][1].update(state='admitted'),
    lambda b:b['requests'][1].update(response_sha256='0'*64),
    lambda b:b['requests'][1].update(response_bytes=True),
    lambda b:b['requests'][1].update(response_base64='garbage'),
    lambda b:b['requests'][1].update(usage=None),
    lambda b:b['requests'][1]['usage'].update(total_tokens=0),
    lambda b:b.update(usage_completeness='partial'),
    lambda b:b['totals'].update(total_tokens=25),
    lambda b:b['totals'].update(cache_read_tokens=0),
    lambda b:b.update(physical_stop_scope='control_stop_verified'),
    lambda b:b.update(cost_status='complete'),
    lambda b:b.update(job_request_sha256='0'*64),
    lambda b:b['job_request'].pop('model_limits'),
    lambda b:b['job_request'].update(profile='video.picture-learning-review.v1'),
])
def test_missing_limits_raw_response_usage_or_identity_never_gets_admission(tmp_path,monkeypatch,change):
    directory,root,_,_=capture(tmp_path,monkeypatch);mutate_budget(root,change)
    with pytest.raises(ServiceError,match='v2_admission'):
        admission.assess_candidate(directory)


@pytest.mark.parametrize('where,change',[
    ('candidate-evidence.json',lambda v:v.pop('model_budget_evidence')),
    ('candidate-evidence.json',lambda v:v['model_budget_evidence'].update(sha256='0'*64)),
    ('candidate-evidence.json',lambda v:v.pop('model_image_evidence')),
    ('candidate-evidence.json',lambda v:v['model_image_evidence']['images'][0].update(media_type='image/jpeg')),
    ('candidate-evidence.json',lambda v:v['model_image_evidence']['images'][0].update(model_request_sha256='1'*64)),
    ('candidate-evidence.json',lambda v:v['model_image_evidence']['images'].clear()),
    ('candidate-evidence.json',lambda v:v['model_calls'][0].update(response_sha256='0'*64)),
    ('worker-pod.json',lambda v:v['metadata'].update(uid='00000000-0000-0000-0000-000000000001')),
    ('worker-pod.json',lambda v:v['status']['containerStatuses'][0].update(state=dict(running={}))),
    ('proxy-pod.json',lambda v:v['status']['containerStatuses'][0].update(restartCount=1)),
    ('uid-reservation.json',lambda v:v.update(uid_start=True)),
    ('uid-reservation.json',lambda v:v.update(reusable=True)),
    ('host-config.json',lambda v:v['execution_catalog']['tool_environments'][0].update(name='tools-pi-picture-learning')),
])
def test_png_transport_native_stop_external_pods_uid_and_env_are_all_mandatory(tmp_path,monkeypatch,where,change):
    directory,root,_,_=capture(tmp_path,monkeypatch);fixture.edit(root,where,change)
    with pytest.raises((ServiceError,ValueError)):
        admission.assess_candidate(directory)


def test_v1_report_or_input_cannot_authorize_v2_and_failure_writes_no_approval(tmp_path,monkeypatch):
    from agent_service.deploy import picture_learning_acceptance as legacy
    with pytest.raises(ServiceError):admission._protocol('video.picture-learning-review.v1')
    with pytest.raises(ServiceError):legacy._protocol(protocol.PROFILE)
    directory,root,_,_=capture(tmp_path,monkeypatch)
    fixture.edit(root,'candidate-evidence.json',lambda value:value.pop('model_budget_evidence'))
    fixture.save(root/'acceptance-evidence.json',dict(schema='video-picture-learning-independent-review.v1',passed=True))
    target=tmp_path/'must-not-exist'
    with pytest.raises(ServiceError):admission.review_candidate(directory,target)
    assert not target.exists()


def test_signed_budget_claim_cannot_replace_missing_actual_provider_usage(tmp_path,monkeypatch):
    directory,root,_,_=capture(tmp_path,monkeypatch)
    def change(budget):
        row=budget['requests'][1]
        raw=canonical_bytes(dict(object='response',status='completed',output=[],usage=None))
        row.update(response_bytes=len(raw),response_base64=base64.b64encode(raw).decode(),response_sha256=hashlib.sha256(raw).hexdigest())
    mutate_budget(root,change)
    value=fixture.read(root/'candidate-evidence.json')
    value['model_calls'][1]['response_sha256']=value['model_budget_evidence']['requests'][1]['response_sha256']
    fixture.save(root/'candidate-evidence.json',value)
    with pytest.raises(ServiceError,match='provider_usage_incomplete'):admission.assess_candidate(directory)


def test_real_candidate_sqlite_export_and_png_transport_match_the_independent_verifier(candidate):
    """Real local persistence and proxy, mocked upstream; no cold/live admission."""
    from agent_service.model_proxy import image_evidence_for_job
    run=candidate();authority=run.authority
    connectivity=authority.proxy.respond(authority.grant,run.body)
    actual=dict(model=authority.config.model.model,store=False,max_output_tokens=8192,
        input=[dict(role='user',content=[dict(type='input_image',image_url='data:image/png;base64,'+
            base64.b64encode(PNG).decode())])])
    authority.proxy.respond(authority.grant,actual)
    evidence=authority.model_budget_evidence();calls=authority.calls()
    record=dict(model_budget_evidence=evidence,
        model_image_evidence=image_evidence_for_job(authority.store,
            authority.store.get_job('candidate-probe','administrator',authority.envelope.job_id)))
    observation=dict(model_round_trip=dict(api_mode='responses',status=200,response_sha256=hashlib.sha256(connectivity[2]).hexdigest()))
    proof=admission.verify_model_budget(record,authority.envelope,calls,observation)
    assert proof['provider_usage_complete'] is True and proof['totals']['total_tokens']==22
    assert evidence['requests'][0]['enforced_model_limits']['max_output_tokens']==2048
    value=document();inputs={frame['file_name']:PNG for frame in value['observation']['frames']}
    frames=admission._frames(value,inputs,record,authority.envelope,calls,proof['connectivity_body_sha256'])
    assert frames['evidence_scope']=='completed_upstream_transport' and frames['model_understanding_verified'] is False


@pytest.mark.parametrize('change',[
    lambda r:r.update(profile='video.picture-learning-review.v1'),
    lambda r:r.update(profile_revision='2'),
    lambda r:r['task'].update(input_sha256='0'*64),
    lambda r:r.pop('model_limits'),
    lambda r:r['model_limits'].update(max_output_tokens=32768),
    lambda r:r['inputs'][0].update(sha256='0'*64),
])
def test_resealed_job_request_still_must_match_original_envelope_and_limits(tmp_path,monkeypatch,change):
    directory,root,_,_=capture(tmp_path,monkeypatch)
    def update(budget):
        change(budget['job_request']);budget['job_request_sha256']=digest(budget['job_request'])
    mutate_budget(root,update)
    with pytest.raises(ServiceError,match='job_request_unbound'):
        admission.assess_candidate(directory)


def test_supplier_terminal_sse_bytes_are_recomputed_without_losing_usage(tmp_path,monkeypatch):
    directory,root,_,_=capture(tmp_path,monkeypatch)
    value=fixture.read(root/'candidate-evidence.json');budget=value['model_budget_evidence'];row=budget['requests'][1]
    provider=json.loads(base64.b64decode(row['response_base64']))
    raw=b'event: response.completed\ndata: '+canonical_bytes(dict(type='response.completed',response=provider))+b'\n\ndata: [DONE]\n\n'
    row.update(response_media_type='text/event-stream; charset=utf-8',response_base64=base64.b64encode(raw).decode(),
        response_bytes=len(raw),response_sha256=hashlib.sha256(raw).hexdigest())
    value['model_calls'][1]['response_sha256']=row['response_sha256']
    budget['sha256']=digest({k:v for k,v in budget.items() if k!='sha256'})
    fixture.save(root/'candidate-evidence.json',value)
    _,report=admission.assess_candidate(directory)
    assert report['model_budget']['provider_usage_complete'] is True
    assert report['model_budget']['totals']['total_tokens']==24
