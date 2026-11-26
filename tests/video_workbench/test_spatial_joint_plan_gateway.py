"""Both real planner transports carry the opt-in task revision, and only it."""
import httpx
import pytest

from agent_service.client import AgentClient
from agent_service.contracts import canonical_bytes, digest
from agent_service.profiles.video_joint_plan import VideoJointPlanProfile
from agent_service.profiles.video_joint_plan_skills import VideoJointPlanSkillsProfile
from agent_service.profiles.video_joint_plan_contract import planning_method_task
from agent_service.spatial_rendering_contract import foundation_guidance
from video_workbench.joint_plan_gateway import JointPlanAgentGateway
from test_animation_wikiskill_production import adapter, release
from test_joint_plan_gateway import document
from test_skill_production_gateway import Peer, TOKEN


@pytest.mark.parametrize('with_skills', [False, True])
@pytest.mark.parametrize('with_offer', [False, True])
def test_revision_is_frozen_in_actual_uploaded_and_submitted_planner_task(
        tmp_path, monkeypatch, with_skills, with_offer):
    monkeypatch.setattr('video_workbench.skill_production_gateway.time.time', lambda: 1000)
    context = document()
    if with_offer:
        context['rendering_foundation'] = foundation_guidance()
    service = None
    if with_skills:
        service = adapter(tmp_path / 'library', dict(active=release(), recent={}), [])
        binding = service.pin('recipient', 'planning-batch')
        delivery = service.freeze('recipient', 'planning', planning_method_task(context),
            context['allowed_engines'], library_binding=binding, discovery_only=True)
        context['skill_catalog'] = delivery['catalog']
    peer = Peer()
    handler = VideoJointPlanSkillsProfile() if with_skills else VideoJointPlanProfile()
    peer.profile['profile'] = handler.name
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        gateway = JointPlanAgentGateway(client, skill_adapter=service)
        uploaded = gateway.upload(context)
        gateway.submit('operation-1', context, 1200, uploaded)
    task = {'input_sha256': digest(context)}
    if with_offer:
        task['rendering_foundation_revision'] = 1
    assert peer.request['task'] == task
    assert peer.uploads['joint-plan-input.json'] == canonical_bytes(context)
    for name, raw in peer.uploads.items():
        path = tmp_path / 'workspace' / 'inputs' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    handler.validate_inputs(tmp_path / 'workspace', task)
