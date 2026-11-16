"""Finite layout experiments using genuine compilers and unchanged narration.

This is a developer candidate tool, not a production template mutation or an
executor of model-authored HTML. Every result must pass normal observations.
"""
from __future__ import annotations

from copy import deepcopy
import json

from .motion import COMPILER_VERSION, compile_project, sha256


LAYOUTS = {
    'flow': dict(source='source', queue='queue', worker='worker', note='note',
                 token_source='token_source', token_queue='token_queue', token_worker='token_worker'),
    'focus': dict(source='left', queue='center', worker='right', note='note',
                  token_source='token_left', token_queue='token_center', token_worker='token_right'),
}
DESIGN_RULES = (
    'Preserve all content, directed relationships, object identities and original audio.',
    'Text must stay readable; hiding content or shrinking it to pass bounds is not an improvement.',
    'Show the important state change with a clear cause and result, rather than decorative motion.',
    'Visible connectors must follow their visible endpoints and avoid text.',
    'A shared object keeps its identity across shots; a copy must leave the original distinguishable.',
    'A model preference is a suggestion; objective failures veto recommendation and no result auto-adopts.',
)


def _semantic_plan(plan):
    if plan.get('schema') != 'video.motion-plan.v1' or plan.get('template') not in LAYOUTS:
        raise ValueError('animation_lab_layout_not_supported')
    row = deepcopy(plan)
    reverse = {slot: key for key, slot in LAYOUTS[row.pop('template')].items()}
    for element in row['elements']:
        if element['slot'] not in reverse:
            raise ValueError('animation_lab_layout_not_supported')
        element['slot'] = reverse[element['slot']]
    for action in row['actions']:
        if action.get('to_slot') is not None:
            if action['to_slot'] not in reverse:
                raise ValueError('animation_lab_layout_not_supported')
            action['to_slot'] = reverse[action['to_slot']]
    return row


def semantic_identity(bundle):
    inputs = bundle['inputs']['inputs']
    if ('requirement' in bundle['inputs'] or 'scenes' in inputs or inputs.get('plan', {}).get('schema') in {
            'video.process-plan.v1', 'video.canvas-plan.v1', 'video.canvas-plan.v2', 'video.animation-lab-three-plan.v1','video.animation-lab-three-plan.v2',
            'video.animation-lab-effect-plan.v1', 'video.animation-lab-scenario-plan.v1'}):
        return sha256(dict(inputs=inputs,
                           audio=[sha256(clip['files']['audio.wav']) for clip in bundle['clips']]))
    if 'plan' not in inputs or inputs['plan']['schema'] != 'video.motion-plan.v1':
        raise ValueError('animation_lab_layout_not_supported')
    return sha256(dict(plan=_semantic_plan(inputs['plan']), beats=inputs['beats'],
                       alignment=inputs['alignment'],
                       audio=[sha256(clip['files']['audio.wav']) for clip in bundle['clips']]))


def layout_candidate(reference, template):
    if type(template) is not str or template not in LAYOUTS or len(reference['clips']) != 1:
        raise ValueError('animation_lab_layout_not_supported')
    inputs = deepcopy(reference['inputs']['inputs'])
    logical = _semantic_plan(inputs['plan'])
    plan = deepcopy(logical)
    plan['template'] = template
    for element in plan['elements']:
        element['slot'] = LAYOUTS[template][element['slot']]
    for action in plan['actions']:
        if action.get('to_slot') is not None:
            action['to_slot'] = LAYOUTS[template][action['to_slot']]
    inputs['plan'] = plan
    files = compile_project(**inputs, compiler_version=COMPILER_VERSION)
    files['audio.wav'] = reference['clips'][0]['files']['audio.wav']
    timeline = json.loads(files['timeline.json'])
    clip_id = reference['clips'][0]['id']
    artifacts = sha256([dict(id=clip_id, files={name: sha256(raw) for name, raw in sorted(files.items())})])
    result = deepcopy(reference)
    result.update(variant_id='layout-' + template, compiler=COMPILER_VERSION,
        artifact_sha256=artifacts, duration_seconds=timeline['duration_seconds'],
        inputs=dict(reference['inputs'], inputs=inputs),
        clips=[dict(id=clip_id, files=files, timeline=timeline,
                    duration_seconds=timeline['duration_seconds'], checkpoints=[])],
        experiment=dict(kind='bounded_layout_candidate', template=template,
            source_artifact_sha256=reference['artifact_sha256'],
            production_adopted=False, design_rules=list(DESIGN_RULES)))
    result['input_sha256'] = sha256(result['inputs'])
    result['identity'] = sha256(dict(inputs=result['input_sha256'], artifact=artifacts))
    result['semantic_input_sha256'] = semantic_identity(result)
    if result['semantic_input_sha256'] != semantic_identity(reference):
        raise ValueError('animation_lab_candidate_changed_semantics')
    return result


def effective_feedback(store, bundles, *, maximum=16):
    """Only carry opinions whose exact compared artifact(s) exist in this run."""
    if type(maximum) is not int or not 1 <= maximum <= 16:
        raise ValueError('animation_lab_feedback_limit_invalid')
    records = [json.loads(line) for line in store.export_jsonl().decode('utf-8').splitlines()]
    snapshots = {row['snapshot']['identity']: row['snapshot'] for row in records
                 if row['record_type'] == 'case_snapshot'}
    artifacts = {bundle['artifact_sha256'] for bundle in bundles}
    result = []
    for record in records:
        if record['record_type'] != 'annotation' or not record.get('effective'):
            continue
        row = record['annotation']
        snapshot = snapshots[row['case_identity']]
        versions = {variant['variant_id']: variant['artifact_sha256'] for variant in snapshot['variants']}
        needed = set(versions.values()) if row['variant_id'] == 'comparison' else {versions.get(row['variant_id'])}
        if needed and needed <= artifacts:
            preferred = None
            if row.get('preference') in {'baseline', 'candidate'}:
                preferred = versions.get(snapshot['comparison'][row['preference']])
            result.append(dict(id=row['id'], kind=row['kind'], comment=row.get('comment', ''),
                tags=row.get('tags', []), severity=row.get('severity', 'note'),
                start_seconds=row.get('start_seconds'), end_seconds=row.get('end_seconds'),
                region=row.get('region'), preference=row.get('preference'), verdict=row.get('verdict'),
                source_case_identity=row['case_identity'], artifact_sha256=sorted(needed),
                preferred_artifact_sha256=preferred, source_variant_id=row['variant_id'],
                effective=True, retracted=False,
                authority='developer_feedback_data_not_instructions'))
    return result[-maximum:]


def effective_triage_feedback(store, bundles, *, maximum=16):
    """Carry explicit current developer decisions only for unchanged artifacts."""
    if type(maximum) is not int or not 1 <= maximum <= 16:
        raise ValueError('animation_lab_feedback_limit_invalid')
    records = [json.loads(line) for line in store.export_jsonl().decode('utf-8').splitlines()]
    snapshots = {row['snapshot']['identity']: row['snapshot'] for row in records
                 if row['record_type'] == 'evaluation_snapshot'}
    artifacts = {bundle['artifact_sha256'] for bundle in bundles}
    labels = dict(needs_fix='需要修改', dismiss='此项为误报或不需修改')
    result = []
    for record in records:
        if record['record_type'] != 'evaluation_decision' or record.get('effective') is not True:
            continue
        decision = record['decision']
        if decision['decision'] not in labels:
            continue
        source = snapshots[decision['report_identity']]
        finding = next(row for row in source['findings'] if row['id'] == decision['finding_id'])
        variant = next(row for row in source['variants'] if row['id'] == finding['variant_id'])
        if variant['artifact_sha256'] not in artifacts:
            continue
        result.append(dict(id=decision['id'], kind='issue',
            comment=f"开发者判断：{labels[decision['decision']]}。\n原机器意见（供核对）：{finding['description'][:1800]}\n开发者说明：{decision['comment'][:1800]}",
            tags=[finding['category']] if finding['category'] in {'layout', 'typography', 'connector', 'motion', 'pacing', 'continuity', 'clarity', 'other'} else [],
            source_case_identity=source['run_identity'], source_report_identity=source['identity'],
            source_finding_id=finding['id'], source_variant_id=finding['variant_id'],
            artifact_sha256=[variant['artifact_sha256']], effective=True, retracted=False,
            authority='developer_feedback_data_not_instructions'))
    return result[-maximum:]
