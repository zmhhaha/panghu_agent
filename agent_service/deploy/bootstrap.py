"""Inspect/bootstrap dedicated Agent credentials and database without secret output.

Run on the administrative server. Default mode is read-only; --apply explicitly
permits CAS Vault writes and DDL restricted to agent_service's role/database.
Only the existing logged-in Vault CLI inside vault/vault-0 is used.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import subprocess
from urllib.parse import quote, unquote, urlsplit


RUNTIME_PATH = 'secret/agent-service/runtime'
CALLERS_PATH = 'secret/llm-service/callers'
MODEL_KEY = 'LLM_TOKEN_AGENT_EXECUTION'
TOKEN_KEYS = ('PPT_SERVICE_TOKEN', 'SCIENTIFIC_SERVICE_TOKEN', 'VIDEO_WORKBENCH_SERVICE_TOKEN',
              'PI_WORKER_TOKEN', 'HERMES_WORKER_TOKEN')
SIGNING_KEYS = ('PI_RECEIPT_KEY_BASE64', 'HERMES_RECEIPT_KEY_BASE64', 'MODEL_SIGNING_KEY_BASE64')
RUNTIME_KEYS = ('DATABASE_URL', *TOKEN_KEYS, *SIGNING_KEYS)
DB_NAME = DB_ROLE = 'agent_service'
LIMIT = 2 * 1024 * 1024


class BootstrapError(RuntimeError):
    """Fixed categories only. Never retain arbitrary subprocess/SQL errors."""


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise BootstrapError('invalid_administrative_response') from None


class Cluster:
    def __init__(self, kubeconfig='/etc/kubernetes/super-admin.conf', *, runner=subprocess.run):
        self.kubeconfig, self.runner = kubeconfig, runner

    def exec(self, namespace, pod, command, *, payload=None):
        args = ['kubectl', '--kubeconfig', self.kubeconfig, '-n', namespace, 'exec']
        if payload is not None:
            args.append('-i')
        args += [pod, '--', *command]
        try:
            result = self.runner(args, input=payload, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=90, check=False)
            if (type(result.stdout) is not bytes or type(result.stderr) is not bytes
                    or len(result.stdout) > LIMIT or len(result.stderr) > LIMIT):
                raise BootstrapError('administrative_response_limit')
            return result
        except (OSError, subprocess.SubprocessError):
            raise BootstrapError('administrative_command_unavailable') from None

    def vault(self, command, *, payload=None, missing=False):
        result = self.exec('vault', 'vault-0', ['vault', *command], payload=payload)
        if result.returncode:
            error = result.stderr.lower()
            if missing and result.returncode == 2:
                missing_path = None
                if len(command) == 4 and command[:3] == ['kv', 'get', '-format=json']:
                    path = command[3]
                    if path.startswith('secret/'):
                        missing_path = 'secret/data/' + path[len('secret/'):]
                elif len(command) == 3 and command[:2] == ['read', '-format=json']:
                    missing_path = command[2]
                if missing_path is not None:
                    expected = ('No value found at ' + missing_path).encode('utf-8')
                    # Vault may print its missing-value diagnostic on stdout;
                    # kubectl may append its own fixed exit-status line on stderr.
                    # Recognize only this complete diagnostic for a read command.
                    lines = [line.strip() for raw in (result.stdout, result.stderr)
                        for line in raw.splitlines() if line.strip()]
                    if expected in lines and all(line in (expected,
                            b'command terminated with exit code 2') for line in lines):
                        return None
            if b'check-and-set' in error and b'did not match' in error:
                raise BootstrapError('vault_cas_conflict')
            operation = ('token_lookup' if command[:2] == ['token', 'lookup'] else
                'kv_get' if command[:2] == ['kv', 'get'] else
                'kv_put' if command[:2] == ['kv', 'put'] else
                'policy_read' if command[:1] == ['read'] else 'operation')
            raise BootstrapError('vault_' + operation + '_unavailable')
        return result.stdout

    def kv(self, path):
        raw = self.vault(['kv', 'get', '-format=json', path], missing=True)
        if raw is None:
            return {}, 0
        document = _json(raw)
        try:
            body, metadata = document['data']['data'], document['data']['metadata']
            version = metadata['version']
            if (type(body) is not dict or type(version) is not int or version < 1
                    or metadata.get('destroyed') or metadata.get('deletion_time')):
                raise ValueError()
            return body, version
        except (KeyError, TypeError, ValueError):
            raise BootstrapError('vault_kv_state_invalid') from None

    def cas(self, path, version, values):
        # The complete merged JSON travels over stdin, never command arguments,
        # a temporary file, shell history or a printed deployment manifest.
        payload = json.dumps(values, separators=(',', ':'), allow_nan=False).encode()
        self.vault(['kv', 'put', '-format=json', '-cas=' + str(version), path, '-'], payload=payload)

    def sql(self, sql, *, database='postgres'):
        # POSTGRES_USER may come from envFrom. The official image's local
        # administrative connection uses that role, falling back to postgres.
        # Suppress statement/error-statement logging in this private session;
        # raw stderr is always captured and never printed by this script.
        shell = ('set -eu; export PGCONNECT_TIMEOUT=10; '
            'export PGOPTIONS="-c log_statement=none -c log_min_duration_statement=-1 '
            '-c log_min_error_statement=panic"; '
            'export PGPASSWORD="${POSTGRES_PASSWORD:-}"; '
            'exec psql -X -qAtw -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5432 '
            '-U "${POSTGRES_USER:-postgres}" -d "$1"')
        result = self.exec('data', 'postgres-0', ['sh', '-c', shell, 'agent-bootstrap', database],
                           payload=sql.encode('utf-8'))
        if result.returncode:
            raise BootstrapError('database_administration_failed')
        return result.stdout

    def authenticate_database(self, password):
        # This psql process receives the password through stdin and passes it
        # only in its private environment. No password-bearing URI/argv is used.
        shell = ('set -eu; IFS= read -r AGENT_BOOTSTRAP_DB_PASSWORD; '
            'export PGPASSWORD="$AGENT_BOOTSTRAP_DB_PASSWORD"; export PGCONNECT_TIMEOUT=10; '
            'exec psql -X -qAtw -h 127.0.0.1 -p 5432 -U agent_service -d agent_service '
            '-c "SELECT current_user = \'agent_service\' AND current_database() = \'agent_service\'"')
        result = self.exec('data', 'postgres-0', ['sh', '-c', shell], payload=(password + '\n').encode())
        if result.returncode or result.stdout.strip() != b't':
            raise BootstrapError('database_runtime_authentication_failed')


DATABASE_INSPECTION = """
WITH r AS (SELECT * FROM pg_roles WHERE rolname = 'agent_service'),
     d AS (SELECT * FROM pg_database WHERE datname = 'agent_service')
SELECT json_build_object(
 'role_exists', EXISTS(SELECT 1 FROM r),
 'role_safe', COALESCE((SELECT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb
       AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls FROM r), false),
 'membership_count', (SELECT count(*) FROM pg_auth_members WHERE member = (SELECT oid FROM r)),
 'database_exists', EXISTS(SELECT 1 FROM d),
 'database_owned', COALESCE((SELECT datdba = (SELECT oid FROM r) FROM d), false),
 'other_database_ownerships', (SELECT count(*) FROM pg_database WHERE datname <> 'agent_service'
       AND datdba = (SELECT oid FROM r)),
 'other_database_explicit_grants', (SELECT count(*) FROM pg_database x,
       LATERAL aclexplode(COALESCE(x.datacl, acldefault('d', x.datdba))) a
       WHERE x.datname <> 'agent_service' AND a.grantee = (SELECT oid FROM r)),
 'other_database_connect_count', (SELECT count(*) FROM pg_database WHERE datallowconn
       AND NOT datistemplate AND datname <> 'agent_service'
       AND has_database_privilege((SELECT oid FROM r), oid, 'CONNECT')));
"""

DATABASE_LIST = """SELECT COALESCE(json_agg(datname), '[]'::json) FROM pg_database
WHERE datallowconn AND NOT datistemplate AND datname <> 'agent_service';"""

# Reviewed pgcrypto/pg_trgm/citext PUBLIC invoker functions supply computation,
# strings and cryptography, not the caller's missing business-table privileges.
# Unknown/custom/definer routines and direct object grants remain blocking.
CROSS_DATABASE_INSPECTION = """
WITH r AS (SELECT oid FROM pg_roles WHERE rolname = 'agent_service'),
routine_access AS (
  SELECT p.prosecdef, has_function_privilege((SELECT oid FROM r), p.oid, 'EXECUTE') AS executable,
    EXISTS (SELECT 1 FROM pg_depend dep JOIN pg_extension ext ON ext.oid = dep.refobjid
      WHERE dep.classid = 'pg_proc'::regclass AND dep.objid = p.oid
        AND dep.refclassid = 'pg_extension'::regclass AND dep.deptype = 'e'
        AND ext.extname IN ('pgcrypto', 'pg_trgm', 'citext')) AS reviewed_extension,
    EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
      WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE') AS public_execute,
    EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
      WHERE a.grantee = (SELECT oid FROM r) AND a.privilege_type = 'EXECUTE') AS direct_agent_execute
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
), classified_routines AS (
  SELECT executable,
    (reviewed_extension AND NOT prosecdef AND public_execute AND NOT direct_agent_execute)
      AS reviewed_public_invoker
  FROM routine_access
)
SELECT json_build_object('unsafe_objects',
  (SELECT count(*) FROM pg_namespace n WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    AND has_schema_privilege((SELECT oid FROM r), n.oid, 'CREATE')) +
  (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    AND CASE WHEN c.relkind IN ('r','p','v','m','f') THEN
      has_table_privilege((SELECT oid FROM r), c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      ELSE false END) +
  (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    AND CASE WHEN c.relkind='S' THEN
      has_sequence_privilege((SELECT oid FROM r), c.oid, 'SELECT,UPDATE,USAGE')
      ELSE false END) +
  (SELECT count(*) FROM classified_routines WHERE executable AND NOT reviewed_public_invoker),
  'reviewed_public_functions',
  (SELECT count(*) FROM classified_routines WHERE executable AND reviewed_public_invoker));
"""


def inspect_database(cluster):
    result = _json(cluster.sql(DATABASE_INSPECTION))
    bools = ('role_exists', 'role_safe', 'database_exists', 'database_owned')
    counts = ('membership_count', 'other_database_ownerships', 'other_database_explicit_grants',
              'other_database_connect_count')
    if (type(result) is not dict or any(type(result.get(key)) is not bool for key in bools)
            or any(type(result.get(key)) is not int or result[key] < 0 for key in counts)):
        raise BootstrapError('database_inspection_invalid')
    result = {key: result[key] for key in (*bools, *counts)}
    result['cross_database_unsafe_objects'] = 0
    result['cross_database_reviewed_public_functions'] = 0
    if result['role_exists']:
        names = _json(cluster.sql(DATABASE_LIST))
        if type(names) is not list or len(names) > 100:
            raise BootstrapError('database_inspection_limit')
        for name in names:
            if type(name) is not str or not name or len(name) > 63 or any(ord(char) < 32 for char in name):
                raise BootstrapError('database_inspection_invalid')
            audit = _json(cluster.sql(CROSS_DATABASE_INSPECTION, database=name))
            number = audit.get('unsafe_objects') if type(audit) is dict else None
            reviewed = audit.get('reviewed_public_functions') if type(audit) is dict else None
            if type(number) is not int or number < 0 or type(reviewed) is not int or reviewed < 0:
                raise BootstrapError('database_inspection_invalid')
            result['cross_database_unsafe_objects'] += number
            result['cross_database_reviewed_public_functions'] += reviewed
    return result


def database_safe(state):
    return (not state['role_exists'] or state['role_safe']) and not any(state[key] for key in
        ('membership_count', 'other_database_ownerships', 'other_database_explicit_grants',
         'cross_database_unsafe_objects')) and (not state['database_exists'] or state['database_owned'])


def database_password(runtime, hostname):
    try:
        value = runtime['DATABASE_URL']
        u = urlsplit(value)
        password = unquote(u.password or '', errors='strict')
        if (u.scheme not in {'postgresql', 'postgresql+psycopg'} or u.hostname != hostname
                or u.port not in (None, 5432) or unquote(u.username or '') != DB_ROLE
                or u.path != '/' + DB_NAME or u.query or u.fragment
                or not password or len(password) > 1024
                or any(ord(char) < 33 or ord(char) == 127 for char in password)):
            raise ValueError()
        return password
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError):
        raise BootstrapError('existing_database_url_invalid') from None


def valid_token(value):
    return type(value) is str and 32 <= len(value) <= 512 and all(33 <= ord(c) <= 126 for c in value)


def validate_runtime(runtime, hostname):
    if any(key not in runtime for key in RUNTIME_KEYS):
        raise BootstrapError('runtime_fields_missing')
    password = database_password(runtime, hostname)
    if any(not valid_token(runtime[key]) for key in TOKEN_KEYS):
        raise BootstrapError('existing_runtime_token_invalid')
    if len({runtime[key] for key in TOKEN_KEYS}) != len(TOKEN_KEYS):
        raise BootstrapError('existing_runtime_tokens_not_independent')
    decoded = []
    try:
        for key in SIGNING_KEYS:
            if type(runtime[key]) is not str or len(runtime[key]) > 1400:
                raise ValueError()
            raw = base64.b64decode(runtime[key], validate=True)
            if not 32 <= len(raw) <= 1024:
                raise ValueError()
            decoded.append(raw)
    except (ValueError, TypeError):
        raise BootstrapError('existing_runtime_key_invalid') from None
    if len(set(decoded)) != len(SIGNING_KEYS):
        raise BootstrapError('existing_runtime_keys_not_independent')
    return password


def ensure_runtime(cluster, hostname):
    for _ in range(5):
        current, version = cluster.kv(RUNTIME_PATH)
        merged = dict(current)
        if 'DATABASE_URL' not in merged:
            state = inspect_database(cluster)
            if state['role_exists']:
                raise BootstrapError('existing_database_role_requires_runtime_url')
            password = secrets.token_urlsafe(36)
            merged['DATABASE_URL'] = ('postgresql+psycopg://agent_service:' + quote(password, safe='')
                + '@' + hostname + ':5432/agent_service')
        for key in TOKEN_KEYS:
            if key not in merged:
                merged[key] = secrets.token_urlsafe(36)
        for key in SIGNING_KEYS:
            if key not in merged:
                merged[key] = base64.b64encode(secrets.token_bytes(32)).decode('ascii')
        validate_runtime(merged, hostname)
        if merged == current:
            return merged, 'preserved'
        try:
            cluster.cas(RUNTIME_PATH, version, merged)
            # Re-read to use exactly the durable winning credential set.
            saved, _ = cluster.kv(RUNTIME_PATH)
            if saved != merged:
                raise BootstrapError('runtime_changed_during_bootstrap')
            return saved, 'created' if version == 0 else 'missing_fields_added'
        except BootstrapError as error:
            if str(error) != 'vault_cas_conflict':
                raise
    raise BootstrapError('vault_cas_retry_limit')


def ensure_model_caller(cluster, *, excluded_tokens=()):
    for _ in range(5):
        current, version = cluster.kv(CALLERS_PATH)
        if MODEL_KEY in current:
            if (not valid_token(current[MODEL_KEY]) or current[MODEL_KEY] in excluded_tokens
                    or any(value == current[MODEL_KEY] for key, value in current.items() if key != MODEL_KEY)):
                raise BootstrapError('existing_model_caller_invalid')
            return 'preserved'
        merged = {**current, MODEL_KEY: secrets.token_urlsafe(36)}
        try:
            cluster.cas(CALLERS_PATH, version, merged)
            saved, _ = cluster.kv(CALLERS_PATH)
            if saved.get(MODEL_KEY) != merged[MODEL_KEY]:
                raise BootstrapError('model_caller_changed_during_bootstrap')
            return 'created'
        except BootstrapError as error:
            if str(error) != 'vault_cas_conflict':
                raise
    raise BootstrapError('vault_cas_retry_limit')


def create_database(cluster, password):
    # Session advisory lock serializes concurrent bootstraps. CREATE DATABASE
    # must run outside a transaction; existing role passwords are never reset.
    literal = "'" + password.replace("'", "''") + "'"
    cluster.sql("SELECT pg_advisory_lock(73124002);\n"
        "SELECT format('CREATE ROLE agent_service LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 32 PASSWORD %L', " + literal + ") "
        "WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='agent_service') \\gexec\n"
        "SELECT 'CREATE DATABASE agent_service OWNER agent_service TEMPLATE template0 ENCODING ''UTF8''' "
        "WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='agent_service') \\gexec\n"
        "REVOKE ALL ON DATABASE agent_service FROM PUBLIC;\n"
        "SELECT pg_advisory_unlock(73124002);\n")
    cluster.sql("REVOKE ALL ON SCHEMA public FROM PUBLIC;\n"
        "GRANT USAGE, CREATE ON SCHEMA public TO agent_service;\n"
        "ALTER DEFAULT PRIVILEGES FOR ROLE agent_service IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;\n"
        "ALTER DEFAULT PRIVILEGES FOR ROLE agent_service IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;\n"
        "ALTER DEFAULT PRIVILEGES FOR ROLE agent_service IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;\n",
        database=DB_NAME)
    cluster.authenticate_database(password)


def _simple_policy_rules(policy):
    """Parse only complete canonical HCL rules; unknown syntax is unproven.

    Comments cannot contribute permissions. This is deliberately narrower than
    Vault's policy grammar rather than an attempted replacement evaluator.
    """
    if type(policy) is not str:
        return None
    trivia = r'(?:\s+|\#[^\n]*(?:\n|$)|//[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)*'
    pattern = re.compile(
        trivia + r'path' + trivia + r'"([A-Za-z0-9_./+*-]+)"' + trivia
        + r'\{' + trivia + r'capabilities' + trivia + r'=' + trivia + r'\['
        + r'(' + trivia + r'(?:"[a-z]+"' + trivia + r'(?:,' + trivia
        + r'"[a-z]+"' + trivia + r')*,?' + trivia + r')?)'
        + r'\]' + trivia + r'\}')
    rules, position = [], 0
    while position < len(policy):
        if re.fullmatch(trivia, policy[position:]):
            return rules
        match = pattern.match(policy, position)
        if match is None:
            return None
        path = match[1]
        if ('*' in path[:-1] or any('+' in part and part != '+' for part in path.split('/'))):
            return None
        capabilities = re.sub(r'\#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/', '', match[2])
        rules.append((path, set(re.findall(r'"([a-z]+)"', capabilities))))
        position = match.end()
    return rules


def inspect_eso(cluster):
    # Read-only policy coverage analysis, not an ESO login or policy mutation.
    paths = ('secret/data/agent-service/runtime', 'secret/data/llm-service/callers')
    result = dict(role_available=False, read_coverage='not_proven', policy_changes=False)
    try:
        raw = cluster.vault(['read', '-format=json', 'auth/kubernetes/role/eso-role'], missing=True)
        if raw is None:
            return result
        role = _json(raw)['data']
        policies = role.get('token_policies') or role.get('policies') or []
        if type(policies) is not list or len(policies) > 100:
            return result
        result['role_available'] = True
        allowed, denied, uncertain = set(), set(), False
        for name in policies:
            if type(name) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name):
                uncertain = True
                continue
            raw = cluster.vault(['read', '-format=json', 'sys/policies/acl/' + name], missing=True)
            if raw is None:
                uncertain = True
                continue
            policy = _json(raw)['data'].get('policy', '')
            rules = _simple_policy_rules(policy)
            if rules is None:
                uncertain = True
                continue
            for pattern, capabilities in rules:
                escaped = re.escape(pattern).replace(r'\+', '[^/]+').replace(r'\*', '.*')
                for path in paths:
                    if re.fullmatch(escaped, path):
                        if 'deny' in capabilities:
                            denied.add(path)
                        if 'read' in capabilities:
                            allowed.add(path)
                        else:
                            # More specific matching rules may take precedence
                            # over a broad read grant. Do not guess precedence.
                            uncertain = True
        result['read_coverage'] = ('denied' if denied else 'confirmed'
            if not uncertain and set(paths) <= allowed else 'not_proven')
    except (BootstrapError, KeyError, TypeError, ValueError):
        pass
    return result


def bootstrap(cluster, *, apply=False, database_host='postgres.data.svc.cluster.local'):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', database_host):
        raise BootstrapError('invalid_database_service_host')
    # Vault CLI's token lookup does not support the generic read -field flag.
    # Capture its JSON response without ever printing the token-bearing data.
    token = _json(cluster.vault(['token', 'lookup', '-format=json']))
    if (type(token) is not dict or type(token.get('data')) is not dict
            or not token['data'].get('id')):
        raise BootstrapError('vault_token_lookup_invalid')
    runtime, _ = cluster.kv(RUNTIME_PATH)
    callers, _ = cluster.kv(CALLERS_PATH)
    state = inspect_database(cluster)
    report = dict(mode='apply' if apply else 'inspect', vault_cli_authenticated=True,
        runtime=dict(exists=bool(runtime), keys={key: key in runtime for key in RUNTIME_KEYS}),
        model_caller=dict(exists=MODEL_KEY in callers), database=state, eso=inspect_eso(cluster))
    if not apply:
        credentials_valid = False
        try:
            validate_runtime(runtime, database_host)
            credentials_valid = (valid_token(callers.get(MODEL_KEY))
                and callers[MODEL_KEY] not in [runtime[key] for key in TOKEN_KEYS])
        except BootstrapError:
            pass
        report['runtime']['values_valid'] = credentials_valid
        report['ready'] = (credentials_valid and state['role_exists'] and state['database_exists']
            and database_safe(state) and report['eso']['read_coverage'] == 'confirmed')
        return report
    if not database_safe(state):
        raise BootstrapError('existing_database_scope_requires_review')
    runtime, action = ensure_runtime(cluster, database_host)
    password = validate_runtime(runtime, database_host)
    report['runtime'] = dict(exists=True, action=action, keys={key: True for key in RUNTIME_KEYS})
    report['model_caller'] = dict(exists=True, action=ensure_model_caller(cluster,
        excluded_tokens=tuple(runtime[key] for key in TOKEN_KEYS)))
    # Recheck ownership immediately before DDL; no existing unsafe role is
    # silently demoted and no existing database is adopted or dropped.
    state = inspect_database(cluster)
    if not database_safe(state):
        raise BootstrapError('existing_database_scope_requires_review')
    create_database(cluster, password)
    state = inspect_database(cluster)
    if not state['role_exists'] or not state['database_exists'] or not database_safe(state):
        raise BootstrapError('database_scope_validation_failed')
    report['database'] = {**state, 'runtime_login_verified': True}
    report['ready'] = report['eso']['read_coverage'] == 'confirmed'
    report['status'] = 'bootstrap_complete' if report['ready'] else 'credentials_database_ready_eso_review_needed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--kubeconfig', default='/etc/kubernetes/super-admin.conf')
    parser.add_argument('--database-host', default='postgres.data.svc.cluster.local')
    args = parser.parse_args(argv)
    try:
        result = bootstrap(Cluster(args.kubeconfig), apply=args.apply, database_host=args.database_host)
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0 if not args.apply or result.get('ready') else 2
    except Exception as error:
        code = str(error) if isinstance(error, BootstrapError) else 'bootstrap_unavailable'
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code):
            code = 'bootstrap_unavailable'
        print(json.dumps(dict(status='error', code=code), separators=(',', ':')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
