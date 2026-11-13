"""Offline private-executor packaging checks; these do not build or deploy it."""
from dataclasses import replace
import json
from pathlib import Path
import re
import shlex

import pytest

from video_workbench.deploy.animation_creation_executor import (
    AGENT_TOKEN, DATA_ROOT, DOCKER_SOCKET, LAB_TOKEN, NAME, NODE, PORT, SELECTOR,
    Settings, main, render,
)


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / 'video_workbench' / 'Dockerfile.animation-creation-executor'


@pytest.fixture
def settings():
    return Settings(
        image='arm-cluster-master:5000/video-animation-creation:latest',
        image_digest='sha256:' + 'a' * 64,
        renderer_image='arm-cluster-master:5000/video-motion-renderer:latest',
        renderer_image_digest='sha256:' + 'b' * 64,
        release_id='executor-reviewed-123',
        agent_token_secret='creation-agent-caller', lab_token_secret='creation-lab-caller',
    )


def by_kind(document, kind):
    return next(row for row in document['items'] if row['kind'] == kind)


def pod(document):
    return by_kind(document, 'Deployment')['spec']['template']['spec']


def arguments(settings):
    return ['--image', settings.image, '--image-digest', settings.image_digest,
            '--renderer-image', settings.renderer_image,
            '--renderer-image-digest', settings.renderer_image_digest,
            '--release-id', settings.release_id,
            '--agent-token-secret', settings.agent_token_secret,
            '--lab-token-secret', settings.lab_token_secret]


def test_method_caller_is_a_distinct_optional_readonly_secret(settings):
    document = render(replace(settings, method_agent_token_secret='methods-agent-caller'))
    spec = pod(document)
    container = spec['containers'][0]
    assert container['args'][-4:] == ['--method-agent-token-file', '/run/secrets/animation-method-agent/token',
                                     '--method-protocol', 'skills']
    assert next(row for row in spec['volumes'] if row['name'] == 'method-agent-token')['secret'] == {
        'secretName': 'methods-agent-caller', 'optional': False, 'defaultMode': 0o400,
        'items': [{'key': 'token', 'path': 'token'}]}
    assert next(row for row in container['volumeMounts'] if row['name'] == 'method-agent-token')['readOnly']
    for secret in (settings.agent_token_secret, settings.lab_token_secret):
        with pytest.raises(ValueError, match='Distinct method'):
            render(replace(settings, method_agent_token_secret=secret))


def test_method_protocol_is_explicit_in_deployment_and_invalid_values_are_rejected(settings):
    legacy = render(replace(settings, method_agent_token_secret='legacy-methods', method_protocol='legacy'))
    assert pod(legacy)['containers'][0]['args'][-2:] == ['--method-protocol', 'legacy']
    assert by_kind(legacy, 'Deployment')['metadata']['annotations'][
        'video-service.panghuer.top/method-protocol'] == 'legacy'
    with pytest.raises(ValueError, match='Invalid method protocol'):
        render(replace(settings, method_protocol='auto-fallback'))


def test_only_three_private_resources_without_provisioning_permissions_or_public_routes(settings):
    document = render(settings)
    assert document['kind'] == 'List' and document['apiVersion'] == 'v1'
    assert [row['kind'] for row in document['items']] == ['Deployment', 'Service', 'NetworkPolicy']
    assert all(row['metadata']['name'] == NAME for row in document['items'])
    assert all(row['metadata']['namespace'] == NAME for row in document['items'])
    deployment = by_kind(document, 'Deployment')['spec']
    assert deployment['replicas'] == 1 and deployment['strategy'] == {'type': 'Recreate'}
    assert deployment['selector']['matchLabels'] == SELECTOR
    spec = pod(document)
    assert spec['automountServiceAccountToken'] is False and spec['enableServiceLinks'] is False
    assert not any(spec.get(key) for key in ('serviceAccountName', 'hostNetwork', 'hostPID', 'hostIPC'))
    assert by_kind(document, 'Service')['spec'] == {
        'type': 'ClusterIP', 'selector': SELECTOR,
        'ports': [{'name': 'http', 'port': PORT, 'targetPort': 'http'}]}
    assert render(settings) == document
    # Returned documents must not share the exported selector mapping.
    deployment['selector']['matchLabels']['test'] = 'changed'
    assert render(settings) != document and SELECTOR == {'app.kubernetes.io/name': NAME}


def test_fixed_master_and_same_host_container_data_root_are_required_for_docker_binds(settings):
    spec = pod(render(settings))
    assert spec['nodeSelector'] == {'kubernetes.io/hostname': NODE, 'kubernetes.io/arch': 'arm64'}
    assert NODE == 'arm-cluster-master' and DATA_ROOT == '/var/lib/video-animation-creation'
    container, = spec['containers']
    volumes = {row['name']: row for row in spec['volumes']}
    mounts = {row['name']: row for row in container['volumeMounts']}
    assert volumes['executions']['hostPath'] == {'path': DATA_ROOT, 'type': 'DirectoryOrCreate'}
    assert mounts['executions'] == {'name': 'executions', 'mountPath': DATA_ROOT}
    assert volumes['docker-socket']['hostPath'] == {'path': DOCKER_SOCKET, 'type': 'Socket'}
    assert mounts['docker-socket'] == {'name': 'docker-socket', 'mountPath': DOCKER_SOCKET}
    assert volumes['temporary'] == {'name': 'temporary', 'emptyDir': {'sizeLimit': '256Mi'}}
    args = container['args']
    assert args[args.index('--root') + 1] == DATA_ROOT
    assert args[args.index('--listen') + 1] == '0.0.0.0'
    assert args[args.index('--port') + 1] == '8767'
    assert '--enable-isolated-rendering' in args
    assert not any('persistentVolumeClaim' in row for row in volumes.values())


def test_trusted_root_controller_has_only_ownership_capabilities_and_does_not_claim_renderer_isolation(settings):
    document = render(settings)
    spec = pod(document)
    assert spec['securityContext'] == {'runAsNonRoot': False, 'runAsUser': 0, 'runAsGroup': 0,
                                       'seccompProfile': {'type': 'RuntimeDefault'}}
    container, = spec['containers']
    assert container['securityContext'] == {
        'runAsNonRoot': False, 'runAsUser': 0, 'runAsGroup': 0,
        'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
        'capabilities': {'drop': ['ALL'], 'add': ['CHOWN', 'FOWNER']}}
    assert not container['securityContext'].get('privileged')
    assert container['resources']['requests'] and container['resources']['limits']
    annotations = by_kind(document, 'Deployment')['spec']['template']['metadata']['annotations']
    assert annotations['video-service.panghuer.top/trust-boundary'] == 'root-docker-controller-not-restricted-renderer'
    # The init process seals only the host root, with no socket or token mount.
    init, = spec['initContainers']
    assert init['command'] == ['python', '-c', f'import os; os.chmod({DATA_ROOT!r}, 0o700)']
    assert init['securityContext']['capabilities'] == {'drop': ['ALL'], 'add': ['FOWNER']}
    assert init['securityContext']['readOnlyRootFilesystem']
    assert init['securityContext']['allowPrivilegeEscalation'] is False
    assert init['volumeMounts'] == [{'name': 'executions', 'mountPath': DATA_ROOT}]
    assert not init.get('env') and not init.get('envFrom')
    assert init['resources']['requests'] and init['resources']['limits']


def test_distinct_single_key_credentials_are_only_read_only_files_in_the_controller(settings):
    custom = replace(settings, agent_token_key='agent.token', lab_token_key='lab.token')
    spec = pod(render(custom))
    container, = spec['containers']
    secrets = {row['name']: row['secret'] for row in spec['volumes'] if 'secret' in row}
    assert secrets == {
        'agent-caller-token': {'secretName': settings.agent_token_secret, 'optional': False,
            'defaultMode': 0o400, 'items': [{'key': 'agent.token', 'path': 'token'}]},
        'lab-caller-token': {'secretName': settings.lab_token_secret, 'optional': False,
            'defaultMode': 0o400, 'items': [{'key': 'lab.token', 'path': 'token'}]},
    }
    args = container['args']
    assert args[args.index('--agent-token-file') + 1] == AGENT_TOKEN
    assert args[args.index('--token-file') + 1] == LAB_TOKEN and LAB_TOKEN != AGENT_TOKEN
    mounts = {row['name']: row for row in container['volumeMounts']}
    for name, path in [('agent-caller-token', AGENT_TOKEN), ('lab-caller-token', LAB_TOKEN)]:
        assert mounts[name] == {'name': name, 'mountPath': path.rsplit('/', 1)[0], 'readOnly': True}
    assert not container.get('envFrom')
    assert {row['name'] for row in container['env']} == {
        'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED', 'DOCKER_HOST', 'DOCKER_CONFIG'}
    assert all(set(row) == {'name', 'value'} for row in container['env'])
    assert not any('projected' in row for row in spec['volumes'])


def test_agent_origin_is_derived_and_network_peers_require_namespace_and_pod_together(settings):
    custom = replace(settings, namespace='creation-private', lab_namespace='lab-private',
                     lab_labels={'app': 'lab', 'component': 'ui'}, agent_namespace='agent-private',
                     agent_name='agent-api', agent_port=8999, agent_labels={'app': 'agent', 'component': 'api'})
    document = render(custom)
    container, = pod(document)['containers']
    args = container['args']
    assert args[args.index('--agent-url') + 1] == 'http://agent-api.agent-private.svc.cluster.local:8999'
    policy = by_kind(document, 'NetworkPolicy')['spec']
    assert policy == {
        'podSelector': {'matchLabels': SELECTOR}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [{
            'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'lab-private'}},
            'podSelector': {'matchLabels': {'app': 'lab', 'component': 'ui'}}}],
            'ports': [{'protocol': 'TCP', 'port': PORT}]}],
        'egress': [
            {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                     'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
             'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
            {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'agent-private'}},
                     'podSelector': {'matchLabels': {'app': 'agent', 'component': 'api'}}}],
             'ports': [{'protocol': 'TCP', 'port': 8999}]},
        ],
    }
    assert all(set(peer) == {'namespaceSelector', 'podSelector'}
               for name, direction in [('from', 'ingress'), ('to', 'egress')]
               for rule in policy[direction] for peer in rule[name])


def test_all_images_keep_latest_and_digests_and_probes_only_read_health(settings):
    document = render(settings)
    spec = pod(document)
    container, = spec['containers']
    for row in [container, *spec['initContainers']]:
        assert row['image'] == settings.image + '@' + settings.image_digest
        assert row['imagePullPolicy'] == 'Always'
    args = container['args']
    assert args[args.index('--renderer-image') + 1] == settings.renderer_image + '@' + settings.renderer_image_digest
    assert container['command'] == ['python', '-m', 'video_workbench.animation_creation_executor']
    for name in ['startupProbe', 'readinessProbe', 'livenessProbe']:
        assert container[name]['httpGet'] == {'path': '/healthz', 'port': 'http'}
    assert container['startupProbe']['periodSeconds'] * container['startupProbe']['failureThreshold'] >= 120
    for resource in document['items']:
        annotations = resource['metadata']['annotations']
        assert annotations['org.opencontainers.image.ref.name'] == settings.image
        assert annotations['org.opencontainers.image.digest'] == settings.image_digest
        assert annotations['org.opencontainers.image.revision'] == settings.release_id


@pytest.mark.parametrize('changes', [
    dict(namespace='bad.namespace'), dict(namespace='https://private.example'),
    dict(lab_namespace='https://lab.example'), dict(agent_namespace='../agent'),
    dict(agent_name='https://agent.example'), dict(agent_name='agent.example'),
    dict(agent_name='1agent'), dict(agent_name='agent:8080'), dict(agent_name='a' * 64),
    dict(agent_port=0), dict(agent_port=65536), dict(agent_port=True), dict(agent_port='8080'),
    dict(lab_labels={}), dict(lab_labels=[]), dict(lab_labels={'*': '*'}),
    dict(agent_labels={}), dict(agent_labels={'app': 'agent\n'}),
    dict(agent_token_secret='../../token'), dict(lab_token_secret='https://secret.example'),
    dict(lab_token_secret='creation-agent-caller'),
    dict(agent_token_key=''), dict(lab_token_key='../../token'),
    dict(agent_token_key='token\n'), dict(lab_token_key='a' * 254),
    dict(image='registry.example/executor:v1'), dict(image='https://registry.example/executor:latest'),
    dict(image='registry.example/executor:latest@sha256:' + 'a' * 64),
    dict(image_digest='latest'), dict(image_digest='sha256:' + 'A' * 64),
    dict(renderer_image='renderer:v1'), dict(renderer_image_digest='sha256:bad'),
    dict(release_id='bad release'), dict(release_id='trailing-'), dict(release_id='trailing.'),
    dict(release_id='a' * 64), dict(release_id=''),
])
def test_unbounded_or_invalid_configuration_is_rejected_before_generation(settings, changes):
    with pytest.raises(ValueError):
        render(replace(settings, **changes))


def test_cli_is_deterministic_preserves_existing_output_and_has_no_url_node_or_root_override(settings, tmp_path, capsys):
    main(arguments(settings))
    assert json.loads(capsys.readouterr().out) == render(settings)
    output = tmp_path / 'private-creation.json'
    custom = ['--agent-namespace', 'private-agent', '--agent-name', 'agent-api',
              '--agent-port', '8989', '--agent-label', 'app=agent', '--agent-label', 'component=api',
              '--lab-label', 'app=lab', '--agent-token-key', 'caller.token',
              '--lab-token-key', 'lab.token', '--output', str(output)]
    main([*arguments(settings), *custom])
    raw = output.read_bytes()
    generated = json.loads(raw)
    args = pod(generated)['containers'][0]['args']
    assert args[args.index('--agent-url') + 1] == 'http://agent-api.private-agent.svc.cluster.local:8989'
    with pytest.raises(FileExistsError):
        main([*arguments(settings), *custom])
    assert output.read_bytes() == raw
    for invalid in [['--agent-url', 'https://public.example'], ['--node', 'other-node'],
                    ['--root', '/data'], ['--socket', '/other.sock'],
                    ['--lab-label', 'app=one', '--lab-label', 'app=two'],
                    ['--agent-label', 'not-a-pair'], ['--release-id', 'invalid-']]:
        with pytest.raises(SystemExit):
            main([*arguments(settings), *invalid])


def test_independent_dockerfile_builds_complete_git_sources_without_application_donor():
    source = DOCKERFILE.read_text('utf8')
    assert source.startswith('# Build the complete executor')
    assert 'FROM ${PYTHON_BASE_IMAGE}\n' in source
    assert re.search(r'^ARG PYTHON_BASE_IMAGE=python@sha256:[0-9a-f]{64}$', source, re.MULTILINE)
    assert 'VIDEO_WORKBENCH_IMAGE' not in source
    assert 'USER 0:0' in source and 'EXPOSE 8767' in source
    packages = re.search(r'apt-get install -y --no-install-recommends ([^\n]+)', source).group(1).split()
    assert {'docker.io', 'docker-cli', 'ffmpeg', 'fonts-noto-cjk', 'ca-certificates'} <= set(packages)
    assert all(check in source for check in ['docker --version', 'ffmpeg -version', 'ffprobe -version', 'python -m pip check'])
    assert 'root-docker-controller-not-restricted-renderer' in source
    copied = []
    for row in source.splitlines():
        if not row.startswith('COPY '):
            continue
        paths = shlex.split(row)[1:]
        assert paths[-1].startswith('/app/')
        for relative in paths[:-1]:
            path = ROOT / relative
            assert path.exists(), relative
            assert relative != '.'
            assert '*' not in relative and '..' not in Path(relative).parts
            assert not any(token in relative for token in ('secrets', 'credentials', '.sqlite', '.db', 'renders/', 'evaluations/'))
            copied.append(relative)
    assert set(copied) == {'video_workbench/requirements.txt', 'agent_service/', 'video_workbench/'}
    # Source changes reuse dependency layers, not a deployed application image.
    assert source.index('pip install') < source.index('COPY agent_service/') < source.index('ARG SOURCE_REVISION\n')
    assert 'from video_workbench.animation_creation_executor import create_server' in source
    assert 'verified_bundle(); old_bundle(); verified_sequence_bundle(); verified_batch_bundle()' in source
    assert 'io.panghu.animation-creation.build="full-source"' in source
    assert 'io.panghu.animation-creation.workbench-donor' not in source
    assert 'create_server(' not in source
    assert '--enable-isolated-rendering' not in source


def test_full_source_context_excludes_runtime_data_and_credentials():
    rules = (DOCKERFILE.with_name(DOCKERFILE.name + '.dockerignore')).read_text('utf8').splitlines()
    patterns = [line for line in rules if line and not line.startswith('#')]
    assert patterns[:5] == ['**', '!agent_service/', '!agent_service/**', '!video_workbench/', '!video_workbench/**']
    assert not any(line.startswith('!') for line in patterns[5:])
    assert {'**/.git', '**/.git/**', '**/rendered', '**/renders', '**/evaluations', '**/data',
            '**/worker-hosts', '**/secrets', '**/credentials', '**/.env', '**/.env.*',
            '**/*.pem', '**/*.key', '**/*.token', '**/*.db', '**/*.db-*', '**/*.sqlite',
            '**/*.sqlite-*', '**/*.sqlite3', '**/*.sqlite3-*',
            'agent_service/config*.json', 'agent_service/worker.config*.json'} <= set(patterns)


@pytest.mark.parametrize('revision,valid', [
    ('b' * 40, True),
    ('B' * 40, False),
    ('b' * 39, False),
    ('', False),
    ('not-measured', False),
])
def test_dockerfile_build_guard_requires_complete_source_revision(monkeypatch, revision, valid):
    source = DOCKERFILE.read_text('utf8')
    guard = re.search(r'^RUN python -c "([^"]+)"', source, re.MULTILINE).group(1)
    monkeypatch.setenv('SOURCE_REVISION', revision)
    if valid:
        exec(compile(guard, str(DOCKERFILE), 'exec'), {})
    else:
        with pytest.raises(AssertionError):
            exec(compile(guard, str(DOCKERFILE), 'exec'), {})


def test_runtime_imports_and_frozen_resources_validate_without_startup_submission():
    from video_workbench.animation_creation_executor import create_server
    from agent_service.profiles.video_animation_create import verified_bundle
    from agent_service.profiles.video_animation_review import (
        verified_bundle as review_bundle, verified_sequence_bundle, verified_batch_bundle,
    )
    assert callable(create_server)
    verified_bundle()
    review_bundle()
    verified_sequence_bundle()
    verified_batch_bundle()


def test_child_renderer_does_not_inherit_controller_socket_credentials_or_capabilities():
    # Full-source packaging keeps the renderer boundary. Verify its launch
    # boundary remains independently credential-free and networkless.
    source = (ROOT / 'video_workbench' / 'animation_experiment_render.py').read_text('utf8')
    for flag in ['--pull=never', '--network=none', '--read-only', '--user=10001:10001',
                 '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=1g']:
        assert flag in source
    assert DOCKER_SOCKET not in source and AGENT_TOKEN not in source and LAB_TOKEN not in source
    assert '--cap-add' not in source
