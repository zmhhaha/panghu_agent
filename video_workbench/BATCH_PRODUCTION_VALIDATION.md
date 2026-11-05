# 整片批量制作发布与真实验收

2026-11-03：批量制作已上线，独立两镜项目完成真实动态图解、配音、逐镜草稿、精确集中采用和整片草稿验收。原用户项目与媒体保持。整片是供效果审阅的草稿；正式交付仍需真人核对配音时序。功能说明见 [BATCH_PRODUCTION.md](BATCH_PRODUCTION.md)。

## 发布身份与数据保护

- 分支：`codex/video-hyperframes-motion`，未合主线。
- 应用源码：`0e4958019f18ece75ae1390a4de505fdc296a47b`。
- Release：`video-batch-production-20261103-r1`。
- 工作台：`arm-cluster-master:5000/video-workbench:latest@sha256:a7b3e071ce035d855a5d34f15e06411bfbb37680c5ed7864a333483ac9d86701`。
- 发布后 Pod：`video-workbench-6f98849df7-r655m`，Ready、0 重启；旧 Pod 已停止。源码包为 56 个允许文件，普通 UID10001 禁网 import 与实际 POSIX 权限检查通过。
- 仅更新工作台镜像及四个发布身份 annotation，独立 executor、renderer、共享 Agent、speech、认证、网络及 PVC 配置不变，所有依赖 spec 与 renderer runtime 身份比较通过。

本轮新增 `video_batches` 表，属于明确的加法 schema 变更。发布时持 SQLite 写锁重新检查队列并捕获一致备份，再停止旧 Pod、切换镜像；原 10 项目／14 张表的 schema、所有旧行各列、采用指针与历史媒体保持，原表没有新增行，新批次表为空。health／ready 200、匿名 session 401、八音色和默认云健保持。新路由探针只访问不存在的隔离资源，全部表计数不变，没有调用模型、TTS 或渲染。

数据库备份仅保存在 master 的本轮私有目录，未下载数据库或会话凭据。构建和部署仅使用 `latest` 标签及固定 digest。

## 真实两镜制作

使用新的独立 owner `operator-video-batch-20261103`，复用此前两镜讲解提案的原稿、旁白与画面指引，重新创建项目；不引用其他项目的候选或录音，也没有修改原用户项目。来源项目为 `642d790a-351d-4e19-bb19-af7415d31aec`，提案版本为 `8b32cd60-7de1-4bbc-946e-1f10d77e082a`。

- 新项目：`1c60d406-688b-415d-9572-a99ec5f28724`。
- 批次：`89e9d4fc-b5f0-44b4-9799-600c610a67f4`。
- 制作清单：2 个 motion Agent 任务、2 次 TTS、2 条逐镜草稿；六个阶段全部 completed，批次用时约 263.719 秒。失败与不确定也占预算，实际没有失败或重试。
- 批次完成时项目 revision 11，两镜 motion／audio heads 均为 null；没有自动采用，时序均为 estimated／pending，未写人工 alignment。
- 内容审阅后以确切双候选集合及双 expected heads 一次原子采用，项目成为 revision 12；总稿、音色和镜头身份保持，时序仍待审。

| 镜头 | motion 候选 | audio 候选 | 实际录音 |
| --- | --- | --- | --- |
| 日志保留什么 | `25e66d18-b2dd-43f1-8a08-cb260c850d4f` | `0dde6b3c-8bfb-4efb-995e-8c417881049a` | 云健，12 秒 |
| 下一轮模型看到什么 | `7f5fb2b9-3818-4ef8-9972-2b7c9be33121` | `cbc8cafb-0280-40f1-bebe-2497497ad6c4` | 云健，7 秒 |

随后仅新增一条整片 draft，没有再请求 Agent 或 TTS。本轮真实调用共 2 个 motion Agent 任务＋2 次 TTS；Agent 内部模型回合与金额未测，不把任务数当作模型调用数，也不把未知费用当作零。

## 整片草稿与独立媒体检查

- Render：`a141b04b-e841-4f33-98e9-6910e532d7b1`，completed，冻结项目 revision 12。
- Snapshot SHA-256：`515c60c254ae7f0c0e07095398d327d13be292d11f6ddf389faea415c780f57f`。
- MP4 SHA-256：`36b97391c441c93479144ad354debf8c5794909e67ab7099cca12514a51f4927`，366,201 bytes。
- 19 秒，1280×720、24fps、456 帧，H.264／AAC、24kHz 单声道；完整解码通过。
- 草稿保留旁白，无附加字幕，字幕 cues 为空且没有字幕流；该结论不包含逐帧字幕 OCR。
- 两段原 WAV 的整段、首尾、四个 beat 和镜头边界共 14 个零 lag PCM 检查通过；整片原 PCM 相关系数为 `0.9999565535252921`、NRMSE `0.009395869825092159`。原录音未被裁断或拉伸，镜头顺序与冻结快照、确切采用版本一致。
- 渲染请求至完成约 109.782 秒，包含排队与收集；不是 CPU、内存或 60 秒组合容量基准。

真实正式 `delivery` 请求返回 HTTP 409 `motion_timing_needs_review`，没有创建第四条渲染任务。两镜继续 estimated／pending，`reviewed_by/reviewed_at` 为空，本轮不调用 timing verify。

最终只读比较以发布后即时数据库为基线，原 10 项目／15 表的全部旧行与媒体保持，只新增 1 个独立项目、2 个 motion operation／version、2 个 audio operation／version、2 个 motion head、3 个 render 和 1 个 batch，总项目数 11。active 0、人工 alignment 新增 0，所有依赖 spec 保持。

## 布局、表现力与未完成验收

独立媒体检查提取 8 帧；助手目视两镜中段及末帧共 4 个样本，标题、中文卡片、主要结论可读，所查帧没有文字裁出画布。第一镜展开两类日志记录，第二镜保留原回答并显示进入历史的回答，没有把“不加入历史”画成“从日志删除”。这是对本次测试文稿的符合性检查，不证明该技术说法适用于所有系统。

画面仍以卡片和关系线为主，部分关系线文字对比偏弱；第二镜 7 秒信息偏密，镜头间为硬切。当前没有验证与最初参考视频同等的表现力。抽帧不替代完整播放的节奏判断、真人听感、句段对齐或用户满意。本轮未更换语音模型，不能据此称机械感已解决。

浏览器工具在页面操作前仍返回 `failed to write kernel assets`／`os error 3`。页面代码、接口和 Node 检查通过；本轮真实浏览器点击、截图及播放验收尚未完成，不能写成已通过。60 秒组合资源、真实渲染取消／超时／OOM 清理与长期容量管理沿用此前待办，不由这个 19 秒成功样本替代。

## 离线验证与证据位置

已执行构建／动态默认回归 46 项、旧 motion／audio／explanation API 集合 42 项、批量 API 22 项、核心批量首轮 19 项；修复明确未发送的 TTS 预检分支后，新增及相关核心 5 项、旧音频取消／退出／provider／重启目标回归 5 项通过。前端全套 87 项，编辑保护修复后音频／批量针对性 34 项通过；无网络实际保存入口与控件锁定／解锁检查通过。范围有重叠，不相加为唯一测试总数。

发布脚本两套 smoke、确切真实验收 bridge 的本地 prepare／preview／单次 start smoke、JavaScript/Python 语法和 diff 检查通过。离线 smoke 没有调用真实模型、TTS 或渲染，也不代替上述真实结果。

- Master 发布回执：`/root/video-batch-production-20261103-r1/build/image.json`、`release/postcheck.json`。
- Master 真实结果：同根 `business/summary.json`、`business/final-postcheck.json`、`business/media-review/report.json`；私有 SQLite 备份只留 master。
- 本地安全证据与样片：`D:/github/armbianbegin/.runtime/video-batch-production/evidence/`，视频为 `two-scene-project-draft.mp4`，另含媒体报告、8 帧及独立 `operator-visual-review.json`。
- OpenSpec：项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的 `add-video-project-batch-production`；格式校验与剩余人工验收分开记录，不 apply／archive，不自动合主线。

OpenSpec 已同步本轮实际结果及原动态视频变更的关联进度，两份规格校验均 `valid=true`、`issues=[]`。本轮清单 8/10，剩余为浏览器可视检查和用户听审／时序／效果确认；旧动态视频组合清单保持 13/21，未将批量技术成功替代 60 秒容量、故障清理或主观效果总验收。
