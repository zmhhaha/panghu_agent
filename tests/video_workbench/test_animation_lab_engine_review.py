"""Explicit synthetic state fixtures, never real Canvas/WebGL quality evidence."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_lab_canvas import example_canvas_plan
from video_workbench.animation_lab_engine_review import verify_engine_observations
from video_workbench.motion import sha256


def _fixture(engine='canvas'):
    plan = example_canvas_plan() if engine == 'canvas' else {
        'schema': 'synthetic.three-plan.fixture', 'layers': [{'id': f'packet-{index+1}'} for index in range(3)]}
    plan['packet_count'] = 3
    timeline = dict(schema='video.motion-timeline.v1', engine=engine,
        compiler=f'animation-lab-{engine}-compiler-1', fps=24, width=1280, height=720,
        duration_seconds=3, plan_sha256=sha256(plan),
        actions=[dict(ordinal=0, kind='move', target='packet-1' if engine == 'canvas' else 'layers', start=1, duration=1)])
    matrix = [1 if i in (0, 5, 10, 15) else 0 for i in range(16)]

    def sample(time):
        fraction = max(0, min(1, time-1))
        objects = []
        for index in range(3):
            x = 100+index*80+fraction*100
            row = dict(id=f'packet-{index+1}', opacity=1,
                       bounds=dict(x=x-19, y=281, width=38, height=38))
            if engine == 'canvas':
                row.update(x=x, y=300, radius=19,
                           status='done' if time > 2.5 else 'incoming', center_pixel=[80, 150, 170, 255])
            else:
                world = list(matrix)
                world[12] = fraction+index
                row.update(position=[fraction+index, 0, 0], rotation=[0, fraction, 0],
                           scale=[1, 1, 1], matrix_world=world, visible=True)
            objects.append(row)
        state = dict(engine=engine, time=time, objects=objects,
            bitmap_samples=[dict(x=0, y=0, rgba=[12, 24, 36, 255]),
                            dict(x=128, y=128, rgba=[30, 70, 90, 255]),
                            dict(x=640, y=360, rgba=[80, 150, 170, 255])])
        if engine == 'canvas':
            state.update(width=1280, height=720, context='2d', seed=plan['seed'], deterministic=True,
                         queue_count=0, completed_count=3 if time > 2.5 else 0)
        else:
            state.update(three_revision='170',
                webgl=dict(version='WebGL 2.0 (synthetic fixture)', context_lost=False,
                           draw_calls=3, triangles=36, width=1280, height=720),
                camera=dict(position=[0, 0, 6], target=[0, 0, 0], matrix_world=matrix, projection_matrix=matrix))
        return dict(time=time, actual_time=time, engine_state=state,
                    frame_sha256=sha256(dict(synthetic=True, time=time)))

    initial, last = sample(0), sample(3-1/24)
    observed = dict(schema='video.animation-lab-engine-observations.v1',
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24,
        timeline_sha256=sha256(timeline), engine=engine,
        windows=[dict(ordinal=0, before=sample(1-1/24), middle=sample(1.5), after=sample(2+1/24))],
        initial=initial, last=last, repeated_initial=deepcopy(initial),
        reverse_seek=dict(first_sha256=initial['frame_sha256'], last_sha256=last['frame_sha256'],
                          repeated_first_sha256=initial['frame_sha256'], repeat_state_equal=True),
        rendering='canvas_2d' if engine == 'canvas' else 'webgl_software_requested', page_errors=[])
    return plan, timeline, observed


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_valid_synthetic_contract_passes_without_claiming_quality_or_production(engine):
    plan, timeline, observed = _fixture(engine)
    result = verify_engine_observations(plan, timeline, observed)
    assert result['technical_status'] == 'passed'
    assert result['checked_action_windows'] == 1
    assert result['observation_sha256'] == sha256(observed)
    assert result['visual_comprehension'] == 'pending_user_review'
    assert result['production_admitted'] is False


@pytest.mark.parametrize('field,value,code', [
    ('timeline_sha256', 'f'*64, 'timeline_binding'),
    ('engine', 'svg', 'timeline_binding'),
    ('seek_protocol', 'guessed-wall-clock', 'seek_protocol'),
    ('render_fps', 25, 'seek_protocol'),
    ('page_errors', ['context creation failed'], 'page_errors'),
    ('windows', [], 'windows_missing'),
])
def test_wrong_binding_incomplete_capture_and_page_failures_reject(field, value, code):
    plan, timeline, observed = _fixture()
    observed[field] = value
    with pytest.raises(ServiceError, match=code):
        verify_engine_observations(plan, timeline, observed)


@pytest.mark.parametrize('sample_field', ['actual_time', 'time'])
def test_each_declared_action_sample_checks_its_actual_time(sample_field):
    plan, timeline, observed = _fixture()
    observed['windows'][0]['middle'][sample_field] += .2
    with pytest.raises(ServiceError, match='seek_mismatch'):
        verify_engine_observations(plan, timeline, observed)


def test_stale_engine_state_time_rejects_even_when_outer_clock_is_correct():
    plan, timeline, observed = _fixture()
    observed['windows'][0]['middle']['engine_state']['time'] = 0
    with pytest.raises(ServiceError, match='seek_mismatch'):
        verify_engine_observations(plan, timeline, observed)


def test_an_image_change_does_not_substitute_for_scene_geometry_change():
    plan, timeline, observed = _fixture()
    window = observed['windows'][0]
    window['after']['engine_state']['objects'] = deepcopy(window['before']['engine_state']['objects'])
    window['after']['engine_state']['objects'][0]['center_pixel'] = [255, 255, 255, 255]
    with pytest.raises(ServiceError, match='change_missing'):
        verify_engine_observations(plan, timeline, observed)


def test_engine_state_change_does_not_substitute_for_changed_screenshot():
    plan, timeline, observed = _fixture()
    window = observed['windows'][0]
    window['after']['frame_sha256'] = window['before']['frame_sha256']
    with pytest.raises(ServiceError, match='change_missing'):
        verify_engine_observations(plan, timeline, observed)


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_whole_frame_black_or_uniform_bitmap_is_rejected(engine):
    plan, timeline, observed = _fixture(engine)
    state = observed['windows'][0]['middle']['engine_state']
    for row in state['bitmap_samples']:
        row['rgba'] = [0, 0, 0, 255]
    with pytest.raises(ServiceError, match='bitmap_blank'):
        verify_engine_observations(plan, timeline, observed)


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_visible_projected_object_outside_viewport_rejects(engine):
    plan, timeline, observed = _fixture(engine)
    observed['windows'][0]['middle']['engine_state']['objects'][0]['bounds']['x'] = 1279
    with pytest.raises(ServiceError, match='bounds_invalid'):
        verify_engine_observations(plan, timeline, observed)


def test_nonfinite_camera_transform_is_rejected():
    plan, timeline, observed = _fixture('three')
    observed['windows'][0]['middle']['engine_state']['camera']['position'][0] = float('nan')
    with pytest.raises(ServiceError, match='nonfinite'):
        verify_engine_observations(plan, timeline, observed)


@pytest.mark.parametrize('mutation,code', [
    (lambda state: state.update(three_revision='invented'), 'three_revision'),
    (lambda state: state['webgl'].update(context_lost=True), 'context_invalid'),
    (lambda state: state['webgl'].update(draw_calls=0), 'webgl_draw_missing'),
    (lambda state: state['objects'][0].update(visible=False), 'object_hidden'),
])
def test_three_requires_actual_context_revision_and_visible_draws(mutation, code):
    plan, timeline, observed = _fixture('three')
    mutation(observed['windows'][0]['middle']['engine_state'])
    with pytest.raises(ServiceError, match=code):
        verify_engine_observations(plan, timeline, observed)


def test_reverse_seek_claim_does_not_override_different_first_state():
    plan, timeline, observed = _fixture()
    observed['repeated_initial']['engine_state']['objects'][0]['opacity'] = .5
    with pytest.raises(ServiceError, match='reverse_seek_mismatch'):
        verify_engine_observations(plan, timeline, observed)


def test_reverse_seek_claim_does_not_override_different_first_image():
    plan, timeline, observed = _fixture()
    observed['repeated_initial']['frame_sha256'] = 'a'*64
    observed['reverse_seek']['repeated_first_sha256'] = 'a'*64
    with pytest.raises(ServiceError, match='reverse_seek_mismatch'):
        verify_engine_observations(plan, timeline, observed)


def test_last_sample_must_be_present_and_bound_to_reverse_seek_digest():
    plan, timeline, observed = _fixture()
    del observed['last']
    with pytest.raises(ServiceError, match='sample_missing'):
        verify_engine_observations(plan, timeline, observed)


def test_canvas_final_completion_cannot_be_invented_by_count_label():
    plan, timeline, observed = _fixture()
    observed['last']['engine_state']['completed_count'] = 0
    with pytest.raises(ServiceError, match='count_mismatch'):
        verify_engine_observations(plan, timeline, observed)


def test_three_cannot_substitute_an_unrelated_mesh_for_a_declared_layer():
    plan, timeline, observed = _fixture('three')
    observed['windows'][0]['middle']['engine_state']['objects'][0]['id'] = 'unrelated-cube'
    with pytest.raises(ServiceError, match='objects_invalid'):
        verify_engine_observations(plan, timeline, observed)


def test_three_cannot_hide_a_declared_layer_by_omitting_its_snapshot():
    plan, timeline, observed = _fixture('three')
    observed['windows'][0]['middle']['engine_state']['objects'].pop()
    with pytest.raises(ServiceError, match='objects_invalid'):
        verify_engine_observations(plan, timeline, observed)


def test_camera_changes_do_not_satisfy_a_declared_layer_explosion():
    plan, timeline, observed = _fixture('three')
    before = observed['windows'][0]['before']['engine_state']
    after = observed['windows'][0]['after']['engine_state']
    for first, last in zip(before['objects'], after['objects']):
        last['matrix_world'] = deepcopy(first['matrix_world'])
    after['camera']['position'][0] = 1
    # Projected bounds and screenshot still differ, as happens under a camera
    # move; the immutable mesh world matrices reveal the missing explosion.
    with pytest.raises(ServiceError, match='target_change_missing'):
        verify_engine_observations(plan, timeline, observed)


def test_mesh_changes_do_not_satisfy_a_declared_camera_move():
    plan, timeline, observed = _fixture('three')
    timeline['actions'][0]['target'] = 'camera'
    observed['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError, match='target_change_missing'):
        verify_engine_observations(plan, timeline, observed)


def test_declared_camera_move_accepts_actual_camera_change_with_fixed_mesh_worlds():
    plan, timeline, observed = _fixture('three')
    timeline['actions'][0]['target'] = 'camera'
    observed['timeline_sha256'] = sha256(timeline)
    window = observed['windows'][0]
    for index, name in enumerate(('before', 'middle', 'after')):
        state = window[name]['engine_state']
        state['camera']['position'][0] = index
        for first, row in zip(window['before']['engine_state']['objects'], state['objects']):
            row['matrix_world'] = deepcopy(first['matrix_world'])
    assert verify_engine_observations(plan, timeline, observed)['technical_status'] == 'passed'


def _context_fixture(engine):
    """Add synthetic text observations; never represented as browser evidence."""
    import math
    plan, timeline, observed = _fixture(engine)
    if engine == 'canvas':
        plan.update(schema='video.canvas-plan.v2', labels=dict(source='登记区', queue='待诊区', worker='诊室'),
            context=dict(noun='登记单', source_detail='登记单依次到达', queue_detail='等待空闲诊室',
                worker_detail='逐张领取并完成', phase_intro='登记单先进入待诊区', phase_queue='待诊区暂存登记单',
                phase_work='诊室逐张领取登记单', phase_done='所有登记单已处理完成'))
    else:
        from video_workbench.animation_lab_three import example_three_plan
        plan = example_three_plan()
        for index, layer in enumerate(plan['layers']):
            layer['id'] = 'packet-'+str(index+1)
        plan.update(schema='video.animation-lab-three-plan.v2',
                    context=dict(phase_whole='完整平台：各层共同协作', phase_expand='展开层级：观察职责'))
    timeline.update(compiler=f'animation-lab-{engine}-compiler-2', variant='candidate', plan_sha256=sha256(plan))
    observed['timeline_sha256'] = sha256(timeline)
    samples = [observed[key] for key in ('initial', 'last', 'repeated_initial')]
    samples += [window[key] for window in observed['windows'] for key in ('before', 'middle', 'after')]
    for sample in samples:
        state = sample['engine_state']
        time = state['time']; normalized = time/timeline['duration_seconds']
        if engine == 'canvas':
            stage = ('arrival' if normalized < 4.8/18 else 'buffer' if normalized < 6.4/18
                     else 'complete' if state['completed_count'] == plan['packet_count'] else 'dispatch')
            phase_key = {'arrival': 'phase_intro', 'buffer': 'phase_queue',
                         'dispatch': 'phase_work', 'complete': 'phase_done'}[stage]
            state.update(stage=stage, phase_text=plan['context'][phase_key])
            texts = list(plan['labels'].values())+[plan['context'][key+'_detail'] for key in ('source', 'queue', 'worker')]
        else:
            smooth = lambda v: (lambda p: p*p*(3-2*p))(max(0, min(1, v)))
            explode = smooth((normalized-.12)/.18)*(1-smooth((normalized-.8)/.16))
            focus = smooth((normalized-.32)/.4)
            strength = math.sin(math.pi*focus)
            index = min(len(plan['layers'])-1, math.floor(focus*len(plan['layers'])))
            state['phase_text'] = (plan['context']['phase_whole'] if explode < .03 else
                plan['layers'][index]['label']+'：'+plan['layers'][index]['detail'] if strength > .1
                else plan['context']['phase_expand'])
            state['layer_texts'] = [dict(id=row['id'], label=row['label'], detail=row['detail']) for row in plan['layers']]
            texts = [row[key] for row in plan['layers'] for key in ('label', 'detail')]
        texts.append(state['phase_text'])
        state['text_boxes'] = [dict(text=text, opacity=1, font_size=14, bounds=dict(x=64, y=100+index*30, width=240, height=22))
                               for index, text in enumerate(texts)]
    return plan, timeline, observed


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_v2_domain_text_observation_contract_is_checked_without_changing_v1(engine):
    plan, timeline, observed = _context_fixture(engine)
    assert verify_engine_observations(plan, timeline, observed)['technical_status'] == 'passed'
    assert verify_engine_observations(*_fixture(engine))['technical_status'] == 'passed'


@pytest.mark.parametrize('engine', ['canvas', 'three'])
@pytest.mark.parametrize('failure', ['missing_text', 'wrong_phase', 'wrong_label', 'small_font', 'offscreen', 'compiler', 'context_plan'])
def test_v2_rejects_domain_language_font_geometry_and_schema_binding_failures(engine, failure):
    plan, timeline, observed = _context_fixture(engine)
    state = observed['windows'][0]['middle']['engine_state']
    if failure == 'missing_text':
        state.pop('text_boxes')
    elif failure == 'wrong_phase':
        state['phase_text'] = '请求全部完成'
    elif failure == 'wrong_label':
        state['text_boxes'][0]['text'] = 'unrelated label'
    elif failure == 'small_font':
        state['text_boxes'][0]['font_size'] = 13
    elif failure == 'offscreen':
        state['text_boxes'][0]['bounds']['x'] = 1270
    elif failure == 'compiler':
        timeline['compiler'] = f'animation-lab-{engine}-compiler-1'
        observed['timeline_sha256'] = sha256(timeline)
    else:
        plan['context']['script'] = 'unbounded program'
        timeline['plan_sha256'] = sha256(plan)
        observed['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError):
        verify_engine_observations(plan, timeline, observed)


def test_three_v2_labels_cannot_be_swapped_while_keeping_all_words_visible():
    plan, timeline, observed = _context_fixture('three')
    state = observed['windows'][0]['middle']['engine_state']
    state['layer_texts'][0]['label'], state['layer_texts'][1]['label'] = state['layer_texts'][1]['label'], state['layer_texts'][0]['label']
    with pytest.raises(ServiceError, match='context_text_mismatch'):
        verify_engine_observations(plan, timeline, observed)
