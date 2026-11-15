"""One generic Agent/Pi compilation with immutable record files and no render."""
from __future__ import annotations

import hashlib
import math
import time

from agent_service.animation_wikiskill_contract import (
    INPUT_NAME, RESULT_NAME, INPUT_MAX_BYTES, OUTPUT_MAX_BYTES,
    validate_input, validate_input_files, validate_result, require,
)
from .agent import _uuid, _sha, _finite_number
from .animation_review_gateway import _AnimationGateway


def stopped_job(job, *, profile, revision='1'):
    """Verify the same attempt-bound writer-stop fields for unsuccessful jobs."""
    attempt, proof, pins = job.get('attempt'), job.get('stop_proof'), job.get('pins')
    return (type(attempt) is dict and _uuid(attempt.get('attempt_id'))
        and type(attempt.get('fence')) is int and attempt['fence'] >= 1
        and type(pins) is dict and pins.get('profile') == profile and pins.get('profile_revision') == revision
        and job.get('stop_proven') is True and type(proof) is dict and proof.get('stop_proven') is True
        and proof.get('attempt_id') == attempt['attempt_id'] and proof.get('fence') == attempt['fence']
        and type(proof.get('fence')) is int and _sha(proof.get('request_sha256'))
        and _sha(proof.get('result_sha256')) and type(proof.get('resource_ids')) is list
        and 1 <= len(proof['resource_ids']) <= 64
        and all(type(item) is str and 1 <= len(item) <= 200
                and all(ord(c) >= 32 and ord(c) != 127 for c in item) for item in proof['resource_ids'])
        and len(set(proof['resource_ids'])) == len(proof['resource_ids'])
        and _finite_number(proof.get('observed_at')) and proof['observed_at'] > 0)


class AnimationWikiSkillGateway(_AnimationGateway):
    profile = 'video.animation-wikiskill-compile.v1'
    input_name, result_name = INPUT_NAME, RESULT_NAME
    input_role = 'animation_wikiskill_input'
    business_prefix, idempotency_prefix = 'animation-wikiskill:', 'wikiskill-'
    input_limit, output_limit = INPUT_MAX_BYTES, OUTPUT_MAX_BYTES
    validate_input = staticmethod(validate_input)
    validate_result = staticmethod(validate_result)

    def _files(self, document, files=None):
        self._document_bytes(document)
        validate_input_files(document, files)
        return files

    def upload(self, document, files=None):
        refs = []
        for name, raw in self._files(document, files).items():
            checksum = hashlib.sha256(raw).hexdigest()
            uploaded = self._remote(self.client.upload_input, name, raw)
            require(type(uploaded) is dict and _uuid(uploaded.get('input_id'))
                and uploaded.get('name') == name and uploaded.get('sha256') == checksum
                and type(uploaded.get('size')) is int and uploaded['size'] == len(raw),
                'animation_wikiskill_upload_receipt_invalid', 502)
            refs.append(dict(input_id=uploaded['input_id'], sha256=checksum, path=name,
                role=self.input_role if name == self.input_name else 'animation_wikiskill_record'))
        require(len({row['input_id'] for row in refs}) == len(refs),
                'animation_wikiskill_input_ref_duplicate', 502)
        return refs

    def prepare_submission(self, operation_id, document, deadline_at, input_refs=None):
        key = self._operation_key(operation_id)
        raw = self._document_bytes(document)
        expected = {self.input_name: hashlib.sha256(raw).hexdigest(),
            **{row['file_name']: row['file_sha256'] for row in document['records']}}
        require(type(input_refs) is list and len(input_refs) == len(expected),
                'animation_wikiskill_input_refs_invalid')
        names, ids = set(), set()
        for ref in input_refs:
            require(type(ref) is dict and set(ref) == {'input_id', 'sha256', 'path', 'role'}
                and _uuid(ref.get('input_id')) and type(ref.get('path')) is str
                and ref['path'] in expected and ref['sha256'] == expected[ref['path']]
                and ref['role'] == (self.input_role if ref['path'] == self.input_name else 'animation_wikiskill_record')
                and ref['path'] not in names and ref['input_id'] not in ids,
                'animation_wikiskill_input_refs_invalid')
            names.add(ref['path'])
            ids.add(ref['input_id'])
        route = self.preflight(deadline_at)
        remaining = deadline_at - time.time()
        require(remaining > 0, 'animation_wikiskill_deadline_expired', 409)
        request = dict(api_version='1', profile=self.profile, profile_revision=self.profile_revision,
            business_ref=self.business_prefix + operation_id, task=dict(input_sha256=hashlib.sha256(raw).hexdigest()),
            inputs=[dict(row) for row in input_refs], execution_deadline_at=deadline_at,
            timeout_seconds=min(900, route['max_timeout_seconds'], math.ceil(remaining)))
        def admit():
            response = self._remote(self.client.submit, request, idempotency_key=key)
            return self._checked_job(response, operation_id=operation_id)
        return admit

    def stopped(self, job):
        return stopped_job(job, profile=self.profile, revision=self.profile_revision)
