"""Trusted host context and conservative projection of immutable human records.

There is deliberately no public function accepting a programmatic-success flag.
P3's full exporter certificate and its core integration have not been delivered.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Protocol

from sqlalchemy.orm import Session
from pydantic import ValidationError

from ..auth import Identity
from .contracts import (
    AcceptanceSnapshot, PageDescriptor, PageView, PowerPointReviewCommand,
    ReviewCommand, ReviewRecord, SourceBinding, VisualReviewCommand,
)


class AcceptanceError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class ReviewContext:
    """Host-issued context; never deserialize this from an HTTP/worker payload.

    The host resolver must authenticate ownership, lock the current project and
    version for reads and writes, re-hash frozen source/evidence, and supply independent PNG
    descriptors. ``candidate`` permits observations, never publication. A legacy
    ``needs_input`` source cannot be made reviewable by an Actor's accepted flag.
    """

    owner: Identity
    binding: SourceBinding
    source_revision: int
    pages: tuple[PageDescriptor, ...]
    native_chart_count: int
    current_input_snapshot_sha256: str
    current_source_fingerprint_sha256: str
    origin_status: Literal["candidate", "completed", "published", "needs_input", "failed", "awaiting_confirmation"]
    phase: Literal["generate", "revise", "plan_initial", "plan_options"]

    def __post_init__(self):
        if type(self.owner) is not Identity or type(self.binding) is not SourceBinding:
            raise ValueError("A typed trusted identity and source binding are required")
        if not self.owner.issuer or not self.owner.sub or type(self.source_revision) is not int or self.source_revision < 0:
            raise ValueError("Incomplete trusted context")
        if self.binding.source_revision != self.source_revision:
            raise ValueError("The authoritative source revision must be bound")
        if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in (self.current_input_snapshot_sha256, self.current_source_fingerprint_sha256)):
            raise ValueError("Authoritative current fingerprints are required")
        if not isinstance(self.pages, tuple) or not 1 <= len(self.pages) <= 100:
            raise ValueError("A nonempty immutable page roster is required")
        if any(type(page) is not PageDescriptor for page in self.pages):
            raise ValueError("Typed page descriptors are required")
        if [page.number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("The page roster must be contiguous and ordered")
        if type(self.native_chart_count) is not int or self.native_chart_count < 0:
            raise ValueError("Invalid trusted chart count")

    @property
    def stale(self):
        return (
            self.binding.input_snapshot_sha256 != self.current_input_snapshot_sha256
            or self.binding.source_fingerprint_sha256 != self.current_source_fingerprint_sha256
        )

    @property
    def reviewable(self):
        return not self.stale and self.origin_status in ("candidate", "completed", "published") and self.phase in ("generate", "revise")


class ContextResolver(Protocol):
    def __call__(self, session: Session, owner: Identity, project_id: str, version_id: str, *, for_update: bool) -> ReviewContext:
        """Return an authoritative context or AcceptanceError('version_not_found',404).

        GET also uses for_update=True to obtain a consistent source/history view.
        All core/source locks must remain held until this session commits. Do not
        trust worker reports, path arguments, browser hashes or Actor booleans.
        """
        ...


def validate_command(context: ReviewContext, command: ReviewCommand):
    if command.binding != context.binding:
        raise AcceptanceError("source_binding_changed")
    if command.kind == "revoke":
        return
    if not context.reviewable:
        raise AcceptanceError("source_stale" if context.stale else "source_unreviewable")
    if isinstance(command, VisualReviewCommand):
        actual = [(row.number, row.preview_sha256) for row in command.pages]
        expected = [(row.number, row.preview_sha256) for row in context.pages]
        if actual != expected:
            raise AcceptanceError("preview_roster_changed")
    elif isinstance(command, PowerPointReviewCommand):
        if command.checks.native_chart_edited == "not_applicable" and context.native_chart_count:
            raise AcceptanceError("native_chart_edit_required", 422)
        if command.checks.native_chart_edited == "pass" and not context.native_chart_count:
            raise AcceptanceError("native_chart_not_present", 422)


def _visual(record: ReviewRecord | None):
    if record is None or record.stale or record.revoked:
        return "pending"
    verdicts = [page["verdict"] for page in record.payload["pages"]]
    if "needs_changes" in verdicts:
        return "needs_changes"
    return "passed" if all(value == "pass" for value in verdicts) else "pending"


def _powerpoint(record: ReviewRecord | None):
    if record is None or record.stale or record.revoked:
        return "pending"
    checks = record.payload["checks"]
    if "fail" in checks.values():
        return "needs_changes"
    return "passed" if all(value in ("pass", "not_applicable") for value in checks.values()) else "pending"


def _record_is_current(context: ReviewContext, record: ReviewRecord):
    if context.stale or record.binding != context.binding or not context.reviewable:
        return False
    # The host is required to advance source/evidence revisions when the roster
    # changes. Recheck visible immutable facts defensively so an adapter mistake
    # cannot silently reuse a page hash or a chartless N/A conclusion.
    if record.kind in ("visual_review", "powerpoint_review"):
        command_type = VisualReviewCommand if record.kind == "visual_review" else PowerPointReviewCommand
        try:
            command = command_type.model_validate({**record.payload, "kind": record.kind, "expected_revision": 0, "binding": record.binding})
            validate_command(context, command)
        except (ValidationError, AcceptanceError):
            return False
    return True


def project_snapshot(context: ReviewContext, revision: int, records: list[ReviewRecord]) -> AcceptanceSnapshot:
    # Revocation is a new record. Old payloads never get updated, and revoking the
    # latest conclusion does not resurrect an earlier passing conclusion.
    revoked = {r.payload["record_id"] for r in records if r.kind == "revoke"}
    history = [r.model_copy(update={"revoked": r.id in revoked, "stale": not _record_is_current(context, r)}) for r in records]
    latest = {r.kind: r for r in history if r.kind != "revoke"}
    visual = _visual(latest.get("visual_review"))
    powerpoint = _powerpoint(latest.get("powerpoint_review"))
    human = "needs_changes" if "needs_changes" in (visual, powerpoint) else "passed" if visual == powerpoint == "passed" else "pending"
    reasons = ["independent_postflight_integration_pending"]
    state = "pending"
    if context.stale:
        state = "stale"
        reasons.append("source_stale")
    elif not context.reviewable:
        state = "unreviewable"
        reasons.append("source_unreviewable")
    if visual != "passed":
        reasons.append("visual_review_" + visual)
    if powerpoint != "passed":
        reasons.append("powerpoint_review_" + powerpoint)
    return AcceptanceSnapshot(
        project_id=context.binding.project_id, version_id=context.binding.version_id,
        source_revision=context.source_revision, revision=revision, binding=context.binding,
        pages=[PageView(**page.model_dump(), preview_url=f"/api/v1/versions/{context.binding.version_id}/slides/{page.number}/preview") for page in context.pages],
        native_chart_count=context.native_chart_count, programmatic_status="pending",
        visual_status=visual, powerpoint_status=powerpoint, human_status=human,
        acceptance_status=state, pending_reasons=reasons, can_review=context.reviewable,
        can_revoke=any(not r.revoked and r.kind != "revoke" for r in history), records=history,
    )
