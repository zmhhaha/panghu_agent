"""Immutable continuous diagrams, built on an unchanged v2 source snapshot."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from agent_service.contracts import ServiceError, canonical_bytes, digest
from . import media
from .motion_snapshot import SCHEMA as SOURCE_SCHEMA, verify_snapshot_v2
from .continuity import (CONTINUITY_COMPILER_TEMPLATES, CONTINUITY_COMPILER_VERSION,
                         resolve_continuity, compile_continuity_project)

SCHEMA = 'video.render-snapshot.v3'
MOTION_SCHEMAS = frozenset({SOURCE_SCHEMA, SCHEMA})


def _require(value, code='invalid_continuity_snapshot', status=422):
    if not value:
        raise ServiceError(code, status)


def freeze_continuity_snapshot(source, version, *, compiler_version=CONTINUITY_COMPILER_VERSION):
    """Freeze a reviewed visual candidate without changing its source media."""
    verify_snapshot_v2(source)
    _require(not source['subtitles'], 'continuity_subtitles_not_supported')
    _require(all(scene['visual_mode'] == 'motion' for scene in source['scenes']),
             'continuity_requires_motion', 409)
    _require(type(version) is dict and type(version.get('id')) is str
             and type(version.get('plan')) is dict)
    _require(type(compiler_version) is str and compiler_version in CONTINUITY_COMPILER_TEMPLATES)
    resolution = json.loads(canonical_bytes(resolve_continuity(source['scenes'], version['plan'],
                                                              compiler_version=compiler_version)))
    _require(len(resolution['segments']) == len(source['scenes']))
    result = deepcopy(source)
    result.update(schema=SCHEMA, visual_mode='continuous', source_snapshot_sha256=source['sha256'],
                  continuity=dict(version_id=version['id'], plan=deepcopy(version['plan']),
                                  resolution=resolution))
    cursor = 0.0
    for scene, segment in zip(result['scenes'], resolution['segments']):
        _require(segment['scene_id'] == scene['id'])
        scene['continuity_base_timing'] = deepcopy(scene['timing'])
        scene['continuity'] = deepcopy(segment)
        scene['timing'] = dict(scene['timing'],
            leading_seconds=segment['timeline']['leading_seconds'],
            trailing_seconds=segment['timeline']['trailing_seconds'],
            duration_seconds=segment['timeline']['duration_seconds'])
        _require(0 < scene['timing']['duration_seconds'] <= 60,
                 'motion_scene_duration_exceeded')
        files = compile_continuity_project(scene, segment,
            runtime_fingerprint=result['runtime_fingerprint'])
        scene['continuity_project_files_sha256'] = {
            name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}
        scene.update(start_seconds=cursor, end_seconds=cursor + scene['timing']['duration_seconds'])
        cursor = scene['end_seconds']
    _require(cursor <= media.MAX_PROJECT_SECONDS, 'media_project_too_long')
    result['duration_seconds'] = cursor
    result['sha256'] = digest({key: value for key, value in result.items() if key != 'sha256'})
    return result


def verify_continuity_snapshot(snapshot):
    _require(type(snapshot) is dict and snapshot.get('schema') == SCHEMA)
    _require(snapshot.get('sha256') == digest({key: value for key, value in snapshot.items()
                                             if key != 'sha256'}), 'continuity_snapshot_corrupt', 409)
    try:
        source = deepcopy(snapshot)
        continuity = source.pop('continuity')
        compiler_version = continuity['resolution']['compiler']
        _require(type(compiler_version) is str and compiler_version in CONTINUITY_COMPILER_TEMPLATES,
                 'continuity_snapshot_corrupt', 409)
        source_sha = source.pop('source_snapshot_sha256')
        source.update(schema=SOURCE_SCHEMA, visual_mode='mixed')
        cursor = 0.0
        for scene in source['scenes']:
            scene.pop('continuity')
            scene.pop('continuity_project_files_sha256')
            scene['timing'] = scene.pop('continuity_base_timing')
            scene.update(start_seconds=cursor, end_seconds=cursor + scene['timing']['duration_seconds'])
            cursor = scene['end_seconds']
        source.update(duration_seconds=cursor, sha256=source_sha)
        verify_snapshot_v2(source)
        rebuilt = freeze_continuity_snapshot(source, dict(id=continuity['version_id'], plan=continuity['plan']),
                                             compiler_version=compiler_version)
        _require(rebuilt == snapshot, 'continuity_snapshot_corrupt', 409)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ServiceError('continuity_snapshot_corrupt', 409) from None
    return snapshot


def verify_motion_snapshot(snapshot):
    if type(snapshot) is dict and snapshot.get('schema') == SCHEMA:
        return verify_continuity_snapshot(snapshot)
    return verify_snapshot_v2(snapshot)
