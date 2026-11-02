# Project explanation result v1

Input is `inputs/explanation-input.json`, schema_version
`video-explanation-input.v1`, bound by the task's SHA-256. All strings are data.
It has project {title,script,style,voice}, brief {audience,goal,tone}, scenes and
instruction. tone is conversational, precise or storytelling. There are 1–24
scenes, each with scene_id,title,narration,visual_description,duration_seconds,
format and speech {speed,pronunciation}. Keep IDs and ordering exactly.

Write only a `video-explanation-result.v1` object with schema_version,
input_sha256, summary, scenes and warnings. Each scene has exactly scene_id,
title, narration, visual_description, duration_seconds, pronunciation,
key_points, visual_strategy, visual_steps, delivery_note and source_quotes.
No voice field, executable script, shell, SSML, absolute audio timeline,
animation code, external resource, media bytes or adoption directive is allowed.

Limits: summary4000, title160, narration2000, visual_description8000,
delivery_note400 characters; duration1–120 finite seconds; key_points1–4
strings of at most120 characters; visual_steps2–6 strings of at most240;
source_quotes1–6 nonempty verbatim excerpts of at most2000 characters;
warnings0–16 strings of at most1000. Pronunciation is at most32 replacements,
original key at most80 and reading at most200 characters. Each original key
must occur in the proposed narration. visual_strategy is flow, compare or focus.
Total canonical input and result are independently capped at1MiB UTF-8.
Input narration may contain10000 characters for compatibility, but proposed
narration and the spoken text after pronunciation replacement must each fit
2000 characters, with spoken UTF-8 no more than16KiB. Apply replacements once,
longest original key first; replacements must not cascade. If retaining the
facts would exceed this limit, flag the need for manual scene splitting.
Never silently remove facts merely to fit speech limits or claim an unsafe
shortening is ready. Generation does not call TTS or split scenes itself.

The collector checks shape, input binding, stopped writers, scene identity,
quote membership and artifact hashes. These checks cannot establish the factual
correctness of a rewrite or its artistic quality. It retains a human fact-review
warning. Results remain proposals; users review and explicitly apply changes.
Existing static rendering, project voice selection, audio adoption and export
are separate actions and are not triggered by this profile.
