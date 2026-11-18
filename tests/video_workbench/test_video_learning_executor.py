"""New video learning owns a separate caller and joins only explicit publication."""
import pytest
from video_workbench import animation_creation_executor as executor
from video_workbench.video_learning_service import VideoLearningService
from video_workbench.video_learning_renderer import VideoLearningRenderer
from test_animation_creation_executor import config, NoNetworkAgentClient


def test_video_runtime_is_idle_separate_and_closed(config, tmp_path):
    token = tmp_path/'video-token'
    token.write_text('video-learning-specific-'+'v'*40)
    server, client = executor.create_server(**dict(config, video_learning_agent_token_file=token,
        method_project_runtime='hyperframes-core-bridge'))
    try:
        videos = server.video_learning_service
        assert isinstance(videos, VideoLearningService)
        assert isinstance(videos.renderer, VideoLearningRenderer)
        assert videos.author.profile == 'video.reference-learning-author.v1'
        assert videos.reviewer.profile == 'video.reference-learning-review.v1'
        assert videos.author.client is videos.reviewer.client
        assert videos.author.client is not server.method_service.author.client
        assert videos.pending() == [] and videos.catalogue('developer')['projects'] == []
        assert videos.library_adapter is not None and server.video_learning_pump.thread.is_alive()
        assert all(peer.calls == [] for peer in NoNetworkAgentClient.instances)
    finally:
        server.server_close(); client.close()
    assert not server.video_learning_pump.thread.is_alive()
    assert videos.author.client.close_count == 1


def test_video_rejects_existing_credential_and_native_only_renderer(config):
    with pytest.raises(ValueError, match='video_learning_distinct_credentials_required'):
        executor.create_server(**dict(config,video_learning_agent_token_file=config['method_agent_token_file'],
            method_project_runtime='hyperframes-core-bridge'))
    with pytest.raises(ValueError, match='video_learning_hyperframes_methods_required'):
        executor.create_server(**dict(config,video_learning_agent_token_file=config['method_agent_token_file']))
