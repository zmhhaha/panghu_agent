"""Opt-in durable adapter for the existing isolated HTML/SVG renderer.

The caller must run this in an explicit rendering executor, never mount a
Docker socket into the ordinary web application. Source remains inert until
the existing networkless renderer executes it inside its bounded container.
One SQLite claim permanently owns one external render attempt. Missing or
ambiguous evidence is recoverable by lookup only, never another execution.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from fractions import Fraction

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_create as creation
from . import animation_experiment_render as isolated


_OP = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_TABLE = 'animation_creation_render_operations'
_BINDING_LIMIT = 3 * 1024 * 1024


def _require(value, code='animation_creation_render_binding_invalid', status=409):
    if not value:
        raise ServiceError(code, status)


def _plain(path):
    path = Path(path).absolute()
    _require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                     for p in (path, *path.parents)), 'animation_creation_render_path_invalid')
    _require(not any(c in str(path) for c in (',', '\r', '\n', '\0')),
             'animation_creation_render_path_invalid')
    return path


def _read(path, limit):
    path = _plain(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    _require(path.is_file() and path.stat().st_nlink == 1
             and 0 < path.stat().st_size <= limit, 'animation_creation_render_artifact_invalid')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    _require(0 < len(raw) <= limit, 'animation_creation_render_artifact_invalid')
    return raw


def _json(path, limit):
    raw = _read(path, limit)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError('animation_creation_render_artifact_invalid', 409) from None
    _require(type(value) is dict, 'animation_creation_render_artifact_invalid')
    return value, raw


def _write(path, raw):
    path = _plain(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class DurableAnimationRenderer:
    """One-attempt renderer with an independent, private root directory.

    ``enabled`` defaults to false. ``lookup`` returns None while actual evidence
    is missing or cleanup remains uncertain. A missing operation is 404;
    changed bindings/evidence are 409. A known failed isolated outcome produces
    a failed verification rather than authorizing another render.

    Goal-independent media/runtime checks can pass when the old renderer reports
    pending_goal_review. That status is preserved in the original outcome and
    the adapter assessment; it never means visual, semantic or user approval.
    """

    def __init__(self, root, *, image, assets_dir=None, enabled=False, timeout=180):
        match = isolated._IMAGE.fullmatch(image) if type(image) is str else None
        _require(match is not None, 'animation_creation_render_image_pin_required', 422)
        _require(type(enabled) is bool and type(timeout) in (int, float)
                 and math.isfinite(timeout) and 30 <= timeout <= isolated.MAX_TIMEOUT,
                 'animation_creation_render_config_invalid', 422)
        self.root = _plain(root)
        self.image, self.image_digest = image, match[2]
        self.assets_dir = _plain(assets_dir) if assets_dir is not None else None
        self.enabled, self.timeout = enabled, timeout
        self.ledger = self.root / 'render-ledger.sqlite3'

    def _key(self, operation_id):
        _require(type(operation_id) is str and _OP.fullmatch(operation_id) is not None,
                 'animation_creation_render_operation_invalid', 422)
        return hashlib.sha256(operation_id.encode('utf-8')).hexdigest()

    def _directory(self, operation_id):
        return _plain(self.root / 'operations' / self._key(operation_id))

    def _connect(self, *, create=False):
        _plain(self.root)
        _plain(self.ledger)
        if not self.ledger.exists():
            operations = self.root / 'operations'
            _require(not operations.exists() or not any(operations.iterdir()),
                     'animation_creation_render_ledger_missing')
            _require(create, 'animation_creation_render_not_found', 404)
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            _require(self.ledger.is_file() and self.ledger.stat().st_nlink == 1,
                     'animation_creation_render_ledger_invalid')
        db = sqlite3.connect(str(self.ledger), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= {_TABLE}, 'animation_creation_render_database_not_isolated')
            if create:
                db.execute('''CREATE TABLE IF NOT EXISTS animation_creation_render_operations (
                    operation_id TEXT PRIMARY KEY, directory_key TEXT NOT NULL UNIQUE,
                    binding_sha256 TEXT NOT NULL, binding BLOB NOT NULL,
                    state TEXT NOT NULL, claimed_at REAL NOT NULL,
                    error_category TEXT, proof_sha256 TEXT, verification BLOB, assessment BLOB)''')
            else:
                _require(_TABLE in tables, 'animation_creation_render_ledger_invalid')
        except BaseException:
            db.close()
            raise
        return db

    def _snapshot(self, operation_id, source, creation_input):
        key = self._key(operation_id)
        context = creation.validate_creation_input(creation_input)
        _require(type(source) is dict and source.get('engine') == 'html-svg',
                 'animation_creation_render_engine_not_admitted', 422)
        payload = creation._validate_source_output(dict(engine=source['engine'], files=source.get('files'),
            strategy=dict(description='Frozen render source', tradeoffs='')), context)
        payload = {name: payload[name] for name in ('engine', 'files')}
        source_sha = creation.source_sha256(payload)
        _require('source_sha256' not in source or source['source_sha256'] == source_sha,
                 'animation_creation_render_source_changed')
        file_shas = {name: hashlib.sha256(text.encode('utf-8')).hexdigest()
                     for name, text in payload['files'].items()}
        _require('files_sha256' not in source or source['files_sha256'] == file_shas,
                 'animation_creation_render_source_changed')
        try:
            isolated._constraints(context['constraints'])
        except ValueError:
            raise ServiceError('animation_creation_render_budget_not_admitted', 422) from None
        adapted = dict(schema='video.animation-experiment-input.v1',
            experiment_id=context['creation_id'], capability='complete-fragment',
            goal=context['goal'], constraints=deepcopy(context['constraints']),
            assets=deepcopy(context['assets']), prompt_revision=context['prompt_revision'],
            guidance=context['guidance'])
        host_sha = hashlib.sha256(_read(isolated.HOST, 1024 * 1024)).hexdigest()
        binding = dict(schema='video.animation-durable-render-binding.v1', operation_id=operation_id,
            directory_key=key, source=payload, source_sha256=source_sha,
            creation_input=context, creation_input_sha256=digest(context),
            adapted_input=adapted, adapted_input_sha256=digest(adapted), image=self.image,
            image_digest=self.image_digest, files_sha256=file_shas,
            assets_sha256=deepcopy(context['assets']), host_sha256=host_sha,
            timeout_seconds=self.timeout)
        _require(len(canonical_bytes(binding)) <= _BINDING_LIMIT,
                 'animation_creation_render_binding_too_large', 413)
        return binding

    def _claim(self, binding):
        raw = canonical_bytes(binding)
        db = self._connect(create=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE operation_id=?',
                             (binding['operation_id'],)).fetchone()
            if row is not None:
                _require(row['binding_sha256'] == digest(binding) and bytes(row['binding']) == raw,
                         'animation_creation_render_operation_conflict')
                db.execute('COMMIT')
                return False
            db.execute('INSERT INTO ' + _TABLE +
                ' (operation_id,directory_key,binding_sha256,binding,state,claimed_at) VALUES (?,?,?,?,?,?)',
                (binding['operation_id'], binding['directory_key'], digest(binding), raw, 'claimed', time.time()))
            db.execute('COMMIT')
            return True
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def _row(self, operation_id):
        key = self._key(operation_id)
        db = self._connect()
        try:
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE operation_id=?', (operation_id,)).fetchone()
            _require(row is not None, 'animation_creation_render_not_found', 404)
            try:
                raw = bytes(row['binding'])
                binding = json.loads(raw)
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise ServiceError('animation_creation_render_ledger_binding_changed', 409) from None
            _require(type(binding) is dict and raw == canonical_bytes(binding)
                     and row['binding_sha256'] == digest(binding)
                     and row['directory_key'] == key == binding.get('directory_key')
                     and binding.get('operation_id') == operation_id and binding.get('image') == self.image
                     and binding.get('image_digest') == self.image_digest,
                     'animation_creation_render_ledger_binding_changed')
            return dict(row), binding
        finally:
            db.close()

    def _state(self, operation_id, state, error=None):
        db = self._connect()
        try:
            db.execute('UPDATE ' + _TABLE + ' SET state=?,error_category=? WHERE operation_id=?',
                       (state, error, operation_id))
        finally:
            db.close()

    def render(self, operation_id, source, creation_input):
        _require(self.enabled, 'animation_creation_renderer_disabled', 503)
        binding = self._snapshot(operation_id, source, creation_input)
        if not self._claim(binding):
            return self.lookup(operation_id)
        operation = self._directory(operation_id)
        try:
            operation.mkdir(mode=0o700, parents=True, exist_ok=False)
            _write(operation / 'binding.json', canonical_bytes(binding))
            scene = operation / 'source'
            scene.mkdir(mode=0o700)
            for name, text in binding['source']['files'].items():
                _write(scene / name, text.encode('utf-8'))
            for name, expected in binding['assets_sha256'].items():
                _require(self.assets_dir is not None, 'animation_creation_render_assets_missing', 422)
                _require(hashlib.sha256(_read(self.assets_dir / name, 32 * 1024 * 1024)).hexdigest()
                         == expected, 'animation_creation_render_assets_changed')
            _write(operation / 'execution-claim.json', canonical_bytes(dict(
                schema='video.animation-durable-render-claim.v1', operation_id=operation_id,
                binding_sha256=digest(binding), max_render_attempts=1, automatic_retry=False)))
            self._state(operation_id, 'executing')
            try:
                isolated.render_scene(scene, operation / 'render', image=self.image,
                    input_document=binding['adapted_input'], timeout=self.timeout, assets_dir=self.assets_dir)
            except isolated.RenderExecutionError:
                pass  # The real bound failed outcome is read below, never retried.
        except Exception as error:
            self._state(operation_id, 'evidence_pending', type(error).__name__)
            # A partial preparation may have crossed an unknown external
            # boundary. Only existing evidence is reconciled from this point.
        return self.lookup(operation_id)

    def _expected(self, binding, run):
        request = dict(schema='video.animation-experiment-render-request.v1',
            input_sha256=binding['adapted_input_sha256'], constraints=binding['adapted_input']['constraints'],
            files_sha256=binding['files_sha256'], assets_sha256=binding['assets_sha256'],
            host_sha256=binding['host_sha256'], image_digest=binding['image_digest'], fps=24,
            max_frames=isolated.MAX_FRAMES, max_media_bytes=isolated.MAX_MEDIA_BYTES,
            max_evidence_bytes=isolated.MAX_EVIDENCE_BYTES,
            output='silent_native_frames_and_mp4', production_admitted=False)
        request['identity'] = digest(request)
        match = isolated._IMAGE.fullmatch(binding['image'])
        plan = dict(schema='video.animation-experiment-render-plan.v1', directory=str(run),
            image=binding['image'], repository=match[1], image_digest=binding['image_digest'],
            timeout_seconds=binding['timeout_seconds'], request_sha256=digest(request),
            request_identity=request['identity'], input_sha256=binding['adapted_input_sha256'],
            host_sha256=binding['host_sha256'], network='none', uid=10001,
            read_only_root=True, automatic_retry=False,
            renderer='native_seek_screenshots_ffmpeg_not_hyperframes_export')
        return request, plan

    @staticmethod
    def _check_media_probe(receipt, constraints, actual_bytes):
        """Bind the trusted container's ffprobe/decode report to this request."""
        try:
            probe = receipt['probe']
            streams = probe['streams']
            video = streams[0]
            duration = float(probe['format']['duration'])
            fps = Fraction(video['avg_frame_rate'])
            valid = (type(streams) is list and len(streams) == 1
                and video.get('codec_type') == 'video' and video.get('codec_name') == 'h264'
                and video.get('width') == constraints['width']
                and video.get('height') == constraints['height'] and fps == 24
                and math.isfinite(duration) and abs(duration - constraints['duration_seconds']) <= 1/24 + .001
                and type(receipt.get('frame_count')) is int
                and receipt['frame_count'] == int(constraints['duration_seconds'] * 24)
                and type(receipt.get('output_bytes')) is int and receipt['output_bytes'] == actual_bytes
                and receipt.get('output') == 'silent_native_frames_and_mp4'
                and receipt.get('production_admitted') is False)
        except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, OverflowError):
            valid = False
        _require(valid, 'animation_creation_render_media_probe_invalid')

    def _evidence(self, operation_id, binding):
        operation = self._directory(operation_id)
        run = _plain(operation / 'render')
        _require(_read(operation / 'binding.json', _BINDING_LIMIT) == canonical_bytes(binding),
                 'animation_creation_render_binding_changed')
        claim, _ = _json(operation / 'execution-claim.json', 16384)
        _require(claim == dict(schema='video.animation-durable-render-claim.v1', operation_id=operation_id,
            binding_sha256=digest(binding), max_render_attempts=1, automatic_retry=False),
            'animation_creation_render_claim_changed')
        scene = _plain(operation / 'source')
        if not scene.exists():
            raise FileNotFoundError(str(scene))
        _require(scene.is_dir() and {path.name for path in scene.iterdir()} == set(binding['source']['files']),
                 'animation_creation_render_source_changed')
        for name, text in binding['source']['files'].items():
            _require(_read(scene / name, creation.SOURCE_MAX_BYTES) == text.encode('utf-8'),
                     'animation_creation_render_source_changed')
        plan, plan_raw = _json(run / 'render-plan.json', 65536)
        expected_request, expected_plan = self._expected(binding, run)
        _require(plan == expected_plan, 'animation_creation_render_plan_changed')
        request_raw = _read(run / 'input/request.json', 65536)
        _require(request_raw == canonical_bytes(expected_request), 'animation_creation_render_request_changed')
        _require(_read(run / 'input-document.json', creation.INPUT_MAX_BYTES) ==
                 canonical_bytes(binding['adapted_input']), 'animation_creation_render_input_changed')
        # Inspect every ancestor before calling the existing strict validators;
        # no artifact path is allowed to resolve through a symlink/junction.
        for name in binding['files_sha256']:
            _read(run / 'input/scene' / name, creation.SOURCE_MAX_BYTES)
        for name in binding['assets_sha256']:
            _read(run / 'input/scene/assets' / name, 32 * 1024 * 1024)
        _read(run / 'adapters' / isolated.HOST.name, 1024 * 1024)
        request = isolated._frozen(plan)
        _require(request == expected_request, 'animation_creation_render_request_changed')
        start, start_raw = _json(run / 'render-start.json', 65536)
        token = start.get('owner_token')
        _require(start.get('schema') == 'video.animation-experiment-render-start.v1'
                 and type(token) is str and re.fullmatch('[0-9a-f]{32}', token) is not None
                 and start.get('container_name') == 'animation-experiment-' + request['identity'][:12] + '-' + token[:12]
                 and start.get('plan') == plan and type(start.get('started_at')) is str,
                 'animation_creation_render_start_changed')
        outcome, outcome_raw = _json(run / 'render-outcome.json', 2 * 1024 * 1024)
        _require(outcome.get('schema') == 'video.animation-experiment-render-outcome.v1'
                 and outcome.get('request_identity') == request['identity']
                 and outcome.get('input_sha256') == binding['adapted_input_sha256']
                 and outcome.get('image') == self.image and outcome.get('renderer') == plan['renderer']
                 and outcome.get('container_name') == start['container_name']
                 and outcome.get('started_at') == start['started_at']
                 and outcome.get('production_adoption') is False and outcome.get('automatic_retry') is False
                 and outcome.get('status') in ('rendered', 'failed'),
                 'animation_creation_render_outcome_changed')
        cleanup = outcome.get('cleanup')
        _require(type(cleanup) is dict, 'animation_creation_render_cleanup_invalid')
        if cleanup.get('status') == 'unconfirmed':
            return None
        _require(cleanup.get('status') in ('removed_and_confirmed_absent', 'not_created'),
                 'animation_creation_render_cleanup_invalid')
        if cleanup['status'] == 'removed_and_confirmed_absent':
            _require(type(outcome.get('container_id')) is str and _SHA.fullmatch(outcome['container_id'])
                     and cleanup.get('container_id') == outcome['container_id']
                     and type(outcome.get('actual_image_id')) is str
                     and re.fullmatch('sha256:[0-9a-f]{64}', outcome['actual_image_id']) is not None,
                     'animation_creation_render_container_identity_invalid')
        else:
            _require(outcome.get('container_id') is None and cleanup.get('container_id') is None,
                     'animation_creation_render_container_identity_invalid')
        checks = [dict(code='source_input_assets_image_binding', status='passed'),
                  dict(code='isolated_run_cleanup', status='passed')]
        versions, receipt, technical = [], None, None
        if outcome['status'] == 'rendered':
            _require(cleanup['status'] == 'removed_and_confirmed_absent' and outcome.get('error') is None,
                     'animation_creation_render_outcome_changed')
            work = _plain(run / 'work')
            _plain(work / 'frames')
            # Distinguish missing custody evidence from contradictory bytes.
            # The underlying validator intentionally reports both as a generic
            # ValueError; our durable caller needs absence to remain pending.
            for name, maximum in (('render-receipt.json', 128 * 1024),
                                  ('observations.json', 8 * 1024 * 1024),
                                  ('output.mp4', isolated.MAX_MEDIA_BYTES)):
                _read(work / name, maximum)
            count = int(request['constraints']['duration_seconds'] * 24)
            for name in [*('frame-%06d.jpg' % index for index in range(count)), 'reverse-initial.jpg']:
                _read(work / 'frames' / name, 4 * 1024 * 1024)
            receipt, technical = isolated._outputs(run, request, plan)
            _require(receipt == outcome.get('receipt') and technical == outcome.get('technical')
                     and receipt.get('image_digest') == self.image_digest,
                     'animation_creation_render_output_changed')
            self._check_media_probe(receipt, request['constraints'],
                                    len(_read(work / 'output.mp4', isolated.MAX_MEDIA_BYTES)))
            checks.append(dict(code='encoded_media_and_all_frame_digests', status='passed'))
            status = technical.get('status')
            _require(status in ('passed', 'failed', 'pending_goal_review'),
                     'animation_creation_render_technical_invalid')
            if status == 'pending_goal_review':
                checks.append(dict(code='runtime_only_goal_not_evaluated',
                                   status='passed' if not technical.get('issues') else 'failed'))
            else:
                checks.append(dict(code='declared_goal_checks',
                                   status='passed' if status == 'passed' and not technical.get('issues') else 'failed'))
            versions = [dict(version_id='animation-media-' + binding['directory_key'],
                             sha256=receipt['output_sha256'])]
        else:
            _require(type(outcome.get('error')) is str and bool(outcome['error']),
                     'animation_creation_render_failure_invalid')
            checks.extend([dict(code='isolated_render_execution', status='failed'),
                           dict(code='encoded_media_and_all_frame_digests', status='not_run')])
        verification = dict(schema='video.animation-render-verification.v1', operation_id=operation_id,
            source_sha256=binding['source_sha256'], technical_passed=all(row['status'] == 'passed' for row in checks),
            media_versions=versions, checks=checks)
        assessment = dict(schema='video.animation-render-assessment.v1', operation_id=operation_id,
            source_sha256=binding['source_sha256'], creation_input_sha256=binding['creation_input_sha256'],
            adapted_input_sha256=binding['adapted_input_sha256'], image=self.image,
            goal_review_status=technical['status'] if technical else 'not_run',
            goal_verified=bool(technical and technical['status'] == 'passed'),
            runtime_scope=technical.get('scope') if technical else None,
            aesthetic_status='unreviewed', whole_video_watched=False, audio_observed=False,
            production_adoption=False)
        proof = digest(dict(binding_sha256=digest(binding), plan_sha256=hashlib.sha256(plan_raw).hexdigest(),
            start_sha256=hashlib.sha256(start_raw).hexdigest(), outcome_sha256=hashlib.sha256(outcome_raw).hexdigest(),
            receipt_sha256=digest(receipt) if receipt else None, verification=verification, assessment=assessment))
        return verification, assessment, proof

    def lookup(self, operation_id):
        row, binding = self._row(operation_id)
        try:
            evidence = self._evidence(operation_id, binding)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
            raise ServiceError('animation_creation_render_evidence_invalid', 409) from None
        if evidence is None:
            return None
        verification, assessment, proof = evidence
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT proof_sha256 FROM ' + _TABLE + ' WHERE operation_id=?',
                                  (operation_id,)).fetchone()
            _require(previous is not None and previous['proof_sha256'] in (None, proof),
                     'animation_creation_render_proof_changed')
            db.execute('UPDATE ' + _TABLE +
                ' SET state=?,proof_sha256=?,verification=?,assessment=? WHERE operation_id=?',
                ('finished', proof, canonical_bytes(verification), canonical_bytes(assessment), operation_id))
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()
        target = self._directory(operation_id) / 'adapter-assessment.json'
        raw = canonical_bytes(assessment)
        try:
            _write(target, raw)
        except FileExistsError:
            _require(_read(target, 65536) == raw, 'animation_creation_render_assessment_changed')
        return deepcopy(verification)

    def media_path(self, operation_id):
        verification = self.lookup(operation_id)
        _require(verification is not None, 'animation_creation_render_pending', 503)
        _require(bool(verification['media_versions']), 'animation_creation_render_media_missing', 404)
        path = self._directory(operation_id) / 'render/work/output.mp4'
        raw = _read(path, isolated.MAX_MEDIA_BYTES)
        _require(hashlib.sha256(raw).hexdigest() == verification['media_versions'][0]['sha256'],
                 'animation_creation_render_media_changed')
        return path
