"""Bound facade calls cannot address arbitrary upstream Agent jobs."""
from copy import deepcopy
import time

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_creation_http import CreationTransportError
from video_workbench.film_review_client import FilmReviewGateway
from test_animation_review_gateway import PNG, visual_document, result_document
from agent_service.profiles import video_animation_review as visual


OWNER, PROJECT = 'film-owner', 'film-project'
SOURCE = dict(run_id='formal-run', version_id='formal-render', sha256='a' * 64)
KEY = 'film-refinement:ad6634f3-b322-400f-8e56-b33213ef111e:review'


class Peer:
    def __init__(self):
        self.calls, self.enabled, self.error = [], True, None
        self.document = visual_document()

    def _request(self, owner, method, path, body=None):
        self.calls.append((owner, method, path, deepcopy(body)))
        if self.error:
            raise CreationTransportError(*self.error)
        if path.endswith('/capabilities'):
            return dict(enabled=self.enabled, profile=visual.REVIEW_PACKED_PROFILE)
        if path.endswith('/result'):
            return dict(result=result_document(visual.REVIEW_PACKED_PROFILE, self.document),
                model_frame_receipt=dict(schema='synthetic-control-receipt'))
        return dict(job_id=KEY, state='queued', stop_proven=False)


def bound(peer):
    return FilmReviewGateway(peer).for_context(OWNER, PROJECT, SOURCE, source_sha256='b' * 64)


def files(document):
    return {visual.REVIEW_INPUT_NAME: canonical_bytes(document), 'frame-1.png': PNG, 'frame-2.png': PNG}


def test_prepare_keeps_complete_real_input_bytes_until_single_claimed_post():
    peer = Peer()
    gateway = bound(peer)
    document = peer.document
    refs = gateway.upload(document, files(document))
    submit = gateway.prepare_submission(KEY, document, time.time() + 100, refs)
    assert all(method == 'GET' for _, method, _, _ in peer.calls)
    result = submit()
    assert result['job_id'] == KEY
    owner, method, path, request = peer.calls[-1]
    assert (owner, method, path) == (OWNER, 'POST', '/api/film-reviews/operations')
    assert request['source'] == SOURCE and request['project_id'] == PROJECT
    assert request['source_sha256'] == document['source_sha256']
    assert request['document'] == document
    assert set(request['files']) == set(files(document))


def test_lookup_result_control_and_cancel_retain_identical_owner_source_binding():
    peer = Peer()
    gateway = bound(peer)
    assert gateway.lookup(KEY)['job_id'] == KEY
    assert gateway.poll(KEY)['state'] == 'queued'
    result = gateway.collect(KEY, digest(peer.document), peer.document)
    assert result['evidence_scope'] == 'visual_only'
    assert gateway.model_frame_receipt(KEY)['schema'] == 'synthetic-control-receipt'
    gateway.client.cancel(KEY)
    expected = dict(project_id=PROJECT, source=SOURCE, source_sha256='b' * 64)
    assert all(owner == OWNER and method == 'POST' and body == expected
        for owner, method, _, body in peer.calls)


def test_source_swap_and_unadmitted_review_fail_before_post():
    peer = Peer()
    gateway = bound(peer)
    document = deepcopy(peer.document)
    document['source_sha256'] = 'c' * 64
    with pytest.raises(ServiceError, match='film_review_source_changed'):
        gateway.prepare_submission(KEY, document, time.time() + 100, gateway.upload(document, files(document)))
    peer.enabled = False
    with pytest.raises(ServiceError, match='film_review_unavailable'):
        gateway.preflight(time.time() + 100)
    assert not any(method == 'POST' for _, method, _, _ in peer.calls)


def test_transport_failure_is_single_attempt_and_never_leaks_private_body():
    peer = Peer()
    gateway = bound(peer)
    submit = gateway.prepare_submission(KEY, peer.document, time.time() + 100, files(peer.document))
    peer.error = ('animation_creation_executor_unavailable', 503)
    with pytest.raises(ServiceError, match='animation_creation_executor_unavailable'):
        submit()
    assert sum(method == 'POST' for _, method, _, _ in peer.calls) == 1


@pytest.mark.parametrize('key', ['../another', 'x?owner=bob', 'x\nAuthorization', 'x' * 97])
def test_operation_path_never_accepts_query_or_header_injection(key):
    peer = Peer()
    with pytest.raises(ServiceError, match='film_review_operation_invalid'):
        bound(peer).lookup(key)
    assert peer.calls == []
