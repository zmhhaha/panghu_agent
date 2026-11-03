# 视频工作台当前状态

## 本轮：连接线按动画时序出现（已发布）

2026-11-03：默认编译器升级为 `motion-compiler-4`／`technical-diagrams-4`，移除动画开始前始终可见的灰色连接底线。实际线条在 CSS 初始状态隐藏，到对应动作时才绘制；标签和箭头按原时序出现。原编译器 1／2／3 的输出及旧快照保持可复现。源码 `a5350a3c2a1b7f4f5fd8626dc129e617b4c7747e`，独立分支尚未合主线。

两个真实镜头的禁网预检已通过，首帧及返回首帧、18 个动作窗口均核验全部 4＋2 条关系；另目视初始与绘制前后共 6 帧，未见提前露线。新工作台与执行器 Ready、0 重启，发布回归确认业务 15 表和执行器 2 表全部旧行与媒体保持、零新增，八音色与 health／ready 200 保持。

本轮复用原文稿和原配音，仅新增一次整片 draft `b1ee94ad-b55f-4f72-b40b-9729c10eb584`，已 completed：19 秒／456 帧，完整解码和 14 项原 PCM 检查通过。目视全部 10 张实际 MP4 关键帧，两镜起始未见提前连接线，之后随动作出现。最终业务仅新增该 render，执行器仅新增它的一条 operation 与两条 scene 记录；原项目 revision 12、采用 heads、所有旧行与媒体、人工时序 pending 保持，0 Agent／0 TTS。抽帧不代替完整播放、人工听感或用户效果确认。完整身份与证据见 [CONNECTOR_VISIBILITY_VALIDATION.md](CONNECTOR_VISIBILITY_VALIDATION.md)。

## 此前：整片批量制作（已发布，真实技术验收完成）

2026-11-03：独立分支 `codex/video-hyperframes-motion` 已发布整片批量制作，应用源码为 `0e4958019f18ece75ae1390a4de505fdc296a47b`，尚未合主线。每批最多选择 12 个镜头，先核对动态图解、配音和镜头草稿的任务上限，再明确启动；全部批次之间串行安排子任务，支持重启恢复、精确候选复用、明确失败步骤补做及集中审阅采用。整片草稿保留旁白、不附加字幕；正式成片仍要求人工核对真实配音时序。项目统一一个已保存音色，制作期间锁定相关编辑。操作与接口见 [BATCH_PRODUCTION.md](BATCH_PRODUCTION.md)。

release 为 `video-batch-production-20261103-r1`，工作台 `latest` digest 为 `sha256:a7b3e071ce035d855a5d34f15e06411bfbb37680c5ed7864a333483ac9d86701`，Pod `video-workbench-6f98849df7-r655m` Ready、0 重启。发布后只读检查确认原 10 个项目／14 张表的全部旧行各列及历史媒体保持，唯一新增表 `video_batches` 在检查时为空；八音色、health／ready 200、匿名 401 及所有依赖 spec 保持。

**真实两镜批次、集中采用、整片原声草稿及正式成片门禁的技术验收已完成**：独立项目 `1c60d406-688b-415d-9572-a99ec5f28724` 的批次 `89e9d4fc-b5f0-44b4-9799-600c610a67f4` 在 263.719 秒内完成 6/6 步骤，完成时 revision 11、两镜 motion／audio heads 均为 `null`；之后按确切的两镜候选原子采用至 revision 12，未核准人工时序。

整片草稿 `a141b04b-e841-4f33-98e9-6910e532d7b1` 已 completed，为 19 秒／456 帧、1280×720、24fps 的 H.264／AAC MP4，有旁白、无字幕。完整解码及两段 12 秒＋7 秒原始 PCM 的首尾、beat 和镜头边界共 14 项检查通过，整片相关系数 0.9999565535、lag 0；正式 delivery 实际返回 HTTP 409 `motion_timing_needs_review`。本轮实际使用 2 次 motion Agent＋2 次 TTS＋2 个镜头草稿＋1 个整片草稿，无重试；模型回合与金额未测，不沿用此前四任务历史预算。

最终只读检查确认原 10 项目／15 表的全部旧行各列及历史媒体保持，仅新增上述隔离项目及其候选／任务，共 11 项目、active 0，人工 alignment 核准仍为 0，所有依赖 spec 保持。已目视检查两镜中段与末帧共 4 张抽帧，所查文字未裁出画布、标题和要点可读；第二镜保留原回答并显现历史回答。画面仍以卡片为主，关系线标签对比偏弱、第二镜偏密、镜头硬切，完整节奏与用户效果仍待核对；浏览器仍遇到 `failed to write kernel assets`／`os error 3`。媒体技术核对不代表听感、人工时序或整片主观效果通过。完整身份、媒体摘要和验收证据见 [BATCH_PRODUCTION_VALIDATION.md](BATCH_PRODUCTION_VALIDATION.md)。

## 此前已发布：动态视频成为默认流程

2026-11-03：用户已决定全面转向动态视频。独立分支 `codex/video-hyperframes-motion` 的本轮应用源码为 `cbd08136ca0dc694ceeada3afdfdb55de65e62bf`，尚未合主线。新项目默认动态制作，新增分镜保留待制作动态意图；动态图解成为主操作区，静态素材与历史版本默认折叠，MP4 操作入口统一。旧项目可显式转动态，保留已采用画面、配音及历史，有制作任务时禁止转换；动态项目不能绕过 v2 成片校验，历史 MP4 仍可下载。

新增动态测试 17 项、最终 API 回归集合 42 项、前端 Node 测试 70 项均通过，统计范围可能重叠，不相加为总量。发布工具离线 smoke 通过。本地 HTTP 页面返回 200；浏览器 js、重置与重试均在页面操作前遇到 `failed to write kernel assets`／`os error 3`，**本轮浏览器点击、截图及可视验收尚未完成**。本轮不增加 Agent／TTS 调用，不把人工时序标记为已核对。

**构建、工作台发布与隔离 API 验收已完成**：release 为 `video-dynamic-default-20261103-r1`，工作台 `latest` digest 为 `sha256:1701f36bc8bb25eb683fd65e5e33afacf42f172ceb848ad55f6b89f5d499f4c2`，Pod `video-workbench-557fb55b99-vdsvh` 为 1/1 Running、0 重启，旧 Pod 已停止。只更新工作台，executor、renderer、共享 Agent、speech、认证、域名、PVC 与数据库 schema 未改；所有依赖 spec 保持。

发布后原 9 个项目／14 张表全部旧行与历史媒体保持，active 0、八音色、health／ready 200、匿名 401。隔离 owner 新增项目 `268d3431-3277-4736-b215-884fafa532d3` 与一个手动镜头，revision 2，省略模式时默认 motion、采用 heads 为空；静态 timeline／render 返回 409 `dynamic_workflow_required`，v2 delivery 返回 409 `missing_motion_plan`，任务和候选表无新增。验收后共 10 项目，旧行／媒体及依赖保持，未转换真实旧项目，Agent／TTS／render 均 0。本轮浏览器操作及人工时序、整片效果核对仍待完成；完整回执位置、行为与边界见 [DYNAMIC_DEFAULT.md](DYNAMIC_DEFAULT.md)。

## 此前已发布：动态生产接线

2026-11-03 最新：分支 `codex/video-hyperframes-motion` 的最新应用源码为 `07bd6cab8a33ba62a5d6eb94aa10651d852197c0`，尚未合主线。**第 1 部分已完成**：共享 Agent r3 的讲解统筹／单镜 motion 能力已发布并跑通两个真实业务任务；两镜解释方案已原子采用到 revision 6，原稿、项目音色和原 scene ID 保持。共享 Agent 已按主仓库 `.research-temp/platform-slots-coordination-video.json` 交接其他聊天，本轮不再修改其部署、配置或运行池。

**第 2 部分的本轮发布、真实草稿与采用门禁验收已完成**：r5／compiler v3 已正式发布，工作台与 executor 均 1/1 Ready，实际 digest、鉴权及 runtime 绑定匹配，旧 Pod 已停止。新版原声草稿 `8b32626a-4956-4a4d-90c6-ac4a2b299448` 已 completed；18 秒／432 帧的 1280×720、24fps H.264／AAC MP4 完整解码通过，原完整录音无截断、整段 PCM 相关系数 0.9999526875、lag 0。三个实际抽帧样本未见文字裁切，移动及连线可见。草稿完成时 revision 5 的 motion／audio 均未自动采用；随后原录音显式采用至 revision 6、motion 显式采用至 revision 7。真实正式 delivery 返回 HTTP 409 `motion_timing_needs_review`，保持 estimated／pending，没有伪造真人核对。整片讲解、节奏、人工时序与声音自然度仍需后续验收。

r5 发布后只读检查已确认原 7 项目／7 表的全部旧行各列及历史媒体摘要保持；另有两个隔离验收项目，总项目数 9，active 0，八音色保持。发布未修改共享 Agent、迁移数据库或调用模型。首个草稿的文字溢出失败、r4 严格预检拒绝及其修复过程均保留：v3 使用整数行高和明确留白，没有放宽检查或改写 v1／v2 冻结字节。

此前动态生产接线的历史预算已使用 **4 个上游 Agent 任务／14 个模型回合，TTS 0**；该轮预算已用完，当轮停止提交 Agent 并复用原候选和原完整录音，不限制上方另行授权的批量验收预算。准确镜像、测试批次、失败证据及剩余事项见 [MOTION_PRODUCTION_VALIDATION.md](MOTION_PRODUCTION_VALIDATION.md)。离线 UI 的 18／36 秒请求复用 3 秒 MP4，只验证选择与门禁。项目统一音色及八个选项保留；真实旁白时间仍为 estimated／pending，声音自然度与整片效果仍待验收。

## 历史记录：本轮动态生产接线之前

下方保留接线前的开发、样片与既有静态／音频发布证据；其中“尚未发布／未连接”和各旧 Pod、镜像、测试计数只适用于相应日期和版本。

2026-11-02 开发更新：独立分支 `codex/video-hyperframes-motion` 已增加项目级讲解方案，复用通用 Agent 生成整片旁白、读音、画面重点和语义步骤候选，支持逐镜对比与原子采用；采用后的指引进入原有视觉/motion 输入。页面与持久化链路已做离线验证，功能和新 profile 默认关闭，尚未发布到现网；不改变制作先后顺序，也未新增语音模型/情绪控制。详见 [EXPLANATION.md](EXPLANATION.md)。

HyperFrames 的受控演示样片已获用户“比之前好了一大截”的反馈；生产动态渲染接线、真实 Agent 方案质量和整片主观效果仍需分别验收，不能把离线接口通过当作成片满意。以下部署记录是现有静态服务的历史证据。

2026-11-01 最新：四女声晓贝、晓妮、晓晓、晓伊已加入，现有四男声保留，共八项，默认云健；一个项目统一一个音色。四次短句真实生成均成功且未采用，原项目不变。语音 Pod `speech-service-77fb679896-tmpsr`、工作台 Pod `video-workbench-5c69474df6-9l6rv` 均 Ready；工作台镜像不变。详细身份、测试和证据见 [PROJECT_VOICES.md](PROJECT_VOICES.md)。自然度仍需用户试听；以下更早部署身份保留为历史。

2026-11-01 当前更新：项目统一男声音色已上线，四选项为云健（默认）、云希、云夏、云扬。工作台 `cbca832` / Pod `video-workbench-5fff98c4d8-wlv86` / digest `sha256:e5e5aff8c1d94ad237bf035268dfd192db79bd8c39c216998dfb4df7eea5bb77`；语音 Pod `speech-service-d97d54fb6-8vbb2`。三种新增男声各一次真实短句成功，历史原项目 r27/r69 及八条采用录音仍有效。完整能力、测试和发布身份见 [PROJECT_VOICES.md](PROJECT_VOICES.md)。听感仍待用户确认；以下更早部署身份是历史。

2026-10-31 用户效果复核：完整稿仍有明显机械感，原字幕叠加遮挡画面，效果验收未通过。用户已选定“取消字幕，把关键点融入画面”。本轮默认关闭附加字幕，并为新项目提供可编辑的口语图解制作指引；原八镜头成片保留为技术流程证据，不作为满意成片。具体样稿与后续验收见 [PRESENTATION_REFINEMENT.md](PRESENTATION_REFINEMENT.md)。

2026-11-01 更新：前端改进 `31cb29d` 已上线，当前 Pod 为 `video-workbench-6cb86d7ccf-jxblm`，镜像 digest `sha256:0ab44a7e62464e000247f138462bb663ff6047f42519fa2f3d2b27e9534bded3`。真实浏览器已验证默认无字幕、保留旁白及可编辑指引；18 秒口语图解样片已生成并通过媒体与助手目视检查，声音仍待用户判断。以下保留此前音频首版的技术记录，旧 Pod/镜像不代表本轮当前发布。

更新：2026-10-31（Asia/Shanghai）。音频第一阶段代码位于 `D:\github\armbianbegin\.video-audio-worktree`，分支 `codex/video-audio-production`，**已部署并完成八镜头配音、206 秒 MP4 独立技术验证和重启后持久化检查，尚未合入主线**。完整稿听感尚未获用户认可。下方 2026-10-30 记录保留为静态素材首版历史。

入口：[video-service.panghuer.top](https://video-service.panghuer.top/?entry=vps-20261030)。本阶段新增逐镜头配音、WAV 上传、试听、明确采用、实测音频时长、字幕编辑、音画预览和 MP4 导出；原 SVG／HTML／ZIP 功能保留。配音使用已部署的语音服务与用户选择的中文男声云健 `zm_yunjian`。

## 音频阶段当前结果

- 工作台使用 `arm-cluster-master:5000/video-workbench:latest@sha256:ccd2509379ad55029760cf5229d09dd33b0577213172a20e791d9818ad06c6fe`，应用源码 `e5b375b02e8b639b0b4a0eecaa17c67145c005bc`。主动 rollout restart 后新 Pod `video-workbench-6454f4845f-bj52n` Ready，实际 imageID 不变、异常重启 0；PID1 五组 capabilities 均为 0、NoNewPrivs=1。
- 真实验收副本 `1e2a822d-c4c1-45de-a8b9-b72a82f714bc` 已完成并采用全部 8 镜 TTS，复用已有 DSH 画面，新增视觉 Agent 调用 0。MP4 render `5641dc62-01d7-4540-8e79-501ec801cced` 已完成，时长 206 秒，H.264／AAC、1280×720、24fps、4944 帧，完整解码通过；SHA-256 `75ab83eba053d20b0d8beee8687709ee5aadd10eabef8549dfc4b55122358c31`。
- 独立检查通过 8 镜音频首尾匹配（全片相关系数 0.9999445、NRMSE 0.010601）、8 张参考 PNG 的画面与顺序、44 条字幕与 timeline 的时间和文本一致性。字幕仍为 estimated，不能据此认定人工校对或听感通过。成片冻结于 revision 67；后续编辑与恢复到 revision 69 的局部失效检查通过。
- 语音分段修复 `c506bb3c105c81200bc3b86577b65fb96f3361e8` 已部署：`speech-service:latest` digest `sha256:adb9d87b15b461db56a2f4aca5fd6999221fe4c715ee3751250b3a4b21423634`，Pod `speech-service-758cbd54f9-bfczb` Ready，实际 PID1 五组 capabilities 均为 0、NoNewPrivs=1。修复保留原语速、文稿、10% snap 限制与禁止 CPU generator 回退。
- 原 DSH 项目 `cdad4be8-3e51-44ff-98df-3d7500d147d3` 保持 revision 27、8 张已采用 SVG；验收在副本中进行。
- 最终镜像的 ARM 禁网媒体检查通过：10 张既有 SVG／HTML 画面输出可解码的 1280×720 PNG；两镜共 7 秒的 H.264／AAC MP4 完整解码通过，真实云健片段的 PCM 旁白与留白核对通过。这是媒体组件验收，不能代替八镜头真实制作验收。
- 本地相关 Python 测试 **471 passed、1 skipped**（Windows Secret 符号链接权限），前端 Node 测试 **25 passed**。生产浏览器已验证第二镜真实 WAV 上传 A2 候选、明确采用、恢复 A1 且保留 A2 历史，以及真实音频时钟驱动第 1→2 镜播放。视频 VPS 上传上限已调整为 34m；未改 DNS／Casdoor。
- 回归检查通过：ZIP 19 个文件含 8 个画面源文件、其他 owner 读取返回 404、损坏 WAV 返回 422 且项目不变。重启后 8 条采用音频逐一 SHA 匹配、上传 A2 历史保留、MP4 摘要匹配、SQLite integrity ok、原 DSH revision 27 不变；当时 PVC 可用 988,618,752B。回执为 master `/root/video-audio-release-20261031/postflight.json`。
- 最终证据已下载、校验并安全解包至 `D:\github\armbianbegin\.runtime\video-audio-production\evidence-final\`；归档 19,122,821B，SHA-256 `64cbe74652f62513504df98d4fa2ea483d5400fdfecd69fe02d2409b7269bec0`。成片位于其中 `live-audio/dsh-narrated.mp4`，已目视检查第 03／06 镜中心帧，中文显示正常。
- OpenSpec 变更 `add-video-workbench-audio-production` 已同步，验证 `valid=true`、无 issues；19 项中完成 14 项。剩余 5 项为整稿听感、术语人工试听，以及 BGM 的 3 项任务；不把估算字幕或可解码媒体当成人工听感认可。

第三镜原 operation `0f99c230-d5b4-414c-8c4e-8175e464ea67` 因 `--patch` 中 U+002D 不在 Kokoro 词表失败；通过工作台显式读音修正处理第 3／5／6 镜 CLI、路径及文件名，原旁白不改。第六镜原 operation `03d700c9-e71d-4dd7-bbff-fa608acc82c1` 因首段 D=181→200 的 snap 约 10.497% 超过 10% 而失败；完整分段规划修复后以新 intent 成功。两次旧 failed 均保留，未自动重试，不反写为首次全部成功。

## 音频阶段边界

估算字幕明确标注 `alignment=estimated`、`requires_review=true`；人工保存后才标为手工字幕，不宣称自动强制对齐。静态 WeasyPrint 渲染不保留 CSS 动画，可能与 Chromium 的 HTML／CSS 排版不同。BGM／混音尚未实现，整稿听感、术语发音与声音自然度仍需用户试听认可。

音频与画面分别采用；修改旁白会使对应音频和字幕过期，保留历史候选。取消不代表上游已物理停止，提交结果不确定时不自动重复 POST。PVC 为 1Gi，虽已设单文件、单项目和成片限额，尚无全服务音频总限额及删除流程，长期容量管理未验收。具体接口和限制见 [AUDIO_PRODUCTION.md](AUDIO_PRODUCTION.md)。

## 历史记录：2026-10-30 静态素材首版

以下是静态素材首版当时的发布、合并和验证记录；其中“配音／MP4 尚未实现”等描述仅适用于当时的版本。

更新：2026-10-30。工作树 `.video-workbench-worktree`，开发分支 `codex/svg-video-workbench`。**用户已批准，功能已快进合入 `panghu_agent` 的 `main`；真实制作功能链路、浏览器控制、最终备份、证据归档和验收环境清理已完成，固定画布裁切限制已记录。**

入口：[video-service.panghuer.top](https://video-service.panghuer.top)。本期制作静态、可编辑的 SVG／HTML 和审阅 ZIP。工作台保存项目与审阅状态，现有通用 Agent 执行任务。配音、动画时间轴、MP4、图片生成尚未实现。

## 当前结果

工作台已部署并能真实登录，文稿到分镜、SVG／HTML、修订、明确采用和 ZIP 导出的功能链路已跑通。修订后的 SVG V2 与新 HTML 已采用，导出包的版本、源码、预览和播放器结构核对通过。独立运行中取消及真实 Worker 停止已验证。真实浏览器已检查完整 HTML 和播放控制，包含新 HTML 的最终备份已通过。播放器存在固定画布裁切限制，见下文；最终证据归档已校验并下载，验收临时资源已清理，工作台与 PVC 保持不变。

截至 r8，生产制作共提交 **5 次、成功 4 次、失败 1 次**；另有 **1 次独立取消测试任务**。候选实验另行统计，不混入上述制作数量。没有自动重复付费提交。

## 已部署和已验证

工作台、独立登录代理、会话 Redis、1Gi SQLite PVC 均已部署。用户完成域名和 Casdoor 回调配置，并在真实浏览器登录。

登录后创建“工作台登录与编辑验收”项目 `225d6c68-c5e3-4ba7-b225-d54856bfd68b`；工作台多次重启后仍保留。另一 operator 身份读取该项目返回 404，用户页面不显示 operator 项目。匿名及伪造身份头不能绕过登录。

此前已发布两个视频 profile 和专用 caller，逐值保留旧科研／PPT 的 16 条路由和 55 个运行池。科研 6 项、PPT 5 项、视频 2 项能力已通过实际只读查询。已发布工作台精确镜像通过 ARM 禁网容器检查，涵盖非 root、只读根文件系统、Host／身份／CSRF 与数据库重启持久化，模型调用 0。这些结果不代替新镜像和新场景的验证。

## 已发布镜像与本次修复

下表记录目前已确认的部署。scene 升级经人工续接完成，分镜沿用原 Pi 绑定。

| 组件 | 源码 | 已部署 latest 镜像 digest |
| --- | --- | --- |
| 工作台 | `f90ba1c` | `sha256:55ee0651e38565b68869b557c2ae9dbd76456ce25134402d4cad5401308a575a` |
| 共享 Agent control | `f90ba1c` | `sha256:ec86cd6602df9e3783b2d0f0c768a6f01fee129385d8987f9c73da1ea2b9c7aa` |
| scene Pi | `f90ba1c` | `sha256:5b865fbea90fa97f59ab4b62395775526d4e5df59e5ad71185dd562744f70e75` |
| 分镜 Pi（原绑定保留） | `254e748` | `sha256:2d440c564e2ec98538ed2a0c1166aa75fe52d260d069cac629a486b357e47f04` |

镜像仓库 `arm-cluster-master:5000`，部署使用 `latest@sha256:...`。初次发布回执为 `production-video-r2`；本次 scene 升级回执为 `scene_binding_upgrade_ready`。未重置数据库。

HTML 修复源码为 `f90ba1cf00ab90ff06051e08db4b6ef2c933a7f7`。新的工作台、control、Pi 三个精确镜像已构建，并在镜像内用原始 HTML 字节验证通过。工作台 55ee 已部署，Ready Pod 为 `video-workbench-6d6499f7d4-ddddr`，UID 为 `482faee0-608b-4784-85aa-968834c8f051`；真实用户浏览器刷新后原项目仍保留。

新的 HTML 候选 `candidate-video-html-r1` 已完成，使用新 Pi `sha256:5b865fbea90fa97f59ab4b62395775526d4e5df59e5ad71185dd562744f70e75` 和 runtime `video-pi-f90ba1c-html-r1`。共 4 次模型调用，包含探针调用；候选 UID 为 44000006–44000008，Worker 与代理 Pod 均已终止、退出码 0、重启 0，独立审查／接受材料已用于新 scene 发布。候选不计入生产制作数量，也不代替正式 HTML 任务。

scene 配置发布首次执行在释放只读空闲保护锁时超时停止，原失败记录保留。当时只创建了配置、预留生产 UID 区段 46200000 并暂停 dispatcher，API 和模型代理仍为原 aff85 镜像。经审查的人工续接已完成，状态为 `scene_binding_upgrade_ready`；**原发布超时保留，不能声称全过程自动发布成功**。当前目录共 18 条路由、58 个运行池，完整保留原 57 个池，除 scene 外的 17 条路由未变。新增 scene 池为 `video-scene-pi-72fc4ecbab3cf7c8edaa`，UID 区段为 46200000–46265535。实际查询科研 6 项、PPT 5 项、视频 2 项能力全部 ready。

## 真实制作结果

验收项目 `49336596-ee04-4478-8a44-8d87aa470b28` 属于独立 operator，r8 项目修订号为 15；历史 r7 快照为 12。

| 任务 | 结果 | 实测时间 | Job |
| --- | --- | --- | --- |
| 分镜 | 完成，两镜中文分镜，输入／产物／事件核对通过 | 100.22 秒，包含业务收集 | `2b0ee239-f867-49a9-ac79-74a711e6283f` |
| SVG 初稿 | 完成，r5 人工修复后恢复唯一候选；后续已采用 | Job 创建至停止证明约 92.11 秒 | `9a264e44-e270-4c57-8a4b-79dd69f8deca` |
| HTML 首次任务 | r6 失败，`worker_output_invalid`；Worker 已证明停止，无发布产物 | 业务观测 93.35 秒 | `c27f9205-5987-4f20-bd68-9ae77a2f163d` |
| SVG 批注修订 | r7 完成，保留 V1，新增并明确采用 V2 | 77.90 秒，包含业务收集 | `d5c82bd4-4f2c-4813-870c-17f741d03454` |
| 修复后 HTML | r8 单次生成完成并明确采用；原失败记录保留 | 92.654 秒，生成＋收集；整个尾段 95.51 秒 | `26df9a50-0f75-4711-8f3e-6b7652762f0f` |

SVG V1 为 `678e5d30-92f2-4bbf-bd67-d098b4f6b9b3`，V2 为 `5632a726-b19f-4c7c-949b-2a8f076372a8`，V2 的父版本为 V1。r7 已核对原始 V1 源码不变、修订任务的输入／产物／事件和 V2 源码一致。r7 时 HTML 无候选且未采用，导出返回预期 409。r8 新 HTML 版本为 `fdf22ab7-5254-40f1-b4d1-0e333bc0d885`，采用前再次确认导出 409，明确采用后成功导出已采用 SVG V2 与新 HTML；V1／V2 源码未变。r8 状态为 `html_tail_verified_adopted_exported`，本轮只提交 1 次生成且完成 1 次。ZIP 源码、manifest、预览及播放器结构已核对。对应 operation 为 `8255189a-9dec-4ec4-9b33-a6589bdfe120`，HTML source SHA-256 为 `1bbf454b193be3e74acd1e13583423161c62f5691bc44f71410455a390af3cdf`。该次临时 token 文件已删除。

独立取消测试 Job 为 `215ea471-a9ae-4ddb-b418-5666206b29cc`。已观察到运行状态，单次取消后 HTTP 停止证明与对应真实 Pod 终止记录一致。取消子报告最初等待 Pod 证据，r7 外层流程随后补齐 `watch-stop.json`；应以完整证据链判断结果。运行超时、精确槽位释放时间尚未实测。

不能据单次分镜或单镜时间推算整部视频的速度。公开 Jobs 的 usage 仍为 null，没有可信可得的计费用量与单价，费用未知；不把空值当作零费用。

真实浏览器中，完整 1280×720 HTML 画面可读，播放器上一镜／下一镜通过；重新载入后从 1/2 自动切到 2/2，观察耗时 10930 毫秒，配置时长为 10 秒；暂停后手动切镜通过。证据为 `browser-review-final-r8.json`。**实际界面限制：HTML 使用固定 1280×720 画布，播放器可用区域为 1280×656 时会裁掉底部 footer，自适应缩放仍待优化。** 这些控制验证不代表所有视口均无裁切，也不代表帧精确时间轴。

## HTML 失败原因与修复边界

r6 原模型回包中恢复的 write 工具参数包含 `<meta charset="UTF-8">`，旧校验器拒绝了该标准字符集声明。移除该声明可离线通过，原文件未修改。恢复的原 HTML 源码 SHA-256 为 `128580c6871f8c1f37c0d36885261c721b3fcd39481c7bf2e18ee6bcd5679924`。Worker 临时目录已清理，不能把模型 write 参数另称为已独立读取的磁盘文件。

f90 修复只允许直接位于 head 内、仅有 charset 一个属性且值为 UTF-8 的 void meta。其他 meta、额外属性、body 内 meta 和 SVG 中 meta 仍拒绝。profile 测试 **82 passed**；工作台核心与 gateway 定向测试 **112 passed**。两个结果分开记录。旧失败 Job 保持 failed，离线修复通过不改变历史任务结果。

## 提交响应丢失的修复与边界

SVG 在 r4 已被 Agent 接受。测试转发丢弃 POST 回包后，lookup 返回自定义 503，旧工作台错误地把 operation 判为失败。修复将上游 5xx 识别为可继续查询的服务错误，保留原幂等键，不重发提交；当时的定向测试 **112 passed**。

r5 对该失败 operation 做受控人工修复，再验证新的固定镜像窗口：lookup 阻断时保持活动、重启后查询原 Job、恢复一个候选，新增 Jobs POST 为 0。**r4 失败原样保留；r5 包含人工修复，不能声称 r4 全过程自动恢复成功。**

## 备份与证据

最终备份 `sqlite-backup-restore-ecc4ee533b964ceba7ace656020bc9c3` 已通过，包含 projects **3**、operations **6**、versions **3**，已纳入新 HTML。实际 SQLite 只读快照在独立离线文件恢复，没有回写生产 PVC。完整性、全部表行数和备份／恢复 SHA-256 一致，备份临时文件清理通过。SHA-256：`6c82e59c8b8e7eecad02cfd4d89a0c967b22c38f2d1ad85c893745244456f885`。最终环境清理回执 `acceptance-cleanup-r8` 的状态为 `cleaned`，精确处理本次 11 个目标，`pvc_unchanged` 与 `workbench_unchanged` 均为 true；3 个自有端口转发已停止，剩余 0。

服务器证据根目录：`/root/video-workbench-release-20261029/evidence/`。

- 发布：`production-video-r2`、`released-routes-r2.json`。
- 分镜：`business-live-r3/verify`；SVG 原故障：`business-live-r4`。
- 人工修复后的窗口：`svg-fixed-window-r5`；HTML 失败与原回包：`business-finish-r6`、`html-admission-readonly-r6`、`html-original-output-r6`。
- SVG V2 与取消：`business-partial-tail-r7`，历史状态为部分尾段已验证、HTML 仍失败、导出仍受阻。
- 修复后生产 HTML 与导出：`business-html-charset-r8`，状态为 `html_tail_verified_adopted_exported`；不覆盖 r6／r7 报告。
- 本地归档：`.runtime/video-release/business-evidence-r7.tar.gz`，SHA-256 `ebc8cf9c3eda9421cef028091466f0d9ff10012d2651597d8486d6162eeb1b42`。
- 最终备份：`sqlite-backup-restore-ecc4ee533b964ceba7ace656020bc9c3`；浏览器检查：`browser-review-final-r8.json`；清理回执：`acceptance-cleanup-r8`。
- 最终归档：`business-evidence-final-r8.tar.gz`，650306 字节，144 个源文件加内置 manifest，共 145 个成员；SHA-256 `051ee361c4cd486ebb481e756e9d7356e9a85b5cc5f8327cc751e178f72bdd7e`。服务器写后逐成员核对、本地下载整包 SHA 均通过；排除数据库与凭据。内嵌旧 r7 归档的 247 个成员也已完整核对。
- 旧备份 `94d3790bb9b147c1866fbf5614c93f13` 包含 versions 2，`ebe8a19a037a4386942ea99451822fc9` 包含 versions 1，`6afaef201238429784818b4d9ae91c7e` 包含 versions 0，均为历史快照。
- 登录：本地私有记录 `.runtime/video-release/authenticated-browser-receipt-r3.json`。

OpenSpec 服务中的规格、设计与任务记录已整合，18 项任务完成，验证通过且无问题；未执行规格 apply 或 archive。后续优化看 [NEXT_STEPS.md](NEXT_STEPS.md)，固定项目和版本见 [CHECKPOINT.md](CHECKPOINT.md)，各批次验证见 [VALIDATION.md](VALIDATION.md)。本次主线合并保留已验收的应用代码树，没有新增模型调用或部署。
