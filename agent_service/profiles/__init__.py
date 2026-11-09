"""Explicit reviewed implementations; configuration never imports code."""
from .base import ProfileImplementation, ProfileLimits


def implementation(name):
    if name == 'workspace-task.v1':
        from .text import TextProfile
        return TextProfile()
    if name in {'ppt.plan_initial.v1', 'ppt.plan_options.v1', 'ppt.prepare.v1', 'ppt.generate.v1', 'ppt.revise.v1'}:
        from .ppt import PPTProfile
        return PPTProfile(name.split('.')[1])
    if name in {'research.generate.v1', 'research.revise.v1'}:
        from .research import ResearchProfile, ResearchRevisionProfile
        return ResearchProfile() if name == 'research.generate.v1' else ResearchRevisionProfile()
    if name in {'research.plan.v1', 'research.retrieve.v1', 'research.outline.v1', 'research.draft.v1'}:
        from .research_workflow import ResearchWorkflowProfile
        return ResearchWorkflowProfile(name.split('.')[1])
    if name in {'video.storyboard.v1', 'video.scene.v1'}:
        from .video import VideoProfile
        return VideoProfile(name.split('.')[1])
    if name == 'video.motion-plan.v1':
        from .video_motion import VideoMotionProfile
        return VideoMotionProfile()
    if name == 'video.process-plan.v1':
        from .video_process import VideoProcessProfile
        return VideoProcessProfile()
    if name == 'video.explanation-plan.v1':
        from .video_explanation import VideoExplanationProfile
        return VideoExplanationProfile()
    if name == 'video.animation-experiment.v1':
        from .video_animation_experiment import VideoAnimationExperimentProfile
        return VideoAnimationExperimentProfile()
    if name == 'video.animation-observe.v1':
        from .video_animation_review import VideoAnimationObserveProfile
        return VideoAnimationObserveProfile()
    if name == 'video.animation-review.v1':
        from .video_animation_review import VideoAnimationReviewProfile
        return VideoAnimationReviewProfile()
    if name == 'video.animation-create.v2':
        from .video_animation_create import VideoAnimationCreateProfile
        return VideoAnimationCreateProfile()
    raise ValueError('No reviewed profile implementation')
