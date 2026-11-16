"""Independent technical admission for the exact progressive-Skill author v3."""
from __future__ import annotations

from agent_service.deploy import animation_production_acceptance as production


SUITE = production.SKILL_SUITE


def assess_candidate(candidate_dir, *, source_root=None, renderer_dir, renderer_image):
    return production.assess_candidate(candidate_dir, source_root=source_root,
        renderer_dir=renderer_dir, renderer_image=renderer_image, skills=True)


def review_candidate(candidate_dir, output, *, source_root=None, renderer_dir, renderer_image):
    return production.review_candidate(candidate_dir, output, source_root=source_root,
        renderer_dir=renderer_dir, renderer_image=renderer_image, skills=True)


def main(argv=None):
    return production.main(argv, skills=True)


if __name__ == '__main__':
    raise SystemExit(main())
