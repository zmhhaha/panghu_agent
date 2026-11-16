"""Offer only production resources whose current producer/consumer passed admission."""
from agent_service.contracts import ServiceError


def admitted_resource_producers(film_creation, owner):
    if film_creation is None or not film_creation.enabled:
        return []
    try:
        film_creation.readiness()
        film_creation.preflight_resources(owner)
    except ServiceError:
        return []
    return [dict(id='manim', kind='animation', revision='manim-cairo-1', admitted=True,
        capabilities=['manim', 'transparent-png-sequence', 'local-frame-clock'],
        description='Agent-authored Manim Cairo animation rendered to transparent PNG frames. '
            'Use for a short mathematical or geometric demonstration when it helps explain the objective. '
            'HTML/SVG, Canvas and Three.js remain available during whole-film rendering; '
            'an empty preproduction graph is valid. No TeX or word-level timing is provided.',
        limits=dict(max_frames=60, max_width=640, max_height=360, renderer='cairo', latex_available=False))]
