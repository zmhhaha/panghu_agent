"""Dependency boundaries, exact text bindings and executable-data separation."""
from copy import deepcopy
import json
import math
import re

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion import (
    LAYOUTS, action_windows, canonical, compile_project, estimated_alignment,
    example_plan, make_beats, manual_alignment, resolve_timeline, semantic_digest,
    sha256, text_sha256, validate_alignment,
)


def fixture():
    beats = make_beats('请求到达。进入队列。执行节点开始工作。')
    plan = example_plan(beats)
    alignment = estimated_alignment(beats, 'a' * 64, 15)
    return plan, beats, alignment


def test_beats_preserve_every_character_including_whitespace():
    text = '  SDK 接收请求。\n随后继续。  '
    beats = make_beats(text)
    assert ''.join(row['source_text'] for row in beats['segments']) == text
    assert ''.join(row['spoken_text'] for row in beats['segments']) == text
    assert beats['segments'][0]['source_start'] == 0
    assert beats['segments'][-1]['source_end'] == len(text)


def test_pronunciation_changes_spoken_mapping_but_not_semantic_identity():
    original = make_beats('SDK 接收请求。最后返回。')
    corrected = make_beats('SDK 接收请求。最后返回。', 'S D K 接收请求。最后返回。')
    assert [r['id'] for r in original['segments']] == [r['id'] for r in corrected['segments']]
    assert semantic_digest(original) == semantic_digest(corrected)
    assert text_sha256(original['spoken_text']) != text_sha256(corrected['spoken_text'])
    with pytest.raises(ServiceError, match='motion_beats_need_explicit_mapping'):
        make_beats('一段。', '拆成两段。另一段。')


def test_duplicate_sentence_ids_are_unique_and_stable():
    first = make_beats('继续。继续。下一步。')
    second = make_beats('继续。继续。下一步。')
    assert len({r['id'] for r in first['segments']}) == 3
    assert first == second


def test_estimates_are_always_pending_and_bound_to_spoken_text():
    _, beats, alignment = fixture()
    assert alignment['method'] == 'estimated'
    assert alignment['review_status'] == 'pending'
    assert alignment['reviewed_by'] is alignment['reviewed_at'] is None
    assert alignment['segments'][0]['start'] == 0
    assert alignment['segments'][-1]['end'] == 15
    changed = make_beats(beats['source_text'], beats['spoken_text'].replace('请求', '任务'))
    with pytest.raises(ServiceError, match='invalid_motion_alignment'):
        validate_alignment(alignment, changed)


@pytest.mark.parametrize('mutation', [
    lambda a: a.update(audio_sha256='no'),
    lambda a: a.update(audio_seconds=float('nan')),
    lambda a: a.update(audio_seconds=10 ** 1000),
    lambda a: a.update(method=[]),
    lambda a: a.update(review_status={}),
    lambda a: a.update(review_status='verified'),
    lambda a: a.update(reviewed_by='operator'),
    lambda a: a['segments'][1].update(start=-1),
    lambda a: a['segments'][1].update(end=16),
    lambda a: a['segments'][1].update(beat_id='beat_' + '0' * 16),
    lambda a: a['segments'][1].update(start=a['segments'][0]['start']),
    lambda a: a['segments'][0].update(end=.01),
    lambda a: a.update(extra='unbounded'),
])
def test_invalid_stale_or_forged_alignment_is_rejected(mutation):
    _, beats, alignment = fixture()
    mutation(alignment)
    with pytest.raises(ServiceError):
        validate_alignment(alignment, beats)


def test_manual_correction_keeps_prior_digest_and_changed_anchors():
    _, beats, before = fixture()
    anchors = deepcopy(before['segments'])
    anchors[0]['end'] += .2
    anchors[1]['start'] += .2
    reviewed = manual_alignment(beats, 'a' * 64, 15, anchors,
        previous_alignment=before, reviewed_by='test-reviewer', reviewed_at='2026-11-02T12:00:00+08:00',
        correction_reason='Checked the sentence boundary against the recording')
    assert reviewed['method'] == 'manual_anchors'
    assert reviewed['review_status'] == 'verified'
    assert reviewed['provenance'] == dict(method='estimated', version=before['version'], alignment_sha256=sha256(before))
    assert len(reviewed['corrections']) == 2
    assert reviewed['corrections'][0]['previous_end'] == before['segments'][0]['end']
    assert validate_alignment(reviewed, beats) == reviewed
    reviewed['corrections'][0]['end'] += .2
    with pytest.raises(ServiceError):
        validate_alignment(reviewed, beats)


def test_manual_review_does_not_validate_another_recording():
    _, beats, alignment = fixture()
    with pytest.raises(ServiceError):
        manual_alignment(beats, 'b' * 64, 15, alignment['segments'], previous_alignment=alignment)
    with pytest.raises(ServiceError):
        manual_alignment(beats, 'a' * 64, 15, alignment['segments'],
                         reviewed_by='operator', reviewed_at='2026-11-02T12:00:00')


def test_voice_change_reuses_visual_plan_but_retimes_actions():
    plan, beats, first = fixture()
    second = estimated_alignment(beats, 'b' * 64, 22)
    one, two = resolve_timeline(plan, beats, first), resolve_timeline(plan, beats, second)
    assert one['plan_sha256'] == two['plan_sha256']
    assert one['semantic_sha256'] == two['semantic_sha256']
    assert one['alignment_sha256'] != two['alignment_sha256']
    assert one['audio_sha256'] != two['audio_sha256']
    assert one['actions'][-1]['start'] < two['actions'][-1]['start']


def test_layout_change_reuses_recording_and_alignment():
    plan, beats, alignment = fixture()
    revised = deepcopy(plan)
    revised['title'] = '换个标题，声音保持不变'
    one, two = resolve_timeline(plan, beats, alignment), resolve_timeline(revised, beats, alignment)
    assert one['audio_sha256'] == two['audio_sha256']
    assert one['alignment_sha256'] == two['alignment_sha256']
    assert one['plan_sha256'] != two['plan_sha256']


def test_padding_preserves_whole_audio_and_rounds_only_up_to_frame():
    plan, beats, alignment = fixture()
    alignment['audio_seconds'] = 15.013
    timeline = resolve_timeline(plan, beats, alignment, leading_seconds=3, trailing_seconds=3)
    assert timeline['duration_seconds'] == math.ceil(21.013 * 24) / 24
    assert timeline['audio_seconds'] == 15.013
    assert timeline['beats'][0]['start'] == 3
    assert timeline['actions'][0]['start'] >= 3
    assert timeline['review_status'] == 'pending'
    assert timeline['duration_seconds'] >= 3 + 15.013 + 3


def test_all_three_templates_compile_and_own_the_root_timeline():
    _, beats, alignment = fixture()
    for template in LAYOUTS:
        files = compile_project(example_plan(beats, template=template), beats, alignment)
        page = files['index.html'].decode()
        manifest = json.loads(files['manifest.json'])
        assert 'data-composition-id="video-motion"' in page
        assert "window.__timelines['video-motion']=tl" in page
        assert 'assets/gsap.min.js' in page
        assert 'https://' not in page
        assert 'setInterval(' not in page and 'Math.random' not in page
        assert manifest['width'] == 1280 and manifest['height'] == 720 and manifest['fps'] == 24
        assert manifest['files']['index.html'] == sha256(files['index.html'])


def test_relation_layout_observations_identify_every_relation_and_check_vertical_bounds():
    args = fixture()
    page = compile_project(*args)['index.html'].decode()
    for relation in args[0]['relations']:
        assert f'data-motion-relation="{relation["id"]}"' in page
    assert 'id:el.dataset.motionRelation' in page
    assert 'el.getBBox().y+el.getBBox().height>720' in page


def test_font_observation_checks_target_font_in_addition_to_finished_requests():
    page = compile_project(*fixture())['index.html'].decode()
    assert "fontReady:document.fonts.status==='loaded'&&document.fonts.check('20px VideoNoto')" in page


def test_compiled_fonts_are_local_declared_faces_or_portable_generics():
    page = compile_project(*fixture())['index.html'].decode()
    css = re.search(r'<style>(.*?)</style>', page, re.DOTALL).group(1)
    declared = set()
    for face in re.findall(r'@font-face\s*\{(.*?)\}', css, re.DOTALL):
        family = re.search(r'font-family\s*:\s*([^;}]+)', face).group(1)
        declared.add(family.strip().strip('\"\''))
        # Every custom family must resolve to this renderer's local asset.
        assert "src:url('assets/NotoSansCJK-Regular.ttc')" in face
    used = {family.strip().strip('\"\'')
            for declaration in re.findall(r'font-family\s*:\s*([^;}]+)', css)
            for family in declaration.split(',')}
    assert used <= declared | {'sans-serif', 'serif', 'monospace'}
    assert declared == {'VideoNoto'}


def test_progress_transform_has_one_owner_and_explicit_seek_endpoints():
    page = compile_project(*fixture())['index.html'].decode()
    css = re.search(r'<style>(.*?)</style>', page, re.DOTALL).group(1)
    progress = re.search(r'#progress\s*\{([^}]+)\}', css).group(1)
    assert not re.search(r'(?:^|;)\s*transform\s*:', progress)
    # A from/to range owns both endpoints; a CSS starting transform combined
    # with GSAP's transform cache is rejected by HyperFrames strict lint.
    assert re.search(r"tl\.fromTo\('#progress',\{scaleX:0\},\{scaleX:1,", page)
    assert "duration:data.timeline.duration_seconds,ease:'none',immediateRender:true},0)" in page


def test_header_and_takeaway_leave_room_for_real_cjk_font_line_metrics():
    page = compile_project(*fixture())['index.html'].decode()
    title = re.search(r'h1\s*\{([^}]+)\}', page).group(1)
    takeaway = re.search(r'\.takeaway\s*\{([^}]+)\}', page).group(1)
    def css_number(rule, name):
        return float(re.search(r'(?:^|;)\s*' + name + r':([0-9.]+)', rule).group(1))
    # The real ARM capture rejected 1.25 and 1.35 line boxes with this font.
    # Give the local CJK face at least 1.5 em plus explicit surrounding room.
    assert css_number(title, 'line-height') >= 1.5
    assert css_number(title, 'padding-bottom') >= 4
    assert css_number(takeaway, 'line-height') >= 1.5
    line = css_number(takeaway, 'line-height') * css_number(takeaway, 'font-size')
    assert css_number(takeaway, 'max-height') >= 2 * line + 4
    assert css_number(takeaway, 'top') + css_number(takeaway, 'max-height') <= 720
    # Overflow remains a strict diagnostic instead of being ignored/tolerated.
    assert 'overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight' in page
    for name in ('scrollWidth', 'clientWidth', 'scrollHeight', 'clientHeight'):
        assert f'{name}:el.{name}' in page


def test_vertical_history_label_uses_the_available_gap_between_connection_ports():
    from video_workbench.examples.build_motion_samples import history_plan, SPOKEN_TEXT
    beats = make_beats(SPOKEN_TEXT)
    page = compile_project(history_plan(beats), beats, estimated_alignment(beats, 'a' * 64, 18),
                           compiler_version='motion-compiler-4')['index.html'].decode()
    label = re.search(r'<text id="label-attempt_log"[^>]* x="([0-9.]+)" y="([0-9.]+)"', page)
    assert label is not None
    x, baseline = map(float, label.groups())
    upper, lower = LAYOUTS['focus']['top'], LAYOUTS['focus']['center']
    gap_top, gap_bottom = upper[1] + upper[3], lower[1]
    assert x == 640
    # At 17px the local CJK font needs about 25px for ascent/descent. The
    # baseline must leave actual ink inside the gap, clear of both opaque cards.
    assert gap_top + 22 <= baseline <= gap_bottom - 6
    assert baseline == (gap_top + gap_bottom) / 2 + 6


def test_line_drawing_keeps_fractional_dash_values_and_defers_each_arrow_tip():
    args = fixture()
    page = compile_project(*args)['index.html'].decode()
    assert 'strokeDashoffset:0,opacity:1,duration,autoRound:false' in page
    assert '.arrow-tip{opacity:0}' in page
    for relation in args[0]['relations']:
        identity = relation['id']
        assert f'id="arrow-{identity}"' in page
        assert f'id="tip-{identity}" class="arrow-tip"' in page
        assert f'marker-end="url(#arrow-{identity})"' in page
    assert 'marker-end="url(#arrow)"' not in page
    assert "document.getElementById('tip-'+a.target),{opacity:0}" in page
    assert 'duration:duration*.15,immediateRender:false},when+duration*.85)' in page
    assert "markerOpacity:Number(getComputedStyle(document.getElementById('tip-'+r.id)).opacity)" in page


@pytest.mark.parametrize('template', ['flow', 'compare', 'focus'])
def test_new_connector_pages_have_no_base_paths_and_hide_active_strokes_before_draw(template):
    beats = make_beats('请求到达。请求进入队列。执行节点开始工作。')
    args = example_plan(beats, template=template), beats, estimated_alignment(beats, 'a' * 64, 15)
    page = compile_project(*args)['index.html'].decode()
    assert not re.findall(r'<path\b[^>]*class="relation-base"', page)
    active = re.search(r'\.relation-active\{([^}]+)\}', page).group(1)
    assert re.search(r'(?:^|;)opacity:0(?:;|$)', active)
    assert len(re.findall(r'<path\b[^>]*class="relation-active"', page)) == len(args[0]['relations'])
    assert '{strokeDashoffset:1,opacity:0},{strokeDashoffset:0,opacity:1,' in page


def test_text_cannot_close_script_or_become_template_interpolation():
    plan, beats, alignment = fixture()
    plan['elements'][0]['label'] = '</script><img src=x>'
    plan['elements'][0]['detail'] = '__PAYLOAD__ <script>alert(1)</script>'
    files = compile_project(plan, beats, alignment)
    page = files['index.html'].decode()
    assert '</script><img' not in page
    assert '&lt;/script&gt;&lt;img src=x&gt;' in page
    assert '\\u003c/script\\u003e\\u003cimg' in page
    assert '"detail":"__PAYLOAD__ \\u003cscript' in page
    assert len(re.findall(r'<script(?: |>)', page)) == 2  # local GSAP and compiler-owned code
    assert page.count('</script>') == 2


def test_compilation_is_deterministic_does_not_mutate_inputs_and_static_is_distinct():
    args = fixture()
    original = deepcopy(args)
    first = compile_project(*args)
    assert compile_project(*args) == first
    assert args == original
    baseline = compile_project(*args, animated=False)
    assert first['index.html'] != baseline['index.html']
    assert first['timeline.json'] == baseline['timeline.json']
    assert json.loads(baseline['manifest.json'])['animated'] is False


def test_observation_windows_cover_action_transition_and_respect_duration():
    timeline = resolve_timeline(*fixture())
    windows = action_windows(timeline)
    assert len(windows) == len(timeline['actions'])
    assert all(0 <= row['before'] < row['middle'] < row['after'] < timeline['duration_seconds']
               for row in windows)


def test_fixture_rejects_short_beat_lists_with_explicit_error():
    with pytest.raises(ServiceError, match='motion_example_requires_three_beats'):
        example_plan(make_beats('一句。'))


def test_subframe_animation_is_refused_without_extending_or_clipping_audio():
    plan, beats, alignment = fixture()
    alignment['segments'][0]['end'] = .15
    alignment['segments'][1]['start'] = .15
    original = deepcopy(alignment)
    with pytest.raises(ServiceError, match='motion_beat_too_short_for_animation'):
        resolve_timeline(plan, beats, alignment)
    assert alignment == original


def test_huge_padding_is_refused_as_input_instead_of_overflowing_float_conversion():
    args = fixture()
    with pytest.raises(ServiceError):
        resolve_timeline(*args, leading_seconds=10 ** 1000)


@pytest.mark.parametrize('template', ['flow', 'compare', 'focus'])
def test_v5_orthogonal_routes_avoid_every_unrelated_slot_in_both_directions(template):
    """Include the prior vertical-through-center failure and reverse arrows."""
    from video_workbench.motion_geometry import inflate, route, segment_hits_box
    slots = LAYOUTS[template]
    for source_name, source in slots.items():
        for target_name, target in slots.items():
            if source_name == target_name:
                continue
            points = route(source, target, list(slots.values()))
            assert len(points) >= 2
            for start, end in zip(points, points[1:]):
                assert start[0] == end[0] or start[1] == end[1]
                assert all(not segment_hits_box(start, end, inflate(box, 4))
                           for name, box in slots.items() if name not in {source_name, target_name})
            # Ports attach to the corresponding object, not a nearby object.
            for point, box in ((points[0], source), (points[-1], target)):
                x, y, width, height = box
                assert ((point[0] in {x, x + width} and y <= point[1] <= y + height)
                        or (point[1] in {y, y + height} and x <= point[0] <= x + width))


def test_v5_label_wrapping_and_reservation_preserve_text_and_avoid_other_objects():
    from video_workbench.motion_geometry import connection_geometry, inflate, overlaps, segment_hits_box
    boxes = {key: value for key, value in LAYOUTS['focus'].items() if key in {'top', 'left', 'center', 'right', 'note'}}
    relations = [
        dict(id='vertical', source='top', target='note', label='插件提供工具与执行环境'),
        dict(id='across', source='left', target='right', label='没有待处理工作时才结束'),
        dict(id='up', source='center', target='top', label='保留完整历史'),
    ]
    geometry = connection_geometry(relations, boxes)
    previous = []
    for relation in relations:
        item = geometry[relation['id']]
        assert ''.join(item['lines']) == relation['label']
        assert len(item['lines']) >= 1
        assert all(not overlaps(item['box'], inflate(box, 4)) for box in boxes.values())
        assert all(not overlaps(item['box'], inflate(box, 8)) for box in previous)
        assert all(not segment_hits_box(start, end, box)
                   for start, end in zip(item['points'], item['points'][1:]) for box in previous)
        previous.append(item['box'])
    assert any(len(item['lines']) > 1 for item in geometry.values())
    for identity, item in geometry.items():
        assert all(not segment_hits_box(start, end, other['box'])
                   for start, end in zip(item['points'], item['points'][1:])
                   for other_id, other in geometry.items() if other_id != identity)


def test_v5_branching_results_keep_every_connection_reachable_when_placing_labels():
    """A fitting plate must not seal the only corridor to another result token."""
    from video_workbench.motion import compiler_layout
    from video_workbench.motion_geometry import connection_geometry, inflate, overlaps, segment_hits_box
    rows = [
        ('model', 'card', 'top', '模型'),
        ('pipeline', 'card', 'left', '执行管线'),
        ('checks', 'card', 'center', '权限与策略检查'),
        ('file_impl', 'card', 'right', '文件能力实现'),
        ('session', 'card', 'note', '回到会话'),
        ('file_content', 'token', 'token_right', '读取成功：文件内容'),
        ('missing_result', 'token', 'token_center', '文件不存在：相应结果'),
        ('fact_boundary', 'label', 'caption', '未读到的内容，不能当事实'),
    ]
    plan = dict(template='focus', elements=[dict(id=identity, kind=kind, slot=slot, label=label)
                                           for identity, kind, slot, label in rows])
    layout = compiler_layout(plan, 'motion-compiler-5')
    boxes = {row['id']: layout[row['slot']] for row in plan['elements']}
    relations = [dict(id=identity, source=source, target=target, label=label)
                 for identity, source, target, label in [
                     ('call_to_pipeline', 'model', 'pipeline', '读取调用'),
                     ('pipeline_to_checks', 'pipeline', 'checks', '经过检查'),
                     ('checks_to_file', 'checks', 'file_impl', '调用实现'),
                     ('success_branch', 'file_impl', 'file_content', '读取成功'),
                     ('success_to_session', 'file_content', 'session', '返回内容'),
                     ('missing_branch', 'file_impl', 'missing_result', '若文件不存在'),
                     ('missing_to_session', 'missing_result', 'session', '记录相应结果'),
                     ('session_to_model', 'session', 'model', '下一次请求的依据'),
                 ]]
    reserved = [(72, 74, 1136, 112)]
    geometry = connection_geometry(relations, boxes, reserved)
    assert set(geometry) == {relation['id'] for relation in relations}
    for relation in relations:
        item = geometry[relation['id']]
        assert ''.join(item['lines']) == relation['label']
        assert all(not overlaps(item['box'], inflate(box, 4)) for box in [*boxes.values(), *reserved])
        assert all(not overlaps(item['box'], inflate(other['box'], 8))
                   for identity, other in geometry.items() if identity != relation['id'])
        for start, end in zip(item['points'], item['points'][1:]):
            assert all(not segment_hits_box(start, end, inflate(box, 4))
                       for identity, box in boxes.items() if identity not in {relation['source'], relation['target']})
            assert all(not segment_hits_box(start, end, inflate(other['box'], 6))
                       for identity, other in geometry.items() if identity != relation['id'])


def test_v5_label_plates_are_above_all_strokes_and_follow_draw_visibility():
    from video_workbench.examples.build_motion_samples import history_plan, SPOKEN_TEXT
    beats = make_beats(SPOKEN_TEXT)
    plan = history_plan(beats)
    page = compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 18))['index.html'].decode()
    assert "version:'motion-compiler-5'" in page
    assert 'class="relation-base"' not in page
    assert page.rfind('class="relation-active"') < page.index('class="relation-label"')
    assert 'class="relation-plate"' in page
    assert "el.querySelectorAll('text')" in page  # Real font ink must fit its plate.
    assert 'strokeDashoffset:1,opacity:0' in page
    for relation in plan['relations']:
        assert f'<g id="label-{relation["id"]}"' in page
    assert "document.getElementById('label-'+a.target),{opacity:0},{opacity:1" in page


def test_v5_crowded_labels_fail_explicitly_instead_of_drawing_over_text():
    from video_workbench.motion_geometry import place_label
    with pytest.raises(ValueError, match='motion_relation_layout_unavailable'):
        place_label('关系', [(100, 200), (1100, 200)], [(0, 0, 1280, 720)])


def test_v5_relationship_labels_avoid_the_entire_moving_token_sweep():
    from video_workbench.examples.build_motion_samples import history_plan, SPOKEN_TEXT
    from video_workbench.motion_geometry import connection_geometry, inflate, overlaps
    beats = make_beats(SPOKEN_TEXT)
    plan = history_plan(beats)
    boxes = {row['id']: LAYOUTS[plan['template']][row['slot']] for row in plan['elements']}
    sweep = (162, 302, 956, 44)
    geometry = connection_geometry(plan['relations'], boxes,
        [(72, 74, 1136, 112), (72, 636, 1136, 72)], [sweep])
    assert all(not overlaps(item['box'], inflate(sweep, 4)) for item in geometry.values())
    page = compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 18))['index.html'].decode()
    assert 'overlapsNodes:Object.keys(data.boxes)' in page
    assert '...relationLayout(el)' in page
    assert 'if(!text.textContent)return false;' in page
