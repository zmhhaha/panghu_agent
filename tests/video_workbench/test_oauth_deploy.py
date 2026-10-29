"""Internal authentication resources keep credentials, stores and routing separate."""
from dataclasses import replace
import json

import pytest

from video_workbench.deploy.oauth import OAuthSettings, main, render


@pytest.fixture
def settings():
    return OAuthSettings(
        host='video-service.panghuer.top',
        proxy_image='registry.example.com/oauth2-proxy:latest',
        proxy_image_digest='sha256:' + 'a' * 64,
        redis_image='registry.example.com/redis:latest',
        redis_image_digest='sha256:' + 'b' * 64,
        release_id='abcdef123456',
    )


def item(document, kind, name):
    return next(r for r in document['items'] if r['kind'] == kind and r['metadata']['name'] == name)


def container(document, name):
    deployment = item(document, 'Deployment', name)
    return deployment['spec']['template']['spec']['containers'][0]


def test_no_public_routing_or_credential_creation(settings):
    document = render(settings)
    assert len(document['items']) == 6
    assert {r['kind'] for r in document['items']} == {'Deployment', 'Service', 'NetworkPolicy'}
    assert all(r['metadata']['namespace'] == 'oauth' for r in document['items'])
    assert all(r['spec']['type'] == 'ClusterIP' for r in document['items'] if r['kind'] == 'Service')
    assert all('data' not in r and 'stringData' not in r for r in document['items'])


def test_oidc_is_reused_selectively_but_cookie_and_redis_credentials_are_independent(settings):
    document = render(settings)
    proxy = container(document, 'oauth2-proxy-video-workbench')
    redis = container(document, 'video-workbench-session-redis')
    env = {r['name']: r['valueFrom']['secretKeyRef'] for r in proxy['env']}
    assert 'envFrom' not in proxy
    assert env['OAUTH2_PROXY_CLIENT_SECRET'] == {'name': 'oauth2-proxy-secret', 'key': 'OIDC_CLIENT_SECRET'}
    assert env['OAUTH2_PROXY_COOKIE_SECRET'] == {'name': 'video-workbench-oauth-runtime', 'key': 'COOKIE_SECRET'}
    assert env['OAUTH2_PROXY_REDIS_PASSWORD'] == {'name': 'video-workbench-oauth-runtime', 'key': 'REDIS_PASSWORD'}
    assert redis['env'][0]['valueFrom']['secretKeyRef'] == env['OAUTH2_PROXY_REDIS_PASSWORD']
    assert not any('--cookie-secret=' in arg or '--redis-password=' in arg for arg in proxy['args'])


def test_session_store_does_not_compete_for_a_shared_redis_database(settings):
    document = render(settings)
    proxy = container(document, 'oauth2-proxy-video-workbench')
    assert '--redis-connection-url=redis://video-workbench-session-redis.oauth.svc.cluster.local:6379/0' in proxy['args']
    assert 'redis.data.svc' not in json.dumps(document)
    deployment = item(document, 'Deployment', 'video-workbench-session-redis')['spec']
    assert deployment['replicas'] == 1
    assert deployment['strategy'] == {'type': 'Recreate'}
    assert not any('persistentVolumeClaim' in volume for volume in deployment['template']['spec']['volumes'])
    redis = container(document, 'video-workbench-session-redis')
    assert redis['command'][:2] == ['/bin/sh', '-ec']
    assert redis['command'][2].endswith('exec redis-server /tmp/redis.conf\n')
    assert 'unset REDIS_PASSWORD' in redis['command'][2]
    assert 'umask 077' in redis['command'][2]
    assert 'save ""' in redis['command'][2]
    assert 'maxmemory 32mb' in redis['command'][2]


def test_cookie_is_host_only_and_owner_headers_are_overwritten(settings):
    proxy = container(render(settings), 'oauth2-proxy-video-workbench')
    args = proxy['args']
    assert '--redirect-url=https://video-service.panghuer.top/oauth2/callback' in args
    assert '--cookie-name=__Host-video_workbench' in args
    assert '--cookie-secure=true' in args and '--cookie-path=/' in args
    assert not any(arg.startswith('--cookie-domain') for arg in args)
    assert '--cookie-csrf-per-request=true' in args
    assert '--pass-user-headers=true' in args
    assert '--skip-auth-strip-headers=true' in args
    assert '--pass-host-header=true' in args
    assert '--upstream=http://video-workbench.video-workbench.svc.cluster.local:8089' in args
    assert not any('insecure-skip-verify' in arg for arg in args)


def test_both_pods_run_without_privileges_and_with_bounded_resources(settings):
    document = render(settings)
    for resource in document['items']:
        if resource['kind'] != 'Deployment':
            continue
        pod = resource['spec']['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        assert pod['securityContext']['runAsNonRoot'] is True
        assert pod['securityContext']['runAsUser'] == 10001
        c, = pod['containers']
        assert c['image'].split('@')[0].endswith(':latest')
        assert '@sha256:' in c['image']
        assert c['securityContext'] == {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                        'capabilities': {'drop': ['ALL']}}
        assert c['volumeMounts'] == [{'name': 'temporary', 'mountPath': '/tmp'}]
        assert pod['volumes'] == [{'name': 'temporary', 'emptyDir': {'sizeLimit': '4Mi'}}]


def test_network_allows_only_tunnel_to_proxy_and_proxy_to_redis(settings):
    document = render(settings)
    proxy = item(document, 'NetworkPolicy', 'oauth2-proxy-video-workbench')['spec']
    source, = proxy['ingress'][0]['from']
    assert source == {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'default'}},
                      'podSelector': {'matchLabels': {'app': 'cloudflared', 'tunnel': 'main'}}}
    redis = item(document, 'NetworkPolicy', 'video-workbench-session-redis')['spec']
    assert redis['egress'] == []
    peer, = redis['ingress'][0]['from']
    assert peer['namespaceSelector']['matchLabels'] == {'kubernetes.io/metadata.name': 'oauth'}
    assert peer['podSelector']['matchLabels'] == proxy['podSelector']['matchLabels']
    assert redis['ingress'][0]['ports'] == [{'protocol': 'TCP', 'port': 6379}]
    assert proxy['egress'][-1]['ports'] == [{'protocol': 'TCP', 'port': 443}]
    assert proxy['egress'][-1]['to'][0]['ipBlock']['except']
    assert all('namespaceSelector' in p and 'podSelector' in p
               for rule in proxy['egress'][:-1] for p in rule['to'])


def test_custom_upstream_and_entry_selectors_remain_consistent(settings):
    document = render(replace(settings, namespace='login', upstream_namespace='studio',
                              upstream_service='app', upstream_port=8888,
                              upstream_labels={'role': 'studio'}, entry_namespace='edge',
                              entry_labels={'role': 'tunnel'}))
    proxy = container(document, 'oauth2-proxy-video-workbench')
    assert '--upstream=http://app.studio.svc.cluster.local:8888' in proxy['args']
    assert '--redis-connection-url=redis://video-workbench-session-redis.login.svc.cluster.local:6379/0' in proxy['args']
    policy = item(document, 'NetworkPolicy', 'oauth2-proxy-video-workbench')['spec']
    assert policy['ingress'][0]['from'][0]['podSelector']['matchLabels'] == {'role': 'tunnel'}
    assert policy['egress'][1]['to'][0]['podSelector']['matchLabels'] == {'role': 'studio'}


@pytest.mark.parametrize('changes', [
    {'host': 'https://video-service.panghuer.top'}, {'host': 'example.com:443'},
    {'host': '127.0.0.1'}, {'host': '[::1]'}, {'host': 'localhost'},
    {'oidc_secret': 'same', 'session_secret': 'same'}, {'session_secret': '../secret'},
    {'namespace': 'a.b'}, {'entry_labels': {}}, {'upstream_labels': {}},
    {'upstream_port': True}, {'upstream_port': 0},
    {'proxy_image': 'registry.example.com/oauth2-proxy:v7.8.0'},
    {'redis_image_digest': 'sha256:fake'}, {'release_id': 'a' * 64},
])
def test_invalid_inputs_do_not_broaden_resources(settings, changes):
    with pytest.raises(ValueError):
        render(replace(settings, **changes))


def test_cli_generates_only_reviewable_resources(tmp_path):
    destination = tmp_path / 'auth.json'
    main(['--host', 'video-service.panghuer.top', '--proxy-image', 'example.com/oauth:latest',
          '--proxy-image-digest', 'sha256:' + 'a' * 64, '--redis-image', 'example.com/redis:latest',
          '--redis-image-digest', 'sha256:' + 'b' * 64, '--release-id', 'abcdef',
          '--output', str(destination)])
    document = json.loads(destination.read_text(encoding='utf-8'))
    assert len(document['items']) == 6
    assert list(tmp_path.iterdir()) == [destination]
