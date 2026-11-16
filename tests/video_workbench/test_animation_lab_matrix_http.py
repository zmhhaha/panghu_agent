"""Authenticated requirement-suite HTTP boundaries with synthetic resources.

These tests use a real local HTTP server and frozen compiler outputs. They do
not execute a browser, render pixels, or approve any animation's appearance.
"""
from copy import deepcopy
import http.client
import json
import threading

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer
from video_workbench.motion import sha256


ORIGIN = 'https://video-service.panghuer.top'
HOST = 'video-service.panghuer.top'
PREFIX = '/dev/animations'
DEVELOPER = 'casdoor:matrix-test-developer'
RESOURCES = {'gsap.min.js': b'/*! GSAP 3.14.2 synthetic matrix HTTP resource */',
             'NotoSansCJK-Regular.ttc': b'ttcf-synthetic-matrix-http'}


class MatrixClient:
    def __init__(self, lab):
        self.lab = lab
        self.server = LabHTTPServer(lab, 0, proxy_mode=True, public_origin=ORIGIN,
                                   base_path=PREFIX+'/', developer_users=[DEVELOPER])
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def get(self, path, *, headers=(), omit=(), defaults=True):
        values = [('Host', HOST), ('X-Forwarded-User', DEVELOPER)] if defaults else []
        values = [(key, value) for key, value in values if key.lower() not in {name.lower() for name in omit}]
        values += list(headers)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=15)
        try:
            connection.putrequest('GET', path, skip_host=True)
            for key, value in values:
                connection.putheader(key, value)
            connection.endheaders()
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw), dict(response.getheaders()), raw
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    client = MatrixClient(AnimationLab(tmp_path_factory.mktemp('matrix-http'), RESOURCES))
    yield client
    client.close()


def test_prefixed_requirement_download_is_lightweight_self_identified_and_read_only(client):
    before_cases = deepcopy(client.lab.cases)
    before_annotations = client.lab.store.list()
    status, suite, headers, raw = client.get(PREFIX+'/api/requirements?seed=20261107&module=all&domain=all&engine=all')
    assert status == 200
    assert suite['schema'] == 'video.animation-lab-requirement-suite.v2'
    assert suite['identity'] == sha256({key: value for key, value in suite.items() if key != 'identity'})
    assert suite['case_count'] == len(suite['cases']) == 39
    assert suite['coverage']['requirements'] == 39
    assert len(suite['coverage']['modules']) == 11
    assert suite['execution_status'] == 'prepared_not_executed'
    assert suite['agent_calls'] == suite['tts_calls'] == suite['production_writes'] == 0
    assert suite['production_adopted'] is False
    assert len(raw) < 128*1024
    assert b'base64' not in raw.lower() and b'data:audio' not in raw and b'index.html' not in raw
    assert b'csrf_token' not in raw and client.lab.csrf_token.encode() not in raw
    assert headers['Content-Type'] == 'application/json; charset=utf-8'
    assert headers['Content-Disposition'] == 'attachment; filename="animation-requirements-'+suite['identity'][:12]+'.json"'
    assert 'no-store' in headers['Cache-Control']
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert client.lab.cases == before_cases and client.lab.store.list() == before_annotations
    for case in suite['cases']:
        current = client.lab.cases[case['case_id']]
        assert case['sample_identity'] == current['sample_identity']
        assert case['lab_snapshot_identity'] == current['identity']
        assert case['input_sha256'] == current['input_sha256']
        assert case['requirement'] == current['requirement']
        assert case['narrative'] == current['narrative']
        assert case['status'] == 'not_run' and case['human_review'] == 'pending'
        assert case['production_admitted'] is False
        assert [row['artifact_sha256'] for row in case['variants']] == [row['artifact_sha256'] for row in current['variants']]


def test_repeatable_download_does_not_rewrite_signed_content_or_claim_execution(client):
    path = PREFIX+'/api/requirements?module=interface&engine=html&seed=17'
    status, suite, _, raw = client.get(path)
    status_again, again, _, raw_again = client.get(path)
    assert status == status_again == 200 and raw == raw_again and suite == again
    assert suite['case_count'] == 4
    assert {row['module'] for row in suite['cases']} == {'interface'}
    assert {row['engine'] for row in suite['cases']} == {'html'}
    assert len({row['domain'] for row in suite['cases']}) == 4
    assert all(row['status'] == 'not_run' for row in suite['cases'])
    _, reordered, _, _ = client.get(PREFIX+'/api/requirements?module=interface&engine=html&seed=18')
    assert {row['case_id'] for row in suite['cases']} == {row['case_id'] for row in reordered['cases']}
    # A seed affects only reviewed-registry ordering; it never creates a new
    # requirement or relabels another artifact as an additional domain.
    by_id = lambda value: {row['case_id']: row for row in value['cases']}
    assert by_id(suite) == by_id(reordered)


def test_known_filter_intersection_can_return_an_honest_empty_suite(client):
    status, suite, headers, _ = client.get(PREFIX+'/api/requirements?module=canvas&engine=html')
    assert status == 200 and suite['case_count'] == 0 and suite['cases'] == []
    assert suite['selection'] == {'module': 'canvas', 'domain': 'all', 'engine': 'html'}
    assert suite['execution_status'] == 'prepared_not_executed'
    assert headers['Content-Disposition'].startswith('attachment;')


@pytest.mark.parametrize('omit,headers,error', [
    (('X-Forwarded-User',), (), 'animation_lab_developer_required'),
    (('X-Forwarded-User',), (('X-Forwarded-User', 'some-other-user'),), 'animation_lab_developer_required'),
    ((), (('X-Forwarded-User', DEVELOPER),), 'animation_lab_developer_required'),
    (('X-Forwarded-User',), (('X-Forwarded-User', 'other'), ('X-Auth-Request-User', DEVELOPER)), 'animation_lab_developer_required'),
    (('Host',), (('Host', 'attacker.invalid'), ('X-Forwarded-Host', HOST)), 'animation_lab_host_rejected'),
    ((), (('Host', HOST),), 'animation_lab_host_rejected'),
    ((), (('Origin', 'https://attacker.invalid'),), 'animation_lab_origin_rejected'),
    ((), (('Origin', ORIGIN), ('Origin', ORIGIN)), 'animation_lab_origin_rejected'),
])
def test_requirement_download_cannot_bypass_developer_identity_or_fixed_origin(client, omit, headers, error):
    status, result, response_headers, _ = client.get(PREFIX+'/api/requirements', omit=omit, headers=headers)
    assert (status, result['error']) == (403, error)
    assert 'Content-Disposition' not in response_headers
    assert 'cases' not in result and 'identity' not in result


@pytest.mark.parametrize('query,error', [
    ('module=canvas&module=three', 'animation_matrix_query_invalid'),
    ('domain=education&domain=commerce', 'animation_matrix_query_invalid'),
    ('engine=html&engine=svg', 'animation_matrix_query_invalid'),
    ('seed=1&seed=2', 'animation_matrix_query_invalid'),
    ('module=charts&module=', 'animation_matrix_query_invalid'),
    ('other=unused', 'animation_matrix_query_invalid'),
    ('seed=', 'animation_matrix_seed_invalid'),
    ('seed=-1', 'animation_matrix_seed_invalid'),
    ('seed=1.5', 'animation_matrix_seed_invalid'),
    ('seed=4294967296', 'animation_matrix_seed_invalid'),
    ('seed=99999999999', 'animation_matrix_seed_invalid'),
    ('module=not-a-module', 'animation_matrix_filter_unknown'),
    ('domain=not-a-domain', 'animation_matrix_filter_unknown'),
    ('engine=not-an-engine', 'animation_matrix_filter_unknown'),
    ('module=', 'animation_matrix_filter_invalid'),
    ('domain=%3Cscript%3E', 'animation_matrix_filter_invalid'),
    ('engine=HTML', 'animation_matrix_filter_invalid'),
])
def test_ambiguous_or_unrecognized_requirement_queries_fail_before_export(client, query, error):
    status, result, headers, _ = client.get(PREFIX+'/api/requirements?'+query)
    assert (status, result['error']) == (422, error)
    assert 'Content-Disposition' not in headers and 'cases' not in result


@pytest.mark.parametrize('path', ['/api/requirements', '/api/requirements?module=all',
    PREFIX+'-other/api/requirements', PREFIX+'/%2e%2e/api/requirements',
    PREFIX+'/api/requirements/extra'])
def test_requirement_routes_cannot_escape_the_developer_prefix(client, path):
    status, result, _, _ = client.get(path)
    assert status == 404 and 'cases' not in result
