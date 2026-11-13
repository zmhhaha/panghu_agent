"""Prepare a separately disabled film-continuity author; preserve all old routes."""
from __future__ import annotations

from agent_service.deploy.animation_production_catalog import (
    measure_animation_production_bundle, prepare_animation_production_catalog,
)
from agent_service.profiles.video_animation_production import FILM_PROFILE


PROFILES = (FILM_PROFILE,)


def measure_animation_film_bundle():
    return measure_animation_production_bundle(film=True)


def prepare_animation_film_catalog(current, **options):
    """Same explicit operator inputs as production preparation, for v3 only."""
    if 'film' in options:
        raise ValueError('animation_film_generation_fixed')
    return prepare_animation_production_catalog(current, film=True, **options)
