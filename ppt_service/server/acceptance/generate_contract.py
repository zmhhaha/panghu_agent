"""Generate public JSON schemas and explicitly synthetic interop fixtures."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from ..auth import Identity
from .contracts import AcceptanceSnapshot, PageDescriptor, ReviewCommand, ReviewRecord, SourceBinding
from .domain import ReviewContext, project_snapshot


def generate(directory: Path | None = None):
    directory = directory or Path(__file__).with_name("contracts")
    directory.mkdir(parents=True, exist_ok=True)
    binding = SourceBinding(
        project_id="contract-project", version_id="contract-version", source_revision=3,
        manifest_sha256="1" * 64, input_snapshot_sha256="2" * 64,
        pptx_sha256="3" * 64, source_fingerprint_sha256="4" * 64,
        evidence_id="synthetic-contract-evidence", evidence_sha256="5" * 64,
        runtime={"engine": "pi", "runtime_revision": "synthetic-contract-revision", "profile": "ppt", "checkpoint_format": "artifact-replay-v1"},
    )
    context = ReviewContext(
        owner=Identity("https://synthetic.invalid", "contract-reviewer"), binding=binding, source_revision=3,
        pages=(PageDescriptor(number=1, title="Synthetic contract page; no acceptance evidence", preview_sha256="6" * 64, source_sha256="7" * 64),),
        native_chart_count=1, current_input_snapshot_sha256=binding.input_snapshot_sha256,
        current_source_fingerprint_sha256=binding.source_fingerprint_sha256,
        origin_status="candidate", phase="generate",
    )
    visual = TypeAdapter(ReviewCommand).validate_python({
        "kind": "visual_review", "expected_revision": 0, "binding": binding.model_dump(),
        "pages": [{"number": 1, "preview_sha256": "6" * 64, "verdict": "pass", "issues": []}],
        "note": "Synthetic contract assertion; not an actual visual review",
    })
    powerpoint = TypeAdapter(ReviewCommand).validate_python({
        "kind": "powerpoint_review", "expected_revision": 1, "binding": binding.model_dump(),
        "application_version": "Microsoft PowerPoint synthetic contract build", "platform": "Synthetic test platform",
        "checks": {"opened": "pass", "content_edited": "pass", "native_chart_edited": "pass", "saved": "pass", "reopened": "pass"},
        "note": "Synthetic contract assertion; PowerPoint was not run",
    })
    records = [ReviewRecord(
        id=f"synthetic-record-{i}", revision=i, kind=command.kind,
        created_at="2000-01-01T00:00:00Z", reviewer={"issuer": context.owner.issuer, "sub": context.owner.sub},
        binding=binding, payload=command.model_dump(exclude={"kind", "expected_revision", "binding"}),
        revoked=False, stale=False,
    ) for i, command in enumerate((visual, powerpoint), 1)]
    revoke = TypeAdapter(ReviewCommand).validate_python({
        "kind": "revoke", "expected_revision": 2, "binding": binding.model_dump(),
        "record_id": records[0].id, "reason": "Synthetic correction",
    })
    output = {
        "command.schema.json": TypeAdapter(ReviewCommand).json_schema(),
        "snapshot.schema.json": AcceptanceSnapshot.model_json_schema(),
        "pending.fixture.json": project_snapshot(context, 0, []).model_dump(mode="json"),
        "human-passed.fixture.json": project_snapshot(context, 2, records).model_dump(mode="json"),
        "visual-review.fixture.json": visual.model_dump(mode="json"),
        "powerpoint-review.fixture.json": powerpoint.model_dump(mode="json"),
        "revoke.fixture.json": revoke.model_dump(mode="json"),
    }
    for name, value in output.items():
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    generate()
