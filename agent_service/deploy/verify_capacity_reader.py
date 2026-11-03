"""Server-only, isolated image-owned catalog reader verification.

Current public configuration and immutable host files are read-only inputs.
Only two never-networked validation containers run; no service is started,
no database/Secret is opened and no cluster resource is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import uuid


IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z')
NAME = re.compile(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z')
PACKAGE = re.compile(r'/[A-Za-z0-9_./-]+/agent_service\Z')
FORBIDDEN = frozenset({'database_url', 'tokens', 'receipt_keys', 'api_key', 'password',
    'authorization', 'client_secret', 'worker_token', 'receipt_key_hex', 'model_proxy_token'})
INPUT_LIMIT = 64 * 1024 * 1024
REPOSITORY = Path(__file__).resolve().parents[2]

# This program imports only the image's own package and installed dependencies.
# No repository/package code or credentials are mounted into either container.
READER_PROGRAM = r'''
import copy, hashlib, importlib, json, pathlib, sys
STAGE = 'startup'

def require(value):
    if not value:
        raise ValueError('reader_check_failed')

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def main():
    global STAGE
    payload = json.load(sys.stdin)
    root = pathlib.Path(payload['package_root']).resolve(strict=True)
    require(root.name == 'agent_service' and root.is_dir())
    sys.path[:] = [str(root.parent)] + [entry for entry in sys.path
        if entry and not entry.startswith(('/src', '/work', '/tmp', '/capacity-inputs'))]
    imported = {}
    STAGE = 'import_image_readers'
    for name in ('config', 'registry', 'worker_entry'):
        module = importlib.import_module('agent_service.' + name)
        path = pathlib.Path(module.__file__).resolve(strict=True)
        require(path.is_relative_to(root))
        imported[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    from agent_service.config import _Public
    from agent_service.registry import DeploymentRegistry
    from agent_service.worker_entry import HostConfig, host_policy
    keys = ('task_contracts', 'capability_bundles', 'tool_environments', 'execution_bindings', 'routes')
    current, hosts = payload['current'], payload['hosts']
    expected_count = payload['expected_binding_count']

    def registry(value):
        _Public.model_validate(value)
        return DeploymentRegistry.from_config(**{key: value[key] for key in keys})

    def rejected(operation):
        try:
            operation()
        except (ValueError, TypeError, KeyError):
            return True
        return False

    STAGE = 'current_public'
    original = registry(current)
    require(len(current['execution_bindings']) == expected_count)
    require(len(current.get('kubernetes_workers', [])) == expected_count)
    require(set(hosts) == {pool['host_config_map'] for pool in current['kubernetes_workers']})
    old_pins = {row['id'] + '/' + row['revision']: original.binding_policy(row['id'], row['revision']).pins()
                for row in current['execution_bindings']}
    host_pins = {}
    STAGE = 'actual_host_bindings'
    pools_by_binding = {}
    for pool in current['kubernetes_workers']:
        require(pool.get('binding_id') is not None and pool.get('binding_revision') is not None)
        identity = (pool['binding_id'], pool['binding_revision'])
        require(identity not in pools_by_binding)
        pools_by_binding[identity] = pool
        actual = host_policy(HostConfig.model_validate(hosts[pool['host_config_map']]))
        expected = original.binding_policy(*identity)
        require(actual.pins() == expected.pins()
            and actual.supports_inputs == expected.supports_inputs
            and actual.max_timeout_seconds == expected.max_timeout_seconds
            and pool['image'].rsplit('@', 1)[1] == actual.image_digest)
        host_pins[pool['host_config_map']] = actual.pins()

    def clone(goal):
        value = copy.deepcopy(current)
        used = {row['id'] for row in current['execution_bindings']}
        used_pools = {row['pool_id'] for row in current['kubernetes_workers']}
        while len(value['execution_bindings']) < goal:
            index = len(value['execution_bindings']) - expected_count
            base = current['execution_bindings'][index % expected_count]
            binding = copy.deepcopy(base)
            binding['id'] = 'capacity-reader-' + format(index, '04d')
            require(binding['id'] not in used)
            used.add(binding['id'])
            binding['acceptance'] = None
            pool = copy.deepcopy(pools_by_binding[(base['id'], base['revision'])])
            pool['pool_id'] = 'capacity-reader-' + format(index, '04d')
            require(pool['pool_id'] not in used_pools)
            used_pools.add(pool['pool_id'])
            pool.update(binding_id=binding['id'], binding_revision=binding['revision'],
                host_config_map='capacity-reader-host-' + format(index, '04d'),
                enabled=False, uid_start=None)
            value['execution_bindings'].append(binding)
            value['kubernetes_workers'].append(pool)
        require(value['execution_bindings'][:expected_count] == current['execution_bindings'])
        require(value['kubernetes_workers'][:expected_count] == current['kubernetes_workers'])
        require(value['routes'] == current['routes'])
        return value

    STAGE = 'synthetic_catalog_preparation'
    synthetic = {count: clone(count) for count in (105, 256, 257)}
    if payload['mode'] == 'legacy':
        STAGE = 'legacy_105_rejection'
        require(rejected(lambda: _Public.model_validate(synthetic[105])))
        require(rejected(lambda: DeploymentRegistry.from_config(**{key: synthetic[105][key] for key in keys})))
        return dict(status='verified', mode='legacy', current_binding_count=expected_count,
            actual_hosts_verified=len(host_pins), rejection_105=True, imported_source_sha256=imported,
            original_policy_pins_sha256=digest(old_pins), actual_host_pins_sha256=digest(host_pins))

    verified_counts = []
    for count in (105, 256):
        STAGE = 'expanded_' + str(count)
        candidate = synthetic[count]
        resolved = registry(candidate)
        require(all(resolved.binding_policy(row['id'], row['revision']).pins() == old_pins[row['id'] + '/' + row['revision']]
                    for row in current['execution_bindings']))
        added = candidate['execution_bindings'][-1]
        # A full expanded host catalog must also pass the actual image-owned
        # HostConfig/host_policy reader, with acceptance and admission disabled.
        source = current['execution_bindings'][(count - expected_count - 1) % expected_count]
        source_pool = pools_by_binding[(source['id'], source['revision'])]
        expanded_host = copy.deepcopy(hosts[source_pool['host_config_map']])
        expanded_host.update(binding_id=added['id'], binding_revision=added['revision'],
            execution_catalog={key: copy.deepcopy(candidate[key]) for key in keys})
        expanded_host['profile']['enabled'] = False
        actual = host_policy(HostConfig.model_validate(expanded_host))
        require(actual.pins() == resolved.binding_policy(added['id'], added['revision']).pins())
        verified_counts.append(count)

    STAGE = 'expanded_257_rejection'
    require(rejected(lambda: _Public.model_validate(synthetic[257])))
    require(rejected(lambda: DeploymentRegistry.from_config(**{key: synthetic[257][key] for key in keys})))
    STAGE = 'unknown_fields_rejection'
    unknown_public = copy.deepcopy(current)
    unknown_public['unreviewed_capacity_field'] = True
    require(rejected(lambda: _Public.model_validate(unknown_public)))
    unknown_binding = copy.deepcopy(current)
    unknown_binding['execution_bindings'][0]['unreviewed_capacity_field'] = True
    require(rejected(lambda: registry(unknown_binding)))
    unknown_host = copy.deepcopy(next(iter(hosts.values())))
    unknown_host['unreviewed_capacity_field'] = True
    require(rejected(lambda: HostConfig.model_validate(unknown_host)))
    return dict(status='verified', mode='expanded', current_binding_count=expected_count,
        synthetic_binding_counts_verified=verified_counts, actual_hosts_verified=len(host_pins),
        rejection_257=True, unknown_fields_rejected=True, original_records_routes_preserved=True,
        original_policy_pins_preserved=True, imported_source_sha256=imported,
        original_policy_pins_sha256=digest(old_pins), actual_host_pins_sha256=digest(host_pins),
        history_record_limit=256)

try:
    print(json.dumps(main(), sort_keys=True))
except Exception:
    print(json.dumps(dict(status='failed', code='image_owned_capacity_reader_failed', stage=STAGE)))
    raise SystemExit(2)
'''


class ReaderError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise ReaderError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def ordinary_json(path):
    path = Path(path).absolute()
    for component in (*reversed(path.parents), path):
        observed = component.lstat()
        require(not stat.S_ISLNK(observed.st_mode)
            and not getattr(observed, 'st_file_attributes', 0) & 0x400, 'linked_reader_input')
    require(stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1
        and observed.st_size <= 4 * 1024 * 1024, 'invalid_reader_input')
    raw = path.read_bytes()
    require(len(raw) == observed.st_size, 'reader_input_changed')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_reader_input_key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    require(type(value) is dict, 'invalid_reader_input')
    def public(item):
        if isinstance(item, dict):
            for key, entry in item.items():
                require(key.lower() not in FORBIDDEN, 'private_reader_input_forbidden')
                public(entry)
        elif isinstance(item, list):
            for entry in item:
                public(entry)
    public(value)
    return value


def run_reader(image, current, hosts, *, package_root, expected_binding_count, mode,
               python_executable='/usr/local/bin/python'):
    require(type(image) is str and IMAGE.fullmatch(image), 'immutable_reader_image_required')
    require(PACKAGE.fullmatch(package_root) and '..' not in Path(package_root).parts,
            'invalid_image_package_root')
    require(re.fullmatch(r'/[A-Za-z0-9_./-]+', python_executable)
        and '..' not in Path(python_executable).parts, 'invalid_image_python_path')
    payload = canonical(dict(current=current, hosts=hosts, package_root=package_root,
                            expected_binding_count=expected_binding_count, mode=mode))
    require(len(payload) <= INPUT_LIMIT, 'reader_inputs_exceed_bound')
    name = 'agent-capacity-reader-' + uuid.uuid4().hex[:20]
    try:
        result = subprocess.run(['docker', 'run', '--rm', '--name', name, '--pull=never', '--network', 'none',
            '--read-only', '--user', '10000:10000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '64', '--memory', '1g', '--cpus', '1', '-i',
            '--entrypoint', python_executable, image, '-I', '-B', '-c', READER_PROGRAM],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=240)
    except subprocess.TimeoutExpired:
        # Killing only the Docker CLI can leave its container running. This
        # name belongs exclusively to this invocation, never to a workload.
        subprocess.run(['docker', 'rm', '--force', name], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=False, timeout=30)
        raise ReaderError('image_reader_timeout') from None
    require(len(result.stdout) <= 32768, 'image_reader_validation_failed')
    report = json.loads(result.stdout)
    if result.returncode:
        stage = report.get('stage') if type(report) is dict else None
        allowed = ('startup', 'import_image_readers', 'current_public', 'actual_host_bindings',
            'synthetic_catalog_preparation', 'legacy_105_rejection', 'expanded_105', 'expanded_256',
            'expanded_257_rejection', 'unknown_fields_rejection')
        raise ReaderError('image_reader_' + mode + '_' + (stage if stage in allowed else 'validation_failed'))
    require(type(report) is dict and report.get('status') == 'verified'
        and report.get('mode') == mode and report.get('actual_hosts_verified') == len(hosts)
        and report.get('current_binding_count') == expected_binding_count,
            'image_reader_validation_failed')
    if mode == 'expanded':
        require(report.get('synthetic_binding_counts_verified') == [105, 256]
            and report.get('rejection_257') is True and report.get('unknown_fields_rejected') is True
            and report.get('original_records_routes_preserved') is True
            and report.get('original_policy_pins_preserved') is True
            and report.get('history_record_limit') == 256, 'expanded_reader_evidence_incomplete')
    else:
        require(report.get('rejection_105') is True, 'legacy_reader_evidence_incomplete')
    return report


def source_identity():
    relative = 'agent_service/deploy/verify_capacity_reader.py'
    base = ['git', '-C', str(REPOSITORY)]
    revision = subprocess.run([*base, 'rev-parse', 'HEAD'], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=30)
    require(revision.returncode == 0 and re.fullmatch(rb'[0-9a-f]{40}\s*', revision.stdout),
            'reader_git_source_unavailable')
    for arguments in (['diff', '--quiet', 'HEAD', '--', relative],
                      ['ls-files', '--error-unmatch', '--', relative]):
        checked = subprocess.run([*base, *arguments], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=30)
        require(checked.returncode == 0, 'reader_git_source_not_clean')
    return dict(revision=revision.stdout.decode('ascii').strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        image_program_sha256=hashlib.sha256(READER_PROGRAM.encode('utf-8')).hexdigest())


def verify(*, image, legacy_image, public_path, host_config_dir, output_dir,
           package_root='/app/agent_service', legacy_package_root=None,
           expected_binding_count=95, consumer='control',
           python_executable='/usr/local/bin/python', legacy_python_executable=None):
    require(type(expected_binding_count) is int and 1 <= expected_binding_count <= 100,
            'invalid_expected_binding_count')
    require(consumer in ('control', 'candidate-worker'), 'invalid_reader_consumer')
    provenance = source_identity()
    directory = Path(output_dir)
    require(not directory.exists() and not directory.is_symlink(), 'reader_output_must_be_new')
    current = ordinary_json(public_path)
    pools = current.get('kubernetes_workers')
    require(type(pools) is list and len(pools) == expected_binding_count, 'unexpected_current_pool_count')
    hosts = {}
    for pool in pools:
        name = pool['host_config_map']
        require(type(name) is str and NAME.fullmatch(name) and name not in hosts,
                'invalid_current_host_reference')
        hosts[name] = ordinary_json(Path(host_config_dir) / name / 'config.json')
    expanded = run_reader(image, current, hosts, package_root=package_root,
        expected_binding_count=expected_binding_count, mode='expanded', python_executable=python_executable)
    legacy = run_reader(legacy_image, current, hosts, package_root=legacy_package_root or package_root,
        expected_binding_count=expected_binding_count, mode='legacy',
        python_executable=legacy_python_executable or python_executable)
    require(expanded['original_policy_pins_sha256'] == legacy['original_policy_pins_sha256']
        and expanded['actual_host_pins_sha256'] == legacy['actual_host_pins_sha256'],
            'legacy_policy_or_host_pins_changed')
    report = dict(schema='agent-catalog-reader-verification.v1', status='verified',
        source=provenance,
        image=image, legacy_image=legacy_image, public_sha256=hashlib.sha256(canonical(current)).hexdigest(),
        package_root=package_root, python_executable=python_executable,
        legacy_package_root=legacy_package_root or package_root,
        legacy_python_executable=legacy_python_executable or python_executable,
        host_inputs_sha256=hashlib.sha256(canonical(hosts)).hexdigest(),
        consumer=consumer, expanded=expanded, legacy=legacy,
        history_record_limit=256, original_actual_hosts_verified=len(hosts),
        image_owned_code_only=True, repository_code_mounted=False, network='none',
        root_filesystem_read_only=True, runtime_secret_mounts_accessed=False,
        database_access=False, model_requests=0, cluster_writes=0, uid_ledger_accessed=False,
        synthetic_acceptance_created=False, runtime_process_health_verified=False,
        publication_authorized=False)
    raw = canonical(report) + b'\n'
    evidence_sha256 = hashlib.sha256(raw).hexdigest()
    roles = ('control-api', 'control-dispatcher', 'model-proxy') if consumer == 'control' else ('candidate-worker',)
    compatibility = dict(schema='agent-catalog-consumer-compatibility.v1', consumers=[
        dict(role=role, image=image, history_record_limit=256, evidence_sha256=evidence_sha256) for role in roles])
    directory.mkdir(parents=True)
    (directory / 'reader-verification.json').write_bytes(raw)
    (directory / 'compatibility.json').write_bytes(canonical(compatibility) + b'\n')
    return dict(status='verified', image=image, history_record_limit=256,
        evidence_sha256=evidence_sha256, consumer_roles=list(roles), actual_hosts_verified=len(hosts),
        publication_authorized=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--legacy-image', required=True)
    parser.add_argument('--public', required=True, type=Path)
    parser.add_argument('--host-config-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--package-root', default='/app/agent_service')
    parser.add_argument('--legacy-package-root')
    parser.add_argument('--python-executable', default='/usr/local/bin/python')
    parser.add_argument('--legacy-python-executable')
    parser.add_argument('--expected-binding-count', type=int, default=95)
    parser.add_argument('--consumer', choices=('control', 'candidate-worker'), default='control')
    args = parser.parse_args(argv)
    try:
        report = verify(image=args.image, legacy_image=args.legacy_image, public_path=args.public,
            host_config_dir=args.host_config_dir, output_dir=args.output_dir,
            package_root=args.package_root, legacy_package_root=args.legacy_package_root,
            expected_binding_count=args.expected_binding_count, consumer=args.consumer,
            python_executable=args.python_executable, legacy_python_executable=args.legacy_python_executable)
        print(json.dumps(report, sort_keys=True))
        return 0
    except ReaderError as error:
        print(json.dumps(dict(schema='agent-catalog-reader-verification.v1', status='blocked',
                              code=str(error), publication_authorized=False)))
        return 2
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        print(json.dumps(dict(schema='agent-catalog-reader-verification.v1', status='blocked',
                              code='capacity_reader_not_verified', publication_authorized=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
