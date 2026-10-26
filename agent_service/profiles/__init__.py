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
    raise ValueError('No reviewed profile implementation')
