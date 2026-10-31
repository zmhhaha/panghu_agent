"""Render internal Kubernetes resources without cluster access or credentials.

Images retain the required latest tag and are pinned to an operator-provided
digest. This module does not build, push, apply, provision credentials, or create
public routing and authentication infrastructure.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import ipaddress
import json
from pathlib import Path
import re
from typing import Mapping, Sequence


NAME = 'video-workbench'
PORT = 8089
TOKEN_PATH = '/run/secrets/video-agent/token'
SELECTOR = {'app.kubernetes.io/name': NAME}
DEFAULT_AGENT_LABELS = {
    'app.kubernetes.io/name': 'agent-execution-service', 'component': 'api',
}
_DNS_LABEL = r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?'
_LABEL_NAME = r'[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?'


def _dns_name(value: str, field_name: str, *, namespace=False) -> str:
    maximum = 63 if namespace else 253
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or not all(re.fullmatch(_DNS_LABEL, part) for part in value.split('.'))
            or namespace and '.' in value):
        raise ValueError(f'Invalid {field_name}')
    return value


def _labels(value: Mapping[str, str], field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f'{field_name} must select at least one Pod label')
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or key.count('/') > 1:
            raise ValueError(f'Invalid {field_name} key')
        prefix, _, name = key.rpartition('/')
        if '/' in key and not prefix:
            raise ValueError(f'Invalid {field_name} key prefix')
        if prefix:
            _dns_name(prefix, field_name + ' key prefix')
        if (not name or not re.fullmatch(_LABEL_NAME, name)
                or not isinstance(item, str) or len(item) > 63
                or item and not re.fullmatch(_LABEL_NAME, item)):
            raise ValueError(f'Invalid {field_name}')
        result[key] = item
    return result


def _hosts(values: Sequence[str]) -> list[str]:
    if isinstance(values, str) or not values:
        raise ValueError('At least one allowed host is required')
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError('Invalid allowed host')
        if value.startswith('[') and value.endswith(']'):
            try:
                ipaddress.IPv6Address(value[1:-1])
            except ValueError:
                raise ValueError('Invalid allowed IPv6 host') from None
        else:
            _dns_name(value, 'allowed host')
        if value not in result:
            result.append(value)
    return result


def _image(image: str, digest: str) -> str:
    if (not isinstance(image, str) or not re.fullmatch(
            r'[a-z0-9][a-z0-9._:/-]*:latest', image)
            or '//' in image or '..' in image or '@' in image):
        raise ValueError('Image must be a repository:latest reference without a digest')
    repository = image[:-len(':latest')]
    components = repository.split('/')
    for index, component in enumerate(components):
        # A port is allowed only on a registry component followed by a path.
        if index == 0 and ':' in component and len(components) > 1:
            hostname, port = component.rsplit(':', 1)
            _dns_name(hostname, 'image registry hostname')
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError('Invalid image registry port')
        elif not re.fullmatch(r'[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*', component):
            raise ValueError('Invalid image repository')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Image digest must be sha256:<64 lowercase hex>')
    return image + '@' + digest


@dataclass(frozen=True)
class Settings:
    image: str
    image_digest: str
    release_id: str
    allowed_hosts: Sequence[str]
    proxy_namespace: str
    proxy_labels: Mapping[str, str]
    storage_class: str
    namespace: str = 'video-workbench'
    agent_namespace: str = 'agent-service'
    agent_service: str = 'agent-service'
    agent_labels: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_AGENT_LABELS))
    agent_port: int = 8080
    profile_revision: str = '1'
    token_secret: str = 'video-agent-service'
    token_secret_key: str = 'token'
    storage_size: str = '1Gi'
    include_agent_ingress: bool = False
    external_secret_store: str | None = None
    external_secret_store_kind: str = 'ClusterSecretStore'
    external_secret_remote_key: str | None = None
    external_secret_property: str | None = None
    speech_enabled: bool = False
    speech_namespace: str = 'speech-service'
    speech_service: str = 'speech-service'
    speech_model: str = 'kokoro-rk3588'
    speech_voice: str = 'zm_yunjian'
    speech_token_secret: str = 'video-speech-service'
    render_enabled: bool = False


def render(settings: Settings) -> dict:
    """Return a Kubernetes List; validation is local and has no side effects."""
    namespace = _dns_name(settings.namespace, 'namespace', namespace=True)
    proxy_namespace = _dns_name(settings.proxy_namespace, 'proxy namespace', namespace=True)
    agent_namespace = _dns_name(settings.agent_namespace, 'Agent namespace', namespace=True)
    agent_service = _dns_name(settings.agent_service, 'Agent Service', namespace=True)
    secret = _dns_name(settings.token_secret, 'caller Secret')
    storage_class = _dns_name(settings.storage_class, 'storage class')
    proxy_labels = _labels(settings.proxy_labels, 'proxy labels')
    agent_labels = _labels(settings.agent_labels, 'Agent labels')
    hosts = _hosts(settings.allowed_hosts)
    image = _image(settings.image, settings.image_digest)
    if not isinstance(settings.release_id, str) or not re.fullmatch(_LABEL_NAME, settings.release_id):
        raise ValueError('Release ID must be a nonempty Kubernetes label value (up to 63 characters)')
    if type(settings.agent_port) is not int or not 1 <= settings.agent_port <= 65535:
        raise ValueError('Invalid Agent port')
    if not isinstance(settings.token_secret_key, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', settings.token_secret_key):
        raise ValueError('Invalid caller Secret key')
    if not isinstance(settings.profile_revision, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', settings.profile_revision):
        raise ValueError('Invalid profile revision')
    if not isinstance(settings.storage_size, str) or not re.fullmatch(r'[1-9][0-9]*(?:Mi|Gi|Ti)', settings.storage_size):
        raise ValueError('Storage size must be a positive whole Mi, Gi or Ti quantity')
    external_values = (settings.external_secret_store, settings.external_secret_remote_key,
                       settings.external_secret_property)
    if any(value is not None for value in external_values):
        if not all(isinstance(value, str) and value for value in external_values):
            raise ValueError('ExternalSecret requires store, remote key and property together')
        _dns_name(settings.external_secret_store, 'ExternalSecret store')
        if settings.external_secret_store_kind not in {'ClusterSecretStore', 'SecretStore'}:
            raise ValueError('Invalid ExternalSecret store kind')
        if (not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*', settings.external_secret_remote_key)
                or any(part in {'.', '..'} for part in settings.external_secret_remote_key.split('/'))):
            raise ValueError('Invalid ExternalSecret remote key')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', settings.external_secret_property):
            raise ValueError('Invalid ExternalSecret property')

    labels = {**SELECTOR, 'app.kubernetes.io/component': 'workbench',
              'app.kubernetes.io/version': settings.release_id}
    annotations = {'org.opencontainers.image.revision': settings.release_id,
                   'org.opencontainers.image.digest': settings.image_digest,
                   'org.opencontainers.image.ref.name': settings.image}

    def obj(api, kind, name, spec=None, *, target_namespace=namespace):
        metadata = dict(name=name, labels=dict(labels))
        if target_namespace is not None:
            metadata['namespace'] = target_namespace
        result = dict(apiVersion=api, kind=kind, metadata=metadata)
        if spec is not None:
            result['spec'] = spec
        return result

    def peer(peer_namespace, peer_labels):
        # Both selectors belong to one peer: AND, rather than namespace OR Pod.
        return {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': peer_namespace}},
                'podSelector': {'matchLabels': peer_labels}}

    def probe(path):
        return {'httpGet': {'path': path, 'port': 'http',
                            'httpHeaders': [{'name': 'Host', 'value': hosts[0]}]},
                'periodSeconds': 10, 'timeoutSeconds': 3, 'failureThreshold': 3}

    deployment = obj('apps/v1', 'Deployment', NAME, {
        'replicas': 1, 'strategy': {'type': 'Recreate'},
        'selector': {'matchLabels': dict(SELECTOR)},
        'template': {'metadata': {'labels': dict(labels), 'annotations': dict(annotations)},
                     'spec': {
            'automountServiceAccountToken': False,
            'terminationGracePeriodSeconds': 30,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                'fsGroup': 10001, 'fsGroupChangePolicy': 'OnRootMismatch',
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{
                'name': 'workbench', 'image': image, 'imagePullPolicy': 'Always',
                'command': ['python', '-m', 'video_workbench'],
                'args': ['--host', '0.0.0.0', '--port', str(PORT)],
                'ports': [{'name': 'http', 'containerPort': PORT}],
                'env': [
                    {'name': 'VIDEO_WORKBENCH_DB', 'value': '/data/workbench.db'},
                    {'name': 'VIDEO_WORKBENCH_HOSTS', 'value': ','.join(hosts)},
                    {'name': 'VIDEO_AGENT_URL', 'value': f'http://{agent_service}.{agent_namespace}.svc.cluster.local:{settings.agent_port}'},
                    {'name': 'VIDEO_AGENT_TOKEN_FILE', 'value': TOKEN_PATH},
                    {'name': 'VIDEO_AGENT_PROFILE_REVISION', 'value': settings.profile_revision},
                ],
                'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                    'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '100m', 'memory': '128Mi', 'ephemeral-storage': '32Mi'},
                              'limits': {'cpu': '1', 'memory': '512Mi', 'ephemeral-storage': '128Mi'}},
                'startupProbe': {**probe('/healthz'), 'periodSeconds': 5, 'failureThreshold': 30},
                'livenessProbe': probe('/healthz'), 'readinessProbe': probe('/readyz'),
                'volumeMounts': [{'name': 'data', 'mountPath': '/data'},
                                 {'name': 'temporary', 'mountPath': '/tmp'},
                                 {'name': 'agent-token', 'mountPath': '/run/secrets/video-agent', 'readOnly': True}],
            }],
            'volumes': [
                {'name': 'data', 'persistentVolumeClaim': {'claimName': NAME}},
                {'name': 'temporary', 'emptyDir': {'sizeLimit': '32Mi'}},
                {'name': 'agent-token', 'secret': {'secretName': secret, 'defaultMode': 0o440,
                                                  'items': [{'key': settings.token_secret_key, 'path': 'token'}]}},
            ],
        }},
    })
    deployment['metadata']['annotations'] = dict(annotations)
    pvc = obj('v1', 'PersistentVolumeClaim', NAME, {
        'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem',
        'storageClassName': storage_class,
        'resources': {'requests': {'storage': settings.storage_size}},
    })
    service = obj('v1', 'Service', NAME, {
        'type': 'ClusterIP', 'selector': dict(SELECTOR),
        'ports': [{'name': 'http', 'port': PORT, 'targetPort': 'http'}],
    })
    policy = obj('networking.k8s.io/v1', 'NetworkPolicy', NAME, {
        'podSelector': {'matchLabels': dict(SELECTOR)}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [peer(proxy_namespace, proxy_labels)],
                     'ports': [{'protocol': 'TCP', 'port': PORT}]}],
        'egress': [
            {'to': [peer('kube-system', {'k8s-app': 'kube-dns'})],
             'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
            {'to': [peer(agent_namespace, agent_labels)],
             'ports': [{'protocol': 'TCP', 'port': settings.agent_port}]},
        ],
    })
    items = [obj('v1', 'Namespace', namespace, target_namespace=None), pvc, deployment, service, policy]
    if type(settings.speech_enabled) is not bool or type(settings.render_enabled) is not bool:
        raise ValueError('Media feature switches must be booleans')
    if settings.speech_enabled:
        speech_namespace = _dns_name(settings.speech_namespace, 'speech namespace', namespace=True)
        speech_service = _dns_name(settings.speech_service, 'speech Service', namespace=True)
        speech_secret = _dns_name(settings.speech_token_secret, 'speech Secret')
        for value in (settings.speech_model, settings.speech_voice):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', value):
                raise ValueError('Invalid speech model or voice')
        template = deployment['spec']['template']
        template['metadata']['labels']['speech-client'] = 'true'
        container = template['spec']['containers'][0]
        container['env'].extend([
            {'name': 'VIDEO_TTS_URL', 'value': f'http://{speech_service}.{speech_namespace}.svc.cluster.local/v1'},
            {'name': 'VIDEO_TTS_MODEL', 'value': settings.speech_model},
            {'name': 'VIDEO_TTS_VOICE', 'value': settings.speech_voice},
            {'name': 'VIDEO_TTS_TOKEN_FILE', 'value': '/run/secrets/video-speech/token'},
        ])
        container['volumeMounts'].append({'name': 'speech-token', 'mountPath': '/run/secrets/video-speech', 'readOnly': True})
        template['spec']['volumes'].append({'name': 'speech-token', 'secret': {
            'secretName': speech_secret, 'defaultMode': 0o440, 'items': [{'key': 'token', 'path': 'token'}]}})
        policy['spec']['egress'].append({'to': [peer(speech_namespace, {'app': 'speech-service'})],
                                        'ports': [{'protocol': 'TCP', 'port': 8092}]})
        template['spec']['terminationGracePeriodSeconds'] = 150
    if settings.render_enabled:
        container = deployment['spec']['template']['spec']['containers'][0]
        container['env'].extend([{'name': 'VIDEO_RENDER_ENABLED', 'value': '1'},
                                 {'name': 'VIDEO_STATIC_RENDERER', 'value': 'weasyprint'}])
        container['resources'] = {'requests': {'cpu': '250m', 'memory': '256Mi', 'ephemeral-storage': '128Mi'},
                                  'limits': {'cpu': '2', 'memory': '1536Mi', 'ephemeral-storage': '2Gi'}}
        for volume in deployment['spec']['template']['spec']['volumes']:
            if volume['name'] == 'temporary':
                volume['emptyDir']['sizeLimit'] = '1Gi'
    if settings.external_secret_store is not None:
        items.append(obj('external-secrets.io/v1', 'ExternalSecret', secret, {
            'refreshInterval': '5m',
            'secretStoreRef': {'name': settings.external_secret_store, 'kind': settings.external_secret_store_kind},
            'target': {'name': secret, 'creationPolicy': 'Owner'},
            'data': [{'secretKey': settings.token_secret_key, 'remoteRef': {
                'key': settings.external_secret_remote_key, 'property': settings.external_secret_property}}],
        }))
    if settings.include_agent_ingress:
        items.append(obj('networking.k8s.io/v1', 'NetworkPolicy', f'{NAME}-from-{namespace}', {
            'podSelector': {'matchLabels': agent_labels}, 'policyTypes': ['Ingress'],
            'ingress': [{'from': [peer(namespace, dict(SELECTOR))],
                         'ports': [{'protocol': 'TCP', 'port': settings.agent_port}]}],
        }, target_namespace=agent_namespace))
    return {'apiVersion': 'v1', 'kind': 'List', 'items': items}


def _parse_labels(values, default=None):
    if values is None:
        return dict(default or {})
    result = {}
    for value in values:
        if '=' not in value:
            raise ValueError('Labels must use KEY=VALUE')
        key, item = value.split('=', 1)
        if key in result:
            raise ValueError('Repeated label keys are not allowed')
        result[key] = item
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Registry/repository:latest; do not include a digest')
    parser.add_argument('--image-digest', required=True, help='Actual pushed image digest: sha256:<64 hex>')
    parser.add_argument('--release-id', required=True, help='Public release revision, e.g. a Git commit ID')
    parser.add_argument('--namespace', default='video-workbench')
    parser.add_argument('--allowed-host', dest='allowed_hosts', action='append', required=True,
                        help='Repeat for each allowed Host; no scheme, path or port. First host is used by probes.')
    parser.add_argument('--proxy-namespace', required=True)
    parser.add_argument('--proxy-label', action='append', required=True, help='Repeat KEY=VALUE selectors for the trusted proxy')
    parser.add_argument('--storage-class', required=True)
    parser.add_argument('--storage-size', default='1Gi')
    parser.add_argument('--agent-namespace', default='agent-service')
    parser.add_argument('--agent-service', default='agent-service')
    parser.add_argument('--agent-label', action='append', help='Repeat KEY=VALUE; overrides the default Agent API selectors')
    parser.add_argument('--agent-port', type=int, default=8080)
    parser.add_argument('--profile-revision', default='1')
    parser.add_argument('--token-secret', default='video-agent-service')
    parser.add_argument('--token-secret-key', default='token')
    parser.add_argument('--include-agent-ingress', action='store_true',
                        help='Also render a narrowly scoped ingress policy in the Agent namespace')
    parser.add_argument('--external-secret-store')
    parser.add_argument('--external-secret-store-kind', default='ClusterSecretStore',
                        choices=('ClusterSecretStore', 'SecretStore'))
    parser.add_argument('--external-secret-remote-key')
    parser.add_argument('--external-secret-property')
    parser.add_argument('--speech-enabled', action='store_true')
    parser.add_argument('--speech-namespace', default='speech-service')
    parser.add_argument('--speech-service', default='speech-service')
    parser.add_argument('--speech-model', default='kokoro-rk3588')
    parser.add_argument('--speech-voice', default='zm_yunjian')
    parser.add_argument('--speech-token-secret', default='video-speech-service')
    parser.add_argument('--render-enabled', action='store_true')
    parser.add_argument('--output', type=Path, help='Write JSON (also valid Kubernetes YAML); default: stdout')
    args = parser.parse_args(argv)
    try:
        values = vars(args).copy()
        output = values.pop('output')
        values['proxy_labels'] = _parse_labels(values.pop('proxy_label'))
        values['agent_labels'] = _parse_labels(values.pop('agent_label'), DEFAULT_AGENT_LABELS)
        document = render(Settings(**values))
    except ValueError as exc:
        parser.error(str(exc))
    content = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding='utf-8')
    else:
        print(content, end='')


if __name__ == '__main__':
    main()
