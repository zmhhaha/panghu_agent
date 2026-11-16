# Controlled process candidate contract

This profile is explicitly selected for a process that can be explained using three nodes and one persistent object. It does not replace general flow, comparison or focus diagrams. Never force a topic into this representation by inventing stages, removing facts or duplicating the object. If the supplied content does not fit, stop and request a narrower process through the Agent's needs-input mechanism.

Read the exact immutable beats. Copy their IDs into actions; never invent IDs, rewrite narration, choose a voice or estimate seconds. Output schema video.process-plan.v1 with template flow. Output only the plan, not an envelope.

Exactly three cards occupy source, queue and worker, and exactly one token occupies token_source, token_queue or token_worker. Element IDs must be unique lowercase identifiers. Card labels have at most 18 characters and details 28; token labels at most six, with empty detail. The title has at most 34 characters and takeaway 90. Text must remain plain text without control characters, code, URLs, coordinates or CSS.

Only source-to-queue, queue-to-worker and worker-to-queue relations are supported, at most one per direction. Relations have unique IDs and labels at most 12 characters. Return and retry must reuse the same token identity, with an explicit reverse relation; a second token is never a retry.

Actions: reveal/hide target an element; draw targets a relation; transfer targets the token and includes relation_id; set_state targets a card and includes state. Every action includes beat_id, placement start/middle/end and pace quick/normal/slow. No other action fields are allowed. At most 24 actions; at least one transfer and one set_state are required.

Cards begin idle. State transitions are idle→waiting/running, waiting→running, running→completed/failed and failed→waiting/running. Completed is terminal. State labels and glyphs are compiler owned. A token begins hidden and must first be explicitly revealed. Visibility actions must alternate. Draw each relation only once, before any transfer on that relation. Transfer requires the token at the relation source and token plus endpoint cards fully revealed. State changes must occur while the card is visible.

Choose separate placements or later beats so every reveal/draw finishes before transfer, and reserve a final beat for a readable hold. Do not schedule a relation draw and its transfer at the same placement. One useful sequence is first beat reveal cards/start, reveal token and draw entering relation/middle, transfer/end; later beats draw/start then transfer/middle and change state/end. A retry can reuse a drawn relation on a later beat. Semantic beat anchors are relative planning intent; actual audio alignment and human timing verification happen later.

Candidates require explicit user adoption. Producing valid data grants no permission to render, publish, alter project narration, choose per-scene voices, add subtitles or approve quality.
