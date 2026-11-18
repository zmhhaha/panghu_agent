"""New directly visual still profiles over the existing stopped-job transport."""
from agent_service.profiles import video_picture_learning as picture
from .animation_review_gateway import _AnimationGateway


class PictureAuthorGateway(_AnimationGateway):
    protocol = picture
    profile = picture.AUTHOR_PROFILE
    visual_input = True
    input_name, input_role = picture.INPUT_NAME, 'picture_learning_input'
    result_name = picture.RESULT_NAME
    business_prefix, idempotency_prefix = 'picture-learning-author:', 'picture-author-'
    input_limit, output_limit = picture.INPUT_MAX_BYTES, picture.OUTPUT_MAX_BYTES
    validate_input = staticmethod(picture.validate_input)
    validate_result = staticmethod(picture.validate_result)


class PictureReviewGateway(PictureAuthorGateway):
    profile = picture.REVIEW_PROFILE
    business_prefix, idempotency_prefix = 'picture-learning-review:', 'picture-review-'
