"""Classified review evidence and mandatory uncertainty; no model invocation."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation, video_picture_learning as legacy
from agent_service.profiles import video_picture_review_v2 as protocol
from test_picture_learning_profile import document as legacy_document, PNG


def policy():
    return dict(schema=protocol.POLICY_SCHEMA, evidence_scope='static_only', criteria=[
        dict(id='shape', requirement='required', text='Retain required shape, scale and coverage.'),
        dict(id='detail', requirement='optional', text='Optional dry-brush detail refinement.')])


def document():
    value = legacy_document('review')
    value['constraints']['review_policy'] = policy()
    for row in value['observation']['frames']:
        row['frame_id'] = row['purpose']
    return value


def result(context):
    return dict(schema=protocol.RESULT_SCHEMA, mode='review', input_sha256=digest(context),
        evidence_scope='static_only', review=dict(score=83, summary='Synthetic sampled-image fixture.',
            assessments=[dict(criterion_id='shape', status='satisfied', evidence_frame_ids=['output'],
                reason='The sampled shape and attachments agree.')], findings=[],
            limitations=['No continuous video or audio was observed.'], stop_recommended=False))


def finding(*, required=False, uncertain=False):
    return dict(id='f1', criterion_id='shape' if required else 'detail',
        severity='blocking' if required else 'advisory', certainty='uncertain' if uncertain else 'observed',
        evidence_frame_ids=['output'], observation='Synthetic visible concern, not actual artistic evidence.',
        recommendation='Retain the evidence and check this condition within the original allowance.')


def stage(tmp_path, context, raw=PNG):
    inputs = tmp_path/'inputs'
    inputs.mkdir()
    (inputs/protocol.INPUT_NAME).write_bytes(canonical_bytes(context))
    for frame in context['observation']['frames']:
        (inputs/frame['file_name']).write_bytes(raw)


def test_independent_profile_bundle_and_legacy_result_are_strictly_separated():
    profile = implementation(protocol.PROFILE)
    assert profile.name != legacy.REVIEW_PROFILE and profile.required_model_capabilities == ('text','tool_calls','vision')
    assert profile.required_tools == ('read_inputs','skill_search','skill_read','write')
    assert set(protocol.verified_bundle()) == protocol.BUNDLE_FILES
    assert legacy.BUNDLE_MANIFEST_SHA256 == 'f99ab90f2863220a59a6d760bb5a422717830d46774d76df45ef2ccd1a8ecde6'
    assert legacy.verified_bundle()
    value = document(); out = result(value)
    with pytest.raises(ServiceError):legacy.validate_result(out,digest(value),value)
    from test_picture_learning_profile import result as legacy_result
    with pytest.raises(ServiceError):protocol.validate_result(legacy_result(value),digest(value),value)
    with pytest.raises(ValueError):implementation(protocol.PROFILE,'2')


@pytest.mark.parametrize('uncertain',[False,True])
def test_optional_advice_preserved_without_blocking_or_becoming_user_acceptance(uncertain):
    value=document(); out=result(value); out['review']['findings']=[finding(uncertain=uncertain)]
    assert protocol.validate_result(out,digest(value),value)==out
    decision=protocol.quality_gate(out['review'],policy(),value['observation']['frames'],80)
    assert decision['passed'] is True and decision['advisory_finding_ids']==['f1']
    assert decision['technical_acceptance'] is decision['user_acceptance'] is False


@pytest.mark.parametrize('status',['violation','unresolved'])
def test_required_violation_and_unknown_both_block_with_matching_evidence(status):
    value=document(); out=result(value); review=out['review']
    review['assessments'][0]['status']=status
    review['findings']=[finding(required=True,uncertain=status=='unresolved')]
    assert protocol.validate_result(out,digest(value),value)==out
    assert protocol.quality_gate(review,policy(),value['observation']['frames'],80)['passed'] is False
    review['findings'][0]['severity']='advisory'
    with pytest.raises(ServiceError,match='required_finding_invalid'):
        protocol.validate_result(out,digest(value),value)


@pytest.mark.parametrize('change',['missing_assessment','duplicate_assessment','missing_blocker','wrong_certainty',
    'satisfied_with_blocker','optional_blocker','unknown_frame','duplicate_frame','reference_only',
    'unknown_criterion','duplicate_finding','unknown_assessment_status','unknown_severity','unknown_certainty',
    'old_issues','author_source','motion_claim','empty_reason','optional_assessment'])
def test_malformed_or_contradictory_classification_cannot_pass(change):
    value=document(); out=result(value); review=out['review']
    review['findings']=[finding()]
    if change=='missing_assessment':review['assessments']=[]
    elif change=='duplicate_assessment':review['assessments']*=2
    elif change=='missing_blocker':review['assessments'][0]['status']='unresolved'
    elif change=='wrong_certainty':
        review['assessments'][0]['status']='violation';review['findings']=[finding(required=True,uncertain=True)]
    elif change=='satisfied_with_blocker':review['findings']=[finding(required=True)]
    elif change=='optional_blocker':review['findings'][0]['severity']='blocking'
    elif change=='unknown_frame':review['findings'][0]['evidence_frame_ids']=['other-run-frame']
    elif change=='duplicate_frame':review['findings'][0]['evidence_frame_ids']=['output','output']
    elif change=='reference_only':review['findings'][0]['evidence_frame_ids']=['reference']
    elif change=='unknown_criterion':review['findings'][0]['criterion_id']='not-supplied'
    elif change=='duplicate_finding':review['findings']*=2
    elif change=='unknown_assessment_status':review['assessments'][0]['status']='probably_ok'
    elif change=='unknown_severity':review['findings'][0]['severity']='ignore'
    elif change=='unknown_certainty':review['findings'][0]['certainty']='proven'
    elif change=='old_issues':review['issues']=[]
    elif change=='author_source':out['source']={}
    elif change=='motion_claim':out['evidence_scope']='continuous_video'
    elif change=='empty_reason':review['assessments'][0]['reason']=' '
    elif change=='optional_assessment':review['assessments'][0]['criterion_id']='detail'
    with pytest.raises(ServiceError):protocol.validate_result(out,digest(value),value)


@pytest.mark.parametrize('score',[True,float('nan'),float('inf'),-1,101,10**1000])
def test_scores_reject_booleans_nonfinite_and_unbounded_integers(score):
    value=document(); out=result(value); out['review']['score']=score
    with pytest.raises(ServiceError):protocol.validate_result(out,digest(value),value)


def test_new_stop_and_low_score_block_even_without_findings():
    value=document(); review=result(value)['review']; frames=value['observation']['frames']
    review['stop_recommended']=True
    decision=protocol.quality_gate(review,policy(),frames,80)
    assert decision['passed'] is False and decision['reasons']==['review_recommends_stop']
    review['stop_recommended']=False;review['score']=79
    assert protocol.quality_gate(review,policy(),frames,80)['reasons']==['score_below_minimum']


@pytest.mark.parametrize('change',['missing_policy','no_required','unknown_requirement','duplicate_criterion',
    'duplicate_frame_id','jpeg_output','transfer_mode','source_handoff'])
def test_input_freezes_policy_and_static_lossless_output_identity(change):
    value=document()
    if change=='missing_policy':del value['constraints']['review_policy']
    elif change=='no_required':value['constraints']['review_policy']['criteria'][0]['requirement']='optional'
    elif change=='unknown_requirement':value['constraints']['review_policy']['criteria'][0]['requirement']='suggested'
    elif change=='duplicate_criterion':value['constraints']['review_policy']['criteria']*=2
    elif change=='duplicate_frame_id':value['observation']['frames'][0]['frame_id']='output'
    elif change=='jpeg_output':value['observation']['frames'][-1]['file_name']='output.jpg'
    elif change=='transfer_mode':value['mode']='transfer_plan'
    elif change=='source_handoff':value['source_handoff']={}
    with pytest.raises(ServiceError):protocol.validate_input(value)


def test_policy_validation_returns_isolated_copy_and_result_hash_cannot_change():
    original=policy(); checked=protocol.validate_review_policy(original)
    assert checked==original and checked is not original
    checked['criteria'][0]['text']='changed';assert checked!=original
    value=document();out=result(value);value['constraints']['review_policy']['criteria'][0]['text']='new goal'
    with pytest.raises(ServiceError,match='binding_mismatch'):protocol.validate_result(out,out['input_sha256'],value)


def test_collector_requires_exact_images_stopped_writer_and_profile(tmp_path):
    value=document();stage(tmp_path,value);out=result(value)
    (tmp_path/'output.json').write_bytes(canonical_bytes(out));attempt=str(uuid4())
    envelope=SimpleNamespace(profile=protocol.PROFILE,profile_revision='1',attempt_id=attempt,
        task=dict(input_sha256=digest(value)),checkpoint_inputs=[],checkpoint_ref=None,inputs=[
            InlineFile.from_bytes(protocol.INPUT_NAME,canonical_bytes(value)),
            *[InlineFile.from_bytes(f['file_name'],PNG) for f in value['observation']['frames']]])
    proof=StopEvidence(execution_id=attempt,all_writers_stopped=True,resource_ids=('synthetic-writer',))
    profile=protocol.VideoPictureReviewV2Profile()
    files=profile.collect_output(tmp_path,envelope,proof)
    assert files[0].name==protocol.RESULT_NAME and json.loads(files[0].content())==out
    assert 'picture-review-result.v2' in profile.build_prompt(envelope.task)
    envelope.profile=legacy.REVIEW_PROFILE
    with pytest.raises(ServiceError):profile.collect_output(tmp_path,envelope,proof)
    envelope.profile=protocol.PROFILE
    (tmp_path/'inputs'/'output.png').write_bytes(PNG+b'changed')
    with pytest.raises(ServiceError,match='image_changed'):profile.collect_output(tmp_path,envelope,proof)


def test_more_than_two_mib_original_is_rejected_without_reencoding(tmp_path):
    value=document();raw=PNG+b'X'*(protocol.MAX_IMAGE_BYTES+1-len(PNG))
    for frame in value['observation']['frames']:frame['sha256']=hashlib.sha256(raw).hexdigest()
    stage(tmp_path,value,raw)
    with pytest.raises(ServiceError,match='invalid_video_file'):
        protocol.VideoPictureReviewV2Profile().validate_inputs(tmp_path,dict(input_sha256=digest(value)))
    assert (tmp_path/'inputs'/'output.png').read_bytes()==raw


def test_list_and_text_limits_reject_oversized_reports():
    value=document(); out=result(value);out['review']['limitations']=['No temporal observation.']*33
    with pytest.raises(ServiceError):protocol.validate_result(out,digest(value),value)
    out=result(value);out['review']['summary']='x'*8001
    with pytest.raises(ServiceError):protocol.validate_result(out,digest(value),value)
