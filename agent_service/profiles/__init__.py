"""Explicit reviewed implementations; configuration never imports code."""
from .base import ProfileImplementation, ProfileLimits


def implementation(name):
    if name == 'video.animation-film-module.v1':
        from .video_animation_film_module import VideoAnimationFilmModuleProfile
        return VideoAnimationFilmModuleProfile()
    if name == 'video.joint-plan.v1':
        from .video_joint_plan import VideoJointPlanProfile
        return VideoJointPlanProfile()
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
    if name == 'video.animation-review.v2':
        from .video_animation_review import VideoAnimationReviewSequenceProfile
        return VideoAnimationReviewSequenceProfile()
    if name == 'video.animation-review.v3':
        from .video_animation_review import VideoAnimationReviewBatchProfile
        return VideoAnimationReviewBatchProfile()
    if name == 'video.animation-review.v4':
        from .video_animation_review import VideoAnimationReviewInputProfile
        return VideoAnimationReviewInputProfile()
    if name == 'video.animation-create.v2':
        from .video_animation_create import VideoAnimationCreateProfile
        return VideoAnimationCreateProfile()
    if name in {'video.animation-method-author.v1', 'video.animation-method-review.v1'}:
        from .video_animation_method import VideoAnimationMethodProfile
        return VideoAnimationMethodProfile(review=name == 'video.animation-method-review.v1')
    if name in {'video.animation-method-author.v2', 'video.animation-method-review.v2'}:
        from .video_animation_skill_method import VideoAnimationSkillMethodProfile
        return VideoAnimationSkillMethodProfile(review=name == 'video.animation-method-review.v2')
    if name == 'video.animation-production.v1':
        from .video_animation_production import VideoAnimationProductionProfile
        return VideoAnimationProductionProfile()
    if name == 'video.animation-production.v2':
        from .video_animation_production import VideoAnimationFilmProductionProfile
        return VideoAnimationFilmProductionProfile()
    if name == 'video.animation-production.v3':
        from .video_animation_skill_production import VideoAnimationSkillProductionProfile
        return VideoAnimationSkillProductionProfile()
    raise ValueError('No reviewed profile implementation')
