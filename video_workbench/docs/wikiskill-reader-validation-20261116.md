# WikiSkill compilation: bounded canonical-input reader

The stopped compiler job `b5e91172-73c0-4198-b564-ede85ac4538d` never wrote
`output.json`. Its recorded model responses show two builtin `read` attempts
against a 147,374-byte single-line canonical manifest, followed by a truthful
statement that the builtin 50 KiB line limit prevented reading it. The
manifest has 502 record references, including 53 single-line records larger
than 50 KiB. This was an input-access failure, not a completed compilation
rejected for quality. This repair does not change that job or resubmit it.

## New execution identity

`video.animation-wikiskill-compile.v2`, profile revision `1`, uses the new
`animation-wikiskill-v2` procedure and the exact tool set
`read_compilation_input`, `write`. The v1 profile, procedure, inputs and
historical bindings remain unchanged. Input/result document schemas remain
v1; v2 inherits the existing canonical-byte, complete-file-set, envelope,
writer-stop and result checks. The business gateway selects the new profile.

The fixed extension is image owned and SHA pinned. Its environment identity
binds extension bytes, exact tool scope, worker image, capabilities and runtime.
Candidate admission checks those same bytes and uses a new v2 acceptance suite.
The new combination needs fresh admission before activation; old acceptance
does not authorize the new image/profile/environment.

## Reader behavior

- Every call requires the frozen task's complete manifest SHA-256.
- Manifest pages include original header/index metadata, at most 128 requested
  records, an explicit next offset and a 32 KiB response-text bound.
- Initial record reads can be grouped in explicit batches of up to 32
  manifest-listed names. The response reports unreturned names and incomplete
  files explicitly. Large files continue through UTF-8 byte ranges.
- File access is limited to the fixed inputs root and exact sequential record
  paths in the verified manifest. Whole-file hashes and sizes are verified
  before returning any range. Links and changing file/directory identities
  are rejected. UTF-8 boundaries preserve exact source bytes.
- There is no shell, network, arbitrary path, code execution, input rewrite or
  unbounded dump. The original canonical JSON and its identity stay intact.

## Offline validation

The actual failed-job manifest and one 462,002-byte record were exported with
read-only SQL, then exercised locally with the real Node extension. All 502
manifest rows were preserved across five bounded pages. The large record
reassembled from 29 ranges to its exact original bytes and SHA-256. No model
request, database write, compilation retry or publication was performed.

Focused regressions cover large single-line input, manifest pagination,
explicit record batches, reply limits, UTF-8 reassembly, invalid offsets and
paths, missing records, changed hashes, hardlinks, cancellation, fixed extension
integrity, environment scope, production/candidate factory loading, runtime
token bounds, original collector behavior and preservation of v1 contracts.
Catalog/acceptance regressions cover all three roles (v1 compiler, progressive
planner, v2 compiler), source bundle capture and disabled additive preparation.

## Output-size limit

The 502 real record IDs contain 24,640 UTF-8 bytes. A minimal complete reference
list is 26,169 bytes / 13,456 o200k tokens. A hypothetical compact per-record
exclusion layout is 48,729 bytes / 17,970 tokens, or 19,987 tokens inside the
`write` call's escaped JSON arguments. These are size estimates, not authored
lessons or evidence that any source was read.

Only the v2 compiler therefore requests up to 32,768 output tokens, within the
existing model proxy ceiling; old profiles retain their 8,192 setting. The
procedure asks for compact focused Skills, brief concrete exclusions and no
unnecessary repeated long IDs. Roughly 12,781 tokens remain after the compact
exclusion argument example; this is sufficient headroom for a compact result,
not a guarantee that arbitrary expansive prose or all source text will fit.
No larger output limit or unbounded automatic retry is introduced.
