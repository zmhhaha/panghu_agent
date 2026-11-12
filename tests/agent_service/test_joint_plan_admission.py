"""Joint planning joins existing exact admission paths without more authority."""
from copy import deepcopy

import pytest

from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_worker_host import AnimationWorkerHostError, project_animation_worker_host
from agent_service.model_proxy import profile_model_call_limit
from agent_service.profiles.video_joint_plan import PROFILE
from test_joint_plan_catalog import prepare


@pytest.mark.parametrize('configured', [1, 3, 8, 16, 32])
def test_joint_planner_uses_at_most_eight_calls_and_keeps_stricter_operator_cap(configured):
    assert profile_model_call_limit(PROFILE, configured) == min(configured, 8)
    assert profile_model_call_limit('workspace-task.v1', configured) == configured


def test_selected_joint_planner_keeps_exact_disabled_unaccepted_binding():
    _, hosts, _ = prepare()
    original = hosts[PROFILE]
    before = deepcopy(original)
    projected = project_animation_worker_host(original)
    host, policy, binding = candidate_config(projected)
    assert original == before
    assert host.profile.name == policy.name == binding.profile == PROFILE
    assert host.profile.enabled is policy.enabled is False
    assert binding.acceptance is None
    assert projected['execution_catalog']['task_contracts'][0]['required_tools'] == ['read_inputs', 'write']
    assert all(len(rows) == 1 for rows in projected['execution_catalog'].values())
    changed = deepcopy(original)
    changed['execution_catalog']['execution_bindings'][0]['profile'] = 'video.joint-plan.v99'
    with pytest.raises(AnimationWorkerHostError, match='profile_invalid'):
        project_animation_worker_host(changed)
