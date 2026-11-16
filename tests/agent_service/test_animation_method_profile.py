"""Contract boundaries for method authors/reviewers, without model execution."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest, InlineFile
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_method as contract


def document(mode='create'):
    return dict(schema=contract.INPUT_SCHEMA, mode=mode, goal='Explain a cause and its outcome',
        criteria=[dict(id='cause', description='Cause precedes the change')], methods=[],
        constraints=dict(width=1280, height=720, fps=24, duration_seconds=18, allowed_engines=['html-svg']),
        feedback='', base_source=None, reference=None,
        observation=dict(frames=[], audio_observed=False, motion_observed=False, limitations=['Only still frames']),
        comparison=None, assets={})


def result(context):
    return dict(schema=contract.RESULT_SCHEMA, input_sha256=digest(context), mode=context['mode'],
        criteria=context['criteria'], methods=[dict(title='Staged causality', problem='Causal explanation',
            guidance='Show an intervention, then its observable effect.', applicability='Causal processes', limitations=[],
            criteria=context['criteria'], runtime_requirements={'engine': 'html-svg'})],
        source=dict(engine='html-svg', strategy=dict(description='Stage the intervention first', tradeoffs=''),
                    files={'scene.html': '<svg></svg>'}) if context['mode'] == 'create' else None,
        sound_events=[], review=None)


def review_context():
    context = document('review')
    current, baseline = str(uuid4()), str(uuid4())
    context['observation']['frames'] = [dict(frame_id='frame-' + str(i), file_name='frame-' + str(i) + '.png',
        sha256=str(i + 1) * 64, timestamp_seconds=2, candidate_id=candidate, media_sha256='f' * 64)
        for i, candidate in enumerate([baseline, current])]
    context['comparison'] = dict(candidate_id=current, baseline_candidate_id=baseline)
    output = result(context)
    output['methods'] = []
    output['review'] = dict(summary='Cause and consequence are separated', outcome='improved', preferred_candidate_id=current,
        criteria_results=[dict(criterion_id='cause', status='passed', evidence='The supplied frames show the change')],
        remaining_issues=[], next_guidance='', limitations=['Only still frames'])
    return context, output


@pytest.mark.parametrize('name', ['frame-candidate-0.png', 'frame-baseline-0.jpg', 'Frame_2.jpeg',
                                 'a' * 120 + '.png'])
def test_review_frame_names_are_flat_images_accepted_by_input_reader(name):
    context, _ = review_context()
    context['observation']['frames'][0]['file_name'] = name
    assert contract.validate_input(context) == context


@pytest.mark.parametrize('name', ['frames/one.png', '../one.png', '/one.png', 'C:/one.png',
                                 r'frames\one.png', 'one..png', '.hidden.png', 'one.gif',
                                 contract.INPUT_NAME, 'one.PNG', 'a' * 121 + '.png', 'one\n.png'])
def test_review_frame_names_reject_paths_and_nonreader_names(name):
    context, _ = review_context()
    context['observation']['frames'][0]['file_name'] = name
    with pytest.raises(ServiceError):
        contract.validate_input(context)


@pytest.mark.parametrize('key', ['file_name', 'frame_id'])
def test_review_frame_names_and_ids_must_be_unique(key):
    context, _ = review_context()
    frames = context['observation']['frames']
    frames[1][key] = frames[0][key]
    with pytest.raises(ServiceError):
        contract.validate_input(context)


def test_bundle_and_roles_are_distinct_and_verified():
    assert set(contract.verified_bundle()) == contract.BUNDLE_FILES
    assert contract.VideoAnimationMethodProfile().name == contract.AUTHOR_PROFILE
    reviewer = contract.VideoAnimationMethodProfile(True)
    assert reviewer.name == contract.REVIEW_PROFILE and 'vision' in reviewer.required_model_capabilities
    assert reviewer.required_tools == ('read_inputs', 'write')


def test_new_engine_bundle_keeps_frozen_v1_resources_and_review_readonly_instructions():
    import hashlib
    import json
    old = contract.BUNDLE_ROOT.parent / contract.LEGACY_BUNDLE_REVISION
    manifest_raw = (old / 'manifest.json').read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == contract.LEGACY_BUNDLE_MANIFEST_SHA256
    for row in json.loads(manifest_raw)['local_files']:
        assert hashlib.sha256((old / row['path']).read_bytes()).hexdigest() == row['sha256']
    assert contract.BUNDLE_REVISION == 'animation-method-v2'
    prompt = contract.VideoAnimationMethodProfile(True).build_prompt({'input_sha256': 'a' * 64})
    assert 'return methods=[], source=null and sound_events=[] exactly.' in prompt
    assert 'constraints.allowed_engines' in prompt and "canvas.getContext('2d')" in prompt
    assert './assets/three.module.min.js' in prompt and 'ready' in prompt
    assert 'Strategy has exactly `description` and `tradeoffs`.' in prompt
    assert 'Both must be JSON strings,\nat most 3000 characters each.' in prompt
    assert '`description` must be non-empty; `tradeoffs` may\nbe empty ("").' in prompt
    assert 'Do not use arrays, objects, null, or additional strategy fields' in prompt
    assert 'combine multiple\nlimitations in the single tradeoffs string.' in prompt


@pytest.mark.parametrize('field', ['description', 'tradeoffs'])
@pytest.mark.parametrize('value', [['One limitation'], {'limitation': 'A tradeoff'}, None, 1, 'x' * 3001])
def test_source_strategy_rejects_nonstrings_and_oversized_strings(field, value):
    context = document()
    output = result(context)
    output['source']['strategy'][field] = value
    with pytest.raises(ServiceError):
        contract.validate_result(output, digest(context), context)


def test_source_strategy_accepts_string_limits_and_empty_tradeoffs_but_requires_description():
    context = document()
    output = result(context)
    output['source']['strategy'] = dict(description='d' * 3000, tradeoffs='t' * 3000)
    assert contract.validate_result(output, digest(context), context) == output
    output['source']['strategy']['tradeoffs'] = ''
    assert contract.validate_result(output, digest(context), context) == output
    output['source']['strategy']['description'] = ''
    with pytest.raises(ServiceError):
        contract.validate_result(output, digest(context), context)


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_author_selects_only_admitted_engine_and_all_methods_must_match_source(engine):
    context = document()
    context['constraints']['allowed_engines'] = [engine]
    if engine == 'three':
        context['assets']['three.module.min.js'] = 'a' * 64
    output = result(context)
    output['source']['engine'] = engine
    output['methods'][0]['runtime_requirements']['engine'] = engine
    assert contract.validate_result(output, digest(context), context) == output
    output['methods'][0]['runtime_requirements']['engine'] = 'html-svg'
    with pytest.raises(ServiceError, match='runtime_not_admitted'):
        contract.validate_result(output, digest(context), context)


def test_result_binds_input_criteria_and_source_engine():
    context = document()
    output = result(context)
    assert contract.validate_result(output, digest(context), context) == output
    for change in ('criteria', 'engine', 'input'):
        bad = deepcopy(output)
        if change == 'criteria':
            bad['criteria'][0]['description'] = 'A different standard'
        elif change == 'engine':
            bad['source']['engine'] = 'canvas'
        else:
            bad['input_sha256'] = 'a' * 64
        with pytest.raises(ServiceError):
            contract.validate_result(bad, digest(context), context)


def test_uuid_comparison_is_consistent_and_requires_two_observed_candidates():
    context, output = review_context()
    assert contract.validate_result(output, digest(context), context) == output
    context['observation']['frames'].pop(0)
    context['comparison']['baseline_candidate_id'] = None
    output['input_sha256'] = digest(context)
    with pytest.raises(ServiceError, match='comparison_evidence_missing'):
        contract.validate_result(output, digest(context), context)


@pytest.mark.parametrize('dimension', ['requires_motion', 'requires_audio'])
def test_still_images_cannot_pass_motion_or_audio_criteria(dimension):
    context, output = review_context()
    context['criteria'][0][dimension] = True
    output['criteria'] = context['criteria']
    output['input_sha256'] = digest(context)
    with pytest.raises(ServiceError, match='unobserved_quality_claim'):
        contract.validate_result(output, digest(context), context)
    output['review']['criteria_results'][0]['status'] = 'insufficient_evidence'
    contract.validate_result(output, digest(context), context)


def test_sound_events_fit_clip_and_only_creation_can_emit_them():
    context = document()
    output = result(context)
    event = dict(time=17, duration=.5, frequency=400, gain=.1, waveform='sine')
    output['sound_events'] = [event]
    contract.validate_result(output, digest(context), context)
    event['duration'] = 2
    with pytest.raises(ServiceError):
        contract.validate_result(output, digest(context), context)
    context, output = review_context()
    output['sound_events'] = [dict(event, time=0)]
    with pytest.raises(ServiceError):
        contract.validate_result(output, digest(context), context)


def test_collector_requires_stopped_execution_and_exact_input_manifest(tmp_path):
    context = document()
    raw = canonical_bytes(context)
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / contract.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result(context)))
    attempt_id = str(uuid4())
    envelope = SimpleNamespace(profile=contract.AUTHOR_PROFILE, profile_revision='1', attempt_id=attempt_id,
        task={'input_sha256': digest(context)}, inputs=[InlineFile.from_bytes(contract.INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt_id, all_writers_stopped=True, resource_ids=('unit-test-resource',))
    profile = contract.VideoAnimationMethodProfile()
    assert profile.collect_output(tmp_path, envelope, proof)[0].name == contract.RESULT_NAME
    envelope.inputs = [InlineFile.from_bytes(contract.INPUT_NAME, b'changed')]
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        profile.collect_output(tmp_path, envelope, proof)


def test_reference_url_is_metadata_and_never_model_observation():
    context = document('plan')
    context['criteria'] = []
    context['reference'] = {'url': 'https://example.org/video'}
    contract.validate_input(context)
    context['observation']['motion_observed'] = True
    with pytest.raises(ServiceError):
        contract.validate_input(context)


def test_unsupplied_parent_fails_before_paid_render_materialization():
    context = document()
    output = result(context)
    output['methods'][0]['parent_version_id'] = 'not-supplied'
    with pytest.raises(ServiceError, match='parent_not_supplied'):
        contract.validate_result(output, digest(context), context)


@pytest.mark.parametrize('outcome,preferred', [('regressed', 'current'), ('improved', 'baseline'),
                                           ('inconclusive', 'current'), ('equal', 'current')])
def test_contradictory_comparison_cannot_replace_better_baseline(outcome, preferred):
    context, output = review_context()
    comparison = context['comparison']
    output['review'].update(outcome=outcome, preferred_candidate_id=comparison[
        'candidate_id' if preferred == 'current' else 'baseline_candidate_id'])
    with pytest.raises(ServiceError, match='comparison_contradiction'):
        contract.validate_result(output, digest(context), context)
