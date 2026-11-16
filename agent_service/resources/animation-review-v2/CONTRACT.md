# Sampled sequence review contract

The following is an inline contract, not a file you need to locate. Read the
input JSON and every declared frame through the read tool. A filename or text
description is not visual evidence. The manifest binds actual PNG/JPEG files to
frame IDs, media SHA-256, supplied times and observation windows. It records
sampled frames, not continuous playback. Audio is unavailable. Report unsampled
ranges, unknown transitions and uncertainty rather than inventing evidence.

This role returns exactly `summary`, `issues`, `limitations` in output.json.
Each issue has `issue_id`, `severity` (`minor` or `major`), `kind` (`composition`,
`motion`, `continuity`, or `clarity`), `start_seconds`, `end_seconds`, `frame_ids`,
`problem`, `change_target`. Issue IDs use lowercase letters, digits, dots,
hyphens or underscores and start with a letter. Return no more than 12 issues.

Give concrete visible evidence and a desired correction, allowing the creative
Agent to choose implementation. A still image can establish local composition
or text readability. Claims about a changing relationship, movement or
continuity require at least two ordered samples. Cite the actual frame IDs,
including an intermediate sample when it establishes the problem; do not
report only the final composition. Every cited time lies inside the issue
interval, and that interval lies within one supplied observation window.
Supplied times need not be verified source-frame PTS; preserve that limitation
when stated. Do not infer a collision, exact duration, velocity or continuously
safe path from missing samples.

Never assess unheard voice, pronunciation, exact speech synchronization or
whole-video smoothness from sampled stills. Never emit acceptance, user approval,
technical success, executable code, new sampling coverage, self-assigned scores
or extra output fields. Empty issues are valid when supported by the evidence.
The host adds source/input identities and preserves all supplied limitations.

The artifact collector verifies supplied frame bytes, not model understanding
or whether the model actually read them. Its frame_evidence_status stays
supplied_only. observed_frame_ids lists manifest-supplied frame IDs and is not
a read receipt. The business service must separately compare real model-proxy
image metadata and SHA-256 values to the manifest before recording transport.
