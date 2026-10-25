"""Read the pinned local task template without interpreting user topic as code."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[2] / "scientific_skills"
BUNDLE_MANIFEST = BUNDLE_ROOT / "manifest.json"
TASK_TEMPLATE = BUNDLE_ROOT / "task-template.md"
SKILL_PATH = BUNDLE_ROOT / "research-procedure" / "SKILL.md"
BUNDLE_REVISION = "local-procedures-v2"

# Server-owned writing contracts, selected only by the validated mode field.
# Topic text and arbitrary job attributes cannot supply or replace this section.
WRITING_MODES = {
    "brief": """Selected output: research brief (mode=brief).
Give a compact answer to the research question, the most relevant evidence and
the limits of that answer. Before drafting, identify the central finding and
which acquired evidence supports or challenges it. Short prose and useful
bullets are appropriate; avoid a paper-by-paper catalogue. Compare studies when
they address a comparable question, without implying completeness. Keep search
and audit detail in the required evidence artifacts; summarize actual methods
and access limits in the report. End with the bounded answer and what remains
unknown. Do not expand this brief into a full review merely to add length.""",
    "review": """Selected output: narrative literature review (mode=review).
Before drafting, plan a thematic argument around the research question: map
included evidence to the relevant mechanisms, comparisons or outcomes, identify
agreements, disagreements and gaps, and decide what can actually be compared.
Keep this working plan internal; do not add an unrequested output artifact.

Write a concise Abstract/摘要, then an Introduction/引言 that explains the
problem, context and question. Include Scope and concise Methods. Organize
Findings into themes that answer the question, using connected analytical prose
rather than one subsection per paper or a sequence of isolated findings.
Where studies address a comparable question, synthesize their evidence in the
same argument: explain common findings and differences in methods, conditions,
populations or outcome definitions only when acquired material supports that
explanation. If a cause of disagreement is unknown, say so. Do not rank results
or pool effects across incompatible settings or metrics.

Include Discussion/讨论 to interpret the synthesis, distinguish published
findings from your own reasoned interpretation, discuss applicability and
unresolved questions, and explain the strength and limits of the evidence.
Include Limitations and a Conclusion/结论 that directly answers the question
within those limits, followed by References. Keep operational search, screening
and verification detail in the required artifacts; summarize the actual methods
and important access limits in readable prose, without reproducing audit logs.
Tables and lists may supplement the argument; they must not replace its main
analysis. Do not force two sources into every paragraph, a fixed word/paragraph
count, or a minimum paper count. A theme with one relevant source can be honest
and useful. With sparse or abstract-only material, narrow the conclusions and
make the evidence gaps explicit; do not invent comparisons, expand by repetition
or infer details from full text that was not acquired. If the question cannot be
answered, produce an evidence-insufficiency review instead of a fabricated one.""",
}


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
    mode = fields.get("mode", "review")
    if not isinstance(mode, str) or mode not in WRITING_MODES:
        raise ValueError("unsupported research mode")
    fields["mode"] = mode
    job_json = json.dumps(fields, ensure_ascii=False, allow_nan=False, sort_keys=True)
    # Encode delimiter characters to prevent user text closing the job boundary.
    job_json = job_json.replace("<", "\\u003c").replace(">", "\\u003e")
    template = TASK_TEMPLATE.read_text(encoding="utf-8")
    # Format once: braces or placeholder names in user data are never expanded.
    # JSON examples in the trusted template use doubled braces.
    return template.format(job_json=job_json, writing_instructions=WRITING_MODES[mode])
