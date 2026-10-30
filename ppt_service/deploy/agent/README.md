# PPT 通过通用 Agent 服务执行

PPT 网页及 API 保留现有的项目、资料、两次确认、任务、审核稿和下载接口。
新的 `agent-service` 控制器把业务任务提交给通用 Agent 服务，不传引擎名、
供应商密钥或 Hermes/Pi 的 HOME。执行引擎、模型、工具环境和能力包由
`agent_service` 的部署目录及审核过的执行绑定配置统一管理。

本目录为可选部署接线。默认部署和现网后端不会因为代码合入而切换。
当前优先完成 Pi 的五阶段业务试接，Hermes 不作为前置条件。通用服务控制面已部署，
离线及 Linux/PostgreSQL 回归已有记录；五条 PPT 生产执行路由仍关闭，PPT 现有后端尚未切换。
2026-10-29 的只读检查确认控制面三个 Deployment 均 Ready，PPT ExternalSecret 同步成功，
但 PPT 调用凭据看到的五个 capability 均为 `configured=false`、`ready=false`。
科研已有六条独立路由启用，更新公共控制面时须保留其合同、绑定与配置。
真实 Pi 执行的进展和限制见 [原生引擎验证记录](../../../agent_service/docs/RUNTIME_VALIDATION_20261027.md)。
控制面就绪、镜像构建或回归通过不能代替五阶段合同及网页业务验收。

## 任务与权限

专用 `ppt-service` 调用凭据只授权以下任务合同：

```text
ppt.plan_initial.v1
ppt.plan_options.v1
ppt.prepare.v1
ppt.generate.v1
ppt.revise.v1
```

本次迁移使用新的 profile revision `v2`，保留旧 `v1` 的目录与历史绑定；任务名称和
任务/输出 schema 仍为上述 `*.v1`。控制器部署须显式指定 `--profile-revision v2`。
先对 Pi 的上述五个合同分别完成能力包、
工具镜像、模型接口与工具组合的验收，配置路由才可启用。以后启用 Hermes 时另行验收，
本次试接无需同时启用两种引擎。
generate/revise 的绑定需要图像输入与实际逐页图像检查能力。

服务连接默认为 `http://agent-service.agent-service.svc.cluster.local:8080`。
调用凭据复用 [平台 ExternalSecret 清单](../../../agent_service/deploy/platform/vault/agent-service-externalsecret.yaml)：
Vault `secret/data/agent-service/runtime` 的 `PPT_SERVICE_TOKEN` 同步为 PPT 命名空间内
`ppt-agent-service` Secret 的 `token`，控制器只读挂载 `/etc/ppt-agent-service/token`。
OAuth、Cloudflare、Vault 和模型供应商密钥继续在各公共服务的现有目录管理；
本目录不复制它们，也不创建新的域名或登录应用。

## 数据与确认

`AgentOutbox` 与 PPT Job/ExecutionSlot 保存在现有 `data` 命名空间共享 PostgreSQL
的 PPT 业务数据库内，地址为 `postgres.data.svc.cluster.local`，由 schema 4 迁移安装。
沿用既有运行角色与受控迁移入口，无需新增 PPT 专用 PostgreSQL。
请求意图、输入上传引用、远端任务 ID、幂等标识、执行绑定、事件游标、
检查点和业务接收状态均持久化；重启后继续同一任务。取消或不确定提交不会产生
第二次付费任务。提交回包丢失时先按幂等键查询；确需重试时保留同一请求与绝对截止时间。
取消后的未知提交只查询、不重新 POST。查询 404 尚不能排除在途请求时，保留执行槽直到
原始截止时间后再次查无任务。终止证明不足或 quarantine 时同样保留业务执行槽。

业务 Job 在首次 claim 时固定截止时间，作为 `execution_deadline_at` 传给通用服务。
上传、排队、心跳和控制器重启均不续期：初步沟通 600 秒，三套设计方向 1200 秒，
准备、生成与修改各 1800 秒。远端任务与本地业务共享这个截止时间。
模型代理完整保存上游响应后才把事件交给 Pi，合法长推理可能超过 120 秒没有子进程输出。
正式 Worker 和候选探针共用 `build_pi_runtime_settings`，沿用共享服务的
`buffered_model_idle_timeout` 策略，同时保留科研已有的长响应处理。
空闲判定容忍度不会延长任务的绝对截止时间，也不会触发付费任务重提交。
上游事件通过持久游标投影为固定的排队、启动与执行阶段；停写确认后的成果取回与
独立校验阶段由业务控制器记录。界面显示阶段和最新活动，不推断完成百分比或把模型文字当作成功证据。

两次确认来自 PPT 数据库内确切 Plan 记录，其确认内容必须匹配不可变输入快照。
Stage2 前必须有 Stage1 确认，prepare 前必须有选中方向的 Stage2 确认。
常规 revise 使用明确选中的、同一所有者的 published Version 和精确检查点。
审核稿修改使用独立的显式授权路径：网站绑定具体 review Version、审核记录 revision、
原始文件及验证证据的哈希，控制器在 claim 和提交远端任务前重新检查真实文件与数据库授权。
只向中立任务传递精确 review 基线和 `ppt-review-authority.v1` 摘要；该路径始终产出 review，
不会把审核稿标记为已发布或赋予常规 published 修订资格。模型输出不构成网站确认或发布许可。

原始资料位于 `inputs/<asset-id>.<extension>`；一个资料文件最多 25 MiB，
与现有网站上传限制一致。网站既有项目资料总量限制保持不变，Agent 输入集合上限仍为 256 MiB。
发布新 profile/catalog 时须同步 API 输入上限与执行绑定；旧的 16 MiB 绑定不能直接沿用。
检查点文件恢复到 `base/project/<project-root>/...`，索引位于
`base/result/checkpoint.json`。业务工作区与输出为 `project/` 和
`result/manifest.json`。控制器停写后重新测量所有文件，并创建
`result/checkpoint.json`。最多 2048 个输出文件、每文件 64 MiB、合计 256 MiB。
原始资料与检查点挂载只读；检查点不含原生会话、凭据或 HOME。

此接线只读取中立的 `ppt-agent-artifacts-v1` 检查点。现有 Hermes/Pi 专用检查点
不自动转换。切换后，旧检查点的继续制作或修改会返回 `legacy_checkpoint_requires_replan`，
并提示从初步沟通重新规划；同一项目可使用保留的资料开始新的初始规划，再完成两次确认。
旧项目与版本仍可查看下载，新任务不会覆盖旧检查点，也不会静默借用原生会话。

PPT 与科研共享一个全局 Agent 执行槽，同一时刻只运行一次尝试；其他请求排队。
每个规划、准备、生成或修订阶段独立创建执行 Pod，全部写进程停止并持久化证据后
按清理流程回收。等待用户确认、查看审核稿和等待下次修改时不保留运行 Pod。
下一阶段使用数据库记录及中立检查点继续，不依赖某个常驻用户 Pod。

## 构建与接线

先提交、推送 Git，再由服务器 fetch 并检出精确提交，随后构建、推送不可变镜像；
外层提交须指向同一应用子模块提交。真实候选与独立校验须绑定最终镜像，不能拿旧镜像的
成功证据放行新镜像。公共目录从切换前最新配置增量追加 PPT v2，保留科研路由、
旧合同/绑定/池、历史 host ConfigMap 与既有 Job pins；执行 UID 范围永久追加、不复用。
切换前还应核对生产控制面源码，先合入其他业务已上线的更新，避免只保留配置却回退处理代码。

共享候选执行和公共目录晋级须持有同一个操作锁，等待原有任务完成后才暂停 Dispatcher。
PPT 网站切换另持有 `ppt-deploy-lock`，通过资源 UID、resourceVersion、模板和操作归属核对
后逐步写入。发现并发修改时重新检查，不用旧快照覆盖新配置。rollout 等待超时后先检查
实际安装镜像与 Ready 状态，再决定继续或受控恢复，不能盲目重复安装。

构建沿用现有 source-only ARM64 overlay：只打包业务代码和显式列出的通用客户端/
合同、引擎合同依赖与 PPT profile 源码；构建上下文须包括 `agent_service/diagnostics.py`，
否则引擎合同的导入会失败。独立校验器镜像仍使用现有完整 PPT Master 工具镜像，
其解释器属于该校验镜像自身，不成为业务任务合同的一部分。

先通过 [网站标准部署入口](../k8s/README.md#服务器标准入口) 构建并推送包含当前代码的
site/migrator 镜像，使用现有迁移流程安装 schema 4，保留网站当前执行后端。
schema 3 属于现有 PPT 审核历史，schema 4 在保留其表、记录和追加约束的基础上新增 Outbox。
早期 Agent 分支测试库的另一种 schema 3 缺少审核表，会被拒绝自动升级；不得改写正式库账本来绕过检查。
再使用固定 site 和完整工具镜像摘要构建控制器与独立校验器：

```bash
bash ppt_service/deploy/agent/build.sh \
  --site-base '<site-registry-image@sha256:digest>' \
  --worker-base '<reviewed-ppt-tools-image@sha256:digest>' \
  --controller-tag '<private-registry>/ppt-agent-controller:<version>' \
  --worker-tag '<private-registry>/ppt-agent-validator:<version>' \
  --push
```

renderer 只生成配置，加入 `--apply` 才会写入集群，`--enable-execution` 才允许提交：

```bash
bash ppt_service/deploy/agent/deploy.sh \
  --controller-image '<controller-registry-image@sha256:digest>' \
  --worker-image '<validator-registry-image@sha256:digest>' \
  --profile-revision v2 \
  --kube-api-cidr 192.168.137.101/32 \
  --kube-api-port 6443
```

确认下节的服务端合同、凭据与网络检查，并排空旧执行链后，再显式添加
`--enable-execution --apply --enable-site`。
renderer 原位替换已有 controller Deployment，保留其标识以避免同时运行两套调度器。
应用前置检查要求网站 ConfigMap 与实际 API 进程均已关闭执行准入、旧控制器已缩容至 0，
且旧控制器、模型代理、Worker 与校验 Pod 均已消失。共享数据库须已是 schema 4，运行角色
有 Outbox CRUD 权限，活动及排队 Job、占用槽、未完成 Outbox 与正在解析的资料均为 0。
需要让旧控制器先完成 prepare 自动排入的 generate，再停止它；仅等待一次执行槽变空并不足够。
启用的清单还会只读检查五个 PPT capability 均 ready。前置检查只读取状态，暂停、保存与恢复
配置由操作脚本负责；此入口不会取消用户任务、修写业务记录或自动覆盖另一轮部署。
site 的常规后续部署会保留已经选定的 `agent-service` 后端。

控制器仅访问 PPT PostgreSQL、Kubernetes API、DNS 和 Agent Service API；
不接收上游模型 Secret，也不需要创建 Secret 的 RBAC 权限。
Agent Service 侧还必须允许来自此控制器标签的 8080 入站，遵循通用服务部署配置。
校验器 Pod 有独立 deny-all 网络策略和固定入口，其精确 Pod UID 的全部容器
实际终止后才能冻结验证证据。远端模型停写回执不会被当成校验器停写证明。

规划与 prepare 通过独立检查后可以推进业务阶段。generate/revise 完成真实
静态检查、重新渲染和原生包级检查后仅保存 review；视觉、完整后验交付及
PowerPoint 验收仍按既有发布门控制。业务数据库提交成功后，才向通用服务
发送 `candidate_received` 接收确认，它不会授予发布权限。

## Pi 优先的单任务试接

1. 在通用服务中核对五个 PPT 合同各自的真实 Pi 验收记录、镜像 digest 和 binding revision。
   只启用已通过的 Pi pool/route，全局并发保持为 1，Hermes 保持关闭。缺少任一阶段证据时，
   先完成该阶段候选验收，不把普通文本生成或服务端 HTTP 200 作为通过依据。
2. 按上节迁移数据库、构建镜像并生成清单；确认 `ppt-agent-service` ExternalSecret Ready，
   控制器至 Agent API 的 TCP 8080 出站和对端精确入站均被允许。控制器还需保留 DNS、
   PostgreSQL 和 Kubernetes API 的既有必要通路。使用 PPT 调用凭据检查 `GET /v1/capabilities`，
   确认五个合同都解析到已验收 Pi 绑定，不打印凭据值。
3. 保存原控制器、RBAC、网络策略与网站配置；将网站 `PPT_EXECUTION_ENABLED=false`，
   完成网站 Recreate rollout 并核对实际进程已关闭准入。保持旧控制器运行直到整条现有执行链、
   解析和远端回执收敛，再将旧控制器缩容为 0 并等待全部旧执行 Pod 消失。
   使用 `--enable-execution --apply --enable-site` 原位切换，等待控制器和 `ppt-site` rollout，
   不另启动一套不同名字的控制器抢占同一数据库。
4. 在网页新建一个三页、无图表的试验项目。从资料上传、初次规划、第一轮确认、设计方向、
   第二轮确认依次进入 prepare 和 generate，每次只推进一个任务。无图表试做默认使用
   `expected_charts=[]`，无需额外创建 chart oracle；若明确需要图表，先按既有流程审查
   与输入快照和资料 SHA256 绑定的 oracle ConfigMap，再通过 `--chart-oracle-configmap` 挂载。
5. 核对每阶段的业务 Job ID、Agent Job ID、唯一幂等提交、检查点、产物摘要及执行 Pod 停止/清理。
   generate 应先进入审核稿，继续完成既有视觉、原生包和发布门检查后，再下载验证 PPT。
   对同一所有者的已发布版本执行一次 revise，并通过显式审核稿修改入口再验证一次 review 分支，
   检查逐页预览/PPTX 哈希、审核记录绑定、未选中范围、基线与两次确认不会被越过。
   review 修改结果仍应为审核稿，保留独立验证证据与真实 runtime pins。
6. 验证取消、控制器重启恢复及接收回执重放，不重复创建付费任务，也不把停止证明不足的尝试
   当作已释放。试接失败时先关闭网站准入，保留任务/产物/停止证据，继续由对应控制器处理活动槽。
   只有确认无排队链、无活动或不确定远端提交，且控制器及相关 Pod 已停止后，才可恢复保存配置。
   恢复前核对资源 UID、当前模板和操作归属，出现并发配置变化应停止恢复；保留 schema 4、
   Outbox、审核历史和原生检查点，不通过回退 schema 或覆盖业务表实现回退。
   同时检查阶段进度断线重连不重复事件、绝对截止时间不延长，以及旧原生检查点可明确返回重新规划。
   完成网页与发布检查后再恢复常规使用，不能只因 Agent 返回 `completed` 就宣布业务迁移成功。

此步骤是待执行的业务验收路径。实际试接结果应与任务证据一同补入验证记录；本文不表示
PPT 已经切换到通用服务，也不表示真实五阶段已通过。
