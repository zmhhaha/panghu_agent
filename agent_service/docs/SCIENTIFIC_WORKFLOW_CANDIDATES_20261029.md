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
