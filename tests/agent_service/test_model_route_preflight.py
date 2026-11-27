"""Readiness metadata can reject the actual alias/model mix-up without spending."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.model_route_preflight import validate_model_route


REVISION = 'fe4ce4894a71e7b9812f7264'


def readiness():
    return dict(status='ready', aliases=2, credentialed=2, callers=1, routes={
        'llm-trusted': dict(requested_alias='llm-trusted', resolved_upstream='custom',
            configured_model='gpt-6.2-sol', tier='trusted', route_revision=REVISION, wire_api='responses'),
        'custom-trusted': dict(requested_alias='custom-trusted', resolved_upstream='custom',
            configured_model='gpt-6.2-sol', tier='trusted', route_revision=REVISION, wire_api='responses')})


def check(document=None, **changes):
    options=dict(http_status=200, public_alias='llm-trusted', expected_configured_model='gpt-6.2-sol',
        expected_route_revision=REVISION, expected_upstream='custom', expected_tier='trusted',
        expected_wire_api='responses')
    options.update(changes)
    return validate_model_route(readiness() if document is None else document, **options)


def test_public_alias_and_effective_backend_are_explicit_and_input_is_unchanged():
    document=readiness();before=deepcopy(document)
    result=check(document)
    assert document==before
    assert result['public_alias']=='llm-trusted' and result['configured_model']=='gpt-6.2-sol'
    assert result['route_revision']==REVISION and result['readiness_sha256']==digest(document)
    assert result['alias_rewritten'] is False and result['model_requests']==0 and result['job_created'] is False


def test_backend_name_used_as_wire_alias_is_rejected_before_the_job_callback():
    created=[]
    with pytest.raises(ServiceError,match='model_route_preflight_alias_unavailable'):
        route=check(public_alias='gpt-6.2-sol')
        created.append(route)
    assert created==[]
    # Neither of the two aliases targeting that model is selected as a fallback.
    assert set(readiness()['routes'])=={'llm-trusted','custom-trusted'}


@pytest.mark.parametrize('change,code',[
    ({'expected_configured_model':'gpt-6.1-sol'},'target_mismatch'),
    ({'expected_route_revision':'earlier-route'},'revision_mismatch'),
    ({'expected_upstream':'openai'},'resolved_upstream_mismatch'),
    ({'expected_tier':'guarded'},'tier_mismatch'),
    ({'expected_wire_api':'chat_completions'},'wire_api_mismatch'),
])
def test_changed_target_revision_or_transport_policy_is_rejected(change,code):
    with pytest.raises(ServiceError,match=code):check(**change)


@pytest.mark.parametrize('status',[503,400,True,'200',None])
def test_http_failure_cannot_be_hidden_by_a_ready_looking_body(status):
    with pytest.raises(ServiceError,match='not_ready'):check(http_status=status)


@pytest.mark.parametrize('change',[
    lambda r:r.update(status='ok'),
    lambda r:r.pop('routes'),
    lambda r:r.update(routes=[]),
    lambda r:r['routes']['llm-trusted'].pop('requested_alias'),
    lambda r:r['routes']['llm-trusted'].update(requested_alias='custom-trusted'),
    lambda r:r['routes']['llm-trusted'].update(configured_model=None),
    lambda r:r['routes']['llm-trusted'].update(route_revision=''),
    lambda r:r['routes']['llm-trusted'].pop('wire_api'),
])
def test_missing_or_malformed_actual_metadata_cannot_authorize_work(change):
    document=readiness();change(document)
    with pytest.raises(ServiceError,match='model_route_preflight_'):check(document)


@pytest.mark.parametrize('changes',[
    {'public_alias':''}, {'public_alias':' llm-trusted'},
    {'expected_configured_model':None}, {'expected_route_revision':None},
    {'expected_route_revision':''}, {'expected_upstream':'https://secret.invalid/?token=value'},
])
def test_missing_revision_or_invalid_expectations_fail_without_echoing_values(changes):
    with pytest.raises(ServiceError,match='expectation_invalid') as failure:check(**changes)
    assert 'token=value' not in str(failure.value)


def test_unrelated_fields_are_not_returned_as_public_route_evidence():
    document=readiness();document['debug_secret']='do-not-export'
    document['routes']['llm-trusted']['upstream_url']='https://hidden.invalid/?token=value'
    result=check(document)
    assert 'do-not-export' not in str(result) and 'token=value' not in str(result)
    assert result['readiness_sha256']==digest(document)


def test_a_later_backend_change_rejects_the_same_pinned_expectation():
    document=readiness();assert check(document)['route_revision']==REVISION
    document['routes']['llm-trusted'].update(configured_model='replacement-model',route_revision='new-route')
    with pytest.raises(ServiceError,match='target_mismatch'):check(document)
