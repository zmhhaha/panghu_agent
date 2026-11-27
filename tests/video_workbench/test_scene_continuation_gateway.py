"""The optional budget field crosses the real HTTP client boundary unchanged."""
from copy import deepcopy

import httpx
import pytest

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.animation_review_gateway import AnimationReviewGateway
from video_workbench.scene_review_continuation import MODEL_LIMITS, MODEL_LIMIT_CAPABILITY
from test_animation_review_gateway import Peer, PNG, visual_document


class BoundedPeer(Peer):
    def __init__(self, *, support=True, echo=True):
        super().__init__(AnimationReviewGateway)
        self.echo=echo
        if support:self.profiles[0]['model_request_limits']=deepcopy(MODEL_LIMIT_CAPABILITY)

    def __call__(self,request):
        response=super().__call__(request)
        if request.method=='POST' and request.url.path=='/v1/jobs' and self.echo:
            self.job['model_limits']=deepcopy(self.request['model_limits'])
            return httpx.Response(202,json=self.job)
        return response


@pytest.mark.parametrize('mode',['enforced','missing_capability','missing_echo'])
def test_gateway_freezes_limits_checks_capability_and_preserves_unknown_submission(monkeypatch,mode):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time',lambda:1000.0)
    peer=BoundedPeer(support=mode!='missing_capability',echo=mode!='missing_echo')
    with AgentClient('http://agent.invalid','test-token-'+'a'*32,transport=httpx.MockTransport(peer)) as client:
        gateway=AnimationReviewGateway(client);document=visual_document()
        files={gateway.input_name:canonical_bytes(document),
            **{f['file_name']:PNG for f in document['observation']['frames']}}
        refs=gateway.upload(document,files)
        if mode=='missing_capability':
            with pytest.raises(ServiceError,match='limits_unavailable'):
                gateway.prepare_submission('continuation-review',document,1200,refs,model_limits=MODEL_LIMITS)
            assert peer.count('POST','/v1/jobs')==0
            return
        call=gateway.prepare_submission('continuation-review',document,1200,refs,model_limits=MODEL_LIMITS)
        if mode=='missing_echo':
            with pytest.raises(ServiceError):call()
            assert gateway.lookup('continuation-review')['job_id']==peer.job['job_id']
        else:assert call()['model_limits']==MODEL_LIMITS
        assert peer.request['model_limits']==MODEL_LIMITS and peer.count('POST','/v1/jobs')==1
