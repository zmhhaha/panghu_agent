"""Recoverable visibility over real stores; synthetic evidence, no paid work."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
from threading import Event
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench import animation_method_renderer as render_module
from video_workbench import animation_method_trash as trash_module
from test_animation_method_service import work, start, finish
from test_animation_method_renderer import adapter, SOURCE, document, NEW_IMAGE
from test_animation_method_effect_groups import alter_frozen_fixture


OWNER = 'developer'


def snapshot_database(path):
    with sqlite3.connect(path) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: list(db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')) for name in names}


def evidence(service):
    return (snapshot_database(service.commissions.path), snapshot_database(service.methods.path))


def no_work(service, **changes):
    run = start(service, **changes)
    service.commissions.stop(OWNER, run['id'], 'failed', 'Fixture has no execution')
    return service.get(OWNER, run['id'])


def stop_reader(renderer, monkeypatch):
    monkeypatch.setattr(renderer, 'stopping_proof', lambda identity: dict(
        operation_id=identity, stop_proven=identity in renderer.results), raising=False)


def delete(service, identity, scope='commission', body=None):
    if body is None:
        if scope == 'commission':
            eligibility = service.get(OWNER, identity)['deletion']
        else:
            eligibility = next(e['deletion'] for e in service.catalogue(OWNER)['effects'] if e['id'] == identity)
        body = dict(request_id=str(uuid4()), expected_snapshot_sha256=eligibility['snapshot_sha256'])
    return service.trash.delete(OWNER, scope, identity, body)


def restore(service, item, **changes):
    return service.restore_trash(OWNER, item['entry']['id'], dict(request_id=str(uuid4()), **changes))


def test_deleting_representative_keeps_stable_effect_and_reselects_retained_media(work, monkeypatch):
    service, creator, reviewer, renderer = work
    stop_reader(renderer, monkeypatch)
    original = finish(service, start(service, title='Stable effect title')['id'])
    newer = finish(service, start(service, base_commission_id=original['id'])['id'])
    before, calls = evidence(service), deepcopy((creator.submissions, reviewer.submissions, renderer.calls))
    item = delete(service, newer['id'])
    effect = service.catalogue(OWNER)['effects'][0]
    assert effect['id'] == original['id'] and effect['title'] == original['title']
    assert effect['open_commission_id'] == original['id']
    assert effect['best_candidate']['id'] == original['best_candidate_id']
    assert service.artifact(OWNER, original['id'], original['best_candidate_id'], 'media')
    with pytest.raises(ServiceError, match='commission_not_found'):
        service.get(OWNER, newer['id'])
    restore(service, item)
    assert service.catalogue(OWNER)['effects'][0]['open_commission_id'] == newer['id']
    assert evidence(service) == before
    assert calls == (creator.submissions, reviewer.submissions, renderer.calls)


def test_root_deletion_and_overlapping_group_restore_keep_identity_and_prior_single_delete(work):
    service, _, _, _ = work
    root = no_work(service, title='Original identity')
    sibling = no_work(service, retry_of_commission_id=root['id'])
    before = evidence(service)
    single = delete(service, root['id'])
    effect = service.catalogue(OWNER)['effects'][0]
    assert effect['id'] == root['id'] and effect['open_commission_id'] == sibling['id']
    whole = delete(service, root['id'], 'effect')
    assert service.catalogue(OWNER)['effects'] == []
    restore(service, whole)
    assert [r['id'] for r in service.catalogue(OWNER)['commissions']] == [sibling['id']]
    assert service.trash_catalogue(OWNER)['items'] == [single['entry']]
    restore(service, single)
    assert {r['id'] for r in service.catalogue(OWNER)['commissions']} == {root['id'], sibling['id']}
    assert evidence(service) == before


def test_restart_idempotency_owner_and_stale_snapshot_are_metadata_only(work):
    service, creator, reviewer, renderer = work
    run = no_work(service)
    body = dict(request_id=str(uuid4()), expected_snapshot_sha256=service.get(OWNER, run['id'])['deletion']['snapshot_sha256'])
    before = evidence(service)
    with pytest.raises(ServiceError, match='snapshot_changed'):
        delete(service, run['id'], body=dict(body, expected_snapshot_sha256='0' * 64))
    item = delete(service, run['id'], body=body)
    reopened = AnimationMethodService(service.root, author_gateway=creator, review_gateway=reviewer, renderer=renderer)
    assert delete(reopened, run['id'], body=body) == item
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        delete(reopened, run['id'], body=dict(body, expected_snapshot_sha256='0' * 64))
    assert reopened.trash_catalogue('other')['items'] == []
    with pytest.raises(ServiceError, match='not_found'):
        reopened.restore_trash('other', item['entry']['id'], {'request_id': str(uuid4())})
    restore_body = {'request_id': str(uuid4())}
    restored = reopened.restore_trash(OWNER, item['entry']['id'], restore_body)
    assert reopened.restore_trash(OWNER, item['entry']['id'], restore_body) == restored
    assert evidence(service) == before


def test_legacy_deleted_failure_stays_hidden_after_new_group_restore(work, monkeypatch):
    service, _, _, renderer = work
    stop_reader(renderer, monkeypatch)
    old = no_work(service)
    current = finish(service, start(service, retry_of_commission_id=old['id'])['id'])
    service.record_failed_attempt_deletion(OWNER, old['id'], reason='Explicit old failed-record removal')
    legacy = snapshot_database(service.metadata_path)['commission_deletions']
    before = evidence(service)
    item = delete(service, current['id'], 'effect')
    assert item['entry']['member_ids'] == [current['id']]
    restore(service, item)
    assert [r['id'] for r in service.catalogue(OWNER)['commissions']] == [current['id']]
    assert snapshot_database(service.metadata_path)['commission_deletions'] == legacy
    assert evidence(service) == before


def test_hidden_old_urls_cannot_write_feedback_withdraw_cancel_or_transfer(work, monkeypatch):
    service, _, _, renderer = work
    stop_reader(renderer, monkeypatch)
    run = finish(service, start(service)['id'])
    comment = dict(request_id=str(uuid4()), candidate_id=run['best_candidate_id'], comment='Keep this opinion')
    saved = service.feedback(OWNER, run['id'], comment)
    delete(service, run['id'])
    before = (evidence(service), snapshot_database(service.metadata_path))
    actions = [lambda: service.feedback(OWNER, run['id'], dict(comment, request_id=str(uuid4()))),
        lambda: service.withdraw_feedback(OWNER, run['id'], dict(request_id=str(uuid4()), feedback_id=saved['id'])),
        lambda: service.cancel(OWNER, run['id'], {}),
        lambda: service.transfer(OWNER, run['id'], dict(request_id=str(uuid4()), goal='Other task',
            structural_changes={'relations': dict(before='chain', after='branch')}, budget=dict(max_rounds=1, max_seconds=300)))]
    for action in actions:
        with pytest.raises(ServiceError, match='commission_not_found') as rejected:
            action()
        assert rejected.value.status == 404
        assert (evidence(service), snapshot_database(service.metadata_path)) == before


@pytest.mark.parametrize('action', ['feedback', 'withdraw_feedback'])
def test_waiting_opinion_rechecks_visibility_after_delete_and_writes_nothing(work, monkeypatch, action):
    service, _, _, renderer = work
    stop_reader(renderer, monkeypatch)
    run = finish(service, start(service)['id'])
    comment = dict(request_id=str(uuid4()), candidate_id=run['best_candidate_id'], comment='Prior opinion')
    saved = service.feedback(OWNER, run['id'], comment)
    body = (dict(comment, request_id=str(uuid4())) if action == 'feedback' else
            dict(request_id=str(uuid4()), feedback_id=saved['id']))
    waiting = Event()
    original_lock = trash_module._run_lock

    @contextmanager
    def observed_lock(path):
        with original_lock(path) as acquired:
            if not acquired:
                waiting.set()
            yield acquired

    monkeypatch.setattr(trash_module, '_run_lock', observed_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service.trash.mutation(OWNER):
            future = pool.submit(getattr(service, action), OWNER, run['id'], body)
            assert waiting.wait(2), 'The opinion must actually wait behind the owner lock'
            delete(service, run['id'])
            before = (evidence(service), snapshot_database(service.metadata_path))
        with pytest.raises(ServiceError, match='commission_not_found') as rejected:
            future.result(timeout=5)
        assert rejected.value.status == 404
    assert (evidence(service), snapshot_database(service.metadata_path)) == before


@pytest.mark.parametrize('kind', ['active', 'uncertain', 'unknown_stop', 'wrong_job'])
def test_running_unknown_and_mismatched_remote_execution_fail_closed(work, kind):
    service, creator, _, _ = work
    run = start(service)
    if kind == 'active':
        expected = 'trash_active'
    else:
        op = service.commissions.reserve(OWNER, run['id'], 'creator', {'mode': 'plan'}, 'test-operation')
        service.commissions.claim(OWNER, op['id'])
        if kind == 'uncertain':
            expected = 'trash_uncertain'
        else:
            service.commissions.fail(OWNER, op['id'], 'Fixture stopped')
            creator.jobs[op['id']] = dict(state='completed', stop_proven=kind == 'wrong_job', job_id='other-job')
            if kind == 'wrong_job':
                # Bind the original job without forging any completion proof.
                with sqlite3.connect(service.commissions.path) as db:
                    raw = json.loads(db.execute('SELECT document FROM animation_method_commission_operations WHERE id=?', (op['id'],)).fetchone()[0])
                    raw['job_id'] = 'bound-job'
                    db.execute('UPDATE animation_method_commission_operations SET document=? WHERE id=?', (json.dumps(raw), op['id']))
            expected = 'trash_stop_unproven'
        if kind != 'uncertain':
            service.commissions.stop(OWNER, run['id'], 'failed', 'Fixture terminal')
    before = evidence(service)
    with pytest.raises(ServiceError, match=expected):
        delete(service, run['id'])
    assert evidence(service) == before and service.trash_catalogue(OWNER)['items'] == []


def publishable(service):
    first = finish(service, start(service)['id'])
    second = service.transfer(OWNER, first['id'], dict(request_id=str(uuid4()), goal='Explain a branch',
        structural_changes={'relations': dict(before='chain', after='branch')}, budget=dict(max_rounds=1, max_seconds=300)))
    second = finish(service, second['id'])
    first = service.get(OWNER, first['id'])
    body = dict(request_id=str(uuid4()), method_version_ids=first['publication_method_version_ids'],
        demonstration_ids=[first['candidates'][0]['demonstration_id'], second['candidates'][0]['demonstration_id']],
        human_verification=dict(motion_watched=True, audio_listened=False))
    return first, second, body


def test_published_reference_and_transfer_evidence_are_protected(work):
    service, _, _, _ = work
    first, second, body = publishable(service)
    service.publish(OWNER, first['id'], body)
    before = evidence(service)
    for identity in (first['id'], second['id']):
        with pytest.raises(ServiceError, match='trash_protected'):
            delete(service, identity)
    assert evidence(service) == before


def test_publication_between_stop_read_and_write_is_rechecked(work, monkeypatch):
    service, _, _, _ = work
    first, _, body = publishable(service)
    monkeypatch.setattr(service.trash, 'stopped', lambda *args: service.publish(OWNER, first['id'], body))
    with pytest.raises(ServiceError, match='snapshot_changed'):
        delete(service, first['id'])
    assert service.trash_catalogue(OWNER)['items'] == []
    assert service.get(OWNER, first['id'])['id'] == first['id']


def test_published_method_reuse_does_not_protect_unpublished_new_experiment(work, monkeypatch):
    service, _, _, renderer = work
    stop_reader(renderer, monkeypatch)
    first, _, body = publishable(service)
    service.publish(OWNER, first['id'], body)
    # The owner mutation lock must not prevent exact formal reads.
    with service.trash.mutation(OWNER):
        delivery = service.read_methods('consumer', dict(method_version_ids=body['method_version_ids'], consumer_id='formal-read'))
    assert delivery['methods'][0]['id'] == body['method_version_ids'][0]
    later = service.transfer(OWNER, first['id'], dict(request_id=str(uuid4()), goal='Another branch',
        structural_changes={'relations': dict(before='chain', after='another branch')}, budget=dict(max_rounds=1, max_seconds=300)))
    later = finish(service, later['id'])
    before = evidence(service)
    delete(service, later['id'])
    assert service.get(OWNER, first['id'])['id'] == first['id']
    assert evidence(service) == before


def test_effect_snapshot_rejects_added_member_and_other_process_mutations(work):
    service, creator, reviewer, renderer = work
    first = no_work(service)
    body = dict(request_id=str(uuid4()), expected_snapshot_sha256=service.catalogue(OWNER)['effects'][0]['deletion']['snapshot_sha256'])
    no_work(service, retry_of_commission_id=first['id'])
    before = evidence(service)
    with pytest.raises(ServiceError, match='snapshot_changed'):
        delete(service, first['id'], 'effect', body)
    another = AnimationMethodService(service.root, author_gateway=creator, review_gateway=reviewer, renderer=renderer)
    with service.trash.mutation(OWNER):
        with pytest.raises(ServiceError, match='library_busy'):
            no_work(another)
        with pytest.raises(ServiceError, match='library_busy'):
            delete(another, first['id'])
    assert evidence(service) == before


@pytest.mark.parametrize('kind', ['missing-ledger', 'recipe-active', 'renderer-active', 'stopped'])
def test_resource_cleanup_proof_is_readonly_and_missing_ledger_never_created(work, tmp_path, kind):
    service, _, _, _ = work
    run = no_work(service)
    alter_frozen_fixture(service, run['id'], resource_authoring={'fixture': 'resource cleanup binding'})
    coordinator = SimpleNamespace(path=tmp_path / 'resource-ledger.sqlite3')
    service.manim = coordinator
    if kind != 'missing-ledger':
        scope = dict(owner=OWNER, experiment_id=run['id'], operation_id='recipe-op')
        old = dict(scope=scope, state='failed', recipe_claimed=kind == 'recipe-active', job_id='bound-job')
        if kind == 'renderer-active':
            old['plan'] = {}
            coordinator.executor = SimpleNamespace(lookup=lambda **kwargs: dict(status='running', stop_proven=False, scope=scope))
        if kind == 'recipe-active':
            coordinator.gateway = SimpleNamespace(lookup=lambda identity: dict(state='running', stop_proven=False, job_id='bound-job'))
        with sqlite3.connect(coordinator.path) as db:
            db.execute('CREATE TABLE resources(owner TEXT,commission_id TEXT,document TEXT,sha256 TEXT)')
            db.execute('INSERT INTO resources VALUES(?,?,?,?)', (OWNER, run['id'], json.dumps(old), digest(old)))
    before = evidence(service)
    resource_before = coordinator.path.read_bytes() if coordinator.path.exists() else None
    with service.trash.database() as db:
        eligibility = service.trash.eligibility(db, service.trash.state(db, OWNER), 'commission', run['id'])[0]
    body = dict(request_id=str(uuid4()), expected_snapshot_sha256=eligibility['snapshot_sha256'])
    if kind == 'stopped':
        delete(service, run['id'], body=body)
    else:
        with pytest.raises(ServiceError, match='stop_unproven'):
            delete(service, run['id'], body=body)
        assert service.trash_catalogue(OWNER)['items'] == []
    assert evidence(service) == before
    assert (coordinator.path.read_bytes() if coordinator.path.exists() else None) == resource_before


@pytest.mark.parametrize('kind', ['intent', 'active-missing-ledger', 'node-stop-unproven', 'node-submission-unknown'])
def test_shared_resource_binding_cannot_hide_incomplete_or_missing_plan(work, tmp_path, kind):
    service, _, _, _ = work
    run = no_work(service)
    alter_frozen_fixture(service, run['id'], resource_authoring={'fixture': 'shared resource binding'})
    service.manim = SimpleNamespace(path=tmp_path / 'absent-coordinator.sqlite3')
    request_sha256 = 'c' * 64
    with sqlite3.connect(service.commissions.path) as db:
        db.execute('CREATE TABLE lab_resource_bindings(owner TEXT,operation_id TEXT,request_sha256 TEXT,state TEXT)')
        db.execute('INSERT INTO lab_resource_bindings VALUES(?,?,?,?)',
            (OWNER, run['id'], request_sha256, 'intent' if kind == 'intent' else 'active'))
    if kind.startswith('node-'):
        plan = dict(owner=OWNER, operation_id=run['id'], project_id='fixture-project', request_sha256=request_sha256,
            nodes=dict(manim=dict(stop_proven=kind != 'node-stop-unproven',
                status='submission_unknown' if kind == 'node-submission-unknown' else 'failed')))
        with sqlite3.connect(service.resource_stage.path) as db:
            db.execute('CREATE TABLE resource_plans(owner TEXT,operation_id TEXT,project_id TEXT,request_sha256 TEXT,document TEXT,sha256 TEXT)')
            db.execute('INSERT INTO resource_plans VALUES(?,?,?,?,?,?)',
                (OWNER, run['id'], plan['project_id'], request_sha256, json.dumps(plan), digest(plan)))
    with service.trash.database() as db:
        eligibility = service.trash.eligibility(db, service.trash.state(db, OWNER), 'commission', run['id'])[0]
    body = dict(request_id=str(uuid4()), expected_snapshot_sha256=eligibility['snapshot_sha256'])
    before = evidence(service)
    plan_before = service.resource_stage.path.read_bytes() if service.resource_stage.path.exists() else None
    with pytest.raises(ServiceError, match='stop_unproven'):
        delete(service, run['id'], body=body)
    assert evidence(service) == before and service.trash_catalogue(OWNER)['items'] == []
    assert (service.resource_stage.path.read_bytes() if service.resource_stage.path.exists() else None) == plan_before
    assert not service.manim.path.exists()


@pytest.mark.parametrize('corrupt', ['raw-json', 'frozen', 'sqlite-state', 'operation-input'])
def test_corrupt_original_records_cannot_be_hidden_or_repaired(work, corrupt):
    service, _, _, _ = work
    run = no_work(service)
    body = dict(request_id=str(uuid4()), expected_snapshot_sha256=service.get(OWNER, run['id'])['deletion']['snapshot_sha256'])
    with sqlite3.connect(service.commissions.path) as db:
        if corrupt == 'sqlite-state':
            db.execute('UPDATE animation_method_commissions SET state=? WHERE id=?', ('queued', run['id']))
        else:
            raw = db.execute('SELECT document FROM animation_method_commissions WHERE id=?', (run['id'],)).fetchone()[0]
            value = json.loads(raw)
            if corrupt == 'frozen':
                value['frozen']['goal'] = 'Changed without matching frozen hash'
            if corrupt == 'operation-input':
                db.execute('INSERT INTO animation_method_commission_operations '
                    '(id,commission_id,owner,stage,idem,request_sha256,status,document) VALUES(?,?,?,?,?,?,?,?)',
                    ('orphan', run['id'], OWNER, 'creator', 'fixture', '0' * 64, 'completed', '{'))
            else:
                db.execute('UPDATE animation_method_commissions SET document=? WHERE id=?',
                           ('{' if corrupt == 'raw-json' else json.dumps(value), run['id']))
    before = evidence(service)
    with pytest.raises(ServiceError, match='trash_record_changed'):
        delete(service, run['id'], body=body)
    assert evidence(service) == before


@pytest.mark.parametrize('accepted', [False, True])
def test_trash_blocks_new_acceptance_and_publication_for_its_evidence(work, monkeypatch, accepted):
    service, _, _, renderer = work
    stop_reader(renderer, monkeypatch)
    first, second, body = publishable(service)
    if accepted:
        version = service.methods.get_method(OWNER, first['publication_method_version_ids'][0])
        for validation in service.methods.list_validations(OWNER, method_version_id=version['id']):
            service.accept_validation(OWNER, validation['id'], dict(request_id=str(uuid4()),
                method_content_sha256=version['content_sha256'], comment='Explicit fixture approval',
                human_verification=dict(criteria_ids=[r['id'] for r in version['criteria']],
                    motion_watched=True, audio_listened=False)))
    delete(service, second['id'])
    before = evidence(service)
    with pytest.raises(ServiceError, match='evidence_trashed'):
        service.publish(OWNER, first['id'], body)
    validations = service.methods.list_validations(OWNER)
    target = next(v for v in validations if v['demonstration_version_id'] == second['candidates'][0]['demonstration_id'])
    with pytest.raises(ServiceError, match='evidence_trashed'):
        service.trash.require_validation_visible(OWNER, target['id'])
    assert evidence(service) == before


def test_original_renderer_stop_proof_is_readonly_and_survives_image_upgrade(adapter):
    renderer, execution = adapter
    renderer.render('stop-proof', SOURCE, document())
    before = renderer.ledger.read_bytes()
    restarted = render_module.MethodAnimationRenderer(renderer.root, NEW_IMAGE)
    assert restarted.stopping_proof('stop-proof')['stop_proven'] is True
    assert execution.calls == 1 and renderer.ledger.read_bytes() == before


@pytest.mark.parametrize('change', ['outcome', 'receipt', 'missing-receipt', 'missing-proof', 'status', 'cleanup-container'])
def test_stop_proof_cannot_be_forged_by_editing_original_evidence(adapter, change):
    renderer, execution = adapter
    renderer.render('stop-tamper', SOURCE, document())
    root = renderer._directory('stop-tamper')
    if change == 'missing-proof':
        with sqlite3.connect(renderer.ledger) as db:
            db.execute('UPDATE animation_method_render_operations SET proof_sha256=NULL')
    elif change == 'missing-receipt':
        (root / 'work/render-receipt.json').unlink()
    else:
        path = root / ('work/render-receipt.json' if change == 'receipt' else 'render-outcome.json')
        raw = json.loads(path.read_bytes())
        if change == 'receipt':
            raw['output_sha256'] = 'f' * 64
        elif change == 'outcome':
            raw['cleanup'] = dict(status='not_created', container_id=None)
        elif change == 'status':
            raw['status'] = 'unknown'
        else:
            raw['cleanup']['container_id'] = 'e' * 64
        path.write_bytes(canonical_bytes(raw))
    before = renderer.ledger.read_bytes()
    with pytest.raises((ServiceError, FileNotFoundError)):
        renderer.stopping_proof('stop-tamper')
    assert execution.calls == 1 and renderer.ledger.read_bytes() == before


def test_unconfirmed_cleanup_cannot_be_changed_into_not_created(adapter):
    renderer, execution = adapter
    execution.failure = 'cleanup'
    assert renderer.render('uncertain-cleanup', SOURCE, document()) is None
    root = renderer._directory('uncertain-cleanup')
    raw = json.loads((root / 'render-outcome.json').read_bytes())
    raw.update(status='failed', container_id=None, cleanup=dict(status='not_created', container_id=None))
    (root / 'render-outcome.json').write_bytes(canonical_bytes(raw))
    before = renderer.ledger.read_bytes()
    with pytest.raises(ServiceError, match='proof_changed'):
        renderer.stopping_proof('uncertain-cleanup')
    assert execution.calls == 1 and renderer.ledger.read_bytes() == before
