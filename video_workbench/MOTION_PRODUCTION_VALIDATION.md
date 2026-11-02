# 动态生产接线：本轮验证与发布记录

更新：2026-11-03（Asia/Shanghai；既有发布／operation ID 仍保留 `20261102`）。本轮按用户授权推进两个部分：发布通用 Agent 的整片讲解统筹和单镜动态图解能力，以及接通候选审阅、明确采用、实际录音时间和 MP4 制作。工作仍在独立分支 `codex/video-hyperframes-motion`，**尚未合入主线**；发布验证与主线合并分别记录。

最新应用源码为 `07bd6cab8a33ba62a5d6eb94aa10651d852197c0`（compiler v3），前序接线与正常关闭恢复提交分别为 `6f20424441ba9ea771c479f6667162433cdc83d1`、`233ffe5830dad7b439a2413cd2c00ef9d77f112c`。**第 1 部分已完成**：共享 Agent r3 已发布，两种真实业务任务成功，解释方案已原子采用。共享 Agent 已交接其他聊天，本轮不再变更。本轮已使用 **4 个上游任务／14 个模型回合，TTS 0**，任务预算用完，禁止继续提交 Agent。

**第 2 部分的本轮发布、真实草稿与采用门禁验收已完成**：r5／compiler v3 已正式发布，两 Deployment 均 1/1 Ready；真实 18 秒原声草稿 completed，完整解码、原 PCM 无截断与首尾／四段对齐检查通过。草稿完成时两个候选均未自动采用，随后显式采用原录音至 revision 6、motion 至 revision 7；estimated／pending 的正式 delivery 实际返回 HTTP 409 `motion_timing_needs_review`。人工时序、声音自然度及整片主观效果仍未通过验收，不能把媒体技术通过当作满意成片。首个草稿的 `motion_text_overflow`、r4／v2 的严格子框拒绝与原冻结 snapshot 全部保留。

此前的受控演示、失败与修复仍保存在 [HYPERFRAMES.md](HYPERFRAMES.md)，讲解方案契约见 [EXPLANATION.md](EXPLANATION.md) 和 [EXPLANATION_VALIDATION.md](EXPLANATION_VALIDATION.md)。这些文件中接线前的“尚未连接／尚未发布／调用 0”描述属于对应历史检查点；本轮生产接线状态以本文件的明确证据为准。

最终只读检查确认 `video-motion-render` namespace 已无 Job／Pod。已登录浏览器刷新后，原用户 5 个项目、八音色与新讲解／动态入口正常，未改变其内容。OpenSpec `add-hyperframes-motion-video` 已同步本轮结果，清单 13/21，格式校验 `valid=true`、`issues=[]`；未 apply／archive，未合主线。

## 实现范围与服务分工

| 组件 | 本轮职责与边界 |
| --- | --- |
| 工作台 | 保存项目、解释／motion 候选与版本、明确采用、实际录音绑定、时间核对状态和 frozen snapshot；展示审阅、草稿与历史成片。不会因任务完成而自动采用候选，也不会因草稿完成自动授予时间核对状态。 |
| 通用 Agent 服务 | `video.explanation-plan.v1` 输出两镜或整片的讲解统筹；`video.motion-plan.v1` 输出受限语义方案。模型不直接输出可任意执行的 HTML／JavaScript；旧科研、PPT、分镜与静态画面能力须逐项保留。 |
| 编译与时序模块 | 使用受限 flow／compare／focus 模板，把计划和原文 beat 编译成 HTML/SVG＋GSAP 工程；按具体录音的锚点解析时间轴，记录完整 runtime fingerprint。更换录音不必重做语义方案，但必须使用新的录音时间绑定。 |
| 独立 executor | 通过内部鉴权 API 接收 snapshot v2，持久化 owner＋operation 身份、查询／取消／恢复，串行协调镜头渲染与混合成片。工作台正常关闭不等于用户取消远端任务。 |
| 隔离 renderer Job | 使用固定 `latest@sha256` 镜像，在独立禁网 namespace 中执行 HyperFrames、seek 观察、媒体编码与产物核对；不挂业务／Agent／TTS 凭据。runtime、文件摘要和实际 Pod imageID 分别核对。 |
| 混合合成 | 保留静态镜头和动态图解的分别处理；按 frozen 时间轴合成原始 PCM 主轨，最后只编码一次 AAC，不拼接每镜已压缩 AAC。draft 对应 CLI draft，final 对应 CLI delivery；质量与运行环境参与冻结身份。 |
| speech-service | 本轮不改语音模型或项目统一音色机制。优先复用原有完整录音，不把更换动画框架称为改善声音自然度。 |

前端已经支持选定未采用候选制作草稿、显式采用、静态／动态模式切换、录音分段试听、修改锚点后保留 pending、独立核对操作、历史 MP4 播放／下载／取消。gate 关闭时历史内容与允许的取消／回到静态路径保留；生成与采用入口关闭。脏草稿和迟到响应不能覆盖当前编辑，旧录音上的草稿需要明确丢弃。

GET 时间估算只读，保持 `method=estimated`、`review_status=pending`。PATCH 锚点写入 `manual_anchors` 后仍为 pending；只有针对确切录音和 alignment SHA 的显式核对才能改变 review 状态，且保留估算／手工来源。上述实现和离线控件测试不等于真实旁白已经听审。

## 本地验证：准确批次与边界

| 批次 | 已确认结果 | 证据与限制 |
| --- | --- | --- |
| 本轮 Python 联合回归 | **161 passed** | 范围为 `test_motion_production_api.py`（当时 19 项）、`test_motion_render_execution.py`（24 项）、`test_video_build.py` 和 `test_deploy.py`。包含实际 FFmpeg 的本地混合／原 PCM 合成检查，远端 Agent、Kubernetes 和产物收集边界使用测试替身。计数来自本轮工具输出，未另保存完整日志。 |
| 正常关闭修复后的 API 定向回归 | **24 passed** | `test_motion_production_api.py` 新增关闭／恢复相关覆盖后单独重跑。与上一联合批次重叠，不将两者相加成一次全量回归。 |
| 本轮 Node 联合回归 | **82 passed** | UI、音频、解释、motion 与 renderer 契约的联合结果；前期 64 项前端结果属于其子集。计数来自本轮工具输出，未另保存完整日志。 |
| 源码检查 | 通过 | JavaScript 语法和 `git diff --check`；没有因此重新扩大付费或生产测试范围。 |
| 业务验收脚本离线冒烟 | 通过，真实 Agent／TTS 0 | 真实本地 API／SQLite＋fake Agent／renderer，覆盖两项目创建、解释提交与明确采用、原 WAV 上传／采用、motion 候选、pending 草稿与 delivery 拒绝、下载和音色保持。kubectl bridge 和 Linux 共享预算锁仍需实际运行验证。 |
| 独立 executor 技术脚本离线检查 | 通过，真实 executor 提交 0 | 真实冻结／编译函数验证 5 秒 flow＋static 的 delivery/final 快照；验证器读取本地单色 H.264/AAC 文件完成解码及 PCM 相关检查。该本地视频用于检查验证器，不是 HyperFrames／混合 renderer 的真实执行结果。 |
| v2 布局修复定向 Python 回归 | **228 passed，1 skipped** | `test_motion.py`、`test_motion_snapshot.py`、`test_motion_renderer_job.py`、`test_motion_review.py`、`test_motion_render_execution.py`；包括 v2 子文字框越界拒绝、现有动作审阅和执行边界。未通过这些本地测试提前宣称真实布局观察或发布成功。 |
| v1／v2 冻结版本专项 | **21 passed** | `test_motion_compiler_versions.py`；改动前捕获的 3 模板／18 文件 SHA 与旧 snapshot 黄金 SHA 完全保持；v2 使用同一计划／原文／录音／动作但产生新身份，重签版本篡改和交叉 compiler／template 被拒绝，executor 实际写出的 v1／v2 文件分别匹配冻结 SHA。与上一批文件不重叠。 |
| v2 接线后的业务 API 定向回归 | **24 passed** | `test_motion_production_api.py`；验证新默认编译版本接入后的业务选择、冻结、门禁及关闭／恢复边界。是本次 v2 修复后的重跑，不与历史同文件结果累加。 |
| v2 renderer 契约 Node 定向回归 | **18 passed** | `motion-renderer.test.mjs`；精确 v1／v2／demo 配对、timeline 身份与其余既有 renderer 检查。与前述历史 Node 82 项存在重叠，不累计为一次全量测试数。 |
| v3 布局与执行定向 Python 回归 | **252 passed，1 skipped** | 同上述 v2 定向的 5 个 Python 文件；v2／v3 都执行严格子框检查，保留原动作与执行检查。与 v2 批次重叠，不累计。 |
| v1／v2／v3 冻结版本专项 | **44 passed** | `test_motion_compiler_versions.py`；v1、v2 的 36 个黄金文件 SHA 和两个历史 snapshot SHA 保持；v3 同候选／原文／动作／原录音但新身份；三版本 executor 实际写入冻结字节、12 种交叉版本和重签篡改均被检查。 |
| v3 业务 API 与 renderer 定向回归 | **Python 24 passed；Node 18 passed** | 分别为 `test_motion_production_api.py` 和 `motion-renderer.test.mjs`；本次新默认 v3 的重跑，与前述同文件计数重叠，仍不能替代实际新镜像 observe。 |
| v3 独立兼容性审阅 | **12 组×6 文件逐字节相同；55 项纯内存检查通过** | v1／v2 对旧 HEAD 的独立比较保持原输出；纯内存检查验证严格边界和交叉版本。与前述测试分别记录，不当作实际浏览器或发布证据。 |

早期的 1077／155／165 等回归集合与本轮存在重叠或对应不同检查点，保留其原记录，不累计成更大的“本次总测试数”。测试目录中的合成媒体、fake Job 和 fake Pod 不作为生产执行或真实故障注入证据。

本地脚本和回执位于 `D:\github\armbianbegin\.runtime\video-motion-production\`，包括 `workbench_acceptance.py`、`workbench_acceptance_offline_smoke.json`、`executor_technical_acceptance.py` 和 `executor_technical_acceptance_offline_smoke.json`。这些私有运行材料不随文档保存凭据；session／CSRF／token 不输出到回执，摘要不包含音频／视频 base64。

## 离线浏览器验收

浏览器使用实际工作台页面、路由和 SQLite，以及固定 fake gateway／renderer。独立项目为 `2c193607-42fc-442c-b4bb-d5c8a20b3cb8`；所有 UI 行为仅发生于本地 fixture，不修改生产用户项目。

已检查：选择图解候选 1 制作未采用草稿；改选候选 2 后明确采用；修改两个相邻锚点并保存为 pending；pending 的 delivery 被服务端拒绝；分别操作离线核对控件；生成 motion＋static 的 final 请求；关闭 gate 后保留历史候选、播放与下载，关闭生成和采用入口。

fixture 共 **3 次 fake gateway 提交、2 次 fake render**，真实 Agent／模型、TTS 和部署均为 **0**。两次输入分别是 18 秒 draft 和 36 秒 mixed delivery，均返回复用的 **3 秒**既有 MP4。因此只证明 UI／API 选择、状态门禁和冻结请求身份，不能证明 18／36 秒媒体时长、音画同步、真实渲染或真人听感。离线核对控件通过，也不代表生产旁白已被人工核对。

完整无 base64 摘要为 `.runtime/video-motion-production/browser-fixture/summary.json`，原始请求记录为同目录 `evidence.json`。fixture 已停止，原监听端口 8097 已关闭。

## 真实发布与运行：当前证据

本节按实际发布回执及运行检查记录；源码、构建完成、候选评审和业务验收分别保留。镜像只构建、推送 `latest`，部署固定 digest，发布身份写标签／annotation，不自动增加版本标签。

| 项目 | 当前记录 | 真实证据与限制 |
| --- | --- | --- |
| 工作台发布 | **r5 已发布，1/1 Ready，motion／explanation gate 均为 1；真实 postcheck 通过** | 工作台与 executor 的实际 Pod digest 均匹配 r5 镜像，旧 Pod 已全部停止。r5 真实只读比较确认原 7 项目／7 表旧行全部列值与历史媒体 SHA 保持，另增两个隔离验收项目后共 9 项目；active 0、八音色保持，health／ready 200、匿名 401。共享 Agent 0 变更、`db_migration=false`、模型调用 0。 |
| Agent control 与两种新 Pi profile | **r2 失败并回滚；r3 修复发布，三组件 Ready** | r3 保留原 Pi 镜像，只重建 control。原 **18 routes／88 pools** 逐值保留，新增两组 video 路由／池，合计 20 routes／90 pools；新分配 UID 起点 46800000／46900000，各 65536，旧 46600000／46700000 预留保持。失败根因、镜像与 ConfigMap 身份见下文。 |
| 独立 executor／renderer | **r5 已发布，executor 1/1 Ready，runtime／image 绑定匹配** | r5 的 live／ready 200；未鉴权 capabilities 401、鉴权后 200。immutable runtime ConfigMap 为 `video-motion-runtime-07f2c6e407777fe9d11b`，能力回执匹配新 runtime 和 renderer 镜像。r1 已通过 15 项 RBAC；executor 固定 `orangepi5-max-server1`、20 GiB `ceph-rbd` PVC、Recreate 单副本和单 worker。限定 SA 只供 executor 使用，renderer 位于 deny-all 隔离环境，无业务凭据；实际新渲染结果另行验收。 |
| 两种发布候选 | **真实生成并独立审阅通过** | `video.explanation-plan.v1` 1 任务／4 模型回合；`video.motion-plan.v1` 1 任务／4 模型回合。分别保留候选回执和独立审阅证据；随后另以业务任务验证生产 dispatcher，不混为同一任务或重复计数。TTS 0。 |
| 两镜解释统筹业务链路 | **真实完成并原子采用，revision 6** | 业务解释候选真实 completed 后显式采用；原总稿、项目音色和两个原 scene ID 保持。业务身份见下表；没有生成新音频。 |
| 原声单镜 motion 草稿 | **首个草稿失败保留；新版真实草稿 completed、媒体技术检查通过** | 新 render `8b32626a-4956-4a4d-90c6-ac4a2b299448` 使用同一 focus 候选和原完整 18 秒录音，不追加 Agent／TTS；18 秒／432 帧 MP4 完整解码，原 PCM 保持、lag 0。完成时项目 revision 5、motion／audio head 均 null，证明草稿未自动采用；后续显式采用另行记录。原失败与 r4 严格预检拒绝均保留，没有放宽检查或改写原 snapshot。 |
| 显式采用与正式 delivery 门禁 | **真实完成；时间仍 estimated／pending** | 原音频显式采用至 revision 6；motion 候选 `8c47905b-8454-417b-909e-2357d3ba3cc4` 显式采用至 revision 7。真实 `reject-delivery --execute` 返回 `pending_delivery_rejected`、HTTP 409、`motion_timing_needs_review`，`no_human_review_fabricated=true`；未把真人旁白时间伪造为已核对，也没有为此再生成成片。 |
| 混合 final 技术夹具 | **r1 失败保留；r2 通过实际执行** | r1 选用 `chromium` 静态渲染器，与 executor 的 WeasyPrint 配置不符，未通过。r2 修正夹具配置为 WeasyPrint 后，以 `technical-mixed-final-20261102-r2` 完成 3 秒 motion＋2 秒 static 的 delivery／final，具体媒体与 PCM 结果见下表。 |
| 实际运行取消／超时／OOM／重启清理 | **待填，未通过生产故障注入验收** | 真实 Job／Pod／journal 终态、资源及临时文件清理；不能用单元测试替代。 |
| 人工质量 | **部分抽帧已检查，整片与真人旁白未通过验收** | 除 r2 技术片样本外，已目视检查新 18 秒业务草稿的 middle／last／action8-middle 三帧，样本文字未见裁切、移动与连线可见。这不构成全片布局、讲解清楚程度、动画节奏、真人时序或声音自然度通过。用户此前对原完整稿机械感的反馈保留。 |

下表保留工作台／executor／renderer 的已发布 r1 历史基线（发布身份 `video-motion-production-20261102-r1`，源码 `233ffe5830dad7b439a2413cd2c00ef9d77f112c`）及 Agent r3 交接快照；当前工作台／executor 与 renderer 绑定已更新为下文的 r5，r4 从未发布。共享 Agent 交接后的新变更由其他聊天维护，本轮不会用旧快照覆盖它。

| 对象 | 固定镜像／摘要 |
| --- | --- |
| 工作台与 executor | `arm-cluster-master:5000/video-workbench:latest@sha256:3b5310532a734b533f7294b101bee02534a287e009bd79eef607dc8d4798046f` |
| renderer | `arm-cluster-master:5000/video-motion-renderer:latest@sha256:43d7fb21f8e3d7ae81a1c215027e68ea4641a4aba7b28024fa520fb41941fb9e` |
| Agent control r3 | `arm-cluster-master:5000/agent-service-video-control:latest@sha256:4ad2742ca9cb551bfb856eaaa02c3a0e46869c0469d01c605b563d6e2b762eac` |
| Agent Pi（r3 保留） | `arm-cluster-master:5000/agent-service-video-pi:latest@sha256:96447a226fc236a95508ecc666c94782d9d9bfe490a3e8b1676337eeb07bc35e` |
| Agent r3 ConfigMap | `agent-service-config-3ee611a5cac04fdb0bf5`；配置 SHA `3ee611a5cac04fdb0bf5c0ad1ff3de2280f82fc87f6730b968038d88b30795e2` |
| 工作台 source manifest | `f8c7643e432a9c521b8f23acb95ab36c8eb0c17949636ec0c9d4386f972fbb5d`（53 文件） |
| 工作台构建归档 | `0eebd8d68b8d11cea4ef1ebb8db6a31f8d3de59143d7c6b372cc55273ce90794` |
| renderer 构建归档 | `a080f02e49cdff9a88506150329d8286ba466f58b4fdf9ee5c85f44b84e3c095` |
| runtime 文件 | `4089bc37bc378cf6c1924c644358d4d2449096930a774f7095755bfa69c4f9b0` |

构建归档与 runtime 的本地摘要已分别核对。runtime 为 HyperFrames `0.8.149`（源码 `2905162d925fc87fb8242e24ed0bbf025d307ea1`）、Node `22.20.0`、Chromium `149.0.7827.0`、FFmpeg `5.1.9-0+deb12u1`、GSAP `3.14.2`；完整字体、lockfile 和 dpkg 摘要保留在私有 `runtime.json`，不以版本字符串代替完整指纹。

r4 已构建工作台 `arm-cluster-master:5000/video-workbench:latest@sha256:9f0794c75a16abc379d34cd4a79b09edc7be9c193d5414324983f2366d830a24` 与 renderer `arm-cluster-master:5000/video-motion-renderer:latest@sha256:3f9be3dd57814f6a9847140ce33034c96e2791c1d2dc2ce97249c66b65c84ea1`，但布局严格预检未通过，没有发布。

r5 已完成构建并正式发布，发布身份为 `video-motion-layout-20261103-r5`，源码为 `07bd6cab8a33ba62a5d6eb94aa10651d852197c0`。下列精确镜像已通过预检、部署 Ready 及真实业务草稿媒体技术检查；**人工时序与整片主观质量仍待验收**：

| r5 对象 | 精确身份与结果 |
| --- | --- |
| 工作台与 executor 镜像 | `arm-cluster-master:5000/video-workbench:latest@sha256:62fb2de3121afffbf8bef1b1c7622d0a89b80cc54f1cde1d723808d01edfe8e5` |
| renderer 镜像 | `arm-cluster-master:5000/video-motion-renderer:latest@sha256:5a95c121cdd74be6fe19b9764877e5df7d08ee181860bd9974c3008585e35971` |
| 编译版本 | `motion-compiler-3`／`technical-diagrams-3` |
| 绑定新 runtime 的 manifest SHA | `ebb5e7fd68282070fc08658a5a7bd4ec99258560646d24e1137806ec42c6c175` |
| 预检状态 | `exact_r5_image_layout_preflight_passed`；禁网编译、实际 Chrome observe 与 11 个动作验证通过，严格父框 scroll／子框 scroll 和 padded-area 检查均通过 |
| 原输入保持 | 原计划、beats、alignment、动作、时间及音频不变；plan SHA `bac8010411a7b1df378c812051c19da561da5c79bddd93a37898a38fbad30974`，原完整 WAV SHA `3fa64b4a6800984b4b87719adca38a2eee533d19a5616d7a79305c08b566bce8` |
| 预检调用与写入 | 仅上述预检阶段：业务写入 0、模型调用 0、TTS 0；未新增业务草稿或 MP4。后续业务草稿写入单独记账 |
| 正式发布状态 | 发布脚本退出 0、`video_r5_rollout_ready`；工作台与 executor 均 1/1 Ready，实际 Pod digest 匹配上述工作台镜像，旧 Pod 全部停止 |
| executor 实际检查 | `/health/live`／`/health/ready` 均 200；无 token 的 capabilities 返回 401，鉴权后 200；runtime／renderer image 绑定匹配 |
| 生效配置与变更边界 | immutable ConfigMap `video-motion-runtime-07f2c6e407777fe9d11b`；motion=1、explanation=1；共享 Agent 0 变更、`db_migration=false`、模型调用 0 |
| 真实只读 postcheck | `postcheck_r5` 通过：原 7 项目／7 表旧行全部列不变、历史媒体 hash 保持，两个新增隔离项目后共 9 项目、active 0、8 voices、health／ready 200、匿名 401 |

r5 原始构建与 runtime 回执位于 master `/root/video-motion-production-20261102/video-r5-build/images.json`、同目录 `runtime.json`；精确镜像预检回执为 `/root/video-motion-production-20261102/layout-preflight-r5/summary.json`。正式发布回执位于 `/root/video-motion-production-20261102/video-r5-release/summary.json` 及同目录 Ready／executor capabilities 记录。本地预检脚本为 `.runtime/video-motion-production/preflight_video_layout_r5.py`；真实媒体回执为 `.runtime/video-motion-production/draft-media-review/report.json`。新草稿在明确采用前的只读摘要 `read-summary-before-adoption-r5` 确认 revision 5、两个 head 均 null、render completed。

### Agent r2 失败、回滚与 r3 修复

r2 control overlay 的生成进程使用 `umask 077`，造成 root-owned 源码／资源目录为 0700、文件为 0600。生产普通 UID 10000 无法搜索目录／读取文件，启动失败；该权限根因已经确认。失败后完整恢复旧 control 镜像 `sha256:10a383f64747242b83b0f226e062c3332254064f0a87bfc2bc92969939b1b9f0` 和旧 ConfigMap `agent-service-config-1d08fcef088589bdc0c5`，恢复 dispatcher Ready，没有覆盖同期 PPT 更新后的共享基线。

r3 把 overlay 源码／资源目录固定为 0755、文件为 0644，只重建 control，Pi 镜像保持。新镜像按真实 UID 10000 验证实际 CLI；使用虚构 secret、临时 SQLite、禁网环境的 health 冒烟通过，再完成生产 API／dispatcher／model-proxy 三组件 Ready 检查及工作台两个 gate=1 的 rollout。隔离冒烟不读取生产 secret，也不发送模型请求；后续两个真实业务任务分别记入原有任务预算。

共享 Agent 的第 1 部分已经完成并交接；主仓库 `D:\github\armbianbegin\.research-temp\platform-slots-coordination-video.json` 记录协调状态，独立只读回执为 master `/root/video-motion-production-20261102/agent-handoff-r3.json`。本轮后续不修改共享 Agent 的 control／Pi 镜像、ConfigMap、路由、运行池或 UID 预留，也不追加上游任务。18 routes／88 pools 的逐值保持与新增后 20／90 是 r3 发布及交接时的证据，不能当作其他聊天后续变更后的实时状态。

### 真实业务任务与失败草稿身份

| 对象 | 身份与已确认状态 |
| --- | --- |
| 解释项目 | `642d790a-351d-4e19-bb19-af7415d31aec`；完成后显式原子采用至 revision 6 |
| 解释任务 | Job `5e018b86-1cd9-4823-9048-8b5c7b8dccc2`；operation `1a0d4796-961a-45fa-8cd1-850859de42db`；candidate `8b32cd60-7de1-4bbc-946e-1f10d77e082a` |
| motion 项目 | `792f4b4d-e33f-4d29-b882-833ba5dd214b`；原声单镜，项目统一音色保持 |
| motion 任务 | Job `e047a3ab-887a-44c6-91ff-cf19aca8deaa`；operation `86fc0663-c254-4087-a301-b8bd18c83171`；candidate `8c47905b-8454-417b-909e-2357d3ba3cc4`；completed，focus／6 元素／3 关系／11 动作 |
| 上传原录音 | candidate `47f4e45a-aa5f-4087-9b82-5aae367aeaa7`；复用原完整 18 秒 WAV，不调用 TTS |
| 未采用候选草稿 | operation `e7ec88bf-a72b-450c-8c4d-546629dca56d`；snapshot SHA `308a9f71f757b0d8f351903d527f9aa9c29afa8f1ac444ccccc1b7a7e394bc4e`；最终 failed，error `motion_text_overflow`，未重试 |
| 修复后独立新草稿 | render `8b32626a-4956-4a4d-90c6-ac4a2b299448`；snapshot SHA `ecd07b47f7259a759b4d9ffad4e111ebfcd8d2ab25c8647aab0359b34f7566f6`；completed，详见下文实际媒体结果 |
| 后续显式采用 | 原录音采用后 revision 6，原 motion 候选采用后 revision 7；没有因任务或草稿 completed 自动采用 |
| pending delivery 拒绝 | 真实执行得到 HTTP 409、`motion_timing_needs_review`、`no_human_review_fabricated=true`；未写入虚构人工核对 |

首个业务草稿失败不否定先前 5 秒技术夹具的结果，也不能由技术夹具的成功掩盖。原候选、原录音与冻结身份保留；修复使用独立 compiler 版本，不放宽溢出检查或悄悄改变原候选 snapshot。

### v1 溢出诊断、r4／v2 预检拒绝与 v3 修复

对原失败输入只做禁网 observe，没有重新渲染、调用 Agent／TTS 或写入业务状态。33 个动作样本共报告 66 次父文字框溢出，来自两个对象：`session_log` 的 top 卡 300×118，clientHeight 114／scrollHeight 128；`attempt_records` 的 right 卡 300×132，clientHeight 128／scrollHeight 141。原诊断 observations SHA 为 `3a5823f6bbde693d22780816ad236d0203284ec5a1f6c280d888a8a104d81f10`。其他对象未报告父 scroll 溢出，这不等于旧版已检查所有子文字框。

随后 `motion-compiler-2`／`technical-diagrams-2` 改为固定紧凑 CSS：卡片 label 26 px／detail 18 px，padding 14 px、间距 6 px；top 上下 padding 12 px，note 纵排、上下 padding 10 px／间距 4 px。它新增每个 copy／label／可见 detail 的实际边界和 scroll 观察，同时保留原父框／画布／动作检查。

真实 r4 精确镜像禁网 observe 退出 0，但后续严格验证失败，未发布。33 个样本中的 198 个父框均无原 scroll 越界，561 个子文字框均位于 padded content area 内；其中 **495 个子框 scrollHeight 恰好比 clientHeight 大 1 px**，其余 66 个没有该差值，导致新增检查拒绝。例：top copy 高 87.09375／client 87／scroll 88，label 高 32.5／client 33／scroll 34；token 高 23.390625／client 23／scroll 24。分数行高与固定 CJK 字体墨迹的组合触发了拒绝；这次不能写成卡片已再次实际裁字，也不能仅因 observe 退出 0 而算通过。原始证据在本地 `.runtime/video-motion-production/layout-preflight-r4-observations.json`，独立诊断保存在 master `/root/video-motion-production-20261102/layout-review-diagnosis-r4/`。

源码 `07bd6cab8a33ba62a5d6eb94aa10651d852197c0` 新增默认 `motion-compiler-3`／`technical-diagrams-3`，仍保留相同严格父／子框检查，改用足够留白的整数行高：card label 26 px／行高 38 px，detail 18 px／行高 27 px；普通 card padding 14 px、间距 6 px；top padding 7 px 14 px，note padding 8 px 14 px／纵排间距 4 px；token 上下 padding 6 px／行高 27 px，caption label 21 px／行高 31 px。没有给 scroll 越界增加容差，没有删字、缩放文字或修改 slot 几何／动画／录音。

v1／v2 按冻结版本完整重建，黄金文件和独立旧 HEAD 逐字节比较均保持；旧 snapshot 从 timeline.compiler 分派，renderer／Job 只接受 v1、v2、v3 和独立 demo 的精确配对。v3 使用新的冻结身份，不改写旧失败 snapshot。私有 v2／v3 夹具分别位于 `.runtime/video-motion-production/fixed-layout-input/`、`fixed-layout-v3-input/`；原计划 SHA `bac8010411a7b1df378c812051c19da561da5c79bddd93a37898a38fbad30974` 与原音频 SHA 保持。这些本地夹具的旧 runtime 身份不能代替上述 r5 新镜像绑定 manifest；**r5 预检、正式发布及新版真实草稿的媒体技术检查已完成，人工时序及整片效果仍待验收。**

### r5 真实 18 秒业务草稿的实际媒体结果

| 检查项 | 真实结果 |
| --- | --- |
| 独立媒体状态 | `independent_business_draft_media_passed`；render `8b32626a-4956-4a4d-90c6-ac4a2b299448`，purpose／quality 均为 draft |
| 冻结身份 | snapshot SHA `ecd07b47f7259a759b4d9ffad4e111ebfcd8d2ab25c8647aab0359b34f7566f6`；alignment SHA `7575ca3fc1bc6906e31540b79abfb84a71eded3244c24307da917205327560bb` |
| MP4 | SHA `44c25b09c126ee8c9d3191d64e1dea192eb55396e5068b71d6ef455449f92d03`，340509 bytes；H.264／AAC，1280×720，24 fps，432 帧，视频／音轨／整体均 18.0 秒，完整解码通过 |
| 原完整音轨 | 原 WAV SHA `3fa64b4a6800984b4b87719adca38a2eee533d19a5616d7a79305c08b566bce8`；单声道 24000 Hz，432000 原 PCM 样本全部保留，解码 432128 样本，尾部 AAC padding 128 样本；`uncut_original_pcm=true` |
| 整段 PCM | 相关系数 0.9999526875177314，NRMSE 0.009814772931987037，lag 0；阈值相关系数 ≥ 0.99、NRMSE ≤ 0.15，未搜索偏移掩盖误差 |
| 首尾与分段 PCM | 首 2 秒 0.9999563012524834、尾 2 秒 0.9999543931939424；四个 beat 分别 0.9999557895066736／0.9999587647418566／0.9999449878243672／0.9999468225749528，lag 全为 0，均通过原录音保真检查 |
| 字幕 | snapshot／render 均关闭字幕、所有 cues 为空、无字幕流；没有执行烧录字幕 OCR 检查 |
| 抽帧与目视 | 报告留存首／中／尾和动作 0／8／10 前中后共 12 帧。报告原始 `visual_review` 仍为 pending；之后独立目视检查 middle／last／action8-middle 三帧，样本未见文字裁切，移动及连线可见，不反写成全片审美通过 |
| 人工核对边界 | `alignment_method=estimated`、`review_status=pending`、`human_voice_review=false`；媒体检查只读，业务 timing 写入 0、Agent 0、TTS 0、render 提交 0。原 PCM 对齐通过不代表估算 beat 与真人语义已人工核对 |

本地原始媒体回执为 `D:\github\armbianbegin\.runtime\video-motion-production\draft-media-review\report.json`。原报告的 `render_submissions=0` 仅指这次独立只读媒体检查，不代表前面的真实工作台渲染没有发生；completed 草稿、显式采用和 delivery 拒绝分别保留独立证据。

### r2 技术夹具的实际媒体结果

| 检查项 | 真实结果 |
| --- | --- |
| 身份 | operation `technical-mixed-final-20261102-r2`；snapshot SHA `b18d44cec659c643b1df9fa5ec54ee123f41cbeae96eefae7c85b75690b5d323` |
| 产物 | MP4 SHA `df3dd6cae142639f9e3cc9e6eb418ca91f1a09fdd0dab14e073e9ffb20d59691`，124132 bytes，执行耗时约 48.619 秒 |
| 视频 | H.264，1280×720，24 fps，120 帧；实际／视频／音频时长均为 5.0 秒，完整解码通过 |
| 音轨 | 单声道 AAC、24000 Hz；原主轨 120000 个 PCM 样本，解码 120832 个，尾部 AAC padding 832 个 |
| motion 原 PCM | 72000 样本，相关系数 0.9999749759763261，NRMSE 0.007091938370991775，lag 0 |
| static 原 PCM | 48000 样本，相关系数 0.9999984089462648，NRMSE 0.0017890240714626895，lag 0 |
| 整片原 PCM | 120000 样本，相关系数 0.9999843333421276，NRMSE 0.005608703751114743，lag 0；阈值为相关系数 ≥ 0.99、NRMSE ≤ 0.15，没有搜索偏移来隐藏对齐误差 |
| 动画技术检查 | 7 个动作通过 HyperFrames exact seek 检查；`voice_naturalness`／`visual_comprehension` 仍为 pending user review |
| 字幕 | snapshot 关闭字幕、全部 cues 为空、无字幕流；没有进行烧录字幕 OCR 检查 |
| 抽帧 | 报告留存 0.5／1.5／2.5／4 秒抽帧；报告原始 `visual_review` 仍为 pending，之后另行检查 frame-0／frame-2／frame-3 的样本结果如上，不改写为完整人工质量通过 |

真实业务验收计划保存在 master `/root/video-motion-production-20261102/business-acceptance/`。真实上游 Agent 预算为 **最多 4 个任务：2 个 explanation＋2 个 motion，包含发布候选与业务任务，失败和不确定也占额**；以同一 `candidate-budget.json` 与锁记账。截至本检查点，**4 个任务已用完：2 个发布候选＋2 个业务任务，合计 14 个模型回合，TTS 0**。任务预算不能当作模型回合数；真实计费 usage／费用仍未知。禁止继续提交 Agent，不因脚本、UI 或渲染失败再生成候选；后续只复用已有计划与原录音做有界渲染验收。

独立 executor 技术夹具另存 `/root/video-motion-production-20261102/executor-technical-acceptance/`。它不创建业务项目、不落业务 timing store、不调用 `/timing/verify`，全部报告明确 `fixture_only=true`、`known_signal_timing=true`、`human_voice_review=false`。其 `review_status=verified` 仅满足直接 executor 合成输入契约，来源是程序已知的提示音时刻，不能据此把真实旁白的 pending 改成已听审。夹具正向合成和真实旁白 delivery 拒绝是两项不同证据。

部署回执位于 master `/root/video-motion-production-20261102/executor-release/` 和 `/root/video-motion-production-20261102/workbench-release/`；后者包含发布前后数据库和私有 0600 的只读 postcheck。候选独立审阅分别位于 `/root/video-motion-production-20261102/review-explanation-r1` 和 `/root/video-motion-production-20261102/review-motion-r1`。本地 `images.json`、`runtime.json`、r2 `report.json` 位于前述私有 `.runtime/video-motion-production/` 目录；不把凭据、候选正文或媒体 payload 写入本文件。

## 容量、剩余任务与回滚

当前实现约束包括单 worker、每个动态镜头最长 60 秒／原录音最多 8 MiB、请求最多 192 MiB、renderer 归档压缩最多 6 MiB／解包最多 32 MiB。executor 的记录／待处理与 8 GiB 存储预算、20 GiB PVC 需要与业务服务自己的容量区分；SQLite JSON 的 32 MiB 项目预算也不是音频上传或 renderer 归档预算。已通过的 5 秒技术片和 18 秒真实原声草稿不能代替 60 秒组合资源实验。

本轮第 1 部分 Agent 发布与业务链路、第 2 部分 r5 发布／真实草稿／显式采用／pending delivery 拒绝均已完成。共享 Agent 已交接，无需再次发布或提交任务；草稿保持 estimated／pending，不伪造人工核对。后续独立验收事项包括：真人旁白时序、整片讲解与声音自然度；60 秒多镜 CPU／内存／墙钟／临时空间测量及 600 秒门槛；真实取消、超时、OOM 与恢复清理；有界批量草稿／批量明确采用及其 UI；换音复用语义计划和局部改图复用音频的完整实际验收；操作负担与可信成本记录。未取得真实计费 usage／单价时费用记为未知，不按离线调用 0 推算生产零成本。本检查点未合主线，未执行 OpenSpec apply／archive；文档、OpenSpec 同步及 Git 保存由本轮收尾分别记账。

回退先关闭新增任务准入，保留历史读取、取消和回到静态的路径；关闭 gate 或正常 rollout 不应伪造已接收远端任务的取消终态。保留数据库／PVC／journal／原录音和旧 immutable 镜像证据，回滚具体部署与本轮增量绑定，不覆盖其他业务的配置。

**runtime fingerprint 或 renderer digest 升级前先 drain**：等待已接收队列与在途镜头终态，或通过有证据的明确取消完成清理，再切换新 runtime／镜像。冻结任务绑定旧 runtime，不能在新指纹下冒充原任务恢复成功。

不确定提交／取消时可能按设计保留同名、`suspend=true`、零 Pod 的 fence Job，阻止迟到的原始 POST 再创建执行。它不是待重试渲染任务；不批量删除这些 fence，也不把“存在 Job 对象”当作仍有渲染 Pod。清理它需要另行核对持久 journal 和迟到提交风险，不能为整洁提前移除。
