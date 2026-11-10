"""Owner-scoped, bounded creation runs for the developer animation laboratory.

Reading a run never drives it. An explicit advance performs one finite step;
the host may call it from a bounded pump. Claims precede every paid submission
and render. Unknown outcomes only reconcile the original operation identity.
These databases and directories are separate from historic experiment records.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import UUID, uuid4

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from . import animation_experiments as experiments
from .animation_observation import file_sha256, load_bundle, prepare_temporal_media
from .animation_polish import AnimationPolishCoordinator, REMOTE_ACTIVE
from .animation_revision_store import AnimationRevisionStore


SCHEMA = 'video.animation-creation-run.v1'
LIMITS = dict(initial_creations=1, reviews=1, creator_revisions=1, renders=2)
TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
_FIXED_GUIDANCE = (
    'Preserve the legibility of fixed explanations, labels and state indicators throughout the '
    'fragment. Plan motion paths and occupied regions together: moving objects must not obscure '
    'fixed explanations or unrelated identities. Use your own composition and expression; '
    'this is a visibility requirement, not a prescribed animation template.')


def _require(value, code='animation_creation_request_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _owner(value):
    _require(type(value) is str and 1 <= len(value) <= 200
             and not any(ord(c) < 32 for c in value), 'animation_creation_owner_invalid', 401)
    return value


@contextmanager
def _run_lock(path):
    """A crash releases this cross-process lock; no stale lease can submit twice."""
    with path.open('a+b') as handle:
        # Windows forbids reading a byte locked by a different open handle.
        # Inspect metadata before attempting the nonblocking byte-range lock.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    pass
            yield acquired
        finally:
            if acquired:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _CancellationGateway:
    """Recheck persistent cancellation at the actual admission boundary."""
    def __init__(self, gateway, cancelled):
        self.gateway, self.cancelled = gateway, cancelled

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def prepare_submission(self, operation_id, document, deadline_at, refs):
        _require(not self.cancelled(), 'animation_creation_cancel_requested', 409)
        prepare = getattr(self.gateway, 'prepare_submission', None)
        admit = (prepare(operation_id, document, deadline_at, refs) if callable(prepare)
                 else lambda: self.gateway.submit(operation_id, document, deadline_at, refs))

        def checked_admit():
            _require(not self.cancelled(), 'animation_creation_cancel_requested', 409)
            return admit()
        return checked_admit


class _CancellationRenderer:
    def __init__(self, renderer, cancelled):
        self.renderer, self.cancelled = renderer, cancelled

    def __getattr__(self, name):
        return getattr(self.renderer, name)

    def render(self, operation_id, source, document):
        _require(not self.cancelled(), 'animation_creation_cancel_requested', 409)
        return self.renderer.render(operation_id, source, document)


class AnimationCreationService:
    def __init__(self, root, *, creation_gateway, review_gateway, renderer):
        self.root = Path(root).absolute()
        _require(not any(p.is_symlink() for p in (self.root, *self.root.parents)),
                 'animation_creation_root_invalid')
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_root = self.root / 'creation-runs'
        self.runs_root.mkdir(exist_ok=True)
        self.path = self.root / 'creation-service.sqlite3'
        _require(not self.path.is_symlink() and not self.runs_root.is_symlink(),
                 'animation_creation_root_invalid')
        self.creation_gateway, self.review_gateway, self.renderer = creation_gateway, review_gateway, renderer
        _require(getattr(creation_gateway, 'profile', None) == creation.PROFILE
                 and getattr(review_gateway, 'profile', None) == visual.REVIEW_SEQUENCE_PROFILE,
                 'animation_creation_profile_mismatch', 409)
        self.revisions = AnimationRevisionStore(self.root / 'creation-polish.sqlite3')
        with self._db() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= {'creation_runs'}, 'animation_creation_database_not_isolated', 409)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS creation_runs ('
                       'id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, '
                       'request_hash TEXT NOT NULL, experiment_id TEXT NOT NULL, document TEXT NOT NULL, '
                       'UNIQUE(owner, request_id))')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _read(self, owner, run_id):
        _owner(owner)
        _require(_uuid(run_id), 'animation_creation_not_found', 404)
        with self._db() as db:
            row = db.execute('SELECT document FROM creation_runs WHERE id=? AND owner=?',
                             (run_id, owner)).fetchone()
        _require(row is not None, 'animation_creation_not_found', 404)
        run = json.loads(row['document'])
        _require(run['id'] == run_id and run['owner'] == owner
                 and run['frozen_sha256'] == digest(run['frozen']),
                 'animation_creation_frozen_input_changed', 409)
        creation.validate_creation_input(run['frozen']['creation_input'])
        return run

    def _save(self, run):
        # cancel() is allowed to persist intent while advance holds the run lock.
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT document FROM creation_runs WHERE id=? AND owner=?',
                               (run['id'], run['owner'])).fetchone()
            _require(prior is not None, 'animation_creation_not_found', 404)
            retained = json.loads(prior['document'])
            _require(run['frozen_sha256'] == retained['frozen_sha256']
                     and run['frozen'] == retained['frozen'], 'animation_creation_frozen_input_changed', 409)
            run['cancel_requested'] = bool(run['cancel_requested'] or retained['cancel_requested'])
            if run['cancel_requested'] and run['state'] not in TERMINAL:
                run['state'] = 'cancelling'
            run['updated_at'] = time.time()
            db.execute('UPDATE creation_runs SET document=? WHERE id=?',
                       (canonical_bytes(run).decode('utf-8'), run['id']))
        return run

    def _directory(self, run):
        directory = self.runs_root / run['id']
        _require(directory.is_dir() and not any(p.is_symlink() for p in (directory, *directory.parents)),
                 'animation_creation_directory_invalid', 409)
        return directory

    def _write(self, run, name, value):
        path, raw = self._directory(run) / name, canonical_bytes(value)
        if path.exists():
            _require(not path.is_symlink() and path.is_file() and path.stat().st_nlink == 1
                     and path.read_bytes() == raw, 'animation_creation_artifact_changed', 409)
        else:
            with path.open('xb') as stream:
                stream.write(raw)
        return path

    def start(self, owner, body):
        _owner(owner)
        _require(type(body) is dict and set(body) == {
            'request_id', 'experiment_id', 'definition_identity', 'input_role'}
            and _uuid(body['request_id']) and type(body['input_role']) is str
            and body['input_role'] in {'development', 'held_out'})
        request_hash = digest(body)
        # An idempotent replay must not resolve a new current definition.
        with self._db() as db:
            prior = db.execute('SELECT id,request_hash FROM creation_runs WHERE owner=? AND request_id=?',
                               (owner, body['request_id'])).fetchone()
        if prior:
            _require(prior['request_hash'] == request_hash, 'animation_creation_idempotency_conflict', 409)
            return self.get(owner, prior['id'])
        try:
            target = experiments.definition(body['experiment_id'], body['definition_identity'])
            bounds = experiments.constraints(body['input_role'], target['id'], target['identity'])
        except experiments.ExperimentError as error:
            raise ServiceError(error.code, error.status) from None
        _require(body['definition_identity'] == target['identity'],
                 'animation_experiment_definition_changed', 409)
        run_id = str(uuid4())
        document = creation.validate_creation_input(dict(schema=creation.INPUT_SCHEMA,
            creation_id='creation-' + run_id, goal=target['goal'] + '\n' + target['exploration'],
            constraints=bounds, assets={}, prompt_revision='lab-create-v2-r1', guidance=_FIXED_GUIDANCE,
            base_source=None, review=None))
        frozen = dict(definition=target, creation_input=document, input_role=body['input_role'])
        now = time.time()
        run = dict(schema=SCHEMA, id=run_id, owner=owner, request_id=body['request_id'],
            input_role=body['input_role'], experiment=dict(id=target['id'], identity=target['identity'],
                                                        title=target['title']),
            frozen=frozen, frozen_sha256=digest(frozen), state='queued', stage='initial_creation',
            limits=deepcopy(LIMITS), versions=[], review=None, error=None,
            aesthetic_status='unreviewed', cancel_requested=False, created_at=now, updated_at=now,
            deadline_at=now + 3600, operations={}, lineage_id=None)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT id,request_hash FROM creation_runs WHERE owner=? AND request_id=?',
                               (owner, body['request_id'])).fetchone()
            if prior:
                _require(prior['request_hash'] == request_hash, 'animation_creation_idempotency_conflict', 409)
                existing_id = prior['id']
            else:
                directory = self.runs_root / run_id
                directory.mkdir()
                self._write(run, 'creation-input.json', document)
                self._write(run, 'definition.json', target)
                db.execute('INSERT INTO creation_runs VALUES (?,?,?,?,?,?)',
                    (run_id, owner, body['request_id'], request_hash, target['id'],
                     canonical_bytes(run).decode('utf-8')))
                existing_id = run_id
        return self.get(owner, existing_id)

    @staticmethod
    def _public(run):
        fields = ('schema', 'id', 'request_id', 'input_role', 'experiment', 'state', 'stage',
                  'limits', 'versions', 'review', 'error', 'aesthetic_status', 'created_at', 'updated_at')
        result = deepcopy({key: run[key] for key in fields})
        for version in result['versions']:
            for key in ('source_path', 'source_file_sha256', 'render_operation_id'):
                version.pop(key, None)
            prefix = '/api/creation-runs/' + run['id'] + '/versions/' + version['id']
            version['source_url'] = prefix + '/source'
            if version.get('sha256'):
                version['media_url'] = prefix + '/media'
        return result

    def get(self, owner, run_id):
        return self._public(self._read(owner, run_id))

    def list(self, owner, experiment_id=None):
        _owner(owner)
        _require(experiment_id is None or type(experiment_id) is str)
        with self._db() as db:
            rows = db.execute('SELECT id FROM creation_runs WHERE owner=? '
                + ('AND experiment_id=? ' if experiment_id is not None else '') + 'ORDER BY rowid DESC',
                (owner, experiment_id) if experiment_id is not None else (owner,)).fetchall()
        return [self.get(owner, row['id']) for row in rows]

    def pending(self, limit=100):
        """Internal pump identities only; does not call an execution adapter."""
        _require(type(limit) is int and 1 <= limit <= 1000)
        with self._db() as db:
            # No owner crosses an HTTP trust boundary through this method.
            rows = db.execute('SELECT id,owner,document FROM creation_runs ORDER BY rowid').fetchall()
        return [dict(owner=row['owner'], id=row['id']) for row in rows
                if json.loads(row['document'])['state'] not in TERMINAL][:limit]

    def _cancelled(self, run):
        return self._read(run['owner'], run['id'])['cancel_requested']

    @staticmethod
    def _operation(run, stage):
        return run['operations'].setdefault(stage, dict(id=run['id'] + ':' + stage,
            status='reserved', job_id=None))

    def _failed(self, run, code):
        run.update(state='cancelled' if self._cancelled(run) else 'failed', error=dict(code=code))
        return self._save(run)

    def _initial_creation(self, run):
        operation = self._operation(run, 'initial_creation')
        gateway, document = self.creation_gateway, run['frozen']['creation_input']
        if operation['status'] == 'reserved':
            self._save(run)
            gateway.preflight(run['deadline_at'])
            refs = gateway.upload(document, None)
            checked = _CancellationGateway(gateway, lambda: self._cancelled(run))
            admit = checked.prepare_submission(operation['id'], document, run['deadline_at'], refs)
            operation['status'] = 'submitting'
            self._save(run)
            try:
                job = admit()
                _require(type(job) is dict and _uuid(job.get('job_id')),
                         'animation_creation_admission_invalid', 502)
            except ServiceError as error:
                if error.status < 500:
                    operation.update(status='failed', error=error.code)
                    return self._failed(run, error.code)
                operation.update(status='submission_unknown', error=error.code)
                run.update(state='submission_unknown', error=dict(code=error.code))
                return self._save(run)
            except Exception:
                operation.update(status='submission_unknown', error='animation_creation_submission_unknown')
                run.update(state='submission_unknown', error=dict(code=operation['error']))
                return self._save(run)
            operation.update(status='submitted', job_id=job['job_id'])
            return self._save(run)
        if operation['status'] in {'submitting', 'submission_unknown'}:
            try:
                job = gateway.lookup(operation['id'])
                _require(type(job) is dict and _uuid(job.get('job_id')),
                         'animation_creation_admission_invalid', 502)
            except ServiceError as error:
                operation.update(status='submission_unknown', error=error.code)
                run.update(state='submission_unknown', error=dict(code=error.code))
                return self._save(run)
            operation.update(status='submitted', job_id=job['job_id'])
            run.update(state='running', error=None)
            return self._save(run)
        job = gateway.poll(operation['job_id'])
        if job['state'] in REMOTE_ACTIVE:
            operation['status'] = 'running'
            return self._save(run)
        if job['state'] != 'completed':
            operation['status'] = 'cancelled' if job['state'] == 'cancelled' else 'failed'
            return self._failed(run, 'animation_creation_upstream_' + job['state'])
        result = creation.validate_creation_result(
            gateway.collect(operation['job_id'], digest(document), document), digest(document), document)
        self._write(run, 'original-source.json', result)
        operation.update(status='completed', result=result)
        run['versions'] = [dict(id='original', status='generated', technical_status='pending',
            source_sha256=result['source_sha256'], source_path='original-source.json',
            source_file_sha256=digest(result), sha256=None)]
        run.update(stage='initial_render', error=None)
        return self._save(run)

    def _render_version(self, run, operation, source, *, first):
        try:
            receipt = (self.renderer.render(operation['id'], source, run['frozen']['creation_input'])
                       if first else self.renderer.lookup(operation['id']))
        except ServiceError as error:
            operation['status'] = 'submission_unknown'
            run.update(state='submission_unknown', error=dict(code=error.code))
            return None
        if receipt is None:
            operation['status'] = 'submission_unknown'
            run['state'] = 'submission_unknown'
            return None
        checked = AnimationPolishCoordinator._checked_verification(receipt, operation['id'], source)
        operation.update(status='completed' if checked['technical_passed'] else 'failed', result=checked)
        return checked

    def _attach_media(self, run, version, operation_id, receipt):
        _require(receipt['technical_passed'] and len(receipt['media_versions']) == 1,
                 'animation_creation_render_media_invalid', 502)
        path = self.renderer.media_path(operation_id)
        checksum = file_sha256(path)
        _require(checksum == receipt['media_versions'][0]['sha256'],
                 'animation_creation_render_media_changed', 409)
        version.update(status='rendered', technical_status='passed', sha256=checksum,
                       render_operation_id=operation_id)

    def _initial_render(self, run):
        operation = self._operation(run, 'initial_render')
        first = operation['status'] == 'reserved'
        if first:
            operation.update(status='submitted', job_id=operation['id'])
            self._save(run)
            if self._cancelled(run):
                operation.update(status='cancelled', cancelled_before_render=True)
                run['state'] = 'cancelled'
                return self._save(run)
        source = run['operations']['initial_creation']['result']
        receipt = self._render_version(run, operation, source, first=first)
        if receipt is None:
            return self._save(run)
        if not receipt['technical_passed']:
            run['versions'][0].update(status='failed', technical_status='failed')
            return self._failed(run, 'animation_creation_initial_render_failed')
        self._attach_media(run, run['versions'][0], operation['id'], receipt)
        lineage = self.revisions.create_lineage(run['owner'], run['id'], 'original',
            source['source_sha256'], run['id'])
        run.update(lineage_id=lineage['id'], state='running', stage='review', error=None)
        return self._save(run)

    def _review_directory(self, run):
        directory = self._directory(run) / 'review-evidence'
        source = run['operations']['initial_creation']['result']
        if directory.exists():
            profile, document, _ = load_bundle(directory)
            _require(profile == visual.REVIEW_SEQUENCE_PROFILE
                     and document['source_sha256'] == source['source_sha256']
                     and document['media']['sha256'] == run['versions'][0]['sha256'],
                     'animation_creation_review_binding_changed', 409)
            return directory
        media = self.renderer.media_path(run['operations']['initial_render']['id'])
        _require(file_sha256(media) == run['versions'][0]['sha256'],
                 'animation_creation_render_media_changed', 409)
        duration = run['frozen']['creation_input']['constraints']['duration_seconds']
        # A declared, neutral sampling grid, never a preselected defect or answer.
        if duration <= 96:
            layers = [dict(start_seconds=0, end_seconds=duration, sample_fps=min(3, 24 / duration))]
        else:
            layers = [dict(start_seconds=start, end_seconds=start + 8, sample_fps=1)
                      for start in (0, duration / 2 - 4, duration - 8)]
        staging = self._directory(run) / ('review-preparing-' + str(uuid4()))
        prepare_temporal_media(media, staging, clip_id='fragment-' + run['id'],
            goal=run['frozen']['creation_input']['goal'] + '\n' + _FIXED_GUIDANCE,
            source_sha256=source['source_sha256'], sampling_layers=layers, frame_limit=24,
            review_profile=visual.REVIEW_SEQUENCE_PROFILE)
        staging.rename(directory)
        return self._review_directory(run)

    def _polish(self, run):
        directory = self._review_directory(run)
        source = run['operations']['initial_creation']['result']
        checked_creator = _CancellationGateway(self.creation_gateway, lambda: self._cancelled(run))
        checked_reviewer = _CancellationGateway(self.review_gateway, lambda: self._cancelled(run))
        checked_renderer = _CancellationRenderer(self.renderer, lambda: self._cancelled(run))
        coordinator = AnimationPolishCoordinator(self.revisions, checked_reviewer, checked_creator, checked_renderer)
        outcome = coordinator.step(run['lineage_id'], review_directory=directory,
            creation_input=run['frozen']['creation_input'], base_source=dict(source_revision='original',
                **{key: source[key] for key in ('source_sha256', 'engine', 'files')}),
            deadline_at=run['deadline_at'])
        run['stage'] = outcome['stage']
        if outcome.get('last_error') == 'animation_creation_cancel_requested':
            # The wrapper proves this particular render call stopped before
            # crossing its I/O boundary; this is not a remote-absence claim.
            self.revisions.finish(outcome['operation_id'], 'cancelled',
                                  result=dict(cancelled_before_render=True))
            run['state'] = 'cancelled'
            return self._save(run)
        operations = self.revisions.operations(run['lineage_id'])
        review = next((op for op in operations if op['stage'] == 'review' and op['status'] == 'completed'), None)
        if review:
            result = review['result']['review']
            run['review'] = {key: deepcopy(result[key]) for key in ('summary', 'issues', 'limitations')}
            run['review']['observation'] = deepcopy(review['result']['observation'])
        revision = next((op for op in operations if op['stage'] == 'revision' and op['status'] == 'completed'), None)
        if revision and not any(v['id'] == 'revised' for v in run['versions']):
            path = self._write(run, 'revised-source.json', revision['result'])
            run['versions'].append(dict(id='revised', status='generated', technical_status='pending',
                source_sha256=revision['result']['source_sha256'], source_path=path.name,
                source_file_sha256=digest(revision['result']), sha256=None))
        failed = next((op for op in operations if op['status'] in {'failed', 'cancelled'}), None)
        if failed:
            return self._failed(run, failed.get('result', {}).get('error',
                                'animation_creation_' + failed['stage'] + '_' + failed['status']))
        if outcome['state'] == 'completed':
            verification = next(op for op in operations if op['stage'] == 'verification')
            if revision:
                self._attach_media(run, run['versions'][-1], verification['id'], verification['result'])
            run.update(state='cancelled' if self._cancelled(run) else 'completed', error=None)
        elif outcome['state'] in {'submission_unknown', 'reconcile_required', 'verification_unknown'}:
            run.update(state='submission_unknown', error=dict(code=outcome.get('last_error', 'submission_unknown')))
        else:
            run.update(state='running', error=None)
        return self._save(run)

    def _cancel_remote(self, run, operation, gateway, *, ledger=False):
        status = operation['status']
        if status == 'reserved':
            if ledger:
                self.revisions.finish(operation['id'], 'cancelled', result=dict(cancelled_before_submission=True))
            else:
                operation['status'] = 'cancelled'
            return True
        if status in {'submitting', 'submission_unknown'}:
            try:
                job = gateway.lookup(operation['id'])
            except ServiceError as error:
                run['error'] = dict(code=error.code)
                return False
            _require(type(job) is dict and _uuid(job.get('job_id')), 'animation_creation_admission_invalid', 502)
            if ledger:
                operation = self.revisions.mark_submitted(operation['id'], job['job_id'])
            else:
                operation.update(status='submitted', job_id=job['job_id'])
                self._save(run)
        if operation['status'] in TERMINAL:
            return True
        # Cancelling the same known job is idempotent; it never reopens a slot.
        gateway.cancel(operation['job_id'])
        job = gateway.poll(operation['job_id'])
        if job['state'] in REMOTE_ACTIVE:
            return False
        if ledger:
            self.revisions.finish(operation['id'], 'cancelled', result=dict(upstream_state=job['state']))
        else:
            operation['status'] = 'cancelled'
        return True

    def _cancellation(self, run):
        done = True
        if run['lineage_id']:
            active = next((op for op in self.revisions.operations(run['lineage_id'])
                           if op['status'] not in TERMINAL and op['status'] != 'released'), None)
            if active and active['stage'] == 'verification':
                if active['status'] == 'reserved':
                    self.revisions.finish(active['id'], 'cancelled', result=dict(cancelled_before_submission=True))
                else:
                    # A render can be executing despite a lost local response.
                    try:
                        receipt = self.renderer.lookup(active['id'])
                    except ServiceError as error:
                        run['error'] = dict(code=error.code)
                        receipt = None
                    done = receipt is not None
                    if done:
                        if active['status'] in {'submitting', 'submission_unknown'}:
                            self.revisions.mark_submitted(active['id'], active['id'])
                        self.revisions.finish(active['id'], 'cancelled', result=dict(render_finished_after_cancel=True))
            elif active:
                gateway = self.review_gateway if active['stage'] == 'review' else self.creation_gateway
                done = self._cancel_remote(run, active, gateway, ledger=True)
        elif run['stage'] == 'initial_render':
            op = run['operations'].get('initial_render')
            if op and op['status'] != 'reserved' and op['status'] not in TERMINAL:
                try:
                    done = self.renderer.lookup(op['id']) is not None
                except ServiceError as error:
                    run['error'] = dict(code=error.code)
                    done = False
                if done:
                    op['status'] = 'cancelled'
        else:
            op = run['operations'].get('initial_creation')
            if op:
                done = self._cancel_remote(run, op, self.creation_gateway)
        run['state'] = 'cancelled' if done else 'cancelling'
        return self._save(run)

    def advance(self, owner, run_id):
        run = self._read(owner, run_id)
        if run['state'] in TERMINAL:
            return self._public(run)
        with _run_lock(self._directory(run) / 'advance.lock') as acquired:
            if not acquired:
                return self.get(owner, run_id)
            run = self._read(owner, run_id)
            if run['state'] in TERMINAL:
                return self._public(run)
            try:
                if run['cancel_requested']:
                    run = self._cancellation(run)
                else:
                    run['state'] = 'running'
                    if run['stage'] == 'initial_creation':
                        run = self._initial_creation(run)
                    elif run['stage'] == 'initial_render':
                        run = self._initial_render(run)
                    else:
                        run = self._polish(run)
            except ServiceError as error:
                # Readiness and result-evidence retrieval may recover without
                # new paid work. Durable claims still control every later step.
                if error.code == 'animation_creation_cancel_requested':
                    run = self._cancellation(run)
                elif error.status >= 500 or run['cancel_requested']:
                    run['error'] = dict(code=error.code)
                    run = self._save(run)
                else:
                    run = self._failed(run, error.code)
            except Exception:
                # A crashed coordinator may have persisted its claim. Preserve
                # ambiguity so restart queries that identity, never retries POST.
                run.update(state='submission_unknown', error=dict(code='animation_creation_execution_unknown'))
                run = self._save(run)
            return self._public(run)

    def cancel(self, owner, run_id):
        self._read(owner, run_id)
        # Persist intent against the latest document under the transaction.
        # A concurrent advance may have just added media/version information;
        # saving the earlier caller snapshot here would erase that history.
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM creation_runs WHERE id=? AND owner=?',
                             (run_id, owner)).fetchone()
            _require(row is not None, 'animation_creation_not_found', 404)
            run = json.loads(row['document'])
            if run['state'] in TERMINAL:
                return self._public(run)
            run.update(cancel_requested=True, state='cancelling', updated_at=time.time())
            db.execute('UPDATE creation_runs SET document=? WHERE id=? AND owner=?',
                       (canonical_bytes(run).decode('utf-8'), run_id, owner))
        return self.advance(owner, run_id)

    def artifact(self, owner, run_id, version_id, kind):
        run = self._read(owner, run_id)
        _require(type(version_id) is str and version_id in {'original', 'revised'}
                 and type(kind) is str and kind in {'source', 'media'}, 'animation_creation_artifact_not_found', 404)
        version = next((v for v in run['versions'] if v['id'] == version_id), None)
        _require(version is not None, 'animation_creation_artifact_not_found', 404)
        if kind == 'source':
            path = self._directory(run) / version['source_path']
            _require(path.name == version_id + '-source.json' and path.parent == self._directory(run),
                     'animation_creation_artifact_changed', 409)
            expected = version['source_file_sha256']
        else:
            _require(version.get('render_operation_id') is not None and version.get('sha256') is not None,
                     'animation_creation_artifact_not_found', 404)
            path = self.renderer.media_path(version['render_operation_id'])
            expected = version['sha256']
        _require(file_sha256(path) == expected, 'animation_creation_artifact_changed', 409)
        return Path(path)
