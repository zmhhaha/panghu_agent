"""Actual executor factory and authenticated HTTP select the real v2 gateway.

The Agent API client and creative outputs are synthetic. Real gateway upload,
preflight, job binding, image transport verification and business gate execute.
"""
import base64
from copy import deepcopy
import hashlib
import http.client
import json
from types import MethodType
from uuid import uuid4

import pytest

from video_workbench import animation_creation_executor as executor
from video_workbench.picture_learning_gateway import ClassifiedPictureReviewGateway
from video_workbench.learning_review_reliability import CONTRACT, MODEL_LIMITS
from test_animation_creation_executor import config, serving, CALLER_TOKEN
from test_learning_classified_review_service import KnownAuthor, LosslessRenderer
from test_learning_review_reliability import png, route, opinion, receipt
from test_picture_learning_service import finish


def post(server, path, body):
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
    try:
        connection.request('POST',path,json.dumps(body),headers={'Authorization':'Bearer '+CALLER_TOKEN,
            'X-Animation-Creation-Owner':'developer','Content-Type':'application/json'})
        response=connection.getresponse();return response.status,json.loads(response.read())
    finally:connection.close()


def install_api(gateway):
    client=gateway.client;client.inputs={};client.jobs={};client.requests=[];client.ready=True
    client.operation_keys={};client.mode='ready'
    def capabilities():
        profile=dict(route(),ready=client.ready)
        if client.mode=='missing_limits':profile.pop('model_request_limits')
        return dict(profiles=[profile])
    def upload(name,raw):
        identity=str(uuid4());client.inputs[identity]=(name,raw)
        return dict(input_id=identity,name=name,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw))
    def submit(request,*,idempotency_key):
        client.requests.append(deepcopy(request));identity=str(uuid4());attempt=str(uuid4())
        input_id=next(row['input_id'] for row in request['inputs'] if row['role']=='picture_learning_input')
        document=json.loads(client.inputs[input_id][1]);job=dict(job_id=identity,business_ref=request['business_ref'],
            model_limits=deepcopy(request.get('model_limits')),
            state='completed',stop_proven=True,publication_authorized=False,document=document,
            attempt=dict(attempt_id=attempt,fence=1),pins=dict(profile=gateway.profile,profile_revision='1'),
            stop_proof=dict(stop_proven=True,attempt_id=attempt,fence=1,request_sha256='c'*64),
            usage=dict(completeness='complete',input_tokens=30,output_tokens=20,
                cache_read_tokens=0,cache_write_tokens=0,request_count=1))
        if client.mode=='changed_limits':job['model_limits']['max_requests']=80
        client.jobs[identity]=job;client.operation_keys[idempotency_key]=identity
        if client.mode=='lost_response':raise OSError('Synthetic acknowledgement lost after durable admission')
        return deepcopy(job)
    def evidence(job_id):
        job=client.jobs[job_id];document=job['document']
        blobs={hashlib.sha256(raw).hexdigest():raw for _,raw in client.inputs.values()}
        proof=receipt(document,blobs,job_id);proof['attempt_id']=job['attempt']['attempt_id'];return proof
    client.capabilities=capabilities;client.upload_input=upload;client.submit=submit
    client.lookup_job=lambda key:deepcopy(client.jobs[client.operation_keys[key]])
    client.get_job=lambda identity:deepcopy(client.jobs[identity]);client.model_image_evidence=evidence
    gateway.collect=MethodType(lambda _self,_job_id,_input_sha,document:opinion(document),gateway)
    return client


@pytest.mark.parametrize('mode',['ready','not_ready','missing_limits','lost_response','changed_limits'])
def test_factory_http_request_real_gateway_and_gate_are_reachable(config,tmp_path,mode):
    picture=tmp_path/'picture';picture.write_text('picture-source-specific-'+'p'*40)
    classified=tmp_path/'classified';classified.write_text('classified-independent-'+'v'*40)
    server,client=executor.create_server(**dict(config,picture_agent_token_file=picture,
        classified_review_agent_token_file=classified,method_project_runtime='hyperframes-core-bridge'))
    try:
        service=server.picture_service;gateway=service.classified_reviewer
        assert isinstance(gateway,ClassifiedPictureReviewGateway)
        assert gateway is server.method_service.classified_reviewer
        assert gateway.client is not service.author.client and gateway.client.token==classified.read_text()
        server.picture_pump.close()
        service.author=KnownAuthor();service.renderer=LosslessRenderer(tmp_path/'test-renders')
        api=install_api(gateway);api.ready=mode!='not_ready';api.mode=mode
        with serving(server):
            code,reference=post(server,'/api/picture-lab/references',dict(request_id=uuid4().hex,
                file_name='reference.png',mime_type='image/png',content_base64=base64.b64encode(png()).decode()))
            assert code==201
            code,project=post(server,'/api/picture-lab/projects',dict(request_id=uuid4().hex,
                title='Explicit contract',goal='Retain the visible requested style and geometry.',
                reference_ids=[reference['id']],width=1280,height=720,use_wikiskill=False,
                max_rounds=2,max_seconds=3600,allow_probes=False,allow_collaboration=False,review_contract=CONTRACT))
            if mode in {'ready','lost_response','changed_limits'}:
                assert code==202,project
                completed=finish(service,project)
                run=service.store.read('experiments','developer',completed['latest_experiment_id'])
                assert len(api.requests)==1 and api.requests[0]['profile']=='video.picture-learning-review.v2'
                assert api.requests[0]['model_limits']==MODEL_LIMITS
                if mode=='changed_limits':
                    assert completed['state']=='failed' and run['error']['code']=='learning_review_model_limits_unproven'
                    assert not run['candidates'][0].get('review_decision')
                    return
                assert run['candidates'][0]['review_decision']['passed'] is True
                assert run['frozen']['classified_review']['model_limits']==MODEL_LIMITS
                assert run['candidates'][0]['review_evidence']['model_limits']==MODEL_LIMITS
                assert run['candidates'][0]['review_evidence']['transport_sha256']
            else:
                assert code==503 and api.requests==[] and service.author.submissions==[]
                assert service.store.list('experiments','developer')==[]
    finally:
        server.server_close();client.close()


def test_factory_rejects_a_v1_caller_reused_for_v2(config):
    with pytest.raises(ValueError,match='classified_review_distinct_credentials'):
        executor.create_server(**dict(config,classified_review_agent_token_file=config['method_agent_token_file']))
