"""Transaction-scoped, fenced adapter for the PPT execution queue."""
from dataclasses import dataclass
from .execution import Attempt, ControlError

_REPOSITORY = None
_MODELS = None
PUBLIC_ERRORS = frozenset(('parse_failed', 'model_unavailable', 'execution_timeout',
    'quality_gate_failed', 'render_failed', 'export_failed', 'execution_interrupted', 'storage_full'))


def public_error(code):
    return code if type(code) is str and code in PUBLIC_ERRORS else 'execution_interrupted'


def _repository():
    global _REPOSITORY
    if _REPOSITORY is None:
        from ppt_service.server.repository import Repository
        _REPOSITORY = Repository
    return _REPOSITORY


def _models():
    global _MODELS
    if _MODELS is None:
        from ppt_service.server.models import Job, ExecutionSlot
        _MODELS = Job, ExecutionSlot
    return _MODELS


@dataclass(frozen=True)
class Claim:
    job_id: str
    kind: str
    attempt: int
    token: int


class RepositoryQueue:
    def __init__(self, sessions, *, lease_seconds=90):
        if type(lease_seconds) is not int or not 60 <= lease_seconds <= 600:
            raise ControlError('invalid_lease')
        self.sessions = sessions
        self.lease_seconds = lease_seconds

    def claim_next(self):
        with self.sessions.begin() as s:
            j = _repository()(s).claim_next(self.lease_seconds)
            return None if j is None else Claim(j.id, j.kind, j.attempt, j.fencing_token)

    def build(self, claim):
        from .attempt_builder import build_attempt
        from .pi_dispatch import ClaimedExecution
        with self.sessions.begin() as s:
            j, _, _ = _repository()(s).fenced(claim.job_id, claim.attempt, claim.token)
            if j.kind != claim.kind:
                raise ControlError('stale_claim')
            a, manifest = build_attempt(s, j, engine='pi')
            return ClaimedExecution(a, manifest)

    def attach_execution(self, a):
        if type(a) is not Attempt:
            raise ControlError('invalid_attempt_request')
        with self.sessions.begin() as s:
            _repository()(s).attach_execution(a.job_id, a.attempt, a.token,
                job_uid=a.uid, session_id=a.session_id)

    def heartbeat(self, a):
        with self.sessions.begin() as s:
            j = _repository()(s).heartbeat(a.job_id, a.attempt, a.token, self.lease_seconds)
            return j.status

    def prove_stopped(self, a, ok):
        if type(a) is not Attempt or ok is not True:
            raise ControlError('stop_proof_required')
        with self.sessions.begin() as s:
            j = _repository()(s).prove_stopped(a.job_id, a.attempt, a.token,
                job_uid=a.uid, all_writers_stopped=True)
            return j.status

    def finish_failed(self, a, code):
        if type(a) is not Attempt:
            raise ControlError('invalid_failure_record')
        with self.sessions.begin() as s:
            _repository()(s).finish_failed(a.job_id, a.attempt, a.token,
                error_code=public_error(code))

    def abandon_claim(self, claim, code):
        """Terminalize an exact, unlaunched claim; never requeue permanent bad input.

        No process was attached. This is a prelaunch disposition, not a writer
        stop observation. Fences/status/attachment are all checked under locks.
        """
        if type(claim) is not Claim or type(code) is not str or not code:
            raise ControlError('invalid_claim')
        job_model, slot_model = _models()
        with self.sessions.begin() as s:
            slot = s.get(slot_model, 1, with_for_update=True)
            j = s.get(job_model, claim.job_id, with_for_update=True)
            if (slot is None or j is None or slot.job_id != claim.job_id
                    or slot.fencing_token != claim.token or j.fencing_token != claim.token
                    or j.attempt != claim.attempt or j.kind != claim.kind
                    or j.status != 'starting' or j.job_uid or j.run_id):
                raise ControlError('stale_claim')
            from ppt_service.server.models import Project
            p = s.get(Project, j.project_id, with_for_update=True)
            if p is None or p.active_job_id != j.id:
                raise ControlError('stale_claim')
            j.status = 'failed'
            j.error_code = public_error(code)
            j.lease_until = None
            # stopped remains false: no OS stop evidence is invented here.
            p.active_job_id = None
            p.stage = 'ready' if p.latest_version_id else 'draft'
            p.cleanup_ready = p.deletion_requested
            slot.job_id = None
            _repository()(s).event(j, 'job.failed', {'error_code': j.error_code})
