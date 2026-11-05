"""A one-attempt Windows Job Object boundary for offline alignment trials.

Linux execution is deliberately refused until an external resource boundary is
implemented and verified. Model timestamps are not a human timing approval.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from agent_service.contracts import ServiceError


MAX_MEMORY_BYTES = 4 * 1024 ** 3
MAX_WALL_SECONDS = 300
MAX_LOG_BYTES = 4 * 1024 ** 2
POLL_SECONDS = .02


def _require(condition, code='invalid_alignment_runner'):
    if not condition:
        raise ServiceError(code, 422)


def _new_output(path):
    path = Path(path)
    _require(not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(),
             'alignment_runner_output_link')
    _require(path.parent.is_dir(), 'alignment_runner_output_parent')
    result = path.parent.resolve(strict=True) / path.name
    _require(not result.exists(), 'alignment_runner_output_exists')
    return result


def _environment(cwd, threads):
    # Do not forward API tokens, proxy credentials, PYTHONPATH or user-site
    # overrides to an otherwise offline model process.
    allowed = ('SystemRoot', 'WINDIR', 'PATH', 'PATHEXT',
               'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    scratch = cwd / '.alignment-runner-cache'
    scratch.mkdir(exist_ok=True)
    result.update(PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                  HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false',
                  HF_HOME=str(scratch / 'huggingface'), TORCH_HOME=str(scratch / 'torch'),
                  TEMP=str(scratch), TMP=str(scratch))
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        result[key] = str(threads)
    return result


def _pump(read_fd, log_fd, state, limit, limit_event):
    try:
        with os.fdopen(read_fd, 'rb') as source, os.fdopen(log_fd, 'wb') as target:
            while True:
                chunk = source.read(16 * 1024)
                if not chunk:
                    break
                state['observed_bytes'] += len(chunk)
                left = max(0, limit - state['stored_bytes'])
                target.write(chunk[:left])
                state['stored_bytes'] += min(left, len(chunk))
                if len(chunk) > left:
                    limit_event.set()
    except OSError:
        state['read_error'] = True


def _win_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = wintypes.HANDLE
    ptr = ctypes.c_void_p
    size = ctypes.c_size_t

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                     'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                    ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', size), ('MaximumWorkingSetSize', size),
                    ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', size),
                    ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', size), ('JobMemoryLimit', size),
                    ('PeakProcessMemoryUsed', size), ('PeakJobMemoryUsed', size)]

    class ACCOUNTING(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ('TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
        _fields_ += [(name, wintypes.DWORD) for name in
                     ('TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]

    class CPU_LIMIT(ctypes.Structure):
        _fields_ = [('ControlFlags', wintypes.DWORD), ('CpuRate', wintypes.DWORD)]

    class COMPLETION(ctypes.Structure):
        _fields_ = [('CompletionKey', ptr), ('CompletionPort', handle)]

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR),
                    ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR)]
        _fields_ += [(name, wintypes.DWORD) for name in
                     ('dwX', 'dwY', 'dwXSize', 'dwYSize', 'dwXCountChars', 'dwYCountChars',
                      'dwFillAttribute', 'dwFlags')]
        _fields_ += [('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                     ('lpReserved2', ptr), ('hStdInput', handle), ('hStdOutput', handle), ('hStdError', handle)]

    class STARTUPINFOEX(ctypes.Structure):
        _fields_ = [('StartupInfo', STARTUPINFO), ('lpAttributeList', ptr)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [('hProcess', handle), ('hThread', handle),
                    ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]

    signatures = {
        'CreateJobObjectW': ([ptr, wintypes.LPCWSTR], handle),
        'SetInformationJobObject': ([handle, ctypes.c_int, ptr, wintypes.DWORD], wintypes.BOOL),
        'QueryInformationJobObject': ([handle, ctypes.c_int, ptr, wintypes.DWORD, ptr], wintypes.BOOL),
        'CreateIoCompletionPort': ([handle, handle, size, wintypes.DWORD], handle),
        'GetQueuedCompletionStatus': ([handle, ptr, ptr, ptr, wintypes.DWORD], wintypes.BOOL),
        'InitializeProcThreadAttributeList': ([ptr, wintypes.DWORD, wintypes.DWORD, ptr], wintypes.BOOL),
        'UpdateProcThreadAttribute': ([ptr, wintypes.DWORD, size, ptr, size, ptr, ptr], wintypes.BOOL),
        'DeleteProcThreadAttributeList': ([ptr], None),
        'CreateProcessW': ([wintypes.LPCWSTR, wintypes.LPWSTR, ptr, ptr, wintypes.BOOL,
                            wintypes.DWORD, ptr, wintypes.LPCWSTR, ptr, ptr], wintypes.BOOL),
        'AssignProcessToJobObject': ([handle, handle], wintypes.BOOL),
        'ResumeThread': ([handle], wintypes.DWORD),
        'WaitForSingleObject': ([handle, wintypes.DWORD], wintypes.DWORD),
        'GetExitCodeProcess': ([handle, ptr], wintypes.BOOL),
        'TerminateJobObject': ([handle, wintypes.UINT], wintypes.BOOL),
        'TerminateProcess': ([handle, wintypes.UINT], wintypes.BOOL),
        'CloseHandle': ([handle], wintypes.BOOL),
    }
    for name, (args, returns) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, returns
    return kernel, EXTENDED_LIMIT, ACCOUNTING, CPU_LIMIT, COMPLETION, STARTUPINFOEX, PROCESS_INFORMATION


def run_bounded(argv, *, cwd, stdout_path, stderr_path, timeout_seconds=300,
                memory_bytes=MAX_MEMORY_BYTES, threads=2, max_log_bytes=MAX_LOG_BYTES):
    """Run one trusted argv list in a job before its first instruction executes.

    The memory metric is Windows job private committed memory, not RSS. A job
    allocation-limit notification fails the trial even if Python catches the
    allocation error. Logs are bounded through pipes and never returned as text.
    """
    _require(sys.platform == 'win32', 'alignment_runner_platform_unverified')
    _require(type(argv) is list and 1 <= len(argv) <= 100 and
             all(type(arg) is str and arg and '\0' not in arg for arg in argv))
    executable = Path(argv[0])
    _require(executable.is_absolute() and executable.is_file(), 'alignment_runner_python_required')
    _require(type(timeout_seconds) in (int, float)
             and .1 <= timeout_seconds <= MAX_WALL_SECONDS and math.isfinite(timeout_seconds))
    _require(type(memory_bytes) is int and 32 * 1024 ** 2 <= memory_bytes <= MAX_MEMORY_BYTES)
    _require(type(threads) is int and 1 <= threads <= 2)
    _require(type(max_log_bytes) is int and 1024 <= max_log_bytes <= MAX_LOG_BYTES)
    cwd = Path(cwd).resolve(strict=True)
    _require(cwd.is_dir())
    logs = [_new_output(stdout_path), _new_output(stderr_path)]
    _require(logs[0] != logs[1])
    environment = _environment(logs[0].parent, threads)
    kernel, Limit, Accounting, Cpu, Completion, Startup, Process = _win_api()

    def check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def query(kind, value):
        check(kernel.QueryInformationJobObject(job, kind, ctypes.byref(value), ctypes.sizeof(value), None))
        return value

    job = completion = None
    process = Process()
    attributes = None
    attributes_initialized = False
    readers, write_fds, remaining_fds, states = [], [], [], []
    overflow = threading.Event()
    status, exit_code, peak, cleanup, pid = 'runner_error', None, 0, False, None
    started = time.monotonic()
    assigned = False
    try:
        job = check(kernel.CreateJobObjectW(None, None))
        limits = Limit()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x200
        limits.ProcessMemoryLimit = limits.JobMemoryLimit = memory_bytes
        check(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        cpu_rate = max(1, min(10_000, 10_000 * threads // max(1, os.cpu_count() or 1)))
        cpu = Cpu(0x1 | 0x4, cpu_rate)
        check(kernel.SetInformationJobObject(job, 15, ctypes.byref(cpu), ctypes.sizeof(cpu)))
        completion = check(kernel.CreateIoCompletionPort(wintypes.HANDLE(-1), None, 0, 1))
        association = Completion(ctypes.c_void_p(1), completion)
        check(kernel.SetInformationJobObject(job, 7, ctypes.byref(association), ctypes.sizeof(association)))

        import msvcrt
        handles = []
        for path in logs:
            log_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
            read_fd, write_fd = os.pipe()
            os.set_inheritable(write_fd, True)
            write_fds.append(write_fd)
            handles.append(msvcrt.get_osfhandle(write_fd))
            state = dict(observed_bytes=0, stored_bytes=0, read_error=False)
            states.append(state)
            reader = threading.Thread(target=_pump, args=(read_fd, log_fd, state, max_log_bytes, overflow),
                                      daemon=True)
            readers.append(reader)
            reader.start()
        stdin_fd = os.open(os.devnull, os.O_RDONLY | os.O_BINARY)
        remaining_fds.append(stdin_fd)
        os.set_inheritable(stdin_fd, True)
        stdin_handle = msvcrt.get_osfhandle(stdin_fd)
        inherited = (wintypes.HANDLE * 3)(stdin_handle, *handles)
        attribute_size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
        _require(0 < attribute_size.value < 64 * 1024, 'alignment_runner_attribute_limit')
        attributes = ctypes.create_string_buffer(attribute_size.value)
        check(kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(attribute_size)))
        attributes_initialized = True
        check(kernel.UpdateProcThreadAttribute(attributes, 0, 0x20002, ctypes.byref(inherited),
                                              ctypes.sizeof(inherited), None, None))
        startup = Startup()
        startup.StartupInfo.cb = ctypes.sizeof(Startup)
        startup.StartupInfo.dwFlags = 0x100
        startup.StartupInfo.hStdInput = stdin_handle
        startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        block = ctypes.create_unicode_buffer('\0'.join(f'{k}={v}' for k, v in sorted(environment.items())) + '\0\0')
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        check(kernel.CreateProcessW(str(executable), command, None, None, True,
                                    0x4 | 0x08000000 | 0x00080000 | 0x400,
                                    block, str(cwd), ctypes.byref(startup), ctypes.byref(process)))
        pid = process.dwProcessId
        check(kernel.AssignProcessToJobObject(job, process.hProcess))
        assigned = True
        _require(kernel.ResumeThread(process.hThread) != 0xFFFFFFFF, 'alignment_runner_resume_failed')
        kernel.CloseHandle(process.hThread)
        process.hThread = None
        for fd in write_fds:
            os.close(fd)
        write_fds.clear()
        root_finished_at = None
        status = 'running'
        while True:
            # Memory-limit events may be queued after process termination.
            while True:
                message, key, overlapped = wintypes.DWORD(), ctypes.c_size_t(), ctypes.c_void_p()
                if not kernel.GetQueuedCompletionStatus(completion, ctypes.byref(message),
                        ctypes.byref(key), ctypes.byref(overlapped), 0):
                    break
                if message.value in (9, 10):
                    status = 'memory_limit'
            accounting = query(1, Accounting())
            peak = max(peak, query(9, Limit()).PeakJobMemoryUsed)
            if status == 'running' and peak > memory_bytes:
                status = 'memory_limit'
            elapsed = time.monotonic() - started
            done = kernel.WaitForSingleObject(process.hProcess, 0) == 0
            if done:
                code = wintypes.DWORD()
                check(kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code)))
                exit_code = code.value
                root_finished_at = elapsed if root_finished_at is None else root_finished_at
            if status == 'running' and overflow.is_set():
                status = 'log_limit'
            if status == 'running' and elapsed >= timeout_seconds:
                status = 'timeout'
            if status == 'running' and done and accounting.ActiveProcesses and elapsed - root_finished_at >= .5:
                status = 'descendant_leak'
            if status != 'running':
                check(kernel.TerminateJobObject(job, 0xC000013A))
                break
            if done and accounting.ActiveProcesses == 0:
                status = 'completed' if exit_code == 0 else 'failed'
                cleanup = True
                break
            time.sleep(POLL_SECONDS)
        if not cleanup:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if query(1, Accounting()).ActiveProcesses == 0:
                    cleanup = True
                    break
                time.sleep(POLL_SECONDS)
            kernel.WaitForSingleObject(process.hProcess, 5000)
            code = wintypes.DWORD()
            if kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
                exit_code = code.value
            peak = max(peak, query(9, Limit()).PeakJobMemoryUsed)
    finally:
        # An assignment failure still has a suspended primary process. Kill it
        # explicitly; an assigned process tree is killed by closing the job.
        if process.hProcess and not assigned:
            kernel.TerminateProcess(process.hProcess, 0xC000013A)
            kernel.WaitForSingleObject(process.hProcess, 5000)
        if job:
            kernel.CloseHandle(job)
        if process.hThread:
            kernel.CloseHandle(process.hThread)
        if process.hProcess:
            kernel.CloseHandle(process.hProcess)
        if attributes_initialized:
            kernel.DeleteProcThreadAttributeList(attributes)
        for fd in write_fds + remaining_fds:
            os.close(fd)
        for reader in readers:
            reader.join(timeout=5)
        if completion:
            kernel.CloseHandle(completion)
    readers_complete = all(not reader.is_alive() for reader in readers)
    cleanup = cleanup and readers_complete and not any(state['read_error'] for state in states)
    if not cleanup:
        status = 'cleanup_unconfirmed'
    if status == 'completed' and overflow.is_set():
        status = 'log_limit'
    log_reports = []
    for path, state in zip(logs, states):
        report = dict(path=str(path), **state)
        if cleanup:
            report['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            report['digest_status'] = 'skipped-cleanup-unconfirmed'
        log_reports.append(report)
    return dict(schema='video.alignment-runner.v1', platform=sys.platform, status=status,
                exit_code=exit_code, wall_seconds=time.monotonic() - started, process_id=pid,
                peak_memory_bytes=peak, memory_metric='windows-job-private-commit',
                cleanup_confirmed=cleanup, attempts=1,
                budget=dict(memory_bytes=memory_bytes, wall_seconds=timeout_seconds,
                            threads=threads, cpu_rate_per_10000=cpu_rate, max_log_bytes=max_log_bytes),
                command_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                logs=log_reports, human_timing_verified=False)


def run_alignment(python, *, cwd, audio, spoken_text, model_path, asset_manifest_sha256,
                  output, stdout_path, stderr_path, source_text=None,
                  raw_output=None,
                  timeout_seconds=300, memory_bytes=MAX_MEMORY_BYTES, threads=2):
    """Use the fixed alignment module, then validate and hash its output."""
    from .alignment_lab import inspect_recording, read_bounded, validate_report_path, MAX_REPORT_BYTES
    from .word_alignment import validate_word_alignment
    _require(type(asset_manifest_sha256) is str and
             re.fullmatch(r'[0-9a-f]{64}', asset_manifest_sha256) is not None,
             'alignment_runner_asset_identity_required')
    recording = inspect_recording(audio)
    inputs = [audio, spoken_text] + ([source_text] if source_text else [])
    paths = [output, stdout_path, stderr_path] + ([raw_output] if raw_output is not None else [])
    paths = [validate_report_path(path, input_paths=inputs) for path in paths]
    _require(len(set(paths)) == len(paths), 'alignment_runner_output_alias')
    output, stdout_path, stderr_path = paths[:3]
    raw_output = paths[3] if raw_output is not None else None
    args = [str(Path(python).resolve(strict=True)), '-m', 'video_workbench.alignment_lab',
            '--audio', str(Path(audio).resolve(strict=True)),
            '--spoken-text', str(Path(spoken_text).resolve(strict=True)),
            '--model-path', str(Path(model_path).resolve(strict=True)),
            '--asset-manifest-sha256', asset_manifest_sha256,
            '--threads', str(threads), '--output', str(output)]
    if source_text:
        args += ['--source-text', str(Path(source_text).resolve(strict=True))]
    if raw_output is not None:
        args += ['--raw-output', str(raw_output)]
    result = run_bounded(args, cwd=cwd, stdout_path=stdout_path, stderr_path=stderr_path,
                         timeout_seconds=timeout_seconds, memory_bytes=memory_bytes, threads=threads)
    if result.get('cleanup_confirmed') is not True:
        # A process or pipe writer may still be changing these files. Neither
        # an in-flight digest nor a transiently valid report is final evidence.
        result['status'] = 'cleanup_unconfirmed'
        result['artifact_status'] = 'skipped-cleanup-unconfirmed'
        return result
    if raw_output is not None and raw_output.is_file():
        try:
            raw = read_bounded(raw_output, MAX_REPORT_BYTES)
            result['raw_artifact'] = dict(path=str(raw_output), bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(), structural_validation='not-certified',
                human_timing_verified=False)
        except (OSError, ServiceError):
            result['raw_artifact_error'] = 'unreadable-or-too-large'
    if result['status'] == 'completed':
        try:
            raw = read_bounded(output, MAX_REPORT_BYTES)
            document = json.loads(raw)
            validate_word_alignment(document['alignment'], document['beats'])
            _require(document['schema'] == 'video.alignment-lab-report.v1' and
                     document['recording'] == recording == inspect_recording(audio) and
                     document['alignment']['audio_sha256'] == recording['sha256'] and
                     document['execution']['inference_executed'] is True and
                     document['execution']['runner_injected'] is False and
                     document['execution']['mode'] == 'qwen-local-cpu' and
                     document['execution']['sdk_runtime_verified'] is True and
                     document['execution']['model_assets']['manifest_sha256'] == asset_manifest_sha256 and
                     document['human_timing_verified'] is False,
                     'alignment_runner_invalid_result')
            result['artifact'] = dict(path=str(output), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        except (OSError, ValueError, KeyError, TypeError, ServiceError):
            result['status'] = 'invalid_output'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('python', 'cwd', 'audio', 'spoken-text', 'model-path', 'output', 'stdout', 'stderr', 'receipt'):
        parser.add_argument('--' + key, required=True, type=Path)
    parser.add_argument('--source-text', type=Path)
    parser.add_argument('--raw-output', type=Path)
    parser.add_argument('--asset-manifest-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        receipt = _new_output(args.receipt)
        paths = [args.output, args.raw_output, args.stdout, args.stderr,
                 args.audio, args.spoken_text, args.source_text]
        _require(receipt not in {Path(p).resolve() for p in paths if p is not None})
        result = run_alignment(args.python, cwd=args.cwd, audio=args.audio,
            spoken_text=args.spoken_text, source_text=args.source_text,
            model_path=args.model_path, asset_manifest_sha256=args.asset_manifest_sha256,
            output=args.output, raw_output=args.raw_output,
            stdout_path=args.stdout, stderr_path=args.stderr)
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps(dict(receipt=str(receipt), status=result['status'])))
        return 0 if result['status'] == 'completed' else 2
    except (ServiceError, OSError, ValueError) as error:
        parser.exit(2, 'bounded alignment failed: ' + str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
