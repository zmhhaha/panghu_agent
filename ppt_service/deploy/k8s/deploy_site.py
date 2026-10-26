"""Operator-side PPT deployment. No worker, model call or external account setup."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
from urllib.parse import unquote, urlsplit
import uuid

import yaml


HERE = Path(__file__).resolve().parent
SERVICE = HERE.parents[1]
REPOSITORY = SERVICE.parents[1]
NAMESPACE = 'ppt-service'
SECRET_NAMES = ('ppt-postgres-bootstrap', 'ppt-database-migrator', 'ppt-database-runtime', 'ppt-oidc')
BOOTSTRAP_MARKER = 'ppt-service-bootstrap-v1'
IMAGE_PATTERN = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}\Z')
RUNTIME_CHECK = """import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ['DATABASE_URL'], connect_args={'connect_timeout': 5})
with engine.connect() as db:
    assert db.scalar(text('SELECT current_user')) == 'ppt_runtime'
    assert db.scalar(text('SELECT max(revision) FROM public.api_schema_revision')) == 2
    assert not db.scalar(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')"))
    assert not db.scalar(text("SELECT has_table_privilege(current_user, 'public.api_schema_revision', 'INSERT')"))
    assert not db.scalar(text("SELECT has_table_privilege(current_user, 'public.api_schema_revision', 'UPDATE')"))
    assert not db.scalar(text("SELECT has_table_privilege(current_user, 'public.api_schema_revision', 'DELETE')"))
engine.dispose()
"""
STORAGE_STATE = 'ppt.panghuer.top/state-layout'
STORAGE_UID = 'ppt.panghuer.top/state-claim-uid'
STORAGE_INIT = """import os, stat
root = '/var/lib/ppt-service'
parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    try:
        info = os.stat('state', dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        # Only a new empty volume may acquire the application directory.
        assert not os.listdir(parent), 'state_missing_on_nonempty_volume'
        os.mkdir('state', mode=0o700, dir_fd=parent)
        # A fresh child of the kubelet's setgid root inherits that bit. Clear
        # it only on the directory just created here, never on reused data.
        created = os.open('state', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(created, 0o700)
        finally:
            os.close(created)
        info = os.stat('state', dir_fd=parent, follow_symlinks=False)
finally:
    os.close(parent)
assert stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
assert (info.st_uid, info.st_gid) == (10000, 10000)
assert stat.S_IMODE(info.st_mode) == 0o700
assert os.access(root + '/state', os.R_OK | os.W_OK | os.X_OK)
"""


class DeployError(RuntimeError):
    pass


def load_documents(path):
    return [doc for doc in yaml.safe_load_all(Path(path).read_text(encoding='utf-8')) if doc]


def load_base():
    config = yaml.safe_load((HERE / 'base/kustomization.yaml').read_text(encoding='utf-8'))
    if config['namespace'] != NAMESPACE:
        raise DeployError('Unexpected deployment namespace')
    documents = []
    for filename in config['resources']:
        path = (HERE / 'base' / filename).resolve()
        if path.parent != HERE / 'base':
            raise DeployError('Base resources must be plain local files')
        documents.extend(load_documents(path))
    for doc in documents:
        if doc['kind'] != 'Namespace':
            doc['metadata']['namespace'] = NAMESPACE
    return documents


def image_digest(value):
    if not isinstance(value, str) or not IMAGE_PATTERN.fullmatch(value):
        raise DeployError('Use a pushed repository@sha256:<manifest digest> for PPT_SITE_IMAGE')
    return value


def validate_auth(documents):
    if len(documents) != 2:
        raise DeployError('PPT_AUTH_CONFIG must contain exactly ppt-auth and ppt-oidc')
    maps = {}
    for doc in documents:
        if doc.get('kind') != 'ConfigMap' or doc.get('metadata', {}).get('namespace') != NAMESPACE:
            raise DeployError('PPT authentication ConfigMaps have the wrong namespace or kind')
        maps[doc['metadata']['name']] = doc['data']
    if set(maps) != {'ppt-auth', 'ppt-oidc'}:
        raise DeployError('PPT_AUTH_CONFIG must contain exactly ppt-auth and ppt-oidc')
    auth = maps['ppt-auth']
    proxy = tomllib.loads(maps['ppt-oidc']['oauth2-proxy.cfg'])
    client = auth.get('OIDC_AUDIENCE', '')
    required_proxy = {
        'http_address': '0.0.0.0:4180', 'provider': 'oidc',
        'upstreams': ['http://127.0.0.1:8000/'], 'pass_host_header': True,
        'reverse_proxy': True, 'proxy_websockets': True, 'pass_authorization_header': True,
        'pass_access_token': False, 'api_routes': ['^/api/'],
        'cookie_name': '__Host-ppt', 'cookie_path': '/', 'cookie_secure': True,
        'cookie_httponly': True, 'cookie_samesite': 'lax',
        'cookie_csrf_per_request': True, 'request_logging': False, 'cookie_expire': '8h',
    }
    allowed_proxy_keys = set(required_proxy) | {
        'oidc_issuer_url', 'redirect_url', 'client_id', 'scope',
        'authenticated_emails_file', 'cookie_expire', 'skip_provider_button',
    }
    if (not isinstance(client, str) or not client or 'REQUIRED_' in client or proxy.get('client_id') != client
            or set(auth) != {'OIDC_ISSUER', 'OIDC_JWKS_URL', 'OIDC_AUDIENCE', 'ALLOWED_ORIGINS', 'INVITATION_EMAILS_FILE'}
            or auth.get('OIDC_JWKS_URL') != 'https://auth.panghuer.top/.well-known/jwks'
            or any(type(proxy.get(key)) is not type(value) or proxy.get(key) != value
                   for key, value in required_proxy.items())
            or set(proxy) - allowed_proxy_keys
            or proxy.get('scope') != 'openid email profile'
            or auth.get('INVITATION_EMAILS_FILE') != '/owner/emails'
            or proxy.get('authenticated_emails_file') != '/owner/emails'
            or 'INVITATION_SUBJECTS' in auth
            or auth.get('OIDC_ISSUER') != 'https://auth.panghuer.top'
            or proxy.get('oidc_issuer_url') != auth['OIDC_ISSUER']
            or auth.get('ALLOWED_ORIGINS') != 'https://ppt.panghuer.top'
            or proxy.get('redirect_url') != 'https://ppt.panghuer.top/oauth2/callback'
            or proxy.get('pass_authorization_header') is not True
            or proxy.get('email_domains')
            or any(proxy.get(key) for key in ('ssl_insecure_skip_verify',
                'insecure_oidc_skip_issuer_verification', 'insecure_oidc_allow_unverified_email'))):
        raise DeployError('PPT authentication configuration is incomplete or violates the shared contract')
    return [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
        'name': doc['metadata']['name'], 'namespace': NAMESPACE}, 'data': doc['data']} for doc in documents]


def validate_owner(documents):
    if (len(documents) != 1 or documents[0].get('kind') != 'ConfigMap'
            or documents[0].get('metadata', {}).get('name') != 'ppt-owner'
            or documents[0]['metadata'].get('namespace') != NAMESPACE
            or not isinstance(documents[0].get('data', {}).get('emails'), str)):
        raise DeployError('PPT_OWNER_CONFIG must contain ppt-owner.data.emails in ppt-service')
    return documents


def database_password(secret, role):
    """Validate the dedicated URL without logging it or passing it in argv."""
    try:
        raw = base64.b64decode(secret['data']['DATABASE_URL'], validate=True).decode('utf-8')
        if any(ord(character) < 32 or ord(character) == 127 for character in raw):
            raise ValueError()
        url = urlsplit(raw)
        if (url.scheme != 'postgresql+psycopg' or unquote(url.username or '') != role
                or url.hostname != 'ppt-postgres.ppt-service.svc.cluster.local'
                or url.port != 5432 or url.path != '/ppt_service' or url.query or url.fragment):
            raise ValueError()
        password = unquote(url.password or '')
        if not password or '\x00' in password or '\n' in password or '\r' in password:
            raise ValueError()
        return password
    except (KeyError, ValueError, TypeError, UnicodeError):
        raise DeployError('Invalid dedicated database URL; check role, driver, host, port and database in Vault') from None


def sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def make_job(image, phase, run_id='', *, initialize_volume=False, claim_uid=None):
    job = copy.deepcopy(load_documents(HERE / 'migrate-job/job.yaml')[0])
    job['metadata']['namespace'] = NAMESPACE
    job['metadata'].pop('annotations', None)
    container = job['spec']['template']['spec']['containers'][0]
    container['image'] = image_digest(image)
    if phase == 'runtime':
        container['command'] = ['/usr/local/bin/python', '-c', RUNTIME_CHECK]
        container['env'][0]['valueFrom']['secretKeyRef']['name'] = 'ppt-database-runtime'
    elif phase == 'storage':
        if not claim_uid:
            raise DeployError('Storage initialization requires the current PVC UID')
        container['command'] = ['/usr/local/bin/python', '-c', STORAGE_INIT]
        container['env'] = []
        job['spec']['template']['metadata']['labels']['app.kubernetes.io/name'] = 'ppt-storage-init'
        job['spec']['template']['metadata']['annotations'] = {STORAGE_UID: claim_uid}
        spec = job['spec']['template']['spec']
        spec['volumes'].append({'name': 'data', 'persistentVolumeClaim': {'claimName': 'ppt-data'}})
        container['volumeMounts'].append({'name': 'data', 'mountPath': '/var/lib/ppt-service'})
        if initialize_volume:
            spec['securityContext'].update(fsGroup=10000, fsGroupChangePolicy='OnRootMismatch')
    elif phase != 'migrate':
        raise DeployError('Unknown deployment Job phase')
    identity = json.dumps(job['spec'], sort_keys=True) + run_id
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
    job['metadata']['name'] = f'ppt-{phase}-{suffix}'
    return job


def matches_expected(expected, actual):
    """Kubernetes may add default fields, but cannot change reviewed inputs."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and matches_expected(value, actual[key])
                                               for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            matches_expected(left, right) for left, right in zip(expected, actual))
    return type(expected) is type(actual) and expected == actual


class Deployment:
    def __init__(self, *, runner=subprocess.run):
        self.runner = runner
        self.kubectl = os.getenv('KUBECTL', 'kubectl')
        self.lock_token = uuid.uuid4().hex
        self.locked = False

    def command(self, args, *, input=None):
        try:
            result = self.runner(args, input=input, text=True, capture_output=True, check=False)
        except OSError:
            raise DeployError('Required deployment command is unavailable') from None
        if result.returncode:
            # Child stderr can include SQL or connection strings. Keep it private.
            raise DeployError('Deployment command failed; inspect resource status without printing credentials')
        return result.stdout

    def kube(self, *args, input=None):
        return self.command([self.kubectl, *args], input=input)

    def get(self, kind, name, *, metadata_only=False):
        output = self.kube('-n', NAMESPACE, 'get', kind, name, '--ignore-not-found',
                           '-o', 'jsonpath={.metadata}' if metadata_only else 'json')
        return json.loads(output) if output.strip() else None

    def apply(self, documents):
        self.kube('apply', '-f', '-', input=yaml.safe_dump_all(documents, sort_keys=False))

    def storage_claim(self, template, *, existing_site):
        """Mark only helper-created dynamic claims for first-mount fsGroup.

        A pending marker is committed with PVC creation, so an interrupted first
        deployment can retry automatically. It is bound to the observed PVC UID
        before any Job runs, and becomes ready before the site can be started.
        Existing unmarked/restored claims never receive fsGroup.
        """
        claim = self.get('pvc', 'ppt-data')
        if claim is None:
            if any(key in template['spec'] for key in ('volumeName', 'selector', 'dataSource', 'dataSourceRef')):
                raise DeployError('Initial storage must use a new dynamically provisioned CephFS claim')
            template = copy.deepcopy(template)
            template['metadata'].setdefault('annotations', {})[STORAGE_STATE] = 'pending-v1'
            self.kube('create', '-f', '-', input=yaml.safe_dump(template, sort_keys=False))
            claim = self.get('pvc', 'ppt-data')
        uid = (claim or {}).get('metadata', {}).get('uid')
        if not uid:
            raise DeployError('Cannot determine the PPT data PVC identity')
        annotations = claim['metadata'].get('annotations', {})
        pending = annotations.get(STORAGE_STATE) == 'pending-v1'
        bound_uid = annotations.get(STORAGE_UID)
        if bound_uid and bound_uid != uid:
            raise DeployError('Storage initialization marker belongs to a different PVC UID')
        if pending:
            if existing_site or any(key in claim.get('spec', {}) for key in ('dataSource', 'dataSourceRef', 'selector')):
                raise DeployError('Pending storage initialization cannot reuse a restored volume or an existing site')
            self.patch_storage(uid, {STORAGE_UID: uid})
        return uid, pending

    def patch_storage(self, uid, annotations):
        claim = self.get('pvc', 'ppt-data')
        if (claim or {}).get('metadata', {}).get('uid') != uid:
            raise DeployError('The PPT data PVC identity changed during initialization')
        merged = dict(claim['metadata'].get('annotations', {}))
        merged.update(annotations)
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': uid}]
        if claim['metadata'].get('resourceVersion'):
            patch.append({'op': 'test', 'path': '/metadata/resourceVersion',
                          'value': claim['metadata']['resourceVersion']})
        patch.append({'op': 'add', 'path': '/metadata/annotations', 'value': merged})
        self.kube('-n', NAMESPACE, 'patch', 'pvc', 'ppt-data', '--type=json', '-p', json.dumps(patch))

    def lock(self):
        lock = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
            'name': 'ppt-deploy-lock', 'namespace': NAMESPACE}, 'data': {'token': self.lock_token}}
        try:
            self.kube('create', '-f', '-', input=json.dumps(lock))
        except DeployError:
            raise DeployError('Could not acquire ppt-deploy-lock; another deployment or an interrupted run needs review') from None
        self.locked = True

    def unlock(self):
        if self.locked:
            lock = self.get('configmap', 'ppt-deploy-lock')
            if lock and lock.get('data', {}).get('token') == self.lock_token:
                # UID precondition prevents deleting a replacement lock.
                self.command([self.kubectl, 'delete', '--raw',
                    f'/api/v1/namespaces/{NAMESPACE}/configmaps/ppt-deploy-lock', '-f', '-'],
                    input=json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions',
                        'preconditions': {'uid': lock['metadata']['uid']}}))
            self.locked = False

    def check_secret_owners(self):
        for name in SECRET_NAMES:
            metadata = self.get('secret', name, metadata_only=True)
            if metadata is None:
                continue
            external = self.get('externalsecret', name)
            expected_uid = (external or {}).get('metadata', {}).get('uid')
            owners = metadata.get('ownerReferences', [])
            if not expected_uid or not any(owner.get('kind') == 'ExternalSecret'
                    and owner.get('name') == name and owner.get('uid') == expected_uid for owner in owners):
                raise DeployError(f'Refusing to take over existing Secret {name}; check its ExternalSecret owner')

    def wait_secrets(self, *names):
        before = {name: (self.get('externalsecret', name) or {}).get('status', {}).get('refreshTime')
                  for name in names}
        self.kube('-n', NAMESPACE, 'annotate', 'externalsecret', *names,
                  'force-sync=' + self.lock_token, '--overwrite')
        try:
            self.kube('-n', NAMESPACE, 'wait', '--for=condition=Ready',
                      *['externalsecret/' + name for name in names], '--timeout=180s')
        except DeployError:
            # Describe only the fixed public mapping, never provider messages or values.
            vault_requirements = {
                'ppt-postgres-bootstrap': ('secret/ppt-service/postgres-bootstrap', 'POSTGRES_PASSWORD'),
                'ppt-database-migrator': ('secret/ppt-service/database-migrator', 'DATABASE_URL'),
                'ppt-database-runtime': ('secret/ppt-service/database-runtime', 'DATABASE_URL'),
                'ppt-oidc': ('secret/ppt-service/oidc', 'CLIENT_SECRET, COOKIE_SECRET'),
            }
            required = '; '.join(f'ExternalSecret/{name} -> {path} fields {fields}'
                                 for name, (path, fields) in vault_requirements.items() if name in names)
            raise DeployError(
                'ExternalSecret readiness wait failed in ppt-service. '
                'Check the Vault CLI paths/fields and ESO read access for the waited resources: '
                + required + '. See vault/inventory/PPT-SERVICE.md; deployment stopped.'
            ) from None
        pending = set(names)
        started = time.monotonic()
        deadline = started + 180
        retriggered = False
        while pending:
            for name in list(pending):
                status = (self.get('externalsecret', name) or {}).get('status', {})
                ready = any(condition.get('type') == 'Ready' and condition.get('status') == 'True'
                            for condition in status.get('conditions', []))
                if ready and status.get('refreshTime') and status['refreshTime'] != before[name]:
                    pending.remove(name)
            if pending:
                now = time.monotonic()
                if now >= deadline:
                    raise DeployError('ExternalSecret has not completed the requested refresh; website rollout stopped')
                # refreshTime can have only second precision. A successful sync
                # in the baseline second needs another distinct annotation to
                # produce an observable completion without accepting stale Ready.
                if not retriggered and now - started >= 2:
                    self.kube('-n', NAMESPACE, 'annotate', 'externalsecret', *sorted(pending),
                              'force-sync=' + self.lock_token + '-retry-1', '--overwrite')
                    retriggered = True
                time.sleep(2)

    def psql(self, sql, database='postgres', user='ppt_bootstrap'):
        return self.kube('-n', NAMESPACE, 'exec', '-i', 'ppt-postgres-0', '--',
            'psql', '-X', '--no-password', '--quiet', '--no-align', '--tuples-only',
            '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database, input=sql)

    def db_state(self):
        output = self.psql("""SELECT json_build_object(
          'database_exists', EXISTS(SELECT 1 FROM pg_database WHERE datname='ppt_service'),
          'owner', (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='ppt_service'),
          'bootstrap_marker', (SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname='ppt_service'),
          'roles', COALESCE((SELECT json_agg(json_build_object('name',rolname,'super',rolsuper,
            'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,
            'inherit',rolinherit,'login',rolcanlogin,
            'member',EXISTS(SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid))) FROM pg_roles
            WHERE rolname IN ('ppt_migrator','ppt_runtime')), '[]'::json));""")
        return json.loads(output)

    def initialize_database(self):
        state = self.db_state()
        if state['database_exists'] or state['roles']:
            self.validate_database_state(state)
            return False
        migrator = database_password(self.get('secret', 'ppt-database-migrator'), 'ppt_migrator')
        runtime = database_password(self.get('secret', 'ppt-database-runtime'), 'ppt_runtime')
        sql = (HERE / 'bootstrap-database.sql').read_text(encoding='utf-8')
        # Identifiers are fixed; no user-controlled SQL identifiers are interpolated.
        sql = sql.replace(':"database"', '"ppt_service"').replace(':database', 'ppt_service')
        sql = sql.replace(':"migrator"', '"ppt_migrator"').replace(':"runtime"', '"ppt_runtime"')
        sql = sql.replace(":'migrator_password'", sql_literal(migrator))
        sql = sql.replace(":'runtime_password'", sql_literal(runtime))
        try:
            self.psql(sql)
        except DeployError:
            raise DeployError('Database initialization failed; partial objects were preserved. Inspect the dedicated database before retrying') from None
        return True

    @staticmethod
    def validate_database_state(state):
        roles = {role['name']: role for role in state['roles']}
        if (not state['database_exists'] or set(roles) != {'ppt_migrator', 'ppt_runtime'}
                or state.get('bootstrap_marker') != BOOTSTRAP_MARKER):
            raise DeployError('Partial database initialization detected; existing database and roles were preserved for repair')
        if state['owner'] != 'ppt_migrator' or any(not role['login'] or role.get('member', False) or any(role[key]
                for key in ('super', 'createdb', 'createrole', 'replication', 'inherit')) for role in roles.values()):
            raise DeployError('Dedicated database ownership or role privileges do not match the PPT contract')

    def revision(self):
        self.validate_database_state(self.db_state())
        exists = self.psql("SELECT to_regclass('public.api_schema_revision') IS NOT NULL;", 'ppt_service').strip()
        if exists == 'f':
            return 0
        if exists != 't':
            raise DeployError('Cannot determine the target schema revision')
        revision = int(self.psql('SELECT coalesce(max(revision),0) FROM public.api_schema_revision;', 'ppt_service').strip())
        if revision not in (0, 1, 2):
            raise DeployError('Unsupported schema revision; refusing automatic migration')
        return revision

    def run_job(self, job, *, rerun_completed=False):
        job = copy.deepcopy(job)
        visited = set()
        while True:
            name = job['metadata']['name']
            if name in visited:
                raise DeployError('Migration Job history contains a repeated identity')
            visited.add(name)
            previous = self.get('job', name)
            if previous:
                if not matches_expected(job['spec'], previous.get('spec')):
                    raise DeployError(f'Existing Job {name} has different inputs; refusing to reuse or replace it')
                conditions = previous.get('status', {}).get('conditions', [])
                if any(c['type'] == 'Failed' and c['status'] == 'True' for c in conditions):
                    raise DeployError(f'Existing Job {name} failed; preserve its evidence and review before retrying')
                if any(c['type'] == 'Complete' and c['status'] == 'True' for c in conditions):
                    if not rerun_completed:
                        return
                    # A schema restored below revision 2 needs a new migration,
                    # even with the same image. Follow a stable receipt chain:
                    # retries find the same active/failed next Job, preserving
                    # both execution deduplication and all historical evidence.
                    uid = previous.get('metadata', {}).get('uid')
                    if not isinstance(uid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', uid):
                        raise DeployError('Completed migration Job has no valid UID for its successor')
                    identity = json.dumps(job['spec'], sort_keys=True) + 'after:' + uid
                    suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
                    job['metadata']['name'] = 'ppt-migrate-' + suffix
                    job['metadata'].setdefault('annotations', {}).update({
                        'ppt.panghuer.top/previous-job-name': name,
                        'ppt.panghuer.top/previous-job-uid': uid,
                    })
                    continue
            else:
                self.kube('create', '-f', '-', input=yaml.safe_dump(job, sort_keys=False))
            break
        try:
            self.kube('-n', NAMESPACE, 'wait', '--for=condition=Complete', 'job/' + name, '--timeout=330s')
        except DeployError:
            raise DeployError(f'Job {name} did not complete; inspect its Pod status without exposing credentials') from None

    def authentication(self):
        selected = os.getenv('PPT_AUTH_CONFIG')
        if selected:
            try:
                return validate_auth(load_documents(selected))
            except (DeployError, OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
                raise DeployError('PPT_AUTH_CONFIG could not be read or parsed as the required authentication configuration; check its YAML/TOML file') from None
        client_id = os.getenv('PPT_CLIENT_ID')
        if client_id:
            try:
                path = REPOSITORY / 'oauth/k8s/configure-ppt.py'
                spec = importlib.util.spec_from_file_location('ppt_client_config', path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return validate_auth(list(yaml.safe_load_all(module.render_configuration(client_id))))
            except (DeployError, OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
                raise DeployError('PPT_CLIENT_ID could not render valid authentication configuration; check the client ID and oauth/k8s/configure-ppt.py template') from None
        existing = [self.get('configmap', name) for name in ('ppt-auth', 'ppt-oidc')]
        if all(existing):
            return validate_auth(existing)
        raise DeployError('Set PPT_CLIENT_ID or PPT_AUTH_CONFIG for the first website deployment')

    def deploy(self, *, image):
        base = load_base()
        self.apply([doc for doc in base if doc['kind'] == 'Namespace'])
        self.lock()
        try:
            self.check_secret_owners()
            auth = self.authentication()
            existing_site = self.get('deployment', 'ppt-site')
            # The execution rollout explicitly enables these two public keys.
            # A routine website release preserves that operator choice.
            current_config = self.get('configmap', 'ppt-site')
            if current_config and current_config.get('data', {}).get('PPT_EXECUTION_ENABLED') == 'true':
                selected_backend=current_config['data'].get('PPT_EXECUTION_BACKEND')
                if selected_backend not in ('kubernetes','agent-service'):
                    raise DeployError('Enabled website execution requires an isolated configured backend')
                site_config = next(doc for doc in base if doc['kind'] == 'ConfigMap' and doc['metadata']['name'] == 'ppt-site')
                site_config['data'].update(PPT_EXECUTION_ENABLED='true', PPT_EXECUTION_BACKEND=selected_backend)
            target_replicas = max(1, (existing_site or {}).get('spec', {}).get('replicas', 1))
            owner_path = os.getenv('PPT_OWNER_CONFIG')
            try:
                owner = validate_owner(load_documents(owner_path or REPOSITORY / 'oauth/k8s/ppt-owner-configmap.yaml'))
            except (DeployError, OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
                raise DeployError('PPT_OWNER_CONFIG or its default oauth/k8s/ppt-owner-configmap.yaml could not be read or parsed as the required owner ConfigMap') from None
            data_claim = next(doc for doc in base if doc['kind'] == 'PersistentVolumeClaim' and doc['metadata']['name'] == 'ppt-data')
            claim_uid, initialize_volume = self.storage_claim(data_claim, existing_site=existing_site)
            self.apply([doc for doc in base if doc['kind'] not in ('Namespace', 'Deployment', 'StatefulSet')
                        and doc is not data_claim])
            self.apply(load_documents(REPOSITORY / 'vault/inventory/ppt-service-externalsecret.yaml'))
            print('Synchronizing the four Vault credentials and starting the dedicated database.', flush=True)
            self.wait_secrets(*SECRET_NAMES)
            for secret_name, role in (('ppt-database-migrator', 'ppt_migrator'),
                                      ('ppt-database-runtime', 'ppt_runtime')):
                database_password(self.get('secret', secret_name), role)
            postgres = next(doc for doc in base if doc['kind'] == 'StatefulSet')
            postgres['spec']['replicas'] = 1
            self.apply([postgres])
            self.kube('-n', NAMESPACE, 'rollout', 'status', 'statefulset/ppt-postgres', '--timeout=300s')
            print('Initializing a new dedicated database or reusing its complete existing setup.', flush=True)
            self.initialize_database()
            print('Checking and upgrading the schema revision.', flush=True)
            revision = self.revision()
            if revision != 2:
                if existing_site and existing_site['spec'].get('replicas', 1):
                    self.kube('-n', NAMESPACE, 'scale', 'deployment/ppt-site', '--replicas=0')
                    self.kube('-n', NAMESPACE, 'wait', '--for=delete', 'pod',
                              '-l', 'app.kubernetes.io/name=ppt-site', '--timeout=120s')
                self.run_job(make_job(image, 'migrate'), rerun_completed=True)
                if self.revision() != 2:
                    raise DeployError('Migration Job completed but the database is not at schema revision 2')
            self.psql((HERE / 'grant-runtime.sql').read_text(encoding='utf-8').replace(':"runtime"', '"ppt_runtime"'),
                      'ppt_service', 'ppt_migrator')
            print('Checking runtime database access and initializing the CephFS state directory.', flush=True)
            self.run_job(make_job(image, 'runtime', self.lock_token))
            try:
                self.run_job(make_job(image, 'storage', self.lock_token,
                                      initialize_volume=initialize_volume, claim_uid=claim_uid))
            except DeployError:
                raise DeployError('Storage initialization failed; retry deploy after resolving the Job/CSI error. Existing state must retain UID/GID 10000 and mode 0700; existing files are never changed') from None
            self.patch_storage(claim_uid, {STORAGE_STATE: 'ready-v1', STORAGE_UID: claim_uid})
            self.apply(auth)
            if owner_path or not self.get('configmap', 'ppt-owner'):
                self.apply(owner)
            self.apply(load_documents(REPOSITORY / 'oauth/k8s/ppt-oidc-egress.yaml'))
            site = next(doc for doc in base if doc['kind'] == 'Deployment')
            site['spec']['replicas'] = target_replicas
            site['spec']['template']['spec']['containers'][0]['image'] = image
            # Credentials and public envFrom values are read only on startup.
            site['spec']['template'].setdefault('metadata', {}).setdefault('annotations', {})['ppt.panghuer.top/config-refresh'] = self.lock_token
            self.apply([site])
            print('Waiting for the website and OAuth proxy rollout.', flush=True)
            self.kube('-n', NAMESPACE, 'rollout', 'status', 'deployment/ppt-site', '--timeout=300s')
            print(self.kube('-n', NAMESPACE, 'get', 'pods,svc,pvc'), end='', flush=True)
            print('Website deployed. Configure the documented Tunnel peer policies and ppt.panghuer.top entry. Execution admission follows the existing explicit website configuration.')
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                self.unlock()
            except DeployError:
                print('ERROR: deployment lock release failed; inspect ppt-deploy-lock before the next deployment', file=sys.stderr)
                if not failed:
                    raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='reuse PPT_SITE_IMAGE or rendered/image.txt')
    parser.add_argument('--dry-run', action='store_true', help='print the plan without building or contacting Kubernetes')
    args = parser.parse_args(argv)
    if os.getenv('NAMESPACE', NAMESPACE) != NAMESPACE:
        parser.error('This deployment is fixed to ppt-service')
    if args.dry_run:
        print('Plan: server build/verify/push (unless --skip-build); namespace and deployment lock; public OIDC client and four Vault credentials; PostgreSQL and automatic database initialization/reuse; schema migration; runtime and state initialization Jobs; website rollout. Casdoor/Tunnel remain administrator-managed. Pi execution remains disabled.')
        return 0
    try:
        if not args.skip_build:
            result = subprocess.run(['bash', str(SERVICE / 'build.sh')], check=False)
            if result.returncode:
                raise DeployError('Server image build/verification/push failed; deployment stopped')
        selected = os.getenv('PPT_SITE_IMAGE')
        if selected is None:
            try:
                selected = (SERVICE / 'rendered/image.txt').read_text(encoding='utf-8').strip()
            except (OSError, ValueError):
                raise DeployError('Default rendered/image.txt could not be read; run build.sh successfully or set PPT_SITE_IMAGE to the pushed image digest') from None
        image = image_digest(selected)
        Deployment().deploy(image=image)
    except (DeployError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError, tomllib.TOMLDecodeError):
        # Print only explicit controlled diagnostics, never raw parsing/subprocess errors.
        error = sys.exc_info()[1]
        print('ERROR: ' + (str(error) if isinstance(error, DeployError) else 'Invalid or unavailable deployment input; check configuration and resource status'), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
