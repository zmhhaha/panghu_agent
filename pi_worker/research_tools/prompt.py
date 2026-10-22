"""Read the pinned local task template without interpreting user topic as code."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[2] / "scientific_skills"
BUNDLE_MANIFEST = BUNDLE_ROOT / "manifest.json"
TASK_TEMPLATE = BUNDLE_ROOT / "task-template.md"
SKILL_PATH = BUNDLE_ROOT / "research-procedure" / "SKILL.md"
BUNDLE_REVISION = "local-procedures-v1"


def build_research_prompt(job) -> str:
    """Accept a job mapping or shared ResearchJob object; omit private fields.

    Only topic/mode/task_id/attempt are sent into the model prompt, never user
    identifiers, email, credentials or arbitrary command/skill-path attributes.
    """
    if isinstance(job, Mapping):
        fields = {key: job[key] for key in ("topic", "mode", "task_id", "attempt") if key in job}
    else:
        fields = {key: getattr(job, key) for key in ("topic", "mode", "task_id", "attempt") if hasattr(job, key)}
    if not isinstance(fields.get("topic"), str) or not fields["topic"].strip():
        raise ValueError("job topic must be a nonempty string")
    if fields.get("mode", "review") not in {"brief", "review"}:
        raise ValueError("unsupported research mode")
    fields.setdefault("mode", "review")
    job_json = json.dumps(fields, ensure_ascii=False, allow_nan=False, sort_keys=True)
    # Encode delimiter characters to prevent user text closing the job boundary.
    job_json = job_json.replace("<", "\\u003c").replace(">", "\\u003e")
    template = TASK_TEMPLATE.read_text(encoding="utf-8")
    # Only this placeholder is substituted; JSON examples use doubled braces.
    return template.replace("{{", "{").replace("}}", "}").replace("{job_json}", job_json)
