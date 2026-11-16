"""Pure stored-record projections; no database, Agent call or media execution."""
from copy import deepcopy

import pytest

from agent_service.contracts import digest
from video_workbench.animation_method_service import _effect_views


OWNER = 'developer'
SCHEMA = 'video.animation-effect-separation.v1'


def selected_candidate(run):
    candidates = [*run['candidates'], run['frozen'].get('base_candidate')]
    return next((row for row in candidates if row and row['id'] == run['best_candidate_id']), None)


def stored_run(identity, *, parent=None, retry_of=None, state='completed', own_sample=True,
               goal=None, engine='canvas'):
    """Inert record shapes, never evidence that any animation was rendered."""
    frozen = dict(role='reference', goal=goal or 'Frozen goal for ' + identity, method_versions=[])
    if retry_of:
        frozen = deepcopy(retry_of['frozen'])
        frozen['retry_of_commission_id'] = retry_of['id']
    elif parent:
        frozen['base_commission_id'] = parent['id']
        baseline = selected_candidate(parent)
        if baseline:
            frozen.update(base_candidate_id=baseline['id'], base_candidate=deepcopy(baseline))
    candidate = dict(id=identity + '-candidate', commission_id=identity, operation_id=identity + '-render',
        source=dict(engine=engine, files={'scene.js': '// Inert projection fixture'}),
        media=dict(sha256=digest(identity)), technical=dict(status='passed'),
        provenance=dict(author_operation_id=identity + '-author'))
    baseline_id = frozen.get('base_candidate_id')
    best_id = candidate['id'] if own_sample and (state == 'completed' or not baseline_id) else baseline_id
    return dict(id=identity, owner=OWNER, title='Recorded title for ' + identity, frozen=frozen,
        frozen_sha256=digest(frozen), state=state, request_id=identity + '-request',
        best_candidate_id=best_id, candidates=[candidate] if own_sample else [],
        stop_reason='budget_exhausted' if state == 'completed' else state, created_at=1)


def demonstrations(runs):
    return [dict(id=run['id'] + '-demo', source=deepcopy(candidate['source']),
        media=deepcopy(candidate['media']), technical=dict(status='passed'),
        runtime=dict(engine=candidate['source']['engine'], admission=dict(status='admitted')),
        provenance=dict(commission_id=run['id'], candidate_id=candidate['id'],
                        render_operation_id=candidate['operation_id']))
        for run in runs for candidate in run['candidates']]


def separation(anchor, source_effect, **changes):
    record = dict(schema=SCHEMA, owner=anchor['owner'], commission_id=anchor['id'],
        commission_frozen_sha256=anchor['frozen_sha256'], source_effect_id=source_effect,
        source_parent_id=anchor['frozen'].get('base_commission_id'), title='Independent ' + anchor['id'],
        reason='The subject or principal creative technique is a different sample.',
        request_id='separate-' + anchor['id'], created_at=100)
    return dict(record, **changes)


def family():
    original = stored_run('original')
    bridge = stored_run('failed-continuation', parent=original, state='failed', own_sample=False)
    retry = stored_run('failed-retry', retry_of=bridge, state='failed')
    anchor = stored_run('rebuild', parent=retry, goal='An independent reed pond and egret')
    child = stored_run('rebuild-child', parent=anchor, goal='A later refinement of the new sample')
    return original, bridge, retry, anchor, child


@pytest.mark.parametrize('reverse_runs', [False, True])
def test_separation_creates_independent_effect_with_descendants_and_preserves_frozen_history(reverse_runs):
    original, bridge, retry, anchor, child = family()
    runs = [original, bridge, retry, anchor, child]
    if reverse_runs:
        runs.reverse()
    demos = demonstrations(runs)
    records = [separation(anchor, original['id'], title='芦塘起翼')]
    before = deepcopy((runs, demos, records))

    effects, membership = _effect_views(runs, demos, (), (), records)

    assert {effect['id'] for effect in effects} == {original['id'], anchor['id']}
    old, independent = membership[original['id']], membership[anchor['id']]
    assert old is membership[bridge['id']] is membership[retry['id']]
    assert independent is membership[child['id']]
    assert {row['id'] for row in old['commissions']} == {original['id'], bridge['id'], retry['id']}
    assert {row['id'] for row in independent['commissions']} == {anchor['id'], child['id']}
    assert old['open_commission_id'] == original['id']
    assert old['best_candidate']['id'] == original['best_candidate_id']
    assert independent['effect_id'] == anchor['id'] and independent['title'] == '芦塘起翼'
    assert independent['goal'] == anchor['frozen']['goal'] != child['frozen']['goal']
    assert independent['open_commission_id'] == child['id']
    assert independent['best_candidate']['id'] == child['best_candidate_id']
    anchor_view = next(row for row in independent['commissions'] if row['id'] == anchor['id'])
    assert anchor_view['base_commission_id'] == retry['id']
    assert old['relationship_status'] == independent['relationship_status'] == 'verified'
    assert (runs, demos, records) == before


@pytest.mark.parametrize('change', [
    {'owner': 'another-owner'},
    {'commission_frozen_sha256': '0' * 64},
    {'source_effect_id': 'another-effect'},
    {'source_parent_id': 'another-parent'},
    {'schema': 'video.animation-effect-separation.v0'},
])
def test_invalid_separation_is_flagged_without_changing_the_existing_tree(change):
    runs = list(family())
    original, _, _, anchor, child = runs
    demos = demonstrations(runs)
    records = [separation(anchor, original['id'], **change)]
    before = deepcopy((runs, demos, records))

    effects, membership = _effect_views(runs, demos, (), (), records)

    assert len(effects) == 1 and effects[0]['id'] == original['id']
    assert membership[anchor['id']] is membership[original['id']] is membership[child['id']]
    assert {row['id'] for row in effects[0]['commissions']} == {row['id'] for row in runs}
    assert effects[0]['relationship_status'] == 'needs_review'
    assert effects[0]['relationship_issues']
    assert (runs, demos, records) == before


@pytest.mark.parametrize('ineligible', ['original', 'retry', 'failed', 'retained-baseline', 'missing-own-best'])
def test_separation_cannot_promote_an_ineligible_anchor(ineligible):
    original, bridge, retry, anchor, child = family()
    if ineligible == 'original':
        target = original
    elif ineligible == 'retry':
        retry['state'], retry['best_candidate_id'] = 'completed', retry['candidates'][0]['id']
        target = retry
    else:
        target = anchor
        if ineligible == 'failed':
            anchor['state'] = 'failed'
        elif ineligible == 'retained-baseline':
            anchor['best_candidate_id'] = anchor['frozen']['base_candidate_id']
        else:
            anchor['best_candidate_id'] = 'unavailable-candidate'
    runs = [original, bridge, retry, anchor, child]
    demos = demonstrations(runs)
    records = [separation(target, original['id'])]
    before = deepcopy((runs, demos, records))

    effects, membership = _effect_views(runs, demos, (), (), records)

    assert len(effects) == 1 and effects[0]['id'] == original['id']
    assert membership[anchor['id']] is membership[original['id']]
    assert effects[0]['relationship_status'] == 'needs_review'
    assert effects[0]['relationship_issues']
    assert (runs, demos, records) == before


def test_deleted_empty_failure_cannot_become_a_separation_anchor():
    original = stored_run('original')
    hidden = stored_run('hidden-failure', parent=original, state='failed', own_sample=False)
    # Legacy empty failure retains a lineage edge without a retained baseline.
    hidden['frozen'].pop('base_candidate')
    hidden['frozen'].pop('base_candidate_id')
    hidden['best_candidate_id'] = None
    hidden['frozen_sha256'] = digest(hidden['frozen'])
    child = stored_run('surviving-child', parent=hidden)
    runs = [original, hidden, child]
    demos = demonstrations(runs)
    deletions = [dict(schema='video.animation-commission-deletion.v1', owner=OWNER,
        commission_id=hidden['id'], commission_frozen_sha256=hidden['frozen_sha256'],
        source='explicit_owner_failed_attempt_deletion', reason='Remove this empty failed attempt.', created_at=10)]
    records = [separation(hidden, original['id'])]
    before = deepcopy((runs, demos, deletions, records))

    effects, membership = _effect_views(runs, demos, (), deletions, records)

    assert len(effects) == 1 and effects[0]['id'] == original['id']
    assert hidden['id'] not in membership
    assert set(membership) == {original['id'], child['id']}
    assert {row['id'] for row in effects[0]['commissions']} == set(membership)
    assert (runs, demos, deletions, records) == before


def transfer(identity, baseline_demo):
    run = stored_run(identity)
    run['frozen'] = dict(role='transfer', goal='Transfer fixture for ' + identity,
        baseline_demonstration_id=baseline_demo['id'], structural_changes={'fixture': 'different content'})
    run['frozen_sha256'] = digest(run['frozen'])
    return run


def test_transfers_follow_exact_demonstration_provenance_after_separation():
    original, bridge, retry, anchor, _ = family()
    runs = [original, bridge, retry, anchor]
    demos = demonstrations(runs)
    old_transfer = transfer('old-transfer', next(row for row in demos
        if row['provenance']['commission_id'] == original['id']))
    new_transfer = transfer('new-transfer', next(row for row in demos
        if row['provenance']['commission_id'] == anchor['id']))
    runs.extend([old_transfer, new_transfer])
    demos = demonstrations(runs)
    records = [separation(anchor, original['id'])]
    before = deepcopy((runs, demos, records))

    effects, membership = _effect_views(runs, demos, (), (), records)

    assert len(effects) == 2
    assert membership[old_transfer['id']] is membership[original['id']]
    assert membership[new_transfer['id']] is membership[anchor['id']]
    assert membership[original['id']]['open_commission_id'] == original['id']
    assert membership[anchor['id']]['open_commission_id'] == anchor['id']
    assert anchor['frozen']['base_commission_id'] == retry['id']
    assert anchor['frozen']['base_candidate']['provenance'] == selected_candidate(retry)['provenance']
    assert (runs, demos, records) == before


def test_transfer_annotation_is_flagged_without_reassigning_its_baseline_effect():
    original = stored_run('original')
    adapted = transfer('transfer', demonstrations([original])[0])
    runs = [original, adapted]
    demos = demonstrations(runs)
    records = [separation(adapted, original['id'])]
    before = deepcopy((runs, demos, records))

    effects, membership = _effect_views(runs, demos, (), (), records)

    assert len(effects) == 1 and effects[0]['id'] == original['id']
    assert membership[adapted['id']] is membership[original['id']]
    assert effects[0]['relationship_status'] == 'needs_review'
    assert effects[0]['relationship_issues']
    assert (runs, demos, records) == before


def test_nested_separations_use_current_effect_identity_and_keep_metadata_on_each_anchor():
    original, bridge, retry, anchor, child = family()
    grandchild = stored_run('grandchild', parent=child)
    runs = [original, bridge, retry, anchor, child, grandchild]
    demos = demonstrations(runs)
    records = [separation(anchor, original['id']), separation(child, anchor['id'], title='Another sample')]
    before = deepcopy((runs, demos, records))

    effects, membership = _effect_views(runs, demos, (), (), records)

    assert {effect['id'] for effect in effects} == {original['id'], anchor['id'], child['id']}
    assert membership[anchor['id']]['title'] == records[0]['title']
    assert membership[child['id']]['title'] == 'Another sample'
    assert membership[child['id']] is membership[grandchild['id']]
    assert membership[child['id']]['goal'] == child['frozen']['goal']
    assert (runs, demos, records) == before


def test_later_separation_with_stale_source_effect_keeps_the_current_tree_and_is_flagged():
    original, bridge, retry, anchor, child = family()
    runs = [original, bridge, retry, anchor, child]
    demos = demonstrations(runs)
    records = [separation(anchor, original['id']), separation(child, original['id'])]
    before = deepcopy((runs, demos, records))

    effects, membership = _effect_views(runs, demos, (), (), records)

    assert {effect['id'] for effect in effects} == {original['id'], anchor['id']}
    assert membership[child['id']] is membership[anchor['id']]
    assert membership[anchor['id']]['title'] == records[0]['title']
    assert membership[anchor['id']]['relationship_status'] == 'needs_review'
    assert membership[anchor['id']]['relationship_issues']
    assert membership[original['id']]['relationship_status'] == 'verified'
    assert (runs, demos, records) == before
