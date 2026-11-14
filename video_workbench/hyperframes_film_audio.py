"""Execute an audio-only full-film schedule from a verified immutable package.

No Agent, browser, video concatenation, project adoption or SFX synthesis runs
here. Original WAV resources remain byte-identical; only the delivery mix is
converted to 48 kHz stereo PCM on one absolute project clock.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from agent_service.contracts import ServiceError, digest
from .hyperframes_film_project import FPS, MAX_SECONDS, verify_film_project
from .speech import inspect_wav


SCHEMA = 'video.hyperframes-film-audio-mix.v1'
SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH = 48000, 2, 2
MAX_OUTPUT_BYTES = 128 * 1024 * 1024


def _require(value, code='hyperframes_film_audio_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def _ratio(value):
    return dict(numerator=value.numerator, denominator=value.denominator)


def _time(value):
    return Fraction(value['numerator'], value['denominator'])


def _run(command, *, cwd, deadline):
    remaining = deadline - time.monotonic()
    _require(remaining > 0, 'hyperframes_film_audio_timeout', 504)
    try:
        result = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=remaining, check=False)
    except subprocess.TimeoutExpired:
        raise ServiceError('hyperframes_film_audio_timeout', 504) from None
    except OSError:
        raise ServiceError('hyperframes_film_audio_process_unavailable', 503) from None
    _require(result.returncode == 0, 'hyperframes_film_audio_process_failed', 502)
    return result


def _schedule(manifest):
    count = manifest['clock']['frame_count'] * SAMPLE_RATE // FPS
    tracks = []
    for row in manifest['narration_tracks']:
        start, end = _time(row['start_time']), _time(row['end_time'])
        first, last = start * SAMPLE_RATE // 1, end * SAMPLE_RATE // 1
        _require(0 <= first < last <= count, 'hyperframes_film_audio_unrepresentable_track')
        tracks.append({key: deepcopy(row[key]) for key in (
            'scene_id', 'audio_version_id', 'path', 'sha256', 'spoken_text_sha256',
            'source_sample_rate', 'source_sample_count', 'source_channels', 'source_sample_width',
            'source_start_sample', 'source_end_sample', 'start_time', 'end_time', 'source_policy')})
        tracks[-1].update(delivery_start_sample=first, delivery_end_sample=last,
            delivery_sample_count=last - first,
            start_quantization_error_seconds=_ratio(Fraction(first, SAMPLE_RATE) - start),
            end_quantization_error_seconds=_ratio(Fraction(last, SAMPLE_RATE) - end),
            channel_mapping='duplicate_mono_at_unity' if row['source_channels'] == 1 else 'preserve_stereo')
    return count, tracks


def _filters(tracks, count):
    filters = [f'anullsrc=sample_rate={SAMPLE_RATE}:channel_layout=stereo,'
               f'atrim=end_sample={count},asetpts=PTS-STARTPTS[bed]']
    labels = ['[bed]']
    for index, track in enumerate(tracks):
        channels = ('pan=stereo|c0=c0|c1=c0' if track['source_channels'] == 1
                    else 'aformat=channel_layouts=stereo')
        # Every delay/trim/pad boundary is an integer sample count. Resampling
        # has no async clock compensation or speed change. The final-grid end
        # follows floor(absolute_end*rate), not accumulated rounded durations.
        filters.append(f'[{index}:a:0]aresample={SAMPLE_RATE}:async=0:first_pts=0:dither_method=none,'
            f'{channels},apad=whole_len={track["delivery_sample_count"]},'
            f'atrim=start_sample=0:end_sample={track["delivery_sample_count"]},'
            f'asetpts=PTS-STARTPTS,adelay={track["delivery_start_sample"]}S:all=1[voice{index}]')
        labels.append(f'[voice{index}]')
    if tracks:
        filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=longest:'
            f'dropout_transition=0:normalize=0,apad=whole_len={count},'
            f'atrim=end_sample={count},asetpts=PTS-STARTPTS[delivery]')
    else:
        filters.append('[bed]anull[delivery]')
    return ';'.join(filters)


@dataclass(frozen=True)
class FilmAudioMix:
    data: bytes
    receipt: dict


def mix_film_audio(files, *, expected_manifest_sha256, scratch_dir, ffmpeg='ffmpeg', timeout_seconds=120):
    """Actually mix verified narration; return WAV bytes and a sealed receipt.

    The caller provides an existing scratch directory. All subprocess inputs
    and output live in a unique temporary child and are removed on every exit;
    no caller-owned output path is accepted or overwritten. The receipt proves
    audio processing only, not scene execution or an accepted video.
    """
    _require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 1200,
             'hyperframes_film_audio_timeout_invalid')
    # Freeze the resource map before independently verifying its external hash.
    _require(type(files) is dict, 'hyperframes_film_audio_package_invalid')
    frozen = dict(files)
    manifest = verify_film_project(frozen, expected_manifest_sha256=expected_manifest_sha256)
    count, tracks = _schedule(manifest)
    root = Path(scratch_dir)
    _require(root.is_absolute() and root.is_dir() and not root.is_symlink()
        and root.resolve() == root, 'hyperframes_film_audio_scratch_invalid')
    _require(type(ffmpeg) is str and ffmpeg.strip(), 'hyperframes_film_audio_binary_invalid')
    selected = shutil.which(ffmpeg)
    _require(selected is not None, 'hyperframes_film_audio_process_unavailable', 503)
    binary = Path(selected).resolve(strict=True)
    _require(binary.is_file(), 'hyperframes_film_audio_process_unavailable', 503)
    binary_sha256 = _file_sha(binary)
    deadline = time.monotonic() + timeout_seconds
    version = _run([str(binary), '-version'], cwd=root, deadline=deadline).stdout
    _require(0 < len(version) <= 65536 and version.startswith(b'ffmpeg version '),
             'hyperframes_film_audio_binary_invalid', 502)
    graph = _filters(tracks, count)
    with tempfile.TemporaryDirectory(prefix='hf-audio-', dir=root) as temporary:
        work = Path(temporary)
        command = [str(binary), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
            '-threads', '1', '-filter_complex_threads', '1']
        for index, track in enumerate(tracks):
            name = f'input-{index:03d}.wav'
            raw = frozen[track['path']]
            (work / name).write_bytes(raw)
            command.extend(['-protocol_whitelist', 'file,pipe', '-f', 'wav', '-i', name])
        command.extend(['-filter_complex', graph, '-map', '[delivery]', '-vn', '-sn', '-dn',
            '-map_metadata', '-1', '-ar', str(SAMPLE_RATE), '-ac', str(CHANNELS),
            '-c:a', 'pcm_s16le', '-fflags', '+bitexact', '-flags:a', '+bitexact',
            '-f', 'wav', 'delivery.wav'])
        _run(command, cwd=work, deadline=deadline)
        output = work / 'delivery.wav'
        _require(output.is_file() and not output.is_symlink()
            and 44 <= output.stat().st_size <= MAX_OUTPUT_BYTES,
            'hyperframes_film_audio_output_invalid', 502)
        raw = output.read_bytes()
        info = inspect_wav(raw, max_bytes=MAX_OUTPUT_BYTES, max_duration_seconds=MAX_SECONDS)
        _require((info.sample_rate, info.channels, info.sample_width, info.frame_count)
            == (SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH, count), 'hyperframes_film_audio_timing_mismatch', 502)
        for index, track in enumerate(tracks):
            _require(_sha((work / f'input-{index:03d}.wav').read_bytes()) == track['sha256'],
                     'hyperframes_film_audio_input_changed', 409)
        _require(_file_sha(binary) == binary_sha256, 'hyperframes_film_audio_binary_changed', 409)
    receipt = dict(schema=SCHEMA, status='mixed',
        evidence_scope='actual_audio_mix_only_not_scene_or_hyperframes_video_execution',
        film_manifest_sha256=expected_manifest_sha256, film_sha256=manifest['film_sha256'],
        clock=dict(fps=FPS, frame_count=manifest['clock']['frame_count'],
            sample_boundary_policy='floor_each_absolute_rational_boundary', sample_rate=SAMPLE_RATE,
            sample_count=count), narration_tracks=tracks, contains_narration=bool(tracks),
        sound_effects_mixed=False,
        conversion_policy='delivery_only_48000_pcm_original_wav_bytes_unchanged_no_speed_change',
        gain_policy='unity_without_normalization_mono_duplicated_stereo_preserved',
        ffmpeg=dict(executable_name=binary.name, executable_sha256=binary_sha256,
            version_line=version.decode('utf-8', errors='replace').splitlines()[0],
            version_output_sha256=_sha(version)),
        filter_graph=graph, filter_graph_sha256=_sha(graph.encode('utf-8')),
        output=dict(format='wav-pcm_s16le', sample_rate=info.sample_rate, channels=info.channels,
            sample_width=info.sample_width, sample_count=info.frame_count, bytes=len(raw), sha256=_sha(raw)))
    receipt['sha256'] = digest(receipt)
    return FilmAudioMix(raw, receipt)
