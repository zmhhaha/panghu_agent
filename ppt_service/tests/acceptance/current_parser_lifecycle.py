#!/usr/bin/env python3
"""Actual current Kubernetes parser deadline, exact stop and healthy follow-up.

Execute only on the cluster's Linux operator host, from committed Git source.
The production controller's installed SDK creates parser Pods over a new private
synthetic .acceptance/run-id subtree. No application DB or model is contacted.
Only a test Pod's UID/container-ID/PID/starttime-bound parser receives SIGSTOP.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import random
import re
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile


NAMESPACE = 'ppt-service'
CONTROLLER = 'ppt-hermes-controller'
CONTROLLER_IMAGE = 'arm-cluster-master:5000/ppt-agent-controller:latest@sha256:345338fa565e288b06063665a01ca2979ac4a6aa8f52cd1068b37d17e5b6a492'
PARSER_IMAGE = 'arm-cluster-master:5000/ppt-agent-validator:latest@sha256:4492a878db0d575f20542379a61986500a1c5263b5ecc5460026a87937549bc9'
RUN_PATTERN = re.compile(r'ppt-r2-life-[a-f0-9]{20}\Z')
SCOPE = 'ppt-service.panghuer.top/attempt'
ROLE = 'ppt-service.panghuer.top/role'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(65536):
            h.update(chunk)
    return h.hexdigest()


def output(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def build_material(path):
    """Valid many-node DOCX; each XML part remains below actual limits."""
    rng = random.Random(2026110402)
    types = b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    document = (b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b'<w:p><w:r><w:t>R2 lifecycle synthetic source.</w:t></w:r></w:p></w:document>')
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('[Content_Types].xml', types)
        archive.writestr('word/document.xml', document)
        for index in range(64):
            entropy = rng.randbytes(65000).hex().encode('ascii')
            xml = b'<root><entropy>' + entropy + b'</entropy>' + b'<a/>' * 90000 + b'</root>'
            archive.writestr(f'word/extra-{index:02d}.xml', xml)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        assert len(entries) == 66
        assert sum(x.file_size for x in entries) <= 64 * 1024**2
        assert all(x.file_size <= 8 * 1024**2 and x.file_size <= max(1, x.compress_size) * 100 for x in entries)
    assert path.stat().st_size <= 25 * 1024**2


def inner(run_id):
    """Runs inside the existing controller, importing only its installed SDK."""
    if not RUN_PATTERN.fullmatch(run_id):
        raise RuntimeError('invalid_owned_run_id')
    from ppt_service.control.agent_validation import AgentKubernetesSandbox
    from ppt_service.control import execution, kubernetes, kubernetes_sandbox
    from ppt_service.control.execution import ControlError, RuntimeConfig
    from ppt_service.control.kubernetes import KubeSettings
    from ppt_service.control.staging import atomic_json

    base = Path(os.environ['DATA_ROOT']).absolute()
    parent = base / '.acceptance'
    for path in (base, parent):
        if path.is_symlink():
            raise RuntimeError('private_acceptance_root_must_not_link')
    parent.mkdir(exist_ok=True)
    root = parent / run_id
    root.mkdir(mode=0o700, exist_ok=False)
    scope = str(uuid.uuid5(uuid.NAMESPACE_URL, run_id))
    marker = {'run_id': run_id, 'scope_uid': scope, 'synthetic_only': True}
    atomic_json(root / 'owner.json', marker)
    settings = KubeSettings.env()
    if settings.namespace != NAMESPACE or settings.worker_image != PARSER_IMAGE:
        raise RuntimeError('current_parser_configuration_changed')
    settings = replace(settings, pvc_data_prefix=settings.pvc_data_prefix + '/.acceptance/' + run_id)
    config = RuntimeConfig(data_root=root)
    if config.parse_timeout != 30:
        raise RuntimeError('unexpected_production_parse_timeout')
    sandbox = AgentKubernetesSandbox(config, settings)
    transport = sandbox.transport
    handle = transport.backend.register_sandbox_scope(scope)
    sandbox.scope_uid = scope
    modules = {name: {'path': module.__file__, 'sha256': sha(module.__file__)}
               for name, module in [('execution', execution), ('kubernetes', kubernetes), ('kubernetes_sandbox', kubernetes_sandbox)]}
    inputs = root / 'inputs'
    inputs.mkdir(mode=0o755)
    material = inputs / 'synthetic.docx'
    build_material(material)
    source = {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, run_id + ':source')),
              'path': material.name, 'sha256': sha(material), 'bytes': material.stat().st_size,
              'media_type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
    snapshot = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    inventory = {'schema_version': 1, 'input_snapshot_sha256': snapshot, 'sources': [source]}
    atomic_json(inputs / 'inventory.json', inventory)
    for path in inputs.iterdir():
        path.chmod(0o444)
    inputs.chmod(0o555)
    before = {path.name: sha(path) for path in inputs.iterdir()}
    phases = []
    error = None
    emit({'event': 'configured', 'scope_uid': scope, 'run_id': run_id, 'parser_image': settings.worker_image,
          'parse_timeout_seconds': config.parse_timeout, 'private_root': str(root), 'module_evidence': modules,
          'source': source, 'snapshot_sha256': snapshot})
    try:
        for phase in ('healthy-before', 'timeout', 'healthy-after'):
            begin = time.monotonic()
            emit({'event': 'phase-start', 'phase': phase, 'scope_uid': scope})
            record = {'phase': phase, 'passed': False}
            try:
                parsed = sandbox.parse(inputs, inventory)
                if phase == 'timeout':
                    raise RuntimeError('timeout_fault_not_observed')
                assert parsed['input_snapshot_sha256'] == snapshot
                assert len(parsed['sources']) == 1
                assert parsed['sources'][0]['sha256'] == source['sha256']
                assert parsed['sources'][0]['text'] == 'R2 lifecycle synthetic source.'
                record.update(passed=True, parsed_source_sha256=parsed['sources'][0]['sha256'])
            except ControlError as exc:
                record['error_code'] = exc.code
                record['passed'] = phase == 'timeout' and exc.code == 'sandbox_timeout'
            record['elapsed_seconds'] = round(time.monotonic() - begin, 3)
            record['input_unchanged'] = before == {path.name: sha(path) for path in inputs.iterdir()}
            proof = transport.backend.prove_stopped(handle)
            record['scope_writers_stopped'] = proof.all_writers_stopped
            record['passed'] = record['passed'] and record['input_unchanged'] and proof.all_writers_stopped
            phases.append(record)
            emit({'event': 'phase-end', **record})
            if not record['passed']:
                raise RuntimeError('phase_not_accepted:' + phase)
    except Exception as exc:
        error = type(exc).__name__ + ':' + str(exc)
    proof = transport.backend.prove_stopped(handle)
    journal = transport.backend._read(handle)
    pod_entries = [x for x in journal['resources'] if x['resource'] == 'pods']
    policy_entries = [x for x in journal['resources'] if x['resource'] == 'networkpolicies']
    stopped = (proof.all_writers_stopped and bool(pod_entries) and
               all(x.get('deleted') is True and x.get('stop_receipt', {}).get('terminated') is True for x in pod_entries)
               and all(x.get('deleted') is True for x in policy_entries))
    safe_result = {'event': 'result', 'run_id': run_id, 'scope_uid': scope,
                  'passed': error is None and len(phases) == 3 and all(x['passed'] for x in phases) and stopped,
                  'error': error, 'phases': phases, 'exact_stop_proven': stopped,
                  'authority_journal': journal, 'private_tree_removed': False}
    if stopped and json.loads((root / 'owner.json').read_text()) == marker:
        # This exact new run directory is synthetic-only, outside all projects.
        # Never delete its shared .acceptance parent or any other run directory.
        if root.parent != parent or not RUN_PATTERN.fullmatch(root.name) or root.is_symlink():
            raise RuntimeError('unsafe_owned_cleanup_path')
        transport._discard(root)
        safe_result['private_tree_removed'] = not root.exists()
    safe_result['passed'] = safe_result['passed'] and safe_result['private_tree_removed']
    emit(safe_result)
    return 0 if safe_result['passed'] else 1


def command(args, *, timeout=30, check=True, stdin=None):
    result = subprocess.run(args, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('operator_command_failed:' + args[0] + ':' + str(result.returncode))
    return result


def kube(*args, **kwargs):
    return command(['kubectl', '-n', NAMESPACE, *args], **kwargs)


NODE_HELPER = r'''import json, os, re, signal, socket, subprocess, sys, time
from pathlib import Path

request = json.loads(sys.argv[1])
assert request['action'] in ('inspect', 'pause', 'resume')
assert socket.gethostname() == request['node_name']
assert re.fullmatch(r'[a-f0-9]{64}', request['container_id'])
assert re.fullmatch(r'[a-f0-9-]{36}', request['pod_uid'])
assert request['runtime'] in ('docker', 'containerd')
cid = request['container_id']
args = ['docker', 'inspect', cid] if request['runtime'] == 'docker' else ['crictl', 'inspect', cid]
run = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
if run.returncode:
    assert request['action'] == 'resume'
    print(json.dumps({'absent': True, 'signal_sent': False}))
    raise SystemExit(0)
data = json.loads(run.stdout)
if request['runtime'] == 'docker':
    row = data[0]
    assert row['Config']['Labels'].get('io.kubernetes.pod.uid') == request['pod_uid']
    pid, running = row['State']['Pid'], row['State']['Running']
else:
    assert data['status']['labels'].get('io.kubernetes.pod.uid') == request['pod_uid']
    pid, running = data['info'].get('pid'), data['status'].get('state') == 'CONTAINER_RUNNING'
if not running or type(pid) is not int or pid <= 1:
    print(json.dumps({'not_running': True, 'signal_sent': False}))
    raise SystemExit(0)

def identity():
    raw = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()
    command = Path('/proc/' + str(pid) + '/cmdline').read_bytes().split(b'\0')[:-1]
    assert command == [b'/opt/hermes/.venv/bin/python', b'-m', b'ppt_service.control.parsing']
    assert cid in Path('/proc/' + str(pid) + '/cgroup').read_text()
    uid = next(x for x in Path('/proc/' + str(pid) + '/status').read_text().splitlines() if x.startswith('Uid:')).split()[1:]
    assert uid == ['10000'] * 4
    return {'host_pid': pid, 'starttime_ticks': raw[19], 'process_state': raw[0],
            'cmdline': [x.decode() for x in command]}

before = identity()
result = dict(before, signal_sent=False)
if request['action'] in ('pause', 'resume'):
    if request['action'] == 'resume':
        assert before['starttime_ticks'] == request['expected_starttime']
    # A pidfd binds the signal to this process even if a PID is later recycled.
    # Require it on the actual node; never fall back to a numeric-PID signal.
    process_fd = os.pidfd_open(pid, 0)
    try:
        assert identity()['starttime_ticks'] == before['starttime_ticks']
        if request['action'] == 'pause':
            signal.pidfd_send_signal(process_fd, signal.SIGSTOP)
            until = time.monotonic() + 1
            after = identity()
            while after['process_state'] not in ('T', 't') and time.monotonic() < until:
                time.sleep(0.01)
                after = identity()
            assert after['starttime_ticks'] == before['starttime_ticks'] and after['process_state'] in ('T', 't')
            result.update(after, signal_sent=True, signal='SIGSTOP')
        elif before['process_state'] in ('T', 't'):
            signal.pidfd_send_signal(process_fd, signal.SIGCONT)
            result.update(signal_sent=True, signal='SIGCONT')
    finally:
        os.close(process_fd)
print(json.dumps(result))
'''


def node_binding(name):
    node = json.loads(command(['kubectl', 'get', 'node', name, '-o', 'json']).stdout)
    addresses = [x['address'] for x in node['status']['addresses'] if x['type'] == 'InternalIP']
    if (node['metadata']['name'] != name or len(addresses) != 1
            or ipaddress.ip_address(addresses[0]) not in ipaddress.ip_network('192.168.137.0/24')):
        raise RuntimeError('target_node_identity_not_allowed')
    return {'name': name, 'uid': node['metadata']['uid'], 'internal_ip': addresses[0]}


def invoke_node(binding, request):
    if node_binding(binding['name']) != binding:
        raise RuntimeError('target_node_identity_changed')
    data = dict(request, node_name=binding['name'])
    if binding['name'] == socket.gethostname():
        args = [sys.executable, '-c', NODE_HELPER, json.dumps(data)]
    else:
        # Fixed Git-owned helper and JSON identities only; no shell interpolation
        # of a command supplied by a user or a Pod. Never accept a new host key.
        remote = '/usr/bin/python3 -c ' + shlex.quote(NODE_HELPER) + ' ' + shlex.quote(json.dumps(data))
        args = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ConnectTimeout=5', 'root@' + binding['internal_ip'], remote]
    return json.loads(command(args, timeout=25).stdout)


def pause_exact_parser(pod):
    metadata, spec, status = pod['metadata'], pod['spec'], pod['status']
    containers = status.get('containerStatuses', [])
    if len(containers) != 1 or containers[0]['name'] != 'sandbox' or not containers[0].get('state', {}).get('running'):
        return None
    runtime, cid = containers[0]['containerID'].split('://', 1)
    if runtime not in ('docker', 'containerd') or not re.fullmatch(r'[a-f0-9]{64}', cid):
        raise RuntimeError('unexpected_container_runtime_identity')
    binding = node_binding(spec['nodeName'])
    request = {'action': 'pause', 'runtime': runtime, 'container_id': cid, 'pod_uid': metadata['uid']}
    latest = json.loads(kube('get', 'pod', metadata['name'], '-o', 'json').stdout)
    if latest['metadata']['uid'] != metadata['uid'] or latest['status']['containerStatuses'][0]['containerID'] != containers[0]['containerID']:
        raise RuntimeError('parser_identity_changed_before_fault')
    # Inspect, pidfd-open, identity recheck and signal share one node helper.
    # A prior separate SSH inspection needlessly consumed the real parse window.
    after = invoke_node(binding, request)
    if after.get('not_running'):
        raise RuntimeError('test_parser_completed_before_bound_pause')
    if after.get('signal') != 'SIGSTOP' or after.get('process_state') not in ('T', 't'):
        raise RuntimeError('parser_stop_signal_not_observed')
    fault = {'pod_name': metadata['name'], 'pod_uid': metadata['uid'], 'container_id': cid,
            'runtime': runtime, 'node': binding, 'host_pid': after['host_pid'],
            'starttime_ticks': after['starttime_ticks'], 'cmdline': after['cmdline'], 'signal': 'SIGSTOP',
            'process_state_after_signal': after['process_state'], 'fault_monotonic': time.monotonic(),
            'pod_identity_after_signal_verified': False}
    try:
        latest = json.loads(kube('get', 'pod', metadata['name'], '-o', 'json').stdout)
        fault['pod_identity_after_signal_verified'] = (
            latest['metadata']['uid'] == metadata['uid']
            and latest['status']['containerStatuses'][0]['containerID'] == containers[0]['containerID'])
    except Exception as exc:
        # Retain the exact signal identity so finally can safely resume it.
        fault['post_signal_check_error'] = type(exc).__name__
    return fault


def outer(args):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('run_on_cluster_linux_operator_as_root')
    source = args.source.resolve(strict=True)
    out = args.output.resolve(strict=False)
    if out.exists() or source == out or source in out.parents:
        raise RuntimeError('output_must_be_new_and_outside_source')
    clean = subprocess.run(['git', '-C', str(source), 'status', '--porcelain=v1', '--untracked-files=all'],
                           text=True, stdout=subprocess.PIPE, check=True).stdout
    if clean.strip():
        raise RuntimeError('source_must_be_clean_committed_git_tree')
    commit = command(['git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.strip()
    deployed = json.loads(kube('get', 'deployment', CONTROLLER, '-o', 'json').stdout)
    if deployed['spec']['replicas'] != 1 or deployed['spec']['template']['spec']['containers'][0]['image'] != CONTROLLER_IMAGE:
        raise RuntimeError('current_controller_identity_changed')
    pods = json.loads(kube('get', 'pods', '-l', 'app=' + CONTROLLER, '-o', 'json').stdout)['items']
    live = [p for p in pods if not p['metadata'].get('deletionTimestamp') and p['status'].get('phase') == 'Running']
    if len(live) != 1:
        raise RuntimeError('expected_one_live_controller')
    controller = live[0]
    if controller['spec']['containers'][0]['image'] != CONTROLLER_IMAGE:
        raise RuntimeError('running_controller_image_mismatch')
    out.mkdir(mode=0o700)
    run_id = 'ppt-r2-life-' + uuid.uuid4().hex[:20]
    scope = str(uuid.uuid5(uuid.NAMESPACE_URL, run_id))
    script = (source / 'ppt_service/tests/acceptance/current_parser_lifecycle.py').read_text()
    output(out / 'source.json', {'commit': commit, 'operator_sha256': hashlib.sha256(script.encode()).hexdigest(),
           'controller_pod_uid': controller['metadata']['uid'], 'controller_image': CONTROLLER_IMAGE,
           'parser_image': PARSER_IMAGE, 'run_id': run_id, 'scope_uid': scope})
    process = subprocess.Popen(['kubectl', '-n', NAMESPACE, 'exec', '-i', controller['metadata']['name'],
                                '-c', 'controller', '--', '/usr/local/bin/python', '-', '--inner', '--run-id', run_id],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    process.stdin.write(script)
    process.stdin.close()
    events, lines, inbox = [], [], queue.Queue()
    reader_done = threading.Event()
    def reader():
        try:
            for line in process.stdout:
                inbox.put(line)
        finally:
            reader_done.set()
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    phase, fault, failure, final = None, None, None, None
    deadline = time.monotonic() + 600
    try:
        while process.poll() is None or not reader_done.is_set() or not inbox.empty():
            try:
                line = inbox.get(timeout=0.1)
                lines.append(line)
                if sum(map(len, lines)) > 4 * 1024**2:
                    raise RuntimeError('operator_output_limit')
                try:
                    event = json.loads(line)
                except ValueError:
                    event = None
                if isinstance(event, dict) and event.get('event'):
                    events.append(event)
                    if event['event'] == 'phase-start':
                        phase = event['phase']
                    if event['event'] == 'result':
                        final = event
            except queue.Empty:
                pass
            if phase == 'timeout' and fault is None:
                probe_pods = json.loads(kube('get', 'pods', '-l', SCOPE + '=' + scope, '-o', 'json').stdout)['items']
                candidates = [p for p in probe_pods if p['metadata']['labels'].get(ROLE) == 'sandbox' and not p['metadata'].get('deletionTimestamp')]
                if len(candidates) > 1:
                    raise RuntimeError('multiple_test_parser_pods')
                if candidates:
                    fault = pause_exact_parser(candidates[0])
                    if fault:
                        output(out / 'fault.json', fault)
                        output(out / 'fault-pod.json', candidates[0])
                        if not fault['pod_identity_after_signal_verified']:
                            raise RuntimeError('parser_pod_identity_not_verified_after_fault')
            if time.monotonic() > deadline:
                raise RuntimeError('operator_deadline_exceeded')
    except Exception as exc:
        failure = type(exc).__name__ + ':' + str(exc)
    finally:
        # If observation failed, resume only the exact test PID so the unchanged
        # controller can finish its bounded parse/stop path; never signal a new PID.
        if fault is not None:
            try:
                resumed = invoke_node(fault['node'], {'action': 'resume', 'runtime': fault['runtime'],
                    'container_id': fault['container_id'], 'pod_uid': fault['pod_uid'],
                    'expected_starttime': fault['starttime_ticks']})
                output(out / 'fault-final-state.json', resumed)
            except (FileNotFoundError, RuntimeError) as exc:
                failure = failure or 'fault_identity_final_check_failed:' + type(exc).__name__
        if process.poll() is None:
            try:
                process.wait(timeout=150)
            except subprocess.TimeoutExpired:
                failure = failure or 'controller_helper_still_running_retained_for_reconciliation'
        thread.join(timeout=2)
        while not inbox.empty():
            line = inbox.get_nowait()
            lines.append(line)
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get('event') == 'result':
                    final = event
            except ValueError:
                pass
    (out / 'controller.log').write_text(''.join(lines), encoding='utf-8')
    output(out / 'events.json', events)
    if final is not None:
        output(out / 'controller-result.json', final)
    remaining_pods = json.loads(kube('get', 'pods', '-l', SCOPE + '=' + scope, '-o', 'json').stdout)['items']
    remaining_policies = json.loads(kube('get', 'networkpolicies', '-l', SCOPE + '=' + scope, '-o', 'json').stdout)['items']
    current_controller = json.loads(kube('get', 'pod', controller['metadata']['name'], '-o', 'json').stdout)
    controller_retained = current_controller['metadata']['uid'] == controller['metadata']['uid']
    after_source = command(['git', '-C', str(source), 'status', '--porcelain=v1', '--untracked-files=all']).stdout
    cleaned = not remaining_pods and not remaining_policies and bool(final and final.get('private_tree_removed'))
    status = {'passed': process.returncode == 0 and failure is None and fault is not None and
              bool(final and final.get('passed')) and cleaned and controller_retained and not after_source.strip(),
              'commit': commit, 'run_id': run_id, 'scope_uid': scope, 'operator_failure': failure,
              'controller_exit_code': process.returncode, 'actual_parser_paused': fault is not None,
              'owned_resources_removed': cleaned, 'remaining_pods': len(remaining_pods),
              'remaining_networkpolicies': len(remaining_policies), 'controller_pod_retained': controller_retained,
              'models_started': 0, 'business_database_accessed': False,
              'scope': 'installed current parser SDK; production namespace/PVC in private synthetic subtree; actual bound process pause, 30s deadline, physical stop and healthy retry'}
    output(out / 'status.json', status)
    emit(status)
    return 0 if status['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--inner', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--run-id', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inner:
        return inner(args.run_id)
    if args.source is None or args.output is None:
        parser.error('--source and --output are required')
    return outer(args)


if __name__ == '__main__':
    raise SystemExit(main())
