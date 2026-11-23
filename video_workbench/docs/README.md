# 视频服务文档导航

更新于 2026-11-24。先读[当前架构与实施边界](current-architecture.md)，再按目的查阅下表。本文是文档入口，不维护另一份任务计数或部署版本表。

本次主线合并保留2026-11-21设计档案，并纳入截至 `fb31f57` 的视频学习、场景空间与共享几何实现；[合并验证记录](mainline-scene-merge-20261124.md)分别说明源码、历史部署和未完成范围。档案中的“未实现/未合主线”按其原日期阅读。

场景空间的[实体覆盖与内部体面](scene-coverage-form.md)说明浅墨遮挡、局部山脊/谷线与皴向，以及真实像素验证边界。
新增的[共享三维网格与绘画投影](scene-shared-geometry.md)说明共同山体、固定正交视向、可见面采样及API3的证据范围；[实施验证](scene-shared-geometry-validation-20261123.md)记录真实 ARM 样图与学习 executor 更新。

同一共同网格的[笔墨疏密迭代](shared-mountain-brush-validation-20261123.md)保留新旧静态图、实际消费及仍有的画法不足；该例不改变运行时或学习默认。

后续[统一墨团与勾骨迭代](shared-mountain-unity-validation-20261123.md)记录共用光向/结构墨簇、少量焦墨和局部轮廓处理，以及近岸与松针的新旧图；仍为艺术候选。

[大墨面四稿研究](shared-mountain-bold-experiment-20261123.md)记录未达艺术目标的试验与对照；四稿均未替换35版。

当前需求与未完成任务以根仓库 `.openspec-project.json` 指向的 OpenSpec 项目为准：`ec663f5a-53d3-4a0e-9037-933eb788d183`，主要变更为 `evolve-video-agent-creation-and-effect-library`。本地设计文档解释已确认的方案；发布、真实模型执行和人工验收必须分别有证据。

2026-11-18 确认的新增目标是需求创作库、图片学习库和视频学习库三个独立成果库，共用创作、渲染、评审和同一套 WikiSkill。[图片学习首版已上线](https://video-service.panghuer.top/dev/animations/picture-library.html)：上传实际参考、直接看图编写源码、真实单帧渲染、对照迭代、换内容验证及源码保存，完整实测见[产品验收](picture-learning-product-validation-20261118.md)。

视频学习运行 r5 executor 与 r6 lab，真实内部 6 秒参考完成独立分析、两轮复现和一次换内容作品；三份 MP4 全解码、12 份产物下载摘要、6 个模型操作的图像输入覆盖和三类越权 404 通过，见[本轮验证记录](video-learning-validation-20261118.md)及[精简证据](evidence/video-learning-20261118.json)。此轮非最初 B 站原片或真实长视频，也未走候选身份绑定创建；连续运动、音频及用户艺术认可未验证。原水墨 8 个作品／24 产物摘要保持，未自动采用或发布 WikiSkill。旧参考误用、评审类型指导和详情刷新已有修复；2 项定向、17 项服务回归、14 项 UI 检查及实际浏览器候选预填、角色输入查看、刷新展开／播放通过。浏览器未重复提交付费创建；内部小样、自适应多人学习和图片 Manim 接入仍待完成。

## 当前使用与维护

| 目的 | 文档 | 阅读范围 |
| --- | --- | --- |
| 了解整体服务 | [当前架构与实施边界](current-architecture.md) | 效果库、知识库、正式制作、渲染与公共服务关系；已实现和未完成的区别 |
| 了解目标设计 | [Agent 效果库、视频工作台与 HyperFrames 统一设计](agent-creation-effect-library-plan.md) | 当前设计及历次检查点；不能将设计图中的全部节点当成已验收能力 |
| 对象空间、分层、网格与镜头公共核心 | [阶段 1 框架与可运行示例](scene-space-framework.md) | 版本化世界、绝对帧、计划修订、独立消费账本及几何预演；运行接入与像素验收另见阶段 2 |
| 空间框架接入制作、学习与效果库 | [阶段 2 接入与配置](scene-space-integration.md)、[宿主与原生像素验收](scene-space-renderer.md) | 无旁白整片共用世界、实际预演门控、学习证据、效果留存与预算；执行准入和未覆盖范围分别说明 |
| 三库与图片／视频参考学习 | [参考学习设计与实施拆分](reference-learning-libraries-design.md) | 完整目标与分阶段差距；图片已上线，视频有内部 6 秒参考产品证据，长片及内部小样、自适应多人学习待补齐 |
| 使用图片学习库 | [网页与结构化 API](picture-learning-library.md) | 看参考、生成绘制源码、渲染单帧、对照迭代与保存技法；单帧是验证产物，完整创作学习流程保持 |
| 了解视频学习实现合同 | [网页与结构化 API](video-learning-library.md) | 参考视频、独立选片、实际 MP4／源码／独立评审的实现；部署和产品结论另看验证记录 |
| 按链接学习与视频截图学习 | [按需远程参考设计](remote-reference-learning.md)、[验证记录](remote-reference-validation-20261119.md) | 共用原站链接、真实时间帧及高清局部；不长期存参考整片，Agent 可在同一预算内补看 |
| 在学习中调用水墨代码库 | [ink-landscape 与 WikiSkill](ink-landscape-wikiskill-integration.md) | 固定本地代码、手动知识快照、图片／视频调用和实际执行证据；保留自主笔路与知识开关 |
| 核对视频学习当前验证范围 | [2026-11-18 验证记录](video-learning-validation-20261118.md)、[精简证据](evidence/video-learning-20261118.json) | 技术准入、最终部署、内部参考 API 产品与浏览器技术验收；采样帧不替代连续观看或听审 |
| 核对图片真实渲染 | [源码与 PNG 验证](picture-learning-render-validation-20261118.md) | 水墨历史源码逐像素复现、Canvas／Three.js 真实渲染、新作者源码保全；不替代艺术认可 |
| 核对图片完整实验 | [真实产品循环与上线](picture-learning-product-validation-20261118.md)、[看图角色准入](picture-learning-agent-validation-20261118.md) | 两轮复现、一次换内容、实际输入和原件下载、静态知识快照；水墨质感仍可继续改善 |
| 同一效果从零重做、知识加载与技术重试 | [设计与实施拆分](fresh-rebuild-and-skill-loading-design.md)、[实现验证](fresh-rebuild-validation-20261117.md) | 效果归属与创作继承分开；知识开关、冻结恢复及候选人工采用 |
| 外部 Agent 使用效果库 | [结构化 JSON API](animation-lab-agent-api.md) | 请求、凭据边界、幂等、轮询、反馈及下载；无需模拟填写网页 |
| 理解知识库 | [整库 WikiSkill 发布](animation-knowledge-workbench.md) | 手动快照、自动编译、同产物浏览、最近五版与正式批次冻结 |
| 理解协作创作 | [通用协作与创作指导](general-creative-collaboration.md)、[视频职责协作](collaborative-effect-authoring.md) | 与绘制技术无关的调度核心，以及视频适配、预算、质量证据 |
| 理解正式修订 | [制作序列与额度账本](production-sequence-ledger.md)、[明确问题输入](film-module-revision-input.md) | 自动一轮、用户最多追加两轮及后续有界需求；不限制开发者持续实验 |
| 维护整片生产 | [HyperFrames 正式集成](hyperframes-film-production-integration.md)、[编排](hyperframes-production-run-orchestration.md) | 方案、资源、原生模块、全片工程和音频排程 |
| 源码构建与发布 | [Git 源码构建](source-build-and-release.md)、[部署参考](../deploy/README.md) | 干净 Git、普通 Dockerfile、latest 标签和 digest；部署参考中的旧阶段说明按原日期阅读 |

## 能力与底座

| 层级 | 文档 |
| --- | --- |
| HTML/SVG、Canvas 2D、Three.js 运行准入 | [Canvas / Three.js 运行时](canvas-three-runtime.md) |
| GSAP / Anime.js 动作编排 | [Anime.js 接入](animejs-authoring-integration.md)、[绘制与动作能力验证](authoring-libraries-server-validation-20261114.md) |
| Manim 预制资源 | [Manim 接入](manim-resource-integration.md) |
| HyperFrames 工程、时钟与浏览器 | [项目合同](hyperframes-film-project-contract-20261114.md)、[项目运行时](hyperframes-project-runtime-20261114.md)、[浏览器 realms](hyperframes-film-browser-realms-20261114.md) |
| 整片执行与导出 | [生命周期](hyperframes-film-lifecycle-host-20261114.md)、[执行运输](hyperframes-film-execution-transport.md)、[Kubernetes 分发](hyperframes-film-kubernetes-dispatch.md)、[离线导出](hyperframes-film-offline-export.md) |
| 音频时间与音效 | [音频时钟](audio-clock-foundation.md)、[整片混音](hyperframes-film-audio-mixer.md) |

## 实施与验收记录

以下文件保留各次真实执行范围、失败、摘要及观察局限。日期较新的记录只更新其实际覆盖的能力，不自动覆盖其他记录中的未完成项，也不表示此刻集群仍运行该镜像。

| 主题 | 证据入口 |
| --- | --- |
| 视频学习产品与浏览器技术证据 | [2026-11-18 验证记录](video-learning-validation-20261118.md)、[精简回执](evidence/video-learning-20261118.json)；内部参考范围，WikiSkill 仅离线兼容、未发布 |
| 主线合并、文档与临时产物整理 | [2026-11-16 收尾记录](mainline-cleanup-20261116.md) |
| 通用协作及非 Canvas 真实样片 | [2026-11-16 协作验证](general-collaboration-validation-20261116.md) |
| 协作水墨效果及未达到的美术目标 | [同题实验验证](collaborative-effect-validation-20261116.md) |
| 最近三条作品动作标签 | [源码识别及线上验证](collaborative-motion-label-validation-20261116.md) |
| 正片打磨代码、上线与业务验证缺口 | [正片打磨验证](production-refinement-validation-20261116.md) |
| WikiSkill 编译、发布及读取 | [整库发布](wikiskill-unified-release-validation-20261116.md)、[读取与真实整库超时](wikiskill-reader-validation-20261116.md) |
| 整片真实旁白、模块及编码 | [有声整片](hyperframes-narrated-film-validation-20261114.md)、[Job 运行](hyperframes-film-job-validation-20261114.md)、[音效定位](hyperframes-random-sound-validation-20261114.md) |
| 正式制作、联合方案及跨镜 | [正式制作](production-run-validation.md)、[联合方案](joint-plan-validation.md)、[整片连续性](film-continuity-validation.md) |
| 生命周期、交互与反馈 | [项目删除](project-lifecycle-validation.md)、[工作台交互](workspace-interaction-review.md)、[2026-11-04 反馈验证](user-feedback-validation-20261104.md) |

## 历史材料的定位

`animation-developer-lab.md`、`animation-capability-lab-redesign.md`、`animation-canvas-three-lab.md`、`animation-requirement-suite.md`、`animation-explanatory-effects.md`、`animation-feedback-*.md`、`animation-auto-evaluation.md`、`animation-evaluation-triage.md` 记录旧样例、标注和参数回归阶段。它们可用于追溯测试来源，**不定义当前效果库的主流程**；当前不是让用户调整固定动画模板。

`agent-creation-phase1-validation.md`、`agent-creation-product-validation.md`、`animation-method-lab-validation.md`、`wikiskill-rollout-validation.md`、`method-lab-task-history-through-20261115.md` 保留早期协议、准入和失败证据。`reference-*.md` 是独立研究资料，不能作为发布 Skill 的外部运行依赖。`mainline-merge-20261104.md` 只记录当次合并。

上级目录的 `CURRENT_STATUS.md`、`NEXT_STEPS.md`、`VALIDATION.md`、`HYPERFRAMES.md`、`AUDIO_PRODUCTION.md`、`EXPLANATION.md` 也按原阶段阅读；当前入口是本页及[当前架构](current-architecture.md)。旧记录中的“未上线”“尚未接入”“未合主线”和任务总数只代表记录当时。临时目录中的原始收据路径是历史来源，清理后以 Git 中保留的摘要、持久证据归档及本轮清理记录为准。

## 2026-11-21 文档整理补充：按证据日期阅读

> 本节来源：文档提交 `03369402067c5cc7fcd9571d3af7d9c81e4a39b2`（`codex/organize-video-design-docs`），其运行及交付事实对应视频分支 `faaa09affca3205ecfa46b2204f8d6aa2a017362` 的历史证据；不表示该分支新增实现已并入当前主线，也不表示当前部署状态。原主线正文按原记录日期保留。

2026-11-21 已有 WikiSkill 第 2 版的实际发布与消费记录，以及《雨霁江渚》五分钟静音试片的技术交付记录：实际 300 秒、1280×720、24 fps、7200 帧，完整视频与音频解码及静音验证通过，见[交付说明](../../docs/codex-work/operations/ink-long-video-20261120/formal/attempt8/DELIVERY.md)。旧正文中 2026-11-18 及更早的“最近编译失败／尚无新发布”等结论，继续作为各自时点和范围的历史记录读取。

该试片的艺术质量尚未获认可，完整连续五分钟的人工审美播放审查未完成；上述技术交付也不扩展为所有视频学习、声音或创作能力均已验收，更不表示此刻集群仍运行当时的镜像。

[通用场景空间、分层与镜头及渐进学习](scene-space-layer-camera-design.md)已记录为待实施设计，尚未实现或部署。完整历史设计、运行准备与评审资料见[本仓库设计档案](../../docs/codex-work/README.md)，按来源、版本和验证范围查阅。
