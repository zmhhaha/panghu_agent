"""Direct timed-frame video learning over the shared stopped-Agent API."""
from agent_service.profiles import video_reference_learning as reference
from .animation_review_gateway import _AnimationGateway


class VideoLearningAuthorGateway(_AnimationGateway):
    # The caller default does not supersede the admitted route or task deadline.
    task_timeout_seconds = 3600
    protocol = reference
    profile = reference.AUTHOR_PROFILE
    visual_input = True
    input_name, input_role = reference.INPUT_NAME, 'reference_learning_input'
    result_name = reference.RESULT_NAME
    business_prefix, idempotency_prefix = 'reference-learning-author:', 'reference-author-'
    input_limit, output_limit = reference.INPUT_MAX_BYTES, reference.OUTPUT_MAX_BYTES
    validate_input = staticmethod(reference.validate_input)
    validate_result = staticmethod(reference.validate_result)


class VideoLearningReviewGateway(VideoLearningAuthorGateway):
    profile = reference.REVIEW_PROFILE
    business_prefix, idempotency_prefix = 'reference-learning-review:', 'reference-review-'


# Explicit names for callers migrating from the picture-learning counterpart.
VideoAuthorGateway = VideoLearningAuthorGateway
VideoReviewGateway = VideoLearningReviewGateway
