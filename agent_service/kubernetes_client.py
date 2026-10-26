"""Small bounded in-cluster Kubernetes transport. No kubectl or ambient config."""
from __future__ import annotations

from pathlib import Path
import ssl

import httpx

from .contracts import ServiceError


class KubernetesClient:
    def __init__(self, *, origin='https://kubernetes.default.svc', token_reader,
                 client: httpx.Client):
        # The production constructor below fixes this origin and TLS authority.
        self.origin, self.token_reader, self.client = origin.rstrip('/'), token_reader, client

    @classmethod
    def from_service_account(cls):
        root = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        # Projected tokens use Kubernetes-managed symlinks and rotate. Read the
        # fixed trusted path per request; never accept a path from a Job.
        def read_token():
            with (root / 'token').open('rb') as stream:
                raw = stream.read(16385)
            if not 32 <= len(raw) <= 16384:
                raise ValueError('Invalid service account token')
            value = raw.decode('ascii').strip()
            if not 32 <= len(value) <= 16384 or any(not 33 <= ord(c) <= 126 for c in value):
                raise ValueError('Invalid service account token')
            return value
        authority = ssl.create_default_context(cafile=str(root / 'ca.crt'))
        return cls(token_reader=read_token, client=httpx.Client(
            verify=authority, timeout=5, trust_env=False, follow_redirects=False))

    def request(self, method, path, body=None, *, content_type='application/json'):
        if not path.startswith('/api/v1/namespaces/') or '\\' in path or '..' in path:
            raise ServiceError('kubernetes_path_invalid', 503)
        try:
            token = self.token_reader()
            with self.client.stream(method, self.origin + path,
                    headers={'Authorization': 'Bearer ' + token,
                             'Accept-Encoding': 'identity', 'Content-Type': content_type},
                    json=body, follow_redirects=False) as response:
                if response.status_code == 404:
                    raise ServiceError('kubernetes_not_found', 404)
                if response.status_code == 409:
                    raise ServiceError('kubernetes_conflict', 409)
                if response.status_code not in {200, 201, 202}:
                    raise ServiceError('kubernetes_request_rejected', 503)
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ValueError()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ValueError()
                    chunks.append(chunk)
            import json
            def pairs(items):
                result = {}
                for k, v in items:
                    if k in result:
                        raise ValueError()
                    result[k] = v
                return result
            value = json.loads(b''.join(chunks), object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ServiceError:
            raise
        except Exception:
            # API responses, auth headers and HTTP error text are private.
            raise ServiceError('kubernetes_unavailable', 503) from None

    def close(self):
        self.client.close()
