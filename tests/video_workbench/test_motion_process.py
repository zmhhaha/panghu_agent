"""Independent process contracts, real state changes and frozen compiler bounds."""
from copy import deepcopy
import json
import math
import re

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.video_motion_contract import validate_motion_plan
from agent_service.profiles.video_process_contract import validate_process_plan
from video_workbench.motion import (
    compile_project, estimated_alignment, make_beats, manual_alignment,
    resolve_timeline, sha256,
)
from video_workbench.motion_demo import compile_demo_project, resolve_demo_timeline
from video_workbench.motion_process import (
    PROCESS_COMPILER_VERSION, PROCESS_LAYOUT, PROCESS_TEMPLATE_VERSION,
    STATE_PRESENTATIONS, compile_process_project, process_paths,
    resolve_process_timeline,
)


def fixture():
    beats = make_beats('提交请求。分发任务。执行失败。返回队列。再次执行。执行完成。')
    ids = [row['id'] for row in beats['segments']]

    def action(beat, target, kind, placement='start', **options):
        return dict(beat_id=ids[beat], target=target, kind=kind, placement=placement,
                    pace='normal', **options)

    plan = dict(schema='video.process-plan.v1', template='flow', title='同一个请求失败后重试',
        takeaway='处理状态改变，请求身份保留。', elements=[
            dict(id='source', kind='card', slot='source', label='提交端', detail='只创建一次'),
            dict(id='queue', kind='card', slot='queue', label='队列', detail='保存同一个请求'),
            dict(id='worker', kind='card', slot='worker', label='执行节点', detail='失败后可以重试'),
            dict(id='request', kind='token', slot='token_source', label='请求01', detail=''),
        ], relations=[
            dict(id='enter', source='source', target='queue', label='进入队列'),
            dict(id='dispatch', source='queue', target='worker', label='交给执行节点'),
            dict(id='return', source='worker', target='queue', label='失败后返回'),
        ], actions=[
            action(0, 'request', 'reveal'), action(0, 'enter', 'draw'),
            action(0, 'request', 'transfer', 'middle', relation_id='enter'),
            action(0, 'queue', 'set_state', 'end', state='waiting'),
            action(1, 'dispatch', 'draw'),
            action(1, 'request', 'transfer', 'middle', relation_id='dispatch'),
            action(1, 'worker', 'set_state', 'end', state='running'),
            action(2, 'worker', 'set_state', state='failed'), action(2, 'return', 'draw'),
            action(2, 'request', 'transfer', 'middle', relation_id='return'),
            action(3, 'request', 'transfer', relation_id='dispatch'),
            action(3, 'worker', 'set_state', 'middle', state='running'),
            action(4, 'worker', 'set_state', state='completed'),
        ])
    return plan, beats, estimated_alignment(beats, 'a' * 64, 24)


def test_failure_return_and_retry_keep_one_identity_and_real_state_changes():
    plan, beats, alignment = fixture()
    timeline = resolve_process_timeline(plan, beats, alignment)
    transfers = [row for row in timeline['actions'] if row['kind'] == 'transfer']
    assert len({row['id'] for row in plan['elements'] if row['kind'] == 'token'}) == 1
    assert {row['target'] for row in transfers} == {'request'}
    assert [(row['from_slot'], row['to_slot']) for row in transfers] == [
        ('token_source', 'token_queue'), ('token_queue', 'token_worker'),
        ('token_worker', 'token_queue'), ('token_queue', 'token_worker')]
    assert [(row['previous_state'], row['state']) for row in timeline['actions']
            if row['kind'] == 'set_state' and row['target'] == 'worker'] == [
        ('idle', 'running'), ('running', 'failed'), ('failed', 'running'),
        ('running', 'completed')]
    assert timeline['initial_visibility']['request'] == 0
    assert set(timeline['initial_states'].values()) == {'idle'}
    assert timeline['alignment_method'] == 'estimated' and timeline['review_status'] == 'pending'


def test_paths_use_exact_token_centers_and_clear_complete_swept_boxes():
    plan, _, _ = fixture()
    paths = process_paths(plan)
    labels = [path['label_box'] for path in paths.values()]
    cards = [PROCESS_LAYOUT[key] for key in ('source', 'queue', 'worker')]
    width, height = PROCESS_LAYOUT['token_source'][2:]
    for path in paths.values():
        for index, slot in ((0, path['source_slot']), (-1, path['target_slot'])):
            x, y, w, h = PROCESS_LAYOUT['token_' + slot]
            assert path['points'][index] == [x + w / 2, y + h / 2]
        assert path['length'] == sum(math.dist(a, b) for a, b in zip(path['points'], path['points'][1:]))
        for before, after in zip(path['points'], path['points'][1:]):
            left = min(before[0], after[0]) - width / 2 - 8
            top = min(before[1], after[1]) - height / 2 - 8
            right = max(before[0], after[0]) + width / 2 + 8
            bottom = max(before[1], after[1]) + height / 2 + 8
            assert 0 <= left < right <= 1280 and 0 <= top < bottom <= 720
            for x, y, w, h in cards + labels:
                assert right <= x or x + w <= left or bottom <= y or y + h <= top
    assert paths['return']['source_slot'] == 'worker' and paths['return']['target_slot'] == 'queue'
    assert paths['return']['points'] != list(reversed(paths['dispatch']['points']))


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(schema='video.motion-plan.v1'),
    lambda p: p.update(template='demonstration'),
    lambda p: p.update(script='alert(1)'),
    lambda p: p['elements'][0].update(x=2),
    lambda p: p['elements'][0].update(css='display:none'),
    lambda p: p['elements'][0].update(slot='token_source'),
    lambda p: p['elements'][1].update(slot='source'),
    lambda p: p['elements'][3].update(detail='another record'),
    lambda p: p['elements'][3].update(kind='card'),
    lambda p: p['elements'][0].update(label='a' * 19),
    lambda p: p['elements'][0].update(label='bad\x00text'),
    lambda p: p['relations'][0].update(target='worker'),
    lambda p: p['relations'][0].update(source='request'),
    lambda p: p['relations'][0].update(path='M0 0 L1 1'),
    lambda p: p['actions'][0].update(start=2),
    lambda p: p['actions'][0].update(kind='execute'),
    lambda p: p['actions'][2].update(target='source'),
    lambda p: p['actions'][2].update(relation_id='unlisted'),
    lambda p: p['actions'][3].update(target='request'),
    lambda p: p['actions'][3].update(state='queued'),
    lambda p: p['actions'][3].update(beat_id='beat_0000000000000000'),
])
def test_contract_rejects_unbounded_data_and_unsupported_states(mutate):
    plan, beats, alignment = fixture()
    mutate(plan)
    with pytest.raises(ServiceError):
        compile_process_project(plan, beats, alignment)


def test_plan_validation_is_strict_for_bare_beats_and_always_returns_a_copy():
    plan, beats, _ = fixture()
    bare = [dict(id=row['id']) for row in beats['segments']]
    assert validate_process_plan(plan, bare) == plan
    validated = validate_process_plan(plan, beats)
    validated['elements'][0]['label'] = 'changed'
    assert plan['elements'][0]['label'] == '提交端'
    for invalid in (None, [], [dict(id='anything')], [bare[0], bare[0]]):
        with pytest.raises(ServiceError):
            validate_process_plan(plan, invalid)


def test_without_transfer_and_state_change_this_cannot_be_a_process_plan():
    plan, beats, _ = fixture()
    for missing in ('transfer', 'set_state'):
        changed = deepcopy(plan)
        changed['actions'] = [a for a in changed['actions'] if a['kind'] != missing]
        with pytest.raises(ServiceError, match='process_changes_required'):
            validate_process_plan(changed, beats)


@pytest.mark.parametrize(('mutate', 'code'), [
    (lambda p: p['actions'].pop(1), 'process_path_not_drawn'),
    (lambda p: p['actions'].insert(2, dict(p['actions'][1], placement='middle')),
     'process_relation_already_drawn'),
    (lambda p: p['actions'][6].update(state='failed'), 'process_state_transition_invalid'),
    (lambda p: p['actions'][11].update(state='waiting'), 'process_state_transition_invalid'),
    (lambda p: p['actions'].append(dict(p['actions'][12], beat_id=p['actions'][12]['beat_id'],
                                      placement='end', state='running')),
     'process_state_transition_invalid'),
    (lambda p: p['actions'].append(dict(p['actions'][0], kind='hide', placement='middle')),
     'process_element_not_visible'),
    (lambda p: p['actions'].append(dict(p['actions'][0], target='queue', kind='hide', placement='middle')),
     'process_element_not_visible'),
    (lambda p: p['actions'].pop(0), 'process_element_not_visible'),
    (lambda p: p['actions'].append(
        {k: v for k, v in dict(p['actions'][6], kind='hide').items() if k != 'state'}),
     'process_element_not_visible'),
])
def test_draw_direction_state_and_entire_visibility_window_are_enforced(mutate, code):
    plan, beats, alignment = fixture()
    mutate(plan)
    with pytest.raises(ServiceError, match=code):
        resolve_process_timeline(plan, beats, alignment)


def test_transfer_cannot_teleport_to_a_different_route_source():
    plan, beats, alignment = fixture()
    plan['actions'][1]['target'] = 'dispatch'
    plan['actions'][2]['relation_id'] = 'dispatch'
    with pytest.raises(ServiceError, match='process_token_source_mismatch'):
        resolve_process_timeline(plan, beats, alignment)


def test_duplicate_visibility_actions_are_rejected_even_with_opposite_verbs():
    plan, beats, _ = fixture()
    plan['actions'].append(dict(plan['actions'][0], kind='hide'))
    with pytest.raises(ServiceError, match='conflicting_process_actions'):
        validate_process_plan(plan, beats)


@pytest.mark.parametrize('kind', ['reveal', 'hide'])
def test_visibility_must_alternate_in_beat_order_even_if_action_list_is_reordered(kind):
    plan, beats, _ = fixture()
    if kind == 'hide':
        plan['actions'].append(dict(plan['actions'][0], kind='hide', beat_id=beats['segments'][4]['id']))
    # Put the latest action first to ensure data-list ordering cannot bypass
    # the independently established semantic beat chronology.
    plan['actions'].insert(0, dict(plan['actions'][0], kind=kind, beat_id=beats['segments'][5]['id']))
    with pytest.raises(ServiceError, match='process_visibility_change_required'):
        validate_process_plan(plan, beats)


def test_token_cannot_be_initially_visible_by_hiding_it_first():
    plan, beats, _ = fixture()
    plan['actions'][0]['kind'] = 'hide'
    with pytest.raises(ServiceError, match='process_token_initial_visibility_invalid'):
        validate_process_plan(plan, beats)


def test_new_audio_retimes_the_process_without_changing_semantics():
    plan, beats, first = fixture()
    second = estimated_alignment(beats, 'b' * 64, 30)
    one, two = (resolve_process_timeline(plan, beats, a) for a in (first, second))
    assert one['plan_sha256'] == two['plan_sha256']
    assert one['semantic_sha256'] == two['semantic_sha256']
    assert one['audio_sha256'] != two['audio_sha256']
    assert one['alignment_sha256'] != two['alignment_sha256']
    assert two['actions'][-1]['start'] > one['actions'][-1]['start']
    first['spoken_sha256'] = 'f' * 64
    with pytest.raises(ServiceError, match='invalid_motion_alignment'):
        resolve_process_timeline(plan, beats, first)


def test_manual_review_is_preserved_and_never_inferred_from_a_render():
    plan, beats, estimated = fixture()
    reviewed = manual_alignment(beats, 'a' * 64, 24, estimated['segments'],
        previous_alignment=estimated, reviewed_by='test-operator',
        reviewed_at='2026-11-05T12:00:00+08:00')
    timeline = resolve_process_timeline(plan, beats, reviewed)
    assert timeline['alignment_method'] == 'manual_anchors'
    assert timeline['review_status'] == 'verified'
    assert json.loads(compile_process_project(plan, beats, reviewed)['alignment.json']) == reviewed


def test_gaps_padding_and_frame_rounding_preserve_the_complete_recording():
    plan, beats, alignment = fixture()
    alignment['audio_seconds'] = 25.013
    for segment in alignment['segments']:
        segment['start'] += .2
        segment['end'] -= .2
    timeline = resolve_process_timeline(plan, beats, alignment, leading_seconds=1, trailing_seconds=2)
    assert timeline['duration_seconds'] == math.ceil(28.013 * 24) / 24
    assert timeline['audio_seconds'] == 25.013
    assert timeline['beats'][0]['start'] == 1.2
    assert timeline['beats'][-1]['end'] == 24.8
    assert timeline['audio_sha256'] == alignment['audio_sha256']


def test_tiny_beat_is_rejected_without_lengthening_the_recording():
    plan, beats, alignment = fixture()
    alignment['segments'][0]['end'] = .2
    with pytest.raises(ServiceError, match='process_beat_too_short'):
        resolve_process_timeline(plan, beats, alignment)


def test_final_action_must_finish_before_actual_last_frame_or_use_explicit_padding():
    plan, beats, alignment = fixture()
    alignment['segments'][-1]['start'] = 23.65
    plan['actions'].append(dict(plan['actions'][0], kind='hide', placement='end',
                                beat_id=beats['segments'][-1]['id']))
    with pytest.raises(ServiceError, match='process_final_hold_required'):
        resolve_process_timeline(plan, beats, alignment)
    padded = resolve_process_timeline(plan, beats, alignment, trailing_seconds=1 / 24)
    assert padded['audio_seconds'] == 24
    assert all(row['start'] + row['duration'] <= padded['duration_seconds'] - 1 / 24
               for row in padded['actions'])


def test_six_files_are_deterministic_digest_bound_and_leave_inputs_untouched():
    args = fixture()
    before = deepcopy(args)
    fingerprint = {'runtime': 'isolated-test'}
    files = compile_process_project(*args, runtime_fingerprint=fingerprint)
    assert files == compile_process_project(*args, runtime_fingerprint=fingerprint)
    assert args == before
    assert set(files) == {'index.html', 'plan.json', 'beats.json', 'alignment.json',
                          'timeline.json', 'manifest.json'}
    manifest, timeline = [json.loads(files[name]) for name in ('manifest.json', 'timeline.json')]
    assert manifest['compiler'] == timeline['compiler'] == PROCESS_COMPILER_VERSION
    assert manifest['template'] == PROCESS_TEMPLATE_VERSION
    assert manifest['runtime_fingerprint'] == fingerprint
    assert manifest['plan_sha256'] == timeline['plan_sha256'] == sha256(args[0])
    assert manifest['timeline_sha256'] == sha256(files['timeline.json'])
    for name, digest in manifest['files'].items():
        assert sha256(files[name]) == digest
    static = compile_process_project(*args, animated=False, runtime_fingerprint=fingerprint)
    assert static['timeline.json'] == files['timeline.json']
    assert static['index.html'] != files['index.html']


def test_escaped_text_local_assets_and_seekable_scene_have_no_executable_input():
    plan, beats, alignment = fixture()
    plan['elements'][0].update(label='<script>', detail='</script><img src=x>')
    page = compile_process_project(plan, beats, alignment)['index.html'].decode()
    assert '&lt;script&gt;' in page and '&lt;/script&gt;&lt;img src=x&gt;' in page
    assert '\\u003c/script\\u003e\\u003cimg' in page
    assert page.count('</script>') == 2
    assert not any(text in page for text in ('https://', 'setInterval(', 'Math.random', 'onUpdate:', 'MotionPath'))
    assert "src:url('assets/NotoSansCJK-Regular.ttc')" in page
    assert 'data-composition-id="video-motion"' in page
    assert "window.__timelines['video-motion']=tl" in page
    assert 'relation-base' not in page and 'opacity="0"' in page
    assert 'stroke-dashoffset:1;opacity:0' in page
    assert 'stateOpacities:' in page and "text_boxes['state-symbol']" in page
    assert 'overflow:false' not in page
    css = re.search(r'<style>(.*?)</style>', page, re.DOTALL).group(1)
    assert not re.search(r'(?:^|[;{])\s*transform\s*:', css)
    for visual in STATE_PRESENTATIONS.values():
        assert f'>{visual["label"]}</span>' in page
        assert f'data-symbol="{visual["symbol"]}"' in page
        assert f'd="{visual["path"]}"' in page


def test_new_contract_cannot_enter_legacy_motion_or_demo_compilers():
    args = fixture()
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(*args[:2])
    for call in (compile_project, resolve_timeline, compile_demo_project, resolve_demo_timeline):
        with pytest.raises(ServiceError, match='invalid_motion_plan'):
            call(*args)
