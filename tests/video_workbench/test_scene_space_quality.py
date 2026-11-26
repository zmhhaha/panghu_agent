"""Budget reductions remain optional, frozen, reproducible and host bound."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.scene_space_quality import (MAX_MATERIAL_NOISE_EVALUATIONS,
    ESTIMATE_SCOPE, select_appearance_policy, validate_appearance_policy)


def intent(**changes):
    value = dict(schema='video.spatial-rendering-intent.v1', space_mode='layered', engine='canvas',
        camera_mode='animated', required_features=['form', 'scale', 'coverage'],
        optional_features=['lighting', 'texture', 'atmosphere'],
        quality=dict(structural_errors='block', optional_degradation='declared'))
    value.update(changes)
    return value


def inputs(**changes):
    value = dict(source_sha256='a' * 64, plan_sha256='b' * 64, surface_runtime_sha256='c' * 64,
        configuration_sha256='d' * 64, usage_lineage='same-content-budget-1', usage_sha256='e' * 64,
        estimated_material_samples=100, material_noise_limit=300)
    value.update(changes)
    return value


def reseal(value):
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    return value


@pytest.mark.parametrize('samples,limit,profile,work', [
    (100, 300, 'full', 300), (100, 299, 'coarse', 100), (100, 100, 'coarse', 100),
    (0, 1, 'full', 0), (1, 3, 'full', 3), (1, 1, 'coarse', 1),
])
def test_selects_a_fitting_real_noise_profile_at_exact_boundaries(samples, limit, profile, work):
    args = inputs(estimated_material_samples=samples, material_noise_limit=limit)
    value = select_appearance_policy(intent(), **args)
    assert value['actual_parameters']['material_quality'] == profile
    assert value['estimated_selected_noise_evaluations'] == work
    assert value['estimated_requested_noise_evaluations'] == 3 * samples
    assert 'actual_noise_evaluations' not in value and 'requested_noise_evaluations' not in value
    assert value['estimate_scope'] == ESTIMATE_SCOPE
    assert value['actual_parameters']['normal_strength'] == ('authored' if profile == 'full' else 0)
    assert value['actual_parameters']['noise_bands'] == (3 if profile == 'full' else 1)
    assert validate_appearance_policy(value, intent=intent(), **args) == value


def test_coarse_retains_intent_all_bindings_and_an_explicit_loss_without_mutating_inputs():
    original = intent(light=dict(space='world', direction=[1, 1, 2], response='artistic'))
    before = deepcopy(original); args = inputs(material_noise_limit=101)
    value = select_appearance_policy(original, **args)
    assert original == before and value['intent'] == original
    assert value['reason'] == 'optional_texture_budget_reduction'
    assert 'normal_perturbation_removed' in value['expected_loss']
    assert value['scope'] == 'per_page_cached_material_sampling'
    for key, expected in args.items():
        assert value[key] == expected
    assert 'remaining_budget' not in value and 'refund' not in value and 'new_lineage' not in value
    assert select_appearance_policy(original, **args) == value


@pytest.mark.parametrize('change,code', [
    ({'required_features': ['form', 'scale', 'coverage', 'texture'],
      'optional_features': ['lighting', 'atmosphere']}, 'scene_appearance_material_budget_exceeded'),
    ({'optional_features': ['lighting', 'atmosphere']}, 'scene_appearance_material_budget_exceeded'),
    ({'quality': {'structural_errors': 'block', 'optional_degradation': 'forbid'}},
     'scene_appearance_optional_downgrade_forbidden'),
])
def test_required_absent_and_forbidden_texture_reductions_cannot_claim_success(change, code):
    with pytest.raises(ServiceError, match=code):
        select_appearance_policy(intent(**change), **inputs(material_noise_limit=299))
    # Sufficient budget preserves full quality even when degradation is forbidden.
    assert select_appearance_policy(intent(**change), **inputs())['actual_parameters']['material_quality'] == 'full'


def test_no_feasible_profile_stops_instead_of_lowering_output_or_resetting_spending():
    with pytest.raises(ServiceError, match='scene_appearance_material_budget_exceeded'):
        select_appearance_policy(intent(), **inputs(material_noise_limit=99))


@pytest.mark.parametrize('key', ['estimated_material_samples', 'material_noise_limit'])
@pytest.mark.parametrize('value', [-1, True, 1.0, '1', None, float('nan'), float('inf'), 64 * 1024 * 1024 + 1])
def test_work_budget_types_and_bounds_are_strict(key, value):
    with pytest.raises(ServiceError):
        select_appearance_policy(intent(), **inputs(**{key: value}))


def test_zero_budget_is_not_promoted_to_an_unlimited_or_unknown_allowance():
    with pytest.raises(ServiceError):
        select_appearance_policy(intent(), **inputs(material_noise_limit=0, estimated_material_samples=0))
    maximum = MAX_MATERIAL_NOISE_EVALUATIONS
    value = select_appearance_policy(intent(), **inputs(estimated_material_samples=maximum,
        material_noise_limit=maximum))
    assert value['actual_parameters']['material_quality'] == 'coarse'
    assert value['estimated_requested_noise_evaluations'] == maximum * 3


@pytest.mark.parametrize('usage_known', [False, None, 0, 1, 'true'])
def test_usage_must_be_known_from_the_host_before_either_selection_or_validation(usage_known):
    with pytest.raises(ServiceError):
        select_appearance_policy(intent(), **inputs(), usage_known=usage_known)
    value = select_appearance_policy(intent(), **inputs())
    with pytest.raises(ServiceError):
        validate_appearance_policy(value, usage_known=usage_known)


@pytest.mark.parametrize('key', ['source_sha256', 'plan_sha256', 'surface_runtime_sha256',
    'configuration_sha256', 'usage_sha256'])
def test_unfrozen_identity_cannot_be_used_as_budget_evidence(key):
    with pytest.raises(ServiceError):
        select_appearance_policy(intent(), **inputs(**{key: 'not-a-frozen-hash'}))


@pytest.mark.parametrize('key,value', [
    ('source_sha256', 'f' * 64), ('plan_sha256', 'f' * 64), ('surface_runtime_sha256', 'f' * 64),
    ('configuration_sha256', 'f' * 64), ('usage_sha256', 'f' * 64), ('usage_lineage', 'another-lineage'),
    ('estimated_material_samples', 1), ('material_noise_limit', 400), ('intent', intent(camera_mode='static')),
])
def test_independent_host_binding_detects_even_a_resealed_changed_budget_or_identity(key, value):
    args = inputs(); policy = select_appearance_policy(intent(), **args)
    policy[key] = value; reseal(policy)
    with pytest.raises(ServiceError, match='scene_appearance_policy_binding_mismatch'):
        validate_appearance_policy(policy, intent=intent(), **args)


@pytest.mark.parametrize('field,value', [
    ('actual_parameters', {'material_quality': 'full', 'noise_bands': 3, 'normal_strength': 'authored'}),
    ('estimated_selected_noise_evaluations', 0), ('reason', 'no_budget_problem'), ('expected_loss', 'none'),
    ('scope', 'all_films'), ('estimate_scope', 'guaranteed_walltime'),
    ('requested_capability', 'coverage'), ('actual_capability', 'pbr'),
])
def test_resealed_false_execution_quality_or_evidence_claims_are_rejected(field, value):
    policy = select_appearance_policy(intent(), **inputs(material_noise_limit=100))
    policy[field] = value; reseal(policy)
    with pytest.raises(ServiceError, match='scene_appearance_policy_decision_changed'):
        validate_appearance_policy(policy)


def test_policy_digest_and_exact_fields_are_checked_before_execution():
    value = select_appearance_policy(intent(), **inputs())
    changed = deepcopy(value); changed['estimated_selected_noise_evaluations'] = 0
    with pytest.raises(ServiceError, match='scene_appearance_policy_identity_changed'):
        validate_appearance_policy(changed)
    for changed in [dict(value, user_approved=True), {k: v for k, v in value.items() if k != 'expected_loss'}]:
        with pytest.raises(ServiceError):
            validate_appearance_policy(changed)
    checked = validate_appearance_policy(value)
    checked['intent']['required_features'].clear()
    assert value['intent']['required_features']


def test_canvas_budget_policy_does_not_grant_other_engine_admission():
    with pytest.raises(ServiceError, match='scene_appearance_engine_not_supported'):
        select_appearance_policy(intent(engine='three'), **inputs())
