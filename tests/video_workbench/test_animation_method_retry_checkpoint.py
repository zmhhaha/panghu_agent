"""Retry persistence preserves genuine completed work, not quality approval."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import threading

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_method_commissions import AnimationMethodCommissionStore


OWNER = 'developer'
SOURCE = dict(engine='canvas', files={'scene.js': '/* Agent source fixture */'})
SOUNDS = [dict(time=0.5, frequency=220)]
MEDIA = dict(sha256='a' * 64, duration_seconds=3, path='/immutable/old.mp4')


def body(**changes):
    value = dict(goal='Follow the same agreed creation plan',
        criteria=[dict(id='cause', description='A visible cause precedes the response')],
        budget=dict(creator=6, render=2, review=2, seconds=300))
    value.update(changes)
    return value


@pytest.fixture
def store(tmp_path):
    return AnimationMethodCommissionStore(tmp_path / 'commissions.sqlite3', clock=lambda: 1000)


def complete(store, run_id, stage, key, document, result):
    operation = store.reserve(OWNER, run_id, stage, document, key)
    if stage == 'creator':
        store.record_input_manifest(OWNER, operation['id'], [
            dict(path='animation-method-input.json', sha256=digest(document))])
    store.claim(OWNER, operation['id'])
    store.mark_submitted(OWNER, operation['id'], 'real-job-' + operation['id'])
    return store.complete(OWNER, operation['id'], result)


def creators(store, run_id):
    plan_input = dict(schema='video.animation-method-input.v2', mode='plan',
        goal='The original direction', constraints={'wikiskill': 'frozen-v1'})
    plan = complete(store, run_id, 'creator', 'plan', plan_input,
        dict(mode='plan', criteria=[dict(id='cause', description='Preserve this criterion')]))
    create_input = dict(schema='video.animation-method-input.v2', mode='create',
        plan=plan['result'], constraints={'wikiskill': 'frozen-v1'})
    create = complete(store, run_id, 'creator', 'create-1', create_input,
        dict(mode='create', source=deepcopy(SOURCE), sound_events=deepcopy(SOUNDS)))
    return plan, create


def failed_run(store, *, render=False, failed_creator=False):
    run = store.create(OWNER, body(), 'original')
    plan, create = creators(store, run['id'])
    rendered = None
    if render:
        rendered = complete(store, run['id'], 'render', 'render-' + create['id'],
            dict(author_operation_id=create['id'], source=SOURCE, document=create['input'], sound_events=SOUNDS),
            dict(media=deepcopy(MEDIA), technical={'status': 'passed'},
                runtime={'original_render_operation_id': 'frozen-real-runtime'}))
    if failed_creator:
        failed = store.reserve(OWNER, run['id'], 'creator', {'mode': 'create', 'attempt': 2}, 'create-2')
        store.claim(OWNER, failed['id'])
        store.fail(OWNER, failed['id'], 'fixture_source_failure')
    review = store.reserve(OWNER, run['id'], 'review', {'fixture': 'not a real visual observation'}, 'review-1')
    store.claim(OWNER, review['id'])
    store.fail(OWNER, review['id'], 'fixture_review_failure')
    run = store.stop(OWNER, run['id'], 'failed', 'Stopped after the failed fixture operation')
    return run, plan, create, rendered


def retry(store, original, request='retry-1'):
    return store.create(OWNER, body(retry_of_commission_id=original['id']), request)


def original_rows(store, run_id):
    with sqlite3.connect(store.path) as db:
        return (db.execute('SELECT * FROM animation_method_commissions WHERE id=?', (run_id,)).fetchall(),
            db.execute('SELECT * FROM animation_method_commission_operations WHERE commission_id=? ORDER BY rowid',
                (run_id,)).fetchall(),
            db.execute('SELECT * FROM animation_method_commission_events WHERE commission_id=? ORDER BY rowid',
                (run_id,)).fetchall())


def reserved_render(store, run, source_author):
    author = next(op for op in run['operations']
        if op.get('reused_from', {}).get('operation_id') == source_author['id'])
    return store.reserve(OWNER, run['id'], 'render', dict(author_operation_id=author['id'],
        source=author['result']['source'], document=author['input'], sound_events=author['result']['sound_events']),
        'render-' + author['id'])


def test_retry_restores_completed_plan_and_creation_without_new_cost_or_old_reviews(store):
    old, plan, authored, _ = failed_run(store, failed_creator=True)
    before = original_rows(store, old['id'])
    new = retry(store, old)
    restored = store.restore_completed_creators(OWNER, new['id'], old['id'])
    assert len(restored['operations']) == 2
    assert restored['usage'] == dict(creator=0, render=0, review=0)
    assert old['usage']['creator'] == 3
    assert restored['candidates'] == restored['reviews'] == []
    assert restored['best_candidate_id'] is None
    for original, copied in zip((plan, authored), restored['operations']):
        assert copied['id'] != original['id']
        assert copied['commission_id'] == new['id']
        assert copied['reused_from'] == dict(commission_id=old['id'], operation_id=original['id'])
        for field in ('input', 'result', 'input_sha256', 'result_sha256', 'request_sha256',
                      'idempotency_key', 'job_id', 'input_resource_manifest', 'input_resource_manifest_sha256'):
            assert canonical_bytes(copied[field]) == canonical_bytes(original[field])
        assert store.reserve(OWNER, new['id'], 'creator', copied['input'], copied['idempotency_key']) == copied
        assert store.claim(OWNER, copied['id'])['submission_claimed'] is False
    assert original_rows(store, old['id']) == before


def test_restart_and_simultaneous_restore_are_idempotent(store):
    old, _, _, _ = failed_run(store)
    new = retry(store, old)
    barrier = threading.Barrier(3)

    def restore(_):
        reopened = AnimationMethodCommissionStore(store.path, clock=lambda: 1000)
        barrier.wait(timeout=10)
        return reopened.restore_completed_creators(OWNER, new['id'], old['id'])

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(restore, range(3)))
    assert all(result == results[0] for result in results)
    assert len(results[0]['operations']) == 2
    events = store.events(OWNER, new['id'])
    assert len([row for row in events if row['event'] == 'creator_checkpoints_restored']) == 1
    assert len([row for row in events if row['event'] == 'operation_reused']) == 2


def test_completed_plan_components_and_integration_keep_their_original_keys(store):
    old = store.create(OWNER, body(), 'original')
    originals = []
    for phase, key, component_id in (('plan', 'collaboration-plan', None),
        ('component', 'collaboration-component-brush', 'brush'),
        ('component', 'collaboration-component-mist', 'mist'),
        ('integrate', 'collaboration-integrate', None)):
        op = store.reserve(OWNER, old['id'], 'creator',
            dict(schema='video.animation-collaboration-input.v1', phase=phase,
                component_id=component_id, original_plan={'unchanged': True}), key)
        store.record_input_manifest(OWNER, op['id'], [dict(path='collaboration-input.json', sha256=op['input_sha256'])])
        store.claim(OWNER, op['id'])
        store.mark_submitted(OWNER, op['id'], 'real-job-' + key)
        originals.append(store.complete(OWNER, op['id'], {'phase': phase, 'unchanged': key}))
    old = store.stop(OWNER, old['id'], 'failed', 'Render did not complete')
    new = retry(store, old)
    restored = store.restore_completed_creators(OWNER, new['id'], old['id'])
    assert [op['idempotency_key'] for op in restored['operations']] == [op['idempotency_key'] for op in originals]
    assert restored['usage']['creator'] == 0


def test_empty_checkpoint_is_recorded_and_does_not_restore_failed_work(store):
    old = store.create(OWNER, body(), 'original')
    failed = store.reserve(OWNER, old['id'], 'creator', {'mode': 'plan'}, 'plan')
    store.claim(OWNER, failed['id'])
    store.fail(OWNER, failed['id'], 'plan_failed')
    old = store.stop(OWNER, old['id'], 'failed', 'No completed creative work')
    new = retry(store, old)
    restored = store.restore_completed_creators(OWNER, new['id'], old['id'])
    assert restored['operations'] == []
    assert restored['retry_creator_checkpoint']['operations'] == []
    assert store.restore_completed_creators(OWNER, new['id'], old['id']) == restored


def test_wrong_ancestor_owner_or_nonpristine_retry_are_rejected(store):
    old, _, _, _ = failed_run(store)
    new = retry(store, old)
    unrelated = store.create(OWNER, body(), 'unrelated')
    unrelated = store.stop(OWNER, unrelated['id'], 'failed', 'Unrelated')
    with pytest.raises(ServiceError, match='animation_method_retry_checkpoint_relationship_invalid'):
        store.restore_completed_creators(OWNER, new['id'], unrelated['id'])
    with pytest.raises(ServiceError, match='animation_method_commission_not_found'):
        store.restore_completed_creators('other-owner', new['id'], old['id'])
    store.reserve(OWNER, new['id'], 'creator', {'already': 'started'}, 'new-work')
    with pytest.raises(ServiceError, match='animation_method_retry_checkpoint_not_pristine'):
        store.restore_completed_creators(OWNER, new['id'], old['id'])
    assert len(store.get(OWNER, new['id'])['operations']) == 1


def test_all_checkpoint_copies_and_receipts_roll_back_together(store):
    old, _, _, _ = failed_run(store)
    new = retry(store, old)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_second_copy BEFORE INSERT ON animation_method_commission_operations "
            "WHEN NEW.commission_id='" + new['id'] + "' AND NEW.idem='create-1' "
            "BEGIN SELECT RAISE(ABORT, 'fixture interruption'); END")
    with pytest.raises(sqlite3.DatabaseError):
        store.restore_completed_creators(OWNER, new['id'], old['id'])
    unchanged = store.get(OWNER, new['id'])
    assert unchanged['operations'] == []
    assert 'retry_creator_checkpoint' not in unchanged
    assert [event['event'] for event in store.events(OWNER, new['id'])] == ['commission_created']
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER fail_second_copy')
    assert len(store.restore_completed_creators(OWNER, new['id'], old['id'])['operations']) == 2


def test_changed_original_completed_result_cannot_be_restored(store):
    old, _, authored, _ = failed_run(store)
    new = retry(store, old)
    with sqlite3.connect(store.path) as db:
        document = json.loads(db.execute('SELECT document FROM animation_method_commission_operations WHERE id=?',
            (authored['id'],)).fetchone()[0])
        document['result']['source']['files']['scene.js'] = 'changed after completion'
        db.execute('UPDATE animation_method_commission_operations SET document=? WHERE id=?',
            (json.dumps(document), authored['id']))
    with pytest.raises(ServiceError, match='animation_method_operation_result_changed'):
        store.restore_completed_creators(OWNER, new['id'], old['id'])
    assert store.get(OWNER, new['id'])['operations'] == []


def test_reuse_identical_successful_render_preserves_real_media_and_does_not_spend_budget(store):
    old, _, authored, rendered = failed_run(store, render=True)
    before = original_rows(store, old['id'])
    new = retry(store, old)
    restored = store.restore_completed_creators(OWNER, new['id'], old['id'])
    reserved = reserved_render(store, restored, authored)
    assert store.get(OWNER, new['id'])['usage']['render'] == 1
    reused = store.reuse_render(OWNER, reserved['id'], rendered['id'])
    assert reused['input'] == reserved['input']
    assert reused['result'] == rendered['result']
    assert reused['result_sha256'] == rendered['result_sha256']
    assert reused['job_id'] == rendered['job_id']
    assert reused['artifact_operation_id'] == rendered['id']
    assert reused['reused_from'] == dict(commission_id=old['id'], operation_id=rendered['id'])
    assert store.get(OWNER, new['id'])['usage'] == dict(creator=0, render=0, review=0)
    assert store.reuse_render(OWNER, reserved['id'], rendered['id']) == reused
    assert store.claim(OWNER, reserved['id'])['submission_claimed'] is False
    assert store.get(OWNER, new['id'])['reviews'] == []
    assert original_rows(store, old['id']) == before


def test_repeated_retry_follows_original_artifact_identity(store):
    old, _, authored, rendered = failed_run(store, render=True)
    first = retry(store, old)
    first = store.restore_completed_creators(OWNER, first['id'], old['id'])
    first_render = reserved_render(store, first, authored)
    first_render = store.reuse_render(OWNER, first_render['id'], rendered['id'])
    first = store.stop(OWNER, first['id'], 'failed', 'A new review still failed')
    first_author = next(op for op in first['operations']
        if op.get('reused_from', {}).get('operation_id') == authored['id'])
    second = retry(store, first, request='retry-2')
    second = store.restore_completed_creators(OWNER, second['id'], first['id'])
    second_render = reserved_render(store, second, first_author)
    second_render = store.reuse_render(OWNER, second_render['id'], first_render['id'])
    assert second_render['artifact_operation_id'] == rendered['id']
    assert second_render['reused_from']['operation_id'] == first_render['id']
    assert second_render['result'] == rendered['result']
    assert store.get(OWNER, second['id'])['usage']['render'] == 0


@pytest.mark.parametrize('field', ['source', 'document', 'sound_events', 'extra'])
def test_changed_render_input_cannot_reuse_old_media(store, field):
    old, _, authored, rendered = failed_run(store, render=True)
    new = retry(store, old)
    restored = store.restore_completed_creators(OWNER, new['id'], old['id'])
    author = next(op for op in restored['operations'] if op['input'].get('mode') == 'create')
    document = dict(author_operation_id=author['id'], source=SOURCE, document=author['input'], sound_events=SOUNDS)
    document[field] = {'changed': True}
    reserved = store.reserve(OWNER, new['id'], 'render', document, 'different-render')
    with pytest.raises(ServiceError, match='animation_method_retry_render_input_changed'):
        store.reuse_render(OWNER, reserved['id'], rendered['id'])
    assert store.operation(OWNER, reserved['id'])['status'] == 'reserved'


def test_render_that_has_already_been_claimed_cannot_be_relabeled_as_reused(store):
    old, _, authored, rendered = failed_run(store, render=True)
    new = retry(store, old)
    new = store.restore_completed_creators(OWNER, new['id'], old['id'])
    reserved = reserved_render(store, new, authored)
    store.claim(OWNER, reserved['id'])
    with pytest.raises(ServiceError, match='animation_method_retry_render_not_reserved'):
        store.reuse_render(OWNER, reserved['id'], rendered['id'])
    assert store.operation(OWNER, reserved['id'])['status'] == 'submitting'


def test_technical_failure_cannot_be_promoted_to_reused_media(store):
    old, _, authored, rendered = failed_run(store, render=True)
    new = retry(store, old)
    new = store.restore_completed_creators(OWNER, new['id'], old['id'])
    reserved = reserved_render(store, new, authored)
    with sqlite3.connect(store.path) as db:
        doc = deepcopy(rendered)
        doc['result']['technical']['status'] = 'failed'
        doc['result_sha256'] = digest(doc['result'])
        db.execute('UPDATE animation_method_commission_operations SET document=? WHERE id=?',
            (json.dumps(doc), rendered['id']))
    with pytest.raises(ServiceError, match='animation_method_retry_render_not_usable'):
        store.reuse_render(OWNER, reserved['id'], rendered['id'])
    assert 'reused_from' not in store.operation(OWNER, reserved['id'])


def test_same_output_from_a_new_author_is_not_an_original_checkpoint(store):
    old, _, authored, rendered = failed_run(store, render=True)
    new = retry(store, old)
    new = store.restore_completed_creators(OWNER, new['id'], old['id'])
    new_author = complete(store, new['id'], 'creator', 'extra-creator', authored['input'], authored['result'])
    reserved = store.reserve(OWNER, new['id'], 'render', dict(author_operation_id=new_author['id'],
        source=SOURCE, document=new_author['input'], sound_events=SOUNDS), 'new-render')
    with pytest.raises(ServiceError, match='animation_method_retry_render_author_changed'):
        store.reuse_render(OWNER, reserved['id'], rendered['id'])


def test_render_reuse_is_owner_scoped_and_requires_direct_retry_ancestor(store):
    old, _, authored, rendered = failed_run(store, render=True)
    new = retry(store, old)
    new = store.restore_completed_creators(OWNER, new['id'], old['id'])
    reserved = reserved_render(store, new, authored)
    with pytest.raises(ServiceError, match='animation_method_operation_not_found'):
        store.reuse_render('another-owner', reserved['id'], rendered['id'])
    other = store.create(OWNER, body(), 'unrelated')
    other_render = complete(store, other['id'], 'render', 'other-render',
        rendered['input'], rendered['result'])
    other = store.stop(OWNER, other['id'], 'failed', 'An unrelated failed run')
    with pytest.raises(ServiceError, match='animation_method_retry_render_relationship_invalid'):
        store.reuse_render(OWNER, reserved['id'], other_render['id'])
