# PPT Service

独立的多用户 PPT 制作网站。当前已实现网站、项目管理与材料上传；Pi 模型生成链路仍在接入。

2026-10-27 审核与修改：网站的 Hermes 业务链已能生成待审核草稿。版本页接入逐页问题和桌面 PowerPoint 实际检查记录，保存记录不调用模型。用户可明确选择一份完整且来源有效的审核稿，通过书面意见创建新审核稿；也可带入当前已保存的问题页。原稿和审核历史保留，新稿仍须独立校验并保持待审核，不自动成为正式发布版本。Pi 接入及最终 PowerPoint 验收仍有待办。部署使用 Git 源码和正式 schema 3 迁移，服务器验证与实际发布结果以本轮收据为准。

2026-10-28 项目授权简化：外部模型数据传输同意按项目保存，首次确认后，上传、规划、生成和修改均复用服务器记录；刷新、换页及重新打开项目不再重复勾选。已授权项目显示状态提示，上传不再重复保存授权而使方案确认失效。未授权或明确撤销授权的项目仍禁止上传及提交模型任务；保存设置时省略授权字段会保留已有选择。审核稿的版本来源、检查修订及方案确认仍需有效，生成任务仍由用户点击提交。

服务器部署入口（2026-10-24）：namespace 为 `ppt-service`。按 [部署说明](deploy/k8s/README.md#服务器标准入口) 配置 Casdoor、邮箱名单、Cloudflare 和 Vault 的 `secret/ppt-service/...` 凭据后，在本目录运行 `bash deploy.sh`，完成构建、推送、数据库初始化或升级及网站发布。也可先运行 `bash build.sh`，再执行 `bash deploy.sh --skip-build` 复用已推送摘要。原 Hermes Worker 构建入口为 `build_worker.sh`；网站发布保持模型执行关闭。

## 历史开发与样稿记录

以下记录保留当时的开发入口、执行器和验证状态；当前网站部署以以上入口为准。

最新状态（2026-10-22）：用户已通过真实选择 `website_development_before_manual_gate` 选择“先开发并测试网站（推荐）”，允许 PowerPoint 人工验收前开发测试；正式发布/邀请用户前仍须实际 PowerPoint 验收。本机 SSH 开发入口为 `http://127.0.0.1:18505`，采用固定 local test identity，FastAPI/PostgreSQL 项目 CRUD、上传和受控材料解析已真实联通，网页可显示解析状态及受限摘要；正式 Casdoor SSO 与 PPT 生成闭环尚未完成，`POST /plans` 仍返回 503 `execution_not_connected`。R8/V2 原 actor 仍为 needs_input，控制器独立 review 未使其成为 accepted base。最新开发测试与限制见末尾；下文旧实现状态、顺序决定和镜像验证均为历史快照。

历史状态（样稿执行器阶段）：执行器样稿验证中。ARM64 固定依赖镜像与禁网 Hermes HTTP 探针已通过；执行器、候选校验和代理用量账本的 91 项测试在目标 ARM64 Linux 容器中全部通过。公开资料样稿的第一阶段已真实执行，网络隔离门禁、源材料哈希和 8 个候选归档/规划文件的完整性校验通过，用户已确认样稿参数。第二阶段 `plan_options` 已从停写检查点恢复并完成：10 项候选文件、136658 bytes 与输入哈希校验通过，Worker/代理停止且临时网络和防火墙规则已清理。用户已通过聊天真实选择 A「证据手册」并确认六页计划、原生图表、仅已有图片、无备注旁白动画及逐页视觉复核，默认不增加锁定前审阅；`prepare` attempt 3 已执行收尾，Worker/代理停止且临时网络和防火墙规则已清理；可信控制器规范化候选 manifest 后，独立严格校验核对 26 项 artifacts、278281 bytes 与输入哈希，`quality_reports_verified=false`。控制器在独立无链接副本中核对已确认页文案、配色和图片，并在禁网容器重跑官方 attribution_guard 与 project_manager validate，均 exit 0；这些证据不代表质量报告已全部独立验收。`generate` attempt 4 触及 1800 秒硬时限，以 `deadline_exceeded` / `worker_failed`（exit 3）收尾；Gateway、Worker、代理均已停，临时网络和防火墙规则已清理。时限边界前落盘的六页 PPTX 仅保留为未验收人工审阅草稿，未发布，失败检查点不作为恢复基线。独立诊断核对原生图表及嵌入数据，归属 guard 与官方 final SVG checker 均 exit 0（6/6 passed、0 warning、0 error）；这些静态检查不代表 PowerPoint 渲染或人工视觉验收，`quality_reports_verified` 仍为 false。当前会话模型不支持 image input，逐页视觉检查未完成。用户先暂停后于 2026-10-21T15:54:44+08:00 回复“可以，继续”，授权从成功 prepare checkpoint 开始 45 分钟受控 retry；这不构成 PowerPoint 人工验收。attempt 5 已停写收尾，但缺少 manifest 与 PPTX，独立校验拒绝，阶段交付失败；仍等待真实逐页反馈及最终 PowerPoint 人工验收。

修复构建进展（已构建本地镜像，未 push 或部署）：代理 SIGTERM 关闭记录 `proxy_shutdown` / unknown，不补历史每阶段 2 条缺项；候选校验兼容标准 printerSettings 与精确 root metadata，仍拒绝 macro/OLE 等；样稿增加显式 `--deadline-seconds`，默认规划 600 秒、其他 1800 秒不变，规划上限 600 秒、其他上限 3600 秒。新镜像 UID 10000/aarch64 smoke 通过 Chromium 141.0.7390.37 与 Noto CJK；在禁网、只读根、drop ALL、no-new-privileges、限资源且不挂凭据或 Docker socket 的容器中，107 项测试全部通过（7.536 秒，含真实 Linux SIGTERM）。本地另有 8 项 checkpoint/deadline 测试通过。本次 retry 已使用该镜像启动：任务 `/root/ppt-service-sample/acceptance-v3/sample-task.generate.retry-5.json`，attempt=5，SHA-256 为 `7e6ab2e75b94b7d52ca988fcaec820e78f645b7c158cd19bfeb81104c058acf2`；运行器 `/root/ppt-service-repair-20261021/scripts/run_sample.py` 显式传入 `--deadline-seconds 2700`，全局默认 600/1800 秒不变。来源为已独立校验的 prepare attempt `4fae7e47cd82499bb83c01c4188845d3`，失败 generate attempt `612bf88efea84f65bf066918e065bd4b` 不提升为基线。attempt `5a1702b416164cfaa68d7e9d27d77506` 的 network-preflight passed；现已停止，runner status 为 `candidate_execution_finished`、Worker exit 0，Hermes execution 为 `completed`（779.87 秒）、`gateway_stopped=true`，Worker/代理均 exit 0 并停写，临时防火墙与 internal 网络已清理。这是执行器收尾状态；独立 `validate_sample` 因 `FileNotFoundError`（缺少 `result/manifest.json`）拒绝，实际仅有 3 个 SVG，无 PPTX，阶段交付失败，部分 runtime 不可提升为恢复基线或 accepted base。Agent 最终用户可见回复说明工具执行授权等待超时而暂停，受阻命令未运行且未绕过；P4–6、preview、final、export、manifest 均未完成，本次未触发 45 分钟 deadline。任务仅增加 `quality` 应只列真实报告 path/SHA、说明移至 `quality_notes` 及简洁清单要求，六页方案与数据不变；之前 failed 草稿和原始记录保留。

attempt 5 代理账本为 21 started / 21 finished，outcome 为 18 completed / 1 client_disconnected / 2 proxy_shutdown，无 unpaired 或 duplicate，SIGTERM shutdown 修复已获本次真实运行证据；`provider_cost_reported_count=0`，费用保持 unknown、待 provider 对账，不写 token 总值。控制器拒绝证据已保存为运行根目录的 `retry5-controller-rejection.json`，本地副本为 `.runtime/recovered-options/retry5-controller-rejection.json`；spec/lock SHA 已实际比对，与 prepare 一致。内部审批规则定位与 Worker 候选交接合同修复已完成本地构建；未部署，源状态原件保持不变。准确诊断为 terminal 中纯算术 `python3 -c`（无 file/net/process 操作）触发通用 `script execution via -e/-c flag`，等待 333.10 秒后超时，命令未运行。`smart` 模式默认 timeout 300 秒且 API 保留 `ask`，`NON_INTERACTIVE=1` 不等于 no-wait；具体 smart guardian 未批准原因仍 unknown，不能断言一定是 guardian timeout。审批策略未关闭、白名单未扩大、blocked command 未执行；本次是工具审批与交接合同问题，不是源材料失真。

此前本地修复镜像为 `ppt-service-worker:arm64-c350bc31-preview4`，image ID 为 `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`，构建目录 `/root/ppt-service-preview-20261021`；固定 base/vendor 不变。UID 10000/aarch64、Chromium 141 与 Noto smoke 通过，新镜像受限禁网容器内全部 142 项测试通过（9.503 秒、无 skip）。此次仅本地构建与无模型生命周期实测，未 push/部署，后续模型 retry 未启动；attempt 6 仍为 needs_input，无 PNG/PPTX 或 PowerPoint 人工验收。后续控制器私有 working continuation 已通过 166 项受限测试（9.121 秒、无 skip）与免费 preflight，Worker 镜像未变；尚未获下一次收费授权，详见末尾补记。

前版本地修复镜像构建目录为 `/root/ppt-service-handoff-20261021`，tag 为 `ppt-service-worker:arm64-c350bc31-handoff3`，image ID 为 `sha256:a11c936ddeb94c8d1e2a217c410778f659ea277dcf22b946d029fe06f4b3247b`；固定 base/vendor 不变。UID 10000/aarch64、Chromium 141.0.7390.37 与 Noto CJK smoke passed；该镜像在禁网、只读根、drop ALL、no-new-privileges、限资源且 tests/scripts 只读、无凭据或 docker.sock 的容器中，125 项测试全部通过（7.566 秒）。旧原件、全部镜像与 attempt 保留，每次实际使用以任务记录为准。

该镜像的 `execute.py` 对候选交接执行 4 MiB 有界读取、普通非链接文件、strict JSON 及 phase/snapshot/status 检查；缺失或非法 manifest 以 exit 4、`candidate_output_missing` / `candidate_output_rejected` 失败，保留 `hermes_status` 且 `candidate_validated=false`，最终独立 validator 不变。初稿 prompt 优先简单算术与文件工具，不提前调用官方 calculator；blocked 时真实交接 `needs_input`，不伪造 candidate/pass，并修正 CLI 聊天确认与网站措辞。

修复构建完成，样稿阶段仍交付失败。无 push、无部署，未进行后续收费 attempt；prompt 调整尚未经新收费生成复验。未来 retry 仍需用户选择，从成功 prepare 开始新 attempt，不恢复 failed runtime；真实逐页反馈及最终 PowerPoint 人工验收仍待完成。网站/API/数据库/生产部署尚未实现，尚无验收通过或正式交付的 PPTX。此服务独立于现有研究助手，不依赖 CrewAI 研究流水线。

## 设计与任务

- [完整架构与交互设计](../../docs/ppt-master-hermes-design.md)
- OpenSpec change：`add-hermes-ppt-workbench`，提案、规范与实施任务以仓库对应的 OpenSpec Service 项目为准。
- 代码归属：`panghu_agent/ppt_service/`；Kubernetes namespace 和服务部署名沿用 `ppt-service`。

## 历史计划目录（保留当时状态）

下面同时列出已实现执行器和后续应用布局；`api/`、`web/`、`dispatcher/`、`k8s/`、`deploy.sh` 尚未建立。执行器测试可在 `panghu_agent` 目录运行 `python -m unittest discover -s ppt_service/tests -v`。构建、固定依赖和验收状态见 [镜像说明](images/README.md)与[样稿说明](samples/README.md)。

```text
ppt_service/
├── web/          # React + TypeScript：项目、方案确认、预览与修改
├── api/          # FastAPI：身份、材料、任务、事件和版本
├── dispatcher/   # 数据库队列与固定模板 Kubernetes Job 调度
├── worker/       # 专用 Hermes 生命周期和 PPT Master 阶段适配
├── images/       # 固定版本 ARM64 执行与校验镜像
├── k8s/          # 独立 namespace、存储、权限和网络策略
├── build.sh      # 网站镜像：服务器构建、验证、推送私仓
├── deploy.sh     # 网站部署（默认先构建）
├── build_worker.sh # 原 Hermes Worker 镜像构建入口
└── deploy.sh     # 部署入口
```

## 已确认的第一版需求

- 少量受邀用户；已有资料转可编辑草稿，每份 3–20 页，全局单任务执行，其余排队。
- 网页大纲确认和逐页文字反馈；有明确源数据的受支持图表可在 PowerPoint 编辑数据。
- 忠于材料，缺关键数据时提问或标注，不编造事实、数值或引用，不联网补齐。
- 上传前明确告知并确认：必要正文和图片会发送给服务指定的外部模型。
- 所有者承担费用，初期不设金额上限，先持久记录实际用量；生成、修改和失败中的已发生费用均计入。每用户额度机制保留，数值/周期待实测后确定，未配置时显示未启用。
- 原始材料、成品及恢复检查点保留到用户主动删除；显示存储占用，配额耗尽暂停新增而非自动删除。

## 首个实施验证

先用公开资料做 5–8 页样稿，验证专用执行器闭环：规划后退出 → 用户确认 → 从检查点恢复生成 → 逐页文字反馈 → 从指定旧版本继续修改。

验证包括 Hermes 认证 Runs/Sessions 接口、工作目录与挂载隔离、完整 PPT Master Skill、ARM64 依赖、质量门、预览与实际 PPTX。通过后再展开后端和网页实现。

用户操作流程：上传材料 → 编辑确认大纲 → 排队生成 → 逐页预览 → 提交修改意见 → 下载历史或最新版本。

## 最新状态：attempt 6 已授权启动，待核验

用户通过真实 `ask_user_question`（id=`handoff3_retry_decision`）选择“按修复方案再试一次（推荐）”，授权界面为 `chat_question_selection`，仅 1 次、2700 秒。新任务 `sample-task.generate.retry-6.json`，SHA-256 为 `fa8df7b7ac88071997a1ffe8a6aae3641761e09570d8e1377ea97194444f3dee`；从成功 prepare `4fae7e47cd82499bb83c01c4188845d3` 开始，使用 `ppt-service-worker:arm64-c350bc31-handoff3`，image ID 为 `sha256:a11c936ddeb94c8d1e2a217c410778f659ea277dcf22b946d029fe06f4b3247b`。

新 attempt `0be96ee63ee145218cbbf47d9c1c47c3` 在启动后 103 秒的快照中为 running、network-preflight passed，尚无 SVG/PPTX/manifest；账本 7 started / 6 finished，仍有 active 请求，不据此判定缺账或完成。前述“未进行后续收费 attempt”等为构建当时的历史状态，当前已按真实授权启动此次 retry、等待收尾和独立核验。attempt 4/5 均不作为基线，全部原件保留；尚无新 PPTX 交付、发布或 PowerPoint 人工验收。

## 最新收尾：attempt 6 needs_input，live-preview 前提待补足

attempt `0be96ee63ee145218cbbf47d9c1c47c3` 已于 1280.34 秒结束，Worker/代理 exit 0，Gateway 与容器均已停写，防火墙及 internal 网络清理为 true；未触发 2700 秒总 deadline。manifest 为 `needs_input`；独立严格 `validate_sample` 完整性校验通过，核对 18 artifacts、184910 bytes，`input_assets_verified=true`、`quality_reports_verified=false`、`pptx=[]`、questions=2。尚无 PNG/PPTX、发布或 PowerPoint 人工验收；needs_input 不视为成功 candidate 或 accepted base。

准确障碍是官方 `visual_review.py` exit 2：没有 running live-preview server。Agent 未启动该服务，顾虑 preview daemon/background terminal 限制；`approval_blocked=false`，此次不是审批阻断，也不是业务缺事实，需补足控制器权限与服务前提。父代理正实现 Worker supervisor 托管真实官方前台 server（不使用 `--daemon`），模型仅使用 ready loopback URL，继续禁止 Agent background/PTY，审批策略不变。官方 foreground、project/PID health、实际 shutdown wait 契约已只读确认，空 SVG 状态可启动并动态发现新文件；该修复源码在此快照尚未构建，未发起新收费尝试。

控制器在 no-link 私有副本 `controller-check-retry6-0be96...` 独立禁网限权检查：attribution_guard exit 0，static final checker 6/6 passed、0 warning、0 error，spec/lock 实际哈希与已接受 prepare 一致；未渲染 PNG、未导出 PPTX、未升级 accepted base。账本 37 starts / 37 finishes，无 unpaired/duplicate，35 completed / 2 proxy_shutdown；observed input/output tokens 为 4081593/34586，provider reported cost count=0，费用保持 unknown，不记 0、不估算人民币。

运行根目录已保存 `retry6-validation.json` 与 `retry6-usage-observation.json`；本地证据为 `.runtime/recovered-options/retry6-validation.json`、`.runtime/recovered-options/retry6-visual-render-blocker.json`。前次 attempt 4/5、全部镜像及原件保留。后续模型 retry 尚未授权，先进行无费用生命周期测试及修复本地构建；完整生成、真实逐页反馈与最终 PowerPoint 人工验收仍待完成。本轮仅同步文档，OpenSpec 额外 artifact 路径被服务拒绝，未改历史 tasks、勾选或规范，未 apply/archive。

## preview4 构建与官方前台预览生命周期实测

`ppt-service-worker:arm64-c350bc31-preview4`（image ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`）已在 `/root/ppt-service-preview-20261021` 本地构建，固定 base/vendor 不变，UID 10000/aarch64、Chromium 141 与 Noto smoke 通过。新镜像本身在禁网、只读根、drop ALL、no-new-privileges、限资源、无凭据或 docker.sock、tests/scripts 只读及 tmpfs 环境下，142 项测试全部通过（9.503 秒、无 skip）。其中包含真实官方前台 server 空项目启动、health/project/PID 绑定、shutdown 与进程组消失的生命周期验证；未渲染 PNG、未发模型请求，本次文档同步不重跑测试。

新的 `preview.py` 由控制器固定本项目 `127.0.0.1:8765`，运行官方 `server.py` 真实 foreground、不使用 `--daemon`，health 核对 pid/project/service/live/status/svg_output；最小环境不含 model/API keys。admit 前必须 ready，失败不提交收费 run；finally 通过真实 HTTP JSON 关闭，再 wait/TERM/KILL 进程组，cleanup 失败 exit 1。检测到旧派生 preview 锁即 fail closed，不 kill 旧进程、不改原件。

COMMON 与 input_message 已改为控制器托管 URL 和实际通道；继续禁止 Agent background/PTY，审批策略不变，SVG 不能替代 PPTX，PNG 不等于视觉门通过。此次仅构建和生命周期验证，无 push/部署，后续模型 retry 未启动；attempt 6 仍为 needs_input，无 PNG/PPTX、发布或 PowerPoint 人工验收。合法 needs_input 的私有 working continuation 已完成严格校验、166 项受限测试及免费 preflight，仅用于私有 working 副本，不是 accepted base；控制器包为 `/root/ppt-service-continuation-20261021/ppt_service`，preview4 Worker 镜像及 ID 不变，详见后续补记。全部旧镜像、attempt 与原件保留。

## 私有 needs_input working continuation 与免费 preflight

控制器新增默认 false 的 `--continue-needs-input`，并与 task.continuation 严格五键对象双重必需：`kind=needs_input`、`source_manifest_sha256`、`approved_spec_sha256`、`approved_lock_sha256`、`resolution=worker_supervised_official_preview`。仅允许 generate→generate，从合法 needs_input 源创建递增新 attempt；核对两容器真实 exited 0、挂载与身份、两道确认和 assets 一致、实际 inputs 哈希、完整 manifest artifact/quality refs 哈希、批准 spec/lock 哈希、无任何 PPTX、全树无链接。不接受 failed/timeout/candidate/revise 或 base_version_id，默认阶段转换不变。控制器脚本不打入 Worker 镜像，preview4 ID 仍为 `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`。

新控制器加 preview4 已在真实 ARM64 禁网、只读根、drop ALL、no-new-privileges、tmpfs、无凭据或 docker.sock 的环境中通过全部 166 项测试（9.121 秒、无 skip），CLI help 确认新 flag。免费 probe `/root/ppt-service-sample/acceptance-v3/controller-continuation-probe-6` 只调用 continuation_inputs 与 checkpoint clone，没有 run/admit；确认实际 attempt 6 源 Stage1/2 非空，scope=`private_needs_input_continuation`，18 文件/184910 bytes 完整性通过，6 个 SVG 哈希逐一与源一致，源 manifest SHA-256 `e2da44ca17feea10232d7c27e60ba0fd23f763d3ee18af04134a986dc4b26b88` 未变。

clone 项目另在 preview4 禁网、无 key 临时容器中启动真实官方前台服务，确认 health ok、live_preview、实际同项目、6 slides、PID 等于 owned child，随后进程组真实停止。未渲染 PNG、未导出 PPTX、零模型请求、未产生业务 attempt 7、未提升原 runtime 或 accepted base、未 push/部署。probe report 保存在 probe 目录的 `probe-report.json` 及本地 `.runtime/recovered-options/continuation6-probe-report.json`。

下一次拟从保留的 6 页只继续 pending Step 7 视觉检查/导出，仅实际视觉发现问题才修正；此快照尚无该次收费授权，未启动续跑。attempt 6 仍为 needs_input，真实逐页反馈与最终 PowerPoint 人工验收未完成。本轮仅文档同步，不重跑测试、probe 或模型任务。

## attempt 7 候选 V1：完成制稿，待用户审阅与 revision

用户通过真实问题 `resume_verified_six_svg` 选择“复用六页继续（推荐）”。任务 `sample-task.generate.continue-7.json` 的 SHA-256 为 `d1d38cab5f4e48af8ef5e93794c46e8c130a3bf3cae42f14c2cbff78f859fb22`；scope=`private_needs_input_continuation`，来源 attempt 6 manifest SHA-256 `e2da44ca17feea10232d7c27e60ba0fd23f763d3ee18af04134a986dc4b26b88`。使用 preview4 镜像，ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`。新 attempt `ed00fb6abfe2489b8f7834ce43f66d9b` execution completed、750.2 秒，Worker/代理 exit 0，Gateway/preview 停止为 true，防火墙/internal 网络清理为 true，`delivery_published=false`。

原 manifest 无需 normalize 或修改，直接通过严格 `validate_sample`：status=candidate，33 artifacts、965257 bytes，inputs verified=true；6 slides、1 chart（slide 4、chart401），可信 Q1/Q2/Q3、Cloud[12,15,19]、Services[8,9,11] 的 cache 与 embedded workbook 均核验通过。`quality_reports_verified=false`、`visual_acceptance=false`，未发布。6 PNG 与 Agent 视觉报告已产生，但不代表父代理独立视觉或 PowerPoint 验收。

PPTX `exports/acceptance-public-sample_20261021_112554_native_charts_tables.pptx` 为 43360 bytes，SHA-256 `dcff0a90aa089ef4e46746d516f95fa148da0223cf67ff2daecec28108006068`；已复制为本地 `.runtime/recovered-options/acceptance-sample-candidate-v1.pptx` 并提供用户手工审阅。独立 python-pptx/OOXML 核对 16:9、6 个可见页面、每页可编辑文字（P2 连续长文本 234 字符）、P4 column clustered 原生源数据图表；仅 P3 一图与原 SHA 一致、4:1 全幅无 crop 且 bounds 在 slide 内，无备注/动画/转场，聊天确认措辞未虚称已通过网站提交。证据为本地 `retry7-independent-pptx-check.json` 与 `retry7-validation.json`。

控制器 no-link 私有 snapshot `controller-check-retry7-ed00fb6abfe2489b8f7834ce43f66d9b` 重验 33 项 artifact 图及 native oracle，通过；spec/lock 批准 SHA 不变、旧 preview 锁 absent。在独立 scratch 禁网限权 pinned image 中重跑 attribution_guard exit 0 与 static final checker 6/6 passed、0 warning、0 error，记录在 snapshot 的 `controller-check.json`。该 snapshot 不是 accepted base，也未发布。

用量 26 starts / 26 finishes，无 unpaired/duplicate，24 completed / 2 proxy_shutdown；observed input/output tokens=3344823/18169，provider reported cost 0 条，费用 unknown，不记作零。attempt 4/5/6、原件及全部镜像保留。当前需用户提供 PowerPoint 版本并实际打开、编辑数据、保存重开，提交真实逐页反馈后至少完成一轮 revision；后续角色/基线待用户明确选择。样稿尚未最终验收，网站/API/DB 未部署；父代理不具备可用视觉能力，不宣称最终 PPTX 渲染已检查。本轮仅文档同步。

## R8 / V2 人工审阅版：actor needs_input 与独立 review

用户于 2026-10-21 20:48 回复“可以，继续”，授权已提议的 P3/P5 状态文案修订；task revise8 SHA-256 `987cf8b31ab0c8d0dbc08133c38c1a266462eacea3dc973f6555a2e56c805453`，`base_version_id=sample-review-v1-dcff0a90aa08`，review-version-maps 绑定原 V1 SHA。这是修订授权，不是 PowerPoint 实际验收。attempt `d6ddeff1bd5f4a8a8bb6e7e498894017` 使用 preview4（ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`），execution completed、1286.08 秒，Worker/代理 exit 0，Gateway/preview 停止为 true，网络与防火墙清理为 true。

原始 actor manifest.status=NEEDS_INPUT，仅声明 5 项 artifacts，图引用不完整；末尾额外只读包比对 `python3 -c` 工具授权等待超时，命令未运行，known rule 为 unknown。不能表述为原 manifest 通过、只是漏目录或正式 candidate 成功。原 `result/manifest.json` 未改。用户 21:50 再回复“继续”后，父代理仅独立只读检查，未重跑受阻命令、未启动模型；单独收集 33 文件实际哈希 inventory、970608 bytes，记录 `controller-review8-collection/manifest.controller-collected.json` 仍为 needs_input，原 manifest 另存且哈希原样。不提升 runtime/accepted base、不发布。

新 PPTX `exports/acceptance-public-sample_20261021_131243_native_charts_tables.pptx` 为 43274 bytes，SHA-256 `1819eb54fe25b1d760f4318da656425fb39a733432bba538b991bb1d58d770ed`；本地 `.runtime/recovered-options/acceptance-sample-review-v2.pptx` 已以“人工审阅版”提供。实际仅 SVG03/05、PNG03/05 改变；P1/2/4/6 SVG bytes 与 PPTX 的对应 slide XML 均完全相同。独立 `revision8-independent-pptx-check.json` 实际读取两份 PPTX，确认 6 页/16:9、只 P3/P5 文字变化、P4 原生 column/clustered 的 cache 与 embedded XLSX 保持 Q1/Q2/Q3、Cloud[12,15,19]、Services[8,9,11]，P3 原图 SHA 一致、4:1 全幅无 crop 且 bounds 在页内，无 notes/animations/transitions。

远端 `final-review-check.json` 独立 inspect_pptx 的 chart data verified=true；no-link snapshot 与 scratch 禁网限权重跑 attribution_guard exit 0、static final checker 6/6 passed、0 warning、0 error。该独立 review 不修改 raw actor 状态，也不代表 PowerPoint 或最终渲染验收。账本 29 starts/29 finishes，无 unpaired/duplicate；26 completed/1 provider_failed/2 proxy_shutdown，observed input/output tokens=2960027/24312，provider cost 0 条，费用 unknown、不估为 0。全部 attempt 4/5/6/7/8、镜像与原件保留。

项目 marker pair 实际为 `['.', 'revisions/round-08/base']`，preview.py 曾误判多 root；已做 direct-child-only、保留全树安全扫描与 parent-marker 拒绝的修复，Windows focused 15 项为 12 passed/3 skipped。preview5 后续已完成构建与 Linux 验证，见下方最终证据；前述 Windows 结果保留为历史记录。COMMON 已补齐未改页必须全部索引、官方检查不可跳；独立包比对应归 trusted publisher，审批规则未变。网站/API/DB 未部署，PowerPoint 实际验收与真实逐页反馈仍未提供，V2 仅供人工审阅。本轮只同步本地文档，不执行测试、模型或 MCP 更新。

## preview5 最终构建与嵌套 marker 实测

最新本地镜像 `ppt-service-worker:arm64-c350bc31-preview5`，image ID `sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a`，构建目录 `/root/ppt-service-preview5-20261021`；base/vendor 不变，UID 10000/aarch64、Chromium 141、Noto smoke 通过。真实新镜像在禁网、只读根、drop ALL、no-new-privileges、tmpfs、限资源、tests/scripts 只读、无凭据或 docker.sock 环境中，169 项测试全部通过（9.145 秒、无 skip）。R8 历史执行仍使用 preview4，不回填新镜像。

preview.py 仅选择当前项目根的 direct-child marker，拒绝工作区 parent marker 与多 sibling；嵌套备份仍执行全树 bounds/links/hardlinks/special-file 安全扫描。实际 V2 只读副本含 `['.', 'revisions/round-08/base']`，pinned preview5 真实 foreground health 绑定当前 project/PID/6 slides，随后进程组实际停止；model requests=0、pngs_rendered=false。报告为控制器运行根目录下的 `controller-preview5-nested-health/probe-report.json`。来源只读，R8 原项目与原 manifest 仍为 needs_input，无 promote/push/deploy。

COMMON 要求全部引用（含未改页）完整哈希声明；包级独立比对归 trusted publisher，官方检查与审批停止边界保留，不绕过或关闭审批，也无需把额外 Python 临时比对当作清单前置。控制器补全索引后的 `/root/ppt-service-sample/acceptance-v3/controller-review8-collection/final-review-check.json` 独立核验通过：33 files、970608 bytes、status 仍 needs_input；另列 inspect_pptx 6 页/1 个 P4 原生图表，cache/workbook 数据 verified=true。实际静态 attribution_guard exit 0、final checker 6/6 passed、0 warning、0 error；原 actor 记录保持原样。

用户 21:50 的“继续”允许当前只读核验与整理，不是 PowerPoint 人工验收声明。是否将网站开发测试提前到 PowerPoint 人工验收前，尚待用户明确选择；当前仍按人工验收后再展开完整业务的规则，未因此修改规范或获得业务实现授权，PowerPoint 验收仍保留为正式发布/邀请前门禁。当前 V2 仍仅供人工审阅，网站/API/DB 未部署，无新增模型请求。本轮仅同步本地文档，不改源码、不执行测试或 MCP 更新。

## 2026-10-22 当前开发测试：网站与材料解析已联通

用户通过真实 `website_development_before_manual_gate` 选择“先开发并测试网站（推荐）”，已授权人工 PowerPoint 验收前进行网站开发测试，更新前节“顺序尚未授权”的历史状态。正式发布与邀请用户前仍需实际 PowerPoint 验收。主代理已恢复 OpenSpec 并更新 design/proposal，记录 revision `4268e63abfaa45f59da2bb87aefd574085e00fa3`、validate valid；未 apply/archive，tasks 暂仍为 26 项/已完成 3 项/剩余 23 项。本次文档同步不访问或修改 MCP。

开发站 `http://127.0.0.1:18505` 经本机 SSH 访问，使用固定 local test identity，不是正式 Casdoor 登录。FastAPI/PostgreSQL 已真实执行项目 CRUD 和 HTTP 上传 201→queued→实际 Docker 解析→DB ready+summary→GET；网页提供材料状态与受限摘要。部署时点快照为 5 projects、2 assets，材料均 ready，plans/jobs/versions/checkpoints/usage 均为 0；后续浏览器 fixture 可增加数据，这不是永久实时计数。

已有前端 27 项测试与 11 项实际浏览器测试记录；parse UI 新增后为 41 项测试通过并完成构建 `Cb8shtpo`，新的 parse browser 仍在运行，尚未记录通过。后端此前 20 项测试（含 3 项真实 PostgreSQL）、控制层 23 项（含 8 case 真实 PostgreSQL）、parser 3 项真实容器测试；本轮 37 项为 17 项材料真实 PostgreSQL 测试（含实际 Docker 解析→PostgreSQL）加 20 项 API 回归。此处仅同步主代理提供的结果，不重跑测试。

schema 2 增量迁移保留原 4 projects、1 asset 的 mapping/hash/bytes；迁移前备份为 `/root/ppt-service-webdev-20261021/backups/pre-materials-20261022`。开发 API 镜像 `ppt-service-api:parse-dev-20261022`，ID `8148cd5182e7`，API 容器 `b516ac8a15c7`、IP `172.18.0.3`；新增 parse coordinator `694a11cfe224`、IP `172.18.0.4`，名称 `ppt-service-materials-dev-20261022`。未挂 upstream env，paid=false、model disabled。仅 trusted outer 持 Docker socket/DB/root DAC_OVERRIDE；实际 parser 子容器 network none、non-root、read-only、drop ALL，无 DB/Docker socket，DATA_ROOT/code 采用相同路径 bind。

生成尚未接通：`POST /plans` 为 503 `execution_not_connected`。正式 SSO、trusted numeric oracle/main、完整 Worker 网络与发布质量门、PowerPoint 人工验收均未完成，不能据此声称 PPT 生成闭环可用。R8/V2 原 actor needs_input 与控制器独立 review 的区分保留，未提升为 accepted base，旧镜像/测试与原件不变。开发测试授权不代表目标完成、生产部署或最终样稿验收；本轮仅整理两份本地 README，不部署、不改源码、不执行模型或测试任务。
