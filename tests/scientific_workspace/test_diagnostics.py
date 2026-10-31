"""Correlation, redaction, observed timing and recoverable save failures."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.scientific_workspace import create_router
from scientific_agent.workspace_diagnostics import diagnostic
from scientific_agent.pi_client import WorkerError
from test_workspace import setup, create, action, finish, outlined, drafted


@pytest.mark.parametrize('code,category', [
    ('runtime_idle_timeout', 'timeout'),
    ('unready_materials_must_be_excluded', 'source_failed'),
    ('model_error', 'generation_failed'),
    ('invalid_workflow_evidence', 'validation_failed'),
    ('workflow_evidence_source_mismatch', 'validation_failed'),
    ('workspace_storage_unavailable', 'save_failed'),
])
def test_failures_have_distinct_reviewed_categories(code, category):
    job = dict(id=str(uuid4()), state='failed', error_code=code,
        context='PRIVATE_CONTEXT', raw_error='PRIVATE_PROVIDER_ERROR',
        api_key='PRIVATE_CREDENTIAL', remote_job_id=str(uuid4()))
    value = diagnostic(job)
    assert value['category'] == category and value['code'] == code
    assert value['reason'] and value['step'] and value['suggested_action']
    assert type(value['retryable']) is bool
    assert value['correlation_id'] == job['id']
    assert value['remote_job_id'] == job['remote_job_id']
    assert 'PRIVATE_' not in json.dumps(value)


def test_unknown_native_text_never_becomes_diagnostic_code_or_reason():
    value = diagnostic(dict(id=str(uuid4()), state='failed', error_code='PRIVATE_API_KEY'))
    assert value['code'] == 'unknown_failure'
    assert 'PRIVATE_' not in json.dumps(value)


def test_queue_and_progress_use_events_not_poll_time(setup):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    service.reconcile('alice', project['id'])
    job = store.raw('alice', project['id'])['jobs'][-1]
    remote = deepcopy(agent.jobs[job['remote_job_id']])
    remote['event_cursor'] = 3
    events = [dict(seq=1, type='job.queued', created_at=1793404800),
              dict(seq=2, type='job.running', created_at=1793404810),
              dict(seq=3, type='engine.progress', created_at=1793404820)]
    original = service._request
    service._request = lambda method, path, *a, **kw: (
        {'items': events} if '/events?' in path else original(method, path, *a, **kw))
    service._observe('alice', project['id'], job, remote)
    first = store.get('alice', project['id'])['jobs'][-1]
    assert first['diagnostic']['queued_at'] == '2026-10-31T00:00:00Z'
    assert first['diagnostic']['started_at'] == '2026-10-31T00:00:10Z'
    assert first['diagnostic']['last_progress_at'] == '2026-10-31T00:00:20Z'
    revision_before = store.get('alice', project['id'])['revision']
    service._observe('alice', project['id'], first, remote)
    assert store.get('alice', project['id'])['revision'] == revision_before
    assert diagnostic(dict(id=str(uuid4()), state='queued'))['started_at'] is None


@pytest.mark.parametrize('start_sequence', [2, 120, None])
def test_completed_long_event_history_reads_tail_and_keeps_unobserved_start_unknown(setup, start_sequence):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    service.reconcile('alice', project['id'])
    task_id = store.raw('alice', project['id'])['active_job_id']
    agent.complete('alice', project['id'], task_id)
    job = store.raw('alice', project['id'])['jobs'][-1]
    remote = agent.jobs[job['remote_job_id']]
    remote['event_cursor'] = 236
    base = 1793404800
    events = [dict(seq=seq, type='job.queued' if seq == 1 else 'job.running'
        if seq == start_sequence else 'job.completed' if seq == 236 else 'engine.progress',
        created_at=base + seq) for seq in range(1, 237)]
    requested = []
    original = service._request
    def request(method, path, *args, **kwargs):
        if '/events?' not in path:
            return original(method, path, *args, **kwargs)
        after = int(parse_qs(urlsplit(path).query)['after'][0])
        requested.append(after)
        return dict(items=[event for event in events if event['seq'] > after][:100])
    service._request = request
    service.reconcile('alice', project['id'])
    saved = store.get('alice', project['id'])['jobs'][-1]
    stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')
    assert requested == [0, 136]  # Two bounded pages, despite 236 recorded events.
    assert saved['state'] == 'completed' and saved['delivery_ack'] is True
    assert saved['diagnostic']['finished_at'] == stamp(base + 236)
    assert saved['diagnostic']['last_progress_at'] == stamp(base + 236)
    assert saved['diagnostic']['started_at'] == (stamp(base + 2) if start_sequence == 2 else None)
    assert saved['observed_event_cursor'] == 236
    assert agent.posts == agent.acks == 1


@pytest.mark.parametrize('failure', ['tail_request', 'timing_save'])
def test_diagnostic_timing_unavailability_does_not_block_result_reception(setup, monkeypatch, failure):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    service.reconcile('alice', project['id'])
    job = store.raw('alice', project['id'])['jobs'][-1]
    agent.complete('alice', project['id'], job['id'])
    agent.jobs[job['remote_job_id']]['event_cursor'] = 236
    original_request = service._request
    def request(method, path, *args, **kwargs):
        if '/events?' not in path:
            return original_request(method, path, *args, **kwargs)
        after = int(parse_qs(urlsplit(path).query)['after'][0])
        if failure == 'tail_request' and after:
            raise WorkerError(503, 'agent_service_unavailable', 'Synthetic event endpoint failure')
        return dict(items=[dict(seq=seq, type='engine.progress', created_at=1793404800 + seq)
                           for seq in range(after + 1, min(after + 101, 237))])
    monkeypatch.setattr(service, '_request', request)
    if failure == 'timing_save':
        original_set = service._set_job
        def set_job(*args, **kwargs):
            if 'observed_event_cursor' in kwargs:
                raise RuntimeError('Synthetic diagnostic metadata save failure')
            return original_set(*args, **kwargs)
        monkeypatch.setattr(service, '_set_job', set_job)
    service.reconcile('alice', project['id'])
    saved = store.get('alice', project['id'])
    assert saved['jobs'][-1]['state'] == 'completed'
    assert saved['jobs'][-1]['delivery_ack'] is True and saved['plan'] is not None
    assert saved['jobs'][-1]['diagnostic']['finished_at'] is None
    assert agent.posts == agent.acks == 1


def test_diagnostic_download_is_owned_and_redacted(setup):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    task_id = project['jobs'][-1]['id']
    app = FastAPI()
    app.include_router(create_router(service))
    client = TestClient(app)
    path = '/api/v1/research/projects/' + project['id'] + '/jobs/' + task_id + '/diagnostic'
    assert client.get(path, headers={'X-Forwarded-User': 'bob'}).status_code == 404
    response = client.get(path, headers={'X-Forwarded-User': 'alice'})
    assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
    assert response.json()['task_id'] == task_id
    assert 'frozen_context' not in response.text and 'request' not in response.json()


def test_public_project_and_download_never_expose_unreviewed_native_error_text(setup):
    store, service, _ = setup
    project = action(store, create(store), 'plan')
    task_id = project['jobs'][-1]['id']
    private = 'PRIVATE_API_KEY https://internal.invalid/private-provider-error'
    service._set_job('alice', project['id'], task_id, state='failed', error_code=private)
    app = FastAPI()
    app.include_router(create_router(service))
    client = TestClient(app, headers={'X-Forwarded-User': 'alice'})
    project_path = '/api/v1/research/projects/' + project['id']
    public = client.get(project_path)
    assert public.status_code == 200 and 'PRIVATE_API_KEY' not in public.text
    assert 'internal.invalid' not in public.text
    assert public.json()['jobs'][-1]['error_code'] == 'unknown_failure'
    assert public.json()['jobs'][-1]['diagnostic']['code'] == 'unknown_failure'
    download = client.get(project_path + '/jobs/' + task_id + '/diagnostic')
    assert download.status_code == 200 and 'PRIVATE_API_KEY' not in download.text
    assert 'internal.invalid' not in download.text
    assert download.json()['diagnostic']['code'] == 'unknown_failure'
    assert store.raw('alice', project['id'])['jobs'][-1]['error_code'] == private


def test_valid_output_save_failure_remains_receivable_then_recovers(setup, monkeypatch):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    service.reconcile('alice', project['id'])
    job = store.raw('alice', project['id'])['jobs'][-1]
    agent.complete('alice', project['id'], job['id'])
    original = service._ingest
    monkeypatch.setattr(service, '_ingest', lambda *a: (_ for _ in ()).throw(RuntimeError('PRIVATE_SQL_URL')))
    service.reconcile('alice', project['id'])
    saved = store.get('alice', project['id'])['jobs'][-1]
    assert saved['state'] == 'receiving'
    assert saved['diagnostic']['category'] == 'save_failed'
    assert agent.acks == 0 and store.get('alice', project['id'])['plan'] is None
    monkeypatch.setattr(service, '_ingest', original)
    service.reconcile('alice', project['id'])
    assert store.get('alice', project['id'])['jobs'][-1]['state'] == 'completed'
    assert agent.posts == 1 and agent.acks == 1


@pytest.mark.parametrize('stage', ['plan', 'draft'])
def test_committed_result_lost_save_response_does_not_publish_twice(setup, monkeypatch, stage):
    store, service, agent = setup
    project = action(store, create(store), 'plan') if stage == 'plan' else action(
        store, outlined(setup), 'confirm_evidence')
    service.reconcile('alice', project['id'])
    task_id = store.raw('alice', project['id'])['active_job_id']
    agent.complete('alice', project['id'], task_id)
    original = store._cas
    lost = False
    def commit_then_lose_response(previous, changed):
        nonlocal lost
        original(previous, changed)
        job = next(item for item in changed['jobs'] if item['id'] == task_id)
        if not lost and job['state'] == 'completed':
            lost = True
            raise ConnectionResetError('Commit succeeded but response was lost')
    monkeypatch.setattr(store, '_cas', commit_then_lose_response)
    service.reconcile('alice', project['id'])
    saved = store.raw('alice', project['id'])
    job = next(item for item in saved['jobs'] if item['id'] == task_id)
    assert lost and job['state'] == 'completed' and job['delivery_ack'] is True
    assert len(saved['plan_history']) == 1
    assert len(saved['versions']) == (1 if stage == 'draft' else 0)
    original_version = saved['current_version_id']
    original_plan = saved['plan']['id']
    posts, acks = agent.posts, agent.acks
    service.reconcile('alice', project['id'])
    recovered = store.raw('alice', project['id'])
    assert recovered['plan']['id'] == original_plan
    assert recovered['current_version_id'] == original_version
    assert len(recovered['plan_history']) == 1
    assert len(recovered['versions']) == (1 if stage == 'draft' else 0)
    assert (agent.posts, agent.acks) == (posts, acks)


def test_evidence_history_http_exports_and_owner_isolation(setup):
    store, service, _ = setup
    project = outlined(setup)
    old_id = project['evidence']['id']
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        selections=[dict(source_id=item['source_id'], included=False, reason='Narrow scope')
                    for item in project['selections']]), str(uuid4()))
    app = FastAPI()
    app.include_router(create_router(service))
    client = TestClient(app)
    path = '/api/v1/research/projects/' + project['id'] + '/evidence/' + old_id
    assert client.get(path, headers={'X-Forwarded-User': 'bob'}).status_code == 404
    result = client.get(path, headers={'X-Forwarded-User': 'alice'}).json()
    assert result['read_only'] is True and result['stale'] is True
    assert 'snapshot' not in result and result['source_lineage']['selections']
    for format in ('json', 'markdown'):
        response = client.get(path + '/download?format=' + format,
                              headers={'X-Forwarded-User': 'alice'})
        assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
