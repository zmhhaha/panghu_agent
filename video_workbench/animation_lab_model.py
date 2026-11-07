"""Caller-only llm-service image review transport. One POST, no retries.

The renderer, developer page and persisted review request never receive service
credentials. A transport timeout is an unknown remote outcome, not permission
to submit the same operation again.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

class ModelError(ValueError):
    pass


def _require(value, code):
    if not value:
        raise ModelError(code)


def _safe(value, maximum=200):
    return value if type(value) is str and re.fullmatch(r'[A-Za-z0-9_./:@-]{1,' + str(maximum) + '}', value) else None


class VisualModel:
    def __init__(self, base_url, token, model, *, timeout=60, transport=None):
        try:
            parsed = urlsplit(base_url)
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except (ValueError, TypeError):
            raise ModelError('visual_model_origin_invalid') from None
        _require(parsed.scheme in {'https', 'http'} and parsed.hostname and parsed.username is None
                 and parsed.password is None and not parsed.query and not parsed.fragment and valid_port
                 and parsed.path in {'', '/', '/v1', '/v1/'}
                 and all(ord(c) > 32 for c in base_url), 'visual_model_origin_invalid')
        _require(type(token) is str and 16 <= len(token) <= 4096
                 and all(33 <= ord(c) <= 126 for c in token), 'visual_model_credential_invalid')
        _require(_safe(model) is not None and type(timeout) in (int, float) and 0 < timeout <= 120,
                 'visual_model_config_invalid')
        self.model = model
        self.origin = parsed.scheme + '://' + parsed.netloc
        self.client = httpx.Client(base_url=self.origin, timeout=timeout, follow_redirects=False,
            trust_env=False, transport=transport,
            headers={'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'})

    @classmethod
    def from_env(cls):
        token, token_file = os.environ.get('LLM_SERVICE_TOKEN'), os.environ.get('LLM_SERVICE_TOKEN_FILE')
        _require(bool(token) != bool(token_file), 'visual_model_one_credential_required')
        if token_file:
            path = Path(token_file)
            _require(path.is_file() and path.stat().st_size <= 4096, 'visual_model_credential_file_invalid')
            try:
                token = path.read_text(encoding='utf-8').strip()
            except (OSError, UnicodeError):
                raise ModelError('visual_model_credential_file_invalid') from None
        return cls(os.environ.get('LLM_BASE_URL', ''), token, os.environ.get('LLM_MODEL', ''))

    def close(self):
        self.client.close()

    def review(self, messages):
        inputs = []
        for row in messages:
            content = row['content']
            if type(content) is str:
                content = [{'type': 'text', 'text': content}]
            converted = []
            for part in content:
                if part['type'] == 'text':
                    converted.append(dict(type='input_text', text=part['text']))
                elif part['type'] == 'image_url':
                    converted.append(dict(type='input_image', image_url=part['image_url']['url']))
                else:
                    raise ModelError('visual_model_content_invalid')
            inputs.append(dict(role=row['role'], content=converted))
        payload = dict(model=self.model, input=inputs, store=False, max_output_tokens=6000)
        _require(len(json.dumps(payload).encode('utf-8')) <= 16 * 1024 * 1024,
                 'visual_model_request_too_large')
        try:
            with self.client.stream('POST', '/v1/responses', json=payload) as response:
                _require(not response.is_redirect, 'visual_model_redirect_rejected')
                _require(response.headers.get('content-encoding', 'identity').lower() == 'identity',
                         'visual_model_encoding_rejected')
                raw = bytearray()
                for part in response.iter_bytes():
                    _require(len(raw) + len(part) <= 1024 * 1024, 'visual_model_response_too_large')
                    raw.extend(part)
                _require(200 <= response.status_code < 300, 'visual_model_http_' + str(response.status_code))
                document = json.loads(raw)
                _require(type(document) is dict and document.get('status', 'completed') == 'completed',
                         'visual_model_response_incomplete')
                output = document.get('output')
                _require(type(output) is list, 'visual_model_response_invalid')
                texts = [part.get('text') for item in output if type(item) is dict and item.get('type') == 'message'
                         for part in item.get('content', []) if type(part) is dict and part.get('type') == 'output_text']
                _require(len(texts) == 1 and type(texts[0]) is str
                         and len(texts[0].encode('utf-8')) <= 64 * 1024, 'visual_model_response_invalid')
                # Record only non-sensitive selected receipt fields, not arbitrary
                # headers, request bodies, upstream errors or HTTP exception text.
                usage = document.get('usage', {})
                usage = {key: value for key, value in usage.items()
                         if key in {'input_tokens', 'output_tokens', 'total_tokens'}
                         and type(value) is int and value >= 0} if type(usage) is dict else {}
                return texts[0], dict(schema='video.animation-lab-model-receipt.v1',
                    configured_model=self.model, actual_model=_safe(document.get('model')),
                    route_revision=_safe(response.headers.get('X-LLM-Route-Revision')),
                    response_id=_safe(document.get('id')), response_sha256=hashlib.sha256(bytes(raw)).hexdigest(),
                    usage=usage, usage_scope='provider_reported_not_billing_evidence',
                    post_attempts=1, automatic_retries=0, image_input_count=sum(
                        part['type'] == 'input_image' for row in inputs for part in row['content']))
        except (httpx.HTTPError, OSError):
            raise ModelError('visual_model_transport_outcome_unknown') from None
        except (UnicodeError, json.JSONDecodeError, AttributeError, TypeError, KeyError):
            raise ModelError('visual_model_response_invalid') from None
