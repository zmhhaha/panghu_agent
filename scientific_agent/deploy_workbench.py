"""Render or upgrade the existing scientific API and web entry, preserving data."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import importlib.util
spec = importlib.util.spec_from_file_location('scientific_renderer', ROOT / 'k8s/scientific-pi/render.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def readiness_assertion():
    """Code run in the already packaged API, using its projected caller token.

    Capability entries are revision-specific. A ready later revision cannot
    satisfy this business coordinator's fixed v1 input/output contracts.
    """
    required = ('research.plan.v1', 'research.retrieve.v1', 'research.outline.v1',
                'research.draft.v1', 'research.revise.v1')
    return ("required=" + repr(required) + "\n"
        "assert type(p) is list and all(any(type(r) is dict and r.get('profile') == name "
        "and r.get('profile_revision') == 'v1' and r.get('ready') is True "
        "and r.get('supports_inputs') is True and type(r.get('max_timeout_seconds')) is int "
        "and r['max_timeout_seconds'] >= 1800 for r in p) for name in required), "
        "'Scientific v1 stages unavailable'\n")


def resources(namespace, api_image, ui_image):
    renderer.namespace_name(namespace)
    renderer.pinned_image(ui_image)
    api = renderer.api_patch(namespace, api_image, execution_backend='agent-service')
    ui = {'spec': {'template': {'spec': {
        'automountServiceAccountToken': False,
        'volumes': [{'name': 'source', '$patch': 'delete'}],
        'containers': [{'name': 'ui', 'image': ui_image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python', '-m', 'uvicorn', 'scientific_agent.workbench_server:app',
                '--host', '0.0.0.0', '--port', '7860', '--no-proxy-headers', '--no-access-log'],
            'volumeMounts': [{'mountPath': '/app/app/ui/agent.py', '$patch': 'delete'}],
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10000,
                'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
            'resources': {'requests': {'cpu': '50m', 'memory': '96Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
            'env': [{'name': 'API_BASE', 'value': f'http://api.{namespace}.svc.cluster.local'},
                    {'name': 'SCIENTIFIC_PUBLIC_ORIGIN', 'value': 'https://scientific-agent.panghuer.top'},
                    {'name': 'GRADIO_ROOT_PATH', '$patch': 'delete'}, {'name': 'GRADIO_SERVER_PORT', '$patch': 'delete'}],
            'readinessProbe': {'httpGet': {'path': '/healthz', 'port': 7860}, 'initialDelaySeconds': 3, 'periodSeconds': 5},
            'livenessProbe': {'httpGet': {'path': '/healthz', 'port': 7860}, 'initialDelaySeconds': 15, 'periodSeconds': 20}}]}}}}
    policies = []
    for name, label, peer, port in (
        ('scientific-ui-from-oauth', 'ui', {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'oauth'}},
            'podSelector': {'matchLabels': {'app': 'oauth2-proxy-scientific-agent'}}}, 7860),
        ('scientific-api-from-ui', 'research-api', {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': namespace}},
            'podSelector': {'matchLabels': {'app': 'ui'}}}, 8000)):
        policies.append({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': name, 'namespace': namespace}, 'spec': {
                'podSelector': {'matchLabels': {'app': label}}, 'policyTypes': ['Ingress'],
                'ingress': [{'from': [peer], 'ports': [{'protocol': 'TCP', 'port': port}]}]}})
    return api, ui, {'apiVersion': 'v1', 'kind': 'List', 'items': policies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-image')
    parser.add_argument('--ui-image')
    parser.add_argument('--namespace', default='scientific-agent')
    parser.add_argument('--kubeconfig')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'scientific_agent/rendered'
    try:
        api_image = args.api_image or (directory / 'api-image.txt').read_text().strip()
        ui_image = args.ui_image or (directory / 'ui-image.txt').read_text().strip()
        api, ui, policies = resources(args.namespace, api_image, ui_image)
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if re.fullmatch(r'[0-9a-f]{40}', revision) is None:
            raise ValueError('source_revision_required')
        for patch, image in ((api, api_image), (ui, ui_image)):
            annotations = {'panghu.io/source-revision': revision,
                           'panghu.io/image-digest': image.rsplit('@', 1)[-1]}
            patch.setdefault('metadata', {}).setdefault('annotations', {}).update(annotations)
            patch['spec']['template'].setdefault('metadata', {}).setdefault('annotations', {}).update(annotations)
        output = args.output or directory / datetime.now(timezone.utc).strftime('deploy-%Y%m%dT%H%M%S%fZ')
        output.mkdir(parents=True, exist_ok=False)
        for name, value in (('api-patch', api), ('ui-patch', ui), ('ingress-policies', policies)):
            (output / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')
        if not args.apply:
            print('Rendered ' + str(output) + '; no cluster changes.')
            return 0
        command = ['kubectl', *(['--kubeconfig', args.kubeconfig] if args.kubeconfig else []), '-n', args.namespace]
        def run(*parts, **kwargs):
            return subprocess.run(command + list(parts), check=True, **kwargs)
        # Capture complete deployment specs for an explicit rollback; no Secret
        # values are fetched or copied. Keep immutable old code ConfigMaps/PVCs.
        for name in ('api', 'ui'):
            saved = json.loads(run('get', 'deployment/' + name, '-o', 'json', stdout=subprocess.PIPE).stdout)
            (output / (name + '-rollback.json')).write_text(json.dumps([
                {'op': 'replace', 'path': '/spec', 'value': saved['spec']}], indent=2) + '\n')
            run('patch', 'deployment/' + name, '--type', 'strategic', '--patch-file',
                str(output / (name + '-patch.json')), '--dry-run=server')
        run('apply', '--dry-run=server', '-f', str(output / 'ingress-policies.json'))
        run('patch', 'deployment/api', '--type', 'strategic', '--patch-file', str(output / 'api-patch.json'))
        run('rollout', 'status', 'deployment/api', '--timeout=180s')
        # Gate public cutover on actual current Agent readiness. This call uses
        # the API's projected caller credential without printing it.
        check = ("from app.api.scientific_agent import app\n"
            "p=app.state.worker._request('GET','/v1/capabilities')['profiles']\n"
            + readiness_assertion() + "print('All scientific stages ready')")
        run('exec', 'deployment/api', '-c', 'api', '--', 'python', '-c', check)
        run('apply', '-f', str(output / 'ingress-policies.json'))
        run('patch', 'deployment/ui', '--type', 'strategic', '--patch-file', str(output / 'ui-patch.json'))
        run('rollout', 'status', 'deployment/ui', '--timeout=180s')
        print('Deployed existing scientific entry; rollback specs saved in ' + str(output))
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Scientific deployment incomplete; inspect resource status and retained rollback specs.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
