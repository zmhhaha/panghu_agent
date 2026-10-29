# 通用 Agent 服务实现交接

日期：2026-10-26。分支：`codex/agent-execution-service`，仓库：`panghu_agent`。

本批次遵照“把功能写好，测试等用户回来再做”的指示，只进行了源码实现和静态阅读。
没有运行测试、编译检查、模型调用、镜像构建、推送或部署。所有新组合默认未验收、关闭。
之前 REGISTRY/CAPSULE 验证报告只适用于之前的提交，不覆盖下表新增代码。

## 实现范围

| 层 | 本批代码 |
| --- | --- |
| 合同与绑定 | 文本、PPT 五阶段、科研生成/修订的实际 task/output schema、校验、提示词、collector；独立能力包/工具环境/adapter/model policy 组合，冻结完整 pins |
| 控制接口 | 持久单槽队列、attempt/fence/租约、幂等提交和只读恢复、绝对 deadline、事件、取消、停止证明、隔离恢复 |
| 文件数据面 | 按合同限制的普通目录/二进制文件、测量成果清单、所有权/引用保留/主动删除、版本化文件 checkpoint、恢复 CAS、交付 ack |
| Pi 宿主 | 固定 0.73.1 CLI、空 HOME、固定资源、显式工具/扩展、只读 inputs/base、真实进程停止与候选收集 |
| Hermes 宿主 | 每 attempt 私有 loopback gateway、独立 HOME、固定工具策略、持久 admission CAS、相同中立文件合同和停止门 |
| 模型与学术工具 | 短期 attempt/request/binding/deadline grant、固定 Responses 代理、持久调用 admission/完成响应重放、受限学术搜索/核验与控制器观测 |
| PPT 业务 | 五阶段 outbox、真实两轮确认、权威 published 修订基线、文件 checkpoint、独立 K8s validator、审核稿和旧 API 映射 |
| 科研业务 | 生成/固定证据修订 outbox、真实工具观测复验、报告/引用/版本独立校验、原发布事务和 pending/ack 恢复 |
| 构建与部署 | control/pi/hermes 构建入口、固定 donor digest、内部服务/独立 worker namespace、DB 初始化、RBAC/NetworkPolicy/ESO、受限 AppArmor 示例 |

业务只指定合同，不传 Agent 名称、原生会话、模型 URL 或供应商凭据。
服务端 `routes` 选择经验收的 `execution_bindings`；切换只影响新 Job。
旧 Job、旧幂等键和恢复继续匹配原 pins，旧 pool/ConfigMap/资源记录在排空前需要保留。

## 合同及输出

| 合同 | 工具与输出 |
| --- | --- |
| `workspace-task.v1` | 仅 read/write，有界文本 |
| `ppt.plan_initial.v1` | 初始方案；实际资料快照，无预先确认 |
| `ppt.plan_options.v1` | Stage1 真实确认后产生完整设计方向 |
| `ppt.prepare.v1` | Stage2 真实确认后准备正式规格与项目 |
| `ppt.generate.v1` | 从已准备项目生成可编辑成果；要求 vision、官方预览和逐页检查 |
| `ppt.revise.v1` | 从业务已发布且允许修订的指定版本修改；相同预览/验收边界 |
| `research.generate.v1` | read/write + 受控 academic_search/academic_verify；报告、证据、claims、references 和受保护工具观测 |
| `research.revise.v1` | 仅 read/write；固定证据和指定章节，保护范围外字节，不增加检索权限 |

PPT 的项目文件保存在 `project/<真实项目根>/...`，结果描述位于 `result/`。
资料由 `inputs/` 只读挂载，检查点由 `base/` 只读挂载；不恢复私人 HOME 或原生 session。
允许的 PPT 检查点阶段边为 initial→options→prepare→generate→revise，以及 revise→revise。
跨引擎只重放兼容文件，要求包身份和文件合同一致，目标组合仍需独立验收。

科研实际使用 `local-procedures-v2`。K-Dense 候选已审阅，但未选入、未安装。
生成必须至少完成一次真实受限搜索；真实零结果可以形成明确披露证据不足的报告。
模型账本与论文元数据必须匹配代理持久记录的观测，业务下载后再次验证。
观测证明桥接返回内容的来源，不证明科研结论正确。

## 生命周期与容量

每次实际阶段执行一个 Pod，服务全局默认并发 1，其余排队。完成后保留候选并立即
进入停止/清理；两轮确认和阅读结果不占 Pod。以后提出修改时创建新的 Job/Pod。

每个 fence 消耗一个持久预留 UID 对：Agent 与 preview/helper，各自独立 UID，永不
复用。只有 PPT generate/revise 会启动官方 preview，但所有 attempt 均保留 UID 对。
UID 范围必须由平台实际预留；禁止重置 fence、删除分配历史来复用号码。
停止证明覆盖两组 writer、detached 后代、原 Pod 的全部容器；30 秒退出宽限不是停止证明。

只有全部 writer 停止后才读取成果；collector 仍可访问只读输入。收集后确认卸载成功，
再冻结/签名。停止或卸载不确定时 quarantine，保留来源与执行槽，禁止替代执行。
`completed` 表示候选交付；`accepted/published` 继续属于业务。

PPT 成果硬上限为 2048 文件、总 256MiB、单文件 64MiB；资料单文件 16MiB。
输入与检查点总量也有独立硬上限。当前有界 inline 传输会产生 base64/SQLite 开销，
worker 控制盘预算 2Gi、工作盘 1Gi、内存上限 2Gi、临时存储上限 4Gi。
这些是配置上限，实际大文件容量、峰值内存和吞吐尚未测量。

## 模型与公共服务配置

业务调用 token、worker 控制 token、receipt key 和模型 grant 互相分离。
Agent 只取得短期 grant；llm-service 调用 token/供应商 key、数据库/Vault/发布凭据
不进入 Agent。代理只转发固定 `/v1/responses`，禁止远端附件 URL、provider file ID、
item reference 和任意 hosted tool；文本及内联图片按协议与大小限制处理。

模型 HTTP 调用先持久 admission，再访问固定上游。丢失响应不会触发自动再付费；
完整响应可从 DB 重放，仍须有效任务授权。SSE 当前先有界收集、持久保存，再交给客户端，
没有向业务承诺逐 token 的实时模型流或 exactly-once 外部计费。

公共配置的权威位置：

- Vault：平台 `vault/inventory/agent-service-externalsecret.yaml`；独立仓库有同源安装模板。
- 模型：平台 llm-service 的路由/调用方权限；Agent 服务只配置专用 caller 和稳定模型名。
- OAuth、Cloudflare：现有 PPT/科研入口的公共服务配置。本内部 Agent API 无新增公网域名/登录应用。
- Agent 自有资源：`agent_service/deploy/`；PPT 接线：`ppt_service/deploy/agent/`。

调用 token 的权威键为 `secret/agent-service/runtime` 中的 `PPT_SERVICE_TOKEN`、
`SCIENTIFIC_SERVICE_TOKEN`；上游 caller 来自 `secret/llm-service/callers` 的
`LLM_TOKEN_AGENT_EXECUTION`。秘密值只进 Vault，不提交 JSON 密钥文件。

## 部署与业务切换入口

构建入口为 `bash agent_service/build.sh --target control|pi|hermes`，按服务既有方式在
服务器构建、推送私人 registry，并仅记录成功核验的不可变 digest。Pi/Hermes 镜像使用
已固定 PPT donor 的工具环境；Pi 另外使用固定 Pi donor。没有运行时下载/更新能力包。

`agent_service/catalog.py` 提供离线公开目录/各绑定 HostConfig 生成器，所有路由关闭。
`bash agent_service/deploy.sh` 负责正规 namespace、ESO、DB 初始化与控制面部署；
实际参数、Vault 写入与节点 AppArmor 配置见服务部署文档。

PPT：显式设置 `PPT_EXECUTION_BACKEND=agent-service`，按
[`ppt_service/deploy/agent/README.md`](../../ppt_service/deploy/agent/README.md) 配置。
科研：显式设置 `SCIENTIFIC_EXECUTION_BACKEND=agent-service`，按
[`scientific_agent/AGENT_SERVICE.md`](../../scientific_agent/AGENT_SERVICE.md) 配置。
默认后端没有切换，现有运行任务由原入口排空，新连接器不会静默接管原生会话。

## 用户回来后的验证顺序

本批收尾只读核对了目录生成器与控制配置、HostConfig、部署渲染器的字段和路径，
并对照本机 `D:/github/hermes-agent` 的提交
`235645b19885aadab97719c51468c780dab867e3` 阅读了 console entry、Gateway 参数、
插件 hook、工具注册与 Responses 配置接口，未发现明确接口错配。未选定的最终 donor
digest 不保证与此本机源码一致；这些静态核对不替代下列执行验收。

以下均为待执行项，本批没有执行，也没有记录为通过：

1. 配置/目录生成、空秘密检查、DB 迁移、幂等与所有权、双 UID 分配/耗尽、引用删除。
2. 固定候选镜像启动；实际节点的 AppArmor/mount/seccomp、CNI、身份降权与取消/崩溃。
3. Pi/Hermes 分别验证 Responses、多轮工具、图片、学术桥、观测、停止与结果完整性。
4. PPT 全五阶段、两轮确认不可绕过、跨阶段/跨引擎文件恢复、正式规格和独立 validator。
5. 科研生成、真实零结果、固定证据修订、精确章节隔离与报告事务/pending/ack 恢复。
6. 按精确合同/包/工具环境/adapter/model 组合保存验收证据，逐业务开新路由并验证排空回滚。

OpenSpec change 保留原未验收任务的未完成状态；代码实现记录与验收状态分别维护。
DSH、原生 resume/fork/steer、任意插件上传不在本次实现范围。
