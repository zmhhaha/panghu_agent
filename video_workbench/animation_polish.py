"""Recoverable one-review, at-most-one-creator-revision coordination.

``step`` advances one durable stage without waiting or repeating paid work.
Inputs are frozen by the review reservation. Unknown submissions only look up
their original operation. The injected renderer must be an isolated, durable
adapter exposing render(operation_id, source, creation_input) and lookup(id).
After a render claim, only lookup is allowed on restart, including when the
process died before receiving a render response. No second Agent review runs.
"""
from __future__ import annotations

from copy import deepcopy

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from .animation_observation import load_bundle, validate_model_frame_receipt


REMOTE_ACTIVE = frozenset({'queued', 'starting', 'running', 'cancelling'})
LEDGER_ACTIVE = frozenset({'reserved', 'submitting', 'submission_unknown', 'submitted', 'running'})


def _require(condition, code='animation_polish_input_invalid', status=422):
    if not condition:
        raise ServiceError(code, status)


def _result(lineage_id, operation, state=None, **extra):
    return dict(schema='video.animation-polish-step.v1', lineage_id=lineage_id,
        stage=operation['stage'], operation_id=operation['id'], job_id=operation['job_id'],
        state=state or operation['status'], aesthetic_status='unreviewed',
        automatic_review_limit=1, automatic_creator_revision_limit=1, **extra)


class AnimationPolishCoordinator:
    """Business orchestration, independent from the execution service and UI.

    ``creation_input`` is the confirmed create.v2 task with null base/review;
    ``base_source`` names the exact already-rendered original scene. A lineage
    created in AnimationRevisionStore must retain that original source/version.
    The final state creates a candidate, never user acceptance or adoption.
    """

    def __init__(self, store, review_gateway, creation_gateway, renderer):
        self.store = store
        self.review_gateway = review_gateway
        self.creation_gateway = creation_gateway
        self.renderer = renderer

    def _remote_step(self, operation, gateway, document, files, deadline_at, *, review=False):
        lineage_id, operation_id = operation['lineage_id'], operation['id']
        status = operation['status']
        if status == 'reserved':
            # Reads and uploads do not consume a model attempt. A failure here
            # leaves the local reservation retryable, without a job POST.
            gateway.preflight(deadline_at)
            refs = gateway.upload(document, files)
            prepare = getattr(gateway, 'prepare_submission', None)
            admit = (prepare(operation_id, document, deadline_at, refs) if callable(prepare)
                     else lambda: gateway.submit(operation_id, document, deadline_at, refs))
            claimed = self.store.mark_submitting(operation_id)
            if not claimed['submission_claimed']:
                return _result(lineage_id, self.store.operation(operation_id), 'reconcile_required')
            try:
                job = admit()
            except ServiceError as error:
                if error.status >= 500:
                    operation = self.store.mark_submission_unknown(operation_id, error.code)
                else:
                    operation = self.store.finish(operation_id, 'failed',
                        result=dict(error=error.code, submission='definitively_rejected'), issues=[])
                return _result(lineage_id, operation)
            operation = self.store.mark_submitted(operation_id, job['job_id'])
            return _result(lineage_id, operation)
        if status in ('submitting', 'submission_unknown'):
            try:
                job = gateway.lookup(operation_id)
            except ServiceError as error:
                # Even an empty lookup cannot prove the earlier POST did not
                # reach admission. It never authorizes a new submit or key.
                if status == 'submitting':
                    operation = self.store.mark_submission_unknown(operation_id, error.code)
                return _result(lineage_id, operation, 'submission_unknown', last_error=error.code)
            operation = self.store.mark_submitted(operation_id, job['job_id'])
            return _result(lineage_id, operation, 'reconciled')
        if status not in ('submitted', 'running'):
            return _result(lineage_id, operation)
        job = gateway.poll(operation['job_id'])
        if job['state'] in REMOTE_ACTIVE:
            if status == 'submitted':
                operation = self.store.mark_running(operation_id)
            return _result(lineage_id, operation, 'waiting')
        if job['state'] != 'completed':
            outcome = 'cancelled' if job['state'] == 'cancelled' else 'failed'
            operation = self.store.finish(operation_id, outcome,
                result=dict(upstream_state=job['state'], job_id=operation['job_id']), issues=[])
            return _result(lineage_id, operation)
        artifact = None
        try:
            artifact = gateway.collect(operation['job_id'], digest(document), document)
            if review:
                receipt = gateway.model_frame_receipt(operation['job_id'])
                _require(type(receipt) is dict and receipt.get('job_id') == operation['job_id'],
                         'animation_polish_model_receipt_job_mismatch', 409)
                observation = validate_model_frame_receipt(document, receipt)
                result = dict(review=artifact, observation=observation, model_frame_receipt=receipt)
                issues = artifact['issues']
            else:
                result, issues = artifact, []
        except ServiceError as error:
            if error.code == 'service_unavailable':
                # Artifacts/control evidence can be read again safely; this is
                # recovery of one attempt, never another model submission.
                return _result(lineage_id, operation, 'result_evidence_pending', last_error=error.code)
            failure = dict(error=error.code, job_id=operation['job_id'])
            if artifact is not None:
                failure['candidate_result'] = artifact
            operation = self.store.finish(operation_id, 'failed', result=failure,
                issues=artifact.get('issues', []) if review and artifact is not None else [])
            return _result(lineage_id, operation)
        operation = self.store.finish(operation_id, result=result, issues=issues)
        return _result(lineage_id, operation, 'stage_completed')

    @staticmethod
    def _checked_verification(value, operation_id, source):
        _require(type(value) is dict and set(value) == {
            'schema', 'operation_id', 'source_sha256', 'technical_passed', 'media_versions', 'checks'},
            'animation_polish_renderer_receipt_invalid', 502)
        _require(value['schema'] == 'video.animation-render-verification.v1'
                 and value['operation_id'] == operation_id
                 and value['source_sha256'] == creation.source_sha256(source)
                 and type(value['technical_passed']) is bool,
                 'animation_polish_renderer_binding_mismatch', 502)
        checks, versions = value['checks'], value['media_versions']
        _require(type(checks) is list and 1 <= len(checks) <= 128
                 and all(type(row) is dict and set(row) == {'code', 'status'}
                         and type(row['code']) is str and 1 <= len(row['code']) <= 100
                         and type(row['status']) is str and row['status'] in ('passed', 'failed', 'not_run')
                         for row in checks), 'animation_polish_renderer_checks_invalid', 502)
        _require(value['technical_passed'] == all(row['status'] == 'passed' for row in checks)
                 and type(versions) is list and (not value['technical_passed'] or bool(versions)),
                 'animation_polish_renderer_checks_invalid', 502)
        # The ledger validates each version's ID, SHA and bounded JSON payload.
        return deepcopy(value)

    def _verification_step(self, operation, source, creation_input):
        lineage_id, operation_id = operation['lineage_id'], operation['id']
        first = False
        if operation['status'] == 'reserved':
            claimed = self.store.mark_submitting(operation_id)
            first = claimed['submission_claimed']
            if not first:
                operation = self.store.operation(operation_id)
            else:
                operation = claimed
        if operation['status'] in ('submitting', 'submission_unknown'):
            # A deterministic local renderer identity is recorded before I/O.
            operation = self.store.mark_submitted(operation_id, operation_id)
        if operation['status'] not in ('submitted', 'running'):
            return _result(lineage_id, operation)
        try:
            value = (self.renderer.render(operation_id, source, creation_input) if first
                     else self.renderer.lookup(operation_id))
        except ServiceError as error:
            return _result(lineage_id, operation, 'verification_unknown', last_error=error.code)
        if value is None:
            return _result(lineage_id, operation, 'verification_waiting')
        try:
            value = self._checked_verification(value, operation_id, source)
        except ServiceError as error:
            operation = self.store.finish(operation_id, 'failed', result=dict(error=error.code), issues=[])
            return _result(lineage_id, operation)
        operation = self.store.finish(operation_id,
            'completed' if value['technical_passed'] else 'failed',
            result=value, issues=[], media_versions=value['media_versions'])
        return _result(lineage_id, operation,
            'completed' if value['technical_passed'] else 'failed',
            media_versions=value['media_versions'], second_agent_review_performed=False)

    def step(self, lineage_id, *, review_directory, creation_input, base_source, deadline_at):
        """Advance one stage; call again explicitly to poll/reconcile/continue."""
        lineage = self.store.lineage(lineage_id)
        profile, review_document, files = load_bundle(review_directory)
        _require(profile in {visual.REVIEW_PROFILE, visual.REVIEW_SEQUENCE_PROFILE, visual.REVIEW_BATCH_PROFILE},
                 'animation_polish_review_bundle_required')
        _require(getattr(self.review_gateway, 'profile', profile) == profile,
                 'animation_polish_review_profile_mismatch', 409)
        creation_input = creation.validate_creation_input(creation_input)
        _require(creation_input['base_source'] is None and creation_input['review'] is None,
                 'animation_polish_initial_creation_required')
        _require(type(base_source) is dict and set(base_source) == {
            'source_revision', 'source_sha256', 'engine', 'files'})
        _require(creation.source_sha256(base_source) == base_source['source_sha256']
                 == lineage['source_sha256'] == review_document['source_sha256']
                 and base_source['source_revision'] == lineage['source_version_id'],
                 'animation_polish_original_source_changed', 409)
        # This immutable reservation also freezes the confirmed creation context
        # and original media, preventing a different step caller from swapping
        # narration/constraints or treating revised output as a fresh allowance.
        media_versions = [dict(version_id=base_source['source_revision'],
            sha256=review_document['media']['sha256'], source_sha256=base_source['source_sha256'],
            creation_input_sha256=digest(creation_input))]
        review_id = lineage_id + ':review'
        review_revision = getattr(self.review_gateway, 'profile_revision', '1')
        versions = self.store.review_media_versions(review_id, media_versions, profile, review_revision)
        review = self.store.reserve(lineage_id, 'review', review_id, review_id,
                                    digest(review_document), versions)
        if review['status'] != 'completed':
            return self._remote_step(review, self.review_gateway, review_document, files,
                                     deadline_at, review=True)
        review_result = visual.validate_review_result(review['result']['review'],
            digest(review_document), review_document)
        # Recheck the retained control receipt, rather than trusting a stored
        # model assertion or a boolean derived from supplied frame names.
        observed = validate_model_frame_receipt(review_document, review['result']['model_frame_receipt'])
        _require(observed == review['result']['observation'], 'animation_polish_observation_changed', 409)
        source = deepcopy(base_source)
        if review_result['issues']:
            revised_input = dict(creation_input, base_source=deepcopy(base_source), review=review_result)
            revised_input = creation.validate_creation_input(revised_input)
            revision_id = lineage_id + ':revision'
            versions = self.store.review_media_versions(review_id, media_versions, profile,
                review_revision, operation_id=revision_id)
            revision = self.store.reserve(lineage_id, 'revision', revision_id, revision_id,
                                          digest(revised_input), versions)
            if revision['status'] != 'completed':
                return self._remote_step(revision, self.creation_gateway, revised_input, None, deadline_at)
            source = creation.validate_creation_result(revision['result'], digest(revised_input), revised_input)
        verification_id = lineage_id + ':verification'
        versions = self.store.review_media_versions(review_id, media_versions, profile,
            review_revision, operation_id=verification_id)
        verification_input = dict(source_sha256=creation.source_sha256(source),
                                  creation_input_sha256=digest(creation_input))
        verification = self.store.reserve(lineage_id, 'verification', verification_id, verification_id,
                                          digest(verification_input), versions)
        if verification['status'] == 'completed':
            return _result(lineage_id, verification, 'completed',
                media_versions=verification['output_media_versions'], second_agent_review_performed=False)
        return self._verification_step(verification, source, creation_input)
