"""Server-only minimal native Pi start/stop controls in a fresh namespace.

Two cases: real native Pi held at an explicitly fake model endpoint, followed by
WorkerHost cancel or pidfd SIGKILL. No supplier call, PPT generation, production
acceptance/route, PVC, shared database, node fault or complete R5 claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

from run_current_recovery_kubernetes import command, ordinary, read, reserve, write

LABEL = 'ppt-service.panghuer.top/native-fixture-owner'
ROLE = 'ppt-service.panghuer.top/native-fixture-role'
SCRIPT = '/probe/native_pi_fixture_capsule.py'
PYTHON = '/opt/agent-service/.venv/bin/python'
CASES = ('cancel', 'native_crash')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def k(args, *, payload=None, timeout=35, check=True):
    return command(['kubectl', '--kubeconfig=/etc/kubernetes/super-admin.conf',
                   '--request-timeout=25s', *args], payload=payload, timeout=timeout,
                   check=check, limit=8 * 1024**2)


def get(kind, name, namespace=None):
    args = ['get', kind, name, '-o', 'json', '--ignore-not-found=true']
    if namespace:
        args += ['-n', namespace]
    result = k(args)
    # --ignore-not-found handles real 404 only. Auth/network/malformed output is
    # never absence, and command() rejects every nonzero response.
    return json.loads(result.stdout) if result.stdout.strip() else None


def fresh(kind, name, namespace, owner, namespace_uid, **fields):
    return dict(apiVersion='networking.k8s.io/v1' if kind == 'NetworkPolicy' else 'v1', kind=kind,
        metadata=dict(name=name, namespace=namespace, labels={LABEL: owner},
            annotations={'ppt-service.panghuer.top/namespace-uid': namespace_uid}), **fields)


def created(value):
    result = k(['create', '-f', '-', '-o', 'json'], payload=json.dumps(value).encode())
    return json.loads(result.stdout)


def owned_pod(name, namespace, owner, namespace_uid, uid=None):
    ns = get('namespace', namespace)
    require(ns is not None and ns['metadata']['uid'] == namespace_uid
            and ns['metadata']['labels'].get(LABEL) == owner, 'namespace_identity_changed')
    value = get('pod', name, namespace)
    require(value is not None and value['metadata']['labels'].get(LABEL) == owner
            and value['metadata']['annotations'].get('ppt-service.panghuer.top/namespace-uid') == namespace_uid
            and (uid is None or value['metadata']['uid'] == uid), 'pod_identity_changed')
    return value


def pod_identity(value, image, node):
    require(len(value['spec']['containers']) == 1 and value['spec']['containers'][0]['image'] == image
            and not value['spec'].get('initContainers') and not value['spec'].get('ephemeralContainers'),
            'original_pinned_single_container_spec_required')
    require(value['spec'].get('nodeName') == node, 'pod_scheduled_on_unreserved_node')
    statuses = value.get('status', {}).get('containerStatuses', [])
    require(len(statuses) == 1 and statuses[0]['name'] == 'capsule'
            and statuses[0].get('containerID') and statuses[0].get('imageID')
            and statuses[0].get('restartCount') == 0, 'single_original_container_required')
    status = statuses[0]
    expected_digest = image.rsplit('@', 1)[1]
    require(status['imageID'] == expected_digest or status['imageID'].endswith('@' + expected_digest),
            'platform_image_id_differs_from_reviewed_pin')
    return {'pod_uid': value['metadata']['uid'], 'container_id': status['containerID'],
            'image_id': status['imageID'], 'node': value['spec']['nodeName']}


def logs(name, namespace):
    return k(['logs', '-n', namespace, name, '-c', 'capsule', '--timestamps=false']).stdout


def records(raw):
    result = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if type(value) is dict and 'kind' in value:
            result.append(value)
    return result


def environment(reservation):
    node = get('node', reservation['node'])
    cluster = get('namespace', 'kube-system')
    require(node is not None and cluster is not None
            and node['metadata']['uid'] == reservation.get('node_uid')
            and cluster['metadata']['uid'] == reservation.get('cluster_uid')
            and node.get('status', {}).get('nodeInfo', {}).get('bootID') == reservation.get('node_boot_id')
            == Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'fresh_reservation_node_or_cluster_identity_mismatch')


def terminated(value):
    statuses = value.get('status', {}).get('containerStatuses', [])
    require(len(statuses) == 1, 'original_container_status_required')
    state = statuses[0].get('state', {}).get('terminated')
    if state is None:
        return None
    require(type(state.get('exitCode')) is int and state.get('finishedAt')
            and state.get('containerID') == statuses[0]['containerID']
            and state.get('reason') not in {'NodeLost', 'ContainerStatusUnknown'}
            and value.get('status', {}).get('reason') not in {'NodeLost', 'ContainerStatusUnknown'},
            'original_container_termination_not_positive')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--uid-reservation', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0, 'server_root_operator_required')
    os.umask(0o077)
    source, output = args.source.absolute(), args.output.absolute()
    require(Path(__file__).resolve() == source / 'ppt_service/tests/e2e/run_native_pi_fixture_kubernetes.py',
            'own_git_checkout_required')
    require(output.parent == Path('/root') and not output.exists()
            and re.fullmatch('ppt-native-stop-[a-z0-9-]+', output.name), 'new_private_output_required')
    require(re.fullmatch(r'arm-cluster-master:5000/agent-service-pi:[a-z0-9.-]+@sha256:[0-9a-f]{64}', args.image),
            'fixed_registry_digest_image_required')
    commit = command(['git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', commit), 'git_commit_required')
    hashes = {}
    for name in ('run_native_pi_fixture_kubernetes.py', 'native_pi_fixture_capsule.py',
                 'run_current_recovery_kubernetes.py'):
        path = ordinary(source / 'ppt_service/tests/e2e' / name)
        raw = read(path)
        require(raw == command(['git', '-C', str(source), 'show',
            commit + ':ppt_service/tests/e2e/' + name]).stdout, 'runner_source_not_committed')
        hashes[name] = hashlib.sha256(raw).hexdigest()
    output.mkdir(mode=0o700)
    reservation = reserve(args.uid_reservation, output)
    owner = 'native-' + reservation['run_id']
    namespace = 'ppt-' + owner
    environment(reservation)
    require(get('namespace', namespace) is None, 'fresh_namespace_required')
    namespace_uid = None
    pods, result_cases, stop_rows = [], [], []
    receipt = dict(schema='ppt-native-stop-control.v1', passed=False, source_commit=commit,
        source_sha256=hashes, worker_image=args.image, namespace=namespace, namespace_uid=namespace_uid,
        actual_supplier_requests=0, existing_supplier_credentials_read=False,
        production_routes_changed=False, production_acceptance_created=False,
        production_resources_accessed=False, real_pg_dispatcher_recovery_verified=False,
        full_ppt_generation_verified=False, oom_network_disk_quality_failures_verified=False,
        boundary='actual image-owned native Pi/WorkerHost with fixture model held before response; two stop controls only',
        started_at=time.time())
    try:
        write(output / 'namespace-create-intent.json',
            {'name': namespace, 'owner': owner, 'initial_get_absent': True})
        try:
            ns = created(dict(apiVersion='v1', kind='Namespace',
                metadata=dict(name=namespace, labels={LABEL: owner})))
        except BaseException:
            # Recover only the unique reserved name and random owner following
            # the recorded initially absent GET. No unrelated namespace qualifies.
            ns = get('namespace', namespace)
            if ns is not None and ns['metadata'].get('labels', {}).get(LABEL) == owner:
                namespace_uid = ns['metadata']['uid']
                receipt['namespace_create_response_lost'] = True
            raise
        namespace_uid = ns['metadata']['uid']
        receipt['namespace_uid'] = namespace_uid
        created(fresh('NetworkPolicy', 'deny-all', namespace, owner, namespace_uid,
            spec=dict(podSelector={}, policyTypes=['Ingress', 'Egress'])))
        created(fresh('NetworkPolicy', 'allow-only-fixture-8080', namespace, owner, namespace_uid,
            spec=dict(podSelector={'matchLabels': {ROLE: 'worker'}}, policyTypes=['Egress'],
                egress=[{'to': [{'podSelector': {'matchLabels': {ROLE: 'fixture'}}}],
                         'ports': [{'protocol': 'TCP', 'port': 8080}]}])))
        created(fresh('NetworkPolicy', 'fixture-ingress', namespace, owner, namespace_uid,
            spec=dict(podSelector={'matchLabels': {ROLE: 'fixture'}}, policyTypes=['Ingress'],
                ingress=[{'from': [{'podSelector': {'matchLabels': {ROLE: 'worker'}}}],
                          'ports': [{'protocol': 'TCP', 'port': 8080}]}])))
        script = read(source / 'ppt_service/tests/e2e/native_pi_fixture_capsule.py').decode()
        created(fresh('ConfigMap', 'harness', namespace, owner, namespace_uid,
            immutable=True, data={'native_pi_fixture_capsule.py': script}))
        created(fresh('Secret', 'fixture-only', namespace, owner, namespace_uid,
            immutable=True, type='Opaque', stringData={'token': secrets.token_hex(48)}))

        def pod(name, role, arguments):
            worker = role == 'worker'
            security = dict(runAsUser=0 if worker else 10000, runAsGroup=0 if worker else 10000,
                allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities={'drop': ['ALL']})
            if worker:
                security['capabilities']['add'] = ['CHOWN', 'DAC_OVERRIDE', 'KILL', 'SETUID', 'SETGID', 'SYS_ADMIN']
                security['appArmorProfile'] = {'type': 'Localhost', 'localhostProfile': 'agent-capsule-inputs'}
            value = fresh('Pod', name, namespace, owner, namespace_uid, spec=dict(
                automountServiceAccountToken=False, enableServiceLinks=False, restartPolicy='Never',
                activeDeadlineSeconds=210 if worker else 600, hostPID=False, hostIPC=False, hostNetwork=False,
                shareProcessNamespace=False, terminationGracePeriodSeconds=15,
                nodeSelector={'kubernetes.io/hostname': reservation['node']},
                securityContext={'seccompProfile': {'type': 'RuntimeDefault'}},
                containers=[dict(name='capsule', image=args.image, imagePullPolicy='Never',
                    command=[PYTHON, SCRIPT], args=arguments, securityContext=security,
                    resources={'requests': {'cpu': '25m', 'memory': '64Mi'},
                        'limits': {'cpu': '1', 'memory': '1536Mi' if worker else '192Mi', 'ephemeral-storage': '2Gi'}},
                    env=[{'name': 'PYTHONUNBUFFERED', 'value': '1'},
                         {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'},
                         {'name': 'PYTHONPATH', 'value': '/opt/agent-service'},
                         {'name': 'AGENT_POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}}],
                    volumeMounts=[{'name': 'harness', 'mountPath': '/probe', 'readOnly': True},
                        {'name': 'fixture-token', 'mountPath': '/run/agent-probe-bootstrap', 'readOnly': True},
                        {'name': 'control', 'mountPath': '/run/agent-control'},
                        {'name': 'work', 'mountPath': '/work'}, {'name': 'tmp', 'mountPath': '/tmp'}])],
                volumes=[{'name': 'harness', 'configMap': {'name': 'harness', 'defaultMode': 0o444}},
                    {'name': 'fixture-token', 'secret': {'secretName': 'fixture-only',
                        'defaultMode': 0o400 if worker else 0o444}},
                    {'name': 'control', 'emptyDir': {'sizeLimit': '128Mi'}},
                    {'name': 'work', 'emptyDir': {'sizeLimit': '1Gi'}},
                    {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '96Mi'}}]))
            value['metadata']['labels'][ROLE] = role
            # Persist create intent before POST. A lost response is recovered by
            # exact reserved name/random owner/namespace UID or quarantined.
            write(output / (name + '-create-intent.json'), dict(name=name, role=role, owner=owner,
                namespace_uid=namespace_uid, pod_spec_sha256=hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()))
            index = len(pods)
            pods.append((name, None))
            row = created(value)
            pods[index] = (name, row['metadata']['uid'])
            return row['metadata']['uid']

        fixture_uid = pod('fixture', 'fixture', ['fixture'])
        end = time.monotonic() + 90
        while True:
            value = owned_pod('fixture', namespace, owner, namespace_uid, fixture_uid)
            if value.get('status', {}).get('phase') == 'Running':
                fixture_identity = pod_identity(value, args.image, reservation['node'])
                if any(row['kind'] == 'fixture_ready' for row in records(logs('fixture', namespace))):
                    break
            require(time.monotonic() < end, 'fixture_pod_not_ready')
            time.sleep(1)
        origin = 'http://' + value['status']['podIP'] + ':8080'
        receipt['fixture_identity'] = fixture_identity
        for index, case in enumerate(CASES):
            name, uid = 'worker-' + case.replace('_', '-'), reservation['uid_start'] + index * 4
            worker_uid = pod(name, 'worker', ['worker', '--case', case, '--uid', str(uid),
                '--image', args.image, '--origin', origin])
            end = time.monotonic() + 145
            before_count = index
            identity = None
            while True:
                value = owned_pod(name, namespace, owner, namespace_uid, worker_uid)
                phase = value.get('status', {}).get('phase')
                if phase == 'Running':
                    identity = pod_identity(value, args.image, reservation['node'])
                    native = [row for row in records(logs('fixture', namespace))
                              if row['kind'] == 'fixture_request' and row['category'] == 'native']
                    admitted = [row for row in records(logs(name, namespace)) if row['kind'] == 'native_admitted']
                    if len(native) == before_count + 1 and len(admitted) == 1:
                        break
                require(phase not in {'Failed', 'Succeeded'} and time.monotonic() < end,
                        'native_never_reached_fixture_hold')
                time.sleep(1)
            # Re-read original Pod/container identity immediately before a trigger.
            environment(reservation)
            require(pod_identity(owned_pod(name, namespace, owner, namespace_uid, worker_uid), args.image, reservation['node']) == identity,
                    'worker_container_identity_changed_before_trigger')
            require(admitted[0]['pod_uid'] == worker_uid and admitted[0]['agent_uid'] == uid,
                    'native_admission_scope_mismatch')
            trigger = k(['exec', '-n', namespace, name, '-c', 'capsule', '--', PYTHON, SCRIPT,
                'trigger', '--case', case, '--uid', str(uid)], timeout=25)
            (output / (case + '-trigger.private.jsonl')).write_bytes(trigger.stdout)
            require(pod_identity(owned_pod(name, namespace, owner, namespace_uid, worker_uid), args.image, reservation['node']) == identity,
                    'worker_container_identity_changed_after_trigger')
            while True:
                value = owned_pod(name, namespace, owner, namespace_uid, worker_uid)
                if value.get('status', {}).get('phase') in {'Failed', 'Succeeded'}:
                    break
                require(time.monotonic() < end, 'native_stop_did_not_complete')
                time.sleep(1)
            raw = logs(name, namespace)
            (output / (case + '-worker.private.jsonl')).write_bytes(raw)
            stopped = [row for row in records(raw) if row['kind'] == 'native_stopped']
            require(value['status']['phase'] == 'Succeeded' and len(stopped) == 1
                    and stopped[0]['stopped'] is True and stopped[0]['stop_signature_verified'] is True,
                    'real_native_stop_control_failed')
            require(pod_identity(value, args.image, reservation['node']) == identity and terminated(value) is not None,
                    'original_container_not_retained_or_terminated')
            native = [row for row in records(logs('fixture', namespace))
                      if row['kind'] == 'fixture_request' and row['category'] == 'native']
            require(len(native) == before_count + 1, 'duplicate_native_request_or_launch')
            result_cases.append(dict(case=case, identity=identity, result=stopped[0],
                native_model_posts=1, duplicate_start_no_second_request=True))
        (output / 'fixture.private.jsonl').write_bytes(logs('fixture', namespace))
        receipt.update(cases=result_cases, tests_succeeded=True)
    except BaseException as error:
        receipt.update(error_type=type(error).__name__, diagnostic=str(error)[:160]
            if type(error) is RuntimeError else 'inspect_private_owned_evidence')
        raise
    finally:
        errors = []
        for name, uid in reversed(pods):
            try:
                if uid is None:
                    row = get('pod', name, namespace)
                    if row is None:
                        stop_rows.append(dict(name=name, creation_intent_only_api_absence=True))
                        continue
                    value = owned_pod(name, namespace, owner, namespace_uid)
                    uid = value['metadata']['uid']
                    write(output / (name + '-lost-response-recovery.json'),
                        dict(pod_uid=uid, namespace_uid=namespace_uid, owner=owner))
                value = owned_pod(name, namespace, owner, namespace_uid, uid)
                identity = pod_identity(value, args.image, reservation['node'])
                if terminated(value) is None:
                    k(['exec', '-n', namespace, name, '-c', 'capsule', '--', PYTHON, SCRIPT,
                       'stop-fixture' if name == 'fixture' else 'stop-worker'], timeout=25)
                    end = time.monotonic() + 45
                    while True:
                        value = owned_pod(name, namespace, owner, namespace_uid, uid)
                        require(pod_identity(value, args.image, reservation['node']) == identity,
                                'original_container_changed_before_termination')
                        if terminated(value) is not None:
                            break
                        require(time.monotonic() < end, 'original_container_stop_not_confirmed')
                        time.sleep(1)
                raw = logs(name, namespace)
                if name != 'fixture':
                    proofs = [row for row in records(raw) if row['kind'] == 'native_stopped'
                              and row.get('stop_signature_verified') is True and row.get('stopped') is True]
                    require(len(proofs) == 1, 'signed_native_stop_missing_namespace_retained')
                else:
                    require(any(row['kind'] == 'fixture_stopped' for row in records(raw)),
                            'fixture_graceful_stop_missing_namespace_retained')
                path = output / (name + '-cleanup.private.jsonl')
                path.write_bytes(raw)
                # DELETE is issued only to this frozen UID via API preconditions.
                payload = json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions',
                    'gracePeriodSeconds': 15, 'preconditions': {'uid': uid}}).encode()
                k(['delete', '--raw', '/api/v1/namespaces/' + namespace + '/pods/' + name, '-f', '-'],
                  payload=payload)
                end = time.monotonic() + 55
                while True:
                    row = get('pod', name, namespace)
                    if row is None:
                        break
                    require(row['metadata']['uid'] == uid, 'pod_replaced_during_cleanup')
                    require(time.monotonic() < end, 'pod_cleanup_timeout')
                    time.sleep(1)
                stop_rows.append(dict(name=name, identity=identity, original_termination=terminated(value),
                    api_uid_deletion_confirmed=True,
                    kernel_stop_from_absence_claimed=False))
            except Exception as error:
                errors.append(name + ':' + type(error).__name__ + ':' + str(error)[:100])
        try:
            ns = get('namespace', namespace)
            require(namespace_uid is not None and ns is not None and ns['metadata']['uid'] == namespace_uid
                    and ns['metadata']['labels'].get(LABEL) == owner and not errors,
                    'namespace_cleanup_not_authorized')
            require(not json.loads(k(['get', 'pods', '-n', namespace, '-o', 'json']).stdout)['items'],
                    'pod_inventory_not_empty')
            k(['delete', '--raw', '/api/v1/namespaces/' + namespace, '-f', '-'],
                payload=json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions',
                    'preconditions': {'uid': namespace_uid}}).encode())
            end = time.monotonic() + 55
            while True:
                ns = get('namespace', namespace)
                if ns is None:
                    break
                require(ns['metadata']['uid'] == namespace_uid and ns['metadata']['labels'].get(LABEL) == owner,
                        'namespace_replaced_during_deletion_wait')
                require(time.monotonic() < end, 'namespace_cleanup_timeout')
                time.sleep(1)
        except Exception as error:
            errors.append('namespace:' + type(error).__name__ + ':' + str(error)[:100])
        receipt.update(namespace_uid=namespace_uid, cases=result_cases, stops=stop_rows, cleanup_errors=errors,
            owned_resources_removed=not errors, passed=receipt.get('tests_succeeded') is True and not errors,
            finished_at=time.time())
        write(output / 'receipt.json', receipt)
        print(json.dumps({'receipt': str(output / 'receipt.json'), 'passed': receipt['passed'],
                          'cleanup_errors': errors}), flush=True)
    require(receipt['passed'], 'native_fixture_controls_failed')


if __name__ == '__main__':
    main()
