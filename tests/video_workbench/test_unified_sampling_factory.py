"""Real executor factory shares one sampler across private HTTP and all labs.

No model, renderer or network peer is invoked. The only HTTP hop is loopback
to the actual executor server, and the only prepared source is retained PNG.
"""
import time

from video_workbench import animation_creation_executor as executor
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.visual_sampling_http import VisualSamplingClient
from test_animation_creation_executor import config, serving, NoNetworkAgentClient, CALLER_TOKEN
from test_visual_sampling_http import OWNER, frames


def test_factory_and_private_http_use_one_sampler_with_unchanged_evaluation(config,tmp_path):
    picture=tmp_path/'picture-token';picture.write_text('picture-unit-'+ 'e'*40,encoding='ascii')
    video=tmp_path/'video-token';video.write_text('video-unit-'+ 'f'*40,encoding='ascii')
    server,agent=executor.create_server(**dict(config,
        method_project_runtime='hyperframes-core-bridge',
        picture_agent_token_file=picture,video_learning_agent_token_file=video,
        enable_scene_space_rendering=True))
    shared=server.visual_sampling_service
    try:
        assert server.sampling_transport.sampler is shared
        assert server.service.sampling_service is shared
        assert server.method_service.sampling_service is shared
        assert server.method_service.renderer.sampling_service is shared
        assert server.picture_service.sampling_service is shared
        assert server.picture_service.renderer.sampling_service is shared
        assert server.video_learning_service.sampling_service is shared
        assert server.video_learning_service.renderer.sampling_service is shared
        assert server.scene_space_service.sampling_service is shared
        assert server.service.review_gateway.profile=='video.animation-review.v4'
        assert server.method_service.reviewer.profile=='video.animation-method-review.v2'
        with serving(server):
            client=VisualSamplingClient(MethodLabClient(
                'http://127.0.0.1:'+str(server.server_port),CALLER_TOKEN))
            result=client.prepare_frames(OWNER,'factory-source',dict(owner=OWNER,render_id='retained'),
                dict(strategy='provided',max_frames=2),frames(),deadline_at=time.time()+60)
            sample_id=result['sampling_operation_id']
            # Explicit advancement reconciles with the real pump under the
            # same key. Reads below cannot invoke a provider or Agent.
            server.sampling_transport.advance(OWNER,'factory-source')
            until=time.monotonic()+5
            completed=client.get(OWNER,'factory-source')
            while completed['state'] in {'queued','running'} and time.monotonic()<until:
                time.sleep(.01);completed=client.get(OWNER,'factory-source')
            assert completed['state']=='completed',completed
            assert client.read_frames(OWNER,'factory-source')==frames()
            assert shared.read_frames(OWNER,sample_id)==frames()
            assert shared.get(OWNER,sample_id)['source_ref']['source']['render_id']=='retained'
            assert all(peer.calls==[] for peer in NoNetworkAgentClient.instances)
    finally:
        server.server_close();agent.close()
