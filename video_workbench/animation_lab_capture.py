"""Bounded, opt-in Linux Docker capture; standard library only.

This entry observes fixed animation-lab inputs. It never pulls images, encodes
video, invokes a model, retries, adopts a result or touches a production route.
Inputs and adapter sources are copied into a new run-owned directory; the
original input bytes remain unchanged. Successful observations are copied back
for the separate production technical reviewer. Success here means capture and
cleanup succeeded, not that animation quality or even semantic checks passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
import uuid


MAX_TIMEOUT = 210
MAX_LOG_BYTES = 256 * 1024
MAX_FRAMES = 240
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 96 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'([a-z0-9][a-z0-9._:/-]*):latest@(sha256:[0-9a-f]{64})\Z')
_FRAME = re.compile(r'[a-z0-9][a-z0-9-]{0,100}\.jpg\Z')
_INPUTS = frozenset({'index.html', 'plan.json', 'beats.json', 'alignment.json',
                     'timeline.json', 'manifest.json', 'audio.wav'})
_ADAPTERS = {'lab_observe.mjs': 'capture_adapter_sha256', 'observe.mjs': 'observer_sha256',
             'runtime.mjs': 'runtime_collector_sha256', 'contract.mjs': None}
_OWNER = 'panghu.animation-lab.owner'
_CAPTURE = 'panghu.animation-lab.capture'
_RUNTIME_KEYS = frozenset({'schema', 'hyperframes', 'hyperframes_source', 'node', 'browser',
    'ffmpeg', 'gsap', 'gsap_sha256', 'font_sha256', 'npm_lock_sha256', 'dpkg_sha256', 'image_digest'})
_IMAGE_FORMAT = '{"image_id":{{json .Id}},"repo_digests":{{json .RepoDigests}}}'
_CONTAINER_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
    '"running":{{json .State.Running}},"exit_code":{{json .State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},'
    '"owner":{{json (index .Config.Labels "' + _OWNER + '")}},'
    '"capture":{{json (index .Config.Labels "' + _CAPTURE + '")}}}')


class CaptureExecutionError(RuntimeError):
    """The durable outcome is available even when execution failed."""
    def __init__(self, outcome):
        self.outcome = outcome
        super().__init__(outcome['error'])


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path, limit):
    path = Path(path)
    _require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit,
             'animation_lab_executor_file_invalid')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, 'animation_lab_executor_file_invalid')
    return raw


def _exclusive(path, raw):
    with Path(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _runtime(value):
    _require(type(value) is dict and set(value) == _RUNTIME_KEYS,
             'animation_lab_executor_runtime_invalid')
    _require(all(type(item) is str and 0 < len(item) <= 2000 and '\0' not in item
                 for item in value.values()), 'animation_lab_executor_runtime_invalid')
    _require(value['schema'] == 'video.motion-runtime.v1' and value['hyperframes'] == '0.8.149'
             and value['gsap'] == '3.14.2', 'animation_lab_executor_runtime_invalid')
    _require(all(_SHA.fullmatch(value[key]) for key in
                 ('gsap_sha256', 'font_sha256', 'npm_lock_sha256', 'dpkg_sha256')),
             'animation_lab_executor_runtime_invalid')
    _require(re.fullmatch(r'sha256:[0-9a-f]{64}', value['image_digest']),
             'animation_lab_executor_runtime_invalid')


def capture_plan(directory, runtime, image, overlay_dir, timeout=MAX_TIMEOUT):
    """Validate local inputs and return a plan without calling Docker or writing."""
    directory, overlay_dir = Path(directory).absolute(), Path(overlay_dir).absolute()
    _require(not directory.is_symlink() and directory.is_dir()
             and not overlay_dir.is_symlink() and overlay_dir.is_dir(),
             'animation_lab_executor_directory_invalid')
    for path in (directory, overlay_dir):
        _require(not any(char in str(path) for char in (',', '\n', '\r', '\0')),
                 'animation_lab_executor_mount_path_invalid')
    _require(type(timeout) in (int, float) and 30 <= timeout <= MAX_TIMEOUT,
             'animation_lab_executor_timeout_invalid')
    _runtime(runtime)
    match = _IMAGE.fullmatch(image) if type(image) is str else None
    _require(match is not None and match[2] == runtime['image_digest'],
             'animation_lab_executor_image_mismatch')
    raw = _read(directory / 'capture-request.json', 64 * 1024)
    request = json.loads(raw)
    _require(type(request) is dict and request.get('schema') == 'video.animation-lab-capture.v1'
             and request.get('runtime') == runtime and request.get('files', {}).keys() == _INPUTS
             and request.get('max_frames') == MAX_FRAMES
             and request.get('max_evidence_bytes') == MAX_EVIDENCE_BYTES
             and request.get('output') == 'sampled_browser_observations_not_mp4'
             and all(type(request.get(key)) is int and request[key] == 0
                     for key in ('agent_calls', 'tts_calls', 'production_writes')),
             'animation_lab_executor_request_invalid')
    unsigned = {key: value for key, value in request.items() if key != 'capture_identity'}
    _require(_sha(_canonical(unsigned)) == request.get('capture_identity'),
             'animation_lab_executor_request_identity_mismatch')
    _require(raw == _canonical(request), 'animation_lab_executor_request_encoding_mismatch')
    for name, digest in request['files'].items():
        _require(_sha(_read(directory / name, 8 * 1024 * 1024)) == digest,
                 'animation_lab_executor_input_mismatch')
    adapters = {}
    bindings = dict(_ADAPTERS)
    if request.get('engine_observer_sha256'):
        bindings['engine_observe.mjs'] = 'engine_observer_sha256'
    for name, binding in bindings.items():
        adapters[name] = _sha(_read(overlay_dir / name, 1024 * 1024))
        _require(binding is None or adapters[name] == request.get(binding),
                 'animation_lab_executor_adapter_mismatch')
    for name, digest in request.get('engine_assets', {}).items():
        _require(re.fullmatch(r'three\.(?:module|core)\.min\.js', name)
                 and _SHA.fullmatch(digest)
                 and _sha(_read(overlay_dir / name, 8 * 1024 * 1024)) == digest,
                 'animation_lab_executor_engine_asset_mismatch')
        adapters[name] = digest
    timeline = json.loads(_read(directory / 'timeline.json', 8 * 1024 * 1024))
    _require(type(timeline) is dict and type(timeline.get('actions')) is list
             and len(timeline['actions']) <= 64
             and timeline.get('compiler') == request.get('compiler')
             and timeline.get('duration_seconds') == request.get('duration_seconds'),
             'animation_lab_executor_timeline_mismatch')
    for name in ('capture-start.json', 'capture-outcome.json', 'capture.log',
                 'capture-work', 'capture-adapter', 'observations', 'observations.json',
                 'capture-receipt.json', 'assets'):
        _require(not (directory / name).exists() and not (directory / name).is_symlink(),
                 'animation_lab_executor_no_overwrite')
    return dict(schema='video.animation-lab-execution-plan.v1', directory=str(directory),
        overlay_dir=str(overlay_dir), image=image, repository=match[1], image_digest=match[2],
        timeout_seconds=timeout, capture_identity=request['capture_identity'],
        request_sha256=_sha(raw), adapters=adapters,
        network='none', pull='never', uid=10001, read_only_root=True,
        cpu_limit=2, memory_limit='1g', max_frames=MAX_FRAMES,
        max_evidence_bytes=MAX_EVIDENCE_BYTES, max_log_bytes=MAX_LOG_BYTES,
        output='sampled_browser_observations_not_mp4', production_writes=0,
        agent_calls=0, tts_calls=0, automatic_retry=False, automatic_adoption=False)


def _ownership(path, *, directory=False):
    os.chown(path, 10001, 10001)
    os.chmod(path, 0o755 if directory else 0o444)


def _prepare(plan, request):
    root = Path(plan['directory'])
    work, adapters = root / 'capture-work', root / 'capture-adapter'
    work.mkdir()
    adapters.mkdir()
    for name, digest in {**request['files'], 'capture-request.json': plan['request_sha256']}.items():
        raw = _read(root / name, 8 * 1024 * 1024)
        _require(_sha(raw) == digest, 'animation_lab_executor_input_changed')
        _exclusive(work / name, raw)
        _ownership(work / name)
    _ownership(work, directory=True)
    for name, digest in plan['adapters'].items():
        raw = _read(Path(plan['overlay_dir']) / name, 8 * 1024 * 1024)
        _require(_sha(raw) == digest, 'animation_lab_executor_adapter_changed')
        _exclusive(adapters / name, raw)
        os.chmod(adapters / name, 0o444)
    os.chmod(adapters, 0o755)
    return work, adapters


def _command(arguments, timeout, *, log_path=None):
    """Bound CLI duration/output; Docker itself receives no host credentials."""
    argv = ['docker', *arguments]
    if log_path is None:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
        _require(len(result.stdout) + len(result.stderr) <= MAX_LOG_BYTES,
                 'animation_lab_executor_command_output_limit')
        return dict(code=result.returncode, out=result.stdout.decode('utf-8', 'replace'),
                    err=result.stderr.decode('utf-8', 'replace'))
    with Path(log_path).open('xb') as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if Path(log_path).stat().st_size > MAX_LOG_BYTES:
                    raise ValueError('animation_lab_executor_capture_log_limit')
                if time.monotonic() >= deadline:
                    raise TimeoutError('animation_lab_executor_capture_timeout')
                time.sleep(.05)
            _require(Path(log_path).stat().st_size <= MAX_LOG_BYTES,
                     'animation_lab_executor_capture_log_limit')
            return dict(code=process.returncode, out='', err='')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            log.flush()
            os.fsync(log.fileno())


def _remaining(deadline, cap=10):
    seconds = min(cap, deadline - time.monotonic())
    if seconds <= 0:
        raise TimeoutError('animation_lab_executor_deadline')
    return seconds


def _checked(arguments, deadline, cap=10):
    result = _command(arguments, _remaining(deadline, cap))
    _require(result['code'] == 0, 'animation_lab_executor_docker_command_failed')
    return result['out'].strip()


def _container(name, deadline):
    result = _command(['container', 'inspect', '--format', _CONTAINER_FORMAT, name],
                      _remaining(deadline, 5))
    if result['code'] != 0:
        _require('No such object' in result['err'] or 'No such container' in result['err'],
                 'animation_lab_executor_container_inspect_failed')
        return None
    value = json.loads(result['out'])
    _require(type(value) is dict, 'animation_lab_executor_container_metadata_invalid')
    return value


def _owned(value, *, name, token, capture, image_id, container_id=None):
    return (type(value) is dict and value.get('name') == '/' + name
        and value.get('owner') == token and value.get('capture') == capture
        and value.get('image') == image_id
        and type(value.get('id')) is str and _SHA.fullmatch(value['id']) is not None
        and (container_id is None or value['id'] == container_id))


def _cleanup(*, name, token, capture, image_id, container_id, deadline):
    value = _container(container_id or name, deadline)
    if value is None:
        return dict(status='confirmed_absent', container_id=container_id)
    _require(_owned(value, name=name, token=token, capture=capture, image_id=image_id,
                    container_id=container_id), 'animation_lab_executor_cleanup_owner_mismatch')
    container_id = value['id']
    _checked(['container', 'rm', '--force', container_id], deadline, 7)
    _require(_container(container_id, deadline) is None,
             'animation_lab_executor_cleanup_not_confirmed')
    return dict(status='removed_and_confirmed_absent', container_id=container_id)


def _outputs(work, request, request_sha, runtime):
    """Validate byte bindings/budgets; semantic review remains a separate step."""
    for name, digest in {**request['files'], 'capture-request.json': request_sha}.items():
        _require(_sha(_read(work / name, 8 * 1024 * 1024)) == digest,
                 'animation_lab_executor_work_input_changed')
    receipt_raw = _read(work / 'capture-receipt.json', 128 * 1024)
    receipt = json.loads(receipt_raw)
    _require(type(receipt) is dict
             and receipt.get('schema') == 'video.animation-lab-capture-receipt.v1'
             and receipt.get('capture_identity') == request['capture_identity']
             and receipt.get('request_sha256') == request_sha and receipt.get('runtime') == runtime
             and receipt.get('image_digest_source') == 'coordinator_pinned_not_self_measured'
             and receipt.get('output') == 'sampled_browser_observations_not_mp4'
             and all(receipt.get(binding) == request[binding]
                     for binding in _ADAPTERS.values() if binding)
             and all(type(receipt.get(key)) is int and receipt[key] == 0
                     for key in ('agent_calls', 'tts_calls', 'production_writes')),
             'animation_lab_executor_receipt_mismatch')
    observations = _read(work / 'observations.json', 8 * 1024 * 1024)
    _require(_sha(observations) == receipt.get('observations_sha256'),
             'animation_lab_executor_observations_mismatch')
    rows = receipt.get('frames')
    _require(type(rows) is list and 1 <= len(rows) <= MAX_FRAMES
             and receipt.get('frame_count') == len(rows), 'animation_lab_executor_frame_limit')
    _require(not (work / 'observations').is_symlink() and (work / 'observations').is_dir(),
             'animation_lab_executor_frame_directory_invalid')
    frames, total = {}, 0
    for row in rows:
        _require(type(row) is dict and type(row.get('name')) is str
                 and _FRAME.fullmatch(row['name']) and row['name'] not in frames,
                 'animation_lab_executor_frame_invalid')
        raw = _read(work / 'observations' / row['name'], MAX_FRAME_BYTES)
        _require(raw[:2] == b'\xff\xd8' and raw[-2:] == b'\xff\xd9'
                 and _sha(raw) == row.get('sha256') and len(raw) == row.get('bytes'),
                 'animation_lab_executor_frame_mismatch')
        frames[row['name']] = raw
        total += len(raw)
        _require(total <= MAX_EVIDENCE_BYTES, 'animation_lab_executor_evidence_limit')
    _require(set(path.name for path in (work / 'observations').iterdir()) == set(frames),
             'animation_lab_executor_extra_frame')
    return receipt_raw, observations, frames


def _error(exc):
    # Keep diagnostics independent of arbitrary Docker output or file contents.
    if type(exc) in (ValueError, TimeoutError) and str(exc).startswith('animation_lab_executor_'):
        return str(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        return 'animation_lab_executor_docker_timeout'
    return 'animation_lab_executor_' + type(exc).__name__.lower()


def capture_directory(directory, runtime, image, overlay_dir, timeout=MAX_TIMEOUT):
    """Execute exactly one fresh capture and confirm cleanup, or raise.

    Linux/root permission to chown the new input copy is required. A failure
    keeps all inputs, logs and run-owned work files for inspection; this same
    directory cannot be rerun. The total deadline reserves 15 seconds for owned
    container cleanup. No Docker pull or connection to an external network occurs.
    """
    plan = capture_plan(directory, runtime, image, overlay_dir, timeout)
    _require(sys.platform == 'linux', 'animation_lab_executor_linux_required')
    root = Path(plan['directory'])
    request = json.loads(_read(root / 'capture-request.json', 64 * 1024))
    token = uuid.uuid4().hex
    name = 'animation-lab-' + request['capture_identity'][:12] + '-' + token[:12]
    start = dict(schema='video.animation-lab-execution-start.v1', started_at=_now(),
                 plan=plan, container_name=name, owner_token=token)
    _exclusive(root / 'capture-start.json', _canonical(start))
    began = time.monotonic()
    deadline = began + timeout
    run_deadline = deadline - 15
    image_id, container_id, created, outputs, error = None, None, False, None, None
    cleanup = dict(status='not_created', container_id=None)
    try:
        actual = json.loads(_checked(['image', 'inspect', '--format', _IMAGE_FORMAT, image], run_deadline))
        _require(type(actual) is dict and type(actual.get('image_id')) is str
                 and re.fullmatch(r'sha256:[0-9a-f]{64}', actual['image_id'])
                 and type(actual.get('repo_digests')) is list
                 and plan['repository'] + '@' + plan['image_digest'] in actual['repo_digests'],
                 'animation_lab_executor_actual_image_mismatch')
        image_id = actual['image_id']
        work, adapters = _prepare(plan, request)
        arguments = ['container', 'create', '--name', name,
            '--label', _OWNER + '=' + token, '--label', _CAPTURE + '=' + request['capture_identity'],
            '--pull=never', '--network=none', '--read-only', '--user=10001:10001',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=1g',
            '--pids-limit=256', '--shm-size=128m', '--tmpfs=/tmp:rw,nosuid,nodev,size=256m',
            '--restart=no', '--log-driver=none', '--stop-timeout=3', '--init', '--workdir=/work',
            '--env', 'ANIMATION_LAB_IMAGE_DIGEST=' + plan['image_digest'],
            '--mount', 'type=bind,source=' + str(work) + ',target=/work']
        for adapter in plan['adapters']:
            arguments += ['--mount', 'type=bind,source=' + str(adapters / adapter)
                          + ',target=/opt/renderer/' + adapter + ',readonly']
        arguments += ['--entrypoint=node', image_id, '/opt/renderer/lab_observe.mjs',
                      '--root', '/work', '--request-sha256', plan['request_sha256']]
        # A timed-out create may still have reached the daemon. Cleanup checks
        # the unique name AND token even if its returned ID was never received.
        created = True
        returned_id = _checked(arguments, run_deadline)
        _require(_SHA.fullmatch(returned_id), 'animation_lab_executor_container_id_invalid')
        container_id = returned_id
        _require(_owned(_container(container_id, run_deadline), name=name, token=token,
            capture=request['capture_identity'], image_id=image_id, container_id=container_id),
            'animation_lab_executor_created_container_mismatch')
        result = _command(['container', 'start', '--attach', container_id],
                          _remaining(run_deadline, timeout), log_path=root / 'capture.log')
        _require(result['code'] == 0, 'animation_lab_executor_capture_failed')
        final = _container(container_id, run_deadline)
        _require(_owned(final, name=name, token=token, capture=request['capture_identity'],
                        image_id=image_id, container_id=container_id)
                 and final.get('running') is False and final.get('exit_code') == 0
                 and final.get('oom_killed') is False,
                 'animation_lab_executor_container_exit_failed')
        outputs = _outputs(work, request, plan['request_sha256'], runtime)
    except BaseException as exc:
        error = _error(exc)
    finally:
        if created:
            try:
                cleanup = _cleanup(name=name, token=token, capture=request['capture_identity'],
                    image_id=image_id, container_id=container_id, deadline=deadline)
            except BaseException as exc:
                cleanup = dict(status='unconfirmed', container_id=container_id, error=_error(exc))
                error = error or cleanup['error']
    if error is None:
        try:
            # Promotion occurs only after owned-container cleanup was confirmed.
            for source, digest in request['files'].items():
                _require(_sha(_read(root / source, 8 * 1024 * 1024)) == digest,
                         'animation_lab_executor_original_input_changed')
            receipt, observations, frames = outputs
            (root / 'observations').mkdir()
            for frame, raw in frames.items():
                _exclusive(root / 'observations' / frame, raw)
            _exclusive(root / 'observations.json', observations)
            _exclusive(root / 'capture-receipt.json', receipt)
        except BaseException as exc:
            error = _error(exc)
    log = root / 'capture.log'
    outcome = dict(schema='video.animation-lab-execution-outcome.v1',
        capture_identity=request['capture_identity'], request_sha256=plan['request_sha256'],
        status='failed' if error else 'captured', error=error,
        started_at=start['started_at'], ended_at=_now(), elapsed_seconds=round(time.monotonic() - began, 3),
        timeout_seconds=timeout, image=image, actual_image_id=image_id,
        container_name=name, container_id=container_id, cleanup=cleanup,
        adapters=plan['adapters'], output='sampled_browser_observations_not_mp4',
        technical_review='not_run', visual_quality='not_evaluated', production_adoption=False,
        agent_calls=0, tts_calls=0, production_writes=0,
        log_bytes=log.stat().st_size if log.exists() else 0,
        log_sha256=_sha(_read(log, MAX_LOG_BYTES)) if log.exists() and log.stat().st_size <= MAX_LOG_BYTES else None)
    _exclusive(root / 'capture-outcome.json', _canonical(outcome))
    if error:
        raise CaptureExecutionError(outcome)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='explicitly execute one bounded Docker capture')
    parser.add_argument('--directory', required=True)
    parser.add_argument('--runtime', required=True, help='local JSON with the frozen measured runtime')
    parser.add_argument('--image', required=True, help='an already-local repository:latest@sha256:...')
    parser.add_argument('--overlay-dir', required=True, help='directory containing the bound capture adapters and engine assets')
    parser.add_argument('--timeout', type=float, default=MAX_TIMEOUT)
    args = parser.parse_args(argv)
    runtime = json.loads(_read(args.runtime, 64 * 1024))
    operation = capture_directory if args.execute else capture_plan
    try:
        result = operation(args.directory, runtime, args.image, args.overlay_dir, args.timeout)
    except CaptureExecutionError as exc:
        print(_canonical(exc.outcome).decode('utf-8'))
        return 1
    print(_canonical(result).decode('utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
