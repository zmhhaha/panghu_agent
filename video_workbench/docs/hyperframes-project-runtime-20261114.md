# HyperFrames project runtime：实现及技术验证

这次增加显式的正式创作场景适配器，使用已校验字节的 HyperFrames 0.8.149 core `renderSeek(exact)` 驱动画面。SVG/HTML、Canvas2D、Three.js 继续由 Agent 原始源码绘制；宿主只提供工程、时钟、完成等待、媒体编码及证据。下列首轮记录中的默认路径未切换是当时状态；随后实验室已完成核心桥接部署，见文末。正式制作仍未接通统一整片工程。

## 两条显式入口

- 实验室 executor 可选 `--method-project-runtime hyperframes-core-bridge`。默认值仍为 `native-seek`。新路径使用真实 core 的 `hf-seek` / `waitUntil` 将工程时间传给现有 `__animationExperiment.seek`，记录 `legacy_bridge=true`。它需要独立的 HyperFrames 引擎准入（`video.animation-engine-admission.v2`），不能借用 native v1 准入。
- 正式适配器调用 `compile_creative_project(..., compiler_version='creative-skill-hyperframes-scene-adapter-1')`。仅接收既有 v5 Skill 创作包，保留其原始 plan/source 及 `creative-skill-film-scene-adapter-1` 来源身份，生成新的工程、manifest 和 timeline 身份。运行时必须为实测的 `video.motion-runtime.v3`。默认编译仍使用历史 native 身份。

新正式适配器沿用原有七文件隔离 Job 输入，工程文档包含在受校验的 timeline 中。工程入口、原始源码 SHA、引擎、24fps、画幅、录音时长及前后静音被绑定；独立执行器重新编译后逐字节比较输入。原始声音不变速、不断尾，前后保持由工程时钟处理。native 的历史 manifest/compiler/receipt 不会得到新 HyperFrames 证明。

捕获使用 `isolated-hyperframes-core-project-ffmpeg-1`，生成 capture/observations v3。独立校验器核对分别重新加载的 render、observe 帧、core 实际时间、seek/dispatch 次数、core SHA、工程 SHA、原生引擎上下文、资产、音频及最终媒体。证据明确标记 `single_scene_project_not_whole_film`。FFmpeg 负责编码和音频封装；它不替代 HyperFrames 工程时钟。

## 已完成的技术检查

服务器证据统一位于既有 OPS 目录的独立子目录；这些检查没有下载依赖、构建镜像、调用模型或部署公共服务。

- `hyperframes-adapter-probe-r1`：真实安装的 pinned core，SVG、Canvas、Three.js 各24帧；异步绘制、正向／逆向／重复／空闲／重新加载，以及异步绘制失败拒绝，4/4通过。这里是手写协议夹具。
- `hyperframes-formal-probe-r1`：新正式捕获路径对三个引擎各24帧，render/observe 独立结果一致，录音前后保持及错时钟拒绝通过；三个720p MP4均实际编码、ffprobe及完整解码，4/4通过。这里也是协议夹具，不是 Agent 美术样片。
- Python creative/Skill/film/HF custody 定向检查96通过；executor/method renderer/普通lab打包检查137通过。Node离线共享工程检查9通过，既有渲染合同20通过；本地真实浏览器用例跳过，真实core结果以上述服务器实测为准。

技术探针使用已有 `video-motion-renderer@sha256:a115fd513a34edc4fb53ce3400d47848a316f420b44272c5141b9b87e684f647` 依赖环境及明确只读挂载的已审查宿主源码。它们证明当前源码在该环境中的行为，不证明一个尚未构建的新发布镜像已准入。发布仍须从完整Git源码普通构建 `latest`，核对标签、digest和镜像内字节，独立准入后才切换。

## 真实 Agent 原源码重渲染

`hyperframes-agent-source-r1` 的首个旧 SVG 作品在严格末帧定位比较中失败，错误为 `method_absolute_seek_mismatch`；停止后容器已经移除，未跑后续两项，失败证据保留。没有编辑 Agent 源码、改变时长或放宽帧哈希检查。

随后仅在实验宿主的新 HF 路径加入与正式宿主一致的布局刷新，稳定 SVG 重新定位后的光栅化；native 路径不变。新独立 `hyperframes-agent-source-r2` 的 `completed.json` 与 `independent-reconciliation.json` 已确认：复用已核验的原 SVG 4秒／96帧、Canvas 14秒／336帧和Three.js 14秒／336帧作品，三项全部通过真实core、非顺序／重复／空闲／重新加载定位、编码、完整解码及独立lookup。三项 Agent 源码均保持，原／新均无音轨（本次回执审计确认，不构成音效 PCM 验证），Canvas 及 Three.js 新 MP4 与原 native MP4 字节也完全一致；SVG因宿主稳帧，新旧媒体摘要不同，不将其记为媒体字节一致。三个技术容器均已独立确认不存在。

本次使用原样片，不是新模型生成，不代表效果已被开发者认可；仍为显式 `legacy_bridge=true`，没有伪称Agent原始源码直接实现了 `hf-seek`。原有业务数据库、作品和准入文件未修改，新技术记录只写入独立ledger。首轮失败与第二轮成功各自保留，未覆盖旧结果。

## 尚未完成的边界

本次适配器是一个场景一个真实HF工程。它提供统一基础，但不等于一个完整影片工程；把多个这样的MP4拼接也不能称为全片HF。

接下来须将整片Agent产物组织为一个共享工程，统一录音主时钟、源文件与资产、场景激活和跨镜状态、全片定位与完成等待，再做完整影片实际捕获、原声及转场验证。正式业务编排、运行快照和部署默认切换也须接上明确的新身份，不能仅因工程模块存在或fixture通过而勾选全片闭环。

普通lab白名单和构建清单已经纳入共享 `hyperframes_project.mjs`，renderer普通Dockerfile原本已包含该文件。UI/知识历史发布可单独提交，避免把新HF宿主摘要带入只需要界面变更的发布。

## 实验室核心桥接部署完成

`hyperframes-h2-plan-0e0eb0e-r2/final-independent-readback.json` 已独立确认完整 Git 源码 `0e0eb0e93614bc152e5c7b1c8c7b55d78f4be37c` 的两个 `latest` 镜像实际启用。executor digest 为 `sha256:47d02339fd9556afb74be21dd9aef8e8ac2a65f03afaae2f3ca93ca6ff6810cf`，renderer digest 为 `sha256:59ee5c356f4c742e515385db4e2b315c9393641bea7d76136f095b27214e79ca`。Pod Ready、原 Service 与端点、实际调用方目录均通过回读；`method_project_runtime=hyperframes-core-bridge`，新准入为 v2。

同一全局预算内实际重渲染三次：旧 Agent SVG 4 秒／96 帧、Canvas 14 秒／336 帧、Three.js 14 秒／336 帧均通过真实核心、严格定位和编码，模型调用为零。三条样片均无音轨；原 PCM／音效摘要为空的相等检查不能表述为带声核验。未改写原作品、原录音和历史记录，首轮资产宿主映射失败及其恢复仍保留，不将其算作已消耗渲染或覆盖。部署回读证据归档为本地既有 D 盘研究目录 `hyperframes-h2-final-live-readback-20261114.json`。

这是实验室的核心桥接运行，仍如实标注 `legacy_bridge=true`；没有把旧 Agent 源码改称原生生命周期模块，也不代表正式整片编排、统一音效或新 realm 宿主生产准入已经完成。正式整片下一步见[接入差距](hyperframes-film-production-integration.md)。

## 完成条件审计补记（2026-11-14）

只读原／新 operation、request、receipt 及 ffprobe 确认三条样片的 sound_events 均为空、audio_sha256 均为 null、无 sound-effects.wav，MP4 只有 video 流。既有 readmission 的 audio_pcm_unchanged=true 来自 null==null；原回执保留，本说明纠正此前过宽描述。实际耗时约 SVG 9.03 秒、Canvas 45.86 秒、Three.js 280.03 秒，三容器均 removed_and_confirmed_absent。

既有 adapter／formal 实测包含异步成功／失败和不同定位顺序，但未包含实际 random／seed 案例。H.1 的随机状态和实验室带声验证、H.2 的必要音效验证仍待完成；核心已上线是独立的完成事实。H.3 技术 WAV 全片导出的有声检查见离线导出记录，不扩写为实验室音效或正式业务验收。紧凑证据保存在既有 D 盘研究目录 hyperframes-task-completion-audit-20261114.json，完整范围见[统一设计 7.6](agent-creation-effect-library-plan.md)。本次没有启动新测试、模型或渲染。
