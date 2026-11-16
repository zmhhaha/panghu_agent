"""Exercise the resource boundary with small subprocesses, never a model."""
import ctypes
import json
import os
from pathlib import Path
import sys
import time

import pytest

from agent_service.contracts import ServiceError
from video_workbench import alignment_runner as runner


WINDOWS = pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job Object test')


def invoke(tmp_path, code, **options):
    return runner.run_bounded([sys.executable, '-u', '-c', code], cwd=tmp_path,
        stdout_path=tmp_path / 'stdout.log', stderr_path=tmp_path / 'stderr.log', **options)


@WINDOWS
def test_success_is_bounded_and_does_not_forward_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('HF_TOKEN', 'not-a-real-token')
    monkeypatch.setenv('TEST_PRIVATE_CREDENTIAL', 'not-a-real-secret')
    report = invoke(tmp_path,
        "import os,json; print(json.dumps({k:os.environ.get(k) for k in "
        "['HF_TOKEN','TEST_PRIVATE_CREDENTIAL','HF_HUB_OFFLINE','OMP_NUM_THREADS']}))")
    assert report['status'] == 'completed'
    assert report['exit_code'] == 0 and report['cleanup_confirmed']
    assert 0 < report['peak_memory_bytes'] <= runner.MAX_MEMORY_BYTES
    assert report['memory_metric'] == 'windows-job-private-commit'
    data = json.loads((tmp_path / 'stdout.log').read_text())
    assert data == dict(HF_TOKEN=None, TEST_PRIVATE_CREDENTIAL=None, HF_HUB_OFFLINE='1', OMP_NUM_THREADS='2')
    assert report['attempts'] == 1
    assert 'not-a-real-token' not in json.dumps(report)


@WINDOWS
def test_nonzero_exit_is_not_success(tmp_path):
    report = invoke(tmp_path, "import sys; print('failed',file=sys.stderr); sys.exit(7)")
    assert report['status'] == 'failed' and report['exit_code'] == 7
    assert report['cleanup_confirmed']
    assert (tmp_path / 'stderr.log').read_text().strip() == 'failed'


@WINDOWS
def test_timeout_kills_complete_job(tmp_path):
    report = invoke(tmp_path, 'import time; time.sleep(60)', timeout_seconds=.2)
    assert report['status'] == 'timeout'
    assert report['cleanup_confirmed'] and report['wall_seconds'] < 10


def _is_alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong], ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        code = ctypes.c_ulong()
        assert kernel.GetExitCodeProcess(process, ctypes.byref(code))
        return code.value == 259
    finally:
        kernel.CloseHandle(process)


@WINDOWS
def test_primary_exit_cannot_leave_an_untracked_descendant(tmp_path):
    pid_file = tmp_path / 'child.pid'
    code = ("import subprocess,sys,pathlib; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
            f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid))")
    report = invoke(tmp_path, code, timeout_seconds=5)
    assert report['status'] == 'descendant_leak'
    assert report['cleanup_confirmed']
    assert not _is_alive(int(pid_file.read_text()))


@WINDOWS
def test_allocation_limit_is_an_explicit_failure(tmp_path):
    report = invoke(tmp_path,
        "a=[]\nwhile True: a.append(bytearray(8*1024*1024))",
        memory_bytes=64 * 1024 ** 2, timeout_seconds=10)
    assert report['status'] == 'memory_limit'
    assert report['cleanup_confirmed']
    assert report['peak_memory_bytes'] > 0
    assert report['budget']['memory_bytes'] == 64 * 1024 ** 2


@WINDOWS
def test_log_output_is_capped_and_trial_fails(tmp_path):
    report = invoke(tmp_path, "import sys; sys.stdout.write('a'*2000000)", max_log_bytes=1024)
    assert report['status'] == 'log_limit'
    assert (tmp_path / 'stdout.log').stat().st_size == 1024
    assert report['cleanup_confirmed']


@WINDOWS
def test_uncertain_log_cleanup_does_not_hash_unstable_logs(tmp_path, monkeypatch):
    pump = runner._pump

    def uncertain_pump(read_fd, log_fd, state, *args):
        pump(read_fd, log_fd, state, *args)
        state['read_error'] = True

    monkeypatch.setattr(runner, '_pump', uncertain_pump)
    report = invoke(tmp_path, "print('finished')")
    assert report['status'] == 'cleanup_unconfirmed'
    assert report['cleanup_confirmed'] is False
    assert all('sha256' not in item and item['digest_status'] == 'skipped-cleanup-unconfirmed'
               for item in report['logs'])


def test_linux_without_verified_external_boundary_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.sys, 'platform', 'linux')
    with pytest.raises(ServiceError, match='platform_unverified'):
        invoke(tmp_path, "print('must not run')")
    assert not (tmp_path / 'stdout.log').exists()


@WINDOWS
@pytest.mark.parametrize('kwargs', [
    {'memory_bytes': True}, {'memory_bytes': runner.MAX_MEMORY_BYTES + 1},
    {'timeout_seconds': float('nan')}, {'timeout_seconds': 301},
    {'timeout_seconds': 10**1000},
    {'threads': 3}, {'threads': True}, {'max_log_bytes': 2**30},
])
def test_invalid_budget_never_starts_a_process(tmp_path, kwargs):
    with pytest.raises(ServiceError):
        invoke(tmp_path, "print('must not run')", **kwargs)
    assert not (tmp_path / 'stdout.log').exists()


@WINDOWS
def test_log_output_cannot_overwrite_existing_files(tmp_path):
    path = tmp_path / 'stdout.log'
    path.write_text('preserve')
    with pytest.raises(ServiceError, match='output_exists'):
        invoke(tmp_path, "print('must not run')")
    assert path.read_text() == 'preserve'


@WINDOWS
def test_start_failure_never_reports_success(tmp_path):
    with pytest.raises(OSError):
        runner.run_bounded([str(Path(__file__).resolve())], cwd=tmp_path,
            stdout_path=tmp_path / 'stdout.log', stderr_path=tmp_path / 'stderr.log')


def alignment_options(tmp_path):
    import wave
    audio = tmp_path / 'sample.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x01\x00' * 8000)
    spoken = tmp_path / 'spoken.txt'
    spoken.write_text('请求。', encoding='utf-8')
    model = tmp_path / 'model'
    model.mkdir()
    return dict(audio=audio, spoken_text=spoken, model_path=model, asset_manifest_sha256='a' * 64,
                output=tmp_path / 'result.json', stdout_path=tmp_path / 'out.log',
                stderr_path=tmp_path / 'err.log')


def test_fixed_alignment_entry_rejects_invalid_result(tmp_path, monkeypatch):
    options = alignment_options(tmp_path)
    output = tmp_path / 'result.json'
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        output.write_text('{}')
        return dict(status='completed', attempts=1, cleanup_confirmed=True)

    monkeypatch.setattr(runner, 'run_bounded', fake)
    report = runner.run_alignment(sys.executable, cwd=tmp_path, **options)
    assert calls[0][1:3] == ['-m', 'video_workbench.alignment_lab']
    assert report['status'] == 'invalid_output'


def test_raw_evidence_is_hashed_even_when_alignment_fails(tmp_path, monkeypatch):
    import hashlib
    options = alignment_options(tmp_path)
    raw = tmp_path / 'raw.json'
    options['raw_output'] = raw
    data = b'{"structural_validation":"not-yet-run"}'

    def fake(args, **kwargs):
        assert args[-2:] == ['--raw-output', str(raw)]
        raw.write_bytes(data)
        return dict(status='failed', attempts=1, cleanup_confirmed=True)

    monkeypatch.setattr(runner, 'run_bounded', fake)
    report = runner.run_alignment(sys.executable, cwd=tmp_path, **options)
    assert report['status'] == 'failed' and 'artifact' not in report
    assert report['raw_artifact']['sha256'] == hashlib.sha256(data).hexdigest()
    assert report['raw_artifact']['structural_validation'] == 'not-certified'
    assert report['raw_artifact']['human_timing_verified'] is False


def test_uncertain_cleanup_does_not_read_or_hash_any_artifact(tmp_path, monkeypatch):
    from video_workbench import alignment_lab
    options = alignment_options(tmp_path)
    options['raw_output'] = tmp_path / 'raw.json'

    def fake(*args, **kwargs):
        options['output'].write_text('{}')
        options['raw_output'].write_text('{}')
        return dict(status='cleanup_unconfirmed', attempts=1, cleanup_confirmed=False)

    monkeypatch.setattr(runner, 'run_bounded', fake)
    monkeypatch.setattr(alignment_lab, 'read_bounded',
                        lambda *a, **k: pytest.fail('must not read unstable artifacts'))
    # The initial WAV identity check is independent of post-run artifact reads.
    monkeypatch.setattr(alignment_lab, 'inspect_recording', lambda *a: {'sha256': 'a' * 64})
    report = runner.run_alignment(sys.executable, cwd=tmp_path, **options)
    assert report['status'] == 'cleanup_unconfirmed'
    assert report['artifact_status'] == 'skipped-cleanup-unconfirmed'
    assert 'artifact' not in report and 'raw_artifact' not in report


@pytest.mark.parametrize('field', ['output', 'stdout_path', 'stderr_path', 'raw_output'])
def test_all_outputs_reject_input_alias_before_starting(tmp_path, monkeypatch, field):
    options = alignment_options(tmp_path)
    original = options['spoken_text'].read_bytes()
    options[field] = options['spoken_text']
    monkeypatch.setattr(runner, 'run_bounded', lambda *a, **k: pytest.fail('must not start'))
    with pytest.raises(ServiceError, match='output_is_input'):
        runner.run_alignment(sys.executable, cwd=tmp_path, **options)
    assert options['spoken_text'].read_bytes() == original


@pytest.mark.parametrize('field', ['stdout_path', 'stderr_path', 'raw_output'])
def test_outputs_cannot_alias_each_other(tmp_path, monkeypatch, field):
    options = alignment_options(tmp_path)
    options[field] = options['output']
    monkeypatch.setattr(runner, 'run_bounded', lambda *a, **k: pytest.fail('must not start'))
    with pytest.raises(ServiceError, match='output_alias'):
        runner.run_alignment(sys.executable, cwd=tmp_path, **options)
    assert not options['output'].exists()


@pytest.mark.parametrize('identity', ['a' * 40, 'g' * 64, 'A' * 64, None, True])
def test_incomplete_asset_identity_is_rejected_before_start(tmp_path, monkeypatch, identity):
    options = alignment_options(tmp_path)
    options['asset_manifest_sha256'] = identity
    monkeypatch.setattr(runner, 'run_bounded', lambda *a, **k: pytest.fail('must not start'))
    with pytest.raises(ServiceError, match='asset_identity_required'):
        runner.run_alignment(sys.executable, cwd=tmp_path, **options)


@pytest.mark.parametrize('mismatch', [None, 'manifest', 'mode', 'sdk_runtime_verified'])
def test_completed_report_requires_exact_asset_and_real_runtime_identity(tmp_path, monkeypatch,
                                                                       mismatch):
    from video_workbench.alignment_lab import inspect_recording
    from video_workbench.motion import make_beats
    from video_workbench.word_alignment import build_word_alignment
    options = alignment_options(tmp_path)
    recording = inspect_recording(options['audio'])
    beats = make_beats('请求。', '请求。')
    alignment = build_word_alignment(beats, recording['sha256'], recording['duration_seconds'],
        [dict(text='请求', start_time=0, end_time=.5)],
        dict(kind='forced_alignment', provider='qwen', model='Qwen3-ForcedAligner-0.6B', version='test'))
    execution = dict(mode='qwen-local-cpu', inference_executed=True, runner_injected=False,
        sdk_runtime_verified=True, model_assets=dict(manifest_sha256='a' * 64))
    if mismatch == 'manifest':
        execution['model_assets']['manifest_sha256'] = 'b' * 64
    elif mismatch == 'mode':
        execution['mode'] = 'import'
    elif mismatch == 'sdk_runtime_verified':
        execution['sdk_runtime_verified'] = False
    document = dict(schema='video.alignment-lab-report.v1', recording=recording,
                    beats=beats, alignment=alignment, execution=execution,
                    human_timing_verified=False)

    def fake(args, **kwargs):
        assert args[args.index('--asset-manifest-sha256') + 1] == 'a' * 64
        assert '--model-revision' not in args
        options['output'].write_text(json.dumps(document), encoding='utf-8')
        return dict(status='completed', attempts=1, cleanup_confirmed=True)

    monkeypatch.setattr(runner, 'run_bounded', fake)
    report = runner.run_alignment(sys.executable, cwd=tmp_path, **options)
    assert report['status'] == ('completed' if mismatch is None else 'invalid_output')
    assert ('artifact' in report) == (mismatch is None)
