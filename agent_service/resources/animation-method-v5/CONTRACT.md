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

## Knowledge proposals: exact JSON contract

`knowledge_proposals` is a JSON array of 0 to 8 objects, with unique page_id
values. `[]` is valid in every mode when there is no justified Wiki change.
If knowledge_snapshot=null (reference-free transfer), it must be `[]`. A frozen
snapshot with no usable experience records also permits no nonempty proposal:
each proposal requires at least one actual frozen experience. Do not create a
Wiki proposal just to fill this field. These proposals may update Wiki pages
through host compare-and-swap; they never publish a Skill or prove success.

A proposal is an object with the following fields. No other keys are allowed.
Required fields are page_id, expected_revision, body, evidence_ids, change_note.
The three optional fields have defaults, but emitting all eight explicitly is
recommended. Omitted optional fields are not equivalent to JSON null.

| Field | JSON type and exact rule |
| --- | --- |
| page_id | String matching `[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}`. The page's stable ID, not a title or a newly invented revision ID. |
| expected_revision | Integer, `0 <= expected_revision < 1000000`. For a page absent from the supplied frozen snapshot.pages, use integer **0**. For an existing page, copy its exact integer revision from that snapshot. This is the current expected revision, not the next revision. **null, false, true, "0", 0.0, -1 and an omitted field are invalid.** |
| body | Object described below, at most 128 KiB in canonical UTF-8 JSON. |
| evidence_ids | Array of 1 to 256 unique ID strings of the same ID syntax as page_id. Every ID must occur in frozen snapshot.experiences, with kind other than `knowledge_proposal`. Neither a Wiki page ID, research_record_id nor a new task/render ID is a substitute. |
| change_note | Nonempty string, at most 8000 characters, explaining the change and its evidence boundary. |
| status | Optional string: `provisional`, `active` or `deprecated`; default `provisional`. Prefer provisional for a new uncertain inference. `active` is a Wiki status, not developer acceptance or a Skill release. |
| change_kind | Optional string: `create`, `revise`, `correct` or `deprecate`; default `create` when expected_revision=0, otherwise `revise`. `create` if and only if expected_revision=0. `deprecate` if and only if status=`deprecated`; use a nonzero exact frozen revision for deprecation. |
| relations | Optional array of 0 to 32 unique objects described below; default `[]`. No guessed target revisions or hashes. |

The raw proposal object is at most 192 KiB canonical UTF-8 JSON. Its body must
fit 128 KiB both before and after default arrays are materialized. The normalized
complete proposals array is at most 512 KiB minus 1024 bytes; the whole result
must also fit its output limit. All prose strings are nonempty after whitespace trimming
and have the stated character bounds; embedded NUL/control characters other
than CR, LF and tab, DEL and unpaired surrogate code points are invalid.

body has exactly the following allowed keys; title and content are required.

| Field | JSON type and exact rule |
| --- | --- |
| title | Nonempty string, at most 1000 characters. |
| content | Nonempty string, at most 64000 characters. Separate observations from inference, preserve contradictions/failures, and state uncertainty. |
| applicability | Optional array of 0 to 32 nonempty strings, each at most 4000 characters; default `[]`. This is an array, unlike a method's prose applicability field. |
| limitations | Optional array of 0 to 32 nonempty strings, each at most 4000 characters; default `[]`. |
| counterexamples | Optional array of 0 to 32 objects; default `[]`. Each has exactly description (nonempty string, at most 8000 characters) and evidence_ids (array of 1 to 32 unique ID strings). Every counterexample evidence ID must also be in this proposal's evidence_ids. |

Each relations item has exactly these four required fields:

| Field | JSON type and exact rule |
| --- | --- |
| relation | String: `corrects`, `supersedes`, `contradicts` or `extends`. |
| page_id | ID string of an existing target in the supplied frozen snapshot.pages. A page proposed in this same result is not yet a frozen target. |
| revision | Integer >= 1; copy the target page's exact frozen revision. Booleans, floats, strings and null are invalid. |
| content_sha256 | String of exactly 64 lowercase hex characters; copy the target page's exact frozen content_sha256, not its sha256 or the snapshot hash. |

Evidence and relations must come from the exact supplied frozen snapshot,
not a live Wiki read, another owner's records, historical revisions outside
that snapshot, an assumed reference, or this unrendered output. A page proposal
is not independent evidence for another page. If a needed fact, revision or
hash is unavailable, omit the unsupported proposal/relation; never invent it.
The host normalizes evidence IDs and relations and rechecks CAS at apply time.
A stale snapshot cannot authorize overwriting a newer page.

The following complete **synthetic** new-page proposal shows JSON types,
including optional fields and a counterexample. The ID is illustrative only:
replace it with actual input evidence. If no such evidence exists, emit `[]`.
This example asserts no rendered observation or developer acceptance.

```json
{
  "page_id": "evidence-scope",
  "expected_revision": 0,
  "body": {
    "title": "Keep evidence scope explicit",
    "content": "A synthetic brief is planning context; it does not establish rendered motion or acceptance.",
    "applicability": ["Interpreting a supplied planning brief"],
    "limitations": ["No new render or continuous playback has been observed."],
    "counterexamples": [
      {
        "description": "Written source alone cannot establish that the requested movement was rendered.",
        "evidence_ids": ["exp_synthetic_brief"]
      }
    ]
  },
  "evidence_ids": ["exp_synthetic_brief"],
  "status": "provisional",
  "change_kind": "create",
  "change_note": "Synthetic contract example only; preserve the planning-versus-render boundary.",
  "relations": []
}
```

For an existing page, construct the same complete proposal with its page_id,
set expected_revision to that page's exact snapshot revision r (not r+1), and
use change_kind=`revise`, `correct` or `deprecate` as appropriate. Never copy
previous_revision_sha256=null into expected_revision. Do not output a stored
page record in place of a proposal.

## Frozen page records are read-only input

Each knowledge_snapshot.pages item has exactly these fields. The host seals
them; do not author, repair, recompute or copy these envelope fields into a
proposal. Read them to select exact evidence and CAS/relation targets.

| Field | JSON type and rule |
| --- | --- |
| schema | String `video.animation-knowledge-page.v1`. |
| owner | Nonempty string, at most 500 characters, without control characters; matches the snapshot owner. |
| page_id | ID string with the same syntax as proposal.page_id. |
| revision | Integer from 1 to 1000000. The current persisted page revision; a revision of 1000000 cannot be the expected_revision of a further proposal. |
| previous_revision_sha256 | JSON null only for revision=1; otherwise a 64-character lowercase hex string identifying the previous sealed page. **This nullable field is different from proposal.expected_revision.** |
| body | The body object above, with all three optional arrays materialized. |
| status | String `provisional`, `active` or `deprecated`. |
| evidence_ids | Sorted unique array of 1 to 256 frozen non-proposal experience IDs. Counterexample evidence IDs are a subset. |
| relations | Array with the same four relation fields and structural types above, normalized by the host. Stored links may retain a historical revision or a target absent from this snapshot. A newly proposed relation must instead match an actual supplied target's exact frozen revision and content_sha256. |
| content_sha256 | 64 lowercase hex characters; hashes the canonical object with exactly body, status, evidence_ids and relations. This is the hash referenced by a relation. |
| change_kind | `create` for revision=1, otherwise `revise`, `correct` or `deprecate`; deprecate if and only if status=`deprecated`. |
| change_note | Nonempty string, at most 8000 characters. |
| created_at | Finite nonnegative JSON number; a host timestamp, not a proposed observation time. |
| sha256 | 64 lowercase hex characters; seals every other field of this persisted page. Not interchangeable with content_sha256. |

The immutable snapshot supplies pages and experiences with their exact hashes;
valid hashes bind contents, while trusted host custody authenticates origin.
Neither hashing, Wiki status, an earlier sample, nor a source write can establish
that the current animation was rendered, heard, transferred or accepted.
