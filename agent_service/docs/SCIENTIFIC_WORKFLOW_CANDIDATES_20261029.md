# 科研项目工作台：四阶段真实候选验证

2026-10-29，新科研 plan/retrieve/outline/draft 已在实际 Kubernetes 节点上依次执行，
使用用户的 `zmhhaha/pi-cage` 和受限 Responses 模型代理。四阶段均完成停写、成果
独立校验、隔离业务数据库保存与读回。没有将旧 generate/revise 的 acceptance
复制给新合同。本页记录候选验证，不表示正式 Jobs HTTP 或业务发布已完成。

## 固定来源

- 运行层源提交：`78e80ac32a7e8c9889bc5d3b556b94048fdb3b28`。
- Pi Worker：`arm-cluster-master:5000/agent-service-pi@sha256:4eaf6752a3e9d87f8d521cbb5cddb39056de194d929f828c6710714390a40731`。
- 新构建控制镜像：`arm-cluster-master:5000/agent-service-control@sha256:c55db993ecd62d97bac8c3282bbcf902843b327da8a178bf5c98f8c1e4a66f74`。
- pi-cage 依赖：`arm-cluster-master:5000/pi-cage-agent-runtime@sha256:8c6c175ae53f0f1f6ebface6855079d7549138ef33de0320b5942dc38f6e6ced`，
  仓库 revision 仍为 `e0262e65e43c6924164e995394f715dc5d19c210`。
- PPT/Hermes 依赖：`arm-cluster-master:5000/ppt-hermes-worker@sha256:e77a8cde745afd782d43549681acdc88ed491f0f7a69f0327233c0c401ad60df`。

部署根目录为 `/root/agent-service-deploy-20261026`。新目录位于其中的
`catalog-workflow-78e80ac-r1/generated/`，所有路由和池均保持关闭。构建使用正常
`agent_service/build.sh --target control|pi`，完整构建日志分别是
`scientific-workflow-control-build-fixed.log`、`scientific-workflow-pi-build.log`。

## 实际执行

| 合同 / 候选 | Job | Attempt | 模型账本 | 永久消费 UID |
| --- | --- | --- | --- | --- |
| plan / candidate-wf-plan-r1 | `3cfe1310-2d2a-4ccb-a8cf-068c93137115` | `237b2717-0bc0-4fe6-99fd-5ccd18b4ac96` | 4 | 42000087–42000089 |
| retrieve / candidate-wf-retrieve-r1 | `e3ca8adc-3e1b-4cb9-8f00-1fd369c17574` | `ac4db93a-7cae-4711-bf0a-dbf9b7bbd6e0` | 5 | 42000090–42000092 |
| outline / candidate-wf-outline-r1 | `458f5418-9cd5-4636-ad9a-d370dee203f0` | `9e444568-1575-4365-9b21-3328aac70ebc` | 4 | 42000093–42000095 |
| draft / candidate-wf-draft-r1 | `0a202e47-d272-4259-8174-9ad9bca03486` | `80a818fd-c6cd-4ec0-afe8-5acbc37126ed` | 4 | 42000096–42000098 |

17 条模型请求均为 complete/200。每个 Worker 都实际 exit 0、restart 0，宿主检查
pi-cage 包、允许工具、只读挂载及隔离；停写后收集文件，任务租约过期后 grant 被拒。
候选 Pod、代理和临时 Secrets 已由操作脚本在保存证据后删除。最终实查
`agent-workers` 中没有 Pod。UID 台账保留全部范围，后续候选从 42000099 起，不能复用。

每个候选证据保存在 `evidence/<候选名>/`，包含原任务、停止回执、实际 Pod、结果和
完整模型/学术调用账本。每阶段独立校验摘要保存在
`scientific-workflow-candidate-canary-r1/<stage>/verified-summary.json`；最终总摘要为
该目录下的 `summary.json`。

## 业务与修订范围

独立 SQLite 位于 `scientific-workflow-candidate-canary-r1/business.sqlite3`。自动化
运维身份通过真实 `WorkspaceStore` 的创建、确认方案、确认证据接口推进项目；这些
是明确标记的测试决策，未冒充实际用户确认，也未使用生产业务库。方案确认之后才执行
一次 Crossref 检索（每次 limit 1，最多一次搜索）；检索结果与受保护观测进入下一阶段
冻结快照，写作没有重新检索。大纲后按正常确认门进入 Draft。

各阶段使用捕获的真实停写成果，经任务/pins、摘要、来源和工具范围复验后调用正常
业务入库逻辑，数据库读回一致。没有伪造远端 delivery ack，也没有声称走过 Jobs HTTP。

Draft 报告 SHA256 为
`226c0ced98682148759e122a55edd61ac3f2e6228fde954a598271c1007b7948`。
随后以该实际报告执行原有 `validate_revision_input` 和 `compose_revision`，证实固定
papers/evidence/references 及选区外字节保持不变；候选报告 SHA256 为
`266674a5afea53dbfd851b969e617886d6982d0a593b4c075c0260a3f2f7eac2`。
这一步是离线兼容校验，未调用第五次模型修订、未采用候选。结果保存在
`revision-compatibility.json`。

此后仍须独立生成四个精确新组合的验收记录，保留既有已接受 generate/revise 路由与
所有历史绑定、池和宿主，完成正式 Jobs HTTP 与科研业务 HTTP 的五阶段验收，再切换
用户入口。科学内容的结构与来源校验不证明研究结论正确。
