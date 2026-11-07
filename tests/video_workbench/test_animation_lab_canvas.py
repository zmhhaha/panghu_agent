"""Canvas project input and provenance checks; these do not claim pixel quality."""
from copy import deepcopy
import json

import pytest

from video_workbench.animation_lab_canvas import (
    COMPILER_VERSION, CONTEXT_COMPILER_VERSION, CONTEXT_PLAN_SCHEMA,
    compile_canvas_project, example_canvas_plan, validate_canvas_plan,
)
from video_workbench.motion import estimated_alignment, make_beats, sha256


@pytest.fixture
def canvas_inputs():
    beats = make_beats('请求同时进入。队列先接住请求。执行节点逐个完成。')
    alignment = estimated_alignment(beats, 'b'*64, 18)
    return example_canvas_plan(), beats, alignment


def test_canvas_variants_preserve_exact_semantic_source_and_audio(canvas_inputs):
    plan, beats, alignment = canvas_inputs
    original = deepcopy(canvas_inputs)
    baseline = compile_canvas_project(plan, beats, alignment, variant='baseline')
    candidate = compile_canvas_project(plan, beats, alignment, variant='candidate')
    assert canvas_inputs == original
    for name in ('plan.json', 'beats.json', 'alignment.json'):
        assert baseline[name] == candidate[name]
    assert baseline['index.html'] != candidate['index.html']
    for files in (baseline, candidate):
        timeline, manifest = (json.loads(files[name]) for name in ('timeline.json', 'manifest.json'))
        assert timeline['audio_sha256'] == manifest['audio_sha256'] == alignment['audio_sha256']
        assert timeline['duration_seconds'] == alignment['audio_seconds']
        assert manifest['compiler'] == COMPILER_VERSION
        assert manifest['engine'] == 'canvas' and manifest['developer_only'] is True
        assert manifest['files'] == {name: sha256(raw) for name, raw in files.items() if name != 'manifest.json'}


def test_repeat_compile_is_byte_identical_and_input_change_gets_new_binding(canvas_inputs):
    first = compile_canvas_project(*canvas_inputs)
    assert first == compile_canvas_project(*deepcopy(canvas_inputs))
    changed = deepcopy(canvas_inputs)
    changed[0]['seed'] += 1
    second = compile_canvas_project(*changed)
    assert second['plan.json'] != first['plan.json']
    assert json.loads(second['manifest.json'])['plan_sha256'] != json.loads(first['manifest.json'])['plan_sha256']
    assert second['beats.json'] == first['beats.json']


@pytest.mark.parametrize('key,value', [
    ('schema', 'video.motion-plan.v1'),
    ('template', 'arbitrary-code'),
    ('packet_count', True),
    ('packet_count', 13),
    ('packet_count', 2),
    ('seed', -1),
    ('seed', True),
    ('seed', 65536),
    ('title', 'x'*33),
    ('takeaway', 'x'*69),
    ('title', 'bad\0text'),
    ('labels', dict(source='过长的队列入口标签', queue='队列', worker='执行器')),
])
def test_unbounded_or_unsupported_plan_is_rejected(canvas_inputs, key, value):
    plan, beats, alignment = canvas_inputs
    plan[key] = value
    with pytest.raises(ValueError, match='animation_lab_canvas_plan_invalid'):
        compile_canvas_project(plan, beats, alignment)


def test_unknown_plan_fields_cannot_smuggle_programs(canvas_inputs):
    plan, beats, alignment = canvas_inputs
    plan['script'] = 'while(true){}'
    with pytest.raises(ValueError):
        compile_canvas_project(plan, beats, alignment)


def test_plan_text_is_data_in_both_html_and_script_contexts(canvas_inputs):
    plan, beats, alignment = canvas_inputs
    plan['title'] = '</script><script>x</script>'
    compiled = compile_canvas_project(plan, beats, alignment)
    page = compiled['index.html'].decode()
    assert plan['title'] not in page
    assert '&lt;/script&gt;&lt;script&gt;x&lt;/script&gt;' in page
    assert '\\u003c/script\\u003e\\u003cscript\\u003ex\\u003c/script\\u003e' in page
    assert json.loads(compiled['plan.json'])['title'] == plan['title']


@pytest.mark.parametrize('duration', [3, 60])
def test_extreme_allowed_duration_keeps_all_action_windows_inside_clock(canvas_inputs, duration):
    plan, beats, _ = canvas_inputs
    plan['packet_count'] = 12
    alignment = estimated_alignment(beats, 'c'*64, duration)
    timeline = json.loads(compile_canvas_project(plan, beats, alignment)['timeline.json'])
    assert len({row['target'] for row in timeline['actions']}) == 12
    assert all(row['start'] >= 0 and row['duration'] > 0
               and row['start']+row['duration'] <= duration for row in timeline['actions'])


@pytest.mark.parametrize('duration', [2.9, 60.1])
def test_unsupported_duration_is_rejected(canvas_inputs, duration):
    plan, beats, _ = canvas_inputs
    alignment = estimated_alignment(beats, 'c'*64, duration)
    with pytest.raises(ValueError, match='animation_lab_canvas_duration_invalid'):
        compile_canvas_project(plan, beats, alignment)


def test_stale_spoken_alignment_cannot_label_a_new_canvas_project(canvas_inputs):
    plan, _, alignment = canvas_inputs
    changed_beats = make_beats('不同旁白。不同队列。不同执行器。')
    with pytest.raises(ValueError, match='animation_lab_canvas_alignment_invalid'):
        compile_canvas_project(plan, changed_beats, alignment)


@pytest.mark.parametrize('variant', [None, 'invented', 1])
def test_only_finite_variants_are_allowed(canvas_inputs, variant):
    with pytest.raises(ValueError, match='animation_lab_canvas_variant_invalid'):
        compile_canvas_project(*canvas_inputs, variant=variant)


def test_plan_validation_returns_an_isolated_value():
    original = example_canvas_plan()
    checked = validate_canvas_plan(original)
    original['labels']['source'] = 'changed'
    assert checked['labels']['source'] != original['labels']['source']


def contextual_inputs():
    plan = example_canvas_plan()
    plan.update(schema=CONTEXT_PLAN_SCHEMA, title='门诊登记单依次完成',
                labels=dict(source='登记区', queue='待诊区', worker='诊室'),
                context=dict(noun='登记单', source_detail='登记单依次到达', queue_detail='等待空闲诊室',
                    worker_detail='逐张领取并完成', phase_intro='登记单先进入待诊区',
                    phase_queue='待诊区暂存登记单', phase_work='诊室逐张领取登记单', phase_done='所有登记单已处理完成'))
    beats = make_beats('登记单先进入待诊区。诊室逐张领取。最后所有登记单完成。')
    return plan, beats, estimated_alignment(beats, 'c'*64, 18)


def test_context_v2_binds_domain_language_and_actual_drawn_text_without_changing_geometry():
    args = contextual_inputs()
    files = compile_canvas_project(*args)
    page = files['index.html'].decode()
    timeline, manifest = (json.loads(files[key]) for key in ('timeline.json', 'manifest.json'))
    assert timeline['compiler'] == manifest['compiler'] == CONTEXT_COMPILER_VERSION
    assert json.loads(files['plan.json'])['schema'] == CONTEXT_PLAN_SCHEMA
    assert "plan.context[id+'_detail']" in page
    assert 'plan.context.phase_intro' in page and 'plan.context.phase_done' in page
    assert "ctx.measureText(String(label))" in page and 'text_boxes:drawnText' in page
    assert '.eyebrow{font-size:14px}' in page
    assert '请求全部完成' not in page and '请求到达速度超过处理速度' not in page
    assert '同时到来的请求' not in page and '执行节点持续领取' not in page
    assert all('请求' not in row['label'] and '执行节点' not in row['label'] for row in timeline['actions'])
    assert '登记单出现' in {row['label'] for row in timeline['actions']}
    old_plan = deepcopy(args[0]); old_plan.pop('context'); old_plan['schema'] = 'video.canvas-plan.v1'
    old = json.loads(compile_canvas_project(old_plan, args[1], args[2])['timeline.json'])
    geometry = lambda rows: [{key: value for key, value in row.items() if key != 'label'} for row in rows]
    assert geometry(old['actions']) == geometry(timeline['actions'])


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(schema='video.canvas-plan.v1'),
    lambda p: p.pop('context'),
    lambda p: p['context'].update(extra='unexpected program'),
    lambda p: p['context'].update(noun='too long an object noun'),
    lambda p: p['context'].update(source_detail='说明'*8),
    lambda p: p['context'].update(phase_work='bad\0text'),
    lambda p: p['context'].pop('phase_done'),
])
def test_context_v2_rejects_schema_smuggling_unknown_and_unbounded_language(mutation):
    plan, beats, alignment = contextual_inputs()
    mutation(plan)
    with pytest.raises(ValueError, match='canvas_plan_invalid'):
        compile_canvas_project(plan, beats, alignment)


def test_context_language_remains_data_in_the_script_context():
    plan, beats, alignment = contextual_inputs()
    plan['context']['phase_done'] = '</script><script>x</script>'
    page = compile_canvas_project(plan, beats, alignment)['index.html'].decode()
    assert plan['context']['phase_done'] not in page
    assert '\\u003c/script\\u003e\\u003cscript\\u003ex\\u003c/script\\u003e' in page


def test_v1_still_uses_original_source_and_compiler_identity(canvas_inputs):
    files = compile_canvas_project(*canvas_inputs)
    page = files['index.html'].decode()
    assert json.loads(files['timeline.json'])['compiler'] == COMPILER_VERSION
    assert 'text_boxes:drawnText' not in page and 'let drawnText=[]' not in page
    assert '请求全部完成' in page
