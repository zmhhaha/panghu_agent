"""Offline machine-handoff examples, never live planning or business acceptance."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy.acceptance_probe import fixture
from agent_service.engines import StopEvidence
from agent_service.profiles.ppt import (PPTProfile, _manifest_example,
    _needs_input_manifest_example, artifact_path, validate_checkpoint)
from ppt_service.control.planning import project_planning
from ppt_service.worker.candidate_validation import validate_manifest


def test_ppt_sources_allow_25_mib_without_increasing_aggregate_limit():
    task, _ = fixture('ppt.plan_initial.v1')
    profile = PPTProfile('plan_initial')
    task['input_snapshot']['assets'][0]['bytes'] = 25 * 1024**2
    task['input_snapshot_sha256'] = digest(task['input_snapshot'])
    profile.validate_task(task)
    assert profile.limits.max_input_total_bytes == 256 * 1024**2
    task['input_snapshot']['assets'][0]['bytes'] += 1
    task['input_snapshot_sha256'] = digest(task['input_snapshot'])
    with pytest.raises(ServiceError):
        profile.validate_task(task)


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


@pytest.fixture
def blocked_initial(tmp_path):
    task, _ = fixture('ppt.plan_initial.v1')
    manifest = _needs_input_manifest_example(task)
    manifest['plan']['blocker'] = 'Synthetic fixture is missing a caller-provided source fact.'
    manifest['questions'] = ['Provide the missing source fact before continuing this synthetic fixture.']
    (tmp_path / 'project' / manifest['project_root']).mkdir(parents=True)
    (tmp_path / 'result').mkdir()
    envelope = SimpleNamespace(task=task, profile='ppt.plan_initial.v1', job_id=str(uuid4()), attempt_id=str(uuid4()))
    return tmp_path, manifest, envelope


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


@pytest.mark.parametrize('missing', ['artifacts', 'provenance'])
def test_needs_input_still_requires_complete_file_handoff_fields(initial, missing):
    initial[1].update(status='needs_input', questions=['An actual blocker needs attention.'])
    initial[1].pop(missing)
    with pytest.raises(ServiceError) as caught:
        collect(initial)
    assert caught.value.code in {'invalid_ppt_output', 'ppt_artifact_roster_mismatch'}


def test_direction_example_passes_existing_business_projection_with_full_page_plans():
    task, _ = fixture('ppt.plan_initial.v1')
    task['phase'] = 'plan_options'
    manifest = _manifest_example(task)
    projection = project_planning(manifest, source_manifest_sha256='a' * 64, expected_page_count=3)
    directions = projection['design']['directions']
    assert len(directions) == 3 and len({row['id'] for row in directions}) == 3
    assert all(len(row['page_plan']['pages']) == 3 and row['production_parameters'] for row in directions)


def test_official_workflow_log_survives_collection_and_checkpoint_validation(initial):
    workspace, manifest, envelope = initial
    path = workspace / 'project' / manifest['project_root'] / 'validation/workflow.log'
    path.parent.mkdir()
    raw = b'2026-10-29 project init; source import\n'
    path.write_bytes(raw)
    manifest['artifacts'].append(dict(role='workflow_audit', path='validation/workflow.log',
        sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), media_type='text/plain'))
    files = {file.name: file for file in collect(initial)}
    assert files['project/deck/validation/workflow.log'].content() == raw
    checkpoint = json.loads(files['result/checkpoint.json'].content())
    validate_checkpoint(checkpoint, project_id=envelope.task['project_id'],
                        snapshot_hash=envelope.task['input_snapshot_sha256'])
    assert any(row['path'] == 'deck/validation/workflow.log' for row in checkpoint['files'])


@pytest.mark.parametrize('path', ['worker.log', 'validation/runtime.log',
    '.pi/validation/workflow.log', 'sessions/validation/workflow.log'])
def test_workflow_log_exception_does_not_admit_other_logs_or_native_state(path):
    with pytest.raises(ServiceError) as caught:
        artifact_path(path)
    assert caught.value.code in {'ppt_artifact_type_forbidden', 'ppt_native_state_forbidden'}


def test_prompt_places_explicit_machine_example_before_untrusted_task_data():
    task, _ = fixture('ppt.plan_initial.v1')
    prompt = PPTProfile('plan_initial').build_prompt(task)
    template_text = prompt.split('Manifest JSON shape for this exact phase and page count:\n', 1)[1].split('\nTask JSON:\n', 1)[0]
    assert json.loads(template_text) == _manifest_example(task)
    assert 'quality is ONLY a tree of report references' in prompt
    assert 'without repeating project_root' in prompt
    assert 'Do not write result/checkpoint.json' in prompt


@pytest.mark.parametrize('phase', ['plan_initial', 'plan_options', 'prepare', 'generate', 'revise'])
def test_blocked_prompt_example_keeps_complete_schema_without_claiming_success(phase):
    task, _ = fixture('ppt.plan_initial.v1')
    task['phase'] = phase
    example = _needs_input_manifest_example(task)
    assert set(example) == {'schema_version', 'phase', 'status', 'input_snapshot_sha256',
        'project_root', 'plan', 'questions', 'artifacts', 'slides', 'quality', 'provenance'}
    assert example['schema_version'] == 1 and example['phase'] == phase
    assert example['status'] == 'needs_input' and example['questions']
    assert example['input_snapshot_sha256'] == task['input_snapshot_sha256']
    assert example['artifacts'] == [dict(path='blocker-note.md', role='blocker_note',
        media_type='text/markdown',
        sha256='<actual 64 lowercase hexadecimal SHA256 of the written blocker note>', bytes=0)]
    assert example['slides'] == []
    assert example['quality'] == example['provenance'] == {}


def test_blocked_example_with_measured_existing_files_crosses_strict_handoff(initial):
    workspace, existing, envelope = initial
    manifest = _needs_input_manifest_example(envelope.task)
    manifest['plan']['blocker'] = 'Synthetic fixture needs a source fact the caller has not provided.'
    manifest['questions'] = ['Provide the missing source fact before continuing this synthetic fixture.']
    # A blocker note is unnecessary when an ordinary measured project file
    # already exists. Declare that real file, not the unwritten note template.
    manifest['artifacts'] = existing['artifacts']
    files = {row.name: row for row in collect((workspace, manifest, envelope))}
    assert json.loads(files['result/manifest.json'].content()) == manifest
    checkpoint = json.loads(files['result/checkpoint.json'].content())
    validate_checkpoint(checkpoint, project_id=envelope.task['project_id'],
        snapshot_hash=envelope.task['input_snapshot_sha256'])
    assert checkpoint['publication_authorized'] is False
    report = validate_manifest(workspace / 'project', manifest)
    assert report['published'] is False and report['quality_reports_verified'] is False


def test_empty_blocked_roster_cannot_form_valid_checkpoint_after_collection(blocked_initial):
    workspace, manifest, envelope = blocked_initial
    manifest['artifacts'] = []
    files = {row.name: row for row in collect(blocked_initial)}
    checkpoint = json.loads(files['result/checkpoint.json'].content())
    assert checkpoint['files'] == [] and checkpoint['total_bytes'] == 0
    with pytest.raises(ServiceError) as caught:
        validate_checkpoint(checkpoint, project_id=envelope.task['project_id'],
            snapshot_hash=envelope.task['input_snapshot_sha256'])
    assert caught.value.code == 'invalid_ppt_checkpoint'


def test_real_blocker_note_without_prior_files_crosses_strict_handoff(blocked_initial):
    workspace, manifest, envelope = blocked_initial
    artifact = manifest['artifacts'][0]
    note = (b'# Synthetic blocker note\n\nThis fixture cannot proceed because a caller-provided '
            b'source fact is absent. No source facts or successful gates are asserted.\n')
    path = workspace / 'project' / manifest['project_root'] / artifact['path']
    path.write_bytes(note)
    artifact.update(sha256=hashlib.sha256(note).hexdigest(), bytes=len(note))
    files = {row.name: row for row in collect(blocked_initial)}
    assert set(files) == {'project/deck/blocker-note.md', 'result/manifest.json', 'result/checkpoint.json'}
    assert files['project/deck/blocker-note.md'].content() == note
    assert json.loads(files['result/manifest.json'].content()) == manifest
    checkpoint = json.loads(files['result/checkpoint.json'].content())
    validate_checkpoint(checkpoint, project_id=envelope.task['project_id'],
        snapshot_hash=envelope.task['input_snapshot_sha256'])
    assert checkpoint['files'] == [dict(path='deck/blocker-note.md', sha256=artifact['sha256'], bytes=len(note))]
    assert checkpoint['publication_authorized'] is False
    report = validate_manifest(workspace / 'project', manifest)
    assert report['published'] is False and report['quality_reports_verified'] is False


def test_blocked_example_preserves_bound_checkpoint_root():
    task, _ = fixture('ppt.plan_initial.v1')
    task['checkpoint'] = {'project_root': 'existing/restored-deck'}
    assert _needs_input_manifest_example(task)['project_root'] == 'existing/restored-deck'


def test_prompt_explains_blocked_roster_before_success_example_and_task_data():
    task, _ = fixture('ppt.plan_initial.v1')
    prompt = PPTProfile('plan_initial').build_prompt(task)
    blocked_marker = 'Blocked handoff JSON shape (needs_input also requires EVERY typed field):\n'
    text = prompt.split(blocked_marker, 1)[1].split('\nThe blocker-note.md artifact above', 1)[0]
    assert json.loads(text) == _needs_input_manifest_example(task)
    assert prompt.index(blocked_marker) < prompt.index('Manifest JSON shape for this exact phase and page count:')
    assert 'including restored files' in prompt
    assert 'never omit artifacts or provenance' in prompt
    assert 'replace artifacts with files' in prompt
    assert 'Preserve the checkpoint project_root when supplied' in prompt
    assert 'Every roster entry still needs role,path,sha256,bytes,media_type' in prompt
    assert 'Every needs_input handoff requires at least one real artifact' in prompt
    assert 'an empty artifacts roster cannot form a valid checkpoint' in prompt
    assert 'write a truthful blocker-note.md' in prompt
    assert 'writing is available and permitted' in prompt
    assert 'not evidence that the note exists' in prompt
    assert 'replace its SHA256 and bytes=0 with the actual digest and byte count' in prompt
    assert 'tasks must retain and declare every existing file' in prompt
    assert 'Do not invent source facts or successful gates' in prompt


def test_execution_guidance_closes_only_redundant_confirmation_and_retains_real_gates():
    task, _ = fixture('ppt.plan_initial.v1')
    prompt = PPTProfile('plan_initial').build_prompt(task)
    assert 'both database confirmations and the prepared checkpoint already authorize' in prompt
    assert 'Do not request another confirmation merely to continue, finalize or export' in prompt
    assert 'pending or denied tool approval' in prompt
    assert 'unresolved needs_human decision still blocks' in prompt
    assert 'Do not change an approved specification or fake any approval' in prompt
    assert 'an optional user-facing preview URL or a separate human browser session is not itself a blocker' in prompt
    assert 'to produce and visually inspect the actual required PNGs, is a blocker' in prompt
    assert 'never invent a successful render or inspection' in prompt
    assert 'If a required tool/approval/preview is unavailable' not in prompt
    assert 'reading the actual rendered PNGs' in prompt
    assert 'main agent to process the same batches sequentially' in prompt
    assert 'later website draft review and owner PowerPoint acceptance remain separate' in prompt
    assert 'never count as already passed or authorize publication' in prompt
