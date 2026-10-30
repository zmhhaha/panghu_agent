"""Parsing, scope, source integrity and genuine stop evidence; no cluster I/O."""
import io
import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from agent_service.contracts import canonical_bytes
from video_workbench.deploy.fault_evidence import PREFIX, pod_stop, read_json
from video_workbench.deploy.verify_live import VerificationFailure
from video_workbench.deploy.watch_video_pods import (
    ATTEMPT_KEY, POOL_KEY, Capture, Frames, WatchSettings, export_stop,
    private_file, sanitize_event, sha, watch,
)


NAMESPACE, POOL = 'agent-service', 'pi-video-scene-v1'
POD, ATTEMPT, OPERATION, JOB = (str(UUID(int=i)) for i in (1, 2, 3, 4))
PRIVATE = 'synthetic-private-pod-env-do-not-publish'


def event(kind='MODIFIED', *, terminated=True, restart=0, profile='video.scene.v1'):
    state = {'terminated': {'startedAt': '1970-01-01T00:16:40Z', 'finishedAt': '1970-01-01T00:16:44Z',
                            'exitCode': 0, 'reason': PRIVATE, 'message': PRIVATE}}
    if not terminated:
        state = {'running': {'startedAt': '1970-01-01T00:16:40Z'}}
    return dict(type=kind, object=dict(apiVersion='v1', kind='Pod',
        metadata=dict(uid=POD, name='agent-' + ATTEMPT, namespace=NAMESPACE, resourceVersion='12345',
            creationTimestamp='1970-01-01T00:16:00Z', annotations={'internal': PRIVATE},
            labels={'app': 'agent-capsule', POOL_KEY: POOL, ATTEMPT_KEY: ATTEMPT}),
        spec=dict(containers=[dict(name='worker', env=[dict(name='AGENT_PROFILE_NAME', value=profile),
                                                     dict(name='PRIVATE', value=PRIVATE)])],
                  volumes=[dict(name='work', persistentVolumeClaim={'claimName': 'test-workspace'})]),
        status=dict(phase='Succeeded' if terminated else 'Running', containerStatuses=[dict(
            name='worker', imageID='containerd://sha256:' + 'a' * 64, ready=not terminated,
            restartCount=restart, state=state)])))


def job():
    return dict(job_id=JOB, state='cancelled', business_ref='video:' + OPERATION, publication_authorized=False,
        not_started=False, pins=dict(profile='video.scene.v1', profile_revision='1'),
        attempt=dict(attempt_id=ATTEMPT, fence=1), stop_proven=True,
        stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1, request_sha256='a' * 64,
            result_sha256='b' * 64, resource_ids=['k8s:' + NAMESPACE + ':' + POD], observed_at=1005.0))


def test_frames_preserve_exact_pretty_json_and_handle_split_utf8_and_successive_values():
    first = event()
    first['object']['metadata']['annotations']['中文'] = '真实终止事件'
    raw1 = json.dumps(first, ensure_ascii=False, indent=2).encode()
    raw2 = canonical_bytes(event('DELETED'))
    stream = b'\n  ' + raw1 + b'\n\n' + raw2 + b'\n'
    parser, result = Frames(), []
    for value in stream:
        result.extend(parser.feed(bytes([value])))
    result.extend(parser.feed(b'', final=True))
    assert result == [(raw1, first), (raw2, event('DELETED'))]


@pytest.mark.parametrize('raw', [b'{"type":"ADDED","type":"ERROR","object":{}}', b'{"type":'])
def test_duplicate_or_truncated_json_cannot_become_a_watch_observation(raw):
    parser = Frames()
    with pytest.raises(VerificationFailure):
        parser.feed(raw)
        parser.feed(b'', final=True)


def test_scoped_capture_sanitizes_public_evidence_and_privately_seals_exact_source(tmp_path):
    capture = Capture(tmp_path / 'watch', NAMESPACE, POOL, clock=lambda: 1007)
    raw = json.dumps(event(), ensure_ascii=False, indent=2).encode()
    capture.add(raw, event(), 1006)
    # Even a mislabeled research Pod is excluded from both archives.
    foreign = event(profile='research.generate.v1')
    capture.add(canonical_bytes(foreign), foreign, 1006.1)
    capture.close('operator_interrupted')
    public = (capture.output / 'events.jsonl').read_bytes()
    assert PRIVATE.encode() not in public
    assert PRIVATE.encode() not in (capture.output / 'report.json').read_bytes()
    row = json.loads(public)
    assert row['source_sha256'] == sha(raw) and row['observed_at'] == 1006
    assert row['all_terminated'] is row['restart_zero'] is True
    assert row['pod']['pvc_claims'] == ['test-workspace']
    assert (capture.raw_dir / (sha(raw) + '.json')).read_bytes() == raw
    assert len(list(capture.raw_dir.iterdir())) == 1
    assert read_json(capture.output / 'report.json')['filtered_events'] == 1
    if os.name == 'posix':
        assert capture.raw_dir.stat().st_mode & 0o077 == 0
        assert next(capture.raw_dir.iterdir()).stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('variant', ['running', 'restart', 'missing_status', 'running_ephemeral'])
def test_incomplete_running_or_restarted_pod_does_not_prove_all_writers_stopped(variant):
    value = event(terminated=variant != 'running', restart=1 if variant == 'restart' else 0)
    if variant == 'missing_status':
        value['object']['status']['containerStatuses'] = []
    if variant == 'running_ephemeral':
        value['object']['spec']['ephemeralContainers'] = [{'name': 'debugger'}]
        value['object']['status']['ephemeralContainerStatuses'] = [dict(name='debugger', restartCount=0,
            ready=True, imageID='sha256:debug', state={'running': {'startedAt': '1970-01-01T00:16:40Z'}})]
    safe = sanitize_event(value, namespace=NAMESPACE, pool=POOL)
    assert not (safe['all_terminated'] and safe['restart_zero'])


def test_export_uses_actual_deleted_event_time_and_binds_raw_source_and_job(tmp_path):
    capture = Capture(tmp_path / 'watch', NAMESPACE, POOL, clock=lambda: 1008)
    first, deleted = event(), event('DELETED')
    raw1, raw2 = canonical_bytes(first), json.dumps(deleted, indent=2).encode()
    capture.add(raw1, first, 1004.5)  # genuine but precedes the controller receipt
    capture.add(raw2, deleted, 1006.0)
    capture.close('operator_interrupted')
    job_file, output = tmp_path / 'job.json', tmp_path / 'observation.json'
    private_file(job_file, canonical_bytes(job()))
    observation = export_stop(capture.output, job_file, output)
    assert observation['started_at'] == observation['observed_at'] == 1006.0
    assert observation['source_watch']['event_type'] == 'DELETED'
    assert observation['source_watch']['source_sha256'] == sha(raw2)
    assert observation['source_watch']['event_seq'] == 2
    assert pod_stop(job(), observation)[0]['pod_uid'] == POD
    assert PRIVATE not in output.read_text()
    # The original pending cancellation report is not edited by conversion.
    assert read_json(capture.output / 'report.json')['terminated_restart_zero_events'] == 2


def test_export_rejects_missing_later_observation_instead_of_inventing_time(tmp_path):
    capture = Capture(tmp_path / 'watch', NAMESPACE, POOL, clock=lambda: 1008)
    capture.add(canonical_bytes(event()), event(), 1004.0)
    capture.close('time_limit')
    private_file(tmp_path / 'job.json', canonical_bytes(job()))
    with pytest.raises(VerificationFailure, match='matching_terminal_watch_event_unavailable'):
        export_stop(capture.output, tmp_path / 'job.json', tmp_path / 'observation.json')
    assert not (tmp_path / 'observation.json').exists()


def test_export_detects_tampered_private_raw_event(tmp_path):
    capture = Capture(tmp_path / 'watch', NAMESPACE, POOL, clock=lambda: 1008)
    raw = canonical_bytes(event())
    capture.add(raw, event(), 1006.0)
    capture.close('completed')
    (capture.raw_dir / (sha(raw) + '.json')).write_bytes(raw + b' ')
    private_file(tmp_path / 'job.json', canonical_bytes(job()))
    with pytest.raises(VerificationFailure, match='watch_source_digest_mismatch'):
        export_stop(capture.output, tmp_path / 'job.json', tmp_path / 'observation.json')


def test_watch_command_is_narrow_read_only_and_bounded(tmp_path):
    settings = WatchSettings(tmp_path / 'kubectl', tmp_path / 'kubeconfig', NAMESPACE, POOL, tmp_path / 'watch')
    command = settings.command()
    assert command[-8:] == ['get', 'pods', '-l', POOL_KEY + '=' + POOL, '--watch', '--output-watch-events', '-o', 'json']
    assert '--request-timeout=1800s' in command
    with pytest.raises(VerificationFailure):
        WatchSettings(settings.kubectl, settings.kubeconfig, NAMESPACE, 'research-pi', settings.output)
    with pytest.raises(VerificationFailure):
        WatchSettings(settings.kubectl, settings.kubeconfig, NAMESPACE, POOL, settings.output, 1801)


def test_watch_fake_process_preserves_reader_time_and_never_prints_kubectl_stderr(tmp_path):
    class Process:
        def __init__(self, command, **kwargs):
            self.command, self.kwargs = command, kwargs
            self.stdout = io.BytesIO(canonical_bytes(event()) + b'\n')
        def poll(self): return 0
        def wait(self, timeout): return 0
    processes = []
    def popen(command, **kwargs):
        value = Process(command, **kwargs)
        processes.append(value)
        return value
    settings = WatchSettings(tmp_path / 'kubectl', tmp_path / 'kubeconfig', NAMESPACE, POOL, tmp_path / 'watch')
    report = watch(settings, popen=popen, wall_clock=lambda: 1006.25, monotonic=lambda: 10)
    assert report['status'] == 'completed' and report['events'] == 1
    row = json.loads((settings.output / 'events.jsonl').read_bytes())
    assert row['observed_at'] == 1006.25
    assert processes[0].kwargs['shell'] is False


def test_ctrl_c_keeps_committed_terminal_event_and_final_report(tmp_path, monkeypatch):
    class Process:
        def __init__(self, *args, **kwargs): self.stdout = io.BytesIO(canonical_bytes(event()))
        def poll(self): return 0
        def wait(self, timeout): return 0
    original = Capture.add
    def interrupt_after_commit(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise KeyboardInterrupt()
    monkeypatch.setattr(Capture, 'add', interrupt_after_commit)
    settings = WatchSettings(tmp_path / 'kubectl', tmp_path / 'kubeconfig', NAMESPACE, POOL, tmp_path / 'watch')
    report = watch(settings, popen=Process, wall_clock=lambda: 1006.25, monotonic=lambda: 10)
    assert report['status'] == 'operator_interrupted' and report['events'] == 1
    assert read_json(settings.output / 'report.json')['terminated_restart_zero_events'] == 1
    assert len((settings.output / 'events.jsonl').read_bytes().splitlines()) == 1
