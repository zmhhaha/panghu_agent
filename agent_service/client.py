"""Small service-to-service client: fixed origin, bounded responses, no retries."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .contracts import MAX_ARTIFACT_BYTES, MAX_INPUT_BYTES, JobRequest, ServiceError, safe_name


class AgentClient:
    def __init__(self, base_url: str, token: str, *, timeout=10.0, transport=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}
                or any(ord(c) < 33 for c in base_url)):
            raise ValueError('An explicit service HTTP(S) origin is required')
        if (not isinstance(token, str) or not 32 <= len(token) <= 512
                or any(not 33 <= ord(c) <= 126 for c in token)):
            raise ValueError('A configured service credential is required')
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError('A bounded HTTP timeout is required')
        self._client = httpx.Client(base_url=base_url.rstrip('/'),
                                    headers={'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'},
                                    timeout=timeout, follow_redirects=False,
                                    trust_env=False, transport=transport)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _id(value):
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('A server-issued canonical UUID is required') from None
        return value

    def _request(self, method, path, *, limit=1024 * 1024, raw=False, **kwargs):
        try:
            with self._client.stream(method, path, **kwargs) as response:
                if response.is_redirect:
                    raise ServiceError('service_redirect_rejected', 502)
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ServiceError('service_protocol_invalid', 502)
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > limit:
                        raise ServiceError('service_response_too_large', 502)
                    body.extend(chunk)
                if response.status_code >= 400:
                    code = 'service_request_failed'
                    try:
                        candidate = json.loads(body).get('detail', {}).get('code')
                        if isinstance(candidate, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', candidate):
                            code = candidate
                    except (ValueError, AttributeError, TypeError, RecursionError):
                        pass
                    raise ServiceError(code, response.status_code)
                if not 200 <= response.status_code < 300:
                    raise ServiceError('service_protocol_invalid', 502)
                if raw:
                    return bytes(body)
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except (httpx.HTTPError, OSError):
            raise ServiceError('service_unavailable', 503) from None
        except (ValueError, UnicodeError, RecursionError):
            raise ServiceError('service_protocol_invalid', 502) from None

    def capabilities(self):
        return self._request('GET', '/v1/capabilities')

    def upload_input(self, name: str, content: bytes):
        safe_name(name)
        if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_INPUT_BYTES:
            raise ValueError('Input exceeds its byte limit')
        return self._request('POST', '/v1/inputs', params={'name': name}, content=content,
                             headers={'Content-Type': 'application/octet-stream'})

    def submit(self, request: JobRequest | dict, *, idempotency_key: str):
        if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128
                or any(not 33 <= ord(c) <= 126 for c in idempotency_key)):
            raise ValueError('An explicit idempotency key is required')
        try:
            parsed = request if isinstance(request, JobRequest) else JobRequest.model_validate(request)
        except (ValueError, TypeError, RecursionError):
            raise ServiceError('invalid_request', 422) from None
        return self._request('POST', '/v1/jobs', json=parsed.model_dump(),
                             headers={'Idempotency-Key': idempotency_key})

    def get_job(self, job_id: str):
        return self._request('GET', '/v1/jobs/' + self._id(job_id))

    def events(self, job_id: str, *, after=0, limit=100):
        if type(after) is not int or not 0 <= after <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid event page')
        return self._request('GET', '/v1/jobs/' + self._id(job_id) + '/events',
                             params={'after': after, 'limit': limit})

    def cancel(self, job_id: str):
        return self._request('POST', '/v1/jobs/' + self._id(job_id) + '/cancel', json={})

    def artifacts(self, job_id: str):
        return self._request('GET', '/v1/jobs/' + self._id(job_id) + '/artifacts')

    def download_artifact(self, artifact_id: str):
        return self._request('GET', '/v1/artifacts/' + self._id(artifact_id) + '/content',
                             limit=MAX_ARTIFACT_BYTES, raw=True)


ExecutionClient = AgentClient
