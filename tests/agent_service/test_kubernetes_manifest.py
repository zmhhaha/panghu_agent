"""Measured Pod spec validation permits API defaults, never execution drift."""
from copy import deepcopy
from dataclasses import replace
import uuid

import pytest

from agent_service.contracts import ExecutionEnvelope, ProfilePolicy, ServiceError
from agent_service.kubernetes_manifest import validate_pod
from agent_service.kubernetes_provider import KubernetesPool, KubernetesWorkerAdapter


@pytest.fixture
def capsule():
    policy = ProfilePolicy('workspace-task.v1', 'v1', 'pi', 'runtime-v1', 'sha256:' + 'a' * 64, enabled=True)
    pool = KubernetesPool('pi-pool', policy, 'agent-service', 'registry/agent@' + policy.image_digest,
                           'agent-host-config', ('10.244.0.0/16',), enabled=True,
                           uid_start=2000000, uid_count=100)
    envelope = ExecutionEnvelope(job_id=str(uuid.UUID(int=1)), attempt_id=str(uuid.UUID(int=2)),
        fence=1, engine='pi', profile=policy.name, profile_revision=policy.revision,
        runtime_revision=policy.runtime_revision, image_digest=policy.image_digest,
        deadline_at=300.0, lease_expires_at=100.0, task={'prompt': 'offline'})
    return pool, envelope


@pytest.fixture
def expected(capsule):
    pool, envelope = capsule
    adapter = KubernetesWorkerAdapter(object(), pool, object(), 'x' * 32, b'y' * 32)
    return adapter._manifest(envelope)[0]


def test_default_pool_adds_no_node_selector(expected):
    assert 'nodeSelector' not in expected['spec']


def test_fixed_pool_node_selector_is_injected_and_accepts_only_exact_values(capsule):
    pool, envelope = capsule
    selected = replace(pool, node_selector=(('kubernetes.io/hostname', 'worker-02'),
        ('worker.example.com/purpose', 'agent-workers')))
    adapter = KubernetesWorkerAdapter(object(), selected, object(), 'x' * 32, b'y' * 32)
    wanted = adapter._manifest(envelope)[0]
    assert wanted['spec']['nodeSelector'] == dict(selected.node_selector)
    validate_pod(deepcopy(wanted), wanted)
    observed = deepcopy(wanted)
    observed['spec']['nodeName'] = 'worker-02'
    validate_pod(observed, wanted)


@pytest.mark.parametrize('mutation', ['delete', 'change', 'add', 'remove-key', 'replace-with-list'])
def test_measured_pod_cannot_change_the_fixed_node_selector(capsule, mutation):
    pool, envelope = capsule
    selected = replace(pool, node_selector=(('kubernetes.io/hostname', 'worker-02'),
        ('worker.example.com/purpose', 'agent-workers')))
    adapter = KubernetesWorkerAdapter(object(), selected, object(), 'x' * 32, b'y' * 32)
    wanted = adapter._manifest(envelope)[0]
    observed = deepcopy(wanted)
    if mutation == 'delete':
        observed['spec'].pop('nodeSelector')
    elif mutation == 'change':
        observed['spec']['nodeSelector']['kubernetes.io/hostname'] = 'worker-03'
    elif mutation == 'add':
        observed['spec']['nodeSelector']['unreviewed.example.com/pool'] = 'other'
    elif mutation == 'remove-key':
        observed['spec']['nodeSelector'].pop('worker.example.com/purpose')
    else:
        observed['spec']['nodeSelector'] = [['kubernetes.io/hostname', 'worker-02']]
    with pytest.raises(ServiceError) as failure:
        validate_pod(observed, wanted)
    assert failure.value.code == 'kubernetes_resource_mismatch'


def test_unconfigured_pool_does_not_accept_injected_scheduling_selector(expected):
    observed = deepcopy(expected)
    observed['spec']['nodeSelector'] = {'kubernetes.io/hostname': 'unexpected-worker'}
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)


def test_original_pod_and_normal_api_defaults_are_accepted(expected):
    assert expected['spec']['containers'][0]['command'] == [
        '/opt/agent-service/.venv/bin/python', '-m', 'agent_service.worker_entry']
    validate_pod(deepcopy(expected), expected)
    observed = deepcopy(expected)
    spec = observed['spec']
    spec.update(nodeName='arm-cluster-worker', dnsPolicy='ClusterFirst',
                schedulerName='default-scheduler', serviceAccountName='default',
                serviceAccount='default',
                tolerations=[{'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists',
                              'effect': 'NoExecute', 'tolerationSeconds': 300}])
    spec.setdefault('enableServiceLinks', True)
    worker = spec['containers'][0]
    worker.update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File',
                  stdin=False, stdinOnce=False, tty=False)
    worker['securityContext']['privileged'] = False
    worker['ports'][0].update(protocol='TCP', hostPort=0)
    for env in worker['env']:
        if env.get('valueFrom', {}).get('fieldRef'):
            env['valueFrom']['fieldRef']['apiVersion'] = 'v1'
    worker['resources']['limits'].update(cpu='1000m', memory='2147483648', **{'ephemeral-storage': '4294967296'})
    worker['resources']['requests'].update(cpu='0.1', memory='134217728', **{'ephemeral-storage': '4Gi'})
    for volume in spec['volumes']:
        if 'emptyDir' in volume and 'medium' not in volume['emptyDir']:
            volume['emptyDir']['medium'] = ''
        for field in ('configMap', 'secret'):
            if field in volume:
                volume[field]['optional'] = False
    validate_pod(observed, expected)


@pytest.mark.parametrize('key,value', [('command', ['/bin/sh']), ('args', ['-c', 'unreviewed']),
    ('workingDir', '/work'), ('envFrom', [{'secretRef': {'name': 'private-key'}}]),
    ('lifecycle', {'postStart': {'exec': {'command': ['/bin/sh']}}}),
    ('readinessProbe', {'exec': {'command': ['/bin/sh']}}), ('restartPolicy', 'Always'),
    ('volumeDevices', [{'name': 'root-device', 'devicePath': '/dev/root'}])])
def test_altered_command_environment_or_root_exec_hook_is_rejected(expected, key, value):
    observed = deepcopy(expected)
    observed['spec']['containers'][0][key] = value
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)


@pytest.mark.parametrize('field,value', [('runAsUser', 10001), ('runAsUser', False),
    ('privileged', True), ('allowPrivilegeEscalation', True), ('readOnlyRootFilesystem', False),
    ('procMount', 'Unmasked'), ('seccompProfile', {'type': 'Unconfined'}),
    ('capabilities', {'drop': ['ALL'], 'add': ['SYS_ADMIN']})])
def test_container_privileges_must_match_reviewed_controller_plan(expected, field, value):
    observed = deepcopy(expected)
    observed['spec']['containers'][0]['securityContext'][field] = value
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)


@pytest.mark.parametrize('field,value', [('hostPID', True), ('hostIPC', True),
    ('hostNetwork', True), ('shareProcessNamespace', True), ('automountServiceAccountToken', True),
    ('securityContext', {'seccompProfile': {'type': 'Unconfined'}}),
    ('securityContext', {'seccompProfile': {'type': 'RuntimeDefault'}, 'fsGroup': 10001}),
    ('initContainers', [{'name': 'extra', 'image': 'anything'}]),
    ('ephemeralContainers', [{'name': 'debug', 'image': 'anything'}]),
    ('hostAliases', [{'ip': '1.1.1.1', 'hostnames': ['model-proxy']}])])
def test_host_access_or_additional_runtime_writer_is_rejected(expected, field, value):
    observed = deepcopy(expected)
    observed['spec'][field] = value
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)


def test_only_exact_downward_api_scope_and_no_extra_environment(expected):
    observed = deepcopy(expected)
    env = observed['spec']['containers'][0]['env']
    env.append({'name': 'PYTHONPATH', 'value': '/work'})
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)
    observed = deepcopy(expected)
    env = observed['spec']['containers'][0]['env']
    env[0]['value'] = 'different-attempt'
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)
    observed = deepcopy(expected)
    env = observed['spec']['containers'][0]['env']
    env[-1]['valueFrom']['fieldRef']['fieldPath'] = 'metadata.name'
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)


@pytest.mark.parametrize('change', ['extra-volume', 'hostpath', 'other-secret', 'other-configmap',
    'larger-workspace', 'private-subpath', 'mount-propagation', 'host-port', 'extra-port', 'token-projection'])
def test_volumes_mounts_ports_and_credentials_are_measured(expected, change):
    observed = deepcopy(expected)
    spec, worker = observed['spec'], observed['spec']['containers'][0]
    if change == 'extra-volume':
        spec['volumes'].append({'name': 'extra', 'secret': {'secretName': 'private'}})
    elif change == 'hostpath':
        spec['volumes'][0] = {'name': 'config', 'hostPath': {'path': '/'}}
    elif change == 'other-secret':
        next(v['secret'] for v in spec['volumes'] if 'secret' in v)['secretName'] = 'private'
    elif change == 'other-configmap':
        next(v['configMap'] for v in spec['volumes'] if 'configMap' in v)['name'] = 'unreviewed'
    elif change == 'larger-workspace':
        next(v['emptyDir'] for v in spec['volumes'] if v['name'] == 'work')['sizeLimit'] = '2Gi'
    elif change == 'private-subpath':
        worker['volumeMounts'][0]['subPath'] = 'somewhere-else'
    elif change == 'mount-propagation':
        worker['volumeMounts'][0]['mountPropagation'] = 'Bidirectional'
    elif change == 'host-port':
        worker['ports'][0]['hostPort'] = 8090
    elif change == 'extra-port':
        worker['ports'].append({'name': 'extra', 'containerPort': 22})
    elif change == 'token-projection':
        spec['volumes'][0] = {'name': 'config', 'projected': {'sources': [{'serviceAccountToken': {'path': 'token'}}]}}
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)


@pytest.mark.parametrize('path,value', [('cpu', '2'), ('cpu', '1000001u'),
    ('memory', '1Gi'), ('memory', '1e100000'), ('memory', 'NaN')])
def test_limits_cannot_expand_or_use_unbounded_quantities(expected, path, value):
    observed = deepcopy(expected)
    observed['spec']['containers'][0]['resources']['limits'][path] = value
    with pytest.raises(ServiceError):
        validate_pod(observed, expected)


def test_metadata_intent_annotation_alone_does_not_authorize_changed_pod(expected):
    observed = deepcopy(expected)
    observed['spec']['containers'][0]['command'] = ['/bin/sh']
    assert observed['metadata']['annotations'] == expected['metadata']['annotations']
    with pytest.raises(ServiceError) as exc:
        validate_pod(observed, expected)
    assert exc.value.code == 'kubernetes_resource_mismatch'


@pytest.mark.parametrize('value', [None, [], {'spec': None}, {'spec': {'containers': [None]}}])
def test_malformed_api_objects_return_only_fixed_error(expected, value):
    with pytest.raises(ServiceError) as exc:
        validate_pod(value, expected)
    assert str(exc.value) == 'kubernetes_resource_mismatch'
