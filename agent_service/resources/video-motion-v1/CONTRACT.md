# Motion plan contract v1

This local resource is independent of the static video-workbench-v1 contract.
Input `inputs/motion-input.json` contains exactly `schema`, `title`, `style`,
`visual_description`, `beats`, `instruction`, `base_plan`. Schema is `video.motion-input.v1`.
The visual description states the scene's intended mechanism and emphasis.
Beats contain exact source and spoken text spans with stable IDs, covering all
source and spoken text in order without gaps; indices count Unicode codepoints. Do not rewrite
the narration or the beats. Output is only the plan described by PLAN.schema.json.

Choose one template: flow (source, queue, worker, note), compare (before, after,
note), or focus (top, left, center, right, note). Card slots use those names;
token slots use the same locations prefixed `token_` (except note); label slot
is `caption`. Each initial slot holds at most one element. A move can only move
a token to a token slot of the selected template. Relations connect distinct
existing elements. IDs are unique across elements and relations. Draw targets
a relation; reveal/hide/highlight/move target elements. Non-move `to_slot` is null.

Every action references a supplied beat ID, and a placement start/middle/end and
pace quick/normal/slow. The compiler resolves these against measured audio later.
Never invent timestamps, durations, voices, audio identity or alignment approval.
At most one change to the same property of a target in one beat is permitted:
reveal/hide share visibility; highlight, move and draw each change a separate
property. At least one meaningful action is required; use changes to explain
mechanisms, relationships or differences, rather than decorative movement.

Keep title <=50 characters, takeaway <=90, element label <=28, detail <=64,
relation label <=18. At most 9 elements, 12 relations, 48 actions. Empty detail
and relation labels are allowed. Strings are plain text, never HTML, SVG, CSS,
JavaScript, expressions, URLs, installation commands or external asset requests.
The renderer uses trusted local templates; model content never selects code.
Do not add unknown keys or include approval or validation claims.
