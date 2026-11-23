"""Technical recovery keeps exact v2 Skill identity; all jobs/media are synthetic."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from test_animation_method_service import finish, start
from test_animation_skill_method_service import work


OWNER = 'developer'


def awaiting_review(service, commission_id):
    for _ in range(50):
        run = service.commissions.get(OWNER, commission_id)
        operation = next((row for row in run['operations']
            if row['stage'] == 'review' and row['status'] == 'reserved'), None)
        if operation is not None:
            return run, operation
        assert run['state'] not in {'completed', 'failed', 'cancelled'}, run['summary']
        service.advance(OWNER, commission_id)
    pytest.fail('technical retry did not reach a fresh review')


def fail_review(service, commission_id):
    run, operation = awaiting_review(service, commission_id)
    service.commissions.claim(OWNER, operation['id'])
    service.commissions.fail(OWNER, operation['id'], 'fixture_review_transport_failed')
    assert finish(service, commission_id)['state'] == 'failed'
    return service.commissions.get(OWNER, commission_id)


def authored(run):
    return next(row for row in run['operations']
        if row['stage'] == 'creator' and row['input'].get('mode') == 'create')


def test_v2_package_retry_and_retry_chain_reuse_persisted_method_without_paid_rework(work):
    service, creator, reviewer, renderer = work
    baseline = finish(service, start(service, use_wikiskill=False)['id'])
    original = fail_review(service, start(service, base_commission_id=baseline['id'])['id'])
    methods = service.methods.list_methods(OWNER)
    version = next(row for row in methods if row['version'] == 2)
    assert version['skill_package']['version'] == 2
    created = authored(original)
    assert version['id'] in original['candidates'][0]['method_versions']
    # Reproduce the rejected pre-fix write: a new author request identity
    # interprets the immutable version-2 package as a third method version.
    with pytest.raises(ServiceError, match='animation_method_skill_identity_mismatch') as rejected:
        service.methods.create_method(OWNER, created['result']['methods'][0], 'wrong-new-author:method:0')
    assert rejected.value.status == 409
    calls = len(creator.submissions), len(renderer.calls), len(reviewer.submissions)
    original_render = next(row for row in original['operations'] if row['stage'] == 'render')
    previous = original
    for index in range(2):
        request = dict(request_id=str(uuid4()), retry_of_commission_id=previous['id'],
            budget=dict(max_rounds=1, max_seconds=300))
        retry = service.start(OWNER, request)
        assert service.start(OWNER, request)['id'] == retry['id']
        recovered, review = awaiting_review(service, retry['id'])
        candidate = next(row for row in recovered['candidates']
            if row['id'] == review['input']['comparison']['candidate_id'])
        assert candidate['method_versions'] == [version['id']]
        assert candidate['source'] == created['result']['source']
        render = next(row for row in recovered['operations'] if row['stage'] == 'render')
        assert render['artifact_operation_id'] == original_render['id']
        assert render['result'] == original_render['result']
        assert (len(creator.submissions), len(renderer.calls)) == calls[:2]
        assert service.methods.list_methods(OWNER) == methods
        assert service.commissions.get(OWNER, previous['id']) == previous
        assert recovered['usage']['creator'] == recovered['usage']['render'] == 0
        # Replaying materialization is also idempotent within this retry.
        service._materialize(OWNER, recovered, render)
        assert service.methods.list_methods(OWNER) == methods
        if index == 0:
            previous = fail_review(service, retry['id'])
        else:
            completed = finish(service, retry['id'])
            assert completed['state'] == 'completed'
            assert completed['usage'] == dict(creator=0, render=0, review=1)
            assert len(reviewer.submissions) == calls[2] + 1
            assert service.artifact(OWNER, retry['id'], candidate['id'], 'media')[0]
            validation = service.methods.list_validations(OWNER, version['id'])[-1]
            demo = service.methods.get_demonstration(OWNER, validation['demonstration_version_id'])
            assert demo['provenance']['commission_id'] == retry['id']
            assert demo['provenance']['creator_operation_id'] == authored(recovered)['id']
    assert service.commissions.get(OWNER, original['id']) == original
    assert service.methods.list_methods(OWNER) == methods


@pytest.mark.parametrize('corruption', ['retry_relationship', 'source_operation', 'input', 'result'])
def test_materialization_rejects_untrusted_reused_author_lineage(work, corruption):
    service, _, _, _ = work
    original = fail_review(service, start(service, use_wikiskill=False)['id'])
    retry = service.start(OWNER, dict(request_id=str(uuid4()), retry_of_commission_id=original['id'],
        budget=dict(max_rounds=1, max_seconds=300)))
    recovered, _ = awaiting_review(service, retry['id'])
    render = next(row for row in recovered['operations'] if row['stage'] == 'render')
    changed = deepcopy(recovered)
    author = authored(changed)
    if corruption == 'retry_relationship':
        changed['frozen']['retry_of_commission_id'] = 'unrelated'
    elif corruption == 'source_operation':
        author['reused_from']['operation_id'] = next(row['id'] for row in original['operations']
            if row['stage'] == 'creator' and row['input'].get('mode') == 'plan')
    elif corruption == 'input':
        author['input']['goal'] += ' different input'
    else:
        author['result']['source']['files']['scene.html'] += '<!-- different source -->'
    before = service.methods.list_methods(OWNER)
    with pytest.raises(ServiceError, match='animation_method_retry_method_author_changed'):
        service._materialize(OWNER, changed, render)
    assert service.methods.list_methods(OWNER) == before
