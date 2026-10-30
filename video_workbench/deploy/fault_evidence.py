"""Read-only Pod observations and strict, offline recovery evidence checks.

Only the observer executes kubectl, using a fixed GET argument vector. It saves
no Pod spec, credentials, environment, annotations, or container log messages.
External observations are evidence, never fabricated or inferred from absence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .verify_live import VerificationFailure, canonical_uuid, decode_object, require, validate_completed_events


SHA = re.compile(r'[a-f0-9]{64}')
PREFIX = 'agent-service.panghuer.top/'
WORKBENCH_LABEL = 'app.kubernetes.io/name'
WORKBENCH_SELECTOR = WORKBENCH_LABEL + '=video-workbench'
JOB_KEYS = ('job_id', 'state', 'business_ref', 'pins', 'created_at', 'deadline_at', 'event_cursor',
            'attempt', 'publication_authorized', 'stop_proven', 'stop_proof', 'not_started',
            'artifact_manifest_sha256', 'error_code', 'usage')


def read_json(path, limit=8 * 1024 * 1024):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= limit, 'evidence_file_invalid')
    return decode_object(path.read_bytes())


def write_new(path, value):
    path = Path(path)
    require(path.is_absolute(), 'absolute_evidence_path_required')
    with path.open('xb') as stream:
        stream.write(canonical_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())


def public_job(job):
    return {key: job.get(key) for key in JOB_KEYS}


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def stopped_job(job, operation_id, *, states=('cancelled',)):
    canonical_uuid(job.get('job_id'))
    require(job.get('business_ref') == 'video:' + canonical_uuid(operation_id)
            and job.get('state') in states and job.get('publication_authorized') is False
            and job.get('not_started') is not True, 'stopped_job_identity_invalid')
    pins, attempt, proof = job.get('pins'), job.get('attempt'), job.get('stop_proof')
    require(type(pins) is dict and pins.get('profile') == 'video.scene.v1'
            and pins.get('profile_revision') == '1' and type(attempt) is dict, 'stopped_job_pins_invalid')
    canonical_uuid(attempt.get('attempt_id'))
    require(type(attempt.get('fence')) is int and attempt['fence'] >= 1 and type(proof) is dict
            and job.get('stop_proven') is True and proof.get('stop_proven') is True
            and proof.get('attempt_id') == attempt['attempt_id']
            and type(proof.get('fence')) is int and proof['fence'] == attempt['fence']
            and all(type(proof.get(k)) is str and SHA.fullmatch(proof[k]) for k in ('request_sha256', 'result_sha256'))
            and type(proof.get('resource_ids')) is list and 1 <= len(proof['resource_ids']) <= 64
            and all(type(v) is str and 1 <= len(v) <= 200 and all(32 <= ord(c) < 127 for c in v)
                    for v in proof['resource_ids']) and len(set(proof['resource_ids'])) == len(proof['resource_ids'])
            and finite(proof.get('observed_at')) and proof['observed_at'] > 0, 'stop_proof_invalid')
    return attempt, proof


def observe_pods(*, kubectl, kubeconfig, namespace, selector, output, executor=subprocess.run, clock=time.time):
    require(Path(kubectl).is_absolute() and Path(kubeconfig).is_absolute(), 'explicit_kubectl_paths_required')
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,62}', namespace), 'namespace_invalid')
    require(selector == WORKBENCH_SELECTOR or re.fullmatch(
        PREFIX + r'attempt=[0-9a-f-]{36}', selector), 'narrow_pod_selector_required')
    started = clock()
    result = executor([str(kubectl), '--kubeconfig', str(kubeconfig), '-n', namespace,
                       'get', 'pods', '-l', selector, '-o', 'json'],
                      check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
    require(len(result.stdout) <= 8 * 1024 * 1024, 'pod_response_too_large')
    response = decode_object(result.stdout)
    require(response.get('kind') in {'List', 'PodList'} and type(response.get('items')) is list
            and len(response['items']) <= 32, 'pod_response_invalid')
    pods = []
    for value in response['items']:
        meta, spec, status = value['metadata'], value['spec'], value.get('status', {})
        require(meta['namespace'] == namespace, 'pod_namespace_mismatch')
        labels = {key: meta.get('labels', {}).get(key) for key in ('app', WORKBENCH_LABEL, PREFIX + 'attempt', PREFIX + 'pool')
                  if key in meta.get('labels', {})}
        key, selected = selector.split('=', 1)
        require(labels.get(key) == selected, 'pod_selector_mismatch')
        containers = []
        for c in status.get('containerStatuses', []):
            state = c.get('state', {})
            # Drop reasons/messages/args even when a runtime includes them.
            safe_state = {kind: {k: fields.get(k) for k in
                (('startedAt',) if kind == 'running' else ('startedAt', 'finishedAt', 'exitCode', 'signal'))
                if k in fields} for kind, fields in state.items() if kind in {'running', 'terminated'}}
            containers.append(dict(name=c['name'], image_id=c.get('imageID'), ready=c.get('ready') is True,
                                   restart_count=c.get('restartCount'), state=safe_state))
        pods.append(dict(uid=canonical_uuid(meta['uid']), name=meta['name'], namespace=namespace,
            labels=labels, created_at=meta['creationTimestamp'], deletion_at=meta.get('deletionTimestamp'),
            phase=status.get('phase'), containers=containers,
            pvc_claims=sorted(v['persistentVolumeClaim']['claimName'] for v in spec.get('volumes', [])
                              if 'persistentVolumeClaim' in v)))
    observation = dict(schema_version='video-pod-observation.v1', namespace=namespace,
                       selector=selector, started_at=started, observed_at=clock(), items=pods)
    write_new(output, observation)
    return observation


def timestamp(value):
    require(type(value) is str, 'pod_timestamp_invalid')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.tzinfo is not None, 'pod_timestamp_invalid')
        return parsed.timestamp()
    except ValueError:
        raise VerificationFailure('pod_timestamp_invalid') from None


def validate_restart(before, after, *, dropped_at=None):
    for value in (before, after):
        require(value.get('schema_version') == 'video-pod-observation.v1'
                and value.get('selector') == WORKBENCH_SELECTOR
                and finite(value.get('started_at')) and finite(value.get('observed_at'))
                and value['observed_at'] >= value['started_at'] and len(value.get('items', [])) == 1,
                'workbench_observation_invalid')
    old, new = before['items'][0], after['items'][0]
    require(before['namespace'] == after['namespace'] == old['namespace'] == new['namespace']
            and canonical_uuid(old['uid']) != canonical_uuid(new['uid'])
            and new['phase'] == 'Running' and new.get('deletion_at') is None
            and old['pvc_claims'] and old['pvc_claims'] == new['pvc_claims']
            and after['started_at'] > before['observed_at'], 'workbench_restart_identity_invalid')
    for pod in (old, new):
        require(pod['labels'].get(WORKBENCH_LABEL) == 'video-workbench' and pod['containers']
                and all(c.get('ready') is True and 'running' in c.get('state', {})
                        and c.get('image_id') for c in pod['containers']), 'workbench_not_ready')
    require({c['name']: c['image_id'] for c in old['containers']} ==
            {c['name']: c['image_id'] for c in new['containers']}, 'restart_changed_image')
    if dropped_at is not None:
        require(before['observed_at'] <= dropped_at < timestamp(new['created_at']) <= after['observed_at'],
                'restart_not_after_response_loss')
    return dict(old_pod_uid=old['uid'], new_pod_uid=new['uid'], pvc_claims=new['pvc_claims'])


def pod_stop(job, observation):
    attempt, proof = stopped_job(job, job['business_ref'][6:], states=('cancelled', 'timed_out', 'completed'))
    require(observation.get('schema_version') == 'video-pod-observation.v1'
            and observation.get('selector') == PREFIX + 'attempt=' + attempt['attempt_id'], 'worker_observation_invalid')
    expected = {r for r in proof['resource_ids'] if r.startswith('k8s:')}
    require(expected, 'kubernetes_stop_resource_missing')
    linked = []
    for resource in expected:
        parts = resource.split(':')
        require(len(parts) == 3, 'kubernetes_stop_resource_invalid')
        _, namespace, uid = parts
        matches = [p for p in observation.get('items', []) if p['namespace'] == namespace and p['uid'] == uid]
        require(len(matches) == 1, 'worker_pod_stop_unobserved')
        pod = matches[0]
        require(pod['labels'].get(PREFIX + 'attempt') == attempt['attempt_id'] and pod['containers']
                and all('terminated' in c.get('state', {}) and c['state']['terminated'].get('finishedAt')
                        for c in pod['containers']), 'worker_pod_not_stopped')
        require(finite(observation.get('observed_at')) and observation['observed_at'] >= proof['observed_at'],
                'worker_observation_before_stop_proof')
        linked.append(dict(resource_id=resource, pod_uid=uid, pod_name=pod['name'], namespace=namespace))
    return linked


def trace_events(trace):
    require(trace.get('schema_version') == 'video-fault-relay.v1' and type(trace.get('events')) is list
            and 1 <= len(trace['events']) <= 10000, 'relay_trace_invalid')
    previous, observed = '0' * 64, 0
    for index, event in enumerate(trace['events'], 1):
        require(type(event) is dict and event.get('seq') == index and event.get('previous_sha256') == previous
                and event.get('sha256') == digest({k: v for k, v in event.items() if k != 'sha256'})
                and finite(event.get('observed_at')) and event['observed_at'] >= observed, 'relay_trace_invalid')
        previous, observed = event['sha256'], event['observed_at']
    return trace['events']


def recovery_evidence(run_dir, proxy_dir, before, after):
    report = read_json(Path(run_dir) / 'report.json')
    intents = [v for v in report['intents'] if v['step'] == 'svg']
    require(len(intents) == 1 and intents[0]['state'] == 'validated_completed', 'svg_not_validated')
    intent = intents[0]
    operation_id, job_id = canonical_uuid(intent['operation_id']), canonical_uuid(intent['job_id'])
    job = read_json(Path(run_dir) / 'svg-job.json')
    attempt, proof = stopped_job(job, operation_id, states=('completed',))
    require(job['job_id'] == job_id, 'recovery_attempt_identity_invalid')
    event_evidence = read_json(Path(run_dir) / 'svg-events.json')
    job_events = validate_completed_events(event_evidence, job)
    trace = read_json(Path(proxy_dir) / 'trace.json')
    require(trace['run_id'] == report['run_id'], 'recovery_run_mismatch')
    events = trace_events(trace)
    relevant = [v for v in events if v.get('operation_id') == operation_id]
    def one(name):
        found = [v for v in relevant if v['event'] == name]
        require(len(found) == 1, 'recovery_trace_missing_or_repeated_' + name)
        return found[0]
    started, accepted, dropped = one('jobs_post_started'), one('jobs_post_accepted'), one('response_dropped')
    released, lookup = one('lookup_released'), one('lookup_forwarded')
    require(started.get('targeted') is True and accepted.get('targeted') is True
            and started['input_sha256'] == accepted['input_sha256'] == intent['input_sha256']
            and accepted['job_id'] == dropped['job_id'] == released['job_id'] == lookup['job_id'] == job_id
            and started['seq'] < accepted['seq'] < dropped['seq'] < released['seq'] < lookup['seq']
            and released['before_sha256'] == digest(before) and released['after_sha256'] == digest(after)
            and not any(v['event'] in {'duplicate_post_rejected', 'upstream_submission_unknown'} for v in relevant),
            'recovery_submission_evidence_invalid')
    blocked = [v for v in relevant if v['event'] == 'lookup_blocked']
    require(blocked and all(dropped['seq'] < v['seq'] < released['seq'] for v in blocked), 'restart_gate_not_observed')
    restarted = validate_restart(before, after, dropped_at=dropped['observed_at'])
    scene = next((s for s in read_json(Path(run_dir) / 'project.json')['scenes']
                  if type(s.get('operation')) is dict and s['operation'].get('id') == operation_id), None)
    require(scene is not None and len(scene['versions']) == 1 and scene['head_version_id'] is None,
            'recovery_candidate_not_unique')
    result = read_json(Path(run_dir) / 'svg-result.json')
    artifact = (Path(run_dir) / 'svg-artifact.json').read_bytes()
    source = (Path(run_dir) / 'svg.svg').read_bytes()
    manifest = read_json(Path(run_dir) / 'svg-manifest.json')
    require(digest(read_json(Path(run_dir) / 'svg-input.json')) == intent['input_sha256']
            and result['schema_version'] == '1' and result['input_sha256'] == intent['input_sha256'] and result['kind'] == 'scene'
            and result['output']['format'] == 'svg' and source == result['output']['source'].encode()
            and scene['versions'][0]['source_sha256'] == hashlib_sha(source)
            and manifest['sha256'] == job['artifact_manifest_sha256']
            and manifest['sha256'] == digest({k: v for k, v in manifest.items() if k != 'sha256'})
            and manifest['job_id'] == job_id and manifest['attempt_id'] == attempt['attempt_id']
            and manifest['schema_version'] == 'agent-artifacts.v1' and manifest['publication_authorized'] is False
            and manifest['total_bytes'] == len(artifact)
            and len(manifest['files']) == 1 and manifest['files'][0]['name'] == 'video-result.json'
            and decode_object(artifact) == result and manifest['files'][0]['sha256'] == hashlib_sha(artifact)
            and manifest['files'][0]['size'] == len(artifact), 'recovery_artifact_evidence_invalid')
    return dict(schema_version='video-recovery-evidence.v1', status='passed', run_id=report['run_id'],
        operation_id=operation_id, job_id=job_id, attempt=attempt, input_sha256=intent['input_sha256'],
        source_sha256=hashlib_sha(source), jobs_posts=1, candidate_versions=1, restart=restarted,
        job_starting_events=sum(v['type'] == 'job.starting' for v in job_events),
        events_sha256=digest(event_evidence), event_cursor=job['event_cursor'],
        stop_proof=proof, scope='submission_response_loss_and_workbench_pod_restart',
        worker_pod_observation='not_checked_here', cost='unknown')


def hashlib_sha(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    observe = sub.add_parser('observe-pods')
    for key in ('kubectl', 'kubeconfig', 'namespace', 'selector', 'output'):
        observe.add_argument('--' + key, required=True, type=Path if key in {'kubectl', 'kubeconfig', 'output'} else str)
    release = sub.add_parser('release-relay')
    arm = sub.add_parser('arm-relay')
    arm.add_argument('--proxy-dir', type=Path, required=True)
    arm.add_argument('--checkpoint', type=Path, required=True)
    check = sub.add_parser('check-recovery')
    for command in (release, check):
        for key in ('proxy-dir', 'before', 'after'):
            command.add_argument('--' + key, type=Path, required=True)
    check.add_argument('--run-dir', type=Path, required=True)
    check.add_argument('--output', type=Path, required=True)
    gate = sub.add_parser('continue-workflow')
    gate.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'observe-pods':
            observe_pods(**{k: v for k, v in vars(args).items() if k != 'command'})
        elif args.command == 'continue-workflow':
            value = read_json(args.checkpoint)
            require(value.get('schema_version') == 'video-operator-checkpoint.v1'
                    and args.checkpoint.name.endswith('-checkpoint.json'), 'operator_checkpoint_invalid')
            write_new(args.checkpoint.with_name(args.checkpoint.name.replace('-checkpoint.json', '-continue.json')),
                      dict(run_id=value['run_id'], checkpoint_sha256=digest(value)))
        elif args.command == 'arm-relay':
            from agent_service.profiles.video import validate_video_input
            value, trace = read_json(args.checkpoint), read_json(args.proxy_dir / 'trace.json')
            trace_events(trace)
            require(value.get('schema_version') == 'video-operator-checkpoint.v1'
                    and value.get('checkpoint') == 'before-svg' and value.get('run_id') == trace.get('run_id')
                    and value['document'].get('format') == 'svg', 'relay_checkpoint_invalid')
            validate_video_input(value['document'], 'scene')
            write_new(args.proxy_dir / 'arm.json', dict(run_id=value['run_id'], input_sha256=digest(value['document'])))
        else:
            before, after = read_json(args.before), read_json(args.after)
            if args.command == 'release-relay':
                trace = read_json(args.proxy_dir / 'trace.json')
                dropped = [v for v in trace_events(trace) if v['event'] == 'response_dropped']
                require(len(dropped) == 1, 'one_dropped_response_required')
                validate_restart(before, after, dropped_at=dropped[0]['observed_at'])
                write_new(args.proxy_dir / 'release.json', dict(job_id=dropped[0]['job_id'],
                    operation_id=dropped[0]['operation_id'], before_sha256=digest(before), after_sha256=digest(after)))
            else:
                write_new(args.output, recovery_evidence(args.run_dir, args.proxy_dir, before, after))
        print('Evidence operation completed; this does not submit a model task.')
        return 0
    except (VerificationFailure, ServiceError, ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
        print('Evidence operation stopped; required observations are missing or inconsistent.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
