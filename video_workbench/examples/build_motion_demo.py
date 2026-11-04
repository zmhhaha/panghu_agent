"""Build the controlled paper-log demonstration, reusing the complete speech.

No Agent or TTS is called. The output directory must not already exist.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import wave

from agent_service.profiles.video_motion_contract import DEMONSTRATION_TEMPLATES, validate_motion_plan
from video_workbench.motion import action_windows, canonical, estimated_alignment, make_beats
from video_workbench.motion_demo import compile_demo_project, resolve_demo_timeline
from video_workbench.examples.build_motion_samples import SPEECH_SHA256, SPOKEN_TEXT


def demonstration_plan(beats):
    first, records, projection, exclusion = [row['id'] for row in beats['segments']]

    def action(beat, target, kind, placement='start', pace='normal', to_slot=None):
        return dict(beat_id=beat, target=target, kind=kind, placement=placement,
                    pace=pace, to_slot=to_slot)

    answer = dict(id='answer', kind='record', slot='answer_center',
                  label='完整回答', detail='可以继续使用的正式回答')
    copied_answer = dict(answer, id='answer_copy', slot='answer_left')
    # The demonstration shows copying, so its two record texts must agree.
    assert (answer['label'], answer['detail']) == (copied_answer['label'], copied_answer['detail'])
    plan = dict(
        schema='video.motion-plan.v1', template='demonstration',
        title='两类记录，谁会带进下一轮？',
        takeaway='只比较完整回答与尝试：两者都留档，完整回答参与下一轮。',
        elements=[
            dict(id='log', kind='document', slot='log_center', label='会话日志', detail='工作记录 · 完整保留'),
            answer,
            dict(id='attempt', kind='attempt', slot='attempt_center', label='尝试记录', detail='失败 · 重试 · 取消'),
            dict(id='context', kind='terminal', slot='context_right', label='下一轮历史（局部）', detail='这里只演示两类记录的去向'),
            copied_answer,
            dict(id='kept', kind='label', slot='result_left', label='两类记录，都留下', detail=''),
            dict(id='included', kind='label', slot='result_right', label='完整回答，参与下一轮', detail=''),
        ],
        relations=[],
        actions=[
            action(first, 'log', 'reveal'),
            action(records, 'answer', 'reveal'),
            action(records, 'attempt', 'reveal', 'middle'),
            action(projection, 'log', 'move', pace='slow', to_slot='log_left'),
            action(projection, 'answer', 'move', pace='slow', to_slot='answer_left'),
            action(projection, 'attempt', 'move', pace='slow', to_slot='attempt_left'),
            action(projection, 'context', 'reveal', 'middle'),
            action(projection, 'answer_copy', 'reveal', 'middle', 'quick'),
            action(projection, 'answer_copy', 'move', 'end', 'slow', 'answer_right'),
            action(exclusion, 'kept', 'reveal'),
            action(exclusion, 'included', 'reveal', 'middle'),
        ])
    return validate_motion_plan(plan, beats, allowed_templates=DEMONSTRATION_TEMPLATES)


def build(output, speech_wav):
    output, speech_wav = Path(output).absolute(), Path(speech_wav)
    if output.exists():
        raise ValueError('Output directory already exists; use a fresh directory')
    if speech_wav.is_symlink() or not speech_wav.is_file() or speech_wav.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Expected the existing ordinary-file speech WAV')
    speech = speech_wav.read_bytes()
    if hashlib.sha256(speech).hexdigest() != SPEECH_SHA256:
        raise ValueError('Speech fixture SHA-256 does not match the existing recording')
    with wave.open(io.BytesIO(speech)) as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) != (1, 2, 24000, 432000):
            raise ValueError('Expected the complete 18-second PCM speech WAV')
    beats = make_beats(SPOKEN_TEXT)
    plan = demonstration_plan(beats)
    alignment = estimated_alignment(beats, SPEECH_SHA256, 18)
    options = dict(leading_seconds=3, trailing_seconds=3)
    files = compile_demo_project(plan, beats, alignment, **options)
    timeline = resolve_demo_timeline(plan, beats, alignment, **options)
    output.mkdir(parents=True)
    for name, data in files.items():
        (output / name).write_bytes(data)
    (output / 'audio.wav').write_bytes(speech)
    (output / 'observation-windows.json').write_bytes(canonical(action_windows(timeline)))
    receipt = dict(schema='video.motion-demo-sample.v1', project=json.loads(files['manifest.json']),
                   agent_calls=0, tts_calls=0, speech_reused=True, audio_seconds=18,
                   duration_seconds=24, alignment='estimated', review_status='pending',
                   note='Local controlled demonstration; rendering, user approval and speech naturalness remain unverified.')
    (output / 'sample.json').write_bytes(canonical(receipt))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--speech-wav', required=True)
    options = parser.parse_args()
    receipt = build(options.output, options.speech_wav)
    print(json.dumps({'output': str(Path(options.output).absolute()),
                      'duration_seconds': receipt['duration_seconds'], 'agent_calls': 0, 'tts_calls': 0}))
