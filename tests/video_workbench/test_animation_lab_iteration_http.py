"""HTTP integration: actual auth/prefix/state boundaries with synthetic assets."""
import http.client
import json
import threading

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer
from video_workbench.motion import sha256


PREFIX = '/dev/animations'
HOST = 'video-service.panghuer.top'
ORIGIN = 'https://' + HOST
REVISION = 'a' * 40


@pytest.fixture(scope='module')
def iteration_http(tmp_path_factory):
    lab = AnimationLab(tmp_path_factory.mktemp('iteration-http'),
        {'gsap.min.js': b'synthetic', 'NotoSansCJK-Regular.ttc': b'ttcf-synthetic'},
        source_revision=REVISION)
    server = LabHTTPServer(lab, 0, proxy_mode=True, public_origin=ORIGIN,
        base_path=PREFIX, developer_users=['developer'])
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()

    def request(method, path, body=None, *, user='developer', token=True, raw=None):
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        headers = {'Host': HOST}
        if user:
            headers['X-Forwarded-User'] = user
        if method == 'POST':
            headers.update(Origin=ORIGIN, **{'Content-Type': 'application/json'})
            if token:
                headers['X-Animation-Lab-Token'] = lab.csrf_token
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
        try:
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            data = response.read()
            parsed = json.loads(data) if response.getheader('Content-Type', '').startswith('application/json') else data
            return response.status, parsed, dict(response.getheaders())
        finally:
            connection.close()

    yield lab, request
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_prepare_download_preserves_package_hash_frozen_case_and_sync_cursor(iteration_http):
    lab, request = iteration_http
    case = lab.cases['domain-canvas-clinic']
    before_identity = case['identity']
    status, saved, _ = request('POST', PREFIX+'/api/annotations', dict(case_id=case['id'],
        case_identity=case['identity'], variant_id='candidate', kind='issue', start_seconds=2,
        end_seconds=3, tags=['motion'], severity='note', comment='Synthetic HTTP check; not a visual opinion.'))
    assert status == 201
    status, summary, _ = request('GET', PREFIX+'/api/iterations?module=canvas')
    assert status == 200 and summary['source_revision'] == REVISION
    assert summary['module_cursor'] is None
    assert len(summary['current_effective_feedback']) == 1
    status, task, _ = request('POST', PREFIX+'/api/iterations',
        dict(module='canvas', expected_cursor=summary['current_cursor']))
    assert status == 201 and task['state'] == 'prepared'
    task_id = task['task_id']
    status, package, headers = request('GET', PREFIX+'/api/iterations/'+task_id+'/export')
    assert status == 200 and 'attachment' in headers['Content-Disposition']
    assert headers['Cache-Control'] == 'no-store'
    assert package == task['package']
    assert package['identity'] == sha256({k:v for k,v in package.items() if k != 'identity'})
    assert len(json.dumps(package).encode()) < 30000
    feedback = package['feedback'][0]
    assert feedback['case_identity'] == before_identity
    assert feedback['links']['annotations_url'].startswith(PREFIX+'/api/annotations?')
    assert all(v['id'] in {'baseline','candidate'} for v in feedback['binding']['variants'])
    assert 'base64' not in json.dumps(package) and 'forwarded_user' not in json.dumps(package)
    assert feedback['source']['requirement']['domain'] == 'healthcare'
    assert feedback['source']['requirement']['requirement']['goal'] == case['requirement']['goal']
    assert lab.cases['domain-canvas-clinic']['identity'] == before_identity
    assert request('GET', PREFIX+'/api/iterations?module=canvas')[1]['module_cursor'] is None
    # The same preparation must not duplicate a task, and download is only a read.
    assert request('POST', PREFIX+'/api/iterations', dict(module='canvas'))[1]['task_id'] == task_id
    assert request('GET', PREFIX+'/api/iterations/'+task_id)[1]['events'] == []


def test_iteration_api_has_existing_developer_csrf_and_path_boundaries(iteration_http):
    _, request = iteration_http
    for path in (PREFIX+'/api/iterations', PREFIX+'/api/iterations/'+'a'*64,
                 PREFIX+'/api/iterations/'+'a'*64+'/export'):
        assert request('GET', path, user=None)[0] == 403
        assert request('GET', path, user='other')[0] == 403
    assert request('POST', PREFIX+'/api/iterations', dict(module='canvas'), token=False)[0] == 403
    assert request('GET', '/api/iterations')[0] == 404
    assert request('GET', PREFIX+'/api/iterations?module=canvas&module=three')[0] == 422
    assert request('GET', PREFIX+'/api/iterations?token=secret')[0] == 422
    assert request('GET', PREFIX+'/api/iterations?module=bad')[0] == 422
    assert request('POST', PREFIX+'/api/iterations', raw=b'{"module":"canvas","module":"three"}')[0] == 400


def test_event_binding_conflicts_and_stale_preparation_are_explicit(iteration_http):
    _, request = iteration_http
    assert request('POST', PREFIX+'/api/iterations', dict(module='canvas', expected_cursor='b'*64))[0] == 409
    task = request('POST', PREFIX+'/api/iterations', dict(module='canvas'))[1]
    task_id = task['task_id']
    body = dict(state='synced', expected_state='prepared', idempotency_key='http-sync',
        receipt=dict(task_identity=task_id, module='canvas', source_revision=REVISION, run_id='http-run'))
    assert request('POST', PREFIX+'/api/iterations/'+task_id+'/events', body, token=False)[0] == 403
    status, updated, _ = request('POST', PREFIX+'/api/iterations/'+task_id+'/events', body)
    assert status == 200 and updated['state'] == 'synced'
    assert updated['production_adopted'] is False
    assert updated['events'][0]['actor'] == 'developer'
    assert request('POST', PREFIX+'/api/iterations/'+task_id+'/events', body)[1]['events'] == updated['events']
    wrong = dict(body, idempotency_key='wrong-task', receipt=dict(body['receipt'], task_identity='c'*64))
    assert request('POST', PREFIX+'/api/iterations/'+task_id+'/events', wrong)[0] == 409
    assert request('GET', PREFIX+'/api/iterations?module=canvas')[1]['module_cursor'] == task['package']['next_cursor']
