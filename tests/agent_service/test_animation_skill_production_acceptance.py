"""Synthetic records exercise v3 custody, never live Skill or visual approval."""
import hashlib

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.deploy import animation_skill_production_acceptance as skill
from agent_service.deploy import animation_production_acceptance as old
from test_animation_production_acceptance import (make_capture, read, edit, save, SOURCE,
    RENDER_IMAGE, synthetic_source_root_owner)


@pytest.fixture
def capture(tmp_path, monkeypatch):
    return make_capture(tmp_path, monkeypatch, skills=True)


def assess(capture):
    return skill.assess_candidate(capture['directory'], source_root=SOURCE,
        renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)


def test_v3_admission_requires_exact_skill_bytes_and_stopped_host_evidence(capture):
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (capture['directory'], capture['renderer']) for path in folder.rglob('*') if path.is_file()}
    host, report = assess(capture)
    assert report['suite'] == skill.SUITE == 'video.animation-production-contract.v3'
    assert report['profile'] == host['profile']['name'] == 'video.animation-production.v3'
    assert report['skill_catalog_sha256'] == capture['package']['skill_catalog_sha256']
    assert report['skill_tool_evidence_sha256'] == capture['package']['skill_tool_evidence_sha256']
    assert report['progressive_skill_tools_exercised'] is True
    assert report['skill_decisions'] == capture['package']['skill_decisions']
    assert report['renderer_proof']['schema'] == 'video.animation-skill-production-captured-render-proof.v1'
    assert 'original_v5_skill_film_input' in report['renderer_proof']['scope']
    assert report['model_requests'] == report['uid_allocations'] == 0
    assert report['acceptance_created'] is host['profile']['enabled'] is False
    assert 'business_publication' in report['skill_catalog_evidence_scope']
    assert before == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (capture['directory'], capture['renderer']) for path in folder.rglob('*') if path.is_file()}
    with pytest.raises(ServiceError, match='profile_not_allowed'):
        old.assess_candidate(capture['directory'], source_root=SOURCE,
            renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE, film=True)


def test_old_film_proof_cannot_admit_progressive_skill_role(tmp_path, monkeypatch):
    capture = make_capture(tmp_path, monkeypatch, film=True)
    with pytest.raises(ServiceError, match='profile_not_allowed'):
        assess(capture)


@pytest.mark.parametrize('name,change,code', [
    ('animation-skill-tool-evidence.json', lambda doc: doc.update(attempt_id='00000000-0000-0000-0000-000000000000'),
     'artifact_mismatch'),
    ('creative-scene-v5.json', lambda doc: doc['result']['handoff'].update(exit_state='replaced'), 'package_mismatch'),
    ('scenario.json', lambda doc: doc['inputs'].pop(), 'scenario_mismatch'),
    ('operator-summary.json', lambda doc: doc.update(lease_expiry_rejected=False), 'operator_mismatch'),
    ('candidate-evidence.json', lambda doc: doc['model_calls'][0].update(status=500), 'model_calls_missing'),
])
def test_v3_cannot_borrow_changed_evidence_or_old_single_result_scope(capture, name, change, code):
    edit(capture['root'], name, change)
    with pytest.raises(ServiceError, match=code):
        assess(capture)


@pytest.mark.parametrize('keep_search', [False, True])
def test_valid_business_creation_without_root_read_cannot_admit_skill_tools(capture, keep_search):
    from agent_service.deploy.candidate_scenario import PARENT_FILES, verify_parent
    from agent_service.profiles.video_animation_skill_production import RESULT_NAME, EVIDENCE_NAME
    root = capture['root']
    documents = {name:read(root/name) for name in PARENT_FILES.values()}
    envelope, result = verify_parent(documents)
    evidence = read(root/EVIDENCE_NAME)
    evidence['events'] = [row for row in evidence['events']
        if keep_search and row['tool_name'] == 'skill_search']
    evidence['sha256'] = digest({key:value for key,value in evidence.items() if key != 'sha256'})
    output = read(root/RESULT_NAME)
    output.update(skill_decisions=[], skill_tool_evidence_sha256=evidence['sha256'])
    save(root/EVIDENCE_NAME,evidence)
    save(root/RESULT_NAME,output)
    # Test this narrow gate with coherent artifacts; the outer shared assessor
    # independently authenticates the stopped receipt before invoking it.
    result = result.model_copy(update=dict(artifacts=[
        InlineFile.from_bytes(RESULT_NAME,canonical_bytes(output)),
        InlineFile.from_bytes(EVIDENCE_NAME,canonical_bytes(evidence))]))
    with pytest.raises(ServiceError, match='progressive_read_required'):
        old._skill_capture(root,envelope,result,{})


def test_v3_optional_admission_only_creates_its_own_disabled_binding(capture, tmp_path, monkeypatch):
    from agent_service import worker_entry
    # Match the existing synthetic operator reader; this does not test a real mount.
    monkeypatch.setattr(worker_entry, '_read_operator_json', lambda path, **kwargs: read(path))
    output = tmp_path / 'admitted'
    summary = skill.review_candidate(capture['directory'], output, source_root=SOURCE,
        renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)
    assert summary['accepted_host'] is True and summary['enabled'] is False
    assert summary['suite'] == skill.SUITE
    assert summary['model_requests'] == summary['uid_allocations'] == 0
    host = read(output / 'worker-configs' / summary['host_config_map'] / 'config.json')
    assert host['profile']['name'] == 'video.animation-production.v3'
    assert host['profile']['enabled'] is False
    assert all(route['enabled'] is False for route in host['execution_catalog']['routes'])
    selected = next(row for row in host['execution_catalog']['execution_bindings']
        if row['id'] == host['binding_id'] and row['revision'] == host['binding_revision'])
    assert selected['acceptance']['suite'] == skill.SUITE
