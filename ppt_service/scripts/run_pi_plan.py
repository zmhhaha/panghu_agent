"""Explicit paid CLI development run of one Pi plan_initial candidate.

Runs only on a trusted Linux root Docker host. The opaque upstream env file is
passed to the proxy's Docker --env-file; its contents are never opened here.
This command creates no website confirmation, accepted checkpoint or publication.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
import threading
import time
import uuid

from ppt_service.control.execution import Attempt, ControlError, RuntimeConfig, RuntimeState
from ppt_service.control.pi_container import PINNED_IMAGE, ROOT_CAPABILITIES
from ppt_service.control.pi_staging import (PiStager, MAX_ASSETS, MAX_ASSET_BYTES, MAX_ASSET_TOTAL,
    checked_directory, require, safe_path)
from ppt_service.control.pi_uid import PiUIDAllocator, UIDScope, _parents, _sync_directory
from ppt_service.control.pods import Docker as ControlDocker, DockerSandbox, Firewall, LABEL, mount
from ppt_service.control.staging import EXT, atomic_json, copy_file, open_regular, read_json
from ppt_service.worker.container_lease import ContainerLaunch
from ppt_service.worker.model_grant import ModelIdentity
from ppt_service.worker.pi_context import PreparedPptContext, bounded_json

RUNTIME_REVISION = 'pi0.73.1-ppt-development-v1'
SKILL_BUNDLE_REVISION = 'ppt-master-c350bc31-development-v1'
PYTHON = '/opt/hermes/.venv/bin/python'
UPSTREAM_IP = '62.234.50.20'
DEV_LABEL = 'io.ppt-service.pi-development'
CODE_ROOT = Path(__file__).absolute().parents[2]
HEX = re.compile('[0-9a-f]{64}')
HEALTH = ("import json,urllib.request; "
          "o=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
          "r=o.open('http://127.0.0.1:8080/healthz',timeout=2); "
          "assert r.status==200 and json.load(r)=={'status':'ok'}")
INSPECT = ('{"id":{{json .Id}},"image":{{json .Image}},"labels":{{json .Config.Labels}},'
           '"state":{{json .State}},"pid_mode":{{json .HostConfig.PidMode}},'
           '"auto_remove":{{json .HostConfig.AutoRemove}},"restart":{{json .HostConfig.RestartPolicy}}}')


def absolute(raw):
    path = Path(raw)
    require(path.is_absolute() and '..' not in path.parts
            and not any(c in str(path) for c in ',\n\r'), 'explicit_safe_absolute_path_required')
    return path


def ordinary(path, limit=None):
    checked_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, 'st_file_attributes', 0) & 0x400, 'ordinary_unlinked_file_required')
    if limit is not None:
        require(info.st_size <= limit, 'source_size_limit')
    return info


def private_json(path, value):
    """Atomic root-owned controller record; no credential values belong here."""
    checked_directory(path.parent)
    if path.exists() or path.is_symlink():
        ordinary(path)
    atomic_json(path, value)
    path.chmod(0o600)
    _sync_directory(path.parent)


def immutable_json(path, value):
    require(not os.path.lexists(path), 'launch_metadata_already_exists')
    atomic_json(path, value)
    path.chmod(0o444)
    _sync_directory(path.parent)


def source_key(raw):
    require(type(raw) is str, 'invalid_source_path')
    if raw.startswith('/work/inputs/'):
        raw = raw[len('/work/inputs/'):]
    else:
        require(not raw.startswith('/'), 'invalid_source_path')
    return safe_path(raw)


def snapshot_from_legacy(task, inputs, data_root, *, project_id=None):
    """Copy only declared ordinary sources; legacy identifiers confer no authority."""
    allowed = {'schema_version', 'job_id', 'attempt', 'phase', 'session_id', 'input_snapshot_sha256',
               'requirements', 'assets', 'feedback', 'base_version_id'}
    require(type(task) is dict and set(task) <= allowed and task.get('phase') == 'plan_initial'
            and task.get('base_version_id') is None and task.get('feedback', '') == '', 'initial_task_required')
    requirements = task.get('requirements')
    require(type(requirements) is dict and requirements.get('external_model_acknowledged') is True,
            'external_model_acknowledgement_required')
    title = requirements.get('title', 'Pi development planning')
    require(type(title) is str and 0 < len(title) <= 200, 'invalid_title')
    pages = requirements.get('page_count', requirements.get('pages', 6))
    require(type(pages) is int and 3 <= pages <= 20, 'invalid_slide_count')
    if 'pages' in requirements and 'page_count' in requirements:
        require(requirements['pages'] == requirements['page_count']
                and type(requirements['pages']) is int, 'invalid_slide_count')
    settings = {'page_count': pages}
    limits = {'purpose': 4000, 'audience': 4000, 'language': 50, 'style': 4000, 'requirements': 12000}
    for key, limit in limits.items():
        if key in requirements:
            require(type(requirements[key]) is str and len(requirements[key]) <= limit, 'invalid_requirements')
            settings[key] = requirements[key]
    if 'purpose' not in settings and 'goal' in requirements:
        require(type(requirements['goal']) is str and len(requirements['goal']) <= 4000, 'invalid_requirements')
        settings['purpose'] = requirements['goal']
    # Legacy acceptance notes remain bounded task data. No legacy executable,
    # provider, endpoint or runtime fields are interpreted as configuration.
    extras = {key: value for key, value in requirements.items() if key not in set(limits) |
              {'title', 'pages', 'page_count', 'goal', 'external_model_acknowledged',
               'source_inventory', 'parsed_materials', 'revision_pages'}}
    pid = project_id or str(uuid.uuid4())
    assets = task.get('assets', [])
    require(type(assets) is list and len(assets) <= MAX_ASSETS, 'asset_limit')
    checked_directory(inputs)
    rows = []; seen = set(); aliases = []; total = 0
    by_suffix = {suffix: mime for mime, suffix in EXT.items()}
    for asset in assets:
        require(type(asset) is dict, 'invalid_source_mapping')
        key = source_key(asset.get('path'))
        require(key not in seen, 'duplicate_source')
        seen.add(key)
        suffix = Path(key).suffix.lower()
        if suffix == '.jpeg':
            suffix = '.jpg'
        mime = by_suffix.get(suffix)
        require(mime is not None and asset.get('media_type', mime) == mime, 'unsupported_media')
        path = inputs / key
        checked_directory(path.parent)
        ordinary(path, MAX_ASSET_BYTES)
        h = hashlib.sha256(); size = 0
        with open_regular(inputs, key, MAX_ASSET_BYTES) as stream:
            before = os.fstat(stream.fileno())
            while chunk := stream.read(65536):
                h.update(chunk); size += len(chunk)
                require(size <= MAX_ASSET_BYTES, 'source_size_limit')
            after = os.fstat(stream.fileno())
            require(before.st_size == size and before.st_mtime_ns == after.st_mtime_ns, 'source_changed')
        require(size > 0, 'source_size_limit')
        total += size
        require(total <= MAX_ASSET_TOTAL, 'source_size_limit')
        if 'bytes' in asset:
            require(type(asset['bytes']) is int and asset['bytes'] == size, 'source_size_mismatch')
        if 'sha256' in asset:
            require(type(asset['sha256']) is str and asset['sha256'] == h.hexdigest(), 'source_hash_mismatch')
        aid = str(uuid.uuid4())
        storage_key = f'projects/{pid}/assets/{aid}/original'
        copied = copy_file(inputs, key, data_root / storage_key, MAX_ASSET_BYTES, h.hexdigest())
        require(copied['bytes'] == size, 'source_size_mismatch')
        rows.append(dict(id=aid, storage_key=storage_key, sha256=h.hexdigest(), media_type=mime, bytes=size))
        alias = asset.get('asset_id', asset.get('id', Path(key).name))
        require(type(alias) is str and len(alias) <= 200, 'invalid_source_alias')
        aliases.append({'source': alias, 'staged_asset_id': aid})
    if extras or aliases:
        detail = bounded_json({'legacy_acceptance_requirements': extras, 'source_aliases': aliases}, 12000)
        settings['requirements'] = settings.get('requirements', '') + '\n' + detail
        require(len(settings['requirements']) <= 12000, 'invalid_requirements')
    core = dict(project_id=pid, settings=settings, title=title, assets=rows)
    return dict(core, hash=hashlib.sha256(bounded_json(core).encode()).hexdigest())


def labels(attempt, nonce, role):
    identity = ModelIdentity(attempt.project_id, attempt.job_id, attempt.attempt, attempt.token, attempt.phase)
    return {LABEL: attempt.uid, **{DEV_LABEL + '.' + key: str(value) for key, value in
            dict(asdict(identity), nonce=nonce, role=role, purpose='paid-cli-development', image=PINNED_IMAGE).items()}}


def container_args(attempt, layout, launch, *, role, code_root, upstream_env, key, deadline):
    """Fixed root entries and mount policy; no legacy task field selects argv."""
    wb, eb, _ = Firewall.names(attempt.uid)
    name = 'ppt-pi-plan-' + attempt.uid.replace('-', '') + '-' + role
    args = ['create', '--name', name, '--restart', 'no', '--user', '0:0', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--ipc', 'private',
            '--pids-limit', '128', '--memory', '1g' if role == 'worker' else '512m',
            '--cpus', '2' if role == 'worker' else '0.5', '--dns', '127.0.0.1',
            '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1', '--workdir', '/',
            '--stop-timeout', '10', '--log-driver', 'local', '--log-opt', 'max-size=20m',
            '--log-opt', 'max-file=3', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777']
    for name, value in labels(attempt, launch['nonce'], role).items():
        args += ['--label', name + '=' + value]
    args += mount(code_root, '/code', True) + ['--env', 'PYTHONPATH=/code', '--env', 'PYTHONDONTWRITEBYTECODE=1',
             '--env', 'PYTHONUNBUFFERED=1', '--env', 'PPT_MODEL=gpt-6.1-sol']
    if role == 'proxy':
        args += ['--network', eb, '--ip', '172.30.241.2', '--add-host',
                 'custom-llm-proxy.panghuer.top:' + UPSTREAM_IP,
                 '--env-file', str(upstream_env), '--env', 'PPT_PROXY_CLIENT_KEY=' + key,
                 '--env', 'PPT_UPSTREAM_BASE_URL=https://custom-llm-proxy.panghuer.top/v1']
        args += mount(layout.task / 'domain-launch.json', '/work/task/domain-launch.json', True)
        args += mount(layout.audit / 'container-authority', '/work/audit/container-authority', True)
        args += mount(layout.audit / 'proxy-ledger', '/audit')
        args += ['--entrypoint', PYTHON, PINNED_IMAGE, '-m', 'ppt_service.worker.pi_proxy_entry']
    else:
        require(role == 'worker', 'invalid_container_role')
        for capability in ROOT_CAPABILITIES:
            args += ['--cap-add', capability]
        args += ['--network', wb, '--ip', '172.30.240.3', '--add-host', 'model-proxy:172.30.240.2',
                 '--env', 'PPT_MODEL_PROXY_ORIGIN=http://model-proxy:8080',
                 '--env', 'PPT_MODEL_API_KEY=' + key, '--env', 'PPT_UPSTREAM_API_KEY=',
                 '--env', 'PPT_PROXY_CLIENT_KEY=', '--env', 'PPT_DEADLINE_SECONDS=' + str(deadline),
                 '--tmpfs', '/work/runtime:rw,nosuid,nodev,size=256m,mode=1777']
        for part in ('inputs', 'task', 'base', 'project', 'result', 'audit'):
            args += mount(getattr(layout, part), '/work/' + part, part in ('inputs', 'task', 'base'))
        args += ['--entrypoint', PYTHON, PINNED_IMAGE, '/code/ppt_service/worker/pi_stage_entry.py']
    return args


def decoded(raw):
    require(type(raw) is bytes and len(raw) <= 2 * 1024**2, 'invalid_docker_response')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise ControlError('invalid_docker_response') from None


def inspect_owned(docker, cid, expected_labels):
    require(type(cid) is str and HEX.fullmatch(cid), 'invalid_container_id')
    _, raw = docker.call('inspect', '--format', INSPECT, cid)
    obj = decoded(raw)
    require(type(obj) is dict and obj.get('id') == cid and obj.get('image') == PINNED_IMAGE,
            'foreign_container')
    actual_labels = obj.get('labels')
    require(type(actual_labels) is dict and all(actual_labels.get(k) == v for k, v in expected_labels.items())
            and {k: v for k, v in actual_labels.items() if k.startswith(DEV_LABEL + '.')} ==
            {k: v for k, v in expected_labels.items() if k.startswith(DEV_LABEL + '.')}, 'foreign_container')
    require(obj.get('pid_mode') in ('', 'private') and obj.get('auto_remove') is False
            and obj.get('restart') == {'Name': 'no', 'MaximumRetryCount': 0}, 'unsafe_container_policy')
    state = obj.get('state')
    require(type(state) is dict and type(state.get('Running')) is bool and type(state.get('Pid')) is int
            and type(state.get('Restarting')) is bool, 'invalid_container_observation')
    return state


def verify_topology(docker, containers, networks, *, attempt, nonce, pending_roles=()):
    require(set(pending_roles) <= {'worker', 'proxy'}, 'invalid_pending_container_role')
    for role, expected in (('worker', {networks[0]: '172.30.240.3'}),
                           ('proxy', {networks[0]: '172.30.240.2', networks[1]: '172.30.241.2'})):
        state = inspect_owned(docker, containers[role], labels(attempt, nonce, role))
        pending = role in pending_roles
        if pending:
            require(state.get('Status') == 'created' and state['Running'] is False
                    and state['Pid'] == 0 and state['Restarting'] is False, 'container_not_pending')
        else:
            require(state.get('Status') == 'running' and state['Running'] is True
                    and state['Pid'] > 0 and state['Restarting'] is False, 'container_not_running')
        _, raw = docker.call('inspect', '--format', '{{json .NetworkSettings.Networks}}', containers[role])
        actual = decoded(raw)
        require(type(actual) is dict and set(actual) == set(expected), 'container_network_mismatch')
        for name, ip in expected.items():
            endpoint = actual[name]
            require(type(endpoint) is dict, 'container_network_mismatch')
            assigned = endpoint.get('IPAddress')
            if pending and assigned == '':
                # Docker 29 does not assign created endpoints yet. Only its
                # explicit static request is meaningful until the process starts.
                ipam = endpoint.get('IPAMConfig')
                require(type(ipam) is dict and ipam.get('IPv4Address') == ip, 'container_network_mismatch')
            else:
                require(assigned == ip, 'container_network_mismatch')


def write_authority(path, authority, *, clock, state, revoked=False, container_id=None):
    authority.update(seq=authority.get('seq', 0) + 1, state=state, revoked=revoked,
                     deadline_monotonic=float(clock() + (0 if revoked else 90)))
    if container_id is not None:
        authority['container_id'] = container_id
    private_json(path, authority)


def stop_owned(docker, cid, expected_labels):
    try:
        state = inspect_owned(docker, cid, expected_labels)
        if state['Running'] or state.get('Status') == 'paused':
            docker.call('stop', '--time', '10', cid, timeout=15, check=False)
            state = inspect_owned(docker, cid, expected_labels)
            if state['Running']:
                docker.call('kill', cid, timeout=10, check=False)
                state = inspect_owned(docker, cid, expected_labels)
        return (state['Running'] is False and state['Pid'] == 0 and state['Restarting'] is False
                and state.get('Status') in ('exited', 'created'))
    except Exception:
        return False


def _safe_error(exc):
    value = exc.code if isinstance(exc, ControlError) else 'development_run_failed'
    return value if type(value) is str and re.fullmatch('[a-z_]{1,80}', value) else 'development_run_failed'


def execute_prepared(config, attempt, layout, context, launch, *, root, upstream_env, code_root,
                     deadline, docker, firewall, interrupted, clock=time.monotonic,
                     pause=time.sleep, boot=None):
    """One owned lifecycle; injected command/time boundaries support offline tests."""
    identity = asdict(ModelIdentity(attempt.project_id, attempt.job_id, attempt.attempt, attempt.token, attempt.phase))
    state = dict(schema_version=1, scope='paid_cli_development', identity=identity, status='starting',
                 paths=dict(root=str(root), attempt=str(layout.root), project=str(layout.project),
                            result=str(layout.result), audit=str(layout.audit),
                            proxy_usage=str(layout.audit/'proxy-ledger/usage.jsonl'), logs=str(root/'logs')),
                 worker_stopped=False, proxy_stopped=False, authority_revoked=False,
                 firewall_installed=False, firewall_removed=False, networks_removed=False,
                 network_preflight_passed=False, delivery_published=False, artifact_acceptance='not_performed')
    authority_path = layout.audit/'container-authority/control.json'
    authority = {key: launch[key] for key in ('schema', 'identity', 'nonce', 'pi_uid', 'preview_uid', 'image_digest')}
    boot = boot or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(str(uuid.UUID(boot)) == boot, 'invalid_boot_id')
    authority.update(boot_id=boot, container_id=None)
    containers = {}; attempted = set(); created_networks = []; network_ids = {}; firewall_owned = False
    wb, eb, chain = Firewall.names(attempt.uid)
    networks = (wb, eb)
    handle = dict(schema_version=1, scope='paid_cli_development', identity=identity, nonce=launch['nonce'],
                  image_digest=PINNED_IMAGE, containers={}, networks={})
    code = 2
    def persist_handle():
        handle['containers'] = dict(containers)
        handle['networks'] = dict(network_ids)
        private_json(root/'execution-handle.json', handle)
    def check_active():
        require(not interrupted.is_set(), 'operator_interrupted')
    try:
        private_json(root/'run-state.json', state)
        persist_handle()
        _, image = docker.call('image', 'inspect', '--format', '{{.Id}}', PINNED_IMAGE)
        require(image.decode('ascii').strip() == PINNED_IMAGE, 'candidate_image_not_present')
        # A pre-existing chain is never ours, including a stale operator run.
        exists, _ = firewall.ipt('-S', chain, check=False)
        require(exists != 0, 'firewall_identity_already_exists')
        for index, (name, subnet) in enumerate(((wb, '172.30.240.0/29'), (eb, '172.30.241.0/29'))):
            check_active()
            argv = ['network', 'create', '--driver', 'bridge', '--subnet', subnet,
                    '--opt', 'com.docker.network.bridge.enable_icc=false',
                    '--opt', 'com.docker.network.bridge.name=' + name,
                    '--label', DEV_LABEL + '=' + attempt.uid]
            if index == 0:
                argv += ['--internal']
            _, raw = docker.call(*argv, name)
            network_id = raw.decode('ascii').strip()
            require(HEX.fullmatch(network_id), 'invalid_network_id')
            created_networks.append(name); network_ids[name] = network_id
            persist_handle()
            _, raw = docker.call('network', 'inspect', '--format', '{{json .}}', name)
            actual = decoded(raw)
            require(type(actual) is dict and actual.get('Id') == network_id
                    and actual.get('Internal') is (index == 0)
                    and actual.get('Labels', {}).get(DEV_LABEL) == attempt.uid
                    and actual.get('Options', {}).get('com.docker.network.bridge.name') == name
                    and actual.get('Options', {}).get('com.docker.network.bridge.enable_icc') == 'false'
                    and [row.get('Subnet') for row in actual.get('IPAM', {}).get('Config', [])] == [subnet],
                    'network_cage_mismatch')
        firewall_owned = True
        firewall.install(attempt.uid, UPSTREAM_IP)
        state['firewall_installed'] = True
        key = secrets.token_urlsafe(48)
        write_authority(authority_path, authority, clock=clock, state='reserved')
        for role in ('proxy', 'worker'):
            check_active()
            attempted.add(role)
            _, raw = docker.call(*container_args(attempt, layout, launch, role=role,
                code_root=code_root, upstream_env=upstream_env, key=key, deadline=deadline))
            cid = raw.decode('ascii').strip()
            require(HEX.fullmatch(cid), 'invalid_container_id')
            containers[role] = cid
            persist_handle()
            inspect_owned(docker, cid, labels(attempt, launch['nonce'], role))
        del key
        docker.call('network', 'connect', '--ip', '172.30.240.2', '--alias', 'model-proxy', wb, containers['proxy'])
        verify_topology(docker, containers, networks, attempt=attempt, nonce=launch['nonce'],
                        pending_roles=('worker', 'proxy'))
        # Worker CID and both container identities are durable before either start.
        write_authority(authority_path, authority, clock=clock, state='starting', container_id=containers['worker'])
        persist_handle()
        check_active()
        started = clock(); last_renewal = started
        docker.call('start', containers['proxy'])
        verify_topology(docker, containers, networks, attempt=attempt, nonce=launch['nonce'],
                        pending_roles=('worker',))
        healthy = False
        for _ in range(15):
            check_active()
            require(clock() - started < deadline, 'controller_deadline_exceeded')
            require(inspect_owned(docker, containers['proxy'], labels(attempt, launch['nonce'], 'proxy'))['Running'],
                    'proxy_exited_before_worker')
            result, _ = docker.call('exec', containers['proxy'], PYTHON, '-c', HEALTH, timeout=5, check=False)
            if result == 0:
                healthy = True
                break
            if clock() - last_renewal >= 10:
                write_authority(authority_path, authority, clock=clock, state='starting')
                last_renewal = clock()
            pause(1)
        require(healthy, 'proxy_readiness_failed')
        check_active()
        docker.call('start', containers['worker'])
        verify_topology(docker, containers, networks, attempt=attempt, nonce=launch['nonce'])
        check_active()
        write_authority(authority_path, authority, clock=clock, state='running')
        state['status'] = 'running'
        private_json(root/'run-state.json', state)
        while True:
            check_active()
            require(clock() - started < deadline, 'controller_deadline_exceeded')
            worker = inspect_owned(docker, containers['worker'], labels(attempt, launch['nonce'], 'worker'))
            if not worker['Running']:
                require(type(worker.get('ExitCode')) is int, 'invalid_worker_exit_code')
                state['worker_exit_code'] = worker['ExitCode']
                state['status'] = 'candidate_execution_finished' if worker['ExitCode'] == 0 else 'worker_failed'
                code = 0 if worker['ExitCode'] == 0 else 2
                break
            require(inspect_owned(docker, containers['proxy'], labels(attempt, launch['nonce'], 'proxy'))['Running'],
                    'proxy_exited_while_worker_running')
            if clock() - last_renewal >= 10:
                require(authority['deadline_monotonic'] > clock(), 'controller_lease_expired')
                write_authority(authority_path, authority, clock=clock, state='running')
                last_renewal = clock()
            pause(0.25)
    except Exception as exc:
        state.update(status='interrupted' if interrupted.is_set() else 'failed', error_code=_safe_error(exc))
    finally:
        # Revocation always precedes a stop, including timeout, signal and setup
        # failure. An ambiguous create or failed identity proof retains the cage.
        try:
            write_authority(authority_path, authority, clock=clock, state='revoking', revoked=True)
            state['authority_revoked'] = True
        except Exception:
            state.update(status='stop_unproven', error_code='authority_revocation_failed')
        for role in ('worker', 'proxy'):
            cid = containers.get(role)
            state[role + '_stopped'] = (role not in attempted if cid is None else
                stop_owned(docker, cid, labels(attempt, launch['nonce'], role)))
            if cid is not None and state[role + '_stopped']:
                try:
                    _, raw = docker.call('logs', cid, timeout=15, check=False)
                    log = root/'logs'/(role + '.private.log')
                    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as output:
                        output.write(raw)
                except Exception:
                    state[role + '_log_retained'] = False
        stopped = state['worker_stopped'] and state['proxy_stopped'] and state['authority_revoked']
        try:
            write_authority(authority_path, authority, clock=clock,
                            state='stopped' if stopped else 'stop_unproven', revoked=True)
        except Exception:
            stopped = False
        if stopped:
            try:
                if 'worker' in containers:
                    report = read_json(layout.audit, 'network-preflight.json', 64 * 1024)
                    state['network_preflight_passed'] = type(report) is dict and report.get('passed') is True
                    if not state['network_preflight_passed'] and code == 0:
                        state['status'] = 'network_preflight_failed'; code = 2
                    if code == 0 and (layout.result/'manifest.json').exists():
                        candidate = read_json(layout.result, 'manifest.json', 4 * 1024**2)
                        if type(candidate) is dict and candidate.get('status') == 'needs_input':
                            state['status'] = 'needs_input'; code = 2
            except Exception:
                if code == 0:
                    state['status'] = 'network_evidence_missing'; code = 2
            try:
                for name in reversed(created_networks):
                    _, raw = docker.call('network', 'inspect', '--format', '{{json .}}', name)
                    network = decoded(raw)
                    require(type(network) is dict and network.get('Id') == network_ids[name]
                            and network.get('Labels', {}).get(DEV_LABEL) == attempt.uid,
                            'foreign_network')
                    for role, cid in containers.items():
                        if name == wb or role == 'proxy':
                            docker.call('network', 'disconnect', '--force', name, cid)
                    docker.call('network', 'rm', name)
                state['networks_removed'] = True
                if firewall_owned:
                    firewall.remove(attempt.uid)
                    removed, _ = firewall.ipt('-S', chain, check=False)
                    require(removed != 0, 'firewall_removal_unproven')
                    for rule in (('-C', 'DOCKER-USER', '-j', chain),
                                 ('-C', 'INPUT', '-i', wb, '-j', 'DROP'),
                                 ('-C', 'INPUT', '-i', eb, '-j', 'DROP')):
                        remaining, _ = firewall.ipt(*rule, check=False)
                        require(remaining != 0, 'firewall_removal_unproven')
                state['firewall_removed'] = True
            except Exception:
                state['status'] = 'cleanup_failed'; code = 2
        else:
            state['status'] = 'stop_unproven'; code = 2
        private_json(root/'run-state.json', state)
    return state, code


def run(args, *, docker=None, firewall=None, sandbox=None):
    require(args.execute_paid is True, 'explicit_paid_execution_required')
    require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
    require(type(args.deadline_seconds) is int and 60 <= args.deadline_seconds <= 600, 'invalid_deadline')
    root, task_path, inputs, upstream = map(absolute, (args.root, args.task, args.inputs, args.upstream_env))
    _parents(root.parent); _parents(inputs.parent); checked_directory(inputs)
    _parents(task_path.parent); _parents(upstream.parent)
    require(not os.path.lexists(root), 'exclusive_new_root_required')
    ordinary(task_path, 64 * 1024)
    info = ordinary(upstream)
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, 'private_upstream_env_required')
    require(upstream != task_path and not upstream.is_relative_to(inputs)
            and not inputs.is_relative_to(root) and not task_path.is_relative_to(root)
            and not upstream.is_relative_to(root) and not upstream.is_relative_to(CODE_ROOT),
            'upstream_or_input_path_overlap')
    _parents(CODE_ROOT)
    task = read_json(task_path.parent, task_path.name, 64 * 1024)
    old_umask = os.umask(0o077)
    previous = {}; interrupted = threading.Event()
    try:
        root.mkdir(mode=0o700)
        (root/'logs').mkdir(mode=0o700)
        private_json(root/'run-state.json', dict(schema_version=1, scope='paid_cli_development',
            status='preparing', paths={'root': str(root)}, delivery_published=False,
            artifact_acceptance='not_performed'))
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: interrupted.set())
        config = RuntimeConfig(root/'data', state=RuntimeState.CONFIGURED,
            upstream_env_file=upstream, allow_paid_execution=True, policy_approved=True,
            code_root=CODE_ROOT/'ppt_service/control')
        config.require_paid_admission()
        stager = PiStager(config, RUNTIME_REVISION, SKILL_BUNDLE_REVISION)
        snapshot = snapshot_from_legacy(task, inputs, stager.root)
        attempt = Attempt(str(uuid.uuid4()), snapshot['project_id'], 1, 1, 'plan_initial',
                          snapshot, 'pi-dev-' + uuid.uuid4().hex)
        scope = UIDScope('ppt', 'cli-development', 'single-operator-root', 100000, 199999)
        allocator = PiUIDAllocator.initialize(root/'uid-ledger', scope)
        binding = {key: getattr(attempt, key) for key in ('project_id', 'job_id', 'attempt', 'token')}
        pi_uid = allocator.allocate(**binding, role='pi')
        preview_uid = allocator.allocate(**binding, role='preview')
        layout, inventory = stager.stage(attempt)
        docker = docker or ControlDocker()
        firewall = firewall or Firewall()
        # Parsing uses the existing reviewed network-none base image. Actual Pi
        # and proxy execution above are independently pinned to PINNED_IMAGE.
        parser = sandbox or DockerSandbox(config, docker=docker)
        parser.scope_uid = attempt.uid
        require(not interrupted.is_set(), 'operator_interrupted')
        parsed = parser.parse(layout.inputs, inventory)
        stager.finish_inputs(attempt, layout, parsed)
        context = PreparedPptContext.from_stager(stager, attempt, layout)
        launch = dict(schema=1, identity=asdict(ModelIdentity(attempt.project_id, attempt.job_id, 1, 1, 'plan_initial')),
                      nonce=secrets.token_hex(32), pi_uid=pi_uid, preview_uid=preview_uid,
                      image_digest=PINNED_IMAGE, mode='complete')
        ContainerLaunch.parse(launch)
        layout.task.chmod(0o755)
        (layout.task/'task.json').chmod(0o444)
        immutable_json(layout.task/'mounted-context.json', context.export_for_mounts())
        immutable_json(layout.task/'domain-launch.json', launch)
        for folder in (layout.inputs, layout.base):
            for parent, dirs, files in os.walk(folder):
                Path(parent).chmod(0o555)
                for name in files:
                    (Path(parent)/name).chmod(0o444)
        for folder in (layout.project, layout.result):
            folder.chmod(0o755)
        for name in ('container-authority', 'proxy-ledger'):
            (layout.audit/name).mkdir(mode=0o700)
        return execute_prepared(config, attempt, layout, context, launch, root=root, upstream_env=upstream,
            code_root=CODE_ROOT, deadline=args.deadline_seconds, docker=docker, firewall=firewall,
            interrupted=interrupted)
    except Exception as exc:
        # A fresh failed operator root is a retained tombstone, never reset.
        state = dict(schema_version=1, scope='paid_cli_development', status='failed',
                     paths={'root': str(root)}, error_code=_safe_error(exc),
                     delivery_published=False, artifact_acceptance='not_performed')
        if (root/'run-state.json').exists():
            private_json(root/'run-state.json', state)
        return state, 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        os.umask(old_umask)


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='New exclusive absolute operator directory')
    parser.add_argument('--task', required=True, help='Existing legacy initial sample task JSON')
    parser.add_argument('--inputs', required=True, help='Existing absolute source inputs directory')
    parser.add_argument('--upstream-env', required=True, help='Opaque root-private proxy-only Docker env file')
    parser.add_argument('--execute-paid', action='store_true', required=True, help='Execute one paid development plan')
    parser.add_argument('--deadline-seconds', type=int, default=600, help='Hard run limit, 60 through 600 seconds')
    return parser


def main(argv=None):
    args = argument_parser().parse_args(argv)
    try:
        state, code = run(args)
    except Exception as exc:
        state, code = dict(schema_version=1, scope='paid_cli_development', status='rejected',
                           error_code=_safe_error(exc), delivery_published=False), 2
    print(json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(',', ':')), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
