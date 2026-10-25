# DSH 代码学习 PPT：部署与首次真实规划回执

记录日期：2026-10-25（Asia/Shanghai）。

**网站执行已开启，资料上传及解析成功；首次真实规划失败，尚未产生可供确认的方案或 PPT。**
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

## 故障证据与尚未确定的原因

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

## 正在修复与后续验收

正在针对只读状态查询加入有限重试，并调整固定 context 与 Ceph journal 配置。
这些修改尚未经过第二次真实模型任务验证；本回执不把修复中的行为记为已交付成功。

后续需要依次核对：

1. 记录修复提交及最终部署镜像，确认 Gateway 初始化与只读状态查询在实际环境完成，
   重试有明确上限，异常有可诊断的安全错误信息。
2. 对同一项目重新提交真实规划，确认资料解析、会话、模型响应和 plan 入库全部完成；
   如再次失败，保留具体失败阶段与脱敏状态信息，并核对停止证明和清理结果。
3. 按真实记录累计模型用量；供应商未报告的费用继续标为 unknown，不补写0。
4. 只有网站实际展示方案后，才进入用户确认与后续制作步骤；当前不记录方案确认、
   PPT 生成、预览、下载或人工产物验收通过。
