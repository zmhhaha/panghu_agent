"""Frozen evaluation/triage contracts using explicitly synthetic observations.

Only prepare() compiles real sample bundles. PNGs, technical observations and
model opinions below are unit fixtures, not actual captures or quality evidence.
No renderer, TTS, external model or Agent is invoked by these tests.
"""
import base64
from copy import deepcopy
import http.client
import json
import struct
import threading
import zlib

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer
from video_workbench.animation_lab_evaluate import load_run, prepare
from video_workbench.animation_lab_evaluation_view import frame_bytes, load_evaluation
from video_workbench.animation_lab_review import (
    REVIEW_SCHEMA, build_review_request, validate_review_output,
)
from video_workbench.motion import canonical, sha256


ASSETS = {'gsap.min.js': b'synthetic-evaluation-test-runtime',
          'NotoSansCJK-Regular.ttc': b'ttcf-synthetic-evaluation-test-font'}
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='synthetic_contract_fixture', node='synthetic',
    browser='synthetic', ffmpeg='synthetic', gsap='3.14.2',
    gsap_sha256=sha256(ASSETS['gsap.min.js']),
    font_sha256=sha256(ASSETS['NotoSansCJK-Regular.ttc']),
    npm_lock_sha256='c'*64, dpkg_sha256='d'*64, image_digest='sha256:'+'e'*64)


def _png(color):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind+data)))
    scanline = b'\0' + bytes(color)*1280
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1280, 720, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanline*720)) + chunk(b'IEND', b''))


def _save(root, name, value):
    (root/name).write_bytes(canonical(value))


def _model_review(fixture, *, issues=True):
    request = fixture['request']
    findings = []
    for label, variant in request['private']['variants'].items():
        if not issues:
            continue
        refs = [row['id'] for row in request['public']['frames'] if row['variant'] == label]
        findings.append(dict(id='synthetic-'+variant['id'], variant=label, category='layout',
            severity='major', claim='static', frame_ids=[refs[-1]],
            region=dict(x=.1, y=.2, width=.3, height=.4),
            description='Synthetic fixture issue for '+variant['id'], confidence=.7))
    suggestions = [dict(issue_id=row['id'], kind='improve_layout',
        parameters=dict(layout_template='flow'), reason='Synthetic bounded layout suggestion')
        for row in findings] if fixture['run']['module'] == 'diagram' else []
    if not suggestions:
        suggestions = [dict(issue_id=None, kind='no_change', parameters={},
                            reason='Synthetic fixture; no executable change')]
    opinion = dict(schema=REVIEW_SCHEMA, request_identity=request['identity'], preference='tie',
        comparison_reason='Synthetic unit fixture comparison, not aesthetic approval',
        comparison_frame_ids=[row['id'] for row in request['public']['frames']],
        issues=findings, suggestions=suggestions,
        limitations=['Synthetic unit images and opinions; no real capture or quality approval.'])
    result = validate_review_output(opinion, request)
    _save(fixture['root'], 'model-review.json', result)
    return result


@pytest.fixture
def evaluation_factory(tmp_path):
    def create(*, case_id='diagram-connections', name='run-one', technical_issue=True,
               model='reviewed', model_issues=True):
        root = tmp_path/'evaluations'/name
        run = prepare(root, deepcopy(RUNTIME), case_id=case_id)
        _, bundles = load_run(root)
        reports, frames, images = [], [], {}
        for side, bundle in enumerate(bundles):
            offset = 0
            for index, clip in enumerate(bundle['clips']):
                raw = _png((20+side*80, 30+index*50, 40))
                source_id = bundle['variant_id']+':'+clip['id']+':seek-first'
                target = root/'inputs'/bundle['variant_id']/clip['id']/'observations'/'seek-first.png'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                images[source_id] = raw
                frames.append(dict(id=source_id, variant_id=bundle['variant_id'], clip_id=clip['id'],
                    time_seconds=offset+(.25 if side == 0 else .75), sha256=sha256(raw),
                    mime_type='image/png', width=1280, height=720))
                failed = technical_issue and side == 1 and index == 0
                report = dict(schema='synthetic.observation.contract-fixture.v1',
                    variant_id=bundle['variant_id'], clip_id=clip['id'],
                    sample_identity=bundle['identity'], artifact_sha256=bundle['artifact_sha256'],
                    input_sha256=bundle['input_sha256'], runtime=deepcopy(run['runtime']),
                    technical_status='failed' if failed else 'passed',
                    issues=[dict(code='synthetic_fixture_failure')] if failed else [], frame_count=1,
                    visual_frames=[dict(frame_id=clip['id']+':seek-first', clip_id=clip['id'],
                        path='observations/seek-first.png', global_time_seconds=frames[-1]['time_seconds'],
                        time_seconds=frames[-1]['time_seconds']-offset, sha256=sha256(raw),
                        mime_type='image/png')])
                reports.append(dict(report, evidence_identity=sha256(report)))
                offset += clip['duration_seconds']
        technical = dict(schema='video.animation-lab-evaluation-technical.v1',
            run_identity=run['identity'], status='failed' if technical_issue else 'passed', reports=reports,
            objective_scope='synthetic_contract_fixture_not_actual_observations',
            visual_quality='not_evaluated', production_adopted=False)
        request = build_review_request(case_identity=run['identity'], module=run['module'],
            variants=[dict(id=bundle['variant_id'], artifact_sha256=bundle['artifact_sha256'],
                           duration_seconds=bundle['duration_seconds']) for bundle in bundles],
            frames=frames, runtime_identity=run['runtime_identity'], evidence_identity=sha256(technical))
        _save(root, 'technical.json', technical)
        _save(root, 'review-request.json', request)
        (root/'report.html').write_text('<!doctype html><p>Synthetic unit fixture, not quality evidence.</p>')
        fixture = dict(root=root, run=run, bundles=bundles, technical=technical,
                       request=request, frames=frames, images=images)
        if model == 'reviewed':
            _model_review(fixture, issues=model_issues)
        elif model != 'not_run':
            name = {'unavailable': 'model-unavailable.json', 'failed': 'model-failure.json',
                    'unfinished_attempt': 'model-attempt.json'}[model]
            _save(root, name, dict(request_identity=request['identity'], scope='synthetic_unit_fixture'))
        return fixture
    return create


class Client:
    def __init__(self, lab):
        self.lab = lab
        self.server = LabHTTPServer(lab, 0)
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.host = '127.0.0.1:'+str(self.server.server_port)
        self.catalog = self.request('GET', '/api/catalog')[1]

    def request(self, method, path, body=None):
        headers = {}
        if method == 'POST':
            headers = {'Origin': 'http://'+self.host, 'X-Animation-Lab-Token': self.lab.csrf_token,
                       'Content-Type': 'application/json'}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, None if body is None else canonical(body), headers)
            response = connection.getresponse()
            data, status = response.read(), response.status
            response_headers = dict(response.getheaders())
        finally:
            connection.close()
        if response_headers.get('Content-Type', '').startswith('application/json'):
            data = json.loads(data)
        return status, data, response_headers

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)


@pytest.fixture
def client_factory(tmp_path):
    clients = []

    def create(fixture, *, assets=None, data_name='data'):
        lab = AnimationLab(tmp_path/data_name, ASSETS if assets is None else assets,
                           evaluations_dir=fixture['root'].parent)
        client = Client(lab)
        clients.append(client)
        return client
    yield create
    for client in reversed(clients):
        client.close()


def _case(client):
    evaluation = client.catalog['evaluations'][0]
    return next(row for row in client.catalog['cases'] if row['id'] == evaluation['case_id'])


def _decision(report, finding, decision, comment='Synthetic developer decision'):
    return dict(report_identity=report['report_identity'], finding_id=finding['id'],
                decision=decision, comment=comment)


def test_frozen_comparison_uses_candidate_and_layout_focus_not_normal_catalog(evaluation_factory, client_factory):
    fixture = evaluation_factory()
    client = client_factory(fixture)
    frozen = _case(client)
    normal = next(row for row in client.catalog['cases'] if row['id'] == 'diagram-connections')
    assert {row['id'] for row in normal['variants']} == {'baseline', 'candidate'}
    assert {row['id'] for row in frozen['variants']} == {'candidate', 'layout-focus'}
    assert frozen['id'] != normal['id']
    expected_order = [row['id'] for _, row in sorted(fixture['request']['private']['variants'].items())]
    assert [row['id'] for row in frozen['variants']] == expected_order
    for variant in frozen['variants']:
        bundle = next(row for row in fixture['bundles'] if row['variant_id'] == variant['id'])
        assert variant['available'] and variant['identity'] == bundle['artifact_sha256']
        assert client.request('GET', variant['preview_url'])[0] == 200
        for clip in bundle['clips']:
            prefix = f"/composition/{frozen['id']}/{variant['id']}/{clip['id']}/"
            for name in ('index.html', 'audio.wav'):
                assert client.request('GET', prefix+name)[1] == clip['files'][name]


@pytest.mark.parametrize('asset', ['gsap.min.js', 'NotoSansCJK-Regular.ttc'])
def test_runtime_asset_mismatch_blocks_preview_but_keeps_verified_evidence(evaluation_factory, client_factory, asset):
    fixture = evaluation_factory()
    client = client_factory(fixture, assets={**ASSETS, asset: b'different synthetic asset'})
    case = _case(client)
    for variant in case['variants']:
        assert variant['available'] is False and variant['preview_url'] is None
        assert client.request('GET', f"/preview/{case['id']}/{variant['id']}/")[0] == 404
    report = client.request('GET', '/api/evaluations/run-one')[1]
    frame = next(row for row in report['findings'] if row['source'] == 'model')['frames'][0]
    assert client.request('GET', frame['image_url'])[0] == 200


def test_model_findings_map_exact_original_side_global_clock_hash_and_region(evaluation_factory, client_factory):
    fixture = evaluation_factory(case_id='continuity-retain')
    client = client_factory(fixture)
    report = client.request('GET', '/api/evaluations/run-one')[1]
    for finding in report['findings']:
        if finding['source'] == 'technical':
            assert finding['location_status'] == 'not_localized'
            assert finding['frames'] == [] and finding['region'] is None
            continue
        assert finding['region'] == dict(x=.1, y=.2, width=.3, height=.4)
        assert finding['location_status'] == 'captured_frames'
        for frame in finding['frames']:
            private = fixture['request']['private']['frames'][frame['id']]
            source = next(row for row in fixture['frames'] if row['id'] == private['source_id'])
            assert frame['variant_id'] == finding['variant_id'] == source['variant_id']
            assert frame['clip_id'] == source['clip_id']
            assert frame['time_seconds'] == source['time_seconds']
            bundle = next(row for row in fixture['bundles'] if row['variant_id'] == frame['variant_id'])
            assert frame['time_seconds'] > bundle['clips'][0]['duration_seconds']
            assert frame['sha256'] == source['sha256']
            status, raw, headers = client.request('GET', frame['image_url'])
            assert status == 200 and headers['Content-Type'] == source['mime_type']
            assert raw == fixture['images'][private['source_id']] and sha256(raw) == frame['sha256']


def test_only_latest_needs_fix_decision_enters_task_without_changing_case_identity(evaluation_factory, client_factory):
    fixture = evaluation_factory()
    client = client_factory(fixture)
    original_case = _case(client)
    route = '/api/evaluations/run-one'
    report = client.request('GET', route)[1]
    model = next(row for row in report['findings'] if row['source'] == 'model')
    technical = next(row for row in report['findings'] if row['source'] == 'technical')
    for disposition in ('needs_fix', 'dismiss', 'defer'):
        assert client.request('POST', route+'/decisions', _decision(report, model, disposition))[0] == 201
        current = client.request('GET', route)[1]
        assert len(current['decisions']) == 1 and current['decisions'][0]['decision'] == disposition
        assert current['report_identity'] == report['report_identity']
        status, result, _ = client.request('GET', report['task_url'])
        if disposition == 'needs_fix':
            assert status == 200 and [row['id'] for row in result['findings']] == [model['id']]
        else:
            assert (status, result['error']) == (422, 'animation_lab_evaluation_invalid')
    assert client.request('POST', route+'/decisions', _decision(report, technical, 'needs_fix'))[0] == 201
    task = client.request('GET', report['task_url'])[1]
    assert [row['id'] for row in task['findings']] == [technical['id']]
    refreshed = client.request('GET', '/api/catalog')[1]
    case = next(row for row in refreshed['cases'] if row['id'] == original_case['id'])
    assert case['identity'] == original_case['identity']
    history = client.lab.triage_store.list(report['report_identity'])
    assert [row['decision'] for row in history] == ['needs_fix', 'dismiss', 'defer', 'needs_fix']
    assert [row['effective'] for row in history] == [False, False, True, True]


@pytest.mark.parametrize('case_id,scope', [
    ('diagram-connections', 'bounded_diagram_layout'), ('process-retry', 'developer_design_required'),
])
def test_revision_task_preserves_original_runtime_inputs_audio_and_requires_explicit_execution(
        evaluation_factory, client_factory, case_id, scope):
    fixture = evaluation_factory(case_id=case_id)
    client = client_factory(fixture)
    report = client.request('GET', '/api/evaluations/run-one')[1]
    finding = next(row for row in report['findings'] if row['source'] == 'model')
    event = client.request('POST', '/api/evaluations/run-one/decisions', _decision(report, finding, 'needs_fix'))[1]['decision']
    status, task, headers = client.request('GET', report['task_url'])
    assert status == 200 and 'animation-lab-revision-task.json' in headers['Content-Disposition']
    assert task['source_run_identity'] == fixture['run']['identity']
    assert task['source_report_identity'] == report['report_identity']
    assert task['source_evidence_identity'] == sha256(fixture['technical'])
    assert task['runtime'] == fixture['run']['runtime']
    assert task['semantic_input_sha256'] == fixture['run']['semantic_input_sha256']
    assert task['editable_scope']['kind'] == scope
    assert task['status'] == 'prepared_not_executed' and not task['production_adopted']
    assert task['agent_calls'] == task['model_calls'] == 0
    assert task['findings'][0]['developer_decision']['id'] == event['id']
    assert task['identity'] == sha256({key: value for key, value in task.items() if key != 'identity'})
    for source in task['sources']:
        bundle = next(row for row in fixture['bundles'] if row['variant_id'] == source['variant_id'])
        assert source['artifact_sha256'] == bundle['artifact_sha256']
        assert source['input_sha256'] == bundle['input_sha256']
        assert source['compiler'] == bundle['compiler'] and source['inputs'] == bundle['inputs']
        assert source['audio'] == [dict(clip_id=clip['id'], sha256=sha256(clip['files']['audio.wav']))
                                   for clip in bundle['clips']]


def test_changed_review_rejects_stale_decision_and_task_with_409(evaluation_factory, client_factory):
    fixture = evaluation_factory()
    client = client_factory(fixture)
    report = client.request('GET', '/api/evaluations/run-one')[1]
    finding = report['findings'][0]
    body = _decision(report, finding, 'needs_fix')
    saved = json.loads((fixture['root']/'model-review.json').read_bytes())
    saved['opinion']['comparison_reason'] = 'Changed synthetic opinion is a different report'
    _save(fixture['root'], 'model-review.json', validate_review_output(saved['opinion'], fixture['request']))
    current = client.request('GET', '/api/evaluations/run-one')[1]
    assert current['report_identity'] != report['report_identity']
    for method, url, payload in [('POST', '/api/evaluations/run-one/decisions', body),
                                 ('GET', report['task_url'], None)]:
        status, value, _ = client.request(method, url, payload)
        assert (status, value['error']) == (409, 'animation_lab_report_changed')
    assert client.lab.triage_store.list() == []


def test_frame_url_is_bound_to_report_and_stale_image_get_returns_409(evaluation_factory, client_factory):
    fixture = evaluation_factory()
    client = client_factory(fixture)
    old = client.request('GET', '/api/evaluations/run-one')[1]
    frame = next(row for row in old['findings'] if row['source'] == 'model')['frames'][0]
    old_url = frame['image_url']
    assert 'report_identity='+old['report_identity'] in old_url
    assert client.request('GET', old_url)[0] == 200
    saved = json.loads((fixture['root']/'model-review.json').read_bytes())
    saved['opinion']['comparison_reason'] = 'Replacement synthetic review; old image URL must expire'
    _save(fixture['root'], 'model-review.json', validate_review_output(saved['opinion'], fixture['request']))
    status, value, _ = client.request('GET', old_url)
    assert (status, value['error']) == (409, 'animation_lab_report_changed')
    current = client.request('GET', '/api/evaluations/run-one')[1]
    current_frame = next(row for row in current['findings'] if row['source'] == 'model')['frames'][0]
    assert current_frame['image_url'] != old_url
    assert client.request('GET', current_frame['image_url'])[0] == 200


def test_no_findings_does_not_invent_revision_tasks_or_jobs(evaluation_factory, client_factory):
    fixture = evaluation_factory(technical_issue=False, model_issues=False)
    client = client_factory(fixture)
    before = {path.relative_to(fixture['root']): sha256(path.read_bytes())
              for path in fixture['root'].rglob('*') if path.is_file()}
    report = client.request('GET', '/api/evaluations/run-one')[1]
    assert report['findings'] == [] and report['technical']['status'] == 'passed'
    assert report['model']['status'] == 'reviewed' and report['production_adopted'] is False
    assert client.request('GET', report['task_url'])[0] == 422
    for _ in range(2):
        assert client.request('GET', '/api/catalog')[0] == 200
    after = {path.relative_to(fixture['root']): sha256(path.read_bytes())
             for path in fixture['root'].rglob('*') if path.is_file()}
    assert after == before
    assert client.lab.triage_store.list() == []
    assert not (fixture['root']/'model-attempt.json').exists()


@pytest.mark.parametrize('corruption', ['image', 'review_identity'])
def test_corrupted_evidence_or_saved_review_is_rejected_and_hidden(evaluation_factory, client_factory, corruption):
    fixture = evaluation_factory()
    client = client_factory(fixture)
    if corruption == 'image':
        path = next((fixture['root']/'inputs').rglob('seek-first.png'))
        path.write_bytes(_png((1, 2, 3)))
    else:
        saved = json.loads((fixture['root']/'model-review.json').read_bytes())
        saved['case_identity'] = '0'*64
        _save(fixture['root'], 'model-review.json', saved)
    with pytest.raises(ValueError):
        load_evaluation(fixture['root'])
    status, value, _ = client.request('GET', '/api/evaluations/run-one')
    assert (status, value['error']) == (422, 'animation_lab_evaluation_invalid')
    catalog = client.request('GET', '/api/catalog')[1]
    assert catalog['evaluations'] == []
    assert all(not row['id'].startswith('evaluation-') for row in catalog['cases'])


def test_frame_read_rechecks_bytes_after_initial_report_load(evaluation_factory):
    fixture = evaluation_factory()
    loaded = load_evaluation(fixture['root'])
    frame = loaded['request']['public']['frames'][0]
    private = loaded['request']['private']['frames'][frame['id']]
    path = fixture['root']/'inputs'/private['variant_id']/private['clip_id']/'observations'/'seek-first.png'
    path.write_bytes(_png((250, 250, 250)))
    with pytest.raises(ValueError, match='frame_mismatch'):
        frame_bytes(fixture['root'], loaded, frame['id'])


@pytest.mark.parametrize('state,expected', [
    ('not_run', 'not_run'), ('unavailable', 'unavailable'), ('failed', 'failed'),
    ('unfinished_attempt', 'failed'), ('reviewed', 'reviewed'),
])
def test_model_execution_states_remain_distinct_without_dispatch(evaluation_factory, state, expected):
    fixture = evaluation_factory(model=state, technical_issue=False, model_issues=False)
    before = sorted(path.name for path in fixture['root'].iterdir())
    report = load_evaluation(fixture['root'])['snapshot']
    assert report['model']['status'] == expected
    assert report['findings'] == []
    assert sorted(path.name for path in fixture['root'].iterdir()) == before
    assert report['production_adopted'] is False


def test_export_preserves_human_annotations_triage_history_and_replayable_sources_after_restart(
        evaluation_factory, client_factory):
    fixture = evaluation_factory()
    client = client_factory(fixture)
    case = _case(client)
    annotation = dict(case_id=case['id'], case_identity=case['identity'],
        variant_id=case['variants'][0]['id'], kind='issue', start_seconds=.1, end_seconds=.2,
        region=None, tags=['clarity'], severity='minor', comment='Synthetic independent human observation')
    status, result, _ = client.request('POST', '/api/annotations', annotation)
    assert status == 201
    human_id = result['annotation']['id']
    report = client.request('GET', '/api/evaluations/run-one')[1]
    finding = report['findings'][0]
    for state in ('needs_fix', 'dismiss', 'defer'):
        assert client.request('POST', '/api/evaluations/run-one/decisions', _decision(report, finding, state))[0] == 201
    client.close()
    restarted = client_factory(fixture)
    status, raw, headers = restarted.request('GET', '/api/export')
    assert status == 200 and 'attachment;' in headers['Content-Disposition']
    rows = [json.loads(line) for line in raw.splitlines()]
    human = next(row for row in rows if row['record_type'] == 'annotation'
                 and row['annotation']['id'] == human_id)
    assert human['effective'] and human['annotation']['comment'] == annotation['comment']
    triage = [row for row in rows if row['record_type'] == 'evaluation_decision']
    assert [row['decision']['decision'] for row in triage] == ['needs_fix', 'dismiss', 'defer']
    assert [row['effective'] for row in triage] == [False, False, True]
    snapshot = next(row['snapshot'] for row in rows if row['record_type'] == 'evaluation_snapshot')
    assert snapshot['identity'] == report['report_identity']
    frozen = next(row['snapshot'] for row in rows if row['record_type'] == 'case_snapshot'
                  and row['snapshot']['identity'] == case['identity'])
    assert frozen['evaluation'] == dict(run_identity=fixture['run']['identity'],
                                      report_identity=report['report_identity'])
    assert frozen['runtime']['assets'] == {name: sha256(raw) for name, raw in ASSETS.items()}
    for variant in frozen['variants']:
        bundle = next(row for row in fixture['bundles'] if row['variant_id'] == variant['variant_id'])
        assert variant['inputs'] == bundle['inputs'] and variant['compiler'] == bundle['compiler']
        for frozen_clip, clip in zip(variant['clips'], bundle['clips']):
            for name, document in frozen_clip['files'].items():
                raw = base64.b64decode(document['data'], validate=True)
                assert raw == clip['files'][name] and sha256(raw) == document['sha256']
    detail = restarted.request('GET', '/api/evaluations/run-one')[1]
    assert [row['decision'] for row in detail['decisions']] == ['defer']
