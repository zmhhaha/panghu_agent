"""Validate public model routing metadata before creating work or spending.

This pure helper performs no I/O and cannot select a replacement alias. Its
caller supplies a fresh readiness response from a trusted service endpoint and
pins the intended backend model and route revision separately from the public
wire alias. Metadata validation is not a model call or proof of model quality.
"""
from __future__ import annotations

import re

from agent_service.contracts import ServiceError, canonical_bytes, digest


_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}\Z')
_REVISION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def _valid(value, pattern=_NAME):
    return type(value) is str and pattern.fullmatch(value) is not None


def validate_model_route(readiness, *, http_status, public_alias,
        expected_configured_model, expected_route_revision, expected_upstream=None,
        expected_tier=None, expected_wire_api=None):
    """Return bounded routing evidence or reject; never rewrite ``public_alias``.

    The caller must run this before a job/grant/Pod/model is created. HTTP status
    is mandatory so an error response cannot masquerade as ready metadata. The
    returned public alias remains the request's wire model; configured_model is
    only the separately verified backend target. A later route change requires
    a new check against the same expected revision, not automatic acceptance.
    """
    if (not _valid(public_alias) or not _valid(expected_configured_model)
            or not _valid(expected_route_revision, _REVISION)
            or any(value is not None and not _valid(value)
                   for value in (expected_upstream, expected_tier, expected_wire_api))):
        raise ServiceError('model_route_preflight_expectation_invalid', 422)
    if type(http_status) is not int or http_status != 200 or type(readiness) is not dict:
        raise ServiceError('model_route_preflight_not_ready', 503)
    if readiness.get('status') != 'ready':
        raise ServiceError('model_route_preflight_not_ready', 503)
    routes = readiness.get('routes')
    if type(routes) is not dict or not 1 <= len(routes) <= 256:
        raise ServiceError('model_route_preflight_routes_invalid', 503)
    # An effective model appearing behind some other alias is insufficient.
    # This exact check would reject gpt-6.2-sol when only llm-trusted is public.
    if public_alias not in routes:
        raise ServiceError('model_route_preflight_alias_unavailable', 503)
    selected = routes[public_alias]
    fields = ('requested_alias', 'configured_model', 'route_revision', 'resolved_upstream', 'tier')
    if (type(selected) is not dict or any(not _valid(selected.get(key),
            _REVISION if key == 'route_revision' else _NAME) for key in fields)
            or selected['requested_alias'] != public_alias
            or selected.get('wire_api') is not None and not _valid(selected['wire_api'])):
        raise ServiceError('model_route_preflight_metadata_invalid', 503)
    if selected['configured_model'] != expected_configured_model:
        raise ServiceError('model_route_preflight_target_mismatch', 409)
    if selected['route_revision'] != expected_route_revision:
        raise ServiceError('model_route_preflight_revision_mismatch', 409)
    for field, expected in (('resolved_upstream', expected_upstream),
            ('tier', expected_tier), ('wire_api', expected_wire_api)):
        if expected is not None and selected.get(field) != expected:
            raise ServiceError('model_route_preflight_' + field + '_mismatch', 409)
    try:
        if len(canonical_bytes(readiness)) > 1024 * 1024:
            raise ValueError('readiness too large')
        snapshot_sha256 = digest(readiness)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceError('model_route_preflight_routes_invalid', 503) from None
    return dict(schema='agent-model-route-preflight.v1', public_alias=public_alias,
        configured_model=selected['configured_model'], route_revision=selected['route_revision'],
        resolved_upstream=selected['resolved_upstream'], tier=selected['tier'],
        wire_api=selected.get('wire_api'), readiness_sha256=snapshot_sha256,
        model_requests=0, job_created=False, alias_rewritten=False,
        model_understanding_verified=False)
