"""The image critic has one bounded, credential-isolated HTTP attempt."""
import json

import httpx
import pytest

from video_workbench.animation_lab_model import ModelError, VisualModel
from video_workbench.motion import sha256


TOKEN = 'test-caller-token-do-not-persist-12345'
MESSAGES = [
    dict(role='system', content='Review supplied visual evidence only.'),
    dict(role='user', content=[dict(type='text', text='Frame A at 1.0 seconds.'),
        dict(type='image_url', image_url=dict(url='data:image/png;base64,aW1hZ2U='))]),
]


def response_document(**overrides):
    value = dict(id='resp-reviewed-1', model='actual-model', status='completed',
        output=[dict(type='message', content=[dict(type='output_text', text='{"preference":"A"}')])],
        usage=dict(input_tokens=27, output_tokens=9, total_tokens=36))
    return dict(value, **overrides)


def invoke(handler, *, messages=MESSAGES):
    model = VisualModel('https://llm.invalid/v1', TOKEN, 'llm-trusted',
        transport=httpx.MockTransport(handler))
    try:
        return model.review(messages)
    finally:
        model.close()


def test_responses_preserves_real_image_payload_and_selected_receipt_only():
    calls = []
    document = response_document(secret='must-not-save',
        usage=dict(input_tokens=27, output_tokens=9, total_tokens=36,
            provider_cost='not billing evidence', input_tokens_details={'cached_tokens': 5}))
    raw = json.dumps(document).encode()

    def peer(request):
        calls.append(request)
        assert request.method == 'POST' and str(request.url) == 'https://llm.invalid/v1/responses'
        assert request.headers['Authorization'] == 'Bearer ' + TOKEN
        assert request.headers['Accept-Encoding'] == 'identity'
        body = json.loads(request.content)
        assert body['model'] == 'llm-trusted' and body['store'] is False
        assert 0 < body['max_output_tokens'] <= 8192
        assert body['input'] == [
            dict(role='system', content=[dict(type='input_text', text=MESSAGES[0]['content'])]),
            dict(role='user', content=[dict(type='input_text', text='Frame A at 1.0 seconds.'),
                dict(type='input_image', image_url='data:image/png;base64,aW1hZ2U=')])]
        return httpx.Response(200, content=raw, headers={
            'X-LLM-Route-Revision': 'route-20261107', 'X-Untrusted-Debug': TOKEN})

    output, receipt = invoke(peer)
    assert output == '{"preference":"A"}' and len(calls) == 1
    assert receipt['actual_model'] == 'actual-model'
    assert receipt['route_revision'] == 'route-20261107'
    assert receipt['response_sha256'] == sha256(raw)
    assert receipt['usage'] == dict(input_tokens=27, output_tokens=9, total_tokens=36)
    assert receipt['usage_scope'] == 'provider_reported_not_billing_evidence'
    assert receipt['post_attempts'] == 1 and receipt['automatic_retries'] == 0
    assert receipt['image_input_count'] == 1
    saved = json.dumps(receipt)
    assert TOKEN not in saved and 'must-not-save' not in saved and 'cached_tokens' not in saved


def test_redirect_never_forwards_bearer_to_another_origin():
    calls = []
    def peer(request):
        calls.append(request)
        return httpx.Response(307, headers={'location': 'https://other.invalid/collect'})
    with pytest.raises(ModelError, match='visual_model_redirect_rejected'):
        invoke(peer)
    assert len(calls) == 1 and calls[0].url.host == 'llm.invalid'


@pytest.mark.parametrize('status', [400, 401, 429, 500, 503])
def test_remote_failures_stop_without_retry_or_persisting_upstream_errors(status):
    calls = []
    def peer(request):
        calls.append(request)
        return httpx.Response(status, json={'error': {'detail': TOKEN}})
    with pytest.raises(ModelError) as error:
        invoke(peer)
    assert str(error.value) == 'visual_model_http_' + str(status)
    assert TOKEN not in str(error.value) and len(calls) == 1


def test_timeout_is_an_unknown_outcome_without_automatic_retry():
    calls = []
    def peer(request):
        calls.append(request)
        raise httpx.ReadTimeout('upstream echoed ' + TOKEN, request=request)
    with pytest.raises(ModelError) as error:
        invoke(peer)
    assert str(error.value) == 'visual_model_transport_outcome_unknown'
    assert TOKEN not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize('raw,headers,code', [
    (b'x' * (1024 * 1024 + 1), {}, 'visual_model_response_too_large'),
    (b'not-json', {}, 'visual_model_response_invalid'),
    (b'[]', {}, 'visual_model_response_incomplete'),
    (json.dumps(response_document(status='incomplete')).encode(), {}, 'visual_model_response_incomplete'),
    (json.dumps(response_document(output=[])).encode(), {}, 'visual_model_response_invalid'),
    (json.dumps(response_document(output=[dict(type='message', content=[
        dict(type='output_text', text='one'), dict(type='output_text', text='two')])])).encode(),
        {}, 'visual_model_response_invalid'),
    (json.dumps(response_document(output=[dict(type='message', content=[
        dict(type='output_text', text='x' * (64 * 1024 + 1))])])).encode(),
        {}, 'visual_model_response_invalid'),
    (b'', {'content-encoding': 'unsupported-compression'}, 'visual_model_encoding_rejected'),
], ids=['oversized-body', 'non-json', 'non-object', 'incomplete', 'missing-output',
        'multiple-output-texts', 'oversized-output-text', 'unexpected-encoding'])
def test_bounded_protocol_errors_have_no_usable_review(raw, headers, code):
    with pytest.raises(ModelError, match=code):
        invoke(lambda request: httpx.Response(200, content=raw, headers=headers))


def test_receipt_rejects_untrusted_metadata_and_noninteger_usage():
    _, receipt = invoke(lambda request: httpx.Response(200,
        json=response_document(id='credential copied\ninto metadata', model='bad model name',
            usage=dict(input_tokens=True, output_tokens=-1, total_tokens=1.5)),
        headers={'X-LLM-Route-Revision': 'unvalidated metadata'}))
    assert receipt['response_id'] is receipt['actual_model'] is receipt['route_revision'] is None
    assert receipt['usage'] == {}


def configured_env(monkeypatch):
    for key in ('LLM_SERVICE_TOKEN', 'LLM_SERVICE_TOKEN_FILE', 'HTTP_PROXY', 'HTTPS_PROXY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LLM_BASE_URL', 'http://llm-service.llm.svc.cluster.local/v1')
    monkeypatch.setenv('LLM_MODEL', 'llm-trusted')


def test_projected_token_file_is_consumed_only_by_the_http_client(tmp_path, monkeypatch):
    configured_env(monkeypatch)
    secret = tmp_path / 'caller-token'
    secret.write_text(TOKEN + '\n', encoding='utf-8')
    monkeypatch.setenv('LLM_SERVICE_TOKEN_FILE', str(secret))
    model = VisualModel.from_env()
    try:
        assert model.client.headers['Authorization'] == 'Bearer ' + TOKEN
        assert model.model == 'llm-trusted'
        assert TOKEN not in repr(model) and TOKEN not in repr(model.client)
        assert 'LLM_SERVICE_TOKEN' not in __import__('os').environ
    finally:
        model.close()


@pytest.mark.parametrize('sources', ['none', 'both'])
def test_missing_or_ambiguous_credential_sources_are_rejected(tmp_path, monkeypatch, sources):
    configured_env(monkeypatch)
    if sources == 'both':
        monkeypatch.setenv('LLM_SERVICE_TOKEN', TOKEN)
        monkeypatch.setenv('LLM_SERVICE_TOKEN_FILE', str(tmp_path / 'not-read'))
    with pytest.raises(ModelError, match='visual_model_one_credential_required'):
        VisualModel.from_env()


def test_oversized_token_file_rejected_before_loading_contents(tmp_path, monkeypatch):
    configured_env(monkeypatch)
    secret = tmp_path / 'caller-token'
    secret.write_text(TOKEN * 200, encoding='utf-8')
    monkeypatch.setenv('LLM_SERVICE_TOKEN_FILE', str(secret))
    with pytest.raises(ModelError) as error:
        VisualModel.from_env()
    assert str(error.value) == 'visual_model_credential_file_invalid'
    assert str(secret) not in str(error.value) and TOKEN not in str(error.value)


def test_request_size_limit_fails_before_any_network_call():
    calls = []
    with pytest.raises(ModelError, match='visual_model_request_too_large'):
        invoke(lambda request: calls.append(request), messages=[dict(role='user', content='x' * (16*1024*1024))])
    assert calls == []
