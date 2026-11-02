"""Stopped motion candidates, strict semantic data and opt-in disabled catalogs."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_service import catalog
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video, video_motion as motion
from agent_service.profiles.video_motion_contract import (DEFAULT_TEMPLATES, DEMONSTRATION_TEMPLATES,
    TEMPLATE_SLOTS, validate_beats, validate_motion_plan)
from agent_service.registry import TaskContract
from test_catalog import image_packages


ATTEMPT = 'motion-attempt'
BEAT = 'beat_0123456789abcdef'


def beats():
    text = '请求进入队列。'
    return dict(schema='video.beats.v1', source_text=text, spoken_text=text, segments=[dict(
        id=BEAT, source_start=0, source_end=len(text), spoken_start=0, spoken_end=len(text),
        source_text=text, spoken_text=text)])


def plan(template='flow'):
    slots = {'flow': ('source', 'queue'), 'compare': ('before', 'after'), 'focus': ('left', 'center')}
    source, target = slots[template]
    return dict(schema='video.motion-plan.v1', template=template, title='请求如何排队',
        takeaway='队列把接收和处理分开', elements=[
            dict(id='input', kind='card', label='请求', detail='先接收', slot=source),
            dict(id='queue', kind='card', label='队列', detail='等待处理', slot=target),
            dict(id='request', kind='token', label='任务', detail='', slot='token_' + source)],
        relations=[dict(id='route', source='input', target='queue', label='入队')],
        actions=[dict(beat_id=BEAT, target='request', kind='move', placement='start',
                      pace='normal', to_slot='token_' + target),
                 dict(beat_id=BEAT, target='route', kind='draw', placement='middle',
                      pace='quick', to_slot=None)])


def document():
    return dict(schema='video.motion-input.v1', title='排队', style='清楚易懂',
                visual_description='请求从入口进入队列，再由工作节点处理',
                beats=beats(), instruction='', base_plan=None)


def prepare(tmp_path):
    profile = implementation('video.motion-plan.v1')
    (tmp_path / 'inputs').mkdir()
    raw = canonical_bytes(document())
    (tmp_path / 'inputs/motion-input.json').write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(plan()))
    envelope = SimpleNamespace(profile=profile.name, profile_revision='1', attempt_id=ATTEMPT,
        task=dict(input_sha256=hashlib.sha256(raw).hexdigest()),
        inputs=[InlineFile.from_bytes('motion-input.json', raw)], checkpoint_inputs=[], checkpoint_ref=None)
    return profile, envelope


def collect(profile, tmp_path, envelope, proof=None):
    return profile.collect_output(tmp_path, envelope,
        proof or StopEvidence(ATTEMPT, True, ('stopped-worker',)))


def test_motion_registered_separately_with_read_write_and_pinned_schema():
    handler = implementation('video.motion-plan.v1')
    assert handler.required_tools == ('read', 'write')
    contract = TaskContract(name=handler.name, revision='1', task_schema=handler.task_schema,
        output_schema=handler.output_schema, required_tools=['read', 'write'],
        required_model_capabilities=['text', 'tool_calls'])
    assert contract.limits()['max_input_files'] == 1
    assert 'inputs/motion-input.json' in handler.build_prompt(dict(input_sha256='a' * 64))
    assert set(motion.verified_bundle()) == motion.BUNDLE_FILES
    assert hashlib.sha256((video.BUNDLE_ROOT / 'manifest.json').read_bytes()).hexdigest() == (
        'd5a5fcb5081d65acce5783f72effe3fafe1954613e62d6dc5471cbfe2cd8a681')
    assert set(catalog.VIDEO_PROFILES) == {'video.storyboard.v1', 'video.scene.v1'}


def test_collector_binds_exact_input_and_stopped_output(tmp_path):
    handler, envelope = prepare(tmp_path)
    handler.validate_inputs(tmp_path, envelope.task)
    files = collect(handler, tmp_path, envelope)
    assert [f.name for f in files] == ['motion-result.json']
    result = video.decode_video_json(files[0].content())
    assert motion.validate_motion_result(result, envelope.task['input_sha256'], beats()) == result
    assert result['plan'] == plan()
    result['plan']['elements'][0]['label'] = 'changed'
    assert video.decode_video_json(files[0].content())['plan'] == plan()


@pytest.mark.parametrize('change', ['input_bytes', 'input_hash', 'ref_name', 'extra_input',
    'checkpoint', 'revision', 'profile', 'noncanonical', 'not_stopped', 'wrong_attempt'])
def test_collector_refuses_changed_execution_or_input(tmp_path, change):
    handler, envelope = prepare(tmp_path)
    proof = None
    if change == 'input_bytes':
        value = document()
        value['title'] = 'changed'
        (tmp_path / 'inputs/motion-input.json').write_bytes(canonical_bytes(value))
    elif change == 'input_hash':
        envelope.task['input_sha256'] = 'b' * 64
    elif change == 'ref_name':
        envelope.inputs = [InlineFile.from_bytes('video-input.json', canonical_bytes(document()))]
    elif change == 'extra_input':
        (tmp_path / 'inputs/extra.txt').write_text('extra')
    elif change == 'checkpoint':
        envelope.checkpoint_ref = object()
    elif change == 'revision':
        envelope.profile_revision = 'v1'
    elif change == 'profile':
        envelope.profile = 'video.scene.v1'
    elif change == 'noncanonical':
        (tmp_path / 'inputs/motion-input.json').write_text(json.dumps(document()), encoding='utf-8')
    elif change == 'not_stopped':
        proof = StopEvidence(ATTEMPT, False, ('worker',))
    else:
        proof = StopEvidence('another-attempt', True, ('worker',))
    with pytest.raises(ServiceError):
        collect(handler, tmp_path, envelope, proof)


@pytest.mark.parametrize('template', ['flow', 'compare', 'focus'])
def test_three_bounded_template_plans_have_no_audio_timing_dependency(template):
    value = plan(template)
    result = validate_motion_plan(value, beats())
    assert result == value and result is not value and result['elements'] is not value['elements']
    assert 'audio' not in canonical_bytes(result).decode()


def demonstration_plan():
    def element(identity, kind, slot, label):
        return dict(id=identity, kind=kind, label=label, detail='', slot=slot)

    def action(target, kind, slot=None, placement='start'):
        return dict(beat_id=BEAT, target=target, kind=kind, placement=placement,
                    pace='normal', to_slot=slot)

    return dict(schema='video.motion-plan.v1', template='demonstration', title='回答如何进入历史',
        takeaway='复制完整回答，尝试记录保留在日志中', elements=[
            element('log', 'document', 'log_center', '执行日志'),
            element('answer', 'record', 'answer_center', '完整回答'),
            element('attempt', 'attempt', 'attempt_center', '尝试记录'),
            element('copy', 'record', 'answer_left', '完整回答'),
            element('context', 'terminal', 'context_right', '对话历史'),
            element('retained', 'label', 'result_left', '尝试保留'),
            element('projected', 'label', 'result_right', '回答进入历史')], relations=[], actions=[
            action('log', 'move', 'log_left'), action('answer', 'move', 'answer_left'),
            action('attempt', 'move', 'attempt_left'), action('context', 'reveal'),
            action('copy', 'reveal', placement='middle'),
            action('copy', 'move', 'answer_right', placement='end'),
            action('retained', 'reveal', placement='end'),
            action('projected', 'reveal', placement='end')])


def test_demonstration_requires_explicit_experimental_template_selection():
    value = demonstration_plan()
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(value, beats())
    result = validate_motion_plan(value, beats(), allowed_templates=DEMONSTRATION_TEMPLATES)
    assert result == value and result is not value and result['elements'] is not value['elements']
    result['elements'][0]['label'] = 'changed'
    assert value['elements'][0]['label'] == '执行日志'
    assert DEFAULT_TEMPLATES == frozenset({'flow', 'compare', 'focus'})
    assert set(TEMPLATE_SLOTS) == DEFAULT_TEMPLATES | DEMONSTRATION_TEMPLATES


@pytest.mark.parametrize('template', ['flow', 'compare', 'focus'])
def test_experiment_opt_in_does_not_change_original_template_results(template):
    value = plan(template)
    assert validate_motion_plan(value, beats()) == validate_motion_plan(value, beats(),
        allowed_templates=DEFAULT_TEMPLATES | DEMONSTRATION_TEMPLATES)
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(value, beats(), allowed_templates=DEMONSTRATION_TEMPLATES)
    # Opting in to a new template never makes card movement valid in an old one.
    value['actions'][0]['target'] = 'input'
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(value, beats(), allowed_templates=DEFAULT_TEMPLATES | DEMONSTRATION_TEMPLATES)


@pytest.mark.parametrize('allowed', [(), [], {}, 'demonstration', True, ['unknown'], [True]],
                         ids=['empty-tuple', 'empty-list', 'mapping', 'string', 'bool', 'unknown', 'non-string'])
def test_template_opt_in_is_a_bounded_trusted_caller_option(allowed):
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(demonstration_plan(), beats(), allowed_templates=allowed)


@pytest.mark.parametrize('change', ['unknown_kind', 'cross_kind_slot', 'unknown_slot', 'coordinates',
    'relation', 'draw', 'highlight', 'copy_action', 'terminal_move', 'label_move', 'record_to_document',
    'document_to_record', 'attempt_to_record', 'token_slot', 'scale', 'schema', 'copy_of', 'model_opt_in'])
def test_demonstration_rejects_unbounded_shapes_and_unsupported_actions(change):
    value = demonstration_plan()
    if change == 'unknown_kind':
        value['elements'][0]['kind'] = 'browser'
    elif change == 'cross_kind_slot':
        value['elements'][0]['slot'] = 'answer_right'
    elif change == 'unknown_slot':
        value['elements'][0]['slot'] = 'free_canvas'
    elif change == 'coordinates':
        value['elements'][0]['x'] = 20
    elif change == 'relation':
        value['relations'].append(dict(id='link', source='answer', target='context', label='复制'))
    elif change in {'draw', 'highlight', 'copy_action'}:
        value['actions'][0]['kind'] = {'draw': 'draw', 'highlight': 'highlight', 'copy_action': 'copy'}[change]
        value['actions'][0]['to_slot'] = None
    elif change in {'terminal_move', 'label_move'}:
        value['actions'][0]['target'] = 'context' if change == 'terminal_move' else 'retained'
        value['actions'][0]['to_slot'] = 'context_right' if change == 'terminal_move' else 'result_right'
    elif change == 'record_to_document':
        value['actions'][1]['to_slot'] = 'log_left'
    elif change == 'document_to_record':
        value['actions'][0]['to_slot'] = 'answer_left'
    elif change == 'attempt_to_record':
        value['actions'][2]['to_slot'] = 'answer_left'
    elif change == 'token_slot':
        value['actions'][1]['to_slot'] = 'token_queue'
    elif change == 'scale':
        value['actions'][0]['scale'] = 2
    elif change == 'schema':
        value['schema'] = 'video.motion-plan.v2'
    elif change == 'copy_of':
        value['elements'][3]['copy_of'] = 'answer'
    else:
        value['allowed_templates'] = ['demonstration']
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(value, beats(), allowed_templates=DEMONSTRATION_TEMPLATES)


def test_published_motion_profile_never_accepts_demonstration_input_or_output(tmp_path):
    handler, envelope = prepare(tmp_path)
    value = demonstration_plan()
    assert 'demonstration' not in handler.build_prompt(envelope.task)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(value))
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        collect(handler, tmp_path, envelope)
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        motion.validate_motion_result(dict(schema='video.motion-result.v1',
            input_sha256=envelope.task['input_sha256'], plan=value), envelope.task['input_sha256'], beats())
    changed_input = document()
    changed_input['base_plan'] = value
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        motion.validate_motion_input(changed_input)
    assert hashlib.sha256((motion.BUNDLE_ROOT / 'manifest.json').read_bytes()).hexdigest() == (
        '4eba854c6f858a6ec18eb01687a87865dca53efc1346eb4456e6a47bd9dac104')


@pytest.mark.parametrize('change', ['code', 'url', 'timestamp', 'dangling_beat', 'dangling_target',
    'dangling_relation', 'duplicate_id', 'duplicate_slot', 'wrong_kind_slot', 'move_card',
    'draw_card', 'reveal_relation', 'move_unknown_slot', 'extra_to_slot', 'conflict',
    'empty_actions', 'many_actions', 'long_text', 'boolean_slot', 'bad_template', 'bad_id'])
def test_plan_rejects_unbounded_or_conflicting_model_data(change):
    value = plan()
    if change in {'code', 'url', 'timestamp'}:
        value[{'code': 'javascript', 'url': 'asset_url', 'timestamp': 'seconds'}[change]] = 'untrusted'
    elif change == 'dangling_beat':
        value['actions'][0]['beat_id'] = 'beat_ffffffffffffffff'
    elif change == 'dangling_target':
        value['actions'][0]['target'] = 'unknown'
    elif change == 'dangling_relation':
        value['relations'][0]['source'] = 'unknown'
    elif change == 'duplicate_id':
        value['relations'][0]['id'] = 'input'
    elif change == 'duplicate_slot':
        value['elements'][1]['slot'] = 'source'
    elif change == 'wrong_kind_slot':
        value['elements'][0]['slot'] = 'token_source'
    elif change == 'move_card':
        value['actions'][0]['target'] = 'input'
    elif change == 'draw_card':
        value['actions'][1]['target'] = 'input'
    elif change == 'reveal_relation':
        value['actions'][1]['kind'] = 'reveal'
    elif change == 'move_unknown_slot':
        value['actions'][0]['to_slot'] = 'token_right'
    elif change == 'extra_to_slot':
        value['actions'][1]['to_slot'] = 'token_queue'
    elif change == 'conflict':
        value['actions'].append(deepcopy(value['actions'][0]))
    elif change == 'empty_actions':
        value['actions'] = []
    elif change == 'many_actions':
        value['actions'] *= 25
    elif change == 'long_text':
        value['elements'][0]['label'] = '文' * 29
    elif change == 'boolean_slot':
        value['elements'][0]['slot'] = True
    elif change == 'bad_template':
        value['template'] = ['flow']
    else:
        value['elements'][0]['id'] = 'x\" onclick=evil'
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(value, beats())


@pytest.mark.parametrize('change', ['span', 'text', 'duplicate', 'bad_id', 'boolean', 'unknown', 'many',
                                  'gap', 'tail_gap'])
def test_beats_bind_source_and_actual_spoken_text(change):
    value = beats()
    if change == 'span':
        value['segments'][0]['spoken_end'] += 1
    elif change == 'text':
        value['segments'][0]['spoken_text'] = 'changed'
    elif change == 'duplicate':
        value['segments'] *= 2
    elif change == 'bad_id':
        value['segments'][0]['id'] = ['invalid']
    elif change == 'boolean':
        value['segments'][0]['source_start'] = False
    elif change == 'unknown':
        value['segments'][0]['seconds'] = 1
    elif change == 'gap':
        value['segments'][0]['source_start'] = 1
        value['segments'][0]['source_text'] = value['source_text'][1:]
    elif change == 'tail_gap':
        value['segments'][0]['spoken_end'] -= 1
        value['segments'][0]['spoken_text'] = value['spoken_text'][:-1]
    else:
        value['segments'] *= 25
    with pytest.raises(ServiceError, match='invalid_motion_beats'):
        validate_beats(value)


def test_instruction_tamper_is_detected(tmp_path, monkeypatch):
    for name in ('manifest.json', *motion.BUNDLE_FILES):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((motion.BUNDLE_ROOT / name).read_bytes())
    monkeypatch.setattr(motion, 'BUNDLE_ROOT', tmp_path)
    assert motion.verified_bundle()
    (tmp_path / 'PLAN.schema.json').write_text('{}')
    with pytest.raises(ServiceError, match='motion_bundle_integrity'):
        motion.verified_bundle()


def test_catalog_motion_is_explicit_disabled_and_uses_separate_resource(image_packages):
    old, _, _ = catalog.generate(pi_image='registry.invalid/pi@sha256:' + 'a' * 64,
                                runtime_revision='test')
    new, hosts, summary = catalog.generate(pi_image='registry.invalid/pi@sha256:' + 'a' * 64,
                                          runtime_revision='test', include_motion=True)
    assert not any(c['name'] == 'video.motion-plan.v1' for c in old['task_contracts'])
    contract = next(c for c in new['task_contracts'] if c['name'] == 'video.motion-plan.v1')
    assert contract['revision'] == '1'
    binding = next(c for c in new['execution_bindings'] if c['profile'] == contract['name'])
    assert binding['bundle'] == dict(name='video-motion', revision='video-motion-v1')
    assert 'acceptance' not in binding
    route = next(c for c in new['routes'] if c['profile'] == contract['name'])
    assert route['enabled'] is False
    assert all(not h['profile']['enabled'] for h in hosts.values())
    assert next(c for c in new['callers'] if c['caller'] == 'video-workbench')['profiles'] == [
        *catalog.VIDEO_PROFILES, *catalog.MOTION_PROFILES]
    assert summary['routes_enabled'] is summary['acceptance_created'] is False
    assert old['capability_bundles'] == new['capability_bundles'][:3]
    assert old['task_contracts'] == new['task_contracts'][:-1]
