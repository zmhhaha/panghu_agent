"""Advance injected producers without introducing another execution service.

Adapter protocol: capabilities(), submit(task), lookup(task), cancel(task), and
validate_output(task, output) -> True. Terminal receipts require actual producer
stop evidence. None from lookup means unknown, never permission to resubmit.
"""
from copy import deepcopy

from agent_service.contracts import ServiceError
from .resource_plan import ACTIVE, require, validate_output, verify_producer


class ResourcePlanWorkflow:
    def __init__(self, store, producers, *, claim_grace_seconds=30):
        require(type(producers) is dict and type(claim_grace_seconds) in (int, float)
            and 0 <= claim_grace_seconds <= 300, 'resource_plan_workflow_invalid')
        self.store, self.producers = store, dict(producers)
        self.claim_grace_seconds = claim_grace_seconds

    def _adapter(self, definition):
        adapter = self.producers.get(definition['producer'])
        require(adapter is not None and all(callable(getattr(adapter, name, None))
            for name in ('capabilities', 'submit', 'lookup', 'cancel', 'validate_output')),
            'resource_plan_producer_unavailable', 503)
        return adapter

    def _accept(self, owner, operation_id, node_id, task, receipt, adapter):
        if receipt is None:
            return self.store.unknown(owner, operation_id, node_id, task['task_id'])
        validated = False
        if isinstance(receipt, dict) and receipt.get('status') == 'completed':
            # Never let a foreign receipt reach a media validator before its
            # exact task and request identity have been established.
            require(receipt.get('task_id') == task['task_id']
                and receipt.get('request_sha256') == task['request_sha256'],
                'resource_plan_receipt_binding_mismatch', 502)
            require(receipt.get('stop_proven') is True, 'resource_plan_stop_unproven', 502)
            try:
                require(adapter.validate_output(deepcopy(task), deepcopy(receipt.get('output'))) is True,
                    'resource_plan_output_unverified', 502)
                definition = self.store.get(owner, operation_id)['nodes'][node_id]['definition']
                validate_output(receipt.get('output'), definition)
                validated = True
            except Exception as error:
                # The real producer has stopped. A rejected artifact is a
                # failed attempt, not an outstanding paid process or success.
                rejected = dict(task_id=task['task_id'], request_sha256=task['request_sha256'],
                    status='failed', stop_proven=True, error=getattr(error, 'code', 'resource_plan_output_rejected'))
                return self.store.record(owner, operation_id, node_id, task['task_id'], rejected)
        try:
            return self.store.record(owner, operation_id, node_id, task['task_id'], receipt, validated=validated)
        except ServiceError as error:
            if not validated or error.code != 'resource_plan_too_large':
                raise
            return self.store.record(owner, operation_id, node_id, task['task_id'], dict(
                task_id=task['task_id'], request_sha256=task['request_sha256'], status='failed',
                stop_proven=True, error=error.code))

    def advance(self, owner, operation_id, *, admit=True):
        """One bounded pass: poll originals, then claim each ready node once."""
        initial = self.store.get(owner, operation_id)
        for node_id in initial['nodes']:
            plan = self.store.get(owner, operation_id)
            node = plan['nodes'][node_id]
            stopping = plan['cancel_requested'] or self.store.clock() >= plan['deadline_at']
            if node['status'] in ACTIVE and node['task']:
                task = node['task']
                # Another coordinator may still be inside the initial submit.
                # After a restart the grace ends, then only lookup is allowed.
                if (node['status'] == 'submitting' and not stopping
                        and self.store.clock() - node['attempts'][-1]['created_at'] < self.claim_grace_seconds):
                    continue
                try:
                    adapter = self._adapter(node['definition'])
                    receipt = adapter.cancel(deepcopy(task)) if stopping else adapter.lookup(deepcopy(task))
                    self._accept(owner, operation_id, node_id, task, receipt, adapter)
                except Exception as error:
                    self.store.unknown(owner, operation_id, node_id, task['task_id'],
                        getattr(error, 'code', 'resource_plan_producer_unreachable'))
                continue
            if node['status'] != 'queued' or stopping or not admit:
                continue
            dependencies = [plan['nodes'][key] for key in node['definition']['dependencies']]
            if any(value['status'] in {'failed', 'blocked', 'cancelled'} for value in dependencies):
                self.store.block(owner, operation_id, node_id, 'resource_plan_dependency_unavailable')
                continue
            if any(value['status'] != 'completed' for value in dependencies):
                continue
            try:
                adapter = self._adapter(node['definition'])
                descriptor = verify_producer(adapter.capabilities(), node['definition'])
                task = self.store.claim(owner, operation_id, node_id, descriptor)
            except Exception as error:
                self.store.block(owner, operation_id, node_id,
                    getattr(error, 'code', 'resource_plan_producer_unavailable'))
                continue
            if task is None:
                continue
            try:
                # Cancellation is checked again after the persisted claim.
                # An original reservation may require an idempotent tombstone.
                if not self.store.submission_allowed(owner, operation_id, task['task_id']):
                    receipt = adapter.cancel(deepcopy(task))
                else:
                    receipt = adapter.submit(deepcopy(task))
                self._accept(owner, operation_id, node_id, task, receipt, adapter)
            except Exception as error:
                self.store.unknown(owner, operation_id, node_id, task['task_id'],
                    getattr(error, 'code', 'resource_plan_submission_unknown'))
        return self.store.get(owner, operation_id)
