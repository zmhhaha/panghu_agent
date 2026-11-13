"""Durable, bounded candidate preparation; approval remains an explicit action.

The journal contains local child admission requests, never network requests.
An admission is recorded before calling an existing store, and its stable
idempotency key bridges a crash before the child id is linked back here.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from .audio_store import narration_binding, narration_document
from .motion import COMPILER_VERSION
from .motion_store import plan_diagram_kind, semantic_binding


STEPS = ('motion', 'audio', 'draft')
TERMINAL = frozenset({'completed', 'partial_failed', 'cancelled'})
CHILD_TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
ITEM_DONE = frozenset({'completed', 'reused', 'failed', 'cancelled', 'skipped'})
MAX_BATCHES_PER_PROJECT = 64
MAX_TOTAL_BATCHES = 1024
MAX_DOCUMENT_BYTES = 256 * 1024
PUBLIC_ITEM = ('id', 'scene_id', 'scene_title', 'step', 'status', 'error',
               'operation_id', 'version_id', 'render_id', 'motion_version_id', 'audio_version_id')
TABLES = {'motion': 'motion_operations', 'audio': 'audio_operations', 'draft': 'video_renders'}
WAIT_ERRORS = frozenset({'motion_operation_in_progress', 'motion_queue_limit',
                         'audio_operation_in_progress', 'render_in_progress', 'revision_conflict'})


def _require(value, code='invalid_batch_request', status=422):
    if not value:
        raise ServiceError(code, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _render_clock(value):
    """Compare authored timing without making a review create new pixels."""
    alignment = value.get('alignment') or {}
    timeline = value.get('timeline') or {}
    return (dict((key, item) for key, item in alignment.items()
                 if key not in {'review_status', 'reviewed_by', 'reviewed_at'}),
            dict((key, item) for key, item in timeline.items()
                 if key not in {'review_status', 'alignment_sha256'}))


def _idempotency(value):
    _require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', value),
             'idempotency_key_required')


class BatchStore:
    def __init__(self, store, motion_store, production):
        self.store, self.motion, self.production = store, motion_store, production
        with store.transaction() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS video_batches (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                idem TEXT NOT NULL, request_hash TEXT NOT NULL, document TEXT NOT NULL,
                UNIQUE(owner, idem));
                CREATE INDEX IF NOT EXISTS video_batches_project ON video_batches(project_id);''')

    @staticmethod
    def _public(batch):
        items = [{key: item.get(key) for key in PUBLIC_ITEM} for item in batch['items']]
        progress = dict(total=len(items), completed=sum(i['status'] == 'completed' for i in items),
                        reused=sum(i['status'] == 'reused' for i in items),
                        failed=sum(i['status'] == 'failed' for i in items),
                        cancelled=sum(i['status'] == 'cancelled' for i in items),
                        running=sum(i['status'] in {'running', 'cancelling'} for i in items),
                        queued=sum(i['status'] == 'queued' for i in items),
                        blocked=sum(i['status'] == 'blocked' for i in items))
        return {key: batch.get(key) for key in ('id', 'project_id', 'status', 'error',
                'created_at', 'finished_at', 'parent_batch_id')} | dict(progress=progress, items=items)

    @staticmethod
    def _read(db, batch_id, owner=None):
        row = db.execute('SELECT owner,document FROM video_batches WHERE id=?', (batch_id,)).fetchone()
        if row is None or owner is not None and row['owner'] != owner:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _write(db, batch):
        raw = _json(batch)
        _require(len(raw.encode('utf-8')) <= MAX_DOCUMENT_BYTES, 'batch_storage_limit', 413)
        db.execute('UPDATE video_batches SET document=? WHERE id=?', (raw, batch['id']))

    def get(self, owner, batch_id):
        with self.store.transaction() as db:
            batch = self._read(db, batch_id, owner)
            self.store._project(db, owner, batch['project_id'])
            return self._public(batch)

    def list(self, owner, project_id):
        with self.store.transaction() as db:
            self.store._project(db, owner, project_id)
            return [self._public(json.loads(row[0])) for row in db.execute(
                'SELECT document FROM video_batches WHERE owner=? AND project_id=? ORDER BY rowid DESC',
                (owner, project_id))]

    def _busy(self, db, owner, project_id, *, exclude_batch=None):
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('operations', 'motion_operations', 'audio_operations',
                      'explanation_operations', 'joint_plan_operations', 'video_renders'):
            if table in tables:
                for row in db.execute('SELECT document FROM ' + table + ' WHERE project_id=?', (project_id,)):
                    _require(json.loads(row[0]).get('status') in CHILD_TERMINAL, 'batch_project_busy', 409)
        for row in db.execute('SELECT document FROM video_batches WHERE owner=? AND project_id=?', (owner, project_id)):
            batch = json.loads(row[0])
            _require(batch['id'] == exclude_batch or batch['status'] in TERMINAL, 'batch_project_busy', 409)

    @staticmethod
    def _item(scene, step):
        return dict(id='item_' + digest(dict(scene_id=scene['id'], step=step))[:24],
                    scene_id=scene['id'], scene_title=scene['title'], step=step,
                    status='queued', error=None, operation_id=None, version_id=None,
                    render_id=None, motion_version_id=None, audio_version_id=None)

    def _binding(self, project, scene):
        return digest(dict(semantic=semantic_binding(project, scene),
            audio=narration_binding(narration_document(scene, self.store._project_speech_provider(project))),
            duration_seconds=scene['duration_seconds'], leading_seconds=scene.get('leading_seconds', 0),
            trailing_seconds=scene.get('trailing_seconds', 0),
            mode=scene.get('motion', {}).get('mode', scene.get('production_mode', 'static'))))

    @staticmethod
    def _candidate(rows, head):
        current = [row for row in rows if not row.get('stale')]
        return next((row['id'] for row in current if row['id'] == head), None) or (current[-1]['id'] if current else None)

    def _draft(self, db, owner, project_id, scene, motion_id, audio_id, current_timing):
        if not motion_id or not audio_id or not current_timing:
            return None
        for row in db.execute('SELECT id,document,snapshot FROM video_renders '
                              'WHERE owner=? AND project_id=? AND data IS NOT NULL AND length(data)>0 '
                              'ORDER BY rowid DESC', (owner, project_id)):
            job, snapshot = json.loads(row['document']), json.loads(row['snapshot'])
            scenes = snapshot.get('scenes', [])
            if job.get('purpose') != 'draft' or job['status'] != 'completed' or len(scenes) != 1:
                continue
            frozen = scenes[0]
            timing = frozen.get('timing', {})
            dynamic = frozen.get('creative' if frozen.get('visual_mode') == 'creative' else 'motion', {})
            if (frozen.get('id') == scene['id'] and dynamic.get('id') == motion_id
                    and frozen.get('audio', {}).get('id') == audio_id
                    and frozen.get('narration') == scene['narration']
                    and frozen.get('title') == scene['title']
                    and timing.get('leading_seconds', 0) == scene.get('leading_seconds', 0)
                    and timing.get('trailing_seconds', 0) == scene.get('trailing_seconds', 0)
                    and _render_clock(dynamic) == _render_clock(current_timing)
                    and (dynamic.get('plan', {}).get('schema') != 'video.motion-plan.v1'
                         or dynamic.get('timeline', {}).get('compiler') == COMPILER_VERSION)
                    and snapshot.get('runtime_fingerprint') == self.production.runtime_fingerprint):
                return row['id']
        return None

    @staticmethod
    def _request(request, *, start=False):
        keys = {'expected_revision', 'scene_ids', 'steps', 'reuse_current'}
        if start:
            keys.add('plan_sha256')
        _require(type(request) is dict and set(request) <= keys
                 and {'expected_revision', 'scene_ids', 'steps'} <= set(request))
        _require(type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        ids, steps = request['scene_ids'], request['steps']
        _require(type(ids) is list and 1 <= len(ids) <= 12
                 and all(type(i) is str and 1 <= len(i) <= 100 for i in ids) and len(set(ids)) == len(ids))
        _require(type(steps) is list and 1 <= len(steps) <= 3
                 and all(type(s) is str and s in STEPS for s in steps) and len(set(steps)) == len(steps))
        # MVP never hides a forced regeneration behind a project-wide action.
        _require(request.get('reuse_current', True) is True)
        return dict(expected_revision=request['expected_revision'], scene_ids=ids,
                    steps=[s for s in STEPS if s in steps], reuse_current=request.get('reuse_current', True))

    def _plan(self, owner, project_id, request):
        normalized = self._request(request)
        project = self.motion.project(owner, project_id)
        self.store._revision(project, normalized['expected_revision'])
        # Resolve candidates/anchors before opening a write transaction: the
        # timing service uses its own read transaction under the same RLock.
        current_timings = {}
        if 'draft' in normalized['steps']:
            for scene_id in normalized['scene_ids']:
                scene = self.store._scene(project, scene_id)
                motion_id = self._candidate(scene['motion']['versions'], scene['motion']['head_version_id'])
                audio_id = self._candidate(scene['audio_versions'], scene['head_audio_version_id'])
                if motion_id and audio_id:
                    current_timings[(motion_id, audio_id)] = self.production.motion_timing.get(owner, motion_id, audio_id)
        with self.store.transaction() as db:
            self._busy(db, owner, project_id)
            items, bindings, refs, timing_bindings = [], {}, {}, {}
            for scene_id in normalized['scene_ids']:
                scene = self.store._scene(project, scene_id)
                bindings[scene_id] = self._binding(project, scene)
                motion_id = self._candidate(scene['motion']['versions'], scene['motion']['head_version_id'])
                audio_id = self._candidate(scene['audio_versions'], scene['head_audio_version_id'])
                refs[scene_id] = dict(motion=motion_id, audio=audio_id)
                for step in normalized['steps']:
                    item = self._item(scene, step)
                    if step in {'motion', 'audio'}:
                        version_id = motion_id if step == 'motion' else audio_id
                        if normalized['reuse_current'] and version_id:
                            item.update(status='reused', version_id=version_id)
                        elif step == 'motion':
                            motion_id = refs[scene_id]['motion'] = None
                        else:
                            audio_id = refs[scene_id]['audio'] = None
                    else:
                        _require(motion_id or 'motion' in normalized['steps'], 'batch_missing_motion_candidate', 409)
                        _require(audio_id or 'audio' in normalized['steps'], 'batch_missing_audio_candidate', 409)
                        item.update(motion_version_id=motion_id, audio_version_id=audio_id)
                        current_timing = current_timings.get((motion_id, audio_id))
                        if current_timing:
                            timing_bindings[f'{motion_id}:{audio_id}'] = dict(motion_version_id=motion_id,
                                audio_version_id=audio_id, alignment_sha256=current_timing['alignment_sha256'])
                        render_id = self._draft(db, owner, project_id, scene, motion_id, audio_id, current_timing)
                        if render_id:
                            item.update(status='reused', render_id=render_id)
                    items.append(item)
        tasks = {s: sum(i['step'] == s and i['status'] == 'queued' for i in items) for s in STEPS}
        plan = dict(request=normalized, bindings=bindings, refs=refs, timing_bindings=timing_bindings,
                    items=items, estimated_tasks=tasks)
        plan['plan_sha256'] = digest(dict(project_id=project_id, **plan))
        return plan

    def preview(self, owner, project_id, request):
        with self.store.lock:
            plan = self._plan(owner, project_id, request)
        return {key: deepcopy(plan[key]) for key in ('plan_sha256', 'items', 'estimated_tasks')}

    def _insert(self, db, owner, project_id, idem, fingerprint, plan, *, parent=None):
        self.store._project(db, owner, project_id)
        _require(db.execute('SELECT count(*) FROM video_batches WHERE project_id=?', (project_id,)).fetchone()[0]
                 < MAX_BATCHES_PER_PROJECT and db.execute('SELECT count(*) FROM video_batches').fetchone()[0]
                 < MAX_TOTAL_BATCHES, 'batch_storage_limit', 409)
        batch = dict(id=str(uuid4()), owner=owner, project_id=project_id, status='queued', error=None,
                     created_at=time.time(), finished_at=None, parent_batch_id=parent,
                     cancel_requested=False, bindings=plan['bindings'], refs=plan['refs'],
                     timing_bindings=plan.get('timing_bindings', {}),
                     items=deepcopy(plan['items']))
        self._summarize(batch)
        raw = _json(batch)
        _require(len(raw.encode()) <= MAX_DOCUMENT_BYTES, 'batch_storage_limit', 413)
        db.execute('INSERT INTO video_batches VALUES (?, ?, ?, ?, ?, ?)',
                   (batch['id'], owner, project_id, idem, fingerprint, raw))
        return self._public(batch)

    @staticmethod
    def _replay(db, owner, idem, fingerprint):
        row = db.execute('SELECT request_hash,document FROM video_batches WHERE owner=? AND idem=?', (owner, idem)).fetchone()
        if row:
            _require(row['request_hash'] == fingerprint, 'idempotency_conflict', 409)
            return json.loads(row['document'])
        return None

    def start(self, owner, project_id, request, idem):
        _idempotency(idem)
        normalized = self._request(request, start=True)
        _require(type(request.get('plan_sha256')) is str and re.fullmatch(r'[0-9a-f]{64}', request['plan_sha256']))
        fingerprint = digest(dict(kind='start', project_id=project_id, request=request))
        with self.store.lock:
            with self.store.transaction() as db:
                self.store._project(db, owner, project_id)
                existing = self._replay(db, owner, idem, fingerprint)
                if existing:
                    return self._public(existing)
            plan = self._plan(owner, project_id, normalized)
            _require(plan['plan_sha256'] == request['plan_sha256'], 'batch_plan_conflict', 409)
            with self.store.transaction() as db:
                self._busy(db, owner, project_id)
                return self._insert(db, owner, project_id, idem, fingerprint, plan)

    def retry(self, owner, batch_id, expected_revision, item_ids, idem):
        _idempotency(idem)
        _require(type(expected_revision) is int and expected_revision >= 1)
        _require(type(item_ids) is list and 1 <= len(item_ids) <= 36
                 and all(type(i) is str for i in item_ids) and len(set(item_ids)) == len(item_ids))
        fingerprint = digest(dict(kind='retry', batch_id=batch_id, expected_revision=expected_revision, item_ids=item_ids))
        with self.store.lock:
            with self.store.transaction() as db:
                old = self._read(db, batch_id, owner)
                existing = self._replay(db, owner, idem, fingerprint)
                if existing:
                    return self._public(existing)
                project = self.store._project(db, owner, old['project_id'])
                self.store._revision(project, expected_revision)
                self._busy(db, owner, old['project_id'])
            current = self.motion.project(owner, old['project_id'])
            _require(self._bindings_match(old, current), 'batch_inputs_changed', 409)
            selected = [item for item in old['items'] if item['id'] in item_ids]
            _require(len(selected) == len(item_ids) and all(i['status'] == 'failed' and not i.get('_uncertain') for i in selected),
                     'batch_retry_not_allowed', 409)
            refs = deepcopy(old['refs'])
            items = []
            for original in selected:
                item = {key: original.get(key) for key in PUBLIC_ITEM}
                item.update(status='queued', error=None, operation_id=None, version_id=None, render_id=None)
                if item['step'] in {'motion', 'audio'}:
                    refs[item['scene_id']][item['step']] = None
                else:
                    item.update(motion_version_id=refs[item['scene_id']]['motion'],
                                audio_version_id=refs[item['scene_id']]['audio'])
                items.append(item)
            for item in items:
                if item['step'] == 'draft':
                    for step in ('motion', 'audio'):
                        if not refs[item['scene_id']][step] and not any(
                                i['scene_id'] == item['scene_id'] and i['step'] == step for i in items):
                            item.update(status='blocked', error='batch_dependency_failed')
            plan = dict(items=items, refs=refs, bindings=deepcopy(old['bindings']),
                        timing_bindings=deepcopy(old.get('timing_bindings', {})))
            with self.store.transaction() as db:
                return self._insert(db, owner, old['project_id'], idem, fingerprint, plan, parent=batch_id)

    def cancel(self, owner, batch_id):
        with self.store.lock:
            with self.store.transaction() as db:
                batch = self._read(db, batch_id, owner)
                self.store._project(db, owner, batch['project_id'])
                if batch['status'] in TERMINAL:
                    return self._public(batch)
                batch['cancel_requested'] = True
                batch['status'] = 'cancelling'
                self._write(db, batch)
            self._reconcile(batch)
            with self.store.transaction() as db:
                self._write(db, batch)
            return self._public(batch)

    def adopt(self, owner, project_id, expected_revision, selections):
        _require(type(expected_revision) is int and expected_revision >= 1)
        _require(type(selections) is list and 1 <= len(selections) <= 12)
        allowed = {'scene_id', 'motion_version_id', 'audio_version_id',
                   'expected_motion_head_version_id', 'expected_audio_head_version_id'}
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, expected_revision)
            self._busy(db, owner, project_id)
            seen, checked = set(), []
            for selection in selections:
                _require(type(selection) is dict and set(selection) <= allowed
                         and type(selection.get('scene_id')) is str and selection['scene_id'] not in seen)
                seen.add(selection['scene_id'])
                scene = self.store._scene(project, selection['scene_id'])
                motion_id, audio_id = selection.get('motion_version_id'), selection.get('audio_version_id')
                motion_version = None
                _require(motion_id is not None or audio_id is not None)
                if motion_id is not None:
                    _require('expected_motion_head_version_id' in selection)
                    head = self.motion._head(db, owner, project_id, scene['id'])
                    _require(head['version_id'] == selection['expected_motion_head_version_id'], 'motion_head_conflict', 409)
                    version = self.motion._version(db, owner, motion_id)
                    _require(version['project_id'] == project_id and version['scene_id'] == scene['id'], 'not_found', 404)
                    _require(version['semantic_binding_sha256'] == semantic_binding(project, scene), 'motion_plan_stale', 409)
                    motion_version = version
                if audio_id is not None:
                    _require(type(audio_id) is str and 1 <= len(audio_id) <= 100 and 'expected_audio_head_version_id' in selection)
                    _require(scene.get('head_audio_version_id') == selection['expected_audio_head_version_id'], 'audio_head_conflict', 409)
                    row = db.execute('SELECT document FROM audio_versions WHERE id=? AND project_id=? AND scene_id=?',
                                     (audio_id, project_id, scene['id'])).fetchone()
                    _require(row is not None, 'not_found', 404)
                    version = json.loads(row[0])
                    binding = narration_binding(narration_document(scene, self.store._project_speech_provider(project)))
                    _require(version['input_sha256'] == binding, 'audio_stale', 409)
                if motion_version is not None and plan_diagram_kind(motion_version['plan']) == 'creative':
                    selected_scene = dict(scene, head_audio_version_id=audio_id or scene.get('head_audio_version_id'))
                    _, _, recording = self.motion._creative_audio(db, project, selected_scene)
                    _require(recording['sha256'] == motion_version['audio_sha256'],
                             'creative_audio_binding_mismatch', 409)
                checked.append((scene, motion_id, audio_id))
            for scene, motion_id, audio_id in checked:
                if motion_id is not None:
                    db.execute('INSERT INTO motion_heads VALUES (?, ?, ?, ?, ?) ON CONFLICT(owner,project_id,scene_id) '
                               'DO UPDATE SET version_id=excluded.version_id,mode=excluded.mode',
                               (owner, project_id, scene['id'], motion_id, 'motion'))
                if audio_id is not None:
                    scene['head_audio_version_id'] = audio_id
            self.store._save(db, project)
        return self.motion.project(owner, project_id)

    def _bindings_match(self, batch, project):
        scenes = {scene['id']: scene for scene in project['scenes']}
        if not all(scene_id in scenes and self._binding(project, scenes[scene_id]) == binding
                   for scene_id, binding in batch['bindings'].items()):
            return False
        for frozen in batch.get('timing_bindings', {}).values():
            try:
                current = self.production.motion_timing.get(batch['owner'], frozen['motion_version_id'], frozen['audio_version_id'])
            except ServiceError:
                return False
            if current['alignment_sha256'] != frozen['alignment_sha256']:
                return False
        return True

    @staticmethod
    def _summarize(batch):
        items = batch['items']
        if any(i['status'] == 'blocked' for i in items):
            batch.update(status='blocked', error=next(i['error'] for i in items if i['status'] == 'blocked'))
        elif batch['cancel_requested']:
            batch.update(status='cancelling' if any(i['status'] in {'running', 'cancelling'} for i in items) else 'cancelled', error=None)
        elif all(i['status'] in ITEM_DONE for i in items):
            batch.update(status='partial_failed' if any(i['status'] in {'failed', 'cancelled', 'skipped'} for i in items) else 'completed', error=None)
        else:
            batch.update(status='running' if any(i['status'] == 'running' for i in items) else 'queued', error=None)
        batch['finished_at'] = (batch.get('finished_at') or time.time()) if batch['status'] in TERMINAL else None

    def _lookup_child(self, batch, item):
        with self.store.transaction() as db:
            if item.get('_idem'):
                row = db.execute('SELECT document FROM ' + TABLES[item['step']] + ' WHERE owner=? AND idem=?',
                                 (batch['owner'], item['_idem'])).fetchone()
                return json.loads(row[0]) if row else None
        return None

    @staticmethod
    def _uncertain(child):
        error = child.get('error') or ''
        return ('uncertain' in error or 'unconfirmed' in error
                or child.get('status') not in CHILD_TERMINAL | {'queued', 'submitting', 'running', 'cancelling'})

    def _sync_child(self, batch, item, child):
        if item['step'] == 'draft':
            item['render_id'] = child['id']
        else:
            item['operation_id'] = child['id']
        item['_child_id'] = child['id']
        status = child['status']
        item['_uncertain'] = self._uncertain(child) or (item['step'] == 'audio'
            and child.get('submit_attempted') and child.get('status') in CHILD_TERMINAL - {'completed'}
            and child.get('upstream_stop_confirmed') is not True)
        if item['_uncertain']:
            item.update(status='blocked', error=child.get('error') or 'batch_child_uncertain')
        elif status not in CHILD_TERMINAL:
            item.update(status='cancelling' if child.get('cancel_requested') else 'running', error=child.get('error'))
        elif status == 'completed':
            version = child.get('version_id') if item['step'] == 'motion' else child.get('candidate_id')
            if item['step'] == 'draft' or version:
                item.update(status='completed', error=child.get('error'))
                if version:
                    item['version_id'] = batch['refs'][item['scene_id']][item['step']] = version
            else:
                item.update(status='cancelled' if batch['cancel_requested'] else 'failed',
                            error=child.get('error') or 'batch_child_no_result')
        else:
            item.update(status='cancelled' if status == 'cancelled' else 'failed', error=child.get('error') or 'batch_child_' + status)

    def _reconcile(self, batch):
        for item in batch['items']:
            child = self._lookup_child(batch, item) if item.get('_idem') else None
            if child:
                self._sync_child(batch, item, child)
            if batch['cancel_requested']:
                if child and child['status'] not in CHILD_TERMINAL:
                    cancel = {'motion': self.motion.cancel, 'audio': self.store.cancel_audio,
                              'draft': self.production.cancel}[item['step']]
                    cancel(batch['owner'], child['id'])
                    self._sync_child(batch, item, self._lookup_child(batch, item))
                elif not child and item['status'] not in ITEM_DONE:
                    item.update(status='cancelled', error=None)
        self._summarize(batch)

    def _admit(self, batch, item):
        """One local durable admission. All external I/O belongs to other workers."""
        owner, project_id = batch['owner'], batch['project_id']
        request, idem = item['_request'], item['_idem']
        if item['step'] == 'motion':
            return self.motion.start(owner, item['scene_id'], request, idem)
        if item['step'] == 'audio':
            return self.store.start_audio(owner, item['scene_id'], request, idem)
        return self.production.start_motion_render(owner, project_id, request, idem)

    def tick(self, *, enabled=True):
        """Reconcile all journals, then admit at most one child globally.

        The outer RLock serializes local mutations without nesting SQLite
        transactions. A nonterminal or ambiguous child anywhere in the batch
        journal fences new admissions across every project and owner.
        """
        with self.store.lock:
            with self.store.transaction() as db:
                batches = [json.loads(r[0]) for r in db.execute('SELECT document FROM video_batches ORDER BY rowid')]
            for batch in batches:
                if batch['status'] not in TERMINAL:
                    self._reconcile(batch)
                    with self.store.transaction() as db:
                        self._write(db, batch)
            for batch in batches:
                for item in batch['items']:
                    if item.get('_uncertain') or item.get('_child_id') and item['status'] in {'running', 'cancelling', 'blocked'}:
                        return
            if not enabled:
                return
            for batch in batches:
                if batch['status'] in TERMINAL or batch['cancel_requested'] or batch['status'] == 'blocked':
                    continue
                project = self.motion.project(batch['owner'], batch['project_id'])
                if not self._bindings_match(batch, project):
                    for item in batch['items']:
                        if item['status'] == 'queued':
                            item.update(status='blocked', error='batch_inputs_changed')
                    self._summarize(batch)
                    with self.store.transaction() as db:
                        self._write(db, batch)
                    continue
                item = next((i for i in batch['items'] if i['status'] == 'queued'), None)
                if item is None:
                    continue
                if item['step'] == 'draft':
                    refs = batch['refs'][item['scene_id']]
                    item.update(motion_version_id=refs['motion'], audio_version_id=refs['audio'])
                    if not all(refs.values()):
                        item.update(status='failed', error='batch_dependency_failed')
                        self._summarize(batch)
                        with self.store.transaction() as db:
                            self._write(db, batch)
                        return
                    try:
                        timing = self.production.motion_timing.get(batch['owner'], refs['motion'], refs['audio'])
                    except ServiceError as exc:
                        item.update(status='failed', error=exc.code)
                        self._summarize(batch)
                        with self.store.transaction() as db:
                            self._write(db, batch)
                        return
                    pair = f"{refs['motion']}:{refs['audio']}"
                    frozen = batch.setdefault('timing_bindings', {}).get(pair)
                    if frozen and frozen['alignment_sha256'] != timing['alignment_sha256']:
                        item.update(status='blocked', error='batch_inputs_changed')
                        self._summarize(batch)
                        with self.store.transaction() as db:
                            self._write(db, batch)
                        return
                    batch['timing_bindings'][pair] = dict(motion_version_id=refs['motion'],
                        audio_version_id=refs['audio'], alignment_sha256=timing['alignment_sha256'])
                # An absent local child is proof no durable admission occurred.
                # Refresh only this unadmitted request's revision; all material
                # inputs and every already admitted request remain immutable.
                item['_idem'] = item.get('_idem') or f"batch:{batch['id']}:{item['id']}"
                item['_request'] = dict(expected_revision=project['revision'])
                if item['step'] == 'draft':
                    item['_request'].update(purpose='draft', subtitles=False, scene_id=item['scene_id'],
                        motion_version_id=item['motion_version_id'], audio_version_id=item['audio_version_id'])
                with self.store.transaction() as db:
                    self._write(db, batch)
                try:
                    self._admit(batch, item)
                except ServiceError as exc:
                    if exc.code not in WAIT_ERRORS:
                        item.update(status='failed', error=exc.code)
                child = self._lookup_child(batch, item)
                if child:
                    self._sync_child(batch, item, child)
                self._summarize(batch)
                with self.store.transaction() as db:
                    self._write(db, batch)
                return
