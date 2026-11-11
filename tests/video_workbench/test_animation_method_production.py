"""Published SQLite fixture delivery; no real rendering/application is claimed."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.animation_method_store import AnimationMethodStore
from video_workbench.animation_method_production import (
    MethodProductionAdapter, collect_method_decision, validate_delivery,
)


TASK = dict(title='群体筛选后的统计变化', visual_description='通过对象去向解释筛选与统计因果',
            narration='筛选改变留下来的群体，所以统计结果会跟着变化。')


def published(store, *, parent=None, **changes):
    key = str(uuid4())
    body = dict(title='保持群体筛选的因果关系', problem='群体变化与统计结果失去关联',
        guidance='保持对象身份可追踪，先展开筛选过程，再让统计变化跟随。',
        applicability='筛选、统计与因果关系的解释', limitations=['只作为当前测试的持久化证据'],
        criteria=[dict(id='causality', description='群体筛选的因果关系可读')],
        runtime_requirements=dict(engine='html-svg'))
    if parent:
        body.update(method_id=parent['method_id'], parent_version_id=parent['id'])
    body.update(changes)
    method = store.create_method('developer', body, 'method-' + key)
    demos = []
    for index in range(2):
        demo = store.add_demonstration('developer', dict(title='Fixture sample', goal='Fixture evidence only',
            media=dict(sha256=('a' if index == 0 else 'b') * 64, duration_seconds=12), source=None,
            runtime=dict(engine=body['runtime_requirements']['engine'], admission=dict(
                status='admitted', id='fixture-runtime', evidence_sha256='c' * 64)),
            technical=dict(status='passed', evidence_sha256='d' * 64),
            provenance=dict(fixture_only=True, render_id=key + str(index))), 'demo-' + key + str(index))
        demos.append(demo)
        validation = dict(method_version_id=method['id'], demonstration_version_id=demo['id'],
            role='reference' if index == 0 else 'transfer', criteria_sha256=method['criteria_sha256'],
            outcome='passed', evidence=dict(media_sha256=demo['media']['sha256'], fixture_only=True))
        if index:
            validation.update(baseline_demonstration_version_id=demos[0]['id'],
                              structural_changes=dict(object_count=dict(before=4, after=9)))
        record = store.link_validation('developer', validation, 'validation-' + key + str(index))
        store.accept('developer', dict(validation_id=record['id'], comment='Fixture approval only',
            method_content_sha256=method['content_sha256']), 'approval-' + key + str(index))
    return store.publish('developer', method['id'], 'publication-' + key)


@pytest.fixture
def store(tmp_path):
    return AnimationMethodStore(tmp_path / 'methods.sqlite3')


def adapter(store, calls):
    def catalogue(owner):
        calls.append(('catalogue', owner))
        return dict(methods=store.list_methods(owner, published_only=True))

    def read(owner, body):
        calls.append(('read', owner, deepcopy(body)))
        return store.read_published(owner, body['method_version_ids'], body['consumer_id'])
    return MethodProductionAdapter(catalogue, read,
        ledger_path=Path(store.path).with_name('production-freezes.sqlite3'))


def test_exact_published_knowledge_is_delivered_to_another_owner_without_copying_sample_source(store):
    guidance = '保持对象身份并明确筛选过程。' * 1100
    method = published(store, guidance=guidance)
    calls = []
    snapshot = adapter(store, calls).freeze('video-user', 'creative-operation-1', TASK, ['html-svg'])
    assert [row['id'] for row in snapshot['methods']] == [method['id']]
    assert snapshot['methods'][0]['guidance'] == guidance
    assert len(guidance) > 10000
    assert 'source' not in snapshot['methods'][0]
    assert snapshot['read_receipt']['owner'] == 'video-user'
    assert snapshot['read_receipt']['method_versions'] == [dict(
        version_id=method['id'], content_sha256=method['content_sha256'])]
    assert snapshot['evidence_scope'] == 'frozen_input_delivery_not_agent_application'
    assert calls[1][2] == dict(method_version_ids=[method['id']], consumer_id='creative-operation-1')
    assert validate_delivery(snapshot) == snapshot


def test_no_matching_method_or_only_an_unadmitted_engine_permits_free_creation_without_fake_receipt(store):
    published(store, title='镜头跟踪', problem='相机越过空间遮挡', applicability='相机空间运镜',
              runtime_requirements=dict(engine='three'))
    calls = []
    instance = adapter(store, calls)
    snapshot = instance.freeze('user', 'no-eligible-engine', TASK, ['html-svg'])
    assert snapshot['methods'] == [] and snapshot['read_receipt'] is None
    assert snapshot['selection']['disposition'] == 'free_creation_no_match'
    assert snapshot['selection']['excluded'][0]['reason'] == 'engine_not_admitted'
    published(store)
    unmatched = instance.freeze('user', 'no-matching-goal', dict(title='planetary orbital astronomy'), ['html-svg'])
    assert unmatched['methods'] == []
    assert all(row[0] == 'catalogue' for row in calls)
    assert collect_method_decision(unmatched, dict(delivery_sha256=unmatched['sha256'], decisions=[]))['decisions'] == []


def test_new_publication_only_changes_new_operations_and_recovery_reads_no_catalogue_or_service(store):
    original = published(store)
    calls = []
    instance = adapter(store, calls)
    frozen = instance.freeze('user', 'in-flight', TASK, ['html-svg'])
    newer = published(store, parent=original, guidance='按当前任务重新安排对象身份与因果关系。')
    before = list(calls)
    recovered = instance.freeze('user', 'in-flight', TASK, ['html-svg'], existing=frozen)
    assert recovered == frozen and calls == before
    assert instance.freeze('user', 'next-operation', TASK, ['html-svg'])['methods'][0]['id'] == newer['id']
    assert frozen['methods'][0]['id'] == original['id']
    with pytest.raises(ServiceError, match='production_method_frozen_input_changed'):
        instance.freeze('other-user', 'in-flight', TASK, ['html-svg'], existing=frozen)
    with pytest.raises(ServiceError, match='production_method_frozen_input_changed'):
        instance.freeze('user', 'in-flight', dict(title='Changed task'), ['html-svg'], existing=frozen)


def test_oversized_method_is_excluded_whole_without_truncating_its_reusable_knowledge(store):
    huge = published(store, guidance='g' * 64000,
        criteria=[dict(id='criterion-' + str(index), description='d' * 8000) for index in range(32)])
    calls = []
    snapshot = adapter(store, calls).freeze('user', 'oversized', TASK, ['html-svg'])
    assert snapshot['methods'] == [] and snapshot['read_receipt'] is None
    assert snapshot['selection']['excluded'] == [dict(version_id=huge['id'], reason='complete_method_exceeds_budget')]
    assert store.get_method('user', huge['id'])['guidance'] == 'g' * 64000
    assert len(canonical_bytes(snapshot)) < 256 * 1024


def test_read_catalog_mismatch_and_lookup_failure_do_not_silently_become_free_creation(store):
    published(store)
    def changed_read(owner, body):
        value = store.read_published(owner, body['method_version_ids'], body['consumer_id'])
        value['methods'][0]['guidance'] += ' modified'
        return value
    instance = MethodProductionAdapter(lambda owner: dict(methods=store.list_methods(owner, published_only=True)), changed_read,
        ledger_path=Path(store.path).with_name('production-freezes.sqlite3'))
    with pytest.raises(ServiceError, match='production_method_catalog_changed'):
        instance.freeze('user', 'changed-read', TASK, ['html-svg'])

    def unavailable(owner):
        raise ServiceError('method_service_unavailable', 503)
    with pytest.raises(ServiceError, match='method_service_unavailable'):
        MethodProductionAdapter(unavailable, None,
            ledger_path=Path(store.path).with_name('unavailable-freezes.sqlite3')).freeze(
                'user', 'unavailable', TASK, ['html-svg'])


def test_candidate_shortlist_is_bounded_and_agent_decisions_are_complete_version_bound_self_reports(store):
    methods = [published(store, title='群体筛选的因果变化 ' + str(index)) for index in range(4)]
    calls = []
    snapshot = adapter(store, calls).freeze('user', 'bounded', TASK, ['html-svg'])
    assert len(snapshot['methods']) == 3
    assert any(row['reason'] == 'shortlist_limit' for row in snapshot['selection']['excluded'])
    decisions = [dict(version_id=row['id'], content_sha256=row['content_sha256'], action='reject',
        reason='本次任务更适合另行组织空间关系。') for row in snapshot['methods']]
    declared = collect_method_decision(snapshot, dict(delivery_sha256=snapshot['sha256'], decisions=decisions))
    assert declared['evidence_scope'] == 'agent_self_report_not_observed_application_or_quality'
    assert all(row['action'] == 'reject' for row in declared['decisions'])
    with pytest.raises(ServiceError, match='production_method_decision_incomplete'):
        collect_method_decision(snapshot, dict(delivery_sha256=snapshot['sha256'], decisions=decisions[:-1]))
    with pytest.raises(ServiceError, match='production_method_decision_binding_invalid'):
        collect_method_decision(snapshot, dict(delivery_sha256='0' * 64, decisions=decisions))
    changed = deepcopy(decisions)
    changed[0]['version_id'] = next(row['id'] for row in methods if row['id'] not in {value['version_id'] for value in decisions})
    with pytest.raises(ServiceError, match='production_method_decision_invalid'):
        collect_method_decision(snapshot, dict(delivery_sha256=snapshot['sha256'], decisions=changed))


def test_crash_after_remote_receipt_before_local_snapshot_recovers_exact_old_selection(store, monkeypatch):
    original = published(store)
    calls = []
    instance = adapter(store, calls)
    captured = []

    def interrupted(binding, intent, snapshot):
        captured.append(deepcopy(snapshot))
        raise OSError('simulated process crash after remote delivery, before local snapshot commit')

    monkeypatch.setattr(instance, '_persist_snapshot', interrupted)
    with pytest.raises(OSError, match='simulated process crash'):
        instance.freeze('user', 'interrupted-operation', TASK, ['html-svg'])
    assert captured[0]['methods'][0]['id'] == original['id']
    newer = published(store, parent=original, guidance='New knowledge for future operations only.')
    before = len(calls)
    recovered = adapter(store, calls).freeze('user', 'interrupted-operation', TASK, ['html-svg'])
    assert recovered == captured[0]
    assert recovered['methods'][0]['id'] != newer['id']
    assert [row[0] for row in calls[before:]] == ['read']
    assert calls[-1][2]['method_version_ids'] == [original['id']]
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM animation_method_deliveries '
            'WHERE owner=? AND consumer_id=?', ('user', 'interrupted-operation')).fetchone()[0] == 1
    before = list(calls)
    assert adapter(store, calls).freeze('user', 'interrupted-operation', TASK, ['html-svg']) == recovered
    assert calls == before  # A fully saved snapshot needs no remote read.


def test_crash_before_remote_read_still_recovers_committed_intent_after_new_publication(store):
    original = published(store)
    path = Path(store.path).with_name('pending-freeze.sqlite3')
    read_calls = []

    def no_read(owner, body):
        read_calls.append(deepcopy(body))
        raise OSError('read not attempted before process interruption')

    instance = MethodProductionAdapter(lambda owner: store.list_methods(owner, published_only=True),
                                       no_read, ledger_path=path)
    with pytest.raises(OSError):
        instance.freeze('user', 'pending-operation', TASK, ['html-svg'])
    published(store, parent=original, guidance='A later published method version.')

    def no_catalogue(_):
        pytest.fail('An existing reservation must never reselect from the catalogue')

    def read(owner, body):
        assert body == read_calls[0]
        return store.read_published(owner, body['method_version_ids'], body['consumer_id'])

    result = MethodProductionAdapter(no_catalogue, read, ledger_path=path).freeze(
        'user', 'pending-operation', TASK, ['html-svg'])
    assert result['methods'][0]['id'] == original['id']


def test_same_operation_without_existing_recovers_no_match_even_when_library_grows(store):
    calls = []
    snapshot = adapter(store, calls).freeze('user', 'no-match-in-flight', TASK, ['html-svg'])
    assert snapshot['methods'] == []
    published(store)
    before = list(calls)
    assert adapter(store, calls).freeze('user', 'no-match-in-flight', TASK, ['html-svg']) == snapshot
    assert calls == before
    assert adapter(store, calls).freeze('user', 'new-operation', TASK, ['html-svg'])['methods']


def test_concurrent_instances_recover_one_snapshot_and_one_remote_delivery(store):
    published(store)
    calls = []
    instances = [adapter(store, calls), adapter(store, calls)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(item.freeze, 'user', 'same-operation', TASK, ['html-svg']) for item in instances]
        snapshots = [future.result(10) for future in futures]
    assert snapshots[0] == snapshots[1]
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM animation_method_deliveries '
            'WHERE owner=? AND consumer_id=?', ('user', 'same-operation')).fetchone()[0] == 1


def test_ledger_binds_task_owner_engines_and_rejects_conflicting_existing_snapshot(store):
    original = published(store)
    calls = []
    instance = adapter(store, calls)
    frozen = instance.freeze('user', 'bound', TASK, ['html-svg'])
    with pytest.raises(ServiceError, match='production_method_frozen_input_changed'):
        adapter(store, calls).freeze('user', 'bound', dict(title='Other task'), ['html-svg'])
    with pytest.raises(ServiceError, match='production_method_frozen_input_changed'):
        adapter(store, calls).freeze('user', 'bound', TASK, ['html-svg', 'canvas'])
    published(store, parent=original)
    changed = instance.freeze('other-user', 'bound', TASK, ['html-svg'])
    assert changed['recipient_owner'] == 'other-user'
    assert changed['methods'][0]['id'] != frozen['methods'][0]['id']
    # A different, individually valid snapshot cannot overwrite the first
    # owner/consumer reservation, even when supplied through `existing`.
    from agent_service.contracts import digest
    changed['recipient_owner'] = 'user'
    changed['read_receipt']['owner'] = 'user'
    changed['sha256'] = digest({key: value for key, value in changed.items() if key != 'sha256'})
    with pytest.raises(ServiceError, match='production_method_frozen_input_changed'):
        instance.freeze('user', 'bound', TASK, ['html-svg'], existing=changed)


def test_freeze_ledger_must_be_separate_from_business_method_database(store):
    with pytest.raises(ServiceError, match='production_method_ledger_not_isolated'):
        MethodProductionAdapter(None, None, ledger_path=store.path)
    assert store.list_methods('developer') == []
