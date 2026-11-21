> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/timeout-recovery/TIMEOUT-POLICY-OPERATIONS.md`；原文件 SHA-256：`d0249cc3a56a356827e2015f19b045d4483329f1b7d11710f5f63428376f4c34`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

This is preparation for clean source revision
`faaa09affca3205ecfa46b2204f8d6aa2a017362`, parent `ac7f702…`.
No new-target image, admission, P/C, capture, probe, build, deployment or POST
has been created by these operators. Unknown target fields remain null.

The original author material and current deployment are separate:

| Scope | Exact identity |
| --- | --- |
| Original source A | Run `63961e9b-a7e1-4ad4-86b9-0c8abfa4af50`; af8 renderer; c23b snapshot; original 12 packages and 13 stopped children; consumer `daa717…`; original child timeout 3600. |
| Current before release | Renderer `3ff1908…`; workbench `b63bf207…`; execution admission `cde3e48…`; policy `dc4f5c18…`; configuration `ec4505e6…`; consumer `f93c68…`; historical long budget 3600. |
| Future timeout target | Image/admission/policy/configuration identities unknown. Required committed source is faaa, actual imported long budget 10800, short budget 600, duration 300 seconds / 7200 frames. |

`source-A-scope.json`, `current-before-scope.json` and
`new-target-requirements.prepared.json` keep these facts separate. The copied
`.sealed.json` files retain their original bytes; `original-copy-provenance.json`
pins every source/copy hash. Old files were not modified. The source-A capture
directory remains the original private source capture outside Git and images.

`long-timeout-source-validation-receipt.json` records the actual earlier 9-pass
boundary run and 66-pass / 1-skip snapshot/execution/adapter run, their original
command summaries, the committed faaa source/test hashes, and six unchanged
original source-A/v7 capture hashes. Full standalone stdout was not previously
saved; the receipt says so explicitly. Old A and v7 snapshots still decode with
their frozen 3600 values and original deadlines. The new source cap changes no
old snapshot or record.

`timeout_source_runtime_policy_ops.py` was copied under a new name and adapted
without changing the earlier operator. Its prepared input is
`timeout-policy-inputs.prepared.json`; current/source proofs are populated while
all actual new-target proofs are absent. `prepare` refuses absent proofs before
creating P/C. It requires these independent actual measurements:

- `source_a_measurement`: real af8 image, original source fingerprint/hosts and
  imported long cap 3600.
- `a_measurement`: current-before 3ff image, all six CPU browser hosts and cap
  3600, matching the sealed current P/C.
- `b_measurement`: the actual new renderer, cap 10800 and short boundary 600.
- Current and target workbench measurements with the same imported limits.
  Target renderer and workbench module source bytes must both equal the reviewed
  faaa limits file SHA `5e4fb1a7a868089bf2b089db6a4fd1f6bbfa06c0886c4ff98caf193589e89aa6`.

The new runtime comparison is **current 3ff → future target**: host differences
must be empty; fingerprint differences contain only image digest; all assets,
licences, capability fields and publication files remain equal. Product-file
drift is restricted to the trusted limits file and the reviewed timeout doc.
Separately, original af8 → future target must still have exactly the original
reviewed CPU Canvas2D flag/comment browser delta. Five other hosts stay equal to
source A. The final production P remains original A → new target, never a chain
through the failed v7 run.

The future Manim replay starts from the current f93 admission. Its report's
`original_admission_sha256` must equal f93. The policy's source consumer remains
the original daa admission and full A consumer context. Source/current/target
producer image, producer requirement, helpers and original consumer package /
binding must match; only the exact formally admitted releases and admission
hashes change. Internal admission SHA and raw file SHA remain separate.

All new technical execution admission, 36-point replay, consumer report and
original media/capture/result evidence must exist and reopen by exact raw SHA.
No old B3ff receipt is accepted as a new-target receipt. P contains no target C
hash: P is built first, C contains P, and only then C is hashed. Extra current
before context belongs to operator inputs/reports, not to invented P fields.

Future `dryrun-capture` requires all nine actual admitted input assets from the
new renderer, not just the two used assets in the old project snapshot. It uses
the new installed trusted `render_timeout(7200) == 10800` for its potential
target snapshot. The old source child/snapshot timeout 3600 is only a historical
pin and is never copied as the new execution budget. Source modules, assets,
clock, targets and all old receipts stay unchanged. `dryrun-live` uses the same
10800 rule and remains read-only; the attempt8 owner prepares the Pod-stdin
variant, so no host evidence volume should be added to a Deployment.

`timeout_b36_source_replay.py` is also prepared only. Its future `capture`
requires a real new image at faaa, the actual new delta and imported 10800 code.
`--execution-admission` is mandatory and must identify the new target image,
fingerprint and hosts; the current cde admission cannot satisfy that check.
It rejects current 3ff as the target. It directly compiles original A module /
transition / declared asset bytes under the new real runtime and unchanged CPU
hosts into a fresh technical identity, without P or production package intake.
Its sparse technical snapshot keeps a separate 900-second wall bound; this is
not the 10800-second production render request. The proof records both scopes.
The native driver has no launcher wrapper or override and still retains all 36
fresh frames, idle double-image equality, core/source/clock binding, PNG hashes,
cleanup and exact container identity. A subsequent pure read-only validator has
no browser/encoder/network. Its DAC_OVERRIDE capability only reads uid10001
private evidence through read-only mounts; it changes no original permissions.

Future actual execution requires root's separate staged authorization and new
actual proof paths. There is no launcher in this preparation that substitutes
current release identities for the unknown target or submits a production run.
Runtime owns build/delta/technical-admission files; the other owner handles
rollout, attempt8 and its live inputs. This preparation does not edit those
operators or manifests.
