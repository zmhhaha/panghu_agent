"""Scheduling regressions with original ledger IDs and synthetic Agent jobs."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
import test_animation_collaboration_service as fixtures
from test_animation_collaboration_service import work, begin, until_parts


def test_ready_successor_fills_free_slot_while_independent_long_part_still_runs(work, monkeypatch):
    service, ordinary, _, author, renderer = work
    plan = deepcopy(fixtures.PLAN)
    plan['components'][2]['depends_on'] = ['structure']
    monkeypatch.setattr(fixtures, 'PLAN', plan)
    row = begin(service)
    _, reserved = until_parts(service, row['id'])
    author.active = True
    service.advance('developer', row['id'])
    run = service.commissions.get('developer', row['id'])
    structure, foreground = run['operations'][1:]
    original_ids = [op['id'] for op in run['operations']]
    original_jobs = [op['job_id'] for op in run['operations']]
    assert len(author.submissions) == 3

    # Only the prerequisite finishes; the unrelated foreground job stays live.
    author._job(structure['job_id']).update(state='completed', stop_proven=True)
    service.advance('developer', row['id'])
    assert author._job(foreground['job_id'])['state'] == 'running'
    run = service.commissions.get('developer', row['id'])
    assert run['operations'][1]['status'] == 'completed'
    assert run['operations'][2]['status'] == 'submitted'

    # The next advance must use the free slot, without waiting for foreground.
    service.advance('developer', row['id'])
    current = service.commissions.get('developer', row['id'])
    parts = [op for op in current['operations'] if op['input'].get('phase') == 'component']
    assert [op['input']['component_id'] for op in parts] == ['structure', 'foreground', 'atmosphere']
    successor = parts[-1]
    assert successor['status'] == 'submitted'
    assert [dep['component_id'] for dep in successor['input']['dependencies']] == ['structure']
    assert author._job(foreground['job_id'])['state'] == author._job(successor['job_id'])['state'] == 'running'
    assert [op['id'] for op in current['operations'][:3]] == original_ids
    assert [op['job_id'] for op in current['operations'][:3]] == original_jobs
    assert author.submissions == original_ids + [successor['id']]
    assert current['deadline_at'] == run['deadline_at']
    assert current['usage']['creator'] == 4 and current['remaining']['creator'] == 1
    assert ordinary.submissions == renderer.calls == []
    assert reserved[0]['id'] == structure['id']


def test_cancel_reaches_second_original_job_even_when_first_poll_returns_503(work, monkeypatch):
    service, ordinary, _, author, renderer = work
    row = begin(service)
    until_parts(service, row['id'])
    author.active = True
    service.advance('developer', row['id'])
    run = service.commissions.get('developer', row['id'])
    first, second = run['operations'][1:]
    original_ids = [op['id'] for op in run['operations']]
    original_submissions = list(author.submissions)
    original_poll, original_cancel = author.poll, author.cancel
    cancelled = []

    def poll(job_id):
        if job_id == first['job_id']:
            raise ServiceError('service_synthetic_transport_unavailable', 503)
        return original_poll(job_id)

    def cancel(job_id):
        cancelled.append(job_id)
        return original_cancel(job_id)

    monkeypatch.setattr(author, 'poll', poll)
    monkeypatch.setattr(author, 'cancel', cancel)
    service.cancel('developer', row['id'], {})
    with pytest.raises(ServiceError, match='service_synthetic_transport_unavailable'):
        service.advance('developer', row['id'])

    current = service.commissions.get('developer', row['id'])
    assert cancelled == [second['job_id']]
    assert author._job(second['job_id'])['state'] == 'cancelled'
    assert author._job(second['job_id'])['stop_proven'] is True
    assert author._job(first['job_id'])['state'] == 'running'
    assert author._job(first['job_id'])['stop_proven'] is False
    assert current['state'] == 'cancelling' and current['cancel_requested'] is True
    assert [op['id'] for op in current['operations']] == original_ids
    assert [op['job_id'] for op in current['operations']] == [op['job_id'] for op in run['operations']]
    assert author.submissions == original_submissions and len(author.submissions) == 3
    assert ordinary.submissions == renderer.calls == []
