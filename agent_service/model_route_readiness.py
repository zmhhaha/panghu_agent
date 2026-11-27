"""Trusted, bounded readiness reads for checks performed before spending."""
from __future__ import annotations

import time

import httpx

from agent_service.contracts import ServiceError, digest
from agent_service.model_route_preflight import validate_model_route

READINESS_URL = 'http://llm-service.llm.svc.cluster.local/health/ready'
MAX_READINESS_BYTES = 1024 * 1024
CONTRACT = 'video.picture-review-v2-model-route.v1'
PROFILE = 'video.picture-learning-review.v2'


def picture_review_expectation(revision):
    """The new operator contract fixes alias and target independently."""
    expected = dict(public_alias='llm-trusted', expected_configured_model='gpt-6.2-sol',
        expected_route_revision=revision, expected_upstream='custom', expected_tier='trusted',
        expected_wire_api='responses')
    # Validate the pinned expectation without reading a service or making work.
    if type(revision) is not str or not revision or len(revision) > 128:
        raise ServiceError('model_route_preflight_expectation_invalid', 422)
    return expected


def read_trusted_readiness(*, client=None):
    """GET one fixed endpoint; no token, redirects, environment proxy or retry."""
    owned = client is None
    client = client or httpx.Client(trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(10, connect=5))
    started = time.time()
    try:
        with client.stream('GET', READINESS_URL, follow_redirects=False,
                timeout=httpx.Timeout(10, connect=5)) as response:
            raw = bytearray()
            for part in response.iter_bytes(65536):
                if len(raw) + len(part) > MAX_READINESS_BYTES:
                    raise ServiceError('model_route_readiness_too_large', 503)
                raw.extend(part)
            from agent_service.profiles.video import decode_video_json
            body = decode_video_json(bytes(raw))
            return dict(http_status=response.status_code, body=body, started_at=started,
                observed_at=time.time(), origin=READINESS_URL)
    except ServiceError:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise ServiceError('model_route_readiness_unavailable', 503) from None
    finally:
        if owned:client.close()


def check_fresh_model_route(expectation, *, reader=None, clock=time.time):
    """The reader is an operator-owned transport, never task/model input."""
    began = clock()
    observation = (reader or read_trusted_readiness)()
    ended = clock()
    if (type(observation) is not dict or observation.get('origin') != READINESS_URL
            or any(type(observation.get(k)) not in (int, float) for k in ('started_at', 'observed_at'))
            or not began - 1 <= observation['started_at'] <= observation['observed_at'] <= ended + 1
            or ended - observation['started_at'] > 30):
        raise ServiceError('model_route_readiness_not_fresh', 503)
    evidence = validate_model_route(observation.get('body'),
        http_status=observation.get('http_status'), **expectation)
    return dict(evidence, origin=READINESS_URL, started_at=observation['started_at'],
        observed_at=observation['observed_at'], expectation_sha256=digest(expectation))


def candidate_model_route(config, contract, revision, *, reader=None):
    """Validate this explicit new contract before UID/job/grant side effects."""
    if contract != CONTRACT or config.profile.name != PROFILE or config.model.model != 'llm-trusted':
        raise ServiceError('candidate_model_route_contract_mismatch', 422)
    expectation = picture_review_expectation(revision)
    evidence = check_fresh_model_route(expectation, reader=reader)
    return dict(evidence, contract=CONTRACT,
        host_config_sha256=digest(config.model_dump(mode='json')))
