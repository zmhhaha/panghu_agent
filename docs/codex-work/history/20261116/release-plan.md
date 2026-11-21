> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`archives/video-service/20261116/c-rendered/feedback-20261106/release-plan.md`；原文件 SHA-256：`a41ed83a4524785d1e7e3897b8453e19828c8455b0349169bea30f6f12d4dbf8`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 11月6日反馈修复发布准备

本文件为发布准备设计及证据索引。当前已只读联网冻结视频业务基线，尚未构建本次镜像、修改集群、调用模型或制作生产媒体；最新状态见文末。它不把旧目录中的 digest、ConfigMap、UID 或 acceptance 当成当前有效基线。

## 最短可复用路径

发布分成两条独立链路：视频工作台＋执行器＋渲染镜像，以及共享 Agent 的精确 profile overlay＋新增接受组合。前者支持旧图解并新增 compiler6；后者使生成器实际产出 sequence/nested/sources 新计划。只更新工作台并不能让旧 Worker 自动获得新的生成能力。

可以复用既有脚本的文件校验、镜像元数据重写、队列排空、SQLite只读备份/逐表内容比较、不可变 ConfigMap、CAS 补丁和实际 Pod digest 验证逻辑。必须适配当前输入、固定文件清单和新的离线证据，不能原样重放历史脚本或删除 once marker。

1. 固定最终已提交源码，按 allowlist 生成包和逐文件 SHA；所有新文件必须入 Git 和构建清单。
2. 实际发布前重新只读冻结当前两项视频 Deployment、三项 Agent control Deployment、实际 worker pool/host/public config/image digest，核对未发生并行发布。旧 baseline 仅用于了解结构。
3. 在当前明确的 ARM64 镜像上离线构建精确 overlay，不升级依赖。视频需重建 workbench 和 renderer；不能沿用上次 renderer-reuse 分支。
4. 对新视频镜像完成 compile→encode→observe→review→media 预检，覆盖真实反馈的五镜、新嵌套/顺序/来源、旧compiler兼容和过程连续图解；用原有录音，不隐式启动新 TTS。
5. 共享 Agent 先准备独立的新 Worker 与支持新旧资源的 control overlay，生成 disabled 新组合，真实候选验收后再用增量发布改选目标 profile route。保留旧 pool/host/image/acceptance，禁止原地替换旧 pool 的 image 或资源 hash。
6. 视频业务无在途任务时，按既有两阶段 prepare/apply/postcheck 完成两项 Deployment 和 runtime ConfigMap 的一致切换。再做浏览器真实入口验证。不要仅凭 rollout ready 判定修复完成。

## 视频镜像：必须同时更新的身份

普通新图解：`motion-compiler-6` ↔ `technical-diagrams-6`。保留 `motion-compiler-1..5`、process compiler2、普通连续 compiler1/2、过程连续 compiler1 旧 pair。

- Python admission：`video_workbench/motion.py::COMPILER_TEMPLATES`；`motion_render_k8s.py` 从此映射及 continuity/process 表验证生产 template。
- 编译、冻结与复验：`motion.py`、`motion_semantic.py`、`motion_geometry.py`、`motion_review.py`、`motion_dispatch.py`、`motion_snapshot.py`；仍须按冻结 compiler 复编译旧输入。
- JS admission：`motion_renderer/contract.mjs::projectPair` 加 compiler6/template6；`observe.mjs`需支持新来源文字/嵌套等观察对象。
- renderer镜像代码：`contract.mjs`、`observe.mjs`、`run.mjs`、`runtime.mjs`、`experiment.py` 为既有 stage 的五文件清单；仅改前两项也要重建整个精确overlay。
- runtime fingerprint：依赖版本/哈希保持相同，`image_digest`必须改为新 renderer registry digest。不可把新 renderer 搭配旧 runtime.json。
- workbench namespace 与 executor namespace 两份 `runtime.json` ConfigMap 一致，使用新 canonical hash 命名的 immutable ConfigMap。
- executor `VIDEO_MOTION_RENDER_IMAGE`与`VIDEO_MOTION_RENDER_RELEASE`更新；两项视频 Deployment 的应用镜像相同，并更新 revision、digest、source SHA、renderer digest、runtime SHA、release-id 注解。

`build_continuity_remote_r6.py` 不可直接执行：它的循环仅构建 `workbench`，后半段要求 renderer源码及archive与历史 build-r4 完全相同，再复用 `aaa8f1c...`。本次 renderer 源码已变，必须去掉该复用前提并实际构建/读回新 renderer；保留其网络关闭、rootfs层与execution config比较、UID10001可读检查、registry digest回执逻辑。r6、stage-r5 和旧 release operator 的 ROOT、release-id、revision、base、证据 pin 都是历史固定值，需新准备目录和明确新身份。

## 固定源文件清单

### workbench/executor 镜像

以 `video_workbench/build_image.py::SOURCE_FILES` 全量白名单打包，排除 Dockerfile/requirements 后做纯源 overlay。三个本次新增且已补入清单的模块为：

- `video_workbench/web/duration.mjs`
- `video_workbench/motion_semantic.py`
- `agent_service/profiles/video_references.py`

此外本次修改必须入包：`video_workbench/app.py`、`motion_store.py`、`production.py`、`motion.py`、`motion_geometry.py`、`motion_review.py`、`web/app.mjs`、`web/index.html`、`web/motion.mjs`，以及 root 后续新增的预检/引用保留模块。旧文件不能从未核查的分支整包复制到共享 Agent。

### renderer 镜像

固定五项相对 `/opt/renderer/`：`contract.mjs`、`observe.mjs`、`run.mjs`、`runtime.mjs`、`experiment.py`。包中保存全部五项逐字节 SHA 和 source_sha256，即使其中三项没有改动。

### Agent profile overlay

从实际线上 control 和受影响 Pi Worker 各自导出源包，逐项对比后只覆盖：

- `profiles/video.py`：拆分说明的真实720p/24fps约束、来源要求保留。
- `profiles/video_explanation.py`：讲解方案的能力约束与要求保留。
- `profiles/video_motion.py`：新bundle、sequence/nested/sources生成提示、输出引用保留。
- `profiles/video_motion_contract.py`：新纯数据模板和来源结构。
- `profiles/video_references.py`：新增的引用保留帮助模块。
- `resources.py`：按 binding 的 bundle_revision 选择 v1/v2，原v1按原manifest及每项资源独立校验。
- `resources/video-motion-v2/manifest.json`
- `resources/video-motion-v2/CONTRACT.md`
- `resources/video-motion-v2/PLAN.schema.json`
- `resources/video-motion-v2/LICENSE`
- `resources/video-motion-v2/motion-procedure/SKILL.md`

原 `resources/video-motion-v1` 五项保持原字节及manifest SHA `4eba854c6f858a6ec18eb01687a87865dca53efc1346eb4456e6a47bd9dac104`。control与Worker基础源可能不同，分别生成manifest/patch；公开目录0755、文件0644，核对原运行UID10000和实际worker UID均可读。

本次profile已注册，不需要历史 `prepare_overlay_r3.py` 的新增注册三桥，也不能直接运行 process overlay 的“新文件必须不存在”分支。应复用其窄文件范围与源形状验证思路。共享 `registry.py`、dispatcher、model proxy、配置解析、research/ppt/process profile不在本次业务overlay覆盖范围。

## Agent catalog pins 与 acceptance

新motion资源为 bundle name `video-motion`、revision `video-motion-v2`，不是覆盖已发布v1行。catalog generator的 `_motion_bundle()`从当前profile声明读取revision和资源；可复用bundle、contract、environment、binding计算逻辑，发布必须在**当前public副本**上做增量合并，不可用全量generate结果覆盖生产catalog。

每个改动profile的执行代码运行在Worker内；只换control不会更新旧Worker的 build_prompt。若要让拆分和讲解方案的新提示生效，需对 `video.storyboard.v1`、`video.explanation-plan.v1`以及 `video.motion-plan.v1`分别新增指向新Worker digest/runtime的environment及binding。`video.scene.v1`若继续保持旧route可沿用旧Worker，不必因共享模块名扩大发布范围。

新组合必须重新计算：

1. CapabilityBundle：新motion revision及五项精确resource hashes；storyboard/explanation资源未变可引用原bundle。
2. ToolEnvironment：新实际Worker `image_digest`、新 `runtime_revision`、原read/write工具和相应output capability，形成新environment revision。
3. ExecutionBinding：原profile/revision、对应bundle/environment、原adapter/model policy，计算全新 `combination_sha256` 与binding revision。
4. WorkerHost ConfigMap：新profile、binding、runtime/image、execution_catalog；先 disabled。新pool使用新永久UID范围，准备阶段不抢占既有UID。
5. 新acceptance：真实受控候选核对新image/runtime/combination、输入输出SHA、stop proof、隔离和容器终止。旧acceptance不证明新代码组合。
6. Route：验收通过后只把目标profile路由指向新binding；旧rows/pools/acceptance及其他服务callers保持原值，旧资源保留用于在途收束和明确回退。

`promote_agent_process_r3.py`与 `agent_process_candidates.py`固定 process profile和历史proof pin，仅能复用其中的CAS、共享UID ledger、guard释放（明确换行）、停止证据及disabled→accepted机制，不能作为motion-v2替换命令原样重放。`promote_new_profiles.py`也固定旧两profile、旧镜像和UID范围，需适配新已注册profile换binding场景。

## 发布前最低验证

- 离线打包和import：新duration/semantic/references模块实际进入workbench；v1/v2 resources选根、manifest与资源hash检查均通过。
- 新renderer离线JS语法/contract、依赖fingerprint不变、实际运行UID读取与精确源码SHA通过。
- 真实五镜反馈至少编译/观察预检；复杂顺序、嵌套和完整URL在720p及640×360可读；来源显示停留至少5秒按最终录音时长核对。
- 原跨镜过程样片及旧冻结compiler可复验，既有BLOB/失败历史/候选/采用记录不改写。
- 浏览器确认跨镜后台更新不误阻启动，真实同镜冲突仍保护草稿，编辑锁说明可见；时长分别显示预计120、已采用录音157、整片实际161的测试事实，不将未完成或候选当全片总时长。

本计划没有添加额外人工审批要求；真实发布仍须服从当前会话授权、工具权限和共享服务当时的状态。主线不自动合并。

## 当前准备进度

已经只读冻结 `video-baseline.json`：两项业务 Deployment、实际就绪 Pod digest、两份一致 runtime ConfigMap、Docker execution config 和原构建帮助脚本 SHA。当前应用为 `latest@sha256:ba9a9ba2b67fa79fca7715de80f9fb3e90afc9d9465fc59f902445004b8ea5a6`，renderer 为 `latest@sha256:aaa8f1c05e141ac83c182df1a66826f9931d2d0a38278aaecf68d9f12b0ba486`。尚未更改 Kubernetes、数据库、媒体或调用模型/TTS。

当前应用已达125层，不能再直接叠加一层。`build-base.json`核对了上次已使用的124层基础镜像 `latest@sha256:7fc022866ad35f0c06d7078998a6fe58f7b91ca2fb59802d3a1e79b7a14efbc2`：它是当前RootFS层的精确前缀，execution config逐项相同。新应用使用此base加一层完整白名单源代码，保持125层；renderer现为21层，单独重建其五文件overlay。

本目录仅增加两份准备程序：

- `prepare_video_release.py`：`baseline`只读冻结；`package --revision <40位已提交SHA>`只从该Git提交读取白名单文件；`stage`只向本次服务器证据目录写不可覆盖的字节；`build`运行精确双镜像构建；`collect`读回构建回执；`plan`生成可审查的immutable runtime ConfigMap和Deployment UID/resourceVersion/spec CAS patch。**没有实际deploy入口。**
- `build_feedback.py`：复用已验证的双镜像overlay构建和镜像metadata重写；固定帮助脚本、package和operator自身SHA；保留网络关闭构建、相同execution config、UID10001逐文件验证、JS语法、依赖runtime指纹比较、registry digest回执。始终使用latest，不创建额外版本tag。

stage/build须等待根代理给出最终源码commit。后续生成的CAS计划只用于审查；实际执行前仍须重新核对当前基线、设置入口guard、排空在途任务、作SQLite一致备份和逐表内容指纹、验证新镜像真实字体验收，再按原有流程切换和恢复guard。不能把离线镜像构建回执当成上线验收。

构建包、服务器build目录及各回执使用exclusive-create；一次失败后保留已有目录和日志，禁止删除或复用失败marker后直接重跑。需要修复时先记录失败阶段及已产生的tag/digest，再准备独立且重新固定SHA的明确新attempt。

已完成的几何和字体离线证据位于 `layout-geometry-acceptance.md` 及对应summary/equivalence文件：共8场景384张 accepted 截图，其中 nested 场景9张、原镜5经真实 operation.document 补回两条完整来源的场景57张。旧6场景仅有未使用container CSS规则差异，其余逐字节断言等价；旧compiler黄金测试保持通过。此处是发布前证据，不替代新镜像中的复验和浏览器验收。

## 最终镜像构建回执

首轮commit `c683232c1a72eec1aba5da56c9b8dc13a9186db3`已构建workbench及renderer。随后审阅发现两个不同label的短来源也会因为重复计入末行留白而必然预检失败；已按实际CSS行间4px、末行0px、takeaway至来源4px修正，不删除标签或URL。此补丁commit为 `613ecf6428701571a90fd288677c2c8cc82bfddb`，来源/旧compiler黄金/candidate预检171项通过。

最终构建收据集中在 `footer-r1/`，package SHA为 `9a0ae264e04a03b3ab72f833c8885cf271c2667feb9c7b5e960f86b505f19fc6`。最终workbench/executor镜像 `latest@sha256:bce6b2facc7ede3b37c9c7325ad0d47778a3b937e865955c06bd070a357f4eff` 使用124层薄base加72文件完整overlay，源码SHA `cb343ba1267240c1e2a465f3670159df93dff6c449678bee11a620be505f4e2d`。renderer使用本轮首轮新构建 `latest@sha256:a437f7cb8c3f50bfd0cf08441bd341ec153568e418246c484b9dd64d22f33800`；两commit的五份renderer源码与对应source inventory逐项相等，source SHA为 `62368d64e349151870f5d81008b97611fe668447143beeed6b8a32da693c8dda`，不是复用旧线上aaa8镜像。新runtime canonical SHA为 `ce8fd3ff7ad6d5aeb99638071181e86b7271def360b404f646a6734cd0e6e0c3`，ConfigMap名 `video-motion-runtime-ce8fd3ff7ad6d5aeb996`。

最终两镜像均通过UID10001精确文件读取、import/JavaScript语法检查、execution config及rootfs比较；renderer实际依赖fingerprint与原线上一致。最终新镜像绑定的两例静音encode/observe/strict review及四例旧processcontinuity精确回放正在 `bound-preflight-r1/` 执行，结果独立记录；本文件与 `footer-r1/video-release-cas-plan.json` 没有执行部署。真实发布operator由根代理独立准备和审查。
