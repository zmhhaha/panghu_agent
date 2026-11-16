"""Whole-library release transport crosses both real authenticated HTTP hops."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_service.contracts import canonical_bytes

from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_lab import AnimationLab, LabHTTPServer
from video_workbench.animation_wikiskill_coordinator import WikiSkillCoordinator
from video_workbench.animation_wikiskill_store import AnimationWikiSkillStore
from test_animation_knowledge_http import serving, request, TOKEN, USER, SECOND, ORIGIN, BASE, PREFIX
from test_animation_wikiskill import Gateway, records, finish


LIBRARY = PREFIX + '/knowledge/library'


@pytest.fixture
def peers(tmp_path):
    captures = []
    def capture(owner):
        captures.append(owner)
        return records()
    store = AnimationWikiSkillStore(tmp_path / 'wikiskill.sqlite3', clock=lambda: 1000.)
    gateway = Gateway()
    coordinator = WikiSkillCoordinator(store, gateway, capture, clock=lambda: 1000.)
    def forbidden(*_args, **_kwargs):
        pytest.fail('Retired manual edit or independent method publication reached service')
    service = SimpleNamespace(wikiskill=coordinator, revise_knowledge_page=forbidden, publish=forbidden)
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)) as backend:
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        lab = AnimationLab(tmp_path / 'unused', None, method_client=client, methods_only=True)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin=ORIGIN,
                base_path=BASE, developer_users=[USER, SECOND])) as frontend:
            yield store, gateway, coordinator, captures, backend, client, frontend


def test_get_catalogue_and_recovery_do_not_create_advance_or_charge_jobs(peers):
    store, gateway, coordinator, captures, backend, client, frontend = peers
    status, catalogue, headers = request(frontend, 'GET', LIBRARY)
    assert status == 200 and catalogue['csrf_token'] == frontend.lab.csrf_token
    assert catalogue['releases'] == catalogue['jobs'] == [] and catalogue['active_release_id'] is None
    assert catalogue['owner_key'] and USER not in catalogue['owner_key']
    assert request(frontend, 'GET', LIBRARY + '/active')[1] is None
    missing = str(uuid4())
    assert request(frontend, 'GET', LIBRARY + '/requests/' + missing)[0] == 404
    assert client.wikiskill_catalogue(USER)['jobs'] == []
    assert backend.wikiskill_pump is None
    assert coordinator.pending(limit=100) == []
    assert not captures and not gateway.uploads and not gateway.submissions
    assert TOKEN not in str(catalogue)


def test_publish_start_replay_and_request_get_preserve_original_owner_and_input(peers):
    store, gateway, coordinator, captures, backend, client, frontend = peers
    body = {'request_id': str(uuid4())}
    first = request(frontend, 'POST', LIBRARY + '/jobs', body)
    assert first[0] == 202 and first[1]['state'] == 'queued' and first[1]['owner'] == USER
    assert request(frontend, 'POST', LIBRARY + '/jobs', body)[1] == first[1]
    assert request(frontend, 'GET', LIBRARY + '/requests/' + body['request_id'])[1] == first[1]
    assert request(frontend, 'GET', LIBRARY + '/jobs/' + first[1]['id'])[1] == first[1]
    assert client.wikiskill_job(USER, first[1]['id']) == first[1]
    assert captures == [USER] and not gateway.submissions and not gateway.uploads
    # Owner headers supplied to the browser endpoint cannot replace the
    # authenticated forwarded identity used for the private connection.
    other = request(frontend, 'GET', LIBRARY + '/jobs/' + first[1]['id'],
        extra={'X-Forwarded-User': SECOND, 'X-Animation-Creation-Owner': USER})
    assert other[0] == 404
    second_catalogue = request(frontend, 'GET', LIBRARY, extra={'X-Forwarded-User': SECOND})[1]
    assert second_catalogue['jobs'] == []
    assert second_catalogue['owner_key'] != request(frontend, 'GET', LIBRARY)[1]['owner_key']


@pytest.mark.parametrize('omit,extra', [
    (('X-Animation-Lab-Token',), None), (('Origin',), None),
    ((), {'Origin': 'https://untrusted.invalid'}),
    (('X-Forwarded-User',), None), ((), {'X-Forwarded-User': 'unlisted-user'}),
])
def test_start_requires_authenticated_developer_origin_and_csrf(peers, omit, extra):
    _, gateway, _, captures, _, _, frontend = peers
    assert request(frontend, 'POST', LIBRARY + '/jobs', {'request_id': str(uuid4())},
        omit=omit, extra=extra)[0] == 403
    assert not captures and not gateway.uploads and not gateway.submissions


def test_body_owner_injection_and_private_missing_credentials_are_rejected(peers):
    _, gateway, _, captures, backend, _, frontend = peers
    assert request(frontend, 'POST', LIBRARY + '/jobs',
        {'request_id': str(uuid4()), 'owner': SECOND})[0] == 422
    assert request(backend, 'GET', LIBRARY, private=True, omit=('Authorization',))[0] == 403
    assert request(backend, 'GET', LIBRARY, private=True, omit=('X-Animation-Creation-Owner',))[0] == 403
    assert not captures and not gateway.submissions


def test_exact_release_activation_and_activation_request_recovery_cross_two_hops(peers):
    store, gateway, coordinator, captures, backend, client, frontend = peers
    versions = []
    for _ in range(2):
        started = request(frontend, 'POST', LIBRARY + '/jobs', {'request_id': str(uuid4())})[1]
        done = finish((store, gateway, coordinator, captures), started)
        versions.append(store.get_release(USER, done['release_id']))
    assert request(frontend, 'GET', LIBRARY + '/active')[1] == versions[1]
    assert request(frontend, 'GET', LIBRARY + '/releases/' + versions[0]['id'])[1] == versions[0]
    before = len(gateway.submissions), len(captures)
    body = dict(request_id=str(uuid4()), release_id=versions[0]['id'])
    status, receipt, _ = request(frontend, 'POST', LIBRARY + '/active', body)
    assert status == 200 and receipt['release_sha256'] == versions[0]['sha256']
    assert request(frontend, 'POST', LIBRARY + '/active', body)[1] == receipt
    assert request(frontend, 'GET', LIBRARY + '/activation-requests/' + body['request_id'])[1] == receipt
    assert client.wikiskill_active(USER) == versions[0]
    assert before == (len(gateway.submissions), len(captures))


@pytest.mark.parametrize('path', [PREFIX + '/knowledge/pages/old-page', PREFIX + '/commissions/old-run/publish'])
def test_retired_manual_edit_and_independent_publication_return_410_on_both_hops(peers, path):
    _, gateway, _, captures, backend, client, frontend = peers
    assert request(frontend, 'POST', path, {'request_id': str(uuid4())})[0] == 410
    assert request(backend, 'POST', path, {'request_id': str(uuid4())}, private=True)[0] == 410
    assert not captures and not gateway.submissions


def test_wikiskill_utf8_response_larger_than_legacy_json_budget_is_complete(peers, monkeypatch):
    _, gateway, coordinator, captures, _, client, frontend = peers
    # This fixture exercises transport limits only, not Skill validity. A full
    # real package library consists of many separately bounded UTF-8 files.
    payload = {'fixture_transport': '多层创作知识' * 150000 + '完整结尾',
        'nested': {'media_url': '/api/method-lab/commissions/run/candidates/candidate/media'}}
    monkeypatch.setattr(coordinator, 'get_release', lambda *_args: payload)
    release_id = str(uuid4())
    assert client.wikiskill_release(USER, release_id) == payload
    status, response, headers = request(frontend, 'GET', LIBRARY + '/releases/' + release_id)
    assert status == 200 and response == payload
    assert int(headers['Content-Length']) == len(canonical_bytes(payload))
    assert not captures and not gateway.submissions


def test_response_over_dedicated_budget_fails_before_partial_content(peers, monkeypatch):
    from video_workbench import animation_method_http as transport
    _, gateway, coordinator, captures, _, _, frontend = peers
    monkeypatch.setattr(transport, 'MAX_WIKISKILL_JSON', 1024)
    monkeypatch.setattr(coordinator, 'get_release', lambda *_args: {'private': 'secret-material-' * 200})
    status, error, _ = request(frontend, 'GET', LIBRARY + '/releases/' + str(uuid4()))
    assert status == 413 and error['error'] == 'animation_wikiskill_response_too_large'
    assert 'secret-material' not in str(error) and not captures and not gateway.submissions
