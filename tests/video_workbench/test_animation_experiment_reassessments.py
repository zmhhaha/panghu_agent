"""Supplemental audit transport; all fixtures are synthetic, not render evidence."""
import http.client
import json
import os
import threading
from types import SimpleNamespace

import pytest

from video_workbench import animation_experiments as lab
from video_workbench.animation_lab import LabHTTPServer


IMAGE = 'registry.test/renderer:latest@sha256:' + '9' * 64
PREFIX = '/dev/animations'
USER = 'casdoor:audit-test-developer'


def setup_audit(tmp_path):
    runs = tmp_path / 'capability-experiments'
    directory = runs / 'synthetic-run'
    directory.mkdir(parents=True)
    document = lab.pilot_input()
    report = dict(schema='video.animation-experiment-report.v1', input_sha256=lab.digest(document),
        agent_job_id='synthetic-audit-job', renderer=dict(image=IMAGE,
            technical=dict(status='failed', issues=['object_identity_not_visible'],
                scope='synthetic original observation')))
    row = dict(schema=lab.RUN_SCHEMA, id=directory.name, label='Synthetic only',
        experiment_id=lab.definition()['id'], experiment_identity=lab.definition()['identity'],
        source='agent', input_role='development', input_document=document,
        input_sha256=lab.digest(document), prompt_text='Synthetic audit transport fixture', status='rendered',
        provenance=dict(job_id='synthetic-audit-job', returned_model=None), artifacts={},
        code_files={'scene.html': '<svg><rect data-id="A"/><text>甲</text></svg>'})
    for name, raw, mime in [('output.mp4', b'0000ftypSYNTHETIC-AUDIT-MEDIA', 'video/mp4'),
                             ('report.json', lab.canonical(report), 'application/json')]:
        (directory / name).write_bytes(raw)
        row['artifacts'][name] = dict(size=len(raw), sha256=lab.digest(raw), mime=mime)
    row = lab.signed(row)
    (directory / 'experiment-run.json').write_bytes(lab.canonical(row))
    audit = lab.signed(dict(schema=lab.REASSESSMENT_SCHEMA, run_id=row['id'], run_identity=row['identity'],
        original_report_sha256=row['artifacts']['report.json']['sha256'],
        media_sha256=row['artifacts']['output.mp4']['sha256'], verifier_revision='svg-label-geometry-v2',
        source_code_sha256={k: lab.digest(v.encode()) for k, v in row['code_files'].items()},
        renderer_image=IMAGE, observer_host_sha256='1' * 64, observations_sha256='2' * 64,
        frame_hashes_match=True, media_hash_matches=True, evaluated_at=1794123000.,
        technical=dict(status='passed', issues=[], metrics={'synthetic_fixture': True},
            scope='pilot_dom_geometry_and_rendered_frames', aesthetic_approval=False)))
    return lab.ExperimentStore(tmp_path / 'feedback.db', runs), directory, row, audit


def publish(experiments, row, audit):
    directory = experiments.runs_dir.parent / 'capability-assessments'
    directory.mkdir(exist_ok=True)
    path = directory / (row['id'] + '.json')
    path.write_bytes(lab.canonical(audit))
    return path


def version(experiments):
    return experiments.detail('identity-tracking')['versions'][0]


def test_valid_reassessment_preserves_original_artwork_identity_report_and_aesthetic_state(tmp_path):
    experiments, directory, row, audit = setup_audit(tmp_path)
    frozen = {path.name: path.read_bytes() for path in directory.iterdir()}
    before = version(experiments)
    assert before['technical']['status'] == 'failed' and before['assessment_url'] is None
    publish(experiments, row, audit)
    after = version(experiments)
    assert after['identity'] == before['identity'] == row['identity']
    assert after['technical']['status'] == 'passed'
    assert after['original_technical'] == before['technical']
    assert after['technical_reassessment'] == audit and after['assessment_error'] is None
    assert after['aesthetic_status'] == 'unreviewed'
    assert after['media_url'] == before['media_url'] and after['report_url'] == before['report_url']
    assert after['code_files'] == before['code_files'] and experiments.feedback() == []
    raw, mime = experiments.artifact('identity-tracking', row['id'], 'assessment.json')
    assert json.loads(raw) == audit and mime == 'application/json'
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == frozen


@pytest.mark.parametrize('field,value', [
    ('schema', 'unreviewed-audit'), ('run_id', 'other-run'), ('run_identity', '0' * 64),
    ('original_report_sha256', '0' * 64), ('media_sha256', '0' * 64),
    ('source_code_sha256', {'scene.html': '0' * 64}), ('renderer_image', IMAGE[:-1] + '8'),
    ('verifier_revision', 'model-self-report'), ('observer_host_sha256', 'unknown'),
    ('observations_sha256', 'unknown'), ('frame_hashes_match', False), ('media_hash_matches', False),
    ('evaluated_at', -1), ('evaluated_at', True), ('evaluated_at', 10 ** 400),
    ('technical', dict(status='passed', issues=[], metrics={},
        scope='pilot_dom_geometry_and_rendered_frames', aesthetic_approval=True)),
    ('technical', dict(status='passed', issues=['still_failed'], metrics={},
        scope='pilot_dom_geometry_and_rendered_frames', aesthetic_approval=False)),
])
def test_mismatched_even_rehashed_assessments_cannot_replace_original_verdict(tmp_path, field, value):
    experiments, _, row, audit = setup_audit(tmp_path)
    audit[field] = value
    audit = lab.signed({k: v for k, v in audit.items() if k != 'identity'})
    publish(experiments, row, audit)
    current = version(experiments)
    assert current['technical'] == current['original_technical']
    assert current['technical']['status'] == 'failed' and current['assessment_url'] is None
    assert current['technical_reassessment'] is None and current['assessment_error']
    with pytest.raises(lab.ExperimentError, match='assessment_invalid'):
        experiments.artifact('identity-tracking', row['id'], 'assessment.json')


def test_unsigned_invalid_json_and_unexpected_fields_keep_original_verdict(tmp_path):
    experiments, _, row, audit = setup_audit(tmp_path)
    path = publish(experiments, row, audit)
    for raw in [b'{bad json', lab.canonical(dict(audit, media_sha256='0' * 64)),
                lab.canonical(lab.signed(dict(audit, unsafe_html='<script>alert(1)</script>')))]:
        path.write_bytes(raw)
        current = version(experiments)
        assert current['technical']['status'] == 'failed' and current['assessment_error']
        assert current['assessment_url'] is None


def test_missing_audit_is_optional_and_artifact_not_found(tmp_path):
    experiments, _, row, _ = setup_audit(tmp_path)
    current = version(experiments)
    assert current['assessment_error'] is None and current['technical_reassessment'] is None
    with pytest.raises(lab.ExperimentError) as error:
        experiments.artifact('identity-tracking', row['id'], 'assessment.json')
    assert error.value.status == 404


def test_supplement_cannot_hide_changes_to_original_media(tmp_path):
    experiments, directory, row, audit = setup_audit(tmp_path)
    publish(experiments, row, audit)
    (directory / 'output.mp4').write_bytes(b'changed-original')
    assert experiments.detail('identity-tracking')['versions'] == []
    with pytest.raises(lab.ExperimentError, match='artifact_changed'):
        experiments.artifact('identity-tracking', row['id'], 'assessment.json')


def test_hardlinked_audit_is_rejected_as_mutable_external_alias(tmp_path):
    experiments, _, row, audit = setup_audit(tmp_path)
    path = publish(experiments, row, audit)
    os.link(path, tmp_path / 'external-alias.json')
    current = version(experiments)
    assert current['technical']['status'] == 'failed'
    assert current['assessment_error'] and current['assessment_url'] is None


def test_http_reassessment_uses_same_auth_prefix_and_safe_json_route(tmp_path):
    experiments, directory, row, audit = setup_audit(tmp_path)
    path = publish(experiments, row, audit)
    before_report = (directory / 'report.json').read_bytes()
    server = LabHTTPServer(SimpleNamespace(experiments=experiments, csrf_token='fixture-token',
        iteration_store=SimpleNamespace()), 0,
        proxy_mode=True, public_origin='https://video-service.panghuer.top',
        base_path=PREFIX, developer_users=[USER])
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    def request(target, user=USER):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            headers = {'Host': 'video-service.panghuer.top'}
            if user:
                headers['X-Forwarded-User'] = user
            connection.request('GET', target, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()
    route = PREFIX + '/api/experiments/identity-tracking/runs/synthetic-run/assessment.json'
    try:
        status, body, headers = request(route)
        assert status == 200 and json.loads(body) == audit
        assert headers['Content-Type'].startswith('application/json')
        assert headers['X-Content-Type-Options'] == 'nosniff'
        assert request(route, user=None)[0] == 403
        assert request(route, user='casdoor:unauthorized')[0] == 403
        status, body, _ = request(PREFIX + '/api/experiments/identity-tracking')
        assert status == 200
        current = json.loads(body)['versions'][0]
        assert current['assessment_url'] == route
        assert current['report_url'].startswith(PREFIX + '/api/')
        assert request(current['report_url'])[1] == before_report
        path.write_bytes(b'{invalid supplemental report')
        assert request(route)[0] == 409
        current = json.loads(request(PREFIX + '/api/experiments/identity-tracking')[1])['versions'][0]
        assert current['technical']['status'] == 'failed' and current['assessment_error']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
