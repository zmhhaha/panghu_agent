"""Shared exact semantic inputs for audio-first film continuity.

This records authoring intent and speech options only. Candidate insertions,
manual media heads and review flags cannot change the frozen content identity.
"""
from copy import deepcopy

from .audio_store import narration_document
from .explanation_store import visual_generation_description


def freeze_production_inputs(store, project, *, film_aware=False):
    from .motion_store import semantic_binding
    provider = store._project_speech_provider(project)
    frozen = dict(title=project['title'], script=project['script'], style=project.get('style', ''),
        joint_plan=deepcopy(project.get('joint_plan')), provider=provider,
        scenes=[dict(id=scene['id'], title=scene['title'],
            semantic_binding=semantic_binding(project, scene),
            audio_input=narration_document(scene, provider), joint_plan=deepcopy(scene.get('joint_plan')),
            duration_seconds=scene['duration_seconds'], format=scene.get('format', 'svg'),
            leading_seconds=scene.get('leading_seconds', 0), trailing_seconds=scene.get('trailing_seconds', 0))
            for scene in project['scenes']])
    if film_aware:
        for item, scene in zip(frozen['scenes'], project['scenes']):
            item['visual_intent'] = visual_generation_description(scene, enforce_limit=True)
    return frozen
