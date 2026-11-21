> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt2/VERIFICATION.md`；原文件 SHA-256：`08d5783794154bf67b9d74161e603025159f330425884e2e4a1ab860171fa7b8`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

This attempt completes only after a real 300-second MP4 and its source, native provenance, motion and visual reviews are available. A prepared state, completed Agent operation, source inventory or sparse contact sheet is insufficient alone.

Deployment gate: `attempt2_driver.py run --fix-evidence D:/codex-work/operations/ink-film-recovery-20261121/rollout-verified.json` validates the agreed `video.film-recovery-rollout-verified.v1` receipt offline. It pins commit `ea6ea02aa84bbf4772c4940005f1e6e6ae3ad7a7`, catalog `9bf02f5…`, control image digest `87d9402…`, workbench digest `99e367c…`, actual ready Pod source hashes and hashed local before/after/build records. The `film-static-assets` init retains renderer digest `af8a17b…`. This gate makes no model calls. Root separately reviews the concrete CAS and postflight. The standard production endpoint is unchanged; the driver persists its one intent before POST and never repeats an uncertain submission.

Use the actual new run ID written in `attempt2/state.json`; the prior failed run ID must never substitute for it. Keep the original first-attempt files unchanged.

Native provenance is collected as each selected operation appears:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/native_receipts.py status
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/native_receipts.py capture --index 0
```

`status` derives each job ID from the exact run item and its immutable child request, not from the newest project job. Report a new job ID to root immediately so its physical/native diagnostic monitor can start while the worker still exists. `capture` accepts only a completed, positively stopped, synchronized actual module version. It re-downloads the authenticated two-file native handoff, verifies it against the selected immutable package, preserves raw native Skill events and lists only actual successful read paths. Failed reads remain failures. The separate read-only PG query records model-response counts and allocation retirement without returning upstream content. These receipts establish custody and stopped completion, not artistic application.

Collect original selected source packages and the original execution snapshot with the explicit second state:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/collect_film_sources.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/state.json --plan
python -B D:/codex-work/operations/ink-long-video-20261120/formal/collect_film_sources.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt2/state.json
```

Each capture is new. The prepared state stays pending until a real response or reconciliation supplies its own run ID. The collector checks 12 exact 25-second silent intervals, the unchanged adopted plan, frozen release `bccef9ca…`/sequence 2, selected source hashes and predecessor chain. The final render child must point to those exact 12 versions and targets. The snapshot must be the original persisted child snapshot, with 7,200 frames, 24 fps, empty recording custody and the same immutable renderer. Never build a replacement snapshot for review.

The source reviewer examines each original scene and incoming transition for actual ink-library use, editable geometry and stroke data, bounded static caches built in `mount`, deterministic absolute-frame evaluation in `renderAt`, proper disposal and actual use of the fixed b6 library. Review world/camera/object identity across every handoff. Reject external photos/media, hidden text/audio, history-dependent integration, short repeated clips, a still image with only a zoom, per-frame reconstruction of heavy static ink, empty groups or transforms that `renderCanvas` resets. Exact source, descriptor and a Skill receipt do not establish visible ink contribution.

After the real snapshot exists, use `files/snapshot/expected-identity.json` from that same source capture for the expected snapshot SHA, run ID, renderer image and original snapshot file SHA. Copy those exact captured snapshot bytes to a new private path on the ARM Docker host. `D:/codex-work/operations/ink-long-video-20261120/sparse_film_preview.py capture` runs the immutable renderer core against that snapshot; `sheet` makes local contact sheets. Its `--revision` comes from the immutable renderer image label, not the new workbench/control commit. The default 36 frames cover opening, closing, each scene midpoint and both sides of all 11 cuts. For a fuller motion comparison, select 60 frames: opening and closing, frames `600*i+120`, `600*i+300`, `600*i+480` for each i=0..11, and `600*i-1`,`600*i` for i=1..11. Both fit the tool's 80-frame limit. Sparse stills must be labelled as stills; they cannot demonstrate continuous motion.

The visual review checks varied near/middle/far masses, dry/wet ink contrast and blank space through the river journey. In particular, inspect every planned anchor across each cut: folded shoulder, canyon gate, stream mouth, low shoal, pine branch, persistent boat, sandbank, rock foot, waist fog, open river and final reed cluster. The closing view must be a new downstream location with a smooth stop. Review short temporal sequences inside every scene and around all cuts, followed by the complete actual MP4. Record the inspected frame/time ranges and limitations; do not infer human approval.

Final media evidence must use the owner-scoped GET of the actual selected render and match its persisted byte count and SHA-256. Preserve the downloaded MP4 as a local artifact. Check HTTP range handling separately. Run independent `ffprobe -count_frames` and a full `ffmpeg -xerror` decode of that downloaded file. Required video facts are 1280×720, 24 fps, 7,200 decoded frames, start at zero and exact 300-second video duration from `duration_ts*time_base`; no subtitle or extra video stream. The renderer may encode a silent AAC track: prove decoded silence (peak ≤2 in signed 16-bit PCM, with the peak and sample count reported), rather than treating track presence as narration. Expected 48-kHz stereo duration is 14,400,000 samples per channel, allowing only reported AAC padding. The source snapshot must contain no recording inputs and no generated narration.

Keep the final run/render IDs, all 12 native receipt directories, original source capture, snapshot expectation, sparse evidence, visual-review notes, independent MP4 probe/decode/audio checks and local MP4 hash together. If any stage fails, retain its original evidence and report that failure accurately. Continue the real formal workflow toward the actual film; a status receipt alone does not complete the user's request.
