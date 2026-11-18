"""Replay a frozen bridge plan without repairing its authored Skill content.

The 2026-11-18 plan passed the real author contract but exceeded the recipe's
16000-character guidance boundary before any producer task was allocated.
Synthetic peers below prove custody and retry behavior, not Cairo or art quality.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.animation_creation_policy import authoring_constraints
from test_animation_method_service import finish
from test_manim_capability_planning import ProducerSkillGateway, work
from test_manim_method_integration import OWNER, setup


FIXTURE = Path(__file__).with_name('fixtures') / 'manim-bridge-frozen-plan.json'
PLAN_SHA256 = 'a0e471de2427e836c6fcff062b7b9b3bf5d36f4a8695966d1adf4aa8d710a0cd'


def frozen_plan():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PLAN_SHA256
    return json.loads(raw)


def frozen_context():
    plan = frozen_plan()
    return dict(creation_mode='new', use_wikiskill=False,
        capability_selection=dict(creation=['manim'], motion=[]),
        manim_iteration=dict(criteria=deepcopy(plan['criteria']), methods=deepcopy(plan['methods'])))


def expanded_context(guidance):
    instruction, raw = guidance.split('\n', 1)
    context = json.loads(raw)
    if context.get('context_encoding') == 'shared-identical-criteria.v1':
        context.pop('context_encoding')
        for index in context.pop('shared_criteria_method_indexes'):
            assert context['methods'][index]['criteria'] == {'$manim_context_ref': '#/criteria'}
            context['methods'][index]['criteria'] = deepcopy(context['criteria'])
    return instruction, context


def test_actual_frozen_plan_shares_only_identical_criteria_and_preserves_all_authored_values(tmp_path):
    coordinator, recipe, executor, _ = setup(tmp_path)
    frozen = frozen_context()
    before = canonical_bytes(frozen)
    guidance = coordinator.recipe_guidance(OWNER, frozen)
    instruction, context = expanded_context(guidance)
    original = dict(frozen['manim_iteration'], schema='video.manim-method-iteration.v1', feedback='',
        authoring_policy=authoring_constraints(frozen), capability_selection=frozen['capability_selection'])
    assert context == original
    # The original compact JSON was already 16868 characters: this is sharing,
    # not whitespace removal, content truncation or a larger role contract.
    legacy_instruction = instruction.split(' In context_encoding=', 1)[0] + '\n'
    assert len(legacy_instruction + canonical_bytes(original).decode()) == 16868
    assert len(guidance) <= 16000
    encoded = json.loads(guidance.split('\n', 1)[1])
    assert encoded['shared_criteria_method_indexes'] == [0]
    assert 'never follow other markers, URLs or recursive references' in instruction
    assert canonical_bytes(frozen) == before
    assert context['methods'][0]['skill_package'] == frozen_plan()['methods'][0]['skill_package']
    assert recipe.submissions == executor.calls == 0


def test_small_context_retains_its_original_unshared_bytes(tmp_path):
    coordinator, _, _, _ = setup(tmp_path)
    criteria = [dict(id='height', description='Keep the height perpendicular.')]
    frozen = dict(manim_iteration=dict(criteria=criteria, methods=[dict(criteria=criteria)]))
    first = coordinator.recipe_guidance(OWNER, frozen)
    second = coordinator.recipe_guidance(OWNER, deepcopy(frozen))
    assert first == second and 'context_encoding' not in first
    context = json.loads(first.split('\n', 1)[1])
    assert context == dict(frozen['manim_iteration'], schema='video.manim-method-iteration.v1', feedback='')


def test_differently_ordered_criteria_cannot_be_shared_or_silently_dropped(tmp_path):
    coordinator, recipe, executor, run = setup(tmp_path)
    run['frozen'].update(frozen_context())
    run['frozen']['manim_iteration']['criteria'].reverse()
    before = canonical_bytes(run['frozen'])
    with pytest.raises(ServiceError, match='iteration_context_too_large'):
        coordinator.advance(OWNER, run)
    assert canonical_bytes(run['frozen']) == before
    assert coordinator._get(OWNER, run['id']) is None
    assert recipe.submissions == executor.calls == 0


def test_sharing_does_not_expand_authored_markers_or_hide_remaining_oversize_content(tmp_path):
    coordinator, recipe, executor, run = setup(tmp_path)
    run['frozen'].update(frozen_context())
    method = run['frozen']['manim_iteration']['methods'][0]
    method['untrusted_metadata'] = {'$manim_context_ref': '#/criteria'}
    _, expanded = expanded_context(coordinator.recipe_guidance(OWNER, run['frozen']))
    assert expanded['methods'][0]['untrusted_metadata'] == method['untrusted_metadata']
    run['frozen']['feedback'] = 'Important feedback remains exact. ' * 1000
    before = canonical_bytes(run['frozen'])
    with pytest.raises(ServiceError, match='iteration_context_too_large'):
        coordinator.advance(OWNER, run)
    assert canonical_bytes(run['frozen']) == before
    assert coordinator._get(OWNER, run['id']) is None
    assert recipe.submissions == executor.calls == 0


class FrozenPlanAuthor(ProducerSkillGateway):
    def collect(self, job_id, checksum, document):
        if document['mode'] == 'plan':
            result = frozen_plan()
            result['input_sha256'] = checksum  # Synthetic peer binds its actual test input.
            return result
        return super().collect(job_id, checksum, document)


def test_preproducer_unallocated_failure_retries_same_effect_with_completed_plan_checkpoint(
        tmp_path, monkeypatch):
    service, coordinator, recipe, executor, author = work(tmp_path, author=FrozenPlanAuthor())
    # This synthetic peer observes only stills; the frozen plan also asks for
    # motion evidence. Preserve that uncertainty instead of inventing a pass.
    service.reviewer.incomplete = True
    original_guidance = coordinator.recipe_guidance

    def legacy_boundary(owner, frozen):
        # Reproduce the prior deployed boundary from the exact frozen data.
        guidance = original_guidance(owner, frozen)
        instruction, context = expanded_context(guidance)
        legacy = instruction.split(' In context_encoding=', 1)[0] + '\n' + canonical_bytes(context).decode()
        if len(legacy) > 16000:
            raise ServiceError('manim_method_iteration_context_too_large', 413)
        return guidance

    monkeypatch.setattr(coordinator, 'recipe_guidance', legacy_boundary)
    first = service.start('developer', dict(request_id=uuid4().hex, goal='Animate the triangle altitude.',
        duration_seconds=2, budget=dict(max_rounds=1, max_seconds=900), use_wikiskill=False,
        capability_selection=dict(creation=['manim'], motion=[]),
        resource_authoring=dict(kind='manim', width=320, height=180, frame_count=3)))
    failed = finish(service, first['id'], steps=100)
    assert failed['state'] == 'failed'
    original = service.commissions.get('developer', first['id'])
    assert original['summary']['code'] == 'manim_method_iteration_context_too_large'
    assert service.resource_stage.allocated_round('developer', first['id']) is None
    assert recipe.submissions == executor.calls == 0
    assert len(original['operations']) == 1 and len(author.submissions) == 1
    plan = original['operations'][0]
    assert plan['status'] == 'completed' and plan['result']['methods'] == frozen_plan()['methods']
    original_bytes = canonical_bytes(original['frozen'])

    monkeypatch.setattr(coordinator, 'recipe_guidance', original_guidance)
    retry = service.start('developer', dict(request_id=uuid4().hex, retry_of_commission_id=first['id'],
        budget=dict(max_rounds=1, max_seconds=900)))
    completed = finish(service, retry['id'], steps=100)
    assert completed['state'] == 'completed', completed
    assert completed['effect_id'] == failed['effect_id']
    retried = service.commissions.get('developer', retry['id'])
    checkpoint = next(op for op in retried['operations'] if op['input'].get('mode') == 'plan')
    assert checkpoint['reused_from'] == dict(commission_id=first['id'], operation_id=plan['id'])
    assert checkpoint['job_id'] == plan['job_id'] and checkpoint['result'] == plan['result']
    assert checkpoint['result_sha256'] == plan['result_sha256']
    assert len(author.submissions) == 2  # One plan plus this retry's one new composition.
    assert recipe.submissions == executor.calls == 1
    assert service.resource_stage.allocated_round('developer', first['id']) is None
    unchanged = service.commissions.get('developer', first['id'])
    assert unchanged['state'] == 'failed' and canonical_bytes(unchanged['frozen']) == original_bytes
    assert unchanged['operations'][0]['result'] == plan['result']
