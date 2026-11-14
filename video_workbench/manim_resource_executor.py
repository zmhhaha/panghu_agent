"""Private, opt-in Docker execution of frozen Manim resource plans.

This adapter belongs in the existing privileged laboratory executor, never the
web application. It does not pull images, invoke an Agent, retry a recipe, or
publish a method. A durable claim authorizes at most one container creation;
recovery only finds/stops that container. PNGs are collected after its writers
have demonstrably stopped and its container has been removed.

Storage boundary: only this operation's input/output directories are mounted.
Execution time, RAM, processes, scratch tmpfs and collected deliverable bytes
are bounded; the dedicated writable output bind has no filesystem quota.
Bounding all authored-Python diagnostic writes requires a shared executor
storage quota, independently of the PNG delivery budget and later reclamation.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from . import animation_lab_capture as docker
from .manim_generated_assets import (MAX_ENVELOPE_BYTES, decode_generated_assets,
    freeze_generated_assets, scope)
from .manim_resource import collect_resource_files, manifest_path, require
from .manim_resource_job import validate_resource_job


IMAGE = ('arm-cluster-master:5000/video-manim-resource:latest@sha256:'
         '285b9c0919c1ec3d81ebeaf729d4ce349d304470b851f0124f67b45d1508efc8')
SCHEMA = 'video.manim-resource-execution.v1'
_TABLE = 'manim_resource_operations'
_TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
_FIELDS = frozenset({'state', 'image_id', 'container_id', 'submit_intent',
                    'exit_proven', 'cancel_requested', 'cleanup', 'result', 'result_sha256'})


def _plain(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
        for p in (path, *path.parents)) and not any(c in str(path) for c in ',\r\n\0'),
        'manim_resource_executor_path_invalid', 409)
    return path


def _read(path, maximum):
    path = _plain(path)
    metadata = path.stat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
        and 0 < metadata.st_size <= maximum, 'manim_resource_executor_artifact_invalid', 409)
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) == metadata.st_size, 'manim_resource_executor_artifact_changed', 409)
    return raw


def _write(path, raw):
    path = _plain(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class ManimResourceExecutor:
    """One frozen operation, one external execution; lookups are read-only.

    Constructing a disabled adapter has no filesystem or Docker effects.
    ``cancel`` records intent; the active executor alone stops its container.
    ``recover`` never starts a container. An interrupted operation without a
    durably observed successful exit cannot be reported completed, even when
    a container's default exit code or existing files look successful.
    """

    def __init__(self, root, *, image=IMAGE, enabled=False):
        match = docker._IMAGE.fullmatch(image) if type(image) is str else None
        require(match is not None, 'manim_resource_executor_image_pin_required')
        require(type(enabled) is bool, 'manim_resource_executor_config_invalid')
        self.root, self.image = _plain(root), image
        self.repository, self.image_digest = match[1], match[2]
        self.enabled = enabled
        self.ledger = self.root / 'manim-resource-ledger.sqlite3'

    def _directory(self, operation_id):
        return _plain(self.root / 'operations' / hashlib.sha256(operation_id.encode()).hexdigest())

    def _connect(self, *, create=False, readonly=False):
        _plain(self.ledger)
        if not self.ledger.exists():
            require(create, 'manim_resource_executor_not_found', 404)
            operations = _plain(self.root / 'operations')
            require(not operations.exists() or not any(operations.iterdir()),
                'manim_resource_executor_ledger_missing', 409)
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            metadata = self.ledger.stat()
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                'manim_resource_executor_ledger_invalid', 409)
        db = sqlite3.connect(self.ledger.as_uri() + '?mode=ro', uri=True, timeout=10,
            isolation_level=None) if readonly else sqlite3.connect(str(self.ledger), timeout=10,
            isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if not readonly:
                db.execute('PRAGMA synchronous=FULL')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require(tables <= {_TABLE}, 'manim_resource_executor_database_not_isolated', 409)
            if create:
                db.execute('''CREATE TABLE IF NOT EXISTS manim_resource_operations (
                    operation_id TEXT PRIMARY KEY, plan BLOB NOT NULL, plan_sha256 TEXT NOT NULL,
                    token TEXT NOT NULL, name TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                    image_id TEXT, container_id TEXT, submit_intent INTEGER NOT NULL DEFAULT 0,
                    exit_proven INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cleanup BLOB, result BLOB, result_sha256 TEXT)''')
            else:
                require(_TABLE in tables, 'manim_resource_executor_ledger_invalid', 409)
            return db
        except BaseException:
            db.close()
            raise

    def _claim(self, plan):
        raw, operation_id = canonical_bytes(plan), plan['scope']['operation_id']
        db = self._connect(create=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE operation_id=?', (operation_id,)).fetchone()
            if row is not None:
                require(bytes(row['plan']) == raw and row['plan_sha256'] == plan['sha256'],
                    'manim_resource_executor_operation_conflict', 409)
                db.execute('COMMIT')
                return False
            token = uuid4().hex
            name = 'manim-resource-' + hashlib.sha256(operation_id.encode()).hexdigest()[:20] + '-' + token[:12]
            db.execute('INSERT INTO ' + _TABLE +
                ' (operation_id,plan,plan_sha256,token,name,state) VALUES (?,?,?,?,?,?)',
                (operation_id, raw, plan['sha256'], token, name, 'claimed'))
            db.execute('COMMIT')
            return True
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def _row(self, binding):
        binding = scope(**binding)
        db = self._connect(readonly=True)
        try:
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE operation_id=?',
                (binding['operation_id'],)).fetchone()
            require(row is not None, 'manim_resource_executor_not_found', 404)
            row = dict(row)
            try:
                plan = json.loads(bytes(row['plan']))
                require(canonical_bytes(plan) == bytes(row['plan']),
                    'manim_resource_executor_binding_changed', 409)
                plan = validate_resource_job(plan, expected_sha256=row['plan_sha256'], image=self.image)
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise ServiceError('manim_resource_executor_binding_changed', 409) from None
            require(plan['scope'] == binding, 'manim_resource_executor_not_found', 404)
            require(row['name'] == 'manim-resource-' + hashlib.sha256(binding['operation_id'].encode()).hexdigest()[:20]
                + '-' + row['token'][:12] and len(row['token']) == 32
                and all(c in '0123456789abcdef' for c in row['token']),
                'manim_resource_executor_identity_changed', 409)
            return row, plan
        finally:
            db.close()

    def _update(self, binding, **changes):
        require(bool(changes) and set(changes) <= _FIELDS, 'manim_resource_executor_update_invalid', 409)
        db = self._connect()
        try:
            db.execute('UPDATE ' + _TABLE + ' SET ' + ','.join(k + '=?' for k in changes)
                + ' WHERE operation_id=?', (*changes.values(), binding['operation_id']))
        finally:
            db.close()

    @contextmanager
    def _lock(self, operation_id):
        locks = _plain(self.root / 'locks')
        locks.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = _plain(locks / (hashlib.sha256(operation_id.encode()).hexdigest() + '.lock'))
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        locked = False
        try:
            require(os.fstat(descriptor).st_nlink == 1, 'manim_resource_executor_lock_invalid', 409)
            if os.name == 'nt':
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b'0')
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError:
                    pass
            yield locked
        finally:
            if locked:
                if os.name == 'nt':
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _cancelled(self, binding):
        return bool(self._row(binding)[0]['cancel_requested'])

    def _attach(self, container_id, directory, binding, deadline):
        require(not self._cancelled(binding), 'manim_resource_executor_cancel_requested', 409)
        if time.monotonic() >= deadline:
            raise TimeoutError('manim_resource_executor_timeout')
        path = directory / 'execution.log'
        with path.open('xb') as log:
            process = subprocess.Popen(['docker', 'container', 'start', '--attach', container_id],
                stdout=log, stderr=subprocess.STDOUT)
            try:
                while process.poll() is None:
                    require(not self._cancelled(binding), 'manim_resource_executor_cancel_requested', 409)
                    require(path.stat().st_size <= docker.MAX_LOG_BYTES, 'manim_resource_executor_log_limit', 413)
                    if time.monotonic() >= deadline:
                        raise TimeoutError('manim_resource_executor_timeout')
                    time.sleep(.05)
                require(path.stat().st_size <= docker.MAX_LOG_BYTES, 'manim_resource_executor_log_limit', 413)
                require(process.returncode == 0, 'manim_resource_executor_container_failed', 409)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                log.flush()
                os.fsync(log.fileno())

    def _ownership(self, row):
        return dict(name=row['name'], token=row['token'], capture=row['plan_sha256'],
            image_id=row['image_id'], container_id=row['container_id'])

    def _collect(self, row, plan):
        stop = StopEvidence(execution_id=plan['scope']['operation_id'],
            all_writers_stopped=True, resource_ids=(row['container_id'] or row['name'],))
        directory = self._directory(plan['scope']['operation_id'])
        require(_read(directory / 'input/request.json', 1024 * 1024) == canonical_bytes(plan['request']),
            'manim_resource_executor_input_changed', 409)
        root = directory / 'output/attempt/resource'
        checksum = hashlib.sha256(_read(root / manifest_path(plan['request']['resource_id']), 128 * 1024)).hexdigest()
        files = collect_resource_files(root, plan['request'], expected_manifest_sha256=checksum,
            runtime_image_digest=self.image_digest)
        return freeze_generated_assets(files, plan['request'], **plan['scope'],
            runtime_image_digest=self.image_digest, stop_evidence=stop)

    def _finish(self, binding, *, status, stopped, error=None, cleanup=None, assets=None):
        row, plan = self._row(binding)
        result = dict(schema=SCHEMA, scope=deepcopy(binding), plan_sha256=plan['sha256'],
            image=self.image, status=status, stop_proven=stopped, generated_assets=assets,
            error=error, cancel_requested=bool(row['cancel_requested']), cleanup=cleanup,
            container_id=row['container_id'], container_name=row['name'], automatic_retry=False)
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT state,cancel_requested FROM ' + _TABLE + ' WHERE operation_id=?',
                (binding['operation_id'],)).fetchone()
            require(current['state'] not in _TERMINAL, 'manim_resource_executor_already_terminal', 409)
            result['cancel_requested'] = bool(current['cancel_requested'])
            if current['cancel_requested'] and stopped:
                result.update(status='cancelled', generated_assets=None,
                    error='manim_resource_executor_cancel_requested')
            raw = canonical_bytes(result)
            db.execute('UPDATE ' + _TABLE + ' SET state=?,cleanup=?,result=?,result_sha256=? WHERE operation_id=?',
                (result['status'], canonical_bytes(cleanup), raw, hashlib.sha256(raw).hexdigest(), binding['operation_id']))
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()
        return result

    def execute(self, plan):
        require(self.enabled, 'manim_resource_executor_disabled', 503)
        plan = validate_resource_job(plan, expected_sha256=plan.get('sha256') if type(plan) is dict else None,
            image=self.image)
        binding = plan['scope']
        if not self._claim(plan):
            return self.lookup(**binding)
        with self._lock(binding['operation_id']) as locked:
            require(locked, 'manim_resource_executor_operation_busy', 409)
            if self._row(binding)[0]['state'] in _TERMINAL:
                return self.lookup(**binding)
            return self._execute_claimed(binding, plan)

    def _execute_claimed(self, binding, plan):
        row, _ = self._row(binding)
        error, successful = None, False
        cleanup = dict(status='not_submitted', container_id=None)
        directory = self._directory(binding['operation_id'])
        deadline = time.monotonic() + plan['timeout_seconds']
        try:
            require(not self._cancelled(binding), 'manim_resource_executor_cancel_requested', 409)
            directory.mkdir(mode=0o700, parents=True)
            input_root, output_root = directory / 'input', directory / 'output'
            input_root.mkdir()
            output_root.mkdir()
            _write(input_root / 'request.json', canonical_bytes(plan['request']))
            for path in (input_root, input_root / 'request.json', output_root):
                docker._ownership(path, directory=path.is_dir())
            self._update(binding, state='preparing')
            inspected = json.loads(docker._checked(
                ['image', 'inspect', '--format', docker._IMAGE_FORMAT, self.image], deadline))
            require(type(inspected) is dict and type(inspected.get('image_id')) is str
                and inspected['image_id'].startswith('sha256:')
                and docker._SHA.fullmatch(inspected['image_id'][7:])
                and self.repository + '@' + self.image_digest in inspected.get('repo_digests', []),
                'manim_resource_executor_image_changed', 409)
            require(not self._cancelled(binding), 'manim_resource_executor_cancel_requested', 409)
            # Persist the exact config image and ownership before an ambiguous
            # create response is possible. Recovery can use the original name.
            self._update(binding, image_id=inspected['image_id'], submit_intent=1, state='submitted')
            row, _ = self._row(binding)
            arguments = ['container', 'create', '--name', row['name'],
                '--label', docker._OWNER + '=' + row['token'], '--label', docker._CAPTURE + '=' + plan['sha256'],
                '--pull=never', '--network=none', '--read-only', '--user=10001:10001',
                '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=2g',
                '--pids-limit=256', '--restart=no', '--log-driver=none', '--init', '--stop-timeout=3',
                '--tmpfs=/tmp:rw,nosuid,nodev,size=64m', '--tmpfs=/work:rw,nosuid,nodev,size=256m',
                '--workdir=/work', '--env', 'MANIM_RESOURCE_ISOLATED_WORKER=1',
                '--env', 'MANIM_RESOURCE_IMAGE_DIGEST=' + self.image_digest,
                '--env', 'TMPDIR=/work', '--env', 'XDG_CACHE_HOME=/work/cache',
                '--env', 'XDG_CONFIG_HOME=/work/config',
                '--mount', 'type=bind,source=' + str(input_root) + ',target=/input,readonly',
                '--mount', 'type=bind,source=' + str(output_root) + ',target=/output',
                '--entrypoint=python', inspected['image_id'], '-m', 'video_workbench.manim_resource_worker',
                '--request', '/input/request.json', '--output', '/output/attempt']
            container_id = docker._checked(arguments, deadline)
            require(docker._SHA.fullmatch(container_id), 'manim_resource_executor_container_identity_invalid', 409)
            self._update(binding, container_id=container_id)
            row, _ = self._row(binding)
            require(docker._owned(docker._container(container_id, deadline), **self._ownership(row)),
                'manim_resource_executor_container_identity_invalid', 409)
            require(not self._cancelled(binding), 'manim_resource_executor_cancel_requested', 409)
            self._update(binding, state='running')
            self._attach(container_id, directory, binding, deadline)
            final = docker._container(container_id, deadline)
            require(docker._owned(final, **self._ownership(row)) and final['running'] is False
                and final['exit_code'] == 0 and final['oom_killed'] is False,
                'manim_resource_executor_exit_invalid', 409)
            self._update(binding, exit_proven=1)
            successful = True
        except Exception as exc:
            error = str(exc) if isinstance(exc, (ServiceError, ValueError, TimeoutError)) else type(exc).__name__
        # Keep a separate cleanup deadline, including after execution timeout.
        row, _ = self._row(binding)
        stopped = not row['submit_intent']
        if row['submit_intent']:
            try:
                cleanup = docker._cleanup(**self._ownership(row), deadline=time.monotonic() + 20)
                stopped = True
            except Exception:
                cleanup = dict(status='unconfirmed', container_id=row['container_id'])
                error = error or 'manim_resource_executor_cleanup_unconfirmed'
        assets = None
        if successful and stopped and not self._cancelled(binding):
            try:
                assets = self._collect(row, plan)
            except Exception as exc:
                error = str(exc) if isinstance(exc, (ServiceError, ValueError)) else type(exc).__name__
        status = 'needs_recovery' if not stopped else 'completed' if assets is not None and not error else 'failed'
        return self._finish(binding, status=status, stopped=stopped, error=error,
            cleanup=cleanup, assets=assets)

    def lookup(self, *, owner, effect_id, experiment_id, operation_id):
        binding = scope(owner, effect_id, experiment_id, operation_id)
        row, plan = self._row(binding)
        if row['result'] is None:
            return dict(schema=SCHEMA, scope=binding, plan_sha256=plan['sha256'], image=self.image,
                status='needs_recovery' if row['state'] == 'needs_recovery' else 'running',
                stop_proven=False, generated_assets=None, error=None,
                cancel_requested=bool(row['cancel_requested']), container_id=row['container_id'],
                container_name=row['name'], cleanup=None, automatic_retry=False)
        raw = bytes(row['result'])
        require(0 < len(raw) <= MAX_ENVELOPE_BYTES + 128 * 1024
            and hashlib.sha256(raw).hexdigest() == row['result_sha256'],
            'manim_resource_executor_result_changed', 409)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ServiceError('manim_resource_executor_result_changed', 409) from None
        require(type(result) is dict and canonical_bytes(result) == raw and result.get('schema') == SCHEMA
            and result.get('scope') == binding and result.get('plan_sha256') == plan['sha256']
            and result.get('image') == self.image and result.get('status') == row['state']
            and result.get('container_id') == row['container_id'] and result.get('container_name') == row['name'],
            'manim_resource_executor_result_binding_changed', 409)
        if result['status'] == 'completed':
            assets = result['generated_assets']
            require(result['stop_proven'] is True and result['error'] is None and row['exit_proven'] == 1
                and type(assets) is dict and assets.get('request') == plan['request'],
                'manim_resource_executor_completion_unproven', 409)
            decode_generated_assets(assets, expected_sha256=assets.get('sha256'),
                **binding, runtime_image_digest=self.image_digest)
            require(assets['writer_stop']['resource_ids'] == [row['container_id'] or row['name']],
                'manim_resource_executor_stop_binding_changed', 409)
        else:
            require(result['generated_assets'] is None, 'manim_resource_executor_incomplete_assets', 409)
        if row['state'] not in _TERMINAL:
            result['cancel_requested'] = bool(row['cancel_requested'])
        return deepcopy(result)

    def cancel(self, *, owner, effect_id, experiment_id, operation_id):
        binding = scope(owner, effect_id, experiment_id, operation_id)
        self._row(binding)
        db = self._connect()
        try:
            db.execute('UPDATE ' + _TABLE + " SET cancel_requested=1 WHERE operation_id=? AND state NOT IN ('completed','failed','cancelled')",
                (operation_id,))
        finally:
            db.close()
        result = self.lookup(**binding)
        result['cancel_requested'] = bool(self._row(binding)[0]['cancel_requested'])
        return result

    def recover(self, *, owner, effect_id, experiment_id, operation_id):
        require(self.enabled, 'manim_resource_executor_disabled', 503)
        binding = scope(owner, effect_id, experiment_id, operation_id)
        row, plan = self._row(binding)
        if row['state'] in _TERMINAL:
            return self.lookup(**binding)
        with self._lock(operation_id) as locked:
            if not locked:
                return self.lookup(**binding)
            row, plan = self._row(binding)
            if row['state'] in _TERMINAL:
                return self.lookup(**binding)
            stopped, assets = not row['submit_intent'], None
            cleanup = dict(status='not_submitted', container_id=None)
            error = 'manim_resource_executor_interrupted'
            if row['submit_intent']:
                try:
                    cleanup = docker._cleanup(**self._ownership(row), deadline=time.monotonic() + 20)
                    stopped = True
                except Exception:
                    cleanup = dict(status='unconfirmed', container_id=row['container_id'])
                    error = 'manim_resource_executor_cleanup_unconfirmed'
            if stopped and row['exit_proven'] and not row['cancel_requested']:
                try:
                    assets = self._collect(row, plan)
                    error = None
                except Exception as exc:
                    error = str(exc) if isinstance(exc, (ServiceError, ValueError)) else type(exc).__name__
            return self._finish(binding, status='needs_recovery' if not stopped else
                'completed' if assets is not None else 'failed', stopped=stopped,
                error=error, cleanup=cleanup, assets=assets)
