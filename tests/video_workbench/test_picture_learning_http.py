"""Real owner-scoped private/public picture API, without paid model or Docker calls."""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
import random
import re
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from video_workbench.animation_creation_http import CreationHTTPServer, CreationTransportError
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_lab import LabHTTPServer
from video_workbench.picture_learning_service import PictureLearningService
from test_animation_method_http import (TOKEN, USER, SECOND, HOST, BASE, CSRF,
    serving, raw_request, public_request)
from test_picture_learning_service import Gateway, Renderer

PREFIX = '/api/picture-lab'


def reference_body(*, noisy=False):
    output = io.BytesIO()
    if noisy:
        # Real, deterministic RGB noise avoids tiny compressed fixtures hiding
        # the old 32 KiB HTTP body limit. It makes no artistic quality claim.
        image = Image.frombytes('RGB', (320, 180), random.Random(18).randbytes(320 * 180 * 3))
    else:
        image = Image.new('RGB', (320, 180), (40, 60, 80))
    image.save(output, 'PNG')
    raw = output.getvalue()
    return dict(request_id=str(uuid4()), file_name='reference.png', mime_type='image/png',
                content_base64=base64.b64encode(raw).decode()), raw


def project_body(reference_id):
    return dict(request_id=str(uuid4()), title='Shape and brush study',
        goal='Learn how the reference shape and brush contrast create volume.',
        reference_ids=[reference_id], width=320, height=180, use_wikiskill=False,
        max_rounds=1, max_seconds=900, criteria=['Shape and contrast must remain legible.'])


@pytest.fixture
def peers(tmp_path):
    author, reviewer = Gateway(), Gateway(scores=[85])
    renderer = Renderer(tmp_path / 'renders')
    service = PictureLearningService(tmp_path / 'pictures', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False,
                                   picture_service=service)) as private:
        client = MethodLabClient('http://127.0.0.1:' + str(private.server_port), TOKEN)
        lab = SimpleNamespace(methods_only=True, csrf_token=CSRF, method_client=client)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                     base_path=BASE, developer_users=[USER, SECOND])) as public:
            yield service, author, reviewer, renderer, client, private, public


def start_public(public):
    upload, _ = reference_body()
    status, reference, _ = public_request(public, 'POST', PREFIX + '/references', upload)
    assert status == 201, reference
    body = project_body(reference['id'])
    status, project, _ = public_request(public, 'POST', PREFIX + '/projects', body)
    assert status == 202, project
    return project, body


def finish(service, project):
    for _ in range(60):
        current = service.get(USER, project['id'])
        if current['state'] in {'completed', 'failed', 'cancelled'}:
            return current
        service.advance(USER, current['latest_experiment_id'])
    raise AssertionError('Bounded fixture did not finish')


def test_private_headers_and_owner_are_required_and_owner_never_comes_from_json(peers):
    service, author, reviewer, renderer, client, private, public = peers
    for headers in (
        [('Host', '127.0.0.1')],
        [('Authorization', 'Bearer ' + TOKEN)],
        [('Authorization', 'Bearer wrong-token'), ('X-Animation-Creation-Owner', USER)],
        [('Authorization', 'Bearer ' + TOKEN), ('X-Animation-Creation-Owner', USER),
         ('X-Animation-Creation-Owner', SECOND)],
    ):
        assert raw_request(private, 'GET', PREFIX + '/catalog', headers=headers)[0] == 403
    assert client.picture_catalogue(USER)['projects'] == []
    upload, _ = reference_body()
    status, reference, _ = public_request(public, 'POST', PREFIX + '/references', upload)
    assert status == 201
    body = dict(project_body(reference['id']), owner=SECOND)
    assert public_request(public, 'POST', PREFIX + '/projects', body)[0] == 422
    assert service.store.list('projects', USER) == []
    assert author.submissions == reviewer.submissions == renderer.calls == []


def test_real_large_reference_upload_survives_both_hops_preserves_pixels_and_is_idempotent(peers):
    service, author, reviewer, renderer, client, _, public = peers
    upload, raw = reference_body(noisy=True)
    assert len(raw) > 32768 and len(json.dumps(upload)) > 32768
    status, reference, _ = public_request(public, 'POST', PREFIX + '/references', upload)
    assert status == 201, reference
    assert reference['sha256'] == hashlib.sha256(raw).hexdigest()
    assert reference['width'] == 320 and reference['height'] == 180
    assert reference['url'].startswith(BASE + PREFIX + '/references/')
    again = public_request(public, 'POST', PREFIX + '/references', upload)
    assert again[0] == 201 and again[1]['id'] == reference['id']
    status, actual, headers = public_request(public, 'GET', reference['url'], absolute=True)
    assert status == 200 and actual == raw
    assert headers['Content-Type'].startswith('image/png')
    assert client.picture_reference(USER, reference['id']) == (raw, 'image/png')
    changed = dict(upload, file_name='different.png')
    assert public_request(public, 'POST', PREFIX + '/references', changed)[0] == 409
    assert len(service.store.list('references', USER)) == 1
    assert author.submissions == reviewer.submissions == renderer.calls == []


def test_project_creation_is_idempotent_read_and_inspect_never_advance_or_notify(peers):
    service, author, reviewer, renderer, client, private, public = peers
    project, body = start_public(public)
    assert project['state'] == 'queued' and project['candidates'] == []
    repeat = public_request(public, 'POST', PREFIX + '/projects', body)
    assert repeat[0] == 202 and repeat[1]['id'] == project['id']
    changed = dict(body, goal='Changed request with same identity')
    assert public_request(public, 'POST', PREFIX + '/projects', changed)[0] == 409

    class ReadOnlyPump:
        def notify(self):
            pytest.fail('GET must not wake an execution pump')

        def close(self):
            pass

    private.picture_pump = ReadOnlyPump()
    for _ in range(2):
        status, catalog, _ = public_request(public, 'GET', PREFIX + '/catalog')
        assert status == 200 and catalog['csrf_token'] == CSRF
        assert re.fullmatch(r'[0-9a-f]{64}', catalog['owner_key'])
        assert catalog['projects'][0]['request_id'] == body['request_id']
        assert catalog['projects'][0]['latest_experiment_request_id'] == body['request_id']
        detail = public_request(public, 'GET', PREFIX + '/projects/' + project['id'])[1]
        assert detail['state'] == 'queued' and detail['experiments'][0]['operations'] == []
        path = PREFIX + '/projects/' + project['id'] + '/experiments/' + project['latest_experiment_id'] + '/input'
        status, inspection, _ = public_request(public, 'GET', path)
        assert status == 200, inspection
        assert inspection['frozen']['goal'] == body['goal']
        assert inspection['scope'] == 'frozen_business_inputs_not_system_prompt'
        assert inspection['operations'] == []
        assert inspection['model_understanding_verified'] is False
    assert len(service.store.list('projects', USER)) == len(service.pending()) == 1
    assert author.submissions == reviewer.submissions == renderer.calls == []


@pytest.mark.parametrize('bad', [
    {'X-Forwarded-User': None}, {'X-Forwarded-User': 'not-a-developer'}, {'Host': 'evil.invalid'},
    {'Origin': None}, {'Origin': 'https://evil.invalid'}, {'X-Animation-Lab-Token': None},
    {'X-Animation-Lab-Token': 'wrong'}, {'Content-Type': 'text/plain'},
])
def test_origin_csrf_and_developer_denial_precede_any_reference_write(peers, bad):
    service, author, reviewer, renderer, _, _, public = peers
    upload, _ = reference_body()
    status, _, _ = public_request(public, 'POST', PREFIX + '/references', upload, headers=bad)
    assert status in {403, 415}
    assert service.store.list('references', USER) == []
    assert author.submissions == reviewer.submissions == renderer.calls == []


def test_cross_owner_reference_project_inspection_and_output_are_denied(peers):
    service, author, reviewer, renderer, client, _, public = peers
    project, _ = start_public(public)
    path = PREFIX + '/projects/' + project['id']
    other = {'X-Forwarded-User': SECOND}
    assert public_request(public, 'GET', PREFIX + '/catalog', headers=other)[1]['projects'] == []
    assert public_request(public, 'GET', path, headers=other)[0] == 404
    assert public_request(public, 'GET', project['references'][0]['url'], absolute=True, headers=other)[0] == 404
    assert public_request(public, 'GET', path + '/experiments/' + project['latest_experiment_id'] + '/input', headers=other)[0] == 404
    with pytest.raises(CreationTransportError) as denied:
        client.picture_get(SECOND, project['id'])
    assert denied.value.status == 404
    body = project_body(project['references'][0]['id'])
    assert public_request(public, 'POST', PREFIX + '/projects', body, headers=other)[0] == 404
    assert author.submissions == reviewer.submissions == renderer.calls == []


def test_completed_png_source_receipt_and_model_inputs_survive_two_hops_without_get_execution(peers):
    service, author, reviewer, renderer, client, private, public = peers
    project, _ = start_public(public)
    completed = finish(service, project)
    assert completed['state'] == 'completed', completed
    before = deepcopy((author.submissions, reviewer.submissions, renderer.calls))
    detail = public_request(public, 'GET', PREFIX + '/projects/' + project['id'])[1]
    candidate = detail['candidates'][0]
    assert candidate['source_url'].startswith(BASE + PREFIX + '/')
    status, png, headers = public_request(public, 'GET', candidate['picture_url'], absolute=True)
    assert status == 200 and png.startswith(b'\x89PNG\r\n\x1a\n')
    assert headers['Content-Type'].startswith('image/png')
    status, source, headers = public_request(public, 'GET', candidate['source_url'], absolute=True)
    assert status == 200 and source['source_sha256'] == candidate['source_sha256']
    assert headers['Content-Disposition'].startswith('attachment;')
    status, receipt, headers = public_request(public, 'GET', candidate['receipt_url'], absolute=True)
    assert status == 200 and receipt['scope'] == 'static_only'
    assert 'attachment;' in headers['Content-Disposition']
    path = PREFIX + '/projects/' + project['id'] + '/experiments/' + project['latest_experiment_id'] + '/input'
    status, inspection, _ = public_request(public, 'GET', path)
    assert status == 200 and inspection['operations']
    assert any(op['input'].get('observation', {}).get('frames') for op in inspection['operations'])
    assert before == (author.submissions, reviewer.submissions, renderer.calls)
    assert public_request(public, 'GET', candidate['picture_url'], absolute=True,
        headers={'X-Forwarded-User': SECOND})[0] == 404


def test_picture_page_and_complete_static_import_graph_are_authenticated_and_never_start_work(peers):
    service, author, reviewer, renderer, _, _, public = peers
    assets = ['picture-library.html', 'picture-library.css', 'picture-library.mjs', 'picture-model.mjs', 'picture-api.openapi.json']
    for name in assets:
        status, content, _ = public_request(public, 'GET', '/' + name)
        assert status == 200, (name, content)
        assert public_request(public, 'GET', '/' + name, headers={'X-Forwarded-User': None})[0] == 403
    status, html, _ = public_request(public, 'GET', '/picture-library.html')
    assert b'id="picture-library-app"' in html
    for name in ('method-gallery.html', 'knowledge.html'):
        assert b'href="./picture-library.html"' in public_request(public, 'GET', '/' + name)[1]
    assert public_request(public, 'GET', PREFIX + '/catalog?execute=true')[0] == 422
    assert public_request(public, 'GET', PREFIX + '/catalog', absolute=True)[0] == 404
    assert public_request(public, 'GET', '/picture_learning_service.py')[0] == 404
    assert service.pending() == [] and author.submissions == reviewer.submissions == renderer.calls == []


def test_without_picture_executor_catalog_remains_readable_and_creation_is_disabled():
    lab = SimpleNamespace(methods_only=True, csrf_token=CSRF, method_client=None)
    with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                 base_path=BASE, developer_users=[USER])) as public:
        status, catalog, _ = public_request(public, 'GET', PREFIX + '/catalog')
        assert status == 200 and catalog['projects'] == []
        assert catalog['availability']['enabled'] is False and catalog['csrf_token'] == CSRF
        assert re.fullmatch('[0-9a-f]{64}', catalog['owner_key'])
        assert public_request(public, 'POST', PREFIX + '/projects', project_body('missing'))[0] == 503
