"""Deterministic request/background reconciliation races; no network or models."""
import pytest

from scientific_agent.pi_client import WorkerError
from scientific_agent.workspace import WorkspaceError
from test_workspace import setup, action, create, outlined


def running(setup, stage):
    store, service, agent = setup
    if stage == 'plan':
        project = action(store, create(store), 'plan')
    else:
        project = action(store, outlined(setup), 'outline' if stage == 'outline' else 'confirm_evidence')
    service.reconcile('alice', project['id'])
    job = store.raw('alice', project['id'])['jobs'][-1]
    agent.jobs[job['remote_job_id']]['state'] = 'running'
    service._set_job('alice', project['id'], job['id'], state='running')
    return project, job


@pytest.mark.parametrize('stage', ['plan', 'draft'])
@pytest.mark.parametrize('late_response', ['running', 'not_found', 'invalid_candidate'])
def test_late_reconciler_cannot_demote_committed_result(setup, monkeypatch, stage, late_response):
    store, service, agent = setup
    project, job = running(setup, stage)
    before = store.raw('alice', project['id'])
    original_remote, original_candidate = service._remote, service._candidate
    fired = False
    committed = None

    def commit_other_reconciler():
        nonlocal fired, committed
        fired = True
        service.reconcile('alice', project['id'])
        committed = store.raw('alice', project['id'])
        saved = next(row for row in committed['jobs'] if row['id'] == job['id'])
        assert saved['state'] == 'completed' and saved['delivery_ack']

    if late_response == 'invalid_candidate':
        agent.complete('alice', project['id'], job['id'])

        def delayed_candidate(local, remote):
            if not fired:
                commit_other_reconciler()
                raise ValueError('Synthetic stale candidate failure')
            return original_candidate(local, remote)

        monkeypatch.setattr(service, '_candidate', delayed_candidate)
    else:
        def delayed_remote(owner, project_id, local):
            old = original_remote(owner, project_id, local)
            if not fired:
                agent.complete(owner, project_id, job['id'])
                commit_other_reconciler()
                if late_response == 'not_found':
                    raise WorkerError(404, 'job_not_found', 'Synthetic delayed negative response')
            return old

        monkeypatch.setattr(service, '_remote', delayed_remote)

    service.reconcile('alice', project['id'])
    assert fired and committed is not None
    expected = next(row for row in committed['jobs'] if row['id'] == job['id'])
    saved = store.raw('alice', project['id'])
    assert next(row for row in saved['jobs'] if row['id'] == job['id']) == expected
    receipt = (saved['plan_history'], saved['versions'], saved['current_version_id'])
    assert len(saved['plan_history']) == len(before['plan_history']) + (stage == 'plan')
    assert len(saved['versions']) == len(before['versions']) + (stage == 'draft')
    posts, acks = agent.posts, agent.acks
    service.reconcile('alice', project['id'])
    saved = store.raw('alice', project['id'])
    assert (saved['plan_history'], saved['versions'], saved['current_version_id']) == receipt
    assert (agent.posts, agent.acks) == (posts, acks)


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled', 'timed_out', 'invalid_result'])
def test_terminal_job_ignores_late_status_and_error_updates(setup, terminal):
    store, service, _ = setup
    project, job = running(setup, 'plan')
    service._set_job('alice', project['id'], job['id'], state=terminal, error_code='original_code')
    expected = store.raw('alice', project['id'])['jobs'][-1]
    service._set_job('alice', project['id'], job['id'], state='running', error_code='late_code')
    service._set_job('alice', project['id'], job['id'], error_code='late_exception')
    assert store.raw('alice', project['id'])['jobs'][-1] == expected


@pytest.mark.parametrize('stage', ['plan', 'outline', 'draft'])
@pytest.mark.parametrize('regressed_state', ['running', 'receiving'])
def test_publication_receipt_prevents_duplicate_after_legacy_state_regression(
        setup, monkeypatch, stage, regressed_state):
    store, service, agent = setup
    project, job = running(setup, stage)
    agent.complete('alice', project['id'], job['id'])
    service.reconcile('alice', project['id'])
    published = store.raw('alice', project['id'])
    posts, acks = agent.posts, agent.acks

    def legacy_regression(document):
        document['jobs'][-1].update(state=regressed_state, error_code='workspace_storage_unavailable',
                                    delivery_ack=False)

    store.internal('alice', project['id'], legacy_regression)

    def no_remote_lookup(*args):
        raise AssertionError('Persisted publication must not depend on remote artifacts')

    monkeypatch.setattr(service, '_remote', no_remote_lookup)
    monkeypatch.setattr(service, '_candidate', no_remote_lookup)
    service.reconcile('alice', project['id'])
    saved = store.raw('alice', project['id'])
    assert saved['jobs'][-1]['state'] == 'completed' and saved['jobs'][-1]['error_code'] is None
    assert saved['plan_history'] == published['plan_history']
    assert saved['evidence_history'] == published['evidence_history']
    assert saved['evidence'] == published['evidence']
    assert saved['versions'] == published['versions']
    assert saved['current_version_id'] == published['current_version_id']
    assert saved['jobs'][-1]['delivery_ack']
    assert (agent.posts, agent.acks) == (posts, acks + 1)


def test_publication_receipt_checks_manifest_as_well_as_result(setup):
    store, service, agent = setup
    project, job = running(setup, 'plan')
    agent.complete('alice', project['id'], job['id'])
    service.reconcile('alice', project['id'])
    published = store.raw('alice', project['id'])
    with pytest.raises(WorkspaceError, match='stage_publication_conflict'):
        service._ingest('alice', project['id'], job['id'], published['jobs'][-1]['result'], 'b' * 64)
    assert store.raw('alice', project['id']) == published


@pytest.mark.parametrize('save_error', [False, True])
def test_late_candidate_or_save_error_preserves_another_terminal_decision(setup, monkeypatch, save_error):
    store, service, agent = setup
    project, job = running(setup, 'plan')
    agent.complete('alice', project['id'], job['id'])
    original_candidate = service._candidate

    def decide_terminal():
        service._set_job('alice', project['id'], job['id'], state='cancelled', error_code='cancelled')

    if save_error:
        def fail_after_terminal(*args):
            decide_terminal()
            raise ConnectionResetError('Synthetic concurrent terminal decision')

        monkeypatch.setattr(service, '_ingest', fail_after_terminal)
    else:
        def candidate_after_terminal(local, remote):
            result = original_candidate(local, remote)
            decide_terminal()
            return result

        monkeypatch.setattr(service, '_candidate', candidate_after_terminal)

    service.reconcile('alice', project['id'])
    saved = store.raw('alice', project['id'])
    terminal = saved['jobs'][-1]
    assert terminal['state'] == 'cancelled' and terminal['error_code'] == 'cancelled'
    assert terminal['result_digest'] is None and terminal['manifest_sha256'] is None
    assert not saved['plan_history'] and not saved['versions'] and agent.acks == 0
