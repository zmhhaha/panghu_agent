# 配音、字幕与静态成片

本轮在已有 SVG / HTML 工作台上增加逐镜头音频候选、明确采用、实测时长、字幕校正、项目试听和异步 MP4 导出。配音通过独立语音服务调用；工作台本身不加载语音模型。当前接入选择为 `kokoro-rk3588` 和中文男声云健 `zm_yunjian`，独立语音服务承担 RK3588 推理。后续更换提供方可保留工作台的候选、采用和制作流程。

本文描述代码行为与操作边界。镜像摘要、集群实际启用状态、ARM 及生产验收结果以本轮发布记录为准；本文不将本地测试视为生产验收。

## 制作流程

1. 保存文稿和镜头内容，制作并采用每个镜头的 SVG / HTML 画面。
2. 在镜头中检查旁白，按需设置语速和读音修正，再生成配音；也可以上传自己的 PCM WAV 录音。配音服务未配置时仍可上传。
3. 从音频历史中选择候选并试听，满意后明确点击采用。生成或上传成功只新增候选，不会自动采用，也不会改变已采用的画面。
4. 设置前后留白。工作台使用 WAV 实测时长计算镜头时间；旁白比原镜头长时延长镜头，不截掉旁白。
5. 生成字幕估算，逐句试听并校正文字和时间，再点击“确认并保存字幕”。估算无需修改时也可以明确确认。
6. 用项目播放器检查顺序、画面、旁白、留白和字幕；需要时下载 SRT。
7. 创建 MP4 导出任务，等待成功后下载。导出历史与取消操作在项目中保留；原有 SVG / HTML 源码和 ZIP 素材导出继续可用。

音频和画面分别采用。切换试听候选不会改变音频采用记录；迟到的任务结果只进入历史，不覆盖用户的选择。所有候选、播放、采用、字幕和导出均按已有登录用户隔离。

## 内容绑定与过期状态

每个音频候选保存不可变 WAV、摘要、原旁白、生成设置及输入绑定。语速范围为 `0.5–2.0`；读音修正每行写 `原词=读法`，最多 32 条，原词最多 80 字、替换读法最多 200 字。替换采用最长匹配、单遍处理，保留原旁白，不对替换结果再次连锁替换。

修改某个镜头的旁白或配音设置，会使这个镜头原有配音和派生字幕过期。只修改画面说明或视觉版本不会使仍匹配旁白和设置的音频过期。过期版本保留历史，但不能作为当前有效旁白直接进入有声制作。

字幕绑定已采用音频的版本 ID 和 SHA-256。更换音频或修改旁白后须重新估算或校正；修改前留白会标记字幕时间过期，修改后留白不会移动已有字幕。保存字幕和编辑设置携带项目 revision，采用音频携带预期的音频 head；发生并发变化时返回冲突，避免覆盖其他操作。

## 时长与字幕时间

单镜头时长按以下关系计算，再向上取整到 24 fps 的帧边界：

```text
镜头时长 = max(原设定时长, 前留白 + 音频实测时长 + 后留白)
```

原设定时长范围为 1–120 秒；单音频最长 300 秒；前后留白各为 0–10 秒。镜头保留整段旁白和设置的留白。

字幕时间使用镜头内秒数，已经包含前留白。调用方不应再次叠加前留白。字幕必须有序、不重叠、文本非空，位于实际旁白的起止范围内；每条最多 500 字，总计最多 10,000 字，公开保存 API 接受 1–200 条。开始和结束时间至少能在毫秒精度下区分，文字不能包含产生新 SRT 段落的连续空行。

当前自动字幕按照标点分句并按文字长度分配音频时间，**属于时间估算，不是语音强制对齐**。估算结果带 `alignment=estimated` 和 `requires_review=true`；明确保存后为 `manual`、`requires_review=false`。当前导出实现允许使用尚待检查的估算字幕，因此制作前仍需主动试听确认，不能把“导出成功”视为逐词同步已验证。

## 项目预览与 MP4 的区别

项目播放器使用服务端实测时间轴，旁白播放以浏览器音频元素的实际 `currentTime` 为准。缓冲期间不会依靠墙上时间直接跳过正在播放的旁白。画面在无权限的 `sandbox=""` iframe 中显示。

部署生成器启用成片时选择 `VIDEO_STATIC_RENDERER=weasyprint`。每个镜头的内联 SVG / HTML 经 WeasyPrint 排版为单页 PDF，再用 `pdftoppm` 转为 PNG，由 FFmpeg 将静态画面、旁白和字幕组成视频。这条路径不执行 JavaScript，也不重放 CSS 动画；HTML/CSS 支持与浏览器不同，项目预览和导出布局可能有差异。

静态源代码仍需通过现有 SVG / HTML 校验。WeasyPrint 子进程的 URL fetcher 拒绝所有 URI 资源，包括 `data:`、`file:` 和 HTTP(S)；系统字体通过 Pango / Fontconfig 正常使用，内联 SVG 直接排版。依赖外部图片、网络字体或资源 URI 的画面可能失败。这里的隔离不代表禁止读取系统字体和任务明确提供的输入、输出文件。

成片格式为 1280×720、24 fps、H.264 `yuv420p`；有声导出将完整 PCM 音频规范化后编码为 AAC、单声道 24 kHz、96 kbps。导出时冻结镜头顺序、已采用画面和音频、字幕、时长及渲染设置，之后编辑项目不会改变已创建的任务。

无声模式需要明确指定 `silent:true`；当前无声导出没有音轨，同时不带字幕。暂未实现 BGM 上传、背景音乐混音、自动压低音乐或响度混合，也未实现动画时间轴。

## API 概览

下表的 `{id}` 分别指相应项目、镜头、音频版本或任务 ID。所有接口使用已有认证身份；写操作遵循既有 CSRF 检查。生成音频、上传音频和创建渲染任务使用 `Idempotency-Key`，相同 key 不能用于不同请求。发生响应不确定时保留原 key，避免用户重试造成重复提交。

| 操作 | 方法与路径 | 主要参数或响应 |
| --- | --- | --- |
| 配音设置 | `PATCH /api/scenes/{id}/speech` | `expected_revision`、`speed`、`pronunciation` 字典 |
| 生成音频 | `POST /api/scenes/{id}/audio/generate` | `expected_revision`；返回 202 和异步操作 |
| 上传音频 | `POST /api/scenes/{id}/audio/upload?expected_revision=N` | 原始 PCM WAV 字节，建议 `Content-Type: audio/wav`；不会自动采用 |
| 采用音频 | `POST /api/scenes/{id}/audio/adopt` | `version_id`、`expected_head_audio_version_id` |
| 取消配音 | `POST /api/audio-operations/{id}/cancel` | 请求本地取消，见下文上游边界 |
| 试听或取音频 | `GET /api/audio-versions/{id}/audio` | WAV，支持单段有界 byte range |
| 设置留白 | `PATCH /api/scenes/{id}/timing` | `expected_revision`、`leading_seconds`、`trailing_seconds` |
| 估算字幕 | `POST /api/scenes/{id}/captions/estimate` | `expected_revision` |
| 确认或校正字幕 | `PUT /api/scenes/{id}/captions` | `expected_revision`、`audio_version_id`、`cues:[{start,end,text}]` |
| 下载字幕 | `GET /api/projects/{id}/subtitles` | 项目 SRT，镜头时间转换为项目时间 |
| 项目时间轴 | `GET /api/projects/{id}/timeline?silent=false&subtitles=true` | 已采用版本引用、实测镜头时间、镜头内字幕 |
| 创建成片任务 | `POST /api/projects/{id}/renders` | `expected_revision`、`silent:false`、`subtitles:true`；返回 202 |
| 成片历史 | `GET /api/projects/{id}/renders` | 项目导出任务列表 |
| 取消成片任务 | `POST /api/renders/{id}/cancel` | 终止排队或正在执行的本地制作 |
| 播放或下载成片 | `GET /api/renders/{id}/video` | MP4，支持单段有界 byte range |

`GET /api/session` 的 `speech_configured`、`speech_model`、`speech_voice` 和 `media_configured` 表示当前进程配置；它们不代表上游健康、模型质量或实际成片已验收。项目读取中包含音频候选、采用记录和当前音频操作，供前端轮询刷新。

## 部署配置

`python -m video_workbench.deploy.render` 在原有必填参数基础上增加两个独立开关，默认均关闭：

| 参数 | 默认值与作用 |
| --- | --- |
| `--speech-enabled` | 启用语音服务连接、凭据挂载和定向网络策略 |
| `--speech-namespace` | `speech-service` |
| `--speech-service` | `speech-service` |
| `--speech-model` | `kokoro-rk3588` |
| `--speech-voice` | `zm_yunjian` |
| `--speech-token-secret` | 工作台命名空间的已有 Secret `video-speech-service`，键名 `token` |
| `--render-enabled` | 启用静态 MP4 制作，并增加资源与临时目录配额 |

可将上述开关加到现有部署生成命令。只启用 render 也可使用上传录音制作；只启用 speech 不会自动增加编码资源。生成器只生成可审阅资源，不执行 `kubectl apply`，不创建语音凭据，不修改公网路由或 Casdoor。

语音开关产生以下运行时配置：

```text
VIDEO_TTS_URL=http://speech-service.speech-service.svc.cluster.local/v1
VIDEO_TTS_MODEL=kokoro-rk3588
VIDEO_TTS_VOICE=zm_yunjian
VIDEO_TTS_TOKEN_FILE=/run/secrets/video-speech/token
```

命名空间和服务名可用参数替换。URL 包含 API 前缀 `/v1`，由适配器调用其语音合成接口。独立服务的 Service 端口为 80，网络策略定向允许选定语音命名空间内 `app: speech-service` Pod 的 TCP 8092 后端端口；Pod 增加 `speech-client: "true"` 标签。原有 DNS 和通用 Agent 访问规则保留，不新增公网访问。

凭据以只读目录挂载，Secret 文件权限为 `0440`，通过 Pod `fsGroup: 10001` 读取；不把 token 写入镜像或前端。连接配置在进程启动时读取，轮换凭据或更换模型后需重启。日志仅输出受控错误码，不向用户透传原始上游异常或凭据。

语音启用后终止宽限期为 150 秒。适配器默认连接、读取、总超时分别为 5、60、120 秒；持久配音操作的默认截止时间为 1800 秒，两者含义不同，操作截止时间不延长单次 HTTP 请求超时。

成片开关产生 `VIDEO_RENDER_ENABLED=1` 和 `VIDEO_STATIC_RENDERER=weasyprint`，并配置：

| 资源 | request | limit |
| --- | --- | --- |
| CPU | `250m` | `2` |
| 内存 | `256Mi` | `1536Mi` |
| 临时存储 | `128Mi` | `2Gi` |

`/tmp` 使用上限 1 GiB 的 `emptyDir`。容器继续使用非 root UID/GID 10001、只读根文件系统、不提权、无 capabilities、无 ServiceAccount token 或主机挂载。维持单进程、单副本和 `Recreate`；不可增加 Uvicorn workers 或多个实例共同调度同一 SQLite。

镜像按仓库规则构建、推送和部署 `latest`，Deployment 引用 `仓库:latest@sha256:实际摘要`；发布身份记录在 OCI labels、Deployment annotations 和摘要中，不创建提交号或版本号标签。实际镜像及发布验收回执单独记录。

## 容量和执行限制

| 项目 | 当前限制 |
| --- | --- |
| 单次生成文稿 | 读音替换后的文本最多 2000 字且 16 KiB |
| 单段音频 | 完整、可解码、非全静音 PCM WAV，最多 32 MiB / 300 秒 |
| WAV 格式 | 8–96 kHz，单声道或双声道，采样宽度 1–4 字节；不接受只改扩展名的压缩音频 |
| 音频候选 | 每镜头最多 64 个；每项目保存音频最多 512 MiB |
| 导出项目 | 最多 24 镜头、总时长最多 1800 秒 |
| 导出采用音频 | 项目聚合最多 64 MiB |
| 单个 MP4 | 最多 256 MiB |
| 已保存 MP4 | 整个数据库中的 MP4 BLOB 聚合最多 256 MiB |
| 配音并发 | 一个工作台串行配音执行槽 |
| 导出并发 | 一个渲染 worker；同一用户最多一个活动导出任务 |
| 渲染时间预算 | 整项最多 900 秒；各外部进程另有短于整项预算的超时 |

音频、渲染状态和完成媒体保存在现有 SQLite PVC；临时渲染目录完成后清理。容量达到上限会拒绝新内容，不代表会自动清除历史。备份和恢复需包括 SQLite 及其 WAL，临时目录不是持久媒体存储。

## 取消、失败和重启

配音提交前先持久化 `submit_attempted`，不会自动重试可能已提交的上游请求。取消尚未提交的排队任务可在本地阻止请求；提交后取消只停止本地收集和候选发布，**不能据此证明上游已停止合成**。独立 RK3588 服务即使调用方断开，也会保持原生执行槽占用直到实际合成结束；取消后迟到结果不会发布为候选或自动采用。

工作台重启时，已尝试提交但未完成确认的配音任务以 `speech_submission_uncertain` 失败；已取消但无法确认上游停止的任务保留 `speech_upstream_stop_unconfirmed`。尚未提交的队列可继续执行。此时先检查任务状态，避免把不确定提交当成从未执行而反复调用。

取消排队渲染会直接取消；取消运行中的渲染会通知当前进程并终止受控子进程，不发布成功 MP4。重启后中断的运行中渲染标记 `render_interrupted` 失败或取消，不自动重放；尚未开始的队列仍可执行。

完成前使用 FFprobe 检查视频编码、分辨率、有声模式下可播放的 AAC 和实际时长。这些检查不评估画面语义、旁白自然度或字幕逐句准确性；失败任务不能以部分文件发布为成功。

## 本轮验证记录与待验收项

本轮已完成的本地验证：471 项 pytest 通过，1 项符号链接测试跳过；23 项 Node 测试通过。浏览器流程已验证生成、明确采用、上传替换 42 秒音频、字幕以及实际音频时钟驱动的播放行为。这些是本地功能与边界验证，不是 ARM 模型性能或生产集群验收。

最终 ARM 镜像、实际静态素材排版、内部语音调用、MP4 导出及上线后的访问结果，由发布验收记录补充。完整文稿的旁白自然度、专有名词发音和字幕同步仍需人工试听；用户选择云健音色不代表整篇配音质量已经验收。
