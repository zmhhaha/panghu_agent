"""No-model submission/handoff bounds for full confirmed PPT instructions."""
from uuid import uuid4

from pydantic import ValidationError
import pytest

from agent_service.contracts import ExecutionEnvelope, JobRequest, canonical_bytes


PPT_PROFILES = (
    'ppt.plan_initial.v1', 'ppt.plan_options.v1', 'ppt.prepare.v1',
    'ppt.generate.v1', 'ppt.revise.v1',
)


def task_of_bytes(size, *, unicode=False):
    # A Chinese plan must use its UTF-8 size, not Python's character count.
    content_bytes = size - len(canonical_bytes({'instructions': ''}))
    content = ('研' * (content_bytes // 3) + 'a' * (content_bytes % 3)
               if unicode else 'a' * content_bytes)
    task = {'instructions': content}
    assert len(canonical_bytes(task)) == size
    return task


def parse_request(profile, task):
    return JobRequest(profile=profile, profile_revision='v1',
                      business_ref='confirmed-ppt-plan', task=task)


def parse_envelope(profile, task):
    return ExecutionEnvelope(
        job_id=str(uuid4()), attempt_id=str(uuid4()), fence=1,
        engine='pi', profile=profile, profile_revision='v1',
        runtime_revision='runtime-v1', image_digest='sha256:' + 'a' * 64,
        deadline_at=1800000300.0, lease_expires_at=1800000200.0, task=task,
    )


@pytest.mark.parametrize('parse', [parse_request, parse_envelope], ids=['request', 'envelope'])
@pytest.mark.parametrize('profile', PPT_PROFILES)
def test_full_ppt_task_survives_submission_and_handoff_with_a_finite_bound(parse, profile):
    # The incident carried 45,650 UTF-8 bytes before any Agent submission.
    incident_sized = task_of_bytes(45650, unicode=True)
    assert parse(profile, incident_sized).task == incident_sized
    boundary = task_of_bytes(256 * 1024, unicode=True)
    assert parse(profile, boundary).task == boundary
    with pytest.raises(ValidationError, match='Task exceeds its bound'):
        parse(profile, task_of_bytes(256 * 1024 + 1, unicode=True))


@pytest.mark.parametrize('parse', [parse_request, parse_envelope], ids=['request', 'envelope'])
@pytest.mark.parametrize('profile', [
    'workspace-task.v1', 'research.search.v1', 'video.generate.v1',
    'ppt.unknown.v1', 'ppt.prepare.v2', 'ppt.prepare.v1.extra',
])
def test_other_profiles_keep_the_original_utf8_byte_bound(parse, profile):
    boundary = task_of_bytes(32 * 1024, unicode=True)
    assert parse(profile, boundary).task == boundary
    with pytest.raises(ValidationError, match='Task exceeds its bound'):
        parse(profile, task_of_bytes(32 * 1024 + 1, unicode=True))


def test_ppt_allowance_does_not_relax_submission_nesting_bound():
    task = {'value': 'small'}
    for _ in range(11):
        task = {'nested': task}
    with pytest.raises(ValidationError, match='Task nesting exceeds its bound'):
        parse_request('ppt.prepare.v1', task)
