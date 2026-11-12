"""Explicit historical grouping keeps execution evidence intact; no paid work."""
import json
import sqlite3
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_service import AnimationMethodService
from test_animation_method_service import work, start, finish
from test_animation_method_retry import fail_run
from test_animation_method_effect_groups import effect_for


REASON = 'Owner confirmed the failed planning attempt and later sample are one effect.'


def ledger_snapshot(service):
    with sqlite3.connect(service.commissions.path) as db:
        return {name: list(db.execute('SELECT * FROM ' + name + ' ORDER BY rowid'))
                for name in ('animation_method_commissions', 'animation_method_commission_operations',
                             'animation_method_commission_requests', 'animation_method_commission_events')}


def memberships(service):
    with sqlite3.connect(service.metadata_path) as db:
        return list(db.execute('SELECT * FROM effect_memberships ORDER BY rowid'))


def pair(service, *, failed_sample=False):
    previous = fail_run(service, start(service, title='Earlier planning attempt')['id'])
    current = start(service, title='Current sample')
    current = (fail_run(service, current['id'], with_sample=True) if failed_sample
               else finish(service, current['id']))
    return previous, current


def link(service, previous, current, **options):
    return service.record_effect_membership('developer', previous['id'], current['id'],
                                            reason=options.get('reason', REASON))


def test_two_historical_pairs_become_three_effects_without_rewriting_any_execution(work):
    service, creator, reviewer, renderer = work
    unrelated = finish(service, start(service, title='Existing separate effect')['id'])
    first_old, first = pair(service)
    second_old, second = pair(service, failed_sample=True)
    assert len(service.catalogue('developer')['effects']) == 5
    before = ledger_snapshot(service)
    calls = (list(creator.submissions), list(reviewer.submissions), list(renderer.calls))
    media = {run['id']: service.artifact('developer', run['id'], run['best_candidate_id'], 'media')
             for run in (first, second)}

    for previous, current in ((first_old, first), (second_old, second)):
        record = link(service, previous, current)
        assert record['source'] == 'explicit_owner_historical_grouping'
        assert record['reason'] == REASON and record['created_at'] == pytest.approx(service.clock(), abs=5)
        effect = effect_for(service, previous['id'])
        assert effect['id'] == effect['open_commission_id'] == current['id']
        assert effect['title'] == current['title']
        assert effect['best_candidate']['id'] == current['best_candidate_id']
        assert [row['kind'] for row in effect['commissions']] == ['historical_attempt', 'original']
        assert [row['state'] for row in effect['commissions']] == [previous['state'], current['state']]
        assert effect['relationship_status'] == 'verified'
        detail = service.get('developer', previous['id'])
        assert detail['id'] == previous['id'] and detail['state'] == 'failed' and detail['candidates'] == []
        assert detail['effect']['id'] == current['id']
        assert detail['retry_of_commission_id'] is None
        assert service.artifact('developer', current['id'], current['best_candidate_id'], 'media') == media[current['id']]

    catalogue = service.catalogue('developer')
    assert len(catalogue['effects']) == 3 and len(catalogue['commissions']) == 5
    assert {row['id'] for row in catalogue['effects']} == {unrelated['id'], first['id'], second['id']}
    assert effect_for(service, second['id'])['state'] == 'failed'
    assert ledger_snapshot(service) == before
    assert calls == (creator.submissions, reviewer.submissions, renderer.calls)


def test_identical_membership_is_durable_idempotent_and_changed_reason_or_target_conflicts(work):
    service, creator, reviewer, renderer = work
    previous, current = pair(service)
    other = finish(service, start(service)['id'])
    record = link(service, previous, current)
    rows = memberships(service)
    reopened = AnimationMethodService(service.root, author_gateway=creator,
        review_gateway=reviewer, renderer=renderer)
    assert link(reopened, previous, current) == record
    assert memberships(reopened) == rows
    assert effect_for(reopened, previous['id'])['id'] == current['id']
    with pytest.raises(ServiceError, match='membership_conflict'):
        link(reopened, previous, current, reason='Different claimed authorization')
    with pytest.raises(ServiceError, match='membership_conflict'):
        link(reopened, previous, other)
    assert memberships(reopened) == rows


def test_only_owned_terminal_originals_can_be_linked_and_cancellation_is_supported(work):
    service, _, _, _ = work
    previous, current = pair(service)
    with pytest.raises(ServiceError, match='not_found'):
        service.record_effect_membership('foreign-owner', previous['id'], current['id'], reason=REASON)
    foreign = service.commissions.create('foreign-owner', dict(goal='PRIVATE FOREIGN EFFECT',
        budget=dict(creator=1, review=0, render=0, seconds=300)), 'foreign-original')
    service.commissions.stop('foreign-owner', foreign['id'], 'failed', 'Fixture stopped')
    with pytest.raises(ServiceError, match='not_found'):
        service.record_effect_membership('developer', previous['id'], foreign['id'], reason=REASON)
    with pytest.raises(ServiceError, match='self_reference'):
        link(service, previous, previous)
    with pytest.raises(ServiceError, match='request_invalid'):
        link(service, previous, current, reason=' ')
    queued = start(service)
    with pytest.raises(ServiceError, match='previous_ineligible'):
        link(service, queued, current)
    with pytest.raises(ServiceError, match='previous_ineligible'):
        link(service, current, finish(service, start(service)['id']))
    with pytest.raises(ServiceError, match='canonical_ineligible'):
        link(service, previous, queued)
    with pytest.raises(ServiceError, match='canonical_ineligible'):
        link(service, previous, fail_run(service, start(service)['id']))
    cancelled = start(service)
    service.cancel('developer', cancelled['id'], {})
    link(service, cancelled, current)
    assert effect_for(service, cancelled['id'])['id'] == current['id']
    assert service.catalogue('foreign-owner')['effects'][0]['title'] == 'PRIVATE FOREIGN EFFECT'
    assert foreign['id'] not in json.dumps(service.catalogue('developer'))


def test_operator_verifies_existing_media_before_recording_membership(work):
    service, _, _, renderer = work
    previous, current = pair(service)
    stored = service.commissions.get('developer', current['id'])
    candidate = next(row for row in stored['candidates'] if row['id'] == stored['best_candidate_id'])
    renderer.media_path(candidate['operation_id']).write_bytes(b'Altered fixture media')
    with pytest.raises(ServiceError, match='media_changed'):
        link(service, previous, current)
    assert memberships(service) == []


@pytest.mark.parametrize('relation', ['source-retry', 'source-continuation', 'target-retry',
                                    'target-continuation', 'target-transfer'])
def test_historical_annotation_cannot_override_existing_frozen_lineage(work, relation):
    service, _, _, _ = work
    previous, current = pair(service)
    if relation == 'source-retry':
        previous = fail_run(service, start(service, retry_of_commission_id=previous['id'])['id'])
    elif relation == 'source-continuation':
        previous = fail_run(service, start(service, base_commission_id=current['id'])['id'])
    elif relation == 'target-retry':
        current = finish(service, start(service, retry_of_commission_id=previous['id'])['id'])
    elif relation == 'target-continuation':
        current = finish(service, start(service, base_commission_id=current['id'])['id'])
    else:
        transfer = service.transfer('developer', current['id'], dict(request_id=str(uuid4()),
            goal='Different branching structure', structural_changes={'relations': {'before': 'chain', 'after': 'fork'}},
            budget={'max_rounds': 1, 'max_seconds': 300}))
        current = finish(service, transfer['id'])
    before = ledger_snapshot(service)
    with pytest.raises(ServiceError, match='frozen_relation_conflict'):
        link(service, previous, current)
    assert memberships(service) == [] and ledger_snapshot(service) == before


@pytest.mark.parametrize('corruption', ['missing', 'foreign', 'self', 'snapshot', 'cycle'])
def test_invalid_metadata_never_merges_or_exposes_a_foreign_effect(work, corruption):
    service, _, _, _ = work
    previous, current = pair(service)
    raw_previous = service.commissions.get('developer', previous['id'])
    raw_current = service.commissions.get('developer', current['id'])
    target_id = current['id']
    if corruption == 'missing':
        target_id = 'missing-commission'
    elif corruption == 'foreign':
        foreign = service.commissions.create('foreign-owner', dict(goal='PRIVATE HIDDEN TITLE',
            budget=dict(creator=1, review=0, render=0, seconds=300)), 'hidden-original')
        target_id = foreign['id']
    elif corruption in {'self', 'cycle'}:
        target_id = previous['id'] if corruption == 'self' else current['id']
    record = dict(commission_id=previous['id'], canonical_commission_id=target_id,
        commission_frozen_sha256=raw_previous['frozen_sha256'],
        canonical_frozen_sha256='invalid' if corruption == 'snapshot' else raw_current['frozen_sha256'])
    with sqlite3.connect(service.metadata_path) as db:
        db.execute('INSERT INTO effect_memberships VALUES (?,?,?,?)',
            ('developer', previous['id'], target_id, json.dumps(record)))
        if corruption == 'cycle':
            reverse = dict(record, commission_id=current['id'], canonical_commission_id=previous['id'],
                commission_frozen_sha256=raw_current['frozen_sha256'],
                canonical_frozen_sha256=raw_previous['frozen_sha256'])
            db.execute('INSERT INTO effect_memberships VALUES (?,?,?,?)',
                ('developer', current['id'], previous['id'], json.dumps(reverse)))
    before = ledger_snapshot(service)
    catalogue = service.catalogue('developer')
    # The invalid reverse record must not make its valid-looking counterpart
    # usable: the group remains independent rather than taking a cycle.
    assert len(catalogue['effects']) == 2
    assert effect_for(service, previous['id'])['relationship_status'] == 'needs_review'
    assert 'PRIVATE HIDDEN TITLE' not in json.dumps(catalogue)
    assert ledger_snapshot(service) == before


def test_normal_continuation_retry_and_transfer_stay_in_canonical_effect_without_new_causality(work):
    service, _, _, _ = work
    previous, current = pair(service)
    link(service, previous, current)
    continued = finish(service, start(service, base_commission_id=current['id'])['id'])
    transfer = service.transfer('developer', current['id'], dict(request_id=str(uuid4()),
        goal='An alternative task', structural_changes={'relations': {'before': 'chain', 'after': 'fork'}},
        budget={'max_rounds': 1, 'max_seconds': 300}))
    finish(service, transfer['id'])
    restarted = start(service, retry_of_commission_id=previous['id'])
    for row in (previous, current, continued, transfer, restarted):
        assert service.get('developer', row['id'])['effect']['id'] == current['id']
    effect = effect_for(service, restarted['id'])
    assert effect['state'] == 'queued' and effect['open_commission_id'] == continued['id']
    assert len(effect['commissions']) == 5
    assert [row['kind'] for row in effect['commissions']] == [
        'historical_attempt', 'original', 'continuation', 'transfer', 'retry']
    # The annotation did not turn the early failure into a creative baseline.
    frozen = service.commissions.get('developer', restarted['id'])['frozen']
    assert frozen['retry_of_commission_id'] == previous['id'] and 'base_commission_id' not in frozen


def test_existing_retries_and_a_running_historical_descendant_keep_canonical_retry_representative(work):
    service, creator, reviewer, renderer = work
    previous, current = pair(service, failed_sample=True)
    canonical_retry = finish(service, start(service, retry_of_commission_id=current['id'])['id'])
    empty_retry = fail_run(service, start(service, retry_of_commission_id=previous['id'])['id'])
    alternate = finish(service, start(service, retry_of_commission_id=previous['id'])['id'])
    running = start(service, retry_of_commission_id=previous['id'])
    service.advance('developer', running['id'])
    assert service.get('developer', running['id'])['state'] == 'running'
    before = ledger_snapshot(service)
    calls = (list(creator.submissions), list(reviewer.submissions), list(renderer.calls))
    link(service, previous, current)
    effect = effect_for(service, running['id'])
    assert len(service.catalogue('developer')['effects']) == 1
    assert effect['id'] == current['id'] and effect['state'] == 'running'
    assert effect['open_commission_id'] == canonical_retry['id']
    assert effect['best_candidate']['id'] == canonical_retry['best_candidate_id']
    assert len(effect['commissions']) == 6
    for row in (previous, current, canonical_retry, empty_retry, alternate, running):
        assert service.get('developer', row['id'])['effect']['id'] == current['id']
    assert ledger_snapshot(service) == before
    assert calls == (creator.submissions, reviewer.submissions, renderer.calls)
