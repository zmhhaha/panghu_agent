# HyperFrames 整片接入：现状与最小下一步

2026-11-14 更新。本文承接[统一设计](agent-creation-effect-library-plan.md)，记录整片基础与正式生产之间的接线差距。最初只读复核后的模块合同、执行运输、普通镜像及真实 Job 补验，见[本轮验证](hyperframes-film-job-validation-20261114.md)；没有把技术 Job 当成正式业务、Agent 新角色或人工验收。

## 已有基础

| 范围 | 已实现的接口与证据边界 |
| --- | --- |
| 整片冻结工程 | `hyperframes_film_project.compile_film_project()`／`verify_film_project()` 冻结模块、对象边界、原 WAV 和一个 24fps 全局时钟；需要外部可信 manifest SHA，执行状态仍为 `not_executed`。 |
| 生命周期与核心接线 | `motion_renderer/hyperframes_film.mjs` 调度 `mount/renderAt/dispose` 并连接真实 HyperFrames 定位；离线调度检查不代替实际整片媒体。 |
| 浏览器工程 | `hyperframes_film_browser.compile_film_browser_bundle()` 冻结工程及镜像自带宿主；`probeFilmBrowser()` 提供受限资源加载、定位和选定帧观察。技术探针不是全帧编码、正式快照或生产准入。 |
| 整片原声 | `hyperframes_film_audio.mix_film_audio()` 独立复核同一工程，按有理数绝对边界产生 48kHz 双声道 PCM，保留原 WAV 字节、声音速度和版本；本地已有 FFmpeg 实测。见[音轨记录](hyperframes-film-audio-mixer.md)。 |
| Skill 包 | `animation_skills_contract.py` 已有 `foundation/expression`、`SKILL.md`／附件摘要、运行要求、冻结目录与读取接口；`animation_method_store.py` 保留不可变候选及确切发布身份。 |
| 配音先行与持久编排 | `ProductionRunStore._freeze_film()` 已在全部录音完成后冻结全片时钟；现有意图、幂等和取消机制可作为新编排基础。 |

整片工程、browser bundle 与 mixer 的项目摘要、24fps 时钟及有理数音频排程相互一致，本次未发现需要重建这些合同的严重冲突。

## 正式生产接入状态

已实现并部署新路径：`ProductionRunStore(..., film_creation=...)` → 全部原 WAV → 顺序 `FilmCreationWorkflow.start_module()` → `assemble_film_production()` → 一个整片 `RemoteFilmRenderer`。模块、整片快照及媒体由现有业务 SQLite 的独立子任务表保存；父任务只持有版本引用与摘要。应用已接入同一播放器、下载和取消入口。新配置不可用时不回退旧渲染；原历史任务仍按其冻结合同读取。真实网页双镜已完成 16 秒／384 帧整片、原声核验和 387 次同源核心定位，H.3 技术范围已验；存在的纹样／投影边界跳变和未完成的审阅另记，见[真实业务验证](hyperframes-narrated-film-validation-20261114.md)。

| 接入缺口 | 需要落实的最小边界 |
| --- | --- |
| 全帧捕获与最终编码 | 离线入口已实现并以目标 ARM 环境同一三引擎工程完成 72 帧／3 秒完整导出，实际 MP4 另行探测及全解码通过；当前是技术夹具与只读源码，不是正式任务或发布准入，详见[导出记录](hyperframes-film-offline-export.md)。 |
| 整片执行快照与运输 | 新多文件快照、PVC staging、固定 CLI、`KubeFilmRenderer`、现有 journal schema 分流及 `RemoteFilmRenderer` 已实际部署。真实 Job、恢复、原声与取消／清理已验，正式新任务使用整片 snapshot。没有扩大旧七文件／ConfigMap 合同。 |
| Agent 新产物合同 | 新 `VideoAnimationFilmModuleProfile` 输出真实可组合模块、状态与可选转场；独立角色、caller、凭据及准入已启用，实际双镜 Agent 产物已停止并核验。旧 creative v5 保持原意。 |
| 一个工程的应用组装 | `FilmCreationStore` 与 `ProductionRunStore` 已接线并完成真实网页运行；原 WAV、确切模块顺序／前任、宿主目标及 runtime 在一个 snapshot 中验证，编码一次且无逐镜视频拼接。 |
| 业务与审阅 | 持久编排、历史媒体读取、取消、项目删除保护与页面模块状态已实现并由本地组合测试覆盖。真实完整媒体的独立整片审阅、一次修订和交付仍待完成。静帧审阅不代替整片运动或声音观察，内部完成不写成人工认可。 |

## 推荐次序与最小真实交付闭环

1. 先收敛当前 H.2 上线及 H.3 浏览器宿主／音轨基础的错误回归和确切发布身份。
2. 离线整片导出入口已按以下闭环完成技术验证：已冻结工程 → 一个 HyperFrames 核心与全局时钟 → 全帧捕获 → 一次编码 → 同包原声混合 → 一个可播放 MP4。覆盖三个场景及其边界，核验完整帧数、48kHz PCM 样本数、音画零起点、完整解码、资源与清理。交付回执绑定项目、bundle、原 WAV、混音回执和最终媒体摘要；AAC 编码填充按实际探测单独记录，不以缩短旁白掩盖差异。技术纯音／图形不替代真实 Agent 与讲解内容。
3. 新模块独立真实准入、执行 API 配置／发布及实际工作台成片已完成。RWO PVC 明确同节点约束，配置普通文件以只读 subPath 挂载。后续 Manim／Anime.js 使用独立版本与准入，不改写本次冻结身份。
4. 继续补完整媒体的独立审阅与一次修订、真实已发布方法的按需消费，以及用户追加优化；改善实际边界出现的外观重新解释。技术成功、实际观察、开发者认可、Skill 发布和用户采用分别留证。

H.4 复用已有 Skill 协议，不另建一套包或发布系统。补齐新的工程／引擎基础 Skill 内容及验证证据即可；2.3 继续负责真实认可发布，2.4 负责主动发现／读取。当前目录主要按引擎兼容性过滤，新创作者仍须收到明确的新生命周期要求，不能把旧 `seek(t)` 用法误认成新模块合同。

## 工作台配置

新路径使用独立的 `VIDEO_FILM_MODULE_AGENT_URL`、`VIDEO_FILM_MODULE_AGENT_TOKEN_FILE` 和 `VIDEO_FILM_MODULE_PROFILE_REVISION=1`，不借用旧作者凭据或角色准入。`VIDEO_FILM_RENDER_URL` 与 `VIDEO_FILM_RENDER_TOKEN_FILE` 明确指定整片执行 API；业务 Pod 不获得 Kubernetes 凭据。

`VIDEO_FILM_PRODUCTION_RELEASE_FILE` 为普通、只读、绝对路径的 JSON 文件，schema 为 `video.film-production-release.v1`，精确包含 `image`、`runtime_fingerprint`、`runtime_capabilities`、`trusted_host_sha256`、`execution_admission`。最后一项由运维依据真实 Job／媒体／取消证据提供，业务代码不自动授予准入。`runtime_capabilities.runtime_identity.host_sha256` 绑定六个生命周期宿主摘要 map 的 canonical digest；不借用旧单镜宿主身份。工作台镜像自带六个宿主文件须与配置逐一匹配。资源从 `VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY` 的普通文件按固定名称与摘要读取，不接受请求指定的宿主或外部脚本。

`VIDEO_FILM_PRODUCTION_ENABLED=1` 开启新任务；配置存在但关闭时保留历史查询、已提交任务恢复与取消。新准入前同时复核 Agent profile 和执行 API 公布的确切 image／runtime／宿主／技术准入摘要，失败时不开始配音。当前完整导出上限为 60 秒，计划早检与实测录音后检查分别执行；应用重启只中断本地等待，保留原远程任务身份，不伪造用户取消。

即使移除新准入的 release 文件，应用也会检测已有整片子任务或新模式父任务，构造只读／排空模式。历史媒体继续经过原 owner 和回收站校验；已提交任务可用保留的专属连接按自身冻结配置恢复，缺少连接时维持待确认，不冒充停止。没有当前 release 时禁止新子任务，不借用旧角色／渲染器。后台模块轮询和整片渲染分别使用一个有界 worker，整片编码等待不阻塞其他项目动画任务的取消。

## 真实新模块候选

以下保存上线前独立候选与静音技术时钟的历史范围；当前上线及真实讲解双镜状态以“正式生产接入状态”和真实业务验证文档为准。

2026-11-14，精确源码 `fe215a5cee719f6d9e0e895a0de5bb6bce3caf82` 普通构建 control／Pi 镜像；候选仅使用 `agent-service-pi:latest@sha256:ed08fd7abbb6f58bcaa2383bce123790794830ba30eec0e5cf2d4414c579eff5`。在独立禁用角色下完成一次原创 Canvas 模块，6 次真实模型请求全部返回 200，真实 `skill_search` 和根 `SKILL.md` 读取、双 artifact、原请求绑定及 worker 停止证据均通过。proxy／worker 均零重启、正常退出，原候选资源和临时凭据已清理，未创建公共路由准入或方法发布。

原始模块包文件 SHA 为 `8eab1256674c4f8bb19a8dbb56bc7d3f82c979b950781265df7396f61c066e4b`，结果 SHA 为 `7eb5bb5434698d041402039f7c10e3df6e6b616ba63fe03f793a139f053e27c8`，Canvas 源码 `modules/technical_canvas_flow_r1/flow.mjs` 的 SHA 为 `ae7facda0dd1ab2ab61ab6b14153c003fcff916e22348d15222bab7d52a4baa6`。原始证据在既定运维目录 `candidate-film-module-r1/candidate-output`，包含请求、实际工具事件、响应 ledger、Pod 与清理回执。技术 WAV 复用先前 3 秒录音时钟，operator-only Skill 是原创技术夹具；没有新增 TTS，不声称实际讲解、审美／迁移认可或业务 Skill 已发布。此候选预算没有渲染，后续整片渲染单独记账与核验。

另一个独立预算仅执行一个整片 Job，原始 Agent 源码不改写、不补手工动画，经 `assemble_film_production()` 生成 snapshot `9fdb4873885e61a782de2aabf863dfc514c6df07716ba3f31d61ebe07eaa8bed`。固定镜像 `701fea47…` 完成 1280×720、24fps、3 秒／72 帧导出，Job 约 98 秒；这是技术工程编码耗时，不是制作总时长或性能达标证明。使用禁止再次 export 的恢复检查取回原输出，Job／Pods、helper 与仅本次创建的策略均确认清理，原 Deployment／PVC 未变。

MP4 文件 SHA 为 `b2e182ae05a9991beb53683224c4ec198b5cfa5d315d2bfb664cd9af9521b6e4`。独立 Windows FFmpeg 7.1 完整解码确认 72 个不同视频帧、音画零起点与 3 秒、48kHz 双声道及 AAC 384 样本填充；外部 snapshot／原 JS／原 WAV 身份也已重验。原 WAV `cb285321…` 的 72000 个 24kHz PCM 样本全零，实际解码也全零，明确记为 `technical_silence`；相关系数为空不能当成声音一致性证据，更不能算真实旁白、音效或试听通过。独立回执内容摘要为 `1cddb5d70f400c13455873836dcba156e2056e9ecf48f9e9357fa8b8b9ba4f81`，D 盘既有 scratch 下 `original-canvas-film-evidence-r1/independent-validation.json`；初版非零音轨假设和一次时间参数格式的分析失败均保留，仅重做现成媒体分析，没有重渲染。

源码接线首先推送 `983d0da`。当时本地专项分别通过业务／Remote 50 项、真实线程调度 3 项、整片编排 9 项、工作台 HTTP 8 项和前端状态 19 项；另有旧编排和核心合同回归，存在交叉，不累加为唯一测试总数。当时仅完成技术原创模块到整片导出，未部署、未验证真实讲解与多镜交接。后续完整源码 `0f68475` 已部署并补齐真实双镜及同源定位，H.3 技术范围完成；完整审阅／修订、开发者认可与 Skill 发布仍独立保留。

## 音频与性能边界

新 mixer 输出 48kHz 双声道；旧 `MixedMotionRenderer` 强制 24kHz 单声道并重新拼接，不能绕回该旧合成器来消费新的交付轨。后续边界应重新校验 mixer 回执摘要、项目身份、WAV SHA／格式／样本数；不使用音频变速、截断旁白或以 `-shortest` 掩盖不匹配。浏览器不提供原 WAV 是职责划分：音轨由同一冻结工程独立消费，而不是浏览器自行播放。

本轮 mixer 明确 `sound_effects_mixed=false`，尚未实现统一音效事件与多轨混音；无旁白与无声音不能混为一谈。已有原声音轨验证不代表声音自然度或音效表现已获认可。

全片逐帧重建模块／realm、重复模块导入、Canvas 状态重绘及 Three/WebGL 资源创建的实际耗时、峰值内存、上下文回收、长片输出体积与取消延迟尚未系统测量。探针包含逐帧空闲稳定性等待，直接用于完整导出会增加耗时；必须保留正确性检查并实测后选择合适捕获策略，不能先假定实时或近实时。目标 ARM 镜像的 FFmpeg、Chromium、HyperFrames 及软件／硬件 WebGL 组合也须单独绑定和验证，本地已有 FFmpeg 的通过不是目标环境准入。
