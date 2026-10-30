# 科研项目工作台：候选、发布与流程验收

当前进度（2026-10-30）：新科研工作台和五个已验收阶段已上线。原 HTTP 项目完整
链路、独立来源与账本审计、失败历史保留及最终集群健康检查均已通过，任务 6.3、6.5
已完成。本文保留历次验证与失败，文末记录本轮最终镜像、交付与证据；不将旧候选或
其他项目替代原项目验收。

## 首轮候选验证（历史记录）

2026-10-29，新科研 plan/retrieve/outline/draft 已在实际 Kubernetes 节点上依次执行，
使用用户的 `zmhhaha/pi-cage` 和受限 Responses 模型代理。四阶段均完成停写、成果
独立校验、隔离业务数据库保存与读回。没有将旧 generate/revise 的 acceptance
复制给新合同。以下首轮记录仅证明当时的候选范围，不表示当时已完成正式 Jobs HTTP
或业务发布；后续发布证据另列。

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

## r3：正式输入读取与全文披露误判修复（实际验收待完成）

r1、r2 的候选和失败记录继续保留。之后两个浏览器大纲任务
`f6db7b0b-6f6e-4598-b075-e9dd6b02b47c`、
`83705d37-558e-49ba-af3f-9cc8e9bf7c0b`，以及正式 HTTP Draft 任务
`1fb50167-0cc8-4f26-931f-9463e0d4c178` 均停止于 `worker_output_invalid`。
诊断阶段停止新增模型重试，使用受保护的原 context、模型返回与落盘输出在精确 r2
镜像中离线复验；没有覆盖失败历史、改写已确认快照或放宽引用校验。

两类真因已通过实际失败输入确认：

- **大纲输入无法完整读取。** 两次 context 分别为 84,195、85,186 字节，均只有一行。
  Pi 0.99.2 的 `read` 每次最多 50 KiB 或 2,000 行，只支持按行 offset；大于 50 KiB 的
  单行无法靠继续读取下一行恢复。将纳入来源由 20 条缩为 7 条仍保留历史检索批次与
  观测，因而没有消除该问题。实际输出的 gaps 明确记录读取上限和无法读取，evidence
  与 sections 均为空；契约按原规则拒绝空 sections。
- **全文披露检测跨越了语法分句。** 实际 Draft 引用行中的技术短语为
  `已获取摘要，无全文`；旧正则将“获取”与逗号后的“全文”连在一起，误判为已取得全文。
  新匹配要求获取/阅读动作与全文对象处于同一分句；否定语句也仅作用于其本地分句，
  转折或分号之后的真实全文获取断言仍被拒绝。没有跳过 References，也没有把所有
  包含“无”的句子全局放行。完整 claims、citations、访问级别与来源校验保持原规则。

提交 `94bac87670d96d40ee27eb80de0880ed81d27f02` 保留唯一传输文件
`inputs/context.json` 的原字节、已确认对象与 digest。
Controller 先只读挂载并校验它，再生成独立的 `context-view/`：

- `START.json`、brief 和来源索引引导当前阶段读取；纳入来源单独保留最新完整原记录，
  不合并字段、不替换来源，也不提升访问级别。完整结构视图保留历史与排除来源。
- 结构 JSON 使用 `indent=0`，按行分页时不会因嵌套缩进产生体积放大。长字符串使用
  `$readable_string`，每块不超过 16 KiB UTF-8 与 1,024 行，并记录顺序、大小和哈希。
  长 key、过长 JSON pointer、深层或过宽的不透明 JSON 使用 `$readable_json`，在进入
  不可读 key 前将原子树的 canonical JSON 分块；按序连接、解析后恢复原对象。
  原内容中的同名保留标签作为原始数据编码，不冒充 host 描述符。
- 派生文件在构造和迭代编码时即检查 64 MiB 单文件、128 MiB 总量与 40,000 文件预算，
  先于 InlineFile/base64 创建；不依赖最后的 staging 检查才发现超限。manifest 记录
  原文件字节哈希、context digest 与派生文件/字符串/子树映射。
- 派生来源经原有受保护 staging 创建，固定 bind 到 `context-view/`，独立核验
  `ro,nosuid,nodev,noexec`。该目录进入 protected_paths，避免任务 UID 接管其所有权。
  Pi 与 Hermes 的生产释放入口统一先证明 writers 已停止、成功卸载，再清理 backing；
  失败保留引用与不确定状态。没有新增 Agent 工具、shell、网络出口或 capability。

AppArmor 只追加以下三条固定规则；原 inputs/base 规则及其拒绝项保留。节点必须解析、
装载后用实际新镜像验证，文件存在或构建完成不代表内核已允许该挂载：

```text
mount options=(bind) /run/agent-control/research-context-views/*/inputs/ -> /work/*/context-view/,
remount options=(ro,bind,nosuid,nodev,noexec) /work/*/context-view/,
umount /work/*/context-view/,
```

模型提示补充了非空 sections、未记录方法应标为 unknown、claim 段落与访问披露要求，
并要求新大纲标题使用单行、无首尾或重复空白的纯文本。**本次未修改标题 schema、
未规范化已确认标题、未重写历史快照。** 现有大纲校验仍可能接受带换行或不规范空白
的旧标题，而 Markdown 章节索引会折叠空白；这种输入的后续 draft 仍可能触发
`workflow_draft_outline_mismatch`。统一用户编辑约束和历史标题匹配规则需要独立修复，
不能由本次提示词改进宣称已解决。

本机最终定向验证为 **147 passed、4 项 Windows 平台跳过、30 subtests passed**；
`git diff --check` 通过。覆盖完整 UTF-8/换行材料重组、50 KiB 以上单行摘要、原 digest
保持、60,000 字符 key、400 层嵌套与 85,000 个短数组元素、原始保留标签、来源 ID 碰撞、
构造期预算、固定挂载与失败清理、生产 Pi/Hermes 释放入口，以及披露语句正反转折。
这组测试没有运行模型，也不证明 Linux 内核挂载或线上任务已通过。

服务器已使用正常构建入口完成以下精确镜像的构建与推送：

- control：`arm-cluster-master:5000/agent-service-control@sha256:7bc5dc06e7ddff9beacf4fcf7fd731bc70e5b55e7e51a38851ead53ed716f619`。
- Pi：`arm-cluster-master:5000/agent-service-pi@sha256:8ceb1ce6c95db2255b15b0b176aa97124e165de8022f3c9d402e1f57bdc6e26b`。

本条记录时，r3 的节点规则实际装载、新镜像降权只读挂载探针、真实旧输出离线复验、
新原生候选、正式 HTTP 与浏览器全流程仍待完成；尚不能登记新 acceptance 或将这些
步骤标记为通过。后续需在保留本节诊断和全部失败历史的基础上追加实际证据。

### r3 后续：五项原生候选与离线独立保留审查

上述待验收状态之后，r3 的 plan/retrieve/outline/draft 与独立 revise 五项真实原生候选
均已通过。下表仅转录四阶段摘要中的 Job/Attempt 前八位；完整 UUID、固定镜像、
执行绑定、结果摘要与模型账本见服务器 `evidence/candidate-wf-<stage>-r3/` 及
`scientific-workflow-candidate-canary-r3/<stage>/verified-summary.json`。

| 合同 | Job 前八位 | Attempt 前八位 | 模型请求 |
| --- | --- | --- | --- |
| plan | `0da9870f` | `1b26ab06` | 5 |
| retrieve | `87962966` | `5bdb2a17` | 8 |
| outline | `6b407744` | `871959b4` | 6 |
| draft | `f577d17e` | `9081cac7` | 6 |

四阶段共 25 条模型请求，隔离项目按明确标记的运维测试身份推进两个确认门，停写产物
经独立合同校验后入隔离业务库并读回。该验证没有冒充用户确认，没有使用生产业务库，
也没有伪造远端 delivery ack；总摘要为
`scientific-workflow-candidate-canary-r3/summary.json`。

独立 revise 又执行 4 条模型请求，五项合计 **29 条**。其永久消费 UID 为
`42000142–42000144`；证据为 `evidence/candidate-wf-revise-r3/`，独立发布摘要为
`research-business-r4/revision-candidate-workflow-r3/verified-revision/summary.json`。
这次新修订仍以既有真实生成 `evidence/candidate-pi-research-r4/` 为父报告；它不能被
记录成“从 r3 Draft 发起了第五次真实修订”。r3 Draft 的离线 compose 兼容检查与这项
真实 revise 具有不同父输入和验收范围。

新镜像的候选证据已经过离线独立导入，并审查生产目录替换方案的历史保留范围，
结果位于 `scientific-workflow-promotion-r3/final/`：

- `config.public.json` SHA256：
  `1822a9d342e3f0e35c6583cb2d6340059d8a42a3e0c2ed154e9c8847fb9614b3`。
- 独立 preservation review SHA256：
  `4811c21850d0f3a4c91750892ed2a9a43cc5d65789fa42e0b6eb4c91150437bf`。

原 generate 与旧执行绑定、历史池和宿主按保留审查范围继续留存，不通过覆盖历史
ConfigMap 或修改旧任务的 pins 完成切换。离线导入和原生候选结果尚不能证明正式
Jobs HTTP、业务发布/接收回执、从新 Draft 修订及浏览器操作均已通过；这些实际发布
验证仍须继续追加独立记录。

### r3 后续：正式部署与历史保留核对

审定目录 `scientific-workflow-promotion-r3/final/` 已完成正式部署。运行中的 control
镜像为
`arm-cluster-master:5000/agent-service-control@sha256:7bc5dc06e7ddff9beacf4fcf7fd731bc70e5b55e7e51a38851ead53ed716f619`，
plan/retrieve/outline/draft/revise 五条科研路由使用本节记录的精确 Pi 镜像
`sha256:8ceb1ce6c95db2255b15b0b176aa97124e165de8022f3c9d402e1f57bdc6e26b`。
业务 API 也已正常部署新独立镜像（摘要前缀 `dd211ece`；完整摘要以本轮 rollout 记录为准），
UI 保留此前镜像（摘要前缀 `a0a20`），没有借本次控制层切换替换界面。

部署前后独立核对的控制层历史账本完全一致；原执行 binding、历史池和宿主保留。
切换核对时全局执行槽为 free、next fence 为 15；这是新业务恢复前的核对时点，后续
正常接收新任务会推进 fence 和槽状态。没有通过清空账本、重建历史绑定或丢弃不确定
任务完成切换。

历史业务数据只读审计结果位于
`evidence/scientific-existing-data-readonly-r3.json`，结果为 passed：

- 11 个科研表中的原有 60 行全部保留；10 个非 workspace 表与基线完全一致。
  workspace 中原有记录完整保留，允许新增工作台项目，不把新增行误计为历史丢失。
- 原 SQLite 备份 SHA256 为
  `df9da72c21e7fae156d60a5c922ceaa5adb075229dbaaf715f182dec7f62b0fa`。
  备份的独立恢复、完整性检查和原行核对通过；只读审计未将恢复副本写回生产。
- 16 份历史报告与 4 个版本共完成 72 项 HTTP 检查，均为 200，正文哈希匹配。
  其中 68 项使用记录中的真实归属身份，另 4 项明确标记为旧 shared 数据的运维检查，
  没有为 ownerless 旧记录伪造用户归属。

正式 HTTP 恢复已接收新的 Draft（Job ID 前八位 `5f67793b`），并保留此前两条 failed
历史。该尝试随后以 `worker_output_invalid` 结束：远端 Job 为
`8506cc3f-e83e-403f-b311-64af1706b2a7`，Attempt 为
`ca208d5b-05e1-4ff1-94d2-176b921942fd`，5 条模型请求均返回 200 SSE。浏览器重新整理
大纲的另一次尝试（远端 Job 前八位 `5e960200`）在 1 条 200 SSE 模型请求后以
`model_error` 结束。HTTP 200 只说明传输返回，不证明模型输出或领域成果校验通过。

两项失败记录和原始响应均保留，正在进行离线诊断；不盲目重放相同模型请求，不把
r3 原生候选通过记成这些实际业务尝试通过。成稿、后续修订、业务发布/接收回执和
浏览器全流程尚未完成，后续修复及新的独立验收仍须追加记录。

## r4 检查点：直接否定披露修复，原生候选等待调度

以下按发生顺序保留各轮检查点及失败记录；“等待”“尚未切换”等状态只对应所在
检查点。最新部署和验收进度见末尾 r6 及后续记录和六任务证据矩阵。

r3 正式 Draft 的原始输出包含 `全部十项来源均无已获取全文`。提交
`61f80600a9e5c3d2487a163398a96ee1de97b92d` 修复这项本地否定误判：对每个获取/阅读
断言分别检查紧邻的“无”，不让该否定覆盖同句后续的正断言；外层否定或含混的嵌套
否定不适用这项窄范围例外。r3 的原失败 Job、Attempt、模型返回与输出继续保留，
没有将旧失败改写为成功，也没有更改已确认需求、证据快照或大纲。

本机定向验证为 **173 passed、4 项 Windows 平台跳过、30 subtests passed**。
服务器已通过正常构建入口构建并推送以下精确镜像：

| 组件 | 镜像摘要 | 此检查点状态 |
| --- | --- | --- |
| agent-service control | `sha256:faa672b5bc0fb56579ba229a4c22b4a2fe2cd2bf4ef1ed1d021117dd6bde5b7a` | 已构建、推送，未 promote |
| agent-service Pi | `sha256:3a92bbdf58d33f2b1e8033c7b23eafd4608b3b675bfbf21b409fa6008c813ea2` | 已构建、推送，未 promote |
| 科研业务 API | `sha256:8880f0a2953910685133224fe1af4c3749cab23e17da290436ed7b0b1e105d9a` | 已正常部署 |
| 科研 UI | 摘要前缀 `a0a20` | 沿用既有镜像 |

control、Pi 镜像仓库分别为 `arm-cluster-master:5000/agent-service-control` 与
`arm-cluster-master:5000/agent-service-pi`。新禁用候选目录为
`catalog-workflow-61f8060-r4/generated/`；构建成功不表示五条科研路由已切换。此检查点
生产控制层与 plan/retrieve/outline/draft/revise 路由仍使用前述已部署 r3 配置。
`scientific-workflow-production-before-r4/` 捕获当前 r3，并与
`scientific-workflow-promotion-r3/final/config.public.json` 做 JSON 语义核对，作为后续
r4 独立导入与历史保留审查的 before-snapshot；不复用更早轮次的部署前快照。

在精确新 Pi 镜像内，只读复验原 r3 失败输出已通过，证据为
`evidence/scientific-workflow-preserved-regression-r4.json`。受保护原输出的 SHA256 为
`878265dbee7a9f22ca25d47ae78ba1f34f4933396e3d00f9aad6a9f74587512d`；复验没有修改
该输出，也没有请求模型。这仅证明原失败输出可通过当前修复后的离线校验，不代表
新原生候选、正式 HTTP Draft 或浏览器成稿已通过。

第一次 r4 调度预检在原生候选启动前遇到其他运维任务持有
`workflow-candidate.lock`；即使当时没有 Worker Pod，该锁仍属于有效调度边界，不能
通过删除锁、跳过锁或干预其他进程继续。该次创建的预检业务目录已保留为
`scientific-workflow-candidate-canary-r4-lock-preflight/`，没有为本轮创建原生执行
证据、预留 UID 或请求模型。

新的 r4 专属调度入口已启动，并在此检查点等待同一把锁。它在创建新业务目录前
先等待锁，每阶段和独立 revise 再次获取同一把锁，使用共享四小时期限；原 generic
和 r3 调度文件、永久 UID 台账与其他任务均未修改。后续 UID 必须在真实候选锁内
重新读取并由原 operator 预留，不能把“当前无 Pod”视为可复用 UID 的证据。
本轮没有再次修改 AppArmor 或固定只读挂载规则。

浏览器另一次大纲任务（远端 Job 前八位 `9759bf0c`）已 completed，结果包含 7 条
纳入来源、8 条证据、4 个章节，现停在第二确认门。这项真实业务推进与 r4 原生候选
是不同验收记录；不能据此标记 Draft、Revise、导出或完整浏览器流程已完成。

正式 HTTP 验收的进度 checkpoint 已复制到新 API Pod，SHA256 为
`21165eef27770ae66ea02df5882173bfaad11c29d1f12d1c5246b2667739f811`。它保留既有
失败记录和确认关系，属于恢复运维验收进度文件，不是向生产数据库回写恢复副本。
此检查点尚未发送新的 Draft 重试 POST；后续需要分别记录原生候选、独立导入及五条
路由切换、显式 HTTP 重试、真实新稿修订和下载结果，不能用离线回归或构建回执替代。

### r4 后续：五项原生候选、独立保留审查与正式切换

上述等待调度的检查点之后，r4 的五项真实原生候选均已通过。下表使用 Job/Attempt
前八位；完整 UUID、精确镜像、绑定、停写证明与模型账本保存在
`evidence/candidate-wf-<stage>-r4/`，四阶段隔离业务读回结果为
`scientific-workflow-candidate-canary-r4/<stage>/verified-summary.json`。

| 合同 | Job 前八位 | Attempt 前八位 | 模型请求 | 永久消费 UID |
| --- | --- | --- | --- | --- |
| plan | `6e887bdd` | `c603fce0` | 7 | 42000160–42000162 |
| retrieve | `0205e4d7` | `1a4c695d` | 8 | 42000163–42000165 |
| outline | `7a51fecf` | `a4a4002a` | 8 | 42000166–42000168 |
| draft | `21d9fb37` | `3af0d821` | 10 | 42000169–42000171 |
| revise | `09be2e36` | `7070964c` | 4 | 42000172–42000174 |

四阶段共 33 条模型请求，独立 revise 为 4 条，五项合计 **37 条**。UID 由原 operator
读取永久台账后逐次预留，未绕过候选锁或复用既有范围。四阶段使用隔离业务库和明确
标记的运维测试身份；独立 revise 仍使用历史真实生成
`evidence/candidate-pi-research-r4/` 作为固定父输入，结果位于
`research-business-r4/revision-candidate-workflow-r4/verified-revision/summary.json`。
它不是从这次 r4 Draft 发起的业务修订，不能替代该项真实链路验收。

五项证据经精确新 Pi 镜像内的离线独立导入和完整历史保留审查，形成
`scientific-workflow-promotion-r4/final/`：

- `config.public.json` SHA256：
  `7068c06eb460317f169f3d96d351ca8185a8c9a8fbd367eed07fde6ea959c2fc`。
- 独立 preservation review SHA256：
  `516769ab8bb1149fdd0d55dc97d30e6f735b8e5bd41a75a84c0071dcc6f0c57c`。

该审定目录已正式部署：control 使用本节构建表中的 `sha256:faa672b5…` 精确镜像，
plan/retrieve/outline/draft/revise 五条路由使用 `sha256:3a92bbdf…` 精确 Pi 镜像。
原 generate、全部历史 binding、池、宿主与旧 acceptance 保留；本次只替换这五条
科研路由的新任务准入，不覆盖 r3 的正式配置目录、原候选证据或失败记录。

部署前后的控制层执行账本完全一致。切换核对时全局执行槽为 free，next fence 为
18；这是业务恢复前的核对时点，随后正常接收任务会推进 fence，不能把 18 当作之后
持续不变的当前值。没有清空任务、抢占其他执行或通过重建账本完成切换。

正式 HTTP 已显式接收新的 Draft 重试，业务 Job 为
`5603cd8c-e0e8-40d9-bbbf-4e3aaff0d8ce`，恢复进程已启动。此检查点该请求仍在排队，
尚无完成结果；此前 r3 Draft 失败及确认关系继续保留。浏览器项目的第二确认门已由
用户在手机上确认，写作任务正在执行。此时一项执行、一项 queued，观察符合全局
同时只运行一个任务的限制，不能将排队误计为失败或额外并发执行。

此检查点尚不能登记正式成稿、从新稿修订、导出或完整浏览器流程通过；它们需要
继续核对实际结果、业务保存/接收回执与下载。五项原生候选和离线审查通过，仅用于
支持本次受控切换，不能替代这些业务验收。

### r4 后续：真实浏览器成稿、修订采用及预览修复

上述排队检查点之后，浏览器项目（ID 前八位 `154644dc`）已取得真实新草稿：业务
Job 前八位为 `4fecf384`，远端 Job 为 `79d19e8e`，Attempt 为 `f5180f88`，本次成稿
执行记录 14 条模型请求。这是正式业务成稿记录，区别于前述 r4 隔离原生候选。

从这份新稿发起的真实 `research.revise.v1` 也已完成，形成版本
`8a0c77dc-3b0b-44ba-abdb-b462698aaf5b`。浏览器已比较所选章节的差异、明确采用
候选、保存批注，并对该明确版本完成项目内私有定稿。它补充了“从新 Draft 发起真实
修订”的业务操作证据，不能用此前基于历史 generate 的独立 revise 候选替代。
此检查点尚未独立核对下载字节与来源记录，也未据此宣称端到端验收全部完成。

真实草稿阅读还暴露了 `<!-- claim:c1 -->` 等内部 trace 注释被显示为正文的问题。
提交 `6b2c0ce` 仅在报告 Markdown AST 中移除整节点匹配的 canonical claim HTML
注释，保留行内/围栏/缩进代码、转义示例及其他 HTML 的原显示行为；引用按钮仍按
已记录的引用编号打开来源。原报告、API 返回、导出字节与引用校验不因预览修复改写。
本机 UI 回归共 **19 passed**，TypeScript 检查和生产构建通过；初次构建仅受系统
Temp 短路径 ACL 影响，改用工作目录中的构建临时目录后完成，没有修改系统设置。

服务器已从该提交按 `latest` 构建 UI 并正常部署，实际固定镜像摘要为
`sha256:56c11683f83ced2d1150a6bdc5e4d828cead05b9c253091f8e936343981fb33e`。
回退规格保存在部署根目录的 `scientific-workbench-previewfix-6b2c0ce/`。
API 保持 `sha256:8880f0a2953910685133224fe1af4c3749cab23e17da290436ed7b0b1e105d9a`，
control/Pi 继续使用本节 r4 已验收的精确 pins；这次预览改动没有更换运行组合或
重新执行五项原生模型候选。新 UI 的真实页面复核仍应追加自己的证据。

另一个原 HTTP 项目（ID 前八位 `b72e4cb7`）的 r4 Draft 重试
`5603cd8c-e0e8-40d9-bbbf-4e3aaff0d8ce` 随后仍以 `worker_output_invalid` 结束；远端
Job 为 `274d4e11-7610-4633-830d-d46a09cf7fb3`，正在保留原始输出进行诊断。
浏览器项目成稿与修订成功不能改写这条失败记录，也不能把两个项目的阶段拼接成
同一条成功链路。本次记录没有再重放该失败模型请求。

### r4 后续：真实浏览器下载字节与访问隔离

对上述浏览器项目的明确版本完成了 **8 项真实 HTTP GET 检查**，其中 4 项成功下载，
另 4 项访问隔离检查返回预期的 404/401。下载使用实际响应正文核对，未用页面预览、
HEAD 响应、文件名或状态码代替字节验证，也未修改报告和来源记录。

| 已核对产物 | 正文字节数 | SHA256 |
| --- | --- | --- |
| v1 Markdown | 4472 | `fa097d55500a305fcd860451f56f63fde3d9e3b90a5caf01ef22035ae56b0362` |
| v2 Markdown | 4619 | `46173034c61bf214b170081deca9edea399702fa94f73b4d2e48256761e83142` |
| v2 来源记录 | 由受保护证据保存 | `b46dcd8cb8e17e1a1eccd79b824374aa4ea2802298b6d0e71f361265954f9d87` |

受保护证据目录为部署根目录下的 `evidence/browser-scientific-download-bytes-r4/`，
其归档 SHA256 为
`757d910d0ca2cc662c79663a170100a48981c2bd3d662676489641ae46f90a1f`。
本节只记录统计、大小和哈希，不复制报告正文、来源内容、用户身份或认证材料。
浏览器下载证据属于该项目自身的业务链路，不替代原始 HTTP canary 的完整 checkpoint、
四条失败历史以及独立执行/模型账本核对。

### r5 检查点：并列否定修复与新候选复验

原 HTTP r4 Draft 的受保护原输出包含窄范围披露表达
`》（2025）均无摘要或已获取全文`。提交
`c67394f75b9ed2afaacea537b48a3a7f9ed6bd4d` 对紧邻的“无摘要或/或者”作并列否定
处理；外层否定、对比表达、后续独立正断言及新主体仍按原限制核验。这项修复没有
编辑原模型输出、确认快照或失败记录。定向验证为 **161 passed、117 subtests passed**。

首次 r5 原生候选的 plan 已通过：Job 为
`6771a6d0-7165-4273-9406-cb890f86aa09`，Attempt 为
`319e6a23-9a7c-40b7-8756-38e98c7a2a66`，7 条模型请求，永久消费 UID
42000181–42000183。随后 retrieve 在 6 条模型请求后以 `model_error` 结束，
UID 42000184–42000186 已消费；原失败输出、终态停写回执和
`evidence/candidate-wf-retrieve-r5/` 证据继续保留，没有人工修补输出。
因此不能把首次 r5 的 plan 通过登记为五项候选全部通过或已切换。

之后启动的 `r5-retry` 使用新 before-snapshot、正常构建入口、新 catalog 后缀及独立的
五阶段证据目录重新验收；原 r5 目录和 UID 台账不覆盖、不复用。最终运行镜像必须
以这次构建的完整 digest 和每个组合自己的候选回执核对，不能仅依赖 `6993` 等摘要
前缀，也不能因源码相同推断镜像摘要相同。此检查点该轮仍在运行，尚未记录正式
promote。科研 API 已正常部署摘要前缀 `165776` 的新构建，UI 仍使用上述
`sha256:56c11683…` 精确镜像；完整 API pin 以部署回执为准。

原始 HTTP canary 在新 API Pod 中的 checkpoint 恢复已经完成，但尚未发送本轮
r5 Draft 重试 POST。原四条失败、其 Attempt 和镜像绑定必须继续逐条保留；
独立审计仍要求原始 run/project 的五阶段完整 checkpoint，并分别核验旧成功阶段
历史镜像和新 Draft/Revise 的明确完整镜像。现有 stop receipt 核验失败仍待独立
诊断，不通过放松核验或拼接浏览器项目结果来登记通过。本节不勾选剩余 OpenSpec
任务。

### r5-retry 后续：五项候选完成与正式部署前审查

后续 `r5-retry` 的五项真实原生候选均已通过，逐阶段模型请求为：

| 合同 | 模型请求数 |
| --- | --- |
| `research.plan.v1` | 5 |
| `research.retrieve.v1` | 8 |
| `research.outline.v1` | 6 |
| `research.draft.v1` | 8 |
| `research.revise.v1` | 4 |

五项合计 **31 条**。每个组合均独立核验权限、模型、工具、输入、成果与停止证明，
并通过独立导入；本轮独立 revise 继续使用历史真实 generate 作为固定父输入，
不将其登记为从本轮新 Draft 发起的修订。首次 r5 的失败候选和早期 r4 证据均保留。

本轮部署前快照为 `scientific-workflow-production-before-r5-retry/`，公开配置
SHA256 为
`7b291d6f130156e4d851dc7106b4c5cbafd4682a5bcc2f9dc58f38824d992561`。
独立导入与历史保留审查形成 `scientific-workflow-promotion-r5-retry/final/`：

- `config.public.json` SHA256：
  `c6d0004272cf7add4bfd92312763e49963ccd05310786c13903f829f03b03a27`。
- preservation review SHA256：
  `ab0423d023018bd024d36b1a0acf7e81f1561e43f1b60da7d31d7d59aafeb6c3`。

独立导入、历史保留检查及部署的 check-only 已通过，随后正常部署入口执行的正式
rollout 也已成功。本轮精确发布镜像如下，镜像通过正常 `latest` 构建/推送，部署与
验收保留不可变 digest、源码标识及合同 pins：

| 组件 | 正式部署的完整镜像摘要 |
| --- | --- |
| agent-service control | `sha256:6331b40d7525b070ace62228db84a56fbdb66d636e4cc30e8c30ab1eb3ae435f` |
| agent-service Pi | `sha256:6993be008891ffd6b6c9a8eba521e8056870d8aa0a1d5f9db9732687341a60eb` |
| 科研业务 API | `sha256:16577613f78c8be556e2424e330b1f76b7cc80be312b500dc712be0bd73f673f` |
| 科研 UI | `sha256:56c11683f83ced2d1150a6bdc5e4d828cead05b9c253091f8e936343981fb33e` |

这些新候选和正常 rollout 不替代下面浏览器项目自身的五阶段实际业务证据，也不能
预先证明另一条原始 HTTP canary 的完整链路通过。

### 真实浏览器链路：独立五阶段账本、恢复与移动布局

浏览器实际项目的五阶段独立账本审计现已通过，结果保存在
`evidence/browser-scientific-lineage-audit-r3/summary.json`。对应真实模型请求数为：

| 业务阶段 | Fence | 模型请求数 |
| --- | --- | --- |
| plan | 8 | 3 |
| retrieve | 9 | 4 |
| outline | 17 | 6 |
| draft | 18 | 14 |
| revise | 20 | 8 |

这条业务链合计 **35 条**模型请求，与前节 31 条新原生候选分别记录。审计逐阶段
核验 native 停写证明与 Kubernetes 外层证明各自的签名、明确 Pod、停止状态及资源
退役，没有将两个不同合同的证明直接判为相等。两道用户确认、来源快照、Responses
模型记录到最终成果的 trace 关联均已核对。审计前后业务文档 revision 保持 84，
摘要前缀保持 `e9a706`，只读审计没有改变业务状态或发起新的模型执行。

其中 revise 的明确父版本确为该项目的新 Draft，独立核对所选章节范围、选区外原
字节不变、来源快照固定，以及候选比较、显式采用、批注和私有定稿的真实读回。
这是对新稿修订的独立业务证明，不依赖前述以历史 generate 为父输入的 revise
候选。项目原来的三项 outline 失败业务记录继续保留；本节不将这三条失败各自的
stop receipt 声称为已完成独立失败历史审计。

前两轮只读审计失败证据同样保留：第一轮误将 native 与外层不同结构的证明作相等
比较，第二轮导入业务 workspace 投影函数时缺少该模块。后续修正只涉及新的独立
审计文件及其运行输入，没有改写原审计文件、产品实现、账本、模型输出或证明，
也没有跳过签名核验。
最终通过的是新的 r3 审计；不能把旧失败日志改记为通过。

真实登录页面已完成刷新、离开到 `about:blank` 后重新进入的恢复检查，能够恢复同一
项目、已选版本与私有定稿状态。前述 4 项成功下载及 4 项预期拒绝仍为实际下载证据。
375 像素宽的实际页面检查未发现横向溢出，报告内部 claim 标记不再显示，引用可打开
相应来源。该检查记录的是实际页面的移动宽度布局与既有用户手机确认；不将其扩大
描述为另一个手机设备重新执行过全部模型阶段，或用户退出认证后重新登录。

原始 HTTP canary 仍是独立验收。在正式 r5-retry rollout 成功后，显式 r5 Draft 重试
已经接收，业务任务为 `ecd37bf8-7dfa-47a1-92cb-6f038f4a148f`，进度恢复进程已启动。
该次执行随后以 `worker_output_invalid` 结束，远端 Job 为
`8a11b3e5-6337-4edb-8f9b-89c4eab04e67`。本轮 checkpoint 和日志继续保留，正在该
精确镜像内进行只读离线诊断，没有将输出改成成功或以相同请求盲目重放。
此前四条失败之外新增这第五条失败，因此仍未取得原 HTTP 完整 checkpoint，也未
完成包含最新失败的整链独立审计。上述浏览器完整账本不是原 HTTP 项目的替代链路。

### 2026-10-30 r6 阶段收尾：相邻明确否定修复与五项候选

对原 r5 Draft 失败输出的只读诊断确认，触发误判的实际子句为
`链接也不等于已获取全文`。修复针对这类相邻明确否定，原输出和第五条失败记录继续
保留。新源码 `d8f593283d3f98610e0c969dc8a8517a416b1b1f` 已提交、推送并由服务器
拉取；定向验证为 **159 passed、15 subtests passed**，另有独立复核通过。此处
记录已有验证结果，没有为文档更新重新请求模型或重复运行原生验收。

服务器已通过正常构建入口构建并正式部署以下 r6 精确镜像：

| 组件 | 正式发布的完整镜像摘要 | 阶段收尾状态 |
| --- | --- | --- |
| 科研业务 API | `sha256:78ec2567c8108737bc1d07627cf5faee523b84860d5ba3926be979f605078636` | 正常部署成功 |
| agent-service control | `sha256:d14ea800d25b0e4a930ca1f1b295c109610aa8559c8b7f5827ba839d9815b197` | 正常 rollout 成功 |
| agent-service Pi | `sha256:1eb3a4c4ce121b16426d05bd40726d1c17234ef143962b7d65e956f9a5f92d92` | 五项原生候选及独立审查通过，五路由正式切换 |
| 科研 UI | `sha256:56c11683f83ced2d1150a6bdc5e4d828cead05b9c253091f8e936343981fb33e` | 保持已发布镜像，升级后真实页面恢复检查通过 |

r6 使用全新 release 身份与目录，部署前基线固定为
`scientific-workflow-promotion-r5-retry/final/config.public.json`；r5 脚本和原证据
保持原字节。候选锁、永久 UID 消费、禁用 catalog、逐组合独立验收、不可变镜像及
历史保留约束不变；独立 revise 的 genuine 历史父输入仍不冒称为本轮新 Draft。

原 r5 失败写出候选（受保护记录的摘要前缀 `7db90`）已在上述新精确 Pi 镜像内完成
context、stage result 和 disclosure 的只读离线复验，均通过，新增模型请求数为 0。
证据保存在 `evidence/scientific-preserved-r5-regression-r6/`。这项结果只说明固定旧
输出通过当前修复后的验证，不将旧 Job 状态改成成功，也不替代新的原生候选和原始
HTTP canary 验收。

在上述四阶段检查点之后，独立 revise 也已完成，r6 五项真实原生候选全部通过：

| 原生候选 | 模型请求数 |
| --- | --- |
| plan | 7 |
| retrieve | 8 |
| outline | 10 |
| draft | 6 |
| revise | 4 |

r6 原生候选合计 **35 条**，与此前浏览器真实业务链的另外 **35 条**分别记账，
也不与 r5-retry 的 31 条混用。独立 revise 的 Attempt 为
`0f169aa6-cc70-49a1-9682-cfb93eaecb87`，永久消费 UID 为 42000214–42000216，
保持历史 genuine 父输入，不替代浏览器新稿修订证据。

部署前快照 `scientific-workflow-production-before-r6/` 的公开配置 SHA256 为
`80bfc678c37608e002760981ab31a2f615368dee221368cfdff44c0bc79bdb8f`。
独立导入与完整历史保留审查形成 `scientific-workflow-promotion-r6/final/`：

- `config.public.json` SHA256：
  `c1b9b6dcf4bf1f8143ee6b940de6ccac20cc5e1b8a0f021ac020581239beab28`。
- preservation review SHA256：
  `200e45b851d5b174c05bcccba867f2fc644b6f4fb8f2405e11438161d2259702`。

独立导入/历史保留审查、正常部署的 check-only、实际 control rollout 和科研 API
部署均已通过，回执为部署根目录的 `scientific-workflow-r6-rollout-status.json`。
正常 rollout 前后的完整执行账本一致，没有通过清空或重建账本切换新组合。

最终只读收尾核对保存在 `evidence/scientific-stage-close-r6.json`：全局执行槽
free，next fence 为 22，Worker Pod 为 0，21/21 项 allocation 已 retired；Job
状态统计为 12 completed、9 failed、1 cancelled。五个部署分别为 ready 1 / desired 1。
这些是本轮结束时的核对值，不作为未来新任务开始后仍固定不变的断言。

升级后真实浏览器刷新恢复了 v1、v2、当前 v2、私有定稿和下载按钮。报告中的科研
结论尚未人工审核；项目内私有定稿记录的是所选版本，不等同科研结论已获人工确认。
另有 r6 显式重试、恢复驱动与账本审计三份 ignored helper 离线自检通过，其中
账本自检为 **381 项检查**；它们未替代尚未执行的原 HTTP r6 模型/业务验收。

用户要求休息并阶段性结工后，本轮不再发起新的模型任务，不对原 HTTP canary 发送
r6 重试。已经启动的正常部署和只读服务/执行槽检查已完成。原五条 HTTP 失败、
确认关系和恢复 checkpoint 均保留；API 替换前的受保护 checkpoint 备份为
`evidence/recovery-pre-api-d8f5932/scientific-workflow-scientific-http-20261029-a.json`。
原 HTTP r6 在当时结工前既未 restore，也未 retry，未产生新的模型请求；当时接续步骤留待下次，
不重置原项目或借用浏览器项目的成功链路。

当时 OpenSpec 服务按真实完成项更新并通过验证，**24/26** 项完成、validation **0 issues**，
revision 为 `fcb76a686389c4e83fa098eba82010ed32f617b2`。本次新勾选 4.3、5.2、5.3、
6.2，6.3 和 6.5 保留待办。整个 change 尚未完成，未 apply 或 archive。

### r6 结工时的接续计划（历史，后续 r9 已完成）

以下为当时计划的历史命令，r6 结工时未执行；后续已完成，不用于重跑当前项目。
当时要求依次核对 restore 成功、retry 明确 accepted，
再启动有限恢复进程；使用保留的原 r5 checkpoint 和本轮明确的完整 Pi digest。
若请求已发送但响应不明确，继续按恢复脚本的 GET/intent 状态核对，不重发 POST。

```bash
cd /root/agent-service-deploy-20261026/panghu_agent
SCI_R6_CHECKPOINT=/root/agent-service-deploy-20261026/evidence/recovery-pre-api-d8f5932/scientific-workflow-scientific-http-20261029-a.json
SCI_R6_PI_DIGEST=sha256:1eb3a4c4ce121b16426d05bd40726d1c17234ef143962b7d65e956f9a5f92d92

python3 -B agent_service/rendered/run_scientific_http_draft_recovery_r6.py restore \
  --r5-checkpoint "$SCI_R6_CHECKPOINT" --image-digest "$SCI_R6_PI_DIGEST"
python3 -B agent_service/rendered/run_scientific_http_draft_recovery_r6.py retry \
  --r5-checkpoint "$SCI_R6_CHECKPOINT" --image-digest "$SCI_R6_PI_DIGEST"
python3 -B agent_service/rendered/run_scientific_http_draft_recovery_r6.py start-resume \
  --r5-checkpoint "$SCI_R6_CHECKPOINT" --image-digest "$SCI_R6_PI_DIGEST" \
  --canary-source /root/agent-service-deploy-20261026/panghu_agent/agent_service/rendered/workflow_http_canary.py
```

仅当原始 run/project 的 `evidence/scientific-http-recovery-r6/latest-checkpoint.json`
实际记录五阶段完成后，再独立核对 ledger、五条历史失败和新阶段的精确镜像：

```bash
python3 -B agent_service/rendered/operator_workflow_http_ledger_audit_r6.py \
  --draft-image "$SCI_R6_PI_DIGEST" --revision-image "$SCI_R6_PI_DIGEST"
```

审计保留历史成功 plan 的旧镜像、retrieve/outline 的旧镜像及五条失败各自的原
Job/Attempt/镜像绑定，逐条验证停写证明；不通过改写历史或跳过签名来取得通过。
这些 ignored 运维脚本、对应公共审计 helper 和 expectation 文件由受保护部署目录
保管，恢复前先核对其已审核版本；业务源码仍从已推送提交读取。

### 六项任务的阶段证据矩阵

以下要求对应 OpenSpec 变更 `redesign-scientific-project-workflow` 曾待办的六项任务，
状态已按本轮最终证据更新。各次历史运行保留自己的镜像与验收范围；未将旧运行
改写为本轮精确镜像的成功记录。

| 任务 | OpenSpec 状态 | 原验收要求 | 已有实际证据及剩余范围 |
| --- | --- | --- | --- |
| 4.3 | 已完成 | 对新草稿完成固定证据 research.revise.v1 兼容验收，保留明确父版本、选区外字节、候选比较与显式采用。 | 浏览器新 Draft 的真实 revise 已通过独立账本与字节审计：明确父版本、固定来源/trace、选区外字节、比较、采用、批注及私有定稿均核对；历史 generate 原生 revise 单列。 |
| 5.2 | 已完成 | 为新 plan/retrieve/outline/draft 组合建立独立权限/模型/工具/输入/成果/停止验收，逐组合开放路由。 | r7 五项原生候选共 39 次请求，逐组合独立导入与历史保留审查通过；精确 Pi537、API8c669 已上线，保留并发 PPT/视频发布。 |
| 5.3 | 已完成 | 验证全局单槽、按有限任务启动清理Pod，等待确认/阅读/离线不占运行Pod。 | 最终健康证据为 scientific-final-health-r9-concurrent-control.json：槽 free、next fence 31、Worker Pod 0、30/30 allocation retired，非终态任务和未确认停写均为 0。 |
| 6.2 | 已完成 | 完成真实登录浏览器桌面/手机全流程及刷新/退出重进恢复验收，不用静态原型替代。 | 真实桌面完整链路、用户手机确认、375px 页面与引用检查、刷新/离开重进及升级后 v1/v2/current v2/定稿/下载按钮恢复已记录；范围如正文，不声称另在手机重复所有模型阶段或退出认证重登。 |
| 6.3 | 已完成 | 用真实研究主题完成规划→第一确认→检索/证据整理→第二确认→成稿→修订比较/采用→定稿导出，并独立核对账本和产物。 | 原 HTTP run r9 完整 checkpoint、两版报告、比较/采用/批注/定稿和下载核对通过；ledger-audit-r9-idle 独立验证五个成功阶段及八次失败、原确认和重试链、来源/选区外字节与双停止签名。科研结论仍需人工审核。 |
| 6.5 | 已完成 | 保存固定源码、镜像/合同pins、真实验收和部署证据后替换科研页面入口，更新OpenSpec状态。 | API8c669/UI56、五个科研 Pi537 绑定已部署；固定源码6492、真实验收、并发配置保留及最终健康证据已保存，OpenSpec 据最终证据收尾。 |

### 2026-10-30 r7 续做：模型流失败、披露修复与并发发布保留

恢复原 HTTP run 后执行了一次有持久意图和幂等键的显式草稿重试，沿用两道原确认，
没有重跑规划、检索或大纲。新增业务 Job 为
`7d7b4b64-cf33-408d-a783-fbedb0dc2d38`，Agent Job 为
`3d50bf7c-cfd8-43de-8e38-09c9e1517264`，Attempt 为
`2bb50a05-a4d5-4ad1-94a3-3c0d3f882fac`，执行镜像仍是原 r6 Pi 摘要。
该任务实际以 `model_error` 失败：前四条 Responses 完成，最后一条虽返回 HTTP 200，
但流内终结事件为 `upstream_error`，不能当作模型成功。五条请求及失败状态均保留。
完整恢复日志和 checkpoint 在 `evidence/scientific-http-recovery-r6/`，真实失败诊断
在 `evidence/scientific-http-model-failure-r6/`。

对已保存的 `write` 候选另做离线检查时，发现真实措辞
`不能据链接声称已阅读全文` 被全文访问断言检查误判。源码
`6492a283ae8c7d86dc298fcff12f728433c2012d` 仅识别紧邻、位于子句末尾的明确禁止
声称；外层反转、双重否定、后续独立获取断言和跨子句仍拒绝。定向验证为
**190 passed、15 subtests passed**，另有独立复核通过，没有扩大提示词或关闭校验。
代码已提交、推送并由服务器拉取。

候选输出 SHA256 为
`98290b125d91cfcf1f94c29ca1cd5156e78f4f5967fc2d861943b8657eb0e1cf`，context SHA256
仍为 `644c70b0bad7826de6966c4e8112803d515a58c9aa64e61b9974ddd636739457`。
同一候选在新精确 Pi 镜像内通过 context、stage result、trace 与 disclosure 离线
检查，新增模型调用为 0，证据为 `evidence/scientific-preserved-r6-regression-r7/`。
这是从持久 Responses 的写出参数重建的候选，不冒称原 Pod 磁盘产物或已成功任务。

正常 `latest` 构建得到以下不可变镜像：

| 组件 | 镜像摘要 | 本轮用途 |
| --- | --- | --- |
| 科研 API | `sha256:8c669f1a5bec7430e194922f87f3fce6fcb891fba5b6beb00e2d3b77686397aa` | 已部署；原 HTTP 验收单独记录 |
| Pi 执行镜像 | `sha256:5372e60ee7080fe0a32e8e13c036d844cf754feae88d834bb5231b57bfee4d0c` | 五阶段新资格验收通过 |
| 本分支 control 构建 | `sha256:136f0719e080433b4f92c39af6aa899d78099df88090fd00da82152321574ebb` | 已构建，未覆盖并发上线的更新版本 |

五项真实原生候选依次通过：

| 阶段 | Agent Job | 模型调用 |
| --- | --- | --- |
| plan | `e7e54c4d-46c3-4e2e-b2e1-aaa211887fe7` | 7 |
| retrieve | `6710f57f-91a0-4f49-9904-4980ce8e6d81` | 6 |
| outline | `03c333f3-a949-4bc7-8dd2-7710692d7c07` | 10 |
| draft | `d092a988-4432-4016-a048-819353365a71` | 6 |
| revise | 见 `evidence/candidate-wf-revise-r7/` | 10 |

合计 **39 次**，不与原 HTTP 新失败的五次、r6 原生或浏览器链混记。独立 revise
仍使用 genuine 历史父版本，不代替原 HTTP 新草稿的修订。候选使用共享锁和永久 UID
记录，没有修改节点隔离策略。完整证据为 `scientific-workflow-five-candidates-r7.json`
及 `scientific-workflow-r7-pipeline-status.json`。独立导入和全历史保留审查已通过，
原资格目录 `scientific-workflow-promotion-r7/final/` 的 config SHA256 为
`aa75bfe0734b2382a88574c85f5058eed1148b45c86ba6dcee6b2c534fc915a0`，保留审查摘要为
`eaba3ba1ccecd22575250226705b0d44d23f42d679cd43727c505a1ab0102195`。

发布前发现 Secret `subPath` 持有可读但 `nlink=0` 的旧 inode，严格配置加载器正确
拒绝重读；并非 Vault 凭据缺失，也未证明它导致模型流错误。当前 Secret 仅新增其他
业务令牌，旧调用令牌及签名密钥未变。临时诊断使用 Pod 内受限临时副本并立即清理；
生产加载器没有放宽。运维脚本最初只接受目录 `0700`，又因 Kubernetes `fsGroup`
产生的 `02700` 停在部署前；新副本仅补充接受该无组/其他访问权限的继承位，仍拒绝
`02710` 等开放权限，旧失败脚本及回执保留。

期间另一项正常发布新增五条 PPT v2 路由，并将 control 更新为
`sha256:956a42131a23e9443774584292cd15cdc72811f8e782d0d35db283dfee791c03`。
旧 r7 发布驱动被保护性检查阻止，未将其改回旧 control 或覆盖新配置。新的只读快照
是 `scientific-workflow-production-before-r7-concurrent/`。后续发布以此为基线，
只移植已资格验收的五个科研组合及原字节 host；保留当前 control、PPT 路由、所有
非科研行、调用方、Secret、RBAC 和网络配置。

原 HTTP 最新检查点还备份在
`evidence/recovery-pre-api-6492a28/scientific-workflow-scientific-http-20261029-a.json`。
新 r7 恢复和六失败历史审计 helper 已离线验证并独立复核：账本启动器 **515 项**、
真实原生/外层签名回归 **98 项**通过；旧 r6/r2 脚本原字节保留。

受保护的配置合并与上线已完成。仅切换三个 control Deployment 的配置卷引用，保留
并发发布的 control `956a…`、PPT v2 和其他业务配置。部署前后完整执行账本一致，
50 份历史 host ConfigMap 原字节保留；当前 control 精确镜像离线解析通过 55 份 host
和 11 条已启用路由。独立复核为 **105 项检查、38 种拒绝场景、5 项历史 host 检查**。
实际 ConfigMap 为 `agent-service-config-a9dac488b1f071db1631`，配置 SHA256 为
`a9dac488b1f071db16315d5d40bd16721fc48cdb4e7e8aa63f67c9f83c54b430`。证据为
`scientific-workflow-promotion-r7-scoped/deployment-evidence/summary.json`。
随后按正常科研部署入口更新 API，UI 仍为原 `56c116…`；部署日志为
`scientific-workflow-r7-api-rollout.log`，没有用本分支较旧的 control 构建覆盖线上版本。

原 HTTP r7 重试沿用两次确认和六条既有失败历史，但新业务 Job
`cb9e3441-f148-4da3-b9b3-12ed58ee5eed` / Agent Job
`5c8b2e2b-2585-4661-977b-16863d5120ba` / Attempt
`2b4a7a80-57fd-4c62-99c6-cfbad665b526` 再次以 `model_error` 结束。
这次实际使用 Pi `5372e6…`，前三次 Responses 正常完成，第四次只有
`error / upstream_error` 事件，未写出初稿。模型账本 `complete` 表示响应字节已完整
保存，不表示模型成功；Pi 正确终止了阶段。llm-service 的通用错误来自上游明确失败
事件，现有日志不足以区分供应商具体原因，不据此认定密钥失效。
证据为 `evidence/scientific-http-model-failure-r7/diagnosis-summary.json`，checkpoint
为 `evidence/scientific-http-recovery-r7/latest-checkpoint.json`。该次失败继续保留；
在该 r7 检查点，原 HTTP 完整验收与最终健康检查当时仍需完成；后续 r9 完成见下文。

### r8 原项目恢复：草稿已入库，修订候选正确拒绝

原项目两次确认、三个成功阶段和六条 retired 失败字典保持不变，再将 r7 失败作为
第七条历史追加。r8 仅显式重试草稿，仍使用已验收的 Pi `5372e6…`，未更换供应商、
Key、模型别名、提示词或校验器。持久 POST 意图与原 checkpoint 先落盘；歧义恢复仅 GET。
本轮草稿业务 Job `30729883-596a-485a-bd94-f9cb3ea79dd0` / Agent Job
`67f55894-bbe0-45bb-bf75-a814a35073cb` 已完成，业务独立校验、保存与 delivery ack 为真，
结果摘要为 `a538a390007ac7db1dc888f99a486c61ab93ccac8d9a629d7114feede0b4b642`。

针对这份新草稿的修订业务 Job `4bdd6e2b-0de4-4cef-a065-271a41b5ceaf` / Agent Job
`be484697-d54d-4ee8-ad7d-2e8b56b78624` / Attempt
`72c7c19a-7b16-4326-97e4-abfdabc6da2f` 在四次正常完成的 Responses 后返回
`worker_output_invalid`。精确 Pi 镜像离线检查确认：输入和候选 JSON 格式均有效，
但新增 `revision_scope_access` claim 关联的证据要求引用 `[1]`，对应段落没有引用，
因此 `compose_revision` 正确拒绝 `claim_citation_mismatch`。不放宽此规则、不手工修补
候选为成功，也未覆盖已保存初稿。诊断使用持久模型写出参数重建候选，不冒充原 Pod
磁盘产物；新增模型调用为 0。证据为
`evidence/scientific-http-revision-failure-r8/diagnosis-summary.json`。

只读独立审计另行固定七份科研验证源码，取自提交 `6492a28…` 的 Git 原始 blob，
逐份与精确 Pi 镜像 SHA256 核对，使用私有命名包，不覆盖并发发布的 control 内置模块。
这是 Windows CRLF 与镜像 LF 的原字节核对，不是替换验证语义。双签名、来源和退役
检查继续保留；旧 disabled 组合与本轮同一 enabled 组合分别如实核验。

### r9 原 HTTP 完整业务链路

对失败的修订执行一次有界显式重试，保留同一父版本、选区、固定材料和原指令。
未修改业务源码、模型、提示词、校验器或已部署镜像。两道确认、四个成功阶段及
七条既有历史原样保留，失败修订作为第八条追加；仅将已接受的修订步骤映射到新任务。
恢复助手和单 POST/丢响应 GET 恢复/Pod 替换防回退均通过针对性离线检查和独立复核。

新修订业务 Job 为 `267d790c-0129-4abb-a8b0-8cc9c5d31a65`，Agent Job 为
`ea5a4e67-8571-4d20-bb63-499cc75721c0`，结果摘要为
`b7084de344822ba16e3186c4721e4161f57e803c31bd634e6be1d72fc6cead89`。
它已成功修订 r8 新草稿，并通过业务发布与交付确认。原 HTTP run
`scientific-http-20261029-a` / 项目 `b72e4cb7-a548-5bb6-a763-c8123f9012ad` 随后完成
真实差异比较、明确采用、批注、私有定稿，以及 Markdown 和来源文件下载字节核对。
整个 run 保留 **13 个任务、2 个报告版本、8 次真实失败**，没有用其他项目替代原链。

| 交付对象 | 固定身份或摘要 |
| --- | --- |
| 原草稿版本 | `7d1dbbf3-a16c-4909-b8a9-de5213010dfc` |
| 已采用修订版本 | `57440e0c-49d5-4e96-bcec-4e7836762d58` |
| 私有定稿记录 | `90cd144a-0008-4620-a692-ef5e89a02495` |
| Markdown SHA256 | `480839ef2cd7ce098d555bd94e0d0ae0c071fbae1cf473940ebf99a79e3ae43a` |
| 来源记录 SHA256 | `050debb6df40ecca56053120d7649838313fc2889f42e105b34ea843edb8095b` |

完整 checkpoint 与恢复日志为 `evidence/scientific-http-recovery-r9/`，
`resume-status.json` 记录 `complete=true`、退出码 0。技术流程验收不代表科研结论
已经人工审核；私有定稿的 `publication_authorized=false`，没有公开发布或投稿。

### 最终独立审计与健康检查

`evidence/scientific-http-recovery-r9/ledger-audit-r9-idle/summary.json` 已通过：五个
成功阶段、八条失败身份和各自的镜像/尝试、完整重试链、来源快照、新草稿父版本、
固定材料、选区外字节、原生与外层停止签名均独立核对。审计使用 PostgreSQL 只读
事务，未新增 Job 或模型请求；验证源码为固定 `6492a28…` 的独立命名包。首份
`ledger-audit-r9/` 因当时全局槽未空闲而停止，原证据保留，未放宽空闲条件。

期间并发视频发布将共享 control 更新为
`arm-cluster-master:5000/agent-service-video-control:latest@sha256:aff85fcb5233e5cf0881e16df8e62ce73a3d07e739c52713beeb21f7e26596cd`，
OCI source revision 为 `323b9430475355bb01716fc04228d47404104bfb`。本轮不回退该发布。
当前共享配置为 `agent-service-config-fdb5a936de57ca2c12e3`，SHA256 为
`fdb5a936de57ca2c12e36897372227a9671aff1110f783a3b16545e41e507d43`。相对 r7 scoped
基线，全部既有行和公共参数保留，仅追加其他业务配置；50 份历史 host 和五份新
科研 host 的内容摘要仍一致。保留证明为
`evidence/scientific-final-preservation-r9-current-control/preservation-summary.json`，
SHA256 `c1286c7f8b56df1d866dfea0ad2f4f02fe2ac9e873966f3ebe14ba98bdaef8b3`。
并发 control 仍带有旧 image/source 注解，本记录以实际 Deployment 镜像与 OCI 标签
标明其身份，没有改动其他发布的注解或触发额外 rollout；科研 API 自身注解仍正确。

旧健康预期针对 control956，因此保留其不匹配结果；新固定预期只更新为实际并发
control 镜像，全部健康谓词不变。最终
`evidence/scientific-final-health-r9-concurrent-control.json` 为 `passed=true`，SHA256
`0fbc565b4b1f37e7f60d6f2d57ee45be87d62362830d74ef85efaf86c43d0443`：五个部署均
1/1 就绪，API8c669/UI56/当前 control 摘要正确，Worker Pod 0，全局槽空闲，
next fence 31，30/30 allocation 退役，非终态、隔离和未确认停写任务均为 0。
配置与凭据挂载链接正常，检查使用 REPEATABLE READ/READ ONLY；未输出凭据或修改生产。

OpenSpec 最终 **26/26** 项完成，validation **0 issues**，变更已归档为
`2026-10-30-redesign-scientific-project-workflow`，12 项规格已更新到项目主规格。
服务存储 revision 为 `03131b1260edc4c07ae05ece4e177deaa6e51f18`；无需再单独 apply。
