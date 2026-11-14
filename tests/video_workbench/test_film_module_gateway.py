"""Actual client transport with stopped fixture artifacts, never a live model."""
from copy import deepcopy
import hashlib
from uuid import UUID

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_film_module as P
from video_workbench.creative_film_module_contract import collect_module_result
from video_workbench.film_module_gateway import FilmModuleAgentGateway
from test_creative_film_module_contract import stage, collected
from test_skill_production_gateway import Peer, JOB, TOKEN


class FrozenAdapter:
    def __init__(self, delivery):
        self.delivery = delivery

    def get_bundle(self, owner, consumer_id, checksum):
        catalog = self.delivery['catalog']
        assert (owner, consumer_id, checksum) == (
            catalog['recipient_owner'], catalog['consumer_id'], catalog['sha256'])
        return deepcopy(self.delivery)


@pytest.fixture
def wire(tmp_path, monkeypatch):
    monkeypatch.setattr('video_workbench.skill_production_gateway.time.time', lambda: 1000.0)
    staged = stage(tmp_path, count=2)
    document, delivery = staged[1:3]
    result, evidence, package = collected(staged)
    peer = Peer()
    peer.profile['profile'] = P.PROFILE
    names = (P.RESULT_NAME, P.EVIDENCE_NAME)
    peer.artifact_ids = {name: str(UUID(int=960 + index)) for index, name in enumerate(names)}
    contents = {P.RESULT_NAME: result, P.EVIDENCE_NAME: evidence}
    peer.contents = {peer.artifact_ids[name]: canonical_bytes(value) for name, value in contents.items()}
    peer.manifest = frozen_manifest(JOB, evidence['attempt_id'], [
        dict(artifact_id=peer.artifact_ids[name], name=name,
             size=len(peer.contents[peer.artifact_ids[name]]),
             sha256=hashlib.sha256(peer.contents[peer.artifact_ids[name]]).hexdigest())
        for name in names])
    stopped = dict(job_id=JOB, state='completed', business_ref='video-film-module:module-operation',
        publication_authorized=False, artifact_manifest_sha256=peer.manifest['sha256'],
        pins=dict(profile=P.PROFILE, profile_revision='1'),
        attempt=dict(attempt_id=evidence['attempt_id'], fence=1), stop_proven=True,
        stop_proof=dict(stop_proven=True, attempt_id=evidence['attempt_id'], fence=1,
            request_sha256='a' * 64, result_sha256='b' * 64,
            resource_ids=['fixture-stopped-module-writer'], observed_at=1000.0))
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        yield FilmModuleAgentGateway(client, FrozenAdapter(delivery)), peer, document, stopped, package, result


def test_new_profile_upload_and_stopped_handoff_produce_composable_module(wire):
    gateway, peer, document, stopped, expected, _ = wire
    uploaded = gateway.upload(document)
    assert P.INPUT_NAME in peer.uploads
    assert 'animation-production-input.json' not in peer.uploads
    assert all(package['files']['SKILL.md'].encode() not in peer.uploads[P.INPUT_NAME]
               for package in expected['skill_delivery']['packages'].values())
    gateway.submit('module-operation', document, 1250, uploaded)
    assert peer.request['profile'] == P.PROFILE
    assert peer.request['business_ref'] == 'video-film-module:module-operation'
    assert all(ref['role'] == P.INPUT_ROLE for ref in peer.request['inputs'] if ref['path'] == P.INPUT_NAME)
    request = next(row for row in peer.calls if row.method == 'POST' and row.url.path == '/v1/jobs')
    assert request.headers['idempotency-key'] == 'film-module-module-operation'
    peer.job = stopped
    delivered = gateway.collect(JOB, digest(document), document)
    assert collect_module_result(document, **delivered) == expected
    assert delivered['result']['module']['lifecycle']['exports']['render_at'] == 'renderAt'
    assert 'source' not in delivered['result']


@pytest.mark.parametrize('change', ['legacy-profile', 'legacy-artifact', 'legacy-result', 'wrong-module'])
def test_scene_handoffs_cannot_be_relabelled_as_composable_modules(wire, change):
    gateway, peer, document, stopped, _, result = wire
    peer.job = stopped
    if change == 'legacy-profile':
        peer.job['pins']['profile'] = 'video.animation-production.v3'
    elif change == 'legacy-artifact':
        peer.manifest['files'][0]['name'] = 'animation-production-result.json'
        peer.rehash_manifest()
    elif change == 'legacy-result':
        result = dict(schema='video.animation-production-result.v3',
                      source={'files': {'scene.html': 'legacy seek callback'}})
        peer.rewrite(P.RESULT_NAME, result)
    else:
        result['module']['film_sha256'] = '0' * 64
        peer.rewrite(P.RESULT_NAME, result)
    with pytest.raises(ServiceError):
        gateway.collect(JOB, digest(document), document)


def test_legacy_ready_route_cannot_admit_a_new_module_job(wire):
    gateway, peer, document, *_ = wire
    uploaded = gateway.upload(document)
    peer.profile['profile'] = 'video.animation-production.v3'
    with pytest.raises(ServiceError, match='profile_unavailable'):
        gateway.submit('module-operation', document, 1250, uploaded)
    assert peer.count('POST', '/v1/jobs') == 0
