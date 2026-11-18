"""Source-preserving picture learning over the shared Agent/render boundary.

Reference reproduction and one independent changed-content check are different
works inside one project. Reads never advance execution or publish knowledge.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_picture_learning as protocol
from .animation_creation_service import _run_lock
from .learning_capabilities import LearningCapabilitiesMixin
from .learning_strategy_service import LearningStrategyMixin
from .picture_learning_store import (
    PictureLearningStore, TERMINAL, identifier, new_id, owner_id, require,
)

PREFIX = '/api/picture-lab'
MAX_UPLOAD = 8 * 1024 * 1024
MAX_VISION = 2 * 1024 * 1024
REMOTE_ACTIVE = frozenset({'queued', 'starting', 'running', 'cancelling'})


def _text(value, maximum, *, empty=False):
    require(type(value) is str and (empty or bool(value.strip())) and len(value) <= maximum
            and not any((ord(c) < 32 and c not in '\n\r\t') or 0xD800 <= ord(c) <= 0xDFFF
                        for c in value))
    return value


def _criteria(value):
    require(type(value) is list and len(value) <= 32)
    return [_text(item, 2000) for item in value]


def _image(raw, mime):
    # Decode rather than trusting the file extension, MIME or container header.
    from PIL import Image, UnidentifiedImageError
    require(type(raw) is bytes and 1 <= len(raw) <= MAX_UPLOAD, 'picture_learning_reference_size', 413)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format in {'PNG', 'JPEG'} and image.width * image.height <= 32_000_000
                    and 1 <= image.width <= 7680 and 1 <= image.height <= 4320,
                    'picture_learning_reference_format')
            actual = 'image/png' if image.format == 'PNG' else 'image/jpeg'
            require(mime == actual, 'picture_learning_reference_mime_mismatch')
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ServiceError('picture_learning_reference_format', 422) from None
    return width, height


class PictureLearningService(LearningCapabilitiesMixin, LearningStrategyMixin):
    protocol = protocol
    prefix = PREFIX
    store_type = PictureLearningStore
    start_fields = frozenset({'request_id', 'title', 'goal', 'reference_ids', 'criteria',
        'width', 'height', 'use_wikiskill', 'max_rounds', 'max_seconds', 'allow_collaboration',
        'allow_probes', 'capability_selection'})

    def __init__(self, root, *, author_gateway, review_gateway, renderer,
                 library_adapter=None, resource_stage=None, clock=time.time):
        self.store = self.store_type(root)
        self.root = self.store.root
        self.author, self.reviewer = author_gateway, review_gateway
        self.renderer, self.library_adapter, self.clock = renderer, library_adapter, clock
        self.resource_stage = resource_stage

    def _enabled(self):
        return self.author is not None and self.reviewer is not None and self.renderer is not None

    def _reference(self, owner, identity):
        return self.store.read('references', owner, identity)

    def _reference_public(self, row):
        return {key: deepcopy(row[key]) for key in
                ('id', 'file_name', 'mime_type', 'sha256', 'width', 'height', 'created_at')} | {
                    'url': self.prefix + '/references/' + row['id'] + '/media'}

    def upload_reference(self, owner, body):
        owner_id(owner)
        require(type(body) is dict and set(body) == {
            'request_id', 'file_name', 'mime_type', 'content_base64'})
        identifier(body['request_id'])
        existing = self.store.request(owner, 'reference', body['request_id'], body)
        if existing:
            return self._reference_public(self._reference(owner, existing))
        name = _text(body['file_name'], 240)
        require('/' not in name and '\\' not in name and name not in {'.', '..'})
        require(type(body['mime_type']) is str and body['mime_type'] in {'image/png', 'image/jpeg'})
        encoded = body['content_base64']
        require(type(encoded) is str and len(encoded) <= (MAX_UPLOAD + 2) // 3 * 4,
                'picture_learning_reference_size', 413)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            raise ServiceError('picture_learning_reference_encoding', 422) from None
        width, height = _image(raw, body['mime_type'])
        sha = self.store.blob(raw)
        observed_sha, observed_mime = self._vision_copy(raw, body['mime_type'])
        row = dict(owner=owner, id=new_id(), file_name=name, mime_type=body['mime_type'],
                   sha256=sha, width=width, height=height, observed_sha256=observed_sha,
                   observed_mime_type=observed_mime, created_at=self.clock())
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.store.request(owner, 'reference', body['request_id'], body, db=db)
            if existing:
                row = self._reference(owner, existing)
            else:
                self.store.put('references', row, db=db, insert=True)
                self.store.bind_request(owner, 'reference', body['request_id'], body, row['id'], db)
        return self._reference_public(row)

    # HTTP adapters can use either explicit or conventional action naming.
    reference = upload_reference

    def reference_artifact(self, owner, reference_id):
        row = self._reference(owner, reference_id)
        self.store.read_blob(row['sha256'])
        return self.store.blob_path(row['sha256']), row['mime_type']

    def _vision_copy(self, raw, mime):
        if len(raw) <= MAX_VISION:
            return self.store.blob(raw), mime
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as original:
            image = original.convert('RGB')
            image.thumbnail((1920, 1080))
            for quality in (95, 90, 82, 72):
                out = io.BytesIO()
                image.save(out, format='JPEG', quality=quality, optimize=True)
                encoded = out.getvalue()
                if len(encoded) <= MAX_VISION:
                    return self.store.blob(encoded), 'image/jpeg'
        raise ServiceError('picture_learning_vision_image_too_large', 413)

    @staticmethod
    def _budget(body, default=None):
        default = default or {}
        rounds = body.get('max_rounds', default.get('max_rounds', 3))
        seconds = body.get('max_seconds', default.get('max_seconds', 900))
        require(type(rounds) is int and 1 <= rounds <= 8 and type(seconds) is int
                and 60 <= seconds <= 3600, 'picture_learning_budget_invalid')
        return dict(max_rounds=rounds, max_seconds=seconds)

    def _freeze_knowledge(self, owner, experiment_id, enabled, *, prior=None):
        if prior is not None:
            return deepcopy(prior['library_binding']), deepcopy(prior['knowledge_snapshot'])
        if self.library_adapter is None:
            return dict(batch_id=experiment_id, use_wikiskill=enabled, release_id=None,
                        release_sha256=None, publisher_owner=None, release_sequence=None), None
        binding = self.library_adapter.pin(owner, experiment_id, use_wikiskill=enabled)
        snapshot = self.library_adapter.laboratory_snapshot(owner, binding) if enabled else None
        return binding, snapshot

    def start(self, owner, body):
        owner_id(owner)
        require(type(body) is dict and {'request_id', 'title', 'goal', 'reference_ids'} <= set(body)
                <= self.start_fields)
        require(self._enabled(), 'picture_learning_unavailable', 503)
        identifier(body['request_id'])
        existing = self.store.request(owner, 'project', body['request_id'], body)
        if existing:
            return self.get(owner, existing)
        _text(body['title'], 200)
        _text(body['goal'], 8000)
        refs = body['reference_ids']
        require(type(refs) is list and 1 <= len(refs) <= 4
                and all(type(item) is str for item in refs) and len(set(refs)) == len(refs))
        references = [self._reference(owner, identity) for identity in refs]
        width, height = body.get('width', 1280), body.get('height', 720)
        require(type(width) is int and 320 <= width <= 1920 and type(height) is int
                and 180 <= height <= 1080, 'picture_learning_dimensions_invalid')
        enabled = body.get('use_wikiskill', True)
        require(type(enabled) is bool)
        now, pid, eid = self.clock(), new_id(), new_id()
        binding, snapshot = self._freeze_knowledge(owner, eid, enabled)
        project = dict(id=pid, owner=owner, request_id=body['request_id'], title=body['title'],
            goal=body['goal'], reference_ids=list(refs), created_at=now, updated_at=now,
            best_candidate_id=None, adopted_candidate_id=None, latest_experiment_id=eid)
        frozen = dict(goal=body['goal'], criteria=_criteria(body.get('criteria', [])),
            width=width, height=height, reference_ids=list(refs), references=references,
            feedback='', use_wikiskill=enabled, library_binding=binding, knowledge_snapshot=snapshot,
            base_candidate_id=None, base_source=None, base_observation=None, learned_method=None,
            input_boundary='frozen_for_entire_experiment',
            runtime_capabilities=self._runtime_capabilities())
        self._freeze_learning_options(body, frozen)
        frozen.update(self._extra_frozen(owner, body, references))
        run = self._new_experiment(owner, pid, eid, body['request_id'], 'initial', frozen,
                                   self._budget(body), now)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.store.request(owner, 'project', body['request_id'], body, db=db)
            if not existing:
                self.store.put('projects', project, db=db, insert=True)
                self.store.put('experiments', run, db=db, insert=True)
                self.store.bind_request(owner, 'project', body['request_id'], body, pid, db)
        return self.get(owner, existing or pid)

    def _extra_frozen(self, owner, body, references):
        return {}

    def _baseline_observation(self, candidate):
        return dict(sha256=candidate['observed_sha256'], suffix=candidate['observed_suffix'])

    @staticmethod
    def _new_experiment(owner, pid, eid, request_id, mode, frozen, budget, now):
        return dict(id=eid, owner=owner, project_id=pid, request_id=request_id, mode=mode,
            frozen=frozen, frozen_sha256=digest(frozen), budget=budget,
            state='queued', stage='author', work_kind='reproduction', round=1,
            candidates=[], active_candidate_id=None, operation_ids=[], active_operation_id=None,
            best_candidate_id=None, transfer_task=None, transfer_status='pending',
            stop_requested=False, stop_proven=False, stop_reason=None, error=None,
            created_at=now, updated_at=now, deadline_at=now + budget['max_seconds'])

    def _all_candidates(self, owner, project_id):
        return [candidate for run in self.store.list('experiments', owner, project_id=project_id)
                for candidate in run['candidates']]

    def _context_candidates(self, run):
        """Read prior works by identity without adding duplicate library works.

        A technical retry keeps the prior experiment's reviewed comparison
        context. Those works remain owned by their original experiment.
        """
        identities = run.get('inherited_candidate_ids', [])
        if not identities:
            return run['candidates']
        available = {candidate['id']: candidate for candidate in
                     self._all_candidates(run['owner'], run['project_id'])}
        require(all(identity in available for identity in identities),
                'picture_learning_retry_context_missing', 409)
        return [available[identity] for identity in identities] + run['candidates']

    def next_experiment(self, owner, project_id, body):
        require(type(body) is dict and {'request_id', 'mode'} <= set(body)
                <= {'request_id', 'mode', 'feedback', 'use_wikiskill', 'max_rounds', 'max_seconds',
                    'base_candidate_id', 'allow_collaboration', 'allow_probes', 'capability_selection'})
        identifier(body['request_id'])
        require(type(body['mode']) is str and body['mode'] in {'continue', 'fresh', 'retry'})
        kind = 'experiment:' + identifier(project_id)
        existing = self.store.request(owner, kind, body['request_id'], body)
        if existing:
            return self.get(owner, project_id)
        project = self.store.read('projects', owner, project_id)
        prior = self.store.read('experiments', owner, project['latest_experiment_id'])
        require(prior['state'] in TERMINAL, 'picture_learning_experiment_active', 409)
        mode, now, eid = body['mode'], self.clock(), new_id()
        retry = mode == 'retry'
        if retry:
            require(prior['state'] in {'failed', 'cancelled'}, 'picture_learning_retry_not_applicable', 409)
            require(set(body) <= {'request_id', 'mode'}, 'picture_learning_retry_input_frozen', 409)
            # An uncertain operation is not proven stopped; retry cannot duplicate it.
            require(prior['stop_proven'], 'picture_learning_stop_unproven', 409)
            frozen, budget = deepcopy(prior['frozen']), deepcopy(prior['budget'])
        else:
            frozen = deepcopy(prior['frozen'])
            frozen['feedback'] = _text(body.get('feedback', ''), 8000, empty=True)
            frozen['use_wikiskill'] = body.get('use_wikiskill', True)
            require(type(frozen['use_wikiskill']) is bool)
            frozen['library_binding'], frozen['knowledge_snapshot'] = self._freeze_knowledge(
                owner, eid, frozen['use_wikiskill'])
            frozen['runtime_capabilities'] = self._runtime_capabilities()
            self._freeze_learning_options(body, frozen, prior=prior['frozen'] if mode == 'continue' else None)
            frozen.update(base_source=None, base_candidate_id=None, base_observation=None, learned_method=None)
            if mode == 'continue':
                chosen = body.get('base_candidate_id') or project['adopted_candidate_id'] or project['best_candidate_id']
                candidate = next((item for item in self._all_candidates(owner, project_id)
                                  if item['id'] == chosen and item['work_kind'] == 'reproduction'), None)
                require(candidate is not None, 'picture_learning_baseline_required', 409)
                require(self._has_picture(candidate), 'picture_learning_baseline_not_rendered', 409)
                frozen.update(base_candidate_id=chosen, base_source=deepcopy(candidate['source']),
                    base_observation=self._baseline_observation(candidate),
                    learned_method=deepcopy(candidate.get('method')))
            budget = self._budget(body, prior['budget'])
        run = self._new_experiment(owner, project_id, eid, body['request_id'], mode, frozen, budget, now)
        if mode == 'continue' and candidate.get('resource_descriptor'):
            run['resource_baseline_descriptor'] = deepcopy(candidate['resource_descriptor'])
            run['resource_prior_review'] = deepcopy(candidate.get('review'))
        if retry:
            run.update(round=prior['round'], work_kind=prior['work_kind'],
                       transfer_task=deepcopy(prior['transfer_task']),
                       transfer_status=prior['transfer_status'])
            if 'transfer_method' in prior:
                run['transfer_method'] = deepcopy(prior['transfer_method'])
            # Keep the actual comparison lineage without republishing every
            # old work as a candidate of the retry experiment.
            run['inherited_candidate_ids'] = list(dict.fromkeys(
                prior.get('inherited_candidate_ids', []) + [item['id'] for item in prior['candidates']
                    if (item['id'] != prior['active_candidate_id']
                        or prior['stage'] not in {'render', 'review', 'transfer_plan'})
                    and item.get('review') is not None]))
            run['best_candidate_id'] = prior['best_candidate_id']
            original_operation = next((self.store.read('operations', owner, identity)
                for identity in reversed(prior['operation_ids'])
                if self.store.read('operations', owner, identity)['stage'] == prior['stage']
                and self.store.read('operations', owner, identity)['status'] != 'completed'), None)
            if original_operation is not None:
                require(digest(original_operation['input']) == original_operation['input_sha256'],
                        'picture_learning_retry_context_changed', 409)
                run['technical_retry_context'] = dict(source_experiment_id=prior['id'],
                    source_operation_id=original_operation['id'], stage=prior['stage'],
                    input=deepcopy(original_operation['input']),
                    input_sha256=original_operation['input_sha256'], consumed=False)
            # Restart the failed technical stage with its effective source and
            # frozen evidence. This is not a new artistic direction or a fresh
            # creator call when rendering/reviewing had already been reached.
            previous = next((item for item in prior['candidates']
                             if item['id'] == prior['active_candidate_id']), None)
            if previous is not None and prior['stage'] in {'render', 'review', 'transfer_plan'}:
                restored = deepcopy(previous)
                restored['retry_of_candidate_id'] = restored['id']
                restored['id'] = new_id()
                for kind, artifact in restored.get('artifacts', {}).items():
                    artifact['id'] = restored['id'] + '-' + kind
                run['candidates'].append(restored)
                run.update(active_candidate_id=restored['id'], stage=prior['stage'])
                if run['best_candidate_id'] == previous['id']:
                    run['best_candidate_id'] = restored['id']
            self._learning_restore_retry(prior, run)
            self._restore_learning_resources(prior, run)
            if prior.get('learning_strategy') or prior.get('resource_plan'):
                run['stage'] = prior['stage']
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.store.request(owner, kind, body['request_id'], body, db=db)
            current = self.store.read('projects', owner, project_id, db=db)
            latest = self.store.read('experiments', owner, current['latest_experiment_id'], db=db)
            require(existing is not None or latest['id'] == prior['id'] and latest['state'] in TERMINAL,
                    'picture_learning_experiment_active', 409)
            if not existing:
                self.store.put('experiments', run, db=db, insert=True)
                current.update(latest_experiment_id=eid, updated_at=now)
                self.store.put('projects', current, db=db)
                self.store.bind_request(owner, kind, body['request_id'], body, eid, db)
        return self.get(owner, project_id)

    experiment = next_experiment

    def pending(self, limit=100):
        require(type(limit) is int and 1 <= limit <= 1000)
        return [dict(owner=run['owner'], id=run['id'])
                for run in reversed(self.store.list('experiments')) if run['state'] not in TERMINAL][:limit]

    def _save(self, run):
        run['updated_at'] = self.clock()
        return self.store.put('experiments', run)

    def _candidate(self, run):
        internal = self._learning_candidate(run)
        if internal is not None:
            return internal
        return next(item for item in run['candidates'] if item['id'] == run['active_candidate_id'])

    def _frames(self, run, mode):
        context_candidates = self._context_candidates(run)
        frames = []
        if run['work_kind'] == 'reproduction':
            for index, ref in enumerate(run['frozen']['references']):
                suffix = '.png' if ref['observed_mime_type'] == 'image/png' else '.jpg'
                frames.append(dict(frame_id=f'reference-{index + 1}', file_name=f'reference-{index + 1}{suffix}',
                    sha256=ref['observed_sha256'], purpose='reference'))
        if mode != 'create':
            candidate = self._candidate(run)
            frames.append(dict(frame_id='output-1', file_name='output-1' + candidate['observed_suffix'],
                sha256=candidate['observed_sha256'], purpose='output'))
            if run['work_kind'] == 'reproduction':
                previous = [item for item in context_candidates
                            if item['id'] != candidate['id'] and item['work_kind'] == 'reproduction'
                            and item.get('picture_sha256') and item.get('review')]
                if previous:
                    baseline = next((item for item in previous if item['id'] == run['best_candidate_id']), None)
                    baseline = baseline or max(previous, key=lambda item: item['review']['score'])
                    frames.append(dict(frame_id='baseline-1',
                        file_name='baseline-1' + baseline['observed_suffix'],
                        sha256=baseline['observed_sha256'], purpose='baseline'))
                elif run['frozen'].get('base_observation'):
                    baseline = run['frozen']['base_observation']
                    frames.append(dict(frame_id='baseline-1', file_name='baseline-1' + baseline['suffix'],
                                       sha256=baseline['sha256'], purpose='baseline'))
        elif run['work_kind'] == 'reproduction':
            # Creator sees its real previous output, not only reviewer prose.
            candidate = next((item for item in reversed(context_candidates)
                              if item.get('picture_sha256') and item['work_kind'] == 'reproduction'), None)
            if candidate:
                best = next((item for item in context_candidates if item['id'] == run['best_candidate_id']), None)
                best = best or candidate
                frames.append(dict(frame_id='baseline-1', file_name='baseline-1' + best['observed_suffix'],
                    sha256=best['observed_sha256'], purpose='baseline'))
                if candidate['id'] != best['id']:
                    frames.append(dict(frame_id='output-1', file_name='output-1' + candidate['observed_suffix'],
                        sha256=candidate['observed_sha256'], purpose='output'))
            elif run['frozen'].get('base_observation'):
                baseline = run['frozen']['base_observation']
                frames.append(dict(frame_id='baseline-1', file_name='baseline-1' + baseline['suffix'],
                                   sha256=baseline['sha256'], purpose='baseline'))
        return frames

    def _document(self, run, mode):
        frozen = run['frozen']
        transfer = run['work_kind'] == 'transfer'
        context_candidates = self._context_candidates(run)
        candidate = next((item for item in reversed(context_candidates)
                          if item.get('review') is not None and item['work_kind'] == 'reproduction'), None)
        best = next((item for item in context_candidates if item['id'] == run['best_candidate_id']), None)
        base = None if transfer else (best['source'] if best else candidate['source']
                                     if candidate else frozen['base_source'])
        method = run.get('transfer_method') if transfer else frozen.get('learned_method')
        goal = run['transfer_task']['goal'] if transfer else frozen['goal']
        criteria = run['transfer_task']['criteria'] if transfer else frozen['criteria']
        capabilities = frozen['runtime_capabilities']
        constraints = dict(width=frozen['width'], height=frozen['height'],
                           allowed_engines=capabilities.get('allowed_engines', ['html-svg', 'canvas']))
        # Runtime capability metadata is supplied as data; no drawing recipe
        # is injected by the host in place of the creator's own implementation.
        if capabilities.get('runtime_identity') is not None:
            constraints['runtime_identity'] = capabilities['runtime_identity']
        assets = deepcopy(capabilities.get('assets', {}))
        document = dict(schema=self.protocol.INPUT_SCHEMA, mode=mode, project_id=run['project_id'],
            experiment_id=run['id'], work_kind=run['work_kind'], goal=goal,
            criteria=deepcopy(criteria), constraints=constraints,
            observation=dict(frames=self._frames(run, mode)), base_source=deepcopy(base),
            feedback=frozen['feedback'], knowledge_snapshot=deepcopy(frozen['knowledge_snapshot']),
            prior_review=deepcopy(candidate['review'] if candidate else None), learned_method=deepcopy(method),
            knowledge_binding=deepcopy(frozen['library_binding']), assets=assets, frozen_at=run['created_at'],
            selection=dict(base_candidate_id=best['id'] if best else frozen.get('base_candidate_id'),
                           previous_review_candidate_id=candidate['id'] if candidate else None))
        return self._learning_document(run, self._resource_document(run, document))

    def _operation(self, run):
        if run['active_operation_id']:
            return self.store.read('operations', run['owner'], run['active_operation_id'])
        mode = 'create' if run['stage'] == 'author' else run['stage']
        retry_context = run.get('technical_retry_context')
        if retry_context and not retry_context['consumed'] and retry_context['stage'] == run['stage']:
            require(digest(retry_context['input']) == retry_context['input_sha256'],
                    'picture_learning_retry_context_changed', 409)
            document = deepcopy(retry_context['input'])
            document['experiment_id'] = run['id']
        else:
            document = self._document(run, mode if mode != 'render' else 'create')
        self.protocol.validate_input(document)
        identity = run['id'] + '-' + str(len(run['operation_ids']) + 1)
        operation = dict(id=identity, owner=run['owner'], experiment_id=run['id'], stage=run['stage'],
            status='reserved', input=document, input_sha256=digest(document), job_id=None,
            result=None, observation_receipt=None, created_at=self.clock())
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.put('operations', operation, db=db, insert=True)
            run['active_operation_id'] = identity
            run['operation_ids'].append(identity)
            self.store.put('experiments', run, db=db)
        return operation

    def _finish_operation(self, run, operation):
        self.store.put('operations', operation)
        run['active_operation_id'] = None
        retry_context = run.get('technical_retry_context')
        if retry_context and retry_context['stage'] == operation['stage']:
            retry_context['consumed'] = True

    def _finish(self, run, reason, state='completed'):
        run.update(state=state, stop_reason=reason, stop_proven=True, active_operation_id=None)
        if run['transfer_status'] == 'pending':
            run['transfer_status'] = 'not_verified'
        return self._save(run)

    def _agent(self, run, operation):
        gateway = self.author if operation['stage'] == 'author' else self.reviewer
        document = operation['input']
        if operation['status'] == 'reserved':
            preflight = getattr(gateway, 'preflight', None)
            if callable(preflight):
                preflight(run['deadline_at'])
            refs = operation.get('input_refs')
            if refs is None:
                files = {self.protocol.INPUT_NAME: canonical_bytes(document)}
                files.update({frame['file_name']: self.store.read_blob(frame['sha256'])
                              for frame in document['observation']['frames']})
                refs = gateway.upload(document, files)
                operation['input_refs'] = deepcopy(refs)
                self.store.put('operations', operation)
            admit = gateway.prepare_submission(operation['id'], document, run['deadline_at'], refs)
            if not self.store.claim(run['owner'], operation['id'], now=self.clock()):
                return
            operation['status'] = 'claimed'
            # Claim committed before this one paid submission. An exception
            # keeps this original operation for lookup, never a second POST.
            try:
                job = admit()
            except Exception:
                run['state'] = 'submission_unknown'
                return
            operation.update(status='submitted', job_id=job['job_id'])
            self.store.put('operations', operation)
            return
        if operation.get('job_id'):
            job = gateway.poll(operation['job_id'])
        else:
            try:
                job = gateway.lookup(operation['id'])
            except ServiceError as error:
                if error.status == 404:
                    run['state'] = 'submission_unknown'
                    return
                raise
            if job is None:
                run['state'] = 'submission_unknown'
                return
            operation.update(status='submitted', job_id=job['job_id'])
            self.store.put('operations', operation)
        if job['state'] in REMOTE_ACTIVE:
            return
        if job.get('stop_proven') is not True:
            run['state'] = 'submission_unknown'
            return
        if job['state'] != 'completed':
            operation.update(status='failed', error='picture_learning_agent_' + job['state'])
            self._finish_operation(run, operation)
            run['error'] = dict(code=operation['error'])
            self._finish(run, 'agent_failed', 'failed')
            return
        try:
            result = gateway.collect(job['job_id'], operation['input_sha256'], document)
            self.protocol.validate_result(result, operation['input_sha256'], document)
            receipt = gateway.model_frame_receipt(job['job_id'])
            expected = {frame['sha256'] for frame in document['observation']['frames']}
            observed = {row.get('sha256') for row in receipt.get('images', [])
                        if row.get('model_request_completed') is True}
            require(expected <= observed, 'picture_learning_frame_transport_incomplete', 409)
        except ServiceError as error:
            if error.status >= 500:
                raise
            operation.update(status='failed', error=error.code)
            self._finish_operation(run, operation)
            run['error'] = dict(code=error.code)
            self._finish(run, 'invalid_result', 'failed')
            return
        operation.update(status='completed', result=result, observation_receipt=receipt)
        self._finish_operation(run, operation)
        resource_proposed = (self._adopt_resource_proposal(run, operation, result)
            if operation['stage'] == 'author' else False)
        if operation['stage'] == 'author':
            require(run['frozen'].get('capability_selection', {}).get('creation') != ['manim']
                or resource_proposed or run.get('resource_ready'),
                'picture_learning_manim_resource_required', 409)
        if self._learning_consume_agent_result(run, operation, result):
            return
        if resource_proposed:
            run['stage'] = 'learning_resources'
            return
        if operation['stage'] == 'author':
            source = result['source']
            self._validate_learning_source(run, source)
            candidate = dict(id=new_id(), work_kind=run['work_kind'], role=run['work_kind'],
                round=run['round'], source=source, source_sha256=source['source_sha256'],
                method=deepcopy(result.get('method')), review=None, status='authored',
                created_at=self.clock(), author_operation_id=operation['id'])
            if run.get('resource_ready'):
                candidate['resource_descriptor'] = deepcopy(run['resource_context']['generated_resources'][0])
                candidate['resource_plan'] = deepcopy(run['resource_plan'])
            candidate['artifacts'] = {'source': dict(id=candidate['id'] + '-source',
                sha256=self.store.blob(canonical_bytes(source)), mime_type='application/json')}
            run['candidates'].append(candidate)
            run.update(active_candidate_id=candidate['id'], stage='render')
        elif operation['stage'] == 'review':
            candidate = self._candidate(run)
            candidate.update(review=deepcopy(result['review']), status='reviewed',
                             review_operation_id=operation['id'])
            if run['work_kind'] == 'transfer':
                run['transfer_status'] = 'reviewed'
                self._finish(run, 'learning_complete')
                return
            self._select_best(run)
            review = result['review']
            previous = [item['review']['score'] for item in self._context_candidates(run)[:-1]
                        if item['work_kind'] == 'reproduction' and item.get('review')]
            no_progress = len(previous) >= 2 and review['score'] <= max(previous[-2:])
            if (run['round'] < run['budget']['max_rounds'] and not review['stop_recommended']
                    and not no_progress and run['deadline_at'] - self.clock() >= 90):
                run.update(stage='author', round=run['round'] + 1)
                best = next((item for item in self._context_candidates(run)
                    if item['id'] == run['best_candidate_id']), candidate)
                self._next_learning_resource_cycle(run, best)
            else:
                run['reproduction_stop_reason'] = ('no_progress' if no_progress else
                    'review_recommends_stop' if review['stop_recommended'] else 'round_budget_reached')
                if result.get('transfer_task'):
                    self._begin_transfer(run, result['transfer_task'])
                elif run['deadline_at'] - self.clock() >= 90:
                    run['stage'] = 'transfer_plan'
                else:
                    self._finish(run, 'budget_exhausted')
        else:
            self._begin_transfer(run, result['transfer_task'])

    def _select_best(self, run):
        candidates = [item for item in self._context_candidates(run) if item['work_kind'] == 'reproduction'
                      and item.get('review') is not None and self._has_picture(item)]
        if not candidates:
            return
        # Prefer an actual side-by-side judgement when supplied; scores remain
        # an internal fallback and never confer developer artistic approval.
        latest = candidates[-1]
        previous_best = next((item for item in candidates
                              if item['id'] == run.get('best_candidate_id')), None)
        preference = latest['review'].get('preferred')
        if previous_best is not None and preference == 'baseline':
            best = previous_best
        elif preference == 'output':
            best = latest
        else:
            best = max(candidates, key=lambda item: (item['review']['score'], -item['round']))
        run['best_candidate_id'] = best['id']
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self.store.read('projects', run['owner'], run['project_id'], db=db)
            existing = next((item for item in self._all_candidates(run['owner'], run['project_id'])
                             if item['id'] == project['best_candidate_id']), None)
            baseline_matches_existing = (existing is not None and
                (existing['id'] == run['frozen'].get('base_candidate_id')
                 or existing['id'] in {item['id'] for item in candidates[:-1]}))
            keep_baseline = baseline_matches_existing and preference == 'baseline'
            choose_output = baseline_matches_existing and preference == 'output' and best['id'] == latest['id']
            if not keep_baseline and (existing is None or choose_output
                    or best['review']['score'] > existing.get('review', {}).get('score', -1)):
                project['best_candidate_id'] = best['id']
            project['updated_at'] = self.clock()
            self.store.put('projects', project, db=db)

    def _begin_transfer(self, run, task):
        if task is None:
            run['transfer_status'] = 'no_task'
            return self._finish(run, 'reproduction_complete')
        best = next((item for item in self._context_candidates(run) if item['id'] == run['best_candidate_id']), None)
        run['transfer_task'] = deepcopy(task)
        if run['deadline_at'] - self.clock() < 90 or best is None or best.get('method') is None:
            return self._finish(run, 'budget_exhausted' if run['deadline_at'] - self.clock() < 90
                                else 'method_not_available_for_transfer')
        run.update(work_kind='transfer', stage='author', transfer_status='running',
                   transfer_method=deepcopy(best['method']))
        self._next_learning_resource_cycle(run, transfer=True)

    def _render(self, run, operation):
        candidate = self._candidate(run)
        if operation['status'] == 'reserved':
            if not self.store.claim(run['owner'], operation['id'], now=self.clock()):
                return
            operation['status'] = 'claimed'
            result = self.renderer.render(operation['id'], candidate['source'], operation['input'],
                                          deadline_at=run['deadline_at'])
        else:
            recover = getattr(self.renderer, 'recover', None) or self.renderer.lookup
            result = recover(operation['id'])
        if not result or result.get('status') == 'unknown' or result.get('stop_proven') is not True:
            run['state'] = 'submission_unknown'
            return
        if result.get('status') != 'rendered' or result.get('technical_passed') is not True:
            operation.update(status='failed', result=result)
            self._finish_operation(run, operation)
            run['error'] = dict(code='picture_learning_render_failed')
            self._finish(run, 'render_failed', 'failed')
            return
        raw = Path(result['picture_path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == result['sha256'],
                'picture_learning_render_changed', 409)
        width, height = _image(raw, 'image/png')
        require((width, height) == (run['frozen']['width'], run['frozen']['height'])
                == (result['width'], result['height']), 'picture_learning_render_dimensions_changed', 409)
        sha = self.store.blob(raw)
        observed_sha, observed_mime = self._vision_copy(raw, 'image/png')
        artifacts = {}
        for kind, content, mime in (
            ('picture', raw, 'image/png'),
            ('source', canonical_bytes(candidate['source']), 'application/json'),
            ('receipt', Path(result['receipt_path']).read_bytes(), 'application/json')):
            artifact_id = candidate['id'] + '-' + kind
            artifacts[kind] = dict(id=artifact_id, sha256=self.store.blob(content), mime_type=mime)
        candidate.update(picture_sha256=sha, width=result['width'], height=result['height'],
            observed_sha256=observed_sha, observed_suffix='.png' if observed_mime == 'image/png' else '.jpg',
            artifacts=artifacts, status='rendered', render_operation_id=operation['id'],
            observation_derivation=dict(original_sha256=sha, observed_sha256=observed_sha,
                original_preserved=True, scope='static_only'))
        operation.update(status='completed', result={key: value for key, value in result.items()
                                                     if not key.endswith('_path')})
        self._finish_operation(run, operation)
        run['stage'] = 'review'

    def _stop_or_expire(self, run):
        if run['stage'] == 'learning_resources':
            if not self._advance_learning_resources(run, cancelled=True):
                return self._save(run)
        operation = (self.store.read('operations', run['owner'], run['active_operation_id'])
                     if run['active_operation_id'] else None)
        if operation and operation['status'] == 'reserved':
            operation['status'] = 'cancelled'
            self._finish_operation(run, operation)
        if operation and operation['status'] not in {'reserved', 'completed', 'failed', 'cancelled'}:
            if operation['stage'] == 'render':
                recover = getattr(self.renderer, 'recover', None) or self.renderer.lookup
                result = recover(operation['id'])
                if not result or result.get('stop_proven') is not True:
                    run['state'] = 'stopping' if run['stop_requested'] else 'submission_unknown'
                    return self._save(run)
                # Keep an already completed picture even when the next review
                # cannot start. It remains visibly unreviewed.
                if result.get('status') == 'rendered':
                    self._render(run, operation)
                else:
                    operation.update(status='failed', result=result)
                    self._finish_operation(run, operation)
            else:
                gateway = self.author if operation['stage'] == 'author' else self.reviewer
                try:
                    job = gateway.poll(operation['job_id']) if operation.get('job_id') else gateway.lookup(operation['id'])
                except ServiceError as error:
                    if error.status >= 500 or error.status == 404:
                        run['state'] = 'stopping' if run['stop_requested'] else 'submission_unknown'
                        return self._save(run)
                    raise
                if not job:
                    return self._save(run)
                if job.get('state') in REMOTE_ACTIVE:
                    gateway.cancel(job['job_id'])
                    return self._save(run)
                if job.get('stop_proven') is not True:
                    return self._save(run)
                if job.get('state') == 'completed':
                    # Read-only collection keeps work already paid for and
                    # finished. It cannot start the next render/Agent task.
                    operation['job_id'] = job['job_id']
                    self._agent(run, operation)
                else:
                    operation.update(status='cancelled', job_id=job['job_id'])
                    self._finish_operation(run, operation)
        return self._finish(run, 'developer_stopped' if run['stop_requested'] else 'budget_exhausted',
                            'cancelled' if run['stop_requested'] else 'completed')

    def advance(self, owner, experiment_id):
        run = self.store.read('experiments', owner, experiment_id)
        if run['state'] in TERMINAL:
            return self.get(owner, run['project_id'])
        lock = self.root / 'locks' / (run['id'] + '.lock')
        with _run_lock(lock) as acquired:
            if not acquired:
                return self.get(owner, run['project_id'])
            run = self.store.read('experiments', owner, experiment_id)
            if run['state'] in TERMINAL:
                return self.get(owner, run['project_id'])
            try:
                if run['stop_requested'] or self.clock() >= run['deadline_at']:
                    self._stop_or_expire(run)
                else:
                    run.update(state='running', error=None)
                    retry = run.get('resource_retry_pending')
                    if retry:
                        previous = self.store.read('experiments', owner, retry)
                        self.resource_stage.restore_retry(owner, run, run['resource_plan'],
                            previous, previous['resource_plan'])
                        run.pop('resource_retry_pending', None)
                    if run['stage'] == 'learning_resources':
                        self._advance_learning_resources(run)
                    else:
                        operation = self._operation(run)
                        if run['stage'] == 'render':
                            self._render(run, operation)
                        else:
                            self._agent(run, operation)
                    self._save(run)
            except ServiceError as error:
                run['error'] = dict(code=error.code)
                active = (self.store.read('operations', owner, run['active_operation_id'])
                          if run['active_operation_id'] else None)
                if active and active['status'] not in {'reserved', 'completed', 'failed', 'cancelled'}:
                    # A definitive invalid result can finish only after the
                    # original attempt is actually stopped; otherwise reconcile.
                    run['state'] = 'submission_unknown'
                    self._save(run)
                elif error.status >= 500:
                    self._save(run)
                else:
                    self._finish(run, 'invalid_input_or_result', 'failed')
            except Exception:
                run.update(state='submission_unknown', error=dict(code='picture_learning_execution_unknown'))
                self._save(run)
        return self.get(owner, run['project_id'])

    def stop(self, owner, project_id):
        project = self.store.read('projects', owner, project_id)
        self.store.stop(owner, project['latest_experiment_id'])
        return self.get(owner, project_id)

    cancel = stop

    @staticmethod
    def _has_picture(candidate):
        return (bool(candidate.get('picture_sha256')) and bool(candidate.get('observed_sha256'))
                and candidate.get('observed_suffix') in {'.png', '.jpg'}
                and type(candidate.get('artifacts')) is dict
                and type(candidate['artifacts'].get('picture')) is dict
                and bool(candidate['artifacts']['picture'].get('id'))
                and candidate['artifacts']['picture'].get('sha256') == candidate['picture_sha256'])

    def adopt(self, owner, project_id, body):
        require(type(body) is dict and set(body) == {'candidate_id'})
        candidate = next((item for item in self._all_candidates(owner, project_id)
                          if item['id'] == body['candidate_id']
                          and item['work_kind'] == 'reproduction'), None)
        require(candidate is not None, 'picture_learning_candidate_not_found', 404)
        require(self._has_picture(candidate), 'picture_learning_candidate_not_rendered', 409)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self.store.read('projects', owner, project_id, db=db)
            latest = self.store.read('experiments', owner, project['latest_experiment_id'], db=db)
            require(latest['state'] in TERMINAL, 'picture_learning_experiment_active', 409)
            project.update(adopted_candidate_id=candidate['id'], updated_at=self.clock())
            self.store.put('projects', project, db=db)
        return self.get(owner, project_id)

    def _candidate_public(self, project_id, candidate):
        result = {key: deepcopy(value) for key, value in candidate.items()
                  if key not in {'source', 'observed_sha256', 'observed_suffix'}}
        result['engine'] = candidate['source']['engine']
        for kind, row in result.get('artifacts', {}).items():
            url = self.prefix + '/projects/' + project_id + '/artifacts/' + row['id']
            row['url'] = url
            result[kind + '_url'] = url
        return result

    def _run_public(self, run):
        result = {key: deepcopy(value) for key, value in run.items()
                  if key not in {'owner', 'frozen', 'candidates', 'operation_ids', 'transfer_method',
                                 'technical_retry_context', 'internal_attempts', 'learning_strategy',
                                 'resource_context', 'resource_draft_source', 'resource_baseline_descriptor'}}
        result['learning_progress'] = self._learning_progress(run)
        result['internal_attempts'] = [self._candidate_public(run['project_id'], item)
                                      for item in run.get('internal_attempts', [])]
        if run.get('resource_plan') and self.resource_stage is not None:
            result['resource_progress'] = self.resource_stage.view(run['owner'], run)
        result['frozen'] = {key: deepcopy(value) for key, value in run['frozen'].items()
                            if key not in {'knowledge_snapshot', 'base_source', 'references'}}
        result['candidates'] = [self._candidate_public(run['project_id'], item) for item in run['candidates']]
        result['operations'] = [{key: op.get(key) for key in ('id', 'stage', 'status', 'job_id', 'input_sha256')}
            for identity in run['operation_ids']
            for op in [self.store.read('operations', run['owner'], identity)]]
        return result

    def get(self, owner, project_id):
        project = self.store.read('projects', owner, project_id)
        runs = list(reversed(self.store.list('experiments', owner, project_id=project_id)))
        latest = next(run for run in runs if run['id'] == project['latest_experiment_id'])
        result = {key: deepcopy(value) for key, value in project.items() if key != 'owner'}
        result.update(schema='video.picture-learning-project.v1',
            references=[self._reference_public(self._reference(owner, identity))
                        for identity in project['reference_ids']],
            experiments=[self._run_public(run) for run in runs],
            candidates=[self._candidate_public(project_id, item) for run in runs for item in run['candidates']],
            state=latest['state'], stage=latest['stage'],
            latest_experiment_request_id=latest['request_id'],
            verification_scope='static_only', published=False)
        bridge = getattr(self, 'animation_bridge', None)
        if bridge is not None:
            result['animation_links'] = bridge.links(owner, project_id)
        return result

    def catalogue(self, owner):
        owner_id(owner)
        projects = []
        for project in self.store.list('projects', owner):
            runs = self.store.list('experiments', owner, project_id=project['id'])
            latest = next(item for item in runs if item['id'] == project['latest_experiment_id'])
            candidates = [item for run in runs for item in run['candidates'] if item.get('picture_sha256')]
            chosen = project['adopted_candidate_id'] or project['best_candidate_id']
            preview = next((item for item in candidates if item['id'] == chosen), None)
            if preview is None and candidates:
                preview = candidates[0]
            row = {key: deepcopy(value) for key, value in project.items() if key != 'owner'}
            row.update(state=latest['state'], experiments_count=len(runs),
                latest_experiment_request_id=latest['request_id'], reference_url=
                    PREFIX + '/references/' + project['reference_ids'][0] + '/media',
                preview_url=(PREFIX + '/projects/' + project['id'] + '/artifacts/'
                    + preview['artifacts']['picture']['id']) if preview else None)
            projects.append(row)
        return dict(schema='video.picture-learning-catalog.v1', enabled=self._enabled(),
            owner_key=hashlib.sha256(owner.encode('utf-8')).hexdigest(),
            availability=dict(enabled=self._enabled(), reason=None if self._enabled() else '图片创作执行器尚未配置'),
            projects=projects, limits=dict(reference_bytes=MAX_UPLOAD, reference_count=4,
                max_rounds=8, max_seconds=3600, max_width=1920, max_height=1080),
            runtime_capabilities=self._runtime_capabilities(), capabilities=self._capabilities_public(),
            verification_scope='static_only')

    catalog = catalogue

    def artifact(self, owner, project_id, artifact_id):
        self.store.read('projects', owner, project_id)
        identifier(artifact_id)
        all_works = self._all_candidates(owner, project_id) + [item
            for run in self.store.list('experiments', owner, project_id=project_id)
            for item in run.get('internal_attempts', [])]
        for candidate in all_works:
            for row in candidate.get('artifacts', {}).values():
                if row['id'] == artifact_id:
                    self.store.read_blob(row['sha256'])
                    return self.store.blob_path(row['sha256']), row['mime_type']
        raise ServiceError('picture_learning_artifact_not_found', 404)

    def inspect(self, owner, project_id, experiment_id):
        self.store.read('projects', owner, project_id)
        run = self.store.read('experiments', owner, experiment_id)
        require(run['project_id'] == project_id, 'picture_learning_not_found', 404)
        return dict(schema='video.picture-learning-input-inspection.v1', experiment_id=run['id'],
            frozen=deepcopy(run['frozen']), frozen_sha256=run['frozen_sha256'],
            operations=[self.store.read('operations', owner, identity) for identity in run['operation_ids']],
            scope='frozen_business_inputs_not_system_prompt',
            model_understanding_verified=False)

    def capture_wikiskill_records(self, owner):
        """Read a consistent picture fact snapshot for an explicit release.

        This contains source/method and actual stored comparison receipts;
        no new creative execution, visual approval or publication occurs.
        """
        records = []

        def add(identity, kind, content):
            content = deepcopy(content)
            records.append(dict(record_id=identity, kind=kind, content=content, sha256=digest(content)))

        owner_id(owner)
        with self.store.db() as db:
            # One connection/transaction gives publication a stable cut across
            # project, run and operation documents while workers may continue.
            db.execute('BEGIN')
            for row in db.execute('SELECT document FROM picture_projects WHERE owner=? ORDER BY id', (owner,)):
                project = json.loads(row['document'])
                add('picture-project:' + project['id'], 'picture_project',
                    dict(project, verification_scope='static_only',
                         artistic_acceptance='developer_adoption_is_not_global_validation'))
            for row in db.execute('SELECT document FROM picture_experiments WHERE owner=? ORDER BY id', (owner,)):
                run = json.loads(row['document'])
                add('picture-experiment:' + run['id'], 'picture_experiment',
                    {key: deepcopy(value) for key, value in run.items()
                     if key not in {'candidates', 'operation_ids'}} | {
                        'verification_scope': 'static_only',
                        'motion_verified': False, 'audio_verified': False})
                for candidate in run['candidates']:
                    add('picture-work:' + candidate['id'], 'picture_work',
                        dict(candidate, project_id=run['project_id'], experiment_id=run['id'],
                             verification_scope='static_only', motion_verified=False, audio_verified=False,
                             model_opinion_is_developer_acceptance=False))
            for row in db.execute('SELECT document FROM picture_operations WHERE owner=? ORDER BY id', (owner,)):
                op = json.loads(row['document'])
                identity = 'picture-operation:' + op['id']
                add(identity, 'picture_operation', {key: value for key, value in op.items()
                                                     if key not in {'input', 'result'}})
                add(identity + ':input', 'picture_operation_input', op['input'])
                if op.get('result') is not None:
                    add(identity + ':result', 'picture_operation_result', op['result'])
        return sorted(records, key=lambda row: row['record_id'])
