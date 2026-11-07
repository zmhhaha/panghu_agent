"""Coordinator invariants with explicit synthetic observations/model transport.

These tests do not constitute browser captures or paid model evidence.
"""
from copy import deepcopy
import json
import struct
import zlib

import pytest

from video_workbench import animation_lab_evaluate as evaluation
from video_workbench.animation_lab import AnimationLab, LabError
from video_workbench.animation_lab_model import ModelError
from video_workbench.animation_lab_review import REVIEW_SCHEMA
from video_workbench.motion import sha256


RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='test', node='test', browser='test', ffmpeg='test', gsap='3.14.2',
    gsap_sha256='a'*64, font_sha256='b'*64, npm_lock_sha256='c'*64,
    dpkg_sha256='d'*64, image_digest='sha256:'+'e'*64)


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1280, 720, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0'+b'\0'*(1280*3))*720)) + chunk(b'IEND', b''))


@pytest.fixture
def ready(tmp_path, monkeypatch):
    root = tmp_path / 'run'
    run = evaluation.prepare(root, RUNTIME)
    raw = png()

    def capture(bundle, clip_id, directory, runtime):
        target = directory / 'observations' / 'seek-first.png'
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw)
        status = 'failed' if bundle['variant_id'] == 'layout-focus' else 'passed'
        return dict(variant_id=bundle['variant_id'], technical_status=status,
            issues=[] if status == 'passed' else [dict(code='synthetic_objective_failure')], frame_count=1,
            visual_frames=[dict(frame_id='scene-1:seek-first', clip_id=clip_id,
                path='observations/seek-first.png', global_time_seconds=0, time_seconds=0,
                sha256=sha256(raw), mime_type='image/png')])

    monkeypatch.setattr(evaluation, 'review_capture', capture)
    technical, request = evaluation.collect(root)
    return root, run, technical, request


def opinion(request, variant_id='candidate'):
    label = next(label for label, row in request['private']['variants'].items() if row['id'] == variant_id)
    return dict(schema=REVIEW_SCHEMA, request_identity=request['identity'], preference=label,
        comparison_reason='Synthetic fixture preference only',
        comparison_frame_ids=[row['id'] for row in request['public']['frames']],
        issues=[], suggestions=[dict(issue_id=None, kind='no_change', parameters={}, reason='test')],
        limitations=['Synthetic transport test; no actual aesthetic judgment.'])


class Model:
    calls = 0

    def __init__(self, root, request, *, variant_id='candidate', fail=False, invalid=False):
        self.root, self.request, self.variant_id = root, request, variant_id
        self.fail, self.invalid = fail, invalid

    def review(self, messages):
        self.calls += 1
        # Reservation has reached disk before the very first transport method.
        assert (self.root / 'model-attempt.json').is_file()
        assert any(row['type'] == 'image_url' for row in messages[1]['content'])
        if self.fail:
            raise ModelError('visual_model_transport_outcome_unknown')
        raw = '{}' if self.invalid else json.dumps(opinion(self.request, self.variant_id))
        return raw, dict(post_attempts=1, automatic_retries=0)


def test_preparation_preserves_inputs_and_owns_distinct_frozen_outputs(tmp_path):
    root = tmp_path / 'run'
    run = evaluation.prepare(root, RUNTIME)
    reloaded, bundles = evaluation.load_run(root)
    assert reloaded == run
    assert len({bundle['artifact_sha256'] for bundle in bundles}) == 2
    assert bundles[0]['clips'][0]['files']['audio.wav'] == bundles[1]['clips'][0]['files']['audio.wav']
    assert all(row['directory'].startswith('inputs/') for row in run['captures'])
    with pytest.raises(ValueError, match='run_exists'):
        evaluation.prepare(root, RUNTIME)
    assert json.loads((root/'run.json').read_bytes()) == run


@pytest.mark.parametrize('case_id,module,captures', [
    ('diagram-long-label','diagram',2), ('process-retry','process',2),
    ('continuity-retain','continuity',4), ('continuity-copy','continuity',4)])
def test_all_fixed_modules_have_real_compiled_frozen_preparation(tmp_path, case_id, module, captures):
    root = tmp_path / case_id
    run = evaluation.prepare(root, RUNTIME, case_id=case_id)
    _, bundles = evaluation.load_run(root)
    assert run['module'] == module and len(run['captures']) == captures
    assert run['limits']['local_candidates'] == (1 if module == 'diagram' else 0)
    assert len(bundles) == 2 and len({b['artifact_sha256'] for b in bundles}) == 2
    assert all('window.__videoMotion' in clip['files']['index.html'].decode('utf-8')
               for bundle in bundles for clip in bundle['clips'])


def test_frozen_content_tamper_rejected_before_dispatch(tmp_path):
    root = tmp_path / 'run'
    run = evaluation.prepare(root, RUNTIME)
    run['bundles'][0]['inputs']['inputs']['plan']['title'] = 'changed'
    (root / 'run.json').write_text(json.dumps(run))
    with pytest.raises(ValueError, match='run_mismatch'):
        evaluation.load_run(root)
    assert not (root / 'model-attempt.json').exists()


def test_collect_binds_real_bytes_and_never_overwrites_evidence(ready):
    root, run, technical, request = ready
    assert technical['status'] == 'failed'
    assert request['public']['evidence_identity'] == sha256(technical)
    assert (root / 'report.html').is_file()
    assert not (root / 'model-attempt.json').exists()
    old = (root / 'technical.json').read_bytes()
    with pytest.raises(FileExistsError):
        evaluation.collect(root)
    assert (root / 'technical.json').read_bytes() == old


@pytest.mark.parametrize('selected,eligible', [('candidate', True), ('layout-focus', False)])
def test_model_preference_cannot_override_objective_failure_or_adopt(ready, selected, eligible):
    root, run, technical, request = ready
    model = Model(root, request, variant_id=selected)
    review = evaluation.model_review(root, model=model)
    assert model.calls == 1
    assert review['eligible_for_developer_comparison'] is eligible
    assert review['status'] == 'suggestion_only'
    assert not review['human_approved'] and not review['auto_adopt'] and not review['production_adopted']
    decision = json.loads((root / 'revision-decision.json').read_bytes())
    assert not decision['generic_agent_used'] and decision['new_automatic_runs'] == 0
    with pytest.raises(FileExistsError):
        evaluation.model_review(root, model=model)
    assert model.calls == 1


@pytest.mark.parametrize('failure', ['unknown', 'invalid'])
def test_failed_model_attempt_preserves_evidence_and_blocks_retry(ready, failure):
    root, run, technical, request = ready
    model = Model(root, request, fail=failure == 'unknown', invalid=failure == 'invalid')
    with pytest.raises(ValueError):
        evaluation.model_review(root, model=model)
    assert json.loads((root / 'model-failure.json').read_bytes())['automatic_retry'] is False
    assert (root / 'technical.json').is_file() and (root / 'review-request.json').is_file()
    assert not (root / 'model-review.json').exists()
    if failure == 'invalid':
        assert (root / 'model-output.txt').read_text() == '{}'
    with pytest.raises(FileExistsError):
        evaluation.model_review(root, model=model)
    assert model.calls == 1


def test_changed_technical_evidence_prevents_model_post(ready, monkeypatch):
    root, run, technical, request = ready
    original = evaluation.review_capture

    def changed(*args):
        row = original(*args)
        row['technical_status'] = 'passed'
        return row

    monkeypatch.setattr(evaluation, 'review_capture', changed)
    model = Model(root, request)
    with pytest.raises(ValueError, match='evidence_changed'):
        evaluation.model_review(root, model=model)
    assert model.calls == 0 and not (root / 'model-attempt.json').exists()


def test_unconfigured_model_records_no_dispatch_and_later_configuration_allows_first_post(ready, monkeypatch):
    root, run, technical, request = ready
    def missing():
        raise ModelError('visual_model_one_credential_required')
    monkeypatch.setattr(evaluation.VisualModel, 'from_env', missing)
    with pytest.raises(ModelError, match='unavailable_before_dispatch'):
        evaluation.model_review(root)
    assert json.loads((root / 'model-unavailable.json').read_bytes())['post_attempts'] == 0
    assert not (root / 'model-attempt.json').exists()
    model = Model(root, request)
    review = evaluation.model_review(root, model=model)
    assert model.calls == 1 and review['status'] == 'suggestion_only'


def test_developer_report_reads_saved_results_without_starting_jobs(ready):
    root, run, technical, request = ready
    lab = AnimationLab.__new__(AnimationLab)
    lab.evaluations_dir = root.parent
    rows = lab.evaluations()
    assert len(rows) == 1 and rows[0]['identity'] == run['identity']
    assert rows[0]['model_status'] == '尚未调用'
    body, mime = lab.evaluation_file(root.name, 'report.html')
    assert mime.startswith('text/html') and '没有自动采用'.encode() in body
    assert not (root / 'model-attempt.json').exists()


@pytest.mark.parametrize('relative', ['run.json', 'model-attempt.json', '../secrets.json', '/etc/passwd'])
def test_report_endpoint_has_no_arbitrary_file_access(ready, relative):
    root, *_ = ready
    lab = AnimationLab.__new__(AnimationLab)
    lab.evaluations_dir = root.parent
    with pytest.raises(LabError, match='report_not_found'):
        lab.evaluation_file(root.name, relative)
