"""The picture runtime is explicitly configured and owns a distinct caller."""
from pathlib import Path
import pytest

from video_workbench import animation_creation_executor as executor
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.picture_learning_renderer import PictureLearningRenderer
from test_animation_creation_executor import config, NoNetworkAgentClient


def test_picture_runtime_uses_distinct_visual_roles_and_shared_publication(config, tmp_path):
    token = tmp_path/'picture-token'
    token.write_text('picture-specific-credential-'+'p'*40)
    server, client = executor.create_server(**dict(config,picture_agent_token_file=token,
        method_project_runtime='hyperframes-core-bridge'))
    try:
        pictures = server.picture_service
        assert isinstance(pictures,PictureLearningService)
        assert isinstance(pictures.renderer,PictureLearningRenderer)
        assert pictures.author.profile=='video.picture-learning-author.v1'
        assert pictures.reviewer.profile=='video.picture-learning-review.v1'
        assert pictures.author.client is pictures.reviewer.client
        assert pictures.author.client is not server.method_service.author.client
        assert pictures.library_adapter is not None
        assert pictures.pending()==[] and pictures.catalogue('developer')['projects']==[]
        assert server.picture_pump.thread.is_alive()
        assert all(peer.calls==[] for peer in NoNetworkAgentClient.instances)
    finally:
        server.server_close(); client.close()
    assert not server.picture_pump.thread.is_alive()
    assert pictures.author.client.close_count==1


def test_picture_credential_cannot_reuse_an_existing_business_caller(config):
    with pytest.raises(ValueError,match='picture_learning_distinct_credentials_required'):
        executor.create_server(**dict(config,picture_agent_token_file=config['method_agent_token_file'],
            method_project_runtime='hyperframes-core-bridge'))


def test_picture_configuration_requires_hyperframes(config, tmp_path):
    token=tmp_path/'picture-token';token.write_text('new-picture-token-'+'p'*40)
    with pytest.raises(ValueError,match='picture_learning_hyperframes_methods_required'):
        executor.create_server(**dict(config,picture_agent_token_file=token))
