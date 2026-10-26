# DSH 代码学习 PPT：部署与真实规划回执

记录日期：2026-10-25（Asia/Shanghai）。

**8页“白底架构导读”审核稿已成功入库；恢复登录后，内置浏览器已逐页核对8张PNG并成功触发“下载审核草稿”。该版本为review草稿，独立视觉、PowerPoint和正式发布门仍未完成，浏览器落盘文件hash未独立核对。**
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
后续 `ad97c11` 已修复错误传播，具体回执仍保留。第二次任务代理账本按请求去重后共13次：
11次completed、1次client_disconnected、1次proxy_shutdown。已知输入428895、输出4753 tokens，
另有2次请求的输入和输出均未知；这些是已知部分，不是完整总量。13次费用均unknown。

## 网站状态同步修复

网站任务状态同步修复 `f2d8838` 已完成构建、部署，站点 Ready。
真实 SSE 的 `job.started`、`stage.changed` 只携带阶段信息，不会提供旧前端依赖的
`payload.status`；旧页面因此可能持续显示最初的“排队中”。修复改为权威任务快照同步，
可见页面的活跃任务每5秒补同步；手动刷新同时刷新任务、项目和版本，
并防止旧响应回退事件游标、终态或已刷新的方案。隐藏页面与终态停止自动同步。
该修复改善显示与实际任务的对应关系，不改变第二次任务的合同校验失败结果。

## build5 发布与规划成功

`ad97c11` 已提交推送并部署：提示明确给出Stage1 JSON和Stage2三方向、实际N页与逐页表达，
保留严格校验；错误码过滤允许有界小写字母、数字、下划线，非零退出仍拒绝。沙箱21项、
提示与任务13项及13子测试通过；独立review另有交叉覆盖的24项fixture/合同检查，不相加。

| 组件 | `arm-cluster-master:5000` 对应 `ppt-hermes-*` 仓库的digest |
| --- | --- |
| Controller | `sha256:db94823530c997989b768533d615b4da876e3386539b5dfd01b533f6d41bc1dc` |
| Worker | `sha256:f19950f0bf09bcf80f822e03b703891b6162d4f0b586e43e52fd4d53e2d5c995` |

先关闭网站准入，再部署禁用状态的Controller；无模型预检scope
`fd0d4a1a-ab59-4945-947f-e5c1fa82d25d` 的四个成功flags全true，三角色各14项通过，退出0。
之后恢复Controller及网站执行，部署助手退出0。报告：`/root/ppt-network-preflight-20261025-b5.log`。

第三次初始规划job `fdddea19-dd1e-4e43-8def-2c7258be1698` 已通过独立校验并入库方案修订1，
项目修订10。只读数据库核实job为succeeded、stopped=true，scope为
`afce56a0-7457-57eb-baa2-a681a1a6c045`；确认前confirmed_at和confirmation_selection均空。

## 两轮真实确认与规范准备

用户在本会话明确确认8页中文、16:9、自由设计、面向开发者的自主阅读与维护参考；
随后代用户在网页保存第一轮确认，项目修订11。第二阶段job
`4b1d35ff-eeaf-4438-9115-7f429e216f1c` 也成功入库方案修订2，项目修订13。
三个方向为白底架构导读、深色链路追踪、纸面维护手册，每方向均有8页内容及8项逐页表达。
用户再次明确选择 `direction-a` 白底架构导读，已保存网页第二轮确认，项目修订14。
随后点击生成演示稿，创建job `fb19dbf7-4bee-49b6-90e4-320a39c8f6c6`，项目修订15，
规范准备已成功，`stopped=true`，scope为 `6d51eaa5-8c08-5a98-9604-b28703d07d15`。
`design_spec` 为26852字节，`spec_lock` 为2652字节。准备阶段代理共26次请求：
24次completed、2次proxy_shutdown；未据此补造完整token或费用总量。
控制器随后自动创建独立生成任务，无第三次业务确认。

## 首次生成：官方预览渲染失败

| 检查 | 实际结果 |
| --- | --- |
| Job ID | `92a705d4-3068-4416-ab71-cdf91b897ae7` |
| scope | `a74b24ad-d169-54a2-afcf-815277ef4c44` |
| 数据库状态 | `failed`、`stopped=true` |
| 公开错误码 | `quality_gate_failed` |
| 候选manifest | `needs_input`，不满足发布条件 |
| SVG | 已写出8份，作者early/final静态检查均0错误、0警告 |
| PNG、PPTX | 未产生，未完成视觉检查、finalize或导出 |

实际失败发生在官方 `visual_review.py`，退出码3，报 `playwright not installed`。
离线检查确认依赖本身存在：`/opt/hermes/.venv/bin/python` 中可导入Playwright1.56.0，
Chromium位于 `/opt/ms-playwright`；相同Worker镜像在无网络、只读根、UID10000、
drop ALL及no-new-privileges条件下，使用固定venv解释器完成真实Chromium渲染与像素校验。
Hermes `tools/environments/local.py` 使用 `bash -lc`，将 `python3` 解析为
`/usr/bin/python3`，该解释器无法导入Playwright；`bash -c` 则解析到venv。
这是已复现的解释器选择问题，不是缺少浏览器依赖，也没有发生审批拒绝或工具超时。

生成阶段代理共36次请求：34次completed、2次proxy_shutdown；token未汇总，费用仍未知。
8份失败SVG保留为私有失败证据，不手动提升为成功checkpoint或可发布草稿。

## build6：固定官方工具解释器与真实8页渲染检查

修复 `94a18c6` 已推送并在服务器快进拉取。Worker提示及可信预览上下文明确使用
`/opt/hermes/.venv/bin/python` 和官方脚本绝对路径，所有官方Python工具含help和导出
均不再依赖登录shell的 `python3` 解析；全页视觉检查不传 `--pages`。
原有工具拒绝/超时停止规则保持，没有安装或替换运行库。
对应回归61 passed、5 skipped、90 subtests passed，独立代码review通过。

新Worker镜像已在无模型、无网络、只读根、UID10000、drop ALL及no-new-privileges条件下，
使用真实 `OfficialPreview` 与官方 `visual_review.py` 完成8页检查：`passed=true`、
`no_model_calls=true`、`rendered=8`、`failed=0`、`all_background=0`、`png_count=8`，
`preview_stopped=true`，退出0。8张PNG均为1280×720，中文及确定色块的像素检查通过。
日志为 `/root/ppt-official-render-smoke-20261025-b6.log`。这是合成8页的渲染检查，
不是DSH业务PPT生成成功或人工视觉验收。

build6 的网站、Controller、Worker镜像均已构建推送。已先关闭网站执行准入，再以禁用状态
部署新Controller和Worker配置。此次完整无模型预检scope为
`f02133c8-46d9-4625-b49c-8e31104b3598`，`passed`、`no_model_calls`、
`writers_stopped`、`cleanup_complete` 全部为 `true`；proxy、worker、sandbox各14项探针，
均0失败。报告为 `/root/ppt-network-preflight-20261025-b6.log`。
执行部署成功并恢复启用，不复用旧build5预检结果。

网站已使用标准 `bash deploy.sh --skip-build` 部署成功，日志为
`/root/ppt-site-deploy-20261025-runtime6.log`。网站2/2、Controller1/1就绪；
实际网站镜像为下表的build6 digest。业务重试继续使用已经确认的8页白底架构导读方案。

| 组件 | build6构建结果 |
| --- | --- |
| 网站 | `arm-cluster-master:5000/ppt-service-site@sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e` |
| Controller | `arm-cluster-master:5000/ppt-hermes-controller@sha256:da874035350d3aa308fd8b1e797ec027cae04a7954cf73b239b1071206fc8af6` |
| Worker | `arm-cluster-master:5000/ppt-hermes-worker@sha256:cc3c6765e5b7c84adebab7fe54b3e49b7085266ca5249cf5c7308d996c5b774e` |

## build6业务重试：候选已产生，独立校验失败

网页保留两轮已确认的方案，重新生成创建prepare job
`e8aa2e0e-ceda-469d-9578-69b9cb1b4369`，已通过并自动创建generate job
`c4aec6c4-4ac9-4e9f-85d8-12e9bbab956b`，scope为
`bca0747a-1c8f-5ad5-9835-bcf38d09c231`。该任务最终为 `failed`、`stopped=true`，
阶段截止时间为 `2026-10-25 11:54:46.876179+00`。最终私有候选包含8份SVG、8份PNG、
1份PPTX；独立沙箱 `ec1cb298-970f-4057-8fdb-47004f6e0d2d` 退出2，
原诊断为 `validation_execution_failed`。候选未成为版本，PPTX不提供下载。

官方delivery检查已报告passed：8 slides、8 notes、`errors=[]`、motion0、hidden0。
但本服务的独立 `render_validation` 硬编码要求notes必须为0，与用户已确认方案中的
文字讲者备注冲突，因此拒绝了合法候选。官方脚本通过不能替代服务独立质量门。
此生成任务代理共61次请求：60次completed、1次client_disconnected；token与费用尚未汇总。

OpenSpec变更已补充build6部署与检查回执，revision为
`9bba9bc451fad31e5090804ba96d71e0450333a6`，validate为true。
这表示规格变更文档校验通过，不等于本次业务PPT验收完成。

## build7：纯文本讲者备注校验与失败诊断修复

修复 `c696a52` 已推送并由服务器拉取。独立校验接受无备注，或每页恰好一个纯文本备注；
同时检查notes XML根、内容类型、slide/notes双向关系，拒绝孤儿、共享、重复部分及
备注内资源，核对官方delivery与原生包解析的计数一致。不是取消备注质量检查。
失败时写入脱敏 `failure.json`，K8s后端可以只读回收诊断；诊断存在不代表任务成功。

范围测试为191 passed、1 skipped、14 deselected、110 subtests passed；首次测试有4项
Windows平台环境失败，随后排除的平台项不计入通过。K8s范围另有46 passed，
这些测试范围可能重叠，不将数量相加。

build7镜像已构建并推送，Worker为
`arm-cluster-master:5000/ppt-hermes-worker@sha256:0b7aeb56f48a81a162e8863e7dbef08e4b6f8d44ff421e6b2047d291b6ff7d73`。
Controller为
`arm-cluster-master:5000/ppt-hermes-controller@sha256:ec99998b3379048f95d8db4acdd8452b903f60ac754b086851bd780a543f079e`。
已完成禁用状态部署、完整无模型预检、恢复启用；网站不重建，继续使用build6网站镜像
`sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e`。

此次预检scope为 `2b5f36c0-828b-4c37-87a7-73fdf58f3238`，`passed`、`no_model_calls`、
`writers_stopped`、`cleanup_complete` 全部为 `true`，proxy、worker、sandbox各14项探针，
均0失败，退出0。日志为 `/root/ppt-network-preflight-20261025-b7.log`。

### 已停止候选的无模型检查副本

服务器测试目录为 `/root/ppt-actual-candidate-check-20261025-b7-r2`，被检查manifest的
SHA256为 `ab5ee2ba5c93fb5824b2a0113c0a8def8edd10df755a290c8dcb5eaef6691e43`。
首个测试副本未带home，触发 `session_database_missing`；随后仅补齐5个 `.db` 文件，
没有拷贝配置或凭据。完整 `run_candidate_delivery` 在禁网、只读根、UID10000、
drop ALL容器中退出0，4个程序质量门通过，`review_artifacts_ready=true`。

该测试的 `accepted=false`、`publication_authorization=false`；人工验收、视觉审查及
导出构建合同仍待完成。没有调用模型，原job和数据库均未变更，也没有把旧失败候选
提升为版本或开放下载。日志为 `/root/ppt-actual-candidate-check-20261025-b7-r2.log`。

OpenSpec已补讲者备注与失败诊断规格，revision为
`54d486cdbbe079e5fbc344c8677c32e05a3acceb`，validate为true；继续保留业务及人工验收门。

### build7新一轮业务生成成功，审核稿已入库

网页新prepare job `be8d728f-a180-46ae-bb7c-195ecdb66159` 已通过，自动创建generate
`94a9e5a7-fe29-4e8c-a5ff-db32bb509a1d`，scope为
`38fe0ae8-74c0-52b7-8ff4-4ec3e4fee57f`。prepare最终为 `succeeded`、`stopped=true`，
scope为 `90f1a9d1-fd09-5913-82de-3e5e917a8f19`；generate最终为 `succeeded`、
`stopped=true`、`error=null`。项目修订18、阶段 `awaiting_review`、`active_job_id=null`。
两轮已确认的8页白底架构导读方案继续沿用。

| 审核稿交付 | 实际结果 |
| --- | --- |
| Version ID | `31d2bb16-f372-44eb-a2bf-16178b1d0eca` |
| 版本状态 | `review` |
| 文件数量 | 8份PNG、8份SVG、1份PPTX |
| PPTX大小 | 56768 bytes |
| PPTX SHA256 | `8ed10a226228b4b65ec733f861204bcf56d9c3b46078a99251624f1325d7761c` |
| 最终存储hash核对 | match=true |
| 可作为修订基线 | `eligible_as_revision_base=false` |
| 正式发布授权 | `publication_authorization=false` |

独立校验与存储证据：

- nonce：`2b5c0a40-c6fd-4eec-b306-18a558e49cbd`。
- source manifest SHA256：`e6d1a17de2b8fd16db6944ff737ba2bfe637e54fa1010252c8b437d0f74f8773`。
- receipt SHA256：`2c34f42647e091421b15f313a968a6083ce6100390fe2773801c498b59132caf`。
- source fingerprint：`7bf19ed0c29eb7a97f1eb405275949b7598e628f843057a48fffe646ef787548`。

仍待完成的必需质量门是 `independent_export_build_contract`、`independent_visual_review`、
`powerpoint_open_edit_save_reopen`、`postflight_warnings_review`。本次成功表示审核草稿交付
入库，不表示这些质量门或正式发布已经通过。

prepare代理18次请求：16次completed、1次client_disconnected、1次proxy_shutdown；
generate代理50次请求：49次completed、1次proxy_shutdown。完整tokens及费用尚未汇总，
未知费用不记录为0。

此前核对时浏览器登录会话已过期，已在内置浏览器打开Casdoor并请用户登录。
用户回复已登录后，会话恢复；网页显示项目修订18、等待审核、“预览与修改”版本数1，
选中版本 `31d2bb16-f372-44eb-a2bf-16178b1d0eca`。

已在内置浏览器按1至8逐页点击并截图，全部8页PNG均加载，无加载错误或明显遮挡；
白底架构图、顺序图、配置层、判断树、runner关系和代码阅读路径均已实际显示。
这属于网页栅格显示检查，不替代独立视觉审查或PowerPoint质量门。
截图临时保存于本机 `.runtime/ppt-dsh-review-page1.png` 至 `ppt-dsh-review-page8.png`，
不将这些二进制临时证据提交Git。

实际点击“下载审核草稿”，按钮等待后恢复且无错误。源码handler仅在HTTP成功且取得非空Blob后
触发原生download，因此本次记录为下载动作成功触发；没有独立核对浏览器落盘文件hash，
也没有据此记录PowerPoint打开、编辑、保存或重开通过。

服务器审核稿入库成功结果已同步到OpenSpec，当时revision为
`cf6fb80c68c3af97cc3a81eba403623ca7b60760`，validate=true。任务仍为26完成、35待办，
P1/P2总项未勾选。上述revision记录服务器成功结果；其后浏览器逐页显示和下载触发已核对，
并已同步到最新OpenSpec revision `76320cb94820eeaefa2fa28c002ae6e95113cbeb`，validate=true。
四项质量门仍pending。

## 方案展示修复与待验边界

`7e11b96` 修复旧页面只显示summary、遗漏官方沟通字段的问题，并安全显示Stage2对象形式
的逐页表达和字符串正文，不修改确认请求与权限。完整前端99项、TypeScript及build通过。
服务器标准build/deploy退出0，实际网站镜像为
`arm-cluster-master:5000/ppt-service-site@sha256:47fb9b2f17dba7e8ba5c6a88c56f2ce43f6092dff3b6e6039dfcf24d947de453`。
真实浏览器重新加载后已核实三方向完整正文及逐页表达显示。

`94a18c6` 同时修复prepare成功后自动串联generate、活跃任务清空时又退回显示prepare成功的
问题：页面保留最后实际任务并显示中文阶段名，不把生成失败掩盖成准备成功。
完整前端100项、TypeScript及production build通过；新网站实际部署状态以build6回执为准。

当前已交付入库的是上述review草稿；网页8页显示与下载动作触发已核对。
不记录独立视觉或PowerPoint打开/编辑/保存/重开验收通过；后续用量仍须按账本统计，
费用未知不补0。历史失败证据继续保留，不因审核稿入库而勾选P1/P2总项或apply/archive。
