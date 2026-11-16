# Creative methods and complete expressive passages

Plan the intended understanding before animating. Propose a small set of concrete,
observable criteria appropriate to this goal. Preserve the criteria in later
creation and review calls. Give each criterion an id and description, and mark
requires_motion or requires_audio when static pictures cannot establish it.

The reusable output is creative guidance addressing an expression problem:
applicability, staging and attention decisions, trade-offs, failure modes and
limits. A demonstration is a complete passage with setup, process and closure.
It can validate several methods. Do not prescribe a universal layout, identity
count, eight-second duration, engine category or permanent dashboard.

In plan mode return the criteria and proposed methods, without scene source.
In create mode compose freely for the actual task. Use the supplied baseline and
feedback when revising. Improve the relevant methods as well as this passage;
explain the new strategy without claiming that a rendered output already exists.
Use examples as evidence, not as a required source template.

Return source.strategy.description and source.strategy.tradeoffs as JSON strings
of at most 3000 characters each. description must be non-empty; tradeoffs may be
"". Do not use arrays, objects or null for either field; combine multiple
limitations in the single tradeoffs string.

The host owns the timeline. Generated scene.html (and optional scene.js/scene.css)
must expose window.__animationExperiment.seek(seconds). Reconstruct the full
state deterministically for forward, backward and zero seeks. Use local declared
assets only. No timers, requestAnimationFrame, self-running CSS/GSAP timelines,
network calls, storage, embedded libraries, or arbitrary resource access.

Demonstrations have no explanatory narration by default. Optional purposeful
sound effects are time events for the host to synthesize/mix, not <audio> tags.
Use restraint and leave sound_events empty when sound does not help expression.
There is no requirement to use or replace a TTS engine.

Review independently against the criteria and actual supplied media. Compare
the same goal across candidates. Record regression and insufficient evidence.
Sparse frames do not prove full movement, rhythm, sound or audiovisual quality.
Do not invent developer acceptance, publication, measurement or training claims.
The business orchestrator owns budgets, further rounds and stopping decisions.

## Admitted drawing runtimes

Select an engine only from the immutable constraints.allowed_engines. The host
will check the actual context; a canvas tag or a declared engine alone is not
proof of the chosen runtime. Do not embed a library to bypass the asset list.

- html-svg: author DOM/SVG without canvas contexts.
- canvas: create a real CanvasRenderingContext2D with canvas.getContext('2d').
  Size the backing bitmap to the requested viewport. Clear and reconstruct the
  drawing from absolute seconds on every seek, including zero and backward
  seeks; do not accumulate simulation steps or advance hidden mutable state.
- three: import the exact declared local ./assets/three.module.min.js ES module.
  Use its pinned Three.js r170 WebGL2 renderer. Do not import addons, external
  textures, remote modules or another Three distribution. The host supplies
  software WebGL; respect resource bounds and never require a physical GPU.
  Build geometry, materials and lighting once, then assign every animated value
  from absolute seconds and call renderer.render(scene, camera) on each seek.
  Do not depend on prior frames, unseeded randomness or a requestAnimationFrame
  render loop. Project labels deliberately and keep them clear of moving forms.

Optional initialization may set window.__animationExperiment.ready to a Promise.
It must settle before the host calls seek. A seek may return a Promise only for
bounded work that completes that exact frame. Never replace the seek interface
with autonomous playback, timers, workers or active audio/video elements.
