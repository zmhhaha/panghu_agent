"""Same-content spatial repairs over the existing scene-plan revision contract.

The host may correct layout, geometry declarations, layer structure and cameras
without changing the declared worlds, seeds, style or object identities. This
does not inspect painting source or prove that arbitrary JavaScript depicts the
same subject. Actual visual review remains a separate piece of evidence.
"""
from __future__ import annotations

from copy import deepcopy

from agent_service.contracts import ServiceError, digest
from .scene_space_plan import FROZEN_SCHEMA, freeze_plan, revise_plan, technical_retry, validate_plan

PLAN_REPAIR_REVISION = 1


def _require(value, code):
    if not value:
        raise ServiceError(code, 409)


def _reason(value):
    # Source-only correction still needs a bounded audit reason, even though it
    # does not consume a plan revision. The changed-plan path also delegates the
    # full reason contract to revise_plan.
    _require(type(value) is str and bool(value.strip()) and len(value) <= 4000
        and not any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127
            or 0xD800 <= ord(c) <= 0xDFFF for c in value),
        'scene_space_same_content_reason_invalid')


def _frozen_original(original):
    if type(original) is dict and original.get('schema') == FROZEN_SCHEMA:
        return technical_retry(original, original.get('plan_sha256'))
    return freeze_plan(original)


def validate_same_content_plan_repair(original, replacement, reason):
    """Validate one unchanged/source-only plan or a traceable structural repair.

    ``original`` may be a raw or frozen plan; ``replacement`` is a raw plan.
    Changed replacements must advance the plan/world revisions and consumed
    revision count exactly as revise_plan requires. Its budget history,
    provenance, geometry and full task-frame coverage checks remain in force.

    Returns ``{plan, change, frozen}``; ``change`` is the existing revision
    report, or None for an identical normalized plan. No input is mutated, no
    store is written, and neither path grants visual acceptance.
    """
    _reason(reason)
    original_frozen = _frozen_original(original)
    before = original_frozen['plan']
    after = validate_plan(replacement)
    if digest(after) == original_frozen['plan_sha256']:
        return dict(plan=deepcopy(after), change=None, frozen=deepcopy(original_frozen))

    # Keep all existing revision and metering safeguards. Content restrictions
    # narrow this revision operation; they never replace its validation.
    revised = revise_plan(original_frozen, after, reason=reason)
    after = revised['plan']
    _require(all(after[key] == before[key] for key in ('fps', 'viewport', 'duration_frames')),
        'scene_space_same_content_clock_changed')
    old_worlds = {world['id']: world for world in before['worlds']}
    new_worlds = {world['id']: world for world in after['worlds']}
    _require(old_worlds.keys() == new_worlds.keys(), 'scene_space_same_content_worlds_changed')
    for world_id, old in old_worlds.items():
        new = new_worlds[world_id]
        _require(new['seed'] == old['seed'], 'scene_space_same_content_seed_changed')
        _require(new['style'] == old['style'], 'scene_space_same_content_style_changed')
        _require({obj['id']: obj['kind'] for obj in new['objects']}
            == {obj['id']: obj['kind'] for obj in old['objects']},
            'scene_space_same_content_objects_changed')
    return dict(plan=deepcopy(after), change=deepcopy(revised['revision']), frozen=deepcopy(revised['frozen']))
