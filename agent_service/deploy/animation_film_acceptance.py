"""Independent technical admission for the exact v3 full-film author only."""
from __future__ import annotations

from agent_service.deploy import animation_production_acceptance as production


SUITE = production.FILM_SUITE


def assess_candidate(candidate_dir, *, source_root=None, renderer_dir, renderer_image):
    return production.assess_candidate(candidate_dir, source_root=source_root,
        renderer_dir=renderer_dir, renderer_image=renderer_image, film=True)


def review_candidate(candidate_dir, output, *, source_root=None, renderer_dir, renderer_image):
    return production.review_candidate(candidate_dir, output, source_root=source_root,
        renderer_dir=renderer_dir, renderer_image=renderer_image, film=True)


def main(argv=None):
    return production.main(argv, film=True)


if __name__ == '__main__':
    raise SystemExit(main())
