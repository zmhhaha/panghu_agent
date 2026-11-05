"""Real owner-scoped API/SQLite integration; document fetch uses explicit fixtures."""
from copy import deepcopy
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.scientific_workspace import create_router
from scientific_agent import official_sources
from scientific_agent.workflow_contracts import digest
from test_official_sources import repo_fixture, COMMIT
from test_workspace import setup, create, action


URL = 'https://github.com/example/project'


@pytest.fixture
def client(setup):
    _, service, _ = setup
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as value:
        yield value


@pytest.fixture
def fetched(monkeypatch):
    # The existing importer, deterministic GitHub response bytes and exact
    # commit are exercised here. These are not live public retrieval results.
    material = official_sources.fetch_official_material(URL, transport=repo_fixture())
    calls = []
    def retrieve(url):
        calls.append(url)
        return deepcopy(material)
    monkeypatch.setattr(official_sources, 'fetch_official_material', retrieve)
    return material, calls


def post(client, project, *, key='url-import', revision=None, url=URL, headers=None, extra=None):
    payload = dict(expected_revision=project['revision'] if revision is None else revision, url=url)
    payload.update(extra or {})
    actual_headers = dict(headers or {})
    if key is not None:
        actual_headers['Idempotency-Key'] = key
    return client.post('/api/v1/research/projects/' + project['id'] + '/materials/url',
                       json=payload, headers=actual_headers)


def code(response):
    return response.json()['detail']['code']


def test_url_import_api_stores_acquired_document_metadata_without_counting_papers(setup, client, fetched):
    store, _, _ = setup
    material, calls = fetched
    project = create(store)
    response = post(client, project)
    assert response.status_code == 200, response.text
    current = response.json()
    assert calls == [URL] and len(current['materials']) == 1
    visible = current['materials'][0]
    assert visible['material_id'] == material['material_id']
    assert visible['retrieval']['github']['commit'] == COMMIT
    assert visible['file_status'] == 'read' and visible['access_level'] == 'full_text'
    assert 'text' not in visible and 'Install the declared dependencies.' in visible['text_preview']
    assert current['materials_summary']['paper_count'] == 0
    assert current['materials_summary']['document_count'] == 2
    raw = store.raw('alice', project['id'])
    assert raw['materials'] == [material]
    assert any(event['action'] == 'official_material_added' for event in raw['history'])
    assert current['input_revision'] == project['input_revision'] + 1


@pytest.mark.parametrize('owner,status', [('bob', 404), ('', 401)])
def test_other_owner_or_missing_identity_cannot_fetch_or_observe_source(setup, client, fetched, owner, status):
    store, _, _ = setup
    _, calls = fetched
    project = create(store)
    before = store.raw('alice', project['id'])
    response = post(client, project, headers={'X-Forwarded-User': owner})
    assert response.status_code == status
    assert calls == [] and store.raw('alice', project['id']) == before


def test_unknown_project_is_rejected_before_fetch(setup, client, fetched):
    _, calls = fetched
    response = post(client, {'id': str(uuid4()), 'revision': 0})
    assert response.status_code == 404 and calls == []


def test_stale_revision_does_not_spend_a_document_request(setup, client, fetched):
    store, _, _ = setup
    _, calls = fetched
    original = create(store)
    store.patch('alice', original['id'], dict(expected_revision=original['revision'],
        title='A concurrent update'), 'change-title')
    before = store.raw('alice', original['id'])
    response = post(client, original)
    assert response.status_code == 409 and code(response) == 'project_revision_conflict'
    assert calls == [] and store.raw('alice', original['id']) == before


@pytest.mark.parametrize('key', [None, 'not a valid key'])
def test_invalid_idempotency_key_is_rejected_before_fetch(setup, client, fetched, key):
    store, _, _ = setup
    _, calls = fetched
    project = create(store)
    before = store.raw('alice', project['id'])
    response = post(client, project, key=key)
    assert response.status_code == 422 and code(response) == 'idempotency_key_required'
    assert calls == [] and store.raw('alice', project['id']) == before


def test_exact_replay_uses_original_revision_and_never_fetches_again(setup, client, fetched):
    store, _, _ = setup
    material, calls = fetched
    original = create(store)
    first = post(client, original)
    assert first.status_code == 200, first.text
    second = post(client, original)
    assert second.status_code == 200 and second.json() == first.json()
    assert calls == [URL]
    assert len(store.raw('alice', original['id'])['materials']) == 1
    assert second.json()['materials'][0]['material_id'] == material['material_id']


def test_replay_after_later_edits_keeps_latest_project_and_does_not_fetch(setup, client, fetched):
    store, _, _ = setup
    _, calls = fetched
    original = create(store)
    first = post(client, original)
    assert first.status_code == 200, first.text
    current = first.json()
    current = store.patch('alice', current['id'], dict(expected_revision=current['revision'],
        title='Title saved after import'), 'later-title')
    replayed = post(client, original)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()['title'] == current['title']
    assert replayed.json()['revision'] == current['revision']
    assert calls == [URL] and len(replayed.json()['materials']) == 1


def test_same_key_with_another_url_is_rejected_before_second_fetch(setup, client, fetched):
    store, _, _ = setup
    _, calls = fetched
    original = create(store)
    assert post(client, original).status_code == 200
    before = store.raw('alice', original['id'])
    response = post(client, original, url='https://docs.example.org/another')
    assert response.status_code == 409 and code(response) == 'idempotency_conflict'
    assert calls == [URL] and store.raw('alice', original['id']) == before


def test_same_key_with_changed_payload_revision_is_a_conflict(setup, client, fetched):
    store, _, _ = setup
    _, calls = fetched
    original = create(store)
    current = post(client, original).json()
    response = post(client, current)
    assert response.status_code == 409 and code(response) == 'idempotency_conflict'
    assert calls == [URL]


def test_key_from_another_operation_cannot_be_reused_for_url_fetch(setup, client, fetched):
    store, _, _ = setup
    _, calls = fetched
    project = create(store)
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        title='A saved edit'), 'shared-key')
    response = post(client, project, key='shared-key')
    assert response.status_code == 409 and code(response) == 'idempotency_conflict'
    assert calls == []


@pytest.mark.parametrize('extra', [{'token': 'not-used'}, {'allow_private': True}, {'raw': 'not-used'}])
def test_extra_payload_fields_cannot_change_source_policy(setup, client, fetched, extra):
    store, _, _ = setup
    _, calls = fetched
    project = create(store)
    before = store.raw('alice', project['id'])
    response = post(client, project, extra=extra)
    assert response.status_code == 422 and calls == []
    assert store.raw('alice', project['id']) == before


def test_fetch_failure_does_not_create_material_receipt_or_false_acquisition(setup, client, fetched, monkeypatch):
    store, _, _ = setup
    _, calls = fetched
    project = create(store)
    before = store.raw('alice', project['id'])
    def unavailable(url):
        calls.append(url)
        raise official_sources.OfficialSourceError('official_source_unavailable', 502, retryable=True)
    monkeypatch.setattr(official_sources, 'fetch_official_material', unavailable)
    response = post(client, project)
    assert response.status_code == 502 and code(response) == 'official_source_unavailable'
    assert response.json()['detail']['retryable'] is True
    assert calls == [URL] and store.raw('alice', project['id']) == before
    assert not before['materials'] and 'url-import' not in before['_receipts']


def test_explicit_retry_after_failed_fetch_can_use_same_uncommitted_key(setup, client, fetched, monkeypatch):
    store, _, _ = setup
    material, calls = fetched
    project = create(store)
    def fail_once(url):
        calls.append(url)
        if len(calls) == 1:
            raise official_sources.OfficialSourceError('official_source_timeout', 504, retryable=True)
        return deepcopy(material)
    monkeypatch.setattr(official_sources, 'fetch_official_material', fail_once)
    assert post(client, project).status_code == 504
    response = post(client, project)
    assert response.status_code == 200, response.text
    assert calls == [URL, URL] and len(response.json()['materials']) == 1


def test_project_material_limit_is_checked_before_fetch(setup, client, fetched):
    store, _, _ = setup
    material, calls = fetched
    project = create(store)
    def fill(document):
        document['materials'] = [dict(deepcopy(material), material_id=str(uuid4())) for _ in range(30)]
    store.internal('alice', project['id'], fill)
    current = store.get('alice', project['id'])
    before = store.raw('alice', project['id'])
    response = post(client, current)
    assert response.status_code == 409 and code(response) == 'project_material_limit'
    assert calls == [] and store.raw('alice', project['id']) == before


def test_edit_during_fetch_wins_without_import_overwriting_new_state(setup, client, fetched, monkeypatch):
    store, _, _ = setup
    material, calls = fetched
    project = create(store)
    def concurrently_edited(url):
        calls.append(url)
        store.patch('alice', project['id'], dict(expected_revision=project['revision'],
            title='Concurrent edit preserved'), 'concurrent-title')
        return deepcopy(material)
    monkeypatch.setattr(official_sources, 'fetch_official_material', concurrently_edited)
    response = post(client, project)
    assert response.status_code == 409 and code(response) == 'project_revision_conflict'
    saved = store.raw('alice', project['id'])
    assert saved['title'] == 'Concurrent edit preserved' and not saved['materials']
    assert 'url-import' not in saved['_receipts'] and calls == [URL]


def test_repository_body_and_commit_enter_immutable_context_without_changing_other_materials(setup, client, fetched):
    store, _, _ = setup
    imported, calls = fetched
    project = create(store)
    project = store.add_material('alice', project['id'], project['revision'], 'original-upload',
        filename='existing-notes.md', raw=b'# Existing material\nPreserve these exact existing bytes.\n')
    existing = deepcopy(store.raw('alice', project['id'])['materials'][0])
    response = post(client, project)
    assert response.status_code == 200, response.text
    project = response.json()
    stored = store.raw('alice', project['id'])
    assert stored['materials'][0] == existing and stored['materials'][1] == imported
    project = action(store, project, 'plan')
    job = store.raw('alice', project['id'])['jobs'][-1]
    frozen = deepcopy(job['frozen_context'])
    assert frozen['materials'][0]['text'] == existing['text']
    assert frozen['materials'][1]['text'] == imported['text']
    assert 'Install the declared dependencies.' in frozen['materials'][1]['text']
    assert 'name = "project"' in frozen['materials'][1]['text']
    assert COMMIT in frozen['materials'][1]['text']
    assert frozen['materials'][1]['sha256'] == imported['sha256']
    assert digest(frozen) == job['task']['context_sha256']
    updated = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        title='Later title edit'), 'later-context-check')
    after = store.raw('alice', updated['id'])
    assert after['jobs'][-1]['frozen_context'] == frozen
    assert after['materials'][0] == existing and calls == [URL]
