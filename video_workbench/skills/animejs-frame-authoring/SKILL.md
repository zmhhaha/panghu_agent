---
name: animejs-frame-authoring
description: Compose expressive SVG/HTML, Canvas or Three.js motion with pinned Anime.js timelines driven by the HyperFrames film clock, including stagger, path motion and reconstructive seeking.
---

Anime.js interpolates values and composes time; it is not a drawing engine. Choose HTML/SVG, Canvas or Three.js from this task's admitted engines, then use Anime.js only where it makes the intended expression easier to author. Design the composition and motion for the actual explanation; no fixed scene recipe is required.

Use this capability only when the task admits all three local assets: `assets/anime.esm.min.js`, `assets/anime_timeline.mjs` and `assets/animejs.LICENSE.txt`, at the pinned Anime.js version 4.5.0. Asset presence in a Skill does not grant runtime admission. Otherwise use the admitted libraries or pure frame math.

Import `createFrameTimeline`, `stagger`, `svg` or `eases` from `/project/assets/anime_timeline.mjs`. The adapter provides real Anime.js with its automatic main loop disabled. It exposes timeline `add`, `label`, `renderAt` and `dispose`; positions and animation durations are **milliseconds**. Keep total animation duration within the assigned frame window.

In `mount(root, context)`, create the scene's objects within `root`, give tweened properties explicit start/end values, then build a paused timeline with `fps:24`, `startFrame:context.start_frame`, `durationFrames:context.end_frame-context.start_frame`. Pass scoped DOM element references or state objects to `add`; global selector strings are rejected. Label positions are milliseconds. Stagger and timeline offsets express causal order, anticipation, grouping and emphasis; choose their values from this explanation's timing rather than adding movement everywhere.

In `renderAt(context)`, call the timeline's `renderAt(context)`. It computes local milliseconds from the host's `context.global_frame` and exact rational `context.time`; it never accumulates elapsed time. Then redraw Canvas or render the Three.js scene from the tweened state. For SVG/HTML, direct element-property animation is available; path reveal can use `svg.createDrawable`, and motion-path values can come from `svg.createMotionPath`. Keep stable geometry separate from the changing reveal or transform. Do not depend on previously visited frames.

Keep ownership of animated state clear. Initialize timeline-owned properties once and give their tweens explicit start/end values. Do not reset or overwrite those properties before or after every seek: Anime can skip writing a cached, unchanged or completed tween, leaving your external reset visible and making the same frame depend on its history. Derive properties not owned by the timeline directly from the absolute frame, and project tweened state into the drawing after seeking. Compare a middle frame reached sequentially, directly from zero, backwards from the end and twice in succession; a plausible forward playback alone cannot detect this failure.

For an interpolated state, the lifecycle binding has this shape:

```js
import {createFrameTimeline} from '/project/assets/anime_timeline.mjs';
let motion, state;
export function mount(root, context) {
  state = {progress:0}; // Derive the actual scene and object state freely.
  motion = createFrameTimeline({fps:24, startFrame:context.start_frame,
    durationFrames:context.end_frame-context.start_frame});
  motion.add(state, {progress:[0,1], duration:500, ease:'inOutQuad'}, 0);
}
export function renderAt(context) {
  motion.renderAt(context);
  // Draw the designed scene from state.progress, including its exact initial state.
}
export function dispose() { motion?.dispose(); motion = state = null; }
```

Avoid `play`, `resume`, `autoplay`, engine updates, WAAPI, scroll/drag observers, timers and your own animation-frame loop. No second clock, external CDN, audio playback or late network resource is needed. The adapter suppresses event callbacks when seeking and rejects callback parameters; draw from state after seeking. A nonlinear easing can be used, but do not use unseeded randomness or callbacks that increment state. Build the full timeline before the first render; dispose it before removing the mount root, and also dispose Three.js geometry/materials/textures when used.

Verify first/last frames, multiple out-of-order seeks, the same frame twice and the same frame after an idle interval. Frames must agree regardless of visitation order; no RAF may remain pending. For Canvas/Three.js, native contexts and visible output must come from the declared engine. These checks establish deterministic execution, not visual quality or publication approval. Compare the complete expressive segment against its goal before proposing a reusable expression Skill.
