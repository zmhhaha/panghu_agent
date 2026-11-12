"""Joint narration/visual planning, with autonomous segmentation and provenance."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from ..contracts import canonical_bytes
from .video_explanation_contract import (
    INPUT_MAX_BYTES, OUTPUT_MAX_BYTES, FACT_REVIEW_WARNING,
    _fields, _text, _number, _strings, _pronunciation, _require,
)
from .video_animation_production_contract import validate_delivery
from .video_references import reference_urls, preserve_project_references


def planning_method_task(document):
    content = {key: value for key, value in document.items() if key != 'method_delivery'}
    return dict(title=content['project']['title'], narration=content['project']['script'],
                style=content['project']['style'], instruction=content['instruction'], planning_input=content)


def validate_joint_plan_input(document):
    _fields(document, {'schema_version', 'project', 'scenes', 'brief', 'instruction',
                       'preserve_script', 'method_delivery', 'allowed_engines'})
    _require(document['schema_version'] == 'video-joint-plan-input.v1')
    project = document['project']
    _fields(project, {'title', 'script', 'style', 'voice'})
    for key, maximum, empty in [('title', 300, False), ('script', 50000, False),
                                ('style', 4000, True), ('voice', 200, False)]:
        _text(project[key], maximum, empty=empty)
    brief = document['brief']
    _fields(brief, {'audience', 'goal', 'tone'})
    _text(brief['audience'], 300, empty=True)
    _text(brief['goal'], 1200, empty=True)
    _require(type(brief['tone']) is str and brief['tone'] in {'conversational', 'precise', 'storytelling'})
    _text(document['instruction'], 2000, empty=True)
    _require(type(document['preserve_script']) is bool)
    if document['preserve_script']:
        # Reject material that cannot fit the exact-output contract before a
        # paid request. The planner cannot rewrite forbidden prose or exceed
        # the total 24 x 2000 character speech boundary in verbatim mode.
        _text(project['script'], 48000, prose=True)
    engines = document['allowed_engines']
    _require(type(engines) is list and bool(engines)
             and all(type(x) is str for x in engines) and len(set(engines)) == len(engines)
             and set(engines) <= {'html-svg', 'canvas', 'three'})
    scenes = document['scenes']
    _require(type(scenes) is list and len(scenes) <= 24)
    seen = set()
    for scene in scenes:
        _fields(scene, {'scene_id', 'title', 'narration', 'visual_description',
                        'duration_seconds', 'format', 'speech'})
        _text(scene['scene_id'], 100)
        _require(scene['scene_id'] not in seen)
        seen.add(scene['scene_id'])
        for key, maximum in [('title', 300), ('narration', 10000), ('visual_description', 10000)]:
            _text(scene[key], maximum)
        _number(scene['duration_seconds'], 1, 120)
        _require(type(scene['format']) is str and scene['format'] in {'svg', 'html'})
        _fields(scene['speech'], {'speed', 'pronunciation'})
        _number(scene['speech']['speed'], 0.5, 2)
        _pronunciation(scene['speech']['pronunciation'])
    delivery = document['method_delivery']
    if delivery is not None:
        validate_delivery(delivery)
        _require(delivery['allowed_engines'] == engines, 'joint_plan_method_engines_mismatch')
        _require(delivery['task_sha256'] == hashlib.sha256(canonical_bytes(planning_method_task(document))).hexdigest(),
                 'joint_plan_method_task_mismatch')
    reference_urls(project['script'], document['instruction'],
                   *(row['visual_description'] for row in scenes))
    _require(len(canonical_bytes(document)) <= INPUT_MAX_BYTES, 'joint_plan_input_too_large')
    return deepcopy(document)


def validate_planning_method_decision(delivery, decision, segment_ids):
    """Planning intentions only; source application is validated by the author."""
    if delivery is None:
        _require(decision is None, 'joint_plan_unoffered_method')
        return
    _fields(decision, {'delivery_sha256', 'decisions'})
    _require(decision['delivery_sha256'] == delivery['sha256'], 'joint_plan_method_binding_mismatch')
    offered = {row['id']: row['content_sha256'] for row in delivery['methods']}
    rows = decision['decisions']
    _require(type(rows) is list and len(rows) == len(offered))
    seen = set()
    for row in rows:
        _fields(row, {'version_id', 'content_sha256', 'action', 'reason', 'segment_ids'})
        identity = row['version_id']
        _require(type(identity) is str and identity in offered and identity not in seen
                 and row['content_sha256'] == offered[identity], 'joint_plan_unoffered_method')
        seen.add(identity)
        _require(type(row['action']) is str and row['action'] in {'apply', 'adapt', 'reject'})
        _text(row['reason'], 4000, prose=True)
        targets = row['segment_ids']
        _require(type(targets) is list and all(type(t) is str for t in targets)
                 and len(targets) == len(set(targets)) and set(targets) <= segment_ids
                 and bool(targets) == (row['action'] != 'reject'), 'joint_plan_method_targets_invalid')


def validate_joint_plan_result(result, input_sha256, input_document):
    context = validate_joint_plan_input(input_document)
    _require(type(input_sha256) is str and re.fullmatch('[0-9a-f]{64}', input_sha256)
             and hashlib.sha256(canonical_bytes(context)).hexdigest() == input_sha256,
             'joint_plan_input_binding_mismatch')
    _fields(result, {'schema_version', 'input_sha256', 'summary', 'understanding_goal',
                     'visual_style', 'continuity', 'scenes', 'method_decision', 'warnings'})
    _require(result['schema_version'] == 'video-joint-plan-result.v1'
             and result['input_sha256'] == input_sha256, 'joint_plan_input_binding_mismatch')
    for key in ('summary', 'understanding_goal', 'visual_style', 'continuity'):
        _text(result[key], 4000, prose=True)
    scenes = result['scenes']
    _require(type(scenes) is list and 1 <= len(scenes) <= 24)
    seen = set()
    original_ids = {row['scene_id'] for row in context['scenes']}
    source_texts = [context['project']['script'], *(row['narration'] for row in context['scenes'])]
    for scene in scenes:
        _fields(scene, {'segment_id', 'source_scene_ids', 'title', 'narration', 'visual_description',
                        'duration_seconds', 'pronunciation', 'key_points', 'expression',
                        'visual_steps', 'delivery_note', 'source_quotes'})
        identity = scene['segment_id']
        _require(type(identity) is str and re.fullmatch('[a-z][a-z0-9_-]{0,39}', identity)
                 and identity not in seen)
        seen.add(identity)
        sources = scene['source_scene_ids']
        _require(type(sources) is list and all(type(x) is str for x in sources)
                 and len(sources) == len(set(sources)) and set(sources) <= original_ids)
        for key, maximum in [('title', 160), ('narration', 2000), ('visual_description', 8000),
                             ('expression', 2000), ('delivery_note', 400)]:
            _text(scene[key], maximum, empty=key == 'delivery_note', prose=True)
        _number(scene['duration_seconds'], 1, 60)
        _pronunciation(scene['pronunciation'], scene['narration'], prose=True)
        rules, narration = scene['pronunciation'], scene['narration']
        spoken = re.sub('|'.join(re.escape(key) for key in sorted(rules, key=len, reverse=True)),
                        lambda m: rules[m.group()], narration) if rules else narration
        _require(len(spoken) <= 2000 and len(spoken.encode('utf-8')) <= 16 * 1024,
                 'joint_plan_narration_requires_split')
        _strings(scene['key_points'], 1, 6, 240, prose=True)
        _strings(scene['visual_steps'], 1, 10, 500, prose=True)
        _strings(scene['source_quotes'], 1, 8, 4000)
        _require(all(any(quote in text for text in source_texts) for quote in scene['source_quotes']),
                 'joint_plan_source_quote_not_found')
    if context['preserve_script']:
        _require(''.join(row['narration'] for row in scenes) == context['project']['script'],
                 'joint_plan_script_not_preserved')
    validate_planning_method_decision(context['method_delivery'], result['method_decision'], seen)
    _strings(result['warnings'], 0, 16, 1000, prose=True)
    # Preserve supplied provenance in visuals and reject invented links anywhere.
    inputs = [*source_texts, context['instruction'],
              *(row['visual_description'] for row in context['scenes'])]
    supplied = set(reference_urls(*inputs))
    outputs = [result[key] for key in ('summary', 'understanding_goal', 'visual_style', 'continuity')]
    for row in scenes:
        outputs.extend(row[key] for key in ('title', 'narration', 'visual_description', 'expression', 'delivery_note'))
        outputs.extend(row['visual_steps'] + row['key_points'] + row['source_quotes'])
        outputs.extend(row['pronunciation'].keys())
        outputs.extend(row['pronunciation'].values())
    if result['method_decision'] is not None:
        outputs.extend(row['reason'] for row in result['method_decision']['decisions'])
    outputs.extend(result['warnings'])
    _require(set(reference_urls(*outputs)) <= supplied, 'video_reference_not_in_input')
    checked = deepcopy(result)
    checked['scenes'] = preserve_project_references(checked['scenes'], *inputs)
    checked['warnings'] = [FACT_REVIEW_WARNING] + [
        row for row in checked['warnings'] if row != FACT_REVIEW_WARNING][:15]
    _require(len(canonical_bytes(checked)) <= OUTPUT_MAX_BYTES, 'joint_plan_output_too_large')
    return checked
