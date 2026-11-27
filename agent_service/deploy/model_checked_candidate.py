"""Actual opt-in candidate preparation/creation gates; no alternate model route.

Trusted operators supply their existing UID authority and one-shot create
callbacks. Both callbacks live inside the gate: invalid metadata cannot reserve
a UID or create a Pod. The runtime repeats the check before its private job and
grant; that later check does not claim to precede Pod creation.
"""
from __future__ import annotations

from copy import deepcopy

from agent_service.contracts import ServiceError, digest
from agent_service.model_route_readiness import CONTRACT, candidate_model_route


def prepare_checked_candidate(config, *, route_revision, reserve_uid, name, worker_image,
        node, bootstrap_secret, upstream_secret, scenario, source_dir, reader=None,
        upstream_key='token'):
    from agent_service.deploy.acceptance_probe import candidate_config, render_probe
    config, _, _ = candidate_config(config.model_dump(mode='json'))
    preflight = candidate_model_route(config, CONTRACT, route_revision, reader=reader)
    preflight['phase'] = 'before_uid_reservation'
    # This is the only call to the existing trusted UID authority. Failed
    # readiness/alias/model/revision checks never reach it.
    reservation = reserve_uid()
    expected = dict(name=name, profile=config.profile.name, node=node, reusable=False, uid_count=3)
    if (type(reservation) is not dict or any(reservation.get(k) != v for k, v in expected.items())
            or type(reservation.get('uid_start')) is not int
            or not 65536 <= reservation['uid_start'] <= 2**31 - 4):
        raise ServiceError('candidate_model_route_uid_reservation_invalid', 422)
    manifest = render_probe(config, name=name, worker_image=worker_image, node=node,
        uid_start=reservation['uid_start'], bootstrap_secret=bootstrap_secret,
        upstream_secret=upstream_secret, upstream_key=upstream_key, scenario=scenario,
        source_dir=source_dir, model_route_contract=CONTRACT,
        model_route_revision=route_revision, model_route_reader=reader)
    return dict(manifest=manifest, uid_reservation=deepcopy(reservation),
        before_uid_preflight=preflight, model_requests=0, job_created=False, pods_created=False)


def create_checked_candidate(config, manifest, *, route_revision, create_once, reader=None):
    """Fresh check immediately before the existing once-only Pod create call."""
    from agent_service.deploy.acceptance_probe import candidate_config
    config, _, _ = candidate_config(config.model_dump(mode='json'))
    expected_args = ['--model-route-contract', CONTRACT, '--model-route-revision', route_revision]
    pods = [row for row in manifest.get('items', []) if row.get('kind') == 'Pod']
    proxy = [row for row in pods if row.get('metadata', {}).get('namespace') == 'agent-service']
    if len(pods) != 2 or len(proxy) != 1:
        raise ServiceError('candidate_model_route_manifest_invalid', 422)
    containers = proxy[0].get('spec', {}).get('containers', [])
    if (len(containers) != 1 or containers[0].get('args', [])[-4:] != expected_args
            or containers[0]['args'].count('--model-route-contract') != 1
            or containers[0]['args'].count('--model-route-revision') != 1
            or any(row.get('metadata', {}).get('annotations', {}).get(
                'agent-service.panghuer.top/model-route-contract') != CONTRACT for row in pods)):
        raise ServiceError('candidate_model_route_manifest_invalid', 422)
    annotations = proxy[0]['metadata']['annotations']
    for key, value in (('public-alias', 'llm-trusted'), ('configured-model', 'gpt-6.2-sol'),
            ('revision', route_revision)):
        if annotations.get('agent-service.panghuer.top/model-route-' + key) != value:
            raise ServiceError('candidate_model_route_manifest_invalid', 422)
    from agent_service.profiles.video import decode_video_json
    for pod in pods:
        marks = pod['metadata']['annotations']
        proof = decode_video_json(marks.get('agent-service.panghuer.top/model-route-preflight', ''))
        if (type(proof) is not dict or digest(proof) != marks.get(
                'agent-service.panghuer.top/model-route-preflight-sha256')
                or proof.get('phase') != 'before_manifest' or proof.get('contract') != CONTRACT
                or proof.get('host_config_sha256') != digest(config.model_dump(mode='json'))
                or proof.get('public_alias') != 'llm-trusted'
                or proof.get('configured_model') != 'gpt-6.2-sol'
                or proof.get('route_revision') != route_revision):
            raise ServiceError('candidate_model_route_manifest_invalid', 422)
    maps = [row.get('data') for row in manifest['items'] if row.get('kind') == 'ConfigMap']
    if len(maps) != 2 or maps[0] != maps[1] or type(maps[0]) is not dict:
        raise ServiceError('candidate_model_route_manifest_invalid', 422)
    projected, _, _ = candidate_config(decode_video_json(maps[0].get('config.json', '')))
    projected_data = projected.model_dump(mode='json')
    projected_data['model']['proxy_base_url'] = config.model.proxy_base_url
    if projected_data != config.model_dump(mode='json'):
        raise ServiceError('candidate_model_route_manifest_invalid', 422)
    preflight = candidate_model_route(config, CONTRACT, route_revision, reader=reader)
    preflight['phase'] = 'before_pod_create'
    # The caller's sealed manifest and permanent one-shot intent remain its
    # responsibility. No callback, task, grant or network POST occurs earlier.
    outcome = create_once(deepcopy(manifest))
    return dict(before_pod_preflight=preflight, manifest_sha256=digest(manifest), outcome=outcome)
