"""Operator-only custody of an already completed offline render; never executes it.

This is deliberately not an HTTP endpoint or a renderer admission path. Imported
media gets a new terminal experiment, candidate and event. Original failed
operations, frozen inputs and renderer ledgers remain untouched.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_reference_learning as protocol
from .picture_learning_store import TERMINAL, _plain, identifier, new_id, owner_id, require
from .video_learning_media import VideoLearningMedia
from .video_learning_store import VideoLearningStore


FILES = {
    'source.json': 2 * 1024 * 1024, 'input.json': 4 * 1024 * 1024,
    'original-binding.json': 4 * 1024 * 1024, 'render-request.json': 256 * 1024,
    'render-receipt.json': 1024 * 1024, 'render-plan.json': 65536,
    'render-outcome.json': 2 * 1024 * 1024, 'output.mp4': 32 * 1024 * 1024,
    'host.mjs': 1024 * 1024, 'original-host.mjs': 1024 * 1024,
}
KIND = 'video.external-offline-import'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _check(value, suffix='invalid'):
    require(value, 'video_learning_import_' + suffix, 409)


def _bytes(path, maximum):
    path = _plain(path)
    _check(path.is_file() and path.stat().st_nlink == 1
        and 0 < path.stat().st_size <= maximum, 'file_invalid')
    raw = path.read_bytes()
    _check(len(raw) <= maximum, 'file_invalid')
    return raw


def _json(raw):
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError('video_learning_import_json_invalid', 409) from None
    _check(type(result) is dict, 'json_invalid')
    return result


def _bundle(directory):
    root = _plain(directory)
    manifest_raw = _bytes(root / 'manifest.json', 65536)
    manifest = _json(manifest_raw)
    _check(manifest.get('schema') == 'video.external-offline-import-bundle.v1'
        and type(manifest.get('origin')) is dict
        and type(manifest.get('files')) is dict and set(manifest['files']) == set(FILES),
        'manifest_invalid')
    data = {}
    for name, maximum in FILES.items():
        data[name] = _bytes(root / name, maximum)
        _check(type(manifest['files'][name]) is str and _SHA.fullmatch(manifest['files'][name])
            and hashlib.sha256(data[name]).hexdigest() == manifest['files'][name], 'file_changed')
    return root, manifest, manifest_raw, data


def _validate(data, origin, source_candidate, operation, run):
    source = _json(data['source.json'])
    source.setdefault('source_sha256', protocol.source_sha256(source))
    protocol.validate_source(source, constraints=operation['input']['constraints'])
    document = protocol.validate_input(_json(data['input.json']))
    binding = _json(data['original-binding.json'])
    request, receipt = (_json(data[name]) for name in ('render-request.json', 'render-receipt.json'))
    plan, outcome = (_json(data[name]) for name in ('render-plan.json', 'render-outcome.json'))
    _check(origin == dict(project_id=run['project_id'], candidate_id=source_candidate['id'],
        experiment_id=run['id'], operation_id=operation['id'],
        input_sha256=operation['input_sha256'], source_sha256=source_candidate['source_sha256']),
        'origin_changed')
    _check(source == source_candidate['source']
        and source['source_sha256'] == protocol.source_sha256(source)
        and document == operation['input'] and digest(document) == operation['input_sha256'],
        'source_or_input_changed')
    _check(binding.get('operation_id') == operation['id']
        and binding.get('document') == document and binding.get('input_sha256') == digest(document)
        and binding.get('source') == {k: source[k] for k in ('engine', 'strategy', 'files')}
        and binding.get('source_sha256') == source['source_sha256']
        and binding.get('learning_source_sha256') == source['source_sha256']
        and type(binding.get('request')) is dict
        and binding.get('request_sha256') == digest(binding['request']), 'binding_changed')
    original = binding['request']
    _check({k: v for k, v in request.items() if k not in {'identity', 'host_sha256'}}
        == {k: v for k, v in original.items() if k not in {'identity', 'host_sha256'}},
        'request_changed')
    file_hashes = {name: hashlib.sha256(text.encode('utf-8')).hexdigest()
        for name, text in source['files'].items()}
    _check(request.get('files_sha256') == file_hashes and request.get('engine') == source['engine']
        and request.get('constraints') == {k: document['constraints'][k]
            for k in ('width', 'height', 'fps', 'duration_seconds')}, 'request_changed')
    _check(plan.get('schema') == 'video.offline-original-render-plan.v1'
        and plan.get('mode') == 'full' and plan.get('original_scene_unchanged') is True
        and plan.get('production_admission_changed') is False
        and plan.get('production_acceptance') is False
        and plan.get('original_source_sha256') == source['source_sha256']
        and plan.get('original_request_sha256') == digest(original)
        and plan.get('request_sha256') == digest(request)
        and plan.get('identity') == request.get('identity'), 'plan_changed')
    original_host, derived_host = (hashlib.sha256(data[name]).hexdigest()
        for name in ('original-host.mjs', 'host.mjs'))
    _check(original.get('host_sha256') == original_host == plan.get('original_host_sha256')
        and request.get('host_sha256') == derived_host == plan.get('derived_host_sha256'), 'host_changed')
    milliseconds = plan.get('screenshot_timeout_seconds')
    _check(type(milliseconds) is int and 10 <= milliseconds <= 600, 'host_changed')
    milliseconds *= 1000
    changes = [
        ['page.setDefaultTimeout(10000)', f'page.setDefaultTimeout({milliseconds})', 1],
        ["page.screenshot({type:'jpeg',quality:80,timeout:10000})",
         f"page.screenshot({{type:'jpeg',quality:80,timeout:{milliseconds}}})", 1],
        ["{waitUntil:'networkidle',timeout:20000}",
         f"{{waitUntil:'networkidle',timeout:{milliseconds}}}", 2],
    ]
    _check(plan.get('host_patch') == changes, 'host_changed')
    expected_host = data['original-host.mjs']
    for before, after, count in changes:
        _check(expected_host.count(before.encode()) == count, 'host_changed')
        expected_host = expected_host.replace(before.encode(), after.encode())
    _check(data['host.mjs'] == expected_host, 'host_changed')
    _check(receipt.get('schema') == 'video.animation-method-render-receipt.v3'
        and receipt.get('status') == 'rendered'
        and receipt.get('request_identity') == request.get('identity')
        and receipt.get('request_sha256') == digest(request)
        and all(receipt.get(k) == request.get(k) for k in
            ('engine', 'host_sha256', 'engine_runtime_sha256', 'project_runtime_sha256', 'image_digest'))
        and receipt.get('decode_verified') is True
        and receipt.get('reverse_seek_verified') is True, 'receipt_changed')
    frames = int(document['constraints']['duration_seconds'] * 24)
    _check(receipt.get('frame_count') == plan.get('expected_frames') == frames
        and receipt.get('output_sha256') == hashlib.sha256(data['output.mp4']).hexdigest()
        and receipt.get('output_bytes') == len(data['output.mp4']), 'media_changed')
    _check(outcome.get('schema') == 'video.offline-original-outcome.v1'
        and outcome.get('mode') == 'full' and outcome.get('status') == 'completed'
        and outcome.get('error') is None
        and outcome.get('cleanup') == 'owner_verified_removed_and_confirmed_absent'
        and outcome.get('original_source_sha256') == source['source_sha256']
        and outcome.get('original_host_sha256') == original_host
        and outcome.get('derived_host_sha256') == derived_host
        and outcome.get('request_sha256') == digest(request)
        and outcome.get('frame_count') == frames and outcome.get('receipt') == receipt,
        'stop_unproven')
    state = outcome.get('container_states', {}).get('before_cleanup', {})
    _check(state.get('Running') is False and state.get('ExitCode') == 0
        and state.get('OOMKilled') is False, 'stop_unproven')
    return source, document, request, receipt, plan


def _decode(path, media, expected):
    """Count actual decoded frames and streams, beyond trusting the supplied receipt."""
    try:
        probe = subprocess.run([media.ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-format_whitelist', 'mov,matroska,webm', '-count_frames', '-show_entries',
            'stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames:format=duration',
            '-of', 'json', str(path)], stdin=subprocess.DEVNULL, capture_output=True,
            timeout=180, check=False)
        _check(probe.returncode == 0 and len(probe.stdout) <= 65536, 'decode_failed')
        result = _json(probe.stdout)
        streams = result.get('streams', [])
        video = [s for s in streams if s.get('codec_type') == 'video']
        _check(len(video) == 1 and video[0].get('codec_name') == 'h264'
            and video[0].get('avg_frame_rate') == '24/1'
            and int(video[0].get('nb_read_frames', -1)) == int(expected['duration_seconds'] * 24),
            'decode_failed')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ServiceError('video_learning_import_decode_failed', 409) from None
    return result


def _result(event):
    return dict(schema='video.external-offline-import-result.v1', **deepcopy(event['result']))


def import_external_render(root, bundle, *, owner, project_id, candidate_id, request_id,
                           media_adapter=None, clock=time.time):
    """Import existing media under a new identity; never author, render, adopt or publish."""
    owner_id(owner)
    for value in (project_id, candidate_id, request_id):
        identifier(value)
    root = _plain(root)
    _check((root / VideoLearningStore.database_name).is_file(), 'store_missing')
    directory, manifest, manifest_raw, data = _bundle(bundle)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    body = dict(project_id=project_id, candidate_id=candidate_id, manifest_sha256=manifest_sha)
    store = VideoLearningStore(root)
    with store.db() as db:
        prior = store.request(owner, KIND, request_id, body, db=db)
        if prior:
            return _result(store.read('operations', owner, prior, db=db))
        project = store.read('projects', owner, project_id, db=db)
        latest = store.read('experiments', owner, project['latest_experiment_id'], db=db)
    _check(latest['state'] in TERMINAL and latest['stop_proven'] is True, 'experiment_active')
    runs = store.list('experiments', owner, project_id=project_id)
    matches = [(run, row) for run in runs for row in run['candidates'] if row['id'] == candidate_id]
    _check(len(matches) == 1, 'candidate_not_found')
    original_run, previous = matches[0]
    _check(previous['work_kind'] == 'reproduction' and original_run['state'] in TERMINAL
        and original_run['stop_proven'] is True, 'stop_unproven')
    origin = manifest['origin']
    operation = store.read('operations', owner, identifier(origin.get('operation_id')))
    _check(operation['id'] in original_run['operation_ids']
        and operation['experiment_id'] == original_run['id'] and operation['stage'] == 'render'
        and operation['status'] == 'failed' and (operation.get('result') or {}).get('stop_proven') is True
        and digest(operation['input']) == operation['input_sha256'], 'origin_changed')
    source, document, request, receipt, plan = _validate(data, origin, previous, operation, original_run)
    media = media_adapter or VideoLearningMedia(root / 'media-preparation')
    path = directory / 'output.mp4'
    metadata = media.probe(path)
    bounds = document['constraints']
    _check((metadata['width'], metadata['height']) == (bounds['width'], bounds['height'])
        and abs(metadata['duration_seconds'] - bounds['duration_seconds']) <= 1 / 24 + 1e-5,
        'media_changed')
    probe = _decode(path, media, bounds)
    _check(metadata['has_audio'] == bool(request.get('sound_events'))
        and receipt.get('sound_events_sha256') == digest(request.get('sound_events', [])), 'audio_changed')
    observed = media.sample(path, media_sha256=receipt['output_sha256'], start_seconds=0,
        end_seconds=bounds['duration_seconds'], store=store)
    # Recheck custody after decoding and before any project is changed.
    _check(_bytes(path, FILES['output.mp4']) == data['output.mp4'], 'media_changed')
    now, eid, cid, oid = clock(), new_id(), new_id(), new_id()
    blobs = {name: store.blob(raw) for name, raw in data.items()}
    evidence = dict(manifest_sha256=store.blob(manifest_raw), files=blobs, actual_probe=probe)
    provenance = dict(schema='video.external-offline-import.v1', origin='external_offline',
        source_candidate_id=candidate_id, source_experiment_id=original_run['id'],
        source_operation_id=operation['id'], source_sha256=source['source_sha256'],
        input_sha256=operation['input_sha256'], manifest_sha256=manifest_sha,
        request_sha256=digest(request), receipt_sha256=blobs['render-receipt.json'],
        media_sha256=receipt['output_sha256'], runtime_scope='external_offline_derived_host',
        production_admission=False, production_acceptance=False, resources=deepcopy(plan.get('resources', {})),
        original_host_sha256=plan['original_host_sha256'], derived_host_sha256=plan['derived_host_sha256'],
        import_operation_id=oid, imported_at=now)
    artifacts = {kind: dict(id=cid + '-' + kind, sha256=sha, mime_type=mime)
        for kind, sha, mime in (
            ('media', blobs['output.mp4'], 'video/mp4'),
            ('poster', observed['frames'][0]['sha256'], 'image/jpeg'),
            ('source', store.blob(canonical_bytes(source)), 'application/json'),
            ('receipt', blobs['render-receipt.json'], 'application/json'),
            ('import_evidence', store.blob(canonical_bytes(evidence)), 'application/json'))}
    candidate = dict(id=cid, work_kind='reproduction', role='reproduction', round=original_run['round'],
        source=source, source_sha256=source['source_sha256'], method=deepcopy(previous.get('method')),
        review=None, status='rendered', created_at=now, media_sha256=receipt['output_sha256'],
        width=metadata['width'], height=metadata['height'], duration_seconds=metadata['duration_seconds'],
        fps=24, has_audio=metadata['has_audio'], observation=observed, artifacts=artifacts,
        external_import=provenance, import_operation_id=oid, verification_scope='sampled_frames_only',
        evidence_scope=deepcopy(provenance), continuous_motion_verified=False, audio_verified=False)
    run = dict(id=eid, owner=owner, project_id=project_id, request_id=request_id,
        mode='external_offline_import', frozen=deepcopy(original_run['frozen']),
        frozen_sha256=original_run['frozen_sha256'], budget=deepcopy(original_run['budget']),
        state='completed', stage='external_import', work_kind='reproduction', round=original_run['round'],
        candidates=[candidate], active_candidate_id=cid, operation_ids=[oid], active_operation_id=None,
        best_candidate_id=None, transfer_task=None, transfer_status='not_verified', stop_requested=False,
        stop_proven=True, stop_reason='external_offline_imported', error=None, external_import=provenance,
        created_at=now, updated_at=now, deadline_at=now)
    event = dict(id=oid, owner=owner, experiment_id=eid, stage='external_import', status='completed',
        input=deepcopy(operation['input']), input_sha256=operation['input_sha256'], job_id=None,
        result=dict(project_id=project_id, experiment_id=eid, candidate_id=cid, operation_id=oid,
            external_import=provenance, evidence=evidence), observation_receipt=None, created_at=now)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = store.request(owner, KIND, request_id, body, db=db)
        if prior:
            return _result(store.read('operations', owner, prior, db=db))
        current = store.read('projects', owner, project_id, db=db)
        current_latest = store.read('experiments', owner, current['latest_experiment_id'], db=db)
        _check(current['latest_experiment_id'] == latest['id'] and current_latest['state'] in TERMINAL
            and current_latest['stop_proven'] is True
            and store.read('experiments', owner, original_run['id'], db=db) == original_run
            and store.read('operations', owner, operation['id'], db=db) == operation, 'origin_changed')
        store.put('experiments', run, db=db, insert=True)
        store.put('operations', event, db=db, insert=True)
        store.bind_request(owner, KIND, request_id, body, oid, db)
        current.update(latest_experiment_id=eid, updated_at=now)
        store.put('projects', current, db=db)
    return _result(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('root', 'bundle', 'owner', 'project-id', 'candidate-id', 'request-id'):
        parser.add_argument('--' + argument, required=True)
    args = parser.parse_args(argv)
    result = import_external_render(args.root, args.bundle, owner=args.owner,
        project_id=args.project_id, candidate_id=args.candidate_id, request_id=args.request_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
