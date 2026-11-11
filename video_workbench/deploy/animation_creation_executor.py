"""Generate private root/Docker-controller resources without cluster access.

This executor is NOT a restricted renderer. It owns the host Docker socket and
separate Agent/Lab credentials. Generated scenes only execute in the existing
networkless uid10001 child renderer, never in this trusted API process.

The node must already have a local Docker daemon and the pinned renderer image.
Its dedicated data directory is mounted at the SAME absolute host/container
path so Docker bind mounts resolve the exact run files. Provision both distinct
token Secrets in the executor namespace separately; this emits no Secret values,
SA/RBAC, public routes, image builds, model calls or apply operations.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Mapping

from .render import _dns_name, _image, _labels


NAME = 'video-animation-creation'
PORT = 8767
NODE = 'arm-cluster-master'
DATA_ROOT = '/var/lib/video-animation-creation'
DOCKER_SOCKET = '/var/run/docker.sock'
AGENT_TOKEN = '/run/secrets/animation-creation-agent/token'
LAB_TOKEN = '/run/secrets/animation-creation-lab/token'
METHOD_TOKEN = '/run/secrets/animation-method-agent/token'
SELECTOR = {'app.kubernetes.io/name': NAME}


@dataclass(frozen=True)
class Settings:
    image: str
    image_digest: str
    renderer_image: str
    renderer_image_digest: str
    release_id: str
    agent_token_secret: str
    lab_token_secret: str
    namespace: str = NAME
    lab_namespace: str = 'video-workbench'
    lab_labels: Mapping[str, str] = field(default_factory=lambda: {'app.kubernetes.io/name': 'video-animation-lab'})
    agent_namespace: str = 'agent-service'
    agent_name: str = 'agent-service'
    agent_port: int = 8080
    agent_labels: Mapping[str, str] = field(default_factory=lambda: {
        'app.kubernetes.io/name': 'agent-execution-service', 'component': 'api'})
    agent_token_key: str = 'token'
    lab_token_key: str = 'token'
    method_agent_token_secret: str | None = None
    method_agent_token_key: str = 'token'


def render(settings: Settings):
    """Pure generation only; host prerequisites remain operator-owned."""
    namespace = _dns_name(settings.namespace, 'executor namespace', namespace=True)
    lab_namespace = _dns_name(settings.lab_namespace, 'lab namespace', namespace=True)
    agent_namespace = _dns_name(settings.agent_namespace, 'Agent namespace', namespace=True)
    agent_name = _dns_name(settings.agent_name, 'Agent Service name', namespace=True)
    if not re.fullmatch(r'[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?', agent_name):
        raise ValueError('Invalid Agent Service name')
    agent_labels = _labels(settings.agent_labels, 'Agent Pod labels')
    lab_labels = _labels(settings.lab_labels, 'Lab Pod labels')
    image = _image(settings.image, settings.image_digest)
    renderer_image = _image(settings.renderer_image, settings.renderer_image_digest)
    if type(settings.agent_port) is not int or not 1 <= settings.agent_port <= 65535:
        raise ValueError('Invalid Agent HTTP port')
    if type(settings.release_id) is not str or not re.fullmatch(r'[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?', settings.release_id):
        raise ValueError('Invalid executor release ID')
    agent_secret = _dns_name(settings.agent_token_secret, 'scoped Agent caller Secret')
    lab_secret = _dns_name(settings.lab_token_secret, 'Lab caller Secret')
    if agent_secret == lab_secret:
        raise ValueError('Distinct Agent and Lab caller Secrets required')
    method_secret = (_dns_name(settings.method_agent_token_secret, 'method Agent caller Secret')
                     if settings.method_agent_token_secret is not None else None)
    if method_secret in {agent_secret, lab_secret}:
        raise ValueError('Distinct method Agent caller Secret required')
    for key in (settings.agent_token_key, settings.lab_token_key, settings.method_agent_token_key):
        if type(key) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', key):
            raise ValueError('Invalid caller Secret key')

    labels = {**SELECTOR, 'app.kubernetes.io/component': 'private-creation-executor',
              'app.kubernetes.io/version': settings.release_id}
    annotations = {
        'org.opencontainers.image.revision': settings.release_id,
        'org.opencontainers.image.digest': settings.image_digest,
        'org.opencontainers.image.ref.name': settings.image,
        'video-service.panghuer.top/renderer-image': renderer_image,
        'video-service.panghuer.top/trust-boundary': 'root-docker-controller-not-restricted-renderer',
        'video-service.panghuer.top/host-data-root': DATA_ROOT,
    }

    def resource(api, kind, spec):
        return dict(apiVersion=api, kind=kind,
            metadata=dict(name=NAME, namespace=namespace, labels=dict(labels), annotations=dict(annotations)), spec=spec)

    args = ['--root', DATA_ROOT,
            '--agent-url', f'http://{agent_name}.{agent_namespace}.svc.cluster.local:{settings.agent_port}',
            '--agent-token-file', AGENT_TOKEN, '--token-file', LAB_TOKEN,
            '--renderer-image', renderer_image, '--enable-isolated-rendering',
            '--listen', '0.0.0.0', '--port', str(PORT)]
    if method_secret:
        args += ['--method-agent-token-file', METHOD_TOKEN]
    health = {'httpGet': {'path': '/healthz', 'port': 'http'}, 'periodSeconds': 10,
              'timeoutSeconds': 3, 'failureThreshold': 3}
    container = {
        'name': 'creation-executor', 'image': image, 'imagePullPolicy': 'Always',
        'command': ['python', '-m', 'video_workbench.animation_creation_executor'], 'args': args,
        'ports': [{'name': 'http', 'containerPort': PORT}],
        # The socket grants host Docker authority even with these kernel flags.
        # This pod is deliberately root, and must not be labelled restricted.
        # Existing _ownership chowns output to uid10001 then chmods it; only
        # CHOWN/FOWNER are restored here, never in the untrusted child renderer.
        'securityContext': {'runAsNonRoot': False, 'runAsUser': 0, 'runAsGroup': 0,
            'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
            'capabilities': {'drop': ['ALL'], 'add': ['CHOWN', 'FOWNER']}},
        'env': [{'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'},
                {'name': 'PYTHONUNBUFFERED', 'value': '1'},
                {'name': 'DOCKER_HOST', 'value': 'unix://' + DOCKER_SOCKET},
                {'name': 'DOCKER_CONFIG', 'value': '/tmp/docker-config'}],
        'resources': {'requests': {'cpu': '250m', 'memory': '512Mi', 'ephemeral-storage': '64Mi'},
                      'limits': {'cpu': '2', 'memory': '2Gi', 'ephemeral-storage': '512Mi'}},
        'startupProbe': {**health, 'periodSeconds': 5, 'failureThreshold': 36},
        'readinessProbe': dict(health), 'livenessProbe': dict(health),
        'volumeMounts': [
            {'name': 'executions', 'mountPath': DATA_ROOT},
            {'name': 'docker-socket', 'mountPath': DOCKER_SOCKET},
            {'name': 'temporary', 'mountPath': '/tmp'},
            {'name': 'agent-caller-token', 'mountPath': AGENT_TOKEN.rsplit('/', 1)[0], 'readOnly': True},
            {'name': 'lab-caller-token', 'mountPath': LAB_TOKEN.rsplit('/', 1)[0], 'readOnly': True},
        ],
    }
    volumes = [
        {'name': 'executions', 'hostPath': {'path': DATA_ROOT, 'type': 'DirectoryOrCreate'}},
        {'name': 'docker-socket', 'hostPath': {'path': DOCKER_SOCKET, 'type': 'Socket'}},
        {'name': 'temporary', 'emptyDir': {'sizeLimit': '256Mi'}},
    ]
    for name, secret, key in (('agent-caller-token', agent_secret, settings.agent_token_key),
                              ('lab-caller-token', lab_secret, settings.lab_token_key)):
        volumes.append({'name': name, 'secret': {'secretName': secret, 'optional': False,
            'defaultMode': 0o400, 'items': [{'key': key, 'path': 'token'}]}})
    if method_secret:
        volumes.append({'name': 'method-agent-token', 'secret': {'secretName': method_secret, 'optional': False,
            'defaultMode': 0o400, 'items': [{'key': settings.method_agent_token_key, 'path': 'token'}]}})
        container['volumeMounts'].append({'name': 'method-agent-token',
            'mountPath': METHOD_TOKEN.rsplit('/', 1)[0], 'readOnly': True})
    deployment = resource('apps/v1', 'Deployment', {
        'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': dict(SELECTOR)},
        'template': {'metadata': {'labels': dict(labels), 'annotations': dict(annotations)}, 'spec': {
            'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'nodeSelector': {'kubernetes.io/hostname': NODE, 'kubernetes.io/arch': 'arm64'},
            'securityContext': {'runAsNonRoot': False, 'runAsUser': 0, 'runAsGroup': 0,
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'terminationGracePeriodSeconds': 240,
            'initContainers': [{
                'name': 'prepare-private-root', 'image': image, 'imagePullPolicy': 'Always',
                'command': ['python', '-c', f'import os; os.chmod({DATA_ROOT!r}, 0o700)'],
                'securityContext': {'runAsNonRoot': False, 'runAsUser': 0, 'runAsGroup': 0,
                    'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                    'capabilities': {'drop': ['ALL'], 'add': ['FOWNER']}},
                'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                              'limits': {'cpu': '100m', 'memory': '128Mi'}},
                'volumeMounts': [{'name': 'executions', 'mountPath': DATA_ROOT}],
            }],
            'containers': [container], 'volumes': volumes,
        }},
    })
    service = resource('v1', 'Service', {'type': 'ClusterIP', 'selector': dict(SELECTOR),
        'ports': [{'name': 'http', 'port': PORT, 'targetPort': 'http'}]})

    def peer(ns, pod_labels):
        return {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': ns}},
                'podSelector': {'matchLabels': dict(pod_labels)}}

    policy = resource('networking.k8s.io/v1', 'NetworkPolicy', {
        'podSelector': {'matchLabels': dict(SELECTOR)}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [peer(lab_namespace, lab_labels)], 'ports': [{'protocol': 'TCP', 'port': PORT}]}],
        'egress': [
            {'to': [peer('kube-system', {'k8s-app': 'kube-dns'})],
             'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
            {'to': [peer(agent_namespace, agent_labels)], 'ports': [{'protocol': 'TCP', 'port': settings.agent_port}]},
        ],
    })
    return dict(apiVersion='v1', kind='List', items=[deployment, service, policy])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('image', 'image-digest', 'renderer-image', 'renderer-image-digest',
                 'release-id', 'agent-token-secret', 'lab-token-secret'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--namespace', default=NAME)
    parser.add_argument('--lab-namespace', default='video-workbench')
    parser.add_argument('--lab-label', action='append', default=[])
    parser.add_argument('--agent-namespace', default='agent-service')
    parser.add_argument('--agent-name', default='agent-service')
    parser.add_argument('--agent-port', type=int, default=8080)
    parser.add_argument('--agent-label', action='append', default=[])
    parser.add_argument('--agent-token-key', default='token')
    parser.add_argument('--lab-token-key', default='token')
    parser.add_argument('--method-agent-token-secret')
    parser.add_argument('--method-agent-token-key', default='token')
    parser.add_argument('--output', type=Path)
    args = vars(parser.parse_args(argv))
    output = args.pop('output')
    for name in ('lab', 'agent'):
        entries = args.pop(name + '_label')
        if entries:
            labels = {}
            for value in entries:
                key, separator, item = value.partition('=')
                if not separator or key in labels:
                    parser.error('--' + name + '-label requires distinct KEY=VALUE entries')
                labels[key] = item
            args[name + '_labels'] = labels
    try:
        document = render(Settings(**args))
    except ValueError as error:
        parser.error(str(error))
    raw = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    if output:
        with output.open('x', encoding='utf8') as stream:
            stream.write(raw)
    else:
        print(raw, end='')


if __name__ == '__main__':
    main()
