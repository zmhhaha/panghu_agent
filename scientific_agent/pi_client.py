"""Authenticated /v1 Pi client. Construction/import never performs network I/O.

The injectable transport accepts (method, url, headers, JSON payload, timeout)
and returns (HTTP status, headers, bytes). It must not retry mutating requests.
"""
from __future__ import annotations
import json
import re
import urllib.request
import urllib.error
from urllib.parse import urlencode, quote
from .pi_contracts import validate_task_id, ContractError


class WorkerError(Exception):
    def __init__(self, status, code, message, retryable=False):
        super().__init__(message)
        self.status, self.code, self.retryable = status, code, retryable


def urllib_transport(method, url, headers, payload, timeout):
    req = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode('utf-8'), headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class PiClient:
    def __init__(self, base_url='', token='', transport=None, timeout=10):
        self.base_url, self.token = base_url.rstrip('/'), token
        self.transport, self.timeout = transport or urllib_transport, timeout

    def request(self, method, path, payload=None, binary=False):
        if not self.base_url or not self.token:
            raise WorkerError(503, 'configuration_missing', 'Pi worker is not configured', True)
        try:
            status, headers, body = self.transport(method, self.base_url + '/v1' + path,
                {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}, payload, self.timeout)
        except Exception as exc:
            raise WorkerError(503, 'worker_unavailable', 'Pi worker is temporarily unreachable', True) from exc
        if binary and 200 <= status < 300:
            return body, headers
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('Expected object')
        except (ValueError, UnicodeDecodeError, TypeError) as exc:
            raise WorkerError(503, 'worker_unavailable', 'Invalid worker response', True) from exc
        if not 200 <= status < 300:
            err = data.get('error', {})
            if not isinstance(err, dict):
                err = {}
            code = err.get('code', 'worker_unavailable')
            if not isinstance(code, str) or not re.fullmatch(r'[a-z_]{1,80}', code):
                code = 'worker_unavailable'
            # Never forward control credentials, internal URLs, or arbitrary upstream text.
            messages = {'user_busy':'You already have an active Pi task', 'queue_full':'Pi queue is full',
                        'task_conflict':'Task ID is bound to different input', 'artifact_expired':'Artifact has expired',
                        'result_not_ready':'Result is not ready', 'runtime_not_ready':'Pi runtime is not ready',
                        'publication_conflict':'Report publication conflict', 'stale_attempt':'Publication attempt is stale'}
            raise WorkerError(status, code, messages.get(code, 'Pi worker request failed'), err.get('retryable') is True or status >= 500)
        return data

    @staticmethod
    def _task(task_id):
        try:
            return '/tasks/' + validate_task_id(task_id)
        except ContractError as exc:
            raise WorkerError(exc.status_code, exc.code, str(exc)) from exc

    def put(self, job): return self.request('PUT', self._task(job['task_id']), job)
    def get(self, task_id): return self.request('GET', self._task(task_id))
    def cancel(self, task_id): return self.request('POST', self._task(task_id) + '/cancel', {})
    def result(self, task_id): return self.request('GET', self._task(task_id) + '/result')
    def pending(self, cursor=None):
        params = {'publication': 'pending', 'limit': 50}
        if cursor: params['cursor'] = cursor
        return self.request('GET', '/tasks?' + urlencode(params))
    def ack(self, task_id, payload): return self.request('POST', self._task(task_id) + '/publication-ack', payload)
    def artifacts(self, task_id): return self.request('GET', self._task(task_id) + '/artifacts')
    def artifact(self, task_id, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', artifact_id):
            raise WorkerError(422, 'invalid_artifact', 'Invalid artifact identifier')
        return self.request('GET', self._task(task_id) + '/artifacts/' + quote(artifact_id, safe=''), binary=True)
    def health(self): return self.request('GET', '/health')
