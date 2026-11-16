# Lab scene artifact contract

Input: the exact frozen `inputs/animation-experiment-input.json` whose SHA-256 is
named in the task. It contains a neutral capability goal, bounded constraints,
available local assets, a prompt revision and creative guidance.

Output: write `output.json` with exactly these fields:

- `engine`: `html-svg`, `canvas` or `three`, within any input engine allowlist.
- `strategy`: `description` and `tradeoffs` strings describing actual choices.
- `files`: complete UTF-8 text for required `scene.html` and optional `scene.js`
  and `scene.css`. Do not output any other file path or embed library copies.

Use `./scene.js`, `./scene.css`, and declared `./assets/<filename>` resources.
Expose `window.__animationExperiment.seek(seconds)` once initialization is ready.
The function synchronously resolves the complete actual scene from absolute
time, clamps to the requested duration, and can reset and seek in any order.
There is no autonomous animation clock, network, runtime package installation,
business data access or browser storage.

For persistent HTML/SVG probe objects, use the input object ID as the actual
visible node's `data-object-id`; keep the same identity and label during motion.
Object declarations and generated self-checks are not trusted observations.

The service collects only stopped, input-bound artifact bytes. It creates file
digests and the result envelope independently. Collection does not execute or
sanitize generated code and does not prove successful rendering. A separate
bounded isolated job renders it; the authenticated lab receives media/evidence,
never executable untrusted scripts in its trusted same-origin preview.
