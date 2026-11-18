# 视频学习库验证记录（内部参考产品与浏览器技术验收）

2026-11-18。本记录区分源码实现、本地检查、角色准入、部署和真实产品实验。首版提交 `0ae66695efd0b9e331aecedd16b58649c3181973`；协议及技术重试上下文修复 `0e338fe4cc1e89e748bfd11c90aab79076d8faac`；r3 构建修复 `35310d17f2430b970b442c992dd7ad26b3b43851`；UI 参考选择修复 `55d081fe9ef9126f7d1c5eb35f1ab87dc3bc291d`；新评审类型指导 `68795854120588e0cae8c787ad504ead850a7971`。r3 control／worker 已完成真实技术准入并激活；r5 executor 与 r6 lab 已部署 Ready 并核对运行源码。内部 6 秒参考的 API 产品已完成，详情刷新由 r6 `e269d4350b420120082efb316c1cef7e9a599572` 修复并通过浏览器技术复验。随 Git 保留的汇总见[精简证据](evidence/video-learning-20261118.json)，使用合同见[视频学习库](video-learning-library.md)，完整目标见[参考学习设计](reference-learning-libraries-design.md)。

## 实现范围与证据边界

代码提供独立网页 `/dev/animations/video-library.html` 和结构化接口 `/dev/animations/api/video-lab`：上传实际参考视频、保留来源和媒体身份、独立分析候选片段、明确选定范围后创作、保留完整源码、经共用 HyperFrames 渲染 MP4，再交给独立角色比较。复现与一次换内容验证分别保存在同一学习项目中；内部最佳、开发者采用与知识发布各自独立。网页和 API 沿用开发者鉴权、owner 隔离和明确写入动作，源码、视频与回执通过产物接口下载。

首版支持 HTML/SVG、Canvas 2D、Three.js 及已绑定的动作能力。Manim 学习、内部技法小样、自适应多个创作者、连续视频模型输入和实际音频评审不在本次已验证范围。真实 HyperFrames MP4 分别保留隔离角色准入产物和本轮实际业务产物；模拟渲染器或 FFmpeg 夹具不替代这些证据，单次内部参考也不代表所有目标均通过。

作者和评审的当前材料是实际采样帧，包含原媒体摘要、请求时间、解码 PTS、片段时间和重复帧映射；这证明被提供的画面，不证明模型理解。模型范围固定为 `sampled_frames_only`，`continuous_motion_verified=false`、`audio_verified=false`。完整视频、音轨、播放器和合成音效存在都不补签连续运动、听感或音画同步；“学习音效”仍是意图，未验证时明确留空结论。

## 构建与部署状态

r2 的干净 Git 构建在 `build_image.py` 的独立 bundle pin 漏同步处失败，该技术失败保留。修复提交 `35310d17f2430b970b442c992dd7ad26b3b43851` 已完成，真实 runtime 资源 99 文件及 control／Pi 构建上下文打包通过，关联回归为 198 passed／2 skipped。该结果说明本次构建准备与回归范围，不代替四镜像构建或产品实验。

r3 四镜像已从完整干净 Git 源码构建并通过离线 smoke，回执为 `r3/build-completed.json`；源码清单摘要为 `358f545831fb8d4844b8314356bb5da9c4f7a487c66fdc0411fba3ffc9fd0859`。该构建回执本身无源码覆盖、部署或模型调用；后续 control／worker 的技术准入与激活另见下节。镜像继续使用 `latest` 标签，确切身份如下。

| 角色 | 镜像仓库 | r3 digest |
| --- | --- | --- |
| control | `arm-cluster-master:5000/agent-service-control` | `sha256:f7d7922a08d65c364c191195ef141784131c8c93c45f2cee0ae4481999e87e89` |
| worker | `arm-cluster-master:5000/agent-service-pi` | `sha256:0a334832372c20852f0b684e0d52793d51267337b05dac055a7a587e3b8a11bb` |
| executor | `arm-cluster-master:5000/video-animation-creation` | `sha256:07346bf8c877bec80b63830b249ef1585a6d12c982088f648460efbd75c49b93` |
| lab | `arm-cluster-master:5000/video-animation-lab` | `sha256:37922b4bc0724e70f5bd2b7f34aa34f097cbcfbc4339a9c01204ff9708bc0200` |

r4 曾针对 UI 修复提交 `55d081fe9ef9126f7d1c5eb35f1ab87dc3bc291d` 启动 executor／lab 完整构建，保留 `r4/build-launch-55d081fe9ef9.json`，未部署。实际发布使用包含评审格式指导的 r5，源码为 `68795854120588e0cae8c787ad504ead850a7971`。`r5/build-completed.json` 为 `built_and_offline_checked`，完整 Git 清单摘要 `ec171e8ec18c4c7ac7cfde4402170af240bba4faf9b5e8810fadd68525f84d60`，无源码覆盖；executor 28 文件、lab 15 文件离线 smoke 通过。

`r5/deployment-completed.json` 为 `deployed_and_source_verified`、`rollout_ready=true`，运行共 43 个源码文件核对通过。executor 为 `arm-cluster-master:5000/video-animation-creation:latest@sha256:d453a0fb02809e77dbc12d9eb9b9279e12f50cef3542f978af0f09bd7454312e`，部署于 `video-animation-creation/video-animation-creation`；lab 为 `arm-cluster-master:5000/video-animation-lab:latest@sha256:3ddc9c7c105c2f6a0adeafbc8df357d738db63490771c70cef036b58c5abb6b1`，部署于 `video-workbench/video-animation-lab`。构建和部署未调用模型；control／worker、渲染器保持既有身份，不因应用修复重复角色准入。部署 Ready 与下文实际业务、浏览器验收分别留证。

r6 仅为详情刷新 UI 修复完整构建 lab，源码 `e269d4350b420120082efb316c1cef7e9a599572`；`r6/build-completed.json` 记录无源码覆盖、15 文件离线 smoke 通过，清单摘要 `4f1247bbfdc8409a824c7a3c0a3b4482f9ebe2d42e6af9f897d90920de542635`。`r6/deployment-completed.json` 确认 `deployed_and_source_verified`、`rollout_ready=true`，运行 15 文件核对通过，最终 lab 镜像为 `arm-cluster-master:5000/video-animation-lab:latest@sha256:76d7816a87e7ff71ca286eb4f63ee96d980bb7dfa48f047c89f39c01166b869a`。本轮不重启 r5 executor，control／worker 和渲染器不变，未增加模型请求或重做已完成渲染。

本记录已分别列出实际构建／部署源码及镜像 digest；后续文档提交另记，不从文档 HEAD 反推线上源码。构建技术失败不能扩写为未运行的业务实验失败；198 passed／2 skipped 只属于当时构建准备关联回归，不代替角色或产品验收，也不与下文本地重叠集合累计。

## 真实角色准入与激活

已核对 `admission/final-admission-summary.json`，终态为 `activated`，源码为 r3 `35310d17f2430b970b442c992dd7ad26b3b43851`，bundle 摘要为 `4bfbd0f9bb20c8a055417287c67e6be14fc05089fba28d8618dad16d40eb119e`。作者与独立评审按各自真实身份准入，激活 r3 control／worker，保留既有路由。回执明确 `technical_host_admission_only=true`、`production_business_end_to_end_verified=false`、`executor_http_live_verified=false`；应用 HTTP、完整产品循环和浏览器交互不能由此勾选完成。

| 角色 | 作业 | 实际帧与请求 | 证据范围 |
| --- | --- | --- | --- |
| `video.reference-learning-author.v1` | `85e7c528-8d1e-45f1-bf5a-d3952caeaf75` | 8 张参考帧，4 次模型请求 | 完成上游帧传输；模型理解未验证，worker／proxy 停止、租约过期拒绝和候选资源精确清理均有回执 |
| `video.reference-learning-review.v1` | `5d306a61-f7eb-4ce5-a584-a74a41b6a6af` | 8 张参考＋8 张输出，4 次模型请求 | 与作者独立的观察和结果身份；同样保留停止及清理证明 |

准入成功尝试共 8 次模型请求；作者 r1 与评审 r3 两次失败各 4 次，全部尝试共 16 次。两次失败均保留原输出、独立 UID 范围和全停／精确清理事实，未自动重试；后续成功不覆盖失败历史。

作者完整源码未经艺术修改，经真实共用渲染器生成 640×360、24 fps、6 秒／144 帧 MP4。源码摘要 `d5ea4b19c320b718418f144d48d22c2b7dd966e8885e6e671efd8ed1219918d8`，媒体摘要 `da367dbe4b8fb233ec6c771557b84adc30c6f49fc9fb701bec009779fd318fa0`；渲染器镜像为 `arm-cluster-master:5000/video-motion-renderer:latest@sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b`，容器结束已验证。此处是技术准入样片，未完整观看、未听审、未证明换内容迁移、未采用、未发布 WikiSkill，艺术质量仍未核准。

## 静态审查与参考选择修复

独立只读审查发现一个会选错创作源的 UI 问题：先成功分析参考 A，再在新建表单改选 B；若 B 的上传或分析收到明确 4xx，原处理器忽略失败返回，仍将旧 `state.analysis.reference_id` 写回隐藏字段。预览保留 B，但下一次开始会跳过 B 上传、以 A 创建任务。此问题按发布前缺陷处理，不归入视觉质量待优化项。

提交 `55d081fe9ef9126f7d1c5eb35f1ab87dc3bc291d` 使用 `analyzeVideoSelection`，仅在本次 `analyze()` 返回 true 时应用参考身份；分析启动、换新文件、选择已存参考和新建表单均清除旧分析状态。独立静态复核确认原失败分支已无法把 A 写回。新增回归通过实际控制器覆盖 A 成功 → 换 B → B 上传 422 → 再开始，并核对创作请求 `reference_ids=['reference-B']`；本轮 UI 共 10 项通过，审查代理未重复运行测试。该证据是控制器与请求合同检查，尚不代表浏览器或生产验收。

同一提交将音频选项明确为学习目标：当前 Agent 接收采样画面，尚不能实际听审音轨或验证音画同步；参考音轨保留供播放。审查的其余重点未发现具体阻断：未知请求保留原 body／key，GET 查看不推进创作，参考原始时间与输出片段时间正确映射，HTML/SVG、Canvas 2D 与 Three.js 合同一致。静态结论不替代真实模型和 HyperFrames 产品闭环。

## 新评审输出格式指导与历史重试

提交 `68795854120588e0cae8c787ad504ead850a7971` 为新 review／transfer_plan 的冻结输入增加 `constraints.output_contract`，列出完整顶层字段、评审必填／可选类型、迁移任务结构、输入摘要绑定及有效结果模板。它独立于 `feedback`，只约束协议表达，不代替实际画面意见；模板摘要必须绑定实际 `read_inputs.details.input_sha256`，模板示例不作为真实评价。`issues` 必须是字符串数组，比较和质量等字段是普通字符串；连续运动和音频仍只能未验证或未请求，迁移计划必须提供任务而不能冒称已经执行。

两项定向检查通过：一项将 review／transfer_plan 示例绑定真实冻结输入摘要，通过实际 `validate_result`，并拒绝错误对象类型和“已验证”音画标志；另一项从历史无指导输入的失败评审恢复，确认提交仅改变实验身份，没有新增作者或渲染调用，也未将新指导补入旧技术重试。使用真实本地 MP4、模拟 Agent／renderer；证明的是格式合同及恢复边界，不是实际模型已稳定遵循格式。

`output-contract-validation.json` 记录上述 2 项已完成运行未重复；视频服务回归退出码 0、17 passed in 67.09s。原运行终态丢失后明确重跑一次，重复计数为 1；该检查没有模型调用、生产改动或艺术验收。文档审查未再运行测试。

## 现有图片数据与浏览器技术验收

`picture-preservation-before.json` 与 `picture-preservation-after.json` 保存升级前后水墨项目 `bcd905f38c29443faa9ec0159d309100`：8 个作品的图片／源码／渲染回执共 24 个摘要逐项一致，3 次实验的身份、状态与停止证明一致，`adopted_candidate_id` 仍为空。该保留检查不改变前述图片艺术判断或历史恢复问题。

实际浏览器进入已认证的视频学习入口，检查三库导航、空库、新建表单和明确的音频未听审说明。详情验收曾发现轮询重建打断展开与播放，提交 `e269d4350b420120082efb316c1cef7e9a599572` 忽略纯更新时间变化，并对有实际变化的重建按项目／作品／媒体范围保留展开、播放时间和状态，防止跨身份或跨作品错误恢复；14 项 UI 检查通过，r6 仅更新 lab。

已核对 `browser-validation.json`，`passed=true`、前端 revision 为 r6。浏览器实际点击 0–2 秒候选，表单正确预填名称、目标、已上传参考和 0／2 时间；未点击开始，仍只有 API 创建的一个项目。该交互验证与实际带 `analysis_id`／`segment_index` 创建分开，后者本轮仅有合同／本地检查。详情显示三份作品、内部最佳、85 分评审、冻结需求和视频／源码／回执下载链接。

实际角色输入成功加载 331,544 字符；手动刷新后历史和输入仍展开。两个媒体均 6 秒、`readyState=4`、`error=null`；共同定位到 3 秒，刷新后参考／输出均为 3.5435 秒、保持播放且静音；切换迁移作品后两侧复位 0 秒。这里检查交互与播放器技术行为，没有完整连续观看、听审或艺术认可。上传、付费创建和下载完整性由前述认证结构化 HTTP 检查，未从浏览器表单再次提交制作。

截图保留在证据根：`video-form-browser.png` 是新建表单上半部分，`video-result-browser.png` 是播放器下半部与控件，均不作为全页截图或完整使用流程的替代证明。

## 真实内部参考 API 产品

归档为 `D:/codex-work/archives/video-service/20261118/video-learning-product/`。已核对 `product-checks.json`、`media-validation.json`、`analysis-transport-proof.json`、实际创建请求和冻结输入。项目 `5b4385351ea4466f9db78d30458b1bef`，实验 `5b5da8c18e85403996df36b5113f19d7`，在原两轮／1,200 秒预算内完成 2 份复现和 1 份迁移，终态 `completed`、`stop_reason=learning_complete`、`stop_proven=true`、`transfer_status=reviewed`。知识与音频学习关闭，没有采用或发布。

实际参考为内部历史 `html-svg-folded-signal-6s.mp4`，原媒体摘要 `0c578e556873ebacd8997ae80b448101ecb9886f7e2e4ad2d3b274ed5992e910`，1280×720、6 秒、无音轨；不是最初调研的 B 站原片。独立分析 `analysis-157e3393fe39421597646ee5a31a2f5f` 实际传输 24 帧，完成并给出 0–2、1–4、3–6 秒三个候选，`creation_started=false`。API 调用方随后另明确提交 0–6 秒全片，冻结 `analysis_selection=null`；此轮证明分析与创建分开及明确范围执行，没有实际覆盖长视频或候选 `analysis_id`／`segment_index` 身份绑定。

| 作品 | 角色与内部意见 | 实际 MP4 SHA-256 |
| --- | --- | --- |
| `9e755f44c25b48d8a574a2a5754e3d79` | 第一轮复现，56 | `6d96b84816e0d67b293d0102ec08c1dda286eafdb815c48f2f3d80ab71169340` |
| `9bd03481942f4a6c8527f82735e2d8f8` | 第二轮复现，85，内部最佳 | `0cc4faf820fee9103032aa31188395bb93b8a39cd68085f02cc6dcc1643d3509` |
| `e6effe3d18af405b97f0c3a57b19686e` | 换内容作品，86 | `d945f4142c6b6b4b641eb93469dbbb627fe5c63a850912e03a365742b5656a5b` |

三份作品均由实际 Agent 完整源码经共用 HyperFrames 渲染，保留源码和渲染回执；全为 640×360、24 fps、6 秒／144 帧，已全解码。每份视频／海报／源码／回执各一，共 12 份产物实际下载并核对摘要。项目、参考媒体、作品媒体由另一 owner 读取均返回 404。这是实际私有 HTTP 与真实模型业务证据，公开浏览器行为另行记录。

6 个作者／评审作业的实际图像传输均覆盖冻结输入：第一轮作者 8 张参考、评审 16 张参考＋输出；第二轮作者 16 张参考＋基线、评审 24 张参考＋输出＋基线；迁移作者无原参考／基线及旧源码，只接收已形成的方法，迁移评审收到 8 张输出。迁移任务改为四个纵向错排的矩形折带模块、自下而上的提示顺序，改变对象数量、几何和排列；6 个模型操作与 3 个渲染操作全部完成。6 个模型作业不等于 6 次上游模型请求，不能与技术准入的 16 次请求直接相加。

56→85／86 是各次独立采样帧意见，不是统一精确艺术量尺。媒体可解码、完整源码和帧输入覆盖不证明连续播放质量、实际听审、音画同步或模型理解；`continuous_motion_verified=false`、`audio_verified=false`、`artistic_acceptance=false` 保持，未把本轮提升推及 B 站参考效果或通用技法掌握。

## 当前可核对的本地检查

证据根为 `D:/codex-work/operations/video-learning-20261118/`。以下沿用各回执的实际范围，未为文档重跑测试，也不将重叠集合累计。

| 回执 | 已记录范围 | 限制 |
| --- | --- | --- |
| `backend-validation.json` | 图片服务／HTTP 37 项、视频服务／HTTP 19 项，以及其中 2 项定向检查通过；使用真实 FFmpeg 媒体和本地私有／公开 HTTP | Agent 与渲染编排为模拟依赖，不是实际模型、生产 HyperFrames 或视频产品验收 |
| `retry-context-validation.json` | 图片／视频各覆盖 render 与 review 技术恢复，4 个定向场景及原服务 39 项回归通过；保留较好基线、源码、原意见和失败阶段输入，恢复时仅实验身份改变 | 其中一项夹具闭包错误修复后单独通过；没有新增模型调用或生产改动，不证明线上技术重试已复测 |
| `wikiskill-capture-validation.json` | 两个隔离本地数据存储的 23 条视频记录经过 capture、snapshot、validate、build-input 与 input-files 兼容检查；含实际解码 MP4 | 只是离线快照兼容，没有知识编译 Agent、发布、激活或正式视频消费；运动和音频均未验证 |

离线知识快照内部摘要为 `f5790bd3d80be0cfccbd1db3a3226d17b4bfe4f5b4bc016772189f9d28951abd`，83,516 字节，生成 24 个输入文件。该摘要不代表新 WikiSkill release。独立角色 r1 的作者准入曾返回 `worker_output_invalid`，原诊断位于 `admission/author-r1-diagnosis.json`；后续源码修复与 r3 真实成功准入分别留证，不覆盖该失败。

## 水墨优化的相关实证与重试问题

图片项目 `bcd905f38c29443faa9ec0159d309100` 的后续有界实验预算为三轮／1,800 秒，WikiSkill 关闭。三份复现图和一份迁移图均实际生成，创作者改完整源码，经 HyperFrames 单帧渲染，再由独立角色比较；没有作品采用或知识发布。

模型对第一轮、第二轮、第三轮技术恢复结果和迁移分别给出 67、72、55、62 的意见。第二轮为内部最佳；第三轮消除部分接缝和雾线，却出现规则浅槽并整体退步；迁移仍有条带皴擦、平坦灰面和前景锯齿边。这些不同请求的评分不是统一精确量尺，只支持局部进展，不证明已学会参考水墨效果或获得用户艺术认可。

第三轮评审作业 `worker_output_invalid` 且已证明停止，随后仅用 `{request_id, mode: retry}` 恢复评审，没有重新创作或渲染。但部署当时的恢复未携带原第二轮比较基线，实际改用了最初冻结的旧基线；源码和 PNG 保持，不等于实际评审图片集合逐字节恢复。后续 r2 代码修复与本地回归单列，不改写原实验 `188a1d818d5e49f693c6b9a24cf398ab` 的 failed 或技术恢复实验 `e739a8795c254e60a8db63c70d44591c` 的 completed 事实。

原件、比较排版与验证摘要保留在 `D:/codex-work/archives/video-service/20261118/ink-refinement/`，以其中 `README.md` 和 `validation-summary.json` 为准。该实验只验证静态图，不用于勾选视频、连续运动或音频目标。

## OpenSpec 同步与剩余目标

OpenSpec 项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的 `evolve-video-agent-creation-and-effect-library` 已更新到 `698fe493b4fa5f7ccc9bb82fd9bf6258c6c775b8`；`openspec-validation.json` 为 `valid=true`、`issues=[]`。本次仅新增勾选 X.17–X.23 七项首版实证，原 121 行 checkbox 逐行保持，X.1–X.9 等宽泛目标未勾选；同步摘要同时写入[精简证据](evidence/video-learning-20261118.json)。规格有效不代替艺术或视听验收。

技术准入、r5 executor／r6 lab 部署、数据保持、上述单次内部参考 API 产品及浏览器技术复验已有各自回执。后续记录继续区分实际构建／部署源码和文档提交，不重复计算已完成模型作业。

真实长视频与候选身份绑定、网页继续／从零／技术重试、取消和未知请求恢复的完整线上使用，按实际覆盖另留证；当前本地检查或单次 API 正常完成不补签所有异常与交互路径。历史技术重试需核对失败阶段实际比较基线，不能只核对源码或目标未变。

连续运动、实际音频／音画关系及艺术认可仍未验证；内部小样、自适应多人、Manim 等后续目标保持。WikiSkill 仍按手动发布：离线快照兼容不扩写为知识编译／发布，新视频实际消费需另有业务证据。本轮首版实证不代表视频学习完整目标已经验收。
