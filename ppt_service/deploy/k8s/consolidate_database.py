"""One-time, operator-run consolidation of PPT into the existing data/postgres.

Run from exact pushed Git commits on the existing Linux manager. No image inputs
change in this release: preserve installed immutable images and all application
settings. This command never submits user work or rewrites business records.
Failures preserve private evidence and partial target objects for inspection.
After target admission opens, never restore the stale source automatically.
"""
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
import tarfile
import time
from urllib.parse import unquote, urlsplit
import uuid

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]
OUTER = APP.parent
KUBECONFIG = '/etc/kubernetes/super-admin.conf'
K = ['kubectl', '--kubeconfig=' + KUBECONFIG]
OLD_HOST = 'ppt-postgres.ppt-service.svc.cluster.local'
NEW_HOST = 'postgres.data.svc.cluster.local'
ACTIVE_SQL = """BEGIN READ ONLY;
SELECT json_build_object(
 'active_jobs',(SELECT count(*) FROM jobs WHERE status IN ('queued','starting','running','collecting','validating','cancelling')),
 'busy_parses',(SELECT count(*) FROM assets WHERE (NOT removed AND parse_status='queued') OR parse_status IN ('parsing','stopping') OR parse_lease_until IS NOT NULL),
 'occupied_slots',(SELECT count(*) FROM execution_slots WHERE job_id IS NOT NULL),
 'schema',(SELECT max(revision) FROM api_schema_revision));
COMMIT;"""
SHARED_SQL = """BEGIN READ ONLY;
SELECT json_build_object(
 'databases',(SELECT json_agg(x ORDER BY x.datname) FROM
   (SELECT datname,pg_get_userbyid(datdba) AS owner,datacl::text,datallowconn,datconnlimit,
    pg_encoding_to_char(encoding) AS encoding,datcollate,datctype,datlocprovider,daticulocale
    FROM pg_database WHERE datname<>'ppt_service') x),
 'roles',(SELECT json_agg(x ORDER BY x.rolname) FROM
   (SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,
    rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig FROM pg_roles
    WHERE rolname NOT IN ('ppt_migrator','ppt_runtime')) x),
 'memberships',(SELECT json_agg(x ORDER BY x.role,x.member) FROM
   (SELECT pg_get_userbyid(roleid) AS role,pg_get_userbyid(member) AS member,admin_option
    FROM pg_auth_members) x));
COMMIT;"""
FILE_CODE = r"""import hashlib,json,os,stat,sys,tarfile
from pathlib import Path
root=Path('/var/lib/ppt-service')
if sys.argv[1]=='archive':
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
  archive.add(root,arcname='data',recursive=True)
else:
 result={}
 def visit(path,name):
  info=path.lstat()
  row={'mode':stat.S_IMODE(info.st_mode),'uid':info.st_uid,'gid':info.st_gid}
  if stat.S_ISLNK(info.st_mode):row.update(kind='link',target=os.readlink(path))
  elif stat.S_ISDIR(info.st_mode):
   row['kind']='directory'
  elif stat.S_ISREG(info.st_mode):
   digest=hashlib.sha256()
   with path.open('rb') as stream:
    for chunk in iter(lambda:stream.read(1048576),b''):digest.update(chunk)
   row.update(kind='file',bytes=info.st_size,sha256=digest.hexdigest())
  elif stat.S_ISSOCK(info.st_mode):
   # Unix sockets in stopped task directories have no restorable contents.
   # Record their metadata; tarfile intentionally omits these runtime nodes.
   row['kind']='socket'
  else:raise RuntimeError('unsupported_state_file')
  result[name]=row
  if row['kind']=='directory':
   for child in sorted(path.iterdir()):visit(child,name+'/'+child.name)
 visit(root,'data')
 print(json.dumps(result,sort_keys=True))
"""


class ConsolidationError(RuntimeError):
    """A deliberately public operator diagnostic, never a credential value."""


def require(condition, message):
    if not condition:
        raise ConsolidationError(message)


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive_index(path):
    result = {}
    with tarfile.open(path, 'r:') as archive:
        for item in archive:
            require(item.name not in result and item.name.split('/')[0] == 'data'
                    and '..' not in item.name.split('/'), 'Unsafe or duplicate backup member')
            row = {'mode': item.mode, 'uid': item.uid, 'gid': item.gid}
            if item.isdir():
                row['kind'] = 'directory'
            elif item.issym():
                row.update(kind='link', target=item.linkname)
            elif item.islnk():
                # tarfile.add emits later names for an existing inode as
                # backward hardlinks. Resolve only a previously checked regular
                # member within data/, never an external/symlink target.
                target = item.linkname
                require(target.split('/')[0] == 'data' and '..' not in target.split('/')
                        and target in result and result[target]['kind'] == 'file',
                        'Unsafe or unresolved backup hardlink')
                row.update(kind='file', bytes=result[target]['bytes'], sha256=result[target]['sha256'])
            elif item.isfile():
                stream = archive.extractfile(item)
                value = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    value.update(chunk)
                row.update(kind='file', bytes=item.size, sha256=value.hexdigest())
            else:
                raise RuntimeError('Unsupported backup member')
            result[item.name] = row
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app_commit')
    parser.add_argument('outer_commit')
    parser.add_argument('--checks-receipt', type=Path)
    parser.add_argument('--resume-after-restore', type=Path,
                        help='Failed private receipt at restored-fingerprint verification; never adopts an unknown target.')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (args.app_commit, args.outer_commit)),
            'Full immutable Git revisions are required')
    if not args.execute:
        print('Plan: exact Git/check receipt; lock; stop idle PPT; private database/files backups; '
              'restore dedicated DB/roles in data/postgres; compare complete fingerprints; '
              'fence old source; CAS Vault URLs; network/role checks; restart existing images; '
              'retain old StatefulSet/PVC at zero replicas. No model calls.')
        return
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Execute only on the existing Linux manager')
    os.umask(0o077)
    os.environ['KUBECONFIG'] = KUBECONFIG
    private_log = None

    def call(command, *, input=None, timeout=420):
        operation = next((arg for arg in command[1:] if arg in {
            'get', 'create', 'patch', 'delete', 'exec', 'wait', 'rollout',
            'rev-parse', 'diff', 'ls-tree'}), 'command')
        diagnostic = {'executable': Path(command[0]).name, 'operation': operation}
        try:
            value = subprocess.run(command, input=input, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if private_log is not None:
                print(json.dumps(dict(diagnostic, timed_out=True)), file=private_log, flush=True)
            raise ConsolidationError('Operator command timed out: ' + operation) from None
        if private_log is not None:
            print(json.dumps(dict(diagnostic, returncode=value.returncode)), file=private_log, flush=True)
        require(value.returncode == 0, 'Operator command failed: ' + operation)
        return value.stdout

    def kube(*arguments, namespace='ppt-service', input=None):
        return call(K + ['-n', namespace, *arguments], input=input)

    def get(kind, name, namespace='ppt-service'):
        return json.loads(kube('get', kind, name, '-o', 'json', namespace=namespace))

    def inventory(kind):
        return json.loads(kube('get', kind, '-o', 'json'))['items']

    for path, commit in ((APP, args.app_commit), (OUTER, args.outer_commit)):
        require(call(['git', '-C', str(path), 'rev-parse', 'HEAD']).strip() == commit, 'Git HEAD mismatch')
        call(['git', '-C', str(path), 'diff', '--quiet'])
        call(['git', '-C', str(path), 'diff', '--cached', '--quiet'])
    require(call(['git', '-C', str(OUTER), 'ls-tree', 'HEAD', 'panghu_agent']).split()[:3]
            == ['160000', 'commit', args.app_commit], 'Outer submodule pointer mismatch')
    require(args.checks_receipt and args.checks_receipt.is_file() and not args.checks_receipt.is_symlink(),
            'Supply the passed server checks receipt')
    checks = json.loads(args.checks_receipt.read_text())
    require(all(checks.get(key) == value for key, value in {
        'app_commit': args.app_commit, 'outer_commit': args.outer_commit, 'passed': True,
        'cleanup_complete': True, 'server_only': True, 'model_requests': 0,
        'production_database_used': False, 'source_from_git': True}.items()), 'Server checks receipt mismatch')
    require({'shared-db-deployment-contracts', 'shared-db-build-contracts', 'shared-db-helper-syntax'}
            <= {row['stage'] for row in checks['stages'] if row.get('passed')}, 'Required server checks missing')

    resume = None
    if args.resume_after_restore:
        path = args.resume_after_restore
        require(path.is_absolute() and path.parent.parent == Path('/root')
                and not path.is_symlink() and not path.parent.is_symlink(), 'Invalid private recovery receipt path')
        previous = json.loads(path.read_text())
        previous_transfer = json.loads((path.parent / 'transfer-receipt.json').read_text())
        require(previous.get('phase') == 'restore-dedicated-database-and-compare-all-data'
                and previous.get('passed') is False and previous.get('target_admission_opened') is False
                and not previous.get('old_source_fenced')
                and previous_transfer.get('phase') == 'verify-restored-schema-data-and-runtime'
                and previous_transfer.get('passed') is False,
                'Recovery is restricted to a restored but unopened, unfenced installation')
        changed = call(['git', '-C', str(APP), 'diff', '--name-only', previous['app_commit'], args.app_commit]).splitlines()
        require(set(changed) <= {'ppt_service/deploy/k8s/consolidate_database.py',
                                'ppt_service/deploy/k8s/database_fingerprint.py',
                                'ppt_service/tests/deployment/test_database_fingerprint.py'},
                'Recovery revision changes application or transfer inputs')
        resume = path.parent

    output = Path('/root') / ('ppt-shared-db-' + args.app_commit[:12] + '-' + uuid.uuid4().hex[:10] + '-migration')
    output.mkdir(mode=0o700)
    save(output / 'checks.json', checks)
    deploy = load('ppt_shared_deploy', HERE / 'deploy_site.py')
    transfer_module = load('ppt_shared_transfer', HERE / 'database_transfer.py')
    receipt = {'app_commit': args.app_commit, 'outer_commit': args.outer_commit,
               'checks_sha256': digest(args.checks_receipt), 'server_only': True, 'source_from_git': True,
               'model_requests': 0, 'application_row_mutations_by_script': False,
               'passed': False, 'target_admission_opened': False, 'phase': 'preflight'}
    if resume:
        receipt['resume_after_restore'] = {'receipt': str(args.resume_after_restore),
                                           'sha256': digest(args.resume_after_restore)}
    maintenance = None
    deployment = deploy.Deployment(runner=lambda command, **kw: subprocess.run(command, timeout=420, **kw))
    with (output / 'migration.log').open('x') as log:
        private_log = log
        transfer = transfer_module.Transfer(kubeconfig=KUBECONFIG, output=output, log=log)

        def phase(name):
            receipt['phase'] = name
            print(json.dumps({'phase': name}), file=log, flush=True)
            print('PPT_DATABASE_MIGRATION phase=' + name, flush=True)

        def replace_spec(kind, name, expected, desired, namespace='ppt-service'):
            current = get(kind, name, namespace)
            require(current['metadata']['uid'] == expected['metadata']['uid']
                    and current['spec'] == expected['spec'], 'Resource changed since snapshot')
            patch = [{'op': 'test', 'path': '/metadata/uid', 'value': current['metadata']['uid']},
                     {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['metadata']['resourceVersion']},
                     {'op': 'replace', 'path': '/spec', 'value': desired}]
            kube('patch', kind, name, '--type=json', '-p', json.dumps(patch), namespace=namespace)
            result = get(kind, name, namespace)
            require(result['spec'] == desired, 'Unexpected resource defaulting')
            return result

        def idle():
            value = json.loads(transfer.psql('source', ACTIVE_SQL))
            require(value == {'active_jobs': 0, 'busy_parses': 0, 'occupied_slots': 0, 'schema': 3},
                    'PPT is not idle at schema 3; wait for existing work')
            require(not any(p['metadata'].get('labels', {}).get('app') in
                            ('ppt-hermes-worker', 'ppt-model-proxy', 'ppt-sandbox')
                            or p['metadata'].get('labels', {}).get('ppt-service.panghuer.top/attempt')
                            or p['metadata'].get('labels', {}).get('ppt.panghuer.top/attempt')
                            for p in inventory('pods')), 'Execution scopes still exist')
            return value

        def stop(name, snapshot):
            if resume:
                before = json.loads((resume / snapshot).read_text())
                current = get('deployment', name)
                expected = copy.deepcopy(before['spec'])
                expected['replicas'] = 0
                require(current['metadata']['uid'] == before['metadata']['uid'] and current['spec'] == expected,
                        'Recovery writers differ from the original stopped snapshots')
                selector = ','.join(k + '=' + v for k, v in current['spec']['selector']['matchLabels'].items())
                require(not json.loads(kube('get', 'pods', '-l', selector, '-o', 'json'))['items'],
                        'Recovery requires all original writer Pods to have stopped')
                save(output / snapshot, before)
                return before, current
            before = get('deployment', name)
            save(output / snapshot, before)
            require(before['spec'].get('replicas', 1) > 0,
                    'Do not adopt a previously suspended installation')
            require(all(re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', c['image'])
                        for c in before['spec']['template']['spec']['containers']),
                    'Expected immutable installed images')
            desired = copy.deepcopy(before['spec'])
            desired['replicas'] = 0
            stopped = replace_spec('deployment', name, before, desired)
            selector = ','.join(k + '=' + v for k, v in stopped['spec']['selector']['matchLabels'].items())
            end = time.monotonic() + 180
            while json.loads(kube('get', 'pods', '-l', selector, '-o', 'json'))['items']:
                require(time.monotonic() < end, 'Writer Pods did not stop')
                time.sleep(2)
            return before, stopped

        def vault_get(path):
            return json.loads(kube('exec', 'vault-0', '--', 'vault', 'kv', 'get', '-format=json', path, namespace='vault'))

        def preserved():
            shared_sts = get('statefulset', 'postgres', 'data')
            shared_claim = get('pvc', 'data-postgres-0', 'data')
            return {'configmaps': {r['metadata']['name']: {k: r.get(k) for k in ('data', 'binaryData', 'immutable')}
                                  for r in inventory('configmaps') if r['metadata']['name'] != 'ppt-deploy-lock'},
                    'claims': {r['metadata']['name']: {'uid': r['metadata']['uid'], 'spec': r['spec']}
                               for r in inventory('pvc')},
                    'shared_sts': {'uid': shared_sts['metadata']['uid'], 'spec': shared_sts['spec']},
                    'shared_claim': {'uid': shared_claim['metadata']['uid'], 'spec': shared_claim['spec']},
                    'shared_catalog': json.loads(transfer.psql('target', SHARED_SQL, database='postgres'))}

        try:
            deployment.lock()
            deployment.check_secret_owners()
            if resume:
                original_metadata = json.loads((resume / 'transfer-source-metadata.json').read_text())
                current_source, current_target = transfer.metadata('source'), transfer.metadata('target')
                transfer.validate_source(current_source)
                require(current_source == original_metadata, 'Source metadata changed after interrupted restore')
                require({k: v for k, v in current_target.items() if k != 'server_version_num'} ==
                        {k: v for k, v in original_metadata.items() if k != 'server_version_num'},
                        'Restored target metadata does not match the recorded source')
                archive = Path(previous_transfer['archive']['path'])
                require(archive.parent == resume and not archive.is_symlink()
                        and digest(archive) == previous_transfer['archive']['sha256'], 'Recorded database backup changed')
            else:
                transfer.preflight()
            before_preserved = preserved()
            if resume:
                require(before_preserved == json.loads((resume / 'preserved-before.json').read_text()),
                        'Preserved installation changed after interrupted restore')
            save(output / 'preserved-before.json', before_preserved)
            credentials = {}
            passwords = {}
            for suffix, role in (('migrator', 'ppt_migrator'), ('runtime', 'ppt_runtime')):
                name = 'ppt-database-' + suffix
                path = 'secret/ppt-service/database-' + suffix
                secret = get('secret', name)
                original = base64.b64decode(secret['data']['DATABASE_URL']).decode()
                require(not any(ord(character) < 32 or ord(character) == 127 for character in original),
                        'Invalid source database URL characters')
                url = urlsplit(original)
                require(url.scheme == 'postgresql+psycopg' and url.hostname == OLD_HOST
                        and unquote(url.username or '') == role and url.path == '/ppt_service'
                        and url.port == 5432 and not url.query and not url.fragment, 'Unexpected source URL')
                vault = vault_get(path)
                require(vault['data']['data']['DATABASE_URL'] == original, 'Vault and Secret differ')
                # Replace only the URL authority host. A raw password may itself
                # contain @host: text; a global replace would change the secret.
                userinfo = url.netloc.rsplit('@', 1)[0]
                replacement = url._replace(netloc=userinfo + '@' + NEW_HOST + ':5432').geturl()
                credentials[suffix] = {'path': path, 'secret': secret, 'vault': vault,
                                       'old': original, 'new': replacement}
                require(credentials[suffix]['new'] != original, 'URL host replacement failed')
                passwords[role] = unquote(url.password or '')
                require(passwords[role] and not any(character in passwords[role] for character in ('\x00', '\r', '\n')),
                        'Invalid source database password')
            save(output / 'credentials-before.private.json', credentials)
            if resume:
                old_credentials = json.loads((resume / 'credentials-before.private.json').read_text())
                require(all(credentials[key]['old'] == old_credentials[key]['old']
                            and credentials[key]['vault']['data'] == old_credentials[key]['vault']['data']
                            and credentials[key]['secret']['data'] == old_credentials[key]['secret']['data']
                            for key in credentials), 'Credentials changed after interrupted restore')
            idle()
            phase('stop-idle-application-writers')
            site, stopped_site = stop('ppt-site', 'site-before.json')
            idle()
            controller, stopped_controller = stop('ppt-hermes-controller', 'controller-before.json')
            idle()
            require(int(transfer.psql('source', "SELECT count(*) FROM pg_stat_activity WHERE datname='ppt_service' AND pid<>pg_backend_pid();")) == 0,
                    'Unexpected remaining source database clients')

            phase('backup-and-verify-ppt-files')
            name = 'ppt-db-backup-' + deployment.lock_token[:12]
            pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': 'ppt-service',
                    'labels': {'app': 'ppt-db-backup', 'ppt-db-backup-owner': deployment.lock_token}}, 'spec': {
                'restartPolicy': 'Never', 'activeDeadlineSeconds': 3600, 'automountServiceAccountToken': False,
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10000,
                                    'seccompProfile': {'type': 'RuntimeDefault'}},
                'nodeSelector': copy.deepcopy(site['spec']['template']['spec'].get('nodeSelector', {})),
                'containers': [{'name': 'backup', 'image': next(c['image'] for c in site['spec']['template']['spec']['containers'] if c['name']=='site'),
                    'command': ['/usr/local/bin/python', '-c', 'import time; time.sleep(3500)'],
                    'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                    'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
                    'volumeMounts': [{'name': 'data', 'mountPath': '/var/lib/ppt-service', 'readOnly': True}]}],
                'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'ppt-data', 'readOnly': True}}]}}
            maintenance = json.loads(kube('create', '-f', '-', '-o', 'json', input=json.dumps(pod)))
            kube('wait', '--for=condition=Ready', 'pod/' + name, '--timeout=180s')
            files_before = json.loads(kube('exec', name, '--', '/usr/local/bin/python', '-c', FILE_CODE, 'index'))
            save(output / 'files-before.private.json', files_before)
            if resume:
                require(files_before == json.loads((resume / 'files-before.private.json').read_text()),
                        'Frozen files changed after interrupted restore')
                backup = Path(previous['file_backup']['path'])
                require(backup.parent == resume and not backup.is_symlink()
                        and digest(backup) == previous['file_backup']['sha256'], 'Recorded file backup changed')
            else:
                backup = output / 'ppt-data.tar'
                with backup.open('xb') as stream:
                    result = subprocess.run(K + ['-n', 'ppt-service', 'exec', name, '--', '/usr/local/bin/python', '-c', FILE_CODE, 'archive'],
                                            stdout=stream, stderr=log, timeout=600)
                    require(result.returncode == 0, 'PPT file backup failed')
                    stream.flush()
                    os.fsync(stream.fileno())
            restorable = {key: value for key, value in files_before.items() if value['kind'] != 'socket'}
            require(archive_index(backup) == restorable, 'File archive differs from frozen files')
            receipt['file_backup'] = {'path': str(backup), 'sha256': digest(backup), 'entries': len(restorable),
                                      'runtime_sockets_metadata_only': len(files_before) - len(restorable)}

            phase('restore-dedicated-database-and-compare-all-data')
            if resume:
                require(transfer.metadata('source') == current_source
                        and transfer.metadata('target') == current_target,
                        'Database or role metadata changed during recovery checks')
                original_fingerprint = json.loads((resume / 'transfer-source-before.private.json').read_text())
                source_now, target_now = transfer.fingerprint('source'), transfer.fingerprint('target')
                require(source_now == original_fingerprint == target_now,
                        'Recovery requires complete original/source/target fingerprint equality')
                verified_hash = hashlib.sha256(transfer_module.canonical(source_now)).hexdigest()
                require(verified_hash == previous_transfer['source_fingerprint_sha256'],
                        'Recorded source fingerprint receipt does not match its evidence')
                save(output / 'transfer-source-before.private.json', source_now)
                save(output / 'transfer-source-metadata.json', current_source)
                save(output / 'transfer-target-after.private.json', target_now)
                role = json.loads(transfer.psql('target', transfer_module.RUNTIME_SQL, user='ppt_runtime'))
                require(role == {'user': 'ppt_runtime', 'schema': 3, 'schema_create': False,
                    'revision_write': False, 'entries_read_append': True, 'entries_mutable': False,
                    'streams_cas': True, 'streams_destructive': False}, 'Recovery runtime role contract failed')
                receipt['transfer'] = dict(previous_transfer, passed=True, phase='complete', source_unchanged=True,
                    fingerprints_equal=True, database_locale_and_acl_equal=True, runtime_read_only_check=True,
                    target_fingerprint_sha256=verified_hash,
                    recovered_from=str(resume), tables=len(target_now['tables']), sequences=len(target_now['sequences']))
                receipt['transfer'].pop('diagnostic', None)
                receipt['transfer'].pop('error_type', None)
            else:
                receipt['transfer'] = transfer.run(passwords)
            require(preserved() == before_preserved, 'Shared databases/configuration/PVC changed')
            require(json.loads(kube('exec', name, '--', '/usr/local/bin/python', '-c', FILE_CODE, 'index')) == files_before,
                    'PPT files changed during database transfer')

            phase('fence-and-reverify-old-source')
            source_metadata = json.loads((output / 'transfer-source-metadata.json').read_text())
            source_limit = source_metadata['database']['connection_limit']
            require(type(source_limit) is int, 'Invalid original source connection limit')
            require(transfer.psql('source', 'SELECT rolsuper FROM pg_roles WHERE rolname=current_user;',
                                  database='postgres').strip() == 't',
                    'Final source verification requires the existing superuser administrator')
            # All PPT roles are verified NOSUPERUSER. Block new application
            # sessions before the final comparison while the existing admin can
            # still connect read-only. A failure leaves this fence in place.
            transfer.psql('source', 'ALTER DATABASE ppt_service CONNECTION LIMIT 0;', database='postgres')
            receipt['old_source_client_fenced'] = True
            require(int(transfer.psql('source', "SELECT count(*) FROM pg_stat_activity WHERE datname='ppt_service';",
                                      database='postgres')) == 0, 'Source clients remain after admission fence')
            final_source = transfer.fingerprint('source')
            save(output / 'source-fenced-final.private.json', final_source)
            require(final_source == json.loads((output / 'transfer-source-before.private.json').read_text()),
                    'Source changed before fencing; do not switch consumers')
            receipt['old_source_final_fingerprint_equal'] = True
            # Apply both settings atomically, retaining the original connection
            # limit for an explicit rollback without reopening this database.
            transfer.psql('source', 'ALTER DATABASE ppt_service ALLOW_CONNECTIONS false CONNECTION LIMIT '
                          + str(source_limit) + ';', database='postgres')
            receipt['old_source_fenced'] = True

            phase('switch-exact-database-network-peers')
            policies = [get('networkpolicy', name) for name in (
                'site-postgres-egress', 'migrator-postgres-egress', 'hermes-controller-egress')]
            save(output / 'policies-before.json', policies)
            for old in policies:
                name = old['metadata']['name']
                desired = copy.deepcopy(old['spec'])
                matches = [rule for rule in desired.get('egress', [])
                           if rule.get('ports') == [{'protocol': 'TCP', 'port': 5432}]]
                require(len(matches) == 1, 'Ambiguous database NetworkPolicy rule')
                matches[0]['to'] = [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'data'}},
                                     'podSelector': {'matchLabels': {'app': 'postgres'}}}]
                replace_spec('networkpolicy', name, old, desired)
            phase('cas-vault-hosts')
            for suffix, values in credentials.items():
                current = vault_get(values['path'])
                require(current['data'] == values['vault']['data'], 'Concurrent Vault credential change')
                kube('exec', '-i', 'vault-0', '--', 'vault', 'kv', 'patch',
                     '-method=patch', '-cas=' + str(current['data']['metadata']['version']), values['path'], '-',
                     namespace='vault', input=json.dumps({'DATABASE_URL': values['new']}))
                after = vault_get(values['path'])
                expected = dict(values['vault']['data']['data'], DATABASE_URL=values['new'])
                require(after['data']['data'] == expected, 'Vault fields differ after CAS patch')
            deployment.wait_secrets('ppt-database-migrator', 'ppt-database-runtime')
            for suffix, values in credentials.items():
                secret = get('secret', 'ppt-database-' + suffix)
                require(base64.b64decode(secret['data']['DATABASE_URL']).decode() == values['new'], 'ExternalSecret URL mismatch')
                deploy.database_password(secret, 'ppt_' + suffix)

            phase('verify-new-runtime-access-before-opening-admission')
            require(deployment.revision() == 3, 'Shared target schema/roles not ready')
            image = next(c['image'] for c in site['spec']['template']['spec']['containers'] if c['name'] == 'site')
            deployment.run_job(deploy.make_job(image, 'runtime', deployment.lock_token))
            receipt['read_only_runtime_role_check'] = True
            require(preserved() == before_preserved, 'Preserved installation drift before reopening')
            require(int(transfer.psql('source', "SELECT count(*) FROM pg_stat_activity WHERE datname='ppt_service';", database='postgres')) == 0,
                    'Old source has live clients after fencing')

            phase('resume-existing-application-images-on-shared-postgres')
            # Reopening either controller or site can admit work. Never roll the
            # database back automatically after the first resume request.
            receipt['target_admission_opened'] = True
            for name, original, stopped in (('ppt-hermes-controller', controller, stopped_controller), ('ppt-site', site, stopped_site)):
                desired = copy.deepcopy(original['spec'])
                desired['template'].setdefault('metadata', {}).setdefault('annotations', {})['ppt-service.panghuer.top/database-consolidation'] = args.app_commit
                replace_spec('deployment', name, stopped, desired)
                kube('rollout', 'status', 'deployment/' + name, '--timeout=300s')
            require(preserved() == before_preserved, 'Preserved installation drift after restart')
            phase('retain-old-database-offline')
            old = get('statefulset', 'ppt-postgres')
            save(output / 'source-statefulset-before.json', old)
            desired = copy.deepcopy(old['spec'])
            desired['replicas'] = 0
            replace_spec('statefulset', 'ppt-postgres', old, desired)
            kube('wait', '--for=delete', 'pod/ppt-postgres-0', '--timeout=180s')
            receipt.update(passed=True, phase='complete', old_database_and_pvc_retained=True,
                           existing_images_preserved=True, files_unchanged=True, shared_resources_preserved=True)
        except BaseException as exc:
            receipt['error_type'] = type(exc).__name__
            if isinstance(exc, ConsolidationError):
                receipt['diagnostic'] = str(exc)
            receipt['recovery'] = ('Inspect private snapshots and phase. Preserve partial target and both backups. '
                                   'Do not resume or roll back the source without checking target admission and writes.')
            raise RuntimeError('Database consolidation stopped in phase ' + receipt['phase'] + '; inspect private receipt') from None
        finally:
            if maintenance:
                try:
                    current = get('pod', maintenance['metadata']['name'])
                    require(current['metadata']['uid'] == maintenance['metadata']['uid']
                            and current['metadata']['labels'].get('ppt-db-backup-owner') == deployment.lock_token,
                            'Backup Pod owner changed')
                    call(K + ['delete', '--raw', '/api/v1/namespaces/ppt-service/pods/' + current['metadata']['name'],
                              '-f', '-'], input=json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions',
                                  'preconditions': {'uid': current['metadata']['uid']}}))
                    kube('wait', '--for=delete', 'pod/' + current['metadata']['name'], '--timeout=180s')
                    receipt['maintenance_pod_removed'] = True
                except Exception as cleanup_error:
                    receipt['maintenance_pod_removed'] = False
                    receipt['passed'] = False
                    receipt.setdefault('cleanup_errors', {})['maintenance_pod'] = type(cleanup_error).__name__
            try:
                deployment.unlock()
                receipt['lock_released'] = True
            except Exception as cleanup_error:
                receipt.update(lock_released=False, passed=False)
                receipt.setdefault('cleanup_errors', {})['deployment_lock'] = type(cleanup_error).__name__
            save(output / 'receipt.json', receipt)
            print(json.dumps({'receipt': str(output / 'receipt.json'), 'phase': receipt['phase'], 'passed': receipt['passed']}), flush=True)
    require(receipt['passed'], 'Migration cleanup needs attention')


if __name__ == '__main__':
    main()
