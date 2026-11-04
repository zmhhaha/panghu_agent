"""Idle-only, unpaid acceptance checks for existing immutable review versions.

This coordinator does not enqueue an Agent task, update business rows, accept a
human check or publish. The isolated validator owns its stopped-attempt cache;
failed or interrupted model generation is never retried by this path.
"""
from __future__ import annotations

import copy
import re
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select

from ppt_service.server.acceptance.domain import AcceptanceError
from ppt_service.server.acceptance.resolver import ReviewContextResolver, fingerprint
from ppt_service.server.auth import Identity
from ppt_service.server.models import AgentOutbox, Asset, Checkpoint, ExecutionSlot, Job, Project, Version
from ppt_service.server.repository import ACTIVE
from ppt_service.server.storage import Storage, safe_key
from .execution import ControlError

IMAGE = re.compile(r"[^@\s]+@sha256:[a-f0-9]{64}\Z")


def require(value, code):
    if not value:
        raise ControlError(code)


class AcceptanceCoordinator:
    """Run at most one new independent validation when the controller is idle.

    The dispatcher calls ``tick`` only after Agent and material processing both
    return idle. The additional read-only checks here defend that integration
    boundary. A request arriving after the final idle check may wait for one
    bounded validation; its model execution is never started concurrently here.
    No database transaction remains open while ``validate_acceptance`` runs.
    """

    def __init__(self, sessions, config, validator, *, oracle_provider=None,
                 enabled=False, backend_mode="agent-service", max_candidates=8):
        require(type(enabled) is bool and type(max_candidates) is int and 1 <= max_candidates <= 8,
                "invalid_acceptance_configuration")
        self.sessions, self.config, self.validator = sessions, config, validator
        self.oracle_provider, self.enabled = oracle_provider, enabled
        self.cursor = None
        self.invalid = set()
        self.max_candidates = max_candidates
        self.execution_image = None
        self.resolver = None
        if enabled:
            settings = getattr(getattr(validator, "transport", None), "settings", None)
            image = getattr(settings, "worker_image", None)
            require(backend_mode == "agent-service" and isinstance(image, str) and IMAGE.fullmatch(image)
                    and callable(getattr(validator, "validate_acceptance", None)) and callable(oracle_provider),
                    "invalid_acceptance_configuration")
            self.execution_image = image
            self.resolver = ReviewContextResolver(Storage(config.data_root))

    @staticmethod
    def _idle(session):
        slot = session.get(ExecutionSlot, 1)
        if slot is None or slot.job_id is not None:
            return False
        if session.scalar(select(Job.id).where(Job.status.in_(ACTIVE)).limit(1)) is not None:
            return False
        if session.scalar(select(Project.id).where(Project.active_job_id != None).limit(1)) is not None:
            return False
        # Give queued uploads and parser stop/recovery priority over optional
        # acceptance work, including assets on another owner's project.
        if session.scalar(select(Asset.id).where(Asset.removed == False,
                Asset.parse_status.in_(("queued", "parsing", "stopping"))).limit(1)) is not None:
            return False
        return True

    def _cached(self, binding):
        target = self.config.data_root / "acceptance-attempts" / fingerprint(binding.model_dump(mode="json"))
        return target.exists() or target.is_symlink()

    def _evidence_exists(self, version):
        # An installed but malformed bundle needs administrator investigation,
        # never an automatic overwrite or another potentially expensive run.
        target = (self.config.data_root / "projects" / version.project_id / "versions" /
                  version.id / "acceptance-evidence")
        return target.exists() or target.is_symlink()

    def _select(self):
        with self.sessions.begin() as session:
            if not self._idle(session):
                return None, "busy"
            query = select(Version).join(Project, Project.id == Version.project_id).where(
                Version.status == "review", Project.deletion_requested == False,
                Project.active_job_id == None)
            if self.cursor is not None:
                timestamp, identifier = self.cursor
                query = query.where(or_(Version.created_at > timestamp,
                    and_(Version.created_at == timestamp, Version.id > identifier)))
            versions = session.scalars(query.order_by(Version.created_at, Version.id).limit(self.max_candidates)).all()
            if not versions:
                self.cursor = None
                return None, "idle"
            for version in versions:
                self.cursor = (version.created_at, version.id)
                try:
                    if any(str(UUID(value)) != value for value in (version.project_id, version.id)):
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                project = session.get(Project, version.project_id)
                if project is None:
                    continue
                key = (version.id, project.revision)
                if key in self.invalid or self._evidence_exists(version):
                    continue
                checkpoint = session.get(Checkpoint, version.checkpoint_id)
                if not checkpoint or checkpoint.manifest.get("checkpoint_format") != "ppt-agent-artifacts-v1":
                    continue
                owner = Identity(version.issuer, version.subject)
                try:
                    # The resolver must observe the row it actually locks,
                    # rather than the candidate query's identity-map snapshot.
                    session.expire(project)
                    context = self.resolver(session, owner, version.project_id, version.id, for_update=True)
                except AcceptanceError:
                    self.invalid.add(key)
                    if len(self.invalid) > 256:
                        self.invalid = {key}
                    continue
                if not context.reviewable or context.publication_blocked_reason or self._cached(context.binding):
                    continue
                out = session.get(AgentOutbox, version.job_id)
                job = session.get(Job, version.job_id)
                require(out is not None and job is not None and out.state == "mapped"
                    and job.kind in ("generate", "revise") and job.stopped is True,
                    "acceptance_source_unavailable")
                meta = copy.deepcopy(checkpoint.manifest)
                task = copy.deepcopy(out.request["task"])
                source_key = meta["agent_source"]["storage_key"]
                # safe_key excludes traversal and drive/absolute paths; resolver
                # already measured this exact no-link source tree and manifest.
                snapshot = self.config.data_root.joinpath(*safe_key(source_key))
                require(snapshot.absolute().is_relative_to(self.config.data_root.absolute()),
                        "acceptance_source_scope_invalid")
                return {"context": context, "task": task, "snapshot": snapshot,
                        "job_id": job.id, "attempt": job.attempt, "fencing_token": job.fencing_token,
                        "version_id": version.id}, "selected"
            return None, "idle"

    def tick(self):
        if not self.enabled:
            return {"status": "disabled"}
        candidate, state = self._select()
        if candidate is None:
            return {"status": "idle", "reason": state}
        context, task = candidate["context"], candidate["task"]
        # ``task`` is copied from this version's original immutable outbox, not
        # from the latest plan, source catalog, Actor output or deployment map.
        oracle = self.oracle_provider(task)
        require(isinstance(oracle, dict) and isinstance(oracle.get("expected_charts"), list)
            and isinstance(oracle.get("source_sha256", []), list), "trusted_chart_oracle_required")
        authorized = {row["sha256"] for row in task["input_snapshot"]["assets"]}
        require(set(oracle.get("source_sha256", [])) <= authorized
            and (not oracle["expected_charts"] or bool(oracle.get("source_sha256"))), "oracle_source_mismatch")
        original = {"execution_contract": "ppt-agent.v1", "task": task, "phase": task["phase"],
            "job_id": candidate["job_id"], "input_snapshot_sha256": context.binding.input_snapshot_sha256,
            "pages": task["input_snapshot"]["settings"].get("page_count", 8),
            "expected_charts": copy.deepcopy(oracle["expected_charts"]),
            "source_manifest_sha256": context.binding.manifest_sha256,
            "validation_nonce": context.binding.evidence_id}
        evidence_id = str(uuid4())
        request = {"schema": "ppt-service.acceptance-request.v1", "evidence_id": evidence_id,
            "validation_nonce": evidence_id, "binding": context.binding.model_dump(mode="json"),
            "job_id": candidate["job_id"], "attempt": candidate["attempt"],
            "fencing_token": candidate["fencing_token"], "execution_image": self.execution_image,
            "original_request_sha256": fingerprint(original)}
        # All metadata/source locks from _select are released. Recheck global
        # priority immediately before entering the bounded unpaid sandbox.
        with self.sessions.begin() as session:
            if not self._idle(session):
                return {"status": "idle", "reason": "busy"}
        if self._cached(context.binding):
            return {"status": "idle", "reason": "already_attempted"}
        result = self.validator.validate_acceptance(candidate["snapshot"], original, request)
        require(isinstance(result, dict) and result.get("status") in
                ("acceptance_validated", "acceptance_failed", "already_attempted")
                and result.get("binding_sha256") == fingerprint(request["binding"]),
                "acceptance_validator_result_invalid")
        return dict(result, version_id=candidate["version_id"])
