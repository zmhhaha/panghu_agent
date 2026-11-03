# 连接线提前显示修复与验证

日期：2026-11-03（Asia/Shanghai）。源码提交：`a5350a3c2a1b7f4f5fd8626dc129e617b4c7747e`。
本轮发布标识为 `video-connectors-20261103-r1`，新增编译器为
`motion-compiler-4`／`technical-diagrams-4`。

当前已完成源码修复、本地回归、固定镜像构建、两镜精确镜像预检、线上发布、
单次新草稿及媒体／数据库检查。人工节奏、听感和用户效果确认仍为 **pending**。

## 用户问题与根因

用户指出：样品视频在动画尚未开始时，画面中已经出现一条条细连接线，破坏了逐步讲解的效果。

旧动态图解模板为每条关系输出了两条 SVG 路径：一条始终显示的深色底线
`relation-base`，以及一条随 `draw` 动作绘制的亮色线 `relation-active`。
亮色线的描边进度虽然尚未开始，深色底线已经把整条关系提前画在画面上。
仅调整动作开始时间或旁白停顿不能消除这条静态底线。

编译器 4 改为：

- 不再为关系输出 `relation-base` 路径。
- 实际连接线在 HTML/CSS 初始状态就完全透明，覆盖脚本和动画运行环境启动之前的时刻。
- 到该关系的 `draw` 动作开始后，才同步改变线条透明度与描边进度；标签和箭头仍按其时间表显示。
- 正向播放、跳转以及播放后返回 0 秒，都应恢复相同的初始隐藏状态。

这次修改保留既有文稿、图解对象与关系、动作安排、配音和时长；画面中的关系随着对应讲解出现。

## 版本兼容与自动检查

新增默认编译器为 4，保留显式指定编译器 1、2、3 的能力。兼容测试对
`flow`、`compare`、`focus` 三种旧模板逐个比较全部输出文件的固定 SHA-256，
包括可执行 HTML、计划、节拍、时序和 manifest；旧冻结快照继续按记录的旧编译器验证和重编译。
既有 MP4 与历史快照保留其原内容，本次新草稿需创建绑定编译器 4 和新运行环境的新快照。

渲染器的输入契约新增接纳 `motion-compiler-4`／`technical-diagrams-4` 配对，
同时保留原来的编译器／模板配对。工作台与独立 executor 都会验证或重新编译冻结输入，
因此发布范围需要同时更新这两个应用及 renderer；HyperFrames、GSAP、浏览器、字体等运行依赖保持。

编译器 4 的浏览器观察和技术审查增加了以下要求：

- 在真正的 0 秒和返回 0 秒后，分别保存 `initial`、`repeated_initial` 的完整元素、关系、布局与截图摘要。
  它们的实际 seek 时间必须都是 0，并且摘要必须与正反向 seek 记录对应。
- 检查计划中的**全部关系**，包含还没轮到绘制、以及没有 `draw` 动作的关系。
  初始时线条、标签和箭头透明度均为 0，页面中的底线路径数量 `baseCount` 必须为整数 0。
- 每个动作的前、中、后观察窗口同样检查全部关系，按固定 GSAP 缓动与动作时间表核对透明度和描边进度。
  尚未开始的关系保持隐藏；已经绘制的关系按时显示。缺失状态字段、提前露线、底线重新出现都会拒绝技术验收。
- 保留文字溢出、画布边界、动作确实产生变化、实际 seek 时间和反向 seek 一致性等原检查。

本地回归结果为 **Python 367 passed、1 skipped；Node 18 passed**。
覆盖源码编译、旧版本固定输出与冻结输入、渲染契约、初始及返回首帧证据、
线条／标签／箭头的前中后状态，以及非当前绘制对象提前显示的反例。
这组结果属于本轮本地测试，不累计为线上浏览器或最终媒体验收。

主要测试文件：

- `tests/video_workbench/test_motion.py`
- `tests/video_workbench/test_motion_compiler_versions.py`
- `tests/video_workbench/test_motion_renderer_job.py`
- `tests/video_workbench/test_motion_review.py`
- `tests/video_workbench/motion-renderer.test.mjs`

## 本轮构建、预检与发布状态

工作台和 executor 共用的新应用镜像为：

`arm-cluster-master:5000/video-workbench:latest@sha256:c6dd463b44cd1b2f4b40be09d91ed934919156b0c8cf2fdd8c3c84dd81375801`

新 renderer 镜像为：

`arm-cluster-master:5000/video-motion-renderer:latest@sha256:9d4e1665aca99cd947b1f4d6de2b8883c87d8c26a67d40aa3c9af69e7508cbf8`

构建先后遇到两次错误，均未部署：第一次工作台逐文件 COPY 达到旧镜像的层数上限，
报 `max depth exceeded`，随后改为单层应用 overlay；第二次 overlay 新建目录受
`umask 077` 影响成为 0700，非 root 导入失败，随后为 overlay 根目录及其下全部目录
显式设置 0755、文件设置 0644。第三次构建成功，非 root 导入检查通过。
两次失败目录 `build-depth-failure`、`build-permission-failure` 及日志保留在本轮远端证据目录。

两镜精确镜像预检结果为 `exact_connector_image_preflight_passed`，
使用 `motion-compiler-4`／`technical-diagrams-4`。两镜分别有 4 条、2 条关系，
合计 18 个动作全部通过技术审查；`initial` 和 `repeated_initial` 中所有关系均完全隐藏，
动作前／中／后观察通过。预检使用 `network=none`，实际模型调用、TTS、业务写入、MP4 生成均为 0。

助手另目视检查了 6 帧：两镜各自的初始帧、首个 `draw` 之前与之后的画面。
所查初始及绘制前画面未见提前连接线，绘制后连接线正常出现。
这些帧属于预检观察；新 MP4 的独立检查记录如下。

线上发布已完成，回执为 `video_connector_rollout_ready`，
`manual_reviewed_continuation=true`。新工作台 Pod
`video-workbench-74c766d569-stwgg` 和 executor Pod
`video-motion-executor-6fdd8b7fdc-b77rq` 均 Ready、0 重启，实际 imageID 与上述应用 digest 匹配。
两者使用新 immutable runtime ConfigMap `video-motion-runtime-5855d029265601b68cc4`。

发布曾在工作台暂停后中断：队列 guard 的 exec 进程先于 Kubernetes Pod 状态更新退出，
原脚本以 `video_queue_guard_stopped_while_old_pods_live` 停止。当时工作台已为 replicas 0、
无 Pod，executor 仍保持原版本 Ready。经人工核对确切暂停结果、原 UID/spec、
依赖、两份新 runtime ConfigMap 和预检摘要后，使用已审阅 continuation 继续原发布。
continuation SHA-256 为
`822b50aeed3e0a8ce1d7da5a0878f796c91573f7ca7b041098fa6fdaee9a2d70`。
它复用原工作台持锁备份和原计划，仅继续 executor 的持锁备份及暂停，
仍要求观察到全部旧容器已停止或 Pod 消失，随后按原窄范围更新并恢复两个服务。
原 `apply-once.json` 保留，未重跑原 apply，未删除或重置原 claim。

发布只读回归 `video_connector_read_only_postcheck_passed` 已通过：
工作台 15 表、executor 2 表的全部 schema、旧行每列及历史媒体保持，零新增行；
active 任务为 0，语音目录仍有 8 个音色，health／ready 均为 HTTP 200，依赖部署 spec 保持。
本轮发布的两个旧 Pod 均已确认停止。

整片草稿 `b1ee94ad-b55f-4f72-b40b-9729c10eb584` 已 completed，
冻结快照 SHA-256 为
`b7c09bd509d855c538ed1f633973c3663dbd238d6b22550066469dc0a817a376`。
新 MP4 为 **365618 字节、19 秒、456 帧、1280×720、24fps、H.264/AAC**，
SHA-256 为 `f8d5a146845153c237e2234137870d6fbe514f62175bfbc5ad3cf58bceab0813`。
全文件解码及 14 项原始 PCM 检查通过；整片相关系数为
`0.9999565535252921`，NRMSE 为 `0.009395869825092159`，lag 为 0。

助手目视检查了全部 10 张 MP4 关键抽帧，覆盖 0 秒、0.0417 秒、6 秒、11.9167 秒、
12 秒、12.0417 秒、15.5 秒、18.9167 秒及镜头边界前后。
两镜起始画面均未见提前出现的细线、箭头或关系标签，后续连接线正常出现。
这 10 张为成片抽帧，独立于前述 6 张预检截图；没有把抽帧检查记作完整播放或人工节奏、听感认可。

本轮实际 **0 次 Agent、0 次 TTS，仅提交 1 条新的整片 render**。
最终只读核对通过：工作台仅 `video_renders` 新增 1 行，executor 仅对应的
`motion_operations` 新增 1 行、`motion_scenes` 新增 2 行；
全部旧 schema、旧行各列和历史媒体保持，依赖部署 spec 保持。
原项目仍为 revision 12，文稿、计划、音频、采用 heads 和 pending 人工时序保持，
人工 alignment 写入为 0。旧草稿 `a141b04b-e841-4f33-98e9-6910e532d7b1` 的 MP4
SHA-256 仍为 `36b97391c441c93479144ad354debf8c5794909e67ab7099cca12514a51f4927`。

验收仅复用原两镜项目 `1c60d406-688b-415d-9572-a99ec5f28724` 的已采用图解和原配音，
不改项目文稿、revision 12、采用版本、音色或人工时序状态。
本轮范围为 **0 次 Agent、0 次 TTS，最多提交 1 条新的整片 draft**，
保留旧草稿 `a141b04b-e841-4f33-98e9-6910e532d7b1`；不自动重试、不提交正式 delivery，
不代替用户确认人工时序或声音自然度。

| 阶段 | 必须保留的结果 | 当前状态 |
| --- | --- | --- |
| 源码与本地回归 | 提交 `a5350a3c…`；Python 367 passed、1 skipped；Node 18 passed | 已完成 |
| 固定镜像构建 | 工作台完整 56 文件 allowlist、54 文件单层 overlay；renderer 仅替换契约与观察代码；两镜像 digest 如上，非 root 导入通过，两次构建失败证据保留 | 已完成 |
| 精确镜像预检 | 两镜 4＋2 条关系、18 动作全部通过；初始／返回初始及动作前中后检查通过；助手另目视 6 帧；无 MP4、无模型／TTS／业务写入 | 已完成 |
| 安全发布 | guard 退出状态竞争经已审 continuation 继续原计划；原 claim 与工作台备份保留，全部旧 Pod 已停；新工作台／executor 均 Ready、0 重启、精确 digest | 已完成 |
| 发布只读回归 | 工作台 15 表、executor 2 表全部 schema、旧行各列及历史媒体保持，零新增；active 0、8 音色、health／ready 200，依赖 spec 保持 | 已完成 |
| 单次新草稿 | `b1ee94ad-b55f-4f72-b40b-9729c10eb584` completed；同一项目、两镜原计划与原音频，编译器 4 新快照、无字幕；0 Agent、0 TTS，仅 1 条新 render | 已完成 |
| 媒体与最终数据库核对 | 19 秒／456 帧、1280×720@24fps、H.264/AAC；全解码、14 PCM 检查及 10 张成片抽帧通过；仅新增该草稿与对应 executor 1＋2 行，全部旧数据及媒体保持，alignment 写入 0 | 已完成 |
| 用户效果确认 | 查看新样片是否消除提前露线，讲解是否更容易理解；声音自然度与人工时序另行核对 | pending |

发布已为工作台和 executor 分别创建新的 immutable runtime ConfigMap；
运行指纹仅变更 renderer 的 `image_digest`，其他依赖字段逐值保持，旧 ConfigMap 保留。
共享 Agent、speech-service、OAuth、Redis、网络与存储配置不在本轮变更范围内。
批次只有 `completed`、`partial_failed`、`cancelled` 视为终态；
`blocked`、`queued`、`running`、`cancelling` 或未知状态都会阻止发布。

本轮远端证据目录为 `/root/video-connectors-20261103-r1/`。
已形成 `build/images.json`、`preflight/summary.json`、`release/summary.json`、
`release/postcheck.json`、经核对的发布恢复回执、构建／预检日志，以及 `sample/*` 的样片与媒体验收回执。
本地成片为 `D:/github/armbianbegin/.runtime/video-connectors-r4/evidence/two-scene-project-draft.mp4`，
抽帧和媒体检查证据位于 `D:/github/armbianbegin/.runtime/video-connectors-r4/evidence/media-review/`。
两份数据库的备份仅留在服务器，以 0600 权限保存，不随文档或构建包公开。

完整播放下的节奏、听感与用户对表现力的认可仍待确认，人工时序仍为 pending。
内置浏览器沿用此前阻碍，本轮没有重新点击页面或完成新的页面验收，
不据此声称工作台浏览器交互或用户效果已通过。

OpenSpec 已同步本轮实际结果，`fix-video-connector-initial-state` 校验为 `valid=true`、`issues=[]`；技术实施与记录 7 项完成，用户效果确认 1 项待审。本轮不 apply／archive，不合主线。
