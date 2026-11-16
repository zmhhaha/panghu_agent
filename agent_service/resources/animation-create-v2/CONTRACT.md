# Complete expression fragment creation v2

The task is an arbitrary complete expression fragment, not a fixed capability
quiz, preset layout, eight-second sample or compulsory HTML/SVG template. The
Agent chooses and writes the composition, visual metaphor and implementation
within declared constraints and available local assets. HTML/SVG, Canvas and
Three are supported when allowed by the input; Three requires its declared
local module. No executable source is trusted merely because it was generated.

Read animation-create-input.json. On a new creation, base_source and review are
null. On a revision they bind exact previous source bytes/version and an
independent review of that render. Make one visual correction attempt guided by
its evidence. Preserve confirmed text, narration, duration and unrelated
successful expression. Never launch another review or revision round.

Write exactly {engine, strategy: {description, tradeoffs}, files} to output.json.
Files must contain complete scene.html and optionally scene.js and scene.css.
Use local relative assets only, no network, workers or browser storage. No
library embedding, unknown files, Markdown, self-assigned passing or acceptance.
The host computes all hashes, validates the artifact envelope and separately
runs the source in a confined renderer.

The scene exposes window.__animationExperiment.seek(seconds) on readiness.
The host owns time. Every seek synchronously reconstructs the entire visible
state from absolute clamped time; reverse seeking and seek(0) after the end must
reproduce the original state. Canvas/Three seek must draw the actual scene.
No requestAnimationFrame, autonomous CSS animation, timers, or self-running GSAP.
Respect input dimensions, avoid body margins and scrollbars, and initialize at 0.
