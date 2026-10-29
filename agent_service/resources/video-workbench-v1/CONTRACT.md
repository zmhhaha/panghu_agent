# Video workbench contract, revision 1

Read inputs/video-input.json as immutable UTF-8 JSON. The task contains only its
SHA-256. Each run performs exactly one stage and writes only output.json in the
workspace root. The controller creates video-result.json after all writers stop.

For storyboard output, use exactly:

    {"schema_version":"1","scenes":[{"title":"Opening","narration":"Supplied facts.","visual_description":"Three labeled rectangles connected by arrows.","duration_seconds":10,"format":"svg"}]}

Use 1..max_scenes scenes, never more than 12. A scene title is at most 300
characters; narration and visual_description are each at most 10,000 characters.
duration_seconds is a finite number from 1 through 120. format is svg or html.

For scene output, use exactly:

    {"schema_version":"1","format":"svg","source":"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1280\" height=\"720\"><rect width=\"1280\" height=\"720\" fill=\"#101827\"/><text x=\"80\" y=\"100\" fill=\"white\">Title</text></svg>","summary":"A title on a dark background."}

source is at most 128 KiB of UTF-8. summary is nonempty and at most 4,000
characters. Return the requested format. Do not add wrapper keys, Markdown
fences, provenance, tool output or validation claims.

Sources are static visuals with a conservative element/attribute allowlist.
SVG permits shapes, text, groups, gradients, clipping, masks, local fragment
references and basic filters. HTML permits structural/text/table elements,
inline SVG, and inline style attributes or style elements. Close non-void HTML
elements explicitly. No script, event handlers, foreignObject, iframe, object,
forms, links, media, image elements, external resource references or processing
instructions. SVG must have the standard SVG namespace or no namespace.

CSS permits ordinary static layout/typography/color properties, including grid
and flexbox. No at-rules, imports, escaped tokens, resource functions or browser
extensions. CSS url() is permitted only for a local #fragment. Avoid backslashes
and angle brackets in CSS. Use system font families; do not load web fonts.
