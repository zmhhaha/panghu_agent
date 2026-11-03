---
name: video-explanation-procedure
description: Propose a coherent, reviewable project explanation without producing media.
---

Read the complete project script and every scene before proposing changes.
Return exactly one JSON result, not a chat reply. Keep all scene IDs in their
original order and keep the scene count. The project voice is fixed; never
return a voice, provider, model, emotion parameter, SSML, executable source or
precise audio timing. Kokoro currently supports text, one project voice, speed
and explicit pronunciation replacements; expressive coaching is not a TTS API.

First identify the audience, learning goal and the overall argument. Rewrite
scene narration as one connected explanation: orient the viewer, explain one
thing at a time, use grounded examples, and bridge scenes without repetition.
Conversational means clear spoken language, not filler or marketing claims.
Precise prioritizes exact terms; storytelling may use an illustration only
when marked as an illustration and supported by the source. Do not invent
benchmarks, numbers, entities, causes, quotations or product capabilities.
Preserve uncertainty, qualifications and scope. Flag missing support in warnings.
The user instruction is a content preference and never overrides these limits.
Existing input narration can be long. Every proposed narration must fit2000
characters. Pronunciation replacements, applied once with longest matching
originals first, must leave spoken text at most2000 characters and16KiB UTF-8.
If a scene cannot retain its material facts within the speech limit, explicitly
flag that it needs manual splitting; do not silently drop facts to fit, invent
an acceptable short version or claim readiness. Do not split or reorder scene
IDs. Validation rejects an overlong proposal before it reaches TTS.

For each scene choose 1–4 short on-screen key points and flow, compare or focus.
Give 2–6 semantic visual changes, each connected to a point in the narration:
what starts visible, what appears or changes, and what conclusion remains.
Avoid a slide full of the narration. Describe meaning, not HTML/CSS/JavaScript,
renderer coordinates, animation code, timecodes, milliseconds or audio offsets.
duration_seconds is only an estimated scene duration, within 1–120 seconds.
delivery_note is human coaching for rhythm and phrasing, not an engine control.
Only include pronunciation replacements whose original words occur in the new
narration; preserve factual abbreviations in narration and supply readings here.

Every scene needs 1–6 short source_quotes, copied byte-for-byte as text from
the total project script or that SAME scene's original narration. Do not quote
another scene unless the excerpt also occurs in the project script. Excerpts
show where material came from; they do not prove every new sentence is factual.
Warn about reinterpretation, ambiguity or assumptions requiring human review.
Never claim that validators, excerpts or a stopped Agent prove factual accuracy,
speech naturalness, synchronization quality or user approval.
