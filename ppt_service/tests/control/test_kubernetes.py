"""Lifecycle tests use API-shaped observations, never synthetic stop success."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import ssl
import urllib.error
import uuid

import pytest

from ppt_service.control.execution import Attempt, ControlError, Layout, RuntimeConfig, RuntimeState
from ppt_service.control.kubernetes import (FINALIZER, INTENT, ROLE, SCOPE, KubeError,
                                           KubeSettings, KubernetesBackend)
from ppt_service.control.kubernetes import KubernetesClient, _NoRedirect


class FakeAPI:
    def __init__(self):
        self.objects, self.calls = {}, []
        self.counter = 0
        self.fail_list = False
        self.terminate_on_delete = True
        self.lost_response = None
        self.on_pod = None

    def terminal(self, obj, exit_code=0):
        names = [c['name'] for c in obj['spec']['containers']]
        obj['status'] = {'phase': 'Succeeded', 'containerStatuses': [
            {'name': name, 'state': {'terminated': {'exitCode': exit_code, 'finishedAt': '2026-10-24T00:00:00Z',
                                                  'containerID': 'containerd://' + name}}} for name in names]}

    def request(self, method, resource, name=None, body=None, query=None):
        self.calls.append((method, resource, name, copy.deepcopy(body), query))
        if method == 'GET' and name is None:
            if self.fail_list:
                raise KubeError(503)
            selector = dict(pair.split('=', 1) for pair in query['labelSelector'].split(','))
            return {'metadata': {}, 'items': [copy.deepcopy(obj) for (kind, _), obj in self.objects.items()
                    if kind == resource and all(obj['metadata']['labels'].get(k) == v for k, v in selector.items())]}
        if method == 'POST':
            name = body['metadata']['name']
            if (resource, name) in self.objects:
                raise KubeError(409)
            obj = copy.deepcopy(body)
            self.counter += 1
            obj['metadata'].update(uid='api-uid-' + str(self.counter), resourceVersion=str(self.counter))
            if resource == 'pods':
                role = obj['metadata']['labels'][ROLE]
                obj['status'] = {'phase': 'Running', 'podIP': '10.42.1.15', 'containerStatuses': [
                    {'name': role, 'ready': True, 'state': {'running': {'startedAt': '2026-10-24T00:00:00Z'}}}]}
                if self.on_pod:
                    self.on_pod(obj)
            self.objects[resource, name] = obj
            if self.lost_response == resource:
                raise KubeError()
            return copy.deepcopy(obj)
        key = (resource, name)
        if key not in self.objects:
            raise KubeError(404)
        obj = self.objects[key]
        if method == 'GET':
            return copy.deepcopy(obj)
        if method == 'DELETE':
            assert body['preconditions']['uid'] == obj['metadata']['uid']
            if resource == 'pods' and obj['metadata'].get('finalizers'):
                obj['metadata']['deletionTimestamp'] = '2026-10-24T00:01:00Z'
                if self.terminate_on_delete:
                    self.terminal(obj)
            else:
                del self.objects[key]
            return {'kind': 'Status', 'status': 'Success'}
        if method == 'PATCH':
            assert body[0] == {'op': 'test', 'path': '/metadata/uid', 'value': obj['metadata']['uid']}
            assert body[1]['value'] == obj['metadata']['resourceVersion']
            obj['metadata']['finalizers'] = body[2]['value']
            result = copy.deepcopy(obj)
            if not body[2]['value'] and obj['metadata'].get('deletionTimestamp'):
                del self.objects[key]
            return result
        raise AssertionError((method, resource, name))


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path.parent / ('k-' + uuid.uuid4().hex[:7])
    root.mkdir()
    config = RuntimeConfig(root, state=RuntimeState.CONFIGURED, allow_paid_execution=True, policy_approved=True,
                           parse_timeout=1, validation_timeout=1)
    settings = KubeSettings(worker_image='registry.internal:5000/ppt-worker@sha256:' + 'a' * 64,
                            startup_timeout=0.05, stop_timeout=0.025, poll_seconds=0.001)
    api = FakeAPI()
    backend = KubernetesBackend(config, settings, api)
    attempt = Attempt(str(uuid.uuid4()), str(uuid.uuid4()), 1, 7, 'plan_initial', {'hash': 'b' * 64}, 'ppt-session')
    return config, settings, api, backend, attempt


def staged(runtime):
    config, settings, api, backend, attempt = runtime
    root = config.data_root / f'projects/{attempt.project_id}/attempts/{attempt.job_id}/{attempt.attempt}-{attempt.token}'
    for name in ('inputs', 'task', 'base', 'home', 'project', 'result', 'audit'):
        (root / name).mkdir(parents=True)
    return Layout(root, *(root / name for name in ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base')))


def test_reconcile_prelaunch_is_durable_and_does_not_break_staging(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.reconcile(attempt)
    assert not (config.data_root / 'projects').exists()
    assert backend._read(h)['launch_not_started'] is True
    replacement = KubernetesBackend(config, settings, api)
    assert replacement.reconcile(attempt) == h
    assert replacement.prove_stopped(h).all_writers_stopped
    assert all(call[0] == 'GET' for call in api.calls)


def test_worker_proxy_manifests_and_credentials_are_separate(runtime):
    config, settings, api, backend, attempt = runtime
    layout = staged(runtime)
    h = backend.create(attempt, layout)
    worker, proxy = (api.objects['pods', name] for name in (h.worker_name, h.proxy_name))
    for obj in (worker, proxy):
        spec = obj['spec']
        assert not spec['automountServiceAccountToken']
        assert not any(spec[k] for k in ('hostNetwork', 'hostIPC', 'hostPID'))
        assert FINALIZER in obj['metadata']['finalizers']
        assert spec['securityContext']['runAsNonRoot'] is True
        container = spec['containers'][0]
        assert container['image'] == settings.worker_image
        assert {'name': 'PPT_RUNTIME_WORKER_IMAGE', 'value': settings.worker_image} in container['env']
        assert container['securityContext']['capabilities'] == {'drop': ['ALL']}
        assert container['securityContext']['readOnlyRootFilesystem']
        assert not container['securityContext']['allowPrivilegeEscalation']
        assert all('hostPath' not in volume for volume in spec['volumes'])
    worker_mounts = worker['spec']['containers'][0]['volumeMounts']
    mounted = {row['mountPath']: row for row in worker_mounts if row['name'] == 'data'}
    assert set(mounted) == {'/work/' + part for part in ('inputs', 'task', 'base', 'home', 'project', 'result')}
    assert all(mounted['/work/' + part]['readOnly'] for part in ('inputs', 'task', 'base'))
    assert not any(mounted['/work/' + part]['readOnly'] for part in ('home', 'project', 'result'))
    assert all(row['subPath'].startswith('state/projects/' + attempt.project_id + '/attempts/') for row in mounted.values())
    worker_secrets = {row['valueFrom']['secretKeyRef']['name'] for row in worker['spec']['containers'][0]['env'] if 'valueFrom' in row}
    proxy_secrets = {row['valueFrom']['secretKeyRef']['name'] for row in proxy['spec']['containers'][0]['env'] if 'valueFrom' in row}
    assert settings.provider_secret not in worker_secrets
    assert settings.provider_secret in proxy_secrets
    assert len(worker_secrets) == 1 and worker_secrets < proxy_secrets
    assert worker['spec']['hostAliases'] == [{'ip': '10.42.1.15', 'hostnames': ['model-proxy']}]
    assert proxy['spec']['hostAliases'] == [{'ip': config.approved_upstream_ip, 'hostnames': ['custom-llm-proxy.panghuer.top']}]
    journal = json.dumps(backend._read(h))
    secret = next(obj for (kind, _), obj in api.objects.items() if kind == 'secrets')
    assert secret['data']['client-key'] not in journal
    assert (layout.audit / 'kubernetes-resources.json').exists()
    assert backend._read(h)['runtime']['execution_image'] == settings.worker_image


def test_network_policy_is_exact_attempt_and_no_dns(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.reconcile(attempt)
    worker, proxy, sandbox = [backend.policy(h, 'policy-' + role, role)['spec'] for role in ('worker', 'proxy', 'sandbox')]
    assert worker['egress'][0]['to'] == [{'podSelector': {'matchLabels': {SCOPE: h.uid, ROLE: 'proxy'}}}]
    assert worker['egress'][0]['ports'] == [{'protocol': 'TCP', 'port': 8080}]
    assert worker['ingress'] == []
    assert proxy['egress'] == [{'to': [{'ipBlock': {'cidr': '62.234.50.20/32'}}], 'ports': [{'protocol': 'TCP', 'port': 443}]}]
    assert sandbox['egress'] == sandbox['ingress'] == []


def test_stop_requires_terminated_statuses_retains_finalizers_then_cleans(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.create(attempt, staged(runtime))
    assert backend.observe(h).running
    assert not backend.prove_stopped(h).all_writers_stopped
    backend.stop(h)
    assert all(FINALIZER in obj['metadata']['finalizers'] for (kind, _), obj in api.objects.items() if kind == 'pods')
    assert not any(c[0] == 'PATCH' for c in api.calls)
    proof = backend.prove_stopped(h)
    assert proof.all_writers_stopped and len(proof.resource_ids) == 2
    backend.cleanup(h)
    assert not api.objects
    assert backend.prove_stopped(h).all_writers_stopped
    assert all(row.get('stop_receipt') for row in backend._read(h)['resources'] if row['resource'] == 'pods')


@pytest.mark.parametrize('fault', ['missing', 'uid', 'extra', 'api', 'no_status', 'unknown', 'extra_container', 'finalizer', 'image', 'image_env'])
def test_uncertain_or_foreign_writer_never_proves_stopped(runtime, fault):
    config, settings, api, backend, attempt = runtime
    h = backend.create(attempt, staged(runtime))
    for (kind, _), obj in list(api.objects.items()):
        if kind == 'pods':
            api.terminal(obj)
    obj = api.objects['pods', h.worker_name]
    if fault == 'missing':
        del api.objects['pods', h.worker_name]
    elif fault == 'uid':
        obj['metadata']['uid'] = 'replacement-uid'
    elif fault == 'extra':
        other = copy.deepcopy(obj)
        other['metadata']['name'] = 'unknown-writer'
        api.objects['pods', 'unknown-writer'] = other
    elif fault == 'api':
        api.fail_list = True
    elif fault == 'no_status':
        obj['status'] = {'phase': 'Succeeded'}
    elif fault == 'unknown':
        obj['status']['containerStatuses'][0]['state']['terminated']['reason'] = 'ContainerStatusUnknown'
    elif fault == 'extra_container':
        obj['status']['ephemeralContainerStatuses'] = [{'name': 'debug'}]
    elif fault == 'finalizer':
        obj['metadata']['finalizers'] = []
    elif fault == 'image':
        obj['spec']['containers'][0]['image'] = settings.worker_image.replace('a' * 64, 'f' * 64)
    elif fault == 'image_env':
        obj['spec']['containers'][0]['env'][0]['value'] = 'forged-image'
    assert not backend.prove_stopped(h).all_writers_stopped
    with pytest.raises(ControlError):
        backend.cleanup(h)
    assert not any(c[0] == 'PATCH' for c in api.calls)


def test_create_response_loss_reconciles_only_matching_intent(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.reconcile(attempt)
    mount = config.data_root / 'sandbox'
    mount.mkdir()
    body = backend.pod(h, 'pending-pod', 'sandbox', 'ppt_service.control.parsing', [(mount, '/inputs', True)])
    api.lost_response = 'pods'
    with pytest.raises(KubeError):
        backend.create_resource(h, 'pods', body)
    entry = backend._read(h)['resources'][0]
    assert entry['api_uid'] is None and entry['submitted']
    backend.stop(h)
    assert backend.prove_stopped(h).all_writers_stopped
    # No matching object after an issued POST is never an empty-launch proof.
    del api.objects['pods', 'pending-pod']
    assert not backend.prove_stopped(h).all_writers_stopped


def test_revoked_attempt_cannot_launch_worker(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.reconcile(attempt)
    backend.stop(h)
    with pytest.raises(ControlError, match='execution_revoked'):
        backend.create_resource(h, 'networkpolicies', backend.policy(h, 'later', 'worker'))


def test_settings_reject_local_image_id_and_unsafe_mount(runtime):
    config, settings, api, backend, attempt = runtime
    with pytest.raises(ControlError):
        KubeSettings(worker_image=config.worker_image)
    with pytest.raises(ControlError):
        KubeSettings(worker_image=settings.worker_image, pvc_data_prefix='../state')
    with pytest.raises(ControlError):
        backend.subpath(config.data_root.parent)


def test_disabled_config_never_submits_paid_work(runtime):
    config, settings, api, backend, attempt = runtime
    disabled = KubernetesBackend(RuntimeConfig(config.data_root), settings, api)
    with pytest.raises(ControlError, match='execution_disabled'):
        disabled.create(attempt, staged(runtime))
    assert not api.calls


def test_cleanup_recovers_lost_finalizer_patch_response_from_prior_receipt(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.create(attempt, staged(runtime))
    backend.stop(h)
    original = api.request
    lost_once = [False]
    def request(method, resource, name=None, body=None, query=None):
        result = original(method, resource, name, body, query)
        if method == 'PATCH' and not lost_once[0]:
            lost_once[0] = True
            raise KubeError()
        return result
    api.request = request
    with pytest.raises(KubeError):
        backend.cleanup(h)
    replacement = KubernetesBackend(config, settings, api)
    assert replacement.prove_stopped(h).all_writers_stopped
    replacement.cleanup(h)
    assert not api.objects


def test_stop_does_not_touch_other_attempt_resources(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.create(attempt, staged(runtime))
    foreign = copy.deepcopy(api.objects['pods', h.worker_name])
    foreign['metadata']['name'] = 'foreign-worker'
    foreign['metadata']['labels'][SCOPE] = str(uuid.uuid4())
    api.objects['pods', 'foreign-worker'] = foreign
    backend.stop(h)
    backend.cleanup(h)
    assert list(api.objects) == [('pods', 'foreign-worker')]
    assert not foreign['metadata'].get('deletionTimestamp')


def test_api_client_uses_verified_tls_rotating_token_and_silent_http_errors(tmp_path, monkeypatch):
    context = ssl.create_default_context()
    calls = []
    def configured_ca(*, cafile):
        calls.append(cafile)
        return context
    monkeypatch.setattr(ssl, 'create_default_context', configured_ca)
    token = tmp_path / 'token'
    token.write_text('first-test-token')
    client = KubernetesClient('ppt-service', api_server='https://kubernetes.default.svc:443',
                              token_file=token, ca_file='/serviceaccount/ca.crt')
    assert calls == ['/serviceaccount/ca.crt']
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert any(isinstance(handler, _NoRedirect) for handler in client.opener.handlers)
    observed = []
    class Opener:
        def open(self, request, timeout):
            observed.append(request)
            raise urllib.error.HTTPError(request.full_url, 403, 'private-provider-detail', {}, None)
    client.opener = Opener()
    for expected in ('first-test-token', 'rotated-test-token'):
        token.write_text(expected)
        with pytest.raises(KubeError) as raised:
            client.request('GET', 'pods', query={'labelSelector': SCOPE + '=' + 'scope'})
        assert raised.value.status == 403
        assert str(raised.value) == 'kubernetes_api_unavailable'
        assert observed[-1].get_header('Authorization') == 'Bearer ' + expected
    assert observed[-1].full_url.startswith('https://kubernetes.default.svc:443/api/v1/namespaces/ppt-service/pods?')


def test_api_client_rejects_unverified_plain_http_before_loading_credentials():
    with pytest.raises(ControlError):
        KubernetesClient('ppt-service', api_server='http://kubernetes.default.svc')


def test_fresh_admission_check_after_proxy_ready_prevents_cancelled_worker(runtime):
    config, settings, api, backend, attempt = runtime
    def revoked(current):
        assert current == attempt
        assert any(kind == 'pods' and obj['metadata']['labels'][ROLE] == 'proxy'
                   for (kind, _), obj in api.objects.items())
        raise ControlError('execution_revoked')
    backend.before_worker = revoked
    with pytest.raises(ControlError, match='execution_revoked'):
        backend.create(attempt, staged(runtime))
    h = backend.reconcile(attempt)
    assert ('pods', h.worker_name) not in api.objects
    backend.stop(h)
    backend.cleanup(h)
    assert not api.objects


def test_rollout_can_stop_prior_digest_but_cannot_launch_with_different_pins(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.create(attempt, staged(runtime))
    replacement = KubernetesBackend(config, replace(settings, worker_image=settings.worker_image.replace('a' * 64, 'f' * 64)), api)
    assert replacement.reconcile(attempt) == h
    assert replacement.execution_image(h) == settings.worker_image
    with pytest.raises(ControlError, match='execution_runtime_mismatch'):
        replacement.create_resource(h, 'networkpolicies', replacement.policy(h, 'new-policy', 'worker'))
    replacement.stop(h)
    assert replacement.prove_stopped(h).all_writers_stopped
    replacement.cleanup(h)
    assert not api.objects


def test_failure_before_any_pod_intent_can_clean_nonwriter_resources(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.reconcile(attempt)
    api.lost_response = 'networkpolicies'
    with pytest.raises(KubeError):
        backend.create_resource(h, 'networkpolicies', backend.policy(h, 'early-network', 'worker'))
    backend.stop(h)
    assert backend.prove_stopped(h).all_writers_stopped
    backend.cleanup(h)
    assert not api.objects
