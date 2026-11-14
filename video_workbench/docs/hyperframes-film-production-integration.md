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

## 正式生产尚未接通

当前新制作仍走 `ProductionRunStore._step()` → 逐镜 `motion.start_candidate()` → `Production.start_candidate_render()` → creative snapshot v4 → `MixedMotionRenderer`／`KubeSceneRenderer` 逐镜编码与 MP4 拼接。它接收的是单镜源码及交接上下文，不能记为统一整片工程已交付。

| 接入缺口 | 需要落实的最小边界 |
| --- | --- |
| 全帧捕获与最终编码 | 离线入口已实现并以目标 ARM 环境同一三引擎工程完成 72 帧／3 秒完整导出，实际 MP4 另行探测及全解码通过；当前是技术夹具与只读源码，不是正式任务或发布准入，详见[导出记录](hyperframes-film-offline-export.md)。 |
| 整片执行快照与运输 | 新多文件快照、PVC staging、固定 CLI、`KubeFilmRenderer`、现有 journal schema 分流及 `RemoteFilmRenderer` 已实现。固定镜像的真实 Job、结果恢复、原声与取消／清理已单独验证；新执行 API 尚未部署启用，业务仍调用旧 motion snapshot。没有扩大旧七文件／ConfigMap 合同。 |
| Agent 新产物合同 | 新 `VideoAnimationFilmModuleProfile` 输出真实可组合模块、状态与可选转场，复用宿主 Skill 工具证据；网关、资源及禁用候选目录已实现，尚未完成新角色真实产物准入。旧 creative v5 保持原意。 |
| 一个工程的应用组装 | `assemble_film_production()` 核对确切模块顺序、前任、宿主目标、录音及 runtime 后产生一个工程／snapshot；这是可调用底座，尚未由 `ProductionRunStore` 调度。 |
| 业务与审阅 | 新编排组装一个工程并提交整片执行；实际完整媒体进入独立审阅、默认最多一轮修订与交付。静帧审阅不代替整片运动或声音观察，内部完成不写成人工认可。 |

## 推荐次序与最小真实交付闭环

1. 先收敛当前 H.2 上线及 H.3 浏览器宿主／音轨基础的错误回归和确切发布身份。
2. 离线整片导出入口已按以下闭环完成技术验证：已冻结工程 → 一个 HyperFrames 核心与全局时钟 → 全帧捕获 → 一次编码 → 同包原声混合 → 一个可播放 MP4。覆盖三个场景及其边界，核验完整帧数、48kHz PCM 样本数、音画零起点、完整解码、资源与清理。交付回执绑定项目、bundle、原 WAV、混音回执和最终媒体摘要；AAC 编码填充按实际探测单独记录，不以缩短旁白掩盖差异。技术纯音／图形不替代真实 Agent 与讲解内容。
3. 新模块合同、组装、HTTP 客户端与执行 journal 接线已实现，普通镜像及固定 Job 的环境补验已完成。接下来完成新 Agent 模块真实产物准入、新执行 API 配置／发布及 `ProductionRunStore` 制作路径；RWO PVC 明确同节点约束，配置普通文件以只读 subPath 挂载。未准入时不直接替换正式生产。
4. 用真实新内容跑通策划确认、真实录音、Skill 按需读取、模块创作、统一工程导出、独立整片审阅与一次修订；随后做网页播放、版本恢复和下载检查。技术成功、实际观察、开发者认可、Skill 发布和用户采用分别留证。

H.4 复用已有 Skill 协议，不另建一套包或发布系统。补齐新的工程／引擎基础 Skill 内容及验证证据即可；2.3 继续负责真实认可发布，2.4 负责主动发现／读取。当前目录主要按引擎兼容性过滤，新创作者仍须收到明确的新生命周期要求，不能把旧 `seek(t)` 用法误认成新模块合同。

## 音频与性能边界

新 mixer 输出 48kHz 双声道；旧 `MixedMotionRenderer` 强制 24kHz 单声道并重新拼接，不能绕回该旧合成器来消费新的交付轨。后续边界应重新校验 mixer 回执摘要、项目身份、WAV SHA／格式／样本数；不使用音频变速、截断旁白或以 `-shortest` 掩盖不匹配。浏览器不提供原 WAV 是职责划分：音轨由同一冻结工程独立消费，而不是浏览器自行播放。

本轮 mixer 明确 `sound_effects_mixed=false`，尚未实现统一音效事件与多轨混音；无旁白与无声音不能混为一谈。已有原声音轨验证不代表声音自然度或音效表现已获认可。

全片逐帧重建模块／realm、重复模块导入、Canvas 状态重绘及 Three/WebGL 资源创建的实际耗时、峰值内存、上下文回收、长片输出体积与取消延迟尚未系统测量。探针包含逐帧空闲稳定性等待，直接用于完整导出会增加耗时；必须保留正确性检查并实测后选择合适捕获策略，不能先假定实时或近实时。目标 ARM 镜像的 FFmpeg、Chromium、HyperFrames 及软件／硬件 WebGL 组合也须单独绑定和验证，本地已有 FFmpeg 的通过不是目标环境准入。
