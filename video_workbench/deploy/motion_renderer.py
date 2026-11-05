"""Pure resource generator for the independent internal motion executor.

This generates no Secret values and no public Ingress. Provision a new random
token Secret independently in the workbench and executor namespaces. Mount only
that token and the complete runtime fingerprint ConfigMap; never Agent/TTS keys.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import json
import re

NAME, PORT = 'video-motion-executor', 8001


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', value):
        raise ValueError('ordinary Kubernetes name required')
    return value


def _image(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]+:latest@sha256:[0-9a-f]{64}', value):
        raise ValueError('immutable latest image required')
    return value


@dataclass(frozen=True)
class Settings:
    image: str
    renderer_image: str
    release_id: str
    api_host: str  # literal ClusterIP or control-plane IP, never a broad CIDR
    api_port: int = 443
    namespace: str = 'video-motion-executor'
    renderer_namespace: str = 'video-motion-render'
    workbench_namespace: str = 'video-workbench'
    workbench_label: str = 'video-workbench'
    token_secret: str = 'video-motion-execution-token'
    runtime_configmap: str = 'video-motion-runtime'
    storage_class: str = ''


def render(settings):
    s = settings
    for value in (s.namespace, s.renderer_namespace, s.workbench_namespace, s.workbench_label,
                  s.token_secret, s.runtime_configmap):
        _name(value)
    if s.namespace == s.renderer_namespace:
        raise ValueError('executor must be outside deny-all renderer namespace')
    image, renderer_image = _image(s.image), _image(s.renderer_image)
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', s.release_id):
        raise ValueError('invalid release identity')
    api_ip = ipaddress.ip_address(s.api_host)
    if type(s.api_port) is not int or not 1 <= s.api_port <= 65535:
        raise ValueError('invalid API port')
    api_url = f'https://[{api_ip}]:{s.api_port}' if api_ip.version == 6 else f'https://{api_ip}:{s.api_port}'
    executor_labels = {'app.kubernetes.io/name': NAME}
    namespace_selector = lambda value: {'matchLabels': {'kubernetes.io/metadata.name': value}}
    meta = lambda name, namespace=s.namespace: {'name': name, 'namespace': namespace}
    service_account = dict(apiVersion='v1', kind='ServiceAccount', metadata=meta(NAME), automountServiceAccountToken=False)
    role = dict(apiVersion='rbac.authorization.k8s.io/v1', kind='Role', metadata=meta(NAME, s.renderer_namespace), rules=[
        dict(apiGroups=['batch'], resources=['jobs'], verbs=['create', 'get', 'list', 'delete']),
        dict(apiGroups=[''], resources=['configmaps'], verbs=['create', 'get', 'delete']),
        dict(apiGroups=[''], resources=['pods'], verbs=['get', 'list']),
        dict(apiGroups=[''], resources=['pods/log'], verbs=['get']),
        dict(apiGroups=['networking.k8s.io'], resources=['networkpolicies'], verbs=['get', 'list'])])
    binding = dict(apiVersion='rbac.authorization.k8s.io/v1', kind='RoleBinding', metadata=meta(NAME, s.renderer_namespace),
        roleRef=dict(apiGroup='rbac.authorization.k8s.io', kind='Role', name=NAME),
        subjects=[dict(kind='ServiceAccount', name=NAME, namespace=s.namespace)])
    deny = dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy', metadata=meta('deny-all', s.renderer_namespace),
        spec=dict(podSelector={}, policyTypes=['Ingress', 'Egress'], ingress=[], egress=[]))
    parent_policy = dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy', metadata=meta(NAME),
        spec=dict(podSelector={'matchLabels': executor_labels}, policyTypes=['Ingress', 'Egress'],
            ingress=[{'from': [{'namespaceSelector': namespace_selector(s.workbench_namespace),
                               'podSelector': {'matchLabels': {'app.kubernetes.io/name': s.workbench_label}}}],
                      'ports': [{'protocol': 'TCP', 'port': PORT}]}],
            egress=[{'to': [{'ipBlock': {'cidr': str(api_ip) + ('/128' if api_ip.version == 6 else '/32')}}],
                     'ports': [{'protocol': 'TCP', 'port': s.api_port}]}]))
    pvc_spec = dict(accessModes=['ReadWriteOnce'], resources={'requests': {'storage': '20Gi'}})
    if s.storage_class:
        pvc_spec['storageClassName'] = _name(s.storage_class)
    pvc = dict(apiVersion='v1', kind='PersistentVolumeClaim', metadata=meta(NAME), spec=pvc_spec)
    env = dict(VIDEO_MOTION_RENDER_IMAGE=renderer_image, VIDEO_MOTION_RENDER_RELEASE=s.release_id,
        VIDEO_MOTION_RUNTIME_FILE='/run/motion-runtime/runtime.json', VIDEO_MOTION_K8S_API=api_url,
        VIDEO_MOTION_K8S_TOKEN_FILE='/run/motion-k8s/token', VIDEO_MOTION_K8S_CA_FILE='/run/motion-k8s/ca.crt',
        VIDEO_MOTION_NAMESPACE=s.renderer_namespace, VIDEO_MOTION_JOURNAL_PATH='/data/motion-render-jobs.db',
        VIDEO_MOTION_DATA_DIR='/data/executions', VIDEO_MOTION_SERVICE_TOKEN_FILE='/run/motion-token/token',
        VIDEO_STATIC_RENDERER='weasyprint', PYTHONDONTWRITEBYTECODE='1', XDG_CACHE_HOME='/tmp/cache',
        TMPDIR='/tmp')
    security = dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, readOnlyRootFilesystem=True,
                    allowPrivilegeEscalation=False, capabilities={'drop': ['ALL']}, seccompProfile={'type': 'RuntimeDefault'})
    container = dict(name='executor', image=image, imagePullPolicy='Always', securityContext=security,
        command=['python', '-m', 'uvicorn', 'video_workbench.motion_render_service:app_factory', '--factory',
                 '--host', '0.0.0.0', '--port', str(PORT), '--workers', '1', '--no-access-log'],
        ports=[dict(name='http', containerPort=PORT)], env=[dict(name=k, value=v) for k, v in env.items()],
        resources=dict(requests=dict(cpu='500m', memory='1Gi', **{'ephemeral-storage': '256Mi'}),
                       limits=dict(cpu='2', memory='4Gi', **{'ephemeral-storage': '2Gi'})),
        startupProbe=dict(httpGet=dict(path='/health/live', port=PORT), periodSeconds=3, failureThreshold=30),
        livenessProbe=dict(httpGet=dict(path='/health/live', port=PORT), periodSeconds=15, timeoutSeconds=3),
        readinessProbe=dict(httpGet=dict(path='/health/ready', port=PORT), periodSeconds=15, timeoutSeconds=25),
        volumeMounts=[dict(name='data', mountPath='/data'), dict(name='tmp', mountPath='/tmp'),
            dict(name='token', mountPath='/run/motion-token', readOnly=True),
            dict(name='runtime', mountPath='/run/motion-runtime', readOnly=True),
            dict(name='k8s', mountPath='/run/motion-k8s', readOnly=True)])
    deployment = dict(apiVersion='apps/v1', kind='Deployment', metadata=meta(NAME),
        spec=dict(replicas=1, strategy={'type': 'Recreate'}, selector={'matchLabels': executor_labels},
            template=dict(metadata=dict(labels=executor_labels, annotations={'video-workbench.panghuer.top/release-id': s.release_id}),
                spec=dict(serviceAccountName=NAME, automountServiceAccountToken=False, enableServiceLinks=False,
                    terminationGracePeriodSeconds=30, nodeSelector={'kubernetes.io/arch': 'arm64'},
                    securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, fsGroup=10001,
                                         seccompProfile={'type': 'RuntimeDefault'}), containers=[container],
                    volumes=[dict(name='data', persistentVolumeClaim={'claimName': NAME}),
                        dict(name='tmp', emptyDir={'sizeLimit': '2Gi'}),
                        dict(name='token', secret={'secretName': s.token_secret, 'defaultMode': 0o440,
                                                 'items': [{'key': 'token', 'path': 'token'}]}),
                        dict(name='runtime', configMap={'name': s.runtime_configmap, 'defaultMode': 0o444,
                                                      'items': [{'key': 'runtime.json', 'path': 'runtime.json'}]}),
                        dict(name='k8s', projected={'defaultMode': 0o440, 'sources': [
                            {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 3600}},
                            {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]})]))))
    service = dict(apiVersion='v1', kind='Service', metadata=meta(NAME),
                   spec=dict(selector=executor_labels, ports=[dict(name='http', port=PORT, targetPort=PORT)]))
    return dict(apiVersion='v1', kind='List', items=[
        dict(apiVersion='v1', kind='Namespace', metadata={'name': s.namespace}),
        dict(apiVersion='v1', kind='Namespace', metadata={'name': s.renderer_namespace, 'labels': {
            'pod-security.kubernetes.io/enforce': 'restricted', 'pod-security.kubernetes.io/enforce-version': 'latest'}}),
        service_account, role, binding, deny, parent_policy, pvc, deployment, service])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--renderer-image', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--api-host', required=True)
    parser.add_argument('--api-port', type=int, default=443)
    parser.add_argument('--namespace', default='video-motion-executor')
    parser.add_argument('--renderer-namespace', default='video-motion-render')
    parser.add_argument('--workbench-namespace', default='video-workbench')
    parser.add_argument('--storage-class', default='')
    args = parser.parse_args()
    try:
        output = render(Settings(**vars(args)))
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
