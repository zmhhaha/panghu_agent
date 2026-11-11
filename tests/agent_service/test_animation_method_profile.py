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
    context['observation']['frames'] = [dict(frame_id='frame-' + str(i), file_name='frames/' + str(i) + '.png',
        sha256=str(i + 1) * 64, timestamp_seconds=2, candidate_id=candidate, media_sha256='f' * 64)
        for i, candidate in enumerate([baseline, current])]
    context['comparison'] = dict(candidate_id=current, baseline_candidate_id=baseline)
    output = result(context)
    output['methods'] = []
    output['review'] = dict(summary='Cause and consequence are separated', outcome='improved', preferred_candidate_id=current,
        criteria_results=[dict(criterion_id='cause', status='passed', evidence='The supplied frames show the change')],
        remaining_issues=[], next_guidance='', limitations=['Only still frames'])
    return context, output


def test_bundle_and_roles_are_distinct_and_verified():
    assert set(contract.verified_bundle()) == contract.BUNDLE_FILES
    assert contract.VideoAnimationMethodProfile().name == contract.AUTHOR_PROFILE
    reviewer = contract.VideoAnimationMethodProfile(True)
    assert reviewer.name == contract.REVIEW_PROFILE and 'vision' in reviewer.required_model_capabilities
    assert reviewer.required_tools == ('read_inputs', 'write')


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
