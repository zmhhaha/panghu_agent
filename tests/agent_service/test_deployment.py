"""Offline rendered deployment contracts; no kubectl, Docker or model is called."""
from copy import deepcopy
import json

import pytest

from agent_service.deploy.render import RenderError, render
from registry_fixtures import make_catalog


@pytest.fixture
def configuration(tmp_path):
    config = make_catalog()
    config.update(callers=[dict(tenant='internal', caller='ppt-service',
        profiles=['workspace-task.v1'], token_ref='ppt-service')],
        api=dict(host='0.0.0.0', port=8080, max_request_bytes=4194304, max_input_bytes=67108864),
        model_proxy=dict(upstream_base_url='http://llm-service.llm.svc.cluster.local/v1',
            upstream_token_ref='model-upstream', signing_key_ref='model-signing'),
        kubernetes_workers=[dict(pool_id='pi-pool', binding_id='workspace-pi',
            binding_revision='binding-v1', engine='pi', namespace='agent-workers',
            image='registry:5000/agent-pi@sha256:' + 'a' * 64,
            host_config_map='agent-pi-host', pod_cidrs=['10.244.0.0/16'], enabled=False,
            token_ref='pi-worker-pool', receipt_key_ref='pi-worker-pool')])
    path = tmp_path / 'config.public.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return config, path


def rendered(tmp_path, path, **changes):
    return render(config_path=path, control_image='registry:5000/agent-control@sha256:' + 'b' * 64,
        output_dir=tmp_path / 'rendered', kubernetes_api_cidrs=['10.96.0.1/32', '192.168.137.101/32'],
        **changes)


def test_regular_deployment_wires_internal_services_init_job_and_fixed_subpath_files(tmp_path, configuration):
    config, path = configuration
    result = rendered(tmp_path, path)
    directory = result['output_dir']
    raw = (directory / 'configuration.yaml').read_text(encoding='utf-8')
    config_string = raw.split('  config.json: ', 1)[1].strip()
    assert json.loads(json.loads(config_string)) == config
    assert 'immutable: true' in raw and result['config_name'] in raw
    workloads = (directory / 'workloads.yaml').read_text(encoding='utf-8')
    assert 'name: agent-service-api' in workloads and 'name: agent-model-proxy' in workloads
    assert workloads.count('subPath: config.json') == workloads.count('subPath: secrets.json') == 3
    assert 'llm-client: \'true\'' in workloads
    job = json.loads((directory / 'init-db.json').read_text(encoding='utf-8'))
    spec = job['spec']['template']['spec']
    assert job['metadata']['namespace'] == 'agent-service' and spec['automountServiceAccountToken'] is False
    assert spec['containers'][0]['args'][-1] == 'init-db'
    assert {item.get('subPath') for item in spec['containers'][0]['volumeMounts']} >= {'config.json', 'secrets.json'}
    network = (directory / 'control-network-policy.yaml').read_text(encoding='utf-8')
    assert '192.168.137.101/32' in network and '10.96.0.1/32' in network
    assert 'app.kubernetes.io/name: ppt-hermes-controller' in network
    assert 'app: research-api' in network and '__' not in network


def test_historical_host_catalog_is_retained_when_current_route_arrays_change(tmp_path, configuration):
    config, path = configuration
    old = {key: deepcopy(config[key]) for key in ('task_contracts', 'capability_bundles',
        'tool_environments', 'execution_bindings', 'routes')}
    old['routes'] = []
    host = dict(schema_version=1, engine='pi', binding_id='workspace-pi', binding_revision='binding-v1',
        profile=dict(name='workspace-task.v1', revision='v1', runtime_revision='runtime-v1',
            image_digest='sha256:' + 'a' * 64, enabled=False, max_timeout_seconds=600),
        execution_catalog=old, model=None)
    directory = tmp_path / 'hosts/agent-pi-host'
    directory.mkdir(parents=True)
    (directory / 'config.json').write_text(json.dumps(host), encoding='utf-8')
    result = rendered(tmp_path, path, host_config_dir=tmp_path / 'hosts')
    maps = json.loads((result['output_dir'] / 'worker-hosts.json').read_text(encoding='utf-8'))
    assert maps['items'][0]['immutable'] is True
    saved = json.loads(maps['items'][0]['data']['config.json'])
    assert saved['execution_catalog']['routes'] == []
    changed = deepcopy(host)
    changed['execution_catalog']['capability_bundles'][0]['resources'][0]['sha256'] = '9' * 64
    (directory / 'config.json').write_text(json.dumps(changed), encoding='utf-8')
    with pytest.raises(RenderError, match='worker_host_catalog_mismatch'):
        rendered(tmp_path, path, host_config_dir=tmp_path / 'hosts')


def test_public_deployment_rejects_credentials_and_unbounded_control_endpoints(tmp_path, configuration):
    config, path = configuration
    config['api_key'] = 'synthetic-secret-that-must-never-render'
    path.write_text(json.dumps(config), encoding='utf-8')
    with pytest.raises(RenderError, match='private_value_in_public_configuration'):
        rendered(tmp_path, path)
    assert not (tmp_path / 'rendered').exists()
    config.pop('api_key')
    path.write_text(json.dumps(config), encoding='utf-8')
    with pytest.raises(RenderError, match='unbounded_control_network'):
        render(config_path=path, control_image='registry:5000/control@sha256:' + 'b' * 64,
            output_dir=tmp_path / 'rendered', kubernetes_api_cidrs=['0.0.0.0/0'])
