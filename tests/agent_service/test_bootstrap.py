"""Credential/database bootstrap contracts using in-memory administrative mocks."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_service.deploy import bootstrap as module


def runtime():
    return dict(DATABASE_URL='postgresql+psycopg://agent_service:private-db-password@postgres.data.svc.cluster.local:5432/agent_service',
        **{key: 'private-' + key + '-token-' + 'x' * 40 for key in module.TOKEN_KEYS},
        **{key: base64.b64encode(bytes([index + 1]) * 32).decode()
           for index, key in enumerate(module.SIGNING_KEYS)})


class ClusterFixture:
    def __init__(self):
        self.records = {module.CALLERS_PATH: {'LLM_TOKEN_EXISTING_SERVICE': 'existing-private-token-' + 'x' * 40}}
        self.versions = {module.CALLERS_PATH: 1}
        self.writes, self.statements, self.authenticated = [], [], []
        self.role_exists = self.database_exists = False
        self.unsafe = False
        self.conflict = None

    def vault(self, command, **_):
        if command == ['token', 'lookup', '-field=ttl']:
            return b'1000'
        if command[-1] == 'auth/kubernetes/role/eso-role':
            return json.dumps({'data': {'token_policies': ['eso-policy']}}).encode()
        if command[-1] == 'sys/policies/acl/eso-policy':
            return json.dumps({'data': {'policy': 'path "secret/data/*" { capabilities = ["read"] }'}}).encode()
        raise AssertionError('Unexpected fixed Vault command')

    def kv(self, path):
        return deepcopy(self.records.get(path, {})), self.versions.get(path, 0)

    def cas(self, path, version, values):
        self.writes.append((path, version, deepcopy(values)))
        if self.conflict == path:
            self.conflict = None
            self.records[path] = {**deepcopy(values), 'CONCURRENT_OPERATOR_FIELD': 'preserved-private-field'}
            self.versions[path] = version + 1
            raise module.BootstrapError('vault_cas_conflict')
        if self.versions.get(path, 0) != version:
            raise module.BootstrapError('vault_cas_conflict')
        self.records[path], self.versions[path] = deepcopy(values), version + 1

    def sql(self, sql, *, database='postgres'):
        self.statements.append((database, sql))
        if sql == module.DATABASE_INSPECTION:
            return json.dumps(dict(role_exists=self.role_exists,
                role_safe=self.role_exists and not self.unsafe, database_exists=self.database_exists,
                database_owned=self.database_exists and self.role_exists,
                membership_count=int(self.unsafe), other_database_ownerships=0,
                other_database_explicit_grants=0, other_database_connect_count=2 if self.role_exists else 0)).encode()
        if sql == module.DATABASE_LIST:
            return b'["postgres","appdb"]'
        if sql == module.CROSS_DATABASE_INSPECTION:
            return b'{"unsafe_objects":0}'
        if sql.startswith('SELECT pg_advisory_lock'):
            self.role_exists = self.database_exists = True
            return b''
        if sql.startswith('REVOKE ALL ON SCHEMA public'):
            assert database == module.DB_NAME
            return b''
        raise AssertionError('Unexpected administrative SQL')

    def authenticate_database(self, password):
        self.authenticated.append(password)


def test_inspect_is_readonly_and_never_generates_or_outputs_credentials(monkeypatch):
    cluster = ClusterFixture()
    def forbidden(*_, **__):
        raise AssertionError('Read-only inspection must not generate credentials')
    monkeypatch.setattr(module.secrets, 'token_urlsafe', forbidden)
    monkeypatch.setattr(module.secrets, 'token_bytes', forbidden)
    result = module.bootstrap(cluster)
    assert result['mode'] == 'inspect' and result['ready'] is False
    assert cluster.writes == [] and cluster.authenticated == []
    assert not any('CREATE ' in statement or 'REVOKE ' in statement for _, statement in cluster.statements)
    assert 'existing-private-token' not in json.dumps(result)
    assert result['eso']['read_coverage'] == 'confirmed'


def test_apply_preserves_shared_fields_and_is_repeatable_without_rotation():
    cluster = ClusterFixture()
    result = module.bootstrap(cluster, apply=True)
    assert result['ready'] is True and result['database']['runtime_login_verified'] is True
    assert result['runtime']['action'] == result['model_caller']['action'] == 'created'
    original_runtime = deepcopy(cluster.records[module.RUNTIME_PATH])
    original_callers = deepcopy(cluster.records[module.CALLERS_PATH])
    assert original_callers['LLM_TOKEN_EXISTING_SERVICE'].startswith('existing-private-token')
    assert len(set(original_runtime[key] for key in module.TOKEN_KEYS)) == 4
    assert len(set(original_runtime[key] for key in module.SIGNING_KEYS)) == 3
    text = json.dumps(result)
    assert all(value not in text for value in [*original_runtime.values(), *original_callers.values()])
    repeated = module.bootstrap(cluster, apply=True)
    assert repeated['runtime']['action'] == repeated['model_caller']['action'] == 'preserved'
    assert cluster.records[module.RUNTIME_PATH] == original_runtime
    assert cluster.records[module.CALLERS_PATH] == original_callers and len(cluster.writes) == 2
    assert all('ALTER ROLE' not in sql and 'DROP ' not in sql for _, sql in cluster.statements)
    creation = next(sql for _, sql in cluster.statements if sql.startswith('SELECT pg_advisory_lock'))
    assert 'NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS' in creation


def test_cas_conflict_rereads_winner_without_clobbering_concurrent_fields():
    cluster = ClusterFixture()
    cluster.conflict = module.RUNTIME_PATH
    saved, action = module.ensure_runtime(cluster, 'postgres.data.svc.cluster.local')
    assert action == 'preserved' and saved['CONCURRENT_OPERATOR_FIELD'] == 'preserved-private-field'
    assert saved == cluster.records[module.RUNTIME_PATH] and len(cluster.writes) == 1
    cluster.conflict = module.CALLERS_PATH
    assert module.ensure_model_caller(cluster) == 'preserved'
    assert cluster.records[module.CALLERS_PATH]['CONCURRENT_OPERATOR_FIELD'] == 'preserved-private-field'
    assert 'LLM_TOKEN_EXISTING_SERVICE' in cluster.records[module.CALLERS_PATH]


def test_existing_role_requires_known_runtime_and_unsafe_role_never_gets_adopted():
    cluster = ClusterFixture()
    cluster.role_exists = True
    with pytest.raises(module.BootstrapError, match='existing_database_role_requires_runtime_url'):
        module.bootstrap(cluster, apply=True)
    assert cluster.writes == []
    cluster.records[module.RUNTIME_PATH] = runtime()
    cluster.unsafe = True
    with pytest.raises(module.BootstrapError, match='existing_database_scope_requires_review'):
        module.bootstrap(cluster, apply=True)
    assert cluster.writes == []
    assert all('CREATE ROLE' not in sql for _, sql in cluster.statements)


def test_existing_values_are_preserved_and_invalid_values_are_never_silently_replaced():
    cluster = ClusterFixture()
    original = runtime()
    original['OPERATOR_EXTRA'] = 'private-operator-value'
    cluster.records[module.RUNTIME_PATH] = deepcopy(original)
    saved, action = module.ensure_runtime(cluster, 'postgres.data.svc.cluster.local')
    assert saved == original and action == 'preserved' and cluster.writes == []
    cluster.records[module.RUNTIME_PATH]['PPT_SERVICE_TOKEN'] = 'too-short'
    with pytest.raises(module.BootstrapError, match='existing_runtime_token_invalid'):
        module.ensure_runtime(cluster, 'postgres.data.svc.cluster.local')
    assert cluster.writes == []


def test_secret_payloads_use_stdin_and_never_appear_in_command_arguments():
    calls = []
    def runner(arguments, **options):
        calls.append((arguments, options['input']))
        return SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')
    cluster = module.Cluster(runner=runner)
    cluster.cas(module.RUNTIME_PATH, 7, {'private-field': 'private-credential-value'})
    arguments, payload = calls[0]
    assert 'private-credential-value' not in ' '.join(arguments)
    assert json.loads(payload) == {'private-field': 'private-credential-value'}
    assert '-cas=7' in arguments and arguments[-1] == '-'
    assert '--kubeconfig' in arguments and '/etc/kubernetes/super-admin.conf' in arguments


def test_cli_never_prints_arbitrary_sql_url_or_subprocess_exception(monkeypatch, capsys):
    def unavailable(*_, **__):
        raise RuntimeError('SQL PASSWORD private-leaked-value postgresql://private@host/db')
    monkeypatch.setattr(module, 'bootstrap', unavailable)
    assert module.main([]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {'status': 'error', 'code': 'bootstrap_unavailable'}
    assert output.err == '' and 'private' not in output.out


@pytest.mark.parametrize('stdout,stderr', [
    (b'No value found at secret/data/agent-service/runtime\n',
     b'command terminated with exit code 2\n'),
    (b'', b'No value found at secret/data/agent-service/runtime\n'
           b'command terminated with exit code 2\n'),
])
def test_missing_kv_read_accepts_exact_stdout_or_stderr_diagnostic(stdout, stderr):
    cluster = module.Cluster(runner=lambda *_, **__: SimpleNamespace(
        returncode=2, stdout=stdout, stderr=stderr))
    assert cluster.kv(module.RUNTIME_PATH) == ({}, 0)


@pytest.mark.parametrize('command,returncode,stdout', [
    (['kv', 'put', '-format=json', module.RUNTIME_PATH, '-'], 2,
        b'No value found at secret/data/agent-service/runtime\n'),
    (['kv', 'get', '-format=json', module.RUNTIME_PATH], 1,
        b'No value found at secret/data/agent-service/runtime\n'),
    (['kv', 'get', '-format=json', module.RUNTIME_PATH], 2,
        b'No value found at secret/data/other-service/runtime\n'),
    (['kv', 'get', '-format=json', module.RUNTIME_PATH], 2,
        b'prefix No value found at secret/data/agent-service/runtime\n'),
    (['kv', 'get', '-format=json', module.RUNTIME_PATH], 2,
        b'No value found at secret/data/agent-service/runtime\nprivate-payload\n'),
])
def test_missing_diagnostic_never_masks_other_commands_or_arbitrary_output(command, returncode, stdout):
    cluster = module.Cluster(runner=lambda *_, **__: SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=b''))
    with pytest.raises(module.BootstrapError, match='vault_operation_unavailable'):
        cluster.vault(command, missing=True)


@pytest.mark.parametrize('policy,coverage', [
    ('# path "secret/data/*" { capabilities = ["read"] }', 'not_proven'),
    ('path "secret/data/*" { capabilities = [ # "read"\n "update"] }', 'not_proven'),
    ('path "secret/data/*" { capabilities = ["read"] }\n'
     'path "secret/data/agent-service/runtime" { capabilities = ["deny"] }', 'denied'),
    ('path "secret/data/*" { capabilities = ["read"] }\n'
     'path "secret/data/agent-service/runtime" { capabilities = ["update"] }', 'not_proven'),
    ('path "secret/data/*" { capabilities = ["read"] }\n'
     'path "secret/data/agent-service/runtime" { capabilities = ["deny"] '
     'required_parameters = ["example"] }', 'not_proven'),
    ('/* administrator comment */ path "secret/data/*" { capabilities = ["read",] }', 'confirmed'),
])
def test_eso_policy_analysis_never_credits_comments_or_unknown_precedence(policy, coverage):
    cluster = ClusterFixture()
    original = cluster.vault
    def vault(command, **options):
        if command[-1] == 'sys/policies/acl/eso-policy':
            return json.dumps({'data': {'policy': policy}}).encode()
        return original(command, **options)
    cluster.vault = vault
    result = module.inspect_eso(cluster)
    assert result['read_coverage'] == coverage and result['policy_changes'] is False
    assert cluster.writes == []
