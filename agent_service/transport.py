"""Bounded authenticated worker transport; no redirects or inherited proxy."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .contracts import ExecutionEnvelope, ProfilePolicy, ServiceError, WorkerResult


@dataclass(frozen=True)
class WorkerEndpoint:
    base_url: str
    token: str = field(repr=False)
    receipt_key: bytes = field(repr=False)
    engine: str = 'pi'

    def __post_init__(self):
        p = urlsplit(self.base_url)
        if (p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password
                or p.path not in {'', '/'} or p.query or p.fragment
                or self.engine not in {'pi', 'hermes'}
                or len(self.token.encode()) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in self.token)
                or len(self.receipt_key) < 32):
            raise ValueError('Invalid trusted Worker endpoint')


class RemoteWorkerAdapter:
    """One assigned Worker capsule. Provisioning capsules belongs to the host.

    start is never automatically retried. inspect may reconcile a lost response
    using the already persisted attempt id; it cannot create a second execution.
    """
    def __init__(self, endpoint: WorkerEndpoint, *, client: httpx.Client | None = None):
        self.endpoint = endpoint
        self.receipt_key = endpoint.receipt_key
        self._client = client or httpx.Client(timeout=5, trust_env=False, follow_redirects=False)

    def _json(self, method: str, path: str, body=None):
        try:
            with self._client.stream(method, self.endpoint.base_url.rstrip('/') + path,
                                     headers={'Authorization': 'Bearer ' + self.endpoint.token,
                                              'Accept-Encoding': 'identity'},
                                     json=body, follow_redirects=False) as response:
                if response.status_code == 404:
                    raise ServiceError('worker_execution_missing', 404)
                if response.status_code not in {200, 201, 202}:
                    raise ServiceError('worker_request_rejected', 503)
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ServiceError('worker_protocol_invalid', 503)
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 12 * 1024 * 1024:
                        raise ServiceError('worker_response_too_large', 503)
                    chunks.append(chunk)
            import json
            value = json.loads(b''.join(chunks))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError, TypeError):
            raise ServiceError('worker_transport_unavailable', 503) from None

    def ready(self, policy: ProfilePolicy | None = None) -> bool:
        try:
            c = self._json('GET', '/v1/capabilities')
            if c.get('ready') is not True or c.get('engine') != self.endpoint.engine:
                return False
            return policy is None or (c.get('profile') == policy.name
                    and c.get('profile_revision') == policy.revision
                    and c.get('runtime_revision') == policy.runtime_revision
                    and c.get('image_digest') == policy.image_digest
                    and (not policy.supports_inputs or c.get('supports_inputs') is True))
        except ServiceError:
            return False

    def close(self):
        self._client.close()

    def start(self, envelope: ExecutionEnvelope):
        return self._json('PUT', f'/v1/executions/{envelope.attempt_id}', envelope.model_dump())

    def inspect(self, envelope: ExecutionEnvelope):
        return self._json('GET', f'/v1/executions/{envelope.attempt_id}')

    def renew(self, envelope: ExecutionEnvelope, lease_expires_at: float):
        return self._json('POST', f'/v1/executions/{envelope.attempt_id}/lease',
                          dict(fence=envelope.fence, lease_expires_at=lease_expires_at))

    def cancel(self, envelope: ExecutionEnvelope):
        return self._json('POST', f'/v1/executions/{envelope.attempt_id}/cancel', {})

    def result(self, envelope: ExecutionEnvelope) -> WorkerResult:
        try:
            return WorkerResult.model_validate(self._json('GET', f'/v1/executions/{envelope.attempt_id}/result'))
        except ValueError:
            raise ServiceError('worker_result_invalid', 503) from None
