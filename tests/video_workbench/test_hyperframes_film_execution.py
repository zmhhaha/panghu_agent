"""PVC/Job custody and recovery fixtures; no actual Kubernetes or rendering."""
from copy import deepcopy
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import hyperframes_film_execution as E
from video_workbench.hyperframes_film_export import FilmExport
from video_workbench.hyperframes_film_snapshot import unpack_film_archive
from test_hyperframes_film_snapshot import snapshot_fixture


def arguments(snapshot, release):
    return dict(expected_snapshot_sha256=snapshot['sha256'], **release)


def fixture_export(snapshot):
    # Nonmedia fixture; tests custody and commit-last recovery only. This is
    # intentionally not called actual decoder or HyperFrames execution proof.
    data = b'fixture-not-media'
    files = unpack_film_archive(base64.b64decode(snapshot['archive_base64']))
    project = json.loads(files['project/manifest.json'])
    count = snapshot['clock']['frame_count']
    clock = dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000)
    samples, tracks = E._schedule(project)
    graph = E._filters(tracks, samples)
    audio = dict(schema='video.hyperframes-film-audio-mix.v1', status='mixed',
        film_manifest_sha256=snapshot['project_manifest_sha256'], film_sha256=snapshot['film_sha256'],
        clock={**clock, 'sample_boundary_policy': 'floor_each_absolute_rational_boundary'},
        narration_tracks=tracks, contains_narration=bool(tracks), sound_effects_mixed=False,
        conversion_policy='delivery_only_48000_pcm_original_wav_bytes_unchanged_no_speed_change',
        gain_policy='unity_without_normalization_mono_duplicated_stereo_preserved',
        filter_graph=graph, filter_graph_sha256=E.sha(graph.encode()),
        output=dict(format='wav-pcm_s16le', sample_rate=48000, channels=2,
            sample_width=2, sample_count=samples, bytes=samples * 4 + 44, sha256='a' * 64))
    audio['sha256'] = digest(audio)
    frames = [dict(frame=i, path=f'frame-{i:06d}.png', bytes=100, sha256=E.sha(str(i).encode()))
              for i in range(count)]
    observations = [dict(requested_frame=frame, completed_frame=frame, actual_seconds=frame / 24,
        manifest_sha256=snapshot['project_manifest_sha256'], film_sha256=snapshot['film_sha256'],
        core_seek_count=ordinal + 1, dispatch_count=ordinal + 1, frame_sha256=frames[frame]['sha256'])
        for ordinal, frame in enumerate([*range(count), count - 1, 0, count // 2])]
    capture = dict(schema='video.hyperframes-film-frame-capture.v1',
        bundle_sha256=snapshot['bundle_sha256'], project_manifest_sha256=snapshot['project_manifest_sha256'],
        film_sha256=snapshot['film_sha256'], fps=24, frame_count=count,
        width=snapshot['width'], height=snapshot['height'], frames=frames, frame_bytes=count * 100,
        probe=dict(schema='video.hyperframes-film-technical-probe.v1',
            bundle_sha256=snapshot['bundle_sha256'],
            core_sha256=snapshot['runtime_fingerprint']['hyperframes_core_sha256'],
            errors=[], blocked=[], service_worker_accessor_disabled=True,
            cleanup=dict(remaining_iframes=0, pending_cleanup_modules=0), observations=observations))
    receipt = dict(schema='video.hyperframes-film-offline-export.v1', status='exported',
        bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
        project_manifest_sha256=snapshot['project_manifest_sha256'], clock=clock,
        delivery_wav_sha256=audio['output']['sha256'], sound_effects_mixed=False,
        media=dict(sha256=E.sha(data), bytes=len(data), full_decode_passed=True,
            format='mp4', video='h264', audio='aac', width=snapshot['width'],
            height=snapshot['height'], starts_at_zero=True),
        capture_sha256=digest(capture), audio_mix_sha256=audio['sha256'], concatenated_scene_videos=False)
    receipt['sha256'] = digest(receipt)
    return FilmExport(data=data, receipt=receipt, capture=capture, audio_mix=audio)


def test_stage_then_read_preserves_multi_file_bytes_and_idempotent_identity(tmp_path):
    snapshot, files, release = snapshot_fixture()
    result = E.stage_film_execution_snapshot(tmp_path.resolve(), snapshot, **arguments(snapshot, release))
    assert result.name == snapshot['sha256']
    assert {path.name for path in result.iterdir()} == E.STAGED_FILES
    descriptor = json.loads((result / 'snapshot.json').read_bytes())
    assert 'archive_base64' not in descriptor  # No second 64MiB archive copy.
    recovered, restored = E.read_staged_film_snapshot(result, **arguments(snapshot, release))
    assert recovered == snapshot and restored == files
    before = {path.name: path.read_bytes() for path in result.iterdir()}
    assert E.stage_film_execution_snapshot(tmp_path.resolve(), snapshot, **arguments(snapshot, release)) == result
    assert before == {path.name: path.read_bytes() for path in result.iterdir()}
    assert not list(tmp_path.glob('.film-stage-*'))


def test_corrupt_existing_stage_is_rejected_and_never_repaired(tmp_path):
    snapshot, _, release = snapshot_fixture()
    root = E.stage_film_execution_snapshot(tmp_path.resolve(), snapshot, **arguments(snapshot, release))
    target = root / 'input.tar.gz'
    target.chmod(0o600)
    target.write_bytes(b'corrupt existing input')
    with pytest.raises(ServiceError, match='snapshot_changed'):
        E.stage_film_execution_snapshot(tmp_path.resolve(), snapshot, **arguments(snapshot, release))
    assert target.read_bytes() == b'corrupt existing input'


def test_stage_rejects_extra_files_without_deleting_them(tmp_path):
    snapshot, _, release = snapshot_fixture()
    root = E.stage_film_execution_snapshot(tmp_path.resolve(), snapshot, **arguments(snapshot, release))
    (root / 'unrelated.txt').write_bytes(b'keep')
    with pytest.raises(ServiceError, match='inventory_invalid'):
        E.read_staged_film_snapshot(root, **arguments(snapshot, release))
    assert (root / 'unrelated.txt').read_bytes() == b'keep'


def test_staged_symlink_is_rejected(tmp_path):
    snapshot, _, release = snapshot_fixture()
    root = E.stage_film_execution_snapshot(tmp_path.resolve(), snapshot, **arguments(snapshot, release))
    original = root / 'snapshot.json'
    copied = tmp_path / 'outside.json'
    copied.write_bytes(original.read_bytes())
    original.chmod(0o600)
    original.unlink()
    try:
        original.symlink_to(copied)
    except OSError:
        pytest.skip('Symlink privilege unavailable on Windows; nothing installed.')
    with pytest.raises(ServiceError, match='file_invalid'):
        E.read_staged_film_snapshot(root, **arguments(snapshot, release))


def test_staged_hardlink_is_rejected_independently_of_symlink_privilege(tmp_path):
    snapshot, _, release = snapshot_fixture()
    root = E.stage_film_execution_snapshot(tmp_path.resolve(), snapshot, **arguments(snapshot, release))
    original = root / 'snapshot.json'
    copied = tmp_path / 'outside.json'
    copied.write_bytes(original.read_bytes())
    original.chmod(0o600)
    original.unlink()
    os.link(copied, original)
    with pytest.raises(ServiceError, match='file_invalid'):
        E.read_staged_film_snapshot(root, **arguments(snapshot, release))


def test_job_resources_are_stable_pvc_bound_and_isolated():
    snapshot, _, release = snapshot_fixture()
    options = dict(namespace='video-motion-experiment', pvc_name='video-motion-data',
        operation_key='e' * 64, release_id='source-revision-1', **arguments(snapshot, release))
    first = E.film_job_resources(snapshot, **options)
    assert first == E.film_job_resources(snapshot, **options)
    job, policy = first['job'], first['network_policy']
    pod = job['spec']['template']['spec']
    assert not pod['automountServiceAccountToken']
    assert not pod['enableServiceLinks']
    assert pod['restartPolicy'] == 'Never' and job['spec']['backoffLimit'] == 0
    assert pod['securityContext']['fsGroup'] == 10001
    assert job['spec']['activeDeadlineSeconds'] == snapshot['timeout_seconds'] + 60
    assert policy['spec']['ingress'] == policy['spec']['egress'] == []
    assert policy['spec']['podSelector']['matchLabels'] == {'app': 'video-hyperframes-film-renderer'}
    assert not any('configMap' in volume for volume in pod['volumes'])
    renderer = pod['containers'][0]
    assert renderer['image'] == release['image']
    assert renderer['command'] == ['python3', '-m', 'video_workbench.hyperframes_film_execution']
    assert renderer['securityContext']['readOnlyRootFilesystem']
    assert renderer['securityContext']['runAsUser'] == 10001
    mount = {row['mountPath']: row for row in renderer['volumeMounts']}
    assert mount['/input']['readOnly']
    assert mount['/input']['subPath'] == E.INPUT_PREFIX + '/' + snapshot['sha256']
    assert mount['/output']['subPath'] == E.OUTPUT_PREFIX + '/' + options['operation_key']
    assert first['evidence_scope'] == 'resources_not_submitted_or_cancelled'


def test_job_rejects_paths_and_mutated_snapshot_from_caller():
    snapshot, _, release = snapshot_fixture()
    options = dict(namespace='video-motion-experiment', pvc_name='../escape',
        operation_key='e' * 64, release_id='source-revision-1', **arguments(snapshot, release))
    with pytest.raises(ServiceError, match='identity_invalid'):
        E.film_job_resources(snapshot, **options)
    options['pvc_name'] = 'video-motion-data'
    modified = deepcopy(snapshot)
    modified['timeout_seconds'] = 2000
    with pytest.raises(ServiceError, match='snapshot_changed'):
        E.film_job_resources(modified, **options)


def test_complete_result_is_recovered_without_rerender_and_original_inputs_stay(tmp_path):
    snapshot, bundle, release = snapshot_fixture()
    inputs, output, scratch = [tmp_path / name for name in ('inputs', 'output', 'scratch')]
    for path in (inputs, output, scratch):
        path.mkdir()
    staged = E.stage_film_execution_snapshot(inputs.resolve(), snapshot, **arguments(snapshot, release))
    calls = []
    def export(files, **kwargs):
        calls.append(kwargs)
        assert files == bundle
        assert kwargs['expected_bundle_sha256'] == snapshot['bundle_sha256']
        return fixture_export(snapshot)
    result = E.run_staged_film(staged, output.resolve(), scratch.resolve(),
        exporter=export, **arguments(snapshot, release))
    assert len(calls) == 1 and result['snapshot_sha256'] == snapshot['sha256']
    assert {path.name for path in output.iterdir()} == E.RESULT_FILES | {'completed.json'}
    assert E.run_staged_film(staged, output.resolve(), scratch.resolve(),
        exporter=lambda *args, **kwargs: pytest.fail('Completed exact output must not be rendered again'),
        **arguments(snapshot, release)) == result
    assert E.read_staged_film_snapshot(staged, **arguments(snapshot, release))[1] == bundle


def test_incomplete_or_changed_output_is_not_restarted_or_adopted(tmp_path):
    snapshot, _, release = snapshot_fixture()
    inputs, output, scratch = [tmp_path / name for name in ('inputs', 'output', 'scratch')]
    for path in (inputs, output, scratch):
        path.mkdir()
    staged = E.stage_film_execution_snapshot(inputs.resolve(), snapshot, **arguments(snapshot, release))
    sentinel = output / 'video.mp4'
    sentinel.write_bytes(b'partial or caller-owned')
    with pytest.raises(ServiceError, match='result_incomplete'):
        E.run_staged_film(staged, output.resolve(), scratch.resolve(),
            exporter=lambda *args, **kwargs: pytest.fail('Do not duplicate uncertain execution'),
            **arguments(snapshot, release))
    assert sentinel.read_bytes() == b'partial or caller-owned'


def test_cancellation_does_not_publish_a_partial_result(tmp_path):
    snapshot, _, release = snapshot_fixture()
    inputs, output, scratch = [tmp_path / name for name in ('inputs', 'output', 'scratch')]
    for path in (inputs, output, scratch):
        path.mkdir()
    staged = E.stage_film_execution_snapshot(inputs.resolve(), snapshot, **arguments(snapshot, release))
    def cancelled(*args, **kwargs):
        raise ServiceError('media_render_cancelled', 409)
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        E.run_staged_film(staged, output.resolve(), scratch.resolve(),
            exporter=cancelled, **arguments(snapshot, release))
    assert not list(output.iterdir()) and not list(scratch.iterdir())


def test_changed_completed_media_is_rejected(tmp_path):
    snapshot, _, release = snapshot_fixture()
    inputs, output, scratch = [tmp_path / name for name in ('inputs', 'output', 'scratch')]
    for path in (inputs, output, scratch):
        path.mkdir()
    staged = E.stage_film_execution_snapshot(inputs.resolve(), snapshot, **arguments(snapshot, release))
    E.run_staged_film(staged, output.resolve(), scratch.resolve(),
        exporter=lambda *args, **kwargs: fixture_export(snapshot), **arguments(snapshot, release))
    (output / 'video.mp4').write_bytes(b'different')
    with pytest.raises(ServiceError, match='result_changed'):
        E.verify_completed_film_output(output, snapshot=snapshot, **arguments(snapshot, release))


@pytest.mark.parametrize('tamper', ['film', 'project', 'bundle', 'clock', 'original_audio',
    'audio_schedule', 'capture_identity', 'capture_frame', 'capture_probe'])
def test_resealed_output_cannot_replace_trusted_film_identity(tmp_path, tamper):
    snapshot, _, release = snapshot_fixture()
    inputs, output, scratch = [tmp_path / name for name in ('inputs', 'output', 'scratch')]
    for path in (inputs, output, scratch):
        path.mkdir()
    staged = E.stage_film_execution_snapshot(inputs.resolve(), snapshot, **arguments(snapshot, release))
    E.run_staged_film(staged, output.resolve(), scratch.resolve(),
        exporter=lambda *args, **kwargs: fixture_export(snapshot), **arguments(snapshot, release))
    result = json.loads((output / 'completed.json').read_bytes())
    receipt = json.loads((output / 'export-receipt.json').read_bytes())
    capture = json.loads((output / 'capture-receipt.json').read_bytes())
    audio = json.loads((output / 'audio-mix-receipt.json').read_bytes())
    wrong = 'b' * 64
    if tamper == 'film':
        for document in (result, receipt, capture, audio):
            document['film_sha256'] = wrong
        for row in capture['probe']['observations']:
            row['film_sha256'] = wrong
    elif tamper == 'project':
        receipt['project_manifest_sha256'] = capture['project_manifest_sha256'] = wrong
        audio['film_manifest_sha256'] = wrong
        for row in capture['probe']['observations']:
            row['manifest_sha256'] = wrong
    elif tamper == 'bundle':
        result['bundle_sha256'] = receipt['bundle_sha256'] = capture['bundle_sha256'] = wrong
        capture['probe']['bundle_sha256'] = wrong
    elif tamper == 'clock':
        receipt['clock']['sample_count'] += 2000
        audio['clock']['sample_count'] += 2000
    elif tamper == 'original_audio':
        audio['narration_tracks'][0]['sha256'] = wrong
    elif tamper == 'audio_schedule':
        audio['narration_tracks'][0]['delivery_start_sample'] += 1
        graph = E._filters(audio['narration_tracks'], audio['clock']['sample_count'])
        audio['filter_graph'], audio['filter_graph_sha256'] = graph, E.sha(graph.encode())
    elif tamper == 'capture_identity':
        capture['probe']['core_sha256'] = wrong
    elif tamper == 'capture_frame':
        capture['frames'][0]['path'] = 'some-other-frame.png'
    else:
        capture['probe']['observations'][0]['actual_seconds'] = 1 / 24
    # All internal checksums are deliberately resealed; external frozen inputs
    # must still reject a self-consistent replacement or changed timing claim.
    audio['sha256'] = digest({key: value for key, value in audio.items() if key != 'sha256'})
    receipt['capture_sha256'] = digest(capture)
    receipt['audio_mix_sha256'] = audio['sha256']
    receipt['sha256'] = digest({key: value for key, value in receipt.items() if key != 'sha256'})
    for name, document in [('export-receipt.json', receipt), ('capture-receipt.json', capture),
                           ('audio-mix-receipt.json', audio)]:
        raw = canonical_bytes(document)
        (output / name).write_bytes(raw)
        result['artifacts'][name] = dict(bytes=len(raw), sha256=E.sha(raw))
    result['sha256'] = digest({key: value for key, value in result.items() if key != 'sha256'})
    (output / 'completed.json').write_bytes(canonical_bytes(result))
    with pytest.raises(ServiceError, match='hyperframes_film_result_'):
        E.verify_completed_film_output(output, snapshot=snapshot, **arguments(snapshot, release))


def test_malformed_export_receipt_is_refused_before_any_output_is_published(tmp_path):
    snapshot, _, release = snapshot_fixture()
    inputs, output, scratch = [tmp_path / name for name in ('inputs', 'output', 'scratch')]
    for path in (inputs, output, scratch):
        path.mkdir()
    staged = E.stage_film_execution_snapshot(inputs.resolve(), snapshot, **arguments(snapshot, release))
    export = fixture_export(snapshot)
    export.capture.pop('probe')
    with pytest.raises(ServiceError, match='hyperframes_film_result_invalid'):
        E.run_staged_film(staged, output.resolve(), scratch.resolve(),
            exporter=lambda *args, **kwargs: export, **arguments(snapshot, release))
    assert list(output.iterdir()) == []


def test_cli_passes_an_explicit_playwright_esm_file_not_a_directory(monkeypatch, capsys):
    snapshot, _, release = snapshot_fixture()
    monkeypatch.setattr(E, '_run', lambda *args, **kwargs: canonical_bytes({
        key: value for key, value in release['runtime_fingerprint'].items() if key != 'image_digest'}))
    monkeypatch.setattr(E.signal, 'signal', lambda *args: None)
    captured = {}
    def run(*paths, **options):
        captured.update(paths=paths, **options)
        return {'snapshot_sha256': snapshot['sha256'], 'sha256': 'a' * 64}
    monkeypatch.setattr(E, 'run_staged_film', run)
    assert E.main(['--expected-snapshot-sha256', snapshot['sha256'], '--image', release['image']]) == 0
    assert captured['paths'] == ('/input', '/output', '/work')
    assert captured['playwright_module'] == '/opt/renderer/node_modules/playwright-core/index.mjs'
    assert captured['chromium_path'] == '/opt/browser/headless_shell'
    assert captured['runtime_fingerprint'] == release['runtime_fingerprint']
    assert json.loads(capsys.readouterr().out)['status'] == 'completed'


def test_real_node_dynamic_import_requires_package_file_instead_of_absolute_directory(tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Existing Node is required; nothing is downloaded.')
    package = tmp_path / 'node_modules/playwright-core'
    package.mkdir(parents=True)
    (package / 'package.json').write_text('{"type":"module","exports":"./index.mjs"}', encoding='utf-8')
    (package / 'index.mjs').write_text('export const chromium={fixture:true};', encoding='utf-8')
    # File URLs make the absolute import experiment portable to Windows; the
    # same Node ESM resolver rejects an absolute directory on Linux too.
    script = '''
const directory=process.argv[1],entry=process.argv[2];
let code;try{await import(directory);}catch(error){code=error.code;}
if(code!=='ERR_UNSUPPORTED_DIR_IMPORT')throw Error('directory was not rejected: '+code);
const {chromium}=await import(entry);
if(chromium.fixture!==true)throw Error('explicit ESM entry failed');
process.stdout.write(JSON.stringify({directory_error:code,file_imported:true}));
'''
    result = subprocess.run([node, '--input-type=module', '-e', script,
        package.as_uri(), (package / 'index.mjs').as_uri()],
        capture_output=True, check=True, timeout=15)
    assert json.loads(result.stdout) == {'directory_error': 'ERR_UNSUPPORTED_DIR_IMPORT', 'file_imported': True}
