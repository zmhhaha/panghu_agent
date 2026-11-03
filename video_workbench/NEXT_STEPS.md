# 视频工作台：后续事项

## 本轮：动态默认流程

2026-11-03：用户已决定全面转向动态视频。应用源码 `cbd08136ca0dc694ceeada3afdfdb55de65e62bf` 已实现动态默认、新分镜继承、旧项目显式转换、静态区域折叠和统一 MP4 入口；新增动态测试 17 项、最终 API 回归集合 42 项、Node 测试 70 项通过，范围可能重叠。工作台发布与隔离 API 验收已完成，独立分支尚未合主线。完整说明见 [DYNAMIC_DEFAULT.md](DYNAMIC_DEFAULT.md)。

已完成发布 `video-dynamic-default-20261103-r1`：工作台 `latest` digest `sha256:1701f36bc8bb25eb683fd65e5e33afacf42f172ceb848ad55f6b89f5d499f4c2`，Pod `video-workbench-557fb55b99-vdsvh` 1/1 Running、0 重启，旧 Pod 已停止。原 9 项目／14 表旧行与媒体保持，active 0、八音色、health／ready 200、匿名 401；隔离新增 1 项目／1 手动镜头后共 10 项目，默认 motion、空采用 heads 和三个导出拒绝路径均验证通过，任务和候选表无新增，所有依赖 spec 保持。未转换真实旧项目，本轮 Agent／TTS／render 均 0，无需重做这些已完成验收。

1. 补充真实浏览器检查。本轮浏览器控制在页面操作前因 `failed to write kernel assets`／`os error 3` 失败，js、重置与重试均无效。仅本地 HTTP 200 和 Node DOM 单测通过，浏览器点击、截图与可视验收仍待完成，不写为已通过；本地 8098 fixture 已正常停止并确认无监听。
2. 保留人工试听、旁白时序、动画节奏、整片理解效果和声音自然度核对；沿用已有候选和完整录音，不自动确认人工时序，也不增加原四任务预算。
3. 独立分支保持未合主线，合并另行处理。executor、renderer、共享 Agent、speech、认证、域名、PVC 与数据库 schema 未改；远端发布回执位置见 [DYNAMIC_DEFAULT.md](DYNAMIC_DEFAULT.md)。

## 此前已发布：动态生产接线

2026-11-03 最新：独立分支 `codex/video-hyperframes-motion` 的最新应用源码为 `07bd6cab8a33ba62a5d6eb94aa10651d852197c0`，尚未合主线。第 1 部分共享 Agent r3 发布及两个真实业务任务已完成，两镜解释方案已原子采用。第 2 部分 r5／compiler v3 发布、真实 18 秒原声草稿、显式采用及 pending delivery 拒绝均已完成：完整解码、432 帧及原 PCM 首尾／四段核对通过，三个抽帧样本未见裁字；时间仍 estimated／pending，真人听感与整片质量未通过验收。原首次草稿和 r4 预检失败记录保留。完整证据见 [MOTION_PRODUCTION_VALIDATION.md](MOTION_PRODUCTION_VALIDATION.md)。

本轮 **4 个上游任务／14 个模型回合已用完，TTS 0，不再提交 Agent**。共享 Agent 已按主仓库 `.research-temp/platform-slots-coordination-video.json` 交接其他聊天；后续只推进工作台／executor／renderer，不再修改共享 Agent 镜像、配置、运行池或 UID 预留，复用已有候选与原完整录音。

1. r5 构建、固定 digest／runtime 绑定和真实禁网 observe／严格验证已完成，状态为 `exact_r5_image_layout_preflight_passed`；保留 r4 原失败与 v1／v2 冻结身份，不更改原失败 snapshot。
2. r5 已完成发布及真实只读 postcheck：两个 Deployment 1/1 Ready、digest／runtime／鉴权匹配，旧 Pod 停止；原 7 项目／7 表旧行各列及历史媒体 SHA 保持，新增两个隔离验收项目后共 9 项目，active 0、八音色保持。未修改共享 Agent、迁移数据库或调用模型。
3. 新草稿 `8b32626a-4956-4a4d-90c6-ac4a2b299448` 已完成媒体技术检查；revision 5 时候选未自动采用，原录音后经显式采用成为 revision 6、motion 显式采用成为 revision 7。真实正式 delivery 返回 HTTP 409 `motion_timing_needs_review`，`no_human_review_fabricated=true`，采用与门禁验收完成。不得以 PCM 保真、抽帧或离线核对控件代替真人旁白听审，不自动标记时间已核对。
4. 本轮验收文档已同步至 OpenSpec `add-hyperframes-motion-video`，清单 13/21，格式校验 `valid=true`、`issues=[]`；记录随本次文档提交保存。人工复核真实旁白时序、动画节奏、讲解清楚程度与声音自然度，以及 60 秒资源、取消／超时／OOM／恢复清理、批量入口和操作负担，作为后续独立事项保留；主线合并另行记录。

## 历史记录：本轮动态生产接线之前

以下保留各日期的原始发布和待办语境；旧镜像、调用计数、尚未接入动画或默认关闭等叙述不用于判断上述本轮真实发布进度。

2026-11-01 最新：四女声晓贝、晓妮、晓晓、晓伊已加入，现有四男声保留，共八项，默认云健；一个项目统一一个音色。四次短句真实生成均成功且未采用，原项目不变。语音 Pod `speech-service-77fb679896-tmpsr`、工作台 Pod `video-workbench-5c69474df6-9l6rv` 均 Ready；工作台镜像不变。详细身份、测试和证据见 [PROJECT_VOICES.md](PROJECT_VOICES.md)。自然度仍需用户试听；以下更早部署身份保留为历史。

2026-11-01：项目统一音色选择已上线并实测，可选云健（默认）、云希、云夏、云扬，不需要再实施逐镜音色。独立项目「男声音色对比（项目统一音色验收）」保留三条同稿短句候选，可先试听比较；原完整稿仍未通过自然度验收。后续优先听感与讲解改进，再处理 BGM。当前发布与证据见 [PROJECT_VOICES.md](PROJECT_VOICES.md)，下方旧 Pod/镜像及任务状态按历史理解。

优先处理 2026-10-31 用户反馈：采用无字幕图解；旁白口语化、密集镜头拆分、准确标识留在画面。先审阅 [PRESENTATION_REFINEMENT.md](PRESENTATION_REFINEMENT.md) 的短样片与样稿，再按认可的方向重制整片。原云健完整稿因机械感未通过效果验收；不能仅凭改稿、调速或媒体解码成功勾选自然度验收。背景音乐优先级在讲解与听感之后。

2026-11-01：无字幕默认选项与可编辑图解指引已上线，18 秒口语样片已完成；真实浏览器、完整解码与助手目视检查通过，无需重复制作该样片。当前部署身份见上方链接。下文保留改进前的音频首版记录，不要按旧 Pod/镜像再次部署。

更新：2026-10-31（Asia/Shanghai）。当前开发分支 `codex/video-audio-production` 已部署音频第一阶段，**尚未合主线**。当前结果见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，接口与功能边界见 [AUDIO_PRODUCTION.md](AUDIO_PRODUCTION.md)。

八镜头真实配音与 206 秒 MP4 的独立技术检查已通过，VPS 34m 上传、浏览器试听／采用／恢复和真实音频时钟跨镜播放已通过；工作台主动重启后的 8 音频／MP4 摘要、SQLite、原项目及进程权限检查也已通过。新 Pod 为 `video-workbench-6454f4845f-bj52n`，镜像摘要不变、异常重启 0，无需重新生成。原第三／六镜的失败及修复过程保留，见 [VALIDATION.md](VALIDATION.md)。

最终 19,122,821B 归档已下载并安全解包至 `D:\github\armbianbegin\.runtime\video-audio-production\evidence-final\`，SHA-256 `64cbe74652f62513504df98d4fa2ea483d5400fdfecd69fe02d2409b7269bec0`；成片为 `live-audio/dsh-narrated.mp4`。OpenSpec 已同步并验证通过，19 项完成 14 项，剩余 1.1 整稿听感、4.2 术语人工试听及 5.1–5.3 BGM。

1. 用户试听完整云健 `zm_yunjian` 技术稿，复核自然度、术语和停顿，人工检查 44 条 estimated 字幕。字幕时间／文本与 timeline 一致已经验证，听感和人工校对尚未获认可，不宣称强制对齐。
2. 后续实现 BGM／混音，补充全服务音频容量管理与删除流程。
3. 按需要评估动画渲染方案。当前 WeasyPrint 输出静态画面，不保留 CSS 动画。

独立分支尚未合主线，合主线仍需另行确认；后续不自动重复 TTS 或视觉 Agent 任务。

## 历史记录：2026-10-30 静态素材首版

以下待办保留当时语境；配音和 MP4 已进入上述音频开发阶段，不再属于“尚未开始”的方向。

更新：2026-10-30。当前事实见 [CURRENT_STATUS.md](CURRENT_STATUS.md)。用户已批准，功能分支 `codex/svg-video-workbench` 已快进合入 `panghu_agent` 的 `main`。本期实现与合并均已完成。

真实制作链路、浏览器控制、包含新 HTML 的最终备份、证据归档与验收环境清理均已完成。累计生产制作提交 5 次、成功 4 次、原 HTML 失败 1 次，另有独立取消任务 1 次；全部历史失败与人工修复记录保留。现有制作、发布和验收任务无需重复执行。

1. 后续优化播放器的画布缩放：完整 1280×720 HTML 可读，但固定画布在 1280×656 的播放器区域会裁掉底部 footer，自适应缩放尚未实现。上一／下一、10 秒配置下自动切镜和暂停后手动切镜已实测通过，不能写成所有视口均无裁切。
2. 后续按需评估配音、动画时间轴、MP4，以及成本下降后的图片生成与 ComfyUI；这些不属于本期已完成范围。

公开 Jobs 的 usage 为 null，没有可信可得的计费用量和单价，费用记为未知。运行超时和精确槽位释放时间未实测，保留为验证边界，不宣称通过。
