"""One-hour caller defaults remain bounded by admitted roles and task deadlines."""
import hashlib

import httpx
import pytest

from agent_service.client import AgentClient
from video_workbench.video_learning_gateway import VideoLearningAuthorGateway, VideoLearningReviewGateway
from video_workbench.animation_review_gateway import AnimationCreateGateway
from test_animation_review_gateway import Peer, PNG, creation_document
from test_video_learning_renderer import document


@pytest.mark.parametrize('gateway_class',[VideoLearningAuthorGateway,VideoLearningReviewGateway])
@pytest.mark.parametrize('route_limit,remaining,expected',[(3600,7200,3600),(1200,7200,1200),(3600,90,90)])
def test_video_role_requests_an_hour_by_default_and_honors_explicit_platform_limits(
        gateway_class,route_limit,remaining,expected,monkeypatch):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time',lambda:1000.0)
    peer=Peer(gateway_class)
    peer.profiles[0]['max_timeout_seconds']=route_limit
    doc=document()
    if gateway_class is VideoLearningReviewGateway:
        doc['mode']='analyze'
    doc['observation']['frames'][0]['sha256']=hashlib.sha256(PNG).hexdigest()
    with AgentClient('http://agent.invalid','offline-video-token-'+'a'*32,
                     transport=httpx.MockTransport(peer)) as client:
        gateway=gateway_class(client)
        refs=gateway.upload(doc,{'reference.png':PNG,gateway.input_name:gateway._document_bytes(doc)})
        gateway.submit('video-hour-default',doc,1000.0+remaining,refs)
    assert peer.request['timeout_seconds']==expected
    assert peer.request['execution_deadline_at']==1000.0+remaining
    assert peer.count('POST','/v1/jobs')==1


def test_non_video_creation_keeps_its_existing_role_timeout(monkeypatch):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time',lambda:1000.0)
    peer=Peer(AnimationCreateGateway)
    peer.profiles[0]['max_timeout_seconds']=3600
    with AgentClient('http://agent.invalid','offline-create-token-'+'a'*32,
                     transport=httpx.MockTransport(peer)) as client:
        gateway=AnimationCreateGateway(client)
        doc=creation_document()
        gateway.submit('existing-timeout',doc,8200.0,gateway.upload(doc))
    assert peer.request['timeout_seconds']==600
