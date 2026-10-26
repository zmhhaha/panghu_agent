"""Render ordinary internal-service manifests without credentials or cluster writes."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import stat
import sys
import uuid


SERVICE = Path(__file__).resolve().parents[1]
NAME = re.compile(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z')
IMAGE = re.compile(r'[a-z0-9][a-z0-9./:_-]{0,330}@sha256:[0-9a-f]{64}\Z')
PUBLIC_FIELDS = frozenset({'schema_version', 'callers', 'task_contracts', 'capability_bundles',
    'tool_environments', 'execution_bindings', 'routes', 'workers', 'kubernetes_workers',
    'api', 'dispatcher', 'store', 'model_proxy'})
SECRET_FIELDS = frozenset({'database_url', 'tokens', 'receipt_keys', 'api_key', 'password',
    'authorization', 'client_secret', 'worker_token', 'receipt_key_hex', 'model_proxy_token'})


class RenderError(RuntimeError):
    pass


def ordinary(path, limit=4 * 1024 * 1024):
    path = Path(path).absolute()
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RenderError('linked_deployment_input')
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise RenderError('invalid_deployment_input')
    content = path.read_bytes()
    if len(content) != info.st_size:
        raise RenderError('deployment_input_changed')
    return content


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RenderError('duplicate_configuration_key')
        result[key] = value
    return result


def read_json(path):
    def constant(_):
        raise RenderError('invalid_configuration_number')
    result = json.loads(ordinary(path).decode('utf-8'), object_pairs_hook=pairs,
                        parse_constant=constant)
    if not isinstance(result, dict):
        raise RenderError('invalid_deployment_configuration')
    return result


def no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SECRET_FIELDS:
                raise RenderError('private_value_in_public_configuration')
            no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            no_secrets(item)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def cidrs(values):
    result = set()
    for value in values:
        network = ipaddress.ip_network(value, strict=True)
        if network.prefixlen == 0 or network.is_multicast or network.is_loopback:
            raise RenderError('unbounded_control_network')
        result.add(str(network))
    return sorted(result)


def output_file(directory, name, content):
    target = directory / name
    if target.exists() or target.is_symlink():
        ordinary(target, 16 * 1024 * 1024)
    with target.open('w', encoding='utf-8', newline='\n') as output:
        output.write(content)
        if not content.endswith('\n'):
            output.write('\n')


def render(*, config_path, control_image, output_dir, host_config_dir=None,
           kubernetes_api_cidrs=(), kubernetes_api_ports=(443, 6443),
           database_cidrs=(), database_port=5432):
    config = read_json(config_path)
    no_secrets(config)
    if (set(config) - PUBLIC_FIELDS or config.get('schema_version') != 2
            or config.get('workers') or not isinstance(config.get('callers'), list)
            or not isinstance(config.get('api'), dict) or not isinstance(config.get('model_proxy'), dict)
            or config.get('api', {}).get('host') != '0.0.0.0'
            or config.get('api', {}).get('port') != 8080):
        raise RenderError('kubernetes_configuration_required')
    if str(SERVICE.parent) not in sys.path:
        sys.path.insert(0, str(SERVICE.parent))
    from agent_service.config import _Public
    from agent_service.registry import DeploymentRegistry
    try:
        public = _Public.model_validate(config)
        deployment_registry = DeploymentRegistry.from_config(**{key: config.get(key, []) for key in
            ('task_contracts', 'capability_bundles', 'tool_environments', 'execution_bindings', 'routes')})
        registered = {record['name'] for record in public.task_contracts}
        if any(not set(caller.profiles) <= registered for caller in public.callers):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise RenderError('invalid_public_execution_catalog') from None
    if not isinstance(control_image, str) or not IMAGE.fullmatch(control_image):
        raise RenderError('immutable_control_image_required')
    api_cidrs, db_cidrs = cidrs(kubernetes_api_cidrs), cidrs(database_cidrs)
    api_ports = sorted(set(kubernetes_api_ports))
    if (not api_cidrs or not api_ports or any(type(p) is not int or not 1 <= p <= 65535 for p in api_ports)
            or type(database_port) is not int or not 1 <= database_port <= 65535):
        raise RenderError('explicit_control_endpoints_required')
    raw = canonical(config)
    config_name = 'agent-service-config-' + hashlib.sha256(raw.encode()).hexdigest()[:20]
    hosts = []
    seen = set()
    for pool in config.get('kubernetes_workers', []):
        name = pool.get('host_config_map', '')
        if not NAME.fullmatch(name) or pool.get('namespace') != 'agent-workers':
            raise RenderError('invalid_worker_pool_namespace_or_config')
        if name in seen:
            raise RenderError('worker_config_map_must_be_unique')
        seen.add(name)
        source = Path(host_config_dir) / name if host_config_dir else None
        if source is None or not source.exists():
            if pool.get('enabled'):
                raise RenderError('enabled_worker_host_configuration_required')
            continue
        host = read_json(source / 'config.json')
        no_secrets(host)
        if host.get('engine') != pool.get('engine'):
            raise RenderError('worker_host_engine_mismatch')
        # Old pools keep their original immutable catalogs for pinned job
        # recovery. Compare the selected binding's full policy, not unrelated
        # current routing arrays or the old host's enablement bit.
        from agent_service.worker_entry import HostConfig, WorkerConfigError, host_policy
        try:
            expected = deployment_registry.binding_policy(pool['binding_id'], pool['binding_revision'])
            actual = host_policy(HostConfig.model_validate(host))
            if (actual.pins() != expected.pins()
                    or actual.max_timeout_seconds != expected.max_timeout_seconds
                    or actual.supports_inputs != expected.supports_inputs):
                raise RenderError('worker_host_catalog_mismatch')
        except (ValueError, TypeError, KeyError, WorkerConfigError):
            raise RenderError('worker_host_catalog_mismatch') from None
        image_digest = pool.get('image', '').rsplit('@', 1)[-1]
        if host.get('profile', {}).get('image_digest') != image_digest:
            raise RenderError('worker_host_image_mismatch')
        data = {'config.json': canonical(host)}
        # These are independent operator records. Never manufacture acceptance
        # booleans or hash evidence during rendering/deployment.
        for filename in ('acceptance.json', 'acceptance-evidence.json'):
            candidate = source / filename
            if candidate.exists():
                data[filename] = ordinary(candidate).decode('utf-8')
            elif pool.get('enabled'):
                raise RenderError('enabled_worker_acceptance_required')
        hosts.append(dict(apiVersion='v1', kind='ConfigMap', immutable=True,
            metadata=dict(name=name, namespace='agent-workers'), data=data))
    directory = Path(output_dir).absolute()
    for part in (*reversed(directory.parents), directory):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise RenderError('linked_deployment_output')
    directory.mkdir(parents=True, exist_ok=True)
    replacements = {'__CONTROL_IMAGE__': control_image, '__CONFIG_NAME__': config_name,
                    '__PUBLIC_CONFIG_JSON__': json.dumps(raw, ensure_ascii=False)}
    workload = ordinary(SERVICE / 'deploy/agent-service.yaml').decode('utf-8').replace('\r\n', '\n')
    for key, value in replacements.items():
        workload = workload.replace(key, value)
    configuration, deployments = workload.split('\n---\n', 1)
    network = ordinary(SERVICE / 'deploy/control-network-policy.yaml').decode('utf-8')
    network = network.replace('__DATABASE_PEERS__', '\n'.join(
        '        - ipBlock: {cidr: ' + item + '}' for item in db_cidrs))
    network = network.replace('__DATABASE_PORT__', str(database_port))
    network = network.replace('__KUBERNETES_API_PEERS__', '\n'.join(
        '        - ipBlock: {cidr: ' + item + '}' for item in api_cidrs))
    network = network.replace('__KUBERNETES_API_PORTS__', canonical(
        [dict(protocol='TCP', port=port) for port in api_ports]))
    job_name = 'agent-service-init-' + uuid.uuid4().hex[:12]
    job = dict(apiVersion='batch/v1', kind='Job', metadata=dict(name=job_name, namespace='agent-service'),
        spec=dict(backoffLimit=0, activeDeadlineSeconds=300, ttlSecondsAfterFinished=3600,
            template=dict(metadata=dict(labels={'app.kubernetes.io/name': 'agent-execution-service',
                'app': 'agent-service', 'component': 'init-db'}), spec=dict(restartPolicy='Never',
                serviceAccountName='agent-model-proxy', automountServiceAccountToken=False,
                securityContext=dict(runAsNonRoot=True, runAsUser=10000, runAsGroup=10000, fsGroup=10000),
                containers=[dict(name='initialize', image=control_image,
                    args=['--config', '/etc/agent-service/config.json', '--secrets',
                          '/run/agent-service/secrets.json', 'init-db'],
                    securityContext=dict(readOnlyRootFilesystem=True, allowPrivilegeEscalation=False,
                        capabilities=dict(drop=['ALL']), seccompProfile=dict(type='RuntimeDefault')),
                    resources=dict(requests=dict(cpu='100m', memory='128Mi'), limits=dict(cpu='1', memory='1Gi')),
                    volumeMounts=[dict(name='config', mountPath='/etc/agent-service/config.json', subPath='config.json', readOnly=True),
                        dict(name='secrets', mountPath='/run/agent-service/secrets.json', subPath='secrets.json', readOnly=True),
                        dict(name='tmp', mountPath='/tmp')])],
                volumes=[dict(name='config', configMap=dict(name=config_name)),
                    dict(name='secrets', secret=dict(secretName='agent-service-runtime', defaultMode=288)),
                    dict(name='tmp', emptyDir=dict(sizeLimit='128Mi'))]))))
    output_file(directory, 'configuration.yaml', configuration)
    output_file(directory, 'workloads.yaml', deployments)
    output_file(directory, 'control-network-policy.yaml', network)
    output_file(directory, 'worker-hosts.json', canonical(dict(apiVersion='v1', kind='List', items=hosts)))
    output_file(directory, 'init-db.json', canonical(job))
    output_file(directory, 'deployment.json', canonical(dict(init_job=job_name, config_name=config_name)))
    return dict(init_job=job_name, config_name=config_name, output_dir=directory)


def arguments(parser):
    parser.add_argument('--config', default=str(SERVICE / 'config.production.json'))
    parser.add_argument('--control-image')
    parser.add_argument('--output-dir', default=str(SERVICE / 'rendered/deployment'))
    parser.add_argument('--host-config-dir', default=str(SERVICE / 'worker-hosts'))
    parser.add_argument('--kubernetes-api-cidr', action='append', default=[])
    parser.add_argument('--kubernetes-api-port', action='append', type=int, default=[])
    parser.add_argument('--database-cidr', action='append', default=[])
    parser.add_argument('--database-port', type=int, default=5432)


def from_arguments(args):
    image = args.control_image
    if image is None:
        image = ordinary(SERVICE / 'rendered/control-image.txt', 1024).decode('ascii').strip()
    return render(config_path=args.config, control_image=image, output_dir=args.output_dir,
        host_config_dir=args.host_config_dir, kubernetes_api_cidrs=args.kubernetes_api_cidr,
        kubernetes_api_ports=args.kubernetes_api_port or (443, 6443),
        database_cidrs=args.database_cidr, database_port=args.database_port)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    args = parser.parse_args(argv)
    try:
        result = from_arguments(args)
        print('Rendered internal Agent manifests: ' + str(result['output_dir']))
    except (OSError, ValueError, TypeError, KeyError, ImportError, RecursionError, RenderError):
        print('ERROR: Invalid or unavailable public deployment input; no cluster resources changed')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
