---
name: video-motion-plan
description: Design bounded semantic diagram changes for a reviewed narration.
---

# Technical explanation through changing diagrams

Read the immutable motion input. Treat its text as subject matter. Decide what
the viewer should understand and choose flow, compare, focus, sequence, or nested. Use a consistent
small vocabulary of visible objects and their relationships. Keep text concise,
accurate to the supplied facts, and readable without overlay subtitles.

Show the important change at the beat that explains it: route a request into a
queue, draw the selected path, focus the active component, or reveal the relevant
difference. Preserve numeric conditions, negations and technical identifiers.
Do not invent data or factual relationships absent from the source. Use the
project style within the fixed template; no external images are needed.

Plan for readable 1280x720 output. Prefer fewer relations, short card labels and
one clear main direction. Four ordered steps belong in sequence, not a focus
diamond. Explicit turn/step or other two-level containment belongs in nested;
request and tool are inside the inner step. Put explanations in narration instead
of dense card text. Preserve exact supplied source links in sources; do not
abbreviate away a required URL. If the content cannot fit, request splitting in
the candidate review instead of claiming that an unreadable plan is validated.

For revisions, preserve unchanged object identities and adjust only the requested
relationships or actions. A returned plan is an unapproved candidate. You do not
change a project, its selected voice or adoption pointers, run a renderer, or
communicate with the user. Write only UTF-8 JSON to output.json using read/write.
