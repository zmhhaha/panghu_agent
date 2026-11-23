"""Both real HTTP hops retain video-learning owner and analysis boundaries."""
import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest

from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_lab import LabHTTPServer
from test_animation_method_http import TOKEN, USER, SECOND, HOST, BASE, CSRF, serving, public_request
from test_video_learning_service import system, real_mp4

PREFIX = '/api/video-lab'


@pytest.fixture
def peers(system):
    service, author, reviewer, renderer = system
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False,
        video_learning_service=service)) as private:
        client = MethodLabClient('http://127.0.0.1:' + str(private.server_port), TOKEN)
        lab = SimpleNamespace(methods_only=True, csrf_token=CSRF, method_client=client)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
            base_path=BASE, developer_users=[USER, SECOND])) as public:
            yield service, author, reviewer, renderer, public


def reference_body(raw):
    return dict(request_id=uuid4().hex, file_name='actual-reference.mp4', mime_type='video/mp4',
        content_base64=base64.b64encode(raw).decode())


def test_video_catalogue_has_an_explicit_history_response_budget(peers, monkeypatch):
    service, _, _, _, public = peers
    padding = 'history-' + 'x' * (2 * 1024 * 1024)
    monkeypatch.setattr(service, 'catalogue', lambda owner: dict(projects=[], analyses=[],
        references=[], availability=dict(enabled=True), retained_history=padding))
    status, catalogue, _ = public_request(public, 'GET', PREFIX + '/catalog')
    assert status == 200 and catalogue['retained_history'] == padding


def test_video_upload_project_media_and_owner_survive_both_hops(peers, real_mp4):
    service, author, reviewer, renderer, public = peers
    body = reference_body(real_mp4)
    status, reference, _ = public_request(public, 'POST', PREFIX + '/references', body)
    assert status == 201, reference
    assert reference['duration_seconds'] == 2
    status, repeated, _ = public_request(public, 'POST', PREFIX + '/references', body)
    assert status == 201 and repeated['id'] == reference['id']
    status, raw, _ = public_request(public, 'GET', PREFIX + '/references/' + reference['id'] + '/media')
    assert status == 200 and raw == real_mp4
    status, _, _ = public_request(public, 'GET', PREFIX + '/references/' + reference['id'] + '/media',
        headers={'X-Forwarded-User': SECOND})
    assert status == 404
    project_body = dict(request_id=uuid4().hex, title='Visible trajectory', goal='Learn the sequence',
        reference_ids=[reference['id']], start_seconds=0, end_seconds=2,
        width=320, height=180, use_wikiskill=False, max_rounds=1, max_seconds=900)
    status, project, _ = public_request(public, 'POST', PREFIX + '/projects', project_body)
    assert status == 202, project
    assert project['references'][0]['url'].startswith(PREFIX)
    status, catalogue, _ = public_request(public, 'GET', PREFIX + '/catalog')
    assert status == 200 and catalogue['projects'][0]['id'] == project['id']
    assert not author.submissions and not reviewer.submissions and not renderer.calls


def test_analysis_post_get_stop_are_explicit_and_never_create_project(peers, real_mp4):
    service, author, reviewer, renderer, public = peers
    _, reference, _ = public_request(public, 'POST', PREFIX + '/references', reference_body(real_mp4))
    path = PREFIX + '/references/' + reference['id'] + '/analyses'
    body = dict(request_id=uuid4().hex, goal='Find useful short techniques.', max_seconds=600)
    status, analysis, _ = public_request(public, 'POST', path, body)
    assert status == 202 and analysis['state'] == 'queued', analysis
    status, same, _ = public_request(public, 'POST', path, body)
    assert status == 202 and same['id'] == analysis['id']
    status, read, _ = public_request(public, 'GET', path + '/' + analysis['id'])
    assert status == 200 and read['creation_started'] is False
    status, stopping, _ = public_request(public, 'POST', path + '/' + analysis['id'] + '/stop', {})
    assert status == 200 and stopping['stop_requested']
    service.advance(USER, analysis['id'])
    status, stopped, _ = public_request(public, 'GET', path + '/' + analysis['id'])
    assert status == 200 and stopped['state'] == 'cancelled'
    status, catalogue, _ = public_request(public, 'GET', PREFIX + '/catalog')
    assert status == 200 and catalogue['analyses'][0]['id'] == analysis['id']
    assert catalogue['references'][0]['id'] == reference['id'] and not catalogue['projects']
    assert not author.submissions and not reviewer.submissions and not renderer.calls


@pytest.mark.parametrize('headers', [{'Origin': 'https://invalid.example'}, {'X-Animation-Lab-Token': None}])
def test_video_upload_keeps_origin_and_csrf_boundary(peers, real_mp4, headers):
    service, author, reviewer, renderer, public = peers
    status, _, _ = public_request(public, 'POST', PREFIX + '/references', reference_body(real_mp4), headers=headers)
    assert status == 403 and service.store.list('references') == []
