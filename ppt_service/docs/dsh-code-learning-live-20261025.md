# DSH 代码学习 PPT：部署与真实规划回执

记录日期：2026-10-25（Asia/Shanghai）。

**网站执行已开启，资料上传及解析成功；首次规划执行中断，第二次 Worker 完成但独立合同校验失败，尚无可供确认的方案或 PPT。**
本回执区分部署、无模型预检和实际业务任务，不把执行器启动或供应商请求完成视为任务完成。

## 资料与项目

| 项目 | 固定信息 |
| --- | --- |
| 网页项目 ID | `66528841-f8f7-4766-ae7b-0dee654a9af5` |
| PPT 目标 | 8 页中文技术导读，面向理解与维护现有 DSH 工作台的开发者 |
| 操作端源素材 | 本地 `.runtime/dsh-code-learning-source.md` |
| 代码范围 | `panghu_chat/dsh/` 的部署与集成实现 |
| DSH 源码固定提交 | `99603ca56500947ef77da58e3e811006cee5373b` |

8 页制作要求已在网页保存，源素材已上传到上述项目。素材覆盖系统接线、启动顺序、
登录适配、SSH 执行、持久化配置层、网页设置补丁、项目 runner 及代码阅读顺序。
它依据固定提交的本仓库集成源码，不延伸到未读取的上游 DSH 模型推理循环、上下文压缩或
工具调度算法；不据此声明上游仓库公开状态或许可证已经核验。

## 部署与无模型预检

部署 helper 使用 `--enable-execution --apply --enable-site` 完成，退出码为0；
网站执行 admission 已开启。这表示请求可以进入执行流程，不表示规划或生成已经成功。

此前完整无模型预检的 scope 为 `7d606076-6503-4753-bac8-ade8376ab0c8`，
`passed`、`no_model_calls`、`writers_stopped`、`cleanup_complete` 全部为 `true`，
proxy、worker、sandbox 各14项网络检查通过，退出码为0。
服务器成功报告为 `/root/ppt-network-preflight-20261025-b3-r2.log`。
首轮失败与停止处理修复记录见 [部署排障第12节](../deploy/k8s/TROUBLESHOOTING.md)。

本轮通过预检并启用业务执行时使用的镜像：

| 组件 | 镜像 |
| --- | --- |
| Controller | `arm-cluster-master:5000/ppt-hermes-controller@sha256:bf51a8662f834c7e2e76dbd9ffa76e54b9fc1c0588eaab486c1ee2267d9c696a` |
| Worker | `arm-cluster-master:5000/ppt-hermes-worker@sha256:8340270fdfb0b4d39c13ef8daa776867cd28890c645726222274cc00b6ef771d` |

## 首次真实规划的已知结果

| 检查 | 实际结果 |
| --- | --- |
| Job ID | `afb4b9aa-c2ff-4b10-b814-84e369803ea5` |
| attempt-token 标识 | `1-1` |
| 源素材重新解析 | 成功，网页显示“解析完成” |
| Gateway、policy、session、run | 已观察到启动过程 |
| Worker 退出 | 出现 `HermesError`，以非0状态退出 |
| Backend 后续处理 | 已停止并清理本次执行资源 |
| 业务错误码 | `execution_interrupted` |
| 可供确认的 plan | 未产生 |
| PPT 产物 | 未产生 |

代理已观察到一条供应商请求完成记录：模型为 `gpt-6.1-sol`，输入685 tokens，
输出65 tokens。供应商费用为 **unknown**，不能当作费用0；本次真实业务任务也不能
记录为“没有调用模型”。此前预检的 `no_model_calls=true` 仅属于预检自身。
供应商请求完成未转化为可供网站使用的规划结果，不能据这条记录认定规划成功。

## 首次执行的超时证据与边界

已知日志事实：Gateway 查询模型 context metadata 失败，随后使用256k fallback；
初始化耗时约29秒，而 worker 状态查询客户端的超时为10秒。
这些时间差与 `HermesError` 一起提示，启动期间的状态查询可能被阻塞并触发失败。

这是待验证的解释。失败当时的具体 HTTP status 未保留，当前证据不能确定旧请求究竟
返回了哪个状态，也不能单凭耗时差断言它就是唯一根因。无模型网络预检通过同样不能
替代 Gateway 初始化、状态查询或完整模型会话的业务验证。

离线读取实际 Worker 镜像中的 Hermes 源码还确认：
`gateway/platforms/api_server_runs.py` 的异步 `_execute_run` 在提交执行线程之前，
同步调用 `_create_agent`（904–911行）；状态查询 `_handle_get_run`（999–1003行）
使用同一事件循环。Agent 初始化中的同步元数据查找因此能够阻塞状态查询。
这为固定服务上下文预算及恢复只读查询提供了代码依据，仍不补造旧请求的 HTTP 状态。

## 状态查询恢复修复与第二次部署

修复已提交为 `12eb8a1`，并完成服务器构建、推送和部署：固定256000服务上下文预算，
使用 `database.journal_mode=delete`；仅对原 run 的状态 GET 传输失败及502/503/504累计
最多恢复三次，不重新 admit，也不重置阶段截止时间。取消先发 stop，Gateway已退出则不重试。
私有回执记录固定操作类别、合法HTTP状态及elapsed_seconds，不保存异常正文或原URL。
范围回归为36 passed、2 skipped、80 subtests passed。

build4 Controller digest为 `sha256:fed62b0f15b97f6b0e71a7eef0e30aff4fc7b84c6ec0e3875a55ef5d77db53da`，
Worker为 `sha256:4203561e86b7a22e401d398825798bda56a97d1237697b57158ec0b252039a93`；
使用上表对应registry仓库。此次镜像完整预检scope为 `8616ce31-6787-4852-893c-e57a67ee1e37`，
四个成功flags全部为true，三个角色各14项检查通过，退出0。
报告为 `/root/ppt-network-preflight-20261025-b4.log`。之后已恢复Controller和网站执行入口。

## 第二次真实规划：Worker 完成，独立合同校验失败

| 检查 | 实际结果 |
| --- | --- |
| Job ID | `daaa0f6a-1cca-48e0-8a27-fe4343ec2c3b` |
| Worker 结果 | `completed` |
| Worker 耗时 | 222.64秒 |
| `hermes_status_retries` | 2 |
| 候选 manifest 状态 | `awaiting_confirmation` |
| 独立校验 sandbox 退出码 | 2 |
| 实际 `validated.json` 错误码 | `stage1_contract_invalid` |
| transport 显示的错误码 | `validation_execution_failed` |
| 数据库中的可确认方案 | 未入库 |
| PPT 产物 | 未产生 |

本次已完成模型 Worker 执行并留下候选文件，但独立校验拒绝了计划结构。
`completed` 和候选自报的 `awaiting_confirmation` 都不等于网站已经接受方案；
校验失败后没有向用户提供确认入口，也没有进入后续 PPT 制作。

已经查明一个具体合同缺口：Worker 提示要求在 `manifest.plan` 中逐项提供确认内容，
却没有明确给出 `control/planning.py` 接受的严格结构。实际候选使用
`stage1_items`、`service_form_parameters`，既不符合官方 `stage1` 结构，
也不符合已支持的 `outline/design` 格式，因此触发 `stage1_contract_invalid`。

错误展示还有独立问题：transport 的安全错误码正则只接受 `[a-z_]`，不允许数字，
将包含数字1的 `stage1_contract_invalid` 遮成了 `validation_execution_failed`。
这没有改变校验结果；真实 `validated.json` 仍保留了具体错误码。
审查修复正在进行，不能把较泛的 transport 错误当作缺乏原始校验证据。

## 网站状态同步修复

网站任务状态同步修复 `f2d8838` 已完成构建、部署，站点 Ready。
真实 SSE 的 `job.started`、`stage.changed` 只携带阶段信息，不会提供旧前端依赖的
`payload.status`；旧页面因此可能持续显示最初的“排队中”。修复改为权威任务快照同步，
可见页面的活跃任务每5秒补同步；手动刷新同时刷新任务、项目和版本，
并防止旧响应回退事件游标、终态或已刷新的方案。隐藏页面与终态停止自动同步。
该修复改善显示与实际任务的对应关系，不改变第二次任务的合同校验失败结果。

## 当前修复与后续验收

正在为 Worker 补充明确的 stage1、stage2 结构规范，保留独立校验的严格要求，
不通过放宽检查接受本次不匹配的候选。随后需要重建执行镜像并再次运行真实规划。
本回执尚未记录该后续尝试的成功结果或最终镜像 digest；前两次失败证据继续保留。

后续需要依次核对：

1. 验证 stage1、stage2 提示合同和安全错误码修复，记录实际部署提交与镜像；
   校验仍须拒绝错误结构，具体错误码应完整传递到私有回执。
2. 对同一项目重新提交真实规划，确认资料解析、会话、模型响应、独立合同校验和 plan
   入库全部完成；如再次失败，保留具体阶段、脱敏状态和停止证明、清理结果。
3. 按真实记录累计模型用量；供应商未报告的费用继续标为 unknown，不补写0。
4. 只有网站实际展示方案后，才进入用户确认与后续制作步骤；当前不记录方案确认、
   PPT 生成、预览、下载或人工产物验收通过。
