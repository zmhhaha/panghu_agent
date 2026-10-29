"""Render dedicated scientific worker resources or the existing API strategic patch.

No cluster calls or credentials. JSON is valid Kubernetes YAML. Images MUST use
content digests; acceptance flags are operator evidence assertions, not probes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlparse

REVISION = 'pi-jsonl-0.73.1-v1'
BUNDLE = 'local-procedures-v2'
WORKER = 'scientific-pi-worker'
AGENT_TOKEN_VOLUME = 'scientific-agent-service'
AGENT_TOKEN_PATH = '/run/secrets/scientific-agent-service/token'


def namespace_name(value):
    if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?', value):
        raise ValueError('Invalid namespace')
    return value


def secret_name(value):
    if (len(value) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?', part)
                                    for part in value.split('.'))):
        raise ValueError('Invalid Agent caller Secret name')
    return value


def backend_name(value):
    if value not in {'pi', 'agent-service'}:
        raise ValueError('Unsupported scientific execution backend')
    return value


def pinned_image(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}', value):
        raise ValueError('Images must be repository@sha256:<64 lowercase hex>')
    return value


def render(namespace, worker_image, model_endpoint, model, verified=False):
    pinned_image(worker_image)
    namespace_name(namespace)
    endpoint = urlparse(model_endpoint)
    if endpoint.scheme not in {'http', 'https'} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError('Invalid trusted model endpoint')
    internal = endpoint.hostname == 'llm-service.llm.svc.cluster.local'
    if not internal and endpoint.scheme != 'https':
        raise ValueError('External model endpoints require HTTPS')
    labels = {'app': WORKER}
    pod_labels = dict(labels)
    if internal:
        # The gateway ingress policy admits only explicitly labelled callers.
        pod_labels['llm-client'] = 'true'
    def obj(api, kind, name, spec):
        return {'apiVersion': api, 'kind': kind, 'metadata': {'name': name, 'namespace': namespace}, 'spec': spec}
    def env(name, value):
        return {'name': name, 'value': str(value)}
    def secret(name):
        return {'name': name, 'valueFrom': {'secretKeyRef': {'name': WORKER, 'key': name}}}
    probe = ['python', '-m', 'scientific_agent.pi_healthcheck']
    container = {
        'name': 'worker', 'image': worker_image, 'imagePullPolicy': 'IfNotPresent',
        'ports': [{'name': 'control', 'containerPort': 8080}],
        'env': [secret('SCIENTIFIC_PI_WORKER_TOKEN'), secret('SCIENTIFIC_PI_MODEL_API_KEY'),
                env('SCIENTIFIC_PI_RUNTIME_DIR', '/data'), env('SCIENTIFIC_PI_IMAGE_DIGEST', worker_image.rsplit('@', 1)[1]),
                env('SCIENTIFIC_PI_RUNTIME_REVISION', REVISION), env('SCIENTIFIC_PI_SKILL_BUNDLE_REVISION', BUNDLE),
                env('SCIENTIFIC_PI_MODEL_PROVIDER', 'scientific-worker'), env('SCIENTIFIC_PI_MODEL', model),
                env('SCIENTIFIC_PI_MODEL_BASE_URL', model_endpoint), env('SCIENTIFIC_PI_MODEL_API', 'openai-completions'),
                *[env('SCIENTIFIC_PI_' + field + '_VERIFIED', '1' if verified else '0') for field in ('CAPABILITY', 'MODEL', 'ISOLATION')]],
        'securityContext': {'runAsUser': 0, 'runAsGroup': 0, 'allowPrivilegeEscalation': False,
            'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL'], 'add': ['CHOWN', 'DAC_OVERRIDE', 'SETUID', 'SETGID', 'KILL']}},
        'resources': {'requests': {'cpu': '250m', 'memory': '512Mi'}, 'limits': {'cpu': '2', 'memory': '2Gi', 'ephemeral-storage': '512Mi'}},
        'volumeMounts': [{'name': 'data', 'mountPath': '/data'}, {'name': 'tmp', 'mountPath': '/tmp'}],
        'startupProbe': {'exec': {'command': probe}, 'periodSeconds': 5, 'failureThreshold': 30},
        'livenessProbe': {'exec': {'command': probe}, 'periodSeconds': 20, 'timeoutSeconds': 5},
        'readinessProbe': {'exec': {'command': probe + ['--ready']}, 'periodSeconds': 10, 'timeoutSeconds': 5},
    }
    deployment = obj('apps/v1', 'Deployment', WORKER, {
        'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': labels},
        'template': {'metadata': {'labels': pod_labels}, 'spec': {
            'automountServiceAccountToken': False, 'terminationGracePeriodSeconds': 30,
            'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [container], 'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': WORKER}},
                                                  {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]}}})
    pvc = obj('v1', 'PersistentVolumeClaim', WORKER, {'accessModes': ['ReadWriteOnce'], 'storageClassName': 'ceph-rbd', 'resources': {'requests': {'storage': '10Gi'}}})
    service = obj('v1', 'Service', WORKER, {'selector': labels, 'ports': [{'name': 'control', 'port': 8080, 'targetPort': 'control'}]})
    source_api = {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': namespace}}, 'podSelector': {'matchLabels': {'app': 'research-api'}}}
    dns = {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}, 'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}], 'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]}
    # Broad public HTTPS is explicit; it is NOT a domain/source allowlist.
    # IPv6 public internet is not allowed; link-local, node and cluster ranges are excluded.
    public = {'to': [{'ipBlock': {'cidr': '0.0.0.0/0', 'except': ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '169.254.0.0/16', '198.18.0.0/15', '224.0.0.0/4', '240.0.0.0/4']}}], 'ports': [{'protocol': 'TCP', 'port': 443}]}
    egress = [dns, public]
    if internal:
        egress.append({'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'llm'}}, 'podSelector': {'matchLabels': {'app': 'llm-service'}}}], 'ports': [{'protocol': 'TCP', 'port': 8000}]})
    policy = obj('networking.k8s.io/v1', 'NetworkPolicy', WORKER, {'podSelector': {'matchLabels': labels}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [source_api], 'ports': [{'protocol': 'TCP', 'port': 8080}]}], 'egress': egress})
    api_egress = api_egress_policy(namespace)
    # Do not create an Egress-only policy if existing API has no other egress policy:
    # NetworkPolicy union would newly isolate it and break SQLite/mail.
    # The deployment script applies worker resources only; API egress patch is
    # returned separately for explicit review against existing policy coverage.
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [pvc, deployment, service, policy]}, api_egress


def api_egress_policy(namespace, *, execution_backend='pi', agent_namespace='agent-service'):
    """Review separately; applying this alone can newly isolate the business API."""
    namespace_name(namespace)
    backend_name(execution_backend)
    if execution_backend == 'agent-service':
        namespace_name(agent_namespace)
        target = {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': agent_namespace}},
                  'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'agent-execution-service', 'component': 'api'}}}
    else:
        target = {'podSelector': {'matchLabels': {'app': WORKER}}}
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': 'scientific-api-to-' + execution_backend, 'namespace': namespace},
            'spec': {'podSelector': {'matchLabels': {'app': 'research-api'}}, 'policyTypes': ['Egress'],
                     'egress': [{'to': [target], 'ports': [{'protocol': 'TCP', 'port': 8080}]}]}}


def api_patch(namespace, image, *, execution_backend='pi', agent_namespace='agent-service',
              agent_caller_secret='scientific-agent-service', agent_caller_secret_key='token',
              agent_generation_revision='v1', agent_revision_revision='v1'):
    namespace_name(namespace)
    pinned_image(image)
    backend_name(execution_backend)
    def value(name, text): return {'name': name, 'value': text}
    agent_env = ('SCIENTIFIC_AGENT_SERVICE_URL', 'SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE',
                 'SCIENTIFIC_AGENT_GENERATION_REVISION', 'SCIENTIFIC_AGENT_REVISION_REVISION')
    volumes = [{'name': name, '$patch': 'delete'} for name in ('source', 'scientific-workflow')]
    mounts = [{'mountPath': path, '$patch': 'delete'} for path in ('/app/app/api/agent.py', '/app/scientific_agent')]
    if execution_backend == 'agent-service':
        namespace_name(agent_namespace)
        secret_name(agent_caller_secret)
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', agent_caller_secret_key):
            raise ValueError('Invalid Agent caller Secret key')
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', revision)
               for revision in (agent_generation_revision, agent_revision_revision)):
            raise ValueError('Invalid scientific task contract revision')
        # Use the existing business ESO projection, never copy a token value or
        # a model credential into generated configuration.
        volumes.append({'name': AGENT_TOKEN_VOLUME, 'secret': {'secretName': agent_caller_secret,
            'defaultMode': 0o444, 'items': [{'key': agent_caller_secret_key, 'path': 'token'}]}})
        mounts.append({'name': AGENT_TOKEN_VOLUME, 'mountPath': AGENT_TOKEN_PATH, 'subPath': 'token', 'readOnly': True})
        agent_values = [value(name, text) for name, text in zip(agent_env, (
            f'http://agent-service.{agent_namespace}.svc.cluster.local:8080', AGENT_TOKEN_PATH,
            agent_generation_revision, agent_revision_revision))]
    else:
        # Explicit rollback removes only this renderer's Agent configuration.
        volumes.append({'name': AGENT_TOKEN_VOLUME, '$patch': 'delete'})
        mounts.append({'mountPath': AGENT_TOKEN_PATH, '$patch': 'delete'})
        agent_values = [{'name': name, '$patch': 'delete'} for name in agent_env]
    return {'spec': {'template': {
        'metadata': {'labels': {'llm-client': None, 'rag-client': None}},
        'spec': {
        # These are the observed scientific API overlays. Keep unrelated volumes,
        # mounts and environment variables through strategic list merge.
        'initContainers': [{'name': 'rag-sync', '$patch': 'delete'}],
        'volumes': volumes,
        'containers': [{'name': 'api', 'image': image,
        'command': ['python', '-m', 'uvicorn', 'app.api.scientific_agent:app', '--host', '0.0.0.0', '--port', '8000'],
        'volumeMounts': mounts,
        'env': [value('SCIENTIFIC_EXECUTION_BACKEND', execution_backend), *agent_values,
                {'name': 'SCIENTIFIC_AGENT_SERVICE_TOKEN', '$patch': 'delete'},
                # Retain the old worker connection for historical task recovery.
                value('SCIENTIFIC_DEFAULT_ENGINE', 'pi'), value('SCIENTIFIC_PI_WORKER_URL', f'http://{WORKER}.{namespace}.svc.cluster.local:8080'),
                value('SCIENTIFIC_PI_RUNTIME_REVISION', REVISION), value('SCIENTIFIC_PI_SKILL_BUNDLE_REVISION', BUNDLE),
                {'name': 'SCIENTIFIC_PI_WORKER_TOKEN', 'valueFrom': {'secretKeyRef': {'name': WORKER, 'key': 'SCIENTIFIC_PI_WORKER_TOKEN'}}},
                *[{'name': name, '$patch': 'delete'} for name in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_SERVICE_TOKEN', 'RAG_URL', 'RAG_TOKEN')]]}]}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='scientific-agent')
    parser.add_argument('--worker-image')
    parser.add_argument('--api-image')
    parser.add_argument('--execution-backend', choices=('pi', 'agent-service'), default='pi')
    parser.add_argument('--agent-namespace', default='agent-service')
    parser.add_argument('--agent-caller-secret', default='scientific-agent-service')
    parser.add_argument('--agent-caller-secret-key', default='token')
    parser.add_argument('--agent-generation-revision', default='v1')
    parser.add_argument('--agent-revision-revision', default='v1')
    parser.add_argument('--model-endpoint', default='http://llm-service.llm.svc.cluster.local/v1')
    parser.add_argument('--model', default='llm-trusted')
    parser.add_argument('--assert-verified-runtime', action='store_true', help='Use only after recording real Linux/model/tool/isolation evidence; this does not perform verification')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--api-patch', action='store_true')
    selection.add_argument('--api-egress-policy', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.execution_backend == 'agent-service' and args.assert_verified_runtime:
            raise ValueError('--assert-verified-runtime applies only to the dedicated Pi worker')
        if args.api_patch:
            if not args.api_image: raise ValueError('--api-image required with --api-patch')
            output = api_patch(args.namespace, args.api_image, execution_backend=args.execution_backend,
                agent_namespace=args.agent_namespace, agent_caller_secret=args.agent_caller_secret,
                agent_caller_secret_key=args.agent_caller_secret_key,
                agent_generation_revision=args.agent_generation_revision, agent_revision_revision=args.agent_revision_revision)
        elif args.api_egress_policy:
            output = api_egress_policy(args.namespace, execution_backend=args.execution_backend,
                                       agent_namespace=args.agent_namespace)
        else:
            if args.execution_backend != 'pi':
                raise ValueError('Agent service mode requires --api-patch or --api-egress-policy')
            if not args.worker_image: raise ValueError('--worker-image required for dedicated Pi worker resources')
            output, _ = render(args.namespace, args.worker_image, args.model_endpoint, args.model, args.assert_verified_runtime)
        text = json.dumps(output, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding='utf-8')
        else:
            print(text, end='')
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
