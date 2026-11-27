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


class ClassifiedPictureReviewGateway(PictureAuthorGateway):
    """A separately admitted contract; never reinterpret an old review result."""
    from agent_service.profiles import video_picture_review_v2 as protocol
    profile = protocol.PROFILE
    input_name, input_role = protocol.INPUT_NAME, 'picture_learning_input'
    result_name = protocol.RESULT_NAME
    input_limit, output_limit = protocol.INPUT_MAX_BYTES, protocol.OUTPUT_MAX_BYTES
    validate_input = staticmethod(protocol.validate_input)
    validate_result = staticmethod(protocol.validate_result)
    business_prefix, idempotency_prefix = 'picture-review-v2:', 'picture-review-v2-'

    def _frame_input_limits(self):
        # This opt-in follows the already admitted contract. Future revisions
        # and every legacy role keep the old transport budget until selected.
        if (self.profile != self.protocol.PROFILE
                or self.profile_revision != self.protocol.PROFILE_REVISION):
            return None
        return self.protocol.VideoPictureReviewV2Profile().limits

    def _files(self, document, files=None):
        expected = super()._files(document, files)
        if self._frame_input_limits() is not None:
            from .picture_review_transport_budget import require_native_wire_budget
            require_native_wire_budget(self.input_name, expected)
        return expected
