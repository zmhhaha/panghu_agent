"""Owner/source-bound gateway over the executor's private review facade.

The workbench uses its existing executor credential. It never receives the
generic Agent token or a capability to inspect arbitrary Agent job IDs.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import math
import re
import time
from urllib.parse import quote

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_review as visual
from .animation_creation_http import CreationTransportError
from .production_sequence_store import require, source_ref, sha, text


PREFIX = '/api/film-reviews'


class FilmReviewGateway:
    profile = visual.REVIEW_PACKED_PROFILE
    profile_revision = '1'

    def __init__(self, client):
        require(callable(getattr(client, '_request', None)), 'film_review_transport_required', 503)
        self.transport = client

    def for_context(self, owner, project_id, source, *, source_sha256):
        return _BoundFilmReviewGateway(self.transport, owner, project_id, source, source_sha256)


class _BoundFilmReviewGateway:
    profile = visual.REVIEW_PACKED_PROFILE
    profile_revision = '1'

    def __init__(self, transport, owner, project_id, source, source_sha256):
        self.transport, self.owner = transport, text(owner)
        self.binding = dict(project_id=text(project_id), source=source_ref(source), source_sha256=sha(source_sha256))
        # Driver cancellation intentionally uses the same narrow facade.
        self.client = self

    def _request(self, method, path, body=None):
        try:
            return self.transport._request(self.owner, method, PREFIX + path, body)
        except CreationTransportError as error:
            raise ServiceError(error.code, error.status) from None

    @staticmethod
    def _operation(value):
        require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}', value) is not None,
            'film_review_operation_invalid', 422)
        return quote(value, safe='')

    def preflight(self, deadline_at):
        require(type(deadline_at) in (int, float) and math.isfinite(deadline_at)
            and time.time() < deadline_at <= time.time() + 601, 'film_review_deadline_invalid', 409)
        capabilities = self._request('GET', '/capabilities')
        require(type(capabilities) is dict and capabilities.get('enabled') is True
            and capabilities.get('profile') == self.profile, 'film_review_unavailable', 503)
        return capabilities

    def upload(self, document, files):
        # Upload is intentionally delayed until the durable submission claim;
        # this preparatory method only validates and freezes local bytes.
        visual.validate_review_input(document)
        expected = {visual.REVIEW_INPUT_NAME: canonical_bytes(document)}
        for frame in document['observation']['frames']:
            raw = files.get(frame['file_name']) if type(files) is dict else None
            require(type(raw) is bytes and hashlib.sha256(raw).hexdigest() == frame['sha256'],
                'film_review_frame_changed', 409)
            expected[frame['file_name']] = raw
        require(type(files) is dict and files == expected, 'film_review_files_changed', 409)
        return deepcopy(expected)

    def prepare_submission(self, operation_id, document, deadline_at, input_refs):
        self._operation(operation_id)
        files = self.upload(document, input_refs)
        require(document['source_sha256'] == self.binding['source_sha256']
            and document['media']['sha256'] == self.binding['source']['sha256'], 'film_review_source_changed', 409)
        self.preflight(deadline_at)
        body = dict(schema='video.film-review-request.v1', operation_id=operation_id, **deepcopy(self.binding),
            deadline_at=deadline_at, document=deepcopy(document),
            files={name: base64.b64encode(raw).decode('ascii') for name, raw in files.items()})
        def submit():
            result = self._request('POST', '/operations', body)
            require(type(result) is dict and result.get('job_id') == operation_id,
                'film_review_response_invalid', 502)
            return result
        return submit

    def lookup(self, operation_id):
        result = self._request('POST', '/operations/' + self._operation(operation_id) + '/lookup', deepcopy(self.binding))
        require(type(result) is dict and result.get('job_id') == operation_id,
            'film_review_response_invalid', 502)
        return result

    def poll(self, job_id):
        return self.lookup(job_id)

    def _result(self, job_id):
        result = self._request('POST', '/operations/' + self._operation(job_id) + '/result', deepcopy(self.binding))
        require(type(result) is dict and set(result) == {'result', 'model_frame_receipt'},
            'film_review_response_invalid', 502)
        return result

    def collect(self, job_id, checksum, document):
        require(digest(document) == checksum, 'film_review_input_changed', 409)
        result = self._result(job_id)
        return visual.validate_review_result(result['result'], checksum, document)

    def model_frame_receipt(self, job_id):
        return self._result(job_id)['model_frame_receipt']

    def cancel(self, job_id):
        return self._request('POST', '/operations/' + self._operation(job_id) + '/cancel', deepcopy(self.binding))

    def cancel_operation(self, operation_id):
        """Cancel a queued facade task without a lookup that could advance it."""
        return self.cancel(operation_id)
