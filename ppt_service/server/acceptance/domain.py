"""Trusted evidence context and append-only review/publication projection.

Programmatic evidence is supplied only by the host's evidence resolver. Browser
commands can record human observations and request publication, never supply a
certificate, renderer result or programmatic-success flag.
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
    ProgrammaticView, PublicationView, RenderedPageView, ReviewCommand, ReviewRecord,
    SourceBinding, VisualReviewCommand,
)


class AcceptanceError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


REQUIRED_PROGRAMMATIC_GATES = frozenset((
    "independent_export_build_contract", "independent_postflight_and_delivery",
    "independent_final_pptx_rendering",
))


@dataclass(frozen=True)
class RenderedPage:
    """Private storage facts measured by the trusted receipt reader."""

    number: int
    preview_sha256: str
    bytes: int
    storage_key: str

    def __post_init__(self):
        if type(self.number) is not int or not 1 <= self.number <= 100 or not re.fullmatch(r"[a-f0-9]{64}", self.preview_sha256):
            raise ValueError("Invalid final preview descriptor")
        if type(self.bytes) is not int or not 0 < self.bytes <= 8 * 1024**2:
            raise ValueError("Invalid final preview size")
        if not isinstance(self.storage_key, str) or any(part in ("", ".", "..") or "\\" in part for part in self.storage_key.split("/")):
            raise ValueError("Invalid final preview storage key")


@dataclass(frozen=True)
class ProgrammaticContext:
    """Host-only result after rechecking an independently retained certificate.

    The resolver must verify the certificate's source/nonce/execution bindings,
    hashes and exact rendered-page roster. Never construct this from public JSON.
    Serialized worker flags or the original partial validator report are not a
    replacement for this reader. The required gate roster is checked again here.
    """

    binding: SourceBinding
    receipt_sha256: str
    completed_gates: tuple[str, ...]
    pending_gates: tuple[str, ...]
    renderer: str
    status: Literal["pending", "passed", "needs_changes"]
    detail: str = ""
    rendered_pages: tuple[RenderedPage, ...] = ()

    def __post_init__(self):
        if type(self.binding) is not SourceBinding or not re.fullmatch(r"[a-f0-9]{64}", self.receipt_sha256):
            raise ValueError("Bound programmatic evidence is required")
        if self.status not in ("pending", "passed", "needs_changes"):
            raise ValueError("Invalid programmatic state")
        if not isinstance(self.renderer, str) or len(self.renderer) > 400 or not isinstance(self.detail, str) or len(self.detail) > 2000:
            raise ValueError("Invalid programmatic description")
        for gates in (self.completed_gates, self.pending_gates):
            if not isinstance(gates, tuple) or len(gates) != len(set(gates)) or any(
                    not isinstance(gate, str) or not re.fullmatch(r"[a-z0-9_]{1,100}", gate) for gate in gates):
                raise ValueError("Invalid programmatic gate roster")
        if set(self.completed_gates).intersection(self.pending_gates):
            raise ValueError("Contradictory programmatic gates")
        if not isinstance(self.rendered_pages, tuple) or any(type(page) is not RenderedPage for page in self.rendered_pages):
            raise ValueError("Typed final preview roster is required")
        if self.rendered_pages and [page.number for page in self.rendered_pages] != list(range(1, len(self.rendered_pages) + 1)):
            raise ValueError("Ordered final preview roster is required")
        if self.status == "passed" and (self.pending_gates or not self.renderer or not self.rendered_pages
                or not REQUIRED_PROGRAMMATIC_GATES.issubset(self.completed_gates)):
            raise ValueError("Incomplete evidence cannot pass programmatic acceptance")


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
    programmatic: ProgrammaticContext | None = None
    publication_blocked_reason: str | None = None

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
        if self.programmatic is not None and type(self.programmatic) is not ProgrammaticContext:
            raise ValueError("Typed trusted programmatic evidence is required")
        if self.programmatic is not None and self.programmatic.binding == self.binding and self.programmatic.status == "passed" and len(self.programmatic.rendered_pages) != len(self.pages):
            raise ValueError("Complete final preview roster is required")

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
    latest = {r.kind: r for r in history if r.kind in ("visual_review", "powerpoint_review")}
    visual = _visual(latest.get("visual_review"))
    powerpoint = _powerpoint(latest.get("powerpoint_review"))
    human = "needs_changes" if "needs_changes" in (visual, powerpoint) else "passed" if visual == powerpoint == "passed" else "pending"
    trusted = context.programmatic
    if trusted is not None and trusted.binding == context.binding:
        programmatic = ProgrammaticView(status=trusted.status, receipt_sha256=trusted.receipt_sha256,
            completed_gates=list(trusted.completed_gates), pending_gates=list(trusted.pending_gates),
            renderer=trusted.renderer or None, detail=trusted.detail,
            rendered_pages=[RenderedPageView(number=page.number, preview_sha256=page.preview_sha256,
                preview_url=(f"/api/v1/projects/{context.binding.project_id}/versions/{context.binding.version_id}"
                             f"/acceptance/rendered-slides/{page.number}/preview?receipt_sha256={trusted.receipt_sha256}"))
                            for page in trusted.rendered_pages])
    else:
        programmatic = ProgrammaticView(status="pending", completed_gates=[],
            pending_gates=["independent_postflight_integration_pending"],
            detail="当前审核稿尚无完整的独立程序验收证据。")
    reasons = list(programmatic.pending_gates)
    if programmatic.status != "passed" and not reasons:
        reasons.append("programmatic_" + programmatic.status)
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
    if state == "pending" and not reasons and human == programmatic.status == "passed":
        state = "passed"
    if context.publication_blocked_reason:
        reasons.append(context.publication_blocked_reason)
    # Publication records bind the exact human conclusions and independent
    # receipt used by that explicit owner decision. A later correction,
    # revocation or evidence change never silently reuses the old authorization.
    publication = PublicationView(status="draft")
    publishes = [r for r in history if r.kind == "publish"]
    if publishes and state == "passed" and not reasons:
        record = publishes[-1]
        expected = {
            "visual_record_id": latest["visual_review"].id,
            "powerpoint_record_id": latest["powerpoint_review"].id,
            "programmatic_receipt_sha256": programmatic.receipt_sha256,
        }
        if not record.revoked and not record.stale and record.payload == expected:
            publication = PublicationView(status="published", record_id=record.id, published_at=record.created_at)
    authorized = publication.status == "published"
    return AcceptanceSnapshot(
        project_id=context.binding.project_id, version_id=context.binding.version_id,
        source_revision=context.source_revision, revision=revision, binding=context.binding,
        pages=[PageView(**page.model_dump(), preview_url=f"/api/v1/versions/{context.binding.version_id}/slides/{page.number}/preview") for page in context.pages],
        native_chart_count=context.native_chart_count, programmatic_status=programmatic.status,
        programmatic=programmatic,
        visual_status=visual, powerpoint_status=powerpoint, human_status=human,
        acceptance_status=state, pending_reasons=reasons, can_review=context.reviewable,
        can_revoke=any(not r.revoked and r.kind != "revoke" for r in history),
        can_publish=state == "passed" and not reasons and not authorized,
        publication_authorized=authorized, publication=publication, records=history,
    )


def publication_payload(context: ReviewContext, snapshot: AcceptanceSnapshot):
    """Issue only from the locked current projection, never a browser payload."""
    if snapshot.binding != context.binding or not context.reviewable or not snapshot.can_publish:
        raise AcceptanceError("publication_requirements_pending")
    active = {record.kind: record for record in snapshot.records
              if record.kind in ("visual_review", "powerpoint_review")}
    return {
        "visual_record_id": active["visual_review"].id,
        "powerpoint_record_id": active["powerpoint_review"].id,
        "programmatic_receipt_sha256": snapshot.programmatic.receipt_sha256,
    }
