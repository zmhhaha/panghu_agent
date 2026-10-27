"""Actual mounted human-review API against frozen synthetic delivery storage.

No model, Office application or production data is used in these tests.
"""
import copy
import json
import os

import pytest
from sqlalchemy import select

from ppt_service.server.auth import Identity
from ppt_service.server.models import Checkpoint, Job, Project, Version
from ppt_service.server.acceptance.store import AcceptanceEntry
from test_api import ctx
from review_fixture import seed_review


@pytest.fixture
def review(ctx):
    app, client, who = ctx
    ids = seed_review(app, who[0])
    ids['url'] = f"/api/v1/projects/{ids['pid']}/versions/{ids['vid']}/acceptance"
    return app, client, who, ids


def visual(snapshot, verdict='needs_changes'):
    return {'kind': 'visual_review', 'expected_revision': snapshot['revision'],
            'binding': snapshot['binding'], 'note': 'Synthetic user observations',
            'pages': [{'number': page['number'], 'preview_sha256': page['preview_sha256'],
                       'verdict': verdict if page['number'] == 1 else 'pending',
                       'issues': [{'category': 'text', 'severity': 'major', 'description': 'Clarify the title'}]
                       if verdict == 'needs_changes' and page['number'] == 1 else []}
                      for page in snapshot['pages']]}


def test_mounted_review_records_survive_refresh_without_jobs_or_publication(review):
    app, client, _, ids = review
    first = client.get(ids['url'])
    assert first.status_code == 200
    snapshot = first.json()
    assert snapshot['can_review'] is True and snapshot['revision'] == 0
    assert snapshot['programmatic_status'] == snapshot['powerpoint_status'] == 'pending'
    assert snapshot['publication_authorized'] is False
    assert len(snapshot['pages']) == 3 and snapshot['native_chart_count'] == 0
    body = visual(snapshot)
    one = client.post(ids['url'] + '/records', json=body, headers={'Idempotency-Key': 'review-one'})
    assert one.status_code == 200
    assert one.json()['visual_status'] == 'needs_changes' and one.json()['revision'] == 1
    assert client.get(ids['url']).json() == one.json()
    replay = client.post(ids['url'] + '/records', json=body, headers={'Idempotency-Key': 'review-one'})
    assert replay.json() == one.json()
    with app.state.sessions() as session:
        assert len(session.scalars(select(AcceptanceEntry)).all()) == 1
        assert len(session.scalars(select(Job)).all()) == 1
        assert session.get(Version, ids['vid']).status == 'review'
        assert session.get(Project, ids['pid']).latest_version_id is None
        assert session.get(Checkpoint, ids['cid']).manifest['eligible_as_revision_base'] is False


def test_project_change_and_restore_never_revive_old_observations(review):
    app, client, _, ids = review
    snapshot = client.get(ids['url']).json()
    saved = client.post(ids['url'] + '/records', json=visual(snapshot), headers={'Idempotency-Key': 'save'})
    assert saved.status_code == 200
    with app.state.sessions.begin() as session:
        project = session.get(Project, ids['pid'])
        title = project.title
        project.title = 'New source title'
        project.revision += 1
    stale = client.get(ids['url']).json()
    assert stale['can_review'] is False and stale['acceptance_status'] == 'stale'
    assert stale['records'][0]['stale'] is True
    with app.state.sessions.begin() as session:
        project = session.get(Project, ids['pid'])
        project.title = title
        project.revision += 1
    restored = client.get(ids['url']).json()
    assert restored['can_review'] is True and restored['visual_status'] == 'pending'
    assert restored['records'][0]['stale'] is True
    old_save = client.post(ids['url'] + '/records', json=visual(snapshot), headers={'Idempotency-Key': 'old-binding'})
    assert old_save.status_code == 409 and old_save.json()['detail']['code'] == 'source_binding_changed'


def test_revocation_is_append_only_and_keeps_issue_history(review):
    app, client, _, ids = review
    first = client.get(ids['url']).json()
    saved = client.post(ids['url'] + '/records', json=visual(first), headers={'Idempotency-Key': 'save'}).json()
    response = client.post(ids['url'] + '/records', json={
        'kind': 'revoke', 'expected_revision': 1, 'binding': saved['binding'],
        'record_id': saved['records'][0]['id'], 'reason': 'Correcting synthetic observations'},
        headers={'Idempotency-Key': 'revoke'})
    assert response.status_code == 200
    state = response.json()
    assert state['revision'] == 2 and state['visual_status'] == 'pending'
    assert state['records'][0]['revoked'] is True
    assert state['records'][0]['payload']['pages'][0]['issues'][0]['description'] == 'Clarify the title'
    with app.state.sessions() as session:
        assert len(session.scalars(select(AcceptanceEntry)).all()) == 2


@pytest.mark.parametrize('fault', ['owner', 'issuer', 'project', 'deleted', 'job_owner', 'checkpoint_owner'])
def test_review_resolver_enforces_all_owner_links_before_reading_storage(review, fault):
    app, client, who, ids = review
    if fault == 'owner': who[0] = Identity(who[0].issuer, 'bob')
    elif fault == 'issuer': who[0] = Identity('https://other-issuer', who[0].sub)
    elif fault == 'project': ids['url'] = ids['url'].replace(ids['pid'], 'unknown-project')
    else:
        with app.state.sessions.begin() as session:
            if fault == 'deleted': session.get(Project, ids['pid']).deletion_requested = True
            elif fault == 'job_owner': session.get(Job, ids['jid']).subject = 'bob'
            elif fault == 'checkpoint_owner': session.get(Checkpoint, ids['cid']).issuer = 'https://other-issuer'
    assert client.get(ids['url']).status_code == 404


@pytest.mark.parametrize('fault', ['source', 'extra_source', 'home', 'preview', 'pptx', 'receipt',
                                  'transport', 'missing_receipt', 'running', 'unstopped',
                                  'checkpoint_manifest', 'foreign_path', 'runtime'])
def test_untrusted_or_changed_delivery_evidence_fails_closed(review, fault):
    app, client, _, ids = review
    root = app.state.storage.root
    key = None
    if fault == 'source': key = ids['cp_key'] + '/project/deck/design_spec.md'
    elif fault == 'home': key = ids['cp_key'] + '/home/state.db'
    elif fault == 'preview': key = ids['artifact_key'] + '/1.png'
    elif fault == 'pptx': key = ids['artifact_key'] + '/presentation.pptx'
    elif fault == 'receipt': key = ids['evidence_key'] + '/delivery/candidate-delivery.json'
    elif fault == 'transport': key = ids['evidence_key'] + '/transport-manifest.json'
    elif fault == 'checkpoint_manifest': key = ids['cp_key'] + '/checkpoint.json'
    elif fault == 'extra_source':
        (root / ids['cp_key'] / 'project/deck/undeclared.py').write_bytes(b'never execute this')
    elif fault == 'missing_receipt':
        (root / ids['evidence_key'] / 'delivery/candidate-delivery.json').unlink()
    else:
        with app.state.sessions.begin() as session:
            if fault == 'running': session.get(Job, ids['jid']).status = 'running'
            elif fault == 'unstopped': session.get(Job, ids['jid']).stopped = False
            else:
                version = session.get(Version, ids['vid'])
                value = copy.deepcopy(version.manifest)
                if fault == 'foreign_path': value['review_evidence']['storage_key'] = 'projects/other/snapshots/evidence'
                else: value['review_evidence']['execution_image'] = 'unbound:latest'
                version.manifest = value
    if key: (root / key).write_bytes(b'changed synthetic bytes')
    response = client.get(ids['url'])
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'review_evidence_unavailable'
    assert 'changed synthetic bytes' not in response.text


@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink', 'directory_link'])
def test_review_storage_rejects_file_and_directory_links(review, link_kind, tmp_path):
    app, client, _, ids = review
    root = app.state.storage.root
    target = root / ids['artifact_key'] / '1.png'
    outside = tmp_path / 'other.png'
    outside.write_bytes(target.read_bytes())
    if link_kind == 'directory_link':
        target = root / ids['cp_key'] / 'project/deck/svg_output'
        moved = tmp_path / 'svg'
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
    else:
        target.unlink()
        if link_kind == 'symlink': target.symlink_to(outside)
        else: os.link(outside, target)
    assert client.get(ids['url']).status_code == 503


@pytest.mark.parametrize('name', ['1.png', 'presentation.pptx'])
def test_review_downloads_serve_only_bound_artifact_bytes(review, name):
    app, client, _, ids = review
    route = (f"/api/v1/versions/{ids['vid']}/download" if name.endswith('pptx')
             else f"/api/v1/versions/{ids['vid']}/slides/1/preview")
    before = client.get(route)
    assert before.status_code == 200
    path = app.state.storage.root / ids['artifact_key'] / name
    path.write_bytes(before.content + b'changed')
    assert client.get(route).status_code == 404
