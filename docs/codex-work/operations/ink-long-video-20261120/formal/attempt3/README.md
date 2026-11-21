> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt3/README.md`；原文件 SHA-256：`7bfba30d4a53df0c42bfb94f2e760c1f59fbe53235aa0bbb61b2a647bdc1bd86`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

This is the separate third standard-run preparation for the existing project, adopted 12×25-second plan and frozen WikiSkill release. The new key is `ink-quiet-film-20261120-run-v3`. It will author all twelve modules with new identities after the real typed-v3 worker admission and deployment have been verified. It does not claim to reuse the five completed versions from attempt 2.

The two original failed attempts remain unchanged. The prepared state hashes both prior state/response files and the original five-module native custody summary. A read-only preflight checks exactly two failed runs, six old module operations, five original completed versions, no sixth operation in attempt 2 and no render. The original plan, script, 300-second silent clock and release remain exact.

`attempt3_driver.py` supports `prepare`, `status`, `offline-check` and a once-only `run`. Actual source c7, worker135, workbench7f03, final binding28dc and catalogcf80 are pinned through the real 39-artifact admission chain. `run` requires the actual `postflight-verified.json` and SHA-bound Ready/source receipt, validates all four live deployment identities/specs, running pod identities/images/source hashes, both immutable CM contents, protected workloads, candidate absence and physical quiet, then records its durable intent before the standard POST. An uncertain POST can only be reconciled with `status`; the driver never resubmits it. This preparation adds no retry API, module renaming or history edits.

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/attempt3_driver.py offline-check
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/attempt3_driver.py status
```

Only root executes the following once actual deployment receipts exist and their offline validation passes:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/attempt3_driver.py offline-check --postflight-evidence D:/codex-work/operations/ink-typed-handoff-20261121/rollout/typed-cas-r2/postflight-verified.json
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/attempt3_driver.py run --postflight-evidence D:/codex-work/operations/ink-typed-handoff-20261121/rollout/typed-cas-r2/postflight-verified.json
```

`verify_driver_offline.py` checks actual captured baseline compatibility, refusal before intent when the receipt is missing, refusal to repeat an uncertain intent, and reconciliation of an active third run. It makes no network call and uses temporary state only.

The explicit third state can be passed to the original-source collector once a real run ID and durable intent exist. Prepared state alone is not collection readiness or a film. New read-only observations, receipts, source snapshots and media belong under this attempt3 directory; previous attempts remain unchanged. The same original-snapshot, native-receipt, full-media and visual completion checks described in `formal/attempt2/VERIFICATION.md` still apply with the new actual run identity.
