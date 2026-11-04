# 视频渲染故障恢复与工作台启动修复：验收记录

更新：2026-11-03（Asia/Shanghai）。本轮源码为
`9cd39e84aba496b81fda231383411259525835a3`，位于独立分支
`codex/video-hyperframes-motion`，**尚未合入主线**。

当前已完成源码修复、本地自动回归、新应用镜像构建、两镜精确镜像禁网预检及线上发布／只读回归。
工作台与 executor 均 Ready、0 重启，原 15／2 张表的 schema、旧行各列与历史媒体保持，零新增。
受控续接已用完 **6 个 Job 总预算**：60 秒三镜技术组合、真实运行取消、注入超时及其精确清理通过。
最后一个用例实际为 exit 137／reason `Error`，没有 `OOMKilled`，**OOM 专用归因未通过，原整轮 failed 保留**。
全部所属 Job／Pod／ConfigMap 最终消失，最终 17 表旧 schema、主键、全部旧 typed 值与历史媒体保持，零新增、active 0。
真实强杀 executor／API 中断、浏览器点击／播放及人工听感／时序／表现力仍为 pending。
本轮 Agent 调用 0、TTS 调用 0，不重新生成文稿、计划或语音。

## 已修复的问题

### 输出写入失败后的资源清理

原实现先把场景输出写到 `output.pending`，再替换正式文件、写入证据并清理 Kubernetes 资源。
如果输出或证据写入遭遇 `OSError`／ENOSPC，异常可能绕过场景清理；外层 operation 被记为 failed，
其 Job、ConfigMap 或半文件仍然存在。worker 只恢复 queued／running operation，因而不再处理该失败行。

现在场景和整项 operation 都使用原有 journal 的状态／result 字段记录内部 `cleanup_pending` 意图，
不新增数据库表或列：

- 先持久化待清理意图及最终要返回的状态和错误，再按原冻结身份清理资源。
- 清理确认完成前，operation 保持可恢复状态；Kubernetes 不可用、绑定冲突、UID 冲突或本地清理失败时，
  保留意图并等待恢复，不提前宣称 failed／cancelled／timed_out 已清理完毕。
- 重启后发现清理意图，直接恢复清理；不重新走渲染提交路径，不重新提交原 renderer Job。
- 输出或证据写入失败使用固定的 `motion_execution_storage_error`，避免暴露文件路径、凭据或异常原文。
  失败场景的 `output.pending`、未通过的 `output.mp4`／`evidence.json`，以及该 operation 的合成 assets、
  未完成成片与报告在确认清理时删除。
- 已完成的历史场景媒体与证据保留；成功 operation 的最终 MP4 和报告保留。
  内部清理 envelope 不通过公开 operation API 当作结果返回。

场景完成路径也先清理、校验缓存媒体摘要，再持久化 completed；清理恢复时发现缓存损坏会失败，
不会把损坏文件返回为成功媒体。

### 严格资源归属与恢复顺序

清理仍要求资源匹配冻结输入、运行镜像和完整绑定定义。
Job 与 ConfigMap 的 DELETE 使用刚查询到的确切 Kubernetes UID 作为 precondition，
不以裸名称强制删除，不把 UID／绑定冲突当作删除成功。
Job／Pod 必须观察到消失；ConfigMap 不能只凭 DELETE 返回成功便认为已删除，还要再次 GET 确认不存在。

对原提交结果不确定且 Job 暂时不存在的情况，继续使用原来的同名 suspended fence：
它只预留名称、不运行 Pod，阻止迟到的原提交创建渲染工作负载。
此类 fence 按既有设计保留；不会为清理而再次执行渲染。

队列优先恢复存在 submitting／running／cleanup_pending 场景句柄的 operation，以及持有整项清理意图的 operation，
之后才启动新的 queued operation。这同时覆盖服务重启，以及 API 暂时不可用、operation 已回到 queued
但原 Job 尚在运行的情况，避免因轮换队列又启动另一个 renderer Job。
无法确认资源安全时，新任务可能继续等待；不通过放弃原句柄来释放这个约束。

本轮没有全局历史清理扫描：worker 仍只调度 queued／running operation，
不会扫描并重写所有旧 failed／cancelled／timed_out／completed operation，也不追溯删除历史孤儿资源。
恢复中的 operation 只清理自身已有的确切场景句柄。

### 工作台跨面板递归

工作台的讲解、动态图解和批量制作面板互相查询忙碌状态。
原状态查询默认重新构造父级 context，父级 context 又查询其他面板，形成循环调用，
可能在页面启动或切换项目时发生递归。
此前各面板单测注入了已经构造好的 context，未覆盖真实应用入口的这一调用环。

现在应用在跨面板查询状态时显式传入 `projectId`；动态图解查询同时接受当前项目的 scene fallback，
在元数据尚未加载时也能判断运行状态。这些查询读取对应项目的缓存与状态，不再为查询重建彼此的 context。
新增回归实际加载应用入口与三个面板，覆盖启动、切换项目、迟到读取和跨面板忙碌判断。
离线夹具仅允许 GET，不产生制作提交；真实浏览器验收仍待完成。

## 已完成的本地验证

| 批次 | 实际结果 | 范围与限制 |
| --- | --- | --- |
| 本轮 Python 广回归 | **503 passed，1 skipped** | 覆盖本轮工作台与渲染相关回归。跳过项是既有 Windows symlink 权限测试。 |
| 最终 execution 定向回归 | **44 passed** | 包含最后补充的恢复优先队列测试；与广回归范围重叠，不与 503 相加。 |
| 本轮 Node 回归 | **106 passed** | 包含真实应用入口与三个面板协作的离线回归；不代表真实浏览器已经通过。 |
| 源码差异检查 | 通过 | `git diff --check`；没有用静态检查替代资源或媒体验收。 |

execution 定向回归覆盖输出与证据两个 ENOSPC 落点、半文件清理、清理 API 失联后的重启恢复、
不新增 Job POST、成功媒体保留、损坏完成缓存、ConfigMap 删除后的不存在确认、外来 Job／ConfigMap 拒删、
UID 冲突、取消、deadline／OOM、内部清理记录不公开、整项任务清理恢复，以及新任务等待已有句柄恢复。
测试还覆盖 SQLite 本身无法写入清理意图时保留 running 身份，存储恢复后沿用原 Job 而非重新提交。

上述故障测试主要使用临时 SQLite 与 FakeKube 等测试替身；已有本地 FFmpeg／原 PCM 合成测试照常执行。
没有向生产 Agent、TTS 或业务渲染 API 提交这些故障夹具。

## 镜像与精确镜像预检

本轮发布身份为 `video-render-hardening-20261103-r1`。镜像只使用 `latest` 标签，
运行身份固定到 digest；不额外创建提交号或版本镜像标签。

| 对象 | 已确认身份／状态 |
| --- | --- |
| 工作台与 executor 新应用镜像 | `arm-cluster-master:5000/video-workbench:latest@sha256:7fc022866ad35f0c06d7078998a6fe58f7b91ca2fb59802d3a1e79b7a14efbc2` |
| renderer 镜像 | 保持 `arm-cluster-master:5000/video-motion-renderer:latest@sha256:9d4e1665aca99cd947b1f4d6de2b8883c87d8c26a67d40aa3c9af69e7508cbf8` |
| 两镜精确镜像禁网预检 | **通过**，`exact_lifecycle_image_preflight_passed` |
| 线上 rollout 与只读 postcheck | **通过**，`video_lifecycle_read_only_postcheck_passed` |

两镜预检复用原计划、节拍、alignment 和完整录音，使用上述确切应用／renderer 镜像，
在禁网环境中编译、执行浏览器观察并严格审查。编译器 4 的初始隐藏、动作及返回首帧检查继续生效。
这证明新应用镜像与原 renderer 的编译／观察契约兼容，不能证明 Kubernetes 故障清理、
60 秒容量或线上浏览器完整播放通过，也不把预检当作新业务成片。

服务器证据根目录为 `/root/video-render-hardening-20261103-r1/`，
构建身份位于 `build/images.json`，禁网预检回执位于 `preflight/summary.json`。
业务数据库保留在服务器，不下载到本地。发布先在各 SQLite `BEGIN IMMEDIATE` 锁内即时备份，再确认旧 Pod 实际停止；仅修改两个视频 Deployment 的应用镜像与发布身份。原 renderer、runtime ConfigMap `video-motion-runtime-5855d029265601b68cc4` 与所有依赖 spec 保持。

实际新 Pod 为工作台 `video-workbench-778856f9b8-bd2c6`、executor `video-motion-executor-86f7bc9475-h4xhp`。发布后动态逐表核对工作台 15 表、executor 2 表的 schema、真实主键、全部旧行 typed 值及媒体摘要，零新增；active 0，八音色、health／ready 200、匿名 401。回执为 `release/postcheck.json`。共享 Agent、speech、认证、域名和 PVC 未改。

## 首轮独立验收失败记录

运行 `render-hardening-20261103` 使用独立 `/tmp` journal 与技术提示音，夹具状态未写入生产数据库。节点实际可用内存在开始时 master 约 4.05GiB、orangepi 约 2.65GiB；各节点 capacity／allocatable 和 kubelet 实际 availableBytes 均保存。

首个 ConfigMap 写入后，验收包装器 `AuditedKube.emit` 同时显式传 `case` 并展开含 `case` 的事件，抛出 Python 重复关键字错误。收尾记录事件时触发同一错误，因而停止于 `cleanup_pending`。该首轮真实 renderer Job／Pod 提交数为 **0**，原失败、claim、驱动 stderr 与 report 保留，不反写为成功。该问题属于验收工具，不能把它写成服务渲染能力不支持 60 秒。

恢复首试尝试列举 ConfigMap，被现有 RBAC 拒绝；该失败同样保留，没有扩大 RBAC。
随后只针对记录中的唯一技术 ConfigMap，按原 UID／binding 执行 GET／DELETE 并确认清理。
受控续接另留明确记录，不覆盖首轮失败，也不绕过总 Job 预算。

## 受控续接：容量与故障用例的实际结果

续接运行标识为 `render-hardening-20261103-reviewed`，使用独立 `/tmp` journal、固定技术计划和
本地生成的 24kHz 提示音，生产工作台／executor 两份数据库均未写入、未下载。
本轮真实使用 6 个 renderer Job：容量 3 个、取消 1 个、超时 1 个、OOM 注入 1 个；
串行执行，预算已用完，最后一个非预期结果出现后停止，没有追加 Job 或自动重试。

| 用例 | 实际结果 | 时间与边界 |
| --- | --- | --- |
| 60 秒三镜组合 | **通过**。flow／compare／focus 各 20 秒，3 个正常 renderer Job；实际编译、渲染、observe 与合成完成。 | 调度至合成总计 **241.583171 秒**，在 600 秒总预算内；含独立媒体检查为 **252.787027 秒**。不将各镜预算相加代替整片预算，也未实际触发 600 秒超预算停止路径。 |
| 真实运行取消 | **通过**。观察 renderer Running 后设置取消，精确清理通过。 | 用例总耗时 **17.763496 秒**；不把总耗时称为仅取消请求的响应延迟。 |
| 注入超时 | **通过**。仅受控夹具把 `MOTION_TIMEOUT_SECONDS` 设为 `1`，真实返回超时并精确清理。 | 用例总耗时 **7.777219 秒**；不代表正常生产默认预算下自然发生的超时。 |
| OOM 专用归因 | **未通过**。第 6 个 Job 的容器实际 exit `137`、reason `Error`，服务返回 `motion_execution_failed`／HTTP `502`。 | 没有 `OOMKilled`，不能因 exit 137 或注入分配行为就认定 `motion_execution_oom` 已验证；一般失败后的精确清理完成。 |

原整轮 `summary.json`／driver report 状态仍为 **failed**，不改写成全通过。
另存 `technical-summary.json`，状态为 `partial_passed_original_fixture_failed`，
只归纳已通过的容量／取消／超时及未验证的 OOM 专用归因。
全部本轮所属 Job、Pod、ConfigMap 最终已确认消失。

### 容量媒体与资源记录

| 项目 | 已确认值 |
| --- | --- |
| 成片 | 60 秒，1280×720，24fps，**1440 个实际解码帧**，H.264／AAC，24kHz mono，无字幕 |
| 文件大小 | **774167 B** |
| 媒体完整性 | 完整 ffmpeg decode 与 ffprobe `count_frames` 通过 |
| MP4 SHA-256 | `3ca5ac5c9d66870cb331f81b492cc132ac6da7142d64b1d8e6e6004c06fed78d` |
| 指标完整性 | `metrics_complete=true`；三个容量镜头各有 **25 个 kubelet 样本** |
| renderer cgroup 累计 CPU 时间 | **277.299919 秒**；累计 CPU 时间与 wall clock 是不同度量 |
| renderer cgroup 记录的最大内存 | **415408128 B**，约 **396 MiB** |
| scratch 间隔采样观察最大值 | **43171840 B**；采样值不等于未遗漏的瞬时真实峰值 |

这些结果证明本次固定技术计划与上述镜像／节点环境下的 60 秒三镜组合可以在预算内完成。
不将其推广为任意复杂内容、所有并发量或所有节点内存压力下的容量保证。
技术提示音、可解码媒体和运行时观察也不证明真人旁白自然度、人工时序或主观表现力通过。

准确回执位于服务器根目录下
`acceptance-results/render-hardening-20261103-reviewed/`：原 `summary.json`、`driver-evidence.json`，
以及另存的 `technical-summary.json`、`resource-summary.json`。
最终只读核对回执为 `final-check/summary.json`，状态
`final_old_rows_media_and_video_runtime_verified`：工作台 15 表、executor 2 表的旧 schema、真实主键、
全部旧行 typed 值与历史媒体保持，`additional_rows=0`、active 0、八音色与依赖 spec 保持。
生产两数据库没有写入或下载；新媒体只属于独立验收夹具，没有新增业务 render 行。

两个 `/tmp` 夹具根已在原 JSON 字节和媒体归档后精确清理，见 `tmp-cleanup.json`：
重新核对 executor UID、原 report／driver 摘要、驱动退出及全部记录资源不存在，
仅删除这两个专用目录，没有执行额外 Kubernetes DELETE、渲染或业务数据库操作。

安全证据归档已下载至本地 `.runtime/video-render-hardening/evidence/` 并安全解包，
共 **84 文件／1813484 B**，归档 SHA-256 为
`3bbcd5b52fd731744a3a3bb76cb8b3f01b67da04dee16aedbf6b8e02f5670f34`。
整包大小／摘要和 manifest 每文件大小／摘要均通过，拒绝绝对路径、链接与路径穿越；
归档不包含数据库或凭据。回执为 `safe-evidence-export.json` 与本地 `local-verification.json`。
助手目视第 0／27／40 秒三张技术抽帧，两个镜头起始未见提前连接线，所查标题和要点未裁切；
这不替代整片播放、真人旁白听审或用户审美认可。

官方浏览器工具在发布后返回 `No browser is available`，按技能检查浏览器列表为空；发布前故障截图不可充当发布后通过证据。只读浏览器点击和播放尚未执行，见本地 `.runtime/video-connectors-r4/ui-audit-20261103/post-release-browser-blocked.json`。

## 仍待验证与明确边界

| 项目 | 当前状态与所需证据 |
| --- | --- |
| 线上发布完成 | **通过**。工作台／executor Ready、实际 imageID、旧 Pod 停止、鉴权、runtime 绑定与全库旧行／媒体只读回归通过。 |
| 真实取消／注入超时与精确清理 | **通过本轮受控用例**。用例结果和所属 Job／Pod／ConfigMap 最终不存在已确认。 |
| 60 秒容量与 600 秒预算 | **通过本次三镜技术组合**。媒体、wall clock、CPU／内存／scratch 与采样完整性见上文；真实超预算停止路径未触发。 |
| OOM 专用归因 | **未通过**。实际是 exit 137／reason Error 和一般失败 502，没有 OOMKilled；原整轮 failed 保留，6 Job 预算用完，不追加验证。 |
| 强杀 executor／API 中断恢复 | **pending**。本轮未做真实故障注入，不能以本地 FakeKube／SQLite 恢复测试替代。 |
| 临时夹具清理与安全证据归档 | **通过**。全部所属 Kubernetes 资源不存在；两个专用 `/tmp` 根已精确清理，84 文件无数据库归档下载并逐文件校验通过。 |
| 真实浏览器与播放 | **pending**。需实际启动／切换项目、检查运行互斥和恢复入口、播放／跳转／下载及截图；Node 106 项不替代该验收。 |
| 人工效果 | **pending**。旁白自然度、真实时序、节奏和讲解清楚程度沿用既有待审状态；本轮不修改听审结论。 |
| 历史终态与全服务存储管理 | 未实施全局终态回扫或长期容量回收；历史孤儿和保存策略需要另行梳理，不因新增 cleanup_pending 而视为已解决。 |

资源定义恢复改用独立临时目录，避免清理还必须向满的执行 PVC 重写输入。
但 SQLite 或临时目录本身完全满时，不能保证立刻记录新意图或重建资源定义：
worker 保留原 running／queued 身份，等待存储恢复后继续；不能声称全盘满时仍可即时可靠清理。
Job POST 之前必须已持久化 submitting，因此 journal 写失败不会成为盲目重投原渲染的理由。

本文件记录源码与已完成证据；后续线上实际结果应逐项补充原始回执、准确身份和边界，
不提前勾选 pending 项，也不把互相重叠的测试批次累加。
