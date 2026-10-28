# pi-cage 科研生成与固定证据修订验证（2026-10-28）

本轮按用户要求优先跑科研，使用用户 `zmhhaha/pi-cage` 的一次性 Worker 入口。
真实科研生成 r4 已成功，报告、检索观测、引用关系及隔离数据库发布均已独立复核。
真实修订 r1 的校验误判已修复，已有真实输出离线复核通过。修订 r2 因模型把选区外
断言提交到修订 claims 而被校验器拒绝；提示词明确该边界后，新镜像的修订 r3
已真实执行完成，并通过独立产物复核、隔离业务发布和候选版本比较。
原 r1/r2 失败记录保持不变。

本记录不代表科研正式入口已经切换。生产 route/pool 保持关闭，未登记 production
acceptance，正式 Jobs HTTP 提交、最终交付和远端 delivery ack 尚未完成验收。
PPT 全流程和 Hermes 不作为本轮科研候选试跑的前置条件。

## 执行来源与模型路径

- 业务仓库：`zmhhaha/panghu_agent`，分支 `codex/agent-execution-service`。
- pi-cage：`zmhhaha/pi-cage`，分支 `codex/agent-service-worker`，固定提交
  `e0262e65e43c6924164e995394f715dc5d19c210`。
- 原生入口：`/usr/local/lib/pi-cage/worker-cli.mjs`，固定包
  `@earendil-works/pi-coding-agent 0.99.2`，adapter 为 `pi-cage-adapter-v1`。
- Worker 配置使用 provider `task-model`、模型别名 `llm-trusted`、Responses API。
  模型请求经任务专用代理转到 llm-service；Worker 仅得到该任务的短期授权，
  不复制供应商 API Key。本记录不把别名推断成某个未核对的上游模型 ID。
- 科研生成只授权 `read,write,academic_search,academic_verify`；固定证据修订只授权
  `read,write`。任务串行执行，每次分配三个新 UID，失败后也不得复用。

生成 r4 和失败修订 r1 使用业务源码 `7379e4c0155275cf390d1b61b0fdd691f39dd8f7`：

| 镜像 | SHA256 |
| --- | --- |
| `arm-cluster-master:5000/pi-cage-agent-runtime`，固定原始 donor | `8c6c175ae53f0f1f6ebface6855079d7549138ef33de0320b5942dc38f6e6ced` |
| `arm-cluster-master:5000/agent-service-pi` | `cdc33e177452134819c9bb9ef8b4ecfefdba05a567d10d7abeb03d2b56f0869f` |
| `arm-cluster-master:5000/agent-service-control`，已构建 | `6f6cd80bf6da37e117ac73799a1ad1b39e7e1aa48b5a897cd216c4be9a0a70ac` |

上述配置来自服务器 `catalog-7379e4c-pi-cage-auth/`，runtime revision 为
`domain-runtime-20261028-auth`。构建通过既有 `agent_service/build.sh`，配置生成时
保留现有调用方、配额及运维字段；生成目录或构建镜像本身不表示已生产 rollout。

## 真实科研生成 r4

`candidate-pi-research-r4` 从隔离业务数据库通过真实 `ScientificAgentProjection.prepare`
准备任务，主题为 reproducible software builds 的简短综述。Crossref 最多返回一条记录，
报告须明确证据限制，不声称完成系统综述或获取全文。

| 观测 | 结果 |
| --- | --- |
| 业务 task ID | `3e555e4c-5695-57d5-b474-4e116334a4c4` |
| Pod UID | `141bbde9-e556-475a-a1e5-375bd519d7a1` |
| 实际运行时间，UTC | `2026-10-28 07:08:28`–`07:12:44` |
| 最终执行状态 | `completed`，exit 0，restart 0 |
| 模型账本 | 18 条 `complete/200`，包含一次前置回显 |
| 学术工具观测 | 一次真实搜索、一次标识符核验；一篇论文、一条引用 |
| 报告 | 4134 bytes，包含 Scope、Methods、Findings、Limitations、References |
| 报告 SHA256 | `83c6f5c6a890e262d960fd388620e19a0704812be8c2ec8d14e50c29cb2412dc` |
| 停止与清理 | 已观察全部 writer 停止，过期 grant 返回 401，临时资源已清理 |

独立验证重新核对真实候选来源、固定 pins、产物集合及 SHA、检索工具观测、报告与引用。
随后在原准备数据库的副本中调用真实 `ScientificAgentProjection.publish()`，读回报告并
记录本地 publication ack。原数据库不变，远端 `delivery_ack` 仍为 NULL。
这证明已验证产物可经过业务发布逻辑，不证明候选曾经过正式 Jobs HTTP 交付链。

证据根目录为服务器 `/root/agent-service-deploy-20261026/`：

- `evidence/candidate-pi-research-r4/`：真实候选、停止证明和受限响应账本。
- `research-business-r4/`：原始准备数据库、业务记录和 scenario。
- `research-business-r4/verified-generation/`：独立验证、引用追踪、报告及发布数据库副本。
- 本机 ignored 报告：`agent_service/rendered/research-validation-20261028/generated-report.md`，
  已核对与服务器报告 SHA256 相同。

## 真实修订 r1 的失败及修复

首个修订从 r4 的真实报告和隔离发布副本建立版本、快照、修订 intent 和新 task，
只改 Findings 章节。修订固定 papers/evidence/references；未选中部分须逐字节保留，
不得用新增检索改变基线。

`candidate-pi-revise-r1` 的 Pod UID 为 `697fd01d-aa17-4d99-97ea-91324b8561e1`，
实际运行时间为 `2026-10-28 07:22:39`–`07:23:29 UTC`。四条模型账本均为
`complete/200`，捕获七次 read、一次 write；最终 collector 返回 `worker_output_invalid`，
exit 2、restart 0，交付产物为 0。过期 grant 返回 401，临时资源已清理。
证据保存在 `evidence/candidate-pi-revise-r1/`。HTTP 200 不能替代成果验收。

离线仅解析已保存 Responses 中的 write 参数，未执行任何保存的工具命令，未调用模型。
真实输出保留同一个 claim ID、原有 `supported` 评估及完全相同的证据集合；另一条历史
`insufficient` claim 恰好共享该集合。旧 `compose_revision()` 按全局不确定证据集合
拒绝 supported，因而误判为 `unsupported_assessment_upgrade`。

源码 `162ed53` 增加窄例外：仅允许同一 ID 的原 supported claim 保留相同证据集合，
证据顺序可以变化。改 ID、改证据集合、uncertain→supported、借用选区外 ID 仍拒绝。
同时将这一固定错误码加入脱敏诊断名单，不输出未知异常正文。
在实际 Worker 的 Python 3.13 禁网容器中挂载修复后的校验器，已有真实 r1 输出
重新 compose 通过，生成 4242 bytes 报告。该离线结果只解释并验证修复，
不改变 r1 的失败状态，也不替代新镜像的新候选。

科研修订相关六组测试为 **94 passed、126 subtests passed**，覆盖校验、合同、Worker、
API 执行与两组修订集成。诊断名单的独立定向检查为 **1 passed、2 subtests**。
这些独立运行不累加成一次完整回归。

## 真实修订 r2 的选区边界拒绝

`162ed53` 已按既有构建脚本生成新镜像，修订 r2 使用下列 Pi 镜像；该版本未作为
生产 rollout：

| 镜像 | SHA256 |
| --- | --- |
| `arm-cluster-master:5000/agent-service-pi` | `3393bc23302387229a9f8040e49ec80dbf51d9caee65e76e22f0c670a41634ae` |
| `arm-cluster-master:5000/agent-service-control` | `a5b5f590c517c44583315bcd18c97a85d009b81f84c6903d82c72bc318c8b157` |

`candidate-pi-revise-r2` 的 Pod UID 为 `37cb8eb9-4fc8-4d06-b11f-706419eb794c`，
实际运行时间为 `2026-10-28 08:17:20`–`08:18:36 UTC`，模型请求四次。
最终 exit 2、restart 0；过期 grant 返回 401，临时资源已清理。
证据保存在 `evidence/candidate-pi-revise-r2/`。该轮没有通过修订成果验收。

离线 compose 返回 `invalid_claim_markers`：模型将选区外 Limitations 章节的 C2
一起放入修订 claims。评估保留的窄修复未触发；这是修订选区合同被正确拒绝，
不能据此放宽校验器。`772bef1` 的提示词明确输出 claims 只属于选中章节，外部 claims 由
宿主自动保留。相应 `test_research_profiles.py` 与 `test_execution_diagnostics.py`
定向检查为 **22 passed、3 subtests**；后续真实执行结果另见 r3，二者不混为一次验证。

## 真实修订 r3 与独立业务验收通过

提示词修复源码为 `772bef1b115788e4321ee136d7fa764cf0372f6d`，按原构建入口得到：

| 镜像 | SHA256 |
| --- | --- |
| `arm-cluster-master:5000/agent-service-pi` | `bf9739abdd993c192e7bb86b87c3564182db0bd660b5a6beb6ef44c0f6bed401` |
| `arm-cluster-master:5000/agent-service-control` | `74f564bc307bae4734699d56b7d0a5450061b163d946666c82ec9873219ae024` |

新配置位于服务器 `catalog-772bef1-research/`，路由与执行池保持关闭。
该版本已通过正常 `deploy.sh` 部署及独立状态核对，详见下方部署记录。

| 观测 | 结果 |
| --- | --- |
| 候选 | `candidate-pi-revise-r3` |
| 业务 task ID | `426c2d37-7d31-5837-b8c1-1830c51e44d4` |
| Pod UID | `6ebef5de-811e-4f12-a9c7-bb6fc0f7cdd6` |
| 实际运行时间，UTC | `2026-10-28 08:31:40`–`08:32:43` |
| 最终执行状态 | `completed`，exit 0，restart 0 |
| 模型账本 | 五条 `complete/200`，包含一次前置回显 |
| 工具观测 | 七次 read、一次 write，学术工具观测为 0 |
| 报告 | 4357 bytes，一条引用 |
| 报告 SHA256 | `e9bac40884dac53617740f8680d4c9cf58531d9761a7901d910ce4bddca465a0` |
| 停止与清理 | 全部容器已实际 terminated，过期 grant 返回 401，临时资源已清理 |

证据保存在 `evidence/candidate-pi-revise-r3/`。独立导入复验发现，捕获的 Pod
`phase` 仍为 `Running`，而全部容器已真实 terminated；候选证据校验器过度依赖
顶层 phase。验收工具修复 `f263796` 要求 `restartPolicy=Never`，核对完整容器集合及
真实终态，相关 **73 项测试通过**。该提交只更新验收工具，实际运行与部署镜像仍为
`772bef1`；不把验收工具提交号当成生成产物的运行版本。

原始证据、时间与哈希保持不变。运行时 `candidate_scenario.py` 的原始源码 SHA256 为
`5ac4226b2cb68111babf0d7282183bc4d5f770ebf804b05c70fafb53fef521bc`，
复验所用源码 SHA256 为
`5e9f2d100297b70ecd89b657e1697b4db9bec7aca66be2770585bf8b84ac3906`。
分别记录两个实测值，不修改历史 provenance 或放宽生产隔离。

独立复验于 `2026-10-28T13:02:16Z` 通过，报告为服务器
`research-business-r4/revision-candidate-r3/verified-revision/summary.json`。
复验解析保存的 Responses 工具调用并核对最终 write 的原始字节，没有执行保存的工具
命令或新增模型请求。`model_write_bytes_match=true`，最终写入 SHA256 为
`922614b8006bcf3d062099d3c1cc9b4ff73fe0288ee68f1f45b70648bde42e35`。
papers/evidence/references 原始字节不变，选区前后的报告字节不变，选中章节确有变化。
该章节由 **1002 bytes 增至 1225 bytes**；本轮证明固定证据、选区隔离与发布流程通过，
不声称“缩短章节”的表达要求已经达到。

隔离数据库副本中的真实 `ScientificAgentProjection.publish()` 和
`ReportVersionStore.compare_candidate()` 均成功，生成候选版本
`eab8be8d-22c7-5b56-b30f-873282d9acd8`。当前版本仍为
`bf0fcc92-28b2-55cd-9ee7-b4d43f2e27a9`，`adopted=false`，没有自动采用新版本。
本地 publication ack 已记录，远端 delivery ack 未记录，原准备数据库不变。
这次候选导入仍不代表正式 Jobs HTTP 交付链已经验收。
引用追踪保留 `recorded_assessment_not_independently_verified` 提示：已核对证据关系和
报告合同，不等于独立验证论文结论。报告及比较结果已下载到本机 ignored
`agent_service/rendered/research-validation-20261028/`，包括 `revised-report.md`、
`section-before.md`、`section-after.md`、`comparison.json` 和 `revision-summary.json`；
本机修订报告 SHA256 与服务器一致。

## 关闭路由状态下的部署

正常 `agent_service/deploy.sh` 已完成 `772bef1` 部署，保留原有数据库出站精确配置，
未执行 `init-db`。独立核对时间为 `2026-10-28T11:55:42.301576Z`，公开证据位于服务器
`/root/agent-service-deploy-20261026/research-rollout-772bef1.json`：

- `agent-service-api`、`agent-service-dispatcher`、`agent-model-proxy` 三个 Deployment
  的 generation 与 observedGeneration 均为 7，desired、ready、available 各为 1。
- 三个控制面 Pod 均 Running、Ready、restart 0，实际镜像为上表的 control
  `74f564bc307bae4734699d56b7d0a5450061b163d946666c82ec9873219ae024`。
- 七个 Pi binding 与 pool 引用 Pi 镜像
  `bf9739abdd993c192e7bb86b87c3564182db0bd660b5a6beb6ef44c0f6bed401`。
- 七条 route、十四个 pool 仍全部关闭，Worker Pod 数为 0。
- `healthz` 返回 200，未认证 capabilities 返回 401。

本次更新使部署配置引用已修复的 pi-cage Worker，不表示已开启科研生产执行入口。

## Pi 0.99.2 凭据解析问题

科研生成 r2/r3 先通过 Python 前置回显，但原生 Pi 请求收到 401 `model_grant_invalid`。
固定 Pi 版本把 `"apiKey": "AGENT_MODEL_API_KEY"` 当作密钥字面值，
不会自动读取该名字的环境变量。`7379e4c` 将配置改为字面量：

```json
{"apiKey": "${AGENT_MODEL_API_KEY}"}
```

真实 CLI 在禁外网容器内完成四组对照：JWT-shaped 和 sk-shaped 两种合成凭据，
旧配置均发送变量名，新配置均精确发送环境变量中的 token。未使用真实密钥，
模型请求为 0，临时容器已清理。聚合报告为本机 ignored
`agent_service/rendered/pi-cage-auth-resolution-report.json`。

此前 pi-cage 协议 r3 只检查 `auth_present`，没有验证 token 等值，因此不能据其结果
声称认证正确。此次等值对照和后续生成 r4 才覆盖这一缺口。
`78d9374` 另补新版 `task-model API error (401): 401 ...` 错误包装的固定分类；
按事件 provider 严格匹配，仅输出分类和 HTTP 状态，不输出正文或更改重试规则。
该错误分类专项为 **7 passed、72 subtests**。

## UID 与失败记录

| 运行 | 永久消耗 UID | 结果 |
| --- | --- | --- |
| `candidate-pi-research-r2` | 42000036–42000038 | 原生鉴权失败；一次前置回显 |
| `pi-cage-offline-launch-r1` | 42000039–42000041 | 真实 launcher、AppArmor、64 进程限制下的 CLI 夹具通过；模型调用 0 |
| `pi-cage-offline-network-r1` | 42000042–42000044 | DNS、未认证 401 和新错误包装复核；模型调用 0 |
| `candidate-pi-research-r3` | 42000045–42000047 | 确认原生 401 `model_grant_invalid`；一次前置回显 |
| `candidate-pi-research-r4` | 42000048–42000050 | 真实生成及独立业务发布复核通过 |
| `candidate-pi-research-revise-r1` | 42000051–42000053 | 渲染时 Secret 名超工具长度限制；未启动 Pod 或模型，已清理临时 Secret |
| `candidate-pi-revise-r1` | 42000054–42000056 | 真实修订在 collector 误判处失败，未交付产物 |
| `candidate-pi-revise-r2` | 42000057–42000059 | 模型提交选区外 claim，被 `invalid_claim_markers` 拒绝 |
| `candidate-pi-revise-r3` | 42000060–42000062 | 真实执行 completed；独立产物、隔离发布与候选版本比较通过 |

UID 以服务器 `uid-reservations.json` 为准。不得复用失败或未启动的预留范围；
新候选使用新的 task、intent、输出目录及 UID，不能延长已经过期的修订任务来重跑。

## 剩余范围

真实科研生成、固定证据修订、独立成果校验、隔离业务发布和候选版本比较已通过。
控制面及关闭状态 Worker 配置已更新。下一步是正式科研 Jobs HTTP 提交与交付链、
远端 delivery ack、交付恢复及逐业务接入；科研入口尚未切换，生产 route/pool 仍关闭。
这些工作不要求先完成 PPT 全流程或 Hermes 接入。

历史 **1124 passed** Linux/真实 PostgreSQL 完整回归仅覆盖 `d871717`，不覆盖本轮
之后的修复；本轮定向测试、真实领域验收和部署检查分别记录，不合并成一次最新全量
回归。进展已写回映射项目的 OpenSpec 变更
`add-pi-first-agent-execution-service`，规格校验通过；任务仍为 10/24，未 apply/archive。
