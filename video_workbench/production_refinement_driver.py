"""Real formal-film adapters for bounded review and exact-module refinement.

Sampling covers the film's time span, not every frame or its audio. Receipts
retain this limitation; neither playback approval nor speech acceptance is
inferred. Authors keep the original module targets, resources and WikiSkill.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_review as visual
from .animation_observation import load_bundle, validate_model_frame_receipt
from .film_render_adapter import validate_film_render_result
from .production_sequence_store import require
from .production_refinement_service import time_range_intersects


MAX_USER_CHANGE_PAYLOAD = 12000


class ProductionRefinementDriver:
    """Bind the coordinator to existing FilmCreationWorkflow and review v4.

    The gateway must be the real ``AnimationInputReviewGateway`` (or an admitted
    earlier visual-review gateway). Local film admissions use their durable
    request keys; an uncertain local response can safely be looked up. Missing
    remote review admission is never retried under a different identity.
    """

    def __init__(self, store, film_creation, review_gateway, root, *, review_timeout_seconds=600,
                 sampling_client=None):
        self.store, self.film, self.reviewer = store, film_creation, review_gateway
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        require(getattr(review_gateway, 'profile', None) in {visual.REVIEW_PACKED_PROFILE,
            visual.REVIEW_BATCH_PROFILE, visual.REVIEW_SEQUENCE_PROFILE},
            'production_refinement_review_profile_required', 503)
        require(type(review_timeout_seconds) is int and 30 <= review_timeout_seconds <= 600)
        self.review_timeout = review_timeout_seconds
        self.sampling_client = sampling_client

    def authorize(self, owner, project_id):
        with self.store.transaction() as db:
            self.store._project(db, owner, project_id)

    def _reviewer(self, context):
        factory = getattr(self.reviewer, 'for_context', None)
        return (factory(context['owner'], context['project_id'], context['source'],
                source_sha256=context['film_sha256'])
            if callable(factory) else self.reviewer)

    def freeze(self, owner, project_id, source):
        self.authorize(owner, project_id)
        operation = self.film.operation('render', source['version_id'], owner)
        require(operation['project_id'] == project_id and operation['status'] == 'completed'
            and operation['stop_proven'] is True and not operation['cancel_requested']
            and operation['request']['production_run_id'] == source['run_id'],
            'production_refinement_source_not_completed', 409)
        snapshot = self.film.render_snapshot(operation['id'])
        checked = validate_film_render_result(operation['result'], snapshot,
            runtime_fingerprint=snapshot['runtime_fingerprint'])
        raw = self.film.video(owner, operation['id'])
        require(hashlib.sha256(raw).hexdigest() == source['sha256'] == checked['sha256']
            and len(raw) == checked['bytes'], 'production_refinement_source_changed', 409)
        request = operation['request']
        require(request['configuration_sha256'] == self.film.readiness().get('configuration_sha256'),
            'production_refinement_configuration_changed', 409)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            # Existing validator verifies semantic inputs, narrations and clock.
            current = dict(request, expected_revision=project['revision'])
            creation = self.film.creation if hasattr(self.film, 'creation') else self.film
            creation._film(db, owner, project_id, current)
            revision = project['revision']
        modules = []
        binding = None
        for version_id in request['module_version_ids']:
            package = self.film.module_package(owner, version_id)
            with self.store.transaction() as db:
                row = db.execute('SELECT operation_id FROM film_module_versions WHERE id=? AND owner=?',
                    (version_id, owner)).fetchone()
                require(row is not None, 'production_refinement_module_missing', 409)
            module_op = self.film.operation('module', row[0], owner)
            require(module_op['status'] == 'completed' and module_op['stop_proven'] is True,
                'production_refinement_module_not_completed', 409)
            original = module_op['request']
            require(original['film_sha256'] == request['film_sha256']
                and package['input_document']['target'] == request['module_targets'][len(modules)],
                'production_refinement_module_changed', 409)
            module_binding = module_op.get('library_binding', original.get('library_binding'))
            if not modules:
                binding = module_binding
            require(binding == module_binding, 'production_refinement_library_mismatch', 409)
            modules.append(dict(version_id=version_id, package_sha256=digest(package),
                resource_references=deepcopy(original.get('resource_references', [])),
                resource_authoring=deepcopy(original.get('resource_authoring'))))
        result = dict(source=deepcopy(source), owner=owner, project_id=project_id, expected_revision=revision,
            film=deepcopy(request['film']), film_sha256=request['film_sha256'],
            configuration_sha256=request['configuration_sha256'], module_targets=deepcopy(request['module_targets']),
            module_version_ids=deepcopy(request['module_version_ids']), modules=modules,
            library_binding=deepcopy(binding), snapshot_sha256=snapshot['sha256'],
            review_profile=self.reviewer.profile, review_deadline_at=time.time() + self.review_timeout)
        require(digest(result['film']) == result['film_sha256'], 'production_refinement_film_changed', 409)
        self._reviewer(result).preflight(result['review_deadline_at'])
        return result

    def _bundle(self, key, context, state):
        # UUID task identity controls the directory; caller text is never a path.
        directory = self.root / state['id'] / 'review'
        if not directory.exists():
            require(self.sampling_client is not None,
                'production_refinement_shared_sampling_unavailable', 503)
            parent = directory.parent
            parent.mkdir(exist_ok=True)
            raw = self.film.video(context['owner'], context['source']['version_id'])
            require(hashlib.sha256(raw).hexdigest() == context['source']['sha256'],
                'production_refinement_source_changed', 409)
            duration = context['film']['total_seconds']
            # Uniform bounded evidence over the entire duration. This is
            # explicitly an overview; it cannot verify unsampled transitions.
            fps = min(3, 23 / duration)
            require(fps >= .25, 'production_refinement_review_duration_budget', 422)
            goal = ('检查这条完整表达的可见构图、因果清晰度与镜头衔接。只依据提供的有序抽帧指出可核对问题，'
                '不能声称连续播放或听过音频；不因存在限制而编造问题。整片目标：' + context['film']['understanding_goal'])
            sampling_id = self._sampling_id(key, context, state)
            source = dict(owner=context['owner'], project_id=context['project_id'],
                render_id=context['source']['version_id'], production_run_id=context['source']['run_id'],
                media_sha256=context['source']['sha256'], film_sha256=context['film_sha256'],
                snapshot_sha256=context['snapshot_sha256'])
            policy = dict(strategy='formal_legacy', clip_id=state['id'], goal=goal,
                source_sha256=context['film_sha256'], sample_fps=fps, frame_limit=24,
                review_profile=context['review_profile'])
            intent = dict(operation_id=sampling_id, source=source, policy=policy,
                deadline_at=context['review_deadline_at'])
            intent_path = parent / 'sampling-intent.json'
            if intent_path.exists():
                require(intent_path.read_bytes() == canonical_bytes(intent),
                    'production_refinement_sampling_identity_changed', 409)
            else:
                with intent_path.open('xb') as stream:stream.write(canonical_bytes(intent))
            sampled = self.sampling_client.prepare_media(context['owner'], sampling_id, source, policy, raw,
                deadline_at=context['review_deadline_at'])
            if sampled['state'] in {'uploading', 'queued', 'running', 'cancelling'}:
                raise ServiceError('production_refinement_sampling_pending', 503)
            require(sampled['state'] == 'completed' and sampled.get('stop_proven') is True,
                sampled.get('error') or 'production_refinement_sampling_failed', 409)
            profile, sampled_document, exported, manifest = self.sampling_client.export_bundle(
                context['owner'], sampling_id)
            require(profile == context['review_profile']
                and digest(sampled_document) == hashlib.sha256(exported["inputs/"+visual.REVIEW_INPUT_NAME]).hexdigest()
                and manifest.get('manifest_sha256') == sampled.get('sampling_manifest_sha256'),
                'production_refinement_sampling_identity_changed', 409)
            reference = dict(sampling_operation_id=sampling_id,
                request_sha256=sampled['request_sha256'], manifest_sha256=sampled['sampling_manifest_sha256'],
                source=source, policy=policy)
            ref_path = parent / 'sampling-reference.json'
            if ref_path.exists():
                require(ref_path.read_bytes() == canonical_bytes(reference),
                    'production_refinement_sampling_identity_changed', 409)
            else:
                with ref_path.open('xb') as stream:stream.write(canonical_bytes(reference))
            temporary = parent / ('review-preparing-' + str(uuid4()))
            try:
                temporary.mkdir()
                for name, content in exported.items():
                    target = temporary / name
                    require(target.resolve().is_relative_to(temporary.resolve()),
                        'production_refinement_sampling_identity_changed', 409)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream:stream.write(content)
                restored_profile, restored_document, _ = load_bundle(temporary)
                require(restored_profile == profile and restored_document == sampled_document,
                    'production_refinement_sampling_identity_changed', 409)
                try:
                    temporary.rename(directory)
                except FileExistsError:
                    require(temporary.resolve().is_relative_to(parent.resolve()),
                        'production_refinement_sampling_identity_changed', 409)
                    shutil.rmtree(temporary)
            except BaseException:
                if temporary.exists():
                    require(temporary.resolve().is_relative_to(parent.resolve()),
                        'production_refinement_sampling_identity_changed', 409)
                    shutil.rmtree(temporary)
                raise
        profile, document, files = load_bundle(directory)
        require(profile == context['review_profile'] == self.reviewer.profile
            and document['media']['sha256'] == context['source']['sha256']
            and document['source_sha256'] == context['film_sha256'],
            'production_refinement_review_input_changed', 409)
        return document, files

    @staticmethod
    def _sampling_id(key, context, state):
        return 'formal-' + digest(dict(owner=context['owner'], key=key, task=state['id'],
            source=context['source'], snapshot_sha256=context['snapshot_sha256']))[:48]

    @staticmethod
    def validate_request(context, request):
        """Reject deterministically oversized user input before any allowance.

        The frozen author accepts 16,000 instruction characters. Preserve user
        text exactly and reserve 4,000 characters for later observed issues and
        limitations. This check does not execute a reviewer or create a child.
        """
        state = dict(kind=request['kind'], request=request, review=dict(issues=[], limitations=[]))
        for index in range(len(context['film']['scenes'])):
            try:
                instruction = ProductionRefinementDriver._instruction(context, state, index)
            except ServiceError as error:
                if error.code == 'production_refinement_instruction_too_large':
                    raise ServiceError('production_refinement_user_input_too_large', 422) from None
                raise
            require(len(instruction) <= MAX_USER_CHANGE_PAYLOAD,
                'production_refinement_user_input_too_large', 422)

    @staticmethod
    def _instruction(context, state, index):
        scene = context['film']['scenes'][index]
        final_scene = index == len(context['film']['scenes']) - 1
        film_end = context['film']['scenes'][-1]['end_seconds']
        issues = [row for row in state['review']['issues'] if time_range_intersects(
            row['start_seconds'], row['end_seconds'], scene['start_seconds'], scene['end_seconds'],
            include_end=final_scene)]
        request = state['request']
        items = [row for row in request.get('items') or [] if row['scope']['kind'] == 'whole_film'
            or row['scope']['start_seconds'] < scene['end_seconds']
            and row['scope']['end_seconds'] > scene['start_seconds']]
        if state['kind'] == 'requirements':
            # Extra reviewer findings are retained for the user, never an
            # excuse to widen explicitly scoped paid change requests.
            issues = [issue for issue in issues if any(item['scope']['kind'] == 'whole_film'
                or time_range_intersects(issue['start_seconds'], issue['end_seconds'],
                    item['scope']['start_seconds'], item['scope']['end_seconds'],
                    include_end=item['scope']['end_seconds'] == film_end) for item in items)]
        instruction = json.dumps(dict(task='本轮只修订一次，保留未受影响的表达，响应确切修改要求；下游保持与当前前任边界一致。',
            user_instruction=request.get('instruction'), requirements=items, observed_issues=issues,
            observation_limitations=state['review']['limitations']), ensure_ascii=False, separators=(',', ':'))
        require(len(instruction) <= 16000, 'production_refinement_instruction_too_large', 413)
        return instruction

    def _request(self, stage, context, state):
        if stage == 'module':
            child = next(row for row in state['children'] if row['stage'] == stage and row['status'] not in {'completed','failed','cancelled'})
            index = child['index']
            request = dict(expected_revision=context['expected_revision'], film=deepcopy(context['film']),
                film_sha256=context['film_sha256'], current_scene_index=index,
                predecessor_version_id=None if index == 0 else state['module_version_ids'][index - 1],
                target=deepcopy(context['module_targets'][index]), instruction=self._instruction(context, state, index),
                configuration_sha256=context['configuration_sha256'],
                base_module_version_id=context['module_version_ids'][index])
            original = context['modules'][index]
            if original['resource_references']:
                request['resource_references'] = deepcopy(original['resource_references'])
            if context['library_binding'] is not None:
                request['library_binding'] = deepcopy(context['library_binding'])
            # Reuse the exact already-generated Manim assets inherited through
            # base_module. Do not silently regenerate a different resource.
            return request
        return dict(expected_revision=context['expected_revision'], film=deepcopy(context['film']),
            film_sha256=context['film_sha256'], module_version_ids=deepcopy(state['module_version_ids']),
            module_targets=deepcopy(context['module_targets']), production_run_id=context['source']['run_id'],
            timeout_seconds=600, configuration_sha256=context['configuration_sha256'])

    def prepare(self, stage, key, context, state):
        if stage == 'review':
            reviewer = self._reviewer(context)
            document, files = self._bundle(key, context, state)
            deadline = context['review_deadline_at']
            reviewer.preflight(deadline)
            refs = reviewer.upload(document, files)
            return digest(document), reviewer.prepare_submission(key, document, deadline, refs)
        request = self._request(stage, context, state)
        if stage == 'module':
            index = request['current_scene_index']
            return digest(request), lambda: self.film.start_module(context['owner'], context['project_id'],
                context['film']['scenes'][index]['scene_id'], request, key)
        return digest(request), lambda: self.film.start_render(context['owner'], context['project_id'], request, key)

    def lookup(self, stage, key, context, state):
        if stage == 'review':
            reviewer = self._reviewer(context)
            try:
                job = reviewer.lookup(key)
            except ServiceError as error:
                if error.code in {'not_found', 'job_not_found'} or error.status == 404:
                    return None
                raise
            job = reviewer.poll(job['job_id'])
            active = job['state'] in {'queued', 'starting', 'running', 'cancelling'}
            status = job['state'] if active else 'completed' if job['state'] == 'completed' else 'cancelled' if job['state'] == 'cancelled' else 'failed'
            receipt = dict(id=job['job_id'], state=status, stop_proven=bool(job.get('stop_proven')), error=None, output=None)
            if status == 'completed':
                document, files = self._bundle(key, context, state)
                result = reviewer.collect(job['job_id'], digest(document), document)
                control = reviewer.model_frame_receipt(job['job_id'])
                observed = validate_model_frame_receipt(document, control, files=files)
                receipt['output'] = dict(issues=deepcopy(result['issues']), summary=result['summary'],
                    evidence_scope=result['evidence_scope'], limitations=deepcopy(result['limitations']),
                    observation=observed, model_frame_receipt_sha256=digest(control),
                    audio_reviewed=False, continuous_playback_reviewed=False)
            elif not active:
                receipt['error'] = 'production_refinement_review_' + job['state']
            receipt['stop_proof_sha256'] = digest(job.get('stop_proof')) if job.get('stop_proven') else None
            return receipt
        try:
            operation = (self.film.module_by_request_key(context['owner'], key) if stage == 'module'
                else self.film.render_by_request_key(context['owner'], key))
        except ServiceError as error:
            if error.status == 404:
                return None
            raise
        active = operation['status'] in {'queued', 'submitting', 'running', 'cancelling'}
        status = operation['status'] if active else 'completed' if operation['status'] == 'completed' else 'cancelled' if operation['status'] == 'cancelled' else 'failed'
        receipt = dict(id=operation['id'], state=status, stop_proven=operation['stop_proven'],
            error=operation.get('error'), output=None)
        if status == 'completed':
            if stage == 'module':
                package = self.film.module_package(context['owner'], operation['version_id'])
                receipt['output'] = dict(version_id=operation['version_id'], package_sha256=digest(package))
            else:
                snapshot = self.film.render_snapshot(operation['id'])
                checked = validate_film_render_result(operation['result'], snapshot,
                    runtime_fingerprint=snapshot['runtime_fingerprint'])
                raw = self.film.video(context['owner'], operation['id'])
                require(len(raw) == checked['bytes'] and hashlib.sha256(raw).hexdigest() == checked['sha256'],
                    'production_refinement_render_changed', 409)
                receipt['output'] = dict(source=dict(run_id=context['source']['run_id'],
                    version_id=operation['id'], sha256=checked['sha256']), snapshot_sha256=snapshot['sha256'])
        return receipt

    def cancel(self, stage, key, context, state):
        if stage == 'review':
            sampler = getattr(self, 'sampling_client', None)
            if sampler is not None:
                try:sampler.cancel(context['owner'], self._sampling_id(key, context, state))
                except ServiceError as error:
                    if error.status != 404:raise
            reviewer = self._reviewer(context)
            cancel_operation = getattr(reviewer, 'cancel_operation', None)
            if callable(cancel_operation):
                try:cancel_operation(key)
                except ServiceError as error:
                    if sampler is None or error.status != 404:raise
                return
            job = reviewer.lookup(key)
            reviewer.client.cancel(job['job_id'])
            return
        lookup = self.film.module_by_request_key if stage == 'module' else self.film.render_by_request_key
        try:
            operation = lookup(context['owner'], key)
        except ServiceError as error:
            if error.status == 404:
                return
            raise
        (self.film.cancel_module if stage == 'module' else self.film.cancel_render)(context['owner'], operation['id'])
