"""Offline deployment safety contracts. Every command and Kubernetes call is fake."""
from __future__ import annotations

import base64
import builtins
import copy
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
import yaml


HELPER = Path(__file__).resolve().parents[2] / 'deploy/k8s/deploy_site.py'
spec = importlib.util.spec_from_file_location('ppt_deployment_under_test', HELPER)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
IMAGE = 'arm-cluster-master:5000/ppt-service-site@sha256:' + 'a' * 64
OTHER_IMAGE = 'arm-cluster-master:5000/ppt-service-site@sha256:' + 'b' * 64
NS = 'ppt-service'


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ('NAMESPACE', 'PPT_SITE_IMAGE', 'PPT_AUTH_CONFIG', 'PPT_CLIENT_ID', 'PPT_OWNER_CONFIG'):
        monkeypatch.delenv(name, raising=False)


class Runner:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if not self.replies:
            raise AssertionError('Unexpected command: ' + repr(args))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            code, stdout, stderr = reply
            return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply) if not isinstance(reply, str) else reply, stderr='')


def secret_url(url):
    return {'data': {'DATABASE_URL': base64.b64encode(url.encode()).decode()}}


def dedicated_url(role='ppt_migrator', password='synthetic%27password%5Cvalue'):
    return f'postgresql+psycopg://{role}:{password}@postgres.data.svc.cluster.local:5432/ppt_service'


def database_state():
    return {'database_exists': True, 'owner': 'ppt_migrator', 'bootstrap_marker': deploy.BOOTSTRAP_MARKER, 'roles': [
        {'name': name, 'super': False, 'createdb': False, 'createrole': False,
         'replication': False, 'inherit': False, 'login': True, 'member':False}
        for name in ('ppt_migrator', 'ppt_runtime')]}


class Scenario(deploy.Deployment):
    """Fake orchestration, while preserving the real deploy() control flow."""
    def __init__(self, *, revision=deploy.TARGET_SCHEMA_REVISION, existing_site=3, fail_secrets=False):
        super().__init__(runner=Runner())
        self.events = []
        self.applied = []
        self.jobs = []
        self.job_options = []
        self.revisions = [revision, deploy.TARGET_SCHEMA_REVISION]
        self.existing_site = existing_site
        self.fail_secrets = fail_secrets

    def apply(self, documents):
        self.applied.extend(copy.deepcopy(documents))
        self.events.append(('apply', [(d['kind'], d['metadata']['name']) for d in documents]))

    def lock(self):
        self.events.append(('lock',))

    def unlock(self):
        self.events.append(('unlock',))

    def check_secret_owners(self):
        self.events.append(('secret-owners',))

    def authentication(self):
        return []

    def wait_secrets(self, *names):
        self.events.append(('wait-secrets', names))
        if self.fail_secrets:
            raise deploy.DeployError('Synthetic unavailable Vault credentials')

    def kube(self, *args, input=None):
        self.events.append(('kube', args, input))
        return ''

    def get(self, kind, name, **kwargs):
        if kind == 'secret' and name in ('ppt-database-migrator', 'ppt-database-runtime'):
            return secret_url(dedicated_url(role='ppt_runtime' if name.endswith('runtime') else 'ppt_migrator'))
        if kind == 'deployment' and name == 'ppt-site' and self.existing_site is not None:
            return {'spec': {'replicas': self.existing_site}}
        return None

    def revision(self):
        self.events.append(('revision',))
        return self.revisions.pop(0)

    def run_job(self, job, **options):
        self.jobs.append(copy.deepcopy(job))
        self.job_options.append(options)

    def storage_claim(self, template, *, existing_site):
        self.events.append(('storage-claim',))
        return 'data-claim-uid', False

    def patch_storage(self, uid, annotations):
        self.events.append(('storage-patch', uid, annotations))

    def initialize_database(self):
        self.events.append(('initialize-database',))

    def psql(self, sql, database='postgres', user='appuser'):
        self.events.append(('psql', database, user, sql))
        return ''


def test_lock_is_namespaced_and_release_uses_uid_precondition():
    runner = Runner(['', {'metadata': {'uid': 'owned-uid'}, 'data': {'token': 'owned-token'}}, ''])
    instance = deploy.Deployment(runner=runner)
    instance.lock_token = 'owned-token'
    instance.lock()
    lock = json.loads(runner.calls[0][1]['input'])
    assert lock['metadata'] == {'name': 'ppt-deploy-lock', 'namespace': NS}
    assert instance.locked
    instance.unlock()
    assert not instance.locked
    args, kwargs = runner.calls[-1]
    assert args == [instance.kubectl, 'delete', '--raw', f'/api/v1/namespaces/{NS}/configmaps/ppt-deploy-lock', '-f', '-']
    assert json.loads(kwargs['input'])['preconditions'] == {'uid': 'owned-uid'}


@pytest.mark.parametrize('value', ['', {'metadata': {'uid': 'foreign'}, 'data': {'token': 'foreign-token'}}])
def test_unlock_never_deletes_missing_or_foreign_lock(value):
    runner = Runner([value])
    instance = deploy.Deployment(runner=runner)
    instance.locked = True
    instance.unlock()
    assert len(runner.calls) == 1
    assert not instance.locked


def test_failed_lock_is_not_claimed_or_deleted():
    runner = Runner([(1, '', 'Synthetic conflict')])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError, match='Could not acquire'):
        instance.lock()
    assert not instance.locked
    instance.unlock()
    assert len(runner.calls) == 1


@pytest.mark.parametrize('owner', [None, [], [{'kind': 'ExternalSecret', 'name': 'ppt-database-migrator', 'uid': 'wrong'}]])
def test_secret_owner_preflight_refuses_takeover(owner):
    metadata = {'name': 'ppt-database-migrator'}
    if owner is not None:
        metadata['ownerReferences'] = owner
    runner = Runner([metadata, {'metadata': {'uid': 'expected'}}])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError, match='Refusing to take over'):
        instance.check_secret_owners()
    assert all('apply' not in args and 'create' not in args for args, _ in runner.calls)
    assert runner.calls[0][0][-1] == 'jsonpath={.metadata}'


def test_secret_owner_matching_current_eso_uid_is_allowed():
    name = 'ppt-database-migrator'
    runner = Runner([{'ownerReferences': [{'kind': 'ExternalSecret', 'name': name, 'uid': 'expected'}]},
                     {'metadata': {'uid': 'expected'}}, '', ''])
    deploy.Deployment(runner=runner).check_secret_owners()
    assert not runner.replies


def eso_status(refresh, ready=True):
    return {'status': {'refreshTime':refresh,'conditions':[{'type':'Ready','status':'True' if ready else 'False'}]}}


@pytest.mark.parametrize(('names', 'required'), [
    (('ppt-database-runtime',), {
        'ppt-database-runtime': ('secret/ppt-service/database-runtime', 'DATABASE_URL'),
    }),
    (('ppt-database-migrator', 'ppt-database-runtime', 'ppt-oidc'), {
        'ppt-database-migrator': ('secret/ppt-service/database-migrator', 'DATABASE_URL'),
        'ppt-database-runtime': ('secret/ppt-service/database-runtime', 'DATABASE_URL'),
        'ppt-oidc': ('secret/ppt-service/oidc', 'CLIENT_SECRET', 'COOKIE_SECRET'),
    }),
])
def test_secret_wait_failure_explains_required_vault_inputs_without_exposing_stderr(names, required, capsys):
    marker = 'synthetic-private-provider-value-do-not-print'
    runner = Runner([*[eso_status('old') for _ in names], '', (1, marker, marker + '\n' + dedicated_url())])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError) as caught:
        instance.wait_secrets(*names)
    message = str(caught.value)
    assert 'readiness wait failed' in message
    assert NS in message and 'ESO read access' in message
    assert 'Vault CLI paths/fields' in message
    assert 'vault/inventory/PPT-SERVICE.md' in message
    for name, expected in required.items():
        assert 'ExternalSecret/' + name in message
        assert all(value in message for value in expected)
    for other in set(deploy.SECRET_NAMES) - set(names):
        assert 'ExternalSecret/' + other not in message
    assert marker not in message and dedicated_url() not in message
    assert capsys.readouterr() == ('', '')
    assert not runner.replies


def test_main_prints_controlled_secret_wait_guidance_without_provider_values(monkeypatch, capsys):
    marker = 'synthetic-private-provider-value-do-not-print'
    instance = deploy.Deployment(runner=Runner([eso_status('old'), '', (1, marker, marker + dedicated_url())]))
    instance.deploy = lambda **kwargs: instance.wait_secrets('ppt-database-runtime')
    monkeypatch.setattr(deploy, 'Deployment', lambda: instance)
    monkeypatch.setenv('PPT_SITE_IMAGE', IMAGE)
    assert deploy.main(['--skip-build']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'ERROR: ExternalSecret readiness wait failed' in captured.err
    assert 'ExternalSecret/ppt-database-runtime' in captured.err
    assert 'secret/ppt-service/database-runtime' in captured.err
    assert 'DATABASE_URL' in captured.err
    assert 'vault/inventory/PPT-SERVICE.md' in captured.err
    assert marker not in captured.err and dedicated_url() not in captured.err


def test_secret_refresh_waits_for_a_new_ready_refresh_not_old_ready(monkeypatch):
    runner=Runner([eso_status('old'),'', '',eso_status('old'),eso_status('new')])
    instance=deploy.Deployment(runner=runner)
    moments=iter([0,1])
    monkeypatch.setattr(deploy.time,'monotonic',lambda:next(moments))
    slept=[]
    monkeypatch.setattr(deploy.time,'sleep',slept.append)
    instance.wait_secrets('ppt-oidc')
    assert slept==[2]
    assert not runner.replies
    assert all(NS in args for args,_ in runner.calls)


def test_same_second_secret_refresh_retriggers_once_with_distinct_annotation(monkeypatch):
    runner=Runner([eso_status('same-second'),'', '',eso_status('same-second'),'',eso_status('later-second')])
    instance=deploy.Deployment(runner=runner)
    moments=iter([0,2])
    monkeypatch.setattr(deploy.time,'monotonic',lambda:next(moments))
    slept=[]
    monkeypatch.setattr(deploy.time,'sleep',slept.append)
    instance.wait_secrets('ppt-oidc')
    annotations=[args for args,_ in runner.calls if 'annotate' in args]
    assert len(annotations)==2
    assert 'force-sync='+instance.lock_token in annotations[0]
    assert 'force-sync='+instance.lock_token+'-retry-1' in annotations[1]
    assert slept==[2]
    assert not runner.replies


def test_retrigger_does_not_reset_deadline_or_accept_stale_ready(monkeypatch):
    runner=Runner([eso_status('old'),'', '',eso_status('old'),'',eso_status('old')])
    instance=deploy.Deployment(runner=runner)
    moments=iter([0,2,181])
    monkeypatch.setattr(deploy.time,'monotonic',lambda:next(moments))
    slept=[]
    monkeypatch.setattr(deploy.time,'sleep',slept.append)
    with pytest.raises(deploy.DeployError,match='requested refresh'):
        instance.wait_secrets('ppt-oidc')
    assert len([args for args,_ in runner.calls if 'annotate' in args])==2
    assert slept==[2]


@pytest.mark.parametrize('latest',[eso_status('old'),eso_status('new',ready=False)])
def test_secret_refresh_timeout_rejects_stale_or_unready_status(monkeypatch,latest):
    runner=Runner([eso_status('old'),'', '',latest])
    instance=deploy.Deployment(runner=runner)
    moments=iter([0,181])
    monkeypatch.setattr(deploy.time,'monotonic',lambda:next(moments))
    monkeypatch.setattr(deploy.time,'sleep',lambda _:pytest.fail('Expired deadline must not sleep'))
    with pytest.raises(deploy.DeployError,match='requested refresh'):
        instance.wait_secrets('ppt-oidc')


def test_deploy_requires_three_secrets_before_shared_database_initialization():
    instance = Scenario()
    instance.deploy(image=IMAGE)
    assert deploy.SECRET_NAMES == ('ppt-database-migrator', 'ppt-database-runtime', 'ppt-oidc')
    waits = [event for event in instance.events if event[0] == 'wait-secrets']
    assert waits == [('wait-secrets', deploy.SECRET_NAMES)]
    wait_index = instance.events.index(waits[0])
    assert wait_index < instance.events.index(('initialize-database',))
    assert any(document['kind'] == 'Deployment' and document['metadata']['name'] == 'ppt-site'
               for document in instance.applied)
    assert not any(document['kind'] == 'StatefulSet' or document['metadata'].get('namespace') == 'data'
                   or document['metadata']['name'] in ('ppt-postgres', 'ppt-postgres-bootstrap')
                   for document in instance.applied)
    commands = [event[1] for event in instance.events if event[0] == 'kube']
    assert not any('statefulset/ppt-postgres' in command or 'statefulset/postgres' in command
                   or 'data' in command for command in commands)
    assert instance.events[-1] == ('unlock',)


def test_secret_failure_releases_lock_before_database_initialization():
    instance = Scenario(fail_secrets=True)
    with pytest.raises(deploy.DeployError):
        instance.deploy(image=IMAGE)
    assert not any(document['kind'] == 'StatefulSet' for document in instance.applied)
    assert ('initialize-database',) not in instance.events
    assert not any(event[0] == 'psql' for event in instance.events)
    assert instance.events[-1] == ('unlock',)


def test_deploy_does_not_create_or_update_retired_owner_list():
    instance = Scenario()
    instance.deploy(image=IMAGE)
    assert not any(d['metadata']['name'] == 'ppt-owner' for d in instance.applied)


def test_legacy_owner_override_is_rejected_before_cluster_changes(monkeypatch):
    instance = Scenario()
    monkeypatch.setenv('PPT_OWNER_CONFIG', '/synthetic/private-owner.yaml')
    with pytest.raises(deploy.DeployError, match='PPT_OWNER_CONFIG is obsolete'):
        instance.deploy(image=IMAGE)
    assert instance.events == []
    assert not instance.runner.calls


@pytest.mark.parametrize('revision', [0, 1, 2, 3])
def test_migration_and_site_use_the_same_manifest_digest(revision):
    instance = Scenario(revision=revision, existing_site=0)
    instance.deploy(image=IMAGE)
    assert {job['spec']['template']['spec']['containers'][0]['image'] for job in instance.jobs} == {IMAGE}
    site = next(d for d in instance.applied if d['kind'] == 'Deployment')
    assert site['spec']['template']['spec']['containers'][0]['image'] == IMAGE
    assert sum(job['metadata']['name'].startswith('ppt-migrate-') for job in instance.jobs) == 1
    assert instance.job_options[0] == {'rerun_completed': True}


def test_current_schema_skips_migration_job():
    instance = Scenario(revision=deploy.TARGET_SCHEMA_REVISION)
    instance.deploy(image=IMAGE)
    assert not any(job['metadata']['name'].startswith('ppt-migrate-') for job in instance.jobs)


def test_incomplete_migration_does_not_release_the_website():
    instance = Scenario(revision=3)
    instance.revisions = [3, 3]
    with pytest.raises(deploy.DeployError, match='not at schema revision 4'):
        instance.deploy(image=IMAGE)
    assert len(instance.jobs) == 1
    assert instance.jobs[0]['metadata']['name'].startswith('ppt-migrate-')
    assert not any(document['kind'] == 'Deployment' for document in instance.applied)
    assert ('kube', ('-n', NS, 'scale', 'deployment/ppt-site', '--replicas=0'), None) in instance.events
    assert instance.events[-1] == ('unlock',)


@pytest.mark.parametrize('revision', [2, 3])
def test_older_schema_is_upgraded_before_runtime_access(revision):
    instance = Scenario(revision=revision)
    instance.deploy(image=IMAGE)
    assert instance.jobs[0]['metadata']['name'].startswith('ppt-migrate-')
    assert instance.job_options[0] == {'rerun_completed': True}


def test_runtime_probe_requires_review_append_only_privileges():
    assert 'ppt_acceptance_entries' in deploy.RUNTIME_CHECK
    assert 'ppt_acceptance_streams' in deploy.RUNTIME_CHECK
    assert "'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'" in deploy.RUNTIME_CHECK
    assert "'public.ppt_acceptance_entries', 'UPDATE'" in deploy.RUNTIME_CHECK


def test_existing_site_replica_count_is_preserved():
    instance = Scenario(existing_site=3)
    instance.deploy(image=IMAGE)
    site = next(d for d in instance.applied if d['kind'] == 'Deployment')
    assert site['spec']['replicas'] == 3


@pytest.mark.parametrize('replacement', [
    'postgresql://ppt_migrator:synthetic@postgres.data.svc.cluster.local:5432/ppt_service',
    dedicated_url().replace('ppt_migrator:', 'ppt_runtime:'), dedicated_url().replace(':5432/', ':5433/'),
    dedicated_url().replace('postgres.data.', 'other-postgres.data.'),
    dedicated_url().replace('postgres.data.', 'postgres.other.'),
    dedicated_url().replace('postgres.data.svc.cluster.local', 'ppt-postgres.ppt-service.svc.cluster.local'),
    dedicated_url().replace('/ppt_service', '/other'), dedicated_url().replace('/ppt_service', '/appdb'),
    dedicated_url().replace('ppt_migrator:', 'appuser:'),
    dedicated_url() + '?sslmode=disable', dedicated_url() + '#fragment', dedicated_url(password=''),
    dedicated_url(password='synthetic%00password'), dedicated_url(password='synthetic%0Apassword'),
], ids=['driver','role','port','host','namespace','legacy-host','database','shared-default-database',
        'shared-admin-role','query','fragment','empty-password','nul','encoded-newline'])
def test_database_url_rejects_noncontract_targets(replacement):
    with pytest.raises(deploy.DeployError):
        deploy.database_password(secret_url(replacement), 'ppt_migrator')


@pytest.mark.parametrize('character', ['\r', '\n', '\t'])
def test_database_url_rejects_raw_control_characters(character):
    with pytest.raises(deploy.DeployError):
        deploy.database_password(secret_url(dedicated_url(password='synthetic' + character + 'password')), 'ppt_migrator')


def test_database_password_decodes_reserved_characters_without_logging(capsys):
    assert deploy.database_password(secret_url(dedicated_url()), 'ppt_migrator') == "synthetic'password\\value"
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize(('database', 'user'), [('postgres', 'appuser'), ('ppt_service', 'ppt_migrator')])
def test_operator_psql_uses_existing_shared_pod_and_stdin(database, user):
    runner = Runner(['synthetic-query-result'])
    instance = deploy.Deployment(runner=runner)
    sql = 'SELECT current_database();'
    if user == 'appuser':
        result = instance.psql(sql)
    else:
        result = instance.psql(sql, database, user)
    assert result == 'synthetic-query-result'
    args, kwargs = runner.calls[0]
    assert args == [instance.kubectl, '-n', 'data', 'exec', '-i', 'postgres-0', '--',
                    'psql', '-X', '--no-password', '--quiet', '--no-align', '--tuples-only',
                    '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database]
    assert kwargs['input'] == sql
    assert sql not in args


@pytest.mark.parametrize('state', [
    {'database_exists': True, 'roles': []}, {'database_exists': False, 'roles': [{'name': 'ppt_migrator'}]},
])
def test_automatic_init_refuses_partial_database(state):
    instance = deploy.Deployment(runner=Runner())
    instance.db_state = lambda: state
    instance.get = lambda *args, **kwargs: pytest.fail('Existing initialization must not fetch credentials')
    with pytest.raises(deploy.DeployError, match='Partial database initialization'):
        instance.initialize_database()


def test_automatic_init_reuses_complete_database_without_fetching_or_changing_passwords():
    instance = deploy.Deployment(runner=Runner())
    instance.db_state = database_state
    instance.get = lambda *args, **kwargs: pytest.fail('Existing database credentials must not be rewritten')
    assert instance.initialize_database() is False


def test_automatic_init_creates_new_database_and_roles_from_vault_urls():
    runner = Runner([''])
    instance = deploy.Deployment(runner=runner)
    instance.db_state = lambda: {'database_exists': False, 'roles': []}
    instance.get = lambda _kind, name: secret_url(dedicated_url(role='ppt_runtime' if name.endswith('runtime') else 'ppt_migrator'))
    assert instance.initialize_database() is True
    args, kwargs = runner.calls[0]
    assert args[1:7] == ['-n', 'data', 'exec', '-i', 'postgres-0', '--']
    assert args[-4:-2] == ['-U', 'appuser']
    assert args[-2:] == ['-d', 'postgres']
    assert 'CREATE DATABASE "ppt_service" OWNER "ppt_migrator";' in kwargs['input']
    sql = kwargs['input']
    assert 'CREATE ROLE "ppt_runtime" LOGIN' in sql
    assert sql.count("PASSWORD 'synthetic''password\\value'") == 2
    assert 'ALTER ROLE' not in sql
    assert 'appdb' not in sql and 'appuser' not in sql
    assert 'synthetic' not in ' '.join(args)
    assert ":'migrator_password'" not in sql and ":'runtime_password'" not in sql
    assert sql.rstrip().endswith("COMMENT ON DATABASE \"ppt_service\" IS 'ppt-service-bootstrap-v1';")
    assert sql.index('SET standard_conforming_strings = on;') < sql.index('CREATE ROLE')


@pytest.mark.parametrize('marker', [None, '', 'unrelated-database'])
def test_existing_roles_and_database_without_completion_marker_remain_partial(marker):
    state = database_state()
    state['bootstrap_marker'] = marker
    instance = deploy.Deployment(runner=Runner())
    instance.db_state = lambda: state
    instance.get = lambda *args, **kwargs: pytest.fail('Partial bootstrap must not rewrite existing passwords')
    with pytest.raises(deploy.DeployError, match='Partial database initialization'):
        instance.initialize_database()


def test_db_inventory_reads_bootstrap_marker_from_database_comment():
    runner = Runner([database_state()])
    assert deploy.Deployment(runner=runner).db_state() == database_state()
    query = runner.calls[0][1]['input']
    assert "'bootstrap_marker'" in query
    assert "shobj_description(oid, 'pg_database')" in query


def test_init_passwords_are_only_in_sql_stdin_and_failures_hide_stderr(capsys):
    marker = "synthetic'password\\value"
    runner = Runner([(1, '', marker + '\n' + dedicated_url())])
    instance = deploy.Deployment(runner=runner)
    instance.db_state = lambda: {'database_exists': False, 'roles': []}
    instance.get = lambda _kind, name: secret_url(dedicated_url(role='ppt_runtime' if name.endswith('runtime') else 'ppt_migrator'))
    with pytest.raises(deploy.DeployError, match='partial objects were preserved') as caught:
        instance.initialize_database()
    args, kwargs = runner.calls[0]
    assert marker not in repr(args) and dedicated_url() not in repr(args)
    assert "PASSWORD 'synthetic''password\\value'" in kwargs['input']
    assert kwargs['capture_output'] is True
    assert marker not in str(caught.value) and 'postgresql' not in str(caught.value)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('revision', [0, 1, 2, 3, 4])
def test_supported_schema_revisions(revision):
    runner = Runner(['t', str(revision)])
    instance = deploy.Deployment(runner=runner)
    instance.db_state = database_state
    assert instance.revision() == revision


def test_absent_schema_revision_table_is_initial_revision_zero():
    instance = deploy.Deployment(runner=Runner(['f']))
    instance.db_state = database_state
    assert instance.revision() == 0


@pytest.mark.parametrize('revision', [5, -1])
def test_unsupported_schema_revision_is_rejected(revision):
    instance = deploy.Deployment(runner=Runner(['t', str(revision)]))
    instance.db_state = database_state
    with pytest.raises(deploy.DeployError, match='Unsupported schema revision'):
        instance.revision()


def test_existing_failed_job_is_preserved():
    job = deploy.make_job(IMAGE, 'migrate')
    previous = copy.deepcopy(job)
    previous['status'] = {'conditions': [{'type': 'Failed', 'status': 'True'}]}
    runner = Runner([previous])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError, match='preserve its evidence'):
        instance.run_job(job)
    assert len(runner.calls) == 1


def test_existing_active_job_is_waited_not_recreated():
    job = deploy.make_job(IMAGE, 'migrate')
    previous = copy.deepcopy(job)
    previous['status'] = {'active': 1}
    runner = Runner([previous, ''])
    instance = deploy.Deployment(runner=runner)
    instance.run_job(job)
    assert len(runner.calls) == 2
    assert 'wait' in runner.calls[1][0] and 'job/' + job['metadata']['name'] in runner.calls[1][0]


def test_completed_job_with_different_image_is_rejected():
    job = deploy.make_job(IMAGE, 'migrate')
    previous = copy.deepcopy(job)
    previous['spec']['template']['spec']['containers'][0]['image'] = OTHER_IMAGE
    previous['status'] = {'conditions': [{'type': 'Complete', 'status': 'True'}]}
    instance = deploy.Deployment(runner=Runner([previous]))
    with pytest.raises(deploy.DeployError):
        instance.run_job(job)


def test_completed_matching_job_allows_server_defaults_and_controller_labels():
    job=deploy.make_job(IMAGE,'migrate')
    previous=copy.deepcopy(job)
    previous['spec']['selector']={'matchLabels':{'batch.kubernetes.io/controller-uid':'synthetic-controller'}}
    previous['spec']['template']['metadata']['labels']['batch.kubernetes.io/controller-uid']='synthetic-controller'
    previous['spec']['template']['spec']['dnsPolicy']='ClusterFirst'
    previous['status']={'conditions':[{'type':'Complete','status':'True'}]}
    runner=Runner([previous])
    deploy.Deployment(runner=runner).run_job(job)
    assert len(runner.calls)==1


def completed_migration(uid, *, after_uid=None):
    job = deploy.make_job(IMAGE, 'migrate', '' if after_uid is None else 'after:' + after_uid)
    job['metadata']['uid'] = uid
    job['status'] = {'conditions': [{'type': 'Complete', 'status': 'True'}]}
    return job


def test_restored_old_schema_does_not_reuse_same_image_completed_migration_receipt():
    job = deploy.make_job(IMAGE, 'migrate')
    original = copy.deepcopy(job)
    previous = completed_migration('previous-migration-uid')
    runner = Runner([previous, '', '', ''])
    deploy.Deployment(runner=runner).run_job(job, rerun_completed=True)
    created = yaml.safe_load(next(kwargs['input'] for args, kwargs in runner.calls if 'create' in args))
    expected = deploy.make_job(IMAGE, 'migrate', 'after:previous-migration-uid')
    assert created['metadata']['name'] == expected['metadata']['name'] != job['metadata']['name']
    assert created['spec'] == job['spec']
    assert created['metadata']['annotations'] == {
        'ppt-service.panghuer.top/previous-job-name': job['metadata']['name'],
        'ppt-service.panghuer.top/previous-job-uid': 'previous-migration-uid',
    }
    assert 'job/' + created['metadata']['name'] in runner.calls[-1][0]
    assert all('delete' not in args for args, _ in runner.calls)
    assert job == original


def test_restored_schema_follows_completed_history_until_an_active_migration():
    root = completed_migration('first-migration-uid')
    second = completed_migration('second-migration-uid', after_uid='first-migration-uid')
    active = deploy.make_job(IMAGE, 'migrate', 'after:second-migration-uid')
    active['status'] = {'active': 1}
    runner = Runner([root, second, active, ''])
    deploy.Deployment(runner=runner).run_job(deploy.make_job(IMAGE, 'migrate'), rerun_completed=True)
    gets = [args[args.index('get') + 2] for args, _ in runner.calls if 'get' in args]
    assert gets == [root['metadata']['name'], second['metadata']['name'], active['metadata']['name']]
    assert 'job/' + active['metadata']['name'] in runner.calls[-1][0]
    assert not any('create' in args or 'delete' in args for args, _ in runner.calls)


def test_failed_successor_is_preserved_instead_of_starting_a_parallel_migration():
    previous = completed_migration('previous-migration-uid')
    failed = deploy.make_job(IMAGE, 'migrate', 'after:previous-migration-uid')
    failed['status'] = {'conditions': [{'type': 'Failed', 'status': 'True'}]}
    runner = Runner([previous, failed])
    with pytest.raises(deploy.DeployError, match='preserve its evidence'):
        deploy.Deployment(runner=runner).run_job(deploy.make_job(IMAGE, 'migrate'), rerun_completed=True)
    assert len(runner.calls) == 2
    assert not any('create' in args or 'delete' in args for args, _ in runner.calls)


def test_interrupted_migration_retry_waits_for_the_same_successor_job():
    previous = completed_migration('previous-migration-uid')
    interrupted = Runner([previous, '', '', (1, '', 'synthetic wait interrupted')])
    with pytest.raises(deploy.DeployError, match='did not complete'):
        deploy.Deployment(runner=interrupted).run_job(deploy.make_job(IMAGE, 'migrate'), rerun_completed=True)
    created = yaml.safe_load(next(kwargs['input'] for args, kwargs in interrupted.calls if 'create' in args))
    created['status'] = {'active': 1}
    resumed = Runner([previous, created, ''])
    deploy.Deployment(runner=resumed).run_job(deploy.make_job(IMAGE, 'migrate'), rerun_completed=True)
    assert 'job/' + created['metadata']['name'] in resumed.calls[-1][0]
    assert not any('create' in args or 'delete' in args for args, _ in resumed.calls)


def test_completed_migration_without_uid_cannot_start_an_untraceable_successor():
    previous = completed_migration('temporary')
    del previous['metadata']['uid']
    runner = Runner([previous])
    with pytest.raises(deploy.DeployError, match='valid UID'):
        deploy.Deployment(runner=runner).run_job(deploy.make_job(IMAGE, 'migrate'), rerun_completed=True)
    assert len(runner.calls) == 1


def test_job_wait_failure_keeps_job_and_hides_child_stderr():
    job=deploy.make_job(IMAGE,'migrate')
    previous=copy.deepcopy(job)
    previous['status']={'active':1}
    runner=Runner([previous,(1,'','SYNTHETIC_PRIVATE_DB_URL')])
    with pytest.raises(deploy.DeployError) as caught:
        deploy.Deployment(runner=runner).run_job(job)
    assert 'SYNTHETIC_PRIVATE_DB_URL' not in str(caught.value)
    assert all('delete' not in args for args,_ in runner.calls)


@pytest.mark.parametrize('initialize_volume', [False, True])
def test_storage_job_is_nonroot_and_has_no_db_credentials(initialize_volume):
    job = deploy.make_job(IMAGE, 'storage', claim_uid='data-claim-uid', initialize_volume=initialize_volume)
    spec = job['spec']['template']['spec']
    container = spec['containers'][0]
    assert spec['automountServiceAccountToken'] is False
    assert spec['securityContext']['runAsNonRoot'] is True
    assert spec['securityContext']['runAsUser'] == 10000
    if initialize_volume:
        assert spec['securityContext']['fsGroup'] == 10000
        assert spec['securityContext']['fsGroupChangePolicy'] == 'OnRootMismatch'
    else:
        assert 'fsGroup' not in spec['securityContext']
    assert container['env'] == []
    assert next(v for v in spec['volumes'] if v['name'] == 'data')['persistentVolumeClaim'] == {'claimName': 'ppt-data'}
    assert next(v for v in container['volumeMounts'] if v['name'] == 'data') == {'name': 'data', 'mountPath': '/var/lib/ppt-service'}
    assert job['spec']['template']['metadata']['annotations'] == {deploy.STORAGE_UID: 'data-claim-uid'}
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert container['securityContext']['capabilities']['drop'] == ['ALL']


def run_storage_script(*, mode=stat.S_IFDIR|0o700, uid=10000, gid=10000, accessible=True, missing=False, contents=()):
    state = SimpleNamespace(mode=mode, missing=missing, calls=[])
    def state_stat(*args, **kwargs):
        if state.missing:
            raise FileNotFoundError()
        return SimpleNamespace(st_mode=state.mode, st_uid=uid, st_gid=gid)
    def mkdir(*args, **kwargs):
        state.calls.append(('mkdir', args, kwargs))
        state.missing = False
        state.mode = stat.S_IFDIR|0o2700
    def fchmod(fd, permissions):
        state.calls.append(('fchmod', fd, permissions))
        state.mode = stat.S_IFDIR|permissions
    fake_os = SimpleNamespace(open=lambda *a, **k: 10, close=lambda *a: None,
        stat=state_stat, listdir=lambda *a: list(contents), mkdir=mkdir, fchmod=fchmod,
        access=lambda *a: accessible, O_RDONLY=0, O_DIRECTORY=1, O_NOFOLLOW=2, R_OK=4, W_OK=2, X_OK=1)
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        return fake_os if name == 'os' else real_import(name, *args, **kwargs)
    namespace = {'__builtins__': vars(builtins)|{'__import__': fake_import}}
    return state, lambda: exec(deploy.STORAGE_INIT, namespace)


@pytest.mark.parametrize('mode,uid,gid,accessible,accepted', [
    (stat.S_IFDIR|0o700,10000,10000,True,True), (stat.S_IFLNK|0o700,10000,10000,True,False),
    (stat.S_IFDIR|0o777,10000,10000,True,False), (stat.S_IFDIR|0o700,0,0,True,False),
    (stat.S_IFDIR|0o700,10000,10000,False,False),
], ids=['private-owner-directory','symlink','broad-mode','wrong-owner','not-accessible'])
def test_storage_init_checks_existing_state_without_permission_changes(mode,uid,gid,accessible,accepted):
    state, execute = run_storage_script(mode=mode,uid=uid,gid=gid,accessible=accessible)
    if accepted:
        execute()
    else:
        with pytest.raises(AssertionError):
            execute()
    assert state.calls == []


def test_storage_init_creates_only_a_private_state_directory_and_clears_inherited_setgid():
    state, execute = run_storage_script(missing=True)
    execute()
    assert state.calls == [('mkdir', ('state',), {'mode': 0o700, 'dir_fd': 10}), ('fchmod', 10, 0o700)]
    assert state.mode == stat.S_IFDIR|0o700


def test_storage_init_does_not_adopt_a_nonempty_volume_with_no_state():
    state, execute = run_storage_script(missing=True, contents=('old-frozen-data',))
    with pytest.raises(AssertionError, match='state_missing_on_nonempty_volume'):
        execute()
    assert state.calls == []


def data_claim(*, pending=False, ready=False, bound_uid=None):
    annotations = {'ppt-service.panghuer.top/retention': 'keep'}
    if pending or ready:
        annotations[deploy.STORAGE_STATE] = 'pending-v1' if pending else 'ready-v1'
    if bound_uid:
        annotations[deploy.STORAGE_UID] = bound_uid
    return {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
            'metadata': {'name': 'ppt-data', 'namespace': NS, 'uid': 'data-claim-uid',
                         'resourceVersion': '42', 'annotations': annotations},
            'spec': {'storageClassName': 'ceph-cephfs', 'accessModes': ['ReadWriteMany']}}


def data_claim_template():
    return next(document for document in deploy.load_base()
                if document['kind'] == 'PersistentVolumeClaim' and document['metadata']['name'] == 'ppt-data')


def test_new_dynamic_claim_is_marked_pending_before_any_job_and_bound_to_uid():
    claim = data_claim(pending=True)
    runner = Runner(['', '', claim, claim, ''])
    instance = deploy.Deployment(runner=runner)
    assert instance.storage_claim(data_claim_template(), existing_site=None) == ('data-claim-uid', True)
    created = yaml.safe_load(runner.calls[1][1]['input'])
    assert created['metadata']['annotations'][deploy.STORAGE_STATE] == 'pending-v1'
    assert 'volumeName' not in created['spec']
    patch = json.loads(runner.calls[-1][0][-1])
    assert patch[0] == {'op': 'test', 'path': '/metadata/uid', 'value': 'data-claim-uid'}
    assert patch[1] == {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '42'}
    assert patch[-1]['value'][deploy.STORAGE_UID] == 'data-claim-uid'
    assert patch[-1]['value']['ppt-service.panghuer.top/retention'] == 'keep'


@pytest.mark.parametrize('bound_uid', [None, 'data-claim-uid'])
def test_interrupted_pending_claim_is_resumed_with_same_uid_and_fsgroup(bound_uid):
    claim = data_claim(pending=True, bound_uid=bound_uid)
    claim['spec']['volumeName'] = 'dynamically-provisioned-volume'
    runner = Runner([claim, claim, ''])
    instance = deploy.Deployment(runner=runner)
    assert instance.storage_claim(data_claim_template(), existing_site=None) == ('data-claim-uid', True)
    assert not any('create' in args for args, _ in runner.calls)


@pytest.mark.parametrize('claim', [data_claim(), data_claim(ready=True, bound_uid='data-claim-uid')])
def test_existing_ready_or_restored_claim_never_requests_fsgroup(claim):
    instance = deploy.Deployment(runner=Runner([claim]))
    assert instance.storage_claim(data_claim_template(), existing_site={'spec': {'replicas': 1}}) == ('data-claim-uid', False)


def test_legacy_ready_storage_markers_preserve_existing_volume_and_migrate_atomically():
    claim = data_claim()
    claim['metadata']['annotations'].update({deploy.LEGACY_STORAGE_STATE: 'ready-v1',
                                            deploy.LEGACY_STORAGE_UID: 'data-claim-uid'})
    runner = Runner([claim, claim, ''])
    instance = deploy.Deployment(runner=runner)
    assert instance.storage_claim(data_claim_template(), existing_site={'spec': {'replicas': 1}}) == ('data-claim-uid', False)
    instance.patch_storage('data-claim-uid', {deploy.STORAGE_STATE: 'ready-v1', deploy.STORAGE_UID: 'data-claim-uid'})
    patch = json.loads(runner.calls[-1][0][-1])
    assert patch[:2] == [{'op': 'test', 'path': '/metadata/uid', 'value': 'data-claim-uid'},
                        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '42'}]
    assert patch[-1]['value'][deploy.STORAGE_STATE] == 'ready-v1'
    assert patch[-1]['value'][deploy.STORAGE_UID] == 'data-claim-uid'
    assert deploy.LEGACY_STORAGE_STATE not in patch[-1]['value']
    assert deploy.LEGACY_STORAGE_UID not in patch[-1]['value']
    assert not any('create' in args for args, _ in runner.calls)


@pytest.mark.parametrize('key,legacy_key,new_value,old_value', [
    (deploy.STORAGE_STATE, deploy.LEGACY_STORAGE_STATE, 'ready-v1', 'pending-v1'),
    (deploy.STORAGE_UID, deploy.LEGACY_STORAGE_UID, 'data-claim-uid', 'other-claim-uid'),
])
def test_conflicting_legacy_storage_markers_are_rejected_before_write(key,legacy_key,new_value,old_value):
    claim = data_claim()
    claim['metadata']['annotations'].update({key: new_value, legacy_key: old_value})
    runner = Runner([claim, claim])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError, match='Conflicting old and new'):
        instance.storage_claim(data_claim_template(), existing_site=None)
    with pytest.raises(deploy.DeployError, match='Conflicting old and new'):
        instance.patch_storage('data-claim-uid', {deploy.STORAGE_UID: 'data-claim-uid'})
    assert not any('patch' in args or 'create' in args for args, _ in runner.calls)


@pytest.mark.parametrize('change', ['different-uid', 'existing-site', 'data-source', 'selector'])
def test_pending_claim_cannot_adopt_another_volume_or_existing_site(change):
    claim = data_claim(pending=True, bound_uid='other-uid' if change == 'different-uid' else 'data-claim-uid')
    if change == 'data-source':
        claim['spec']['dataSource'] = {'kind': 'VolumeSnapshot', 'name': 'frozen-data'}
    if change == 'selector':
        claim['spec']['selector'] = {'matchLabels': {'restored': 'true'}}
    instance = deploy.Deployment(runner=Runner([claim]))
    with pytest.raises(deploy.DeployError):
        instance.storage_claim(data_claim_template(), existing_site={} if change != 'existing-site' else {'spec': {'replicas': 0}})


@pytest.mark.parametrize('field', ['volumeName', 'selector', 'dataSource', 'dataSourceRef'])
def test_first_claim_initialization_rejects_preloaded_sources(field):
    template = data_claim_template()
    template['spec'][field] = 'preloaded'
    runner = Runner([''])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError, match='dynamically provisioned'):
        instance.storage_claim(template, existing_site=None)
    assert len(runner.calls) == 1


def test_storage_ready_mark_refuses_pvc_replacement():
    claim = data_claim(pending=True, bound_uid='data-claim-uid')
    claim['metadata']['uid'] = 'replacement-uid'
    runner = Runner([claim])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError, match='identity changed'):
        instance.patch_storage('data-claim-uid', {deploy.STORAGE_STATE: 'ready-v1'})
    assert len(runner.calls) == 1


def test_storage_is_marked_ready_before_site_rollout():
    instance = Scenario()
    instance.deploy(image=IMAGE)
    ready = ('storage-patch', 'data-claim-uid', {deploy.STORAGE_STATE: 'ready-v1', deploy.STORAGE_UID: 'data-claim-uid'})
    site_index = next(i for i, event in enumerate(instance.events)
                      if event[0] == 'apply' and ('Deployment', 'ppt-site') in event[1])
    assert instance.events.index(ready) < site_index


def configure_main(monkeypatch,tmp_path,receipt=IMAGE):
    service = tmp_path/'service'
    (service/'rendered').mkdir(parents=True)
    (service/'rendered/image.txt').write_text(receipt,encoding='utf-8')
    stub = SimpleNamespace(commands=[],deployments=[])
    def command(args,**kwargs):
        stub.commands.append((args,kwargs))
        return 'Synthetic build progress\n'
    def execute(**kwargs):
        stub.deployments.append(kwargs)
    stub.command=command
    stub.deploy=execute
    monkeypatch.setattr(deploy,'SERVICE',service)
    monkeypatch.setattr(deploy,'Deployment',lambda:stub)
    def build(args,**kwargs):
        stub.commands.append((args,kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(deploy.subprocess,'run',build)
    return stub,service


def test_default_main_builds_then_deploys_digest_receipt(monkeypatch,tmp_path):
    stub,service=configure_main(monkeypatch,tmp_path)
    assert deploy.main([])==0
    assert stub.commands==[(['bash',str(service/'build.sh')],{'check':False})]
    assert stub.deployments==[{'image':IMAGE}]


@pytest.mark.parametrize('value',[IMAGE,'arm-cluster-master:5000/ppt-service-site:latest'])
def test_skip_build_uses_only_valid_manifest_digest(monkeypatch,tmp_path,value):
    stub,_=configure_main(monkeypatch,tmp_path,receipt=value)
    result=deploy.main(['--skip-build'])
    assert stub.commands==[]
    assert result==(0 if value==IMAGE else 1)
    assert bool(stub.deployments)==(value==IMAGE)


@pytest.mark.parametrize('failure', ['unreadable', 'yaml', 'toml', 'shape'])
def test_auth_input_diagnostic_names_override_without_echoing_private_path_or_content(monkeypatch, tmp_path, capsys, failure):
    marker = 'synthetic-private-auth-input-do-not-print'
    path = tmp_path / (marker + '.yaml')
    if failure == 'yaml':
        path.write_text('secret: ["' + marker, encoding='utf-8')
    elif failure == 'toml':
        documents = deploy.load_documents(deploy.REPOSITORY / 'oauth/k8s/ppt-proxy-configmap.yaml')
        for document in documents:
            if document['metadata']['name'] == 'ppt-oidc':
                document['data']['oauth2-proxy.cfg'] = 'secret = ["' + marker
        path.write_text(yaml.safe_dump_all(documents), encoding='utf-8')
    elif failure == 'shape':
        path.write_text(yaml.safe_dump_all([marker, marker]), encoding='utf-8')
    instance = deploy.Deployment(runner=Runner())
    instance.deploy = lambda **kwargs: instance.authentication()
    monkeypatch.setattr(deploy, 'Deployment', lambda: instance)
    monkeypatch.setenv('PPT_SITE_IMAGE', IMAGE)
    monkeypatch.setenv('PPT_AUTH_CONFIG', str(path))
    assert deploy.main(['--skip-build']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'ERROR: PPT_AUTH_CONFIG could not be read or parsed' in captured.err
    assert marker not in captured.err and str(path) not in captured.err
    assert 'Traceback' not in captured.err


def test_invalid_client_renderer_diagnostic_names_input_without_printing_value(monkeypatch, capsys):
    marker = 'synthetic-private-client-input-do-not-print'
    instance = deploy.Deployment(runner=Runner())
    instance.deploy = lambda **kwargs: instance.authentication()
    monkeypatch.setattr(deploy, 'Deployment', lambda: instance)
    monkeypatch.setenv('PPT_SITE_IMAGE', IMAGE)
    monkeypatch.setenv('PPT_CLIENT_ID', marker + '\ninvalid-client')
    assert deploy.main(['--skip-build']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'ERROR: PPT_CLIENT_ID could not render valid authentication configuration' in captured.err
    assert marker not in captured.err and 'invalid-client' not in captured.err
    assert 'Traceback' not in captured.err


@pytest.mark.parametrize('failure', ['unreadable', 'yaml', 'shape'])
def test_owner_input_diagnostic_names_override_without_echoing_private_path_or_emails(monkeypatch, tmp_path, capsys, failure):
    marker = 'synthetic-private-owner-input-do-not-print'
    path = tmp_path / (marker + '.yaml')
    if failure == 'yaml':
        path.write_text('emails: ["' + marker + '@example.invalid', encoding='utf-8')
    elif failure == 'shape':
        path.write_text(yaml.safe_dump([marker + '@example.invalid']), encoding='utf-8')
    instance = Scenario()
    monkeypatch.setattr(deploy, 'Deployment', lambda: instance)
    monkeypatch.setenv('PPT_SITE_IMAGE', IMAGE)
    monkeypatch.setenv('PPT_OWNER_CONFIG', str(path))
    assert deploy.main(['--skip-build']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'ERROR: PPT_OWNER_CONFIG' in captured.err
    assert 'is obsolete' in captured.err
    assert marker not in captured.err and str(path) not in captured.err
    assert '@example.invalid' not in captured.err and 'Traceback' not in captured.err
    assert instance.events == []


@pytest.mark.parametrize('failure', ['missing', 'permission', 'encoding'])
def test_default_image_receipt_diagnostic_names_file_without_printing_exception(monkeypatch, tmp_path, capsys, failure):
    marker = 'synthetic-private-image-input-do-not-print'
    service = tmp_path / marker
    monkeypatch.setattr(deploy, 'SERVICE', service)
    monkeypatch.setattr(deploy, 'Deployment', lambda: pytest.fail('Unreadable image receipt must stop before deployment'))
    if failure != 'missing':
        def fail_read(*args, **kwargs):
            if failure == 'permission':
                raise PermissionError(marker)
            raise UnicodeDecodeError('utf-8', marker.encode(), 0, 1, marker)
        monkeypatch.setattr(Path, 'read_text', fail_read)
    assert deploy.main(['--skip-build']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'ERROR: Default rendered/image.txt could not be read' in captured.err
    assert 'PPT_SITE_IMAGE' in captured.err
    assert marker not in captured.err and str(service) not in captured.err
    assert 'Traceback' not in captured.err


def test_environment_namespace_override_is_rejected_without_commands(monkeypatch):
    monkeypatch.setenv('NAMESPACE','default')
    with pytest.raises(SystemExit) as caught:
        deploy.main(['--dry-run'])
    assert caught.value.code==2


def test_website_release_preserves_enabled_kubernetes_admission():
    instance = Scenario()
    original = instance.get
    def get(kind, name, **kwargs):
        if (kind, name) == ('configmap', 'ppt-site'):
            return {'data': {'PPT_EXECUTION_ENABLED': 'true', 'PPT_EXECUTION_BACKEND': 'kubernetes'}}
        return original(kind, name, **kwargs)
    instance.get = get
    instance.deploy(image=IMAGE)
    config = next(d for d in instance.applied if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'ppt-site')
    assert config['data']['PPT_EXECUTION_ENABLED'] == 'true'
    assert config['data']['PPT_EXECUTION_BACKEND'] == 'kubernetes'


def test_website_release_rejects_unknown_enabled_backend():
    instance = Scenario()
    original = instance.get
    instance.get = lambda kind, name, **kwargs: ({'data': {'PPT_EXECUTION_ENABLED': 'true'}}
        if (kind, name) == ('configmap', 'ppt-site') else original(kind, name, **kwargs))
    with pytest.raises(deploy.DeployError, match='isolated configured backend'):
        instance.deploy(image=IMAGE)


def test_dry_run_never_instantiates_command_runner(monkeypatch):
    monkeypatch.setattr(deploy,'Deployment',lambda:pytest.fail('dry run must not construct command runner'))
    assert deploy.main(['--dry-run'])==0


@pytest.mark.parametrize('change', ['cookie','bypass-route','upstream','cookie-domain','access-token','jwks','extra-env',
                                  'email-domain', 'email-file', 'invitation-env'])
def test_auth_config_rejects_relaxed_security_fields(change):
    documents=deploy.load_documents(deploy.REPOSITORY/'oauth/k8s/ppt-proxy-configmap.yaml')
    by_name={doc['metadata']['name']:doc for doc in documents}
    by_name['ppt-auth']['data']['OIDC_AUDIENCE']='synthetic-dedicated-client'
    proxy=by_name['ppt-oidc']['data']['oauth2-proxy.cfg'].replace('REQUIRED_DEDICATED_PPT_CLIENT_ID','synthetic-dedicated-client')
    if change=='cookie':proxy=proxy.replace('cookie_secure = true','cookie_secure = false')
    elif change=='bypass-route':proxy+='\nskip_auth_routes = [".*"]\n'
    elif change=='upstream':proxy=proxy.replace('http://127.0.0.1:8000/','https://external.example.invalid/')
    elif change=='cookie-domain':proxy+='\ncookie_domains = [".example.invalid"]\n'
    elif change=='access-token':proxy=proxy.replace('pass_access_token = false','pass_access_token = true')
    elif change=='jwks':by_name['ppt-auth']['data']['OIDC_JWKS_URL']='https://external.example.invalid/jwks'
    elif change=='extra-env':by_name['ppt-auth']['data']['PPT_LOCAL_TEST_ONLY']='1'
    elif change=='email-domain':proxy=proxy.replace('email_domains = ["*"]', 'email_domains = ["example.invalid"]')
    elif change=='email-file':proxy+='\nauthenticated_emails_file = "/owner/emails"\n'
    elif change=='invitation-env':by_name['ppt-auth']['data']['INVITATION_EMAILS_FILE']='/owner/emails'
    by_name['ppt-oidc']['data']['oauth2-proxy.cfg']=proxy
    with pytest.raises(deploy.DeployError):
        deploy.validate_auth(documents)
