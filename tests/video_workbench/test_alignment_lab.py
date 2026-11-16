"""Offline experiment validates bindings without importing model runtimes."""
import hashlib
import json
import os
from types import SimpleNamespace
import wave

import pytest

from agent_service.contracts import ServiceError
from video_workbench import alignment_lab as lab


SOURCE = dict(kind='forced_alignment', provider='example-aligner',
              model='local-test-model', version='test-only-not-real-inference')


def recording(tmp_path, *, seconds=2, rate=8_000, width=2, channels=1):
    path = tmp_path / 'speech.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01' * (int(seconds * rate) * width * channels))
    return path


def rows():
    return [dict(text='请求', start_time=.2, end_time=.7),
            dict(text='返回', start_time=1.1, end_time=1.8)]


def mock_assets(monkeypatch):
    from video_workbench import alignment_assets
    assets = dict(manifest_sha256='b' * 64, file_count=11, total_bytes=123)
    monkeypatch.setattr(alignment_assets, 'verify_assets', lambda _directory: dict(assets))
    return assets


def test_import_binds_actual_wav_and_preserves_backend_time(tmp_path):
    path = recording(tmp_path)
    report = lab.align_recording(path, '请求。返回。', backend_items=rows(), source=SOURCE)
    assert report['recording']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report['recording']['duration_seconds'] == 2
    assert [(r['start'], r['end']) for r in report['alignment']['units']] == [(.2, .7), (1.1, 1.8)]
    assert [r['end'] for r in report['beat_segments']] == [.7, 1.8]
    assert report['execution']['mode'] == 'import'
    assert report['execution']['inference_executed'] is False
    assert report['alignment']['review_status'] == 'pending'
    assert report['human_timing_verified'] is report['human_listening_approved'] is False
    assert report['production_state_modified'] is False


def test_native_provider_timing_keeps_its_actual_provenance(tmp_path):
    source = dict(SOURCE, kind='provider_native')
    report = lab.align_recording(recording(tmp_path), '请求。返回。',
                                 backend_items=rows(), source=source)
    assert report['alignment']['source'] == source
    assert 'method' not in report['alignment']


def test_actual_pronunciation_maps_original_and_spoken_text(tmp_path):
    report = lab.align_recording(recording(tmp_path), 'S D K请求。', source_text='SDK请求。',
        backend_items=[dict(text='S', start_time=.1, end_time=.2),
                       dict(text='D', start_time=.2, end_time=.3),
                       dict(text='K', start_time=.3, end_time=.4),
                       dict(text='请求', start_time=.5, end_time=1)], source=SOURCE)
    assert report['beats']['source_text'] == 'SDK请求。'
    assert report['beats']['spoken_text'] == 'S D K请求。'
    assert report['alignment']['units'][3]['spoken_start'] == 5


@pytest.mark.parametrize('items', [
    [dict(text='请求', start_time=.2, end_time=.7)],
    [dict(text='请求返回', start_time=.2, end_time=3)],
    [dict(text='请求返回', start_time=float('nan'), end_time=1)],
    [dict(text='返回请求', start_time=.2, end_time=1.8)],
])
def test_import_rejects_missing_text_or_invalid_time(tmp_path, items):
    with pytest.raises(ServiceError):
        lab.align_recording(recording(tmp_path), '请求。返回。', backend_items=items, source=SOURCE)


def test_raw_response_is_preserved_without_claiming_structural_success(tmp_path):
    raw = []
    missing = [dict(text='请求', start_time=.2, end_time=.7)]
    with pytest.raises(ServiceError, match='text_mismatch'):
        lab.align_recording(recording(tmp_path), '请求。返回。', backend_items=missing,
                            source=SOURCE, backend_observer=raw.append)
    assert len(raw) == 1
    assert raw[0]['backend_items'] == missing
    assert raw[0]['structural_validation'] == 'not-yet-run'
    assert raw[0]['human_timing_verified'] is False
    assert raw[0]['execution']['inference_executed'] is False


@pytest.mark.parametrize('seconds,width,channels', [(61, 2, 1), (.05, 2, 1), (2, 1, 1), (2, 2, 3)])
def test_unsupported_or_unbounded_wav_is_rejected(tmp_path, seconds, width, channels):
    with pytest.raises(ServiceError):
        lab.inspect_recording(recording(tmp_path, seconds=seconds, width=width, channels=channels))


def test_truncated_and_silent_recordings_are_rejected(tmp_path):
    path = recording(tmp_path)
    data = path.read_bytes()
    path.write_bytes(data[:-20])
    with pytest.raises(ServiceError, match='invalid_wav'):
        lab.inspect_recording(path)
    path.write_bytes(data[:44] + b'\x00' * (len(data) - 44))
    with pytest.raises(ServiceError, match='invalid_wav'):
        lab.inspect_recording(path)
    path.write_bytes(b'not a wav')
    with pytest.raises(ServiceError, match='invalid_wav'):
        lab.inspect_recording(path)


def test_requires_explicit_local_model_before_importing_runtime(tmp_path, monkeypatch):
    imported = []
    monkeypatch.setattr(lab.importlib, 'import_module', lambda name: imported.append(name))
    with pytest.raises(ServiceError, match='local_model_required'):
        lab.align_recording(recording(tmp_path), '请求。返回。')
    with pytest.raises(FileNotFoundError):
        lab.align_recording(tmp_path / 'speech.wav', '请求。返回。',
                            model_path='Qwen/Qwen3-ForcedAligner-0.6B', asset_manifest_sha256='a' * 64)
    assert imported == []


def test_injected_runner_is_never_evidence_of_real_inference(tmp_path, monkeypatch):
    assets = mock_assets(monkeypatch)
    directory = tmp_path / 'model'
    directory.mkdir()
    (directory / 'config.json').write_text('{}')
    calls = []
    def runner(audio, text, model, *, threads):
        calls.append((audio, text, model, threads))
        return rows()
    report = lab.align_recording(recording(tmp_path), '请求。返回。',
        model_path=directory, asset_manifest_sha256='b' * 64, qwen_runner=runner)
    assert calls[0][1:] == ('请求。返回。', directory, 2)
    assert report['execution']['runner_injected'] is True
    assert report['execution']['inference_executed'] is False
    assert report['execution']['sdk_runtime_verified'] is False
    assert report['execution']['adapter_status'] == 'injected-adapter-test'
    assert report['execution']['sdk_source_package_version'] == '0.0.6'
    assert report['execution']['sdk_source_reference_mutable'] is True
    assert report['execution']['model_timestamp_postprocessing'] == 'qwen-fix_timestamp-model-derived'
    assert report['human_timing_verified'] is False
    assert report['alignment']['review_status'] == 'pending'
    assert report['alignment']['source']['kind'] == 'forced_alignment'
    assert report['execution']['model_assets'] == assets
    assert report['alignment']['source']['version'].endswith(';assets-sha256:' + 'b' * 64)


def test_recording_replacement_during_runner_is_detected(tmp_path, monkeypatch):
    mock_assets(monkeypatch)
    path = recording(tmp_path)
    directory = tmp_path / 'model'
    directory.mkdir()
    (directory / 'config.json').write_text('{}')
    def runner(audio, *_args, **_kwargs):
        data = bytearray(audio.read_bytes())
        data[-1] = 3
        audio.write_bytes(data)
        return rows()
    with pytest.raises(ServiceError, match='audio_changed'):
        lab.align_recording(path, '请求。返回。', model_path=directory,
                            asset_manifest_sha256='b' * 64, qwen_runner=runner)


def test_model_identity_mismatch_is_rejected_before_inference(tmp_path, monkeypatch):
    mock_assets(monkeypatch)
    directory = tmp_path / 'model'
    directory.mkdir()
    (directory / 'config.json').write_text('{}')
    with pytest.raises(ServiceError, match='model_identity_mismatch'):
        lab.align_recording(recording(tmp_path), '请求。返回。', model_path=directory,
                            asset_manifest_sha256='a' * 64,
                            qwen_runner=lambda *_a, **_k: pytest.fail('must not run'))


def test_model_replacement_during_runner_is_detected(tmp_path, monkeypatch):
    from video_workbench import alignment_assets
    assets = mock_assets(monkeypatch)
    sequence = iter([assets, dict(assets, manifest_sha256='c' * 64)])
    monkeypatch.setattr(alignment_assets, 'verify_assets', lambda _p: next(sequence))
    directory = tmp_path / 'model'
    directory.mkdir()
    (directory / 'config.json').write_text('{}')
    with pytest.raises(ServiceError, match='model_changed'):
        lab.align_recording(recording(tmp_path), '请求。返回。', model_path=directory,
                            asset_manifest_sha256='b' * 64, qwen_runner=lambda *_a, **_k: rows())


@pytest.mark.parametrize('threads', [0, 5, True, 1.5])
def test_qwen_thread_budget_is_checked_before_running_model(tmp_path, threads):
    directory = tmp_path / 'model'
    directory.mkdir()
    (directory / 'config.json').write_text('{}')
    calls = []
    with pytest.raises(ServiceError, match='threads'):
        lab.align_recording(recording(tmp_path), '请求。返回。', model_path=directory,
                            asset_manifest_sha256='b' * 64, threads=threads,
                            qwen_runner=lambda *_a, **_k: calls.append(True))
    assert calls == []


def test_qwen_sdk_boundary_is_cpu_local_offline_and_restores_environment(tmp_path, monkeypatch):
    directory = tmp_path / 'model'
    directory.mkdir()
    (directory / 'config.json').write_text('{}')
    (directory / 'model.safetensors').write_bytes(b'test-only')
    path = recording(tmp_path)
    calls = []
    class Torch:
        float32 = 'float32'
        def get_num_threads(self): return 8
        def set_num_threads(self, value): calls.append(('threads', value))
        def inference_mode(self): return lab._offline_hub()
    class Aligner:
        @staticmethod
        def from_pretrained(model_path, **kwargs):
            assert os.environ['HF_HUB_OFFLINE'] == '1'
            assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
            calls.append(('load', model_path, kwargs))
            return Aligner()
        def align(self, **kwargs):
            calls.append(('align', kwargs))
            return [SimpleNamespace(items=[SimpleNamespace(**item) for item in rows()])]
    monkeypatch.setenv('HF_HUB_OFFLINE', 'previous')
    monkeypatch.delenv('TRANSFORMERS_OFFLINE', raising=False)
    monkeypatch.setattr(lab.importlib, 'import_module',
                        lambda name: Torch() if name == 'torch' else SimpleNamespace(Qwen3ForcedAligner=Aligner))
    assert lab.qwen_local_items(path, '请求。返回。', directory) == rows()
    assert calls[0] == ('threads', 2)
    assert calls[-1] == ('threads', 8)
    assert calls[1][2]['local_files_only'] is True
    assert calls[1][2]['trust_remote_code'] is False
    assert calls[1][2]['device_map'] == 'cpu'
    assert os.environ['HF_HUB_OFFLINE'] == 'previous'
    assert 'TRANSFORMERS_OFFLINE' not in os.environ


def test_output_is_exclusive_and_cannot_replace_an_input(tmp_path):
    path = recording(tmp_path)
    with pytest.raises(ServiceError, match='output_is_input'):
        lab.write_report(path, {}, input_paths=[path])
    report = tmp_path / 'receipt.json'
    lab.write_report(report, {'human_timing_verified': False})
    with pytest.raises(ServiceError, match='output_exists'):
        lab.write_report(report, {'replacement': True})
    assert json.loads(report.read_text()) == {'human_timing_verified': False}


def test_dangling_output_symlink_does_not_create_its_target(tmp_path):
    missing = tmp_path / 'should-not-be-created.json'
    output = tmp_path / 'receipt.json'
    try:
        output.symlink_to(missing)
    except OSError as error:
        pytest.skip('creating symlinks is not permitted on this platform: ' + str(error))
    assert output.is_symlink() and not output.exists()
    with pytest.raises(ServiceError, match='output_link'):
        lab.write_report(output, {'must_not_write': True})
    assert not missing.exists()
    assert output.is_symlink()


def test_output_hardlink_to_input_is_rejected(tmp_path):
    source = tmp_path / 'input.json'
    source.write_text('{"original":true}')
    alias = tmp_path / 'receipt.json'
    try:
        alias.hardlink_to(source)
    except OSError as error:
        pytest.skip('creating hard links is not permitted on this platform: ' + str(error))
    with pytest.raises(ServiceError, match='output_is_input'):
        lab.write_report(alias, {}, input_paths=[source])
    assert source.read_text() == '{"original":true}'


def test_exclusive_open_rejects_a_leaf_created_after_validation(tmp_path, monkeypatch):
    output = tmp_path / 'receipt.json'
    validate = lab.validate_report_path
    def create_after_validation(path, **kwargs):
        result = validate(path, **kwargs)
        result.write_text('other creator')
        return result
    monkeypatch.setattr(lab, 'validate_report_path', create_after_validation)
    with pytest.raises(FileExistsError):
        lab.write_report(output, {'must_not_replace': True})
    assert output.read_text() == 'other creator'


@pytest.mark.parametrize('parent_is_file', [False, True])
def test_cli_checks_output_parent_before_model_or_input_loading(tmp_path, monkeypatch,
                                                               parent_is_file):
    parent = tmp_path / 'not-a-directory'
    if parent_is_file:
        parent.write_text('ordinary file')
    calls = []
    monkeypatch.setattr(lab, 'align_recording', lambda *_a, **_k: calls.append('model'))
    monkeypatch.setattr(lab, 'read_bounded', lambda *_a, **_k: calls.append('read'))
    with pytest.raises(SystemExit) as failure:
        lab.main(['--audio', str(tmp_path / 'audio.wav'), '--spoken-text', str(tmp_path / 'spoken.txt'),
                  '--model-path', str(tmp_path / 'model'), '--asset-manifest-sha256', 'a' * 64,
                  '--output', str(parent / 'receipt.json')])
    assert failure.value.code == 2
    assert calls == []


def test_cli_rejects_dangling_output_before_inference(tmp_path, monkeypatch):
    output = tmp_path / 'receipt.json'
    missing = tmp_path / 'missing.json'
    try:
        output.symlink_to(missing)
    except OSError as error:
        pytest.skip('creating symlinks is not permitted on this platform: ' + str(error))
    calls = []
    monkeypatch.setattr(lab, 'align_recording', lambda *_a, **_k: calls.append('model'))
    with pytest.raises(SystemExit) as failure:
        lab.main(['--audio', str(tmp_path / 'audio.wav'), '--spoken-text', str(tmp_path / 'spoken.txt'),
                  '--model-path', str(tmp_path / 'model'), '--asset-manifest-sha256', 'a' * 64,
                  '--output', str(output)])
    assert failure.value.code == 2
    assert calls == [] and not missing.exists()


def test_output_junction_detection_is_checked_before_resolving(tmp_path, monkeypatch):
    path_type = type(tmp_path)
    if not hasattr(path_type, 'is_junction'):
        pytest.skip('pathlib junction detection is unavailable on this Python version')
    output = tmp_path / 'receipt.json'
    monkeypatch.setattr(path_type, 'is_junction', lambda path: path == output)
    with pytest.raises(ServiceError, match='output_link'):
        lab.write_report(output, {})
    assert not output.exists()


def test_cli_import_creates_one_complete_report_and_refuses_reuse(tmp_path, capsys):
    path = recording(tmp_path)
    text = tmp_path / 'spoken.txt'
    text.write_text('请求。返回。', encoding='utf-8')
    backend = tmp_path / 'backend.json'
    backend.write_text(json.dumps(rows()), encoding='utf-8')
    output = tmp_path / 'receipt.json'
    argv = ['--audio', str(path), '--spoken-text', str(text), '--backend-json', str(backend),
            '--source-kind', 'forced_alignment', '--provider', 'example', '--model', 'test',
            '--version', 'test-only', '--output', str(output)]
    assert lab.main(argv) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['alignment']['review_status'] == 'pending'
    assert json.loads(capsys.readouterr().out)['mode'] == 'import'
    with pytest.raises(SystemExit) as failure:
        lab.main(argv)
    assert failure.value.code == 2


def test_cli_invalid_backend_never_publishes_report(tmp_path):
    path = recording(tmp_path)
    text = tmp_path / 'spoken.txt'
    text.write_text('请求。返回。', encoding='utf-8')
    backend = tmp_path / 'backend.json'
    backend.write_text('[]', encoding='utf-8')
    output = tmp_path / 'receipt.json'
    with pytest.raises(SystemExit) as failure:
        lab.main(['--audio', str(path), '--spoken-text', str(text), '--backend-json', str(backend),
                  '--source-kind', 'forced_alignment', '--provider', 'example', '--model', 'test',
                  '--version', 'test-only', '--output', str(output)])
    assert failure.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize('invalid_time', [float('nan'), float('inf'), float('-inf')])
def test_cli_preserves_nonfinite_backend_diagnostics_but_refuses_alignment(tmp_path, invalid_time):
    path = recording(tmp_path)
    text = tmp_path / 'spoken.txt'
    text.write_text('请求。返回。', encoding='utf-8')
    backend = tmp_path / 'backend.json'
    items = rows()
    items[0]['end_time'] = invalid_time
    backend.write_text(json.dumps(items), encoding='utf-8')
    raw, output = tmp_path / 'raw.json', tmp_path / 'result.json'
    with pytest.raises(SystemExit) as failure:
        lab.main(['--audio', str(path), '--spoken-text', str(text), '--backend-json', str(backend),
                  '--source-kind', 'forced_alignment', '--provider', 'example', '--model', 'test',
                  '--version', 'test-only', '--output', str(output), '--raw-output', str(raw)])
    assert failure.value.code == 2 and not output.exists()
    diagnostic = json.loads(raw.read_text(encoding='utf-8'),
                            parse_constant=lambda _v: pytest.fail('nonstandard JSON'))
    assert diagnostic['backend_items'][0]['end_time'] == {
        'diagnostic_nonfinite_float': str(invalid_time)}
    assert diagnostic['structural_validation'] == 'not-yet-run'
    assert diagnostic['number_encoding'] == 'strict-json-with-tagged-nonfinite-floats'
    assert diagnostic['human_timing_verified'] is False
