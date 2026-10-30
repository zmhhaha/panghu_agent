"""Render only the internal login proxy and its isolated Redis session store.

Casdoor callbacks and public Cloudflare/domain configuration remain user-owned.
This generator never reads credentials, creates Secrets, or applies resources.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Mapping

from .render import _dns_name, _hosts, _image, _labels


PROXY = 'oauth2-proxy-video-workbench'
REDIS = 'video-workbench-session-redis'
PROXY_LABELS = {'app': PROXY}
REDIS_LABELS = {'app': REDIS}
WORKBENCH_LABELS = {'app.kubernetes.io/name': 'video-workbench'}
OIDC_INGRESS_LABELS = {
    'app.kubernetes.io/name': 'ingress-nginx',
    'app.kubernetes.io/instance': 'ingress-nginx',
    'app.kubernetes.io/component': 'controller',
}

# The password is never placed in process arguments or a public ConfigMap. The
# operator provisions an independent 32..128-character alphanumeric credential.
REDIS_START = '''umask 077
case "$REDIS_PASSWORD" in
  ''|*[!A-Za-z0-9]*) echo 'Invalid session credential format' >&2; exit 1 ;;
esac
[ "${#REDIS_PASSWORD}" -ge 32 ] && [ "${#REDIS_PASSWORD}" -le 128 ] || exit 1
printf 'bind 0.0.0.0\\nport 6379\\nprotected-mode yes\\nsave ""\\nappendonly no\\ndir /tmp\\nmaxmemory 32mb\\nmaxmemory-policy allkeys-lru\\nrequirepass %s\\n' "$REDIS_PASSWORD" > /tmp/redis.conf
unset REDIS_PASSWORD
exec redis-server /tmp/redis.conf
'''


@dataclass(frozen=True)
class OAuthSettings:
    host: str
    proxy_image: str
    proxy_image_digest: str
    redis_image: str
    redis_image_digest: str
    release_id: str
    namespace: str = 'oauth'
    oidc_secret: str = 'oauth2-proxy-secret'
    session_secret: str = 'video-workbench-oauth-runtime'
    upstream_namespace: str = 'video-workbench'
    upstream_service: str = 'video-workbench'
    upstream_port: int = 8089
    upstream_labels: Mapping[str, str] = field(default_factory=lambda: dict(WORKBENCH_LABELS))
    entry_namespace: str = 'default'
    entry_labels: Mapping[str, str] = field(default_factory=lambda: {'app': 'cloudflared', 'tunnel': 'main'})
    oidc_egress_namespace: str = 'ingress-nginx'
    oidc_egress_labels: Mapping[str, str] = field(default_factory=lambda: dict(OIDC_INGRESS_LABELS))
    oidc_egress_port: int = 443


def render(settings: OAuthSettings) -> dict:
    namespace = _dns_name(settings.namespace, 'proxy namespace', namespace=True)
    upstream_namespace = _dns_name(settings.upstream_namespace, 'upstream namespace', namespace=True)
    upstream_service = _dns_name(settings.upstream_service, 'upstream Service', namespace=True)
    entry_namespace = _dns_name(settings.entry_namespace, 'entry namespace', namespace=True)
    oidc_namespace = _dns_name(settings.oidc_egress_namespace, 'OIDC egress namespace', namespace=True)
    oidc_secret = _dns_name(settings.oidc_secret, 'OIDC Secret')
    session_secret = _dns_name(settings.session_secret, 'session Secret')
    if oidc_secret == session_secret:
        raise ValueError('Session credentials must use a dedicated Secret')
    host, = _hosts([settings.host])
    if host.startswith('[') or re.fullmatch(r'[0-9.]+', host) or '.' not in host:
        raise ValueError('Login host must be an explicit DNS hostname')
    if type(settings.upstream_port) is not int or not 1 <= settings.upstream_port <= 65535:
        raise ValueError('Invalid upstream port')
    if type(settings.oidc_egress_port) is not int or not 1 <= settings.oidc_egress_port <= 65535:
        raise ValueError('Invalid OIDC egress port')
    if not isinstance(settings.release_id, str) or not re.fullmatch(r'[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?', settings.release_id):
        raise ValueError('Release ID must be a nonempty Kubernetes label value')
    entry_labels = _labels(settings.entry_labels, 'entry labels')
    upstream_labels = _labels(settings.upstream_labels, 'upstream labels')
    oidc_labels = _labels(settings.oidc_egress_labels, 'OIDC egress labels')
    proxy_image = _image(settings.proxy_image, settings.proxy_image_digest)
    redis_image = _image(settings.redis_image, settings.redis_image_digest)
    upstream = f'http://{upstream_service}.{upstream_namespace}.svc.cluster.local:{settings.upstream_port}'

    def obj(api, kind, name, spec):
        return {'apiVersion': api, 'kind': kind,
                'metadata': {'name': name, 'namespace': namespace}, 'spec': spec}

    def secret_env(name, secret_name, key):
        return {'name': name, 'valueFrom': {'secretKeyRef': {'name': secret_name, 'key': key}}}

    def peer(peer_namespace, labels):
        return {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': peer_namespace}},
                'podSelector': {'matchLabels': labels}}

    def deployment(name, labels, image, digest, container):
        return obj('apps/v1', 'Deployment', name, {
            'replicas': 1, 'selector': {'matchLabels': labels},
            'template': {'metadata': {
                'labels': {**labels, 'app.kubernetes.io/version': settings.release_id},
                'annotations': {'org.opencontainers.image.revision': settings.release_id,
                                'org.opencontainers.image.digest': digest,
                                'org.opencontainers.image.ref.name': image.split('@', 1)[0]}},
                'spec': {'automountServiceAccountToken': False,
                         'terminationGracePeriodSeconds': 30,
                         'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                             'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
                         'containers': [{**container, 'image': image, 'imagePullPolicy': 'Always',
                             'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                                 'capabilities': {'drop': ['ALL']}},
                             'volumeMounts': [{'name': 'temporary', 'mountPath': '/tmp'}]}],
                         'volumes': [{'name': 'temporary', 'emptyDir': {'sizeLimit': '4Mi'}}]}}})

    proxy = deployment(PROXY, dict(PROXY_LABELS), proxy_image, settings.proxy_image_digest, {
        'name': 'oauth2-proxy',
        # Standard v7.8+ options: OIDC session User remains the subject identifier.
        # pass-user-headers + strip-auth headers overwrite inbound owner headers.
        'args': [
            '--provider=oidc', '--oidc-issuer-url=$(OIDC_ISSUER_URL)', '--client-id=$(OIDC_CLIENT_ID)',
            '--scope=openid email profile', '--oidc-email-claim=email', '--email-domain=$(ALLOWED_DOMAINS)',
            '--redirect-url=https://' + host + '/oauth2/callback', '--http-address=0.0.0.0:4180',
            '--upstream=' + upstream, '--pass-host-header=true', '--pass-user-headers=true',
            '--skip-auth-strip-headers=true', '--reverse-proxy=true', '--skip-provider-button=true',
            '--cookie-name=__Host-video_workbench', '--cookie-path=/', '--cookie-secure=true',
            '--cookie-samesite=lax', '--cookie-csrf-per-request=true', '--cookie-csrf-expire=5m',
            '--cookie-expire=168h', '--cookie-refresh=24h', '--session-store-type=redis',
            f'--redis-connection-url=redis://{REDIS}.{namespace}.svc.cluster.local:6379/0',
            '--silence-ping-logging=true',
        ],
        'env': [
            secret_env('OIDC_CLIENT_ID', oidc_secret, 'OIDC_CLIENT_ID'),
            secret_env('OIDC_ISSUER_URL', oidc_secret, 'OIDC_ISSUER_URL'),
            secret_env('ALLOWED_DOMAINS', oidc_secret, 'ALLOWED_DOMAINS'),
            secret_env('OAUTH2_PROXY_CLIENT_SECRET', oidc_secret, 'OIDC_CLIENT_SECRET'),
            secret_env('OAUTH2_PROXY_COOKIE_SECRET', session_secret, 'COOKIE_SECRET'),
            secret_env('OAUTH2_PROXY_REDIS_PASSWORD', session_secret, 'REDIS_PASSWORD'),
        ],
        'ports': [{'name': 'http', 'containerPort': 4180}],
        'startupProbe': {'httpGet': {'path': '/ping', 'port': 'http'}, 'periodSeconds': 5, 'failureThreshold': 30},
        'readinessProbe': {'httpGet': {'path': '/ping', 'port': 'http'}, 'periodSeconds': 10},
        'livenessProbe': {'httpGet': {'path': '/ping', 'port': 'http'}, 'periodSeconds': 30},
        'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '200m', 'memory': '256Mi'}},
    })
    redis = deployment(REDIS, dict(REDIS_LABELS), redis_image, settings.redis_image_digest, {
        'name': 'redis', 'command': ['/bin/sh', '-ec', REDIS_START],
        'env': [secret_env('REDIS_PASSWORD', session_secret, 'REDIS_PASSWORD')],
        'ports': [{'name': 'redis', 'containerPort': 6379}],
        'startupProbe': {'tcpSocket': {'port': 'redis'}, 'periodSeconds': 5, 'failureThreshold': 12},
        'readinessProbe': {'tcpSocket': {'port': 'redis'}, 'periodSeconds': 10},
        'livenessProbe': {'tcpSocket': {'port': 'redis'}, 'periodSeconds': 30},
        'resources': {'requests': {'cpu': '10m', 'memory': '16Mi'}, 'limits': {'cpu': '100m', 'memory': '96Mi'}},
    })
    redis['spec']['strategy'] = {'type': 'Recreate'}
    services = [obj('v1', 'Service', name, {'type': 'ClusterIP', 'selector': labels,
                'ports': [{'name': port_name, 'port': port, 'targetPort': port_name}]})
                for name, labels, port_name, port in ((PROXY, PROXY_LABELS, 'http', 4180), (REDIS, REDIS_LABELS, 'redis', 6379))]
    proxy_policy = obj('networking.k8s.io/v1', 'NetworkPolicy', PROXY, {
        'podSelector': {'matchLabels': dict(PROXY_LABELS)}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [peer(entry_namespace, entry_labels)], 'ports': [{'protocol': 'TCP', 'port': 4180}]}],
        'egress': [
            {'to': [peer('kube-system', {'k8s-app': 'kube-dns'})],
             'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
            {'to': [peer(upstream_namespace, upstream_labels)], 'ports': [{'protocol': 'TCP', 'port': settings.upstream_port}]},
            {'to': [peer(namespace, dict(REDIS_LABELS))], 'ports': [{'protocol': 'TCP', 'port': 6379}]},
            # CoreDNS resolves the unchanged HTTPS issuer to this internal TLS
            # ingress. Select its destination Pods after Service DNAT, rather
            # than permitting every private address or all public HTTPS.
            {'to': [peer(oidc_namespace, oidc_labels)],
             'ports': [{'protocol': 'TCP', 'port': settings.oidc_egress_port}]},
        ],
    })
    redis_policy = obj('networking.k8s.io/v1', 'NetworkPolicy', REDIS, {
        'podSelector': {'matchLabels': dict(REDIS_LABELS)}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [peer(namespace, dict(PROXY_LABELS))], 'ports': [{'protocol': 'TCP', 'port': 6379}]}],
        'egress': [],
    })
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [proxy, redis, *services, proxy_policy, redis_policy]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='Confirmed user-managed public host, without scheme/path/port')
    parser.add_argument('--proxy-image', required=True, help='oauth2-proxy v7.8+ repository:latest')
    parser.add_argument('--proxy-image-digest', required=True)
    parser.add_argument('--redis-image', required=True, help='Compatible Redis repository:latest with /bin/sh and redis-server')
    parser.add_argument('--redis-image-digest', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--namespace', default='oauth')
    parser.add_argument('--oidc-secret', default='oauth2-proxy-secret')
    parser.add_argument('--session-secret', default='video-workbench-oauth-runtime')
    parser.add_argument('--upstream-namespace', default='video-workbench')
    parser.add_argument('--upstream-service', default='video-workbench')
    parser.add_argument('--upstream-port', type=int, default=8089)
    parser.add_argument('--entry-namespace', default='default')
    parser.add_argument('--entry-label', action='append', help='Repeat KEY=VALUE; overrides cloudflared/main selectors')
    parser.add_argument('--upstream-label', action='append', help='Repeat KEY=VALUE; overrides workbench selectors')
    parser.add_argument('--oidc-egress-namespace', default='ingress-nginx',
                        help='Namespace of the existing internal TLS ingress for the OIDC issuer')
    parser.add_argument('--oidc-egress-label', action='append',
                        help='Repeat KEY=VALUE; overrides internal ingress controller selectors')
    parser.add_argument('--oidc-egress-port', type=int, default=443,
                        help='Actual destination Pod TCP port after Service translation')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    from .render import _parse_labels
    try:
        values = vars(args).copy()
        destination = values.pop('output')
        entry = values.pop('entry_label')
        upstream = values.pop('upstream_label')
        oidc = values.pop('oidc_egress_label')
        if entry is not None:
            values['entry_labels'] = _parse_labels(entry)
        if upstream is not None:
            values['upstream_labels'] = _parse_labels(upstream)
        if oidc is not None:
            values['oidc_egress_labels'] = _parse_labels(oidc)
        text = json.dumps(render(OAuthSettings(**values)), ensure_ascii=False, indent=2) + '\n'
    except ValueError as exc:
        parser.error(str(exc))
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding='utf-8')
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
