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
BUNDLE = 'local-procedures-v1'
WORKER = 'scientific-pi-worker'


def pinned_image(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}', value):
        raise ValueError('Images must be repository@sha256:<64 lowercase hex>')
    return value


def render(namespace, worker_image, model_endpoint, model, verified=False):
    pinned_image(worker_image)
    if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?', namespace):
        raise ValueError('Invalid namespace')
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
    api_egress = obj('networking.k8s.io/v1', 'NetworkPolicy', 'scientific-api-to-pi', {'podSelector': {'matchLabels': {'app': 'research-api'}}, 'policyTypes': ['Egress'],
        'egress': [{'to': [{'podSelector': {'matchLabels': labels}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]}]})
    # Do not create an Egress-only policy if existing API has no other egress policy:
    # NetworkPolicy union would newly isolate it and break SQLite/model/mail.
    # The deployment script applies worker resources only; API egress patch is
    # returned separately for explicit review against existing policy coverage.
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [pvc, deployment, service, policy]}, api_egress


def api_patch(namespace, image):
    pinned_image(image)
    def value(name, text): return {'name': name, 'value': text}
    return {'spec': {'template': {'spec': {'containers': [{'name': 'api', 'image': image,
        'command': ['python', '-m', 'uvicorn', 'app.api.scientific_agent:app', '--host', '0.0.0.0', '--port', '8000'],
        'env': [value('SCIENTIFIC_DEFAULT_ENGINE', 'crewai'), value('SCIENTIFIC_PI_WORKER_URL', f'http://{WORKER}.{namespace}.svc.cluster.local:8080'),
                value('SCIENTIFIC_PI_RUNTIME_REVISION', REVISION), value('SCIENTIFIC_PI_SKILL_BUNDLE_REVISION', BUNDLE),
                {'name': 'SCIENTIFIC_PI_WORKER_TOKEN', 'valueFrom': {'secretKeyRef': {'name': WORKER, 'key': 'SCIENTIFIC_PI_WORKER_TOKEN'}}}]}]}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='scientific-agent')
    parser.add_argument('--worker-image', required=True)
    parser.add_argument('--api-image')
    parser.add_argument('--model-endpoint', default='http://llm-service.llm.svc.cluster.local/v1')
    parser.add_argument('--model', default='llm-trusted')
    parser.add_argument('--assert-verified-runtime', action='store_true', help='Use only after recording real Linux/model/tool/isolation evidence; this does not perform verification')
    parser.add_argument('--api-patch', action='store_true')
    parser.add_argument('--api-egress-policy', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        resources, egress = render(args.namespace, args.worker_image, args.model_endpoint, args.model, args.assert_verified_runtime)
        if args.api_patch:
            if not args.api_image: raise ValueError('--api-image required with --api-patch')
            output = api_patch(args.namespace, args.api_image)
        else:
            output = egress if args.api_egress_policy else resources
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
