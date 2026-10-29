"""Render the PPT business connector; model routing belongs to Agent Service."""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from ppt_service.deploy.hermes.runtime import CONTROLLER, kubectl, render as render_sandbox_runtime

NAME = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')
DRAIN_QUERY = """SELECT json_build_object(
    'revision', (SELECT max(revision) FROM public.api_schema_revision),
    'active_jobs', (SELECT count(*) FROM public.jobs WHERE status IN
        ('queued','starting','running','collecting','validating','cancelling')),
    'occupied_slots', (SELECT count(*) FROM public.execution_slots WHERE job_id IS NOT NULL),
    'pending_outbox', (SELECT count(*) FROM public.ppt_agent_outbox WHERE state != 'mapped'),
    'parsing_assets', (SELECT count(*) FROM public.assets WHERE parse_status = 'parsing'),
    'outbox_runtime_access', (SELECT bool_and(has_table_privilege('ppt_runtime',
        'public.ppt_agent_outbox', privilege)) FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS privilege)
);"""
CAPABILITY_CHECK = """import json, pathlib, urllib.request
try:
    config = json.loads(pathlib.Path('/etc/agent-service/config.json').read_text())
    caller = next(row for row in config['callers'] if row['caller'] == 'ppt-service')
    private = json.loads(pathlib.Path('/run/agent-service/secrets.json').read_text())
    request = urllib.request.Request('http://127.0.0.1:8080/v1/capabilities',
        headers={'Authorization': 'Bearer ' + private['tokens'][caller['token_ref']]})
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.loads(response.read(1024 * 1024))
    print(json.dumps({'profiles': [{key: row.get(key) for key in
        ('profile','profile_revision','configured','ready')} for row in value['profiles']]}))
except Exception:
    raise SystemExit('agent_capability_check_failed')
"""


def require_drained(namespace, *, runner=subprocess.run):
    """Read-only cutover gate; the operator pauses and drains the old chain first.

    A queued prepare -> generate successor counts as active. A free execution
    slot alone is not sufficient evidence that the old backend is drained.
    """
    config = json.loads(kubectl(['-n', namespace, 'get', 'configmap', 'ppt-site', '-o', 'json'], runner=runner))
    if config.get('data', {}).get('PPT_EXECUTION_ENABLED') != 'false':
        raise RuntimeError('site_admission_must_be_paused_before_connector_switch')
    site = json.loads(kubectl(['-n', namespace, 'get', 'deployment', 'ppt-site', '-o', 'json'], runner=runner))
    if site.get('spec', {}).get('replicas', 1):
        marker = kubectl(['-n', namespace, 'exec', 'deploy/ppt-site', '-c', 'site', '--',
            'python', '-c', "import os; print('disabled' if os.environ.get('PPT_EXECUTION_ENABLED') == 'false' else 'enabled')"], runner=runner)
        if marker.strip() != 'disabled':
            raise RuntimeError('website_must_restart_with_admission_disabled')
    old = json.loads(kubectl(['-n', namespace, 'get', 'deployment', CONTROLLER, '-o', 'json'], runner=runner))
    if old.get('spec', {}).get('replicas') != 0:
        raise RuntimeError('old_controller_must_be_drained_and_stopped')
    pods = json.loads(kubectl(['-n', namespace, 'get', 'pods', '-l',
        'app in (' + CONTROLLER + ',ppt-hermes-worker,ppt-model-proxy,ppt-sandbox,ppt-execution)',
        '-o', 'json'], runner=runner))
    if pods.get('items') != []:
        raise RuntimeError('old_execution_pods_must_be_gone')
    state = json.loads(kubectl(['-n', 'data', 'exec', 'postgres-0', '--', 'psql', '-X', '-At',
        '-v', 'ON_ERROR_STOP=1', '-U', 'appuser', '-d', 'ppt_service', '-c', DRAIN_QUERY], runner=runner))
    if (state.get('revision') != 4 or state.get('outbox_runtime_access') is not True
            or any(type(state.get(key)) is not int or state[key] != 0 for key in
                   ('active_jobs', 'occupied_slots', 'pending_outbox', 'parsing_assets'))):
        raise RuntimeError('schema4_runtime_access_and_fully_drained_chain_required')
    return old


def require_capabilities(namespace, revision, *, runner=subprocess.run):
    value = json.loads(kubectl(['-n', namespace, 'exec', 'deploy/agent-service-api', '--',
                                'python', '-c', CAPABILITY_CHECK], runner=runner))
    expected = {'ppt.' + phase + '.v1' for phase in ('plan_initial','plan_options','prepare','generate','revise')}
    rows = value.get('profiles')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError('all_five_ppt_capabilities_must_be_ready')
    rows = [row for row in rows if row.get('profile_revision') == revision]
    if (len(rows) != len(expected)
            or {row.get('profile') for row in rows} != expected
            or any(row.get('profile_revision') != revision or row.get('configured') is not True
                   or row.get('ready') is not True for row in rows)):
        raise RuntimeError('all_five_ppt_capabilities_must_be_ready')


def render(*, agent_service_url='http://agent-service.agent-service.svc.cluster.local:8080',
           agent_namespace='agent-service', caller_secret='ppt-agent-service',
           caller_secret_key='token', profile_revision='v1', **runtime):
    for name in (agent_namespace, caller_secret):
        if not NAME.fullmatch(name):
            raise ValueError('invalid_agent_connector_resource')
    if not re.fullmatch('[A-Za-z0-9_.-]{1,100}', profile_revision):
        raise ValueError('invalid_agent_profile_revision')
    if not re.fullmatch('[-._a-zA-Z0-9]{1,253}', caller_secret_key):
        raise ValueError('invalid_agent_caller_secret_key')
    parsed = urlsplit(agent_service_url)
    if (parsed.scheme != 'http' or parsed.hostname != f'agent-service.{agent_namespace}.svc.cluster.local'
            or parsed.port != 8080 or parsed.path not in ('', '/') or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('explicit_internal_agent_service_origin_required')
    manifest = render_sandbox_runtime(**runtime)
    for item in manifest['items']:
        if item['kind'] == 'Role':
            item['rules'] = [rule for rule in item['rules'] if 'secrets' not in rule.get('resources', [])]
        if item['kind'] == 'Deployment':
            spec = item['spec']['template']['spec']; container = spec['containers'][0]
            env = {row['name']: row for row in container['env']}
            enabled = env['PPT_CONTROL_STATE']['value'] == 'configured'
            for name in ('PPT_PROVIDER_SECRET', 'PPT_PROVIDER_SECRET_KEY'):
                env.pop(name, None)
            for name, value in {'PPT_EXECUTION_BACKEND': 'agent-service',
                    'PPT_AGENT_SERVICE_ENABLED': 'true' if enabled else 'false',
                    'PPT_AGENT_SERVICE_URL': agent_service_url.rstrip('/'),
                    'PPT_AGENT_SERVICE_TOKEN_FILE': '/etc/ppt-agent-service/token',
                    'PPT_AGENT_PROFILE_REVISION': profile_revision}.items():
                env[name] = {'name': name, 'value': value}
            container['env'] = list(env.values())
            container['volumeMounts'].append({'name': 'agent-caller', 'mountPath': '/etc/ppt-agent-service/token',
                                            'subPath': 'token', 'readOnly': True})
            spec['volumes'].append({'name': 'agent-caller', 'secret': {'secretName': caller_secret,
                  'defaultMode': 0o444, 'items': [{'key': caller_secret_key, 'path': 'token'}]}})
            # The existing controller Deployment is replaced in place. Its stable
            # identity avoids running an old and new scheduler simultaneously.
            item['metadata'].setdefault('annotations', {})['ppt.panghuer.top/execution-contract'] = 'agent-service.v1'
        if item['kind'] == 'NetworkPolicy' and item['metadata']['name'] == 'hermes-controller-egress':
            item['spec']['egress'].append({'to': [{
                'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': agent_namespace}},
                'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'agent-execution-service'}}}],
                'ports': [{'protocol': 'TCP', 'port': 8080}]})
    return manifest


def deploy(manifest, *, runner=subprocess.run, enable_site=False):
    deployment = next(item for item in manifest['items'] if item['kind'] == 'Deployment')
    namespace = deployment['metadata']['namespace']; spec = deployment['spec']['template']['spec']
    env = {row['name']: row.get('value') for row in spec['containers'][0]['env']}
    if enable_site and env.get('PPT_AGENT_SERVICE_ENABLED') != 'true':
        raise ValueError('enabled_agent_connector_required')
    kubectl(['get', 'namespace', namespace, '-o', 'name'], runner=runner)
    kubectl(['-n', namespace, 'get', 'deployment', 'ppt-site', '-o', 'name'], runner=runner)
    pvc = json.loads(kubectl(['-n', namespace, 'get', 'pvc', env['PPT_DATA_PVC'], '-o', 'json'], runner=runner))
    if pvc.get('status', {}).get('phase') != 'Bound' or 'ReadWriteMany' not in pvc.get('spec', {}).get('accessModes', []):
        raise RuntimeError('existing_bound_rwx_pvc_required')
    volume = next(v for v in spec['volumes'] if v['name'] == 'agent-caller')['secret']
    secrets = [('ppt-database-runtime', 'DATABASE_URL'), (volume['secretName'], volume['items'][0]['key'])]
    for name, key in secrets:
        # Contents remain private; failures only contain a fixed code.
        item = json.loads(kubectl(['-n', namespace, 'get', 'secret', name, '-o', 'json'], runner=runner))
        if not item.get('data', {}).get(key):
            raise RuntimeError('required_connector_secret_unavailable')
    # In-place replacement is permitted only after the saved old backend has
    # drained and stopped. This gate never cancels tasks or changes business DBs.
    require_drained(namespace, runner=runner)
    if env.get('PPT_AGENT_SERVICE_ENABLED') == 'true':
        agent_namespace = urlsplit(env['PPT_AGENT_SERVICE_URL']).hostname.split('.')[1]
        require_capabilities(agent_namespace, env['PPT_AGENT_PROFILE_REVISION'], runner=runner)
    # Apply is explicit; migrations and live readiness checks precede enabling.
    kubectl(['apply', '--dry-run=server', '-f', '-'], runner=runner, body=json.dumps(manifest))
    kubectl(['apply', '-f', '-'], runner=runner, body=json.dumps(manifest))
    kubectl(['-n', namespace, 'rollout', 'status', 'deployment/' + CONTROLLER, '--timeout=180s'], runner=runner)
    if enable_site:
        kubectl(['-n', namespace, 'patch', 'configmap', 'ppt-site', '--type=merge', '-p', json.dumps({
            'data': {'PPT_EXECUTION_ENABLED': 'true', 'PPT_EXECUTION_BACKEND': 'agent-service'}})], runner=runner)
        kubectl(['-n', namespace, 'rollout', 'restart', 'deployment/ppt-site'], runner=runner)
        kubectl(['-n', namespace, 'rollout', 'status', 'deployment/ppt-site', '--timeout=180s'], runner=runner)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller-image', required=True)
    parser.add_argument('--worker-image', required=True, help='Digest of the independent PPT validator image')
    parser.add_argument('--kube-api-cidr', action='append', required=True)
    parser.add_argument('--kube-api-port', action='append', type=int)
    parser.add_argument('--kube-api-server', default='https://192.168.137.101:6443')
    parser.add_argument('--namespace', default='ppt-service')
    parser.add_argument('--pvc', default='ppt-data')
    parser.add_argument('--pvc-data-prefix', default='state')
    parser.add_argument('--chart-oracle-configmap')
    parser.add_argument('--agent-service-url', default='http://agent-service.agent-service.svc.cluster.local:8080')
    parser.add_argument('--agent-namespace', default='agent-service')
    parser.add_argument('--caller-secret', default='ppt-agent-service')
    parser.add_argument('--caller-secret-key', default='token')
    parser.add_argument('--profile-revision', default='v1')
    parser.add_argument('--enable-execution', action='store_true', dest='enabled')
    parser.add_argument('--enable-site', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('ppt_service/deploy/agent/rendered/runtime.json'))
    args = vars(parser.parse_args(argv)); output = args.pop('output'); apply = args.pop('apply'); site = args.pop('enable_site')
    if site and (not apply or not args['enabled']):
        raise ValueError('enable_site_requires_apply_and_enabled_connector')
    args['kube_api_port'] = args['kube_api_port'] or (6443,)
    manifest = render(**args)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Reviewable PPT Agent Service manifest: ' + str(output))
    if apply:
        deploy(manifest, enable_site=site)


if __name__ == '__main__':
    main()
