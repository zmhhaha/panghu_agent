# Sampled visual evidence contract

Read the input JSON and every frame through the read tool. A filename or a text
description is not visual evidence. The manifest binds actual PNG/JPEG files to
frame IDs, media SHA-256, times and observation windows. It records sampled
frames, not continuous playback. Audio is unavailable. Report unsampled ranges,
unknown transitions and any uncertainty rather than inventing evidence.

Observe output has exactly `summary`, `candidates`, `limitations`.
Each candidate has `candidate_id`, `title`, `start_seconds`, `end_seconds`,
`frame_ids`, `observable_mechanism`, `adaptation_ideas` (list of strings).
Describe complete expression fragments: how objects, relationships, composition
and changes convey an idea, rather than extracting a reusable fixed template.
Separate directly visible structure from hypothesized movement. Candidate IDs
use lowercase letters, digits, dots, hyphens or underscores and start with a letter.

Review output has exactly `summary`, `issues`, `limitations`.
Each issue has `issue_id`, `severity` (`minor` or `major`), `kind` (`composition`,
`motion`, `continuity`, or `clarity`), `start_seconds`, `end_seconds`, `frame_ids`,
`problem`, `change_target`. Issue IDs follow the same identifier convention.
Give concrete visual evidence and a desired correction, allowing the creative
Agent to choose implementation. Motion/continuity issues cite at least two
ordered frames. Every cited frame time lies inside its issue/candidate interval,
which lies within one supplied observation window.

Never assess an unheard voice, pronunciation, exact speech synchronization, or
whole-video smoothness from sampled stills. Never emit acceptance, user approval,
technical success, executable code, new sampling coverage, or self-assigned
scores. Empty issues or candidates are valid when supported by the evidence.
The host adds source/input identities and preserves all supplied limitations.

The artifact collector can verify supplied frame bytes, not whether the model
actually read them. Its frame_evidence_status is always supplied_only.
observed_frame_ids lists manifest-supplied frame IDs and is not a read receipt.
The business service must separately compare real model-proxy image metadata
and SHA-256 values against this manifest before recording successful observation.
