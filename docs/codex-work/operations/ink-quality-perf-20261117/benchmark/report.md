> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-quality-perf-20261117/benchmark/report.md`；原文件 SHA-256：`cb6281ecb800c814bdf4fe5d96ccf503d1b2077b8553f5acb88f71487bec4fe8`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

# One ARM comparison of the two unchanged ink-animation works

The authorized comparison completed once, in the frozen old → new order, using the admitted ARM `video-motion-renderer:latest@sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b` image and Chromium 149.0.7827.0. Each work ran in a fresh browser at 1280×720, through genuine HyperFrames absolute seeks: the same eight warmup times, then all 576 frame times for the 24-second work. Neither run reported an engine error or blocked resource. No screenshots, encoded video, audio, model request, or creative/review task was produced by this comparison.

| Measured cost | Old work | New work |
| --- | ---: | ---: |
| Initialization, including browser launch and navigation readiness | 1.425 s | 1.062 s |
| All eight warmup seeks plus full output RGBA readbacks | 0.751 s | 0.283 s |
| Initialization plus warmup | 2.176 s | 1.345 s |
| All 576 measured seeks plus full output RGBA readbacks | 53.205 s | 17.989 s |
| Initialization plus warmup plus measured sequence | 55.381 s | 19.334 s |
| Seek median | 78.99 ms | 21.43 ms |
| Seek P95 | 88.86 ms | 33.38 ms |
| Seek plus readback median | 90.92 ms | 32.43 ms |
| Seek plus readback P95 | 101.73 ms | 45.97 ms |
| Native Canvas context count | 75 | 6 |
| RGBA pixel-storage lower bound | 15,158,636 bytes / 14.46 MiB | 54,067,200 bytes / 51.56 MiB |

The new work has lower measured per-frame CPU/IPC cost in this probe, while its retained Canvas pixel-storage lower bound is higher. Its six contexts include a 3072×3072 atlas, whose RGBA lower bound alone is 36 MiB. Fewer contexts do not establish lower memory use. The context accounting does not measure browser-process, GPU, driver, temporary readback, or total system memory.

| Film time interval | Old seek median / P95 | New seek median / P95 |
| --- | ---: | ---: |
| 0–3 s | 77.56 / 81.55 ms | 22.73 / 31.93 ms |
| 3–7 s | 79.49 / 86.14 ms | 25.12 / 41.69 ms |
| 7–12 s | 85.23 / 95.48 ms | 24.83 / 34.18 ms |
| 12–15 s | 75.52 / 81.10 ms | 20.83 / 24.18 ms |
| 15–19 s | 80.91 / 86.81 ms | 19.72 / 24.82 ms |
| 19–24 s | 76.09 / 81.40 ms | 3.35 / 17.64 ms |

Warmup cost is fully retained in `old.result.json`, `new.result.json` and `result.json`: each warmup seek, browser/wall readback and combined duration, as well as all lifecycle totals. Thus preparation performed during warmup is not omitted from the total comparison.

This compares two different complete works under the same probe. It does not isolate the effect of a particular caching/code change, establish statistical significance, or measure production export speed. Seek includes Playwright IPC; some native drawing can defer until the full RGBA readback. Repeated `getImageData` may alter Chromium backing-store/readback heuristics, and this is not the production screenshot/JPEG/FFmpeg workload. Fixed execution order, OS cache effects, temperature and other host load remain potential confounders; environmental snapshots are included in the per-work results. Navigation uses `networkidle`, which includes its quiet-window wait.

These timings are not visual-quality evidence and make no claim of watching the work. Offline 24 fps export awaits every frame; a slow machine does not itself drop exported frames or change their in-video timing.

Input identities:

- Old engine-and-files SHA-256: `26e684f9af66322afe2cdb2b614b38fbeb6fe2248c84ce17fdc798e22f21178f`.
- New engine-and-files SHA-256: `ad52b8596f331712714bc5e2e079f2a9957070da259c3a9c54582662df0f9a19`, independently matched between the stopped creator Job immutable artifact and the business candidate.
- Frozen plan SHA-256: `1c0293157339ce151bc1d737e88c9ff7c2efe8845f6c692ab80d10b4f8d98f98`.
