"""Render/review the execution extension; never replace website/database/owner."""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
NAME = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')
IMAGE = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}\Z')
ENV_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
CONTROLLER = 'ppt-hermes-controller'


def metadata(name, namespace):
    return {'name': name, 'namespace': namespace,
            'labels': {'app.kubernetes.io/part-of': 'ppt-service'}}


def policy(name, namespace, selector, *, ingress=None, egress=None):
    spec = {'podSelector': selector, 'policyTypes': []}
    if ingress is not None:
        spec.update(ingress=ingress)
        spec['policyTypes'].append('Ingress')
    if egress is not None:
        spec.update(egress=egress)
        spec['policyTypes'].append('Egress')
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': metadata(name, namespace), 'spec': spec}


def render(*, controller_image, worker_image, kube_api_cidr, kube_api_port=(6443,),
           kube_api_server='https://192.168.137.101:6443', chart_oracle_configmap=None,
           namespace='ppt-service', pvc='ppt-data', provider_secret='ppt-model-upstream',
           provider_secret_key='PPT_UPSTREAM_API_KEY', worker_service_account='ppt-worker',
           pvc_data_prefix='state', enabled=False):
    """Configuration is public. The provider key only reaches each proxy Pod."""
    for value in (namespace, pvc, provider_secret, worker_service_account):
        if not isinstance(value, str) or not NAME.fullmatch(value):
            raise ValueError('invalid_kubernetes_resource_name')
    if worker_service_account == CONTROLLER:
        raise ValueError('worker_service_account_must_be_unprivileged')
    if not ENV_KEY.fullmatch(provider_secret_key):
        raise ValueError('invalid_provider_secret_key')
    if (not isinstance(pvc_data_prefix, str) or not pvc_data_prefix
            or any(not NAME.fullmatch(part) for part in pvc_data_prefix.split('/'))):
        raise ValueError('invalid_pvc_data_prefix')
    for value in (controller_image, worker_image):
        if not isinstance(value, str) or not IMAGE.fullmatch(value):
            raise ValueError('registry_manifest_digest_required')
    if controller_image == worker_image:
        raise ValueError('separate_controller_and_worker_images_required')
    networks = []
    for value in kube_api_cidr:
        network = ipaddress.ip_network(value, strict=True)
        if network.prefixlen != network.max_prefixlen or network.network_address.is_unspecified:
            raise ValueError('kubernetes_api_requires_exact_host_cidr')
        networks.append(str(network))
    if not networks:
        raise ValueError('kubernetes_api_address_required')
    ports = sorted(set(kube_api_port))
    if not ports or any(type(value) is not int or not 1 <= value <= 65535 for value in ports):
        raise ValueError('invalid_kubernetes_api_port')
    api = urlsplit(kube_api_server)
    if (api.scheme != 'https' or not api.hostname or api.username or api.password
            or api.path not in ('', '/') or api.query or api.fragment):
        raise ValueError('invalid_kubernetes_api_server')
    address = ipaddress.ip_address(api.hostname)
    if not any(address in ipaddress.ip_network(value) for value in networks) or (api.port or 443) not in ports:
        raise ValueError('kubernetes_api_server_not_allowed_by_policy')
    if chart_oracle_configmap is not None and not NAME.fullmatch(chart_oracle_configmap):
        raise ValueError('invalid_chart_oracle_configmap')
    documents = []
    for name, token in ((CONTROLLER, True), (worker_service_account, False)):
        documents.append({'apiVersion': 'v1', 'kind': 'ServiceAccount',
                          'metadata': metadata(name, namespace), 'automountServiceAccountToken': token})
    documents += [
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role',
         'metadata': metadata(CONTROLLER, namespace), 'rules': [
             {'apiGroups': [''], 'resources': ['pods'],
              'verbs': ['get', 'list', 'create', 'delete', 'patch']},
             {'apiGroups': [''], 'resources': ['secrets'],
              'verbs': ['get', 'create', 'delete']},
             {'apiGroups': ['networking.k8s.io'], 'resources': ['networkpolicies'],
              'verbs': ['get', 'list', 'create', 'delete']},
         ]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding',
         'metadata': metadata(CONTROLLER, namespace),
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': CONTROLLER},
         'subjects': [{'kind': 'ServiceAccount', 'name': CONTROLLER, 'namespace': namespace}]},
    ]
    environment = {
        'DATA_ROOT': '/var/lib/ppt-service/' + pvc_data_prefix,
        'PPT_CONTROL_STATE': 'configured' if enabled else 'disabled',
        'PPT_ALLOW_PAID_EXECUTION': 'true' if enabled else 'false',
        'PPT_NETWORK_POLICY_APPROVED': 'true' if enabled else 'false',
        'PPT_EXECUTION_BACKEND': 'kubernetes',
        'PPT_KUBE_API_SERVER': kube_api_server,
        'PPT_KUBE_NAMESPACE': namespace, 'PPT_DATA_PVC': pvc,
        'PPT_PVC_DATA_PREFIX': pvc_data_prefix, 'PPT_WORKER_IMAGE': worker_image,
        'PPT_PROVIDER_SECRET': provider_secret, 'PPT_PROVIDER_SECRET_KEY': provider_secret_key,
        'PPT_WORKER_SERVICE_ACCOUNT': worker_service_account,
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1', 'HOME': '/tmp',
    }
    if chart_oracle_configmap:
        environment['PPT_CHART_ORACLE_FILE'] = '/etc/ppt-oracle/oracles.json'
    labels = {'app': CONTROLLER, 'app.kubernetes.io/name': CONTROLLER,
              'app.kubernetes.io/part-of': 'ppt-service'}
    container = {
        'name': 'controller', 'image': controller_image, 'imagePullPolicy': 'IfNotPresent',
        'command': ['/usr/local/bin/python'], 'args': ['-m', 'ppt_service.control.dispatcher'],
        'workingDir': '/opt/ppt-service',
        'env': [{'name': key, 'value': value} for key, value in sorted(environment.items())] + [
            {'name': 'DATABASE_URL', 'valueFrom': {'secretKeyRef': {
                'name': 'ppt-database-runtime', 'key': 'DATABASE_URL'}}}],
        'securityContext': {'runAsUser': 10000, 'runAsGroup': 10000, 'runAsNonRoot': True,
                            'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                            'capabilities': {'drop': ['ALL']}},
        'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                      'limits': {'cpu': '1', 'memory': '1Gi'}},
        'volumeMounts': [{'name': 'data', 'mountPath': '/var/lib/ppt-service'},
                         {'name': 'tmp', 'mountPath': '/tmp'}],
    }
    if chart_oracle_configmap:
        # subPath is a regular bind-mounted file; a projected directory's
        # symlink layout is deliberately rejected by the no-follow loader.
        container['volumeMounts'].append({'name': 'chart-oracle',
            'mountPath': '/etc/ppt-oracle/oracles.json', 'subPath': 'oracles.json', 'readOnly': True})
    documents.append({'apiVersion': 'apps/v1', 'kind': 'Deployment',
        'metadata': metadata(CONTROLLER, namespace), 'spec': {
            'replicas': 1, 'strategy': {'type': 'Recreate'},
            'selector': {'matchLabels': {'app': CONTROLLER}},
            'template': {'metadata': {'labels': labels}, 'spec': {
                'serviceAccountName': CONTROLLER, 'automountServiceAccountToken': True,
                'nodeSelector': {'kubernetes.io/arch': 'arm64'},
                'terminationGracePeriodSeconds': 60,
                'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [container], 'volumes': [
                    {'name': 'data', 'persistentVolumeClaim': {'claimName': pvc}},
                    {'name': 'tmp', 'emptyDir': {'sizeLimit': '128Mi'}}],
            }}}})
    if chart_oracle_configmap:
        documents[-1]['spec']['template']['spec']['volumes'].append({
            'name': 'chart-oracle', 'configMap': {'name': chart_oracle_configmap,
                'items': [{'key': 'oracles.json', 'path': 'oracles.json'}]}})
    # NetworkPolicy rules are additive. Replace the website baseline's broad
    # dns-egress selector, so no worker/proxy/sandbox inherits DNS access.
    documents.append(policy('dns-egress', namespace, {'matchExpressions': [{
        'key': 'app.kubernetes.io/name', 'operator': 'In',
        'values': ['ppt-site', 'ppt-migrator', CONTROLLER]}]}, egress=[{
            'to': [{'namespaceSelector': {'matchLabels': {
                'kubernetes.io/metadata.name': 'kube-system'}},
                'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
            'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]}]))
    documents.append(policy('hermes-controller-egress', namespace, {'matchLabels': {'app': CONTROLLER}},
        egress=[{'to': [{'namespaceSelector': {'matchLabels': {
                            'kubernetes.io/metadata.name': 'data'}},
                        'podSelector': {'matchLabels': {'app': 'postgres'}}}],
                 'ports': [{'protocol': 'TCP', 'port': 5432}]},
                {'to': [{'ipBlock': {'cidr': network}} for network in sorted(set(networks))],
                 'ports': [{'protocol': 'TCP', 'port': port} for port in ports]}]))
    # Shared data/postgres owns its ingress policy. Never replace that policy
    # or give workers/proxies database access through this execution extension.
    documents.append(policy('hermes-task-default-deny', namespace, {'matchExpressions': [{
        'key': 'app', 'operator': 'In',
        'values': ['ppt-hermes-worker', 'ppt-model-proxy', 'ppt-sandbox']}]}, ingress=[], egress=[]))
    return {'apiVersion': 'v1', 'kind': 'List', 'items': documents}


def kubectl(args, *, runner=subprocess.run, body=None):
    result = runner(['kubectl', *args], input=body, text=True, capture_output=True, check=False)
    if result.returncode:
        # kubectl stderr can contain resources or inline credential-bearing input.
        operation = next((value for value in args if value in ('get', 'apply', 'rollout')), 'unknown')
        raise RuntimeError('kubernetes_command_failed_' + operation)
    return result.stdout


def deploy(manifest, *, runner=subprocess.run, enable_site=False):
    """Only add runtime resources after validating the established installation."""
    deployment = next(item for item in manifest['items'] if item['kind'] == 'Deployment')
    namespace = deployment['metadata']['namespace']
    pod = deployment['spec']['template']['spec']
    environment = {item['name']: item.get('value') for item in pod['containers'][0]['env']}
    if enable_site and any(environment.get(name) != value for name, value in (
            ('PPT_CONTROL_STATE', 'configured'), ('PPT_ALLOW_PAID_EXECUTION', 'true'),
            ('PPT_NETWORK_POLICY_APPROVED', 'true'))):
        raise ValueError('enable_site_requires_enabled_controller')
    kubectl(['get', 'namespace', namespace, '-o', 'name'], runner=runner)
    kubectl(['-n', namespace, 'get', 'deployment', 'ppt-site', '-o', 'name'], runner=runner)
    pvc = json.loads(kubectl(['-n', namespace, 'get', 'pvc', environment['PPT_DATA_PVC'], '-o', 'json'], runner=runner))
    if pvc.get('status', {}).get('phase') != 'Bound' or 'ReadWriteMany' not in pvc.get('spec', {}).get('accessModes', []):
        raise RuntimeError('existing_bound_rwx_pvc_required')
    for name, key in [('ppt-database-runtime', 'DATABASE_URL'),
                      (environment['PPT_PROVIDER_SECRET'], environment['PPT_PROVIDER_SECRET_KEY'])]:
        # Keep all Secret contents private; never print these results.
        secret = json.loads(kubectl(['-n', namespace, 'get', 'secret', name, '-o', 'json'], runner=runner))
        if not secret.get('data', {}).get(key):
            raise RuntimeError('required_secret_key_unavailable')
    kubectl(['apply', '--dry-run=server', '-f', '-'], runner=runner, body=json.dumps(manifest))
    kubectl(['apply', '-f', '-'], runner=runner, body=json.dumps(manifest))
    kubectl(['-n', namespace, 'rollout', 'status', 'deployment/' + CONTROLLER, '--timeout=180s'], runner=runner)
    if enable_site:
        kubectl(['-n', namespace, 'patch', 'configmap', 'ppt-site', '--type=merge', '-p', json.dumps({
            'data': {'PPT_EXECUTION_ENABLED': 'true', 'PPT_EXECUTION_BACKEND': 'kubernetes'}})], runner=runner)
        kubectl(['-n', namespace, 'rollout', 'restart', 'deployment/ppt-site'], runner=runner)
        kubectl(['-n', namespace, 'rollout', 'status', 'deployment/ppt-site', '--timeout=180s'], runner=runner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller-image', required=True)
    parser.add_argument('--worker-image', required=True)
    parser.add_argument('--kube-api-cidr', action='append', required=True,
                        help='Exact /32 or /128 host; repeat for Service and node API endpoints')
    parser.add_argument('--kube-api-port', action='append', type=int,
                        help='Repeat for API ports, defaults to 6443')
    parser.add_argument('--kube-api-server', default='https://192.168.137.101:6443')
    parser.add_argument('--chart-oracle-configmap', help='Reviewed oracles.json, mounted as a regular read-only file')
    parser.add_argument('--namespace', default='ppt-service')
    parser.add_argument('--pvc', default='ppt-data')
    parser.add_argument('--provider-secret', default='ppt-model-upstream')
    parser.add_argument('--provider-secret-key', default='PPT_UPSTREAM_API_KEY')
    parser.add_argument('--worker-service-account', default='ppt-worker')
    parser.add_argument('--pvc-data-prefix', default='state')
    parser.add_argument('--enable-execution', action='store_true', dest='enabled',
                        help='Allow paid queue execution after actual CNI isolation checks')
    parser.add_argument('--output', type=Path, default=HERE / 'rendered/runtime.json')
    parser.add_argument('--apply', action='store_true', help='Apply this extension; website is unchanged')
    parser.add_argument('--enable-site', action='store_true', help='Explicitly enable website after enabled controller rollout')
    args = vars(parser.parse_args())
    output = args.pop('output')
    apply = args.pop('apply')
    enable_site = args.pop('enable_site')
    if enable_site and (not apply or not args['enabled']):
        raise ValueError('enable_site_requires_apply_and_enabled_controller')
    if args['kube_api_port'] is None:
        args['kube_api_port'] = (6443,)
    manifest = render(**args)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Reviewable execution manifest: ' + str(output))
    if apply:
        deploy(manifest, enable_site=enable_site)
        print('Execution extension rolled out.' + (' Website execution enabled.' if enable_site else ' Website admission is unchanged.'))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        code = str(exc) if re.fullmatch(r'[a-z_]+', str(exc)) else type(exc).__name__
        raise SystemExit('PPT execution deployment failed: ' + code) from None
