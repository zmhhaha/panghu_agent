"""Strict, engine-neutral HTTP contracts for human review (not publication)."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
Check = Literal["pending", "pass", "fail"]
HumanStatus = Literal["pending", "passed", "needs_changes"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class RuntimeBinding(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)
    engine: str = Field(min_length=1, max_length=80)
    runtime_revision: str = Field(min_length=1, max_length=200)
    profile: str = Field(min_length=1, max_length=100)
    checkpoint_format: str = Field(min_length=1, max_length=100)


class SourceBinding(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)
    project_id: Identifier
    version_id: Identifier
    source_revision: int = Field(ge=0)
    manifest_sha256: Digest
    input_snapshot_sha256: Digest
    pptx_sha256: Digest
    source_fingerprint_sha256: Digest
    evidence_id: Identifier
    evidence_sha256: Digest
    runtime: RuntimeBinding


class PageDescriptor(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)
    number: int = Field(ge=1, le=100)
    title: str = Field(max_length=200)
    preview_sha256: Digest
    source_sha256: Digest


class PageView(PageDescriptor):
    preview_url: str


class Issue(Strict):
    category: Literal["layout", "text", "chart", "image", "other"]
    severity: Literal["minor", "major", "blocking"]
    description: str = Field(min_length=1, max_length=2000)


class PageReview(Strict):
    number: int = Field(ge=1, le=100)
    preview_sha256: Digest
    verdict: Literal["pending", "pass", "needs_changes"]
    issues: list[Issue] = Field(max_length=50)

    @model_validator(mode="after")
    def explicit_conclusion(self):
        if self.verdict == "pass" and self.issues:
            raise ValueError("A passing page cannot retain unresolved issues")
        if self.verdict == "needs_changes" and not self.issues:
            raise ValueError("A page needing changes must describe an issue")
        return self


class PowerPointChecks(Strict):
    opened: Check
    content_edited: Check
    native_chart_edited: Literal["pending", "pass", "fail", "not_applicable"]
    saved: Check
    reopened: Check

    @model_validator(mode="after")
    def sequence(self):
        if any(value == "pass" for value in (self.content_edited, self.native_chart_edited, self.saved, self.reopened)) and self.opened != "pass":
            raise ValueError("Open the presentation before recording subsequent successful checks")
        if self.reopened == "pass" and self.saved != "pass":
            raise ValueError("Save the presentation before recording a successful reopen")
        return self


class Command(Strict):
    expected_revision: int = Field(ge=0)
    binding: SourceBinding


class VisualReviewCommand(Command):
    kind: Literal["visual_review"]
    pages: list[PageReview] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=4000)


class PowerPointReviewCommand(Command):
    kind: Literal["powerpoint_review"]
    application_version: str = Field(max_length=200)
    platform: str = Field(max_length=100)
    checks: PowerPointChecks
    note: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def identify_application(self):
        if any(value in ("pass", "fail") for value in self.checks.model_dump().values()):
            if not self.application_version or not self.platform:
                raise ValueError("Record the actual Microsoft PowerPoint version and platform")
        return self


class RevokeCommand(Command):
    kind: Literal["revoke"]
    record_id: Identifier
    reason: str = Field(min_length=1, max_length=2000)


ReviewCommand = Annotated[Union[VisualReviewCommand, PowerPointReviewCommand, RevokeCommand], Field(discriminator="kind")]


class Reviewer(Strict):
    issuer: str
    sub: str


class ReviewRecord(Strict):
    id: Identifier
    revision: int
    kind: Literal["visual_review", "powerpoint_review", "revoke"]
    created_at: str
    reviewer: Reviewer
    binding: SourceBinding
    payload: dict
    revoked: bool
    stale: bool


class AcceptanceSnapshot(Strict):
    project_id: Identifier
    version_id: Identifier
    source_revision: int
    revision: int
    binding: SourceBinding
    pages: list[PageView]
    native_chart_count: int
    programmatic_status: Literal["pending"]
    visual_status: HumanStatus
    powerpoint_status: HumanStatus
    human_status: HumanStatus
    acceptance_status: Literal["pending", "stale", "unreviewable"]
    pending_reasons: list[str]
    can_review: bool
    can_revoke: bool
    publication_authorized: Literal[False] = False
    records: list[ReviewRecord]
