"""Durable execution budgets, without any model or renderer calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import threading

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_revision_store import AnimationRevisionStore


SOURCE = 'a' * 64
INPUT = 'b' * 64
MEDIA = [dict(version_id='draft-1', sha256='c' * 64, role='original-render')]
ISSUES = [dict(id='issue-1', start_seconds=1.5, end_seconds=2.0,
               observation='连接线提前可见', target='在对应动作前保持隐藏')]


@pytest.fixture
def state(tmp_path):
    store = AnimationRevisionStore(tmp_path / 'animation-revision.sqlite3')
    lineage = store.create_lineage('alice', 'project-1', 'source-1', SOURCE, 'lineage-1')
    return store, lineage['id']


def reserve(store, lineage, stage='review', key=None, **kwargs):
    key = key or stage
    return store.reserve(lineage, stage, f'operation-{key}', f'idem-{key}', INPUT,
                         media_versions=deepcopy(MEDIA), **kwargs)


def complete(store, operation, *, issues=None, result=None, media=None):
    claimed = store.mark_submitting(operation['id'])
    assert claimed['submission_claimed'] is True
    store.mark_submitted(operation['id'], 'job-' + operation['id'])
    return store.finish(operation['id'], result={} if result is None else result,
                        issues=[] if issues is None else issues,
                        media_versions=MEDIA if media is None else media)


def error(code):
    return pytest.raises(ServiceError, match='^' + code + '$')


def test_lineage_identity_and_creation_keys_are_immutable_and_survive_restart(state):
    store, lineage_id = state
    original = store.lineage(lineage_id)
    assert original['round_kind'] == 'automatic'
    assert original['creator_revision_limit'] == 1
    restarted = AnimationRevisionStore(store.path)
    assert restarted.create_lineage('alice', 'project-1', 'source-1', SOURCE, 'lineage-1') == original
    assert restarted.create_lineage('alice', 'project-1', 'source-1', SOURCE, 'alias-key') == original
    with error('animation_revision_idempotency_conflict'):
        restarted.create_lineage('alice', 'different-project', 'source-1', SOURCE, 'alias-key')
    with error('animation_revision_source_conflict'):
        restarted.create_lineage('alice', 'project-1', 'copied-source', SOURCE, 'new-key')
    assert len(restarted.events(lineage_id)) == 1


def test_reservation_input_identity_and_media_are_frozen(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    same = reserve(store, lineage_id)
    assert same == operation
    operation['media_versions'][0]['sha256'] = 'd' * 64
    assert store.operation(same['id'])['media_versions'] == MEDIA
    with error('animation_revision_idempotency_conflict'):
        store.reserve(lineage_id, 'review', same['id'], same['idempotency_key'], 'e' * 64,
                      media_versions=MEDIA)
    with error('animation_revision_idempotency_conflict'):
        store.reserve(lineage_id, 'review', 'new-id', same['idempotency_key'], INPUT,
                      media_versions=MEDIA)
    with error('animation_revision_idempotency_conflict'):
        store.reserve(lineage_id, 'review', same['id'], 'new-key', INPUT,
                      media_versions=MEDIA)
    with error('animation_revision_allowance_consumed'):
        reserve(store, lineage_id, key='review-again')


@pytest.mark.parametrize('profile', ['video.animation-review.v1', 'video.animation-review.v2'])
def test_review_execution_identity_is_added_before_atomic_reservation(state, profile):
    store, lineage_id = state
    original = deepcopy(MEDIA)
    media = store.review_media_versions('operation-review', original, profile)
    assert media == [dict(MEDIA[0], review_profile=profile, review_profile_revision='1')]
    assert original == MEDIA and store.operations(lineage_id) == []
    operation = store.reserve(lineage_id, 'review', 'operation-review', 'idem-review', INPUT, media)
    before = store.events(lineage_id)
    assert store.review_media_versions(operation['id'], original, profile) == media
    changed = 'video.animation-review.v2' if profile.endswith('v1') else 'video.animation-review.v1'
    with error('animation_revision_idempotency_conflict'):
        store.review_media_versions(operation['id'], original, changed)
    assert store.operation(operation['id']) == operation
    assert store.events(lineage_id) == before


@pytest.mark.parametrize('status', ['reserved', 'submitting', 'submission_unknown', 'submitted',
                                   'running', 'completed', 'failed', 'cancelled', 'released'])
def test_legacy_review_identity_only_replays_v1_without_rewriting_metadata(state, status):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    if status in {'submitting', 'submission_unknown', 'submitted', 'running', 'completed'}:
        store.mark_submitting(operation['id'])
    if status == 'submission_unknown':
        store.mark_submission_unknown(operation['id'], 'synthetic lost response')
    elif status in {'submitted', 'running', 'completed'}:
        store.mark_submitted(operation['id'], 'original-job')
        if status == 'running':
            store.mark_running(operation['id'])
        elif status == 'completed':
            store.finish(operation['id'], issues=[])
    elif status in {'failed', 'cancelled'}:
        store.finish(operation['id'], status=status, issues=[])
    elif status == 'released':
        store.release_unsubmitted(operation['id'], dict(reason='unsubmitted fixture'))
    with sqlite3.connect(store.path) as db:
        original_bytes = db.execute('SELECT document FROM animation_revision_operations WHERE id=?',
                                    (operation['id'],)).fetchone()[0]
    before = store.events(lineage_id)
    assert store.review_media_versions(operation['id'], MEDIA, 'video.animation-review.v1') == MEDIA
    with error('animation_revision_idempotency_conflict'):
        store.review_media_versions(operation['id'], MEDIA, 'video.animation-review.v2')
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT document FROM animation_revision_operations WHERE id=?',
                           (operation['id'],)).fetchone()[0] == original_bytes
    assert store.events(lineage_id) == before


@pytest.mark.parametrize('change', ['different_media', 'different_role', 'partial_identity', 'mixed_identity'])
def test_review_identity_helper_rejects_changed_or_partial_frozen_metadata(state, change):
    store, lineage_id = state
    media = deepcopy(MEDIA)
    if change == 'partial_identity':
        media[0]['review_profile'] = 'video.animation-review.v1'
    elif change == 'mixed_identity':
        media.append(dict(media[0], review_profile='video.animation-review.v1', review_profile_revision='1'))
    operation = store.reserve(lineage_id, 'review', 'operation-review', 'idem-review', INPUT, media)
    candidate = deepcopy(MEDIA)
    if change == 'different_media':
        candidate[0]['sha256'] = 'd' * 64
    elif change == 'different_role':
        candidate[0]['role'] = 'different-role'
    with error('animation_revision_idempotency_conflict'):
        store.review_media_versions(operation['id'], candidate, 'video.animation-review.v1')


@pytest.mark.parametrize('profile,revision', [('unknown', '1'), ('video.animation-review.v1', '2'),
    ('video.animation-review.v2', 1), ('video.animation-review.v1', None)])
def test_review_identity_helper_rejects_unsupported_profile_revisions(state, profile, revision):
    store, _ = state
    with error('animation_revision_review_profile_invalid'):
        store.review_media_versions('operation-review', MEDIA, profile, revision)


def test_legacy_lineage_labels_new_stages_but_does_not_relabel_existing_stages(state):
    store, lineage_id = state
    review = complete(store, reserve(store, lineage_id), issues=ISSUES)
    tagged = store.review_media_versions(review['id'], MEDIA, 'video.animation-review.v1',
                                        operation_id='operation-revision')
    assert tagged == [dict(MEDIA[0], review_profile='video.animation-review.v1', review_profile_revision='1')]
    assert store.operation(review['id'])['media_versions'] == MEDIA
    # Reproduce an already existing legacy revision using its historical API.
    revision = reserve(store, lineage_id, 'revision')
    assert store.review_media_versions(review['id'], MEDIA, 'video.animation-review.v1',
                                      operation_id=revision['id']) == MEDIA
    with error('animation_revision_idempotency_conflict'):
        store.review_media_versions(review['id'], MEDIA, 'video.animation-review.v2',
                                    operation_id='operation-verification')


def test_reopened_legacy_three_stage_ledger_keeps_every_original_document_byte(state):
    store, lineage_id = state
    review = complete(store, reserve(store, lineage_id), issues=ISSUES)
    revision = complete(store, reserve(store, lineage_id, 'revision'))
    verification = complete(store, reserve(store, lineage_id, 'verification'))
    with sqlite3.connect(store.path) as db:
        frozen = db.execute('SELECT id,document FROM animation_revision_operations ORDER BY rowid').fetchall()
    events = store.events(lineage_id)
    store = AnimationRevisionStore(store.path)
    for operation in (review, revision, verification):
        assert store.review_media_versions(review['id'], MEDIA, 'video.animation-review.v1',
            operation_id=operation['id']) == MEDIA
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT id,document FROM animation_revision_operations ORDER BY rowid').fetchall() == frozen
    assert store.events(lineage_id) == events


def test_concurrent_independent_connections_reserve_only_one_stage_slot(state):
    store, lineage_id = state
    stores = [AnimationRevisionStore(store.path) for _ in range(8)]
    barrier = threading.Barrier(len(stores))

    def compete(index):
        barrier.wait(timeout=10)
        try:
            return reserve(stores[index], lineage_id, key=f'concurrent-{index}')
        except ServiceError as exc:
            assert exc.code == 'animation_revision_allowance_consumed'
            return None

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        outcomes = list(pool.map(compete, range(len(stores))))
    assert sum(item is not None for item in outcomes) == 1
    assert len(store.operations(lineage_id)) == 1


def test_idempotent_callers_claim_external_submission_only_once(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    stores = [AnimationRevisionStore(store.path) for _ in range(8)]
    barrier = threading.Barrier(len(stores))

    def claim(instance):
        barrier.wait(timeout=10)
        return instance.mark_submitting(operation['id'])['submission_claimed']

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        assert list(pool.map(claim, stores)).count(True) == 1
    restarted = AnimationRevisionStore(store.path)
    assert restarted.mark_submitting(operation['id'])['submission_claimed'] is False
    assert restarted.operation(operation['id'])['status'] == 'submitting'
    assert [event['event'] for event in restarted.events(lineage_id)].count('submission_claimed') == 1


def test_unknown_submission_reconciles_only_the_original_operation_after_restart(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    store.mark_submitting(operation['id'])
    store.mark_submission_unknown(operation['id'], 'HTTP timeout after submit')
    restarted = AnimationRevisionStore(store.path)
    assert reserve(restarted, lineage_id)['id'] == operation['id']
    assert restarted.mark_submitting(operation['id'])['submission_claimed'] is False
    with error('animation_revision_allowance_consumed'):
        reserve(restarted, lineage_id, key='replacement')
    with error('animation_revision_reconciliation_required'):
        restarted.finish(operation['id'], status='failed')
    with error('animation_revision_unsubmitted_proof_required'):
        restarted.release_unsubmitted(operation['id'],
                                      dict(reason='not found', confirmed_not_submitted=True))
    accepted = restarted.mark_submitted(operation['id'], 'original-remote-job')
    assert accepted['status'] == 'submitted'
    assert restarted.mark_submitted(operation['id'], 'original-remote-job') == accepted
    with error('animation_revision_job_conflict'):
        restarted.mark_submitted(operation['id'], 'replacement-job')
    finished = restarted.finish(operation['id'], issues=[], media_versions=MEDIA)
    assert finished['status'] == 'completed'
    assert finished['submission_unknown_reason'] == 'HTTP timeout after submit'


def test_crash_between_claim_and_submit_does_not_allow_unproven_release(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    store.mark_submitting(operation['id'])
    restarted = AnimationRevisionStore(store.path)
    with error('animation_revision_unsubmitted_proof_required'):
        restarted.release_unsubmitted(operation['id'], dict(reason='process restarted before response'))
    assert restarted.operation(operation['id'])['status'] == 'submitting'


@pytest.mark.parametrize('status,submitted', [('failed', False), ('cancelled', False),
                                            ('failed', True), ('cancelled', True)])
def test_failure_and_cancellation_never_refund_the_consumed_allowance(state, status, submitted):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    if submitted:
        store.mark_submitting(operation['id'])
        store.mark_submitted(operation['id'], 'remote-job')
    store.finish(operation['id'], status=status, result=dict(reason='stopped'))
    restarted = AnimationRevisionStore(store.path)
    with error('animation_revision_allowance_consumed'):
        reserve(restarted, lineage_id, key='retry-after-stop')
    with error('animation_revision_unsubmitted_proof_required'):
        restarted.release_unsubmitted(operation['id'], dict(reason='cancelled'))
    assert restarted.mark_submitting(operation['id'])['submission_claimed'] is False


def test_definitive_submission_failure_is_recorded_without_refunding(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    store.mark_submitting(operation['id'])
    store.finish(operation['id'], status='failed', result=dict(error='executor_rejected_input'))
    with error('animation_revision_allowance_consumed'):
        reserve(store, lineage_id, key='resubmit-rejected')


def test_only_never_submitted_reservation_can_be_released_with_retained_audit(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    evidence = dict(reason='input preflight failed before any external I/O', check_id='preflight-1')
    released = store.release_unsubmitted(operation['id'], evidence)
    assert store.release_unsubmitted(operation['id'], evidence) == released
    assert reserve(store, lineage_id)['status'] == 'released'
    assert store.mark_submitting(operation['id'])['submission_claimed'] is False
    replacement = reserve(store, lineage_id, key='new-reservation')
    assert replacement['id'] != operation['id']
    assert len(store.operations(lineage_id)) == 2
    audit = store.events(lineage_id)
    assert any(event['event'] == 'unsubmitted_released' and event['evidence'] == evidence for event in audit)
    assert [event['sequence'] for event in audit] == sorted(event['sequence'] for event in audit)


def test_revision_cannot_skip_review_or_use_failed_review(state):
    store, lineage_id = state
    with error('animation_revision_review_required'):
        reserve(store, lineage_id, 'revision')
    with error('animation_revision_review_required'):
        reserve(store, lineage_id, 'verification')
    operation = reserve(store, lineage_id)
    with error('animation_revision_stage_in_progress'):
        reserve(store, lineage_id, 'revision')
    store.finish(operation['id'], status='failed')
    with error('animation_revision_review_required'):
        reserve(store, lineage_id, 'revision')


def test_issue_free_review_goes_directly_to_reusable_technical_verification(state):
    store, lineage_id = state
    reviewed = complete(store, reserve(store, lineage_id), result=dict(summary='no visible issues'))
    with error('animation_revision_no_issues'):
        reserve(store, lineage_id, 'revision')
    verification = reserve(store, lineage_id, 'verification')
    assert verification['review_operation_id'] == reviewed['id']
    verified = complete(store, verification, result=dict(checks='passed'))
    assert reserve(store, lineage_id, 'verification') == verified
    assert store.mark_submitting(verified['id'])['submission_claimed'] is False
    assert len(store.operations(lineage_id)) == 2


def test_review_revision_verification_bind_issues_and_keep_both_media_versions(state):
    store, lineage_id = state
    review = complete(store, reserve(store, lineage_id), issues=ISSUES,
                      result=dict(summary='fix connector timing'))
    with error('animation_revision_creator_result_required'):
        reserve(store, lineage_id, 'verification')
    revision = reserve(store, lineage_id, 'revision')
    assert revision['review_operation_id'] == review['id']
    assert revision['review_result_sha256'] == digest(review['result'])
    assert revision['review_issues_sha256'] == digest(ISSUES)
    changed_media = [dict(version_id='revised-1', sha256='d' * 64)]
    revised = complete(store, revision, result=dict(source_version_id='revised-source'),
                       issues=[dict(id='remaining-1', observation='still needs user judgement')],
                       media=changed_media)
    verification = reserve(store, lineage_id, 'verification')
    assert verification['revision_operation_id'] == revised['id']
    assert verification['revision_result_sha256'] == digest(revised['result'])
    complete(store, verification, result=dict(render_valid=True), media=changed_media)
    restarted = AnimationRevisionStore(store.path)
    assert restarted.operation(review['id'])['output_media_versions'] == MEDIA
    assert restarted.operation(revised['id'])['output_media_versions'] == changed_media
    assert restarted.operation(revised['id'])['issues'][0]['id'] == 'remaining-1'
    for stage in ('review', 'revision', 'verification'):
        with error('animation_revision_allowance_consumed'):
            reserve(restarted, lineage_id, stage, key=stage + '-second')
    assert len(restarted.operations(lineage_id)) == 3


def test_completed_outcome_is_idempotent_and_cannot_be_overwritten(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    result = dict(issues=deepcopy(ISSUES), summary='known issue')
    completed = complete(store, operation, result=result, issues=ISSUES)
    assert store.finish(operation['id'], result=result, media_versions=MEDIA) == completed
    result['summary'] = 'pretend it passed'
    with error('animation_revision_outcome_conflict'):
        store.finish(operation['id'], result=result, media_versions=MEDIA)
    with error('animation_revision_issues_conflict'):
        store.finish(operation['id'], result=dict(issues=ISSUES), issues=[])
    assert store.operation(operation['id'])['result']['summary'] == 'known issue'


def test_false_completion_and_unsupported_rounds_are_rejected(state):
    store, lineage_id = state
    operation = reserve(store, lineage_id)
    with error('animation_revision_state_conflict'):
        store.finish(operation['id'])
    for stage in ('user_round', 'requirements_task', 'second_review'):
        with error('animation_revision_stage_not_supported'):
            reserve(store, lineage_id, stage)
    with error('invalid_animation_revision_sha256'):
        store.reserve(lineage_id, 'review', 'bad-sha', 'bad-sha-key', 'not-a-sha256')


def test_refuses_to_modify_an_existing_business_database(tmp_path):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO projects VALUES (?)', ('untouched-project',))
    with error('animation_revision_database_not_isolated'):
        AnimationRevisionStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM projects').fetchall() == [('untouched-project',)]
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('projects',)]
