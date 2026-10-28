"""Isolated transfer safety contracts; no database, cluster or credentials used."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


HELPER = Path(__file__).resolve().parents[2] / 'deploy/k8s/database_transfer.py'
spec = importlib.util.spec_from_file_location('ppt_database_transfer_under_test', HELPER)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)
PASSWORDS = {'ppt_migrator': "synthetic'quote\\value", 'ppt_runtime': 'synthetic-runtime'}


def source_metadata():
    return {'server_version_num': 160015,
        'database': {'name': 'ppt_service', 'owner': 'ppt_migrator',
            'comment': transfer.MARKER, 'allow_connections': True, 'is_template': False,
            'tablespace': 'pg_default', 'encoding': 'UTF8', 'collate': 'C.UTF-8',
            'ctype': 'C.UTF-8', 'locale_provider': 'c', 'icu_locale': None},
        'roles': [{'name': name, 'login': True, 'superuser': False, 'inherit': False,
            'create_role': False, 'create_db': False, 'replication': False, 'bypass_rls': False,
            'connection_limit': -1, 'valid_until': None, 'config': []} for name in transfer.ROLES],
        'memberships': [], 'database_settings': []}


class Harness(transfer.Transfer):
    def __init__(self, tmp_path, *, conflict=False, readable=True, equivalent=True):
        tmp_path.chmod(0o700)
        self.log_stream = (tmp_path / 'private.log').open('w')
        super().__init__(kubeconfig='/synthetic/kubeconfig', output=tmp_path, log=self.log_stream,
                         fingerprint_fn=lambda _: {})
        self.conflict, self.readable, self.equivalent = conflict, readable, equivalent
        self.restored = False
        self.commands = []
        self.sql_calls = []

    def metadata(self, side):
        value = source_metadata()
        if side == 'target':
            value['server_version_num'] = 160014
            if not (self.restored or self.conflict):
                value.update(database=None, roles=[], memberships=[], database_settings=[])
        return value

    def fingerprint(self, side):
        return {'tables': {'synthetic_table': {'rows': 1 if side == 'source' or self.equivalent else 2,
                                             'sha256': 'a' * 64}}, 'sequences': {}}

    def psql(self, side, sql, database='ppt_service', user=None):
        self.sql_calls.append((side, sql, database, user))
        if user == 'ppt_runtime':
            return json.dumps({'user': 'ppt_runtime', 'schema': 3, 'schema_create': False,
                'revision_write': False, 'entries_read_append': True, 'entries_mutable': False,
                'streams_cas': True, 'streams_destructive': False})
        return ''

    def execute(self, command, **kwargs):
        self.commands.append(command)
        if 'pg_dump' in command:
            kwargs['stdout'].write(b'synthetic-private-custom-archive')
        elif '--list' in command:
            value = b'1; 1262 16384 DATABASE - ppt_service ppt_migrator\n' if self.readable else b'; missing database entry\n'
            kwargs['stdout'].write(value)
        elif 'pg_restore' in command:
            assert any('CREATE ROLE' in row[1] for row in self.sql_calls)
            self.restored = True
        else:
            pytest.fail('Unexpected synthetic command')
        return SimpleNamespace(returncode=0)


def test_existing_target_is_refused_before_dump_or_role_creation(tmp_path):
    instance = Harness(tmp_path, conflict=True)
    with pytest.raises(transfer.TransferError, match='phase inspect'):
        instance.run(PASSWORDS)
    assert not instance.commands and not instance.sql_calls
    assert not (tmp_path / 'ppt-service.dump').exists()
    receipt = json.loads((tmp_path / 'transfer-receipt.json').read_text())
    assert not receipt['passed']
    assert 'already exist' in receipt['diagnostic']


def test_unreadable_target_archive_never_creates_roles_or_restores(tmp_path):
    instance = Harness(tmp_path, readable=False)
    with pytest.raises(transfer.TransferError, match='verify-target-archive-reader'):
        instance.run(PASSWORDS)
    assert (tmp_path / 'ppt-service.dump').is_file()
    assert not instance.restored and not instance.sql_calls
    assert all('pg_dump' in command or '--list' in command for command in instance.commands)


def test_equivalent_restore_preserves_creation_options_and_uses_passwords_only_in_stdin(tmp_path):
    instance = Harness(tmp_path)
    receipt = instance.run(PASSWORDS)
    assert receipt['passed'] and receipt['fingerprints_equal'] and receipt['source_unchanged']
    dump, listing, restore = instance.commands
    assert '--create' in dump and '--format=custom' in dump
    assert '--list' in listing and listing[listing.index('-n') + 1] == 'data'
    assert '--create' in restore and '--dbname=postgres' in restore and '--exit-on-error' in restore
    assert '--no-owner' not in restore and '--no-acl' not in restore and '--clean' not in restore
    assert all(secret not in ' '.join(command) for command in instance.commands for secret in PASSWORDS.values())
    role_sql = next(sql for side, sql, _, _ in instance.sql_calls if 'CREATE ROLE' in sql)
    assert "PASSWORD 'synthetic''quote\\value'" in role_sql
    assert all(side == 'target' for side, _, _, _ in instance.sql_calls)
    assert all('DROP ' not in sql and 'CREATE DATABASE' not in sql for _, sql, _, _ in instance.sql_calls)
    assert (tmp_path / 'transfer-credentials.private.json').stat().st_mode & 0o777 == 0o600
    assert PASSWORDS['ppt_runtime'] not in json.dumps(receipt)


def test_fingerprint_mismatch_retains_partial_target_and_all_private_evidence(tmp_path, capsys):
    instance = Harness(tmp_path, equivalent=False)
    with pytest.raises(transfer.TransferError, match='verify-restored-schema-data-and-runtime'):
        instance.run(PASSWORDS)
    assert instance.restored
    assert (tmp_path / 'ppt-service.dump').is_file()
    assert (tmp_path / 'transfer-source-before.private.json').is_file()
    assert (tmp_path / 'transfer-target-after.private.json').is_file()
    assert (tmp_path / 'transfer-credentials.private.json').is_file()
    receipt = json.loads((tmp_path / 'transfer-receipt.json').read_text())
    assert not receipt['passed'] and receipt['target_objects_preserved_on_failure']
    assert 'fingerprint differs' in receipt['diagnostic']
    assert not any('DROP ' in sql for _, sql, _, _ in instance.sql_calls)
    assert capsys.readouterr() == ('', '')


def test_operator_psql_uses_only_reviewed_socket_endpoint_and_hides_command_failure(tmp_path):
    tmp_path.chmod(0o700)
    calls = []
    marker = 'synthetic-private-database-error'
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout=marker)
    with (tmp_path / 'private.log').open('w') as log:
        instance = transfer.Transfer(kubeconfig='/synthetic/kubeconfig', output=tmp_path,
                                     log=log, runner=runner, fingerprint_fn=lambda _: {})
        with pytest.raises(transfer.TransferError) as caught:
            instance.psql('target', 'SELECT 1;', database='postgres')
    command, options = calls[0]
    assert command[:9] == ['kubectl', '--kubeconfig=/synthetic/kubeconfig', '-n', 'data',
                           'exec', '-i', 'postgres-0', '--', 'psql']
    assert command[-4:] == ['-U', 'appuser', '-d', 'postgres']
    assert '-qAt' in command and options['input'] == 'SELECT 1;'
    assert marker not in str(caught.value)
