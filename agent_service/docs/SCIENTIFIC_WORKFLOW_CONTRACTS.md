# 科研工作台分阶段执行合同

2026-10-29：代码已提供下列四个独立合同，尚未获得新合同/镜像/引擎组合的生产验收。
目录生成器为所有新路由和池输出 `enabled: false`。已有科研生成/修订的验收不能
作为这四个合同的验收记录。

| Profile | 实际工作 | 工具 | 交付物 |
| --- | --- | --- | --- |
| `research.plan.v1` | 根据结构化需求、已提供材料和反馈提出可编辑方案 | read/write | 问题、查询、纳排标准、范围、限制 |
| `research.retrieve.v1` | 根据已确认方案执行有界学术检索 | read/write/academic_search/academic_verify | 实际检索记录、原始文献元数据、受保护观测 |
| `research.outline.v1` | 对固定文献选择整理证据和大纲 | read/write | 证据记录、章节与证据关联、缺口 |
| `research.draft.v1` | 按已确认证据和大纲写作 | read/write | 报告、claims、references、固定文献和证据 |

每次任务只挂载一份只读 `inputs/context.json`。任务字段为 `schema_version`、
`business_task_id`、`project_id`、`stage`、`context_sha256`、`created_at`、
`execution_deadline_at`、`source_policy`。来源策略固定 providers、每次最大结果数
`max_results` 和本次最大搜索调用数 `max_search_calls`；后者最多 20。代理在数据库
admission 时原子计数，丢失响应的调用也消耗预算；已完成调用的相同请求只读回放。
总时长最多 3600 秒，沿用原来的模型调用上限与全局单槽。

所有上下文包含结构化 `requirements`、材料快照和项目 ID，可另带本阶段反馈。
检索上下文额外包含方案及绑定其完整摘要的确认记录。整理与写作上下文包含不可变
`evidence_snapshot`，其中保留原始 SearchBatch 的项目、attempt、请求摘要、来源策略、
完整观测和逐来源选择。写作还须提供绑定快照的大纲及绑定两者摘要的人工确认。
服务在启动引擎前检查上下文，停止所有写入者后再次验证相同字节。

Agent 只写 `result/output.json`。可信 collector 校验后输出独立的
`stage-result.json`，绑定业务任务、项目、上下文摘要、结果摘要和可信执行 provenance。
检索 collector 只接受宿主在停写后保存的 root/0400 观测，不接受 Agent 自报的账本。
业务再次核对权威成果 manifest 与这些合同，才能保存阶段成果。

跨阶段校验链为：原检索 attempt 的受保护观测 → 原始检索记录和文献 → 项目内的
文献选择与材料快照 → EvidenceSnapshot → 已确认 Outline → 本次 Draft。写作和
大纲阶段没有学术或 shell 工具。不能让写作伪造“本次已检索”的观测，也不能把
抽象后的摘要冒充已获得全文。用户材料保留自己的实际记录时间、提取文字和源文件
摘要；没有伪造公开 URL 或标识符核验。文献 ID 和每一级实际访问范围均需匹配。

Draft 附带 `report.md`、`papers.jsonl`、`evidence.jsonl`、`claims.jsonl`、
`references.json`。这些字节通过现有引用链、claim 段落与选区修订输入检查后，才能
交给已有 `research.revise.v1`。未选择、未观测来源不能进入引用；报告须保留访问
限制，零证据时只能明确报告证据不足。校验建立结构和来源关系，不证明科研结论。

旧 `research.generate.v1` 仍要求同一次 attempt 的真实检索；其历史读取和验收
规则保持原样。`research.revise.v1` 仍只使用固定材料，严格保留选区外正文。
新工作台不会把旧的一次性生成换名来模拟分阶段执行。

共享纯合同与业务复验入口位于 `scientific_agent/workflow_contracts.py`，可信运行层
在 `agent_service/profiles/research_workflow.py`，两者无业务数据库依赖。精确组合
候选使用已有 `agent_service.deploy.acceptance_probe` 与独立业务导出；每个新合同
都要自己的候选、停写证明、正式 Jobs 交接和业务接收验收。
