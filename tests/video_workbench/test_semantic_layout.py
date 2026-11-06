"""Nov 6 feedback: renderable text, ordered flow, containment, full sources."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.video_motion_contract import validate_motion_plan
from video_workbench.motion import (compile_project, compiler_layout, estimated_alignment,
                                    example_plan, make_beats, preflight_plan, resolve_timeline)
from video_workbench.motion_geometry import overlaps, segment_hits_box


URL = ('https://github.com/deepseek-ai/deepseek-harness/blob/'
       '8bd073392eef6f54e921a093b95076248eb08cbb/docs/architecture.md')


def fixture(template='flow'):
    beats = make_beats('用户提出问题。模型请求读取文件。工具返回结果。')
    return example_plan(beats, template=template), beats


def test_focus_card_grows_for_full_label_and_detail_without_changing_text():
    plan, beats = fixture('focus')
    node = next(r for r in plan['elements'] if r['slot'] == 'center')
    node.update(label='DeepSeek Harness（DSH）', detail='基于插件的 Agent 框架')
    before = deepcopy(plan)
    old = compiler_layout(plan, 'motion-compiler-5')['center']
    checked = preflight_plan(plan, beats)
    assert checked['boxes'][node['id']][3] > old[3]
    assert plan == before
    files = compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 20))
    assert json.loads(files['plan.json']) == before


def test_wide_relationship_plate_fits_below_compare_cards_without_truncation():
    plan, beats = fixture('compare')
    plan['relations'][0]['label'] = '按规则投影：选取并整理'
    before = deepcopy(plan)
    checked = preflight_plan(plan, beats)
    relationship = checked['geometry'][plan['relations'][0]['id']]
    assert relationship['lines'] == ['按规则投影：选取并整理']
    assert relationship['box'][2] > 180
    assert plan == before


def test_sequence_is_left_to_right_and_return_has_a_separate_lane():
    plan, beats = fixture('sequence')
    plan['elements'] = [r for r in plan['elements'] if r['kind'] != 'token']
    plan['relations'] = [dict(id='request', source='node1', target='node2', label='请求读取'),
                         dict(id='return', source='node2', target='node1', label='工具结果')]
    plan['actions'] = [dict(beat_id=beats['segments'][0]['id'], target='request', kind='draw',
                            placement='start', pace='normal', to_slot=None),
                       dict(beat_id=beats['segments'][1]['id'], target='return', kind='draw',
                            placement='start', pace='normal', to_slot=None)]
    checked = preflight_plan(plan, beats)
    positions = [checked['boxes']['node' + str(i)][0] for i in range(4)]
    assert positions == sorted(positions)
    assert checked['geometry']['request']['box'][1] < 332
    assert checked['geometry']['return']['box'][1] > 480
    assert checked['geometry']['request']['path'] != checked['geometry']['return']['path']
    for relation in plan['relations']:
        own = checked['geometry'][relation['id']]
        assert len(own['lines']) <= 2
        for box in checked['boxes'].values():
            assert not overlaps(own['box'], box)


def test_nested_has_real_parent_frames_and_wires_avoid_headers():
    plan, beats = fixture('nested')
    checked = preflight_plan(plan, beats)
    outer, inner = checked['boxes']['outer'], checked['boxes']['inner']
    assert outer[0] < inner[0] and outer[1] < inner[1]
    assert inner[0] + inner[2] < outer[0] + outer[2]
    for identity in ('request', 'tool'):
        x, y, width, height = checked['boxes'][identity]
        assert inner[0] < x < x + width < inner[0] + inner[2]
        assert inner[1] < y < y + height < inner[1] + inner[3]
    for item in checked['geometry'].values():
        for parent in ('outer', 'inner'):
            assert not overlaps(item['box'], checked['obstacles'][parent])
            assert not any(segment_hits_box(a, b, checked['obstacles'][parent])
                           for a, b in zip(item['points'], item['points'][1:]))
    page = compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 20))['index.html'].decode()
    assert 'class="element container slot-outer"' in page
    assert 'class="element container slot-inner"' in page
    assert page.index('id="el-outer"') < page.index('id="el-request"')


@pytest.mark.parametrize('change', [
    lambda p: p['elements'].pop(0),
    lambda p: p['actions'].append(dict(beat_id=p['actions'][0]['beat_id'], target='outer',
                                      kind='hide', placement='start', pace='normal', to_slot=None)),
    lambda p: p['relations'].append(dict(id='fake_containment', source='outer', target='request', label='包含')),
])
def test_nested_rejects_missing_frames_and_misleading_containment(change):
    plan, beats = fixture('nested')
    change(plan)
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(plan, beats)


def test_full_source_url_is_visible_text_and_requires_five_seconds():
    plan, beats = fixture()
    plan['sources'] = [dict(url=URL, label='官方固定版本 8bd0733 · 核对2026-11-04')]
    alignment = estimated_alignment(beats, 'a' * 64, 20)
    checked = preflight_plan(plan, beats)
    files = compile_project(plan, beats, alignment)
    page = files['index.html'].decode()
    assert URL in page and '<div class="source-url">' + URL + '</div>' in page
    assert '<a ' not in page and 'fetch(' not in page
    assert json.loads(files['plan.json']) == plan
    assert checked['boxes']['node0'][1] + checked['boxes']['node0'][3] < 568
    with pytest.raises(ServiceError, match='motion_source_reading_time_too_short'):
        resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 3))
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 3), trailing_seconds=2)
    assert timeline['trailing_seconds'] == 2 and timeline['duration_seconds'] == 5


def test_focus_preserves_original_note_caption_two_urls_and_separate_tokens():
    plan, beats = fixture('focus')
    plan['takeaway'] = '在这个问答例子中，模型按需继续处理，直到没有尚待处理的工作并形成回答。'
    plan['elements'] += [
        dict(id='source_note', kind='card', slot='note', label='DeepSeek Harness 官方资料',
             detail='官方仓库与 docs/architecture.md\n依据官方文档固定版本8bd0733；核对2026-11-04'),
        dict(id='caption', kind='label', slot='caption', label='在这个问答例子中', detail=''),
        dict(id='records', kind='token', slot='token_center', label='两类记录', detail='联系运行过程'),
        dict(id='scope', kind='token', slot='token_right', label='架构概念介绍', detail='非性能测评'),
    ]
    plan['relations'] = [
        dict(id='question', source='node0', target='node1', label='问题'),
        dict(id='choose', source='node1', target='node2', label='选择行动'),
        dict(id='again', source='node2', target='node1', label='需要时继续处理'),
        dict(id='answer', source='node1', target='node3', label='没有尚待处理的工作'),
        dict(id='history', source='node1', target='records', label='运行过程联系记录'),
    ]
    plan['actions'] = [dict(beat_id=beats['segments'][i % 3]['id'], target=r['id'], kind='draw',
                            placement='start', pace='normal', to_slot=None)
                       for i, r in enumerate(plan['relations'])]
    label = '输入提供的参考链接；固定版本8bd0733；核对2026-11-04'
    plan['sources'] = [dict(url='https://github.com/deepseek-ai/deepseek-harness', label=label),
                       dict(url='https://github.com/deepseek-ai/deepseek-harness/blob/8bd0733/docs/architecture.md', label=label)]
    before = deepcopy(plan)
    checked = preflight_plan(plan, beats)
    boxes = list(checked['boxes'].values())
    for index, box in enumerate(boxes):
        assert not any(overlaps(box, other) for other in boxes[index + 1:])
    files = compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 30))
    page = files['index.html'].decode()
    assert '<div class="source-label">' + label + '</div>' in page
    assert page.split('<script>')[0].count('<div class="source-label">') == 1
    assert 'class="element label slot-caption"' in page
    for source in plan['sources']:
        assert '<div class="source-url">' + source['url'] + '</div>' in page
    assert plan == before and json.loads(files['plan.json']) == before


@pytest.mark.parametrize('template', ['flow', 'focus', 'sequence', 'nested'])
def test_two_short_sources_preserve_distinct_labels_in_the_footer(template):
    plan, beats = fixture(template)
    plan['takeaway'] = '模型读取文件后继续处理。'
    plan['sources'] = [
        dict(label='官方仓库', url='https://github.com/deepseek-ai/deepseek-harness'),
        dict(label='架构文档', url='https://github.com/deepseek-ai/deepseek-harness/blob/8bd0733/docs/architecture.md'),
    ]
    original = deepcopy(plan)
    preflight_plan(plan, beats)
    files = compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 20))
    page = files['index.html'].decode()
    assert page.split('<script>')[0].count('<div class="source-label">') == 2
    for source in plan['sources']:
        assert '<div class="source-label">' + source['label'] + '</div>' in page
        assert '<div class="source-url">' + source['url'] + '</div>' in page
    assert '.source-row:last-child{margin-bottom:0}' in page
    assert plan == original and json.loads(files['plan.json']) == original


@pytest.mark.parametrize('compiler', [f'motion-compiler-{i}' for i in range(1, 6)])
@pytest.mark.parametrize('feature', ['sources', 'nested', 'sequence'])
def test_old_compilers_reject_new_semantics_instead_of_dropping_them(compiler, feature):
    plan, beats = fixture(feature if feature != 'sources' else 'flow')
    if feature == 'sources':
        plan['sources'] = [dict(url=URL, label='官方资料')]
    with pytest.raises(ServiceError, match='motion_compiler_version_unsupported'):
        compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 20), compiler_version=compiler)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://', 'https://user:pass@example.org/a',
                                'https://example.org/a b', 'https://[bad'])
def test_source_url_is_bounded_inert_http_text(url):
    plan, beats = fixture()
    plan['sources'] = [dict(url=url, label='来源')]
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        validate_motion_plan(plan, beats)


def test_overfull_source_panel_is_rejected_without_shortening_any_url():
    plan, beats = fixture()
    plan['sources'] = [dict(url='https://example.org/' + 'a' * 480, label='完整来源')]
    original = deepcopy(plan)
    with pytest.raises(ServiceError, match='motion_text_overflow'):
        preflight_plan(plan, beats)
    assert plan == original


@pytest.mark.parametrize('change', [
    lambda source: None,
    lambda source: dict(source, urls=['https://example.org/wrong']),
    lambda source: dict(source, opacity=0),
    lambda source: dict(source, overflow=True),
])
def test_review_rejects_missing_hidden_or_shortened_source_panel(change):
    from video_workbench.motion_review import _layout
    box = dict(x=72, y=604, width=1136, height=83, opacity=1, overflow=False, urls=[URL])
    observation = dict(elements={}, relations={}, layout=dict(fontReady=True, relations=[],
        title=dict(x=72, y=74, width=1136, height=58, overflow=False),
        takeaway=dict(x=72, y=568, width=1136, height=32, overflow=False), sources=box))
    _layout(observation, source_urls=[URL])
    observation['layout']['sources'] = change(box)
    with pytest.raises(ServiceError):
        _layout(observation, source_urls=[URL])
