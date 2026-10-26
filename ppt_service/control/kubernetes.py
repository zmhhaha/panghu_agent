"""Namespace-scoped Kubernetes transport for the existing Hermes dispatcher.

The controller owns the private authority journal. A Pod disappearing is never
stop evidence: deletion is allowed only after observing its exact API UID and
every container terminated while our finalizer still retains the object.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .execution import Attempt, ControlError, Handle, Layout, Observation, RuntimeConfig, RuntimeState, StopProof
from .staging import atomic_json, read_json, relative

METADATA_PREFIX = 'ppt-service.panghuer.top/'
LEGACY_METADATA_PREFIX = 'ppt.panghuer.top/'
FINALIZER = METADATA_PREFIX + 'writers-stopped'
SCOPE = METADATA_PREFIX + 'attempt'
ROLE = METADATA_PREFIX + 'role'
INTENT = METADATA_PREFIX + 'intent'
RESOURCES = {'pods': '/api/v1', 'secrets': '/api/v1',
             'networkpolicies': '/apis/networking.k8s.io/v1'}
NAME = re.compile(r'[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?\Z')
IMAGE = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}\Z')
MAX_API_BYTES = 8 * 1024**2


def require(condition, code='invalid_kubernetes_configuration'):
    if not condition:
        raise ControlError(code)


def metadata_keys(record):
    # An existing authority journal keeps its exact resource identity scheme.
    # Never guess a prefix from an untrusted Pod or mix selectors in one scope.
    prefix = record.get('metadata_prefix', LEGACY_METADATA_PREFIX)
    require(prefix in (METADATA_PREFIX, LEGACY_METADATA_PREFIX), 'execution_authority_mismatch')
    return tuple(prefix + key for key in ('attempt', 'role', 'intent', 'writers-stopped'))


@dataclass(frozen=True)
class KubeSettings:
    worker_image: str
    namespace: str = 'ppt-service'
    pvc_name: str = 'ppt-data'
    provider_secret: str = 'ppt-model-upstream'
    provider_secret_key: str = 'PPT_UPSTREAM_API_KEY'
    worker_service_account: str = 'ppt-worker'
    pvc_data_prefix: str = 'state'
    startup_timeout: float = 120
    stop_timeout: float = 45
    poll_seconds: float = 0.5

    def __post_init__(self):
        require(isinstance(self.worker_image, str) and IMAGE.fullmatch(self.worker_image))
        for value in (self.namespace, self.pvc_name, self.provider_secret, self.worker_service_account):
            require(isinstance(value, str) and NAME.fullmatch(value) and len(value) <= 63)
        require(isinstance(self.provider_secret_key, str) and re.fullmatch(r'[-._a-zA-Z0-9]{1,253}', self.provider_secret_key))
        relative(self.pvc_data_prefix)
        require(0 < self.startup_timeout <= 300 and 0 < self.stop_timeout <= 120
                and 0 < self.poll_seconds <= 5)

    @classmethod
    def env(cls):
        return cls(worker_image=os.environ['PPT_WORKER_IMAGE'],
                   namespace=os.getenv('PPT_KUBE_NAMESPACE', 'ppt-service'),
                   pvc_name=os.getenv('PPT_DATA_PVC', 'ppt-data'),
                   provider_secret=os.getenv('PPT_PROVIDER_SECRET', 'ppt-model-upstream'),
                   provider_secret_key=os.getenv('PPT_PROVIDER_SECRET_KEY', 'PPT_UPSTREAM_API_KEY'),
                   worker_service_account=os.getenv('PPT_WORKER_SERVICE_ACCOUNT', 'ppt-worker'),
                   pvc_data_prefix=os.getenv('PPT_PVC_DATA_PREFIX', 'state'))


class KubeError(ControlError):
    def __init__(self, status=None):
        self.status = status
        super().__init__('kubernetes_api_unavailable')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class KubernetesClient:
    """Verified TLS, rotating service-account token, bounded and silent errors."""
    def __init__(self, namespace, *, api_server=None, token_file=None, ca_file=None, timeout=10):
        require(NAME.fullmatch(namespace) and len(namespace) <= 63)
        self.namespace = namespace
        host = os.getenv('KUBERNETES_SERVICE_HOST', '')
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        self.api_server = api_server or os.getenv('PPT_KUBE_API_SERVER') or ('https://' + host + ':' + os.getenv('KUBERNETES_SERVICE_PORT_HTTPS', '443'))
        parsed = urllib.parse.urlsplit(self.api_server)
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                and not parsed.password and parsed.path in ('', '/') and not parsed.query and not parsed.fragment)
        self.api_server = self.api_server.rstrip('/')
        root = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        self.token_file = Path(token_file) if token_file else root / 'token'
        context = ssl.create_default_context(cafile=str(ca_file or root / 'ca.crt'))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPSHandler(context=context), _NoRedirect())
        self.timeout = timeout

    def request(self, method, resource, name=None, body=None, query=None):
        require(resource in RESOURCES and method in ('GET', 'POST', 'PATCH', 'DELETE'))
        require(name is None or (NAME.fullmatch(name) and len(name) <= 253))
        path = RESOURCES[resource] + '/namespaces/' + self.namespace + '/' + resource
        if name:
            path += '/' + name
        if query:
            path += '?' + urllib.parse.urlencode(query)
        try:
            token = self.token_file.read_text(encoding='ascii').strip()
            require(token and not any(c.isspace() for c in token), 'kubernetes_credentials_unavailable')
            raw = None if body is None else json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
            if raw is not None:
                headers['Content-Type'] = 'application/json-patch+json' if method == 'PATCH' else 'application/json'
            request = urllib.request.Request(self.api_server + path, data=raw, headers=headers, method=method)
            with self.opener.open(request, timeout=self.timeout) as response:
                output = response.read(MAX_API_BYTES + 1)
            require(len(output) <= MAX_API_BYTES, 'kubernetes_response_limit')
            result = json.loads(output)
            require(isinstance(result, dict), 'invalid_kubernetes_response')
            return result
        except urllib.error.HTTPError as exc:
            raise KubeError(exc.code) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise KubeError() from None


class KubernetesBackend:
    def __init__(self, config: RuntimeConfig, settings: KubeSettings, client=None, *, sleep=time.sleep,
                 monotonic=time.monotonic, before_worker=None):
        self.config, self.settings = config, settings
        self.client = client or KubernetesClient(settings.namespace)
        self.sleep, self.monotonic = sleep, monotonic
        self.before_worker = before_worker
        self._thread_lock = threading.RLock()
        self.authority = config.data_root.absolute() / 'kubernetes-authority'
        self.authority.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not self.authority.is_symlink(), 'linked_kubernetes_authority')
        self.authority.chmod(0o700)

    @property
    def ready(self):
        # Provider credentials come from the proxy-only K8s Secret, not Docker's env-file.
        return (self.config.state == RuntimeState.CONFIGURED and self.config.allow_paid_execution
                and self.config.policy_approved)

    def _path(self, uid):
        require(str(uuid.UUID(uid)) == uid, 'invalid_execution_identity')
        return self.authority / (uid + '.json')

    @contextmanager
    def _locked(self, uid):
        with self._thread_lock:
            path = self.authority / (uid + '.lock')
            require(not path.is_symlink(), 'linked_kubernetes_authority')
            with path.open('a+b') as stream:
                if os.name == 'posix':
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == 'posix':
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self, h):
        record = read_json(self.authority, self._path(h.uid).name)
        expected = {'uid': h.uid, 'job_id': h.job_id, 'attempt': h.attempt, 'token': h.token}
        require(record.get('identity') == expected and record.get('schema') == 1, 'execution_authority_mismatch')
        pins = record.get('runtime', {})
        require(pins.get('namespace') == self.settings.namespace
                and isinstance(pins.get('execution_image'), str) and IMAGE.fullmatch(pins['execution_image']),
                'execution_runtime_mismatch')
        metadata_keys(record)
        return record

    def runtime_pins(self):
        return {'execution_image': self.settings.worker_image, 'namespace': self.settings.namespace,
                'pvc_name': self.settings.pvc_name, 'pvc_data_prefix': self.settings.pvc_data_prefix,
                'worker_service_account': self.settings.worker_service_account,
                'provider_secret': self.settings.provider_secret,
                'provider_secret_key': self.settings.provider_secret_key}

    def execution_image(self, handle):
        """The exact immutable registry digest recorded for this attempt."""
        return self._read(handle)['runtime']['execution_image']

    def _write(self, h, record):
        atomic_json(self._path(h.uid), record)
        audit = Path(record['audit'])
        # Reconcile must not pre-create a directory that Stager.stage expects absent.
        if audit.is_dir():
            require(not audit.is_symlink(), 'linked_kubernetes_authority')
            atomic_json(audit / 'kubernetes-resources.json', record)

    def reconcile(self, a: Attempt):
        prefix = 'ppt-' + a.uid.replace('-', '')
        h = Handle(a.uid, a.job_id, a.attempt, a.token, prefix + '-worker', prefix + '-proxy')
        with self._locked(h.uid):
            if self._path(h.uid).exists():
                self._read(h)
            else:
                audit = self.config.data_root.absolute() / f'projects/{a.project_id}/attempts/{a.job_id}/{a.attempt}-{a.token}/audit'
                self._write(h, {'schema': 1, 'metadata_prefix': METADATA_PREFIX,
                            'identity': {'uid': h.uid, 'job_id': h.job_id,
                            'attempt': h.attempt, 'token': h.token}, 'audit': str(audit),
                            'project_id': a.project_id, 'phase': a.phase, 'revoked': False,
                            'runtime': self.runtime_pins(), 'launch_not_started': True, 'resources': []})
        return h

    def handle_for_scope(self, uid):
        record = read_json(self.authority, self._path(uid).name)
        identity = record['identity']
        prefix = 'ppt-' + uid.replace('-', '')
        h = Handle(uid, identity['job_id'], identity['attempt'], identity['token'], prefix + '-worker', prefix + '-proxy')
        self._read(h)
        return h

    def register_sandbox_scope(self, uid):
        """Called by a live material claim before staging, never by recovery."""
        self._path(uid)
        prefix = 'ppt-' + uid.replace('-', '')
        h = Handle(uid, str(uuid.uuid5(uuid.NAMESPACE_URL, 'ppt-material-job:' + uid)), 1, 1,
                   prefix + '-worker', prefix + '-proxy')
        with self._locked(uid):
            if self._path(uid).exists():
                self._read(h)
            else:
                self._write(h, {'schema': 1, 'metadata_prefix': METADATA_PREFIX,
                            'identity': {'uid': uid, 'job_id': h.job_id,
                            'attempt': 1, 'token': 1}, 'audit': str(self.authority / (uid + '-audit')),
                            'project_id': None, 'phase': 'parse', 'sandbox_only': True,
                            'runtime': self.runtime_pins(), 'revoked': False,
                            'launch_not_started': True, 'resources': []})
        return h

    def _metadata(self, h, name, role):
        scope_key, role_key, intent_key, _ = metadata_keys(self._read(h))
        return {'name': name, 'namespace': self.settings.namespace,
                'labels': {scope_key: h.uid, role_key: role,
                           'app': {'worker': 'ppt-hermes-worker', 'proxy': 'ppt-model-proxy',
                                   'sandbox': 'ppt-sandbox'}.get(role, 'ppt-execution')},
                'annotations': {intent_key: str(uuid.uuid4())}}

    def create_resource(self, h, resource, body, *, sandbox=False):
        """Persist intent before POST. A lost response can only be reconciled by exact intent."""
        with self._locked(h.uid):
            record = self._read(h)
            _, role_key, intent_key, _ = metadata_keys(record)
            require(record['runtime'] == self.runtime_pins(), 'execution_runtime_mismatch')
            require(not record['revoked'] or (sandbox and body['metadata']['labels'][role_key] == 'sandbox'
                    and resource in ('pods', 'networkpolicies')
                    and all(e.get('stop_receipt') for e in record['resources'] if e['resource'] == 'pods'
                            and e['role'] != 'sandbox')), 'execution_revoked')
            name = body['metadata']['name']
            require(not any(r['resource'] == resource and r['name'] == name for r in record['resources']), 'execution_already_exists')
            entry = {'resource': resource, 'name': name, 'intent': body['metadata']['annotations'][intent_key],
                     'role': body['metadata']['labels'][role_key], 'api_uid': None, 'submitted': True,
                     'finalized': False, 'deleted': False}
            if resource == 'pods':
                entry['execution_image'] = record['runtime']['execution_image']
                record['launch_not_started'] = False
            record['resources'].append(entry)
            self._write(h, record)
            result = self.client.request('POST', resource, body=body)
            self._bind(h, entry, result)
            self._write(h, record)
            return result

    def _bind(self, h, entry, obj):
        scope_key, role_key, intent_key, _ = metadata_keys(self._read(h))
        meta = obj.get('metadata', {})
        require(meta.get('name') == entry['name'] and meta.get('namespace') == self.settings.namespace
                and meta.get('labels', {}).get(scope_key) == h.uid
                and meta.get('labels', {}).get(role_key) == entry['role']
                and meta.get('annotations', {}).get(intent_key) == entry['intent']
                and isinstance(meta.get('uid'), str) and bool(meta['uid']), 'kubernetes_resource_identity_mismatch')
        require(entry['api_uid'] in (None, meta['uid']), 'kubernetes_resource_uid_mismatch')
        if entry['resource'] == 'pods':
            spec = obj.get('spec', {})
            containers = spec.get('containers', [])
            require(len(containers) == 1 and containers[0].get('name') == entry['role']
                    and containers[0].get('image') == entry['execution_image']
                    and not spec.get('initContainers') and not spec.get('ephemeralContainers'),
                    'kubernetes_runtime_image_mismatch')
            image_env = [row for row in containers[0].get('env', []) if row.get('name') == 'PPT_RUNTIME_WORKER_IMAGE']
            require(image_env == [{'name': 'PPT_RUNTIME_WORKER_IMAGE', 'value': entry['execution_image']}],
                    'kubernetes_runtime_image_mismatch')
        entry['api_uid'] = meta['uid']

    def _get(self, h, entry):
        try:
            obj = self.client.request('GET', entry['resource'], entry['name'])
        except KubeError as exc:
            if exc.status == 404:
                return None
            raise
        self._bind(h, entry, obj)
        return obj

    def _pods(self, h, record):
        scope_key, _, _, _ = metadata_keys(record)
        result = self.client.request('GET', 'pods', query={'labelSelector': scope_key + '=' + h.uid})
        require(isinstance(result.get('items'), list) and not result.get('metadata', {}).get('continue'), 'incomplete_execution_inventory')
        known = {e['name']: e for e in record['resources'] if e['resource'] == 'pods'}
        require(all(o.get('metadata', {}).get('name') in known for o in result['items']), 'untracked_execution_writer')
        for obj in result['items']:
            self._bind(h, known[obj['metadata']['name']], obj)
        return {obj['metadata']['name']: obj for obj in result['items']}

    def subpath(self, path):
        path, root = Path(path).absolute(), self.config.data_root.absolute()
        require(path == root or root in path.parents, 'invalid_kubernetes_mount')
        for part in (path, *path.parents):
            require(not part.is_symlink() and not getattr(part, 'is_junction', lambda: False)(), 'linked_kubernetes_mount')
            if part == root:
                break
        require(path.is_dir(), 'invalid_kubernetes_mount')
        suffix = path.relative_to(root).as_posix()
        return self.settings.pvc_data_prefix + (('/' + relative(suffix)) if suffix != '.' else '')

    def pod(self, h, name, role, module, mounts, *, env=(), deadline=1800, args=()):
        """Only image-owned entrypoints and per-task PVC subpaths enter a Pod."""
        require(module in ('ppt_service.worker.execute', 'ppt_service.worker.model_proxy',
                           'ppt_service.control.parsing', 'ppt_service.control.publisher',
                           'ppt_service.control.hermes_delivery',
                           'ppt_service.control.kubernetes_preflight'), 'unapproved_kubernetes_entrypoint')
        require(not any(row.get('name') == 'PPT_RUNTIME_WORKER_IMAGE' for row in env),
                'reserved_kubernetes_environment')
        env = [{'name': 'PPT_RUNTIME_WORKER_IMAGE', 'value': self.settings.worker_image}, *env]
        metadata = self._metadata(h, name, role)
        metadata['finalizers'] = [metadata_keys(self._read(h))[3]]
        volumes = [{'name': 'data', 'persistentVolumeClaim': {'claimName': self.settings.pvc_name}},
                   {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '256Mi'}},
                   {'name': 'shm', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Mi'}}]
        volume_mounts = [{'name': 'data', 'mountPath': target, 'subPath': self.subpath(source), 'readOnly': readonly}
                         for source, target, readonly in mounts]
        volume_mounts += [{'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'shm', 'mountPath': '/dev/shm'}]
        container = {'name': role, 'image': self.settings.worker_image, 'imagePullPolicy': 'IfNotPresent',
                     'command': ['/opt/hermes/.venv/bin/python'], 'args': ['-m', module, *args],
                     'workingDir': '/work/project' if role == 'worker' else '/',
                     'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                         'capabilities': {'drop': ['ALL']}, 'privileged': False},
                     'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                                   'limits': {'cpu': '2' if role == 'worker' else '1',
                                              'memory': '4Gi' if role == 'worker' else '1Gi',
                                              'ephemeral-storage': '512Mi'}},
                     'env': list(env), 'volumeMounts': volume_mounts}
        return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata,
                'spec': {'serviceAccountName': self.settings.worker_service_account,
                         'automountServiceAccountToken': False, 'enableServiceLinks': False,
                         'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 30,
                         'activeDeadlineSeconds': deadline, 'nodeSelector': {'kubernetes.io/arch': 'arm64'},
                         'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
                         'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10000,
                                             'seccompProfile': {'type': 'RuntimeDefault'}},
                         'containers': [container], 'volumes': volumes}}

    def policy(self, h, name, role):
        scope_key, role_key, _, _ = metadata_keys(self._read(h))
        selector = {scope_key: h.uid, role_key: role}
        rules = {'podSelector': {'matchLabels': selector}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}
        port = [{'protocol': 'TCP', 'port': 8080}]
        if role == 'worker':
            rules['egress'] = [{'to': [{'podSelector': {'matchLabels': {scope_key: h.uid, role_key: 'proxy'}}}], 'ports': port}]
        elif role == 'proxy':
            rules['ingress'] = [{'from': [{'podSelector': {'matchLabels': {scope_key: h.uid, role_key: 'worker'}}}], 'ports': port}]
            rules['egress'] = [{'to': [{'ipBlock': {'cidr': self.config.approved_upstream_ip + '/32'}}],
                                'ports': [{'protocol': 'TCP', 'port': 443}]}]
        return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
                'metadata': self._metadata(h, name, role), 'spec': rules}

    def create(self, a, layout):
        require(self.ready, 'execution_disabled')
        h = self.reconcile(a)
        record = self._read(h)
        expected = self.config.data_root.absolute() / f'projects/{a.project_id}/attempts/{a.job_id}/{a.attempt}-{a.token}'
        require(layout.root == expected and all(getattr(layout, x) == expected / x
                for x in ('inputs', 'task', 'base', 'home', 'project', 'result', 'audit')), 'attempt_layout_mismatch')
        require(not any(e['role'] in ('worker', 'proxy') for e in record['resources']), 'execution_already_exists')
        key = secrets.token_urlsafe(48)
        key_name = h.worker_name.removesuffix('-worker') + '-key'
        credential = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': self._metadata(h, key_name, 'credential'),
                      'type': 'Opaque', 'immutable': True, 'data': {'client-key': base64.b64encode(key.encode()).decode()}}
        self.create_resource(h, 'secrets', credential)
        for role in ('worker', 'proxy'):
            self.create_resource(h, 'networkpolicies', self.policy(h, h.worker_name + '-' + role + '-net', role))
        ledger = layout.audit / 'proxy-ledger'
        ledger.mkdir(mode=0o700, exist_ok=False)
        if os.name == 'posix':
            os.chown(ledger, 10000, 10000)
        env = lambda name, value: {'name': name, 'value': value}
        secret = lambda name, secret_name, field: {'name': name, 'valueFrom': {'secretKeyRef': {'name': secret_name, 'key': field}}}
        proxy_env = [secret('PPT_PROXY_CLIENT_KEY', key_name, 'client-key'),
                     secret('PPT_UPSTREAM_API_KEY', self.settings.provider_secret, self.settings.provider_secret_key),
                     env('PPT_MODEL', self.config.model), env('PPT_UPSTREAM_BASE_URL', 'https://custom-llm-proxy.panghuer.top/v1'),
                     env('PPT_PROXY_USAGE_PATH', '/audit/usage.jsonl'), env('PPT_USAGE_JOB_ID', a.job_id),
                     env('PPT_USAGE_ATTEMPT', str(a.attempt)), env('PPT_USAGE_PHASE', a.phase)]
        proxy = self.pod(h, h.proxy_name, 'proxy', 'ppt_service.worker.model_proxy', [(ledger, '/audit', False)], env=proxy_env, deadline=2100)
        proxy['spec']['hostAliases'] = [{'ip': self.config.approved_upstream_ip, 'hostnames': ['custom-llm-proxy.panghuer.top']}]
        proxy['spec']['containers'][0]['readinessProbe'] = {'tcpSocket': {'port': 8080}, 'periodSeconds': 2}
        self.create_resource(h, 'pods', proxy)
        proxy_ip = self._wait_proxy(h)
        worker_env = [secret('PPT_MODEL_API_KEY', key_name, 'client-key'), env('PPT_MODEL', self.config.model),
                      env('PPT_MODEL_PROXY_ORIGIN', 'http://model-proxy:8080'), env('PPT_MODEL_BASE_URL', 'http://model-proxy:8080/v1')]
        worker = self.pod(h, h.worker_name, 'worker', 'ppt_service.worker.execute',
                          [(getattr(layout, part), '/work/' + part, part in ('inputs', 'task', 'base'))
                           for part in ('inputs', 'task', 'base', 'home', 'project', 'result')],
                          env=worker_env, deadline=720 if a.phase.startswith('plan') else 1920,
                          args=['--task', '/work/task/task.json', '--deadline', '600' if a.phase.startswith('plan') else '1800'])
        worker['spec']['hostAliases'] = [{'ip': proxy_ip, 'hostnames': ['model-proxy']}]
        if self.before_worker is not None:
            # Production supplies a fresh DB fence/lease/cancellation check after
            # potentially slow image startup and before admitting model work.
            self.before_worker(a)
        self.create_resource(h, 'pods', worker)
        return h

    def _wait_proxy(self, h):
        deadline = self.monotonic() + self.settings.startup_timeout
        while self.monotonic() < deadline:
            record = self._read(h)
            require(not record['revoked'], 'execution_revoked')
            entry = next(e for e in record['resources'] if e['resource'] == 'pods' and e['role'] == 'proxy')
            obj = self._get(h, entry)
            require(obj is not None, 'execution_writer_missing')
            status = obj.get('status', {})
            if status.get('phase') in ('Failed', 'Succeeded') or obj['metadata'].get('deletionTimestamp'):
                raise ControlError('model_proxy_unavailable')
            statuses = status.get('containerStatuses', [])
            if len(statuses) == 1 and statuses[0].get('ready') and statuses[0].get('state', {}).get('running'):
                address = status.get('podIP')
                try:
                    require(ipaddress.ip_address(address).version == 4, 'invalid_proxy_address')
                    return address
                except ValueError:
                    raise ControlError('invalid_proxy_address') from None
            self.sleep(self.settings.poll_seconds)
        raise ControlError('model_proxy_startup_timeout')

    @staticmethod
    def terminated(obj):
        spec, status = obj.get('spec', {}), obj.get('status', {})
        if spec.get('ephemeralContainers') or status.get('ephemeralContainerStatuses'):
            return False
        for field, statuses_field in (('containers', 'containerStatuses'), ('initContainers', 'initContainerStatuses')):
            names = {c['name'] for c in spec.get(field, [])}
            rows = status.get(statuses_field, [])
            if len(rows) != len(names) or {r.get('name') for r in rows} != names:
                return False
            for row in rows:
                end = row.get('state', {}).get('terminated', {})
                if (type(end.get('exitCode')) is not int or not end.get('finishedAt')
                        or not end.get('containerID') or end.get('reason') in ('ContainerStatusUnknown', 'NodeLost')):
                    return False
        return bool(spec.get('containers'))

    def observe(self, h):
        with self._locked(h.uid):
            record = self._read(h)
            pods = self._pods(h, record)
            entries = [e for e in record['resources'] if e['resource'] == 'pods' and e['role'] == 'worker']
            require(len(entries) == 1, 'execution_writer_missing')
            obj = pods.get(entries[0]['name'])
            require(obj is not None, 'execution_writer_missing')
            self._write(h, record)
            if not self.terminated(obj):
                return Observation(True)
            return Observation(False, obj['status']['containerStatuses'][0]['state']['terminated']['exitCode'])

    def wait_pod(self, h, name, timeout):
        deadline = self.monotonic() + timeout
        while self.monotonic() < deadline:
            with self._locked(h.uid):
                record = self._read(h)
                entry = next(e for e in record['resources'] if e['resource'] == 'pods' and e['name'] == name)
                obj = self._get(h, entry)
                require(obj is not None and metadata_keys(record)[3] in obj['metadata'].get('finalizers', []), 'execution_writer_missing')
                self._write(h, record)
                if self.terminated(obj):
                    return obj['status']['containerStatuses'][0]['state']['terminated']['exitCode']
            self.sleep(self.settings.poll_seconds)
        raise ControlError('sandbox_timeout')

    def finish_sandbox(self, h, name):
        """Stop and retain proof for one sandbox without revoking the model attempt."""
        deadline = self.monotonic() + self.settings.stop_timeout
        with self._locked(h.uid):
            record = self._read(h)
            entry = next(e for e in record['resources'] if e['resource'] == 'pods' and e['name'] == name)
            require(entry['role'] == 'sandbox', 'invalid_sandbox_identity')
            obj = self._get(h, entry)
            require(obj is not None and metadata_keys(record)[3] in obj['metadata'].get('finalizers', []), 'execution_writer_missing')
            self._write(h, record)
            self.client.request('DELETE', 'pods', name,
                                {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': 30,
                                 'preconditions': {'uid': entry['api_uid']}})
        self.wait_pod(h, name, max(0, deadline - self.monotonic()))
        with self._locked(h.uid):
            record = self._read(h)
            entry = next(e for e in record['resources'] if e['resource'] == 'pods' and e['name'] == name)
            obj = self._get(h, entry)
            require(obj is not None and self.terminated(obj), 'writers_not_stopped')
            entry['stop_receipt'] = {'api_uid': entry['api_uid'], 'resource_version': obj['metadata'].get('resourceVersion'),
                                     'status': obj['status'], 'terminated': True}
            self._write(h, record)
            self._remove_resource(h, record, entry, obj)
        # DELETE acceptance does not mean the API has removed the Pod yet.
        # Both views must settle before a caller can keep a successful result;
        # prove_stopped deliberately rejects deleted entries still in LIST.
        while self.monotonic() < deadline:
            with self._locked(h.uid):
                record = self._read(h)
                entry = next(e for e in record['resources'] if e['resource'] == 'pods' and e['name'] == name)
                obj = self._get(h, entry)
                listed = self._pods(h, record).get(name)
                require(all(self.terminated(candidate) for candidate in (obj, listed) if candidate is not None),
                        'writers_not_stopped')
                self._write(h, record)
                if obj is None and listed is None:
                    return
            self.sleep(self.settings.poll_seconds)
        raise ControlError('writers_not_stopped')

    def stop(self, h):
        with self._locked(h.uid):
            record = self._read(h)
            record['revoked'] = True
            self._write(h, record)
            pods = self._pods(h, record)
            self._write(h, record)
            for entry in record['resources']:
                if entry['resource'] != 'pods' or entry['deleted']:
                    continue
                obj = pods.get(entry['name'])
                require(obj is not None, 'execution_writer_missing')
                require(metadata_keys(record)[3] in obj['metadata'].get('finalizers', []), 'execution_finalizer_missing')
                self.client.request('DELETE', 'pods', entry['name'],
                                    {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': 30,
                                     'preconditions': {'uid': entry['api_uid']}})
        deadline = self.monotonic() + self.settings.stop_timeout
        while self.monotonic() < deadline:
            if self.prove_stopped(h).all_writers_stopped:
                return
            self.sleep(self.settings.poll_seconds)
        raise ControlError('writers_not_stopped')

    def prove_stopped(self, h):
        try:
            with self._locked(h.uid):
                record = self._read(h)
                pods = self._pods(h, record)
                ids = []
                for entry in record['resources']:
                    if entry['resource'] != 'pods':
                        continue
                    obj = pods.get(entry['name'])
                    if entry['deleted'] or (obj is None and entry.get('finalizer_release_requested')):
                        require(entry.get('stop_receipt') and entry['stop_receipt']['api_uid'] == entry['api_uid']
                                and obj is None, 'invalid_stop_receipt')
                        entry['deleted'] = True
                    else:
                        require(obj is not None and (metadata_keys(record)[3] in obj['metadata'].get('finalizers', [])
                                or (entry.get('finalizer_release_requested') and entry.get('stop_receipt')))
                                and self.terminated(obj), 'writers_not_stopped')
                        entry['stop_receipt'] = {'api_uid': entry['api_uid'],
                                                 'resource_version': obj['metadata'].get('resourceVersion'),
                                                 'status': obj.get('status'), 'terminated': True}
                    ids.append(entry['api_uid'])
                # An unresolved POST for a non-Pod cannot itself write task files.
                # Empty prelaunch is valid only with the original explicit no-intent journal.
                if not ids:
                    require(record['launch_not_started'] and not pods
                            and all(e['resource'] != 'pods' for e in record['resources']), 'writers_not_stopped')
                self._write(h, record)
                return StopProof(h.uid, True, tuple(ids))
        except (ControlError, OSError, ValueError, KeyError, TypeError):
            return StopProof(h.uid, False)

    def cleanup(self, h):
        require(self.prove_stopped(h).all_writers_stopped, 'writers_not_stopped')
        with self._locked(h.uid):
            record = self._read(h)
            require(record['revoked'], 'execution_not_revoked')
            for entry in record['resources']:
                if entry['deleted']:
                    continue
                obj = self._get(h, entry)
                if obj is None:
                    require(entry['resource'] != 'pods' or (entry.get('finalizer_release_requested')
                            and entry.get('stop_receipt')), 'execution_writer_missing')
                    entry['deleted'] = True
                    self._write(h, record)
                    continue
                self._remove_resource(h, record, entry, obj)

    def _remove_resource(self, h, record, entry, obj):
        if entry['resource'] == 'pods':
            require(self.terminated(obj) and entry.get('stop_receipt'), 'writers_not_stopped')
            meta = obj['metadata']
            finalizers = meta.get('finalizers', [])
            finalizer = metadata_keys(record)[3]
            require(finalizer in finalizers or entry.get('finalizer_release_requested'), 'execution_finalizer_missing')
            entry['finalizer_release_requested'] = True
            self._write(h, record)
            if finalizer in finalizers:
                self.client.request('PATCH', 'pods', entry['name'],
                    [{'op': 'test', 'path': '/metadata/uid', 'value': entry['api_uid']},
                     {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
                     {'op': 'replace', 'path': '/metadata/finalizers', 'value': [f for f in finalizers if f != finalizer]}])
            entry['finalized'] = True
            self._write(h, record)
        try:
            self.client.request('DELETE', entry['resource'], entry['name'],
                                {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                                 'preconditions': {'uid': entry['api_uid']}})
        except KubeError as exc:
            if exc.status != 404 or entry['resource'] != 'pods' or not entry.get('stop_receipt'):
                raise
        entry['deleted'] = True
        self._write(h, record)

    def remove_sandbox_policy(self, h, name):
        with self._locked(h.uid):
            record = self._read(h)
            entry = next(e for e in record['resources'] if e['resource'] == 'networkpolicies' and e['name'] == name)
            require(entry['role'] == 'sandbox', 'invalid_sandbox_identity')
            if not entry['deleted']:
                obj = self._get(h, entry)
                if obj is not None:
                    self._remove_resource(h, record, entry, obj)


class KubernetesParseScopes:
    """MaterialParser scope adapter; missing recovery authority is fail-closed."""
    def __init__(self, backend):
        self.backend = backend

    def ensure_scope(self, uid):
        return self.backend.register_sandbox_scope(uid)

    def prove_stopped(self, uid):
        try:
            h = self.backend.handle_for_scope(uid)
            return self.backend.prove_stopped(h).all_writers_stopped
        except (ControlError, OSError, ValueError, KeyError):
            return False

    def stop_and_prove(self, uid):
        try:
            h = self.backend.handle_for_scope(uid)
            self.backend.stop(h)
            if not self.backend.prove_stopped(h).all_writers_stopped:
                return False
            self.backend.cleanup(h)
            return self.backend.prove_stopped(h).all_writers_stopped
        except (ControlError, OSError, ValueError, KeyError):
            return False

    scope_stop_and_prove = stop_and_prove
