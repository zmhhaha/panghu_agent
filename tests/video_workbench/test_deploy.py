"""Offline checks for deployment isolation, persistence and credential handling."""
from dataclasses import replace
import json

import pytest

from video_workbench.deploy.render import Settings, main, render


@pytest.fixture
def settings():
    return Settings(
        image='registry.example.com:5000/studio/video-workbench:latest',
        image_digest='sha256:' + 'a' * 64,
        release_id='abcdef123456',
        allowed_hosts=['studio.example.com'],
        proxy_namespace='identity',
        proxy_labels={'app': 'studio-proxy', 'role': 'authenticated'},
        storage_class='ceph-rbd',
    )


def resource(document, kind, namespace='video-workbench'):
    return next(item for item in document['items'] if item['kind'] == kind
                and item['metadata'].get('namespace') == namespace)


def test_sqlite_is_single_coordinator_with_durable_writable_data(settings):
    document = render(settings)
    deployment = resource(document, 'Deployment')['spec']
    pod = deployment['template']['spec']
    container, = pod['containers']
    pvc = resource(document, 'PersistentVolumeClaim')['spec']
    assert deployment['replicas'] == 1
    assert deployment['strategy'] == {'type': 'Recreate'}
    assert '--local' not in container['args']
    assert pod['securityContext']['runAsUser'] == pod['securityContext']['fsGroup'] == 10001
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    data = next(volume for volume in pod['volumes'] if volume['name'] == 'data')
    assert data['persistentVolumeClaim']['claimName'] == 'video-workbench'
    assert pvc['accessModes'] == ['ReadWriteOnce']
    assert pvc['storageClassName'] == 'ceph-rbd'
    env = {item['name']: item['value'] for item in container['env']}
    assert env['VIDEO_WORKBENCH_DB'] == '/data/workbench.db'
    assert {'name': 'data', 'mountPath': '/data'} in container['volumeMounts']


def test_container_gets_no_cluster_credentials_and_only_bounded_tmp(settings):
    pod = resource(render(settings), 'Deployment')['spec']['template']['spec']
    container, = pod['containers']
    assert pod['automountServiceAccountToken'] is False
    assert pod['securityContext']['runAsNonRoot'] is True
    assert pod['securityContext']['seccompProfile'] == {'type': 'RuntimeDefault'}
    assert container['securityContext']['allowPrivilegeEscalation'] is False
    assert container['securityContext']['capabilities'] == {'drop': ['ALL']}
    assert not any('hostPath' in volume for volume in pod['volumes'])
    assert not pod.get('hostNetwork')
    assert next(volume for volume in pod['volumes'] if volume['name'] == 'temporary') == {
        'name': 'temporary', 'emptyDir': {'sizeLimit': '32Mi'}}


def test_network_peers_require_both_namespace_and_pod_and_no_public_egress(settings):
    document = render(settings)
    policy = resource(document, 'NetworkPolicy')['spec']
    assert set(policy['policyTypes']) == {'Ingress', 'Egress'}
    ingress, = policy['ingress']
    proxy, = ingress['from']
    assert proxy == {
        'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'identity'}},
        'podSelector': {'matchLabels': {'app': 'studio-proxy', 'role': 'authenticated'}},
    }
    assert ingress['ports'] == [{'protocol': 'TCP', 'port': 8089}]
    dns, agent = policy['egress']
    assert dns['ports'] == [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]
    assert dns['to'][0]['podSelector']['matchLabels'] == {'k8s-app': 'kube-dns'}
    assert agent['ports'] == [{'protocol': 'TCP', 'port': 8080}]
    assert agent['to'][0]['namespaceSelector']['matchLabels'] == {'kubernetes.io/metadata.name': 'agent-service'}
    assert agent['to'][0]['podSelector']['matchLabels']['component'] == 'api'
    assert all(set(peer) == {'namespaceSelector', 'podSelector'}
               for rule in policy['egress'] for peer in rule['to'])
    service = resource(document, 'Service')['spec']
    assert service['type'] == 'ClusterIP'
    assert not any(item['kind'] in {'Ingress', 'Secret'} for item in document['items'])
    assert all(item['metadata'].get('namespace', 'video-workbench') == 'video-workbench'
               for item in document['items'])


def test_custom_agent_target_stays_consistent_and_agent_ingress_is_opt_in(settings):
    settings = replace(settings, namespace='video-lab', agent_namespace='agent-lab',
                       agent_service='control', agent_labels={'component': 'api-lab'},
                       agent_port=8181, include_agent_ingress=True)
    document = render(settings)
    container, = resource(document, 'Deployment', 'video-lab')['spec']['template']['spec']['containers']
    env = {item['name']: item['value'] for item in container['env']}
    assert env['VIDEO_AGENT_URL'] == 'http://control.agent-lab.svc.cluster.local:8181'
    policy = resource(document, 'NetworkPolicy', 'video-lab')['spec']
    target, = policy['egress'][1]['to']
    assert target['podSelector']['matchLabels'] == {'component': 'api-lab'}
    ingress = resource(document, 'NetworkPolicy', 'agent-lab')['spec']
    assert ingress['podSelector']['matchLabels'] == target['podSelector']['matchLabels']
    assert ingress['policyTypes'] == ['Ingress']
    source, = ingress['ingress'][0]['from']
    assert source['namespaceSelector']['matchLabels'] == {'kubernetes.io/metadata.name': 'video-lab'}
    assert source['podSelector']['matchLabels'] == policy['podSelector']['matchLabels']
    assert ingress['ingress'][0]['ports'] == policy['egress'][1]['ports']


def test_probes_pass_an_allowed_host_without_identity_or_model_calls(settings):
    container, = resource(render(settings), 'Deployment')['spec']['template']['spec']['containers']
    for probe_name, path in [('startupProbe', '/healthz'), ('livenessProbe', '/healthz'),
                             ('readinessProbe', '/readyz')]:
        probe = container[probe_name]['httpGet']
        assert probe['path'] == path
        assert probe['httpHeaders'] == [{'name': 'Host', 'value': 'studio.example.com'}]
        assert probe['port'] == 'http'


def test_release_uses_latest_and_exact_digest_and_rollout_metadata(settings):
    deployment = resource(render(settings), 'Deployment')
    template = deployment['spec']['template']
    container, = template['spec']['containers']
    assert container['image'] == settings.image + '@' + settings.image_digest
    assert container['imagePullPolicy'] == 'Always'
    for metadata in (deployment['metadata'], template['metadata']):
        assert metadata['labels']['app.kubernetes.io/version'] == settings.release_id
        assert metadata['annotations']['org.opencontainers.image.digest'] == settings.image_digest
        assert metadata['annotations']['org.opencontainers.image.revision'] == settings.release_id
    changed = resource(render(replace(settings, image_digest='sha256:' + 'b' * 64)), 'Deployment')
    assert changed['spec']['template'] != template


def test_credential_is_readonly_projected_file_without_literal_secret(settings):
    settings = replace(settings, token_secret='video-caller', token_secret_key='caller-token',
                       external_secret_store='vault-backend',
                       external_secret_remote_key='secret/data/agent-service/runtime',
                       external_secret_property='VIDEO_SERVICE_TOKEN')
    document = render(settings)
    pod = resource(document, 'Deployment')['spec']['template']['spec']
    container, = pod['containers']
    env = {item['name']: item['value'] for item in container['env']}
    assert 'VIDEO_AGENT_TOKEN' not in env
    assert env['VIDEO_AGENT_TOKEN_FILE'] == '/run/secrets/video-agent/token'
    mount = next(item for item in container['volumeMounts'] if item['name'] == 'agent-token')
    assert mount['readOnly'] is True
    assert 'subPath' not in mount
    secret = next(item for item in pod['volumes'] if item['name'] == 'agent-token')['secret']
    assert secret == {'secretName': 'video-caller', 'defaultMode': 0o440,
                      'items': [{'key': 'caller-token', 'path': 'token'}]}
    external = resource(document, 'ExternalSecret')['spec']
    assert external['target'] == {'name': 'video-caller', 'creationPolicy': 'Owner'}
    assert external['data'] == [{'secretKey': 'caller-token', 'remoteRef': {
        'key': 'secret/data/agent-service/runtime', 'property': 'VIDEO_SERVICE_TOKEN'}}]
    assert not any(item['kind'] == 'Secret' for item in document['items'])


@pytest.mark.parametrize('change', [
    {'namespace': ''}, {'namespace': 'a.b'}, {'namespace': 'a' * 64},
    {'proxy_namespace': 'bad/name'}, {'proxy_labels': {}},
    {'proxy_labels': {'/app': 'proxy'}}, {'proxy_labels': {'a..b/app': 'proxy'}},
    {'proxy_labels': {'app': 'a' * 64}}, {'agent_labels': {}},
    {'allowed_hosts': []}, {'allowed_hosts': 'studio.example.com'},
    {'allowed_hosts': ['https://studio.example.com']},
    {'allowed_hosts': ['studio.example.com:8089']}, {'allowed_hosts': ['*']},
    {'allowed_hosts': ['[not-ipv6]']}, {'allowed_hosts': ['foo,bar']},
    {'image': 'registry.example.com/workbench:v1'},
    {'image': 'registry.example.com/workbench:latest@sha256:' + 'a' * 64},
    {'image': 'registry.example.com/workbench:other:latest'},
    {'image': 'registry.example.com//workbench:latest'},
    {'image': 'registry.example.com:70000/workbench:latest'},
    {'image_digest': 'sha256:' + 'A' * 64}, {'image_digest': 'sha256:abc'},
    {'release_id': 'commit/123'}, {'release_id': 'a' * 64},
    {'storage_class': ''}, {'storage_size': '0Gi'}, {'storage_size': '10; apply'},
    {'agent_port': 0}, {'agent_port': 65536}, {'agent_port': True},
    {'token_secret': 'secret/token'}, {'token_secret_key': 'bad/key'},
    {'profile_revision': 'v1\n'},
    {'external_secret_store': 'vault-backend'},
    {'external_secret_store': 'vault-backend', 'external_secret_remote_key': 'secret/data/runtime',
     'external_secret_property': 'TOKEN', 'external_secret_store_kind': 'Other'},
    {'external_secret_store': 'vault-backend', 'external_secret_remote_key': 'secret/../runtime',
     'external_secret_property': 'TOKEN'},
])
def test_invalid_or_broad_deployment_inputs_fail_closed(settings, change):
    with pytest.raises(ValueError):
        render(replace(settings, **change))


def cli_args():
    return ['--image', 'registry.example.com/video:latest', '--image-digest', 'sha256:' + 'a' * 64,
            '--release-id', 'abcdef', '--allowed-host', 'studio.example.com',
            '--proxy-namespace', 'oauth', '--proxy-label', 'app=studio-proxy',
            '--storage-class', 'ceph-rbd']


def test_cli_emits_parseable_manifest_without_files_or_cluster_access(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(cli_args() + ['--allowed-host', '[::1]', '--agent-label', 'custom.example.com/role=control'])
    document = json.loads(capsys.readouterr().out)
    assert list(tmp_path.iterdir()) == []
    container, = resource(document, 'Deployment')['spec']['template']['spec']['containers']
    assert next(item['value'] for item in container['env'] if item['name'] == 'VIDEO_WORKBENCH_HOSTS') == 'studio.example.com,[::1]'
    policy = resource(document, 'NetworkPolicy')['spec']
    assert policy['egress'][1]['to'][0]['podSelector']['matchLabels'] == {'custom.example.com/role': 'control'}


def test_cli_output_and_explicit_cross_namespace_policy(tmp_path):
    output = tmp_path / 'rendered' / 'workbench.json'
    main(cli_args() + ['--include-agent-ingress', '--output', str(output)])
    document = json.loads(output.read_text(encoding='utf-8'))
    assert resource(document, 'NetworkPolicy', 'agent-service')['metadata']['name'] == 'video-workbench-from-video-workbench'


@pytest.mark.parametrize('extra', [
    ['--proxy-label', 'app=another'], ['--proxy-label', 'missing-equals'],
    ['--external-secret-store', 'vault-backend'],
])
def test_invalid_cli_never_creates_partial_output(tmp_path, extra):
    output = tmp_path / 'rendered' / 'workbench.json'
    with pytest.raises(SystemExit) as exc:
        main(cli_args() + extra + ['--output', str(output)])
    assert exc.value.code == 2
    assert not output.parent.exists()
