# PPT 通过通用 Agent 服务执行

PPT 网页及 API 保留现有的项目、资料、两次确认、任务、审核稿和下载接口。
新的 `agent-service` 控制器把业务任务提交给通用 Agent 服务，不传引擎名、
供应商密钥或 Hermes/Pi 的 HOME。执行引擎、模型、工具环境和能力包由
`agent_service` 的部署目录及审核过的执行绑定配置统一管理。

本目录为可选部署接线。默认部署和现网后端不会因为代码合入而切换。
当前优先完成 Pi 的五阶段业务试接，Hermes 不作为前置条件。通用服务控制面已部署，
离线及 Linux/PostgreSQL 回归已有记录；生产执行路由仍关闭，PPT 现有后端尚未切换。
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

每个任务的 profile revision 默认 `v1`。先对 Pi 的上述五个合同分别完成能力包、
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

`AgentOutbox` 与 PPT Job/ExecutionSlot 保存在同一 PostgreSQL 内，由 schema 4
迁移安装。请求意图、输入上传引用、远端任务 ID、幂等标识、执行绑定、事件游标、
检查点和业务接收状态均持久化；重启后继续同一任务。取消或不确定提交不会产生
第二次付费任务。终止证明不足或 quarantine 时保留业务执行槽。

两次确认来自 PPT 数据库内确切 Plan 记录，其确认内容必须匹配不可变输入快照。
Stage2 前必须有 Stage1 确认，prepare 前必须有选中方向的 Stage2 确认。
revise 只能使用明确选中的、同一所有者的 published Version 和精确检查点。
审核稿不能成为修订基线。模型输出不构成网站确认或发布许可。

原始资料位于 `inputs/<asset-id>.<extension>`；一个资料文件最多 16 MiB。
检查点文件恢复到 `base/project/<project-root>/...`，索引位于
`base/result/checkpoint.json`。业务工作区与输出为 `project/` 和
`result/manifest.json`。控制器停写后重新测量所有文件，并创建
`result/checkpoint.json`。最多 2048 个输出文件、每文件 64 MiB、合计 256 MiB。
原始资料与检查点挂载只读；检查点不含原生会话、凭据或 HOME。

此接线只读取中立的 `ppt-agent-artifacts-v1` 检查点。现有 Hermes/Pi 专用检查点
不自动转换；切换后需要从新的初始规划进入流程，旧项目与版本仍可查看下载。

PPT 与科研共享一个全局 Agent 执行槽，同一时刻只运行一次尝试；其他请求排队。
每个规划、准备、生成或修订阶段独立创建执行 Pod，全部写进程停止并持久化证据后
按清理流程回收。等待用户确认、查看审核稿和等待下次修改时不保留运行 Pod。
下一阶段使用数据库记录及中立检查点继续，不依赖某个常驻用户 Pod。

## 构建与接线

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
  --kube-api-cidr 192.168.137.101/32 \
  --kube-api-port 6443
```

确认下节的服务端合同、凭据与网络检查，并排空旧执行槽后，再显式添加
`--enable-execution --apply --enable-site`。
renderer 原位替换已有 controller Deployment，保留其标识以避免同时运行两套调度器。
切换前应等当前执行槽释放；发现旧控制器任务尚在运行时，新接入层会拒绝抢占。
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
3. 暂停旧入口的新生成提交，确认当前 ExecutionSlot 已释放并保存原控制器及网站配置。
   使用 `--enable-execution --apply --enable-site` 原位切换，等待控制器和 `ppt-site` rollout，
   不另启动一套不同名字的控制器抢占同一数据库。
4. 在网页新建一个三页、无图表的试验项目。从资料上传、初次规划、第一轮确认、设计方向、
   第二轮确认依次进入 prepare 和 generate，每次只推进一个任务。无图表试做默认使用
   `expected_charts=[]`，无需额外创建 chart oracle；若明确需要图表，先按既有流程审查
   与输入快照和资料 SHA256 绑定的 oracle ConfigMap，再通过 `--chart-oracle-configmap` 挂载。
5. 核对每阶段的业务 Job ID、Agent Job ID、唯一幂等提交、检查点、产物摘要及执行 Pod 停止/清理。
   generate 应先进入审核稿，继续完成既有视觉、原生包和发布门检查后，再下载验证 PPT。
   对同一所有者的已发布版本执行一次 revise，检查未选中范围、基线和确认内容不会被越过。
6. 验证取消、控制器重启恢复及接收回执重放，不重复创建付费任务，也不把停止证明不足的尝试
   当作已释放。试接失败时暂停新提交，保留任务/产物/停止证据，处理活动槽后恢复保存的配置。
   完成网页与发布检查后再恢复常规使用，不能只因 Agent 返回 `completed` 就宣布业务迁移成功。

此步骤是待执行的业务验收路径。实际试接结果应与任务证据一同补入验证记录；本文不表示
PPT 已经切换到通用服务，也不表示真实五阶段已通过。
