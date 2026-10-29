"""Portable Pi/Hermes review routes; synthetic bytes do not prove acceptance."""
import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from ppt_service.control.agent_connector import build_agent_task
from ppt_service.server.auth import Identity
from ppt_service.server.acceptance.domain import AcceptanceError
from ppt_service.server.models import AgentOutbox, Checkpoint, Job, Plan, Project, Version
from ppt_service.server.repository import Repository, digest
from ppt_service.server.review_revisions import recheck_review_source, validate_review_base
from agent_review_fixture import seed_agent_review
from test_api import ctx


@pytest.fixture(params=['pi', 'hermes'])
def portable_review(ctx, request):
    app, client, owners = ctx
    data = seed_agent_review(app, owners[0], request.param)
    data['url'] = f"/api/v1/projects/{data['pid']}/versions/{data['vid']}/acceptance"
    return app, client, owners, data


def revision_body(snapshot, data):
    return {'expected_revision': snapshot['source_revision'], 'base_version_id': data['vid'],
            'feedback': 'Clarify page two using the approved source.', 'pages': [2],
            'review_binding': snapshot['binding'], 'review_revision': snapshot['revision']}


def queue_revision(client, data, body, key='portable-review-revision'):
    return client.post(f"/api/v1/projects/{data['pid']}/review-revisions", json=body,
                       headers={'Idempotency-Key': key})


def test_portable_review_keeps_actual_engine_and_can_queue_only_private_revision(portable_review):
    app, client, _, data = portable_review
    response = client.get(data['url'])
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot['can_review'] is True and snapshot['publication_authorized'] is False
    assert snapshot['binding']['runtime'] == {'engine': data['pins']['engine'],
        'runtime_revision': digest(data['pins']), 'profile': 'ppt.generate.v1',
        'checkpoint_format': 'ppt-agent-artifacts-v1'}
    assert snapshot['revision'] == 0 and len(snapshot['pages']) == 3
    body = revision_body(snapshot, data)
    queued = queue_revision(client, data, body)
    assert queued.status_code == 202, queued.text
    assert queue_revision(client, data, body).json() == queued.json()
    with app.state.sessions.begin() as session:
        job = Repository(session).claim_next()
        assert job.id == queued.json()['job_id'] and job.kind == 'revise'
        recheck_review_source(session, app.state.storage, job)
        task, checkpoint_ref = build_agent_task(session, job)
        assert task['base_version']['status'] == 'review'
        assert task['base_version']['eligible_as_revision_base'] is False
        assert task['base_version']['review_authority']['target_status'] == 'review'
        assert task['base_version']['review_authority']['review_revision'] == 0
        assert task['revision_pages'] == [2] and task['feedback'] == body['feedback']
        assert set(task['confirmations']) == {'stage1', 'stage2'}
        original = session.get(Version, data['vid'])
        assert original.status == 'review' and original.manifest['publication_authorization'] is False
        assert session.get(Project, data['pid']).latest_version_id is None
        assert checkpoint_ref == session.get(AgentOutbox, data['jid']).checkpoint_ref
        assert len(session.scalars(select(Version)).all()) == 1


def test_portable_review_records_survive_refresh_without_publication(portable_review):
    app, client, _, data = portable_review
    snapshot = client.get(data['url']).json()
    body = {'kind': 'visual_review', 'expected_revision': 0, 'binding': snapshot['binding'],
        'pages': [{'number': row['number'], 'preview_sha256': row['preview_sha256'],
                   'verdict': 'pending', 'issues': []} for row in snapshot['pages']],
        'note': 'Synthetic user observations'}
    response = client.post(data['url'] + '/records', json=body, headers={'Idempotency-Key': 'portable-feedback'})
    assert response.status_code == 200, response.text
    assert client.get(data['url']).json() == response.json()
    stale = queue_revision(client, data, revision_body(snapshot, data))
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'review_records_changed'
    assert queue_revision(client, data, revision_body(response.json(), data), key='current-feedback').status_code == 202
    with app.state.sessions() as session:
        assert session.get(Version, data['vid']).status == 'review'
        assert session.get(Checkpoint, data['cid']).manifest['eligible_as_revision_base'] is False


@pytest.mark.parametrize('fault', ['source', 'extra_source', 'source_manifest', 'remote_job',
    'checkpoint_file', 'preview', 'pptx', 'validator_report', 'validator_transport', 'independent_report',
    'independent_preview', 'pins', 'remote_identity', 'source_unstopped', 'foreign_checkpoint', 'format'])
def test_portable_evidence_mutation_fails_closed_before_revision(portable_review, fault):
    app, client, _, data = portable_review
    initial = client.get(data['url'])
    assert initial.status_code == 200, initial.text
    paths = {'source': data['cp_key'] + '/project/deck/spec_lock.md',
        'extra_source': data['source_key'] + '/project/deck/unindexed.md',
        'source_manifest': data['source_key'] + '/result/manifest.json',
        'remote_job': data['source_key'] + '/remote-job.json',
        'checkpoint_file': data['cp_key'] + '/result/checkpoint.json',
        'preview': data['artifact_key'] + '/2.png', 'pptx': data['artifact_key'] + '/presentation.pptx',
        'validator_report': data['evidence_key'] + '/validated.json',
        'validator_transport': data['evidence_key'] + '/transport-manifest.json',
        'independent_report': data['programmatic_key'] + '/programmatic-evidence.json',
        'independent_preview': data['programmatic_key'] + '/scratch-projects/deck/.preview/2.png'}
    if fault in paths:
        (app.state.storage.root / paths[fault]).write_bytes(b'Changed immutable evidence')
    else:
        with app.state.sessions.begin() as session:
            if fault == 'source_unstopped':
                session.get(Job, data['jid']).stopped = False
            elif fault == 'pins':
                out = session.get(AgentOutbox, data['jid'])
                out.remote_pins = dict(out.remote_pins, image_digest='sha256:' + '0' * 64)
            elif fault == 'remote_identity':
                session.get(AgentOutbox, data['jid']).remote_job_id = '11111111-1111-1111-1111-111111111111'
            else:
                checkpoint = session.get(Checkpoint, data['cid'])
                if fault == 'foreign_checkpoint':checkpoint.subject = 'another-owner'
                elif fault == 'format':
                    checkpoint.manifest = dict(checkpoint.manifest, checkpoint_format='unknown-format')
    response = queue_revision(client, data, revision_body(initial.json(), data))
    assert response.status_code in (404, 503), response.text
    with app.state.sessions() as session:
        assert not session.scalars(select(Job).where(Job.kind == 'revise')).all()


@pytest.mark.parametrize('stage,fault', [('stage1', 'unconfirmed'), ('stage2', 'unconfirmed'),
                                      ('stage1', 'changed_plan'), ('stage2', 'changed_plan')])
def test_both_real_database_confirmations_remain_required(portable_review, stage, fault):
    app, client, _, data = portable_review
    snapshot = client.get(data['url']).json()
    with app.state.sessions.begin() as session:
        plan = session.get(Plan, data['plan_ids'][stage])
        if fault == 'unconfirmed':plan.confirmed_at = None
        else:plan.outline = {'pages': []}
    response = queue_revision(client, data, revision_body(snapshot, data))
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'review_checkpoint_unavailable'


def test_portable_review_rechecks_all_source_before_launch_and_never_is_published_base(portable_review):
    app, client, owners, data = portable_review
    snapshot = client.get(data['url']).json()
    body = revision_body(snapshot, data)
    published = client.post(f"/api/v1/projects/{data['pid']}/revisions",
        json={key: value for key, value in body.items() if not key.startswith('review_')},
        headers={'Idempotency-Key': 'cannot-publish-review'})
    assert published.status_code == 409
    assert published.json()['detail']['code'] == 'review_version_not_revision_base'
    assert queue_revision(client, data, body).status_code == 202
    (app.state.storage.root / data['source_key'] / 'project/deck/spec_lock.md').write_bytes(b'Changed before launch')
    with app.state.sessions.begin() as session:
        job = Repository(session).claim_next()
        with pytest.raises(AcceptanceError) as error:
            recheck_review_source(session, app.state.storage, job)
        assert getattr(error.value, 'code', None) == 'review_evidence_unavailable'
    owners[0] = Identity('https://issuer', 'another-owner')
    assert client.get(data['url']).status_code == 404


@pytest.mark.parametrize('fault', ['engine', 'profile', 'format', 'pins_digest', 'publication_target'])
def test_portable_controller_rejects_mutated_review_authority(portable_review, fault):
    app, client, _, data = portable_review
    snapshot = client.get(data['url']).json()
    queued = queue_revision(client, data, revision_body(snapshot, data))
    assert queued.status_code == 202
    with app.state.sessions.begin() as session:
        job = session.get(Job, queued.json()['job_id'])
        changed = copy.deepcopy(job.input_snapshot)
        runtime = changed['review_base']['review_binding']['runtime']
        if fault == 'engine':runtime['engine'] = 'different-engine'
        elif fault == 'profile':runtime['profile'] = 'research.generate.v1'
        elif fault == 'format':runtime['checkpoint_format'] = 'hermes-home-project-v1'
        elif fault == 'pins_digest':runtime['runtime_revision'] = '0' * 64
        else:changed['review_base']['target_status'] = 'published'
        job.input_snapshot = changed
        with pytest.raises(HTTPException) as error:
            validate_review_base(session, job, session.get(Version, data['vid']), session.get(Checkpoint, data['cid']))
        assert error.value.detail['code'] == 'review_checkpoint_unavailable'
