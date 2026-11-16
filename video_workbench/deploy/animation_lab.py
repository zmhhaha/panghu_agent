"""Render isolated Kubernetes resources for the authenticated developer lab.

This command creates JSON only. It does not build, push, apply, create public
DNS/Casdoor configuration, or modify the ordinary video-workbench Deployment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from .render import _dns_name, _image, _labels


NAME = 'video-animation-lab'
PORT = 8766
DATA_CLAIM = NAME + '-data'
EVALUATIONS_CLAIM = NAME + '-evaluations'
SELECTOR = {'app.kubernetes.io/name': NAME}
CREATION_TOKEN_PATH = '/run/secrets/animation-creation/token'


@dataclass(frozen=True)
class Settings:
    image: str
    image_digest: str
    release_id: str
    developer_users: Sequence[str]
    storage_class: str
    namespace: str = 'video-workbench'
    public_origin: str = 'https://video-service.panghuer.top'
    base_path: str = '/dev/animations'
    proxy_namespace: str = 'oauth'
    proxy_labels: Mapping[str, str] = field(default_factory=lambda: {'app': 'oauth2-proxy-video-workbench'})
    data_size: str = '2Gi'
    evaluations_size: str = '2Gi'
    creation_namespace: str | None = None
    creation_name: str | None = None
    creation_port: int | None = None
    creation_labels: Mapping[str, str] | None = None
    creation_token_secret: str | None = None
    creation_token_key: str = 'token'


def render(settings: Settings):
    namespace = _dns_name(settings.namespace, 'namespace', namespace=True)
    proxy_namespace = _dns_name(settings.proxy_namespace, 'proxy namespace', namespace=True)
    storage_class = _dns_name(settings.storage_class, 'storage class')
    proxy_labels = _labels(settings.proxy_labels, 'proxy labels')
    image = _image(settings.image, settings.image_digest)
    if type(settings.release_id) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', settings.release_id):
        raise ValueError('Invalid lab release ID')
    if (type(settings.developer_users) not in (list, tuple) or not settings.developer_users
            or any(type(user) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,199}', user)
                   for user in settings.developer_users)
            or len(set(settings.developer_users)) != len(settings.developer_users)):
        raise ValueError('Exact nonempty developer user allowlist required')
    if type(settings.public_origin) is not str:
        raise ValueError('Lab public origin must be a canonical HTTPS origin')
    origin = urlsplit(settings.public_origin)
    if (origin.scheme != 'https' or not origin.hostname or origin.path or origin.query or origin.fragment
            or origin.username or origin.password or settings.public_origin != 'https://' + origin.netloc):
        raise ValueError('Lab public origin must be a canonical HTTPS origin')
    _dns_name(origin.hostname, 'public origin host')
    try:
        if origin.port is not None and not 1 <= origin.port <= 65535:
            raise ValueError()
    except ValueError:
        raise ValueError('Invalid lab origin port') from None
    if type(settings.base_path) is not str or not re.fullmatch(r'(?:/[a-z][a-z0-9-]*)+', settings.base_path):
        raise ValueError('Invalid lab base path')
    for size in (settings.data_size, settings.evaluations_size):
        if type(size) is not str or not re.fullmatch(r'[1-9][0-9]*(?:Mi|Gi|Ti)', size):
            raise ValueError('Lab storage size must be positive Mi/Gi/Ti')
    creation_fields = (settings.creation_namespace, settings.creation_name, settings.creation_port,
                       settings.creation_labels, settings.creation_token_secret)
    creation_enabled = any(value is not None for value in creation_fields)
    creation = None
    if creation_enabled:
        if any(value is None for value in creation_fields):
            raise ValueError('Creation executor namespace, name, port, labels and dedicated token Secret must be supplied together')
        creation_namespace = _dns_name(settings.creation_namespace, 'creation namespace', namespace=True)
        creation_name = _dns_name(settings.creation_name, 'creation Service name', namespace=True)
        if not re.fullmatch(r'[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?', creation_name):
            raise ValueError('Invalid creation Service name')
        if type(settings.creation_port) is not int or not 1 <= settings.creation_port <= 65535:
            raise ValueError('Invalid creation HTTP port')
        creation_labels = _labels(settings.creation_labels, 'creation Pod labels')
        creation_secret = _dns_name(settings.creation_token_secret, 'creation caller Secret')
        if type(settings.creation_token_key) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', settings.creation_token_key):
            raise ValueError('Invalid creation caller Secret key')
        creation = dict(namespace=creation_namespace, labels=creation_labels, secret=creation_secret,
                        origin=f'http://{creation_name}.{creation_namespace}.svc.cluster.local:{settings.creation_port}')
    elif settings.creation_token_key != 'token':
        raise ValueError('Creation token key requires the complete executor configuration')

    labels = {**SELECTOR, 'app.kubernetes.io/component': 'developer-lab',
              'app.kubernetes.io/version': settings.release_id}
    annotations = {'org.opencontainers.image.revision': settings.release_id,
                   'org.opencontainers.image.digest': settings.image_digest,
                   'org.opencontainers.image.ref.name': settings.image}

    def obj(api, kind, name, spec):
        return dict(apiVersion=api, kind=kind,
                    metadata=dict(name=name, namespace=namespace, labels=dict(labels),
                                  annotations=dict(annotations)), spec=spec)

    args = ['--data-dir', '/data', '--assets-dir', '/opt/animation-lab-assets',
            '--evaluations-dir', '/evaluations', '--port', str(PORT),
            '--proxy-mode', '--listen', '0.0.0.0', '--public-origin', settings.public_origin,
            '--base-path', settings.base_path]
    for user in settings.developer_users:
        args.extend(['--developer-user', user])
    if creation:
        args.extend(['--creation-url', creation['origin'], '--creation-token-file', CREATION_TOKEN_PATH])
    probe = {'httpGet': {'path': '/healthz', 'port': 'http',
                        'httpHeaders': [{'name': 'Host', 'value': origin.netloc}]},
             'periodSeconds': 10, 'timeoutSeconds': 3, 'failureThreshold': 3}
    security = {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                'fsGroup': 10001, 'fsGroupChangePolicy': 'OnRootMismatch',
                'seccompProfile': {'type': 'RuntimeDefault'}}
    deployment = obj('apps/v1', 'Deployment', NAME, {
        'replicas': 1, 'strategy': {'type': 'Recreate'},
        'selector': {'matchLabels': dict(SELECTOR)},
        'template': {'metadata': {'labels': dict(labels), 'annotations': dict(annotations)}, 'spec': {
            'automountServiceAccountToken': False, 'securityContext': security,
            'terminationGracePeriodSeconds': 30,
            'containers': [{
                'name': 'animation-lab', 'image': image, 'imagePullPolicy': 'Always',
                'command': ['python', '-m', 'video_workbench.animation_lab'], 'args': args,
                'ports': [{'name': 'http', 'containerPort': PORT}],
                'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                    'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '100m', 'memory': '256Mi', 'ephemeral-storage': '32Mi'},
                              'limits': {'cpu': '1', 'memory': '1Gi', 'ephemeral-storage': '128Mi'}},
                'startupProbe': {**probe, 'periodSeconds': 5, 'failureThreshold': 36},
                'readinessProbe': dict(probe), 'livenessProbe': dict(probe),
                'volumeMounts': [{'name': 'data', 'mountPath': '/data'},
                                 {'name': 'evaluations', 'mountPath': '/evaluations', 'readOnly': True},
                                 {'name': 'temporary', 'mountPath': '/tmp'}],
            }],
            'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': DATA_CLAIM}},
                        {'name': 'evaluations', 'persistentVolumeClaim': {'claimName': EVALUATIONS_CLAIM}},
                        {'name': 'temporary', 'emptyDir': {'sizeLimit': '32Mi'}}],
        }},
    })
    if creation:
        pod = deployment['spec']['template']['spec']
        pod['containers'][0]['volumeMounts'].append({
            'name': 'creation-caller-token', 'mountPath': CREATION_TOKEN_PATH.rsplit('/', 1)[0], 'readOnly': True,
        })
        # This is only the lab-to-executor credential. Agent credentials stay
        # with the separately deployed executor, never in the lab container.
        pod['volumes'].append({'name': 'creation-caller-token', 'secret': {
            'secretName': creation['secret'], 'optional': False, 'defaultMode': 0o440,
            'items': [{'key': settings.creation_token_key, 'path': 'token'}],
        }})
    claims = [obj('v1', 'PersistentVolumeClaim', name, {
        'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem',
        'storageClassName': storage_class, 'resources': {'requests': {'storage': size}},
    }) for name, size in ((DATA_CLAIM, settings.data_size), (EVALUATIONS_CLAIM, settings.evaluations_size))]
    service = obj('v1', 'Service', NAME, {
        'type': 'ClusterIP', 'selector': dict(SELECTOR),
        'ports': [{'name': 'http', 'port': PORT, 'targetPort': 'http'}],
    })
    policy = obj('networking.k8s.io/v1', 'NetworkPolicy', NAME, {
        'podSelector': {'matchLabels': dict(SELECTOR)}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [{
            'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': proxy_namespace}},
            'podSelector': {'matchLabels': dict(proxy_labels)},
        }], 'ports': [{'protocol': 'TCP', 'port': PORT}]}],
        'egress': [],
    })
    if creation:
        policy['spec']['egress'] = [
            {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                     'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
             'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
            {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': creation['namespace']}},
                     'podSelector': {'matchLabels': dict(creation['labels'])}}],
             'ports': [{'protocol': 'TCP', 'port': settings.creation_port}]},
        ]
    return dict(apiVersion='v1', kind='List', items=[*claims, deployment, service, policy])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--image-digest', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--developer-user', action='append', required=True)
    parser.add_argument('--storage-class', required=True)
    parser.add_argument('--namespace', default='video-workbench')
    parser.add_argument('--public-origin', default='https://video-service.panghuer.top')
    parser.add_argument('--base-path', default='/dev/animations')
    parser.add_argument('--proxy-namespace', default='oauth')
    parser.add_argument('--proxy-label', action='append', default=[])
    parser.add_argument('--data-size', default='2Gi')
    parser.add_argument('--evaluations-size', default='2Gi')
    parser.add_argument('--creation-namespace', help='optional private creation executor namespace; requires all creation settings')
    parser.add_argument('--creation-name', help='creation executor Service DNS label; no URL or external hostname')
    parser.add_argument('--creation-port', type=int, help='creation HTTP Service and selected Pod port')
    parser.add_argument('--creation-label', action='append', default=[], help='exact executor Pod selector KEY=VALUE; repeat as needed')
    parser.add_argument('--creation-token-secret', help='existing dedicated lab-to-executor token Secret in the lab namespace; never an Agent credential')
    parser.add_argument('--creation-token-key', default='token', help='single caller token key from the dedicated Secret (default: token)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    labels = None
    if args.proxy_label:
        labels = {}
        for value in args.proxy_label:
            key, separator, item = value.partition('=')
            if not separator or key in labels:
                parser.error('--proxy-label requires distinct KEY=VALUE entries')
            labels[key] = item
    creation_labels = None
    if args.creation_label:
        creation_labels = {}
        for value in args.creation_label:
            key, separator, item = value.partition('=')
            if not separator or key in creation_labels:
                parser.error('--creation-label requires distinct KEY=VALUE entries')
            creation_labels[key] = item
    settings = Settings(image=args.image, image_digest=args.image_digest,
        release_id=args.release_id, developer_users=args.developer_user,
        storage_class=args.storage_class, namespace=args.namespace,
        public_origin=args.public_origin, base_path=args.base_path,
        proxy_namespace=args.proxy_namespace, data_size=args.data_size,
        evaluations_size=args.evaluations_size,
        creation_namespace=args.creation_namespace, creation_name=args.creation_name,
        creation_port=args.creation_port, creation_labels=creation_labels,
        creation_token_secret=args.creation_token_secret, creation_token_key=args.creation_token_key,
        **({'proxy_labels': labels} if labels is not None else {}))
    raw = json.dumps(render(settings), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf8') as stream:
            stream.write(raw)
    else:
        print(raw, end='')


if __name__ == '__main__':
    main()
