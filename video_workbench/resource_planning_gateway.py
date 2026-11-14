"""Use the existing stopped-artifact transport with a distinct admitted profile."""
from agent_service.profiles.video_resource_plan import PROFILE, INPUT_NAME, INPUT_ROLE, RESULT_NAME
from agent_service.profiles.video_resource_plan_contract import (
    INPUT_MAX_BYTES, OUTPUT_MAX_BYTES, validate_input, validate_result)
from .joint_plan_gateway import JointPlanAgentGateway


class ResourcePlanningAgentGateway(JointPlanAgentGateway):
    profile = PROFILE
    input_name, input_role, result_name = INPUT_NAME, INPUT_ROLE, RESULT_NAME
    input_limit, output_limit = INPUT_MAX_BYTES, OUTPUT_MAX_BYTES
    business_prefix, key_prefix = 'video-resource-plan:', 'resource_plan-'
    input_validator = staticmethod(validate_input)
    result_validator = staticmethod(validate_result)
