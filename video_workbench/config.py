"""Operator-owned Agent connection settings; credentials never enter API responses."""
from __future__ import annotations

from dataclasses import dataclass
import os
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class MotionSettings:
    enabled: bool = False
    revision: str = '1'
    runtime_fingerprint: dict | None = None
    process_enabled: bool = False
    process_revision: str = '1'
    creative_enabled: bool = False
    creative_revision: str = '1'

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        enabled = env.get('VIDEO_MOTION_ENABLED', '0')
        revision = env.get('VIDEO_MOTION_PROFILE_REVISION', '1')
        process_enabled = env.get('VIDEO_PROCESS_ENABLED', '0')
        process_revision = env.get('VIDEO_PROCESS_PROFILE_REVISION', '1')
        creative_enabled = env.get('VIDEO_CREATIVE_ENABLED', '0')
        creative_revision = env.get('VIDEO_CREATIVE_PROFILE_REVISION', '1')
        if (enabled not in {'0', '1'} or process_enabled not in {'0', '1'}
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', revision)
                or creative_enabled not in {'0', '1'}
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', creative_revision)
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', process_revision)):
            raise ValueError('Invalid motion feature settings')
        runtime = None
        if name := env.get('VIDEO_MOTION_RUNTIME_FILE', ''):
            try:
                path = Path(name)
                if not path.is_absolute() or not path.is_file():
                    raise ValueError()
                with path.open('rb') as source:
                    raw = source.read(16385)
                if len(raw) > 16384:
                    raise ValueError()
                from .motion_snapshot import _runtime
                runtime = _runtime(json.loads(raw))
            except Exception:
                raise ValueError('Motion runtime fingerprint is unavailable or invalid') from None
        return cls(enabled == '1', revision, runtime, process_enabled == '1', process_revision,
                   creative_enabled == '1', creative_revision)


@dataclass(frozen=True)
class ExplanationSettings:
    enabled: bool = False
    revision: str = '1'

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        enabled = env.get('VIDEO_EXPLANATION_ENABLED', '0')
        revision = env.get('VIDEO_EXPLANATION_PROFILE_REVISION', '1')
        if enabled not in {'0', '1'}:
            raise ValueError('VIDEO_EXPLANATION_ENABLED must be 0 or 1')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', revision):
            raise ValueError('Invalid explanation task contract revision')
        return cls(enabled == '1', revision)


@dataclass(frozen=True, repr=False)
class AgentSettings:
    base_url: str
    token: str
    revision: str = '1'

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        url = env.get('VIDEO_AGENT_URL', '')
        token = env.get('VIDEO_AGENT_TOKEN', '')
        token_file = env.get('VIDEO_AGENT_TOKEN_FILE', '')
        if not any((url, token, token_file)):
            return None
        if not url or bool(token) == bool(token_file):
            raise ValueError('Configure VIDEO_AGENT_URL and exactly one Agent credential source')
        if token_file:
            try:
                path = Path(token_file)
                # Kubernetes projected secrets are symlinks; follow them, but read
                # only an ordinary, bounded file. Never include its path or value
                # in startup errors.
                if not path.is_absolute() or not path.is_file():
                    raise ValueError()
                with path.open('rb') as source:
                    raw = source.read(4097)
                if len(raw) > 4096:
                    raise ValueError()
                token = raw.decode('utf-8').strip()
            except (ValueError, OSError, UnicodeError):
                raise ValueError('Video Agent credential file is unavailable') from None
        if not 32 <= len(token) <= 512 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError('A valid Video Agent credential is required')
        try:
            parsed = urlsplit(url)
            port = parsed.port
            valid = (parsed.scheme in {'http', 'https'} and parsed.hostname and parsed.username is None
                     and parsed.password is None and not parsed.query and not parsed.fragment
                     and parsed.path in {'', '/'} and not any(ord(c) < 33 for c in url)
                     and (port is None or 1 <= port <= 65535))
            if not valid:
                raise ValueError()
        except ValueError:
            raise ValueError('An explicit Video Agent HTTP(S) origin is required') from None
        revision = env.get('VIDEO_AGENT_PROFILE_REVISION', '1')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', revision):
            raise ValueError('Invalid video task contract revision')
        return cls(url, token, revision)


@dataclass(frozen=True, repr=False)
class ProductionMethodSettings:
    """Separate internal executor credential; it grants no Agent admission."""
    base_url: str
    token: str

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        try:
            settings = AgentSettings.from_env({
                'VIDEO_AGENT_URL': env.get('VIDEO_METHOD_LAB_URL', ''),
                'VIDEO_AGENT_TOKEN': env.get('VIDEO_METHOD_LAB_TOKEN', ''),
                'VIDEO_AGENT_TOKEN_FILE': env.get('VIDEO_METHOD_LAB_TOKEN_FILE', ''),
            })
        except ValueError:
            raise ValueError('Invalid dedicated VIDEO_METHOD_LAB origin or credential configuration') from None
        return cls(settings.base_url, settings.token) if settings else None


@dataclass(frozen=True, repr=False)
class ProductionAgentSettings:
    """Dedicated caller identity for methods-aware formal authoring."""
    base_url: str
    token: str
    revision: str = '1'

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        try:
            settings = AgentSettings.from_env({
                'VIDEO_AGENT_URL': env.get('VIDEO_PRODUCTION_AGENT_URL', ''),
                'VIDEO_AGENT_TOKEN': env.get('VIDEO_PRODUCTION_AGENT_TOKEN', ''),
                'VIDEO_AGENT_TOKEN_FILE': env.get('VIDEO_PRODUCTION_AGENT_TOKEN_FILE', ''),
                'VIDEO_AGENT_PROFILE_REVISION': env.get('VIDEO_PRODUCTION_AGENT_PROFILE_REVISION', '1'),
            })
        except ValueError:
            raise ValueError('Invalid dedicated VIDEO_PRODUCTION_AGENT origin, credential or revision configuration') from None
        return cls(settings.base_url, settings.token, settings.revision) if settings else None
