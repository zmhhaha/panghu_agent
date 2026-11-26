"""Manual release intent, one Agent compilation, atomic whole-library switch.

GET helpers never advance work. An ambiguous admission is reconciled only by
its original operation key, including after process death or a 404 lookup.
"""
from __future__ import annotations

import time

from agent_service.animation_wikiskill_contract import make_snapshot, build_input, input_files, require
from agent_service.contracts import ServiceError
from .animation_wikiskill_store import (TERMINAL, collection_reserve_seconds,
    stored_compiler_revision, validate_start)

_REMOTE_TERMINAL = {'failed', 'cancelled', 'timed_out', 'quarantined', 'needs_input'}


class WikiSkillCoordinator:
    def __init__(self, store, gateway, capture, *, clock=time.time):
        self.store, self.gateway, self.capture, self.clock = store, gateway, capture, clock

    def start(self, owner, body):
        request = validate_start(body)
        try:
            prior = self.store.recover(owner, request['request_id'])
        except ServiceError as error:
            if error.code != 'animation_wikiskill_request_not_found':
                raise
        else:
            from agent_service.contracts import digest
            if prior['request_sha256'] != digest(request):
                raise ServiceError('animation_wikiskill_request_conflict', 409)
            return prior
        revision = '3'
        snapshot = make_snapshot(owner, self.capture(owner), captured_at=self.clock(),
                                 compilation_revision=revision)
        document = build_input(snapshot, note=request['note'], compilation_revision=revision)
        return self.store.create(owner, request, snapshot, document, compilation_revision=revision)

    def _gateway_for_revision(self, revision):
        factory = getattr(self.gateway, 'for_revision', None)
        if callable(factory):
            return factory(revision)
        require(getattr(self.gateway, 'profile_revision', revision) == revision,
                'animation_wikiskill_compiler_revision_unavailable', 503)
        return self.gateway

    def get(self, owner, job_id):
        return self.store.get(owner, job_id)

    def recover(self, owner, request_id):
        return self.store.recover(owner, request_id)

    def catalogue(self, owner):
        return self.store.catalogue(owner)

    def pending(self, *, limit=20):
        return self.store.pending(limit=limit)

    def active(self, owner):
        return self.store.active(owner)

    def get_release(self, owner, release_id, *, include_archived=False):
        return self.store.get_release(owner, release_id, include_archived=include_archived)

    def activate(self, owner, body):
        return self.store.activate(owner, body)

    def recover_activation(self, owner, request_id):
        return self.store.recover_activation(owner, request_id)

    def advance(self, owner, job_id):
        token = self.store.acquire(owner, job_id)
        if token is None:
            return self.store.get(owner, job_id)
        try:
            operation = self.store.operation(owner, job_id, token)
            revision = stored_compiler_revision(operation)
            gateway = self._gateway_for_revision(revision)
            state = operation['state']
            document = operation['input_document']
            reserve = collection_reserve_seconds(operation)
            submission_deadline = operation['deadline_at'] - (reserve or 0)
            if state in {'submitting', 'submission_unknown'}:
                try:
                    remote = gateway.lookup(operation['operation_id'])
                except ServiceError as error:
                    self.store.change(owner, job_id, token, state='submission_unknown',
                        error=dict(code='animation_wikiskill_submission_unknown', lookup_code=error.code))
                    return self.store.get(owner, job_id)
                if not remote:
                    self.store.change(owner, job_id, token, state='submission_unknown',
                        error=dict(code='animation_wikiskill_submission_unknown'))
                    return self.store.get(owner, job_id)
                self.store.change(owner, job_id, token, state='running', job_id=remote['job_id'], error=None,
                    remote_receipt=remote, remote_state=remote['state'],
                    remote_stop_proven=gateway.stopped(remote))
                return self.store.get(owner, job_id)
            if state in {'queued', 'uploading'}:
                if submission_deadline <= self.clock():
                    self.store.change(owner, job_id, token, state='failed',
                        error=dict(code='animation_wikiskill_deadline_expired_before_submission'))
                    return self.store.get(owner, job_id)
                gateway.preflight(submission_deadline)
                self.store.change(owner, job_id, token, state='uploading', error=None)
                snapshot = self.store.snapshot(owner, operation['snapshot_id'], compilation_revision=revision)
                refs = gateway.upload(document, input_files(snapshot, document, compilation_revision=revision))
                self.store.change(owner, job_id, token, state='prepared', input_refs=refs, error=None)
                return self.store.get(owner, job_id)
            if state == 'prepared':
                if submission_deadline <= self.clock():
                    self.store.change(owner, job_id, token, state='failed',
                        error=dict(code='animation_wikiskill_deadline_expired_before_submission'))
                    return self.store.get(owner, job_id)
                timing = {} if reserve is None else dict(collection_reserve_seconds=reserve)
                admit = gateway.prepare_submission(operation['operation_id'], document,
                    operation['deadline_at'], operation['input_refs'], **timing)
                self.store.change(owner, job_id, token, state='submitting', submission_attempted=True, error=None)
                try:
                    remote = admit()
                except Exception as error:
                    self.store.change(owner, job_id, token, state='submission_unknown',
                        error=dict(code='animation_wikiskill_submission_unknown',
                            submission_code=error.code if isinstance(error, ServiceError) else 'transport_error'))
                    return self.store.get(owner, job_id)
                self.store.change(owner, job_id, token, state='running', job_id=remote['job_id'],
                    remote_receipt=remote, remote_state=remote['state'],
                    remote_stop_proven=gateway.stopped(remote), error=None)
                return self.store.get(owner, job_id)
            if state == 'running':
                remote = gateway.poll(operation['job_id'])
                stopped = gateway.stopped(remote)
                self.store.change(owner, job_id, token, remote_receipt=remote,
                    remote_state=remote['state'], remote_stop_proven=stopped)
                if remote['state'] == 'completed' and stopped:
                    if reserve is not None and self.clock() >= operation['deadline_at']:
                        raise ServiceError('animation_wikiskill_publication_deadline_expired', 409)
                    result = gateway.collect(operation['job_id'], operation['input_sha256'], document)
                    self.store.publish(owner, job_id, token, result)
                elif remote['state'] in _REMOTE_TERMINAL and stopped:
                    self.store.change(owner, job_id, token, state='failed',
                        error=dict(code='animation_wikiskill_agent_' + remote['state']))
                elif remote['state'] == 'completed' or remote['state'] in _REMOTE_TERMINAL:
                    self.store.change(owner, job_id, token,
                        error=dict(code='animation_wikiskill_writer_stop_unproven'))
                return self.store.get(owner, job_id)
            return self.store.get(owner, job_id)
        except ServiceError as error:
            if error.code == 'animation_wikiskill_lease_lost':
                return self.store.get(owner, job_id)
            current = self.store.operation(owner, job_id, token)
            if current['state'] not in TERMINAL:
                # Availability can be retried without new admission. A stopped
                # invalid result fails this release and keeps the old active.
                updates = dict(error=dict(code=error.code))
                if (not current['submission_attempted'] and error.status < 500
                        or current['state'] == 'running' and current['remote_stop_proven']
                            and error.code != 'service_unavailable'):
                    updates['state'] = 'failed'
                elif current['state'] == 'submitting':
                    updates['state'] = 'submission_unknown'
                self.store.change(owner, job_id, token, **updates)
            return self.store.get(owner, job_id)
        except Exception:
            current = self.store.operation(owner, job_id, token)
            if current['state'] not in TERMINAL:
                updates = dict(error=dict(code='animation_wikiskill_coordinator_unavailable'))
                if current['state'] in {'submitting', 'submission_unknown'}:
                    updates['state'] = 'submission_unknown'
                self.store.change(owner, job_id, token, **updates)
            return self.store.get(owner, job_id)
        finally:
            self.store.release_lease(owner, job_id, token)
