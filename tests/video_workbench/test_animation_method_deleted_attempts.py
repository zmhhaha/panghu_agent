"""Explicitly remove empty failures from the library without erasing ancestry."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_service import AnimationMethodService
from test_animation_method_service import work, start, finish
from test_animation_method_retry import fail_run
from test_animation_method_effect_groups import effect_for, alter_frozen_fixture
from test_animation_method_historical_groups import ledger_snapshot, memberships


REASON = 'Owner requested removal of the failed test attempts, retaining successful samples.'


def remove(service, identity, **options):
    return service.record_failed_attempt_deletion('developer', identity,
        reason=options.get('reason', REASON))


def deletion_rows(service):
    with sqlite3.connect(service.metadata_path) as db:
        return list(db.execute('SELECT * FROM commission_deletions ORDER BY rowid'))


def test_remove_failed_group_preserves_independent_successful_retry_and_later_samples(work):
    service, creator, reviewer, renderer = work
    old = fail_run(service, start(service, title='Earlier Canvas attempt')['id'])
    old_retry = fail_run(service, start(service, retry_of_commission_id=old['id'])['id'])
    succeeded = finish(service, start(service, retry_of_commission_id=old_retry['id'])['id'])
    later = finish(service, start(service, title='Later Canvas sample')['id'])
    service.record_effect_membership('developer', old['id'], later['id'], reason='Earlier grouping')
    before = ledger_snapshot(service)
    calls = deepcopy((creator.submissions, reviewer.submissions, renderer.calls))
    media = {row['id']: service.artifact('developer', row['id'], row['best_candidate_id'], 'media')
             for row in (succeeded, later)}

    first = remove(service, old['id'])
    second = remove(service, old_retry['id'])
    assert len(first['removed_memberships']) == 1
    assert second['removed_memberships'] == [] and memberships(service) == []
    catalogue = service.catalogue('developer')
    assert {row['id'] for row in catalogue['commissions']} == {succeeded['id'], later['id']}
    assert {row['id'] for row in catalogue['effects']} == {succeeded['id'], later['id']}
    retry_effect = effect_for(service, succeeded['id'])
    assert retry_effect['open_commission_id'] == succeeded['id']
    assert retry_effect['relationship_status'] == 'verified'
    assert retry_effect['relationship_issues'] == []
    assert len(retry_effect['commissions']) == 1
    assert retry_effect['commissions'][0]['retry_of_commission_id'] == old_retry['id']
    for row in (old, old_retry):
        with pytest.raises(ServiceError, match='commission_not_found'):
            service.get('developer', row['id'])
        assert service.commissions.get('developer', row['id'])['state'] == 'failed'
    for row in (succeeded, later):
        assert service.get('developer', row['id'])['state'] == 'completed'
        assert service.artifact('developer', row['id'], row['best_candidate_id'], 'media') == media[row['id']]
    assert ledger_snapshot(service) == before
    assert calls == (creator.submissions, reviewer.submissions, renderer.calls)


def test_deletion_replay_retains_removed_memberships_after_restart_and_conflicts_on_reason(work):
    service, creator, reviewer, renderer = work
    failed = fail_run(service, start(service)['id'])
    later = finish(service, start(service)['id'])
    service.record_effect_membership('developer', failed['id'], later['id'], reason='Original grouping')
    record = remove(service, failed['id'])
    assert record['removed_memberships'] and memberships(service) == []
    rows = deletion_rows(service)
    reopened = AnimationMethodService(service.root, author_gateway=creator,
        review_gateway=reviewer, renderer=renderer)
    assert remove(reopened, failed['id']) == record
    assert deletion_rows(reopened) == rows
    with pytest.raises(ServiceError, match='deletion_conflict'):
        remove(reopened, failed['id'], reason='A different asserted request')
    assert deletion_rows(reopened) == rows


def test_completed_samples_and_failed_samples_or_retained_baselines_cannot_be_removed(work):
    service, _, _, _ = work
    completed = finish(service, start(service)['id'])
    failed_sample = fail_run(service, start(service)['id'], with_sample=True)
    baseline = fail_run(service, start(service, base_commission_id=completed['id'])['id'])
    queued = start(service)
    cancelled = start(service)
    service.cancel('developer', cancelled['id'], {})
    before = ledger_snapshot(service)
    for row in (completed, failed_sample, baseline, queued, cancelled):
        with pytest.raises(ServiceError, match='deletion_ineligible'):
            remove(service, row['id'])
    assert deletion_rows(service) == []
    assert ledger_snapshot(service) == before


def test_deleted_attempts_cannot_be_retried_replayed_or_relinked(work):
    service, creator, reviewer, renderer = work
    failed = fail_run(service, start(service)['id'])
    later = finish(service, start(service)['id'])
    frozen = service.commissions.get('developer', failed['id'])['frozen']
    remove(service, failed['id'])
    before = ledger_snapshot(service)
    calls = deepcopy((creator.submissions, reviewer.submissions, renderer.calls))
    with pytest.raises(ServiceError, match='commission_not_found'):
        start(service, retry_of_commission_id=failed['id'])
    with pytest.raises(ServiceError, match='commission_not_found'):
        service.start('developer', dict(goal=frozen['goal'], duration_seconds=18,
            request_id=failed['request_id'], budget=dict(max_rounds=1, max_seconds=300)))
    with pytest.raises(ServiceError, match='commission_not_found'):
        service.record_effect_membership('developer', failed['id'], later['id'], reason=REASON)
    assert ledger_snapshot(service) == before
    assert calls == (creator.submissions, reviewer.submissions, renderer.calls)


def test_preserved_successful_retry_request_replay_works_after_ancestor_deletion(work):
    service, creator, reviewer, renderer = work
    failed = fail_run(service, start(service, title='Earlier attempt')['id'])
    body = dict(request_id=str(uuid4()), retry_of_commission_id=failed['id'],
        goal=failed['goal'], budget=dict(max_rounds=1, max_seconds=300))
    succeeded = finish(service, service.start('developer', body)['id'])
    remove(service, failed['id'])
    before = ledger_snapshot(service)
    calls = deepcopy((creator.submissions, reviewer.submissions, renderer.calls))
    replay = service.start('developer', body)
    assert replay['id'] == succeeded['id'] and replay['state'] == 'completed'
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.start('developer', dict(body, goal='Changed replay goal'))
    with pytest.raises(ServiceError, match='commission_not_found'):
        service.start('developer', dict(body, request_id=str(uuid4())))
    assert ledger_snapshot(service) == before
    assert calls == (creator.submissions, reviewer.submissions, renderer.calls)


def test_only_owned_failures_with_explicit_reason_can_be_removed(work):
    service, _, _, _ = work
    failed = fail_run(service, start(service)['id'])
    for reason in ('', ' ', 'x' * 2001, None):
        with pytest.raises(ServiceError, match='request_invalid'):
            remove(service, failed['id'], reason=reason)
    with pytest.raises(ServiceError, match='not_found'):
        service.record_failed_attempt_deletion('another-owner', failed['id'], reason=REASON)
    with pytest.raises(ServiceError, match='not_found'):
        remove(service, str(uuid4()))
    assert deletion_rows(service) == []


def test_invalid_deletion_annotations_do_not_hide_records(work):
    service, _, _, _ = work
    failed = fail_run(service, start(service)['id'])
    record = remove(service, failed['id'])
    corruptions = [dict(owner='foreign'), dict(commission_frozen_sha256='0' * 64),
        dict(schema='unknown'), dict(reason=' '), dict(source='not-an-explicit-request'),
        dict(created_at=-1), dict(created_at=True), dict(created_at=float('inf'))]
    for changes in corruptions:
        invalid = dict(record, **changes)
        with sqlite3.connect(service.metadata_path) as db:
            db.execute('UPDATE commission_deletions SET document=?', (json.dumps(invalid),))
        assert service.get('developer', failed['id'])['id'] == failed['id']
        assert len(service.catalogue('developer')['effects']) == 1


def test_deleting_ancestor_does_not_conceal_an_invalid_retry_relationship(work):
    service, _, _, _ = work
    failed = fail_run(service, start(service)['id'])
    succeeded = finish(service, start(service, retry_of_commission_id=failed['id'])['id'])
    alter_frozen_fixture(service, succeeded['id'], method_versions=['unknown-method'])
    remove(service, failed['id'])
    effect = effect_for(service, succeeded['id'])
    assert effect['relationship_status'] == 'needs_review'
    assert effect['relationship_issues'] == ['retry_relationship_invalid']
    assert effect['best_candidate']['id'] == succeeded['best_candidate_id']
