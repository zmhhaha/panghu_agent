---
name: animation-skill-method-laboratory
description: Learn reusable animation Skills from frozen Wiki knowledge and real experiments, then create and evaluate complete expressive samples without inventing success evidence.
---

Read the exact goal, budget context, admitted runtime, frozen Wiki pages and
actual experience records. Treat Wiki conclusions as revisable hypotheses.
Compare contradictory observations and preserve failures as useful knowledge.
Distinguish a reference observation, an attempted implementation, an actual
render, sampled-frame assessment, and explicit developer acceptance.

Plan a complete expression fragment with identifiable subjects, meaningful
changes, purposeful pacing, transitions and optional sound effects. Choose
HTML/SVG, Canvas2D or Three.js for the expressive problem, not a default card
layout. Source and reusable knowledge are different outputs: write source for
this sample and a standard SKILL.md package that teaches decisions and
adaptation across different content and structures. Do not copy one layout
or replace semantic decisions with an enumerated parameter template.

Honor constraints.allowed_engines and assets exactly. HTML/SVG should reveal
relations when needed rather than pre-draw connectors. Canvas animations must
derive their frame from the supplied seek time, not accumulated wall-clock
state. Three.js uses only the admitted local ./assets/three.module.min.js;
do not fetch a CDN. Preserve deterministic seeking, ready state and requested
duration. Never run, render or inspect source here; the trusted renderer does
that later. A successfully authored file is not a successful animation.

The source entry is exactly scene.html; optional source files are scene.js and
scene.css. Never emit index.html or put SKILL.md in source.files. The reusable
Skill package and this scene's source are separate objects. Match the input's
constraints.width, constraints.height, constraints.fps and
constraints.duration_seconds. Preserve nonempty criteria exactly and keep
source.strategy.description and source.strategy.tradeoffs as JSON strings.

The host owns playback. Expose window.__animationExperiment.seek(seconds) and,
when initialization is asynchronous, a ready Promise on that same object.
Reconstruct the whole frame from absolute seconds, including zero, reverse,
repeated and fresh-load seeks. Do not leave state from an earlier frame. Clear
canvas each time; for Three.js set every animated value and render in seek.
Never use timers, requestAnimationFrame, wall-clock state, autonomous CSS/GSAP
playback, network access or active audio/video elements. Purposeful sounds
belong only in sound_events; a request for no sound requires an empty array.

Before calling write, check the result against CONTRACT.md: exact top-level
fields and types, the scene.html entry, only admitted source filenames/assets,
the host seek interface, a complete Skill draft, frozen criteria and the
mode-specific null/empty fields. This is an in-memory contract check, not
permission to execute the scene or claim that a renderer has accepted it.

Use frozen true experiences to improve Wiki principles, applicability and
counterexamples through knowledge_proposals. Do not assert that this task's
new source has been rendered, heard, accepted, transferred or published.
For reference-free transfers apply only the supplied exact Skills to changed
content and structure; do not reconstruct the absent reference or Wiki.
