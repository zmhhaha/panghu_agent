"""Bounded read-only video-pool Pod watch and offline stop-evidence conversion.

Run ``watch`` on the Linux operator host BEFORE admitting video jobs. Only Pod
GET/watch is performed; no Secret API, changes, finalizers, or delayed cleanup.
Public evidence contains allowlisted metadata/status. Exact source events stay
in the local private .raw/ archive (0700 directory, 0600 files); do not publish
that full Pod archive. Ctrl-C closes kubectl and fsyncs the captured evidence.

Example: python -m video_workbench.deploy.watch_video_pods watch --kubectl
/usr/bin/kubectl --kubeconfig /etc/kubernetes/super-admin.conf --namespace
agent-workers --pool video-scene-pi-example --output /private/video-watch --max-seconds 1800

After stopping the watch with Ctrl-C: ... export-stop --watch-dir /private/video-watch --job-file
/private/cancel/cancel-stopped-job.json --output /private/cancel/watch-stop.json
The exported observation uses the event's ACTUAL local observation time, never
the later conversion time. A missing/too-early event remains missing evidence.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agent_service.contracts import canonical_bytes, digest
from .fault_evidence import PREFIX, finite, pod_stop, read_json, stopped_job, timestamp
from .verify_live import VerificationFailure, canonical_uuid, decode_object, require


MAX_FRAME = 2 * 1024 * 1024
MAX_BYTES = 128 * 1024 * 1024
MAX_EVENTS = 10000
POOL_KEY, ATTEMPT_KEY = PREFIX + 'pool', PREFIX + 'attempt'
VIDEO_PROFILES = {'video.scene.v1', 'video.storyboard.v1'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordinary(value, maximum=1024):
    require(type(value) is str and 1 <= len(value) <= maximum
            and all(ord(c) >= 32 and ord(c) != 127 for c in value), 'watch_field_invalid')
    return value


def pool_scope(namespace, pool):
    require(type(namespace) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', namespace), 'watch_namespace_invalid')
    require(type(pool) is str and re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,79}', pool)
            and re.search(r'(^|[._-])video([._-]|$)', pool), 'explicit_video_pool_required')
    return POOL_KEY + '=' + pool


class Frames:
    """kubectl emits successive pretty-printed JSON values, not JSON lines."""

    def __init__(self):
        self.utf8 = codecs.getincrementaldecoder('utf-8')('strict')
        self.decoder, self.buffer = json.JSONDecoder(), ''

    def feed(self, chunk, *, final=False):
        try:
            self.buffer += self.utf8.decode(chunk, final=final)
            require(len(self.buffer.encode('utf-8')) <= MAX_FRAME, 'watch_frame_limit')
            frames = []
            while True:
                self.buffer = self.buffer.lstrip()
                if not self.buffer:
                    break
                try:
                    _, end = self.decoder.raw_decode(self.buffer)
                except json.JSONDecodeError:
                    break
                raw = self.buffer[:end].encode('utf-8')
                frames.append((raw, decode_object(raw)))
                self.buffer = self.buffer[end:]
            if final:
                require(not self.buffer.strip(), 'watch_stream_truncated')
            return frames
        except (UnicodeError, ValueError, RecursionError):
            raise VerificationFailure('watch_json_invalid') from None


def sanitize_event(value, *, namespace, pool):
    event_type, obj = value.get('type'), value.get('object')
    require(event_type in {'ADDED', 'MODIFIED', 'DELETED', 'BOOKMARK', 'ERROR'} and type(obj) is dict,
            'watch_event_invalid')
    if event_type == 'ERROR':
        raise VerificationFailure('kubectl_watch_error')
    if event_type == 'BOOKMARK':
        return None
    require(obj.get('kind') == 'Pod', 'watch_object_is_not_pod')
    meta, spec, status = obj.get('metadata', {}), obj.get('spec', {}), obj.get('status', {})
    labels = meta.get('labels', {})
    # Check both the explicit pool and the public pinned profile. A server or
    # selector mistake cannot mix research Pods into either evidence archive.
    profiles = [e.get('value') for c in spec.get('containers', []) for e in c.get('env', [])
                if e.get('name') == 'AGENT_PROFILE_NAME']
    if (meta.get('namespace') != namespace or labels.get(POOL_KEY) != pool
            or labels.get('app') != 'agent-capsule' or len(profiles) != 1 or profiles[0] not in VIDEO_PROFILES):
        return None
    attempt_id = canonical_uuid(labels.get(ATTEMPT_KEY))
    groups = (('main', 'containers', 'containerStatuses'), ('init', 'initContainers', 'initContainerStatuses'),
              ('ephemeral', 'ephemeralContainers', 'ephemeralContainerStatuses'))
    containers, complete = [], True
    for role, spec_key, status_key in groups:
        expected = {c['name'] for c in spec.get(spec_key, [])}
        statuses = status.get(status_key, [])
        require(type(statuses) is list and len(statuses) <= 32, 'watch_container_status_invalid')
        names = [c.get('name') for c in statuses]
        require(len(set(names)) == len(names), 'watch_duplicate_container_status')
        complete = complete and set(names) == expected
        for container in statuses:
            state = container.get('state', {})
            require(type(state) is dict and len(state) <= 1, 'watch_container_status_invalid')
            require(type(container.get('restartCount')) is int and container['restartCount'] >= 0,
                    'watch_restart_count_invalid')
            safe_state = {}
            for kind, fields in state.items():
                if kind not in {'running', 'terminated'}:
                    continue
                require(type(fields) is dict, 'watch_container_status_invalid')
                keys = ('startedAt',) if kind == 'running' else ('startedAt', 'finishedAt', 'exitCode', 'signal')
                safe_state[kind] = {key: fields[key] for key in keys if key in fields}
                for key in ('startedAt', 'finishedAt'):
                    if key in safe_state[kind]:
                        ordinary(safe_state[kind][key], 100)
                        timestamp(safe_state[kind][key])
                for key in ('exitCode', 'signal'):
                    if key in safe_state[kind]:
                        require(type(safe_state[kind][key]) is int and -2**31 <= safe_state[kind][key] < 2**31,
                                'watch_container_status_invalid')
            image = container.get('imageID')
            if image:
                ordinary(image, 1024)
            containers.append(dict(name=ordinary(container['name'], 253), role=role, image_id=image,
                ready=container.get('ready') is True, restart_count=container['restartCount'], state=safe_state))
    require(len(containers) <= 64 and spec.get('containers'), 'watch_container_limit')
    require(status.get('phase') in {None, 'Pending', 'Running', 'Succeeded', 'Failed', 'Unknown'}, 'watch_phase_invalid')
    if meta.get('deletionTimestamp') is not None:
        timestamp(meta['deletionTimestamp'])
    timestamp(meta.get('creationTimestamp'))
    pod = dict(uid=canonical_uuid(meta.get('uid')), name=ordinary(meta.get('name'), 253), namespace=namespace,
        labels={'app': 'agent-capsule', POOL_KEY: pool, ATTEMPT_KEY: attempt_id},
        resource_version=ordinary(meta.get('resourceVersion'), 100), profile=profiles[0],
        created_at=ordinary(meta.get('creationTimestamp'), 100), deletion_at=meta.get('deletionTimestamp'),
        phase=status.get('phase'), containers=containers,
        pvc_claims=sorted(ordinary(v['persistentVolumeClaim']['claimName'], 253) for v in spec.get('volumes', [])
                          if 'persistentVolumeClaim' in v))
    all_terminated = bool(complete and containers and all('terminated' in c['state']
        and c['state']['terminated'].get('finishedAt') for c in containers))
    restart_zero = bool(complete and containers and all(c['restart_count'] == 0 for c in containers))
    return dict(event_type=event_type, pod=pod, all_terminated=all_terminated, restart_zero=restart_zero)


def private_file(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class Capture:
    def __init__(self, output, namespace, pool, *, clock=time.time):
        self.output, self.namespace, self.pool, self.clock = Path(output), namespace, pool, clock
        require(self.output.is_absolute(), 'absolute_watch_directory_required')
        self.selector = pool_scope(namespace, pool)
        try:
            self.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        except OSError:
            raise VerificationFailure('new_watch_directory_required') from None
        self.raw_dir = self.output / '.raw'
        self.raw_dir.mkdir(mode=0o700)
        self.seq, self.total_bytes, self.previous = 0, 0, '0' * 64
        self.report = dict(schema_version='video-pod-watch.v1', namespace=namespace, pool=pool,
            selector=self.selector, started_at=clock(), status='watching', events=0, terminated_restart_zero_events=0,
            filtered_events=0, raw_bytes=0, clock='local_unix_epoch_seconds', scope='read_only_pods_get_watch',
            raw_archive='private_0700_directory_0600_files')
        self.index = os.fdopen(os.open(self.output / 'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')
        self.flush()

    def flush(self):
        target = self.output / 'report.json'
        temporary = self.output / ('report-' + str(uuid4()) + '.tmp')
        private_file(temporary, canonical_bytes(self.report))
        temporary.replace(target)

    def add(self, raw, value, observed_at):
        safe = sanitize_event(value, namespace=self.namespace, pool=self.pool)
        if safe is None:
            self.report['filtered_events'] += 1
            self.flush()
            return
        require(finite(observed_at) and observed_at > 0, 'watch_observation_time_invalid')
        require(self.seq < MAX_EVENTS and self.total_bytes + len(raw) <= MAX_BYTES, 'watch_archive_limit')
        source_sha = sha(raw)
        archive = self.raw_dir / (source_sha + '.json')
        if not archive.exists():
            private_file(archive, raw)
            self.total_bytes += len(raw)
        else:
            require(sha(archive.read_bytes()) == source_sha, 'watch_archive_changed')
        self.seq += 1
        entry = dict(schema_version='video-pod-watch-event.v1', seq=self.seq, observed_at=observed_at,
            source_sha256=source_sha, raw_archive='.raw/' + source_sha + '.json', previous_sha256=self.previous, **safe)
        entry['sha256'] = digest(entry)
        self.index.write(canonical_bytes(entry) + b'\n')
        self.index.flush()
        os.fsync(self.index.fileno())
        self.previous = entry['sha256']
        self.report.update(events=self.seq, raw_bytes=self.total_bytes, last_event_sha256=self.previous)
        if safe['all_terminated'] and safe['restart_zero']:
            self.report['terminated_restart_zero_events'] += 1
        self.flush()

    def close(self, status, *, error=None, incomplete_frame=False):
        if self.index.closed:
            return
        self.index.flush()
        os.fsync(self.index.fileno())
        self.index.close()
        self.report.update(status=status, ended_at=self.clock(), incomplete_frame=incomplete_frame)
        if error:
            self.report['error'] = error
        self.flush()


@dataclass(frozen=True)
class WatchSettings:
    kubectl: Path
    kubeconfig: Path
    namespace: str
    pool: str
    output: Path
    max_seconds: int = 1800

    def __post_init__(self):
        require(self.kubectl.is_absolute() and self.kubeconfig.is_absolute() and self.output.is_absolute(),
                'explicit_absolute_watch_paths_required')
        pool_scope(self.namespace, self.pool)
        require(type(self.max_seconds) is int and 1 <= self.max_seconds <= 1800, 'bounded_watch_duration_required')

    def command(self):
        return [str(self.kubectl), '--kubeconfig', str(self.kubeconfig),
                '--request-timeout=' + str(self.max_seconds) + 's', '-n', self.namespace,
                'get', 'pods', '-l', pool_scope(self.namespace, self.pool), '--watch', '--output-watch-events', '-o', 'json']


def watch(settings, *, popen=subprocess.Popen, wall_clock=time.time, monotonic=time.monotonic):
    capture = Capture(settings.output, settings.namespace, settings.pool, clock=wall_clock)
    frames, messages, stop = Frames(), queue.Queue(maxsize=16), threading.Event()
    process, reader, status, error = None, None, 'completed', None
    def read_stdout():
        try:
            while not stop.is_set():
                chunk = process.stdout.read1(65536)
                if not chunk:
                    break
                received_at = wall_clock()
                while not stop.is_set():
                    try:
                        messages.put((chunk, received_at), timeout=.2)
                        break
                    except queue.Full:
                        pass
        except (OSError, ValueError):
            pass
        finally:
            while not stop.is_set():
                try:
                    messages.put(None, timeout=.2)
                    break
                except queue.Full:
                    pass
    try:
        process = popen(settings.command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
                        stdin=subprocess.DEVNULL)
        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        deadline = monotonic() + settings.max_seconds
        received = 0
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                status = 'time_limit'
                break
            try:
                message = messages.get(timeout=min(.5, remaining))
            except queue.Empty:
                continue
            if message is None:
                for raw, value in frames.feed(b'', final=True):
                    capture.add(raw, value, wall_clock())
                return_code = process.wait(timeout=3)
                status = 'completed' if return_code == 0 else 'kubectl_exited'
                break
            chunk, observed_at = message
            received += len(chunk)
            require(received <= MAX_BYTES + MAX_FRAME, 'watch_stream_byte_limit')
            for raw, value in frames.feed(chunk):
                capture.add(raw, value, observed_at)
    except KeyboardInterrupt:
        status = 'operator_interrupted'
    except (VerificationFailure, OSError, subprocess.SubprocessError) as failure:
        status, error = 'stopped', failure.code if isinstance(failure, VerificationFailure) else 'kubectl_unavailable'
    finally:
        stop.set()
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            process.stdout.close()
        if reader:
            reader.join(timeout=1)
        # Flush complete queued frames on Ctrl-C/time limit; a partial JSON
        # frame is recorded as incomplete, never promoted to a Pod event.
        try:
            while True:
                message = messages.get_nowait()
                if message:
                    chunk, observed_at = message
                    for raw, value in frames.feed(chunk):
                        capture.add(raw, value, observed_at)
        except queue.Empty:
            pass
        except VerificationFailure:
            error = error or 'watch_tail_incomplete'
            status = 'stopped'
        capture.close(status, error=error, incomplete_frame=bool(frames.buffer.strip()))
    return capture.report


def export_stop(watch_dir, job_file, output):
    watch_dir = Path(watch_dir)
    report, job = read_json(watch_dir / 'report.json'), read_json(job_file)
    require(report.get('schema_version') == 'video-pod-watch.v1', 'watch_report_invalid')
    pool_scope(report['namespace'], report['pool'])
    attempt, proof = stopped_job(job, job['business_ref'][6:], states=('completed', 'cancelled', 'timed_out'))
    index_path = watch_dir / 'events.jsonl'
    require(index_path.is_file() and index_path.stat().st_size <= 32 * 1024 * 1024, 'watch_index_invalid')
    index_raw = index_path.read_bytes()
    previous, eligible = '0' * 64, []
    rows = index_raw.splitlines()
    require(len(rows) == report['events'] and len(rows) <= MAX_EVENTS, 'watch_index_count_invalid')
    for seq, raw in enumerate(rows, 1):
        row = decode_object(raw)
        require(row.get('seq') == seq and row.get('previous_sha256') == previous
                and row.get('sha256') == digest({k: v for k, v in row.items() if k != 'sha256'})
                and finite(row.get('observed_at')), 'watch_index_integrity_invalid')
        previous = row['sha256']
        source = row.get('source_sha256')
        require(type(source) is str and re.fullmatch(r'[a-f0-9]{64}', source)
                and row.get('raw_archive') == '.raw/' + source + '.json', 'watch_source_reference_invalid')
        archive_path = watch_dir / '.raw' / (source + '.json')
        require(archive_path.is_file() and archive_path.stat().st_size <= MAX_FRAME, 'watch_raw_archive_missing')
        original = archive_path.read_bytes()
        require(sha(original) == source, 'watch_source_digest_mismatch')
        safe = sanitize_event(decode_object(original), namespace=report['namespace'], pool=report['pool'])
        require(safe is not None and all(row.get(k) == v for k, v in safe.items()), 'watch_source_sanitization_mismatch')
        if row['all_terminated'] and row['restart_zero'] and row['pod']['labels'][ATTEMPT_KEY] == attempt['attempt_id']:
            resource = 'k8s:' + row['pod']['namespace'] + ':' + row['pod']['uid']
            if resource in proof['resource_ids'] and row['observed_at'] >= proof['observed_at']:
                eligible.append(row)
    require(previous == report.get('last_event_sha256'), 'watch_index_head_invalid')
    require(eligible, 'matching_terminal_watch_event_unavailable')
    row = min(eligible, key=lambda item: (item['observed_at'], item['seq']))
    observation = dict(schema_version='video-pod-observation.v1', namespace=report['namespace'],
        selector=ATTEMPT_KEY + '=' + attempt['attempt_id'], started_at=row['observed_at'], observed_at=row['observed_at'],
        items=[row['pod']], source_watch=dict(event_seq=row['seq'], event_type=row['event_type'],
            observed_at=row['observed_at'], event_sha256=row['sha256'], source_sha256=row['source_sha256'],
            raw_archive=row['raw_archive'], index_sha256=sha(index_raw), clock='local_unix_epoch_seconds'))
    resources = pod_stop(job, observation)
    # Output is a separate derived artifact; original pending cancellation
    # report and all raw/index evidence remain intact.
    observation['verified_job'] = dict(job_id=job['job_id'], attempt=attempt, resources=resources)
    require(Path(output).is_absolute(), 'absolute_observation_path_required')
    private_file(Path(output), canonical_bytes(observation))
    return observation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    watcher = sub.add_parser('watch')
    for key in ('kubectl', 'kubeconfig', 'output'):
        watcher.add_argument('--' + key, type=Path, required=True)
    for key in ('namespace', 'pool'):
        watcher.add_argument('--' + key, required=True)
    watcher.add_argument('--max-seconds', type=int, default=1800)
    convert = sub.add_parser('export-stop')
    for key in ('watch-dir', 'job-file', 'output'):
        convert.add_argument('--' + key, type=Path, required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'watch':
            require(os.name == 'posix', 'linux_private_archive_required')
            report = watch(WatchSettings(**args))
            print('Pod watch ' + report['status'] + '; events=' + str(report['events'])
                  + '; terminal/restart0=' + str(report['terminated_restart_zero_events']) + '.')
            return 0 if report['status'] in {'completed', 'time_limit', 'operator_interrupted'} else 1
        export_stop(**args)
        print('Derived Pod stop observation saved with original watch time and source hashes.')
        return 0
    except (VerificationFailure, ValueError, TypeError, KeyError, OSError):
        print('Pod watch evidence incomplete or inconsistent; no cluster changes were made.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
