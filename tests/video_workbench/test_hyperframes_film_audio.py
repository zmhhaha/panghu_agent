"""Audio processing fixtures: real local FFmpeg, no Agent or scene execution."""
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import hyperframes_film_audio as A
from video_workbench import hyperframes_film_project as H
from test_hyperframes_film_project import explicit_clock, inputs


FFMPEG = shutil.which('ffmpeg')


def package(*, rates=(24000, 44100, 48000), channels=(1, 1, 1), widths=(2, 2, 2),
            silent=(), fractional_onset=False):
    value = inputs(rates=rates, channels=channels, widths=widths)
    if fractional_onset:
        for row in value['film']['scenes']:
            row['leading_silence_seconds'] = 1 / 44100
            row['trailing_silence_seconds'] = (row['end_seconds'] - row['start_seconds']
                - row['audio_seconds'] - row['leading_silence_seconds'])
    value = explicit_clock(value, set(silent))
    files = H.compile_film_project(**value)
    checksum = hashlib.sha256(files['manifest.json']).hexdigest()
    return files, checksum, H.verify_film_project(files, expected_manifest_sha256=checksum)


def pcm(raw):
    with wave.open(io.BytesIO(raw), 'rb') as stream:
        return stream.readframes(stream.getnframes())


def run(files, checksum, tmp_path):
    if not FFMPEG:
        pytest.skip('An existing local FFmpeg is required; dependencies are never downloaded.')
    return A.mix_film_audio(files, expected_manifest_sha256=checksum,
        scratch_dir=tmp_path, ffmpeg=FFMPEG)


def test_real_mixed_rate_voice_schedule_preserves_every_input_and_exact_film_length(tmp_path):
    files, checksum, manifest = package(fractional_onset=True)
    original = dict(files)
    sentinel = tmp_path / 'delivery.wav'
    sentinel.write_bytes(b'caller-owned output')
    mixed = run(files, checksum, tmp_path)
    receipt, signal = mixed.receipt, pcm(mixed.data)
    assert files == original
    assert sentinel.read_bytes() == b'caller-owned output'
    assert set(tmp_path.iterdir()) == {sentinel}
    assert receipt['schema'] == A.SCHEMA and receipt['status'] == 'mixed'
    assert receipt['sha256'] == digest({key: value for key, value in receipt.items() if key != 'sha256'})
    assert receipt['film_manifest_sha256'] == checksum
    count = manifest['clock']['frame_count'] * 48000 // 24
    assert receipt['clock']['sample_count'] == receipt['output']['sample_count'] == count
    assert len(signal) == count * 4
    assert receipt['output']['sha256'] == hashlib.sha256(mixed.data).hexdigest()
    assert receipt['contains_narration'] is True and receipt['sound_effects_mixed'] is False
    assert receipt['ffmpeg']['version_line'].startswith('ffmpeg version ')
    assert len(receipt['ffmpeg']['executable_sha256']) == 64
    assert receipt['filter_graph_sha256'] == hashlib.sha256(receipt['filter_graph'].encode()).hexdigest()
    assert 'async=0' in receipt['filter_graph'] and 'normalize=0' in receipt['filter_graph']
    assert 'atempo' not in receipt['filter_graph']
    previous = 0
    for declared, track in zip(manifest['narration_tracks'], receipt['narration_tracks']):
        start = Fraction(**declared['start_time'])
        end = Fraction(**declared['end_time'])
        first, last = int(start * 48000 // 1), int(end * 48000 // 1)
        assert track['delivery_start_sample'] == first and track['delivery_end_sample'] == last
        assert track['delivery_sample_count'] == last - first
        for boundary, sample in (('start', first), ('end', last)):
            error = Fraction(**track[boundary + '_quantization_error_seconds'])
            assert error == Fraction(sample, 48000) - (start if boundary == 'start' else end)
            assert -Fraction(1, 48000) < error <= 0
        assert signal[previous * 4:first * 4] == bytes((first - previous) * 4)
        assert any(signal[(first + 100) * 4:(last - 100) * 4])
        assert track['sha256'] == hashlib.sha256(files[track['path']]).hexdigest()
        assert track['source_sample_count'] == declared['source_sample_count']
        previous = last
    assert signal[previous * 4:] == bytes((count - previous) * 4)


def test_48k_unity_mapping_preserves_full_first_and_last_source_samples(tmp_path):
    files, checksum, _ = package(rates=(48000, 48000, 48000), channels=(1, 2, 1))
    mixed = run(files, checksum, tmp_path)
    signal = pcm(mixed.data)
    for track in mixed.receipt['narration_tracks']:
        original = pcm(files[track['path']])
        expected = (b''.join(original[index:index + 2] * 2 for index in range(0, len(original), 2))
                    if track['source_channels'] == 1 else original)
        assert signal[track['delivery_start_sample'] * 4:track['delivery_end_sample'] * 4] == expected
        assert expected[:4] != bytes(4) and expected[-4:] != bytes(4)


def test_real_mixed_pcm_widths_and_channels_are_delivery_conversion_only(tmp_path):
    files, checksum, _ = package(channels=(1, 2, 1), widths=(1, 2, 3))
    before = dict(files)
    mixed = run(files, checksum, tmp_path)
    assert files == before
    assert [row['source_sample_width'] for row in mixed.receipt['narration_tracks']] == [1, 2, 3]
    assert [row['source_channels'] for row in mixed.receipt['narration_tracks']] == [1, 2, 1]
    assert mixed.receipt['output']['sample_width'] == 2
    assert mixed.receipt['output']['channels'] == 2


@pytest.mark.parametrize('silent', [('scene-1',), ('scene-0', 'scene-1', 'scene-2')],
                         ids=['silent-middle', 'entirely-silent'])
def test_explicit_silence_has_no_invented_recording_or_narration_claim(tmp_path, silent):
    files, checksum, manifest = package(silent=silent)
    mixed = run(files, checksum, tmp_path)
    signal = pcm(mixed.data)
    assert mixed.receipt['contains_narration'] == bool(manifest['narration_tracks'])
    assert {row['scene_id'] for row in mixed.receipt['narration_tracks']}.isdisjoint(silent)
    for row in manifest['scenes']:
        if row['scene_id'] in silent:
            first, last = row['start_frame'] * 2000, row['end_frame'] * 2000
            assert signal[first * 4:last * 4] == bytes((last - first) * 4)
    if not manifest['narration_tracks']:
        assert mixed.receipt['narration_tracks'] == []
        assert 'voice' not in mixed.receipt['filter_graph']
        assert not any(signal)
        assert not any(name.startswith('recordings/') for name in files)


@pytest.mark.parametrize('change', ['manifest', 'recording', 'channels', 'onset'])
def test_exact_package_and_recording_metadata_are_verified_before_any_subprocess(tmp_path, monkeypatch, change):
    files, checksum, _ = package()
    if change == 'manifest':
        checksum = 'a' * 64
    elif change == 'recording':
        raw = files['recordings/000.wav']
        files['recordings/000.wav'] = raw[:-1] + bytes([raw[-1] ^ 1])
    else:
        manifest = json.loads(files['manifest.json'])
        if change == 'channels': manifest['narration_tracks'][0]['source_channels'] = 2
        else: manifest['narration_tracks'][0]['start_time']['numerator'] += 1
        files['manifest.json'] = canonical_bytes(manifest)
        checksum = hashlib.sha256(files['manifest.json']).hexdigest()
    monkeypatch.setattr(A, '_run', lambda *args, **kwargs: pytest.fail('Invalid package must not start FFmpeg.'))
    with pytest.raises(ServiceError): run(files, checksum, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('failure', ['nonzero', 'timeout', 'wrong-samples', 'changed-input'])
def test_subprocess_or_output_failure_never_returns_success_and_cleans_own_scratch(tmp_path, monkeypatch, failure):
    files, checksum, _ = package()
    sentinel = tmp_path / 'delivery.wav'
    sentinel.write_bytes(b'caller-owned')
    original = subprocess.run
    def injected(command, **kwargs):
        if '-version' in command:
            return original(command, **kwargs)
        root = Path(kwargs['cwd'])
        if failure in ('timeout', 'nonzero'):
            (root / 'delivery.wav').write_bytes(b'incomplete subprocess output')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if failure == 'nonzero':
            return subprocess.CompletedProcess(command, 1, b'', b'synthetic bounded failure')
        result = original(command, **kwargs)
        if failure == 'changed-input':
            (root / 'input-000.wav').write_bytes(b'changed')
        else:
            with wave.open(str(root / 'delivery.wav'), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(bytes(400))
        return result
    monkeypatch.setattr(A.subprocess, 'run', injected)
    with pytest.raises(ServiceError): run(files, checksum, tmp_path)
    assert sentinel.read_bytes() == b'caller-owned'
    assert set(tmp_path.iterdir()) == {sentinel}


def test_real_repeated_mix_is_deterministic_for_the_same_package_and_binary(tmp_path):
    files, checksum, _ = package()
    first = run(files, checksum, tmp_path)
    second = run(files, checksum, tmp_path)
    assert first.data == second.data
    assert first.receipt == second.receipt
