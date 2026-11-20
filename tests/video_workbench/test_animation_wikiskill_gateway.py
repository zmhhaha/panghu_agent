"""Real generic-Agent HTTP bindings with synthetic offline compilation output."""
import hashlib

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.animation_wikiskill_contract import build_input, input_files
from video_workbench.animation_wikiskill_gateway import AnimationWikiSkillGateway
from test_animation_review_gateway import Peer, JOB, ATTEMPT, ARTIFACT
from test_animation_wikiskill import snapshot, result_for


def complete(peer, operation_id, document):
    result = result_for(document)
    peer.content = canonical_bytes(result)
    peer.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT,
        name=AnimationWikiSkillGateway.result_name, size=len(peer.content),
        sha256=hashlib.sha256(peer.content).hexdigest())])
    peer.job = dict(job_id=JOB, state='completed', publication_authorized=False,
        business_ref=AnimationWikiSkillGateway.business_prefix + operation_id,
        pins=dict(profile=AnimationWikiSkillGateway.profile, profile_revision='1'),
        attempt=dict(attempt_id=ATTEMPT, fence=1), stop_proven=True,
        artifact_manifest_sha256=peer.manifest['sha256'],
        stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1,
            request_sha256='c' * 64, result_sha256='d' * 64,
            resource_ids=['synthetic-stopped-compiler'], observed_at=1000.))
    return result


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time', lambda: 1000.)
    peer = Peer(AnimationWikiSkillGateway)
    frozen = snapshot()
    document = build_input(frozen)
    with AgentClient('http://agent.invalid', 'offline-compiler-token-' + 'a' * 32,
            transport=httpx.MockTransport(peer)) as client:
        yield AnimationWikiSkillGateway(client), peer, document, input_files(frozen, document)


def test_manifest_and_each_record_are_real_bound_input_files(wire):
    gateway, peer, document, files = wire
    refs = gateway.upload(document, files)
    gateway.submit('compile-one', document, 1250., refs)
    assert peer.uploads == files
    assert peer.request['profile'] == gateway.profile
    assert peer.request['task'] == {'input_sha256': digest(document)}
    assert {row['path'] for row in peer.request['inputs']} == set(files)
    assert {row['role'] for row in peer.request['inputs']} == {
        'animation_wikiskill_input', 'animation_wikiskill_record'}
    assert peer.count('POST', '/v1/jobs') == 1


@pytest.mark.parametrize('now,route_limit,expected_timeout', [
    (1050., 900, 900), (1050., 600, 600), (1100., 900, 850), (1000., 1200, 900)])
def test_new_publication_keeps_native_route_bound_and_reserves_outer_collection(
        wire, monkeypatch, now, route_limit, expected_timeout):
    gateway, peer, document, files = wire
    peer.profiles[0]['max_timeout_seconds'] = route_limit
    refs = gateway.upload(document, files)
    monkeypatch.setattr('video_workbench.animation_wikiskill_gateway.time.time', lambda: now)
    gateway.prepare_submission('new-budget', document, 2200., refs,
        collection_reserve_seconds=250)()
    assert peer.request['timeout_seconds'] == expected_timeout
    assert peer.request['execution_deadline_at'] == 1950.
    assert 2200. - peer.request['execution_deadline_at'] == 250.
    assert peer.count('POST', '/v1/jobs') == 1


def test_unmarked_gateway_request_retains_original_absolute_deadline(wire):
    gateway, peer, document, files = wire
    peer.profiles[0]['max_timeout_seconds'] = 900
    gateway.submit('historical-budget', document, 1900., gateway.upload(document, files))
    assert peer.request['timeout_seconds'] == 900
    assert peer.request['execution_deadline_at'] == 1900.


@pytest.mark.parametrize('reserve', [True, 249, 251, 250.0])
def test_unrecognized_publication_reserve_never_posts_job(wire, reserve):
    gateway, peer, document, files = wire
    with pytest.raises(ServiceError, match='publication_timing_invalid'):
        gateway.prepare_submission('invalid-budget', document, 2200., gateway.upload(document, files),
            collection_reserve_seconds=reserve)
    assert peer.count('POST', '/v1/jobs') == 0


def test_exhausted_native_window_never_borrows_collection_margin(wire):
    gateway, peer, document, files = wire
    with pytest.raises(ServiceError, match='deadline_expired'):
        gateway.prepare_submission('exhausted-budget', document, 1250., gateway.upload(document, files),
            collection_reserve_seconds=250)
    assert peer.count('POST', '/v1/jobs') == 0


def test_stopped_manifest_and_artifact_are_verified_before_result_is_accepted(wire):
    gateway, peer, document, files = wire
    gateway.submit('compile-one', document, 1250., gateway.upload(document, files))
    expected = complete(peer, 'compile-one', document)
    assert gateway.stopped(peer.job)
    assert gateway.collect(JOB, digest(document), document) == expected
    peer.content += b' '
    with pytest.raises(ServiceError, match='artifact_digest_mismatch'):
        gateway.collect(JOB, digest(document), document)


@pytest.mark.parametrize('mutation', ['fence', 'attempt', 'profile', 'resource', 'manifest'])
def test_forged_or_unbound_stop_and_manifest_cannot_publish(wire, mutation):
    gateway, peer, document, files = wire
    gateway.submit('compile-one', document, 1250., gateway.upload(document, files))
    complete(peer, 'compile-one', document)
    if mutation == 'fence':
        peer.job['stop_proof']['fence'] += 1
    elif mutation == 'attempt':
        peer.job['stop_proof']['attempt_id'] = ARTIFACT
    elif mutation == 'profile':
        peer.job['pins']['profile'] = 'video.animation-method-author.v2'
    elif mutation == 'resource':
        peer.job['stop_proof']['resource_ids'] = []
    else:
        peer.job['artifact_manifest_sha256'] = '0' * 64
    if mutation != 'manifest':
        assert not gateway.stopped(peer.job)
    with pytest.raises(ServiceError):
        gateway.collect(JOB, digest(document), document)


def test_changed_record_is_rejected_before_any_upload_or_admission(wire):
    gateway, peer, document, files = wire
    files[document['records'][0]['file_name']] = b'{}'
    with pytest.raises(ServiceError, match='binding_mismatch'):
        gateway.upload(document, files)
    assert not peer.calls


def test_unknown_response_lookup_uses_same_key_without_another_post(wire):
    gateway, peer, document, files = wire
    refs = gateway.upload(document, files)
    peer.lose_response = True
    with pytest.raises(ServiceError):
        gateway.submit('compile-one', document, 1250., refs)
    assert gateway.lookup('compile-one')['job_id'] == JOB
    assert peer.count('POST', '/v1/jobs') == 1
    assert peer.calls[-1].url.path.endswith('/wikiskill-compile-one')
