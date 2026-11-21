> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt2/README.md`；原文件 SHA-256：`d7daaf22d814066913149d59775fc21a300e1c0b024463a4f5227832f8799059`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

The second attempt uses the existing project, adopted plan, silent 300-second clock and audited WikiSkill release through the standard production-run endpoint. It receives a new production-run identity and module namespaces. The original failed run and native attempt remain unchanged.

The separate state is prepared with `intent=null`; no production POST has occurred. A fresh authenticated read confirmed the original failed module and native attempt are positively stopped, the plan remains current, and there are no other project children. Original first-attempt files are checked against their exact SHA-256 values.

After the root agent has fixed, deployed and verified the actual cause, it can run:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/attempt2_driver.py run --fix-evidence D:/absolute/path/to/actual-deployed-fix-receipt.json
```

The fix receipt must use the agreed `video.film-recovery-rollout-verified.v1` schema. `validate_fix_receipt.py` checks the exact committed source, both built image digests, ready Pod source hashes and hashed local before/after/build records. Root separately reviews the concrete deployment. Before the single POST, the driver also checks the current project and plan, the old failure's real native stop proof, project children and the SHA-pinned physical quiet helper. It persists the new intent before submission. It never repeats an uncertain POST.

Read-only reconciliation is available with:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/attempt2_driver.py status
```

All new state, responses and observation files stay in this directory. The original driver/state/response/native evidence files are only read. The source collector may use the new actual run identity after it exists; the prepared record is not a module or media acceptance.

`VERIFICATION.md` records the full source/native/snapshot/motion/visual/MP4 completion evidence and the explicit second-state collector commands.
