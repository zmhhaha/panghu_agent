"""Freeform creation/revision identity boundaries without a renderer or model call."""
from copy import deepcopy
import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual


ATTEMPT = 'creation-attempt'


def source_output():
    return dict(engine='canvas', strategy=dict(description='Continuous object transformation.', tradeoffs=''),
        files={'scene.html': '<!doctype html><canvas></canvas><script src="./scene.js"></script>',
               'scene.js': 'window.__animationExperiment = {seek(t) { document.body.dataset.t = t; }};'})


def review_result(source):
    return dict(schema=visual.REVIEW_RESULT_SCHEMA, input_sha256='a' * 64,
        prompt_revision='review-r1', media_sha256='b' * 64, coverage='sampled_frames',
        evidence_scope='visual_only', observed_frame_ids=['frame-1', 'frame-2'],
        frame_evidence_status='supplied_only',
        source_sha256=creation.source_sha256(source), summary='Crossing is hard to follow.',
        issues=[dict(issue_id='overlap', severity='major', kind='motion',
            start_seconds=1, end_seconds=3, frame_ids=['frame-1', 'frame-2'],
            problem='Two objects overlap.', change_target='Separate the paths while retaining timing.')],
        limitations=['Sampled visual frames only; audio not supplied.'])


def input_document(revise=False):
    source = source_output()
    return dict(schema=creation.INPUT_SCHEMA, creation_id='freely-authored-fragment',
        goal='Explain an unfamiliar process with a visual metaphor chosen by the Agent.',
        constraints=dict(width=1280, height=720, fps=24, duration_seconds=83,
                         allowed_engines=['html-svg', 'canvas', 'three']),
        assets={'three.module.min.js': 'c' * 64}, prompt_revision='create-r1', guidance='',
        base_source=(dict(source_revision='source-r1', source_sha256=creation.source_sha256(source),
                          **{key: source[key] for key in ('engine', 'files')}) if revise else None),
        review=review_result(source) if revise else None)


def prepare(tmp_path, revise=False):
    profile = implementation(creation.PROFILE)
    (tmp_path / 'inputs').mkdir()
    raw = canonical_bytes(input_document(revise))
    (tmp_path / 'inputs' / creation.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(source_output()))
    envelope = SimpleNamespace(profile=creation.PROFILE, profile_revision='1', attempt_id=ATTEMPT,
        task=dict(input_sha256=hashlib.sha256(raw).hexdigest()),
        inputs=[InlineFile.from_bytes(creation.INPUT_NAME, raw)], checkpoint_inputs=[], checkpoint_ref=None)
    return profile, envelope


def collect(profile, tmp_path, envelope, proof=None):
    return profile.collect_output(tmp_path, envelope,
        proof or StopEvidence(ATTEMPT, True, ('stopped-creative-agent',)))


@pytest.mark.parametrize('revise', [False, True])
def test_freeform_complete_fragment_and_revision_collect_service_owned_identities(tmp_path, revise):
    profile, envelope = prepare(tmp_path, revise)
    document = input_document(revise)
    profile.validate_inputs(tmp_path, envelope.task)
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    assert creation.validate_creation_result(result, envelope.task['input_sha256'], document) == result
    assert creation.scene_files(result) == {name: value.encode() for name, value in source_output()['files'].items()}
    assert result['source_sha256'] == creation.source_sha256(source_output())
    assert result['base_source_sha256'] == (document['base_source']['source_sha256'] if revise else None)
    assert result['review_sha256'] == (hashlib.sha256(canonical_bytes(document['review'])).hexdigest()
                                      if revise else None)
    assert 'approved' not in result and 'passed' not in result
    assert profile.required_tools == ('read', 'write')
    prompt = profile.build_prompt(envelope.task)
    assert 'no fixed question, eight-second template' in prompt
    assert 'single attempt' in prompt and 'window.__animationExperiment' in prompt


@pytest.mark.parametrize('change', ['changed_source', 'wrong_source_digest', 'wrong_review_source',
    'no_review', 'no_base', 'empty_review', 'unknown_field', 'too_long', 'nan', 'untrusted_asset'])
def test_creation_revision_cannot_drift_from_exact_source_and_review(change):
    document = input_document(True)
    if change == 'changed_source':
        document['base_source']['files']['scene.js'] += '\n// changed'
    elif change == 'wrong_source_digest':
        document['base_source']['source_sha256'] = 'd' * 64
    elif change == 'wrong_review_source':
        document['review']['source_sha256'] = 'd' * 64
    elif change == 'no_review':
        document['review'] = None
    elif change == 'no_base':
        document['base_source'] = None
    elif change == 'empty_review':
        document['review']['issues'] = []
    elif change == 'unknown_field':
        document['review']['accepted'] = True
    elif change == 'too_long':
        document['constraints']['duration_seconds'] = 601
    elif change == 'nan':
        document['constraints']['duration_seconds'] = float('nan')
    else:
        document['assets']['remote.js'] = 'd' * 64
    with pytest.raises(ServiceError):
        creation.validate_creation_input(document)


@pytest.mark.parametrize('change', ['source_content', 'source_digest', 'file_digest', 'review_digest',
    'base_digest', 'input_digest', 'prompt_revision', 'self_approval'])
def test_artifact_validation_rejects_rebound_source_or_review_receipts(tmp_path, change):
    profile, envelope = prepare(tmp_path, True)
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    if change == 'source_content':
        result['files']['scene.js'] += '\n// changed'
    elif change == 'file_digest':
        result['files_sha256']['scene.js'] = 'd' * 64
    elif change == 'source_digest':
        result['source_sha256'] = 'd' * 64
    elif change == 'review_digest':
        result['review_sha256'] = 'd' * 64
    elif change == 'base_digest':
        result['base_source_sha256'] = 'd' * 64
    elif change == 'input_digest':
        result['input_sha256'] = 'd' * 64
    elif change == 'prompt_revision':
        result['prompt_revision'] = 'changed-r2'
    else:
        result['accepted'] = True
    with pytest.raises(ServiceError):
        creation.validate_creation_result(result, envelope.task['input_sha256'], input_document(True))


@pytest.mark.parametrize('change', ['writer_active', 'wrong_attempt', 'no_resources', 'profile',
    'revision', 'checkpoint', 'input_hash', 'input_extra', 'output_extra', 'path_traversal'])
def test_collector_enforces_stop_and_bounded_source_artifact_boundaries(tmp_path, change):
    profile, envelope = prepare(tmp_path)
    proof = None
    if change == 'writer_active':
        proof = StopEvidence(ATTEMPT, False, ('writer',))
    elif change == 'wrong_attempt':
        proof = StopEvidence('another-attempt', True, ('writer',))
    elif change == 'no_resources':
        proof = StopEvidence(ATTEMPT, True, ())
    elif change == 'profile':
        envelope.profile = 'video.animation-experiment.v1'
    elif change == 'revision':
        envelope.profile_revision = '2'
    elif change == 'checkpoint':
        envelope.checkpoint_inputs = [object()]
    elif change == 'input_hash':
        envelope.task['input_sha256'] = 'e' * 64
    elif change == 'input_extra':
        (tmp_path / 'inputs/extra.json').write_text('{}')
    else:
        output = source_output()
        if change == 'output_extra':
            output['quality_score'] = 100
        else:
            output['files']['../scene.js'] = 'untrusted'
        (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError):
        collect(profile, tmp_path, envelope, proof)


def test_collecting_source_never_executes_it(tmp_path):
    profile, envelope = prepare(tmp_path)
    output = source_output()
    output['files']['scene.js'] = 'throw new Error("must remain inert");'
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    assert result['files']['scene.js'] == output['files']['scene.js']


def test_new_creation_bundle_does_not_mutate_frozen_v1(tmp_path, monkeypatch):
    from agent_service.profiles import video_animation_experiment as frozen
    assert set(creation.verified_bundle()) == creation.BUNDLE_FILES
    assert frozen.verified_bundle()
    copied = tmp_path / creation.BUNDLE_REVISION
    shutil.copytree(creation.BUNDLE_ROOT, copied)
    (copied / 'creation-procedure/SKILL.md').write_text('mutated')
    monkeypatch.setattr(creation, 'BUNDLE_ROOT', copied)
    with pytest.raises(ServiceError, match='animation_creation_bundle_integrity'):
        implementation(creation.PROFILE).build_prompt({'input_sha256': 'a' * 64})


def test_creation_probe_does_not_manufacture_a_native_acceptance_scenario():
    from agent_service.deploy import acceptance_probe
    assert creation.PROFILE in acceptance_probe.PROFILES
    with pytest.raises(ServiceError, match='candidate_scenario_required'):
        acceptance_probe.fixture(creation.PROFILE)
