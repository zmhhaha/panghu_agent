"""Build a local, silent process-animation fixture without model calls.

The 24-second silent WAV is a timing fixture, not a recording of the script.
Its estimated timeline cannot establish narration accuracy or human approval.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import wave

from agent_service.profiles.video_process_contract import validate_process_plan
from video_workbench.motion import canonical, estimated_alignment, make_beats
from video_workbench.motion_process import (
    PROCESS_COMPILER_VERSION, PROCESS_COMPILER_VERSIONS, compile_process_project,
)


SAMPLE_SECONDS = 24
SAMPLE_TEXT = (
    '一份请求先进入队列。'
    '队列把请求交给执行节点。'
    '这次执行失败，请求返回队列。'
    '同一个请求重新执行，而不是再造一份。'
    '执行成功，结果保留下来。'
    '失败和重试改变的是处理状态，请求本身没有丢失。'
)


def process_plan(beats):
    first, dispatch, failure, retry, complete, _hold = [row['id'] for row in beats['segments']]

    def action(beat, target, kind, placement='start', pace='normal', **options):
        return dict(beat_id=beat, target=target, kind=kind,
                    placement=placement, pace=pace, **options)

    return validate_process_plan(dict(
        schema='video.process-plan.v1', template='flow',
        title='执行失败了，请求会丢失吗？',
        takeaway='同一个请求返回队列重试；处理状态改变，请求身份不变。',
        elements=[
            dict(id='source', kind='card', slot='source', label='提交请求', detail='只创建一次'),
            dict(id='queue', kind='card', slot='queue', label='任务队列', detail='等待与重新分配'),
            dict(id='worker', kind='card', slot='worker', label='执行节点', detail='执行失败后可以重试'),
            dict(id='request', kind='token', slot='token_source', label='请求 01', detail=''),
        ],
        relations=[
            dict(id='source_queue', source='source', target='queue', label='进入队列'),
            dict(id='queue_worker', source='queue', target='worker', label='交给执行节点'),
            dict(id='worker_queue', source='worker', target='queue', label='失败后返回'),
        ],
        actions=[
            action(first, 'source', 'reveal'),
            action(first, 'queue', 'reveal'),
            action(first, 'worker', 'reveal'),
            action(first, 'request', 'reveal', 'middle', 'quick'),
            action(first, 'source_queue', 'draw', 'middle'),
            action(first, 'request', 'transfer', 'end', relation_id='source_queue'),
            action(dispatch, 'queue', 'set_state', state='waiting'),
            action(dispatch, 'queue_worker', 'draw'),
            action(dispatch, 'request', 'transfer', 'middle', relation_id='queue_worker'),
            action(dispatch, 'worker', 'set_state', 'end', state='running'),
            action(failure, 'worker', 'set_state', state='failed'),
            action(failure, 'worker_queue', 'draw'),
            action(failure, 'request', 'transfer', 'middle', relation_id='worker_queue'),
            action(retry, 'request', 'transfer', relation_id='queue_worker'),
            action(retry, 'worker', 'set_state', 'middle', state='running'),
            action(complete, 'worker', 'set_state', 'middle', state='completed'),
        ]), beats)


def silent_fixture():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\0' * (SAMPLE_SECONDS * 24000 * 2))
    return output.getvalue()


def preview_page(timeline):
    """A visible local seek UI; the compiled composition stays unmodified."""
    checkpoints = [dict(label='首帧', time=0)]
    transfer, state = 0, 0
    for row in timeline['actions']:
        if row['kind'] not in {'transfer', 'set_state'}:
            continue
        if row['kind'] == 'transfer':
            transfer += 1
            title = f'传递 {transfer}'
        else:
            state += 1
            title = f'状态 {state}'
        checkpoints += [
            dict(label=title + ' · 动作前', time=max(0, row['start'] - 1 / 24)),
            dict(label=title + ' · 途中', time=row['start'] + row['duration'] / 2),
            dict(label=title + ' · 完成', time=row['start'] + row['duration'] + 1 / 24),
        ]
    checkpoints.append(dict(label='末帧', time=timeline['duration_seconds'] - 1 / 24))
    payload = json.dumps(dict(duration=timeline['duration_seconds'], checkpoints=checkpoints),
                         ensure_ascii=False).replace('<', '\\u003c')
    return ('''<!doctype html><html lang="zh-CN"><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>二维过程演示 · 本地审阅</title><style>
*{box-sizing:border-box}body{margin:0;background:#0c1523;color:#edf2f7;font:16px system-ui,sans-serif}
main{max-width:1320px;margin:auto;padding:16px}h1{font-size:21px;margin:0 0 8px}
p{color:#b3c3d6;margin:0 0 12px;line-height:1.6}.viewport{position:relative;overflow:hidden;background:#0c1523}
iframe{border:0;width:1280px;height:720px;transform-origin:top left;display:block}
.controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}
button,select{font:inherit;padding:7px 10px}input{flex:1;min-width:180px}output{font-variant-numeric:tabular-nums}
</style><main><h1>同一个请求：进入队列 → 执行失败 → 返回重试 → 完成</h1>
<p>无旁白技术样例。静音 WAV 仅用于估算时间；未验证配音同步，也未作人工效果核准。</p>
<div class="viewport"><iframe title="二维过程画面" src="index.html"></iframe></div>
<div class="controls"><button id="play">播放</button><button id="reset">回到首帧</button>
<label for="checkpoint">检查位置</label><select id="checkpoint"></select>
<input id="seek" aria-label="预览时间" type="range" min="0" step="0.001" value="0">
<output id="time">0.000 秒</output></div></main><script>
'use strict';const data=__DATA__,frame=document.querySelector('iframe'),seek=document.querySelector('#seek');
let playing=false,current=0,last=0;
const player=()=>frame.contentWindow.__timelines?.['video-motion'];
function show(t){current=Math.max(0,Math.min(data.duration,t));player()?.seek(current,false);
 seek.value=String(current);document.querySelector('#time').textContent=current.toFixed(3)+' 秒';}
function stop(){playing=false;document.querySelector('#play').textContent='播放';}
function fit(){const v=document.querySelector('.viewport'),scale=v.clientWidth/1280;
 frame.style.transform='scale('+scale+')';v.style.height=(720*scale)+'px';}
seek.max=String(data.duration);seek.addEventListener('input',()=>{stop();show(Number(seek.value));});
const menu=document.querySelector('#checkpoint');for(const row of data.checkpoints){const option=document.createElement('option');
 option.value=String(row.time);option.textContent=row.label;menu.append(option);}
menu.addEventListener('change',()=>{stop();show(Number(menu.value));});
document.querySelector('#reset').addEventListener('click',()=>{stop();menu.selectedIndex=0;show(0);});
document.querySelector('#play').addEventListener('click',()=>{if(playing){stop();return;}if(!player())return;
 if(current>=data.duration)show(0);playing=true;last=performance.now();document.querySelector('#play').textContent='暂停';});
function tick(now){if(playing){show(current+(now-last)/1000);if(current>=data.duration)stop();}last=now;requestAnimationFrame(tick);}
frame.addEventListener('load',async()=>{await frame.contentWindow.__videoMotion?.ready;fit();show(0);});
window.addEventListener('resize',fit);requestAnimationFrame(tick);
</script></html>'''.replace('__DATA__', payload)).encode('utf-8')


def build(output, *, compiler_version=PROCESS_COMPILER_VERSION):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output directory already exists; use a fresh directory')
    audio = silent_fixture()
    beats = make_beats(SAMPLE_TEXT)
    alignment = estimated_alignment(beats, hashlib.sha256(audio).hexdigest(), SAMPLE_SECONDS)
    files = compile_process_project(process_plan(beats), beats, alignment, compiler_version=compiler_version)
    timeline = json.loads(files['timeline.json'])
    files['audio.wav'] = audio
    files['preview.html'] = preview_page(timeline)
    receipt = dict(schema='video.process-sample.v1', compiler=timeline['compiler'],
                   audio_kind='synthetic_silence_not_narration', audio_seconds=SAMPLE_SECONDS,
                   duration_seconds=timeline['duration_seconds'], alignment='estimated',
                   timing_review='pending', visual_comprehension='pending_user_review',
                   voice_naturalness='not_evaluated', agent_calls=0, tts_calls=0,
                   production_integrated=False,
                   files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()})
    files['sample.json'] = canonical(receipt)
    output.mkdir(parents=True)
    for name, raw in files.items():
        (output / name).write_bytes(raw)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--compiler-version', choices=PROCESS_COMPILER_VERSIONS,
                        default=PROCESS_COMPILER_VERSION)
    options = parser.parse_args()
    result = build(options.output, compiler_version=options.compiler_version)
    print(json.dumps(dict(output=str(Path(options.output).absolute()),
                          compiler=result['compiler'], seconds=result['duration_seconds'],
                          audio_kind=result['audio_kind'], agent_calls=0, tts_calls=0)))
