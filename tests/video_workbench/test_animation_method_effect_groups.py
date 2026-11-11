"""Explicit effect history projections over real SQLite; no paid execution."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest

from agent_service.contracts import digest
from video_workbench.animation_method_service import _effect_views
from test_animation_method_service import work, start, finish


def history(service):
    with sqlite3.connect(service.commissions.path) as db:
        return list(db.execute('SELECT id,owner,state,document FROM '
                               'animation_method_commissions ORDER BY rowid'))


def effect_for(service, commission_id):
    return next(row for row in service.catalogue('developer')['effects']
                if any(run['id'] == commission_id for run in row['commissions']))


def alter_frozen_fixture(service, commission_id, **changes):
    """Model an imported unresolved relation; production reads must not repair it."""
    with sqlite3.connect(service.commissions.path) as db:
        row = db.execute('SELECT document FROM animation_method_commissions WHERE id=?',
                         (commission_id,)).fetchone()
        document = json.loads(row[0])
        document['frozen'].update(changes)
        document['frozen_sha256'] = digest(document['frozen'])
        db.execute('UPDATE animation_method_commissions SET document=? WHERE id=?',
                   (json.dumps(document), commission_id))


def test_linear_continuation_uses_recorded_best_and_retained_baseline_without_rewriting(work):
    service, creator, reviewer, renderer = work
    original = finish(service, start(service, title='Original effect')['id'])
    reviewer.choose_baseline = True
    continued = finish(service, start(service, title='A different approach',
        base_commission_id=original['id'])['id'])
    failed = start(service, base_commission_id=continued['id'])
    service.commissions.stop('developer', failed['id'], 'failed', 'Fixture stopped')
    before, calls = history(service), (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))

    catalogue = service.catalogue('developer')
    assert len(catalogue['effects']) == 1 and len(catalogue['commissions']) == 3
    effect = catalogue['effects'][0]
    assert effect['id'] == effect['effect_id'] == original['id']
    assert effect['title'] == original['title']
    assert effect['open_commission_id'] == continued['id']
    assert effect['best_candidate']['id'] == continued['best_candidate_id'] == original['best_candidate_id']
    assert '/commissions/' + continued['id'] + '/' in effect['media_url']
    assert [row['kind'] for row in effect['commissions']] == ['original', 'continuation', 'continuation']
    assert [row['state'] for row in effect['commissions']] == ['completed', 'completed', 'failed']
    assert effect['relationship_status'] == 'verified'
    detail = service.get('developer', failed['id'])
    assert detail['id'] == failed['id'] and detail['state'] == 'failed'
    assert detail['effect']['id'] == original['id']
    assert detail['effect']['open_commission_id'] == continued['id']
    assert detail['effect']['commissions'] == effect['commissions']
    assert service.artifact('developer', continued['id'], effect['best_candidate']['id'], 'media')
    assert history(service) == before
    assert calls == (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))


def test_queued_continuation_keeps_effect_active_without_replacing_completed_representative(work):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    queued = start(service, base_commission_id=original['id'])
    effect = effect_for(service, queued['id'])
    assert effect['open_commission_id'] == original['id'] and effect['state'] == 'queued'
    assert effect['best_candidate']['id'] == original['best_candidate_id']
    assert next(row for row in effect['commissions'] if row['id'] == queued['id'])['state'] == 'queued'
    assert service.catalogue('developer')['commissions'][0]['state'] == 'queued'


def test_transfer_joins_exact_demonstration_lineage_and_failure_cannot_replace_reference(work):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    continued = finish(service, start(service, base_commission_id=original['id'])['id'])
    transfer = service.transfer('developer', continued['id'], dict(request_id=str(uuid4()),
        goal='Explain two alternative paths', structural_changes={
            'relations': dict(before='one path', after='two paths')},
        budget=dict(max_rounds=1, max_seconds=300)))
    service.commissions.stop('developer', transfer['id'], 'failed', 'Fixture transfer failed')
    effect = effect_for(service, transfer['id'])
    assert effect['effect_id'] == original['id'] and effect['open_commission_id'] == continued['id']
    assert len(effect['commissions']) == 3
    assert effect['commissions'][-1]['role'] == effect['commissions'][-1]['kind'] == 'transfer'
    assert effect['commissions'][-1]['state'] == 'failed'
    assert service.get('developer', transfer['id'])['effect']['id'] == original['id']


def test_competing_continuation_branches_keep_original_representative_and_all_choices(work):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    first = finish(service, start(service, base_commission_id=original['id'], title='Branch A')['id'])
    second = finish(service, start(service, base_commission_id=original['id'], title='Branch B')['id'])
    effect = effect_for(service, first['id'])
    assert effect['open_commission_id'] == original['id']
    assert effect['best_candidate']['id'] == original['best_candidate_id']
    assert {row['id'] for row in effect['commissions']} == {original['id'], first['id'], second['id']}
    assert service.get('developer', second['id'])['effect']['commissions'] == effect['commissions']


def test_same_title_goal_and_shared_method_never_imply_effect_identity(work):
    service, _, _, _ = work
    original = finish(service, start(service, title='Same title')['id'])
    independent = start(service, title='Same title', method_versions=original['publication_method_version_ids'])
    catalogue = service.catalogue('developer')
    assert len(catalogue['effects']) == 2
    assert {row['effect_id'] for row in catalogue['effects']} == {original['id'], independent['id']}
    assert all(len(row['commissions']) == 1 for row in catalogue['effects'])


def test_owner_history_outside_default_window_still_resolves_the_original_effect(work):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    for index in range(101):
        service.commissions.create('developer', dict(goal='Unrelated ' + str(index),
            budget=dict(creator=1, review=0, render=0, seconds=300)), 'unrelated-' + str(index))
    continuation = start(service, base_commission_id=original['id'])
    assert original['id'] not in {row['id'] for row in service.commissions.list('developer')}
    all_rows = service.commissions.list_all('developer')
    assert len(all_rows) == 103 and all('operations' not in row for row in all_rows)
    catalogue = service.catalogue('developer')
    assert len(catalogue['commissions']) == 103 and len(catalogue['effects']) == 102
    effect = effect_for(service, continuation['id'])
    assert effect['effect_id'] == original['id'] and len(effect['commissions']) == 2
    assert service.get('developer', continuation['id'])['effect']['id'] == original['id']


@pytest.mark.parametrize('relation', ['missing', 'foreign', 'missing-demo', 'cycle'])
def test_unresolved_ancestry_is_independent_flagged_and_never_repaired(work, relation):
    service, _, _, _ = work
    first, second = start(service), start(service)
    if relation == 'foreign':
        foreign = service.commissions.create('another-developer', dict(goal='Private foreign title',
            budget=dict(creator=1, review=0, render=0, seconds=300)), 'private-request')
        alter_frozen_fixture(service, first['id'], base_commission_id=foreign['id'])
    elif relation == 'missing-demo':
        alter_frozen_fixture(service, first['id'], role='transfer',
                             baseline_demonstration_id='unavailable-demonstration')
    else:
        alter_frozen_fixture(service, first['id'], base_commission_id=second['id'] if relation == 'cycle' else 'missing-run')
    alter_frozen_fixture(service, second['id'], base_commission_id=first['id'])
    before = history(service)
    catalogue = service.catalogue('developer')
    assert len(catalogue['commissions']) == len(catalogue['effects']) == 2
    for effect in catalogue['effects']:
        assert len(effect['commissions']) == 1 and effect['effect_id'] == effect['commissions'][0]['id']
        assert effect['relationship_status'] == 'needs_review' and effect['relationship_issues']
    assert 'Private foreign title' not in json.dumps(catalogue)
    assert service.get('developer', first['id'])['effect']['relationship_status'] == 'needs_review'
    assert history(service) == before


def test_long_frozen_lineage_uses_no_recursive_depth_limit():
    # Minimal stored-record fields exercise a lineage beyond Python's recursion
    # depth; no fake media or acceptance is introduced by this projection.
    rows = []
    for index in range(1100):
        identity = 'run-' + str(index)
        frozen = dict(goal='Explain an effect')
        if index:
            frozen['base_commission_id'] = 'run-' + str(index - 1)
        rows.append(dict(id=identity, title=identity, frozen=frozen, state='queued', request_id=identity,
                         best_candidate_id=None, candidates=[], stop_reason=None, created_at=index))
    original = deepcopy(rows)
    effects, membership = _effect_views(list(reversed(rows)), [])
    assert len(effects) == 1 and effects[0]['effect_id'] == 'run-0'
    assert len(effects[0]['commissions']) == len(membership) == 1100
    assert rows == original
