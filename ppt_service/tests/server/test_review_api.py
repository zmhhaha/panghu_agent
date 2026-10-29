"""Owner-scoped review draft access, without revision or publication authority."""
import hashlib
import pytest
from sqlalchemy import select

from ppt_service.server.auth import Identity
from ppt_service.server.models import Job, Project, Version
from ppt_service.tests.server.test_api import ctx, project, seed_version


def seed_review(app, p):
    vid, folder = seed_version(app, p)
    with app.state.sessions.begin() as s:
        v = s.get(Version, vid); v.status = 'review'
        v.manifest = {**v.manifest, 'status': 'review', 'publication_authorization': False,
                      'eligible_as_revision_base': False,
                      'pptx_sha256': hashlib.sha256((folder / 'deck.pptx').read_bytes()).hexdigest(),
                      'slides': [{'preview_key': v.manifest['slides'][0]['preview_key'],
                                  'preview_sha256': hashlib.sha256((folder / '1.png').read_bytes()).hexdigest()}],
                      'required_pending_gates': ['independent_export_build_contract', 'independent_visual_review',
                                                 'powerpoint_open_edit_save_reopen', '/private/internal-file'],
                      'review_evidence': {'storage_key': '/private/diagnostics', 'controller_nonce': 'PRIVATE-NONCE'}}
    return vid, folder


@pytest.mark.parametrize('artifact', ['deck.pptx', '1.png'])
def test_review_download_rejects_changed_bytes_even_when_file_signature_is_valid(ctx, artifact):
    app, client, _ = ctx
    current = project(client)
    version_id, folder = seed_review(app, current)
    path = folder / artifact
    path.write_bytes(path.read_bytes() + b' changed after validation')
    route = '/download' if artifact.endswith('.pptx') else '/slides/1/preview'
    assert client.get('/api/v1/versions/' + version_id + route).status_code == 404


def test_review_list_download_and_preview_keep_owner_acl_and_hide_evidence(ctx):
    app, c, who = ctx; p = project(c); vid, _ = seed_review(app, p)
    response = c.get('/api/v1/projects/' + p['id'] + '/versions')
    assert response.status_code == 200
    draft = response.json()[0]
    assert draft['id'] == vid and draft['status'] == 'review'
    assert draft['can_revise'] is False and draft['publication_authorization'] is False
    assert draft['validation_status'] == 'gate_pending'
    assert len(draft['required_pending_gates']) == 3
    assert 'PRIVATE-NONCE' not in response.text and '/private' not in response.text
    assert c.get(draft['download_url']).status_code == 200
    assert c.get(draft['slides'][0]['preview_url']).headers['content-type'] == 'image/png'
    for owner in (Identity('https://issuer', 'bob'), Identity('https://other-issuer', 'alice')):
        who[0] = owner
        assert c.get('/api/v1/projects/' + p['id'] + '/versions').status_code == 404
        assert c.get(draft['download_url']).status_code == 404
        assert c.get(draft['slides'][0]['preview_url']).status_code == 404


def test_review_cannot_be_used_as_a_revision_baseline_or_enqueue_a_job(ctx):
    app, c, _ = ctx; p = project(c); vid, _ = seed_review(app, p)
    with app.state.sessions() as s:
        before = len(s.scalars(select(Job)).all())
    response = c.post('/api/v1/projects/' + p['id'] + '/revisions',
                      json={'expected_revision': p['revision'], 'base_version_id': vid, 'feedback': 'Modify'},
                      headers={'Idempotency-Key': 'review-revision'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'review_version_not_revision_base'
    with app.state.sessions() as s:
        assert len(s.scalars(select(Job)).all()) == before
        assert s.get(Project, p['id']).active_job_id is None


def test_published_versions_retain_revision_support(ctx):
    app, c, _ = ctx; p = project(c); vid, _ = seed_version(app, p)
    dto = c.get('/api/v1/projects/' + p['id'] + '/versions').json()[0]
    assert dto['status'] == 'published' and dto['can_revise'] is True
    response = c.post('/api/v1/projects/' + p['id'] + '/revisions',
                      json={'expected_revision': p['revision'], 'base_version_id': vid, 'feedback': 'Modify'},
                      headers={'Idempotency-Key': 'published-revision'})
    assert response.status_code == 202
    assert response.json()['base_version_id'] == vid


def test_deleted_project_revokes_review_download_preview_and_list(ctx):
    app, c, _ = ctx; p = project(c); vid, _ = seed_review(app, p)
    with app.state.sessions.begin() as s:
        s.get(Project, p['id']).deletion_requested = True
    assert c.get('/api/v1/projects/' + p['id'] + '/versions').status_code == 404
    assert c.get('/api/v1/versions/' + vid + '/download').status_code == 404
    assert c.get('/api/v1/versions/' + vid + '/slides/1/preview').status_code == 404
