"""Apply rendered internal Agent resources using canonical External Secrets."""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import subprocess

from render import SERVICE, RenderError, arguments, from_arguments, ordinary


class DeploymentError(RuntimeError):
    pass


def kubectl(args, *, capture=False):
    result = subprocess.run(['kubectl', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=360, check=False)
    if result.returncode:
        raise DeploymentError('resource_operation_failed')
    if capture:
        if len(result.stdout) > 4 * 1024 * 1024:
            raise DeploymentError('resource_response_exceeds_bound')
        return json.loads(result.stdout)


def discover_api(args):
    # Both service VIP and post-DNAT endpoints are included, because CNI
    # enforcement may observe either. No credential/config Secret is read.
    service = kubectl(['-n', 'default', 'get', 'service', 'kubernetes', '-o', 'json'], capture=True)
    endpoints = kubectl(['-n', 'default', 'get', 'endpoints', 'kubernetes', '-o', 'json'], capture=True)
    addresses = set(service.get('spec', {}).get('clusterIPs', []))
    if service.get('spec', {}).get('clusterIP'):
        addresses.add(service['spec']['clusterIP'])
    ports = {443}
    for subset in endpoints.get('subsets', []):
        addresses.update(item['ip'] for item in subset.get('addresses', []))
        ports.update(item['port'] for item in subset.get('ports', []))
    args.kubernetes_api_cidr = [str(ipaddress.ip_network(address)) for address in sorted(addresses)]
    if not args.kubernetes_api_port:
        args.kubernetes_api_port = sorted(ports)


def diagnostics():
    # Never print Secret manifests, arbitrary command errors, environment or
    # Pod logs. Operators can inspect private logs through their normal tools.
    for namespace, label in (('agent-service', 'app.kubernetes.io/name=agent-execution-service'),
                             ('agent-workers', 'app=agent-capsule')):
        try:
            value = kubectl(['-n', namespace, 'get', 'pods', '-l', label, '-o', 'json'], capture=True)
            for item in value.get('items', []):
                name = item.get('metadata', {}).get('name', '')
                phase = item.get('status', {}).get('phase', 'Unknown')
                reasons = []
                for container in item.get('status', {}).get('containerStatuses', []):
                    for state in ('waiting', 'terminated'):
                        reason = container.get('state', {}).get(state, {}).get('reason')
                        if reason:
                            reasons.append(reason)
                print(namespace + '/' + name + ': ' + phase + (' (' + ', '.join(reasons) + ')' if reasons else ''))
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, DeploymentError):
            print(namespace + ': status unavailable')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument('--init-db', action='store_true', help='Initialize/migrate the dedicated database before rollout')
    parser.add_argument('--render-only', action='store_true', help='Write public manifests without any cluster requests')
    canonical = SERVICE.parent.parent / 'vault/inventory/agent-service-externalsecret.yaml'
    fallback = SERVICE / 'deploy/platform/vault/agent-service-externalsecret.yaml'
    parser.add_argument('--external-secret-file', default=str(canonical if canonical.exists() else fallback))
    args = parser.parse_args(argv)
    stage = 'rendering'
    try:
        if not args.kubernetes_api_cidr and not args.render_only:
            discover_api(args)
        result = from_arguments(args)
        directory = result['output_dir']
        if args.render_only:
            print('Rendered internal Agent manifests: ' + str(directory))
            return 0
        ordinary(args.external_secret_file)
        stage = 'namespace_and_rbac'
        kubectl(['apply', '-f', str(SERVICE / 'deploy/rbac.yaml')])
        stage = 'external_secret'
        kubectl(['apply', '-f', args.external_secret_file])
        for namespace, name in (('agent-service', 'agent-service-runtime'),
                                ('ppt-service', 'ppt-agent-service'),
                                ('scientific-agent', 'scientific-agent-service')):
            kubectl(['-n', namespace, 'wait', '--for=condition=Ready',
                     'externalsecret/' + name, '--timeout=120s'])
        stage = 'configuration_and_network'
        for filename in ('configuration.yaml', 'worker-hosts.json', 'control-network-policy.yaml'):
            kubectl(['apply', '-f', str(directory / filename)])
        kubectl(['apply', '-f', str(SERVICE / 'deploy/worker-network-policy.yaml')])
        if args.init_db:
            stage = 'database_initialization'
            kubectl(['apply', '-f', str(directory / 'init-db.json')])
            kubectl(['-n', 'agent-service', 'wait', '--for=condition=complete',
                     'job/' + result['init_job'], '--timeout=310s'])
        stage = 'control_rollout'
        kubectl(['apply', '-f', str(directory / 'workloads.yaml')])
        for deployment in ('agent-service-api', 'agent-service-dispatcher', 'agent-model-proxy'):
            kubectl(['-n', 'agent-service', 'rollout', 'status', 'deployment/' + deployment, '--timeout=180s'])
        print('Agent control service deployed in agent-service; reviewed worker routes keep their configured enablement.')
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError, RecursionError,
            subprocess.SubprocessError, RenderError, DeploymentError):
        print('ERROR: Agent deployment failed at ' + stage + '; credentials and private logs were not printed')
        if not args.render_only:
            diagnostics()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
