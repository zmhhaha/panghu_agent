"""One source-only recipe and one isolated resource operation per experiment.

The method author keeps its existing role. This durable prelude prepares exact
assets before its normal plan/create/review loop. Reads never execute work.
"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_manim_contract import INPUT_SCHEMA, validate_core_task, validate_input
from .manim_method_resources import method_resource
from .manim_resource import require, MANIM_VERSION
from .manim_resource_job import freeze_resource_job


def authoring_request(value, duration):
    require(type(value) is dict and {'kind'} <= set(value) <= {
        'kind', 'width', 'height', 'frame_count', 'global_start_frame', 'seed'}
        and value['kind'] == 'manim', 'manim_method_authoring_invalid')
    result = dict(kind='manim', width=640, height=360, frame_count=48, global_start_frame=0, seed=17)
    result.update(value)
    bounds = dict(resource_id='validation', fps=24, transparent=True, manim_version=MANIM_VERSION,
        **{key: item for key, item in result.items() if key != 'kind'})
    validate_core_task(dict(effect_id='validation', goal='validate resource bounds', guidance='', resource_constraints=bounds))
    require(bounds['global_start_frame'] + bounds['frame_count'] <= duration * 24,
        'manim_method_resource_clock_outside_project')
    return result


class ManimMethodCoordinator:
    def __init__(self, root, *, gateway, executor, skill_adapter, enabled=False, candidate=False):
        self.root = Path(root).absolute()
        require(not any(path.is_symlink() for path in (self.root, *self.root.parents)), 'manim_method_path_invalid')
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'manim-method.sqlite3'
        require(not self.path.is_symlink(), 'manim_method_path_invalid')
        self.gateway, self.executor, self.skill_adapter = gateway, executor, skill_adapter
        self.enabled = enabled is True
        self.candidate = candidate is True
        with closing(self._connect()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS resources(owner TEXT NOT NULL,commission_id TEXT NOT NULL,'
                'document BLOB NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(owner,commission_id))')

    def _connect(self):
        require(not self.path.is_symlink(), 'manim_method_path_invalid')
        db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def _get(self, owner, commission_id):
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM resources WHERE owner=? AND commission_id=?', (owner, commission_id)).fetchone()
        if row is None:
            return None
        value = json.loads(row['document'])
        require(digest(value) == row['sha256'] and value['scope']['owner'] == owner
            and value['scope']['experiment_id'] == commission_id, 'manim_method_record_changed', 409)
        return value

    def _save(self, value, previous=None):
        raw = canonical_bytes(value)
        require(len(raw) <= 40 * 1024 * 1024, 'manim_method_record_too_large', 413)
        owner, commission = value['scope']['owner'], value['scope']['experiment_id']
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            if previous is None:
                db.execute('INSERT OR IGNORE INTO resources VALUES(?,?,?,?)', (owner, commission, raw, digest(value)))
            else:
                changed = db.execute('UPDATE resources SET document=?,sha256=? WHERE owner=? AND commission_id=? AND sha256=?',
                    (raw, digest(value), owner, commission, digest(previous))).rowcount
                require(changed == 1, 'manim_method_state_conflict', 409)
            db.commit()
        return self._get(owner, commission)

    def capabilities(self):
        return dict(kind='manim', enabled=self.enabled, status='admitted' if self.enabled else 'prepared_not_admitted',
            category='offline-frame-resource-authoring', version=MANIM_VERSION, browser_engine=None,
            max_frames=60, latex_available=False)

    def preflight(self, deadline_at):
        require(self.enabled or self.candidate, 'manim_method_resource_not_admitted', 503)
        return self.gateway.preflight(deadline_at)

    def view(self, owner, commission_id):
        value = self._get(owner, commission_id)
        if value is None:
            return dict(kind='manim', status='pending', operation_id=None, error=None, artifact_sha256=None,
                budget=dict(recipe_jobs=1, resource_executions=1), usage=dict(recipe_jobs=0, resource_executions=0))
        return dict(kind='manim', status=value['state'], operation_id=value['scope']['operation_id'],
            error=value.get('error'), artifact_sha256=value.get('envelope', {}).get('sha256'),
            budget=dict(recipe_jobs=1, resource_executions=1), usage=dict(
                recipe_jobs=int(value.get('recipe_claimed', False)),
                resource_executions=int('plan' in value)),
            scope='resource_not_visual_acceptance')

    def resolve(self, descriptor):
        require(type(descriptor) is dict and type(descriptor.get('scope')) is dict,
            'manim_method_resource_binding_invalid', 409)
        binding = descriptor['scope']
        value = self._get(binding.get('owner'), binding.get('experiment_id'))
        require(value is not None and value['state'] == 'completed' and value['scope'] == binding
            and value['descriptor'] == descriptor, 'manim_method_resource_not_owned', 409)
        actual, assets = method_resource(value['envelope'], expected_sha256=descriptor['envelope_sha256'],
            expected_scope=binding, image_digest=value['image'].rsplit('@', 1)[1])
        require(actual == descriptor, 'manim_method_resource_changed', 409)
        return assets

    def context(self, owner, commission_id):
        value = self._get(owner, commission_id)
        require(value is not None and value['state'] == 'completed', 'manim_method_resource_not_ready', 409)
        return deepcopy(value['descriptor'])

    def advance(self, owner, run, *, cancelled=False):
        """One transition; caller holds the existing cross-process run lock."""
        commission = run['id']
        value = self._get(owner, commission)
        if value is None:
            if cancelled:
                return 'cancelled'
            options = authoring_request(run['frozen']['resource_authoring'], run['frozen']['duration_seconds'])
            operation = 'manim-' + digest(dict(owner=owner, commission=commission))[:40]
            bounds = dict(resource_id='resource_' + digest(operation)[:24], fps=24, transparent=True,
                manim_version=MANIM_VERSION, **{key: item for key, item in options.items() if key != 'kind'})
            task = validate_core_task(dict(effect_id=commission, goal=run['frozen']['goal'],
                guidance=run['frozen'].get('feedback', ''), resource_constraints=bounds))
            delivery = self.skill_adapter.freeze(owner, operation, task, ['html-svg'])
            document = validate_input(dict(schema=INPUT_SCHEMA, **task, skill_catalog=delivery['catalog']))
            value = dict(scope=dict(owner=owner, effect_id=commission, experiment_id=commission, operation_id=operation),
                state='reserved', input=document, image=self.executor.image, job_id=None, error=None)
            return self._save(value)['state']
        previous = deepcopy(value)
        state = value['state']
        if state in {'completed', 'failed', 'cancelled'}:
            return state
        if state == 'reserved':
            if cancelled:
                value.update(state='cancelled', error='stopped_before_recipe_claim')
            else:
                value.update(upload=self.gateway.upload(value['input']), state='uploaded')
            return self._save(value, previous)['state']
        if state == 'uploaded':
            if cancelled:
                value.update(state='cancelled', error='stopped_before_recipe_claim')
                return self._save(value, previous)['state']
            admit = self.gateway.prepare_submission(value['scope']['operation_id'], value['input'], run['deadline_at'], value['upload'])
            value.update(state='recipe_claimed', recipe_claimed=True)
            value = self._save(value, previous)
            previous = deepcopy(value)
            try:
                value.update(job_id=admit()['job_id'], state='recipe_submitted')
            except Exception:
                value['state'] = 'recipe_unknown'
            return self._save(value, previous)['state']
        if state in {'recipe_claimed', 'recipe_unknown', 'recipe_submitted'}:
            try:
                job = self.gateway.poll(value['job_id']) if value['job_id'] else self.gateway.lookup(value['scope']['operation_id'])
            except ServiceError as error:
                if error.status != 404:
                    raise
                return state
            if job is None:
                return state
            value['job_id'] = job['job_id']
            if job['state'] in {'queued', 'starting', 'running', 'cancelling'}:
                if cancelled:
                    self.gateway.cancel(job['job_id'])
            elif job.get('stop_proven') is True:
                if cancelled or job['state'] != 'completed':
                    value.update(state='cancelled' if cancelled else 'failed', error='manim_recipe_agent_failed')
                else:
                    collected = self.gateway.collect(job['job_id'], digest(value['input']), value['input'])
                    value.update(recipe=collected, state='recipe_completed', recipe_stop_proof_sha256=digest(job['stop_proof']))
            return self._save(value, previous)['state']
        if state == 'recipe_completed':
            if cancelled or run['deadline_at'] - time.time() < 30:
                value.update(state='cancelled', error='stopped_before_resource_claim')
            else:
                value['plan'] = freeze_resource_job(value['recipe']['result']['request'], **value['scope'], image=value['image'],
                    recipe_input_sha256=digest(value['input']), recipe_result_sha256=digest(value['recipe']['result']),
                    recipe_stop_proof_sha256=value['recipe_stop_proof_sha256'],
                    timeout_seconds=max(30, min(180, int(run['deadline_at'] - time.time()))))
                value['state'] = 'resource_claimed'
            return self._save(value, previous)['state']
        if state in {'resource_claimed', 'resource_running', 'needs_recovery'}:
            try:
                result = self.executor.lookup(**value['scope'])
            except ServiceError as error:
                if error.status != 404:
                    raise
                result = None
            if cancelled:
                if result is not None:
                    self.executor.cancel(**value['scope'])
                    result = self.executor.recover(**value['scope'])
                else:
                    value.update(state='cancelled', error='stopped_before_resource_execution')
                    return self._save(value, previous)['state']
            elif result is None:
                result = self.executor.execute(value['plan'])
            elif result['status'] in {'running', 'needs_recovery'}:
                result = self.executor.recover(**value['scope'])
            if result is not None:
                require(result['scope'] == value['scope'] and result['plan_sha256'] == value['plan']['sha256']
                    and result['image'] == value['image'], 'manim_method_execution_binding_mismatch', 409)
                if cancelled and result['stop_proven'] is True:
                    value.update(state='cancelled', error='cancelled_before_resource_adoption')
                    return self._save(value, previous)['state']
                value.update(state={'running': 'resource_running'}.get(result['status'], result['status']), error=result.get('error'))
                if result['status'] == 'completed':
                    require(result['stop_proven'] is True, 'manim_method_writers_not_stopped', 409)
                    envelope = result['generated_assets']
                    descriptor, _ = method_resource(envelope, expected_sha256=envelope['sha256'],
                        expected_scope=value['scope'], image_digest=value['image'].rsplit('@', 1)[1])
                    require(envelope['request'] == value['plan']['request'], 'manim_method_recipe_changed', 409)
                    value.update(envelope=envelope, descriptor=descriptor)
            return self._save(value, previous)['state']
        raise ServiceError('manim_method_state_invalid', 409)
