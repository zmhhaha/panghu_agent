"""Original, bounded collaboration jobs over the existing generic Agent API."""
from agent_service.profiles import video_animation_collaboration_contract as contract
from agent_service.profiles.video_animation_collaboration import PROFILE
from .animation_review_gateway import _AnimationGateway


class AnimationCollaborationGateway(_AnimationGateway):
    protocol = contract
    profile = PROFILE
    input_name, input_role = contract.INPUT_NAME, 'animation_collaboration_input'
    result_name = contract.RESULT_NAME
    business_prefix = 'animation-collaboration:'
    idempotency_prefix = 'animation-collaboration-'
    input_limit, output_limit = contract.INPUT_MAX_BYTES, contract.OUTPUT_MAX_BYTES
    validate_input = staticmethod(contract.validate_input)
    validate_result = staticmethod(contract.validate_result)
