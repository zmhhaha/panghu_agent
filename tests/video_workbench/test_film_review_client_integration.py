"""Workbench client -> authenticated private facade -> frozen v4 Agent gateway.

All network requests are local/offline test peers; no paid model or aesthetic
claim is involved. This verifies the real composed protocol and image custody.
"""
import base64

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_review_gateway import AnimationInputReviewGateway
from video_workbench.animation_observation import validate_model_frame_receipt
from video_workbench.film_review_client import FilmReviewGateway
from video_workbench.production_refinement_driver import ProductionRefinementDriver
from test_film_review_proxy import OWNER, TOKEN, work, serving


def test_private_client_and_real_review_gateway_preserve_owner_source_and_original_frames(work):
    service, _, peer, request = work
    with serving(CreationHTTPServer(object(), TOKEN, pump=False, film_review_service=service)) as server:
        client = MethodLabClient('http://127.0.0.1:' + str(server.server_port), TOKEN)
        bound = FilmReviewGateway(client).for_context(OWNER, request['project_id'], request['source'],
            source_sha256=request['source_sha256'])
        files = {key: base64.b64decode(value) for key, value in request['files'].items()}
        submit = bound.prepare_submission(request['operation_id'], request['document'], request['deadline_at'],
            bound.upload(request['document'], files))
        assert peer.count('POST', '/v1/jobs') == 0
        assert submit()['job_id'] == request['operation_id']
        bound.lookup(request['operation_id'])
        remote = peer.request['business_ref'][len(AnimationInputReviewGateway.business_prefix):]
        peer.complete(remote, request['document'])
        assert bound.poll(request['operation_id'])['state'] == 'completed'
        result = bound.collect(request['operation_id'], digest(request['document']), request['document'])
        control = bound.model_frame_receipt(request['operation_id'])
        assert result['input_sha256'] == digest(request['document'])
        assert control['job_id'] != request['operation_id']
        assert validate_model_frame_receipt(request['document'], control)
        other = FilmReviewGateway(client).for_context('other-owner', request['project_id'], request['source'],
            source_sha256=request['source_sha256'])
        with pytest.raises(ServiceError, match='film_review_not_found'):
            other.lookup(request['operation_id'])
        assert peer.count('POST', '/v1/jobs') == 1


def test_cancel_queued_proxy_operation_does_not_first_submit_it(work):
    service, _, peer, request = work
    with serving(CreationHTTPServer(object(), TOKEN, pump=False, film_review_service=service)) as server:
        client = MethodLabClient('http://127.0.0.1:' + str(server.server_port), TOKEN)
        bound = FilmReviewGateway(client).for_context(OWNER, request['project_id'], request['source'],
            source_sha256=request['source_sha256'])
        files = {key: base64.b64decode(value) for key, value in request['files'].items()}
        bound.prepare_submission(request['operation_id'], request['document'], request['deadline_at'], files)()
        driver = object.__new__(ProductionRefinementDriver)
        driver.reviewer = FilmReviewGateway(client)
        driver.cancel('review', request['operation_id'], dict(owner=OWNER, project_id=request['project_id'],
            source=request['source'], film_sha256=request['source_sha256']), {})
        assert bound.poll(request['operation_id'])['state'] == 'cancelled'
        assert peer.count('POST', '/v1/jobs') == 0
