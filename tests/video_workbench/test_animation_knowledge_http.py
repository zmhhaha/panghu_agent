"""Two-hop Wiki/release transport tests, without Agent or renderer execution."""
from contextlib import contextmanager
import http.client
import json
from threading import Thread
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_creation_http import CreationHTTPServer, CreationTransportError, MAX_JSON
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_knowledge import AnimationKnowledgeStore
from video_workbench.animation_lab import AnimationLab, LabHTTPServer


TOKEN = 'private-wiki-http-fixture-' + 'x' * 32
HOST, ORIGIN = 'video-service.panghuer.top', 'https://video-service.panghuer.top'
USER, SECOND, BASE = 'casdoor:developer', 'casdoor:another', '/dev/animations'
PREFIX = '/api/method-lab'


@contextmanager
def serving(server):
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def request(server, method, path, body=None, *, omit=(), extra=None, private=False):
    headers = ({'Host': '127.0.0.1', 'Authorization': 'Bearer ' + TOKEN,
                'X-Animation-Creation-Owner': USER} if private else
               {'Host': HOST, 'X-Forwarded-User': USER})
    if method == 'POST':
        headers['Content-Type'] = 'application/json'
        if not private:
            headers.update(Origin=ORIGIN)
            headers['X-Animation-Lab-Token'] = server.lab.csrf_token
    for key in omit:
        headers.pop(key, None)
    headers.update(extra or {})
    raw = None if body is None else json.dumps(body).encode()
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        connection.request(method, path if private else BASE + path, body=raw, headers=headers)
        response = connection.getresponse()
        content, metadata = response.read(), dict(response.getheaders())
        return response.status, json.loads(content), metadata
    finally:
        connection.close()


class WikiTransportService:
    def __init__(self, root):
        self.knowledge = AnimationKnowledgeStore(root / 'knowledge.sqlite3')
        self.calls = []
        self.oversized = False

    def knowledge_catalogue(self, owner):
        self.calls.append(('catalogue', owner))
        if self.oversized:
            return {'pages': ['x' * MAX_JSON], 'experiences': []}
        pages = self.knowledge.list_pages(owner)
        records = self.knowledge.list_experiences(owner)
        return dict(pages=[{key: row[key] for key in ('page_id', 'revision', 'status')} for row in pages],
                    experiences=[{key: row[key] for key in ('id', 'kind', 'source_id')} for row in records])

    def knowledge_page(self, owner, page_id):
        self.calls.append(('page', owner, page_id))
        return dict(page=self.knowledge.get_page(owner, page_id), history=[dict(revision=row['revision'])
            for row in self.knowledge.page_history(owner, page_id)[-64:]])

    def knowledge_experience(self, owner, record_id):
        self.calls.append(('experience', owner, record_id))
        return self.knowledge.get_experience(owner, record_id)

    def knowledge_page_revision(self, owner, page_id, revision):
        self.calls.append(('page_revision', owner, page_id, revision))
        return dict(page=self.knowledge.get_page(owner, page_id, revision=revision),
                    current_revision=self.knowledge.get_page(owner, page_id)['revision'], evidence_issues=[])

    def revise_knowledge_page(self, owner, page_id, body):
        self.calls.append(('revise', owner, page_id))
        body = dict(body)
        operation_id = body.pop('request_id')
        if 'owner' in body:
            raise ServiceError('invalid_animation_knowledge', 422)
        content = body.pop('body')
        return self.knowledge.revise_page(owner, page_id, content, operation_id=operation_id, **body)

    def publication_readiness(self, owner, method_version_id):
        self.calls.append(('readiness', owner, method_version_id))
        return dict(method_version_id=method_version_id, ready=False, reasons=['not_human_accepted'])

    def published_catalogue(self, owner):
        self.calls.append(('published', owner))
        return dict(methods=[dict(id='method:1', status='published', skill_package_sha256='a' * 64)])

    def get_method_version(self, owner, method_version_id):
        self.calls.append(('method_version', owner, method_version_id))
        return dict(id=method_version_id, status='draft', skill_package={'files': {'SKILL.md': 'Synthetic full body.'}})

    def accept_validation(self, owner, validation_id, body):
        self.calls.append(('accept', owner, validation_id, body))
        return dict(validation_id=validation_id, accepted=True, published=False)


@pytest.fixture
def peers(tmp_path):
    service = WikiTransportService(tmp_path)
    record = service.knowledge.record_experience(USER, 'failure', 'actual-fixture-run',
                                               {'error': 'Synthetic observed failure.', 'status': 'failed'})
    service.knowledge.revise_page(USER, 'draw:async', {'title': 'Async draw', 'content': 'Preserve the observed failure.'},
        expected_revision=0, evidence_ids=[record['id']], change_note='Provisional lesson.', operation_id='initial')
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)) as backend:
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        lab = AnimationLab(tmp_path / 'unused', None, method_client=client, methods_only=True)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin=ORIGIN,
                     base_path=BASE, developer_users=[USER, SECOND])) as frontend:
            yield service, backend, client, frontend, record


def revision_body(record):
    return dict(request_id='explicit-correction', expected_revision=1,
        body={'title': 'Async draw', 'content': 'A fixed delay did not establish completion.'},
        evidence_ids=[record['id']], change_kind='correct', change_note='Correct the provisional interpretation.')


def test_wiki_reads_cross_both_authenticated_hops_without_work_or_writes(peers):
    service, backend, client, frontend, record = peers
    initial = service.knowledge.get_page(USER, 'draw:async')
    status, catalogue, _ = request(frontend, 'GET', PREFIX + '/knowledge')
    assert status == 200 and catalogue['csrf_token'] == frontend.lab.csrf_token
    assert catalogue['pages'] == [dict(page_id='draw:async', revision=1, status='provisional')]
    assert 'document' not in catalogue['experiences'][0] and 'body' not in catalogue['pages'][0]
    for _ in range(2):
        status, value, _ = request(frontend, 'GET', PREFIX + '/knowledge/pages/draw%3Aasync')
        assert status == 200 and value['page'] == initial and value['history'] == [{'revision': 1}]
        assert request(frontend, 'GET', PREFIX + '/knowledge/experiences/' + record['id'])[1] == record
    assert len(service.knowledge.page_history(USER, 'draw:async')) == 1
    assert len(service.knowledge.list_experiences(USER)) == 1
    assert client.knowledge_page(USER, 'draw:async')['page'] == initial
    assert backend.method_pump is None
    assert all(row[1] == USER for row in service.calls)
    assert TOKEN not in json.dumps(catalogue)


def test_wiki_explicit_revision_uses_cas_idempotency_and_never_schedules(peers):
    service, backend, _, frontend, record = peers
    path, body = PREFIX + '/knowledge/pages/draw%3Aasync', revision_body(record)
    first = request(frontend, 'POST', path, body)
    assert first[0] == 200 and first[1]['revision'] == 2 and first[1]['change_kind'] == 'correct'
    assert request(frontend, 'POST', path, body)[1] == first[1]
    changed = dict(body, request_id='another-request')
    assert request(frontend, 'POST', path, changed)[0] == 409
    assert len(service.knowledge.page_history(USER, 'draw:async')) == 2
    assert backend.method_pump is None


def test_historical_reads_return_original_body_through_both_hops_without_writes(peers):
    service, backend, client, frontend, record = peers
    initial = service.knowledge.get_page(USER, 'draw:async')
    body = revision_body(record)
    assert request(frontend, 'POST', PREFIX + '/knowledge/pages/draw%3Aasync', body)[0] == 200
    service.calls.clear()
    path = PREFIX + '/knowledge/pages/draw%3Aasync/revisions/1'
    status, historical, _ = request(frontend, 'GET', path)
    assert status == 200 and historical['page'] == initial and historical['current_revision'] == 2
    assert historical['page']['body']['content'] != body['body']['content']
    assert client.knowledge_page_revision(USER, 'draw:async', 1) == historical
    assert service.calls == [('page_revision', USER, 'draw:async', 1)] * 2
    assert request(frontend, 'GET', path, extra={'X-Forwarded-User': SECOND})[0] == 404
    assert request(frontend, 'GET', path.removesuffix('/1') + '/3')[0] == 404
    assert request(frontend, 'POST', path, body)[0] == 404
    assert len(service.knowledge.page_history(USER, 'draw:async')) == 2
    assert len(service.knowledge.list_experiences(USER)) == 1
    assert backend.method_pump is None


@pytest.mark.parametrize('revision', ['0', '-1', '01', '1.0', '1000001', '99999999', 'true'])
def test_historical_revision_rejects_ambiguous_or_out_of_range_numbers_before_service(peers, revision):
    service, backend, _, frontend, _ = peers
    path = PREFIX + '/knowledge/pages/draw:async/revisions/' + revision
    assert request(frontend, 'GET', path)[0] == 422
    assert request(backend, 'GET', path, private=True)[0] == 422
    assert service.calls == []


def test_historical_client_requires_integer_and_valid_id_before_network(peers):
    service, _, client, _, _ = peers
    for revision in (0, -1, True, 1.0, '1', 1000001):
        with pytest.raises(CreationTransportError, match='revision_invalid'):
            client.knowledge_page_revision(USER, 'draw:async', revision)
    for identity in ('../escape', 'with/slash', 'encoded%2Fslash', ''):
        with pytest.raises(CreationTransportError, match='identifier_invalid'):
            client.knowledge_page_revision(USER, identity, 1)
    assert service.calls == []


@pytest.mark.parametrize('omitted,extra', [
    (['X-Forwarded-User'], {}), ([], {'X-Forwarded-User': 'unknown'}),
    ([], {'Host': 'evil.invalid'}), (['Origin'], {}), ([], {'Origin': 'https://evil.invalid'}),
    (['X-Animation-Lab-Token'], {}), ([], {'X-Animation-Lab-Token': 'wrong'}),
    ([], {'Content-Type': 'text/plain'}),
])
def test_wiki_write_retains_developer_origin_and_csrf_guards(peers, omitted, extra):
    service, _, _, frontend, record = peers
    status = request(frontend, 'POST', PREFIX + '/knowledge/pages/draw:async', revision_body(record),
                     omit=omitted, extra=extra)[0]
    assert status in {403, 415}
    assert service.calls == []
    assert len(service.knowledge.page_history(USER, 'draw:async')) == 1


def test_wiki_owner_isolation_survives_public_proxy_and_private_token(peers):
    service, backend, _, frontend, record = peers
    assert request(frontend, 'GET', PREFIX + '/knowledge', extra={'X-Forwarded-User': SECOND})[1]['pages'] == []
    for suffix in ('pages/draw:async', 'experiences/' + record['id']):
        assert request(frontend, 'GET', PREFIX + '/knowledge/' + suffix,
                       extra={'X-Forwarded-User': SECOND})[0] == 404
    assert request(frontend, 'POST', PREFIX + '/knowledge/pages/draw:async',
                   dict(revision_body(record), owner=SECOND))[0] == 422
    before = len(service.calls)
    assert request(backend, 'GET', PREFIX + '/knowledge', private=True, omit=['Authorization'])[0] == 403
    assert request(backend, 'GET', PREFIX + '/knowledge', private=True,
                   omit=['X-Animation-Creation-Owner'])[0] == 403
    assert len(service.calls) == before


def test_readiness_is_read_only_and_acceptance_is_explicit_csrf_write(peers):
    service, backend, client, frontend, _ = peers
    readiness = request(frontend, 'GET', PREFIX + '/methods/method:1/readiness')
    assert readiness[0] == 200 and readiness[1]['ready'] is False
    assert service.calls == [('readiness', USER, 'method:1')]
    assert client.publication_readiness(USER, 'method:1')['ready'] is False
    body = dict(request_id='human-decision', method_content_sha256='a' * 64,
                comment='Explicit fixture decision.', human_verification={'motion_watched': True})
    path = PREFIX + '/validations/validation:1/accept'
    assert request(frontend, 'POST', path, body, omit=['X-Animation-Lab-Token'])[0] == 403
    assert not any(row[0] == 'accept' for row in service.calls)
    status, decision, _ = request(frontend, 'POST', path, body)
    assert status == 200 and decision == dict(validation_id='validation:1', accepted=True, published=False)
    assert service.calls[-1] == ('accept', USER, 'validation:1', body)
    assert backend.method_pump is None


def test_method_catalogue_uses_summary_adapter_and_full_version_is_explicit(peers):
    service, _, client, frontend, _ = peers
    status, value, _ = request(frontend, 'GET', PREFIX + '/methods/published')
    assert status == 200 and 'skill_package' not in value['methods'][0]
    assert service.calls == [('published', USER)]
    status, version, _ = request(frontend, 'GET', PREFIX + '/methods/method%3A1')
    assert status == 200 and version['skill_package']['files']['SKILL.md'] == 'Synthetic full body.'
    assert client.method_version(USER, 'method:1') == version
    assert service.calls[-1] == ('method_version', USER, 'method:1')


def test_knowledge_api_does_not_expose_snapshot_or_production_skill_reads(peers):
    service, _, _, frontend, _ = peers
    for suffix in ('/knowledge/snapshots/arbitrary', '/knowledge?refresh=1',
                   '/knowledge/pages/draw%2Fasync', '/methods/missing/readiness?run=1'):
        assert request(frontend, 'GET', PREFIX + suffix)[0] in {404, 422}
    assert request(frontend, 'POST', PREFIX + '/methods/read', {})[0] == 404
    assert service.calls == []


def test_oversized_adapter_response_is_bounded_before_private_transport(peers):
    service, _, _, frontend, _ = peers
    service.oversized = True
    status, error, _ = request(frontend, 'GET', PREFIX + '/knowledge')
    assert status == 413 and error == {'error': 'animation_method_response_too_large'}


def test_client_rejects_non_identifier_route_segments_before_network(peers):
    service, _, client, _, _ = peers
    for identity in ('../escape', 'with/slash', 'encoded%2Fslash', '', 'with?query'):
        with pytest.raises(CreationTransportError, match='identifier_invalid'):
            client.knowledge_page(USER, identity)
    assert service.calls == []
