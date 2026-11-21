> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt8/README.md`；原文件 SHA-256：`49576438a8c87e4f5b94271b820438b4f144e1a328450b2a8a52b0d466f42888`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

# Attempt8 preparation

The independent external operations are in
`D:/codex-work/operations/ink-long-video-20261120/timeout-recovery`.
Read `ROLLOUT-ATTEMPT8-PREPARATION.md` there before dispatching anything.

Source revision: `faaa09affca3205ecfa46b2204f8d6aa2a017362`.
Reuse source: original A run `63961e9b-a7e1-4ad4-86b9-0c8abfa4af50`.
The new derived `reuse-baseline.json` pins the original af98 baseline, unchanged
12 modules/13 stopped children and terminal v7 history. v7's run is not reused.

`state.json` has a new v8 key and no run/intent. All future runtime expected
values and seven proof slots in `actual-runtime-inputs.json` are null. Root
review must also bind the actual new 10800-second timeout, current-before P/C,
source daa versus current-before f93 consumer admission and v7 terminal proof.

Offline inspection is available through `attempt8_driver.py offline-check` and
the observers' `--plan` commands. No submit, observer, capture, download, build,
probe or deployment has been run for attempt8 by this preparation. Collector
copies already account for the shared helper's read-only transaction. The new
native follower has a bounded 10980-second observation window.

Future policy/configuration/image/Pod values must come from actual reviewed
evidence. Keep partials, intents and all v7 material. Only root dispatches the
new ordinary POST after concrete preflight, using zero new authors/recordings
and one whole-film render. Duration remains 300 seconds and 7200 expected frames;
captured frames remain unknown until actual MP4 verification.
