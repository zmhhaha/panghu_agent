"""Offline machine-handoff examples, never live planning or business acceptance."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.deploy.acceptance_probe import fixture
from agent_service.engines import StopEvidence
from agent_service.profiles.ppt import PPTProfile, _manifest_example
from ppt_service.control.planning import project_planning
from ppt_service.worker.candidate_validation import validate_manifest


@pytest.fixture
def initial(tmp_path):
    task, _ = fixture('ppt.plan_initial.v1')
    manifest = _manifest_example(task)
    body = b'# Explicit synthetic test notes, not Agent output or acceptance.\n'
    artifact = manifest['artifacts'][0]
    artifact.update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
    root = tmp_path / 'project' / manifest['project_root']
    root.mkdir(parents=True)
    (root / artifact['path']).write_bytes(body)
    (tmp_path / 'result').mkdir()
    envelope = SimpleNamespace(task=task, profile='ppt.plan_initial.v1', job_id=str(uuid4()), attempt_id=str(uuid4()))
    return tmp_path, manifest, envelope


def collect(case):
    workspace, manifest, envelope = case
    (workspace / 'result/manifest.json').write_bytes(canonical_bytes(manifest))
    return PPTProfile('plan_initial').collect_output(workspace, envelope,
        StopEvidence(envelope.attempt_id, True, ('offline-synthetic-stop',)))


@pytest.mark.parametrize('quality', [{}, {'reports': [{'path': 'planning-notes.md'}]}])
def test_initial_example_is_accepted_by_stopped_collector_and_business_parsers(initial, quality):
    workspace, manifest, envelope = initial
    manifest['quality'] = quality
    files = collect(initial)
    assert {file.name for file in files} == {
        'project/deck/planning-notes.md', 'result/manifest.json', 'result/checkpoint.json'}
    report = validate_manifest(workspace / 'project', manifest)
    assert report['published'] is False and report['quality_reports_verified'] is False
    projection = project_planning(manifest, source_manifest_sha256=hashlib.sha256(canonical_bytes(manifest)).hexdigest(),
        expected_page_count=3)
    assert projection['design']['source_plan']['stage'] == 'stage1'


@pytest.mark.parametrize('value', ['awaiting_confirmation', 'No final checks yet', True, 1])
def test_quality_descriptions_are_rejected_without_weakening_the_collector(initial, value):
    initial[1]['quality'] = {'reports': [{'path': 'planning-notes.md'}], 'status': value}
    with pytest.raises(ServiceError) as caught:
        collect(initial)
    assert caught.value.code == 'invalid_ppt_quality_refs'


def test_direction_example_passes_existing_business_projection_with_full_page_plans():
    task, _ = fixture('ppt.plan_initial.v1')
    task['phase'] = 'plan_options'
    manifest = _manifest_example(task)
    projection = project_planning(manifest, source_manifest_sha256='a' * 64, expected_page_count=3)
    directions = projection['design']['directions']
    assert len(directions) == 3 and len({row['id'] for row in directions}) == 3
    assert all(len(row['page_plan']['pages']) == 3 and row['production_parameters'] for row in directions)


def test_prompt_places_explicit_machine_example_before_untrusted_task_data():
    task, _ = fixture('ppt.plan_initial.v1')
    prompt = PPTProfile('plan_initial').build_prompt(task)
    template_text = prompt.split('Manifest JSON shape for this exact phase and page count:\n', 1)[1].split('\nTask JSON:\n', 1)[0]
    assert json.loads(template_text) == _manifest_example(task)
    assert 'quality is ONLY a tree of report references' in prompt
    assert 'without repeating project_root' in prompt
    assert 'Do not write result/checkpoint.json' in prompt
