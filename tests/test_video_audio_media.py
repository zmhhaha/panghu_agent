"""Timing, binding and real local FFmpeg encoding; never calls a speech model."""
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave
import zlib

import pytest

from agent_service.contracts import ServiceError
from video_workbench import media
from video_workbench.static_render_worker import NoResources


def audio(seconds=1.0, *, rate=24000, channels=1):
    output = io.BytesIO()
    frames = b''.join(struct.pack('<h', round(math.sin(index * 2 * math.pi * 440 / rate) * 5000)) * channels
                      for index in range(round(seconds * rate)))
    with wave.open(output, 'wb') as target:
        target.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        target.writeframes(frames)
    data = output.getvalue()
    return dict(id='a-1', data=data, sha256=hashlib.sha256(data).hexdigest(),
                duration_seconds=seconds, input_sha256='a' * 64)


def scene(**changes):
    result = dict(id='scene-1', title='第一镜', narration='这是第一句。还有第二句。', duration_seconds=1,
                  visual=dict(id='v-1', format='svg', source='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#164e63"/></svg>'),
                  audio=audio())
    result.update(changes)
    return result


def freeze(items=None, **options):
    return media.freeze_snapshot('project-1', 3, '中文技术解说', items or [scene()], **options)


def test_timing_extends_for_speech_and_retains_configured_slack():
    timing = media.scene_timing(8, 10, leading=0.5, trailing=1)
    assert timing['duration_seconds'] == 11.5
    assert timing['difference_seconds'] == 3.5 and timing['extends_configured']
    assert media.scene_timing(20, 10, 0.5, 1)['duration_seconds'] == 20


@pytest.mark.parametrize('configured,speech,lead,tail', [
    (True, 1, 0, 0), (8, float('nan'), 0, 0), (8, 3, -1, 0),
    (8, 3, 0, 11), (121, 3, 0, 0), (8, 301, 0, 0),
])
def test_timing_rejects_nonfinite_unbounded_and_boolean(configured, speech, lead, tail):
    with pytest.raises(ServiceError):
        media.scene_timing(configured, speech, lead, tail)


def test_estimates_are_labelled_and_cover_text_inside_audio():
    estimated = media.estimate_captions('中文第一句。DSH 与 SDK 继续。最后一句！', 6.3, leading=0.5)
    assert estimated['alignment'] == 'estimated' and estimated['requires_review']
    assert ''.join(c['text'] for c in estimated['cues']) == '中文第一句。DSH 与 SDK 继续。最后一句！'
    assert estimated['cues'][0]['start'] == 0.5
    assert estimated['cues'][-1]['end'] == pytest.approx(6.8)


def test_manual_captions_validate_audio_binding_and_nonoverlap():
    cues = [dict(start=0.5, end=2, text='第一句'), dict(start=2, end=4, text='第二句')]
    valid = media.validate_captions(cues, 8, 'a' * 64, leading_seconds=0.5, audio_seconds=4)
    assert valid['alignment'] == 'manual' and not valid['requires_review']
    assert valid['audio_sha256'] == 'a' * 64
    for wrong in [[dict(start=0, end=2, text='早于留白')],
                  [dict(start=2, end=4, text='一'), dict(start=3, end=4.5, text='重叠')],
                  [dict(start=1, end=4.6, text='超出音频')],
                  [dict(start=1, end=1.00001, text='无法表达的时间')],
                  [dict(start=1, end=2, text='字幕\r\r注入空段')]]:
        with pytest.raises(ServiceError):
            media.validate_captions(wrong, 8, 'a' * 64, leading_seconds=0.5, audio_seconds=4)


def test_snapshot_freezes_bytes_versions_order_and_real_media_duration():
    first = scene(audio=audio(1.11), leading_seconds=0.2, trailing_seconds=0.3)
    second = scene(id='scene-2', title='第二镜')
    result = freeze([first, second])
    json.dumps(result, allow_nan=False)
    assert result['project_revision'] == 3 and result['visual_mode'] == 'static'
    assert [item['id'] for item in result['scenes']] == ['scene-1', 'scene-2']
    assert result['scenes'][0]['timing']['duration_seconds'] == math.ceil(1.61 * 24) / 24
    assert result['scenes'][1]['start_seconds'] == result['scenes'][0]['end_seconds']
    digest = result['sha256']
    first['visual']['source'] = 'edited after export started'
    first['audio']['data'] = b'replaced'
    assert media.verify_snapshot(result)['sha256'] == digest
    assert result['scenes'][0]['visual']['source'].startswith('<svg')
    assert result['scenes'][0]['captions']['requires_review']


def test_wrong_bytes_duration_and_stale_audio_are_rejected():
    for change in [dict(sha256='f' * 64), dict(duration_seconds=2), dict(stale=True), dict(data=b'bad')]:
        candidate = audio()
        candidate.update(change)
        with pytest.raises(ServiceError):
            freeze([scene(audio=candidate)])
    with pytest.raises(ServiceError) as error:
        freeze([scene(audio=None)])
    assert error.value.code == 'missing_or_stale_audio'


def test_manual_captions_cannot_be_reused_for_changed_audio():
    item = scene(captions=dict(audio_sha256='b' * 64, alignment='manual', cues=[]))
    with pytest.raises(ServiceError) as error:
        freeze([item])
    assert error.value.code == 'stale_captions'
    item['captions'].pop('audio_sha256')
    with pytest.raises(ServiceError):
        freeze([item])


def test_silent_is_explicit_and_srt_uses_global_offsets():
    with pytest.raises(ServiceError):
        freeze([scene(audio=None)])
    silent = freeze([scene(audio=None)], silent=True)
    assert silent['silent'] and not silent['subtitles'] and silent['scenes'][0]['audio'] is None
    normal = freeze([scene(), scene(id='scene-2')])
    srt = media.subtitles_srt(normal)
    assert '00:00:00,000 -->' in srt and '00:00:01,000 -->' in srt
    assert srt.count('这是第一句。') == 2


def test_snapshot_tampering_and_external_visual_content_fail():
    frozen = freeze()
    frozen['scenes'][0]['visual']['source'] = '<svg/>'
    with pytest.raises(ServiceError) as error:
        media.verify_snapshot(frozen)
    assert error.value.code == 'media_snapshot_corrupt'
    item = scene(visual=dict(id='v', format='html', source='<script>fetch("https://outside.test")</script>'))
    with pytest.raises(ServiceError):
        freeze([item])


def test_cancelled_subprocess_is_stopped_before_return(tmp_path):
    cancel = threading.Event()
    timer = threading.Timer(0.15, cancel.set)
    timer.start()
    started = time.monotonic()
    with pytest.raises(ServiceError) as error:
        media._run([sys.executable, '-c', 'import time; time.sleep(10)'], cwd=tmp_path,
                   timeout=5, cancel_event=cancel)
    timer.join()
    assert error.value.code == 'media_cancelled' and time.monotonic() - started < 3


def png_fixture(_source, _format, destination, **_):
    def chunk(name, value):
        return struct.pack('>I', len(value)) + name + value + struct.pack('>I', zlib.crc32(name + value) & 0xFFFFFFFF)
    pixels = (b'\0' + bytes((22, 78, 99)) * media.WIDTH) * media.HEIGHT
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', media.WIDTH, media.HEIGHT, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))
    destination.write_bytes(data)


def test_screenshot_enforces_csp_and_keeps_chromium_sandbox(tmp_path, monkeypatch):
    seen = {}

    def fake_browser(args, **options):
        assert '--no-sandbox' not in args and '--disable-setuid-sandbox' not in args
        assert '--blink-settings=scriptEnabled=false' in args
        assert any(value.startswith('--proxy-bypass-list=<-loopback>;127.0.0.1:') for value in args)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(args[-1], timeout=2) as response:
            seen['policy'] = response.headers['Content-Security-Policy']
            seen['source'] = response.read()
        target = next(value.split('=', 1)[1] for value in args if value.startswith('--screenshot='))
        png_fixture('', '', Path(target))
        return b''

    monkeypatch.setattr(media, '_run', fake_browser)
    media.screenshot_scene(scene()['visual']['source'], 'svg', tmp_path / 'shot.png', chromium=sys.executable)
    assert "sandbox; default-src 'none'; script-src 'none'" in seen['policy']
    assert "connect-src 'none'" in seen['policy'] and b'<svg' in seen['source']


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://localhost/private',
                                 'https://outside.test/font.ttf', 'data:image/png;base64,AA=='])
def test_weasy_worker_denies_all_resource_schemes(url):
    guard = NoResources()
    with pytest.raises(ValueError):
        guard(url)
    assert guard.blocked


def test_weasy_path_is_explicit_isolated_and_cleans_source_files(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **options):
        calls.append(args)
        if len(calls) == 1:
            assert args[1] == '-I' and args[2].endswith('static_render_worker.py')
            content = Path(args[3]).read_text(encoding='utf-8')
            assert '<svg' in content and 'position:fixed!important' in content
            Path(args[4]).write_bytes(b'%PDF-1.7 test fixture')
        else:
            assert '-singlefile' in args and args[args.index('-scale-to-x') + 1] == '1280'
            png_fixture('', '', Path(args[-1] + '.png'))
        return b''

    monkeypatch.setenv('VIDEO_STATIC_RENDERER', 'weasyprint')
    monkeypatch.setattr(media, '_run', fake_run)
    monkeypatch.setattr(media, '_binary', lambda name, configured=None: name)
    media.screenshot_scene(scene()['visual']['source'], 'svg', tmp_path / 'shot.png')
    assert len(calls) == 2 and (tmp_path / 'shot.png').is_file()
    assert not list(tmp_path.glob('*.html')) and not list(tmp_path.glob('*.pdf'))


def test_media_child_does_not_inherit_provider_or_cloud_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDEO_TTS_TOKEN', 'test-private-token')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test-cloud-key')
    monkeypatch.setenv('HTTPS_PROXY', 'http://test-secret-proxy')
    child = media._child_env(tmp_path)
    assert not {'VIDEO_TTS_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'HTTPS_PROXY'}.intersection(child)
    assert child['TMPDIR'] == str(tmp_path) and child['XDG_CACHE_HOME'] == str(tmp_path / '.cache')


def test_renderer_selection_is_explicit_and_frozen(monkeypatch):
    monkeypatch.setenv('VIDEO_STATIC_RENDERER', 'weasyprint')
    result = freeze()
    assert result['static_renderer'] == 'weasyprint'
    monkeypatch.setenv('VIDEO_STATIC_RENDERER', 'chromium')
    assert media.verify_snapshot(result)['static_renderer'] == 'weasyprint'
    monkeypatch.setenv('VIDEO_STATIC_RENDERER', 'unknown-engine')
    assert not media.available()
    with pytest.raises(ServiceError) as error:
        freeze()
    assert error.value.code == 'invalid_static_renderer'


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='local FFmpeg unavailable')
def test_real_ffmpeg_two_scene_movie_preserves_pcm_and_has_audio(tmp_path):
    first = scene(audio=audio(1.2, rate=16000), leading_seconds=0.25, trailing_seconds=0.25)
    second = scene(id='scene-2', audio=audio(0.8, rate=48000, channels=2))
    snapshot = freeze([first, second], subtitles=True)
    result = media.render_snapshot(snapshot, tmp_path, screenshotter=png_fixture)
    target = Path(result['path'])
    assert target.is_file() and result['audio_codec'] == 'aac' and result['video_codec'] == 'h264'
    assert result['duration_seconds'] == pytest.approx(snapshot['duration_seconds'], abs=0.15)
    assert result['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
    with wave.open(str(tmp_path / 'assets/master.wav'), 'rb') as pcm:
        raw = pcm.readframes(pcm.getnframes())
        assert pcm.getnframes() == round(snapshot['duration_seconds'] * 24000)
    assert not any(raw[:round(0.25 * 24000) * 2])
    # The end of first speech and the last part of second speech survive.
    assert any(raw[round(1.35 * 24000) * 2:round(1.45 * 24000) * 2])
    offset = snapshot['scenes'][1]['start_seconds']
    assert any(raw[round((offset + 0.65) * 24000) * 2:round((offset + 0.75) * 24000) * 2])
    decoded = subprocess.check_output([shutil.which('ffmpeg'), '-v', 'error', '-i', str(target),
                                       '-vn', '-f', 's16le', '-acodec', 'pcm_s16le', '-'])
    assert len(decoded) >= round((snapshot['duration_seconds'] - 0.05) * 24000) * 2 and any(decoded)


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='local FFmpeg unavailable')
def test_real_silent_movie_is_labelled_and_has_no_audio_track(tmp_path):
    result = media.render_snapshot(freeze([scene(audio=None)], silent=True), tmp_path,
                                   screenshotter=png_fixture)
    assert result['silent'] and result['audio_codec'] is None
    probe = json.loads(subprocess.check_output([shutil.which('ffprobe'), '-v', 'error', '-show_streams',
                                               '-of', 'json', result['path']]))
    assert [stream['codec_type'] for stream in probe['streams']] == ['video']
