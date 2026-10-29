"""Offline contracts for the production execution extension, no cluster/model calls."""
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'deploy/hermes'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


runtime = module('ppt_hermes_runtime_deploy', DIRECTORY / 'runtime.py')
build = module('ppt_hermes_runtime_build', DIRECTORY / 'build_runtime.py')
CONTROLLER_IMAGE = 'arm-cluster-master:5000/ppt-hermes-controller@sha256:' + 'a' * 64
WORKER_IMAGE = 'arm-cluster-master:5000/ppt-hermes-worker@sha256:' + 'b' * 64


def manifest(**overrides):
    return runtime.render(**dict(controller_image=CONTROLLER_IMAGE, worker_image=WORKER_IMAGE,
        kube_api_cidr=['10.96.0.1/32', '192.168.137.101/32'], **overrides))


def resource(result, kind, name):
    return next(row for row in result['items'] if row['kind'] == kind and row['metadata']['name'] == name)


def test_extension_preserves_existing_site_database_and_data():
    result = manifest()
    assert all(row['metadata']['namespace'] == 'ppt-service' for row in result['items'])
    assert not ({'Namespace', 'StatefulSet', 'PersistentVolumeClaim', 'Secret', 'ConfigMap', 'Job'} &
                {row['kind'] for row in result['items']})
    deployments = [row for row in result['items'] if row['kind'] == 'Deployment']
    assert len(deployments) == 1 and deployments[0]['metadata']['name'] == runtime.CONTROLLER
    pod = deployments[0]['spec']['template']['spec']
    assert pod['volumes'][0]['persistentVolumeClaim']['claimName'] == 'ppt-data'
    assert not any('hostPath' in value for value in pod['volumes'])


def test_controller_is_trusted_and_tokens_never_reach_task_service_account():
    result = manifest()
    trusted = resource(result, 'ServiceAccount', runtime.CONTROLLER)
    worker = resource(result, 'ServiceAccount', 'ppt-worker')
    assert trusted['automountServiceAccountToken'] is True
    assert worker['automountServiceAccountToken'] is False
    role = resource(result, 'Role', runtime.CONTROLLER)
    assert {rule['resources'][0] for rule in role['rules']} == {'pods', 'secrets', 'networkpolicies'}
    assert all('*' not in json.dumps(rule) for rule in role['rules'])
    secret_rule = next(rule for rule in role['rules'] if rule['resources'] == ['secrets'])
    assert 'list' not in secret_rule['verbs']
    security = resource(result, 'Deployment', runtime.CONTROLLER)['spec']['template']['spec']['containers'][0]['securityContext']
    assert security['capabilities'] == {'drop': ['ALL']}
    assert security['allowPrivilegeEscalation'] is False and security['readOnlyRootFilesystem'] is True


def test_controller_and_task_manifests_fit_restricted_policy_and_shared_file_owner(tmp_path):
    import uuid
    from ppt_service.control.execution import RuntimeConfig
    from ppt_service.control.kubernetes import KubeSettings, KubernetesBackend

    controller_pod = resource(manifest(), 'Deployment', runtime.CONTROLLER)['spec']['template']['spec']
    backend = KubernetesBackend(RuntimeConfig(tmp_path), KubeSettings(WORKER_IMAGE), client=SimpleNamespace())
    handle = backend.register_sandbox_scope(str(uuid.uuid4()))
    modules = {'worker': 'ppt_service.worker.execute', 'proxy': 'ppt_service.worker.model_proxy',
               'sandbox': 'ppt_service.control.parsing'}
    pods = [controller_pod, *[backend.pod(handle, 'security-probe-' + role, role, module, [])['spec']
                             for role, module in modules.items()]]
    for pod in pods:
        assert not any(pod.get(key, False) for key in ('hostNetwork', 'hostPID', 'hostIPC'))
        assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
        assert not any('hostPath' in volume for volume in pod['volumes'])
        for container in pod['containers']:
            effective = pod.get('securityContext', {}) | container['securityContext']
            # The shared data layout is 10000:10000/0700. All components can
            # handle their own files without root or added Linux capabilities.
            assert effective['runAsUser'] == effective['runAsGroup'] == 10000
            assert effective['runAsNonRoot'] is True
            assert effective.get('privileged', False) is False
            assert effective['allowPrivilegeEscalation'] is False
            assert effective['readOnlyRootFilesystem'] is True
            assert effective['capabilities'].get('add', []) == []
            assert 'ALL' in effective['capabilities']['drop']


def test_controller_receives_only_runtime_database_secret_and_disabled_default():
    result = manifest()
    container = resource(result, 'Deployment', runtime.CONTROLLER)['spec']['template']['spec']['containers'][0]
    settings = {row['name']: row for row in container['env']}
    assert settings['DATABASE_URL']['valueFrom']['secretKeyRef'] == {
        'name': 'ppt-database-runtime', 'key': 'DATABASE_URL'}
    assert [row['name'] for row in container['env'] if 'valueFrom' in row] == ['DATABASE_URL']
    assert 'envFrom' not in container
    assert settings['PPT_PROVIDER_SECRET']['value'] == 'ppt-model-upstream'
    assert settings['PPT_PROVIDER_SECRET_KEY']['value'] == 'PPT_UPSTREAM_API_KEY'
    assert 'PPT_UPSTREAM_API_KEY' not in settings
    assert settings['PPT_ALLOW_PAID_EXECUTION']['value'] == 'false'
    assert settings['PPT_CONTROL_STATE']['value'] == 'disabled'


def test_additive_dns_policy_no_longer_permits_task_dns():
    result = manifest()
    dns = resource(result, 'NetworkPolicy', 'dns-egress')['spec']
    expression = dns['podSelector']['matchExpressions'][0]
    assert expression['key'] == 'app.kubernetes.io/name' and expression['operator'] == 'In'
    assert expression['values'] == ['ppt-site', 'ppt-migrator', runtime.CONTROLLER]
    egress = resource(result, 'NetworkPolicy', 'hermes-controller-egress')['spec']['egress']
    api = egress[1]
    assert api['to'] == [{'ipBlock': {'cidr': '10.96.0.1/32'}},
                         {'ipBlock': {'cidr': '192.168.137.101/32'}}]
    assert api['ports'] == [{'protocol': 'TCP', 'port': 6443}]
    denied = resource(result, 'NetworkPolicy', 'hermes-task-default-deny')['spec']
    assert denied['egress'] == [] and denied['ingress'] == []
    # Per-attempt exact policies come from the controller, never broad static grants.
    assert all('0.0.0.0/0' not in json.dumps(row) for row in result['items'])


def test_only_controller_reaches_shared_postgres_without_managing_database_ingress():
    result = manifest()
    policies = {row['metadata']['name']: row for row in result['items'] if row['kind'] == 'NetworkPolicy'}
    controller = policies['hermes-controller-egress']['spec']
    assert controller['podSelector'] == {'matchLabels': {'app': runtime.CONTROLLER}}
    assert controller['policyTypes'] == ['Egress']
    assert controller['egress'][0] == {'to': [{
        'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'data'}},
        'podSelector': {'matchLabels': {'app': 'postgres'}},
    }], 'ports': [{'protocol': 'TCP', 'port': 5432}]}
    database_grants = [name for name, row in policies.items()
                       if any(port['port'] == 5432 for rule in row['spec'].get('egress', [])
                              for port in rule.get('ports', []))]
    assert database_grants == ['hermes-controller-egress']
    assert 'hermes-controller-postgres-ingress' not in policies
    assert all(row['metadata']['namespace'] == 'ppt-service' for row in result['items'])
    assert not any(rule.get('ports') for row in policies.values() for rule in row['spec'].get('ingress', []))
    denied = policies['hermes-task-default-deny']['spec']
    assert denied['podSelector']['matchExpressions'] == [{
        'key': 'app', 'operator': 'In',
        'values': ['ppt-hermes-worker', 'ppt-model-proxy', 'ppt-sandbox'],
    }]
    assert denied['egress'] == denied['ingress'] == []


@pytest.mark.parametrize('changes', [
    {'kube_api_cidr': []}, {'kube_api_cidr': ['0.0.0.0/0']},
    {'kube_api_cidr': ['192.168.137.0/24']}, {'kube_api_port': [0]},
    {'worker_image': 'sha256:' + 'b' * 64}, {'controller_image': 'worker:latest'},
    {'namespace': '../ppt-service'}, {'pvc_data_prefix': '../state'},
    {'pvc_data_prefix': '/state'}, {'worker_service_account': runtime.CONTROLLER},
])
def test_unsafe_inputs_fail_before_rendering(changes):
    arguments = dict(controller_image=CONTROLLER_IMAGE, worker_image=WORKER_IMAGE,
                     kube_api_cidr=['10.96.0.1/32'])
    arguments.update(changes)
    with pytest.raises(ValueError):
        runtime.render(**arguments)


def test_source_context_excludes_private_data_and_pins_reviewed_current_sources():
    context, digest = build.source_context(DIRECTORY / 'Dockerfile.worker')
    with tarfile.open(fileobj=io.BytesIO(context), mode='r:') as archive:
        names = archive.getnames()
        assert 'code/worker/execute.py' in names
        assert 'code/control/dispatcher.py' in names
        assert 'code/server/repository.py' in names
        assert 'code/server/review_revisions.py' in names
        assert 'code/server/acceptance/resolver.py' in names
        assert 'code/server/acceptance/store.py' in names
        assert not any('.env' in name or '/.venv/' in name or 'HOME' in name or '/tests/' in name for name in names)
        assert all(name.endswith('.py') or name in ('Dockerfile', 'source-manifest.json', 'requirements-control.lock') for name in names)
        assert not any('/contracts/' in name or '/demo' in name or 'generate_contract' in name for name in names)
        receipt = json.load(archive.extractfile('source-manifest.json'))
        assert receipt['worker/execute.py']
    assert len(digest) == 64


@pytest.mark.parametrize('dockerfile', ['Dockerfile.controller', 'Dockerfile.worker'])
def test_packaged_agent_sources_import_without_the_checkout(tmp_path, dockerfile):
    context, digest = build.source_context(DIRECTORY / dockerfile)
    with tarfile.open(fileobj=io.BytesIO(context), mode='r:') as archive:
        receipt = json.load(archive.extractfile('source-manifest.json'))
        diagnostics = archive.extractfile('agent-code/agent_service/diagnostics.py').read()
        assert receipt['agent_service/diagnostics.py'] == hashlib.sha256(diagnostics).hexdigest()
        assert digest == hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
        # Reproduce only the explicit Agent COPY subtree. Do not let the source
        # checkout or PYTHONPATH hide a missing transitive runtime dependency.
        for member in archive.getmembers():
            if member.name.startswith('agent-code/agent_service/'):
                assert member.isfile()
                target = tmp_path / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
    result = subprocess.run([sys.executable, '-I', '-c',
        'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
        'from agent_service import engines, client; from agent_service.profiles import ppt; '
        'assert Path(engines.__file__).is_relative_to(Path(sys.argv[1])); '
        'assert Path(engines.log_execution_failure.__code__.co_filename).is_relative_to(Path(sys.argv[1]))',
        str(tmp_path / 'agent-code')], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_build_writes_registry_manifest_receipts_only_after_both_pushes(tmp_path):
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if args[:3] == ['docker', 'image', 'inspect']:
            if args[-1] == '{{json .}}':
                return SimpleNamespace(stdout=json.dumps({'Id': 'sha256:' + 'd' * 64, 'Architecture': 'arm64'}))
            repository = args[3].rsplit(':', 1)[0]
            return SimpleNamespace(stdout=json.dumps([repository + '@sha256:' + 'c' * 64]))
        return SimpleNamespace(stdout='')

    receipt = build.build(site_base=CONTROLLER_IMAGE, worker_base='sha256:' + 'd' * 64,
        controller_tag='registry:5000/controller:review', worker_tag='registry:5000/worker:review',
        push=True, output=tmp_path, runner=runner)
    assert (tmp_path / 'worker-image.txt').read_text().strip() == receipt['worker']['image']
    assert (tmp_path / 'controller-image.txt').read_text().strip() == receipt['controller']['image']
    assert len([args for args, _ in calls if args[:2] == ['docker', 'push']]) == 2
    assert all(kwargs['input'] for args, kwargs in calls if args[:2] == ['docker', 'build'])
    assert ['docker', 'tag', 'sha256:' + 'd' * 64, 'ppt-hermes-build-base:' + 'd' * 20] in [args for args, _ in calls]


def test_wrong_cached_base_architecture_cannot_build(tmp_path):
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({'Id': 'sha256:' + 'd' * 64, 'Architecture': 'amd64'}))

    with pytest.raises(ValueError, match='cached_worker_base_id_or_architecture_mismatch'):
        build.build(site_base=CONTROLLER_IMAGE, worker_base='sha256:' + 'd' * 64,
            controller_tag='registry/controller:review', worker_tag='registry/worker:review',
            output=tmp_path, runner=runner)
    assert not any('build' in args for args in calls)


def test_wrong_repository_digest_is_rejected_without_writing_receipt(tmp_path):
    def runner(args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(['other/worker@sha256:' + 'c' * 64]))
    with pytest.raises(ValueError, match='pushed_repository_digest_unavailable'):
        build.build(site_base=CONTROLLER_IMAGE, worker_base=WORKER_IMAGE,
            controller_tag='registry/controller:review', worker_tag='registry/worker:review',
            push=True, output=tmp_path, runner=runner)
    assert not (tmp_path / 'build.json').exists()


def test_preflight_failure_makes_no_mutation_and_discloses_no_credentials():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        if 'pvc' in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'spec': {'accessModes': ['ReadWriteOnce']}, 'status': {'phase': 'Bound'}}))
        return SimpleNamespace(returncode=0, stdout='existing')

    with pytest.raises(RuntimeError, match='existing_bound_rwx_pvc_required'):
        runtime.deploy(manifest(), runner=runner)
    assert not any('apply' in args for args in calls)


def test_deploy_checks_established_resources_then_dry_runs_before_apply():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        body = 'existing'
        if 'pvc' in args:
            body = json.dumps({'spec': {'accessModes': ['ReadWriteMany']}, 'status': {'phase': 'Bound'}})
        if 'secret' in args:
            key = 'DATABASE_URL' if 'ppt-database-runtime' in args else 'PPT_UPSTREAM_API_KEY'
            body = json.dumps({'data': {key: 'c3ludGhldGlj'}})
        return SimpleNamespace(returncode=0, stdout=body)

    runtime.deploy(manifest(), runner=runner)
    mutations = [args for args, _ in calls if 'apply' in args]
    assert mutations == [['kubectl', 'apply', '--dry-run=server', '-f', '-'], ['kubectl', 'apply', '-f', '-']]
    assert all('create' not in args and 'delete' not in args for args, _ in calls)


def test_baseline_dns_keeps_the_same_task_boundary_after_website_upgrade():
    import yaml
    baseline = list(yaml.safe_load_all((ROOT / 'deploy/k8s/base/networkpolicy.yaml').read_text()))
    expected = resource(manifest(), 'NetworkPolicy', 'dns-egress')['spec']['podSelector']
    actual = next(item for item in baseline if item['metadata']['name'] == 'dns-egress')['spec']['podSelector']
    assert actual == expected


def test_fixed_api_endpoint_matches_tls_host_and_network_policy():
    container = resource(manifest(), 'Deployment', runtime.CONTROLLER)['spec']['template']['spec']['containers'][0]
    settings = {item['name']: item.get('value') for item in container['env']}
    assert settings['PPT_KUBE_API_SERVER'] == 'https://192.168.137.101:6443'
    with pytest.raises(ValueError, match='kubernetes_api_server_not_allowed_by_policy'):
        runtime.render(controller_image=CONTROLLER_IMAGE, worker_image=WORKER_IMAGE,
                       kube_api_cidr=['10.96.0.1/32'])


def test_reviewed_chart_oracle_is_a_single_regular_readonly_file():
    result = manifest(chart_oracle_configmap='ppt-reviewed-oracles')
    pod = resource(result, 'Deployment', runtime.CONTROLLER)['spec']['template']['spec']
    container = pod['containers'][0]
    mount = next(value for value in container['volumeMounts'] if value['name'] == 'chart-oracle')
    assert mount == {'name': 'chart-oracle', 'mountPath': '/etc/ppt-oracle/oracles.json',
                     'subPath': 'oracles.json', 'readOnly': True}
    assert next(value for value in pod['volumes'] if value['name'] == 'chart-oracle')['configMap']['name'] == 'ppt-reviewed-oracles'


def test_site_cannot_enable_before_controller_is_enabled():
    calls = []
    with pytest.raises(ValueError, match='enable_site_requires_enabled_controller'):
        runtime.deploy(manifest(), enable_site=True, runner=lambda *args, **kwargs: calls.append(args))
    assert calls == []


def test_explicit_enable_site_updates_only_two_public_keys_after_controller_rollout():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        body = 'existing'
        if 'pvc' in args:
            body = json.dumps({'spec': {'accessModes': ['ReadWriteMany']}, 'status': {'phase': 'Bound'}})
        if 'secret' in args:
            key = 'DATABASE_URL' if 'ppt-database-runtime' in args else 'PPT_UPSTREAM_API_KEY'
            body = json.dumps({'data': {key: 'c3ludGhldGlj'}})
        return SimpleNamespace(returncode=0, stdout=body)

    runtime.deploy(manifest(enabled=True), enable_site=True, runner=runner)
    index = next(i for i, args in enumerate(calls) if 'patch' in args)
    assert 'deployment/' + runtime.CONTROLLER in calls[index - 1]
    patch = calls[index]
    assert json.loads(patch[-1]) == {'data': {'PPT_EXECUTION_ENABLED': 'true',
                                           'PPT_EXECUTION_BACKEND': 'kubernetes'}}
    assert calls[-2] == ['kubectl', '-n', 'ppt-service', 'rollout', 'restart', 'deployment/ppt-site']
    assert calls[-1] == ['kubectl', '-n', 'ppt-service', 'rollout', 'status', 'deployment/ppt-site', '--timeout=180s']
