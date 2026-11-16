"""The external JSON contract can express tool choices without inventing runtime admission."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip('jsonschema')
WEB = Path(__file__).resolve().parents[2] / 'video_workbench' / 'animation_lab_web'


def document():
    return json.loads((WEB / 'agent-api.openapi.json').read_text(encoding='utf-8'))


def validator(name):
    contract = document()
    schema = {'$ref': '#/components/schemas/' + name, 'components': contract['components']}
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def request():
    return deepcopy(document()['paths']['/commissions']['post']['requestBody']['content']
                    ['application/json']['examples']['newAnimation']['value'])


@pytest.mark.parametrize('selection', [
    {}, {'creation': []}, {'motion': []}, {'creation': [], 'motion': []},
    {'creation': ['canvas']}, {'motion': ['animejs']},
    {'creation': ['html-svg', 'canvas', 'three'], 'motion': ['native-js', 'gsap', 'animejs']},
    {'creation': ['manim']},
    {'creation': ['manim'], 'motion': []},
    {'creation': ['canvas', 'manim'], 'motion': ['gsap']},
])
def test_explicit_automatic_single_and_combined_choices_match_backend_selection_shape(selection):
    from video_workbench.animation_creation_selection import normalize_selection

    validator('CapabilitySelection').validate(selection)
    assert normalize_selection(selection) == selection
    body = request()
    body['capability_selection'] = selection
    validator('CommissionRequest').validate(body)


@pytest.mark.parametrize('selection', [
    None, [], 'canvas', {'creation': 'canvas'}, {'motion': True},
    {'creation': ['canvas', 'canvas']}, {'motion': ['gsap', 'gsap']},
    {'creation': ['Canvas 2D']}, {'motion': ['manim']},
    {'creation': ['gsap']}, {'engine': 'canvas'}, {'motion': ['unknown']},
])
def test_invalid_or_ambiguous_tool_selection_is_rejected(selection):
    from agent_service.contracts import ServiceError
    from video_workbench.animation_creation_selection import normalize_selection

    assert list(validator('CapabilitySelection').iter_errors(selection))
    with pytest.raises(ServiceError):
        normalize_selection(selection)


def test_catalog_separates_available_motion_tools_from_drawing_and_resource_producers():
    catalog = {'effects': [], 'commissions': [], 'csrf_token': 'test-csrf',
               'availability': {'enabled': True, 'allowed_engines': ['html-svg', 'canvas', 'three'],
                   'motion_tools': ['native-js', 'gsap', 'animejs'],
                   'resource_authoring': {'manim': {'enabled': True, 'max_frames': 60}}}}
    validator('Catalog').validate(catalog)
    catalog['availability']['motion_tools'] = ['manim']
    assert list(validator('Catalog').iter_errors(catalog))
    catalog['availability'].pop('motion_tools')
    validator('Catalog').validate(catalog)  # Older catalogs remain readable without claiming extra admission.


def test_detail_exposes_requested_choices_separately_from_resource_progress_and_render_evidence():
    detail = {'id': 'test-commission', 'request_id': '17c8a3ea-52b4-42d0-b875-f55ab241e2db',
              'title': 'test', 'goal': 'test', 'state': 'queued', 'best_candidate_id': None,
              'stop_reason': None, 'candidates': [], 'summary': None, 'error': None,
              'capability_selection': {'creation': ['manim'], 'motion': []},
              'resource_authoring': {'kind': 'manim', 'status': 'pending', 'error': None}}
    validator('CommissionDetail').validate(detail)
    # Read-only progress must not accidentally be accepted as the next resource request.
    assert list(validator('ManimResourceAuthoring').iter_errors(detail['resource_authoring']))
    detail.pop('capability_selection')
    validator('CommissionDetail').validate(detail)


@pytest.mark.parametrize('motion', [['native-js'], ['gsap'], ['animejs'], ['gsap', 'animejs']])
def test_new_manim_only_request_rejects_browser_motion_but_historical_choices_remain_readable(motion):
    selection = {'creation': ['manim'], 'motion': motion}
    body = request()
    body['capability_selection'] = selection
    assert list(validator('CommissionRequest').iter_errors(body))
    # This shape remains legal on old frozen details; only new submissions apply the cross-axis rule.
    validator('CapabilitySelection').validate(selection)
    detail = {'id': 'old-commission', 'request_id': body['request_id'],
              'title': 'old', 'goal': 'old', 'state': 'completed', 'best_candidate_id': None,
              'stop_reason': 'budget_exhausted', 'candidates': [], 'summary': None, 'error': None,
              'capability_selection': selection}
    validator('CommissionDetail').validate(detail)


@pytest.mark.parametrize('creation', [['canvas', 'manim'], ['manim', 'three'],
                                     ['manim', 'html-svg']])
def test_mixed_manim_candidate_keeps_browser_motion_and_optional_resource_parameters(creation):
    body = request()
    body['capability_selection'] = {'creation': creation, 'motion': ['gsap', 'animejs']}
    body['resource_authoring'] = {'kind': 'manim', 'frame_count': 48}
    validator('CommissionRequest').validate(body)


@pytest.mark.parametrize('adoption', [None, {
    'creation': ['canvas'], 'motion': ['gsap'], 'engine': 'canvas', 'uses_manim': False,
    'plan_operation_id': 'plan-1', 'plan_result_sha256': 'a' * 64,
}, {
    'creation': ['manim'], 'motion': [], 'engine': 'html-svg', 'uses_manim': True,
    'plan_operation_id': 'plan-2', 'plan_result_sha256': 'b' * 64,
}, {
    'creation': ['html-svg', 'manim'], 'motion': ['gsap'], 'engine': 'html-svg', 'uses_manim': True,
    'plan_operation_id': 'plan-3', 'plan_result_sha256': 'c' * 64,
}])
def test_host_adoption_is_read_only_and_separate_from_requested_pool_and_resource_progress(adoption):
    body = request()
    detail = {'id': 'planned-commission', 'request_id': body['request_id'],
              'title': 'plan', 'goal': 'plan', 'state': 'running', 'best_candidate_id': None,
              'stop_reason': None, 'candidates': [], 'summary': None, 'error': None,
              'capability_selection': {'creation': ['canvas', 'manim'], 'motion': ['gsap']},
              'adopted_capabilities': adoption,
              'resource_authoring': {'kind': 'manim', 'status': 'awaiting_plan', 'error': None}}
    if adoption is not None and not adoption['uses_manim']:
        detail['resource_authoring']['status'] = 'not_adopted'
        detail['resource_authoring']['usage'] = {'attempts': 0}
    validator('CommissionDetail').validate(detail)
    body['adopted_capabilities'] = adoption
    assert list(validator('CommissionRequest').iter_errors(body))


@pytest.mark.parametrize('field,value', [
    ('resource_planning', 'after-plan.v1'),
    ('adopted_capabilities_sha256', 'a' * 64),
    ('runtime_requirements', {'engine': 'canvas'}),
    ('plan_operation_id', 'caller-plan'),
])
def test_request_cannot_inject_host_planning_or_runtime_evidence(field, value):
    body = request()
    body[field] = value
    assert list(validator('CommissionRequest').iter_errors(body))


@pytest.mark.parametrize('change', [
    {'creation': []}, {'motion': ['manim']}, {'engine': 'manim'},
    {'uses_manim': 'false'}, {'plan_operation_id': ''}, {'plan_result_sha256': 'not-a-digest'},
])
def test_adoption_shape_requires_specific_host_decision_and_result_identity(change):
    adoption = {'creation': ['canvas'], 'motion': ['gsap'], 'engine': 'canvas',
                'uses_manim': False, 'plan_operation_id': 'plan-1', 'plan_result_sha256': 'a' * 64}
    adoption.update(change)
    assert list(validator('AdoptedCapabilities').iter_errors(adoption))


def test_manim_can_use_default_resource_request_and_valid_custom_frames_but_not_incompatible_modes():
    body = request()
    body['capability_selection'] = {'creation': ['manim'], 'motion': []}
    validator('CommissionRequest').validate(body)
    body['resource_authoring'] = {'kind': 'manim', 'frame_count': 48, 'global_start_frame': 0}
    validator('CommissionRequest').validate(body)
    for change in [{'frame_count': 61}, {'frame_count': False}, {'global_start_frame': -1}]:
        invalid = deepcopy(body)
        invalid['resource_authoring'].update(change)
        assert list(validator('CommissionRequest').iter_errors(invalid))
    body['capability_selection']['creation'] = ['canvas']
    assert list(validator('CommissionRequest').iter_errors(body))
    body.pop('resource_authoring')
    body['collaboration'] = {'max_components': 3}
    body['budget']['max_rounds'] = 1
    validator('CommissionRequest').validate(body)
    body['capability_selection']['creation'] = ['canvas', 'manim']
    assert list(validator('CommissionRequest').iter_errors(body))


def test_continuation_and_stopped_task_retry_can_omit_choices_for_server_inheritance():
    body = request()
    body.update(base_commission_id='old-effect', feedback='改善布局')
    validator('CommissionRequest').validate(body)
    body.pop('base_commission_id')
    body.pop('feedback')
    body['retry_of_commission_id'] = 'failed-effect'
    validator('CommissionRequest').validate(body)
    assert 'capability_selection' not in body


@pytest.mark.parametrize('selection', [
    {'creation': ['three'], 'motion': ['gsap']},
    {'creation': [], 'motion': []},
])
def test_stopped_task_retry_can_explicitly_choose_different_tools_or_restore_automatic(selection):
    body = request()
    body['retry_of_commission_id'] = 'failed-effect'
    body['capability_selection'] = selection
    validator('CommissionRequest').validate(body)


def test_new_api_examples_remain_optional_and_manim_example_does_not_require_a_browser_motion_tool():
    examples = document()['paths']['/commissions']['post']['requestBody']['content']['application/json']['examples']
    for example in examples.values():
        validator('CommissionRequest').validate(example['value'])
    assert 'capability_selection' not in examples['newAnimation']['value']
    assert examples['selectedCapabilities']['value']['capability_selection']['motion'] == ['animejs']
    assert examples['manimCreation']['value']['capability_selection'] == {'creation': ['manim'], 'motion': []}
    assert 'resource_authoring' not in examples['manimCreation']['value']
    assert examples['plannedManimCandidate']['value']['capability_selection'] == {
        'creation': ['canvas', 'manim'], 'motion': ['gsap']}
    explicit = examples['explicitManimResource']['value']
    assert 'capability_selection' not in explicit
    assert explicit['resource_authoring'] == {'kind': 'manim', 'frame_count': 48, 'global_start_frame': 0}
