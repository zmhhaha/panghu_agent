"""New source-only recipe handoff, using synthetic stopped Agent artifacts."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import bundle_files
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_manim_recipe as P
from agent_service.profiles import video_animation_manim_contract as C
from video_workbench.animation_skill_production import SkillProductionAdapter
from test_manim_resource import request


def stage(root):
    bounds = {key: item for key, item in request().items() if key not in {'schema', 'source', 'scene_class'}}
    core = dict(effect_id='effect', goal='Explain a geometric relation.', guidance='', resource_constraints=bounds)
    delivery = SkillProductionAdapter(lambda _: [], None, root / 'skills.sqlite3').freeze(
        'fixture-owner', 'recipe-op', core, ['html-svg'])
    document = C.validate_input(dict(schema=C.INPUT_SCHEMA, **core, skill_catalog=delivery['catalog']))
    files = [InlineFile.from_bytes(P.INPUT_NAME, canonical_bytes(document)),
        *bundle_files(delivery['catalog'], delivery['packages'])]
    for item in files:
        path = root / 'inputs' / item.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.content())
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=P.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(document)}, inputs=files, checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('fixture-author',))
    result = dict(schema=C.RESULT_SCHEMA, input_sha256=digest(document), request=request(),
        strategy=dict(description='Original Python expression.', tradeoffs='No TeX runtime.'), skill_decisions=[])
    (root / 'output.json').write_bytes(canonical_bytes(result))
    return document, envelope, proof, result, delivery


def test_separate_recipe_role_has_no_execution_tool_or_legacy_profile_mutation(tmp_path):
    document, envelope, proof, raw, _ = stage(tmp_path)
    handler = implementation(P.PROFILE)
    assert handler.required_tools == ('read_inputs', 'skill_search', 'skill_read', 'write')
    assert implementation('video.animation-film-module.v1').name == 'video.animation-film-module.v1'
    assert set(P.verified_bundle()) == P.BUNDLE_FILES
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    files = handler.collect_output_with_host_evidence(tmp_path, envelope, proof, observer.finish(envelope.attempt_id))
    result = json.loads(files[0].content())
    assert result['request']['source'] == raw['request']['source']
    assert result['request'] == raw['request']
    assert files[0].name == P.RESULT_NAME
    assert files[1].name == P.EVIDENCE_NAME


@pytest.mark.parametrize('mutation', ['running', 'old-role', 'changed-clock', 'new-tool', 'forged-evidence'])
def test_recipe_collection_rejects_unstopped_or_relabelled_or_changed_contract(tmp_path, mutation):
    document, envelope, proof, raw, _ = stage(tmp_path)
    handler = implementation(P.PROFILE)
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    evidence = observer.finish(envelope.attempt_id)
    if mutation == 'running':
        proof = replace(proof, all_writers_stopped=False)
    elif mutation == 'old-role':
        envelope.profile = 'video.animation-film-module.v1'
    elif mutation == 'changed-clock':
        raw['request']['global_start_frame'] += 1
    elif mutation == 'new-tool':
        raw['bash'] = 'python scene.py'
    else:
        evidence['input_sha256'] = '0' * 64
    (tmp_path / 'output.json').write_bytes(canonical_bytes(raw))
    with pytest.raises(ServiceError):
        handler.collect_output_with_host_evidence(tmp_path, envelope, proof, evidence)


def test_initial_consumer_budget_does_not_silently_expand_module_asset_slots(tmp_path):
    document, *_ = stage(tmp_path)
    core = {key: document[key] for key in C.CORE_FIELDS}
    core['resource_constraints']['frame_count'] = 61
    with pytest.raises(ServiceError, match='asset_slots_exceeded'):
        C.validate_core_task(core)


def test_existing_agent_http_transport_uploads_and_collects_the_new_exact_recipe_role(tmp_path, monkeypatch):
    import hashlib
    from uuid import UUID
    import httpx
    from agent_service.artifacts import frozen_manifest
    from agent_service.client import AgentClient
    from video_workbench.manim_recipe_gateway import ManimRecipeAgentGateway
    from test_skill_production_gateway import Peer, JOB, TOKEN
    from test_film_module_gateway import FrozenAdapter
    monkeypatch.setattr('video_workbench.skill_production_gateway.time.time', lambda: 1000.)
    document, envelope, proof, raw, delivery = stage(tmp_path)
    handler = implementation(P.PROFILE)
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    contents = {file.name: file.content() for file in handler.collect_output_with_host_evidence(
        tmp_path, envelope, proof, observer.finish(envelope.attempt_id))}
    peer = Peer()
    peer.profile['profile'] = P.PROFILE
    peer.artifact_ids = {name: str(UUID(int=970 + i)) for i, name in enumerate(contents)}
    peer.contents = {peer.artifact_ids[name]: raw for name, raw in contents.items()}
    peer.manifest = frozen_manifest(JOB, envelope.attempt_id, [
        dict(artifact_id=peer.artifact_ids[name], name=name, size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest()) for name, raw in contents.items()])
    stopped = dict(job_id=JOB, state='completed', business_ref='video-manim-recipe:recipe-op',
        publication_authorized=False, artifact_manifest_sha256=peer.manifest['sha256'],
        pins=dict(profile=P.PROFILE, profile_revision='1'),
        attempt=dict(attempt_id=envelope.attempt_id, fence=1), stop_proven=True,
        stop_proof=dict(stop_proven=True, attempt_id=envelope.attempt_id, fence=1,
            request_sha256='a' * 64, result_sha256='b' * 64,
            resource_ids=['fixture-stopped-recipe-author'], observed_at=1000.))
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        gateway = ManimRecipeAgentGateway(client, FrozenAdapter(delivery))
        uploaded = gateway.upload(document)
        gateway.submit('recipe-op', document, 1250, uploaded)
        assert peer.request['profile'] == P.PROFILE
        peer.job = stopped
        delivered = gateway.collect(JOB, digest(document), document)
        assert delivered['result']['request'] == raw['request']
