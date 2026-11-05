"""Actual-recording timing and explicit human review for motion candidates."""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
import json

from agent_service.contracts import ServiceError, digest
from .audio_store import narration_document
from .motion import (estimated_alignment, make_beats, manual_alignment,
                     resolve_timeline, semantic_digest, validate_alignment)
from .motion_process import resolve_process_timeline
from .motion_store import PROCESS_CANDIDATE_COMPILER, plan_diagram_kind


MAX_PROJECT_ALIGNMENTS = 256


def _require(value, code='invalid_motion_timing', status=422):
    if not value:
        raise ServiceError(code, status)


class MotionTiming:
    def __init__(self, motion_store):
        self.motion = motion_store
        self.store = motion_store.store
        with self.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS motion_alignments (
                owner TEXT NOT NULL, project_id TEXT NOT NULL, scene_id TEXT NOT NULL,
                motion_version_id TEXT NOT NULL, audio_version_id TEXT NOT NULL,
                document TEXT NOT NULL, PRIMARY KEY(owner,motion_version_id,audio_version_id))''')

    def get(self, owner, version_id, audio_version_id):
        """Read-only: a deterministic estimate never persists or grants review."""
        with self.store.lock:
            version = self.motion.version(owner, version_id)
            _require(not version['stale'], 'motion_plan_stale', 409)
            project = self.store.project(owner, version['project_id'])
            scene = self.store._scene(project, version['scene_id'])
            audio = self.store.audio_version(owner, audio_version_id)
            _require(audio['project_id'] == project['id'] and audio['scene_id'] == scene['id'], 'not_found', 404)
            _require(audio['input_sha256'] == scene['audio_input_sha256'], 'audio_stale', 409)
            spoken = narration_document(scene, self.store._project_speech_provider(project))['spoken_text']
            beats = make_beats(scene['narration'], spoken)
            _require(semantic_digest(beats) == semantic_digest(version['beats']), 'motion_semantics_stale', 409)
            with self.store.transaction() as db:
                row = db.execute('SELECT document FROM motion_alignments WHERE owner=? '
                                 'AND motion_version_id=? AND audio_version_id=?',
                                 (owner, version_id, audio_version_id)).fetchone()
            alignment = (validate_alignment(json.loads(row[0]), beats) if row else
                         estimated_alignment(beats, audio['sha256'], audio['duration_seconds']))
            _require(alignment['audio_sha256'] == audio['sha256'] and
                     alignment['audio_seconds'] == audio['duration_seconds'], 'motion_audio_binding_mismatch', 409)
            options = dict(leading_seconds=scene.get('leading_seconds', 0),
                           trailing_seconds=scene.get('trailing_seconds', 0))
            if plan_diagram_kind(version['plan']) == 'process':
                _require(version.get('compiler_version') == PROCESS_CANDIDATE_COMPILER,
                         'process_candidate_compiler_invalid', 409)
                timeline = resolve_process_timeline(version['plan'], beats, alignment,
                    compiler_version=PROCESS_CANDIDATE_COMPILER, **options)
            else:
                timeline = resolve_timeline(version['plan'], beats, alignment, **options)
            return dict(project_id=project['id'], project_revision=project['revision'], scene_id=scene['id'],
                version_id=version_id, audio_version_id=audio_version_id, beats=beats, alignment=alignment,
                alignment_sha256=digest(alignment), requires_review=alignment['review_status'] != 'verified',
                audio={key: audio.get(key) for key in ('id', 'sha256', 'duration_seconds', 'origin', 'model', 'voice')},
                timeline=timeline)

    def save(self, owner, version_id, request, *, verify=False):
        fields = {'expected_revision', 'audio_version_id', 'expected_alignment_sha256'}
        _require(type(request) is dict and fields <= set(request)
                 and set(request) <= fields | ({'segments', 'correction_reason'} if not verify else set())
                 and (verify or 'segments' in request)
                 and type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        with self.store.lock:
            current = self.get(owner, version_id, request['audio_version_id'])
            _require(current['alignment_sha256'] == request['expected_alignment_sha256'], 'motion_alignment_conflict', 409)
            actor = owner if len(owner) <= 120 else 'owner-sha256:' + digest(owner)
            if verify:
                # Review is an assertion about these exact existing anchors.
                # Preserve their estimate/manual provenance and corrections.
                alignment = deepcopy(current['alignment'])
                alignment.update(review_status='verified', reviewed_by=actor,
                                 reviewed_at=datetime.now(timezone.utc).isoformat())
                alignment = validate_alignment(alignment, current['beats'])
            else:
                alignment = manual_alignment(current['beats'], current['audio']['sha256'],
                    current['audio']['duration_seconds'], request['segments'],
                    previous_alignment=current['alignment'],
                    correction_reason=request.get('correction_reason', 'Manually adjusted audio anchors'))
            raw = json.dumps(alignment, ensure_ascii=False, separators=(',', ':'))
            _require(len(raw.encode('utf-8')) <= 64 * 1024, 'motion_alignment_too_large', 413)
            with self.store.transaction() as db:
                project = self.store._project(db, owner, current['project_id'])
                self.store._revision(project, request['expected_revision'])
                old = db.execute('SELECT document FROM motion_alignments WHERE owner=? AND motion_version_id=? '
                                 'AND audio_version_id=?', (owner, version_id, request['audio_version_id'])).fetchone()
                if old is None:
                    count = db.execute('SELECT count(*) FROM motion_alignments WHERE project_id=?',
                                       (project['id'],)).fetchone()[0]
                    _require(count < MAX_PROJECT_ALIGNMENTS, 'motion_alignment_limit', 409)
                self.motion._capacity(db, project['id'], extra_bytes=len(raw.encode('utf-8')) -
                    (len(old[0].encode('utf-8')) if old else 0))
                db.execute('INSERT INTO motion_alignments VALUES (?, ?, ?, ?, ?, ?) '
                           'ON CONFLICT(owner,motion_version_id,audio_version_id) DO UPDATE SET document=excluded.document',
                           (owner, project['id'], current['scene_id'], version_id, request['audio_version_id'], raw))
                self.store._save(db, project)
            return self.get(owner, version_id, request['audio_version_id'])
