"""Trusted Kubernetes pool routing remains offline to inspect and explicit to run."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_service import __main__ as cli
from agent_service.config import ConfigurationError, load_configuration


CALLER_TOKEN = 'offline-caller-token-00000000000000000'
POOL_TOKEN = 'offline-pool-master-token-000000000000'
EXTERNAL_TOKEN = 'offline-external-token-00000000000000'
RECEIPT = b'offline-pool-master-receipt-key-000000'
DATABASE = 'postgresql+psycopg://agent:private-database-password@invalid.internal/agent'
IMAGE_DIGEST = 'sha256:' + 'a' * 64


def public():
    return {'schema_version': 1,
        'callers': [{'tenant': 'internal', 'caller': 'ppt', 'profiles': ['workspace-task.v1'], 'token_ref': 'ppt'}],
        'profiles': [{'name': 'workspace-task.v1', 'revision': 'v1', 'engine': 'pi',
                      'runtime_revision': 'r1', 'image_digest': IMAGE_DIGEST}],
        'kubernetes_workers': [{'pool_id': 'pi-workspace', 'engine': 'pi', 'namespace': 'agent-workers',
            'image': 'registry.internal:5000/agent-worker-pi@' + IMAGE_DIGEST,
            'host_config_map': 'agent-pi-host', 'pod_cidrs': ['10.244.0.0/16'],
            'token_ref': 'pi-pool', 'receipt_key_ref': 'pi-pool'}]}


def private():
    return {'database_url': DATABASE,
        'tokens': {'ppt': CALLER_TOKEN, 'pi-pool': POOL_TOKEN, 'external': EXTERNAL_TOKEN},
        'receipt_keys': {'pi-pool': base64.b64encode(RECEIPT).decode()}}


def files(tmp_path, cfg=None, secret=None):
    cfg_path, secret_path = tmp_path / 'public.json', tmp_path / 'private.json'
    cfg_path.write_text(json.dumps(public() if cfg is None else cfg), encoding='utf-8')
    secret_path.write_text(json.dumps(private() if secret is None else secret), encoding='utf-8')
    return cfg_path, secret_path


def test_pool_loads_reuses_fixed_profile_and_defaults_to_disabled(tmp_path):
    loaded = load_configuration(*files(tmp_path))
    assert loaded.worker_endpoints == ()
    assert len(loaded.kubernetes_pools) == 1
    pool = loaded.kubernetes_pools[0]
    assert pool.policy is loaded.registry.policy('workspace-task.v1', 'v1')
    assert pool.policy.enabled is pool.enabled is False
    assert pool.pod_cidrs == ('10.244.0.0/16',) and pool.port == 8090
    assert pool.uid_start is None and pool.uid_count == 65536
    assert loaded.kubernetes_credentials[0].pool_id == pool.pool_id
    assert loaded.kubernetes_credentials[0].receipt_key == RECEIPT
    assert loaded.summary()['worker_engines'] == ['pi']
    assert loaded.summary()['worker_provisioning'] == 'kubernetes_per_attempt'
    for value in (loaded, loaded.kubernetes_credentials, loaded.api_settings):
        assert POOL_TOKEN not in repr(value) and CALLER_TOKEN not in repr(value)
        assert RECEIPT.decode() not in repr(value) and 'private-database-password' not in repr(value)


def test_mixed_backend_declares_both_engines_without_implicit_fallback(tmp_path):
    cfg = public()
    cfg['workers'] = [{'engine': 'hermes', 'base_url': 'http://hermes.internal:8092',
                       'token_ref': 'external', 'receipt_key_ref': 'pi-pool'}]
    loaded = load_configuration(*files(tmp_path, cfg))
    assert loaded.summary()['worker_provisioning'] == 'mixed'
    assert set(loaded.summary()['worker_engines']) == {'pi', 'hermes'}


def test_private_ipv6_ula_pod_network_is_explicitly_supported(tmp_path):
    cfg = public()
    cfg['kubernetes_workers'][0]['pod_cidrs'] = ['10.244.0.0/16', 'fd42:1234::/64']
    loaded = load_configuration(*files(tmp_path, cfg))
    assert loaded.kubernetes_pools[0].pod_cidrs == ('10.244.0.0/16', 'fd42:1234::/64')


def test_enabling_pool_requires_explicit_reserved_uid_range(tmp_path):
    cfg = public()
    cfg['kubernetes_workers'][0]['enabled'] = True
    with pytest.raises(ConfigurationError):
        load_configuration(*files(tmp_path, cfg))
    cfg['kubernetes_workers'][0].update(uid_start=1000000, uid_count=65536)
    loaded = load_configuration(*files(tmp_path, cfg))
    assert loaded.kubernetes_pools[0].uid_start == 1000000
    assert loaded.kubernetes_pools[0].uid_count == 65536


@pytest.mark.parametrize('start,count', [(65536, 1), (1000000, 1000000), (2147483647, 1)])
def test_explicit_uid_range_accepts_its_bounded_edges(tmp_path, start, count):
    cfg = public()
    cfg['kubernetes_workers'][0].update(enabled=True, uid_start=start, uid_count=count)
    loaded = load_configuration(*files(tmp_path, cfg))
    assert loaded.kubernetes_pools[0].uid_start == start
    assert loaded.kubernetes_pools[0].uid_count == count


@pytest.mark.parametrize('start,count', [
    (65535, 65536), (2147483648, 1), (2147483647, 2), (2147483640, 10),
    (1000000, 0), (1000000, 1000001), (True, 1), (1000000, True),
])
def test_unbounded_or_unreserved_uid_allocation_is_rejected(tmp_path, start, count):
    cfg = public()
    cfg['kubernetes_workers'][0].update(enabled=True, uid_start=start, uid_count=count)
    with pytest.raises(ConfigurationError):
        load_configuration(*files(tmp_path, cfg))


@pytest.mark.parametrize('mutate', [
    lambda cfg: cfg['kubernetes_workers'].append(deepcopy(cfg['kubernetes_workers'][0])),
    lambda cfg: cfg.update(workers=[{'engine': 'pi', 'base_url': 'http://worker.internal',
                                     'token_ref': 'external', 'receipt_key_ref': 'pi-pool'}]),
    lambda cfg: cfg['profiles'].append(dict(cfg['profiles'][0], revision='v2')),
    lambda cfg: cfg.update(profiles=[]),
    lambda cfg: cfg['profiles'][0].update(engine='hermes'),
    lambda cfg: cfg['profiles'][0].update(image_digest='sha256:' + 'b' * 64),
    lambda cfg: cfg['kubernetes_workers'][0].update(image='registry.internal/agent-worker:latest'),
    lambda cfg: cfg['kubernetes_workers'][0].update(image='https://registry.internal/agent@' + IMAGE_DIGEST),
    lambda cfg: cfg['kubernetes_workers'][0].update(image='registry.internal:99999/agent@' + IMAGE_DIGEST),
    lambda cfg: cfg['kubernetes_workers'][0].update(image='registry.internal/user/agent@' + IMAGE_DIGEST + '/path'),
    lambda cfg: cfg['kubernetes_workers'][0].update(pool_id='../other'),
    lambda cfg: cfg['kubernetes_workers'][0].update(namespace='OtherNamespace'),
    lambda cfg: cfg['kubernetes_workers'][0].update(host_config_map='host/other'),
    lambda cfg: cfg['kubernetes_workers'][0].update(host_config_map='host..other'),
    lambda cfg: cfg['kubernetes_workers'][0].update(enabled='true'),
    lambda cfg: cfg['kubernetes_workers'][0].update(port=True),
    lambda cfg: cfg['kubernetes_workers'][0].update(port=0),
    lambda cfg: cfg['kubernetes_workers'][0].update(port=8080),
    lambda cfg: cfg['kubernetes_workers'][0].update(port=8091),
    lambda cfg: cfg['kubernetes_workers'][0].update(provider_key='not-public'),
])
def test_ambiguous_unpinned_or_untrusted_pool_configuration_is_rejected(tmp_path, mutate):
    cfg = public()
    mutate(cfg)
    with pytest.raises(ConfigurationError):
        load_configuration(*files(tmp_path, cfg))


@pytest.mark.parametrize('networks', [
    [], ['0.0.0.0/0'], ['::/0'], ['127.0.0.0/8'], ['169.254.0.0/16'],
    ['224.0.0.0/4'], ['8.8.8.0/24'], ['2001:4860::/32'], ['::1/128'], ['fe80::/10'],
    ['10.244.0.1/16'], ['not-a-cidr'], ['10.244.0.0/16', '10.244.0.0/16'],
    ['fd42::/64', 'fd42:0:0:0::/64'], '10.244.0.0/16',
])
def test_pod_networks_are_private_fixed_networks_without_unbounded_or_local_ranges(tmp_path, networks):
    cfg = public()
    cfg['kubernetes_workers'][0]['pod_cidrs'] = networks
    with pytest.raises(ConfigurationError):
        load_configuration(*files(tmp_path, cfg))


def test_missing_master_secret_has_fixed_error_without_value_echo(tmp_path):
    secret = private()
    secret['tokens'].pop('pi-pool')
    with pytest.raises(ConfigurationError) as error:
        load_configuration(*files(tmp_path, secret=secret))
    assert error.value.code == 'required_secret_missing'
    assert POOL_TOKEN not in str(error.value)


def test_two_kubernetes_engines_cannot_share_a_pool_id(tmp_path):
    cfg = public()
    cfg['profiles'].append(dict(cfg['profiles'][0], engine='hermes', revision='v2'))
    cfg['kubernetes_workers'].append(dict(cfg['kubernetes_workers'][0], engine='hermes'))
    with pytest.raises(ConfigurationError):
        load_configuration(*files(tmp_path, cfg))


def test_check_is_offline_and_emits_only_public_summary(tmp_path, monkeypatch, capsys):
    from agent_service import kubernetes_client, kubernetes_provider, store
    def forbidden(*_args, **_kwargs):
        raise AssertionError('check must not construct a database, client or Worker')
    monkeypatch.setattr(kubernetes_client.KubernetesClient, 'from_service_account', forbidden)
    monkeypatch.setattr(kubernetes_provider, 'KubernetesWorkerAdapter', forbidden)
    monkeypatch.setattr(store, 'Store', forbidden)
    enabled = public()
    enabled['profiles'][0]['enabled'] = True
    enabled['kubernetes_workers'][0].update(enabled=True, uid_start=1000000)
    cfg, secret = files(tmp_path, enabled)
    assert cli.main(['check', '--config', str(cfg), '--secrets', str(secret)]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary['worker_provisioning'] == 'kubernetes_per_attempt'
    assert summary['model_requests'] == 0
    assert captured.err == '' and POOL_TOKEN not in captured.out
    assert 'private-database-password' not in captured.out


def test_runtime_requires_store_and_constructs_explicit_kubernetes_adapter(tmp_path, monkeypatch):
    from agent_service import kubernetes_client, kubernetes_provider
    loaded = load_configuration(*files(tmp_path))
    observed = {}
    class Kube:
        def close(self):
            observed['client_closed'] = True
    kube, store = Kube(), object()
    def make_client():
        observed['client_constructed'] = True
        return kube
    class Adapter:
        def __init__(self, received_store, pool, received_kube, token, key):
            assert received_store is store and received_kube is kube
            assert token == POOL_TOKEN and key == RECEIPT
            assert pool is loaded.kubernetes_pools[0]
        def close(self):
            observed['adapter_closed'] = True
    monkeypatch.setattr(kubernetes_client.KubernetesClient, 'from_service_account', make_client)
    monkeypatch.setattr(kubernetes_provider, 'KubernetesWorkerAdapter', Adapter)
    with pytest.raises(ConfigurationError, match='worker_configuration_unavailable'):
        loaded.build_adapters()
    assert observed == {}
    adapters = loaded.build_adapters(store)
    assert set(adapters) == {'pi'} and observed['client_constructed'] is True
    adapters['pi'].close()


def test_failed_adapter_assembly_closes_new_client_and_existing_adapter(tmp_path, monkeypatch):
    from agent_service import kubernetes_client, kubernetes_provider, transport
    cfg = public()
    cfg['workers'] = [{'engine': 'hermes', 'base_url': 'http://hermes.internal',
                      'token_ref': 'external', 'receipt_key_ref': 'pi-pool'}]
    loaded = load_configuration(*files(tmp_path, cfg))
    closed = []
    class Existing:
        def __init__(self, *_):
            pass
        def close(self):
            closed.append('external')
    class Kube:
        def close(self):
            closed.append('kube')
    def unavailable(*_args):
        raise RuntimeError('private provider URL ' + POOL_TOKEN)
    monkeypatch.setattr(transport, 'RemoteWorkerAdapter', Existing)
    monkeypatch.setattr(kubernetes_client.KubernetesClient, 'from_service_account', Kube)
    monkeypatch.setattr(kubernetes_provider, 'KubernetesWorkerAdapter', unavailable)
    with pytest.raises(ConfigurationError) as error:
        loaded.build_adapters(object())
    assert error.value.code == 'worker_configuration_unavailable'
    assert sorted(closed) == ['external', 'kube']
    assert POOL_TOKEN not in str(error.value)


def test_checked_in_kubernetes_example_is_disabled_and_has_no_private_credentials(tmp_path):
    example = Path(__file__).parents[2] / 'agent_service' / 'config.kubernetes.example.json'
    secret = private()
    secret['tokens']['ppt-service'] = secret['tokens'].pop('ppt')
    secret['tokens']['pi-worker-pool'] = secret['tokens'].pop('pi-pool')
    secret['receipt_keys']['pi-worker-pool'] = secret['receipt_keys'].pop('pi-pool')
    _, secret_path = files(tmp_path, secret=secret)
    loaded = load_configuration(example, secret_path)
    assert loaded.kubernetes_pools[0].enabled is False
    assert loaded.kubernetes_pools[0].policy.enabled is False
    assert loaded.kubernetes_pools[0].uid_start is None
    wire = json.loads(example.read_text(encoding='utf-8'))
    assert 'tokens' not in wire and 'database_url' not in wire
