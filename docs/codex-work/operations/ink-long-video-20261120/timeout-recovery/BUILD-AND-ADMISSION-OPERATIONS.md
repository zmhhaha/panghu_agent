> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/timeout-recovery/BUILD-AND-ADMISSION-OPERATIONS.md`；原文件 SHA-256：`934ee166331f21d138635ea2be5ecd26e6e76ac0466043c1b1622fc912296e4d`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

These external operators keep the original A packages, A snapshot, current B
records, and the failed v7 evidence intact. New operation outputs are under
`/root/video-releases/ink-film-20261120/timeout-recovery`.

The committed source is `faaa09affca3205ecfa46b2204f8d6aa2a017362`. Its only
runtime product change from ac7 is `MAX_TIMEOUT_SECONDS = 10800`; the existing
short default remains 600 seconds. The two other committed changes are tests
and documentation. The independent ARM checkout is `source-faaa09affca3`.

`build_renderer_workbench.py` is the unchanged original operator. It built and
pushed only the ordinary renderer and workbench `latest` tags, with source
revision/context labels and recorded digests. It did not rebuild creation,
control, worker, Manim producer, or any model role.

`measure_timeout_delta.py` measures five real images: original source A af8,
current renderer 3ff, target renderer 845, current workbench b63, and target
workbench b228. The renderer measurement imports the actual trusted Python
limits, evaluates 1440/1441/7200-frame timeout boundaries, measures all six
hosts and runtime fingerprints, `/opt/assets`, licences, library publication
files, and runtime entry files. Workbench measurement imports its actual limits.
Both image roles compare their complete `/app/agent_service` and
`/app/video_workbench` inventories. Only the reviewed limits constant and
documentation differ from current B. Original A to target still requires the
exact approved CPU Canvas2D flag and two comments. Measurement containers use
readonly filesystems, no network, one CPU, 512 MiB and 64 PIDs. Actual Docker
events prove all five exited zero and were destroyed; subsequent inspect found
each exact ID absent.

`arm_long_film_probe.py` is unchanged. Its complete 61-second export and
independent decode prove actual streaming execution, original clock, resource
bounds, silence and exact process/container cleanup. Its example exporter
continues to use a 3600-second technical execution budget. This is not a
10800-second duration test or the 300-second user film.

Only the new copy of `arm_film_k8s_probe.py` changes PREPARE: its trusted timeout
comes from installed `render_timeout` using the actual manifest frame count.
The 1-second snapshot remains 600 seconds with Job deadline 660. The 61-second
snapshot uses 10800 seconds with Job deadline 10860. The operator reads the
staged canonical metadata back from PVC and records it and its raw hash. The
native long Job is observed with a real browser and PNG encoder before exact
UID/foreground cancellation. This proves that native input accepts the new
budget; it does not claim a three-hour workload completed. Both technical
probe outputs retain their original media, Job, process and cleanup evidence.

The first pure EA attempt, `prepare_execution_admission.py` (3873...), failed
before creating its output because the additional Job deadline check read
the `before-delete.json` wrapper as a top-level Job. Its intent, script,
stdout, stderr and result remain immutable. `prepare_execution_admission_v2.py`
reads the explicit `job` member, binds its actual name, UID and annotation
binding to the cancellation evidence, then checks actual deadline 10860.
The actual original record passes; wrong names, UIDs, bindings and old deadline
values are rejected by local tests. The pure v2 revalidation passed without
repeating either probe. The standard execution admission was created only
under `admission-faaa09affca3`.

The new execution admission is technical evidence. The original-source
36-point replay, original Manim consumer replay, exact server-owned policy and
configuration, reviewed rollout, journal/client probe and one fresh ordinary
full-film request remain separately controlled phases. No technical fixture
substitutes for a genuine complete 7200-frame, 300-second H.264/AAC user film.
