> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt3/OBSERVERS.md`；原文件 SHA-256：`7f80464e0b097905dc23b2c5fa93011ec1bd08817ce0623f0b22eef537fa7f43`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

The scripts read only the exact third-run identity in this directory's `state.json`. Their `--plan` modes make no remote calls. They preserve the previous attempts and put new captures under this directory. No observer submits work, creates a render, refreshes Skill delivery, or updates driver state.

After the once-only production response or reconciliation records the real `run_id`, start these two foreground commands in separate hidden operator processes, with stdout/stderr captured to fresh files:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/watch_selected_modules.py --capture-completed --collect-originals
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/follow_new_jobs.py
```

The first command polls selected module status every 15 seconds, preserves authenticated stopped artifacts and native Skill receipts for each completed module, and collects each newly completed source prefix with the unchanged original-source collector. It exits on terminal failure or when all twelve modules have completed. The second reads the first command's local status files and starts the already-authorized read-only diagnostic followers for every actual selected job, including scene 1. It never independently polls the service. Launch directories are never replayed. A launch/capture failure stops its loop and leaves the partial evidence for inspection.

Individual read-only operations:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/native_receipts.py status
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/native_receipts.py capture --index 0
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/observe_run.py
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/collect_originals.py
```

After all twelve modules complete, use `observe_run.py` to follow the render, then run `collect_originals.py` again to capture the actual original render snapshot. This snapshot collector already verifies the full scene and transition module inventory.

Download the completed actual MP4 into a new absolute directory whose parent exists:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/download_actual_film.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/state.json --plan
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/download_actual_film.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/state.json --download --output D:/codex-work/operations/ink-long-video-20261120/formal/attempt3/media-final
```

The downloader requires the actual third idempotency key and stopped completed run/render, verifies the complete immutable scene+transition package inventory against the original snapshot, checks stored and HTTP media hashes plus range behavior, and retains partial files on failure. It preserves the initial state bytes' hash while allowing unrelated current-state observation fields to change; the run/request/intent/release identity must remain exact. The existing 128 MiB media and 24 MiB snapshot limits remain in force. Download custody alone does not establish full decode, silence, motion continuity or artistic acceptance.

Diagnostics pin worker `135e8ac2f5915b1cf89fb20a2536af8af55190cae466a5603c2c56ab91942392`. The Docker reader's entire remote FD, Pod UID, container ID, image configuration/RepoDigest, restart-zero and log-path validation program must equal the reviewed R2 helper at runtime; its diagnostic allowlist is unchanged. Only source-selected metadata and allowlisted diagnostic records leave the host. Missing/empty diagnostics are never treated as evidence of success or failure.
