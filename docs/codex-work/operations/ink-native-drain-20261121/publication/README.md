> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-native-drain-20261121/publication/README.md`；原文件 SHA-256：`bbae3a718875f04cb5b45184dcab01d4cb69a126a529f24e731064473a3eff54`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

This directory prepares publication request `be53e2a8-e990-41bc-a384-0b89e9966b70`
for owner `27714443`. No helper has submitted it, changed a deployment, activated
a binding, cleaned recovery flags, or copied a formal release.

`request.json` reuses the fifth request's exact 3,197-character note. Its mention
of the fifth attempt is retained historical text; this new UUID identifies the
sixth request. It uses only the original fourth failed draft, SHA `ce16fdab…`,
through the same existing private manifest `1d5930bd…`. No corrected draft or
fifth candidate becomes a source. The independently reproduced adapter drain
defect is fixed at source revision `e4fc65cf…`; the exact fifth failure cause
remains unproven.

Before any submit, root reviews both genuine admission receipts and the actual
combined CAS/live verification, then creates `activation-reviewed.json` using
`activation-reviewed.TEMPLATE.json`. The template is deliberately unusable.
Record the actual worker digest, both exact binding SHAs, verification time,
and absolute paths plus raw SHA-256 hashes for the three actual receipts.
The module profile remains `video.animation-film-module.v1`, revision `1`;
"module-v2" describes its admitted instruction/tool revision.

After explicit root authorization to submit, run from this directory:

```powershell
python -B publication.py preflight
python -B publication.py submit
python -B publication.py status
```

Submit repeats the preflight immediately before its sole POST. It verifies live
ready compiler/module binding and worker digests against the reviewed receipts,
using creation's method credential for the compiler and the dedicated
`FilmModuleAgentSettings.from_env()` credential inside the Ready workbench pod
for the module. Tokens remain in their original pods and caller grants stay as
configured. Both capability probes repeat after source capture. It also checks
the fifth failed/stopped job, absence of pending publications, all 32 exact fixed
source hashes, and the same original fourth-draft manifest/provider. The capture
must still have 1,030 original records plus that one draft at index 1,030, unchanged
original addresses, 1,032 input files, and a total below 32 MiB. No snapshot is
persisted by preflight. A changed capture or binding stops before POST.

`submission-intent.json` is created exclusively and fsynced before POST. A lost
response is reconciled by GET of this UUID using `status`; a subsequent 404 is
an uncertain submission, never permission to send another POST. No helper
automatically retries a failed publication or creates a seventh request.

While it runs, `read_control.py` and `read_model_stage.py` inspect only this
request's actual job/attempt. Use the separately prepared task diagnostic
capture operator as soon as an actual worker Pod appears. It is not started by
these helpers. A failed job must obtain matching positive native stop proof;
then `preserve_failed.py` can preserve original SSE/write proposals without
normalizing them, publishing them, or adding them to any source provider.

After actual publication, run `python -B audit_release.py`. It verifies the exact
new binding/native stop, active release pointer, immutable snapshot and all 32
hashes; exports the actual published packages into `packages/`; and writes
`coverage-audit.json`. Review those actual package contents and create
`manual-scope-reviewed.json` using its deliberately false template. Root then
performs the separate cleanup CAS and waits for the Ready rollout and actual
cleanup verification described below. Copy that remote opt-out operator's exact
`verified.json` to this directory as `cleanup-verified.json`.

Only after audit, actual content review and verified cleanup run
`python -B finalize.py`. It rejects an absent or partial cleanup receipt, a
different request/publication/release ID/SHA/sequence, changed 32 source hashes,
or missing capability/other-deployment/private-evidence preservation checks.
It checks complete dependency descriptors and copies the exact audited new
release to the existing driver's original path:

`D:/codex-work/operations/ink-long-video-20261120/publication-final-release.json`

It also writes the matching audited summary there. Both formal aliases remain
unavailable until cleanup verification passes. Existing conflicting files are
rejected. Failed candidates can never satisfy finalize's publication gate.

`cleanup_recovery_optin.py` is a separate copy for this request. Do not execute
any mode now. After the actual publication is created, `status` records its
`publication-identity.json`; upload that identity and the cleanup copy to:

`/root/video-releases/ink-native-drain-20261121/publication/`

Cleanup preparation later requires actual publication, completed/stopped native
job, the reviewed release ID/SHA, no active jobs or business/render work, and
matching runtime capabilities. It prepares the exact three recovery flag/value
removals using fresh UID/resourceVersion/full-spec CAS. Root must review that
concrete plan before its separate one-shot apply. Verification retains all
private evidence, all other arguments, deployments, capabilities and source
hashes. The actual new receipt includes this request/publication identity,
release ID/SHA/sequence and the 32 fixed source hashes. Its remote path is:

`/root/video-releases/ink-native-drain-20261121/publication/recovery-optout-be53e2a8/verified.json`

After copying that exact receipt to `cleanup-verified.json`, finalize can enable
the original formal driver aliases. The original operator hard-bound to the
fifth request is untouched.
