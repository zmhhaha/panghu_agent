"""Real HTTP serialization keeps the new result and profile separate from v1."""
from copy import deepcopy
import hashlib

import httpx
import pytest

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError,canonical_bytes,digest
from agent_service.profiles import video_picture_learning as legacy
from agent_service.profiles import video_picture_review_v2 as v2
from video_workbench.scene_space_production import SceneSpaceClassifiedReviewGateway
from video_workbench.scene_review_reliability import default_review_policy
import test_animation_review_gateway as wire


def document():
    return dict(schema=legacy.INPUT_SCHEMA,mode='review',project_id='test-project',experiment_id='test-candidate',
        work_kind='transfer',goal='Required composition and style.',criteria=['Keep the requested style.'],
        constraints=dict(width=1280,height=720,review_policy=default_review_policy()),
        observation=dict(frames=[dict(frame_id='output',file_name='output.png',purpose='output',
            sha256=hashlib.sha256(wire.PNG).hexdigest())]),base_source=None,feedback='',
        prior_review=None,learned_method=None,knowledge_snapshot=None)


def result(context):
    return dict(schema=v2.RESULT_SCHEMA,input_sha256=digest(context),mode='review',evidence_scope='static_only',
        review=dict(score=90,summary='Synthetic transport result.',
            assessments=[dict(criterion_id=c['id'],status='satisfied',evidence_frame_ids=['output'],
                reason='Synthetic fixture only.') for c in context['constraints']['review_policy']['criteria']
                if c['requirement']=='required'],findings=[],limitations=['No artistic or motion acceptance.'],
            stop_recommended=False))


@pytest.mark.parametrize('legacy_result',[False,True])
def test_v2_http_profile_manifest_and_collected_result_are_not_legacy(monkeypatch,legacy_result):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time',lambda:1000.0)
    peer=wire.Peer(SceneSpaceClassifiedReviewGateway);context=document()
    expected=result(context)
    if legacy_result:
        expected=dict(schema=legacy.RESULT_SCHEMA,input_sha256=digest(context),mode='review',source=None,
            method=None,transfer_task=None,review=dict(score=90,summary='Legacy contract.',issues=[],
                stop_recommended=False))
    monkeypatch.setattr(wire,'result_document',lambda profile,document:deepcopy(expected))
    with AgentClient('http://agent.invalid','test-token-'+'b'*32,transport=httpx.MockTransport(peer)) as client:
        gateway=SceneSpaceClassifiedReviewGateway(client)
        files={gateway.input_name:canonical_bytes(context),'output.png':wire.PNG}
        refs=gateway.upload(context,files)
        gateway.prepare_submission('new-review',context,1200,refs)()
        assert peer.request['profile']==v2.PROFILE and peer.request['task']=={'input_sha256':digest(context)}
        assert peer.request['inputs'][0]['role']=='picture_learning_input'
        peer.complete('new-review',context)
        if legacy_result:
            with pytest.raises(ServiceError):gateway.collect(wire.JOB,digest(context),context)
        else:
            assert gateway.collect(wire.JOB,digest(context),context)==expected
            assert gateway.model_frame_receipt(wire.JOB)['images'][0]['sha256']==context['observation']['frames'][0]['sha256']
        assert peer.count('POST','/v1/jobs')==1


def test_legacy_only_readiness_does_not_authorize_a_new_profile_job(monkeypatch):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time',lambda:1000.0)
    peer=wire.Peer(SceneSpaceClassifiedReviewGateway);peer.profiles[0]['profile']=legacy.REVIEW_PROFILE
    with AgentClient('http://agent.invalid','test-token-'+'b'*32,transport=httpx.MockTransport(peer)) as client:
        gateway=SceneSpaceClassifiedReviewGateway(client);context=document()
        files={gateway.input_name:canonical_bytes(context),'output.png':wire.PNG}
        refs=gateway.upload(context,files)
        with pytest.raises(ServiceError,match='animation_profile_unavailable'):
            gateway.prepare_submission('new-review',context,1200,refs)
        assert peer.count('POST','/v1/jobs')==0
