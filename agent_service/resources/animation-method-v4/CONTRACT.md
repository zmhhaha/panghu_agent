# Animation Skill laboratory wire contract

Read `animation-method-input.json`, schema `video.animation-method-input.v2`.
Write one JSON object to `output.json`; do not wrap it in Markdown or a string.
It has exactly these fields and JSON types:

- `schema`: the string `video.animation-method-result.v2`.
- `input_sha256`: the exact input hash string given in the task.
- `mode`: the input's string `plan`, `create` or `review`.
- `criteria`: an array of 1 to 16 objects described below.
- `methods`: an array of at most 8 candidate Skill objects described below.
- `source`: a source object in create mode; JSON null in plan/review modes.
- `sound_events`: an array, including [] when there is no sound.
- `review`: a review object in review mode; JSON null in plan/create modes.
- `knowledge_proposals`: an array, including [] when there is no proposal.

Each criterion has string `id` and nonempty string `description`. Optional
`requires_motion` and `requires_audio` are booleans; optional `observation` is a
nonempty string. IDs are unique lowercase `[a-z][a-z0-9_.-]{0,99}`. Preserve a
nonempty supplied criteria array exactly, including optional fields and order.
Only plan mode may receive an empty criteria array and propose new criteria.

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

Distinguish a planning draft from a persisted parent before choosing lineage:
- An input method without `version_id` is an unpersisted planning draft, even
  when its skill_package already has skill_id and version=1. The next create
  stage is its first implementation, not a new stored version. Keep the package
  at version=1 and omit both method_id and parent_version_id. Refining the draft
  text, Skill files or scene during create does not increment its version.
- Only an input method with an exact persisted `version_id` is an offered
  parent. To revise it, copy that input method's method_id, set parent_version_id
  to that exact version_id, and keep package.skill_id equal to its method_id.
  Set package.version to the input parent's top-level version plus one. Do not
  copy its historical parent_version_id or invent a version_id from skill_id.
- A new independent Skill still starts at version=1 with both lineage fields
  omitted. A matching name, plan stage, previous task or changed files alone
  never authorize version=2. Frozen transfer Skills remain read-only as below.
title, problem and guidance are nonempty strings. criteria is a nonempty exact
subset of the result criteria; runtime_requirements is an object whose engine
string matches source.engine in create mode. research_record_ids is a unique
string array referencing only the supplied records. Do not substitute an array
for applicability or an object for a prose field.

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
Create mode: author complete source and at least one candidate Skill,
review=null. The transfer exception below requires methods=[] instead.
Source has exactly engine, strategy and files. engine is one string from
constraints.allowed_engines. strategy has exactly description and tradeoffs,
both JSON strings of at most 3000 characters; description is nonempty and
tradeoffs may be "". Neither field is an array, object or null.

files is a JSON object mapping filenames to nonempty source-text strings.
The entry file `scene.html` is mandatory. The only other permitted filenames
are `scene.js` and `scene.css`, both optional. `index.html`, nested paths,
additional source files and filename-to-object values are not accepted.
Keep each source file at most 256 KiB UTF-8 and their combined size at most
512 KiB. These scene files are separate from skill_package.files: SKILL.md
belongs to the reusable Skill, never to source.files.

The trusted host opens scene.html at constraints.width by constraints.height
CSS pixels, uses constraints.fps and controls the entire requested
constraints.duration_seconds. Author the actual requested size and duration;
do not assume a different viewport, frame rate or a permanently fixed layout.
Keep HTML/body free of unwanted margins and scrolling. Use an SVG viewBox and
explicit canvas backing size appropriate to that viewport. Create a real 2D
canvas context for canvas; Three.js uses only an admitted local
./assets/three.module.min.js. Use only the exact supplied assets. Do not import
CDNs, addons, network resources, embedded libraries or arbitrary local files.

Every scene must expose `window.__animationExperiment.seek(seconds)` before
the host captures a frame. An optional `window.__animationExperiment.ready`
may be a Promise for bounded initialization; register the interface before
initialization finishes and resolve ready when objects, fonts and permitted
assets are usable. seek may return a Promise, but it must finish that exact
frame before resolving. It accepts absolute elapsed seconds and reconstructs
the complete state for that time. The host will seek forward, backward, to
zero, to the same time repeatedly, and after a fresh page load. Equal times
must produce the same pixels regardless of prior seeks or idle time.

Do not advance from the previous frame, use wall-clock time, unseeded random
state, timers, requestAnimationFrame, autonomous CSS/GSAP animation or a render
loop. A supplied GSAP timeline must stay paused and be set from absolute time.
For canvas, clear and redraw each frame from absolute seconds. For Three.js,
build the scene once, assign all animated transforms/material/camera state
from seconds, then call renderer.render(scene, camera) inside seek. Never
replace this interface with autoplay, Web Workers or active audio/video tags.

sound_events contains at most 128 objects, each with exactly numeric time,
duration, frequency and gain plus string waveform. All numbers are finite:
time is 0..600 seconds, duration is 0.01..5 seconds, frequency is 40..16000 Hz,
gain is 0..0.8, and time+duration must not exceed the requested scene duration.
waveform is sine, triangle or noise. These are host-synthesized sound effects;
do not create narrated audio or play sound from scene code. Use [] when the
task requests no sound. Plan and review always use sound_events=[].
Review mode: methods=[], source=null, sound_events=[] exactly. Assess supplied
frames only. Preserve observation limitations; still images do not establish
motion, timing continuity, audio or complete playback. Use insufficient_evidence
for unobserved criteria, even when historical Wiki prose says a technique works.
review has exactly summary (nonempty string), outcome (improved, regressed,
equal, inconclusive or goal_met), preferred_candidate_id (an observed candidate
ID string or null), criteria_results (array), remaining_issues (string array),
next_guidance (string, may be empty) and limitations (string array).
Each criteria_results object has exactly criterion_id, status and evidence,
all strings; status is passed, failed or insufficient_evidence. Include every
criterion exactly once. Motion/audio criteria cannot be passed from frames.
goal_met requires every criterion passed. Compare improved/regressed/equal
only with actual evidence for two candidates. improved prefers the current
candidate; regressed prefers the baseline; equal/inconclusive retain the
baseline or null when a baseline exists. Retain all input limitations.

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
