"""Stopped Agent handoffs for composable film modules, never legacy scenes.

Reuse the frozen Skill transport and host-observed tool evidence. A registered
implementation does not grant a caller or make an unadmitted profile ready.
"""
from agent_service.profiles.video_animation_film_module import (
    PROFILE, INPUT_NAME, INPUT_ROLE, RESULT_NAME, INPUT_MAX_BYTES, OUTPUT_MAX_BYTES,
    VideoAnimationFilmModuleProfile,
)
from agent_service.profiles.video_animation_film_module_contract import validate_result
from .creative_film_module_contract import validate_module_input
from .skill_production_gateway import SkillProductionAgentGateway


class FilmModuleAgentGateway(SkillProductionAgentGateway):
    profile = PROFILE
    input_name, input_role, result_name = INPUT_NAME, INPUT_ROLE, RESULT_NAME
    business_prefix = 'video-film-module:'
    idempotency_prefix = 'film-module-'
    input_limit, output_limit = INPUT_MAX_BYTES, OUTPUT_MAX_BYTES
    validate_input = staticmethod(validate_module_input)
    validate_result = staticmethod(validate_result)
    profile_implementation = VideoAnimationFilmModuleProfile
