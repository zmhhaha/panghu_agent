"""PVC staging and an isolated one-project Job, not another execution service.

The existing owner-scoped ExecutionJournal must persist intent before create,
recover the same named Job after an uncertain response, and await Pod deletion
before cancellation is terminal. Generating these resources proves none of it.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import signal
import stat
import tempfile
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .hyperframes_film_browser import HOST_NAMES
from .hyperframes_film_audio import _schedule, _filters
from .hyperframes_film_export import export_film_browser_bundle, MAX_MEDIA_BYTES, _run
from .hyperframes_film_snapshot import (
    MAX_ARCHIVE_BYTES, MAX_METADATA_BYTES, decode_film_execution_snapshot, require, sha,
)

STAGED_FILES = frozenset({'snapshot.json', 'input.tar.gz'})
INPUT_PREFIX = 'hyperframes-films/inputs'
OUTPUT_PREFIX = 'hyperframes-films/results'
LABEL = 'video-workbench.panghuer.top/'
RESULT_FILES = frozenset({'video.mp4', 'export-receipt.json', 'capture-receipt.json', 'audio-mix-receipt.json'})
MAX_RESULT_JSON_BYTES = 16 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_KUBE = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')


def _link(path):
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _directory(path):
    path = Path(path)
    require(path.is_absolute() and path.is_dir() and not _link(path)
        and path.resolve(strict=True) == path
        and not any(_link(parent) for parent in path.parents),
        'hyperframes_film_execution_directory_invalid')
    return path


def _ordinary(path, limit):
    require(path.is_file() and not _link(path), 'hyperframes_film_execution_file_invalid')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= limit,
                'hyperframes_film_execution_file_invalid')
        raw = stream.read(limit + 1)
        require(len(raw) == info.st_size, 'hyperframes_film_execution_file_changed', 409)
    return raw


def read_staged_film_snapshot(directory, *, expected_snapshot_sha256, **release):
    root = _directory(directory)
    require({path.name for path in root.iterdir()} == STAGED_FILES,
            'hyperframes_film_staged_inventory_invalid')
    raw = _ordinary(root / 'snapshot.json', MAX_METADATA_BYTES)
    try:
        snapshot = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ServiceError('hyperframes_film_staged_metadata_invalid', 422) from None
    require(type(snapshot) is dict and raw == canonical_bytes(snapshot)
        and 'archive_base64' not in snapshot, 'hyperframes_film_staged_metadata_invalid')
    snapshot['archive_base64'] = base64.b64encode(
        _ordinary(root / 'input.tar.gz', MAX_ARCHIVE_BYTES)).decode('ascii')
    files = decode_film_execution_snapshot(snapshot,
        expected_snapshot_sha256=expected_snapshot_sha256, **release)
    return snapshot, files


def stage_film_execution_snapshot(root, snapshot, *, expected_snapshot_sha256, **release):
    """Write one content-addressed immutable input, atomically and idempotently.

    Root is the execution service's trusted PVC input directory, not a caller
    path. Existing inputs are independently verified and never overwritten.
    """
    root = _directory(root)
    decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=expected_snapshot_sha256, **release)
    destination = root / expected_snapshot_sha256
    if destination.exists() or _link(destination):
        existing, _ = read_staged_film_snapshot(destination,
            expected_snapshot_sha256=expected_snapshot_sha256, **release)
        require(existing == snapshot, 'hyperframes_film_stage_conflict', 409)
        return destination
    temporary = Path(tempfile.mkdtemp(prefix='.film-stage-', dir=root))
    try:
        descriptor = {key: value for key, value in snapshot.items() if key != 'archive_base64'}
        inputs = {'snapshot.json': canonical_bytes(descriptor),
                  'input.tar.gz': base64.b64decode(snapshot['archive_base64'], validate=True)}
        for name, raw in inputs.items():
            with (temporary / name).open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            (temporary / name).chmod(0o444)
        read_staged_film_snapshot(temporary,
            expected_snapshot_sha256=expected_snapshot_sha256, **release)
        try:
            temporary.rename(destination)
        except OSError:
            # A concurrent stager may have won; only exact verified identity is
            # a successful replay. A corrupt destination is never repaired here.
            if not destination.exists():
                raise
            existing, _ = read_staged_film_snapshot(destination,
                expected_snapshot_sha256=expected_snapshot_sha256, **release)
            require(existing == snapshot, 'hyperframes_film_stage_conflict', 409)
        return destination
    finally:
        if temporary.exists():
            for path in temporary.iterdir():
                require(path.name in STAGED_FILES and not _link(path),
                        'hyperframes_film_stage_cleanup_invalid')
                path.chmod(0o600)
                path.unlink()
            temporary.rmdir()


def film_job_resources(snapshot, *, expected_snapshot_sha256, runtime_fingerprint,
        trusted_host_sha256, image, namespace, pvc_name, operation_key, release_id):
    """Use exact PVC subPaths; no ConfigMap, URL, shell or caller command."""
    decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=expected_snapshot_sha256,
        runtime_fingerprint=runtime_fingerprint, trusted_host_sha256=trusted_host_sha256, image=image)
    require(all(type(value) is str and _KUBE.fullmatch(value) for value in (namespace, pvc_name))
        and type(operation_key) is str and _SHA.fullmatch(operation_key)
        and type(release_id) is str and re.fullmatch(r'[A-Za-z0-9._-]{1,80}', release_id),
        'hyperframes_film_job_identity_invalid')
    name = 'hf-film-' + operation_key[:24] + '-' + expected_snapshot_sha256[:12]
    identity = dict(schema='video.hyperframes-film-job-binding.v1', operation_key=operation_key,
        snapshot_sha256=expected_snapshot_sha256, image=image, release_id=release_id,
        namespace=namespace, pvc_name=pvc_name, name=name)
    binding = digest(identity)
    labels = {'app': 'video-hyperframes-film-renderer', LABEL + 'execution': operation_key[:32]}
    annotations = {LABEL + 'binding': binding, LABEL + 'snapshot-sha256': expected_snapshot_sha256,
        LABEL + 'input-sha256': snapshot['bundle_sha256'], LABEL + 'release-id': release_id}
    security = dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True,
        runAsNonRoot=True, runAsUser=10001, runAsGroup=10001,
        capabilities={'drop': ['ALL']}, seccompProfile={'type': 'RuntimeDefault'})
    input_path = INPUT_PREFIX + '/' + expected_snapshot_sha256
    result_path = OUTPUT_PREFIX + '/' + operation_key
    renderer = dict(name='renderer', image=image, imagePullPolicy='IfNotPresent',
        command=['python3', '-m', 'video_workbench.hyperframes_film_execution'],
        args=['--expected-snapshot-sha256', expected_snapshot_sha256, '--image', image],
        securityContext=security,
        env=[dict(name='PYTHONPATH', value='/app'),
             dict(name='TMPDIR', value='/work'), dict(name='XDG_CACHE_HOME', value='/work/cache'),
             dict(name='XDG_CONFIG_HOME', value='/work/config')],
        resources=dict(requests=dict(cpu='1', memory='1Gi', **{'ephemeral-storage': '512Mi'}),
            limits=dict(cpu='2', memory='2Gi', **{'ephemeral-storage': '2Gi'})),
        volumeMounts=[dict(name='data', mountPath='/input', subPath=input_path, readOnly=True),
            dict(name='data', mountPath='/output', subPath=result_path),
            dict(name='scratch', mountPath='/work'), dict(name='scratch', mountPath='/tmp')])
    pod = dict(restartPolicy='Never', automountServiceAccountToken=False, enableServiceLinks=False,
        securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, fsGroup=10001,
            seccompProfile={'type': 'RuntimeDefault'}),
        nodeSelector={'kubernetes.io/arch': 'arm64'}, containers=[renderer],
        volumes=[dict(name='data', persistentVolumeClaim=dict(claimName=pvc_name)),
            dict(name='scratch', emptyDir=dict(sizeLimit='2Gi'))], terminationGracePeriodSeconds=20)
    job = dict(apiVersion='batch/v1', kind='Job',
        metadata=dict(name=name, namespace=namespace, labels=labels, annotations=annotations),
        spec=dict(backoffLimit=0, activeDeadlineSeconds=snapshot['timeout_seconds'] + 60,
            ttlSecondsAfterFinished=86400,
            template=dict(metadata=dict(labels=deepcopy(labels), annotations=deepcopy(annotations)), spec=pod)))
    policy = dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy',
        metadata=dict(name='hyperframes-films-deny-all', namespace=namespace),
        spec=dict(podSelector=dict(matchLabels={'app': 'video-hyperframes-film-renderer'}),
            policyTypes=['Ingress', 'Egress'], ingress=[], egress=[]))
    return dict(job=job, network_policy=policy, binding=binding, identity=identity,
        input_sub_path=input_path, result_sub_path=result_path,
        evidence_scope='resources_not_submitted_or_cancelled')


def _verify_receipt_bindings(snapshot, project, result, receipt, capture, audio):
    """Bind every chain to authenticated inputs, not just to its own hashes."""
    count = snapshot['clock']['frame_count']
    clock = dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000)
    for document in (result, receipt, capture, audio):
        require(document['film_sha256'] == snapshot['film_sha256'],
                'hyperframes_film_result_film_mismatch', 409)
    require(result['bundle_sha256'] == receipt['bundle_sha256'] == capture['bundle_sha256']
        == snapshot['bundle_sha256']
        and receipt['project_manifest_sha256'] == capture['project_manifest_sha256']
        == audio['film_manifest_sha256'] == snapshot['project_manifest_sha256'],
        'hyperframes_film_result_project_mismatch', 409)
    require(receipt['clock'] == clock and receipt['media']['width'] == snapshot['width']
        and receipt['media']['height'] == snapshot['height']
        and receipt['media']['format'] == 'mp4' and receipt['media']['video'] == 'h264'
        and receipt['media']['audio'] == 'aac' and receipt['media']['starts_at_zero'] is True,
        'hyperframes_film_result_clock_mismatch', 409)
    samples, tracks = _schedule(project)
    graph = _filters(tracks, samples)
    require(audio['schema'] == 'video.hyperframes-film-audio-mix.v1' and audio['status'] == 'mixed'
        and audio['clock'] == {**clock, 'sample_boundary_policy': 'floor_each_absolute_rational_boundary'}
        and audio['narration_tracks'] == tracks and audio['contains_narration'] == bool(tracks)
        and {row['path']: row['sha256'] for row in tracks} == snapshot['recording_sha256']
        and audio['sound_effects_mixed'] is receipt['sound_effects_mixed'] is False
        and audio['conversion_policy'] == 'delivery_only_48000_pcm_original_wav_bytes_unchanged_no_speed_change'
        and audio['gain_policy'] == 'unity_without_normalization_mono_duplicated_stereo_preserved'
        and audio['filter_graph'] == graph and audio['filter_graph_sha256'] == sha(graph.encode())
        and audio['output']['format'] == 'wav-pcm_s16le' and audio['output']['sample_rate'] == 48000
        and audio['output']['channels'] == 2 and audio['output']['sample_width'] == 2
        and audio['output']['sample_count'] == samples
        and receipt['delivery_wav_sha256'] == audio['output']['sha256']
        and _SHA.fullmatch(audio['output']['sha256']),
        'hyperframes_film_result_original_audio_mismatch', 409)
    require(capture['schema'] == 'video.hyperframes-film-frame-capture.v1'
        and capture['frame_count'] == count and capture['fps'] == 24
        and capture['width'] == snapshot['width'] and capture['height'] == snapshot['height']
        and type(capture['frames']) is list and len(capture['frames']) == count,
        'hyperframes_film_result_capture_mismatch', 409)
    frame_bytes = 0
    for index, frame in enumerate(capture['frames']):
        require(type(frame) is dict and frame['frame'] == index
            and frame['path'] == 'frame-' + str(index).zfill(6) + '.png'
            and type(frame['bytes']) is int and 0 < frame['bytes'] <= 12 * 1024 * 1024
            and type(frame['sha256']) is str and _SHA.fullmatch(frame['sha256']),
            'hyperframes_film_result_capture_mismatch', 409)
        frame_bytes += frame['bytes']
    require(capture['frame_bytes'] == frame_bytes <= 512 * 1024 * 1024,
            'hyperframes_film_result_capture_mismatch', 409)
    probe = capture['probe']
    require(probe['schema'] == 'video.hyperframes-film-technical-probe.v1'
        and probe['bundle_sha256'] == snapshot['bundle_sha256']
        and probe['core_sha256'] == snapshot['runtime_fingerprint']['hyperframes_core_sha256']
        and probe['errors'] == probe['blocked'] == []
        and probe['service_worker_accessor_disabled'] is True
        and probe['cleanup']['remaining_iframes'] == probe['cleanup']['pending_cleanup_modules'] == 0
        and type(probe['observations']) is list and len(probe['observations']) == count + 3,
        'hyperframes_film_result_capture_mismatch', 409)
    dispatches = 0
    for ordinal, frame in enumerate([*range(count), count - 1, 0, count // 2]):
        row = probe['observations'][ordinal]
        require(row['requested_frame'] == row['completed_frame'] == frame
            and abs(row['actual_seconds'] - frame / 24) <= 1e-7
            and row['manifest_sha256'] == snapshot['project_manifest_sha256']
            and row['film_sha256'] == snapshot['film_sha256']
            and row['core_seek_count'] == ordinal + 1
            and type(row['dispatch_count']) is int and row['dispatch_count'] > dispatches
            and row['frame_sha256'] == capture['frames'][frame]['sha256'],
            'hyperframes_film_result_capture_mismatch', 409)
        dispatches = row['dispatch_count']


def verify_completed_film_output(directory, *, snapshot, expected_snapshot_sha256, **release):
    """Require trusted complete inputs when recovering an output identity."""
    files = decode_film_execution_snapshot(snapshot,
        expected_snapshot_sha256=expected_snapshot_sha256, **release)
    project = json.loads(files['project/manifest.json'])
    root = _directory(directory)
    require({path.name for path in root.iterdir()} == RESULT_FILES | {'completed.json'},
            'hyperframes_film_result_incomplete', 409)
    raw = _ordinary(root / 'completed.json', MAX_METADATA_BYTES)
    try:
        result = json.loads(raw)
        require(raw == canonical_bytes(result)
            and result['schema'] == 'video.hyperframes-film-job-result.v1'
            and result['snapshot_sha256'] == expected_snapshot_sha256
            and result['sha256'] == digest({key: value for key, value in result.items() if key != 'sha256'})
            and set(result['artifacts']) == RESULT_FILES, 'hyperframes_film_result_binding_mismatch', 409)
        for name, identity in result['artifacts'].items():
            content = _ordinary(root / name, MAX_MEDIA_BYTES if name == 'video.mp4' else MAX_RESULT_JSON_BYTES)
            require(identity == dict(bytes=len(content), sha256=sha(content)),
                    'hyperframes_film_result_changed', 409)
        receipt = json.loads((root / 'export-receipt.json').read_bytes())
        capture = json.loads((root / 'capture-receipt.json').read_bytes())
        audio = json.loads((root / 'audio-mix-receipt.json').read_bytes())
        require(receipt['schema'] == 'video.hyperframes-film-offline-export.v1'
            and receipt['status'] == 'exported'
            and receipt['sha256'] == digest({key: value for key, value in receipt.items() if key != 'sha256'})
            and receipt['media']['sha256'] == result['artifacts']['video.mp4']['sha256']
            and receipt['media']['bytes'] == result['artifacts']['video.mp4']['bytes']
            and receipt['capture_sha256'] == digest(capture)
            and receipt['audio_mix_sha256'] == audio['sha256']
            and audio['sha256'] == digest({key: value for key, value in audio.items() if key != 'sha256'})
            and receipt['bundle_sha256'] == result['bundle_sha256']
            and receipt['film_sha256'] == result['film_sha256']
            and receipt['concatenated_scene_videos'] is False
            and receipt['media']['full_decode_passed'] is True,
            'hyperframes_film_result_binding_mismatch', 409)
        _verify_receipt_bindings(snapshot, project, result, receipt, capture, audio)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ServiceError('hyperframes_film_result_invalid', 422) from None
    return result


def run_staged_film(input_directory, output_directory, scratch_directory, *,
        expected_snapshot_sha256, runtime_fingerprint, trusted_host_sha256, image,
        exporter=export_film_browser_bundle, **export_options):
    """Fixed Job runner. Completion is written last; existing outputs survive."""
    snapshot, files = read_staged_film_snapshot(input_directory,
        expected_snapshot_sha256=expected_snapshot_sha256,
        runtime_fingerprint=runtime_fingerprint, trusted_host_sha256=trusted_host_sha256, image=image)
    output, scratch = _directory(output_directory), _directory(scratch_directory)
    if any(output.iterdir()):
        return verify_completed_film_output(output, snapshot=snapshot,
            expected_snapshot_sha256=expected_snapshot_sha256,
            runtime_fingerprint=runtime_fingerprint, trusted_host_sha256=trusted_host_sha256, image=image)
    exported = exporter(files, expected_bundle_sha256=snapshot['bundle_sha256'],
        scratch_dir=scratch, timeout_seconds=snapshot['timeout_seconds'], **export_options)
    # The Job has a read-only mount; recheck custody anyway before publishing
    # completion so an unexpected storage-side change is never glossed over.
    unchanged, _ = read_staged_film_snapshot(input_directory,
        expected_snapshot_sha256=expected_snapshot_sha256,
        runtime_fingerprint=runtime_fingerprint, trusted_host_sha256=trusted_host_sha256, image=image)
    require(unchanged == snapshot, 'hyperframes_film_execution_input_changed', 409)
    entries = {'video.mp4': exported.data, 'export-receipt.json': canonical_bytes(exported.receipt),
        'capture-receipt.json': canonical_bytes(exported.capture),
        'audio-mix-receipt.json': canonical_bytes(exported.audio_mix)}
    require(0 < len(entries['video.mp4']) <= MAX_MEDIA_BYTES
        and all(0 < len(value) <= MAX_RESULT_JSON_BYTES for name, value in entries.items() if name != 'video.mp4'),
        'hyperframes_film_result_budget', 413)
    result = dict(schema='video.hyperframes-film-job-result.v1', snapshot_sha256=expected_snapshot_sha256,
        bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
        artifacts={name: dict(bytes=len(raw), sha256=sha(raw)) for name, raw in entries.items()},
        evidence_scope='whole_project_export_not_adoption_or_human_approval')
    result['sha256'] = digest(result)
    try:
        _verify_receipt_bindings(snapshot, json.loads(files['project/manifest.json']), result,
            exported.receipt, exported.capture, exported.audio_mix)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ServiceError('hyperframes_film_result_invalid', 422) from None
    for name, raw in {**entries, 'completed.json': canonical_bytes(result)}.items():
        with (output / name).open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return verify_completed_film_output(output, snapshot=snapshot,
        expected_snapshot_sha256=expected_snapshot_sha256,
        runtime_fingerprint=runtime_fingerprint, trusted_host_sha256=trusted_host_sha256, image=image)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run exactly one verified full-film PVC input.')
    parser.add_argument('--expected-snapshot-sha256', required=True)
    parser.add_argument('--image', required=True)
    arguments = parser.parse_args(argv)
    def terminate(signum, frame):
        # export._run catches this exception and kills its owned process group
        # before TemporaryDirectory cleanup. Kubernetes is the final cgroup bound.
        raise ServiceError('media_render_cancelled', 409)
    signal.signal(signal.SIGTERM, terminate)
    # The image supplies these exact files and binaries. Nothing is selected by
    # a model result or a URL; the Job fixes the only mutable argument identities.
    runtime = json.loads(_run(['node', '/opt/renderer/runtime.mjs'],
        cwd=Path('/work'), deadline=time.monotonic() + 30))
    runtime['image_digest'] = arguments.image.rsplit('@', 1)[-1]
    hosts = {name: sha((Path(__file__).parent / 'motion_renderer' / name).read_bytes()) for name in HOST_NAMES}
    result = run_staged_film('/input', '/output', '/work',
        expected_snapshot_sha256=arguments.expected_snapshot_sha256,
        runtime_fingerprint=runtime, trusted_host_sha256=hosts, image=arguments.image,
        playwright_module='/opt/renderer/node_modules/playwright-core/index.mjs',
        chromium_path='/opt/browser/headless_shell',
        hyperframes_dist='/opt/renderer/node_modules/hyperframes/dist')
    print(json.dumps(dict(status='completed', snapshot_sha256=result['snapshot_sha256'],
        result_sha256=result['sha256'])), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
