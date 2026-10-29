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

## r2：缓冲响应等待修复与五个新候选

上述 r1 记录保留。之后正式 HTTP 检索的 attempt
`fbd67e0c-dcef-4b9f-b5d6-53c566cd4943` 在约 123 秒无原生事件时触发
`runtime_idle_timeout`：此前四条模型请求及三条学术观测已经完成，最后一条模型请求
已入账但未收到结果。该失败与不确定请求均保留，没有覆盖记录、重放付费请求或复用
原 acceptance。

根因是任务代理收完 Responses 流后才将结果交给 Pi，因此代理收到上游片段期间，
原生 stdout 仍可能持续沉默。HTTPX 的 600 秒配置是单次 I/O 等待上限，持续收到片段的
完整流可以更久，不能将它当成整个响应的 600 秒墙钟上限。

提交 `62afd8f445cb7dabbbeef9401bf5acfb7bc6fad4` 将 I/O 上限与 30 秒返回容差放入
`agent_service/model_timeouts.py`，仅任务代理使用方的生产 Pi factory 与候选 factory
将 idle 设为 `max(合同最大任务时长, 600 + 30)`。科研合同允许 1800 秒时，idle 也是
1800 秒；短合同容差为 630 秒。每次执行仍先检查取消与其原有绝对 deadline，任务和
模型 grant 没有延期；直接使用原生 runtime 的默认 idle 仍为 120 秒。

本机定向验证为 245 passed、6 项 Windows 平台跳过、18 subtests passed，包含全部候选
factory、生产 factory、模型代理、精确 deadline，以及真实静默子进程启动后的 deadline
和取消测试。这是定向验证，不表示其他领域的整套测试通过。

通过原有 build.sh 在服务器构建、推送的新镜像为：

- control：`arm-cluster-master:5000/agent-service-control@sha256:66b0bd82361ccddec81caa40ba2c2cc2542ac9b0be977211ec1b6fdcda001d10`。
- Pi：`arm-cluster-master:5000/agent-service-pi@sha256:fb0a543841c2acd38ce22835fd15710b88c5e66b3a3dcd5bb3d442d595b492e8`。

pi-cage 与 PPT/Hermes 依赖仍使用本页记录的固定 digest。构建日志为部署根目录下的
`control-build-62afd8f-r2.log` 与 `pi-build-62afd8f-r2.log`。新候选目录为
`catalog-workflow-62afd8f-r2/generated`；生成时所有路由和池均关闭，未创建 acceptance。

| 合同 / 新候选 | Job | Attempt | 模型账本 | 永久消费 UID |
| --- | --- | --- | --- | --- |
| plan / candidate-wf-plan-r2 | `989c59f2-7283-4b32-b03f-1782facca998` | `95c6959b-c279-4d1a-8aed-57dc2cdb7307` | 4 | 42000099–42000101 |
| retrieve / candidate-wf-retrieve-r2 | `4f97ab15-54bc-4ed4-a838-ff53e12cf038` | `7fca6509-1c19-4d5e-978d-25d084ce2cd2` | 5 | 42000102–42000104 |
| outline / candidate-wf-outline-r2 | `0c4b4cfb-b9c4-4612-8a88-fd4a3f9a5e1f` | `18aa2d55-7419-4d22-bcc5-efa13018ca2d` | 4 | 42000105–42000107 |
| draft / candidate-wf-draft-r2 | `8bec481b-02a4-40c6-a4a0-fb449cc5bc82` | `505cc2bb-1df8-47bc-8f2d-ec2ffde0e9ca` | 4 | 42000108–42000110 |
| revise / candidate-wf-revise-r2 | `8f358f16-f3d1-4487-a490-46f8be23d29e` | `cb6322f2-a0bc-496b-9d00-0303d8efc0d1` | 5 | 42000111–42000113 |

五次独立原生执行合计 22 条模型请求，均 complete/200；Worker 实际 exit 0、restart 0，
均确认停写、固定镜像、受限工具、隔离与过期 grant 拒绝。每次保存独立证据后删除临时
Pod、代理与 Secrets。最终只读检查 `agent-workers` 中无任何 Pod；全部 UID 范围无重叠，
候选范围按预留顺序递增。检查时下一候选 UID 为 42000114，后续仍须动态读取永久台账。

四阶段继续使用隔离业务库和自动化运维测试身份，Crossref 每次 limit 1、最多一次检索。
新证据位于 `evidence/candidate-wf-<stage>-r2/`，业务数据库、每阶段保存读回与总摘要位于
`scientific-workflow-candidate-canary-r2/`。该受限检索不覆盖正式默认三个 provider、
每次最多十条结果、最多三次搜索的广度。

新 Draft 报告 SHA256 为
`4da48b32824a399754325560de01ffba7df81577c1f5dfd5932fad157a6b7d85`。
以它运行固定证据离线 compose 后候选 SHA256 为
`c3d512f1f910311bf220992f2a58fe1a5ae9e56505d83aa43853cccdca6f8eff`，范围外字节与固定
papers/evidence/references 均未变化；这项仍仅是离线兼容验证。

第五个真实模型修订候选独立使用此前已完成的真实生成
`evidence/candidate-pi-research-r4/` 和 `research-business-r4/verified-generation/`。
它未重新运行 generate，也未将原始生成记录或 r2 Draft 冒充其父报告。新版本、固定
证据快照、意图和任务写在全新的
`research-business-r4/revision-candidate-workflow-r2/`。使用新目录的禁用 revise host
`worker-configs/research-revise-pi-18dab4f9e8f651faa243/config.json` 后，独立验证器复核
实际运行镜像内的场景代码、模型落盘、完整账本、停写、固定 trace 和选区外原文字节，
再在 `verified-revision/publication.sqlite3` 发布并比较候选。结果未采用新版本，原始
准备库与父报告数据未变；没有伪造远端回执。

修订报告 SHA256 为
`afbfdedbd89b4475dba72f42edb9ed8cc79050863566e609c30f1d16fe29ecd6`；独立摘要为
该目录下 `verified-revision/summary.json`。五个候选验证本身不改生产配置。正式 Jobs
HTTP、默认检索策略、从新 Draft 发起修订及远端 delivery ack 仍须独立验收，不能由本页
的隔离数据库发布结果替代。
