"""Tamper tests over synthetic DOM; these are not real browser evidence.

The fixture oracle uses the compiler evaluator, while the production reviewer
implements its own evaluator and independently checks measured geometry.
"""
from copy import deepcopy
import hashlib
import json
import math

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.motion import FPS, estimated_alignment, make_beats, sha256
from video_workbench.motion_process import STATE_PRESENTATIONS, resolve_process_timeline
from video_workbench.process_continuity import (
    build_process_continuity_plan, resolve_process_continuity,
    compile_process_continuity_project, expected_process_continuity_states,
)
from video_workbench.process_continuity_review import (
    verify_process_continuity_observations, seek_sequence_times, _segment_hits_box, expected_states,
)
from video_workbench.process_review import _path_position


@pytest.mark.parametrize('start,end,hit', [((0, 0), (20, 20), True), ((0, 0), (10, 10), False),
    ((0, 10), (20, 10), False), ((0, 15), (20, 15), True), ((15, 0), (15, 20), True),
    ((5, 15), (15, 5), False), ((0, 30), (30, 0), True), ((20, 20), (30, 30), False)])
def test_swept_archive_clearance_checks_diagonals_and_permits_exact_touch(start, end, hit):
    assert _segment_hits_box(start, end, (10, 10, 10, 10)) is hit


@pytest.mark.parametrize('time,slot', [(math.nextafter(.6, -math.inf), 'token_queue'),
    (.6, 'token_source'), (math.nextafter(.6, math.inf), 'token_source')])
def test_discrete_transfer_slot_completes_at_exact_timestamp_without_boundary_tolerance(time, slot):
    # Real copy-boundary evidence exposed this cancellation error: the quotient
    # is below one at exactly .6, while its endpoint timestamp is exactly .6.
    assert (.6 - .15) / .45 < 1 and .15 + .45 == .6
    segment = dict(index=1, relations={}, initial_states=dict(request=dict(slot='token_queue',
        x=572, y=470, width=136, height=44)), timeline=dict(actions=[dict(
            scope='element', target='request', kind='tween', process_kind='transfer', start=.15, duration=.45,
            before=dict(x=572, y=470, slot='token_queue'), after=dict(x=136, y=470, slot='token_source'),
            points=[[640, 492], [204, 492]])]))
    assert expected_states(segment, time)['elements']['request']['slot'] == slot


def inputs():
    result = []
    for index in range(2):
        narration = '请求进入。队列处理。保留请求。' if index == 0 else '继续处理。交给执行器。执行完成。'
        beats = make_beats(narration)
        beat_ids = [row['id'] for row in beats['segments']]
        def action(beat, kind, target, placement='start', **extra):
            return dict(beat_id=beat_ids[beat], kind=kind, target=target,
                        placement=placement, pace='normal', **extra)
        elements = [dict(id=name, kind='card', slot=name, label=label, detail='处理节点')
                    for name, label in [('source', '入口'), ('queue', '队列'), ('worker', '执行器')]]
        elements.append(dict(id='request', kind='token', slot='token_source' if index == 0 else 'token_queue',
                             label='请求', detail=''))
        relation = dict(id='route', source='source' if index == 0 else 'queue',
                        target='queue' if index == 0 else 'worker', label='交付')
        actions = [action(0, 'draw', 'route'), action(0, 'reveal', 'request', 'middle'),
                   action(1, 'transfer', 'request', relation_id='route')]
        if index == 0:
            actions += [action(1, 'set_state', 'queue', 'end', state='waiting'),
                        action(2, 'set_state', 'queue', state='running')]
        else:
            actions += [action(0, 'set_state', 'queue', state='running'),
                        action(1, 'set_state', 'worker', 'end', state='running'),
                        action(2, 'set_state', 'worker', state='completed')]
        plan = dict(schema='video.process-plan.v1', template='flow', title=f'请求阶段{index + 1}',
                    takeaway='相同对象继续完成处理。', elements=elements, relations=[relation], actions=actions)
        audio_sha = ('a' if index == 0 else 'b') * 64
        alignment = estimated_alignment(beats, audio_sha, 12)
        timeline = resolve_process_timeline(plan, beats, alignment, compiler_version='motion-process-compiler-2')
        result.append(dict(id=f'scene-{index}', title=plan['title'], narration=narration,
                           motion=dict(id=f'motion-{index}', plan=plan, beats=beats, alignment=alignment, timeline=timeline),
                           audio=dict(id=f'audio-{index}', sha256=audio_sha),
                           timing=dict(leading_seconds=0, trailing_seconds=0)))
    return result


def box(x, y, width, height, **extra):
    return dict(x=x, y=y, width=width, height=height, scrollWidth=width, clientWidth=width,
                scrollHeight=height, clientHeight=height, overflow=False, **extra)


def sample(segment, time):
    expected = expected_process_continuity_states(segment, time)
    actual = {}
    for identity, row in expected['elements'].items():
        x, y, width, height = (row[key] for key in ('x', 'y', 'width', 'height'))
        state = box(x, y, width, height, **{key: row[key] for key in ('label', 'detail', 'slot', 'copied_from', 'opacity')})
        if row['kind'] == 'card':
            presentation = STATE_PRESENTATIONS[row['state']]
            state.update(state=row['state'], stateLabel=presentation['label'], stateSymbol=presentation['symbol'],
                         statePath=presentation['path'], stateOpacities={key: int(key == row['state']) for key in STATE_PRESENTATIONS},
                         docking_bay=dict(x=x + 14, y=y + 204, width=252, height=56, owner=identity, opacity=row['opacity']),
                         text_area=dict(left=x + 22, top=y + 22, right=x + 258, bottom=y + 188),
                         text_boxes={'element-label': box(x + 22, y + 22, 236, 64),
                                     'element-detail': box(x + 22, y + 92, 236, 48),
                                     'state-label': box(x + 56, y + 158, 54, 28),
                                     'state-symbol': box(x + 22, y + 160, 24, 24)})
        else:
            state.update(state=None, stateLabel=None, stateSymbol=None, statePath=None, stateOpacities={},
                         docking_bay=None, text_area=dict(left=x, top=y, right=x + width, bottom=y + height),
                         text_boxes={'element-label': box(x + 40, y + 9, 54, 26)})
        actual[identity] = state
    relations, labels = {}, []
    for identity, row in expected['relations'].items():
        relations[identity] = {key: row[key] for key in ('opacity', 'strokeDashoffset', 'labelOpacity', 'markerOpacity')}
        relations[identity].update(length=row['relation_length'], pathD=row['path'], baseCount=0,
                                  sourcePort=dict(zip(('x', 'y'), row['source_port'])),
                                  targetPort=dict(zip(('x', 'y'), row['target_port'])),
                                  middlePoint=dict(zip(('x', 'y'), _path_position(row['relation_points'], .5))))
        x, y, width, height = row['label_box']
        labels.append(box(x, y, width, height, id=identity, text=row['label'], opacity=row['labelOpacity'],
                          ink=dict(x=x + 4, y=y + 4, width=width - 8, height=height - 8)))
    headers = {}
    for key, row in expected['headers'].items():
        checkpoint = segment['initial_checkpoint'] if key == 'previous' else segment['final_checkpoint']
        headers[key] = dict(opacity=row['opacity'], title=box(64, 44, 1152, 47, text=checkpoint['title']),
                            takeaway=box(64, 664, 1152, 24, text=checkpoint['takeaway']))
    return dict(time=time, actual_time=time, elements=actual, relations=relations,
                layout=dict(title=box(64, 44, 1152, 47), takeaway=box(64, 664, 1152, 24),
                            headers=headers, relations=labels, fontReady=True),
                continuity={key: deepcopy(segment[key]) for key in ('scene_id', 'index', 'entity_map', 'copy_lineage')},
                frame_sha256=sha256(dict(time=round(time, 9), segment=segment['sha256'])))


def fixture(*, copy=False, index=1, scene_inputs=None):
    scenes = inputs() if scene_inputs is None else scene_inputs
    plan = build_process_continuity_plan(scenes)
    if copy:
        plan['links'] = [dict(link, mode='copy') for link in plan['links'] if link['to_element_id'] == 'request']
    resolution = resolve_process_continuity(scenes, plan)
    segment = resolution['segments'][index]
    files = compile_process_continuity_project(scenes[index], segment)
    wrapper, timeline = [json.loads(files[name]) for name in ('plan.json', 'timeline.json')]
    windows = []
    for action in timeline['actions']:
        times = dict(before=max(0, action['start'] - 1 / FPS), middle=action['start'] + action['duration'] / 2,
                     after=min(timeline['duration_seconds'] - 1 / FPS, action['start'] + action['duration'] + 1 / FPS))
        windows.append(dict(ordinal=action['ordinal'], **{key: sample(segment, time) for key, time in times.items()}))
    initial, final = sample(segment, 0), sample(segment, timeline['duration_seconds'] - 1 / FPS)
    boundary = dict(initial=initial, final=final, repeated_initial=deepcopy(initial))
    if index:
        boundary.update(transition_middle=sample(segment, .375), transition_end=sample(segment, .6))
    observations = dict(schema='video.motion-observations.v1', seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24,
                        timeline_sha256=sha256(timeline), windows=windows, continuity_boundary=boundary,
                        seek_sequence=dict(schema='video.process-continuity-seek-sequence.v1',
                                           samples=[sample(segment, time) for time in seek_sequence_times(timeline)]),
                        reverse_seek=dict(first_sha256=initial['frame_sha256'], last_sha256=final['frame_sha256'],
                                          repeated_first_sha256=initial['frame_sha256'], repeat_state_equal=True))
    return wrapper, timeline, observations


@pytest.mark.parametrize('copy,index', [(False, 0), (False, 1), (True, 1)])
def test_process_continuity_review_accepts_complete_synthetic_evidence_without_quality_approval(copy, index):
    report = verify_process_continuity_observations(*fixture(copy=copy, index=index))
    assert report['technical_status'] == report['continuity']['boundary_status'] == 'passed'
    assert report['timing_review_status'] == 'pending'
    assert report['visual_comprehension_status'] == report['voice_review_status'] == 'pending'


def empty_detail_fixture(index=1):
    scenes = inputs()
    for scene in scenes:
        for element in scene['motion']['plan']['elements']:
            element['detail'] = ''
        scene['motion']['timeline'] = resolve_process_timeline(
            scene['motion']['plan'], scene['motion']['beats'], scene['motion']['alignment'],
            compiler_version='motion-process-compiler-2')
    return fixture(scene_inputs=scenes, index=index)


@pytest.mark.parametrize('index', [0, 1])
def test_empty_card_detail_retains_measured_region_across_continuity_scenes(index):
    # The actual card DOM retains .element-detail for later in-place updates,
    # even when these scenes have no detail text. Empty text is not a reason to
    # discard its measured region or its clearance/overflow checks.
    report = verify_process_continuity_observations(*empty_detail_fixture(index))
    assert report['technical_status'] == 'passed'


@pytest.mark.parametrize('failure,code', [
    ('missing', 'process_text_bounds_missing'),
    ('extra', 'process_text_bounds_missing'),
    ('outside', 'process_text_overflow'),
    ('overflow', 'process_text_overflow'),
])
def test_empty_card_detail_still_requires_exact_observed_bounds_and_clearance(failure, code):
    wrapper, timeline, observations = empty_detail_fixture()
    card_id = next(identity for identity, row in wrapper['segment']['initial_states'].items()
                   if row['kind'] == 'card')
    state = observations['continuity_boundary']['initial']['elements'][card_id]
    assert state['detail'] == ''
    boxes = state['text_boxes']
    if failure == 'missing':
        boxes.pop('element-detail')
    elif failure == 'extra':
        boxes['unexpected-detail'] = deepcopy(boxes['element-detail'])
    elif failure == 'outside':
        boxes['element-detail']['y'] = state['text_area']['bottom']
    else:
        boxes['element-detail']['scrollHeight'] += 1
    with pytest.raises(ServiceError, match=code):
        verify_process_continuity_observations(wrapper, timeline, observations)


@pytest.mark.parametrize('repeat', [False, True])
def test_three_scene_process_review_preserves_earlier_identity_states_and_archived_originals(repeat):
    from tests.video_workbench.test_process_continuity_core import process_scenes

    wrapper, timeline, observations = fixture(scene_inputs=process_scenes(count=3, repeat=repeat), index=2)
    report = verify_process_continuity_observations(wrapper, timeline, observations)
    assert report['technical_status'] == 'passed'
    archived = [identity for identity, row in wrapper['segment']['final_checkpoint']['objects'].items()
                if row['slot'].startswith('archive_')]
    assert len(archived) == (2 if repeat else 0)
    if repeat:
        first = next(identity for identity in archived if wrapper['segment']['initial_states'][identity]['slot'] == 'archive_0')
        observations['continuity_boundary']['final']['elements'][first]['opacity'] = 0
        with pytest.raises(ServiceError):
            verify_process_continuity_observations(wrapper, timeline, observations)


@pytest.mark.parametrize('failure', ['missing_boundary', 'missing_sequence', 'wrong_seek', 'reset_state', 'wrong_glyph',
        'slot_reset', 'identity', 'metadata', 'transfer_position', 'transfer_hidden', 'copy_hidden', 'bay_owner',
        'path_float', 'future_line', 'text_crop', 'text_in_path', 'frozen', 'sequence_order', 'sequence_reset'])
def test_process_continuity_review_rejects_tampered_observations(failure):
    wrapper, timeline, observations = fixture(copy=failure == 'copy_hidden')
    sample_ = observations['continuity_boundary']['initial']
    card_id = next(identity for identity, row in wrapper['segment']['initial_states'].items() if row['kind'] == 'card')
    token_id = wrapper['segment']['entity_map']['request']
    transfer = next(row for row in timeline['actions'] if row['process_kind'] == 'transfer')
    transfer_window = next(row for row in observations['windows'] if row['ordinal'] == transfer['ordinal'])
    if failure == 'missing_boundary':
        observations.pop('continuity_boundary')
    elif failure == 'missing_sequence':
        observations.pop('seek_sequence')
    elif failure == 'wrong_seek':
        sample_['actual_time'] += .1
    elif failure == 'reset_state':
        sample_['elements'][wrapper['segment']['entity_map']['queue']]['state'] = 'idle'
    elif failure == 'wrong_glyph':
        sample_['elements'][card_id]['statePath'] = 'M0 0'
    elif failure == 'slot_reset':
        sample_['elements'][token_id]['slot'] = 'token_source'
    elif failure == 'identity':
        sample_['elements'][token_id]['copied_from'] = 'wrong'
    elif failure == 'metadata':
        sample_['continuity']['entity_map']['request'] = 'wrong'
    elif failure == 'transfer_position':
        transfer_window['middle']['elements'][token_id]['x'] += 30
    elif failure == 'transfer_hidden':
        transfer_window['middle']['elements'][token_id]['opacity'] = 0
    elif failure == 'copy_hidden':
        original = wrapper['segment']['copy_lineage'][0]['source']
        observations['continuity_boundary']['final']['elements'][original]['opacity'] = 0
    elif failure == 'bay_owner':
        sample_['elements'][card_id]['docking_bay']['owner'] = 'wrong'
    elif failure == 'path_float':
        next(iter(sample_['relations'].values()))['sourcePort']['y'] -= 124
    elif failure == 'future_line':
        relation_id = transfer['relation_id']
        sample_['relations'][relation_id]['opacity'] = 1
    elif failure == 'text_crop':
        sample_['elements'][card_id]['text_boxes']['element-label']['scrollHeight'] += 1
    elif failure == 'text_in_path':
        sample_['elements'][card_id]['text_boxes']['element-label']['y'] = 470
    elif failure == 'frozen':
        for row in observations['windows']:
            for key in ('before', 'middle', 'after'):
                row[key]['frame_sha256'] = 'a' * 64
    elif failure == 'sequence_order':
        observations['seek_sequence']['samples'].reverse()
    else:
        observations['seek_sequence']['samples'][-1]['elements'][token_id]['opacity'] = 0
    with pytest.raises(ServiceError):
        verify_process_continuity_observations(wrapper, timeline, observations)


@pytest.mark.parametrize('failure', ['timeline_sha', 'timeline_event', 'bad_compiler', 'nonfinite', 'missing_state', 'malformed'])
def test_process_continuity_review_rejects_timeline_binding_and_malformed_evidence(failure):
    wrapper, timeline, observations = fixture()
    if failure == 'timeline_sha':
        observations['timeline_sha256'] = 'f' * 64
    elif failure == 'timeline_event':
        timeline['actions'][0]['start'] += .1
        observations['timeline_sha256'] = sha256(timeline)
    elif failure == 'bad_compiler':
        timeline['compiler'] = 'motion-continuity-compiler-2'
    elif failure == 'nonfinite':
        observations['continuity_boundary']['initial']['actual_time'] = math.nan
    elif failure == 'missing_state':
        next(iter(observations['continuity_boundary']['initial']['elements'].values())).pop('stateOpacities')
    else:
        observations['continuity_boundary']['initial']['elements'] = []
    with pytest.raises(ServiceError):
        verify_process_continuity_observations(wrapper, timeline, observations)


@pytest.mark.parametrize('failure', ['false_flag', 'reverse_frame', 'sequence_endpoint_frame', 'repeat_state'])
def test_process_continuity_reverse_evidence_requires_actual_repeated_frames_and_states(failure):
    wrapper, timeline, observations = fixture()
    if failure == 'false_flag':
        observations['reverse_seek']['repeat_state_equal'] = False
    elif failure == 'reverse_frame':
        observations['continuity_boundary']['repeated_initial']['frame_sha256'] = 'f' * 64
    elif failure == 'sequence_endpoint_frame':
        observations['seek_sequence']['samples'][-1]['frame_sha256'] = 'f' * 64
    else:
        observations['continuity_boundary']['repeated_initial']['continuity']['copy_lineage'].append(dict(source='x', target='y'))
    with pytest.raises(ServiceError):
        verify_process_continuity_observations(wrapper, timeline, observations)


def production_artifacts(*, copy=False, index=1):
    """Wrap synthetic DOM in the exact receipts/screenshots production admits."""
    from tests.video_workbench.test_motion_snapshot import RUNTIME

    wrapper, _, observations = fixture(copy=copy, index=index)
    files = compile_process_continuity_project(inputs()[index], wrapper['segment'], runtime_fingerprint=RUNTIME)
    observations['timeline_sha256'] = hashlib.sha256(files['timeline.json']).hexdigest()
    entries = {**files, 'output.mp4': b'synthetic-process-continuity-media'}
    entries['experiment.json'] = canonical_bytes(dict(status='pending_validation', stages={'render': 0, 'observe': 0}))

    def screenshot(name, sample):
        # Same states at the same time intentionally produce identical bytes,
        # including independent reverse/boundary/nonmonotonic observations.
        raw = f"synthetic-frame-{sample['time']:.9f}".encode()
        entries[f'observations/{name}.jpg'] = raw
        sample['frame_sha256'] = hashlib.sha256(raw).hexdigest()

    for row in observations['windows']:
        for key in ('before', 'middle', 'after'):
            screenshot(f"a{row['ordinal']}-{key}", row[key])
    boundary = observations['continuity_boundary']
    for name, key, field in [('seek-first', 'initial', 'first_sha256'),
                             ('seek-last', 'final', 'last_sha256'),
                             ('seek-repeat', 'repeated_initial', 'repeated_first_sha256')]:
        screenshot(name, boundary[key])
        observations['reverse_seek'][field] = boundary[key]['frame_sha256']
    for key in ('transition_middle', 'transition_end'):
        if key in boundary:
            screenshot('continuity-' + key.replace('_', '-'), boundary[key])
    for sequence_index, sample_ in enumerate(observations['seek_sequence']['samples']):
        screenshot(f'continuity-sequence-{sequence_index}', sample_)
    entries['observations.json'] = canonical_bytes(observations)
    for name, status in [('render-receipt.json', 'encoded_pending_validation'),
                         ('observe-receipt.json', 'observed_pending_validation')]:
        entries[name] = canonical_bytes(dict(schema='video.motion-render-receipt.v1', status=status,
            input_manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest(),
            timeline_sha256=hashlib.sha256(files['timeline.json']).hexdigest(),
            runtime={key: value for key, value in RUNTIME.items() if key != 'image_digest'},
            runtime_binding=dict(status='matched', image_digest=RUNTIME['image_digest']),
            quality='draft', cli_quality='draft', decode_verified=True,
            output_sha256=hashlib.sha256(entries['output.mp4']).hexdigest()))
    return entries, files, RUNTIME


@pytest.mark.parametrize('copy,index', [(False, 0), (False, 1), (True, 1)])
def test_production_artifact_entry_admits_complete_process_continuity_evidence(copy, index):
    from video_workbench.motion_render_k8s import validate_artifacts

    entries, files, runtime = production_artifacts(copy=copy, index=index)
    result = validate_artifacts(entries, files, runtime, 'draft')
    report = result['technical_review']
    assert report['technical_status'] == report['continuity']['boundary_status'] == 'passed'
    assert report['continuity']['seek_sequence_samples'] == len(json.loads(entries['observations.json'])['seek_sequence']['samples'])
    assert report['timing_review_status'] == report['visual_comprehension_status'] == report['voice_review_status'] == 'pending'
    assert result['media_sha256'] == hashlib.sha256(entries['output.mp4']).hexdigest()


@pytest.mark.parametrize('failure,code', [
    ('sequence_schema', 'motion_observation_sequence_invalid'),
    ('sequence_list', 'motion_observation_sequence_invalid'),
    ('sequence_screenshot', 'motion_observation_frame_mismatch'),
    ('sequence_declared_sha', 'motion_observation_frame_mismatch'),
    ('sequence_missing_screenshot', 'motion_artifacts_invalid'),
    ('transition_screenshot', 'motion_observation_frame_mismatch'),
    ('receipt_schema', 'motion_runtime_receipt_mismatch'),
    ('receipt_timeline', 'motion_runtime_receipt_mismatch'),
    ('receipt_runtime', 'motion_runtime_receipt_mismatch'),
    ('media_bytes', 'motion_media_receipt_invalid'),
])
def test_production_artifact_entry_rejects_sequence_screenshot_and_receipt_tampering(failure, code):
    from video_workbench.motion_render_k8s import validate_artifacts

    entries, files, runtime = production_artifacts()
    observations = json.loads(entries['observations.json'])
    if failure == 'sequence_schema':
        observations['seek_sequence']['schema'] = 'video.untrusted-sequence.v1'
    elif failure == 'sequence_list':
        observations['seek_sequence'] = observations['seek_sequence']['samples']
    elif failure == 'sequence_screenshot':
        entries['observations/continuity-sequence-0.jpg'] = b'tampered-sequence-frame'
    elif failure == 'sequence_declared_sha':
        observations['seek_sequence']['samples'][0]['frame_sha256'] = '0' * 64
    elif failure == 'sequence_missing_screenshot':
        entries.pop('observations/continuity-sequence-0.jpg')
    elif failure == 'transition_screenshot':
        entries['observations/continuity-transition-middle.jpg'] = b'tampered-boundary-frame'
    elif failure.startswith('receipt_'):
        receipt = json.loads(entries['observe-receipt.json'])
        if failure == 'receipt_schema':
            receipt['schema'] = 'video.untrusted-receipt.v1'
        elif failure == 'receipt_timeline':
            receipt['timeline_sha256'] = '0' * 64
        else:
            receipt['runtime_binding']['image_digest'] = 'sha256:' + '0' * 64
        entries['observe-receipt.json'] = canonical_bytes(receipt)
    else:
        entries['output.mp4'] = b'tampered-media'
    entries['observations.json'] = canonical_bytes(observations)
    with pytest.raises(ServiceError, match=code):
        validate_artifacts(entries, files, runtime, 'draft')
