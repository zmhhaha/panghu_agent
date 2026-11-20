---
name: hyperframes-composable-film-foundation
description: Author original HTML/SVG, Canvas2D or Three.js modules for an exact global film clock and deterministic lifecycle.
---

Use this engineering foundation for composable modules inside one HyperFrames
film. It supplies lifecycle and resource constraints, not visual composition or
an animation recipe. Choose spatial structure, medium and movements for the
actual explanation. Do not relabel a fixed demo or make every passage a title
card. Legacy window.__animationExperiment.seek is a different contract.

Export async function mount(root, context), async function renderAt(context),
and async function dispose(). Save only your supplied root and owned resources.
mount initializes an original DOM/SVG, real Canvas2D or real Three WebGL2 scene;
await all resources it needs. renderAt reconstructs the full exact frame from
context and awaits completion. dispose releases listeners, Three geometries,
materials, renderer/context and owned DOM; no pending callbacks may remain.
The host may create a fresh realm and mount for every frame. Never require a
previous render call, real elapsed time or persistent mutation history.

context includes global_frame, time {numerator,denominator} in seconds,
film_sha256, module_id, namespace, role, engine, scene_id, start_frame, end_frame,
local_frame, phase, states. time is the global frame/24 rational. Scene
local_frame = global_frame - scene start; it can be negative during entering
or extend past the scene end while exiting. phase is entering, active or
exiting. Clamp deliberately and preserve meaningful boundary states; do not
change the clock. Leading/trailing holds belong to the declared scene clock,
while beats/alignment follow the recording after its leading hold.

A transition context adds boundary_index, from_scene_id, to_scene_id, at_frame
and objects. Its local_frame starts at its own window start, and phase is
active. Other roots are separate opaque realms. Shared semantic identities
travel as frozen state declarations, never shared DOM references. You may draw
an original explanation of transformation in your own root, but cannot read or
reposition another module's DOM. Make each root's background and layering an
intentional choice so concurrent roots do not accidentally cover the subject.

Absolute seeks must agree for forward, reverse, random order, repeat and fresh
load. Seed randomness with fixed input and object identity and reconstruct by
frame; do not advance a random generator differently depending on seek order.
Avoid wall clocks, autonomous RAF/timers, CSS running animations, accumulated
simulation, workers, external I/O and storage. Return all promises; a pending
RAF or late asset draw cannot complete a captured frame reliably.

For Canvas, reset every relevant drawing state and redraw the complete bitmap,
not just pixels or transform; lineJoin, clipping, compositing, alpha, font and
dash state must not leak across seeks. Use save/restore or explicit reset.
For Three, create a real WebGL2 renderer using only admitted pinned Three.js,
update camera and scene from exact time, and render before resolving. No
external addons/textures; support the admitted software WebGL environment.
HTML/SVG must not open Canvas; Canvas2D must not open WebGL. Keep elements
within the mount root and avoid relying on global document selection.

The module entry lives at /project/modules/<namespace>/<entry>. Relative local
imports can address declared module files or ../../assets/<name>. Explicit
asset fetches, where needed, may only read exact admitted local assets. Wait
for document.fonts.ready when font metrics affect layout. Do not use a top-level
HTML file as the entry, script injection, data code or external URL. The browser
does not play narration; the shared film mixer consumes the exact original
recordings separately. This module cannot add unsupported audio events.

Check your declared entry/exit identities, real medium, source paths, chosen
resource requirements and deterministic reconstruction before returning. These
checks are reasoning, not a claim that the code has already run. Actual
HyperFrames capture, decoding, motion/audio review and developer recognition
belong to later independent evidence.
