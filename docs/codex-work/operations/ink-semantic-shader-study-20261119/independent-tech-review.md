> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-semantic-shader-study-20261119/independent-tech-review.md`；原文件 SHA-256：`64e582d80ad2d09f995a6fda626349532cc6b7b1055af83ca518951d4e910de4`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# Independent technical review — semantic ink shader study

Date: 2026-11-19, Asia/Shanghai. Scope: read-only review of the captured artifacts and original frozen inputs. This reviewer did not start a rendering process. Artistic evaluation belongs to the separate visual review.

## R1: technical evidence passes within the single-frame scope

The parent executed `ink-semantic-shader-study-20261119-r1`, Docker CID `5a790f1ce6c18d2de6b56513fdecc28feb159a956225fdf3a7a3a1e5059eb6af`. The controller recorded exit 0, no timeout, no OOM, and 6.1815 seconds elapsed. The last observed running cgroup memory peak was 421,056,512 bytes (401.55 MiB); it is an observed value, not a guaranteed final or whole-host peak. CPU usage at the last sample was 6,382,608 microseconds, also not guaranteed final.

All four frozen input SHA-256 values match both the archived bytes and the executed-input receipt. All ten downloaded output byte lengths and SHA-256 values match the transferred-output receipt. Originals were not modified. The exact checks and local decoded-image measurements are in `r1-independent-byte-review.json` in this directory.

### Frozen identities and explicit host change

| Item | SHA-256 |
|---|---|
| Agent source JSON | `ea342abfe9106d9ad9c39060180a714fd19e4b349dfa0c92ac55e4ce0e28cb0a` |
| Capture host actually executed | `d40a48a38f1f7ea2c63d6df5758b05a0d5f20a6471d88946e97389997e9b842a` |
| Strict Mali guard, unchanged | `4772a11e139650204594ceb683bead3c5b0f6c016ba44e90683e9c71ac5b6562` |
| Capture request | `7f600e9d603d897ecba3578ddca75075325f71eb7dcd72e097543cbfbe364539` |
| libmali before and after | `07cc993b13d6591161b0a12a270c75e06f25d56437016c6fbff8ea36e47c6614` |
| Pinned image digest | `sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b` |

The unexecuted preparation host had SHA `f227fab78da9729887a3bf44e4e76d4700bb1723e335d22a3f28958d0ad23940`. Before R1 execution, the parent requested source-provided semantic diagnostics. That host and its old preparation manifests remain under `superseded-preparation/`. The replacement host adds only diagnostic reading, export and provenance metadata. It calls the author's existing `__inkStudyLayer` helper and reads `__inkStudy`; it does not add or change drawing code. The strict guard is unchanged. No executed identity was overwritten.

### Actual GPU, HyperFrames and replay evidence

- The executed image ID is `sha256:e21e5b04e4092c2bcf6df0df36a9084abc81fce6b01066ec4ac1414c6cfbb73a`.
- Browser version is `149.0.7827.0`; actual arguments include `--use-gl=angle`, `--use-angle=gles-egl`, `--enable-gpu`, `--disable-software-rasterizer`, and headless Ozone. Playwright also injects `--enable-unsafe-swiftshader`; that flag alone establishes no backend. The actual guarded context, CDP identity and host FD observation together establish the backend used in this run.
- The visible WebGL2 context reports `ANGLE (ARM, Mali-G610, OpenGL ES 3.2)`, `software:false`, `hardware_mali:true`, no context loss, and GL error 0. Browser CDP independently reports ARM/Mali-G610 and `ANGLE_OPENGLES_EGL`.
- The host observed PID 554252, start ticks 297791943, holding `/dev/mali0` FD 13 in the exact container cgroup. This is external process evidence; it is not supplied by the art source.
- Actual HyperFrames core version is `0.8.149`, SHA `675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc`. Both initial and reloaded observations show one core seek to requested/actual time 0. The legacy bridge is recorded explicitly.
- There is one visible canvas, with buffer, viewport and CSS bounds all 1024×640. Sixteen author-created Canvas2D inputs are disconnected/invisible and do not constitute additional visible layers.
- `still.png`, `repeat.png`, and `reloaded.png` all decode to 1024×640 RGBA and share SHA `7edb748c7ccbc8e9877133b29baa997df92b9a8631088ec484d7db297615aa2b`.
- The same-source shader-disabled capture has SHA `a0aab389bc51ffead1a8345f8bb1709df22f40c160d8d77680a9486d123acbf5`. Local RGB comparison finds 58,558 changed pixels out of 655,360 (8.9352%), maximum absolute channel difference 18/255, and mean absolute channel differences R/G/B 0.0987/0.1073/0.0945. This proves a measurable contribution. It does not prove a large perceptual contribution, better ink appearance, or improved similarity.
- Page errors and blocked network requests are empty. The container ran with network disabled, 2 CPU, 1536 MiB memory/swap ceiling, pids 256, read-only root, nonroot user 10001, dropped capabilities and no-new-privileges. The only GPU device mapping is `/dev/mali0`; source/library mounts are read-only.

### Author semantic diagnostics: useful, explicitly untrusted for GPU proof

`semantic-layers.json` and the four `semantic-*.png` files are marked `source-provided` and `trusted_gpu_evidence:false`. The host really called the existing helper and archived its return bytes. That provenance verifies what was exported, not whether the author's semantic labels or claims are correct.

All four diagnostic images decode to 1280×800 RGBA and have varying red-channel data:

| Diagnostic | Red range | Distinct red values | Nonzero-red share |
|---|---:|---:|---:|
| wet | 0–232 | 232 | 27.259% |
| dry | 0–255 | 256 | 7.972% |
| depth | 0–194 | 177 | 27.259% |
| cloud | 0–238 | 238 | 5.290% |

These data rule out a completely empty/uniform exported mask. They do not independently establish correct material semantics or effective shader use. In particular, the depth metadata describes nominal values through 231, while the exported red maximum is 194. This may follow from compositing or coverage and is a point for the author to inspect in R2; it is not an automatic GPU failure or an artistic verdict. Author stroke counts remain source-provided metadata.

### Stop and identity preservation

The executed CID exited normally and Docker removal returned 0. Subsequent inspect reports no such object, matching `confirmed_absent:true`. The original GPU PID is absent at the recorded post-cleanup check, and all observed GPU processes are marked gone using their birth ticks. The source inputs and libmali bytes remain unchanged. These are recorded point-in-time stop checks, not a claim about every process on the server.

### Limits

This run establishes a deterministic static frame plus same-source shader ablation on the fixed experimental Mali path. The four captures contain only two unique images and do not establish continuous animation, motion quality, audio, production admission, generic shader-service readiness, WikiSkill publication, or agreement with the reference image. Production renderer guards were not changed. No R2 rendering was started by this reviewer.

## R2

The parent completed the second and final execution. This reviewer read only the local frozen inputs, receipts and image bytes. No additional render or server query was performed.

R2 used a separate source and request identity while preserving the exact same trusted host, strict guard, pinned image and libmali bytes as R1:

| Item | SHA-256 |
|---|---|
| R2 Agent source JSON | `d884d788678cada014f1971416373570012430aa82b2ab51a913e6c288b627dd` |
| R2 capture request | `5f06d03438be31f787a1ab432a52dcd612b67613939eede720786c80c95701f6` |
| Shared capture host | `d40a48a38f1f7ea2c63d6df5758b05a0d5f20a6471d88946e97389997e9b842a` |
| Shared strict guard | `4772a11e139650204594ceb683bead3c5b0f6c016ba44e90683e9c71ac5b6562` |

All four input digests match the frozen and executed records. All ten downloaded outputs have the recorded lengths and SHA-256 values. The four actual capture images decode to 1024×640 RGBA; still, repeat and reload share SHA `17bd80f4479533a4d3ea1c3c32a2f6c5511f2f05501bf3649d9adc28450afbef`. The same-source shader-disabled image has SHA `4e6bda658bdc3f7ec2d1af01676a0d293cefa7e5aafdfdf3958086decfcca755`.

The guarded context again identifies hardware Mali, software false, no context loss and GL error 0. Both HyperFrames observations record a real core seek at time 0; the visible-canvas count is one. Errors and blocked requests remain empty. The fixed experimental renderer backend and confinement are unchanged. This is a genuine single-frame render rather than evidence of continuous animation.

Container `ink-semantic-shader-study-20261119-r2`, CID `85c9d9ba245d217c62397c848aeeeacae9cd5ddc64e4443d471d144f8ff89673`, completed with exit 0, no timeout and no OOM in 8.6738 controller seconds. The last observed running cgroup memory peak is 452,460,544 bytes (431.50 MiB); the last CPU sample is 9,257,913 microseconds. These remain sampled cgroup observations, not guaranteed final peaks or a server-wide benchmark. The external host observation recorded PID 563556, start ticks 297816012, with `/dev/mali0` FD 13. The exact CID was removed and absent on inspect. The recorded original GPU process is gone at the post-cleanup birth-ticks check. Frozen inputs and library bytes remained unchanged.

### R2 ablation and author diagnostics

The local shader-on/off pixel comparison finds 83,255 changed RGB pixels (12.7037% of the frame), maximum absolute channel delta 30/255, and mean absolute differences R/G/B 0.2856/0.2955/0.2892. The measured contribution is larger than R1 under this simple metric. A larger byte difference is not evidence of better material, reference similarity or perceptual quality; those conclusions require the independent visual review.

All semantic PNGs remain source-provided 1280×800 RGBA diagnostics. They are never counted as additional trusted GPU captures. Their nonzero red-channel shares are wet 27.254%, dry 13.321%, depth 27.254% and cloud 5.290%. Their ranges remain wet 0–232, dry 0–255, depth 0–194 and cloud 0–238. The author now explicitly describes depth as an artistic nonlinear alpha-composited field, so the nominal 20/92/169/231 values are not claims about measured texture extrema or metric distance. This clarifies the R1 metadata issue without upgrading it into independently validated depth semantics.

The author-supplied counters report 744 rock strokes, 189 dry gaps, 13 pine clusters and 5,907 broken fibres. They are not a trusted performance measurement or independent stroke census. The parent checked the actual runtime and clarified that the applicable `MAX_CANVAS_CONTEXTS=500` limit concerns cumulative native Canvas contexts, not strokes; this study brief sets no total-stroke cap. Therefore these counters neither violate nor verify that context limit, and they support no 60 fps or general performance claim. The actual experiment limits are two attempts, 150 active seconds each, 2 CPU, 1536 MiB, pids 256 and the existing context guard.

Full local checks are retained in `r2-independent-byte-review.json`. R1 evidence was not overwritten.

## Closed technical conclusion

Both allocated attempts are complete and stopped according to their exact execution receipts. Both pass the bounded static Mali/HyperFrames capture, identity, replay and byte-integrity checks. Four genuine captures per attempt were retained, alongside four separately marked author diagnostic layers. Neither attempt establishes production admission, continuous animation, a visual-quality pass, WikiSkill adoption, or a generic shader rendering service. Two attempts were used; this reviewer will not initiate a third.
