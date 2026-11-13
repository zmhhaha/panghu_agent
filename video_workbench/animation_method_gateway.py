"""Methods-first jobs use the generic Agent transport, with no admission retry."""
from agent_service.profiles import video_animation_method as method
from agent_service.profiles import video_animation_skill_method as skill_method
from .animation_review_gateway import _AnimationGateway


class AnimationMethodAuthorGateway(_AnimationGateway):
    protocol = method
    profile = method.AUTHOR_PROFILE
    input_name, input_role = method.INPUT_NAME, 'animation_method_input'
    result_name = method.RESULT_NAME
    business_prefix, idempotency_prefix = 'animation-method-author:', 'method-author-'
    input_limit, output_limit = method.INPUT_MAX_BYTES, method.OUTPUT_MAX_BYTES
    validate_input = staticmethod(method.validate_input)
    validate_result = staticmethod(method.validate_result)


class AnimationMethodReviewGateway(AnimationMethodAuthorGateway):
    profile = method.REVIEW_PROFILE
    business_prefix, idempotency_prefix = 'animation-method-review:', 'method-review-'
    visual_input = True


class AnimationSkillMethodAuthorGateway(_AnimationGateway):
    protocol = skill_method
    profile = skill_method.AUTHOR_PROFILE
    input_name, input_role = skill_method.INPUT_NAME, 'animation_method_input'
    result_name = skill_method.RESULT_NAME
    business_prefix, idempotency_prefix = 'animation-skill-method-author:', 'skill-method-author-'
    input_limit, output_limit = skill_method.INPUT_MAX_BYTES, skill_method.OUTPUT_MAX_BYTES
    validate_input = staticmethod(skill_method.validate_input)
    validate_result = staticmethod(skill_method.validate_result)


class AnimationSkillMethodReviewGateway(AnimationSkillMethodAuthorGateway):
    profile = skill_method.REVIEW_PROFILE
    business_prefix, idempotency_prefix = 'animation-skill-method-review:', 'skill-method-review-'
    visual_input = True
