# Complete-input sampled sequence review contract

This is an inline contract, not another file to locate. Call read_inputs once
with no arguments. It returns the complete manifest text and every original
PNG/JPEG in one tool result. Compare the returned ordered evidence together,
then write one opinion. Only read_inputs and write exist; individual frame
reads, shell commands, paths supplied as arguments and partial substitutes are
unavailable. A source description or contact sheet is not the original evidence.

The manifest binds PNG/JPEG bytes to frame IDs, media SHA-256, supplied times
and observation windows. Coverage remains sampled frames, not continuous
playback; audio is unavailable. Preserve supplied limitations and report gaps,
unknown transitions and unreadable evidence rather than inventing observations.

Return exactly `summary`, `issues`, `limitations` in output.json. Each issue has
`issue_id`, `severity` (`minor` or `major`), `kind` (`composition`, `motion`,
`continuity`, or `clarity`), `start_seconds`, `end_seconds`, `frame_ids`, `problem`,
`change_target`. IDs start with a letter and contain lowercase letters, digits,
dots, hyphens or underscores. Return at most 12 consolidated issues.

Use concrete visible evidence and a desired correction. A still image can
establish local composition, clipping or readability. A changing relationship,
movement or continuity needs at least two ordered samples in one window. Cite
actual frame IDs and an intermediate sample when it establishes the problem.
Every cited timestamp must lie inside the issue interval, and the interval
inside one supplied observation window. Preserve any limitation on source PTS.
Do not infer exact duration, velocity, collision or a continuously safe path
from gaps. Check moving objects and fixed explanations together; visual
proximity by itself is not a defect.

Never assess unheard voice, pronunciation, exact speech synchronization or
whole-video smoothness. Never return acceptance, user approval, technical
success, executable code, new coverage, self-assigned scores or extra fields.
Empty issues are valid when justified. The host adds immutable input/source
identities and preserves all supplied limitations.

The collector checks supplied image bytes, not understanding or actual reads.
Its frame_evidence_status remains supplied_only, and observed_frame_ids is the
manifest list, not a read receipt. The business service must independently
compare real model-proxy image metadata and SHA-256 values against every
expected image before recording complete transport. Complete-input transport never
waives that gate or the single-review / at-most-one-revision budget.
