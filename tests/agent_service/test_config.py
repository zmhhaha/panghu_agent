"""Offline startup configuration tests; no model or remote database is used."""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent_service import __main__ as cli
from agent_service.config import ConfigurationError, load_configuration
from agent_service.contracts import JobRequest, ServiceError
from registry_fixtures import make_catalog


DATABASE_SENTINEL = 'postgresql+psycopg://agent:private-database-password@db.invalid/agent'
TOKEN_SENTINEL = 'synthetic-service-token-for-offline-test-only'
WORKER_TOKEN = 'synthetic-worker-token-for-offline-test-only'
RECEIPT = base64.b64encode(b'synthetic-receipt-key-32-bytes-long').decode('ascii')


def public():
    return {**make_catalog(),
            'callers': [{'tenant': 'internal', 'caller': 'ppt-service',
                         'profiles': ['workspace-task.v1'], 'token_ref': 'ppt-service'}],
            'workers': [{'engine': 'pi', 'base_url': 'http://127.0.0.1:8091',
                         'token_ref': 'pi-worker', 'receipt_key_ref': 'pi-worker'}]}


def secrets(database_url=DATABASE_SENTINEL):
    return {'database_url': database_url,
            'tokens': {'ppt-service': TOKEN_SENTINEL, 'pi-worker': WORKER_TOKEN},
            'receipt_keys': {'pi-worker': RECEIPT}}


def files(tmp_path, cfg=None, private=None):
    config_path, secrets_path = tmp_path / 'public.json', tmp_path / 'private.json'
    config_path.write_text(json.dumps(public() if cfg is None else cfg), encoding='utf-8')
    secrets_path.write_text(json.dumps(secrets() if private is None else private), encoding='utf-8')
    return config_path, secrets_path


def test_loading_is_offline_and_defaults_execution_to_disabled(tmp_path, monkeypatch):
    import agent_service.store
    import agent_service.transport
    def forbidden(*args, **kwargs):
        raise AssertionError('Configuration checking must not construct runtime resources')
    monkeypatch.setattr(agent_service.store, 'Store', forbidden)
    monkeypatch.setattr(agent_service.transport, 'RemoteWorkerAdapter', forbidden)
    config = load_configuration(*files(tmp_path))
    policy = config.registry.policy('workspace-task.v1', 'v1')
    assert policy.enabled is False and policy.engine == 'pi'
    assert config.poll_seconds == 1.0 and config.lease_seconds == 60
    assert config.database_kind == 'postgresql'
    assert config.worker_endpoints[0].receipt_key == base64.b64decode(RECEIPT)
    assert TOKEN_SENTINEL not in repr(config) and 'private-database-password' not in repr(config)
    assert TOKEN_SENTINEL not in repr(config.api_settings)
    principal = config.api_settings.principals[TOKEN_SENTINEL]
    task = JobRequest(profile='workspace-task.v1', profile_revision='v1', business_ref='one',
                      task={'prompt': 'trusted text only'})
    with pytest.raises(ServiceError, match='profile_not_ready'):
        config.registry.resolve(principal, task)


def test_only_reviewed_validator_and_static_engine_route_are_loaded(tmp_path):
    cfg = public()
    cfg.update(make_catalog('hermes', enabled=True))
    loaded = load_configuration(*files(tmp_path, cfg))
    principal = loaded.api_settings.principals[TOKEN_SENTINEL]
    request = JobRequest(profile='workspace-task.v1', profile_revision='v1', business_ref='one',
                         task={'prompt': 'hello', 'executable': '/bin/sh'})
    with pytest.raises(ServiceError, match='invalid_workspace_task'):
        loaded.registry.resolve(principal, request)
    assert loaded.registry.policy('workspace-task.v1', 'v1').engine == 'hermes'


@pytest.mark.parametrize('mutate', [
    lambda cfg: cfg.update(schema_version=True),
    lambda cfg: cfg.update(executable='arbitrary.module:run'),
    lambda cfg: cfg['task_contracts'][0].update(validator='arbitrary.module:run'),
    lambda cfg: cfg['task_contracts'][0].update(name='unreviewed-profile'),
    lambda cfg: cfg['routes'][0].update(enabled='true'),
    lambda cfg: cfg['execution_bindings'][0].update(engine='dsh'),
    lambda cfg: cfg['tool_environments'][0].update(image_digest='mutable:latest'),
    lambda cfg: cfg.update(dispatcher={'poll_seconds': 6.0}),
    lambda cfg: cfg.update(dispatcher={'lease_seconds': True}),
    lambda cfg: cfg['workers'].append(copy.deepcopy(cfg['workers'][0])),
    lambda cfg: cfg['task_contracts'].append(copy.deepcopy(cfg['task_contracts'][0])),
    lambda cfg: cfg['callers'].append(copy.deepcopy(cfg['callers'][0])),
    lambda cfg: cfg['workers'][0].update(base_url='https://user:secret@worker.internal'),
    lambda cfg: cfg['workers'][0].update(base_url='https://worker.internal/other'),
    lambda cfg: cfg['workers'][0].update(base_url='http://worker.internal\n'),
])
def test_invalid_public_execution_or_route_configuration_is_rejected(tmp_path, mutate):
    cfg = public()
    mutate(cfg)
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, cfg))


@pytest.mark.parametrize('mutate,code', [
    (lambda value: value.pop('database_url'), 'required_secret_missing'),
    (lambda value: value['tokens'].pop('ppt-service'), 'required_secret_missing'),
    (lambda value: value['receipt_keys'].pop('pi-worker'), 'required_secret_missing'),
    (lambda value: value['tokens'].update({'ppt-service': 'short'}), 'secrets_invalid'),
    (lambda value: value['tokens'].update({'pi-worker': TOKEN_SENTINEL}), 'secrets_invalid'),
    (lambda value: value['receipt_keys'].update({'pi-worker': 'not base64'}), 'secrets_invalid'),
    (lambda value: value['receipt_keys'].update({'pi-worker': base64.b64encode(b'short').decode()}), 'secrets_invalid'),
    (lambda value: value.update(provider_key='not-an-agent-service-secret'), 'secrets_invalid'),
    (lambda value: value.update(database_url='mysql://user:private-password@db/agent'), 'database_configuration_invalid'),
])
def test_missing_or_invalid_secrets_have_fixed_errors_without_value_echo(tmp_path, mutate, code):
    value = secrets()
    mutate(value)
    with pytest.raises(ConfigurationError) as error:
        load_configuration(*files(tmp_path, private=value))
    assert error.value.code == code
    assert 'private-password' not in str(error.value) and TOKEN_SENTINEL not in str(error.value)


def test_duplicate_keys_nonfinite_json_and_oversize_files_rejected(tmp_path):
    config_path, private_path = files(tmp_path)
    config_path.write_text('{"schema_version":2,"schema_version":2}', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(config_path, private_path)
    config_path, private_path = files(tmp_path)
    private_path.write_text('{"database_url":"private","tokens":{},"tokens":{}}', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='secrets_invalid'):
        load_configuration(config_path, private_path)
    config_path, private_path = files(tmp_path)
    config_path.write_bytes(b'x' * 65537)
    with pytest.raises(ConfigurationError, match='configuration_file_unavailable'):
        load_configuration(config_path, private_path)
    config_path, private_path = files(tmp_path)
    private_path.write_bytes(b'x' * 131073)
    with pytest.raises(ConfigurationError, match='secrets_file_unavailable'):
        load_configuration(config_path, private_path)
    config_path, private_path = files(tmp_path)
    config_path.write_text('{"schema_version":NaN}', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(config_path, private_path)


def test_hardlinked_configuration_is_not_treated_as_ordinary_file(tmp_path):
    config_path, private_path = files(tmp_path)
    linked = tmp_path / 'linked-public.json'
    os.link(config_path, linked)
    with pytest.raises(ConfigurationError, match='configuration_file_unavailable'):
        load_configuration(linked, private_path)


def test_symlink_configuration_is_rejected_where_supported(tmp_path):
    config_path, private_path = files(tmp_path)
    linked = tmp_path / 'linked-public.json'
    try:
        linked.symlink_to(config_path)
    except OSError:
        pytest.skip('This platform does not grant creation of symbolic links')
    with pytest.raises(ConfigurationError, match='configuration_file_unavailable'):
        load_configuration(linked, private_path)


def test_sqlite_requires_explicit_development_authorization_and_file_database(tmp_path):
    paths = files(tmp_path, private=secrets('sqlite:///' + str(tmp_path / 'development.db')))
    with pytest.raises(ConfigurationError, match='postgresql_required'):
        load_configuration(*paths)
    config = load_configuration(*paths, allow_sqlite=True)
    assert config.database_kind == 'sqlite-development'
    assert not (tmp_path / 'development.db').exists()
    paths = files(tmp_path, private=secrets('sqlite:///:memory:'))
    with pytest.raises(ConfigurationError, match='database_configuration_invalid'):
        load_configuration(*paths, allow_sqlite=True)


def test_postgresql_uses_the_pinned_psycopg_driver_without_connecting(tmp_path):
    config = load_configuration(*files(tmp_path, private=secrets('postgresql://user:private@db.invalid/agent')))
    assert config.database_url.startswith('postgresql+psycopg://')
    assert 'private' not in repr(config)


def test_cli_check_never_constructs_store_or_worker_and_does_not_print_secrets(tmp_path, monkeypatch, capsys):
    import agent_service.store
    import agent_service.transport
    def forbidden(*args, **kwargs):
        raise AssertionError('check must be offline')
    monkeypatch.setattr(agent_service.store, 'Store', forbidden)
    monkeypatch.setattr(agent_service.transport, 'RemoteWorkerAdapter', forbidden)
    cfg, private = files(tmp_path)
    assert cli.main(['--config', str(cfg), '--secrets', str(private), 'check']) == 0
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert summary['model_requests'] == 0 and summary['status'] == 'configuration_valid'
    assert summary['worker_provisioning'] == 'external_single_capsule'
    assert TOKEN_SENTINEL not in output.out + output.err
    assert 'private-database-password' not in output.out + output.err


def test_cli_missing_secrets_are_fixed_and_do_not_dump_json_or_tracebacks(tmp_path, capsys):
    cfg, _ = files(tmp_path)
    assert cli.main(['check', '--config', str(cfg), '--secrets', str(tmp_path / 'missing.json')]) == 1
    result = capsys.readouterr()
    assert result.out == ''
    assert json.loads(result.err) == {'status': 'error', 'code': 'secrets_file_unavailable'}
    assert 'Traceback' not in result.err


def test_explicit_init_then_empty_dispatch_tick_uses_real_local_store(tmp_path, capsys):
    value = public()
    value['workers'] = []
    database = tmp_path / 'development.db'
    cfg, private = files(tmp_path, value, secrets('sqlite:///' + str(database)))
    args = ['--config', str(cfg), '--secrets', str(private), '--allow-sqlite']
    assert cli.main(args + ['check']) == 0
    assert not database.exists()
    assert cli.main(args + ['init-db']) == 0
    assert database.exists()
    assert cli.main(args + ['dispatch', '--once']) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert json.loads(lines[-1]) == {'status': 'dispatch_tick_complete', 'state': 'idle'}


def test_api_command_does_not_initialize_database_or_start_an_agent(tmp_path, monkeypatch):
    import agent_service.store
    cfg = public()
    cfg['workers'] = []
    database = tmp_path / 'not-initialized.db'
    paths = files(tmp_path, cfg, secrets('sqlite:///' + str(database)))
    def forbidden(*args, **kwargs):
        raise AssertionError('api must not initialize database')
    monkeypatch.setattr(agent_service.store.Store, 'initialize', forbidden)
    observed = {}
    def run(app, **kwargs):
        observed.update(kwargs)
        assert app.state.store is not None
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(run=run))
    assert cli.main(['api', '--config', str(paths[0]), '--secrets', str(paths[1]), '--allow-sqlite']) == 0
    assert observed['host'] == '127.0.0.1'
    assert observed['access_log'] is observed['proxy_headers'] is False
    assert not database.exists()


def test_cli_runtime_exception_and_server_logging_do_not_expose_raw_diagnostics(tmp_path, monkeypatch, capsys):
    from agent_service.config import ServiceConfiguration
    cfg, private = files(tmp_path)
    def fail(self):
        raise RuntimeError('private provider URL and token ' + TOKEN_SENTINEL)
    monkeypatch.setattr(ServiceConfiguration, 'build_store', fail)
    assert cli.main(['init-db', '--config', str(cfg), '--secrets', str(private)]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)['code'] == 'runtime_unavailable'
    assert TOKEN_SENTINEL not in output.err
    record = logging.LogRecord('uvicorn.error', logging.ERROR, __file__, 1,
                               'private raw %s', (TOKEN_SENTINEL,), None)
    record.exc_info = (RuntimeError, RuntimeError(TOKEN_SENTINEL), None)
    assert cli._FixedErrorLog().filter(record) is True
    assert record.getMessage() == 'agent_service_runtime_error' and record.exc_info is None


def test_checked_in_example_contains_public_references_only(tmp_path):
    example = Path(__file__).parents[2] / 'agent_service' / 'config.example.json'
    example_wire = json.loads(example.read_text(encoding='utf-8'))
    value = secrets()
    value['tokens']['hermes-worker'] = 'synthetic-hermes-worker-token-for-offline-test-only'
    value['receipt_keys']['hermes-worker'] = RECEIPT
    _, private = files(tmp_path, private=value)
    loaded = load_configuration(example, private)
    assert len(loaded.worker_endpoints) == 2
    assert loaded.registry.policy('workspace-task.v1', 'v1').enabled is False
    assert 'database_url' not in example_wire and 'tokens' not in example_wire
