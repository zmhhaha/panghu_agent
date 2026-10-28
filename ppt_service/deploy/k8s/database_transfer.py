"""Private, one-time transfer of an already quiesced PPT PostgreSQL database.

The orchestrator owns admission, deployment locks, file backups and consumers.
This helper never stops or drops a database, changes source data, reads password
catalogs, or adopts an existing target database/role. Failed target objects and
all private evidence remain available for explicit operator recovery.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess


HERE = Path(__file__).resolve().parent
DATABASE = 'ppt_service'
ROLES = ('ppt_migrator', 'ppt_runtime')
MARKER = 'ppt-service-bootstrap-v1'
ENDPOINTS = {
    'source': ('ppt-service', 'ppt-postgres-0', 'ppt_bootstrap'),
    'target': ('data', 'postgres-0', 'appuser'),
}
METADATA_SQL = """BEGIN READ ONLY;
SET LOCAL standard_conforming_strings = on;
SELECT json_build_object(
 'server_version_num',current_setting('server_version_num')::int,
 'database',(SELECT json_build_object(
    'name',d.datname,'owner',pg_get_userbyid(d.datdba),
    'encoding',pg_encoding_to_char(d.encoding),'collate',d.datcollate,'ctype',d.datctype,
    'locale_provider',d.datlocprovider,'icu_locale',d.daticulocale,
    'icu_rules',d.daticurules,'collation_version',d.datcollversion,
    'allow_connections',d.datallowconn,'connection_limit',d.datconnlimit,
    'is_template',d.datistemplate,'tablespace',t.spcname,
    'comment',shobj_description(d.oid,'pg_database'),
    'acl',COALESCE((SELECT json_agg(x ORDER BY x.grantor COLLATE "C",x.grantee COLLATE "C",x.privilege COLLATE "C",x.grantable)
       FROM (SELECT pg_get_userbyid(a.grantor) AS grantor,
          CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
          a.privilege_type AS privilege,a.is_grantable AS grantable
          FROM aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) a) x),'[]'::json))
    FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace WHERE d.datname='ppt_service'),
 'roles',COALESCE((SELECT json_agg(x ORDER BY x.name COLLATE "C") FROM
    (SELECT rolname AS name,rolsuper AS superuser,rolinherit AS inherit,
       rolcreaterole AS create_role,rolcreatedb AS create_db,rolcanlogin AS login,
       rolreplication AS replication,rolbypassrls AS bypass_rls,rolconnlimit AS connection_limit,
       rolvaliduntil::text AS valid_until,
       COALESCE((SELECT json_agg(c ORDER BY c COLLATE "C") FROM unnest(rolconfig) c),'[]'::json) AS config
     FROM pg_roles WHERE rolname IN ('ppt_migrator','ppt_runtime')) x),'[]'::json),
 'memberships',COALESCE((SELECT json_agg(x ORDER BY x.role COLLATE "C",x.member COLLATE "C") FROM
    (SELECT pg_get_userbyid(roleid) AS role,pg_get_userbyid(member) AS member,admin_option
     FROM pg_auth_members WHERE roleid IN (SELECT oid FROM pg_roles WHERE rolname IN ('ppt_migrator','ppt_runtime'))
        OR member IN (SELECT oid FROM pg_roles WHERE rolname IN ('ppt_migrator','ppt_runtime'))) x),'[]'::json),
 'database_settings',COALESCE((SELECT json_agg(x ORDER BY x.role COLLATE "C") FROM
    (SELECT CASE WHEN s.setrole=0 THEN 'ALL' ELSE pg_get_userbyid(s.setrole) END AS role,
        (SELECT json_agg(c ORDER BY c COLLATE "C") FROM unnest(s.setconfig) c) AS config
     FROM pg_db_role_setting s JOIN pg_database d ON d.oid=s.setdatabase
     WHERE d.datname='ppt_service') x),'[]'::json));
COMMIT;
"""
RUNTIME_SQL = """BEGIN READ ONLY;
SELECT json_build_object('user',current_user,
 'schema',(SELECT max(revision) FROM public.api_schema_revision),
 'schema_create',has_schema_privilege(current_user,'public','CREATE'),
 'revision_write',has_table_privilege(current_user,'public.api_schema_revision','INSERT')
    OR has_table_privilege(current_user,'public.api_schema_revision','UPDATE')
    OR has_table_privilege(current_user,'public.api_schema_revision','DELETE'),
 'entries_read_append',has_table_privilege(current_user,'public.ppt_acceptance_entries','SELECT')
    AND has_table_privilege(current_user,'public.ppt_acceptance_entries','INSERT'),
 'entries_mutable',has_table_privilege(current_user,'public.ppt_acceptance_entries','UPDATE')
    OR has_table_privilege(current_user,'public.ppt_acceptance_entries','DELETE')
    OR has_table_privilege(current_user,'public.ppt_acceptance_entries','TRUNCATE')
    OR has_table_privilege(current_user,'public.ppt_acceptance_entries','REFERENCES')
    OR has_table_privilege(current_user,'public.ppt_acceptance_entries','TRIGGER'),
 'streams_cas',has_table_privilege(current_user,'public.ppt_acceptance_streams','SELECT')
    AND has_table_privilege(current_user,'public.ppt_acceptance_streams','INSERT')
    AND has_table_privilege(current_user,'public.ppt_acceptance_streams','UPDATE'),
 'streams_destructive',has_table_privilege(current_user,'public.ppt_acceptance_streams','DELETE')
    OR has_table_privilege(current_user,'public.ppt_acceptance_streams','TRUNCATE')
    OR has_table_privilege(current_user,'public.ppt_acceptance_streams','REFERENCES')
    OR has_table_privilege(current_user,'public.ppt_acceptance_streams','TRIGGER'));
COMMIT;
"""


class TransferError(RuntimeError):
    """Controlled diagnostic; details belong only in private evidence."""


def require(condition, message):
    if not condition:
        raise TransferError(message)


def literal(value):
    require(isinstance(value, str) and '\x00' not in value, 'Invalid transfer SQL value')
    return "'" + value.replace("'", "''") + "'"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


class Transfer:
    def __init__(self, *, kubeconfig, output, log, runner=subprocess.run, fingerprint_fn=None):
        self.k = ['kubectl', '--kubeconfig=' + str(kubeconfig)]
        self.output, self.log, self.runner = Path(output), log, runner
        require(self.output.is_dir() and not self.output.is_symlink(), 'Private transfer directory is required')
        info = self.output.stat()
        require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid(),
                'Transfer directory must be private and operator-owned')
        if fingerprint_fn is None:
            spec = importlib.util.spec_from_file_location('ppt_transfer_fingerprint', HERE / 'database_fingerprint.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            fingerprint_fn = module.fingerprint
        self.fingerprint_fn = fingerprint_fn

    def command(self, side, *args):
        require(side in ENDPOINTS, 'Unknown database endpoint')
        namespace, pod, _ = ENDPOINTS[side]
        return self.k + ['-n', namespace, 'exec', '-i', pod, '--', *args]

    def execute(self, command, **kwargs):
        try:
            result = self.runner(command, stderr=self.log, check=False, timeout=900, **kwargs)
        except (OSError, subprocess.SubprocessError):
            raise TransferError('Transfer command could not complete; inspect private log') from None
        require(result.returncode == 0, 'Transfer command failed; inspect private log')
        return result

    def psql(self, side, sql, database=DATABASE, user=None):
        require(side in ENDPOINTS and database in (DATABASE, 'postgres'), 'Unexpected transfer database')
        user = user or ENDPOINTS[side][2]
        require(user in (*ROLES, ENDPOINTS[side][2]), 'Unexpected transfer database role')
        args = self.command(side, 'psql', '-X', '--no-password', '-qAt',
                            '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database)
        return self.execute(args, input=sql, stdout=subprocess.PIPE, text=True).stdout

    def open_private(self, name, *, binary=False):
        require(Path(name).name == name, 'Unexpected transfer evidence filename')
        descriptor = os.open(self.output / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        return os.fdopen(descriptor, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8'}))

    def save(self, name, value):
        with self.open_private(name) as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())

    def metadata(self, side):
        try:
            result = json.loads(self.psql(side, METADATA_SQL, database='postgres'))
        except (TypeError, ValueError):
            raise TransferError('Invalid database metadata response') from None
        require(isinstance(result, dict) and set(result) == {
            'server_version_num', 'database', 'roles', 'memberships', 'database_settings'},
            'Incomplete database metadata response')
        return result

    @staticmethod
    def validate_source(metadata):
        db = metadata['database']
        require(isinstance(db, dict) and db['name'] == DATABASE and db['owner'] == 'ppt_migrator'
                and db['comment'] == MARKER and db['allow_connections'] and not db['is_template']
                and db['tablespace'] == 'pg_default', 'Source database does not match the PPT contract')
        roles = metadata['roles']
        require({role['name'] for role in roles} == set(ROLES) and len(roles) == 2
                and not metadata['memberships'], 'Source role inventory does not match the PPT contract')
        require(all(role['login'] and not any(role[key] for key in
                ('superuser', 'inherit', 'create_role', 'create_db', 'replication', 'bypass_rls')) for role in roles),
                'Source database roles must remain restricted')
        require(all(row['role'] in ('ALL', *ROLES) for row in metadata['database_settings']),
                'Unexpected source database role setting')

    @staticmethod
    def validate_empty_target(metadata):
        require(metadata['database'] is None and metadata['roles'] == []
                and metadata['memberships'] == [] and metadata['database_settings'] == [],
                'Target PPT database or roles already exist; no objects were adopted or overwritten')

    def inspect(self):
        source, target = self.metadata('source'), self.metadata('target')
        self.validate_source(source)
        self.validate_empty_target(target)
        require(type(source['server_version_num']) is int and type(target['server_version_num']) is int
                and source['server_version_num'] // 10000 == target['server_version_num'] // 10000 == 16,
                'Only the reviewed PostgreSQL 16 source and target are supported')
        return source, target

    def preflight(self):
        source, target = self.inspect()
        self.save('transfer-preflight.json', {'source': source, 'target': target})
        return {'source_version': source['server_version_num'], 'target_version': target['server_version_num'],
                'target_database_and_roles_absent': True}

    def fingerprint(self, side):
        return self.fingerprint_fn(lambda sql: self.psql(side, sql))

    def create_roles(self, source, passwords):
        statements = ['BEGIN;', 'SET LOCAL standard_conforming_strings = on;']
        for role in source['roles']:
            name = role['name']
            require(type(role['connection_limit']) is int, 'Invalid source role connection limit')
            statement = ('CREATE ROLE "' + name + '" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                         'NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT ' + str(role['connection_limit'])
                         + ' PASSWORD ' + literal(passwords[name]))
            if role['valid_until'] is not None:
                statement += ' VALID UNTIL ' + literal(role['valid_until'])
            statements.append(statement + ';')
            for config in role['config']:
                key, separator, value = config.partition('=')
                require(separator and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*', key), 'Invalid source role configuration')
                statements.append('ALTER ROLE "' + name + '" SET "' + key + '" TO ' + literal(value) + ';')
        statements.append('COMMIT;')
        self.psql('target', '\n'.join(statements), database='postgres')

    def run(self, passwords):
        receipt = {'passed': False, 'phase': 'inspect', 'source_unchanged': False,
                   'target_objects_preserved_on_failure': True, 'application_rows_rewritten': False}
        try:
            source, target = self.inspect()
            require(set(passwords) == set(ROLES) and all(isinstance(value, str) and value
                    and not any(ord(c) < 32 or ord(c) == 127 for c in value) for value in passwords.values()),
                    'Provide the two existing valid PPT role passwords')
            self.save('transfer-credentials.private.json', passwords)
            self.save('transfer-source-metadata.json', source)
            before = self.fingerprint('source')
            self.save('transfer-source-before.private.json', before)
            before_hash = hashlib.sha256(canonical(before)).hexdigest()
            receipt['source_fingerprint_sha256'] = before_hash

            receipt['phase'] = 'archive-source'
            archive = self.output / 'ppt-service.dump'
            with self.open_private(archive.name, binary=True) as stream:
                self.execute(self.command('source', 'pg_dump', '--format=custom', '--create',
                    '--no-password', '-U', ENDPOINTS['source'][2], '--dbname=' + DATABASE),
                    input=b'', stdout=stream)
                stream.flush()
                os.fsync(stream.fileno())
            require(archive.stat().st_size > 0, 'Source database archive is empty')
            receipt['archive'] = {'path': str(archive), 'bytes': archive.stat().st_size, 'sha256': digest(archive)}

            receipt['phase'] = 'verify-target-archive-reader'
            listing = self.output / 'ppt-service-archive-list.private.txt'
            with archive.open('rb') as stream, self.open_private(listing.name, binary=True) as out:
                # --list shows only selected entries. Without --create it hides
                # DATABASE/its ACL even when those entries exist in the archive.
                self.execute(self.command('target', 'pg_restore', '--create', '--list'), stdin=stream, stdout=out)
            toc = listing.read_text(encoding='utf-8')
            require(re.search(r'^\d+;\s+\d+\s+\d+\s+DATABASE\s+-\s+ppt_service\s+ppt_migrator\s*$', toc, re.M),
                    'Archive is missing the reviewed PPT database creation entry')
            receipt['target_archive_reader_verified'] = True

            receipt['phase'] = 'create-target-roles-and-restore'
            self.validate_empty_target(self.metadata('target'))
            self.create_roles(source, passwords)
            with archive.open('rb') as stream:
                self.execute(self.command('target', 'pg_restore', '--create', '--exit-on-error', '--verbose',
                    '--no-password', '-U', ENDPOINTS['target'][2], '--dbname=postgres'),
                    stdin=stream, stdout=self.log)
            restored = self.metadata('target')
            self.save('transfer-target-metadata.json', restored)
            require({k: v for k, v in restored.items() if k != 'server_version_num'} ==
                    {k: v for k, v in source.items() if k != 'server_version_num'},
                    'Restored database locale, roles, ownership, ACL, settings or completion marker differs')
            receipt['phase'] = 'verify-restored-schema-data-and-runtime'
            grant = (HERE / 'grant-runtime.sql').read_text(encoding='utf-8').replace(':"runtime"', '"ppt_runtime"')
            self.psql('target', grant, user='ppt_migrator')
            after = self.fingerprint('target')
            self.save('transfer-target-after.private.json', after)
            require(after == before, 'Restored database fingerprint differs; preserve partial target and inspect private evidence')
            runtime = json.loads(self.psql('target', RUNTIME_SQL, user='ppt_runtime'))
            require(runtime == {'user': 'ppt_runtime', 'schema': 3, 'schema_create': False,
                'revision_write': False, 'entries_read_append': True, 'entries_mutable': False,
                'streams_cas': True, 'streams_destructive': False}, 'Target runtime role privilege contract failed')
            source_after = self.fingerprint('source')
            self.save('transfer-source-after.private.json', source_after)
            require(source_after == before and self.metadata('source') == source,
                    'Quiesced source changed during transfer; do not switch consumers')
            receipt.update(passed=True, phase='complete', source_unchanged=True, fingerprints_equal=True,
                database_locale_and_acl_equal=True, runtime_read_only_check=True,
                target_fingerprint_sha256=hashlib.sha256(canonical(after)).hexdigest(),
                source_version=source['server_version_num'], target_version=target['server_version_num'],
                tables=len(after['tables']), sequences=len(after['sequences']))
            return receipt
        except BaseException as error:
            receipt['error_type'] = type(error).__name__
            # Only messages constructed by this helper are safe for a receipt.
            # Raw subprocess/database/parser messages can contain SQL or values.
            receipt['diagnostic'] = str(error) if isinstance(error, TransferError) else type(error).__name__
            raise TransferError('Database transfer stopped in phase ' + receipt['phase']
                                + '; preserve private evidence and partial target objects') from None
        finally:
            self.save('transfer-receipt.json', receipt)
