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
NS = 'ppt-service-test'


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
    return f'postgresql+psycopg://{role}:{password}@ppt-postgres.ppt-service-test.svc.cluster.local:5432/ppt_service'


def database_state():
    return {'database_exists': True, 'owner': 'ppt_migrator', 'roles': [
        {'name': name, 'super': False, 'createdb': False, 'createrole': False,
         'replication': False, 'inherit': False, 'login': True, 'member':False}
        for name in ('ppt_migrator', 'ppt_runtime')]}


class Scenario(deploy.Deployment):
    """Fake orchestration, while preserving the real deploy() control flow."""
    def __init__(self, *, revision=2, existing_site=3, owner=True, fail_bootstrap=False):
        super().__init__(runner=Runner())
        self.events = []
        self.applied = []
        self.jobs = []
        self.revisions = [revision, 2]
        self.owner = owner
        self.existing_site = existing_site
        self.fail_bootstrap = fail_bootstrap

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
        if self.fail_bootstrap:
            raise deploy.DeployError('Synthetic unavailable bootstrap')

    def kube(self, *args, input=None):
        self.events.append(('kube', args, input))
        return ''

    def get(self, kind, name, **kwargs):
        if kind == 'secret' and name in ('ppt-database-migrator', 'ppt-database-runtime'):
            return secret_url(dedicated_url(role='ppt_runtime' if name.endswith('runtime') else 'ppt_migrator'))
        if kind == 'deployment' and name == 'ppt-site' and self.existing_site is not None:
            return {'spec': {'replicas': self.existing_site}}
        if kind == 'configmap' and name == 'ppt-owner' and self.owner:
            return {'data': {'emails': 'existing-owner@example.invalid\n'}}
        return None

    def revision(self):
        self.events.append(('revision',))
        return self.revisions.pop(0)

    def run_job(self, job):
        self.jobs.append(copy.deepcopy(job))

    def psql(self, sql, database='ppt_test', user='ppt_bootstrap'):
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


@pytest.mark.parametrize('owner', [None, [], [{'kind': 'ExternalSecret', 'name': 'ppt-postgres-bootstrap', 'uid': 'wrong'}]])
def test_secret_owner_preflight_refuses_takeover(owner):
    metadata = {'name': 'ppt-postgres-bootstrap'}
    if owner is not None:
        metadata['ownerReferences'] = owner
    runner = Runner([metadata, {'metadata': {'uid': 'expected'}}])
    instance = deploy.Deployment(runner=runner)
    with pytest.raises(deploy.DeployError, match='Refusing to take over'):
        instance.check_secret_owners()
    assert all('apply' not in args and 'create' not in args for args, _ in runner.calls)
    assert runner.calls[0][0][-1] == 'jsonpath={.metadata}'


def test_secret_owner_matching_current_eso_uid_is_allowed():
    name = 'ppt-postgres-bootstrap'
    runner = Runner([{'ownerReferences': [{'kind': 'ExternalSecret', 'name': name, 'uid': 'expected'}]},
                     {'metadata': {'uid': 'expected'}}, '', '', ''])
    deploy.Deployment(runner=runner).check_secret_owners()
    assert not runner.replies


def eso_status(refresh, ready=True):
    return {'status': {'refreshTime':refresh,'conditions':[{'type':'Ready','status':'True' if ready else 'False'}]}}


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


def test_prepare_preserves_site_and_waits_only_bootstrap():
    instance = Scenario()
    instance.deploy(prepare=True, init_db=False, image=None)
    assert not instance.jobs
    assert not any(d['kind'] == 'Deployment' for d in instance.applied)
    assert [e for e in instance.events if e[0] == 'wait-secrets'] == [('wait-secrets', ('ppt-postgres-bootstrap',))]
    assert next(d for d in instance.applied if d['kind'] == 'StatefulSet')['spec']['replicas'] == 1
    assert not any(e[0] == 'kube' and 'scale' in e[1] for e in instance.events)
    assert instance.events.index(('secret-owners',)) < next(i for i, e in enumerate(instance.events)
        if e[0] == 'apply' and any(kind == 'ExternalSecret' for kind, _ in e[1]))
    assert instance.events[-1] == ('unlock',)


def test_prepare_failure_releases_deployment_lock():
    instance = Scenario(fail_bootstrap=True)
    with pytest.raises(deploy.DeployError):
        instance.deploy(prepare=True, init_db=False, image=None)
    assert instance.events[-1] == ('unlock',)


def test_existing_owner_list_is_preserved_without_override():
    instance = Scenario()
    instance.deploy(prepare=False, init_db=False, image=IMAGE)
    assert not any(d['metadata']['name'] == 'ppt-owner' for d in instance.applied)


def test_initial_owner_list_is_applied_when_absent():
    instance = Scenario(owner=False)
    instance.deploy(prepare=False, init_db=False, image=IMAGE)
    assert sum(d['metadata']['name'] == 'ppt-owner' for d in instance.applied) == 1


def test_migration_and_site_use_the_same_manifest_digest():
    instance = Scenario(revision=1, existing_site=0)
    instance.deploy(prepare=False, init_db=False, image=IMAGE)
    assert {job['spec']['template']['spec']['containers'][0]['image'] for job in instance.jobs} == {IMAGE}
    site = next(d for d in instance.applied if d['kind'] == 'Deployment')
    assert site['spec']['template']['spec']['containers'][0]['image'] == IMAGE
    assert sum(job['metadata']['name'].startswith('ppt-migrate-') for job in instance.jobs) == 1


def test_current_schema_skips_migration_job():
    instance = Scenario(revision=2)
    instance.deploy(prepare=False, init_db=False, image=IMAGE)
    assert not any(job['metadata']['name'].startswith('ppt-migrate-') for job in instance.jobs)


def test_existing_site_replica_count_is_preserved():
    instance = Scenario(existing_site=3)
    instance.deploy(prepare=False, init_db=False, image=IMAGE)
    site = next(d for d in instance.applied if d['kind'] == 'Deployment')
    assert site['spec']['replicas'] == 3


@pytest.mark.parametrize('replacement', [
    'postgresql://ppt_migrator:synthetic@ppt-postgres.ppt-service-test.svc.cluster.local:5432/ppt_service',
    dedicated_url().replace('ppt_migrator:', 'ppt_runtime:'), dedicated_url().replace(':5432/', ':5433/'),
    dedicated_url().replace('ppt-postgres.', 'other-postgres.'), dedicated_url().replace('/ppt_service', '/other'),
    dedicated_url() + '?sslmode=disable', dedicated_url() + '#fragment', dedicated_url(password=''),
    dedicated_url(password='synthetic%00password'), dedicated_url(password='synthetic%0Apassword'),
], ids=['driver','role','port','host','database','query','fragment','empty-password','nul','encoded-newline'])
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


@pytest.mark.parametrize('state', [
    {'database_exists': True, 'roles': []}, {'database_exists': False, 'roles': [{'name': 'ppt_migrator'}]},
])
def test_explicit_init_refuses_existing_or_partial_database(state):
    instance = deploy.Deployment(runner=Runner())
    instance.db_state = lambda: state
    instance.get = lambda *args, **kwargs: pytest.fail('Existing initialization must not fetch credentials')
    with pytest.raises(deploy.DeployError, match='existing or partial'):
        instance.initialize_database()


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


@pytest.mark.parametrize('revision', [0, 1, 2])
def test_supported_schema_revisions(revision):
    runner = Runner(['t', str(revision)])
    instance = deploy.Deployment(runner=runner)
    instance.db_state = database_state
    assert instance.revision() == revision


def test_absent_schema_revision_table_is_initial_revision_zero():
    instance = deploy.Deployment(runner=Runner(['f']))
    instance.db_state = database_state
    assert instance.revision() == 0


@pytest.mark.parametrize('revision', [3, -1])
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


def test_job_wait_failure_keeps_job_and_hides_child_stderr():
    job=deploy.make_job(IMAGE,'migrate')
    previous=copy.deepcopy(job)
    previous['status']={'active':1}
    runner=Runner([previous,(1,'','SYNTHETIC_PRIVATE_DB_URL')])
    with pytest.raises(deploy.DeployError) as caught:
        deploy.Deployment(runner=runner).run_job(job)
    assert 'SYNTHETIC_PRIVATE_DB_URL' not in str(caught.value)
    assert all('delete' not in args for args,_ in runner.calls)


def test_storage_job_is_readonly_nonroot_and_has_no_db_credentials():
    job = deploy.make_job(IMAGE, 'storage')
    spec = job['spec']['template']['spec']
    container = spec['containers'][0]
    assert spec['automountServiceAccountToken'] is False
    assert spec['securityContext']['runAsNonRoot'] is True
    assert spec['securityContext']['runAsUser'] == 10000
    assert 'fsGroup' not in spec['securityContext']
    assert container['env'] == []
    assert next(v for v in spec['volumes'] if v['name'] == 'data')['persistentVolumeClaim']['readOnly'] is True
    assert next(v for v in container['volumeMounts'] if v['name'] == 'data')['readOnly'] is True
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert container['securityContext']['capabilities']['drop'] == ['ALL']


@pytest.mark.parametrize('mode,uid,gid,accessible,accepted', [
    (stat.S_IFDIR|0o700,10000,10000,True,True), (stat.S_IFLNK|0o700,10000,10000,True,False),
    (stat.S_IFDIR|0o777,10000,10000,True,False), (stat.S_IFDIR|0o700,0,0,True,False),
    (stat.S_IFDIR|0o700,10000,10000,False,False),
], ids=['private-owner-directory','symlink','broad-mode','wrong-owner','not-traversable'])
def test_readonly_storage_check_rejects_unsafe_metadata(mode,uid,gid,accessible,accepted):
    fake_os = SimpleNamespace(lstat=lambda _: SimpleNamespace(st_mode=mode,st_uid=uid,st_gid=gid),
                              access=lambda *_: accessible,R_OK=4,X_OK=1)
    real_import = builtins.__import__
    def fake_import(name,*args,**kwargs):
        return fake_os if name=='os' else real_import(name,*args,**kwargs)
    namespace = {'__builtins__': vars(builtins)|{'__import__':fake_import}}
    if accepted:
        exec(deploy.STORAGE_CHECK,namespace)
    else:
        with pytest.raises(AssertionError):
            exec(deploy.STORAGE_CHECK,namespace)


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
    assert stub.deployments==[{'prepare':False,'init_db':False,'image':IMAGE}]


def test_prepare_main_never_builds_or_reads_image_receipt(monkeypatch,tmp_path):
    stub,service=configure_main(monkeypatch,tmp_path)
    (service/'rendered/image.txt').unlink()
    assert deploy.main(['--prepare'])==0
    assert stub.commands==[]
    assert stub.deployments==[{'prepare':True,'init_db':False,'image':None}]


@pytest.mark.parametrize('value',[IMAGE,'arm-cluster-master:5000/ppt-service-site:latest'])
def test_skip_build_uses_only_valid_manifest_digest(monkeypatch,tmp_path,value):
    stub,_=configure_main(monkeypatch,tmp_path,receipt=value)
    result=deploy.main(['--skip-build'])
    assert stub.commands==[]
    assert result==(0 if value==IMAGE else 1)
    assert bool(stub.deployments)==(value==IMAGE)


def test_environment_namespace_override_is_rejected_without_commands(monkeypatch):
    monkeypatch.setenv('NAMESPACE','default')
    with pytest.raises(SystemExit) as caught:
        deploy.main(['--dry-run'])
    assert caught.value.code==2


def test_dry_run_never_instantiates_command_runner(monkeypatch):
    monkeypatch.setattr(deploy,'Deployment',lambda:pytest.fail('dry run must not construct command runner'))
    assert deploy.main(['--dry-run'])==0


@pytest.mark.parametrize('change', ['cookie','bypass-route','upstream','cookie-domain','access-token','jwks','extra-env'])
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
    by_name['ppt-oidc']['data']['oauth2-proxy.cfg']=proxy
    with pytest.raises(deploy.DeployError):
        deploy.validate_auth(documents)
