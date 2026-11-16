"""Portable sample identities versus actual HTTP lab snapshots.

HTTP uses the real lab/server and compiled matrix. Assets and runtime metadata
are explicit synthetic fixtures; these checks make no browser/render claim.
"""
from copy import deepcopy
import http.client
import json
import threading

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer
from video_workbench.animation_lab_evaluate import collect, load_run
from video_workbench.animation_lab_matrix import SCHEMA, requirement_suite, prepare_suite
from video_workbench.animation_lab_samples import catalog, compile_sample
from video_workbench.motion import sha256


@pytest.fixture(scope='module')
def matrix_http(tmp_path_factory):
    lab = AnimationLab(tmp_path_factory.mktemp('matrix-v2'),
        {'gsap.min.js': b'synthetic-not-executed', 'NotoSansCJK-Regular.ttc': b'ttcf-synthetic'},
        source_revision='a'*40)
    server = LabHTTPServer(lab, 0, proxy_mode=True,
        public_origin='https://video-service.panghuer.top',
        base_path='/dev/animations', developer_users=['developer'])
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()

    def get(path):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=20)
        try:
            connection.request('GET', '/dev/animations'+path,
                headers={'Host': 'video-service.panghuer.top', 'X-Forwarded-User': 'developer'})
            response = connection.getresponse()
            raw = response.read()
            assert response.status == 200, raw
            return json.loads(raw), dict(response.getheaders())
        finally:
            connection.close()

    yield lab, get
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.mark.parametrize('module', ['algorithms', 'math', 'spatial', 'fields'])
def test_http_cli_sample_bindings_and_semantic_identity_match(matrix_http, module):
    lab, get = matrix_http
    web, headers = get('/api/requirements?module='+module)
    cli = requirement_suite(catalog(), module=module, runtime=lab.runtime)
    assert web['schema'] == cli['schema'] == SCHEMA == 'video.animation-lab-requirement-suite.v2'
    assert web['semantic_identity'] == cli['semantic_identity']
    assert web['identity'] != cli['identity']
    assert web['export_scope'] == 'lab_snapshots' and cli['export_scope'] == 'compiled_samples'
    assert web['execution_status'] == cli['execution_status'] == 'prepared_not_executed'
    assert web['agent_calls'] == web['tts_calls'] == web['production_writes'] == 0
    assert headers['Content-Disposition'].startswith('attachment;')
    assert web['identity'] == sha256({k: v for k, v in web.items() if k != 'identity'})
    by_id = {row['case_id']: row for row in cli['cases']}
    assert len(by_id) == len(web['cases']) == 4
    for row in web['cases']:
        source = by_id[row['case_id']]
        bundle = compile_sample(row['case_id'], 'candidate')
        assert row['sample_identity'] == source['sample_identity'] == bundle['identity']
        assert row['lab_snapshot_identity'] == lab.cases[row['case_id']]['identity']
        assert row['sample_identity'] != row['lab_snapshot_identity']
        assert row['input_sha256'] == source['input_sha256'] == bundle['input_sha256']
        assert row['variants'] == source['variants'] and row['requirement'] == source['requirement']
        assert 'lab_snapshot_identity' not in source
        assert 'case_identity' not in row and 'case_identity' not in source


def test_http_matrix_exposes_only_the_39_current_requirement_samples(matrix_http):
    _, get = matrix_http
    doc, _ = get('/api/catalog')
    rows = [r for r in doc['cases'] if not r.get('evaluation')]
    assert len(rows) == 39
    assert all(row['is_matrix_case'] for row in rows)
    assert len({r['module'] for r in rows}) == 11
    assert all(r['sample_identity'] and r['lab_snapshot_identity'] for r in rows)


def test_semantic_identity_tracks_requirements_inputs_and_artifacts_but_not_runtime():
    rows = [r for r in catalog() if r['module'] == 'algorithms']
    original = requirement_suite(rows, runtime={'environment': 'A'})
    runtime_changed = requirement_suite(rows, runtime={'environment': 'B'})
    assert original['semantic_identity'] == runtime_changed['semantic_identity']
    assert original['identity'] != runtime_changed['identity']
    for mutate in (
        lambda r: r.update(input_sha256='1'*64),
        lambda r: r.update(sample_identity='2'*64),
        lambda r: r['requirement'].update(goal='另一个表达目标'),
        lambda r: r['variants'][0].update(artifact_sha256='3'*64),
    ):
        changed = deepcopy(rows)
        mutate(changed[0])
        result = requirement_suite(changed, runtime={'environment': 'A'})
        assert result['semantic_identity'] != original['semantic_identity']


def test_snapshot_identity_cannot_substitute_for_explicit_sample_identity():
    rows = [r for r in catalog() if r['module'] == 'algorithms']
    missing = deepcopy(rows)
    missing[0].pop('sample_identity')
    missing[0]['identity'] = 'a'*64
    with pytest.raises(ValueError, match='sample_identity_required'):
        requirement_suite(missing)
    malformed = deepcopy(rows)
    malformed[0]['lab_snapshot_identity'] = 'not-a-digest'
    with pytest.raises(ValueError, match='lab_snapshot_identity_invalid'):
        requirement_suite(malformed)


def test_prepared_children_bind_to_suite_without_claiming_capture_execution(tmp_path):
    runtime = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
        hyperframes_source='synthetic_contract_fixture', node='synthetic', browser='synthetic',
        ffmpeg='synthetic', gsap='3.14.2', gsap_sha256='a'*64,
        font_sha256='b'*64, npm_lock_sha256='c'*64, dpkg_sha256='d'*64,
        image_digest='sha256:'+'e'*64)
    root = tmp_path/'suite'
    receipt = prepare_suite(root, runtime, module='continuity', domain='education')
    suite = json.loads((root/'suite.json').read_bytes())
    assert receipt['schema'] == 'video.animation-lab-suite-preparation.v2'
    assert receipt['suite_identity'] == suite['identity']
    assert receipt['suite_semantic_identity'] == suite['semantic_identity']
    assert receipt['identity'] == sha256({k: v for k, v in receipt.items() if k != 'identity'})
    assert receipt['status'] == 'prepared_not_executed' and receipt['captures'] == 6
    assert receipt['agent_calls'] == receipt['tts_calls'] == receipt['production_writes'] == 0
    assert len(receipt['runs']) == suite['case_count'] == 1
    item = receipt['runs'][0]
    run = json.loads((root/item['case_id']/'run.json').read_bytes())
    assert item['run_identity'] == run['identity']
    assert item['sample_identity'] == suite['cases'][0]['sample_identity']
    assert all(bundle['identity'] == item['sample_identity'] for bundle in run['bundles'])
    assert receipt['captures'] == len(run['captures'])
    child_root = root/item['case_id']
    loaded, bundles = load_run(child_root)
    assert loaded == run
    assert {bundle['variant_id']: bundle['artifact_sha256'] for bundle in bundles} == {
        variant['id']: variant['artifact_sha256'] for variant in suite['cases'][0]['variants']}
    with pytest.raises(ValueError, match='animation_lab_capture_file_invalid'):
        collect(child_root)
    assert not (child_root/'technical.json').exists()
    assert not (child_root/'review-request.json').exists()
