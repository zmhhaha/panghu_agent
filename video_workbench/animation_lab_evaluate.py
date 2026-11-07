"""Explicit developer-only fixed-sample evaluation commands.

prepare freezes current compiler versus one finite layout experiment; collect
checks real captures; model makes at most one useful image review per run.
No command changes user projects, production templates or adoption state.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import html
import json
import os
from pathlib import Path
import re

from .animation_lab_candidates import DESIGN_RULES, effective_feedback, effective_triage_feedback, layout_candidate, semantic_identity
from .animation_lab_evidence import review_capture, write_capture_inputs
from .animation_lab_model import ModelError, VisualModel
from .animation_lab_review import build_review_messages, build_review_request, validate_review_output
from .animation_lab_samples import catalog, compile_sample
from .animation_lab_store import AnnotationStore
from .motion import canonical, sha256


def _require(value, code):
    if not value:
        raise ValueError(code)


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def _text(path, value, *, replace=False):
    path = Path(path)
    _require(not path.is_symlink() and (not path.exists() or (replace and path.is_file())),
             'animation_evaluation_output_exists_or_symlink')
    with path.open('w' if replace else 'x', encoding='utf-8') as stream:
        stream.write(value)


def _freeze(bundle):
    value = deepcopy(bundle)
    for clip in value['clips']:
        clip['files'] = {name: base64.b64encode(raw).decode('ascii') for name, raw in clip['files'].items()}
    return value


def _thaw(value):
    value = deepcopy(value)
    for clip in value['clips']:
        clip['files'] = {name: base64.b64decode(raw, validate=True) for name, raw in clip['files'].items()}
    artifact = sha256([dict(id=clip['id'], files={name: sha256(raw) for name, raw in sorted(clip['files'].items())})
                       for clip in value['clips']])
    _require(artifact == value['artifact_sha256'], 'animation_evaluation_artifact_mismatch')
    _require(sha256(value['inputs']) == value['input_sha256'], 'animation_evaluation_input_mismatch')
    return value


def prepare(destination, runtime, *, case_id='diagram-connections', template=None, feedback_store=None, engine_assets_dir=None):
    root = Path(destination).absolute()
    _require(not root.exists() and not root.is_symlink(), 'animation_evaluation_run_exists')
    reference = compile_sample(case_id, 'candidate')
    case = next(row for row in catalog() if row['id'] == case_id)
    if reference['inputs']['inputs'].get('plan', {}).get('schema') == 'video.motion-plan.v1':
        alternative = layout_candidate(reference, template or 'focus')
        local_candidates = 1
    else:
        _require(template is None, 'animation_evaluation_layout_not_supported')
        reference, alternative = compile_sample(case_id, 'baseline'), reference
        local_candidates = 0
    _require(reference['artifact_sha256'] != alternative['artifact_sha256'], 'animation_evaluation_duplicate_candidate')
    bundles = [reference, alternative]
    frozen = [_freeze(bundle) for bundle in bundles]
    semantic = semantic_identity(reference)
    _require(all(semantic_identity(bundle) == semantic for bundle in bundles), 'animation_evaluation_semantics_changed')
    engine_assets = {name: digest for bundle in bundles for clip in bundle['clips']
        for name, digest in json.loads(clip['files']['manifest.json']).get('engine_assets', {}).items()}
    asset_bytes = {}
    for name, digest in engine_assets.items():
        _require(engine_assets_dir is not None, 'animation_evaluation_engine_assets_required')
        path = Path(engine_assets_dir) / name
        _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 8*1024*1024,
                 'animation_evaluation_engine_assets_invalid')
        asset_bytes[name] = path.read_bytes()
        _require(sha256(asset_bytes[name]) == digest, 'animation_evaluation_engine_assets_mismatch')
    root.mkdir(parents=True)
    captures = []
    for bundle in bundles:
        rows = write_capture_inputs(bundle, root / 'inputs' / bundle['variant_id'], runtime)
        for row in rows:
            row['directory'] = str(Path(row['directory']).relative_to(root)).replace('\\', '/')
            row['variant_id'] = bundle['variant_id']
            captures.append(row)
    overlay = Path(__file__).with_name('motion_renderer')
    for name in ('lab_observe.mjs', 'observe.mjs', 'runtime.mjs', 'contract.mjs'):
        path = root / 'overlay' / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes((overlay / name).read_bytes())
    if any(clip['timeline'].get('engine') in {'canvas', 'three', 'svg-effects'} for bundle in bundles for clip in bundle['clips']):
        (root / 'overlay' / 'engine_observe.mjs').write_bytes((overlay / 'engine_observe.mjs').read_bytes())
    for name, raw in asset_bytes.items():
        (root / 'overlay' / name).write_bytes(raw)
    feedback = effective_feedback(feedback_store, bundles) if feedback_store else []
    if feedback_store:
        from .animation_lab_triage_store import TriageStore
        feedback += effective_triage_feedback(TriageStore(feedback_store.path), bundles)
    value = dict(schema='video.animation-lab-evaluation-run.v1', case_id=case_id, module=case['module'],
        title=case['title'], runtime=runtime,
        runtime_identity=sha256(runtime), semantic_input_sha256=semantic, bundles=frozen,
        captures=captures, artifact_feedback=feedback, design_rules=list(DESIGN_RULES),
        limits=dict(captures=len(captures), model_calls=1, local_candidates=local_candidates, automatic_retries=0),
        scope='developer_fixed_sample', audio_kind='synthetic_silence_not_narration',
        agent_calls=0, tts_calls=0, production_writes=0, production_adopted=False)
    value['identity'] = sha256(value)
    _save(root / 'run.json', value)
    _save(root / 'runtime.json', runtime)
    return value


def load_run(root):
    root = Path(root)
    raw = root / 'run.json'
    _require(raw.is_file() and not raw.is_symlink() and raw.stat().st_size <= 16 * 1024 * 1024,
             'animation_evaluation_run_invalid')
    value = json.loads(raw.read_bytes())
    unsigned = {key: row for key, row in value.items() if key != 'identity'}
    _require(value.get('schema') == 'video.animation-lab-evaluation-run.v1'
             and sha256(unsigned) == value.get('identity'), 'animation_evaluation_run_mismatch')
    bundles = [_thaw(row) for row in value['bundles']]
    _require(len(bundles) == 2 and all(semantic_identity(bundle) == value['semantic_input_sha256'] for bundle in bundles)
             and sha256(value['runtime']) == value['runtime_identity'], 'animation_evaluation_semantics_changed')
    expected = {f"inputs/{bundle['variant_id']}/{clip['id']}" for bundle in bundles for clip in bundle['clips']}
    _require({row['directory'] for row in value['captures']} == expected, 'animation_evaluation_paths_invalid')
    return value, bundles


def collect(root):
    root = Path(root)
    run, bundles = load_run(root)
    reports, frames, images, sequences = [], [], {}, []
    for bundle in bundles:
        for clip in bundle['clips']:
            directory = root / 'inputs' / bundle['variant_id'] / clip['id']
            report = review_capture(bundle, clip['id'], directory, run['runtime'])
            reports.append(report)
            ordered = []
            for item in report['visual_frames']:
                source_id = bundle['variant_id'] + ':' + item['frame_id']
                raw = (directory / item['path']).read_bytes()
                # The compiler viewport is authoritative and the message builder
                # verifies these dimensions against the actual JPEG header.
                frame = dict(id=source_id, variant_id=bundle['variant_id'], clip_id=clip['id'],
                    time_seconds=item['global_time_seconds'], sha256=item['sha256'],
                    mime_type=item['mime_type'], width=1280, height=720)
                frames.append(frame)
                images[source_id] = raw
                if item['path'].split('/')[-1].startswith('a'):
                    ordered.append(frame)
            ordered.sort(key=lambda row: row['time_seconds'])
            if len(ordered) >= 3 and all(a['time_seconds'] < b['time_seconds'] for a, b in zip(ordered, ordered[1:])):
                sequences.append([row['id'] for row in ordered])
    technical = dict(schema='video.animation-lab-evaluation-technical.v1', run_identity=run['identity'],
        status='passed' if all(row['technical_status'] == 'passed' for row in reports) else 'failed',
        reports=reports, objective_scope='recorded_action_and_seek_samples',
        visual_quality='not_evaluated', production_adopted=False)
    request = build_review_request(case_identity=run['identity'], module=run['module'],
        variants=[dict(id=bundle['variant_id'], artifact_sha256=bundle['artifact_sha256'],
                       duration_seconds=bundle['duration_seconds']) for bundle in bundles],
        frames=frames, runtime_identity=run['runtime_identity'], evidence_identity=sha256(technical),
        checks=[dict(variant_id=row['variant_id'], code='native_observation', status=row['technical_status']) for row in reports],
        artifact_feedback=run['artifact_feedback'], sequence_groups=sequences)
    # Also validate all real image bytes before persisting any review request.
    build_review_messages(request, images)
    for path, value in [('technical.json', technical), ('review-request.json', request)]:
        _save(root / path, value)
    write_report(root, run, technical, request)
    return technical, request


def _review_images(root, run, technical):
    result, reports = {}, []
    # Bind to technical review again; a saved request is insufficient if evidence
    # has been changed after collect(). No new renderer/model run occurs here.
    for bundle in [_thaw(row) for row in run['bundles']]:
        for clip in bundle['clips']:
            directory = root / 'inputs' / bundle['variant_id'] / clip['id']
            report = review_capture(bundle, clip['id'], directory, run['runtime'])
            reports.append(report)
            for frame in report['visual_frames']:
                result[bundle['variant_id'] + ':' + frame['frame_id']] = (directory / frame['path']).read_bytes()
    _require(reports == technical['reports'], 'animation_evaluation_evidence_changed')
    return result


def model_review(root, *, model=None):
    root = Path(root)
    run, _ = load_run(root)
    request = json.loads((root / 'review-request.json').read_bytes())
    technical = json.loads((root / 'technical.json').read_bytes())
    _require(request['public']['case_identity'] == run['identity']
             and request['public']['evidence_identity'] == sha256(technical)
             and technical['run_identity'] == run['identity'], 'animation_evaluation_review_mismatch')
    messages = build_review_messages(request, _review_images(root, run, technical))
    owned = model is None
    if model is None:
        try:
            model = VisualModel.from_env()
        except (ModelError, OSError):
            _save(root / 'model-unavailable.json', dict(request_identity=request['identity'],
                state='unavailable_before_dispatch', post_attempts=0,
                code='visual_model_not_configured_or_credential_unavailable'))
            raise ModelError('visual_model_unavailable_before_dispatch') from None
    # Reserve the only POST before network access. Existence blocks resume POSTs
    # even after process termination or an uncertain transport failure.
    try:
        _save(root / 'model-attempt.json', dict(schema='video.animation-lab-model-attempt.v1',
            run_identity=run['identity'], request_identity=request['identity'],
            maximum_posts=1, automatic_retries=0, state='reserved_before_post'))
        try:
            raw, receipt = model.review(messages)
            _text(root / 'model-output.txt', raw)
            _save(root / 'model-receipt.json', receipt)
            review = validate_review_output(raw, request)
            selection = review['selected_variant_id']
            eligible = selection is not None and all(row['technical_status'] == 'passed'
                for row in technical['reports'] if row['variant_id'] == selection)
            review.update(eligible_for_developer_comparison=eligible,
                production_adopted=False, selection_reason='model_opinion_with_objective_veto')
            _save(root / 'model-review.json', review)
            # Suggestions referring to already measured layouts need no duplicate
            # render. This is a bounded deterministic executor, not Agent service.
            revision = revision_decision(run, review)
            _save(root / 'revision-decision.json', revision)
            write_report(root, run, technical, request, review, revision)
            return review
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[a-z0-9_]{1,120}', str(exc)) else 'visual_review_failed'
            _save(root / 'model-failure.json', dict(code=code, request_identity=request['identity'],
                automatic_retry=False, state='stopped_preserving_evidence'))
            raise
    finally:
        if owned:
            model.close()


def revision_decision(run, review):
    measured = ({row['inputs']['inputs']['plan']['template']: row['artifact_sha256'] for row in run['bundles']}
                if run['module'] == 'diagram' and all(row['inputs']['inputs'].get('plan', {}).get('schema') == 'video.motion-plan.v1' for row in run['bundles']) else {})
    suggestions = []
    for item in review['opinion']['suggestions']:
        if item['kind'] == 'no_change':
            continue
        template = item['parameters']['layout_template']
        suggestions.append(dict(template=template, issue_id=item['issue_id'],
            status='already_measured_same_input' if template in measured else 'requires_new_explicit_run',
            measured_artifact_sha256=measured.get(template)))
    return dict(schema='video.animation-lab-revision-decision.v1', run_identity=run['identity'],
        kind='finite_local_layout_executor', generic_agent_used=False, suggestions=suggestions,
        local_candidates=run['limits']['local_candidates'], model_calls=1, new_automatic_runs=0,
        outcome='same_input_comparison_complete', production_adopted=False)


def write_report(root, run, technical, request, review=None, revision=None):
    esc = lambda value: html.escape(str(value), quote=True)
    figures = []
    for frame in sorted(request['public']['frames'], key=lambda row: (row['time_seconds'], row['variant'], row['id'])):
        private = request['private']['frames'][frame['id']]
        filename = private['source_id'].split(':')[-1] + '.jpg'
        url = f"inputs/{private['variant_id']}/{private['clip_id']}/observations/{filename}"
        figures.append(f'<figure><img src="{esc(url)}" alt="{esc(frame["id"])}"><figcaption>{esc(frame["id"])} · {frame["time_seconds"]:.3f}s</figcaption></figure>')
    opinion = review['opinion'] if review else {'status': '尚未调用视觉模型'}
    conclusion = {'A': '倾向 A', 'B': '倾向 B', 'tie': '两版相当，尚未发现明确改进',
                  'inconclusive': '证据不足，暂无法判断'}.get(opinion.get('preference'), '尚未评审')
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>动画自动评测</title>
<meta name="referrer" content="no-referrer"><style>body{{font:16px/1.6 sans-serif;background:#101b2d;color:#e6edf7;margin:28px}}section{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}figure{{margin:0}}img{{width:100%}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#172640;padding:16px}}a{{color:#9fcaff}}</style>
<h1>{esc(run['title'])} · 自动评测</h1><p>技术检查：{esc(technical['status'])}。静音固定样例；画面为有序采样，不证明整段流畅或配音自然度。</p>
<p>模型意见、开发者判断、生产采用分别记录。本报告没有自动采用任何版本。</p>
<p>运行身份 <code>{esc(run['identity'])}</code></p><h2>真实采样画面（盲标识）</h2><section>{''.join(figures)}</section>
<h2>视觉模型意见：{esc(conclusion)}</h2><pre>{esc(json.dumps(opinion, ensure_ascii=False, indent=2))}</pre>
<h2>技术检查与局部修改</h2><pre>{esc(json.dumps(dict(reports=[dict(variant=row['variant_id'], status=row['technical_status'], issues=row['issues'], frames=row['frame_count']) for row in technical['reports']], revision=revision),ensure_ascii=False,indent=2))}</pre>
<p><a href="technical.json">技术记录</a> · <a href="review-request.json">冻结评审请求</a></p></html>'''
    # Report is a derived view. All underlying receipts remain write-once.
    _text(Path(root) / 'report.html', body, replace=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--runtime', type=Path, required=True)
    prep.add_argument('--case', default='diagram-connections', choices=[row['id'] for row in catalog()])
    prep.add_argument('--template', choices=['flow', 'focus'], help='diagram only; defaults to focus')
    prep.add_argument('--feedback-db', type=Path)
    prep.add_argument('--engine-assets-dir', type=Path, help='pinned local Three.js assets for the three-layers sample')
    for name in ('collect', 'model'):
        sub.add_parser(name).add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        store = AnnotationStore(args.feedback_db) if args.feedback_db else None
        run = prepare(args.output, json.loads(args.runtime.read_bytes()), case_id=args.case,
                      template=args.template, feedback_store=store, engine_assets_dir=args.engine_assets_dir)
        result = dict(status='prepared', identity=run['identity'], captures=run['limits']['captures'])
    elif args.command == 'collect':
        report, request = collect(args.run)
        result = dict(status=report['status'], review_request_identity=request['identity'])
    else:
        review = model_review(args.run)
        result = dict(status=review['status'], preference=review['opinion']['preference'],
                      eligible=review['eligible_for_developer_comparison'])
    print(json.dumps(result))


if __name__ == '__main__':
    main()
