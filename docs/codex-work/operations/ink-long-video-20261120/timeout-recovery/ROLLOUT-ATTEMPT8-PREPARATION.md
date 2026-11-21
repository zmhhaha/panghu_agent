> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/timeout-recovery/ROLLOUT-ATTEMPT8-PREPARATION.md`；原文件 SHA-256：`2076fd389c622a6906dd44f7c84d1d9fd60f5faaa7cfb1b4baec20589a7bcaa7`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

# Trusted 10800-second wall-clock recovery: preparation only

Workspace: `D:/codex-work/operations/ink-long-video-20261120/timeout-recovery`.
The separate request workspace is `../formal/attempt8`. The new committed source
revision is `faaa09affca3205ecfa46b2204f8d6aa2a017362`. New renderer/workbench
digests, admission, consumer, P, C and workbench Pod identity remain null until
actual new evidence exists. No command in this preparation has built an image,
read a remote service, deployed, captured a source, called a model or rendered.

The trusted long-film default changes from 3600 to 10800 seconds. The short-film
default at or below 1440 frames remains 600 seconds; the first long frame is
1441. The user's film remains 300 seconds, 24 fps and 7200 frames. Existing
snapshots and child/journal deadlines retain their original bytes and budgets.
Historical short-snapshot compatibility follows the committed contract; the
new default must not be applied retroactively to any old record.

## Three distinct identities

| Role | Exact material |
| --- | --- |
| Reusable original source A | Run `63961e9b-a7e1-4ad4-86b9-0c8abfa4af50`, all original 12 modules and 13 stopped children; run SHA `e1780cc7…d865`, original snapshot `c23b39f7…fdd3`, A renderer `af8a17ba…9475`. |
| Current deployment before this rollout | B renderer `3ff1908b…175f`, workbench `b63bf207…318a`, policy `dc4f5c18…d3c9`, configuration `ec4505e6…214d`, consumer admission `f93c68a1…8845`. |
| Future target | New `latest@sha256` images, execution admission, consumer, P/C and Pod identity: all awaiting actual proof. |

The original source consumer admission is `daa717fe…57a8`; it belongs to A's
frozen configuration. Current-before consumer `f93c68a1…8845` belongs to B. Both
are retained separately under `rollout-before/`. The future target consumer
must bind the real new renderer release while preserving producer, resource
image, helper bytes and original consumer package. Current B is never substituted
for A in the policy's source side.

v7 run `4fa4c5fc-9640-4020-a21c-88a71369d66a` is retained as history and as a
stop gate. Its actual analysis proves a cancelled child with `stop_proven=true`,
native SIGTERM/exit 1 and resource disappearance, with no OOM observed. The parent
deadline is temporally aligned; the native log does not directly name its caller.
The analysis remains at its original path and SHA `fec40b37…495e`; its bytes are
also copied into the new before folder. v7's mixed target-C run is not the reuse
source. All v7 helpers, intents, partial captures and logs remain untouched.

The derived attempt8 baseline changes only the new request identity, operator
revision, retained history and target slots. Its original A run, packages,
module targets, source bytes, predecessor chain and 13 stop receipts remain exact.
It also pins the original af98 baseline as a separate immutable input; that old
artifact is not renamed or rewritten. The reserved new key is
`ink-quiet-film-20261120-run-v8`; run ID and intent remain null.

## Required operator adaptations

| Operator family | Required behavior in the independent copies |
| --- | --- |
| Build/delta/admission | Runtime agent owns these copies. Build only renderer/workbench with latest tags and real immutable digests. Measure the actual Python timeout module/defaults in each new image. A-to-target evidence retains the original browser-flag delta; current-B-to-target evidence expects unchanged six host files and the reviewed timeout-only Python delta. Runtime JSON's image digest alone does not prove the installed timeout constant. |
| Policy/B36/source operators | Review agent owns these copies. P still scopes original A, original source consumer daa, exact 12 packages and stopped render. P contains no target-C hash; C contains P and is hashed afterward. Fresh target evidence must bind the actual new image. B36 remains technical source reconstruction and makes no production package or film-completion claim. |
| `prepare_rollout.py` | Capture reads all three targets/old CMs and the four protected Deployments/one protected CM into a fresh directory. Preparation uses current-B before reference and old mounted P, rather than requiring current release to equal source A. Validates current C from current B/P/f93 separately. Retargets exactly one existing P ConfigMap volume; preserves the existing readonly single-file subPath and env. Filters preserved P mounts symmetrically when comparing mount lists. Measures both actual renderer and workbench timeout boundaries. |
| `apply_rollout.py` | Fresh original UID/resourceVersion/full-spec tests, new namespace-specific path and plan schema; original UID plus complete desired spec after each rollout. Requires the terminal v7 proof and the prepared trusted-timeout facts. An uncertain apply is reconciled, never replayed. |
| `postflight_operator.py` | New revision and derived baseline; checks current-before protected objects and old CMs, exact actual P/C/B/consumer, 13 original A children and packages, native catalog/worker pins and new installed timeout defaults. Keeps single-file control config mapping and a new output stem for every report/intent/private log set. |
| `readonly_live_source_dryrun.py` | Streams data and the real public-facade definitions through stdin with no operation-tree mount. Uses actual installed assets and exactly six host files. The new potential snapshot uses `render_timeout(7200)==10800`; the old source child's explicit 3600 timeout remains a historical pin. No browser, encoder, production row or write. |
| attempt8 driver/contract/transport | Independent revision/key/state/intent and exact new proofs, with original A as source. Old af98 source custody is pinned by the new derived baseline. Expected target fields remain null. Root review additionally binds timeout facts, before P/C, daa/f93 distinction and v7's terminal evidence. |
| Capture/download/progress | New collector requires the shared connection's already-open read-only transaction; its separate ledger connection retains its own BEGIN. Downloader still owns one separate read-only transaction. No original helper is patched, no second v7 watcher/capture is launched, and frame counts stay unknown until actual media verification. |
| Watch/client budgets | New native follower observes up to 10980 seconds: 10800 execution plus observation/cleanup margin. Existing client code already derives its wait budget from the new snapshot timeout plus 150 seconds. HTTP submission, source capture and media-download budgets are not film execution budgets and are not increased indiscriminately. |

The new helpers are prepared executable copies for later root dispatch. None
has been used to perform its build/prepare/capture/apply/start/download action.
The only validation performed here is local parsing, guarded offline plans,
pure object checks and original-file hashing. Parent operators and source remain
under their assigned owners.

## Files and gates

- `rollout-before.json` describes last verified B state. Its copied Deployments
  are historical reference objects, not a fresh resourceVersion/CAS authority.
- `recovery-plan.prepared.json` and `../formal/attempt8/actual-runtime-inputs.json`
  keep all new runtime identities unfilled.
- `frozen-references.json` pins 135 original files without changing them. Only
  their owners may append separate new evidence; this preparation edits none.
- `helper-copy-provenance.json` records the old operator bytes and new copies.
- `preparation-validation.json` and `final-preparation-manifest.json` record this
  preparation after local validation.

Actual progression requires the real new image measurements and technical
admission, exact new consumer/P/C, fresh before capture/quiet, reviewed full-spec
CAS plan, installed postflight, pure live assembly with 10800 timeout, real client
journal cleanup and final quiet. Only then can root seal and separately dispatch
one v8 standard full12-reuse POST. Budget: zero new authors, zero recordings,
one whole-film render. Nothing here replays the prior POST or extends v7's old
deadline. User authorization is already present; no new confirmation is pending.

The user's result still requires the actual 300-second MP4, full 7200-frame
decode, clock/silence checks and motion/visual review. Sparse stills, stage
labels, resource termination and this preparation establish no captured frames
or artistic acceptance.
