> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-wikiskill-integration-20261120/technical-probe/README.md`；原文件 SHA-256：`017c7f1c52126d22e37673599e67e75ba24bb8186f73fe9a6bb18d7f9d650505`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

# ink-landscape real runtime technical probe

Verified on 2026-11-20 in executor pod `video-animation-creation-586c47f99b-ps4c5`, node `arm-cluster-master`.

- Application commit: `177181b15f14b50c38dbd24f64e6c00ad9ed4af7`.
- Executor image digest: `sha256:a77ce26f82504cec230c55256711002a287b68fe4eac8d931c48abc5d54aaf22`.
- Existing renderer digest: `sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b`.
- Fixed library revision: `bb7338a5c57d4151c3d8635a8549057bb0383ca0`.
- Module SHA-256: `03ff8324ebee4e33ab1991399fe43ae7751f189bfa0116c30db29d64fd5f7469`.
- Shared host operation root: `/var/lib/video-animation-creation/technical-probes/ink-library-20261120`.

The fixture creates two explicit brush documents with fixed seeds. It calls `builder.stroke(result.points, result)` and `renderCanvas(ctx, scene, {time: seconds, clear: true})` from the pinned local ES module. The second brush reveals over absolute time. Source and input hashes are frozen in the renderer binding. Both inputs disable WikiSkill; the probe exercises the fixed code runtime independently of artistic instructions.

| Check | PNG | MP4 |
| --- | --- | --- |
| Result | Passed | Passed |
| Dimensions | 640 × 360 | 640 × 360 |
| Duration/frame count | One still | 2 seconds / 48 frames |
| V8 `createBrushDocument` calls | 4 | 4 |
| V8 `renderCanvas` calls | 7 | 60 |
| Exact V8 script source SHA | Passed | Passed |
| Actual dark brush pixels | 3,775 | Frame 0 and 47 differ |
| Deterministic reload | Passed | Passed |
| Absolute/reverse/repeated/idle seek | Still seek 0 repeated | All checks passed |
| Actual media validation | PNG decoded/dimensions checked | ffprobe and complete decode passed |
| Original temporary container removed | Stop receipt and independent Docker inspect | Stop receipt and independent Docker inspect |

PNG SHA-256: `81d5f756831999125601fe597234bf1b22b22d67d75cf034b91f677eaccee398`.

MP4 SHA-256: `f500e8658a31b2b76b8613ea818ff80515e7d2b5c2aed7a1730c4d8d3ef40f7c`.

The CDP observer starts precise V8 coverage before scene navigation, captures before and after reload, and confirms the script source against the pinned module hash. It excludes anonymous module evaluation and records actual named-function call counts. These measurements establish runtime execution; the fixture does not establish reference similarity, artistic quality, Agent authorship or that the formal film pipeline has been given this library.

`completed.json` contains the full summary and stop evidence; the per-kind receipts preserve the actual capture and V8 observations. `technical-fixture.png` and `technical-fixture.mp4` are technical output fixtures. Model submissions, business projects created and knowledge publications are all zero.

The first launch failed before rendering because the independent `technical-probes` parent directory did not exist. Its original `launch.json`, `stdout.txt` and `stderr.txt` are retained. The second launch adds parent-directory creation; the authored scene is unchanged. `launch-attempt2.json` and its logs retain that invocation. No failed render was retried or simplified.
