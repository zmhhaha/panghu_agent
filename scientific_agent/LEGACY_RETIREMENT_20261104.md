# 旧科研服务移除与历史规格对账

日期：2026-11-04，Asia/Shanghai。

用户要求移除不再使用的旧服务。现役科研入口使用 React 网关、科研 API 和共享
agent-service，不再使用旧 Gradio 或科研专用常驻 Pi Worker 接收新任务。
本次 OpenSpec 变更为 `retire-scientific-dedicated-pi-worker`。

## 历史四项验收的处置

| 原变更 / 任务 | 当时事实 | 当前处置 |
| --- | --- | --- |
| `add-scientific-pi-workbench` / D2b | 原 Gradio HTTP 已验收；完整 DOM、点击、自动 Timer 与登录链路未全部验收。后续只读浏览器覆盖引用、章节和下载，不能代签完整旧任务。 | 旧公开 UI 已由新工作台替代，原任务移入历史记录，不再作为当前开发待办，也不改记“原验收通过”。 |
| `add-scientific-pi-worker` / F4 | 同节点 IPv4 允许/阻断与文件隔离有证据；IPv6 无可路由基线，metadata 无有效基线服务，人用 Pi 被目标自身策略阻断，三者仍不能归因。 | 随旧执行服务移除结束该矩阵的当前适用性；历史未知原样保留，新架构的文件/进程隔离不冒充旧网络矩阵验收。 |
| `add-scientific-pi-worker` / E7 | 无邮件 Pi canary、CrewAI 兼容有证据；没有保存所有无关工作负载的完整前后资源快照。 | 原默认引擎切换计划已被后续正式 Agent 接入取代；缺失的历史快照不能事后补造。 |
| `add-scientific-pi-worker` / I1.2 | 原最终交付门依赖 F4/E7，当时未通过。 | 原交付门被后续 Agent 接入与本次旧服务移除取代，不写成旧门通过。 |

这些任务从旧清单的当前 checkbox 中移出，并保留原任务文字、原未完成结论和替代关系。
因此旧清单没有当前剩余任务，不表示原 14/47 项全部按原标准验收通过。

## 后继证据及边界

- [旧 Worker 网络实测](../docs/scientific-pi-network-acceptance.md)：记录可判定检查和无法归因的检查。
- [节点隔离实测](../agent_service/docs/NODE_ISOLATION_VALIDATION_20261027.md)：实际只读挂载、降权、控制目录拒读、停止 writer 和非懒卸载；不证明全部网络目标。
- [2026-10-29 科研正式接入](../agent_service/docs/SCIENTIFIC_CUTOVER_20261029.md)：正式 Jobs HTTP、生产生成/修订、取消恢复、发布与双 ACK，保留原失败及当时切换快照。
- [新工作台浏览器链路](../agent_service/docs/SCIENTIFIC_WORKFLOW_CANDIDATES_20261029.md)：真实五阶段业务及独立账本审计、刷新/离开后恢复和 375px 布局；不声称退出认证后重新登录。
- [2026-11-04 反馈发布](FEEDBACK_20261104.md)：后续生产 HTTP、真实登录页面只读验收及发布回退，保留精确范围。

共享 Agent 当前版本的完整真实故障/排空/回滚演练，仍属于
`add-pi-first-agent-execution-service` 的 `5.5R`，不随旧服务删除而自动完成。
最新版本正式网页新建项目完整五阶段和桌面 Word/LibreOffice 排版，仍按反馈交付记录
独立验收。本次不发起模型调用、不修改用户方案、正文、引用映射或采用记录。

## 删除不再使用的旧任务数据

删除前只读核对发现 14 个旧任务均已结束：10 个成功、3 个失败、1 个取消。
用户明确确认这 14 个任务的数据和附件不再需要，不要求兼容读取或恢复。

清理以显式的 14 个 task UUID 为边界，核对任务、报告、版本、修订、采用和快照依赖。
`scientific_pi_index` 等共享业务表仍被现役 Agent 任务使用，不能按 `engine=pi` 清空或
直接 drop。删除前确认无现役任务或当前项目依赖待删记录，按关联顺序精确删除，保留
当前任务与项目的内容摘要以验证其未变。

本轮曾临时建立静态档案；按用户最新决定，档案模块、兼容读取、两张专用归档表和
本次原始 tar 备份一并撤除。最终不保留用于这批旧任务的运行服务、数据卷、附件副本
或恢复入口。旧任务删除后返回 404，不伪造为新流程成果。现役共享业务表、Agent
任务及当前工作台项目保留。

## 现役部署约束

`SCIENTIFIC_EXECUTION_BACKEND` 默认且仅接受 `agent-service`。API 补丁移除旧
`SCIENTIFIC_PI_WORKER_URL/TOKEN` 以及旧 runtime/bundle 变量；正常部署不再依赖旧 Secret。
现役入口仍是 `bash scientific_agent/build.sh --target api` 和
`bash scientific_agent/deploy.sh`。

`scripts/deploy-scientific-pi.sh`、旧 renderer 的独立 Worker CLI 和通用
`deploy-api.sh` / `deploy-ui.sh` 的科研路径均明确拒绝执行，指向现役部署入口，防止
误恢复旧 Worker 或 Gradio。科研与 Agent 仍共用的合同、工具、投影模块继续保留。

清理按“核对旧数据关联和现役保留范围 → 构建并发布无兼容 API → 删除旧数据与专用资源”
执行。共享 Agent、LLM、认证、数据库及其他业务不作为旧服务删除。

## 执行结果

应用源码 `565a01bf5ac43deda3082da0821f70f1393918ed` 已推送至
`codex/scientific-feedback-20261104`，服务器独立检出拉取并原生 ARM64 构建，使用
`latest` 构建标签和以下精确镜像部署：

`arm-cluster-master:5000/scientific-api@sha256:5132000a1af729b0cd976df7be2c3e50f8aa08814f667ece551b2c228d0c4056`

精确镜像在禁网、只读、无 capabilities 的非 root 容器内导入真实 API，通过检查：
只使用 Agent 服务、没有旧 Worker fallback 属性，也找不到静态归档模块。
相关 API、实际 COPY/构建、部署和脚本回归为 47 passed、46 subtests passed；不与
已撤除的归档方案测试累加。

共享 SQLite 使用一个 `BEGIN IMMEDIATE` 事务精确删除：14 条旧 Pi 索引、10 份报告、
1 条通知、2 个版本、1 份证据快照、4 个修订意图、2 条修订绑定、1 条采用记录。
旧任务在 `scientific_tasks` 中没有对应行。删除两张本轮临时归档表，并逐表比对所有
非目标行摘要；现役 3 个 Agent 任务和 10 个工作台项目均保留，无跨代依赖。

已删除旧 `scientific-pi-worker` 的 Deployment、Service、NetworkPolicy、ExternalSecret、
Secret、PVC 及对应 PV；同时删除未使用的旧直连 `llm-token` ExternalSecret/Secret、
13 个废弃 API/Gradio ReplicaSet 和 9 个无引用源代码 ConfigMap。本次原始 tar 备份
也已删除。现役科研命名空间仅保留 API/UI、对应 Service、Agent 调用凭据和两条入口
网络策略等有效资源；共享 Agent Worker/成果存储和 UID 账本不在删除范围。

规范 Vault 专用清单 `vault/inventory/scientific-pi-worker-externalsecret.yaml` 已从
外层仓库删除，提交 `9e2e5ad`，保留并发远端更新后以 `0250c1b` 推送 main。
共享 Vault callers 记录及其他业务令牌没有删除；科研专用旧令牌的运行 Secret 已清理，
默认令牌同步也不再包含 `scientific-agent`。

服务器证据目录：`192.168.137.101:/root/scientific-legacy-retirement-20261104/`。
其中 `old-data-deletion-receipt.json` 记录单一事务及保留摘要，
`resource-deletion-receipt.json` 记录资源移除清单，`final-image.json` 记录镜像身份。
仅保留脱敏操作证据和源码，不保留这批旧任务的内容、附件或兼容备份。

删除后的真实 HTTP 验收通过：14 个旧任务及下载均返回 404；现役 3 个任务可读、
2 份现役报告下载字节与数据库一致；10 个当前项目均可读，其他身份访问返回 404。
后端为 `agent-service`，旧 fallback/归档模块与归档表均不存在，API/UI 均 1/1 Ready，
旧服务资源不存在。结果为 `final-status.json` 和 `final-http-smoke.stdout`。
