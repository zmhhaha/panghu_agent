"""Exercise the developer annotation API over real loopback HTTP and SQLite."""
import base64
import hashlib
import http.client
import json
import threading

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer
import video_workbench.animation_lab as animation_lab_module


ASSETS = {'gsap.min.js': b'fake-runtime-for-api-tests',
          'NotoSansCJK-Regular.ttc': b'ttcf-fake-font-for-api-tests'}


class Client:
    def __init__(self, lab):
        self.lab = lab
        self.server = LabHTTPServer(lab, 0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.catalog = self.request('GET', '/api/catalog')[1]

    def request(self, method, path, body=None, headers=None, raw=None):
        supplied = {}
        if method == 'POST':
            supplied.update({'Origin': 'http://' + self.host,
                'X-Animation-Lab-Token': self.lab.csrf_token,
                'Content-Type': 'application/json'})
        for key, value in (headers or {}).items():
            if value is None:
                supplied.pop(key, None)
            else:
                supplied[key] = value
        payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, payload, supplied)
            response = connection.getresponse()
            data = response.read()
            status, response_headers = response.status, dict(response.getheaders())
        finally:
            connection.close()
        if response_headers.get('Content-Type', '').startswith('application/json'):
            data = json.loads(data)
        return status, data, response_headers

    def issue(self):
        case = next(row for row in self.catalog['cases'] if row['id'] == 'diagram-connections')
        return dict(case_id=case['id'], case_identity=case['identity'], variant_id='candidate',
            kind='issue', start_seconds=5.25, end_seconds=6, region=dict(x=.1, y=.2, width=.3, height=.4),
            tags=['connector'], severity='major', comment='这条线遮挡了说明文字')

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)


@pytest.fixture
def lab_factory(tmp_path):
    clients = []

    def create(*, assets=None, name='data'):
        client = Client(AnimationLab(tmp_path / name, ASSETS if assets is None else assets))
        clients.append(client)
        return client

    yield create
    for client in reversed(clients):
        client.close()


def test_annotation_rejects_obsolete_comparison_after_real_runtime_change(lab_factory):
    old = lab_factory()
    body = old.issue()
    assert old.request('POST', '/api/annotations', body)[0] == 201
    old.close()
    # Use the same database but a genuinely different runtime fingerprint.
    current = lab_factory(assets={**ASSETS, 'gsap.min.js': b'new-test-runtime'})
    assert current.issue()['case_identity'] != body['case_identity']
    status, value, _ = current.request('POST', '/api/annotations', body)
    assert (status, value['error']) == (409, 'animation_lab_case_changed')
    records = current.request('GET', '/api/annotations')[1]['annotations']
    assert len(records) == 1 and records[0]['case_identity'] == body['case_identity']


def test_case_title_edit_opens_same_database_as_new_experiment(lab_factory, monkeypatch):
    old = lab_factory()
    body = old.issue()
    assert old.request('POST', '/api/annotations', body)[0] == 201
    old.close()
    original_catalog = animation_lab_module.catalog

    def revised_catalog():
        cases = original_catalog()
        cases[0]['title'] += '（更清楚的样例名称）'
        return cases

    monkeypatch.setattr(animation_lab_module, 'catalog', revised_catalog)
    current = lab_factory()
    assert current.issue()['case_identity'] != body['case_identity']
    assert current.request('POST', '/api/annotations', body)[0] == 409
    records = current.request('GET', '/api/annotations')[1]['annotations']
    assert len(records) == 1 and records[0]['case_identity'] == body['case_identity']


@pytest.mark.parametrize('patch', [
    {'start_seconds': float('nan')}, {'end_seconds': float('inf')},
    {'start_seconds': True}, {'start_seconds': -1}, {'end_seconds': 99},
    {'end_seconds': 5}, {'variant_id': ['candidate']}, {'kind': 'train'},
    {'region': {'x': 0, 'y': 0, 'width': 1.1, 'height': .2}},
    {'region': {'x': .9, 'y': 0, 'width': .2, 'height': .2}},
    {'region': {'x': 0, 'y': 0, 'width': False, 'height': .2}},
    {'region': {'x': 0, 'y': float('nan'), 'width': .2, 'height': .2}},
    {'region': {'x': 0, 'y': 0, 'width': .2}},
    {'tags': ['connector', 'connector']}, {'comment': {}}, {'unexpected': 'field'},
])
def test_malformed_annotations_are_rejected_without_writing(lab_factory, patch):
    client = lab_factory()
    body = dict(client.issue(), **patch)
    status, value, _ = client.request('POST', '/api/annotations', body)
    assert status == 422 and value['error'].startswith('animation_lab_')
    assert client.request('GET', '/api/annotations')[1] == {'annotations': []}


def test_issue_preference_review_retraction_and_frozen_export_survive_restart(lab_factory):
    client = lab_factory()
    issue = client.issue()
    preference = dict(issue, kind='preference', variant_id='comparison', region=None,
                      preference='candidate', comment='右侧更容易理解')
    review = dict(issue, kind='review', verdict='needs_work', comment='继续改善文字层次')
    results = []
    for body in (issue, preference, review):
        status, data, _ = client.request('POST', '/api/annotations', body)
        assert status == 201
        results.append(data['annotation'])
    assert results[0]['start_frame'] == 126 and results[0]['end_frame'] == 144
    assert results[1]['preference'] == 'candidate'
    assert results[2]['verdict'] == 'needs_work'
    assert all(row['review_scope'] == 'developer_visual_only' for row in results)
    annotation_id = results[0]['id']
    route = '/api/annotations/' + annotation_id + '/retract'
    withdrawn = client.request('POST', route, {'reason': '重新观看后撤回此问题'})[1]['annotation']
    assert withdrawn['retracted'] is True
    assert client.request('POST', route, {'reason': 'second reason'})[1]['annotation']['retraction'] == withdrawn['retraction']
    client.close()
    restarted = lab_factory()
    rows = restarted.request('GET', '/api/annotations?case_id=diagram-connections')[1]['annotations']
    assert [row['id'] for row in rows] == [row['id'] for row in results]
    assert rows[0]['retracted'] and rows[0]['comment'] == issue['comment']
    assert rows[0]['retraction'] == withdrawn['retraction']
    status, raw, headers = restarted.request('GET', '/api/export?format=jsonl')
    assert status == 200 and 'attachment;' in headers['Content-Disposition']
    exported = [json.loads(line) for line in raw.splitlines()]
    snapshot = next(row['snapshot'] for row in exported if row['record_type'] == 'case_snapshot'
                    and row['snapshot']['identity'] == issue['case_identity'])
    assert snapshot['case_id'] == issue['case_id']
    assert snapshot['runtime']['assets'] == {name: hashlib.sha256(raw).hexdigest() for name, raw in ASSETS.items()}
    assert snapshot['review_scope'] == 'developer_visual_only'
    for variant in snapshot['variants']:
        assert variant['inputs']['inputs']['plan']
        assert variant['tts_calls'] == variant['agent_calls'] == 0
        for clip in variant['clips']:
            for name, document in clip['files'].items():
                assert document['encoding'] == 'base64'
                actual = base64.b64decode(document['data'], validate=True)
                assert hashlib.sha256(actual).hexdigest() == document['sha256']
                if name == 'index.html':
                    assert b'video-motion' in actual
    exported_rows = [row for row in exported if row['record_type'] == 'annotation']
    assert len(exported_rows) == 3 and exported_rows[0]['effective'] is False
    assert all(row['effective'] for row in exported_rows[1:])


@pytest.mark.parametrize('headers,error', [
    ({'Host': 'attacker.invalid'}, 'animation_lab_host_rejected'),
    ({'Origin': 'http://attacker.invalid'}, 'animation_lab_origin_rejected'),
    ({'Origin': None}, 'animation_lab_origin_required'),
    ({'X-Animation-Lab-Token': None}, 'animation_lab_token_required'),
    ({'X-Animation-Lab-Token': 'wrong'}, 'animation_lab_token_required'),
    ({'X-Animation-Lab-Token': '\xff'}, 'animation_lab_token_required'),
])
def test_http_write_protection_rejects_untrusted_requests(lab_factory, headers, error):
    client = lab_factory()
    status, value, _ = client.request('POST', '/api/annotations', client.issue(), headers=headers)
    assert (status, value['error']) == (403, error)
    assert client.request('GET', '/api/annotations')[1] == {'annotations': []}


@pytest.mark.parametrize('path', ['/animation-lab.db', '/data/animation-lab.db', '/../animation-lab.db',
                                  '/%2e%2e/animation-lab.db', '/composition/diagram-connections/candidate/scene-1/../animation-lab.db'])
def test_fixed_get_routes_never_serve_the_annotation_database(lab_factory, path):
    client = lab_factory()
    status, value, _ = client.request('GET', path)
    assert status == 404
    assert value['error'].startswith('animation_lab_')
    assert 'SQLite' not in str(value)
