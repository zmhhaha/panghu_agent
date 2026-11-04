#!/usr/bin/env python3
"""Measure actual Agent Pod admission using non-persisting API-server dry runs.

Read-only discovery and dry-run only: this operator never applies policies,
creates Pods/Secrets, enters a production container or uses business credentials.
Run committed source on the Linux cluster operator host. Rendered policy files
are review inputs; installing them is a separate guarded release operation.
"""
from __future__ import annotations

import argparse
import ast
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types
from uuid import UUID, uuid4


PREFIX = 'agent-service.panghuer.top/'
POLICY = 'agent-capsule-boundaries'
PARAMS = 'agent-capsule-admission'
NAMESPACE = 'agent-workers'
CONTROLLER = 'system:serviceaccount:agent-service:agent-service-dispatcher'
OBSERVER = 'system:serviceaccount:agent-service:agent-service-api'
WORKER = 'system:serviceaccount:agent-workers:default'


def admission_documents(parameters):
    """Render a deny policy preserving each retained pool's measured public pins.

    This intentionally does not change Pod Security Admission to restricted:
    the trusted WorkerHost must retain its reviewed root/mount helper boundary.
    """
    variables = [
        dict(name='key', expression="has(object.metadata.labels) && 'agent-service.panghuer.top/pool' in object.metadata.labels ? 'pool.' + object.metadata.labels['agent-service.panghuer.top/pool'] : ''"),
        dict(name='pin', expression="params != null && variables.key in params.data ? params.data[variables.key].split('|') : []"),
        dict(name='worker', expression='object.spec.containers[0]'),
        dict(name='env', expression='has(variables.worker.env) ? variables.worker.env : []'),
        dict(name='names', expression='variables.env.map(e, e.name)'),
        dict(name='nodekey', expression="'nodes.' + object.metadata.labels['agent-service.panghuer.top/pool']"),
        dict(name='nodes', expression="params != null && variables.nodekey in params.data && params.data[variables.nodekey] != '' ? params.data[variables.nodekey].split('\\n') : []"),
    ]
    checks = [
        ('Registered retained execution pool', "params != null && variables.pin.size() == 11"),
        ('Fixed capsule identity', "object.metadata.labels['app'] == 'agent-capsule' && object.metadata.labels['agent-service.panghuer.top/attempt'].matches('^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') && object.metadata.name == 'agent-' + object.metadata.labels['agent-service.panghuer.top/attempt']"),
        ('No host namespaces, injected containers or service-account token', "object.spec.automountServiceAccountToken == false && object.spec.restartPolicy == 'Never' && object.spec.enableServiceLinks == false && (!has(object.spec.hostPID) || !object.spec.hostPID) && (!has(object.spec.hostIPC) || !object.spec.hostIPC) && (!has(object.spec.hostNetwork) || !object.spec.hostNetwork) && (!has(object.spec.shareProcessNamespace) || !object.spec.shareProcessNamespace) && (!has(object.spec.serviceAccountName) || object.spec.serviceAccountName == 'default') && (!has(object.spec.initContainers) || object.spec.initContainers.size() == 0) && (!has(object.spec.ephemeralContainers) || object.spec.ephemeralContainers.size() == 0)"),
        ('One pinned WorkerHost entrypoint', "object.spec.containers.size() == 1 && variables.worker.name == 'worker' && variables.worker.image == variables.pin[0] && variables.worker.command == ['/opt/agent-service/.venv/bin/python', '-m', 'agent_service.worker_entry'] && variables.worker.args == ['--config', '/etc/agent-worker/config.json', '--secrets', '/run/agent-secrets/worker.json'] && variables.worker.imagePullPolicy == 'IfNotPresent'"),
        ('Fixed runtime deadline and seccomp', "object.spec.activeDeadlineSeconds == int(variables.pin[9]) && object.spec.terminationGracePeriodSeconds == 30 && object.spec.securityContext.seccompProfile.type == 'RuntimeDefault' && (!has(object.spec.securityContext.sysctls) || object.spec.securityContext.sysctls.size() == 0)"),
        ('Minimal reviewed WorkerHost capabilities', "variables.worker.securityContext.runAsUser == 0 && variables.worker.securityContext.runAsGroup == 0 && variables.worker.securityContext.readOnlyRootFilesystem && !variables.worker.securityContext.allowPrivilegeEscalation && (!has(variables.worker.securityContext.privileged) || !variables.worker.securityContext.privileged) && variables.worker.securityContext.capabilities.drop == ['ALL'] && variables.worker.securityContext.capabilities.add.size() == (variables.pin[4] == '1' ? 6 : 5) && variables.worker.securityContext.capabilities.add.all(c, c in ['CHOWN', 'DAC_OVERRIDE', 'KILL', 'SETUID', 'SETGID'] || c == 'SYS_ADMIN' && variables.pin[4] == '1') && (!has(variables.worker.securityContext.procMount) || variables.worker.securityContext.procMount == 'Default')"),
        ('Reviewed mount-helper AppArmor', "variables.pin[4] == '1' ? has(variables.worker.securityContext.appArmorProfile) && variables.worker.securityContext.appArmorProfile.type == 'Localhost' && variables.worker.securityContext.appArmorProfile.localhostProfile == variables.pin[10] : (!has(variables.worker.securityContext.appArmorProfile) || variables.worker.securityContext.appArmorProfile.type == 'RuntimeDefault')"),
        ('No arbitrary environment, credentials or lifecycle hooks', "(!has(variables.worker.envFrom) || variables.worker.envFrom.size() == 0) && (!has(variables.worker.lifecycle)) && (!has(variables.worker.livenessProbe)) && (!has(variables.worker.readinessProbe)) && (!has(variables.worker.startupProbe)) && (!has(variables.worker.workingDir) || variables.worker.workingDir == '') && (!has(variables.worker.volumeDevices) || variables.worker.volumeDevices.size() == 0) && (!has(variables.worker.stdin) || !variables.worker.stdin) && (!has(variables.worker.tty) || !variables.worker.tty) && variables.names.size() == variables.names.unique().size() && variables.names.all(n, n in ['AGENT_ATTEMPT_ID','AGENT_REQUEST_SHA256','AGENT_RUNTIME_IMAGE_DIGEST','AGENT_ENGINE','AGENT_PROFILE_NAME','AGENT_PROFILE_REVISION','AGENT_RUNTIME_REVISION','AGENT_RUNTIME_UID','AGENT_PREVIEW_UID','AGENT_POD_UID','AGENT_EXECUTION_BINDING_SHA256','AGENT_READ_ONLY_MOUNTS']) && ['AGENT_ATTEMPT_ID','AGENT_REQUEST_SHA256','AGENT_RUNTIME_IMAGE_DIGEST','AGENT_ENGINE','AGENT_PROFILE_NAME','AGENT_PROFILE_REVISION','AGENT_RUNTIME_REVISION','AGENT_RUNTIME_UID','AGENT_PREVIEW_UID','AGENT_POD_UID'].all(n, n in variables.names)"),
        ('Only Pod UID uses a field reference', "variables.env.all(e, e.name == 'AGENT_POD_UID' ? has(e.valueFrom) && has(e.valueFrom.fieldRef) && e.valueFrom.fieldRef.fieldPath == 'metadata.uid' && (!has(e.value) || e.value == '') : !has(e.valueFrom))"),
        ('Runtime environment matches its retained pool', "variables.env.exists(e, e.name == 'AGENT_ATTEMPT_ID' && e.value == object.metadata.labels['agent-service.panghuer.top/attempt']) && variables.env.exists(e, e.name == 'AGENT_REQUEST_SHA256' && e.value.matches('^[0-9a-f]{64}$') && e.value == object.metadata.annotations['agent-service.panghuer.top/request']) && variables.env.exists(e, e.name == 'AGENT_RUNTIME_IMAGE_DIGEST' && e.value == variables.pin[0].split('@')[1]) && variables.env.exists(e, e.name == 'AGENT_ENGINE' && e.value == variables.pin[8]) && variables.env.exists(e, e.name == 'AGENT_PROFILE_NAME' && e.value == variables.pin[5]) && variables.env.exists(e, e.name == 'AGENT_PROFILE_REVISION' && e.value == variables.pin[6]) && variables.env.exists(e, e.name == 'AGENT_RUNTIME_REVISION' && e.value == variables.pin[7]) && variables.env.all(e, e.name != 'AGENT_EXECUTION_BINDING_SHA256' || e.value.matches('^[0-9a-f]{64}$')) && (variables.pin[4] == '1' ? variables.env.exists(e, e.name == 'AGENT_READ_ONLY_MOUNTS' && e.value == '1') : !('AGENT_READ_ONLY_MOUNTS' in variables.names))"),
        ('Permanent runtime UID pair is in its retained pool', "variables.env.exists(a, a.name == 'AGENT_RUNTIME_UID' && a.value.matches('^[0-9]{1,10}$') && int(a.value) >= int(variables.pin[2]) && int(a.value) + 1 < int(variables.pin[2]) + int(variables.pin[3]) && (int(a.value) - int(variables.pin[2])) % 2 == 0 && variables.env.exists(b, b.name == 'AGENT_PREVIEW_UID' && b.value.matches('^[0-9]{1,10}$') && int(b.value) == int(a.value) + 1))"),
        ('Only the fixed config, per-attempt credentials and bounded emptyDirs', "object.spec.volumes.size() == 5 && object.spec.volumes.all(v, v.name in ['config','credentials','control','work','tmp']) && object.spec.volumes.exists(v, v.name == 'config' && has(v.configMap) && v.configMap.name == variables.pin[1] && v.configMap.defaultMode == 292) && object.spec.volumes.exists(v, v.name == 'credentials' && has(v.secret) && v.secret.secretName == object.metadata.name + '-auth' && v.secret.defaultMode == 256) && object.spec.volumes.exists(v, v.name == 'control' && has(v.emptyDir) && string(v.emptyDir.sizeLimit) == '2Gi' && (!has(v.emptyDir.medium) || v.emptyDir.medium == '')) && object.spec.volumes.exists(v, v.name == 'work' && has(v.emptyDir) && string(v.emptyDir.sizeLimit) == '1Gi' && (!has(v.emptyDir.medium) || v.emptyDir.medium == '')) && object.spec.volumes.exists(v, v.name == 'tmp' && has(v.emptyDir) && string(v.emptyDir.sizeLimit) == '128Mi' && v.emptyDir.medium == 'Memory')"),
        ('Fixed mount names and locations', "variables.worker.volumeMounts.size() == 5 && variables.worker.volumeMounts.all(m, !has(m.subPath) && !has(m.subPathExpr) && (!has(m.mountPropagation) || m.mountPropagation == 'None') && (m.name == 'config' && m.mountPath == '/etc/agent-worker' && m.readOnly || m.name == 'credentials' && m.mountPath == '/run/agent-secrets' && m.readOnly || m.name == 'control' && m.mountPath == '/run/agent-control' && (!has(m.readOnly) || !m.readOnly) || m.name == 'work' && m.mountPath == '/work' && (!has(m.readOnly) || !m.readOnly) || m.name == 'tmp' && m.mountPath == '/tmp' && (!has(m.readOnly) || !m.readOnly)))"),
        ('Only fixed WorkerHost port, no host port', "variables.worker.ports.size() == 1 && variables.worker.ports[0].name == 'worker' && variables.worker.ports[0].containerPort == 8090 && (!has(variables.worker.ports[0].protocol) || variables.worker.ports[0].protocol == 'TCP') && (!has(variables.worker.ports[0].hostPort) || variables.worker.ports[0].hostPort == 0) && (!has(variables.worker.ports[0].hostIP) || variables.worker.ports[0].hostIP == '')"),
        ('Retained node selector', "variables.nodes.size() == 0 ? (!has(object.spec.nodeSelector) || object.spec.nodeSelector.size() == 0) : has(object.spec.nodeSelector) && object.spec.nodeSelector.size() == variables.nodes.size() && variables.nodes.all(n, n.split('=')[0] in object.spec.nodeSelector && object.spec.nodeSelector[n.split('=')[0]] == n.split('=')[1])"),
        ('Pod execution plan is immutable after admission', 'oldObject == null || object.spec == oldObject.spec'),
    ]
    # Requiring every mandatory name and the exact optional-name count also
    # excludes duplicates, without a quadratic CEL uniqueness expression.
    checks = [(message, expression.replace("variables.names.size() == variables.names.unique().size()", "variables.names.size() == 10 + ('AGENT_EXECUTION_BINDING_SHA256' in variables.names ? 1 : 0) + (variables.pin[4] == '1' ? 1 : 0)")) for message, expression in checks]
    policy = dict(apiVersion='admissionregistration.k8s.io/v1', kind='ValidatingAdmissionPolicy',
        metadata=dict(name=POLICY), spec=dict(failurePolicy='Fail',
            paramKind=dict(apiVersion='v1', kind='ConfigMap'),
            matchConstraints=dict(namespaceSelector=dict(matchLabels={
                'kubernetes.io/metadata.name': NAMESPACE}), resourceRules=[dict(
                apiGroups=[''], apiVersions=['v1'], operations=['CREATE', 'UPDATE'],
                resources=['pods', 'pods/ephemeralcontainers'])]),
            variables=variables, validations=[dict(expression=expression, message=message,
                reason='Forbidden') for message, expression in checks]))
    binding = dict(apiVersion='admissionregistration.k8s.io/v1', kind='ValidatingAdmissionPolicyBinding',
        metadata=dict(name=POLICY), spec=dict(policyName=POLICY,
            paramRef=dict(name=PARAMS, namespace=NAMESPACE, parameterNotFoundAction='Deny'),
            validationActions=['Deny']))
    return [parameters, policy, binding]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def require(value, message):
    if not value:
        raise RuntimeError(message)


class Audit:
    def __init__(self, args):
        self.args = args
        self.kube = ['kubectl', '--request-timeout=15s']
        if args.kubeconfig:
            self.kube += ['--kubeconfig', str(args.kubeconfig)]
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.nonce = uuid4().hex

    def run(self, arguments, *, document=None, check=True):
        result = subprocess.run(self.kube + arguments, text=True, input=(
            json.dumps(document) if document is not None else None),
            capture_output=True, timeout=25, check=False)
        if check and result.returncode:
            raise RuntimeError('kubectl_failed:' + result.stderr.strip()[:900])
        return result

    def get(self, kind, name, namespace=None):
        args = (['-n', namespace] if namespace else []) + ['get', kind, name, '-o', 'json']
        return json.loads(self.run(args).stdout)

    def discover(self):
        deployment = self.get('deployment', 'agent-service-dispatcher', 'agent-service')
        config_name = next(v['configMap']['name'] for v in
            deployment['spec']['template']['spec']['volumes'] if v['name'] == 'config')
        config_map = self.get('configmap', config_name, 'agent-service')
        raw = config_map['data']['config.json']
        self.public = json.loads(raw)
        save(self.output / 'public-config.json', self.public)
        self.identity = dict(config_name=config_name, config_uid=config_map['metadata']['uid'],
            config_resource_version=config_map['metadata']['resourceVersion'],
            config_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            dispatcher_service_account=deployment['spec']['template']['spec']['serviceAccountName'],
            dispatcher_image=deployment['spec']['template']['spec']['containers'][0]['image'],
            namespace=self.get('namespace', NAMESPACE)['metadata'])
        require(self.identity['dispatcher_service_account'] == 'agent-service-dispatcher',
                'unexpected_dispatcher_identity')
        pools = self.public['kubernetes_workers']
        require(0 < len(pools) <= 256 and all(p['namespace'] == NAMESPACE for p in pools),
                'unexpected_pool_scope_or_bound')
        bindings = {(x['id'], x['revision']): x for x in self.public['execution_bindings']}
        tools = {(x['name'], x['revision']): x for x in self.public['tool_environments']}
        contracts = {(x['name'], x['revision']): x for x in self.public['task_contracts']}
        self.pools = []
        self.unprovisionable = []
        data = {}
        for pool in pools:
            binding = bindings[(pool['binding_id'], pool['binding_revision'])]
            tool = tools[(binding['tool_environment']['name'], binding['tool_environment']['revision'])]
            contract = contracts[(binding['profile'], binding['profile_revision'])]
            if pool.get('uid_start') is None or pool.get('uid_count', 65536) < 2:
                require(pool.get('enabled') is False, 'enabled_pool_has_no_runtime_uid_range')
                self.unprovisionable.append(dict(pool=pool['pool_id'], enabled=False,
                    reason='provider_refuses_unreserved_runtime_uid', uid_start=pool.get('uid_start')))
                continue
            row = [pool['image'], pool['host_config_map'], str(pool['uid_start']),
                str(pool['uid_count']), '1' if pool['read_only_mounts'] else '0',
                binding['profile'], binding['profile_revision'], tool['runtime_revision'],
                pool['engine'], str(contract['max_timeout_seconds'] + 90),
                pool.get('mount_apparmor_profile', 'agent-capsule-inputs')]
            require(all('|' not in v and '\n' not in v for v in row), 'invalid_pool_pin')
            data['pool.' + pool['pool_id']] = '|'.join(row)
            data['nodes.' + pool['pool_id']] = '\n'.join(k + '=' + v for k, v in
                sorted(pool.get('node_selector', {}).items()))
            self.pools.append((pool, binding, tool, contract))
        self.parameters = dict(apiVersion='v1', kind='ConfigMap', metadata=dict(
            name=PARAMS, namespace=NAMESPACE, labels={PREFIX + 'purpose': 'capsule-admission'}), data=data)
        save(self.output / 'discovery.json', self.identity)
        save(self.output / 'admission-parameters.json', self.parameters)
        for document in admission_documents(self.parameters):
            save(self.output / (document['kind'] + '.json'), document)

    def real_manifest(self, pool_row):
        """Run only the committed provider's pure manifest renderer, no DB/config loader.

        The envelope and signing material are explicitly synthetic and never sent
        to an executor. This validates production Pod shape, not paid admission.
        """
        pool, binding, tool, contract = pool_row
        path = self.args.source / 'agent_service/kubernetes_provider.py'
        source = path.read_text()
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'KubernetesWorkerAdapter')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_manifest')
        module = ast.Module(body=[method], type_ignores=[])
        scope = dict(UUID=UUID, canonical_bytes=canonical, hashlib=hashlib, base64=base64,
            PREFIX=PREFIX, FINALIZER=PREFIX + 'observed-stop', ServiceError=RuntimeError)
        exec(compile(module, str(path), 'exec'), scope)
        policy = types.SimpleNamespace(matches=lambda _: True,
            max_timeout_seconds=contract['max_timeout_seconds'])
        values = {k: pool[k] for k in ('pool_id', 'namespace', 'image', 'host_config_map',
            'uid_start', 'uid_count', 'port', 'read_only_mounts')}
        values.update(policy=policy, node_selector=tuple(sorted(pool.get('node_selector', {}).items())),
            mount_apparmor_profile=pool.get('mount_apparmor_profile', 'agent-capsule-inputs'))
        adapter = types.SimpleNamespace(pool=types.SimpleNamespace(**values),
            _credentials=lambda _: dict(worker_token='dry-run-only-' + 'a' * 48,
                                       receipt_key_hex='b' * 64))
        attempt = str(uuid4())
        envelope = types.SimpleNamespace(attempt_id=attempt, fence=1, inputs=(), checkpoint_inputs=(),
            request_sha256=hashlib.sha256((self.nonce + attempt).encode()).hexdigest(),
            image_digest=pool['image'].rsplit('@', 1)[1], engine=pool['engine'],
            profile=binding['profile'], profile_revision=binding['profile_revision'],
            runtime_revision=tool['runtime_revision'], execution_binding=types.SimpleNamespace(sha256='c' * 64))
        pod, _ = scope['_manifest'](adapter, envelope)
        return pod

    def dry_run(self, pod, identity, label, expected):
        result = self.run(['--as', identity, '-n', NAMESPACE, 'create', '--dry-run=server',
                          '-f', '-', '-o', 'json'], document=pod, check=False)
        accepted = result.returncode == 0
        # Never persist API-returned Pod data or any real Secret material.
        return dict(case=label, identity=identity, accepted=accepted, expected_accepted=expected,
                    passed=accepted == expected, response_error=result.stderr.strip()[:1000],
                    pod_name=pod['metadata']['name'], pool=pod['metadata']['labels'][PREFIX + 'pool'])

    def probe(self):
        results = []
        for row in self.pools:
            pod = self.real_manifest(row)
            results.append(self.dry_run(pod, CONTROLLER, 'baseline:' + row[0]['pool_id'], True))
        row = next((r for r in self.pools if r[1]['profile'] == 'ppt.generate.v1' and r[0].get('enabled')), None)
        row = row or next(r for r in self.pools if r[1]['profile'].startswith('ppt.'))
        baseline = self.real_manifest(row)
        for actor in (OBSERVER, WORKER):
            results.append(self.dry_run(baseline, actor, 'unauthorized-service-account', False))
        mutations = {
            'privileged': lambda p: p['spec']['containers'][0]['securityContext'].update(privileged=True),
            'host-network': lambda p: p['spec'].update(hostNetwork=True),
            'host-pid': lambda p: p['spec'].update(hostPID=True),
            'serviceaccount-token': lambda p: p['spec'].update(automountServiceAccountToken=True),
            'alternate-serviceaccount': lambda p: p['spec'].update(serviceAccountName='agent-service-dispatcher'),
            'host-path': lambda p: p['spec']['volumes'].append(dict(name='host', hostPath=dict(path='/'))),
            'projected-token': lambda p: p['spec']['volumes'].append(dict(name='token', projected=dict(sources=[dict(serviceAccountToken=dict(path='token'))]))),
            'sidecar': lambda p: p['spec']['containers'].append(copy.deepcopy(p['spec']['containers'][0]) | dict(name='extra')),
            'alternate-command': lambda p: p['spec']['containers'][0].update(command=['/bin/sh', '-c'], args=['sleep 1']),
            'extra-capability': lambda p: p['spec']['containers'][0]['securityContext']['capabilities']['add'].append('SYS_PTRACE'),
            'writable-root': lambda p: p['spec']['containers'][0]['securityContext'].update(readOnlyRootFilesystem=False),
            'privilege-escalation': lambda p: p['spec']['containers'][0]['securityContext'].update(allowPrivilegeEscalation=True),
            'unconfined-seccomp': lambda p: p['spec']['securityContext'].update(seccompProfile=dict(type='Unconfined')),
            'unconfined-apparmor': lambda p: p['spec']['containers'][0]['securityContext'].update(appArmorProfile=dict(type='Unconfined')),
            'wrong-config': lambda p: p['spec']['volumes'][0]['configMap'].update(name='wrong-host-config'),
            'wrong-secret': lambda p: p['spec']['volumes'][1]['secret'].update(secretName='unrelated-credentials'),
            'extra-env-secret': lambda p: p['spec']['containers'][0].update(envFrom=[dict(secretRef=dict(name='unrelated'))]),
            'wrong-image': lambda p: p['spec']['containers'][0].update(image='registry.invalid/unsafe@sha256:' + '0' * 64),
            'wrong-runtime-uid': lambda p: next(e for e in p['spec']['containers'][0]['env'] if e['name']=='AGENT_RUNTIME_UID').update(value='0'),
        }
        for name, mutate in mutations.items():
            pod = copy.deepcopy(baseline)
            mutate(pod)
            results.append(self.dry_run(pod, CONTROLLER, name, False))
        save(self.output / 'admission-probes.json', results)
        summary = dict(passed=all(r['passed'] for r in results), cases=len(results),
            retained_pool_count=len(self.public['kubernetes_workers']),
            provisionable_pools_tested=len(self.pools),
            disabled_unprovisionable_pools=self.unprovisionable,
            accepted_unsafe=[r['case'] for r in results if not r['expected_accepted'] and r['accepted']],
            rejected_baselines=[r['case'] for r in results if r['expected_accepted'] and not r['accepted']],
            production_resources_created=False, operator='server-side-dry-run-only',
            config_sha256=self.identity['config_sha256'], source_commit=subprocess.check_output(
                ['git', '-C', str(self.args.source), 'rev-parse', 'HEAD'], text=True).strip())
        save(self.output / 'summary.json', summary)
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kubeconfig', type=Path)
    args = parser.parse_args()
    require(sys.platform.startswith('linux'), 'server_linux_required')
    audit = Audit(args)
    audit.discover()
    return audit.probe()


if __name__ == '__main__':
    raise SystemExit(main())
