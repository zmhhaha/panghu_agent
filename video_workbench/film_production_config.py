"""Explicit, operator-owned release and credentials for whole-film production.

Configuration verifies a previously admitted executor; it does not admit an
Agent profile, approve animation quality, or publish a Skill.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from agent_service.contracts import ServiceError, digest
from .config import AgentSettings
from .hyperframes_film_browser import HOST_NAMES
from .hyperframes_film_execution import _ordinary
from .hyperframes_film_snapshot import _release
from .hyperframes_film_k8s import verify_execution_admission
from .animation_runtime_capabilities import validate_runtime_capabilities


@dataclass(frozen=True, repr=False)
class FilmModuleAgentSettings:
    base_url: str
    token: str
    revision: str = '1'

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        try:
            settings = AgentSettings.from_env({
                'VIDEO_AGENT_URL': env.get('VIDEO_FILM_MODULE_AGENT_URL', ''),
                'VIDEO_AGENT_TOKEN': env.get('VIDEO_FILM_MODULE_AGENT_TOKEN', ''),
                'VIDEO_AGENT_TOKEN_FILE': env.get('VIDEO_FILM_MODULE_AGENT_TOKEN_FILE', ''),
                'VIDEO_AGENT_PROFILE_REVISION': env.get('VIDEO_FILM_MODULE_PROFILE_REVISION', '1'),
            })
        except ValueError:
            raise ValueError('Invalid dedicated VIDEO_FILM_MODULE_AGENT configuration') from None
        if settings is not None and settings.revision != '1':
            raise ValueError('Only the independently implemented film module revision 1 is supported')
        return cls(settings.base_url, settings.token, settings.revision) if settings else None


@dataclass(frozen=True)
class FilmProductionSettings:
    enabled: bool
    image: str
    runtime_fingerprint: dict
    runtime_capabilities: dict
    trusted_host_sha256: dict
    host_files: dict
    assets: dict
    admission: dict

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        gate = env.get('VIDEO_FILM_PRODUCTION_ENABLED', '0')
        if gate not in {'0', '1'}:
            raise ValueError('Invalid VIDEO_FILM_PRODUCTION_ENABLED')
        name = env.get('VIDEO_FILM_PRODUCTION_RELEASE_FILE', '')
        if not name:
            if gate == '1':
                raise ValueError('Whole-film production requires an explicit admitted release')
            return None
        try:
            path = Path(name)
            if not path.is_absolute():
                raise ValueError()
            value = json.loads(_ordinary(path, 65536))
            if type(value) is not dict or set(value) != {
                    'schema', 'image', 'runtime_fingerprint', 'runtime_capabilities',
                    'trusted_host_sha256', 'execution_admission'} or value['schema'] != 'video.film-production-release.v1':
                raise ValueError()
            runtime, hosts = _release(value['runtime_fingerprint'], value['image'], value['trusted_host_sha256'])
            capabilities = validate_runtime_capabilities(value['runtime_capabilities'])
            identity = capabilities['runtime_identity']
            if (identity['image_digest'] != runtime['image_digest']
                    or identity['engine_runtime_sha256'] != runtime['engine_runtime_sha256']
                    or identity['host_sha256'] != digest(hosts)):
                raise ValueError()
            # These are application-image-owned hosts. Request files and Agent
            # output can never nominate the executable implementation.
            root = Path(__file__).parent / 'motion_renderer'
            host_files = {name: _ordinary(root / name, 2 * 1024 * 1024) for name in HOST_NAMES}
            if {key: hashlib.sha256(raw).hexdigest() for key, raw in host_files.items()} != hosts:
                raise ValueError()
            admission = verify_execution_admission(value['execution_admission'], image=value['image'],
                runtime_fingerprint=runtime, trusted_host_sha256=hosts)
            assets = {}
            if capabilities['assets']:
                directory = Path(env.get('VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY', ''))
                if not directory.is_absolute() or not directory.is_dir():
                    raise ValueError()
                for asset, expected in capabilities['assets'].items():
                    raw = _ordinary(directory / asset, 32 * 1024 * 1024)
                    if hashlib.sha256(raw).hexdigest() != expected:
                        raise ValueError()
                    assets['assets/' + asset] = raw
            expected_assets = {'gsap.min.js': runtime['gsap_sha256'],
                'NotoSansCJK-Regular.ttc': runtime['font_sha256'],
                'three.module.min.js': runtime['three_sha256'],
                'three.LICENSE.txt': runtime['three_license_sha256']}
            # Version 2 only admits the complete, pinned optional library.
            # Image digest and the frozen project asset inventory bind these
            # bytes without changing the legacy motion-runtime.v3 schema.
            if capabilities['schema'] == 'video.animation-runtime-capabilities.v2':
                from .animejs_library import ASSETS as ANIME_ASSETS
                expected_assets.update(ANIME_ASSETS)
            if any(checksum != expected_assets[key] for key, checksum in capabilities['assets'].items()):
                raise ValueError()
            return cls(gate == '1', value['image'], runtime, capabilities, hosts, host_files, assets, admission)
        except (ValueError, KeyError, TypeError, OSError, ServiceError):
            raise ValueError('Whole-film production release is unavailable or does not match installed hosts and assets') from None


def execution_readiness(renderer, settings):
    """Check an authenticated executor's exact release before incurring work."""
    try:
        response = renderer._request('GET', '/v1/capabilities', 'film-production-preflight')
        value = response.json()['hyperframes_film']
        available = (value.get('configured') is True
            and value.get('schema') == 'video.hyperframes-film-execution-snapshot.v1'
            and value.get('image') == settings.image
            and value.get('runtime_fingerprint') == settings.runtime_fingerprint
            and value.get('trusted_host_sha256') == settings.trusted_host_sha256
            and value.get('admission_sha256') == settings.admission['sha256']
            and value.get('fps') == 24 and value.get('audio_sample_rate') == 48000
            and value.get('audio_channels') == 2 and value.get('max_seconds') == 60)
        return {'available': bool(available)}
    except (ServiceError, ValueError, KeyError, TypeError, AttributeError):
        return {'available': False}
