import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_service.contracts import digest
from video_workbench.film_production_config import (
    FilmModuleAgentSettings, FilmProductionSettings, execution_readiness,
)
from video_workbench.hyperframes_film_browser import HOST_NAMES
from video_workbench.hyperframes_film_k8s import ADMISSION_SCHEMA, ADMISSION_SCOPE
from test_creative_film_module_contract import RUNTIME


def release(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer'
    hosts = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in HOST_NAMES}
    image = 'registry.internal/video-motion-renderer:latest@' + RUNTIME['image_digest']
    admission = dict(schema=ADMISSION_SCHEMA, status='admitted', scope=ADMISSION_SCOPE,
        image=image, runtime_fingerprint=deepcopy(RUNTIME), trusted_host_sha256=hosts,
        evidence={key: dict(reference='operator-test/' + key, sha256='a' * 64)
                  for key in ('job', 'media', 'cancellation')})
    admission['sha256'] = digest(admission)
    capabilities = dict(schema='video.animation-runtime-capabilities.v1',
        allowed_engines=['html-svg', 'canvas'], assets={},
        runtime_identity=dict(image_digest=RUNTIME['image_digest'], host_sha256=digest(hosts), admission_id='operator-test',
                              engine_runtime_sha256=RUNTIME['engine_runtime_sha256']))
    value = dict(schema='video.film-production-release.v1', image=image,
        runtime_fingerprint=deepcopy(RUNTIME), runtime_capabilities=capabilities,
        trusted_host_sha256=hosts, execution_admission=admission)
    target = tmp_path / 'release.json'
    target.write_text(json.dumps(value), encoding='utf-8')
    return value, target


def test_whole_film_never_reuses_legacy_agent_credentials():
    assert FilmModuleAgentSettings.from_env({'VIDEO_AGENT_URL': 'http://legacy',
        'VIDEO_AGENT_TOKEN': 'x' * 40, 'VIDEO_SKILL_PRODUCTION_AGENT_URL': 'http://old-role',
        'VIDEO_SKILL_PRODUCTION_AGENT_TOKEN': 'y' * 40}) is None
    value = FilmModuleAgentSettings.from_env({'VIDEO_FILM_MODULE_AGENT_URL': 'http://film-agent',
        'VIDEO_FILM_MODULE_AGENT_TOKEN': 'z' * 40, 'VIDEO_FILM_MODULE_PROFILE_REVISION': '1'})
    assert (value.base_url, value.revision) == ('http://film-agent', '1')
    assert 'zzzz' not in repr(value)
    with pytest.raises(ValueError):
        FilmModuleAgentSettings.from_env({'VIDEO_FILM_MODULE_AGENT_URL': 'http://film-agent',
            'VIDEO_FILM_MODULE_AGENT_TOKEN': 'z' * 40, 'VIDEO_FILM_MODULE_PROFILE_REVISION': '2'})


def test_feature_is_explicit_and_technical_release_does_not_enable_it(tmp_path):
    assert FilmProductionSettings.from_env({}) is None
    with pytest.raises(ValueError):
        FilmProductionSettings.from_env({'VIDEO_FILM_PRODUCTION_ENABLED': '1'})
    value, path = release(tmp_path)
    settings = FilmProductionSettings.from_env({'VIDEO_FILM_PRODUCTION_RELEASE_FILE': str(path)})
    assert settings.enabled is False
    assert settings.trusted_host_sha256 == value['trusted_host_sha256']
    assert set(settings.host_files) == HOST_NAMES


@pytest.mark.parametrize('mutation', ['host', 'image', 'admission', 'runtime', 'asset'])
def test_modified_release_cannot_authorize_execution(tmp_path, mutation):
    value, path = release(tmp_path)
    if mutation == 'host': value['trusted_host_sha256']['hyperframes_film.mjs'] = 'a' * 64
    elif mutation == 'image': value['image'] = value['image'].replace(':latest@', ':unverified@')
    elif mutation == 'admission': value['execution_admission']['status'] = 'pending'
    elif mutation == 'runtime': value['runtime_capabilities']['runtime_identity']['image_digest'] = 'sha256:' + '9' * 64
    else: value['runtime_capabilities']['assets']['gsap.min.js'] = '9' * 64
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError):
        FilmProductionSettings.from_env({'VIDEO_FILM_PRODUCTION_RELEASE_FILE': str(path),
            'VIDEO_FILM_PRODUCTION_ENABLED': '1'})


def test_remote_readiness_requires_exact_executor_not_legacy_top_level(tmp_path):
    _, path = release(tmp_path)
    settings = FilmProductionSettings.from_env({'VIDEO_FILM_PRODUCTION_RELEASE_FILE': str(path)})
    value = dict(configured=True, schema='video.hyperframes-film-execution-snapshot.v1',
        image=settings.image, runtime_fingerprint=settings.runtime_fingerprint,
        trusted_host_sha256=settings.trusted_host_sha256, admission_sha256=settings.admission['sha256'],
        fps=24, audio_sample_rate=48000, audio_channels=2, max_seconds=60)
    payload = {'hyperframes_film': value}
    renderer = SimpleNamespace(_request=lambda *args: SimpleNamespace(json=lambda: payload))
    assert execution_readiness(renderer, settings) == {'available': True}
    value['admission_sha256'] = 'b' * 64
    assert execution_readiness(renderer, settings) == {'available': False}
    payload.clear()
    assert execution_readiness(renderer, settings) == {'available': False}
