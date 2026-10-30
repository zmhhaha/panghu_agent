"""Offline speech boundary tests with real PCM files and mocked transport."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import io
import json
import struct
import threading
import wave

import httpx
import pytest

from video_workbench.speech import (
    SpeechClient, SpeechError, SpeechSettings, inspect_wav,
)


def wav_bytes(*, frames=8000, rate=8000, channels=1, width=2):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as out:
        out.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(b'\x01' * frames * channels * width)
    return stream.getvalue()


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self):
        self.closed = True


def response(raw, status=200, *, headers=None):
    return httpx.Response(status, headers=headers or {}, stream=Chunks([raw]))


def settings(**kwargs):
    return SpeechSettings('http://speech.internal:8880/v1', 'kokoro', voice='zf_xiaobei', **kwargs)


def test_unconfigured_and_internal_service_without_key():
    assert SpeechSettings.from_env({}) is None
    config = SpeechSettings.from_env({'VIDEO_TTS_URL': 'http://speech:8880/v1/', 'VIDEO_TTS_MODEL': 'model'})
    assert config.base_url == 'http://speech:8880/v1'
    assert config.token is None and config.voice is None


@pytest.mark.parametrize('env', [
    {'VIDEO_TTS_URL': 'http://speech'}, {'VIDEO_TTS_MODEL': 'model'},
    {'VIDEO_TTS_VOICE': 'voice'}, {'VIDEO_TTS_TOKEN': 'secret'},
    {'VIDEO_TTS_TOKEN_FILE': '/missing'},
    {'VIDEO_TTS_URL': 'http://speech', 'VIDEO_TTS_MODEL': 'model',
     'VIDEO_TTS_TOKEN': 'secret', 'VIDEO_TTS_TOKEN_FILE': '/missing'},
])
def test_ambiguous_or_partial_configuration_fails_closed(env):
    with pytest.raises(ValueError):
        SpeechSettings.from_env(env)


@pytest.mark.parametrize('url', [
    'http://u:p@speech/v1', 'http://@speech/v1', 'http://speech/v1?token=secret',
    'http://speech/v1?', 'https://speech/v1#part', 'file:///tmp/audio',
    'http://speech:wrong/v1', 'http://speech:0/v1', 'http://speech/v1\n',
    'http://speech/v1/../admin', 'http://speech/v1%2Fadmin', 'http://speech\\evil/v1',
])
def test_invalid_operator_url_is_rejected_without_disclosure(url):
    with pytest.raises(ValueError) as error:
        SpeechSettings(url, 'model')
    assert url not in str(error.value)
    assert 'secret' not in str(error.value)


def test_credential_file_and_optional_projected_symlink(tmp_path):
    path = tmp_path / 'token'
    path.write_text('a-test-token\n', encoding='utf-8')
    config = SpeechSettings.from_env({'VIDEO_TTS_URL': 'https://speech/v1', 'VIDEO_TTS_MODEL': 'model',
                                      'VIDEO_TTS_TOKEN_FILE': str(path)})
    assert config.token == 'a-test-token'
    assert config.token not in repr(config)
    assert str(path) not in repr(config)
    link = tmp_path / 'projected-token'
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip('Creating a projected-secret symlink requires Windows privileges')
    linked = SpeechSettings.from_env({'VIDEO_TTS_URL': 'https://speech/v1', 'VIDEO_TTS_MODEL': 'model',
                                      'VIDEO_TTS_TOKEN_FILE': str(link)})
    assert linked.token == config.token


@pytest.mark.parametrize('value', [b'', b's' * 4097, b'\xff', b'bad\x00token'])
def test_invalid_secret_files_reveal_neither_content_nor_path(tmp_path, value):
    path = tmp_path / 'secret-value'
    path.write_bytes(value)
    with pytest.raises(ValueError) as error:
        SpeechSettings.from_env({'VIDEO_TTS_URL': 'https://speech/v1', 'VIDEO_TTS_MODEL': 'model',
                                 'VIDEO_TTS_TOKEN_FILE': str(path)})
    assert 'secret-value' not in str(error.value)
    assert 'bad' not in str(error.value)


def test_fixed_request_and_measured_audio_identity():
    raw, calls = wav_bytes(), []

    def handle(request):
        calls.append(request)
        return response(raw, headers={'Content-Type': 'application/octet-stream'})

    config = settings(token='operator-token')
    with SpeechClient(config, transport=httpx.MockTransport(handle)) as client:
        audio = client.synthesize('你好，DSH。', speed=1)
        assert audio.input_sha256 == client.input_sha256('你好，DSH。', speed=1.0)
        assert audio.input_sha256 != client.input_sha256('你好，DSH。', speed=1.1)
        assert audio.input_sha256 != client.input_sha256('你好，DSH。', voice='other')
    request = calls[0]
    assert str(request.url) == 'http://speech.internal:8880/v1/audio/speech'
    assert request.headers['authorization'] == 'Bearer operator-token'
    assert request.headers['accept-encoding'] == 'identity'
    assert json.loads(request.content) == {'model': 'kokoro', 'input': '你好，DSH。',
                                           'voice': 'zf_xiaobei', 'speed': 1.0, 'response_format': 'wav'}
    assert audio.data == raw and audio.sha256 == hashlib.sha256(raw).hexdigest()
    assert (audio.duration_seconds, audio.sample_rate, audio.channels, audio.frame_count) == (1.0, 8000, 1, 8000)
    assert "\\x01" not in repr(audio)


def test_injected_client_defaults_cannot_change_target_or_send_cookies():
    calls = []

    def handle(request):
        calls.append(request)
        return response(wav_bytes())

    injected = httpx.Client(transport=httpx.MockTransport(handle), base_url='https://other/',
                            headers={'Authorization': 'Bearer wrong'}, cookies={'session': 'wrong'},
                            params={'secret': 'wrong'}, follow_redirects=True)
    client = SpeechClient(settings(), client=injected)
    client.synthesize('text')
    client.close()
    assert not injected.is_closed
    assert str(calls[0].url) == 'http://speech.internal:8880/v1/audio/speech'
    assert 'authorization' not in calls[0].headers and 'cookie' not in calls[0].headers
    injected.close()


@pytest.mark.parametrize(('status', 'code'), [
    (401, 'speech_upstream_auth_failed'), (403, 'speech_upstream_auth_failed'),
    (429, 'speech_upstream_rate_limited'), (503, 'speech_upstream_unavailable'),
    (400, 'speech_upstream_rejected'), (302, 'speech_upstream_redirect_rejected'),
])
def test_error_responses_do_not_leak_or_retry(status, code):
    calls = []
    stream = Chunks([b'{"secret":"upstream-body-token"}'])

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': 'https://different.example/collect'}, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as injected:
        with SpeechClient(settings(token='operator-token'), client=injected) as client:
            with pytest.raises(SpeechError) as error:
                client.synthesize('text')
    assert error.value.code == code and str(error.value) == code
    assert 'token' not in str(error.value) and len(calls) == 1 and stream.closed


def test_timeout_is_not_retried_and_exception_is_sanitized():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.extensions['timeout']['connect'] == 5
        assert request.extensions['timeout']['read'] == 60
        raise httpx.ReadTimeout('upstream-secret', request=request)

    with SpeechClient(settings(), transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SpeechError) as error:
            client.synthesize('text')
    assert error.value.code == 'speech_timeout' and len(calls) == 1
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize(('raw', 'headers', 'code'), [
    (b'{"audio":"not audio"}', {'Content-Type': 'audio/wav'}, 'speech_invalid_audio'),
    (wav_bytes()[:-2], {}, 'speech_invalid_audio'),
    (wav_bytes(), {'Content-Length': '1'}, 'speech_invalid_audio'),
    (wav_bytes(), {'Content-Encoding': 'gzip'}, 'speech_encoded_audio_rejected'),
    (wav_bytes(), {'Content-Length': '999999999'}, 'speech_audio_too_large'),
    (wav_bytes(), {'Content-Length': '9' * 5000}, 'speech_audio_too_large'),
], ids=['json-as-wav', 'truncated-wav', 'false-length', 'encoded-response', 'declared-oversize', 'huge-length-header'])
def test_malformed_responses_never_publish_audio(raw, headers, code):
    with SpeechClient(settings(), transport=httpx.MockTransport(lambda req: response(raw, headers=headers))) as client:
        with pytest.raises(SpeechError) as error:
            client.synthesize('text')
    assert error.value.code == code


def test_actual_stream_size_limit_without_content_length():
    stream = Chunks([b'R' * 100, b'I' * 100])
    with SpeechClient(settings(max_audio_bytes=150), transport=httpx.MockTransport(
            lambda req: httpx.Response(200, stream=stream))) as client:
        with pytest.raises(SpeechError, match='speech_audio_too_large'):
            client.synthesize('text')
    assert stream.closed


def test_absolute_deadline_stops_slow_trickle_without_retry(monkeypatch):
    clock, calls, raw = [0.0], [], wav_bytes()
    monkeypatch.setattr('video_workbench.speech.monotonic', lambda: clock[0])

    def chunks():
        yield raw[:44]
        clock[0] += 0.6
        yield raw[44:100]
        clock[0] += 0.6
        yield raw[100:]

    stream = Chunks(chunks())

    def handle(request):
        calls.append(request)
        assert request.extensions['timeout']['read'] == 1.0
        return httpx.Response(200, stream=stream)

    with SpeechClient(settings(total_timeout=1.0), transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SpeechError, match='speech_timeout'):
            client.synthesize('text')
    assert len(calls) == 1 and stream.closed


@pytest.mark.parametrize('raw', [wav_bytes(frames=0), wav_bytes(channels=3), wav_bytes(rate=1000)],
                         ids=['empty', 'too-many-channels', 'invalid-rate'])
def test_invalid_pcm_properties(raw):
    with pytest.raises(SpeechError, match='speech_invalid_audio'):
        inspect_wav(raw)


def test_duration_limit_and_partial_frame_or_duplicate_data():
    raw = wav_bytes()
    with pytest.raises(SpeechError):
        inspect_wav(raw, max_duration_seconds=0.5)
    # The container is the right size, but the declared data has half a frame.
    partial = bytearray(raw)
    struct.pack_into('<I', partial, 40, len(raw) - 45)
    with pytest.raises(SpeechError):
        inspect_wav(bytes(partial))
    duplicate = bytearray(raw + b'data\x00\x00\x00\x00')
    struct.pack_into('<I', duplicate, 4, len(duplicate) - 8)
    with pytest.raises(SpeechError):
        inspect_wav(bytes(duplicate))


@pytest.mark.parametrize(('text', 'speed'), [('', 1), ('\x00bad', 1), ('text', True),
                                         ('text', float('nan')), ('text', float('inf')),
                                         ('text', 0.1), ('x' * 10001, 1)],
                         ids=['empty', 'control-character', 'boolean-speed', 'nan-speed',
                              'infinite-speed', 'out-of-range-speed', 'too-long-text'])
def test_invalid_inputs_make_no_request(text, speed):
    def handle(request):
        pytest.fail('Invalid input must not reach the paid provider')

    with SpeechClient(settings(), transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SpeechError):
            client.synthesize(text, speed=speed)


def test_voice_is_required_when_operator_default_is_absent():
    with SpeechClient(replace(settings(), voice=None), transport=httpx.MockTransport(
            lambda req: response(wav_bytes()))) as client:
        with pytest.raises(SpeechError, match='speech_voice_required'):
            client.synthesize('text')
        assert client.synthesize('text', voice='chosen').duration_seconds == 1


def test_cancel_before_send_and_concurrent_request_isolation():
    cancelled = threading.Event()
    cancelled.set()
    calls = []
    raw = wav_bytes()

    def handle(request):
        text = json.loads(request.content)['input']
        calls.append(text)
        if text == 'cancel':
            def chunks():
                yield raw[:44]
                cancelled.set()
                yield raw[44:]
            return httpx.Response(200, stream=Chunks(chunks()))
        return response(raw)

    with SpeechClient(settings(), transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SpeechError, match='speech_cancelled'):
            client.synthesize('not-sent', cancel_event=cancelled)
        assert calls == []
        cancelled.clear()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.synthesize, 'cancel', cancel_event=cancelled)
            second = pool.submit(client.synthesize, 'keep')
            with pytest.raises(SpeechError, match='speech_cancelled'):
                first.result()
            assert second.result().sha256 == hashlib.sha256(raw).hexdigest()
    assert sorted(calls) == ['cancel', 'keep']
