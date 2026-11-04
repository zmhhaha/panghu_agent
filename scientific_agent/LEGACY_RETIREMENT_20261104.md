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

## 有用历史迁入静态档案

删除前只读核对发现 14 个旧任务均已结束：10 个成功、3 个失败、1 个取消。
10 个成功任务的公开附件仍可读取；不能通过删除服务把这些有效历史变成 404。

静态档案使用共享业务数据库的新表 `scientific_retired_pi_tasks` 和
`scientific_retired_pi_artifacts`。导入仅接受有旧归属索引、真实终态的任务，逐项验证
任务/attempt、结果摘要、manifest、附件大小与 SHA256；所有数据写入并读回后才提交
可见标记，幂等重放不覆盖冲突数据。原失败/取消不改成成功；源附件若已过期仍返回
明确过期。API 继续先验证所有者，再读静态档案，不需要旧 Worker 或其 PVC。

历史服务的执行和发布修改接口返回明确退役错误。静态档案不接新任务、不调用模型，
不保存私人 HOME、运行环境或密钥。原业务表、报告正文和版本记录不删除。

## 现役部署约束

`SCIENTIFIC_EXECUTION_BACKEND` 默认且仅接受 `agent-service`。API 补丁移除旧
`SCIENTIFIC_PI_WORKER_URL/TOKEN` 以及旧 runtime/bundle 变量；正常部署不再依赖旧 Secret。
现役入口仍是 `bash scientific_agent/build.sh --target api` 和
`bash scientific_agent/deploy.sh`。

`scripts/deploy-scientific-pi.sh`、旧 renderer 的独立 Worker CLI 和通用
`deploy-api.sh` / `deploy-ui.sh` 的科研路径均明确拒绝执行，指向现役部署入口，防止
误恢复旧 Worker 或 Gradio。科研与 Agent 仍共用的合同、工具、投影模块继续保留。

清理按“完整归档和读回 → 构建并发布 API → 验证新旧读取与无旧引用 → 删除专用资源”
执行。共享 Agent、LLM、认证、数据库及其他业务不作为旧服务删除。

## 执行记录

归档、精确 API 镜像、线上移除清单与验收结果将在实际操作完成后记录；本段尚不声称
旧 Worker/PVC 已删除。
