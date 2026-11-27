"""Actual encoded images and existing validators; no model or render invocation."""
from copy import deepcopy
import hashlib
import io
import random
from uuid import uuid4

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_picture_review_v2 as protocol
from video_workbench.learning_review_reliability import (CONTRACT, freeze_binding, validate_binding,
    review_document, capture_frames, validate_transport, validate_provenance, decision)
from video_workbench.scene_space_capture import SAMPLE_CAPTURE_POLICY
from video_workbench.scene_space_renderer import render_capability
from video_workbench.scene_review_continuation import MODEL_LIMITS, MODEL_LIMIT_CAPABILITY


def png(*, noisy=False):
    image = (Image.frombytes('RGB', (1280, 720), random.Random(19).randbytes(1280 * 720 * 3))
             if noisy else Image.new('RGB', (1280, 720), (33, 45, 61)))
    out = io.BytesIO(); image.save(out, format='PNG'); return out.getvalue()


def route():
    return dict(profile=protocol.PROFILE, profile_revision='1', ready=True, configured=True,
        model_request_limits=deepcopy(MODEL_LIMIT_CAPABILITY),
        supports_inputs=True, required_model_capabilities=['text', 'tool_calls', 'vision'], max_timeout_seconds=3600)


class ReviewRoute:
    profile = protocol.PROFILE
    profile_revision = '1'
    def preflight(self, _deadline):
        return route()


def binding():
    return freeze_binding(CONTRACT, gateway=ReviewRoute(), capability=render_capability(),
        width=1280, height=720, deadline_at=9999999999)


def capture(raw, absolute=24):
    return dict(schema='video.scene-space-frame-capture.v1', absolute_frame=absolute,
        relative_frame=absolute, format='png', quality=None,
        sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def fixture(*, noisy=False):
    raw = png(noisy=noisy); blobs = {}
    def store(value):
        sha = hashlib.sha256(value).hexdigest(); blobs[sha] = value; return sha
    rows = [dict(raw=raw, mime='image/png', capture=capture(raw, frame), timestamp_seconds=frame / 24)
            for frame in [0, 24, 47]]
    result = dict(sample_capture=deepcopy(SAMPLE_CAPTURE_POLICY),
        sample_frame_range=dict(start_frame=0, end_frame=48))
    candidate = dict(source_sha256='b' * 64, scene_space=dict(plan=dict(id='fixture-plan')))
    observation, provenance = capture_frames(rows, result=result, candidate=candidate,
        render_id='render-one', frame_range=result['sample_frame_range'], store_blob=store)
    frames = [dict(row, file_name=row['frame_id'] + '.png', purpose='output') for row in observation['frames']]
    document = review_document(dict(project_id='study', experiment_id='run', goal='Preserve the actual requested style.',
        criteria=['Visible occlusion'], feedback='', observation=dict(frames=frames)), binding())
    return raw, blobs, document, provenance


def opinion(document, *, status='satisfied', advisory=True):
    policy = document['constraints']['review_policy']; frames = document['observation']['frames']
    output_id = next(row['frame_id'] for row in frames if row['purpose'] == 'output')
    result = dict(score=91, summary='Synthetic contract finding, not artistic acceptance.',
        assessments=[dict(criterion_id=row['id'], status=status, reason='Synthetic fixture.',
            evidence_frame_ids=[output_id]) for row in policy['criteria'] if row['requirement'] == 'required'],
        findings=[], limitations=['Only supplied static images.'], stop_recommended=False)
    if status != 'satisfied':
        result['findings'] = [dict(id='blocking-' + str(index), criterion_id=row['criterion_id'],
            severity='blocking', certainty='observed' if status == 'violation' else 'uncertain',
            evidence_frame_ids=row['evidence_frame_ids'], observation='Required criterion is not established.',
            recommendation='Retain the issue.') for index, row in enumerate(result['assessments'])]
    elif advisory:
        result['findings'] = [dict(id='advice', criterion_id='optional-refinement', severity='advisory',
            certainty='observed', evidence_frame_ids=[output_id],
            observation='Optional refinement.', recommendation='Preserve required content.')]
    return dict(schema=protocol.RESULT_SCHEMA, input_sha256=digest(document), mode='review',
        review=result, evidence_scope='static_only')


def receipt(document, blobs, job_id='job-one'):
    return dict(schema='agent-model-image-evidence.v1', evidence_scope='completed_upstream_transport',
        job_id=job_id, attempt_id=str(uuid4()), request_sha256='c' * 64,
        images=[dict(sha256=row['sha256'], model_request_completed=True,
            model_request_sha256='d' * 64, media_type='image/png' if row['file_name'].endswith('.png') else 'image/jpeg',
            byte_length=len(blobs[row['sha256']]), input_item_index=index, part_index=0)
            for index, row in enumerate(document['observation']['frames'])])


def test_original_renderer_png_capture_clock_and_model_transport_are_identical():
    raw, blobs, document, provenance = fixture()
    validate_transport(document, read_blob=blobs.__getitem__, provenance=provenance)
    validate_provenance(provenance, source_sha256='b' * 64, plan_sha256=digest(dict(id='fixture-plan')),
        render_id='render-one', frame_range=dict(start_frame=0, end_frame=48), read_blob=blobs.__getitem__)
    assert all(row['conversion'] == 'identity_original_png' and row['model_input_bytes'] == len(raw) for row in provenance)
    assert [row['absolute_frame'] for row in provenance] == [0, 24, 47]
    assert all(row['file_name'].endswith('.png') for row in document['observation']['frames'])


@pytest.mark.parametrize('status,passed', [('satisfied', True), ('violation', False), ('unresolved', False)])
def test_existing_classified_gate_controls_required_findings_and_optional_advice(status, passed):
    _, blobs, document, _ = fixture()
    verdict = decision(opinion(document, status=status), document, binding(), receipt(document, blobs),
        job_id='job-one', read_blob=blobs.__getitem__)
    assert verdict['passed'] is passed


@pytest.mark.parametrize('field,value', [('media_type','image/jpeg'), ('byte_length',1),
    ('model_request_completed',False), ('model_request_sha256','invented')])
def test_hash_alone_cannot_forge_image_transport(field, value):
    _, blobs, document, _ = fixture(); proof = receipt(document, blobs)
    proof['images'][0][field] = value
    with pytest.raises(ServiceError, match='transport_metadata'):
        decision(opinion(document), document, binding(), proof, job_id='job-one', read_blob=blobs.__getitem__)


def test_completed_transport_must_belong_to_the_actual_job():
    _, blobs, document, _ = fixture()
    with pytest.raises(ServiceError, match='transport_identity'):
        decision(opinion(document), document, binding(), receipt(document, blobs),
            job_id='a-different-job', read_blob=blobs.__getitem__)


@pytest.mark.parametrize('field,value', [('review_route',dict(route(), profile_revision='2')),
    ('renderer_capability',dict(render_capability(), sample_capture=None)),
    ('continuous_motion_verified',True), ('minimum_score',True),
    ('model_limits',dict(max_requests=80,max_output_tokens=8192)),
    ('model_limits_scope',dict(MODEL_LIMIT_CAPABILITY,max_requests_scope='each_attempt'))])
def test_rehash_cannot_authorize_an_inconsistent_frozen_contract(field, value):
    value_binding = binding(); value_binding[field] = value
    value_binding['sha256'] = digest({k:v for k,v in value_binding.items() if k!='sha256'})
    with pytest.raises(ServiceError): validate_binding(value_binding)


def test_four_mib_capture_does_not_relax_two_mib_model_transport_or_resize():
    raw, blobs, document, provenance = fixture(noisy=True)
    assert 2*1024*1024 < len(raw) < 4*1024*1024
    before = dict(blobs)
    with pytest.raises(ServiceError, match='model_image_too_large'):
        validate_transport(document, read_blob=blobs.__getitem__, provenance=provenance)
    assert blobs == before and raw in blobs.values()


def test_capture_metadata_and_source_identity_are_revalidated_on_resume():
    _, blobs, _, provenance = fixture(); provenance[1]['capture']['relative_frame'] += 1
    with pytest.raises(ServiceError):
        validate_provenance(provenance, source_sha256='b'*64, plan_sha256=digest(dict(id='fixture-plan')),
            render_id='render-one', frame_range=dict(start_frame=0,end_frame=48), read_blob=blobs.__getitem__)
