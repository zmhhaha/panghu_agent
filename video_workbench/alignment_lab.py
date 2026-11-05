"""Bounded offline alignment experiment; never changes workbench state.

Import mode validates existing backend timestamps. The optional Qwen runner was
checked against official source, but an installed SDK and a real local model
still require runtime validation.
It loads only an explicit local directory and disables automatic Hugging Face
Hub downloads. These settings are not OS-level network isolation. No heavy
inference dependencies are imported by the workbench or the ordinary tests.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import time
import wave

from agent_service.contracts import ServiceError
from .motion import make_beats
from .word_alignment import beat_segments, build_word_alignment


MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 60
MAX_TEXT_BYTES = 40_000
MAX_BACKEND_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
_MANIFEST_SHA = re.compile(r'[0-9a-f]{64}\Z')
QWEN_SDK_SOURCE = ('https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/main/'
                   'qwen_asr/inference/qwen3_forced_aligner.py')
QWEN_SDK_SOURCE_PACKAGE_VERSION = '0.0.6'


def _require(condition, code):
    if not condition:
        raise ServiceError(code, 422)


def read_bounded(path, maximum):
    """Read ordinary local files without trusting their declared size alone."""
    path = Path(path).resolve(strict=True)
    _require(path.is_file() and path.stat().st_size <= maximum, 'alignment_lab_input_too_large')
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    _require(len(data) <= maximum, 'alignment_lab_input_too_large')
    return data


def inspect_recording(path):
    data = read_bounded(path, MAX_AUDIO_BYTES)
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            channels, rate = wav.getnchannels(), wav.getframerate()
            width, count = wav.getsampwidth(), wav.getnframes()
            _require(wav.getcomptype() == 'NONE' and channels in (1, 2)
                     and width == 2 and 8_000 <= rate <= 48_000,
                     'alignment_lab_unsupported_wav')
            seconds = count / rate
            _require(.1 <= seconds <= MAX_AUDIO_SECONDS, 'alignment_lab_audio_duration')
            pcm = wav.readframes(count)
            _require(len(pcm) == count * channels * width and any(pcm),
                     'alignment_lab_invalid_wav')
    except (wave.Error, EOFError, ZeroDivisionError) as error:
        raise ServiceError('alignment_lab_invalid_wav', 422) from error
    return dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                duration_seconds=seconds, sample_rate=rate, channels=channels,
                sample_width=width)


@contextmanager
def _offline_hub():
    # This helper is used only by the isolated CLI, not by a shared server.
    keys = ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = '1'
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def qwen_local_items(audio_path, spoken_text, model_path, *, threads=2):
    """Source-reviewed SDK boundary; local CPU runtime remains unverified.

    The official source returns ``results[0].items`` with
    text/start_time/end_time attributes containing float seconds. Unexpected structures fail explicitly;
    this adapter never manufactures token timing or falls back to estimates.
    CPU dtype/device kwargs reach AutoModel.from_pretrained; AutoProcessor is
    loaded separately, so Hub-offline flags cover its initialization as well.
    Keyword behavior in an installed SDK and real CPU inference still require
    a measured trial. The SDK itself fixes/interpolates anomalous predicted
    times: these are model-derived timings, not independent human annotations.
    Environment flags do not provide a network sandbox.
    """
    _require(type(threads) is int and 1 <= threads <= 4, 'alignment_lab_threads')
    model_path = Path(model_path).resolve(strict=True)
    _require(model_path.is_dir() and (model_path / 'config.json').is_file(),
             'alignment_lab_local_model_required')
    _require(any(model_path.glob('*.safetensors')), 'alignment_lab_local_model_required')
    with _offline_hub():
        try:
            torch = importlib.import_module('torch')
            qwen = importlib.import_module('qwen_asr')
        except ImportError as error:
            raise ServiceError('alignment_lab_qwen_not_installed', 422) from error
        old_threads = torch.get_num_threads()
        try:
            torch.set_num_threads(threads)
            model = qwen.Qwen3ForcedAligner.from_pretrained(
                str(model_path), dtype=torch.float32, device_map='cpu',
                local_files_only=True, trust_remote_code=False,
                attn_implementation='eager')
            with torch.inference_mode():
                results = model.align(audio=str(Path(audio_path).resolve(strict=True)),
                                      text=spoken_text, language='Chinese')
            _require(type(results) in (list, tuple) and len(results) == 1,
                     'alignment_lab_qwen_result')
            rows = getattr(results[0], 'items', None)
            _require(type(rows) in (list, tuple) and 1 <= len(rows) <= 20_000,
                     'alignment_lab_qwen_result')
            try:
                return [dict(text=row.text, start_time=row.start_time, end_time=row.end_time)
                        for row in rows]
            except AttributeError as error:
                raise ServiceError('alignment_lab_qwen_result', 422) from error
        finally:
            torch.set_num_threads(old_threads)


def align_recording(audio_path, spoken_text, *, source_text=None,
                    backend_items=None, source=None, model_path=None,
                    asset_manifest_sha256=None, threads=2, qwen_runner=None,
                    backend_observer=None):
    """Return a report without writing files, adopting audio or granting review.

    An injected runner is explicitly labelled as an adapter test. Such a report
    is not evidence that Qwen or any other inference model actually ran.
    """
    audio_path = Path(audio_path).resolve(strict=True)
    recording = inspect_recording(audio_path)
    _require(type(spoken_text) is str and len(spoken_text.encode('utf-8')) <= MAX_TEXT_BYTES,
             'alignment_lab_text_limit')
    original = spoken_text if source_text is None else source_text
    _require(type(original) is str and len(original.encode('utf-8')) <= MAX_TEXT_BYTES,
             'alignment_lab_text_limit')
    beats = make_beats(original, spoken_text)
    started = time.perf_counter()
    if backend_items is not None:
        _require(model_path is None and asset_manifest_sha256 is None and qwen_runner is None,
                 'alignment_lab_backend_conflict')
        _require(type(source) is dict, 'alignment_lab_source_required')
        items = backend_items
        execution = dict(mode='import', inference_executed=False,
                         runner_injected=False, sdk_runtime_verified=False)
    else:
        _require(model_path is not None and type(asset_manifest_sha256) is str
                 and _MANIFEST_SHA.fullmatch(asset_manifest_sha256),
                 'alignment_lab_local_model_required')
        _require(source is None, 'alignment_lab_backend_conflict')
        _require(type(threads) is int and 1 <= threads <= 4, 'alignment_lab_threads')
        directory = Path(model_path).resolve(strict=True)
        _require(directory.is_dir() and (directory / 'config.json').is_file(),
                 'alignment_lab_local_model_required')
        from .alignment_assets import verify_assets
        assets = verify_assets(directory)
        _require(assets['manifest_sha256'] == asset_manifest_sha256,
                 'alignment_lab_model_identity_mismatch')
        runner = qwen_local_items if qwen_runner is None else qwen_runner
        items = runner(audio_path, spoken_text, directory, threads=threads)
        _require(verify_assets(directory) == assets, 'alignment_lab_model_changed')
        try:
            package_version = importlib.metadata.version('qwen-asr')
        except importlib.metadata.PackageNotFoundError:
            package_version = 'not-installed'
        source = dict(kind='forced_alignment', provider='qwen',
                      model='Qwen3-ForcedAligner-0.6B',
                      version='qwen-asr:' + package_version + ';assets-sha256:' + asset_manifest_sha256)
        execution = dict(mode='qwen-local-cpu', inference_executed=qwen_runner is None,
                         runner_injected=qwen_runner is not None,
                         sdk_runtime_verified=qwen_runner is None,
                         adapter_status=('real-sdk-call-completed' if qwen_runner is None
                                         else 'injected-adapter-test'),
                         sdk_source_reference=QWEN_SDK_SOURCE,
                         sdk_source_reference_mutable=True,
                         sdk_source_package_version=QWEN_SDK_SOURCE_PACKAGE_VERSION,
                         model_timestamp_postprocessing='qwen-fix_timestamp-model-derived',
                         threads=threads,
                         model_assets=assets)
    # Never allow an audio replacement while the backend was running to leave
    # a successful receipt bound to the recording inspected before inference.
    _require(inspect_recording(audio_path) == recording, 'alignment_lab_audio_changed')
    if backend_observer is not None:
        # Preserve the real model response even if exact text or unit checks
        # reject it. This raw evidence cannot certify a valid alignment.
        backend_observer(dict(schema='video.alignment-raw-result.v1',
            recording=recording, beats=beats, source=source,
            backend_items=items, execution=execution,
            structural_validation='not-yet-run', human_timing_verified=False,
            production_state_modified=False))
    alignment = build_word_alignment(beats, recording['sha256'],
                                     recording['duration_seconds'], items, source)
    return dict(schema='video.alignment-lab-report.v1', recording=recording,
                beats=beats, alignment=alignment,
                beat_segments=beat_segments(alignment, beats), backend_items=items,
                execution=execution,
                measurements=dict(elapsed_seconds=time.perf_counter() - started),
                human_timing_verified=False, human_listening_approved=False,
                production_state_modified=False)


def validate_report_path(path, *, input_paths=()):
    """Validate before inference without resolving an output's final component.

    Resolving the leaf would follow a dangling symlink and allow exclusive
    creation at its missing target. Resolve only the existing parent and keep
    the final name intact for the later exclusive open.
    """
    path = Path(path)
    _require(not path.is_symlink() and
             not getattr(path, 'is_junction', lambda: False)(),
             'alignment_lab_output_link')
    _require(path.parent.is_dir(), 'alignment_lab_output_parent_required')
    path = path.parent.resolve(strict=True) / path.name
    inputs = {Path(item).resolve() for item in input_paths}
    _require(path not in inputs and not any(path.exists() and item.exists() and
                                           path.samefile(item) for item in inputs),
             'alignment_lab_output_is_input')
    _require(not path.exists(), 'alignment_lab_output_exists')
    return path


def write_report(path, report, *, input_paths=()):
    path = validate_report_path(path, input_paths=input_paths)
    data = (json.dumps(report, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    _require(len(data) <= MAX_REPORT_BYTES, 'alignment_lab_report_too_large')
    # Exclusive creation also rejects a leaf/symlink created after validation.
    with path.open('xb') as stream:
        stream.write(data)


def write_raw_report(path, report, *, input_paths=()):
    """Preserve invalid numeric evidence in strict JSON without repairing it.

    Tagged values are diagnostic only and cannot be imported as valid times.
    The certified report path continues to reject all nonfinite numbers.
    """
    def encode(value):
        if type(value) is float and not math.isfinite(value):
            return dict(diagnostic_nonfinite_float=str(value))
        if type(value) is dict:
            return {key: encode(item) for key, item in value.items()}
        if type(value) is list:
            return [encode(item) for item in value]
        return value
    document = encode(report)
    document['number_encoding'] = 'strict-json-with-tagged-nonfinite-floats'
    write_report(path, document, input_paths=input_paths)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True, type=Path)
    parser.add_argument('--spoken-text', required=True, type=Path)
    parser.add_argument('--source-text', type=Path)
    backend = parser.add_mutually_exclusive_group(required=True)
    backend.add_argument('--backend-json', type=Path)
    backend.add_argument('--model-path', type=Path)
    parser.add_argument('--source-kind', choices=('forced_alignment', 'provider_native'))
    parser.add_argument('--provider')
    parser.add_argument('--model')
    parser.add_argument('--version')
    parser.add_argument('--asset-manifest-sha256')
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--raw-output', type=Path,
                        help='Preserve backend response before strict text validation')
    args = parser.parse_args(argv)
    try:
        inputs = [args.audio, args.spoken_text]
        if args.source_text:
            inputs.append(args.source_text)
        if args.backend_json:
            inputs.append(args.backend_json)
        output = validate_report_path(args.output, input_paths=inputs)
        raw_output = None
        if args.raw_output is not None:
            raw_output = validate_report_path(args.raw_output, input_paths=[*inputs, output])
            _require(raw_output != output, 'alignment_lab_output_is_input')
        spoken = read_bounded(args.spoken_text, MAX_TEXT_BYTES).decode('utf-8')
        original = (read_bounded(args.source_text, MAX_TEXT_BYTES).decode('utf-8')
                    if args.source_text else None)
        options = dict(source_text=original, threads=args.threads)
        if raw_output is not None:
            options['backend_observer'] = lambda value: write_raw_report(
                raw_output, value, input_paths=[*inputs, output])
        if args.backend_json:
            _require(all((args.source_kind, args.provider, args.model, args.version))
                     and args.asset_manifest_sha256 is None, 'alignment_lab_source_required')
            options.update(backend_items=json.loads(read_bounded(args.backend_json,
                                                                MAX_BACKEND_BYTES)),
                           source=dict(kind=args.source_kind, provider=args.provider,
                                       model=args.model, version=args.version))
        else:
            _require(not any((args.source_kind, args.provider, args.model, args.version)),
                     'alignment_lab_backend_conflict')
            options.update(model_path=args.model_path,
                           asset_manifest_sha256=args.asset_manifest_sha256)
        report = align_recording(args.audio, spoken, **options)
        write_report(output, report, input_paths=inputs)
        print(json.dumps(dict(output=str(output),
                              mode=report['execution']['mode'],
                              review_status=report['alignment']['review_status']),
                         ensure_ascii=False))
        return 0
    except (ServiceError, OSError, UnicodeError, ValueError) as error:
        parser.exit(2, 'alignment experiment failed: ' + str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
