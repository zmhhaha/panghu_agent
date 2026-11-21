> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/ARM_LONG_PROBE.md`；原文件 SHA-256：`d1d7eb40cef5b8a1881abb2641a47c14af6de71f7576bec6426a3c408759dc03`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

Run on the ARM build host after the complete, measured film image has been
built. This script neither deploys services nor calls a model. Use a new output
directory under the operation's existing remote release directory:

```sh
python3 -B /root/video-releases/ink-long-video-20261120/arm_long_film_probe.py \
  --image 'arm-cluster-master:5000/video-motion-renderer:latest@sha256:ACTUAL_DIGEST' \
  --revision ACTUAL_40_HEX_COMMIT \
  --output /root/video-releases/ink-long-video-20261120/probe61 \
  --seconds 61
```

The image must already be present in Docker; the script checks its immutable
digest, ARM64 architecture and revision label before creating anything. It uses
only code/libraries/browser from that image. The two bind mounts start empty;
they contain evidence and temporary files, never application-source overlays.
Only uniquely named containers bearing this run's exact ownership label are
stopped/removed. Host directories and evidence are retained.

The renderer gets 2 CPU, 2 GiB memory (swap disabled), 256 PIDs, UID 10001,
read-only root and no network. Writable directories are on host disk, matching
the disk-backed nature of the production emptyDir; the 2 GiB check is sampled,
not a filesystem quota. The report identifies that distinction explicitly.

Allow roughly 12 minutes or more for a five-minute export: every captured frame
retains the 100 ms stability wait and double screenshot. The independent 61s
probe is the minimum genuine long-path test, not evidence of a completed 300s
film. `--seconds 300` can measure the full duration independently if needed.

Successful evidence includes image/container identity, real browser/core/ink
receipts, exact 24 fps frame count, zero frame files, one streaming encoder,
main-realm continuity/fresh reconstruction, full video and audio decode repeated
independently, three distinct decoded frames, decoded silent-audio sample and
peak checks, sampled total writable disk, cgroup v2 exact memory peak samples,
OOM events, stopped/removed container and original process-identity checks.
`artifact-inventory.json` hashes every retained regular file except itself.

The fixture only proves technical execution. It does not establish visual
quality, model use of WikiSkill, production admission, or the execution-journal
cancel protocol. Parent must separately bind this evidence to the new exact
runtime admission and exercise that protocol rather than treating container
cleanup as journal cancellation.

Do not overwrite an earlier output or silently repeat a failed run. Inspect
`result.json`, the logs and cleanup records first; use a new clearly named
output directory for a justified retry.
