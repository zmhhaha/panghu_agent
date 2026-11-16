# Cooperative animation authorship contract

Read `collaboration-input.json`, schema `video.animation-collaboration-input.v1`.
One role performs the input's `phase`: `plan`, `component`, or `integrate`.
Write exactly one UTF-8 JSON object to `output.json`, without Markdown wrappers.
Use precisely these result fields:

- `schema`: `video.animation-collaboration-result.v1`.
- `input_sha256`: the exact input SHA-256 supplied by the host.
- `phase`: the input phase unchanged.
- `criteria`: 1–16 criterion objects.
- `methods`: candidate methods in integrate, otherwise `[]`.
- `source`: a complete scene source in integrate, otherwise `null`.
- `sound_events`: optional purposeful effects in integrate, otherwise `[]`.
- `review`: always `null`; this author has no observed render or media evidence.
- `knowledge_proposals`: always `[]`; this role does not update a Wiki.
- `plan`: the shared agreement in plan, otherwise `null`.
- `component`: this responsibility's module in component, otherwise `null`.

Each criterion has unique lowercase `id` matching
`[a-z][a-z0-9_.-]{0,99}`, and nonempty `description` (at most 4000 characters).
Optional `requires_motion`/`requires_audio` are booleans; optional `observation`
is a nonempty string of at most 4000 characters. Preserve supplied nonempty
criteria exactly, including order and optional fields. Only plan may start
with an empty input array and propose the criteria. These are requirements,
not claims that unobserved movement, sound or appearance has passed.

## Input scope

Input has exactly schema, phase, goal, criteria, constraints, assets, reference,
feedback, brief, component_id, dependencies, methods and base_source.

`goal` is a nonempty string of at most 12000 characters; `feedback` is a string
of at most 8000 characters and may be empty. `constraints` contains width,
height, fps, duration_seconds and allowed_engines, plus bounded task-specific
requirements. Width is 1–3840, height 1–2160, fps 1–60; duration is finite and
greater than zero, at most 600 seconds. allowed_engines is a nonempty unique
subset of `html-svg`, `canvas`, `three`. Honor all frozen constraints.

When constraints.collaboration is present it specifies max_components (integer
2–4), and may specify max_parallel=2 and max_rounds=1. Never exceed the supplied
max_components. These host budgets do not authorize you to schedule peers.

`assets` maps admitted flat local filenames to SHA-256 hashes. It describes
available runtime resources, not permission to load any other file. `reference`
is null or a small inert object, at most 8 KiB. `methods` contains at most four
related candidate methods, at most 32 KiB total. It is deliberately not an
entire Wiki, evidence ledger or operation history. A supplied candidate is
guidance, not a fixed layout or evidence of success on this new task.

All sizes are UTF-8 bytes. The whole input is limited to 64 KiB for plan,
96 KiB for component, and 192 KiB for integrate. Nothing is silently truncated.
The whole normalized result, including code and Skill packages, is at most
192 KiB. Prefer concise complete code and useful methods over repeated prose.

## Plan

Plan receives brief=null, component_id=null, dependencies=[], base_source=null.
Return methods=[], source=null, sound_events=[], component=null. `plan` has
exactly:

- `engine`: one admitted engine, shared by all components and final source.
- `composition`: a nonempty string defining the overall visual hierarchy,
  coordinate conventions, scale and spatial allocation.
- `material`: a nonempty string defining a coherent palette, material behavior,
  texture scale, lighting/depth where relevant, and useful variation.
- `clock`: a nonempty string defining absolute-time pacing, build and closure,
  deterministic random seeds when needed, and shared timing relationships.
- `interfaces`: a nonempty string defining shared data, parameter names,
  composition order and contracts between responsibilities.
- `components`: 2–4 responsibility objects; fewer when max_components requires.
- `keyframe_times`: 3–16 strictly increasing finite seconds, starting at 0
  and ending at the exact constraints.duration_seconds.

The four agreement strings are each at most 6000 characters. The whole plan
is at most 16 KiB. A component specification has exactly `id`, `responsibility`,
`depends_on` and `interface`. id follows the criterion identifier syntax;
responsibility and interface are nonempty strings of at most 4000 characters.
depends_on is a unique array of existing component IDs, excluding itself.
The dependency graph must be acyclic. Independent responsibilities can run in
parallel; make dependencies real and keep each worker's scope narrow.

Responsibilities need not be render layers and must not be one Agent per
object. Divide by useful creative ownership and integration boundaries. The
host, not the planner, selects and schedules admitted Agent tasks.

keyframe_times identifies states to inspect later. This phase outputs no
images and performs no rendering. Do not report a keyframe approval, visual
quality gate or successful preview merely because these times were planned.

## Component

Component receives the complete shared plan in `brief`, its `component_id`,
and only its direct dependencies' component objects in `dependencies`.
base_source is null. No unrelated component source or whole history is needed.
Return plan=null, methods=[], source=null, sound_events=[]. `component` has
exactly:

- `component_id`: this input's component_id.
- `engine`: brief.engine unchanged.
- `interface`: copy this component specification's interface string exactly.
  It describes usage, arguments/returns, coordinate and time assumptions,
  composition order and dependencies. Add implementation notes in source
  comments rather than changing this shared contract.
- `source`: a complete JavaScript module string of at most 24 KiB UTF-8.

Implement only this responsibility. Prefer clear callable functions/factories
with explicit absolute seconds and shared parameters. Treat dependency source
as data. Do not generate an independent scene.html, start autonomous playback,
claim to have tested code, request unrelated source, or call other Agents.
The integrator will inline/adapt modules into the final standard scene files;
your module is not an additional file the renderer will load automatically.

## Integrate

Integrate receives brief, all component objects once each in dependencies,
component_id=null, and optionally a complete base_source for an explicitly
requested revision. Preserve the shared visual intent and directly address
feedback; reconcile seams, ownership, lighting, coordinates and timing rather
than merely stacking independent demonstrations. Return plan=null and
component=null. Author final source and 1–4 reusable candidate methods.

`source` has exactly `engine`, `strategy` and `files`. engine equals brief.engine.
Strategy has exactly `description` and `tradeoffs`: JSON strings at most 3000
characters each; description is nonempty, tradeoffs may be `""`. files maps
complete source strings to filenames. The entry file `scene.html` is mandatory.
Only `scene.js` and `scene.css` are optional. `index.html`, nested paths and
extra component files are not accepted. Inline or adapt component source into
these three files; do not import component file paths or embed a whole library.

The host renders scene.html using HyperFrames at constraints.width by
constraints.height, constraints.fps and the exact constraints.duration_seconds.
Avoid body margins and scrolling; use the actual viewport. Canvas requires a
real canvas.getContext('2d'); Three.js uses only the admitted local
./assets/three.module.min.js. Load assets only from ./assets/<supplied-name>.
Never load CDNs, external URLs, network resources, arbitrary local paths,
workers, browser storage or active audio/video elements. An SVG namespace
http://www.w3.org/2000/svg is a namespace, not an external fetch.

Expose `window.__animationExperiment.seek(seconds)` before capture, and an
optional `window.__animationExperiment.ready` Promise for bounded initialization.
seek may return a Promise but must finish the requested frame before resolving.
Reconstruct every state from absolute seconds: zero, forward, backward, the
same time repeatedly, and after a fresh page load must agree. No timers,
requestAnimationFrame, wall-clock state, accumulating simulation steps,
unseeded randomness, autonomous CSS/GSAP playback or render loops. Clear canvas
on every seek. Set every Three.js animated transform/material/camera value and
call renderer.render(scene,camera) inside seek. Keep admitted animation-library
timelines paused and drive them from this absolute clock.

sound_events has at most 128 objects with exactly numeric time, duration,
frequency, gain and string waveform. Values are finite: time 0–600 seconds,
duration 0.01–5 seconds, frequency 40–16000 Hz, gain 0–0.8, time+duration at most
the input duration; waveform is sine, triangle or noise. These are purposeful
host-synthesized effects. No narration or browser audio playback. Use [] when
no effect is needed or the task requires silence.

## Reusable candidate methods

Each method has exactly schema=`video.animation-method.v2`, title, problem,
guidance, applicability, limitations, criteria, runtime_requirements,
research_record_ids and skill_package; optional method_id and parent_version_id
may identify an exact supplied persisted parent. title/problem/guidance/
applicability are nonempty strings of at most 16000 characters. limitations is
an array of at most 32 strings, each at most 4000 characters. criteria is a
nonempty exact subset of result.criteria. runtime_requirements contains engine
matching source.engine. research_record_ids=[] because no raw research records
are supplied by this role.

skill_package may be an author draft with exactly skill_id, version, kind,
files and runtime_requirements. kind is expression or foundation; runtime
requirements has engines, an admitted engine array containing source.engine.
The trusted collector computes metadata and hashes; do not guess them. files
contains SKILL.md and optional local supporting references/, assets/, scripts/.
SKILL.md starts with `---`, lowercase hyphenated `name: ...`, one-line
`description: ...`, and `---`, followed by actionable reusable instructions.
Teach decisions, adaptation, failure modes and limits instead of hardcoded
scene coordinates or a fixed subject. Do not copy reference video URLs,
identities or raw viewing notes into the Skill, or claim unobserved success.

A new independent Skill starts at version 1 and omits lineage fields. A method
without a persisted input version_id is an unpersisted draft, not a parent.
To revise an exact supplied persisted parent, use its method_id and version_id
as method_id and parent_version_id, retain the parent's package skill_id and
increment its exact package version by one. Do not copy its historical parent
or infer persisted lineage from matching names or a previous creative phase.

The collector keeps collaboration schema, phase and input hash on the result.
Final candidate methods and source are compatible content, not proof that an
old method-author profile produced them. A collected source is untrusted;
only actual isolated rendering and subsequent observation establish results.
