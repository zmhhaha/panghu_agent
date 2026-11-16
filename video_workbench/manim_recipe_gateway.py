"""Existing general Agent transport for an independent Manim recipe role."""
from agent_service.profiles.video_animation_manim_recipe import (
    PROFILE, INPUT_NAME, INPUT_ROLE, RESULT_NAME, INPUT_MAX_BYTES, OUTPUT_MAX_BYTES,
    VideoAnimationManimRecipeProfile,
)
from agent_service.profiles.video_animation_manim_contract import validate_input, validate_result
from .skill_production_gateway import SkillProductionAgentGateway


class ManimRecipeAgentGateway(SkillProductionAgentGateway):
    profile = PROFILE
    input_name, input_role, result_name = INPUT_NAME, INPUT_ROLE, RESULT_NAME
    business_prefix = 'video-manim-recipe:'
    idempotency_prefix = 'manim-recipe-'
    input_limit, output_limit = INPUT_MAX_BYTES, OUTPUT_MAX_BYTES
    validate_input = staticmethod(validate_input)
    validate_result = staticmethod(validate_result)
    profile_implementation = VideoAnimationManimRecipeProfile
