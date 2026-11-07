"""Feedback handoff must not lose corrections, duplicate work, or claim adoption."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from video_workbench.animation_lab_iteration import IterationError, IterationStore
from video_workbench.animation_lab_store import AnnotationStore
from video_workbench.animation_lab_triage_store import TriageStore
from video_workbench.motion import canonical, sha256


REVISION = '1' * 40


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / 'lab.db'
    manual, triage = AnnotationStore(path), TriageStore(path)
    snapshot = dict(schema='video.animation-lab-case.v1', case_id='canvas-queue',
        module='canvas', title='真实身份的测试夹具', runtime=dict(assets={'font.ttc': '2' * 64}),
        comparison=dict(baseline='baseline', candidate='candidate'),
        variants=[dict(variant_id='candidate', compiler='canvas-1', artifact_sha256='3' * 64,
            input_sha256='4' * 64, inputs={'never_export_plan': 'plan'},
            clips=[dict(files={'audio.wav': dict(encoding='base64', data='LARGE_BINARY_SENTINEL')})])])
    snapshot['identity'] = sha256(snapshot)
    manual.register_snapshot(snapshot)
    iteration = IterationStore(path, REVISION, '/dev/animations')
    return manual, triage, iteration, snapshot


def add(stores, kind='issue', **changes):
    manual, _, _, snapshot = stores
    value = dict(case_id='canvas-queue', case_identity=snapshot['identity'], module='canvas',
        variant_id='comparison' if kind == 'preference' else 'candidate', kind=kind,
        start_seconds=2, end_seconds=3, region=dict(x=.1, y=.2, width=.3, height=.4),
        tags=['pacing'], severity='major', comment='降低请求队列出现速度', preference=None, verdict=None,
        client={'forwarded_user': 'DO_NOT_EXPORT_PRINCIPAL', 'user_agent': 'DO_NOT_EXPORT_AGENT'})
    if kind == 'preference':
        value['preference'] = 'candidate'
    if kind == 'review':
        value['verdict'] = 'needs_work'
    value.update(changes)
    return manual.append(value)


def event(task, state, expected=None, key=None, **receipt):
    bound = dict(task_identity=task['task_id'], module='canvas', source_revision=task['source_revision'])
    bound.update(receipt)
    return dict(state=state, expected_state=expected or task['state'],
                idempotency_key=key or state, receipt=bound)


def synced(store, task):
    return store.transition(task['task_id'], event(task, 'synced', run_id='run-1'))


def candidate_event(task):
    return event(task, 'candidate_ready', commit='a' * 40, run_slug='canvas-r1', report_identity='d' * 64,
        candidate_url='/dev/animations/#evaluation-canvas-r1-' + 'e' * 16,
        checks=[dict(name='native', status='passed')])


def synthetic_verifier(package, receipt):
    """Unit-test verifier only; real publication/files are HTTP integration scope."""
    case_id = 'canvas-queue'
    return dict(task_identity=package['identity'], module=package['module'], source_revision=package['source_revision'],
        commit=receipt['commit'], run_slug=receipt['run_slug'], report_identity=receipt['report_identity'],
        run_identity='e' * 64, artifact_sha256s=['f' * 64], semantic_input_sha256='9' * 64,
        publication_identity='8' * 64, candidate_url=receipt['candidate_url'],
        case_id=case_id,
        covered_feedback_keys=sorted(item['key'] for item in package['feedback'] if item.get('case_id') == case_id),
        uncovered_feedback_keys=sorted(item['key'] for item in package['feedback'] if item.get('case_id') != case_id),
        verification_scope='frozen_report_and_developer_publication_binding')


def test_projection_uses_original_hashes_without_binary_plan_or_identity_leak(stores):
    _, _, store, snapshot = stores
    row = add(stores)
    summary = store.summary('canvas', current_cases={'canvas-queue': snapshot['identity']})
    feedback = summary['current_effective_feedback'][0]
    assert feedback['record_id'] == row['id']
    assert feedback['historical_source'] is False
    assert feedback['case_identity'] == snapshot['identity']
    assert feedback['start_seconds'] == 2 and feedback['region']['width'] == .3
    assert feedback['binding']['variants'][0]['id'] == 'candidate'
    assert feedback['binding']['variants'][0]['artifact_sha256'] == '3' * 64
    assert feedback['binding']['runtime']['assets'] == {'font.ttc': '2' * 64}
    task = store.prepare('canvas', expected_cursor=summary['current_cursor'])
    raw = canonical(task['package']).decode()
    assert task['package']['identity'] == sha256({key: value for key, value in task['package'].items() if key != 'identity'})
    assert all(value not in raw for value in ('LARGE_BINARY_SENTINEL', 'never_export_plan', 'DO_NOT_EXPORT', 'base64'))
    assert len(raw) < 10000
    assert feedback['links']['annotations_url'].startswith('/dev/animations/api/')


def test_prepare_and_download_never_acknowledge_and_identical_prepares_are_one_task(stores):
    _, _, store, _ = stores
    add(stores)
    first = store.prepare('canvas')
    second = store.prepare('canvas')
    assert first['task_id'] == second['task_id']
    store.get_task(first['task_id'])
    store.export_jsonl()
    summary = store.summary('canvas')
    assert summary['module_cursor'] is None
    assert len(summary['preview_delta']) == 1 and len(summary['tasks']) == 1
    assert summary['tasks'][0]['state'] == 'prepared'
    assert summary['tasks'][0]['events'] == []


def test_explicit_sync_checkpoint_survives_restart_and_no_change_is_not_a_new_task(stores):
    _, _, store, _ = stores
    add(stores)
    task = store.prepare('canvas')
    result = synced(store, task)
    assert result['state'] == 'synced'
    restored = IterationStore(store.path, REVISION, '/dev/animations')
    assert restored.summary('canvas')['module_cursor'] == task['package']['next_cursor']
    assert restored.summary('canvas')['preview_delta'] == []
    with pytest.raises(IterationError, match='no_changes'):
        restored.prepare('canvas')


def test_retraction_supersession_and_restoration_have_explicit_incremental_changes(stores):
    manual, _, store, _ = stores
    issue = add(stores)
    preference = add(stores, 'preference', preference='baseline')
    first = synced(store, store.prepare('canvas'))
    replacement = add(stores, 'preference', preference='candidate')
    manual.retract(issue['id'], '误点')
    next_task = store.prepare('canvas')
    delta = {item['key']: item for item in next_task['package']['changes']}
    assert delta['annotation:' + issue['id']]['reason'] == 'retracted'
    assert delta['annotation:' + preference['id']]['reason'] == 'superseded'
    assert delta['annotation:' + replacement['id']]['op'] == 'upsert'
    synced(store, next_task)
    manual.retract(replacement['id'], '恢复原偏好')
    restored = store.prepare('canvas')['package']['changes']
    assert any(item['op'] == 'upsert' and item['feedback']['record_id'] == preference['id'] for item in restored)
    assert any(item['op'] == 'inactivate' and item['feedback']['record_id'] == replacement['id'] for item in restored)
    assert first['package']['base_cursor'] is None


def test_machine_findings_only_enter_after_effective_needs_fix_and_changed_decision_removes(stores):
    _, triage, store, _ = stores
    report = dict(schema='video.animation-lab-triage-snapshot.v1', module='canvas', case_id='canvas-queue',
        run_identity='a' * 64, evidence_identity='b' * 64, review_identity=None,
        variants=[dict(id='candidate', artifact_sha256='c' * 64)],
        findings=[dict(id='model-1', source='model', description='队列运动太快', variant_id='candidate',
                       frames=[dict(id='frame-1', variant_id='candidate', time_seconds=2, sha256='d' * 64)])])
    report['identity'] = sha256(report)
    triage.register_report(report)
    triage.append(report['identity'], 'model-1', 'defer')
    assert store.summary('canvas')['current_effective_feedback'] == []
    confirmed = triage.append(report['identity'], 'model-1', 'needs_fix', '改慢一些')
    task = store.prepare('canvas', evaluation_runs={report['identity']: 'canvas-r1'})
    item = task['package']['feedback'][0]
    assert item['record_id'] == confirmed['id'] and item['report_identity'] == report['identity']
    assert item['frames'][0]['image_url'].startswith('/dev/animations/api/evaluations/canvas-r1/frames/')
    # Navigation metadata is not part of opinion freshness.
    synced(store, task)
    triage.append(report['identity'], 'model-1', 'dismiss')
    delta = store.prepare('canvas')['package']['changes']
    assert len(delta) == 1 and delta[0]['op'] == 'inactivate' and delta[0]['reason'] == 'decision_dismiss'


def test_feedback_version_is_never_rebound_and_code_revision_alone_is_not_new_feedback(stores):
    _, _, store, snapshot = stores
    add(stores)
    task = store.prepare('canvas', current_cases={'canvas-queue': snapshot['identity']})
    current = {'canvas-queue': 'f' * 64}
    assert store.prepare('canvas', current_cases=current)['task_id'] == task['task_id']
    stale_display = store.summary('canvas', current_cases=current)
    assert stale_display['current_effective_feedback'][0]['historical_source'] is True
    assert stale_display['current_effective_feedback'][0]['case_identity'] == snapshot['identity']
    assert store.get_task(task['task_id'], current_cases=current)['current_feedback_matches'] is True
    synced(store, task)
    new_code = IterationStore(store.path, '2' * 40, '/dev/animations')
    assert new_code.summary('canvas', current_cases=current)['preview_delta'] == []


def test_concurrent_prepares_do_not_duplicate_and_stale_preview_cannot_be_saved(stores):
    _, _, store, _ = stores
    add(stores)
    old = store.summary('canvas')['current_cursor']
    add(stores, comment='第二个问题')
    with pytest.raises(IterationError, match='feedback_changed'):
        store.prepare('canvas', expected_cursor=old)
    def prepare(_):
        return IterationStore(store.path, REVISION).prepare('canvas')['task_id']
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(prepare, range(3)))
    assert len(set(ids)) == 1


def test_late_sync_records_external_fact_but_stale_task_cannot_start_execution(stores):
    _, _, store, _ = stores
    row = add(stores)
    task = store.prepare('canvas')
    stores[0].retract(row['id'], '不成立')
    task = synced(store, task)
    assert task['current_feedback_matches'] is False
    with pytest.raises(IterationError, match='feedback_changed'):
        store.transition(task['task_id'], event(task, 'running', run_id='run-1', branch='codex/stale'))
    result = store.transition(task['task_id'], event(task, 'canceled', comment='任务生成后意见已撤回'))
    assert result['state'] == 'canceled'
    assert store.summary('canvas')['preview_delta'][0]['op'] == 'inactivate'


def test_states_are_sequential_bound_idempotent_and_not_production_adoption(stores):
    _, _, store, _ = stores
    add(stores)
    task = store.prepare('canvas')
    body = event(task, 'synced', run_id='run-1')
    task = store.transition(task['task_id'], body, actor='developer')
    again = store.transition(task['task_id'], body, actor='developer')
    assert len(again['events']) == 1
    altered = deepcopy(body)
    altered['receipt']['run_id'] = 'different-run'
    with pytest.raises(IterationError, match='idempotency_conflict'):
        store.transition(task['task_id'], altered)
    task = store.transition(task['task_id'], event(task, 'running', run_id='run-1', branch='codex/canvas-fix'))
    with pytest.raises(IterationError, match='execution_binding_changed'):
        store.transition(task['task_id'], event(task, 'pr_open', run_id='other-run', branch='codex/canvas-fix',
            commit='a' * 40, pr_url='https://github.com/zmhhaha/panghu_agent/pull/1'))
    task = store.transition(task['task_id'], event(task, 'pr_open', run_id='run-1', branch='codex/canvas-fix',
        commit='a' * 40, pr_url='https://github.com/zmhhaha/panghu_agent/pull/1'))
    task = store.transition(task['task_id'], candidate_event(task), candidate_verifier=synthetic_verifier)
    accepted = store.transition(task['task_id'], event(task, 'accepted', comment='本样例队列出现节奏合适'),
                                candidate_verifier=synthetic_verifier)
    assert accepted['state'] == 'accepted' and accepted['production_adopted'] is False
    assert accepted['events'][-1]['acceptance_scope'] == 'candidate_case_only'
    assert [row['state'] for row in accepted['events']] == ['synced', 'running', 'pr_open', 'candidate_ready', 'accepted']


@pytest.mark.parametrize('mutate,code', [
    (lambda body: body['receipt'].update(task_identity='f' * 64), 'binding_mismatch'),
    (lambda body: body['receipt'].update(module='three'), 'binding_mismatch'),
    (lambda body: body['receipt'].update(source_revision='2' * 40), 'binding_mismatch'),
    (lambda body: body['receipt'].update(token='secret'), 'receipt_invalid'),
    (lambda body: body['receipt'].update(branch='main'), 'branch_invalid'),
    (lambda body: body['receipt'].update(pr_url='https://github.com/other/repo/pull/1'), 'pr_url_invalid'),
    (lambda body: body['receipt'].update(candidate_url='https://evil.example/'), 'candidate_url_invalid'),
    (lambda body: body['receipt'].update(checks=[dict(name='test', status='approved')]), 'checks_invalid'),
])
def test_unbound_or_unsafe_receipts_are_rejected_without_events(stores, mutate, code):
    _, _, store, _ = stores
    add(stores)
    task = store.prepare('canvas')
    body = event(task, 'synced', run_id='run-1')
    mutate(body)
    with pytest.raises(IterationError, match=code):
        store.transition(task['task_id'], body)
    assert store.get_task(task['task_id'])['events'] == []


def test_unknown_revision_can_export_feedback_but_cannot_dispatch_to_developer_runner(stores):
    _, _, store, _ = stores
    add(stores)
    unknown = IterationStore(store.path)
    task = unknown.prepare('canvas')
    with pytest.raises(IterationError, match='source_revision_unknown'):
        synced(unknown, task)


def test_late_external_pr_receipt_is_retained_even_when_opinion_was_retracted(stores):
    manual, _, store, _ = stores
    row = add(stores)
    task = synced(store, store.prepare('canvas'))
    task = store.transition(task['task_id'], event(task, 'running', run_id='run-1', branch='codex/canvas'))
    manual.retract(row['id'], '此项不需要改了')
    task = store.transition(task['task_id'], event(task, 'pr_open', run_id='run-1', branch='codex/canvas',
        commit='a' * 40, pr_url='https://github.com/zmhhaha/panghu_agent/pull/2'))
    assert task['state'] == 'pr_open' and task['current_feedback_matches'] is False
    task = store.transition(task['task_id'], candidate_event(task), candidate_verifier=synthetic_verifier)
    with pytest.raises(IterationError, match='feedback_changed'):
        store.transition(task['task_id'], event(task, 'accepted', comment='陈旧意见不能核准'), candidate_verifier=synthetic_verifier)
    assert task['events'][-1]['evidence_scope'] == 'developer_imported_receipt_not_independent_execution_verification'


def test_out_of_order_sync_receipt_never_moves_a_newer_checkpoint_backwards(stores):
    _, _, store, _ = stores
    add(stores)
    old = store.prepare('canvas')
    add(stores, comment='更新的一条问题')
    newer = store.prepare('canvas')
    synced(store, newer)
    late = synced(store, old)
    assert late['state'] == 'synced'
    assert late['events'][-1]['synchronization_cursor_applied'] is False
    assert store.summary('canvas')['module_cursor'] == newer['package']['next_cursor']
    assert store.summary('canvas')['preview_delta'] == []


def test_explicit_retry_requires_new_attempt_id_and_keeps_canceled_history(stores):
    _, _, store, _ = stores
    add(stores)
    task = synced(store, store.prepare('canvas'))
    task = store.transition(task['task_id'], event(task, 'running', run_id='run-1', branch='codex/first'))
    task = store.transition(task['task_id'], event(task, 'failed', comment='固定模块检查失败'))
    with pytest.raises(IterationError, match='retry_run_required'):
        store.transition(task['task_id'], event(task, 'running', key='retry-old', run_id='run-1', branch='codex/second'))
    task = store.transition(task['task_id'], event(task, 'running', key='retry-new', run_id='run-2', branch='codex/second'))
    assert task['events'][-1]['new_attempt'] is True
    task = store.transition(task['task_id'], event(task, 'pr_open', key='second-pr', run_id='run-2', branch='codex/second',
        commit='b' * 40, pr_url='https://github.com/zmhhaha/panghu_agent/pull/3'))
    assert [item['state'] for item in task['events']] == ['synced', 'running', 'failed', 'running', 'pr_open']


def test_canceled_before_sync_can_restart_only_after_explicit_new_handoff(stores):
    _, _, store, _ = stores
    add(stores)
    task = store.prepare('canvas')
    task = store.transition(task['task_id'], event(task, 'canceled', comment='暂时取消'))
    with pytest.raises(IterationError, match='sync_required'):
        store.transition(task['task_id'], event(task, 'running', run_id='retry-1', branch='codex/retry'))
    task = store.transition(task['task_id'], event(task, 'synced', key='retry-sync', run_id='retry-1'))
    task = store.transition(task['task_id'], event(task, 'running', key='retry-run', run_id='retry-1', branch='codex/retry'))
    assert task['state'] == 'running' and len(task['events']) == 3


def test_candidate_cannot_be_self_reported_and_acceptance_reverifies_actual_binding(stores):
    _, _, store, _ = stores
    add(stores)
    task = synced(store, store.prepare('canvas'))
    task = store.transition(task['task_id'], event(task, 'running', run_id='run-1', branch='codex/canvas'))
    task = store.transition(task['task_id'], event(task, 'pr_open', run_id='run-1', branch='codex/canvas',
        commit='a' * 40, pr_url='https://github.com/zmhhaha/panghu_agent/pull/4'))
    with pytest.raises(IterationError, match='candidate_verification_required'):
        store.transition(task['task_id'], candidate_event(task))
    assert store.get_task(task['task_id'])['state'] == 'pr_open'
    task = store.transition(task['task_id'], candidate_event(task), candidate_verifier=synthetic_verifier)
    def changed(package, receipt):
        return dict(synthetic_verifier(package, receipt), publication_identity='7' * 64)
    with pytest.raises(IterationError, match='candidate_changed'):
        store.transition(task['task_id'], event(task, 'accepted', comment='错误地核准已变候选'), candidate_verifier=changed)
    assert store.get_task(task['task_id'])['state'] == 'candidate_ready'
    def unavailable(package, receipt):
        raise OSError('private path or credential must not escape')
    with pytest.raises(IterationError, match='candidate_verification_failed'):
        store.transition(task['task_id'], event(task, 'accepted', comment='报告不再可读'), candidate_verifier=unavailable)
    with pytest.raises(IterationError, match='candidate_verification_required'):
        store.transition(task['task_id'], event(task, 'accepted', comment='不重新核验'))


def test_one_case_candidate_cannot_approve_feedback_from_an_entire_module(stores):
    manual, _, store, snapshot = stores
    add(stores)
    other = deepcopy(snapshot)
    other.pop('identity')
    other['case_id'] = 'canvas-other'
    other['identity'] = sha256(other)
    manual.register_snapshot(other)
    add(stores, case_id='canvas-other', case_identity=other['identity'], comment='另一样例仍有问题')
    task = synced(store, store.prepare('canvas'))
    task = store.transition(task['task_id'], event(task, 'running', run_id='run-1', branch='codex/canvas'))
    task = store.transition(task['task_id'], event(task, 'pr_open', run_id='run-1', branch='codex/canvas',
        commit='a' * 40, pr_url='https://github.com/zmhhaha/panghu_agent/pull/5'))
    def overclaim(package, receipt):
        proof = synthetic_verifier(package, receipt)
        return dict(proof, covered_feedback_keys=sorted(item['key'] for item in package['feedback']), uncovered_feedback_keys=[])
    with pytest.raises(IterationError, match='candidate_coverage_invalid'):
        store.transition(task['task_id'], candidate_event(task), candidate_verifier=overclaim)
    task = store.transition(task['task_id'], candidate_event(task), candidate_verifier=synthetic_verifier)
    assert len(task['events'][-1]['verified_candidate']['covered_feedback_keys']) == 1
    assert len(task['events'][-1]['verified_candidate']['uncovered_feedback_keys']) == 1
    with pytest.raises(IterationError, match='candidate_coverage_incomplete'):
        store.transition(task['task_id'], event(task, 'accepted', comment='不能以一例代替整模块'),
                         candidate_verifier=synthetic_verifier)
    assert store.get_task(task['task_id'])['state'] == 'candidate_ready'


def test_no_cross_module_cursor_or_state_skip_and_backup_keeps_task_event_history(stores):
    _, _, store, _ = stores
    add(stores)
    task = store.prepare('canvas')
    with pytest.raises(IterationError, match='transition_invalid'):
        store.transition(task['task_id'], event(task, 'accepted', comment='跳过执行的伪批准'))
    with pytest.raises(IterationError, match='base_cursor_changed'):
        store.prepare('three', base_cursor=task['package']['next_cursor'])
    synced(store, task)
    records = [json.loads(row) for row in store.export_jsonl().splitlines()]
    assert [row['record_type'] for row in records] == ['iteration_task', 'iteration_event', 'iteration_cursor', 'iteration_checkpoint']
    assert records[1]['event']['production_adopted'] is False
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM lab_annotations').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM lab_snapshots').fetchone()[0] == 1
