# Canvas / Three.js 自由创作接入

本轮对应 OpenSpec `evolve-video-agent-creation-and-effect-library` 的 5.1、5.2。动画仍由通用 Agent / Pi 写完整源码；工程只提供可用资源、隔离渲染、时间接口和验证，不新增固定动画模板。渲染暂留 video-service，不启动公共服务拆分或多图层改造。

## 两条运行链

- 实验室：冻结实际能力与资源 → Agent 创作 → 独立 host 实拍 → 检查真实上下文、确定性与媒体 → 展示样片。Canvas / Three 使用 render request / receipt v2；历史 HTML/SVG v1 保持原有意义。
- 正式视频：单独冻结正式 renderer 能力 → production input/result v2 → creative-scene v3 → 实测旁白时钟驱动绝对 seek → H.264 导出。实验室与正式 host 身份不同，不能共用 host 摘要冒认通过。

共享 `motion_renderer/engine_runtime.mjs` 负责原生 Canvas2D / WebGL2 上下文测量与启动配置；Three 使用固定本地 0.170.0 模块及 MIT 许可，软件 SwiftShader 渲染，无外网依赖或 GPU 要求。源文件仍限 scene.html/js/css，可自主编排 HTML/SVG 与对应画布，不固定表现形式。

## 放开能力的条件

未配置引擎准入时，实验室默认只公布 HTML/SVG。离线验证可显式指定候选引擎，但不会因此在 HTTP 可用能力中出现。`--engine-admission-file` 必须指向匹配实际镜像、host、共享模块、资产的准入文件；每个新增引擎的记录必须能回查原渲染账本中通过的真实 v2 证据，之后才公布给创作者。本轮 r4 已完成准入并上线，网页现已实际公布 HTML/SVG、Canvas 2D、Three.js 三种引擎。

正式业务通过 `VIDEO_CREATIVE_RUNTIME_CAPABILITIES_FILE` 接收独立验收后生成的能力表，核对正式 runtime 的镜像、host、共享模块与资源摘要。不能把实验室能力表直接用于正式入口。已开始的委托和正式任务保留冻结的能力，不随扩容改变输入或重放身份。

## 验收范围

1. Canvas 真实 2D 上下文；Three 真实 WebGL2、SwiftShader、无 context loss / GL error。
2. 完整逐帧捕获、反向与中间位置 seek、重复 seek、静止稳定、重新加载后重复画面一致。
3. 原有无外网、无 worker/frame/自主媒体、容器只读与资源限制继续有效；错误引擎声明不能取得通过证据。
4. 实验室与正式视频各自预览和 MP4 全解码，正式音频 SHA 与实测时钟不变。
5. 新 Agent 包、镜像和工具组合重新做真实候选准入。技术通过不等于艺术效果已获认可，也不替用户发布方法。

## 实施状态

2026-11-12 状态：应用源码已推送至 `ca911ca64afd81c981edeb4a2f00e4443f6c7e7b`。本次补明确认 `source.strategy.description`、`source.strategy.tradeoffs` 均须为字符串的提示说明，继续保留原严格校验；该次定向回归为 **168 passed、2 skipped**。这是这一轮测试集合的结果，不与此前或其他组的测试数相加。

此前源码 `9e33b13775951b839ada470db5bece6718ed5c96` 的四个完整源码镜像已构建并推送，分别为 worker、creation executor、workbench、renderer，均使用 `latest@sha256` 身份。该版实验室 HTML/SVG、Canvas、Three 的真实容器协议验收，以及正式 renderer 三引擎各一次真实 K8s Job 的 render / observe 均已通过，保留独立的镜像、原输入、运行回执、停止证明和媒体证据。现用旧控制镜像 `fc85…` 经实测，相关控制源码与 task 合同相同，可以复用；这不代替新 worker、角色或渲染器的候选验收。

第一次真实 Canvas Agent 候选 `candidate-method-author-c3ca1` 已发出 **4 次模型请求**，随后因 `strategy.tradeoffs` 输出为数组被严格拒绝。该 Pod 已独立核实停止并清理；原失败、响应和停止证据保留，没有手动重写失败输出，也没有采用该候选。上面的技术协议夹具均为明确标记的手工测试输入，不能作为这次 Agent 创作成功的证据。

修复说明后的 r4 四镜像构建已经完成，实验室 HTML/SVG、Canvas、Three 各一次真实容器协议渲染，以及正式三引擎各一次真实 K8s render / observe，六项技术复验均已通过。r4 原始回执保存在下表的服务器目录；准备清单和 r3 记录不作为 r4 完成证据。

r4 的 Canvas、Three 两条真实 method-author 候选均已成功，各使用 **4 次模型请求**，原生渲染产物的 **72 帧技术验收通过**。加上前述失败候选的 4 次，截至两作者阶段累计 **12 次模型请求**；原稿字段类型的提示修复已经在真实作者输出中验证有效，没有放宽原合同校验。

剩余候选续跑先因宿主机缺少 ffmpeg，在任何新模型请求前停止；随后使用固定 renderer 容器成功解码，但 standard assess 又因 capture 文件属于 UID 10001、mode 0444，而该流程要求 root 持有原始捕获，按预期拦停。root 已独立封存两作者的原捕获，仅将 **16 个文件的 uid / gid 改为 0**；文件原字节、mode、inode、mtime 与 lookup 绑定均未改变。两条作者的 standard assess 随后通过，封存证据位于 `OP/author-capture-seal-r1`。这一步没有手改作品或伪造新的捕获证据。

`OP/agent-remaining-r4-sealed-r1/remaining-result.json` 已记录 `five_slot_technical_validation_complete`。此前六帧已实际解码；review、Canvas / Three 两条 production 及其真实 K8s render / observe 验证现均通过。本轮 **5 个新候选全部成功**，含前述历史失败在内累计 **24 次模型请求**，新模型候选阶段结束。

三条新 Agent 路由现已完成切换并全部 Ready，使用新的 r4 worker，保留经核验可复用的旧控制镜像 `fc85…`。服务入口已经恢复；数据库、executor 历史记录与凭据均保持原样。路由执行完成证据为 `PARENT/candidate-routing-apply-r4/apply-complete.json`。

三项业务部署 creation executor、workbench、motion-executor 也已全部 Ready，证据为 `OP/business-deploy-r4/deploy-completed.json`。root 持有、mode 0444 的 `/var/lib/video-animation-creation/engine-admission-ca911ca-r4.json` 已被线上实际引用；实际 Pod 内 `MotionSettings.runtime_fingerprint`、正式能力快照与 r4 原件精确一致，三引擎均可用，核对记录为 `OP/live-postflight-r4.json`。

内置浏览器已经核对三引擎可用能力、两个筛选条件的正确空态和新建实验表单，表单未提交；正式视频工作台连通，实际读取原有 **12 个项目**及已有视频记录。上线后核对仅执行读取，没有新增业务写入或模型请求，因此本轮模型请求总数仍为 **24 次，包含旧失败的 4 次**。

OpenSpec **5.1、5.2 的工程技术范围现已完成并上线验证**。艺术表现、声音效果、人工验收和方法发布均没有借本次技术验证冒认通过；当前更改**尚未合入主线**。

OpenSpec 变更 `evolve-video-agent-creation-and-effect-library` 已按 revision `b2217b3f0ca67de7c6380d09e53451d9c6cb3d45` 精确回读并通过校验。主清单现为 34 项、12 项完成、22 项待办，本轮仅新增完成 5.1、5.2；当前设计里的旧支持状态已同步，历史失败及其他任务状态保留。

本次线上实际使用的 r4 镜像如下，均保留 `latest` 标签并以完整摘要绑定；workbench 与 motion-executor 使用同一 workbench 镜像。renderer 是实际渲染任务所用镜像，worker 是三条 Agent 路由所用镜像：

| 角色 | 实际镜像身份 |
| --- | --- |
| worker | `arm-cluster-master:5000/agent-service-pi:latest@sha256:54c25ac3f208558dba53f9f232a014b8ac0d566cec0a91c6c2ef4c8dce3be5fe` |
| creation executor | `arm-cluster-master:5000/video-animation-creation:latest@sha256:f3d38af568a88cdf18cb767aef37893a2d230d7256ec94bfad3244e8b0d3c450` |
| workbench / motion-executor | `arm-cluster-master:5000/video-workbench:latest@sha256:6df092753ec5006752a524e943fd487522401d86ffaf78f8b99c3d7dc9846040` |
| renderer | `arm-cluster-master:5000/video-motion-renderer:latest@sha256:3fff8d076330a5becc876baa822de1fe9227bad19a3708848e736b3ceca19bc6` |

两条真实作者的 **3 秒技术自由创作原片**已保存到 D:，本地已核对文件大小与 SHA-256。它们用于回看原始技术输出，不代表艺术效果已经认可：

| 原片路径 | 字节数 | SHA-256 |
| --- | --- | --- |
| `D:/github/armbianbegin/.research-temp/canvas-three-r4/samples/canvas-author-r4.mp4` | 105226 | `ad46e473f899fc7b9bfa8edc354bf85303d56e164c290ead956c5679c015c6ad` |
| `D:/github/armbianbegin/.research-temp/canvas-three-r4/samples/three-author-r4.mp4` | 24246 | `e3d040f73121ad49545c4c5b9574a5faef8b97736ea5d9573bbbbdc7e548de0b` |

本轮服务器目录：`PARENT = /root/video-process-20261105/feedback-20261106/animation-capability-redesign/agent-creation-phase1/creation-product-r1`，`OP = PARENT/spec-consolidation-r1/canvas-three-r1`。以下证据路径均相对 OP，路由记录以相对路径回到 PARENT：

| 范围 | 原始记录位置 | 已证明的范围 |
| --- | --- | --- |
| r3 四镜像构建 | `full-source-release-r3/native-build-completed.json`、同目录各角色 `*-build.json`、`renderer-runtime.json` | 指定 Git 源码与完整镜像、摘要和 runtime 身份 |
| r3 实验室三引擎 | `render-admission/lab-container-admission-result.json`、`render-admission/engine-admission-v2.json`、`render-admission/runtime-capabilities.json` | 三个独立容器协议运行及原账本回查；准入文件仍是技术候选证据 |
| r3 正式三引擎 | `formal-container-r1/html-svg/`、`formal-container-r1/canvas/`、`formal-container-r1/three/` 下的 `original-k8s-render-proof.json` 及其原始输入、Job / Pod / log / artifacts | 各引擎独立 render / observe、原音频绑定、媒体解码、上下文与 seek 技术检查 |
| r3 正式能力快照 | `render-admission/formal-capabilities.json` | 由三个正式原始 K8s proof 生成的能力身份，不表示业务已部署或效果获认可 |
| c3ca1 原失败 | `../../candidate-method-author-c3ca1/candidate-output/` | 原始模型输出、4 次请求及失败／停止现场；没有成功作品或采用记录 |
| r4 四镜像与六项技术复验 | `full-source-release-r4/native-build-completed.json`、同目录各角色 `*-build.json`、`renderer-runtime.json` 及相关渲染记录 | root 已确认四镜像构建完成、实验室三次真实容器渲染与正式三次真实 K8s render / observe 通过；本地尚无服务器原始结果副本 |
| r4 两作者捕获封存与评估 | `author-capture-seal-r1/` | root 已确认真实 Canvas / Three 作者各 4 次模型请求成功、72 帧技术验收与两作者 standard assess 通过；仅封存 16 个文件的所有权元数据，原捕获内容和绑定未改 |
| r4 五候选技术验收 | `agent-remaining-r4-sealed-r1/remaining-result.json` 及同目录原始记录 | root 已确认 `five_slot_technical_validation_complete`，review、两条 production 及其真实 K8s render / observe 验证通过；5 个新候选全部成功，含旧失败累计 24 次模型请求，新模型候选阶段结束；后续上线另见下列独立证据 |
| r4 Agent 路由切换 | `../../candidate-routing-apply-r4/apply-complete.json` | root 已确认三条新路由 Ready、新 worker 生效、旧 control 保留、入口恢复；数据库、executor 历史与凭据保持原样 |
| r4 三业务上线 | `business-deploy-r4/deploy-completed.json` | root 已确认 creation executor、workbench、motion-executor Ready，新的 r4 准入文件已实际引用 |
| r4 上线后实际运行核对 | `live-postflight-r4.json` | root 已确认实际 Pod runtime 与正式能力快照精确匹配 r4、三引擎可用；浏览器另已只读核对表单、筛选空态与原有 12 项目和视频，无新模型或业务写入 |
