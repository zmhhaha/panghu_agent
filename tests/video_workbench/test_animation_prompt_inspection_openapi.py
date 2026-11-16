"""The inspection API labels provenance and exports real frozen application inputs."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from agent_service.contracts import digest
from test_animation_method_service import work, start


jsonschema = pytest.importorskip('jsonschema')
CONTRACT = Path(__file__).resolve().parents[2] / 'video_workbench/animation_lab_web/agent-api.openapi.json'


def document():
    return json.loads(CONTRACT.read_text(encoding='utf-8'))


def validator(name):
    contract = document()
    schema = {'$ref': '#/components/schemas/' + name, 'components': contract['components']}
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_inspection_routes_are_separate_read_only_cookie_authenticated_gets():
    contract = document()
    for path in ('/commissions/{commission_id}/prompt-inspection',
                 '/commissions/{commission_id}/operations/{operation_id}/input'):
        route = contract['paths'][path]
        assert set(route) == {'parameters', 'get'}
        operation = route['get']
        assert operation.get('security', contract['security']) == [{'OAuthSessionCookie': []}]
        assert 'requestBody' not in operation
        assert set(operation['responses']) >= {'200', '403', '404', '413'}


@pytest.mark.parametrize('historical', [False, True])
def test_real_service_submission_provenance_and_reserved_input_match_openapi(work, historical):
    service, creator, reviewer, renderer = work
    if historical:
        row = service.commissions.create('developer', dict(goal='Original record was not captured',
            budget=dict(creator=2, review=1, render=1, seconds=300), duration_seconds=18), str(uuid4()))
    else:
        row = start(service)
    run = service.commissions.get('developer', row['id'])
    frozen = service._document('developer', run, 'plan')
    operation = service.commissions.reserve('developer', row['id'], 'creator', frozen, 'plan')
    index = service.prompt_inspection('developer', row['id'])
    validator('PromptInspection').validate(index)
    assert index['submission']['is_original'] is not historical
    assert index['submission']['sha256'] == digest(index['submission']['document'])
    exported = service.operation_input('developer', row['id'], operation['id'])
    validator('OperationInput').validate(exported)
    assert exported['input'] == operation['input']
    assert exported['input_sha256'] == digest(frozen)
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_unavailable_input_is_an_explicit_index_item_without_a_download_url(work):
    service, _, _, _ = work
    row = start(service)
    run = service.commissions.get('developer', row['id'])
    frozen = service._document('developer', run, 'plan')
    frozen['constraints']['claim_token'] = 'not-for-export'
    service.commissions.reserve('developer', row['id'], 'creator', frozen, 'plan')
    index = service.prompt_inspection('developer', row['id'])
    validator('PromptInspection').validate(index)
    operation = index['operations'][0]
    assert operation['available'] is False and operation['input_url'] is None
    assert operation['unavailable_reason'] == 'animation_prompt_inspection_private_data'
    assert 'not-for-export' not in json.dumps(index)


@pytest.mark.parametrize('origin,is_original', [
    ('captured_request', False), ('reconstructed_frozen_fields', True),
])
def test_reconstructed_fields_cannot_be_labelled_as_original_submission(origin, is_original):
    submission = dict(origin=origin, is_original=is_original, action='unknown',
                      document={'goal': 'A reconstructed goal'}, sha256='a' * 64, limitations=[])
    assert list(validator('SubmissionInspection').iter_errors(submission))


def test_operation_export_contract_requires_actual_input_not_a_goal_or_prompt_summary():
    exported = dict(schema='video.animation-operation-input.v1', commission_id='commission-1',
        operation_id='plan-1', stage='creator', mode='plan', round=0,
        input_schema='video.animation-method-input.v1', input_sha256='a' * 64,
        input={'goal': 'Frozen document'}, limitations=[])
    validator('OperationInput').validate(exported)
    for change in ({'input': 'A prose summary'}, {'stage': 'render'}, {'input_sha256': 'unknown'}):
        invalid = deepcopy(exported)
        invalid.update(change)
        assert list(validator('OperationInput').iter_errors(invalid))
