"""Knowledge policy is frozen across consumers, restarts and old ledgers."""
from copy import deepcopy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_laboratory_library import (
    LaboratoryLibraryAdapter, make_laboratory_library_adapter,
)
from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter
from video_workbench.joint_plan_store import JointPlanStore
from video_workbench.app import create_app
from video_workbench.store import Store
from test_animation_wikiskill_production import adapter, release
from test_production_runs import OWNER, seed


def test_disabled_never_resolves_publication_and_survives_restart_import_and_retry(tmp_path):
    original = release()
    calls, state = [], dict(active=original, recent={original['id']: original})
    service = adapter(tmp_path / 'local', state, calls)
    binding = service.pin('owner', 'disabled-batch', use_wikiskill=False)
    assert binding['use_wikiskill'] is False and binding['release_id'] is None
    delivery = service.freeze('owner', 'planner', {'goal': 'Draw freely.'}, ['html-svg'], library_binding=binding)
    assert delivery['catalog']['skills'] == [] and delivery['packages'] == {} and calls == []
    assert delivery['retrieval_receipts'][0]['library_binding'] == binding
    # Importing a disabled child must not query even an available active release.
    remote = LibrarySkillProductionAdapter(lambda *_: pytest.fail('Disabled must not browse'),
        lambda *_: pytest.fail('Disabled must not read releases'), None, tmp_path / 'remote' / 'ledger.sqlite3',
        import_release=lambda *_: pytest.fail('Disabled must not import release bytes'))
    assert remote.pin_import('owner', binding) == binding
    child = remote.freeze('owner', 'recipe', {}, ['html-svg'], library_binding=binding)
    assert child['packages'] == {} and child['catalog']['skills'] == []
    reopened = adapter(tmp_path / 'local', state, calls)
    assert reopened.pin('owner', 'disabled-batch', use_wikiskill=False) == binding
    assert reopened.freeze('owner', 'planner', {'goal': 'Draw freely.'}, ['html-svg'], library_binding=binding) == delivery
    with pytest.raises(ServiceError, match='frozen_input_changed'):
        reopened.pin('owner', 'disabled-batch')
    assert calls == []


def test_enabled_empty_and_disabled_are_distinct_immutable_policies(tmp_path):
    state, calls = dict(active=None, recent={}), []
    service = adapter(tmp_path, state, calls)
    enabled = service.pin('owner', 'empty-but-enabled')
    disabled = service.pin('owner', 'disabled', use_wikiskill=False)
    assert enabled['use_wikiskill'] is True and disabled['use_wikiskill'] is False
    assert enabled['release_id'] is disabled['release_id'] is None
    state['active'] = release()
    assert service.freeze('owner', 'enabled-child', {}, ['html-svg'], library_binding=enabled)['packages'] == {}
    assert calls == [('active', 'owner')]


@pytest.mark.parametrize('policy', [None, 0, 1, 'false', [], {}])
def test_non_boolean_and_conflicting_policy_is_rejected_before_publication_reads(tmp_path, policy):
    service = adapter(tmp_path, dict(active=release(), recent={}), [])
    with pytest.raises(ServiceError, match='policy_invalid'):
        service.pin('owner', 'bad-policy', use_wikiskill=policy)
    with pytest.raises(ServiceError, match='policy_conflict'):
        service.pin('owner', 'contradiction', release()['id'], use_wikiskill=False)


def test_historical_five_field_binding_and_exact_ledger_bytes_remain_readable(tmp_path):
    original = release()
    state, calls = dict(active=original, recent={original['id']: original}), []
    service = adapter(tmp_path, state, calls)
    binding = service.pin('owner', 'legacy-batch')
    binding.pop('use_wikiskill')
    record = dict(binding=binding, release=original)
    raw = canonical_bytes(record)
    with sqlite3.connect(service.ledger_path) as db:
        db.execute('UPDATE animation_wikiskill_batch_freezes SET record=?,sha256=?', (raw, digest(record)))
    assert service.pin('owner', 'legacy-batch') == binding
    assert 'use_wikiskill' not in service.pin('owner', 'legacy-batch', use_wikiskill=True)
    result = service.freeze('owner', 'legacy-child', {}, ['html-svg'], library_binding=binding)
    assert result['packages'] and result['retrieval_receipts'][0]['library_binding'] == binding
    with sqlite3.connect(service.ledger_path) as db:
        assert db.execute('SELECT record FROM animation_wikiskill_batch_freezes').fetchone()[0] == raw


def _laboratory(tmp_path, state):
    return LaboratoryLibraryAdapter(lambda owner: deepcopy(state['active']),
        lambda owner, identity: deepcopy(state['recent'][identity]), lambda owner: [], tmp_path / 'ledger.sqlite3')


def test_laboratory_reads_complete_published_files_not_mutable_wiki_and_is_restart_stable(tmp_path):
    original = release(count=1)
    old = original['packages'][0]
    files = dict(old['files'], **{'references/material.md': 'A brush principle.\n' * 2400,
        'scripts/optional.js': ' '.ljust(16020) + '\n// Original source.'})
    original['packages'] = [build_skill_package(skill_id=old['skill_id'], version=old['version'],
        kind=old['kind'], files=files, runtime_requirements=old['runtime_requirements'])]
    original['sha256'] = digest({key: value for key, value in original.items() if key != 'sha256'})
    state = dict(active=original, recent={original['id']: original})
    service = _laboratory(tmp_path, state)
    binding = service.pin('owner', 'lab')
    snapshot = service.laboratory_snapshot('owner', binding)
    assert snapshot['experiences'][0]['document']['source'] == 'published-release-inline'
    recovered = {}
    for page in snapshot['pages']:
        metadata = json.loads(page['body']['applicability'][1])
        recovered.setdefault(metadata['path'], []).append((metadata['offset'], page['body']['content'].split('\n', 1)[1]))
    assert {path: ''.join(chunk for _, chunk in sorted(parts)) for path, parts in recovered.items()} == files
    state['active'] = release(2)
    assert _laboratory(tmp_path, state).laboratory_snapshot('owner', binding) == snapshot
    disabled = service.pin('owner', 'no-knowledge', use_wikiskill=False)
    empty = service.laboratory_snapshot('owner', disabled)
    assert empty['pages'] == empty['experiences'] == []


def test_inline_release_over_protocol_budget_fails_without_truncation(tmp_path, monkeypatch):
    service = _laboratory(tmp_path, dict(active=release(), recent={}))
    binding = service.pin('owner', 'oversized')
    monkeypatch.setattr('video_workbench.animation_laboratory_library.INPUT_MAX_BYTES', 200)
    with pytest.raises(ServiceError, match='laboratory_library_too_large'):
        service.laboratory_snapshot('owner', binding)


def test_factory_reads_shared_publication_database_without_working_knowledge(tmp_path):
    service = make_laboratory_library_adapter(tmp_path)
    assert service.ledger_path == (tmp_path / 'wikiskill-consumption.sqlite3').absolute()
    assert (tmp_path / 'wikiskill.sqlite3').is_file()
    assert not (tmp_path / 'knowledge.sqlite3').exists()
    binding = service.pin('owner', 'new-request')
    assert binding['use_wikiskill'] is True and binding['release_id'] is None
    assert service.laboratory_snapshot('owner', binding)['pages'] == []
    # A separate publisher connection writes the same immutable release store;
    # a newly constructed laboratory reader sees it without touching live Wiki.
    original = release()
    with sqlite3.connect(tmp_path / 'wikiskill.sqlite3') as db:
        db.execute('INSERT INTO wikiskill_releases VALUES(?,?,?,?,?)',
            (original['id'], original['owner'], original['sequence'], original['job_id'],
                canonical_bytes(original).decode('utf-8')))
        db.execute('INSERT INTO wikiskill_active VALUES(?,?,?)',
            (original['owner'], original['id'], original['created_at']))
    other_process = make_laboratory_library_adapter(tmp_path)
    published = other_process.pin(original['owner'], 'after-publication')
    assert published['release_id'] == original['id']
    assert other_process.laboratory_snapshot(original['owner'], published)['pages']
    assert service.laboratory_snapshot('owner', binding)['pages'] == []


def test_planning_default_replay_and_explicit_true_are_identical_and_disable_is_frozen(tmp_path):
    original = release()
    state, calls = dict(active=original, recent={original['id']: original}), []
    service = adapter(tmp_path / 'library', state, calls)
    store = Store(tmp_path / 'business.db')
    joint = JointPlanStore(store, skill_adapter=service)
    project = seed(store, joint)
    request = dict(expected_revision=project['revision'], brief=dict(audience='', goal='', tone='precise'))
    owner = OWNER
    public = joint.start(owner, project['id'], request, 'default')
    assert joint.start(owner, project['id'], dict(request, use_wikiskill=True), 'default') == public
    joint.cancel(owner, public['id'])
    joint.update_operation(public['id'], status='cancelled')
    disabled = joint.start(owner, project['id'], dict(request, use_wikiskill=False,
        expected_revision=store.project(owner, project['id'])['revision']), 'disabled')
    assert disabled['library_binding']['use_wikiskill'] is False
    prepared = joint.prepare(disabled['id'], service)
    assert prepared['document']['skill_catalog']['skills'] == []
    assert service.get_bundle(owner, 'video-joint-plan:' + disabled['id'],
        prepared['document']['skill_catalog']['sha256'])['packages'] == {}
    assert calls == [('active', owner)]


def test_conflicting_disabled_plan_rejected_before_pin_or_operation(tmp_path):
    state, calls = dict(active=release(), recent={}), []
    service = adapter(tmp_path / 'library', state, calls)
    store = Store(tmp_path / 'business.db')
    joint = JointPlanStore(store, skill_adapter=service)
    project = seed(store, joint)
    with pytest.raises(ServiceError, match='policy_conflict'):
        joint.start(OWNER, project['id'], dict(expected_revision=project['revision'],
            brief=dict(audience='', goal='', tone='precise'), use_wikiskill=False,
            library_release_id=state['active']['id']), 'contradictory')
    assert calls == []
    with store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM joint_plan_operations').fetchone()[0] == 0


def test_http_boolean_and_conflict_rejection_do_not_create_hidden_work(tmp_path):
    from test_joint_plan_api import Gateway
    gateway = Gateway()
    app = create_app(db_path=tmp_path / 'http.db', local_mode=True, background=False,
        joint_plan_gateway=gateway, joint_plan_enabled=True, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        project = client.post('/api/projects', json=dict(title='Knowledge policy', script='A clear explanation.')).json()
        url = f'/api/projects/{project["id"]}/joint-plan'
        request = dict(expected_revision=project['revision'], brief=dict(audience='', goal='', tone='precise'))
        for policy in ('false', 0, None):
            assert client.post(url, json=dict(request, use_wikiskill=policy),
                headers={'Idempotency-Key': 'bad-policy'}).status_code == 422
        assert client.post(url, json=dict(request, use_wikiskill=False, library_release_id=release()['id']),
            headers={'Idempotency-Key': 'conflicting-policy'}).status_code == 422
        result = client.post(url, json=dict(request, use_wikiskill=False),
            headers={'Idempotency-Key': 'disabled-policy'})
        assert result.status_code == 202, result.text
        assert result.json()['library_binding']['use_wikiskill'] is False
        assert gateway.uploads == gateway.submits == 0


def test_disabled_formal_manim_child_carries_policy_and_retains_platform_contract(tmp_path):
    from test_manim_film_resource_transport import setup, prepared_request, OWNER as RESOURCE_OWNER
    calls = []
    parent = adapter(tmp_path / 'workbench', dict(active=release(), recent={}), calls)
    binding = parent.pin(RESOURCE_OWNER, 'disabled-root', use_wikiskill=False)
    service, gateway, executor = setup(tmp_path / 'service')
    remote = LibrarySkillProductionAdapter(lambda *_: pytest.fail('Never choose active'),
        lambda *_: pytest.fail('Never read published bytes'), None, tmp_path / 'creation' / 'library.sqlite3')
    service.coordinator.skill_adapter = remote
    request = prepared_request(library_binding=binding)
    service.start(RESOURCE_OWNER, request)
    service.advance(RESOURCE_OWNER, request['operation_id'])
    saved = service.coordinator._get(RESOURCE_OWNER, 'film-' + request['operation_id'])
    assert saved['input']['skill_catalog']['skills'] == [] and calls == []
    assert saved['input']['resource_constraints']['transparent'] is True
    assert saved['input']['resource_constraints']['fps'] == 24
    assert gateway.submissions == executor.calls == 0
    delivery = remote.get_bundle(RESOURCE_OWNER, saved['input']['skill_catalog']['consumer_id'],
        saved['input']['skill_catalog']['sha256'])
    assert delivery['packages'] == {} and delivery['retrieval_receipts'][0]['library_binding'] == binding
