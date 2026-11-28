"""Shared retained-image sampling, independent of evaluation methods.

Only prepare/cancel write. Sources are resolved by trusted business adapters;
the service never accepts a user path, invokes an author, or submits a model.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest


SCHEMA = 'video.visual-sampling.v1'
MAX_FRAMES = 240
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_MEDIA_BYTES = 512 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out', 'interrupted'}


def require(ok, code='visual_sampling_invalid', status=422):
    if not ok:
        raise ServiceError(code, status)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _identity(value):
    require(type(value) is str and 0 < len(value) <= 512
            and all(ord(c) >= 32 and ord(c) != 127 for c in value),
            'visual_sampling_identity_invalid')
    return value


def _document(value, limit=128 * 1024):
    require(type(value) is dict, 'visual_sampling_document_invalid')
    try:
        raw = canonical_bytes(value)
    except (ValueError, TypeError, RecursionError):
        raise ServiceError('visual_sampling_document_invalid', 422) from None
    require(len(raw) <= limit, 'visual_sampling_document_too_large', 413)
    return json.loads(raw)


def uniform_indices(frame_count, max_frames):
    """The existing method rule, including its short-clip coverage limitation.

    The denominator intentionally remains max_frames - 1 even when the source
    is shorter. For three source frames and a limit of twelve the old method
    returns only frame zero. This is compatibility, not full-span coverage.
    """
    require(type(frame_count) is int and 1 <= frame_count <= 2**31 - 1
            and type(max_frames) is int and 1 <= max_frames <= MAX_FRAMES,
            'visual_sampling_frame_budget_invalid')
    return sorted({round(i * (frame_count - 1) / max(1, max_frames - 1))
                   for i in range(min(max_frames, frame_count))})


def select_indices(strategy, frame_count, max_frames, *, indices=None):
    """Common parameterized selection; a policy is never inferred from a reviewer."""
    uniform_indices(frame_count, max_frames)  # shared bounds
    if strategy in {'uniform', 'native_uniform', 'method_uniform'}:
        return uniform_indices(frame_count, max_frames)
    if strategy in {'first-middle-last', 'native_capture'}:
        return sorted({0, frame_count // 2, frame_count - 1})[:max_frames]
    require(strategy in {'provided', 'explicit'} and type(indices) is list
            and 0 < len(indices) <= max_frames
            and all(type(i) is int and 0 <= i < frame_count for i in indices)
            and len(set(indices)) == len(indices), 'visual_sampling_selection_invalid')
    return list(indices)


def image_mime(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_IMAGE_BYTES,
            'visual_sampling_image_invalid')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        return 'image/webp'
    raise ServiceError('visual_sampling_image_invalid', 422)


def verify_delivery(frames, files, receipt):
    """Check actual image parts, including repeated pixels at different times.

    The caller still verifies the receipt's authenticated job/attempt/fence
    binding through its existing gateway. This check does not replace that.
    Historical callers without retained input bytes may pass files=None; their
    proof is limited to declared hashes, formats and independent part counts.
    """
    if type(frames) is dict:
        frames = frames.get('observation', {}).get('frames')
    require(type(frames) is list and 0 < len(frames) <= MAX_FRAMES
            and (files is None or type(files) is dict) and type(receipt) is dict
            and receipt.get('evidence_scope') == 'completed_upstream_transport'
            and type(receipt.get('images')) is list and len(receipt['images']) <= 10000,
            'visual_sampling_delivery_invalid', 409)
    expected = Counter()
    identities = []
    for frame in frames:
        require(type(frame) is dict and type(frame.get('file_name')) is str,
                'visual_sampling_delivery_invalid', 409)
        if files is None:
            extension = Path(frame['file_name']).suffix.lower()
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp'}.get(extension)
            require(mime is not None and type(frame.get('sha256')) is str
                    and _SHA.fullmatch(frame['sha256']), 'visual_sampling_delivery_invalid', 409)
            expected[(frame['sha256'], mime, None)] += 1
        else:
            raw = files.get(frame['file_name'])
            mime = image_mime(raw)
            require(_sha(raw) == frame.get('sha256'), 'visual_sampling_delivery_input_changed', 409)
            expected[(_sha(raw), mime, len(raw))] += 1
        identities.append(frame.get('frame_id', frame['file_name']))
    actual, positions, sizes = {}, set(), {}
    for row in receipt['images']:
        require(type(row) is dict and type(row.get('model_request_completed')) is bool,
                'visual_sampling_delivery_invalid', 409)
        if not row['model_request_completed']:
            continue
        request = row.get('model_request_sha256')
        require(type(request) is str and _SHA.fullmatch(request)
                and type(row.get('sha256')) is str and _SHA.fullmatch(row['sha256'])
                and row.get('media_type') in {'image/png', 'image/jpeg', 'image/webp'}
                and type(row.get('byte_length')) is int and 0 < row['byte_length'] <= MAX_IMAGE_BYTES
                and all(type(row.get(k)) is int and 0 <= row[k] < 2000
                        for k in ('input_item_index', 'part_index')),
                'visual_sampling_delivery_invalid', 409)
        position = (request, row['input_item_index'], row['part_index'])
        require(position not in positions, 'visual_sampling_delivery_duplicate_part', 409)
        positions.add(position)
        size_key = (row['sha256'], row['media_type'])
        require(size_key not in sizes or sizes[size_key] == row['byte_length'],
                'visual_sampling_delivery_size_inconsistent', 409)
        sizes[size_key] = row['byte_length']
        actual.setdefault(request, Counter())[(row['sha256'], row['media_type'],
            row['byte_length'] if files is not None else None)] += 1
    matching = sorted(request for request, counts in actual.items()
                      if all(counts[key] >= amount for key, amount in expected.items()))
    require(bool(matching), 'visual_sampling_delivery_incomplete', 409)
    return dict(schema='video.visual-sampling-delivery.v1', frame_ids=identities,
                image_parts=len(frames), model_request_sha256=matching[0],
                receipt_sha256=digest(receipt), evidence_scope='completed_upstream_transport',
                encoded_bytes_verified=files is not None, model_understanding_verified=False)


class VisualSamplingService:
    """One persistent image authority used by business-specific source adapters."""

    def __init__(self, root, providers=None, *, clock=time.time):
        self.root = Path(root).absolute()
        self.clock = clock
        self._ordinary_parents(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = self.root / 'blobs'
        self.operations = self.root / 'operations'
        self.locks = self.root / 'locks'
        for path in (self.blobs, self.operations, self.locks):
            self._ordinary_parents(path)
            path.mkdir(exist_ok=True)
        self.path = self.root / 'sampling.sqlite3'
        self._providers = dict(providers or {})
        self._thread_locks = {}
        self._guard = threading.Lock()
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS samples (
                owner TEXT NOT NULL, operation_id TEXT NOT NULL,
                request BLOB NOT NULL, request_sha256 TEXT NOT NULL,
                status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                manifest BLOB, manifest_sha256 TEXT, error TEXT,
                created_at REAL NOT NULL, completed_at REAL,
                PRIMARY KEY(owner, operation_id))''')

    @staticmethod
    def _ordinary_parents(path):
        require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                        for p in (path, *path.parents)),
                'visual_sampling_path_invalid', 409)

    @contextmanager
    def _db(self, *, readonly=False):
        self._ordinary_parents(self.path)
        db = (sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=15)
              if readonly else sqlite3.connect(self.path, timeout=15))
        db.row_factory = sqlite3.Row
        try:
            if readonly:
                db.execute('PRAGMA query_only=ON')
            else:
                db.execute('PRAGMA synchronous=FULL')
            yield db
            if not readonly:
                db.commit()
        finally:
            db.close()

    def register_provider(self, name, provider):
        _identity(name)
        require(callable(provider), 'visual_sampling_provider_invalid')
        previous = self._providers.get(name)
        require(previous is None or previous is provider, 'visual_sampling_provider_conflict', 409)
        self._providers[name] = provider

    def _key(self, owner, operation):
        return digest([_identity(owner), _identity(operation)])

    @contextmanager
    def _lock(self, key):
        with self._guard:
            lock = self._thread_locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            yield False
            return
        path = self.locks / key
        stream = None
        held = False
        try:
            self._ordinary_parents(path)
            stream = path.open('a+b')
            if os.name == 'nt':
                import msvcrt
                if stream.seek(0, 2) == 0:
                    stream.write(b'0'); stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    held = True
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                except BlockingIOError:
                    pass
            yield held
        finally:
            if held:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)
            if stream is not None:
                stream.close()
            lock.release()

    def _row(self, owner, operation):
        self._key(owner, operation)
        with self._db(readonly=True) as db:
            row = db.execute('SELECT * FROM samples WHERE owner=? AND operation_id=?',
                             (owner, operation)).fetchone()
        require(row is not None, 'visual_sampling_not_found', 404)
        raw = bytes(row['request'])
        require(_sha(raw) == row['request_sha256'], 'visual_sampling_record_changed', 409)
        return row, json.loads(raw)

    def get(self, owner, operation_id, *, expected_manifest_sha256=None):
        row, request = self._row(owner, operation_id)
        value = dict(schema=SCHEMA, operation_id=operation_id, status=row['status'],
                     source_ref=request['source_ref'], policy=request['policy'],
                     request_sha256=row['request_sha256'], deadline_at=request['deadline_at'],
                     manifest_sha256=row['manifest_sha256'], error=row['error'],
                     cancel_requested=bool(row['cancel_requested']),
                     stop_proven=row['status'] in _TERMINAL,
                     created_at=row['created_at'], completed_at=row['completed_at'])
        if row['manifest'] is not None:
            raw = bytes(row['manifest'])
            require(_sha(raw) == row['manifest_sha256'], 'visual_sampling_manifest_changed', 409)
            manifest = json.loads(raw)
            require(manifest['request_sha256'] == row['request_sha256'],
                    'visual_sampling_manifest_changed', 409)
            value.update(source=manifest['source'], frames=manifest['frames'],
                         usage=manifest['usage'], evidence_scope=manifest['evidence_scope'])
        if expected_manifest_sha256 is not None:
            require(value['manifest_sha256'] == expected_manifest_sha256,
                    'visual_sampling_manifest_changed', 409)
        return value

    def _finish(self, owner, operation, status, *, manifest=None, error=None):
        raw = canonical_bytes(manifest) if manifest is not None else None
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE samples SET status=?,manifest=?,manifest_sha256=?,error=?,completed_at=? '
                       'WHERE owner=? AND operation_id=?',
                       (status, raw, _sha(raw) if raw is not None else None, error,
                        self.clock(), owner, operation))

    def _blob(self, raw):
        # Different operations can retain identical pixels concurrently. The
        # shared DB writer fence keeps a second writer from seeing a partially
        # written content-addressed file. Reads of completed records stay pure.
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._write_blob(raw)

    def _write_blob(self, raw):
        require(type(raw) is bytes and 0 < len(raw) <= MAX_TOTAL_BYTES,
                'visual_sampling_blob_invalid', 413)
        checksum = _sha(raw)
        path = self.blobs / checksum
        self._ordinary_parents(path)
        if path.exists():
            require(self._read_blob(checksum, len(raw)) == raw, 'visual_sampling_blob_changed', 409)
            return checksum
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            require(self._read_blob(checksum, len(raw)) == raw, 'visual_sampling_blob_changed', 409)
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        return checksum

    def _read_blob(self, checksum, length):
        require(type(checksum) is str and _SHA.fullmatch(checksum)
                and type(length) is int and 0 < length <= MAX_TOTAL_BYTES,
                'visual_sampling_blob_invalid', 409)
        path = self.blobs / checksum
        self._ordinary_parents(path)
        try:
            before = path.stat()
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == length,
                    'visual_sampling_blob_changed', 409)
            raw = path.read_bytes(); after = path.stat()
        except OSError:
            raise ServiceError('visual_sampling_blob_unavailable', 409) from None
        require((before.st_size, before.st_mtime_ns, before.st_ino) ==
                (after.st_size, after.st_mtime_ns, after.st_ino)
                and len(raw) == length and _sha(raw) == checksum,
                'visual_sampling_blob_changed', 409)
        return raw

    def _validate_frames(self, rows, policy, check):
        require(type(rows) is list and 0 < len(rows) <= MAX_FRAMES,
                'visual_sampling_frames_invalid')
        limit = policy.get('max_frames', policy.get('frame_limit', MAX_FRAMES))
        require(type(limit) is int and 1 <= limit <= MAX_FRAMES and len(rows) <= limit,
                'visual_sampling_frame_budget_exceeded', 413)
        byte_limit = policy.get('max_frame_bytes', MAX_IMAGE_BYTES)
        total_limit = policy.get('max_total_bytes', MAX_TOTAL_BYTES)
        require(type(byte_limit) is int and 0 < byte_limit <= MAX_IMAGE_BYTES
                and type(total_limit) is int and 0 < total_limit <= MAX_TOTAL_BYTES,
                'visual_sampling_image_budget_invalid')
        frames, total = [], 0
        for index, row in enumerate(rows):
            check()
            require(type(row) is dict and type(row.get('raw')) is bytes
                    and _number(row.get('timestamp_seconds')) and row['timestamp_seconds'] >= 0,
                    'visual_sampling_frame_invalid')
            raw = row['raw']; mime = image_mime(raw)
            require(len(raw) <= byte_limit, 'visual_sampling_frame_budget_exceeded', 413)
            require(row.get('mime') == mime, 'visual_sampling_frame_mime_changed', 409)
            from PIL import Image
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    require(image.width > 0 and image.height > 0 and image.width * image.height <= 64 * 1024 * 1024,
                            'visual_sampling_frame_dimensions_invalid')
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    width, height, pixel_mode = image.width, image.height, image.mode
                    pixel_sha = _sha(image.convert('RGBA').tobytes())
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                raise ServiceError('visual_sampling_image_invalid', 422) from None
            total += len(raw)
            require(total <= total_limit, 'visual_sampling_image_budget_exceeded', 413)
            original = _document({k: v for k, v in row.items() if k != 'raw'})
            require('sha256' not in original or original['sha256'] == _sha(raw),
                    'visual_sampling_frame_changed', 409)
            require('bytes' not in original or original['bytes'] == len(raw),
                    'visual_sampling_frame_changed', 409)
            frames.append(dict(frame_id=original.get('frame_id', 'sample-%03d' % index),
                sha256=self._blob(raw), bytes=len(raw), mime=mime,
                timestamp_seconds=row['timestamp_seconds'], width=width, height=height,
                decoded_mode=pixel_mode, decoded_rgba_sha256=pixel_sha,
                pixel_scope='Pillow_RGBA8_conversion_without_ICC_color_management', original=original))
        requested_ids = policy.get('frame_ids', policy.get('selection'))
        if requested_ids is not None:
            require(type(requested_ids) is list and 0 < len(requested_ids) <= MAX_FRAMES
                    and all(type(identity) is str and 0 < len(identity) <= 512 for identity in requested_ids)
                    and requested_ids == [f['frame_id'] for f in frames],
                    'visual_sampling_selection_changed', 409)
        return frames

    def prepare(self, owner, operation_id, source_ref, policy, *, provider=None, deadline_at=None):
        key = self._key(owner, operation_id)
        source_ref, policy = _document(source_ref), _document(policy)
        deadline = self.clock() + 3600 if deadline_at is None else deadline_at
        require(_number(deadline), 'visual_sampling_deadline_invalid')
        with self._lock(key) as held:
            if not held:
                # Another process owns this exact identity; no alternate key.
                try:
                    existing = self.get(owner, operation_id)
                except ServiceError as error:
                    if error.code != 'visual_sampling_not_found':
                        raise
                    # Its lock can precede the first durable registration. Do
                    # not turn that brief window into a failure, claim a frozen
                    # request, or start a second resolver. A later read/prepare
                    # reconciles against the authoritative persisted identity.
                    return dict(schema=SCHEMA, operation_id=operation_id,
                        status='running', registration_pending=True,
                        stop_proven=False, manifest_sha256=None)
                require(existing['source_ref'] == source_ref and existing['policy'] == policy
                        and (deadline_at is None or existing['deadline_at'] == deadline_at),
                        'visual_sampling_operation_conflict', 409)
                return existing
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM samples WHERE owner=? AND operation_id=?',
                                 (owner, operation_id)).fetchone()
                if row is None:
                    require(deadline > self.clock(), 'visual_sampling_deadline_expired', 409)
                    request = dict(source_ref=source_ref, policy=policy, deadline_at=deadline)
                    raw = canonical_bytes(request)
                    db.execute('INSERT INTO samples(owner,operation_id,request,request_sha256,status,created_at) '
                               'VALUES(?,?,?,?,?,?)', (owner, operation_id, raw, _sha(raw), 'running', self.clock()))
                else:
                    request = json.loads(row['request'])
                    require(_sha(bytes(row['request'])) == row['request_sha256']
                            and request['source_ref'] == source_ref and request['policy'] == policy
                            and (deadline_at is None or request['deadline_at'] == deadline_at),
                            'visual_sampling_operation_conflict', 409)
                    if row['status'] == 'running':
                        # The lock is free: the previous writer disappeared.
                        db.execute('UPDATE samples SET status=?,error=?,completed_at=? '
                                   'WHERE owner=? AND operation_id=?',
                                   ('interrupted', 'visual_sampling_interrupted', self.clock(), owner, operation_id))
                    # Read only after this connection has committed.
            if row is not None:
                return self.get(owner, operation_id)
            started, cpu_started = self.clock(), time.process_time()

            def check():
                current, frozen = self._row(owner, operation_id)
                require(not current['cancel_requested'], 'visual_sampling_cancelled', 409)
                require(self.clock() < frozen['deadline_at'], 'visual_sampling_deadline_expired', 409)

            try:
                check()
                resolver = provider or self._providers.get(source_ref.get('provider'))
                require(callable(resolver), 'visual_sampling_provider_unavailable', 503)
                provided = resolver(owner, deepcopy(source_ref), deepcopy(policy), check)
                require(type(provided) is dict and type(provided.get('source')) is dict,
                        'visual_sampling_provider_invalid')
                source = _document(provided['source'])
                bundle = None
                if 'media_path' in provided:
                    rows, bundle = self._decode(key, provided, source_ref, check)
                else:
                    rows = provided.get('frames')
                    if 'inventory' in provided:
                        inventory = provided['inventory']
                        reader = provided.get('read_frame')
                        require(type(inventory) is list and callable(reader), 'visual_sampling_inventory_invalid')
                        strategy = policy.get('strategy', 'uniform')
                        selected = select_indices(strategy, len(inventory), policy.get('max_frames', 12),
                                                  indices=policy.get('indices'))
                        rows = []
                        for i in selected:
                            check(); rows.append(reader(i, deepcopy(inventory[i]))); check()
                frames = self._validate_frames(rows, policy, check)
                check()
                manifest = dict(schema=SCHEMA, request_sha256=self._row(owner, operation_id)[0]['request_sha256'],
                    source=source, frames=frames, bundle=bundle,
                    evidence_scope='retained_samples_not_continuous_playback_or_model_observation',
                    usage=dict(wall_seconds=max(0., self.clock() - started),
                               process_cpu_seconds=max(0., time.process_time() - cpu_started),
                               cpu_scope='sampling_python_process_excludes_decoder_subprocess',
                               frames=len(frames), image_bytes=sum(f['bytes'] for f in frames), model_calls=0))
                self._finish(owner, operation_id, 'completed', manifest=manifest)
            except BaseException as error:
                code = (error.code if isinstance(error, ServiceError) else 'visual_sampling_provider_failed')
                status = ('cancelled' if code == 'visual_sampling_cancelled' else
                          'timed_out' if code == 'visual_sampling_deadline_expired' else 'failed')
                self._finish(owner, operation_id, status, error=code)
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
            return self.get(owner, operation_id)

    def _decode(self, key, provided, source_ref, check):
        from .animation_observation import (file_sha256, load_bundle,
            prepare_sampled_media, prepare_temporal_media)
        path = Path(provided['media_path']).absolute()
        self._ordinary_parents(path)
        require(path.is_file() and 0 < path.stat().st_size <= MAX_MEDIA_BYTES,
                'visual_sampling_media_invalid')
        checksum = file_sha256(path)
        if 'media_sha256' in source_ref:
            require(source_ref['media_sha256'] == checksum, 'visual_sampling_media_changed', 409)
        if 'bytes' in source_ref:
            require(source_ref['bytes'] == path.stat().st_size, 'visual_sampling_media_changed', 409)
        args = deepcopy(provided.get('legacy_bundle'))
        require(type(args) is dict and not {'media_path', 'directory', 'check'} & set(args),
                'visual_sampling_decode_policy_invalid')
        folder = self.operations / key
        self._ordinary_parents(folder)
        folder.mkdir(exist_ok=True)
        target = folder / 'bundle'
        check()
        temporal = 'sampling_layers' in args
        prepare = prepare_temporal_media if temporal else prepare_sampled_media
        prepare(path, target, check=check, **args)
        check()
        profile, document, files = load_bundle(target)
        require(file_sha256(path) == checksum, 'visual_sampling_media_changed', 409)
        members, total = {}, 0
        for member in target.rglob('*'):
            self._ordinary_parents(member)
            if member.is_dir():
                continue
            raw = member.read_bytes(); total += len(raw)
            require(0 < len(raw) <= MAX_TOTAL_BYTES and total <= MAX_TOTAL_BYTES,
                    'visual_sampling_bundle_budget_exceeded', 413)
            members[member.relative_to(target).as_posix()] = dict(sha256=self._blob(raw), bytes=len(raw))
        receipt = json.loads(self._read_blob(members['evidence.json']['sha256'], members['evidence.json']['bytes']))
        rows = []
        measured = {}
        if temporal:
            plan_name = receipt['sampling_plan_file']
            plan = json.loads(self._read_blob(members[plan_name]['sha256'], members[plan_name]['bytes']))
            measured = {row['frame_id']: row for row in plan['requests'] if not row['alias']}
        for frame in document['observation']['frames']:
            row = dict(raw=files[frame['file_name']], mime=image_mime(files[frame['file_name']]),
                             timestamp_seconds=frame['time_seconds'], frame_id=frame['frame_id'],
                             file_name=frame['file_name'], origin='decoded_media',
                             clock_scope=('measured_ffmpeg_normalized_pts_not_original_container_timeline'
                                          if temporal else 'requested_decode_grid_not_measured_original_pts'))
            if temporal:
                sample = measured[frame['frame_id']]
                row.update(requested_time_seconds=sample['requested_time_seconds'],
                           pts=sample['pts'], time_base=deepcopy(sample['time_base']))
            rows.append(row)
        return rows, dict(profile=profile, document=document, receipt=receipt, files=members)

    def cancel(self, owner, operation_id):
        key = self._key(owner, operation_id)
        row, _ = self._row(owner, operation_id)
        if row['status'] in _TERMINAL:
            return self.get(owner, operation_id)
        with self._db() as db:
            db.execute('UPDATE samples SET cancel_requested=1 WHERE owner=? AND operation_id=?',
                       (owner, operation_id))
        with self._lock(key) as held:
            if held:
                self._finish(owner, operation_id, 'cancelled', error='visual_sampling_cancelled')
        return self.get(owner, operation_id)

    def _manifest(self, owner, operation):
        row, _ = self._row(owner, operation)
        require(row['status'] == 'completed' and row['manifest'] is not None,
                'visual_sampling_not_completed', 409)
        raw = bytes(row['manifest'])
        require(_sha(raw) == row['manifest_sha256'], 'visual_sampling_manifest_changed', 409)
        manifest = json.loads(raw)
        require(manifest['request_sha256'] == row['request_sha256'],
                'visual_sampling_manifest_changed', 409)
        return manifest

    def read_frames(self, owner, operation_id, *, expected_manifest_sha256=None):
        if expected_manifest_sha256 is not None:
            self.get(owner, operation_id, expected_manifest_sha256=expected_manifest_sha256)
        manifest = self._manifest(owner, operation_id)
        return [dict(deepcopy(frame['original']), raw=self._read_blob(frame['sha256'], frame['bytes']))
                for frame in manifest['frames']]

    def read_frame(self, owner, operation_id, index, *, expected_manifest_sha256=None):
        """Read exactly one retained image, without loading the whole sequence."""
        if expected_manifest_sha256 is not None:
            self.get(owner, operation_id, expected_manifest_sha256=expected_manifest_sha256)
        manifest = self._manifest(owner, operation_id)
        require(type(index) is int and 0 <= index < len(manifest['frames']),
                'visual_sampling_frame_not_found', 404)
        frame = manifest['frames'][index]
        return dict(deepcopy(frame['original']), raw=self._read_blob(frame['sha256'], frame['bytes']))

    def export_bundle(self, owner, operation_id):
        bundle = self._manifest(owner, operation_id).get('bundle')
        require(type(bundle) is dict, 'visual_sampling_bundle_unavailable', 409)
        return {name: self._read_blob(row['sha256'], row['bytes']) for name, row in bundle['files'].items()}

    def bundle_receipt(self, owner, operation_id):
        bundle = self._manifest(owner, operation_id).get('bundle')
        require(type(bundle) is dict, 'visual_sampling_bundle_unavailable', 409)
        return deepcopy(bundle['receipt'])

    def bundle(self, owner, operation_id):
        bundle = self._manifest(owner, operation_id).get('bundle')
        require(type(bundle) is dict, 'visual_sampling_bundle_unavailable', 409)
        members = self.export_bundle(owner, operation_id)
        files = {name[len('inputs/'):]: raw for name, raw in members.items() if name.startswith('inputs/')}
        return bundle['profile'], deepcopy(bundle['document']), files
