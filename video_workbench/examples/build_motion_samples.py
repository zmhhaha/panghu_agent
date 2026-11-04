"""Build local, inspectable experiment inputs. Never calls an Agent or TTS.

Usage: python -m video_workbench.examples.build_motion_samples --output DIR
Add --speech-wav /path/to/the/existing/narration-sample.wav for the same-audio
24-second comparison. The output directory must not already exist.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import wave

from video_workbench.motion import (
    action_windows, canonical, compile_project, estimated_alignment,
    example_plan, make_beats, resolve_timeline,
)


SPOKEN_TEXT = ('你可以把会话日志想成一本工作记录。'
               '完整回答会留下来，失败、重试或取消的尝试也会留下来，方便查问题。'
               '下一轮交给模型时，会把完整回答整理进历史；这些尝试记录则不会加入。')
SPEECH_SHA256 = '3fa64b4a6800984b4b87719adca38a2eee533d19a5616d7a79305c08b566bce8'


def test_tone(seconds=15):
    """A quiet timing fixture, explicitly not a speech/naturalness sample."""
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        samples = []
        for index in range(int(seconds * 24000)):
            t = index / 24000
            # Three short pips separated by genuine silence; avoids pretending
            # that text-to-interval estimates were speech recognition.
            phase = t % 5
            envelope = max(0, min(1, phase / .02, (.2 - phase) / .02)) if phase < .2 else 0
            samples.append(struct.pack('<h', int(2000 * envelope * math.sin(2 * math.pi * 440 * t))))
        wav.writeframes(b''.join(samples))
    return output.getvalue()


def history_plan(beats):
    first, records, projection, exclusion = [row['id'] for row in beats['segments']]
    def action(beat, target, kind, placement='start', pace='normal', to_slot=None):
        return dict(beat_id=beat, target=target, kind=kind, placement=placement,
                    pace=pace, to_slot=to_slot)
    return {
        'schema': 'video.motion-plan.v1', 'template': 'focus',
        'title': '哪些记录，会进入模型历史？',
        'takeaway': '图中只比较两类记录；尝试保留在日志，不加入模型历史。',
        'elements': [
            dict(id='log', kind='card', slot='top', label='会话日志', detail='一本工作记录'),
            dict(id='answer', kind='card', slot='left', label='完整回答', detail='assistant/message'),
            dict(id='attempt', kind='card', slot='center', label='尝试记录', detail='失败 · 重试 · 取消'),
            dict(id='history', kind='card', slot='right', label='模型历史', detail='下一轮继续使用'),
            dict(id='note', kind='card', slot='note', label='尝试留在日志，不加入历史', detail='assistant/attempt'),
            dict(id='packet', kind='token', slot='token_left', label='回答', detail=''),
        ],
        'relations': [
            dict(id='answer_log', source='answer', target='log', label='保存'),
            dict(id='attempt_log', source='attempt', target='log', label='也保存'),
            dict(id='answer_history', source='answer', target='history', label='整理后加入'),
        ],
        'actions': [
            action(first, 'log', 'highlight'),
            action(records, 'answer', 'reveal'),
            action(records, 'answer_log', 'draw'),
            action(records, 'attempt', 'reveal', 'middle'),
            action(records, 'attempt_log', 'draw', 'end'),
            action(projection, 'history', 'reveal'),
            action(projection, 'answer_history', 'draw', 'middle'),
            action(projection, 'packet', 'reveal'),
            action(projection, 'packet', 'move', 'middle', 'slow', 'token_right'),
            action(exclusion, 'attempt', 'highlight'),
            action(exclusion, 'note', 'reveal'),
        ],
    }


def _write_project(destination, plan, beats, alignment, audio, *, animated=True, lead=0, tail=0):
    destination.mkdir()
    files = compile_project(plan, beats, alignment, animated=animated,
                            leading_seconds=lead, trailing_seconds=tail)
    for name, data in files.items():
        (destination / name).write_bytes(data)
    (destination / 'audio.wav').write_bytes(audio)
    timeline = resolve_timeline(plan, beats, alignment, leading_seconds=lead, trailing_seconds=tail)
    (destination / 'observation-windows.json').write_bytes(canonical(action_windows(timeline)))
    return json.loads(files['manifest.json'])


def build(output, speech_wav=None):
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Output directory already exists; use a fresh directory')
    # Validate speech input before writing anything. This sample deliberately
    # requires the known existing recording instead of silently synthesizing it.
    speech = None
    if speech_wav is not None:
        path = Path(speech_wav)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Expected the existing ordinary-file speech WAV')
        speech = path.read_bytes()
        if hashlib.sha256(speech).hexdigest() != SPEECH_SHA256:
            raise ValueError('Speech fixture SHA-256 does not match the existing recording')
        with wave.open(io.BytesIO(speech)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) != (1, 2, 24000, 432000):
                raise ValueError('Expected the complete 18-second PCM speech WAV')
    output.mkdir(parents=True)
    tone = test_tone()
    technical_beats = make_beats('首先提交请求。然后进入任务队列。最后由执行节点处理。')
    technical_plan = example_plan(technical_beats)
    technical_alignment = estimated_alignment(technical_beats, hashlib.sha256(tone).hexdigest(), 15)
    manifests = {'technical-15s': _write_project(output / 'technical-15s', technical_plan,
                  technical_beats, technical_alignment, tone)}
    if speech is not None:
        beats = make_beats(SPOKEN_TEXT)
        plan = history_plan(beats)
        alignment = estimated_alignment(beats, hashlib.sha256(speech).hexdigest(), 18)
        for name, animated in [('history-motion-24s', True), ('history-static-24s', False)]:
            manifests[name] = _write_project(output / name, plan, beats, alignment, speech,
                                             animated=animated, lead=3, tail=3)
    receipt = dict(schema='video.motion-samples.v1', projects=manifests,
                   agent_calls=0, tts_calls=0, technical_audio='synthetic timing pips; not speech',
                   speech_reused=speech is not None, alignment='estimated', review_status='pending',
                   note='Rendered motion, real speech alignment, user visual approval and naturalness remain unverified.')
    (output / 'samples.json').write_bytes(canonical(receipt))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--speech-wav')
    options = parser.parse_args()
    receipt = build(options.output, options.speech_wav)
    print(json.dumps({'output': str(Path(options.output).absolute()),
                      'projects': list(receipt['projects']), 'agent_calls': 0, 'tts_calls': 0}))
