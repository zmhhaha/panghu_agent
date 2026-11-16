"""Explicit library separation never creates or rewrites animation evidence."""
from copy import deepcopy
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_effect_identity import AnimationEffectIdentity, SCHEMA
from test_animation_method_http import (
    serving, CreationHTTPServer, TOKEN, MethodLabClient, CreationTransportError)
from test_animation_method_service import work, start, finish


def identity(service):
    return getattr(service, 'effect_identity', None) or AnimationEffectIdentity(service)


def request(**changes):
    return dict(request_id=str(uuid4()), title='A different complete effect',
        reason='The subject and expression goal changed.', **changes)


def ledger(service):
    with sqlite3.connect(service.commissions.path) as db:
        return list(db.execute('SELECT id,state,document FROM animation_method_commissions ORDER BY rowid'))


def test_separation_is_an_audited_idempotent_identity_record_without_new_work(work):
    service, creator, reviewer, renderer = work
    original = finish(service, start(service, title='Original subject')['id'])
    child = finish(service, start(service, base_commission_id=original['id'], title='New subject')['id'])
    store = identity(service)
    body = request()
    before = ledger(service)
    media = service.artifact('developer', child['id'], child['best_candidate_id'], 'media')
    calls = (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))

    record = store.separate('developer', child['id'], body)
    assert record['schema'] == SCHEMA
    assert record['commission_id'] == child['id']
    assert record['source_effect_id'] == original['id']
    assert record['source_parent_id'] == original['id']
    assert record['commission_frozen_sha256'] == service.commissions.get('developer', child['id'])['frozen_sha256']
    assert all(record[key] == value for key, value in body.items())
    assert store.separate('developer', child['id'], deepcopy(body)) == record
    assert store.records('developer') == [record]
    assert store.records('another-owner') == []
    detail = service.get('developer', child['id'])
    own = service.commissions.get('developer', child['id'])['candidates']
    assert detail['title'] == body['title']
    assert detail['execution_title'] == child['title']
    assert {row['id'] for row in detail['candidates']} == {row['id'] for row in own}
    assert detail['baseline_candidate'] is None
    assert detail['creation_reference']['commission_id'] == original['id']
    assert detail['creation_reference']['candidate']['id'] == original['best_candidate_id']
    assert detail['effect']['separation']['commission_id'] == child['id']
    assert detail['effect']['separation']['source_effect_id'] == original['id']
    original_detail = service.get('developer', original['id'])
    assert original_detail['title'] == original['title']
    assert 'creation_reference' not in original_detail and 'execution_title' not in original_detail
    assert ledger(service) == before
    assert service.artifact('developer', child['id'], child['best_candidate_id'], 'media') == media
    assert calls == (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))
    for changed in (dict(body, title='Different title'), dict(body, request_id=str(uuid4()))):
        with pytest.raises(ServiceError, match='animation_effect_separation_conflict'):
            store.separate('developer', child['id'], changed)
    assert store.records('developer') == [record]


def test_continuation_inside_separated_effect_keeps_its_same_effect_baseline(work):
    service, creator, reviewer, renderer = work
    original = finish(service, start(service)['id'])
    anchor = finish(service, start(service, base_commission_id=original['id'])['id'])
    body = request()
    identity(service).separate('developer', anchor['id'], body)
    anchor_before = service.commissions.get('developer', anchor['id'])
    calls = (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))

    continuation = start(service, base_commission_id=anchor['id'], title='Improve this distinct effect')
    detail = service.get('developer', continuation['id'])
    assert detail['state'] == 'queued'
    assert detail['effect']['id'] == anchor['id']
    assert detail['effect']['title'] == body['title']
    assert detail['title'] == continuation['title']
    assert detail['baseline_candidate']['id'] == anchor['best_candidate_id']
    assert {row['id'] for row in detail['candidates']} == {anchor['best_candidate_id']}
    assert original['best_candidate_id'] not in {row['id'] for row in detail['candidates']}
    assert 'creation_reference' not in detail and 'execution_title' not in detail
    raw = service.commissions.get('developer', continuation['id'])
    assert raw['frozen']['base_commission_id'] == anchor['id']
    assert raw['frozen']['base_candidate_id'] == anchor['best_candidate_id']
    assert service.commissions.get('developer', anchor['id']) == anchor_before
    assert calls == (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))


def test_separation_requires_visible_owned_completed_continuation_and_own_choice(work):
    service, _, reviewer, _ = work
    original = finish(service, start(service)['id'])
    reviewer.choose_baseline = True
    retained = finish(service, start(service, base_commission_id=original['id'])['id'])
    queued = start(service, base_commission_id=original['id'])
    store = identity(service)
    with pytest.raises(ServiceError, match='animation_method_commission_not_found'):
        store.separate('another-owner', retained['id'], request())
    with pytest.raises(ServiceError, match='animation_effect_separation_requires_continuation'):
        store.separate('developer', original['id'], request())
    for child in (retained, queued):
        with pytest.raises(ServiceError, match='animation_effect_separation_requires_completed_own_sample'):
            store.separate('developer', child['id'], request())
    assert store.records('developer') == []


def test_active_descendant_blocks_separation_but_unrelated_active_sibling_does_not(work):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    anchor = finish(service, start(service, base_commission_id=original['id'])['id'])
    descendant = start(service, base_commission_id=anchor['id'])
    sibling = start(service, base_commission_id=original['id'])
    store = identity(service)
    body = request()
    with pytest.raises(ServiceError, match='animation_effect_separation_branch_active'):
        store.separate('developer', anchor['id'], body)
    assert store.records('developer') == []
    service.commissions.stop('developer', descendant['id'], 'failed', 'Fixture stopped descendant')
    record = store.separate('developer', anchor['id'], body)
    assert record['source_effect_id'] == original['id']
    assert service.commissions.get('developer', sibling['id'])['state'] == 'queued'


def test_uncertain_descendant_operation_is_rejected_without_writing(work, monkeypatch):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    anchor = finish(service, start(service, base_commission_id=original['id'])['id'])
    store = identity(service)
    state = service.trash.state

    def uncertain(db, owner):
        result = deepcopy(state(db, owner))
        result['by_id'][anchor['id']]['operations'][0]['status'] = 'submission_unknown'
        return result

    monkeypatch.setattr(service.trash, 'state', uncertain)
    with pytest.raises(ServiceError, match='animation_effect_separation_branch_active'):
        store.separate('developer', anchor['id'], request())
    assert store.records('developer') == []


@pytest.mark.parametrize('change', [dict(extra='not accepted'), dict(title=''), dict(title='\n'),
    dict(reason=' '), dict(request_id='../outside'), dict(request_id=123)])
def test_separation_body_is_strict_without_changing_library(work, change):
    service, _, _, _ = work
    store = identity(service)
    body = dict(request(), **change)
    with pytest.raises(ServiceError, match='animation_effect_separation_invalid'):
        store.separate('developer', 'unused', body)
    assert store.records('developer') == []


def test_private_separation_route_checks_owner_and_never_wakes_agent_work(work):
    service, creator, reviewer, renderer = work
    original = finish(service, start(service)['id'])
    child = finish(service, start(service, base_commission_id=original['id'])['id'])
    store = identity(service)
    service.effect_identity = store
    notices = []
    backend = CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)
    backend.method_pump = SimpleNamespace(notify=lambda: notices.append('unexpected'), close=lambda: None)
    body = request()
    path = '/api/method-lab/commissions/' + child['id'] + '/separate-effect'
    before = ledger(service)
    calls = (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))
    with serving(backend):
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        with pytest.raises(CreationTransportError) as denied:
            client._request('another-owner', 'POST', path, body)
        assert denied.value.status == 404
        assert store.records('developer') == []
        record = client._request('developer', 'POST', path, body)
        assert record['schema'] == SCHEMA and record['commission_id'] == child['id']
        assert client._request('developer', 'POST', path, body) == record
        effects = client.catalogue('developer')['effects']
        assert {row['id'] for row in effects} == {original['id'], child['id']}
        independent = next(row for row in effects if row['id'] == child['id'])
        assert independent['title'] == body['title']
        assert independent['open_commission_id'] == child['id']
        assert independent['best_candidate']['id'] == child['best_candidate_id']
        assert independent['separation']['source_effect_id'] == original['id']
    assert notices == []
    assert ledger(service) == before
    assert calls == (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))
