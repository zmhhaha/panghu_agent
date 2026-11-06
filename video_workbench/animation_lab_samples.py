"""Fixed developer evaluation cases compiled by the real animation compilers.

This module has no filesystem, network, Agent or TTS operations. Historical
variants are comparison baselines, not a change to production admission. Audio
is explicitly synthetic silence and cannot establish narration quality/timing.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import io
import json
import wave

from .examples.build_process_sample import SAMPLE_TEXT, process_plan
from .motion import FPS, compile_project, estimated_alignment, example_plan, make_beats, sha256
from .motion_process import compile_process_project
from .process_continuity import (build_process_continuity_plan,
                                 compile_process_continuity_project, resolve_process_continuity)


FIXTURE_VERSION = 'animation-lab-fixtures-1'
MODULES = (
    dict(id='diagram', title='框图与信息图', status='available'),
    dict(id='process', title='二维过程动画', status='available'),
    dict(id='continuity', title='跨镜衔接', status='available'),
    dict(id='canvas', title='Canvas 2D', status='planned',
         note='尚未实现；后续由具体题材建立样例，不以 SVG 冒充 Canvas 能力。'),
    dict(id='three', title='Three.js 三维动画', status='planned',
         note='尚未实现；后续在明确三维需求后建立样例。'),
)


def _variant(identity, label, compiler, *, role='candidate', status='available'):
    return dict(id=identity, label=label, compiler=compiler, role=role, status=status)


_CASES = (
    dict(id='diagram-connections', module='diagram', title='框图：节点退出后的连线与结论',
         description='相同方案下比较布局、连线显隐及末帧信息层次。',
         checks=['文字是否易读', '线条是否跟随端点隐藏', '末帧结论是否清楚'],
         variants=[_variant('baseline', '历史框图 compiler 5', 'motion-compiler-5', role='baseline'),
                   _variant('candidate', '当前框图 compiler 6', 'motion-compiler-6')]),
    dict(id='diagram-long-label', module='diagram', title='框图：长请求名称的可读性',
         description='保持内容和动作一致，检查长标签与移动对象尺寸。',
         checks=['长标签是否完整', '移动途中是否遮挡', '对象尺寸是否一致'],
         variants=[_variant('baseline', '历史框图 compiler 4', 'motion-compiler-4', role='baseline'),
                   _variant('candidate', '当前框图 compiler 6', 'motion-compiler-6')]),
    dict(id='process-retry', module='process', title='过程：同一请求失败后重试',
         description='比较卡片、端口、状态反馈和请求传递路径。',
         checks=['连线是否接到节点', '请求移动是否清楚', '失败重试是否容易理解'],
         variants=[_variant('baseline', '历史本地实验 compiler 1', 'motion-process-compiler-1',
                            role='baseline', status='historical_local_experiment'),
                   _variant('candidate', '当前过程 compiler 2', 'motion-process-compiler-2')]),
    dict(id='continuity-retain', module='continuity', title='跨镜：同一请求继续处理',
         description='使用完全相同的两镜输入，对比独立切镜与对象连续承接。',
         checks=['对象身份是否连续', '镜头边界是否跳变', '状态是否得到保留'],
         variants=[_variant('baseline', '两镜独立播放', 'motion-process-compiler-2', role='baseline'),
                   _variant('candidate', '连续承接 retain', 'motion-process-continuity-compiler-1')]),
    dict(id='continuity-copy', module='continuity', title='跨镜：复制新请求并保留原件',
         description='同源两镜对比；连续版本显式保留原件并显示复制后的新对象。',
         checks=['原件是否仍可辨认', '复制关系是否清楚', '衔接速度是否合适'],
         variants=[_variant('baseline', '两镜独立播放', 'motion-process-compiler-2', role='baseline'),
                   _variant('candidate', '复制并保留 copy', 'motion-process-continuity-compiler-1')]),
)


def _case(case_id):
    if type(case_id) is not str:
        raise ValueError('unknown_animation_lab_case')
    value = next((row for row in _CASES if row['id'] == case_id), None)
    if value is None:
        raise ValueError('unknown_animation_lab_case')
    return value


def _silence(seconds):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0' * (int(seconds * 24000) * 2))
    return output.getvalue()


def _diagram_inputs(long_label):
    text = '首先提交请求。然后进入任务队列。最后由执行节点处理。队列退出画面，结论保留。'
    beats = make_beats(text)
    plan = example_plan(beats)
    if long_label:
        next(row for row in plan['elements'] if row['kind'] == 'token')['label'] = '文件不存在：相应结果'
    else:
        plan['elements'].append(dict(id='summary', kind='card', slot='note',
                                    label='队列完成交接，执行节点继续工作', detail='保留结果，不保留无意义的线条'))
        last = beats['segments'][-1]['id']
        plan['actions'].extend([
            dict(beat_id=last, target='node1', kind='hide', placement='start', pace='normal', to_slot=None),
            dict(beat_id=last, target='summary', kind='reveal', placement='middle', pace='normal', to_slot=None),
        ])
    audio = _silence(18)
    alignment = estimated_alignment(beats, sha256(audio), 18)
    return dict(plan=plan, beats=beats, alignment=alignment), [audio]


def _continuity_inputs(copy):
    audio = _silence(9)
    scenes = []
    for index in range(2):
        start, end = ('source', 'queue') if index == 0 or copy else ('queue', 'worker')
        text = ('请求进入处理队列。执行节点开始工作。保留同一个请求及处理状态。' if index == 0 else
                '新的一镜继续说明。请求交给下一环节。处理状态继续保留。')
        beats = make_beats(text)
        ids = [row['id'] for row in beats['segments']]
        plan = dict(schema='video.process-plan.v1', template='flow',
            title='请求先进入队列' if index == 0 else ('复制一份新请求' if copy else '同一请求继续执行'),
            takeaway='观察请求身份与处理状态的延续。',
            elements=[dict(id=slot, kind='card', slot=slot, label=label, detail='同一处理环节')
                      for slot, label in [('source', '入口'), ('queue', '队列'), ('worker', '执行器')]] +
                     [dict(id='request', kind='token', slot='token_'+start, label='请求 01', detail='')],
            relations=[dict(id='route', source=start, target=end, label='传递')],
            actions=[dict(kind='draw', target='route', beat_id=ids[0], placement='start', pace='quick'),
                     dict(kind='reveal', target='request', beat_id=ids[0], placement='middle', pace='quick'),
                     dict(kind='transfer', target='request', relation_id='route', beat_id=ids[1],
                          placement='start', pace='slow'),
                     dict(kind='set_state', target='worker', state='running', beat_id=ids[1],
                          placement='end', pace='normal')])
        alignment = estimated_alignment(beats, sha256(audio), 9)
        scenes.append(dict(id=f'lab-scene-{index+1}', title=plan['title'],
            audio=dict(id=f'lab-silence-{index+1}', sha256=sha256(audio)),
            motion=dict(id=f'lab-motion-{index+1}', plan=plan, beats=beats, alignment=alignment,
                        compiler_version='motion-process-compiler-2'),
            timing=dict(leading_seconds=0, trailing_seconds=0)))
    return dict(scenes=scenes), [audio, audio]


def _inputs(case_id):
    if case_id.startswith('diagram-'):
        return _diagram_inputs(case_id == 'diagram-long-label')
    if case_id == 'process-retry':
        audio = _silence(24)
        beats = make_beats(SAMPLE_TEXT)
        return dict(plan=process_plan(beats), beats=beats,
                    alignment=estimated_alignment(beats, sha256(audio), 24)), [audio]
    return _continuity_inputs(case_id == 'continuity-copy')


def _checkpoints(timeline):
    duration = timeline['duration_seconds']
    points = [dict(label='首帧', time=0)]
    if timeline.get('compiler') == 'motion-process-continuity-compiler-1':
        points.append(dict(label='跨镜承接完成', time=min(.6, duration-1/FPS)))
    for index, row in enumerate(timeline['actions']):
        if row.get('kind') not in {'move', 'hide', 'transfer', 'set_state'}:
            continue
        title = f'{index+1} · {row.get("target", "")} · {row["kind"]}'
        for label, time in [('动作前', row['start']-1/FPS),
                            ('途中', row['start']+row['duration']/2),
                            ('完成', row['start']+row['duration']+1/FPS)]:
            points.append(dict(label=title+' · '+label,
                               time=max(0, min(duration-1/FPS, time))))
    points.append(dict(label='末帧', time=max(0, duration-1/FPS)))
    return points


def _clip(identity, files, audio):
    timeline = json.loads(files['timeline.json'])
    return dict(id=identity, duration_seconds=timeline['duration_seconds'],
                timeline=timeline, checkpoints=_checkpoints(timeline),
                files={**files, 'audio.wav': audio})


def _compile_variant(case_id, variant, inputs, audio):
    compiler = variant['compiler']
    if case_id.startswith('diagram-'):
        clips = [_clip('scene-1', compile_project(**inputs, compiler_version=compiler), audio[0])]
    elif case_id == 'process-retry':
        clips = [_clip('scene-1', compile_process_project(**inputs, compiler_version=compiler), audio[0])]
    else:
        scenes = inputs['scenes']
        if variant['id'] == 'baseline':
            clips = [_clip(scene['id'], compile_process_project(scene['motion']['plan'],
                      scene['motion']['beats'], scene['motion']['alignment'],
                      compiler_version=compiler), raw) for scene, raw in zip(scenes, audio)]
        else:
            resolution = resolve_process_continuity(scenes, build_process_continuity_plan(scenes))
            clips = [_clip(scene['id'], compile_process_continuity_project(scene, segment), raw)
                     for scene, segment, raw in zip(scenes, resolution['segments'], audio)]
    hashes = [dict(id=clip['id'], files={name: sha256(raw) for name, raw in sorted(clip['files'].items())})
              for clip in clips]
    return dict(case_id=case_id, variant_id=variant['id'], compiler=compiler, clips=clips,
                artifact_sha256=sha256(hashes), duration_seconds=sum(c['duration_seconds'] for c in clips))


@lru_cache(maxsize=8)
def _compiled_case(case_id):
    case = _case(case_id)
    inputs, audio = _inputs(case_id)
    source = dict(fixture_version=FIXTURE_VERSION, case_id=case_id, inputs=inputs,
                  audio_kind='synthetic_silence_not_narration')
    input_sha = sha256(source)
    variants = [_compile_variant(case_id, row, inputs, audio) for row in case['variants']]
    identity = sha256(dict(input_sha256=input_sha,
        variants=[dict(id=row['variant_id'], artifact_sha256=row['artifact_sha256']) for row in variants]))
    for row in variants:
        row.update(schema='video.animation-lab-sample.v1', fixture_version=FIXTURE_VERSION,
                   identity=identity, input_sha256=input_sha, inputs=source,
                   audio_kind='synthetic_silence_not_narration', alignment_method='estimated',
                   timing_review='pending', voice_naturalness='not_evaluated',
                   agent_calls=0, tts_calls=0, production_write=False)
    return variants


def catalog():
    """Return isolated JSON metadata, including exact input/output comparison IDs."""
    result = []
    for definition in _CASES:
        row = deepcopy(definition)
        variants = _compiled_case(row['id'])
        row.update(identity=variants[0]['identity'], input_sha256=variants[0]['input_sha256'],
                   fixture_version=FIXTURE_VERSION, status='available',
                   audio_kind='synthetic_silence_not_narration', agent_calls=0, tts_calls=0)
        for spec, compiled in zip(row['variants'], variants):
            spec.update(artifact_sha256=compiled['artifact_sha256'],
                        duration_seconds=compiled['duration_seconds'], clip_count=len(compiled['clips']))
        result.append(row)
    return result


def compile_sample(case_id, variant_id):
    """Return real project bytes; an HTTP adapter supplies fixed runtime assets."""
    case = _case(case_id)
    if type(variant_id) is not str or variant_id not in {row['id'] for row in case['variants']}:
        raise ValueError('unknown_animation_lab_variant')
    return deepcopy(next(row for row in _compiled_case(case_id) if row['variant_id'] == variant_id))
