---
name: animation-hyperframes-foundation-v1
description: Author seekable HyperFrames animation projects with HTML/SVG, Canvas2D or Three.js against the supplied project clock, assets and runtime contract.
---

# Seekable animation projects

Use the task's exact project schema, admitted assets and native HyperFrames
composition/adapter interface. A package being installed, a private seek
function or successful browser playback does not establish native rendering.
This guidance supplies engineering invariants; choose the composition and
expression yourself.

## Project clock

Treat each passage as part of one project clock. Derive local time from its
declared absolute interval; preserve the confirmed narration's actual timing.
Keep meaning, identities and useful spatial context coherent at passage
boundaries. Do not replace the recording, stretch it or start a second clock
to hide a mismatch. Rendering and audio composition belong to the host.

Every requested time must reconstruct the complete intended state, including
before an entrance and after an exit. Seeking backward, seeking out of order,
seeking twice or reloading must produce the same frame. Avoid unseeded random
values, wall-clock time, accumulating simulation steps and autonomous playback.
Derive procedural variation from a fixed seed and the requested time.

## Drawing choices

- HTML/SVG: use explicit initial states and the admitted HyperFrames timeline
  interface. A connector should appear when its relationship is explained;
  hiding only an endpoint does not hide its line. Avoid simultaneous competing
  CSS and JavaScript ownership of the same animated property.
- Canvas2D: redraw from absolute time, clear prior output and reset transforms,
  clipping and drawing state. Reconstruct particles or traces analytically or
  from deterministic data; do not depend on the previously captured frame.
- Three.js: derive object/camera state from absolute time and explicitly draw
  the resulting scene through the admitted adapter. Complete asynchronous
  resource or shader work before the seek reports ready. Browser/WebGL context
  admission is supplied by the host, never inferred from your engine label.

Combine these drawing choices when useful without inventing another export
protocol. Use local, admitted resources; runtime code must not fetch reference
videos, libraries or fonts. Standard SVG/XML namespace identifiers are inert
identifiers and can remain in local source.

## Completion checks

Check entrances, intermediate changes, exits and the neighboring states, rather
than only a pleasing final frame. Ensure camera changes, clipping and text
remain legible throughout their actual motion. Let asynchronous readiness
finish before capture; never substitute a fixed delay for resource completion.

Keep technical validity, actual Skill reading, visual application and observed
quality separate. Declare unknowns and tradeoffs. The host validates, renders
and observes the result; source declarations alone cannot attest to a working
engine, deterministic export or human approval.
