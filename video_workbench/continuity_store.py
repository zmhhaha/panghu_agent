"""Explicit project continuity candidates over already adopted motion plans.

Candidate creation never calls an Agent, changes media, or selects a head. The
only mutable pointer is the independent project continuity head; all versions
retain the exact scene/motion inputs that were presented for review.
"""
from __future__ import annotations

from copy import deepcopy
import json
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .motion_store import MotionStore, semantic_binding


PLAN_SCHEMA = 'video.continuity-plan.v1'
INPUT_SCHEMA = 'video.continuity-input.v1'
MAX_PROJECT_CANDIDATES = 64
MAX_VERSION_BYTES = 512 * 1024
MAX_PROJECT_JSON_BYTES = 32 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 128 * 1024 * 1024
LEGACY_COMPILER = 'motion-continuity-compiler-2'
PROCESS_COMPILER = 'motion-process-continuity-compiler-1'
PROCESS_SOURCE_COMPILER = 'motion-process-compiler-2'
PROCESS_SCHEMA = 'video.process-plan.v1'


def _require(value, code='invalid_continuity_request', status=422):
    if not value:
        raise ServiceError(code, status)


def _identity(value):
    _require(type(value) is str and 1 <= len(value) <= 100)
    return value


def _revision(value):
    _require(type(value) is int and value >= 1)


def _validated(plan, scenes, compiler=LEGACY_COMPILER):
    # Import the video-owned pure contract here; no shared Agent profile is
    # widened by registering or reading a continuity candidate.
    if compiler == PROCESS_COMPILER:
        from .process_continuity import validate_process_continuity_plan
        result = validate_process_continuity_plan(plan, scenes)
    else:
        _require(type(compiler) is str and compiler in {'motion-continuity-compiler-1', LEGACY_COMPILER},
                 'continuity_compiler_version_unsupported', 409)
        _require(not any(scene['motion']['plan'].get('schema') == PROCESS_SCHEMA for scene in scenes),
                 'continuity_compiler_schema_mismatch', 409)
        from .continuity import validate_continuity_plan
        result = validate_continuity_plan(plan, scenes)
    return deepcopy(plan if result is None else result)


def _require_supported(current):
    for reason in ('process_continuity_mixed_not_supported', 'process_continuity_source_unsupported',
                   'continuity_semantic_layout_not_supported', 'creative_continuity_not_supported'):
        _require(not any(issue['reason'] == reason for issue in current['issues']), reason, 409)


class ContinuityStore:
    def __init__(self, store, motion_store=None):
        self.store = store
        self.motion = motion_store if motion_store is not None else MotionStore(store)
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS continuity_versions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS continuity_heads (
                    owner TEXT NOT NULL, project_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    PRIMARY KEY(owner, project_id));
                CREATE INDEX IF NOT EXISTS continuity_versions_project
                    ON continuity_versions(owner, project_id);
            ''')

    @staticmethod
    def _head(db, owner, project_id):
        row = db.execute('SELECT version_id FROM continuity_heads WHERE owner=? AND project_id=?',
                         (owner, project_id)).fetchone()
        return row['version_id'] if row is not None else None

    @staticmethod
    def _version(db, owner, version_id):
        _identity(version_id)
        row = db.execute('SELECT v.document FROM continuity_versions v JOIN projects p '
                         'ON p.id=v.project_id WHERE v.id=? AND v.owner=? AND p.owner=?',
                         (version_id, owner, owner)).fetchone()
        if row is None:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    def _current(self, db, owner, project_id, project=None):
        project = project if project is not None else self.store._project(db, owner, project_id)
        scenes, summaries, inputs, issues, kinds = [], [], [], [], []
        if len(project['scenes']) < 2:
            issues.append(dict(scene_id=None, scene_title=None, kind='project', reason='continuity_requires_scenes'))
        for scene in project['scenes']:
            scene_id = scene['id']
            binding = semantic_binding(project, scene)
            head = self.motion._head(db, owner, project_id, scene_id,
                                     default_mode=scene.get('production_mode', 'static'))
            reason, version = None, None
            if head['mode'] != 'motion':
                reason = 'continuity_requires_motion'
            elif not head['version_id']:
                reason = 'continuity_missing_motion'
            else:
                try:
                    version = self.motion._version(db, owner, head['version_id'])
                except ServiceError as error:
                    if error.code != 'not_found':
                        raise
                    reason = 'continuity_missing_motion'
                if version is not None:
                    if version['project_id'] != project_id or version['scene_id'] != scene_id:
                        reason = 'continuity_missing_motion'
                        version = None
                    elif (version['semantic_binding_sha256'] != binding
                          or digest(version['plan']) != version['plan_sha256']):
                        reason = 'continuity_motion_stale'
                    elif version['plan'].get('schema') in {'video.creative-scene.v1', 'video.creative-scene.v2'}:
                        reason = 'creative_continuity_not_supported'
                    elif (version['plan'].get('schema') == PROCESS_SCHEMA
                          and version.get('compiler_version') != PROCESS_SOURCE_COMPILER):
                        reason = 'process_continuity_source_unsupported'
                    elif (version['plan'].get('template') in {'sequence', 'nested'}
                          or 'sources' in version['plan']):
                        reason = 'continuity_semantic_layout_not_supported'
            if reason:
                issues.append(dict(scene_id=scene_id, scene_title=scene['title'], kind='motion', reason=reason))
            creative = version is not None and version['plan'].get('schema') in {'video.creative-scene.v1', 'video.creative-scene.v2'}
            elements = deepcopy(version['plan']['elements']) if version is not None and not creative else []
            summary = dict(scene_id=scene_id, title=scene['title'], elements=elements)
            source = dict(scene_id=scene_id, semantic_binding_sha256=binding,
                          motion_version_id=head['version_id'],
                          motion_plan_sha256=version['plan_sha256'] if version is not None else None)
            process = version is not None and version['plan'].get('schema') == PROCESS_SCHEMA
            kinds.append('process' if process else 'diagram')
            if process:
                summary['diagram_kind'] = 'process'
                # This is a new input binding only for process candidates. Old
                # diagram bindings keep their original bytes and freshness.
                source['motion_compiler_version'] = version.get('compiler_version')
                if reason is None:
                    from .process_continuity import process_scene_summary
                    try:
                        summary = process_scene_summary(dict(id=scene_id, title=scene['title'],
                                                             motion=deepcopy(version)))
                    except ServiceError:
                        issues.append(dict(scene_id=scene_id, scene_title=scene['title'], kind='motion',
                                           reason='process_continuity_source_invalid'))
            summaries.append(summary)
            inputs.append(source)
            if version is not None:
                scenes.append(dict(id=scene_id, title=scene['title'], narration=scene['narration'],
                                   motion=deepcopy(version)))
        if 'process' in kinds and 'diagram' in kinds:
            issues.append(dict(scene_id=None, scene_title=None, kind='project',
                               reason='process_continuity_mixed_not_supported'))
        input_binding = dict(schema=INPUT_SCHEMA, scenes=inputs)
        return dict(project=project, scenes=scenes, scene_summaries=summaries, issues=issues,
                    input_binding=input_binding, input_binding_sha256=digest(input_binding),
                    compiler=PROCESS_COMPILER if kinds and set(kinds) == {'process'} else LEGACY_COMPILER)

    @staticmethod
    def _public(version, current, head_id):
        result = deepcopy(version)
        # Candidates predating compiler freezing are diagram compiler 2. In
        # particular, a new process default must never reinterpret old rows.
        result['compiler'] = version.get('compiler', LEGACY_COMPILER)
        compilers = ((PROCESS_COMPILER,) if current['compiler'] == PROCESS_COMPILER
                     else ('motion-continuity-compiler-1', LEGACY_COMPILER))
        result['stale'] = bool(current['issues'] or result['compiler'] not in compilers
                               or version['input_binding_sha256'] != current['input_binding_sha256'])
        result['adoptable'] = not result['stale']
        result['adopted'] = version['id'] == head_id
        return result

    @staticmethod
    def _capacity(db, project_id, extra):
        project_bytes = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                                   'FROM continuity_versions WHERE project_id=?', (project_id,)).fetchone()[0]
        total_bytes = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                                 'FROM continuity_versions').fetchone()[0]
        _require(extra <= MAX_VERSION_BYTES and project_bytes + extra <= MAX_PROJECT_JSON_BYTES
                 and total_bytes + extra <= MAX_TOTAL_JSON_BYTES, 'continuity_storage_limit', 413)

    def propose(self, owner, project_id, expected_revision, links=None):
        """Freeze a new reviewable candidate; an empty link list is explicit."""
        _identity(project_id)
        _revision(expected_revision)
        _require(links is None or type(links) is list)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, expected_revision)
            current = self._current(db, owner, project_id, project)
            _require_supported(current)
            _require(not current['issues'], 'continuity_inputs_unavailable', 409)
            if links is None:
                if current['compiler'] == PROCESS_COMPILER:
                    from .process_continuity import build_process_continuity_plan
                    plan = build_process_continuity_plan(current['scenes'])
                else:
                    from .continuity import build_continuity_plan
                    plan = build_continuity_plan(current['scenes'])
            else:
                plan = dict(schema=PLAN_SCHEMA, links=deepcopy(links))
            plan = _validated(plan, current['scenes'], current['compiler'])
            summaries = current['scene_summaries']
            if current['compiler'] == PROCESS_COMPILER:
                from .process_continuity import process_continuity_summaries
                summaries = process_continuity_summaries(current['scenes'], plan)
            count = db.execute('SELECT COUNT(*) FROM continuity_versions WHERE owner=? AND project_id=?',
                               (owner, project_id)).fetchone()[0]
            _require(count < MAX_PROJECT_CANDIDATES, 'continuity_candidate_limit', 409)
            version = dict(id=str(uuid4()), project_id=project_id, number=count + 1, created_at=time.time(),
                           input_binding=current['input_binding'], input_binding_sha256=current['input_binding_sha256'],
                           plan=plan, plan_sha256=digest(plan), compiler=current['compiler'],
                           scene_summaries=summaries)
            raw = canonical_bytes(version)
            self._capacity(db, project_id, len(raw))
            db.execute('INSERT INTO continuity_versions VALUES (?, ?, ?, ?)',
                       (version['id'], owner, project_id, raw.decode('utf-8')))
            return self._public(version, current, self._head(db, owner, project_id))

    def get(self, owner, version_id):
        with self.store.transaction() as db:
            version = self._version(db, owner, version_id)
            current = self._current(db, owner, version['project_id'])
            return self._public(version, current, self._head(db, owner, version['project_id']))

    # Match the naming used for the other authoring candidate stores.
    version = get

    def project_projection(self, owner, project_id):
        with self.store.transaction() as db:
            current = self._current(db, owner, project_id)
            head_id = self._head(db, owner, project_id)
            versions = [self._public(json.loads(row['document']), current, head_id) for row in db.execute(
                'SELECT document FROM continuity_versions WHERE owner=? AND project_id=? ORDER BY rowid',
                (owner, project_id))]
            head = next((version for version in versions if version['id'] == head_id), None)
            return dict(project_id=project_id, project_revision=current['project']['revision'],
                        head_version_id=head_id, head_stale=bool(head_id and (head is None or head['stale'])),
                        versions=versions, scene_summaries=current['scene_summaries'], issues=current['issues'])

    def list(self, owner, project_id):
        return self.project_projection(owner, project_id)['versions']

    def adopt(self, owner, project_id, version_id, expected_revision, expected_head_id):
        """CAS an exact reviewed candidate; edited links require a new proposal."""
        _identity(project_id)
        _identity(version_id)
        _revision(expected_revision)
        _require(expected_head_id is None or type(expected_head_id) is str and 1 <= len(expected_head_id) <= 100)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, expected_revision)
            version = self._version(db, owner, version_id)
            _require(version['project_id'] == project_id, 'not_found', 404)
            head_id = self._head(db, owner, project_id)
            _require(head_id == expected_head_id, 'continuity_head_conflict', 409)
            current = self._current(db, owner, project_id, project)
            _require_supported(current)
            _require(not current['issues'] and version['input_binding_sha256'] == current['input_binding_sha256'],
                     'continuity_plan_stale', 409)
            _require(digest(version['plan']) == version['plan_sha256'], 'continuity_plan_invalid', 409)
            _validated(version['plan'], current['scenes'], version.get('compiler', LEGACY_COMPILER))
            if head_id != version_id:
                db.execute('INSERT INTO continuity_heads VALUES (?, ?, ?) '
                           'ON CONFLICT(owner,project_id) DO UPDATE SET version_id=excluded.version_id',
                           (owner, project_id, version_id))
                self.store._save(db, project)
            return dict(project_id=project_id, project_revision=project['revision'], head_version_id=version_id,
                        head_stale=False, version=self._public(version, current, version_id))

    def render_selection(self, owner, project_id, version_id=None):
        """Resolve a frozen candidate for a draft; delivery must additionally require the head."""
        _identity(project_id)
        _require(version_id is None or type(version_id) is str and 1 <= len(version_id) <= 100)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            selected_id = version_id if version_id is not None else self._head(db, owner, project_id)
            if selected_id is None:
                return None
            version = self._version(db, owner, selected_id)
            _require(version['project_id'] == project_id, 'not_found', 404)
            current = self._current(db, owner, project_id, project)
            _require_supported(current)
            _require(not current['issues'] and version['input_binding_sha256'] == current['input_binding_sha256'],
                     'continuity_plan_stale', 409)
            _require(digest(version['plan']) == version['plan_sha256'], 'continuity_plan_invalid', 409)
            compiler = version.get('compiler', LEGACY_COMPILER)
            plan = _validated(version['plan'], current['scenes'], compiler)
            return dict(id=version['id'], version_id=version['id'], plan=plan,
                        input_binding_sha256=version['input_binding_sha256'], plan_sha256=version['plan_sha256'],
                        compiler=compiler)
