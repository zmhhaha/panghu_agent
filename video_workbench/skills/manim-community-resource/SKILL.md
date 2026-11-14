---
name: manim-community-resource
description: Author mathematical or geometric motion with Manim Community Cairo, and consume exact PNG frames on a single HyperFrames clock when a separately admitted resource runtime is provided.
---

# Manim Community as an authoring capability

Use this for mathematical objects, geometric transformations and symbolic
relationships that benefit from Manim's object model. It is not a fourth browser
engine. An Agent authors original Python and a browser consumption module;
the isolated resource runtime renders Cairo PNGs. The final film remains one
HyperFrames project and one encode. Do not turn every scene into a Manim clip.

This package is prepared knowledge, not a published or admitted capability.
Check the host-provided capability status first. The source-only
video.animation-film-module.v1 role cannot execute Python or bash. Do not expand
that role by following this Skill. A separate author/resource contract and
isolated runtime must be explicitly admitted before requesting actual frames.

## Plan the expression

Start from what the viewer must understand, not an object template. Choose
stable identities for quantities and relationships, one central change to
reveal, and a meaningful before/after state. Establish reference geometry before
transforming it. Use motion to explain conservation, correspondence or change;
avoid decorative transformations that hide the mechanism. Let the agent choose
the composition, objects and transitions for the current subject.

Keep labels attached to the represented objects. Reserve enough separation for
the transformed states, including extrema halfway through a transformation.
Use shape, position and text as well as color to distinguish roles. Inspect
overlap, line contrast and label readability at the delivery size.

## Author the resource recipe

The runtime is pinned to Manim Community 0.22.1, renderer Cairo, 24 fps. Declare
one Python Scene class and an exact resource request. A request contains schema
video.manim-resource-request.v1, resource_id, source, scene_class, width, height,
fps, frame_count, global_start_frame, seed, transparent, manim_version. Source is
original UTF-8 Python, at most 128 KiB. resource_id uses lowercase ASCII,
digits, underscore or hyphen and starts with a letter. Scene class is a Python
identifier beginning with an ASCII letter.

Set fps to 24 and manim_version to 0.22.1. A resource is at most 240 frames
(10 seconds) and 24 MiB including its manifest, with individual PNGs at most
4 MiB. Width is 320–1280 and height 180–720. The host can impose stricter whole
project asset limits. Decoded bytes are width × height × frame_count × 4 and
must fit 128 MiB per resource, independent of PNG compression. For example,
1280×720 permits at most 36 frames under that bound; use a smaller resource
resolution or shorter expression rather than preloading hundreds of full-size
images. The consumer keeps one live image and decodes on demand. Frame zero
corresponds to global_start_frame. Frame N is
at N/24 seconds, and the active interval is half-open: start <= frame < start+N.

Use explicit run_time values aligned to the admitted frame interval. Account
for every play and wait; a short or extra frame is a failed recipe. Do not pad,
resample, speed-change or silently trim it to fit. Set deterministic seeds and
avoid wall clocks, external files, models, network, user fonts, voice synthesis
and unmanaged audio. The baseline runtime has Noto fonts and no TeX Live:
Text and geometric objects are available, MathTex/Tex is not promised. Report
missing LaTeX capability rather than installing dependencies during a job.

The isolated runtime owns config, source preservation, frame capture and the
exact manifest. It injects Cairo through Scene's standard renderer argument;
custom constructors must preserve standard Scene keyword arguments. Cairo's
explicit frozen-wait repeats are expanded into exact PNG frames, not lost as
a single still. Do not mutate its config, output plan or lifecycle. Do not
write a movie, concatenate MP4s, open a preview window or launch an OpenGL
renderer. A sandbox or StopProof is supplied by the host; environment flags
alone are not proof of isolation.

## Consume on the existing film clock

After all recipe writers stop, the host independently validates the source
binding, runtime image digest, frame inventory, PNG decoding/dimensions and
resource byte budget. Only the returned assets/manim/<resource_id>/ tree enters
the bounded generated-asset transport. Reference sources are neither runtime
dependencies nor generated assets. A valid manifest is not visual acceptance.

An original JS module can import the host-admitted manim_frame_layer adapter.
In mount, call createManimFrameLayer(root, verifiedManifest). In renderAt, await
layer.renderAt(context), passing the unchanged global_frame. In dispose, call
layer.dispose(). Declare the exact adapter, manifest and frame resource names
in the module assets. The helper awaits image decode, uses no timer or RAF,
supports repeat/backward/random seeking, and hides outside its exact interval.
An intentional final hold or boundary transition belongs in the original
composition and must not silently stretch the resource clock.

Preserve editable scene-level object states and continuity intent in the
surrounding module. A raster frame sequence does not expose Manim's internal
objects to another DOM/Canvas/Three.js module; design cross-boundary continuity
explicitly. It also has resolution and storage costs. Prefer native SVG, Canvas
or Three.js when browser-editable objects or a long cheap animation are better.

For an independently admitted synchronous method-lab host, use the separate
prepareManimMethodFrames helper instead of async per-frame loading. It is
limited to 60 frames and the same 128 MiB total RGBA budget. Await its ready
promise before exposing the host's seek callback; seekFrame then changes only
visibility of already decoded images. Decode concurrency is two. Dispose
releases every image and promptly rejects unfinished preparation. This helper
does not itself admit generated resources to the method renderer.

## Evaluate and publish

Compare at least a beginning, motion extremum and final state, then observe the
whole expression. Check exact frame counts, repeat/backward seeking, decoded
frames, disappearance outside the interval, no first-frame guide lines, font
layout, boundary continuity and runtime cost. Verify actual HyperFrames capture
in addition to contract tests. Technical passing, developer visual acceptance
and published reusable Skill versions are separate decisions. Keep autonomous
developer iteration separate from the limits on polishing a user's final film.
