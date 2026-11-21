> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt2/DOWNLOAD.md`；原文件 SHA-256：`92d06c1f36fa6af327f0a1e357e57c9e4842f57e369f0379562217c0007510f0`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

The actual MP4 helper is prepared for root review. It has only been syntax
checked and run with the entirely local `--plan` mode. No download has run.

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/download_actual_film.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/state.json --plan
```

After the actual run and selected render are completed and root has reviewed
the helper, download into a new directory whose parent already exists:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/download_actual_film.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/state.json --output D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/actual-media --download
```

The helper pins the explicit second-attempt state, owner, project, run, adopted
plan, audited release and renderer. Inside the existing workbench Pod it reads
the owner-scoped existing run/render/snapshot/data with SQLite `mode=ro` and
`query_only`, verifies frozen content, all 12 selected versions/targets,
stopped completed render, original snapshot/runtime and 7,200-frame silent
clock. It streams the existing DB blob only to hash it. No Store, application,
provider, renderer or replacement snapshot is created.

Owner-scoped public GETs confirm the same completed run/render. The standard
GET `/api/renders/ACTUAL_RENDER_ID/video` checks first-32-byte, suffix-32-byte
and unsatisfiable ranges against the stored bytes. Full HTTP status/selected
media headers and range custody are saved. The full MP4 is streamed over SSH
directly to a bounded local partial file, limited to the exact result size and
128 MiB, then checked against its actual result SHA256. Post-download read-only
DB custody must remain exact. Trusted owner headers stay inside the Pod.

Every invocation requires a new output directory. Failure preserves intent,
metadata, stderr status, partial bytes and a failure receipt; no automatic
retry occurs. A verified file is renamed `ink-landscape-300s.mp4` only after
all custody checks and transport EOF pass. `snapshot.json` preserves original
persisted snapshot bytes. `download-verified.json` contains the actual public
download URL suitable for the final response, alongside local MP4 and snapshot
paths, expected SHA256/size and HTTP/range evidence.

This proves custody and download behavior. Run the independent actual-file
`verify_user_film.py` ffprobe/full-decode/silence checks and complete the
separate original-source/motion/art reviews before declaring film completion.
