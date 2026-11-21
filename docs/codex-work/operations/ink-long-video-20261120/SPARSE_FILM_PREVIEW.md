> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/SPARSE_FILM_PREVIEW.md`；原文件 SHA-256：`e39344ecbd93516979ce91926e226923eb8eb9e99540c8383a44612083c61de0`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

The sparse tool reads the original complete execution snapshot JSON and its
independent SHA. It validates the immutable image runtime and every original
host/project/module/asset byte, then calls that image's `probeFilmBrowser` with
only the selected global frames. Every still uses a fresh reconstruction in
the original opaque realm and real HyperFrames core, the original viewport,
and the existing idle double-screenshot check. It never edits the frozen
bundle or generates an MP4.

Capture on the ARM host, using a new output directory:

```sh
python3 -B /root/video-releases/ink-film-20261120/sparse_film_preview.py capture \
  --image arm-cluster-master:5000/video-motion-renderer:latest@sha256:af8a17ba948d2c17bdaf0a9e176508c2a5bc601285684088892f71af5f649475 \
  --revision de5a5adef40c03e7ebac74dc886ae8cb9fca0a3a \
  --snapshot-json /root/video-releases/ink-film-20261120/ACTUAL_USER_SNAPSHOT.json \
  --snapshot-sha256 ACTUAL_SNAPSHOT_SHA256 \
  --output /root/video-releases/ink-film-20261120/ACTUAL_USER_SPARSE_REVIEW
```

Default samples include frame 0, the last frame, every scene midpoint, and the
frames immediately before and at each new scene boundary. Duplicate frames
merge their labels. There is a strict maximum of 80 samples. Eight scenes
usually produce about 24 stills; ten scenes produce about 30. Preserve all
required composition checkpoints rather than silently removing boundaries to
hit an exact count. For a tool-only one-frame check use `--frames 0`.

Copy the output evidence (regular files; skip `work/` browser caches) to the
local operation directory. Create the contact sheet with local Pillow:

```powershell
python -B sparse_film_preview.py sheet `
  --preview D:/codex-work/operations/ink-long-video-20261120/runtime-evidence-arm/ACTUAL_USER_SPARSE_REVIEW `
  --output D:/codex-work/operations/ink-long-video-20261120/ACTUAL_USER_CONTACT_SHEET.png
```

`source-metadata.json` binds the original snapshot, bundle, film, clock,
viewport and rational frame times. `native-probe-receipt.json` retains actual
core observations/resources/errors and realm cleanup. Every PNG has a SHA in
`frame-inventory.json`. `summary.json` reports failure diagnostics or verified
sparse stills; the sheet refuses unverified captures and checks PNG hashes.

The tool checks composition and sampled rendering only. It cannot establish
motion between stills, transition smoothness, frame-by-frame stability across
the full film, or completion of the user's five-minute movie. The formal
snapshot is pending; the tool smoke output is an explicitly technical fixture.
