"""Demonstration opt-in, copy semantics and compatibility with diagram projects."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.video_motion_contract import validate_motion_plan
from video_workbench.examples.build_motion_demo import build, demonstration_plan
from video_workbench.examples.build_motion_samples import SPEECH_SHA256, SPOKEN_TEXT
from video_workbench.motion import (
    canonical, compile_project, estimated_alignment, make_beats, resolve_timeline, sha256,
)
from video_workbench.motion_demo import (
    DEMO_COMPILER_VERSION, DEMO_LAYOUT, DEMO_TEMPLATE_VERSION,
    compile_demo_project, resolve_demo_timeline,
)


def fixture():
    beats = make_beats(SPOKEN_TEXT)
    return demonstration_plan(beats), beats, estimated_alignment(beats, SPEECH_SHA256, 18)


def test_demo_is_opt_in_and_both_original_entry_points_stay_closed():
    args = fixture()
    for call in (validate_motion_plan, resolve_timeline, compile_project):
        with pytest.raises(ServiceError, match='invalid_motion_plan'):
            call(*args[:2] if call is validate_motion_plan else args)
    files = compile_demo_project(*args, leading_seconds=3, trailing_seconds=3)
    manifest, timeline = json.loads(files['manifest.json']), json.loads(files['timeline.json'])
    assert manifest['compiler'] == timeline['compiler'] == DEMO_COMPILER_VERSION
    assert manifest['template'] == DEMO_TEMPLATE_VERSION
    assert timeline['duration_seconds'] == 24
    assert timeline['audio_seconds'] == 18
    assert timeline['audio_sha256'] == SPEECH_SHA256
    assert timeline['alignment_method'] == 'estimated' and timeline['review_status'] == 'pending'
    assert manifest['runtime_fingerprint'] is None


def test_scene_is_a_copy_without_discarding_or_recoloring_the_log_records():
    plan, beats, alignment = fixture()
    elements = {row['id']: row for row in plan['elements']}
    original, copied = elements['answer'], elements['answer_copy']
    assert {k: v for k, v in original.items() if k not in {'id', 'slot'}} == {
        k: v for k, v in copied.items() if k not in {'id', 'slot'}}
    assert plan['relations'] == []
    assert {row['kind'] for row in plan['actions']} == {'reveal', 'move'}
    timeline = resolve_demo_timeline(plan, beats, alignment, leading_seconds=3, trailing_seconds=3)
    slide = [a for a in timeline['actions'] if a['kind'] == 'move' and a['target'] != 'answer_copy']
    assert {a['target'] for a in slide} == {'log', 'answer', 'attempt'}
    assert len({(a['start'], a['duration']) for a in slide}) == 1
    offsets = []
    for action in slide:
        before, after = DEMO_LAYOUT[elements[action['target']]['slot']], DEMO_LAYOUT[action['to_slot']]
        offsets.append((after[0] - before[0], after[1] - before[1]))
        assert before[2:] == after[2:]
    assert set(offsets) == {(-308, 0)}
    clone_reveal = next(a for a in timeline['actions'] if a['target'] == 'answer_copy' and a['kind'] == 'reveal')
    clone_move = next(a for a in timeline['actions'] if a['target'] == 'answer_copy' and a['kind'] == 'move')
    assert clone_reveal['start'] > slide[0]['start'] + slide[0]['duration']
    assert clone_move['start'] > clone_reveal['start'] + clone_reveal['duration']
    assert clone_move['to_slot'] == 'answer_right'
    final = {key: row['slot'] for key, row in elements.items()}
    for action in timeline['actions']:
        if action['kind'] == 'move':
            final[action['target']] = action['to_slot']
    assert final['answer'] == 'answer_left' and final['answer_copy'] == 'answer_right'
    assert final['attempt'] == 'attempt_left'
    assert not any(a['kind'] == 'hide' and a['target'] in {'answer', 'attempt'} for a in plan['actions'])


def test_same_kind_slots_fit_the_canvas_and_keep_dimensions_throughout_moves():
    for x, y, width, height in DEMO_LAYOUT.values():
        assert 0 <= x < x + width <= 1280
        assert 0 <= y < y + height <= 720
    for prefix in ('log_', 'answer_', 'attempt_'):
        assert len({rect[2:] for slot, rect in DEMO_LAYOUT.items() if slot.startswith(prefix)}) == 1
    left, right = DEMO_LAYOUT['log_left'], DEMO_LAYOUT['context_right']
    assert left[0] + left[2] < right[0]
    for container_slot, record_slots in (
        ('log_center', ('answer_center', 'attempt_center')),
        ('log_left', ('answer_left', 'attempt_left')),
        ('context_right', ('answer_right',)),
    ):
        x, y, width, height = DEMO_LAYOUT[container_slot]
        for slot in record_slots:
            rx, ry, rw, rh = DEMO_LAYOUT[slot]
            assert x < rx < rx + rw < x + width
            assert y < ry < ry + rh < y + height


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(template='flow'),
    lambda p: p.update(relations=[dict(id='ghost', source='answer', target='context', label='')]),
    lambda p: p['actions'][0].update(kind='highlight'),
    lambda p: p['actions'][0].update(kind='draw'),
    lambda p: p['actions'][3].update(to_slot='answer_left'),
    lambda p: p['actions'][3].update(target='context', to_slot='context_right'),
    lambda p: p['actions'][3].update(target='kept', to_slot='result_right'),
    lambda p: p['elements'][0].update(x=20),
    lambda p: p['actions'][0].update(start=3),
])
def test_demo_never_accepts_connections_arbitrary_code_or_unbounded_motion(mutate):
    plan, beats, alignment = fixture()
    mutate(plan)
    with pytest.raises(ServiceError, match='invalid_motion_plan'):
        compile_demo_project(plan, beats, alignment)


def test_all_text_is_escaped_and_observation_owns_the_complete_scene():
    plan, beats, alignment = fixture()
    plan['elements'][1]['label'] = '</script><img src=x>'
    plan['elements'][1]['detail'] = '__PAYLOAD__ <script>alert(1)</script>'
    page = compile_demo_project(plan, beats, alignment)['index.html'].decode()
    assert '&lt;/script&gt;&lt;img src=x&gt;' in page
    assert '\\u003c/script\\u003e\\u003cimg' in page
    assert '"detail":"__PAYLOAD__ \\u003cscript' in page
    assert page.count('</script>') == 2
    assert 'https://' not in page and 'setInterval(' not in page and 'Math.random' not in page
    assert 'data-composition-id="video-motion"' in page
    assert "window.__timelines['video-motion']=tl" in page
    assert 'inspectRelations:()=>({})' in page and 'relations:[]' in page
    assert 'relation-base' not in page and 'id="connections"' not in page
    for row in plan['elements']:
        assert f'data-motion-element="{row["id"]}"' in page
    assert "fontReady:document.fonts.status==='loaded'&&document.fonts.check('20px VideoNoto')" in page
    assert 'ready:document.fonts.ready.then(()=>true)' in page


def test_fixed_colors_and_local_fonts_have_no_animation_or_css_transform_owner_conflict():
    page = compile_demo_project(*fixture())['index.html'].decode()
    css, script = re.search(r'<style>(.*?)</style>', page, re.DOTALL).group(1), page.split("'use strict';", 1)[1]
    assert "src:url('assets/NotoSansCJK-Regular.ttc')" in css
    assert 'font-family:VideoNoto,sans-serif' in css
    assert not re.search(r'(?:^|[;{])\s*transform\s*:', css)
    assert 'backgroundColor:' not in script.split('const metrics=el=>')[0]
    assert 'borderColor:' not in script.split('const metrics=el=>')[0]
    assert '.attempt{border-color:#d7a36b;background:#ffebd5;color:#8a491d}' in css
    assert "tl.fromTo('#progress',{scaleX:0},{scaleX:1," in script
    assert 'line-height:1.5' in css
    assert 'descendants.some' in script
    assert 'child.scrollWidth>child.clientWidth||child.scrollHeight>child.clientHeight' in script


def test_empty_label_detail_has_no_dom_node_while_nonempty_text_keeps_strict_bounds_checks():
    plan, beats, alignment = fixture()
    labels = [row for row in plan['elements'] if row['kind'] == 'label']
    assert all(row['detail'] == '' for row in labels)
    page = compile_demo_project(plan, beats, alignment)['index.html'].decode()
    details = re.findall(r'<div class="element-detail">(.*?)</div>', page, re.DOTALL)
    assert len(details) == len(plan['elements']) - len(labels)
    assert all(detail.strip() for detail in details)
    assert '.element-detail:empty' not in page

    labels[0]['detail'] = '保留原始记录'
    labels[1]['detail'] = '   '
    revised = compile_demo_project(plan, beats, alignment)['index.html'].decode()
    details = re.findall(r'<div class="element-detail">(.*?)</div>', revised, re.DOTALL)
    assert len(details) == len(plan['elements']) - 1
    assert '保留原始记录' in details and all(detail.strip() for detail in details)
    # Real nonempty text is still observed regardless of visibility, size or
    # position; do not fix the zero-box false positive by filtering metrics.
    assert "el.querySelectorAll('.element-label,.element-detail')" in revised
    assert 'descendants.some(child=>' in revised
    assert 'child.scrollWidth>child.clientWidth||child.scrollHeight>child.clientHeight' in revised
    for edge in ('c.x<r.x', 'c.y<r.y', 'c.right>r.right', 'c.bottom>r.bottom'):
        assert edge in revised


def test_compile_is_deterministic_and_preserves_inputs_with_distinct_static_baseline():
    args = fixture()
    before = deepcopy(args)
    files = compile_demo_project(*args)
    assert files == compile_demo_project(*args) and args == before
    static = compile_demo_project(*args, animated=False)
    assert files['timeline.json'] == static['timeline.json']
    assert files['index.html'] != static['index.html']
    for name, expected in json.loads(files['manifest.json'])['files'].items():
        assert sha256(files[name]) == expected


def test_short_beat_is_rejected_without_stretching_audio():
    plan, beats, alignment = fixture()
    alignment['segments'][0]['end'] = .15
    alignment['segments'][1]['start'] = .15
    with pytest.raises(ServiceError, match='motion_beat_too_short_for_animation'):
        resolve_demo_timeline(plan, beats, alignment)


def test_builder_refuses_unknown_audio_without_creating_output(tmp_path):
    speech = tmp_path / 'unknown.wav'
    speech.write_bytes(b'unknown audio')
    with pytest.raises(ValueError, match='SHA-256'):
        build(tmp_path / 'sample', speech)
    assert not (tmp_path / 'sample').exists()


def test_r8_real_projects_remain_byte_identical_when_available():
    # This is an additional integration regression, not the sole compatibility
    # test. R8 is a local validation artifact outside version control.
    root = Path('D:/github/armbianbegin/.runtime/video-motion-samples-r8')
    if not root.is_dir():
        pytest.skip('R8 local validation projects are unavailable')
    names = ('technical-15s', 'history-motion-24s', 'history-static-24s')
    for name in names:
        directory = root / name
        args = [json.loads((directory / f'{kind}.json').read_bytes()) for kind in ('plan', 'beats', 'alignment')]
        manifest, timeline = [json.loads((directory / f'{kind}.json').read_bytes()) for kind in ('manifest', 'timeline')]
        files = compile_project(*args, animated=manifest['animated'], runtime_fingerprint=manifest['runtime_fingerprint'],
                                leading_seconds=timeline['leading_seconds'], trailing_seconds=timeline['trailing_seconds'])
        for filename, content in files.items():
            assert content == (directory / filename).read_bytes(), (name, filename)
