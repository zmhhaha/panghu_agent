"""Evidence-bound, interactive views of saved developer evaluations.

Opening a report never invokes a renderer, model or Agent. The compared bundles
come from the frozen run, rather than today's similarly named sample catalog.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from .animation_lab_candidates import DESIGN_RULES
from .animation_lab_evaluate import load_run
from .animation_lab_review import build_review_messages, validate_review_output
from .motion import sha256


_RUN = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
_FRAME = re.compile(r'[a-z0-9][a-z0-9-]{0,100}\Z')


def _require(value, code='animation_evaluation_view_invalid'):
    if not value:
        raise ValueError(code)


def read_file(root, relative, maximum=16*1024*1024):
    root = Path(root).absolute()
    path = root / relative
    _require(not root.is_symlink() and not path.is_symlink()
             and path.resolve().is_relative_to(root.resolve())
             and path.is_file() and path.stat().st_size <= maximum,
             'animation_evaluation_view_file_invalid')
    return path.read_bytes()


def _json(root, name, maximum=16*1024*1024):
    return json.loads(read_file(root, name, maximum))


def _frame_path(request, frame):
    private = request['private']['frames'][frame['id']]
    name = private['source_id'].split(':')[-1]
    _require(_FRAME.fullmatch(name), 'animation_evaluation_view_frame_invalid')
    suffix = '.jpg' if frame['mime_type'] == 'image/jpeg' else '.png'
    return f"inputs/{private['variant_id']}/{private['clip_id']}/observations/{name}{suffix}"


def load_evaluation(root):
    """Verify saved identities/images and project opinions without relabeling."""
    root = Path(root)
    _require(_RUN.fullmatch(root.name) and not root.is_symlink(), 'animation_evaluation_view_run_invalid')
    run, bundles = load_run(root)
    request = _json(root, 'review-request.json')
    technical = _json(root, 'technical.json')
    _require(technical.get('run_identity') == run['identity']
             and request['public']['case_identity'] == run['identity']
             and request['public']['runtime_identity'] == run['runtime_identity']
             and request['public']['evidence_identity'] == sha256(technical),
             'animation_evaluation_view_evidence_mismatch')
    by_variant = {row['variant_id']: row for row in bundles}
    expected = {(bundle['variant_id'], clip['id']) for bundle in bundles for clip in bundle['clips']}
    reports = technical.get('reports', [])
    _require(type(reports) is list and {(row['variant_id'], row['clip_id']) for row in reports} == expected
             and len(reports) == len(expected), 'animation_evaluation_view_coverage_mismatch')
    for report in reports:
        bundle = by_variant[report['variant_id']]
        _require(report['sample_identity'] == bundle['identity']
                 and report['artifact_sha256'] == bundle['artifact_sha256']
                 and report['input_sha256'] == bundle['input_sha256']
                 and report['runtime'] == run['runtime']
                 and report['technical_status'] in {'passed', 'failed'}
                 and report['evidence_identity'] == sha256({key: value for key, value in report.items() if key != 'evidence_identity'}),
                 'animation_evaluation_view_technical_mismatch')
    status = 'passed' if all(row['technical_status'] == 'passed' for row in reports) else 'failed'
    _require(technical.get('status') == status, 'animation_evaluation_view_technical_mismatch')
    blind_variants = request['private']['variants']
    _require(set(blind_variants) == {'A', 'B'} and {row['id'] for row in blind_variants.values()} == set(by_variant),
             'animation_evaluation_view_variant_mismatch')
    for variant in blind_variants.values():
        bundle = by_variant[variant['id']]
        _require(variant['artifact_sha256'] == bundle['artifact_sha256']
                 and variant['duration_seconds'] == bundle['duration_seconds'],
                 'animation_evaluation_view_variant_mismatch')
    frames, images = {}, {}
    for frame in request['public']['frames']:
        private = request['private']['frames'][frame['id']]
        _require((private['variant_id'], private['clip_id']) in expected
                 and blind_variants[frame['variant']]['id'] == private['variant_id'],
                 'animation_evaluation_view_frame_invalid')
        raw = read_file(root, _frame_path(request, frame), 4*1024*1024)
        images[private['source_id']] = raw
        frames[frame['id']] = dict(id=frame['id'], variant_id=private['variant_id'], clip_id=private['clip_id'],
            time_seconds=frame['time_seconds'], sha256=frame['sha256'], mime_type=frame['mime_type'])
    build_review_messages(request, images)
    model = dict(status='not_run', preference=None, comparison_reason='', limitations=[])
    validated, review_identity = None, None
    if (root / 'model-review.json').exists():
        saved = _json(root, 'model-review.json', 128*1024)
        validated = validate_review_output(saved['opinion'], request)
        _require(all(saved.get(key) == validated[key] for key in
                     ('request_identity', 'case_identity', 'runtime_identity', 'evidence_identity', 'selected_variant_id')),
                 'animation_evaluation_view_review_mismatch')
        review_identity = sha256(saved)
        opinion = validated['opinion']
        model.update(status='reviewed', preference=opinion['preference'],
            comparison_reason=opinion['comparison_reason'], limitations=opinion['limitations'])
    elif (root / 'model-failure.json').exists():
        failure = _json(root, 'model-failure.json', 64*1024)
        _require(failure['request_identity'] == request['identity'], 'animation_evaluation_view_review_mismatch')
        model.update(status='failed', comparison_reason='视觉评审失败，原始证据仍保留。')
        review_identity = sha256(failure)
    elif (root / 'model-unavailable.json').exists():
        unavailable = _json(root, 'model-unavailable.json', 64*1024)
        _require(unavailable['request_identity'] == request['identity'], 'animation_evaluation_view_review_mismatch')
        model.update(status='unavailable', comparison_reason='模型尚未配置或凭据不可用，没有提交评审请求。')
        review_identity = sha256(unavailable)
    elif (root / 'model-attempt.json').exists():
        attempt = _json(root, 'model-attempt.json', 64*1024)
        _require(attempt['request_identity'] == request['identity'], 'animation_evaluation_view_review_mismatch')
        model.update(status='failed', comparison_reason='存在调用预留但没有完成结果，请检查原运行；不会自动重试。')
        review_identity = sha256(attempt)
    findings = []
    for report in reports:
        for index, issue in enumerate(report['issues']):
            findings.append(dict(id='technical-'+sha256(dict(variant=report['variant_id'], clip=report['clip_id'], index=index, issue=issue))[:24],
                source='technical', variant_id=report['variant_id'], clip_id=report['clip_id'],
                category='other', severity=issue.get('severity', 'blocker'),
                description='技术检查未通过：'+issue['code'], code=issue['code'],
                claim='technical_check', region=None, frames=[], suggestions=[],
                location_status='not_localized'))
    if validated:
        for issue in validated['opinion']['issues']:
            findings.append(dict(id='model-'+sha256(issue)[:24], source='model',
                variant_id=blind_variants[issue['variant']]['id'], category=issue['category'],
                severity=issue['severity'], description=issue['description'], claim=issue['claim'],
                confidence=issue['confidence'], region=issue['region'],
                frames=[deepcopy(frames[identity]) for identity in issue['frame_ids']],
                suggestions=[deepcopy(item) for item in validated['opinion']['suggestions'] if item['issue_id'] == issue['id']],
                location_status='captured_frames'))
    value = dict(schema='video.animation-lab-triage-snapshot.v1', run_identity=run['identity'],
        evidence_identity=request['public']['evidence_identity'], review_identity=review_identity,
        case_id=run['case_id'], module=run['module'], title=run['title'],
        runtime_identity=run['runtime_identity'], semantic_input_sha256=run['semantic_input_sha256'],
        variants=[dict(id=row['id'], label='评测版本 '+label,
                       artifact_sha256=row['artifact_sha256'], duration_seconds=row['duration_seconds'])
                  for label, row in sorted(blind_variants.items())],
        technical=dict(status=status, scope=technical['objective_scope']), model=model,
        findings=findings, frame_catalog=list(frames.values()),
        production_adopted=False, review_scope='developer_sampled_visual_review')
    snapshot = dict(value, identity=sha256(value))
    return dict(run=run, bundles=bundles, request=request, snapshot=snapshot)


def detail(loaded, run_id, decisions=()):
    snapshot = loaded['snapshot']
    value = deepcopy(snapshot)
    value.update(id=run_id, report_identity=snapshot['identity'], decisions=list(decisions),
                 task_url=f"/api/evaluations/{run_id}/revision-task?report_identity={snapshot['identity']}")
    for finding in value['findings']:
        for frame in finding['frames']:
            frame['image_url'] = (f"/api/evaluations/{run_id}/frames/{frame['id']}"
                                  f"?report_identity={snapshot['identity']}")
    return value


def frame_bytes(root, loaded, frame_id):
    frame = next((row for row in loaded['request']['public']['frames'] if row['id'] == frame_id), None)
    _require(frame is not None, 'animation_evaluation_view_unknown_frame')
    raw = read_file(root, _frame_path(loaded['request'], frame), 4*1024*1024)
    _require(sha256(raw) == frame['sha256'], 'animation_evaluation_view_frame_mismatch')
    return raw, frame['mime_type']


def revision_task(loaded, decisions):
    """Prepare an auditable task from current confirmed findings; execute none."""
    snapshot = loaded['snapshot']
    selected = {row['finding_id']: row for row in decisions if row.get('effective') is True
                and row.get('report_identity') == snapshot['identity'] and row['decision'] == 'needs_fix'}
    findings = [dict(deepcopy(row), developer_decision=deepcopy(selected[row['id']]))
                for row in snapshot['findings'] if row['id'] in selected]
    _require(bool(findings), 'animation_evaluation_no_confirmed_findings')
    value = dict(schema='video.animation-lab-revision-task.v1', source_run_identity=snapshot['run_identity'],
        source_report_identity=snapshot['identity'], source_evidence_identity=snapshot['evidence_identity'],
        module=snapshot['module'], case_id=snapshot['case_id'], runtime=loaded['run']['runtime'],
        semantic_input_sha256=snapshot['semantic_input_sha256'], findings=findings,
        sources=[dict(variant_id=bundle['variant_id'], artifact_sha256=bundle['artifact_sha256'],
            input_sha256=bundle['input_sha256'], compiler=bundle['compiler'], inputs=deepcopy(bundle['inputs']),
            audio=[dict(clip_id=clip['id'], sha256=sha256(clip['files']['audio.wav'])) for clip in bundle['clips']])
            for bundle in loaded['bundles']],
        editable_scope=(dict(kind='bounded_diagram_layout', fields=['template', 'mapped_slots'], templates=['flow', 'focus'])
                        if snapshot['module'] == 'diagram' and all(bundle['inputs']['inputs'].get('plan', {}).get('schema') == 'video.motion-plan.v1' for bundle in loaded['bundles'])
                        else dict(kind='developer_design_required', fields=[])),
        design_rules=list(DESIGN_RULES), required_checks=['recompile', 'same_content_and_audio', 'native_observations', 'same_input_comparison'],
        status='prepared_not_executed', agent_calls=0, model_calls=0, production_adopted=False)
    return dict(value, identity=sha256(value))
