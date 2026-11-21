> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/context500-20261117/admissions/README.md`；原文件 SHA-256：`ae78a714264d5898e50ab06ed3839c7eb790262bb6eb4c8adb1545e42063f637`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

# Context 500 admission evidence

See `evidence-summary.json` for checked local/server paths, byte counts and
SHA-256 values. This directory contains 36 small JSON receipts (603,504 bytes),
plus this note and the summary. No MP4, PNG, recorded audio, source archive or
Secret was downloaded.

The accepted renderer is the full-source `Dockerfile.film` image
`arm-cluster-master:5000/video-motion-renderer:latest@sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b`,
from Git revision `e08474172da4bf25f36927cde3c1dbe202b9f5fa`.

- `methods-r2/completed.json`: four real method renders, 144 frames, covering
  Canvas, Three.js, Anime.js and the original stopped Manim PNG resource.
- `formal-r2/completed.json`: one real complete-film render, 384 frames/16s,
  retaining exact original Agent module sources, recordings and prepared assets.
- `formal-r2/verified-media.json` and `independent-decode-environment.json`:
  independent stream/timestamp validation and complete video+audio decode in
  the same pinned film image with read-only input mounts and no network.
- `formal-r2/render/cleanup-completed.json`: exact test Job, Pod and helper
  absent; durable PVC evidence retained.
- `formal-r2/cancellation-evidence-applicability.json`: the original native
  running-cancellation/process-cleanup evidence is reused for its unchanged
  mechanism. This is explicitly historical evidence, not a new context500
  runtime cancellation probe or business API cancellation test.
- `r1-plain-renderer/`: preserved first attempt using the Node-only renderer
  entry. Its four method probes passed, but formal staging lacked the Python
  package. No formal export Job was submitted, the failed helper was removed,
  and this image was not deployed. The accepted r2 source build uses the
  repository's complete `Dockerfile.film`, with new identities and evidence.

All work in this admission task invoked zero models, zero TTS and zero Cairo
producers, and performed no Deployment replacement or Skill publication.
Technical admission and full decode do not assert aesthetic approval.
