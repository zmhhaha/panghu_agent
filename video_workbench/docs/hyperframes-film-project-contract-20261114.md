# HyperFrames 整片工程包与音频时钟合同

2026-11-14，H.3 第一步。实现位于 `video_workbench/hyperframes_film_project.py`，验证位于 `tests/video_workbench/test_hyperframes_film_project.py`。

## 已实现范围

把确切的冻结整片计划、Agent 原始模块、资源和完整 WAV 装配成一个有限大小的工程包。所有来源字节、模块身份、全局帧区间、结构化跨镜状态和录音采样区间可独立复核。装配函数不读取“项目当前最新版”，不取网络资源，不调用 Agent，也不执行 JavaScript。

这不是已渲染整片。每份包强制包含：

```json
{
  "status": "not_executed",
  "legacy_bridge": false,
  "required_host_protocol": "hyperframes-composable-film-host-1",
  "evidence_scope": "frozen_inputs_and_declared_lifecycle_not_runtime_or_visual_proof"
}
```

`execution` 常量只读。即使重新计算 manifest 的 SHA，把状态改成 `rendered` 也不能通过独立重编译检查。声明生命周期、状态相等和源文件 SHA，只证明输入与声明一致；不证明导出函数存在、实际执行、视觉连续或开发者认可。H.3 仍未完成。

## API 与包身份

| 接口 | 输入／结果 |
| --- | --- |
| `module_identity(module)` | 验证模块声明与原始源码，返回内容 SHA；不执行生命周期 |
| `compile_film_project(...)` | 接收冻结 film／SHA、modules、recordings、boundaries、assets、runtime_fingerprint，返回 `dict[str, bytes]` |
| `verify_film_project(files, expected_manifest_sha256=...)` | 要求外部可信 manifest SHA，检查精确文件清单、每份字节与完整纯函数重编译；返回独立的 manifest 副本 |
| `frame_to_sample(frame, sample_rate)` | `floor(frame × sample_rate / 24)`，按每个绝对边界单独计算 |
| `frame_context(files, frame, expected_manifest_sha256=...)` | 离线查询该全局帧的场景、转场与原录音采样窗口；每次完整复核工程包 |

包包含 `manifest.json`、各自 namespace 下的 `modules/<namespace>/...`、精确声明的 `assets/...` 和 `recordings/<scene-index>.wav`。文件清单不得多出或缺失资源，大小和 SHA 均进入 manifest。源码与资产保留原字节；不套绘图模板、不改写 Agent 转场。

manifest 使用 `video.hyperframes-film-project.v1`。调用者必须先解决 owner、版本与授权，再将确切输入交给纯函数；本模块的 SHA 检查不能替代这些权限检查。外部可信 manifest SHA 应由后续执行快照冻结，不能从同一个待验证包里自行读取后当成信任根。

`frame_context` 是离线核对工具，逐次复核会重新哈希 WAV，不可直接放进逐帧渲染循环。后续宿主应只验证一次，保存只读解析结果，再执行相同的时间与区间规则。

## 新模块合同

`video.hyperframes-composable-module.v1` 明确记录：module_id、唯一 namespace、scene／transition 角色、scene_id、film_sha256、engine、入口、源码及每份 SHA、资源清单、入口／出口对象状态和生命周期声明。

- engine 为 `html-svg`、`canvas` 或 `three`；同一整片可组合不同能力。
- 所有源码属于本模块 `modules/<namespace>/`，入口为其中 `.mjs`。拒绝路径穿越、外部 URL、不可移植名称和大小写碰撞。
- 生命周期协议为 `hyperframes-composable-module-1`：`mount(root, context)`、`renderAt(context)`、`dispose()`；作用域限定 mount-root，使用绝对全片帧时间，等待返回的 Promise，任意定位必须重建状态。
- 结构化 `states.entry`／`states.exit` 按稳定 object_id 保存 JSON 状态。限制深度、节点数与字节数，拒绝非有限数字。
- 旧 creative v5 的 `__animationExperiment.seek` 产物不能直接作为新模块；不会自动重标、包一层或转换成已执行生命周期。

当前只检查以上声明与字节，下一阶段的新 Agent profile、资源合同和实际宿主分别负责产出与验证这些模块。此实现不修改已有 profile 或创作执行器。

## 全局帧与跨镜交接

全片固定 24fps，场景基础区间采用半开区间 `[start_frame, end_frame)`；前后留白包含在场景原区间内，不通过转场挤压旁白。所有相邻场景都必须有 `video.hyperframes-film-boundary.v1`，绑定确切的两份模块 SHA 和全局边界帧。

`objects` 必须完整覆盖“前镜出口对象”和“后镜入口对象”的交集，逐项绑定两侧结构化状态 SHA。`retain` 要求状态完全一致；`transform` 必须有 Agent 创作的转场模块，其入口／出口状态也必须逐对象对应。它仍是连续性声明，实际画面是否符合须另行观察。

转场模块有独立内容 SHA 及全局半开时间窗，最多 48 帧，须包含关联边界并位于相邻两镜内。不同边界转场窗不得重叠；未引用转场模块也拒绝。两个场景在转场窗内可共同激活。

查询会暴露 `phase=entering/active/exiting`。提前激活的下一镜可能有负 `local_frame`，延后退出的上一镜可能超过自身基础长度；宿主不得钳制这些值或改用局部时钟。真正驱动时间始终是同一个 `global_frame`，画面层级、对象表现和转场内容由 Agent 模块创作。

## 完整录音与显式无旁白区间

可接受现有 coordinator 冻结的完整 film。每个场景必须提供精确 scene_id、audio_version_id、SHA、实际 spoken_text 声明和完整原 WAV；原 film 缺录音会失败，不会自动变成静音。WAV 容器和每个声明 PCM 帧均被实际检查，实测时长须与冻结计划一致。

另提供 `video.hyperframes-film-clock.v1`，在同样的根字段和场景字段上增加根 `schema` 与场景 `clock_source`：

- `recording`：必须有真实、完整录音，沿用相同身份和时间校验。
- `silent`：narration 为空，audio_version_id／audio_sha256 为 null，audio_seconds 和 leading_silence_seconds 为零，trailing_silence_seconds 覆盖整个显式区间；不制造占位 WAV 或虚假语音 SHA。

整片可全部无旁白，也可混合录音段与无旁白段。这里的 silent 仅表示没有旁白录音；必要音效另属尚未实现的正式音效排程，不能据此认定最终媒体必须无声。

录音的实际 spoken_text 单独哈希，证据范围为 `declared_not_transcribed`，不声称已经 ASR 校对；它可能因显式发音替换而不同于原 narration。上层必须从确切音频版本取得该声明，不能用当前文稿反推。

## 混合原始 PCM 的时间规则

每条录音保留原采样率、声道、位宽和全部样本。支持原 WAV 的 8–96kHz、单／双声道、1–4 字节 PCM，包括 24000、44100、48000Hz 混用。本步骤不隐式重采样、不裁尾、不变速，也不产出统一 PCM master 或 FFmpeg 混音参数。

录音起点为全局场景起点加前留白，终点为起点加 `source_sample_count / source_sample_rate`，以有理数保存。每一帧的源采样边界独立计算：

```text
start = clamp(floor((frame / 24 - onset) × source_rate), 0, source_samples)
end   = clamp(floor(((frame + 1) / 24 - onset) × source_rate), 0, source_samples)
```

不累加“每帧整数样本数”，因此 44100Hz 的 1837／1838 样本交替不会产生漂移。测试穷举所有帧，确认每份原录音的样本从 0 到完整尾样本恰好分区，无丢失、重复或隐式转换。

旧 film 使用 JSON 浮点数：只允许在 `1e-12` 秒以内把表示噪声还原成简单有理数，并在时钟记录 `rational_reconstruction_within_1e-12_seconds`；不把起音对齐到视频帧或另一份录音的采样率。这避免 `.30000000000000004 + .7` 导致最后一个原样本掉出区间。

## 首版预算与运行身份

| 资源 | 上限 |
| --- | --- |
| 全片／场景 | 600 秒／24 镜，每镜最多 60 秒 |
| 模块／工程文件 | 47／512，含 manifest |
| 单源码／单模块／全部源码 | 512KiB／2MiB／8MiB |
| 单资产／全部资产 | 32MiB／48MiB |
| 全部录音／全部资源 | 128MiB／192MiB |
| manifest | 4MiB，另计于资源上限之外 |
| 每个边界转场 | 48 帧，不与其他边界窗口重叠 |

这些是新路径的输入预算，不修改现有业务 1800 秒上限，也不承诺整片宿主已能在当前 Job 期限和内存内完成。后续须按同一工程的全局帧分块另行验证。

runtime_fingerprint 须通过现有 v3 身份校验。包中使用已知 GSAP、字体、Three 库和许可证文件时，实际字节须等于指定 runtime SHA；Three 模块必须声明固定库及许可证。v3 身份只冻结基础 runtime，本步骤不据此授予尚未实现的 whole-film host／新镜像准入。

## 接下来的 H.3 工作

1. 新的可组合 Agent 模块产物与独立角色／资源准入，把真实创作输入接到本合同。
2. 真实 HyperFrames 根工程加载多个模块，按全局帧调用生命周期并等待异步完成，实际检验正向、反向、乱序、重复定位和跨镜状态。
3. 整片 snapshot、传输、独立捕获／观察／收据与执行 journal，接入正式协调器；分块仍绑定同一个工程 SHA。
4. 原 PCM 与必要音效的明确混音策略，完整影片解码、时序检查、取消恢复与清理证据。
5. 用真实 Agent 的多镜、多引擎工程做完整样片，再分别记录技术通过、视觉与听感审阅和开发者认可。

现有 workbench／executor 镜像的整目录复制会包含新模块；实验室构建白名单未引用它，当前不增加无用依赖或修改部署。
