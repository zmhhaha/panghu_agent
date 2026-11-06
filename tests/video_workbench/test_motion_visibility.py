"""HTTPS feedback: moves need visible tokens and hidden nodes take wires with them."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion import (
    action_windows, compile_project, estimated_alignment, example_plan, make_beats,
    preflight_plan, resolve_timeline,
)
from video_workbench.motion_review import _connector_states, _connectors
from video_workbench.motion_semantic import element_opacity


def fixture():
    beats = make_beats('甲。乙。丙。')
    plan = example_plan(beats)
    plan['elements'] = [row for row in plan['elements'] if row['id'] == 'request']
    plan['relations'] = []
    plan['elements'][0].update(id='certificate', label='证书', slot='token_source')
    plan['actions'] = []
    return plan, beats


def action(beats, kind, beat=0, placement='start', target='certificate', pace='normal'):
    return dict(beat_id=beats['segments'][beat]['id'], kind=kind, target=target,
                placement=placement, pace=pace, to_slot='token_queue' if kind == 'move' else None)


@pytest.mark.parametrize('reverse', [False, True])
def test_https_certificate_reveal_and_move_at_same_anchor_is_rejected_before_saving(reverse):
    plan, beats = fixture()
    plan['actions'] = [action(beats, 'reveal', pace='quick'), action(beats, 'move')]
    if reverse:
        plan['actions'].reverse()
    before = deepcopy(plan)
    with pytest.raises(ServiceError, match='motion_invisible_move') as raised:
        preflight_plan(plan, beats)
    ordinal = next(index for index, row in enumerate(plan['actions']) if row['kind'] == 'move')
    assert raised.value.motion_context == dict(target='certificate', kind='move', ordinal=ordinal,
                                               beat_id=beats['segments'][0]['id'])
    assert plan == before
    with pytest.raises(ServiceError, match='motion_invisible_move'):
        resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 54))
    # Historical projects remain reproducible instead of silently losing verbs.
    assert len(resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 54),
                                compiler_version='motion-compiler-5')['actions']) == 2


@pytest.mark.parametrize('placement', ['start', 'middle'])
def test_hide_before_or_during_move_is_rejected_before_saving(placement):
    plan, beats = fixture()
    plan['actions'] = [action(beats, 'hide', beat=1, placement=placement),
                       action(beats, 'move', beat=1, placement='middle')]
    with pytest.raises(ServiceError, match='motion_invisible_move'):
        preflight_plan(plan, beats)


def test_move_before_initial_reveal_is_rejected_but_after_completed_reveal_passes():
    plan, beats = fixture()
    plan['actions'] = [action(beats, 'move'), action(beats, 'reveal', beat=1)]
    with pytest.raises(ServiceError, match='motion_invisible_move'):
        preflight_plan(plan, beats)
    plan['actions'] = [action(beats, 'reveal'), action(beats, 'move', placement='middle')]
    assert preflight_plan(plan, beats)
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))
    samples = action_windows(timeline)[1]
    assert all(element_opacity(timeline, 'certificate', samples[label]) >= .98
               for label in ('before', 'middle', 'after'))


def test_hidden_token_can_be_revealed_again_before_moving():
    plan, beats = fixture()
    plan['actions'] = [action(beats, 'hide'), action(beats, 'reveal', beat=1),
                       action(beats, 'move', beat=1, placement='middle')]
    assert preflight_plan(plan, beats)
    assert resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))


@pytest.mark.parametrize('reverse_simultaneous', [False, True])
def test_visible_token_can_be_redundantly_revealed_while_moving(reverse_simultaneous):
    plan, beats = fixture()
    simultaneous = [action(beats, 'reveal', beat=1), action(beats, 'move', beat=1)]
    if reverse_simultaneous:
        simultaneous.reverse()
    plan['actions'] = [action(beats, 'reveal'), *simultaneous]
    before = deepcopy(plan)
    assert preflight_plan(plan, beats)
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))
    move, samples = next((row, times) for row, times in zip(timeline['actions'], action_windows(timeline))
                         if row['kind'] == 'move')
    assert all(element_opacity(timeline, move['target'], samples[label]) == 1
               for label in ('before', 'middle', 'after'))
    assert plan == before


def test_reveal_after_prior_hide_cannot_make_a_simultaneous_move_visible():
    plan, beats = fixture()
    plan['actions'] = [action(beats, 'reveal'), action(beats, 'hide', beat=1),
                       action(beats, 'reveal', beat=2), action(beats, 'move', beat=2)]
    with pytest.raises(ServiceError, match='motion_invisible_move'):
        preflight_plan(plan, beats)
    with pytest.raises(ServiceError, match='motion_invisible_move'):
        resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))


def test_actual_recording_can_reject_move_whose_earlier_reveal_is_still_fading():
    plan, beats = fixture()
    plan['actions'] = [action(beats, 'reveal', placement='end'), action(beats, 'move', beat=1)]
    assert preflight_plan(plan, beats)
    with pytest.raises(ServiceError, match='motion_invisible_move') as raised:
        resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 1.05))
    assert raised.value.motion_context['ordinal'] == 1
    assert resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))


def relations_fixture():
    beats = make_beats('构建渲染树。生成画面。收束整个过程。')
    plan = example_plan(beats, template='focus')
    plan['elements'] = [dict(id=identity, kind=kind, label=label, detail='', slot=slot)
        for identity, kind, label, slot in [
            ('html_dom', 'card', 'DOM', 'top'), ('css_cssom', 'card', 'CSSOM', 'left'),
            ('render_tree', 'card', '渲染树', 'center'), ('render_stages', 'card', '布局与绘制', 'right'),
            ('first_screen', 'card', '首屏可见', 'note'),
            ('chain_recap', 'label', '名字 → 地址 → 通道 → 消息 → 像素', 'caption')]]
    plan['relations'] = [dict(id=identity, source=source, target=target, label='')
        for identity, source, target in [
            ('dom_to_tree', 'html_dom', 'render_tree'), ('cssom_to_tree', 'css_cssom', 'render_tree'),
            ('tree_to_stages', 'render_tree', 'render_stages'),
            ('stages_to_screen', 'render_stages', 'first_screen')]]
    plan['actions'] = [action(beats, 'draw', target=row['id']) for row in plan['relations']]
    plan['actions'] += [action(beats, 'hide', beat=2, target=row['id'])
                        for row in plan['elements'] if row['kind'] == 'card']
    plan['actions'].append(action(beats, 'reveal', beat=2, target='chain_recap'))
    return plan, beats


def test_https_five_hidden_cards_leave_caption_and_zero_orphan_lines_in_trailing_hold():
    plan, beats = relations_fixture()
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 53), trailing_seconds=4)
    final = timeline['duration_seconds'] - 1 / 24
    assert element_opacity(timeline, 'chain_recap', final) == 1
    for state in _connector_states(plan, timeline, final).values():
        assert state == dict(opacity=0, labelOpacity=0, markerOpacity=0, strokeDashoffset=0)
    old = dict(timeline, compiler='motion-compiler-5')
    assert all(state['opacity'] == state['labelOpacity'] == state['markerOpacity'] == 1
               for state in _connector_states(plan, old, final).values())


def test_endpoint_visibility_multiplies_source_target_draw_and_retains_strict_observer_checks():
    plan, beats = relations_fixture()
    plan['relations'] = plan['relations'][:1]
    plan['actions'] = [action(beats, 'reveal', target='html_dom'),
                       action(beats, 'reveal', target='render_tree'),
                       action(beats, 'draw', beat=1, target='dom_to_tree'),
                       action(beats, 'hide', beat=2, target='html_dom')]
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))
    hide = next(row for row in timeline['actions'] if row['kind'] == 'hide')
    time = hide['start'] + hide['duration'] / 2
    expected = _connector_states(plan, timeline, time)
    assert expected['dom_to_tree']['opacity'] == pytest.approx(.5)
    assert expected['dom_to_tree']['labelOpacity'] == pytest.approx(.5)
    assert expected['dom_to_tree']['markerOpacity'] == pytest.approx(.5)
    sample = dict(relations={key: dict(value, baseCount=0, length=100) for key, value in expected.items()})
    _connectors(sample, plan, timeline, time)
    sample['relations']['dom_to_tree']['opacity'] = 1
    with pytest.raises(ServiceError, match='motion_connector_state_mismatch'):
        _connectors(sample, plan, timeline, time)
    # A relation can already be drawn while both endpoints start to appear.
    plan['actions'] = [action(beats, 'draw', target='dom_to_tree'),
                       action(beats, 'reveal', beat=1, target='html_dom'),
                       action(beats, 'reveal', beat=1, target='render_tree')]
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))
    reveal = next(row for row in timeline['actions'] if row['kind'] == 'reveal')
    state = _connector_states(plan, timeline, reveal['start'] + reveal['duration'] / 2)['dom_to_tree']
    assert state['opacity'] == state['labelOpacity'] == state['markerOpacity'] == pytest.approx(.25)


def test_compiler_owns_two_endpoint_wrappers_for_strokes_and_labels_without_changing_plan():
    plan, beats = relations_fixture()
    before = deepcopy(plan)
    files = compile_project(plan, beats, estimated_alignment(beats, 'a' * 64, 53), trailing_seconds=4)
    page = files['index.html'].decode()
    assert page.count('class="relation-endpoint relation-endpoint-html_dom"') == 2
    assert page.count('class="relation-endpoint relation-endpoint-render_tree"') == 6
    assert 'opacity:Number(style.opacity)*endpointFactor(el)' in page
    assert 'endpointFactor(document.getElementById(\'label-\'+r.id))' in page
    assert 'for(const endpoint of endpointNodes(a.target))tl.fromTo(endpoint' in page
    assert 'onUpdate' not in page
    assert page.index('id="rel-stages_to_screen"') < page.index('id="label-dom_to_tree"')
    assert json.loads(files['plan.json']) == before == plan
