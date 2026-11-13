# Animation Skill laboratory wire contract

Input schema: `video.animation-method-input.v2`. Output schema:
`video.animation-method-result.v2`. Preserve exact input_sha256 and mode.
Result has exactly schema, input_sha256, mode, criteria, methods, source,
sound_events, review, knowledge_proposals. Criteria IDs use lowercase
`[a-z][a-z0-9_.-]{0,99}`. Preserve all supplied criteria exactly.

Each new method has exactly schema=`video.animation-method.v2`, title, problem,
guidance, applicability (one nonempty string), limitations (string array),
criteria (an exact nonempty subset of result.criteria), runtime_requirements
(`{"engine":"html-svg|canvas|three"}`), research_record_ids and skill_package;
optional method_id and parent_version_id may name only supplied owned lineage.
skill_package is an author draft with exactly skill_id, version, kind
(`expression` or `foundation`), files and runtime_requirements (engines array).
The trusted collector computes metadata and SHA-256 hashes; do not guess hashes.
New independent skills start at version 1. A supplied parent's successor keeps
its skill_id and increments that parent's exact version by one.

files contains SKILL.md and optionally local references/, assets/, scripts/.
SKILL.md begins with `---`, a lowercase hyphenated `name: ...`, one-line
`description: ...`, and `---`, followed by actionable reusable instructions.
Explain when to apply, decision rules, adaptation knobs, failure modes and
counterexamples. Local Markdown links must resolve inside the package. Do not
embed reference URLs, original media identity, raw viewing notes or external
runtime dependencies in operational Skills. research_record_ids may only name
the supplied independent research records. A Skill is knowledge, not a fixed
animation recipe or permission to execute its scripts.

Plan mode: source=null, sound_events=[], review=null. Propose criteria and
complete candidate Skills; do not claim the unrendered proposals succeeded.
Create mode: author complete source and candidate Skills, review=null. Source
has engine, strategy (exactly description and tradeoffs strings, max 3000 chars
each; description nonempty, tradeoffs may be empty), and files (path-to-text).
Respect admitted assets and seekable runtime requirements already in input.
Review mode: methods=[], source=null, sound_events=[] exactly. Assess supplied
frames only. Preserve observation limitations; still images do not establish
motion, timing continuity, audio or complete playback. Use insufficient_evidence
for unobserved criteria, even when historical Wiki prose says a technique works.

For a transfer (`constraints.method_application.role=transfer`) return methods=[]
in every mode. Reuse the exact frozen Skills without revising them. The transfer
has no original reference, raw observations or Wiki snapshot. Create a distinct
composition for changed content and structure, not a renamed reference layout.
Never generate reference-removal receipts, render records, acceptances or releases.

knowledge_proposals is an array (possibly empty) of page_id, expected_revision,
body, evidence_ids, status, change_kind, change_note, relations. body has title,
content and optional applicability[], limitations[], counterexamples[] (each
description and evidence_ids). status is provisional, active or deprecated;
change_kind is create, revise, correct or deprecate. evidence_ids must name actual
experiences in the frozen knowledge_snapshot; existing pages must use the frozen
revision. relations entries contain relation (corrects, supersedes, contradicts,
extends), page_id, revision, content_sha256. Separate observation from inference,
retain failed knowledge and counterexamples, and explain uncertainty. These
proposals may update the Wiki through host CAS; they never publish a Skill.
If knowledge_snapshot=null (reference-free transfer), knowledge_proposals=[]
is required. Do not infer success from this task before a render exists.
