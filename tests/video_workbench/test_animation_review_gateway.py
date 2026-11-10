"""Offline HTTP proof for multi-file visual and freeform creation Agent jobs."""
import base64
from copy import deepcopy
import hashlib
import json
from uuid import UUID

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from video_workbench.animation_review_gateway import (
    AnimationObserveGateway, AnimationReviewGateway, AnimationCreateGateway,
    AnimationSequenceReviewGateway)


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII=')
JOB, ATTEMPT, ARTIFACT = [str(UUID(int=i)) for i in (800, 801, 802)]


def visual_document(review=True):
    document = dict(schema=visual.REVIEW_INPUT_SCHEMA if review else visual.OBSERVE_INPUT_SCHEMA,
        clip_id='whole-expression', goal='Explain an arbitrary process clearly.',
        media=dict(sha256='a' * 64, duration_seconds=4, width=1280, height=720),
        observation=dict(coverage='sampled_frames', audio_available=False,
            windows=[dict(start_seconds=0, end_seconds=4)], limitations=['Two sampled frames only.'],
            frames=[dict(frame_id=f'frame-{i}', file_name=f'frame-{i}.png', time_seconds=i,
                         sha256=hashlib.sha256(PNG).hexdigest()) for i in (1, 2)]),
        prompt_revision='review-r1')
    if review:
        document['source_sha256'] = 'b' * 64
    return document


def creation_document():
    return dict(schema=creation.INPUT_SCHEMA, creation_id='arbitrary-fragment',
        goal='Invent an expressive visual explanation.', guidance='', prompt_revision='create-r1',
        constraints=dict(width=1280, height=720, fps=24, duration_seconds=13),
        assets={}, base_source=None, review=None)


def result_document(profile, document):
    if profile == creation.PROFILE:
        source = dict(engine='html-svg', strategy=dict(description='Continuous topology change.', tradeoffs=''),
            files={'scene.html': '<!doctype html><svg></svg><script>window.__animationExperiment={seek(t){}};</script>'})
        return dict(schema=creation.RESULT_SCHEMA, input_sha256=digest(document),
            prompt_revision=document['prompt_revision'], **source,
            source_sha256=creation.source_sha256(source), base_source_sha256=None, review_sha256=None,
            files_sha256={key:hashlib.sha256(value.encode()).hexdigest() for key,value in source['files'].items()})
    review = profile in {visual.REVIEW_PROFILE, visual.REVIEW_SEQUENCE_PROFILE}
    result = dict(schema=visual.REVIEW_RESULT_SCHEMA if review else visual.OBSERVE_RESULT_SCHEMA,
        input_sha256=digest(document), prompt_revision=document['prompt_revision'],
        media_sha256=document['media']['sha256'], coverage='sampled_frames', evidence_scope='visual_only',
        frame_evidence_status='supplied_only', observed_frame_ids=['frame-1', 'frame-2'],
        summary='The available samples establish no concrete defect.',
        limitations=document['observation']['limitations'])
    if review:
        result.update(source_sha256=document['source_sha256'], issues=[])
    else:
        result['candidates'] = []
    return result


class Peer:
    def __init__(self, gateway_class):
        self.calls, self.job, self.request, self.uploads = [], None, None, {}
        self.gateway_class = gateway_class
        self.profiles = [dict(profile=gateway_class.profile, profile_revision='1', configured=True,
            ready=True, supports_inputs=True, max_timeout_seconds=600,
            required_model_capabilities=['text', 'tool_calls'] +
                (['vision'] if gateway_class.visual_input else []))]
        self.lose_response = False
        self.bad_upload = None
        self.receipt_change = {}

    def __call__(self, request):
        self.calls.append(request)
        method, path = request.method, request.url.path
        if path == '/v1/capabilities':
            return httpx.Response(200, json=dict(profiles=self.profiles))
        if path == '/v1/inputs' and method == 'POST':
            name = request.url.params['name']
            self.uploads[name] = request.content
            receipt = dict(input_id=str(UUID(int=1000 + len(self.uploads))), name=name,
                size=len(request.content), sha256=hashlib.sha256(request.content).hexdigest())
            if self.bad_upload == name:
                receipt['sha256'] = '0' * 64
            return httpx.Response(201, json=receipt)
        if path == '/v1/jobs' and method == 'POST':
            self.request = json.loads(request.content)
            self.job = dict(job_id=JOB, state='queued', business_ref=self.request['business_ref'],
                            publication_authorized=False)
            if self.lose_response:
                raise httpx.ReadTimeout('lost admission response', request=request)
            return httpx.Response(202, json=self.job)
        if path.startswith('/v1/jobs/by-idempotency/') or path == '/v1/jobs/' + JOB:
            return httpx.Response(200, json=self.job) if self.job else httpx.Response(
                404, json=dict(detail=dict(code='job_not_found')))
        if path == '/v1/jobs/' + JOB + '/artifact-manifest':
            return httpx.Response(200, json=self.manifest)
        if path == '/v1/artifacts/' + ARTIFACT + '/content':
            return httpx.Response(200, content=self.content)
        if path == '/v1/jobs/' + JOB + '/model-image-evidence':
            receipt = dict(schema='agent-model-image-evidence.v1', job_id=JOB,
                attempt_id=ATTEMPT, request_sha256='c' * 64,
                evidence_scope='completed_upstream_transport',
                images=[dict(sha256=hashlib.sha256(PNG).hexdigest(), model_request_completed=True,
                    model_request_sha256='d' * 64, media_type='image/png', byte_length=len(PNG),
                    input_item_index=1, part_index=0)])
            receipt.update(self.receipt_change)
            return httpx.Response(200, json=receipt)
        raise AssertionError((method, path))

    def complete(self, operation_id, document):
        result = result_document(self.gateway_class.profile, document)
        self.content = canonical_bytes(result)
        self.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT,
            name=self.gateway_class.result_name, size=len(self.content), sha256=hashlib.sha256(self.content).hexdigest())])
        self.job = dict(job_id=JOB, state='completed', publication_authorized=False,
            business_ref=self.gateway_class.business_prefix + operation_id,
            pins=dict(profile=self.gateway_class.profile, profile_revision='1'),
            attempt=dict(attempt_id=ATTEMPT, fence=1), stop_proven=True,
            artifact_manifest_sha256=self.manifest['sha256'],
            stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1,
                request_sha256='c' * 64, result_sha256='d' * 64,
                resource_ids=['isolated-agent'], observed_at=1000.0))
        return result

    def count(self, method, path):
        return sum(request.method == method and request.url.path == path for request in self.calls)


@pytest.fixture(params=[AnimationObserveGateway, AnimationReviewGateway, AnimationCreateGateway,
                       AnimationSequenceReviewGateway])
def wire(request, monkeypatch):
    gateway_class = request.param
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time', lambda: 1000.0)
    peer = Peer(gateway_class)
    with AgentClient('http://agent.invalid', 'offline-animation-token-' + 'a' * 32,
                     transport=httpx.MockTransport(peer)) as client:
        gateway = gateway_class(client)
        doc = (visual_document(gateway_class is not AnimationObserveGateway) if gateway.visual_input
               else creation_document())
        files = {gateway.input_name:canonical_bytes(doc), **(
            {frame['file_name']:PNG for frame in doc['observation']['frames']} if gateway.visual_input else {})}
        yield gateway, peer, doc, files


def test_all_inputs_upload_before_the_single_job_submission(wire):
    gateway, peer, doc, files = wire
    refs = gateway.upload(doc, files)
    assert len(refs) == len(files)
    assert peer.uploads == files
    assert peer.count('POST', '/v1/jobs') == 0
    gateway.submit('polish-operation', doc, 1200, refs)
    assert peer.request['inputs'] == refs
    assert peer.request['task'] == dict(input_sha256=digest(doc))
    assert peer.request['profile'] == gateway.profile
    assert peer.request['execution_deadline_at'] == 1200
    assert peer.count('POST', '/v1/jobs') == 1
    assert ([request.url.path for request in peer.calls if request.method == 'POST'] ==
            ['/v1/inputs'] * len(files) + ['/v1/jobs'])


def test_bad_upload_receipt_cannot_submit_a_partial_bundle(wire):
    gateway, peer, doc, files = wire
    peer.bad_upload = list(files)[-1]
    with pytest.raises(ServiceError):
        gateway.upload(doc, files)
    assert peer.count('POST', '/v1/jobs') == 0


def test_preparation_finishes_readiness_before_the_admission_callable(wire):
    gateway, peer, doc, files = wire
    refs = gateway.upload(doc, files)
    admit = gateway.prepare_submission('polish-operation', doc, 1200, refs)
    assert peer.count('POST', '/v1/jobs') == 0
    reads_before = peer.count('GET', '/v1/capabilities')
    assert reads_before == 1
    admit()
    assert peer.count('GET', '/v1/capabilities') == reads_before
    assert peer.count('POST', '/v1/jobs') == 1


@pytest.mark.parametrize('change', ['not_ready', 'duplicate_route', 'missing_capabilities', 'wrong_profile',
                                    'incomplete_refs', 'wrong_frame_sha', 'duplicate_input_id'])
def test_profile_readiness_and_all_input_bindings_are_required_before_post(wire, change):
    gateway, peer, doc, files = wire
    refs = gateway.upload(doc, files)
    if change == 'not_ready':
        peer.profiles[0]['ready'] = False
    elif change == 'duplicate_route':
        peer.profiles *= 2
    elif change == 'missing_capabilities':
        peer.profiles[0]['required_model_capabilities'] = ['text']
    elif change == 'wrong_profile':
        peer.profiles[0]['profile'] = 'video.scene.v1'
    elif change == 'incomplete_refs':
        refs.pop()
    elif change == 'wrong_frame_sha':
        refs[-1]['sha256'] = '0' * 64
    else:
        if len(refs) == 1:
            refs.append(deepcopy(refs[0]))
        else:
            refs[-1]['input_id'] = refs[0]['input_id']
    with pytest.raises(ServiceError):
        gateway.submit('polish-operation', doc, 1200, refs)
    assert peer.count('POST', '/v1/jobs') == 0


def test_lost_response_is_recovered_only_by_the_original_key(wire):
    gateway, peer, doc, files = wire
    refs = gateway.upload(doc, files)
    peer.lose_response = True
    with pytest.raises(ServiceError, match='service_unavailable'):
        gateway.submit('polish-operation', doc, 1200, refs)
    assert gateway.lookup('polish-operation')['job_id'] == JOB
    assert peer.count('POST', '/v1/jobs') == 1
    assert peer.calls[-1].url.path.endswith(gateway.idempotency_prefix + 'polish-operation')


def test_collection_reuses_stopped_artifact_validation_and_profile_input_binding(wire):
    gateway, peer, doc, _ = wire
    result = peer.complete('polish-operation', doc)
    assert gateway.collect(JOB, digest(doc), doc) == result
    peer.job['stop_proven'] = False
    with pytest.raises(ServiceError):
        gateway.collect(JOB, digest(doc), doc)


@pytest.mark.parametrize('change', [{}, {'job_id':str(UUID(int=900))}, {'attempt_id':str(UUID(int=901))},
    {'request_sha256':'0' * 64}, {'evidence_scope':None}, {'evidence_scope':'model_understood'}])
def test_image_control_receipt_is_bound_to_the_completed_stopped_attempt(wire, change):
    gateway, peer, doc, _ = wire
    peer.complete('polish-operation', doc)
    peer.receipt_change = change
    if not gateway.visual_input:
        with pytest.raises(ServiceError, match='animation_visual_profile_required'):
            gateway.model_frame_receipt(JOB)
    elif change:
        with pytest.raises(ServiceError, match='animation_frame_receipt_binding_mismatch'):
            gateway.model_frame_receipt(JOB)
    else:
        receipt = gateway.model_frame_receipt(JOB)
        assert receipt['job_id'] == JOB and receipt['attempt_id'] == ATTEMPT
        assert receipt['request_sha256'] == peer.job['stop_proof']['request_sha256']


def test_visual_upload_rejects_substituted_frames_before_any_http(wire):
    gateway, peer, doc, files = wire
    if not gateway.visual_input:
        return
    files['frame-1.png'] = b'not-the-frame'
    with pytest.raises(ServiceError, match='animation_frame_integrity'):
        gateway.upload(doc, files)
    assert peer.calls == []


def test_visual_model_must_explicitly_declare_vision_support(wire):
    gateway, peer, doc, files = wire
    if not gateway.visual_input:
        return
    refs = gateway.upload(doc, files)
    peer.profiles[0]['required_model_capabilities'] = ['text', 'tool_calls']
    with pytest.raises(ServiceError, match='animation_model_capability_unavailable'):
        gateway.submit('polish-operation', doc, 1200, refs)
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('change', ['wrong_fence', 'wrong_profile', 'unstopped'])
def test_image_receipt_cannot_bypass_the_completed_attempt_proof(wire, change):
    gateway, peer, doc, _ = wire
    if not gateway.visual_input:
        return
    peer.complete('polish-operation', doc)
    if change == 'wrong_fence':
        peer.job['stop_proof']['fence'] = 2
    elif change == 'wrong_profile':
        peer.job['pins']['profile'] = creation.PROFILE
    else:
        peer.job['stop_proven'] = False
    with pytest.raises(ServiceError, match='animation_frame_receipt_attempt_unproven'):
        gateway.model_frame_receipt(JOB)
    assert peer.count('GET', '/v1/jobs/' + JOB + '/model-image-evidence') == 0
