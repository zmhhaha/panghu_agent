"""External callers can opt in without changing ordinary creation requests."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip('jsonschema')

WEB = Path(__file__).resolve().parents[2] / 'video_workbench' / 'animation_lab_web'


def contract():
    return json.loads((WEB / 'agent-api.openapi.json').read_text(encoding='utf-8'))


def validator(document):
    # Keep refs local; no network or authenticated API calls are needed.
    return jsonschema.Draft202012Validator({
        '$ref': '#/components/schemas/CommissionRequest',
        'components': document['components'],
    })


def test_ordinary_and_collaboration_examples_validate_with_existing_auth_and_limits():
    document = contract()
    examples = document['paths']['/commissions']['post']['requestBody']['content']['application/json']['examples']
    schema = validator(document)
    for example in examples.values():
        schema.validate(example['value'])
    assert 'collaboration' not in examples['newAnimation']['value']
    collaborative = examples['collaborativeAnimation']['value']
    assert collaborative['collaboration'] == {'max_components': 3}
    assert collaborative['budget'] == {'max_rounds': 1, 'max_seconds': 1800}
    assert document['paths']['/commissions']['post']['security'] == [
        {'OAuthSessionCookie': [], 'AnimationLabCsrf': []}]


@pytest.mark.parametrize('components', [None, False, 1, 5, 2.5, '3'])
def test_collaboration_rejects_invalid_component_limits(components):
    document = contract()
    value = deepcopy(document['paths']['/commissions']['post']['requestBody']['content']['application/json']
                     ['examples']['collaborativeAnimation']['value'])
    value['collaboration']['max_components'] = components
    assert list(validator(document).iter_errors(value))


def test_collaboration_rejects_extra_rounds_unknown_options_and_permits_continuation():
    document = contract()
    original = document['paths']['/commissions']['post']['requestBody']['content']['application/json']
    original = original['examples']['collaborativeAnimation']['value']
    for change in ('rounds', 'options'):
        value = deepcopy(original)
        if change == 'rounds':
            value['budget']['max_rounds'] = 2
        elif change == 'options':
            value['collaboration']['enabled'] = True
        assert list(validator(document).iter_errors(value)), change
    for components in (2, 3, 4):
        value = deepcopy(original)
        value['collaboration']['max_components'] = components
        validator(document).validate(value)
    value = deepcopy(original)
    value['base_commission_id'] = 'old-effect'
    value['feedback'] = '保留主体运动，改善部件之间的呼应'
    validator(document).validate(value)
