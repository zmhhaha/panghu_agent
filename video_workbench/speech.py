"""Bounded, provider-neutral speech HTTP adapter.

Only operator configuration selects the upstream and model.  This module does
not start jobs, adopt audio, retry paid requests or claim that cancelling local
collection has stopped upstream generation.  Each response must be a complete
PCM WAV file, including when the upstream sends a generic content type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
from time import monotonic
from typing import Mapping
from urllib.parse import urlsplit
import wave

import httpx

from agent_service.contracts import ServiceError


MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 300.0
MAX_TEXT_CHARACTERS = 10_000
MAX_TEXT_BYTES = 64 * 1024
_TOKEN_BYTES = 4096


class SpeechError(ServiceError):
    """Public error codes only; never upstream bodies, headers or exceptions."""


def _plain(value, maximum):
    return (type(value) is str and 1 <= len(value) <= maximum
            and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _origin_prefix(value):
    try:
        if type(value) is not str or not 1 <= len(value) <= 2048 or any(ord(c) < 33 or ord(c) == 127 for c in value):
            raise ValueError()
        parts = urlsplit(value)
        port = parts.port
        if (parts.scheme not in {'http', 'https'} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or '?' in value or '#' in value
                or (port is not None and not 1 <= port <= 65535)
                or '\\' in value
                or not re.fullmatch(r'(?:/[A-Za-z0-9._~-]+)*/?', parts.path)
                or any(segment in {'.', '..'} for segment in parts.path.split('/'))):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('An explicit speech HTTP(S) URL with an ordinary API prefix is required') from None
    return value.rstrip('/')


@dataclass(frozen=True, repr=False)
class SpeechSettings:
    base_url: str
    model: str
    token: str | None = None
    voice: str | None = None
    connect_timeout: float = 5.0
    read_timeout: float = 60.0
    total_timeout: float = 120.0
    max_audio_bytes: int = MAX_AUDIO_BYTES
    max_audio_seconds: float = MAX_AUDIO_SECONDS

    def __post_init__(self):
        object.__setattr__(self, 'base_url', _origin_prefix(self.base_url))
        if not _plain(self.model, 200) or self.voice is not None and not _plain(self.voice, 200):
            raise ValueError('An explicit speech model and valid optional voice are required')
        if self.token is not None and (not _plain(self.token, _TOKEN_BYTES)
                                       or any(not 33 <= ord(c) <= 126 for c in self.token)):
            raise ValueError('Invalid speech credential')
        for timeout in (self.connect_timeout, self.read_timeout, self.total_timeout):
            if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 300:
                raise ValueError('Invalid speech timeout')
        if (type(self.max_audio_bytes) is not int or not 44 <= self.max_audio_bytes <= MAX_AUDIO_BYTES
                or type(self.max_audio_seconds) not in {int, float}
                or not math.isfinite(self.max_audio_seconds) or not 0 < self.max_audio_seconds <= MAX_AUDIO_SECONDS):
            raise ValueError('Invalid speech audio limits')

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        url, model, voice, token, token_file = (env.get(name, '') for name in
            ('VIDEO_TTS_URL', 'VIDEO_TTS_MODEL', 'VIDEO_TTS_VOICE', 'VIDEO_TTS_TOKEN', 'VIDEO_TTS_TOKEN_FILE'))
        if not any((url, model, voice, token, token_file)):
            return None
        if not url or not model or token and token_file:
            raise ValueError('Configure speech URL and model with at most one credential source')
        if token_file:
            try:
                path = Path(token_file)
                # Projected Kubernetes secrets are symlinks. Follow them while
                # requiring an ordinary bounded file, and do not expose paths.
                if not path.is_absolute() or not path.is_file():
                    raise ValueError()
                with path.open('rb') as stream:
                    raw = stream.read(_TOKEN_BYTES + 1)
                if len(raw) > _TOKEN_BYTES:
                    raise ValueError()
                token = raw.decode('utf-8').strip()
                if not token:
                    raise ValueError()
            except (OSError, ValueError, UnicodeError):
                raise ValueError('Speech credential file is unavailable') from None
        return cls(url, model, token or None, voice or None)


@dataclass(frozen=True)
class WavInfo:
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int


@dataclass(frozen=True)
class SpeechAudio(WavInfo):
    data: bytes = field(repr=False)
    sha256: str
    input_sha256: str


def inspect_wav(data: bytes, *, max_bytes=MAX_AUDIO_BYTES, max_duration_seconds=MAX_AUDIO_SECONDS) -> WavInfo:
    """Check container boundaries and actually read every declared PCM frame."""
    if type(data) is not bytes or not 44 <= len(data) <= max_bytes:
        raise SpeechError('speech_invalid_audio', 502)
    try:
        if data[:4] != b'RIFF' or data[8:12] != b'WAVE' or struct.unpack_from('<I', data, 4)[0] + 8 != len(data):
            raise ValueError()
        position, seen_format, data_size = 12, False, None
        while position < len(data):
            if position + 8 > len(data):
                raise ValueError()
            name = data[position:position + 4]
            size = struct.unpack_from('<I', data, position + 4)[0]
            end = position + 8 + size
            if end > len(data) or end + (size & 1) > len(data):
                raise ValueError()
            if name == b'fmt ':
                if seen_format or data_size is not None:
                    raise ValueError()
                seen_format = True
            if name == b'data':
                if not seen_format or data_size is not None:
                    raise ValueError()
                data_size = size
            position = end + (size & 1)
        if not seen_format or data_size is None or data_size == 0:
            raise ValueError()
        with wave.open(io.BytesIO(data), 'rb') as audio:
            rate, channels, width, frames = audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()
            if (audio.getcomptype() != 'NONE' or not 8000 <= rate <= 96000
                    or channels not in {1, 2} or width not in {1, 2, 3, 4} or frames <= 0
                    or data_size != frames * channels * width):
                raise ValueError()
            duration = frames / rate
            if not math.isfinite(duration) or not 0 < duration <= max_duration_seconds:
                raise ValueError()
            if len(audio.readframes(frames + 1)) != data_size:
                raise ValueError()
        return WavInfo(duration, rate, channels, width, frames)
    except (ValueError, OSError, EOFError, wave.Error, struct.error, OverflowError):
        raise SpeechError('speech_invalid_audio', 502) from None


def _cancel(event):
    if event is not None and event.is_set():
        raise SpeechError('speech_cancelled', 409)


def _checkpoint(event, deadline):
    _cancel(event)
    if monotonic() >= deadline:
        raise SpeechError('speech_timeout', 504)


class SpeechClient:
    """Thread-safe independent requests, with no retry or adoption side effect.

    Cancellation and the absolute request deadline are checked before/after
    each read. A blocked read still exits at its configured read timeout;
    this does not prove the provider stopped work or cancelled billing.
    The injected client remains owned by its caller.
    """

    def __init__(self, settings: SpeechSettings, *, client: httpx.Client | None = None,
                 transport: httpx.BaseTransport | None = None):
        if not isinstance(settings, SpeechSettings) or client is not None and transport is not None:
            raise ValueError('Provide speech settings and at most one HTTP client or transport')
        self.settings = settings
        self._owned = client is None
        self._client = client or httpx.Client(transport=transport, follow_redirects=False, trust_env=False)

    def close(self):
        if self._owned:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _payload(self, text, voice, speed):
        if (type(text) is not str or not 1 <= len(text) <= MAX_TEXT_CHARACTERS or not text.strip()
                or any((ord(c) < 32 and c not in '\t\r\n') or ord(c) == 127
                       or 0xD800 <= ord(c) <= 0xDFFF for c in text)
                or len(text.encode('utf-8')) > MAX_TEXT_BYTES
                or type(speed) not in {int, float} or not math.isfinite(speed) or not 0.25 <= speed <= 4):
            raise SpeechError('speech_invalid_input', 422)
        voice = self.settings.voice if voice is None else voice
        if not _plain(voice, 200):
            raise SpeechError('speech_voice_required', 422)
        return dict(model=self.settings.model, input=text, voice=voice, speed=float(speed), response_format='wav')

    def input_sha256(self, text, *, voice=None, speed=1.0):
        payload = self._payload(text, voice, speed)
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def synthesize(self, text, *, voice=None, speed=1.0, cancel_event=None) -> SpeechAudio:
        _cancel(cancel_event)
        deadline = monotonic() + self.settings.total_timeout
        payload = self._payload(text, voice, speed)
        headers = {'Accept': 'audio/wav', 'Accept-Encoding': 'identity'}
        if self.settings.token is not None:
            headers['Authorization'] = 'Bearer ' + self.settings.token
        remaining = deadline - monotonic()
        _checkpoint(cancel_event, deadline)
        timeout = httpx.Timeout(min(self.settings.read_timeout, remaining),
                                connect=min(self.settings.connect_timeout, remaining))
        # Build a stand-alone request so injected clients cannot add defaults
        # such as query parameters, cookies or a different Authorization value.
        request = httpx.Request('POST', self.settings.base_url + '/audio/speech',
                                headers=headers, json=payload,
                                extensions={'timeout': timeout.as_dict()})
        response = None
        try:
            _checkpoint(cancel_event, deadline)
            response = self._client.send(request, stream=True, auth=None, follow_redirects=False)
            _checkpoint(cancel_event, deadline)
            status = response.status_code
            if 300 <= status < 400:
                raise SpeechError('speech_upstream_redirect_rejected', 502)
            if status in {401, 403}:
                raise SpeechError('speech_upstream_auth_failed', 502)
            if status == 429:
                raise SpeechError('speech_upstream_rate_limited', 503)
            if status >= 500:
                raise SpeechError('speech_upstream_unavailable', 503)
            if not 200 <= status < 300:
                raise SpeechError('speech_upstream_rejected', 502)
            if response.headers.get('Content-Encoding', 'identity').lower().strip() not in {'', 'identity'}:
                raise SpeechError('speech_encoded_audio_rejected', 502)
            length = response.headers.get('Content-Length')
            if length is not None and (len(length) > 12 or not length.isascii() or not length.isdecimal()
                                       or int(length) > self.settings.max_audio_bytes):
                raise SpeechError('speech_audio_too_large', 502)
            parts, size = [], 0
            chunks = response.iter_raw()
            while True:
                _checkpoint(cancel_event, deadline)
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
                _checkpoint(cancel_event, deadline)
                size += len(chunk)
                if size > self.settings.max_audio_bytes:
                    raise SpeechError('speech_audio_too_large', 502)
                parts.append(chunk)
            _checkpoint(cancel_event, deadline)
            data = b''.join(parts)
            if length is not None and len(data) != int(length):
                raise SpeechError('speech_invalid_audio', 502)
            info = inspect_wav(data, max_bytes=self.settings.max_audio_bytes,
                               max_duration_seconds=self.settings.max_audio_seconds)
            _checkpoint(cancel_event, deadline)
            return SpeechAudio(**vars(info), data=data, sha256=hashlib.sha256(data).hexdigest(),
                               input_sha256=hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':'), allow_nan=False).encode()).hexdigest())
        except httpx.TimeoutException:
            _cancel(cancel_event)
            raise SpeechError('speech_timeout', 504) from None
        except httpx.HTTPError:
            _cancel(cancel_event)
            raise SpeechError('speech_unavailable', 503) from None
        finally:
            if response is not None:
                response.close()
