"""One durable resource-planner child before the existing overall confirmation.

The old joint-plan-v1 result remains untouched. This coordinator attaches a
separate verified candidate sidecar and never produces media or publishes Skill.
"""
from copy import deepcopy
import json
import time

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_resource_plan import PROFILE
from agent_service.profiles.video_resource_plan_contract import INPUT_SCHEMA, validate_input, validate_result, require
from .joint_plan_store import ACTIVE, TERMINAL, _json, _size
from .agent import _uuid, _sha, _finite_number


def stopped(job):
    attempt, proof, pins = (job.get(key) for key in ('attempt', 'stop_proof', 'pins'))
    return (job.get('stop_proven') is True and type(attempt) is dict and _uuid(attempt.get('attempt_id'))
        and type(attempt.get('fence')) is int and attempt['fence'] >= 1
        and type(pins) is dict and pins.get('profile') == PROFILE and pins.get('profile_revision') == '1'
        and type(proof) is dict and proof.get('stop_proven') is True
        and proof.get('attempt_id') == attempt['attempt_id'] and type(proof.get('fence')) is int
        and proof['fence'] == attempt['fence']
        and _sha(proof.get('request_sha256')) and _sha(proof.get('result_sha256'))
        and type(proof.get('resource_ids')) is list and 1 <= len(proof['resource_ids']) <= 64
        and all(type(value) is str and 1 <= len(value) <= 200
            and not any(ord(char) < 32 or ord(char) == 127 for char in value) for value in proof['resource_ids'])
        and len(set(proof['resource_ids'])) == len(proof['resource_ids'])
        and _finite_number(proof.get('observed_at')) and proof['observed_at'] > 0)


class ResourcePlanningCoordinator:
    def __init__(self, joint_store, gateway, *, producer_catalog):
        require(gateway is not None and callable(producer_catalog), 'resource_planning_configuration_invalid')
        self.store, self.gateway, self.producer_catalog = joint_store, gateway, producer_catalog

    def _update(self, operation_id, changes, *, terminal=None, result=None):
        with self.store.store.transaction() as db:
            op = self.store._op(db, operation_id)
            stage = op['resource_planning']
            if op['status'] not in ACTIVE:
                return deepcopy(op)
            before_op = _size(op)
            version = self.store._version(db, op['owner'], op['version_id'])
            before_version = _size(version)
            if 'input_ref' in changes and stage.get('input_ref') is not None:
                changes = {key: value for key, value in changes.items() if key != 'input_ref'}
            if result is not None and (op['cancel_requested'] or time.time() >= op['deadline_at']):
                result = None
                terminal = 'cancelled' if op['cancel_requested'] else 'timed_out'
            stage.update(deepcopy(changes))
            if terminal is not None:
                require(terminal in TERMINAL and stage.get('stop_proven') is True,
                    'resource_planning_stop_unproven', 502)
                op.update(status=terminal, error=stage.get('error'))
                stage['status'] = terminal
            annotation = {key: deepcopy(stage[key]) for key in (
                'operation_id', 'status', 'input_sha256', 'joint_input_sha256', 'joint_result_sha256',
                'producer_catalog_sha256', 'result_sha256', 'job_id', 'stop_proven', 'error') if key in stage}
            annotation.update(profile=PROFILE, origin='agent_resource_planner')
            if result is not None:
                require(terminal == 'completed' and not op['cancel_requested'], 'resource_planning_late_result', 409)
                result = validate_result(result, stage['input_sha256'], stage['document'])
                version['resource_plan'] = deepcopy(result['resource_plan'])
                annotation['raw_result'] = deepcopy(result)
                annotation['result_sha256'] = digest(result)
                stage['result_sha256'] = digest(result)
            version['resource_planning'] = annotation
            self.store._capacity(db, op['project_id'], extra_bytes=_size(op) - before_op + _size(version) - before_version,
                active_delta=-1 if terminal else 0)
            self.store._write_op(db, op)
            db.execute('UPDATE joint_plan_versions SET document=? WHERE id=? AND owner=?',
                (_json(version), version['id'], op['owner']))
            return deepcopy(op)

    def _initialize(self, operation_id):
        op = self.store.operation(operation_id)
        if op['resource_planning'].get('document') is not None:
            return op
        version = self.store.version(op['owner'], op['version_id'])
        producers = self.producer_catalog(op['owner'])
        document = dict(schema_version=INPUT_SCHEMA, joint_input=op['document'], joint_input_sha256=op['input_sha256'],
            joint_result=version['result'], joint_result_sha256=digest(version['result']),
            producer_catalog=producers, producer_catalog_sha256=digest(producers))
        document = validate_input(document)
        with self.store.store.transaction() as db:
            current = self.store._op(db, operation_id)
            stage = current['resource_planning']
            if current['status'] not in ACTIVE or stage.get('document') is not None:
                return current
            before = _size(current)
            stage.update(document=document, input_sha256=digest(document),
                joint_input_sha256=document['joint_input_sha256'], joint_result_sha256=document['joint_result_sha256'],
                producer_catalog_sha256=document['producer_catalog_sha256'])
            self.store._capacity(db, current['project_id'], extra_bytes=_size(current) - before)
            self.store._write_op(db, current)
            return current

    def _claim(self, operation_id):
        with self.store.store.transaction() as db:
            op = self.store._op(db, operation_id)
            stage = op['resource_planning']
            if (op['status'] not in ACTIVE or stage['submit_attempted']
                    or op['cancel_requested'] or time.time() >= op['deadline_at']):
                return None
            stage.update(submit_attempted=True, status='submitting', stop_proven=False)
            self.store._write_op(db, op)
            return op

    def advance(self, operation_id, *, enabled=True):
        op = self.store.operation(operation_id)
        if op['status'] not in ACTIVE:
            return
        stage = op['resource_planning']
        stopping = op['cancel_requested'] or time.time() >= op['deadline_at']
        if not stage['submit_attempted'] and (stopping or not enabled):
            return self._update(operation_id, dict(stop_proven=True, error=None),
                terminal='cancelled' if op['cancel_requested'] or not enabled else 'timed_out')
        op = self._initialize(operation_id)
        stage = op['resource_planning']
        if not stage.get('job_id'):
            if not stage['submit_attempted']:
                if stage.get('input_ref') is None:
                    uploaded = self.gateway.upload(stage['document'])
                    op = self._update(operation_id, dict(input_ref=uploaded))
                    stage = op['resource_planning']
                op = self._claim(operation_id)
                if op is None:
                    return
                stage = op['resource_planning']
                try:
                    job = self.gateway.submit(stage['operation_id'], stage['document'], op['deadline_at'], input_ref=stage['input_ref'])
                except Exception:
                    return self._update(operation_id, dict(status='submission_unknown', error='resource_planning_submission_unknown'))
            else:
                try:
                    job = self.gateway.lookup(stage['operation_id'])
                except ServiceError as error:
                    return self._update(operation_id, dict(status='submission_unknown',
                        error='resource_planning_submission_unconfirmed' if error.status == 404 else error.code))
            op = self._update(operation_id, dict(job_id=job['job_id'], status='running', error=None))
            stage = op['resource_planning']
        job = self.gateway.poll(stage['job_id'])
        op = self.store.operation(operation_id)
        stage = op['resource_planning']
        stopping = op['cancel_requested'] or time.time() >= op['deadline_at']
        if job['state'] == 'completed':
            if not stopped(job):
                return self._update(operation_id, dict(error='resource_planning_stop_unproven'))
            if stopping:
                return self._update(operation_id, dict(stop_proven=True, error=None),
                    terminal='cancelled' if op['cancel_requested'] else 'timed_out')
            try:
                result = self.gateway.collect(stage['job_id'], stage['input_sha256'], stage['document'])
                result = validate_result(result, stage['input_sha256'], stage['document'])
            except ServiceError as error:
                if error.status >= 500 and error.code.startswith(('service_', 'http_')):
                    return self._update(operation_id, dict(error=error.code))
                return self._update(operation_id, dict(stop_proven=True, error=error.code), terminal='failed')
            return self._update(operation_id, dict(stop_proven=True, error=None), terminal='completed', result=result)
        if job['state'] in {'failed', 'needs_input', 'cancelled', 'timed_out', 'quarantined'}:
            if not stopped(job):
                return self._update(operation_id, dict(error='resource_planning_stop_unproven'))
            terminal = 'cancelled' if op['cancel_requested'] else 'timed_out' if stopping else 'failed'
            return self._update(operation_id, dict(stop_proven=True, error='resource_planning_agent_' + job['state']), terminal=terminal)
        if stopping:
            now = time.time()
            if stage['cancel_attempts'] < 4 and now >= stage['next_cancel_at']:
                delays = (2, 5, 15, 30)
                self._update(operation_id, dict(status='cancelling', cancel_attempts=stage['cancel_attempts'] + 1,
                    next_cancel_at=now + delays[stage['cancel_attempts']]))
                self.gateway.cancel(stage['job_id'])
        return None
