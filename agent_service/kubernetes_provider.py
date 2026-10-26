"""One durable Pod per attempt; never reconstruct a missing paid execution.

The Pod finalizer retains terminated container statuses until the controller
has persisted stop observation. A 404, deletion timestamp or native terminal
flag alone cannot release a slot. Workers and native processes never receive
this controller's Kubernetes credentials or master receipt key.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import ipaddress
import re
import time
from uuid import UUID

from .contracts import (ProfilePolicy, ServiceError, WorkerResult,
                        canonical_bytes, digest, sign_receipt, verify_receipt)
from .transport import RemoteWorkerAdapter, WorkerEndpoint

FINALIZER = 'agent-service.panghuer.top/observed-stop'
PREFIX = 'agent-service.panghuer.top/'


@dataclass(frozen=True)
class KubernetesPool:
    pool_id: str
    policy: ProfilePolicy
    namespace: str
    image: str
    host_config_map: str
    pod_cidrs: tuple[str, ...]
    enabled: bool = False
    port: int = 8090
    uid_start: int | None = None
    uid_count: int = 65536

    def __post_init__(self):
        label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
        if (not re.fullmatch(label, self.pool_id) or not re.fullmatch(label, self.namespace)
                or not re.fullmatch(label + r'(?:\.' + label + r')*', self.host_config_map)
                or len(self.host_config_map) > 253 or type(self.enabled) is not bool
                or type(self.port) is not int or self.port != 8090
                or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}', self.image)
                or self.image.rsplit('@', 1)[1] != self.policy.image_digest
                or not 1 <= len(self.pod_cidrs) <= 10):
            raise ValueError('Invalid fixed Kubernetes pool')
        if (type(self.uid_count) is not int or not 1 <= self.uid_count <= 1000000
                or self.uid_start is not None and (type(self.uid_start) is not int
                    or not 65536 <= self.uid_start <= 2147483647
                    or self.uid_start + self.uid_count - 1 > 2147483647)
                or self.enabled and self.uid_start is None):
            raise ValueError('An explicitly reserved runtime UID range is required')
        for cidr in self.pod_cidrs:
            net = ipaddress.ip_network(cidr, strict=True)
            if (not net.is_private or net.prefixlen == 0 or net.is_loopback or net.is_link_local
                    or net.is_multicast or net.is_unspecified):
                raise ValueError('A private cluster Pod network is required')


class KubernetesWorkerAdapter:
    provisioning = 'kubernetes_per_attempt'

    def __init__(self, store, pool: KubernetesPool, kube, master_token: str,
                 master_receipt_key: bytes, *, client_factory=None, clock=time.time):
        if (not 32 <= len(master_token) <= 512 or len(master_receipt_key) < 32
                or any(not 33 <= ord(c) <= 126 for c in master_token)):
            raise ValueError('Dedicated pool credentials required')
        self.store, self.pool, self.kube = store, pool, kube
        self._token_key, self._receipt_key = master_token.encode(), master_receipt_key
        self._factory, self.clock = client_factory or RemoteWorkerAdapter, clock

    def close(self):
        self.kube.close()

    def receipt_key_for(self, envelope):
        return hmac.new(self._receipt_key,
            ('agent-receipt-v1:' + envelope.request_sha256).encode(), hashlib.sha256).digest()

    def _credentials(self, envelope):
        token = hmac.new(self._token_key,
            ('agent-token-v1:' + envelope.request_sha256).encode(), hashlib.sha256).hexdigest()
        return {'worker_token': token, 'receipt_key_hex': self.receipt_key_for(envelope).hex()}

    def _path(self, kind, name=''):
        return f'/api/v1/namespaces/{self.pool.namespace}/{kind}' + ('/' + name if name else '')

    def _get(self, kind, name):
        try:
            return self.kube.request('GET', self._path(kind, name))
        except ServiceError as exc:
            if exc.status == 404:
                return None
            raise

    def ready(self, policy=None):
        if not self.pool.enabled or not self.pool.policy.enabled:
            return False
        if policy is not None and policy.pins() != self.pool.policy.pins():
            return False
        try:
            if not self.store.worker_uid_available(self.pool.uid_start, self.pool.uid_count):
                return False
            self._ensure_host_config()
            # Admission capacity, never a claim that a cold native engine is
            # already running. Native readiness is checked before its only PUT.
            value = self.kube.request('GET', self._path('pods') + '?limit=1&labelSelector=app%3Dagent-capsule')
            return isinstance(value.get('items'), list)
        except ServiceError:
            return False

    def _ensure_host_config(self):
        from .worker_entry import HostConfig
        import json
        value = self._get('configmaps', self.pool.host_config_map)
        try:
            raw = value['data']['config.json']
            if value.get('immutable') is not True or type(raw) is not str or len(raw.encode()) > 65536:
                raise ValueError()
            def pairs(items):
                result = {}
                for key, item in items:
                    if key in result:
                        raise ValueError()
                    result[key] = item
                return result
            config = HostConfig.model_validate(json.loads(raw, object_pairs_hook=pairs))
            p = self.pool.policy
            if (config.engine != p.engine or config.profile.name != p.name
                    or config.profile.revision != p.revision or config.profile.runtime_revision != p.runtime_revision
                    or config.profile.image_digest != p.image_digest or config.profile.enabled != p.enabled
                    or config.profile.max_timeout_seconds != p.max_timeout_seconds):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ServiceError('worker_host_config_invalid', 503) from None

    def _manifest(self, envelope):
        if (any(getattr(envelope, k) != v for k, v in self.pool.policy.pins().items())
                or envelope.inputs):
            raise ServiceError('execution_pins_mismatch', 422)
        attempt = str(UUID(envelope.attempt_id))
        if self.pool.uid_start is None or envelope.fence > self.pool.uid_count:
            raise ServiceError('worker_uid_range_exhausted', 503)
        runtime_uid = self.pool.uid_start + envelope.fence - 1
        name = 'agent-' + attempt
        credentials = canonical_bytes(self._credentials(envelope))
        annotations = {PREFIX + 'request': envelope.request_sha256,
                       PREFIX + 'credentials': hashlib.sha256(credentials).hexdigest()}
        labels = {'app': 'agent-capsule', PREFIX + 'pool': self.pool.pool_id,
                  PREFIX + 'attempt': attempt}
        env = [{'name': k, 'value': v} for k, v in {
            'AGENT_ATTEMPT_ID': attempt, 'AGENT_REQUEST_SHA256': envelope.request_sha256,
            'AGENT_RUNTIME_IMAGE_DIGEST': envelope.image_digest, 'AGENT_ENGINE': envelope.engine,
            'AGENT_PROFILE_NAME': envelope.profile, 'AGENT_PROFILE_REVISION': envelope.profile_revision,
            'AGENT_RUNTIME_REVISION': envelope.runtime_revision,
            'AGENT_RUNTIME_UID': str(runtime_uid)}.items()]
        env.append({'name': 'AGENT_POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}})
        pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
            'name': name, 'namespace': self.pool.namespace, 'labels': labels,
            'annotations': annotations, 'finalizers': [FINALIZER]}, 'spec': {
                'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                'enableServiceLinks': False,
                'hostPID': False, 'hostIPC': False, 'hostNetwork': False,
                'shareProcessNamespace': False, 'terminationGracePeriodSeconds': 30,
                'activeDeadlineSeconds': self.pool.policy.max_timeout_seconds + 90,
                'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [{'name': 'worker', 'image': self.pool.image, 'imagePullPolicy': 'IfNotPresent',
                    'command': ['python', '-m', 'agent_service.worker_entry'],
                    'args': ['--config', '/etc/agent-worker/config.json',
                             '--secrets', '/run/agent-secrets/worker.json'],
                    'ports': [{'name': 'worker', 'containerPort': self.pool.port}], 'env': env,
                    'securityContext': {'runAsUser': 0, 'runAsGroup': 0, 'readOnlyRootFilesystem': True,
                        'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL'],
                            'add': ['CHOWN', 'DAC_OVERRIDE', 'KILL', 'SETUID', 'SETGID']}},
                    'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                        'limits': {'cpu': '1', 'memory': '768Mi', 'ephemeral-storage': '1Gi'}},
                    'volumeMounts': [{'name': 'config', 'mountPath': '/etc/agent-worker', 'readOnly': True},
                        {'name': 'credentials', 'mountPath': '/run/agent-secrets', 'readOnly': True},
                        {'name': 'control', 'mountPath': '/run/agent-control'},
                        {'name': 'work', 'mountPath': '/work'}, {'name': 'tmp', 'mountPath': '/tmp'}]}],
                'volumes': [{'name': 'config', 'configMap': {'name': self.pool.host_config_map,
                                'defaultMode': 0o444}},
                    {'name': 'credentials', 'secret': {'secretName': name + '-auth', 'defaultMode': 0o400}},
                    {'name': 'control', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '32Mi'}},
                    {'name': 'work', 'emptyDir': {'sizeLimit': '512Mi'}},
                    {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Mi'}}]}}
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque', 'immutable': True,
            'metadata': {'name': name + '-auth', 'namespace': self.pool.namespace,
                         'labels': labels, 'annotations': annotations},
            'data': {'worker.json': base64.b64encode(credentials).decode()}}
        return pod, secret

    def _allocation(self, envelope, *, reserve=False):
        if self.store.attempt_provisioning(envelope) != self.provisioning:
            raise ServiceError('worker_provisioning_mismatch', 409)
        pod, secret = self._manifest(envelope)
        intent = digest({'pod': pod, 'secret': secret})
        try:
            value = self.store.get_worker_allocation(envelope.attempt_id)
        except ServiceError as exc:
            if not reserve or exc.status != 404:
                raise
            value = self.store.reserve_worker(envelope, self.pool.pool_id, self.pool.namespace,
                                             pod['metadata']['name'], intent,
                                             agent_uid=self.pool.uid_start + envelope.fence - 1)
        if (value['request_sha256'] != envelope.request_sha256 or value['pool_id'] != self.pool.pool_id
                or value['namespace'] != self.pool.namespace or value['intent_sha256'] != intent
                or value['pod_name'] != pod['metadata']['name']
                or value.get('agent_uid') != self.pool.uid_start + envelope.fence - 1):
            raise ServiceError('worker_allocation_conflict', 409)
        return value, pod, secret

    def _check_resource(self, actual, expected, *, pod=False):
        meta, wanted = actual.get('metadata', {}), expected['metadata']
        if (meta.get('name') != wanted['name'] or meta.get('namespace') != wanted['namespace']
                or not isinstance(meta.get('uid'), str) or not 1 <= len(meta['uid']) <= 100
                or any(meta.get('labels', {}).get(k) != v for k, v in wanted['labels'].items())
                or any(meta.get('annotations', {}).get(k) != v for k, v in wanted['annotations'].items())):
            raise ServiceError('kubernetes_resource_mismatch', 503)
        if pod:
            from .kubernetes_manifest import validate_pod
            validate_pod(actual, expected)
            spec = actual.get('spec', {})
            containers = spec.get('containers', [])
            if (len(containers) != 1 or containers[0].get('name') != 'worker'
                    or containers[0].get('image') != self.pool.image
                    or spec.get('restartPolicy') != 'Never'
                    or any(spec.get(k) is True for k in ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace'))
                    or spec.get('automountServiceAccountToken') is not False
                    or spec.get('initContainers') or spec.get('ephemeralContainers')):
                raise ServiceError('kubernetes_resource_mismatch', 503)
        elif actual.get('data') != expected['data'] or actual.get('immutable') is not True:
            raise ServiceError('kubernetes_resource_mismatch', 503)

    def _ensure_secret(self, expected):
        actual = self._get('secrets', expected['metadata']['name'])
        if actual is None:
            try:
                actual = self.kube.request('POST', self._path('secrets'), expected)
            except ServiceError:
                actual = self._get('secrets', expected['metadata']['name'])
                if actual is None:
                    raise ServiceError('worker_credentials_uncertain', 503) from None
        self._check_resource(actual, expected)

    def _status(self, envelope, state):
        return {'attempt_id': envelope.attempt_id, 'request_sha256': envelope.request_sha256, 'state': state}

    def _remote(self, envelope, pod):
        address = ipaddress.ip_address(pod.get('status', {}).get('podIP', ''))
        if (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast
                or not any(address in ipaddress.ip_network(cidr) for cidr in self.pool.pod_cidrs)):
            raise ServiceError('worker_address_invalid', 503)
        host = '[' + str(address) + ']' if address.version == 6 else str(address)
        return self._factory(WorkerEndpoint(f'http://{host}:{self.pool.port}',
            self._credentials(envelope)['worker_token'], self.receipt_key_for(envelope), envelope.engine))

    def _all_terminated(self, pod):
        spec, status = pod.get('spec', {}), pod.get('status', {})
        if status.get('reason') in {'NodeLost', 'Unknown'}:
            return False
        # Pod phase, Ready=false or an absent status do not prove stopped writers.
        for names, statuses in (('containers', 'containerStatuses'), ('initContainers', 'initContainerStatuses'),
                                ('ephemeralContainers', 'ephemeralContainerStatuses')):
            expected = [v.get('name') for v in spec.get(names, [])]
            actual = status.get(statuses, [])
            if (len(actual) != len(expected) or len({v.get('name') for v in actual}) != len(actual)
                    or set(expected) != {v.get('name') for v in actual}
                    or any(type(v.get('state', {}).get('terminated')) is not dict for v in actual)):
                return False
            for value in actual:
                term = value['state']['terminated']
                if (type(term.get('exitCode')) is not int
                        or type(term.get('containerID')) is not str or not 1 <= len(term['containerID']) <= 300
                        or any(ord(c) < 33 or ord(c) > 126 for c in term['containerID'])
                        or term.get('reason') in {'ContainerStatusUnknown', 'NodeLost', 'Unknown'}
                        or type(value.get('restartCount', 0)) is not int or value.get('restartCount', 0) != 0):
                    return False
                try:
                    finished = datetime.fromisoformat(term['finishedAt'].replace('Z', '+00:00'))
                    if finished.tzinfo is None or not 0 < finished.timestamp() <= self.clock() + 5:
                        return False
                except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                    return False
        return bool(spec.get('containers'))

    def _pod(self, allocation, expected):
        pod = self._get('pods', allocation['pod_name'])
        if pod is None:
            if allocation['stop_observed_at'] is not None:
                return None
            raise ServiceError('worker_resource_missing', 503)
        self._check_resource(pod, expected, pod=True)
        if allocation['pod_uid'] and pod['metadata']['uid'] != allocation['pod_uid']:
            raise ServiceError('worker_resource_replaced', 503)
        if allocation['stop_observed_at'] is None and FINALIZER not in pod['metadata'].get('finalizers', []):
            raise ServiceError('worker_finalizer_missing', 503)
        return pod

    def _advance(self, envelope, *, reserve=False):
        allocation, expected, secret = self._allocation(envelope, reserve=reserve)
        if allocation['retired']:
            if allocation['stop_observed_at'] is not None:
                return self._status(envelope, allocation['candidate_result']['state'])
            raise ServiceError('worker_resource_retired', 409)
        pod = self._get('pods', allocation['pod_name'])
        if pod is None and not allocation['create_claimed']:
            self._ensure_host_config()
            self._ensure_secret(secret)
            if self.store.claim_worker_create(envelope.attempt_id, envelope.request_sha256):
                try:
                    pod = self.kube.request('POST', self._path('pods'), expected)
                except ServiceError:
                    pod = self._get('pods', allocation['pod_name'])
            else:
                pod = self._get('pods', allocation['pod_name'])
        if pod is None:
            if allocation['stop_observed_at'] is not None:
                return self._status(envelope, allocation['candidate_result']['state'])
            raise ServiceError('worker_resource_missing', 503)
        self._check_resource(pod, expected, pod=True)
        if allocation['stop_observed_at'] is None and FINALIZER not in pod['metadata'].get('finalizers', []):
            raise ServiceError('worker_finalizer_missing', 503)
        allocation = self.store.bind_worker(envelope.attempt_id, envelope.request_sha256, pod['metadata']['uid'])
        if allocation['candidate_result'] is not None:
            return self._status(envelope, allocation['candidate_result']['state'])
        if self._all_terminated(pod):
            return self._status(envelope, 'failed')
        if pod['metadata'].get('deletionTimestamp'):
            return self._status(envelope, 'cancelling')
        if not pod.get('status', {}).get('podIP'):
            return self._status(envelope, 'preparing')
        remote = self._remote(envelope, pod)
        try:
            if not allocation['worker_start_claimed']:
                if not remote.ready(self.pool.policy):
                    return self._status(envelope, 'preparing')
                if self.store.claim_worker_start(envelope.attempt_id, envelope.request_sha256):
                    try:
                        return remote.start(envelope)
                    except ServiceError:
                        return remote.inspect(envelope)
            return remote.inspect(envelope)
        finally:
            remote.close()

    def start(self, envelope):
        if not self.pool.enabled:
            raise ServiceError('worker_pool_disabled', 503)
        return self._advance(envelope, reserve=True)

    def inspect(self, envelope):
        # Claim may commit just before a controller crash. Persisting the first
        # resource intent is safe while the lease is live: all physical creation
        # still requires its committed CAS. An existing claimed create is never
        # replayed, including when GET returns 404.
        return self._advance(envelope, reserve=True)

    def resolve_uncreated(self, envelope, code='worker_not_created'):
        # Only this provider's persisted, unclaimed creation proves prelaunch.
        # The transaction retires its plan before releasing the slot; a late
        # controller cannot subsequently claim creation. No receipt is invented.
        resolved = self.store.resolve_uncreated_attempt(envelope, code)
        if resolved is not None:
            try:
                allocation = self.store.get_worker_allocation(envelope.attempt_id)
                if allocation['retired'] and not allocation['create_claimed']:
                    _, expected = self._manifest(envelope)
                    actual = self._get('secrets', expected['metadata']['name'])
                    if actual is not None:
                        self._check_resource(actual, expected)
                        self.kube.request('DELETE', self._path('secrets', expected['metadata']['name']),
                            {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                             'preconditions': {'uid': actual['metadata']['uid']}})
            except ServiceError:
                # Credentials alone cannot launch a Pod. An orphan Secret after
                # a failed/late cleanup is an operator cleanup item, never a
                # reason to invent physical execution or reuse the burned UID.
                pass
        return resolved

    def renew(self, envelope, lease_expires_at):
        allocation, expected, _ = self._allocation(envelope)
        pod = self._pod(allocation, expected)
        if allocation['candidate_result'] is not None or not allocation['worker_start_claimed']:
            return self._status(envelope, 'preparing')
        remote = self._remote(envelope, pod)
        try:
            return remote.renew(envelope, lease_expires_at)
        finally:
            remote.close()

    def _delete_pod(self, allocation):
        try:
            self.kube.request('DELETE', self._path('pods', allocation['pod_name']),
                {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': 30,
                 'preconditions': {'uid': allocation['pod_uid']}})
        except ServiceError as exc:
            if exc.status != 404:
                raise

    def cancel(self, envelope):
        allocation, expected, _ = self._allocation(envelope)
        pod = self._pod(allocation, expected)
        if pod is not None:
            self._delete_pod(allocation)
        return self._status(envelope, 'cancelling')

    def events(self, envelope, after=0, limit=100):
        allocation, expected, _ = self._allocation(envelope)
        page = {'attempt_id': envelope.attempt_id, 'request_sha256': envelope.request_sha256,
                'items': [], 'next_cursor': after, 'has_more': False}
        if allocation['candidate_result'] is not None or allocation['stop_observed_at'] is not None:
            return page
        pod = self._pod(allocation, expected)
        if not allocation['worker_start_claimed'] or self._all_terminated(pod):
            return page
        remote = self._remote(envelope, pod)
        try:
            return remote.events(envelope, after, limit)
        finally:
            remote.close()

    def _cleanup(self, envelope, allocation, expected, secret):
        if allocation['retired']:
            return allocation
        pod = self._pod(allocation, expected)
        if pod is not None:
            if not self._all_terminated(pod):
                raise ServiceError('worker_stop_observation_changed', 503)
            self._delete_pod(allocation)
            pod = self._pod(allocation, expected)
        if pod is not None:
            finalizers = pod['metadata'].get('finalizers', [])
            if FINALIZER in finalizers:
                self.kube.request('PATCH', self._path('pods', allocation['pod_name']), [
                    {'op': 'test', 'path': '/metadata/uid', 'value': allocation['pod_uid']},
                    {'op': 'test', 'path': '/metadata/resourceVersion', 'value': pod['metadata']['resourceVersion']},
                    {'op': 'replace', 'path': '/metadata/finalizers',
                     'value': [v for v in finalizers if v != FINALIZER]}], content_type='application/json-patch+json')
        actual = self._get('secrets', secret['metadata']['name'])
        if actual is not None:
            self._check_resource(actual, secret)
            self.kube.request('DELETE', self._path('secrets', secret['metadata']['name']),
                {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': actual['metadata']['uid']}})
        return self.store.retire_worker(envelope.attempt_id, envelope.request_sha256, allocation['pod_uid'])

    def result(self, envelope):
        allocation, expected, secret = self._allocation(envelope)
        pod = None if allocation['retired'] else self._pod(allocation, expected)
        candidate = allocation['candidate_result']
        if candidate is None:
            if pod is not None and self._all_terminated(pod):
                # The outer runtime can prove stop even if the worker never
                # admitted or lost its result. Never invent usage or artifacts.
                result = WorkerResult(state='failed', error_code='capsule_stopped_without_result')
                receipt = sign_receipt(envelope, result, self.receipt_key_for(envelope),
                    (f'k8s:{self.pool.namespace}:{allocation["pod_uid"]}',), self.clock())
                result = result.model_copy(update={'stop_receipt': receipt})
            else:
                remote = self._remote(envelope, pod)
                try:
                    result = remote.result(envelope)
                finally:
                    remote.close()
                if not verify_receipt(envelope, result, self.receipt_key_for(envelope), self.clock()):
                    raise ServiceError('worker_stop_receipt_invalid', 503)
            allocation = self.store.stage_worker_result(envelope.attempt_id, envelope.request_sha256,
                                                        result.model_dump())
            candidate = allocation['candidate_result']
        stored = WorkerResult.model_validate(candidate)
        if not verify_receipt(envelope, stored, self.receipt_key_for(envelope), self.clock()):
            raise ServiceError('worker_stop_receipt_invalid', 503)
        if allocation['stop_observed_at'] is None:
            self._delete_pod(allocation)
            pod = self._pod(allocation, expected)
            if pod is None or not self._all_terminated(pod):
                raise ServiceError('worker_stop_pending', 409)
            allocation = self.store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256,
                                                          allocation['pod_uid'], float(self.clock()))
        allocation = self._cleanup(envelope, allocation, expected, secret)
        result = WorkerResult.model_validate(candidate)
        # The outer proof covers every writer in this exact Pod. Preserve the
        # full native receipt in the private candidate record, rather than
        # overflowing the bounded resource list by adding a 65th identifier.
        receipt = sign_receipt(envelope, result, self.receipt_key_for(envelope),
                               (f'k8s:{self.pool.namespace}:{allocation["pod_uid"]}',),
                               allocation['stop_observed_at'])
        return result.model_copy(update={'stop_receipt': receipt})

    def reconcile_quarantined(self, envelope):
        """Stop the original resource; never allocate or re-admit an engine.

        Only positive, persisted Pod stop observation can resolve quarantine.
        Recovery deliberately discards candidate artifacts at the Job boundary.
        """
        allocation, expected, _ = self._allocation(envelope)
        if not allocation['retired']:
            pod = self._pod(allocation, expected)
            if pod is not None and not self._all_terminated(pod):
                self._delete_pod(allocation)
                raise ServiceError('worker_stop_pending', 409)
        return self.result(envelope)
